"""Raccorder son abonnement Claude ou ChatGPT.

Choix de l'utilisateur, 2026-08-13 : **les CLI officielles, pas une clé
d'API**. Le joueur installe l'outil de son fournisseur, se connecte une
fois, et Chris l'appelle — son abonnement paie, aucune clé ne transite
ni ne se stocke, et la garantie du §7 tient par construction puisque
c'est le même chemin que l'analyste de l'atelier.

**On ne télécharge rien à sa place.** Règle du projet, écrite pour
`cloudflared` : une dépendance installée en silence est une dépendance
que personne ne sait retirer. Quand un CLI manque, on rend la commande à
taper — visible, copiable, et c'est le joueur qui décide.
"""

from __future__ import annotations

import os
import json
import pathlib
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Fournisseur:
    """Un CLI d'abonnement, et de quoi guider celui qui ne l'a pas."""

    cle: str
    nom: str
    executable: str
    installation: str
    connexion: str


#: N'annoncer que les portes réellement implémentées par `analyste.py`.
#: Gemini a été retiré le 2026-08-16 : détecter son CLI sans savoir le
#: consommer donnait un faux raccordement, pire qu'une option absente.
FOURNISSEURS: tuple[Fournisseur, ...] = (
    Fournisseur(
        "claude", "Claude (Anthropic)", "claude",
        "npm install -g @anthropic-ai/claude-code",
        "claude   puis suis la connexion dans le navigateur"),
    Fournisseur(
        "codex", "ChatGPT (OpenAI)", "codex",
        "npm install -g @openai/codex",
        "codex login"),
)


def _executable_windows(nom: str) -> str | None:
    """Le binaire natif, jamais le shim `.CMD`.

    **Piège payé le 2026-08-07** : le shim npm est un `.CMD`, et le lancer
    réinterprète les arguments par cmd.exe, qui **mange les `%`** — or les
    consignes contiennent des `LIKE '%P4-AR%'`. La liste d'autorisation en
    sortait corrompue et chaque requête restait bloquée à l'approbation.
    Le vrai binaire est à côté du shim, dans `node_modules`.
    """
    trouve = shutil.which(nom)
    if trouve is None:
        return None
    chemin = pathlib.Path(trouve)
    if chemin.suffix.lower() not in (".cmd", ".bat"):
        return str(chemin)
    for candidat in (
            chemin.parent / "node_modules" / "@anthropic-ai" / "claude-code"
            / "bin" / f"{nom}.exe",
            chemin.parent / "node_modules" / ".bin" / f"{nom}.exe",
            chemin.with_suffix(".exe")):
        if candidat.exists():
            return str(candidat)
    return str(chemin)


def disponible(fournisseur: Fournisseur) -> str | None:
    """Le chemin du CLI s'il est installé, sinon None."""
    if os.name == "nt":
        return _executable_windows(fournisseur.executable)
    return shutil.which(fournisseur.executable)


def _fichier_selection() -> pathlib.Path:
    from .. import config

    dossier = pathlib.Path(config.DATA_DIR)
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier / "fournisseur.json"


def _par_cle(cle: str) -> Fournisseur | None:
    return next((fournisseur for fournisseur in FOURNISSEURS
                 if fournisseur.cle == str(cle).strip().lower()), None)


def selection_actuelle() -> Fournisseur | None:
    """La sélection persistée, ou None si le fichier est absent/illisible."""
    try:
        charge = json.loads(_fichier_selection().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _par_cle(charge.get("fournisseur", "")) \
        if isinstance(charge, dict) else None


def _activer(fournisseur: Fournisseur) -> dict[str, object]:
    """Active la porte dans l'environnement et le cœur déjà importé."""
    from .. import config

    modele = (config.ANALYSTE_CODEX if fournisseur.cle == "codex"
              else config.ANALYSTE_CLAUDE)
    os.environ["DISCO_ANALYSTE_FOURNISSEUR"] = fournisseur.cle
    os.environ["DISCO_ANALYSTE"] = modele
    config.ANALYSTE_FOURNISSEUR = fournisseur.cle
    config.ANALYSTE = modele
    return {"fournisseur": fournisseur.cle, "modele": modele,
            "actif": True, "cli_present": True}


def selectionner(cle: str) -> dict[str, object]:
    """Persiste puis active un fournisseur réellement installé.

    La présence est contrôlée avant l'écriture : un bouton périmé ne doit pas
    transformer le prochain démarrage en analyste annoncé mais muet.
    """
    fournisseur = _par_cle(cle)
    if fournisseur is None:
        raise ValueError(f"fournisseur inconnu : {cle}")
    if disponible(fournisseur) is None:
        raise RuntimeError(
            f"{fournisseur.nom} n'est pas installé ou n'est plus dans PATH")
    fichier = _fichier_selection()
    provisoire = fichier.with_suffix(".tmp")
    provisoire.write_text(json.dumps({"fournisseur": fournisseur.cle}),
                          encoding="utf-8")
    provisoire.replace(fichier)
    return _activer(fournisseur)


def appliquer_selection() -> dict[str, object]:
    """Applique au démarrage le choix explicite du joueur, sans en inventer.

    Détecter le premier CLI ne vaut pas consentement à consommer son quota.
    Sans choix persisté, le déterministe continue seul et l'onglet explique
    comment raccorder un abonnement.
    """
    fournisseur = selection_actuelle()
    if fournisseur is None:
        return {"fournisseur": None, "actif": False, "cli_present": False}
    present = disponible(fournisseur) is not None
    if not present:
        return {"fournisseur": fournisseur.cle, "actif": False,
                "cli_present": False}
    return _activer(fournisseur)


def etat() -> list[dict[str, object]]:
    """Ce que l'écran de raccordement affiche.

    Il montre **tous** les fournisseurs, installés ou non : un joueur qui
    n'en a aucun doit voir ce qu'il peut choisir, pas une liste vide qui
    ne lui dit rien.
    """
    selection = selection_actuelle()
    resultat = []
    for fournisseur in FOURNISSEURS:
        chemin = disponible(fournisseur)
        resultat.append({
            "cle": fournisseur.cle, "nom": fournisseur.nom,
            "chemin": chemin, "installe": chemin is not None,
            "selectionne": (selection is not None
                             and selection.cle == fournisseur.cle),
            "installation": fournisseur.installation,
            "connexion": fournisseur.connexion,
        })
    return resultat


def premier_disponible() -> Fournisseur | None:
    """Celui qu'on propose par défaut : le premier effectivement là."""
    for fournisseur in FOURNISSEURS:
        if disponible(fournisseur) is not None:
            return fournisseur
    return None
