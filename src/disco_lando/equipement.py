"""Classer l'équipement personnel et les munitions.

Ouvert le 2026-08-06, après l'audit de source. Trois familles entières
n'avaient **aucune** statistique en base — 2 416 armures, 1 266 propulseurs,
68 missiles — et le joueur ne voyait pas la frontière : « le meilleur bouclier
taille 2 » se classait, « la meilleure armure pour Pyro » rendait une liste
vide sans qu'aucune erreur ne se produise. C'est mot pour mot le piège des
refroidisseurs, à trente fois l'échelle.

**Deux familles de statistiques où le plus petit gagne**, et pour des raisons
différentes qu'il fallait distinguer :

- un **multiplicateur** de dégâts bas protège mieux — 0,125 arrête sept dégâts
  sur huit ;
- une **température minimale** basse tient plus froid — « quelle armure pour
  une lune glacée » aurait sinon rendu celle qui gèle le plus vite.

**La taille classe l'armure comme elle classe les armes.** Une armure lourde
protège plus qu'une légère, et les comparer ensemble revient à répondre « la
plus lourde » à toutes les questions. C'est la règle déjà appliquée à
`compare_items` et aux valeurs calculées.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3

from ._socle import NotFound

# La famille nommée dans la question, et les types d'objet qu'elle recouvre.
# L'ordre décide : « casque » avant « armure », sinon la question la plus
# précise recevrait la réponse la plus large.
_FAMILLES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # **Les composants de vaisseau, absents jusqu'au 2026-08-14.** Mesuré :
    # « quel est le meilleur bouclier taille 2 », « le bouclier le plus
    # résistant », « le meilleur refroidisseur » étaient **tous muets** —
    # alors que `_COMPONENT_STATS` connaît leurs axes depuis longtemps et
    # que cet outil est générique. Le trou était ici : cette liste ne
    # portait que de l'équipement personnel, et sans famille le préparateur
    # abandonne. Deux listes de familles qui ne se recoupent pas — le même
    # défaut que le motif du catalogue, à l'autre bout du projet.
    #
    # Ils passent **avant** l'armure : « bouclier » n'y ressemble pas, mais
    # l'ordre reste celui du plus précis au plus large.
    ("bouclier", r"\bboucliers?\b|\bshields?\b", ("Shield",)),
    ("refroidisseur", r"\brefroidisseurs?\b|\bcoolers?\b", ("Cooler",)),
    ("générateur", r"\bgenerateurs?\b|\bcentrales? electriques?\b"
     r"|\bpower ?plants?\b", ("PowerPlant",)),
    ("moteur quantique", r"\bmoteurs? quantiques?\b|\bquantum ?drives?\b",
     ("QuantumDrive",)),
    # **Le radar se reconnaît et ne se classe pas.** Mesuré sur les 77
    # radars : aucune colonne propre — ni portée, ni résolution, ni
    # discrétion de balayage. Il ne reste que `health`, `item_mass` et
    # `signature_em`, communes à tous les composants. C'est le cas des
    # propulseurs et des réservoirs, et il se traite pareil : on garde la
    # famille pour **dire pourquoi**, plutôt que de se taire.
    ("radar", r"\bradars?\b", ()),

    ("casque", r"\bcasques?\b|\bhelmets?\b",
     ("Char_Armor_Helmet",)),
    ("sac à dos", r"\bsacs? a dos\b|\bbackpacks?\b",
     ("Char_Armor_Backpack",)),
    ("combinaison", r"\bcombinaisons?\b|\bundersuits?\b",
     ("Char_Armor_Undersuit",)),
    ("plastron", r"\bplastrons?\b|\btorses?\b",
     ("Char_Armor_Torso",)),
    ("jambière", r"\bjambieres?\b|\bpantalons?\b",
     ("Char_Armor_Legs",)),
    ("armure", r"\barmures?\b|\btenues?\b|\bprotections?\b",
     ("Char_Armor_Torso", "Char_Armor_Helmet",
      "Char_Armor_Legs", "Char_Armor_Arms", "Char_Armor_Undersuit")),
    ("missile", r"\bmissiles?\b|\broquettes?\b|\btorpilles?\b",
     ("Missile",)),
    # **Propulseurs et réservoirs se reconnaissent, et ne se classent pas.**
    # L'audit les recommandait avec les missiles ; la mesure sépare les trois.
    # Les missiles portent **64 noms distincts sur 68** — un vrai catalogue, où
    # le joueur choisit. Les propulseurs n'ont que **15 lignes nommées sur
    # 1 252**, pour **6 noms distincts** (« Main Thruster », « Maneuver
    # Thruster »), et les réservoirs **18 noms pour 364 lignes**, dont
    # « Internal Tank » répété : ce sont des pièces internes de vaisseau, pas
    # des articles entre lesquels on arbitre.
    #
    # Les classer aurait rendu « Thruster Main Aux, 362 millions de newtons »
    # contre « Main Thruster, 2 millions » — exact, et exactement le piège du
    # Destroyer Mass Driver Cannon. Les colonnes restent ingérées et lisibles
    # sur une fiche ; il n'y a simplement personne à départager. On garde donc
    # la famille pour pouvoir **dire pourquoi**, plutôt que de se taire.
    ("propulseur", r"\bpropulseurs?\b|\bthrusters?\b|\breacteurs?\b", ()),
    ("réservoir", r"\breservoirs?\b|\bfuel ?tanks?\b", ()),
)

# La rareté telle que le joueur la dit, et telle que le jeu l'écrit. Cinq
# paliers sur 5 230 objets — et **deux** légendaires seulement.
_RARETES: tuple[tuple[str, str], ...] = (
    (r"\bcommunes?\b|\bcommons?\b", "Common"),
    (r"\bpeu communes?\b|\buncommons?\b", "Uncommon"),
    (r"\brares?\b", "Rare"),
    (r"\bepiques?\b|\bepics?\b", "Epic"),
    (r"\blegendaires?\b|\blegendarys?\b", "Legendary"),
)

# Les statistiques par défaut d'une famille, quand la question n'en nomme
# aucune. Sans elles, « quelle armure lourde » n'aurait rien à classer.
_DEFAUT = {
    "missile": ("s.degats_missile", "dégâts"),
    # L'axe qu'un joueur veut dire par « le meilleur X », mesuré sur ce que
    # chaque famille publie réellement : les 73 boucliers ont leurs points,
    # les 81 refroidisseurs leur débit, 83 générateurs sur 88 leur
    # puissance, les 63 moteurs leur vitesse de saut.
    "bouclier": ("s.shield_health", "points de bouclier"),
    "refroidisseur": ("s.cooling_rate", "fluide produit par seconde"),
    "générateur": ("s.power_rate", "puissance produite par seconde"),
    "moteur quantique": ("s.qt_drive_speed", "vitesse de saut"),
}

# **La famille prime sur le vocabulaire partagé.** « Portée » vaut la portée de
# saut d'un moteur quantique dans `stats._COMPONENT_STATS`, et « quel missile a
# la plus grande portée » y sortait donc `qt_jump_range` — colonne vide sur les
# missiles, donc réponse vide sur une question parfaitement claire. Le même mot
# ne désigne pas la même colonne selon ce dont on parle.
_PAR_FAMILLE: dict[str, dict[str, tuple[str, str]]] = {
    "missile": {
        "portee": ("s.portee_missile", "portée"),
        "range": ("s.portee_missile", "portée"),
        "vitesse": ("s.vitesse_missile", "vitesse"),
        "degat": ("s.degats_missile", "dégâts"),
        "dps": ("s.degats_missile", "dégâts"),
        "explosion": ("s.rayon_explosion", "rayon d'explosion"),
        "souffle": ("s.rayon_explosion", "rayon d'explosion"),
    },
    # **« Refroidisseur » contient « froid ».** Le vocabulaire partagé fait
    # correspondre par sous-chaîne, et « froid » y vaut `temp_min` — la
    # température minimale qu'une **armure** supporte. Mesuré : « le
    # meilleur refroidisseur taille 2 » sortait donc un axe d'armure
    # personnelle, colonne vide sur les 81 refroidisseurs. C'est la leçon
    # du « g » de « aven**g**er », transposée aux composants.
    "refroidisseur": {
        "refroidissement": ("s.cooling_rate", "fluide produit par seconde"),
        "refroidit": ("s.cooling_rate", "fluide produit par seconde"),
        "coolant": ("s.cooling_rate", "fluide produit par seconde"),
        "froid": ("s.cooling_rate", "fluide produit par seconde"),
        "thermique": ("s.signature_ir", "signature infrarouge"),
        "discret": ("s.signature_em", "signature électromagnétique"),
        "furtif": ("s.signature_em", "signature électromagnétique"),
    },
    "bouclier": {
        # « Capacité » est le mot qu'un joueur emploie pour les points de
        # bouclier, et il ne figurait dans aucun vocabulaire : « quels
        # boucliers font plus de 5 000 de capacité » n'avait donc pas d'axe.
        "capacite": ("s.shield_health", "points de bouclier"),
        "recharge": ("s.shield_regen", "recharge du bouclier"),
        "regen": ("s.shield_regen", "recharge du bouclier"),
        # Le délai avant reprise après effondrement — plus court vaut
        # mieux, et `COMPONENT_MOINS_EST_MIEUX` le sait déjà.
        "effondr": ("s.shield_downed", "délai de reprise après effondrement"),
        "tombe": ("s.shield_downed", "délai de reprise après effondrement"),
        "discret": ("s.signature_em", "signature électromagnétique"),
        "furtif": ("s.signature_em", "signature électromagnétique"),
    },
    "générateur": {
        "puissance": ("s.power_rate", "puissance produite par seconde"),
        "energie": ("s.power_rate", "puissance produite par seconde"),
        "produit": ("s.power_rate", "puissance produite par seconde"),
        "discret": ("s.signature_em", "signature électromagnétique"),
        "furtif": ("s.signature_em", "signature électromagnétique"),
    },
    # **`qt_jump_range` est vide sur les 63 moteurs**, et le vocabulaire
    # partagé y envoie pourtant « portée ». La portée quantique se
    # **calcule** — carburant ÷ consommation — et c'est `peut_voyager` qui
    # le fait. Ici on classe sur ce qui est publié : la consommation, qui
    # ordonne les moteurs exactement comme la portée puisque le réservoir
    # est celui du vaisseau, pas du moteur.
    "moteur quantique": {
        "portee": ("s.qt_fuel_rate", "consommation de carburant quantique"),
        "range": ("s.qt_fuel_rate", "consommation de carburant quantique"),
        "autonomie": ("s.qt_fuel_rate",
                      "consommation de carburant quantique"),
        "consommation": ("s.qt_fuel_rate",
                         "consommation de carburant quantique"),
        "vitesse": ("s.qt_drive_speed", "vitesse de saut"),
        "rapide": ("s.qt_drive_speed", "vitesse de saut"),
        "refroidissement": ("s.qt_cooldown", "temps de refroidissement"),
        "cooldown": ("s.qt_cooldown", "temps de refroidissement"),
    },
}


def question_hors_famille_equipement(question: str, famille: str) -> str:
    """La question privée des mots qui nomment sa famille d'équipement.

    Le pendant de `armurerie.question_hors_famille`, pour l'autre
    vocabulaire de familles. Il sert au repli générique du routeur :
    « re**froid**isseur » contient « froid », qui vaut `temp_min` dans le
    vocabulaire partagé — un axe d'armure personnelle, vide sur les 81
    refroidisseurs. La question posée gagne contre le nom de la chose.
    """
    import re

    from .normalize import normalize

    norm = normalize(question or "")
    for nom, motif, _types in _FAMILLES:
        if nom == famille:
            return re.sub(motif, " ", norm)
    return norm


def stat_de_famille(famille: str, question: str) -> tuple[str, str] | None:
    """La statistique que ce mot désigne **pour cette famille**.

    **Le nom de la famille n'est pas un axe.** « Re**froid**isseur »
    contient « froid », et « quel refroidisseur est le plus discret »
    répondait donc le débit de fluide au lieu de la signature : le mot qui
    nomme la famille gagnait contre le mot qui pose la question. On retire
    d'abord ce que le motif de la famille apparie — même mécanique que
    `armurerie.question_hors_famille`, et que `_mots_d_intention` qui
    retire l'intention avant de chercher l'entité.
    """
    import re

    from .normalize import normalize

    table = _PAR_FAMILLE.get(famille or "")
    if not table:
        return None
    norm = normalize(question or "")
    for nom, motif, _types in _FAMILLES:
        if nom == famille:
            norm = re.sub(motif, " ", norm)
            break
    for mot, valeur in table.items():
        if mot in norm:
            return valeur
    return None

# Ce qui s'affiche derrière le nombre, par colonne.
# Comment se nomme une pièce d'armure quand on la range.
_NOM_DE_TYPE = {
    "Char_Armor_Helmet": "casque", "Char_Armor_Torso": "plastron",
    "Char_Armor_Legs": "jambières", "Char_Armor_Arms": "bras",
    "Char_Armor_Undersuit": "combinaison", "Char_Armor_Backpack": "sac à dos",
    "MainThruster": "propulseur principal", "ManneuverThruster": "propulseur de manœuvre",
    "FuelTank": "réservoir à hydrogène", "QuantumFuelTank": "réservoir quantique",
    "ExternalFuelTank": "réservoir externe",
}

# Les sous-types de missile, tels qu'ils s'affichent. « Groundvehiclemissile »
# capitalisé est un identifiant, pas un mot français.
_NOM_DE_SOUS_TYPE = {
    "Missile": "missile", "Torpedo": "torpille", "Rocket": "roquette",
    "GroundVehicleMissile": "missile de véhicule terrestre",
    "Light": "légère", "Medium": "moyenne", "Heavy": "lourde",
    "Helmet": "casque",
}

_UNITE = {
    "s.temp_min": "°C", "s.temp_max": "°C",
    "s.oxygene": "", "s.poussee": "N", "s.capacite_scu": "SCU",
    "s.portee_missile": "m", "s.rayon_explosion": "m",
    "s.vitesse_missile": "m/s", "s.degats_missile": "",
    "s.radiation_max": "", "s.gforce_resistance": "G",
}


def detect_famille(question: str) -> tuple[str, tuple[str, ...]] | None:
    from .normalize import normalize

    norm = normalize(question or "")
    for nom, motif, types in _FAMILLES:
        if re.search(motif, norm):
            return nom, types
    return None


def detect_rarete(question: str) -> str | None:
    from .normalize import normalize

    norm = normalize(question or "")
    for motif, valeur in _RARETES:
        if re.search(motif, norm):
            return valeur
    return None


@dataclasses.dataclass(frozen=True)
class Ligne:
    nom: str
    valeur: float
    taille: str | None
    rarete: str | None
    prix: float | None = None


@dataclasses.dataclass(frozen=True)
class Classement:
    famille: str
    libelle: str          # « température minimale supportée »
    unite: str
    moins_est_mieux: bool
    rarete: str | None
    groupes: tuple[tuple[str | None, tuple[Ligne, ...]], ...]
    total: int
    # « La meilleure armure sous 5 000 aUEC » — le filtre et sa comptabilité :
    # ce qui est écarté se dit, un objet sans relevé n'étant pas gratuit.
    budget: float | None = None
    sans_prix: int = 0
    hors_budget: int = 0
    systeme: str | None = None
    hors_systeme: int = 0
    # La taille de port demandée, pour que le rendu dise sur quoi il a
    # filtré : « les boucliers de taille 2 » n'est pas « les boucliers ».
    taille: int | None = None
    # Le seuil honoré, dit dans la réponse : une borne appliquée sans être
    # annoncée est indiscernable d'une borne oubliée.
    seuil: str | None = None
    valeur: float | None = None


def _colonne_alimentee(con, types, stat: str) -> bool:
    """Cette colonne porte-t-elle une valeur pour cette famille ?

    Le nom vient d un vocabulaire ferme (`stats._COMPONENT_STATS`), jamais
    de la question : il est donc sur a interpoler. La sonde s arrete au
    premier objet trouve.
    """
    marques = ",".join("?" * len(types))
    return con.execute(
        f"SELECT 1 FROM items i JOIN item_stats s ON s.item_uuid = i.uuid "
        f"WHERE i.type IN ({marques}) AND {stat} IS NOT NULL LIMIT 1",
        list(types)).fetchone() is not None


def classer_equipement(con: sqlite3.Connection, query: str,
                       famille: str | None = None,
                       types: tuple[str, ...] = (),
                       stat: str | None = None,
                       libelle: str = "",
                       rarete: str | None = None,
                       taille: int | None = None,
                       seuil: str | None = None,
                       valeur: float | None = None,
                       budget: float | None = None,
                       systeme: str | None = None) -> Classement:
    """« Quelle armure pour une lune glacée », « quel missile va le plus loin ».

    Le routeur calcule la famille, la statistique et la rareté : cet outil ne
    résout aucune entité, il a donc son propre garde-fou côté routeur — sans
    famille nommée il avalerait toute question contenant « le meilleur ».
    """
    from .stats import COMPONENT_MOINS_EST_MIEUX

    if famille in ("propulseur", "réservoir"):
        # Mesuré, pas supposé : 6 noms distincts de propulseur et 18 de
        # réservoir dans tout le catalogue. Le dire vaut mieux que rendre un
        # classement de « Internal Tank » contre « Internal Tank ».
        raise NotFound(
            query,
            explication=(
                f"Le jeu ne nomme pas ses {famille}s : ce sont des pièces "
                f"internes de vaisseau, pas des articles qu'on choisit. "
                f"Mesuré — 6 noms distincts de propulseur et 18 de réservoir "
                f"dans tout le catalogue, souvent répétés à l'identique."))
    if famille == "radar":
        # Le cas inverse : 77 radars bien nommés, et **aucune colonne pour
        # les départager**. Mesuré — ni portée de détection, ni résolution,
        # ni discrétion de balayage ; il ne reste que la robustesse, la
        # masse et la signature, communes à tous les composants. Se taire
        # laisserait croire qu'on n'a pas compris la question.
        raise NotFound(
            query,
            explication=(
                "Le jeu ne publie aucune caractéristique propre aux radars — "
                "ni portée de détection, ni résolution, ni discrétion de "
                "balayage. Les 77 radars du catalogue ne se distinguent que "
                "par leur taille, leur masse et leur signature "
                "électromagnétique, communes à tous les composants."))
    if not types:
        raise NotFound("je n'ai pas reconnu la famille d'équipement")
    defaut = _DEFAUT.get(famille or "", ("s.armor_physical",
                                         "encaissement des dégâts physiques"))
    if not stat:
        stat, libelle = defaut
    elif famille and not _colonne_alimentee(con, types, stat):
        # **Un mot peut désigner deux axes selon la famille.**
        # « Résistance » vaut les points de bouclier sur un bouclier et
        # l'encaissement sur une armure ; le vocabulaire est une table
        # plate mot → colonne, donc il n'en connaît qu'un. Résultat :
        # « quel est le casque le plus résistant » cherchait
        # `shield_health` sur des casques et répondait « je n'ai pas
        # trouvé la donnée », alors que « les meilleurs casques » marchait.
        #
        # On ne corrige pas en ajoutant le mot — il est **déjà pris**, et
        # le redéclarer écrase l'autre sens en silence (mesuré en
        # l'écrivant : « le bouclier le plus résistant » cassait). On
        # retombe sur le défaut de la famille quand la colonne demandée
        # n'est alimentée pour aucun de ses objets, ce qui se **mesure**
        # au lieu de se supposer. Le rendu annonce son axe, donc le
        # lecteur voit sur quoi on a classé.
        stat, libelle = defaut

    # Le nom de colonne vient d'un vocabulaire fermé (`stats._COMPONENT_STATS`
    # et `_DEFAUT`), jamais de la question.
    if not re.fullmatch(r"s\.[a-z_]+", stat):
        raise NotFound("statistique inconnue")

    moins = stat in COMPONENT_MOINS_EST_MIEUX
    marques = ",".join("?" * len(types))
    params: list = list(types)
    filtre_rarete = ""
    if rarete:
        filtre_rarete = " AND i.rarity = ?"
        params.append(rarete)
    # **« Le meilleur bouclier taille 2 » est la question, pas « le
    # meilleur bouclier ».** Un composant se choisit d'abord par ce qui
    # rentre dans le port : classer les 73 boucliers ensemble met en tête
    # un S3 qu'un chasseur ne montera jamais. La contrainte se lisait dans
    # la question et se perdait ici — grille, ligne 1.
    filtre_taille = ""
    if taille is not None:
        filtre_taille = " AND i.size = ?"
        params.append(taille)
    # **« Plus de 5 000 de capacité » est un filtre, pas un décor.** Le
    # classement rendait la liste entière : exact et à côté, puisque le
    # joueur a posé une borne. Le comparateur vient d'un couple fermé, la
    # valeur est un nombre — rien de la question n'entre dans le SQL.
    filtre_seuil = ""
    if seuil in (">=", "<=") and valeur is not None:
        filtre_seuil = f" AND {stat} {seuil} ?"
        params.append(valeur)

    lignes = con.execute(
        f"SELECT i.name, i.type, i.subtype, i.rarity, i.size, {stat} AS valeur "
        f"  FROM item_stats s JOIN items i ON i.uuid = s.item_uuid "
        f" WHERE i.type IN ({marques}) AND {stat} IS NOT NULL "
        f"   AND i.name IS NOT NULL{filtre_rarete}{filtre_taille}"
        f"{filtre_seuil} "
        f" ORDER BY valeur {'ASC' if moins else 'DESC'}", params).fetchall()

    if not lignes:
        quoi = f" {rarete}" if rarete else ""
        de_taille = f" de taille {taille}" if taille is not None else ""
        raise NotFound(f"aucun{quoi} {famille}{de_taille} n'a de "
                       f"{libelle or 'valeur'} en base")

    # « La meilleure armure sous 5 000 aUEC » : le budget filtre par le prix
    # UEX, rapproché par le nom — le seul lien entre les deux sources. Grille,
    # lignes 1 et 9 : la contrainte s'applique, et ce qu'elle écarte se
    # compte — un objet sans relevé n'est pas gratuit, il est inconnu.
    prix_retenus: dict[str, float | None] = {}
    sans_prix = hors_budget = hors_systeme = 0
    if budget is not None or systeme:
        cotes_globales = {r[0].lower(): r[1] for r in con.execute(
            "SELECT name, MIN(price_buy) FROM uex_prices "
            "WHERE price_buy > 0 GROUP BY name")}
        cotes = cotes_globales
        if systeme:
            cotes = {r[0].lower(): r[1] for r in con.execute(
                "SELECT name, MIN(price_buy) FROM uex_prices "
                "WHERE price_buy > 0 AND LOWER(star_system) = LOWER(?) "
                "GROUP BY name", (systeme,))}
        gardees = []
        for r in lignes:
            cle_prix = (r["name"] or "").lower()
            p = cotes.get(cle_prix)
            if p is None:
                if systeme and cle_prix in cotes_globales:
                    hors_systeme += 1
                else:
                    sans_prix += 1
            elif budget is not None and p > budget:
                hors_budget += 1
            else:
                prix_retenus[r["name"]] = p
                gardees.append(r)
        lignes = gardees
        if not lignes:
            lieu = f" dans {systeme}" if systeme else ""
            plafond = f" sous {budget:.0f} aUEC" if budget is not None else ""
            raise NotFound(
                f"aucun {famille}{plafond}{lieu} parmi les prix relevés — "
                f"{hors_budget} au-dessus du budget, {hors_systeme} sans "
                f"point de vente relevé dans le système, {sans_prix} sans "
                "relevé nulle part")

    # **Classer par taille, comme les armes.** Une armure lourde protège plus
    # qu'une légère : les mélanger revient à répondre « la plus lourde » à
    # toutes les questions.
    #
    # Mais quand la famille couvre plusieurs **pièces** — « une armure », c'est
    # un casque, un plastron, des jambières —, c'est la pièce qui groupe et non
    # la taille : le joueur en porte une de chaque, et un classement mêlé
    # rendait cinq casques d'affilée. Le sous-type ne s'y prête d'ailleurs pas,
    # les casques portant « Helmet » là où les torses portent « Heavy ».
    par_piece = len(types) > 1
    par_taille: dict[str | None, list[Ligne]] = {}
    # **Un choix proposé trois fois n'est pas un choix.** Le catalogue porte
    # plusieurs entrées sous le même nom affiché — trois « Holdstrong » en
    # tête des boucliers, à la valeur près —, et le classement les listait
    # toutes : le joueur lit trois lignes identiques et croit à un bug. Même
    # règle que le dédoublonnage des candidats, sur le **nom affiché** et
    # non l'UUID.
    vus: set[str] = set()
    for r in lignes:
        if r["name"] in vus:
            continue
        vus.add(r["name"])
        if par_piece:
            cle = _NOM_DE_TYPE.get(r["type"], r["type"])
        else:
            brut = r["subtype"] if r["subtype"] not in ("UNDEFINED", None) else None
            cle = _NOM_DE_SOUS_TYPE.get(brut, brut) if brut else None
        par_taille.setdefault(cle, []).append(
            Ligne(nom=r["name"], valeur=r["valeur"], taille=cle,
                  rarete=r["rarity"], prix=prix_retenus.get(r["name"])))

    # Une seule taille — ou aucune — ne se groupe pas : ce serait un titre
    # pour une liste.
    if len(par_taille) <= 1:
        seule = next(iter(par_taille.values()))
        groupes = ((None, tuple(seule[:5])),)
    else:
        ordre = {"légère": 0, "moyenne": 1, "lourde": 2,
                 "casque": 0, "plastron": 1, "bras": 2, "jambières": 3,
                 "combinaison": 4}
        groupes = tuple(
            (t, tuple(v[:3]))
            for t, v in sorted(par_taille.items(),
                               key=lambda kv: (ordre.get(kv[0] or "", 9), kv[0] or "")))

    return Classement(
        famille=famille or "équipement", libelle=libelle,
        unite=_UNITE.get(stat, ""), moins_est_mieux=moins, rarete=rarete,
        groupes=groupes, total=len(lignes),
        budget=budget, sans_prix=sans_prix, hors_budget=hors_budget,
        systeme=systeme, hors_systeme=hors_systeme, taille=taille,
        seuil=seuil, valeur=valeur,
    )
