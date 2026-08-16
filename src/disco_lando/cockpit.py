"""Renvoyer vers le cockpit — « ça, tu le vois mieux sur la page ».

Demande de l'utilisateur (2026-08-14) : quand une question a son écran dans
le cockpit, la réponse de Chris doit y mener. Trois exemples nommés — « quoi
de neuf », « par où débuter », « quels blueprints a un autre joueur » — et
la consigne d'aller chercher le reste.

Trois règles tiennent ce module, et deux sont des refus :

- **on ne lie que ce que la page fait mieux.** Chris rend la réponse ; le
  lien n'a de sens que si l'écran va **plus loin** — les filtres de « Quoi
  faire », les avis des membres, qui détient un blueprint, qui peut faire
  une mission. Lier « le prix d'un Gladius » vers un écran qui ne parle pas
  de prix, c'est le contrôle qui crie au loup : on ne le lit plus ;
- **un lien qui ne s'ouvre pas est pire que pas de lien.** Chaque écran cité
  ici existe dans le `routes` de `app-boot.js`, et chaque paramètre émis est
  lu par la vue qui le reçoit. `tests/test_cockpit_liens.py` le vérifie
  statiquement, des deux côtés ;
- **l'adresse est celle du lecteur, pas de la machine qui répond.** Un
  `127.0.0.1` sur Discord ne mène nulle part. `base()` rend l'URL publique
  quand le tunnel est ouvert (voir `config.COCKPIT_URL_FICHIER`).
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from . import config

#: **Le format de la ligne, écrit une seule fois.** Trois endroits ont besoin
#: de la reconnaître pour la retirer : le comparateur de variantes (elle
#: porte le nom encodé et ferait croire à deux réponses différentes), et les
#: deux chemins de synthèse vocale (Piper lirait l'URL lettre par lettre).
#: Chacun avec son propre motif, ils divergeraient au premier changement de
#: tournure.
PREFIXE = "*Sur le cockpit"
_LIGNE = re.compile(rf"(?m)^{re.escape(PREFIXE)}[^\n]*$")


def remettre_en_queue(texte: str) -> str:
    """Ramène la ligne de renvoi tout en bas, si elle a été enjambée.

    `render()` la pose en fin de réponse, mais `dialogue` colle **après**
    la note des variantes et la réserve de confiance : le lien se retrouvait
    alors au milieu, où un pied de page ressemble à une erreur de montage.
    On ne le pose pas dans `dialogue` pour autant — `render()` est le point
    de passage de tous les frontends, y compris ceux qui n'ont pas de tour
    de conversation.
    """
    trouve = _LIGNE.search(texte)
    if trouve is None or texte.rstrip().endswith(trouve.group(0)):
        return texte
    return f"{sans_le_lien(texte)}\n\n{trouve.group(0)}"


def sans_le_lien(texte: str) -> str:
    """Le texte sans sa ligne de renvoi — pour la voix et les comparaisons.

    On referme aussi les lignes vides que le retrait laisse : le comparateur
    de variantes repose sur une relation de **préfixe**, et une ligne vide en
    fin de texte suffit à la casser.
    """
    return re.sub(r"\n{3,}", "\n\n", _LIGNE.sub("", texte)).strip()

#: Les écrans du cockpit, tels que `routes` les nomme dans `app-boot.js`.
#: Fermé comme les autres vocabulaires du projet : un écran mal orthographié
#: produirait un lien qui ouvre le tableau de bord sans rien dire.
ECRANS = {
    "tableau", "membres", "activites", "missions", "reputation", "blueprints",
    "inventaire", "journal", "assistant", "guide", "ticket", "commencer",
}

#: Ce que chaque écran **ajoute** à la réponse de Chris. C'est ce texte qui
#: s'affiche, et il doit dire le supplément — « voir sur le cockpit » tout
#: court ne donne aucune raison de cliquer.
_PROMESSES = {
    "activites": "les filtres, le déroulé complet et les avis de la guilde",
    "blueprints": "qui le détient dans la guilde",
    "missions": "qui peut la faire, réputation par réputation",
    "reputation": "le plan de grind, membre par membre",
    "inventaire": "qui possède quoi",
    # `guide` ne sort pas de `_cible` : il n'est pas la suite d'une réponse,
    # il est ce qu'on propose quand **il n'y a pas** de réponse. C'est
    # `dialogue` qui le pose, sur l'aveu `no_intent`.
    "guide": "tout ce que je sais répondre",
}


def base() -> str | None:
    """L'adresse du cockpit, ou None si on n'en connaît aucune.

    L'ordre est celui du plus explicite au plus supposé : la surcharge, le
    fichier qu'écrit le lanceur en publiant le serveur, puis `CORE_URL`.

    **Le fichier n'est pas un cache.** Le tunnel s'ouvre et se ferme pendant
    que le cœur tourne ; on le relit donc à chaque appel plutôt que de le
    mémoriser, sinon Chris continuerait de donner une adresse morte après
    « Fermer aux membres ».
    """
    if config.COCKPIT_URL:
        return config.COCKPIT_URL.rstrip("/")
    try:
        if config.COCKPIT_URL_FICHIER.exists():
            lu = config.COCKPIT_URL_FICHIER.read_text(encoding="utf-8").strip()
            if lu.startswith("http"):
                return lu.rstrip("/")
    except OSError:
        # Un fichier illisible ne doit pas faire tomber une réponse de jeu.
        pass
    return (config.CORE_URL or "").rstrip("/") or None


def publier(url: str | None) -> None:
    """Le lanceur annonce l'adresse publique — ou son retrait.

    Appelé à l'ouverture et à la fermeture du tunnel. Écrire une chaîne vide
    **efface** le fichier : une adresse de tunnel fermé est un lien mort, et
    un lien mort donné à un membre coûte plus qu'une absence de lien.
    """
    chemin = config.COCKPIT_URL_FICHIER
    try:
        if not url:
            chemin.unlink(missing_ok=True)
            return
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(url.strip().rstrip("/"), encoding="utf-8")
    except OSError:
        pass


def url(ecran: str, **params: Any) -> str | None:
    """L'adresse d'un écran, paramètres compris.

    Le cockpit route sur le fragment (`#activites`), et le fragment porte
    aussi les paramètres (`#activites?tri=debutant`) : ils restent donc côté
    client et ne partent jamais au serveur. C'est voulu — un identifiant de
    membre dans une chaîne de requête finirait dans les journaux d'accès.
    """
    if ecran not in ECRANS:
        raise ValueError(f"écran inconnu du cockpit : {ecran}")
    racine = base()
    if not racine:
        return None
    propres = {c: v for c, v in params.items() if v not in (None, "", [])}
    queue = ""
    if propres:
        queue = "?" + urllib.parse.urlencode(propres, doseq=True)
    return f"{racine}/guilde#{ecran}{queue}"


def _fiche_visee(data: dict[str, Any]) -> str | None:
    """La clé d'activité d'une réponse, quand elle n'en vise qu'une."""
    if data.get("cle"):
        return data["cle"]
    activites = data.get("activites") or []
    return activites[0]["cle"] if len(activites) == 1 else None


def lien(outil: str, data: dict[str, Any]) -> tuple[str, str] | None:
    """(adresse, promesse) pour un outil et sa réponse — ou None.

    **`None` est la réponse la plus fréquente, et c'est voulu.** Sur 77
    outils, une petite vingtaine a un écran qui va plus loin ; les autres
    répondent complètement et un lien n'y ajouterait qu'une ligne à lire.
    """
    ecran, params = _cible(outil, data)
    if ecran is None:
        return None
    adresse = url(ecran, **params)
    if adresse is None:
        return None
    return adresse, _PROMESSES[ecran]


def _cible(outil: str, data: dict[str, Any]) -> tuple[str | None, dict]:
    """L'écran et ses paramètres — la table, isolée pour être testable."""
    # --- « Quoi faire » : les filtres, le déroulé replié, les avis.
    if outil == "par_ou_commencer":
        return "activites", {"tri": "debutant"}
    if outil == "activites_a_faire":
        return "activites", {"tri": data.get("tri") or "type"}
    if outil == "fiche_activite":
        return "activites", {"fiche": data.get("cle")}
    if outil == "quoi_de_neuf":
        return "activites", {}

    # --- Blueprints : le cockpit dit **qui le détient**, Chris ne le sait pas.
    if outil in ("get_blueprint", "plan_de_fabrication",
                 "blueprints_de_la_meme_serie"):
        return "blueprints", {"q": _nom_du_blueprint(data)}
    if outil == "blueprints_par_systeme":
        return "blueprints", {}

    # --- Missions : le cockpit croise la réputation de chaque membre.
    if outil in ("get_mission_reputation", "get_mission_group",
                 "missions_par_activite", "missions_du_site",
                 "missions_payantes", "panorama_missions", "missions_guilde"):
        return "missions", {"q": _titre_de_mission(data)}
    if outil == "progression_dans":
        return "reputation", {}

    # --- Ce que la guilde possède. L'outil dit lui-même de quelle moitié il
    # parle (`type`), et les deux écrans ne montrent pas la même chose : le
    # blueprint a ses détenteurs et ses missions, l'inventaire ses preuves.
    if outil == "possessions_guilde":
        # On ne passe **pas** le membre visé : `vises` porte des noms
        # d'affichage, le sélecteur de l'écran des identifiants. Résoudre
        # l'un vers l'autre ici ferait un lien qui casse dès qu'un membre se
        # renomme — et le filtre est à un clic sur la page.
        ecran = "blueprints" if data.get("type") == "blueprint" else "inventaire"
        return ecran, {"q": data.get("nom")}

    return None, {}


def _nom_du_blueprint(data: dict[str, Any]) -> str | None:
    """Le nom sous lequel chercher dans l'écran Blueprints.

    On prend le nom **affiché**, jamais la clé technique : c'est ce que la
    recherche de l'écran indexe, et c'est aussi ce que le joueur a lu.
    """
    if isinstance(data.get("nom"), str) and data["nom"]:
        return data["nom"]
    # `get_blueprint` et `blueprints_de_la_meme_serie` rangent l'objet dans
    # `blueprint`, et son nom lisible s'y appelle `output_name` — la recette
    # nomme sa **sortie**, pas elle-même. Mesuré : sans cette ligne, le lien
    # ouvrait l'écran sans filtre, ce qui a l'air de marcher et ne mène nulle
    # part quand le catalogue compte 1 587 schémas.
    blueprint = data.get("blueprint")
    if isinstance(blueprint, dict):
        return blueprint.get("output_name") or blueprint.get("nom")
    return None


def _titre_de_mission(data: dict[str, Any]) -> str | None:
    """Le titre à chercher — et rien plutôt qu'un gabarit.

    1 138 contrats sur 5 105 portent un titre que le serveur remplace à la
    génération. Le passer en recherche ne rendrait aucun résultat : mieux
    vaut ouvrir l'écran sans filtre que sur une liste vide.
    """
    contrat = data.get("contract")
    if isinstance(contrat, dict):
        titre = contrat.get("title")
        if isinstance(titre, str) and titre and "[" not in titre:
            return titre
    return None
