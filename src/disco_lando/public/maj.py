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

import json
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
    for asset in charge.get("assets") or []:
        nom = str(asset.get("name") or "")
        if nom.lower().endswith(".exe"):
            telechargement = asset.get("browser_download_url")
            break
    return {"version": tag, "telechargement": telechargement or "",
            "page": charge.get("html_url") or ""}


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
            "page": publiee["page"]}
