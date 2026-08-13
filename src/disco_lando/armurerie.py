"""L'armurerie — armes, accessoires, objets : fiches, montages, classements.

Découpé de `queries.py` le 2026-08-07, mécaniquement — même règle que les
missions, le commerce et la flotte.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .constructeurs import _constructeur
from .normalize import normalize
from .resolver import resolve
from .stats import MOUNTABLE, STATS, VOISINES, _METRIQUES, _weapon_filter


def get_compatible_items(con: sqlite3.Connection, query: str,
                         *, category: str | None = None,
                         limit: int = 20) -> dict[str, Any]:
    """« Quel canon balistique je peux monter sur un Gladius ? »

    La compatibilité se lit dans les ports du vaisseau : un type accepté et une
    fourchette de tailles. On croise avec le catalogue d'objets, on classe par
    DPS, et on dit sur quels emplacements ça se monte.
    """
    res = resolve(con, query, entity_types=("ship",))
    if not res.best:
        raise NotFound(query, res)
    ship = _dict(_row(con, "SELECT * FROM ships WHERE uuid = ?", res.best.entity_id))
    if ship is None:
        raise NotFound(query, res)

    # Les emplacements d'arme pilote : le port porte la taille, l'arme montée
    # dessus donne le type attendu.
    ports = [dict(r) for r in con.execute(
        "SELECT hardpoint_name, min_size, max_size, installed_name, category "
        "FROM hardpoints WHERE ship_uuid = ? AND category IN ('weapon','weapon_mount') "
        "AND depth <= 1 ORDER BY max_size DESC, hardpoint_name",
        (ship["uuid"],),
    )]
    tailles = sorted({p["max_size"] for p in ports if p["max_size"]})
    if not tailles:
        return {"ship": ship, "resolution": res, "ports": [], "items": [],
                "sizes": [], "filter": ""}

    where, args, libelle = _weapon_filter(category or query)
    items = [dict(r) for r in con.execute(
        f"SELECT i.uuid, i.name, i.size, i.manufacturer_name, st.* "
        f"FROM items i JOIN item_stats st ON st.item_uuid = i.uuid "
        f"{MOUNTABLE} "
        f"WHERE i.type = 'WeaponGun' AND i.name IS NOT NULL "
        f"  AND i.size IN ({','.join('?' * len(tailles))}) AND {where} "
        f"ORDER BY st.dps DESC LIMIT ?",
        (*tailles, *args, limit),
    )]
    return {"ship": ship, "resolution": res, "ports": ports,
            "sizes": tailles, "items": items, "filter": libelle}


def compare_items(con: sqlite3.Connection, query: str, *, stat: str = "dps",
                  size: int | None = None, limit: int = 10) -> dict[str, Any]:
    """« Le meilleur canon balistique en DPS. »

    **Un classement toutes tailles confondues n'a aucun intérêt** : un S8
    écrase un S1 sur toutes les statistiques, et le S8 en question est souvent
    le canon d'un capital-ship qu'aucun joueur ne montera. Sans taille précisée,
    on répond donc par le meilleur de chaque taille — ce qui est à la fois
    honnête et directement utile, le joueur sachant quelle taille porte son
    vaisseau.
    """
    if stat not in STATS:
        stat = "dps"
    assert stat in STATS, stat
    where, args, libelle = _weapon_filter(query)
    ordre = "DESC" if STATS[stat][2] else "ASC"

    if size:
        rows = [dict(r) for r in con.execute(
            f"SELECT i.uuid, i.name, i.size, i.manufacturer_name, st.* "
            f"FROM items i JOIN item_stats st ON st.item_uuid = i.uuid "
            f"{MOUNTABLE} "
            f"WHERE i.name IS NOT NULL AND st.{stat} IS NOT NULL AND {where} "
            f"AND i.size = ? ORDER BY st.{stat} {ordre} LIMIT ?",
            (*args, size, limit),
        )]
        if not rows:
            raise NotFound(query)
        return {"items": rows, "by_size": [], "stat": stat,
                "stat_label": STATS[stat][0], "stat_unit": STATS[stat][1],
                "higher_is_better": STATS[stat][2], "filter": libelle,
                "size": size, "resolution": None}

    toutes = [dict(r) for r in con.execute(
        f"SELECT i.uuid, i.name, i.size, i.manufacturer_name, st.* "
        f"FROM items i JOIN item_stats st ON st.item_uuid = i.uuid "
        f"{MOUNTABLE} "
        f"WHERE i.name IS NOT NULL AND st.{stat} IS NOT NULL AND {where} "
        f"AND i.size IS NOT NULL ORDER BY i.size, st.{stat} {ordre}",
        args,
    )]
    if not toutes:
        raise NotFound(query)

    par_taille: dict[int, list[dict]] = {}
    for item in toutes:
        par_taille.setdefault(item["size"], []).append(item)
    by_size = [
        {"size": taille, "best": items[0], "count": len(items),
         "runners_up": items[1:3]}
        for taille, items in sorted(par_taille.items())
    ]
    return {"items": [g["best"] for g in by_size], "by_size": by_size,
            "stat": stat, "stat_label": STATS[stat][0],
            "stat_unit": STATS[stat][1], "higher_is_better": STATS[stat][2],
            "filter": libelle, "size": None, "resolution": None}


def accessoires_compatibles(con: sqlite3.Connection, query: str, *,
                            famille: str | None = None,
                            limit: int = 12) -> dict[str, Any]:
    """« Quelles optiques vont sur un P8-AR ? »

    `stdItem.Ports` déclare, pour chaque arme, ce que chaque emplacement
    accepte : le P8-AR a un `optics_attach` qui prend
    `WeaponAttachment.IronSight` en tailles 1 à 2. Rien ne lisait ces ports —
    `hardpoints` ne couvre que les vaisseaux — et la question tombait dans le
    silence.

    Le filtrage se fait sur **le type et la taille**, comme pour les vaisseaux.
    Un accessoire trop gros pour le rail ne se monte pas, et le proposer serait
    une fausse promesse.
    """
    res = resolve(con, query, entity_types=("item",))
    if res.best is None:
        raise NotFound(query, res)
    arme = _dict(_row(con, "SELECT uuid, name, type, subtype FROM items "
                           "WHERE uuid = ?", res.best.entity_id))
    if arme is None:
        raise NotFound(query, res)

    ports = [dict(r) for r in con.execute(
        "SELECT port_name, accepted, min_size, max_size FROM item_ports "
        "WHERE item_uuid = ? AND accepted LIKE 'WeaponAttachment.%'",
        (arme["uuid"],))]
    if famille:
        ports = [p for p in ports if p["accepted"].endswith("." + famille)]

    par_famille: dict[str, list[dict[str, Any]]] = {}
    for port in ports:
        sous_type = port["accepted"].split(".", 1)[-1]
        # **Les chargeurs sont propres à l'arme, les autres accessoires non.**
        # Mesuré : les optiques (« Omarof (16x Telescopic) »), les canons
        # (« Veil Flash Hider ») et les sous-canons (« Tracer Laser Pointer »)
        # sont universels, et la taille du port suffit à les borner. Un
        # chargeur, lui, porte le nom de son arme — sans cette distinction, le
        # P8-AR se voyait proposer une batterie de pistolet Arclight. C'est la
        # règle que `get_item_stats` applique déjà.
        if sous_type == "Magazine":
            compatibles = [dict(r) for r in con.execute(
                "SELECT name, size, manufacturer_name FROM items "
                "WHERE subtype = 'Magazine' AND name IS NOT NULL "
                "  AND name LIKE ? GROUP BY name ORDER BY name",
                (f"{(arme.get('name') or '').split(' (')[0]}%",))]
        else:
            # La taille du port borne ce qui s'y monte : un port 1-2 accepte
            # les accessoires de taille 1 et 2, pas ceux de taille 3.
            # **Ce qui est en vente d'abord.** Un accessoire qu'on ne peut
            # acheter nulle part n'est pas une mauvaise réponse, mais c'est une
            # mauvaise **première** réponse : « où les acheter » proposait la
            # Delta « Scorched », introuvable, alors que la Delta simple est
            # relevée dans 28 boutiques.
            compatibles = [dict(r) for r in con.execute(
                "SELECT i.name, i.size, i.manufacturer_name, "
                "       EXISTS (SELECT 1 FROM uex_prices u "
                "               WHERE u.ref_uuid = i.uuid "
                "                 AND u.price_buy IS NOT NULL) en_vente "
                "FROM items i "
                "WHERE i.type = 'WeaponAttachment' AND i.subtype = ? "
                "  AND i.name IS NOT NULL "
                "  AND (i.size IS NULL OR (i.size >= ? AND i.size <= ?)) "
                "GROUP BY i.name ORDER BY en_vente DESC, i.size, i.name",
                (sous_type, port["min_size"] or 0, port["max_size"] or 99))]
        if compatibles:
            par_famille.setdefault(sous_type, []).extend(compatibles)

    familles = [{"sous_type": s, "libelle": _LIBELLE_ACCESSOIRE.get(s, s),
                 "items": v[:limit], "total": len(v)}
                for s, v in par_famille.items()]
    familles.sort(key=lambda f: -f["total"])
    return {"arme": arme, "familles": familles,
            "total": sum(f["total"] for f in familles), "resolution": res}


_LIBELLE_ACCESSOIRE = {
    "IronSight": "optiques", "Barrel": "canons", "Magazine": "chargeurs",
    "BottomAttachment": "accessoires sous canon", "Utility": "utilitaires",
    "Missile": "missiles",
}


def qui_peut_monter(con: sqlite3.Connection, query: str, *,
                    limit: int = 10) -> dict[str, Any]:
    """« Quels vaisseaux peuvent monter un Omnisky XII ? »

    L'inverse de `get_compatible_items`, qui ne savait répondre que « ce qui
    monte sur ce vaisseau ». La question dans l'autre sens n'avait aucun outil
    et tombait dans le silence.

    Le critère est le port : un emplacement qui **accepte le type** de l'objet
    et dont l'intervalle de taille le contient. On se limite aux ports
    `editable` — les autres sont soudés, et les proposer ferait espérer un
    montage impossible.

    On ne rend **pas** le nombre d'emplacements : mesuré sur le Hammerhead, le
    comptage des ports donne 36 pour 24 tourelles réelles, les variantes de
    loadout créant des doublons. Le nombre de vaisseaux, lui, est sûr.
    """
    res = resolve(con, query, entity_types=("item",))
    if res.best is None:
        raise NotFound(query, res)
    objet = _dict(_row(con, "SELECT uuid, name, type, size FROM items "
                            "WHERE uuid = ?", res.best.entity_id))
    if objet is None or not objet.get("type"):
        raise NotFound(query, res)

    taille = objet.get("size")
    # Le nom d'affichage est la clé, pas l'UUID : trois « Aegis Idris-P »
    # existent en base — livrée, édition spéciale — et les lister trois fois
    # laisse croire à un bug. Même règle que pour les titres de mission.
    vaisseaux = [dict(r) for r in con.execute(
        "SELECT s.name, MIN(s.manufacturer_name) manufacturer_name, "
        "       MAX(s.size) size FROM ships s "
        "WHERE EXISTS (SELECT 1 FROM hardpoints h WHERE h.ship_uuid = s.uuid "
        "  AND h.editable = 1 AND h.accepted_types LIKE '%' || ? || '%' "
        "  AND (? IS NULL OR (h.min_size <= ? AND h.max_size >= ?))) "
        "GROUP BY s.name ORDER BY size DESC, s.name",
        (objet["type"], taille, taille, taille))]

    return {"item": objet, "ships": vaisseaux[:limit],
            "total": len(vaisseaux), "resolution": res}


def armes_par_metrique(con: sqlite3.Connection, query: str, *,
                       metrique: str | None = None,
                       size: int | None = None,
                       limit: int = 8) -> dict[str, Any]:
    """Classe les armes par métrique dérivée, taille par taille.

    Couvre les dégâts avant panne sèche, l'autonomie de tir, le DPS soutenu,
    les tirs par capacitor et l'alpha par projectile/plomb. Préférer cet outil
    au SQL : il applique le filtre des armes réellement montables sans exiger
    que ``weapon_class`` soit renseigné.

    Une valeur qui n'existe dans aucune colonne mais se déduit de deux : le
    catalogue savait classer sur ce qu'il stocke, jamais sur ce qui s'en
    calcule. Le joueur, lui, raisonne en dégâts par chargeur et en secondes de
    tir — pas en `alpha` et `ammo_capacity`.

    **Le rechargement n'est pas dans les données.** « Le meilleur DPS par
    seconde de rechargement » reste donc sans réponse, et c'est dit plutôt que
    deviné. Ce qui existe : le capacitor, dont la recharge est publiée, d'où
    le « DPS soutenu ».
    """
    if metrique not in _METRIQUES:
        raise NotFound(query)
    libelle, unite, expression, condition, plus_grand = _METRIQUES[metrique]
    where, args, famille = _weapon_filter(query)
    if size is not None:
        where += " AND i.size = ?"
        args.append(size)

    # **Par taille, jamais globalement.** `mount_usable` ne distingue pas ce
    # qu'un joueur peut monter : le canon du porte-nefs Bengal le porte aussi.
    # Un classement global rendait « Destroyer Mass Driver Cannon, 432 millions
    # de dégâts par chargeur » — exact et absurde. C'est la règle que
    # `compare_items` applique déjà.
    lignes = [dict(r) for r in con.execute(
        f"SELECT i.name, i.size, i.manufacturer_name, "
        f"       {expression} valeur FROM items i "
        f"JOIN item_stats s ON s.item_uuid = i.uuid {MOUNTABLE} "
        f"WHERE i.name IS NOT NULL AND i.size IS NOT NULL "
        f"  AND {condition} AND {where} "
        f"GROUP BY i.name ORDER BY i.size, valeur "
        f"{'DESC' if plus_grand else 'ASC'}", args)]
    if not lignes:
        raise NotFound(query)

    par_taille: dict[int, list[dict]] = {}
    for ligne in lignes:
        par_taille.setdefault(ligne["size"], []).append(ligne)
    tailles = [{"size": taille, "best": items[0], "count": len(items),
                "suivants": items[1:3]}
               for taille, items in sorted(par_taille.items())][:limit]
    return {"metrique": metrique, "libelle": libelle, "unite": unite,
            "famille": famille, "par_taille": tailles,
            "items": [t["best"] for t in tailles]}


def objets_au_seuil(con: sqlite3.Connection, query: str, *,
                    stat: str | None = None, seuil: str | None = None,
                    valeur: float | None = None,
                    limit: int = 10) -> dict[str, Any]:
    """« Quelles armes font plus de 500 DPS ? »

    Le pendant de `vaisseaux_au_seuil` côté catalogue. Il manquait, et la
    question tombait chez `vaisseaux_au_seuil` — « dps » est aussi une
    statistique de vaisseau — qui répondait **240 vaisseaux** à une question
    sur les armes. Le garde-fou posé alors la faisait taire ; elle répond
    maintenant.

    On garde le filtre de `compare_items` : `MOUNTABLE` écarte les canons de
    capital-ship que le joueur ne montera jamais, et la famille se lit dans la
    question — « quelles armes **balistiques** font plus de 500 DPS ».
    """
    if stat not in STATS or seuil not in (">=", "<=") or valeur is None:
        raise NotFound(query)
    assert stat in STATS and seuil in (">=", "<="), (stat, seuil)
    libelle_stat, unite, _ = STATS[stat]
    where, args, famille = _weapon_filter(query)

    lignes = [dict(r) for r in con.execute(
        f"SELECT i.name, i.size, i.manufacturer_name, st.{stat} valeur "
        f"FROM items i JOIN item_stats st ON st.item_uuid = i.uuid "
        f"{MOUNTABLE} "
        f"WHERE i.name IS NOT NULL AND st.{stat} IS NOT NULL AND {where} "
        f"  AND st.{stat} {seuil} ? "
        f"GROUP BY i.name ORDER BY st.{stat} "
        f"{'DESC' if seuil == '>=' else 'ASC'}", (*args, valeur))]
    if not lignes:
        raise NotFound(query)
    return {"stat": stat, "libelle": libelle_stat, "unite": unite,
            "seuil": seuil, "valeur": valeur, "famille": famille,
            "items": lignes[:limit], "total": len(lignes)}


def combien_y_a_t_il(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Combien de vaisseaux dans le jeu ? », « combien de blueprints ? »

    Une question à laquelle on ne savait pas répondre alors que la base la
    connaît par construction. Le vocabulaire est **fermé** : on ne compte que
    ce dont on sait dire honnêtement ce qu'il recouvre.
    """
    norm = normalize(query)
    if (re.search(r"\bcontrats?\b", norm)
            and re.search(r"\bblueprints?\b", norm)):
        combien = con.execute(
            "SELECT COUNT(DISTINCT p.contract_uuid) "
            "FROM contract_reward_pools p JOIN contracts c "
            "ON c.uuid = p.contract_uuid WHERE c.not_for_release = 0 "
            "AND c.work_in_progress = 0").fetchone()[0]
        from .descriptions import _titre_utilisable
        titres = [r[0] for r in con.execute(
            "SELECT DISTINCT c.title FROM contract_reward_pools p "
            "JOIN contracts c ON c.uuid = p.contract_uuid "
            "WHERE c.not_for_release = 0 AND c.work_in_progress = 0")]
        return {
            "quoi": "contrats publiés distincts qui donnent un blueprint",
            "combien": combien,
            "missions_affichables": sum(
                _titre_utilisable(titre) for titre in titres),
        }
    for mots, (libelle, sql) in _COMPTAGES.items():
        if any(m in norm for m in mots):
            return {"quoi": libelle, "combien": con.execute(sql).fetchone()[0]}
    raise NotFound(query)


# Chaque entrée dit **ce qui est compté**, pas seulement combien : « 316
# vaisseaux » sans préciser qu'on compte les variantes prête à confusion.
_COMPTAGES: dict[tuple[str, ...], tuple[str, str]] = {
    ("vaisseau", "vaisseaux", "ship"): (
        "vaisseaux et véhicules, variantes comprises",
        "SELECT COUNT(*) FROM ships"),
    ("blueprint", "recette"): (
        "blueprints",
        "SELECT COUNT(*) FROM blueprints"),
    ("mission", "contrat"): (
        "missions distinctes, titres de gabarit fusionnés",
        "SELECT COUNT(DISTINCT title) FROM contracts WHERE title IS NOT NULL"),
    ("systeme",): (
        "systèmes contenant des lieux",
        "SELECT COUNT(*) FROM (SELECT system_name FROM starmap "
        "WHERE system_name IS NOT NULL GROUP BY 1 HAVING COUNT(*) > 1)"),
    ("lieu", "endroit", "planete", "lune"): (
        "lieux au starmap",
        "SELECT COUNT(*) FROM starmap WHERE name IS NOT NULL"),
    ("arme",): (
        "armes montables par un joueur",
        "SELECT COUNT(*) FROM items WHERE type IN ('WeaponGun', "
        "'WeaponPersonal') AND mount_usable = 1"),
    ("objet", "item"): (
        "objets au catalogue",
        "SELECT COUNT(*) FROM items"),
    ("faction", "organisation", "org"): (
        "factions et organisations",
        "SELECT COUNT(*) FROM factions"),
    ("constructeur", "fabricant"): (
        "constructeurs",
        "SELECT COUNT(*) FROM manufacturers"),
}


def que_fabrique_t_on_avec(con: sqlite3.Connection, query: str, *,
                           limit: int = 6) -> dict[str, Any]:
    """« Qu'est-ce qu'on fabrique avec du Quantanium ? »

    L'inverse de `get_blueprint`, et la même famille que « combien de
    blueprints sortent des mêmes missions que le P6-LR » : une relation qu'on
    avait dans les deux sens en base et qu'on ne savait lire que dans un.
    Jusqu'ici la question répondait le **prix d'une gourde** — `get_price`
    résolvait « quantanium » et rendait ce qu'il trouvait.

    On **groupe par type de sortie** plutôt que d'énumérer : l'Aslarite entre
    dans 856 recettes, en citer 856 est exact et inutilisable. Même leçon que
    les groupes de missions.
    """
    res = resolve(con, query, entity_types=("resource", "item", "commodity"))
    if res.best is None:
        raise NotFound(query, res)

    lignes = [dict(r) for r in con.execute(
        "SELECT b.output_name, b.output_type, i.ref_name, "
        "       i.quantity_scu, i.quantity_units "
        "FROM blueprint_ingredients i "
        "JOIN blueprint_tiers t ON t.id = i.tier_id "
        "JOIN blueprints b ON b.uuid = t.blueprint_uuid "
        "WHERE i.ref_uuid = ? OR i.ref_name = ?",
        (res.best.entity_id, res.best.name))]

    par_type: dict[str, list[str]] = {}
    for l in lignes:
        nom = l["output_name"]
        if not nom:
            continue
        # Un même blueprint compte une fois, même s'il consomme l'ingrédient
        # à plusieurs paliers.
        famille = par_type.setdefault(l["output_type"] or "autre", [])
        if nom not in famille:
            famille.append(nom)

    groupes = sorted(({"type": t, "exemples": sorted(noms)[:3], "total": len(noms)}
                      for t, noms in par_type.items()),
                     key=lambda g: -g["total"])
    return {"ingredient": res.best.name, "groupes": groupes[:limit],
            "total": sum(g["total"] for g in groupes),
            "familles": len(groupes), "resolution": res}


def constructeur_de(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Quelle est la marque du Vanguard ? »

    Posée deux fois dans le journal, deux fois sans réponse — alors que
    `manufacturers` est en base depuis le 2026-08-05, avec **116 fiches sur
    140**. Le constructeur figurait déjà au bas d'une fiche de vaisseau ; il
    n'avait pas de question à lui.

    On répond aussi pour un objet : « la marque du P8-AR » est une question
    aussi naturelle, et `items.manufacturer_name` la porte — par le **code**
    (« KSAR ») là où les vaisseaux portent le nom entier, d'où la double clé
    de `_constructeur()`.
    """
    # Fenêtre large et non pas huit candidats : « vanguard » sort **six
    # livrées** avant le moindre vaisseau, et une fenêtre courte ne laisse
    # jamais l'*Aegis Vanguard Warden* entrer en lice.
    res = resolve(con, query, entity_types=("ship", "item"), limit=30)
    if not res.best:
        raise NotFound(query, res)

    # **Un vaisseau avant sa livrée.** « Vanguard » sort six livrées à 93 —
    # ce sont des `item`, donc des peintures — devant l'*Aegis Vanguard
    # Warden* à 90. La marque est la même, mais répondre « Vanguard Fortuna
    # Livery » à « quelle est la marque du Vanguard » est absurde. C'est la
    # règle de `decrire` : l'ordre des types prime sur le score.
    candidats = sorted(
        (c for c in res.candidates if c.score >= res.best.score - 10),
        key=lambda c: (c.entity_type != "ship", -c.score, len(c.name)))

    entite = marque = None
    for cand in candidats:
        table = "ships" if cand.entity_type == "ship" else "items"
        ligne = _dict(_row(
            con, f"SELECT name, manufacturer_name FROM {table} WHERE uuid = ?",
            cand.entity_id))
        brut = (ligne or {}).get("manufacturer_name")
        # « Unknown Manufacturer » est la valeur que le jeu écrit quand il ne
        # sait pas : la rendre reviendrait à faire passer un trou pour une
        # réponse.
        if brut and brut.lower() != "unknown manufacturer":
            entite, marque = ligne, brut
            break

    if entite is None:
        return {"resolution": res, "entite": None, "marque": None}

    fiche = _dict(_row(
        con, "SELECT code, name, description FROM manufacturers "
             "WHERE name = ? COLLATE NOCASE OR code = ? COLLATE NOCASE "
             "ORDER BY LENGTH(COALESCE(description, '')) DESC LIMIT 1",
        marque, marque))
    nom = (fiche or {}).get("name") or marque
    code = (fiche or {}).get("code")
    # Le français quand il existe : les 123 fiches de constructeur sont
    # traduites par le Cirque Lisoir, et un paragraphe anglais au milieu d'une
    # réponse française est un défaut (R6).
    traduit = _dict(_row(
        con, "SELECT nom_fr, description_fr FROM traductions "
             "WHERE entity_type = 'manufacturer' AND uuid = ?", code or ""))
    description = ((traduit or {}).get("description_fr")
                   or (fiche or {}).get("description"))
    en_francais = bool((traduit or {}).get("description_fr"))

    # Ce qu'il construit d'autre : c'est la question qui suit toujours.
    autres = [r[0] for r in con.execute(
        "SELECT name FROM ships WHERE manufacturer_name = ? COLLATE NOCASE "
        "AND name <> ? ORDER BY name", (marque, entite["name"] or ""))]
    return {"resolution": res, "entite": entite, "marque": nom, "code": code,
            "description": description, "en_francais": en_francais,
            "autres": autres[:6], "total_autres": len(autres)}


def que_trouve_t_on(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Qu'est-ce qu'on vend à Lorville ? », « quels vaisseaux à New Deal ? »

    Toutes les questions **par lieu** tombaient jusqu'ici dans le vide, et pas
    dans le silence : « quels vaisseaux sont vendus à Lorville » répondait un
    T-shirt, parce qu'aucun outil ne prenait le lieu comme sujet et que
    `get_price` résolvait ce qu'il pouvait dans la phrase.

    La donnée était là : UEX ne publie pas de position, mais **le libellé de
    ses terminaux porte le lieu** — « Tammany and Sons - Metro Center -
    Lorville ». On prend le problème par ce bout plutôt que par les
    coordonnées, qui n'existent pas.

    On compte par terminal et par nature. Citer 655 articles serait exact et
    illisible ; dire « sept commerces, dont New Deal qui vend 101 vaisseaux »
    répond à la question posée.
    """
    res = resolve(con, query, entity_types=("starmap",))
    if res.best is None:
        raise NotFound(query, res)
    lieu = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?",
                      res.best.entity_id))
    if lieu is None:
        raise NotFound(query, res)

    # Le nom du lieu apparaît dans le libellé du terminal, en dernier segment
    # le plus souvent. On accepte n'importe où : « Vantage Rentals - Lorville »
    # et « Kel-To - Teasa Spaceport - Lorville » désignent le même endroit.
    #
    # **UEX ne met pas les espaces au même endroit que le starmap** : « Grim
    # HEX » y est « GrimHEX », et la comparaison littérale ne trouvait rien
    # alors que la station a quatre commerces.
    noms = [lieu["name"]]
    if " " in lieu["name"]:
        noms.append(lieu["name"].replace(" ", ""))

    # Une lune ou une planète n'a pas de terminal à son nom : ce sont ses
    # avant-postes qui en ont. Sans ses enfants, « qu'est-ce qu'il y a sur
    # Yela » répondait « aucun commerce », ce qui est faux.
    enfants = [r[0] for r in con.execute(
        "SELECT name FROM starmap WHERE parent_uuid = ? AND name IS NOT NULL",
        (lieu["uuid"],))]
    noms += [e for e in enfants if e]

    filtres = " OR ".join("terminal LIKE '%' || ? || '%'" for _ in noms)
    terminaux = [dict(r) for r in con.execute(
        "SELECT terminal, kind, COUNT(*) n, "
        "       SUM(price_buy IS NOT NULL) achetables "
        f"FROM uex_prices WHERE {filtres} "
        "GROUP BY terminal, kind ORDER BY n DESC", noms)]

    par_nature: dict[str, int] = {}
    for t in terminaux:
        par_nature[t["kind"]] = par_nature.get(t["kind"], 0) + t["n"]

    return {"lieu": lieu, "terminaux": terminaux, "par_nature": par_nature,
            "commerces": len({t["terminal"] for t in terminaux}),
            "resolution": res}


def get_item_stats(con: sqlite3.Connection, query: str,
                   *, stat: str | None = None) -> dict[str, Any]:
    """« Combien de balles a un Coda ? », « le capacitor du Panther ? »

    Le catalogue avait de quoi classer les armes entre elles mais aucun outil
    pour en décrire **une**. « Combien de balles a un Coda » n'avait donc
    aucune réponse, alors que le chiffre est en base depuis le premier jour.

    Le chargeur et le capacitor ne cohabitent jamais : mesuré sur la base, les
    61 canons balistiques de vaisseau ont une capacité de munitions et **aucun**
    capacitor, les 88 armes laser ont les deux. Répondre « pas de capacitor »
    sur une arme balistique n'est pas une lacune, c'est la réponse.
    """
    res = resolve(con, query, entity_types=("item",))
    if res.best is None:
        raise NotFound(query, res)
    ligne = _row(
        con,
        "SELECT i.*, s.* FROM items i LEFT JOIN item_stats s ON s.item_uuid = i.uuid "
        "WHERE i.uuid = ?",
        res.best.entity_id,
    )
    if ligne is None:
        raise NotFound(query, res)
    item = dict(ligne)

    # Les objets citent leur constructeur par son **code** — la fiche du Coda
    # s'affichait « Coda Pistol — KSAR ». Les vaisseaux, eux, portent le nom
    # complet. Une seule table répond aux deux.
    item["manufacturer_name"] = _constructeur(con, item.get("manufacturer_name"))

    # Autonomie : deux nombres de la base, une division. La cadence est en
    # coups par minute, la capacité en coups — le reste se déduit sans rien
    # inventer. Sur les armes à charge (`AmmoPerShot` > 1), un tir consomme
    # plusieurs unités et l'autonomie tombe d'autant.
    autonomie = None
    if item.get("ammo_capacity") and item.get("rounds_per_minute"):
        par_tir = item.get("ammo_per_shot") or 1
        tirs = item["ammo_capacity"] / par_tir
        autonomie = round(tirs / (item["rounds_per_minute"] / 60.0), 1)

    # Le chargeur d'origine est un objet à part entière, avec son propre nom et
    # sa capacité : « Coda Pistol Magazine (6 cap) ». Les variantes se
    # reconnaissent au même préfixe de classe.
    chargeurs = [dict(r) for r in con.execute(
        "SELECT i.uuid, i.name, s.ammo_capacity FROM items i "
        "JOIN item_stats s ON s.item_uuid = i.uuid "
        "WHERE i.subtype = 'Magazine' AND s.ammo_capacity IS NOT NULL "
        "  AND i.name LIKE ? ORDER BY s.ammo_capacity",
        (f"{(item.get('name') or '').split(' (')[0]}%",),
    )] if item.get("type") == "WeaponPersonal" else []

    # La remarque du journal « combien de points d'énergie ? » : la fiche
    # d'un composant du réseau porte ses pips, ses paliers et ses
    # signatures — sprint 20, sur la table du sprint 19.
    reseau = _row(con,
                  "SELECT pips_conso, std_conso, pips_generes, ressource, "
                  "       generation_std, min_fraction, mult_low, mult_med, "
                  "       em, ir FROM item_reseau "
                  "WHERE item_uuid = ? AND etat = 'Online'", item["uuid"])

    if stat and item.get(stat) is None:
        stat = None
    return {
        "item": item,
        "reseau": dict(reseau) if reseau else None,
        "resolution": res,
        "stat": stat,
        "voisines": [v for v in VOISINES.get(stat or "", ()) if item.get(v)],
        "autonomie_secondes": autonomie,
        "chargeurs": chargeurs,
    }


# ------------------------------------------------ catalogues par famille

# La classe d'une arme personnelle vit dans ses **tags** — le jeu écrit
# « volt_sniper_energy_01 », « gmni_shotgun_ballistic_01 » — et le nom la
# confirme. Ordre : du plus spécifique au plus générique, « sniper » avant
# « rifle » sans quoi tout sniper serait un fusil.
_CLASSES_D_ARME: tuple[tuple[str, str], ...] = (
    ("sniper", "sniper"), ("shotgun", "fusil à pompe"),
    ("smg", "mitraillette"), ("lmg", "mitrailleuse"),
    ("gatling", "gatling"), ("launcher", "lance-roquettes"),
    ("railgun", "railgun"), ("grenade", "grenade"),
    ("pistol", "arme de poing"), ("rifle", "fusil"),
)


def _classe_d_arme(tags: str | None, nom: str | None) -> str:
    bas = f"{tags or ''} {nom or ''}".lower()
    for cle, libelle in _CLASSES_D_ARME:
        if cle in bas:
            return libelle
    return "autre"


# Les familles qu'un joueur nomme, avec leur filtre et leur mode de
# regroupement. Le vocabulaire est fermé : ce qui n'y est pas ne se catalogue
# pas — mieux vaut un refus qu'une liste au hasard.
_FAMILLES_D_OBJETS: tuple[tuple[str, str, str, str], ...] = (
    ("armes FPS", r"\barmes? (?:fps|personnelles?|d infanterie)\b",
     "type = 'WeaponPersonal'", "classe"),
    ("armes de vaisseau", r"\b(?:armes?|canons?) de vaisseaux?\b",
     "type = 'WeaponGun'", "taille"),
    ("boucliers", r"\bboucliers\b", "type = 'Shield'", "taille"),
    ("refroidisseurs", r"\brefroidisseurs\b", "type = 'Cooler'", "taille"),
    ("générateurs", r"\bgenerateurs\b|\bcentrales? electriques?\b",
     "type = 'PowerPlant'", "taille"),
    ("radars", r"\bradars\b", "type = 'Radar'", "taille"),
    ("moteurs quantiques", r"\bmoteurs? quantiques?\b",
     "type = 'QuantumDrive'", "taille"),
    ("armes de minage", r"\b(?:armes?|lasers?) de minage\b",
     "type = 'WeaponMining'", "taille"),
    ("casques", r"\bcasques\b", "type = 'Char_Armor_Helmet'", "classe_armure"),
    ("armures", r"\barmures\b", "type LIKE 'Char_Armor_%'", "classe_armure"),
)

_CLASSE_ARMURE_FR = {"Light": "légère", "Medium": "moyenne", "Heavy": "lourde",
                     "UNDEFINED": "autre"}


def detect_famille_objets(question: str) -> tuple[str, str, str] | None:
    """La famille d'objets nommée — (libellé, clause SQL, mode de groupe)."""
    norm = normalize(question or "")
    for libelle, motif, clause, mode in _FAMILLES_D_OBJETS:
        if re.search(motif, norm):
            return libelle, clause, mode
    return None


def _nom_de_base(nom: str) -> str:
    """« Atzkav "Venom" Sniper Rifle » → « Atzkav Sniper Rifle ».

    Les coloris s'écrivent entre guillemets dans le nom : les garder ferait
    un catalogue de 427 « armes » dont les deux tiers sont des peintures.
    """
    return re.sub(r'\s*"[^"]*"\s*', " ", nom).strip()


def _groupe_de(ligne: dict[str, Any], mode: str) -> str:
    if mode == "classe":
        return _classe_d_arme(ligne.get("tags"), ligne.get("name"))
    if mode == "classe_armure":
        return _CLASSE_ARMURE_FR.get(ligne.get("subtype") or "",
                                     (ligne.get("subtype") or "autre").lower())
    taille = ligne.get("size")
    return f"taille {taille}" if taille is not None else "sans taille"


def catalogue_objets(con: sqlite3.Connection, query: str, *,
                     famille: str | None = None, clause: str | None = None,
                     mode: str = "taille",
                     taille: int | None = None) -> dict[str, Any]:
    """« C'est quoi les boucliers ? » — le catalogue d'une famille, groupé.

    Remarque de l'utilisateur : une liste simple par type avec la marque,
    puis « je demande des détails sur un nom et on repart sur la boucle
    habituelle ». Les coloris se replient sur leur base — un catalogue de
    peintures n'aide personne — et le compte le dit.
    """
    if not famille or not clause:
        raise NotFound(query)
    # « Les boucliers de taille 2 » : la taille filtre — grille, ligne 1,
    # une contrainte ne se perd jamais en silence.
    filtre_taille = " AND size = ?" if taille is not None else ""
    lignes = [dict(r) for r in con.execute(
        f"SELECT name, subtype, size, manufacturer_name, tags FROM items "
        f"WHERE {clause} AND name IS NOT NULL "
        f"AND name NOT LIKE 'PLACEHOLDER%' AND name NOT LIKE '[PH]%'"
        f"{filtre_taille} ORDER BY name",
        ([taille] if taille is not None else []))]
    if not lignes:
        raise NotFound(f"aucun objet dans la famille {famille}")

    groupes: dict[str, dict[str, dict[str, Any]]] = {}
    total_avec_coloris = len(lignes)
    for ligne in lignes:
        base = _nom_de_base(ligne["name"])
        groupe = groupes.setdefault(_groupe_de(ligne, mode), {})
        # Le premier nom d'une base gagne ; les coloris se comptent.
        entree = groupe.setdefault(base, {"nom": base, "coloris": 0,
                                          "marque": ligne.get("manufacturer_name")})
        if ligne["name"] != base:
            entree["coloris"] += 1
    return {"famille": famille + (f" de taille {taille}"
                                  if taille is not None else ""),
            # La clause repart dans la donnée : la suite « liste entière »
            # reconstruit l'appel depuis elle, filtres conservés.
            "clause": clause, "taille": taille,
            "mode": mode, "groupes": groupes,
            "total": sum(len(g) for g in groupes.values()),
            "total_avec_coloris": total_avec_coloris,
            "resolution": None}
