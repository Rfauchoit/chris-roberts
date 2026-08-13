"""Interface commune du routeur — §7 du brief.

« Interface commune : `question: str -> ToolCall`. Trois implémentations
interchangeables par variable d'environnement. **Pas trois codebases.** »

La Phase 2 n'implémente que l'étage 1 (déterministe). Les étages 2 et 3 (LLM
cloud, LLM local) viendront derrière cette même interface, sans rien changer
ici ni dans l'API.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any, Protocol

from .. import qualite, queries


@dataclasses.dataclass(frozen=True)
class ToolCall:
    """Ce qu'un routeur produit : un appel de fonction, pas une réponse.

    Le §7 est explicite — le routeur traduit la question en appel de fonction.
    Les chiffres viennent ensuite de la base, jamais du routeur.
    """

    tool: str
    args: dict[str, Any]
    confidence: float
    via: str                       # deterministic | llm_local
    intent_score: float = 0.0
    entity_score: float = 0.0


class Router(Protocol):
    def route(self, con: sqlite3.Connection, question: str) -> ToolCall | None: ...


# Le catalogue des outils. Un ajout ici suffit à exposer une fonction au
# routeur, à l'API et — plus tard — au function calling des étages 2 et 3.
@dataclasses.dataclass(frozen=True)
class Tool:
    name: str
    fn: Any
    arg: str                       # nom de l'argument qui reçoit l'entité
    entity_types: tuple[str, ...]  # types acceptés pour cet argument
    description: str               # servira de description de fonction en Phase 3


TOOLS: dict[str, Tool] = {
    "get_ship_hardpoints": Tool(
        "get_ship_hardpoints", queries.get_ship_hardpoints, "query", ("ship",),
        "Points d'emport et armement d'un vaisseau ou d'un véhicule.",
    ),
    "get_blueprint": Tool(
        "get_blueprint", queries.get_blueprint, "query", ("blueprint",),
        "Recette, provenance et grind de réputation d'un blueprint depuis "
        "un rang explicitement donné.",
    ),
    "where_to_find_resource": Tool(
        "where_to_find_resource", queries.where_to_find_resource, "query",
        ("resource", "commodity"),
        "Lieux où trouver une ressource minable ou une commodité.",
    ),
    "ou_miner": Tool(
        "ou_miner", queries.ou_miner, "query", ("resource", "commodity"),
        "Où miner un minerai en tenant compte de la composition des filons : "
        "ceux qui portent son nom, et ceux qui en contiennent en prime.",
    ),
    "get_mission_reputation": Tool(
        "get_mission_reputation", queries.get_mission_reputation, "query",
        ("contract",),
        "Réputation requise et gagnée pour une mission précise.",
    ),
    "get_mission_group": Tool(
        "get_mission_group", queries.get_mission_group, "query", ("org",),
        "Ensemble des missions d'une organisation dans un système : activités, "
        "paliers de réputation, et blueprints que la progression débloque.",
    ),
    "get_compatible_items": Tool(
        "get_compatible_items", queries.get_compatible_items, "query", ("ship",),
        "Armes montables sur un vaisseau donné, filtrables par famille "
        "(balistique, laser, gatling…), classées par DPS.",
    ),
    "compare_items": Tool(
        "compare_items", queries.compare_items, "query", (),
        "Classement d'une famille d'armes sur une statistique : DPS, alpha, "
        "cadence, portée, vitesse de projectile, munitions, masse.",
    ),
    "get_item_stats": Tool(
        "get_item_stats", queries.get_item_stats, "query", ("item",),
        "Fiche d'un objet nommé : munitions et chargeur, capacitor, cadence, "
        "dégâts, portée, masse.",
    ),
    "blueprints_de_la_meme_serie": Tool(
        "blueprints_de_la_meme_serie", queries.blueprints_de_la_meme_serie,
        "query", ("blueprint",),
        "Les autres blueprints que distribuent les mêmes missions qu'un "
        "blueprint donné : ce que la série débloque en plus.",
    ),
    "vaisseau_pour_budget": Tool(
        "vaisseau_pour_budget", queries.vaisseau_pour_budget, "query", (),
        "Le meilleur vaisseau qu'on peut s'offrir pour un budget donne, "
        "eventuellement dans une categorie : combat, transport, exploration.",
    ),
    "accessoires_compatibles": Tool(
        "accessoires_compatibles", queries.accessoires_compatibles, "query",
        ("item",),
        "Accessoires montables sur une arme personnelle : optiques, canons, "
        "chargeurs, accessoires sous canon, d'apres ses emplacements.",
    ),
    "qui_peut_monter": Tool(
        "qui_peut_monter", queries.qui_peut_monter, "query", ("item",),
        "Quels vaisseaux peuvent monter un objet donne, d'apres le type et la "
        "taille de leurs emplacements modifiables.",
    ),
    "objets_au_seuil": Tool(
        "objets_au_seuil", queries.objets_au_seuil, "query", (),
        "Armes au-dessus ou en dessous d'un seuil sur une statistique : DPS, "
        "alpha, cadence, portee, munitions, capacitor, masse.",
    ),
    "armes_par_metrique": Tool(
        "armes_par_metrique", queries.armes_par_metrique, "query", (),
        "Armes classees sur une valeur calculee : degats avant panne seche, "
        "autonomie de tir, DPS soutenu, tirs par capacitor, alpha par "
        "projectile.",
    ),
    "vaisseaux_sans_composant": Tool(
        "vaisseaux_sans_composant", queries.vaisseaux_sans_composant, "query",
        (),
        "Vaisseaux depourvus d'un composant : jump drive, moteur quantique, "
        "bouclier, refroidisseur, generateur, radar, tourelle.",
    ),
    "vaisseaux_au_seuil": Tool(
        "vaisseaux_au_seuil", queries.vaisseaux_au_seuil, "query", (),
        "Vaisseaux au-dessus ou en dessous d'un seuil sur une "
        "caracteristique : fret, vitesse, equipage, bouclier, masse.",
    ),
    "combien_y_a_t_il": Tool(
        "combien_y_a_t_il", queries.combien_y_a_t_il, "query", (),
        "Combien il existe de vaisseaux, blueprints, missions, systemes, "
        "lieux, armes, objets, factions ou constructeurs ; distingue les "
        "contrats publiés distribuant un blueprint des titres de missions.",
    ),
    "que_fabrique_t_on_avec": Tool(
        "que_fabrique_t_on_avec", queries.que_fabrique_t_on_avec, "query",
        ("resource", "item", "commodity"),
        "Ce qu'une ressource permet de fabriquer, groupé par famille "
        "d'objets — l'inverse de la recette.",
    ),
    "methode_de_raffinage": Tool(
        "methode_de_raffinage", queries.methode_de_raffinage, "query", (),
        "Quelle technique de raffinage choisir, selon le critère demandé — "
        "rendement, vitesse ou coût. Aucune n'est la meilleure partout.",
    ),
    "constructeur_de": Tool(
        "constructeur_de", queries.constructeur_de, "query", ("ship", "item"),
        "Qui construit un vaisseau ou un objet : la marque, ce qu'elle est, "
        "et ce qu'elle fabrique d'autre.",
    ),
    "ou_raffiner": Tool(
        "ou_raffiner", queries.ou_raffiner, "query",
        ("resource", "commodity"),
        "Où raffiner un minerai : les raffineries les mieux placées par "
        "rapport à ses gisements, éventuellement près d'un lieu donné.",
    ),
    "conseil_de_raffinage": Tool(
        "conseil_de_raffinage", queries.conseil_de_raffinage, "query",
        ("blueprint", "item"),
        "Où raffiner ce qu'il faut pour une recette — c'est le minerai le "
        "plus rare qui commande le choix.",
    ),
    "ou_miner_pour": Tool(
        "ou_miner_pour", queries.ou_miner_pour, "query",
        ("blueprint", "item"),
        "Le plan de minage d'une recette : le coin — une planète et ses "
        "lunes — où extraire ensemble les minerais qu'elle demande.",
    ),
    "que_trouve_t_on": Tool(
        "que_trouve_t_on", queries.que_trouve_t_on, "query", ("starmap",),
        "Ce qu'on trouve à vendre dans un lieu : commerces, nombre "
        "d'articles, de vaisseaux et de locations.",
    ),
    "combien_dans_la_soute": Tool(
        "combien_dans_la_soute", queries.combien_dans_la_soute, "query",
        ("item",),
        "Combien d'exemplaires d'un objet tiennent dans la soute d'un "
        "vaisseau, d'après le volume de l'objet et la capacité du vaisseau.",
    ),
    "peut_detruire": Tool(
        "peut_detruire", queries.peut_detruire, "query", ("ship",),
        "Le duel : un vaisseau peut-il en détruire un autre — bouclier, "
        "déflexion de l'armure, coque et budget de munitions, avec armes ou "
        "bouclier de remplacement, une seule arme explicite, ou son meilleur "
        "loadout résolu (l'armement qui passe la déflexion de la cible, "
        "réglable par grade/famille/cible rapide). Temps pondérés par la "
        "mobilité et la poursuite des armes.",
    ),
    "matchups_vaisseau": Tool(
        "matchups_vaisseau", queries.matchups_vaisseau, "query", ("ship",),
        "Les matchups théoriques d'un vaisseau : comparaison stock dans les "
        "deux sens, classement des avantages mécaniques ou périmètre des "
        "cibles destructibles, sans transformer ce calcul en résultat PvP.",
    ),
    "bataille": Tool(
        "bataille", queries.bataille, "query", ("ship",),
        "Le jeu de guerre, pour le fun : N vaisseaux contre un autre, avec "
        "surnombre, mobilité, riposte et états (sans bouclier, à l'arrêt, "
        "moitié de vie ou d'armes) — poids maison annoncés.",
    ),
    "budget_energie": Tool(
        "budget_energie", queries.budget_energie, "query", ("ship",),
        "Le budget d'énergie d'un vaisseau : pips produits par le "
        "générateur contre pips demandés par les composants, le déficit, "
        "et les descentes de palier chiffrées (medium/low) pour le combler.",
    ),
    "composants_par_pip": Tool(
        "composants_par_pip", queries.composants_par_pip, "query", (),
        "Le classement des composants par consommation d'énergie : quel "
        "bouclier ou refroidisseur consomme le moins de pips, ou produit "
        "le plus par pip consommé.",
    ),
    "loadout_energie": Tool(
        "loadout_energie", queries.loadout_energie, "query", ("ship",),
        "L'optimiseur d'énergie d'un vaisseau : le loadout économe qui "
        "libère le plus de pips, ou le plus puissant qui garde tout "
        "alimenté à fond — remplacements chiffrés à taille de port égale.",
    ),
    "loadout_discret": Tool(
        "loadout_discret", queries.loadout_discret, "query", ("ship",),
        "La furtivité d'un vaisseau : ses composants classés par signature "
        "EM et IR (une signature basse vaut mieux), et les remplacements "
        "plus discrets au même port.",
    ),
    "missions_payantes": Tool(
        "missions_payantes", queries.missions_payantes, "query", (),
        "Missions classées par ce qu'elles rapportent en aUEC, "
        "éventuellement pour une organisation donnée ; un compte à seuil "
        "passe plancher et plancher_strict.",
    ),
    "missions_du_site": Tool(
        "missions_du_site", queries.missions_du_site, "query", (),
        "Les missions qui se passent dans un complexe nommé : "
        "« quelles missions se passent à Onyx, dans les sites ASD ».",
    ),
    "catalogue_objets": Tool(
        "catalogue_objets", queries.catalogue_objets, "query", (),
        "Le catalogue d'une famille d'objets — boucliers, armes FPS, "
        "moteurs quantiques — groupé par classe ou taille, avec la marque.",
    ),
    "panorama_missions": Tool(
        "panorama_missions", queries.panorama_missions, "query", (),
        "Le menu des missions d'un système : par type ou par donneur, avec "
        "les comptes — « donne-moi les missions de Pyro ».",
    ),
    "missions_par_activite": Tool(
        "missions_par_activite", queries.missions_par_activite, "query", (),
        "Les missions d'une activité — minage, récupération, transport, "
        "course — telles que le jeu les type, filtrables par système.",
    ),
    "blueprints_par_systeme": Tool(
        "blueprints_par_systeme", queries.blueprints_par_systeme, "query", (),
        "Les blueprints qu'on débloque dans les missions, résumés par "
        "organisation — filtrables par système.",
    ),
    "rentabilite_minage": Tool(
        "rentabilite_minage", queries.rentabilite_minage, "query", (),
        "Les minerais classés par prix de vente du raffiné, avec leurs "
        "gisements — « quel minerai rapporte le plus », filtrable par "
        "système.",
    ),
    "ligne_de_vie": Tool(
        "ligne_de_vie", queries.ligne_de_vie, "query", (),
        "La preuve de vie : « t'es là ? », « tu m'entends ? » — répond que "
        "le bot tourne, avec la fraîcheur de sa base et de ses prix.",
    ),
    "decrire": Tool(
        "decrire", queries.decrire, "query",
        # **Les types déclarés ici sont ceux que le garde-fou du routeur
        # consulte**, et ils doivent suivre `_SOURCES_DESCRIPTION`. Les avoir
        # ajoutés d'un seul côté laissait « c'est quoi le Helium » se faire
        # rejeter avant d'arriver à l'outil, qui savait pourtant répondre :
        # le routeur ne trouvait aucune entité dans les types annoncés et
        # passait la main à la recherche de gisement.
        ("contract", "org", "starmap", "ship", "item", "manufacturer",
         "commodity", "resource"),
        "Description officielle d'une mission, d'un objet, d'un vaisseau, "
        "d'un lieu ou d'un personnage, telle qu'elle est écrite dans les "
        "fichiers du jeu.",
    ),
    "where_is_location": Tool(
        "where_is_location", queries.where_is_location, "query", ("starmap",),
        "Où se situe un lieu : la chaîne complète, de l'avant-poste au "
        "système, avec la distinction entre posé au sol et en orbite.",
    ),
    "peut_voyager": Tool(
        "peut_voyager", queries.peut_voyager, "query", ("starmap",),
        "Un vaisseau donné peut-il rallier un lieu depuis un autre : "
        "distance, point de saut, autonomie quantique et part du "
        "réservoir consommée. Accepte un moteur quantique de rechange.",
    ),
    "ou_acheter_pres": Tool(
        "ou_acheter_pres", queries.ou_acheter_pres, "query",
        ("ship", "item", "commodity", "resource"),
        "Points de vente d'un objet les plus proches d'un lieu donné, "
        "avec leur distance et leur prix.",
    ),
    "get_ship_components": Tool(
        "get_ship_components", queries.get_ship_components, "query", ("ship",),
        "Composants hors armement montables sur un vaisseau : bouclier, "
        "moteur quantique, refroidisseur, générateur, radar, réservoir.",
    ),
    "get_ship_stats": Tool(
        "get_ship_stats", queries.get_ship_stats, "query", ("ship",),
        "Caractéristiques d'un vaisseau : capacité de fret en SCU, équipage, "
        "vitesses, bouclier, résistance de coque, masse, carburant.",
    ),
    "compare_ships": Tool(
        "compare_ships", queries.compare_ships, "query", (),
        "Comparaison ou classement de vaisseaux sur une caractéristique : "
        "vitesse, fret, équipage, bouclier, masse.",
    ),
    "get_trade_route": Tool(
        "get_trade_route", queries.get_trade_route, "query", (),
        "Routes commerciales rentables : quoi acheter, où, où le revendre et "
        "pour quelle marge par SCU.",
    ),
    "get_distance": Tool(
        "get_distance", queries.get_distance, "query", ("starmap",),
        "Distance entre deux lieux, ou trajet par point de saut quand ils "
        "sont dans des systèmes différents.",
    ),
    "nearest_locations": Tool(
        "nearest_locations", queries.nearest_locations, "query", ("starmap",),
        "Lieux les plus proches d'un point donné, dans le même système.",
    ),
    "get_price": Tool(
        "get_price", queries.get_price, "query",
        ("ship", "item", "commodity", "resource"),
        "Prix d'achat ou de vente d'un vaisseau ou d'une commodité, et lieux "
        "où l'échanger. Données relevées par des joueurs, pas issues des "
        "fichiers du jeu.",
    ),
    "vaisseaux_multi_criteres": Tool(
        "vaisseaux_multi_criteres", queries.vaisseaux_multi_criteres, "query",
        (),
        "Vaisseaux réunissant plusieurs conditions à la fois : capacité de "
        "fret, équipage, vitesse, bouclier, masse, budget et catégorie.",
    ),
    "progression_dans": Tool(
        "progression_dans", queries.progression_dans, "query", ("org",),
        "Échelle de réputation d'une organisation : les paliers dans "
        "l'ordre, les missions de chacun, et les blueprints qu'ils ouvrent.",
    ),
    "comparer_loadouts": Tool(
        "comparer_loadouts", queries.comparer_loadouts, "query", (),
        "Compare l'équipement d'origine de plusieurs vaisseaux : armes "
        "montées, supports, missiles, bouclier et coque.",
    ),
    "plan_de_fabrication": Tool(
        "plan_de_fabrication", queries.plan_de_fabrication, "query",
        ("blueprint", "item"),
        "Plan complet pour fabriquer un objet : les matériaux, où les "
        "extraire, où les raffiner, le temps d'assemblage, l'effet de la "
        "qualité demandée et si fabriquer vaut mieux qu'acheter.",
    ),
    "acheter_ou_fabriquer": Tool(
        "acheter_ou_fabriquer", queries.acheter_ou_fabriquer, "query",
        ("blueprint", "item", "ship"),
        "Vaut-il mieux acheter un objet ou le fabriquer : compare le prix "
        "d'achat au coût des matériaux de sa recette.",
    ),
    "fiche_qualite": Tool(
        "fiche_qualite", qualite.fiche_qualite, "query", ("blueprint", "item"),
        "Ce que la qualité des matériaux change sur un objet fabriqué, à une "
        "qualité donnée : « les statistiques d'un P6-LR 900 ».",
    ),
    "vaisseaux_par_metier": Tool(
        "vaisseaux_par_metier", queries.vaisseaux_par_metier, "query", (),
        "Les vaisseaux d'un métier — récupération, minage, course, médical — "
        "d'après le rôle que le jeu leur écrit.",
    ),
    "comment_gagner": Tool(
        "comment_gagner", queries.comment_gagner, "query", (),
        "Comment gagner des aUEC : les missions les mieux payées, les "
        "routes commerciales rentables et les minerais les plus chers.",
    ),
    "ou_consomme": Tool(
        "ou_consomme", queries.ou_consomme, "query", ("commodity", "resource"),
        "Quelles installations consomment une matière première — où elle part, "
        "pas où on la vend.",
    ),
    "classer_equipement": Tool(
        # Sans entité : « une armure », « un missile » ne nomment aucun objet.
        "classer_equipement", queries.classer_equipement, "query", (),
        "Classement de l'équipement personnel et des munitions : armure "
        "contre le froid, les radiations ou un type de dégâts, casque, "
        "missile par portée ou par dégâts.",
    ),
    "emports_d_armure": Tool(
        # **Outil sans entité** : « une armure moyenne » ne nomme aucun objet,
        # et exiger une résolution faisait abandonner l'intention avant même
        # son garde-fou. C'est la classe qui porte la question ; le nom d'une
        # armure précise se résout dans l'outil.
        "emports_d_armure", queries.emports_d_armure, "query", (),
        "Ce qu'une armure permet de porter : armes, chargeurs de rechange, "
        "medpens, lancers de combat, sac à dos — par classe ou pour une "
        "armure nommée.",
    ),
    "comparer_qualites": Tool(
        "comparer_qualites", qualite.comparer_qualites, "query",
        ("blueprint", "item"),
        "Différence entre deux niveaux de qualité de fabrication du même "
        "objet : « entre un P6-LR 900 et un P6-LR 990 ».",
    ),
    "chaine_de_qualites": Tool(
        "chaine_de_qualites", qualite.chaine_de_qualites, "query",
        ("blueprint", "item"),
        "Les statistiques d'un objet le long de toute l'échelle de qualité "
        "de fabrication : « toutes les qualités du P6-LR ».",
    ),
    "qualite_pour_tuer": Tool(
        "qualite_pour_tuer", qualite.qualite_pour_tuer, "query",
        ("blueprint", "item"),
        "À partir de quelle qualité de fabrication une arme tue d'un seul tir, "
        "selon la zone touchée et la classe d'armure de la cible, avec ou sans "
        "accessoire : « à partir de quelle qualité de P6-LR je one shot une "
        "armure lourde dans la tête ».",
    ),
    "qualite_maximale_utile": Tool(
        "qualite_maximale_utile", qualite.qualite_maximale_utile, "query",
        ("blueprint", "item"),
        "Jusqu'à quelle qualité de fabrication il vaut le coup de payer pour "
        "une arme : la dernière qualité où le nombre de balles descend "
        "encore, tous scénarios d'impact confondus (tête, torse, jambes × "
        "armure légère, moyenne, lourde, plus la tête nue) — et ce que "
        "change un accessoire, silencieux compris : « jusqu'à quelle "
        "qualité ça vaut le coup pour un P6-LR ».",
    ),
    "jalons_de_qualite": Tool(
        "jalons_de_qualite", qualite.jalons_de_qualite, "query",
        ("blueprint", "item"),
        "Les jalons de qualité d'une arme : à partir de quelle qualité elle "
        "tue d'un tir, pour chaque zone et chaque classe d'armure (tête sans "
        "casque comprise), avec et sans accessoire de dégâts — et le compte "
        "de balles quand l'OS n'existe pas. C'est aussi la réponse aux "
        "mises à mort sans cible nommée (« combien de balles pour tuer "
        "avec un F55 »).",
    ),
    "echelle_de_qualite": Tool(
        "echelle_de_qualite", qualite.echelle_de_qualite, "query", (),
        "Ce qu'est l'échelle de qualité de fabrication : continue de 0 à "
        "1000, sans paliers fixes, interpolation linéaire.",
    ),
    "missions_guilde": Tool(
        "missions_guilde", queries.missions_guilde, "query", (),
        "Missions qu'un groupe de membres peut proposer selon ses preuves de "
        "réputation et de prérequis, et progression dans une chaîne.",
    ),
    "possessions_guilde": Tool(
        "possessions_guilde", queries.possessions_guilde, "query", (),
        "Membres de la guilde qui détiennent un blueprint ou qui ont déclaré "
        "ou fait observer un objet, une arme ou un vaisseau.",
    ),
}


def execute(con: sqlite3.Connection, call: ToolCall) -> dict[str, Any]:
    """Exécute un ToolCall. Aucune interprétation, juste l'aiguillage."""
    tool = TOOLS.get(call.tool)
    if tool is None:
        raise queries.NotFound(call.tool)
    kwargs = dict(call.args)
    value = kwargs.pop(tool.arg)
    return tool.fn(con, value, **kwargs)
