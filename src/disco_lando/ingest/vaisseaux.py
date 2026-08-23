"""Les vaisseaux : fiche, hardpoints, tourelles, combat.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
import json
import pathlib
import sqlite3
from collections.abc import Iterator
from ..hardpoint_categories import categorize
from ..porteurs import jetons_de_vaisseaux as _jetons_de_vaisseaux  # noqa: F401
from .duel import charger_vaisseau_duel
from .source import read_json
from .socle import AliasCollector, PLACEHOLDER


# ------------------------------------------------------------------ vaisseaux

def _walk_loadout(nodes: list[dict], parent: str | None, depth: int) -> Iterator[dict]:
    for node in nodes or []:
        yield {"node": node, "parent": parent, "depth": depth}
        yield from _walk_loadout(node.get("Loadout") or [], node.get("PortId"), depth + 1)


_SOURCES_TOURELLES = (
    ("habitee", "MannedTurrets"),
    ("telecommandee", "RemoteTurrets"),
    ("pdc", "PdcTurrets"),
)


def load_ships(con: sqlite3.Connection, root: pathlib.Path,
               aliases: AliasCollector, labels: dict[str, str]) -> tuple[int, int]:
    ships = hardpoints = 0
    for path in sorted((root / "ships").glob("*.json")):
        s = read_json(path)
        uuid = s.get("UUID")
        if not uuid:
            continue
        manufacturer = s.get("Manufacturer") or {}
        name = s.get("Name") or labels.get(s.get("NameKey") or "") or s.get("ClassName")
        # `FlightCharacteristics` porte la vitesse SCM, l'une des questions les
        # plus fréquentes, et le premier passage l'ignorait complètement.
        # Les vitesses existent en double, sous deux nomenclatures : `Speeds`
        # les nomme Scm/Max/BoostForward, `IFCS` les nomme ScmSpeed/MaxSpeed/
        # BoostSpeedForward. On lit les deux plutôt que de parier sur l'une.
        characteristics = s.get("FlightCharacteristics") or {}
        speeds = characteristics.get("Speeds") or {}
        ifcs = characteristics.get("IFCS") or {}
        flight = {
            "ScmSpeed": speeds.get("Scm") or ifcs.get("ScmSpeed"),
            "MaxSpeed": speeds.get("Max") or ifcs.get("MaxSpeed"),
            "BoostSpeed": speeds.get("BoostForward") or ifcs.get("BoostSpeedForward"),
        }
        agility = s.get("Agility") or {}
        acceleration = agility.get("Acceleration") or {}
        timing = characteristics.get("Timing") or {}
        afterburner = (ifcs.get("AfterburnerNew")
                       or ifcs.get("Afterburner")
                       or characteristics.get("Afterburner") or {})
        propulsion = s.get("Propulsion") or {}
        insurance = s.get("Insurance") or {}
        con.execute(
            "INSERT OR REPLACE INTO ships "
            "(uuid, class_name, name, manufacturer_code, manufacturer_name, "
            " career, role, size, crew, length, width, height, "
            " cross_x, cross_y, cross_z, mass, cargo_scu, "
            " health, shield_hp, pilot_dps, pilot_alpha, qt_speed, qt_range, "
            " scm_speed, max_speed, boost_speed, pitch, yaw, roll, "
            " boost_backward, pitch_boosted, yaw_boosted, roll_boosted, "
            " torque_imbalance, "
            " accel_main, accel_retro, accel_maneuver, "
            " accel_main_boosted, accel_retro_boosted, "
            " accel_maneuver_boosted, zero_to_scm, scm_to_zero, "
            " boost_capacity, boost_regen, boost_regen_time, "
            " fuel_capacity, fuel_usage, fuel_intake, quantum_fuel, ore_capacity, "
            " insurance_cost, "
            " insurance_minutes, is_spaceship, is_vehicle, is_gravlev, description) "
            "VALUES (" + ",".join("?" for _ in range(56)) + ")",
            (
                uuid, s.get("ClassName"), name,
                manufacturer.get("Code"), manufacturer.get("Name"),
                s.get("Career"), s.get("Role"), s.get("Size"), s.get("Crew"),
                s.get("Length"), s.get("Width"), s.get("Height"),
                # La section présentée : ce que `Length × Width × Height` ne
                # sait pas dire, faute de décrire la forme. Sert au taux de
                # touche du modèle d'engagement.
                (s.get("CrossSection") or {}).get("X"),
                (s.get("CrossSection") or {}).get("Y"),
                (s.get("CrossSection") or {}).get("Z"),
                s.get("Mass"),
                s.get("Cargo"), s.get("Health"), s.get("ShieldHp"),
                (s.get("Weaponry") or {}).get("PilotDps"),
                (s.get("Weaponry") or {}).get("PilotAlpha"),
                (s.get("QuantumTravel") or {}).get("Speed"),
                (s.get("QuantumTravel") or {}).get("Range"),
                flight["ScmSpeed"], flight["MaxSpeed"], flight["BoostSpeed"],
                agility.get("Pitch"), agility.get("Yaw"), agility.get("Roll"),
                speeds.get("BoostBackward") or ifcs.get("BoostSpeedBackward"),
                agility.get("PitchBoosted"), agility.get("YawBoosted"),
                agility.get("RollBoosted"),
                # Publié uniquement sous `IFCS` — `Agility` ne le reprend
                # pas, contrairement à Pitch/Yaw/Roll.
                ifcs.get("TorqueImbalanceMultiplier"),
                acceleration.get("Main"), acceleration.get("Retro"),
                acceleration.get("Maneuver"),
                acceleration.get("MainBoosted"),
                acceleration.get("RetroBoosted"),
                acceleration.get("ManeuverBoosted"),
                timing.get("ZeroToScm"), timing.get("ScmToZero"),
                afterburner.get("CapacitorMax"),
                afterburner.get("CapacitorRegenPerSec"),
                afterburner.get("RegenTime"),
                propulsion.get("FuelCapacity"),
                # La moitié manquante de l'autonomie : la capacité était lue
                # depuis le début, la consommation jamais.
                (propulsion.get("FuelUsage") or {}).get("Main"),
                propulsion.get("FuelIntakeRate"),
                (s.get("QuantumTravel") or {}).get("FuelCapacity"),
                s.get("OreCapacity"),
                insurance.get("ExpeditedCost"), insurance.get("StandardClaimTime"),
                int(bool(s.get("IsSpaceship"))), int(bool(s.get("IsVehicle"))),
                int(bool(s.get("IsGravlev"))), s.get("DescriptionText"),
            ),
        )
        charger_vaisseau_duel(con, s)
        ships += 1
        # Les grilles de soute : « est-ce que ma caisse rentre » se joue sur
        # la taille max acceptée, pas sur les SCU totaux.
        for grille in s.get("CargoGrids") or []:
            taille = grille.get("MaxSize") or {}
            cellule = grille.get("MinSize") or {}
            con.execute(
                "INSERT INTO cargo_grids (ship_uuid, scu, max_x, max_y, "
                " max_z, dim_x, dim_y, dim_z, min_x, min_y, min_z, "
                " ouverte, externe) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid, grille.get("SCU"), taille.get("X"), taille.get("Y"),
                 taille.get("Z"), grille.get("X"), grille.get("Y"),
                 grille.get("Z"), cellule.get("X"), cellule.get("Y"),
                 cellule.get("Z"),
                 int(bool(grille.get("IsOpenContainer"))),
                 int(bool(grille.get("IsExternalContainer")))))
        # Le bloc de combat : armure à seuil de déflexion (4.7), bouclier
        # total, coque, missiles. Un vaisseau sans bloc Armor (9 sur 316)
        # garde une ligne à trous — l'outil dira ce qui manque.
        armor = s.get("Armor") or {}
        defl = armor.get("Deflection") or {}
        mult = armor.get("DamageMultipliers") or {}
        shields = s.get("ShieldsTotal") or {}
        missiles = (s.get("Weaponry") or {}).get("Missiles") or {}
        # Ce que l'armure encaisse, à distinguer de ce qu'elle laisse passer :
        # deux couches successives, et non deux facteurs sur la même étape.
        resist = armor.get("ResistanceMultipliers") or {}
        con.execute(
            "INSERT INTO ship_combat (ship_uuid, hull_health, armor_health, "
            " defl_physical, defl_energy, mult_physical, mult_energy, "
            " res_physical, res_energy, "
            " shield_hp, shield_regen, missiles_count, missile_damage) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, s.get("Health"), armor.get("Health"),
             defl.get("Physical"), defl.get("Energy"),
             mult.get("Physical"), mult.get("Energy"),
             resist.get("Physical"), resist.get("Energy"),
             shields.get("Hp"), shields.get("Regen"),
             missiles.get("Count"),
             (missiles.get("Damage") or {}).get("Total")))
        # L'armement stock, avec son poste réel. La 4.9 publie désormais les
        # armes filles des tourelles : les ignorer laissait le Hammerhead sans
        # aucun canon dans le duel. Les PDC sont conservées comme donnée
        # source, mais le métier de combat les écarte du feu anti-vaisseau.
        stock: dict[tuple, int] = {}
        armes_par_poste = [("pilote", arme) for arme in
                           (((s.get("Weaponry") or {}).get("FixedWeapons")
                             or {}).get("Weapons") or [])]
        for poste, cle_source in _SOURCES_TOURELLES:
            for tourelle in s.get(cle_source) or []:
                armes_par_poste.extend(
                    (poste, arme) for arme in (tourelle.get("Weapons") or []))
        for poste, arme in armes_par_poste:
            cle = (poste, arme.get("UUID"), arme.get("Name"))
            stock[cle] = stock.get(cle, 0) + 1
        for (poste, w_uuid, w_name), n in stock.items():
            con.execute(
                "INSERT INTO ship_armes (ship_uuid, weapon_uuid, name, n, poste) "
                "VALUES (?,?,?,?,?)", (uuid, w_uuid, w_name, n, poste))
        aliases.add_variants("ship", uuid, name, s.get("ClassName"),
                             drop_prefix=manufacturer.get("Name"))

        # Une tourelle est qualifiée par les listes sémantiques de la source,
        # pas par un « turret » trouvé dans son nom (les SeatAccess en portent
        # aussi). Les 432 entrées mesurées se relient ainsi au Loadout, y
        # compris la PDC du MDC imbriquée sous son hardpoint arrière.
        meta_tourelles: dict[str, tuple[str, str | None]] = {}
        for genre, cle_source in _SOURCES_TOURELLES:
            for tourelle in s.get(cle_source) or []:
                meta = (genre, tourelle.get("TurretType"))
                for cle in (tourelle.get("PartName"),
                            tourelle.get("HardpointName")):
                    if cle:
                        meta_tourelles[cle] = meta

        rows = []
        for entry in _walk_loadout(s.get("Loadout") or [], None, 0):
            node, parent, depth = entry["node"], entry["parent"], entry["depth"]
            raw_type = node.get("Type") or ""
            head, _, sub = raw_type.partition(".")
            accepted = [c.get("Type") for c in (node.get("CompatibleTypes") or [])]
            installed_name = node.get("Name")
            if installed_name == PLACEHOLDER:
                installed_name = None
            meta_tourelle = (meta_tourelles.get(node.get("PortId"))
                             or meta_tourelles.get(node.get("HardpointName")))
            rows.append((
                uuid,
                node.get("PortId"), parent, node.get("RootPortId"), depth,
                "/".join(node.get("Path") or []),
                node.get("HardpointName"),
                json.dumps([a for a in accepted if a], ensure_ascii=False) if accepted else None,
                node.get("MinSize"), node.get("MaxSize"),
                int(bool(node.get("Editable"))),
                node.get("UUID"), node.get("ClassName"), installed_name,
                head or None, sub or None, node.get("Grade"),
                meta_tourelle[0] if meta_tourelle else None,
                meta_tourelle[1] if meta_tourelle else None,
                categorize(raw_type, [a for a in accepted if a]),
            ))
        con.executemany(
            "INSERT OR REPLACE INTO hardpoints "
            "(ship_uuid, port_id, parent_port_id, root_port_id, depth, path, "
            " hardpoint_name, accepted_types, min_size, max_size, editable, "
            " installed_uuid, installed_class, installed_name, installed_type, "
            " installed_subtype, installed_grade, turret_kind, turret_type, "
            " category) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        hardpoints += len(rows)
    return ships, hardpoints


def load_composants_exposes(con: sqlite3.Connection,
                            root: pathlib.Path) -> int:
    """Relie tourelles/propulseurs du loadout à leur durabilité publiée.

    `ship-items.json` ne couvre pas toutes les tourelles et ne porte la santé
    que sur huit familles de propulseurs. Les fichiers individuels `items/`
    contiennent en revanche le même bloc normalisé `Item.stdItem.Durability`.
    On ne les ingère pas tous : seulement les classes réellement montées sur
    un hardpoint sémantiquement exposé. Un nom contenant « turret » ne suffit
    jamais — les `SeatAccess` ont déjà prouvé que cette heuristique ment.
    """
    composants = con.execute(
        "SELECT h.ship_uuid, h.port_id, h.hardpoint_name, h.installed_name, "
        "       i.uuid item_uuid, "
        "       h.installed_class, h.category, h.turret_kind "
        "FROM hardpoints h "
        "LEFT JOIN items i ON i.uuid=h.installed_uuid "
        "WHERE h.installed_class IS NOT NULL "
        "  AND (h.turret_kind IS NOT NULL OR h.category = 'thruster')"
    ).fetchall()
    cache: dict[str, tuple[dict, dict] | None] = {}
    total = 0
    for h in composants:
        classe = h["installed_class"]
        cle = classe.lower()
        if cle not in cache:
            chemin = root / "items" / f"{cle}.json"
            if not chemin.exists():
                cache[cle] = None
            else:
                brut = read_json(chemin)
                item = brut.get("Item") or brut
                composants_bruts = (((brut.get("Raw") or {}).get("Entity") or {})
                                     .get("Components") or {})
                cache[cle] = (
                    item.get("stdItem") or {},
                    composants_bruts.get("SDistortionParams") or {},
                )
        std, distorsion = cache[cle] or ({}, {})
        durabilite = std.get("Durability") or {}
        resistance = durabilite.get("Resistance") or {}

        def _resistance(nom: str, champ: str, defaut: float) -> float:
            bloc = resistance.get(nom) or {}
            valeur = bloc.get(champ) if isinstance(bloc, dict) else None
            return defaut if valeur is None else valeur

        genre = "tourelle" if h["turret_kind"] else "propulseur"
        nom = h["installed_name"]
        if not nom or nom == PLACEHOLDER:
            nom = h["hardpoint_name"] or std.get("Name") or classe
        con.execute(
            "INSERT INTO ship_composants_exposes "
            "(ship_uuid, port_id, genre, poste, nom, class_name, item_uuid, pv, "
            " mult_physical, mult_energy, seuil_physical, seuil_energy, "
            " distortion_maximum, distortion_decay_rate, "
            " distortion_decay_delay, distortion_seuil_unique) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (h["ship_uuid"], h["port_id"], genre, h["turret_kind"], nom,
             classe, h["item_uuid"], durabilite.get("Health"),
             _resistance("Physical", "Multiplier", 1.0),
             _resistance("Energy", "Multiplier", 1.0),
             _resistance("Physical", "Threshold", 0.0),
             _resistance("Energy", "Threshold", 0.0),
             distorsion.get("Maximum"), distorsion.get("DecayRate"),
             distorsion.get("DecayDelay"),
             int(bool(distorsion.get("PowerChangeOnlyAtMaxDistortion")))
             if distorsion else None))
        total += 1
    return total


def load_delais_bouclier(con: sqlite3.Connection, root: pathlib.Path) -> int:
    """Les délais de régénération du bouclier **équipé** de chaque vaisseau.

    Passe à part pour la même raison que `load_dps_soutenu` : la donnée décrit
    un **objet** — le générateur — mais il faut le loadout du **vaisseau**
    pour savoir lequel est monté, et les objets se chargent après les
    vaisseaux.

    `ShieldsTotal` du vaisseau agrège les PV et la régénération mais **pas**
    les délais ; il faut donc descendre au générateur. C'est ce que demande
    l'utilisateur (2026-08-14) : « tu ne dois pas calculer avec la médiane
    mais par rapport au bouclier équipé dans le loadout ».

    Deux délais distincts, et la différence compte dans un duel :
    `DamagedDelay` (médiane 5,5 s) court quand le bouclier a seulement
    encaissé, `DownedDelay` (~11 s) quand il est tombé. Ils varient de
    générateur en générateur — 32 et 31 valeurs distinctes sur 73 — là où le
    modèle d'engagement se contentait de 2 secondes inventées.
    """
    # Les délais, par UUID de générateur.
    delais: dict[str, tuple[float | None, float | None]] = {}
    for filename in ("ship-items.json",):
        path = root / filename
        if not path.exists():
            continue
        for entry in read_json(path):
            std = entry.get("stdItem") or {}
            bouclier = std.get("Shield") or {}
            if not bouclier.get("MaxShieldHealth"):
                continue
            uuid = entry.get("reference") or std.get("UUID")
            if uuid:
                delais[uuid] = (bouclier.get("DamagedDelay"),
                                bouclier.get("DownedDelay"))

    ecrits = 0
    for path in sorted((root / "ships").glob("*.json")):
        s = read_json(path)
        uuid = s.get("UUID")
        if not uuid:
            continue
        # Un vaisseau porte plusieurs générateurs identiques : le délai est
        # celui du modèle, pas une somme. On prend le premier reconnu.
        trouve = None
        for noeud in _walk_loadout(s.get("Loadout") or [], None, 0):
            ref = (noeud["node"] or {}).get("UUID")
            if ref in delais:
                trouve = delais[ref]
                break
        if trouve is None or not any(v is not None for v in trouve):
            continue
        con.execute(
            "UPDATE ship_combat SET shield_damaged_delay = ?, "
            " shield_downed_delay = ? WHERE ship_uuid = ?",
            (trouve[0], trouve[1], uuid))
        ecrits += 1
    return ecrits


def load_dps_soutenu(con: sqlite3.Connection, root: pathlib.Path) -> int:
    """Le DPS soutenu **publié**, depuis les armes fixes et les tourelles.

    Passe à part parce que la donnée traverse deux familles de fichiers : elle
    décrit une **arme**, mais elle est écrite dans les fichiers de **vaisseau**,
    et les objets se chargent après eux.

    C'est le correctif d'un défaut en service. `stats.dps_soutenu` le déduisait
    du capacitor et sortait 3,25 sur l'Omnisky IX là où CIG publie 290,3 —
    faux sur 103 armes sur 114, écart médian 97,5 %. Le bloc `Capacitor` mêle
    deux échelles : `MaxAmmoLoad` vaut 25 quand `RequestedAmmoLoad` vaut
    20 200, si bien que 80 armes sur 115 ont un coût par tir supérieur à leur
    réservoir entier.

    L'appariement est **prouvé**, pas supposé : le `Dps` publié au même endroit
    vaut celui d'`item_stats` à l'arrondi près. La 4.9 porte 91 armes distinctes
    avec un soutenu, dont 14 n'apparaissent qu'en tourelle ; les ignorer
    remettait leur DPS maximal à la place du soutenu dans le duel. Et la clé est
    l'UUID, jamais le nom — deux armes distinctes s'appellent « CF-117 Bulldog
    Repeater », avec 50,2 et 111,9 de soutenu.
    """
    valeurs: dict[str, float] = {}
    for path in sorted((root / "ships").glob("*.json")):
        s = read_json(path)
        armes = list(
            ((s.get("Weaponry") or {}).get("FixedWeapons") or {}).get("Weapons")
            or [])
        for _, cle_source in _SOURCES_TOURELLES:
            for tourelle in s.get(cle_source) or []:
                armes.extend(tourelle.get("Weapons") or [])
        for arme in armes:
            uuid, soutenu = arme.get("UUID"), arme.get("SustainedDps")
            if uuid and soutenu is not None:
                valeurs[uuid] = soutenu
    if not valeurs:
        return 0
    con.executemany(
        "UPDATE item_stats SET dps_soutenu = ? WHERE item_uuid = ?",
        [(v, u) for u, v in valeurs.items()])
    # Relire ce qui est entré : un `UPDATE` qui ne touche aucune ligne ne lève
    # pas, et le compte de `valeurs` masquerait une jointure vide.
    return con.execute(
        "SELECT COUNT(*) FROM item_stats WHERE dps_soutenu IS NOT NULL").fetchone()[0]


def load_emp(con: sqlite3.Connection, root: pathlib.Path) -> int:
    """Les paramètres des EMP et le pool de distorsion des composants.

    **Sept EMP étaient en base sans une seule statistique.** Le bloc
    `Raw.Entity.Components.SCItemEMPParams` publie pourtant tout — charge,
    dégâts de distorsion, rayons, décharge, recharge — et personne ne le
    lisait. Le Hawk devenait un chasseur médiocre alors que son EMP est ce
    qui le rend redoutable (utilisateur, 2026-08-16 : « le Hawk est assez
    OP grâce à ça »).

    On ingère au passage `SDistortionParams`, qui dit ce que la décharge
    **fait** : chaque composant a un pool, et saturé il s'éteint. Sans lui
    on saurait qu'un EMP délivre 2 475 points sans savoir si c'est beaucoup.
    C'est beaucoup : un générateur de bouclier de chasseur tient entre 1 650
    et 2 500, une centrale 1 950. Les armes, elles, sont à 500 000 — elles
    ne s'éteignent jamais, et c'est pour ça que l'EMP désarme un vaisseau
    sans le désarmer.

    On ne balaye pas les 10 804 objets : seuls les EMP du catalogue et les
    composants réellement montés sur un vaisseau nous intéressent.
    """
    interessants = {r[0].lower() for r in con.execute(
        "SELECT DISTINCT i.class_name FROM items i "
        "LEFT JOIN hardpoints h ON h.installed_uuid=i.uuid "
        "WHERE i.class_name IS NOT NULL "
        "AND (h.installed_uuid IS NOT NULL OR i.type='EMP')")}
    total = 0
    for classe in sorted(interessants):
        chemin = root / "items" / f"{classe}.json"
        if not chemin.exists():
            continue
        brut = read_json(chemin)
        composants = ((brut.get("Raw") or {}).get("Entity") or {}
                      ).get("Components") or {}
        uuid = con.execute(
            "SELECT uuid FROM items WHERE lower(class_name) = ? LIMIT 1",
            (classe,)).fetchone()
        if not uuid:
            continue
        emp = composants.get("SCItemEMPParams")
        if emp:
            con.execute(
                "INSERT OR REPLACE INTO emp_devices "
                "(item_uuid, charge_time, distortion_damage, emp_radius, "
                " emp_radius_min, unleash_time, cooldown_time) "
                "VALUES (?,?,?,?,?,?,?)",
                (uuid[0], emp.get("chargeTime"), emp.get("distortionDamage"),
                 emp.get("empRadius"), emp.get("minEmpRadius"),
                 emp.get("unleashTime"), emp.get("cooldownTime")))
            total += 1
        distorsion = composants.get("SDistortionParams")
        if distorsion and distorsion.get("Maximum"):
            con.execute(
                "INSERT OR REPLACE INTO distortion_params "
                "(item_uuid, maximum, decay_rate, decay_delay, seuil_unique) "
                "VALUES (?,?,?,?,?)",
                (uuid[0], distorsion.get("Maximum"),
                 distorsion.get("DecayRate"), distorsion.get("DecayDelay"),
                 int(bool(distorsion.get("PowerChangeOnlyAtMaxDistortion")))))
            total += 1
    return total


