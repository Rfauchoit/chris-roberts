"""Verrou d'instance unique pour les services de longue durée.

**Six bots Discord tournaient en même temps le 2026-08-11** — accumulés au
fil des sessions du lanceur, chacun répondant aux mêmes messages. Rien
n'empêchait un deuxième `disco discord` de démarrer : le cœur a son garde
(le port refuse le doublon), les services sans port n'en avaient aucun.

Le verrou est un fichier à côté des bases (`data/<service>.lock`) portant le
PID et l'horodatage. **Un verrou dont le PID est mort est repris sans un
mot** : après un crash ou un arrêt brutal, le service doit redémarrer sans
qu'on aille effacer un fichier à la main — un garde qui exige une
intervention après chaque incident finit contourné.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import UTC, datetime

from . import config


class DejaEnMarche(RuntimeError):
    """Une autre instance vivante tient le verrou."""

    def __init__(self, service: str, pid: int, depuis: str):
        super().__init__(
            f"{service} est déjà en marche (PID {pid}, depuis {depuis}) — "
            "une seule instance à la fois, sinon chaque message reçoit "
            "autant de réponses que de doublons")
        self.pid = pid
        self.depuis = depuis


def _pid_vivant(pid: int) -> bool:
    """Le processus existe-t-il encore ? Sans le toucher.

    **Jamais `os.kill(pid, 0)` sous Windows** : pour tout signal autre que
    CTRL_C/CTRL_BREAK, CPython appelle TerminateProcess — le « contrôle de
    vie » tuerait le processus contrôlé. Mesuré aussi : un PID terminé y
    passait pour vivant (handle encore ouvrable). On interroge le vrai état
    par l'API : handle + code de sortie STILL_ACTIVE (259).
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquerir(service: str, *, dossier: pathlib.Path | None = None
             ) -> pathlib.Path:
    """Prend le verrou du service ou lève `DejaEnMarche`.

    Rend le chemin du fichier ; le relâcher est `liberer()`, mais un oubli
    n'est pas grave — le prochain démarrage verra le PID mort et reprendra.
    """
    racine = pathlib.Path(dossier or config.DATA_DIR)
    racine.mkdir(parents=True, exist_ok=True)
    chemin = racine / f"{service}.lock"
    if chemin.exists():
        try:
            contenu = json.loads(chemin.read_text(encoding="utf-8"))
            pid = int(contenu.get("pid", 0))
            depuis = str(contenu.get("depuis", "?"))
        except (OSError, ValueError):
            # Verrou illisible : un fichier corrompu ne doit pas bloquer le
            # service pour toujours — on le reprend.
            pid, depuis = 0, "?"
        if pid != os.getpid() and _pid_vivant(pid):
            raise DejaEnMarche(service, pid, depuis)
    # **Le commit que ce service a chargé**, ajouté le 2026-08-19 : un bot
    # démarré avant un `git pull` tourne sur du code périmé et le voyant du
    # lanceur reste vert — exactement l'angle mort qui a fait servir de
    # vieilles réponses pendant deux heures côté cœur. Le verrou est le
    # seul endroit qu'un processus long partage déjà avec le lanceur.
    from .revision import commit_court

    chemin.write_text(json.dumps({
        "pid": os.getpid(),
        "depuis": datetime.now(UTC).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
        "commit": commit_court(),
    }), encoding="utf-8")
    return chemin


def liberer(chemin: pathlib.Path) -> None:
    """Efface le verrou — seulement s'il est encore le nôtre.

    Un service qui a mis longtemps à s'arrêter pourrait sinon effacer le
    verrou du remplaçant déjà démarré.
    """
    try:
        contenu = json.loads(chemin.read_text(encoding="utf-8"))
        if int(contenu.get("pid", 0)) == os.getpid():
            chemin.unlink()
    except (OSError, ValueError):
        pass
