"""Chargement scunpacked-data -> SQLite.

Chaque loader lit sa source, produit ses lignes et déclare ses alias. Aucun ne
connaît les autres : l'ordre d'appel dans `run.py` ne dépend que des clés
étrangères.
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3
from collections.abc import Iterator

from ..hardpoint_categories import categorize
from ..normalize import normalize, split_class_name
from .source import read_json

PLACEHOLDER = "<= PLACEHOLDER =>"


def _prose_utile(texte) -> str | None:
    """Un texte, ou rien — jamais l'échafaudage de CIG.

    Le jeu écrit « <= UNINITIALIZED => » et « <= PLACEHOLDER => » dans les
    cases qu'il n'a pas remplies. Les stocker comme prose les faisait
    ressortir dans les fiches, où ils se lisent comme une réponse.
    """
    texte = (texte or "").strip()
    if not texte or texte.startswith("<="):
        return None
    return texte


# ------------------------------------------------------------------ alias

class AliasCollector:
    """Accumule les alias et les écrit en une passe.

    Le nom canonique est inséré comme un alias parmi d'autres : le résolveur
    n'interroge qu'une table, jamais huit (cf. docs/SCHEMA.md).
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], tuple] = {}

    def add(
        self,
        entity_type: str,
        entity_id: str,
        alias: str | None,
        *,
        source: str = "canonical",
        weight: float = 1.0,
        lang: str = "en",
    ) -> None:
        if not alias or not entity_id:
            return
        text = alias.strip()
        if not text or text == PLACEHOLDER:
            return
        norm = normalize(text)
        if not norm or len(norm) < 2:
            return
        key = (entity_type, entity_id, norm, source)
        if key in self._rows:
            return
        self._rows[key] = (
            entity_type, entity_id, text, norm, norm.replace(" ", ""),
            lang, source, weight,
        )

    def add_variants(self, entity_type: str, entity_id: str, name: str | None,
                     class_name: str | None = None, *, drop_prefix: str | None = None) -> None:
        """Nom canonique + variantes dérivées mécaniquement.

        Le joueur dit « Gladius », jamais « Aegis Gladius » ni
        « AEGS_Gladius ». Les variantes portent un poids inférieur pour que le
        nom officiel gagne à score égal.
        """
        self.add(entity_type, entity_id, name, source="canonical", weight=1.0)

        if name and drop_prefix and name.lower().startswith(drop_prefix.lower()):
            trimmed = name[len(drop_prefix):].strip(" -—")
            self.add(entity_type, entity_id, trimmed, source="derived", weight=0.9)

        if name and '"' in name:
            self.add(entity_type, entity_id, name.replace('"', " "),
                     source="derived", weight=0.9)

        if class_name:
            words = split_class_name(class_name)
            if words:
                self.add(entity_type, entity_id, " ".join(words),
                         source="derived", weight=0.8)

        # Le jeton de modèle est le nom que tape un joueur : « un C2 »,
        # « le 300i », « la M2 ». Mesuré le 2026-08-07 : aucun alias « c2 »
        # n'existait, et « combien de Cyclone dans un C2 » perdait son
        # porteur (60, sous le seuil). Un jeton qui mêle lettres et chiffres
        # est distinctif par construction ; un pur chiffre reste écarté —
        # « un nombre nu n'est pas une entité » — et un alias de trois
        # lettres ou moins ne vaut déjà que par égalité stricte.
        if entity_type == "ship" and name:
            for mot in normalize(name).split():
                if (len(mot) >= 2 and any(c.isdigit() for c in mot)
                        and any(c.isalpha() for c in mot)):
                    self.add(entity_type, entity_id, mot,
                             source="derived", weight=0.9)

    def flush(self, con: sqlite3.Connection) -> int:
        rows = list(self._rows.values())
        con.executemany(
            "INSERT OR IGNORE INTO aliases "
            "(entity_type, entity_id, alias, alias_norm, alias_flat, "
            " lang, source, weight) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        # Table FTS5 à contenu externe : elle se reconstruit par commande, pas
        # par DELETE + INSERT (qui corrompt l'index). « rebuild » est
        # idempotent, ce qui rend le second appel de flush() sans danger.
        con.execute("INSERT INTO aliases_fts (aliases_fts) VALUES ('rebuild')")

        # Découpage en mots, pour retrouver un terme dans un nom à rallonge.
        # Une seule passe de
        # relecture : c'est moins cher que de tenir les identifiants côté
        # Python à travers un executemany.
        con.execute("DELETE FROM alias_tokens")
        tokens = []
        for row in con.execute("SELECT id, alias_norm FROM aliases"):
            for word in dict.fromkeys(row["alias_norm"].split()):
                if len(word) < 3:
                    continue
                tokens.append((row["id"], word))
        con.executemany(
            "INSERT INTO alias_tokens (alias_id, token) VALUES (?,?)",
            tokens,
        )
        self._rows.clear()
        return len(rows)


# ------------------------------------------------------------------ labels

SYSTEMS = ("Stanton", "Pyro", "Nyx", "Terra", "Odin", "Castra")

# « FoxwellEnforcement_Patrol_Pyro_Super » — le nom de debug porte le système et
# la difficulté, que rien d'autre ne donne de façon fiable.
_DIFFICULTIES = ("VeryEasy", "Easy", "Medium", "Hard", "VeryHard", "Super", "Extreme")


def parse_debug_name(debug_name: str | None) -> tuple[str | None, str | None]:
    """Système et difficulté lus dans le nom de debug d'un contrat."""
    if not debug_name:
        return None, None
    parts = debug_name.split("_")
    system = next((s for s in SYSTEMS if s in parts), None)
    difficulty = next((d for d in _DIFFICULTIES if d in parts), None)
    return system, difficulty


def load_labels(root: pathlib.Path) -> dict[str, str]:
    """`labels.json` : 90 000 clés, anglais uniquement (pas de FR, cf. audit)."""
    path = root / "labels.json"
    if not path.exists():
        return {}
    raw = read_json(path)
    return {k.lstrip("﻿"): v for k, v in raw.items() if isinstance(v, str)}


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
            " career, role, size, crew, length, width, height, mass, cargo_scu, "
            " health, shield_hp, pilot_dps, pilot_alpha, qt_speed, qt_range, "
            " scm_speed, max_speed, boost_speed, pitch, yaw, roll, "
            " boost_backward, pitch_boosted, yaw_boosted, roll_boosted, "
            " accel_main, accel_retro, accel_maneuver, "
            " accel_main_boosted, accel_retro_boosted, "
            " accel_maneuver_boosted, zero_to_scm, scm_to_zero, "
            " boost_capacity, boost_regen, boost_regen_time, "
            " fuel_capacity, fuel_usage, fuel_intake, quantum_fuel, ore_capacity, "
            " insurance_cost, "
            " insurance_minutes, is_spaceship, is_vehicle, is_gravlev, description) "
            "VALUES (" + ",".join("?" for _ in range(52)) + ")",
            (
                uuid, s.get("ClassName"), name,
                manufacturer.get("Code"), manufacturer.get("Name"),
                s.get("Career"), s.get("Role"), s.get("Size"), s.get("Crew"),
                s.get("Length"), s.get("Width"), s.get("Height"), s.get("Mass"),
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
        con.execute(
            "INSERT INTO ship_combat (ship_uuid, hull_health, armor_health, "
            " defl_physical, defl_energy, mult_physical, mult_energy, "
            " shield_hp, shield_regen, missiles_count, missile_damage) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, s.get("Health"), armor.get("Health"),
             defl.get("Physical"), defl.get("Energy"),
             mult.get("Physical"), mult.get("Energy"),
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


# ------------------------------------------------------------------ objets

# Le champ `tags` d'un objet porte sa famille : « GATS BallisticGatling
# flightReady weaponMountUsable ». C'est là, et nulle part ailleurs, que se lit
# la différence entre un canon balistique et un canon laser.
_WEAPON_CLASSES = {
    "ballistic": "ballistic", "laser": "laser", "plasma": "plasma",
    "tachyon": "tachyon", "distortion": "distortion", "emp": "emp",
}
_WEAPON_KINDS = {
    "gatling": "gatling", "repeater": "repeater", "scattergun": "scattergun",
    "cannon": "cannon", "shotgun": "scattergun",
}


_DEV_MARQUEURS = ("test", "template", "placeholder", "debug", "_wip")


def _weapon_family(tags: str | None, class_name: str | None) -> tuple[str | None, str | None]:
    """Famille d'une arme, le nom de classe faisant foi.

    Les deux sources se contredisent parfois : `BEHR_JavelinBallisticCannon_S7`
    porte le tag `BEHR LaserCannon`. Le nom de classe est généré
    systématiquement à partir du fichier de définition, le tag est saisi à la
    main — on croit le premier et on se rabat sur le second.
    """
    def cherche(source: str) -> tuple[str | None, str | None]:
        bas = source.lower()
        return (
            next((v for k, v in _WEAPON_CLASSES.items() if k in bas), None),
            next((v for k, v in _WEAPON_KINDS.items() if k in bas), None),
        )

    classe, genre = cherche(class_name or "")
    secours_classe, secours_genre = cherche(tags or "")
    return classe or secours_classe, genre or secours_genre


def _usability(tags: str | None, class_name: str | None) -> tuple[int, int, int]:
    """Montable par un joueur, ou pièce de décor technique ?"""
    bas_tags = (tags or "").lower()
    bas_class = (class_name or "").lower()
    return (
        int("flightready" in bas_tags),
        int("weaponmountusable" in bas_tags),
        int(any(m in bas_class for m in _DEV_MARQUEURS)),
    )


def _lootabilite(etiquettes: list | None) -> tuple[int, str | None]:
    """Un objet peut-il apparaître en butin, et de quelle provenance.

    `entity_tag_map` porte un vocabulaire fermé : `CanGenerateAsLoot`,
    `LootableFromSuit`, `CannotGenerateAsLoot`, `Unlootable`. Mesuré — 3 823
    objets sur 6 266 étiquetés sont lootables, mais **presque tous sont de
    l'équipement personnel** : 2 boucliers de vaisseau seulement, aucun moteur
    quantique, aucune arme de vaisseau.

    L'interdiction l'emporte sur l'autorisation : un objet portant les deux
    n'est pas lootable.
    """
    noms = {t.get("name") for t in (etiquettes or []) if t.get("name")}
    if not noms:
        return 0, None
    if {"CannotGenerateAsLoot", "Unlootable", "unlootable"} & noms:
        return 0, None
    if "LootableFromSuit" in noms:
        return 1, "suit"
    if "CanGenerateAsLoot" in noms:
        return 1, "generic"
    return 0, None


def _component_stats(std: dict) -> dict | None:
    """Statistiques des composants hors armement — boucliers, moteurs quantiques.

    Elles étaient là depuis le début, dans `stdItem.Shield` et
    `stdItem.QuantumDrive` ; le premier passage ne lisait que les armes, d'où
    l'impossibilité de classer un bouclier. `flight_ready` n'y était pour rien :
    il vaut 0 sur ces types parce qu'il dérive du champ `tags`, que les
    boucliers ne portent pas — ce n'est pas un signal de non-montabilité.

    **Les refroidisseurs et générateurs y sont aussi, et le contraire avait
    été écrit ici.** Le premier passage concluait que leur performance « vit
    dans `ResourceNetwork`, dont le format n'est pas comparable d'un type à
    l'autre ». Trop pessimiste : on ne compare jamais un refroidisseur à un
    générateur, et **à l'intérieur d'un type** un seul nombre sort de
    `States[Online].Deltas` — 38 de fluide pour un Glacier, 16 de puissance
    pour un OverDrive. C'est le même piège que `refineries_audits`, où une
    source pauvre avait fait conclure à l'absence de toute une famille.
    """
    bouclier = std.get("Shield") or {}
    quantique = std.get("QuantumDrive") or {}
    reseau = _reseau_de_ressource(std)
    if not bouclier and not quantique and not reseau:
        return None

    saut = quantique.get("StandardJump") or {}
    durabilite = std.get("Durability") or {}

    # JumpRange vaut parfois 3.4e38 — le float max, autrement dit « illimité ».
    # Le stocker tel quel ferait remporter tout classement à n'importe quel
    # moteur qui le porte.
    portee = quantique.get("JumpRange")
    if portee is not None and portee > 1e30:
        portee = None

    return {
        "shield_health": bouclier.get("MaxShieldHealth"),
        "shield_regen": bouclier.get("MaxShieldRegen"),
        "shield_downed": bouclier.get("DownedDelay"),
        "qt_jump_range": portee,
        "qt_drive_speed": saut.get("DriveSpeed"),
        "qt_cooldown": saut.get("CooldownTime"),
        "qt_fuel_rate": quantique.get("QuantumFuelRequirement"),
        "health": durabilite.get("Health"),
        "mass": std.get("Mass"),
        **reseau,
    }


#: Quel type de composant consomme son Power en **pips entiers** (les
#: barres de l'interface) plutôt qu'en unités standard fractionnaires.
#: Mesuré le 2026-08-12 sur les fichiers bruts (`SPowerSegmentResourceUnit`
#: contre `SStandardResourceUnit`, docs/ANALYSE_ENERGIE.md §3) : la forme
#: normalisée des index perd le type d'unité, cette table le restitue.
#: Les armes et le quantum drive sont les seuls en unités standard.
#: `disco verifier` ré-échantillonne les fichiers bruts pour attraper un
#: patch qui déplacerait la frontière.
_TYPES_EN_PIPS = frozenset({
    "Shield", "Cooler", "PowerPlant", "Radar", "FlightController",
    "LifeSupportGenerator", "WheeledController", "WeaponMining",
    "TractorBeam", "SalvageHead", "QuantumInterdictionGenerator",
    "TowingBeam", "EMP", "Battery", "SelfDestruct",
})
_TYPES_EN_STD = frozenset({"WeaponGun", "QuantumDrive", "WeaponDefensive"})


def _lignes_reseau(std: dict) -> list[dict]:
    """Le réseau de ressources complet, une ligne par état.

    Contrairement à `_reseau_de_ressource` (qui n'extrait que le débit
    produit de l'état Online pour `item_stats`), on garde **tous** les
    états, la consommation, les paliers d'allocation et les signatures —
    c'est la matière du budget énergie et des optimiseurs de loadout.
    Les pips et les unités standard ne se mélangent jamais (§3 de
    l'analyse) : chaque valeur part dans sa colonne selon le type.
    """
    reseau = std.get("ResourceNetwork") or {}
    type_ = (std.get("Type") or "").split(".", 1)[0]
    en_pips = type_ in _TYPES_EN_PIPS
    en_std = type_ in _TYPES_EN_STD
    if not en_pips and not en_std:
        # Type hors de la table mesurée : plutôt refuser qu'inventer
        # l'unité — le contrôle `reseau_energie` de verifier comptera
        # ces absences si elles deviennent nombreuses.
        return []
    lignes = []
    for etat in reseau.get("States") or []:
        nom = etat.get("Name")
        if not nom:
            continue
        ligne: dict = {"etat": nom}
        for delta in etat.get("Deltas") or []:
            genre = (delta.get("Type") or "").lower()
            ressource = delta.get("Resource")
            if ressource == "Power" and genre in ("consumption", "conversion"):
                colonne = "pips_conso" if en_pips else "std_conso"
                ligne[colonne] = delta.get("Rate")
                if delta.get("MinimumFraction") is not None:
                    ligne["min_fraction"] = delta.get("MinimumFraction")
            if genre == "generation" and ressource == "Power":
                ligne["pips_generes"] = delta.get("Rate")
            if genre == "conversion" and delta.get("GeneratedResource"):
                ligne["ressource"] = delta.get("GeneratedResource")
                ligne["generation_std"] = delta.get("GeneratedRate")
        signature = etat.get("Signature") or {}
        if signature.get("EM") is not None:
            ligne["em"] = signature.get("EM")
        if signature.get("IR") is not None:
            ligne["ir"] = signature.get("IR")
        # Les trois paliers d'allocation, dans l'ordre croissant de pips.
        paliers = sorted((p for p in etat.get("PowerRanges") or []
                          if p.get("Start") is not None),
                         key=lambda p: p["Start"])
        for palier, prefixe in zip(paliers[:3], ("low", "med", "high")):
            ligne[f"pips_{prefixe}"] = palier.get("Start")
            ligne[f"mult_{prefixe}"] = palier.get("Modifier")
        if len(ligne) > 1:
            lignes.append(ligne)
    return lignes


def _reseau_de_ressource(std: dict) -> dict:
    """Ce qu'un refroidisseur produit, ce qu'un générateur fournit.

    Les deux vivent dans `ResourceNetwork.States`, sous l'état « Online » :
    une **conversion** pour le refroidisseur (puissance → fluide), une
    **génération** pour le générateur. Le nombre utile est le débit produit,
    et la signature EM/IR se lit au même endroit.

    On ne retient que l'état en marche : les autres décrivent l'objet éteint
    ou endommagé, et les mélanger ferait varier le classement selon un état
    que le joueur ne choisit pas.
    """
    reseau = std.get("ResourceNetwork") or {}
    etats = [e for e in (reseau.get("States") or [])
             if (e.get("Name") or "").lower() == "online"]
    if not etats:
        return {}
    etat = etats[0]

    refroidissement = puissance = None
    for delta in etat.get("Deltas") or []:
        genre = (delta.get("Type") or "").lower()
        if genre == "conversion" and delta.get("GeneratedResource") == "Coolant":
            refroidissement = delta.get("GeneratedRate")
        elif genre == "generation" and delta.get("Resource") == "Power":
            puissance = delta.get("Rate")

    signature = etat.get("Signature") or {}
    valeurs = {
        "cooling_rate": refroidissement,
        "power_rate": puissance,
        "signature_em": signature.get("EM"),
        "signature_ir": signature.get("IR"),
    }
    return {c: v for c, v in valeurs.items() if v}


def _weapon_stats(std: dict) -> dict | None:
    """Statistiques d'une arme, prises sur son mode de tir principal.

    Une arme a plusieurs modes (Single, Rapid, Charge). Le mode retenu est
    celui de plus haut DPS : c'est celui que le joueur a en tête quand il
    demande « le meilleur ».
    """
    weapon = std.get("Weapon")
    if not isinstance(weapon, dict):
        return None
    modes = [m for m in weapon.get("Modes") or [] if isinstance(m, dict)]
    if not modes:
        return None
    principal = max(modes, key=lambda m: m.get("DamagePerSecond") or 0)
    ammo = std.get("Ammunition") or {}
    # `Capacitor` porte la réserve d'énergie des armes de vaisseau à énergie ;
    # `Consumption` dit la même chose sur quelques entrées plus anciennes.
    # Aucune arme balistique n'en a — c'est le chargeur qui joue ce rôle.
    condensateur = weapon.get("Capacitor") or weapon.get("Consumption") or {}
    return {
        "cap_max": condensateur.get("MaxAmmoLoad"),
        "cap_regen": condensateur.get("MaxRegenPerSec")
                     or condensateur.get("RequestedRegenPerSec"),
        "cap_cost": condensateur.get("CostPerBullet"),
        "cap_cooldown": condensateur.get("Cooldown"),
        "ammo_per_shot": principal.get("AmmoPerShot"),
        "dps": principal.get("DamagePerSecond") or principal.get("Dps"),
        "alpha": principal.get("Alpha") or principal.get("DamagePerShot"),
        "rpm": principal.get("RoundsPerMinute") or weapon.get("RateOfFire"),
        "range": weapon.get("EffectiveRange") or ammo.get("Range"),
        "speed": ammo.get("Speed"),
        "capacity": weapon.get("Capacity") or ammo.get("Capacity"),
        "pellets": principal.get("PelletsPerShot"),
        "physical": principal.get("DpsPhysical"),
        "energy": principal.get("DpsEnergy"),
        "distortion": principal.get("DpsDistortion"),
        # Le detail de l'alpha, type par type. `Alpha` seul est leur somme, et
        # additionner des degats que l'armure resiste differemment — voire qui
        # ne touchent pas les points de vie du tout — fabrique un chiffre qui
        # n'existe pas.
        "alpha_physical": principal.get("AlphaPhysical"),
        "alpha_energy": principal.get("AlphaEnergy"),
        "alpha_thermal": principal.get("AlphaThermal"),
        "alpha_biochemical": principal.get("AlphaBiochemical"),
        "alpha_distortion": principal.get("AlphaDistortion"),
        "alpha_stun": principal.get("AlphaStun"),
        "health": (std.get("Durability") or {}).get("Health"),
        "mass": std.get("Mass"),
        "modes": ", ".join(m.get("Name") for m in modes if m.get("Name")) or None,
    }


def load_items(con: sqlite3.Connection, root: pathlib.Path,
               aliases: AliasCollector) -> int:
    """`fps-items.json` + `ship-items.json`.

    Pas `items/` : ses 11 045 fichiers exclusifs sont du mobilier (cf. audit).
    Le nom affichable vit dans `stdItem.Name` ; le `name` de premier niveau est
    souvent un placeholder.
    """
    total = 0
    seen: set[str] = set()
    for filename in ("ship-items.json", "fps-items.json"):
        path = root / filename
        if not path.exists():
            continue
        for entry in read_json(path):
            uuid = entry.get("reference")
            if not uuid or uuid in seen:
                continue
            seen.add(uuid)
            std = entry.get("stdItem") or {}
            name = std.get("Name") or entry.get("name")
            if name == PLACEHOLDER:
                name = None
            std_manu = std.get("Manufacturer")
            # Le fabricant de premier niveau est renseigné à 80 %, celui de
            # stdItem beaucoup moins. On prend le meilleur des deux.
            manufacturer = entry.get("manufacturer") or (
                std_manu.get("Name") if isinstance(std_manu, dict) else std_manu
            )
            vol, mount, dev = _usability(entry.get("tags"), entry.get("className"))
            butin, provenance = _lootabilite(entry.get("entity_tag_map"))
            # La lettre de grade et la classe d'usage vivent dans le bloc
            # descriptif, pas à la racine — et seulement sur les composants.
            descriptif = std.get("DescriptionData") or {}
            con.execute(
                "INSERT OR REPLACE INTO items "
                "(uuid, class_name, name, type, subtype, size, grade, "
                " manufacturer_name, classification, tags, rarity, "
                " grade_lettre, item_class, volume_uscu, "
                " flight_ready, mount_usable, is_dev, description, lootable, "
                " loot_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid, entry.get("className"), name,
                    entry.get("type"), entry.get("subType"),
                    entry.get("size"), str(entry.get("grade") or ""),
                    manufacturer, entry.get("classification"), entry.get("tags"),
                    # « Une armure légendaire » — il y en a deux.
                    std.get("Rarity"),
                    # La lettre du jeu et la classe d'usage. Elles ne sont
                    # renseignées que sur les composants — 336 objets — et
                    # c'est justement là qu'un joueur les cherche.
                    str(descriptif.get("Grade") or "") or None,
                    str(descriptif.get("Class") or "") or None,
                    _volume_uscu(std.get("InventoryOccupancy")),
                    vol, mount, dev,
                    std.get("DescriptionText") or std.get("Description"),
                    butin, provenance,
                ),
            )
            total += 1
            # **L'échafaudage de CIG n'a pas d'alias** — même règle que les
            # gisements `MineableRock_test_*` : cinq objets « PLACEHOLDER - … »
            # et « [PH] … » au balayage du 2026-08-07, du gabarit de vêtement.
            # La ligne reste en base, mais un joueur ne peut pas la nommer.
            nom_bas = (name or "").upper()
            if not nom_bas.startswith(("PLACEHOLDER", "[PH]")):
                aliases.add_variants("item", uuid, name, entry.get("className"))
            _charger_ports(con, uuid, std.get("Ports"))
            _charger_modificateur_accessoire(con, uuid, std)

            mecanique = _mecanique_stats(std)
            if mecanique:
                colonnes = ", ".join(mecanique)   # fermés par _mecanique_stats
                marques = ",".join("?" * (len(mecanique) + 1))
                con.execute(
                    f"INSERT OR REPLACE INTO item_stats (item_uuid, {colonnes}) "
                    f"VALUES ({marques})", (uuid, *mecanique.values()))

            armure = _armor_stats(std)
            if armure:
                # Les noms de colonne sont fermés par `_armor_stats`, jamais
                # construits depuis la source.
                colonnes = ", ".join(armure)
                marques = ",".join("?" * (len(armure) + 1))
                con.execute(
                    f"INSERT OR REPLACE INTO item_stats (item_uuid, {colonnes}) "
                    f"VALUES ({marques})", (uuid, *armure.values()))

            composant = _component_stats(std)
            if composant:
                con.execute(
                    "INSERT OR REPLACE INTO item_stats "
                    "(item_uuid, shield_health, shield_regen, shield_downed, "
                    " qt_jump_range, qt_drive_speed, qt_cooldown, qt_fuel_rate, "
                    " health, item_mass, cooling_rate, power_rate, "
                    " signature_em, signature_ir) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid, composant["shield_health"], composant["shield_regen"],
                     composant["shield_downed"], composant["qt_jump_range"],
                     composant["qt_drive_speed"], composant["qt_cooldown"],
                     composant["qt_fuel_rate"], composant["health"],
                     composant["mass"], composant.get("cooling_rate"),
                     composant.get("power_rate"), composant.get("signature_em"),
                     composant.get("signature_ir")),
                )

            for ligne_reseau in _lignes_reseau(std):
                # Colonnes fermées par `_lignes_reseau`, jamais par la source.
                etat = ligne_reseau.pop("etat")
                colonnes = ", ".join(ligne_reseau)
                marques = ",".join("?" * (len(ligne_reseau) + 2))
                con.execute(
                    f"INSERT OR REPLACE INTO item_reseau "
                    f"(item_uuid, etat, {colonnes}) VALUES ({marques})",
                    (uuid, etat, *ligne_reseau.values()))

            stats = _weapon_stats(std)
            if stats:
                classe, genre = _weapon_family(entry.get("tags"), entry.get("className"))
                con.execute(
                    "INSERT OR REPLACE INTO item_stats "
                    "(item_uuid, weapon_class, weapon_kind, dps, alpha, "
                    " rounds_per_minute, effective_range, projectile_speed, "
                    " ammo_capacity, pellets_per_shot, dps_physical, dps_energy, "
                    " dps_distortion, health, item_mass, fire_modes, "
                    " ammo_per_shot, capacitor_max, capacitor_regen, "
                    " capacitor_cost, capacitor_cooldown, "
                    " alpha_physical, alpha_energy, alpha_thermal, "
                    " alpha_biochemical, alpha_distortion, alpha_stun) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "?,?,?,?,?,?)",
                    (uuid, classe, genre, stats["dps"], stats["alpha"], stats["rpm"],
                     stats["range"], stats["speed"], stats["capacity"],
                     stats["pellets"], stats["physical"], stats["energy"],
                     stats["distortion"], stats["health"], stats["mass"],
                     stats["modes"], stats["ammo_per_shot"], stats["cap_max"],
                     stats["cap_regen"], stats["cap_cost"], stats["cap_cooldown"],
                     stats["alpha_physical"], stats["alpha_energy"],
                     stats["alpha_thermal"], stats["alpha_biochemical"],
                     stats["alpha_distortion"], stats["alpha_stun"]),
                )
                # « canon balistique », « gatling » : des façons de désigner
                # une arme qui ne figurent dans aucun nom propre.
                if classe and name:
                    aliases.add("item", uuid, f"{genre or 'arme'} {classe}",
                                source="derived", weight=0.5)
    return total


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
        "       h.installed_class, h.category, h.turret_kind "
        "FROM hardpoints h "
        "WHERE h.installed_class IS NOT NULL "
        "  AND (h.turret_kind IS NOT NULL OR h.category = 'thruster')"
    ).fetchall()
    cache: dict[str, dict | None] = {}
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
                cache[cle] = item.get("stdItem") or {}
        std = cache[cle] or {}
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
            "(ship_uuid, port_id, genre, poste, nom, class_name, pv, "
            " mult_physical, mult_energy, seuil_physical, seuil_energy) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (h["ship_uuid"], h["port_id"], genre, h["turret_kind"], nom,
             classe, durabilite.get("Health"),
             _resistance("Physical", "Multiplier", 1.0),
             _resistance("Energy", "Multiplier", 1.0),
             _resistance("Physical", "Threshold", 0.0),
             _resistance("Energy", "Threshold", 0.0)))
        total += 1
    return total


# ------------------------------------------------------------------ blueprints

def _walk_requirements(node: dict, group: dict | None = None) -> Iterator[tuple[dict, dict | None]]:
    kind = node.get("Kind")
    if kind in ("resource", "item"):
        yield node, group
        return
    nxt = node if kind == "group" else group
    for child in node.get("Children") or []:
        yield from _walk_requirements(child, nxt)


def _walk_modifiers(node: dict, group: dict | None = None) -> Iterator[tuple[dict, dict | None]]:
    """Les modificateurs pendent au **groupe** — c'est le composant (FRAME,
    EMITTER) dont la qualité fait varier la statistique, pas l'ingrédient."""
    nxt = node if node.get("Kind") == "group" else group
    for mod in node.get("Modifiers") or []:
        yield mod, nxt
    for child in node.get("Children") or []:
        yield from _walk_modifiers(child, nxt)


def load_blueprints(con: sqlite3.Connection, root: pathlib.Path,
                    aliases: AliasCollector) -> tuple[int, int]:
    """`blueprints.json`, l'index racine — pas le répertoire `blueprints/`,
    qui n'est qu'un dump XML->JSON brut aux références non résolues."""
    blueprints = ingredients = 0
    for bp in read_json(root / "blueprints.json"):
        uuid = bp.get("UUID")
        if not uuid:
            continue
        out = bp.get("Output") or {}
        dismantle = bp.get("Dismantle") or {}
        availability = bp.get("Availability") or {}
        con.execute(
            "INSERT OR REPLACE INTO blueprints "
            "(uuid, key, kind, category_uuid, output_uuid, output_class, "
            " output_name, output_type, output_subtype, output_grade, "
            " available_by_default, dismantle_seconds, dismantle_efficiency) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid, bp.get("Key"), bp.get("Kind"), bp.get("CategoryUUID"),
                out.get("UUID"), out.get("Class"), out.get("Name") or bp.get("Key"),
                out.get("Type"), out.get("Subtype"), out.get("Grade"),
                int(bool(availability.get("Default"))),
                dismantle.get("TimeSeconds"), dismantle.get("Efficiency"),
            ),
        )
        blueprints += 1

        # Le joueur nomme le produit, pas le blueprint : « comment on fabrique
        # un P6-LR », jamais « BP_CRAFT_behr_sniper_ballistic_01 ».
        aliases.add_variants("blueprint", uuid, out.get("Name"), out.get("Class"))

        for tier in bp.get("Tiers") or []:
            cur = con.execute(
                "INSERT OR REPLACE INTO blueprint_tiers "
                "(blueprint_uuid, tier_index, craft_time_seconds) VALUES (?,?,?)",
                (uuid, tier.get("TierIndex"), tier.get("CraftTimeSeconds")),
            )
            tier_id = cur.lastrowid
            requirements = tier.get("Requirements")
            if not requirements:
                continue
            rows = []
            for pos, (node, group) in enumerate(_walk_requirements(requirements)):
                kind = node["Kind"]
                rows.append((
                    tier_id, pos,
                    (group or {}).get("Key"), (group or {}).get("Name"),
                    (group or {}).get("RequiredCount"),
                    kind, node.get("UUID"), node.get("Name") or "?",
                    node.get("QuantityScu") if kind == "resource" else None,
                    node.get("Quantity") if kind == "item" else None,
                    node.get("MinQuality"),
                ))
            con.executemany(
                "INSERT INTO blueprint_ingredients "
                "(tier_id, position, group_key, group_name, required_count, "
                " ingredient_kind, ref_uuid, ref_name, quantity_scu, "
                " quantity_units, min_quality) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            ingredients += len(rows)

            # Ce qui change entre un « P6-LR 900 » et un « P6-LR 990 » : la
            # qualité du matériau interpole le multiplicateur entre ses deux
            # bornes. On garde `mult_min` et `mult_max` dans l'ordre du jeu
            # sans les réordonner — 462 modificateurs sur 5 695 décroissent,
            # et ce sont ceux dont la baisse est un gain (le recul d'une arme).
            mods = []
            for mod, group in _walk_modifiers(requirements):
                cle = mod.get("Key")
                if not cle:
                    continue
                q = mod.get("QualityRange") or {}
                r = mod.get("ModifierRange") or {}
                mods.append((
                    tier_id, (group or {}).get("Key"), (group or {}).get("Name"),
                    mod.get("UUID"), cle, mod.get("Name"),
                    q.get("Min"), q.get("Max"),
                    r.get("AtMinQuality"), r.get("AtMaxQuality"),
                    mod.get("ValueRangeType"),
                ))
            if mods:
                con.executemany(
                    "INSERT INTO blueprint_modifiers "
                    "(tier_id, group_key, group_name, uuid, cle, nom, "
                    " quality_min, quality_max, mult_min, mult_max, "
                    " interpolation) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    mods,
                )

        for pool in availability.get("RewardPools") or []:
            con.execute(
                "INSERT OR IGNORE INTO blueprint_sources "
                "(blueprint_uuid, pool_uuid, pool_key) VALUES (?,?,?)",
                (uuid, pool.get("UUID"), pool.get("Key") or "?"),
            )
        # Les blueprints sont chargés avant les contrats : c'est ce dernier qui
        # remplira contract_reward_pools et reward_pool_contents.
        for ret in dismantle.get("Returns") or []:
            con.execute(
                "INSERT INTO blueprint_dismantle_returns "
                "(blueprint_uuid, ref_uuid, ref_name, quantity_scu) VALUES (?,?,?,?)",
                (uuid, ret.get("UUID"), ret.get("Name") or "?", ret.get("QuantityScu")),
            )
    return blueprints, ingredients


# ------------------------------------------------------------------ ressources

def _location_id(system: str | None, name: str) -> str:
    """`locations.json` ne donne aucun UUID de lieu (vérifié : 0 sur 330).
    On fabrique une clé déterministe, stable d'une réingestion à l'autre."""
    return f"loc:{(system or '?').strip()}:{name.strip()}"


def load_resources(con: sqlite3.Connection, root: pathlib.Path,
                   aliases: AliasCollector) -> tuple[int, int, int]:
    resources = locations = links = 0

    for res in read_json(root / "resources" / "resources.json"):
        uuid = res.get("UUID")
        if not uuid:
            continue
        name = res.get("Name")
        con.execute(
            "INSERT OR REPLACE INTO resources (uuid, key, name, kind, tier) "
            "VALUES (?,?,?,?,?)",
            (uuid, res.get("Key"), name or res.get("Key"), res.get("Kind"), res.get("Tier")),
        )
        resources += 1
        # **Une entrée d'outillage n'a pas d'alias.** 27 gisements
        # `MineableRock_test_*` et 10 `_TEMPLATE` sont l'échafaudage de CIG,
        # pas des filons : indexés, ils ressortaient comme **choix** — le
        # journal a montré « Tu parles duquel ? Iron, …, MineableRock_test_
        # Iron ? ». La ligne reste en base (le palier de rareté se lit sur les
        # vraies entrées), seul l'alias saute : ce qu'un joueur ne peut pas
        # nommer n'a pas à être nommable.
        nom_bas = (name or "").lower()
        if "_test_" not in nom_bas and not nom_bas.endswith(("_test",
                                                            "template")):
            aliases.add_variants("resource", uuid, name, res.get("Key"))

        composition = res.get("Composition") or {}
        rows = [
            (uuid, part.get("UUID"), part.get("Key"), part.get("Name") or "?",
             part.get("MinPercentage"), part.get("MaxPercentage"), part.get("Probability"))
            for part in composition.get("Parts") or []
        ]
        if rows:
            con.executemany(
                "INSERT INTO resource_composition "
                "(deposit_uuid, part_uuid, part_key, part_name, min_pct, max_pct, "
                " probability) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            # Le nom du composant (« Quantainium (Raw) ») est une entrée de
            # résolution valable, rattachée au gisement qui le porte.
            for part in composition.get("Parts") or []:
                aliases.add("resource", uuid, part.get("Name"),
                            source="derived", weight=0.7)

    known = {r[0] for r in con.execute("SELECT uuid FROM resources")}

    for provider in read_json(root / "resources" / "locations.json"):
        pname = (provider.get("Provider") or {}).get("Name")
        loc_ids = []
        for loc in provider.get("Locations") or []:
            name = loc.get("Name")
            if not name:
                continue
            lid = _location_id(loc.get("System"), name)
            con.execute(
                "INSERT OR REPLACE INTO locations "
                "(uuid, name, system, loc_type, provider_name) VALUES (?,?,?,?,?)",
                (lid, name, loc.get("System"), loc.get("Type"), pname),
            )
            loc_ids.append(lid)
            locations += 1
            aliases.add("location", lid, name, source="canonical")

        for group in provider.get("Groups") or []:
            gname = group.get("GroupName")
            gprob = group.get("GroupProbability") or 0.0
            for deposit in group.get("Deposits") or []:
                ruuid = deposit.get("ResourceUUID")
                if ruuid not in known:
                    continue
                prob = gprob * (deposit.get("RelativeProbability") or 0.0)
                for lid in loc_ids:
                    con.execute(
                        "INSERT INTO resource_locations "
                        "(resource_uuid, location_uuid, group_name, probability) "
                        "VALUES (?,?,?,?) "
                        "ON CONFLICT (resource_uuid, location_uuid, group_name) "
                        "DO UPDATE SET probability = MAX(probability, excluded.probability)",
                        (ruuid, lid, gname, prob),
                    )
                    links += 1
    return resources, locations, links


# ------------------------------------------------------------------ commerce

def load_starmap(con: sqlite3.Connection, root: pathlib.Path,
                 aliases: AliasCollector) -> int:
    """`starmap.json` : la hiérarchie des objets célestes, jamais ouverte au
    premier passage.

    Chaque objet pointe son parent ; on remonte la chaîne jusqu'à la racine pour
    savoir de quel système il relève. C'est ce qui permet de dire « à Pyro »
    sans le deviner d'après un nom de lieu.
    """
    path = root / "starmap.json"
    if not path.exists():
        return 0

    nodes = {}
    for entry in read_json(path):
        uuid = entry.get("UUID")
        if not uuid:
            continue
        name = entry.get("Name")
        if name in (PLACEHOLDER, "<= UNINITIALIZED =>"):
            name = None
        # `Description` est renseignée sur 2 032 lieux sur 2 054 et n'était
        # pas lue : « c'est quoi Grim HEX » n'avait aucune source alors que la
        # réponse est écrite par CIG dans ce fichier. `Jurisdiction` dit qui
        # tient l'endroit — « Rough & Ready » ou « XenoThreat » ne promettent
        # pas la même tranquillité que « UEE ».
        juridiction = entry.get("Jurisdiction")
        nodes[uuid] = {
            "uuid": uuid,
            "name": name,
            "parent": entry.get("ParentUUID"),
            "type": (entry.get("Type") or {}).get("Name"),
            # « <= UNINITIALIZED => » n'est pas une description : 80 lieux le
            # portent, et le rendu l'affichait comme une prose du jeu.
            "description": _prose_utile(entry.get("Description")),
            "jurisdiction": (juridiction or {}).get("Name")
                            if isinstance(juridiction, dict) else juridiction,
            # 281 lieux portent la liste de ce qu'on y trouve, et le mot
            # `Amenities` n'apparaissait nulle part dans le projet.
            "amenities": entry.get("Amenities") or [],
        }

    def ascendance(uuid: str) -> list[dict]:
        """Chaîne du nœud jusqu'à la racine, incluse."""
        out, current, seen = [], uuid, set()
        while current and current in nodes and current not in seen:
            seen.add(current)
            out.append(nodes[current])
            current = nodes[current]["parent"]
        return out

    def racine(uuid: str) -> tuple[str | None, str | None, int]:
        seen, current, depth = set(), uuid, 0
        while True:
            node = nodes.get(current)
            if node is None or current in seen:
                break
            seen.add(current)
            parent = node["parent"]
            if not parent or parent not in nodes:
                return current, node["name"], depth
            current, depth = parent, depth + 1
        return None, None, depth

    # Un système, c'est une étoile ou un système stellaire — pas n'importe quel
    # nœud sans parent. Sans ce garde-fou, les 993 avant-postes orphelins du
    # starmap deviennent chacun leur propre « système », et l'on se retrouve à
    # annoncer « 51 lieux dans Mining Base #UU6-1EI ».
    RACINES_VALIDES = {"Star", "SolarSystem"}

    rows = []
    for uuid, node in nodes.items():
        sys_uuid, sys_name, depth = racine(uuid)
        if sys_uuid and nodes[sys_uuid]["type"] not in RACINES_VALIDES:
            sys_uuid = sys_name = None
        chaine = ascendance(uuid)
        parent = chaine[1] if len(chaine) > 1 else None
        # « Stanton / Crusader / Yela » : de la racine vers la feuille, comme
        # le joueur se représente l'espace.
        path = " / ".join(n["name"] for n in reversed(chaine) if n["name"]) or None
        rows.append((uuid, node["name"], node["parent"],
                     parent["name"] if parent else None,
                     parent["type"] if parent else None,
                     node["type"], sys_uuid, sys_name, path, depth,
                     node.get("description"), node.get("jurisdiction")))
        # Les stations, avant-postes et points de saut n'avaient pas d'alias :
        # seuls les corps célestes et les zones d'atterrissage en recevaient.
        # Conséquence mesurée — « où se trouve Grim HEX » ne résolvait aucun
        # lieu et partait chercher une commodité, comme Klescher, Everus Harbor
        # et Ruin Station. Ce sont pourtant les endroits qu'un joueur nomme le
        # plus souvent : c'est là qu'il atterrit.
        #
        # Le risque de pollution est nul pour les autres outils : le résolveur
        # filtre par type d'entité, et seuls les outils de lieu demandent
        # « starmap ». Les `Anomaly` sont les points de saut, qui manquaient
        # aussi.
        if node["name"] and node["type"] in (
            "Planet", "Moon", "Star", "SolarSystem", "LandingZone",
            "PointOfInterest", "Manmade", "Manmade_VisibleOnInteraction",
            "Outpost", "Outpost_InvalidQT", "Anomaly", "NavPoint",
            "Asteroid", "Asteroid_ValidQT",
        ):
            aliases.add("starmap", uuid, node["name"], source="canonical")

    con.executemany(
        "INSERT OR REPLACE INTO starmap "
        "(uuid, name, parent_uuid, parent_name, parent_type, type_name, "
        " system_uuid, system_name, path, depth, description, jurisdiction) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )

    # Les services d'un lieu. Le jeu écrit « Hangar L » dans `Name` et
    # « Hangar (L) » dans `DisplayName` : on garde les deux, la seconde forme
    # étant celle qui s'affiche.
    services = []
    for uuid, node in nodes.items():
        for a in node.get("amenities") or []:
            if not isinstance(a, dict) or not a.get("Name"):
                continue
            services.append((uuid, a.get("UUID"), a["Name"],
                             a.get("DisplayName") or a["Name"]))
    if services:
        con.executemany(
            "INSERT OR REPLACE INTO location_amenities "
            "(location_uuid, amenity_uuid, name, display_name) VALUES (?,?,?,?)",
            services)

    _load_positions(con, root)

    # Les lieux de `resources/locations.json` n'ont pas d'UUID : on les
    # rattache au starmap par leur nom, ce qui leur donne leur position dans
    # l'arborescence.
    con.execute(
        """
        UPDATE locations SET system = COALESCE(
          (SELECT s.system_name FROM starmap s
            WHERE s.name = locations.name AND s.system_name IS NOT NULL LIMIT 1),
          system)
        """
    )
    return len(rows)


def _load_positions(con: sqlite3.Connection, root: pathlib.Path) -> int:
    """Coordonnées et points de saut, depuis `starmap_positions.json`.

    Ce fichier était **entièrement ignoré** : 1 771 positions et les liaisons
    inter-systèmes, sur le disque depuis le premier clone. Sans lui, « c'est
    loin ? » n'a pas de réponse, et une route commerciale ne peut pas être
    départagée d'une autre par le trajet qu'elle impose.

    L'appariement se fait par UUID avec `starmap`, à 98 % — les 2 % restants
    sont des entités positionnées que la hiérarchie ne connaît pas, et qu'on
    laisse tomber plutôt que d'inventer un parent.
    """
    path = root / "starmap_positions.json"
    if not path.exists():
        return 0
    donnees = read_json(path)
    if not isinstance(donnees, dict):
        return 0

    positions = [
        (e.get("x"), e.get("y"), e.get("z"),
         1 if e.get("qt_valid") else 0, e["uuid"])
        for e in donnees.get("entities", []) if e.get("uuid")
    ]
    con.executemany(
        "UPDATE starmap SET x = ?, y = ?, z = ?, qt_valid = ? WHERE uuid = ?",
        positions,
    )

    con.executemany(
        "INSERT OR REPLACE INTO jump_points "
        "(entry_uuid, exit_uuid, entry_system, exit_system, fuel_cost) "
        "VALUES (?,?,?,?,?)",
        [(c.get("entry_uuid"), c.get("exit_uuid"),
          (c.get("entry_system") or "").capitalize(),
          (c.get("exit_system") or "").capitalize(),
          c.get("fuel_cost"))
         for c in donnees.get("connections", [])
         if c.get("entry_system") and c.get("exit_system")],
    )
    _completer_sauts_par_gateways(con)
    return len(positions)


def _completer_sauts_par_gateways(con: sqlite3.Connection) -> None:
    """Les liaisons que les stations prouvent et que la source ne liste pas.

    **Le saut Stanton ↔ Nyx existe en jeu et `connections` ne le porte
    pas** — signalé par l'utilisateur le 2026-08-12, vérifié : la liste de
    scunpacked s'arrête à Nyx→Pyro et Pyro→Stanton, mais le starmap place
    « Nyx Gateway » dans Stanton et « Stanton Gateway » dans Nyx, deux
    destinations quantiques valides. Une paire de Gateways réciproques —
    « X Gateway » dans Y **et** « Y Gateway » dans X, les deux systèmes
    jouables — matérialise le saut : ce sont les quais des deux bouts.

    Le coût de carburant n'est pas publié pour ces liaisons ; les deux
    sauts publiés valent 0,24 chacun, même mécanique — on aligne, et le
    choix est écrit ici plutôt que déduit en silence. Terra Gateway
    n'apparie rien : Terra n'est pas un système de la base, la liaison
    reste dehors sans liste à tenir à la main.
    """
    cout_publie = con.execute(
        "SELECT MAX(fuel_cost) FROM jump_points").fetchone()[0] or 0.24
    gateways = con.execute(
        "SELECT s.uuid, s.name, sys.name AS systeme FROM starmap s "
        "JOIN starmap sys ON sys.uuid = s.system_uuid "
        "WHERE s.name LIKE '% Gateway' AND s.type_name = 'Manmade'"
    ).fetchall()
    par_cle = {(g["name"].rsplit(" Gateway", 1)[0].lower(),
                g["systeme"].lower()): g for g in gateways}
    systemes = {r["name"].lower(): r["name"] for r in con.execute(
        "SELECT name FROM starmap WHERE type_name IN ('Star', 'SolarSystem') "
        "AND name IS NOT NULL")}
    for (destination, systeme), quai in par_cle.items():
        retour = par_cle.get((systeme, destination))
        if retour is None or destination not in systemes:
            continue
        existe = con.execute(
            "SELECT 1 FROM jump_points WHERE "
            "(LOWER(entry_system)=? AND LOWER(exit_system)=?) OR "
            "(LOWER(entry_system)=? AND LOWER(exit_system)=?)",
            (systeme, destination, destination, systeme)).fetchone()
        if existe:
            continue
        con.execute(
            "INSERT OR REPLACE INTO jump_points "
            "(entry_uuid, exit_uuid, entry_system, exit_system, fuel_cost) "
            "VALUES (?,?,?,?,?)",
            (quai["uuid"], retour["uuid"],
             systemes.get(systeme, systeme.capitalize()),
             systemes.get(destination, destination.capitalize()),
             cout_publie))


def load_commerce(con: sqlite3.Connection, root: pathlib.Path,
                  aliases: AliasCollector) -> tuple[int, int]:
    commodities = shops = 0
    for c in read_json(root / "resources" / "commodities.json"):
        uuid = c.get("UUID")
        if not uuid:
            continue
        name = c.get("Name")
        if name == PLACEHOLDER:
            name = c.get("Key")
        con.execute(
            "INSERT OR REPLACE INTO commodities "
            "(uuid, key, name, description, density_g_per_cc, groups, "
            " refined_name, refined_uuid) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uuid, c.get("Key"), name or c.get("Key"),
             None if c.get("Description") == PLACEHOLDER else c.get("Description"),
             c.get("DensityGPerCc"),
             json.dumps(c.get("CommodityGroups") or [], ensure_ascii=False),
             # Le rapprochement brut → raffiné se faisait par le nom ; le jeu
             # le dit, avec un UUID. « Raw Ice » → « Pressurized Ice » est
             # justement le cas que le nom seul ne donne pas.
             c.get("RefinedVersionName"), c.get("RefinedVersionUUID")),
        )
        commodities += 1
        # Le jeu s'appelle lui-même « Quantanium » en clé et « Quantainium » en
        # nom : les deux méritent d'être des alias.
        aliases.add_variants("commodity", uuid, name, c.get("Key"))
        aliases.add("commodity", uuid, c.get("Key"), source="derived", weight=0.9)

    path = root / "resources" / "commodity_trade_locations.json"
    if not path.exists():
        return commodities, shops

    seen_shops: set[str] = set()
    for entry in read_json(path):
        cuuid = entry.get("CommodityUUID")
        for direction, key in (("sold_at", "SoldAt"), ("bought_at", "BoughtAt")):
            for place in entry.get(key) or []:
                suuid = place.get("TradeLocationUUID")
                if not suuid:
                    continue
                if suuid not in seen_shops:
                    con.execute(
                        "INSERT OR REPLACE INTO shops "
                        "(uuid, class_name, display_name, starmap_object_uuid) "
                        "VALUES (?,?,?,?)",
                        (suuid, place.get("TradeLocationClassName"),
                         place.get("TradeLocationDisplayName"),
                         place.get("StarmapObjectUUID")),
                    )
                    seen_shops.add(suuid)
                    shops += 1
                    aliases.add_variants("shop", suuid,
                                         place.get("TradeLocationDisplayName"),
                                         place.get("TradeLocationClassName"))
                if cuuid:
                    con.execute(
                        "INSERT OR IGNORE INTO commodity_shops "
                        "(commodity_uuid, shop_uuid, direction) VALUES (?,?,?)",
                        (cuuid, suuid, direction),
                    )
    return commodities, shops


# ------------------------------------------------------------------ missions

# 1 SCU = 100 cSCU = 1 000 mSCU = 1 000 000 µSCU. Le jeu emploie surtout µSCU.
_UNITES_SCU = {"SCU": 1_000_000, "cSCU": 10_000, "mSCU": 1_000, "µSCU": 1}


def _volume_uscu(occupancy) -> int | None:
    """La place qu'occupe un objet, en µSCU entiers.

    On lit `SCUConverted` et son `Unit`, jamais le champ `SCU` : celui-ci est
    arrondi à **0** pour tout ce qui tient sous le SCU, c'est-à-dire pour
    presque tout le catalogue. Un pistolet Coda y vaut 0 alors qu'il occupe
    2 500 µSCU. Prendre `SCU` aurait donné « tu en mets une infinité ».

    Une unité inconnue rend `None` plutôt qu'un chiffre faux : mieux vaut ne
    pas répondre que répondre à côté d'un facteur mille.
    """
    volume = (occupancy or {}).get("Volume") if isinstance(occupancy, dict) else None
    if not isinstance(volume, dict):
        return None
    facteur = _UNITES_SCU.get(volume.get("Unit"))
    valeur = volume.get("SCUConverted")
    if facteur is None or not isinstance(valeur, (int, float)):
        return None
    return round(valeur * facteur)


def _charger_ports(con: sqlite3.Connection, uuid: str, ports) -> None:
    """Les emplacements déclarés par un objet, un par type accepté.

    Un port peut accepter plusieurs types — on écrit une ligne par type plutôt
    qu'une chaîne à découper à la lecture : c'est ce qui rend la question
    inverse (« qu'est-ce qui va dans ce port ») indexable.

    Les ports sans type (`item_grab`, la prise en main) ne servent à rien ici
    et ne sont pas écrits.
    """
    if not isinstance(ports, list):
        return
    for port in ports:
        if not isinstance(port, dict):
            continue
        for accepte in (port.get("Types") or []):
            if not accepte:
                continue
            # Les tags exigés partent en une chaîne séparée par des espaces,
            # comme `items.tags` : les deux se comparent, autant qu'ils
            # s'écrivent pareil.
            exiges = [t for t in (port.get("RequiredTags") or []) if t]
            con.execute(
                "INSERT INTO item_ports (item_uuid, port_name, accepted, "
                " min_size, max_size, required_tags) VALUES (?,?,?,?,?,?)",
                (uuid, port.get("PortName"), accepte,
                 port.get("MinSize"), port.get("MaxSize"),
                 " ".join(exiges) or None))


def _bool(valeur) -> int | None:
    """Un drapeau amont en 0/1, et **None quand il est absent**.

    La distinction compte : 36 factions sur 74 n'ont pas de `Properties`, donc
    pas de `Lawful`. Les ranger en « pas lawful » les ferait passer pour des
    hors-la-loi — ce que Rough & Ready n'est pas, faute d'information.
    """
    return None if valeur is None else int(bool(valeur))


def load_manufacturers(con: sqlite3.Connection, root: pathlib.Path,
                       aliases: AliasCollector) -> int:
    """141 constructeurs, dont 116 décrits. Fichier jamais ouvert jusqu'ici.

    Le code est un alias à part entière : « KSAR » est ce que porte la fiche
    d'un objet, et un joueur peut très bien le taper. Les deux se résolvent.
    """
    total = 0
    for m in read_json(root / "manufacturers.json"):
        uuid = m.get("Reference")
        nom = m.get("Name")
        if not uuid or not nom:
            continue
        con.execute(
            "INSERT OR REPLACE INTO manufacturers (uuid, code, name, description) "
            "VALUES (?,?,?,?)",
            (uuid, m.get("Code"), nom, (m.get("Description") or "").strip() or None))
        aliases.add_variants("manufacturer", uuid, nom)
        # Certains constructeurs ont un code identique à leur nom (« 987 ») :
        # `add` dédoublonne, inutile de tester.
        if m.get("Code"):
            aliases.add("manufacturer", uuid, m["Code"],
                        source="derived", weight=0.9)
        total += 1
    return total


def load_factions(con: sqlite3.Connection, root: pathlib.Path,
                  aliases: AliasCollector) -> int:
    total = 0
    for path in sorted((root / "factions").glob("*.json")):
        f = read_json(path)
        uuid = f.get("UUID")
        if not uuid:
            continue
        # `Reputation.Properties` porte l'état civil — et une **seconde**
        # description, plus longue que celle de la racine sur 12 factions.
        # Prendre la plus riche des deux plutôt que la première venue.
        proprietes = ((f.get("Reputation") or {}).get("Properties") or {})
        descriptions = [d for d in (f.get("Description"),
                                    proprietes.get("Description")) if d]
        description = max(descriptions, key=len) if descriptions else None

        con.execute(
            "INSERT OR REPLACE INTO factions "
            "(uuid, key, name, faction_type, default_reaction, description, "
            " headquarters, founded, leadership, area, focus, lawful, "
            " able_to_arrest, polices_criminality, polices_trespass, "
            " no_legal_rights) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, path.stem, f.get("Name") or path.stem, f.get("FactionType"),
             f.get("DefaultReaction"), description,
             proprietes.get("Headquarters"), proprietes.get("Founded"),
             proprietes.get("Leadership"), proprietes.get("Area"),
             proprietes.get("Focus"), _bool(proprietes.get("Lawful")),
             _bool(f.get("AbleToArrest")), _bool(f.get("PolicesCriminality")),
             _bool(f.get("PolicesLawfulTrespass")), _bool(f.get("NoLegalRights"))),
        )
        total += 1
        aliases.add_variants("faction", uuid, f.get("Name"), path.stem)
    return total


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


# « Very Low » avant « Low » : sans cet ordre, l'alternation retiendrait
# « Low » dans « Very Low » et confondrait le plus lent avec le simplement lent.
_NIVEAUX = {"very low": 1, "low": 2, "moderate": 3, "high": 4}
_DETAIL_METHODE = re.compile(
    r"(very low|low|moderate|high)\s+speed\s*//\s*"
    r"(very low|low|moderate|high)\s+cost\s*//\s*"
    r"(very low|low|moderate|high)\s+yield", re.IGNORECASE)


def load_methodes_de_raffinage(con: sqlite3.Connection,
                               labels: dict[str, str]) -> int:
    """Les neuf méthodes de raffinage et leurs trois notes.

    Demande de l'utilisateur : « quelle est la meilleure technique de
    raffinerie selon des critères ». Le jeu publie les notes lui-même, sous
    `refinery_ui_ProcessingType_<clé>_Details` — « Low Speed // High Cost //
    High Yield ».

    **Le segment de clé ment.** `FastCareful` porte « Low Speed » : le nom a
    vieilli, le détail a été mis à jour. On n'analyse donc que `_Details`.
    """
    lignes = []
    for cle, valeur in labels.items():
        nu = cle.removesuffix(",P")
        if not nu.startswith("refinery_ui_ProcessingType_") or "_" in nu[27:]:
            continue
        segment = nu[len("refinery_ui_ProcessingType_"):]
        if not valeur or valeur == PLACEHOLDER:
            continue

        def libelle(suffixe: str) -> str | None:
            for essai in (f"{nu}_{suffixe}", f"{nu}_{suffixe},P"):
                if labels.get(essai) and labels[essai] != PLACEHOLDER:
                    return labels[essai]
            return None

        notes = _DETAIL_METHODE.search(libelle("Details") or "")
        lignes.append((
            segment, valeur, libelle("Desc"),
            *( [_NIVEAUX[g.lower()] for g in notes.groups()] if notes
               else [None, None, None] ),
        ))

    con.executemany(
        "INSERT OR REPLACE INTO refinery_methods "
        "(cle, nom_en, description_en, vitesse, cout, rendement) "
        "VALUES (?,?,?,?,?,?)", lignes)
    return len(lignes)


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


# Les six axes de résistance d'une armure. La valeur est un objet
# `{Multiplier, Threshold}` : il faut descendre d'un niveau pour la voir, et
# les six `Threshold` valent 0 sur les 2 271 — variance nulle, rien à ingérer.
_AXES_ARMURE = ("Physical", "Energy", "Distortion",
                "Thermal", "Biochemical", "Stun")


def _charger_modificateur_accessoire(con, uuid: str, std: dict) -> None:
    """Ce qu'un accessoire fait à l'arme qui le porte.

    116 accessoires publient ces multiplicateurs dans
    `stdItem.WeaponModifier.WeaponStats.Base`, et rien ne les lisait : « et
    si je mets un silencieux » restait sans réponse.

    **Tous les silencieux ne se valent pas**, mesuré le 2026-08-10 : le Tacit
    coûte 8 % de dégâts, le Stoic rien du tout — il ne baisse que le bruit.
    Un joueur qui dit « le silencieux » suppose le contraire, et c'est
    exactement le genre d'approximation qu'un outil doit corriger.

    **En `UPDATE`, pas en `INSERT OR REPLACE`.** Les autres chargeurs de
    statistiques écrivent la ligne entière : passer après eux avec un
    `REPLACE` effacerait leurs colonnes, et passer avant ferait effacer
    les nôtres. C'est le seul ordre qui survit aux deux.
    """
    base = ((std.get("WeaponModifier") or {}).get("WeaponStats")
            or {}).get("Base") or {}
    degats = _nombre(base.get("DamageMultiplier"))
    bruit = _nombre(base.get("SoundRadiusMultiplier"))
    if degats is None and bruit is None:
        return
    con.execute(
        "INSERT INTO item_stats (item_uuid, damage_multiplier, sound_multiplier) "
        "VALUES (?,?,?) ON CONFLICT(item_uuid) DO UPDATE SET "
        "damage_multiplier=excluded.damage_multiplier, "
        "sound_multiplier=excluded.sound_multiplier",
        (uuid, degats, bruit))


def _armor_stats(std: dict) -> dict | None:
    """Statistiques d'une armure personnelle — 2 416 objets jamais lus.

    Même piège que les refroidisseurs, à trente fois l'échelle : le joueur ne
    voit aucune frontière, parce que rien ne lève. « La meilleure armure pour
    Pyro » rendait simplement une liste vide.
    """
    suit = std.get("SuitArmor") or {}
    casque = std.get("Helmet") or {}
    temp = std.get("TemperatureResistance") or {}
    rads = std.get("RadiationResistance") or {}
    gforce = std.get("GForceResistance") or {}
    if not (suit or casque.get("AtmosphereCapacity") is not None):
        return None

    resist = suit.get("DamageResistance") or {}
    valeurs = {}
    for axe in _AXES_ARMURE:
        bloc = resist.get(axe)
        # `Impact` est un nombre nu, les six autres des objets : on ne lit que
        # ceux dont on connaît la forme plutôt que de deviner.
        valeurs[f"armor_{axe.lower()}"] = (
            _nombre(bloc.get("Multiplier")) if isinstance(bloc, dict) else None)

    signature = suit.get("Signature") or {}
    valeurs.update({
        "temp_min": _nombre(temp.get("Minimum")),
        "temp_max": _nombre(temp.get("Maximum")),
        "radiation_max": _nombre(rads.get("MaximumRadiationCapacity")),
        "radiation_rate": _nombre(rads.get("RadiationDissipationRate")),
        "gforce_resistance": _nombre(gforce.get("Value")),
        "oxygene": _nombre(casque.get("AtmosphereCapacity")),
        "signature_em": _nombre(signature.get("Electromagnetic")),
        "signature_ir": _nombre(signature.get("Infrared")),
        "item_mass": _nombre(std.get("Mass")),
    })
    return valeurs if _porte_une_stat(valeurs) else None


def _porte_une_stat(valeurs: dict) -> bool:
    """Y a-t-il autre chose que la masse ?

    **La masse ne fait pas une fiche.** `stdItem.Mass` est renseignée sur
    presque tout le catalogue : accepter une ligne qui ne porte qu'elle a fait
    tomber le nombre d'objets sans statistique de 9 500 à **0**, ce qui vide de
    son sens la question « cet objet a-t-il des statistiques » — et le balayage
    s'en sert pour savoir quoi vérifier.
    """
    return any(v is not None for cle, v in valeurs.items() if cle != "item_mass")


def _nombre(valeur) -> float | None:
    """Un nombre, ou rien. La source n'a pas toujours la forme attendue :
    `Missile.ExplosionRadius` est un couple `{Minimum, Maximum}` sur 67
    missiles sur 68 et un nombre nu sur le dernier. Un `INSERT` reçoit alors
    un dict et lève au milieu de l'ingestion — mieux vaut refuser ce qu'on ne
    sait pas lire que parier sur une forme."""
    return valeur if isinstance(valeur, (int, float)) and not isinstance(valeur, bool) else None


def _mecanique_stats(std: dict) -> dict | None:
    """Propulseurs, réservoirs et missiles — trois familles à zéro statistique.

    1 266 propulseurs pour 10 lignes de stats, 363 réservoirs pour 0, 68
    missiles pour 0. Le missile est le cas le plus net : « quel missile a la
    plus grande portée » n'avait **aucune** source, et `Missile.Distance` vaut
    20 580 mètres sur le premier venu.

    Même famille que les refroidisseurs et l'armure : rien ne lève, la liste
    est simplement vide, et le joueur ne voit pas la frontière.
    """
    thr = std.get("Thruster") or {}
    res = (std.get("ResourceContainer") or {}).get("Capacity") or {}
    mis = std.get("Missile") or {}
    gcs = mis.get("GCS") or {}
    # Le rayon est un couple sur 67 missiles sur 68 : on retient le
    # **maximum**, celui qui décide de la distance de sécurité.
    rayon = mis.get("ExplosionRadius")
    if isinstance(rayon, dict):
        rayon = rayon.get("Maximum") or rayon.get("Minimum")

    valeurs = {
        "poussee": _nombre(thr.get("ThrustCapacity")) or None,
        "conso_par_10kn": _nombre(thr.get("FuelBurnRatePer10KNewton")),
        "capacite_scu": _nombre(res.get("Value")),
        "degats_missile": _nombre(mis.get("DamageTotal")),
        "rayon_explosion": _nombre(rayon),
        "portee_missile": _nombre(mis.get("Distance")),
        "vitesse_missile": _nombre(gcs.get("LinearSpeed")),
        # `IsDumbMissile` dit l'inverse de ce qu'on affiche : on stocke le
        # guidage, pas son absence, pour que la colonne se lise seule.
        "guidage": (0 if gcs.get("IsDumbMissile") else 1) if gcs else None,
        "item_mass": _nombre(std.get("Mass")),
    }
    return valeurs if _porte_une_stat(valeurs) else None


def load_trade_flows(con: sqlite3.Connection, root: pathlib.Path) -> int:
    """`trade_locations.json` — ce qu'un lieu produit et ce qu'il **consomme**.

    Le fichier avait été écarté sur ses `ProducesTags`, et l'argument tenait.
    Mais `ConsumesTags` couvre 849 lieux et répond à une question que rien ne
    couvrait : **où écouler une cargaison**. La production dit où ça sort, la
    consommation dit où ça se vend.

    Deux prudences mesurées :

    - sur 102 tags de consommation distincts, **36 seulement portent un nom de
      commodité** ; les plus fréquents sont des catégories (Supplies 913,
      Common 679, Food 642). On garde le tag brut et on ne résout la commodité
      que lorsqu'elle existe, plutôt que de faire passer une catégorie pour une
      marchandise ;
    - la liste `Negative` — 276 lieux en consommation — dit ce qu'un site
      **refuse**. La confondre avec `Positive` enverrait vendre là où personne
      n'achète.

    Appelée après `load_commerce`, dont elle a besoin pour apparier les noms.
    """
    path = root / "trade_locations.json"
    if not path.exists():
        return 0

    from ..normalize import normalize

    commodites = {normalize(nom): uuid for nom, uuid in con.execute(
        "SELECT name, uuid FROM commodities WHERE name IS NOT NULL")}

    lignes = []
    for lieu in read_json(path):
        uuid = lieu.get("UUID")
        if not uuid or lieu.get("Disabled"):
            continue
        nom = lieu.get("DisplayName") or lieu.get("ClassName")
        for champ, sens in (("ConsumesTags", "consomme"), ("ProducesTags", "produit")):
            bloc = lieu.get(champ) or {}
            for liste, refuse in (("Positive", 0), ("Negative", 1)):
                for tag in bloc.get(liste) or []:
                    tag_nom = tag.get("Name")
                    if not tag_nom:
                        continue
                    lignes.append((uuid, nom, sens, refuse, tag.get("UUID"),
                                   tag_nom, commodites.get(normalize(tag_nom))))
    if not lignes:
        return 0
    con.executemany(
        "INSERT OR REPLACE INTO trade_flows "
        "(location_uuid, location_name, sens, refuse, tag_uuid, tag_name, "
        " commodity_uuid) VALUES (?,?,?,?,?,?,?)", lignes)
    # Relire ce qui est entré : la clé primaire dédoublonne, et le compte de
    # `lignes` masquerait l'écart.
    return con.execute("SELECT COUNT(*) FROM trade_flows").fetchone()[0]


def load_contenances(con: sqlite3.Connection, root: Path) -> int:
    """Combien on peut ranger **dans** un objet, en µSCU.

    **La trouvaille de l'audit du 2026-08-13**
    (`docs/AUDIT_SCUNPACKED_2026-08-13.md`), demandé par l'utilisateur :
    « j'ai bien l'impression qu'il y a encore des secrets à livrer ». Il
    avait raison. 2 073 objets publient leur contenance, et **aucun index
    racine ne la porte** — mesuré, 0 sur 10 804. Elle n'existe que dans
    les fichiers individuels de `items/`, sous le `Raw` du moteur :

        Raw.Entity.Components.SCItemInventoryContainerComponentParams
           .inventoryContainer.inventoryType.InventoryClosedContainerType
           .capacity.SMicroCargoUnit.microSCU

    **À ne pas confondre avec `volume_uscu`**, qui est la place que
    l'objet prend. Ici c'est ce qu'il avale : un sac Novikov porte
    0,180 SCU quand le plus petit en porte 0,054 — plus du triple, donc
    un vrai critère de choix.

    On filtre sur la sous-chaîne avant de parser : 21 849 fichiers pour
    3,4 Go, et le JSON complet de chacun coûterait cent fois plus cher
    que le test. Mesuré : 10 s pour le lot entier.
    """
    dossier = root / "items"
    if not dossier.is_dir():
        return 0
    connus = {ligne[0].lower(): ligne[1] for ligne in con.execute(
        "SELECT LOWER(class_name), uuid FROM items WHERE class_name IS NOT NULL")}
    total = 0
    for chemin in dossier.glob("*.json"):
        try:
            brut = chemin.read_text(encoding="utf-8")
        except OSError:
            continue
        if "microSCU" not in brut or "InventoryContainer" not in brut:
            continue
        try:
            donnees = json.loads(brut)
        except ValueError:
            continue
        item = donnees.get("Item") or {}
        classe = str(item.get("className") or chemin.stem).lower()
        uuid = connus.get(classe)
        if uuid is None:
            continue          # objet hors catalogue : mobilier, décor, test
        composants = ((donnees.get("Raw") or {}).get("Entity") or {}
                      ).get("Components") or {}
        conteneur = (composants.get(
            "SCItemInventoryContainerComponentParams") or {}
        ).get("inventoryContainer") or {}
        capacite = (((conteneur.get("inventoryType") or {}).get(
            "InventoryClosedContainerType") or {}).get("capacity") or {})
        micro = (capacite.get("SMicroCargoUnit") or {}).get("microSCU")
        if not micro:
            continue
        con.execute("UPDATE items SET contenance_uscu = ? WHERE uuid = ?",
                    (int(micro), uuid))
        total += 1
    return total
