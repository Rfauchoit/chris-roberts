"""Lire la question : n-grammes, extraction d'entité, lieux nommés,
détections de volet et de portée."""

from __future__ import annotations

import re
import sqlite3

from .. import queries
from ..normalize import MOTS_GRAMMATICAUX, normalize
from ..resolver import mots_inexpliques, resolve
from .motifs import _INTENTS, _INTENT_WORDS

# Mots vides français + verbes de question. Retirés avant de fabriquer les
# n-grammes, sinon « le gladius » et « du quantanium » polluent la résolution.
# Ce dont `missions_payantes` sait parler. Sans un de ces mots, et sans
# organisation ni lieu à filtrer, la question ne le concerne pas.
_VOCABULAIRE_PAYE = re.compile(
    r"\bpay\w+|\brapport\w+|\brentab\w+|\bgagn\w+|\bremuner\w+"
    r"|\breputation\b|\breput\b|\bstanding\b|\bmieux\b|\bplus\b"
    r"|\buec\b|\bauec\b|\bargent\b|\bcredits?\b|\bfarm\w*"
    r"|\bdisponibles?\b|\bdispos?\b|\bproposees?\b|\bil y a\b")


#: « Sur 10 combats », « combien de fois sur dix ». Les lettres comptent
#: autant que les chiffres : c'est la forme parlée la plus courante, et le
#: joueur écrit comme il parle.
_COMPTES_EN_LETTRES = {
    "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10, "quinze": 15, "vingt": 20, "cent": 100,
}
_NOMBRE_DE_COMBATS = re.compile(
    r"\b(?:sur|en|de)\s+(\d+|" + "|".join(_COMPTES_EN_LETTRES) + r")\s+"
    r"(?:combats?|duels?|affrontements?|matchs?|fois)\b"
    r"|\bfois\s+sur\s+(\d+|" + "|".join(_COMPTES_EN_LETTRES) + r")\b")

#: Dix par défaut : c'est le nombre que le joueur a employé les deux fois au
#: journal, et il se lit sans effort (« 7 fois sur 10 »).
COMBATS_PAR_DEFAUT = 10


def detect_nombre_de_combats(question: str) -> int:
    """Combien de combats la question demande de simuler.

    Borné à 50 comme la fonction métier : au-delà, le compte n'apprend plus
    rien de neuf et la réponse se met à coûter des secondes.
    """
    trouve = _NOMBRE_DE_COMBATS.search(normalize(question))
    if trouve is None:
        return COMBATS_PAR_DEFAUT
    brut = trouve.group(1) or trouve.group(2)
    valeur = _COMPTES_EN_LETTRES.get(brut) or int(brut)
    return min(max(valeur, 1), 50)


def _nomme_une_org(con: sqlite3.Connection, question: str) -> bool:
    """La question désigne-t-elle une organisation à filtrer."""
    trouve = resolve(con, question, entity_types=("org",), limit=1).best
    return trouve is not None and trouve.score >= 85.0


# Construit à partir de MOTS_GRAMMATICAUX (normalize.py) — la liste
# canonique — avec les deux verbes de question propres au routeur.
# « or » reste exclu : c'est aussi le métal Gold, et le retirer des
# n-grammes empêcherait « où trouver de l'or » de résoudre l'entité.
# « jusque » reste après le motif « je peux aller » et ne nomme évidemment
# aucun lieu. Sa faute réelle ``jsuque`` figure sept fois dans le journal : la
# conserver faisait douter de microTech malgré trois entités exactes.
# Les verbes de liste sont eux aussi purement directifs. « Liste-moi tous les
# points de vente » résolvait `liste` en *Lister Surgical Shoes* à 90 avant
# que la mémoire puisse reprendre le P4-AR du tour précédent.
STOPWORDS = (MOTS_GRAMMATICAUX | {
    "besoin", "partir", "jusque", "jsuque", "liste", "lister", "listez",
}) - {"or"}

# Les seuls mots qu'on retire d'une phrase qu'on soupçonne d'être un simple
# nom d'entité. Volontairement **beaucoup** plus court que `STOPWORDS` :
# retirer les verbes creux réduirait « il fait beau » à « beau », qui résout.
_DETERMINANTS = {"le", "la", "les", "l", "un", "une", "des", "du", "de", "d"}
MIN_ENTITY_SCORE = 78.0
# Longueur maximale d'un n-gramme candidat, en mots.
#
# **Porté de 4 à 6 le 2026-08-06, sur mesure.** Les titres de mission font
# couramment six à dix mots : « live and let an independent contractor deal out
# revenge » était tronqué en « independent contractor deal out », qui résout le
# lieu *Goner's Deal* — la question partait sur la carte au lieu du contrat.
#
# Mesuré au banc sur 161 questions : 116/116 justes et 13 paires fragiles aux
# trois valeurs testées (4, 6, 8), donc **aucune régression**. Le coût est de
# 10 % de latence (90 → 99 ms par question) contre 30 % à 8. Et 6 suffit :
# sur huit titres longs, 7/8 étaient reconnus à 4, 8/8 à 6, et 8 n'apporte
# rien de plus.
MAX_NGRAM = 6


def _ngrams(question: str) -> list[str]:
    """N-grammes candidats pour l'entité, du plus long au plus court.

    Le plus long d'abord : « cutlass black » doit être tenté avant « cutlass »,
    sinon on résout le mauvais vaisseau de la gamme.
    """
    words = [
        w for w in _INTENT_WORDS.sub(" ", normalize(question)).split()
        if w not in STOPWORDS and len(w) > 1
    ]
    grams: list[str] = []
    for size in range(min(MAX_NGRAM, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            grams.append(" ".join(words[start:start + size]))
    return grams


def _mots_d_intention(tool_name: str | None, question: str) -> set[str]:
    """Mots de la question qui portent l'intention de cet outil.

    Ils doivent être exclus de la recherche d'entité. Le résolveur est
    phonétique et généreux : livré à lui-même, il apparie le mot d'intention
    avec un nom du jeu. Mesuré — « combien coûte un Gladius » retenait
    **« coute »** comme entité, que le résolveur rendait en « Drake Cutlass
    Black » à 128,8 de score, très au-dessus du vrai Gladius. La réponse
    donnait le prix du Cutlass, avec aplomb.
    """
    norm = normalize(question)
    mots: set[str] = set()
    # `None` = tous les outils. Un mot qui porte l'intention de *n'importe
    # quel* outil dans cette question n'est pas un nom d'entité : « coûte »
    # n'était retiré que pour `get_price`, si bien que `get_ship_components`
    # le résolvait encore — en « Drake Cutlass Black », à 129 de score.
    motifs = (_INTENTS.get(tool_name, ()) if tool_name
              else [m for liste in _INTENTS.values() for m in liste])
    for motif, _ in motifs:
        for trouve in re.finditer(motif, norm):
            mots.update(trouve.group(0).split())
    return mots


def sans_les_mots_d_un_nom(con: sqlite3.Connection, question: str,
                           exclus: set[str]) -> set[str]:
    """Rend `exclus` privé des mots qui composent un nom du catalogue.

    **Un mot d'intention peut être la moitié d'un nom propre.** Mesuré sur
    le catalogue : **1 176 objets** portent dans leur nom un mot qui sert
    aussi de famille — 677 casques, 181 missiles, 154 combinaisons, 133
    sacs à dos. « Les stats du Ready-Up **Helmet** » perdait « helmet » et
    cherchait *Ready-Up* ; « les stats du Novikov **Backpack** » cherchait
    *Novikov* et rendait le **casque** Novikov, à 93 comme le sac.

    Le critère n'est pas le mot mais la **fenêtre** : on ne réhabilite un
    mot que s'il appartient à une suite de mots qui est, telle quelle, un
    alias exact du catalogue. « Quel casque sous 2 000 aUEC » garde donc son
    « casque » exclu — aucun objet ne s'appelle « quel casque » — tandis que
    « Novikov Backpack » le récupère. C'est le pendant de « un fragment
    expliqué bat le score », un étage plus tôt : avant d'arbitrer entre
    candidats, encore faut-il que le bon gramme puisse se former.

    **Les paires ne suffisent pas.** Mesuré sur 40 armures tirées à graine
    fixe : à deux mots, 13 rendaient encore la mauvaise pièce — « Aves
    Shrike **Helmet** » sortait *Aves Shrike Arms*, parce que l'alias fait
    trois mots et qu'aucune paire ne le porte. On balaie donc jusqu'à cinq,
    la longueur du plus long nom d'armure du catalogue.
    """
    mots = normalize(question).split()
    garde: set[str] = set()
    for taille in range(2, 6):
        for debut in range(len(mots) - taille + 1):
            fenetre = mots[debut:debut + taille]
            if not any(mot in exclus for mot in fenetre):
                continue
            if _alias_exact(con, " ".join(fenetre)):
                garde.update(fenetre)
    return exclus - garde


def _alias_exact(con: sqlite3.Connection, gram: str) -> bool:
    """Ce gramme est-il, tel quel, le nom d'une entité."""
    return bool(con.execute(
        "SELECT 1 FROM aliases WHERE alias_norm = ? LIMIT 1",
        (normalize(gram),)).fetchone())


def extract_entity(
    con: sqlite3.Connection, question: str, entity_types: tuple[str, ...],
    exclus: set[str] | None = None,
) -> tuple[str, float] | None:
    """Meilleure entité du type attendu mentionnée dans la question.

    On ne cherche pas « le mot qui désigne l'entité » — on soumet tous les
    n-grammes au résolveur et on garde le meilleur score. C'est ce qui permet à
    « les emports du gladiousse » de marcher sans qu'aucune règle ne connaisse
    « gladiousse ».

    `exclus` retire les mots qui portent l'intention : eux aussi ressemblent à
    des noms du jeu pour un résolveur phonétique.
    """
    exclus = exclus or set()
    best: tuple[str, float] | None = None
    retenus: list[tuple[str, float, str]] = []
    for gram in _ngrams(question):
        # Le mot d'intention est retiré **à l'intérieur** du n-gramme, pas
        # seulement quand il en constitue la totalité. Écarter le seul gramme
        # « coute » ne suffisait pas : « coute gladius » l'emportait ensuite à
        # 119 sur « gladius » à 90, et rendait toujours le Cutlass. C'est le
        # mot lui-même qui empoisonne, où qu'il se trouve.
        if exclus:
            gram = " ".join(m for m in gram.split() if normalize(m) not in exclus)
            if not gram.strip():
                continue
        # Un jeton de moins de trois caractères ne porte pas d'identité, mais
        # le résolveur phonétique lui trouve quand même un vaisseau : « gt »,
        # extrait de « Tarantula GT-870 », rendait « Drake Cutlass Black » à
        # 129 de score et volait la question.
        # **Sauf s'il est un alias exact.** Mesuré : la base ne compte que
        # quatre alias de moins de trois caractères, dont « or » — le métal,
        # que l'utilisateur a demandé et qui répondait sur le Fer. « gt », lui,
        # n'est l'alias exact de rien : le garde-fou garde tout son effet là où
        # il a été écrit.
        if len(gram.replace(" ", "")) < 3 and not _alias_exact(con, gram):
            continue
        # **Un nombre nu n'est pas une entité.** Mesuré : « 100 » sort
        # *Origin 100i* à 90, « 500 » sort *JS-500* à 90, « 300 » sort
        # *Origin 300i* à 90. Toute question à seuil était donc fausse —
        # « quels vaisseaux ont plus de 100 SCU » répondait l'Origin 100i,
        # qui en a 2. Un chiffre ne devient un nom qu'accompagné : « 890 jump »
        # et « 300i » restent des grammes valides, « 890 » seul non.
        if gram.replace(" ", "").isdigit():
            continue
        res = resolve(con, gram, entity_types=entity_types, limit=1)
        if res.best is None:
            continue
        score = res.best.score
        # Un n-gramme plus long qui atteint le même score est plus informatif :
        # il explique une plus grande part de la question. Le commentaire disait
        # déjà cette règle, le code ne l'appliquait pas — `>` strict laissait
        # gagner le premier gramme rencontré. Mesuré : « la vitesse max du 890
        # jump » donnait « max » (90, un mot) avant « 890 jump » (90, deux
        # mots), et répondait *MISC Freelancer MAX* sur une question qui nommait
        # explicitement son vaisseau.
        # À égalité de mots, le gramme le plus long en caractères identifie
        # mieux : « max » et « avenger » sortent tous deux à 90, mais « max »
        # n'explique que trois lettres de « MISC Freelancer MAX » là où
        # « avenger » en explique sept de « Aegis Avenger Titan ». Mesuré sur
        # « la vitesse max d'un Avenger », qui partait sur le Freelancer.
        if best is None or (score, len(gram.split()), len(gram)) > (
                best[1], len(best[0].split()), len(best[0])):
            best = (gram, score)
        retenus.append((gram, score, res.best.alias))
    if best is None or best[1] < MIN_ENTITY_SCORE:
        return None

    # **Un gramme contenu dans un autre, et entièrement expliqué par ce qu'il
    # résout, est le bon.** La doctrine dit déjà « un fragment de nom n'est pas
    # une entité », mais elle ne s'appliquait qu'aux vaisseaux nommés, jamais
    # entre grammes — et le score seul se trompe de sens. Mesuré sur une vraie
    # question du journal : « combien d'UEC coûte un Star Runner » retenait
    # **« star »**, qui sort *Star Kitten Mug* à 93, contre « star runner » qui
    # sort le *Mercury Star Runner* à 90. La réponse annonçait un mug à 6 aUEC
    # pour un vaisseau à plusieurs millions.
    #
    # Le critère n'est **pas** l'écart de score : « avenger » sort à 90 et
    # « max avenger » à 85,5, soit moins de cinq points, et pourtant c'est le
    # court qui a raison. Ce qui les sépare est ce que le projet utilise déjà
    # pour la certitude — « star runner » est **entièrement expliqué** par
    # « Crusader Mercury Star Runner », alors que le « max » de « max avenger »
    # ne correspond à rien dans « Aegis Avenger Titan ».
    mots_best = best[0].split()
    for gram, score, alias in retenus:
        autres = gram.split()
        if (len(autres) > len(mots_best) and _contient(autres, mots_best)
                and not mots_inexpliques(gram, alias)):
            best, mots_best = (gram, score), autres
    return best


def _contient(longs: list[str], courts: list[str]) -> bool:
    """`courts` apparaît-il en bloc dans `longs` ?

    En bloc, et non en vrac : « star » est bien un morceau de « star runner »,
    alors que « max avenger » n'est pas un nom parce que ses deux mots ne se
    suivent pas dans la question.
    """
    return any(longs[i:i + len(courts)] == courts
               for i in range(len(longs) - len(courts) + 1))


def _fragment_de_vaisseau(con: sqlite3.Connection, question: str,
                          gram: str) -> bool:
    """Le gramme retenu n'est-il qu'un morceau du nom d'un vaisseau nommé ?

    Mesuré : « la recette d'un Cutlass Black ». Aucun vaisseau n'a de
    blueprint, donc rien ne se résout sur « cutlass black » — mais « black »
    seul sort « BLOC » à 92 par la clé phonétique, et on répondait la recette
    d'un composant que personne n'a demandé.

    Le critère n'est pas « un vaisseau est nommé quelque part » : ça se
    déclenchait sur n'importe quoi, « donnent coda » ressortant *Drake Cutlass
    Black* à 85 par simple partage de jetons. C'est la **comparaison** qui
    tranche — un gramme plus long qui décrit le vaisseau *au moins aussi bien*
    que le fragment prouve que le fragment n'était qu'un morceau de ce nom :

        « black »        95   ⊂  « cutlass black »   95   → débris
        « coda »         92   ⊂  « donnent coda »    85   → entité réelle
    """
    mots = set(normalize(gram).split())
    propre = resolve(con, gram, entity_types=("ship",), limit=1)
    reference = propre.best.score if propre.best is not None else 0.0
    for autre in _ngrams(question):
        autres = set(normalize(autre).split())
        if not mots < autres:
            continue
        res = resolve(con, autre, entity_types=("ship",), limit=1)
        if res.best is not None and res.best.score >= max(85.0, reference):
            return True
    return False


_SYSTEMS = ("stanton", "pyro", "nyx", "terra", "odin", "castra")


def extract_system(question: str) -> str | None:
    """Le système, quand il est nommé — « les missions Foxwell à Pyro »."""
    mots = set(normalize(question).split())
    for systeme in _SYSTEMS:
        if systeme in mots:
            return systeme.capitalize()
    return None


# Composants que le catalogue ne couvre pas encore : `item_stats` ne porte que
# des statistiques d'armes. Une question qui les nomme ne doit pas être servie
# par un outil d'armement — mieux vaut ne pas répondre que répondre à côté.
_HORS_ARMEMENT = (
    "bouclier", "shield", "quantum", "saut", "refroidisseur", "cooler",
    "generateur", "power plant", "centrale", "propulseur", "thruster",
    "reacteur", "radar", "scanner", "mining laser", "tete de minage",
    "vitesse", "scu", "cargo", "equipage", "membres",

)
# « prix », « coûte », « acheter » et « vendre » ont quitté cette liste : depuis
# l'arrivée d'UEX, `get_price` sait y répondre. Ils restaient bloquants tant
# qu'aucun outil ne couvrait les prix.


# C'est le **verbe** qui dit ce qu'on veut savoir. « Où acheter un Coda »
# appelle les points de vente et rien d'autre ; « où fabriquer » appelle la
# recette ; « où trouver » appelle les trois voies, parce que le joueur ne
# présume pas laquelle existe.
#
# Répondre les trois à chaque fois noierait la réponse à une question précise,
# et n'en répondre qu'une à « où trouver » la tronquerait.
_VERBE_ACHAT = re.compile(
    r"\b(?:ach[eè]te?\w*|acquerir|prix|tarif|co[uû]te?\w*|vendeurs?|"
    r"points? de vente|boutiques?|magasins?|lou(?:er|e|ation)\w*)\b"
)
_VERBE_FABRICATION = re.compile(
    r"\b(?:fabriqu\w*|craft\w*|recette|blueprint\w*|construi\w+|"
    r"assembl\w+|ingredient\w*)\b"
)


# Même principe pour un blueprint : « la recette du Coda » et « quelles
# missions donnent le blueprint du Coda » visent le même blueprint et deux
# moitiés opposées de la réponse. Personne ne demande les deux d'un coup.
_VERBE_MISSIONS = re.compile(
    r"\b(?:missions?|contrats?|quetes?|ou (?:je |on |l )*"
    r"(?:obtien\w*|trouve\w*|chopp?e\w*|debloque\w*|recupere\w*|avoir\b)|"
    r"comment (?:je |on |l )*(?:obtien\w*|debloque\w*|avoir\b))"
)

_VERBE_DEMANTELEMENT = re.compile(
    r"\b(?:demantel\w*|demont\w*|recycl\w*)\b"
)

_VERBE_GRIND = re.compile(
    r"\b(?:grind\w*|farm\w*|combien (?:de )?(?:missions?|contrats?))\b|"
    r"\bdepuis (?:le )?(?:rang )?\w+"
)


# « Sa vitesse max », « son DPS », « ses points de vente » : un possessif ne
# nomme rien, il renvoie à ce dont on parlait. Mesuré : après une question sur
# le Gladius, « sa vitesse max » résolvait « max » en *MISC Freelancer MAX* —
# et détournait le sujet pour toutes les questions suivantes.
_ANAPHORE = re.compile(r"\b(?:son|sa|ses|leur|leurs)\b")


def reprend_le_sujet(question: str) -> bool:
    """La question renvoie-t-elle explicitement au sujet précédent ?"""
    return bool(_ANAPHORE.search(normalize(question)))


def detect_volet(question: str) -> str:
    """« missions », « grind », « démantèlement » ou « recette » d'un plan."""
    norm = normalize(question)
    # « Que récupère-t-on en démantelant ? » contient « récupère », qui sert
    # aussi aux sources de mission. Le verbe le plus spécifique doit gagner.
    if _VERBE_DEMANTELEMENT.search(norm):
        return "demantelement"
    if _VERBE_GRIND.search(norm):
        return "grind"
    return "missions" if _VERBE_MISSIONS.search(norm) else "recette"


def detect_portee(question: str) -> str:
    """« achat », « location », « fabrication » ou « tout », selon le verbe."""
    norm = normalize(question)
    achat = bool(_VERBE_ACHAT.search(norm))
    fabrication = bool(_VERBE_FABRICATION.search(norm))
    # « Combien coûte la location d'un Prospector » : le verbe demandait la
    # location et la réponse ouvrait sur le prix d'achat. La location prime
    # sur l'achat quand elle est nommée — on ne loue pas par accident.
    if re.search(r"\blou[ée]?[rs]?\b|\blocation\b", norm) and not fabrication:
        return "location"
    if achat and not fabrication:
        return "achat"
    if fabrication and not achat:
        return "fabrication"
    return "tout"


# « liste-moi tout », « la recette complète », « et les autres ? ». Ces
# tournures ne portent pas d'entité : elles reprennent celle d'avant.
_EXHAUSTIF = re.compile(
    r"\b(?:tous?|toutes?|tout|complet\w*|complete\w*|integral\w*|"
    r"liste[rz]?|detaill?e\w*|entier\w*|"
    # « Décris-moi les missions » demande le contenu, pas un compte : demander
    # confirmation après ça fait perdre un tour pour rien. Le verbe *est* la
    # demande. Remarque du journal.
    r"decri[stvz]\w*|donne moi les|cite[rz]?|enumere\w*|quelles? sont)\b"
)


def veut_tout(question: str) -> bool:
    """La question demande-t-elle l'exhaustivité plutôt qu'un résumé.

    Les réponses sont volontairement courtes — elles sont lues à voix haute.
    Mais « liste-moi **tous** les points de vente » demande explicitement le
    contraire, et tronquer à trois serait désobéir.
    """
    return bool(_EXHAUSTIF.search(normalize(question)))


def _lieux_nommes(con: sqlite3.Connection, question: str) -> list[str]:
    """Les lieux nommés dans la phrase, dans l'ordre et sans doublon.

    « C'est loin, Yela depuis Lorville ? » en contient deux.

    On rend le **nom résolu**, pas le n-gramme brut. Mesuré : « de microTech à
    Ruin Station dans un 890 Jump » produisait le gramme « ruin station 890
    jump », qui recolle le vaisseau au lieu et se résout en *Ruin Clinic*.
    Le gramme brut marchait tant que l'outil le re-résolvait seul ; il ne
    marche plus dès qu'une autre entité de la phrase vient s'y agglutiner.

    **« Dans l'ordre » veut dire dans l'ordre de la phrase**, et ce n'était pas
    le cas : `_ngrams` va du plus long au plus court, si bien que « d'Orison à
    Levski » rendait *Levski* en premier. Le planificateur partait donc de
    l'arrivée — trajet inversé, et personne pour s'en apercevoir puisque la
    réponse reste plausible. On trie sur la position du gramme retenu.
    """
    trouves: list[tuple[int, str, str, float]] = []
    deja: set[str] = set()
    phrase = normalize(question)
    for gram in _ngrams(question):
        # **Un gramme purement numérique ne nomme aucun lieu.** « avec 10 %
        # de carburant » faisait entrer *Cluster NBD-102*, *Shubin SPMC-10*
        # et *SM0-10* dans la liste des étapes d'un trajet à deux lieux :
        # c'est la règle du nombre nu, qui manquait ici. Un gramme
        # accompagné reste un nom — « Area 18 », « Levski 2 ».
        if gram.replace(" ", "").isdigit():
            continue
        res = resolve(con, gram, entity_types=("starmap",), limit=1)
        if res.best and res.best.score >= 85.0 and res.best.entity_id not in deja:
            deja.add(res.best.entity_id)
            # La position du **gramme** ne suffit pas : celui qui résout Yela
            # peut être « distance lorville yela », qui commence au début de
            # la phrase. On se repère sur le premier mot de l'alias retenu,
            # c'est-à-dire sur l'endroit où le lieu est réellement nommé.
            mots_alias = normalize(res.best.alias).split()
            position = phrase.find(mots_alias[0]) if mots_alias else -1
            if position < 0:
                position = phrase.find(gram)
            trouves.append((position if position >= 0 else len(phrase),
                            res.best.name, gram, res.best.score))

    # Le score plancher FTS (85,5) signifie « un mot en commun », pas « ce
    # lieu est nommé ». Dans une longue phrase contenant « station alpha »,
    # il faisait apparaître QV Extraction Station, Ruin Station et RAB-ALPHA
    # en plus de People's Service Station Alpha et microTech. On garde un nom
    # exact, une correspondance franchement supérieure au plancher, ou au
    # moins deux mots du nom réellement présents dans le gramme qui l'a élu.
    # Cela conserve « station alpha » → People's Service Station Alpha sans
    # transformer chaque mot générique en étape de tournée.
    filtres = []
    for position, nom, gram, score in trouves:
        nom_n = normalize(nom)
        mots_communs = set(nom_n.split()) & set(normalize(gram).split())
        explicite = bool(re.search(
            r"(?:^| )" + re.escape(nom_n) + r"(?: |$)", phrase))
        if explicite or score > 90.0 or len(mots_communs) >= 2:
            filtres.append((position, nom))
    lieux = [nom for _, nom in sorted(filtres, key=lambda t: t[0])]

    # **Un nom écrit en entier bat les voisins trouvés sur un seul mot.**
    # « Ruin Station » faisait aussi entrer *Ruin Clinic* par le gramme
    # ``ruin`` ; le trajet inventait alors un départ et n'osait plus demander
    # d'où le joueur partait. Dès qu'un alias complet est présent dans la
    # phrase, les candidats qui partagent son premier mot sans être eux-mêmes
    # écrits sont du bruit de résolution, pas une seconde étape.
    explicites = [
        nom for nom in lieux
        if re.search(r"(?:^| )" + re.escape(normalize(nom)) + r"(?: |$)",
                     phrase)
    ]
    if explicites:
        lieux = [
            nom for nom in lieux
            if nom in explicites or not any(
                normalize(nom).split()[:1] == normalize(exp).split()[:1]
                for exp in explicites)
        ]

    # **Un point de saut qui porte le nom des deux extrémités est la route, pas
    # une étape.** « Comment aller de Nyx à Pyro » retenait *Nyx - Pyro Jump
    # Point* comme premier lieu, et calculait un trajet depuis le saut
    # lui-même. Le planificateur trouve la route tout seul ; ce qu'on lui
    # passe, ce sont les deux bouts.
    autres = [normalize(nom) for nom in lieux]
    lieux = [nom for nom, norme in zip(lieux, autres)
             if sum(1 for a in autres
                    if a != norme and a and a in norme) < 2]

    # **Une dépendance n'est pas une étape de plus.** « L'aller-retour Port
    # Tressler ↔ New Babbage » sortait *Port Tressler* **et** *Port Tressler
    # Clinic* : le trajet partait de la station pour arriver à sa propre
    # clinique, et New Babbage — le vrai but — passait à la trappe. C'est la
    # règle du préfixe déjà appliquée aux variantes de vaisseau : un nom qui
    # prolonge celui d'un lieu déjà retenu désigne un endroit **dans** ce
    # lieu, pas un autre point du trajet. Le premier cité gagne, parce que
    # c'est celui que le joueur a tapé.
    garde: list[str] = []
    for nom in lieux:
        norme = normalize(nom)
        if any(norme.startswith(normalize(g) + " ") for g in garde):
            continue
        garde.append(nom)
    return garde


def mots_d_entite(question: str, gram: str) -> list[str]:
    """Ce qui, dans la question, prétend nommer l'entité — **autour du gramme**.

    Une liste de vocabulaire ne suffisait pas : tout verbe non répertorié
    devenait un « mot d'entité inexpliqué », et « où je choppe le blueprint du
    Devastator Shotgun » doutait à cause de « choppe ».

    Le vrai critère est la contiguïté. Un numéro de modèle **colle** à son nom
    — « Omnisky XI » — alors qu'un verbe en est séparé par des mots vides. On
    part donc du gramme retenu et on l'étend de proche en proche, tant que le
    voisin n'est ni un mot vide, ni un mot d'intention, ni un nom de
    statistique.
    """
    from ..context import _ACQUIESCEMENTS, _LIANTS, _ORDINAUX, _TOUT

    mots = normalize(question).split()
    morceau = normalize(gram).split()
    if not mots or not morceau:
        return []

    place = next((i for i in range(len(mots) - len(morceau) + 1)
                  if mots[i:i + len(morceau)] == morceau), None)
    if place is None:
        return morceau

    exclus = (_mots_d_intention(None, question) | STOPWORDS
              | queries.MOTS_DE_STAT | _ACQUIESCEMENTS | _TOUT | _LIANTS
              | set(_ORDINAUX))
    debut, fin = place, place + len(morceau)
    while debut > 0 and mots[debut - 1] not in exclus:
        debut -= 1
    while fin < len(mots) and mots[fin] not in exclus:
        fin += 1
    return mots[debut:fin]


def _entite_douteuse(con: sqlite3.Connection, gram: str,
                     entity_types: tuple[str, ...], question: str) -> bool:
    """Le terme tapé est-il expliqué par ce que cet outil y résout ?

    Une intention dont l'entité n'est pas fiable est abandonnée au profit de
    la suivante — c'est le mécanisme du §7, appliqué à la certitude et plus
    seulement à l'existence. Mesuré : « décris-moi un Omnisky IX » résolvait
    « omnisky » en *Aegis Gladius* du côté vaisseau, et s'arrêtait là au lieu
    de laisser l'outil des objets répondre.
    """
    from ..resolver import mots_inexpliques

    tapes = mots_d_entite(question, gram)
    # Un compte n'est pas un mot d'entité : « 5 rsi scorpius » jugeait le
    # Scorpius douteux pour un « 5 » que son alias n'explique pas — c'est
    # la règle du nombre nu, appliquée au doute. « 300i » et « 890 jump »
    # ne sont pas concernés, leurs jetons ne sont pas purement numériques.
    tapes = [m for m in tapes if not m.isdigit()]
    if not tapes:
        return False
    res = resolve(con, gram, entity_types=entity_types, limit=1)
    if res.best is None:
        return True
    return bool(mots_inexpliques(" ".join(tapes), res.best.alias))


# « le plus proche de Lorville », « près d'Orison » : la préposition **désigne**
# le lieu de référence. Le deviner parmi tous les lieux résolus de la phrase ne
# marchait pas — « pour un P4-AR » ressortait *Area04*, et on comparait des
# distances depuis un endroit dont personne n'avait parlé.
_PROXIMITE = re.compile(
    r"\b(?:le |au )?plus proche (?:de |du |des |d )|"
    r"\bpres (?:de |du |des |d )|\bproche (?:de |du |des |d )|"
    r"\ba cote (?:de |du |des |d )|\bautour (?:de |du |des |d )"
)


def coupe_sur_proximite(question: str) -> tuple[str, str] | None:
    """Sépare « ce qu'on cherche » de « d'où on part ».

    Rend (avant, apres) autour de la préposition de proximité, ou None si la
    question n'en contient pas — auquel cas ce n'est pas une question de
    proximité et un autre outil répond.
    """
    norm = normalize(question)
    trouve = _PROXIMITE.search(norm)
    if trouve is None:
        return None
    avant, apres = norm[:trouve.start()].strip(), norm[trouve.end():].strip()
    return (avant, apres) if apres else None


def _hors_armement(question: str) -> str | None:
    """Le terme hors-armement présent dans la question, s'il y en a un."""
    norm = normalize(question)
    return next((mot for mot in _HORS_ARMEMENT if mot in norm), None)
