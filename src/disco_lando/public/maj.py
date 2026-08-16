"""Se mettre à jour tout seul, sans que le joueur y pense.

Demande de l'utilisateur, 2026-08-13 : « il faut qu'il puisse être mis à
jour facilement lorsque je mets à jour de mon côté ». Un joueur qui doit
retourner sur un site pour chercher une version est un joueur qui reste
sur l'ancienne — c'est exactement ce qui s'est passé avec le compagnon
de guilde, où la mise à jour se téléchargeait sans jamais aboutir.

## Le piège, déjà payé une fois

**Un binaire qui se met à jour doit céder sa place.** Le compagnon
lançait la version neuve sans s'arrêter : celle-ci trouvait le port
occupé, ouvrait la page de l'ancienne, et le membre croyait la mise à
jour faite en restant sur la version périmée. Corrigé en 1.1.6 par une
relève qui juge **sur la version, jamais sur le port seul** — deux
binaires qui se tueraient mutuellement se relanceraient sans fin.

## Pourquoi une release et pas un `git pull`

Un `git pull` suppose git installé, un dépôt propre, et une résolution de
conflit quand le joueur a touché à un fichier. Une release est un
fichier : elle s'installe ou elle échoue, et l'échec se dit.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

#: Le dépôt public dédié — décidé le 2026-08-13. Le dépôt de travail
#: reste privé ; celui-ci ne porte que ce qu'un joueur exécute.
DEPOT_PUBLIC = "Rfauchoit/chris-roberts"
API_RELEASE = f"https://api.github.com/repos/{DEPOT_PUBLIC}/releases/latest"


def en_tuple(version: str) -> tuple[int, ...]:
    """« 1.2.10 » → (1, 2, 10), pour comparer autrement qu'en texte.

    Sans ça « 1.2.10 » passe pour antérieur à « 1.2.9 » : la comparaison
    de chaînes lit chiffre par chiffre.
    """
    morceaux = []
    for part in str(version).lstrip("vV").split("."):
        chiffres = "".join(c for c in part if c.isdigit())
        morceaux.append(int(chiffres) if chiffres else 0)
    return tuple(morceaux)


def derniere_publiee(timeout: float = 8.0) -> dict[str, str] | None:
    """La dernière version publiée, ou None si injoignable.

    **Sans jeton, et c'est tout l'intérêt du dépôt public** : un binaire
    distribué ne peut pas porter de clé d'accès — elle serait lisible par
    quiconque l'extrait.

    Une panne réseau rend None : la mise à jour attendra, elle ne casse
    rien. Même contrat que les étages LLM — en panne, on passe la main.
    """
    try:
        requete = urllib.request.Request(
            API_RELEASE, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            charge = json.load(reponse)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    tag = str(charge.get("tag_name") or "").lstrip("vV")
    if not tag:
        return None
    telechargement = None
    nom_executable = None
    actifs = charge.get("assets") or []
    for asset in actifs:
        nom = str(asset.get("name") or "")
        if nom.lower().endswith(".exe"):
            telechargement = asset.get("browser_download_url")
            nom_executable = nom
            break
    empreinte = ""
    if nom_executable:
        somme = next((actif for actif in actifs
                      if str(actif.get("name") or "").lower()
                      == f"{nom_executable}.sha256".lower()), None)
        if somme:
            empreinte = str(somme.get("browser_download_url") or "")
    return {"version": tag, "telechargement": telechargement or "",
            "empreinte": empreinte, "page": charge.get("html_url") or ""}


def maj_disponible(version_locale: str) -> dict[str, Any] | None:
    """Y a-t-il mieux que ce qui tourne ?

    La comparaison se fait **par numéro**, pas par différence : une
    version publiée plus ancienne que la nôtre — un retour arrière côté
    auteur — ne doit pas déclencher une « mise à jour » vers le passé.
    """
    publiee = derniere_publiee()
    if not publiee or not publiee.get("version"):
        return None
    if en_tuple(publiee["version"]) <= en_tuple(version_locale):
        return None
    return {"version": publiee["version"], "actuelle": version_locale,
            "telechargement": publiee["telechargement"],
            "empreinte": publiee.get("empreinte", ""),
            "page": publiee["page"]}


def _empreinte_fichier(chemin: pathlib.Path) -> str:
    somme = hashlib.sha256()
    with open(chemin, "rb") as fichier:
        while bloc := fichier.read(1 << 20):
            somme.update(bloc)
    return somme.hexdigest()


def _empreinte_distante(url: str, timeout: float) -> str:
    if not url:
        raise RuntimeError("la release ne publie pas le SHA-256 du binaire")
    with urllib.request.urlopen(url, timeout=timeout) as reponse:
        texte = reponse.read().decode("utf-8", errors="replace")
    empreinte = texte.strip().split()[0] if texte.strip() else ""
    if len(empreinte) != 64 or any(c not in "0123456789abcdefABCDEF"
                                  for c in empreinte):
        raise RuntimeError("l'empreinte publiée du binaire est illisible")
    return empreinte.lower()


def telecharger(publiee: dict[str, Any],
                destination: pathlib.Path | None = None,
                timeout: float = 900.0) -> pathlib.Path:
    """Télécharge le nouvel exe, contrôle son SHA-256, puis le rend visible."""
    adresse = str(publiee.get("telechargement") or "")
    if not adresse:
        raise RuntimeError("la release ne contient aucun exécutable")
    attendu = _empreinte_distante(
        str(publiee.get("empreinte") or ""), min(timeout, 30.0))
    if destination is None:
        from .. import config

        destination = (pathlib.Path(config.DATA_DIR) / "mises-a-jour" /
                       f"Chris-{publiee.get('version', 'nouveau')}.exe")
    destination.parent.mkdir(parents=True, exist_ok=True)
    provisoire = destination.with_suffix(destination.suffix + ".partiel")
    try:
        with urllib.request.urlopen(adresse, timeout=timeout) as reponse, \
                open(provisoire, "wb") as sortie:
            shutil.copyfileobj(reponse, sortie, length=1 << 20)
        recue = _empreinte_fichier(provisoire)
        if not secrets.compare_digest(recue, attendu):
            raise RuntimeError(
                "l'exécutable reçu diffère de son empreinte SHA-256")
        provisoire.replace(destination)
    except Exception:
        provisoire.unlink(missing_ok=True)
        raise
    return destination


def installer(publiee: dict[str, Any]) -> pathlib.Path | None:
    """Lance le binaire neuf en relève ; l'appelant peut alors se fermer."""
    if not getattr(sys, "frozen", False):
        return None
    nouveau = telecharger(publiee)
    subprocess.Popen(                              # noqa: S603
        [str(nouveau), "--installer-maj", sys.executable, str(os.getpid())],
        close_fds=True)
    return nouveau


def _pid_actif(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # PROCESS_QUERY_LIMITED_INFORMATION suffit et n'autorise ni écriture
        # ni terminaison du processus observé.
        processus = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not processus:
            return False
        ctypes.windll.kernel32.CloseHandle(processus)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def remplacer(executable_actuel: str, pid: int,
              timeout: float = 120.0) -> int:
    """Attend l'ancien Chris, le remplace atomiquement puis le relance.

    La copie se fait d'abord sous un nom voisin. Si elle est interrompue ou
    si `replace` échoue, l'ancien exécutable reste intact ; le `.nouveau`
    incomplet n'est jamais celui que le raccourci lance.
    """
    actuel = pathlib.Path(executable_actuel).resolve()
    if actuel.suffix.lower() != ".exe" or not actuel.name.lower().startswith(
            "chris"):
        raise ValueError("la relève ne peut remplacer qu'un exécutable Chris")
    limite = time.monotonic() + timeout
    while _pid_actif(pid) and time.monotonic() < limite:
        time.sleep(0.2)
    if _pid_actif(pid):
        return 1

    provisoire = actuel.with_suffix(".nouveau.exe")
    try:
        shutil.copy2(sys.executable, provisoire)
        if not secrets.compare_digest(_empreinte_fichier(sys.executable),
                                      _empreinte_fichier(provisoire)):
            raise RuntimeError("la copie locale de mise à jour est incomplète")
        os.replace(provisoire, actuel)
    except Exception:
        provisoire.unlink(missing_ok=True)
        raise
    subprocess.Popen([str(actuel)], close_fds=True)  # noqa: S603
    return 0
