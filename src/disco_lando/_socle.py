"""Les trois primitives que tout le monde partage.

Elles vivaient en tête de `queries.py`, ce qui obligeait tout module extrait à
réimporter `queries` — et donc à fabriquer un cycle, puisque `queries`
réimporte les modules extraits. Un socle sans dépendance casse le cycle : rien
ici ne connaît quoi que ce soit du reste du projet, hors le résolveur dont
`NotFound` transporte le diagnostic.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .resolver import Resolution


class NotFound(LookupError):
    """Aucune donnée — et parfois, une raison qui vaut mieux qu'un silence.

    **`explication` s'adresse au joueur, `query` au journal.** Plusieurs
    outils lèvent une absence *documentée* : le jeu ne nomme que 6
    propulseurs distincts, ne publie aucune caractéristique propre aux 77
    radars. Ces phrases ont été écrites pour « dire pourquoi » plutôt que
    de se taire — et mesuré le 2026-08-14, **aucune n'atteignait le
    joueur** : le dialogue rendait « je n'ai pas trouvé la donnée » et la
    raison mourait dans le message d'exception.

    Une explication est donc un champ à part, que le rendu peut afficher
    tel quel. Sans elle, le comportement ne change pas d'un caractère.
    """

    def __init__(self, query: str, resolution: Resolution | None = None,
                 explication: str | None = None):
        self.query = query
        self.resolution = resolution
        self.explication = explication
        super().__init__(explication or f"rien trouvé pour {query!r}")


def _row(con: sqlite3.Connection, sql: str, *args) -> sqlite3.Row | None:
    return con.execute(sql, args).fetchone()


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# Ce qui fait d'un objet l'**accessoire** d'une arme plutôt que l'arme.
# Volontairement court : on ne cherche pas à classer le catalogue, seulement à
# reconnaître le cas où deux candidats ne diffèrent que par cette qualité.
#
# La liste vit ici plutôt que dans le module qui l'a écrite en premier : c'est
# la leçon de `MOTS_GRAMMATICAUX`, où chaque consommateur tenait sa propre
# copie et où chacune en oubliait une part différente.
MOTS_D_ACCESSOIRE = ("magazine", "chargeur", "ammo", "ammunition", "munition")


def est_un_accessoire(nom: str) -> bool:
    """Ce nom désigne-t-il le chargeur d'une arme plutôt que l'arme ?"""
    from .normalize import normalize

    return any(mot in normalize(nom or "") for mot in MOTS_D_ACCESSOIRE)


def nomme_un_accessoire(texte: str) -> bool:
    """La question réclame-t-elle explicitement le chargeur ?

    **Sur la question entière, pas sur le terme extrait.** Le routeur ne
    transmet à l'outil que « p8ar » : « comment fabriquer le **chargeur** du
    P8AR » y perd le seul mot qui désigne l'accessoire, et la règle se
    retournait contre elle-même en écartant ce qu'on demandait.
    """
    from .normalize import normalize

    return any(mot in normalize(texte or "") for mot in MOTS_D_ACCESSOIRE)
