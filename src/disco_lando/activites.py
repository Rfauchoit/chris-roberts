"""« On est trois ce soir, on fait quoi ? » — le catalogue d'activités.

Sprint 38. Les autres modules répondent à des questions sur le **contenu** de
la base — quelle mission paie, quel vaisseau est le plus rapide. Celui-ci
répond à une question sur la **soirée**, et c'est une nature différente : la
réponse est éditoriale (`data/activites/`, ingéré par `ingest/activites.py`),
mais tout ce qui est chiffrable est repris de la base à l'affichage.

Deux règles du projet s'appliquent mot pour mot, et elles ont dicté le code :

- **une contrainte perdue en silence est pire qu'une question incomprise.**
  Quand aucune activité ne passe, on ne rend pas une liste vide : on relâche
  chaque critère à tour de rôle et on nomme celui dont l'abandon débloque le
  plus. « À cinq, sans combat et sans mission, il ne reste rien — accepte le
  combat en vaisseau et tu as trois activités » est utilisable ; une liste
  vide ne l'est pas ;
- **« la meilleure » n'existe pas sans critère.** Le tri annonce son axe, et
  une fiche dit d'où vient sa difficulté : les quatre axes publiés par CIG
  quand des contrats sont rattachés, une note déclarée sinon. **Les deux ne
  se mélangent jamais dans la même colonne** — l'une est mesurée, l'autre
  est une opinion documentée.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ._socle import NotFound, _dict
from .render.socle import _GABARIT

LIBELLES_COMBAT = {
    "aucun": "sans combat",
    "fps": "combat à pied",
    "vaisseau": "combat en vaisseau",
    "les_deux": "à pied et en vaisseau",
}
LIBELLES_PVP = {
    "non": "PvE seulement",
    "possible": "PvP possible",
    "au_coeur": "PvP au cœur du jeu",
}
LIBELLES_MISSION = {
    "oui": "sur contrat",
    "non": "sans contrat",
    "partiellement": "contrat facultatif",
}
LIBELLES_NATURE = {
    "chaine": "chaîne de missions",
    "activite_libre": "activité libre",
    "boucle": "boucle de jeu",
    "evenement": "événement",
}
LIBELLES_STATUT = {
    "vivant": "disponible",
    "a_venir": "à venir",
    "temporaire": "temporaire",
    "retire": "retirée du jeu",
}

#: Les quatre axes publiés par CIG, dans l'ordre où ils intéressent le joueur.
#: Repris de `descriptions.py` — un seul vocabulaire pour tout le projet.
_AXES = ("diff_risque", "diff_charge", "diff_pilotage", "diff_connaissance")

TRIS = ("type", "debutant", "difficulte", "duree", "nouveaute", "joueurs", "nom")

#: Une seule valeur de référence pour le cœur, l'API et le site. Trois
#: littéraux séparés avaient fini par diverger : le cœur et le navigateur
#: rangeaient par type, la route HTTP par difficulté.
TRI_PAR_DEFAUT = "type"

#: L'ordre des natures, **et c'est le tri par défaut**. Demande de
#: l'utilisateur (2026-08-14) : « de base ça doit être rangé dans cet ordre ».
#:
#: Les événements passent **en tête** parce qu'ils sont datés : ce qui est
#: temporaire se rate si on ne le voit pas. Le rendu doit le dire, sans quoi
#: la première ligne d'une liste ressemble à une recommandation alors que
#: c'est un compte à rebours.
ORDRE_DES_NATURES = ("evenement", "chaine", "activite_libre", "boucle")

#: L'ordre des natures **dans le parcours du débutant**, qui n'est pas celui
#: du catalogue. Demande de l'utilisateur (2026-08-14) : « les missions sont
#: à faire avant les activités, elles sont forcément plus simples ».
#:
#: C'est une raison de fond et pas une préférence : une chaîne de missions
#: **guide** — le jeu donne le contrat, le marqueur, l'ordre des étapes, et
#: la suivante ne s'ouvre qu'une fois la précédente finie. Une activité libre
#: ne donne rien de tout ça : il faut savoir où aller, quoi emporter et dans
#: quel ordre agir avant même de partir. Un débutant qui ne sait pas encore
#: ce qu'il ignore a besoin d'être mené.
#:
#: **Et c'est mécanique, pas une conséquence des numéros.** Les valeurs
#: d'`ordre_debutant` suffiraient aujourd'hui à produire cet ordre ; une
#: fiche neuve qui retombe sur le calcul le casserait sans un mot. La nature
#: passe donc **avant** le rang dans la clé de tri.
ORDRE_DEBUTANT_DES_NATURES = ("chaine", "activite_libre", "evenement")

#: Ce qu'un rang exigé coûte à un débutant. Lu dans
#: `contracts.min_standing_name`, donc **mesuré**. L'échelle est décuplée
#: pour partager l'axe de `ordre_debutant` : une fiche qui ne déclare rien se
#: range parmi celles qui déclarent, au lieu de toutes les précéder.
_COUT_DU_RANG = {
    "prospective associate": 10,
    "associate": 20,
    "jr. contractor": 20, "junior contractor": 20,
    "trusted associate": 30,
    "contractor": 30,
    "sr. contractor": 40, "senior contractor": 40,
    "prestige 1": 50, "prestige 2": 60, "prestige 3": 70,
}


def _rang(valeur: str | None) -> int | None:
    """Le rang d'un libellé de difficulté CIG — le jeu l'écrit en suffixe."""
    if not valeur:
        return None
    queue = valeur.rsplit("_", 1)[-1]
    return int(queue) if queue.isdigit() else None


def _mediane(valeurs: list[int]) -> int:
    ordonnees = sorted(valeurs)
    milieu = len(ordonnees) // 2
    if len(ordonnees) % 2:
        return ordonnees[milieu]
    return round((ordonnees[milieu - 1] + ordonnees[milieu]) / 2)


def difficulte_cig(con: sqlite3.Connection, cle: str) -> dict[str, Any] | None:
    """Les axes CIG d'une activité — et l'agrégat dépend de sa nature.

    **Une chaîne se juge sur son étape la plus dure, une boucle sur son
    ordinaire.** C'est la distinction qui a demandé le plus de soin :

    - une **chaîne** (Onyx, TSG) s'impose en entier. Le maximum décrit ce
      qu'il faut être capable de faire pour la terminer ; une moyenne ferait
      passer Project Hyperion pour une promenade parce que les trois premiers
      dossiers sont faciles ;
    - une **boucle** (Mercenary, Bounty Hunter) est un catalogue dans lequel
      on **choisit**. Le maximum y décrirait la mission la plus dure des 1 253,
      c'est-à-dire un cas qu'on peut simplement ne pas prendre — et
      classerait « bunkers » au-dessus de TSG. On rend donc la **médiane**,
      qui décrit ce qu'on croise vraiment, plus l'**étendue**, qui dit
      qu'on peut viser plus facile ou plus dur.

    Le même piège que la résistance de casque prise sur la moyenne plutôt
    que sur la pièce la plus répandue : l'agrégat doit décrire la question
    posée, pas la population.
    """
    colonnes = ", ".join(f"c.{axe}" for axe in _AXES)
    # **Seuls les contrats qui définissent la difficulté.** Une fiche peut
    # en écarter une partie — les offres répétables d'un commanditaire, qui
    # se choisissent — via `ancrages.difficulte`. Sans déclaration, le
    # drapeau vaut 1 partout et la requête ne filtre rien.
    lignes = con.execute(
        f"SELECT {colonnes} FROM activite_contrats a "
        "JOIN contracts c ON c.uuid = a.uuid "
        "WHERE a.cle = ? AND a.pour_difficulte = 1", (cle,)).fetchall()
    agregat = "chaine"
    if not lignes:
        # Une boucle n'a pas de contrats nommés, mais elle a un **type**, et
        # les axes y sont tout aussi publiés. Sans ce second chemin, les
        # seize boucles classiques n'auraient aucune difficulté mesurée et
        # retomberaient toutes sur l'estimation de fiche.
        lignes = con.execute(
            f"SELECT {colonnes} FROM contracts c "
            "JOIN activite_types t ON t.mission_type = c.mission_type "
            "WHERE t.cle = ? AND c.not_for_release=0 AND c.work_in_progress=0",
            (cle,)).fetchall()
        agregat = "boucle"
    if not lignes:
        return None

    par_axe: dict[str, list[int]] = {}
    for ligne in lignes:
        for axe, brut in zip(_AXES, ligne):
            rang = _rang(brut)
            if rang is not None:
                par_axe.setdefault(axe, []).append(rang)
    if not par_axe:
        return None

    if agregat == "chaine":
        axes = {axe: max(rangs) for axe, rangs in par_axe.items()}
        etendue = None
        libelle = "le palier le plus dur de la chaîne"
    else:
        axes = {axe: _mediane(rangs) for axe, rangs in par_axe.items()}
        etendue = {axe: [min(rangs), max(rangs)] for axe, rangs in par_axe.items()}
        libelle = "la médiane du catalogue, on choisit son contrat"

    # `diff_charge` à 6 ou plus, c'est le jeu qui dit « pas faisable seul ».
    # Sur une boucle, c'est la médiane qui parle : un contrat exceptionnel
    # ne rend pas toute la boucle impraticable en solo.
    charge = axes.get("diff_charge")
    return {"axes": axes, "contrats_lus": len(lignes),
            "agregat": agregat, "agregat_libelle": libelle,
            "etendue": etendue,
            "solo_possible": None if charge is None else charge < 6}


def _note_de_difficulte(fiche: dict[str, Any],
                        cig: dict[str, Any] | None) -> tuple[int | None, str]:
    """Un nombre pour trier, **et** d'où il vient.

    Sans la seconde moitié, une note déclarée et une note mesurée se
    confondent dans la même colonne, ce qui est exactement ce que le projet
    interdit ailleurs (« deux natures de chiffre ne se mélangent pas »).

    **La note est la moyenne des quatre axes, pas le pire.** Corrigé le
    2026-08-14 après une remarque de l'utilisateur — « je ne suis pas sûr
    que les contrats de Recco soient si durs que ça » — et il avait raison
    pour une raison qui dépassait sa fiche : le maximum était **empilé deux
    fois**. `difficulte_cig` prend déjà le pire contrat d'une chaîne, à
    juste titre ; reprendre le pire axe par-dessus faisait décider la note
    d'une fiche par **une cellule sur 152** chez Recco — `diff_risque = 6`
    sur `BattagliaStory3` —, alors que ses 38 contrats se répartissent en 10
    accessibles, 21 exigeants et 7 difficiles.

    Deux mesures ont tranché :

    - **le pire axe efface les trois autres.** Recco est risquée mais
      légère, facile à piloter et peu exigeante en connaissance : moyenne
      4,75, la plus basse des chaînes (Onyx et Vanduul 5,75, Storm Breaker
      6,00, Tactical Strike Group 6,50). Le maximum les donnait toutes à 6
      ou 7 ;
    - **et il sature.** Neuf fiches sur onze hors boucles sortaient à 4/5 :
      prendre le pire de quatre dimensions plafonne presque tout. C'était la
      vraie cause du « la difficulté ne discrimine pas » mesuré le même jour
      en cherchant à classer le parcours du débutant. À la moyenne, six
      fiches descendent d'un cran et les quatre notes sont représentées.

    Ce qui **ne** change pas : l'agrégat sur les contrats. Une chaîne se
    juge toujours sur son pire palier, une boucle sur son ordinaire — cette
    règle-là décrit bien la question posée.
    """
    if cig and cig["axes"]:
        # Les axes CIG vont de 1 à 7, la note déclarée de 1 à 5 : on ramène
        # sur la même échelle pour que le tri soit cohérent entre les deux
        # populations, et on garde les rangs bruts pour l'affichage.
        valeurs = list(cig["axes"].values())
        moyenne = sum(valeurs) / len(valeurs)
        return (round(1 + (moyenne - 1) * 4 / 6),
                "mesurée sur les contrats du jeu")
    if fiche.get("difficulte"):
        return fiche["difficulte"], "estimation de la fiche"
    return None, "inconnue"


def _base(con: sqlite3.Connection) -> list[dict[str, Any]]:
    lignes = [_dict(r) for r in con.execute(
        "SELECT * FROM activites ORDER BY ordre, nom")]
    for fiche in lignes:
        cig = difficulte_cig(con, fiche["cle"])
        note, origine = _note_de_difficulte(fiche, cig)
        fiche["difficulte_cig"] = cig
        fiche["difficulte_note"] = note
        fiche["difficulte_origine"] = origine
        fiche["combat_libelle"] = LIBELLES_COMBAT.get(fiche["combat"])
        fiche["pvp_libelle"] = LIBELLES_PVP.get(fiche["pvp"])
        fiche["mission_libelle"] = LIBELLES_MISSION.get(fiche["mission"])
        fiche["nature_libelle"] = LIBELLES_NATURE.get(fiche["nature"])
        fiche["statut_libelle"] = LIBELLES_STATUT.get(fiche["statut"])
        # **Le compte porte sur le titre affiché, pas sur les lignes.**
        # Mesuré à l'écran le 2026-08-14 : la carte annonçait « 15 contrats »
        # là où la fiche en listait 12. Le jeu porte plusieurs contrats de
        # titre identique — les variantes `_Repeat` d'Onyx — et un joueur
        # compte ce qu'il lit. Les deux vues doivent compter pareil, sinon
        # l'incohérence saute aux yeux dès qu'on ouvre le détail.
        # Le même filtre de gabarit que dans `fiche_activite`, sinon la carte
        # compterait un titre que la fiche n'affiche pas.
        titres = {t for (t,) in con.execute(
            "SELECT DISTINCT COALESCE(c.title, c.debug_name) "
            "FROM activite_contrats a JOIN contracts c ON c.uuid = a.uuid "
            "WHERE a.cle=?", (fiche["cle"],)) if t and not _GABARIT.search(t)}
        fiche["contrats"] = len(titres)
        # **Une boucle se compte, elle ne s'énumère pas.** Le type est
        # stocké, le compte se fait ici — sinon « Mercenary » ferait une
        # fiche de 1 253 lignes. Les deux populations ne se mélangent pas
        # dans `contrats` : l'une est une liste qu'on peut lire, l'autre un
        # volume de catalogue.
        fiche["contrats_du_type"] = con.execute(
            "SELECT COUNT(*) FROM contracts c "
            "JOIN activite_types t ON t.mission_type = c.mission_type "
            "WHERE t.cle=? AND c.not_for_release=0 AND c.work_in_progress=0",
            (fiche["cle"],)).fetchone()[0]
    return lignes


#: Chaque critère est une fonction, pour qu'on puisse le **retirer** un par un
#: quand plus rien ne passe. Les écrire en dur dans un `WHERE` rendrait le
#: relâchement impossible sans dupliquer la requête.
def _critere_joueurs(fiche: dict[str, Any], n: int) -> bool:
    bas = fiche.get("joueurs_min") or 1
    haut = fiche.get("joueurs_max")
    return bas <= n and (haut is None or n <= haut)


def _critere_combat(fiche: dict[str, Any], veut: str) -> bool:
    if veut == "aucun":
        return fiche["combat"] == "aucun"
    if veut in ("fps", "vaisseau"):
        return fiche["combat"] in (veut, "les_deux")
    return True


def _critere_mission(fiche: dict[str, Any], veut: str) -> bool:
    if veut == "oui":
        return fiche["mission"] in ("oui", "partiellement")
    if veut == "non":
        return fiche["mission"] in ("non", "partiellement")
    return True


def _rang_d_entree(con: sqlite3.Connection, fiche: dict[str, Any]) -> int:
    """Ce qu'il faut avoir accompli **avant** de pouvoir commencer.

    **La fiche décide, le calcul complète.** C'est le sens de la réponse de
    l'utilisateur le 2026-08-14 — « calculé, corrigé à la main » —, et la
    mesure du même jour a dit lequel des deux porte réellement la liste :

    - la **difficulté** ne classe pas une progression. Les axes de CIG
      décrivent la mission, pas le chemin pour y accéder. Elle saturait en
      plus — neuf des onze fiches hors boucles à la même note tant que
      `_note_de_difficulte` prenait le pire axe ; elle discrimine depuis
      qu'elle prend leur moyenne (5 fiches à 3, 6 à 4, 1 à 5), et
      l'objection de fond reste entière ;
    - le **rang exigé** ne la classe pas davantage. Six fiches sur onze n'ont
      aucun contrat rattaché — une activité libre n'en a pas —, et sur les
      cinq restantes, trois ne portent que des contrats sans rang. Le seul
      endroit où le rang est écrit, c'est le `prerequis` **rédigé** de la
      fiche : « Rang Senior Contractor chez InterSec Defense Solutions ».

    Le calcul reste, parce qu'une fiche neuve doit atterrir quelque part
    plutôt que d'ouvrir la liste par accident, mais il ne prétend plus porter
    le classement.

    **Et l'agrégat est un minimum, pas un maximum.** C'est la règle du projet
    — « une chaîne se juge sur son pire palier, une boucle sur son
    ordinaire » — appliquée à la bonne question : on ne demande pas ce que la
    chaîne exige pour être *terminée*, mais pour être *commencée*. Pris au
    maximum, le type `Mercenary` de la fiche Ghilly agrégeait 36 rangs
    distincts sur 1 253 contrats et sortait « Contractor » : le tutoriel de
    combat se rangeait derrière Tactical Strike Group.
    """
    manuel = fiche.get("ordre_debutant")
    if manuel is not None:
        return int(manuel)

    rangs = [r[0] for r in con.execute(
        "SELECT DISTINCT c.min_standing_name FROM activite_contrats a "
        "JOIN contracts c ON c.uuid = a.uuid "
        "WHERE a.cle = ? AND c.min_standing_name IS NOT NULL", (fiche["cle"],))]
    if not rangs:
        rangs = [r[0] for r in con.execute(
            "SELECT DISTINCT c.min_standing_name FROM contracts c "
            "JOIN activite_types t ON t.mission_type = c.mission_type "
            "WHERE t.cle = ? AND c.min_standing_name IS NOT NULL",
            (fiche["cle"],))]
    return min((_COUT_DU_RANG.get((r or "").strip().lower(), 0) for r in rangs),
               default=0)


def _rang_requis(con, fiche) -> dict | None:
    """Le rang de réputation qu'il faut **pour commencer**, ou None.

    **À ne pas confondre avec `rang_entree`**, qui est une clé de tri : dès
    qu'une fiche pose `ordre_debutant`, celle-là vaut 10, 20, 30… et ne
    dit plus rien d'une réputation. Le lecteur du parcours, lui, demande
    « qu'est-ce qu'il me faut pour m'y mettre » — constat de l'utilisateur
    le 2026-08-20, « il n'y a toujours pas les prérequis de réputation ».

    **C'est un minimum, jamais un maximum** : on ne demande pas ce que la
    chaîne exige pour être *terminée* mais pour être *commencée*. Pris au
    maximum, le type `Mercenary` de Ghilly agrégeait 36 rangs distincts sur
    1 253 contrats et sortait « Contractor » — le tutoriel de combat se
    rangeait derrière Tactical Strike Group.

    Rend `None` sans contrat rattaché plutôt que zéro : six fiches sur onze
    sont dans ce cas, et « aucun rang requis » est une affirmation, pas une
    absence de mesure. Ce qui reste vrai est alors écrit dans le
    `prerequis` rédigé de la fiche.
    """
    ligne = con.execute(
        "SELECT c.min_standing_name AS nom, c.min_standing_value AS valeur, "
        "       c.mission_giver AS org "
        "FROM activite_contrats a JOIN contracts c ON c.uuid = a.uuid "
        "WHERE a.cle = ? AND c.min_standing_name IS NOT NULL "
        "  AND c.not_for_release = 0 "
        "ORDER BY COALESCE(c.min_standing_value, 0), c.min_standing_name "
        "LIMIT 1", (fiche["cle"],)).fetchone()
    if ligne is None:
        return None
    return {"rang": ligne["nom"], "valeur": ligne["valeur"],
            "organisation": ligne["org"]}


def _cle_de_tri(tri: str):
    if tri == "type":
        # Événement, chaîne, activité libre, boucle — puis la difficulté
        # à l'intérieur de chaque groupe, pour que l'ordre reste utile.
        return lambda f: (
            ORDRE_DES_NATURES.index(f["nature"])
            if f["nature"] in ORDRE_DES_NATURES else len(ORDRE_DES_NATURES),
            f.get("difficulte_note") or 99, f["nom"])
    if tri == "debutant":
        # **La difficulté d'abord** — révision de l'utilisateur le
        # 2026-08-20 : « range les débuter par ordre de difficulté plutôt
        # qu'activité libre ou mission, SOO est beaucoup plus simple que
        # Tactical Strike Group ». Il avait raison et c'était mesurable :
        # la nature passant en premier, Tactical Strike Group (note **5**,
        # la plus haute du catalogue) sortait en 7ᵉ position parce que
        # c'est une chaîne, quand Siege of Orison (note **3**) tombait en
        # 9ᵉ parce que c'est une activité libre.
        #
        # La règle du 2026-08-14 — « une chaîne guide, une activité libre
        # ne guide rien » — n'est pas jetée : elle **descend d'un cran** et
        # départage à difficulté égale, ce qui est sa vraie portée. Entre
        # deux activités de note 3, la guidée reste le meilleur premier pas.
        return lambda f: (
            f.get("difficulte_note") or 99,
            ORDRE_DEBUTANT_DES_NATURES.index(f["nature"])
            if f["nature"] in ORDRE_DEBUTANT_DES_NATURES
            else len(ORDRE_DEBUTANT_DES_NATURES),
            f.get("rang_entree", 0), f["nom"])
    if tri == "duree":
        return lambda f: (f.get("duree_min_minutes") or 10**6, f["nom"])
    if tri == "nouveaute":
        # Tri décroissant sur un tuple : on ne peut pas le nier comme un
        # nombre, donc on inverse l'ordre au lieu du signe.
        return lambda f: (
            tuple(-n for n in version_patch(f.get("patch_introduction"))),
            f["nom"])
    if tri == "joueurs":
        return lambda f: (f.get("joueurs_conseilles") or 99, f["nom"])
    if tri == "nom":
        return lambda f: f["nom"]
    return lambda f: (f.get("difficulte_note") or 99, f["nom"])


def version_patch(patch: str | None) -> tuple[int, ...]:
    """« 4.10 » est **postérieur** à « 4.9 », pas antérieur.

    Comparer les versions comme des flottants est le piège classique, et il
    s'est déclenché ici le 2026-08-14 : `float("4.10")` vaut 4,1, donc plus
    petit que `float("4.9")` — le contrôle de péremption annonçait qu'une
    note écrite pour la 4.10 décrivait un patch déjà installé en 4.9.

    Un numéro de version est une **suite d'entiers**, pas un nombre décimal.
    Un segment non numérique arrête la lecture plutôt que de lever : les
    patchs portent parfois un suffixe (`4.9.0-LIVE`).
    """
    segments: list[int] = []
    for morceau in (patch or "").replace("-", ".").split("."):
        if not morceau.isdigit():
            break
        segments.append(int(morceau))
    return tuple(segments)


LIBELLES_TRI = {
    "type": "par type — événements, chaînes, activités libres, boucles",
    "debutant": "dans l'ordre où un débutant peut les aborder",
    "difficulte": "de la plus accessible à la plus exigeante",
    "duree": "de la plus courte à la plus longue",
    "nouveaute": "de la plus récente à la plus ancienne",
    "joueurs": "du plus petit groupe au plus grand",
    "nom": "par ordre alphabétique",
}


def activites_a_faire(con: sqlite3.Connection, query: str = "", *,
                      joueurs: int | None = None,
                      combat: str | None = None,
                      mission: str | None = None,
                      pvp: str | None = None,
                      duree_max_minutes: int | None = None,
                      systeme: str | None = None,
                      statuts: tuple[str, ...] = ("vivant",),
                      tri: str = TRI_PAR_DEFAUT,
                      limit: int = 50) -> dict[str, Any]:
    """Les activités qui passent les filtres — et ce qui bloque quand rien ne passe.

    `query` est la question telle qu'elle a été posée ; elle ne filtre rien —
    c'est le préparateur du routeur qui a déjà lu le nombre de joueurs et les
    envies. On la garde pour que le rendu puisse s'y référer.
    """
    fiches = _base(con)

    # Chaque entrée : (nom lisible, prédicat). L'ordre est celui dans lequel
    # on proposera de relâcher, du moins coûteux au plus coûteux à abandonner.
    filtres: list[tuple[str, Any]] = []
    if statuts:
        filtres.append((f"statut {', '.join(statuts)}",
                        lambda f: f["statut"] in statuts))
    if systeme:
        cible = systeme.strip().lower()
        filtres.append((f"dans {systeme}",
                        lambda f: (f.get("systeme") or "").lower() == cible))
    if duree_max_minutes:
        # Un budget est une promesse de fin, pas une heure de départ. La
        # borne minimale disait qu'une activité de 30 à 120 minutes tenait
        # dans un créneau de 60 ; sans borne haute connue, on ne promet rien.
        filtres.append((f"au plus {duree_max_minutes} minutes",
                        lambda f: f.get("duree_max_minutes") is not None
                        and f["duree_max_minutes"] <= duree_max_minutes))
    if pvp == "non":
        filtres.append(("sans PvP", lambda f: f["pvp"] == "non"))
    if mission in ("oui", "non"):
        filtres.append((LIBELLES_MISSION[mission],
                        lambda f, v=mission: _critere_mission(f, v)))
    if combat in ("aucun", "fps", "vaisseau"):
        filtres.append((LIBELLES_COMBAT[combat],
                        lambda f, v=combat: _critere_combat(f, v)))
    if joueurs:
        filtres.append((f"à {joueurs}",
                        lambda f, n=joueurs: _critere_joueurs(f, n)))

    def _passe(fiche, sauf=None):
        return all(pred(fiche) for nom, pred in filtres if nom != sauf)

    retenues = [f for f in fiches if _passe(f)]

    # **Ce qui bloque, quand rien ne passe.** On relâche un critère à la fois
    # et on rend celui dont l'abandon débloque le plus — une liste vide est
    # inutilisable, le joueur veut savoir laquelle de ses exigences coûte les
    # autres.
    debloquerait = []
    if not retenues and filtres:
        for nom, _ in filtres:
            combien = sum(1 for f in fiches if _passe(f, sauf=nom))
            if combien:
                debloquerait.append({"critere": nom, "activites": combien})
        debloquerait.sort(key=lambda d: -d["activites"])

    tri = tri if tri in TRIS else TRI_PAR_DEFAUT
    if tri == "debutant":
        # **Une boucle n'est pas une étape de progression.** On ne « finit »
        # pas le mercenariat : les boucles se pratiquent en parallèle, à
        # n'importe quel moment. Les mêler à un parcours débutant
        # laisserait croire qu'il faut les traverser dans l'ordre.
        retenues = [f for f in retenues if f["nature"] != "boucle"]
        for fiche in retenues:
            fiche["rang_entree"] = _rang_d_entree(con, fiche)
            fiche["rang_requis"] = _rang_requis(con, fiche)
    retenues.sort(key=_cle_de_tri(tri))
    return {
        "activites": retenues[:limit],
        "total": len(retenues),
        "tri": tri,
        "tri_libelle": LIBELLES_TRI[tri],
        "criteres": [nom for nom, _ in filtres],
        "debloquerait": debloquerait,
        "joueurs": joueurs,
    }


def par_ou_commencer(con: sqlite3.Connection, query: str = "",
                     **filtres: Any) -> dict[str, Any]:
    """« Par quoi je commence ? » — le parcours d'un joueur qui débute.

    C'est `activites_a_faire` avec le tri de progression, et **un outil à
    part plutôt qu'un tri de plus dans le premier**. Deux raisons, et la
    seconde est celle qui a décidé :

    - le vocabulaire ne se recoupe pas. « On fait quoi ce soir » et « par où
      je commence » sont deux questions, et les mêler ferait un garde-fou
      assez large pour avaler les deux à la fois ;
    - le tri **écarte les boucles**, donc la liste rendue n'est pas la même
      population. Un outil qui change ce qu'il liste selon un argument
      annonce un compte qui ne correspond pas à ce qu'on lui a demandé.

    Le drapeau `debutant` est ce que le rendu lit pour ouvrir sur une phrase
    d'orientation : sans lui, une liste de dix activités « difficile » se
    lit comme un refus alors que c'est un parcours.
    """
    filtres.pop("tri", None)
    data = activites_a_faire(con, query, tri="debutant", **filtres)
    data["debutant"] = True
    return data


def _releve_uex(con: sqlite3.Connection) -> str | None:
    """Quand les prix ont été rapatriés pour la dernière fois.

    **Un bloc calculé qui ne se date pas ment plus tard.** C'est la même
    règle que le cache de l'analyste, qui se périme par l'état de la base et
    jamais par l'horloge : ici on ne périme rien, mais on **dit** de quand
    date le relevé, pour qu'un chiffre vieux de trois semaines se voie.
    """
    try:
        ligne = con.execute("SELECT MAX(fetched_at) FROM uex_prices").fetchone()
    except sqlite3.OperationalError:
        return None
    return ligne[0] if ligne else None


#: Ce que le salvage produit et que le marché cote. Les sigles du jeu ne sont
#: pas ceux d'UEX : « RMC » n'existe nulle part en base, la commodité s'appelle
#: *Recycled Material Composite*. Chercher le sigle rendait zéro ligne.
_COMMODITES_DE_SALVAGE = ("Recycled Material Composite", "Construction Materials")


def bloc_calcule(con: sqlite3.Connection, calcul: str,
                 systeme: str | None = None) -> dict[str, Any] | None:
    """La moitié vivante d'une fiche — calculée, jamais rédigée.

    Une fiche de minage qui écrirait « le Quantainium se vend 148 462 aUEC »
    serait fausse au prochain rafraîchissement UEX. On appelle donc les
    outils qui savent déjà répondre, et on **date** ce qu'on rend.

    Le bloc peut être absent : sans relevé UEX, la page ne casse pas, elle
    retire le bloc en le disant. C'est la règle « une réponse partielle
    s'annonce partielle ».
    """
    from . import queries

    releve = _releve_uex(con)

    if calcul == "minage":
        classement = queries.rentabilite_minage(
            con, "quel minerai rapporte le plus", systeme=systeme, limit=8)
        rares = [m for m in classement.get("classement", ()) if m.get("palier", 0) >= 4]
        return {
            "genre": "minage",
            "titre": "Les minerais qui paient le plus, aujourd'hui",
            "lignes": [
                {"nom": m["nom"], "detail": m["rarete"],
                 "valeur": m["prix"], "unite": "aUEC",
                 "ou": ", ".join(m.get("systemes") or []),
                 "gisements": m.get("gisements")}
                for m in (rares or classement.get("classement", ()))[:6]],
            # Nommer ce qu'on ne sait pas chiffrer plutôt que de l'omettre :
            # 18 minerais d'ingrédient ne se vendent à aucun terminal.
            "sans_cotation": [m["nom"] for m in classement.get("sans_cote", ())][:6],
            "releve": releve,
            "note": "Prix du minerai **raffiné**. Les bruts ne sont presque "
                    "jamais cotés.",
        }

    if calcul == "salvage":
        # **Le titre a dû changer après mesure.** Il annonçait « où revendre
        # au mieux » ; vérifié le 2026-08-14, les trois relevés du *Recycled
        # Material Composite* sont de type `commodity` et portent **zéro
        # terminal**. UEX cote la matière, pas le comptoir. Promettre un lieu
        # qu'on n'a pas serait la faute que le projet paie le plus cher.
        lignes, sans_lieu = [], False
        for nom in _COMMODITES_DE_SALVAGE:
            meilleur = con.execute(
                "SELECT terminal, star_system, price_sell FROM uex_prices "
                "WHERE name = ? AND price_sell > 0 "
                "ORDER BY price_sell DESC LIMIT 1", (nom,)).fetchone()
            if not meilleur:
                continue
            if not meilleur[0]:
                sans_lieu = True
            lignes.append({"nom": nom, "detail": meilleur[0] or "",
                           "valeur": meilleur[2], "unite": "aUEC",
                           "ou": meilleur[1] or "", "gisements": None})
        if not lignes:
            return None
        note = "Meilleur prix de vente relevé."
        if sans_lieu:
            note += (" UEX cote ces matières **sans terminal** : le prix est "
                     "connu, le comptoir ne l'est pas.")
        return {"genre": "salvage", "titre": "Ce que ça vaut à la revente",
                "lignes": lignes, "sans_cotation": [], "releve": releve,
                "note": note}

    if calcul == "commerce":
        route = queries.get_trade_route(con, "quelle route commerciale")
        propositions = route.get("routes") or []
        if not propositions:
            return None
        return {"genre": "commerce",
                "titre": "Les écarts de prix du moment",
                "lignes": [
                    {"nom": r.get("commodity"),
                     "detail": f"{r.get('from_terminal') or '?'}"
                               f" → {r.get('to_terminal') or '?'}",
                     "valeur": r.get("margin"),
                     "unite": "aUEC/unité",
                     "ou": " → ".join(
                         dict.fromkeys(x for x in (r.get("from_system"),
                                                   r.get("to_system")) if x)),
                     "gisements": None}
                    for r in propositions[:6]],
                "sans_cotation": [], "releve": releve,
                "note": "Marge à l'unité, avant frais. Les écarts bougent à "
                        "chaque relevé : à revérifier avant d'acheter."}
    return None


def _lignes(con: sqlite3.Connection, table: str, cle: str,
            ordre: str = "rang") -> list[dict[str, Any]]:
    return [_dict(r) for r in con.execute(
        f"SELECT * FROM {table} WHERE cle=? ORDER BY {ordre}", (cle,))]


def resoudre_activite(con: sqlite3.Connection, terme: str) -> str | None:
    """La clé d'une activité depuis son nom ou un de ses alias.

    Volontairement lexical et strict : ce catalogue compte neuf entrées, et
    une correspondance floue y ferait plus de dégâts qu'ailleurs — « minage »
    ne doit pas rendre « Storm Breaker » sous prétexte qu'on y mine.
    """
    from .normalize import normalize

    cible = normalize(terme or "").strip()
    if not cible:
        return None
    ligne = con.execute(
        "SELECT cle FROM activites WHERE LOWER(nom)=? OR cle=?",
        (cible, cible)).fetchone()
    if ligne:
        return ligne[0]
    ligne = con.execute(
        "SELECT cle FROM activite_aliases WHERE alias=?", (cible,)).fetchone()
    if ligne:
        return ligne[0]
    # Un alias **ou un nom** contenu dans le terme tapé — « la mission tsg »,
    # « les onyx », « le minage ».
    #
    # **Le nom compte autant que l'alias.** La première version ne balayait
    # que `activite_aliases`, et « le minage » ne résolvait donc rien : le
    # mot est le `nom` et la `cle` de la fiche, pas un de ses alias. Le
    # défaut ne s'est vu qu'en vérifiant que les questions de la sonde du
    # balayage atteignaient bien leur outil — « en quoi consiste le minage »
    # partait chez `vaisseaux_par_metier`.
    #
    # Le plus long d'abord, pour que « salvage multicrew » gagne sur
    # « salvage » quand les deux sont dans la phrase.
    # **Les noms se normalisent, les alias sont déjà normalisés.** Les noms
    # portent leurs accents en base — « Récupération en équipage » — et la
    # cible n'en a plus. Sans ce passage, le nom le plus précis ne matchait
    # jamais : « la récupération en équipage » rendait `salvage` au lieu de
    # `salvage-multicrew`.
    candidats = [
        (cle, normalize(terme))
        for cle, terme in con.execute(
            "SELECT cle, alias FROM activite_aliases "
            "UNION SELECT cle, nom FROM activites "
            "UNION SELECT cle, cle FROM activites")]
    # Le plus long d'abord, pour que « récupération en équipage » gagne sur
    # « récupération » quand les deux sont dans la phrase.
    for cle, terme_connu in sorted(candidats, key=lambda c: -len(c[1])):
        if len(terme_connu) >= 4 and terme_connu in cible:
            return cle
    return None


def fiche_activite(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """Le détail d'une activité : l'éditorial, plus ce que la base sait d'elle."""
    cle = resoudre_activite(con, query) or query
    ligne = con.execute("SELECT * FROM activites WHERE cle=?", (cle,)).fetchone()
    if ligne is None:
        raise NotFound(f"je ne connais pas d'activité « {query} »")

    detail = next(f for f in _base(con) if f["cle"] == cle)
    detail["etapes"] = _lignes(con, "activite_etapes", cle)
    detail["materiel"] = _lignes(con, "activite_materiel", cle)
    detail["recompenses"] = _lignes(con, "activite_recompenses", cle)
    detail["avertissements"] = _lignes(con, "activite_avertissements", cle)
    detail["sources"] = _lignes(con, "activite_sources", cle)
    detail["lieux"] = _lignes(con, "activite_lieux", cle, ordre="cherche")
    detail["liens"] = [
        {"vers": vers, "relation": relation, "nom": nom}
        for vers, relation, nom in con.execute(
            "SELECT l.vers, l.relation, a.nom FROM activite_liens l "
            "LEFT JOIN activites a ON a.cle = l.vers WHERE l.cle=?", (cle,))]

    # **Le compte annoncé doit égaler ce qui est listé** : on dédoublonne sur
    # le titre affiché, jamais sur l'UUID. Le jeu porte plusieurs contrats de
    # titre identique, et un joueur compte ce qu'il lit.
    contrats = [_dict(r) for r in con.execute(
        "SELECT a.via, c.uuid, c.debug_name, c.title, c.mission_type, "
        "       c.min_standing_name, c.mission_giver, c.faction_name, "
        "       c.reward_uec "
        "FROM activite_contrats a JOIN contracts c ON c.uuid = a.uuid "
        "WHERE a.cle=? ORDER BY c.debug_name", (cle,))]
    # **Un titre de gabarit n'est le nom de rien.** `<= UNINITIALIZED =>` et
    # `[TargetName]` sont des valeurs que le serveur remplace à la
    # génération ; les afficher présente un jeton technique comme un nom de
    # mission. La règle existait dans le projet et manquait ici — vu à
    # l'écran le 2026-08-14 sur la fiche d'abordage. On écarte de la
    # **liste** et donc du **compte**, pour que les deux restent d'accord.
    vus, distincts = set(), []
    for contrat in contrats:
        titre = contrat.get("title") or contrat.get("debug_name")
        if not titre or _GABARIT.search(titre):
            continue
        if titre in vus:
            continue
        vus.add(titre)
        distincts.append(contrat)
    detail["contrats_lies"] = distincts
    detail["contrats"] = len(distincts)
    detail["contrats_bruts"] = len(contrats)

    # Les rangs réellement exigés par les contrats du jeu, plutôt que la
    # phrase de la fiche : c'est la moitié mesurée du prérequis.
    detail["rangs_exiges"] = sorted({
        c["min_standing_name"] for c in contrats if c.get("min_standing_name")})

    # La moitié vivante, s'il y en a une. Elle échoue **sans casser la
    # fiche** : sans relevé UEX, on retire le bloc plutôt que de rendre une
    # page en erreur — le reste de la fiche est toujours vrai.
    detail["calcule"] = None
    if detail.get("calcul"):
        try:
            detail["calcule"] = bloc_calcule(
                con, detail["calcul"], systeme=detail.get("systeme"))
        except (sqlite3.Error, NotFound, KeyError, TypeError):
            detail["calcule"] = None
    return detail


LIBELLES_PREUVE = {
    "annonce": "annoncé, pas encore vérifiable",
    "fichiers": "déjà dans les fichiers du jeu",
}


def quoi_de_neuf(con: sqlite3.Connection, query: str = "") -> dict[str, Any]:
    """Ce qui arrive au prochain patch — et ce qu'on peut déjà en prouver.

    **Une note de patch n'est pas une donnée de jeu.** Notre base décrit le
    patch *installé* ; un patch à venir ne s'y vérifie pas. Chaque ligne
    porte donc son niveau de preuve, et le rendu le dit :

    - `annonce` — dit par un guide ou par CIG, invérifiable ici ;
    - `fichiers` — l'objet est **déjà dans les fichiers du build courant**,
      donc on peut le nommer exactement. Mesuré : la BUL-H4 et la Vendetta
      sont en base depuis la 4.9 alors qu'elles sont annoncées pour la 4.10.

    C'est la seule chose qu'un guide ne sait pas dire, et c'est ce qui
    justifie que le bot en parle plutôt que de renvoyer vers un lien.
    """
    notes = [_dict(ligne) for ligne in con.execute("SELECT * FROM nouveautes")]
    if not notes:
        raise NotFound("je n'ai aucune note de patch")
    # Une note à venir reste prioritaire sur une note sortie ; dans chaque
    # groupe, un patch est une suite d'entiers. Le tri SQL lexical plaçait
    # 4.9 après 4.10 alors que `version_patch` connaissait déjà la règle.
    note = max(notes, key=lambda n: (
        n["statut"] == "a_venir", version_patch(n["patch"])))
    patch = note["patch"]

    lignes = [_dict(r) for r in con.execute(
        "SELECT * FROM nouveaute_lignes WHERE patch=? ORDER BY rang", (patch,))]
    for item in lignes:
        item["preuve_libelle"] = LIBELLES_PREUVE.get(item["preuve"])
        # Le lien vers la fiche d'activité n'est rendu que s'il **résout** :
        # un bouton mort vaut moins que pas de bouton.
        if item.get("voir"):
            nom = con.execute("SELECT nom FROM activites WHERE cle=?",
                              (item["voir"],)).fetchone()
            item["voir_nom"] = nom[0] if nom else None

    # Groupé par catégorie, dans l'ordre où les lignes ont été écrites : c'est
    # l'ordre d'importance choisi par le rédacteur, pas l'alphabet.
    categories: list[dict[str, Any]] = []
    for item in lignes:
        trouve = next((c for c in categories if c["nom"] == item["categorie"]), None)
        if trouve is None:
            trouve = {"nom": item["categorie"], "lignes": []}
            categories.append(trouve)
        trouve["lignes"].append(item)

    note["categories"] = categories
    note["total"] = len(lignes)
    note["deja_en_base"] = sum(1 for i in lignes if i["preuve"] == "fichiers")
    note["avertissements"] = [_dict(r) for r in con.execute(
        "SELECT * FROM nouveaute_avertissements WHERE patch=? ORDER BY rang",
        (patch,))]
    note["sources"] = [_dict(r) for r in con.execute(
        "SELECT * FROM nouveaute_sources WHERE patch=? ORDER BY rang", (patch,))]
    # Le build installé, pour que le lecteur situe la note par rapport à lui.
    build = con.execute("SELECT game_version FROM ingest_runs WHERE status='ok' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    note["build_installe"] = build[0] if build else None
    return note


def catalogue_activites(con: sqlite3.Connection) -> dict[str, Any]:
    """De quoi peupler les filtres sans que le front devine les valeurs."""
    fiches = _base(con)
    return {
        "total": len(fiches),
        "systemes": sorted({f["systeme"] for f in fiches if f.get("systeme")}),
        "combats": [{"value": v, "label": lib} for v, lib in LIBELLES_COMBAT.items()],
        "missions": [{"value": v, "label": lib} for v, lib in LIBELLES_MISSION.items()],
        "statuts": [{"value": v, "label": lib} for v, lib in LIBELLES_STATUT.items()],
        "tris": [{"value": v, "label": LIBELLES_TRI[v]} for v in TRIS],
        "tri_par_defaut": TRI_PAR_DEFAUT,
        "joueurs_max": max((f.get("joueurs_max") or 0) for f in fiches) or None,
    }
