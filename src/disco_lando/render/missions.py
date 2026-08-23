"""Missions : fiches, groupes, payes et progression."""

from __future__ import annotations

from typing import Any

from .socle import (
    _RENDERERS,
    _nombre,
    _plural,
    _uec,
    enumerate_fr,
    speakable_title,
)


def render_mission_group(groupe: dict[str, Any]) -> str:
    """Un groupe de missions, dit comme un joueur le dirait.

    « les missions de patrouille Foxwell Enforcement à Pyro, du rang Neutral
    au rang Head Contractor » — plutôt que six titres qui disent la même chose
    en moins clair.
    """
    org = groupe.get("mission_giver") or "une organisation inconnue"
    morceaux = ["les missions"]
    if groupe.get("activity"):
        morceaux.append(f"de {groupe['activity']}")
    morceaux.append(org)
    if groupe.get("system"):
        morceaux.append(f"à {groupe['system']}")
    texte = " ".join(morceaux)

    rang_min, rang_max = groupe.get("rank_min"), groupe.get("rank_max")
    if rang_min and rang_max and rang_min != rang_max:
        texte += f", du rang {rang_min} au rang {rang_max}"
    elif rang_min:
        texte += f", à partir du rang {rang_min}"

    # Le groupe ne couvre qu'une partie des missions de cette org dans ce
    # système : le dire, plutôt que laisser croire que n'importe laquelle fait
    # l'affaire.
    if not groupe.get("complete") and groupe.get("mission_count"):
        texte += f" ({_plural(groupe['mission_count'], 'mission')} concernée"
        texte += "s)" if groupe["mission_count"] > 1 else ")"
    return texte


def render_mission_groups(groupes: list[dict[str, Any]], limit: int = 3) -> str:
    return enumerate_fr([render_mission_group(g) for g in groupes[:limit]], limit=limit)


def _activite(mission: dict[str, Any]) -> str | None:
    """L'activité en français, lue dans la famille du contrat.

    Import tardif : `queries` ne connaît pas `render`, et l'inverse ne doit
    pas devenir vrai au chargement du module.
    """
    from ..queries import _activity

    return _activity(mission.get("family"))


def _rangs_ordonnes(missions: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    """Titres distincts par rang, du plus bas au plus haut.

    Deux missions du même titre reviennent à des rangs différents — c'est le
    jeu qui rejoue le même contrat plus haut dans la progression, pas un
    doublon d'ingestion. On dédoublonne **dans** un rang, jamais entre eux.
    """
    par_rang: dict[tuple[int, str], set[str]] = {}
    for m in missions:
        cle = (m.get("min_standing_value") if m.get("min_standing_value") is not None
               else (m.get("rank_index") or 0),
               m.get("min_standing_name") or "rang inconnu")
        titre = speakable_title(m.get("title"))
        if titre:
            par_rang.setdefault(cle, set()).add(titre)
    return [(cle[1], sorted(titres)) for cle, titres in sorted(par_rang.items())]

# Les six paliers du jeu, en français. La valeur reste anglaise en base — les
# noms de champs amont ne se traduisent pas, seul le rendu le fait.
_DIFFICULTE = {
    "VeryEasy": "très facile", "Easy": "facile", "Medium": "moyenne",
    "Hard": "difficile", "VeryHard": "très difficile", "Super": "extrême",
}


def duree(secondes: float | None) -> str | None:
    """Un délai dit comme on le lit : « 5 minutes », « 2 heures ».

    Les valeurs vont de 1 s à 7 200 s ; « 7 200 secondes » ne se retient pas,
    « 2 heures » si.
    """
    if not secondes:
        return None
    if secondes >= 3600:
        heures = round(secondes / 3600, 1)
        # « 2.0 heures » se lit comme une erreur d'affichage.
        return _plural(int(heures) if heures == int(heures) else heures, "heure")
    if secondes >= 60:
        return _plural(round(secondes / 60), "minute")
    return _plural(int(secondes), "seconde")


def render_mission(data: dict[str, Any]) -> str:
    contract = data["contract"]
    titre = speakable_title(contract["title"]) or contract["debug_name"]
    # « Que donne la mission X en blueprint » — la question ne veut que ça.
    # Mesuré sur « Secure Site » : aucun pool de récompense, et c'est la
    # progression chez le commanditaire qui débloque — le dire est la
    # réponse, se taire laisserait croire à un trou dans les données.
    if data.get("volet") == "blueprints":
        qui = ", ".join(p for p in (contract["mission_giver"],
                                    contract["system"]) if p)
        tete = f"**{titre}**" + (f" ({qui})" if qui else "")
        if data.get("blueprints"):
            noms = enumerate_fr(data["blueprints"], limit=6)
            return (f"{tete} peut donner "
                    f"{_plural(len(data['blueprints']), 'blueprint')} : {noms}.")
        texte = f"{tete} ne distribue aucun blueprint en récompense directe."
        if data.get("groupe_blueprints") and contract["mission_giver"]:
            ou = f" à {contract['system']}" if contract["system"] else ""
            texte += (f" C'est la progression chez {contract['mission_giver']} "
                      f"qui les débloque — "
                      f"{_plural(data['groupe_blueprints'], 'blueprint')}{ou}. "
                      f"Tu veux la liste ?")
        return texte
    phrases = [titre]
    if contract["mission_type"]:
        phrases.append(f"mission {contract['mission_type']}")
    if contract["mission_giver"]:
        phrases.append(f"donnée par {contract['mission_giver']}")
    if contract["system"]:
        phrases.append(f"à {contract['system']}")
    texte = ", ".join(phrases)

    if data["prerequisites"]:
        exigences = [
            f"{p['min_standing_name']} chez {p['faction_name']}"
            for p in data["prerequisites"] if p["min_standing_name"]
        ]
        texte += f". Réputation requise : {enumerate_fr(exigences)}"
    else:
        texte += ". Aucune réputation requise"

    if data["gained"]:
        # Une même faction peut apparaître plusieurs fois, sur des portées de
        # réputation différentes. On somme plutôt que de répéter le nom.
        par_faction: dict[str, int] = {}
        for g in data["gained"]:
            if g["amount"]:
                par_faction[g["faction_name"]] = (
                    par_faction.get(g["faction_name"], 0) + g["amount"]
                )
        gains = [f"{montant} points chez {faction}"
                 for faction, montant in par_faction.items()]
        if gains:
            texte += f". Elle rapporte {enumerate_fr(gains, limit=3)}"

    if contract.get("reward_uec"):
        texte += f". Elle paye {contract['reward_uec']} aUEC"
    elif contract.get("reward_calculated"):
        texte += ". La récompense est calculée selon la difficulté"

    groupe = data.get("group")
    if groupe and groupe.get("contract_count", 0) > 1:
        texte += (f". C'est une des {groupe['contract_count']} missions "
                  f"{groupe['mission_giver']}"
                  + (f" à {groupe['system']}" if groupe["system"] else ""))
        if groupe.get("max_standing_name") and groupe["max_standing_name"] != \
                groupe.get("min_standing_name"):
            texte += (f", dont les rangs vont de {groupe['min_standing_name']} "
                      f"à {groupe['max_standing_name']}")

    # Colonnes déjà en base et qu'aucun rendu ne lisait (point 4 de l'audit).
    # Elles ne décrivent pas la mission, elles disent **comment on la joue** :
    # seul ou à plusieurs, une fois ou en boucle, chronométré ou non.
    pratique = []
    if contract.get("difficulty_label"):
        pratique.append("difficulté " + _DIFFICULTE.get(
            contract["difficulty_label"], contract["difficulty_label"]))
    if contract.get("deadline_seconds"):
        pratique.append(f"chronométrée, {duree(contract['deadline_seconds'])}")
    if contract.get("shareable"):
        pratique.append("partageable en groupe")
    if contract.get("once_only"):
        pratique.append("jouable une seule fois")
    if pratique:
        texte += ". " + enumerate_fr(pratique, limit=4).capitalize()

    # Les chaînes de missions : 4 262 liens en base et jamais lus. C'est la
    # réponse à « pourquoi je ne vois pas cette mission ».
    if data.get("prealables"):
        texte += (". Il faut d'abord avoir fait "
                  + enumerate_fr(data["prealables"], limit=3))
    if data.get("debloque"):
        texte += ". Elle débloque " + enumerate_fr(data["debloque"], limit=3)

    if contract["not_for_release"] or contract["work_in_progress"]:
        texte += ". Attention, ce contenu n'est pas encore sorti"
    return texte + "."


def render_missions_payantes(data: dict[str, Any]) -> str:
    """Les missions classées par ce qu'elles rapportent.

    Les titres sont des gabarits : le serveur y insère la cible à la
    génération. On les élague, et on garde le montant, qui lui est ferme.
    """
    missions = data["missions"]
    # « Combien … rapportent plus de X » : le compte mène, le classement
    # illustre — et le compte égale la liste filtrée des titres affichés.
    if data.get("plancher") is not None and data.get("total_seuil") is not None:
        ou = f" dans {data['system']}" if data.get("system") else ""
        seuil = (f"plus de {_uec(data['plancher'])}"
                 if data.get("plancher_strict")
                 else f"{_uec(data['plancher'])} ou plus")
        if not missions:
            texte = f"**Aucune mission** ne rapporte {seuil}{ou}."
            meilleure = data.get("meilleure_sous_plancher")
            if meilleure:
                texte += (f" La mieux payée{ou} est "
                          f"{speakable_title(meilleure['title'])}, à "
                          f"{_uec(meilleure['montant'])}.")
            return texte
        tete = (f"**{_nombre(data['total_seuil'])} missions** rapportent "
                f"{seuil}{ou}")
        lignes = [f"- {speakable_title(m['title'])} — {_uec(m['montant'])}"
                  for m in missions[:5]]
        return (tete + ". Les mieux payées :\n" + "\n".join(lignes))
    if not missions and data.get("sans_montant"):
        # Le dire plutôt que se taire : l'organisation existe, ses missions
        # aussi, c'est le montant qui n'est pas fixé à l'avance.
        qui = data.get("org") or f"dans {data['system']}"
        calculees = data.get("calculees") or 0
        cause = ("leur récompense est calculée à la génération du contrat"
                 if calculees else "aucune ne porte de montant en base")
        return (f"{_plural(data['sans_montant'], 'mission')} pour "
                f"**{qui}**, mais {cause} — je ne peux pas les classer par "
                "ce qu'elles rapportent.")
    # Deux monnaies : « ça paye combien » et « ça fait monter ma réputation de
    # combien » ne classent pas les mêmes missions.
    reput = data.get("critere") == "reputation"
    diff = data.get("difficulte")
    quoi = ("missions qui rapportent le plus de réputation" if reput
            else "missions les mieux payées")
    if diff:
        quoi = f"missions {diff}s les mieux payées"
    if data.get("activite"):
        # « De combat », « de transport » : la contrainte lue s'annonce.
        quoi = quoi.replace("missions", f"missions de {data['activite']}", 1)
    tete = (f"**Les {quoi}**"
            + (f" chez {data['org']}" if data.get("org") else "")
            + (f" dans {data['system']}" if data.get("system") else "") + " :")
    lignes = [tete]
    if diff:
        # Le filtre vient de l'étiquette du jeu, et elle ne couvre pas tout :
        # sans cette ligne, le lecteur croirait que les autres missions sont
        # dures — elles sont juste non étiquetées.
        lignes.append(f"_(« {diff} » d'après l'étiquette de difficulté des "
                      "fichiers du jeu — la plupart des contrats n'en portent "
                      "pas, le classement se fait parmi les étiquetés)_")
    if data.get("elargi_au_systeme"):
        # Dire la granularité plutôt que laisser croire à un filtre à la lune
        # près : les contrats payés ne portent aucun lieu plus fin.
        lignes.append(f"_(les contrats ne sont situés qu'au système, pas à "
                      f"{data['lieu']} près)_")
    for mission in missions:
        titre = speakable_title(mission.get("title")) or "(sans titre)"
        montant = mission.get("montant")
        detail = [f"**{_nombre(montant)} points**" if reput
                  else f"**{_uec(montant)}**", titre]
        if reput and mission.get("faction_name"):
            detail.append(f"chez {mission['faction_name']}")
        if mission.get("mission_giver") and not data.get("org"):
            detail.append(mission["mission_giver"])
        # « Il manque le type de mission » — remarque de l'utilisateur : la
        # ligne dit désormais ce qu'on y fait, et la difficulté quand le jeu
        # l'étiquette.
        if mission.get("mission_type") and not data.get("activite"):
            detail.append(_type_de_mission(mission["mission_type"]))
        if mission.get("difficulty_label") and not diff:
            etiquette = _DIFFICULTE_FR.get(mission["difficulty_label"])
            if etiquette:
                detail.append(etiquette)
        if mission.get("min_standing_name"):
            detail.append(f"rang {mission['min_standing_name']}")
        lignes.append("- " + " — ".join(detail))
    return "\n".join(lignes)


# Le type d'un contrat, en français court. Les valeurs viennent du vocabulaire
# fermé `mission_type` (30 valeurs mesurées) ; ce qui n'y est pas s'affiche
# tel quel — un type nouveau de la 4.10 doit se voir, pas se cacher.
_MISSION_TYPE_COURT = {
    "Mercenary": "mercenariat", "Bounty Hunter": "chasse à la prime",
    "Hauling": "transport", "Hauling - Planetary": "transport planétaire",
    "Hauling - Stellar": "transport stellaire",
    "Hauling - Interstellar": "transport interstellaire",
    "Hauling - Local": "transport local",
    "Delivery": "livraison", "Courier": "coursier",
    "Investigation": "enquête", "Priority": "priorité", "Salvage": "récupération",
    "Ship Mining": "minage en vaisseau",
    "Ground Vehicle Mining": "minage en véhicule",
    "Hand Mining": "minage à la main", "Mining": "minage",
    "Refueling": "ravitaillement", "Maintenance": "maintenance",
    "Racing": "course", "Collection": "collecte", "Search": "recherche",
    "Service Beacons": "balise de service", "Appointment": "rendez-vous",
    "Research": "recherche", "PvP Missions": "JcJ",
}

_DIFFICULTE_FR = {"VeryEasy": "très facile", "Easy": "facile",
                  "Medium": "moyenne", "Hard": "difficile",
                  "VeryHard": "très difficile", "Super": "extrême"}


def _type_de_mission(brut: str) -> str:
    return _MISSION_TYPE_COURT.get(brut, brut)


def render_panorama_missions(data: dict[str, Any]) -> str:
    """« Donne-moi les missions de Pyro » — le menu à deux entrées.

    Huit cents titres ne se lisent pas : on demande par où entrer, avec des
    exemples chiffrés, et les volets « tous les types » / « tous les
    donneurs » déroulent la liste complète de l'entrée choisie.
    """
    systeme = data["systeme"]
    if data.get("volet") == "types":
        lignes = [f"**Les types de missions dans {systeme}** — "
                  f"{_plural(data['total'], 'mission')} distinctes :", ""]
        for t in data["par_type"]:
            lignes.append(f"- **{_type_de_mission(t['type'])}** — "
                          f"{_plural(t['n'], 'mission')}")
        lignes += ["", f"Demande « les missions de <type> à {systeme} » "
                   "pour la liste."]
        return "\n".join(lignes)
    if data.get("volet") == "donneurs":
        lignes = [f"**Les donneurs de missions dans {systeme}** :", ""]
        for d in data["par_donneur"]:
            lignes.append(f"- **{d['org']}** — {_plural(d['n'], 'mission')}")
        lignes += ["", "Demande « les missions <donneur> » pour sa vue "
                   "d'ensemble."]
        return "\n".join(lignes)

    types = enumerate_fr(
        [f"{_type_de_mission(t['type'])} ({t['n']})"
         for t in data["par_type"][:4]], limit=4)
    donneurs = enumerate_fr(
        [f"{d['org']} ({d['n']})" for d in data["par_donneur"][:4]], limit=4)
    return (f"**{_plural(data['total'], 'mission')} distinctes dans "
            f"{systeme}.** Par où veux-tu entrer ?\n\n"
            f"- **Par type** — {types}…\n"
            f"- **Par donneur** — {donneurs}…\n\n"
            "Dis « tous les types », « tous les donneurs », un type "
            f"(« les missions de minage à {systeme} ») ou un donneur "
            "(« les missions XenoThreat »).")


def render_missions_du_site(data: dict[str, Any]) -> str:
    """« Quelles missions se passent à Onyx » — la liste, par commanditaire.

    Pas de classement : aucune de ces missions ne porte de montant fixe, et
    l'annoncer vaut mieux que de laisser croire à un oubli.
    """
    missions = data["missions"]
    lignes = [f"**{_plural(len(missions), 'mission')}** se "
              f"{'passe' if len(missions) == 1 else 'passent'} "
              f"dans les installations **{data['site']}** :", ""]
    par_org: dict[str, list] = {}
    for m in missions:
        par_org.setdefault(m.get("mission_giver") or "commanditaire inconnu",
                           []).append(m)
    for org, groupe in par_org.items():
        if len(par_org) > 1:
            lignes.append(f"*{org}*")
        for m in groupe:
            titre = speakable_title(m.get("title")) or "(sans titre)"
            lignes.append(f"- {titre}")
        if len(par_org) > 1:
            lignes.append("")
    if not data.get("payes_fixes") and data.get("calculees"):
        lignes.append("_Leur récompense est calculée à la génération du "
                      "contrat — aucun montant fixe à annoncer._")
    return "\n".join(lignes).rstrip()


# Le libellé français d'un `mission_type` précis, pour distinguer les trois
# minages. Défini ici plutôt qu'importé : `render` ne remonte pas vers les
# modules métier — même règle que `_LIBELLE_EMPORT`. Une chaîne vide veut
# dire « rien à préciser » : « Hauling » tout court rendait « *Transport
# Hauling* », moitié français moitié clé brute.
_TYPE_DE_MISSION_FR = {
    "Ship Mining": "en vaisseau", "Ground Vehicle Mining": "en véhicule",
    "Hand Mining": "à la main", "Mining": "",
    "Hauling": "", "Hauling - Planetary": "planétaire",
    "Hauling - Stellar": "stellaire",
    "Hauling - Interstellar": "interstellaire", "Hauling - Local": "local",
}


def render_missions_par_activite(data: dict[str, Any]) -> str:
    """« Les missions de minage à Stanton » — par type, puis par titre.

    Le jeu distingue le minage en vaisseau, en véhicule et à la main : les
    mélanger ferait croire qu'un Prospector sert aux trois.
    """
    ou = f" dans {data['system']}" if data.get("system") else ""
    lignes = [f"**{_plural(data['total'], 'mission')} de "
              f"{data['activite']}**{ou} :", ""]
    plusieurs = len(data["par_type"]) > 1
    for type_brut, missions in data["par_type"].items():
        if plusieurs:
            precision = _TYPE_DE_MISSION_FR.get(type_brut, type_brut)
            titre = f"{data['activite'].capitalize()} {precision}".strip()
            lignes.append(f"*{titre}*")
        for m in missions:
            titre = speakable_title(m.get("title")) or "(sans titre)"
            detail = f"- {titre}"
            if m.get("mission_giver"):
                detail += f" — {m['mission_giver']}"
            if not data.get("system") and m.get("system"):
                detail += f" ({m['system']})"
            lignes.append(detail)
        if plusieurs:
            lignes.append("")
    lignes.append("Demande la fiche d'une mission pour ses prérequis, sa "
                  "paye et où la prendre.")
    return "\n".join(lignes).rstrip()


def render_group(data: dict[str, Any]) -> str:
    """« Les missions Foxwell Enforcement à Pyro » — la vue d'ensemble."""
    # `volet="blueprints"` : « donne moi les blueprint » après la vue
    # d'ensemble. La liste entière, par rang — remarque de l'utilisateur, la
    # vue d'ensemble demande d'abord si on la veut.
    if data.get("volet") == "blueprints":
        return _render_group_blueprints(data)
    phrases = []
    for groupe in data["groups"][:2]:
        lieu = f" à {groupe['system']}" if groupe["system"] else ""
        texte = (f"{groupe['mission_giver']}{lieu} propose "
                 f"{_plural(groupe['contract_count'], 'mission')}")

        activites = [a["activity"] for a in groupe["activities"] if a["activity"]]
        if activites:
            uniques = list(dict.fromkeys(activites))
            texte += f", de type {enumerate_fr(uniques, limit=4)}"

        paliers = [p["nom"] for p in groupe["ranks"]]
        if len(paliers) > 1:
            texte += f". Les rangs vont de {paliers[0]} à {paliers[-1]}"
            texte += f", soit {_plural(len(paliers), 'palier')}"
        elif paliers:
            texte += f". Rang requis : {paliers[0]}"

        # La paye, là où le joueur choisit. Elle était en base depuis le
        # premier jour et n'apparaissait que sur une mission nommée.
        paye = groupe.get("paye")
        if paye and paye.get("n"):
            if paye["mini"] == paye["maxi"]:
                texte += f". Elles payent {_uec(paye['mini'])}"
            else:
                texte += (f". La paye va de {_uec(paye['mini'])} à "
                          f"{_uec(paye['maxi'])}, {_uec(paye['moyenne'])} en moyenne")

        # Le compte, pas les noms : « me demander si je veux les blueprint »
        # — remarque de l'utilisateur. Quatre noms suivis d'« et 3 autres »
        # n'étaient ni la liste ni la question.
        if groupe["blueprints"]:
            texte += (f". La progression débloque "
                      f"{_plural(len(groupe['blueprints']), 'blueprint')}")
        phrases.append(texte)
    corps = ". ".join(phrases) + "."
    if any(g["blueprints"] for g in data["groups"][:2]):
        corps += " Tu veux la liste des blueprints ?"
    return corps


def _render_group_blueprints(data: dict[str, Any]) -> str:
    """La liste entière des blueprints d'un groupe, chacun à son rang."""
    blocs = []
    for groupe in data["groups"][:2]:
        if not groupe["blueprints"]:
            continue
        lieu = f" à {groupe['system']}" if groupe["system"] else ""
        lignes = [f"**{_plural(len(groupe['blueprints']), 'blueprint')}** "
                  f"dans la progression {groupe['mission_giver']}{lieu} :", ""]
        # Le rang se lit dans les paliers du groupe : `rang_mini` est la
        # valeur de réputation, le palier porte son nom lisible.
        rangs = {p["valeur"]: p["nom"] for p in groupe["ranks"]}
        for bp in groupe["blueprints"]:
            rang = rangs.get(bp.get("rang_mini"))
            suffixe = f" — au rang {rang}" if rang else ""
            lignes.append(f"- **{bp['output_name']}**{suffixe}")
        blocs.append("\n".join(lignes))
    if not blocs:
        org = data.get("org") or "cette organisation"
        return f"La progression chez {org} ne débloque aucun blueprint."
    return "\n\n".join(blocs)


def render_progression(data: dict[str, Any]) -> str:
    """L'échelle des rangs d'une organisation, et ce que chacun ouvre.

    Une échelle se lit **verticalement** et dans l'ordre : à plat, dix rangs
    se lisent comme dix conditions cumulées au lieu de dix étapes. Même règle
    que pour les chaînes de missions.
    """
    org = data.get("org") or "cette organisation"
    echelle = data.get("echelle") or []
    if not echelle:
        return f"Je n'ai pas d'échelle de réputation pour **{org}**."

    ou = f" dans {data['systeme']}" if data.get("systeme") else ""
    lignes = [f"**Progression chez {org}**{ou} — "
              f"{_plural(len(echelle), 'palier')}, "
              f"{_plural(data['total_missions'], 'mission')}, "
              f"{_plural(data['total_blueprints'], 'blueprint')} à la clé.", ""]

    for rang in echelle:
        # **Le seuil était calculé et jamais rendu.** « Jr. Contractor »
        # sans son chiffre ne dit pas l'effort : 800 points et 38 000 sont
        # deux mondes, et c'est exactement ce qu'un joueur vient chercher.
        # Le rang de départ vaut 0 — on ne l'affiche pas, « à partir de 0 »
        # est du bruit.
        seuil = rang.get("valeur")
        texte = f"- **{rang['rang']}**"
        if seuil:
            texte += f" — à partir de {_nombre(seuil)} points"
        texte += f" — {_plural(rang['missions'], 'mission')}"
        # « Par quoi je monte » est la question posée : un compte de
        # missions n'y répond pas, un titre de contrat oui. Un contrat déjà
        # nommé par sa récompense en vaisseau ne se répète pas — « par ex.
        # Extra Special Job · Drake Golem via Extra Special Job » dit deux
        # fois la même chose.
        nommes = {g["contrat"] for g in (rang.get("payent_un_vaisseau") or [])}
        exemples = [c for c in (rang.get("contrats") or []) if c not in nommes]
        if exemples:
            texte += " · par ex. " + enumerate_fr(exemples, limit=3)
        # **Un vaisseau en récompense passe devant tout le reste.** C'est
        # ce qu'un joueur vient chercher à ces paliers, et le compte de
        # missions le noyait : « Prestige 3 — 1 mission » ne dit pas qu'on
        # y gagne un ARGO MOLE.
        for gain in rang.get("payent_un_vaisseau") or []:
            texte += f" · **{gain['vaisseau']}** via « {gain['contrat']} »"
        if rang["blueprints"]:
            texte += (f" · débloque {_plural(len(rang['blueprints']), 'blueprint')} : "
                      + enumerate_fr(rang["blueprints"], limit=4))
        lignes.append(texte)

    # Un palier sans blueprint n'est pas un palier inutile : il fait monter la
    # réputation vers le suivant. Le dire évite de le faire passer pour un
    # trou dans les données.
    if any(not r["blueprints"] for r in echelle):
        lignes.append("\n*Les paliers sans blueprint font monter la réputation "
                      "vers ceux qui en donnent.*")
    return "\n".join(lignes)
_RENDERERS["progression_dans"] = render_progression
