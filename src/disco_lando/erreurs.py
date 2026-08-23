"""Dire d'où vient une panne — pour le joueur, et pour le débogage.

Trois familles de pannes se déguisaient l'une en l'autre :

- un **outil qui plante** (bug, colonne disparue) sortait en 500 générique,
  et le joueur lisait « Le service a répondu 500 » sans nom d'outil ni trace ;
- une **table annexe vide** — le pire cas documenté : une réingestion vide
  `uex_prices` et `traductions` sans rien lever — se lisait « je n'ai pas
  trouvé la donnée », indiscernable d'une vraie absence ;
- un **cœur injoignable** disait « le service ne répond pas » sans dire ni
  où il a cherché ni le geste qui répare.

Chaque message doit nommer sa cause et, quand il existe, le geste qui répare.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# Les tables que les commandes annexes remplissent **après** l'ingestion.
# Une réingestion reconstruit la base de zéro et les laisse vides — rien ne
# lève, les tests passent, et seule une question de joueur le révèle. Le
# libellé dit ce que la table porte, le geste dit comment la remplir.
TABLES_ANNEXES: tuple[tuple[str, str, str], ...] = (
    ("uex_prices", "les prix UEX", "disco uex"),
    ("traductions", "les traductions françaises", "disco trad"),
    ("wiki_items", "les fiches du wiki", "disco wiki"),
)


def tables_vides(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """Les tables annexes vides, en (libellé, geste qui répare)."""
    vides: list[tuple[str, str]] = []
    for table, libelle, geste in TABLES_ANNEXES:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            # Table absente : même signal qu'une table vide — le schéma a
            # changé et la commande annexe n'est pas repassée.
            n = 0
        if n == 0:
            vides.append((libelle, geste))
    return vides


def note_de_tables_vides(con: sqlite3.Connection) -> str:
    """Ce qui s'ajoute à « je n'ai pas trouvé la donnée » quand la cause
    probable est une base incomplète, pas la question posée."""
    vides = tables_vides(con)
    if not vides:
        return ""
    lignes = " ; ".join(f"{libelle} (à recharger par « {geste} »)"
                        for libelle, geste in vides)
    return (f"\n\n⚠ La base est incomplète, et c'est sans doute la vraie "
            f"cause : {lignes}. C'est l'effet d'une réingestion sans les "
            f"commandes annexes.")


def message_de_crash(tool: str | None, args: dict[str, Any] | None,
                     exc: BaseException) -> str:
    """Ce que lit le joueur quand un outil lève autre chose que NotFound.

    Le nom de l'outil et le type d'exception suffisent à orienter le
    diagnostic sans noyer le joueur ; le traceback complet part dans le
    journal du cœur, jamais dans la réponse.
    """
    ou = f"l'outil « {tool} »" if tool else "le traitement de la question"
    sur = f" sur {args}" if args else ""
    return (f"Une panne de mon côté — ce n'est pas ta question : {ou} a "
            f"levé {type(exc).__name__} ({exc}){sur}. Le détail complet "
            f"est dans le journal du cœur.")
