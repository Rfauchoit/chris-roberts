"""Vaisseaux : emports, statistiques, composants, seuils et budgets."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .socle import (
    _RENDERERS,
    _mult,
    _de,
    _nom_stat,
    _nombre,
    _plural,
    _uec,
    dit_stat,
    enumerate_fr,
    question_de_suite,
    volume_scu,
)


# ------------------------------------------------------------------ rendus

def render_ship_hardpoints(data: dict[str, Any]) -> str:
    ship = data["ship"]
    totals = data["totals"]
    phrases = []

    mounts = data.get("pilot_mounts")
    if mounts is None:
        mounts = [m for m in data["mounts"]
                  if m["category"] == "weapon_mount"]
    if mounts:
        tailles = Counter(m["max_size"] for m in mounts)
        detail = enumerate_fr([
            f"{_plural(n, 'support')} de taille {size}"
            for size, n in sorted(tailles.items(), reverse=True)
        ])
        armes = Counter(
            child["installed_name"]
            for m in mounts for child in m["children"]
            if child["category"] == "weapon" and child["installed_name"]
        )
        if armes:
            monte = enumerate_fr([f"{n} {nom}" for nom, n in armes.most_common()])
            phrases.append(f"{ship['name']} a {detail}, équipés de {monte}")
        else:
            phrases.append(f"{ship['name']} a {detail}, actuellement vides")
    elif not data.get("tourelles"):
        phrases.append(f"{ship['name']} n'a aucun support d'arme pilote")

    libelles = {
        "habitee": ("tourelle habitée", "tourelles habitées"),
        "telecommandee": ("tourelle télécommandée",
                           "tourelles télécommandées"),
        "pdc": ("tourelle de défense rapprochée (PDC)",
                "tourelles de défense rapprochée (PDC)"),
    }
    groupes: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for tourelle in data.get("tourelles") or []:
        utilitaire = "Utility" in (tourelle.get("type") or "")
        groupes.setdefault((tourelle["kind"], utilitaire), []).append(tourelle)
    for (genre, utilitaire), tourelles in groupes.items():
        singulier, pluriel = libelles[genre]
        if utilitaire:
            singulier = "tourelle utilitaire télécommandée"
            pluriel = "tourelles utilitaires télécommandées"
        n = len(tourelles)
        morceau = _plural(n, singulier, pluriel)
        affuts = [a for t in tourelles for a in t["affuts"]]
        if affuts:
            tailles = Counter(a["max_size"] for a in affuts)
            detail_affuts = enumerate_fr([
                f"{_plural(nombre, 'affût')} de taille {taille}"
                for taille, nombre in sorted(tailles.items(), reverse=True)
            ])
            morceau += f" : {detail_affuts}"
        armes = Counter(a["installed_name"] for t in tourelles
                        for a in t["armes"] if a["installed_name"])
        if armes:
            equipees = enumerate_fr(
                [f"{n} {nom}" for nom, n in armes.most_common()])
            morceau += f", équipées de {equipees}"
        elif not utilitaire:
            morceau += ", sans arme installée publiée"
        phrases.append((f"{ship['name']} a {morceau}"
                        if not phrases else morceau))

    racks = [m for m in data["mounts"] if m["category"] == "missile_rack"]
    if racks:
        missiles = totals.get("missile", 0)
        texte = f"{_plural(len(racks), 'rack')} de missiles"
        if missiles:
            texte += f" portant {_plural(missiles, 'missile')}"
        phrases.append(texte)

    if contre := totals.get("countermeasure"):
        phrases.append(f"{_plural(contre, 'lance-contre-mesures', 'lance-contre-mesures')}")

    texte = ". ".join(phrases[:1]) + (". " + ", ".join(phrases[1:]) if phrases[1:] else "")
    if ship.get("pilot_dps"):
        texte += f". DPS pilote {ship['pilot_dps']:.0f}"
    return texte + "."


# Le rôle est une valeur de base, donc en anglais — et il part chez Piper.
# Traduit mot à mot plutôt que par une table de rôles complets : les rôles se
# combinent librement (« Starter / Light Freight », « Heavy Gunship ») et une
# table d'expressions entières serait incomplète au prochain patch.
_ROLE_MOTS = {
    "light": "léger", "heavy": "lourd", "medium": "moyen", "stealth": "furtif",
    "fighter": "chasseur", "freight": "cargo", "racing": "de course",
    "medical": "médical", "expedition": "d'exploration", "pathfinder": "éclaireur",
    "passenger": "de transport de passagers", "interdiction": "d'interdiction",
    "dropship": "de débarquement", "gunship": "canonnière", "mining": "minier",
    "salvage": "de récupération", "starter": "de débutant", "touring": "de tourisme",
    "luxury": "de luxe", "support": "de soutien", "refuel": "ravitailleur",
    "repair": "de réparation", "science": "scientifique", "bomber": "bombardier",
    "transport": "de transport", "exploration": "d'exploration",
    "interceptor": "intercepteur", "courier": "coursier", "data": "de données",
}


def role_fr(role: str | None) -> str | None:
    """« Light Freight » → « cargo léger ».

    L'ordre s'inverse : l'anglais antépose l'adjectif, le français le postpose.
    Un mot inconnu passe tel quel — mieux vaut un mot anglais isolé qu'un rôle
    amputé.
    """
    if not role:
        return None
    parties = []
    for bloc in role.split("/"):
        mots = [_ROLE_MOTS.get(m.lower(), m) for m in bloc.split()]
        # « Light Fighter » : le nom est le dernier mot en anglais.
        parties.append(" ".join(reversed(mots)) if len(mots) > 1 else mots[0])
    return " ou ".join(p.strip() for p in parties if p.strip())


def _grilles_de_soute(grilles: list[dict]) -> str:
    """Les grilles et la plus grande caisse que chacune accepte.

    Deux détails mesurés sur les premiers rendus : un Caterpillar a
    **quatorze** grilles dont douze identiques — les répéter mot pour mot ne
    disait rien de plus que leur compte — et « 1,25 m » sortait en « 1,2 »,
    l'arrondi au dixième mangeant précisément la dimension qui distingue les
    caisses. Les dimensions gardent leurs décimales (`_mult`), et les
    grilles identiques se groupent.
    """
    if not grilles:
        return ""
    groupes: dict[str, int] = {}
    for g in grilles:
        dims = " × ".join(_mult(g[d]) for d in ("max_x", "max_y", "max_z")
                          if g.get(d) is not None)
        detail = f"{_nombre(g['scu'])} SCU" if g.get("scu") else "grille"
        if dims:
            detail += f", caisses jusqu'à {dims} m"
        if g.get("externe"):
            detail += ", à l'extérieur"
        elif g.get("ouverte"):
            detail += ", plateau ouvert"
        groupes[detail] = groupes.get(detail, 0) + 1
    morceaux = [(f"{n} × ({detail})" if n > 1 else detail)
                for detail, n in groupes.items()]
    tete = (" Une seule grille : " if len(grilles) == 1
            else f" {len(grilles)} grilles : ")
    return tete + " ; ".join(morceaux[:4]) + "."


def render_ship_stats(data: dict[str, Any]) -> str:
    ship = data["ship"]
    morceaux = []

    if data.get("stat"):
        stat = data["stat"]
        texte = f"**{ship['name']}** : {dit_stat(stat, ship[stat])}."
        if stat == "autonomie_vol" and ship.get("fuel_intake"):
            # L'autonomie est exactement capacité / consommation. Le jeu
            # publie aussi un taux de captation, mais pas les conditions qui
            # permettraient d'en faire une autonomie « nette » honnête.
            texte += (f" Le calcul n'inclut pas sa captation de carburant en "
                      f"vol publiée à {_nombre(ship['fuel_intake'])} unités "
                      "par seconde.")
        # **« Est-ce que ma caisse rentre ? »** — la capacité ne le dit pas,
        # c'est la taille de caisse que la grille accepte qui décide. Un outil
        # communautaire entier existe pour ça. Le jeu publie les dimensions en
        # mètres ; celles des caisses standard ne sont dans aucun fichier, on
        # rend donc les mètres, on ne devine pas la caisse.
        if stat == "cargo_scu":
            texte += _grilles_de_soute(data.get("grilles") or [])
        # La taille seule ne dit rien sans les dimensions — c'est une classe,
        # pas une mesure.
        if stat == "size" and ship.get("length"):
            texte = (f"**{ship['name']}** est un vaisseau de taille "
                     f"{_nombre(ship['size'])} — {_nombre(ship['length'], 'm')} "
                     f"de long")
            if ship.get("width") and ship.get("height"):
                texte += (f", {_nombre(ship['width'], 'm')} d'envergure, "
                          f"{_nombre(ship['height'], 'm')} de haut")
            texte += "."
        offres = [_nom_stat(v, article=True)
                  for v in data.get("voisines") or []]
        return texte + " " + question_de_suite(offres + ["la fiche complète"])

    role = role_fr(ship.get("role") or ship.get("career"))
    if role:
        # « Expedition » se traduit en « d'exploration », qui est un
        # complément et non un nom : « un d'exploration » ne se dit pas. On
        # rétablit le nom que le rôle qualifie.
        article = "un vaisseau" if role.startswith(("de ", "d'", "à ")) else "un"
        morceaux.append(f"{ship['name']} est {article} {role}"
                        f" de {ship.get('manufacturer_name') or 'constructeur inconnu'}")
    else:
        morceaux.append(ship["name"])

    # Un champ vide n'est pas zéro : 149 vaisseaux sur 316 déclarent une soute,
    # et un chasseur n'en a tout simplement pas. Annoncer « 0 SCU » laisserait
    # croire à une donnée manquante.
    details = []
    if ship.get("crew"):
        # `_plural` inclut déjà le nombre — le préfixer donnait « 4 4 places ».
        details.append(_plural(int(ship["crew"]), "place"))
    if ship.get("cargo_scu"):
        details.append(f"{_nombre(ship['cargo_scu'])} SCU de fret")
    if ship.get("scm_speed"):
        details.append(f"{_nombre(ship['scm_speed'])} mètres par seconde en combat")
    if ship.get("max_speed"):
        details.append(f"{_nombre(ship['max_speed'])} en pointe")
    if ship.get("shield_hp"):
        details.append(f"{_nombre(ship['shield_hp'])} de bouclier")
    if ship.get("health"):
        details.append(f"{_nombre(ship['health'])} de coque")
    # La soute à minerai n'existe que sur 13 vaisseaux : la citer là où elle
    # est, c'est répondre à « quel vaisseau pour miner » sans autre outil.
    if ship.get("ore_capacity"):
        details.append(f"{_nombre(ship['ore_capacity'])} SCU de minerai")
    if details:
        morceaux.append(enumerate_fr(details, limit=6))

    # L'assurance en phrase à part : elle ne décrit pas le vaisseau, elle dit
    # ce qu'il en coûte de le perdre. Les deux chiffres sont sur les 316.
    if ship.get("insurance_cost") and ship.get("insurance_minutes"):
        morceaux.append(
            f"En cas de perte, {_nombre(ship['insurance_cost'])} aUEC de prime "
            f"et {_nombre(ship['insurance_minutes'])} minutes d'attente")
    # 12 vaisseaux sur 316 sont des gravlev : ils ne quittent pas le sol, et
    # c'est la première chose à savoir avant de demander leur portée quantique.
    if ship.get("is_gravlev"):
        morceaux.append("C'est un véhicule à sustentation, il ne va pas dans "
                        "l'espace")
    elif ship.get("is_vehicle"):
        morceaux.append("C'est un véhicule terrestre, il ne va pas dans "
                        "l'espace")

    return ". ".join(morceaux) + "."


def render_ship_comparison(data: dict[str, Any]) -> str:
    label, unite = data["stat_label"], data["stat_unit"]
    stat = data["stat"]
    lignes = [f"{s['name']} à {_nombre(s[stat])}" for s in data["ships"]]

    if data["mode"] == "duel":
        sens = "devant" if data["higher_is_better"] else "sous"
        return (f"En {label}, {lignes[0]} passe {sens} "
                + enumerate_fr(lignes[1:], limit=4) + f" ({unite}).")

    return (f"Les vaisseaux avec la plus grande {label} : "
            + enumerate_fr(lignes, limit=5) + f" ({unite}).")


#: Ce que la classe d'un composant veut dire pour un joueur. Le jeu la
#: publie en anglais sur 333 composants — c'est ce qu'on entend par « un
#: bouclier militaire », et rien ne le portait avant le 2026-08-10.
#:
#: Attention : la même colonne porte la **famille de dégâts** sur les armes
#: (« Ballistic », « Energy (Laser) »). On ne traduit donc que les classes
#: d'usage, et on laisse le reste tel quel plutôt que d'inventer.
_CLASSES = {
    "Military": "militaire", "Civilian": "civil",
    "Industrial": "industriel", "Stealth": "furtif",
    "Competition": "compétition",
}


def _signature_composant(item: dict[str, Any]) -> str:
    """« grade A, militaire » — ce que le jeu affiche, pas notre indice.

    `items.grade` est un nombre interne qui vaut 1 sur 4 941 objets sans
    grade réel : le rendre annonçait « grade 3 » sans que personne puisse
    dire ce que ça veut dire. Remarque de l'utilisateur — « c'est quoi le
    grade ? ». La lettre, elle, est celle du jeu.
    """
    morceaux = []
    if item.get("grade_lettre"):
        morceaux.append(f"grade {item['grade_lettre']}")
    classe = item.get("item_class")
    if classe in _CLASSES:
        morceaux.append(_CLASSES[classe])
    return ", ".join(morceaux)


#: Les axes sur lesquels un composant se départage vraiment, et ce qu'ils
#: veulent dire pour un joueur. L'ordre est celui qu'on propose.
#: **Le vocabulaire est celui du jeu, pas celui de la colonne.** Premier
#: essai rejeté par l'utilisateur le 2026-08-10 : « se relever vite, le
#: délai le plus court après effondrement, ça ne veut rien dire, c'est un
#: bouclier énergétique ». Il a raison — un bouclier ne se relève pas, il
#: se recharge, et `shield_downed` mesure le temps pendant lequel il ne
#: recharge plus une fois tombé. Traduire une colonne mot à mot donne une
#: phrase que personne n'emploie à la table.
# La table vit dans `stats.py`, avec le reste du vocabulaire : la question
# des axes et les **reprises** qui y répondent (« encaisser ») doivent lire
# la même liste, sinon la proposition et son acceptation divergent — c'est
# exactement ce qui laissait « encaisser » sans réponse.
from ..stats import COMPONENT_AXES as _AXES  # noqa: E402


def _axes_disponibles(items: list[dict[str, Any]]) -> list[tuple]:
    """Les axes que la donnée permet réellement de classer, et qui séparent.

    Un axe dont toutes les valeurs sont égales ne départage rien — les
    boucliers taille 2 ont tous 10 560 PV chez les militaires — et le
    proposer ferait promettre un choix qui n'en est pas un. Rend les axes
    **entiers** : la question n'affiche que le libellé et l'explication, les
    suites ont besoin de la colonne et des mots.
    """
    trouves = []
    for axe in _AXES:
        colonne = axe[0]
        valeurs = {i[colonne] for i in items if i.get(colonne)}
        if len(valeurs) >= 2:
            trouves.append(axe)
    return trouves


def render_components(data: dict[str, Any]) -> str:
    ship, libelle = data["ship"], data["label"]
    if not data["sizes"]:
        return f"{ship['name']} n'a pas d'emplacement de {libelle}."
    if not data["items"]:
        return (f"{ship['name']} accepte un {libelle} en taille "
                f"{enumerate_fr([f'S{t}' for t in data['sizes']])}, "
                f"mais je n'ai aucun modèle correspondant.")

    tailles = enumerate_fr([f"S{t}" for t in data["sizes"]])
    stat, libelle_stat = data.get("stat"), data.get("stat_label")

    if stat and any(i.get(stat) for i in data["items"]):
        # Un classement n'est rendu que s'il a été demandé : « quel bouclier
        # avec la meilleure recharge » trie sur la recharge, « quel bouclier »
        # se contente de lister ce qui rentre.
        lignes = [f"- **{i['name']}** — {_nombre(i[stat])}"
                  + (f" · {_signature_composant(i)}"
                     if _signature_composant(i) else "")
                  for i in data["items"] if i.get(stat)]
        return (f"**{ship['name']}** — {libelle} en taille {tailles}, "
                f"classés par {libelle_stat} :\n" + "\n".join(lignes))

    # **« Le meilleur » n'existe pas sans critère, et on le demande.**
    # Remarque de l'utilisateur, deux fois : « ce n'est pas la question, tu
    # donnes les meilleurs en stats mais tu demandes pour quel contexte ».
    # C'est la règle déjà écrite pour le raffinage — trois notes qui
    # s'opposent — transposée aux composants, où elle s'oppose autant :
    # mesuré sur les boucliers taille 2, le militaire achète 560 PV de plus
    # contre 300 points de signature EM, et à PV égaux le grade ne change
    # que la régénération. Classer sur un axe choisi par nous, c'est
    # répondre à côté sept fois sur dix.
    if (data.get("meilleur_sans_critere") and data["items"]
            and not stat):
        axes = _axes_disponibles(data["items"])
        if len(axes) >= 2:
            return (f"**{ship['name']}** accepte "
                    f"{_plural(len(data['items']), libelle)} en taille "
                    f"{tailles}, et « le meilleur » dépend de ce que tu "
                    f"cherches :\n"
                    + "\n".join(f"- **{axe[1]}** — {axe[2]}" for axe in axes)
                    + "\n\nLequel t'intéresse ?")

    if data.get("classement_demande") is False and data["items"]:
        # La question portait sur la compatibilité. On liste tout ce qui rentre,
        # avec les chiffres utiles quand ils existent.
        lignes = []
        for i in data["items"]:
            détail = []
            if i.get("shield_health"):
                détail.append(f"{_nombre(i['shield_health'])} PV")
            if i.get("shield_regen"):
                détail.append(f"{_nombre(i['shield_regen'])}/s de recharge")
            # Ingérées depuis le début et jamais rendues — trouvées par la
            # sentinelle des colonnes : le temps à terre d'un bouclier tombé
            # et le refroidissement d'un moteur quantique décident autant
            # que leurs maxima.
            if i.get("shield_downed"):
                # « À terre après effondrement » décrivait la colonne, pas
                # le jeu : un bouclier tombe et cesse de recharger pendant
                # ce délai. Remarque de l'utilisateur, 2026-08-10.
                détail.append(f"{_nombre(i['shield_downed'], 's')} sans "
                              "recharge s'il tombe")
            if i.get("qt_drive_speed"):
                détail.append(f"{_nombre(i['qt_drive_speed'])} de vitesse")
            if i.get("qt_cooldown"):
                détail.append(f"refroidit {_nombre(i['qt_cooldown'], 's')}")
            signature = _signature_composant(i)
            if signature:
                détail.append(signature)
            lignes.append(f"- **{i['name']}**"
                          + (f" — {', '.join(détail)}" if détail else ""))
        return (f"**{ship['name']}** accepte {_plural(len(lignes), libelle)} "
                f"en taille {tailles} :\n" + "\n".join(lignes))

    # Refroidisseurs, générateurs, radars : aucune statistique exploitable en
    # amont. On dit ce qui rentre, et on dit qu'on ne peut pas les départager —
    # inventer un classement serait pire que l'avouer.
    noms = [f"{i['name']} ({_signature_composant(i)})"
            if _signature_composant(i) else i["name"]
            for i in data["items"][:4]]
    texte = (f"{ship['name']} accepte un {libelle} en taille {tailles}. "
             f"Par exemple {enumerate_fr(noms, limit=4)}")
    if len(data["items"]) > 4:
        texte += f", sur {len(data['items'])} compatibles"
    return texte + ". Je n'ai pas de quoi les départager."


def render_soute(data: dict[str, Any]) -> str:
    """« Tu peux en mettre 400 dans la soute d'un Cutlass Black. »

    Le chiffre est une division de volumes, pas une simulation d'empilement :
    le jeu n'expose aucune règle de rangement, et une soute se remplit de
    caisses. On donne donc le calcul avec ses deux termes, pour que le joueur
    voie d'où sort le nombre et sache le corriger.
    """
    item, navire = data["item"], data.get("ship")
    volume = volume_scu(item.get("volume_uscu"))
    nom = item.get("name") or "cet objet"

    if not volume:
        return (f"Je n'ai pas le volume de **{nom}** : "
                "impossible de dire combien il en tient.")
    if navire is None:
        return (f"**{nom}** occupe **{volume}**. "
                "Dis-moi dans quel vaisseau, et je te dis combien il en tient.")

    if not navire.get("cargo_scu"):
        # Un chasseur n'a pas de soute, et « 0 » se lit comme une panne de
        # données alors que c'est la réponse.
        return (f"**{navire['name']}** n'a pas de soute — "
                f"rien à y charger. **{nom}** occupe {volume}.")

    tient = data.get("tient")
    return (f"**{_nombre(tient)} {nom}** dans la soute de "
            f"**{navire['name']}**.\n\n"
            f"- {nom} : {volume} l'unité\n"
            f"- {navire['name']} : {_nombre(navire['cargo_scu'])} SCU de soute\n\n"
            "*Volume divisé par volume : le jeu ne dit pas comment les objets "
            "s'empilent, et une soute se charge en caisses.*")


# Enregistré ici et non dans la table : la fonction est définie après elle.
_RENDERERS["combien_dans_la_soute"] = render_soute


def render_comparer_loadouts(data: dict[str, Any]) -> str:
    """« Compare l'armement d'un Gladius et d'un Arrow. »

    Aucun vainqueur global n'est annoncé : « le meilleur » n'existe pas sans
    critère, et un chasseur qui perd en coque gagne en vitesse. On dit qui
    gagne **sur chaque axe**, et le lecteur arbitre.
    """
    vaisseaux = data.get("vaisseaux") or []
    if len(vaisseaux) < 2:
        return "Il me faut au moins deux vaisseaux nommés pour comparer."

    noms = enumerate_fr([f"**{v['ship']['name']}**" for v in vaisseaux])
    lignes = [f"Comparaison de l'équipement d'origine : {noms}.", ""]

    for v in vaisseaux:
        ship = v["ship"]
        lignes.append(f"### {ship['name']}")
        detail = []
        if ship.get("pilot_dps"):
            detail.append(f"{_nombre(ship['pilot_dps'])} de DPS pilote")
        if ship.get("shield_hp"):
            detail.append(f"{_nombre(ship['shield_hp'])} de bouclier")
        if ship.get("health"):
            detail.append(f"{_nombre(ship['health'])} de coque")
        # Pas de `capitalize()` : il met le reste de la chaîne en minuscules et
        # « DPS » devenait « dps ». Même piège que dans `render_metrique`.
        if detail:
            lignes.append(", ".join(detail) + ".")

        if v["armes"]:
            lignes.append("")
            for arme in v["armes"]:
                taille = (f", taille {arme['taille']}"
                          if arme.get("taille") is not None else "")
                lignes.append(f"- {arme['n']} × {arme['nom']}{taille}")
        else:
            lignes.append("\nAucune arme montée d'origine.")

        extras = []
        if v.get("supports"):
            extras.append(_plural(v["supports"], "support orientable",
                                  "supports orientables"))
        for groupe in v["tourelles"]:
            genre = groupe["genre"]
            if genre == "habitee":
                labels = ("tourelle habitée", "tourelles habitées")
            elif genre == "pdc":
                labels = ("PDC", "PDC")
            elif "Utility" in (groupe.get("type") or ""):
                labels = ("tourelle utilitaire télécommandée",
                          "tourelles utilitaires télécommandées")
            else:
                labels = ("tourelle télécommandée",
                          "tourelles télécommandées")
            extras.append(_plural(groupe["n"], *labels))
        if v["missiles"]:
            extras.append(_plural(v["missiles"], "rack de missiles",
                                  "racks de missiles"))
        if extras:
            lignes.append(f"\nPlus {enumerate_fr(extras)}.")
        lignes.append("")

    axes = data.get("axes") or []
    if axes:
        lignes.append("### Qui gagne sur quoi")
        lignes.append("")
        for axe in axes:
            # `_plural` inclut déjà le nombre : « 1 places » sortait de la
            # concaténation naïve d'une unité au pluriel.
            if axe["unite"] == "places":
                chiffres = ", ".join(f"{nom} {_plural(int(val), 'place')}"
                                     for nom, val in axe["valeurs"])
            else:
                unite = f" {axe['unite']}" if axe["unite"] else ""
                chiffres = ", ".join(f"{nom} {_nombre(val)}{unite}"
                                     for nom, val in axe["valeurs"])
            verdict = (f" → **{axe['meilleur']}**" if axe["meilleur"]
                       else " → à égalité")
            lignes.append(f"- **{axe['libelle']}** : {chiffres}{verdict}")
        lignes.append("\n*Aucun n'est « le meilleur » : ça dépend de ce que tu "
                      "en fais.*")
    return "\n".join(lignes)
_RENDERERS["comparer_loadouts"] = render_comparer_loadouts


def render_porteurs(data: dict[str, Any]) -> str:
    """« 98 vaisseaux peuvent monter un Omnisky XII. »

    On ne donne pas le nombre d'emplacements : mesuré sur le Hammerhead, le
    comptage des ports rend 36 pour 24 tourelles réelles — les variantes de
    loadout créent des doublons. Le nombre de vaisseaux, lui, est sûr.
    """
    item, ships = data["item"], data["ships"]
    nom = item.get("name") or "cet objet"
    if not ships:
        return (f"Aucun vaisseau ne peut monter **{nom}** : "
                "aucun emplacement modifiable n'accepte ce type et cette taille.")

    taille = f" de taille {_nombre(item['size'])}" if item.get("size") else ""
    lignes = [f"**{_plural(data['total'], 'vaisseau')}** peuvent monter "
              f"**{nom}**{taille} :", ""]
    lignes += [f"- {s['name']}"
               + (f" — {s['manufacturer_name']}" if s.get("manufacturer_name") else "")
               for s in ships]
    if data["total"] > len(ships):
        lignes.append(f"- … et {data['total'] - len(ships)} autres")
    lignes.append("\n*Seuls les emplacements modifiables comptent : les autres "
                  "sont soudés.*")
    return "\n".join(lignes)


def render_seuil(data: dict[str, Any]) -> str:
    """« 41 vaisseaux ont plus de 100 SCU de fret. »"""
    sens = "plus de" if data["seuil"] == ">=" else "moins de"
    unite = f" {data['unite']}" if data["unite"] else ""
    lignes = [f"**{_plural(data['total'], 'vaisseau')}** ont {sens} "
              f"**{_nombre(data['valeur'])}{unite}** de {data['libelle']} :", ""]
    lignes += [f"- {s['name']} — {_nombre(s['valeur'])}{unite}"
               for s in data["ships"]]
    if data["total"] > len(data["ships"]):
        lignes.append(f"- … et {data['total'] - len(data['ships'])} autres")
    return "\n".join(lignes)


def render_comptage(data: dict[str, Any]) -> str:
    """« 316 vaisseaux et véhicules, variantes comprises. »

    Le libellé dit **ce qui est compté** : « 316 vaisseaux » sans préciser
    qu'on compte les variantes prête à confusion, et un joueur qui en dénombre
    une centaine en jeu croirait à une erreur.
    """
    texte = f"**{_nombre(data['combien'])}** {data['quoi']}."
    # **Un compte de trois se nomme.** « 3 systèmes » est exact et
    # inutilisable ; le joueur veut savoir lesquels. La requête ne joint la
    # liste qu'en dessous d'une douzaine — au-delà, c'est le compte qui
    # répond.
    if data.get("noms"):
        texte += " " + enumerate_fr(list(data["noms"]), limit=12) + "."
    if data.get("missions_affichables") is not None:
        texte += (f" Ils se regroupent sous **"
                  f"{_nombre(data['missions_affichables'])}** titres de "
                  "missions affichables.")
    return texte


_RENDERERS["qui_peut_monter"] = render_porteurs
_RENDERERS["vaisseaux_au_seuil"] = render_seuil
_RENDERERS["combien_y_a_t_il"] = render_comptage


def render_budget(data: dict[str, Any]) -> str:
    """« Pour 17 M en combat : l'Esperia Prowler à 16,8 M. »

    Du plus cher au moins cher : à budget donné, le meilleur vaisseau est le
    plus gros qui rentre dedans.
    """
    budget = _uec(data["budget"])
    quoi = f" de {_CARRIERE_FR.get(data['carriere'], data['carriere'])}" \
        if data.get("carriere") else ""

    if not data["ships"]:
        texte = f"Aucun vaisseau{quoi} coté à {budget} ou moins."
        manque = data.get("juste_au_dessus")
        if manque:
            texte += (f" Le moins cher est **{manque['name']}** à "
                      f"{_uec(manque['prix'])}.")
        return texte

    lignes = [f"Pour **{budget}**{quoi}, du plus cher au moins cher :", ""]
    lignes += [f"- **{s['name']}** — {_uec(s['prix'])}"
               + (f", {s['manufacturer_name']}" if s.get("manufacturer_name") else "")
               for s in data["ships"]]
    if data["total"] > len(data["ships"]):
        lignes.append(f"- … et {_plural(data['total'] - len(data['ships']), 'autre')}")

    manque = data.get("juste_au_dessus")
    if manque:
        ecart = manque["prix"] - data["budget"]
        lignes.append(f"\nJuste au-dessus : **{manque['name']}** à "
                      f"{_uec(manque['prix'])}, soit {_uec(ecart)} de plus.")
    lignes.append("\n*Prix relevés par les joueurs sur UEX ; "
                  f"{data['cotes']} vaisseaux cotés.*")
    return "\n".join(lignes)


# Les carrières du jeu, en français. La valeur reste anglaise en base.
_CARRIERE_FR = {
    "Combat": "combat", "Transporter": "transport",
    "Exploration": "exploration", "Industrial": "industrie",
    "Support": "soutien", "Competition": "course",
    "Multi-Role": "polyvalent", "Ground": "véhicule terrestre",
}

_RENDERERS["vaisseau_pour_budget"] = render_budget


def _critere_fr(critere: dict[str, Any]) -> str:
    """« plus de 100 SCU de capacité de fret »."""
    # Un critère qualitatif — « rapide », « avec du fret » — n'a pas de
    # seuil : afficher « plus de 0 m/s » ferait passer un tri pour une
    # exigence absurde.
    if critere.get("valeur") is None:
        return "**" + critere["libelle"] + "** (classés dessus)"
    sens = "plus de" if critere["sens"] == ">=" else "moins de"
    unite = f" {critere['unite']}" if critere["unite"] else ""
    # `_de` et non « de » : « de équipage » ne se dit pas.
    return (f"{sens} **{_nombre(critere['valeur'])}{unite}** "
            + _de(critere["libelle"]))


def render_multi_criteres(data: dict[str, Any]) -> str:
    """« Quel vaisseau de combat avec plus de 100 SCU sous 2 millions ? »

    Une liste vide n'est pas une réponse : quand aucun vaisseau ne satisfait
    tout, on dit **quelle exigence coûte les autres**. C'est ce que le joueur
    fera de la réponse — assouplir quelque chose — et il ne peut pas deviner
    quoi.
    """
    criteres = data.get("criteres") or []
    budget = data.get("budget")
    carriere = data.get("carriere")

    exigences = [_critere_fr(c) for c in criteres]
    if budget:
        sens = "sous" if budget[0] == "<=" else "au-dessus de"
        exigences.append(f"{sens} **{_uec(budget[1])}**")
    quoi = (f" de {_CARRIERE_FR.get(carriere, carriere)}" if carriere else "")

    if not data["ships"]:
        tete = (f"Aucun vaisseau{quoi} ne réunit {enumerate_fr(exigences, limit=4)}.")
        blocage = data.get("blocage")
        if blocage:
            quel, combien = blocage
            if quel == "budget":
                nom = "le budget"
            else:
                nom = "la contrainte sur " + next(
                    c["libelle"] for c in criteres if c["stat"] == quel)
            tete += (f"\n\nC'est **{nom}** qui bloque : en la laissant tomber, "
                     f"{_plural(combien, 'vaisseau')} correspondent.")
        else:
            tete += ("\n\nRelâcher une seule contrainte ne suffit pas — il y a "
                     "plusieurs exigences incompatibles à la fois.")
        return tete

    lignes = [f"**{_plural(data['total'], 'vaisseau')}**{quoi} réunissent "
              f"{enumerate_fr(exigences, limit=4)} :", ""]
    for ship in data["ships"]:
        details = []
        # On montre les colonnes **demandées**, pas une fiche complète : la
        # question porte sur des critères précis, et c'est sur eux que le
        # lecteur compare.
        for critere in criteres:
            valeur = ship.get(critere["stat"])
            if valeur is not None:
                unite = f" {critere['unite']}" if critere["unite"] else ""
                details.append(f"{_nombre(valeur)}{unite}")
        if ship.get("prix"):
            details.append(_uec(ship["prix"]))
        suffixe = f" — {', '.join(details)}" if details else ""
        lignes.append(f"- **{ship['name']}**{suffixe}")

    if data["total"] > len(data["ships"]):
        lignes.append(f"- … et {_plural(data['total'] - len(data['ships']), 'autre')}")
    if budget:
        lignes.append("\n*Les prix viennent des relevés UEX : un vaisseau non "
                      "coté n'apparaît pas dans un filtre sur le budget.*")
    return "\n".join(lignes)


_RENDERERS["vaisseaux_multi_criteres"] = render_multi_criteres


def render_sans_composant(data: dict[str, Any]) -> str:
    """« 64 vaisseaux sur 287 n'ont pas de jump drive. »

    Un filtre par absence : tous les autres outils répondent « ce qui a ».
    """
    quoi = _COMPOSANT_FR.get(data["type_item"], data["type_item"])
    if not data["ships"]:
        return f"Tous les vaisseaux ont {quoi}."
    lignes = [f"**{_plural(data['total'], 'vaisseau')}** sur {data['sur']} "
              f"n'ont pas {quoi} :", ""]
    lignes += [f"- {s['name']}"
               + (f" — {s['manufacturer_name']}" if s.get("manufacturer_name") else "")
               for s in data["ships"]]
    if data["total"] > len(data["ships"]):
        lignes.append(f"- … et {_plural(data['total'] - len(data['ships']), 'autre')}")
    lignes.append("\n*D'après l'équipement d'origine : un vaisseau qui pourrait "
                  "en recevoir un mais n'en a pas est bien sans.*")
    return "\n".join(lignes)


_COMPOSANT_FR = {
    "JumpDrive": "de jump drive", "QuantumDrive": "de moteur quantique",
    "Shield": "de bouclier", "Cooler": "de refroidisseur",
    "PowerPlant": "de générateur", "Radar": "de radar",
    "Turret": "de tourelle",
}
_RENDERERS["vaisseaux_sans_composant"] = render_sans_composant


def render_par_metier(data: dict[str, Any], montrer: int = 8) -> str:
    """« Quel vaisseau pour le salvage » — par gabarit, avec l'essentiel."""
    lignes = [f"**Les vaisseaux {data['libelle']}** :", ""]
    for v in data["vaisseaux"][:montrer]:
        detail = [role_fr(v.get("role")) or ""]
        if v.get("crew"):
            detail.append(_plural(int(v["crew"]), "place"))
        if v.get("cargo_scu"):
            detail.append(f"{_nombre(v['cargo_scu'])} SCU")
        lignes.append(f"- **{v['name']}** — "
                      + ", ".join(d for d in detail if d))
    # **Une troncature s'annonce** — ligne 4 de la grille : couper à huit en
    # silence laissait croire que le métier n'avait que huit vaisseaux.
    reste = len(data["vaisseaux"]) - montrer
    if reste > 0:
        lignes.append(f"- … et {_plural(reste, 'autre')}")
    return "\n".join(lignes)


def render_par_metier_complet(data: dict[str, Any]) -> str:
    """« La liste entière » — tous les vaisseaux du métier."""
    return render_par_metier(data, montrer=len(data["vaisseaux"]))


_RENDERERS["vaisseaux_par_metier"] = render_par_metier


_FAMILLE_FR = {
    "Shield": "bouclier", "Cooler": "refroidisseur",
    "PowerPlant": "générateur", "Radar": "radar",
    "QuantumDrive": "moteur quantique", "JumpDrive": "jump drive",
    "LifeSupportGenerator": "support de vie", "FuelIntake": "captation",
    "FuelTank": "réservoir", "WeaponGun": "arme", "Turret": "tourelle",
}


def render_marges_d_upgrade(data: dict) -> str:
    """« En quoi je peux upgrade mon Polaris ? »

    **La marge est dans le grade, pas dans la taille** : CIG remplit les
    emplacements à la taille, et chercher un port sous-occupé donne zéro.
    Ce qui varie est la lettre — 296 vaisseaux sur 316 portent au moins
    un composant qu'un meilleur grade peut remplacer.

    Le Polaris n'en fait pas partie, et c'est une **réponse** : aucun
    jump drive S3 de meilleur grade n'existe au catalogue. Le dire vaut
    mieux que de laisser croire à un oubli.
    """
    nom = data["nom"]
    postes = data.get("postes") or []
    if not postes:
        return (f"**{nom}** est déjà au mieux de ce que le catalogue "
                "propose : pour chacun de ses composants, il n'existe pas "
                "de meilleur grade à la même taille. Ce n'est pas une "
                "lacune de mes données — c'est le catalogue du jeu.")

    lignes = [f"**{nom}** — {len(postes)} famille"
              f"{'s' if len(postes) > 1 else ''} à améliorer :", ""]
    for poste in postes:
        famille = _FAMILLE_FR.get(poste["type"], poste["type"])
        combien = (f" ×{poste['emplacements']}"
                   if poste["emplacements"] > 1 else "")
        candidats = ", ".join(
            f"{c['nom']} ({c['grade']})" for c in poste["candidats"][:2])
        lignes.append(
            f"- **{famille}** S{poste['taille']}{combien} — monté en grade "
            f"{poste['grade']} ({poste['monte']}) → {candidats}")
    lignes += ["", "*Le grade A est le sommet de l'échelle. Seuls les "
                   "composants de la même taille sont proposés : un autre "
                   "format ne rentre pas dans le port.*"]
    return "\n".join(lignes)


_RENDERERS["marges_d_upgrade"] = render_marges_d_upgrade
