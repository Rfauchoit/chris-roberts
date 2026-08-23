"""Sélection et enchaînement des étages du routeur — §7 du brief.

`DISCO_ROUTER` accepte un étage seul (`deterministic`) ou une cascade
séparée par des virgules (`deterministic,local`). Les étages sont
essayés dans l'ordre ; le premier qui produit un `ToolCall` gagne.

Le §7 impose que l'étage 1 reste le chemin par défaut même quand un LLM est
disponible, et le §3 en donne la raison : pendant une session de jeu, la VRAM
est prise par Star Citizen. La cascade le respecte structurellement — un étage
LLM ne peut être atteint qu'après l'échec du déterministe.
"""

from __future__ import annotations

import sqlite3

from .. import config
from . import deterministic, llm
from .base import TOOLS, Tool, ToolCall, execute

# L'étage 2 (« cloud ») a été retiré le 2026-08-07 — décision de
# l'utilisateur, pas de clé d'API. Une cascade qui le nomme encore lève un
# ValueError explicite dans `stages()`, ce qui vaut mieux qu'un étage
# silencieusement mort.
_ROUTERS = {
    "deterministic": deterministic.route,
    "local": llm.route_local,
}


def available() -> tuple[str, ...]:
    return tuple(_ROUTERS)


def stages(name: str | None = None) -> list[str]:
    """Étages demandés, dans l'ordre, dédoublonnés."""
    brut = name or config.ROUTER
    vus, ordre = set(), []
    for etage in (e.strip() for e in brut.split(",")):
        if not etage or etage in vus:
            continue
        if etage not in _ROUTERS:
            raise ValueError(
                f"routeur inconnu : {etage!r} (disponibles : {', '.join(_ROUTERS)})"
            )
        vus.add(etage)
        ordre.append(etage)
    return ordre or ["deterministic"]


def route(con: sqlite3.Connection, question: str,
          *, name: str | None = None,
          contexte: str | None = None,
          speaker: str | None = None) -> ToolCall | None:
    """Route une question en descendant la cascade.

    Renvoyer `None` après tous les étages est un résultat, pas une erreur : la
    question est consignée dans le journal (cf. unanswered.py) plutôt que
    devinée.
    """
    faute_de_mieux: ToolCall | None = None

    for etage in stages(name):
        # Seul l'étage déterministe sait reprendre une entité du contexte —
        # le LLM, lui, reçoit la question telle quelle et n'a pas de mémoire.
        if etage == "deterministic" and (contexte is not None
                                          or speaker is not None):
            appel = _ROUTERS[etage](
                con, question, contexte=contexte, speaker=speaker)
        else:
            appel = _ROUTERS[etage](con, question)
        if appel is None:
            continue
        # Un étage sûr de lui s'arrête là.
        if appel.confidence >= config.ROUTER_MIN_CONFIDENCE:
            return appel
        # Sinon on garde sous le coude et on demande l'avis du suivant. Le
        # premier résultat non nul gagnait auparavant, quelle que soit sa
        # qualité — c'est ce qui laissait « je cherche à savoir ce que porte le
        # Cutlass Black » partir en recherche de ressource à 0,70 de confiance,
        # alors que l'étage suivant aurait tranché. Mesuré sur la machine :
        # les routages corrects sortent à 0,86 et au-dessus, les douteux en
        # dessous de 0,71.
        # Le dernier consulté l'emporte, sans comparer les confiances : elles
        # ne sont pas sur la même échelle. L'étage 1 calcule la sienne à partir
        # de l'intention et de l'entité ; l'étage LLM rend une constante de
        # 0,70. Les comparer donnait ceci, mesuré : 0,702 contre 0,700 laissait
        # gagner un routage erroné de l'étage 1 sur un routage correct du LLM,
        # pour deux millièmes de bruit.
        #
        # On n'arrive ici que si tous les étages précédents doutaient. Le
        # suivant a été interrogé exprès : lui refuser la main viderait la
        # consultation de son sens.
        faute_de_mieux = appel

    # Aucun étage n'était sûr : mieux vaut le meilleur doute que rien, mais il
    # reste marqué comme tel par sa confiance — le journal en garde la trace.
    return faute_de_mieux


__all__ = ["TOOLS", "Tool", "ToolCall", "available", "execute", "route", "stages"]
