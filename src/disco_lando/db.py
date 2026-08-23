"""Accès SQLite. Rien de plus qu'une connexion correctement réglée."""

from __future__ import annotations

import functools
import pathlib
import sqlite3

from . import config

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")


def connect(path: pathlib.Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    target = path or config.DB_PATH
    if read_only:
        con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        con.execute("PRAGMA journal_mode = WAL")
        # L'ingestion est un batch mono-écrivain : on peut se permettre de ne
        # pas fsync à chaque transaction, la base étant reconstructible.
        con.execute("PRAGMA synchronous = NORMAL")
        _colonnes_attendues(con)
    return con


#: Colonnes ajoutées au schéma après coup. Une colonne dérivée ne peut pas
#: rester vide : `porteur_exclusif IS NULL` est précisément le filtre du
#: catalogue montable. `_colonnes_attendues` rejoue donc son calcul après la
#: migration au lieu d'attendre une réingestion qui peut arriver des jours
#: plus tard.
_AJOUTS: tuple[tuple[str, str, str], ...] = (
    ("items", "porteur_exclusif", "TEXT"),
    ("ships", "cross_x", "REAL"),
    ("ships", "cross_y", "REAL"),
    ("ships", "cross_z", "REAL"),
)


def _colonnes_attendues(con: sqlite3.Connection) -> None:
    """Ajoute les colonnes récentes absentes d'une base plus ancienne."""
    modifie = False
    for table, colonne, typage in _AJOUTS:
        try:
            presentes = {r[1] for r in con.execute(
                f"PRAGMA table_info({table})")}
        except sqlite3.DatabaseError:
            continue
        if presentes and colonne not in presentes:
            try:
                con.execute(
                    f"ALTER TABLE {table} ADD COLUMN {colonne} {typage}")
                modifie = True
            except sqlite3.OperationalError:
                # Base en lecture concurrente ou verrouillée : on n'insiste
                # pas, la réingestion posera la colonne pour de bon.
                pass

    # L'algorithme lui-même peut évoluer entre deux ingestions (VanguardNose
    # en camelCase, Vanduul Mauler dans le nom affiché). Sur quelques centaines
    # d'armes, la comparaison est bornée et évite qu'une base déjà migrée garde
    # l'ancienne projection sans aucun signal.
    try:
        presentes = {r[1] for r in con.execute("PRAGMA table_info(items)")}
        if "porteur_exclusif" in presentes:
            from . import porteurs

            if porteurs.ecarts(con):
                porteurs.remplir(con)
                modifie = True
    except sqlite3.DatabaseError:
        # Une base en cours de création n'a encore ni vaisseaux ni fabricants.
        # Le chargeur calculera la colonne lorsqu'il ingérera les objets.
        pass
    if modifie:
        con.commit()


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def schema_attendu() -> dict[str, frozenset[str]]:
    """Carte exacte ``table -> colonnes`` du schéma embarqué.

    Elle sert autant à l'ingestion qu'au Chris public : deux parseurs du même
    contrat finiraient par diverger. SQLite exécute le vrai fichier en mémoire
    et ``table_xinfo`` inclut les colonnes générées que ``table_info`` cache.
    """
    con = sqlite3.connect(":memory:")
    try:
        create_schema(con)
        tables = [ligne[0] for ligne in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {
            table: frozenset(ligne[1] for ligne in con.execute(
                f"PRAGMA table_xinfo({table})"))
            for table in tables
        }
    finally:
        con.close()


def fts_available() -> bool:
    """FTS5 est une dépendance dure du résolveur — autant le dire tôt."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()
