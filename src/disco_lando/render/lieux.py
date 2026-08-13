"""Lieux et voyage : où c'est, à quelle distance, par où passer."""

from __future__ import annotations

from typing import Any

from .fiches import _TYPE_LIEU
from .socle import _nombre, _plural, enumerate_fr


def render_location(data: dict[str, Any]) -> str:
    """« Grim HEX est une station en orbite autour de Yela, une lune de… »

    La chaîne entière, parce que c'est elle qui situe : « dans Stanton » est
    exact et inutilisable. On s'arrête avant l'étoile, qu'on annonce comme
    système — personne ne dit « en orbite autour de Stanton l'étoile ».
    """
    lieu = data["location"]
    nom = lieu["name"]
    quoi, prep = _TYPE_LIEU.get(lieu.get("type_name") or "", ("un lieu", "près de"))

    # Les ancêtres jusqu'à l'étoile ; l'étoile devient « le système ».
    ancetres = [a for a in data["chaine"] if (a.get("type_name") or "") != "Star"]
    systeme = lieu.get("system_name")

    if not ancetres:
        if systeme and systeme != nom:
            # **Dire pourquoi il n'y a pas de planète, plutôt que se taire.**
            # Remarque du journal sur les stations Wikelo : « tu n'indiques pas
            # la proximité d'un système planétaire ». Mesuré — leur parent est
            # l'**étoile**, et le corps le plus proche est à 2,4 millions de
            # km : il n'y a pas de planète à citer. « Du système Stanton » tout
            # court se lit comme une donnée manquante alors que c'est la
            # réponse complète ; inventer une proximité serait pire.
            autour = ("en orbite directe autour de l'étoile"
                      if any((a.get("type_name") or "") == "Star"
                             for a in data["chaine"])
                      else "")
            phrase = f"**{nom}** est {quoi} du système **{systeme}**"
            phrase += f", {autour}" if autour else ""
            return phrase + "." + _autres_du_meme_nom(data)
        return f"**{nom}** est {quoi}." + _autres_du_meme_nom(data)

    morceaux = [f"**{nom}** est {quoi} {prep} **{ancetres[0]['name']}**"]
    for enfant, parent in zip(ancetres, ancetres[1:]):
        quoi_enfant, _ = _TYPE_LIEU.get(enfant.get("type_name") or "",
                                        ("un lieu", "près de"))
        morceaux.append(f"{quoi_enfant} de **{parent['name']}**")
    if systeme:
        morceaux.append(f"dans le système **{systeme}**")
    return ", ".join(morceaux) + "." + _autres_du_meme_nom(data)


def _autres_du_meme_nom(data: dict[str, Any], *,
                        exhaustif: bool = False) -> str:
    """« Il y a aussi Wikelo Emporium Selo et Kinga. »

    Remarque du journal : « il n'y a pas qu'une seule station Wikelo à
    Stanton ». N'en nommer qu'une laisse croire qu'on a la bonne — alors qu'on
    a pris la première d'une fratrie. Et « et 4 autres » doit être
    reprenable : « c'est quoi les autres » (journal du 2026-08-12) rendait
    la fiche entière au lieu de les nommer.
    """
    freres = data.get("freres") or []
    if not freres:
        return ""
    noms = [f["name"] for f in freres] if exhaustif else \
        [f["name"] for f in freres[:4]]
    reste = len(freres) - len(noms)
    suite = f" et {_nombre(reste)} autres" if reste > 0 else ""
    verbe = "Il y en a aussi" if len(freres) > 1 else "Il y a aussi"
    return f" {verbe} **{'**, **'.join(noms)}**{suite}."


def render_location_exhaustif(data: dict[str, Any]) -> str:
    """La même fiche, fratrie entière — « c'est quoi les autres ». """
    tronquee = render_location(data)
    complete = _autres_du_meme_nom(data, exhaustif=True)
    if not complete:
        return tronquee
    courte = _autres_du_meme_nom(data)
    return tronquee.replace(courte, complete) if courte in tronquee \
        else tronquee + complete


def render_voyage(data: dict[str, Any]) -> str:
    """« Je peux aller de microTech à Ruin Station dans un Gladius ? »

    La réponse commence par oui ou par non : c'est ce qui a été demandé. Le
    détail suit, parce qu'un « non » sans chiffre n'aide pas à décider.
    """
    from ..queries import distance_fr

    # Le départ manque : on le demande, en montrant qu'on a compris le reste.
    if data.get("depart_manquant"):
        arrivee = (data.get("to") or {}).get("name") or "là"
        navire = (data.get("ship") or {}).get("nom") or \
                 (data.get("ship") or {}).get("name")
        avec = f" en **{navire}**" if navire else ""
        return (f"Pour aller jusqu'à **{arrivee}**{avec}, d'où pars-tu ? "
                "La distance change tout — et le nombre d'escales avec elle.")

    a, b = data["from"]["name"], data["to"]["name"]
    if data.get("deja_sur_place"):
        return (f"Tu es déjà à **{a}** : il n'y a aucun trajet à faire, "
                "donc ni saut quantique ni carburant à prévoir.")

    vaisseau = data.get("ship")
    if vaisseau is None:
        # **On demande, on ne constate pas.** « Je n'ai pas reconnu le
        # vaisseau » laissait le joueur devant un mur : trois remarques du
        # journal disent « me demander avec quel vaisseau ». La question posée
        # ouvre un complément que le tour suivant vient remplir.
        return (f"Le trajet {a} → {b}, d'accord — mais avec quel vaisseau ? "
                "La réponse dépend de son moteur quantique.")
    nom = vaisseau["name"]

    if data.get("manque_saut"):
        return (f"Je n'ai pas de point de saut pour {data['manque_saut']} : "
                f"je ne peux pas calculer ce trajet.")

    # Aucun moteur quantique monté, et ce n'est pas une lacune d'extraction :
    # certains petits vaisseaux n'en ont pas du tout.
    if data.get("drive") is None and not data.get("portee"):
        return (f"**{nom}** n'a pas de moteur quantique : il ne peut pas "
                f"sauter du tout, ni vers {b} ni ailleurs.")

    # Le jump drive est un autre objet que le moteur quantique, et une autre
    # fonction : franchir un point de saut, pas circuler dans un système.
    if data.get("saut_requis") and data.get("jump_drive") is None:
        return (f"Non. {a} et {b} sont dans deux systèmes, et **{nom}** n'a "
                f"pas de jump drive — son moteur quantique le fait circuler "
                f"dans son système, pas franchir un point de saut.")

    etapes, portee = data["etapes"], data.get("portee")
    escales = data.get("escales") or [[] for _ in etapes]
    moteur = data.get("drive") or {}
    pleins = data.get("pleins", 0)

    if data.get("carburant_insuffisant"):
        # Le départ ne ravitaille pas, et la jauge n'atteint aucun point de
        # plein : conseiller un plein impossible serait inventer. Le trajet
        # passe réservoir plein — c'est le carburant qu'il faut régler.
        pct = data.get("carburant_pct")
        return (f"Pas avec **{pct:.0f} %** de carburant : depuis {a}, tu "
                f"n'atteins aucun point de ravitaillement que je connais. "
                f"{a} ne ravitaille pas — il te faut un transfert de "
                f"carburant sur place (un Starfarer, un bidon) avant de "
                f"tenter le trajet. Réservoir plein, il passe.")

    if data.get("possible") is False:
        return (f"Je ne trouve pas d'enchaînement d'arrêts qui mène "
                f"**{nom}** de {a} à {b} : la portée est trop courte pour "
                f"les points de ravitaillement que je connais.")

    # L'itinéraire, arrêt par arrêt. Le plein pris au pied d'un point de saut
    # ne compte pas : on s'y arrête de toute façon pour franchir.
    itineraire = []
    for index, (etape, arrets) in enumerate(zip(etapes, escales)):
        depuis = etape["de"]
        for arret in arrets:
            ou = (f" ({arret['system_name']})"
                  if arret.get("system_name") else "")
            if arret["name"] == depuis:
                # Ravitaillement sur place : on est déjà au quai, il n'y a pas
                # de trajet à annoncer.
                itineraire.append(f"plein à **{arret['name']}**{ou}")
            else:
                itineraire.append(f"{depuis} → **{arret['name']}**{ou} — plein")
            depuis = arret["name"]
        arrivee = etape["a"]
        systeme = (etape.get("a_obj") or {}).get("system_name")
        itineraire.append(f"{depuis} → {arrivee}"
                          + (f" ({systeme})" if systeme else "")
                          + (" — **plein**"
                             if arrivee == data.get("plein_avant_saut") else ""))
        if etape.get("station_orbite"):
            itineraire[-1] += (
                f" *(ou **{etape['station_orbite']}**, en orbite, pour "
                "ravitailler sans descendre en ville)*")
        # Le franchissement lui-même est une étape du trajet : l'omettre
        # laissait croire à un saut direct entre deux stations sans rapport.
        # Mais il ne s'affiche qu'au **changement de système** : une tournée
        # enchaîne des étapes dans Stanton, et le marqueur s'insérait entre
        # chacune — mesuré au rejeu du journal.
        if index + 1 < len(etapes):
            suivant = (etapes[index + 1].get("de_obj") or {}).get("system_name")
            if suivant and suivant != systeme:
                itineraire.append(f"**franchissement du point de saut**"
                                  f" vers {suivant}")

    tete = "Oui" if pleins == 0 else f"Oui, avec {_plural(pleins, 'plein')}"
    # « J'ai 13 % de carburant » : quand le trajet demande un plein, le dire
    # **en premier** — et si le joueur est posé sur une station qui
    # ravitaille, le plein se fait là, avant de décoller. Remarque de
    # l'utilisateur : le plan l'envoyait remplir à Comm Array en route alors
    # qu'il était déjà posé sur Grim HEX.
    if data.get("plein_au_depart"):
        pct = data.get("carburant_pct")
        avec = (f"Avec **{pct:.0f} %** de carburant" if pct is not None
                else "Avec ce qui reste")
        tete = (f"⛽ {avec}, **fais le plein à {data['plein_au_depart']} "
                f"avant de décoller** — tu es déjà sur place. "
                f"Réservoir plein, oui"
                + (f", avec {_plural(pleins, 'plein')} en route"
                   if pleins else ""))
    elif data.get("carburant_pct") is not None:
        tete += f", même en partant à {data['carburant_pct']:.0f} %"
    entete = (f"{tete}. **{nom}** a {distance_fr(portee)} d'autonomie"
              + (f" avec son {moteur['name']}" if moteur.get("name") else ""))
    if data.get("aller_retour"):
        entete += ". **Aller-retour compris** — la jauge suit les deux sens"
    elif data.get("vias"):
        entete += (f". **Tournée** : "
                   f"{' → '.join([a, *data['vias'], b])}")
    saut = data.get("jump_drive") or {}
    if data.get("saut_requis") and saut.get("name"):
        entete += f", et un jump drive {saut['name']} pour franchir le saut"
    lignes = [entete + f". Distance totale : {distance_fr(data['metres'])}."]
    lignes.append("")
    lignes.extend(f"- {ligne}" for ligne in itineraire)

    if data.get("restant_pct") is not None:
        lignes.append("")
        lignes.append(f"Tu arrives avec **{data['restant_pct']:.0f} %** de "
                      f"carburant quantique.")

    risquees = data.get("escales_risquees") or []
    quais = data.get("sans_detour") or []
    if data.get("plein_avant_saut"):
        lignes.append(f"Le plein se fait à **{data['plein_avant_saut']}**, "
                      f"avant de franchir : le quai est du côté sûr, et on "
                      f"entre de l'autre système avec le réservoir plein.")
    elif quais and not pleins:
        lignes.append(f"Tu passes devant {enumerate_fr(quais, limit=2)} sans "
                      f"avoir à t'y poser — autant faire le trajet d'une "
                      f"traite et refaire le plein à l'arrivée.")

    conseil = data.get("conseil_drive")
    if conseil:
        actuel = moteur.get("name") or "ton moteur d'origine"
        lignes.append(f"💡 Avec un **{conseil['name']}** à la place du "
                      f"{actuel} — même taille S{conseil['size']} — tu aurais "
                      f"{distance_fr(conseil['portee'])} d'autonomie et tu "
                      f"ferais le trajet sans t'arrêter"
                      + (" dans Pyro." if risquees else "."))
    if risquees:
        lignes.append(f"⚠️ {enumerate_fr(risquees, limit=3)} "
                      f"{'est' if len(risquees) == 1 else 'sont'} dans Pyro : "
                      f"s'y poser n'est pas sans risque.")
    # **L'ordre cité est le sien, le meilleur se propose** — remarque du
    # journal : « demander s'il veut cet ordre ou le plus économe ». On ne
    # perd pas un tour à demander : on répond dans son ordre ET on chiffre
    # l'autre.
    mo = data.get("meilleur_ordre")
    if mo:
        lignes.append("")
        lignes.append(f"💡 C'est l'itinéraire dans l'ordre que tu as cité. "
                      f"En passant plutôt par "
                      f"**{' → '.join(mo['noms'])}**, le trajet tombe à "
                      f"{distance_fr(mo['total'])}.")
    return "\n".join(lignes)


def render_distance(data: dict[str, Any]) -> str:
    from ..queries import distance_fr

    a, b = data["from"], data["to"]
    if not data["same_system"]:
        # Les coordonnées sont relatives à l'étoile de chaque système : les
        # soustraire donnerait un nombre, et ce nombre serait faux. Le trajet
        # réel passe par un point de saut, et c'est ça, la réponse à « c'est
        # loin ».
        saut = data.get("jump")
        texte = (f"{a['name']} est dans {a['system_name']} et {b['name']} dans "
                 f"{b['system_name']} : il faut passer par un point de saut")
        if saut and saut.get("fuel_cost"):
            cout = f"{saut['fuel_cost']:g}".replace(".", ",")
            texte += f", qui coûte {cout} de carburant quantique"
        return texte + "."

    if data["metres"] is None:
        return f"Je n'ai pas la position de {a['name']} ou de {b['name']}."

    return (f"{a['name']} est à {distance_fr(data['metres'])} de {b['name']}, "
            f"dans {a['system_name']}.")


def render_nearest(data: dict[str, Any]) -> str:
    from ..queries import distance_fr

    centre = data["centre"]
    noms = [f"{v['name']} à {distance_fr(v['metres'])}" for v in data["voisins"]]
    return (f"Autour de {centre['name']} : " + enumerate_fr(noms, limit=5) + ".")
