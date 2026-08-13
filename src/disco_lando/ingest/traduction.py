"""Traduction française du Cirque Lisoir (CircusPES) — la vraie localisation.

Le jeu n'est pas traduit en français par CIG : `labels.json`, qui porte tous
les textes, n'a que l'anglais. Une communauté francophone traduit depuis des
années le `global.ini` du jeu — **le même espace de clés**, 90 121 des deux
côtés, vérifié le 2026-08-04. C'est donc une correspondance exacte, pas un
appariement approximatif : `labels.json[clé]` est l'anglais,
`global.ini[clé]` est le français de la même chaîne.

**Ce qu'elle apporte que le wiki n'a pas du tout :**

| table | CircusPES | wiki |
|---|---|---|
| lieux | **2 013 / 2 032 (99 %)** | 0 |
| missions | **3 803 / 4 281 (88 %)** | 0 |
| factions et personnages | **47 / 48** | 0 |
| objets | 6 969 / 7 166 (97 %) | 7 149 |
| vaisseaux | 303 / 315 (96 %) | 278 |

Les deux sources sont **complémentaires, pas concurrentes**. CircusPES est
seule à couvrir les lieux, les missions et les factions ; le wiki reste utile
en secours sur les objets. Là où les deux répondent, CircusPES passe devant :
c'est une traduction humaine des chaînes réelles du jeu, et la comparaison
côte à côte est nette — « les moindres recoins » contre « chaque centimètre ».

**Trois voies pour retrouver la clé d'une entité**, parce qu'aucune ne suffit,
et c'est en les cumulant qu'on passe de 46 % à 97 % sur les objets :

1. par `class_name` — `item_Desc<class_name>`. Attention, l'écart avec les
   clés du jeu suit trois règles : un tiret bas facultatif après `Desc`
   (1 135 objets à lui seul), un suffixe technique `_SCItem`, et la casse.
2. par le **texte anglais**, via `labels.json` inversé. Seule voie pour les
   lieux, les missions et les factions, qui n'ont pas de `class_name`.
3. par la seule **prose**, quand la fiche est fabriquée. C'est ce qui
   débloquait le gros du reste : scunpacked **compose** la fiche d'un objet en
   préfixant un bloc « Item Type / Manufacturer / Size » au texte de CIG.
   `labels.json` ne porte que le texte, donc l'égalité stricte échouait sur
   3 031 objets dont la prose existait pourtant mot pour mot. 2 834 s'y
   retrouvent.

**Les noms aussi sont traduits**, et deviennent des alias : « le pistolet
Coda », « le casque Wrecker Payback ». 4 883 noms, mesurés avant écriture —
**zéro** collision avec un alias anglais pointant vers une autre entité. Un
seul nom sur 4 859 reste inatteignable, « Réservoir de Carburant Quantum » :
tous ses mots appartiennent au vocabulaire d'intention des composants de
vaisseau, et l'extraction d'entité les retire. Un cas sur 4 859 ne justifie
pas de retoucher l'arbitrage du routeur.

**Le texte des missions demande une normalisation.** scunpacked réécrit les
jetons de gabarit du jeu : `~mission(NameSave1)` devient `[NameSave1]`. Sans
cette équivalence, la correspondance tombait de 2 127 à **24** — le genre de
détail qui fait conclure à tort qu'une source est vide.

**Le téléchargement est conditionnel.** Le fichier pèse 11 Mo ; le serveur rend
un `ETag`, qu'on garde pour ne rapatrier que ce qui a changé. Un
rafraîchissement qui retélécharge 11 Mo pour rien est un rafraîchissement qu'on
finit par ne plus lancer.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import urllib.error
import urllib.request

from .. import config

SOURCE = "https://traduction.circuspes.fr/download/global.ini"

ENTETES = {"User-Agent": "chris-roberts/0.3 (+assistant Star Citizen, usage privé)"}

SCHEMA = """
-- Traductions résolues par entité. Table à part, comme UEX et le wiki : le
-- texte français vient d'une communauté, pas des fichiers de CIG.
CREATE TABLE IF NOT EXISTS traductions (
  entity_type  TEXT NOT NULL,
  uuid         TEXT NOT NULL,
  cle          TEXT,          -- la clé de localisation, pour pouvoir remonter
  nom_fr       TEXT,
  description_fr TEXT,
  PRIMARY KEY (entity_type, uuid)
);

-- De quoi éviter de retélécharger 11 Mo pour rien, et de savoir quelle
-- version de la traduction est en base.
CREATE TABLE IF NOT EXISTS traduction_source (
  url         TEXT PRIMARY KEY,
  etag        TEXT,
  version     TEXT,           -- ex. « 4.9-1.15-20260801 »
  couverture  TEXT,           -- ex. « 115% »
  fetched_at  TEXT
);
"""

# scunpacked réécrit les jetons du jeu. Sans cette équivalence, 2 127 textes de
# mission sur 2 151 ne se retrouvaient pas.
_JETON = re.compile(r"~mission\(([^)]*)\)")

# Les guillemets et apostrophes typographiques diffèrent d'un pipeline à
# l'autre pour la même chaîne : on compare le sens, pas la ponctuation.
_PONCTUATION = str.maketrans({"’": "'", "‘": "'",
                              "“": '"', "”": '"'})


def _cle_de_texte(texte: str | None) -> str:
    """Forme comparable d'un texte anglais, des deux côtés du pont."""
    if not texte:
        return ""
    texte = _JETON.sub(r"[\1]", texte)
    texte = unicodedata.normalize("NFKC", texte).translate(_PONCTUATION)
    texte = texte.replace("\\n", "\n")
    return re.sub(r"\s+", " ", texte).strip().lower()


# Le `.ini` stocke les sauts de ligne en deux caractères, « \ » puis « n ».
# Les rendre tels quels affiche « Rôle : Chasseur Léger\n\nLe Gladius est… ».
_ECHAPPEMENTS = (("\\n", "\n"), ("\\r", ""), ("\\t", "\t"))

# Presque toutes les fiches s'ouvrent sur un bloc « Constructeur : … / Rôle :
# … », suivi d'une ligne vide. C'est de la statistique, pas de la description,
# et `decrire` propose déjà les caractéristiques à part — « décrire n'est pas
# chiffrer ». On coupe au premier paragraphe vide, jamais au-delà.
#
# **Au moins deux lignes, et pas de virgule dans l'étiquette.** Une seule
# ligne suffisait à faire passer une vraie phrase pour un en-tête : le
# briefing de « The Price of Freedom » ouvre sur « Écoute, voilà le topo :
# Quatre de mes gars… » et perdait son premier paragraphe. Un bloc de
# statistiques en a toujours au moins deux, et ses étiquettes sont des
# syntagmes, pas des propositions — c'est la **virgule** qui les sépare, pas
# la longueur ni le point : « Vitesse d'Élimination des Radiations » fait
# 37 caractères et « Résistance Therm. » porte un point. Les deux sont de
# vraies étiquettes, et les exclure laissait le bloc s'afficher.
_PREAMBULE = re.compile(
    r"\A(?:[^\n:,!?]{1,45}\s*:[^\n]*\n){2,8}\s*\n", re.MULTILINE)


def _nettoyer(texte: str | None) -> str | None:
    if not texte:
        return None
    for avant, apres in _ECHAPPEMENTS:
        texte = texte.replace(avant, apres)
    # Les jetons de gabarit du jeu : le serveur les remplace à la génération du
    # contrat, nous n'avons que le gabarit. `render` sait déjà élaguer la forme
    # `[X]` ; on ramène `~mission(X)` à cette forme plutôt que d'apprendre les
    # deux au rendu.
    texte = _JETON.sub(r"[\1]", texte)
    ampute = _PREAMBULE.sub("", texte).strip()
    # Une fiche qui n'est *que* le bloc de statistiques ne doit pas devenir
    # vide : mieux vaut la rendre entière que ne rien dire.
    return ampute or texte.strip() or None


# Les `class_name` de scunpacked ne sont pas exactement ceux du jeu, et l'écart
# suit trois règles, mesurées et non devinées :
#
# * un tiret bas facultatif après `Desc` / `Name` — `item_Desc_SHLD_BEHR_S01_6SA`
#   existe à côté de `item_DescSHLD_ASAS_S02_Shroud`. À lui seul, 1 135 objets ;
# * un suffixe technique ajouté par l'extraction — `_SCItem` et ses variantes ;
# * la casse, qui ne suit pas toujours.
_SUFFIXES = ("_SCItem", "_SCItemVehicleWeapon", "_SCItemManufacturer",
             "_SCItemWeaponComponentParams")


def _candidats(prefixe: str, champ: str, classe: str):
    """Toutes les formes plausibles d'une clé, de la plus sûre à la moins."""
    bases = [classe]
    for suffixe in _SUFFIXES:
        if classe.endswith(suffixe):
            bases.append(classe[: -len(suffixe)])
    for base in bases:
        yield f"{prefixe}_{champ}{base}"
        yield f"{prefixe}_{champ}_{base}"


def _entete(texte: str, champ: str) -> str | None:
    """Une valeur de l'en-tête en commentaire du fichier."""
    motif = re.compile(rf"^;\s*{champ}\s*:\s*(.+)$", re.MULTILINE)
    trouve = motif.search(texte)
    return trouve.group(1).strip() if trouve else None


def _lire_ini(texte: str) -> dict[str, str]:
    """Le fichier est un `clé=valeur` par ligne, commentaires en `;`."""
    paires: dict[str, str] = {}
    for ligne in texte.splitlines():
        if not ligne or ligne.lstrip().startswith(";") or "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        cle, valeur = cle.strip(), valeur.strip()
        if cle and valeur:
            paires[cle] = valeur
    return paires


def chemin_cache() -> "config.pathlib.Path":
    return config.DATA_DIR / "raw" / "traduction-fr.ini"


def _etag_connu(con: sqlite3.Connection) -> str | None:
    try:
        ligne = con.execute("SELECT etag FROM traduction_source WHERE url = ?",
                            (SOURCE,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return ligne[0] if ligne else None


def telecharger(con: sqlite3.Connection, *, echo=print,
                force: bool = False) -> tuple[str | None, str | None]:
    """Rapatrie le fichier s'il a changé. Rend (texte, etag).

    `texte` vaut `None` si rien n'a changé **et** qu'un cache existe déjà : le
    fichier local sert alors, sans un octet de réseau.
    """
    entetes = dict(ENTETES)
    connu = None if force else _etag_connu(con)
    if connu and chemin_cache().exists():
        entetes["If-None-Match"] = connu
    try:
        with urllib.request.urlopen(                       # noqa: S310
                urllib.request.Request(SOURCE, headers=entetes), timeout=120) as rep:
            brut = rep.read()
            etag = rep.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            echo("  traduction inchangée — on relit le cache local")
            return None, connu
        echo(f"[yellow]traduction injoignable (HTTP {exc.code})[/]")
        return None, connu
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        echo(f"[yellow]traduction injoignable ({type(exc).__name__})[/]")
        return None, connu

    chemin_cache().parent.mkdir(parents=True, exist_ok=True)
    chemin_cache().write_bytes(brut)
    return brut.decode("utf-8-sig", "replace"), etag


def _prose(texte: str | None) -> str:
    """La partie rédigée d'une fiche, sans le bloc de statistiques en tête.

    C'est ce qui débloquait le gros du reste. scunpacked **fabrique** la fiche
    d'un objet : il préfixe un bloc « Item Type / Manufacturer / Size » de sa
    composition au texte que CIG a écrit. `labels.json` ne porte, lui, que le
    texte. L'égalité stricte échouait donc sur 3 031 objets dont la prose
    existait pourtant mot pour mot — 2 834 se retrouvent ainsi.

    On prend la queue, après la dernière ligne vide : le bloc généré est
    toujours devant.
    """
    if not texte:
        return ""
    texte = texte.replace("\\n", "\n")
    return _cle_de_texte(texte.split("\n\n")[-1])


# Une prose courte n'identifie rien : « Un casque. » se retrouverait dans
# quarante fiches. En dessous de ce seuil on s'en remet aux autres voies.
_PROSE_MINIMALE = 60


def _index_anglais(labels: dict[str, str]) -> dict[str, str]:
    """Texte anglais normalisé → clé de localisation.

    Une même chaîne peut porter plusieurs clés (trois variantes d'un même
    briefing de mission, par exemple). On garde la première : leurs traductions
    sont les mêmes à la ponctuation près, et choisir entre elles n'apporterait
    rien.
    """
    index: dict[str, str] = {}
    for cle, anglais in labels.items():
        index.setdefault(_cle_de_texte(anglais), cle)
    return index


def _index_prose(labels: dict[str, str]) -> dict[str, str]:
    """Même index, mais sur la seule prose — pour les fiches fabriquées."""
    index: dict[str, str] = {}
    for cle, anglais in labels.items():
        prose = _prose(anglais)
        if len(prose) >= _PROSE_MINIMALE:
            index.setdefault(prose, cle)
    return index


# Quelle table porte quel préfixe de clé. `starmap` et `contracts` n'ont pas de
# `class_name` : ils passent uniquement par le texte, ce qui suffit largement
# (99 % des lieux).
_TABLES = (
    ("item", "items", "item"),
    ("ship", "ships", "vehicle"),
    ("starmap", "starmap", None),
    ("contract", "contracts", None),
    # Les factions portent aussi les **personnages** — « c'est qui Wikelo »
    # passe par là. 47 des 48 fiches décrites se traduisent.
    ("org", "factions", None),
)


def sync(con: sqlite3.Connection, *, echo=print, force: bool = False) -> int:
    """Rapatrie et résout la traduction. Rend le nombre de lignes écrites."""
    fichier = config.SOURCE_DIR / "labels.json"
    if not fichier.exists():
        echo("[red]labels.json absent[/] — lance « disco ingest » d'abord : "
             "c'est lui qui porte l'anglais que la traduction éclaire.")
        return 0

    texte, etag = telecharger(con, echo=echo, force=force)
    if texte is None:
        if not chemin_cache().exists():
            echo("[red]aucune traduction, ni en ligne ni en cache[/].")
            return 0
        texte = chemin_cache().read_bytes().decode("utf-8-sig", "replace")

    version = _entete(texte, "Version")
    couverture = _entete(texte, r"Traduction effectuée")
    echo(f"  traduction {version or '?'} — {couverture or '?'} traduit")

    # La version de la traduction commence par la version du jeu : « 4.9-1.15… »
    # pour la 4.9. Un texte de la 4.9 collé sur des données de la 4.10 décrirait
    # des objets qui ont changé de nom — et rien ne le signalerait.
    ligne = con.execute("SELECT game_version FROM ingest_runs WHERE status = 'ok' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    jeu = ligne[0] if ligne else None
    if jeu and version and not version.startswith(_majeure(jeu)):
        if not force:
            echo(f"[yellow]traduction pour {version.split('-')[0]}, base en "
                 f"{jeu} — ingestion refusée.[/] Force avec --force en sachant "
                 "que les textes décrivent un autre patch.")
            return 0
        echo("[yellow]versions différentes, forcé[/].")

    labels = json.loads(fichier.read_text(encoding="utf-8-sig"))
    labels = {k.lstrip("﻿"): v for k, v in labels.items()}
    ini = _lire_ini(texte)
    par_texte = _index_anglais(labels)
    par_prose = _index_prose(labels)
    # La casse des `class_name` de scunpacked ne suit pas toujours celle des
    # clés du jeu — quatorze vaisseaux se perdaient sur ce seul détail.
    sans_casse = {c.lower(): c for c in ini}

    def cle_reelle(cle: str | None) -> str | None:
        if not cle:
            return None
        return cle if cle in ini else sans_casse.get(cle.lower())

    def francais(cle: str | None) -> str | None:
        """Le français d'une clé — s'il est vraiment traduit.

        Le fichier reprend l'anglais pour ce qui reste à traduire. Rendre ce
        texte-là serait pire que rien : on croirait avoir traduit.
        """
        cle = cle_reelle(cle)
        if not cle:
            return None
        valeur = ini.get(cle)
        if not valeur:
            return None
        return valeur if valeur.strip() != (labels.get(cle) or "").strip() else None

    con.executescript(SCHEMA)
    con.execute("DELETE FROM traductions")

    ecrits = 0
    for entity_type, table, prefixe in _TABLES:
        nom_col = "title" if table == "contracts" else "name"
        colonnes = f"uuid, description, {nom_col}" + (", class_name" if prefixe else "")
        for r in con.execute(f"SELECT {colonnes} FROM {table}").fetchall():
            uuid, description, nom = r[0], r[1], r[2]
            classe = r[3] if prefixe else None

            # Une « description » qui n'est que le nom n'a rien à traduire, et
            # la traduire donne pire que l'original : « Crusader Security »
            # ressortait en « SÉcuritÉ de Crusader ». Le nom propre d'une
            # organisation ne se traduit pas — surtout mal.
            if _cle_de_texte(description) == _cle_de_texte(nom):
                continue
            # La clé par nom de classe d'abord : elle est exacte par
            # construction, là où le texte peut collisionner.
            cle = desc = None
            if prefixe and classe:
                for essai in _candidats(prefixe, "Desc", classe):
                    desc = francais(essai)
                    if desc:
                        cle = cle_reelle(essai)
                        break
            if desc is None:
                # D'abord la fiche entière — sans ambiguïté possible — puis la
                # seule prose, pour les fiches que scunpacked a fabriquées.
                for index, sonde in ((par_texte, _cle_de_texte),
                                     (par_prose, _prose)):
                    autre = index.get(sonde(description))
                    if autre and francais(autre):
                        cle, desc = autre, francais(autre)
                        break
            nom = None
            if prefixe and classe:
                for essai in _candidats(prefixe, "Name", classe):
                    nom = francais(essai)
                    if nom:
                        break
            if desc is None and nom is None:
                continue
            con.execute("INSERT OR REPLACE INTO traductions VALUES (?,?,?,?,?)",
                        (entity_type, uuid, cle, _nettoyer(nom), _nettoyer(desc)))
            ecrits += 1

    # **Les traductions d'abord, l'indexation ensuite.** `_alias_francais` lit
    # `traductions` : tout ce qui s'y écrit après lui n'est pas indexé, et sans
    # un mot — les 165 noms de minerais ont produit **zéro** alias avant que
    # l'ordre ne soit corrigé. Même famille de piège que l'`INSERT OR IGNORE`
    # qui avale une violation de contrainte : relire ce qui est entré.
    _commodites_francaises(con, francais, echo=echo)
    _alias_francais(con, echo=echo)
    consignes = _consignes_francaises(con, francais, echo=echo)
    _lieux_de_mission_francais(con, par_texte, francais, echo=echo)
    _constructeurs_francais(con, francais, echo=echo)
    _methodes_francaises(con, francais, echo=echo)
    _libelles_de_qualite(con, labels, francais, echo=echo)
    con.execute("INSERT OR REPLACE INTO traduction_source VALUES (?,?,?,?,"
                "datetime('now'))", (SOURCE, etag, version, couverture))
    con.commit()

    detail = ", ".join(
        f"{t} {con.execute('SELECT COUNT(*) FROM traductions WHERE entity_type = ? '
                           'AND description_fr IS NOT NULL', (t,)).fetchone()[0]}"
        for t, _, _ in _TABLES)
    echo(f"traduction  {ecrits} entités — descriptions : {detail} — "
         f"{consignes} consignes de mission")
    return ecrits


def _commodites_francaises(con: sqlite3.Connection, francais, *,
                           echo=print) -> int:
    """Les minerais et marchandises en français, et leurs gisements avec.

    **Elles n'étaient traduites nulle part**, et ça se voyait : « où raffiner
    de l'or » a répondu sur le **Fer**, faute d'un « or » qui résolve. Les
    noms existent pourtant — `items_commodities_gold` vaut « Or » — 97 sur 206.

    La clé se déduit du champ `key`, et non du nom : « CadmiumAllinide » d'un
    côté, « Cadmium Allinide » de l'autre.

    On propage ensuite vers `resources` : un gisement s'appelle
    `MineableRock_AsteroidRare_Gold`, et c'est le même minerai. Sans ça, « où
    trouver de l'or » resterait muet alors que « où en acheter » répondrait.
    """
    if not _table(con, "commodities"):
        return 0
    ecrits = []
    par_nom: dict[str, str] = {}
    for uuid, cle, nom in con.execute(
            "SELECT uuid, key, name FROM commodities WHERE key IS NOT NULL"):
        base = f"items_commodities_{cle.lower()}"
        nom_fr = _nettoyer(francais(base) or francais(f"{base},P"))
        desc_fr = _nettoyer(francais(f"{base}_desc")
                            or francais(f"{base}_desc,P"))
        if nom_fr or desc_fr:
            ecrits.append(("commodity", uuid, base, nom_fr, desc_fr))
        if nom_fr and nom:
            par_nom[nom.lower()] = nom_fr

    # Le gisement porte le nom du minerai en suffixe : on relie par ce suffixe
    # plutôt que par une clé, qui n'existe pas côté `resources`.
    for uuid, nom in con.execute(
            "SELECT uuid, name FROM resources WHERE name IS NOT NULL"):
        trouve = _GABARIT_GISEMENT.match(nom)
        minerai = (trouve.group(1) if trouve else nom).lower()
        if (nom_fr := par_nom.get(minerai)):
            ecrits.append(("resource", uuid, None, nom_fr, None))

    con.executemany("INSERT OR REPLACE INTO traductions VALUES (?,?,?,?,?)",
                    ecrits)
    echo(f"  minerais et marchandises {len(ecrits)}")
    return len(ecrits)


_GABARIT_GISEMENT = re.compile(
    r"^MineableRock_\w*?(?:Common|Uncommon|Rare|Epic|Legendary|test)_(\w+)$",
    re.IGNORECASE)


def _methodes_francaises(con: sqlite3.Connection, francais, *,
                         echo=print) -> int:
    """Le nom et la description des méthodes de raffinage, en français.

    La clé se reconstruit depuis celle qu'on a gardée à l'ingestion :
    `refinery_ui_ProcessingType_SlowCareful` et son `_Desc`. Les neuf sont
    traduites.
    """
    if not _table(con, "refinery_methods"):
        return 0
    ecrits = []
    for (cle,) in con.execute("SELECT cle FROM refinery_methods"):
        base = f"refinery_ui_ProcessingType_{cle}"
        # Le fichier français indexe le **nom** sous la clé suffixée « ,P » et
        # la description sous la clé nue : essayer les deux, sinon le nom
        # reste vide alors que la traduction existe.
        nom = francais(base) or francais(f"{base},P")
        desc = francais(f"{base}_Desc") or francais(f"{base}_Desc,P")
        if nom or desc:
            ecrits.append((_nettoyer(nom), _nettoyer(desc), cle))
    con.executemany("UPDATE refinery_methods SET nom_fr = ?, "
                    "description_fr = ? WHERE cle = ?", ecrits)
    echo(f"  méthodes de raffinage {len(ecrits)}")
    return len(ecrits)


def _constructeurs_francais(con: sqlite3.Connection, francais, *,
                            echo=print) -> int:
    """Les fiches de constructeur en français.

    La clé se déduit du **code** — `manufacturer_DescAEGS` — et les 123 fiches
    sont traduites, toutes. Sans ça, « quelle est la marque du Vanguard »
    rendait un paragraphe entier en anglais au milieu d'une réponse française.
    """
    if not _table(con, "manufacturers"):
        return 0
    ecrits = []
    for code, nom in con.execute(
            "SELECT code, name FROM manufacturers WHERE code IS NOT NULL"):
        desc = francais(f"manufacturer_Desc{code}")
        if desc:
            ecrits.append(("manufacturer", code, f"manufacturer_Desc{code}",
                           _nettoyer(francais(f"manufacturer_Name{code}"))
                           or nom, _nettoyer(desc)))
    con.executemany("INSERT OR REPLACE INTO traductions VALUES (?,?,?,?,?)",
                    ecrits)
    echo(f"  constructeurs {len(ecrits)}")
    return len(ecrits)


def _consignes_francaises(con: sqlite3.Connection, francais, *,
                          echo=print) -> int:
    """Les consignes de mission en français.

    Elles vivent dans le **même espace de clés** que le reste — c'est tout
    l'intérêt du Cirque Lisoir, qui traduit le `global.ini` du jeu. Mesuré :
    les 1 306 clés `_obj_long_` sont traduites **en entier**, contre 88 % pour
    les titres de mission. On garde donc la clé à l'ingestion et on la résout
    ici, plutôt que de recopier l'anglais.
    """
    if not _table(con, "contract_objectives"):
        return 0
    lignes = [(cle,) for (cle,) in con.execute(
        "SELECT DISTINCT cle_texte FROM contract_objectives "
        "WHERE cle_texte IS NOT NULL")]
    traduites = []
    for (cle,) in lignes:
        # La clé peut porter le suffixe « ,P » côté labels ; le fichier
        # français indexe les deux formes selon les entrées.
        texte = francais(cle) or francais(cle.removesuffix(",P"))
        if texte:
            traduites.append((_nettoyer(texte), cle))
    con.executemany(
        "UPDATE contract_objectives SET texte_fr = ? WHERE cle_texte = ?",
        traduites)
    echo(f"  consignes de mission {len(traduites)}/{len(lignes)}")
    return len(traduites)


def _lieux_de_mission_francais(con: sqlite3.Connection, par_texte, francais, *,
                               echo=print) -> int:
    """Les lieux de mission en français — 1 535 réponses qui étaient en anglais.

    Un lieu de mission porte souvent une **périphrase** et non un nom propre :
    « the clinic inside Megumi Refueling at the L5 Lagrange of Pyro VI ». Le
    balayage les signalait comme de l'anglais laissé dans une réponse
    française, et j'en ai d'abord conclu à une lacune de la source.

    C'était faux, et vérifiable en deux requêtes : les 40 testés sont **tous**
    dans `labels.json` sous des clés propres, et le Cirque Lisoir les traduit
    tous. Le nom n'étant pas une clé mais du texte, on passe par l'index
    anglais inversé — la même voie que pour les fiches d'objet.

    On traduit les noms **distincts**, pas les 1,4 million de lignes : ils sont
    quelques centaines, et la table les répète pour chaque contrat.
    """
    if not _table(con, "contract_locations"):
        return 0
    noms = [n for (n,) in con.execute(
        "SELECT DISTINCT location_name FROM contract_locations "
        "WHERE location_name IS NOT NULL")]
    traduits = []
    for nom in noms:
        cle = par_texte.get(_cle_de_texte(nom))
        texte = francais(cle) if cle else None
        if texte and _nettoyer(texte) != nom:
            traduits.append((_nettoyer(texte), nom))
    con.executemany(
        "UPDATE contract_locations SET location_name_fr = ? "
        "WHERE location_name = ?", traduits)
    echo(f"  lieux de mission {len(traduits)}/{len(noms)}")
    return len(traduits)


def _libelles_de_qualite(con: sqlite3.Connection, labels, francais, *,
                         echo=print) -> int:
    """Les statistiques et les composants de fabrication, en français.

    Le premier réflexe — chercher le libellé anglais dans l'index inversé —
    donne de faux amis mesurés : « Shell » y sort `Human_First_Names_F_1373`,
    un prénom, et « Laser Power » a deux clés dont l'une est un contrôle de
    minage orbital. Le jeu porte pourtant la correspondance dans sa **façon de
    nommer ses clés** : `StatName_GPP_<Key>` reprend exactement la clé du
    modificateur, et `crafting_ui_slotname_<slot>` son composant. C'est la même
    leçon que le lien salle → complexe : chercher la convention avant d'écrire
    une table à la main.

    Couverture mesurée : 17 statistiques sur 24 et 51 composants sur 56. Les
    manquantes — rayon tracteur, découpe de coque — n'ont **aucune** clé
    `StatName_GPP_`, et non pas une traduction absente : on laisse l'anglais du
    jeu plutôt que d'aller le chercher par un chemin qui se trompe.
    """
    if not _table(con, "blueprint_modifiers"):
        return 0

    def index(prefixe: str) -> dict[str, str]:
        n = len(prefixe)
        return {re.sub(r"[^a-z0-9]", "", k[n:].removesuffix(",P").lower()): k
                for k in labels if k.startswith(prefixe)}

    stats, slots = index("StatName_GPP_"), index("crafting_ui_slotname_")

    faits = 0
    for cle, in con.execute("SELECT DISTINCT cle FROM blueprint_modifiers"):
        k = stats.get(re.sub(r"[^a-z0-9]", "", cle.lower()))
        texte = _nettoyer(francais(k)) if k else None
        if texte:
            con.execute("UPDATE blueprint_modifiers SET nom_fr = ? WHERE cle = ?",
                        (texte, cle))
            faits += 1

    composants = 0
    for g, in con.execute("SELECT DISTINCT group_name FROM blueprint_modifiers "
                          "WHERE group_name IS NOT NULL"):
        k = slots.get(re.sub(r"[^a-z0-9]", "", g.lower()))
        texte = _nettoyer(francais(k)) if k else None
        if texte:
            con.execute("UPDATE blueprint_modifiers SET group_name_fr = ? "
                        "WHERE group_name = ?", (texte, g))
            composants += 1

    echo(f"  libellés de fabrication {faits} stats, {composants} composants")
    return faits + composants


def _table(con: sqlite3.Connection, nom: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (nom,)).fetchone())


def _alias_francais(con: sqlite3.Connection, *, echo=print) -> int:
    """Rend les noms français interrogeables : « le pistolet Coda ».

    Mesuré avant d'écrire quoi que ce soit : sur 4 883 noms traduits, **zéro
    collision** avec un alias anglais existant pointant vers une autre entité,
    et 23 ambiguïtés entre noms français — toutes des doublons que la version
    anglaise porte déjà (trois « Quantum Fuel Tank » identiques, deux nuances
    de la même chemise). Le mécanisme de variantes les traite déjà.

    **Poids 1,0, comme le nom anglais.** Le mettre à 0,9 paraissait prudent —
    « le nom officiel gagne à score égal » — et c'était un faux raisonnement :
    les deux noms désignent la **même** entité, donc l'écart ne départage rien
    entre eux, et il fait perdre l'entité face à une **autre**. Mesuré : « le
    casque Wrecker Payback » plafonnait à 90 au lieu de 100 et se faisait
    battre par le contrat « Payback », à 90 lui aussi et premier dans l'ordre
    des types. La version anglaise, elle, sortait à 100 et gagnait.
    """
    from ..normalize import normalize

    # Sans les tables du résolveur, il n'y a rien à indexer — cas d'une base
    # partielle. On le dit et on s'arrête là plutôt que de faire échouer toute
    # la traduction, qui, elle, est déjà écrite.
    presentes = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('aliases', 'alias_tokens', 'aliases_fts')")}
    if not {"aliases", "alias_tokens", "aliases_fts"} <= presentes:
        echo("  [yellow]pas de table d'alias — noms français non indexés[/]")
        return 0

    # On ne touche qu'à nos propres lignes : une réingestion les recrée, mais
    # `disco trad` peut aussi être relancé seul entre deux ingestions.
    con.execute("DELETE FROM alias_tokens WHERE alias_id IN "
                "(SELECT id FROM aliases WHERE source = 'circuspes')")
    con.execute("DELETE FROM aliases WHERE source = 'circuspes'")

    # **Le nom français doit suivre jusqu'au blueprint.** Le catalogue relie un
    # blueprint à l'objet qu'il produit, mais ses alias viennent du nom
    # **anglais** de la sortie : « arbalète » résolvait bien *Novian Crossbow*
    # côté objet et rien du tout côté blueprint, si bien que « où se trouve le
    # blueprint de l'arbalète » restait muet. Remarque du journal.
    blueprints = {r[0]: r[1] for r in con.execute(
        "SELECT output_uuid, uuid FROM blueprints "
        "WHERE output_uuid IS NOT NULL")} if _table(con, "blueprints") else {}

    lignes = []
    for r in con.execute("SELECT entity_type, uuid, nom_fr FROM traductions "
                         "WHERE nom_fr IS NOT NULL").fetchall():
        norme = normalize(r[2])
        if not norme:
            continue
        lignes.append((r[0], r[1], r[2], norme, norme.replace(" ", ""),
                       "fr", "circuspes", 1.0))
        bp = blueprints.get(r[1])
        if bp:
            lignes.append(("blueprint", bp, r[2], norme,
                           norme.replace(" ", ""), "fr", "circuspes", 1.0))
    con.executemany(
        "INSERT OR IGNORE INTO aliases (entity_type, entity_id, alias, "
        "alias_norm, alias_flat, lang, source, weight) VALUES (?,?,?,?,?,?,?,?)",
        lignes)

    # `INSERT OR IGNORE` avale **aussi** les violations de CHECK, pas seulement
    # les doublons. Le vocabulaire fermé de `aliases.source` a rejeté les
    # 4 883 noms en silence tant que « circuspes » n'y figurait pas : rien ne
    # levait, le compte affiché restait juste, et seule une question posée en
    # français l'aurait révélé. On vérifie donc ce qui est vraiment entré.
    entres = con.execute("SELECT COUNT(*) FROM aliases "
                         "WHERE source = 'circuspes'").fetchone()[0]
    if lignes and not entres:
        raise RuntimeError(
            "aucun alias français inséré alors que "
            f"{len(lignes)} étaient prêts — la base date-t-elle d'avant "
            "l'ajout de « circuspes » au CHECK de `aliases.source` ? "
            "Relance « disco ingest ».")

    # Table FTS5 à contenu externe : elle se reconstruit par commande, jamais
    # par DELETE + INSERT, qui corrompt l'index.
    con.execute("INSERT INTO aliases_fts (aliases_fts) VALUES ('rebuild')")

    # Et l'étage `token` du résolveur travaille sur `alias_tokens` : sans ça,
    # « pistolet » ne retrouverait rien dans « Pistolet Coda ».
    jetons = []
    for r in con.execute("SELECT id, alias_norm FROM aliases "
                         "WHERE source = 'circuspes'").fetchall():
        jetons += [(r[0], mot) for mot in dict.fromkeys(r[1].split())
                   if len(mot) >= 3]
    con.executemany("INSERT INTO alias_tokens (alias_id, token) VALUES (?,?)",
                    jetons)
    echo(f"  alias français {len(lignes)}, {len(jetons)} jetons")
    return len(lignes)


def _majeure(version: str) -> str:
    """« 4.9.0 » → « 4.9 », la granularité qu'annonce la traduction."""
    morceaux = version.split(".")
    return ".".join(morceaux[:2]) if len(morceaux) >= 2 else version


def version_en_base(con: sqlite3.Connection) -> str | None:
    try:
        ligne = con.execute("SELECT version FROM traduction_source WHERE url = ?",
                            (SOURCE,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return ligne[0] if ligne else None
