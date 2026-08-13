"""La fenêtre du Chris que chacun installe — clé en main.

Le lanceur de l'atelier expose l'ingestion, le balayage, le tunnel, les
services, la publication : rien de tout cela n'a de sens chez un joueur.
Celui-ci fait trois choses et pas une de plus — poser une question,
raccorder un abonnement, régler le partage — et vérifie au démarrage
qu'il n'est pas périmé.

**Deux règles reprises de l'atelier, chacune payée.** L'ordre d'empilage
compte : une zone de texte en `expand=True` repousse hors de la fenêtre
ce qu'on empile après elle, si bien que la barre de saisie n'apparaissait
qu'en plein écran — ce qui doit rester visible se pose **avant**
l'élément extensible. Et on ne télécharge jamais rien à la place de
l'utilisateur : quand un CLI manque, on affiche la commande à taper.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk

from . import fournisseurs, maj, remontee

VERSION = "0.1.0"

_FOND = "#12151c"
_FOND_CLAIR = "#1b2029"
_TEXTE = "#e6e9ef"
_ACCENT = "#7aa2f7"
_DISCRET = "#8b93a7"


class Application(tk.Tk):
    """La fenêtre unique. Tout tient dedans, y compris les réglages."""

    def __init__(self, url_hote: str = "") -> None:
        super().__init__()
        self.title(f"Chris Roberts {VERSION}")
        self.geometry("880x620")
        self.configure(bg=_FOND)
        self._url_hote = url_hote
        self._reponses: queue.Queue = queue.Queue()

        # **Le thème `clam` d'abord** : les onglets ttk ignorent
        # `configure(bg=…)` hors de ce thème, et gardent le gris système
        # au milieu d'une fenêtre sombre. Trouvé en parcourant l'arbre des
        # widgets, pas en regardant.
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=_FOND, borderwidth=0)
        style.configure("TNotebook.Tab", background=_FOND_CLAIR,
                        foreground=_TEXTE, padding=(14, 8), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", _FOND)])

        onglets = ttk.Notebook(self)
        onglets.pack(fill="both", expand=True, padx=10, pady=10)
        self._page_questions(onglets)
        self._page_raccordement(onglets)
        self._page_reglages(onglets)

        self.after(200, self._verifier_version)
        self.after(500, self._vider_file)

    # ------------------------------------------------------- les questions

    def _page_questions(self, parent: ttk.Notebook) -> None:
        cadre = tk.Frame(parent, bg=_FOND)
        parent.add(cadre, text="  Questions  ")

        # La barre de saisie **avant** la zone extensible : empilée après,
        # elle sort de la fenêtre et n'apparaît qu'en plein écran.
        barre = tk.Frame(cadre, bg=_FOND)
        barre.pack(side="bottom", fill="x", pady=(8, 0))
        self._saisie = tk.Entry(barre, bg=_FOND_CLAIR, fg=_TEXTE,
                                insertbackground=_TEXTE, relief="flat",
                                font=("Segoe UI", 11))
        self._saisie.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self._saisie.bind("<Return>", lambda _e: self._poser())
        tk.Button(barre, text="Demander", command=self._poser, bg=_ACCENT,
                  fg="#0d1017", relief="flat", font=("Segoe UI", 10, "bold"),
                  padx=18, pady=6).pack(side="right")

        self._fil = scrolledtext.ScrolledText(
            cadre, bg=_FOND_CLAIR, fg=_TEXTE, relief="flat", wrap="word",
            font=("Segoe UI", 10), padx=14, pady=12)
        self._fil.pack(fill="both", expand=True)
        self._fil.tag_configure("moi", foreground=_ACCENT,
                                font=("Segoe UI", 10, "bold"))
        self._fil.tag_configure("gris", foreground=_DISCRET)
        self._dire("gris", "Pose ta question — « où miner du quantainium », "
                           "« combien de balles a un Coda », « jusqu'à quelle "
                           "qualité ça vaut le coup pour un P6-LR ».\n\n")
        self._saisie.focus_set()

    def _dire(self, tag: str, texte: str) -> None:
        self._fil.configure(state="normal")
        self._fil.insert("end", texte, tag)
        self._fil.see("end")
        self._fil.configure(state="disabled")

    def _poser(self) -> None:
        question = self._saisie.get().strip()
        if not question:
            return
        self._saisie.delete(0, "end")
        self._dire("moi", f"\n{question}\n")
        threading.Thread(target=self._repondre, args=(question,),
                         daemon=True).start()

    def _repondre(self, question: str) -> None:
        """Le cœur répond dans un fil : la fenêtre ne doit jamais figer."""
        from ..client import CoreClient

        try:
            reponse = CoreClient().ask(question)
            texte = reponse.get("answer") or "Je n'ai pas compris la question."
            outil = reponse.get("tool")
        except Exception as exc:                               # noqa: BLE001
            texte = (f"Je n'arrive pas à joindre le cœur ({exc}). "
                     "Il démarre peut-être encore.")
            outil = None
        self.after(0, lambda: self._dire("", texte + "\n"))
        remontee.enfiler({
            "question": question, "reponse": texte, "outil": outil,
            "aboutie": outil is not None, "version": VERSION})

    # ---------------------------------------------------- le raccordement

    def _page_raccordement(self, parent: ttk.Notebook) -> None:
        cadre = tk.Frame(parent, bg=_FOND)
        parent.add(cadre, text="  Mon abonnement  ")
        tk.Label(cadre, text="Chris utilise ton abonnement, jamais une clé.",
                 bg=_FOND, fg=_TEXTE, font=("Segoe UI", 12, "bold"),
                 anchor="w").pack(fill="x", padx=16, pady=(18, 4))
        tk.Label(cadre, text="Installe l'un de ces outils et connecte-toi une "
                             "fois — c'est ton compte qui paie, et rien ne "
                             "transite par nous.",
                 bg=_FOND, fg=_DISCRET, font=("Segoe UI", 10), anchor="w",
                 wraplength=780, justify="left").pack(fill="x", padx=16,
                                                      pady=(0, 14))
        for etat in fournisseurs.etat():
            bloc = tk.Frame(cadre, bg=_FOND_CLAIR)
            bloc.pack(fill="x", padx=16, pady=5)
            marque = "installé" if etat["installe"] else "absent"
            couleur = "#9ece6a" if etat["installe"] else _DISCRET
            tk.Label(bloc, text=f"{etat['nom']} — {marque}", bg=_FOND_CLAIR,
                     fg=couleur, font=("Segoe UI", 11, "bold"),
                     anchor="w").pack(fill="x", padx=12, pady=(10, 2))
            if not etat["installe"]:
                # On rend la commande, on ne l'exécute pas : une
                # dépendance installée en silence est une dépendance que
                # personne ne sait retirer.
                for ligne in (etat["installation"], etat["connexion"]):
                    champ = tk.Entry(bloc, bg=_FOND, fg=_TEXTE, relief="flat",
                                     font=("Consolas", 10))
                    champ.insert(0, ligne)
                    champ.configure(state="readonly")
                    champ.pack(fill="x", padx=12, pady=2, ipady=4)
                tk.Label(bloc, text="Copie ces commandes dans un terminal.",
                         bg=_FOND_CLAIR, fg=_DISCRET,
                         font=("Segoe UI", 9), anchor="w").pack(
                             fill="x", padx=12, pady=(2, 10))
            else:
                tk.Label(bloc, text=etat["chemin"], bg=_FOND_CLAIR,
                         fg=_DISCRET, font=("Consolas", 9),
                         anchor="w").pack(fill="x", padx=12, pady=(0, 10))

    # -------------------------------------------------------- les réglages

    def _page_reglages(self, parent: ttk.Notebook) -> None:
        cadre = tk.Frame(parent, bg=_FOND)
        parent.add(cadre, text="  Réglages  ")
        tk.Label(cadre, text="Aider Chris à s'améliorer", bg=_FOND, fg=_TEXTE,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(
                     fill="x", padx=16, pady=(18, 6))
        # L'annonce vient du module, pas d'un texte réécrit ici : c'est
        # elle qui rend l'activation par défaut défendable.
        tk.Label(cadre, text=remontee.ANNONCE, bg=_FOND, fg=_TEXTE,
                 font=("Segoe UI", 10), anchor="w", justify="left",
                 wraplength=780).pack(fill="x", padx=16, pady=(0, 14))

        self._partage = tk.BooleanVar(value=remontee.active())
        tk.Checkbutton(
            cadre, text="Partager mes questions pour améliorer Chris",
            variable=self._partage, bg=_FOND, fg=_TEXTE,
            selectcolor=_FOND_CLAIR, activebackground=_FOND,
            activeforeground=_TEXTE, font=("Segoe UI", 10),
            command=lambda: remontee.regler(self._partage.get()),
            anchor="w").pack(fill="x", padx=16)

        tk.Label(cadre, text=f"Instance : {remontee.identifiant_instance()}",
                 bg=_FOND, fg=_DISCRET, font=("Consolas", 9),
                 anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(cadre, text="Ce nombre est tiré au hasard à l'installation. "
                             "Il ne dit pas qui tu es — seulement que deux "
                             "questions viennent de la même machine.",
                 bg=_FOND, fg=_DISCRET, font=("Segoe UI", 9), anchor="w",
                 wraplength=780, justify="left").pack(fill="x", padx=16)

        self._version = tk.Label(cadre, text=f"Version {VERSION}", bg=_FOND,
                                 fg=_DISCRET, font=("Segoe UI", 9), anchor="w")
        self._version.pack(fill="x", padx=16, pady=(18, 0))

    # ------------------------------------------------------------ le fond

    def _verifier_version(self) -> None:
        def chercher() -> None:
            trouve = maj.maj_disponible(VERSION)
            if trouve:
                self.after(0, lambda: self._version.configure(
                    text=f"Version {VERSION} — la {trouve['version']} est "
                         "disponible, redémarre pour l'installer",
                    fg="#e0af68"))
        threading.Thread(target=chercher, daemon=True).start()

    def _vider_file(self) -> None:
        """Envoie ce qui attend, puis se replanifie.

        L'hôte est un PC qui dort : l'échec est normal et silencieux, la
        file garde tout jusqu'au réveil.
        """
        if self._url_hote:
            threading.Thread(
                target=lambda: remontee.envoyer(self._url_hote),
                daemon=True).start()
        self.after(int(remontee.PERIODE_ENVOI * 1000), self._vider_file)


def lancer(url_hote: str = "") -> None:
    Application(url_hote).mainloop()
