"""Repli du vocabulaire de ports scunpacked vers un vocabulaire fermé.

Sans cette table, chaque requête dupliquerait un `port_type IN (…)` de vingt
lignes. Avec elle, « les points d'emport d'armes du Gladius » devient un
`WHERE category IN (…)`.

Règle non négociable : **aucun repli silencieux**. Un type inconnu fait échouer
l'ingestion. C'est délibéré — si un patch introduit un `port_type` non mappé et
qu'on le range dans « other » par défaut, « les armes du Gladius » renvoie une
liste vide sans la moindre erreur. C'est exactement la retombée muette que le
§3 du brief demande de rendre bruyante.

Vocabulaire relevé le 2026-08-02 sur les 316 vaisseaux de la 4.9.0 :
119 types distincts, profondeur d'arbre maximale 4.
"""

from __future__ import annotations

# Du plus spécifique au plus générique. Départage un port qui accepte
# plusieurs types.
PRIORITY = [
    "weapon",
    "weapon_mount",
    "missile_rack",
    "missile",
    "countermeasure",
    "weapon_attachment",
    "shield",
    "power",
    "cooler",
    "qdrive",
    "thruster",
    "fuel",
    "radar",
    "armor",
    "cargo",
    "mining",
    "salvage",
    "utility",
    "docking",
    "life_support",
    "controller",
    "personal_gear",
    "cosmetic",
    "interior",
    "structure",
    "misc",
]

CATEGORIES: dict[str, str] = {
    # -- armement pilote
    "WeaponGun": "weapon",
    "WeaponMining": "weapon",
    "Turret": "weapon_mount",
    "TurretBase": "weapon_mount",
    "UtilityTurret": "weapon_mount",
    "WeaponMount": "weapon_mount",
    "MissileLauncher": "missile_rack",
    "GroundVehicleMissileLauncher": "missile_rack",
    "BombLauncher": "missile_rack",
    "Missile": "missile",
    "Bomb": "missile",
    "WeaponDefensive": "countermeasure",
    # Internes d'une arme (canon, mécanisme de tir, ventilation) : ce sont des
    # sous-emplacements de l'arme, pas des points d'emport du vaisseau.
    "WeaponAttachment": "weapon_attachment",
    "AmmoBox": "weapon_attachment",
    "WeaponRegenPool": "power",
    # -- composants
    "Shield": "shield",
    "PowerPlant": "power",
    "Powerplant - Power": "power",
    "Battery": "power",
    "Cooler": "cooler",
    "QuantumDrive": "qdrive",
    "QuantumFuelTank": "qdrive",
    "JumpDrive": "qdrive",
    "QuantumInterdictionGenerator": "qdrive",
    "MainThruster": "thruster",
    "ManneuverThruster": "thruster",
    "FuelTank": "fuel",
    "FuelIntake": "fuel",
    "ExternalFuelTank": "fuel",
    "Armor": "armor",
    # -- détection
    "Radar": "radar",
    "Scanner": "radar",
    "Sensor": "radar",
    "Ping": "radar",
    "TargetSelector": "radar",
    "Transponder": "radar",
    "Avionics": "radar",
    # -- métiers
    "Cargo": "cargo",
    "CargoGrid": "cargo",
    "Container": "cargo",
    "MiningController": "mining",
    "MiningModifier": "mining",
    "ToolArm": "mining",
    "SalvageController": "salvage",
    "SalvageFieldEmitter": "salvage",
    "SalvageFieldSupporter": "salvage",
    "SalvageFillerStation": "salvage",
    "SalvageHead": "salvage",
    "SalvageInternalStorage": "salvage",
    "SalvageModifier": "salvage",
    "TractorBeam": "utility",
    "TowingBeam": "utility",
    "EMP": "utility",
    "DockingCollar": "docking",
    "DockingAnimator": "docking",
    # -- vie à bord
    "LifeSupportGenerator": "life_support",
    "LifeSupportSystem": "life_support",
    "LifeSupportTank": "life_support",
    "GravityGenerator": "life_support",
    # -- pilotage et contrôleurs
    "AirTrafficController": "controller",
    "CapacitorAssignmentController": "controller",
    "CommsController": "controller",
    "Computer": "controller",
    "CoolerController": "controller",
    "DoorController": "controller",
    "EnergyController": "controller",
    "FlightController": "controller",
    "FuelController": "controller",
    "LandingSystem": "controller",
    "LightController": "controller",
    "MissileController": "controller",
    "SelfDestruct": "controller",
    "ShieldController": "controller",
    "WeaponController": "controller",
    "WheeledController": "controller",
    # -- équipement personnel rangé à bord
    "Char_Armor_Arms": "personal_gear",
    "Char_Armor_Feet": "personal_gear",
    "Char_Armor_Helmet": "personal_gear",
    "Char_Armor_Legs": "personal_gear",
    "Char_Armor_Torso": "personal_gear",
    "Char_Armor_Undersuit": "personal_gear",
    "Char_Body": "personal_gear",
    "Char_Clothing_Feet": "personal_gear",
    "Char_Clothing_Legs": "personal_gear",
    "Char_Clothing_Torso_0": "personal_gear",
    "Char_Clothing_Torso_1": "personal_gear",
    "Char_Clothing_Torso_2": "personal_gear",
    "FPS_Consumable": "personal_gear",
    "Suit": "personal_gear",
    # Un râtelier à fusils est du rangement, pas un point d'emport.
    "WeaponPersonal": "personal_gear",
    "Gadget": "personal_gear",
    # -- décoratif
    "Decal": "cosmetic",
    "Paints": "cosmetic",
    "Flair_Cockpit": "cosmetic",
    "Flair_Floor": "cosmetic",
    "Flair_Surface": "cosmetic",
    # -- intérieur
    "Button": "interior",
    "ControlPanel": "interior",
    "Display": "interior",
    "Door": "interior",
    "Interior": "interior",
    "Light": "interior",
    "Lightgroup": "interior",
    "MultiLight": "interior",
    "Room": "interior",
    "Seat": "interior",
    "SeatAccess": "interior",
    "SeatDashboard": "interior",
    "Usable": "interior",
    "Useable": "interior",
    # -- structure
    "AttachedPart": "structure",
    "Relay": "structure",
    "Module": "structure",
    "NOITEM_Vehicle": "structure",
    # -- fourre-tout assumé, jamais un défaut
    "AIModule": "misc",
    "Crafter": "misc",
    "MISC": "misc",
    "Misc": "misc",
    "UNDEFINED": "misc",
}

_RANK = {c: i for i, c in enumerate(PRIORITY)}

# Catégories qui répondent à « quels sont les points d'emport d'armes ».
WEAPON_CATEGORIES = ("weapon_mount", "weapon", "missile_rack", "missile", "countermeasure")


class UnknownPortType(RuntimeError):
    """Un type de port absent du dictionnaire. On échoue plutôt que de deviner."""


def categorize(installed_type: str | None, accepted: list[str]) -> str:
    """Catégorie d'un port.

    Le `Type` de l'objet monté prime : c'est lui qui distingue un support
    (`Turret`) de l'arme qu'il porte (`WeaponGun`). Pour un port vide, on
    retombe sur le contrat déclaré par `CompatibleTypes`.

    Un port sans aucune des deux sources est rangé en `misc` — il n'y en a que
    725 sur 57 759 et ils ne portent aucune information exploitable.
    """
    head = (installed_type or "").partition(".")[0].strip()
    if head:
        try:
            return CATEGORIES[head]
        except KeyError:
            raise UnknownPortType(
                f"type de port inconnu : {head!r}. Ajoute-le à CATEGORIES "
                f"(hardpoint_categories.py) plutôt que de le laisser filer."
            ) from None

    cats = []
    for raw in accepted:
        name = (raw or "").partition(".")[0].strip()
        if not name:
            continue
        try:
            cats.append(CATEGORIES[name])
        except KeyError:
            raise UnknownPortType(
                f"type accepté inconnu : {name!r}. Ajoute-le à CATEGORIES "
                f"(hardpoint_categories.py) plutôt que de le laisser filer."
            ) from None

    if not cats:
        return "misc"
    return min(cats, key=lambda c: _RANK[c])
