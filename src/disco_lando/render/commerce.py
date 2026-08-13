"""Commerce : prix, points de vente, routes, consommation."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .missions import render_mission_group
from .socle import (
    _RENDERERS,
    _de,
    _nombre,
    _plural,
    _uec,
    enumerate_fr,
    question_de_suite,
)


def render_trade_route(data: dict[str, Any]) -> str:
    """Les routes, en liste et chiffrées de bout en bout.

    L'ancienne version enfilait trois routes dans une phrase — l'ère vocale —
    et ne disait ni ce qu'il faut investir ni ce qu'on ramène : « 2 000 aUEC
    de marge par SCU » ne répond pas à « j'y gagne quoi ? ». On donne
    l'aller-retour complet : charger combien, payer combien, revendre où,
    ramener combien. Et le danger se **dit** — une route par Pyro peut être
    la meilleure, c'est au joueur de trancher.
    """
    routes = data["routes"]
    if not routes:
        return "Je n'ai pas de route rentable dans mes relevés."

    entete = "**Meilleures routes"
    if data.get("system"):
        entete += f" depuis {data['system']}"
    if data.get("ship"):
        entete += f" en {data['ship']}"
    entete += "**"
    contraintes = []
    if data.get("cargo"):
        soute = f"{_nombre(data['cargo'])} SCU de soute"
        if not data.get("ship"):
            soute += " supposée — nomme ton vaisseau pour affiner"
        contraintes.append(soute)
    if data.get("budget"):
        contraintes.append(f"{_uec(data['budget'])} d'investissement au plus")
    if contraintes:
        entete += " — " + " et ".join(contraintes)

    lignes = [entete, ""]
    def _ou(terminal: str, systeme: str | None) -> str:
        # « Admin - Nyx Gateway (Stanton) (Stanton) » : UEX écrit déjà le
        # système dans le libellé de la moitié de ses terminaux.
        if not systeme or systeme in (terminal or ""):
            return terminal
        return f"{terminal} ({systeme})"

    for route in routes[:5]:
        ligne = (f"- **{route['commodity']}** : "
                 f"{_nombre(route['charge'])} SCU à "
                 f"{_ou(route['from_terminal'], route['from_system'])} pour "
                 f"{_uec(route['investissement'])}, revente à "
                 f"{_ou(route['to_terminal'], route['to_system'])} → "
                 f"**{_uec(route['realisable'])}** de gain")
        if route.get("sans_loi"):
            ligne += " — ⚠️ passe par un système sans loi"
        if "demande acheteur" in (route.get("limites") or []):
            ligne += (f" — demande à destination limitée à "
                      f"{_nombre(route['demand'])} SCU")
        lignes.append(ligne)
    # Même avertissement que pour les prix, et il pèse plus lourd ici : une
    # route repose sur deux relevés qui vieillissent chacun de leur côté.
    lignes += ["", f"_{_mention_uex(data.get('fetched_at'))}, à vérifier "
               "sur place._"]
    return "\n".join(lignes)


def _phrases_achat(data: dict[str, Any], nom: str) -> list[str]:
    """Achat, revente, location — les voies marchandes, et elles seules."""
    achats = [o for o in data["offers"] if o["price_buy"]]
    ventes = [o for o in data["offers"] if o["price_sell"]]
    locations = [o for o in data.get("rentals", []) if o["price_buy"]]
    phrases: list[str] = []

    if achats:
        lieux = [f"{o['terminal']} dans {o['star_system']}" if o["star_system"]
                 else o["terminal"] for o in achats[:3] if o["terminal"]]
        texte = f"{nom} coûte {_uec(achats[0]['price_buy'])}"
        if lieux:
            texte += f", à {enumerate_fr(lieux, limit=3)}"
        if len(achats) > 3:
            texte += f", sur {len(achats)} points de vente"
        phrases.append(texte)

    if ventes:
        phrases.append(f"Il se revend {_uec(ventes[0]['price_sell'])}")

    if locations:
        lieux = [o["terminal"] for o in locations[:2] if o["terminal"]]
        texte = f"Il se loue à partir de {_uec(locations[0]['price_buy'])}"
        if lieux:
            texte += f", à {enumerate_fr(lieux, limit=2)}"
        phrases.append(texte)
    return phrases


def _phrase_fabrication(craft: dict[str, Any], *, aussi: bool) -> str:
    """La voie de fabrication, blueprint compris."""
    texte = "Il se fabrique aussi" if aussi else "Il se fabrique"
    groupes = craft.get("groups") or []
    if groupes:
        noms = [render_mission_group(g) for g in groupes[:2]]
        # `render_mission_group` commence par « les missions … » :
        # « sort de les missions » ne se dit pas.
        texte += f", et son blueprint sort {_de(enumerate_fr(noms, limit=2))}"
    elif craft["blueprint"].get("available_by_default"):
        texte += ", et son blueprint est disponible d'emblée"
    elif craft.get("source_count"):
        # Source référencée, mais missions pas encore publiées : dire
        # « je ne sais pas où » serait faux.
        texte += (", mais aucune mission sortie ne distribue son "
                  "blueprint pour l'instant")
    if craft.get("ingredient_count"):
        texte += (f". La recette demande "
                  f"{_plural(craft['ingredient_count'], 'composant')}")
    return texte


def _phrase_butin(loot: dict[str, Any]) -> str:
    # Dire « si », jamais « où » : aucune source, ni le dépôt ni UEX, ne relie
    # un objet lootable à un lieu. L'inventer serait pire que se taire.
    ou = ("sur les combinaisons" if loot["loot_source"] == "suit"
          else "en butin")
    return f"On peut aussi le ramasser {ou}, mais les fichiers du jeu ne disent pas où"


def _mention_uex(fetched_at: str | None) -> str:
    """La provenance et l'âge d'un relevé joueur, jusque dans le rendu."""
    if not fetched_at:
        return "Prix relevés par les joueurs sur UEX"
    try:
        instant = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        date = instant.strftime("%Y-%m-%d à %H:%M UTC")
    except (TypeError, ValueError):
        date = fetched_at
    return f"Prix relevés par les joueurs sur UEX le {date}"


def _avec_source_uex(texte: str, fetched_at: str | None = None) -> str:
    # Le §4 : la provenance reste visible. Un chiffre relevé par des joueurs
    # n'a pas la valeur d'une donnée lue dans les fichiers, et il vieillit.
    return texte + " " + _mention_uex(fetched_at) + "."


def render_proches(data: dict[str, Any]) -> str:
    """Les points de vente les plus proches d'un lieu donné.

    Vingt-huit points de vente, c'est vingt-huit trajets : ce qui décide n'est
    pas le prix mais la distance depuis là où on est. On la donne donc, et on
    dit franchement quand un point est dans un autre système — les
    coordonnées ne s'y comparent pas, il faut d'abord franchir un saut.
    """
    from ..queries import distance_fr

    nom = data["name"]
    proches = data.get("proches") or []
    if not proches:
        if data.get("portee_pres") == "location":
            return f"Je n'ai pas de loueur relevé pour {nom}."
        return f"Je n'ai pas de point de vente pour {nom}."
    origine = data.get("depuis") or {}
    if not origine:
        return render_price(data)

    quoi = ("loueurs" if data.get("portee_pres") == "location"
            else "points de vente")
    lignes = [f"**{nom}** — les {len(proches)} {quoi} les plus proches "
              f"de **{origine.get('name')}**, sur {data.get('total_points')} :"]
    for proche in proches:
        ou = proche.get("lieu") or proche.get("terminal")
        distance = (distance_fr(proche["metres"]) if proche["metres"] is not None
                    else f"dans {proche.get('systeme')}, après un point de saut")
        lignes.append(f"- **{proche['terminal']}** — {_uec(proche['prix'])}, "
                      f"{distance}"
                      + (f" ({ou})" if ou and ou not in proche['terminal'] else ""))
    return "\n".join(lignes) + "\n\n" + _mention_uex(data.get("fetched_at")) + "."


def render_price(data: dict[str, Any]) -> str:
    """L'article demandé, puis ses compagnons de conjonction, avec la note de
    filtre système — les deux exigences de la grille (lignes 2 et 3)."""
    texte = _render_price_un(data)
    if data.get("systeme"):
        hors = data.get("hors_systeme") or 0
        vendus = [o for o in data["offers"] if o["price_buy"]]
        loues = [o for o in data.get("rentals") or [] if o["price_buy"]]
        if not vendus and not loues and hors:
            texte = (f"{data['name']} n'a aucun relevé dans "
                     f"**{data['systeme']}** — {_plural(hors, 'point')} "
                     "ailleurs. Demande sans le système pour les voir.")
        elif hors:
            texte += (f"\n\n_Relevés limités à **{data['systeme']}** — "
                      f"{_plural(hors, 'autre')} ailleurs._")
    for autre in data.get("autres_prix") or []:
        texte += "\n\n" + render_price(autre)
    return texte


def _render_price_un(data: dict[str, Any]) -> str:
    """Les voies d'obtention — celles que le verbe de la question appelle.

    Un objet peut s'acheter, se louer, se fabriquer et se ramasser. Mais
    « où **acheter** un Coda » ne demande pas la recette : tout cumuler noyait
    la réponse attendue dans trois autres. C'est donc le verbe qui décide
    (`detect_portee`), et « où **trouver** » — qui ne présume de rien — reste
    le seul à tout dire, en proposant d'aller plus loin.
    """
    nom = data["name"]
    portee = data.get("portee") or "tout"
    craft = data.get("craft")
    loot = data.get("loot")
    achats = [o for o in data["offers"] if o["price_buy"]]
    locations = [o for o in data.get("rentals", []) if o["price_buy"]]

    if data.get("exclusive") and not (portee == "fabrication" and craft):
        voie = {"pledge": "à l'achat de vaisseau en argent réel",
                "abonnement": "par l'abonnement mensuel",
                "concierge": "par le programme concierge"}.get(
                    data["exclusive"], data["exclusive"])
        return f"{nom} ne s'achète pas en jeu : il s'obtient {voie}."

    # ------------------------------------------------------------- « louer »
    # « Combien coûte la location d'un Prospector » répondait le prix d'achat
    # en tête : le verbe demandait la location, elle arrivait en troisième
    # position. Même mécanique que les autres portées.
    if portee == "location":
        if locations:
            moins_cher = min(o["price_buy"] for o in locations)
            lieux = [o["terminal"] for o in locations[:2] if o["terminal"]]
            texte = (f"{nom} se loue à partir de {_uec(moins_cher)}"
                     + (", à " + " et ".join(lieux) if lieux else "")
                     + f", sur {_plural(len(locations), 'loueur')}.")
            if len(locations) > 3:
                texte += " " + question_de_suite(["la liste des loueurs"])
            return _avec_source_uex(texte, data.get("fetched_at"))
        if achats:
            texte = ". ".join(_phrases_achat(data, nom)) + "."
            return _avec_source_uex(
                f"{nom} ne se loue nulle part d'après les relevés. " + texte,
                data.get("fetched_at"))
        return f"Je n'ai pas de loueur relevé pour {nom}."

    # ------------------------------------------------------------ « acheter »
    if portee == "achat":
        phrases = _phrases_achat(data, nom)
        if phrases:
            texte = ". ".join(phrases) + "."
            if len(achats) > 3:
                texte += " " + question_de_suite(["la liste des points de vente"])
            return _avec_source_uex(texte, data.get("fetched_at"))
        # Aucun point de vente : le dire, et ouvrir l'autre porte plutôt que
        # de laisser le joueur croire que l'objet est introuvable.
        if craft:
            fabrication = _phrase_fabrication(craft, aussi=False).replace(
                "Il se fabrique", "En revanche il se fabrique", 1)
            return (f"{nom} ne s'achète nulle part. {fabrication}. "
                    + question_de_suite(["la recette complète"]))
        if loot:
            ou = ("sur les combinaisons" if loot["loot_source"] == "suit"
                  else "en butin")
            return (f"{nom} ne s'achète nulle part. On ne peut que le ramasser "
                    f"{ou}, et les fichiers du jeu ne disent pas où.")
        return f"Je n'ai pas de prix pour {nom}."

    # -------------------------------------------------------- « fabriquer »
    if portee == "fabrication":
        if craft:
            return (_phrase_fabrication(craft, aussi=False).replace(
                "Il se fabrique", f"{nom} se fabrique", 1) + ". "
                + question_de_suite(["la recette complète"]))
        if achats or locations:
            texte = ". ".join(_phrases_achat(data, nom)) + "."
            return _avec_source_uex(
                f"{nom} n'a pas de recette connue. " + texte,
                data.get("fetched_at"))
        if loot:
            return f"{nom} n'a pas de recette connue. {_phrase_butin(loot)}."
        return f"Je n'ai pas de recette pour {nom}."

    # ------------------------------------------------------------ « trouver »
    phrases = _phrases_achat(data, nom)
    if not phrases:
        if not craft and not loot:
            return f"Je n'ai pas de prix pour {nom}."
        phrases.append(f"{nom} ne s'achète pas")
    if craft:
        phrases.append(_phrase_fabrication(craft, aussi=bool(achats)))
    if loot:
        phrases.append(_phrase_butin(loot))

    texte = ". ".join(phrases) + "."
    if achats or locations or [o for o in data["offers"] if o["price_sell"]]:
        texte = _avec_source_uex(texte, data.get("fetched_at"))
    # La proposition de suite : `context.py` retient l'entité, donc « donne-moi
    # tous les points de vente » ou « où trouver le blueprint » repartent sans
    # avoir à renommer l'objet.
    offres = []
    if len(achats) > 3 or (achats and craft):
        offres.append("tous les points de vente")
    if craft:
        offres.append("la recette complète")
        offres.append("où trouver le blueprint")
    if offres:
        texte += " " + question_de_suite(offres)
    return texte


def render_price_complet(data: dict[str, Any]) -> str:
    """Tous les points de vente, sans troncature.

    Le résumé en cite trois et annonce le total ; ici on les nomme tous. La
    réponse devient longue et c'est voulu — elle n'a été demandée que parce
    que le résumé ne suffisait pas.
    """
    # La liste demandée après une question de **location** est celle des
    # loueurs, pas des vendeurs — le verbe de départ garde sa portée.
    if data.get("portee") == "location":
        locations = [o for o in data.get("rentals", []) if o["price_buy"]]
        if locations:
            lignes = [f"- {o['terminal']} — {_uec(o['price_buy'])}"
                      + (f" · {o['star_system']}" if o.get("star_system") else "")
                      for o in locations if o["terminal"]]
            return (f"**{data['name']}** — {_plural(len(lignes), 'loueur')} :\n"
                    + "\n".join(lignes)
                    + "\n\n" + _mention_uex(data.get("fetched_at")) + ".")

    achats = [o for o in data["offers"] if o["price_buy"]]
    if not achats:
        return render_price(data)

    # Une ligne par lieu. Vingt-huit terminaux enfilés dans une phrase, séparés
    # par des virgules, sont illisibles — c'était le format d'une réponse lue à
    # voix haute, pas d'une réponse écrite.
    lignes = [
        f"- {o['terminal']} — {_uec(o['price_buy'])}"
        + (f" · {o['star_system']}" if o["star_system"] else "")
        for o in achats if o["terminal"]
    ]
    texte = (f"**{data['name']}** — {_plural(len(lignes), 'point')} de vente :\n"
             + "\n".join(lignes))

    locations = [o for o in data.get("rentals", []) if o["price_buy"]]
    if locations:
        loc = [f"- {o['terminal']} — {_uec(o['price_buy'])}"
               for o in locations if o["terminal"]]
        texte += "\n\n**En location :**\n" + "\n".join(loc)
    return texte + "\n\n" + _mention_uex(data.get("fetched_at")) + "."


# Ce que vend un terminal, en français. Les valeurs viennent d'UEX et restent
# anglaises en base ; la traduction se fait ici, au dernier moment.
_NATURE_TERMINAL = {
    "item": ("article", "articles"),
    "vehicle": ("vaisseau", "vaisseaux"),
    "rental": ("location", "locations"),
    "commodity": ("commodité", "commodités"),
}


def render_lieu_contenu(data: dict[str, Any]) -> str:
    """« À Lorville : sept commerces, dont New Deal qui vend 101 vaisseaux. »

    On compte plutôt qu'on n'énumère : 655 références à Lorville se citent mal.
    Les trois plus gros commerces suffisent à orienter, et le total dit s'il en
    reste.
    """
    lieu = data["lieu"]
    nom = lieu["name"]
    terminaux, nature = data["terminaux"], data["par_nature"]

    if not terminaux:
        # Le silence est une réponse : beaucoup de lieux n'ont aucun commerce,
        # et l'inventer serait pire que de le dire.
        return (f"Aucun commerce connu à **{nom}**. "
                "Les relevés viennent d'UEX, alimenté par les joueurs.")

    resume = ", ".join(
        f"{_nombre(n)} {_NATURE_TERMINAL.get(k, (k, k))[n > 1]}"
        for k, n in sorted(nature.items(), key=lambda kv: -kv[1]))
    lignes = [f"**{nom}** — {_plural(data['commerces'], 'commerce')} : {resume}.", ""]

    for t in terminaux[:5]:
        formes = _NATURE_TERMINAL.get(t["kind"], (t["kind"], t["kind"]))
        # Le pluriel est dans la table : « vaisseau » + « s » donnait
        # « vaisseaus ».
        detail = f"{_nombre(t['n'])} {formes[t['n'] > 1]}"
        if t["achetables"] and t["achetables"] < t["n"]:
            detail += f", dont {_nombre(t['achetables'])} à l'achat"
        lignes.append(f"- {t['terminal']} : {detail}")
    if len(terminaux) > 5:
        lignes.append(f"- … et {len(terminaux) - 5} autres")

    lignes.append("\n*Relevés par les joueurs sur UEX.*")
    return "\n".join(lignes)


_RENDERERS["que_trouve_t_on"] = render_lieu_contenu


def render_ou_consomme(data: dict[str, Any]) -> str:
    """« Qui consomme du Laranite ».

    **Ce n'est pas « où le vendre », et le rendu doit le dire.** L'audit
    proposait ces données pour répondre « où écouler une cargaison » ; la
    mesure ne le permet pas — les lieux de consommation sont des salles, et
    seules 144 sur 471 sont de vraies installations. Ce qu'elles disent, c'est
    où part la matière première. Pour vendre, c'est UEX qui a les prix.
    """
    lignes = [f"**{data['total']}** installations consomment du "
              f"**{data['commodite']}** :", ""]
    for systeme, lieux in data["par_systeme"].items():
        lignes.append(f"- **{systeme}** : {enumerate_fr(lieux, limit=6)}")
    if data.get("refusent"):
        lignes += ["", "Refusent d'en prendre : "
                   + enumerate_fr(data["refusent"], limit=4) + "."]
    lignes += ["", "_Ce sont les sites qui l'utilisent, pas des points de "
               "vente : pour les prix, demande où l'acheter ou le vendre._"]
    return "\n".join(lignes)


_RENDERERS["ou_consomme"] = render_ou_consomme


def render_comment_gagner(data: dict[str, Any]) -> str:
    """« Comment gagner de l'argent » — les trois voies que la base connaît.

    Et une honnêteté en tête : les boucles chronométrées des guides — « la
    meilleure boucle aUEC/heure » — ne sont dans aucun fichier. Ce qu'on
    sait dire, ce sont les montants publiés et les prix relevés.
    """
    from .missions import render_missions_payantes

    volets = data["volets"]
    lignes = ["**Trois façons de gagner des aUEC, d'après les données :**", ""]

    if volets.get("missions"):
        # Pas d'en-tête à nous : le rendu des missions porte déjà le sien,
        # et le doubler écrivait deux fois la même ligne.
        lignes.append(render_missions_payantes(volets["missions"]))
        lignes.append("")
    if volets.get("routes"):
        lignes.append("**Le commerce**")
        lignes.append(render_trade_route(volets["routes"]))
        lignes.append("")
    if volets.get("minerais"):
        haut = ", ".join(f"{m['commodity']} à {_uec(m['prix'])}/SCU"
                         for m in volets["minerais"][:3])
        lignes.append(f"**Le minage** — les minerais les mieux payés au "
                      f"relevé : {haut}. Demande « où miner » l'un d'eux "
                      "pour les gisements.")
        lignes.append("")
    lignes.append("_Les rythmes de farm des guides — telle boucle, tant par "
                  "heure — ne sont dans aucun fichier du jeu : ici, tout est "
                  "publié ou relevé._")
    return "\n".join(lignes)


_RENDERERS["comment_gagner"] = render_comment_gagner
