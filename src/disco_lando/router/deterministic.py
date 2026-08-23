"""Étage 1 du routeur — déterministe, toujours actif, chemin rapide.

Le §7 du brief : « Matcher d'intent par mots-clés/regex + résolution d'entité.
Les types de questions sont peu nombreux et très typés. Couvre la majorité des
requêtes réelles en < 200 ms. **Ne pas le sauter même si un LLM est
disponible.** »

Deux étapes indépendantes, et c'est volontaire :

1. **L'intention** vient des mots de la question — un vocabulaire fermé, court,
   que l'on peut énumérer.
2. **L'entité** ne vient jamais des mots de la question : elle vient du
   résolveur du §6, appliqué aux n-grammes de la phrase. C'est la décision
   centrale du brief — les synonymes portent sur les entités, pas sur la
   phrase.

Une intention sans entité résoluble est un échec, pas une réponse. On essaie
alors l'intention suivante : « où je choppe le blueprint de tel équipement »
déclenche à la fois la ressource et le blueprint, et c'est l'entité qui tranche.
"""

from __future__ import annotations

import re
import sqlite3

from ..guilde import questions as questions_guilde
from ..normalize import normalize
from ..resolver import mots_inexpliques, resolve
from .base import TOOLS, ToolCall

# Le découpage du 2026-08-07 a sorti les données (`motifs`), la
# lecture de la question (`analyse`) et les garde-fous par outil
# (`preparateurs`). Ce module garde le moteur — et réexporte tout :
# l'extérieur importe ici, et ne doit pas suivre le découpage.

from .motifs import (  # noqa: F401
    MIN_INTENT_SCORE,
    _COMPILED,
    _INTENTS,
    _INTENT_WORDS,
)
from .analyse import (  # noqa: F401
    MAX_NGRAM,
    MIN_ENTITY_SCORE,
    STOPWORDS,
    _ANAPHORE,
    _DETERMINANTS,
    _EXHAUSTIF,
    _HORS_ARMEMENT,
    _PROXIMITE,
    _SYSTEMS,
    _VERBE_ACHAT,
    _VERBE_FABRICATION,
    _VERBE_MISSIONS,
    _VOCABULAIRE_PAYE,
    _alias_exact,
    _contient,
    _entite_douteuse,
    _fragment_de_vaisseau,
    _hors_armement,
    _lieux_nommes,
    _mots_d_intention,
    sans_les_mots_d_un_nom,
    _ngrams,
    _nomme_une_org,
    coupe_sur_proximite,
    detect_portee,
    detect_volet,
    extract_entity,
    extract_system,
    mots_d_entite,
    reprend_le_sujet,
    veut_tout,
)
from .preparateurs import (  # noqa: F401
    Candidature,
    PREPARATEURS,
    _prep_accessoires,
    _prep_armes_par_metrique,
    _prep_au_seuil,
    _prep_blueprint,
    _prep_classer_equipement,
    _prep_compare_items,
    _prep_compare_ships,
    _prep_comparer_qualites,
    _prep_compatible_items,
    _prep_dans_la_soute,
    _prep_decrire,
    _prep_distance,
    _prep_emports_armure,
    _prep_fiche_qualite,
    _prep_hardpoints,
    _prep_item_stats,
    _prep_methode_raffinage,
    _prep_mission_group,
    _prep_missions_payantes,
    _prep_matchups_vaisseau,
    _prep_multi_criteres,
    _prep_objets_au_seuil,
    _prep_ou_acheter_pres,
    _prep_peut_voyager,
    _prep_plan_de_fabrication,
    _prep_price,
    _prep_raffinage,
    _prep_sans_composant,
    _prep_ship_components,
    _prep_ship_stats,
    _prep_trade_route,
    _prep_vaisseau_pour_budget,
    preparateur,
)


def score_intents(question: str) -> list[tuple[str, float]]:
    """Intentions candidates, la plus probable en tête.

    **À score égal, la spécificité départage — plus jamais l'ordre du
    dictionnaire.** Six paires d'outils sortaient à égalité parfaite de
    poids, et c'était l'ordre d'insertion de `_PATTERNS` qui choisissait le
    gagnant : un motif déplacé dans le fichier pouvait retourner un routage
    sans qu'aucun poids n'ait changé.

    Deux signaux, dans cet ordre, et le premier essai s'est fait battre par
    la mesure : la **couverture d'union** récompensait plusieurs matchs
    génériques — « blueprint » + « missions », 17 caractères — contre un
    seul match spécifique — « memes missions », 14 — soit l'inverse de la
    spécificité. Ce qui départage vraiment :

    1. **la contrainte d'entité** : un outil qui n'accepte qu'un type
       (`get_ship_hardpoints`, un vaisseau) est plus spécifique qu'un
       généraliste (`decrire`) ou qu'un outil sans entité. « C'est quoi
       l'armement du Cutlass » veut l'armement, pas la fiche — et aucun
       signal de texte ne le dit, « c est quoi » couvrant plus de
       caractères qu'« armement » ;
    2. **le plus long match unique** : à contrainte égale, « memes
       missions » bat « blueprint » — la phrase la plus longue reconnue
       est l'indice le plus sûr, c'est la règle des n-grammes transposée
       aux motifs.

    L'ordre alphabétique ferme la marche : le tri ne dépend plus que de la
    question. Mesuré : banc à 132/132, aucun routage du corpus déplacé.
    """
    norm = normalize(question)
    scores = []
    for tool, patterns in _COMPILED.items():
        total, plus_long = 0.0, 0
        for rx, weight in patterns:
            m = rx.search(norm)
            if m:
                total += weight
                plus_long = max(plus_long, m.end() - m.start())
        if total >= MIN_INTENT_SCORE:
            contrainte = len(TOOLS[tool].entity_types) or 99
            scores.append((tool, total, contrainte, plus_long))
    scores.sort(key=lambda item: (-item[1], item[2], -item[3], item[0]))
    return [(tool, total) for tool, total, _, _ in scores]


def route(con: sqlite3.Connection, question: str,
          *, contexte: str | None = None,
          speaker: str | None = None) -> ToolCall | None:
    """Question -> ToolCall, ou None si l'étage 1 ne sait pas.

    Renvoyer None est un résultat légitime : c'est le signal qui déclenchera
    l'étage 2 en Phase 3. Deviner serait pire que passer la main.
    """
    collectif = questions_guilde.detecter(
        question, speaker=speaker, contexte=contexte)
    if collectif is not None:
        outil, args, confiance = collectif
        return ToolCall(
            tool=outil, args=args, confidence=confiance,
            via="deterministic", intent_score=confiance * 4,
            entity_score=100.0,
        )

    doute: ToolCall | None = None
    for tool_name, intent_score in score_intents(question):
        tool = TOOLS[tool_name]
        if not tool.entity_types:
            # Outil sans entité à résoudre : la question entière lui sert
            # d'argument (« le meilleur canon balistique »). L'intention doit
            # alors être franche, faute de second garde-fou.
            if intent_score < 3.0:
                continue
            gram, entity_score = question, 100.0
        else:
            found = extract_entity(
                con, question, tool.entity_types,
                # Un mot d'intention qui forme un nom du catalogue avec son
                # voisin n'est pas de l'intention : c'est la moitié d'un nom
                # propre. 1 176 objets sont dans ce cas.
                exclus=sans_les_mots_d_un_nom(
                    con, question, _mots_d_intention(None, question)))
            # Une question de suite ne nomme rien : ce que le résolveur y
            # trouve est du bruit, et un score élevé n'y change rien —
            # « vente » ressortait en « Bexalite » à plus de 90. Quand la
            # question demande l'exhaustivité et qu'un sujet précède, c'est de
            # ce sujet qu'on parle, sans discussion.
            if found is not None and contexte and (
                    veut_tout(question) or reprend_le_sujet(question)):
                found = None
            if found is None and contexte:
                # Question de suite : « liste-moi tous les points de vente »
                # n'a pas d'entité, elle reprend celle d'avant. On repasse par
                # le résolveur plutôt que de faire confiance au texte gardé —
                # le type attendu peut différer d'un outil à l'autre.
                repris = resolve(con, contexte, entity_types=tool.entity_types,
                                 limit=1)
                # Exiger une résolution franche, et pas seulement un candidat :
                # le contexte est un **nom officiel déjà résolu**, il doit
                # ressortir à 100 dans le bon type. Sans plancher, « Coda
                # Pistol » ressortait « FR-66 » à 68 dans le type `resource`,
                # et « où trouver le blueprint » partait chercher un gisement
                # avant d'échouer. Le plancher fait retomber la question sur
                # l'intention suivante, qui est la bonne.
                if repris.best is not None and repris.best.score >= 85.0:
                    found = (contexte, repris.best.score)
            if found is None:
                continue
            gram, entity_score = found
        # L'intention est sûre si elle est bien détachée ; l'entité fait le
        # reste. On borne à 1.0.
        confidence = min(1.0, (min(intent_score, 4.0) / 4.0) * 0.4
                         + (entity_score / 100.0) * 0.6)
        args: dict = {tool.arg: gram}
        # **Le moteur ne connaît aucun outil.** Ce qui était ici une chaîne
        # de vingt-huit blocs `elif tool_name == …` vit désormais dans les
        # préparateurs, déclarés outil par outil plus haut : le garde-fou
        # abandonne l'intention, les arguments calculés se posent, l'entité
        # peut être remplacée, l'appel peut être conclu sur-le-champ.
        # Ajouter un outil ne touche plus cette fonction.
        prepare = PREPARATEURS.get(tool_name)
        maitrise = False
        if prepare is not None:
            candidature = Candidature(con=con, question=question,
                                      contexte=contexte, tool=tool,
                                      gram=gram, entity_score=entity_score,
                                      args=args)
            if not prepare(candidature):
                continue
            gram = candidature.gram
            entity_score = candidature.entity_score
            maitrise = candidature.entites_maitrisees
            if candidature.conclure:
                return ToolCall(tool=tool_name, args=args,
                                confidence=round(confidence, 3),
                                via="deterministic",
                                intent_score=intent_score,
                                entity_score=round(entity_score, 1))
        # Entité résolue mais pas fiable : on garde l'appel sous le coude et
        # on laisse sa chance à l'intention suivante. S'il ne reste que lui,
        # l'API en fera une question — « tu veux dire… ? » — plutôt qu'une
        # réponse assurée sur un autre objet.
        if (tool.entity_types and len(TOOLS[tool_name].entity_types) == 1
                and not maitrise
                and _entite_douteuse(con, gram, tool.entity_types, question)):
            if doute is None:
                doute = ToolCall(tool=tool_name, args=args,
                                 confidence=round(confidence * 0.6, 3),
                                 via="deterministic", intent_score=intent_score,
                                 entity_score=round(entity_score, 1))
            continue

        return ToolCall(
            tool=tool_name,
            args=args,
            confidence=round(confidence, 3),
            via="deterministic",
            intent_score=intent_score,
            entity_score=round(entity_score, 1),
        )
    return doute or _nom_nu(con, question)


def _nom_nu(con: sqlite3.Connection, question: str) -> ToolCall | None:
    """Une phrase qui n'est **que** le nom d'une entité vaut « c'est quoi ? ».

    « P6-LR Sniper Rifle », « novian crossbow », « levski » : aucun mot
    d'intention, donc aucun motif ne se déclenche et la question sortait
    muette. Elle ne l'était que parce que l'étage 3 la rattrapait — quatre au
    rejeu du journal — ce qui la rend dépendante d'un PC allumé pour une
    question triviale.

    Trois garde-fous, et ils sont serrés parce que ce chemin s'ouvre après
    l'échec de **tous** les autres :

    - la phrase entière doit résoudre une entité à **90 ou plus**, le score
      d'une correspondance franche ;
    - le nom trouvé doit **expliquer tous les mots tapés** ;
    - la phrase ne doit pas dépasser cinq mots — au-delà, c'est une question
      qu'on n'a pas comprise, pas un nom.

    **On ne retire que les déterminants, pas les mots vides.** Mesuré : avec
    `STOPWORDS`, « il fait beau » se réduisait à « beau », qui résout à 90 et
    s'explique lui-même — la phrase la plus hors sujet du cahier ressortait en
    `decrire`. Un fragment de phrase n'est pas un nom ; c'est le même piège que
    « black » extrait de « la recette d'un Cutlass Black ».

    **La confiance vient du résolveur, elle n'est plus une constante.** Elle
    valait 0,75 — c'est-à-dire **sous** le seuil de doute de 0,80, donc tous
    les noms nus repartaient malgré tout au LLM : exactement ce que ce repli
    avait été écrit pour éviter, et c'est écrit trois paragraphes plus haut.
    Vingt et une questions du corpus étaient dans ce cas, mesurées le
    2026-08-10.

    Il n'y a pas d'intention à mesurer ici — c'est tout le principe — donc
    la seule certitude disponible est celle de l'entité. À 90, le plancher
    du repli, on sort à 0,87 ; à 100, un nom tapé exactement, à 0,90. Jamais
    1,00 : une lecture par défaut ne vaut pas une intention reconnue.
    """
    mots = [m for m in normalize(question).split() if m not in _DETERMINANTS]
    if not mots or len(mots) > 5:
        return None

    terme = " ".join(mots)
    # La forme normalisée sert à vérifier que tous les mots sont expliqués,
    # pas à exécuter l'outil. Elle retire les tirets significatifs des modèles
    # (« L-21 », « P6-LR ») : `decrire("kruger 21 wolf")` préférait alors
    # Wolf Point Aid Shelter au vaisseau exact. Le résolveur vient déjà de
    # choisir le type ; on transmet la graphie brute et ce type à la fiche.
    terme_brut = question.strip().strip(" ?!.,;:")
    terme_brut = re.sub(
        r"^(?:le|la|les|un|une|des|l['’])\s*", "", terme_brut,
        flags=re.IGNORECASE)
    tool = TOOLS["decrire"]
    trouve = resolve(con, terme_brut, entity_types=tool.entity_types,
                     limit=1).best
    if trouve is None or trouve.score < 90.0:
        return None
    if mots_inexpliques(terme, trouve.alias):
        return None
    return ToolCall(tool="decrire", args={"query": terme_brut,
                                           "type_prefere": trouve.entity_type},
                    confidence=round(0.6 + min(trouve.score, 100.0) / 100 * 0.3, 3),
                    via="deterministic", entity_score=round(trouve.score, 1))
