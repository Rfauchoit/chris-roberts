"""La flotte — fiches, comparaisons, seuils, métiers et emports de vaisseau.

Découpé de `queries.py` le 2026-08-07, mécaniquement — même règle que les
missions et le commerce : l'ordre du fichier d'origine, la façade en
ré-export, les imports différés à travers les familles.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .hardpoint_categories import WEAPON_CATEGORIES
from .normalize import normalize
from .resolver import resolve
from .stats import (
    COMPONENT_AXES,
    COMPONENT_MOINS_EST_MIEUX,
    SHIP_STATS,
    VOISINES,
    detect_component,
    detect_component_stat,
    detect_ship_stat,
)


def get_ship_hardpoints(con: sqlite3.Connection, query: str,
                        *, weapons_only: bool = True) -> dict[str, Any]:
    """« Quels sont les points d'emport d'armes sur un Gladius ? »

    Les ports vides comptent : un emplacement d'arme libre est une réponse, pas
    une absence de réponse.
    """
    res = resolve(con, query, entity_types=("ship",))
    if not res.best:
        raise NotFound(query, res)

    ship = _dict(_row(con, "SELECT * FROM ships WHERE uuid = ?", res.best.entity_id))
    if ship is None:
        raise NotFound(query, res)

    if weapons_only:
        placeholders = ",".join("?" * len(WEAPON_CATEGORIES))
        rows = con.execute(
            f"SELECT * FROM hardpoints WHERE ship_uuid = ? "
            f"AND category IN ({placeholders}) ORDER BY depth, port_id",
            (ship["uuid"], *WEAPON_CATEGORIES),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM hardpoints WHERE ship_uuid = ? ORDER BY depth, port_id",
            (ship["uuid"],),
        ).fetchall()

    by_id = {r["port_id"]: dict(r) for r in rows}
    for node in by_id.values():
        node["children"] = []
    roots = []
    for node in by_id.values():
        parent = by_id.get(node["parent_port_id"])
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)

    def descendants(node: dict[str, Any]) -> list[dict[str, Any]]:
        trouves = []
        for enfant in node["children"]:
            trouves.append(enfant)
            trouves.extend(descendants(enfant))
        return trouves

    # Le type de tourelle vient des listes dédiées de la source, jamais d'un
    # nom contenant « turret ». Son sous-arbre se reconstruit sur **tous** les
    # hardpoints : filtrer d'abord les catégories offensives casserait le lien
    # de la PDC du MDC, imbriquée sous un port arrière non offensif.
    lignes_completes = con.execute(
        "SELECT * FROM hardpoints WHERE ship_uuid = ? ORDER BY depth, port_id",
        (ship["uuid"],)).fetchall()
    tous_par_id = {r["port_id"]: dict(r) for r in lignes_completes}
    for node in tous_par_id.values():
        node["children"] = []
    for node in tous_par_id.values():
        parent = tous_par_id.get(node["parent_port_id"])
        if parent is not None:
            parent["children"].append(node)

    tourelles = []
    for node in tous_par_id.values():
        if not node.get("turret_kind"):
            continue
        dessous = descendants(node)
        affuts = [n for n in dessous if n["category"] == "weapon_mount"]
        armes = [n for n in dessous
                 if n["category"] == "weapon" and n["installed_name"]]
        # Les PDC portent souvent leur arme directement, sans nœud de gimbal
        # intermédiaire : ce port d'arme est alors l'affût publié.
        if not affuts:
            affuts = [n for n in dessous if n["category"] == "weapon"]
        tourelles.append({
            "port_id": node["port_id"],
            "kind": node["turret_kind"],
            "type": node["turret_type"],
            "size": node["max_size"],
            "affuts": affuts,
            "armes": armes,
        })

    # Les supports du pilote sont les racines non qualifiées comme tourelles.
    # Une tourelle imbriquée dont le parent non offensif a été filtré devient
    # elle aussi racine ici, d'où le test explicite sur `turret_kind`.
    pilot_mounts = [m for m in roots
                    if m["category"] == "weapon_mount"
                    and not m.get("turret_kind")]

    return {
        "ship": ship,
        "resolution": res,
        "mounts": roots,
        "pilot_mounts": pilot_mounts,
        "tourelles": tourelles,
        "totals": {
            cat: sum(1 for r in rows if r["category"] == cat)
            for cat in sorted({r["category"] for r in rows})
        },
    }


def vaisseau_pour_budget(con: sqlite3.Connection, query: str, *,
                         budget: float | None = None,
                         carriere: str | None = None,
                         limit: int = 6) -> dict[str, Any]:
    """« C'est quoi le meilleur vaisseau de combat pour 17M de crédits ? »

    Remarque du journal : « tu dois cibler un vaisseau proche de 17M de la
    catégorie combat, pas me dire lequel a la meilleure vitesse ». La question
    partait chez `compare_ships`, qui classait sur une statistique — juste, et
    sans rapport avec un budget.

    On rend **ce qu'on peut s'offrir**, du plus cher au moins cher : à budget
    donné, le meilleur vaisseau est le plus gros qui rentre dedans. Et on cite
    le premier au-dessus du budget, parce que « il t'en manque 800 000 » est
    une information que le joueur veut avoir.

    Le prix vient d'UEX — 141 vaisseaux cotés sur 316 — donc de relevés de
    joueurs, et la réponse le dit.
    """
    if budget is None:
        raise NotFound(query)

    clauses = ["u.kind = 'vehicle'", "u.price_buy IS NOT NULL"]
    args: list[Any] = []
    if carriere:
        clauses.append("s.career = ?")
        args.append(carriere)
    filtre = " AND ".join(clauses)

    lignes = [dict(r) for r in con.execute(
        f"SELECT s.name, s.career, s.role, s.manufacturer_name, "
        f"       MIN(u.price_buy) prix FROM uex_prices u "
        f"JOIN ships s ON s.uuid = u.ref_uuid WHERE {filtre} "
        f"GROUP BY s.name ORDER BY prix DESC", args)]

    dans_le_budget = [l for l in lignes if l["prix"] <= budget]
    au_dessus = [l for l in lignes if l["prix"] > budget]
    return {"budget": budget, "carriere": carriere,
            "ships": dans_le_budget[:limit],
            "total": len(dans_le_budget),
            # Le premier hors budget, s'il n'est pas très loin : « il te manque
            # 800 000 aUEC » oriente une décision.
            "juste_au_dessus": au_dessus[-1] if au_dessus else None,
            "cotes": len(lignes)}


def vaisseaux_multi_criteres(con: sqlite3.Connection, query: str, *,
                             criteres: list[tuple[str, str, float]] | None = None,
                             budget: tuple[str, float] | None = None,
                             carriere: str | None = None,
                             limit: int = 8) -> dict[str, Any]:
    """« Quel vaisseau de combat avec plus de 100 SCU et moins de 2 millions ? »

    Chaque contrainte savait déjà se traiter seule — `vaisseaux_au_seuil` pour
    un seuil, `vaisseau_pour_budget` pour un prix, `detect_carriere` pour une
    catégorie — et aucune ne savait se combiner. La question à plusieurs
    variables recevait donc une réponse juste sur une variable, ce qui est la
    pire des formes : elle a l'air de répondre.

    **Quand rien ne passe, on dit ce qui bloque.** Une liste vide est une
    réponse inutilisable ; le joueur veut savoir laquelle de ses exigences
    coûte les autres. On relâche donc chaque critère à tour de rôle et on rend
    celui dont l'abandon débloque le plus — c'est la seule mesure qui réponde
    à « et si j'assouplissais ? ».
    """
    criteres = list(criteres or [])
    if not criteres and budget is None:
        raise NotFound(query)
    for stat, sens, _ in criteres:
        if stat not in SHIP_STATS or sens not in (">=", "<="):
            raise NotFound(query)
        assert stat in SHIP_STATS and sens in (">=", "<="), (stat, sens)

    def lignes_pour(retenus: list[tuple[str, str, float]],
                    avec_budget: bool) -> list[dict[str, Any]]:
        clauses = ["s.name IS NOT NULL", "s.is_spaceship = 1"]
        args: list[Any] = []
        for stat, sens, valeur in retenus:
            # **`valeur is None` est un critère qualitatif** — « rapide »,
            # « avec du fret » : la statistique doit exister et être non
            # nulle, et le tri fait le reste. Inventer un seuil chiffré pour
            # « rapide » serait le §7 ; exiger la présence ne l'est pas.
            if valeur is None:
                clauses.append(f"s.{stat} IS NOT NULL AND s.{stat} > 0")
            else:
                clauses.append(f"s.{stat} IS NOT NULL AND s.{stat} {sens} ?")
                args.append(valeur)
        if carriere:
            clauses.append("s.career = ?")
            args.append(carriere)

        # Le prix ne vit pas dans `ships` : il faut passer par UEX, et tous les
        # vaisseaux n'y sont pas cotés. Sans budget demandé, on **n'impose pas**
        # la jointure — sinon la moitié du catalogue disparaîtrait sans raison.
        if avec_budget and budget is not None:
            sens_prix, montant = budget
            clauses.append(
                "EXISTS (SELECT 1 FROM uex_prices u WHERE u.ref_uuid = s.uuid "
                f"AND u.price_buy > 0 AND u.price_buy {sens_prix} ?)")
            args.append(montant)

        tri = (f"s.{retenus[0][0]} {'DESC' if retenus[0][1] == '>=' else 'ASC'}"
               if retenus else "s.name")
        return [dict(r) for r in con.execute(
            f"SELECT s.name, s.career, s.role, s.manufacturer_name, "
            f"       s.cargo_scu, s.crew, s.max_speed, s.shield_hp, s.mass, "
            f"       (SELECT MIN(u.price_buy) FROM uex_prices u "
            f"        WHERE u.ref_uuid = s.uuid AND u.price_buy > 0) prix "
            f"FROM ships s WHERE {' AND '.join(clauses)} "
            f"GROUP BY s.name ORDER BY {tri}", args)]

    trouves = lignes_pour(criteres, avec_budget=True)

    # Ce qui bloque : on relâche une contrainte à la fois. La plus coûteuse est
    # celle dont l'abandon rend le plus de vaisseaux.
    blocage = None
    if not trouves:
        essais: list[tuple[str, int]] = []
        for rang, (stat, _, _) in enumerate(criteres):
            reste = criteres[:rang] + criteres[rang + 1:]
            if reste or budget is not None:
                essais.append((stat, len(lignes_pour(reste, avec_budget=True))))
        if budget is not None and criteres:
            essais.append(("budget", len(lignes_pour(criteres, avec_budget=False))))
        essais = [(quoi, n) for quoi, n in essais if n > 0]
        if essais:
            blocage = max(essais, key=lambda e: e[1])

    return {
        "criteres": [{"stat": s, "libelle": SHIP_STATS[s][0],
                      "unite": SHIP_STATS[s][1], "sens": sens, "valeur": v}
                     for s, sens, v in criteres],
        "budget": budget,
        "carriere": carriere,
        "ships": trouves[:limit],
        "total": len(trouves),
        # (critère abandonné, nombre de vaisseaux que ça débloque)
        "blocage": blocage,
    }


def vaisseaux_au_seuil(con: sqlite3.Connection, query: str, *,
                       stat: str | None = None, seuil: str | None = None,
                       valeur: float | None = None,
                       limit: int = 10) -> dict[str, Any]:
    """« Quels vaisseaux ont plus de 100 SCU ? »

    Le nombre d'une question à seuil n'est pas un nom, et le prendre pour tel
    donnait des réponses absurdes : « plus de 100 SCU » répondait l'*Origin
    100i*, qui en a 2. Le routeur écarte désormais les grammes purement
    numériques ; encore fallait-il que quelqu'un lise le seuil.
    """
    if stat not in SHIP_STATS or seuil not in (">=", "<=") or valeur is None:
        raise NotFound(query)
    assert stat in SHIP_STATS and seuil in (">=", "<="), (stat, seuil)
    libelle, unite, _ = SHIP_STATS[stat]
    lignes = [dict(r) for r in con.execute(
        f"SELECT name, manufacturer_name, {stat} valeur FROM ships "
        f"WHERE {stat} IS NOT NULL AND {stat} {seuil} ? "
        f"ORDER BY {stat} {'DESC' if seuil == '>=' else 'ASC'}", (valeur,))]
    if not lignes:
        raise NotFound(query)
    return {"stat": stat, "libelle": libelle, "unite": unite, "seuil": seuil,
            "valeur": valeur, "ships": lignes[:limit], "total": len(lignes)}


def vaisseaux_sans_composant(con: sqlite3.Connection, query: str, *,
                             type_item: str | None = None,
                             limit: int = 10) -> dict[str, Any]:
    """« Quels vaisseaux n'ont pas de jump drive ? »

    Un filtre par **absence**, que rien ne savait faire : tous les outils
    répondaient « ce qui a », jamais « ce qui n'a pas ». C'est pourtant la
    question qui décide avant un voyage — 65 vaisseaux sur 316 n'ont pas de
    jump drive et ne quitteront jamais leur système.

    On compte sur l'**équipement d'origine**, pas sur la compatibilité : un
    vaisseau qui pourrait en recevoir un mais n'en a pas est bien un vaisseau
    sans jump drive.
    """
    if not type_item:
        raise NotFound(query)
    lignes = [dict(r) for r in con.execute(
        "SELECT s.name, s.manufacturer_name, s.size FROM ships s "
        "WHERE NOT EXISTS (SELECT 1 FROM hardpoints h "
        "  JOIN items i ON i.uuid = h.installed_uuid "
        "  WHERE h.ship_uuid = s.uuid AND i.type = ?) "
        "GROUP BY s.name ORDER BY s.size DESC, s.name", (type_item,))]
    total_vaisseaux = con.execute(
        "SELECT COUNT(DISTINCT name) FROM ships").fetchone()[0]
    return {"type_item": type_item, "ships": lignes[:limit],
            "total": len(lignes), "sur": total_vaisseaux}


def combien_dans_la_soute(con: sqlite3.Connection, query: str, *,
                          to: str | None = None) -> dict[str, Any]:
    """« Combien de Coda je peux mettre dans un Cutlass Black ? »

    Une division, mais entre deux chiffres qui n'étaient pas dans la même
    table et dont l'un n'était pas ingéré : le volume d'un objet
    (`InventoryOccupancy`, sur les 10 804) et la soute d'un vaisseau
    (`ships.cargo_scu`, sur 149 sur 316).

    **Les deux entités sont de nature différente**, donc elles se volent
    mutuellement dans la phrase — le piège déjà rencontré sur « le point de
    vente le plus proche de microTech pour un P4-AR ». C'est le routeur qui
    coupe et nous passe les deux termes séparément.

    On ne prétend pas au réalisme du jeu : une soute se remplit de caisses, pas
    de pistolets en vrac, et le jeu n'expose aucune règle d'empilement. Le
    chiffre rendu est donc un **volume divisé par un volume**, et le rendu le
    dit.
    """
    objet = resolve(con, query, entity_types=("item",))
    if objet.best is None:
        raise NotFound(query, objet)
    item = _dict(_row(con, "SELECT uuid, name, volume_uscu FROM items WHERE uuid = ?",
                      objet.best.entity_id))
    if item is None:
        raise NotFound(query, objet)

    vaisseau = resolve(con, to, entity_types=("ship",)) if to else None
    navire = None
    if vaisseau is not None and vaisseau.best is not None:
        navire = _dict(_row(con, "SELECT uuid, name, cargo_scu FROM ships "
                                 "WHERE uuid = ?", vaisseau.best.entity_id))

    # Un volume nul n'est pas une place infinie : c'est une donnée absente, ou
    # un objet que le jeu ne range pas en inventaire. Diviser par zéro n'aurait
    # pas seulement levé, il aurait menti.
    volume = item.get("volume_uscu") or 0
    soute = (navire or {}).get("cargo_scu") or 0
    tient = None
    if volume > 0 and soute > 0:
        tient = int(soute * 1_000_000 // volume)

    return {"item": item, "ship": navire, "tient": tient,
            "resolution": objet, "resolution_vaisseau": vaisseau}


def get_ship_stats(con: sqlite3.Connection, query: str,
                   *, stat: str | None = None) -> dict[str, Any]:
    """« Combien de SCU dans un Freelancer ? », « la vitesse du Gladius ? »

    Toutes ces valeurs viennent des fichiers du jeu, contrairement aux prix.
    Le taux de remplissage varie — 149 vaisseaux sur 316 déclarent une capacité
    de fret, ce qui est normal : un chasseur n'a pas de soute. Un champ vide
    n'est donc pas une lacune d'extraction, et la réponse doit le dire plutôt
    que d'annoncer zéro.

    `stat` restreint la réponse à ce qui a été demandé. « Combien de SCU a un
    Avenger Titan » recevait la fiche entière — huit chiffres dont un seul
    répondait. Retour du journal, et il revient six fois.
    """
    res = resolve(con, query, entity_types=("ship",))
    if res.best is None:
        raise NotFound(query, res)
    ship = _dict(_row(con, "SELECT * FROM ships WHERE uuid = ?", res.best.entity_id))
    if ship is None:
        raise NotFound(query, res)
    # Une statistique demandée mais vide sur ce vaisseau n'est pas une réponse :
    # on retombe sur la fiche, qui dira ce qui existe.
    if stat and not ship.get(stat):
        stat = None
    # Les grilles de soute accompagnent toute question de fret : la capacité
    # ne dit pas ce qui rentre, la taille de caisse acceptée oui.
    grilles = []
    if stat in (None, "cargo_scu") and ship.get("cargo_scu"):
        try:
            grilles = [dict(r) for r in con.execute(
                "SELECT scu, max_x, max_y, max_z, ouverte, externe "
                "FROM cargo_grids WHERE ship_uuid = ? ORDER BY scu DESC",
                (ship["uuid"],))]
        except sqlite3.OperationalError:
            grilles = []          # base d'avant l'ingestion des grilles
    return {"ship": ship, "resolution": res, "stat": stat, "grilles": grilles,
            "voisines": [v for v in VOISINES.get(stat or "", ()) if ship.get(v)]}


def compare_ships(con: sqlite3.Connection, query: str, *,
                  stat: str | None = None, limit: int = 5) -> dict[str, Any]:
    """« Le vaisseau le plus rapide », « compare le Cutlass et le Freelancer ».

    Deux formes dans une seule fonction, parce que c'est la même question posée
    autrement. Si la phrase nomme au moins deux vaisseaux, on compare ceux-là ;
    sinon on classe le catalogue.
    """
    stat = stat if stat in SHIP_STATS else detect_ship_stat(query)
    assert stat in SHIP_STATS, stat
    libelle, unite, plus_grand = SHIP_STATS[stat]

    nommes = _ships_nommes(con, query)
    ordre = "DESC" if plus_grand else "ASC"

    if len(nommes) >= 2:
        marques = ",".join("?" * len(nommes))
        lignes = [dict(r) for r in con.execute(
            f"SELECT * FROM ships WHERE uuid IN ({marques}) "
            f"AND {stat} IS NOT NULL AND {stat} > 0 ORDER BY {stat} {ordre}",
            nommes,
        )]
        cadre = "duel"
    else:
        lignes = [dict(r) for r in con.execute(
            f"SELECT * FROM ships WHERE {stat} IS NOT NULL AND {stat} > 0 "
            f"AND is_spaceship = 1 ORDER BY {stat} {ordre} LIMIT ?",
            (limit,),
        )]
        cadre = "classement"

    # Dédoublonnage par **nom affiché**, pas par UUID : plusieurs variantes
    # portent le même nom dans les données du jeu, et la comparaison annonçait
    # « Cutlass Black passe devant Cutlass Black ». Les lignes sont déjà
    # triées, donc la première gardée est la meilleure.
    vus: set[str] = set()
    lignes = [l for l in lignes
              if not (l["name"] in vus or vus.add(l["name"]))]

    if not lignes:
        raise NotFound(query)
    return {"ships": lignes, "stat": stat, "stat_label": libelle,
            "stat_unit": unite, "higher_is_better": plus_grand,
            "mode": cadre, "resolution": None}


def _ships_nommes(con: sqlite3.Connection, question: str) -> list[str]:
    """UUID des vaisseaux nommés dans la phrase, sans doublon et dans l'ordre.

    Sert au cas « compare A et B ». On repasse par le résolveur plutôt que par
    une liste de noms : « compare le cutty et le freelanceur » doit marcher.
    """
    from .router.deterministic import _ngrams

    vus: list[str] = []
    for gram in _ngrams(question):
        res = resolve(con, gram, entity_types=("ship",), limit=1)
        if res.best and res.best.score >= 85.0 and res.best.entity_id not in vus:
            vus.append(res.best.entity_id)
    return vus


def get_ship_components(con: sqlite3.Connection, query: str, *,
                        category: str | None = None,
                        stat: str | None = None,
                        limit: int = 20) -> dict[str, Any]:
    """« Quel bouclier je peux mettre sur un Cutlass ? »

    Deux limites à connaître, et elles sont dans les données amont, pas dans
    l'extraction.

    **La couverture d'`item_stats` est inégale, et le commentaire d'origine a
    vieilli.** Il affirmait qu'elle « ne porte que des statistiques d'armes »
    et qu'on ne pouvait donc pas classer les boucliers : c'est faux depuis
    l'ingestion de `shield_health`. Mesuré le 2026-08-06 — 73 boucliers sur 73
    et 63 moteurs quantiques sur 63 ont leurs statistiques, mais **0
    refroidisseur sur 81 et 0 générateur sur 88**.

    L'asymétrie est invisible pour le joueur : « quel est le meilleur bouclier
    taille 2 » se classe, « quel est le meilleur refroidisseur taille 2 » ne
    peut que lister ce qui rentre. Combler le trou est du même motif que
    `stdItem.Shield`, déjà fait une fois.

    Et `flight_ready` vaut 0 pour tous les boucliers, moteurs quantiques et
    refroidisseurs : le drapeau vient du champ `tags`, que ces types ne
    portent pas. Filtrer dessus, comme on le fait pour les armes, les ferait
    tous disparaître. On s'appuie sur `classification` à la place.
    """
    cible = detect_component(category or query)
    if cible is None:
        raise NotFound(query)
    port_cat, item_type, libelle = cible

    res = resolve(con, query, entity_types=("ship",))
    if res.best is None:
        raise NotFound(query, res)
    ship = _dict(_row(con, "SELECT * FROM ships WHERE uuid = ?", res.best.entity_id))
    if ship is None:
        raise NotFound(query, res)

    tailles = sorted({
        r["max_size"] for r in con.execute(
            "SELECT DISTINCT max_size FROM hardpoints "
            "WHERE ship_uuid = ? AND category = ? AND max_size IS NOT NULL",
            (ship["uuid"], port_cat),
        ) if r["max_size"] is not None
    })
    if not tailles:
        return {"ship": ship, "resolution": res, "label": libelle,
                "sizes": [], "items": []}

    # « Quel bouclier avec la meilleure recharge » ne demande pas le même
    # classement que « quel bouclier ». Sans stat demandée, on **liste** ce qui
    # rentre : la question était « lesquels puis-je monter », pas « lequel est
    # le meilleur ».
    # **L'axe peut arriver en argument, pas seulement dans le texte.** La
    # question des axes (« le meilleur dépend de ce que tu cherches ») fait
    # de chaque axe une proposition ; la reprise « encaisser » rejoue l'outil
    # avec `stat="shield_health"` — sans repasser par la détection, qui n'a
    # qu'un mot à se mettre sous la dent.
    if stat:
        axe = next((a for a in COMPONENT_AXES if a[0] == stat), None)
        demandee = (f"s.{axe[0]}", axe[3]) if axe else None
    else:
        demandee = detect_component_stat(category or query)
    tri = demandee
    colonne = tri[0] if tri else None

    marques = ",".join("?" * len(tailles))
    lignes = [dict(r) for r in con.execute(
        f"SELECT i.uuid, i.name, i.size, i.grade, i.grade_lettre, "
        f"i.item_class, i.manufacturer_name, "
        f"       s.shield_health, s.shield_regen, s.shield_downed, "
        f"       s.qt_drive_speed, s.qt_jump_range, s.cooling_rate, "
        f"       s.power_rate, s.signature_em, s.signature_ir "
        f"FROM items i LEFT JOIN item_stats s ON s.item_uuid = i.uuid "
        f"WHERE i.type = ? AND i.size IN ({marques}) "
        f"AND i.name IS NOT NULL AND i.name NOT LIKE '%test%' "
        # Les valeurs nulles en dernier : `ORDER BY col DESC IS NULL` est une
        # erreur de syntaxe, il faut deux termes distincts.
        #
        # **Le sens du tri dépend de la statistique.** Une signature basse rend
        # discret : la classer par ordre décroissant mettrait en tête le
        # composant le plus visible, soit l'inverse de la question.
        + (f"ORDER BY {colonne} IS NULL, {colonne} "
           f"{'ASC' if colonne in COMPONENT_MOINS_EST_MIEUX else 'DESC'}, "
           f"i.name LIMIT ?"
           if colonne else "ORDER BY i.size DESC, i.grade, i.name LIMIT ?"),
        (item_type, *tailles, limit),
    )]

    # Le catalogue amont contient plusieurs entrées de même nom pour un même
    # composant — des déclinaisons internes que rien ne distingue à
    # l'affichage. « Holdstrong à 1 056 000, Holdstrong à 1 056 000 » n'est pas
    # une liste de deux boucliers, c'est un doublon.
    items, vus = [], set()
    for ligne in lignes:
        cle = (ligne["name"], ligne.get("size"))
        if cle in vus:
            continue
        vus.add(cle)
        items.append(ligne)

    # **« Le meilleur » sans axe nommé n'est pas une question à trancher.**
    # « C'est quoi le meilleur bouclier pour un Wolf » ne dit pas meilleur
    # en quoi — et les axes s'opposent : mesuré sur les boucliers taille 2,
    # le militaire achète 560 PV de plus contre 300 points de signature EM.
    # Le rendu demande donc lequel, au lieu de choisir à la place du joueur.
    superlatif = re.search(r"\b(?:meilleur\w*|le plus|top|optimal\w*)\b",
                           normalize(f"{query} {category or ''}"))
    return {"ship": ship, "resolution": res, "label": libelle,
            "sizes": tailles, "items": items,
            "stat": tri[0].split(".")[1] if tri else None,
            "stat_label": tri[1] if tri else None,
            "meilleur_sans_critere": bool(superlatif) and demandee is None,
            "classement_demande": demandee is not None}


def comparer_loadouts(con: sqlite3.Connection, query: str, *,
                      limit: int = 4) -> dict[str, Any]:
    """« Compare l'armement d'un Gladius et d'un Arrow. »

    `compare_ships` compare une **statistique** — la vitesse, le fret, le DPS
    pilote. Il répond donc « le Gladius fait 1 132 de DPS » sans dire avec
    quoi, alors que c'est l'équipement d'origine qui explique le chiffre et
    que c'est lui qu'un joueur va changer.

    Mesuré : **295 vaisseaux sur 316** portent un armement monté, soit 3 627
    lignes. La donnée était là depuis la première ingestion et aucun outil ne
    la croisait avec les défenses.

    **Les tourelles ne se confondent pas avec les canons fixes.** La colonne
    `category` sépare `weapon` (l'arme elle-même) de `weapon_mount` (le
    support orientable qui la porte) : les additionner compterait chaque arme
    deux fois sur les vaisseaux à gimbal, dont le Gladius.
    """
    nommes = _ships_nommes(con, query)
    if len(nommes) < 2:
        raise NotFound(query)

    lignes_ships = [s for s in (
        _dict(_row(con, "SELECT * FROM ships WHERE uuid = ?", uuid))
        for uuid in nommes) if s is not None]

    # **Une variante n'est pas un second vaisseau.** « Compare un Gladius et un
    # Arrow » ramenait quatre entrées — le *Gladius Pirate* et le *Gladius
    # Dunlevy* résolvent sur le même mot — et la comparaison annonçait alors
    # « à égalité » entre trois exemplaires du même appareil. On écarte donc
    # tout vaisseau dont le nom **prolonge** celui d'un autre de la liste :
    # c'est une déclinaison du modèle que le joueur a nommé, pas un rival.
    # `compare_ships` a le même problème et le règle sur le nom affiché ; ici
    # les noms diffèrent réellement, seul le préfixe les rattache.
    noms = [s["name"] for s in lignes_ships]
    retenus = [s for s in lignes_ships
               if not any(autre != s["name"] and s["name"].startswith(autre + " ")
                          for autre in noms)]

    vaisseaux = []
    for ship in retenus[:limit]:
        uuid = ship["uuid"]

        # `weapon` seulement : le support qui porte l'arme est une ligne à
        # part, et le compter doublerait l'armement des vaisseaux à gimbal.
        armes = [dict(r) for r in con.execute(
            "SELECT installed_name nom, max_size taille, COUNT(*) n "
            "FROM hardpoints WHERE ship_uuid = ? AND category = 'weapon' "
            "  AND installed_name IS NOT NULL "
            "GROUP BY installed_name, max_size ORDER BY max_size DESC, n DESC",
            (uuid,))]

        # Les tourelles sont des nœuds explicitement qualifiés par la source.
        # Un gimbal de pilote reste un support orientable, pas une tourelle ;
        # l'ancien comptage les confondait et incluait aussi les affûts fils.
        tourelles = [dict(r) for r in con.execute(
            "SELECT turret_kind genre, turret_type type, COUNT(*) n "
            "FROM hardpoints WHERE ship_uuid = ? AND turret_kind IS NOT NULL "
            "GROUP BY turret_kind, turret_type ORDER BY turret_kind, turret_type",
            (uuid,))]
        supports = con.execute(
            "WITH RECURSIVE sous_tourelle(port_id) AS ("
            " SELECT port_id FROM hardpoints WHERE ship_uuid = ? "
            " AND turret_kind IS NOT NULL UNION ALL "
            " SELECT h.port_id FROM hardpoints h JOIN sous_tourelle t "
            " ON h.parent_port_id = t.port_id WHERE h.ship_uuid = ?) "
            "SELECT COUNT(*) FROM hardpoints WHERE ship_uuid = ? "
            "AND category = 'weapon_mount' AND installed_name IS NOT NULL "
            "AND port_id NOT IN (SELECT port_id FROM sous_tourelle)",
            (uuid, uuid, uuid)).fetchone()[0]

        missiles = con.execute(
            "SELECT COUNT(*) FROM hardpoints WHERE ship_uuid = ? "
            "AND category = 'missile' AND installed_name IS NOT NULL",
            (uuid,)).fetchone()[0]

        vaisseaux.append({
            "ship": ship,
            "armes": armes,
            "canons": sum(a["n"] for a in armes),
            "tourelles": tourelles,
            "supports": supports,
            "missiles": missiles,
        })

    if len(vaisseaux) < 2:
        raise NotFound(query)

    # Ce qui départage, statistique par statistique. On ne désigne **pas** de
    # vainqueur global : « le meilleur » n'existe pas sans critère, et un
    # chasseur qui perd en fret gagne en maniabilité.
    axes = []
    for stat in ("pilot_dps", "shield_hp", "health", "max_speed", "crew",
                 "cargo_scu"):
        valeurs = [(v["ship"]["name"], v["ship"].get(stat)) for v in vaisseaux]
        connues = [(n, x) for n, x in valeurs if x]
        if len(connues) < 2:
            continue
        plus_grand = SHIP_STATS[stat][2]
        meilleur = (max if plus_grand else min)(connues, key=lambda c: c[1])
        # Une égalité n'a pas de vainqueur, et l'annoncer serait faux.
        ex_aequo = sum(1 for _, x in connues if x == meilleur[1]) > 1
        axes.append({
            "stat": stat, "libelle": SHIP_STATS[stat][0],
            "unite": SHIP_STATS[stat][1], "valeurs": connues,
            "meilleur": None if ex_aequo else meilleur[0],
        })

    return {"vaisseaux": vaisseaux, "axes": axes}


# Le métier tel que le joueur le dit, et le mot que `ships.role` porte. Fermé :
# un métier inconnu ne filtre pas au hasard, il laisse la main aux autres
# outils. « Salvage » passe par le rôle et non par la carrière — la carrière
# range Vulture et Prospector dans le même « Industrial », le rôle les sépare.
_METIERS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("salvage", "recuperation", "recup", "ferrailleur", "ferraillage"),
     "Salvage", "de récupération"),
    (("minage", "minier", "miner"), "Mining", "de minage"),
    (("course", "racing"), "Racing", "de course"),
    (("medical", "secours"), "Medical", "médical"),
    (("ravitaillement", "refuel"), "Refuel", "de ravitaillement"),
    (("reparation", "repair"), "Repair", "de réparation"),
    (("debarquement", "dropship"), "Dropship", "de débarquement"),
    (("passagers",), "Passenger", "de transport de passagers"),
    (("tourisme", "touring"), "Touring", "de tourisme"),
    (("science", "scientifique"), "Science", "scientifique"),
    (("bombardier",), "Bomber", "bombardier"),
    # **Le jeu déclare ses vaisseaux de départ** : 20 vaisseaux portent
    # « Starter » dans `role` (et `career`), avec leur sous-rôle — Light
    # Fighter, Light Freight, Light Mining, Pathfinder, Light Salvage.
    # Mesuré le 2026-08-19 contre le corpus citizen-starter-guide.com :
    # « quel starter choisir » était muet et « quel vaisseau pour
    # commencer » répondait la vitesse max. Rien n'est déduit — c'est CIG
    # qui écrit le métier, comme pour le salvage. **En dernier**, pour
    # qu'un métier explicite prime : « quel vaisseau de minage pour
    # commencer » doit répondre le minage.
    # Les verbes seuls attraperaient trop large — « quels vaisseaux peuvent
    # commencer la chaîne » n'est pas une question de starter. La locution
    # entière, ou le mot du store.
    (("starter", "starters", "debutant", "pour commencer", "pour debuter",
      "pour demarrer"), "Starter", "de départ"),
)


def detect_metier(question: str) -> tuple[str, str] | None:
    """Le métier nommé, ou None — (mot du rôle, libellé français)."""
    from .normalize import normalize

    norm = normalize(question)
    for mots, role, libelle in _METIERS:
        if any(m in norm.split() or f" {m}" in norm for m in mots):
            return role, libelle
    return None


def vaisseaux_par_metier(con: sqlite3.Connection, query: str, *,
                         role: str | None = None,
                         libelle: str = "") -> dict[str, Any]:
    """« Quel vaisseau pour le salvage ? » — le rôle est en base.

    La question partait chez `compare_ships`, qui répondait « les vaisseaux
    les plus rapides » : exact, chiffré, et sans rapport — le pire cas
    documenté. Le champ `role` porte la réponse : Heavy Salvage pour le
    Reclaimer, Light Salvage pour le Vulture. Rien n'est déduit du type de
    mission — la règle « bounty donc chasseur » reste interdite — ici c'est
    CIG qui écrit le métier sur le vaisseau.
    """
    if not role:
        raise NotFound(query)
    lignes = [dict(r) for r in con.execute(
        "SELECT name, role, career, size, crew, cargo_scu FROM ships "
        "WHERE role LIKE '%' || ? || '%' OR career LIKE '%' || ? || '%' "
        "ORDER BY size, name", (role, role))]
    if not lignes:
        raise NotFound(query)
    # Les éditions et livrées prolongent le nom du vaisseau de base : la règle
    # des variantes — on écarte un nom qui prolonge celui d'un autre retenu.
    gardes: list[dict] = []
    for ligne in sorted(lignes, key=lambda x: len(x["name"])):
        if not any(ligne["name"].startswith(g["name"]) for g in gardes):
            gardes.append(ligne)
    gardes.sort(key=lambda x: (x["size"] or 0, x["name"]))
    return {"vaisseaux": gardes, "libelle": libelle, "role": role,
            "resolution": None}


def marges_d_upgrade(con: sqlite3.Connection, query: str) -> dict:
    """« En quoi je peux upgrade mon Polaris ? »

    Question du corpus restée **sans aucun outil** :
    `get_ship_components` liste l'équipement **installé**, jamais ce qu'on
    pourrait mettre à la place. Le joueur, lui, demande la marge.

    **La marge est dans le grade, pas dans la taille.** Première
    hypothèse mesurée puis abandonnée : chercher les ports dont le
    composant occupe moins que `max_size` donne **zéro** sur le Polaris —
    CIG remplit les emplacements à la taille. Ce qui varie, c'est la
    lettre : mesuré, **301 vaisseaux sur 316** portent au moins un
    composant sous le grade A, et les familles concernées sont celles
    qu'un joueur change en premier — bouclier (471 emplacements),
    refroidisseur (458), générateur (323), radar (285).

    On ne propose que ce qui **rentre** : même type, même taille, grade
    strictement meilleur. Un composant d'une autre taille ne se monte
    pas, et le proposer ferait perdre un aller-retour au joueur — c'est
    la leçon des accessoires d'arme, où le port a ses propres exigences.
    """
    res = resolve(con, query, entity_types=("ship",))
    if not res.best:
        raise NotFound(f"je ne connais pas le vaisseau « {query} »")
    vaisseau = res.best

    lignes = con.execute(
        "SELECT h.hardpoint_name, i.uuid, i.name, i.type, i.size, "
        "       i.grade_lettre, i.item_class "
        "FROM hardpoints h JOIN items i ON i.uuid = h.installed_uuid "
        "WHERE h.ship_uuid = ? AND h.editable = 1 "
        "  AND i.grade_lettre IS NOT NULL "
        "ORDER BY i.type, i.grade_lettre DESC",
        (vaisseau.entity_id,)).fetchall()

    postes: list[dict] = []
    for ligne in lignes:
        grade = (ligne["grade_lettre"] or "").strip().upper()
        if not grade:
            continue
        # Le meilleur montable : même type, même taille, grade plus haut
        # dans l'alphabet inversé (A est le sommet).
        mieux = con.execute(
            "SELECT name, grade_lettre, item_class FROM items "
            "WHERE type = ? AND size = ? AND grade_lettre IS NOT NULL "
            "  AND grade_lettre < ? AND name IS NOT NULL "
            "  AND COALESCE(is_dev, 0) = 0 "
            "ORDER BY grade_lettre, name LIMIT 3",
            (ligne["type"], ligne["size"], grade)).fetchall()
        if not mieux:
            continue
        postes.append({
            "poste": ligne["hardpoint_name"],
            "monte": ligne["name"],
            "type": ligne["type"],
            "taille": ligne["size"],
            "grade": grade,
            "classe": ligne["item_class"],
            "candidats": [{"nom": m["name"], "grade": m["grade_lettre"],
                           "classe": m["item_class"]} for m in mieux],
            "meilleur_grade": mieux[0]["grade_lettre"],
        })

    # Un même type revient sur plusieurs emplacements identiques : le
    # joueur ne veut pas lire six fois la même ligne de bouclier.
    par_type: dict[str, dict] = {}
    for poste in postes:
        entree = par_type.setdefault(poste["type"], {**poste, "emplacements": 0})
        entree["emplacements"] += 1
    return {"nom": vaisseau.name, "postes": sorted(
        par_type.values(), key=lambda p: (-p["emplacements"], p["type"])),
        "sans_marge": not par_type, "resolution": None}
