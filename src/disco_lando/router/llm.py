"""Étage 3 du routeur — §7 du brief.

Un LLM **local** derrière la même interface que l'étage 1 : endpoint
compatible OpenAI (Ollama ou llama.cpp server) sur le PC du joueur. Quand le
PC est allumé, la VM peut taper dessus.

L'étage 2 (Anthropic, cloud) a été écrit puis **retiré le 2026-08-07** :
décision de l'utilisateur, pas de clé d'API — le code dormant se relit dans
l'historique git si la décision change un jour.

**Règle absolue du §7 : les chiffres viennent toujours de la base, jamais du
modèle.** Le LLM ne fait que traduire la question en appel de fonction. Il ne
voit aucune donnée de jeu et n'en produit aucune — la mise en forme reste à
`render.py`, qui lit la base.

Cette contrainte n'est pas qu'une intention : le modèle est appelé avec
`tool_choice={"type": "any"}`, donc sa seule sortie possible est un appel de
fonction. Il n'a pas de chemin pour écrire une réponse en prose.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.error
import urllib.request

from .. import config
from ..resolver import mots_inexpliques, resolve
from .base import TOOLS, ToolCall
from .deterministic import MIN_ENTITY_SCORE

_LOG = logging.getLogger(__name__)

SYSTEM = """Tu traduis des questions de joueurs de Star Citizen, posées en \
français, en appels de fonction.

Tu ne réponds jamais toi-même : tu choisis la fonction et son argument. Les \
données viennent d'une base locale, jamais de toi. N'invente aucun nom de \
vaisseau, d'arme, de ressource ou de mission.

L'argument `query` doit contenir le terme tel que le joueur l'a prononcé, sans \
le corriger ni le compléter. Un résolveur phonétique se charge des \
déformations : « gladiousse » se transmet tel quel, pas « Gladius ».

Si la question ne relève d'aucune fonction, appelle `unsupported`."""

# Outil de sortie : le modèle doit toujours appeler une fonction
# (tool_choice=any), il lui en faut donc une pour dire « je ne sais pas ».
_UNSUPPORTED = {
    "name": "unsupported",
    "description": (
        "À appeler quand la question ne relève d'aucune autre fonction — "
        "hors sujet, ou donnée absente du périmètre."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Pourquoi, en une phrase."}
        },
        "required": ["reason"],
    },
}


def tool_schemas() -> list[dict]:
    """Catalogue des outils au format function calling — **en entier**.

    Construit depuis `TOOLS` : ajouter un outil au catalogue l'expose
    automatiquement aux trois étages, sans duplication de schéma.

    **Une sélection des huit outils les plus pertinents a été écrite, mesurée,
    et retirée le 2026-08-06.** Elle réduisait le prompt de 5 353 à 1 125
    tokens — 79 % de moins — et le raisonnement paraissait solide : moins de
    distracteurs pour un modèle de 4 milliards de paramètres. Mesurée sur le
    corpus, avec le serveur allumé, elle faisait **exactement l'inverse** :

    - 128 questions routées contre 130 avec le catalogue entier ;
    - « P6-LR Sniper Rifle » sortait `accessoires_compatibles` au lieu de
      `get_item_stats`, parce que les deux n'acceptent qu'un type d'entité et
      que le tri départageait alphabétiquement — l'ordre du dictionnaire, le
      défaut même qu'on reproche au routeur ;
    - et c'était **plus lent** (244 ms contre 207 par question) : résoudre les
      n-grammes pour choisir coûte plus cher que les tokens économisés.

    Le modèle fait mieux avec le menu complet. La panne qui avait motivé
    l'idée — le contexte à 4 096 — se règle en servant 8 192, où les 5 460
    tokens du catalogue tiennent avec 33 % de marge.

    Deux leçons, plus utiles que le code retiré : un test de rappel qui ne
    couvre pas la population concernée ne prouve rien (le mien annonçait 100 %
    sur 70 questions étiquetées, dont aucune n'était un nom nu — le cas qui
    cassait), et une optimisation de prompt doit se mesurer **modèle allumé**,
    jamais sur le papier.
    """
    schemas = []
    for tool in TOOLS.values():
        # **La ligne de vie n'a rien à faire dans le menu du modèle.** Un
        # ping (« t'es là ? ») est toujours attrapé par l'étage 1 ; laissé au
        # catalogue, le LLM s'en servait de fourre-tout — « donne moi des
        # infos sur toutes », une reprise ratée, ressortait en preuve de vie.
        # Journal du 2026-08-07.
        if tool.name == "ligne_de_vie":
            continue
        proprietes: dict = {
            tool.arg: {
                "type": "string",
                "description": (
                    "Le terme prononcé par le joueur, non corrigé"
                    + (f" (type attendu : {', '.join(tool.entity_types)})"
                       if tool.entity_types else "")
                ),
            }
        }
        if tool.name == "get_mission_group":
            proprietes["system"] = {
                "type": "string",
                "enum": ["Stanton", "Pyro", "Nyx"],
                "description": "Le système, si la question le précise.",
            }
        if tool.name == "compare_items":
            proprietes["stat"] = {
                "type": "string",
                "enum": ["dps", "alpha", "rounds_per_minute", "effective_range",
                         "projectile_speed", "ammo_capacity", "health", "item_mass"],
                "description": "La statistique de comparaison. dps par défaut.",
            }
            proprietes["size"] = {
                "type": "integer",
                "description": "La taille d'arme, si la question la précise.",
            }
        schemas.append({
            "name": tool.name,
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": proprietes,
                "required": [tool.arg],
            },
        })
    schemas.append(_UNSUPPORTED)
    return schemas


def _to_toolcall(name: str, args: dict, via: str,
                 con: sqlite3.Connection | None = None) -> ToolCall | None:
    if name == "unsupported" or name not in TOOLS:
        return None
    tool = TOOLS[name]
    if not args.get(tool.arg):
        return None
    if con is not None and not _entite_plausible(con, tool, args[tool.arg]):
        return None
    propre = {k: v for k, v in args.items() if v not in (None, "")}
    return ToolCall(tool=name, args=propre, confidence=0.7, via=via)


def _entite_plausible(con: sqlite3.Connection, tool, terme: str) -> bool:
    """L'entité que le modèle propose existe-t-elle vraiment ?

    **C'est la discipline de l'étage 1, appliquée à l'étage 3.** « Une
    intention sans entité résoluble est un échec, pas une réponse » vaut aussi
    quand c'est un modèle qui a choisi l'intention — et le modèle, lui, ne
    consulte pas la base : rien ne l'empêche d'inventer un nom qui sonne juste.

    Mesuré : `tool_choice: "required"` force un appel même hors sujet, et « il
    fait beau » sortait `conseil_de_raffinage(« circuit de puissance de base »)`
    — un outil réel, une entité qui n'existe pas, et une réponse chiffrée sur
    du vide. Le test qui l'interdit ne passait que parce que le serveur local
    était éteint.

    On ne juge que les outils qui **déclarent** un type d'entité : ceux qui
    n'en ont pas (`compare_items`, `combien_y_a_t_il`) portent leur propre
    garde-fou côté métier, et il n'y a rien à vérifier ici.

    Le terme n'est **pas** corrigé au passage : « gladiousse » se transmet tel
    quel, le résolveur fait ça mieux — il sort à 90, largement au-dessus du
    seuil.

    **Le score seul ne suffit pas, et c'est mesuré.** « circuit de puissance de
    base » sort *CBH-3 Helmet Base* à 85, au-dessus du seuil, parce que le mot
    « base » suffit à l'étage `token` : quatre mots sur cinq ne correspondent à
    rien. C'est le même piège qu'« omnisky xi » à 90, et il se règle de la même
    façon — on exige que le nom trouvé **explique** ce qui a été proposé.
    """
    if not tool.entity_types:
        return True
    trouve = resolve(con, terme, entity_types=tool.entity_types, limit=1).best
    if trouve is None or trouve.score < MIN_ENTITY_SCORE:
        return False
    return not mots_inexpliques(terme, trouve.alias)


# ------------------------------------------------------------ étage 3 : local

def _openai_tools() -> list[dict]:
    """Le même catalogue, au format OpenAI attendu par Ollama et llama-server."""
    return [
        {"type": "function",
         "function": {"name": s["name"], "description": s["description"],
                      "parameters": s["input_schema"]}}
        for s in tool_schemas()
    ]


def route_local(con: sqlite3.Connection, question: str) -> ToolCall | None:
    """Étage 3 — endpoint compatible OpenAI sur le PC du joueur.

    Ollama et llama.cpp server exposent tous deux `/v1/chat/completions`. On
    passe par `urllib` plutôt que par un SDK : le contrat est trivial et une
    dépendance de moins sur la VM, c'est autant de gagné.
    """
    payload = json.dumps({
        "model": config.LOCAL_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
        ],
        "tools": _openai_tools(),
        "tool_choice": "required",
        "temperature": 0,
        # Un appel d'outil fait une centaine de tokens. Sans plafond, un petit
        # modèle à qui `required` impose d'appeler un outil alors qu'aucun ne
        # convient n'émet pas de bloc `tool_calls` : il écrit l'appel en texte
        # brut puis part en roue libre. Mesuré sur Qwen3-4B — 2 956 tokens et
        # 18,7 s pour « quel est le vaisseau le plus rapide », en finissant sur
        # une digression à propos de Poutine. Le plafond ramène ce pire cas
        # sous les deux secondes.
        #
        # `tool_choice: "auto"` évite la divagation mais rattrape une question
        # de moins : mesuré, « je cherche à savoir ce que porte le Cutlass
        # Black » revient correctement avec `required` et pas avec `auto`. On
        # garde donc `required`, borné.
        "max_tokens": config.LOCAL_MAX_TOKENS,
        "stream": False,
    }).encode()

    requete = urllib.request.Request(
        f"{config.LOCAL_LLM_URL.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=config.LOCAL_LLM_TIMEOUT) as reponse:
            corps = json.load(reponse)
    except urllib.error.HTTPError as erreur:
        # **Un serveur qui refuse n'est pas un serveur absent.** Les deux
        # rendaient `None` par la même porte, et l'étage 3 est resté mort
        # plusieurs semaines sans que rien ne le dise : le catalogue d'outils
        # avait dépassé le contexte du modèle, llama-server répondait
        # « 400 exceed_context_size » à chaque question, et la cascade
        # retombait sur l'étage 1 exactement comme si le PC était éteint.
        #
        # On passe quand même la main — un étage en panne ne lève jamais, c'est
        # la règle — mais la panne devient **visible**, et `disco verifier`
        # contrôle la cause la plus probable avant qu'elle ne se reproduise.
        detail = ""
        try:
            detail = erreur.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 — diagnostic, jamais bloquant
            pass
        _LOG.warning("étage 3 (LLM local) a refusé la requête : HTTP %s %s",
                     erreur.code, detail)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        # PC éteint, modèle non chargé, endpoint injoignable : repli silencieux
        # sur l'étage suivant, comme le prévoit le §7.
        return None

    try:
        appels = corps["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        return None

    for appel in appels:
        fonction = appel.get("function") or {}
        try:
            args = json.loads(fonction.get("arguments") or "{}")
        except json.JSONDecodeError:
            continue
        resultat = _to_toolcall(fonction.get("name", ""), args, "llm_local", con)
        if resultat is not None:
            return resultat
    return None
