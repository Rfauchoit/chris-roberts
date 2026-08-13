"""Le point d'entrée du Chris que chacun installe.

**Les imports du cœur sont absolus, et doivent le rester** : PyInstaller
lance ce fichier comme un script et non comme un module de paquet, si
bien qu'un `from .. import config` lève « attempted relative import with
no known parent package » — et le binaire meurt sans fenêtre.

Un joueur télécharge un fichier et double-clique. Tout le reste — la
base de jeu, le cœur, la fenêtre — se met en place tout seul, dans cet
ordre et sans qu'il ait rien à comprendre.

**Ce qui doit marcher du premier coup**, parce qu'un premier lancement
raté ne se rejoue pas : la base se télécharge si elle manque, le cœur
démarre en fond, et la fenêtre s'ouvre en disant où il en est. Une
erreur à n'importe laquelle de ces étapes doit se **lire**, jamais
laisser une fenêtre vide.
"""

from __future__ import annotations

import os
import pathlib
import socket
import sys
import threading
import time
import tkinter as tk
import urllib.request


def _installer_le_dossier_de_donnees() -> pathlib.Path:
    """Ranger la base et les réglages là où ils survivent.

    **Sans ça, rien ne marche en binaire** : `config.DATA_DIR` se calcule
    depuis l'emplacement du paquet, et PyInstaller le déplie dans un
    dossier temporaire effacé à la fermeture. Le joueur retéléchargerait
    79 Mo à chaque lancement — s'il arrivait jusque-là.

    Mesuré le 2026-08-13 : le premier binaire mourait sur ce point.

    Doit être appelé **avant** le premier import de `config`, qui fige
    ses chemins à l'import.
    """
    if "DISCO_DATA_DIR" not in os.environ:
        base = pathlib.Path(
            os.environ.get("LOCALAPPDATA")
            or pathlib.Path.home() / ".local" / "share") / "ChrisRoberts"
        base.mkdir(parents=True, exist_ok=True)
        os.environ["DISCO_DATA_DIR"] = str(base)
        os.environ.setdefault("DISCO_DB_PATH", str(base / "disco_lando.db"))
    # Le déterministe seul chez un joueur : l'analyste se branche sur son
    # abonnement, jamais par défaut et jamais à son insu.
    os.environ.setdefault("DISCO_ROUTER", "deterministic")
    return pathlib.Path(os.environ["DISCO_DATA_DIR"])


def _port_libre(prefere: int = 8000) -> int:
    """Le port du cœur, en évitant celui qui est déjà pris.

    Un joueur peut faire tourner autre chose sur 8000 — et sur la machine
    de l'auteur, c'est le cœur de l'atelier. Un port occupé faisait
    mourir le binaire sans un mot.
    """
    for port in (prefere, 8010, 8020, 8080, 0):
        with socket.socket() as sonde:
            try:
                sonde.bind(("127.0.0.1", port))
                return sonde.getsockname()[1]
            except OSError:
                continue
    return prefere

#: L'adresse de l'hôte, gravée à la compilation. Elle sert à la remontée
#: des usages, jamais à répondre : les réponses viennent du cœur local.
def _hote_grave() -> str:
    """L'URL de l'atelier, lue dans le fichier gravé par PyInstaller.

    Le compagnon de guilde a payé ce piège : gravé sur `localhost`, il
    pointait chez le membre où rien n'écoutait, et le symptôme
    ressemblait à un bug du compagnon. Ici l'absence est **sans
    conséquence** — la remontée attendra, la file la garde — donc on ne
    bloque pas.
    """
    for base in (getattr(sys, "_MEIPASS", None), pathlib.Path(__file__).parent):
        if not base:
            continue
        fichier = pathlib.Path(base) / "hote.txt"
        if fichier.exists():
            adresse = fichier.read_text(encoding="utf-8").strip()
            if adresse and "127.0.0.1" not in adresse:
                return adresse
    return os.environ.get("CHRIS_HOTE", "")


class Accueil(tk.Tk):
    """L'écran qui parle pendant que tout se met en place.

    Sans lui, le premier lancement est une fenêtre noire pendant le
    téléchargement de 79 Mo — et un joueur qui ferme croit que ça ne
    marche pas.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("Chris Roberts")
        self.geometry("560x220")
        self.configure(bg="#12151c")
        tk.Label(self, text="Chris Roberts", bg="#12151c", fg="#e6e9ef",
                 font=("Segoe UI", 18, "bold")).pack(pady=(46, 6))
        self._etat = tk.Label(self, text="Démarrage…", bg="#12151c",
                              fg="#8b93a7", font=("Segoe UI", 10))
        self._etat.pack()
        self._detail = tk.Label(self, text="", bg="#12151c", fg="#8b93a7",
                                font=("Segoe UI", 9))
        self._detail.pack(pady=(4, 0))

    def dire(self, texte: str, detail: str = "") -> None:
        self._etat.configure(text=texte)
        self._detail.configure(text=detail)
        self.update_idletasks()


def _base_presente() -> bool:
    from disco_lando import config

    chemin = pathlib.Path(config.DB_PATH)
    return chemin.exists() and chemin.stat().st_size > 1_000_000


def _telecharger_la_base(accueil: Accueil) -> bool:
    """Récupère la base publiée. Rend False si ça échoue, en le disant.

    L'empreinte du manifeste est vérifiée : un téléchargement coupé à
    99 % produirait une base illisible, et le message « aucun résultat »
    qui suivrait serait incompréhensible.
    """
    import json

    from disco_lando import config, distribution
    from disco_lando.public import maj

    accueil.dire("Récupération des données du jeu…",
                 "environ 79 Mo, une seule fois")
    base_url = (f"https://github.com/{maj.DEPOT_PUBLIC}/releases/latest/"
                "download/")
    cible = pathlib.Path(config.DATA_DIR)
    cible.mkdir(parents=True, exist_ok=True)
    archive = cible / distribution.NOM_ARCHIVE
    try:
        with urllib.request.urlopen(
                base_url + distribution.NOM_MANIFESTE, timeout=30) as reponse:
            manifeste = json.load(reponse)
        with urllib.request.urlopen(
                base_url + distribution.NOM_ARCHIVE, timeout=900) as reponse, \
                open(archive, "wb") as sortie:
            lus = 0
            while bloc := reponse.read(1 << 20):
                sortie.write(bloc)
                lus += len(bloc)
                accueil.dire("Récupération des données du jeu…",
                             f"{lus / 1048576:.0f} Mo")
        accueil.dire("Installation…", "décompression")
        distribution.installer(archive, pathlib.Path(config.DB_PATH),
                               attendu=manifeste.get("sha256"))
        archive.unlink(missing_ok=True)
        return True
    except Exception as exc:                                   # noqa: BLE001
        accueil.dire("Impossible de récupérer les données",
                     f"{exc} — vérifie ta connexion et relance")
        return False


def _demarrer_le_coeur() -> threading.Thread:
    """Le cœur tourne **dans ce processus**, sur la boucle locale.

    Pas de second exécutable à lancer ni à surveiller : c'est ce qui
    rendait le lanceur de l'atelier compliqué, et un joueur n'a aucune
    raison de savoir qu'un serveur existe.
    """
    def servir() -> None:
        import uvicorn

        from disco_lando.api import app

        uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="warning")

    fil = threading.Thread(target=servir, daemon=True)
    fil.start()
    return fil


def _attendre_le_coeur(accueil: Accueil, secondes: float = 40.0) -> bool:
    accueil.dire("Démarrage du moteur…")
    limite = time.time() + secondes
    while time.time() < limite:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{_PORT}/health", timeout=2) as reponse:
                if reponse.status == 200:
                    return True
        except Exception:                                      # noqa: BLE001
            time.sleep(0.6)
    accueil.dire("Le moteur ne répond pas",
                 "relance l'application ; si ça persiste, signale-le")
    return False


#: Fixé au démarrage, avant tout import du cœur.
_PORT = 8000


def main() -> int:
    global _PORT

    _installer_le_dossier_de_donnees()
    _PORT = _port_libre()
    # `client.py` lit cette variable pour joindre le cœur : sans elle, la
    # fenêtre interrogerait 8000 pendant que le cœur écoute ailleurs.
    os.environ["DISCO_CORE_URL"] = f"http://127.0.0.1:{_PORT}"

    accueil = Accueil()
    accueil.update()
    if not _base_presente() and not _telecharger_la_base(accueil):
        accueil.after(9000, accueil.destroy)
        accueil.mainloop()
        return 1
    _demarrer_le_coeur()
    if not _attendre_le_coeur(accueil):
        accueil.after(9000, accueil.destroy)
        accueil.mainloop()
        return 1
    accueil.destroy()

    from disco_lando.public.fenetre import lancer

    lancer(_hote_grave())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
