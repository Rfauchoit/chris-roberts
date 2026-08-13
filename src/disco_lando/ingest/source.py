"""Le dépôt scunpacked-data comme source : état, garde-fous, lecture JSON."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import subprocess

from .. import config

# "4.9.0-LIVE.12232306" — le message de commit amont *est* la version du build.
_SUBJECT = re.compile(r"^(?P<version>\d+\.\d+\.\d+)[-.](?P<channel>[A-Z]+)\.(?P<build>\d+)")

_LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"


class SourceError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class SourceState:
    commit: str
    subject: str
    game_version: str | None
    build_id: str | None

    @property
    def label(self) -> str:
        return self.subject or self.commit[:12]


def _git(path: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SourceError(f"git {' '.join(args)} a échoué : {proc.stderr.strip()}")
    return proc.stdout.strip()


def state(path: pathlib.Path | None = None) -> SourceState:
    """Commit courant du dépôt source, décomposé en version et build."""
    root = path or config.SOURCE_DIR
    if not (root / ".git").exists():
        raise SourceError(
            f"{root} n'est pas un dépôt git. Lance d'abord « disco source clone »."
        )
    commit, subject = _git(root, "log", "-1", "--format=%H%n%s").split("\n", 1)
    match = _SUBJECT.match(subject.strip())
    return SourceState(
        commit=commit.strip(),
        subject=subject.strip(),
        game_version=match.group("version") if match else None,
        build_id=match.group("build") if match else None,
    )


def remote_state(path: pathlib.Path | None = None) -> SourceState:
    """État de la branche distante, après `git fetch`.

    C'est l'étape quotidienne : quelques kilo-octets quand rien n'a bougé.
    """
    root = path or config.SOURCE_DIR
    _git(root, "fetch", "--quiet", "origin")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    commit, subject = _git(
        root, "log", "-1", "--format=%H%n%s", f"origin/{branch}"
    ).split("\n", 1)
    match = _SUBJECT.match(subject.strip())
    return SourceState(
        commit=commit.strip(),
        subject=subject.strip(),
        game_version=match.group("version") if match else None,
        build_id=match.group("build") if match else None,
    )


def clone(path: pathlib.Path | None = None) -> pathlib.Path:
    """Clone complet. `items/` est récupéré mais pas ingéré (cf. INGESTION.md).

    GIT_LFS_SKIP_SMUDGE : `items.json` est un pointeur LFS de 128 Mo dont on n'a
    aucun usage. On évite de le rapatrier plutôt que de le télécharger pour
    l'ignorer ensuite.
    """
    root = path or config.SOURCE_DIR
    if (root / ".git").exists():
        return root
    root.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", config.SOURCE_REPO, str(root)],
        env={"GIT_LFS_SKIP_SMUDGE": "1", "PATH": __import__("os").environ.get("PATH", "")},
    )
    if proc.returncode != 0:
        raise SourceError("le clone de scunpacked-data a échoué")
    return root


def pull(path: pathlib.Path | None = None) -> SourceState:
    root = path or config.SOURCE_DIR
    _git(root, "pull", "--ff-only", "--quiet")
    return state(root)


def read_json(path: pathlib.Path):
    """Lit un JSON du dépôt source, en refusant les pointeurs Git LFS.

    `items.json` fait 134 octets sur un clone sans git-lfs : c'est le pointeur,
    pas la donnée. Le charger silencieusement produirait une base vide sans la
    moindre erreur — d'où l'échec explicite.
    """
    raw = path.read_bytes()
    if raw.startswith(_LFS_POINTER):
        raise SourceError(
            f"{path.name} est un pointeur Git LFS non résolu ({len(raw)} octets). "
            "Installe git-lfs et lance « git lfs pull », ou n'ingère pas ce fichier."
        )
    # Le dépôt amont sème des BOM : labels.json commence par un ﻿ qui
    # collerait au premier nom de clé.
    return json.loads(raw.decode("utf-8-sig"))
