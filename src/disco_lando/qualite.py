"""Ce que la qualité des matériaux change sur un objet fabriqué.

Un joueur dit « un P6-LR 900 » : 900 est la **qualité** du matériau qui a servi
à le fabriquer, sur une échelle de 0 à 1000. Le jeu publie, pour chaque
blueprint, les statistiques que cette qualité fait varier et l'amplitude de la
variation — 5 695 modificateurs sur 1 546 blueprints, soit 97 % d'entre eux.

Quatre mesures ont dicté la forme de ce module :

- **Deux composants peuvent porter la même statistique sans s'accorder.** Le
  canon et les pièces de précision agissent tous deux sur les dégâts du P6-LR,
  et 591 couples (palier, statistique) sur 4 559 portent des plages
  **différentes**. On rend donc l'effet **par composant** ; quand un calcul
  exige un total, leurs écarts au barème s'additionnent, règle mesurée sur le
  terminal du P6-LR (+7,48 % + +5,24 % = +12,72 %).
- **Tout ne s'interpole pas.** 476 modificateurs n'ont aucune plage et 150 sont
  en `linear_integer_additive` — un autre mécanisme, sans bornes lisibles. On
  les **nomme** sans les chiffrer plutôt que de leur appliquer une règle qui
  n'est pas la leur.
- **Le multiplicateur va toujours dans le sens du gain.** Le recul descend de
  1,2 à 0,8, la consommation quantique de 1,2 à 1,0, les dégâts montent de 0,9
  à 1,1. C'est la donnée qui dit qu'une baisse de recul est une amélioration :
  la qualité 1000 est par construction le meilleur matériau. On n'a donc aucune
  liste de « statistiques où moins vaut mieux » à tenir à la main.
- **Le barème du catalogue n'est pas la valeur à qualité zéro.** Rien ne dit à
  quelle qualité correspond le chiffre de `item_stats`. On rend donc le
  multiplicateur, qui est publié, et on situe le point où il vaut 1 — ce qui
  laisse le lecteur placer le barème lui-même. Comparer deux qualités, en
  revanche, est **exact** : c'est le rapport de deux multiplicateurs publiés.
"""

from __future__ import annotations

import dataclasses
import math
import re
import sqlite3

from ._socle import NotFound, _row, est_un_accessoire, nomme_un_accessoire
from .normalize import normalize
from .resolver import resolve

QUALITE_MAX = 1000.0


def composer_multiplicateurs(multiplicateurs) -> float:
    """Compose les effets de composants comme le terminal du jeu.

    Le P6-LR observé avec Iron 874 et Hadanite 762 affiche +12,72 % : ses
    deux composants contribuent +7,48 % et +5,24 %. Le terminal additionne
    donc les écarts au barème, il ne multiplie pas les deux facteurs
    (ce qui donnerait +13,11 %). La règle reste centralisée ici pour que le
    panneau, la létalité et les paliers ne puissent pas diverger.
    """
    valeurs = tuple(float(mult) for mult in multiplicateurs)
    return 1.0 + sum(mult - 1.0 for mult in valeurs)


# La colonne de `item_stats` qui porte le barème, quand elle existe. Huit clés
# sur vingt-quatre en ont une ; les seize autres — le recul, la mitigation
# d'armure, l'assistance de visée — ne sont pas en base, et le multiplicateur
# reste la seule chose vraie qu'on puisse en dire.
_COLONNE = {
    "weapon_damage": ("alpha", "dégâts par tir"),
    "weapon_firerate": ("rounds_per_minute", "cadence"),
    "health_maxhealth": ("health", "intégrité"),
    "shield_maxhealth": ("shield_health", "bouclier"),
    "quantum_speed": ("qt_drive_speed", "vitesse quantique"),
    "quantum_fuelrequirement": ("qt_fuel_rate", "consommation quantique"),
    "itemresource_coolantgeneration": ("cooling_rate", "refroidissement"),
    "itemresource_powergeneration": ("power_rate", "génération d'énergie"),
}


@dataclasses.dataclass(frozen=True)
class Effet:
    """L'effet d'un composant sur une statistique, à une qualité donnée."""

    cle: str
    nom: str
    composant: str | None
    qualite_min: float | None
    qualite_max: float | None
    mult_min: float | None
    mult_max: float | None
    chiffrable: bool
    hors_plage: bool = False
    #: La **clé** du composant, telle que le jeu la nomme (`FRAME`, `EMITTER`).
    #: `composant` en est le libellé lisible ; c'est la clé qui se recoupe avec
    #: `blueprint_ingredients.group_key`, les deux tables la tirant du **même**
    #: nœud de groupe à l'ingestion (`_walk_requirements` et `_walk_modifiers`).
    #: Sans elle, on ne peut pas dire quel matériau pilote quelle statistique.
    poste: str | None = None

    def multiplicateur(self, qualite: float) -> float | None:
        """Interpolation linéaire, bornée aux extrémités : au-delà de la
        qualité maximale le jeu ne promet rien de plus."""
        if not self.chiffrable:
            return None
        etendue = self.qualite_max - self.qualite_min
        if etendue <= 0:
            return self.mult_max
        t = (qualite - self.qualite_min) / etendue
        t = min(1.0, max(0.0, t))
        return self.mult_min + t * (self.mult_max - self.mult_min)

    @property
    def monte(self) -> bool:
        """La statistique augmente-t-elle avec la qualité ? Le sens du gain est
        le même dans les deux cas — c'est l'affichage qui en a besoin."""
        return bool(self.chiffrable and self.mult_max >= self.mult_min)

    def qualite_neutre(self) -> float | None:
        """La qualité à laquelle le multiplicateur vaut exactement 1, c'est-à-
        dire l'endroit où se situe le barème du catalogue. Elle vaut 500 sur
        une plage 0,9-1,1 et 1000 sur une plage 0,85-1,0."""
        if not self.chiffrable or self.mult_min == self.mult_max:
            return None
        t = (1.0 - self.mult_min) / (self.mult_max - self.mult_min)
        if not 0.0 <= t <= 1.0:
            return None
        return self.qualite_min + t * (self.qualite_max - self.qualite_min)


@dataclasses.dataclass(frozen=True)
class FicheQualite:
    nom: str
    blueprint_uuid: str
    item_uuid: str | None
    qualite: float
    borne_max: float
    effets: tuple[Effet, ...]
    barèmes: dict          # clé de stat -> valeur du catalogue
    variantes: tuple[str, ...] = ()


def _effets(con: sqlite3.Connection, blueprint_uuid: str,
            tier_id: int | None = None) -> tuple[Effet, ...]:
    """Les effets publiés d'un blueprint, tous paliers confondus par défaut.

    `tier_id` restreint à **un** palier : le poste de fabrication charge un
    palier à la fois, et fusionner les paliers y ferait figurer un composant
    que la recette affichée ne demande pas.
    """
    lignes = con.execute(
        "SELECT m.cle, COALESCE(m.nom_fr, m.nom) AS nom, "
        "       COALESCE(m.group_name_fr, m.group_name) AS group_name, "
        "       m.group_key, m.quality_min, m.quality_max, "
        "       m.mult_min, m.mult_max, m.interpolation "
        "  FROM blueprint_modifiers m "
        "  JOIN blueprint_tiers t ON t.id = m.tier_id "
        " WHERE t.blueprint_uuid = ? AND (? IS NULL OR t.id = ?) "
        " ORDER BY m.cle, m.group_name",
        (blueprint_uuid, tier_id, tier_id),
    ).fetchall()
    effets = []
    for r in lignes:
        bornes = (r["quality_min"], r["quality_max"], r["mult_min"], r["mult_max"])
        chiffrable = r["interpolation"] == "linear" and all(b is not None for b in bornes)
        # « <= PLACEHOLDER => » est un composant que le jeu n'a pas nommé : le
        # montrer serait présenter un identifiant pour une pièce.
        composant = r["group_name"]
        if composant and composant.strip().startswith("<="):
            composant = None
        effets.append(Effet(
            cle=r["cle"], nom=r["nom"] or r["cle"], composant=composant,
            qualite_min=r["quality_min"], qualite_max=r["quality_max"],
            mult_min=r["mult_min"], mult_max=r["mult_max"], chiffrable=chiffrable,
            poste=r["group_key"],
        ))
    return tuple(effets)


def effets_par_poste(con: sqlite3.Connection, blueprint_uuid: str,
                     tier_id: int | None = None
                     ) -> dict[str | None, tuple[Effet, ...]]:
    """Les mêmes effets, rangés par **emplacement de fabrication**.

    C'est la vue dont le poste de fabrication a besoin : la fiche de qualité
    applique une qualité unique à tout l'objet, le fabricateur charge un
    matériau par emplacement. La clé est celle du jeu (`FRAME`, `EMITTER`) ;
    `None` regroupe les modificateurs qu'aucun groupe ne porte — ils existent
    et se **disent**, mais on ne peut les rattacher à aucun matériau.
    """
    par_poste: dict[str | None, list[Effet]] = {}
    for effet in _effets(con, blueprint_uuid, tier_id):
        par_poste.setdefault(effet.poste, []).append(effet)
    return {cle: tuple(v) for cle, v in par_poste.items()}


def multiplicateur_cumule(con: sqlite3.Connection, item_uuid: str,
                          cle: str, qualite: float
                          ) -> tuple[float, int, float] | None:
    """Le multiplicateur utilisable par un **calcul** pour une statistique.

    La fiche de qualité garde les composants séparés, parce que le jeu ne dit
    pas comment leurs effets se combinent. Un duel doit néanmoins produire un
    scénario : il multiplie alors les plages linéaires publiées et rend le
    nombre de composants, afin que le rendu puisse annoncer cette hypothèse.
    Une plage absente ou non linéaire fait abandonner tout le calcul plutôt
    que de compléter silencieusement ce qui manque.
    """
    lignes = con.execute(
        "SELECT m.quality_min, m.quality_max, m.mult_min, m.mult_max, "
        "       m.interpolation "
        "FROM blueprint_modifiers m "
        "JOIN blueprint_tiers t ON t.id = m.tier_id "
        "JOIN blueprints b ON b.uuid = t.blueprint_uuid "
        "WHERE b.output_uuid = ? AND m.cle = ?",
        (item_uuid, cle),
    ).fetchall()
    if not lignes:
        return None
    produit = 1.0
    borne_max = 0.0
    for ligne in lignes:
        valeurs = (ligne["quality_min"], ligne["quality_max"],
                   ligne["mult_min"], ligne["mult_max"])
        if ligne["interpolation"] != "linear" or any(v is None for v in valeurs):
            return None
        qmin, qmax, mmin, mmax = valeurs
        part = min(max((qualite - qmin) / ((qmax - qmin) or 1), 0.0), 1.0)
        produit *= mmin + (mmax - mmin) * part
        borne_max = max(borne_max, qmax)
    return produit, len(lignes), borne_max


def _blueprint(con: sqlite3.Connection, terme: str, question: str = "",
               *, exiger: bool = True
               ) -> tuple[str | None, str, str | None, tuple[str, ...]]:
    """Le blueprint d'un objet nommé. Le joueur dit « le P6-LR » sans préciser
    la livrée ; les quatre déclinaisons partagent exactement les mêmes
    modificateurs, donc on répond et on **dit** lesquelles partagent.

    Le nom nu d'une arme désigne l'arme, jamais son chargeur : « les stats d'un
    P6-LR 900 » sortait « P6-LR Magazine (8 cap) » parmi les variantes. Le mot
    « chargeur » se cherche dans la **question entière**, le routeur ne
    transmettant ici que « p6 lr »."""
    candidats = resolve(con, terme, entity_types=("blueprint", "item"), limit=8).candidates
    if not candidats:
        raise NotFound(f"je ne connais pas « {terme} »")

    if not nomme_un_accessoire(question or terme):
        armes = [c for c in candidats if not est_un_accessoire(c.name)]
        if armes:
            candidats = armes

    # **Le blueprint doit rester dans la fenêtre de score.** On prenait le
    # premier candidat de type `blueprint` de la liste, quel que soit son
    # rang : « P4-AR "Fortuna" Rifle », qui n'a pas de recette à lui, sortait
    # la recette du **CQ7 Rifle** — une autre arme, avec d'autres chiffres,
    # sous le nom demandé. Un candidat lointain n'est pas un repli, c'est une
    # erreur. Mesuré le 2026-08-10.
    plafond = max(c.score for c in candidats)
    fenetre = [c for c in candidats if c.score >= plafond - 5.0]

    for c in fenetre:
        if c.entity_type == "blueprint":
            uuid = c.entity_id
            break
    else:
        # Nommé côté objet : on retrouve son blueprint par la sortie.
        uuid = None
        for c in fenetre:
            r = _row(con, "SELECT uuid FROM blueprints WHERE output_uuid = ?", c.entity_id)
            if r:
                uuid = r["uuid"]
                break
        if uuid is None:
            if not exiger:
                # **Toutes les armes ne se fabriquent pas, et la question ne
                # le demandait pas.** « Combien de dégâts dans le torse lourd
                # d'un CQ7 » ne dépend d'aucune recette : le CQ7 n'en a pas et
                # la question tombait en « je n'ai pas trouvé la donnée »,
                # alors que l'alpha et l'armure suffisent. La qualité devient
                # simplement un levier absent, ce que le rendu dit.
                objets = [c for c in fenetre if c.entity_type == "item"]
                if objets:
                    r = _row(con, "SELECT uuid, name FROM items WHERE uuid = ?",
                             objets[0].entity_id)
                    if r:
                        return None, r["name"], r["uuid"], ()
            raise NotFound(
            terme,
            explication=(
                f"**{terme}** ne se fabrique pas : le jeu ne lui donne "
                f"aucune recette, donc aucune qualité de matériaux ne le "
                f"modifie. Sur les 10 804 objets du catalogue, 1 588 "
                f"seulement sont fabricables."))

    r = _row(con, "SELECT uuid, output_name, output_uuid FROM blueprints WHERE uuid = ?", uuid)
    if not r:
        raise NotFound(
            terme,
            explication=(
                f"**{terme}** ne se fabrique pas : le jeu ne lui donne "
                f"aucune recette, donc aucune qualité de matériaux ne le "
                f"modifie. Sur les 10 804 objets du catalogue, 1 588 "
                f"seulement sont fabricables."))

    # Les variantes qui partagent la même sensibilité à la qualité : le joueur
    # a nommé une famille, pas un exemplaire. **Dans une fenêtre de score** :
    # sans elle, les candidats lointains du résolveur passaient pour des
    # variantes — « Mêmes effets sur CQ7 Rifle » sur la fiche du P6-LR, une
    # affirmation fausse sur une arme sans rapport.
    partagent = tuple(
        c.name for c in candidats
        if c.entity_type == "blueprint" and c.entity_id != uuid
        and c.score >= plafond - 5.0
    )
    return r["uuid"], r["output_name"], r["output_uuid"], partagent[:4]


def fiche_qualite(con: sqlite3.Connection, query: str, qualite: float,
                  question: str = "") -> FicheQualite:
    """« C'est quoi les statistiques d'un P6-LR 900 »."""
    uuid, nom, item_uuid, variantes = _blueprint(con, query, question)
    effets = _effets(con, uuid)
    if not effets:
        raise NotFound(f"la qualité des matériaux ne change rien sur {nom}")

    bornes = [e.qualite_max for e in effets if e.qualite_max]
    borne_max = max(bornes) if bornes else QUALITE_MAX

    barèmes = {}
    if item_uuid:
        colonnes = {_COLONNE[e.cle][0] for e in effets if e.cle in _COLONNE}
        if colonnes:
            noms = ", ".join(sorted(colonnes))  # noms fermés par _COLONNE
            r = _row(con, f"SELECT {noms} FROM item_stats WHERE item_uuid = ?", item_uuid)
            if r:
                for e in effets:
                    if e.cle in _COLONNE:
                        v = r[_COLONNE[e.cle][0]]
                        if v is not None:
                            barèmes[e.cle] = v

    return FicheQualite(
        nom=nom, blueprint_uuid=uuid, item_uuid=item_uuid, qualite=qualite,
        borne_max=borne_max, effets=effets, barèmes=barèmes, variantes=variantes,
    )


@dataclasses.dataclass(frozen=True)
class Ecart:
    nom: str
    composant: str | None
    mult_a: float
    mult_b: float
    barème: float | None
    cle: str

    @property
    def rapport(self) -> float:
        return self.mult_b / self.mult_a if self.mult_a else 1.0


@dataclasses.dataclass(frozen=True)
class Comparaison:
    nom: str
    qualite_a: float
    qualite_b: float
    ecarts: tuple[Ecart, ...]
    sans_effet: tuple[str, ...]
    non_chiffrables: tuple[str, ...]


def comparer_qualites(con: sqlite3.Connection, query: str,
                      qualite_a: float, qualite_b: float,
                      question: str = "") -> Comparaison:
    """« Quelle différence entre un P6-LR 900 et un P6-LR 990 » — le rapport de
    deux multiplicateurs publiés, donc exact, sans passer par le barème."""
    fiche = fiche_qualite(con, query, qualite_b, question)
    ecarts, sans_effet, non_chiffrables = [], [], []
    for e in fiche.effets:
        if not e.chiffrable:
            non_chiffrables.append(e.nom)
            continue
        ma, mb = e.multiplicateur(qualite_a), e.multiplicateur(qualite_b)
        if abs(mb - ma) < 1e-9:
            sans_effet.append(e.nom)
            continue
        ecarts.append(Ecart(nom=e.nom, composant=e.composant, mult_a=ma, mult_b=mb,
                            barème=fiche.barèmes.get(e.cle), cle=e.cle))
    # Le plus gros écart en premier : c'est lui qui justifie de payer plus cher.
    ecarts.sort(key=lambda x: -abs(x.rapport - 1.0))
    return Comparaison(
        nom=fiche.nom, qualite_a=qualite_a, qualite_b=qualite_b,
        ecarts=tuple(ecarts),
        sans_effet=tuple(dict.fromkeys(sans_effet)),
        non_chiffrables=tuple(dict.fromkeys(non_chiffrables)),
    )


# Les points auxquels la chaîne s'affiche. Ce n'est **pas** un barème du jeu —
# l'échelle est continue et les fichiers ne définissent aucun palier — c'est un
# choix d'affichage : les deux bornes, la référence usuelle (500, où la plage
# 0,9-1,1 vaut 1), et les deux valeurs que l'utilisateur emploie lui-même.
POINTS_DE_CHAINE = (0.0, 500.0, 900.0, 990.0, 1000.0)


def echelle_de_qualite(con: sqlite3.Connection, query: str) -> dict:
    """« Quels sont les différents types de qualité ? » — il n'y en a pas.

    L'utilisateur attend des paliers (« base, 570 il me semble, 900, 990 ») ;
    les fichiers du jeu n'en définissent **aucun** : la qualité est une échelle
    continue, et chaque statistique s'interpole linéairement entre ses bornes.
    Répondre des paliers serait le §7 violé ; répondre « je ne sais pas »
    serait faux aussi — on sait très bien, et c'est chiffré.
    """
    mesure = _row(con, "SELECT COUNT(*) AS n, MIN(quality_min) AS lo, "
                       "MAX(quality_max) AS hi FROM blueprint_modifiers "
                       "WHERE interpolation = 'linear'")
    if not mesure or not mesure["n"]:
        raise NotFound(query)
    blueprints = _row(con, "SELECT COUNT(DISTINCT t.blueprint_uuid) AS n "
                           "FROM blueprint_modifiers m "
                           "JOIN blueprint_tiers t ON t.id = m.tier_id")
    return {"modificateurs": mesure["n"], "borne_min": mesure["lo"],
            "borne_max": mesure["hi"], "blueprints": blueprints["n"],
            "points": POINTS_DE_CHAINE, "resolution": None}


def chaine_de_qualites(con: sqlite3.Connection, query: str,
                       question: str = "") -> dict:
    """« Les caractéristiques de toutes les qualités du P6-LR ».

    Demande de l'utilisateur : le format d'une comparaison, mais sur toute la
    chaîne. L'échelle étant continue, « toutes les qualités » se rend aux
    points d'affichage — chaque nombre est l'interpolation exacte du
    multiplicateur publié, pas un palier du jeu.
    """
    fiche = fiche_qualite(con, query, POINTS_DE_CHAINE[-1], question)
    return {"fiche": fiche, "points": POINTS_DE_CHAINE, "resolution": None}


# ------------------------------------------------------------ lire la qualité

# Ce qui suit un nombre quand ce nombre n'est **pas** une qualité. Sans cette
# liste, « quels vaisseaux ont plus de 100 SCU » et « un vaisseau à 2 millions »
# deviendraient des questions de fabrication.
_UNITE = (
    r"scu|uec|auec|aeuc|ueec|dps|m\s*/\s*s|km|metres?|m|places?|millions?|"
    r"milliers?|k|tonnes?|kg|secondes?|minutes?|heures?|balles?|degats?|%"
)

# Un mot de comparaison **devant** le nombre en fait un seuil, pas une qualité.
# Le contrôle se fait en regardant en arrière, ce qu'un `re` Python ne sait pas
# faire en longueur variable : on repère les nombres, puis on relit la queue du
# texte qui les précède. Écrit d'abord en lookahead — donc en avant —, le
# garde-fou ne servait à rien et « moins de 500 de bouclier » passait pour une
# qualité ; seule l'unité rattrapait « plus de 100 SCU », par accident.
_SEUIL_DEVANT = re.compile(
    r"\b(?:plus|moins|mieux|au dessus|au dessous|sous|dessus|dessous|maxi\w*|"
    r"mini\w*|superieur\w*|inferieur\w*|environ|entre|depasse\w*)\s+"
    r"(?:de\s+|a\s+|que\s+|d\s+)?$")

_NOMBRE = re.compile(r"(?<![\w.,])(\d{1,4})(?![\w.,])")

_UNITE_APRES = re.compile(rf"^\s*(?:{_UNITE})\b")

_MOT_DE_QUALITE = re.compile(
    r"\bqualites?\b|\bniveaux?\b|\bgrades?\b|\brangs?\b|\bpalliers?\b|\bpaliers?\b")


def detect_qualites(question: str) -> list[float]:
    """Les qualités de fabrication citées dans une question.

    Trois garde-fous, chacun payé par un faux positif réel du projet :

    - **un nombre nu n'est pas une entité**, et il n'est pas non plus une
      qualité dès qu'une unité le suit — « plus de 100 SCU » est un seuil ;
    - **un mot de comparaison devant le nombre en fait un seuil** — c'est le
      même mécanisme que `_SEUIL`, et sans lui « moins de 900 » entrait ici ;
    - **hors de 0-1000, ce n'est pas une qualité**, la borne étant publiée par
      le jeu sur tous les modificateurs chiffrables.

    Le numéro de modèle ne gêne pas : « P6-LR » se normalise en « p6 lr », et
    le 6 n'y est jamais un jeton isolé.
    """
    from .normalize import normalize

    norm = normalize(question or "")
    vus = []
    for m in _NOMBRE.finditer(norm):
        if _SEUIL_DEVANT.search(norm[:m.start()]):
            continue
        if _UNITE_APRES.match(norm[m.end():]):
            continue
        v = float(m.group(1))
        if 0 <= v <= QUALITE_MAX and v not in vus:
            vus.append(v)
    return vus


def nomme_une_qualite(question: str) -> bool:
    """La question parle-t-elle explicitement de qualité, de niveau ou de
    grade ? Un nombre seul ne suffit pas à le supposer."""
    from .normalize import normalize

    return bool(_MOT_DE_QUALITE.search(normalize(question or "")))


# En dessous de cette valeur, un nombre nu dans une question est presque
# toujours un **compte** — « les stats de 3 vaisseaux » — et non une qualité.
# L'échelle du jeu va de 0 à 1000 et un joueur qui donne une qualité l'écrit
# dans les centaines : 900, 990, 750. On ne descend sous ce plancher que si la
# question dit explicitement « qualité », « niveau » ou « grade ».
QUALITE_PLANCHER = 100.0


def qualites_lues(question: str) -> list[float]:
    """Les qualités d'une question, plancher appliqué."""
    lues = detect_qualites(question)
    if nomme_une_qualite(question):
        return lues
    return [v for v in lues if v >= QUALITE_PLANCHER]


# ------------------------------------------------- à partir de quelle qualité

# **Les règles de terrain, données par l'utilisateur le 2026-08-10.** Aucun
# fichier du jeu ne publie les points de vie d'un joueur ni les multiplicateurs
# de zone : ils se mesurent en jeu, et c'est l'utilisateur qui les a mesurés.
# Tout le reste du calcul — alpha, qualité, accessoire, résistance de l'armure —
# sort de la base. Le rendu sépare les deux, pour qu'on sache ce qui se vérifie
# dans les données et ce qui vient du terrain.
PV_JOUEUR = 100.0

MULT_ZONE = {"tete": 1.5, "torse": 1.0, "jambes": 0.8}

# Sans casque, la tête n'est plus protégée du tout **et** encaisse six fois les
# dégâts : c'est la seule zone dont le multiplicateur dépend de l'équipement.
MULT_TETE_SANS_CASQUE = 6.0

_PIECE = {"tete": "Char_Armor_Helmet", "torse": "Char_Armor_Torso",
          "jambes": "Char_Armor_Legs"}

_CLASSES = {"legere": "Light", "moyenne": "Medium", "lourde": "Heavy"}

_LIBELLE_CLASSE = {"legere": "légère", "moyenne": "moyenne", "lourde": "lourde"}

_CLASSE_ENTREE = {"legere": "legere", "light": "legere", "légère": "legere",
                  "moyenne": "moyenne", "medium": "moyenne",
                  "lourde": "lourde", "heavy": "lourde"}

_ZONES = ((r"\btetes?\b|\bcranes?\b|\bhead ?shots?\b|\bcasques?\b", "tete"),
          (r"\bjambes?\b|\bpieds?\b|\bcuisses?\b", "jambes"),
          (r"\btorses?\b|\bbustes?\b|\bpoitrines?\b|\bcorps\b|\bventre\b", "torse"))

# « Est-ce que je le tue d'une balle » — le vocabulaire qui distingue cette
# question de « c'est quoi les stats d'un P6-LR 900 ». Sans lui, le mot
# « qualité » suffirait à voler la fiche, qui est la bonne réponse neuf fois
# sur dix.
_MOT_DE_MISE_A_MORT = re.compile(
    r"\bone ?shots?\b|\bos\b|\btue\w*\b|\bkill\w*\b|\babattre\b|\bdescendre\b|"
    r"\bd(?:'|e )une? (?:balle|tir|cartouche|coup)\b|\bd(?:'|e )un (?:tir|coup)\b")

# Comment un joueur nomme une famille d'accessoire, et ce que le jeu écrit.
# Le catalogue est anglais et la question française : « et si je mets un
# silencieux » ne trouvait rien face à « Tacit Suppressor2 ».
_FAMILLE_ACCESSOIRE = (
    (r"\bsilencieux\b|\bsilencieuse\b|\bsuppresseurs?\b", "suppressor"),
    (r"\bcompensateurs?\b|\bfrein de bouche\b", "compensator"),
    (r"\bstabilisateurs?\b", "stabilizer"),
    (r"\bcache[- ]?flammes?\b", "flash hider"),
)


def detect_zone(question: str) -> str | None:
    """La partie du corps visée. Sans elle, on ne suppose pas la tête : le
    bonus de 1,5 changerait le verdict sans que le joueur l'ait demandé."""
    norm = normalize(question or "")
    for motif, zone in _ZONES:
        if re.search(motif, norm):
            return zone
    return None


def detect_accessoire_cite(question: str) -> str | None:
    """« Avec un silencieux » — la famille, à défaut d'un nom d'accessoire."""
    norm = normalize(question or "")
    for motif, famille in _FAMILLE_ACCESSOIRE:
        if re.search(motif, norm):
            return famille
    return None


_COMPTE_DE_BALLES = re.compile(
    r"\bcombien de (?:balles?|tirs?|coups?|cartouches?|munitions?)\b|"
    r"\bnombre de (?:balles?|tirs?|coups?)\b")


_COMPTE_DE_DEGATS = re.compile(
    r"\bcombien de degats?\b|\bcombien ca (?:fait|met)\b|"
    r"\bquels? degats?\b|\bdegats? (?:dans|sur|contre|au|en)\b|"
    r"\bca fait combien\b")


def demande_des_degats(question: str) -> bool:
    """« Combien de dégâts dans le torse lourd d'un CQ7 » — la question porte
    sur les **dégâts**, pas sur le tir unique.

    Remarque de l'utilisateur, 2026-08-11 : « je ne te demande pas comment
    tuer d'une balle ». Le calcul est le même, la réponse ne l'est pas — on
    mène par le nombre de dégâts, et le compte de balles vient ensuite.
    """
    return bool(_COMPTE_DE_DEGATS.search(normalize(question or "")))


def demande_un_compte_de_balles(question: str) -> bool:
    """« Combien de balles dans la tête pour un P4-AR » — la même mécanique,
    mais c'est le **compte** qu'on veut, pas le seuil du tir unique. Sans ce
    volet, l'arme qui tue d'une balle taisait les paliers, qui sont
    précisément ce qu'on lui demandait."""
    return bool(_COMPTE_DE_BALLES.search(normalize(question or "")))


def demande_une_mise_a_mort(question: str) -> bool:
    """Le garde-fou de l'outil : sans ce vocabulaire, la question de qualité
    est celle de la fiche."""
    return bool(_MOT_DE_MISE_A_MORT.search(normalize(question or "")))

_LIBELLE_ZONE = {"tete": "dans la tête", "torse": "dans le torse",
                 "jambes": "dans les jambes"}

# Les entrées de test du catalogue : réserve de 99 999 balles, alpha à cinq
# chiffres. Mesuré, un second « BR-2 Shotgun » y sort à 95 350 de dégâts contre
# 88 pour l'arme réelle — le proposer comme solution serait absurde.
_RESERVE_DE_TEST = 99999


def _armes_qui_suffisent(con: sqlite3.Connection, resistances: dict,
                         mult_zone: float, pv: float,
                         limite: int = 4) -> list[dict]:
    """Les armes dont le barème publié suffit **déjà**, sans bonus de qualité.

    Quand aucune qualité ne sauve l'arme demandée, dire « impossible » et
    s'arrêter laisse le joueur sans rien. On propose donc les armes qui y
    arrivent — et **les plus légères d'abord** : la question est « laquelle
    prendre », pas « laquelle frappe le plus fort », et le lance-roquettes
    répondrait toujours.
    """
    # Les dégâts utiles se calculent en SQL, type par type, avec la résistance
    # de la pièce touchée. Les noms de colonne viennent de `_TYPES_LETAUX`,
    # vocabulaire fermé : rien de ce que le joueur tape n'entre ici.
    utiles = " + ".join(
        f"COALESCE(s.{cle}, 0) * {float(resistances.get(colonne) or 1.0)}"
        for cle, colonne, _ in _TYPES_LETAUX)
    utiles = f"({utiles}) * COALESCE(s.pellets_per_shot, 1) * {float(mult_zone)}"
    lignes = con.execute(
        f"SELECT i.name, i.subtype, {utiles} AS utiles "
        "FROM items i JOIN item_stats s ON s.item_uuid = i.uuid "
        "WHERE i.type = 'WeaponPersonal' AND i.name IS NOT NULL "
        "  AND COALESCE(s.ammo_capacity, 0) < ? "
        f"  AND {utiles} >= ? "
        f"ORDER BY {utiles}", (_RESERVE_DE_TEST, pv)).fetchall()
    vues: dict[str, dict] = {}
    for r in lignes:
        # Les livrées d'une même arme partagent ses chiffres : « Scourge
        # "Nightstalker" Railgun » n'est pas une seconde réponse.
        base = re.sub(r'\s*"[^"]*"\s*', " ", r["name"]).strip()
        vues.setdefault(base, {"nom": base, "classe": r["subtype"],
                               "degats": r["utiles"]})
        if len(vues) >= limite:
            break
    return list(vues.values())


# **Les types de dégâts qui retirent des points de vie, et la colonne
# d'armure qui leur répond.** L'Atzkav publie 165 d'alpha : 120 d'énergie,
# 35 de distorsion et 10 de stun. La distorsion vide un condensateur, le stun
# assomme — ni l'un ni l'autre ne tue. Les sommer et les opposer à la seule
# résistance physique donnait 148,5 de dégâts utiles là où il y en a 108.
# Remarque de l'utilisateur, 2026-08-10 : « tes dégâts me semblent étranges
# sur l'Atzkav ».
_TYPES_LETAUX = (
    ("alpha_physical", "armor_physical", "physique"),
    ("alpha_energy", "armor_energy", "énergie"),
    ("alpha_thermal", "armor_thermal", "thermique"),
    ("alpha_biochemical", "armor_biochemical", "biochimique"),
)

# Ce qui est publié dans l'alpha mais ne coûte aucun point de vie.
_TYPES_NON_LETAUX = (("alpha_distortion", "distorsion"),
                     ("alpha_stun", "stun"))


def _resistance(con: sqlite3.Connection, zone: str, classe: str) -> dict | None:
    """Les résistances d'une pièce d'armure, prises sur la pièce **la plus
    répandue** de sa classe et non sur une moyenne.

    Mesuré sur les 168 casques lourds : 165 valent 0,6, deux valent 0,125 et un
    vaut 1,0. La moyenne (0,597) ne décrit aucun casque réel, et l'écart n'est
    pas du bruit — le BUL-H4 encaisse presque cinq fois mieux qu'un casque
    lourd ordinaire. On répond donc sur le cas courant et on **nomme**
    l'exception, sans quoi le seuil annoncé serait faux contre elle.

    Les six résistances se prennent **sur la même pièce**, jamais type par
    type : un casque qui résisterait mieux à l'énergie et moins au balistique
    existerait sinon en base sans exister dans le jeu.
    """
    piece, sous_type = _PIECE.get(zone), _CLASSES.get(classe)
    if not piece or not sous_type:
        return None
    colonnes = [c for _, c, _ in _TYPES_LETAUX]
    lignes = con.execute(
        f"SELECT {', '.join(colonnes)}, COUNT(*) n FROM items i "
        "JOIN item_stats s ON s.item_uuid = i.uuid "
        "WHERE i.type = ? AND i.subtype = ? AND s.armor_physical IS NOT NULL "
        f"GROUP BY {', '.join(colonnes)} ORDER BY n DESC", (piece, sous_type)
    ).fetchall()
    if not lignes:
        return None
    courant = lignes[0]
    resistances = {c: courant[c] for c in colonnes}
    # Une exception ne compte que si elle protège **mieux** : une pièce à 1,0
    # n'encaisse rien de plus, elle ne change aucun verdict. On compare sur le
    # physique, qui est renseigné sur toutes les pièces.
    meilleures = [r for r in lignes[1:]
                  if r["armor_physical"] is not None
                  and r["armor_physical"] < courant["armor_physical"]]
    exception = None
    if meilleures:
        r = min(meilleures, key=lambda x: x["armor_physical"])
        noms = [n for n, in con.execute(
            "SELECT i.name FROM items i JOIN item_stats s ON s.item_uuid = i.uuid "
            "WHERE i.type = ? AND i.subtype = ? AND s.armor_physical = ? "
            "ORDER BY i.name LIMIT 2",
            (piece, sous_type, r["armor_physical"]))]
        exception = {"resistance": r["armor_physical"], "nombre": r["n"],
                     "noms": noms,
                     "resistances": {c: r[c] for c in colonnes}}
    return {"resistance": courant["armor_physical"],
            "resistances": resistances, "nombre": courant["n"],
            "total": sum(r["n"] for r in lignes), "exception": exception}


def _degats_utiles(stats, resistances: dict) -> tuple[float, list[dict], list[dict]]:
    """Ce qu'un tir retire vraiment, type par type, et ce qui ne tue pas.

    Rend le total létal, le détail par type et la part publiée qui n'entre pas
    dans le calcul — parce que la différence entre 165 et 120 doit se lire dans
    la réponse, sinon elle passe pour une erreur.
    """
    detail, ecarte = [], []
    total = 0.0
    for cle, colonne, libelle in _TYPES_LETAUX:
        brut = stats[cle] or 0.0
        if not brut:
            continue
        resistance = resistances.get(colonne)
        if resistance is None:
            resistance = 1.0
        total += brut * resistance
        detail.append({"type": libelle, "brut": brut, "resistance": resistance,
                       "passe": brut * resistance})
    for cle, libelle in _TYPES_NON_LETAUX:
        brut = stats[cle] or 0.0
        if brut:
            ecarte.append({"type": libelle, "brut": brut})
    return total, detail, ecarte


def _accessoires_de_degats(con: sqlite3.Connection, arme_uuid: str) -> list[dict]:
    """Les accessoires montables sur cette arme qui changent ses dégâts.

    116 accessoires publient un `DamageMultiplier` dans
    `stdItem.WeaponModifier`, et **tous les silencieux ne se valent pas** : le
    Tacit coûte 8 % de dégâts, le Stoic n'en coûte aucun et ne baisse que le
    bruit. Un joueur suppose l'inverse — d'où la mesure plutôt que la règle.

    **Le type et la taille ne suffisent pas.** Le port `barrel_attach` du
    P6-LR exige les tags `FPS_Barrel` et `ballistic_attach` ; l'Emod
    « Tweaker » Stabilizer est un `energy_attach` de taille 2, donc de la
    bonne famille et de la bonne taille, et il ne se monte pas. On le
    proposait, chiffres à l'appui — remarque de l'utilisateur du 2026-08-10.
    2 199 ports sur 33 762 portent une telle exigence.
    """
    ports = con.execute(
        "SELECT accepted, min_size, max_size, required_tags FROM item_ports "
        "WHERE item_uuid = ? AND accepted LIKE 'WeaponAttachment.%'",
        (arme_uuid,)).fetchall()
    trouves: dict[str, dict] = {}
    for port in ports:
        sous_type = port["accepted"].split(".", 1)[-1]
        if sous_type == "Magazine":     # propre à l'arme, jamais un modificateur
            continue
        exiges = set((port["required_tags"] or "").split())
        for r in con.execute(
                "SELECT i.name, i.size, i.tags, s.damage_multiplier d, "
                "       s.sound_multiplier b FROM items i "
                "JOIN item_stats s ON s.item_uuid = i.uuid "
                "WHERE i.type = 'WeaponAttachment' AND i.subtype = ? "
                "  AND i.size BETWEEN ? AND ? AND i.name IS NOT NULL "
                "  AND s.damage_multiplier IS NOT NULL",
                (sous_type, port["min_size"], port["max_size"])):
            if not exiges <= set((r["tags"] or "").split()):
                continue
            trouves.setdefault(r["name"], {
                "nom": r["name"], "famille": sous_type, "taille": r["size"],
                "mult": r["d"], "bruit": r["b"],
                "silencieux": (r["b"] is not None and r["b"] < 1.0)})
    return sorted(trouves.values(), key=lambda a: -a["mult"])


def _qualite_requise(effets: tuple[Effet, ...], borne: float,
                     seuil: float) -> float | None:
    """La plus petite qualité dont le multiplicateur de dégâts atteint `seuil`.

    On balaie l'échelle au point près plutôt que d'inverser la formule : les
    effets ont chacun leurs propres bornes et leurs écarts au barème
    s'additionnent comme sur le terminal observé. Un point sur 1 000 est très
    en dessous de la précision utile — un joueur achète « du 950 », pas
    « du 947,3 ».
    """
    chiffrables = [e for e in effets if e.chiffrable]
    if not chiffrables:
        return None
    for pas in range(0, int(borne) + 1):
        compose = composer_multiplicateurs(
            e.multiplicateur(float(pas)) for e in chiffrables)
        if compose >= seuil - 1e-9:
            return float(pas)
    return None


def _paliers_de_balles(effets: tuple[Effet, ...], borne: float,
                       par_tir: float, pv: float,
                       plafond: int = 30) -> list[dict]:
    """Combien de balles il faut, et où le compte descend d'une unité.

    Demande de l'utilisateur, 2026-08-10 : « à 250 il en faut 8, à partir de
    550 sept, et à 900 six ». La donnée le permet exactement — le
    multiplicateur de qualité s'interpole, le compte est le plafond entier de
    `PV ÷ dégâts par balle`, et un palier est le point où ce plafond change.
    On rend donc **les frontières**, pas une table de mille lignes : c'est ce
    qu'un joueur retient et ce qu'il peut viser en fabriquant.

    Le compte est plafonné : au-delà d'une trentaine de balles, la question
    n'est plus « combien » mais « avec quelle autre arme ».
    """
    if par_tir <= 0:
        return []
    paliers: list[dict] = []
    precedent = None
    for pas in range(0, int(borne) + 1):
        degats = par_tir * _mult_a(effets, float(pas))
        if degats <= 0:
            continue
        balles = math.ceil(pv / degats - 1e-9)
        if balles > plafond:
            continue
        if balles != precedent:
            paliers.append({"qualite": float(pas), "balles": balles,
                            "degats": degats})
            precedent = balles
    return paliers


def _plancher_de_balles(effets: tuple[Effet, ...], borne: float,
                        par_tir: float, pv: float,
                        accessoires: list[dict]) -> dict:
    """Le minimum de balles atteignable, **tout compris**, et ce qu'il coûte.

    Demande de l'utilisateur, 2026-08-11 : « tu peux aussi dire qu'avec une
    certaine configuration d'équipement on peut descendre à 8 balles, ou bien
    qu'on ne peut pas descendre à 8 peu importe la qualité et l'équipement ».
    C'est la seule réponse qui ferme la question : sans elle, un joueur peut
    croire qu'il lui manque un accessoire qu'il n'a pas trouvé.

    Le plancher se prend à la **qualité maximale** et avec le meilleur
    accessoire de dégâts montable — les deux leviers publiés, et rien d'autre.
    """
    if par_tir <= 0:
        return {}
    mult_qualite = _mult_a(effets, borne)
    meilleur = max(accessoires, key=lambda a: a["mult"], default=None)
    gain = meilleur["mult"] if meilleur and meilleur["mult"] > 1 else 1.0

    def compte(mult: float) -> int:
        return math.ceil(pv / (par_tir * mult) - 1e-9)

    au_bareme = compte(1.0)
    sans_accessoire = compte(mult_qualite)
    avec = compte(mult_qualite * gain)
    return {
        "balles": avec,
        "sans_accessoire": sans_accessoire,
        "au_bareme": au_bareme,
        "qualite": borne,
        # L'accessoire n'est cité que s'il **change** le compte : le nommer
        # sans gain ferait courir après un objet pour rien.
        "accessoire": meilleur if avec < sans_accessoire else None,
        "degats": par_tir * mult_qualite * gain,
    }


def tir_a_multiplicateur(con: sqlite3.Connection, item_uuid: str,
                        mult_degats: float, *, zone: str = "tete",
                        classe: str = "lourde") -> dict:
    """Combien de balles pour tuer, à un multiplicateur de dégâts **donné**.

    `qualite_pour_tuer` part d'une qualité unique et cherche le seuil ; le
    terminal de fabrication pose l'inverse — le montage est choisi, chaque
    emplacement a sa qualité, et la question est « est-ce que ça passe en une
    balle ? ». Les deux consomment le même calcul mesuré : dégâts par type
    contre résistances de la pièce touchée, multiplicateur de zone, points de
    vie du joueur.

    Le compte de départ (`balles_de_base`) est celui du barème du catalogue,
    à multiplicateur 1 : c'est lui qui donne son sens à « une balle de
    moins ».
    """
    stats = _row(con, "SELECT alpha, pellets_per_shot, rounds_per_minute, "
                      "       alpha_physical, alpha_energy, alpha_thermal, "
                      "       alpha_biochemical, alpha_distortion, alpha_stun "
                      "FROM item_stats WHERE item_uuid = ?", item_uuid)
    if not stats or not stats["alpha"]:
        raise NotFound(
            item_uuid,
            explication="Ce calcul se fait sur les **dégâts par tir**, et cet "
                        "objet n'en a pas : il ne tire pas.")
    zone = zone if zone in MULT_ZONE else "torse"
    classe = _CLASSE_ENTREE.get(str(classe).lower(), "lourde")
    armure = _resistance(con, zone, classe)
    if armure is None:
        raise NotFound(f"je n'ai pas la résistance d'une armure {classe}")

    plombs = stats["pellets_per_shot"] or 1
    par_balle, detail, ecartes = _degats_utiles(stats, armure["resistances"])
    if not par_balle:
        raise NotFound("cette arme ne fait aucun dégât qui retire des "
                       "points de vie")
    utiles = par_balle * plombs * MULT_ZONE[zone]

    def balles(mult: float) -> int:
        return math.ceil(PV_JOUEUR / (utiles * mult))

    compte = balles(mult_degats)
    depart = balles(1.0)
    return {
        "zone": zone, "classe": classe,
        "multiplicateur": mult_degats,
        "degats_par_balle": utiles * mult_degats,
        "degats_de_base": utiles,
        "balles": compte,
        "balles_de_base": depart,
        # « Le nombre de balles réduites » : ce que le montage fait gagner.
        "balles_gagnees": depart - compte,
        "os": compte == 1,
        "os_de_base": depart == 1,
        "detail_degats": detail,
        "degats_ecartes": ecartes,
        # Combien de pièces publient cette résistance, et celle qui protège
        # mieux : le verdict change d'un casque à l'autre, et le taire ferait
        # passer un « oui » pour une garantie.
        "resistance": armure["resistance"],
        "pieces": armure["nombre"],
        "exception": armure["exception"],
    }


def _mult_a(effets: tuple[Effet, ...], qualite: float) -> float:
    return composer_multiplicateurs(
        e.multiplicateur(qualite) for e in effets if e.chiffrable)


def qualite_pour_tuer(con: sqlite3.Connection, query: str, *,
                      zone: str = "tete", classe: str = "lourde",
                      accessoire: str | None = None, volet: str | None = None,
                      question: str = "") -> dict:
    """« À partir de quelle qualité de P6-LR je tue d'une balle dans la tête
    quelqu'un en armure lourde ? »

    Quatre facteurs se multiplient et un seul est réglable : l'alpha publié de
    l'arme, le multiplicateur de qualité (0,9 à 1,1 sur les dégâts), celui de
    l'accessoire monté, puis la résistance de la pièce touchée et le
    multiplicateur de zone. On rend la **qualité minimale** qui fait passer le
    produit au-dessus des points de vie, sans accessoire et avec chacun de ceux
    qui changent la donne.

    Le cas « impossible » est une réponse, pas un échec : le P6-LR nu atteint
    la tête en casque lourd à 778, mais le Tacit (−8 %) plafonne à 99,36
    dégâts utiles et n'y arrive à aucune qualité. Répondre 1000 serait faux ;
    répondre « je ne sais pas » le serait tout autant.
    """
    zone_supposee = zone not in MULT_ZONE
    zone = zone if zone in MULT_ZONE else "torse"
    # Le préparateur transmet ce que `armure.detect_classe` a lu, c'est-à-dire
    # le mot du jeu (« Heavy ») ; l'API du module parle français. On accepte
    # les deux plutôt que d'obliger chaque appelant à traduire.
    classe = _CLASSE_ENTREE.get(str(classe).lower(), "lourde")

    # `exiger=False` : la question porte sur des dégâts, pas sur une recette.
    uuid, nom, item_uuid, variantes = _blueprint(con, query, question,
                                                 exiger=False)
    if item_uuid is None:
        raise NotFound(f"je n'ai pas les caractéristiques de {nom}")
    stats = _row(con, "SELECT alpha, pellets_per_shot, rounds_per_minute, "
                      "       alpha_physical, alpha_energy, alpha_thermal, "
                      "       alpha_biochemical, alpha_distortion, alpha_stun "
                      "FROM item_stats WHERE item_uuid = ?", item_uuid)
    alpha = stats["alpha"] if stats else None
    if not alpha:
        # **Une absence expliquée est une réponse.** Le balayage du
        # 2026-08-15 signalait ces outils « muets » sur 11 entités sur 12 —
        # mais son échantillon tire dans tout le catalogue, où l'immense
        # majorité des objets ne sont pas des armes. Des bottes n'ont pas
        # de dégâts par tir : le dire vaut mieux que « je n'ai pas trouvé
        # la donnée », qui laisse croire à une lacune du bot.
        raise NotFound(
            nom,
            explication=(
                f"Cette question se calcule sur les **dégâts par tir**, et "
                f"**{nom}** n'en a pas — ce n'est pas une arme. Elle ne "
                f"vaut que pour ce qui tire."))
    # Un fusil à pompe publie ses dégâts **par plomb** : une balle, c'est la
    # gerbe entière. L'ignorer ferait rater le seul cas où l'arme tue d'un tir
    # sans qu'on change quoi que ce soit.
    plombs = stats["pellets_per_shot"] or 1

    effets = (tuple(e for e in _effets(con, uuid) if e.cle == "weapon_damage")
              if uuid else ())
    bornes = [e.qualite_max for e in effets if e.qualite_max]
    borne = max(bornes) if bornes else QUALITE_MAX

    armure = _resistance(con, zone, classe)
    if armure is None:
        raise NotFound(f"je n'ai pas la résistance d'une armure {classe}")

    mult_zone = MULT_ZONE[zone]
    # **Les dégâts passent type par type.** Un seul nombre — l'alpha total —
    # opposé à la seule résistance physique donnait 148,5 dégâts utiles à
    # l'Atzkav là où il en fait 108 : ses 35 de distorsion et 10 de stun ne
    # retirent aucun point de vie, et ses 120 d'énergie répondent à
    # `armor_energy`. `_degats_utiles` rend le total létal, son détail, et ce
    # qui a été écarté — la différence doit se lire dans la réponse.
    par_balle, detail_degats, degats_ecartes = _degats_utiles(
        stats, armure["resistances"])
    if not par_balle:
        raise NotFound(f"{nom} ne fait aucun dégât qui retire des points de vie")
    par_balle *= plombs
    # Le seuil que le couple (qualité, accessoire) doit atteindre pour passer
    # les points de vie. Tout le reste du calcul en découle.
    seuil = PV_JOUEUR / (par_balle * mult_zone)

    accessoires = _accessoires_de_degats(con, item_uuid)
    demande = None
    if accessoire:
        cible = normalize(accessoire)
        demande = next((a for a in accessoires
                        if cible and cible in normalize(a["nom"])), None)

    def scenario(acc: dict | None) -> dict:
        mult_acc = acc["mult"] if acc else 1.0
        return {"accessoire": acc,
                "qualite": _qualite_requise(effets, borne, seuil / mult_acc),
                "borne": borne,
                "degats_au_max": par_balle * _mult_a(effets, borne)
                * mult_acc * mult_zone,
                "degats_a_1": par_balle * mult_acc * mult_zone}

    # Ce qui **change le verdict** d'abord : le meilleur gain de dégâts, le
    # silencieux qui coûte le moins de dégâts, et le plus silencieux quand ce
    # n'est pas le même — mesuré sur le P6-LR, le Quell ne coûte rien et divise
    # le bruit par 1,5, le Tacit le divise par 2,5 pour 8 % de dégâts. Lister
    # les seize accessoires montables noierait la réponse.
    meilleur = accessoires[0] if accessoires and accessoires[0]["mult"] > 1 else None
    silencieux = [a for a in accessoires if a["silencieux"]]
    plus_discret = min(silencieux, key=lambda a: a["bruit"]) if silencieux else None

    scenarios = [("sans accessoire", scenario(None))]
    deja = set()
    for acc in (demande, meilleur, silencieux[0] if silencieux else None,
                plus_discret):
        if acc and acc["nom"] not in deja:
            deja.add(acc["nom"])
            scenarios.append((f"avec {acc['nom']}", scenario(acc)))

    # Sans casque, la tête encaisse six fois et l'armure ne protège plus :
    # c'est le cas où presque tout tue d'une balle, et il vaut d'être dit.
    sans_casque = None
    if zone == "tete":
        # Sans casque il n'y a plus de résistance du tout : on repart des
        # dégâts bruts, pas de ceux qu'une armure vient d'absorber.
        brut = sum(stats[c] or 0.0 for c, _, _ in _TYPES_LETAUX) * plombs             * MULT_TETE_SANS_CASQUE
        sans_casque = {
            "qualite": _qualite_requise(effets, borne, PV_JOUEUR / brut),
            "degats": brut, "toujours": brut >= PV_JOUEUR}

    # **L'exception se recalcule, elle ne se commente pas.** Le premier rendu
    # concluait « contre eux, aucune qualité ne suffit » sans rien mesurer : le
    # BR-2, avec ses huit plombs, traverse très bien un BUL-H4. Une phrase
    # d'ambiance à la place d'un calcul est exactement ce que le §7 interdit.
    if armure["exception"]:
        e = armure["exception"]
        gain = max((a["mult"] for a in accessoires), default=1.0)
        contre, _, _ = _degats_utiles(stats, e["resistances"])
        contre *= plombs * mult_zone
        e["qualite"] = _qualite_requise(effets, borne, PV_JOUEUR / contre)
        e["qualite_avec_accessoire"] = _qualite_requise(
            effets, borne, PV_JOUEUR / (contre * gain))
        e["degats_au_max"] = contre * _mult_a(effets, borne) * gain

    # Aucun scénario ne passe : on nomme ce qui y arriverait plutôt que de
    # laisser le joueur sur un « non ». C'est la règle des contraintes —
    # une liste vide est inutilisable, il veut savoir ce qui débloque.
    alternatives = []
    if all(s["qualite"] is None for _, s in scenarios):
        alternatives = _armes_qui_suffisent(
            con, armure["resistances"], mult_zone, PV_JOUEUR)

    return {"nom": nom, "item_uuid": item_uuid, "alpha": alpha, "plombs": plombs,
            "cadence": stats["rounds_per_minute"] if stats else None,
            "alternatives": alternatives,
            # Le détail des dégâts, et ce qui ne tue pas : sans lui, un lecteur
            # qui a vu « 165 » sur une fiche croit à une erreur de calcul.
            "degats_par_balle": par_balle, "detail_degats": detail_degats,
            "degats_ecartes": degats_ecartes,
            # « Combien de balles à quelle qualité » : les paliers où le compte
            # descend d'une balle — demande de l'utilisateur du 2026-08-10.
            "paliers": _paliers_de_balles(effets, borne, par_balle * mult_zone,
                                          PV_JOUEUR),
            # Le plancher tout compris : ce qu'on ne peut pas battre, et ce
            # qu'il faut pour l'atteindre. C'est ce qui ferme la question —
            # sans lui, on cherche un accessoire qui n'existe pas.
            "plancher": _plancher_de_balles(effets, borne,
                                            par_balle * mult_zone, PV_JOUEUR,
                                            accessoires),
            # « combien de balles » demande les paliers meme quand l'arme tue
            # d'un tir : le compte est alors 1, et le dire est la reponse.
            "volet": volet,
            "zone": zone, "zone_libelle": _LIBELLE_ZONE[zone], "classe": classe,
            "classe_libelle": _LIBELLE_CLASSE[classe],
            # Le rendu doit **dire** ce qu'il a supposé : sans zone nommée, le
            # torse est un choix, pas une lecture de la question.
            "zone_supposee": zone_supposee,
            "mult_zone": mult_zone, "armure": armure, "pv": PV_JOUEUR,
            # **Le P6-LR tranche la composition du terminal.** Son canon à
            # +7,48 % et ses pièces de précision à +5,24 % donnent +12,72 % :
            # les écarts au barème s'additionnent. Le rendu doit encore
            # annoncer que l'application aux autres schémas est extrapolée ;
            # sans cette phrase, le seuil paraîtrait publié.
            "composants": tuple(e.composant or e.nom for e in effets if e.chiffrable),
            "non_chiffrables": tuple(e.nom for e in effets if not e.chiffrable),
            "seuil": seuil, "scenarios": scenarios, "sans_casque": sans_casque,
            "accessoires": accessoires, "accessoire_demande": demande,
            "accessoire_introuvable": (accessoire if accessoire and not demande
                                       else None),
            "variantes": variantes, "resolution": None}


def _dernier_gain(effets: tuple[Effet, ...], borne: float, par_tir: float,
                  pv: float) -> dict | None:
    """La dernière qualité à laquelle le compte de balles descend encore.

    Au-delà, chaque point payé l'est pour rien : le multiplicateur monte
    toujours, mais `ceil(pv / dégâts)` ne change plus. C'est toute la
    question — un joueur achète du minerai, pas des décimales.

    Le plafond de `_paliers_de_balles` est volontairement large ici : il
    sert à ne pas répondre « 214 balles » sur une question de mise à mort,
    pas à borner un seuil de fabrication. Vérifié le 2026-08-13 en le
    portant de 40 à 200 — les neuf scénarios restaient retenus et aucun
    seuil ne bougeait, donc il ne fausse pas le calcul.
    """
    paliers = _paliers_de_balles(effets, borne, par_tir, pv, plafond=200)
    if not paliers:
        return None
    return dict(paliers[-1])


def qualite_maximale_utile(con: sqlite3.Connection, query: str, *,
                           accessoire: str | None = None,
                           question: str = "") -> dict:
    """« Jusqu'à quelle qualité ça vaut le coup ? »

    Demande fondatrice du projet (l'utilisateur, 2026-08-13 : « c'est
    littéralement pour ça que j'ai initié ce projet »). Fabriquer en 1000
    coûte bien plus cher qu'en 780, et **ce qui compte se compte en
    balles** : un nombre entier ne change qu'à des frontières précises. Il
    existe donc une qualité au-delà de laquelle plus aucun scénario
    d'impact ne s'améliore.

    **Dix scénarios, pas vingt-sept.** Le joueur mixe les pièces — casque
    lourd, torse moyen, jambes légères — mais *un impact touche une seule
    pièce*. Les cas sont donc les neuf couples (zone × classe) plus la
    tête nue, et le rendu le **dit** : sans cette phrase, un lecteur croit
    qu'on a testé les tenues complètes.

    **L'accessoire déplace le seuil dans les deux sens**, et c'est mesuré :
    sur 431 accessoires publiant un `DamageMultiplier`, 5 en donnent
    (×1,05 à ×1,175) et 7 en coûtent (×0,90 à ×0,95). Les silencieux Tacit
    coûtent 8 %, le Stoic n'en coûte aucun — la règle « un silencieux
    coûte des dégâts » est fausse, la donnée tranche pièce par pièce.

    **Le seuil n'est pas monotone, et ce n'est pas un défaut.** Le P6-LR
    monte de 771 à 995 avec un Tacit, comme l'intuition le veut. Le P4-AR
    *descend* de 956 à 823, parce qu'un palier est une frontière entière :
    son dernier gain (14 → 13 balles) demanderait plus de 1 000 une fois
    amputé de 8 %, donc il sort de l'échelle, et le dernier gain
    atteignable devient 19 → 18. Le seuil baisse parce que le gain suivant
    est devenu **hors de portée** — le rendu doit le dire, sinon le chiffre
    passe pour une erreur.
    """
    uuid, nom, item_uuid, variantes = _blueprint(con, query, question,
                                                 exiger=False)
    if item_uuid is None:
        raise NotFound(f"je n'ai pas les caractéristiques de {nom}")
    stats = _row(con, "SELECT alpha, pellets_per_shot, "
                      "       alpha_physical, alpha_energy, alpha_thermal, "
                      "       alpha_biochemical, alpha_distortion, alpha_stun "
                      "FROM item_stats WHERE item_uuid = ?", item_uuid)
    if not stats or not stats["alpha"]:
        # **Une absence expliquée est une réponse.** Le balayage du
        # 2026-08-15 signalait ces outils « muets » sur 11 entités sur 12 —
        # mais son échantillon tire dans tout le catalogue, où l'immense
        # majorité des objets ne sont pas des armes. Des bottes n'ont pas
        # de dégâts par tir : le dire vaut mieux que « je n'ai pas trouvé
        # la donnée », qui laisse croire à une lacune du bot.
        raise NotFound(
            nom,
            explication=(
                f"Cette question se calcule sur les **dégâts par tir**, et "
                f"**{nom}** n'en a pas — ce n'est pas une arme. Elle ne "
                f"vaut que pour ce qui tire."))
    plombs = stats["pellets_per_shot"] or 1

    effets = (tuple(e for e in _effets(con, uuid) if e.cle == "weapon_damage")
              if uuid else ())
    if not [e for e in effets if e.chiffrable]:
        raise NotFound(
            f"le blueprint de {nom} ne publie aucun modificateur de dégâts "
            "chiffrable : la qualité ne change pas ses dégâts")
    bornes = [e.qualite_max for e in effets if e.qualite_max]
    borne = max(bornes) if bornes else QUALITE_MAX

    # Les bases de chaque scénario, calculées une fois : la suite ne fait
    # que les multiplier par un facteur d'accessoire.
    bases: list[tuple[str, str, float]] = []
    for classe in ("legere", "moyenne", "lourde"):
        for zone in ("tete", "torse", "jambes"):
            armure = _resistance(con, zone, classe)
            if armure is None:
                continue
            par_balle, _, _ = _degats_utiles(stats, armure["resistances"])
            if not par_balle:
                continue
            bases.append((zone, classe, par_balle * plombs * MULT_ZONE[zone]))
    brut = sum(stats[colonne] or 0.0 for colonne, _, _ in _TYPES_LETAUX) \
        * plombs * MULT_TETE_SANS_CASQUE

    #: Le meilleur palier **par zone**, et non le seul maximum global.
    #:
    #: Le calcul les rencontrait tous et n'en gardait qu'un : la réponse
    #: listait donc quatre paliers, tous « sur la tête », sans jamais dire
    #: ce qu'il en est ailleurs. Le joueur en a tiré la conclusion
    #: raisonnable et fausse — « ça veut dire qu'aucune qualité ne baisse
    #: le nombre de balles pour tuer dans le torse ? » (journal du
    #: 2026-08-20). Mesuré : le torse a bien ses paliers, simplement plus
    #: bas que ceux de la tête, donc invisibles derrière le maximum.
    #:
    #: Une réponse doit dire **sur quoi elle classe**. C'est la règle déjà
    #: payée par « la meilleure méthode de raffinage » : le premier d'un
    #: cumul n'est le premier d'aucun axe pris seul.
    par_zone: dict[str, dict] = {}

    def seuil_pour(facteur: float) -> dict | None:
        """Le plus haut « dernier gain » sur tous les scénarios."""
        meilleur = None
        for zone, classe, base in bases:
            gain = _dernier_gain(effets, borne, base * facteur, PV_JOUEUR)
            if gain is None:
                continue
            vu = par_zone.get(zone)
            if vu is None or gain["qualite"] > vu["qualite"]:
                par_zone[zone] = {**gain, "zone": zone, "classe": classe}
            if meilleur is None or gain["qualite"] > meilleur["qualite"]:
                meilleur = {**gain, "zone": zone, "classe": classe,
                            "sans_casque": False}
        if brut > 0:
            nu = _dernier_gain(effets, borne, brut * facteur, PV_JOUEUR)
            if nu and (meilleur is None or nu["qualite"] > meilleur["qualite"]):
                meilleur = {**nu, "zone": "tete", "classe": None,
                            "sans_casque": True}
        return meilleur

    nu = seuil_pour(1.0)

    # Les accessoires qui **changent** quelque chose, dans les deux sens.
    # Ceux qui valent exactement 1 sont 419 sur 431 : les lister noierait
    # les deux seuls qui comptent.
    variantes_acc: list[dict] = []
    montables = _accessoires_de_degats(con, item_uuid)
    demande = None
    for candidat in sorted(montables, key=lambda a: a["mult"]):
        if abs(candidat["mult"] - 1.0) < 1e-9:
            continue
        seuil = seuil_pour(candidat["mult"])
        if seuil is None:
            continue
        entree = {"nom": candidat["nom"], "mult": candidat["mult"],
                  "silencieux": candidat.get("silencieux", False),
                  "seuil": seuil["qualite"], "balles": seuil["balles"],
                  "zone": seuil["zone"], "classe": seuil["classe"],
                  "sans_casque": seuil["sans_casque"]}
        variantes_acc.append(entree)
        if accessoire and accessoire.lower() in candidat["nom"].lower():
            demande = entree

    # **Où l'on tue d'une balle, et de combien ça rate ailleurs.**
    # Remarque de l'utilisateur, 2026-08-13 : « je pensais que pour le
    # P6-LR avec une très haute qualité on pouvait OS dans le torse
    # léger ? » Il avait raison — mais seulement **avec un accessoire**,
    # et ça se joue à 3,2 points : 96,8 dégâts à qualité 1000 contre 100
    # PV, et 101,6 avec un Torrent. Le seuil global le savait déjà (956
    # avec Torrent, exactement ce basculement) mais ne le **disait** pas.
    # C'est la règle du projet : un « jamais » se chiffre, parce que « il
    # te manque 3 %, un accessoire suffit » et « il t'en manque 88 % » ne
    # se jouent pas du tout de la même façon.
    meilleur_acc = max(montables, key=lambda a: a["mult"], default=None)
    if meilleur_acc and meilleur_acc["mult"] <= 1.0:
        meilleur_acc = None
    mult_max = _mult_a(effets, borne)
    carte_os: list[dict] = []
    for zone, classe, base in bases:
        seuil_nu = _qualite_requise(effets, borne, PV_JOUEUR / base)
        avec = (_qualite_requise(effets, borne,
                                 PV_JOUEUR / (base * meilleur_acc["mult"]))
                if meilleur_acc else None)
        atteint = base * mult_max
        carte_os.append({
            "zone": zone, "classe": classe,
            "qualite": seuil_nu, "qualite_avec": avec,
            "accessoire": meilleur_acc["nom"] if meilleur_acc else None,
            # Le déficit se dit en pourcentage des points de vie : c'est
            # l'unité dans laquelle un joueur juge « c'est jouable ».
            "manque_pct": (round((PV_JOUEUR - atteint) / PV_JOUEUR * 100, 1)
                           if seuil_nu is None and avec is None else None),
        })

    # **Le plafond absolu, toutes configurations confondues.**
    # Remarque de l'utilisateur, 2026-08-13 : « donc la limite utile de
    # qualité d'un P6-LR est 956 ». Il a raison de challenger — l'outil
    # annonçait 771, qui n'est vrai que pour l'**arme nue**. Un Torrent
    # débloque l'OS torse léger à 956, et un silencieux Tacit repousse
    # encore à 995 : un joueur qui monte l'un ou l'autre gagne encore
    # au-dessus de 771, et on lui aurait dit de ne pas payer.
    #
    # Le titre porte donc le maximum atteignable, et le détail dit à
    # quelle configuration chaque seuil appartient. Annoncer le seuil nu
    # comme *la* limite était le genre de vérité partielle qui se lit
    # comme un conseil complet.
    seuil_nu = nu["qualite"] if nu else None
    plafond, config = seuil_nu, "à nu"
    for entree in variantes_acc:
        if plafond is None or entree["seuil"] > plafond:
            plafond, config = entree["seuil"], f"avec un {entree['nom']}"

    return {
        "nom": nom, "borne": borne, "variantes": variantes,
        "os": carte_os,
        "plafond": plafond, "plafond_config": config,
        "seuil": nu["qualite"] if nu else None,
        "balles": nu["balles"] if nu else None,
        "zone": nu["zone"] if nu else None,
        "classe": nu["classe"] if nu else None,
        "sans_casque": nu["sans_casque"] if nu else False,
        "scenarios": len(bases) + (1 if brut > 0 else 0),
        # Le plafond de chaque zone, à nu — pour que « au-delà de 889 plus
        # rien ne bouge » ne se lise pas comme « le torse ne bouge jamais ».
        "par_zone": [par_zone[z] for z in ("tete", "torse", "jambes")
                     if z in par_zone],
        "accessoires": variantes_acc,
        "accessoire_demande": demande,
        "accessoire_introuvable": (accessoire if accessoire and not demande
                                   else None),
        "resolution": None,
    }


def jalons_de_qualite(con: sqlite3.Connection, query: str, *,
                      question: str = "") -> dict:
    """« Les jalons de qualité du P6-LR » — demande de l'utilisateur du
    2026-08-12 : à partir de quelle qualité l'arme tue d'une balle, pour
    **chaque** zone et chaque classe d'armure, avec et sans accessoire de
    dégâts.

    C'est `qualite_pour_tuer` sans cible : la même mécanique — alpha typé ×
    qualité × accessoire × résistance de la pièce × zone contre les points
    de vie — balayée sur les neuf couples (zone, classe). Un « jamais » se
    chiffre : les dégâts au maximum disent de combien ça rate.
    """
    uuid, nom, item_uuid, variantes = _blueprint(con, query, question,
                                                 exiger=False)
    if item_uuid is None:
        raise NotFound(f"je n'ai pas les caractéristiques de {nom}")
    stats = _row(con, "SELECT alpha, pellets_per_shot, "
                      "       alpha_physical, alpha_energy, alpha_thermal, "
                      "       alpha_biochemical, alpha_distortion, alpha_stun "
                      "FROM item_stats WHERE item_uuid = ?", item_uuid)
    if not stats or not stats["alpha"]:
        # **Une absence expliquée est une réponse.** Le balayage du
        # 2026-08-15 signalait ces outils « muets » sur 11 entités sur 12 —
        # mais son échantillon tire dans tout le catalogue, où l'immense
        # majorité des objets ne sont pas des armes. Des bottes n'ont pas
        # de dégâts par tir : le dire vaut mieux que « je n'ai pas trouvé
        # la donnée », qui laisse croire à une lacune du bot.
        raise NotFound(
            nom,
            explication=(
                f"Cette question se calcule sur les **dégâts par tir**, et "
                f"**{nom}** n'en a pas — ce n'est pas une arme. Elle ne "
                f"vaut que pour ce qui tire."))
    plombs = stats["pellets_per_shot"] or 1

    effets = (tuple(e for e in _effets(con, uuid) if e.cle == "weapon_damage")
              if uuid else ())
    bornes = [e.qualite_max for e in effets if e.qualite_max]
    borne = max(bornes) if bornes else QUALITE_MAX

    accessoires = _accessoires_de_degats(con, item_uuid)
    meilleur = (accessoires[0] if accessoires
                and accessoires[0]["mult"] > 1 else None)

    jalons: list[dict] = []
    for classe in ("legere", "moyenne", "lourde"):
        for zone in ("tete", "torse", "jambes"):
            armure = _resistance(con, zone, classe)
            if armure is None:
                continue
            par_balle, _, _ = _degats_utiles(stats, armure["resistances"])
            if not par_balle:
                continue
            par_balle *= plombs
            seuil = PV_JOUEUR / (par_balle * MULT_ZONE[zone])
            sans = _qualite_requise(effets, borne, seuil)
            avec = (_qualite_requise(effets, borne, seuil / meilleur["mult"])
                    if meilleur else None)
            base = par_balle * MULT_ZONE[zone]
            jalons.append({
                "zone": zone, "classe": classe,
                "qualite": sans,
                "qualite_avec": avec,
                "accessoire": meilleur["nom"] if meilleur else None,
                # De combien ça rate quand même tout au maximum — un
                # « jamais » se chiffre.
                "degats_au_max": par_balle * _mult_a(effets, borne)
                * (meilleur["mult"] if meilleur else 1.0) * MULT_ZONE[zone],
                # **Quand l'OS n'existe pas, la réponse est le compte de
                # balles** — et la qualité qui le réduit (demande de
                # l'utilisateur, 2026-08-13). Deux bases : sans accessoire,
                # et avec le meilleur, pour que la parenthèse du rendu
                # puisse dire ce que l'accessoire change.
                "balles": _paliers_de_balles(effets, borne, base, PV_JOUEUR),
                "balles_avec": (_paliers_de_balles(
                    effets, borne, base * meilleur["mult"], PV_JOUEUR)
                    if meilleur else []),
            })

    # La cible que tout joueur vise : la tête sans casque — ×6, aucune
    # résistance. Le calcul est celui de `qualite_pour_tuer`, transposé.
    brut = sum(stats[colonne] or 0.0
               for colonne, _, _ in _TYPES_LETAUX) * plombs \
        * MULT_TETE_SANS_CASQUE
    sans_casque = None
    if brut > 0:
        sans_casque = {
            "qualite": _qualite_requise(effets, borne, PV_JOUEUR / brut),
            "degats_au_max": brut * _mult_a(effets, borne),
            "balles": _paliers_de_balles(effets, borne, brut, PV_JOUEUR),
        }

    return {"nom": nom, "jalons": jalons, "borne": borne,
            "accessoire": meilleur, "sans_casque": sans_casque,
            "variantes": variantes, "resolution": None}
