"""Les missions — groupes, payes, sites, activités, chaînes et réputation.

Découpé de `queries.py` le 2026-08-07, mécaniquement : les fonctions sont
celles du fichier d'origine, dans son ordre, et `queries` les réexporte —
aucun appelant ne change de porte. La règle du découpage est celle du
render : un module par famille de questions, et les imports différés quand
une fonction traverse les familles.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .resolver import resolve


def group_missions(con: sqlite3.Connection,
                   missions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Regroupe des missions en (org, système, activité, plage de rangs).

    Star Citizen ne distribue pas des missions isolées : il distribue des
    *paliers* d'une activité d'une org dans un système. Le blueprint de
    l'Antium Core Sand sort des patrouilles Foxwell Enforcement à Pyro et à
    Stanton, sur les six rangs de Neutral à Head Contractor. Répondre par six
    titres serait exact et inutilisable ; répondre par le groupe, c'est
    répondre à la question posée.

    `complete` dit si le groupe couvre *toutes* les missions de son (org,
    système) connues en base — auquel cas on peut dire « les missions X à Y »
    sans restriction, plutôt que « certaines missions ».
    """
    from .render import speakable_title

    par_cle: dict[tuple, dict[str, Any]] = {}
    for mission in missions:
        cle = (mission.get("mission_giver"), mission.get("system"),
               _activity(mission.get("family")))
        groupe = par_cle.setdefault(cle, {
            "mission_giver": cle[0], "system": cle[1], "activity": cle[2],
            "families": set(), "titles": [], "ranks": [], "distinctes": set(),
            "payes": [],
        })
        groupe["families"].add(mission.get("family"))
        groupe["titles"].append(mission.get("title"))
        # La paye décide autant que le rang : « 337 missions » ne dit pas si
        # ça vaut le déplacement. La colonne était ingérée et n'apparaissait
        # que sur une mission nommée.
        if mission.get("reward_uec"):
            groupe["payes"].append(mission["reward_uec"])
        # Même dédoublonnage que pour le blueprint : un joueur compte les
        # missions qu'il peut distinguer, pas les lignes de la source.
        groupe["distinctes"].add(
            (speakable_title(mission.get("title")), mission.get("min_standing_name")))
        if mission.get("min_standing_value") is not None:
            groupe["ranks"].append(
                (mission["min_standing_value"], mission.get("min_standing_name"))
            )

    resultat = []
    for groupe in par_cle.values():
        rangs = sorted(set(groupe["ranks"]))
        total = con.execute(
            "SELECT COUNT(*) FROM contracts WHERE mission_giver IS ? AND system IS ? "
            "AND not_for_release = 0 AND work_in_progress = 0 AND title IS NOT NULL",
            (groupe["mission_giver"], groupe["system"]),
        ).fetchone()[0]
        resultat.append({
            "mission_giver": groupe["mission_giver"],
            "system": groupe["system"],
            "activity": groupe["activity"],
            "families": sorted(f for f in groupe["families"] if f),
            "titles": sorted(set(t for t in groupe["titles"] if t)),
            "mission_count": len(groupe["distinctes"]),
            "group_total": total,
            "complete": total > 0 and len(groupe["titles"]) >= total,
            "rank_min": rangs[0][1] if rangs else None,
            "rank_max": rangs[-1][1] if rangs else None,
            "rank_count": len(rangs),
            "paye_min": min(groupe["payes"]) if groupe["payes"] else None,
            "paye_max": max(groupe["payes"]) if groupe["payes"] else None,
            "paye_mediane": (sorted(groupe["payes"])[len(groupe["payes"]) // 2]
                             if groupe["payes"] else None),
            "paye_connue": len(groupe["payes"]),
        })
    resultat.sort(key=lambda g: (-g["mission_count"], str(g["system"])))
    return resultat


# « FoxwellEnforcement_Patrol » -> « Patrol ». L'org est déjà portée par
# mission_giver ; ce qui reste est l'activité.
_ACTIVITIES = {
    "Patrol": "patrouille", "Ambush": "embuscade", "EscortShips": "escorte",
    "DestroyItems": "destruction d'installations", "RecoverCargo": "récupération de cargo",
    "Courier": "livraison", "Hauling": "transport", "Mercenary": "mercenariat",
    "Investigation": "enquête", "MissingPersons": "personnes disparues",
    "DefendShip": "défense de vaisseau", "ShipWaveAttack": "vagues d'assaut",
    "Bounty": "prime", "ShipMining": "minage", "ResourceGathering": "collecte",
    "DefendDestructibleEntities": "défense d'installations",
    "DefendEntitiesAndEscort": "défense et escorte",
}


def get_mission_group(con: sqlite3.Connection, query: str,
                      *, system: str | None = None,
                      volet: str | None = None) -> dict[str, Any]:
    """« Les missions Foxwell Enforcement à Pyro, ça donne quoi ? »

    L'unité de réponse naturelle du jeu n'est pas le contrat mais le couple
    (organisation, système) : une org y propose une série d'activités échelonnées
    par rang, et c'est la progression dans cette série qui débloque les
    récompenses.
    """
    res = resolve(con, query, entity_types=("org",))
    if not res.best:
        raise NotFound(query, res)
    org = res.best.entity_id

    rows = con.execute(
        "SELECT * FROM mission_groups WHERE mission_giver = ? "
        + ("AND system = ? " if system else "")
        + "ORDER BY contract_count DESC",
        (org, system) if system else (org,),
    ).fetchall()
    if not rows:
        raise NotFound(query, res)

    groupes = []
    for row in rows:
        activites = [dict(r) for r in con.execute(
            "SELECT family, COUNT(*) n, MIN(min_standing_value) lo, "
            "       MAX(min_standing_value) hi "
            "FROM contracts WHERE mission_giver = ? AND system IS ? "
            "  AND not_for_release = 0 AND work_in_progress = 0 AND family IS NOT NULL "
            "GROUP BY family ORDER BY n DESC",
            (org, row["system"]),
        )]
        for activite in activites:
            activite["activity"] = _activity(activite["family"])

        paliers = [dict(r) for r in con.execute(
            "SELECT min_standing_name nom, min_standing_value valeur, COUNT(*) n "
            "FROM contracts WHERE mission_giver = ? AND system IS ? "
            "  AND min_standing_name IS NOT NULL AND not_for_release = 0 "
            "GROUP BY min_standing_name, min_standing_value ORDER BY valeur",
            (org, row["system"]),
        )]

        # Ce que la progression dans ce groupe finit par débloquer.
        blueprints = [dict(r) for r in con.execute(
            "SELECT DISTINCT b.output_name, MIN(c.min_standing_value) rang_mini "
            "FROM contracts c "
            "JOIN contract_reward_pools crp ON crp.contract_uuid = c.uuid "
            "JOIN blueprint_sources bs ON bs.pool_uuid = crp.pool_uuid "
            "JOIN blueprints b ON b.uuid = bs.blueprint_uuid "
            "WHERE c.mission_giver = ? AND c.system IS ? AND c.not_for_release = 0 "
            "GROUP BY b.output_name ORDER BY rang_mini, b.output_name",
            (org, row["system"]),
        )]

        # La paye : « 337 missions » ne dit pas si ça vaut le déplacement.
        # La colonne est ingérée depuis le premier jour et n'apparaissait que
        # sur une mission nommée, jamais sur le groupe — c'est-à-dire jamais
        # là où le joueur choisit.
        paye = _dict(_row(
            con,
            "SELECT COUNT(*) n, MIN(reward_uec) mini, MAX(reward_uec) maxi, "
            "       AVG(reward_uec) moyenne "
            "FROM contracts WHERE mission_giver = ? AND system IS ? "
            "  AND not_for_release = 0 AND work_in_progress = 0 "
            "  AND reward_uec > 0",
            org, row["system"]))

        groupes.append({**dict(row), "activities": activites,
                        "ranks": paliers, "blueprints": blueprints,
                        "paye": paye if (paye or {}).get("n") else None})

    # `volet="blueprints"` : la question ne veut que la liste des blueprints —
    # demande de l'utilisateur, « me demander si je veux les blueprint » puis
    # les donner tous quand il dit oui.
    return {"resolution": res, "org": org, "groups": groupes, "volet": volet}


def missions_payantes(con: sqlite3.Connection, query: str, *,
                      system: str | None = None, lieu: str | None = None,
                      critere: str = "uec", difficulte: str | None = None,
                      activite: str | None = None,
                      types: tuple[str, ...] = (),
                      plancher: float | None = None,
                      plancher_strict: bool = False,
                      limit: int = 8) -> dict[str, Any]:
    """« Quelles sont les missions les mieux payées ? »

    Le montant est en base depuis le premier jour — 2 345 contrats sur 5 108
    en portent un, de 1 à 711 750 aUEC. Aucun outil ne les classait, alors que
    « ça rapporte combien » est la question qui décide.

    On filtre sur une organisation si la question en nomme une, sinon on prend
    tout le catalogue. Les contrats non sortis restent exclus : annoncer une
    paye pour une mission qui n'existe pas serait pire qu'un silence.
    Pour un seuil, le compte porte sur les titres distincts affichables ;
    ``plancher_strict`` distingue « plus de » de « au moins ».
    """
    org = None
    res = resolve(con, query, entity_types=("org",), limit=1)
    if res.best is not None and res.best.score >= 85.0:
        org = res.best.entity_id

    # Deux monnaies, deux questions. « Ça paye combien » et « ça fait monter
    # ma réputation de combien » n'ont pas la même réponse et ne classent pas
    # les mêmes missions : 2 345 contrats portent un montant en aUEC, 5 491
    # lignes portent un gain de réputation, et les deux ensembles ne se
    # recouvrent qu'en partie.
    reputation = critere == "reputation"
    clauses = ["c.not_for_release = 0",
               "c.work_in_progress = 0", "c.title IS NOT NULL"]
    clauses.append("g.amount > 0" if reputation else "c.reward_uec > 0")
    args: list[Any] = []
    # « Les missions faciles qui paient le plus » : le filtre est l'étiquette
    # de difficulté du jeu — la seule note qui recouvre des contrats payés.
    if difficulte in _DIFFICULTES:
        labels = _DIFFICULTES[difficulte]
        clauses.append(f"c.difficulty_label IN ({','.join('?' * len(labels))})")
        args.extend(labels)
    # « Mission de combat qui paye le plus » : l'activité filtre par le
    # typage du jeu — la contrainte était perdue en silence (grille, l. 1).
    if types:
        clauses.append(f"c.mission_type IN ({','.join('?' * len(types))})")
        args.extend(types)
    if org:
        clauses.append("c.mission_giver = ?")
        args.append(org)
    if system:
        clauses.append("c.system = ?")
        args.append(system)

    # « Sur Yela » : la donnée ne descend pas à la lune, et il faut le dire.
    # Mesuré sur `contract_locations`, pourtant riche de 1,4 million de lignes :
    # **aucun** des 2 345 contrats payés n'a de lieu de *disponibilité* nommé —
    # ce sont des convoyages, qu'on prend n'importe où — et leurs lieux de
    # *mission* sont des salles (« Lobby », « Storehouse »), pas des corps
    # célestes. La seule géographie fiable est `contracts.system`.
    #
    # On résout donc le lieu demandé jusqu'à son système et on filtre là-dessus,
    # en rendant les deux pour que la réponse annonce la granularité réelle
    # plutôt que de laisser croire à un filtre à la lune près.
    lieu_resolu = systeme_du_lieu = None
    if lieu:
        res_lieu = resolve(con, lieu, entity_types=("starmap",), limit=1)
        if res_lieu.best is not None and res_lieu.best.score >= 85.0:
            ligne = _dict(_row(con, "SELECT name, system_name FROM starmap "
                                    "WHERE uuid = ?", res_lieu.best.entity_id))
            if ligne:
                lieu_resolu = ligne["name"]
                systeme_du_lieu = ligne["system_name"]
    # **Le lieu ne doit pas se faire passer pour une organisation.** Mesuré :
    # « quelles missions dans Stanton » résolvait « Stanton System » comme
    # `mission_giver` à 85,5 — le filtre ne rendait alors aucune ligne, et la
    # question limpide finissait en `NotFound`. Un nom qui désigne déjà le lieu
    # demandé n'est pas l'organisation qui donne les missions.
    # Le désamorçage vaut pour le lieu ET pour le système : « facile à
    # stanton » passe désormais par `system=` sans lieu, et « Stanton
    # System » redevenait l'organisation — zéro ligne, NotFound.
    prefixes = tuple(p.lower() for p in (lieu_resolu, systeme_du_lieu, system)
                     if p)
    if org and prefixes and org.lower().startswith(prefixes):
        clauses = [c for c in clauses if c != "c.mission_giver = ?"]
        # Retirer la **valeur**, jamais la dernière position : quand
        # `system` arrive en argument, il est appendu après l'organisation,
        # et `args[:-1]` décalait tous les paramètres SQL d'un cran.
        if org in args:
            args.remove(org)
        org = None

    if systeme_du_lieu and not system:
        clauses.append("c.system = ?")
        args.append(systeme_du_lieu)
    # « Combien de missions rapportent plus de 50 000 aUEC » : la question
    # partait chez l'analyste (~30 s) pour un simple filtre — le
    # plancher se lit en déterministe, et le compte se fait sur les titres
    # affichés, jamais sur les gabarits (règle « compte = liste »).
    total_seuil = None
    clause_plancher = None
    if plancher is not None and not reputation:
        clause_plancher = ("c.reward_uec > ?" if plancher_strict
                           else "c.reward_uec >= ?")
        clauses.append(clause_plancher)
        args.append(plancher)
        from .descriptions import _titre_utilisable as _tu
        total_seuil = sum(
            1 for (t,) in con.execute(
                "SELECT DISTINCT c.title FROM contracts c WHERE "
                + " AND ".join(clauses), args)
            if _tu(t))
    jointure = (" JOIN contract_reputation g ON g.contract_uuid = c.uuid "
                "AND g.direction = 'gained'" if reputation else "")
    montant = "MAX(g.amount)" if reputation else "c.reward_uec"

    lignes = [dict(r) for r in con.execute(
        "SELECT c.title, c.mission_giver, c.system, c.mission_type, "
        # `montant` porte la valeur du critère choisi ; `reward_uec` reste
        # exposé tel quel, c'est la paye et rien d'autre.
        f"       {montant} montant, c.reward_uec, c.min_standing_name, "
        "       c.deadline_seconds, c.difficulty_label"
        + (", g.faction_name" if reputation else "") +
        " FROM contracts c" + jointure + " WHERE " + " AND ".join(clauses)
        # Le regroupement porte sur ce qui identifie la mission, jamais sur
        # l'agrégat lui-même : SQLite refuse `GROUP BY MAX(...)`.
        + " GROUP BY c.title, c.mission_giver"
        + ("" if reputation else ", c.reward_uec")
        + " ORDER BY montant DESC LIMIT ?",
        # Le double du plafond : les gabarits filtrés après coup ne doivent
        # pas raccourcir la liste rendue.
        (*args, limit * 2))]
    # **Un titre de gabarit n'est le nom de rien** — « (sans titre) —
    # 500 000 aUEC » en tête de liste est une ligne que le joueur ne peut ni
    # chercher ni prendre. Journal du 2026-08-07.
    from .descriptions import _titre_utilisable

    lignes = [l for l in lignes if _titre_utilisable(l["title"])][:limit]
    # **Aucune mission payée n'est un fait, pas une absence de donnée.**
    # Wikelo a 88 contrats et aucun montant fixe ; Foxwell en a 110, tous à
    # récompense calculée. `NotFound` disait « je ne connais pas », ce qui est
    # faux et coupe la conversation — surtout en reprise, où l'organisation
    # vient d'être nommée par la réponse précédente.
    if not lignes and (org or systeme_du_lieu):
        connus = con.execute(
            "SELECT COUNT(*), SUM(reward_calculated) FROM contracts c WHERE "
            + " AND ".join(c for c in clauses
                           if c not in ("c.reward_uec > 0", "g.amount > 0")),
            args).fetchone()
        if connus and connus[0]:
            return {"org": org, "system": system or systeme_du_lieu,
                    "critere": critere, "lieu": lieu_resolu,
                    "difficulte": difficulte, "activite": activite,
                    "elargi_au_systeme": False, "missions": [],
                    "sans_montant": connus[0], "calculees": connus[1] or 0,
                    "resolution": res if org else None}
    if not lignes and plancher is not None:
        # **Zéro au-dessus d'un plancher est une réponse, pas une absence.**
        # « Plus de 100k dans Pyro » rendait NotFound ; on dit zéro, et ce
        # qui bloque — la meilleure paye disponible sous le seuil (grille :
        # relâcher le critère et dire lequel coûte).
        sans_plancher = [cl for cl in clauses if cl != clause_plancher]
        # Le meilleur contrat **au titre affichable** : le premier de la
        # liste brute est souvent un gabarit, et « La mieux payée est , à
        # 18 000 aUEC » nommait le vide.
        from .descriptions import _titre_utilisable as _tu
        meilleure = next(
            (dict(r) for r in con.execute(
                "SELECT c.title, c.reward_uec montant FROM contracts c "
                "WHERE " + " AND ".join(sans_plancher)
                + " ORDER BY c.reward_uec DESC LIMIT 10", args[:-1])
             if _tu(r["title"])), None)
        return {"org": org, "system": system or systeme_du_lieu,
                "critere": critere, "lieu": lieu_resolu,
                "difficulte": difficulte, "activite": activite,
                "elargi_au_systeme": False, "plancher": plancher,
                "plancher_strict": plancher_strict,
                "total_seuil": 0, "missions": [],
                "meilleure_sous_plancher": _dict(meilleure),
                "resolution": res if org else None}
    if not lignes:
        raise NotFound(query, res)
    return {"org": org, "system": system or systeme_du_lieu,
            "critere": critere, "lieu": lieu_resolu,
            "difficulte": difficulte, "activite": activite,
            # Vrai quand le joueur a nommé un endroit plus fin que le système :
            # la réponse doit alors dire qu'elle a élargi.
            "elargi_au_systeme": bool(lieu_resolu and systeme_du_lieu
                                      and lieu_resolu != systeme_du_lieu),
            "plancher": plancher, "plancher_strict": plancher_strict,
            "total_seuil": total_seuil,
            "missions": lignes, "resolution": res if org else None}


# Les activités telles que le jeu les type — `contracts.mission_type` est un
# vocabulaire fermé de 30 valeurs, mesuré le 2026-08-07. On ne mappe que
# celles qu'un joueur nomme ; « Battaglia » ou « Priority » ne sont pas des
# activités, ce sont des campagnes.
_ACTIVITES_DE_MISSION: dict[str, tuple[tuple[str, ...], str]] = {
    "minage": (("Ship Mining", "Ground Vehicle Mining", "Hand Mining",
                "Mining"),
               r"\bminages?\b|\bminer\b|\bminieres?\b"),
    "récupération": (("Salvage",),
                     r"\brecuperations?\b|\bsalvage\b|\brecyclages?\b"),
    "transport": (("Hauling", "Hauling - Planetary", "Hauling - Stellar",
                   "Hauling - Interstellar", "Hauling - Local"),
                  r"\btransports?\b|\bfrets?\b|\bhauling\b|\bconvoyages?\b"),
    "livraison": (("Delivery", "Courier"),
                  r"\blivraisons?\b|\bcoursiers?\b|\bcolis\b"),
    "course": (("Racing",), r"\bcourses?\b|\bracing\b"),
    "chasse à la prime": (("Bounty Hunter",),
                          r"\bprimes?\b|\bbounty\b"),
    "mercenariat": (("Mercenary",),
                    r"\bmercenaires?\b|\bmercenariats?\b"),
    "ravitaillement": (("Refueling",),
                       r"\bravitaillements?\b|\brefueling\b"),
    "enquête": (("Investigation",),
                r"\benquetes?\b|\binvestigations?\b"),
    "maintenance": (("Maintenance",), r"\bmaintenances?\b"),
    # « Mission de combat qui paye le plus » — la contrainte était perdue en
    # silence. Le combat recouvre les types que le jeu étiquette ainsi.
    "combat": (("Mercenary", "Bounty Hunter", "PvP Missions"),
               r"\bcombats?\b|\bcombattre\b|\bpvp\b"),
}


def detect_activite(question: str) -> tuple[str, tuple[str, ...]] | None:
    """L'activité de mission nommée dans la question, ou None."""
    from .normalize import normalize

    norm = normalize(question or "")
    for label, (types, motif) in _ACTIVITES_DE_MISSION.items():
        if re.search(motif, norm):
            return label, types
    return None


def missions_par_activite(con: sqlite3.Connection, query: str, *,
                          activite: str | None = None,
                          types: tuple[str, ...] = (),
                          system: str | None = None) -> dict[str, Any]:
    """« Y a-t-il des missions de minage à Stanton ? » — la liste, par type.

    Remarque de l'utilisateur : le jeu type ses missions (`mission_type` —
    Ship Mining, Hand Mining…), il faut pouvoir les demander par activité et
    « poser des questions spécifiques dessus derrière ». Le compte se fait
    sur le titre affiché, comme partout.
    """
    if not types:
        trouve = detect_activite(query)
        if trouve is None:
            raise NotFound(f"je ne reconnais pas d'activité dans « {query} »")
        activite, types = trouve

    trous = ",".join("?" * len(types))
    args: list[Any] = list(types)
    clause = ""
    if system:
        clause = " AND c.system = ?"
        args.append(system)
    lignes = [dict(r) for r in con.execute(
        f"SELECT DISTINCT c.title, c.mission_giver, c.mission_type, c.system "
        f"  FROM contracts c "
        f" WHERE c.mission_type IN ({trous}){clause} "
        f"   AND c.not_for_release = 0 AND c.work_in_progress = 0 "
        f"   AND c.title IS NOT NULL "
        f" ORDER BY c.mission_type, c.mission_giver, c.title", args)]
    if not lignes:
        raise NotFound(f"aucune mission de {activite}"
                       + (f" dans {system}" if system else ""))

    # Dédoublonnage sur le titre affiché, par type — un joueur compte ce
    # qu'il lit.
    par_type: dict[str, list[dict[str, Any]]] = {}
    vus: set[tuple[str, str]] = set()
    for ligne in lignes:
        cle = (ligne["mission_type"], ligne["title"])
        if cle in vus:
            continue
        vus.add(cle)
        par_type.setdefault(ligne["mission_type"], []).append(ligne)
    return {"activite": activite, "system": system,
            "par_type": par_type,
            "total": sum(len(v) for v in par_type.values()),
            "resolution": None}


# « Facile » et « difficile » tels que le jeu étiquette ses contrats —
# `difficulty_label`, 791 contrats sur 4 097. Mesuré le 2026-08-07 : les notes
# fines (`diff_risque`…) ne recouvrent **aucun** contrat payé, le label en
# recouvre 217 — c'est donc lui qui répond à « facile qui paie ».
_DIFFICULTES = {"facile": ("VeryEasy", "Easy"),
                "difficile": ("Hard", "VeryHard", "Super")}


def detect_difficulte(question: str) -> str | None:
    """« Facile », « difficile », ou rien."""
    from .normalize import normalize

    norm = normalize(question or "")
    if re.search(r"\bfaciles?\b|\bsimples?\b|\btranquilles?\b|\bsans risques?\b",
                 norm):
        return "facile"
    if re.search(r"\bdifficiles?\b|\bdur[es]?s?\b|\brisquee?s?\b", norm):
        return "difficile"
    return None


def blueprints_par_systeme(con: sqlite3.Connection, query: str, *,
                           systeme: str | None = None,
                           famille: str | None = None,
                           clause: str | None = None,
                           mode: str = "classe") -> dict[str, Any]:
    """« Quels blueprints s'obtiennent dans les missions à Stanton ? »

    La chaîne existait sans qu'aucun outil ne la remonte dans ce sens :
    `blueprint_sources` → pools de récompense → contrats. 201 blueprints à
    Stanton — les énumérer serait illisible, et **le jeu raisonne par
    groupes** : on rend le résumé par organisation, le détail d'un groupe
    étant à un « que donnent les missions X en blueprint » de distance.
    """
    # « Les blueprints d'armes FPS de Pyro » : la famille filtre par le type
    # d'objet de la **sortie**, et le rendu groupe par classe d'arme avec la
    # marque — format demandé par l'utilisateur, journal du 2026-08-07.
    if famille and clause:
        from .armurerie import _groupe_de, _nom_de_base

        filtre_sys = " AND c.system = ?" if systeme else ""
        lignes = [dict(r) for r in con.execute(
            "SELECT DISTINCT b.output_name nom, i.subtype, i.size, i.tags, "
            "       i.manufacturer_name marque "
            "  FROM blueprint_sources bs "
            "  JOIN contract_reward_pools p ON p.pool_uuid = bs.pool_uuid "
            "  JOIN contracts c ON c.uuid = p.contract_uuid "
            "  JOIN blueprints b ON b.uuid = bs.blueprint_uuid "
            "  JOIN items i ON i.uuid = b.output_uuid "
            f" WHERE c.not_for_release = 0 AND i.{clause}{filtre_sys}",
            ([systeme] if systeme else []))]
        if not lignes:
            raise NotFound(f"aucun blueprint de {famille}"
                           + (f" dans {systeme}" if systeme else ""))
        groupes_armes: dict[str, dict[str, dict[str, Any]]] = {}
        for ligne in lignes:
            ligne["name"] = ligne["nom"]
            base = _nom_de_base(ligne["nom"])
            groupe = groupes_armes.setdefault(_groupe_de(ligne, mode), {})
            entree = groupe.setdefault(base, {"nom": base, "coloris": 0,
                                              "marque": ligne.get("marque")})
            if ligne["nom"] != base:
                entree["coloris"] += 1
        return {"systeme": systeme, "famille": famille, "mode": mode,
                "clause": clause,
                "groupes_armes": groupes_armes,
                "total": sum(len(g) for g in groupes_armes.values()),
                "total_avec_coloris": len(lignes),
                "groupes": [], "resolution": None}

    clause, args = "", []
    if systeme:
        clause = " AND c.system = ?"
        args.append(systeme)
    groupes = [dict(r) for r in con.execute(
        "SELECT c.mission_giver org, c.system, "
        "       COUNT(DISTINCT b.output_name) n "
        "  FROM blueprint_sources bs "
        "  JOIN contract_reward_pools p ON p.pool_uuid = bs.pool_uuid "
        "  JOIN contracts c ON c.uuid = p.contract_uuid "
        "  JOIN blueprints b ON b.uuid = bs.blueprint_uuid "
        f" WHERE c.not_for_release = 0 AND c.mission_giver IS NOT NULL{clause} "
        "GROUP BY c.mission_giver, c.system ORDER BY n DESC", args)]
    if not groupes:
        raise NotFound(f"aucun blueprint de mission"
                       + (f" dans {systeme}" if systeme else ""))
    total = con.execute(
        "SELECT COUNT(DISTINCT b.output_name) "
        "  FROM blueprint_sources bs "
        "  JOIN contract_reward_pools p ON p.pool_uuid = bs.pool_uuid "
        "  JOIN contracts c ON c.uuid = p.contract_uuid "
        "  JOIN blueprints b ON b.uuid = bs.blueprint_uuid "
        f" WHERE c.not_for_release = 0{clause}", args).fetchone()[0]
    return {"systeme": systeme, "groupes": groupes, "total": total,
            "resolution": None}


def panorama_missions(con: sqlite3.Connection, query: str, *,
                      systeme: str | None = None,
                      volet: str | None = None) -> dict[str, Any]:
    """« Donne-moi les missions de Pyro » — le menu, pas huit cents lignes.

    Remarque de l'utilisateur : « vu qu'il y en a beaucoup tu me demandes
    quels types de missions je veux, ou bien pour qui, en me citant quelques
    exemples ». On rend donc les deux entrées — par type, par donneur — avec
    leurs comptes ; `volet` sert les reprises « tous les types » et « tous
    les donneurs », et le choix d'un type ou d'un donneur retombe sur les
    outils existants.
    """
    if not systeme:
        raise NotFound(query)
    base = ("FROM contracts WHERE system = ? AND not_for_release = 0 "
            "AND work_in_progress = 0 AND title IS NOT NULL")
    par_type = [dict(r) for r in con.execute(
        f"SELECT mission_type type, COUNT(DISTINCT title) n {base} "
        "AND mission_type IS NOT NULL GROUP BY 1 ORDER BY n DESC",
        (systeme,))]
    par_donneur = [dict(r) for r in con.execute(
        f"SELECT mission_giver org, COUNT(DISTINCT title) n {base} "
        "AND mission_giver IS NOT NULL GROUP BY 1 ORDER BY n DESC",
        (systeme,))]
    total = con.execute(f"SELECT COUNT(DISTINCT title) {base}",
                        (systeme,)).fetchone()[0]
    if not total:
        raise NotFound(query)
    return {"systeme": systeme, "volet": volet, "total": total,
            "par_type": par_type, "par_donneur": par_donneur,
            "resolution": None}


def detect_site(con: sqlite3.Connection, question: str,
                exclus: set[str] | None = None) -> dict[str, Any] | None:
    """Le complexe nommé dans une question de missions — « à Onyx », « dans
    les complexes ASD ».

    Deux voies, celles que la base porte réellement :

    - `mission_sites` : le nom du site (« Onyx Facility ») contient le mot —
      c'est la famille de clés `labels.json` qui relie salles et site ;
    - un **segment exact** du nom de debug : « ASD » est un segment de
      `Redwind_ASD_…`, et les titres Eckhart écrivent « PURGE ASD SERVERS ».
      L'exigence du segment exact évite qu'un mot courant serve de site ; le
      plafond écarte les segments d'infrastructure (« Delivery » en compte
      259, un site réel se compte en dizaines au plus).
    """
    from .normalize import MOTS_GRAMMATICAUX, normalize

    deja = exclus or set()
    sites = [dict(r) for r in con.execute(
        "SELECT famille, nom FROM mission_sites WHERE est_salle = 0")]
    for mot in normalize(question or "").split():
        if mot in MOTS_GRAMMATICAUX or mot in deja or len(mot) < 3:
            continue
        familles = sorted({s["famille"] for s in sites
                           if mot in normalize(s["nom"]).split()})
        if familles:
            noms = sorted({s["nom"] for s in sites
                           if mot in normalize(s["nom"]).split()})
            return {"terme": mot, "familles": familles, "nom": noms[0]}
        n = con.execute(
            "SELECT COUNT(*) FROM contracts WHERE "
            "INSTR(UPPER('_' || debug_name || '_'), ?) > 0",
            (f"_{mot.upper()}_",)).fetchone()[0]
        if 0 < n <= 100:
            return {"terme": mot, "familles": [], "nom": mot.upper()}
    return None


def missions_du_site(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Quelles missions se passent à Onyx / dans les complexes ASD ? »

    La liste, pas un classement : mesuré, **aucune** des 20 missions du
    complexe ne porte de montant fixe — toutes à récompense calculée — donc
    `missions_payantes` ne peut rien en classer. Le compte se fait sur le
    titre affiché, comme partout.
    """
    site = detect_site(con, query)
    if site is None:
        raise NotFound(f"je ne connais pas de site « {query} »")

    conds, args = [], []
    for famille in site["familles"]:
        conds.append("c.debug_name LIKE '%' || ? || '%'")
        args.append(famille)
    # Le terme lui-même, dans le debug et dans le titre : c'est ce qui ramène
    # « BHG_ASDFacilityDelving » et « PURGE ASD SERVERS », que le segment
    # strict et la famille ratent chacun de leur côté.
    conds.append("c.debug_name LIKE '%' || ? || '%'")
    args.append(site["terme"])
    conds.append("c.title LIKE '%' || ? || '%'")
    args.append(site["terme"])

    lignes = [dict(r) for r in con.execute(
        "SELECT c.title, c.mission_giver, c.system, c.reward_uec, "
        "       c.reward_calculated "
        "  FROM contracts c "
        " WHERE c.not_for_release = 0 AND c.work_in_progress = 0 "
        "   AND c.title IS NOT NULL AND (" + " OR ".join(conds) + ")",
        args)]
    if not lignes:
        raise NotFound(f"je n'ai aucune mission pour « {site['nom']} »")

    # Dédoublonnage sur le titre affiché — douze UUID pour huit missions
    # distinguables, le joueur compte ce qu'il lit.
    vues: dict[str, dict[str, Any]] = {}
    for ligne in lignes:
        vues.setdefault(ligne["title"], ligne)
    missions = sorted(vues.values(),
                      key=lambda m: (m["mission_giver"] or "", m["title"]))
    return {"site": site["nom"], "terme": site["terme"],
            "missions": missions,
            "payes_fixes": sum(1 for m in missions if m["reward_uec"]),
            "calculees": sum(1 for m in missions if m["reward_calculated"]),
            "resolution": None}


def _activity(family: str | None) -> str | None:
    if not family:
        return None
    for token, label in _ACTIVITIES.items():
        if token.lower() in family.replace("_", "").lower():
            return label
    return None


def get_mission_reputation(con: sqlite3.Connection, query: str,
                           *, include_unreleased: bool = False,
                           volet: str | None = None) -> dict[str, Any]:
    """« Il faut quelle réputation pour cette mission ? »

    Mesuré : seuls ~8 % des contrats portent un `ReputationPrerequisite`.
    « Aucun prérequis » est donc une réponse fréquente et correcte, pas un
    échec. La donnée riche est l'inverse — ce que la mission rapporte.
    """
    res = resolve(con, query, entity_types=("contract",), limit=12)
    if not res.best:
        raise NotFound(query, res)

    candidates = [c for c in res.candidates if c.score >= res.best.score - 4]
    contract = None
    for cand in candidates:
        row = _row(con, "SELECT * FROM contracts WHERE uuid = ?", cand.entity_id)
        if row is None:
            continue
        if not include_unreleased and (row["not_for_release"] or row["work_in_progress"]):
            continue
        contract = dict(row)
        break
    if contract is None:
        contract = _dict(_row(con, "SELECT * FROM contracts WHERE uuid = ?",
                              res.best.entity_id))
    if contract is None:
        raise NotFound(query, res)

    reputation = [dict(r) for r in con.execute(
        "SELECT * FROM contract_reputation WHERE contract_uuid = ? "
        "ORDER BY direction, faction_name", (contract["uuid"],))]

    prerequis = [r for r in reputation if r["direction"] == "prerequisite"]
    # Le rang porté par le contrat lui-même : renseigné sur 61 % des contrats,
    # contre 6 % pour ReputationPrerequisite. Le premier passage ne lisait que
    # le second et concluait à tort que presque rien n'exigeait de réputation.
    if contract["min_standing_name"] and not prerequis:
        prerequis = [{
            "direction": "prerequisite",
            "faction_name": contract["mission_giver"] or contract["faction_name"],
            "scope": contract["reputation_scope"],
            "min_standing_name": contract["min_standing_name"],
            "min_standing_value": contract["min_standing_value"],
            "max_standing_name": contract["max_standing_name"],
            "max_standing_value": contract["max_standing_value"],
            "source": "contract",
        }]

    # Ce que la mission distribue en blueprint, par ses pools de récompense.
    # Mesuré sur « Secure Site » : **aucun** pool — c'est la progression chez
    # le commanditaire qui débloque, et le rendu doit faire le relais plutôt
    # que de laisser croire à une mission avare.
    blueprints = [r[0] for r in con.execute(
        "SELECT DISTINCT rpc.item_name FROM contract_reward_pools crp "
        "JOIN reward_pool_contents rpc ON rpc.pool_uuid = crp.pool_uuid "
        "WHERE crp.contract_uuid = ? AND rpc.item_name IS NOT NULL "
        "ORDER BY rpc.item_name", (contract["uuid"],))]
    groupe_blueprints = con.execute(
        "SELECT COUNT(DISTINCT b.output_name) FROM contracts c "
        "JOIN contract_reward_pools crp ON crp.contract_uuid = c.uuid "
        "JOIN blueprint_sources bs ON bs.pool_uuid = crp.pool_uuid "
        "JOIN blueprints b ON b.uuid = bs.blueprint_uuid "
        "WHERE c.mission_giver IS ? AND c.system IS ? AND c.not_for_release = 0",
        (contract["mission_giver"], contract["system"])).fetchone()[0]

    return {
        "contract": contract,
        "resolution": res,
        "volet": volet,
        "prerequisites": prerequis,
        "gained": [r for r in reputation if r["direction"] == "gained"],
        "blueprints": blueprints,
        "groupe_blueprints": groupe_blueprints,
        "group": _dict(_row(
            con,
            "SELECT * FROM mission_groups WHERE mission_giver IS ? AND system IS ?",
            contract["mission_giver"], contract["system"],
        )),
        "locations": [dict(r) for r in con.execute(
            "SELECT role, location_name, pool_name FROM contract_locations "
            "WHERE contract_uuid = ? AND role = 'availability' LIMIT 40",
            (contract["uuid"],))],
        **_chaine_de_missions(con, contract),
    }


def _rang_dans_la_chaine(con: sqlite3.Connection,
                         uuids: list[str]) -> dict[str, int]:
    """Combien d'étapes chacun de ces contrats a-t-il **avant lui**.

    C'est ce qui ordonne une chaîne sans rien inventer. Mesuré sur la famille
    Hockrow : le jeu matérialise la **clôture transitive** — P1M1 pointe vers
    les onze étapes suivantes, P1M2 vers les dix, et ainsi de suite. Le nombre
    de préalables d'une étape est donc exactement son rang.

    On ne s'appuie **pas** sur le nom de debug : le gabarit `_P<n>M<m>` ne
    couvre que 11 contrats sur 5 105, tous de cette même famille. Le graphe,
    lui, relie 1 473 contrats.
    """
    if not uuids:
        return {}
    return {r[0]: r[1] for r in con.execute(
        f"SELECT u.v, ("
        f"  SELECT COUNT(*) FROM contract_links l WHERE"
        f"    (l.contract_uuid = u.v AND l.kind = 'requires')"
        f" OR (l.other_uuid    = u.v AND l.kind = 'unlocks')) "
        f"FROM (SELECT ? AS v" + " UNION ALL SELECT ?" * (len(uuids) - 1) +
        ") u", uuids)}


def _chaine_de_missions(con: sqlite3.Connection,
                        contract: dict[str, Any]) -> dict[str, list[str]]:
    """La chaîne de missions : ce qui ouvre celle-ci, ce qu'elle ouvre.

    **Le lien s'écrit dans les deux sens, et il faut lire les deux.** Mesuré
    sur le blueprint du Zenith : « Jorrit Dossier: Updated Energy Anomaly
    Data » ne déclare aucun `requires`, et sept missions déclarent `unlocks`
    **vers** elle. En ne lisant que le sens sortant, la fiche annonçait
    « prérequis : aucun » alors que la mission est au bout d'une chaîne de
    sept. Remarque de l'utilisateur, et c'était un vrai trou : le sens entrant
    porte **887 contrats ciblés** contre 425 pour le sens sortant.

    Un préalable est donc : ce que ce contrat **exige**, plus ce qui le
    **débloque**. Symétriquement pour ce qu'il ouvre. Et les deux listes
    sortent **dans l'ordre de la chaîne**, par `_rang_dans_la_chaine`.

    On nomme sur le **titre affiché** : le jeu porte plusieurs contrats de même
    titre, et un renvoi vers soi n'apprend rien.
    """
    from .render import speakable_title

    soi = speakable_title(contract.get("title") or "")
    amont: dict[str, str] = {}   # titre -> uuid de la première occurrence
    aval: dict[str, str] = {}

    def retenir(titre: str | None, uuid: str | None,
                dans: dict[str, str]) -> None:
        propre = speakable_title(titre or "")
        # Les titres techniques (« PU_Bounty_PVE_… ») sont des noms de debug :
        # le contrat visé n'est pas en base sous un titre lisible.
        if not propre or propre == soi or propre.startswith(("PU_", "mg_")):
            return
        dans.setdefault(propre, uuid or "")

    # Sens sortant : ce contrat exige X, ce contrat ouvre Y.
    for ligne in con.execute(
            "SELECT l.kind, l.other_uuid uuid, "
            "       COALESCE(c.title, l.other_name) titre "
            "FROM contract_links l LEFT JOIN contracts c ON c.uuid = l.other_uuid "
            "WHERE l.contract_uuid = ?", (contract["uuid"],)):
        retenir(ligne["titre"], ligne["uuid"],
                amont if ligne["kind"] == "requires" else aval)

    # Sens entrant : X ouvre ce contrat — donc X est un préalable. Y exige ce
    # contrat — donc ce contrat ouvre Y.
    for ligne in con.execute(
            "SELECT l.kind, l.contract_uuid uuid, c.title titre "
            "FROM contract_links l JOIN contracts c ON c.uuid = l.contract_uuid "
            "WHERE l.other_uuid = ?", (contract["uuid"],)):
        retenir(ligne["titre"], ligne["uuid"],
                amont if ligne["kind"] == "unlocks" else aval)

    rangs = _rang_dans_la_chaine(
        con, [u for u in list(amont.values()) + list(aval.values()) if u])

    def ordonner(bloc: dict[str, str]) -> list[str]:
        # **Un titre cité porte les mêmes gabarits qu'un titre décrit.** Le
        # filtre posé sur les fiches ne valait pas ici, et « Elle débloque
        # <= UNINITIALIZED =>, ATLS Cool Meta » sortait au joueur — trouvé
        # par le balayage, même famille que les 397 chaînes de `decrire`.
        from .descriptions import _titre_utilisable

        # À rang inconnu (contrat absent de la base), on renvoie en fin de
        # liste plutôt que de le faire passer pour une première étape.
        return sorted((titre for titre in bloc if _titre_utilisable(titre)),
                      key=lambda titre: (rangs.get(bloc[titre], 10**6), titre))

    return {"prealables": ordonner(amont), "debloque": ordonner(aval)}


def progression_dans(con: sqlite3.Connection, query: str, *,
                     systeme: str | None = None) -> dict[str, Any]:
    """« Comment je progresse chez Foxwell ? »

    `get_mission_group` sait déjà dire qu'une organisation compte dix paliers
    et débloque quarante blueprints. C'est un **agrégat** : il ne dit pas à
    quel rang chaque chose s'ouvre, donc il ne répond pas à la seule question
    que le joueur se pose — « qu'est-ce qu'il me faut pour avoir ça ».

    L'échelle se lit dans `min_standing_value`, qui ordonne les rangs. Les
    blueprints remontent par les pools de récompense, et se rattachent au rang
    **le plus bas** qui les distribue : c'est le seuil à atteindre, pas la
    liste des missions qui les donnent.
    """
    res = resolve(con, query, entity_types=("org",))
    if not res.best:
        raise NotFound(query, res)
    org = res.best.name

    conditions = ["c.mission_giver = ?", "c.not_for_release = 0",
                  "c.work_in_progress = 0", "c.min_standing_name IS NOT NULL"]
    args: list[Any] = [org]
    if systeme:
        conditions.append("c.system = ?")
        args.append(systeme)
    filtre = " AND ".join(conditions)

    paliers: dict[str, dict[str, Any]] = {}
    for ligne in con.execute(
        f"SELECT c.min_standing_name rang, c.min_standing_value valeur, "
        f"       COUNT(DISTINCT c.uuid) missions, c.system "
        f"FROM contracts c WHERE {filtre} "
        f"GROUP BY c.min_standing_name, c.min_standing_value "
        f"ORDER BY c.min_standing_value", args,
    ):
        paliers[ligne["rang"]] = {
            "rang": ligne["rang"], "valeur": ligne["valeur"],
            "missions": ligne["missions"], "blueprints": [], "contrats": [],
        }

    if not paliers:
        raise NotFound(query, res)

    # **« Par quoi je monte » manquait, et c'est la question posée.**
    # L'échelle disait « 20 missions » sans en nommer une seule : le joueur
    # savait qu'un palier existe, pas comment le franchir. Les guides
    # communautaires, eux, nomment les contrats — c'est ce que
    # l'utilisateur a signalé absent le 2026-08-20.
    #
    # Le titre est un **gabarit** une fois sur cinq (« [Contractor|…] ») :
    # `speakable_title` les élague, et un titre qui n'est le nom de rien
    # est écarté plutôt que rendu tel quel.
    from .render import speakable_title

    for ligne in con.execute(
        f"SELECT c.min_standing_name rang, c.title, COUNT(*) n "
        f"FROM contracts c WHERE {filtre} AND c.title IS NOT NULL "
        f"GROUP BY c.min_standing_name, c.title ORDER BY n DESC", args,
    ):
        palier = paliers.get(ligne["rang"])
        if palier is None or len(palier["contrats"]) >= 4:
            continue
        titre = speakable_title(ligne["title"] or "")
        if titre and titre not in palier["contrats"]:
            palier["contrats"].append(titre)

    # **Certains contrats payent en vaisseau, et le briefing le dit.**
    # Le site communautaire en fait son titre — « 10,8k de réputation pour
    # le Drake Golem » — et l'information est chez nous depuis toujours,
    # dans une ligne `PAYMENT:` du texte du contrat. Mesuré : 27 contrats
    # sur 4 149 en portent une, et ce sont exactement ceux qui payent en
    # nature.
    #
    # **Le nom se valide contre le catalogue, il ne se devine pas.** La
    # ligne est de la prose libre (« Polished up a special Drake Golem
    # from Teach's ») : n'en retenir que ce que `ships` connaît évite
    # d'annoncer un vaisseau qui n'existe pas, ce que le §7 interdit.
    flotte = [(r["name"], r["name"].casefold())
              for r in con.execute("SELECT name FROM ships WHERE name IS NOT NULL")]
    for ligne in con.execute(
        f"SELECT c.min_standing_name rang, c.title, c.description "
        f"FROM contracts c WHERE {filtre} AND c.description LIKE '%PAYMENT:%'",
        args,
    ):
        palier = paliers.get(ligne["rang"])
        if palier is None:
            continue
        paiement = (ligne["description"] or "").split("PAYMENT:", 1)[-1]
        paiement = paiement.split("AUTHORIZATION")[0].casefold()
        for nom, plie in flotte:
            if plie in paiement:
                palier.setdefault("payent_un_vaisseau", []).append(
                    {"contrat": speakable_title(ligne["title"] or ""),
                     "vaisseau": nom})
                break

    # Le rang **le plus bas** qui donne un blueprint : c'est le seuil, et
    # l'annoncer au rang le plus haut ferait croire à une exigence qui
    # n'existe pas.
    for ligne in con.execute(
        f"SELECT b.output_name nom, MIN(c.min_standing_value) seuil "
        f"FROM contracts c "
        f"JOIN contract_reward_pools crp ON crp.contract_uuid = c.uuid "
        f"JOIN blueprint_sources bs ON bs.pool_uuid = crp.pool_uuid "
        f"JOIN blueprints b ON b.uuid = bs.blueprint_uuid "
        f"WHERE {filtre} GROUP BY b.output_name ORDER BY seuil, b.output_name",
        args,
    ):
        for palier in paliers.values():
            if palier["valeur"] == ligne["seuil"]:
                palier["blueprints"].append(ligne["nom"])
                break

    echelle = sorted(paliers.values(), key=lambda p: p["valeur"])
    return {
        "resolution": res, "org": org, "systeme": systeme,
        "echelle": echelle,
        "total_missions": sum(p["missions"] for p in echelle),
        "total_blueprints": sum(len(p["blueprints"]) for p in echelle),
    }
