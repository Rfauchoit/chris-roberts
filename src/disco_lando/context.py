"""Mémoire de la question précédente, par interlocuteur.

Le §2 veut un cœur qui prend du texte et rend du texte. Il ne dit pas qu'il
doit être amnésique, et l'usage le contredit : après « où trouver un P4-AR »,
la question suivante est « liste-moi tous les points de vente ». Sans mémoire,
elle n'a aucune entité et part en échec.

Trois partis pris.

**Une seule entité retenue, pas un historique.** On garde de quoi comprendre
la question suivante, rien de plus. Personne n'a besoin qu'un bot se souvienne
d'avant-hier, et le §9 pousse dans le même sens : ce qui n'est pas conservé ne
fuit pas.

**En mémoire, jamais sur disque.** Le contexte disparaît au redémarrage, et
c'est très bien : une conversation interrompue par un redémarrage n'a plus de
suite.

**Borné et daté.** Un dictionnaire qui grossit sans fin est une fuite ; une
entité vieille d'une heure n'est plus le sujet de la conversation.
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time

# Au-delà, l'entité n'est plus le sujet : on préfère avouer ne pas comprendre
# plutôt que répondre sur ce dont on parlait ce matin.
DUREE_SECONDES = 15 * 60
MAX_SESSIONS = 500

_verrou = threading.Lock()


@dataclasses.dataclass(frozen=True)
class Suite:
    """Une proposition que la réponse vient de faire, sous forme exécutable.

    Le rendu disait « je peux te donner tous les points de vente » et rien ne
    permettait de dire oui : la phrase suivante devait renommer l'objet et
    reformuler la demande entière. Une proposition doit donc être une donnée,
    pas seulement une phrase.
    """
    libelle: str                    # « tous les points de vente »
    outil: str
    args: dict
    exhaustif: bool = False
    mots: tuple[str, ...] = ()      # ce qui la désigne : « vente », « recette »
    # Le libellé est-il le **nom d'une entité** qu'on a proposée — « Ruin
    # Station » — plutôt qu'une tournure comme « tous les points de vente » ?
    # Seuls ces libellés-là autorisent leurs mots à revenir dans la réponse
    # sans passer pour un sujet neuf : le joueur recopie ce qu'on lui a
    # proposé, on ne peut pas le lui reprocher. Une tournure, elle, garde la
    # règle stricte — « les points de vente » reprend, « les points de vente
    # du Gladius » repart au routeur.
    entite_proposee: bool = False
    # **Ce qui manque à la question, et que le tour suivant apportera.**
    # « Je peux aller de Crusader à Levski » est une question complète sauf le
    # vaisseau : la réponse le demande, et « en gladius » doit la terminer au
    # lieu de repartir de zéro. `complete_avec` nomme l'argument à remplir,
    # `complete_types` les types que la réponse doit résoudre pour être
    # acceptée — sans ce contrôle, n'importe quelle phrase suivante serait
    # avalée comme un complément.
    complete_avec: str | None = None
    complete_types: tuple[str, ...] = ()
    # Ce qu'un « oui » nu désigne quand plusieurs propositions sont ouvertes.
    # Après « cadence, DPS, ou la fiche complète », « oui » veut dire la fiche.
    # Là où aucune proposition ne s'impose, aucune n'est marquée et on demande.
    defaut: bool = False
    # Une expression multi-mot exacte, pour les choix dont les mots se
    # recouvrent. « Jr. Contractor » et « Contractor » partagent le second
    # mot : les départager mot par mot sélectionnerait les deux.
    expressions: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Contexte:
    entite: str | None   # le terme tel qu'il a été résolu, pas l'UUID
    outil: str
    au: float
    suites: tuple[Suite, ...] = ()
    # La question elle-même — seulement pour l'analyste : « et le
    # P6-LR ? » se recompose avec elle. Elle est désormais retenue après
    # **toute** réponse : « demande à Codex » peut ainsi rejouer exactement la
    # demande que le déterministe vient de servir.
    question: str | None = None
    # La réponse précédente donne au LLM le sens de « explique mieux » ou
    # « vérifie ce résultat ». Une seule paire question/réponse, en mémoire et
    # bornée comme le reste du contexte — pas un historique de conversation.
    reponse: str | None = None
    # Les deux vaisseaux d'un duel qui vient d'être rendu. « Tu as oublié
    # que c'était des vaisseaux 2 joueurs » est une correction du duel en
    # cours, pas une question neuve — sans cette paire, elle sortait la
    # fiche d'un casque à 0,69 (journal du 2026-08-12). Une seule entité ne
    # suffit pas : la correction parle des deux bords à la fois.
    vaisseaux: tuple[str, ...] = ()

    @property
    def frais(self) -> bool:
        return (time.monotonic() - self.au) < DUREE_SECONDES


_sessions: dict[str, Contexte] = {}

# Vocabulaire de reprise. Fermé et court : c'est ce qui permet de distinguer
# « oui » d'une vraie question sans rien résoudre.
_ACQUIESCEMENTS = {
    "oui", "ouais", "ouaip", "yes", "yep", "ok", "okay", "dac", "daccord",
    "carrement", "volontiers", "vas", "y", "go", "envoie", "balance",
    "stp", "please", "merci", "bien", "sur", "evidemment", "je", "veux",
    "dis", "moi", "donne", "montre", "liste", "cite", "affiche",
}
# « Ces » y figure avec « les » : un démonstratif pluriel désigne ce qu'on
# vient de lister, en entier — « décris moi ces blueprint » après une liste de
# trois mourait en inconnu sur le seul mot « ces ».
_TOUT = {"tout", "tous", "toutes", "deux", "trois", "reste", "ensemble",
         "les", "ces"}
_ORDINAUX = {
    "premier": 0, "premiere": 0, "1er": 0, "1ere": 0, "un": 0,
    "second": 1, "seconde": 1, "deuxieme": 1, "2eme": 1,
    "troisieme": 2, "3eme": 2, "dernier": -1, "derniere": -1,
}
# Mots vides qui peuvent traîner dans une reprise sans rien signifier.
_LIANTS = {
    "le", "la", "l", "les", "de", "du", "des", "d", "a", "au", "aux", "et",
    "se",
    "ou", "en", "the", "s", "il", "elle", "ca", "cela", "que", "qui",
    # **Un pronom et un interrogatif sont de la grammaire, jamais un nom.**
    # Sans eux ici, ils partaient au contrôle « ce mot nomme-t-il un sujet »,
    # et le résolveur leur trouvait une entité : « elles » sortait *Gilick
    # Boots White / Teal* à 90, ce qui suffisait à faire passer « quels
    # blueprints elles donnent » pour une question neuve. La reprise mourait
    # sur un pronom.
    "elles", "ils", "eux", "on", "nous", "vous", "je", "tu", "me", "te",
    "lui", "leur", "celui", "celle", "ceux", "celles", "ce", "cet", "cette",
    "quel", "quelle", "quels", "quelles", "lequel", "laquelle",
    "lesquels", "lesquelles", "quoi", "combien", "comment", "est", "sont",
}

# Flexions et raccourcis réellement employés dans les reprises. Les suites
# publient des infinitifs stables (« miner », « détail ») ; le joueur répond
# naturellement « ça se mine où ? » ou « des infos sur toutes ». Ce petit
# canon lexical évite de dupliquer toutes les conjugaisons dans chaque rendu.
_SYNONYMES_REPRISE = {
    "mine": "miner", "mines": "miner",
    "info": "detail", "infos": "detail",
    "information": "detail", "informations": "detail",
}


def choisir(session: str | None, question: str,
            est_une_entite=None) -> list[Suite]:
    """Les propositions que cette question reprend, s'il s'agit d'une reprise.

    La première version exigeait que **tous** les mots appartiennent au
    vocabulaire de reprise. Trop strict, et mesuré en usage : après « Coda
    Pistol a 6 balles », « la vitesse de projectile » partait en classement
    général des armes, parce que « projectile » n'était dans aucune liste. Un
    vocabulaire énumérable ne couvrira jamais les façons de dire une même
    chose — c'est le raisonnement du §6, et il vaut ici aussi.

    Le vrai critère n'est pas « ce mot est-il connu » mais « ce mot
    **nomme-t-il un sujet** ». « Les points de vente du Gladius » est une
    question neuve parce qu'elle nomme le Gladius ; « la vitesse de projectile »
    ne nomme rien, elle qualifie. `est_une_entite` reçoit les mots restants —
    le contexte ne touche pas la base, l'appelant lui prête le résolveur.
    """
    from .normalize import normalize

    contexte = dernier(session)
    if contexte is None or not contexte.suites:
        return []

    normalisee = normalize(question)
    # Les réponses à une demande de valeur sont souvent « X », « je suis X »
    # ou « au rang X ». Une expression exacte passe avant le vocabulaire mot
    # par mot, qui ne peut pas distinguer deux libellés emboîtés.
    formes = {normalisee}
    sans_prefixe = re.sub(
        r"^(?:je suis|mon rang est|rang|au rang|depuis)\s+", "", normalisee)
    formes.add(sans_prefixe)
    exactes = [s for s in contexte.suites
               if any(normalize(e) in formes for e in s.expressions)]
    if len(exactes) == 1:
        return exactes

    mots = [_SYNONYMES_REPRISE.get(m, m) for m in normalisee.split()
            if m and m not in _LIANTS]
    if not mots or len(mots) > 8:
        return []

    # **Ce qu'on vient de proposer ne peut pas être un sujet neuf.** Quand on
    # demande « Ruin Station, Ruin Clinic, ou Seraphim Station ? », le joueur
    # répond « ruin station » — et « station », partagé par les trois, est
    # retiré des mots *distinctifs*. Il repartait alors au contrôle « ce mot
    # nomme-t-il un sujet », où il résout *Ruin Station* à 90 : la reprise
    # mourait sur le mot même qu'on avait écrit. Quatre cas au journal, tous
    # commentés « ça devrait marcher sans problème ».
    mots_proposes = {
        mot for suite in contexte.suites if suite.entite_proposee
        for mot in normalize(suite.libelle).split()
    }

    vise: list[Suite] = []
    communes: list[Suite] = []
    inconnus: list[str] = []
    tout_demande = False
    for mot in mots:
        # « Les 3 » après « tu veux X, Y ou Z ? » : le compte qui égale le
        # nombre de propositions les désigne toutes — journal du 2026-08-07,
        # la réponse partait chez un casque CBH-3.
        if mot in _TOUT or (mot.isdigit()
                            and int(mot) == len(contexte.suites)):
            tout_demande = True
            continue
        # **« Un » n'est un ordinal que tout seul.** Mesuré le 2026-08-10 :
        # « et si je mets un silencieux » désignait la **première**
        # proposition — l'article indéfini lu comme « le numéro un » — et la
        # réponse portait sur autre chose que ce qui était demandé. Le
        # défaut vaut pour toute reprise contenant l'article, pas seulement
        # celle-là. Seul « un » nu, après « tu veux X ou Y ? », désigne.
        if mot == "un" and len(mots) > 1:
            continue
        if mot in _ORDINAUX:
            index = _ORDINAUX[mot]
            if -len(contexte.suites) <= index < len(contexte.suites):
                vise.append(contexte.suites[index])
            continue
        if mot in _ACQUIESCEMENTS:
            continue
        touchees = [s for s in contexte.suites if mot in s.mots]
        if touchees:
            # **Un mot que toutes les propositions partagent désigne le
            # groupe, pas un membre.** Après trente-deux optiques, « où en
            # acheter » ne nomme aucune d'elles : « acheter » est dans les
            # trois propositions. Le filtre de précision en élisait une —
            # la Delta, parce que son nom est le plus court — et donnait un
            # prix précis pour une question qui n'avait pas de sujet.
            # Remarque de l'utilisateur : « me demander de quoi je parle ».
            # On les retient toutes, et l'API repose la question.
            if len(touchees) == len(contexte.suites) > 1:
                communes.extend(t for t in touchees if t not in communes)
                continue
            # « Titan » désigne l'Avenger Titan et l'Avenger Titan Renegade.
            # Le plus spécifique gagne — celui dont c'est le seul mot
            # distinctif. Même règle que le résolveur, où l'alias le plus
            # court l'emporte à score égal.
            precision = min(len(t.mots) for t in touchees)
            touchees = [t for t in touchees if len(t.mots) == precision]
            vise.extend(t for t in touchees if t not in vise)
        elif mot not in mots_proposes:
            inconnus.append(mot)

    if inconnus:
        # Rien de reconnu du tout : c'est une phrase qui passait par là, pas
        # une réponse. Le silence est le bon comportement — d'autant qu'un
        # salon Discord écoute désormais sans mot-clé. Les mots de groupe
        # comptent comme du reconnu — « décris moi ces blueprint » n'a que
        # des mots partagés par les trois propositions, et le refus prématuré
        # la tuait avant le contrôle d'entité. Le « tout » seul ne suffit
        # pas : « bonjour tout le monde » n'est pas une reprise.
        if not vise and not communes:
            return []
        if est_une_entite is None or any(est_une_entite(m) for m in inconnus):
            return []

    if vise:
        return vise
    if tout_demande:
        return list(contexte.suites)
    # Le vocabulaire du groupe, sans de quoi trancher : l'appelant en fera une
    # question. On ne devine pas à la place du joueur.
    if communes:
        return communes
    # Un « oui » nu prend la proposition par défaut, ou l'unique s'il n'y en a
    # qu'une. Sans défaut et avec plusieurs propositions, on ne devine pas.
    if len(contexte.suites) == 1:
        return [contexte.suites[0]]
    defauts = [s for s in contexte.suites if s.defaut]
    return defauts[:1]


# Refuser une proposition est une réponse, pas une incompréhension. « Non »
# après « tu veux la recette ? » tombait dans le vide et rendait « je n'ai pas
# compris la question » — ce qui accuse le joueur d'avoir mal parlé alors qu'il
# a répondu exactement ce qu'il fallait.
_REFUS = {"non", "nan", "nope", "no", "nada", "rien", "laisse", "tomber",
          "annule", "stop", "abandonne", "oublie", "jamais", "pas"}


def est_refus(question: str) -> bool:
    """La question est-elle un refus net de ce qu'on vient de proposer ?

    On exige que **tous** les mots utiles en soient : « non » refuse, « non
    mais la recette du Gladius » est une question. Même discipline que
    `est_grammatical` — un seul mot de refus ne suffit pas à colorer la phrase.
    """
    from .normalize import normalize

    mots = [m for m in normalize(question).split() if m and m not in _LIANTS]
    return bool(mots) and all(m in _REFUS or m in _ACQUIESCEMENTS for m in mots) \
        and any(m in _REFUS for m in mots)


def est_acquiescement(question: str) -> bool:
    """La question est-elle un « oui » nu, sans rien pour trancher ?

    Répondre « je n'ai pas compris » à un « oui » est absurde quand on vient
    de proposer trois choses : on ne les a pas départagées, c'est tout.
    """
    from .normalize import normalize

    mots = [m for m in normalize(question).split() if m and m not in _LIANTS]
    return bool(mots) and all(m in _ACQUIESCEMENTS or m in _TOUT for m in mots)


def propositions(session: str | None) -> list[Suite]:
    """Ce qui reste proposé et non tranché."""
    contexte = dernier(session)
    return list(contexte.suites) if contexte else []


def complement_attendu(session: str | None) -> Suite | None:
    """La question précédente attend-elle une pièce manquante ?

    Une seule à la fois : deux compléments en attente voudraient dire qu'on a
    posé deux questions dans le même tour, ce qu'on ne fait pas.
    """
    contexte = dernier(session)
    if contexte is None:
        return None
    manquants = [s for s in contexte.suites if s.complete_avec]
    return manquants[0] if len(manquants) == 1 else None


def retenir(session: str | None, entite: str | None, outil: str,
            suites: list[Suite] | tuple[Suite, ...] = (),
            question: str | None = None,
            reponse: str | None = None,
            vaisseaux: tuple[str, ...] = ()) -> None:
    """Note ce dont on vient de parler, et ce qu'on a proposé.

    `question` ne sert qu'à l'analyste : sa réponse n'a ni outil rejouable
    ni proposition, et sans la question d'origine, « et le P6-LR ? »
    repartait de zéro — remarque de la feuille de route, la plus dure à
    transférer parce qu'elle touche à la mécanique de reprise.
    """
    if not session or (not entite and not question):
        return
    with _verrou:
        # Purge paresseuse : pas de fil de nettoyage pour une structure qui
        # tient dans quelques kilo-octets.
        if len(_sessions) >= MAX_SESSIONS:
            for cle in [c for c, v in _sessions.items() if not v.frais]:
                del _sessions[cle]
            if len(_sessions) >= MAX_SESSIONS:
                _sessions.clear()
        _sessions[session] = Contexte(entite, outil, time.monotonic(),
                                      tuple(suites), question, reponse,
                                      tuple(vaisseaux))


def dernier(session: str | None) -> Contexte | None:
    """Ce dont on parlait, si c'est encore d'actualité."""
    if not session:
        return None
    with _verrou:
        contexte = _sessions.get(session)
        if contexte is None:
            return None
        if not contexte.frais:
            del _sessions[session]
            return None
        return contexte


def oublier(session: str | None) -> None:
    with _verrou:
        _sessions.pop(session or "", None)
