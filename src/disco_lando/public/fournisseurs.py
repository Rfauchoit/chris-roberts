"""Raccorder son abonnement Claude, ChatGPT ou Gemini.

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


#: L'ordre est celui de la proposition à l'écran, pas une préférence
#: technique : les trois se valent pour ce qu'on leur demande.
FOURNISSEURS: tuple[Fournisseur, ...] = (
    Fournisseur(
        "claude", "Claude (Anthropic)", "claude",
        "npm install -g @anthropic-ai/claude-code",
        "claude   puis suis la connexion dans le navigateur"),
    Fournisseur(
        "codex", "ChatGPT (OpenAI)", "codex",
        "npm install -g @openai/codex",
        "codex login"),
    Fournisseur(
        "gemini", "Gemini (Google)", "gemini",
        "npm install -g @google/gemini-cli",
        "gemini   puis choisis « Login with Google »"),
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


def etat() -> list[dict[str, object]]:
    """Ce que l'écran de raccordement affiche.

    Il montre **tous** les fournisseurs, installés ou non : un joueur qui
    n'en a aucun doit voir ce qu'il peut choisir, pas une liste vide qui
    ne lui dit rien.
    """
    return [{
        "cle": f.cle, "nom": f.nom,
        "chemin": disponible(f),
        "installe": disponible(f) is not None,
        "installation": f.installation,
        "connexion": f.connexion,
    } for f in FOURNISSEURS]


def premier_disponible() -> Fournisseur | None:
    """Celui qu'on propose par défaut : le premier effectivement là."""
    for fournisseur in FOURNISSEURS:
        if disponible(fournisseur) is not None:
            return fournisseur
    return None
