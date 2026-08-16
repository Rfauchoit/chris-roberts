"""La porte de données de l'analyste, empaquetable et lecture seule.

L'atelier l'appelle par ``scripts/requete.py`` ; le Chris figé l'appelle par
``Chris.exe --porte-analyste``. Les deux chemins aboutissent ici, afin que la
garantie du §7 ne dépende ni d'un prompt ni de fichiers laissés à côté du
binaire. SQLite reste ouvert en ``mode=ro`` et le catalogue métier est fermé.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import sys
from typing import Sequence

MAX_LIGNES = 200
MAX_SORTIE = 8000


def _sql(requete: str) -> int:
    sql = requete.strip()
    if not sql.lower().startswith(("select", "with", "pragma table_info",
                                   "explain")):
        print("refusé : seules les requêtes de lecture passent "
              "(SELECT, WITH, PRAGMA table_info, EXPLAIN)")
        return 2

    from . import config

    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        if config.GUILD_DB_PATH.exists():
            from .guilde import exposition
            exposition.installer(con, config.GUILD_DB_PATH)
        curseur = con.execute(sql)
        colonnes = [description[0]
                    for description in curseur.description or []]
        if colonnes:
            print("\t".join(colonnes))
        compte = 0
        for ligne in curseur:
            print("\t".join("" if valeur is None else str(valeur)
                            for valeur in ligne))
            compte += 1
            if compte >= MAX_LIGNES:
                print(f"… tronqué à {MAX_LIGNES} lignes")
                break
        if compte == 0:
            print("(aucune ligne)")
    except sqlite3.Error as exc:
        print(f"erreur SQLite : {exc}")
        return 1
    finally:
        con.close()
    return 0


def _jsonable(valeur):
    """Les objets du résolveur ne sont pas sérialisables tels quels."""
    if isinstance(valeur, dict):
        return {cle: _jsonable(contenu) for cle, contenu in valeur.items()
                if cle not in ("resolution", "resolution_porteur")}
    if isinstance(valeur, (list, tuple)):
        return [_jsonable(contenu) for contenu in valeur]
    if hasattr(valeur, "__dataclass_fields__"):
        return {cle: _jsonable(getattr(valeur, cle))
                for cle in valeur.__dataclass_fields__}
    return valeur


def _outil(nom: str, brut: str) -> int:
    from . import db, render, router
    from .queries import NotFound

    outil = router.TOOLS.get(nom)
    if outil is None:
        print(f"outil inconnu : {nom}. Le catalogue est dans les consignes.")
        return 2
    try:
        args = json.loads(brut) if brut else {}
    except json.JSONDecodeError as exc:
        print(f"arguments illisibles ({exc}) — un objet JSON est attendu, "
              "ex. {\"query\": \"gladius\"}")
        return 2

    con = db.connect(read_only=True)
    try:
        donnees = router.execute(con, router.ToolCall(
            tool=nom, args=args, confidence=1.0, via="analyste"))
    except NotFound as exc:
        print(f"aucune donnée : {exc}")
        return 1
    except TypeError as exc:
        print(f"mauvais arguments pour {nom} : {exc}")
        return 2
    finally:
        con.close()

    texte = render.render(nom, donnees)
    print("--- réponse rendue ---")
    print(texte[:MAX_SORTIE])
    print("--- données (json) ---")
    print(json.dumps(_jsonable(donnees), ensure_ascii=False,
                     default=str)[:MAX_SORTIE])
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if len(arguments) >= 2 and arguments[0] == "outil":
        return _outil(arguments[1], arguments[2]
                      if len(arguments) > 2 else "")
    if len(arguments) == 1:
        return _sql(arguments[0])
    print("usage : porte-analyste \"SELECT …\"  |  "
          "porte-analyste outil <nom> '{\"query\": \"…\"}'")
    return 2


def executer(arguments: Sequence[str]) -> tuple[int, str]:
    """Exécute la même porte pour le MCP, sans second interpréteur."""
    sortie = io.StringIO()
    with contextlib.redirect_stdout(sortie):
        code = main(arguments)
    return code, sortie.getvalue().strip()

