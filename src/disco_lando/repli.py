"""Ce qu'on sait dire sur ce qui a été nommé.

Sprint 40, US-5. Demande de l'utilisateur (2026-08-16) : « tu peux toujours
proposer selon quel axe plutôt que d'être silencieux, d'ailleurs tu ne dois
plus être silencieux sauf si tu détectes qu'on ne te parle pas ».

## Ce que la règle corrige

Le banc adversarial a laissé trois tournures muettes — « y a-t-il mieux que
X », « est-ce que X vaut le coup », « X ou son concurrent » — au motif que
**« la meilleure n'existe pas sans critère »**. La règle est juste, et je
l'avais mal appliquée : elle interdit de **choisir l'axe à la place du
joueur**, pas de répondre. Proposer les axes n'est pas répondre à côté,
c'est rendre la question posable.

Le silence garde exactement un domaine : **quand on ne parle pas à Chris**.
`meta.py` le traite déjà — l'aparté, le bavardage d'atelier, la colère, le
hors-univers. Ce module prend tout le reste, c'est-à-dire les questions
adressées au bot qu'aucun outil n'a su router.

## Pourquoi les axes sont mesurés et non listés

Proposer « combien coûte le Bengal » à qui vient de le nommer serait une
proposition **inexécutable** : aucun relevé UEX ne le cote, et le « oui »
suivant tomberait dans le vide. C'est la règle « une proposition annoncée
doit être exécutable », payée deux fois au journal.

Chaque axe porte donc une **sonde** : une requête d'existence sur l'entité
résolue. On ne propose que ce dont on a la donnée. Les axes sans sonde
(`None`) sont ceux que la résolution suffit à garantir — la fiche d'un
vaisseau qui vient de résoudre en `ship` existe par construction.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from decimal import Decimal
from typing import Any

from .normalize import MOTS_GRAMMATICAUX, normalize
from .router.analyse import extract_entity

#: Sous ce score, le terme n'est pas assez sûr pour qu'on bâtisse une
#: proposition dessus : mieux vaut la question générique que trois axes sur
#: la mauvaise entité. C'est le seuil d'entité du routeur, pas un réglage
#: neuf — un repli qui accepterait plus large que le routeur proposerait
#: précisément ce que le routeur a refusé de croire.
SCORE_MINIMUM = 78.0


@dataclasses.dataclass(frozen=True)
class Axe:
    """Une question qu'on sait servir sur cette entité.

    `question` est un gabarit **exécutable** : il passe par le routeur comme
    s'il avait été tapé. Le test du cahier le vérifie pour chacun — une
    proposition qu'on ne sait pas relire est une impasse polie.
    """

    libelle: str
    question: str
    #: `SELECT 1 … WHERE …` prenant le nom de l'entité. `None` = garanti par
    #: la résolution elle-même.
    sonde: str | None = None


#: **Ce qu'on sait dire, par type d'entité.** Écrit depuis les outils qui
#: existent, et vérifié par le cahier : chaque `question` doit router.
_AXES: dict[str, tuple[Axe, ...]] = {
    "ship": (
        Axe("sa fiche", "la fiche du {nom}"),
        Axe("son armement", "l'armement du {nom}",
            "SELECT 1 FROM ship_armes a JOIN ships s ON s.uuid = a.ship_uuid "
            "WHERE s.name = ? LIMIT 1"),
        Axe("ses composants", "les composants du {nom}"),
        Axe("son prix", "combien coûte un {nom}",
            "SELECT 1 FROM uex_prices WHERE name = ? LIMIT 1"),
    ),
    "item": (
        Axe("ses caractéristiques", "les stats du {nom}",
            "SELECT 1 FROM item_stats st JOIN items i ON i.uuid = st.item_uuid "
            "WHERE i.name = ? LIMIT 1"),
        Axe("sa fiche", "c'est quoi le {nom}"),
        Axe("son prix", "combien coûte un {nom}",
            "SELECT 1 FROM uex_prices WHERE name = ? LIMIT 1"),
        Axe("où l'acheter", "où acheter un {nom}",
            "SELECT 1 FROM uex_prices WHERE name = ? AND price_buy > 0 LIMIT 1"),
        Axe("comment le fabriquer", "le blueprint du {nom}",
            "SELECT 1 FROM blueprints WHERE output_name = ? LIMIT 1"),
    ),
    "starmap": (
        Axe("où c'est", "c'est où {nom}"),
        Axe("ce qu'il y a sur place", "qu'est-ce qu'il y a à {nom}"),
        Axe("comment y aller", "comment aller à {nom}"),
    ),
    "contract": (
        Axe("ce que c'est", "c'est quoi la mission {nom}"),
        Axe("où la prendre", "où prendre la mission {nom}"),
        Axe("ce qu'elle paye", "combien paye la mission {nom}",
            "SELECT 1 FROM contracts WHERE title = ? "
            "AND reward_uec IS NOT NULL LIMIT 1"),
    ),
    "resource": (
        Axe("où en trouver", "où trouver du {nom}"),
        Axe("où en miner", "où miner du {nom}"),
        Axe("où le raffiner", "où raffiner du {nom}"),
        Axe("son prix", "le prix du {nom}",
            "SELECT 1 FROM uex_prices WHERE name = ? LIMIT 1"),
    ),
    "blueprint": (
        Axe("sa recette", "la recette du {nom}"),
        Axe("où le débloquer", "quelles missions donnent le blueprint du {nom}"),
    ),
}

# Une commodité et un gisement se demandent pareil — c'est le même minerai,
# indexé deux fois. Les axes se partagent plutôt que de se recopier : deux
# listes jumelles finissent par diverger, et la divergence tient alors lieu
# de règle (leçon du motif recopié à côté de son vocabulaire).
_AXES["commodity"] = _AXES["resource"]

@dataclasses.dataclass(frozen=True)
class Comparaison:
    """Un axe de « mieux que », **ancré sur la valeur de l'entité**.

    Premier jet, corrigé par le cahier : je proposais « quelle arme a le
    plus de DPS », qui ne route pas — et n'aurait de toute façon été qu'un
    menu. La bonne question était sous la main : le P8-AR fait 168 DPS,
    donc « y a-t-il mieux » **est** « quelles armes FPS font plus de 168
    dps ». Le seuil sort de la base, pas de mon imagination — §7 — et le
    joueur reçoit une question complète au lieu d'un intitulé.

    `{famille}` est le libellé du vocabulaire fermé auquel l'entité
    appartient ; il n'est rempli que pour les objets, dont le classement se
    fait à l'intérieur d'un rayon.
    """

    libelle: str
    mesure: str          # SQL rendant un seul nombre, prenant le nom
    question: str        # gabarit portant {valeur} et parfois {famille}


#: **« Mieux » demande un axe de classement, pas une fiche.** Les questions
#: d'opinion — « y a-t-il mieux que X », « X vaut le coup ? » — nomment une
#: entité mais réclament une comparaison. Répondre par sa fiche serait une
#: esquive ; répondre « le meilleur, c'est Y » serait choisir l'axe à la
#: place du joueur.
#:
#: Une mesure nulle ou absente **retire** son axe : le Gladius a 0 SCU, et
#: « quels vaisseaux ont plus de 0 SCU » n'est pas une question. C'est la
#: discipline du « prix de 0 n'est pas un prix ».
_CLASSEMENTS: dict[str, tuple[Comparaison, ...]] = {
    "ship": (
        Comparaison("les dégâts",
                    "SELECT pilot_dps FROM ships WHERE name = ?",
                    "quels vaisseaux ont plus de {valeur} de DPS"),
        Comparaison("le bouclier",
                    "SELECT shield_hp FROM ships WHERE name = ?",
                    "quels vaisseaux ont plus de {valeur} de bouclier"),
        Comparaison("la vitesse",
                    "SELECT max_speed FROM ships WHERE name = ?",
                    "quels vaisseaux ont plus de {valeur} de vitesse"),
        Comparaison("la soute",
                    "SELECT cargo_scu FROM ships WHERE name = ?",
                    "quels vaisseaux ont plus de {valeur} SCU"),
    ),
    "item": (
        Comparaison("les dégâts",
                    "SELECT st.dps FROM item_stats st JOIN items i "
                    "ON i.uuid = st.item_uuid WHERE i.name = ?",
                    "quelles {famille} font plus de {valeur} dps"),
        Comparaison("la portée",
                    "SELECT st.effective_range FROM item_stats st JOIN items i "
                    "ON i.uuid = st.item_uuid WHERE i.name = ?",
                    "quelles {famille} ont plus de {valeur} de portée"),
    ),
}

#: Les tournures d'opinion. Elles ne servent qu'**ici** — le routeur ne les
#: connaît pas, et c'est voulu : aucune ne désigne un outil.
_COMPARAISON = re.compile(
    r"\bmieux que\b|\bmeilleur que\b|\bvaut le coup\b|\bvaut il le coup\b|"
    r"\bca vaut\b|\bson concurrent\b|\bses concurrents\b|\bequivalent\b|"
    r"\bcomparable\b|\bplutot que\b|\ba la place\b")

#: L'ordre dans lequel on interroge le résolveur — il départage à couverture
#: égale. Deux choix mesurés en l'écrivant :
#:
#: - **`item` passe avant `blueprint`.** « P8-AR Rifle » résout à 100 dans les
#:   deux, et le nom nu d'une arme désigne l'arme — c'est la règle donnée par
#:   l'utilisateur le 2026-08-06. La recette reste accessible : elle est un
#:   **axe** de l'objet, pas un type concurrent ;
#: - **`commodity` passe avant `resource`.** « quantainium » sort *Quantainium*
#:   à 100 côté commodité et *MineableRock_SurfaceLegendary_Quantainium* à 90
#:   côté ressource. Le second est un nom de classe : l'afficher est laid, le
#:   mettre dans une proposition la rend **inexécutable**.
_TYPES = ("ship", "starmap", "contract", "commodity", "resource", "item",
          "blueprint")


def _existe(con: sqlite3.Connection, sonde: str | None, nom: str) -> bool:
    """La donnée est-elle là ? Une proposition sans donnée est une impasse."""
    if sonde is None:
        return True
    try:
        return con.execute(sonde, (nom,)).fetchone() is not None
    except sqlite3.Error:
        # Une table absente (base partielle, schéma en cours de migration)
        # ne doit pas faire tomber le repli : on s'abstient de proposer cet
        # axe, ce qui est exactement ce que la sonde mesure.
        return False


#: L'élision devant voyelle. « du Aegis Gladius » n'est pas du français, et
#: c'est le même défaut que l'article doublé des jetons de gabarit — une
#: proposition mal écrite se lit comme un bug, quand bien même elle marche.
_ELISIONS = (("du ", "de l'"), ("le ", "l'"), ("un ", "un "), ("au ", "à l'"))


def _phrase(gabarit: str, nom: str) -> str:
    """Le gabarit rempli, avec l'article que le nom impose.

    On n'élide que devant une voyelle, et on laisse « un » tranquille : « un
    Aegis Gladius » est correct. Le « h » n'est pas traité — aucun nom du
    catalogue ne commence par un h muet français, ce sont des noms propres
    anglais.
    """
    if not nom[:1].lower() in "aeiouy":
        return gabarit.format(nom=nom)
    for avant, apres in _ELISIONS:
        marqueur = avant + "{nom}"
        if marqueur in gabarit:
            return gabarit.replace(marqueur, apres + "{nom}").format(nom=nom)
    return gabarit.format(nom=nom)


def _entite(con: sqlite3.Connection,
            question: str) -> tuple[str, str] | None:
    """Ce que la question a nommé, et de quelle sorte.

    **Un fragment expliqué bat le score, et il bat aussi l'ordre des
    types.** Mesuré en écrivant ce module : « y a-t-il mieux que le P8-AR
    Rifle » retenait le seul mot « rifle » et sortait la ressource *weapon
    rack 1 volt rifle common 001* — un décor de niveau — parce que
    `resource` passe avant `item` dans l'ordre de préférence. « rifle » est
    pourtant *entièrement expliqué* par ce nom-là : l'explication seule ne
    suffit donc pas, il faut aussi regarder **combien de la question** le
    gramme retenu couvre. « p8 ar rifle » en couvre trois mots, « rifle »
    un seul.

    L'ordre des types ne disparaît pas — il départage à couverture égale,
    et c'est lui qui fait gagner le vaisseau contre le bibelot « Gladius
    Model ».
    """
    from .resolver import mots_inexpliques, resolve

    # **Une seule passe de résolution, pas une par type.** Le premier jet en
    # faisait sept — `extract_entity` soumet tous les n-grammes au résolveur,
    # et sept fois de suite coûtait 4,7 à 6,4 s sur la machine. Le repli est
    # un chemin d'échec, pas une excuse pour faire attendre : on extrait une
    # fois sur l'union des types, puis on trie les candidats.
    extrait = extract_entity(con, question, _TYPES)
    if extrait is None:
        return None
    terme, score = extrait
    if score < SCORE_MINIMUM:
        return None

    # **Un mot sur sept ne nomme pas le sujet d'une phrase.** Rejeu du
    # journal, 2026-08-21 : « il doit vérifier si la question a une réponse
    # **possible** sinon il coupe direct » — une remarque sur le
    # fonctionnement du bot — ressortait en propositions sur la mission
    # *Patrol to Prevent **Possible** Attack*. Le terme extrait est juste,
    # les contrôles d'explication et de frontière passent : c'est bien un
    # mot entier du nom. Ce qui cloche est la **proportion** — un seul mot
    # utile de la phrase sur sept a servi à choisir.
    #
    # Le seuil est bas (un tiers) parce que les vraies questions sont
    # courtes et pleines de grammaire : « où trouver du quantainium » a deux
    # mots utiles dont un nomme. C'est la phrase longue qui ne parle pas du
    # jeu qu'on écarte, et c'est la plus nuisible — elle rend une fiche
    # chiffrée qui a l'air d'une réponse.
    utiles = [m for m in normalize(question).split()
              if m not in MOTS_GRAMMATICAUX]
    nommants = set(normalize(terme).split())
    if len(utiles) >= 4 and len([m for m in utiles if m in nommants]) * 3 < len(utiles):
        return None

    # **La fenêtre doit être large.** « Gladius » sort six livrées à 93 —
    # des `item`, donc des peintures — devant l'*Aegis Gladius* à 90, et une
    # fenêtre de huit candidats ne laisse même pas le vaisseau entrer en
    # lice. Mesuré à nouveau ici : le repli rendait « Gladius Dunlevy » et
    # sa seule fiche. C'est le même piège que dans `decrire`, et la même
    # parade — l'ordre des types prime, encore faut-il que le vaisseau soit
    # dans la liste.
    res = resolve(con, terme, entity_types=_TYPES, limit=40)
    meilleurs: list[tuple[int, str, str]] = []
    for candidat in res.candidates:
        if candidat.entity_type not in _AXES:
            continue
        # L'explication tranche là où le score ne sépare rien — c'est la
        # leçon d'« omnisky xi », qui sort à 90 en étant faux.
        if mots_inexpliques(terme, candidat.name):
            continue
        if not _sur_frontiere(terme, candidat):
            continue
        nom = _nom_lisible(candidat)
        if nom is None:
            continue
        meilleurs.append((_TYPES.index(candidat.entity_type),
                          candidat.entity_type, nom))
    if not meilleurs:
        return None
    # L'ordre des types tranche, comme dans `decrire` : le score dit à quel
    # point le nom colle, pas quelle **sorte** de chose on cherche.
    _rang, entity_type, nom = min(meilleurs)
    return entity_type, nom


def _sur_frontiere(terme: str, candidat: Any) -> bool:
    """Chaque mot tapé retrouve-t-il un **mot entier** du nom trouvé ?

    Le piège que ce contrôle referme est déjà au dossier du projet : « il
    fait beau » se réduit à « beau », qui résout *CRU-L5 **Beau**tiful Glen
    Clinic* à 90 par FTS — et `mots_inexpliques` ne le rattrape pas, « beau »
    étant un préfixe de « beautiful ». La phrase la plus hors sujet du cahier
    ressortait donc en trois propositions sur une clinique. C'est la règle
    posée pour l'étage `flat` — « un préfixe ne vaut son score que s'il
    s'arrête sur une frontière de mot » — qu'il fallait appliquer ici aussi.

    On compare au nom **et** à l'alias : « cutty » ne se retrouve pas dans
    « Drake Cutlass Black », mais c'est un alias à part entière et il
    désigne bien ce vaisseau.
    """
    mots_du_terme = set(normalize(terme).split())
    connus = set(normalize(candidat.name).split())
    connus |= set(normalize(candidat.alias or "").split())
    return mots_du_terme <= connus


def _nom_lisible(candidat: Any) -> str | None:
    """Le nom qu'on peut montrer — et **retaper**. Sinon rien.

    Les gisements portent leur nom de classe :
    *MineableRock_SurfaceLegendary_Quantainium*. L'afficher est laid ; le
    mettre dans une proposition la rend **inexécutable**, ce qui est le
    défaut que ce module est censé corriger. On essaie l'alias qui a matché
    — c'est par construction ce que le joueur a écrit — et on **renonce à ce
    type** si lui aussi est une clé technique. Un type de moins vaut mieux
    qu'une proposition qu'on ne peut pas taper.
    """
    for nom in (candidat.name, candidat.alias):
        if nom and not ("_" in nom and " " not in nom):
            return nom
    return None


def proposer(con: sqlite3.Connection, question: str) -> dict[str, Any] | None:
    """Les axes qu'on sait servir sur ce que la question a nommé.

    Rend `None` quand rien ne résout : là, « je n'ai pas compris » est la
    réponse honnête, et la question part au journal comme avant.
    """
    trouve = _entite(con, question)
    if trouve is None:
        return None

    entity_type, nom = trouve
    proposes: list[dict[str, str]] = []
    # **Un lieu ne se classe pas.** Sans ce contrôle, « est-ce que Orison
    # vaut le coup » annonçait « je sais classer sur » puis listait « c'est
    # où Orison » : deux phrases qui se contredisent valent moins qu'une.
    veut_comparer = (bool(_COMPARAISON.search(normalize(question)))
                     and entity_type in _CLASSEMENTS)
    # Le joueur a-t-il demandé une comparaison, même si on ne sait pas la
    # servir ? Le distinguer change la phrase : « je n'ai pas saisi ta
    # question » est faux quand elle était limpide et que c'est **nous** qui
    # ne savons pas classer cette famille.
    demande_a_comparer = veut_comparer
    if veut_comparer:
        proposes = _axes_de_comparaison(con, entity_type, nom)
        # Une entité sans aucune mesure ne se compare pas ; on retombe alors
        # sur ce qu'on sait en dire, plutôt que de rendre une liste vide.
        veut_comparer = bool(proposes)
    if not proposes:
        proposes = [
            {"libelle": axe.libelle, "question": _phrase(axe.question, nom)}
            for axe in _AXES.get(entity_type) or ()
            if _existe(con, axe.sonde, nom)
        ]
    if not proposes:
        return None
    return {
        "entite": nom, "entity_type": entity_type,
        "comparaison": veut_comparer,
        "comparaison_impossible": demande_a_comparer and not veut_comparer,
        "axes": proposes, "resolution": None,
    }


def _axes_de_comparaison(con: sqlite3.Connection, entity_type: str,
                         nom: str) -> list[dict[str, str]]:
    """« Mieux que X » devient « plus de <ce que X vaut> »."""
    famille = _famille_de(con, nom) if entity_type == "item" else None
    if entity_type == "item" and famille is None:
        # Sans famille, le classement porterait sur tout le catalogue et
        # rendrait le canon du Bengal — le piège du Destroyer Mass Driver.
        return []
    axes: list[dict[str, str]] = []
    for axe in _CLASSEMENTS[entity_type]:
        try:
            ligne = con.execute(axe.mesure, (nom,)).fetchone()
        except sqlite3.Error:
            continue
        valeur = ligne[0] if ligne else None
        # `0` est une absence ici, pas une mesure : « plus de 0 SCU » rendrait
        # la flotte entière sous couvert de répondre à la question.
        if not valeur:
            continue
        phrase = axe.question.format(valeur=_seuil(valeur), famille=famille)
        if not _repond(con, phrase):
            continue
        axes.append({"libelle": axe.libelle, "question": phrase})
    return axes


def _repond(con: sqlite3.Connection, phrase: str) -> bool:
    """La proposition va-t-elle **jusqu'au bout** ?

    Router ne suffit pas : `objets_au_seuil` route parfaitement « quelles
    armes FPS font plus de 176 dps » et ne rend rien, faute de pouvoir
    classer les armes de poing (voir sa docstring — `MOUNTABLE` les exclut
    toutes). Proposer cette question serait une impasse polie, c'est-à-dire
    le défaut même que ce module corrige.

    On paie donc l'exécution. C'est un chemin déjà en échec — le routeur a
    renoncé, l'analyste aussi — et quelques millisecondes y valent mieux
    qu'une proposition morte. Le contrôle est **mécanique** : le jour où un
    outil gagne une population, l'axe réapparaît sans qu'on y touche.
    """
    from . import router
    from .router.deterministic import route

    appel = route(con, phrase)
    if appel is None:
        return False
    try:
        router.execute(con, appel)
    except Exception:                                         # noqa: BLE001
        return False
    return True


def _seuil(valeur: float) -> str:
    """Conserve la précision publiée dans une phrase relue par le routeur.

    Arrondir 1 597,9 à 1 598 avant « plus de » déplaçait la frontière et
    pouvait exclure une valeur pourtant supérieure à l'entité comparée. Le
    format décimal évite aussi la notation scientifique ; la virgule reste
    comprise par `detect_seuil`.
    """
    decimal = Decimal(str(valeur))
    return format(decimal.normalize(), "f").replace(".", ",")


def _famille_de(con: sqlite3.Connection, nom: str) -> str | None:
    """Le rayon auquel l'objet appartient, dans le vocabulaire fermé.

    On interroge les clauses de `_FAMILLES_D_OBJETS` plutôt que de recopier
    une correspondance : deux listes jumelles finissent par diverger, et
    c'est la divergence qui se met alors à tenir lieu de règle.

    La **dernière** famille qui répond gagne : la liste va du type large
    (« armes FPS ») au rayon précis (« snipers »), et le classement le plus
    utile est le plus étroit.
    """
    from .armurerie import _FAMILLES_D_OBJETS

    trouvee: str | None = None
    for libelle, _motif, clause, _mode in _FAMILLES_D_OBJETS:
        try:
            ligne = con.execute(
                f"SELECT 1 FROM items WHERE name = ? AND ({clause}) LIMIT 1",
                (nom,)).fetchone()
        except sqlite3.Error:
            continue
        if ligne is not None:
            trouvee = libelle
    return trouvee
