"""La fabrication — recettes, séries de blueprints, plans complets.

Découpé de `queries.py` le 2026-08-07, mécaniquement — même règle que les
autres familles : l'ordre du fichier d'origine, la façade en ré-export, les
imports différés à travers les familles.
"""

from __future__ import annotations

from collections import deque
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .commerce import acheter_ou_fabriquer
from .missions import group_missions
from .normalize import normalize
from .raffinage import conseil_de_raffinage
from .resultats import fraicheur_jeu, qualite_reponse
from .resolver import resolve


def extraire_rang_actuel(con: sqlite3.Connection, question: str) -> str | None:
    """Retrouve un libellé de rang publié dans la question du joueur.

    Le rang n'est pas une entité du résolveur : c'est une borne d'une échelle
    de réputation. On lit donc le vocabulaire dans les contrats eux-mêmes. Les
    formes longues gagnent — « Jr. Contractor » avant « Contractor » — pour
    ne jamais transformer un rang précis en rang générique.
    """
    texte = f" {normalize(question)} "
    candidats: list[tuple[int, int, str]] = []
    for ligne in con.execute(
        "SELECT DISTINCT min_standing_name nom FROM contracts "
        "WHERE TRIM(COALESCE(min_standing_name, '')) <> '' "
        "UNION SELECT DISTINCT max_standing_name FROM contracts "
        "WHERE TRIM(COALESCE(max_standing_name, '')) <> ''"
    ):
        nom = ligne[0]
        forme = normalize(nom)
        formes = {forme}
        if forme.startswith("jr "):
            formes.add("junior " + forme[3:])
        if forme.startswith("sr "):
            formes.add("senior " + forme[3:])
        for variante in formes:
            if f" {variante} " in texte:
                candidats.append((len(variante.split()), len(variante), nom))
    return max(candidats)[2] if candidats else None


def _echelle_de_reputation(con: sqlite3.Connection, faction_uuid: str,
                           scope: str) -> list[dict[str, Any]]:
    """Les seuils de rang d'une faction et d'un axe, sans copie codée en dur."""
    lignes = con.execute(
        "SELECT nom, MIN(valeur) valeur FROM ("
        " SELECT min_standing_name nom, min_standing_value valeur "
        " FROM contracts WHERE faction_uuid = ? AND reputation_scope = ? "
        "   AND not_for_release = 0 AND work_in_progress = 0 "
        " UNION ALL "
        " SELECT max_standing_name, max_standing_value FROM contracts "
        " WHERE faction_uuid = ? AND reputation_scope = ? "
        "   AND not_for_release = 0 AND work_in_progress = 0"
        ") WHERE nom IS NOT NULL AND valeur IS NOT NULL "
        "GROUP BY nom ORDER BY valeur",
        (faction_uuid, scope, faction_uuid, scope),
    ).fetchall()
    return [dict(r) for r in lignes]


def _missions_de_grind(con: sqlite3.Connection, faction_uuid: str,
                       scope: str, echelle: list[dict[str, Any]]
                       ) -> list[dict[str, Any]]:
    """Missions répétables qui font réellement monter l'axe demandé."""
    from .render import speakable_title

    valeurs = sorted({r["valeur"] for r in echelle})
    plancher = valeurs[0] if valeurs else 0
    missions = []
    for ligne in con.execute(
        "SELECT c.uuid, c.title, c.mission_giver, c.system, "
        "       c.min_standing_name, c.min_standing_value, "
        "       c.max_standing_name, c.max_standing_value, MAX(r.amount) gain "
        "FROM contracts c JOIN contract_reputation r ON r.contract_uuid = c.uuid "
        "WHERE c.faction_uuid = ? AND c.reputation_scope = ? "
        "  AND c.not_for_release = 0 AND c.work_in_progress = 0 "
        "  AND c.once_only = 0 AND c.title IS NOT NULL "
        "  AND r.direction = 'gained' AND r.faction_uuid = ? "
        "  AND r.scope = ? AND r.amount > 0 "
        "GROUP BY c.uuid ORDER BY gain DESC, c.title",
        (faction_uuid, scope, faction_uuid, scope),
    ):
        mission = dict(ligne)
        titre = speakable_title(mission.get("title"))
        if not titre:
            continue
        mission["title"] = titre
        mission["seuil"] = (mission.get("min_standing_value")
                             if mission.get("min_standing_value") is not None
                             else plancher)
        # `max_standing_value` est le seuil du **dernier rang autorisé**, pas
        # le dernier point autorisé. « Sr. Contractor / 5 800 » reste valable
        # dans tout le palier 5 800–14 999 : la borne exclusive est donc le
        # seuil suivant de l'échelle, pas 5 800 lui-même.
        maximum = mission.get("max_standing_value")
        mission["plafond_exclusif"] = (
            next((v for v in valeurs if maximum is not None and v > maximum), None)
            if maximum is not None else None)
        missions.append(mission)
    return missions


def _simuler_grind(depart: int, cible: int,
                    missions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Plus court chemin exact, uniquement avec des missions répétables.

    Un choix glouton du plus gros gain est faux aux bornes : prendre 49 points
    peut laisser une mission à +60 ouverte une dernière fois, quand prendre
    directement +60 la fermerait. Chaque score atteignable est donc un état,
    chaque mission une arête de coût 1 ; une recherche en largeur garantit le
    premier chemin qui atteint la cible.
    """
    if depart >= cible:
        return {"missions": 0, "etapes": [], "score_final": depart}

    # Des dizaines d'UUID portent parfois exactement le même gain sur la même
    # plage. Ils sont interchangeables pour le compte : garder le titre le
    # plus court évite jusqu'à 416 arêtes identiques par score chez Covalex.
    uniques: dict[tuple[int, int | None, int], dict[str, Any]] = {}
    for mission in missions:
        cle = (mission["seuil"], mission["plafond_exclusif"], mission["gain"])
        precedente = uniques.get(cle)
        if precedente is None or (len(mission["title"]), mission["title"]) < (
                len(precedente["title"]), precedente["title"]):
            uniques[cle] = mission
    transitions = sorted(
        uniques.values(), key=lambda m: (-m["gain"], m["title"], m["uuid"]))

    file = deque([depart])
    parents: dict[int, tuple[int, dict[str, Any]] | None] = {depart: None}
    arrivee = None
    while file and arrivee is None:
        score = file.popleft()
        for mission in transitions:
            if mission["seuil"] > score:
                continue
            if (mission["plafond_exclusif"] is not None
                    and score >= mission["plafond_exclusif"]):
                continue
            suivant = min(cible, score + mission["gain"])
            if suivant in parents:
                continue
            parents[suivant] = (score, mission)
            if suivant == cible:
                arrivee = suivant
                break
            file.append(suivant)
        # La base courante n'a jamais plus de 48 001 scores utiles sur un axe.
        # Une explosion au-delà du million signale une nouvelle granularité ;
        # on passe alors la main plutôt que de monopoliser le bot.
        if len(parents) > 1_000_000:
            return None
    if arrivee is None:
        return None

    sequence: list[tuple[int, int, dict[str, Any]]] = []
    score = arrivee
    while score != depart:
        parent = parents[score]
        if parent is None:
            return None
        avant, mission = parent
        sequence.append((avant, score, mission))
        score = avant
    sequence.reverse()

    etapes: list[dict[str, Any]] = []
    for avant, apres, mission in sequence:
        if etapes and etapes[-1]["uuid"] == mission["uuid"]:
            etapes[-1]["repetitions"] += 1
            etapes[-1]["arrivee"] = apres
        else:
            etapes.append({
                "uuid": mission["uuid"], "titre": mission["title"],
                "gain": mission["gain"], "repetitions": 1,
                "depart": avant, "arrivee": apres,
                "systeme": mission.get("system"),
            })
    return {"missions": len(sequence), "etapes": etapes,
            "score_final": arrivee}


def _grind_du_blueprint(con: sqlite3.Connection, bp: dict[str, Any],
                        sources: list[dict[str, Any]],
                        rang_actuel: str | None) -> dict[str, Any]:
    """Bornes de missions depuis un rang déclaré jusqu'à une mission source."""
    if bp.get("available_by_default"):
        return {"besoin_rang": False, "rang_actuel": rang_actuel,
                "rangs_possibles": [], "plans": [],
                "meilleur": {"disponible_par_defaut": True}}

    groupes: dict[tuple[str | None, str | None, str | None], dict[str, Any]] = {}
    for mission in sources:
        faction_uuid = mission.get("faction_uuid")
        scope = mission.get("reputation_scope")
        cle = (faction_uuid, scope,
               None if faction_uuid and scope else mission.get("mission_giver"))
        groupe = groupes.setdefault(cle, {
            "faction_uuid": faction_uuid, "faction": mission.get("faction_name"),
            "scope": scope, "orgs": set(), "systemes": set(), "sources": [],
        })
        if mission.get("mission_giver"):
            groupe["orgs"].add(mission["mission_giver"])
        if mission.get("system"):
            groupe["systemes"].add(mission["system"])
        groupe["sources"].append(mission)

    plans = []
    rangs_possibles: dict[str, int] = {}
    for groupe in groupes.values():
        lignes = groupe["sources"]
        sans_prerequis = any(m.get("min_standing_value") is None for m in lignes)
        seuils = [m["min_standing_value"] for m in lignes
                  if m.get("min_standing_value") is not None]
        cible = None if sans_prerequis else (min(seuils) if seuils else None)
        finales = [m for m in lignes
                   if (sans_prerequis and m.get("min_standing_value") is None)
                   or (not sans_prerequis and m.get("min_standing_value") == cible)]
        finales.sort(key=lambda m: (-(m.get("chance") or 0),
                                    m.get("title") or ""))
        finale = finales[0] if finales else None
        plan: dict[str, Any] = {
            "faction": groupe.get("faction"),
            "orgs": sorted(groupe["orgs"]),
            "scope": groupe.get("scope"),
            "systemes": sorted(groupe["systemes"]),
            "cible": cible,
            "rang_cible": (finale or {}).get("min_standing_name"),
            "mission_finale": (finale or {}).get("title"),
            "chance": (finale or {}).get("chance"),
            "missions_min": None, "missions_max": None,
            "total_min": None, "total_max": None, "etapes": [],
        }
        if sans_prerequis or not groupe.get("faction_uuid") or not groupe.get("scope"):
            plan["deja_accessible"] = True
            plan["missions_min"] = plan["missions_max"] = 0
            if plan["chance"] == 1:
                plan["total_min"] = plan["total_max"] = 1
            plans.append(plan)
            continue

        echelle = _echelle_de_reputation(
            con, groupe["faction_uuid"], groupe["scope"])
        for rang in echelle:
            rangs_possibles[rang["nom"]] = min(
                rang["valeur"], rangs_possibles.get(rang["nom"], rang["valeur"]))
        plan["rangs"] = echelle
        # Le premier rang de l'échelle est déjà acquis : il ne nécessite pas
        # de connaître la progression exacte du joueur.
        if echelle and cible is not None and cible <= echelle[0]["valeur"]:
            plan["deja_accessible"] = True
            plan["missions_min"] = plan["missions_max"] = 0
            if plan["chance"] == 1:
                plan["total_min"] = plan["total_max"] = 1
            plans.append(plan)
            continue
        if rang_actuel is None:
            plans.append(plan)
            continue

        actuel = next((r for r in echelle
                       if normalize(r["nom"]) == normalize(rang_actuel)), None)
        if actuel is None:
            plan["raison"] = "rang absent de cette échelle"
            plans.append(plan)
            continue
        plan["rang_actuel"] = actuel["nom"]
        plan["score_min"] = actuel["valeur"]
        suivant = next((r["valeur"] for r in echelle
                        if r["valeur"] > actuel["valeur"]), None)
        plan["score_max"] = ((suivant - 1) if suivant is not None
                             else actuel["valeur"])
        if cible is None or actuel["valeur"] >= cible:
            plan["deja_accessible"] = True
            plan["missions_min"] = plan["missions_max"] = 0
            if plan["chance"] == 1:
                plan["total_min"] = plan["total_max"] = 1
            plans.append(plan)
            continue

        missions = _missions_de_grind(
            con, groupe["faction_uuid"], groupe["scope"], echelle)
        pire = _simuler_grind(actuel["valeur"], cible, missions)
        meilleur_depart = min(cible - 1, plan["score_max"])
        mieux = _simuler_grind(meilleur_depart, cible, missions)
        if pire is None or mieux is None:
            plan["raison"] = "aucune chaîne répétable complète dans la base"
            plans.append(plan)
            continue
        plan["missions_min"] = mieux["missions"]
        plan["missions_max"] = pire["missions"]
        plan["etapes"] = pire["etapes"]
        if plan["chance"] == 1:
            plan["total_min"] = plan["missions_min"] + 1
            plan["total_max"] = plan["missions_max"] + 1
        plans.append(plan)

    calculables = [p for p in plans if p.get("missions_max") is not None]
    calculables.sort(key=lambda p: (p["missions_max"], p.get("chance") != 1,
                                    p.get("faction") or ""))
    # Sans voie publiée, aucun rang ne rendra le calcul possible : on annonce
    # l'absence de mission au lieu de demander une information inutile.
    besoin_rang = (rang_actuel is None and bool(plans)
                   and bool(rangs_possibles) and not calculables)
    return {
        "besoin_rang": besoin_rang,
        "rang_actuel": rang_actuel,
        "rang_inconnu": rang_actuel is not None and not calculables,
        "rangs_possibles": [nom for nom, _ in sorted(
            rangs_possibles.items(), key=lambda item: (item[1], item[0]))],
        "plans": plans,
        "meilleur": calculables[0] if calculables else None,
        "hypothese": "plus court chemin parmi les gains répétables publiés",
    }


def get_blueprint(con: sqlite3.Connection, query: str,
                  *, volet: str = "recette",
                  rang_actuel: str | None = None) -> dict[str, Any]:
    """« Comment on fabrique un P6-LR ? »

    Répond aussi à « où je choppe le blueprint » via les pools de récompense —
    dont les clés restent techniques faute de résolution en amont.
    """
    res = resolve(con, query, entity_types=("blueprint",))
    if not res.best:
        raise NotFound(query, res)

    bp = _dict(_row(con, "SELECT * FROM blueprints WHERE uuid = ?", res.best.entity_id))
    if bp is None:
        raise NotFound(query, res)

    tiers = []
    for tier in con.execute(
        "SELECT * FROM blueprint_tiers WHERE blueprint_uuid = ? ORDER BY tier_index",
        (bp["uuid"],),
    ):
        ingredients = [
            dict(r) for r in con.execute(
                "SELECT * FROM blueprint_ingredients WHERE tier_id = ? ORDER BY position",
                (tier["id"],),
            )
        ]
        groups: dict[str, dict[str, Any]] = {}
        for ing in ingredients:
            label = ing["group_name"] or ing["group_key"] or "—"
            groups.setdefault(label, {"name": label,
                                      "required_count": ing["required_count"],
                                      "options": []})["options"].append(ing)
        tiers.append({**dict(tier), "groups": list(groups.values())})

    # « Où je choppe le blueprint ? » — on remonte du pool aux missions qui le
    # distribuent. Les contrats non sortis sont exclus : annoncer une mission
    # qui n'existe pas dans le jeu est pire que ne rien annoncer.
    sources = []
    for src in con.execute(
        "SELECT * FROM blueprint_sources WHERE blueprint_uuid = ?", (bp["uuid"],)
    ):
        missions = [dict(r) for r in con.execute(
            "SELECT DISTINCT c.uuid, c.title, c.mission_type, c.mission_giver, "
            "       c.system, c.family, c.faction_uuid, c.faction_name, "
            "       c.reputation_scope, c.min_standing_name, "
            "       c.min_standing_value, c.max_standing_name, "
            "       c.max_standing_value, c.rank_index, c.once_only, crp.chance "
            "FROM contract_reward_pools crp "
            "JOIN contracts c ON c.uuid = crp.contract_uuid "
            "WHERE crp.pool_uuid = ? AND c.title IS NOT NULL "
            "  AND c.not_for_release = 0 AND c.work_in_progress = 0 "
            "ORDER BY c.min_standing_value, c.title",
            (src["pool_uuid"],),
        )]
        sources.append({**dict(src), "missions": missions})

    toutes = [m for s in sources for m in s["missions"]]
    # Le jeu porte plusieurs contrats de titre et de rang identiques — douze
    # UUID pour huit missions distinguables. Un joueur ne peut pas les
    # différencier : il compte ce qu'il lit. Annoncer douze en listant huit
    # était une incohérence visible, relevée par l'utilisateur. On compte donc
    # sur le **titre affiché**, celui d'où les jetons de gabarit sont retirés,
    # pour que le nombre et la liste soient forcément d'accord.
    from .render import speakable_title

    distinctes = {(speakable_title(m.get("title")), m.get("min_standing_name"))
                  for m in toutes if m.get("title")}
    resultat = {
        "blueprint": bp,
        "resolution": res,
        "volet": volet,
        "tiers": tiers,
        "sources": sources,
        "mission_count": len(distinctes),
        # Le compte brut reste disponible pour le diagnostic : l'écart entre
        # les deux dit combien de doublons porte la source amont.
        "contrats_bruts": len(toutes),
        "mission_groups": group_missions(con, toutes),
        "dismantle": [dict(r) for r in con.execute(
            "SELECT * FROM blueprint_dismantle_returns WHERE blueprint_uuid = ?",
            (bp["uuid"],))],
    }
    if volet == "grind":
        grind = _grind_du_blueprint(
            con, bp, toutes, rang_actuel)
        resultat["grind"] = grind
        # Un rang manquant ou invalide appelle le joueur : aucun LLM ne peut
        # connaître sa progression. En revanche, un rang valide sans chaîne
        # répétable est un vrai trou de jointure ; il suit le contrat commun
        # d'incomplétude et passe à l'analyste s'il est activé.
        manque_chaine = bool(
            rang_actuel is not None and grind.get("meilleur") is None
            and not grind.get("rang_inconnu"))
        manques = (["chaîne répétable de réputation publiée"]
                   if manque_chaine else [])
        qualite = qualite_reponse(
            faits={
                "blueprint": bp["output_name"],
                "missions_sources": len(toutes),
                "rang_actuel": rang_actuel,
                "calcul_borne": grind.get("meilleur") is not None,
                "disponible_par_defaut": bool(bp.get("available_by_default")),
            },
            manques=manques, sources=("jeu",),
            fraicheur={"jeu": fraicheur_jeu(con)},
            # Un rang absent/invalide est une entrée à demander au joueur,
            # pas une lacune qu'un LLM aurait le droit de deviner.
            complet=not manque_chaine)
        resultat["qualite_reponse"] = qualite
        resultat["complet"] = qualite["complet"]
        if manques:
            resultat["manques"] = manques
    return resultat


def reponse_vide(tool: str, data: dict[str, Any]) -> bool:
    """La réponse est-elle une absence plutôt qu'un contenu ?

    Sert à trancher entre « il y a plusieurs réponses possibles, laquelle ? »
    et « il y en a une, et les autres variantes n'ont rien ». Le second cas ne
    mérite pas de question : le P6-LR et son chargeur sortent des mêmes douze
    missions, les trois déclinaisons cosmétiques n'en ont aucune. Demander
    entre « une réponse » et « pas de réponse » n'a pas de sens — on répond et
    on signale.
    """
    if tool == "blueprints_de_la_meme_serie":
        return not data.get("missions")
    if tool == "get_blueprint":
        return not data.get("tiers")
    if tool == "get_price":
        return not (data.get("offers") or data.get("craft") or data.get("loot")
                    or data.get("exclusive"))
    if tool == "get_item_stats":
        item = data.get("item") or {}
        return not any(item.get(c) for c in
                       ("dps", "ammo_capacity", "shield_health", "qt_jump_range"))
    return False


def blueprints_de_la_meme_serie(con: sqlite3.Connection, query: str,
                                *, limit: int = 30) -> dict[str, Any]:
    """« Combien de blueprints sortent des mêmes missions que le P6-LR ? »

    La question vaut mieux que sa formulation le laisse croire : elle demande
    ce que la série débloque **en plus** de ce qu'on visait. Un joueur qui
    farme seize missions pour un fusil a intérêt à savoir qu'il en sort aussi
    l'armure complète.

    Le chemin est déjà en base et personne ne le parcourait : blueprint →
    pools de récompense → contrats → et retour vers **tous** les blueprints
    que ces contrats distribuent.

    On distingue deux voisinages, parce qu'ils ne se valent pas : ceux qui
    partagent **toutes** les missions de la cible tombent forcément avec elle ;
    ceux qui n'en partagent qu'une partie demandent d'aller plus loin.
    """
    res = resolve(con, query, entity_types=("blueprint",))
    if res.best is None:
        raise NotFound(query, res)
    cible = _dict(_row(con, "SELECT * FROM blueprints WHERE uuid = ?",
                       res.best.entity_id))
    if cible is None:
        raise NotFound(query, res)

    # Les contrats sortis qui distribuent la cible. Les non publiés sont
    # exclus ici aussi : compter une mission qui n'existe pas fausserait le
    # « toutes les missions » du voisinage.
    from .render import speakable_title

    lignes = con.execute(
        "SELECT DISTINCT crp.contract_uuid, c.title, c.min_standing_name "
        "FROM blueprint_sources bs "
        "JOIN contract_reward_pools crp ON crp.pool_uuid = bs.pool_uuid "
        "JOIN contracts c ON c.uuid = crp.contract_uuid "
        "WHERE bs.blueprint_uuid = ? AND c.not_for_release = 0 "
        "  AND c.work_in_progress = 0 AND c.title IS NOT NULL",
        (cible["uuid"],)).fetchall()
    if not lignes:
        return {"blueprint": cible, "resolution": res, "missions": 0,
                "contrats_bruts": 0, "voisins": [], "total": 0}

    # Le jeu porte plusieurs contrats de titre et de rang identiques : douze
    # UUID pour **sept** missions que le joueur distingue. Il compte ce qu'il
    # lit, donc on compte comme lui — la même règle que `get_blueprint`, que
    # ce module avait oubliée en repartant des UUID bruts.
    cle = {r["contract_uuid"]: (speakable_title(r["title"]),
                                r["min_standing_name"]) for r in lignes}
    missions = set(cle.values())
    contrats = list(cle)

    marques = ",".join("?" * len(contrats))
    brut = con.execute(
        f"SELECT b.uuid, b.output_name, crp.contract_uuid "
        f"FROM contract_reward_pools crp "
        f"JOIN blueprint_sources bs ON bs.pool_uuid = crp.pool_uuid "
        f"JOIN blueprints b ON b.uuid = bs.blueprint_uuid "
        f"WHERE crp.contract_uuid IN ({marques}) AND b.uuid != ?",
        (*contrats, cible["uuid"])).fetchall()

    # Le partage se compte lui aussi en missions, pas en contrats : annoncer
    # « 16 missions communes sur 12 » n'a aucun sens.
    par_blueprint: dict[str, dict[str, Any]] = {}
    for ligne in brut:
        entree = par_blueprint.setdefault(
            ligne["uuid"], {"uuid": ligne["uuid"],
                            "output_name": ligne["output_name"], "cles": set()})
        entree["cles"].add(cle[ligne["contract_uuid"]])

    voisins = []
    for entree in par_blueprint.values():
        communs = len(entree["cles"])
        voisins.append({"uuid": entree["uuid"],
                        "output_name": entree["output_name"],
                        "communs": communs,
                        "toutes": communs >= len(missions)})
    voisins.sort(key=lambda v: (-v["communs"], v["output_name"]))

    return {
        "blueprint": cible,
        "resolution": res,
        "missions": len(missions),
        # Le compte brut reste disponible : l'écart avec le précédent mesure
        # les doublons de la source amont.
        "contrats_bruts": len(contrats),
        "voisins": voisins[:limit],
        "total": len(voisins),
    }


def plan_de_fabrication(con: sqlite3.Connection, query: str,
                        *, qualite: float | None = None) -> dict[str, Any]:
    """« Comment je fabrique un P8-AR, de A à Z ? »

    Quatre questions que le joueur pose l'une après l'autre, et qui n'ont
    jamais de raison d'être séparées : ce qu'il faut, où le miner, où le
    raffiner, et si ça vaut le coup. Chaque moitié existait déjà — la recette,
    les gisements, le conseil de raffinage, la comparaison des coûts — et
    l'enchaînement se faisait à la main, un tour de conversation par étape.

    **La résolution se fait une seule fois.** Chaque outil appelé refait
    normalement la sienne, et rien ne garantit qu'ils tombent sur le même
    objet : « Omnisky » sort six modèles. On résout donc le blueprint, puis on
    passe son **nom de sortie** aux autres, qui retombent forcément dessus.

    Ce qui manque est **dit** plutôt que comblé : un minerai sans gisement
    connu, une recette sans prix, une étape de raffinage sans rendement relevé.
    Un plan qui prétend être complet là où il ne l'est pas envoie le joueur
    dans le vide.
    """
    recette = get_blueprint(con, query)
    nom = recette["blueprint"]["output_name"]

    # Le conseil de raffinage sait déjà lire les minerais d'une recette, leur
    # rareté, et lequel commande. Inutile de refaire ce calcul ici.
    try:
        raffinage = conseil_de_raffinage(con, nom)
    except NotFound:
        raffinage = None

    try:
        couts = acheter_ou_fabriquer(con, nom)
    except NotFound:
        couts = None

    # La qualité demandée appartient au même plan : renvoyer seulement la
    # recette puis demander un second tour pour l'effet cassait précisément
    # la composition « de A à Z ». La fiche garde ses garanties propres :
    # effets par composant, plages publiées, aucun cumul présenté comme exact.
    effet_qualite = None
    if qualite is not None:
        from .qualite import fiche_qualite
        try:
            effet_qualite = fiche_qualite(con, nom, qualite, query)
        except NotFound:
            pass

    # Où miner chaque minerai. On garde le lien minerai → systèmes plutôt
    # qu'une liste à plat : le joueur planifie un trajet, pas une liste
    # d'endroits sans propriétaire.
    gisements = []
    for minerai in (raffinage or {}).get("minerais") or []:
        gisements.append({
            "nom": minerai["nom"],
            "rarete": minerai["rarete"],
            "systemes": minerai["systemes"],
            "total": minerai["gisements"],
        })

    # **Un ingrédient qui n'est pas un minerai disparaîtrait de l'étape
    # « où l'extraire ».** Le P6-LR demande de l'Hadanite en **unités** : c'est
    # un objet, pas un gisement, et le conseil de raffinage ne le connaît donc
    # pas. Sans cette liste, il figurait dans la recette et nulle part
    # ailleurs — un plan qui laisse un matériau sans provenance.
    connus = {g["nom"] for g in gisements}
    hors_minerai = [
        ing["ref_name"]
        for tier in recette.get("tiers") or []
        for groupe in tier.get("groups") or []
        for ing in groupe.get("options") or []
        if ing["ref_name"] not in connus
    ]

    sans_gisement = [g["nom"] for g in gisements if not g["systemes"]]
    hors_minerai = list(dict.fromkeys(hors_minerai))
    manques = []
    if raffinage is None:
        manques.append("conseil de raffinage absent")
    if couts is None:
        manques.append("comparaison des coûts absente")
    elif couts.get("qualite_reponse"):
        manques.extend(couts["qualite_reponse"].get("manques") or [])
    elif couts.get("complet") is False:
        manques.append("coût de fabrication partiel")
    manques.extend(f"gisement absent pour {nom}" for nom in sans_gisement)
    manques.extend(f"provenance non minière absente pour {nom}"
                   for nom in hors_minerai)
    if qualite is not None and effet_qualite is None:
        manques.append(f"effet publié absent à la qualité {qualite:g}")

    sources = ["jeu"]
    fraicheur = {"jeu": fraicheur_jeu(con)}
    if couts is not None:
        contrat_couts = couts.get("qualite_reponse") or {}
        if "uex" in (contrat_couts.get("sources") or []):
            sources.append("uex")
            fraicheur["uex"] = (contrat_couts.get("fraicheur") or {}).get(
                "uex", {})
    contrat = qualite_reponse(
        faits={
            "objet": nom,
            "tiers_recette": len(recette.get("tiers") or []),
            "minerais": len(gisements),
            "minerais_localises": sum(bool(g["systemes"]) for g in gisements),
            "cout_fabrication_connu": ((couts or {}).get("cout_fabrication")),
            "prix_achat": ((couts or {}).get("prix_achat")),
            "qualite_demandee": qualite,
        },
        manques=manques, sources=sources, fraicheur=fraicheur)

    return {
        "nom": nom,
        "recette": recette,
        "gisements": gisements,
        "raffinage": raffinage,
        "couts": couts,
        "qualite": qualite,
        "effet_qualite": effet_qualite,
        "complet": contrat["complet"],
        "qualite_reponse": contrat,
        "temps_fabrication": max(
            (t.get("craft_time_seconds") or 0
             for t in recette.get("tiers") or []), default=0) or None,
        # Ce qu'on ne sait pas, nommément.
        "sans_gisement": sans_gisement,
        "hors_minerai": hors_minerai,
    }
