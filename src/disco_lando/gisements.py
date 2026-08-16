"""Les gisements — où trouver, où miner, et ce que ça rapporte.

Découpé de `queries.py` le 2026-08-07, mécaniquement — même règle que les
autres familles : l'ordre du fichier d'origine, la façade en ré-export, les
imports différés à travers les familles.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .normalize import normalize
from .resultats import fraicheur_jeu, fraicheur_uex, qualite_reponse
from .resolver import Resolution, resolve


def rentabilite_minage(con: sqlite3.Connection, query: str, *,
                       systeme: str | None = None,
                       limit: int = 10) -> dict[str, Any]:
    """« Quel minerai rapporte le plus ? » — prix de vente × où le miner.

    Le croisement n'existait nulle part : les prix vivaient dans `get_price`,
    les gisements dans `where_to_find_resource`, et le mineur faisait le
    rapprochement à la main. Grille appliquée : le classement porte sur le
    **prix du minerai raffiné** et le dit (ligne 8) — les bruts ne sont
    presque jamais cotés, 2 sur 29 ; un système nommé filtre les minerais
    qu'on peut y extraire (ligne 2) ; les minerais sans cotation sont nommés,
    pas omis (ligne 9).
    """
    from .raffinage import _GABARIT_MINERAI, _PALIER_FR, _PALIERS, _minerai

    noms: dict[str, str] = {}
    for r in con.execute(
            "SELECT name FROM resources WHERE name LIKE 'MineableRock%'"):
        t = _GABARIT_MINERAI.match(r["name"])
        # Les segments à tiret bas (« Savrilium_RCD_large ») sont des
        # gabarits techniques, pas des minerais qu'un joueur nomme.
        if t and t.group(2) != "TEMPLATE" and "_" not in t.group(2):
            noms.setdefault(t.group(2), t.group(1).lower())

    classement, sans_cote = [], []
    for nom, palier_brut in noms.items():
        fiche = _minerai(con, nom)
        if not fiche:
            continue
        if systeme and not any(s.lower() == systeme.lower()
                               for s in fiche["systemes"]):
            continue
        # **UEX cote les commodités en global, pas par terminal** — mesuré :
        # 0 ligne sur 113 ne porte de terminal. On rend donc le prix, jamais
        # un « où vendre » qu'on ne sait pas (ligne 9 de la grille).
        vente = _row(con, "SELECT MAX(price_sell) prix FROM uex_prices "
                          "WHERE name = ? COLLATE NOCASE AND price_sell > 0",
                     nom)
        prix = vente["prix"] if vente else None
        ligne = {"nom": nom, "palier": _PALIERS.get(palier_brut, 0),
                 "rarete": _PALIER_FR.get(_PALIERS.get(palier_brut, 0),
                                          palier_brut),
                 "prix": prix,
                 "gisements": fiche["gisements"],
                 "systemes": sorted(fiche["systemes"])}
        (classement if prix else sans_cote).append(ligne)

    if not classement and not sans_cote:
        raise NotFound(query)
    classement.sort(key=lambda l: -l["prix"])
    contrat = qualite_reponse(
        faits={
            "minerais_classes": len(classement),
            "meilleur": classement[0]["nom"] if classement else None,
            "systeme": systeme,
            # L'absence de cotation est ici une réponse : le minerai ne se
            # vend à aucun terminal relevé. Elle ampute une comparaison de
            # coût de fabrication, mais pas un classement des ventes.
            "sans_cotation_de_vente": [ligne["nom"] for ligne in sans_cote],
        },
        sources=("jeu", "uex"),
        fraicheur={
            "jeu": fraicheur_jeu(con),
            "uex": fraicheur_uex(con, "uex_prices"),
        })
    return {"classement": classement[:limit], "toutes": classement,
            "total": len(classement),
            "sans_cote": sorted(sans_cote, key=lambda l: -l["palier"]),
            "systeme": systeme, "resolution": None,
            "complet": contrat["complet"], "qualite_reponse": contrat}


def _nom_de_minerai(con: sqlite3.Connection, cand) -> str | None:
    """Le nom courant d'un minerai depuis un candidat du résolveur.

    « MineableRock_AsteroidCommon_Iron » → « Iron », « Hephaestanite (R) » →
    « Hephaestanite » : le gisement et la forme raffinée désignent le même
    minerai, et c'est lui que le joueur nomme.
    """
    from .raffinage import _GABARIT_MINERAI

    nom = (_dict(_row(con, "SELECT name FROM resources WHERE uuid = ?",
                      cand.entity_id)) or {}).get("name") or cand.name or ""
    trouve = _GABARIT_MINERAI.match(nom)
    if trouve:
        return trouve.group(2)
    return re.sub(r"\s*\((?:R|Raw|Ore)\)$", "", nom, flags=re.I) or None


def where_to_find_resource(con: sqlite3.Connection, query: str, *,
                           minerais: list[str] | None = None,
                           systeme: str | None = None) -> dict[str, Any]:
    """« Où je trouve du Quantanium ? »

    Une ressource se décline souvent en plusieurs gisements — Quantainium
    existe en version astéroïde et en version surface. On ne garde donc pas le
    seul meilleur candidat, mais tous ceux qui s'en approchent.

    Les probabilités sont des probabilités de *spawn*, pas un rendement : elles
    servent à trier, pas à promettre.

    Plusieurs minerais nommés — « où miner de l'iron et de l'héphaestanite » —
    rendent le **plan croisé** : le coin où tout extraire ensemble, pas deux
    listes que le joueur doit recouper lui-même. Journal du 2026-08-07.
    """
    if minerais:
        from .raffinage import _minerai, plan_de_minage

        fiches, vus_noms = [], set()
        for terme in [query, *minerais]:
            r = resolve(con, terme, entity_types=("resource", "commodity"),
                        limit=5).best
            nom_courant = _nom_de_minerai(con, r) if r else None
            fiche = _minerai(con, nom_courant) if nom_courant else None
            if fiche and fiche["nom"] not in vus_noms:
                vus_noms.add(fiche["nom"])
                fiches.append(fiche)
        if len(fiches) >= 2:
            plan = plan_de_minage(con, fiches, systeme_filtre=systeme)
            plan["resolution"] = None
            return plan

    res = resolve(con, query, entity_types=("resource", "commodity"), limit=30)
    if not res.best:
        raise NotFound(query, res)

    # Fenêtre large et non pas « le meilleur candidat ». Le piège est réel :
    # « QuantaniumMineableRock » est une entrée morte qui match parfaitement le
    # terme tapé mais n'a aucun lieu, tandis que les vrais gisements
    # s'appellent « MineableRock_AsteroidLegendary_Quantainium » et ne matchent
    # que de loin. Trancher au score seul donnerait une réponse vide.
    keep = [c for c in res.candidates if c.score >= res.best.score - 25]
    # **Un terme court n'a pas de place pour un presque-match.** « Or »
    # ramenait *Origin 890 Jump* — un débris récupérable, à 90 — en tête d'une
    # réponse sur le métal, à 100 : « or » est un préfixe d'« origin », et
    # `mots_inexpliques` le tient donc pour expliqué. Sur deux lettres, la
    # fenêtre de 25 points ne veut plus rien dire ; on exige le meilleur score.
    if len(normalize(query).replace(" ", "")) < 4:
        keep = [c for c in keep if c.score >= res.best.score]
    deposits, seen = [], set()

    for cand in keep:
        if cand.entity_type != "resource" or cand.entity_id in seen:
            continue
        seen.add(cand.entity_id)
        resource = _dict(_row(con, "SELECT * FROM resources WHERE uuid = ?", cand.entity_id))
        if resource is None:
            continue
        # Jointure sur le starmap pour situer chaque lieu dans l'arborescence
        # spatiale : Yela est une lune de Crusader, dans Stanton.
        locations = [dict(r) for r in con.execute(
            "SELECT l.name, l.system, l.loc_type, rl.group_name, rl.probability, "
            "       s.parent_name, s.parent_type, s.type_name, s.path "
            "FROM resource_locations rl "
            "JOIN locations l ON l.uuid = rl.location_uuid "
            "LEFT JOIN starmap s ON s.name = l.name AND s.system_name IS NOT NULL "
            "WHERE rl.resource_uuid = ? "
            "GROUP BY l.uuid ORDER BY rl.probability DESC, l.name",
            (cand.entity_id,),
        )]
        # « À Stanton » restreint les lieux — la contrainte se perdait en
        # silence, journal du 2026-08-07. Un gisement vidé par le filtre
        # tombe : il ne répond pas à la question posée.
        if systeme:
            locations = [l for l in locations
                         if (l.get("system") or "").lower() == systeme.lower()]
        composition = [dict(r) for r in con.execute(
            "SELECT part_name, min_pct, max_pct, probability FROM resource_composition "
            "WHERE deposit_uuid = ? ORDER BY max_pct DESC", (cand.entity_id,),
        )]
        if not locations and not (composition and not systeme):
            continue
        # Pas de troncature ici : un gisement plafonne à quelques dizaines de
        # lieux, et couper au niveau de la donnée fait mentir les comptages en
        # aval — « 51 lieux dont 25 bases minières » alors que les 51 en sont.
        # C'est à l'affichage de décider ce qu'il montre.
        deposits.append({
            "resource": resource,
            "match": cand,
            "locations": locations,
            "location_count": len(locations),
            "composition": composition,
        })

    # Un gisement qui a des lieux répond à la question ; un gisement sans lieu
    # ne répond à rien. S'il existe au moins un des premiers, on écarte les
    # seconds plutôt que de les faire passer devant.
    located = [d for d in deposits if d["location_count"]]
    if located:
        deposits = sorted(located, key=lambda d: -d["location_count"])
    else:
        deposits = sorted(deposits, key=lambda d: -d["match"].score)

    trade = []
    for cand in keep:
        if cand.entity_type != "commodity":
            continue
        trade = [dict(r) for r in con.execute(
            "SELECT s.display_name, s.class_name, cs.direction FROM commodity_shops cs "
            "JOIN shops s ON s.uuid = cs.shop_uuid WHERE cs.commodity_uuid = ? "
            "ORDER BY cs.direction, s.class_name", (cand.entity_id,),
        )]
        break

    if not deposits and not trade:
        raise NotFound(query, res)

    # La résolution renvoyée doit désigner l'entité **effectivement retenue**,
    # pas le premier candidat du résolveur. « Quantanium » match parfaitement
    # « QuantaniumMineableRock », une entrée morte sans aucun gisement, tandis
    # que les vrais s'appellent « MineableRock_AsteroidLegendary_Quantainium »
    # et ne matchent que de loin. La réponse était juste, mais le champ
    # `entity` de l'API annonçait l'entrée morte — donc mentait sur sa source.
    if deposits:
        retenu = deposits[0]["match"]
        res = Resolution(res.query,
                         [retenu] + [c for c in res.candidates if c is not retenu])
    return {"resolution": res, "deposits": deposits, "trade": trade,
            "systeme": systeme}


# Les trois formes sous lesquelles la composition d'un gisement nomme un
# minerai — « Gold (Ore) », « Quantainium (Raw) », « Hephaestanite (R) ».
# Mesuré : 591 mentions en « Raw », 503 en « Ore », 54 en « R », 67 sans
# suffixe. Ne chercher qu'une forme en raterait les deux tiers.
_FORME_DE_MINERAI = re.compile(r"\s*\((?:ore|raw|r|pure)\)\s*$", re.I)


def ou_miner(con: sqlite3.Connection, query: str, *,
             limit: int = 10, systeme: str | None = None) -> dict[str, Any]:
    """« Où miner de l'or ? » — en tenant compte de ce que contiennent les filons.

    **`where_to_find_resource` répond dans l'autre sens**, et c'est une autre
    question. Il part d'un gisement et dit ce qu'on y trouve ; celui-ci part
    d'un **minerai** et cherche tous les gisements qui en contiennent, y
    compris ceux qui portent le nom d'un autre.

    L'écart est loin d'être théorique. Mesuré sur l'or : les filons qui
    s'appellent « Gold » en rendent 28 à 78 %, mais l'or apparaît dans **157
    gisements au total**, à 28 % de probabilité moyenne — un filon de Savrilium
    en contient 5 à 10 %, un filon de Bexalite 2 à 5 % et se trouve sur 15
    lieux répartis dans trois systèmes. Un mineur qui ne connaît que les filons
    homonymes ignore la majorité de ses occasions.

    Le classement se fait sur le **rendement espéré** — probabilité × teneur
    moyenne — parce que c'est ce qui décide où l'on va : un filon riche mais
    rare vaut moins qu'un filon moyen et certain. Les deux nombres restent
    rendus séparément, l'espérance ne remplaçant pas ce qui la compose.
    """
    res = resolve(con, query, entity_types=("resource", "commodity"), limit=8)
    if not res.best:
        raise NotFound(query, res)

    # On cherche sur le **nom du minerai**, forme retirée : la composition
    # écrit « Gold (Ore) » là où la ressource s'appelle « Gold ».
    cible = normalize(_FORME_DE_MINERAI.sub("", res.best.name or query))
    if not cible:
        raise NotFound(query, res)

    lignes = [dict(r) for r in con.execute(
        "SELECT r.name gisement, r.tier palier, rc.part_name minerai, "
        "       rc.min_pct, rc.max_pct, rc.probability, "
        "       COUNT(DISTINCT rl.location_uuid) lieux, "
        "       GROUP_CONCAT(DISTINCT s.system_name) systemes "
        "FROM resource_composition rc "
        "JOIN resources r ON r.uuid = rc.deposit_uuid "
        "LEFT JOIN resource_locations rl ON rl.resource_uuid = rc.deposit_uuid "
        "LEFT JOIN locations l ON l.uuid = rl.location_uuid "
        "LEFT JOIN starmap s ON s.name = l.name AND s.system_name IS NOT NULL "
        "GROUP BY rc.id ORDER BY rc.probability * rc.max_pct DESC")]

    filons = []
    for ligne in lignes:
        nom = normalize(_FORME_DE_MINERAI.sub("", ligne["minerai"] or ""))
        if nom != cible:
            continue
        moyenne = ((ligne["min_pct"] or 0) + (ligne["max_pct"] or 0)) / 2
        ligne["esperance"] = round((ligne["probability"] or 0) * moyenne, 1)
        # Un filon qui porte le nom du minerai est la source évidente ; les
        # autres sont la vraie valeur de cet outil, et le rendu les sépare.
        ligne["dedie"] = cible in normalize(ligne["gisement"] or "")
        ligne["systemes"] = sorted(
            {s for s in (ligne["systemes"] or "").split(",") if s})
        filons.append(ligne)

    if not filons:
        raise NotFound(query, res)

    # « À Stanton » restreint aux filons présents dans le système — ligne 2
    # de la grille : la contrainte se perdait en silence.
    if systeme:
        dans_systeme = [f for f in filons
                        if any(s.lower() == systeme.lower()
                               for s in f["systemes"])]
        if not dans_systeme:
            raise NotFound(
                f"aucun filon de {res.best.name} localisé dans {systeme}", res)
        filons = dans_systeme

    # **Un filon sans lieu connu ne se mine pas.** Il existe en base, mais
    # l'envoyer en tête enverrait le joueur nulle part — même règle que
    # `where_to_find_resource`.
    situes = [f for f in filons if f["lieux"]]
    noms_situes = {f["gisement"] for f in situes}
    noms_sans_lieu = {
        f["gisement"] for f in filons
        if not f["lieux"] and f["gisement"] not in noms_situes
    }
    retenus = situes or filons
    retenus.sort(key=lambda f: (-f["esperance"], -f["lieux"]))

    # **Dédoublonner sur le nom affiché**, comme partout ailleurs dans le
    # projet. Plusieurs gisements distincts portent le même nom avec des
    # teneurs différentes — deux « MineableRock_AsteroidRare_Gold », l'un à
    # 28-78 %, l'autre à 6-12 %. Le joueur lit deux fois la même ligne et ne
    # peut pas les distinguer sur le terrain : on garde la meilleure, la liste
    # étant déjà triée.
    vus, uniques = set(), []
    for filon in retenus:
        if filon["gisement"] in vus:
            continue
        vus.add(filon["gisement"])
        uniques.append(filon)
    retenus = uniques
    # Le joueur distingue les filons par leur nom affiché. Cent cinquante
    # lignes de composition pouvaient ne représenter que quelques noms ; le
    # compte des absents suit donc le même dédoublonnage que la liste rendue.
    sans_lieu = len(noms_sans_lieu)
    contrat = qualite_reponse(
        faits={
            "minerai": res.best.name,
            "filons_localises": len(retenus),
            "systeme": systeme,
        },
        manques=([f"lieu publié absent pour {sans_lieu} filon(s)"]
                  if sans_lieu else []),
        sources=("jeu",), fraicheur={"jeu": fraicheur_jeu(con)})
    return {
        "resolution": res,
        "minerai": res.best.name,
        "filons": retenus[:limit],
        "total": len(retenus),
        "dedies": sum(1 for f in retenus if f["dedie"]),
        "sans_lieu": sans_lieu,
        "systeme": systeme,
        "complet": contrat["complet"],
        "qualite_reponse": contrat,
    }
