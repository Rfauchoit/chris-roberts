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
}


def stat_de_famille(famille: str, question: str) -> tuple[str, str] | None:
    """La statistique que ce mot désigne **pour cette famille**."""
    from .normalize import normalize

    table = _PAR_FAMILLE.get(famille or "")
    if not table:
        return None
    norm = normalize(question or "")
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


def classer_equipement(con: sqlite3.Connection, query: str,
                       famille: str | None = None,
                       types: tuple[str, ...] = (),
                       stat: str | None = None,
                       libelle: str = "",
                       rarete: str | None = None,
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
            f"le jeu ne nomme pas ses {famille}s : ce sont des pièces internes "
            f"de vaisseau, pas des articles qu'on choisit")
    if not types:
        raise NotFound("je n'ai pas reconnu la famille d'équipement")
    if not stat:
        stat, libelle = _DEFAUT.get(famille or "", ("s.armor_physical",
                                                    "encaissement des dégâts physiques"))

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

    lignes = con.execute(
        f"SELECT i.name, i.type, i.subtype, i.rarity, {stat} AS valeur "
        f"  FROM item_stats s JOIN items i ON i.uuid = s.item_uuid "
        f" WHERE i.type IN ({marques}) AND {stat} IS NOT NULL "
        f"   AND i.name IS NOT NULL{filtre_rarete} "
        f" ORDER BY valeur {'ASC' if moins else 'DESC'}", params).fetchall()

    if not lignes:
        quoi = f" {rarete}" if rarete else ""
        raise NotFound(f"aucun{quoi} {famille} n'a de {libelle or 'valeur'} en base")

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
    for r in lignes:
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
        systeme=systeme, hors_systeme=hors_systeme,
    )
