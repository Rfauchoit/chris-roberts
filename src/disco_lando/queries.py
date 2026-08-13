"""Les fonctions métier — une par question que le joueur sait poser.

Chacune renvoie des données structurées. Le §2 du brief impose que le cœur
produise « réponse texte + données structurées brutes » : la mise en forme est
l'affaire de l'appelant, pas d'ici.

Règle du §7, valable dès maintenant : **les chiffres viennent toujours de la
base**. Aucune de ces fonctions n'invente ni n'estime quoi que ce soit.

Le **vocabulaire** — tout ce qui se lit dans la phrase sans toucher la base —
vit dans [stats.py](stats.py) et se réimporte ici : les appelants font
`queries.detect_ship_stat(...)` depuis toujours, et rien ne justifie de leur
faire changer de porte. Ce qui reste dans ce fichier interroge SQLite.
"""

from __future__ import annotations

import sqlite3
from typing import Any


# Réexport explicite plutôt qu'un `import *` : la liste **est** l'inventaire
# de ce que la couche vocabulaire expose, et un nom qui y apparaît deux fois
# se voit. C'est précisément ce qui manquait quand `detect_criteres` existait
# en double dans ce fichier.
from .stats import (  # noqa: F401  (réexportés pour les appelants)
    COMPONENT_MOINS_EST_MIEUX,
    COMPONENTS, MOTS_DE_STAT, MOUNTABLE, SHIP_STATS, STATS, VOISINES,
    _ACCESSOIRES, _CARRIERES, _COMPONENT_STATS, _COMPOSANTS_NOMMES,
    _ITEM_STAT_MOTS, _METRIQUES, _MONTANT, _MOTS_ARGENT, _MOTS_DE_METRIQUE,
    _REPRISE_EN_PLUS, _SEUIL, _SHIP_STAT_MOTS, _STAT_MOTS, _inverser,
    _weapon_filter, detect_carriere, detect_component, detect_component_stat,
    detect_composant, detect_contraintes, detect_famille_accessoire,
    detect_item_stat, detect_metrique, detect_montant, detect_seuil,
    detect_ship_stat, detect_ship_stat_ou_rien, detect_stat, mots_de_reprise,
)


# Réexportés : `queries.NotFound` est attrapé partout dans le projet, et rien
# ne justifie de faire changer de porte à tous les appelants.
from ._socle import NotFound, _dict, _row  # noqa: F401,E402

from .constructeurs import _constructeur  # noqa: F401  (réexporté)

# Raffinage — voir [raffinage.py](raffinage.py).
from .raffinage import (  # noqa: F401  (réexportés pour les appelants)
    _CRITERES, _PALIER_FR, _PALIERS, _methodes_de_raffinage, _minerai,
    _raffineries_pres, conseil_de_raffinage, detect_criteres,
    methode_de_raffinage, ou_miner_pour, ou_raffiner,
)

# Descriptions — voir [descriptions.py](descriptions.py).
from .descriptions import (  # noqa: F401  (réexportés pour les appelants)
    _SOURCES_DESCRIPTION, _complexes_du_contrat, _couverture_par_systeme,
    _description_francaise, _fiche_de_mission, _lieux_manquants,
    _ligne_decrite, _prix_argent_reel, _systemes_couverts, decrire,
)

# Classement de l'équipement personnel — voir [equipement.py](equipement.py).
from .equipement import (  # noqa: F401  (réexportés pour les appelants)
    classer_equipement, detect_famille, detect_rarete, stat_de_famille,
)

# Emports d'armure — voir [armure.py](armure.py).
from .armure import (  # noqa: F401  (réexportés pour les appelants)
    detect_classe, emports_d_armure, nomme_une_armure,
)

# Qualité des matériaux de fabrication — voir [qualite.py](qualite.py).
from .qualite import (  # noqa: F401  (réexportés pour les appelants)
    QUALITE_MAX, chaine_de_qualites, comparer_qualites, detect_qualites,
    detect_accessoire_cite, detect_zone, demande_une_mise_a_mort,
    echelle_de_qualite, fiche_qualite, jalons_de_qualite,
    qualite_maximale_utile,
    nomme_une_qualite, qualite_pour_tuer, qualites_lues,
)


# Missions — voir [missions.py](missions.py).
from .missions import (  # noqa: F401  (réexportés)
    _ACTIVITES_DE_MISSION, _ACTIVITIES, _DIFFICULTES, _activity,
    _chaine_de_missions, _rang_dans_la_chaine, blueprints_par_systeme,
    detect_activite, detect_difficulte, detect_site, get_mission_group,
    get_mission_reputation, group_missions, missions_du_site,
    missions_par_activite, missions_payantes, panorama_missions,
    progression_dans,
)


# Commerce — voir [commerce.py](commerce.py).
from .commerce import (  # noqa: F401  (réexportés)
    MARGE_TYPE, QUALITE, _COTATION, _PRIX_INGREDIENT, _cotations, _cout_ingredients, _fabrication, _prix_d_un, acheter_ou_fabriquer, comment_gagner, get_price, get_trade_route, ou_acheter_pres, ou_consomme,
)


# Flotte — voir [flotte.py](flotte.py).
from .flotte import (  # noqa: F401  (réexportés)
    _METIERS, _ships_nommes, combien_dans_la_soute, compare_ships, comparer_loadouts, detect_metier, get_ship_components, get_ship_hardpoints, get_ship_stats, vaisseau_pour_budget, vaisseaux_au_seuil, vaisseaux_multi_criteres, vaisseaux_par_metier, vaisseaux_sans_composant,
)


# Armurerie — voir [armurerie.py](armurerie.py).
from .armurerie import (  # noqa: F401  (réexportés)
    catalogue_objets, detect_famille_objets,
    _COMPTAGES, _LIBELLE_ACCESSOIRE, accessoires_compatibles, armes_par_metrique, combien_y_a_t_il, compare_items, constructeur_de, get_compatible_items, get_item_stats, objets_au_seuil, que_fabrique_t_on_avec, que_trouve_t_on, qui_peut_monter,
)


# Combat — voir [combat.py](combat.py).
from .combat import (  # noqa: F401  (réexportés)
    bataille, matchups_vaisseau, peut_detruire,
)


# Énergie — voir [energie.py](energie.py) et docs/ANALYSE_ENERGIE.md.
from .energie import (  # noqa: F401  (réexportés)
    budget_energie, composants_par_pip, loadout_discret, loadout_energie,
)


# Gisements — voir [gisements.py](gisements.py).
from .gisements import (  # noqa: F401  (réexportés)
    _FORME_DE_MINERAI, _nom_de_minerai, ou_miner, rentabilite_minage, where_to_find_resource,
)

# Croisements publics du catalogue avec les preuves multi-membres.
from .guilde.questions import (  # noqa: F401
    missions_guilde, possessions_guilde,
)


# Fabrication — voir [fabrication.py](fabrication.py).
from .fabrication import (  # noqa: F401  (réexportés)
    blueprints_de_la_meme_serie, extraire_rang_actuel, get_blueprint,
    plan_de_fabrication, reponse_vide,
)

# Lieux, distances et trajets — voir [voyage.py](voyage.py).
from .voyage import (  # noqa: F401  (réexportés pour les appelants)
    SYSTEMES_RISQUES, _ecart, _escales, _jump_drive_monte, _point_de_saut,
    _quantum_drive_monte, _ravitaillements, _station_du_saut, distance_fr,
    drive_nomme_dans, get_distance, lieu_du_terminal, nearest_locations,
    peut_voyager, route_de_systemes, systemes_risques, where_is_location,
)


# ------------------------------------------------------------ 1. hardpoints

# ------------------------------------------------------------ 2. blueprint

# ------------------------------------------------- 5. montage et comparaison


def ligne_de_vie(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Chris, t'es là ? » — répondre est la preuve de vie.

    Demande de l'utilisateur : un joueur qui ne reçoit rien ne sait pas si le
    bot est éteint ou s'il n'a simplement pas compris. La ligne de vie est une
    vraie question routée : si elle répond, tout le chemin — frontend, cœur,
    base — est vivant, et la réponse dit la fraîcheur de ce qu'il sait.
    """
    build = _dict(_row(con, "SELECT build_id, game_version, finished_at "
                            "FROM ingest_runs WHERE status = 'ok' "
                            "ORDER BY id DESC LIMIT 1"))
    if build is None:
        raise NotFound(query)
    prix = _row(con, "SELECT COUNT(*), MAX(fetched_at) FROM uex_prices")
    traductions = con.execute("SELECT COUNT(*) FROM traductions").fetchone()[0]
    return {"build": build, "n_prix": prix[0], "prix_du": prix[1],
            "n_traductions": traductions, "resolution": None}


# ------------------------------------------------------------ 3. ressource

# ------------------------------------------------------------ 4. réputation

# ------------------------------------------- 9. fiche et comparaison vaisseau

# ------------------------------------------ 10. composants hors armement

# ------------------------------------------------- 11. routes commerciales

# ------------------------------------------ 15. acheter ou fabriquer

