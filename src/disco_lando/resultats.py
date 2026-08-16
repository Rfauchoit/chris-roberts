"""Contrat commun des réponses composées.

Les fonctions métier historiques rendent des dictionnaires libres. Les
réécrire d'un bloc ferait courir plus de risques qu'elle n'en retirerait ;
les familles migrent donc progressivement en ajoutant ``qualite_reponse``.
Cette enveloppe ne remplace jamais les données métier : elle nomme les faits
sur lesquels la conclusion repose, ce qui manque, leurs sources et leur
fraîcheur. Le dialogue peut alors décider sans relire la prose si l'analyste
doit chercher un complément.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from typing import Any, Iterable, Mapping


def fraicheur_jeu(con: sqlite3.Connection) -> dict[str, Any]:
    """Version du dernier export du jeu réellement ingéré."""
    try:
        ligne = con.execute(
            "SELECT build_id, game_version, finished_at FROM ingest_runs "
            "WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return {}
    if ligne is None:
        return {}
    return {
        "build": ligne[0],
        "version": ligne[1],
        "ingere_le": ligne[2],
    }


def fraicheur_uex(con: sqlite3.Connection, table: str) -> dict[str, Any]:
    """Date du relevé UEX lu, sur un vocabulaire de tables fermé.

    Le nom de table ne vient jamais d'un joueur. La liste fermée conserve
    néanmoins la même garantie que les requêtes paramétrées du reste du
    projet et empêche ce petit helper de devenir une porte SQL générique.
    """
    if table not in {
        "uex_prices", "uex_commodity_prices", "refineries",
        "refinery_yields",
    }:
        raise ValueError(f"table UEX sans contrat de fraîcheur : {table}")
    try:
        ligne = con.execute(
            f"SELECT MAX(fetched_at) AS releve_le FROM {table}"
        ).fetchone()
    except sqlite3.Error:
        return {}
    return {"releve_le": ligne[0]} if ligne and ligne[0] else {}


def qualite_reponse(*, faits: Mapping[str, Any],
                    manques: Iterable[str] = (),
                    sources: Iterable[str] = (),
                    fraicheur: Mapping[str, Any] | None = None,
                    complet: bool | None = None) -> dict[str, Any]:
    """Construit l'enveloppe stable sans inventer de complétude.

    ``complet`` est normalement déduit des manques. Le paramètre explicite
    sert aux réponses de clarification : l'entrée du joueur peut manquer sans
    que la base soit incomplète ni qu'un LLM puisse la deviner.
    """
    manques_uniques = list(dict.fromkeys(m for m in manques if m))
    sources_uniques = list(dict.fromkeys(s for s in sources if s))
    return {
        "complet": not manques_uniques if complet is None else bool(complet),
        "faits": dict(faits),
        "manques": manques_uniques,
        "sources": sources_uniques,
        "fraicheur": dict(fraicheur or {}),
    }


def _simplifier(valeur: Any, profondeur: int, maximum: int) -> Any:
    """Projection JSON bornée d'un résultat, pas un second modèle métier."""
    if valeur is None or isinstance(valeur, (bool, int, float, str)):
        return valeur[:500] if isinstance(valeur, str) else valeur
    if profondeur <= 0:
        return "…"
    if dataclasses.is_dataclass(valeur):
        valeur = {champ.name: getattr(valeur, champ.name)
                  for champ in dataclasses.fields(valeur)}
    elif isinstance(valeur, sqlite3.Row):
        valeur = dict(valeur)
    if isinstance(valeur, Mapping):
        lignes = list(valeur.items())
        rendu = {
            str(cle): _simplifier(v, profondeur - 1, maximum)
            for cle, v in lignes[:maximum]
        }
        if len(lignes) > maximum:
            rendu["_champs_omis"] = len(lignes) - maximum
        return rendu
    if isinstance(valeur, (list, tuple, set, frozenset)):
        lignes = list(valeur)
        rendu = [_simplifier(v, profondeur - 1, maximum)
                 for v in lignes[:maximum]]
        if len(lignes) > maximum:
            rendu.append({"_elements_omis": len(lignes) - maximum})
        return rendu
    return str(valeur)[:500]


def apercu_pour_analyste(data: Any, *, max_caracteres: int = 12_000) -> str:
    """Sérialise les faits vérifiés sans gonfler indéfiniment le prompt.

    Deux projections de plus en plus serrées sont tentées. Si un résultat
    exceptionnel les dépasse encore, le contrat de qualité et les scalaires
    de premier niveau restent disponibles ; la réponse rendue, elle, est
    toujours transmise séparément par le dialogue.
    """
    for profondeur, maximum in ((5, 8), (3, 3)):
        texte = json.dumps(
            _simplifier(data, profondeur, maximum),
            ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(texte) <= max_caracteres:
            return texte

    if isinstance(data, Mapping):
        minimum = {
            cle: valeur for cle, valeur in data.items()
            if cle == "qualite_reponse"
            or valeur is None or isinstance(valeur, (bool, int, float, str))
        }
    else:
        minimum = {"type_resultat": type(data).__name__}
    texte = json.dumps(_simplifier(minimum, 3, 8), ensure_ascii=False,
                       separators=(",", ":"), sort_keys=True)
    if len(texte) <= max_caracteres:
        return texte
    contrat = (minimum.get("qualite_reponse")
               if isinstance(minimum, Mapping) else None)
    return json.dumps(
        {"qualite_reponse": _simplifier(contrat, 3, 8),
         "_apercu_omis": "résultat trop volumineux"},
        ensure_ascii=False, separators=(",", ":"), sort_keys=True)
