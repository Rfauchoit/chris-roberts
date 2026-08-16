"""Distribuer la base de jeu — pour que chacun ait les mêmes réponses.

**scunpacked ne sert qu'à construire la base.** Remarque décisive de
l'utilisateur, 2026-08-13 : « a-t-il vraiment besoin d'avoir scunpacked ?
Sachant que le but n'est PAS qu'ils puissent itérer dessus, mais qu'ils
puissent poser des questions et qu'ils aient les mêmes résultats que
moi ». Non — et lui faire cloner plusieurs gigaoctets puis attendre une
ingestion complète pour arriver au même fichier serait absurde.

Mesuré le 2026-08-13 : la base pèse **390 Mo**, tombe à **368 Mo** après
`VACUUM` et à **79 Mo** compressée en gzip, en six secondes. Une release
GitHub l'accepte largement.

## Ce qui ne doit jamais partir

La base de jeu et celle de la guilde sont **deux fichiers séparés**
(`disco_lando.db` et `guilde.db`), et les 59 tables de la première ne
contiennent rien d'un membre. `verifier_publiable()` le contrôle avant
chaque publication : une conviction ne suffit pas quand l'erreur expose
des gens.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import shutil
import sqlite3
import tempfile
from typing import Any

from . import config, db

#: Les fragments de nom qui trahissent une table de guilde. Le contrôle
#: porte sur le **nom des tables**, pas sur leur contenu : une table vide
#: aujourd'hui se remplira demain.
_TABLES_INTERDITES = ("membre", "guilde", "appareil", "ticket", "escouade",
                      "evenement", "inventaire_membre", "session",
                      "blueprints_membres", "usages", "tours")

NOM_ARCHIVE = "base-chris.db.gz"
NOM_MANIFESTE = "base-chris.json"
FORMAT_MANIFESTE = 2


def empreinte_schema() -> str:
    """Empreinte du vrai schéma embarqué, commune au binaire et à la base."""
    return hashlib.sha256(db.SCHEMA_PATH.read_bytes()).hexdigest()


def verifier_manifeste(manifeste: dict[str, Any]) -> None:
    """Refuse un couple base/binaire dont le contrat n'est pas prouvé."""
    if manifeste.get("format") != FORMAT_MANIFESTE:
        raise ValueError(
            f"format de manifeste incompatible : {manifeste.get('format')!r}")
    if manifeste.get("nom") != NOM_ARCHIVE:
        raise ValueError("nom d'archive inattendu dans le manifeste")
    archive = str(manifeste.get("sha256") or "")
    schema = str(manifeste.get("schema_sha256") or "")
    if len(archive) != 64 or any(c not in "0123456789abcdefABCDEF"
                                for c in archive):
        raise ValueError("empreinte SHA-256 de l'archive absente ou invalide")
    if schema.lower() != empreinte_schema().lower():
        raise ValueError(
            "la base publiée vise un autre schéma — mets Chris à jour avant "
            "de l'installer")


def verifier_base(base: pathlib.Path) -> list[str]:
    """Explique pourquoi une base ne peut pas être servie par ce binaire."""
    if not base.exists():
        return ["base absente"]
    try:
        con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        try:
            integrite = con.execute("PRAGMA quick_check").fetchone()
            if not integrite or integrite[0] != "ok":
                return [f"intégrité SQLite : {integrite[0] if integrite else '?'}"]
            presentes = {
                table: {ligne[1] for ligne in con.execute(
                        f"PRAGMA table_xinfo({table})")}
                for (table,) in con.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            con.close()
    except sqlite3.Error as exc:
        return [f"base SQLite illisible : {exc}"]
    return [
        f"{table}.{colonne} absente"
        for table, colonnes in db.schema_attendu().items()
        for colonne in sorted(colonnes - presentes.get(table, set()))
    ]


def verifier_publiable(base: pathlib.Path) -> list[str]:
    """Rend la liste des tables qui interdisent la publication.

    Vide = publiable. **On regarde les noms plutôt que les lignes** :
    une table de guilde vide au moment du contrôle se remplirait chez
    l'auteur et partirait à la publication suivante.
    """
    con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
    try:
        tables = [ligne[0] for ligne in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    finally:
        con.close()
    return sorted(nom for nom in tables
                  if any(interdit in nom.lower()
                         for interdit in _TABLES_INTERDITES))


def _empreinte(chemin: pathlib.Path) -> str:
    """SHA-256, lu par blocs : la base fait des centaines de mégaoctets."""
    somme = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        while bloc := fichier.read(1 << 20):
            somme.update(bloc)
    return somme.hexdigest()


def preparer(destination: pathlib.Path | None = None,
             base: pathlib.Path | None = None) -> dict[str, Any]:
    """Compacte, compresse et décrit la base — prête pour une release.

    Le `VACUUM` d'abord : une base de production traîne des pages libres
    que la compression garderait. Puis gzip, dont le niveau 6 suffit — le
    9 gagne quelques mégaoctets pour plusieurs fois le temps.

    Le manifeste porte le **build du jeu**, sans quoi personne ne peut
    savoir de quel patch vient la base qu'il télécharge : c'est la même
    règle qu'une source externe doit prouver qu'elle parle du même patch.
    """
    from .public.version import VERSION

    base = base or pathlib.Path(config.DB_PATH)
    destination = destination or pathlib.Path(config.DATA_DIR) / "publication"
    destination.mkdir(parents=True, exist_ok=True)

    interdites = verifier_publiable(base)
    if interdites:
        raise ValueError(
            "la base contient des tables de guilde et ne peut pas être "
            f"publiée : {', '.join(interdites)}")
    incompatibilites = verifier_base(base)
    if incompatibilites:
        raise ValueError(
            "la base ne respecte pas le schéma publié : "
            + "; ".join(incompatibilites[:4]))

    with tempfile.TemporaryDirectory() as travail:
        compacte = pathlib.Path(travail) / "compacte.db"
        con = sqlite3.connect(f"file:{base}?mode=ro", uri=True)
        try:
            con.execute("VACUUM INTO ?", (str(compacte),))
            build = con.execute(
                "SELECT build_id, game_version FROM ingest_runs "
                "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()

        archive = destination / NOM_ARCHIVE
        with open(compacte, "rb") as source, \
                gzip.open(archive, "wb", compresslevel=6) as sortie:
            shutil.copyfileobj(source, sortie, length=1 << 20)
        manifeste = {
            "format": FORMAT_MANIFESTE,
            "build_id": build[0] if build else None,
            "game_version": build[1] if build else None,
            "schema_sha256": empreinte_schema(),
            "version_application": VERSION,
            "octets_compresse": archive.stat().st_size,
            "octets_decompresse": compacte.stat().st_size,
            "sha256": _empreinte(archive),
            "nom": NOM_ARCHIVE,
        }

    (destination / NOM_MANIFESTE).write_text(
        json.dumps(manifeste, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifeste


def installer(archive: pathlib.Path, destination: pathlib.Path,
              attendu: str | None = None,
              manifeste: dict[str, Any] | None = None) -> pathlib.Path:
    """Décompresse une base téléchargée, après contrôle d'empreinte.

    **La bascule se fait par renommage**, comme l'ingestion : un
    téléchargement interrompu ne doit pas laisser une base à moitié écrite
    à la place d'une base qui marchait.
    """
    if manifeste is not None:
        verifier_manifeste(manifeste)
        attendu = str(manifeste["sha256"])
    if attendu:
        reel = _empreinte(archive)
        if reel.lower() != attendu.lower():
            raise ValueError(
                f"l'archive ne correspond pas à son empreinte ({reel[:12]}… "
                f"au lieu de {attendu[:12]}…) — téléchargement à refaire")
    destination.parent.mkdir(parents=True, exist_ok=True)
    provisoire = destination.with_suffix(destination.suffix + ".partiel")
    try:
        with gzip.open(archive, "rb") as source, \
                open(provisoire, "wb") as sortie:
            shutil.copyfileobj(source, sortie, length=1 << 20)
        if manifeste is not None:
            erreurs = verifier_base(provisoire)
            if erreurs:
                raise ValueError(
                    "base téléchargée incompatible : " + "; ".join(erreurs[:4]))
        # `replace` bascule le nom en une opération : une interruption laisse
        # soit l'ancienne base, soit la nouvelle déjà validée, jamais un flux
        # gzip partiellement décompressé à l'emplacement servi.
        provisoire.replace(destination)
    except Exception:
        provisoire.unlink(missing_ok=True)
        raise
    return destination
