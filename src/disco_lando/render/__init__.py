"""Mise en forme française des réponses.

Le §2 impose que le cœur renvoie « réponse texte + données structurées brutes ».
Le texte produit ici est destiné à être **lu à voix haute** par Piper : pas de
tableau, pas de parenthèses techniques, pas de `ClassName`. Le frontend qui veut
un affichage riche pioche dans les données structurées.
"""

# Découpé le 2026-08-07 — même geste que queries.py, pour la même raison :
# un module de 4 076 lignes se réécrit par blocs, et ce geste a déjà avalé
# deux fois une constante intercalée. La façade réexporte tout : les
# appelants gardent « from . import render » et « render.render(...) ».

from .socle import (  # noqa: F401
    _CONTEXTE,
    _DEPOSIT,
    _DIT_STAT,
    _FAMILLE_LIEU,
    _FIN_DE_PHRASE,
    _GABARIT,
    _MASCULINS,
    _PLURIELS,
    _PLURIEL_EN_X,
    _RARETE,
    _TOKEN,
    _TRAILING,
    _avec_article,
    _de,
    _nom_stat,
    _nombre,
    _plural,
    _uec,
    deposit_label,
    dit_stat,
    duration_fr,
    enumerate_fr,
    for_speech,
    quantity_fr,
    question_de_suite,
    speakable_title,
    volume_scu,
)
from .armes import (  # noqa: F401
    _FAMILLES,
    _TYPES_DEGAT,
    _famille_fr,
    note_de_variantes,
    question_de_doute,
    reserve_de_confiance,
    question_de_variante,
    render_accessoires,
    render_comparison,
    render_compatible,
    render_item_stats,
    render_metrique,
    render_objets_seuil,
)
from .vaisseaux import (  # noqa: F401
    _CARRIERE_FR,
    _COMPOSANT_FR,
    _ROLE_MOTS,
    _critere_fr,
    render_budget,
    render_comparer_loadouts,
    render_components,
    render_comptage,
    render_multi_criteres,
    render_porteurs,
    render_sans_composant,
    render_seuil,
    render_ship_comparison,
    render_ship_hardpoints,
    render_ship_stats,
    render_soute,
    role_fr,
)
from .lieux import (  # noqa: F401
    _autres_du_meme_nom,
    render_distance,
    render_location,
    render_nearest,
    render_voyage,
)
from .missions import (  # noqa: F401
    _DIFFICULTE,
    _activite,
    _rangs_ordonnes,
    duree,
    render_group,
    render_mission,
    render_mission_group,
    render_mission_groups,
    render_missions_payantes,
    render_progression,
)
from .fabrication import (  # noqa: F401
    _TYPE_SORTIE,
    _quantite_ingredient,
    render_acheter_ou_fabriquer,
    render_blueprint,
    render_blueprint_complet,
    render_grind_blueprint,
    render_blueprint_missions,
    render_fabrique_avec,
    render_meme_serie,
    render_plan_de_fabrication,
)
from .commerce import (  # noqa: F401
    _NATURE_TERMINAL,
    _avec_source_uex,
    _phrase_butin,
    _phrase_fabrication,
    _phrases_achat,
    render_lieu_contenu,
    render_ou_consomme,
    render_price,
    render_price_complet,
    render_proches,
    render_trade_route,
)
from .ressources import (  # noqa: F401
    _CRITERE_FR,
    _NIVEAU_FR,
    _NIVEAU_FR_F,
    _bloc_methodes,
    _bloc_raffineries,
    _nom_de_gisement,
    _par_systeme,
    _situer_raffinerie,
    render_conseil_raffinage,
    render_methode_raffinage,
    render_ou_miner,
    render_raffinage,
    render_resource,
)
from .fiches import (  # noqa: F401
    _ARTICLE_DOUBLE,
    _ARTICLE_GENRE,
    _CHAINE_MAX,
    _CONTRACTIONS,
    _ETAT_CIVIL,
    _JETON_FR,
    _OBJECTIF_FR,
    _ORDINAUX,
    _RARETE_FR,
    _RECOLTE_FR,
    _SERVICES_FR,
    _SOURCES_FR,
    _STATUT_LIEU,
    _TYPE_LIEU,
    _bloc_chaine,
    _bloc_complexes,
    _bloc_lieu,
    _bloc_lieux,
    _bloc_minerai,
    _bloc_pratique,
    _bloc_raffine,
    _consigne,
    _entete_description,
    _garder_un_article,
    _lisible,
    _ordinal,
    _singulier_de_lieu,
    _situation,
    bloc_de_difficulte,
    render_constructeur,
    render_description,
    service_fr,
    statut_de_lieu,
)
from .personnel import (  # noqa: F401
    _LIBELLE_EMPORT,
    _composants,
    _grouper,
    _pourcent,
    render_classement_equipement,
    render_comparer_qualites,
    render_emports_armure,
    render_fiche_qualite,
)
from .energie import (  # noqa: F401
    render_budget_energie, render_composants_par_pip,
    render_loadout_discret, render_loadout_energie,
)

from .duel import (  # noqa: F401
    render_duel,
    render_matchups_vaisseau,
)
from .guilde import (  # noqa: F401
    render_missions_guilde,
    render_possessions_guilde,
)
from .conversation import (  # noqa: F401
    _EXHAUSTIFS,
    _ingredients_minables,
    _titres_de_missions,
    render,
    suites,
    sujet,
)
from .socle import _RENDERERS, _mult  # noqa: F401
