"""Qui construit quoi — la marque derrière un vaisseau ou un objet.

Un module minuscule, et c'est délibéré : `_constructeur()` est appelé depuis
les fiches d'objet, les fiches de vaisseau **et** les descriptions. Le laisser
dans `queries.py` obligeait `descriptions.py` à réimporter `queries`, qui
réimporte `descriptions` — un cycle pour une fonction de quinze lignes.

**Les objets citent leur constructeur par son code, les vaisseaux par son
nom.** « Coda Pistol — KSAR » d'un côté, « Aegis Dynamics » de l'autre. La
table `manufacturers` — 141 entrées, 116 décrites — répond aux deux clés, et
rend la valeur d'origine plutôt qu'un vide quand elle ne connaît pas : mieux
vaut un code qu'une case blanche.
"""

from __future__ import annotations

import sqlite3

from ._socle import _row


def _constructeur(con: sqlite3.Connection, valeur: str | None) -> str | None:
    """Le nom lisible d'un constructeur, qu'on lui donne son code ou son nom.

    Les objets stockent « KSAR », les vaisseaux « Aegis Dynamics ». La table
    répond aux deux, et rend la valeur d'origine si elle ne connaît pas — mieux
    vaut un code qu'un vide.
    """
    if not valeur or "PLACEHOLDER" in valeur:
        return None
    try:
        ligne = _row(con, "SELECT name FROM manufacturers WHERE code = ? "
                          "OR name = ? ORDER BY (name = ?) DESC LIMIT 1",
                     valeur, valeur, valeur)
    except sqlite3.OperationalError:
        return valeur
    return ligne["name"] if ligne else valeur
