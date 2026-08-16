"""L'unique outil MCP de l'analyste Codex, embarqué dans Chris.

Le protocole stdio expose seulement ``interroger`` puis appelle directement
``porte_analyste``. Le binaire public peut donc se relancer avec
``--mcp-analyste`` sans Python, sans `.venv` et sans script externe.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

from . import porte_analyste


def _reponse(identifiant: Any, resultat: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifiant, "result": resultat}


def _erreur(identifiant: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifiant,
            "error": {"code": code, "message": message}}


def _texte(texte: str, *, erreur: bool = False) -> dict[str, Any]:
    resultat: dict[str, Any] = {
        "content": [{"type": "text", "text": texte}],
    }
    if erreur:
        resultat["isError"] = True
    return resultat


def _executer(arguments: dict[str, Any]) -> dict[str, Any]:
    sql = arguments.get("sql")
    outil = arguments.get("outil")
    options = arguments.get("arguments", {})
    if bool(sql) == bool(outil):
        return _texte("Fournis soit `sql`, soit `outil`, jamais les deux.",
                      erreur=True)
    if sql and not isinstance(sql, str):
        return _texte("`sql` doit être une chaîne.", erreur=True)
    if outil and (not isinstance(outil, str)
                  or not isinstance(options, dict)):
        return _texte("`outil` doit être une chaîne et `arguments` un objet.",
                      erreur=True)

    porte = [sql] if sql else [
        "outil", outil, json.dumps(options, ensure_ascii=False)]
    code, sortie = porte_analyste.executer(porte)
    return _texte(sortie or "aucune sortie", erreur=code != 0)


def _traiter(message: dict[str, Any]) -> dict[str, Any] | None:
    methode = message.get("method")
    identifiant = message.get("id")
    if identifiant is None:
        return None
    if methode == "initialize":
        version = (message.get("params") or {}).get(
            "protocolVersion", "2025-06-18")
        return _reponse(identifiant, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "disco-analyste", "version": "1.1"},
        })
    if methode == "ping":
        return _reponse(identifiant, {})
    if methode == "tools/list":
        return _reponse(identifiant, {"tools": [{
            "name": "interroger",
            "description": (
                "Interroge les données Star Citizen en lecture seule. "
                "Fournir soit une requête SELECT/WITH dans `sql`, soit le "
                "nom d'une fonction du catalogue dans `outil` avec son objet "
                "`arguments`."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "outil": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }]})
    if methode == "tools/call":
        parametres = message.get("params") or {}
        if parametres.get("name") != "interroger":
            return _reponse(identifiant,
                            _texte("outil MCP inconnu", erreur=True))
        arguments = parametres.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _reponse(identifiant,
                            _texte("arguments MCP illisibles", erreur=True))
        return _reponse(identifiant, _executer(arguments))
    return _erreur(identifiant, -32601, f"méthode inconnue : {methode}")


def _forcer_utf8() -> None:
    """MCP impose UTF-8 ; ne remplacer que les flux qui en ont besoin."""
    if (getattr(sys.stdin, "buffer", None) is not None
            and str(getattr(sys.stdin, "encoding", "")).lower() != "utf-8"):
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8",
                                     errors="replace")
    if (getattr(sys.stdout, "buffer", None) is not None
            and str(getattr(sys.stdout, "encoding", "")).lower() != "utf-8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="strict")


def main() -> int:
    _forcer_utf8()
    for ligne in sys.stdin:
        if not ligne.strip():
            continue
        try:
            message = json.loads(ligne)
            reponse = _traiter(message)
        except (TypeError, ValueError) as exc:
            reponse = _erreur(None, -32700, f"JSON illisible : {exc}")
        if reponse is not None:
            print(json.dumps(reponse, ensure_ascii=False), flush=True)
    return 0
