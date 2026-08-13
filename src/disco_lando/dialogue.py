"""L'orchestration d'un tour de conversation — partagée par l'API et les tests.

Ce module est né d'un défaut de structure : `api.ask` portait toute la
mécanique conversationnelle — compléments, reprises, refus, doutes,
variantes — et le harnais de test la **répliquait** en version simplifiée.
Toute divergence entre les deux se payait par des tests qui passent sur une
conversation que personne ne peut avoir. Il n'y a désormais qu'une
implémentation : `traiter()`, appelée par l'API (qui n'ajoute que le chrono,
le journal et les modèles HTTP) et par les tests (qui n'ajoutent rien).

L'ordre des opérations est le contrat de comportement du cahier :

1. le **complément** — « en gladius » après « avec quel vaisseau ? » ;
2. les **reprises** du contexte — « oui », « la recette », « les deux » ;
3. le **refus** — « non » écarte et propose la suite ;
4. le « oui » **ambigu** — plusieurs propositions, on redemande ;
5. le **routeur**, puis « même question, autre sujet » en dernier recours ;
6. le **doute** (terme mal expliqué) avant l'**ambiguïté** (variantes) ;
7. l'exécution, la mémorisation du sujet et des propositions.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import sqlite3
from typing import Any

from . import (analyste, config, context, erreurs, meta, render,
               resultats, router)
from ._socle import est_un_accessoire, nomme_un_accessoire
from .guilde import questions as questions_guilde
from .queries import NotFound
from .queries import reponse_vide as queries_reponse_vide
from .router.deterministic import mots_d_entite
from .router.deterministic import veut_tout as _veut_tout
from .resolver import mots_inexpliques
from .resolver import resolve as resolve_entity

#: Au-delà, on n'énumère pas — la question deviendrait aussi pénible que
#: l'erreur qu'elle évite.
MAX_VARIANTES = 5


def _avec_identite_analyste(question: str, speaker: str | None) -> str:
    """Explique « moi » au LLM sans lui transmettre l'identifiant privé."""
    identite = questions_guilde.identite_liee(speaker)
    if identite is None:
        return question
    return f"Identité liée du demandeur : {identite}.\n{question}"

# Une demande explicite n'est pas une nuance de routage : elle court-circuite
# le déterministe et choisit son fournisseur. Les noms de modèle sont admis
# parce que le joueur dit naturellement « demande à Sonnet » ou « demande à
# Sol » même si les portes techniques s'appellent Claude et Codex.
# `demande[rz]?` : « demander à Claude » (infinitif) et « demandez à Claude »
# sont la même intention — mesuré au journal du 2026-08-10, « demander à
# claude » mourait en « je n'ai pas compris ».
_FORCAGE_ANALYSTE = re.compile(
    r"^\s*demande[rz]?\s+[àa]\s+(claude|codex|sonnet|sol)\b\s*"
    r"(?:[:,;\-–—]\s*)?(.*)$", re.IGNORECASE | re.DOTALL)
_FOURNISSEUR_FORCE = {
    "claude": "claude", "sonnet": "claude",
    "codex": "codex", "sol": "codex",
}

# **Changer de fournisseur pour de bon, sans redémarrer le cœur.** Demande
# de l'utilisateur (2026-08-10) : « changer le modèle à la volée selon où
# j'ai des tokens ». `demande à Sonnet : …` ne vaut que pour une question ;
# ici on déplace le défaut. Le verbe est exigé — « passe à Sonnet » bascule,
# « le prix du Sonnet » n'est pas une commande.
_BASCULE_ANALYSTE = re.compile(
    r"^\s*(?:chris[,\s]+)?(?:passe|bascule|repasse|switch\w*|utilise)\s+"
    r"(?:[àa]\s+|sur\s+|en\s+)?(claude|codex|sonnet|sol)\b\s*[.!]?\s*$",
    re.IGNORECASE)


def extraire_bascule_analyste(question: str) -> str | None:
    """« passe à Sonnet » → ``claude``, sinon None."""
    match = _BASCULE_ANALYSTE.match(question)
    return (_FOURNISSEUR_FORCE[match.group(1).lower()]
            if match is not None else None)


def extraire_forcage_analyste(question: str) -> tuple[str | None, str]:
    """« demande à Codex : … » → (``codex``, question sans préfixe)."""
    match = _FORCAGE_ANALYSTE.match(question)
    if match is None:
        return None, question
    return _FOURNISSEUR_FORCE[match.group(1).lower()], match.group(2).strip()


def _champ(data: Any, cle: str, defaut: Any = None) -> Any:
    """Lit un résultat métier, dictionnaire ou dataclass.

    Les outils historiques rendent des dictionnaires ; qualité et équipement
    rendent des dataclasses typées. Le dialogue est leur frontière commune et
    ne doit pas supposer l'une des deux représentations.
    """
    if isinstance(data, dict):
        return data.get(cle, defaut)
    return getattr(data, cle, defaut)


def raisons_reponse_incomplete(outil: str, data: Any) -> list[str]:
    """Signaux structurés qui interdisent de prendre une réponse pour entière.

    On ne cherche jamais ces signaux dans la prose : chaque cas correspond à
    un contrat métier mesurable. Le LLM reçoit alors le résultat déterministe
    déjà vérifié et tente seulement d'en combler les trous par d'autres
    jointures de la même base.
    """
    raisons: list[str] = []
    qualite = _champ(data, "qualite_reponse") or {}
    manques = _champ(qualite, "manques") or []
    if _champ(qualite, "complet") is False:
        raisons.extend(str(manque) for manque in manques)
        if not manques:
            raisons.append("calcul partiel")
    elif _champ(data, "complet") is False:
        # Compatibilité pendant la migration famille par famille. Dès qu'un
        # résultat porte le nouveau contrat, sa raison précise gagne.
        raisons.append("calcul partiel")
    if not qualite and outil in ("peut_detruire", "bataille"):
        if _champ(data, "sans_defense_connue"):
            raisons.append("défense de la cible incomplète")
        if _champ(data, "duel") is None:
            raisons.append("duel non calculable avec le profil retenu")
        if _champ(data, "arme_refusee"):
            raisons.append("arme demandée non résolue ou incompatible")
        if _champ(data, "incomprises"):
            raisons.append("modulations de combat non comprises")
    return list(dict.fromkeys(raisons))


def _avec_contexte_analyste(question: str,
                            precedent: context.Contexte | None) -> str:
    """Donne au LLM une paire précédente, pas un historique sans borne.

    **La fenêtre de quinze minutes vaut aussi pour le LLM.** Sonnet est sans
    état : sans cette paire, chaque question de suite repartait de zéro, et le
    joueur voyait « c'est notre premier échange » alors qu'il enchaînait sur
    le même sujet — quatre commentaires au journal du 2026-08-10. On ne joint
    donc le tour précédent que s'il est **frais** ; passé le délai, la
    conversation est réputée close et la question repart nue.
    """
    if precedent is None or not precedent.question or not precedent.frais:
        return question
    morceaux = [
        "Contexte du tour précédent (à utiliser seulement si la nouvelle "
        "demande y fait référence) :",
        f"Question : {precedent.question}",
    ]
    if precedent.reponse:
        # Une réponse Discord ne dépasse normalement pas 2 000 caractères ;
        # la borne protège aussi les appels directs à l'API.
        morceaux.append(f"Réponse : {precedent.reponse[:4000]}")
    morceaux.append(f"Nouvelle demande : {question}")
    return "\n".join(morceaux)


def _prompt_analyste(session: str | None, question: str,
                     speaker: str | None) -> str:
    """Ce qu'on confie à l'analyste : l'identité du demandeur **et** le tour
    précédent frais.

    **Un seul point de passage.** Le contexte n'était donné qu'au chemin
    forcé ; les quatre autres chemins d'analyste — vocabulaire d'analyse,
    incapacité, échec de routage, absence de donnée — envoyaient la question
    nue. Une même session pouvait donc voir Sonnet répondre en connaissance
    de cause sur « demande à Claude » et l'oublier sur la question suivante.
    On compose au même endroit pour tous.
    """
    avec_contexte = _avec_contexte_analyste(question, context.dernier(session))
    return _avec_identite_analyste(avec_contexte, speaker)


@dataclasses.dataclass
class Echange:
    """Ce qu'un tour de conversation produit — le texte et ses métadonnées.

    `echec` distingue les deux façons de ne pas répondre : « no_intent »
    (le routeur n'a pas compris) et « no_data » (compris, mais rien en
    base) — c'est ce que `unanswered` consigne côté API.
    """

    question: str
    texte: str
    answered: bool
    tool: str | None = None
    args: dict[str, Any] | None = None
    confidence: float | None = None
    via: str | None = None
    entity: str | None = None
    follow_up: list[str] = dataclasses.field(default_factory=list)
    data: dict[str, Any] | None = None
    echec: str | None = None


def variantes_ambigues(con: sqlite3.Connection, call: router.ToolCall,
                       question: str = "") -> dict[str, Any]:
    """Les variantes entre lesquelles il faut trancher, ou rien.

    Le critère n'est pas « plusieurs entités se ressemblent » mais « la réponse
    changerait ». Mesuré sur les deux cas du journal : « les SCU d'un Avenger »
    donne 8, 0, 0 et 8 selon la variante — il faut demander. « La vitesse max
    d'un Avenger » donne 1 425 pour les quatre, et « les balles d'un Coda » 6
    pour les six : demander serait un péage inutile.

    On rend donc chaque candidat et on compare les textes, nom retiré. C'est
    plus coûteux qu'une heuristique sur les noms, mais c'est la seule mesure
    qui réponde à la vraie question.
    """
    vide: dict[str, Any] = {"question": [], "partagent": [], "sans_reponse": []}
    outil = router.TOOLS.get(call.tool)
    if outil is None or not outil.entity_types:
        return vide
    terme = call.args.get(outil.arg)
    if not isinstance(terme, str) or not terme.strip():
        return vide

    resolution = resolve_entity(con, terme, entity_types=outil.entity_types,
                                limit=MAX_VARIANTES + 3)
    if resolution.best is None:
        return vide
    from . import config

    plancher = resolution.best.score - config.AMBIGUITY_MARGIN
    candidats, vus, noms_vus = [], set(), set()
    meilleur_nom = resolution.best.name
    for candidat in resolution.candidates:
        if candidat.score < plancher or candidat.entity_id in vus:
            continue
        # **Un choix proposé deux fois n'est pas un choix.** Deux entités
        # distinctes portent le même nom affiché — le journal a montré « Tu
        # parles duquel ? Iron, Iron, … ». Le dédoublonnage par UUID ne voyait
        # rien ; on dédoublonne aussi sur le **nom**, comme `compare_ships`.
        if candidat.name in noms_vus:
            continue
        # **Une entrée technique n'est pas un choix.** Les gisements
        # `MineableRock_test_*` (27) et `_TEMPLATE` (10) sont des entrées
        # d'outillage de CIG : les proposer, c'est demander au joueur de
        # trancher entre le jeu et son échafaudage.
        nom_bas = candidat.name.lower()
        if "_test_" in nom_bas or nom_bas.endswith(("_test", "template")):
            continue
        # **La forme raffinée ne concurrence pas son minerai.** « Où miner de
        # l'héphaestanite » demandait « Hephaestanite, ou Hephaestanite
        # (R) ? » — le même minerai sous deux formes, une question sans bonne
        # réponse. Journal du 2026-08-07.
        souche = re.sub(r"\s*\((?:r|raw|ore)\)$", "", nom_bas)
        if souche != nom_bas and any(c.name.lower() == souche
                                     for c in resolution.candidates):
            continue
        # **Un gisement ne concurrence pas son minerai.** La fenêtre de
        # tolérance attrape les `MineableRock_*` exprès — c'est ainsi que
        # l'outil trouve les filons — mais au moment de *demander*, ils sont
        # la réponse du meilleur candidat, pas des alternatives : « quel filon
        # a le plus de fer » proposait Iron contre ses propres gisements.
        if (candidat.name.startswith("MineableRock_")
                and not meilleur_nom.startswith("MineableRock_")):
            continue
        vus.add(candidat.entity_id)
        noms_vus.add(candidat.name)
        candidats.append(candidat)
    if len(candidats) < 2 or len(candidats) > MAX_VARIANTES:
        return vide

    # On masque **tous** les noms de variantes avant de comparer, pas seulement
    # celui du candidat : sinon elles se distinguent par le seul fait de
    # s'exclure mutuellement de leur propre liste — le fusil citant son
    # chargeur, le chargeur citant le fusil.
    noms = [c.name for c in candidats]

    def sans_les_noms(texte: str) -> str:
        for nom in sorted(noms, key=len, reverse=True):
            texte = texte.replace(nom, "«»")
        # « Mêmes effets sur X, Y » cite les *autres* variantes : une fois
        # masquée, l'énumération ne diffère plus que par son **compte** de
        # masques — deux pour l'une, trois pour l'autre — et ce compte
        # faisait passer des réponses identiques pour concurrentes. Une
        # énumération de masques se replie sur un seul.
        texte = re.sub(r"«»(?:(?:,| et) «»)*", "«»", texte)
        # **Et la phrase entière s'efface.** Elle peut nommer des variantes
        # qui sont *hors* de la fenêtre de candidats — donc non masquées — et
        # deux réponses identiques au chiffre près se remettaient à diverger
        # sur cette seule ligne. Elle parle du choix lui-même, pas de la
        # réponse : la garder dans la comparaison, c'est demander au joueur de
        # trancher entre deux textes qui ne diffèrent que par la liste des
        # choix qu'on lui propose.
        return re.sub(r"(?m)^_?Mêmes [^\n]*$", "", texte)

    rendus: list[tuple[str, str, bool]] = []
    for candidat in candidats:
        args = dict(call.args)
        args[outil.arg] = candidat.name
        try:
            data = router.execute(con, router.ToolCall(
                tool=call.tool, args=args, confidence=1.0, via="variante"))
        except (NotFound, sqlite3.Error):
            rendus.append((candidat.name, "", True))
            continue
        rendus.append((candidat.name,
                       sans_les_noms(render.render(call.tool, data)),
                       queries_reponse_vide(call.tool, data)))

    if len(rendus) < 2:
        return vide

    # Une variante sans réponse n'est pas une réponse concurrente. S'il ne
    # reste qu'un seul contenu, la question ne se pose pas : on répond — mais
    # on **dit** qui partage cette réponse et qui n'a rien, sinon on perd
    # l'information que ces variantes existent. C'est le cas du P6-LR : le
    # fusil et son chargeur sortent des mêmes douze missions, les trois
    # déclinaisons cosmétiques n'en ont aucune.
    pleins = [(nom, texte) for nom, texte, sans in rendus if not sans]

    # **Une réponse contenue dans une autre n'est pas concurrente.** Les
    # quatre blueprints du P6-LR partagent la même recette au centime près ;
    # seule la base porte en plus la phrase des missions qui la donnent. Les
    # textes différaient donc, et on demandait « tu parles duquel ? » sur
    # quatre variantes interchangeables — remarque de l'utilisateur : « c'est
    # la même recette et le même blueprint non ? ». Le court, préfixe du
    # long, est la même réponse en moins renseigné : on garde le riche et on
    # cite les autres en partage.
    def _absorber(couples: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
        gardes: list[tuple[str, str]] = []
        absorbes: list[str] = []
        for nom, texte in sorted(couples, key=lambda c: -len(c[1])):
            if any(texte in grand for _, grand in gardes):
                absorbes.append(nom)
            else:
                gardes.append((nom, texte))
        return gardes, absorbes

    if len({texte for _, texte in pleins}) > 1:
        # **« Le P8-AR » désigne l'arme, jamais son chargeur.** Règle donnée
        # par l'utilisateur pour *toutes* les armes : « sinon j'aurais dit
        # les munitions de ». Le catalogue porte les deux sous des noms qui ne
        # diffèrent que d'un mot, et « comment fabriquer le P8AR » se voyait
        # répondre « tu parles duquel ? » — une ambiguïté qui n'existe que
        # dans nos données, facturée au joueur.
        #
        # On écarte l'accessoire de la **question**, jamais de la réponse : il
        # a sa propre recette, et l'effacer reviendrait à nier un objet qui
        # existe. C'est la règle voisine — « une variante sans réponse n'est
        # pas une réponse concurrente, mais on dit qu'elle existe ».
        armes = [(nom, texte) for nom, texte in pleins
                 if not est_un_accessoire(nom)]
        ecartes = [nom for nom, _ in pleins if est_un_accessoire(nom)]
        if ecartes and armes and not nomme_un_accessoire(f"{question} {terme}"):
            gardes, absorbes = _absorber(armes)
            if len(gardes) == 1:
                return {"question": [],
                        "partagent": [n for n, _ in armes
                                      if n != gardes[0][0]],
                        "sans_reponse": [n for n, _, s in rendus if s],
                        "ecartes": ecartes, "retenu": gardes[0][0]}
            return {"question": gardes, "partagent": [],
                    "sans_reponse": [], "ecartes": ecartes}
        gardes, absorbes = _absorber(pleins)
        if len(gardes) == 1:
            return {"question": [],
                    "partagent": [n for n, _ in pleins if n != gardes[0][0]],
                    "sans_reponse": [n for n, _, s in rendus if s],
                    "retenu": gardes[0][0]}
        return {"question": gardes, "partagent": [],
                "sans_reponse": [n for n, _, s in rendus if s]}

    retenu = pleins[0][0] if pleins else rendus[0][0]
    return {
        "question": [],
        "partagent": [nom for nom, _ in pleins[1:]],
        "sans_reponse": [nom for nom, _, sans in rendus if sans],
        "retenu": retenu,
    }


def meme_question_autre_sujet(con: sqlite3.Connection, session: str | None,
                              question: str) -> router.ToolCall | None:
    """« Et l'iron ? » après « où raffiner de l'hephaestanite ».

    Une conjonction et une entité, rien d'autre : c'est la **même question
    sur un nouveau sujet**. Le routeur ne peut pas la comprendre — elle n'a
    aucun mot d'intention — et la reprise du contexte non plus, « iron »
    n'étant dans aucun vocabulaire de proposition. Journal du 2026-08-07 :
    « et l'iron » mourait en « Je n'ai pas compris la question ».

    Le garde-fou est double : l'entité doit résoudre franchement dans les
    types de l'outil précédent, et **tous** les mots restants doivent lui
    appartenir — une vraie question nouvelle a ses propres mots, et elle
    n'arrive de toute façon jamais ici puisqu'elle se route.
    """
    from .context import _LIANTS
    from .normalize import normalize

    precedent = context.dernier(session)
    if precedent is None:
        return None
    outil = router.TOOLS.get(precedent.outil)
    if outil is None or not outil.entity_types:
        return None
    # « pour » manquait : « et pour le p6-lr ? » gardait un mot inexpliqué
    # et la reprise mourait chez l'analyste — 24 s pour rejouer le même
    # outil (journal du 2026-08-13).
    mots = [m for m in normalize(question).split()
            if m not in _LIANTS
            and m not in ("aussi", "egalement", "pareil", "idem", "plutot",
                          "pour")]
    if not mots:
        return None
    # **On résout le terme débarrassé des liants, pas la phrase.** Sur
    # « et pour le p8 ar », la phrase entière sortait la *Tourelle de Nez
    # Hornet* à 85 — le « pour » d'un alias français pesait plus que
    # l'arme ; « p8 ar » seul sort le P8-AR à 93. Le P6-LR ne passait que
    # par chance, ses alias longs flattant le ratio partiel (mesuré,
    # 2026-08-13).
    best = resolve_entity(con, " ".join(mots),
                          entity_types=outil.entity_types, limit=1).best
    if best is None or best.score < 85.0:
        return None
    if mots_inexpliques(" ".join(mots), best.alias):
        return None
    # **Un nombre nu n'est pas un sujet** — transposé ici : « les 3 » après
    # « tu veux X, Y ou Z ? » résolvait *CBH-3 Helmet Red* et répondait la
    # recette d'un casque au milieu d'une conversation sur des missions.
    if all(m.isdigit() for m in mots):
        return None
    args = {outil.arg: best.name}
    # **Pour le raffinage, la conjonction cumule.** « Et l'iron » après
    # l'hephaestanite veut les deux dans la même soute : on garde le minerai
    # précédent et la réponse devient le croisement.
    if precedent.outil == "ou_raffiner" and precedent.entite:
        args["minerais"] = [precedent.entite]
    # **La reprise du point unique garde sa cible.** « Et pour le p8-ar ? »
    # après « combien de balles dans le torse une armure lourde » doit
    # rester torse + lourde : ne passer que `query` retombait sur les
    # défauts de signature — la cible réinventée que le sprint 27 vient
    # d'interdire, par la porte de la reprise. La cible se relit dans la
    # question précédente : c'est elle, la « même question ».
    if precedent.outil == "qualite_pour_tuer" and precedent.question:
        from . import armure as armure_mod
        from . import qualite as qualite_mod
        zone = qualite_mod.detect_zone(precedent.question)
        classe = armure_mod.detect_classe(precedent.question)
        if zone:
            args["zone"] = zone
        if classe:
            args["classe"] = classe
        if qualite_mod.demande_un_compte_de_balles(precedent.question):
            args["volet"] = "balles"
        elif qualite_mod.demande_des_degats(precedent.question):
            args["volet"] = "degats"
        args["question"] = precedent.question
    return router.ToolCall(tool=precedent.outil, args=args, confidence=0.85,
                           via="meme_question",
                           entity_score=round(best.score, 1))


def certitude_insuffisante(con: sqlite3.Connection, call: router.ToolCall,
                           question: str) -> list[str]:
    """Les candidats à proposer quand le terme tapé n'est pas expliqué.

    Le score ne dit pas si une correspondance est fiable — mesuré sur dix-neuf
    cas : « gladiousse », qui est juste, sort à 82 ; « omnisky xi », qui est
    faux, sort à 90. Aucun seuil ne les sépare. Ce qui les sépare, c'est que
    « gladiousse » est entièrement expliqué par « Gladius » alors que le « xi »
    d'« omnisky xi » ne correspond à rien dans « Omnisky XII Cannon ».

    Répondre quand même donnait le pire cas : une réponse chiffrée, détaillée,
    et sur un autre objet.
    """
    outil = router.TOOLS.get(call.tool)
    if outil is None or not outil.entity_types:
        return []
    terme = call.args.get(outil.arg)
    if not isinstance(terme, str) or not terme.strip():
        return []

    resolution = resolve_entity(con, terme, entity_types=outil.entity_types, limit=6)
    if resolution.best is None:
        return []

    # Une question peut nommer plusieurs entités — « de Crusader à Wala dans
    # un Gladius » en nomme trois. Un mot n'est inexpliqué que si **aucune**
    # d'elles ne le justifie, sinon le contrôle doutait de toute question à
    # deux lieux.
    aliases = [resolution.best.alias]
    for cle in ("to", "ship", "drive", "cible", "arme", "bouclier", "vias"):
        valeur = call.args.get(cle)
        # `vias` porte une liste de détours — chacun explique ses mots.
        valeurs = valeur if isinstance(valeur, list) else [valeur]
        for v in valeurs:
            if isinstance(v, str) and v.strip():
                autre = resolve_entity(con, v, limit=1).best
                if autre is not None:
                    aliases.append(autre.alias)

    tapes = mots_d_entite(question, terme)
    # **Un nombre déjà consommé comme qualité ne nomme pas l'entité.**
    # « Différence entre un p6lr 900 et 990 » : « 900 » colle au gramme, donc
    # `mots_d_entite` le retient, et il n'existe dans aucun alias du fusil —
    # le contrôle doutait d'une question limpide. Le routeur a pourtant déjà
    # rangé ces nombres dans les arguments : c'est là qu'on vérifie.
    # Le compte de la bataille (« 5 gladius ») est consommé comme la
    # qualité : un « 5 » orphelin faisait douter d'une question limpide.
    consommes = {str(int(v))
                 for cle in ("qualite", "qualite_a", "qualite_b", "n")
                 if isinstance(v := call.args.get(cle), (int, float))}
    tapes = [t for t in tapes if t not in consommes]
    if not tapes:
        return []
    orphelins = set(mots_inexpliques(" ".join(tapes), aliases[0]))
    for alias in aliases[1:]:
        orphelins &= set(mots_inexpliques(" ".join(tapes), alias))
    if not orphelins:
        return []

    # Proposer des candidats faibles serait remplacer une erreur par trois.
    # Le seuil est celui du routeur : en dessous, ce n'est plus une piste.
    from .router.deterministic import MIN_ENTITY_SCORE

    # **Le dédoublonnage se fait sur le nom affiché, pas sur l'UUID.** Deux
    # entités distinctes portent le même nom en base — mesuré, « je peux aller
    # jusque ruin station » demandait « Ruin Station, QV Breaker Station, ou
    # QV Breaker Station ? ». Un choix proposé deux fois n'est pas un choix, et
    # le joueur ne peut pas les départager puisqu'il lit la même chose. Même
    # règle que `compare_ships`, qui annonçait « Cutlass Black passe devant
    # Cutlass Black ».
    propositions, vus = [], set()
    for candidat in resolution.candidates:
        if candidat.score < MIN_ENTITY_SCORE:
            continue
        if candidat.name is None or candidat.name in vus:
            continue
        vus.add(candidat.name)
        propositions.append(candidat.name)
        if len(propositions) == 3:
            break
    # Un seul nom distinct : il n'y a plus de question à poser. Sans ça, on
    # demandait « tu veux dire Orbituary ? » pour une question limpide — trois
    # occurrences au journal, toutes commentées « ça devrait marcher ».
    return propositions if len(propositions) > 1 else []


def _propositions(call: router.ToolCall, noms: list[str]) -> list[context.Suite]:
    """Chaque candidat devient une proposition, donc « titan » suffit."""
    outil = router.TOOLS[call.tool]
    suites = []
    for nom in noms:
        args = dict(call.args)
        args[outil.arg] = nom
        suites.append(context.Suite(nom, call.tool, args,
                                    mots=_mots_distinctifs(nom, noms),
                                    entite_proposee=True))
    return suites


def _mots_distinctifs(nom: str, tous: list[str]) -> tuple[str, ...]:
    """Ce qui distingue ce nom des autres — « titan », pas « aegis avenger ».

    C'est ce qui permet de répondre « titan » plutôt que de réécrire
    « Aegis Avenger Titan » en entier.
    """
    from .normalize import normalize

    partages = set.intersection(*(set(normalize(n).split()) for n in tous)) \
        if len(tous) > 1 else set()
    propres = tuple(m for m in normalize(nom).split() if m not in partages)
    # Un nom entièrement partagé ne se distingue plus : on garde tous ses mots
    # plutôt que rien, quitte à ce que la reprise en désigne deux.
    return propres or tuple(normalize(nom).split())


def precedent_entite(session: str | None) -> str | None:
    """L'entité dont on parlait, pour la reconduire après une reprise."""
    precedent = context.dernier(session)
    return precedent.entite if precedent else None


def _vaisseaux_de_combat(outil: str | None, data: Any) -> tuple[str, ...]:
    """Les deux bords d'un duel qui vient d'être rendu, sinon rien."""
    if outil in ("peut_detruire", "bataille"):
        paire = ((_champ(data, "attaquant") or {}).get("name"),
                 (_champ(data, "defenseur") or {}).get("name"))
    elif outil == "matchups_vaisseau" and _champ(data, "direct"):
        paire = ((_champ(data, "vaisseau") or {}).get("name"),
                 ((_champ(data, "cible") or {}) or {}).get("name"))
    else:
        return ()
    return tuple(n for n in paire if n)


#: Le vocabulaire qui rattache une phrase au duel en cours. Fermé et
#: mesuré sur le journal du 2026-08-12 : « vaisseaux 2 joueurs », « dps
#: pilote », « duel entre 4 joueurs », « toutes les statistiques », « par
#: rapport à leur taille ». Un mot suffit — le garde-fou est ailleurs :
#: il faut un duel frais, et aucune entité étrangère franche.
_MOTS_DE_DUEL = frozenset({
    "joueur", "joueurs", "equipage", "tourelle", "tourelles", "poste",
    "postes", "pilote", "dps", "duel", "duels", "statistique",
    "statistiques", "stats", "agilite", "taille", "vitesse",
    "acceleration", "accelerations", "maniabilite", "silhouette",
    "matchup", "matchups",
})


def _reprise_de_duel(con: sqlite3.Connection,
                     precedent: context.Contexte | None,
                     question: str,
                     autonome: bool) -> router.ToolCall | None:
    """« Tu as oublié que c'était des vaisseaux 2 joueurs » corrige le duel.

    La séquence du journal du 2026-08-12 : cette correction sortait la fiche
    d'un casque à 0,69, et « Scorpius et Hurricane, prends bien en compte
    toutes les statistiques » partait en `chaine_de_qualites` (0,86) puis
    proposait des **livrées**. Une phrase qui parle du duel en cours rejoue
    le matchup — c'est lui qui porte le feu par poste et l'agilité.

    Deux régimes : une question qui ne route pas sûrement se reprend dès
    qu'elle parle le vocabulaire du duel ; une question routée avec
    confiance ne se fait reprendre que si elle nomme **les deux** vaisseaux
    de la paire — là, la coïncidence n'existe pas.
    """
    from .normalize import normalize

    if precedent is None or len(precedent.vaisseaux) != 2:
        return None
    mots = set(normalize(question).split())
    if not (mots & _MOTS_DE_DUEL):
        return None

    a, b = precedent.vaisseaux
    nommes = sum(
        1 for nom in (a, b)
        if mots & (set(normalize(nom).split()) - context._LIANTS))
    if autonome and nommes < 2:
        return None

    # Une entité étrangère franchement nommée fait une question neuve :
    # « et contre un hammerhead ? » ouvre un autre duel, il ne corrige pas
    # celui-ci.
    tiers = resolve_entity(con, question, entity_types=("ship",),
                           limit=1).best
    if (tiers is not None and tiers.score >= 85.0
            and tiers.name not in (a, b)
            and not mots_inexpliques(question, tiers.alias)):
        return None

    return router.ToolCall(tool="matchups_vaisseau",
                           args={"query": a, "cible": b},
                           confidence=0.9, via="reprise_duel")


def _echange_d_analyste(con: sqlite3.Connection, session: str | None,
                        question: str, reponse: str, posee: str,
                        fournisseur: str | None = None,
                        via: str = "analyste") -> Echange:
    """Une réponse LLM qui laisse la même fenêtre de contexte que le métier."""
    meilleur = resolve_entity(con, posee, limit=1).best
    context.retenir(
        session,
        meilleur.name if meilleur and meilleur.score >= 85.0 else None,
        "analyste", question=posee)
    return Echange(question=question, answered=True,
                   tool="analyste", via=via,
                   texte=reponse + "\n\n*Réponse de l'analyste "
                   f"({analyste.libelle(fournisseur)}) — chiffres lus "
                   "dans la base.*")


def _completer_si_partiel(con: sqlite3.Connection, session: str | None,
                          question: str, outil: str, data: Any,
                          texte: str,
                          limites: list[str] | None = None
                          ) -> Echange | None:
    """Confie une réponse incomplète au LLM, quel que soit son chemin."""
    limites = (raisons_reponse_incomplete(outil, data)
               if limites is None else limites)
    if not limites or not analyste.disponible():
        return None
    apercu = resultats.apercu_pour_analyste(data)
    demande = (
        f"Question du joueur : {question}\n"
        f"Réponse déterministe déjà vérifiée : {texte}\n"
        f"Limites structurées détectées : {', '.join(limites)}.\n"
        f"Données déterministes structurées (aperçu borné) : {apercu}\n"
        "Produis la réponse finale la plus complète possible en gardant "
        "les faits vérifiés, en cherchant les jointures complémentaires "
        "dans la base, et en disant explicitement ce qui reste absent.")
    reponse = analyste.repondre(demande, session=session)
    if not reponse:
        return None
    return _echange_d_analyste(
        con, session, question, reponse, question,
        via="analyste_apres_partiel")


def _executer(con: sqlite3.Connection, session: str | None, question: str,
              call: router.ToolCall) -> Echange | None:
    """Exécute un appel déjà tranché et rend l'échange, ou `None` s'il échoue.

    Sert le chemin du **complément** : l'appel n'est pas issu du routeur mais
    du tour précédent, auquel il ne manquait qu'un argument. Rendre `None`
    plutôt que de lever laisse l'appelant retomber sur le traitement normal —
    une phrase qui ressemblait à un complément mais n'en était pas ne doit pas
    faire perdre la question.
    """
    try:
        data = router.execute(con, call)
    except NotFound:
        return None

    resolution = _champ(data, "resolution")
    entity = resolution.best.name if resolution and resolution.best else None
    propositions = render.suites(call.tool, data)
    context.retenir(session, render.sujet(call.tool, data, entity),
                    call.tool, propositions,
                    vaisseaux=_vaisseaux_de_combat(call.tool, data))
    texte = render.render(call.tool, data)
    completee = _completer_si_partiel(
        con, session, question, call.tool, data, texte)
    if completee is not None:
        return completee
    return Echange(question=question, texte=texte, answered=True,
                   tool=call.tool, args=call.args, confidence=call.confidence,
                   via=call.via, entity=entity, data=data,
                   follow_up=[s.libelle for s in propositions])


def _traiter(con: sqlite3.Connection, session: str | None, question: str,
             *, router_name: str | None = None,
             speaker: str | None = None) -> Echange:
    """Un tour de conversation complet — le cœur de `api.ask`, sans le HTTP.

    Peut lever `ValueError` si `router_name` est inconnu : c'est à l'appelant
    d'en faire un 400 ou un échec de test.
    """
    # **Le forçage du fournisseur passe avant tout.** Même « demande à Codex »
    # après une proposition ne doit pas être avalé comme une reprise. Sans
    # texte après le préfixe, on rejoue la dernière question — c'est le bouton
    # verbal demandé par l'utilisateur quand une réponse ne lui plaît pas.
    # **La bascule passe avant le forçage** : « passe à Sonnet » n'est pas
    # une question à confier à un analyste, c'est un réglage. Elle répond
    # tout de suite et ne consomme aucun quota.
    bascule = extraire_bascule_analyste(question)
    if bascule is not None:
        etat = analyste.basculer(bascule)
        if not etat["actif"]:
            texte = (f"L'analyste est éteint sur ce cœur — je note "
                     f"{etat['libelle']} pour quand il sera allumé "
                     f"(DISCO_ANALYSTE).")
        elif not etat["cli_present"]:
            texte = (f"Je passe à **{etat['libelle']}**, mais son CLI n'est "
                     f"pas installé sur cette machine : les questions "
                     f"d'analyse resteront sans réponse tant qu'il manque.")
        else:
            texte = (f"C'est noté — les analyses passent par "
                     f"**{etat['libelle']}** à partir de maintenant. "
                     f"Le réglage tient jusqu'au prochain redémarrage du "
                     f"cœur.")
        return Echange(question=question, answered=True, tool="analyste",
                       via="bascule", texte=texte)

    fournisseur_force, demande_forcee = extraire_forcage_analyste(question)
    if fournisseur_force is not None:
        precedent_force = context.dernier(session)
        demande = demande_forcee or (
            precedent_force.question if precedent_force else "")
        if not demande:
            return Echange(
                question=question, answered=False, echec="analyste_sans_question",
                tool="analyste", via="analyste_force",
                texte="Je n'ai pas de question précédente à confier à "
                      f"{analyste.libelle(fournisseur_force)}. Nomme-la après "
                      f"« demande à {fournisseur_force.title()} ».")
        if not analyste.disponible(fournisseur_force):
            # **Le message doit dire la vraie cause.** « Sonnet est
            # indisponible sur cette machine » était faux dans le cas mesuré
            # au journal du 2026-08-11 : le CLI était installé, c'est
            # l'interrupteur (`DISCO_ANALYSTE`) qui manquait, parce que le
            # cœur avait été relancé sans passer par le lanceur. Deux causes,
            # deux remèdes — un message qui les confond n'aide personne.
            if analyste._commande_cli(fournisseur_force) is None:
                texte = (f"Le CLI de {analyste.libelle(fournisseur_force)} "
                         "n'est pas installé sur la machine du cœur ; je n'ai "
                         "pas remplacé ton choix par une réponse déterministe.")
            else:
                texte = ("L'analyste est éteint sur ce serveur — il a "
                         "probablement été démarré sans passer par le "
                         "lanceur. Relance le serveur depuis le lanceur, ou "
                         "pose `setx DISCO_ANALYSTE sonnet` une fois pour "
                         "toutes sur cette machine.")
            return Echange(
                question=question, answered=False,
                echec="analyste_indisponible", tool="analyste",
                via="analyste_force", texte=texte)
        # Même sans nouvelle phrase, le modèle voit ce qui vient d'être
        # répondu : « demande à Codex » signifie précisément « vérifie cette
        # réponse », pas « oublie-la et repars de la question nue ».
        a_analyser = _avec_contexte_analyste(
            demande_forcee or "Vérifie et complète cette réponse.",
            precedent_force)
        a_analyser = _avec_identite_analyste(a_analyser, speaker)
        reponse_forcee = analyste.repondre(
            a_analyser, fournisseur=fournisseur_force, session=session)
        if not reponse_forcee:
            return Echange(
                question=question, answered=False, echec="analyste_echec",
                tool="analyste", via="analyste_force",
                texte=f"{analyste.libelle(fournisseur_force)} n'a pas pu "
                      "produire une réponse vérifiée ; je n'ai rien inventé "
                      "à sa place.")
        meilleur = resolve_entity(con, demande, limit=1).best
        context.retenir(
            session,
            meilleur.name if meilleur and meilleur.score >= 85.0 else None,
            "analyste", question=demande)
        return Echange(
            question=question, answered=True, tool="analyste",
            via="analyste_force", texte=reponse_forcee +
            "\n\n*Réponse forcée de l'analyste "
            f"({analyste.libelle(fournisseur_force)}) — chiffres lus dans "
            "la base.*")

    # « Oui », « les deux », « la recette » : une reprise, pas une question.
    # Elle est traitée avant le routeur, parce qu'elle n'a ni intention ni
    # entité à lui offrir — c'est la proposition précédente qui porte les deux.
    def _nomme_un_sujet(mot: str) -> bool:
        """Ce mot désigne-t-il une entité du jeu, ou qualifie-t-il seulement ?

        Le seuil de 85 est celui du résolveur pour une correspondance franche.
        Mesuré sur les mots qui se présentent ici : « gladius » sort à 93,
        « 890 » à 93, « titan » à 93 — des sujets. « projectile » sort à 80,
        « recharge » à 80, « vitesse » à 59 — des qualificatifs.
        """
        meilleur = resolve_entity(con, mot, limit=1).best
        return meilleur is not None and meilleur.score >= 85.0

    # **Une question autonome passe avant la mémoire.** La mémoire sert aux
    # fragments (« en Gladius », « le blueprint »), jamais à réinterpréter
    # une phrase qui porte déjà une intention et son entité avec une confiance
    # normale. Deux défauts du rejeu intégral venaient du même ordre : un
    # nouveau trajet complétait l'ancien vers Orbituary, et « où se trouve le
    # blueprint de la Novian » sélectionnait la proposition « décrire ».
    appel_autonome = router.route(
        con, question, name=router_name, speaker=speaker)
    autonome = bool(
        appel_autonome is not None
        and (appel_autonome.intent_score or 0.0) > 0.0
        and bool(router.TOOLS[appel_autonome.tool].entity_types)
        and appel_autonome.confidence is not None
        and appel_autonome.confidence >= config.ROUTER_MIN_CONFIDENCE
    )

    # **Une pièce manquante se rapporte, elle ne relance pas la question.**
    # Après « avec quel vaisseau ? », « en gladius » n'a ni intention ni trajet
    # à offrir au routeur : c'est l'appel précédent qui les porte, il ne lui
    # manquait qu'un argument. On l'accepte seulement si la phrase résout
    # vraiment le type attendu — sinon toute question suivante serait avalée.
    complement = None if autonome else context.complement_attendu(session)
    if complement is not None:
        # **On résout le terme débarrassé des liants, pas la phrase.**
        # Mesuré le 2026-08-13 : après « avec quel vaisseau ? », les
        # réponses « gladius », « un gladius » et « en gladius » passaient,
        # mais **« avec un gladius » cassait la conversation** — le mot
        # même que la question venait d'employer. Le résolveur ne rendait
        # rien à 85 sur la phrase entière.
        #
        # Et le liant ne fait pas que bloquer : il **déplace** la réponse.
        # « un gladius » sortait *Gladius Pirate* là où « gladius » sort
        # l'*Aegis Gladius* — l'article suffisait à changer de vaisseau.
        # Même leçon que `meme_question_autre_sujet` au sprint 29.
        from .normalize import est_grammatical, normalize

        terme = " ".join(m for m in normalize(question).split()
                         if not est_grammatical(m)) or question
        trouve = resolve_entity(con, terme,
                                entity_types=complement.complete_types,
                                limit=1).best
        if trouve is not None and trouve.score >= 85.0:
            args = dict(complement.args)
            args[complement.complete_avec] = trouve.name
            echange = _executer(con, session, question,
                                router.ToolCall(tool=complement.outil,
                                                args=args, confidence=1.0,
                                                via="complement"))
            if echange is not None:
                return echange

    reprises = ([] if autonome else
                context.choisir(session, question, _nomme_un_sujet))
    # **Plusieurs reprises du même outil ne s'additionnent pas, elles
    # s'excluent.** « Où trouver ces matériaux » vise trois ressources et les
    # trois réponses se cumulent — c'est voulu. « Où en acheter » après
    # trente-deux optiques vise trois objets **concurrents** : les enchaîner
    # donnerait trois prix pour une question qui n'en demandait qu'un, et
    # choisir à la place du joueur lui donnerait le mauvais. On demande.
    if len(reprises) > 1 and len({s.args.get("query") for s in reprises}) > 1 \
            and len({s.outil for s in reprises}) == 1 \
            and reprises[0].outil == "get_price":
        texte = render.question_de_suite([s.libelle for s in reprises])
        return Echange(question=question, texte=texte, answered=True,
                       via="reprise",
                       follow_up=[s.libelle for s in reprises])
    if reprises:
        morceaux, dernier_outil, dernieres_suites = [], None, []
        resultats_reprises = []
        derniere_data = None
        for suite in reprises:
            try:
                data = router.execute(
                    con, router.ToolCall(tool=suite.outil,
                                         args=dict(suite.args),
                                         confidence=1.0, via="reprise"))
            except NotFound:
                # **Ne pas faire porter l'échec à la proposition.** « Je n'ai
                # finalement pas Nyx » se lit comme un aveu absurde : le libellé
                # d'une reprise n'est pas une phrase française, et le joueur
                # vient surtout de taper quelque chose qu'on n'a pas compris.
                # Remarque du journal.
                morceaux.append(
                    "Je n'ai pas compris — reformule, ou nomme le vaisseau, "
                    "l'objet ou le lieu dont tu parles.")
                continue
            morceaux.append(render.render(suite.outil, data,
                                          exhaustif=suite.exhaustif))
            resultats_reprises.append({"outil": suite.outil, "data": data})
            dernier_outil, derniere_data = suite.outil, data
            # **Une reprise exhaustive vient de tout dire.** Sans le signaler,
            # `suites()` reproposait la liste qu'on venait d'afficher, et un
            # « oui » la réaffichait indéfiniment. Mesuré dans le journal :
            # trois « oui » de suite pour la même liste de missions.
            dernieres_suites = render.suites(
                suite.outil, data, deja_tout_dit=suite.exhaustif)
        if dernier_outil and precedent_entite(session):
            context.retenir(session, precedent_entite(session),
                            dernier_outil, dernieres_suites,
                            vaisseaux=_vaisseaux_de_combat(dernier_outil,
                                                           derniere_data))
        texte_reprises = "\n\n".join(morceaux)
        limites_reprises = list(dict.fromkeys(
            raison
            for resultat in resultats_reprises
            for raison in raisons_reponse_incomplete(
                resultat["outil"], resultat["data"])))
        completee = _completer_si_partiel(
            con, session, question, dernier_outil or "reprise",
            {"reponses": resultats_reprises}, texte_reprises,
            limites=limites_reprises)
        if completee is not None:
            return completee
        return Echange(
            question=question, texte=texte_reprises,
            answered=bool(dernier_outil), tool=dernier_outil, via="reprise",
            data=derniere_data,
            follow_up=[s.libelle for s in dernieres_suites])

    # **Refuser est une réponse — et un refus appelle une autre proposition.**
    # « Non » rendait d'abord « je n'ai pas compris la question », ce qui
    # accuse le joueur d'avoir mal parlé alors qu'il a répondu exactement ce
    # qu'il fallait. La correction suivante disait « d'accord, je laisse
    # tomber » : polie, et tout aussi inutile. Remarque de l'utilisateur —
    # « si je dis non, essayer de proposer quelque chose de différent ».
    #
    # On écarte donc ce qui vient d'être refusé et on **propose la suite**. Ce
    # n'est qu'à défaut d'autre chose qu'on s'efface.
    en_attente = context.propositions(session)
    if context.est_refus(question) and not en_attente:
        # Un « non » sans rien en attente n'est pas une question incomprise
        # non plus : on le reçoit, sans faire semblant d'avoir une suite.
        return Echange(question=question, answered=True, via="refus",
                       texte="D'accord. Dis-moi ce que tu cherches et j'y "
                             "vais.")
    if en_attente and context.est_refus(question):
        refusee = next((s for s in en_attente if s.defaut), en_attente[0])
        autres = [s for s in en_attente if s is not refusee]
        if autres:
            context.retenir(session, precedent_entite(session),
                            refusee.outil, autres)
            texte = render.question_de_suite([s.libelle for s in autres])
        else:
            context.oublier(session)
            texte = ("D'accord. Je n'ai rien d'autre à proposer là-dessus — "
                     "demande-moi autre chose.")
        return Echange(question=question, texte=texte, answered=True,
                       via="refus", follow_up=[s.libelle for s in autres])

    # Un « oui » qu'on n'a pas su trancher n'est pas une question incomprise :
    # c'est une proposition ambiguë. On redemande au lieu d'abandonner.
    attente = context.propositions(session)
    if len(attente) > 1 and context.est_acquiescement(question):
        texte = render.question_de_suite([s.libelle for s in attente])
        return Echange(question=question, texte=texte, answered=True,
                       via="reprise",
                       follow_up=[s.libelle for s in attente])

    precedent = context.dernier(session)

    # **Les reprises du duel restent dans le duel.** Avant l'analyste et
    # avant le routeur contextuel : la correction du journal partait chez
    # l'un ou l'autre, et aucun des deux ne connaissait la paire en cours.
    #
    # Deux exceptions arbitrées le 2026-08-12, même mécanique : une
    # demande de **format** hors outil (« 7/10 », « sur 10 combats ») part
    # directement à l'analyste avec la paire ; et si le rendu serait
    # **identique** au tour précédent (« prends en compte le boost », trois
    # fois le même texte au journal), l'analyste reprend aussi — jamais
    # deux fois la même réponse à deux questions différentes.
    duel_repris = _reprise_de_duel(con, precedent, question,
                                   autonome=autonome)
    if duel_repris is not None:
        from .normalize import normalize as _norm
        # « 7/10 » se lit sur le texte brut — normalize retire la barre,
        # même piège que « la 4.10 » du méta.
        format_demande = bool(
            re.search(r"\d\s*/\s*\d", question)
            or re.search(r"\bsur \d+ combats?\b|\bformat\b|"
                         r"\bpourcentage\b|\bproba\w*\b", _norm(question)))
        if format_demande and analyste.disponible():
            reponse = analyste.repondre(
                _prompt_analyste(session, question, speaker), session=session)
            if reponse:
                return _echange_d_analyste(
                    con, session, question, reponse, question,
                    via="analyste_apres_format_duel")
        echange = _executer(con, session, question, duel_repris)
        if echange is not None:
            if (precedent is not None and precedent.reponse
                    and echange.texte == precedent.reponse
                    and analyste.disponible()):
                reponse = analyste.repondre(
                    _prompt_analyste(session, question, speaker),
                    session=session)
                if reponse:
                    return _echange_d_analyste(
                        con, session, question, reponse, question,
                        via="analyste_apres_reprise_identique")
            return echange

    # **Le vocabulaire d'analyse passe devant le routeur.** « Le ratio
    # DPS/coût de la balle entre un P4-AR et un P6-LR » partait chez
    # get_item_stats par le mot « dps » et recevait la fiche d'une seule
    # arme — exacte et à côté. Si l'analyste échoue, le routeur reprend la
    # main normalement.
    if analyste.disponible() and analyste.question_d_analyse(question):
        reponse = analyste.repondre(
            _prompt_analyste(session, question, speaker), session=session)
        if reponse:
            return _echange_d_analyste(
                con, session, question, reponse, question)

    call = (appel_autonome if autonome else
            router.route(con, question, name=router_name,
                         contexte=precedent.entite if precedent else None,
                         speaker=speaker))

    # Un nom nu est routé vers la description générale avec un score
    # d'intention nul. Dans une conversation, « P8-AR Rifle Magazine » juste
    # après « comment fabriquer le P8-AR » signifie pourtant « même question,
    # autre objet ». La reprise typée est plus informative que ce repli neutre.
    meme_question = meme_question_autre_sujet(con, session, question)
    if call is None or (call.tool == "decrire"
                        and (call.intent_score or 0.0) == 0.0
                        and precedent is not None
                        and precedent.outil == "get_blueprint"
                        and meme_question is not None):
        # « Et l'iron ? » : la même question sur un nouveau sujet — dernier
        # recours avant d'avouer, voir `meme_question_autre_sujet`.
        call = meme_question
    # **Un routage douteux répond quand même, et le dit.** Révision de la
    # décision du 2026-08-07 par l'utilisateur le 2026-08-10 : sous le seuil,
    # on consultait l'analyste d'office — 10 à 100 s et du quota — avant de
    # retomber, huit fois sur dix, sur la réponse déterministe qu'on avait
    # déjà. Mesuré sur le corpus le 2026-08-10 : **37 questions sur 338**
    # étaient dans ce cas.
    #
    # On sert donc la réponse tout de suite, avec une réserve visible et une
    # proposition exécutable — « demande à Claude ». Le joueur arbitre :
    # c'est lui qui sait si la réponse tombe à côté, et c'est son quota.
    doute_de_routage = (call is not None and call.confidence is not None
                        and call.confidence < config.ROUTER_MIN_CONFIDENCE)

    # **La bande basse : l'étage 1 ne sait pas servir cette question.** Deux
    # sorties en découlent, dans cet ordre — la conversation, puis l'analyste.
    incapable = (call is None
                 or (call.confidence is not None
                     and call.confidence < config.ROUTER_INCAPABLE_CONFIDENCE))

    # **Ce qui n'est pas une question de jeu se règle ici, pas au LLM.**
    # « t'es offline ? », « keske », « 1 min pour ça », « la distance entre
    # la Terre et Proxima b » : mesuré le 2026-08-10, chacune coûtait 10 à
    # 150 s d'analyste pour une réponse à côté ou aucune.
    #
    # **Le contrôle ne voit que ce que l'étage 1 ne sait pas servir** — rien
    # au-dessus du seuil d'incapacité. Il ne peut donc rien prendre à un
    # outil, seulement au LLM ; c'est ce qui autorise des motifs courts sans
    # garde-fou dans le motif lui-même. Et il passe **avant** l'analyste,
    # sans quoi « il y a quoi dans la 4.10 », routé à 0,56, y partait quand
    # même chercher une réponse de mémoire.
    if incapable:
        precedent = context.dernier(session)
        meta_reponse = meta.detecte(
            question, a_un_tour_precedent=bool(precedent and precedent.reponse))
        if meta_reponse is not None:
            if meta_reponse.genre in ("hors_univers", "hors_donnees"):
                # L'univers connu et le build se **comptent** dans la base :
                # un patch ne doit pas laisser une phrase fausse derrière lui.
                outil = meta_reponse.genre
                try:
                    data = getattr(meta, outil)(con, question)
                except NotFound:
                    data = None
                if data is not None:
                    return Echange(question=question, answered=True,
                                   texte=render.render(outil, data),
                                   tool=outil, via="meta",
                                   confidence=1.0, data=data)
            else:
                texte = meta_reponse.texte
                if meta_reponse.suite:
                    texte += f"\n\n_Par exemple : {meta_reponse.suite}._"
                return Echange(question=question, answered=True, texte=texte,
                               tool="conversation", via="meta",
                               confidence=1.0)

    # **Sous la bande basse, l'étage 1 est incapable — il ne réserve pas, il
    # passe la main.** Demande de l'utilisateur (2026-08-10). La réserve
    # convient quand l'outil a compris de travers ; elle ne convient pas
    # quand l'entité elle-même est douteuse, parce que la réponse porte alors
    # sur un autre objet. Si l'analyste décline, on retombe sur le routage
    # douteux, réserve comprise : un doute vaut mieux que rien.
    if call is not None and incapable and analyste.disponible():
        reponse = analyste.repondre(
            _prompt_analyste(session, question, speaker), session=session)
        if reponse:
            return _echange_d_analyste(
                con, session, question, reponse, question,
                via="analyste_apres_incapacite")

    if call is None:
        # **L'analyste** — Sol ou Sonnet sur l'abonnement, éteint par
        # défaut (`DISCO_ANALYSTE` l'active). C'est l'étage des
        # questions que la donnée sait servir mais qu'aucun outil ne
        # formule : « le ratio DPS/coût de la balle entre un P4-AR et un
        # P6-LR ». Il lit la base en lecture seule et passe la main sur
        # tout le reste — en panne comme hors sujet, on retombe ici.
        if analyste.disponible():
            # **« Et le P6-LR ? » se recompose avec la question d'avant.**
            # Une suite courte n'a ni intention ni outil — c'est le tour
            # précédent qui les porte. `_prompt_analyste` joint la paire Q/R
            # fraîche pour tout le monde ; on garde la question **nue** en
            # contexte, pour que le tour suivant ne voie pas le prompt composé.
            reponse = analyste.repondre(
                _prompt_analyste(session, question, speaker), session=session)
            if reponse:
                return _echange_d_analyste(
                    con, session, question, reponse, question)
        # Passer la main est un résultat légitime — on consigne côté API.
        return Echange(question=question, answered=False, echec="no_intent",
                       texte="Je n'ai pas compris la question.")

    # Le doute passe avant l'ambiguïté : si on n'est pas sûr de l'objet, se
    # demander de quelle variante il s'agit n'a pas de sens.
    doutes = certitude_insuffisante(con, call, question)
    if doutes:
        context.retenir(session, doutes[0], call.tool,
                        _propositions(call, doutes))
        return Echange(question=question, answered=True,
                       texte=render.question_de_doute(doutes),
                       tool=call.tool, args=call.args,
                       confidence=call.confidence, via="doute",
                       follow_up=doutes)

    # Plusieurs variantes, et la réponse en dépend : demander plutôt que
    # choisir à la place du joueur. Les variantes deviennent des propositions,
    # donc « titan » suffit à trancher — pas besoin de reposer la question.
    variantes = variantes_ambigues(con, call, question)
    if variantes["question"]:
        choix = variantes["question"]
        context.retenir(session, choix[0][0], call.tool,
                        _propositions(call, [nom for nom, _ in choix]))
        return Echange(question=question, answered=True,
                       texte=render.question_de_variante(
                           [nom for nom, _ in choix]),
                       tool=call.tool, args=call.args,
                       confidence=call.confidence, via="ambigu",
                       follow_up=[nom for nom, _ in choix])

    try:
        data = router.execute(con, call)
    except NotFound:
        # Un outil peut viser la bonne famille mais manquer une jointure. Le
        # LLM relit alors toute la base avant qu'on conclue à l'absence ; il
        # reste tenu par le même accès SQLite en lecture seule.
        if analyste.disponible():
            reponse = analyste.repondre(
                _prompt_analyste(session, question, speaker), session=session)
            if reponse:
                return _echange_d_analyste(
                    con, session, question, reponse, question,
                    via="analyste_apres_absence")
        # Une table annexe vide (réingestion sans « disco uex/trad/wiki »)
        # produit exactement ce NotFound — et c'est le pire cas parce qu'il
        # est invisible. Si la base est incomplète, on le dit.
        return Echange(question=question, answered=False, echec="no_data",
                       texte="J'ai compris la question mais je n'ai pas "
                             "trouvé la donnée."
                             + erreurs.note_de_tables_vides(con),
                       tool=call.tool, args=call.args,
                       confidence=call.confidence, via=call.via)
    except Exception as exc:  # une panne doit se dire, pas se perdre en 500
        logging.getLogger(__name__).exception(
            "panne dans %s(%s)", call.tool, call.args)
        return Echange(question=question, answered=False, echec="crash",
                       texte=erreurs.message_de_crash(call.tool, call.args,
                                                      exc),
                       tool=call.tool, args=call.args,
                       confidence=call.confidence, via=call.via)

    resolution = _champ(data, "resolution")
    entity = resolution.best.name if resolution and resolution.best else None
    # Retenir l'entité pour la question suivante. On garde le **nom résolu** et
    # non le terme tapé : « liste tous les points de vente » repassera par le
    # résolveur, et un nom officiel s'y résout mieux qu'une approximation.
    # On retient aussi ce qui vient d'être **proposé** : sans ça, « oui » n'a
    # rien à quoi se rattacher.
    propositions = render.suites(
        call.tool, data,
        # Ce qui vient d'être dit en entier ne se repropose pas.
        deja_tout_dit=bool(question and _veut_tout(question)))
    # Le sujet n'est pas toujours l'entité interrogée : « quelles optiques vont
    # sur un P8-AR » parle des optiques, et c'est à elles que « les » renverra.
    context.retenir(session, render.sujet(call.tool, data, entity),
                    call.tool, propositions,
                    vaisseaux=_vaisseaux_de_combat(call.tool, data))
    texte = render.render(call.tool, data,
                          exhaustif=question and _veut_tout(question))
    # Les variantes qui donnent la même réponse, et celles qui n'en ont pas :
    # on ne pose pas de question pour elles, mais on ne les efface pas non
    # plus — savoir que le chargeur suit le même farm, et que les déclinaisons
    # cosmétiques ne donnent rien, fait partie de la réponse.
    texte += render.note_de_variantes(variantes.get("partagent"),
                                      variantes.get("sans_reponse"),
                                      variantes.get("ecartes"))
    texte += render.reserve_de_confiance(doute_de_routage)
    completee = _completer_si_partiel(
        con, session, question, call.tool, data, texte)
    if completee is not None:
        return completee
    return Echange(
        question=question, texte=texte, answered=True,
        tool=call.tool, args=call.args, confidence=call.confidence,
        via=call.via, entity=entity, data=data,
        follow_up=[s.libelle for s in propositions])


def traiter(con: sqlite3.Connection, session: str | None, question: str,
            *, router_name: str | None = None,
            speaker: str | None = None) -> Echange:
    """Traite un tour puis garde une fenêtre de contexte après toute réponse.

    Les branches métier continuent de retenir leur sujet et leurs propositions
    au moment exact où elles les connaissent. Cette enveloppe ajoute la paire
    question/réponse sans dupliquer leurs dizaines de retours anticipés.
    """
    avant = context.dernier(session)
    # **Répéter un choix répète sa réponse.** Une reprise consommée n'a plus
    # de propositions, mais sa paire question/réponse reste dans la fenêtre.
    # Le journal contient « encaisser » puis « ENCAISSER » : la seconde forme
    # rendait une incompréhension alors qu'elle est textuellement identique.
    # On ne rejoue ni un échec ni une commande d'analyste : leur état externe
    # peut avoir changé entre les deux essais.
    from .normalize import normalize
    repetition = bool(
        avant and avant.question and avant.reponse
        and normalize(avant.question) == normalize(question)
        and avant.outil in router.TOOLS
        and not context.est_acquiescement(question)
        and not context.est_refus(question)
        and "je n'ai pas compris" not in avant.reponse.lower()
    )
    if repetition:
        echange = Echange(
            question=question, answered=True, tool=avant.outil,
            via="meme_reponse", texte=avant.reponse,
            entity=avant.entite)
    else:
        echange = _traiter(
            con, session, question, router_name=router_name, speaker=speaker)
    apres = context.dernier(session)
    if session and echange.texte:
        mis_a_jour = apres is not None and apres is not avant
        entite = apres.entite if apres else (avant.entite if avant else None)
        outil = (apres.outil if apres else None) or echange.tool or "conversation"
        suites = (apres.suites if mis_a_jour and apres else
                  (avant.suites if avant and echange.follow_up else ()))
        # `_echange_d_analyste` a déjà choisi la question non recomposée à
        # garder ; toutes les autres branches mémorisent le texte reçu.
        posee = (apres.question if mis_a_jour and apres and apres.question
                 else question)
        # La paire de duel survit tant qu'une nouvelle ne la remplace pas —
        # la séquence réelle du journal intercale un « demande à Claude »
        # entre le duel et sa correction, et l'analyste ne porte aucune
        # paire. La fenêtre de quinze minutes borne cette mémoire comme le
        # reste du contexte.
        vaisseaux = ((apres.vaisseaux if apres and apres.vaisseaux else None)
                     or (avant.vaisseaux if avant else ()))
        context.retenir(session, entite, outil, suites,
                        question=posee, reponse=echange.texte,
                        vaisseaux=vaisseaux)
    return echange
