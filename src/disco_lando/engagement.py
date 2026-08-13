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

#: Règle de terrain (utilisateur, 2026-08-13). En deçà de 200 m on se percute,
#: au-delà de 1 500 m on décroche pour régénérer.
DISTANCE_MIN = 200.0
DISTANCE_MAX = 1500.0

#: Au-delà, l'engagement est rompu : le fuyard est hors de portée utile de
#: toutes les armes de chasse du catalogue (`effective_range` médian ~2 000 m).
DISTANCE_ROMPUE = 3000.0

#: Pas de la simulation. À 0,1 s, un projectile à 1 500 m/s parcourt 150 m —
#: assez fin pour que la distance et les fenêtres de tir évoluent proprement.
PAS = 0.1

#: Un combat qui dure plus longtemps n'en est plus un : les deux camps ont eu
#: mille occasions de rompre. Sert de garde contre les boucles infinies quand
#: aucun des deux ne perce et qu'aucun ne peut décrocher.
DUREE_MAX = 600.0

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

#: Le talent, la géométrie des arcs et la latence ne sont dans aucune colonne.
#: Plutôt que de les ignorer, la simulation les tire : un écart-type de 20 %
#: sur le taux de touche, borné, qui est **la** source d'aléa du « X sur 10 ».
BRUIT_DE_TIR = 0.20

#: Un bouclier ne régénère que s'il n'a pas encaissé récemment. Le jeu publie
#: `RegenerationTime` (5,26 s sur les chasseurs testés) ; on retient le délai
#: sans impact au-delà duquel la régénération reprend.
DELAI_REGENERATION = 2.0

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
    orientable = min(1.0, _rotation(ship) / ROTATION_CHASSEUR)
    return principale * orientable


def _surface_apparente(ship: dict[str, Any]) -> float | None:
    """Surface présentée à un tireur, en m².

    `CrossSection` est la bonne donnée — elle décrit la forme et non la boîte
    qui l'entoure : l'Aurora Mk II, en H, n'occupe que 61 % de son gabarit là
    où l'Arrow en occupe 96 %. Deux réserves, mesurées le 2026-08-13 :

    - **son unité n'est pas identifiable.** L'échelle au centième de m² cale
      la médiane à 0,43 de la boîte englobante, ce qui est crédible, mais
      quelques vaisseaux dépassent 1 — impossible pour une section. On s'en
      sert donc surtout par ses **rapports**, qui eux sont homogènes ;
    - `CrossSection.Y` ne correspond à aucune vue (0,05 de `L×l` en médiane) :
      on ne retient que **X** et **Z**, cohérents avec la vue de face et la
      vue de côté.
    """
    x, z = ship.get("cross_x"), ship.get("cross_z")
    if x and z:
        return (x + z) / 2.0 / 100.0
    longueur, largeur, hauteur = (
        ship.get("length"), ship.get("width"), ship.get("height"))
    if all(v for v in (longueur, largeur, hauteur)):
        return REMPLISSAGE_MEDIAN * (largeur * hauteur + longueur * hauteur) / 2.0
    return None


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
        "scm": scm,
        "boost": ship.get("boost_speed") or scm,
        "masse": ship.get("mass"),
        # Ce qui manque se dit, il ne se remplace pas par un défaut muet.
        "sans_dimensions": surface is None,
        "sans_acceleration": not (ship.get("accel_main")
                                  or ship.get("accel_maneuver")),
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
        ecart = 0.5 * (cible.get("acceleration_evasion") or 0.0) * temps_de_vol ** 2
        rayon = cible.get("rayon")
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

    return max(0.0, min(1.0, p_anticipation * p_suivi * avantage_de_position(
        tireur, cible)))


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

def dps_net(camp: dict[str, Any], adverse: dict[str, Any],
            distance: float) -> float:
    """Le DPS qui entame vraiment la cible, régénération déduite.

    C'est **le** chiffre qui décide d'un duel, et il n'existait nulle part :
    un bouclier qui régénère plus vite qu'on ne le vide ne tombe jamais. Deux
    cas, l'un et l'autre publiés :

    - si les impacts atteignent le seuil d'interruption (0,5 % des PV d'un
      générateur), la régénération est suspendue et tout le DPS travaille ;
    - sinon elle continue sous le feu, et il faut la dépasser pour percer.
    """
    brut = dps_effectif(camp["armes"], camp["profil"], adverse["profil"],
                        distance)
    if camp.get("interrompt"):
        return brut
    return max(0.0, brut - (adverse.get("regen") or 0.0))


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
    d = DISTANCE_MIN
    while d <= DISTANCE_MAX + 1e-9:
        score = _avantage(a, b, pv_a, pv_b, d)
        if meilleur is None or score > meilleur:
            meilleure, meilleur = d, score
        d += 50.0
    # Aucune distance ne lui donne l'avantage : il ne campe pas, il décroche.
    if meilleur is None or meilleur <= 0.0:
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
    # Les armes se filtrent une fois pour toutes contre la défense d'en face.
    utiles = armes_passantes(armes, defense_adverse)
    return {
        "nom": nom,
        "armes": utiles,
        "armes_deviees": len(armes) - len(utiles),
        "interrompt": _interrompt_la_regeneration(utiles, defense_adverse),
        "profil": profil,
        "bouclier_max": float(defense.get("shield_hp") or 0.0),
        "regen": float(defense.get("shield_regen") or 0.0),
        "coque": float((defense.get("hull_health") or 0.0)
                       + (defense.get("armor_health") or 0.0)),
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

    Deux conditions, et il fallait les deux :

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
      qui a intérêt à rompre.
    """
    if camp["bouclier_max"] <= 0 or camp["regen"] <= 0:
        return False
    if (camp["profil"].get("boost") or 0) <= (adverse["profil"].get("boost") or 0):
        return False
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


def _un_combat(a: dict[str, Any], b: dict[str, Any],
               alea: random.Random) -> dict[str, Any]:
    """Une passe : distance évolutive, régénération, décrochage."""
    etat = {}
    for camp in (a, b):
        etat[camp["nom"]] = {
            "bouclier": camp["bouclier_max"],
            "coque": camp["coque"],
            "depuis_impact": DELAI_REGENERATION,
            "en_repli": False,
            # Le talent et les arcs ne sont pas publiés : chaque camp tire son
            # facteur une fois par combat, et le garde — un pilote ne change
            # pas d'adresse en cours de duel.
            "adresse": max(0.2, min(1.8, alea.gauss(1.0, BRUIT_DE_TIR))),
        }
    pv_a = a["bouclier_max"] + a["coque"]
    pv_b = b["bouclier_max"] + b["coque"]

    # Les distances de combat ne dépendent pas de l'état : elles se calculent
    # une fois. Les recalculer à chaque pas coûtait une minute par duel pour
    # un résultat identique.
    combat_a, rompt_a = distance_preferee(a, b, pv_a, pv_b)
    combat_b, rompt_b = distance_preferee(b, a, pv_b, pv_a)

    distance = alea.uniform(DISTANCE_MIN * 2, DISTANCE_MAX)
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
        etat_a, etat_b = etat[a["nom"]], etat[b["nom"]]
        voulue_a = (DISTANCE_ROMPUE if rompt_a
                    else DISTANCE_MAX if _souffle(etat_a, a, b, etat_b)
                    else combat_a)
        voulue_b = (DISTANCE_ROMPUE if rompt_b
                    else DISTANCE_MAX if _souffle(etat_b, b, a, etat_a)
                    else combat_b)
        marche = 25.0
        intention_a = (0.0 if abs(voulue_a - distance) < marche
                       else math.copysign(1.0, voulue_a - distance))
        intention_b = (0.0 if abs(voulue_b - distance) < marche
                       else math.copysign(1.0, voulue_b - distance))
        distance += (intention_a * a["profil"]["boost"]
                     + intention_b * b["profil"]["boost"]) * PAS
        distance = max(distance, 50.0)

        # --- l'engagement est-il rompu ?
        #
        # La marche est retranchée du seuil : sans elle, un fuyard voyait son
        # intention retomber à zéro 25 m avant la rupture et restait planté
        # là. Mesuré sur Mustang Gamma / Sabre — 2 978,95 m au bout de dix
        # minutes, un match nul annoncé pour une fuite réussie.
        if distance >= DISTANCE_ROMPUE - marche:
            # Celui qui cherchait à partir y est parvenu. Si les deux
            # renoncent, personne ne poursuit : il n'y a pas de vainqueur.
            fuyard = (a["nom"] if rompt_a and not rompt_b
                      else b["nom"] if rompt_b and not rompt_a else None)
            return {"issue": "fuite" if fuyard else "nul", "fuyard": fuyard,
                    "duree": temps, "distance": distance}

        # --- le tir, dans les deux sens
        for tireur, cible in ((a, b), (b, a)):
            recu = etat[cible["nom"]]
            adresse = etat[tireur["nom"]]["adresse"]
            touche = False
            for arme in tireur["armes"]:
                porte = ((arme.get("dps") or 0.0) * adresse
                         * taux_de_touche(arme, tireur["profil"],
                                          cible["profil"], distance) * PAS)
                if porte <= 0:
                    continue
                touche = True
                if recu["bouclier"] > 0 and arme.get("type") == "physical":
                    # Le balistique **traverse** : le bouclier n'en absorbe
                    # que 45 %, et encore avec 25 % de résistance.
                    au_bouclier = porte * ABSORPTION_PHYSIQUE * (
                        1 - RESISTANCE_PHYSIQUE)
                    a_la_coque = porte * (1 - ABSORPTION_PHYSIQUE)
                    pris = min(recu["bouclier"], au_bouclier)
                    recu["bouclier"] -= pris
                    recu["coque"] -= a_la_coque
                    continue
                pris = min(recu["bouclier"], porte)
                recu["bouclier"] -= pris
                recu["coque"] -= porte - pris
            # Un bouclier ne cesse de régénérer que s'il est **secoué** : des
            # impacts sous le seuil le grattent sans suspendre sa recharge.
            if touche and tireur["interrompt"]:
                recu["depuis_impact"] = 0.0

        # --- la régénération, seulement hors du feu qui secoue
        for camp in (a, b):
            e = etat[camp["nom"]]
            e["depuis_impact"] += PAS
            if e["depuis_impact"] >= DELAI_REGENERATION and e["bouclier"] < camp["bouclier_max"]:
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

    return {"issue": "nul", "duree": temps, "distance": distance}


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
