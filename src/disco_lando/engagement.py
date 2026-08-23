"""Le modèle d'engagement — qui touche, à quelle distance, et qui décroche.

Écrit le 2026-08-13 après la séquence Arrow / Aurora Mk II du journal, où
le duel donnait l'Aurora gagnante « en 18,6 s contre 44,1 s » alors qu'il
n'existe, mot de l'utilisateur, « aucun monde où l'Aurora gagne ».

Trois défauts mesurés avaient produit ce verdict, et ce module répond aux
trois :

1. **l'agilité ne pouvait pas renverser un duel.** `combat._facteur_tir`
   borne son effet à ×0,55–1,45 et multiplie un **temps final** ; face à un
   rapport de PV de 2,15× le verdict était acquis d'avance. Ici l'agilité
   produit un **taux de touche** qui multiplie le DPS *avant* les verrous —
   ce qui la rend capable de faire passer un attaquant sous la régénération
   du bouclier adverse, c'est-à-dire de rendre la victoire **impossible** et
   non seulement lente ;
2. **l'évasion se lisait sur `accel_maneuver`**, qui vaut dans scunpacked la
   somme des poussées de *tous* les thrusters de manœuvre divisée par la
   masse — un total scalaire, toutes directions confondues. L'Aurora Mk II,
   avec 10 tuyères pour 13,3 MN contre 8 pour 3,55 MN à l'Arrow, sortait
   « plus évasive » **parce qu'elle a deux tuyères de plus**. Ici la manœuvre
   est ramenée à une accélération réellement disponible dans *une* direction ;
3. **rien ne modélisait la distance**, alors qu'elle décide de tout.

Le modèle est **géométrique**, et c'est délibéré : un temps de vol, un
déplacement, un rayon apparent et une vitesse angulaire sont des grandeurs
physiques tirées de colonnes publiées. Il reste maison — la géométrie des
tuyères, les arcs de tir et le talent ne sont dans aucun fichier — mais il
invente beaucoup moins qu'un jeu de poids normalisés, et chacune de ses
hypothèses se nomme dans la réponse.

Les deux règles de terrain viennent de l'utilisateur (2026-08-13) :

- **le duel se joue entre 200 et 1 500 m**, et la distance est un *choix* :
  « 1 500 m maintenu pour régénérer ses boucliers sans être touché, et des
  distances beaucoup plus proches pour tirer sans rater même si l'adversaire
  esquive » ;
- **la fuite est une issue** : « un Arrow ne pourra pas forcément se faire
  détruire par un Hammerhead, il va pouvoir s'enfuir. »
"""

from __future__ import annotations

import math
import random
from typing import Any

# --------------------------------------------------------------- les bornes

#: En deçà de 200 m on se percute (règle de terrain, 2026-08-13).
DISTANCE_MIN = 200.0

#: La plus grande distance à laquelle on cherche encore à se battre.
#:
#: **Elle valait 1 500 m, et c'était faux.** L'utilisateur a dû le dire deux
#: fois — « je t'ai déjà dit d'arrêter avec cette notion de 1 500 m, tu peux
#: la supprimer totalement : des combats peuvent se dérouler à 6 km d'écart
#: si ça permet de gagner. Certains combats durent 10 min car l'Arrow
#: harcèle de loin un Corsair et finit par le détruire, car il ne peut pas
#: le toucher alors que l'Arrow si » (2026-08-16).
#:
#: Le harcèlement à longue distance n'est donc pas du grignotage à écarter,
#: c'est **une tactique gagnante** — et le modèle la rendait impossible en
#: bornant la recherche à 1 500 m. Les armes le permettent : une gatling
#: publie jusqu'à 2 799 m de portée efficace, et `_facteur_portee` décroît
#: en carré inverse au-delà plutôt que de couper net.
DISTANCE_MAX = 6000.0

#: La distance à laquelle un duel **commence**, elle, n'a pas changé : on
#: s'engage entre 200 et 1 500 m (« on est plus sur du 200 m - 1 500 m »,
#: 2026-08-13). Ouvrir le tirage jusqu'à 6 km ferait démarrer la moitié des
#: duels hors de portée, ce que personne n'a décrit. C'est la recherche de
#: la meilleure distance qui va loin, pas la rencontre.
DISTANCE_ENGAGEMENT = 1500.0

#: Au-delà, l'engagement est rompu : plus personne ne porte utilement. Le
#: seuil valait 3 000 m quand le combat s'arrêtait à 1 500 ; il doit rester
#: franchement au-dessus de `DISTANCE_MAX`, sinon chercher la distance qui
#: fait gagner reviendrait à quitter le combat.
DISTANCE_ROMPUE = 10000.0

#: Pas de la simulation. À 0,1 s, un projectile à 1 500 m/s parcourt 150 m —
#: assez fin pour que la distance et les fenêtres de tir évoluent proprement.
PAS = 0.1

#: Un combat qui dure plus longtemps n'en est plus un : les deux camps ont eu
#: mille occasions de rompre. Sert de garde contre les boucles infinies quand
#: aucun des deux ne perce et qu'aucun ne peut décrocher.
#:
#: **Porté de 600 à 900 s le 2026-08-16.** « Certains combats durent 10 min
#: car l'Arrow harcèle de loin un Corsair » : à 600 s, dix minutes tombaient
#: pile sur la borne, donc la victoire par harcèlement était systématiquement
#: coupée juste avant de se conclure. Une marge de moitié suffit à la laisser
#: aboutir sans rouvrir les sièges de deux heures que ce garde-fou écarte.
DUREE_MAX = 900.0

#: Vitesse de rotation moyenne (tangage et lacet) d'un chasseur médian —
#: **46,0 °/s mesurés** sur les 87 chasseurs armés de la base. Sert d'ancrage
#: à la part de poussée principale qu'un vaisseau sait réorienter.
ROTATION_CHASSEUR = 46.0

#: Un vaisseau ne pousse pas dans les six directions à la fois : sa poussée de
#: manœuvre publiée est un **total** réparti sur les axes. La géométrie n'étant
#: pas publiée (les tuyères sortent en `ManneuverThruster.UNDEFINED`), on
#: suppose une répartition uniforme sur les six directions. Hypothèse, dite
#: comme telle — mais infiniment plus proche du vrai qu'un total scalaire.
DIRECTIONS_DE_MANOEUVRE = 6.0

#: Fraction de sa boîte englobante qu'un vaisseau occupe réellement —
#: **0,43 médian mesuré** sur les 288 vaisseaux qui publient `CrossSection`.
#: Sert de repli quand la section n'est pas en base.
REMPLISSAGE_MEDIAN = 0.43

#: Vitesse de projectile de référence quand une arme ne la publie pas :
#: **1 196 m/s**, la médiane du catalogue (déjà retenue par `combat.py`).
VITESSE_PROJECTILE_MEDIANE = 1196.0

#: Cadence de référence : **200 coups/minute**, médiane mesurée sur les 138
#: armes de vaisseau montables (p10 à 50, p90 à 900). Au-dessus, on ne
#: bonifie pas — arroser ne crée pas de dégâts que le DPS ne dit pas déjà.
CADENCE_MEDIANE = 200.0

#: Part de la distorsion qui atteint les composants **quand le bouclier est
#: debout** : aucune. `ShieldsTotal.Absorption.Distortion` vaut 1 sur toute
#: la flotte — le bouclier prend tout, en n'encaissant que 5 à 25 % grâce à
#: sa résistance (0,75 à 0,95). C'est pour ça qu'un EMP se déclenche **après**
#: avoir fait tomber le bouclier, et pas avant.
RESISTANCE_DISTORSION = 0.85          # médiane de la plage publiée

#: Une fois les systèmes saturés, le vaisseau ne régénère plus, ne tire plus
#: et ne peut plus basculer en NAV. La durée se calcule sur les données du
#: composant : `decay_delay`, puis le temps que l'excédent redescende sous le
#: pool à `decay_rate` par seconde. Pour un TroMag (2 475) contre un
#: générateur de chasseur (1 900, 127/s, 1,5 s), cela fait ~6 s.
#: Plafonnée pour qu'une donnée aberrante ne fige pas un duel entier.
EXTINCTION_MAX = 20.0


def duree_d_extinction(distorsion: float, pool: dict[str, Any]) -> float:
    """Combien de temps les systèmes restent éteints, en secondes.

    Zéro si la décharge ne sature pas le pool : un composant qui encaisse
    sans saturer continue de fonctionner (`PowerRatioAtMaxDistortion` ne
    s'applique qu'au maximum).
    """
    maximum = pool.get("maximum") or 0.0
    if not maximum or distorsion < maximum:
        return 0.0
    taux = pool.get("decay_rate") or (maximum / 15.0)
    delai = pool.get("decay_delay") or 0.0
    return min(EXTINCTION_MAX, delai + (distorsion - maximum) / max(taux, 1e-6))


#: Couple correcteur d'un chasseur médian, servi quand la colonne manque —
#: base ingérée avant que `torque_imbalance` n'existe. Mesuré sur les
#: chasseurs de la flotte : la valeur la plus fréquente est 0,3, et 0,35
#: est la médiane.
COUPLE_MEDIAN = 0.35

#: Hérité d'un calibrage sur le corpus de chasseurs, désormais réduit à
#: dix-huit verdicts après retrait de la ligne Arrow/Shiv. Ce corpus est
#: incomplet et a servi au réglage : il ne valide ni la sémantique IFCS de la
#: colonne ni la fidélité du modèle. La valeur reste gelée en attendant que
#: les données et mécanismes du duel soient complets ; elle ne doit plus être
#: optimisée sur ce corpus.
#:
#: L'exposant est inférieur à 1 parce qu'un couple de 0,3 ne divise pas le
#: suivi par trois : le pilote compense en partie, il tire simplement moins
#: souvent dans la fenêtre. `scripts/essai_couple.py` refait la mesure.
EXPOSANT_COUPLE = 0.40

#: Part de l'esquive maximale qui est réellement **imprévisible**.
#:
#: L'anticipation traitait `½·a·t²` comme une dispersion totale, c'est-à-dire
#: comme si la cible changeait de cap à pleine puissance, dans une direction
#: nouvelle, à chaque instant. Un pilote **mène** sa cible : il voit la
#: manœuvre en cours et ne subit que ce qu'elle a d'imprévu.
#:
#: L'écart que ça produisait est mesuré, pas supposé : le Hornet Mk II
#: battait le Guardian MX **20-0** en portant 587 de DPS net contre 1 568 et
#: en encaissant sept fois moins de PV — parce qu'à 400 m il touchait à 77 %
#: quand le Guardian touchait à 19 %. Un facteur 4,2 sur la seule agilité,
#: là où le terrain donne le Guardian gagnant.
#:
#: La valeur a été calibrée historiquement par
#: `scripts/balayage_imprevisible.py`. Casey a depuis précisé que le banc n'a
#: pas de sens avant ingestion et consommation de tous les chiffres requis.
#: Le verrou `COMPLETUDE_DEMONTREE` interdit donc tout nouveau balayage ; 0,05
#: est conservé seulement pour ne pas changer silencieusement le moteur avant
#: sa reconstruction.
#:
#: Conséquence à assumer : l'anticipation cesse d'être le facteur dominant,
#: et ce qui départage devient le **suivi** — garder le nez sur une cible qui
#: traverse. C'est le dogfight tel qu'il se joue, et c'est cohérent avec les
#: verdicts ; ce n'est pas une façon de neutraliser l'agilité, qui reste
#: pleinement dans `p_suivi` et `avantage_de_position`.
#:
PART_IMPREVISIBLE = 0.05

# **Un écart connu, assumé.** Le camp qui ne peut pas l'emporter cherche la
# distance où il souffre le moins — souvent 1 500 m, où plus personne ne se
# touche. Le duel finit alors « sans vainqueur » là où le terrain dit
# « serré » (Talon/Gladius, Mustang Gamma/Sabre, verdicts du 2026-08-14) :
# un pilote ne perçoit pas un désavantage de 1 % et engage quand même.
#
# Trois corrections ont été essayées et **retirées**, toutes cassant la
# monotonie du modèle — améliorer un vaisseau pouvait le desservir :
# un seuil de rupture (13 régressions sur 1 500), le traitement du campement
# comme une fuite (3), et la borne de durée appliquée au choix de distance
# (49). Chacune remplaçait un défaut visible sur deux duels par un défaut
# structurel sur tous.
#
# On garde donc la propriété structurelle et on **dit** l'écart, plutôt que
# de le masquer. Détail dans docs/REVUE_MODELE_DUEL.md.

#: Rayon apparent d'un chasseur médian, en mètres — sert d'étalon à la
#: gerbe des armes à plombs. Tiré des sections publiées : un chasseur léger
#: présente de l'ordre de 30 à 70 m², soit un disque équivalent de ~4 m.
RAYON_CHASSEUR = 4.0

#: La géométrie des arcs, l'angle du croisement et la latence ne sont dans
#: aucune colonne. Plutôt que de les ignorer, la simulation les tire : un
#: écart-type sur le taux de touche, borné, qui est **la** source d'aléa du
#: « X sur 10 ». Ce n'est pas le talent — voir `_adresse`.
#:
#: **0,45 est calé, pas choisi** (2026-08-14). À 0,20, le rejeu par phase
#: d'engagement moyenne trop et les duels finissent en nuls ; au-delà de
#: 0,45, les issues nettes commencent à se brouiller. Balayé sur les 28
#: verdicts de terrain à 60 passes : 0,20 rend 19/28, 0,45 rend **21/28**,
#: 0,60 retombe à 20/28.
BRUIT_DE_TIR = 0.45

#: Délai sans impact au-delà duquel un bouclier repart, quand le générateur
#: équipé ne le publie pas. **Repli seulement** : le jeu donne la vraie
#: valeur par générateur, et elle varie — 32 valeurs distinctes sur 73 pour
#: `DamagedDelay`, dont voici la médiane, 31 pour `DownedDelay`.
#:
#: Le modèle a longtemps utilisé **2 secondes en dur**. C'était assez court
#: pour que les deux camps se rechargent entre deux passes sans qu'aucune
#: coque soit entamée — ce qui a fait retirer le repli tactique par deux
#: fois, alors qu'il existe bel et bien en jeu (« les pilotes expérimentés
#: se désengagent quand leurs boucliers sont bas, attendent la régénération
#: et reviennent »). La constante inventée avait produit le symptôme qui
#: condamnait une mécanique réelle.
DELAI_DEGAT_MEDIAN = 5.5
DELAI_TOMBE_MEDIAN = 11.0

# Constantes de version, identiques sur les 73 générateurs de bouclier —
# mesurées le 2026-08-07. Elles vivaient dans `combat.py`, qui les importe
# désormais d'ici : la simulation doit opposer **exactement** les mêmes
# verrous que le duel, et deux copies auraient fini par diverger.
ABSORPTION_PHYSIQUE = 0.45   # part du physique que le bouclier absorbe (max)
RESISTANCE_PHYSIQUE = 0.25   # réduction sur ce que le bouclier encaisse (max)
SEUIL_INTERRUPTION = 0.005   # part des PV d'un générateur qu'un impact doit
                             # atteindre pour suspendre la régénération


# ------------------------------------------------------- le profil physique

def _rotation(ship: dict[str, Any]) -> float:
    """Vitesse de réorientation en °/s : le pilote combine tangage et lacet."""
    pitch, yaw = ship.get("pitch") or 0.0, ship.get("yaw") or 0.0
    if pitch and yaw:
        return (pitch + yaw) / 2.0
    return pitch or yaw or ROTATION_CHASSEUR


#: Densité apparente (masse ÷ boîte englobante) sous laquelle la masse
#: publiée contredit les propres dimensions du vaisseau. **Mesurée** sur les
#: 230 vaisseaux armés : médiane 23,5 kg/m³, minimum légitime 3,8. Le seul
#: cas sous 1,0 est l'*Aegis Gladius Valiant*, annoncé à **502 kg** quand le
#: Gladius ordinaire en pèse 48 552 — soit 0,25 kg/m³, la densité d'un gaz.
#:
#: Sans ce garde-fou, sa poussée divisée par cette masse donnait **945 g**
#: d'accélération, contre 23,3 g au maximum légitime du catalogue, et il
#: sortait **invaincu sur 143 duels** de la revue exhaustive du 2026-08-14.
#: Une donnée amont fausse ne doit pas devenir un verdict.
DENSITE_MINIMALE = 1.0

#: L'accélération d'un chasseur médian, servie quand la masse est
#: inexploitable. Mesurée : 113,3 m/s² sur les 87 chasseurs armés.
ACCELERATION_CHASSEUR = 113.3


def masse_incoherente(ship: dict[str, Any]) -> bool:
    """La masse publiée contredit-elle les dimensions publiées.

    On ne juge pas la masse « au bon sens » : on la confronte à une autre
    colonne de la même source. C'est la seule façon d'écarter une donnée
    fausse sans inventer de plafond.
    """
    masse = ship.get("mass")
    longueur, largeur, hauteur = (
        ship.get("length"), ship.get("width"), ship.get("height"))
    if not masse or not all(v for v in (longueur, largeur, hauteur)):
        return False
    return masse / (longueur * largeur * hauteur) < DENSITE_MINIMALE


def _acceleration_d_evasion(ship: dict[str, Any]) -> float:
    """L'accélération latérale réellement disponible, en m/s².

    **`accel_maneuver` n'est pas utilisée, et c'est mesuré.** Elle prétend
    53,6 g de strafe sur un Pitbull, et **5,08 fois la poussée principale**
    sur un Terrapin : aucun vaisseau ne dérape cinq fois plus fort qu'il
    n'accélère en ligne droite. La colonne est un **total de poussée
    installée** — somme de toutes les tuyères de manœuvre divisée par la
    masse —, pas une accélération dans une direction. La géométrie qui
    permettrait de la répartir n'est pas publiée (les tuyères sortent en
    `ManneuverThruster.UNDEFINED`). On ne chiffre donc pas plutôt que de
    chiffrer faux : c'est la règle du §7, et c'est elle qui faisait sortir
    l'Aurora Mk II « plus évasive » que l'Arrow.

    Reste la **poussée principale**, qui est directionnelle et sans
    ambiguïté — c'est d'ailleurs la manœuvre réelle du dogfight : on oriente
    le nez et on pousse. Elle est pondérée par la vitesse de rotation,
    rapportée au chasseur médian (46,0 °/s mesurés) : un vaisseau qui met
    trois secondes à se retourner ne convertit pas sa poussée en esquive.
    """
    principale = ship.get("accel_main") or 0.0
    # `accel_main` est une poussée **déjà divisée par la masse** en amont :
    # une masse fausse la rend fausse. On retombe alors sur le chasseur
    # médian plutôt que de servir 945 g.
    if masse_incoherente(ship):
        principale = ACCELERATION_CHASSEUR
    orientable = min(1.0, _rotation(ship) / ROTATION_CHASSEUR)
    return principale * orientable


def _surface_apparente(ship: dict[str, Any]) -> float | None:
    """Surface présentée à un tireur, en m².

    **C'est `cross_y`, et les trois axes ont un nom.** SPViewer publie pour
    l'Arrow « 1 910 front × 5 647 side × 7 473 top », soit exactement nos
    `cross_y`, `cross_x`, `cross_z` — vérifié aussi sur le Sabre, dont le
    modificateur de signature de −40 % explique l'écart (6 467 × 0,6 =
    3 880 side). L'axe **Y est donc la face avant**, celle qu'un vaisseau
    présente quand il fait face.

    Le modèle retenait `(X + Z) / 2`, c'est-à-dire la moyenne du **flanc et
    du dessus** : il supposait une cible offerte de profil en permanence, et
    surestimait la surface d'un facteur **1,9 à 5,6** — 65,6 m² pour un
    Arrow qui n'en présente que 19,1.

    Deux raisons de retenir la face avant, et la seconde est de terrain :

    - **elle est la plus petite sur 233 vaisseaux sur 252**, un vaisseau
      étant profilé dans son axe de vol ;
    - **les armes sont à l'avant**, donc un vaisseau qui tire présente
      nécessairement cette face-là. « Un bon pilote montrera sa face la
      plus petite… il faut déterminer si les armes sont situées sur la
      face la plus petite pour savoir si même en tirant il présente sa
      meilleure face » (utilisateur, 2026-08-16) : la réponse est oui.

    Reste vraie la réserve d'unité : l'échelle au centième de m² cale la
    médiane à 0,43 de la boîte englobante, ce qui est crédible, mais
    quelques vaisseaux dépassent 1. Et `CrossSection` sert aussi de
    signature radar — c'est la même grandeur, modulée par un pourcentage de
    furtivité que le duel n'a pas à connaître.
    """
    y = ship.get("cross_y")
    if y:
        return y / 100.0
    x, z = ship.get("cross_x"), ship.get("cross_z")
    if x and z:
        return (x + z) / 2.0 / 100.0
    longueur, largeur, hauteur = (
        ship.get("length"), ship.get("width"), ship.get("height"))
    if all(v for v in (longueur, largeur, hauteur)):
        return REMPLISSAGE_MEDIAN * (largeur * hauteur + longueur * hauteur) / 2.0
    return None


def rayon_presente(cible: dict[str, Any], tireur: dict[str, Any]) -> float | None:
    """Le rayon apparent de la cible **face à ce tireur-là**.

    Une cible ne choisit pas toujours la face qu'elle montre. « Un Arrow
    pourra tourner facilement autour d'un vaisseau plus gros pour le forcer
    à montrer sa face la plus grosse tout en pouvant garder sa face fine »
    (utilisateur, 2026-08-16) : c'est le cœur du dogfight, et le modèle
    donnait la face avant **aux deux camps**, donc au gros comme à l'agile.

    Ce qui décide est la capacité de la cible à se maintenir nez au tireur,
    et le modèle la mesure déjà : `avantage_de_position`. Le rayon
    interpole donc entre la **face avant** — la cible tient l'angle — et le
    **flanc** — elle le subit et se fait déborder.

    Un Corsair (23 °/s) face à un Arrow (66 °/s) tient 0,12 de l'angle : il
    présente presque tout son flanc, 128 m² au lieu de 37. L'Arrow, lui,
    reste de face à 19 m². C'est le facteur que l'agilité achète vraiment,
    et il s'ajoute au taux de touche et à l'arc, sans les remplacer.
    """
    face = cible.get("rayon")
    flanc = cible.get("rayon_flanc")
    if face is None:
        return None
    if flanc is None or flanc <= face:
        return face
    tenue = avantage_de_position(cible, tireur)
    return face + (flanc - face) * (1.0 - tenue)


def profil_de_vol(ship: dict[str, Any]) -> dict[str, Any]:
    """Tout ce dont l'engagement a besoin, en grandeurs physiques."""
    surface = _surface_apparente(ship)
    # Rayon du disque de même surface : la cible n'est pas ronde, mais aucun
    # fichier ne publie son orientation dans le duel.
    rayon = math.sqrt(surface / math.pi) if surface else None
    scm = ship.get("scm_speed") or 0.0
    return {
        "rotation": _rotation(ship),
        "acceleration_evasion": _acceleration_d_evasion(ship),
        "surface": surface,
        "rayon": rayon,
        # Le flanc, pour la cible qui se fait déborder : `cross_x` est le
        # « side » nommé par SPViewer. Sans lui, un gros vaisseau lent
        # présenterait sa face avant même à un chasseur qui lui tourne
        # autour.
        "rayon_flanc": (math.sqrt((ship["cross_x"] / 100.0) / math.pi)
                        if ship.get("cross_x") else None),
        "scm": scm,
        "boost": ship.get("boost_speed") or scm,
        # **La vitesse du mode NAV**, où l'on coupe armes et boucliers pour
        # aller vite. C'est elle qui décide d'une fuite, pas le boost —
        # règle de terrain donnée par l'utilisateur le 2026-08-14. Elle
        # vaut 1 125 à 1 400 m/s contre 320 à 605 en boost.
        "nav": ship.get("max_speed") or ship.get("boost_speed") or scm,
        # Le couple correcteur réel — la latence et l'oversteer. Absent
        # d'une base pré-couple : on prend alors la médiane des chasseurs
        # plutôt que de pénaliser ou d'avantager au hasard.
        "couple": ship.get("torque_imbalance") or COUPLE_MEDIAN,
        "masse": ship.get("mass"),
        # Ce qui manque se dit, il ne se remplace pas par un défaut muet.
        "sans_dimensions": surface is None,
        "sans_acceleration": not (ship.get("accel_main")
                                  or ship.get("accel_maneuver")),
        # Se **dit** plutôt que de se corriger en silence : une fiche dont la
        # masse contredit les dimensions doit pouvoir être signalée au joueur.
        "masse_incoherente": masse_incoherente(ship),
    }


# ---------------------------------------------------------- le taux de touche

def taux_de_touche(arme: dict[str, Any], tireur: dict[str, Any],
                   cible: dict[str, Any], distance: float) -> float:
    """Probabilité qu'un tir de cette arme porte, à cette distance.

    Deux facteurs indépendants, tous deux géométriques :

    **L'anticipation.** Le projectile met `distance / vitesse` à parcourir la
    portée ; pendant ce temps la cible peut se déplacer de `½·a·t²`. On traite
    cet écart comme une dispersion aléatoire (la cible manœuvre sans prévenir)
    et on intègre une loi de Rayleigh sur le disque apparent — la forme
    classique du tir sur cible mobile, qui évite d'inventer un seuil.

    **Le suivi.** Pour garder le nez sur une cible qui traverse à `v`, il faut
    tourner à `v / distance` rad/s. Au-delà de ce que le vaisseau sait faire,
    le tir décroche proportionnellement. C'est ce terme qui rend le combat
    rapproché favorable au plus maniable — et il joue en sens **inverse** de
    l'anticipation, qui elle s'améliore en se rapprochant. Le compromis n'est
    donc pas réglé à la main : chaque vaisseau a sa distance idéale, et elle
    tombe du calcul.

    La **cadence** n'entre pas ici volontairement : elle est déjà dans le DPS,
    et l'espérance de dégâts d'une rafale ne dépend pas de son débit. La
    vitesse de projectile, elle, y entre pleinement (demande de l'utilisateur).
    """
    distance = max(distance, 1.0)

    # --- l'anticipation
    if arme.get("continu"):
        # Un faisceau touche à l'instant où il est pointé : aucun temps de vol.
        p_anticipation = 1.0
    else:
        vitesse = arme.get("projectile_speed") or VITESSE_PROJECTILE_MEDIANE
        temps_de_vol = distance / vitesse
        ecart = (0.5 * (cible.get("acceleration_evasion") or 0.0)
                 * temps_de_vol ** 2 * PART_IMPREVISIBLE)
        rayon = rayon_presente(cible, tireur)
        if rayon is None:
            p_anticipation = 1.0          # dimensions inconnues : dit ailleurs
        elif ecart <= 0:
            p_anticipation = 1.0
        else:
            # Rayleigh : P(toucher un disque de rayon r) = 1 - exp(-r²/2σ²).
            sigma = ecart / math.sqrt(3.0)
            p_anticipation = 1.0 - math.exp(-(rayon ** 2) / (2.0 * sigma ** 2))

    # --- le suivi
    vitesse_laterale = cible.get("scm") or 0.0
    omega_requis = vitesse_laterale / distance                    # rad/s
    omega_max = math.radians(tireur.get("rotation") or 0.0)
    if omega_requis <= 0:
        p_suivi = 1.0
    else:
        p_suivi = min(1.0, omega_max / omega_requis)
    # **Tourner vite ne suffit pas : encore faut-il s'arrêter où l'on
    # visait.** Le couple correcteur de l'IFCS dit exactement ça, et il
    # sépare franchement la flotte — Aurora 0,3, Arrow 1, tous les Anvil à
    # 1. « Un Aurora se pilote mal, quand tu tournes il y a énormément de
    # latence et d'oversteer et tu ne peux rien y faire » (utilisateur,
    # 2026-08-16) ; la vitesse angulaire publiée de l'Aurora Mk I est
    # pourtant celle d'un chasseur (65/59 contre 75/57 pour l'Arrow), ce
    # qui rendait la remarque inexplicable jusqu'à cette colonne.
    p_suivi *= min(1.0, tireur.get("couple") or COUPLE_MEDIAN) ** EXPOSANT_COUPLE

    return max(0.0, min(1.0,
                        p_anticipation * p_suivi
                        * _facteur_cadence(arme)
                        * _facteur_portee(arme, tireur, cible, distance)
                        * avantage_de_position(tireur, cible)))


def _facteur_cadence(arme: dict[str, Any]) -> float:
    """Une arme lente touche moins, et ce n'est pas une question d'espérance.

    Retiré du modèle le 2026-08-13 au motif que l'espérance de dégâts d'une
    rafale ne dépend pas de son débit — raisonnement juste pour des tirs
    indépendants, faux pour un tir **corrigé** : le pilote voit ses impacts
    et rectifie. Une arme rapide converge vers la solution de tir, une arme
    lente part à l'aveugle à chaque coup.

    C'est une règle donnée par l'utilisateur au sprint 21 — « un canon lent
    a peu de chances de toucher un Arrow, contrairement à un repeater
    rapide » — et sa réfutation par la mesure : le *Razor EX* et ses
    scatterguns Hellion (60 c/min, 8 plombs) battaient 19 fois sur 20 le
    *Razor LX* et ses Badger (750 c/min) sur la même coque, quand le verdict
    de terrain est l'inverse.

    Ancrage sur la **médiane du catalogue, 200 c/min** (138 armes), et
    racine carrée : 1 200 c/min ne valent pas six fois 200.
    """
    if arme.get("continu"):
        return 1.0
    cadence = arme.get("rpm")
    if not cadence:
        return 1.0
    return min(1.0, (cadence / CADENCE_MEDIANE) ** 0.5)


def _facteur_portee(arme: dict[str, Any], tireur: dict[str, Any],
                    cible: dict[str, Any], distance: float) -> float:
    """Portée efficace et **dispersion de la gerbe**.

    `effective_range` est publiée sur **142 armes de vaisseau sur 142**, et
    n'était jamais lue. Elle sépare franchement les familles : 999 à
    1 251 m pour les scatterguns, 1 734 à 2 799 m pour les repeaters et
    gatlings. C'est la façon dont le jeu dit qu'une arme est faite pour le
    contact.

    **Un scattergun disperse, c'est son principe** (règle de terrain de
    l'utilisateur, 2026-08-14 : « ça ne marche que de près sur les
    chasseurs »). On le chiffre sans rien inventer : la gerbe s'élargit
    linéairement avec la distance, et l'angle se déduit des deux colonnes
    publiées — à sa portée efficace, une arme couvre encore à peu près une
    cible de chasseur. La fraction de plombs qui portent est alors le
    rapport des surfaces, borné à 1.

    Une arme à projectile unique n'est concernée qu'au-delà de sa portée.
    """
    portee = arme.get("effective_range")
    if not portee:
        return 1.0
    plombs = arme.get("plombs") or 1
    if plombs > 1:
        # Rayon de la gerbe : nul au canon, égal à un chasseur médian à la
        # portée efficace. La cible plus grosse qu'un chasseur en attrape
        # davantage — d'où le rapport à son propre rayon.
        rayon_gerbe = RAYON_CHASSEUR * distance / portee
        rayon_cible = rayon_presente(cible, tireur) or RAYON_CHASSEUR
        if rayon_gerbe > rayon_cible:
            return min(1.0, (rayon_cible / rayon_gerbe) ** 2)
        return 1.0
    if distance <= portee:
        return 1.0
    # Décroissance en carré inverse plutôt qu'une coupure nette : le jeu
    # publie une portée « efficace », pas un mur.
    return (portee / distance) ** 2


def avantage_de_position(tireur: dict[str, Any],
                         cible: dict[str, Any]) -> float:
    """Fraction du temps où le tireur a seulement la cible dans son arc.

    C'est le facteur qui manquait, et c'est le principal en dogfight : entre
    chasseurs légers, l'avantage de l'agile n'est pas de mieux viser à
    distance égale — c'est de **se placer là où l'autre ne le voit pas**.
    Deux vaisseaux qui tournent l'un autour de l'autre voient l'écart de
    leurs vitesses de rotation se cumuler à chaque passe : celui qui tourne
    plus court prend les six heures de l'autre et l'y garde.

    Le rapport des rotations est donc élevé au carré — un avantage de
    position se compose sur la durée, il ne s'additionne pas —, et borné à 1
    en haut : mieux tourner que l'adversaire donne l'arc, pas des dégâts
    supplémentaires. Poids maison assumé, le seul du taux de touche : aucun
    fichier ne publie d'arc de tir.
    """
    mienne = tireur.get("rotation") or 0.0
    sienne = cible.get("rotation") or 0.0
    if mienne <= 0 or sienne <= 0:
        return 1.0
    return min(1.0, (mienne / sienne) ** 2)


def dps_effectif(armes: list[dict[str, Any]], tireur: dict[str, Any],
                 cible: dict[str, Any], distance: float) -> float:
    """Le DPS qui porte réellement, arme par arme."""
    return sum((a.get("dps") or 0.0)
               * taux_de_touche(a, tireur, cible, distance)
               for a in armes)


# ------------------------------------------------------ le choix de distance

def _rendement_structure(arme: dict[str, Any],
                         adverse: dict[str, Any]) -> float:
    """Ce qu'une arme rend en moyenne sur l'armure **puis** la coque.

    La simulation traverse les deux couches l'une après l'autre ; `dps_net`
    est un débit et doit donc les **pondérer par leurs points de vie** —
    c'est ce rapport qui décide du temps total. Sans lui, les deux moitiés
    du modèle se contrediraient de nouveau : la simulation appliquerait des
    multiplicateurs que le choix de distance et le verdict ignoreraient.

    Les deux vont en sens inverse — l'énergie fond l'armure (1,15 contre
    0,81), le balistique perce la coque (0,75 contre 0,60) — donc le
    rendement dépend de la part d'armure du vaisseau visé : 1 % sur un
    Polaris, 35 % sur un Gladius.
    """
    armure = adverse.get("armure") or 0.0
    coque = adverse.get("coque_nue") or 0.0
    total = armure + coque
    if total <= 0:
        return 1.0
    cle = "physical" if arme.get("type") == "physical" else "energy"
    res = (adverse.get("res") or {}).get(cle, 1.0)
    mult = (adverse.get("mult") or {}).get(cle, 1.0)
    return (armure * res + coque * mult) / total


def dps_net(camp: dict[str, Any], adverse: dict[str, Any],
            distance: float) -> float:
    """Le DPS qui entame vraiment la cible, régénération déduite.

    C'est **le** chiffre qui décide d'un duel, et il n'existait nulle part :
    un bouclier qui régénère plus vite qu'on ne le vide ne tombe jamais. Deux
    cas, l'un et l'autre publiés :

    - si les impacts atteignent le seuil d'interruption (0,5 % des PV d'un
      générateur), la régénération est suspendue et tout le DPS travaille ;
    - sinon elle continue sous le feu, et il faut la dépasser pour percer.

    **La part balistique qui traverse ne subit pas ce test**, et c'était
    l'erreur de logique. Le bouclier n'absorbe que 45 % du physique : les
    55 % restants atteignent la coque dès la première seconde, sans avoir
    à faire tomber quoi que ce soit. Remarque de l'utilisateur, 2026-08-15
    — « vu que les gatling traversent, elles ne sont même pas obligées de
    faire tomber le bouclier une seule fois pour détruire la cible ».

    Déduire la régénération du DPS **total** faisait donc rendre « jamais »
    à des duels que le balistique gagne : un F8C Lightning en gatlings ne
    perçait « jamais » un Vanguard Warden, alors qu'il le tue en 32 s. Et
    comme `distance_preferee` lit ce chiffre, le camp concerné décidait de
    rompre — le duel finissait en fuite ou en match nul, jamais par un
    verdict. La simulation, elle, traitait déjà la traversée correctement :
    les deux moitiés du modèle se contredisaient.
    """
    traverse = sum(
        (a.get("dps") or 0.0)
        * taux_de_touche(a, camp["profil"], adverse["profil"], distance)
        * (1 - ABSORPTION_PHYSIQUE)
        for a in camp["armes"] if a.get("type") == "physical")
    brut = sum(
        (a.get("dps") or 0.0)
        * taux_de_touche(a, camp["profil"], adverse["profil"], distance)
        * _rendement_structure(a, adverse)
        for a in camp["armes"])
    if camp.get("interrompt"):
        return brut
    # Ce qui doit vaincre la régénération, c'est le reste : l'énergie en
    # entier, et la fraction du balistique que le bouclier absorbe.
    contre_bouclier = max(0.0, brut - traverse - (adverse.get("regen") or 0.0))
    return traverse + contre_bouclier


#: Rapport des vitesses de mise à mort au-delà duquel une distance lente
#: reste gagnante. **Ce qui rend le harcèlement viable n'est pas le débit
#: mais l'impunité** : « l'Arrow harcèle de loin un Corsair et finit par le
#: détruire car il ne peut pas le toucher alors que l'Arrow si ». À dix
#: fois, l'adversaire met dix fois plus longtemps à tuer — autrement dit il
#: n'y arrivera jamais avant la fin du combat, et la lenteur n'est plus un
#: défaut mais le prix de ne pas être touché.
DOMINATION = 10.0


def _domine(a: dict[str, Any], b: dict[str, Any], pv_a: float, pv_b: float,
            distance: float) -> bool:
    """A tue-t-il B assez impunément pour qu'une distance lente reste bonne.

    Le rapport, et non la différence : c'est la seule forme qui dise
    « il ne me touche pas », indépendamment du débit absolu.
    """
    mien = dps_net(a, b, distance)
    sien = dps_net(b, a, distance)
    if mien <= 0:
        return False
    if sien <= 0:
        return True                      # il ne me touche plus du tout
    return (mien / max(pv_b, 1.0)) / (sien / max(pv_a, 1.0)) >= DOMINATION


def _avantage(a: dict[str, Any], b: dict[str, Any], pv_a: float, pv_b: float,
              distance: float) -> float:
    """Écart des vitesses de mise à mort, vu de A. Positif, A prend le dessus.

    On compare des **fractions de barre de vie par seconde** — la question
    d'un duel n'est pas « qui tape le plus fort » mais « qui tombe le
    premier ». Le DPS est net de régénération : un bouclier qui se recharge
    plus vite qu'on ne le vide ne tombe jamais, et cette distance-là ne vaut
    donc rien même si elle protège.

    Deux formulations ont été essayées et écartées le 2026-08-13, chacune
    par la mesure :

    - **soustraire les DPS bruts** ignorait la régénération, et donnait un
      Arrow perçant un Corsair qui se recharge à 19 000/s ;
    - **diviser** — le rapport des temps de mort — récompensait la distance
      où *personne* ne se touche : un rapport énorme sur un DPS résiduel, un
      combat de 500 s, et dix-sept duels sur dix-huit en match nul. Un pilote
      cherche à gagner, pas à avoir théoriquement raison dans une heure.

    La différence, elle, pousse au contact tant que l'échange y reste
    favorable : c'est le comportement décrit par l'utilisateur — « des
    distances beaucoup plus proches pour tirer sans rater ».
    """
    mien = dps_net(a, b, distance) / max(pv_b, 1.0)
    sien = dps_net(b, a, distance) / max(pv_a, 1.0)
    return mien - sien


def conclut(temps: float | None) -> bool:
    """Un temps de destruction est-il celui d'un duel, ou d'un siège.

    **Une victoire qui demande deux heures n'en est pas une.** Mesuré le
    2026-08-14 sur les 26 335 duels du catalogue : 2,3 % des issues
    décisives réclamaient plus de dix minutes de tir continu, la pire
    125 minutes — du grignotage à 1 500 m, où l'attaquant n'est plus touché
    mais n'inflige que 26 DPS. Le Mustang Gamma « battait » ainsi un
    Gladius avec 437 DPS de catalogue.

    Le seuil est celui de la simulation, et c'est le point : sans lui, le
    matchup annonçait un vainqueur là où la simulation rendait un match nul
    — deux réponses contradictoires du même bot sur la même question.

    Le contrôle porte sur le **résultat**, jamais sur le choix de distance :
    l'y mettre introduit un saut dans la fonction qu'on maximise, et
    améliorer un vaisseau pouvait alors le desservir (mesuré : 49
    régressions de monotonie sur 4 500 essais).
    """
    return temps is not None and temps <= DUREE_MAX


def distance_preferee(a: dict[str, Any], b: dict[str, Any],
                      pv_a: float, pv_b: float) -> tuple[float, bool]:
    """La distance que A cherche à imposer, et s'il cherche à rompre.

    A balaye les distances tenables et retient celle où il tombe l'autre le
    plus vite **relativement** à ce qu'il encaisse. Si aucune distance ne lui
    donne l'avantage, il ne campe pas : il **décroche** — règle de terrain de
    l'utilisateur, « un Arrow ne pourra pas forcément se faire détruire par
    un Hammerhead, il va pouvoir s'enfuir ». Qu'il y parvienne dépend ensuite
    de sa vitesse, pas de sa volonté.
    """
    meilleure, meilleur = DISTANCE_MIN, None
    peut_percer = False
    d = DISTANCE_MIN
    while d <= DISTANCE_MAX + 1e-9:
        net = dps_net(a, b, d)
        if net > 0:
            peut_percer = True
        # **On ne choisit que parmi les distances où l'on peut conclure.**
        # Le camp désavantagé a naturellement intérêt à s'éloigner jusqu'à
        # ce que plus personne ne se touche — c'est là que ses pertes sont
        # minimales, et c'est un match nul déguisé.
        #
        # Mais « conclure » ne veut pas dire « conclure vite ». Le seuil
        # était `DUREE_MAX`, ce qui écartait précisément la tactique que
        # l'utilisateur décrit : « l'Arrow harcèle de loin un Corsair et
        # finit par le détruire car il ne peut pas le toucher alors que
        # l'Arrow si ». Dix minutes de harcèlement gagnant, refusées par le
        # même test qui refuse les sièges de deux heures.
        #
        # Ce qui distingue les deux n'est pas la durée mais **l'asymétrie** :
        # on accepte une distance lente si l'on y encaisse nettement moins
        # qu'on n'inflige. Le grignotage stérile, lui, est lent des deux
        # côtés et reste écarté.
        lent = pv_b / net > DUREE_MAX if net > 0 else True
        conclut_ici = net > 0 and (not lent or _domine(a, b, pv_a, pv_b, d))
        if conclut_ici:
            score = _avantage(a, b, pv_a, pv_b, d)
            if meilleur is None or score > meilleur:
                meilleure, meilleur = d, score
        d += 50.0
    # **On rompt quand on ne peut pas gagner, pas quand on est moins bon.**
    #
    # La règle portait d'abord sur l'avantage : décrocher dès qu'aucune
    # distance ne le rendait positif. Mesuré le 2026-08-14 contre les
    # verdicts de l'utilisateur, c'était bien trop prompt — le Gladius
    # fuyait un Talon pour **1,2 %** de désavantage, là où le terrain dit
    # « serré ». Un pilote qui peut entamer son adversaire se bat.
    #
    # Ce qui déclenche vraiment la rupture, c'est l'impossibilité : aucune
    # distance où le feu franchisse la déflexion et la régénération. C'est
    # le cas de l'Arrow devant un Corsair, du Fury devant un F8C et du
    # Pitbull devant un Polaris — les trois fuites que le terrain confirme.
    # **On rompt quand on ne peut pas gagner, ou quand on perd nettement.**
    #
    # Deux règles trop promptes ont été essayées et réfutées par les
    # verdicts de terrain du 2026-08-14 :
    #
    # - décrocher dès que l'avantage est négatif faisait fuir le Gladius
    #   devant un Talon pour **1,2 %** de désavantage, là où le terrain dit
    #   « serré » ;
    # - traiter le campement à 1 500 m comme une fuite revenait au même, et
    #   réintroduisait des régressions de monotonie.
    #
    # Ce qui manquait : un pilote **ne perçoit pas** un désavantage de un
    # pour cent. Les deux se croient capables de l'emporter et engagent ;
    # c'est ce qui rend tant de duels de chasseurs serrés. On ne décroche
    # que sur un écart franc, ou sur une impossibilité.
    # Percer quelque part sans pouvoir conclure nulle part, c'est ne pas
    # pouvoir gagner : on rompt.
    if not peut_percer or meilleur is None:
        return DISTANCE_ROMPUE, True
    return meilleure, False


# ------------------------------------------------------------ la simulation

def armes_passantes(armes: list[dict[str, Any]],
                    defense: dict[str, Any]) -> list[dict[str, Any]]:
    """Ne garde que ce qui franchit la déflexion d'armure de la cible.

    **La mobilité ne fait pas passer un tir qui ricoche.** Un Fury frappe à
    26 par plomb contre les 54 de déflexion d'un F8C Lightning : quelle que
    soit son agilité, il ne perce pas. La simulation doit opposer le même
    verrou que `combat._verrous`, sans quoi un taux de touche parfait
    contournerait la physique publiée — exactement ce que le §7 interdit.
    """
    gardees = []
    for arme in armes:
        seuil = defense.get(f"defl_{arme.get('type')}") or 0
        par_plomb = arme.get("par_plomb")
        if arme.get("continu") or par_plomb is None or par_plomb >= seuil:
            gardees.append(arme)
    return gardees


def _interrompt_la_regeneration(armes: list[dict[str, Any]],
                                defense: dict[str, Any]) -> bool:
    """Un bouclier régénère sous le feu tant qu'aucun impact ne le secoue.

    Seuil publié : 0,5 % des PV d'un générateur. C'est l'observation de
    l'utilisateur retrouvée dans `StunParams` — l'Idris régénère sous les
    tirs légers —, et c'est ce qui interdit à un chasseur de percer un gros
    bouclier : sans interruption, la régénération se compare directement au
    DPS effectif, et 873 DPS ne franchissent pas les 19 000/s d'un Corsair.
    """
    hp_par_gen = (defense.get("shield_hp") or 0.0) / (
        defense.get("n_generateurs") or 1)
    seuil = SEUIL_INTERRUPTION * hp_par_gen
    return any(arme.get("continu")
               or (arme.get("par_plomb") or 0) >= seuil
               for arme in armes)


def _camp(nom: str, armes: list[dict[str, Any]], profil: dict[str, Any],
          defense: dict[str, Any], defense_adverse: dict[str, Any]
          ) -> dict[str, Any]:
    # Les armes se filtrent contre la défense d'en face — mais les recalées
    # ne sont **pas jetées** : le seuil de déflexion ne vaut que tant que
    # l'armure est à pleine santé. « Une fois l'armure affaiblie par des
    # armes plus lourdes, les petits calibres redeviennent efficaces »
    # (sources 4.7). Elles servent donc dès que l'armure est tombée.
    utiles = armes_passantes(armes, defense_adverse)
    passantes = {id(a) for a in utiles}
    return {
        "nom": nom,
        "armes": utiles,
        "armes_toutes": list(armes),
        "armes_deviees": len(armes) - len(utiles),
        "_passantes": passantes,
        "interrompt": _interrompt_la_regeneration(utiles, defense_adverse),
        # L'EMP porté, s'il y en a un, et ce que la cible lui oppose. Les
        # deux viennent de `combat.py` ; absents, la mécanique ne se
        # déclenche simplement pas.
        "emp": defense.get("emp"),
        "pool_distorsion": defense_adverse.get("pool_distorsion"),
        "profil": profil,
        "bouclier_max": float(defense.get("shield_hp") or 0.0),
        "regen": float(defense.get("shield_regen") or 0.0),
        # Les délais du **générateur équipé**, jamais une médiane : c'est le
        # loadout qui décide. Le repli n'existe que parce qu'ils sont longs.
        "delai_degat": float(defense.get("shield_damaged_delay")
                             or DELAI_DEGAT_MEDIAN),
        "delai_tombe": float(defense.get("shield_downed_delay")
                             or DELAI_TOMBE_MEDIAN),
        "coque": float((defense.get("hull_health") or 0.0)
                       + (defense.get("armor_health") or 0.0)),
        # **L'armure est une couche à part, et le jeu la traite ainsi.**
        # Deux sources communautaires le disent dans les mêmes termes :
        # « les armes à énergie sont plus efficaces pour fondre l'armure,
        # tandis que les balistiques offrent une pénétration supérieure une
        # fois l'armure réduite ». Les deux colonnes publiées le disent
        # aussi, et en sens inverse l'une de l'autre :
        #
        #   `ResistanceMultipliers` — 0,81 physique / 1,15 énergie, ce que
        #     l'**armure** encaisse ; l'énergie la fond mieux ;
        #   `DamageMultipliers` — 0,75 physique / 0,60 énergie, ce qui
        #     atteint la **coque** ensuite ; le balistique y passe mieux.
        #
        # Les additionner en une seule barre, comme le fait `coque`
        # ci-dessus, revient donc à perdre l'arbitrage entre les deux
        # familles d'armes. L'armure pèse 14 à 35 % du total selon le
        # vaisseau — 1 % sur un Polaris, 35 % sur un Gladius.
        "armure": float(defense.get("armor_health") or 0.0),
        "coque_nue": float(defense.get("hull_health") or 0.0),
        "res": {"physical": float(defense.get("res_physical") or 1.0),
                "energy": float(defense.get("res_energy") or 1.0)},
        "mult": {"physical": float(defense.get("mult_physical") or 1.0),
                 "energy": float(defense.get("mult_energy") or 1.0)},
    }


#: Bouclier tombé sous cette part, on rompt le contact pour se recharger ; on
#: revient une fois au-dessus. Deux seuils distincts pour qu'un vaisseau pile
#: sur la limite ne fasse pas la navette à chaque pas.
#:
#: **Le seuil est bas, et c'est mesuré.** À 25 % de bouclier restant, le
#: repli déclenchait avant que le tireur ait seulement percé le bouclier
#: d'en face : l'Arrow décrochait après 5 s en n'ayant entamé que 2 200 des
#: 6 000 PV de l'Aurora, les deux se rechargeaient pendant le décrochage, et
#: aucune coque n'était jamais touchée — dix-sept duels sur dix-huit sans
#: conclusion. Le repli n'a de sens qu'une fois le bouclier réellement à
#: terre, quand c'est la coque qui est en jeu.
BOUCLIER_REPLI = 0.10
BOUCLIER_RETOUR = 0.85


def _souffle(etat: dict[str, Any], camp: dict[str, Any],
             adverse: dict[str, Any], etat_adverse: dict[str, Any]) -> bool:
    """Le camp cherche-t-il à rompre le contact pour se recharger ?

    C'est une mécanique **réelle** du jeu — « les pilotes expérimentés se
    désengagent quand leurs boucliers sont bas, attendent la régénération
    et reviennent » — et la première description qu'en avait donnée
    l'utilisateur : « 1 500 m maintenu pour régénérer ses boucliers sans
    être touché ».

    Trois conditions, et il faut les trois :

    - **on ne se dégage que si l'on est plus rapide.** Rompre face à plus
      véloce que soi n'offre pas un répit, ça offre sa poupe. C'est la
      remarque de l'utilisateur — « un Aurora ne pourra jamais s'échapper
      d'un Arrow qui le poursuit » — et c'est ce qui donne au duel de
      référence son mécanisme : à 515 m/s contre 460, l'Arrow peut se refaire
      quand il veut et l'Aurora jamais ;
    - **on ne se dégage que si l'on perd.** La première version repliait
      aussi le camp qui dominait, ce qui n'a aucun sens : on lâche un
      adversaire dont on est en train de venir à bout. On compare donc les
      deux barres — celui qui est le plus entamé, en proportion, est celui
      qui a intérêt à rompre ;
    - **le bouclier doit être réellement bas.** À 25 % restants, le repli
      déclenchait avant même d'avoir percé celui d'en face.

    Elle avait été retirée deux fois parce qu'elle bouclait à l'infini. La
    cause n'était pas la manœuvre mais le **délai de régénération**, fixé à
    2 s en dur : les deux camps se rechargeaient entre deux passes. Avec les
    délais publiés par générateur, décrocher coûte 5,5 à 11 secondes, et le
    calcul redevient honnête.
    """
    if camp["bouclier_max"] <= 0 or camp["regen"] <= 0:
        return False
    if (camp["profil"].get("boost") or 0) <= (adverse["profil"].get("boost") or 0):
        return False
    # **Un repli qui ne produit rien s'abandonne.** Mesuré sur le duel
    # fondateur en armement d'origine : à 40 s l'Arrow décroche, bouclier à
    # zéro, monte à 1 450 m — et y reste **jusqu'au bout des dix minutes**.
    # Son bouclier ne remonte jamais parce que l'Aurora le touche encore
    # assez pour réinitialiser le délai de régénération, donc la condition
    # de retour (85 %) n'est jamais remplie. Huit passes sur dix finissaient
    # ainsi, à une distance que **personne ne veut** — l'Arrow cherche
    # 450 m, l'Aurora 250.
    #
    # Ce correctif avait été écrit puis retiré le 2026-08-14 : il portait
    # alors l'Aurora à dix victoires sur vingt, ce que la consigne d'alors
    # — « il n'y a aucun monde où l'Aurora gagne » — interdisait. La
    # consigne a été **révisée** depuis : « il peut, mais très
    # difficilement ». Un pilote qui constate que sa manœuvre ne le sort
    # pas du feu se retourne et combat.
    if etat.get("repli_renonce"):
        return False
    if etat.get("en_repli"):
        etat["repli_depuis"] = etat.get("repli_depuis", 0.0) + PAS
        # Le temps qu'il faudrait pour que le repli aboutisse : redémarrage
        # du bouclier, puis recharge complète. Au-delà, il a échoué.
        if etat["repli_depuis"] > camp["delai_tombe"] + (
                camp["bouclier_max"] / max(camp["regen"], 1e-9)):
            etat["repli_renonce"] = True
            etat["en_repli"] = False
            return False

    # **Un repli peut ne mener à rien, et il ne faut pas le corriger.**
    # Mesuré le 2026-08-14 : le Gladius décroche à 1 500 m, le Talon l'y
    # suit et le touche sans interruption, donc son bouclier ne remonte
    # jamais, donc la condition de retour n'est jamais remplie ; il finit
    # les dix minutes en fuite et le duel n'a pas de vainqueur.
    #
    # Le correctif naturel — renoncer au bout du temps qu'il faudrait pour
    # se recharger — a été écrit puis **retiré**, pour deux raisons dans
    # cet ordre. D'abord il rend l'Aurora Mk II gagnante dix fois sur vingt
    # contre l'Arrow en armement d'origine, ce que l'utilisateur exclut
    # absolument. Ensuite et surtout, il corrigeait un défaut **qui
    # n'existe pas** : un duel sans vainqueur au bout de dix minutes est un
    # duel serré, et « serré » est précisément le verdict de terrain sur
    # Talon/Gladius comme sur Gamma/Sabre. C'est le classement de l'issue
    # qui manquait, pas le modèle — voir `tests/test_verdicts_terrain.py`.
    mien = (etat["bouclier"] + etat["coque"]) / max(
        camp["bouclier_max"] + camp["coque"], 1.0)
    sien = (etat_adverse["bouclier"] + etat_adverse["coque"]) / max(
        adverse["bouclier_max"] + adverse["coque"], 1.0)
    if mien >= sien:
        return False
    part = etat["bouclier"] / camp["bouclier_max"]
    if etat.get("en_repli"):
        etat["en_repli"] = part < BOUCLIER_RETOUR
    else:
        etat["en_repli"] = part < BOUCLIER_REPLI
    return bool(etat["en_repli"])


#: Durée pendant laquelle une configuration d'engagement tient, en
#: secondes. C'est l'échelle à laquelle l'avantage change de main : on se
#: croise, on tourne, l'un des deux ressort dans le dos de l'autre.
#:
#: **Calée par mesure, pas choisie.** Rejouer à chaque pas de 0,1 s
#: moyennerait tout — la loi des grands nombres écraserait la variance sur
#: un duel de quarante secondes — et ne jamais rejouer revient à décider du
#: vainqueur au premier tirage.
PERIODE_D_ENGAGEMENT = 15.0


#: **Le boost est une réserve, pas un régime.** `boost_capacity` vaut 20
#: sur les 124 vaisseaux de combat, `boost_regen` 0,8/s et
#: `boost_regen_time` 26,9 s — trois constantes publiées, identiques
#: partout, donc sans pouvoir discriminant mais qui **bornent** la
#: manœuvre. Le modèle déplaçait à `boost_speed` indéfiniment.
#:
#: Le **coût** par seconde n'est pas publié. On pose 1 unité/s, ce qui
#: donne 20 s de boost continu et 25 s pour refaire le plein — cohérent
#: avec les 26,9 s publiées. C'est une hypothèse, dite comme telle ; elle
#: ne départage aucun vaisseau puisqu'elle est la même pour tous.
BOOST_CAPACITE = 20.0
BOOST_COUT = 1.0
BOOST_REGEN = 0.8


def _adresse(alea: random.Random) -> float:
    """Le facteur de tir d'un camp sur une phase d'engagement.

    **Ce n'est pas le talent du pilote, et le nommer ainsi était
    l'erreur.** Il représente la configuration de l'engagement : qui prend
    l'ascendant au croisement, sous quel angle, avec quelle énergie. Les
    deux camps tirent dans la **même** loi — donc à niveau égal — et le
    facteur se rejoue toutes les `PERIODE_D_ENGAGEMENT` secondes, parce
    qu'un dogfight n'est pas décidé par sa première passe. C'est la
    remarque de l'utilisateur (2026-08-14) : « pilote égal ne veut pas dire
    que le combat sera égal à chaque fois. »

    Ce rejeu a d'abord été écrit, **retiré**, puis remis le même jour, et
    l'aller-retour vaut d'être gardé. Il avait été retiré parce qu'il
    laissait l'Aurora Mk II battre l'Arrow 3 à 9 fois sur 300 selon les
    réglages, contre 0 sur 300 à facteur figé — ce qui contredisait la
    consigne d'alors, « il n'y a aucun monde où l'Aurora gagne ».
    L'utilisateur a **révisé cette consigne** : « il peut, mais très
    difficilement ». Une victoire sur trente à cinquante est précisément
    cela, et le facteur figé — qui rendait la chose *impossible* — décrivait
    donc moins bien le jeu que le rejeu.

    La leçon n'est pas « le garde-fou était mauvais » : il a écarté quatre
    corrections réellement fausses. C'est qu'un garde-fou **catégorique**
    interdit aussi ce qui est seulement rare, et qu'il faut alors le
    chiffrer plutôt que l'énoncer.
    """
    return max(0.2, min(1.8, alea.gauss(1.0, BRUIT_DE_TIR)))


#: Part de coque en dessous de laquelle on ne se bat plus : on part.
#: **La coque, pas le bouclier** — celui-ci se recharge, elle non. Un quart
#: restant, c'est le moment où un joueur décroche plutôt que de rendre son
#: vaisseau. Seuil maison, mais la mécanique est de terrain (utilisateur,
#: 2026-08-16) et la donnée qu'elle lit est publiée.
COQUE_ABANDON = 0.25

#: Avance de vitesse NAV qu'il faut pour semer un poursuivant. Voir
#: `_abandonne` : mesurée sur les quatre fuites du banc, qui vont de
#: +10,9 % à +35,7 %, contre +1,2 % pour le faux positif qu'elle écarte.
MARGE_DE_DECROCHAGE = 1.10

#: Avance suffisante pour **partir** d'un combat qu'on ne peut pas gagner.
#:
#: Ce n'est pas la même question que `MARGE_DE_DECROCHAGE`, qui décide si
#: l'on peut fuir la mort une fois la coque entamée — là il faut être sûr.
#: Ici on quitte un combat qu'on ne perdra pas non plus : il suffit
#: d'ouvrir la distance, même lentement, et c'est la **simulation** qui dit
#: si l'on y arrive, pas ce seuil.
#:
#: Le seuil de 10 % était appliqué aux deux, et il interdisait la fuite à
#: 59 % des couples de chasseurs — l'écart médian de vitesse NAV entre
#: chasseurs est de 7,4 %. Cas signalé par l'utilisateur le 2026-08-16 :
#: « le Arrow ne peut pas fuir face au Shiv ? ça m'étonne » — 1 215 contre
#: 1 175, soit +3,4 %, refusé par la marge.
#:
#: Ce qui rend une fuite risquée n'est pas un seuil de vitesse mais les
#: cinq secondes de bascule NAV, armes et boucliers déjà coupés : elles
#: sont déjà modélisées, et c'est là que la fuite se paie.
MARGE_DE_DEPART = 1.02

#: Secondes entre l'activation du mode NAV et l'accélération pleine, armes
#: et boucliers déjà coupés. C'est la fenêtre où une fuite peut échouer, et
#: elle est le prix du décrochage. Règle de terrain donnée par
#: l'utilisateur (2026-08-16) ; l'ordre de grandeur recoupe le « mode
#: toggling 1 s (+4 QD) » publié par SPViewer.
BASCULE_NAV = 5.0

#: Écart au-delà de sa distance de combat à partir duquel un poursuivant
#: préfère courir que tirer. En deçà, couper ses armes pour gagner quelques
#: mètres coûte tout le feu de la fenêtre — et c'est ainsi que six duels du
#: banc finissaient à 0-0, les deux camps désarmés en NAV.
MARCHE_DE_POURSUITE = 500.0

#: Secondes entre la sortie du mode NAV et le retour des armes et des
#: boucliers. « Tu dois sortir du mode NAV un peu avant, car il y a quand
#: même une seconde pour que tout revienne » (utilisateur, 2026-08-16).
#:
#: C'est la contrepartie de `BASCULE_NAV` : entrer coûte cinq secondes,
#: sortir en coûte une. Sans elle, un poursuivant tirait à l'instant même où
#: il repassait en combat, ce qui rendait la poursuite gratuite dans un sens
#: et payante dans l'autre.
RETOUR_DE_NAV = 1.0


def _abandonne(etat: dict[str, Any], camp: dict[str, Any],
               adverse: dict[str, Any]) -> bool:
    """Le camp décide-t-il de quitter le combat, coque entamée ?

    Distinct du repli de `_souffle`, qui va se recharger pour **revenir** :
    ici on s'en va pour de bon. La condition de vitesse porte donc sur le
    **mode NAV**, seule façon de décrocher réellement — et non sur le
    boost, qu'un poursuivant tient aussi.
    """
    if etat.get("abandonne"):
        return True
    coque_max = camp.get("coque_nue") or camp.get("coque") or 0.0
    if coque_max <= 0:
        return False
    if etat["coque"] / coque_max >= COQUE_ABANDON:
        return False
    # **Il faut une marge, pas une supériorité au mètre près.** Sans elle,
    # l'Aurora Mk II décrochait de l'Arrow 40 fois sur 40 : sa vitesse NAV
    # la dépasse de **1,2 %** (1 230 contre 1 215), ce qui ne suffit pas à
    # semer quelqu'un dans les faits — et l'utilisateur l'a dit dès le
    # premier jour, « un Aurora ne pourra jamais s'échapper d'un Arrow qui
    # le poursuit ». Les quatre fuites du banc, elles, tiennent toutes avec
    # 10 % : Pitbull/Terrapin +10,9 %, Arrow/Corsair +18,5 %,
    # Fury/F8C +17,5 %, Pitbull/Polaris +35,7 %. Le seuil sépare donc les
    # vrais décrochages du bruit, et il est lu dans la mesure.
    if (camp["profil"].get("nav") or 0) <= (
            adverse["profil"].get("nav") or 0) * MARGE_DE_DECROCHAGE:
        return False        # trop lent : partir ne ferait qu'offrir sa poupe
    etat["abandonne"] = True
    return True


#: Dégâts par seconde en dessous desquels on est **sorti du feu**.
#:
#: Le seuil est très bas, et il le faut : ce qui empêche un bouclier de
#: repartir n'est pas le montant des dégâts mais le **fait d'être touché**,
#: chaque impact réarmant le délai de régénération. À 1 500 m l'Aurora ne
#: rend plus que 9 DPS à l'Arrow, et cela suffisait à le laisser bouclier
#: à zéro pendant dix minutes. « Se recharger » veut donc dire « ne plus
#: être atteint du tout », pas « être peu touché ».
FEU_NEGLIGEABLE = 1.0


def distance_de_repli(camp: dict[str, Any], adverse: dict[str, Any]) -> float:
    """Jusqu'où il faut aller pour se recharger tranquille.

    « Si l'Arrow ne peut pas regen à 1 500 il n'a qu'à aller plus loin.
    Dans la réalité, si je veux regen mon shield et que j'ai l'occasion car
    je vais plus vite, je vais aussi loin qu'il faut et je reviens »
    (utilisateur, 2026-08-16).

    Le repli visait 1 500 m et rien d'autre. Quand l'adversaire y touchait
    encore un peu, le bouclier ne repartait jamais (chaque impact réarme le
    délai de régénération) et le fuyard restait planté là jusqu'à la fin des
    dix minutes. La borne était arbitraire : on cherche une **distance**,
    pas un plafond.

    On prend donc la première distance où le feu reçu devient négligeable —
    c'est un décrochage temporaire, pas une fuite : le camp revient dès son
    bouclier refait. Le balayage part de la distance d'engagement, non de
    `DISTANCE_MAX` : depuis que celle-ci vaut 6 km, partir de là ferait
    reculer de six kilomètres un vaisseau que l'on cesse de toucher à mille.
    """
    d = DISTANCE_ENGAGEMENT
    while d < DISTANCE_ROMPUE:
        if dps_effectif(adverse["armes"], adverse["profil"], camp["profil"],
                        d) <= FEU_NEGLIGEABLE:
            return d
        d += 100.0
    return DISTANCE_ROMPUE - 200.0    # au plus loin sans quitter le combat


def _un_combat(a: dict[str, Any], b: dict[str, Any],
               alea: random.Random | None = None, *,
               adresses: tuple[float, float] | None = None,
               depart: float | None = None) -> dict[str, Any]:
    """Une passe : distance évolutive, régénération, décrochage.

    Les adresses et la distance de départ peuvent être **imposées** : c'est
    ce qui permet de rejouer le combat sans hasard pour chiffrer une issue
    rare (`chance_de_renverser`).
    """
    # Les deux camps tirent dans la **même** loi — donc à pilote égal — et
    # rejouent à chaque phase d'engagement : voir `_adresse`. `fige`
    # protège `chance_de_renverser`, qui impose ses facteurs pour chiffrer
    # une issue rare sans hasard.
    fige = adresses is not None
    if adresses is None:
        adresses = (_adresse(alea), _adresse(alea))
    etat = {}
    for camp, adresse in zip((a, b), adresses):
        etat[camp["nom"]] = {
            "bouclier": camp["bouclier_max"],
            # **Trois couches, pas deux.** L'armure est une barre à part —
            # 14 à 35 % du total selon le vaisseau — et les deux
            # multiplicateurs publiés vont en sens inverse l'un de
            # l'autre : l'énergie fond l'armure (`res` 1,15 contre 0,81),
            # le balistique perce la coque ensuite (`mult` 0,75 contre
            # 0,60). Les fusionner effaçait l'arbitrage entre les deux
            # familles d'armes, celui-là même que l'utilisateur décrit —
            # « une coque fine il faudra forcément des balistiques ».
            "armure": camp["armure"],
            "coque": camp["coque_nue"],
            # On entre au combat bouclier plein : la régénération n'attend pas.
            "depuis_impact": DELAI_TOMBE_MEDIAN,
            "en_repli": False,
            "adresse": adresse,
            "prochain_engagement": PERIODE_D_ENGAGEMENT,
            "boost": BOOST_CAPACITE,
            "en_nav": False,
        }
    pv_a = a["bouclier_max"] + a["coque"]
    pv_b = b["bouclier_max"] + b["coque"]

    # Les distances de combat ne dépendent pas de l'état : elles se calculent
    # une fois. Les recalculer à chaque pas coûtait une minute par duel pour
    # un résultat identique.
    combat_a, rompt_a = distance_preferee(a, b, pv_a, pv_b)
    combat_b, rompt_b = distance_preferee(b, a, pv_b, pv_a)
    # Aussi loin qu'il faut pour se recharger — calculé une fois, comme les
    # distances de combat.
    repli_a = distance_de_repli(a, b)
    repli_b = distance_de_repli(b, a)
    # Qui a les jambes pour quitter l'autre. La vitesse NAV ne change pas
    # pendant le duel : le calcul est fait une fois.
    peut_semer_a = a["profil"]["nav"] >= b["profil"]["nav"] * MARGE_DE_DEPART
    peut_semer_b = b["profil"]["nav"] >= a["profil"]["nav"] * MARGE_DE_DEPART

    # **Un duel commence parfois au contact, et c'est ce qui décide d'une
    # fuite.** Le tirage partait de `DISTANCE_MIN * 2` — 400 m —, ce qui
    # excluait toute la moitié basse de la plage que l'utilisateur a
    # définie (« on est plus sur du 200 m - 1 500 m »). Conséquence
    # mesurée : l'Arrow décrochait du Hurricane 20 fois sur 20, alors que
    # le verdict de terrain est « ça dépend à quel point l'Arrow est
    # proche : il a des chances de s'enfuir comme il peut se faire
    # détruire ». Le `× 2` n'était justifié nulle part.
    distance = (depart if depart is not None
                else alea.uniform(DISTANCE_MIN, DISTANCE_ENGAGEMENT))
    temps = 0.0
    while temps < DUREE_MAX:
        # --- chacun cherche sa distance, et la vitesse tranche
        #
        # Le repli est la seconde moitié de la règle de terrain : « 1 500 m
        # maintenu pour régénérer ses boucliers sans être touché ». Bouclier
        # bas, on rompt le contact le temps de le remonter — et on revient.
        # Le repli est la seconde moitié de la règle de terrain : « 1 500 m
        # maintenu pour régénérer ses boucliers sans être touché ». Bouclier
        # à terre et combat mal engagé, on rompt le temps de le remonter.
        # Le **repli tactique** — décrocher à bouclier bas, se recharger,
        # revenir — a été écrit, puis retiré le 2026-08-14. Quel que soit le
        # seuil, il bouclait : les deux camps se rechargent pendant le
        # décrochage dans des temps quasi identiques (5,26 s sur tous les
        # générateurs testés), aucune coque n'est durablement entamée, et le
        # duel n'aboutit pas. Talon contre Gladius et Mustang Gamma contre
        # Sabre finissaient ainsi « sans vainqueur » là où le terrain dit
        # « serré ». Le modéliser demanderait le coût réel du décrochage —
        # l'exposition pendant la manœuvre —, que rien ne publie.
        # **Chacun vise sa propre distance, et c'est essentiel.** Les faire
        # converger vers la plus courte — l'idée que celui qui cherche
        # l'engagement l'obtient — a été essayé le 2026-08-14 et retiré : le
        # modèle passait de huit à cinq verdicts de terrain justes, et les
        # duels dits « serrés » devenaient 17-3 ou 0-20.
        #
        # La raison est que l'oscillation **est** le combat serré : chacun
        # a l'avantage à sa distance, la distance alterne, et l'issue tient
        # à qui impose la sienne le plus longtemps. L'écraser revient à
        # donner le duel à celui qui préfère le contact.
        #
        # Le **repli tactique** se superpose : bouclier bas et plus rapide
        # que l'autre, on rompt le contact le temps de se recharger. Il
        # avait été retiré deux fois parce qu'il bouclait — mais la cause
        # était une constante de régénération inventée à 2 s. Avec les
        # délais publiés (5,5 s entamé, 11 s à terre), décrocher coûte
        # vraiment, et la manœuvre redevient un choix.
        etat_a, etat_b = etat[a["nom"]], etat[b["nom"]]
        # Nouvelle phase d'engagement : la géométrie se rejoue pour les
        # deux camps, dans la même loi.
        if not fige and temps >= etat_a["prochain_engagement"]:
            etat_a["adresse"] = _adresse(alea)
            etat_b["adresse"] = _adresse(alea)
            etat_a["prochain_engagement"] += PERIODE_D_ENGAGEMENT
        # **On peut décider de partir en cours de route.** « Fuite reste une
        # option si jamais un des vaisseaux n'a plus beaucoup de vie »
        # (utilisateur, 2026-08-16). Le modèle ne décidait de rompre qu'**au
        # début**, sur la seule impossibilité de percer : un vaisseau qui
        # perdait sa coque pendant l'échange n'avait aucun moyen de s'en
        # aller. Il se battait jusqu'à la destruction, ce qu'aucun joueur ne
        # fait.
        #
        # Deux conditions, comme pour le repli : il faut être **à terre** —
        # la coque entamée, pas seulement le bouclier — et pouvoir
        # distancer l'autre en NAV, sinon partir revient à offrir sa poupe.
        rompt_a = rompt_a or _abandonne(etat_a, a, b)
        rompt_b = rompt_b or _abandonne(etat_b, b, a)
        # **On ne rompt que si l'on peut semer.** `distance_preferee` rend
        # « rompt » dès qu'aucune distance ne permet de conclure — c'est
        # juste comme intention, et faux comme option : l'Aurora Mk I, à
        # 1 200 de vitesse NAV, n'a aucun moyen de quitter un Scorpius à
        # 1 385. Sans ce filtre elle partait quand même, le poursuivant la
        # rattrapait, elle repartait, et dix minutes de navette rendaient un
        # **nul** sur un duel que le terrain dit écrasé.
        #
        # `_abandonne` appliquait déjà cette marge au décrochage sur coque
        # basse ; elle manquait à la rupture d'impuissance. Celui qui ne
        # peut ni percer ni semer reste et subit — c'est ce qui fait la
        # différence entre une défaite et un match nul.
        rompt_a = rompt_a and peut_semer_a
        rompt_b = rompt_b and peut_semer_b
        # **Systèmes éteints, on ne part pas non plus.** Le mode NAV demande
        # des moteurs alimentés ; c'est précisément à quoi sert un EMP —
        # « le désactiver entièrement et en faire une cible facile ». Sans
        # cette ligne, la victime rompait le combat en étant désarmée, ce
        # qui aurait fait de l'EMP une façon de laisser fuir sa proie.
        rompt_a = rompt_a and not etat_a.get("eteint")
        rompt_b = rompt_b and not etat_b.get("eteint")
        voulue_a = (DISTANCE_ROMPUE if rompt_a
                    else repli_a if _souffle(etat_a, a, b, etat_b)
                    else combat_a)
        voulue_b = (DISTANCE_ROMPUE if rompt_b
                    else repli_b if _souffle(etat_b, b, a, etat_a)
                    else combat_b)
        marche = 25.0
        intention_a = (0.0 if abs(voulue_a - distance) < marche
                       else math.copysign(1.0, voulue_a - distance))
        intention_b = (0.0 if abs(voulue_b - distance) < marche
                       else math.copysign(1.0, voulue_b - distance))

        # --- le mode NAV : on décroche en coupant armes et boucliers
        #
        # Règle de terrain (utilisateur, 2026-08-14) : « tu peux réactiver
        # l'autre mode qui te permet d'accélérer plus vite, mais tu n'as
        # plus de bouclier ni armes. Ça te permet de tenter de rattraper
        # une cible qui fuit, mais celle-ci peut aussi activer ce mode-là. »
        #
        # C'est **la** mécanique de la fuite, et elle manquait : le modèle
        # décrochait à `boost_speed`, alors que la vitesse NAV vaut deux à
        # trois fois plus. Un fuyard en NAV distance donc toujours un
        # poursuivant resté en boost — le poursuivant n'a d'autre choix que
        # de couper aussi, et la course se joue sur `max_speed`.
        # On ne suit en NAV que si l'on peut effectivement rattraper :
        # couper ses armes pour se laisser distancer n'a aucun intérêt.
        #
        # **Et on ne poursuit en NAV que de loin.** Un poursuivant a les
        # armes coupées lui aussi : tant qu'il court, il ne tue pas. Le
        # Scorpius, plus rapide que l'Aurora Mk I qui décroche, basculait
        # en NAV à la première seconde et n'en sortait plus — les deux
        # camps désarmés, dix minutes, et un **nul** annoncé sur un duel
        # que le terrain dit écrasé (utilisateur, 2026-08-16 : « l'adversaire
        # de l'Aurora PULVÉRISE »). Six duels du banc sur vingt-deux
        # rendaient 0-0 pour cette seule raison.
        #
        # La règle : on ne coupe ses armes que si l'on est **trop loin pour
        # tirer**. À portée, on tire — le fuyard, lui, reste en NAV, donc
        # sans bouclier ni armes, et c'est là qu'il meurt.
        loin_a = distance > combat_a + MARCHE_DE_POURSUITE
        loin_b = distance > combat_b + MARCHE_DE_POURSUITE
        etat_a["en_nav"] = bool(rompt_a) or (
            bool(rompt_b) and a["profil"]["nav"] > b["profil"]["nav"]
            and loin_a)
        etat_b["en_nav"] = bool(rompt_b) or (
            bool(rompt_a) and b["profil"]["nav"] > a["profil"]["nav"]
            and loin_b)

        # --- la vitesse de chacun : NAV, boost tant qu'il en reste, sinon SCM
        vitesses = []
        for camp, e, intention in ((a, etat_a, intention_a),
                                   (b, etat_b, intention_b)):
            if e["en_nav"]:
                # Couper les boucliers, c'est les perdre : ils repartent de
                # zéro au retour, après le délai de redémarrage.
                e["bouclier"] = 0.0
                e["depuis_impact"] = 0.0
                # **La bascule n'est pas instantanée**, et c'est ce qui
                # rend une fuite risquée. « Il y a du temps entre le moment
                # de l'activation du mode NAV et le moment où tu peux
                # accélérer à fond, je dirais 5 secondes où tu n'as plus de
                # shield ni d'armes » (utilisateur, 2026-08-16). Pendant
                # cette fenêtre le fuyard est déjà désarmé et à découvert,
                # mais pas encore rapide : il subit le feu sans rendre.
                e["nav_depuis"] = e.get("nav_depuis", 0.0) + PAS
                vitesses.append(camp["profil"]["nav"]
                                if e["nav_depuis"] >= BASCULE_NAV
                                else camp["profil"]["boost"])
                continue
            # **Sortir du NAV coûte une seconde.** Armes et boucliers ne
            # reviennent pas à l'instant où l'on repasse en combat : c'est
            # la contrepartie des cinq secondes d'entrée, et sans elle la
            # poursuite était gratuite au retour. Règle de terrain donnée
            # par l'utilisateur le 2026-08-16.
            if e.get("nav_depuis"):
                e["retour_nav"] = RETOUR_DE_NAV
            elif e.get("retour_nav"):
                e["retour_nav"] = max(0.0, e["retour_nav"] - PAS)
            e["nav_depuis"] = 0.0
            if intention and e["boost"] > 0:
                e["boost"] = max(0.0, e["boost"] - BOOST_COUT * PAS)
                vitesses.append(camp["profil"]["boost"])
            else:
                e["boost"] = min(BOOST_CAPACITE, e["boost"] + BOOST_REGEN * PAS)
                vitesses.append(camp["profil"]["scm"])

        distance += (intention_a * vitesses[0]
                     + intention_b * vitesses[1]) * PAS
        distance = max(distance, 50.0)

        # --- l'engagement est-il rompu ?
        #
        # La marche est retranchée du seuil : sans elle, un fuyard voyait son
        # intention retomber à zéro 25 m avant la rupture et restait planté
        # là. Mesuré sur Mustang Gamma / Sabre — 2 978,95 m au bout de dix
        # minutes, un match nul annoncé pour une fuite réussie.
        # **Un repli n'est pas une rupture** : le camp revient dès son
        # bouclier refait. Seul un vaisseau qui a décidé de partir — parce
        # qu'il ne perce pas, ou parce que sa coque est à terre — quitte
        # réellement le combat.
        if distance >= DISTANCE_ROMPUE - marche and (rompt_a or rompt_b):
            # Celui qui cherchait à partir y est parvenu. Si les deux
            # renoncent, personne ne poursuit : il n'y a pas de vainqueur.
            fuyard = (a["nom"] if rompt_a and not rompt_b
                      else b["nom"] if rompt_b and not rompt_a else None)
            # Le fuyard a forcément les jambes pour partir : `rompt_*` a
            # déjà été filtré par `peut_semer_*` en tête de tour. Trois
            # kilomètres ne mettent à l'abri que si l'on est plus rapide, et
            # c'est là que la règle est appliquée — pas ici.
            return {"issue": "fuite" if fuyard else "nul", "fuyard": fuyard,
                    "duree": temps, "distance": distance}

        # --- l'EMP : on le charge en combattant, on le lâche au bon moment
        #
        # « Il vaut mieux que le vaisseau n'ait plus de bouclier pour le
        # désactiver entièrement et en faire une cible facile »
        # (utilisateur, 2026-08-16) — et la donnée dit pourquoi : le
        # bouclier absorbe **toute** la distorsion tant qu'il tient
        # (`Absorption.Distortion = 1`). On charge donc en permanence, et
        # on décharge quand les trois conditions sont réunies : chargé, la
        # cible à portée, et son bouclier à terre.
        for porteur, cible in ((a, b), (b, a)):
            emp = porteur.get("emp")
            if not emp:
                continue
            e = etat[porteur["nom"]]
            recu = etat[cible["nom"]]
            e["emp_charge"] = e.get("emp_charge", 0.0) + PAS
            if e["emp_charge"] < (emp.get("charge_time") or 0.0):
                continue
            if distance > (emp.get("emp_radius") or 0.0):
                continue
            if recu["bouclier"] > 0:
                # Le bouclier encaisse tout, et n'en souffre presque pas.
                continue
            duree = duree_d_extinction(emp.get("distortion_damage") or 0.0,
                                       porteur.get("pool_distorsion") or {})
            if duree <= 0:
                continue
            recu["eteint"] = max(recu.get("eteint", 0.0), duree)
            # Charge consommée, et il faut attendre la recharge complète.
            e["emp_charge"] = -(emp.get("cooldown_time") or 0.0)

        # Les systèmes éteints ne tirent pas, ne régénèrent pas et ne
        # basculent pas en NAV : c'est le « cible facile » du terrain.
        for camp in (a, b):
            e = etat[camp["nom"]]
            if e.get("eteint"):
                e["eteint"] = max(0.0, e["eteint"] - PAS)

        # --- le tir, dans les deux sens
        #
        # **Un camp en repli ne tire pas.** Rompre le contact, c'est mettre
        # le nez à l'opposé de l'adversaire ; les armes fixes d'un chasseur
        # pointent alors le vide, et le décrochage se paie du feu qu'on
        # n'inflige pas. Le repli restait jusqu'ici entièrement gratuit.
        #
        # Mesuré : sur les douze verdicts de terrain, cette règle ne change
        # **aucun** résultat — le repli ne se déclenche que dans les duels
        # déjà décidés. Elle est gardée parce qu'elle est juste, pas parce
        # qu'elle corrige quelque chose ; c'est ce qui la rend sûre.
        for tireur, cible in ((a, b), (b, a)):
            # En NAV, les armes sont coupées — c'est le prix de la vitesse.
            # Et pendant la seconde qui suit la sortie, elles ne sont pas
            # encore revenues.
            if (etat[tireur["nom"]].get("en_repli")
                    or etat[tireur["nom"]].get("en_nav")
                    or etat[tireur["nom"]].get("retour_nav")
                    or etat[tireur["nom"]].get("eteint")):
                continue
            recu = etat[cible["nom"]]
            adresse = etat[tireur["nom"]]["adresse"]
            touche = False
            # Armure à terre : même les calibres que le seuil recalait
            # travaillent de nouveau.
            armes = (tireur["armes"] if recu["armure"] > 0
                     else tireur.get("armes_toutes") or tireur["armes"])
            for arme in armes:
                porte = ((arme.get("dps") or 0.0) * adresse
                         * taux_de_touche(arme, tireur["profil"],
                                          cible["profil"], distance) * PAS)
                if porte <= 0:
                    continue
                touche = True
                physique = arme.get("type") == "physical"
                if recu["bouclier"] > 0 and physique:
                    # Le balistique **traverse** : le bouclier n'en absorbe
                    # que 45 %, et encore avec 25 % de résistance.
                    au_bouclier = porte * ABSORPTION_PHYSIQUE * (
                        1 - RESISTANCE_PHYSIQUE)
                    porte *= (1 - ABSORPTION_PHYSIQUE)
                    recu["bouclier"] -= min(recu["bouclier"], au_bouclier)
                elif recu["bouclier"] > 0:
                    pris = min(recu["bouclier"], porte)
                    recu["bouclier"] -= pris
                    porte -= pris
                if porte <= 0:
                    continue
                # Puis l'armure, puis la coque — chacune avec son
                # multiplicateur, et ils ne vont pas dans le même sens.
                cle = "physical" if physique else "energy"
                if recu["armure"] > 0:
                    recu["armure"] -= porte * cible["res"][cle]
                else:
                    recu["coque"] -= porte * cible["mult"][cle]
            # Un bouclier ne cesse de régénérer que s'il est **secoué** : des
            # impacts sous le seuil le grattent sans suspendre sa recharge.
            if touche and tireur["interrompt"]:
                recu["depuis_impact"] = 0.0

        # --- la régénération, seulement hors du feu qui secoue
        #
        # **Le délai dépend de l'état du bouclier**, et le jeu publie les deux
        # cas séparément : un bouclier seulement entamé repart après
        # `DamagedDelay` (5,5 s en médiane), un bouclier **tombé** après
        # `DownedDelay` (~11 s). C'est ce qui rend un décrochage coûteux, et
        # donc décidable — précision de l'utilisateur, 2026-08-14.
        for camp in (a, b):
            e = etat[camp["nom"]]
            e["depuis_impact"] += PAS
            attente = (camp["delai_tombe"] if e["bouclier"] <= 0
                       else camp["delai_degat"])
            # Générateur saturé de distorsion : il est **éteint**, il ne
            # régénère rien. C'est ce qui fait la force d'un EMP — plus que
            # ses dégâts, qui sont nuls.
            if e.get("eteint"):
                e["depuis_impact"] = 0.0
                continue
            if e["depuis_impact"] >= attente and e["bouclier"] < camp["bouclier_max"]:
                e["bouclier"] = min(camp["bouclier_max"],
                                    e["bouclier"] + camp["regen"] * PAS)

        # --- une destruction ?
        mort_a = etat[a["nom"]]["coque"] <= 0
        mort_b = etat[b["nom"]]["coque"] <= 0
        if mort_a or mort_b:
            if mort_a and mort_b:
                return {"issue": "double", "duree": temps, "distance": distance}
            return {"issue": "victoire",
                    "vainqueur": b["nom"] if mort_a else a["nom"],
                    "duree": temps, "distance": distance}

        temps += PAS

    # **Juger aux points a été écrit, mesuré, puis retiré — et ce qu'il a
    # révélé compte plus que le correctif.** « Ton modèle doit supposer la
    # victoire » (utilisateur, 2026-08-14) : rendre « nul » au bout de dix
    # minutes revient à dire que personne n'a pris l'ascendant, ce qui est
    # rarement vrai. Départager sur la **part de barre restante** fait bien
    # conclure tous les duels — plus un seul nul sur les douze du premier
    # banc.
    #
    # Mais le score tombe de 21 à 16 sur 28, et surtout l'Aurora Mk II bat
    # l'Arrow **15 fois sur 20**. Ce n'est pas le correctif qui est faux :
    # il expose que le modèle ne donne pas l'avantage à l'Arrow, il le fait
    # seulement *survivre* en se repliant à 1 500 m où plus rien ne se
    # touche. La cause est chiffrée : la YellowJacket GT-210 de l'Arrow
    # frappe à 7,56 par plomb contre un seuil de déflexion de 11 sur
    # l'Aurora — elle **ricoche**, et l'Arrow perd 338 de ses 896 DPS. En
    # part de barre par seconde, à 250 m, l'Aurora inflige alors 3,41 %
    # contre 1,91 % : elle est presque deux fois plus efficace, et l'Arrow
    # ne repasse devant qu'au-delà de 650 m, là où les deux ne font
    # pratiquement plus rien.
    #
    # Renforcer l'agilité redresse ce duel — à l'exposant 6 l'Arrow gagne
    # 94 % — mais fait tomber le banc à 11 sur 28. Le défaut n'est donc pas
    # un réglage, et il ne se corrigera pas ici : soit le ricochet de la
    # gatling sur une Aurora est faux, soit c'est l'écart de points de vie
    # (2,15×) qui l'est.
    return {"issue": "nul", "duree": temps, "distance": distance}


def camp(nom: str, armes: list[dict[str, Any]], vol: dict[str, Any],
         defense: dict[str, Any],
         defense_adverse: dict[str, Any]) -> dict[str, Any]:
    """Un bord de duel, prêt à être opposé. Porte d'entrée publique."""
    return _camp(nom, armes, profil_de_vol(vol), defense, defense_adverse)


def taux_moyen(camp_tireur: dict[str, Any], camp_cible: dict[str, Any],
               distance: float) -> float:
    """Le taux de touche du bord entier, pondéré par la part de DPS.

    Une gatling qui porte le gros du feu compte plus qu'un canon d'appoint :
    moyenner à parts égales ferait dépendre le résultat du nombre d'affûts.
    """
    total = sum((a.get("dps") or 0.0) for a in camp_tireur["armes"])
    if total <= 0:
        return 0.0
    porte = dps_effectif(camp_tireur["armes"], camp_tireur["profil"],
                         camp_cible["profil"], distance)
    return porte / total


def distance_d_equilibre(a: dict[str, Any], b: dict[str, Any],
                         pv_a: float, pv_b: float) -> float:
    """La distance à laquelle le duel se joue réellement.

    Chacun cherche la sienne ; **le plus rapide impose la sienne**, parce
    que c'est lui qui décide d'ouvrir ou de fermer. C'est le mécanisme
    central du modèle, et celui que le rendu doit pouvoir expliquer : à
    515 m/s de boost contre 460, l'Arrow choisit le combat et l'Aurora le
    subit. À vitesse égale, on prend le milieu — aucun des deux ne peut
    forcer l'autre.
    """
    voulue_a, rompt_a = distance_preferee(a, b, pv_a, pv_b)
    voulue_b, rompt_b = distance_preferee(b, a, pv_b, pv_a)
    # Un camp qui cherche à rompre ne « veut » pas une distance de combat :
    # il veut partir. La distance du duel reste alors celle de l'autre.
    if rompt_a and not rompt_b:
        return voulue_b
    if rompt_b and not rompt_a:
        return voulue_a
    # **La vitesse décide de partir, pas de camper.** Elle a d'abord servi
    # à départager les distances voulues — « le plus rapide impose la
    # sienne » —, mais le camp désavantagé veut *toujours* la distance
    # longue, celle où plus personne ne se touche. Lui donner raison parce
    # qu'il court plus vite vidait le duel : le Wolf et l'Arrow, à 510 et
    # 515 m/s, finissaient à 1 500 m sans qu'aucun ne conclue, et le Gladius
    # campait devant un Talon que le terrain dit « serré ».
    #
    # S'il pouvait vraiment partir, `rompt` l'aurait déjà dit plus haut :
    # rester veut dire accepter le combat. Celui qui le cherche l'obtient.
    # **À vitesse égale, c'est la cible qui choisit son terrain.**
    #
    # Toute règle qui *bascule* entre les deux distances voulues casse la
    # monotonie du modèle, et deux l'ont prouvé le 2026-08-14 : le **milieu**
    # des deux (le Banu Defender à qui l'on donne 20 % de DPS voyait
    # l'adversaire reculer, le milieu se déplacer de 425 à 500 m et son
    # avantage baisser), puis le **maximum** des deux (améliorer la rotation
    # du Gladiator poussait le Hornet à camper à 1 500 m, et le maximum lui
    # donnait raison : marge divisée par treize).
    #
    # Reste que **camper n'est pas tenable** : le camp désavantagé cherche
    # toujours la distance longue, où plus personne ne se touche. Lui donner
    # raison vidait le duel de sa substance — le Wolf et l'Arrow, à 510 et
    # 515 m/s de boost, finissaient à 1 500 m sans qu'aucun ne conclue.
    #
    # Or il ne campe qu'à défaut de pouvoir vraiment partir : sa fuite est
    # traitée plus haut, par `rompt`. S'il reste, c'est qu'il accepte le
    # combat.
    #
    # **On retient alors la distance que A cherche, et rien d'autre.** Toute
    # règle qui *arbitre* entre les deux distances voulues casse la
    # monotonie, et trois l'ont prouvé le 2026-08-14 : le milieu des deux,
    # puis le maximum, puis le minimum — à chaque fois, améliorer un
    # vaisseau déplaçait l'arbitrage contre lui. Seule une référence fixe
    # est monotone : la marge de A à sa propre distance est le **maximum**
    # de sa marge, et un maximum de fonctions croissantes croît.
    #
    # Chaque sens du duel se calcule donc à **sa** distance — l'aller à
    # celle que l'attaquant cherche, le retour à celle que la cible
    # cherche. C'est aussi plus fidèle qu'une distance unique : deux
    # pilotes ne veulent pas le même combat.
    return voulue_a


def chance_de_renverser(a: dict[str, Any], b: dict[str, Any],
                        outsider: str) -> float | None:
    """« Un Aurora a une chance sur combien de battre un Arrow ? »

    Demande de l'utilisateur (2026-08-13) : quand une issue est écrasante,
    un « 0 sur 10 » est moins parlant — et moins honnête — qu'un ordre de
    grandeur. Reste à le calculer sans tirer cent mille combats.

    Le seul hasard du modèle est l'**adresse** des deux pilotes, tirée une
    fois par combat selon une normale centrée sur 1. On cherche donc par
    dichotomie le rapport d'adresses `r` à partir duquel l'outsider
    l'emporte, puis on intègre : avec `A` et `B` normales de moyenne 1 et
    d'écart-type `s`, `A - r·B` est normale de moyenne `1 - r` et de
    variance `s²(1 + r²)`, d'où `P(A > r·B)` en une fonction d'erreur.

    Rend `None` quand aucun écart d'adresse ne renverse l'issue : c'est un
    verrou, pas de la malchance, et annoncer « une chance sur un million »
    laisserait croire qu'il en existe une.
    """
    perdant, gagnant = ((a, b) if outsider == a["nom"] else (b, a))

    def _issue(rapport: float) -> bool:
        """L'outsider gagne-t-il si son adresse vaut `rapport` fois l'autre."""
        adresses = ((rapport, 1.0) if perdant is a else (1.0, rapport))
        issue = _un_combat(a, b, adresses=adresses,
                           depart=(DISTANCE_MIN + DISTANCE_ENGAGEMENT) / 2)
        return (issue["issue"] == "victoire"
                and issue["vainqueur"] == perdant["nom"])

    # Le plafond : au-delà, l'adresse n'est plus une explication plausible.
    haut = 12.0
    if not _issue(haut):
        return None
    bas = 1.0
    # 18 pas suffisent : la précision sur le rapport tombe sous 5·10⁻⁵, très
    # en deçà de ce qu'un ordre de grandeur exige, et chaque pas est un
    # combat complet rejoué.
    for _ in range(18):
        milieu = (bas + haut) / 2
        if _issue(milieu):
            haut = milieu
        else:
            bas = milieu
    ratio = haut

    variance = (BRUIT_DE_TIR ** 2) * (1.0 + ratio ** 2)
    z = (1.0 - ratio) / math.sqrt(variance)
    # P(N(0,1) > -z) = ½(1 + erf(z/√2))
    probabilite = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return probabilite if probabilite > 0 else None


def simuler(nom_a: str, armes_a: list[dict[str, Any]], vol_a: dict[str, Any],
            defense_a: dict[str, Any],
            nom_b: str, armes_b: list[dict[str, Any]], vol_b: dict[str, Any],
            defense_b: dict[str, Any], *,
            passes: int = 10, graine: int = 20260813) -> dict[str, Any]:
    """« Sur 10 combats, combien en gagne le Arrow ? »

    Graine fixe : deux appels sur la même base rendent le même compte. Un
    banc qui change de résultat d'une exécution à l'autre tombe un jour sur
    deux — la leçon de `test_generalisation.py`.
    """
    alea = random.Random(graine)
    a = _camp(nom_a, armes_a, profil_de_vol(vol_a), defense_a, defense_b)
    b = _camp(nom_b, armes_b, profil_de_vol(vol_b), defense_b, defense_a)

    comptes = {nom_a: 0, nom_b: 0}
    fuites = {nom_a: 0, nom_b: 0}
    nuls = 0
    durees: list[float] = []
    for _ in range(passes):
        issue = _un_combat(a, b, alea)
        if issue["issue"] == "victoire":
            comptes[issue["vainqueur"]] += 1
            durees.append(issue["duree"])
        elif issue["issue"] == "fuite" and issue.get("fuyard"):
            fuites[issue["fuyard"]] += 1
        else:
            nuls += 1

    # Une issue écrasante mérite son ordre de grandeur plutôt qu'un zéro :
    # « une chance sur 1 300 » se retient, « 0 sur 10 » n'apprend rien.
    chances: dict[str, float | None] = {}
    for perdant, vainqueur in ((nom_a, nom_b), (nom_b, nom_a)):
        if comptes[perdant] == 0 and comptes[vainqueur] > 0:
            chances[perdant] = chance_de_renverser(a, b, perdant)

    # Les distances d'équilibre expliquent le résultat mieux que le compte.
    pv_a = a["bouclier_max"] + a["coque"]
    pv_b = b["bouclier_max"] + b["coque"]
    d_a, rompt_a = distance_preferee(a, b, pv_a, pv_b)
    d_b, rompt_b = distance_preferee(b, a, pv_b, pv_a)
    return {
        "passes": passes,
        "victoires": comptes,
        "fuites": fuites,
        "nuls": nuls,
        # Probabilité qu'un outsider renverse l'issue, quand elle existe.
        # `None` explicite : le verrou n'est pas de la malchance.
        "chance_de_renverser": chances,
        "duree_moyenne": (sum(durees) / len(durees)) if durees else None,
        "distance_voulue": {nom_a: d_a, nom_b: d_b},
        "cherche_a_rompre": {nom_a: rompt_a, nom_b: rompt_b},
        "dps_effectif": {
            nom_a: dps_effectif(a["armes"], a["profil"], b["profil"], d_a),
            nom_b: dps_effectif(b["armes"], b["profil"], a["profil"], d_b),
        },
        # Régénération déduite : c'est ce chiffre, et non le DPS brut, qui
        # dit si une victoire est seulement possible.
        "dps_net": {nom_a: dps_net(a, b, d_a), nom_b: dps_net(b, a, d_b)},
        # Ce que la déflexion a écarté, et ce qui secoue le bouclier d'en
        # face : sans ça, un « il ne perce pas » n'est pas explicable.
        "armes_deviees": {nom_a: a["armes_deviees"], nom_b: b["armes_deviees"]},
        "interrompt_regen": {nom_a: a["interrompt"], nom_b: b["interrompt"]},
        "regen": {nom_a: a["regen"], nom_b: b["regen"]},
        "profils": {nom_a: a["profil"], nom_b: b["profil"]},
        "graine": graine,
    }
