"""Le vocabulaire des questions — ce qui se lit dans une phrase.

Cette couche ne touche **jamais** la base. Elle traduit ce qu'un joueur écrit
en noms de colonnes, en seuils et en familles : « la plus grosse soute à
minerai » devient `ore_capacity`, « plus de 100 SCU et 4 places » devient deux
contraintes. Les fonctions de `queries` s'en servent ensuite pour interroger
SQLite.

Elle vivait au milieu des 4 400 lignes de `queries.py`, éparpillée entre les
outils qui la consomment, et c'est ce qui a permis à `detect_criteres` d'être
défini **deux fois** — les axes de raffinage écrasant silencieusement les
seuils de vaisseau, sans qu'aucun test ne bronche. Rassemblée ici, une
collision de nom se voit à la lecture.

L'ordre des tables compte, et c'est la seule contrainte de ce module :
`MOTS_DE_STAT` se construit en deux temps, la seconde moitié ayant besoin de
`_COMPONENT_STATS`.
"""

from __future__ import annotations

import re

from .normalize import normalize


# --------------------- Armes — statistiques comparables et filtre de famille

# Statistiques comparables, avec leur sens et leur unité. Le sens compte :
# pour la masse, « le meilleur » est le plus petit.
STATS = {
    "dps": ("DPS", "dégâts par seconde", True),
    "alpha": ("alpha", "dégâts par tir", True),
    "rounds_per_minute": ("cadence", "coups par minute", True),
    "effective_range": ("portée", "mètres", True),
    "projectile_speed": ("vitesse", "m/s", True),
    "ammo_capacity": ("munitions", "coups", True),
    # Réserve d'énergie des armes de vaisseau à énergie. Aucune arme balistique
    # n'en a — le classement sur cette colonne les écarte donc de lui-même,
    # et c'est exactement ce qu'il faut répondre.
    "capacitor_max": ("capacitor", "unités", True),
    "health": ("robustesse", "PV", True),
    "item_mass": ("masse", "kg", False),
}

# Écarte les entrées de test et ce qui n'est pas monté en vol.
#
# Ce filtre ne suffit pas à isoler ce qu'un *joueur* peut monter : le canon du
# porte-nefs Bengal porte lui aussi `flightReady weaponMountUsable`, puisqu'il
# est bel et bien monté — sur un capital-ship. Aucun champ amont ne fait la
# distinction. C'est pourquoi les comparaisons se font par taille (§ compare_items)
# plutôt qu'en prétendant trier le montable de l'immontable.
MOUNTABLE = "AND i.flight_ready = 1 AND i.mount_usable = 1 AND i.is_dev = 0"

_STAT_MOTS = {
    "dps": "dps", "degat": "dps", "dommage": "dps", "damage": "dps",
    "alpha": "alpha", "cadence": "rounds_per_minute", "rpm": "rounds_per_minute",
    "portee": "effective_range", "range": "effective_range",
    "vitesse": "projectile_speed", "munition": "ammo_capacity",
    "chargeur": "ammo_capacity", "robuste": "health", "masse": "item_mass",
    # « L'arme la plus légère » classait sur le DPS : `_SHIP_STAT_MOTS` avait
    # « léger » et « lourd », pas celle-ci. Le joueur dit l'adjectif, pas le
    # nom de la colonne.
    "poids": "item_mass", "leger": "item_mass", "lourd": "item_mass",
    # Le joueur dit « balles », jamais « ammo_capacity ». Et « capacitor » est
    # assez spécifique pour tenir lieu de famille à lui seul : 115 objets en
    # ont un, tous des armes de vaisseau à énergie.
    "balle": "ammo_capacity", "cartouche": "ammo_capacity",
    "pruneau": "ammo_capacity", "bastos": "ammo_capacity",
    "capacitor": "capacitor_max", "capaciteur": "capacitor_max",
    "condensateur": "capacitor_max",
}


def detect_stat(question: str) -> str:
    """Statistique visée par une question de comparaison. DPS par défaut."""
    norm = normalize(question)
    for mot, stat in _STAT_MOTS.items():
        if mot in norm:
            return stat
    return "dps"


def _weapon_filter(query: str) -> tuple[str, list, str]:
    """Filtre SQL déduit d'une formulation du genre « canon balistique »."""
    norm = normalize(query)
    clauses, args, libelle = [], [], []
    for mot, classe in (("balistique", "ballistic"), ("ballistic", "ballistic"),
                        ("laser", "laser"), ("plasma", "plasma"),
                        ("tachyon", "tachyon"), ("distortion", "distortion")):
        if mot in norm:
            clauses.append("st.weapon_class = ?")
            args.append(classe)
            libelle.append(classe)
            break
    for mot, genre in (("gatling", "gatling"), ("repeater", "repeater"),
                       ("repeteur", "repeater"), ("scatter", "scattergun"),
                       ("canon", "cannon"), ("cannon", "cannon")):
        if mot in norm:
            clauses.append("st.weapon_kind = ?")
            args.append(genre)
            libelle.append(genre)
            break
    return (" AND ".join(clauses) if clauses else "1=1"), args, " ".join(libelle)


# --------------------- Vaisseaux et objets — statistiques, voisines, reprise

# Statistique visée par une question sur un vaisseau. Le libellé sert à la
# réponse orale, l'unité à la lire, et le booléen dit si « meilleur » veut dire
# « plus grand » — pour la masse, c'est l'inverse.
SHIP_STATS: dict[str, tuple[str, str, bool]] = {
    "max_speed": ("vitesse maximale", "mètres par seconde", True),
    "scm_speed": ("vitesse de combat", "mètres par seconde", True),
    "cargo_scu": ("capacité de fret", "SCU", True),
    "crew": ("équipage", "places", True),
    "shield_hp": ("bouclier", "points", True),
    "health": ("résistance de coque", "points", True),
    "mass": ("masse", "kilos", False),
    "pilot_dps": ("DPS pilote", "dégâts par seconde", True),
    "qt_speed": ("vitesse quantique", "mètres par seconde", True),
    "fuel_capacity": ("carburant", "unités", True),
    # `fuel_capacity` était lue depuis le début, la consommation jamais : la
    # moitié manquante d'un calcul dont l'autre moitié dormait en base, comme
    # `qt_fuel_rate` l'était pour la portée quantique.
    "autonomie_vol": ("autonomie en vol", "secondes", True),
    "fuel_usage": ("consommation", "unités par seconde", False),
    # `FuelIntakeRate` est publié séparément de la consommation. Il ne faut
    # surtout pas les soustraire : la source ne dit ni dans quelles conditions
    # le taux est atteint, ni s'il est constant. On rend donc le taux brut.
    "fuel_intake": ("captation de carburant en vol", "unités par seconde", True),
    "length": ("longueur", "mètres", True),
    # « Il manque la vitesse max en boost » — retour du journal. La colonne
    # existait depuis l'ingestion, renseignée sur 276 vaisseaux sur 316.
    "boost_speed": ("vitesse en boost", "mètres par seconde", True),
    "size": ("taille", "", True),
    "quantum_fuel": ("carburant quantique", "unités", True),
    # Colonnes en base qu'aucun outil n'interrogeait (point 4 de l'audit).
    # Elles rendent aussi les comparaisons possibles : « quel vaisseau a la
    # plus grosse soute à minerai » n'avait pas de réponse.
    "ore_capacity": ("soute à minerai", "SCU", True),
    # Une prime **basse** est meilleure, comme la masse : c'est ce que coûte
    # la perte du vaisseau, pas une performance.
    "insurance_cost": ("prime d'assurance", "aUEC", False),
    "insurance_minutes": ("attente à la réclamation", "minutes", False),
    "pitch": ("tangage", "degrés par seconde", True),
    "yaw": ("lacet", "degrés par seconde", True),
    "roll": ("roulis", "degrés par seconde", True),
}

_SHIP_STAT_MOTS = (
    # « Minerai » avant « soute » : la détection rend la **première**
    # correspondance, et « la plus grosse soute à minerai » contient les deux.
    # Sans cet ordre, la question comparait la capacité de fret.
    ("minerai", "ore_capacity"), ("minage", "ore_capacity"),
    ("scu", "cargo_scu"), ("cargo", "cargo_scu"), ("fret", "cargo_scu"),
    ("caisse", "cargo_scu"), ("conteneur", "cargo_scu"),
    ("soute", "cargo_scu"), ("marchandise", "cargo_scu"),
    ("equipage", "crew"), ("place", "crew"), ("personne", "crew"),
    ("membre", "crew"), ("siege", "crew"),
    ("bouclier", "shield_hp"), ("shield", "shield_hp"),
    ("coque", "health"), ("resistance", "health"), ("blindage", "health"),
    ("masse", "mass"), ("poids", "mass"), ("pese", "mass"),
    ("lourd", "mass"), ("leger", "mass"),
    ("quantique", "qt_speed"), ("quantum", "qt_speed"),
    # « Autonomie » avant « carburant » : la détection rend la **première**
    # correspondance, et « quelle autonomie de carburant » contient les deux.
    ("autonomie", "autonomie_vol"), ("combien de temps", "autonomie_vol"),
    ("tient en vol", "autonomie_vol"), ("vol le plus long", "autonomie_vol"),
    ("consommation", "fuel_usage"), ("consomme", "fuel_usage"),
    ("captation", "fuel_intake"), ("collecte", "fuel_intake"),
    ("capte", "fuel_intake"), ("intake", "fuel_intake"),
    ("carburant", "fuel_capacity"), ("fuel", "fuel_capacity"),
    ("longueur", "length"), ("long", "length"),
    ("taille", "size"), ("gros", "size"), ("grand", "size"),
    ("dps", "pilot_dps"), ("degat", "pilot_dps"),
    # « Qui est le plus fort entre un Gladius et un Arrow » comparait la
    # **vitesse**, faute de mieux : c'est la statistique par défaut. Entre deux
    # chasseurs, « fort » désigne la puissance de feu — c'est la lecture la
    # plus courante, et la comparaison propose les autres ensuite.
    ("fort", "pilot_dps"), ("puissant", "pilot_dps"),
    # Colonnes déjà en base et qu'aucun outil n'interrogeait (point 4 de
    # l'audit). Aucune réingestion, seulement du vocabulaire.
    ("assurance", "insurance_cost"), ("prime", "insurance_cost"),
    ("reclamation", "insurance_minutes"), ("reclam", "insurance_minutes"),
    ("maniabilite", "pitch"), ("maniable", "pitch"), ("tangage", "pitch"),
    ("roulis", "roll"), ("roll", "roll"),
    ("lacet", "yaw"), ("rotation", "yaw"),
    # La vitesse en dernier : « vitesse quantique » doit gagner sur « vitesse ».
    ("boost", "boost_speed"), ("postcombustion", "boost_speed"),
    ("scm", "scm_speed"), ("combat", "scm_speed"),
    ("vitesse", "max_speed"), ("rapide", "max_speed"), ("vite", "max_speed"),
)


def detect_ship_stat(question: str) -> str:
    """Statistique de vaisseau visée. Vitesse maximale par défaut."""
    return detect_ship_stat_ou_rien(question) or "max_speed"


def detect_ship_stat_ou_rien(question: str) -> str | None:
    """La statistique nommée, ou None si la question n'en nomme aucune.

    Le défaut de `detect_ship_stat` convient à un classement — « le vaisseau
    le plus rapide » sous-entend la vitesse. Il ne convient pas à une fiche :
    « combien de SCU a un Avenger Titan » doit rendre les SCU **et rien
    d'autre**, et « décris-moi un Avenger Titan » doit rendre tout.
    """
    norm = normalize(question)
    for mot, stat in _SHIP_STAT_MOTS:
        if mot in norm:
            return stat
    return None


# Statistiques qui vont par paire dans la tête d'un joueur. Après avoir donné
# la vitesse maximale, proposer la vitesse en combat ; après le bouclier,
# proposer la coque. C'est un retour d'usage, pas une déduction des données.
VOISINES: dict[str, tuple[str, ...]] = {
    "max_speed": ("scm_speed", "boost_speed"),
    "scm_speed": ("max_speed", "boost_speed"),
    "boost_speed": ("max_speed", "scm_speed"),
    "shield_hp": ("health",),
    "health": ("shield_hp",),
    "cargo_scu": ("crew",),
    "crew": ("cargo_scu",),
    "size": ("length",),
    "length": ("size",),
    "qt_speed": ("qt_range", "quantum_fuel"),
    "autonomie_vol": ("fuel_usage", "fuel_intake"),
    "fuel_usage": ("autonomie_vol", "fuel_intake"),
    "fuel_intake": ("fuel_usage", "autonomie_vol"),
    "pitch": ("yaw", "roll"),
    "yaw": ("pitch", "roll"),
    "roll": ("pitch", "yaw"),
    "ammo_capacity": ("rounds_per_minute", "dps"),
    "capacitor_max": ("ammo_capacity", "dps"),
    "dps": ("alpha", "rounds_per_minute"),
    "rounds_per_minute": ("dps", "ammo_capacity"),
    "effective_range": ("projectile_speed", "dps"),
    "projectile_speed": ("effective_range", "dps"),
}


# Statistiques d'objet nommables dans une question, pour une fiche ciblée.
_ITEM_STAT_MOTS = (
    ("balle", "ammo_capacity"), ("cartouche", "ammo_capacity"),
    ("pruneau", "ammo_capacity"), ("bastos", "ammo_capacity"),
    ("munition", "ammo_capacity"), ("chargeur", "ammo_capacity"),
    ("capacitor", "capacitor_max"), ("capaciteur", "capacitor_max"),
    ("condensateur", "capacitor_max"),
    ("cadence", "rounds_per_minute"), ("rpm", "rounds_per_minute"),
    ("portee", "effective_range"),
    ("masse", "item_mass"), ("poids", "item_mass"), ("pese", "item_mass"),
    ("alpha", "alpha"),
    ("dps", "dps"), ("degat", "dps"), ("dommage", "dps"),
    ("vitesse", "projectile_speed"),
    # « Ça prend combien de place ? » — la question se pose autant que le DPS
    # dès qu'il faut remplir une soute. « scu » avant « volume » : c'est le mot
    # que le jeu affiche, donc celui que le joueur tape.
    ("scu", "volume_uscu"), ("volume", "volume_uscu"),
    ("place", "volume_uscu"), ("encombrement", "volume_uscu"),
)


def detect_item_stat(question: str) -> str | None:
    """La statistique d'objet nommée, ou None pour la fiche entière."""
    norm = normalize(question)
    for mot, stat in _ITEM_STAT_MOTS:
        if mot in norm:
            return stat
    return None


def _inverser(paires) -> dict[str, tuple[str, ...]]:
    par_stat: dict[str, list[str]] = {}
    for mot, stat in paires:
        par_stat.setdefault(stat, []).append(mot)
    return {stat: tuple(mots) for stat, mots in par_stat.items()}


# Mots qui désignent une statistique **en reprise**, quand le sujet est déjà
# connu. Le vocabulaire est plus large que celui de la détection : « max » ne
# peut pas servir à détecter une intention — « Freelancer MAX » est un
# vaisseau — mais après « quelle taille fait un Gladius », « la vitesse max »
# ne peut désigner que la vitesse du Gladius.
_REPRISE_EN_PLUS = {
    "max_speed": ("max", "maximale", "maximum", "pointe"),
    "scm_speed": ("combat", "croisiere"),
    "boost_speed": ("boost", "postcombustion"),
    "shield_hp": ("bouclier", "boucliers", "shields"),
    "health": ("coque", "hull", "pv"),
    "cargo_scu": ("scu", "fret", "cargo", "soute"),
    "qt_range": ("saut", "jump", "portee"),
    "quantum_fuel": ("quantique", "qt"),
    "ammo_capacity": ("balles", "munitions", "chargeur"),
    "capacitor_max": ("capacitor", "capaciteur"),
    "projectile_speed": ("projectile", "projectiles", "sortie", "balistique"),
    "effective_range": ("portee", "distance", "range"),
    "rounds_per_minute": ("cadence", "rpm", "tir"),
    "item_mass": ("masse", "poids"),
    "alpha": ("alpha", "tir"),
    "dps": ("dps", "degats", "dommages"),
}


# Tout le vocabulaire qui désigne une statistique. Ces mots ne font jamais
# partie d'un nom d'entité : « la vitesse max d'un Avenger » parle du Avenger,
# et « max » qualifie la vitesse. Sans cette liste, le contrôle de certitude
# voyait « max » comme un mot inexpliqué et doutait d'une question limpide.
MOTS_DE_STAT = frozenset(
    [mot for mot, _ in _SHIP_STAT_MOTS]
    + [mot for mot, _ in _ITEM_STAT_MOTS]
    + [mot for mots in _REPRISE_EN_PLUS.values() for mot in mots]
    + list(_STAT_MOTS)
    # Qualificatifs de demande. Ils accompagnent une question sans jamais
    # nommer quoi que ce soit — et sans eux, « liste-moi tous les points de
    # vente du Coda » doutait du Coda.
    + ["meilleur", "meilleure", "meilleurs", "pire", "stock", "origine",
       "lieu", "lieux", "endroit", "endroits", "total", "totalite",
       "complet", "complete", "exact", "exacte", "precis", "precise"]
)


def mots_de_reprise(stat: str, vaisseau: bool) -> tuple[str, ...]:
    """Tout ce qui peut désigner cette statistique dans une reprise."""
    table = _inverser(_SHIP_STAT_MOTS if vaisseau else _ITEM_STAT_MOTS)
    return tuple(dict.fromkeys(table.get(stat, ()) + _REPRISE_EN_PLUS.get(stat, ())))


# -------------------------------------------------- Composants hors armement

# Vocabulaire du joueur → catégorie de port, type d'objet, libellé français.
# Le port donne la taille acceptée, le type filtre le catalogue.
COMPONENTS: dict[str, tuple[str, str, str]] = {
    "bouclier": ("shield", "Shield", "bouclier"),
    "shield": ("shield", "Shield", "bouclier"),
    "quantum": ("qdrive", "QuantumDrive", "moteur quantique"),
    "quantique": ("qdrive", "QuantumDrive", "moteur quantique"),
    "saut": ("qdrive", "QuantumDrive", "moteur quantique"),
    "refroidisseur": ("cooler", "Cooler", "refroidisseur"),
    "cooler": ("cooler", "Cooler", "refroidisseur"),
    "generateur": ("power", "PowerPlant", "générateur"),
    "centrale": ("power", "PowerPlant", "générateur"),
    "power plant": ("power", "PowerPlant", "générateur"),
    "radar": ("radar", "Radar", "radar"),
    "reservoir": ("fuel", "FuelTank", "réservoir"),
}


def detect_component(question: str) -> tuple[str, str, str] | None:
    """Composant visé par la question, s'il y en a un."""
    norm = normalize(question)
    for mot, cible in COMPONENTS.items():
        if mot in norm:
            return cible
    return None


# Statistiques de composants qu'un joueur nomme dans sa question. Sans l'une
# d'elles, la question porte sur la compatibilité et pas sur un classement.
_COMPONENT_STATS = {
    "recharge": ("s.shield_regen", "recharge du bouclier"),
    "regen": ("s.shield_regen", "recharge du bouclier"),
    "point": ("s.shield_health", "points de bouclier"),
    "resistance": ("s.shield_health", "points de bouclier"),
    # « Le bouclier le plus **résistant** » reposait la question des axes au
    # lieu de classer : « resistance » n'est pas une sous-chaîne de
    # « resistant ». Journal du 2026-08-11, trois allers-retours perdus.
    "resistant": ("s.shield_health", "points de bouclier"),
    "encaisse": ("s.shield_health", "points de bouclier"),
    "solide": ("s.shield_health", "points de bouclier"),
    "vitesse": ("s.qt_drive_speed", "vitesse de saut"),
    "portee": ("s.qt_jump_range", "portée de saut"),
    "range": ("s.qt_jump_range", "portée de saut"),
    # Refroidisseurs et générateurs, ingérés le 2026-08-06. Sans eux, « le
    # meilleur bouclier taille 2 » se classait et « le meilleur refroidisseur
    # taille 2 » ne pouvait que lister ce qui rentre — une asymétrie invisible
    # pour le joueur, qui ne comprend pas pourquoi la même question rend deux
    # formes de réponse.
    "refroidissement": ("s.cooling_rate", "fluide produit par seconde"),
    "refroidit": ("s.cooling_rate", "fluide produit par seconde"),
    "coolant": ("s.cooling_rate", "fluide produit par seconde"),
    "puissance": ("s.power_rate", "puissance produite par seconde"),
    "watt": ("s.power_rate", "puissance produite par seconde"),
    # La furtivité se joue sur ces deux nombres, et **plus bas vaut mieux**.
    "discret": ("s.signature_em", "signature électromagnétique"),
    "furtif": ("s.signature_em", "signature électromagnétique"),
    "signature": ("s.signature_em", "signature électromagnétique"),
    "thermique": ("s.signature_ir", "signature infrarouge"),
    # **L'armure personnelle, ingérée le 2026-08-06.** 2 416 objets pour zéro
    # statistique : le même piège que les refroidisseurs, à trente fois
    # l'échelle. « La meilleure armure pour Pyro » ne rendait rien, et rien ne
    # disait pourquoi.
    "froid": ("s.temp_min", "température minimale supportée"),
    "glace": ("s.temp_min", "température minimale supportée"),
    "glacee": ("s.temp_min", "température minimale supportée"),
    "gel": ("s.temp_min", "température minimale supportée"),
    "chaud": ("s.temp_max", "température maximale supportée"),
    "chaleur": ("s.temp_max", "température maximale supportée"),
    "canicule": ("s.temp_max", "température maximale supportée"),
    "temperature": ("s.temp_max", "température maximale supportée"),
    "radiation": ("s.radiation_max", "capacité avant saturation aux radiations"),
    "irradie": ("s.radiation_max", "capacité avant saturation aux radiations"),
    "rad": ("s.radiation_max", "capacité avant saturation aux radiations"),
    # **L'oxygène ne se classe pas** : mesuré, `AtmosphereCapacity` ne prend
    # que **deux valeurs** — 0,0015 et 0,02 — sur les 672 casques. Classer
    # 659 casques identiques n'apprend rien. La colonne reste lisible sur une
    # fiche, où la question « j'ai combien d'oxygène » se pose vraiment.
    "balistique": ("s.armor_physical", "encaissement des dégâts physiques"),
    "physique": ("s.armor_physical", "encaissement des dégâts physiques"),
    "energie": ("s.armor_energy", "encaissement des dégâts d'énergie"),
    "laser": ("s.armor_energy", "encaissement des dégâts d'énergie"),
    "protege": ("s.armor_physical", "encaissement des dégâts physiques"),
    "blindage": ("s.armor_physical", "encaissement des dégâts physiques"),
    # **Jamais une clé d'une lettre ici** : la détection se fait par
    # sous-chaîne, et « g » matchait dans « aven**g**er » — « les missiles de
    # l'Avenger Titan » partait alors en classement d'armures sur la tenue aux
    # G. C'est la leçon des alias de moins de trois caractères, transposée à
    # une table de vocabulaire.
    "force g": ("s.gforce_resistance", "tenue aux G"),
    "acceleration": ("s.gforce_resistance", "tenue aux G"),
    # **Missiles et propulseurs.** « Quel missile a la plus grande portée »
    # n'avait aucune source jusqu'ici — 68 missiles, zéro statistique.
    "explosion": ("s.rayon_explosion", "rayon d'explosion"),
    "souffle": ("s.rayon_explosion", "rayon d'explosion"),
    "poussee": ("s.poussee", "poussée"),
    "pousse": ("s.poussee", "poussée"),
    "newton": ("s.poussee", "poussée"),
    "capacite": ("s.capacite_scu", "capacité"),
    "reservoir": ("s.capacite_scu", "capacité"),
}

# Les statistiques de composant où le **plus petit** gagne. Une signature
# basse rend discret : un classement décroissant mettrait en tête le composant
# le plus visible, soit l'inverse de la question posée.
#
# L'armure en ajoute deux familles, et pour deux raisons différentes :
# un **multiplicateur** de dégâts bas protège mieux — 0,125 arrête sept dégâts
# sur huit — et une **température minimale** basse tient plus froid. Sans
# elles, « quelle armure pour une lune glacée » aurait rendu celle qui gèle
# le plus vite.
COMPONENT_MOINS_EST_MIEUX = frozenset({
    "s.signature_em", "s.signature_ir",
    "s.armor_physical", "s.armor_energy", "s.armor_distortion",
    "s.armor_thermal", "s.armor_biochemical", "s.armor_stun",
    "s.temp_min",
    # Le temps qu'un bouclier tombé passe sans recharger : plus court vaut
    # mieux. Classable depuis que l'axe « tenir sous le feu » se choisit.
    "s.shield_downed",
})


#: **Les axes d'un « meilleur » de composant, et les mots qui les
#: choisissent.** La question des axes (« le meilleur dépend de ce que tu
#: cherches ») était posée sans qu'aucune réponse ne soit acceptée : le joueur
#: tapait « encaisser » — le mot même qu'on venait de lui proposer — et
#: recevait « je n'ai pas compris ». Quatre fois au journal du 2026-08-10/11.
#: C'est la règle « une proposition annoncée doit être exécutable », et son
#: vocabulaire vit ici, avec le reste du vocabulaire.
#:
#: (colonne, libellé d'axe, explication, libellé du classement, mots de
#: reprise). Les mots sont **distinctifs** — pas de « vite », partagé par
#: deux axes : un mot commun à plusieurs propositions désigne le groupe.
COMPONENT_AXES = (
    ("shield_health", "encaisser", "la plus grosse réserve de bouclier",
     "points de bouclier",
     ("encaisser", "encaisse", "reserve", "resistant", "resistance",
      "solide", "tank", "pv")),
    ("shield_regen", "recharger vite", "la recharge la plus rapide",
     "recharge du bouclier",
     ("recharger", "recharge", "regen")),
    ("shield_downed", "tenir sous le feu",
     "le temps d'arrêt le plus court quand le bouclier tombe",
     "temps d'arrêt après effondrement",
     ("tenir", "feu", "effondrement", "arret")),
    ("signature_em", "rester discret", "la signature EM la plus basse",
     "signature électromagnétique",
     ("discret", "discretion", "furtif", "signature")),
    ("qt_drive_speed", "voyager vite", "la vitesse quantique la plus haute",
     "vitesse de saut",
     ("voyager", "quantique", "rapide")),
    ("qt_jump_range", "aller loin", "la plus grande portée",
     "portée de saut",
     ("loin", "portee", "autonomie")),
    ("cooling_rate", "refroidir", "le meilleur refroidissement",
     "fluide produit par seconde",
     ("refroidir", "refroidissement", "froid")),
)


# Complété ici : `_COMPONENT_STATS` est défini plus bas dans le fichier que
# `MOTS_DE_STAT`. « Quel bouclier avec la meilleure recharge » doutait, parce
# que « recharge » n'était dans aucune liste et passait pour un nom inexpliqué.
MOTS_DE_STAT = MOTS_DE_STAT | frozenset(_COMPONENT_STATS)


def detect_component_stat(question: str) -> tuple[str, str] | None:
    """La statistique nommée dans la question, ou None si aucune.

    Le défaut ne doit pas être un classement : « quel bouclier pour un 890 »
    répondait « les meilleurs en points de bouclier », alors que la question
    était « lesquels puis-je monter ».
    """
    norm = normalize(question)
    for mot, cible in _COMPONENT_STATS.items():
        if mot in norm:
            return cible
    return None


# ----------------------------------- Accessoires d'arme, carrières et budget

# Les familles d'accessoires, et les mots qui les désignent. Vocabulaire
# fermé : « quels accessoires » sans précision rend tout, et c'est la réponse.
_ACCESSOIRES = (
    (("optique", "optiques", "lunette", "lunettes", "viseur", "viseurs",
      "scope", "reflex", "visee"), "IronSight", "optiques"),
    (("canon", "canons", "silencieux", "barrel", "compensateur"),
     "Barrel", "canons"),
    (("chargeur", "chargeurs", "magazine"), "Magazine", "chargeurs"),
    (("sous canon", "underbarrel", "poignee", "lampe", "laser"),
     "BottomAttachment", "accessoires sous canon"),
    (("utilitaire", "utility"), "Utility", "utilitaires"),
)


def detect_famille_accessoire(question: str) -> tuple[str, str] | None:
    """La famille d'accessoire nommée, ou None pour toutes."""
    norm = normalize(question)
    for mots, sous_type, libelle in _ACCESSOIRES:
        if any(m in norm for m in mots):
            return sous_type, libelle
    return None


# Les carrières telles que le jeu les écrit, et les mots français qui les
# désignent. Vocabulaire fermé : sans catégorie nommée, on ne filtre pas.
_CARRIERES = (
    (("combat", "chasseur", "guerre", "militaire", "combattre"), "Combat"),
    (("transport", "fret", "cargo", "marchandise", "commerce", "hauling"),
     "Transporter"),
    (("exploration", "explorer", "decouverte"), "Exploration"),
    (("industriel", "minage", "miner", "minier", "salvage", "recuperation"),
     "Industrial"),
    (("support", "medical", "ravitaillement", "soutien"), "Support"),
    (("course", "racing", "competition"), "Competition"),
    (("polyvalent", "multirole", "multi role"), "Multi-Role"),
    (("terrestre", "vehicule terrestre", "rover"), "Ground"),
)


# « 17M », « 17 millions », « 2,5 M de crédits », « 800k ». Le joueur écrit son
# budget comme il le dit, et « 17M » ne vaut pas 17.
_MONTANT = re.compile(
    r"\b(?P<valeur>\d[\d\s]*(?:[.,]\d+)?)\s*"
    r"(?P<echelle>millions?|m\b|milliards?|milliers?|k\b)?"
    r"\s*(?:de\s+)?(?:aeuc|auec|uec|credits?|crédits?)?")


def detect_montant(question: str) -> float | None:
    """Le budget lu dans la question, en aUEC. None s'il n'y en a pas.

    On exige une **échelle ou une monnaie** : sans elles, « pour 3 joueurs »
    passerait pour un budget de trois crédits.
    """
    # **Sur le texte brut, pas normalisé.** `normalize` retire la ponctuation :
    # « 2,5 millions » y devient « 25 millions », soit dix fois trop.
    norm = question.lower()
    for trouve in _MONTANT.finditer(norm):
        echelle = (trouve.group("echelle") or "").strip()
        suffixe = norm[trouve.end("valeur"):trouve.end()]
        if not echelle and not any(
                m in suffixe for m in ("uec", "credit")):
            continue
        try:
            valeur = float(trouve.group("valeur").replace(" ", "").replace(",", "."))
        except ValueError:
            continue
        if echelle.startswith(("million", "m")):
            valeur *= 1_000_000
        elif echelle.startswith("milliard"):
            valeur *= 1_000_000_000
        elif echelle.startswith(("millier", "k")):
            valeur *= 1_000
        return valeur
    return None


def detect_carriere(question: str) -> str | None:
    """La catégorie de vaisseau nommée, ou None."""
    norm = normalize(question)
    for mots, carriere in _CARRIERES:
        if any(m in norm for m in mots):
            return carriere
    return None


# ---------------------------------- Seuils, budgets et contraintes multiples

# « Plus de 100 SCU », « moins de 2 millions ». Le nombre est un **seuil**, pas
# un nom — c'est ce que le résolveur prenait pour une entité, d'où l'Origin
# 100i sur « plus de 100 SCU ».
_SEUIL = re.compile(
    r"\b(?P<sens>plus|moins|au moins|au plus|superieur\w*|inferieur\w*"
    # « Sous 2 millions » et « en dessous de 100 » sont des seuils que le
    # joueur écrit tout autant que « moins de » — les ignorer faisait perdre
    # la contrainte **en silence**, et la réponse annonçait alors le reste
    # comme si la question n'avait rien demandé de plus.
    r"|en dessous|sous|au dessus|dessus)\s+"
    r"(?:de\s+|a\s+|que\s+)?(?P<valeur>\d[\d\s]*(?:[.,]\d+)?)"
    r"\s*(?P<echelle>millions?|milliers?|k\b)?")


def detect_seuil(question: str) -> tuple[str, float] | None:
    """Le seuil demandé : (« >= » ou « <= », valeur). None si aucun."""
    trouve = _SEUIL.search(normalize(question))
    if not trouve:
        return None
    brut = trouve.group("valeur").replace(" ", "").replace(",", ".")
    try:
        valeur = float(brut)
    except ValueError:
        return None
    echelle = trouve.group("echelle") or ""
    if echelle.startswith("million"):
        valeur *= 1_000_000
    elif echelle.startswith(("millier", "k")):
        valeur *= 1_000
    sens = trouve.group("sens")
    return ("<=" if sens.startswith(("moins", "inferieur", "au plus")) else ">=",
            valeur)


# Mots qui désignent de l'argent. Un seuil qui les accompagne est un
# **budget**, pas une caractéristique : « moins de 2 millions de crédits » ne
# se cherche pas dans une colonne de `ships` mais dans les relevés UEX.
_MOTS_ARGENT = ("auec", "uec", "credit", "budget", "prix", "coute", "cout",
                "argent", "euro", "cher")


def detect_contraintes(question: str) -> tuple[list[tuple[str, str, float]],
                                               tuple[str, float] | None]:
    """Tous les seuils d'une question, et le budget s'il y en a un.

    Nommée `contraintes` et non `criteres` : `detect_criteres` existe déjà
    plus bas, pour les axes de raffinage. Définie après celle-ci, elle
    l'écrasait silencieusement — le genre de collision qu'un module de
    4 000 lignes rend invisible.

    `detect_seuil` n'en lit qu'un — c'est la limite qui empêchait de répondre
    à « quel vaisseau de combat avec plus de 100 SCU et moins de 4 places ».
    Le joueur pose ses contraintes ensemble ; les lire une par une oblige à
    n'en retenir qu'une, et la réponse est alors juste sur un tiers de la
    question.

    **La statistique se lit après le nombre**, pas avant : on dit « plus de
    100 SCU », jamais « plus de SCU 100 ». Le repli sur le texte qui précède
    sert aux tournures inversées — « une soute de plus de 100 ».
    """
    norm = normalize(question)
    trouves = list(_SEUIL.finditer(norm))
    criteres: list[tuple[str, str, float]] = []
    budget: tuple[str, float] | None = None

    for rang, trouve in enumerate(trouves):
        brut = trouve.group("valeur").replace(" ", "").replace(",", ".")
        try:
            valeur = float(brut)
        except ValueError:
            continue
        echelle = trouve.group("echelle") or ""
        if echelle.startswith("million"):
            valeur *= 1_000_000
        elif echelle.startswith(("millier", "k")):
            valeur *= 1_000
        sens = ("<=" if trouve.group("sens").startswith(
            ("moins", "inferieur", "au plus", "sous", "en dessous")) else ">=")

        # La fenêtre va de la fin de ce seuil au début du suivant : c'est là
        # que vit le mot d'unité, et la borner évite d'attraper celui du
        # critère d'après.
        fin = trouves[rang + 1].start() if rang + 1 < len(trouves) else len(norm)
        apres = norm[trouve.end():fin]
        debut = trouves[rang - 1].end() if rang else 0
        avant = norm[debut:trouve.start()]

        # **L'ordre de ces quatre tests est le cœur de la fonction.** Le repli
        # sur le texte qui *précède* le nombre doit venir en dernier : sur
        # « plus de 10 places et moins de 1 million », il attrapait « places »
        # et lisait un équipage inférieur à un million. Une échelle de million
        # sans unité est un budget bien avant d'être une caractéristique —
        # aucune colonne de `ships` ne se compte en millions.
        stat_apres = detect_ship_stat_ou_rien(apres)
        if any(mot in apres for mot in _MOTS_ARGENT):
            budget = (sens, valeur)
        elif stat_apres is not None:
            criteres.append((stat_apres, sens, valeur))
        elif echelle.startswith(("million", "millier", "k")):
            budget = (sens, valeur)
        elif (stat_avant := detect_ship_stat_ou_rien(avant)) is not None:
            criteres.append((stat_avant, sens, valeur))

    # **Un compte nu à unité fermée est aussi une contrainte.** « Un vaisseau
    # avec plus de 50 SCU **et 2 places** » perdait la seconde moitié : sans
    # mot de comparaison, `_SEUIL` ne la voyait pas, et la question partait
    # chez l'outil à critère unique — juste sur un tiers de la demande, la
    # pire des formes. Le nombre nu n'entre que collé à une unité d'un
    # vocabulaire **fermé** (la leçon d'« Origin 100i » : un nombre seul n'est
    # pas une contrainte), et un compte demandé se lit « au moins » — le
    # joueur qui dit « 2 places » veut s'asseoir à deux, pas exclure les
    # vaisseaux de trois.
    deja = [(t.start(), t.end()) for t in trouves]
    for nu in _COMPTE_NU.finditer(norm):
        if any(d <= nu.start() < f or d < nu.end() <= f for d, f in deja):
            continue
        stat = detect_ship_stat_ou_rien(nu.group("unite"))
        if stat is not None:
            criteres.append((stat, ">=", float(nu.group("valeur"))))

    # Dédoublonnage sur la statistique : « plus de 100 SCU et plus de 200 SCU »
    # n'a qu'une lecture utile, la dernière énoncée.
    par_stat = {stat: (stat, sens, valeur) for stat, sens, valeur in criteres}
    return list(par_stat.values()), budget


# Le compte nu : un nombre immédiatement suivi d'une unité de vaisseau. Le
# vocabulaire est fermé exprès — « 890 jump » ne doit pas devenir une
# contrainte de 890 quelque chose.
_COMPTE_NU = re.compile(
    r"(?<![\w.,])(?P<valeur>\d{1,4})\s*"
    r"(?P<unite>places?|sieges?|scu|personnes?|membres?)\b")


# ------------------------------- Valeurs calculées à partir de deux colonnes

# Ce qui se **calcule** à partir de deux colonnes, et que rien ne lisait. Le
# jeu ne publie aucun temps de rechargement — « le meilleur DPS par seconde de
# rechargement » n'a donc pas de réponse — mais il publie de quoi dériver ce
# qui compte vraiment : ce qu'un chargeur encaisse, combien de temps on tire,
# et le débit qu'un capacitor laisse tenir.
#
# Chaque entrée : (libellé, unité, expression SQL, condition, plus_grand_est_mieux).
_METRIQUES = {
    # **« Par chargeur » serait faux pour une arme de vaisseau** : son
    # `ammo_capacity` est la réserve entière — 2 040 coups sur un Havoc
    # Scattergun — et non ce que contient un magasin. Le libellé dit donc ce
    # qui est réellement calculé : tout ce qu'on peut sortir avant d'être à sec.
    "degats_par_chargeur": (
        "dégâts avant d'être à sec", "",
        "s.alpha * (s.ammo_capacity / MAX(COALESCE(s.ammo_per_shot, 1), 1))",
        "s.alpha > 0 AND s.ammo_capacity > 0", True),
    "autonomie_de_tir": (
        "autonomie de tir", "s",
        "(s.ammo_capacity / MAX(COALESCE(s.ammo_per_shot, 1), 1)) "
        "/ (s.rounds_per_minute / 60.0)",
        "s.ammo_capacity > 0 AND s.rounds_per_minute > 0", True),
    # **Le DPS soutenu se lit, il ne se calcule plus.** Il valait
    # `MIN(dps, alpha × regen / cost)` — une borne tirée de deux colonnes, donc
    # conforme au §7 en apparence, et fausse sur **103 armes sur 114** : 3,25
    # sur l'Omnisky IX là où CIG publie 290,3, écart médian 97,5 %. Les deux
    # colonnes ne comptaient pas dans la même unité, et le `MIN` ajouté pour
    # écraser les 809 952 DPS du PyroBurst écrasait un nombre faux par un
    # autre. La valeur publiée est ingérée depuis les fichiers de vaisseau, et
    # les 37 armes qui ne l'ont pas se taisent — comme le rechargement.
    "dps_soutenu": (
        "DPS soutenu", "dégâts par seconde",
        "s.dps_soutenu",
        "s.dps_soutenu > 0", True),
    "tirs_par_capacitor": (
        "tirs par capacitor", "",
        "s.capacitor_max / s.capacitor_cost",
        "s.capacitor_max > 0 AND s.capacitor_cost > 0", True),
    # La déflexion s'applique projectile par projectile. Un scattergun peut
    # afficher un alpha total supérieur tout en frappant moins fort par plomb ;
    # le classement doit donc diviser, jamais filtrer les lignes dont la
    # famille amont est absente (le Sledge III, montable, en est une).
    "alpha_par_projectile": (
        "alpha par projectile", "dégâts par tir",
        "s.alpha / MAX(COALESCE(s.pellets_per_shot, 1), 1)",
        "s.alpha > 0", True),
}

_MOTS_DE_METRIQUE = (
    (("par chargeur", "dans un chargeur", "par magasin", "avant d etre a sec",
      "avant la panne seche", "en tout"), "degats_par_chargeur"),
    (("autonomie", "tir continu", "combien de temps"), "autonomie_de_tir"),
    (("soutenu", "en continu", "sur la duree", "dps reel"), "dps_soutenu"),
    (("par capacitor", "tirs avant", "avant recharge"), "tirs_par_capacitor"),
    (("alpha par projectile", "alpha par plomb", "degats par projectile",
      "degats par plomb"), "alpha_par_projectile"),
)


def detect_metrique(question: str) -> str | None:
    """La métrique calculée nommée dans la question, ou None."""
    norm = normalize(question)
    for mots, cle in _MOTS_DE_METRIQUE:
        if any(m in norm for m in mots):
            return cle
    return None


# ----------------------------- Composants nommés, pour le filtre par absence

# Ce qu'un joueur nomme, et le type d'objet correspondant. Vocabulaire fermé :
# sans composant nommé, « quels vaisseaux n'ont pas… » ne veut rien dire.
_COMPOSANTS_NOMMES = (
    (("jump drive", "jumpdrive", "saut interstellaire"), "JumpDrive"),
    (("moteur quantique", "quantum drive", "quantumdrive"), "QuantumDrive"),
    (("bouclier", "shield"), "Shield"),
    (("refroidisseur", "cooler"), "Cooler"),
    (("generateur", "power plant", "centrale"), "PowerPlant"),
    (("radar",), "Radar"),
    (("tourelle", "turret"), "Turret"),
)


def detect_composant(question: str) -> str | None:
    """Le composant nommé dans la question, ou None."""
    norm = normalize(question)
    for mots, type_item in _COMPOSANTS_NOMMES:
        if any(m in norm for m in mots):
            return type_item
    return None
