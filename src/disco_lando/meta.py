"""Ce qui n'est pas une question de jeu, et qui coûtait pourtant un LLM.

Mesuré le 2026-08-10 sur le journal : « t'es offline ? » partait chez
l'analyste pour 10,2 s puis 12,1 s **sans réponse** ; « jesus christ c'est
quoi cette réponse » mettait 2,5 s rien qu'à échouer au routage, avant les
dizaines de secondes de l'analyste ; « c'est quoi la distance entre la Terre
et Proxima b » rendait une entité au hasard ou rien. Aucune de ces questions
n'a de réponse dans la base, et toutes en ont une évidente en français.

**Ce module ne voit que ce que le routeur n'a pas compris.** Il est consulté
après l'étage déterministe et avant l'analyste : il ne peut donc rien
prendre à un outil, seulement au LLM. C'est ce qui autorise des motifs
courts sans risquer d'avaler une vraie question — le garde-fou est en
amont, pas dans le motif.

Il ne moralise pas et ne répond pas à la place du joueur : chaque réponse
dit ce qu'on sait faire, et propose une suite exécutable.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .normalize import normalize


@dataclasses.dataclass(frozen=True)
class Meta:
    """Une réponse de conversation, sans donnée de jeu derrière."""

    genre: str
    texte: str
    # Ce qu'on propose ensuite, en clair. Le rendu l'ajoute tel quel : une
    # proposition annoncée doit pouvoir être tapée à la main.
    suite: str | None = None


# **Le joueur n'a pas compris, ou n'a pas entendu.** « keske », « hein »,
# « quoi ? » — trois mots qui ne demandent rien au jeu et tout à la
# conversation. Ils partaient chez l'analyste, qui répondait à côté ou pas
# du tout.
_INCOMPREHENSION = re.compile(
    r"^(?:keske|kes ?ke ?(?:sa|ca)?|hein|quoi|comment ca|comment|pardon|"
    r"kwa|wtf|\?+)\s*\?*$")

# **Le joueur n'est pas content de la réponse précédente.** Le dire vaut
# mieux que l'ignorer, et la seule chose utile qu'on puisse offrir est un
# autre chemin : reformuler, ou passer la question à l'analyste.
_MECONTENTEMENT = re.compile(
    r"\bveut rien dire\b|\bn importe quoi\b|\bc est quoi cette reponse\b|"
    r"\bca sert a rien\b|\bnul\b|\bmauvaise reponse\b|\bfaux\b|"
    r"\b\d+ ?(?:min|minutes?|s|secondes?) pour ca\b|\btrop long\b|"
    r"\btrop lent\b|\btu comprends rien\b")

# **Une insulte est une réponse aussi.** On la reconnaît pour ne pas la
# faire payer dix secondes de LLM, et on répond court — ni leçon, ni excuse
# en trois paragraphes. « Sale fou » et « fdp » viennent du journal du
# 2026-08-12 : tous deux mouraient en « Je n'ai pas compris la question ».
_COLERE = re.compile(
    r"\bfils de (?:pute|chien)\b|\bfdp\b|\bconnard\b|\bputain\b|\bmerde\b|"
    r"\benculé?\b|\bencule\b|\bta gueule\b|\bdegage\b|\bsalop\w*\b|"
    r"\bsale (?:fou|folle|con\w*|bete|type|bot|robot)\b|"
    r"\bjesus\b|\bbouffon\b")

# **Le bavardage d'atelier parle du bot, pas au bot.** « Je vais attendre la
# mep », « c'est dans le backlog », « il avait pas encore commit » — les
# joueurs commentent le développement dans le même salon. Chacune coûtait
# une incompréhension affichée (journal du 2026-08-10). Le vocabulaire est
# fermé et ne recouvre aucun mot du jeu : rien à voler à un outil.
_ATELIER = re.compile(
    r"\bmep\b|\bbacklog\b|\bcommit\w*\b|\bdeploi\w*\b|\bmerge\w*\b|"
    r"\bmise en prod\w*\b|\bhotfix\b|\btroll\w*\b")

# **Ce qui n'est pas dans l'univers du jeu.** Mesuré : la base ne publie que
# trois systèmes — Stanton, Pyro et Nyx. « Terre » sort *Morozov-SH Arms
# Terrene* à 90, « proxima b » sort *Hull B Horizon Livery* à 86 : des
# réponses chiffrées sur des objets sans rapport. La liste est fermée et ne
# contient **ni « lune » ni « sol »** — « les lunes de Crusader » est une
# question du jeu, et Sol est le nom d'un de nos analystes.
_HORS_UNIVERS = re.compile(
    r"\bterre\b|\bearth\b|\bproxima\b|\bcentauri\b|\bcentaure\b|"
    r"\bannees? lumiere\b|\blight ?years?\b|\bvoie lactee\b|\bgalaxie\b|"
    r"\bmars\b|\bjupiter\b|\bsaturne\b|\bvenus\b|\bmercure\b|\bneptune\b|"
    r"\buranus\b|\bpluton\b|\bsysteme solaire\b|\bnasa\b|\bspacex\b")

# **La feuille de route de CIG n'est pas dans les fichiers du jeu.** On
# ingère un build ; il ne contient ni date de sortie, ni notes de patch, ni
# contenu à venir. « Quaand sort squadron 42 » et « il y a quoi dans la
# 4.10 » partaient au LLM, qui répond de mémoire — c'est-à-dire de
# l'invention datée. Le numéro de version se lit large (`4.10`, `1.0`) mais
# **toujours accompagné** d'un verbe de sortie ou du mot « patch » : « les
# armes de taille 4.10 » n'existe pas, « combien coûte le 890 Jump » si.
_HORS_DONNEES = re.compile(
    r"\bsquadron 4\d\b|\bsq ?4\d\b|\broadmap\b|\bfeuille de route\b|"
    r"\b(?:quand|date de) sort\w*\b|\bdate de sortie\b|"
    r"\b(?:prochaine|prochain) (?:mise a jour|patch|version)\b|"
    r"\b(?:patch ?notes?|notes? de (?:patch|version))\b|"
    # **Ce que CIG n'a pas encore livré n'est dans aucun fichier.**
    # Mesuré en service le 2026-08-13 : « en quelle année sort squadron
    # 43 » et « à quand le base building » partaient chez l'analyste, qui
    # mettait 30 s de quota à répondre « ce n'est pas dans ma base ».
    # `meta` le dit en millisecondes — et c'est la même leçon que « t'es
    # offline ? » : le méta-dialogue ne se paie pas au LLM.
    #
    # « Squadron 43 » est une coquille courante pour 42, d'où `4\d`.
    r"\b(?:a quand|c est pour quand|prevu pour quand|ca sort quand)\b|"
    r"\b(?:base ?building|construction de base)\b|"
    r"\bpyro 2\b|\bnyx (?:arrive|sort)\b")

# **Le numéro de version se lit sur le texte brut.** `normalize` retire la
# ponctuation : « la 4.10 » devient « la 4 10 », et un motif écrit avec un
# point ne matche plus jamais. Même famille que « ?? », vidé par la même
# normalisation. On exige un mot de contenu devant, sinon « les armes de
# taille 4 » ou une quantité décimale entrerait ici.
_VERSION_JEU = re.compile(
    r"\b(?:quoi |contenu |nouveaut\w* |sortie |ajout\w* )?(?:dans|de|pour) "
    r"la (\d+\.\d+)\b", re.IGNORECASE)

# **Un sujet nu n'est pas une question, mais il en désigne une.** « missions »
# ou « ou miner ? » se taisaient ; le joueur a nommé son intention et oublié
# son complément. On rend la question qui manque, avec un exemple complet —
# c'est la règle « une question à laquelle il manque une pièce reste
# ouverte », appliquée avant même qu'un outil ait pu se déclencher.
_SUJETS_NUS: tuple[tuple[str, str, str], ...] = (
    (r"^(?:les )?missions?\??$", "Les missions de quoi ?",
     "« les missions de Foxwell », « les missions de minage à Stanton », "
     "« quelles missions paient le plus »"),
    (r"^ou miner\??$", "Miner quoi ?",
     "« où miner du quantainium », « quel minerai rapporte le plus à Pyro »"),
    (r"^ou (?:acheter|trouver)\??$", "Acheter quoi ?",
     "« où acheter un P8-AR », « le point de vente le plus proche de "
     "microTech pour un P4-AR »"),
    (r"^(?:les )?blueprints?\??$", "Le blueprint de quoi ?",
     "« le blueprint du P6-LR », « quels blueprints à débloquer à Stanton »"),
    (r"^(?:les )?prix\??$", "Le prix de quoi ?",
     "« combien coûte un Gladius », « le prix du quantainium »"),
    (r"^(?:les )?vaisseaux?\??$", "Quel vaisseau ?",
     "« la fiche du Gladius », « quel vaisseau pour le minage », "
     "« quel vaisseau de combat sous 2 millions »"),
    (r"^(?:ou )?raffiner\??$", "Raffiner quoi ?",
     "« où raffiner du stileron », « quelle méthode de raffinage est la plus "
     "rentable »"),
)


def detecte(question: str, *, a_un_tour_precedent: bool = False) -> Meta | None:
    """La question relève-t-elle de la conversation plutôt que du jeu ?

    L'ordre compte : le mécontentement se lit avant l'incompréhension, parce
    que « c'est quoi cette réponse » contient « quoi » ; et le sujet nu passe
    en dernier, un mot seul étant le cas le plus faible.
    """
    brut = (question or "").strip()
    norm = normalize(brut)
    if not norm:
        # **« ?? » ne survit pas à la normalisation**, qui retire la
        # ponctuation : la question devenait vide et repartait au LLM. C'est
        # pourtant le signal d'incompréhension le plus tapé sur Discord.
        if brut and set(brut) <= set("?! "):
            return Meta("incomprehension",
                        "Dis-moi ce qui n'était pas clair et je le reprends "
                        "autrement.")
        return None

    # **Une insulte incruste n'avale pas la question.** « Il y a un jump
    # point entre nyx et Stanton sale fou » mourait en colère et la vraie
    # question était perdue (journal du 2026-08-12). La colère et le
    # bavardage d'atelier ne répondent court que si la phrase EST
    # l'expression — au-delà de trois mots utiles hors du motif, elle
    # porte un contenu et passe son chemin.
    def _essentiellement(motif: re.Pattern) -> bool:
        match = motif.search(norm)
        if match is None:
            return False
        reste = (norm[:match.start()] + " " + norm[match.end():]).split()
        from .normalize import MOTS_GRAMMATICAUX
        utiles = [m for m in reste if m not in MOTS_GRAMMATICAUX]
        return len(utiles) <= 3

    if _essentiellement(_COLERE):
        return Meta("colere",
                    "Reçu. Repose ta question autrement et je m'y remets.")

    if _essentiellement(_ATELIER):
        return Meta("atelier",
                    "Vu — ça se règle côté atelier, pas dans le 'Verse. "
                    "Moi je réponds sur le jeu dès que tu veux.")

    # **Une relance n'est pas une question, un compliment non plus.**
    # « réponds moi » coûtait 8,6 s d'analyste pour un bonjour, « Ah ça
    # c'est intéressant » et « je t'ai pas parlé » mouraient en
    # incompréhension (journal du 2026-08-12).
    if re.fullmatch(r"(?:reponds? ?(?:moi)?|alors|tu dors|t es la)\s*", norm):
        return Meta("relance",
                    "Je suis là — pose ta question sur le jeu et je réponds.")
    if re.search(r"\bc est pas une question\b|\bje (?:ne )?t ai pas parle\b|"
                 r"\bje te parlais pas\b", norm):
        return Meta("aparte",
                    "Compris, je me tais — appelle-moi par mon nom quand "
                    "c'est pour moi.")
    if re.fullmatch(
            r"(?:ah |oh )?(?:ca )?(?:c est )?(?:vraiment )?(?:tres |plutot )?"
            r"(?:interessant|pas mal|bien joue|top|parfait|excellent|gg)"
            r"(?: ca)?(?: comme reponse)?\s*", norm):
        return Meta("compliment", "Merci ! La suite quand tu veux.")

    # **Le vocabulaire du carnet se définit gratuitement.** « Qu'est-ce
    # qu'une session pour toi ? » partait à l'analyste pour un haussement
    # d'épaules à 9 s (journal du 2026-08-13). La définition est à nous —
    # c'est l'arbitrage du sprint 26, autant le dire en millisecondes.
    if re.search(r"\bsessions?\b|\breapparitions?\b|\brez\b", norm) \
            and re.search(r"\bpour toi\b|\bc est quoi\b|\bqu est ce\b|"
                          r"\bcomment tu (?:les? )?comptes?\b", norm):
        return Meta(
            "vocabulaire_carnet",
            "Au carnet, une **session** est une connexion au jeu — un "
            "lancement du client, une session. Les **réapparitions** (le "
            "rez : réveil, respawn) se comptent à part, et un fait que le "
            "Game.log écrit deux fois n'est compté qu'une. Les hauts faits "
            "d'assiduité suivent les sessions ; ceux du rez — de Gandalf "
            "le Gris à Jésus-Chris — suivent les réapparitions.")

    # **« Pour toi » n'est pas une question de données.** Le reste de la
    # phrase, si : on invite à la reposer en termes de jeu au lieu de payer
    # l'analyste pour un refus. Sûr par architecture — le méta ne voit que
    # ce que l'étage 1 n'a pas su servir, il ne peut rien voler à un outil.
    if re.search(r"\bpour toi\b|\bselon toi\b|\b(?:a )?ton avis\b|"
                 r"\bd apres toi\b|\btu en penses quoi\b|\bqui es tu\b|"
                 r"\bt es (?:qui|quoi)\b|"
                 r"\bcomment tu (?:fonctionnes?|marches?)\b", norm):
        return Meta("sur_moi",
                    "Je n'ai pas d'avis — je mesure. Mes réponses sortent "
                    "des fichiers du jeu, des prix relevés sur UEX et du "
                    "carnet de la guilde. Repose la question en termes de "
                    "jeu et je réponds en chiffres.")

    if _MECONTENTEMENT.search(norm):
        texte = ("Ma réponse n'a pas servi." if a_un_tour_precedent
                 else "Je n'ai pas répondu à côté volontairement.")
        return Meta("mecontentement",
                    texte + " Reformule et je reprends — ou dis « demande à "
                    "Claude » et je passe la question à l'analyste, qui a le "
                    "droit de calculer librement sur la base.",
                    suite="demande à Claude")

    if _INCOMPREHENSION.match(norm):
        if a_un_tour_precedent:
            return Meta("incomprehension",
                        "Dis-moi ce qui n'était pas clair et je le reprends "
                        "autrement — ou repose la question avec d'autres mots.")
        return Meta("incomprehension",
                    "Je n'ai pas de question à reprendre. Pose-la — un "
                    "vaisseau, une arme, un lieu, une mission — et je "
                    "réponds sur les fichiers du jeu.")

    # Une question courte, sinon « la distance de la Terre à New Babbage en
    # années-lumière » — qui mêle les deux mondes — se ferait refuser en
    # bloc. Au-delà, c'est l'analyste qui tranche, comme avant.
    if _HORS_UNIVERS.search(norm) and len(norm.split()) <= 14:
        return Meta("hors_univers", "")

    # **Ce que CIG n'a pas encore livré n'est pas dans les fichiers.** « Quand
    # sort Squadron 42 », « il y a quoi dans la 4.10 » : la base est extraite
    # d'un build précis, elle n'a ni calendrier ni notes de patch. C'était le
    # dernier vrai trou isolé du corpus au 2026-08-10.
    if _HORS_DONNEES.search(norm) or _VERSION_JEU.search(brut):
        return Meta("hors_donnees", "")

    for motif, demande, exemples in _SUJETS_NUS:
        if re.match(motif, norm):
            return Meta("sujet_nu", demande, suite=exemples)
    return None


def hors_univers(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« C'est quoi la distance entre la Terre et Proxima b ».

    Ce n'est pas une question à laquelle on peut répondre à moitié : la base
    est extraite des fichiers du jeu et ne connaît que ce que CIG a livré.
    Le dire est la réponse — et on **compte** les systèmes plutôt que de les
    écrire à la main, pour que l'ouverture d'un système par un patch ne
    laisse pas une phrase fausse derrière elle.
    """
    systemes = [r["name"] for r in con.execute(
        "SELECT name FROM starmap WHERE type_name = 'Star' AND name IS NOT NULL "
        "ORDER BY name")]
    if not systemes:
        raise NotFound(query)
    build = _dict(_row(con, "SELECT game_version, build_id FROM ingest_runs "
                            "WHERE status = 'ok' ORDER BY id DESC LIMIT 1"))
    return {"systemes": systemes, "build": build, "resolution": None}


def hors_donnees(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Quand sort Squadron 42 », « il y a quoi dans la 4.10 ».

    On ingère **un build**, pas un calendrier. Le laisser partir au LLM,
    c'est laisser répondre une mémoire d'entraînement datée — une réponse
    plausible, précise et fausse, exactement le pire cas du §7. La bonne
    réponse dit de quel build on parle, et ce qu'on sait en faire.
    """
    build = _dict(_row(con, "SELECT game_version, build_id, finished_at "
                            "FROM ingest_runs WHERE status = 'ok' "
                            "ORDER BY id DESC LIMIT 1"))
    if build is None:
        raise NotFound(query)
    return {"build": build, "resolution": None}
