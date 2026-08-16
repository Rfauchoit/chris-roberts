"""Les contrats : missions, objectifs, sites, groupes.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
import pathlib
import re
import sqlite3
from .source import read_json
from .socle import AliasCollector, PLACEHOLDER, parse_debug_name


def _standing(block: dict | None) -> tuple[str | None, int | None]:
    if not isinstance(block, dict):
        return None, None
    return block.get("Name"), block.get("MinReputation")


def load_contracts(con: sqlite3.Connection, root: pathlib.Path,
                   aliases: AliasCollector, labels: dict[str, str]) -> tuple[int, int]:
    """5 108 fichiers. Les contrats non sortis sont ingérés et marqués, jamais
    écartés à l'ingestion : filtrer se fait à la requête (cf. DECISIONS.md)."""
    contracts = reputation = 0
    textes = consignes_redigees(labels)
    faction_names = {
        r["uuid"]: r["name"] for r in con.execute("SELECT uuid, name FROM factions")
    }

    for path in sorted((root / "contracts").glob("*.json")):
        c = read_json(path)
        uuid = c.get("UUID")
        if not uuid:
            continue
        faction = c.get("Faction") or {}
        fuuid = faction.get("UUID")
        # 2 695 contrats n'ont pas de DisplayTitle ; 1 551 d'entre eux ont un
        # TitleKey résoluble dans labels.json. Le premier passage les perdait.
        title = c.get("DisplayTitle") or labels.get(c.get("TitleKey") or "")
        if title == PLACEHOLDER:
            title = None
        title = title or c.get("Title") if (c.get("Title") or "").strip("~") else title
        if title and title.startswith("~mission("):
            title = None
        description = c.get("DisplayDescription")
        if description and description.startswith("["):
            # « [Contractor|PrisonerBreakDescription] » : un jeton non résolu.
            tokens = c.get("MissionTokens") or {}
            candidate = tokens.get(description.strip("[]"))
            description = candidate[0] if isinstance(candidate, list) and candidate else None

        system, difficulty = parse_debug_name(c.get("DebugName"))
        # MinStanding/MaxStanding à la racine sont renseignés sur 64 % des
        # contrats, contre 6 % pour ReputationPrerequisite. Ignorer les
        # premiers faisait conclure à tort que presque aucune mission n'avait
        # d'exigence de rang.
        lo_name, lo_val = _standing(c.get("MinStanding"))
        hi_name, hi_val = _standing(c.get("MaxStanding"))
        reward = c.get("FixedReward") or {}
        crime = c.get("CrimeStat") or {}
        con.execute(
            "INSERT OR REPLACE INTO contracts "
            "(uuid, debug_name, title, description, mission_type, mission_giver, "
            " faction_uuid, faction_name, reputation_scope, family, system, "
            " difficulty_label, rank_index, min_standing_name, min_standing_value, "
            " max_standing_name, max_standing_value, reward_uec, reward_calculated, "
            " crime_min, crime_max, deadline_seconds, illegal, shareable, "
            " once_only, time_to_complete, difficulty_profile, "
            " diff_connaissance, diff_pilotage, diff_charge, diff_risque, "
            " not_for_release, work_in_progress) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "?,?,?,?,?,?)",
            (
                uuid, c.get("DebugName"), title, description,
                (c.get("MissionType") or {}).get("Name"), c.get("MissionGiver"),
                fuuid, faction.get("Name") or faction_names.get(fuuid),
                c.get("ReputationScope"),
                c.get("GeneratorClass"), system, difficulty, c.get("RankIndex"),
                lo_name if lo_name != PLACEHOLDER else None, lo_val,
                hi_name if hi_name != PLACEHOLDER else None, hi_val,
                reward.get("Amount"), int(bool(c.get("CalculatedReward"))),
                crime.get("Min"), crime.get("Max"),
                (c.get("Deadline") or {}).get("CompletionTime"),
                int(bool(c.get("Illegal"))), int(bool(c.get("Shareable"))),
                int(bool(c.get("OnceOnly"))), c.get("TimeToComplete"),
                (c.get("Difficulty") or {}).get("DifficultyProfile"),
                # Les quatre axes, tels que le jeu les écrit : leur rang est
                # dans le suffixe, aucune table de correspondance à tenir.
                (c.get("Difficulty") or {}).get("GameKnowledge"),
                (c.get("Difficulty") or {}).get("MechanicalSkill"),
                (c.get("Difficulty") or {}).get("MentalLoad"),
                (c.get("Difficulty") or {}).get("RiskOfLoss"),
                int(bool(c.get("NotForRelease"))), int(bool(c.get("WorkInProgress"))),
            ),
        )
        contracts += 1
        aliases.add_variants("contract", uuid, title, c.get("DebugName"))

        # Arborescence explicite entre missions.
        for required in c.get("RequiredMissions") or []:
            if required.get("UUID"):
                con.execute(
                    "INSERT OR IGNORE INTO contract_links "
                    "(contract_uuid, kind, other_uuid, other_name) "
                    "VALUES (?,'requires',?,?)",
                    (uuid, required["UUID"], required.get("DebugName")),
                )
        for tag in c.get("CompletionTags") or []:
            for unlocked in tag.get("UnlocksMissions") or []:
                target = unlocked if isinstance(unlocked, str) else unlocked.get("UUID")
                if target:
                    con.execute(
                        "INSERT OR IGNORE INTO contract_links "
                        "(contract_uuid, kind, other_uuid, other_name) "
                        "VALUES (?,'unlocks',?,?)",
                        (uuid, target,
                         unlocked.get("DebugName") if isinstance(unlocked, dict) else None),
                    )

        prereq = c.get("ReputationPrerequisite")
        if isinstance(prereq, dict) and prereq.get("Faction"):
            lo_name, lo_val = _standing(prereq.get("MinStanding"))
            hi_name, hi_val = _standing(prereq.get("MaxStanding"))
            con.execute(
                "INSERT INTO contract_reputation "
                "(contract_uuid, direction, faction_uuid, faction_name, scope, "
                " scope_uuid, min_standing_name, min_standing_value, "
                " max_standing_name, max_standing_value) "
                "VALUES (?,'prerequisite',?,?,?,?,?,?,?,?)",
                (uuid, prereq.get("FactionUUID"), prereq.get("Faction"),
                 prereq.get("Scope"), prereq.get("ScopeUUID"),
                 lo_name, lo_val, hi_name, hi_val),
            )
            reputation += 1

        for gained in c.get("ReputationGained") or []:
            con.execute(
                "INSERT INTO contract_reputation "
                "(contract_uuid, direction, faction_uuid, faction_name, scope, "
                " scope_uuid, amount, tier) VALUES (?,'gained',?,?,?,?,?,?)",
                (uuid, gained.get("FactionUUID"), gained.get("Faction"),
                 gained.get("Scope"), gained.get("ScopeUUID"),
                 gained.get("Amount"), gained.get("Tier")),
            )
            reputation += 1

        # « Où je choppe le blueprint de tel équipement ? » — la réponse est
        # ici, pas dans blueprints.json qui ne donne que des clés techniques.
        for pool in c.get("Blueprints") or []:
            pool_uuid = pool.get("PoolUUID")
            if not pool_uuid:
                continue
            con.execute(
                "INSERT OR IGNORE INTO contract_reward_pools "
                "(contract_uuid, pool_uuid, chance) VALUES (?,?,?)",
                (uuid, pool_uuid, pool.get("Chance")),
            )
            con.executemany(
                "INSERT OR IGNORE INTO reward_pool_contents "
                "(pool_uuid, blueprint_uuid, item_uuid, item_name) VALUES (?,?,?,?)",
                [
                    (pool_uuid, entry.get("BlueprintUUID"), entry.get("ItemUUID"),
                     entry.get("ItemName"))
                    for entry in pool.get("PoolContents") or []
                    if entry.get("BlueprintUUID")
                ],
            )

        rows = []
        for role, key in (("availability", "AvailabilityLocations"),
                          ("required", "RequiredLocations")):
            for pool in c.get(key) or []:
                for loc in pool.get("ResolvedLocations") or []:
                    rows.append((uuid, role, loc.get("UUID"), loc.get("Name"),
                                 pool.get("Name")))
        for pool in (c.get("LocationPools") or {}).values():
            if not isinstance(pool, dict):
                continue
            for loc in pool.get("ResolvedLocations") or []:
                rows.append((uuid, "mission", loc.get("UUID"), loc.get("Name"),
                             pool.get("Purpose")))
        if rows:
            con.executemany(
                "INSERT INTO contract_locations "
                "(contract_uuid, role, location_uuid, location_name, pool_name) "
                "VALUES (?,?,?,?,?)",
                rows,
            )

        # Les objectifs. Un nom de debug et un type de gestionnaire — et,
        # quand le jeu en écrit une, la **consigne rédigée**.
        prefixe = _prefixe_objectif(c, textes)
        objectifs = []
        for rang, jeton in enumerate(c.get("ObjectiveTokens") or []):
            if not isinstance(jeton, dict):
                continue
            cle = texte_en = None
            consignes = textes.get(prefixe or "", [])
            if rang < len(consignes):
                cle, texte_en = consignes[rang]
            objectifs.append((uuid, rang, jeton.get("DebugName"),
                              jeton.get("HandlerType"), cle, texte_en))
        if objectifs:
            con.executemany(
                "INSERT INTO contract_objectives "
                "(contract_uuid, position, debug_name, handler, cle_texte, "
                " texte_en) VALUES (?,?,?,?,?,?)", objectifs)

    _fill_missing_systems(con)
    _build_mission_groups(con)

    # Les organisations donneuses d'ordre sont des entités à part entière : le
    # joueur dit « Foxwell », pas le titre d'un de ses 49 contrats.
    #
    # **Un jeton de gabarit n'est pas une organisation.** Deux `mission_giver`
    # sur 76 valent « ~mission(Contractor|BountyFrom) » : le serveur les
    # remplace à la génération du contrat, nous n'avons que le gabarit. Indexés
    # comme alias, ils se résolvaient à 85 sur le mot « mission » de n'importe
    # quelle question — « quelle mission rapporte le plus de réputation »
    # filtrait sur cette pseudo-organisation et ne rendait rien.
    for row in con.execute(
        "SELECT DISTINCT mission_giver FROM contracts WHERE mission_giver IS NOT NULL"
    ):
        if row["mission_giver"].startswith("~mission("):
            continue
        aliases.add_variants("org", row["mission_giver"], row["mission_giver"])

    return contracts, reputation


_CLE_OBJECTIF = re.compile(r"^(.*?)_obj_long_(\d+)(?:,P)?$", re.IGNORECASE)


def consignes_redigees(labels: dict[str, str]) -> dict[str, list[tuple]]:
    """Les consignes de mission écrites, indexées par famille et par rang.

    `labels.json` porte 1 306 clés `<famille>_obj_long_NN`, et ce sont de
    vraies phrases — « Retrieve Energy Anomaly data from the Engineering
    wing. » — là où `ObjectiveTokens` ne donne qu'un nom de debug. J'avais
    écrit qu'elles n'existaient pas ; elles existent depuis le premier jour.

    On ne garde que `_obj_long_` : `_short_` et `_hud_` sont les versions
    tronquées pour l'ATH, `_marker_` un libellé de balise. La plus longue est
    la seule qui soit une consigne.

    **La numérotation ne part pas du même chiffre selon la famille** — 206
    commencent à 1, sept à 0, deux à 2 ou 3. Un décalage fixe se tromperait
    donc sur toutes les familles sauf une catégorie : on rend une **liste
    ordonnée**, et l'appelant l'aligne sur ses objectifs dans l'ordre.
    """
    brut: dict[str, dict[int, tuple]] = {}
    for cle, valeur in labels.items():
        trouve = _CLE_OBJECTIF.match(cle)
        if not trouve or not valeur or valeur == PLACEHOLDER:
            continue
        brut.setdefault(trouve.group(1), {})[int(trouve.group(2))] = (
            cle, valeur)
    return {famille: [rangs[n] for n in sorted(rangs)]
            for famille, rangs in brut.items()}


def _prefixe_objectif(contrat: dict, textes: dict[str, dict]) -> str | None:
    """Quelle famille de consignes ce contrat utilise.

    Trois voies, mesurées dans cet ordre de fiabilité :

    1. la **clé de titre** (ou de description, ou le nom de debug), dont on
       retire les segments de queue un à un — « Hockrow_FacilityDelve_P2M1
       _Repeat » retombe sur « Hockrow_FacilityDelve_P2M1 » ;
    2. le **nom de l'objectif** lui-même, qui est parfois l'archétype
       (« BoardShip », « CreateUplink ») ;
    3. un **segment du nom de debug**, pour les missions générées :
       « PU_Bounty_PVE_Stanton2… » relève de « bounty ».

    Ensemble : **496 contrats sur les 2 520 qui ont des objectifs**. Le reste
    n'a pas de consigne écrite du tout — le courrier, le fret et les primes
    générées ne sont décrits que par leur nom de debug. Ne pas chercher à
    combler : la donnée n'existe pas.
    """
    index = {nom.lower(): nom for nom in textes}

    for brut in (contrat.get("TitleKey"), contrat.get("DescriptionKey"),
                 contrat.get("DebugName")):
        if not brut:
            continue
        base = re.sub(r"_(title|desc)(_\d+)?$", "", brut, flags=re.IGNORECASE)
        base = re.sub(r"-[A-Za-z0-9]+", "", base)
        morceaux = base.split("_")
        for i in range(len(morceaux), 0, -1):
            trouve = index.get("_".join(morceaux[:i]).lower())
            if trouve:
                return trouve

    for jeton in contrat.get("ObjectiveTokens") or []:
        if isinstance(jeton, dict):
            nom = (jeton.get("DebugName") or "").strip().replace(" ", "")
            if (trouve := index.get(nom.lower())):
                return trouve

    morceaux = [m for m in re.split(r"[_-]", contrat.get("DebugName") or "")
                if m]
    for longueur in (3, 2, 1):
        for i in range(len(morceaux) - longueur + 1):
            trouve = index.get("_".join(morceaux[i:i + longueur]).lower())
            if trouve:
                return trouve
    return None


_SITE_DE_MISSION = re.compile(r"^(\w+?)_(\w+)_name(?:,P)?$")


def load_sites_de_mission(con: sqlite3.Connection,
                          labels: dict[str, str]) -> int:
    """Les complexes où se jouent les missions, et leurs salles.

    **Remarque de l'utilisateur, et elle était juste** : « il y a 120
    complexes certes, mais ça reste toujours une Onyx Facility qui est un
    clone d'une autre, donc l'information doit bien être quelque part ». Elle
    y est, et je l'avais manquée — pas dans le contrat, dans `labels.json` :

        FacilityDelve_Stanton4a_name = Onyx Facility
        FacilityDelve_WingA_name     = Engineering Wing
        FacilityDelve_WingB_name     = Research Wing
        FacilityDelve_WingD_name     = Site-B Lab

    Une même famille de clés nomme le **site** et ses **salles**. Le contrat
    ne cite que la salle (« Engineering Wing ») ; le site se retrouve par la
    famille, qui est le préfixe du `DebugName` du contrat.

    Le segment distingue les deux : « Wing… » désigne une salle, le reste un
    site. J'avais écrit ce rattachement en dur comme « règle de terrain » —
    c'était une donnée, et une donnée se lit.
    """
    lignes = []
    for cle, valeur in labels.items():
        trouve = _SITE_DE_MISSION.match(cle)
        if not trouve or not valeur or valeur == PLACEHOLDER:
            continue
        famille, segment = trouve.group(1), trouve.group(2)
        lignes.append((famille, segment, valeur,
                       1 if segment.lower().startswith("wing") else 0))

    con.executemany(
        "INSERT OR REPLACE INTO mission_sites "
        "(famille, segment, nom, est_salle) VALUES (?,?,?,?)", lignes)
    return len(lignes)


def _fill_missing_systems(con: sqlite3.Connection) -> None:
    """Système déduit des lieux quand le nom de debug ne le porte pas.

    On ne l'attribue que si **tous** les lieux du contrat relèvent du même
    système : un contrat multi-systèmes n'a pas de système, et le prétendre
    serait pire que de laisser vide.
    """
    con.execute(
        """
        UPDATE contracts SET system = (
          SELECT s.system_name
          FROM contract_locations cl
          JOIN starmap s ON s.uuid = cl.location_uuid
          WHERE cl.contract_uuid = contracts.uuid AND s.system_name IS NOT NULL
          GROUP BY cl.contract_uuid
          HAVING COUNT(DISTINCT s.system_name) = 1
        )
        WHERE system IS NULL
        """
    )


def _build_mission_groups(con: sqlite3.Connection) -> None:
    """« Les missions Foxwell Enforcement à Pyro » — l'unité de réponse.

    Précalculé plutôt que recalculé à chaque question : le regroupement ne
    dépend que de la donnée ingérée.
    """
    con.execute(
        """
        INSERT OR REPLACE INTO mission_groups
          (mission_giver, system, contract_count, family_count,
           min_standing_name, min_standing_value, max_standing_name, max_standing_value)
        SELECT
          mission_giver,
          system,
          COUNT(*),
          COUNT(DISTINCT family),
          (SELECT c2.min_standing_name FROM contracts c2
            WHERE c2.mission_giver = c.mission_giver
              AND (c2.system IS c.system) AND c2.min_standing_value IS NOT NULL
              AND c2.not_for_release = 0
            ORDER BY c2.min_standing_value LIMIT 1),
          MIN(min_standing_value),
          (SELECT c3.min_standing_name FROM contracts c3
            WHERE c3.mission_giver = c.mission_giver
              AND (c3.system IS c.system) AND c3.min_standing_value IS NOT NULL
              AND c3.not_for_release = 0
            ORDER BY c3.min_standing_value DESC LIMIT 1),
          MAX(min_standing_value)
        FROM contracts c
        WHERE mission_giver IS NOT NULL AND not_for_release = 0
          AND work_in_progress = 0
        GROUP BY mission_giver, system
        """
    )
