"""Le fil : sujet, suites proposées, et l'aiguillage du rendu."""

from __future__ import annotations

from typing import Any

from .armes import (
    render_catalogue_objets_complet,
    render_comparison,
    render_compatible,
    render_item_stats,
)
from .commerce import (
    render_price,
    render_price_complet,
    render_proches,
    render_trade_route,
)
from .fabrication import (
    render_blueprint,
    render_blueprint_complet,
    render_blueprints_par_systeme_complet,
    render_meme_serie,
)
from .fiches import render_description
from .lieux import (
    render_distance,
    render_location,
    render_location_exhaustif,
    render_nearest,
    render_voyage,
)
from .missions import (
    render_group,
    render_mission,
    render_missions_du_site,
    render_missions_par_activite,
    render_missions_payantes,
    render_panorama_missions,
)
from .ressources import (
    render_ou_miner,
    render_raffinage_complet,
    render_rentabilite_minage_complet,
    render_resource,
)
from .socle import _RENDERERS, _nom_stat, _nombre, speakable_title

# Comment une suite nomme la zone visée. Le libellé d'une proposition doit se
# lire comme une phrase du joueur — « le même tir dans la tête » —, pas comme
# la clé technique qui la porte.
_ZONE_LIBELLE = {"tete": "dans la tête", "torse": "dans le torse",
                 "jambes": "dans les jambes"}
from .vaisseaux import (
    render_components,
    render_par_metier_complet,
    render_ship_comparison,
    render_ship_hardpoints,
    render_ship_stats,
)


_RENDERERS.update({
    "get_price": render_price,
    "ou_acheter_pres": render_proches,
    "get_item_stats": render_item_stats,
    "where_is_location": render_location,
    "decrire": render_description,
    "peut_voyager": render_voyage,
    "get_ship_stats": render_ship_stats,
    "get_ship_components": render_components,
    "get_trade_route": render_trade_route,
    "get_distance": render_distance,
    "nearest_locations": render_nearest,
    "compare_ships": render_ship_comparison,
    "get_ship_hardpoints": render_ship_hardpoints,
    "get_compatible_items": render_compatible,
    "compare_items": render_comparison,
    "get_blueprint": render_blueprint,
    "where_to_find_resource": render_resource,
    "ou_miner": render_ou_miner,
    "get_mission_reputation": render_mission,
    "get_mission_group": render_group,
    "missions_payantes": render_missions_payantes,
    "missions_du_site": render_missions_du_site,
    "missions_par_activite": render_missions_par_activite,
    "panorama_missions": render_panorama_missions,
    "blueprints_de_la_meme_serie": render_meme_serie,
})


# Rendus alternatifs, choisis quand la question demande l'exhaustivité.
_EXHAUSTIFS = {
    "get_price": render_price_complet,
    "get_blueprint": render_blueprint_complet,
    # « La liste entière » après « … et 15 autres » — journal du 2026-08-07,
    # la demande mourait en « je n'ai pas compris ».
    "ou_raffiner": render_raffinage_complet,
    "vaisseaux_par_metier": render_par_metier_complet,
    "rentabilite_minage": render_rentabilite_minage_complet,
    "catalogue_objets": render_catalogue_objets_complet,
    "blueprints_par_systeme": render_blueprints_par_systeme_complet,
    # « C'est quoi les autres » après « … et 4 autres » — journal du
    # 2026-08-12, la reprise rendait la fiche entière au lieu des noms.
    "where_is_location": render_location_exhaustif,
}


def _jour(iso: str | None) -> str | None:
    """« 2026-08-07T01:46:46+00:00 » → « 07/08/2026 » — lisible, sans heure."""
    if not iso or len(iso) < 10:
        return None
    annee, mois, jour = iso[:10].split("-")
    return f"{jour}/{mois}/{annee}"


def render_ligne_de_vie(data: dict[str, Any]) -> str:
    """« Chris, t'es là ? » — oui, et voilà ce que je sais aujourd'hui.

    La réponse elle-même est la preuve de vie ; la fraîcheur des données est
    ce qu'un joueur veut vérifier ensuite — un bot vivant sur une base
    périmée est un autre genre de panne.
    """
    build = data["build"]
    morceaux = [f"Je suis là ✅ — base du jeu **{build['game_version']}** "
                f"(build {build['build_id']})"]
    if (jour := _jour(build.get("finished_at"))):
        morceaux[0] += f", ingérée le {jour}"
    ligne_prix = f"{_nombre(data['n_prix'])} prix UEX"
    if (jour_prix := _jour(data.get("prix_du"))):
        ligne_prix += f" relevés le {jour_prix}"
    morceaux[0] += (f". {ligne_prix}, "
                    f"{_nombre(data['n_traductions'])} traductions du Cirque "
                    f"Lisoir chargées.")
    morceaux.append("\nPose ta question — un vaisseau, un prix, une mission, "
                    "un minerai.")
    return "\n".join(morceaux)


_RENDERERS["ligne_de_vie"] = render_ligne_de_vie


def sujet(tool: str, data: dict[str, Any], defaut: str | None) -> str | None:
    """Ce dont la réponse **parle**, qui n'est pas toujours ce qu'on a demandé.

    « Quelles optiques vont sur un P8-AR » interroge l'arme et répond des
    optiques : le « les » de la question suivante désigne les optiques. Retenir
    l'arme faisait chercher où acheter le P8-AR, ce que personne n'avait
    demandé.

    Par défaut, le sujet reste l'entité résolue — c'est vrai partout ailleurs.
    """
    if tool == "accessoires_compatibles":
        # Celui qu'on peut acheter, sinon le premier : « où les acheter » ne
        # doit pas tomber sur l'exemplaire introuvable.
        for cle in (lambda i: i.get("en_vente"), lambda i: True):
            for famille in data.get("familles") or []:
                for item in famille.get("items") or []:
                    if cle(item):
                        return item["name"]
    if tool == "rentabilite_minage":
        # Un outil sans entité n'a pas de sujet résolu, et `retenir` sautait :
        # le « oui » qui suivait tombait dans le vide. Le sujet d'un
        # classement est son premier — c'est lui que « où le miner » désigne.
        premier = (data.get("classement") or [{}])[0].get("nom")
        if premier:
            return premier
    if tool == "blueprints_par_systeme":
        # Même règle : le sujet du panorama est sa première organisation.
        premier = (data.get("groupes") or [{}])[0].get("org")
        if premier:
            return premier
    if tool == "catalogue_objets":
        # Le sujet d'un catalogue est son premier modèle — « son prix »,
        # « ses stats » enchaînent dessus.
        for groupe in (data.get("groupes") or {}).values():
            for entree in groupe.values():
                return entree["nom"]
    if tool == "qualite_pour_tuer":
        # L'arme est résolue par le blueprint, pas par un champ `resolution` :
        # sans cette ligne le sujet restait nul, `retenir` ne gardait rien, et
        # « et dans les jambes ? » tombait dans le vide.
        return data.get("nom") or defaut
    if tool == "composants_par_pip":
        # Le sujet d'un classement sans entité est son premier — « son
        # prix », « où l'acheter » enchaînent dessus.
        premier = (data.get("items") or [{}])[0].get("name")
        if premier:
            return premier
    if tool == "panorama_missions":
        # Le sujet du menu est son système — c'est lui que les volets et les
        # reprises prolongent.
        return data.get("systeme") or defaut
    return defaut


def suites(tool: str, data: dict[str, Any], *,
           deja_tout_dit: bool = False) -> list[Any]:
    """Ce que la réponse vient de proposer, sous forme exécutable.

    Le rendu écrivait « je peux te donner tous les points de vente » et rien
    ne permettait de répondre « oui » : la phrase suivante devait renommer
    l'objet et reformuler la demande entière. Les propositions sont donc
    produites ici — au même endroit que la phrase qui les annonce, pour
    qu'elles ne puissent pas diverger — et retenues par `context.py`.
    """
    from ..context import Suite
    from ..normalize import normalize as _normalize

    proposees: list[Suite] = []

    # « … et 4 autres » doit être reprenable : « c'est quoi les autres »
    # rendait la fiche entière au lieu des noms (journal du 2026-08-12).
    if tool == "where_is_location" and len(data.get("freres") or []) > 4:
        nom = (data.get("location") or {}).get("name")
        if nom:
            proposees.append(Suite(
                "les autres du même nom", tool, {"query": nom},
                exhaustif=True, defaut=True,
                mots=("autres", "autre", "lesquels", "lesquelles", "reste",
                      "liste", "noms")))

    # **Une question à laquelle il manque une pièce reste ouverte.** Le trajet
    # est reconnu, les deux lieux aussi, seul le vaisseau manque : on garde
    # l'appel entier sous le coude et le tour suivant n'a plus qu'à le nommer.
    # Sans ça, « en gladius » repartait au routeur, n'y trouvait aucun trajet,
    # et la conversation redémarrait à zéro.
    if tool == "peut_voyager":
        depart, arrivee = data.get("from") or {}, data.get("to") or {}
        # Le départ manque : la pièce à rapporter est un lieu.
        if data.get("depart_manquant") and arrivee.get("name"):
            args = {"to": arrivee["name"]}
            if data.get("ship"):
                args["ship"] = data["ship"].get("name")
            # La tournée et l'aller-retour survivent à « d'où pars-tu ? » :
            # sans ça, « toutes les planètes de Stanton » perdait ses
            # détours dès que le joueur nommait son départ — journal.
            if data.get("vias"):
                args["vias"] = list(data["vias"])
            if data.get("aller_retour"):
                args["aller_retour"] = True
            return [Suite(f"le trajet vers {arrivee['name']}", "peut_voyager",
                          args, complete_avec="query",
                          complete_types=("starmap",))]
        # Le vaisseau manque : la pièce à rapporter est un vaisseau.
        if data.get("ship") is None and depart.get("name") and arrivee.get("name"):
            return [Suite(
                f"le trajet {depart['name']} → {arrivee['name']}",
                "peut_voyager",
                {"query": depart["name"], "to": arrivee["name"]},
                complete_avec="ship", complete_types=("ship",))]

    # Le duel a conseillé des armes : « où en acheter » et « comment les
    # fabriquer » doivent être exécutables — le rendu vient de les proposer.
    if tool == "get_ship_components":
        # **« Encaisser » était la seule chose à ne pas savoir lire.** La
        # question des axes proposait quatre mots et n'en acceptait aucun :
        # le joueur tapait « encaisser » — le mot même qu'on venait d'écrire —
        # et recevait « je n'ai pas compris ». Quatre fois au journal du
        # 2026-08-10/11. Chaque axe devient une proposition qui rejoue
        # l'outil avec la statistique en argument ; aucune n'est le défaut,
        # la question demande un choix.
        if (data.get("meilleur_sans_critere") and data.get("items")
                and not data.get("stat")):
            from .vaisseaux import _axes_disponibles

            navire = (data.get("ship") or {}).get("name")
            if navire:
                for colonne, libelle_axe, _, _, mots in \
                        _axes_disponibles(data["items"]):
                    proposees.append(Suite(
                        libelle_axe, "get_ship_components",
                        {"query": navire, "category": data.get("label") or "",
                         "stat": colonne},
                        mots=mots, expressions=(libelle_axe,)))
        return proposees

    if tool == "qualite_pour_tuer":
        # **Les trois variables de la question sont ses trois suites.** « Et
        # dans la tête ? », « et contre une armure légère ? », « et si je mets
        # un silencieux ? » sont les reprises naturelles — demande explicite de
        # l'utilisateur pour la troisième. Sans elles, chaque variante oblige à
        # retaper la question entière, arme comprise.
        arme = data.get("nom")
        if not arme:
            return proposees
        # **Le volet voyage avec la reprise.** « Combien de balles… » puis
        # « et dans le corps ? » perdait le volet : la reprise menait par
        # « Non, à aucune qualité » et listait les shotguns — deux choses
        # que la question ne demandait pas (journal du 2026-08-12).
        base = {"query": arme, "classe": data.get("classe"),
                "zone": data.get("zone"), "volet": data.get("volet")}
        for zone, mots in (("tete", ("tete", "tête", "crane", "headshot")),
                           ("torse", ("torse", "buste", "corps", "poitrine")),
                           ("jambes", ("jambes", "jambe", "pieds"))):
            if zone != data.get("zone"):
                proposees.append(Suite(
                    f"le même tir {_ZONE_LIBELLE[zone]}", "qualite_pour_tuer",
                    base | {"zone": zone}, mots=mots))
        for classe, mots in (("legere", ("legere", "légère", "leger", "light")),
                             ("moyenne", ("moyenne", "moyen", "medium")),
                             ("lourde", ("lourde", "lourd", "heavy"))):
            if classe != data.get("classe"):
                proposees.append(Suite(
                    f"contre une armure {classe}", "qualite_pour_tuer",
                    base | {"classe": classe}, mots=mots))
        if not data.get("accessoire_demande"):
            proposees.append(Suite(
                "avec un silencieux", "qualite_pour_tuer",
                base | {"accessoire": "suppressor"}, defaut=True,
                mots=("silencieux", "suppresseur", "discret", "discretion")))
        # Le hors-sujet sort du rendu par défaut mais reste posable
        # (demande de l'utilisateur, sprint 21) : les armes qui tuent
        # d'une balle, et les pièces d'exception qui résistent à tout.
        if data.get("alternatives"):
            proposees.append(Suite(
                "les armes qui tuent d'une balle", "qualite_pour_tuer",
                base | {"volet": "alternatives"},
                mots=("armes", "alternatives", "laquelle", "autres")))
        if (data.get("armure") or {}).get("exception"):
            noms_exception = tuple(
                m for nom in (data["armure"]["exception"].get("noms") or [])
                for m in nom.lower().split())
            proposees.append(Suite(
                "les pièces qui résistent quand même", "qualite_pour_tuer",
                base | {"volet": "exception"},
                mots=("exception", "resiste", "resistent", "casques",
                      "pieces") + noms_exception))
        return proposees

    if tool == "peut_detruire":
        conseillees = (data.get("conseils") or {}).get("armes") or []
        if conseillees:
            premiere = conseillees[0]["name"]
            proposees.append(Suite(
                f"où acheter la {premiere}", "get_price",
                {"query": premiere, "portee": "achat"}, defaut=True,
                entite_proposee=True,
                mots=("acheter", "achat", "vente", "prix", "vend")))
            proposees.append(Suite(
                f"comment fabriquer la {premiere}", "get_blueprint",
                {"query": premiere}, entite_proposee=True,
                mots=("fabriquer", "fabrication", "recette", "blueprint",
                      "craft")))
        return proposees

    if tool == "get_price":
        nom = data.get("name")
        achats = [o for o in data.get("offers") or [] if o["price_buy"]]
        if nom and len(achats) > 3:
            proposees.append(Suite(
                "tous les points de vente", "get_price",
                {"query": nom, "portee": "achat"}, exhaustif=True,
                mots=("vente", "ventes", "point", "points", "acheter",
                      "boutique", "boutiques", "magasin", "magasins", "lieu",
                      "lieux", "terminal", "terminaux")))
        # « La liste des loueurs » vient d'être annoncée : elle doit être
        # exécutable, et c'est le défaut quand la portée est la location.
        loueurs = [o for o in data.get("rentals") or [] if o["price_buy"]]
        if nom and len(loueurs) > 3:
            proposees.append(Suite(
                "la liste des loueurs", "get_price",
                {"query": nom, "portee": "location"}, exhaustif=True,
                defaut=data.get("portee") == "location",
                mots=("louer", "location", "locations", "loueur", "loueurs",
                      "liste")))
        if nom and data.get("craft"):
            proposees.append(Suite(
                "la recette complète", "get_blueprint",
                {"query": nom, "volet": "recette"}, exhaustif=True,
                mots=("recette", "fabrication", "fabriquer", "craft",
                      "ingredient", "ingredients", "composant", "composants")))
            proposees.append(Suite(
                "où trouver le blueprint", "get_blueprint",
                {"query": nom, "volet": "missions"}, exhaustif=True,
                mots=("blueprint", "mission", "missions", "plan", "obtenir")))

    elif tool in ("get_item_stats", "get_ship_stats"):
        from ..queries import SHIP_STATS, STATS, mots_de_reprise

        vaisseau = tool == "get_ship_stats"
        objet = data.get("item") or data.get("ship") or {}
        nom = objet.get("name")
        if nom and data.get("stat"):
            # La réponse n'annonce que les deux statistiques voisines — trois
            # lignes de propositions seraient pires que la fiche entière. Mais
            # **toutes** celles que l'objet renseigne sont reprenables : après
            # « 8 SCU », « la coque » doit marcher même si on ne l'a pas
            # proposée. Ce qu'on annonce et ce qu'on accepte sont deux choses.
            connues = SHIP_STATS if vaisseau else STATS
            for stat in connues:
                if stat == data["stat"] or not objet.get(stat):
                    continue
                mots = mots_de_reprise(stat, vaisseau)
                if mots:
                    proposees.append(Suite(
                        _nom_stat(stat), tool, {"query": nom, "stat": stat},
                        mots=mots))
            proposees.append(Suite(
                "la fiche complète", tool, {"query": nom, "stat": None},
                mots=("fiche", "complete", "totalite", "detail", "details",
                      "caracteristiques", "stat", "stats", "specs", "reste"),
                defaut=True))

    elif tool == "decrire" and data.get("pratique") and data.get("description"):
        # La fiche pratique a pris la place du briefing : celui-ci devient une
        # proposition, et doit donc être **exécutable**. `volet` dit au rendu
        # de donner le texte plutôt que la fiche.
        proposees.append(Suite(
            "le texte de la mission", "decrire",
            {"query": data["name"], "volet": "texte"}, defaut=True,
            mots=("texte", "briefing", "description", "histoire", "lire",
                  "raconte", "message", "consigne", "consignes")))
        # **« Où la prendre ? » et « y a-t-il des prérequis ? » sont des
        # reprises légitimes.** Mesuré dans le journal : la première rendait
        # « Je n'ai pas compris », la seconde « Tu veux dire Built on S71
        # rifle ? ». La fiche contient déjà les deux réponses ; il manquait le
        # chemin pour y revenir.
        proposees.append(Suite(
            "la fiche de la mission", "decrire", {"query": data["name"]},
            mots=("prendre", "trouver", "situe", "situee", "ou", "prerequis",
                  "prerequi", "requis", "condition", "conditions", "rang",
                  "reputation", "accepter", "debloquer", "objectif",
                  "objectifs", "faire",
                  # La fiche porte désormais la chaîne de missions.
                  "chaine", "avant", "precede", "precedente", "precedentes",
                  "suite", "apres", "ensuite", "debloque", "ouvre", "etape",
                  "etapes", "mission", "missions")))

    elif tool == "decrire" and data.get("stats_disponibles"):
        # Décrire et chiffrer sont deux questions. On répond à la première et
        # on ouvre la seconde, mais seulement quand elle a une réponse : un
        # lieu ou un personnage n'a pas de caractéristiques.
        outil = ("get_ship_stats" if data["stats_disponibles"] == "ship"
                 else "get_item_stats")
        proposees.append(Suite(
            "ses caractéristiques", outil,
            {"query": data["name"], "stat": None},
            mots=("caracteristiques", "stats", "specs", "fiche", "chiffres",
                  "details", "statistiques"),
            defaut=True))
        # **Un nom nu appelle une question, et toutes ses réponses.** Remarque
        # de l'utilisateur sur « Deadbolt III Cannon » : demander ce qu'il
        # veut — stats, blueprint, prix. On annonce peu (la phrase du rendu
        # propose les caractéristiques) et on accepte large : « le
        # blueprint » et « son prix » doivent marcher sans reformuler.
        if data.get("fabricable"):
            proposees.append(Suite(
                "son blueprint", "get_blueprint", {"query": data["name"]},
                mots=("blueprint", "blueprints", "recette", "fabriquer",
                      "fabrication", "craft", "plan", "obtenir")))
        if data.get("entity_type") == "item":
            proposees.append(Suite(
                "son prix", "get_price",
                {"query": data["name"], "portee": "achat"},
                mots=("prix", "coute", "cout", "acheter", "achat", "vendre",
                      "vente", "uec", "auec")))

    elif tool == "get_blueprint":
        nom = (data.get("blueprint") or {}).get("output_name")
        grind = data.get("grind") or {}
        if nom and data.get("volet") == "grind" and grind.get("besoin_rang"):
            # Le rang n'est pas une entité du jeu : on ouvre une proposition
            # fixe par libellé publié. « Member » au tour suivant rejoue ainsi
            # le même blueprint avec l'argument exact, sans demander au
            # résolveur de prendre un rang pour un objet.
            from ..normalize import normalize

            for rang in grind.get("rangs_possibles") or []:
                proposees.append(Suite(
                    f"depuis {rang}", "get_blueprint",
                    {"query": nom, "volet": "grind", "rang_actuel": rang},
                    mots=tuple(normalize(rang).split()),
                    expressions=(normalize(rang),)))
            return proposees
        # **La proposition annoncée doit être exécutable, et être le défaut.**
        # Le résumé du volet « missions » demandait « tu veux les N missions ? »
        # et ne produisait **aucune** suite : un « oui » ne trouvait rien à
        # reprendre. Remarque du journal, deux fois de suite — « impossible
        # d'obtenir ma mission du blueprint ».
        #
        # Et `defaut=True` est indispensable dès qu'il y a plus d'une
        # proposition : un « oui » nu ne devine pas, il prend celle qui est
        # marquée. En ajoutant « où trouver les matériaux », j'avais fait
        # passer le compte de une à deux et cassé le « oui » qui marchait.
        if nom and data.get("mission_count"):
            proposees.append(Suite(
                ("la mission qui donne le blueprint"
                 if data["mission_count"] == 1
                 else f"les {_nombre(data['mission_count'])} missions "
                      "qui donnent le blueprint"),
                "get_blueprint", {"query": nom, "volet": "missions"},
                exhaustif=True, defaut=True,
                mots=("mission", "missions", "blueprint", "obtenir", "plan",
                      "liste", "lister", "detail", "details")))
        if data.get("volet") == "missions":
            # La liste vient d'être donnée en entier : la reproposer serait
            # une question pour rien. « Décris-moi les missions » enchaîne
            # directement sur ce qu'on y fait.
            if deja_tout_dit:
                proposees.clear()
            # **Après les noms, ce qu'on y fait.** « Décris-moi les missions »
            # veut d'abord la liste ; la suite naturelle est le briefing de
            # chacune, que `decrire` sait rendre. On en propose au plus trois :
            # au-delà, une liste de propositions n'aide plus.
            titres = _titres_de_missions(data)[:3]
            for rang, titre in enumerate(titres):
                proposees.append(Suite(
                    f"ce qu'il faut faire dans « {speakable_title(titre)} »",
                    "decrire", {"query": titre, "prefere": "chose"},
                    # Une seule mission : « oui » la prend sans hésiter.
                    defaut=(deja_tout_dit and len(titres) == 1 and rang == 0),
                    # Cette proposition mène à la **fiche** de la mission, qui
                    # porte le lieu, les prérequis et les objectifs : les trois
                    # questions posées dans le journal juste après cette liste
                    # doivent donc y mener aussi.
                    mots=("description", "descriptions", "objectif",
                          "objectifs", "faire", "detail", "details",
                          "briefing", "consigne", "consignes",
                          "prendre", "trouver", "situe", "situee",
                          "prerequis", "prerequi", "requis", "condition",
                          "conditions", "rang", "reputation", "accepter",
                          "chaine", "avant", "precede", "precedente",
                          "precedentes", "etape", "etapes", "ouvre",
                          "mission", "missions")))
            return proposees
        # « Et où trouver les matériaux ? » — la suite naturelle d'une recette,
        # et elle n'existait pas : la question tombait dans le silence parce
        # qu'elle ne nomme aucune entité. On propose le premier ingrédient
        # minable, celui qui coûte le plus de peine à réunir.
        # **Tous les matériaux, pas le premier.** « Où trouver ces matériaux »
        # est au pluriel, et une recette en demande trois : n'en proposer qu'un
        # laissait la question à moitié répondue. Les propositions partageant
        # le même vocabulaire, une question au pluriel les déclenche toutes.
        # « miner » et « minage » n'y figurent plus : « où miner ? » désigne
        # le **plan** groupé (`ou_miner_pour`), pas trois listes de gisements.
        for ingredient in _ingredients_minables(data)[:4]:
            proposees.append(Suite(
                f"où trouver du {ingredient}", "where_to_find_resource",
                {"query": ingredient},
                mots=("materiaux", "materiau", "ingredient", "ingredients",
                      "minerai", "minerais", "trouver", "ressource", "ressources",
                      "composant", "composants")))

        # **Miner n'est que la moitié du trajet : il faut ensuite raffiner.**
        # Demande de l'utilisateur — après « où trouver les matériaux »,
        # proposer où aller les raffiner. Le conseil se cale sur le minerai le
        # plus rare de la recette, et le rendu dit lequel.
        if _ingredients_minables(data):
            sortie = (data.get("blueprint") or {}).get("output_name")
            if sortie:
                # « Où raffiner ces éléments ? » mourait sur « éléments »,
                # absent du vocabulaire — journal du 2026-08-07.
                proposees.append(Suite(
                    f"où raffiner pour fabriquer {sortie}",
                    "conseil_de_raffinage", {"query": sortie},
                    mots=("raffiner", "raffinage", "raffinerie", "raffineries",
                          "refiner", "refinery", "elements", "element",
                          "composants", "composant", "materiaux", "materiau",
                          "ingredients", "ingredient", "matieres")))
                # **« Où miner ? » après une recette veut le plan, pas trois
                # listes.** Remarque de l'utilisateur : c'est l'interpolation
                # qu'il demande — le coin où tout extraire ensemble.
                proposees.append(Suite(
                    f"où miner pour fabriquer {sortie}",
                    "ou_miner_pour", {"query": sortie},
                    mots=("miner", "minage", "extraire", "gisement",
                          "gisements", "filon", "filons")))

    elif tool == "accessoires_compatibles":
        # **« Où en acheter » après trente-deux optiques ne désigne rien.**
        # On proposait le prix du premier accessoire vendable — exact, et à
        # côté : remarque de l'utilisateur, « me demander de quoi je parle ».
        # On rend donc **plusieurs** propositions nommées : `context.choisir`
        # ne peut pas trancher entre elles, l'API repose la question, et
        # « la Delta » suffit ensuite à répondre.
        #
        # Trois, pas trente-deux : une question qui énumère un catalogue n'est
        # pas une question. Les vendables d'abord — proposer d'acheter ce qui
        # ne se vend nulle part serait une impasse.
        vendables = [i for f in data.get("familles") or []
                     for i in f.get("items") or [] if i.get("en_vente")]
        choix = vendables or [i for f in data.get("familles") or []
                              for i in f.get("items") or []]
        communs = ("acheter", "achat", "vente", "ou", "prix", "boutique",
                   "trouver")
        for item in choix[:3]:
            proposees.append(Suite(
                f"où acheter {item['name']}", "get_price",
                {"query": item["name"], "portee": "achat"},
                mots=communs + tuple(_normalize(item["name"]).split())))

    elif tool == "get_mission_group":
        # **Après la liste des blueprints, la suite naturelle est leur
        # description.** « Décris moi ces blueprint » tombait dans le vide :
        # la liste venait d'être rendue et aucune proposition ne portait ses
        # noms. On propose les sorties — ce sont elles qui ont une fiche.
        if data.get("volet") == "blueprints":
            noms = [bp["output_name"]
                    for groupe in (data.get("groups") or [])[:2]
                    for bp in groupe.get("blueprints") or []]
            for rang, nom in enumerate(noms[:3]):
                proposees.append(Suite(
                    f"la fiche de {nom}", "decrire", {"query": nom},
                    entite_proposee=True, defaut=(rang == 0),
                    mots=("decris", "decrire", "description", "descriptions",
                          "fiche", "fiches", "detail", "details",
                          "blueprint", "blueprints")
                    + tuple(_normalize(nom).split())))
            return proposees
        # Après « les missions Wikelo », « lesquelles rapportent le plus »
        # repartait sur **toutes** les missions du jeu : l'organisation était
        # perdue. Elle est ici, et la proposition la porte.
        for groupe in (data.get("groups") or [])[:1]:
            org = groupe.get("mission_giver")
            if not org:
                continue
            proposees.append(Suite(
                f"les missions {org} les mieux payées", "missions_payantes",
                {"query": org}, exhaustif=True,
                mots=("paye", "payees", "payantes", "rapporte", "rapportent",
                      "rentable", "rentables", "argent", "uec", "plus")))
            if groupe.get("blueprints"):
                # Le rendu vient de demander « tu veux la liste ? » : cette
                # proposition est la réponse au « oui », et elle garde le
                # système du groupe — « de Stanton » se perdait en route.
                args = {"query": org, "volet": "blueprints"}
                if groupe.get("system"):
                    args["system"] = groupe["system"]
                proposees.append(Suite(
                    "la liste des blueprints que la progression débloque",
                    "get_mission_group", args, exhaustif=True, defaut=True,
                    mots=("blueprint", "blueprints", "debloque", "recompense",
                          "recompenses", "donnent", "donne", "liste")))

    elif tool == "panorama_missions" and not data.get("volet"):
        # Le menu vient de proposer deux entrées : les deux volets sont des
        # reprises, et les premiers types/donneurs se choisissent d'un mot.
        systeme = data["systeme"]
        proposees.append(Suite(
            "tous les types de missions", "panorama_missions",
            {"query": systeme, "systeme": systeme, "volet": "types"},
            defaut=True,
            mots=("tous", "toutes", "types", "type", "genres", "liste")))
        proposees.append(Suite(
            "tous les donneurs de missions", "panorama_missions",
            {"query": systeme, "systeme": systeme, "volet": "donneurs"},
            mots=("donneurs", "donneur", "missionnaires", "missionnaire",
                  "commanditaires", "commanditaire", "orgs", "organisations",
                  "qui")))
        for d in (data.get("par_donneur") or [])[:3]:
            proposees.append(Suite(
                f"les missions {d['org']}", "get_mission_group",
                {"query": d["org"], "system": systeme}, entite_proposee=True,
                mots=tuple(_normalize(d["org"]).split())))

    elif tool == "blueprints_par_systeme":
        # Le rendu propose le détail d'un groupe — la suite est exécutable
        # et par défaut sur le plus riche (grille, ligne 5).
        for rang, groupe in enumerate((data.get("groupes") or [])[:3]):
            proposees.append(Suite(
                f"les blueprints des missions {groupe['org']}",
                "get_mission_group",
                {"query": groupe["org"], "volet": "blueprints"},
                entite_proposee=True, defaut=(rang == 0),
                mots=("blueprint", "blueprints", "detail", "details",
                      "groupe", "missions")
                + tuple(_normalize(groupe["org"]).split())))

    elif tool in ("catalogue_objets", "blueprints_par_systeme") and (
            data.get("groupes") or data.get("groupes_armes")):
        # « … et N autres » appelle « la liste entière » — grille, ligne 4 :
        # la proposition doit être exécutable, filtres conservés.
        groupes = data.get("groupes_armes") or data.get("groupes") or {}
        if isinstance(groupes, dict) and any(
                len(g) > 10 for g in groupes.values()):
            args: dict[str, Any] = {"query": data.get("famille") or ""}
            for cle in ("famille", "clause", "mode", "systeme"):
                if data.get(cle):
                    args[cle] = data[cle]
            proposees.append(Suite(
                "la liste entière", tool, args, exhaustif=True, defaut=True,
                mots=("liste", "entiere", "entier", "complete", "complet",
                      "toutes", "tous", "autres", "reste", "suite",
                      "modeles")))

    elif tool == "rentabilite_minage":
        # Le rendu propose « où miner l'un d'eux » — la suite par défaut est
        # le plan du premier du classement (grille, ligne 5).
        premier = (data.get("classement") or [{}])[0].get("nom")
        if premier:
            proposees.append(Suite(
                f"où miner du {premier}", "where_to_find_resource",
                {"query": premier}, entite_proposee=True, defaut=True,
                mots=("miner", "minage", "gisement", "gisements", "trouver",
                      "plan") + tuple(_normalize(premier).split())))
        if (data.get("total") or 0) > len(data.get("classement") or []):
            args = {"query": "rentabilité"}
            if data.get("systeme"):
                args["systeme"] = data["systeme"]
            proposees.append(Suite(
                "la liste entière des minerais", "rentabilite_minage", args,
                exhaustif=True,
                mots=("liste", "entiere", "complete", "toutes", "tous",
                      "autres", "reste", "minerais", "suite")))

    elif tool == "vaisseaux_par_metier":
        # « … et N autres » appelle « la liste entière » — ligne 4 de la
        # grille, l'appel reconstruit avec le rôle pour garder le filtre.
        if len(data.get("vaisseaux") or []) > 8 and data.get("role"):
            proposees.append(Suite(
                "la liste entière des vaisseaux", "vaisseaux_par_metier",
                {"query": data.get("libelle") or data["role"],
                 "role": data["role"], "libelle": data.get("libelle") or ""},
                exhaustif=True, defaut=True,
                mots=("liste", "entiere", "entier", "complete", "complet",
                      "toutes", "tous", "autres", "reste", "vaisseaux",
                      "suite")))

    elif tool == "missions_par_activite":
        # « Poser des questions spécifiques dessus derrière » — remarque de
        # l'utilisateur. Les trois premières fiches sont proposées ; les
        # autres titres restent typables tels quels.
        noms = [m["title"] for missions in (data.get("par_type") or {}).values()
                for m in missions if m.get("title") and "[" not in m["title"]]
        for rang, nom in enumerate(list(dict.fromkeys(noms))[:3]):
            proposees.append(Suite(
                f"la fiche de « {nom} »", "get_mission_reputation",
                {"query": nom}, entite_proposee=True, defaut=(rang == 0),
                mots=("fiche", "fiches", "detail", "details", "prerequis",
                      "paye", "reputation", "mission", "missions")
                + tuple(_normalize(nom).split())))

    elif tool == "get_mission_reputation" and not data.get("volet"):
        # « Oui » après une fiche de mission tombait dans le vide : aucune
        # proposition n'était retenue. La suite naturelle est le groupe du
        # commanditaire — c'est là que le joueur choisit sa prochaine.
        contract = data.get("contract") or {}
        org = contract.get("mission_giver")
        if org:
            args = {"query": org}
            if contract.get("system"):
                args["system"] = contract["system"]
            proposees.append(Suite(
                f"les autres missions {org}", "get_mission_group", args,
                defaut=True,
                mots=("autres", "missions", "groupe", "reste", "propose")))

    elif tool == "ou_raffiner":
        # « … et 15 autres » appelle « la liste entière », et elle mourait en
        # « je n'ai pas compris » — journal du 2026-08-07. La proposition
        # reconstruit l'appel depuis la donnée : minerais croisés, lieu et
        # système compris, sans quoi la liste complète perdrait les filtres.
        if (data.get("total") or 0) > len(data.get("raffineries") or []):
            minerai = data.get("minerai") or {}
            args: dict[str, Any] = {"query": minerai.get("nom") or ""}
            croises = [f["nom"] for f in data.get("croises") or []
                       if f["nom"] != args["query"]]
            if croises:
                args["minerais"] = croises
            if data.get("lieu"):
                args["lieu"] = data["lieu"]
            if data.get("systeme"):
                args["systeme"] = data["systeme"]
            if args["query"]:
                proposees.append(Suite(
                    "la liste entière des raffineries", "ou_raffiner", args,
                    exhaustif=True, defaut=True,
                    mots=("liste", "entiere", "entier", "complete", "complet",
                          "toutes", "tous", "autres", "reste", "raffineries",
                          "integrale", "suite")))

    elif tool == "ou_miner_pour" or (tool == "where_to_find_resource"
                                     and data.get("coins") is not None):
        # Le rendu vient de demander « tu veux savoir où raffiner tout ça ? »
        # — la suite est la réponse au « oui », calée sur le plus rare.
        sortie = data.get("sortie")
        if sortie:
            proposees.append(Suite(
                f"où raffiner pour fabriquer {sortie}",
                "conseil_de_raffinage", {"query": sortie}, defaut=True,
                mots=("raffiner", "raffinage", "raffinerie", "raffineries",
                      "rendement", "refiner", "refinery", "elements",
                      "element", "composants", "composant", "materiaux",
                      "materiau", "ingredients", "ingredient")))
        elif data.get("minerais"):
            # Pas de recette — les minerais ont été nommés à la main : le
            # raffinage passe par le croisement de `ou_raffiner`.
            noms = [m["nom"] for m in data["minerais"]]
            proposees.append(Suite(
                "où raffiner ces minerais", "ou_raffiner",
                {"query": noms[0], "minerais": noms[1:]}, defaut=True,
                mots=("raffiner", "raffinage", "raffinerie", "raffineries",
                      "rendement", "elements", "element", "minerais",
                      "minerai", "materiaux", "materiau")))

    elif tool == "get_mission_reputation" and data.get("volet") == "blueprints":
        # « Secure Site » ne distribue rien en direct : le rendu relaie vers
        # la progression du commanditaire et demande si on veut la liste.
        contract = data.get("contract") or {}
        if (not data.get("blueprints") and data.get("groupe_blueprints")
                and contract.get("mission_giver")):
            args = {"query": contract["mission_giver"], "volet": "blueprints"}
            if contract.get("system"):
                args["system"] = contract["system"]
            proposees.append(Suite(
                "la liste des blueprints de la progression",
                "get_mission_group", args, exhaustif=True, defaut=True,
                mots=("blueprint", "blueprints", "liste", "debloque",
                      "progression")))
    return proposees


def _titres_de_missions(data: dict[str, Any]) -> list[str]:
    """Les titres distincts des missions qui donnent ce blueprint.

    Le compte se fait sur le **titre affiché**, comme partout : le jeu porte
    plusieurs contrats de même titre, et proposer deux fois la même mission
    n'apprend rien.
    """
    titres: list[str] = []
    for source in data.get("sources") or []:
        for mission in source.get("missions") or []:
            titre = mission.get("title")
            if titre and titre not in titres:
                titres.append(titre)
    return titres


def _ingredients_minables(data: dict[str, Any]) -> list[str]:
    """Les matières premières d'une recette, dans l'ordre de la fiche.

    On ne garde que ce qui a un nom : les paliers d'une recette citent aussi
    des composants intermédiaires sans référence nommée.
    """
    noms: list[str] = []
    # Trois niveaux, et pas deux : un palier porte des **groupes** (« Frame »,
    # « Barrel »), chacun offrant plusieurs **options** interchangeables.
    for palier in data.get("tiers") or []:
        for groupe in palier.get("groups") or []:
            for option in groupe.get("options") or []:
                nom = option.get("ref_name")
                if nom and option.get("ingredient_kind") == "resource" \
                        and nom not in noms:
                    noms.append(nom)
    return noms


def render(tool: str, data: dict[str, Any], *, exhaustif: bool = False) -> str:
    """Met en forme, en résumé par défaut.

    Les réponses sont courtes parce qu'elles sont lues à voix haute. Mais
    « liste-moi **tous** les points de vente » demande explicitement le
    contraire : tronquer à trois serait désobéir. Seuls les rendus qui savent
    tout dire acceptent le drapeau ; les autres l'ignorent.
    """
    renderer = _RENDERERS.get(tool)
    if renderer is None:
        return "Je ne sais pas mettre cette réponse en forme."
    if exhaustif and tool in _EXHAUSTIFS:
        return _EXHAUSTIFS[tool](data)
    return renderer(data)
