"""Renvoyer ce qu'on vit à l'atelier — côté joueur.

C'est la moitié cliente de `usages.py`. Le joueur pose ses questions ;
son Chris garde chaque tour et l'envoie à l'hôte, qui en fera des cas de
test pour améliorer le bot de tout le monde.

## Les trois règles qui la rendent acceptable

**Elle se dit.** L'activation par défaut a été choisie par l'utilisateur
le 2026-08-13, et elle n'est défendable qu'à une condition : que le
premier écran l'annonce en clair, montre ce qui part, et que le réglage
pour couper soit visible. `ANNONCE` porte ce texte — il est ici, dans le
code, pour qu'on ne puisse pas le livrer sans lui.

**Elle épure avant d'envoyer, pas seulement à l'arrivée.** Le filtre de
l'hôte protège l'hôte ; celui-ci protège le joueur, y compris contre un
hôte mal configuré. Une donnée personnelle qui ne quitte jamais la
machine ne peut fuir nulle part.

**Elle n'empêche jamais de jouer.** Aucune erreur d'envoi ne remonte à
l'utilisateur, aucune ne bloque une réponse : c'est le contrat du
journal, et il vaut à plus forte raison pour un service accessoire.

## La file de secours

Choix de l'utilisateur : tunnel **plus** file. L'hôte est un PC qui dort
la nuit ; sans file, tout ce qui est posé pendant ce temps est perdu —
c'est-à-dire, statistiquement, une bonne part des questions du soir. La
file est bornée en nombre et en âge : un joueur sans réseau ne doit pas
accumuler indéfiniment.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from .. import usages

#: Ce que le premier écran doit dire, mot pour mot.
ANNONCE = """Chris s'améliore avec les questions qu'on lui pose.

Les tiennes sont envoyées à l'auteur du projet — la question, la réponse
reçue, et l'outil qui a servi — pour corriger ce qui répond mal. Ce qui
identifierait ta machine ou ton compte (chemins de fichiers, adresses,
pseudos) est retiré avant l'envoi.

Tu peux couper ce partage à tout moment dans les réglages."""

#: Bornes de la file : au-delà, on jette le plus ancien. Un joueur hors
#: ligne pendant un mois ne doit pas garder un fichier qui enfle.
FILE_MAX = 500
AGE_MAX_JOURS = 30
PERIODE_ENVOI = 900.0

_VERROU = threading.Lock()


def _dossier() -> pathlib.Path:
    from .. import config

    chemin = pathlib.Path(config.DATA_DIR)
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def identifiant_instance() -> str:
    """Un nombre au hasard, tiré une fois, sans lien avec aucun compte.

    Il ne sert qu'à **recoudre les conversations** côté atelier : sans
    lui, les tours arrivent en vrac et le banc de conversation ne peut
    rien en faire. Il ne dit pas qui parle, seulement que c'est la même
    installation.
    """
    fichier = _dossier() / "instance.txt"
    if fichier.exists():
        return fichier.read_text(encoding="utf-8").strip()
    genere = f"anon-{uuid.uuid4().hex[:16]}"
    fichier.write_text(genere, encoding="utf-8")
    return genere


def _fichier_file() -> pathlib.Path:
    return _dossier() / "remontee-file.jsonl"


def active() -> bool:
    """Par défaut oui — mais l'annonce a été faite, et le réglage existe."""
    fichier = _dossier() / "remontee.json"
    if not fichier.exists():
        return True
    try:
        return bool(json.loads(fichier.read_text(encoding="utf-8"))
                    .get("active", True))
    except (ValueError, OSError):
        return True


def regler(actif: bool) -> None:
    (_dossier() / "remontee.json").write_text(
        json.dumps({"active": bool(actif)}), encoding="utf-8")


def enfiler(tour: dict[str, Any]) -> None:
    """Garde un tour pour l'envoi suivant. N'échoue jamais.

    L'épuration se fait **ici**, avant même l'écriture sur disque : ce qui
    n'entre pas dans la file ne peut pas partir, ni traîner en clair sur
    la machine du joueur.
    """
    if not active():
        return
    try:
        propre = usages.epurer(tour)
        if not (propre.get("question") or "").strip():
            return
        propre["horodatage"] = propre.get("horodatage") or time.strftime(
            "%Y-%m-%dT%H:%M:%S")
        with _VERROU:
            fichier = _fichier_file()
            lignes = (fichier.read_text(encoding="utf-8").splitlines()
                      if fichier.exists() else [])
            lignes.append(json.dumps(propre, ensure_ascii=False))
            fichier.write_text("\n".join(lignes[-FILE_MAX:]) + "\n",
                               encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass          # une remontée ne casse jamais une réponse


def _purger(lignes: list[str]) -> list[str]:
    """Jette ce qui est trop vieux pour servir encore."""
    limite = time.time() - AGE_MAX_JOURS * 86400
    gardees = []
    for ligne in lignes:
        try:
            quand = time.mktime(time.strptime(
                json.loads(ligne)["horodatage"], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, KeyError, OSError):
            gardees.append(ligne)
            continue
        if quand >= limite:
            gardees.append(ligne)
    return gardees


def envoyer(url_hote: str, timeout: float = 10.0) -> int:
    """Vide la file vers l'hôte. Rend le nombre de tours acceptés.

    **En cas d'échec, la file reste intacte** — c'est tout l'intérêt :
    l'hôte est un PC qui dort la nuit, et ce qui est posé pendant ce
    temps doit survivre jusqu'au réveil.
    """
    if not active() or not url_hote:
        return 0
    try:
        with _VERROU:
            fichier = _fichier_file()
            if not fichier.exists():
                return 0
            lignes = _purger(fichier.read_text(encoding="utf-8").splitlines())
            if not lignes:
                fichier.unlink(missing_ok=True)
                return 0
            lot = lignes[:usages.TOURS_MAX_PAR_ENVOI]

        charge = json.dumps({
            "instance": identifiant_instance(),
            "tours": [json.loads(ligne) for ligne in lot],
        }, ensure_ascii=False).encode("utf-8")
        requete = urllib.request.Request(
            f"{url_hote.rstrip('/')}/api/usages", data=charge,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            gardes = int(json.load(reponse).get("gardes", 0))

        with _VERROU:
            restantes = lignes[len(lot):]
            if restantes:
                _fichier_file().write_text("\n".join(restantes) + "\n",
                                           encoding="utf-8")
            else:
                _fichier_file().unlink(missing_ok=True)
        return gardes
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0      # l'hôte dort : la file attend, rien n'est perdu
