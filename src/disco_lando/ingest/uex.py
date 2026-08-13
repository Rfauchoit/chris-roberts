"""UEX — prix et lieux d'achat (Phase 6 du brief).

C'est la **seule** voie programmatique pour les prix : CIG a retiré les
inventaires de boutique et les prix des fichiers du jeu à partir de la 3.20
(cf. docs/SOURCES_EXTERNES.md). La donnée n'existe plus en amont ; Cornerstone
la reconstitue par saisie communautaire, sans API. UEX expose la même matière
avec une API 2.0 documentée et versionnée.

**Les UUID sont les mêmes que les nôtres**, vérifié sur la machine : le Gladius
extrait du jeu porte `b6b59889-7226-458e-a6b0-1c9392128a3c`, et
`/vehicles?uuid=…` rend exactement cette fiche chez UEX. La jointure est donc
directe, sans appariement par nom — ce qui aurait été fragile, les noms
différant d'un « Aegis » ou d'un tiret.

**Aucun jeton n'est nécessaire** pour les endpoints de lecture utilisés ici,
constaté le 2026-08-03. `UEX_API_TOKEN` reste honoré s'il est défini : il
donne accès aux quotas plus élevés et aux endpoints réservés. Sans lui, tout ce
qui suit fonctionne.

Ces données ne sont **pas** mélangées aux données du jeu :

- table séparée, horodatée, jamais jointe en dur aux tables statiques ;
- source explicitée dans la réponse, parce que « rapporté par des joueurs » et
  « lu dans les fichiers du jeu » n'ont pas la même valeur de vérité ;
- l'absence de réseau n'empêche rien : tout le reste fonctionne sans.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

from .. import config

SCHEMA = """
-- Lieux de vente, horodatés. Séparés des tables du jeu : ces chiffres sont
-- rapportés par des joueurs, pas lus dans les fichiers.
-- La version de jeu qu'UEX décrit, pour le contrôle du jour de patch : des
-- prix de la 4.9 sous un catalogue 4.10 se lisent comme des réponses justes.
CREATE TABLE IF NOT EXISTS uex_source (
  url         TEXT PRIMARY KEY,
  game_live   TEXT,
  fetched_at  TEXT
);

CREATE TABLE IF NOT EXISTS uex_prices (
  id             INTEGER PRIMARY KEY,
  fetched_at     TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('commodity', 'vehicle', 'item', 'rental')),
  ref_uuid       TEXT,
  name           TEXT NOT NULL,
  terminal       TEXT,
  star_system    TEXT,
  price_buy      REAL,
  price_sell     REAL,
  UNIQUE (kind, name, terminal)
);

CREATE INDEX IF NOT EXISTS ix_uex_name ON uex_prices (name);
CREATE INDEX IF NOT EXISTS ix_uex_uuid ON uex_prices (ref_uuid);

-- Prix par terminal, la matière première des routes commerciales. Séparée de
-- `uex_prices` parce que la granularité diffère : ici une ligne par couple
-- (commodité, terminal), soit plusieurs milliers.
CREATE TABLE IF NOT EXISTS uex_commodity_prices (
  id             INTEGER PRIMARY KEY,
  fetched_at     TEXT NOT NULL,
  commodity      TEXT NOT NULL,
  terminal       TEXT NOT NULL,
  star_system    TEXT,
  price_buy      REAL,          -- ce que le terminal vend au joueur
  price_sell     REAL,          -- ce qu'il rachète au joueur
  scu_available  REAL,          -- stock à l'achat
  scu_demand     REAL,
  UNIQUE (commodity, terminal)
);

-- Objets qui ne s'obtiennent pas en jeu : souscription, abonnement,
-- concierge. Sans ça, « où acheter ce casque » n'a aucune réponse alors que
-- la vraie réponse est « nulle part, il s'achète en argent réel ».
CREATE TABLE IF NOT EXISTS uex_items (
  uuid       TEXT PRIMARY KEY,
  name       TEXT,
  exclusive  TEXT,          -- pledge | abonnement | concierge
  wiki       TEXT
);

CREATE INDEX IF NOT EXISTS ix_uexc_commodity ON uex_commodity_prices (commodity);
CREATE INDEX IF NOT EXISTS ix_uexc_system ON uex_commodity_prices (star_system);

-- Raffineries. Les fichiers du jeu ne les portent pas ; UEX les publie avec
-- leur position complète (système, planète, orbite, station). 21 terminaux
-- mesurés le 2026-08-05, sur Stanton, Pyro et Nyx.
--
-- La position répond à « où raffiner sans traverser un système la soute
-- pleine » ; le rendement, lui, vit dans `refinery_yields` juste en dessous.
-- Ne pas confondre `refineries_audits` (3 lignes, inutilisable) et
-- `refineries_yields` (215) : c'est la confusion qui m'a fait conclure à tort
-- que le rendement n'existait pas.
CREATE TABLE IF NOT EXISTS refineries (
  id             INTEGER PRIMARY KEY,   -- id UEX du terminal
  fetched_at     TEXT NOT NULL,
  name           TEXT NOT NULL,
  nickname       TEXT,
  star_system    TEXT,
  planet         TEXT,
  orbit          TEXT,
  moon           TEXT,
  station        TEXT,
  outpost        TEXT,
  city           TEXT,
  available      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS ix_refineries_system ON refineries (star_system);

-- **Le rendement d'une raffinerie dépend du minerai, et il est publié.**
-- `refineries_yields` : 215 lignes, 24 minerais sur 20 terminaux, de -9 % à
-- +13 %. J'avais d'abord regardé `refineries_audits` — 3 lignes — et conclu
-- que la donnée n'existait pas ; l'utilisateur a signalé que c'était faux, et
-- ça l'était. Regarder **tous** les endpoints d'une famille avant de conclure
-- à l'absence.
--
-- La valeur est un écart en pourcentage par rapport au rendement de base,
-- relevé par les joueurs. Tous les minerais ne sont pas couverts : le
-- Stileron, par exemple, n'y figure pas.
CREATE TABLE IF NOT EXISTS refinery_yields (
  id            INTEGER PRIMARY KEY,
  fetched_at    TEXT NOT NULL,
  commodity     TEXT NOT NULL,
  terminal_id   INTEGER,
  terminal      TEXT,
  star_system   TEXT,
  yield_pct     REAL NOT NULL,
  UNIQUE (commodity, terminal)
);

CREATE INDEX IF NOT EXISTS ix_ryield_commodity ON refinery_yields (commodity);

"""


class UexError(RuntimeError):
    pass


def token() -> str | None:
    return os.environ.get(config.UEX_TOKEN_ENV) or None


def available() -> bool:
    """UEX est utilisable sans jeton : seul le réseau est indispensable."""
    return True


def _get(chemin: str, **parametres) -> list[dict]:
    url = f"{config.UEX_API_URL.rstrip('/')}/{chemin.lstrip('/')}"
    if parametres:
        url += "?" + urllib.parse.urlencode(parametres)

    # Le `User-Agent` n'est pas décoratif : UEX renvoie **403** à l'agent par
    # défaut d'urllib (« Python-urllib/3.12 »). Mesuré — le même appel passe
    # avec n'importe quel agent nommé. Sans cette ligne, toute la Phase 6
    # échouerait sur un refus qui ressemble à un problème de jeton.
    entetes = {
        "User-Agent": "chris-roberts/0.3 (+assistant Star Citizen, usage privé)",
        "Accept": "application/json",
    }
    if (jeton := token()):
        entetes["Authorization"] = f"Bearer {jeton}"

    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=entetes), timeout=30
        ) as reponse:
            corps = json.load(reponse)
    except urllib.error.HTTPError as exc:
        raise UexError(f"UEX a répondu {exc.code} sur {chemin}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UexError(f"UEX injoignable : {exc}") from None
    except json.JSONDecodeError:
        raise UexError(f"réponse UEX illisible sur {chemin}") from None

    if isinstance(corps, dict):
        return corps.get("data") or []
    return corps if isinstance(corps, list) else []


def _lieux() -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    """Terminaux et systèmes, par identifiant.

    Les endpoints de prix ne rendent que des identifiants numériques ; sans
    cette table de correspondance, une réponse dirait « terminal 149 », ce qui
    n'aide personne à aller acheter quoi que ce soit.

    Le troisième dictionnaire associe un terminal à son système, parce que
    `commodities_prices_all` — le seul endpoint groupé — **ne renvoie pas**
    `id_star_system`, contrairement aux autres. Sans lui, les 2 591 cotations
    arrivaient avec un système vide et tout filtre par système rendait zéro.
    """
    systemes = {s["id"]: s.get("name") for s in _get("star_systems") if s.get("id")}
    terminaux, systeme_du_terminal = {}, {}
    for t in _get("terminals"):
        if not t.get("id"):
            continue
        terminaux[t["id"]] = t.get("name") or t.get("nickname") or f"terminal {t['id']}"
        systeme_du_terminal[t["id"]] = systemes.get(t.get("id_star_system"))
    return terminaux, systemes, systeme_du_terminal


# Sections écartées, plutôt qu'une liste de sections retenues : le joueur pose
# des questions sur **tout** l'équipement, pas seulement sur ce qui se monte
# sur un vaisseau. Armures, vêtements et armes personnelles représentent à eux
# seuls 3 500 des 4 513 objets appariés.
#
# Ce qui reste dehors : les commodités ont leur propre table, et livrées,
# décorations, babioles et cartes de données n'appellent pas de question.
SECTIONS_EXCLUES = frozenset({
    "Commodities", "Liveries", "Decorations", "Flair", "Data",
})


def _prix_objets(con: sqlite3.Connection, terminaux: dict, systemes: dict,
                 maintenant: str, echo=print) -> list[tuple]:
    """Prix des armes et composants de vaisseau.

    Les UUID sont les mêmes que les nôtres, comme pour les vaisseaux —
    vérifié : 72 refroidisseurs sur 73, 75 générateurs sur 75, 64 boucliers
    sur 64. C'est ce qui rend la jointure sûre là où un appariement par nom
    achopperait sur les préfixes de constructeur.

    `items` et `items_prices` exigent tous deux un `id_category` : il n'existe
    pas d'endpoint groupé comme pour les commodités. On boucle donc sur les
    seules sections qui se montent sur un vaisseau, soit une trentaine de
    catégories et deux appels chacune.
    """
    nos_uuid = {u for (u,) in con.execute("SELECT uuid FROM items")}
    categories = [
        c for c in _get("categories")
        if (c.get("section") or "") not in SECTIONS_EXCLUES and c.get("id")
    ]
    echo(f"  objets… ({len(categories)} catégories)")

    lignes: list[tuple] = []
    exclusifs: list[tuple] = []
    for categorie in categories:
        objets = {
            o["id"]: o for o in _get("items", id_category=categorie["id"])
            if o.get("id") and o.get("uuid") in nos_uuid
        }
        if not objets:
            continue
        for prix in _get("items_prices", id_category=categorie["id"]):
            objet = objets.get(prix.get("id_item"))
            if objet is None or not (prix.get("price_buy") or prix.get("price_sell")):
                continue
            # `items_prices` porte directement les noms de lieu, contrairement
            # aux prix de vaisseaux : pas besoin de la table de correspondance.
            lignes.append((
                maintenant, "item", objet.get("uuid"), objet.get("name"),
                prix.get("terminal_name") or terminaux.get(prix.get("id_terminal")),
                prix.get("star_system_name"),
                prix.get("price_buy"), prix.get("price_sell"),
            ))
        exclusifs.extend(
            (o.get("uuid"), o.get("name"), _exclusivite(o), o.get("wiki"))
            for o in objets.values() if _exclusivite(o)
        )
    echo(f"  {len(lignes)} prix d'objets, {len(exclusifs)} objets hors commerce")
    con.executemany(
        "INSERT OR REPLACE INTO uex_items (uuid, name, exclusive, wiki) "
        "VALUES (?,?,?,?)", exclusifs)
    return lignes


def _exclusivite(objet: dict) -> str | None:
    """Un objet qui ne s'obtient pas en jeu, et par quelle voie.

    UEX distingue trois exclusivités, toutes en argent réel. C'est la réponse
    à « où acheter ça » quand aucun terminal ne le vend et qu'aucune recette
    n'existe : ce n'est ni une lacune de relevé, ni un oubli d'ingestion.
    """
    for champ, libelle in (("is_exclusive_pledge", "pledge"),
                           ("is_exclusive_subscriber", "abonnement"),
                           ("is_exclusive_concierge", "concierge")):
        if objet.get(champ):
            return libelle
    return None


def sync(con: sqlite3.Connection, *, echo=print) -> int:
    """Rapatrie prix de commodités et lieux d'achat de vaisseaux.

    Renvoie le nombre de lignes écrites. Ne touche à aucune table du jeu.
    """
    import datetime as dt

    con.executescript(SCHEMA)
    maintenant = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")

    echo("  lieux…")
    terminaux, systemes, systeme_du_terminal = _lieux()

    lignes: list[tuple] = []

    echo("  commodités…")
    for entree in _get("commodities"):
        nom = entree.get("name")
        if not nom:
            continue
        lignes.append((
            maintenant, "commodity", entree.get("uuid"), nom, None, None,
            entree.get("price_buy"), entree.get("price_sell"),
        ))

    # Les vaisseaux : c'est ce qui répond à « où acheter un Gladius ». On ne
    # garde que ceux dont l'UUID existe chez nous — le reste est du bruit pour
    # cet assistant, et la jointure par UUID est fiable là où l'appariement par
    # nom ne l'est pas.
    echo("  vaisseaux…")
    connus = {
        u for (u,) in con.execute("SELECT uuid FROM ships WHERE uuid IS NOT NULL")
    }
    vaisseaux = {
        v["id"]: v for v in _get("vehicles")
        if v.get("uuid") in connus
    }
    echo(f"  {len(vaisseaux)} vaisseaux appariés par UUID")

    for prix in _get("vehicles_purchases_prices"):
        vaisseau = vaisseaux.get(prix.get("id_vehicle"))
        if vaisseau is None:
            continue
        lignes.append((
            maintenant, "vehicle", vaisseau.get("uuid"),
            vaisseau.get("name_full") or vaisseau.get("name"),
            terminaux.get(prix.get("id_terminal")),
            systemes.get(prix.get("id_star_system")),
            prix.get("price_buy"), None,
        ))

    # Location de vaisseaux — endpoint `vehicles_purchases_prices` ne couvre
    # que l'achat. « Où louer un Cutlass » est pourtant la question du joueur
    # qui n'a pas trois millions d'aUEC devant lui.
    echo("  locations…")
    for prix in _get("vehicles_rentals_prices"):
        vaisseau = vaisseaux.get(prix.get("id_vehicle"))
        if vaisseau is None or not prix.get("price_rent"):
            continue
        lignes.append((
            maintenant, "rental", vaisseau.get("uuid"),
            vaisseau.get("name_full") or vaisseau.get("name"),
            terminaux.get(prix.get("id_terminal")),
            systeme_du_terminal.get(prix.get("id_terminal")),
            prix.get("price_rent"), None,
        ))

    lignes += _prix_objets(con, terminaux, systeme_du_terminal, maintenant, echo)

    con.executemany(
        "INSERT OR REPLACE INTO uex_prices "
        "(fetched_at, kind, ref_uuid, name, terminal, star_system, "
        " price_buy, price_sell) VALUES (?,?,?,?,?,?,?,?)",
        lignes,
    )

    # Prix par terminal — la matière des routes commerciales. Un seul appel
    # groupé plutôt qu'un par commodité : `commodities_prices` exige un
    # `id_commodity`, ce qui ferait 206 requêtes pour la même donnée.
    echo("  prix par terminal…")
    noms_commodites = {
        c["id"]: c.get("name") for c in _get("commodities") if c.get("id")
    }
    par_terminal = []
    for prix in _get("commodities_prices_all"):
        commodite = noms_commodites.get(prix.get("id_commodity"))
        terminal = terminaux.get(prix.get("id_terminal"))
        if not commodite or not terminal:
            continue
        par_terminal.append((
            maintenant, commodite, terminal,
            # Le système vient du terminal : cet endroit-là ne le donne pas.
            systeme_du_terminal.get(prix.get("id_terminal")),
            prix.get("price_buy"), prix.get("price_sell"),
            prix.get("scu_buy"), prix.get("scu_sell_stock"),
        ))

    con.executemany(
        "INSERT OR REPLACE INTO uex_commodity_prices "
        "(fetched_at, commodity, terminal, star_system, price_buy, price_sell,"
        " scu_available, scu_demand) VALUES (?,?,?,?,?,?,?,?)",
        par_terminal,
    )

    raffineries = _raffineries(con, maintenant, echo)

    # **La version de jeu qu'UEX décrit.** Le wiki a déjà son contrôle de
    # build ; UEX n'en avait pas, et c'est le même piège au jour du patch :
    # un catalogue 4.10 sous des prix encore 4.9. L'endpoint est minuscule,
    # et son absence n'est pas une panne — le contrôle dira « inconnu ».
    try:
        versions = _get("game_versions")
        live = (versions[0] if isinstance(versions, list) and versions
                else versions or {})
        if isinstance(live, dict) and live.get("live"):
            con.execute("INSERT OR REPLACE INTO uex_source VALUES (?,?,?)",
                        ("game_versions", str(live["live"]), maintenant))
    except Exception:                                     # noqa: BLE001
        pass

    con.commit()
    echo(f"UEX  {len(lignes)} prix, {len(par_terminal)} cotations par terminal, "
         f"{raffineries} raffineries le {maintenant}")
    return len(lignes) + len(par_terminal)


def _raffineries(con: sqlite3.Connection, maintenant: str, echo) -> int:
    """Les raffineries et leur position.

    « À quel endroit je dois raffiner tel minerai » — demande de
    l'utilisateur. Les fichiers du jeu ne portent pas les raffineries ; UEX
    les publie avec leur position complète.

    **On ne rapatrie aucun rendement.** `refineries_audits` existe et ne
    contient que 3 lignes, sur 2 commodités et 2 terminaux : classer
    « la meilleure raffinerie pour le Stileron » là-dessus serait inventer un
    chiffre, ce qu'interdit le §7. Ce que la position permet de dire, en
    revanche, est vrai et utile : laquelle est la mieux placée par rapport aux
    gisements.
    """
    echo("  raffineries…")
    lignes = []
    for terminal in _get("terminals", type="refinery"):
        if not terminal.get("id") or not terminal.get("name"):
            continue
        lignes.append((
            terminal["id"], maintenant, terminal["name"],
            terminal.get("nickname"), terminal.get("star_system_name"),
            terminal.get("planet_name"), terminal.get("orbit_name"),
            terminal.get("moon_name"), terminal.get("space_station_name"),
            terminal.get("outpost_name"), terminal.get("city_name"),
            1 if terminal.get("is_available") else 0,
        ))
    con.executemany(
        "INSERT OR REPLACE INTO refineries "
        "(id, fetched_at, name, nickname, star_system, planet, orbit, moon, "
        " station, outpost, city, available) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        lignes,
    )

    # Le rendement par minerai et par terminal. UEX écrit « Iron (Ore) » et
    # « Quantainium (Raw) » là où le jeu écrit « Iron » : on range le nom nu,
    # sinon aucune recette ne s'y raccroche.
    rendements = []
    for ligne in _get("refineries_yields"):
        nom, valeur = ligne.get("commodity_name"), ligne.get("value")
        if not nom or valeur is None:
            continue
        rendements.append((
            maintenant, re.sub(r"\s*\((?:ore|raw)\)\s*$", "", nom,
                               flags=re.IGNORECASE),
            ligne.get("id_terminal"), ligne.get("terminal_name"),
            ligne.get("star_system_name"), float(valeur),
        ))
    con.executemany(
        "INSERT OR REPLACE INTO refinery_yields "
        "(fetched_at, commodity, terminal_id, terminal, star_system, yield_pct)"
        " VALUES (?,?,?,?,?,?)", rendements)

    # Les **méthodes** ne viennent pas d'ici : le jeu les publie lui-même avec
    # ses trois notes, et le Cirque Lisoir les traduit. UEX les note de 1 à 3,
    # les fichiers du jeu de 1 à 4 sur la vitesse — la source la plus fine, et
    # la seule qui soit traduisible, gagne.
    echo(f"  {len(rendements)} rendements")
    return len(lignes)
