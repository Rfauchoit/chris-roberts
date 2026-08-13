"""Normalisation et clés phonétiques.

Le §6 du brief impose de cumuler fuzzy et phonétique. Mesuré sur des
déformations réalistes : le phonétique seul rattrape 14 cas sur 17, le fuzzy
seul 13, leur union 15. « gladiousse » est à 82 en fuzzy — sous n'importe quel
seuil raisonnable — mais donne exactement la même clé phonétique que
« Gladius ». Les deux couches ne font pas doublon.
"""

from __future__ import annotations

import re
import unicodedata


_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Bruit récurrent dans les noms scunpacked, sans valeur discriminante.
_NOISE = {"scitem", "sc", "item"}

# ------------------------------------------------------------ grammaire
#
# **Un mot de grammaire n'est jamais un nom d'entité**, et c'est la cause
# unique de presque tous les faux positifs rencontrés. Mesuré sur les listes
# déjà éparpillées dans le projet : **80 de ces 142 mots résolvent une entité à
# 85 ou plus** — « elles » sort *Gilick Boots White / Teal* à 90, « ca » sort
# *Castra*, « avec » sort *Fried Seanut with Sauce*, « coute » sortait *Drake
# Cutlass Black* à 129.
#
# Chaque module avait sa propre parade — `STOPWORDS` dans le routeur,
# `_LIANTS` dans le contexte, `_mots_d_intention`, le filtre des nombres — et
# chacun en oubliait une part différente. Un pronom manquant dans une seule des
# listes suffisait à casser une reprise. La liste vit donc **ici**, en un seul
# endroit, et le résolveur la fait respecter pour tout le monde : un nouveau
# consommateur ne peut plus oublier de filtrer.
MOTS_GRAMMATICAUX = frozenset({
    # articles et déterminants
    "le", "la", "les", "l", "un", "une", "des", "du", "de", "d", "au", "aux",
    "ce", "cet", "cette", "ces", "mon", "ma", "mes", "ton", "ta", "tes",
    "son", "sa", "ses", "notre", "nos", "votre", "vos", "leur", "leurs",
    # pronoms
    "je", "j", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "me", "te", "se", "moi", "toi", "lui", "eux", "y", "en",
    "celui", "celle", "ceux", "celles", "ca", "cela", "c", "s", "n",
    # interrogatifs. « qu » est l'élision de « que » — sans lui,
    # « qu'est-ce que c'est » gardait un mot inexpliqué et passait pour un nom.
    "qui", "que", "qu", "quoi", "quel", "quelle", "quels", "quelles",
    "lequel", "laquelle", "lesquels", "lesquelles",
    "combien", "comment", "quand", "pourquoi", "ou",
    # verbes creux et auxiliaires
    "est", "sont", "etre", "a", "ai", "as", "ont", "avoir", "fait", "faire",
    "peut", "peux", "peuvent", "pouvoir", "veux", "veut", "vouloir",
    "sais", "sait", "savoir", "dis", "dit", "dire", "faut", "va", "vont",
    "aller", "donne", "donnent", "coute", "coutent", "prend", "prennent",
    # « Sa **question** est "c'est quoi les autres" » sortait la fiche du
    # moteur quantique *Quest* à 90 (journal du 2026-08-12) : le mot parle
    # de la conversation, jamais d'une entité du jeu.
    "question", "questions", "reponse", "reponses",
    # « Et si je **mets** un silencieux » : mesuré le 2026-08-10, « mets »
    # résout *Metis* à 88,9 et « si » résout *Siren* à 90 — au-dessus du
    # seuil, donc la reprise mourait en croyant lire un sujet neuf, et
    # répondait sur une station. Même famille que « elles » → *Gilick Boots*.
    "mets", "met", "mettre", "mis", "mettent", "pose", "poser", "monte",
    "monter", "si",
    # prépositions et liaisons
    "et", "ou", "mais", "donc", "or", "ni", "car", "avec", "sans", "sous",
    "sur", "dans", "pour", "par", "chez", "vers", "depuis", "entre",
    # « contre » manquait alors que « entre » et « vers » y sont depuis le
    # début : « un p6 lr **contre** une armure lourde » laissait un mot
    # inexpliqué, et le contrôle de certitude posait « tu veux dire… ? » sur
    # une question limpide. Mesuré le 2026-08-10.
    "contre",
    "jusqu", "jusqua", "pres", "apres", "avant", "plus", "moins", "tres",
    "aussi", "encore", "deja", "toujours", "jamais", "pas", "ne",
    # quantifieurs et reprises
    "tout", "tous", "toute", "toutes", "autre", "autres", "meme", "memes",
    "quelque", "quelques", "chaque", "certain", "certains",
    "oui", "non", "ok", "voila", "alors", "the", "there",
})


def est_grammatical(texte: str) -> bool:
    """Le terme n'est-il **que** de la grammaire ?

    On exige que **tous** les mots le soient : « castra » reste le système
    Castra même si « ca » est un pronom, et « les points de vente du Gladius »
    nomme bien le Gladius. Seul le terme entièrement grammatical est refusé.
    """
    mots = normalize(texte).split()
    return bool(mots) and all(m in MOTS_GRAMMATICAUX for m in mots)


#: « mkii » collé ne se découpait pas : « aurora mkii » servait le Mk I CL
#: à 85,5 — zéro mot inexpliqué, mauvais vaisseau (journal du 2026-08-12).
#: On sépare la marque de son numéro, chiffres arabes et romains ; les
#: alias passent par la même fonction, donc les deux formes convergent.
_MARQUE_MK = re.compile(r"\bmk(\d+|i{1,3}v?|vi{0,3}|ix|x)\b")

#: « p6lr » collé ne résolvait rien — le meilleur candidat était une
#: tourelle de nez Hornet, par le « pour » d'un alias français (journal du
#: 2026-08-13, « et pour le p6lr ? »). On coupe entre les chiffres du
#: modèle et la queue de gamme : « p6lr » → « p6 lr », « p8ar » → « p8 ar »,
#: « f55lmg » → « f55 lmg ». Mesuré avant d'écrire : **zéro** jeton d'alias
#: ne matche ce motif — la queue exige deux lettres, « f7c » et « cq7 »
#: restent donc intacts, et le découpage ne touche que les frappes collées.
_ARME_COLLEE = re.compile(r"\b([a-z]{1,2}\d{1,2})([a-z]{2,})\b")


def normalize(text: str) -> str:
    """Minuscules, sans accent ni ponctuation, espaces normalisés.

    C'est la forme sur laquelle portent l'égalité stricte et le fuzzy.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    aplati = " ".join(_NON_ALNUM.sub(" ", stripped).split())
    aplati = _ARME_COLLEE.sub(r"\1 \2", aplati)
    return _MARQUE_MK.sub(r"mk \1", aplati)


def tokenize(text: str) -> list[str]:
    """Mots utiles d'un nom normalisé, sans le bruit technique."""
    return [w for w in normalize(text).split() if w not in _NOISE]


def split_class_name(class_name: str) -> list[str]:
    """Découpe un ClassName scunpacked en fragments lisibles.

    « AEGS_Gladius » -> ['aegs', 'gladius'] ; « KLWE_LaserRepeater_S3 » ->
    ['klwe', 'laser', 'repeater', 's3']. Sert à fabriquer des alias
    supplémentaires : le joueur dit « laser repeater », jamais
    « KLWE_LaserRepeater_S3 ».
    """
    if not class_name:
        return []
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", class_name.replace("_", " "))
    return [w for w in normalize(spaced).split() if w not in _NOISE]
