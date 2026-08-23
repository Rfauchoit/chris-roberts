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
        os.environ["DISCO_DATA_DIR"] = str(base)
    base = pathlib.Path(os.environ["DISCO_DATA_DIR"])
    base.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DISCO_DB_PATH", str(base / "disco_lando.db"))
    # Le déterministe seul chez un joueur : l'analyste se branche sur son
    # abonnement, jamais par défaut et jamais à son insu.
    os.environ.setdefault("DISCO_ROUTER", "deterministic")
    return pathlib.Path(os.environ["DISCO_DATA_DIR"])


def _commande_interne(arguments: list[str] | None = None) -> int | None:
    """Sous-commandes cachées qui servent la porte LLM du binaire figé.

    Elles passent avant Tk et Uvicorn : le CLI fournisseur relance le même
    exécutable avec des pipes stdio, il ne doit ni ouvrir de fenêtre ni tenter
    de reprendre le port du cœur principal.
    """
    global _PORT

    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not arguments or arguments[0] not in {
            "--porte-analyste", "--mcp-analyste", "--installer-maj",
            "--diagnostic-public"}:
        return None
    if arguments[0] == "--installer-maj":
        if len(arguments) != 3:
            return 2
        from disco_lando.public import maj
        return maj.remplacer(arguments[1], int(arguments[2]))
    _installer_le_dossier_de_donnees()
    if arguments[0] == "--porte-analyste":
        from disco_lando.porte_analyste import main as porte
        return porte(arguments[1:])
    if arguments[0] == "--diagnostic-public":
        import json

        from disco_lando import config, distribution
        from disco_lando.public import fournisseurs
        from disco_lando.public.version import VERSION

        fournisseur = fournisseurs.appliquer_selection()
        resultat = {
            "version": VERSION,
            "fournisseur": fournisseur,
            "base_compatible": not distribution.verifier_base(
                pathlib.Path(config.DB_PATH)),
        }
        restants = arguments[1:]
        candidat = next((valeur for valeur in restants
                          if valeur != "--coeur"), None)
        if candidat:
            manifeste = json.loads(pathlib.Path(candidat).read_text(
                encoding="utf-8"))
            resultat["base_a_rafraichir"] = _base_a_rafraichir(manifeste)
        if "--coeur" in restants:
            _PORT = _port_libre()
            os.environ["DISCO_CORE_URL"] = f"http://127.0.0.1:{_PORT}"
            _demarrer_le_coeur()
            limite = time.time() + 45
            resultat["coeur"] = False
            while time.time() < limite:
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{_PORT}/health",
                            timeout=1) as reponse:
                        resultat["coeur"] = reponse.status == 200
                        break
                except Exception:                              # noqa: BLE001
                    time.sleep(0.2)
        print(json.dumps(resultat, ensure_ascii=False))
        return 0 if resultat.get("coeur", True) else 1
    from disco_lando.mcp_analyste import main as mcp
    return mcp()


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


def _journaliser(message: str) -> None:
    """Écrit dans `chris.log`, à côté de la base.

    Un binaire fenêtré n'a ni console ni stderr : sans fichier, une
    panne au démarrage est un mur pour celui qui la subit **et** pour
    celui qui doit la corriger.
    """
    try:
        import datetime as dt

        chemin = pathlib.Path(
            os.environ.get("DISCO_DATA_DIR", ".")) / "chris.log"
        with open(chemin, "a", encoding="utf-8") as fichier:
            fichier.write(
                f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {message}" + "\n")
    except Exception:                                          # noqa: BLE001
        pass


def _base_presente() -> bool:
    from disco_lando import config, distribution

    chemin = pathlib.Path(config.DB_PATH)
    return not distribution.verifier_base(chemin)


def _fichier_manifeste_local() -> pathlib.Path:
    from disco_lando import config

    return pathlib.Path(config.DATA_DIR) / "base-installee.json"


def _ecrire_manifeste_local(manifeste: dict) -> None:
    import json

    fichier = _fichier_manifeste_local()
    provisoire = fichier.with_suffix(".tmp")
    provisoire.write_text(json.dumps(
        manifeste, ensure_ascii=False, indent=2), encoding="utf-8")
    provisoire.replace(fichier)


def _manifeste_local() -> dict:
    import json

    try:
        charge = json.loads(
            _fichier_manifeste_local().read_text(encoding="utf-8"))
        return charge if isinstance(charge, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifeste_distant(timeout: float = 30.0) -> dict | None:
    import json

    from disco_lando import distribution
    from disco_lando.public import maj

    adresse = (f"https://github.com/{maj.DEPOT_PUBLIC}/releases/latest/"
               f"download/{distribution.NOM_MANIFESTE}")
    try:
        with urllib.request.urlopen(adresse, timeout=timeout) as reponse:
            charge = json.load(reponse)
        return charge if isinstance(charge, dict) else None
    except Exception as exc:                                   # noqa: BLE001
        _journaliser(f"manifeste de base injoignable : {exc}")
        return None


def _build_installe() -> str | None:
    import sqlite3

    from disco_lando import config

    try:
        con = sqlite3.connect(
            f"file:{pathlib.Path(config.DB_PATH)}?mode=ro", uri=True)
        try:
            ligne = con.execute(
                "SELECT build_id FROM ingest_runs WHERE status='ok' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            return str(ligne[0]) if ligne and ligne[0] is not None else None
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _base_a_rafraichir(manifeste: dict) -> bool:
    """Compare contenu, schéma et build — jamais seulement la présence."""
    from disco_lando import distribution

    distribution.verifier_manifeste(manifeste)
    if not _base_presente():
        return True
    build = _build_installe()
    if build is None or build != str(manifeste.get("build_id")):
        return True
    locale = _manifeste_local()
    if (locale.get("sha256") == manifeste.get("sha256")
            and locale.get("schema_sha256") == manifeste.get("schema_sha256")):
        return False
    # Installation antérieure au manifeste local : le schéma et le build
    # prouvent déjà la compatibilité, on adopte seulement la métadonnée.
    _ecrire_manifeste_local(manifeste)
    return False


def _telecharger_la_base(accueil: Accueil, manifeste: dict) -> bool:
    """Récupère la base publiée. Rend False si ça échoue, en le disant.

    L'empreinte du manifeste est vérifiée : un téléchargement coupé à
    99 % produirait une base illisible, et le message « aucun résultat »
    qui suivrait serait incompréhensible.
    """
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
                               manifeste=manifeste)
        _ecrire_manifeste_local(manifeste)
        archive.unlink(missing_ok=True)
        return True
    except Exception as exc:                                   # noqa: BLE001
        archive.unlink(missing_ok=True)
        accueil.dire("Impossible de récupérer les données",
                     f"{exc} — vérifie ta connexion et relance")
        return False


def _preparer_la_base(accueil: Accueil) -> bool:
    """Rafraîchit une base ancienne, ou garde la compatible hors ligne."""
    from disco_lando import distribution
    from disco_lando.public.version import VERSION

    manifeste = _manifeste_distant()
    if manifeste is None:
        if _base_presente():
            accueil.dire("Données locales vérifiées", "réseau indisponible")
            return True
        accueil.dire("Aucune base compatible",
                     "connexion nécessaire pour récupérer les données")
        return False
    try:
        distribution.verifier_manifeste(manifeste)
        version_base = str(manifeste.get("version_application") or "")
        if version_base and version_base != VERSION:
            raise ValueError(
                f"la base publiée accompagne Chris {version_base}, "
                f"pas la version {VERSION}")
        if _base_a_rafraichir(manifeste):
            return _telecharger_la_base(accueil, manifeste)
        accueil.dire("Données du jeu vérifiées",
                     f"build {manifeste.get('build_id') or '?'}")
        return True
    except Exception as exc:                                   # noqa: BLE001
        accueil.dire("Base incompatible", str(exc))
        _journaliser(f"base incompatible : {exc}")
        return False


def _installer_mise_a_jour(accueil: Accueil) -> bool:
    """Installe une version neuve avant qu'elle rencontre une vieille base."""
    if not getattr(sys, "frozen", False):
        return False
    from disco_lando.public import maj
    from disco_lando.public.version import VERSION

    accueil.dire("Vérification de la version…")
    trouve = maj.maj_disponible(VERSION)
    if not trouve:
        return False
    try:
        maj.installer(trouve)
    except Exception as exc:                                   # noqa: BLE001
        accueil.dire("Mise à jour refusée", str(exc))
        _journaliser(f"mise à jour refusée : {exc}")
        return False
    accueil.dire(f"Chris {trouve['version']} est vérifié",
                 "relève de cette version…")
    accueil.after(800, accueil.destroy)
    accueil.mainloop()
    return True


def _nettoyer_mises_a_jour() -> None:
    """Retire les exe de relève anciens ; le plus récent peut être verrouillé."""
    from disco_lando import config

    dossier = pathlib.Path(config.DATA_DIR) / "mises-a-jour"
    if not dossier.exists():
        return
    courant = pathlib.Path(sys.executable).resolve()
    for fichier in dossier.glob("Chris-*.exe"):
        try:
            if fichier.resolve() != courant:
                fichier.unlink()
        except OSError:
            pass  # la relève qui vient de nous lancer n'a pas encore quitté


def _demarrer_le_coeur() -> threading.Thread:
    """Le cœur tourne **dans ce processus**, sur la boucle locale.

    Pas de second exécutable à lancer ni à surveiller : c'est ce qui
    rendait le lanceur de l'atelier compliqué, et un joueur n'a aucune
    raison de savoir qu'un serveur existe.
    """
    def servir() -> None:
        import traceback

        try:
            _servir_vraiment()
        except BaseException:                                  # noqa: BLE001
            # **Un thread qui meurt en mode fenêtré ne dit rien.** Sans
            # cette trace, le symptôme est un processus vivant qui
            # n'écoute sur aucun port — impossible à diagnostiquer chez
            # un joueur, et déjà coûteux ici (2026-08-13).
            _journaliser("le moteur n'a pas démarré : "
                         + traceback.format_exc())

    def _servir_vraiment() -> None:
        import uvicorn

        from disco_lando.api import app

        # **`uvicorn.run()` ne marche pas dans un thread secondaire.**
        # Il installe des gestionnaires de signaux, réservés au thread
        # principal, et lève `ValueError: signal only works in main
        # thread` — avalée par le thread, donc invisible. En mode
        # fenêtré, où stderr est fermé, le symptôme est un processus
        # vivant qui n'écoute sur aucun port et ne dit rien.
        #
        # Mes essais concluants étaient tous compilés `-Console` :
        # c'est précisément le genre de différence qui fait livrer un
        # binaire cassé (2026-08-13).
        serveur = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=_PORT, log_level="critical"))
        _journaliser("uvicorn : import fait, lancement")
        serveur.install_signal_handlers = lambda: None
        serveur.run()

    fil = threading.Thread(target=servir, daemon=True)
    fil.start()
    return fil


def _attendre_le_coeur(accueil: Accueil, secondes: float = 180.0) -> bool:
    """**Trois minutes, pas quarante secondes.**

    Mesuré le 2026-08-13 : le cœur met plus d'une minute à répondre au
    premier lancement d'un binaire — PyInstaller déplie son archive, puis
    l'import de FastAPI, du routeur et des 72 outils se fait à froid. Avec
    quarante secondes, l'application abandonnait sur « le moteur ne
    répond pas » alors qu'il démarrait très bien, et le diagnostic
    prenait une heure parce que le symptôme accusait le moteur.

    L'écran d'accueil compte les secondes : une attente qu'on voit
    avancer est une attente qu'on accepte.
    """
    accueil.dire("Démarrage du moteur…", "quelques dizaines de secondes "
                                          "au premier lancement")
    limite = time.time() + secondes
    while time.time() < limite:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{_PORT}/health", timeout=2) as reponse:
                if reponse.status == 200:
                    return True
        except Exception:                                      # noqa: BLE001
            reste = int(limite - time.time())
            accueil.dire("Démarrage du moteur…",
                         f"encore {reste} s au plus")
            time.sleep(0.6)
    accueil.dire("Le moteur ne répond pas",
                 "relance l'application ; si ça persiste, signale-le")
    return False


#: Fixé au démarrage, avant tout import du cœur.
_PORT = 8000


def main() -> int:
    global _PORT

    interne = _commande_interne()
    if interne is not None:
        return interne

    # **En mode fenêtré, stdout et stderr sont `None`.** Une bibliothèque
    # qui y écrit lève `AttributeError`, et l'application meurt sans un
    # mot. On leur donne un puits avant tout import du cœur.
    for flux in ("stdout", "stderr"):
        if getattr(sys, flux, None) is None:
            setattr(sys, flux, open(os.devnull, "w", encoding="utf-8"))

    _installer_le_dossier_de_donnees()
    # La sélection persistée active réellement l'étage avant l'import du
    # cœur. Sans cette ligne, l'onglet détectait un CLI mais toutes les
    # questions restaient déterministes jusqu'à la fermeture du processus.
    from disco_lando.public import fournisseurs
    fournisseurs.appliquer_selection()
    _nettoyer_mises_a_jour()
    # **Chaque étape se trace.** Un binaire fenêtré qui s'arrête quelque
    # part ne dit rien, et deviner coûte des heures : la seule question
    # utile est « jusqu'où est-il allé ». Le journal y répond en une
    # ligne (2026-08-13).
    _journaliser(f"--- démarrage, PID {os.getpid()}, "
                 f"gelé={getattr(sys, 'frozen', False)}")

    _PORT = _port_libre()
    _journaliser(f"port choisi : {_PORT}")
    # `client.py` lit cette variable pour joindre le cœur : sans elle, la
    # fenêtre interrogerait 8000 pendant que le cœur écoute ailleurs.
    os.environ["DISCO_CORE_URL"] = f"http://127.0.0.1:{_PORT}"

    accueil = Accueil()
    accueil.update()
    _journaliser("accueil affiché")
    if _installer_mise_a_jour(accueil):
        return 0
    if not _preparer_la_base(accueil):
        accueil.after(9000, accueil.destroy)
        accueil.mainloop()
        return 1
    _journaliser("base présente, démarrage du cœur")
    _demarrer_le_coeur()
    if not _attendre_le_coeur(accueil):
        accueil.after(9000, accueil.destroy)
        accueil.mainloop()
        return 1
    accueil.destroy()

    _journaliser("cœur prêt, ouverture de la fenêtre")

    from disco_lando.public.fenetre import lancer

    lancer(_hote_grave())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
