"""Les statistiques d'un objet : armes, armures, composants, réseau d'énergie, modificateurs d'accessoire.

Séparé du catalogue parce qu'`objets.py` dépassait les 800 lignes que l'US se donne pour borne — et parce que ce sont deux gestes différents : ranger un objet, et le chiffrer.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
from .socle import _nombre


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
