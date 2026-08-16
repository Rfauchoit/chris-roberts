"""Minerais et raffinage : gisements, raffineries, méthodes."""

from __future__ import annotations

import re
from typing import Any

from .socle import (
    _DEPOSIT,
    _RARETE,
    _RENDERERS,
    _de,
    _nombre,
    _plural,
    _uec,
    deposit_label,
    enumerate_fr,
    provenance_composee,
)


def render_rentabilite_minage(data: dict[str, Any], montrer: int = 10) -> str:
    """« Quel minerai rapporte le plus ? » — le prix, et où aller le chercher.

    La règle de classement s'annonce (grille, ligne 8) : le prix est celui du
    minerai **raffiné**, les bruts n'étant presque jamais cotés.
    """
    ou = f" dans {data['systeme']}" if data.get("systeme") else ""
    lignes = [f"**Les minerais qui rapportent le plus{ou}** — classés au "
              "prix de vente du minerai **raffiné**, relevé par les joueurs "
              "sur UEX :", ""]
    for ligne in data["classement"][:montrer]:
        lignes.append(
            f"- **{ligne['nom']}** ({ligne['rarete']}) : "
            f"**{_uec(ligne['prix'])}** · "
            f"{_plural(ligne['gisements'], 'gisement')} dans "
            f"{enumerate_fr(ligne['systemes'], limit=3)}")
    reste = data["total"] - min(montrer, len(data["classement"]))
    if reste > 0:
        lignes.append(f"- … et {_plural(reste, 'autre')}")
    if data.get("sans_cote"):
        noms = enumerate_fr([l["nom"] for l in data["sans_cote"]], limit=5)
        lignes += ["", f"Sans cotation de vente : {noms} — ils ne se vendent "
                   "à aucun terminal relevé, leur valeur est dans la "
                   "fabrication."]
    lignes += ["", "_Prix UEX du minerai raffiné, cotés en global — pas par "
               "terminal. La rareté du gisement dit le temps de recherche : "
               "demande « où miner » l'un d'eux pour le plan._"]
    return "\n".join(lignes) + provenance_composee(data)


def render_rentabilite_minage_complet(data: dict[str, Any]) -> str:
    """« La liste entière » — tous les minerais cotés."""
    return render_rentabilite_minage(
        dict(data, classement=data.get("toutes") or data["classement"]),
        montrer=len(data.get("toutes") or data["classement"]))


_RENDERERS["rentabilite_minage"] = render_rentabilite_minage


def render_ou_miner_pour(data: dict[str, Any]) -> str:
    """« Où miner pour fabriquer un P6-LR » — le coin d'abord, le détail après.

    Remarque de l'utilisateur : trois listes de gisements séparées lui
    laissaient l'interpolation à faire. On recommande le système planétaire
    qui couvre le plus de minerais de la recette — le plus rare commande —
    et on dit ce qui manque au coin et où le prendre.
    """
    minerais = data["minerais"]
    besoins = ", ".join(f"**{m['nom']}** ({m['rarete']})" for m in minerais)
    ou = f" dans {data['systeme']}" if data.get("systeme") else ""
    if data.get("sortie"):
        tete = f"Pour fabriquer **{data['sortie']}**, il faut : {besoins}."
    else:
        # Pas de recette : le joueur a nommé les minerais lui-même — « où
        # miner de l'iron et de l'héphaestanite » — et veut le même plan.
        tete = f"Miner {besoins} ensemble{ou} :"
    lignes = [tete, ""]

    coins = data.get("coins") or []
    if not coins:
        lignes.append("Aucun de ces minerais n'a de gisement de surface "
                      "localisé — voir les champs d'astéroïdes ci-dessous.")
    else:
        meilleur = coins[0]
        compte = len(meilleur["minerais"])
        de_quoi = "de la recette " if data.get("sortie") else ""
        verbe = "s'y trouve" if compte == 1 else "s'y trouvent"
        lignes.append(f"**Le meilleur coin : autour de {meilleur['planete']} "
                      f"({meilleur['systeme']})** — "
                      f"{_plural(compte, 'minerai')} {de_quoi}{verbe} :")
        for nom, lieux in meilleur["minerais"].items():
            ou = enumerate_fr([l if l != meilleur["planete"]
                               else f"{l} même" for l in lieux], limit=4)
            lignes.append(f"- **{nom}** — {ou}")
        if data.get("commande") and data["commande"] in meilleur["minerais"]:
            # « Le plus rare » ne se dit que s'il y a un plus rare : Iron et
            # Hephaestanite sont tous deux communs, et la phrase mentait.
            if len({m["palier"] for m in minerais}) > 1:
                lignes.append(f"\n_C'est {data['commande']}, le plus rare, "
                              f"qui commande le choix — le reste se trouve "
                              f"plus facilement._")
            elif len(minerais) > 1:
                lignes.append("\n_À rareté égale, le coin qui couvre le plus "
                              "de minerais gagne._")
        manquants = [m["nom"] for m in minerais
                     if m["nom"] not in meilleur["minerais"]
                     and m["nom"] not in (data.get("sans_lieu") or [])]
        for nom in manquants:
            # Où le prendre à défaut : un autre coin, ou les astéroïdes.
            autre = next((c for c in coins[1:] if nom in c["minerais"]), None)
            if autre:
                lignes.append(f"- **{nom}** n'y est pas — le plus simple : "
                              f"{autre['planete']} ({autre['systeme']}).")
            elif nom in (data.get("asteroides") or {}):
                pars = data["asteroides"][nom]
                lignes.append(f"- **{nom}** n'y est pas — il se mine en "
                              f"astéroïdes ({enumerate_fr(sorted(pars), limit=3)}).")

    if data.get("asteroides"):
        pars = sorted({s for d in data["asteroides"].values() for s in d})
        noms = [n for n in data["asteroides"]]
        lignes += ["", f"Les champs d'astéroïdes ({enumerate_fr(pars, limit=3)}) "
                   f"portent aussi {enumerate_fr(noms, limit=4)}."]
    if data.get("sans_lieu"):
        lignes += ["", "Sans gisement localisé dans les fichiers : "
                   + enumerate_fr(data["sans_lieu"], limit=4) + "."]

    lignes += ["", "Tu veux savoir où raffiner tout ça ?"]
    return "\n".join(lignes) + provenance_composee(data)


_RENDERERS["ou_miner_pour"] = render_ou_miner_pour


def render_ou_miner(data: dict[str, Any]) -> str:
    """« Où miner de l'or » — les filons dédiés, puis ceux où il est en prime.

    Les deux familles se séparent parce qu'elles répondent à deux stratégies :
    on va **chercher** un filon dédié, on **récolte** le reste en passant. Les
    mélanger dans un seul classement ferait passer un appoint de 3 % pour une
    destination.
    """
    minerai = data.get("minerai") or "ce minerai"
    filons = data.get("filons") or []
    if not filons:
        return f"Je ne connais pas de filon contenant du {minerai}."

    dedies = [f for f in filons if f["dedie"]]
    primes = [f for f in filons if not f["dedie"]]
    # « À Stanton » a restreint la liste : le dire — ligne 2 de la grille.
    ou = f" dans {data['systeme']}" if data.get("systeme") else ""
    lignes = [f"**{minerai}** — {_plural(len(filons), 'filon')} localisé"
              + ("s" if len(filons) > 1 else "") + f"{ou} :"]

    def _ligne(filon: dict) -> str:
        systemes = ", ".join(filon["systemes"]) or "système inconnu"
        teneur = f"{_nombre(filon['min_pct'])} à {_nombre(filon['max_pct'])} %"
        # La probabilité ne s'affiche que si elle discrimine : à 100 % elle
        # n'apprend rien et alourdit chaque ligne.
        chance = ("" if (filon["probability"] or 0) >= 1
                  else f", {_nombre(100 * filon['probability'])} % du temps")
        return (f"- **{_nom_de_gisement(filon['gisement'])}** — {teneur}"
                f"{chance} · {_plural(filon['lieux'], 'lieu', 'lieux')}"
                f" dans {systemes}")

    if dedies:
        lignes += [""] + [_ligne(f) for f in dedies]
    if primes:
        lignes += ["", "**En prime dans d'autres filons** — moins concentré, "
                       "mais c'est autant de trajets évités :"]
        lignes += [_ligne(f) for f in primes]

    manquants = data.get("sans_lieu") or 0
    if manquants:
        # Dire ce qu'on ne sait pas situer : le joueur comprend pourquoi la
        # liste est courte au lieu de croire qu'il n'y a que ça. `_plural`
        # mettait le « s » en fin de **phrase** — « autre filon en
        # contients » — le verbe se conjugue à la main.
        phrase = ("Un autre filon en contient" if manquants == 1
                  else f"{manquants} autres filons en contiennent")
        lignes.append(f"\n*{phrase} aussi, sans lieu connu dans les "
                      "fichiers du jeu.*")
    return "\n".join(lignes) + provenance_composee(data)


def _nom_de_gisement(nom: str | None) -> str:
    """« MineableRock_AsteroidRare_Gold » → « astéroïde rare, Gold ».

    Le nom brut est un identifiant : le montrer tel quel oblige le lecteur à
    le décoder. Ses trois segments portent le contexte, le palier et le
    minerai — c'est déjà la lecture que fait `where_to_find_resource`.
    """
    segments = (nom or "").split("_")
    if len(segments) < 3:
        return nom or "?"
    contexte = "astéroïde" if "asteroid" in segments[1].lower() else "surface"
    palier = re.sub(r"^(asteroid|surface)", "", segments[1], flags=re.I).lower()
    traduits = {"common": "commun", "uncommon": "peu commun", "rare": "rare",
                "epic": "épique", "legendary": "légendaire"}
    return f"{segments[-1]} — {contexte} {traduits.get(palier, palier)}".strip()


def render_resource(data: dict[str, Any]) -> str:
    """Où miner une ressource, dit comme un joueur s'y rendrait.

    Trois partis pris, tous issus d'une remarque du journal — « j'ai du mal à
    comprendre le "87 astéroïdes", où se situe-t-il ? ».

    **Les astéroïdes ne se citent pas.** Mesuré : 292 des 353 gisements sont
    des champs anonymes accrochés à l'étoile — « RMB-ALME », « Akiro
    Cluster » — répartis dans tout le système. Les énumérer n'aide personne :
    ce qui compte est qu'il faut aller miner en ceinture, dans ce système-là.

    **Les corps célestes se citent tous.** Ils sont 53 en tout sur les mêmes
    sept ressources, quatorze au maximum pour un gisement : la liste tient, et
    c'est elle qui envoie le joueur quelque part. On la donne avec sa
    hiérarchie — « Stanton : Crusader — Yela, Daymar » — parce qu'une lune sans
    sa planète ne se trouve pas.

    **La rareté s'explique.** « Légendaire » est le palier du gisement dans les
    fichiers du jeu, pas un adjectif de style : le dire une fois évite de
    laisser un jargon sans réponse.
    """
    # Plusieurs minerais nommés : la donnée est un plan croisé, pas une liste
    # de gisements — même rendu que « où miner pour fabriquer X ».
    if data.get("coins") is not None and data.get("minerais"):
        return render_ou_miner_pour(data)

    deposits = data["deposits"]
    if not deposits:
        if data["trade"]:
            return "Cette commodité ne se mine pas, elle s'achète et se revend."
        if data.get("systeme"):
            return (f"Aucun gisement connu dans {data['systeme']} — cette "
                    "ressource s'y trouve peut-être ailleurs : redemande sans "
                    "le système.")
        return "Aucun gisement connu."

    lignes: list[str] = []
    raretes: set[str] = set()
    for deposit in deposits[:4]:
        nom = deposit_label(deposit["resource"]["name"])
        # Lire la rareté à la source, pas dans le libellé : « commun » est une
        # sous-chaîne de « peu commun », et la phrase d'explication annonçait
        # « commun et peu commun » pour un seul palier.
        brut = _DEPOSIT.match(deposit["resource"]["name"] or "")
        if brut and (r := _RARETE.get((brut.group("rarete") or "").lower())):
            raretes.add(r)
        lieux = deposit["locations"]

        corps = [l for l in lieux
                 if (l.get("type_name") or "").split("_")[0] in ("Planet", "Moon")]
        champs = [l for l in lieux
                  if (l.get("type_name") or "").split("_")[0] == "Asteroid"]
        # **Les bases minières sont un sous-ensemble des astéroïdes**, pas une
        # catégorie à part : mesuré sur le Quantainium, les 50 « Mining Base »
        # *sont* les 50 champs. Les compter deux fois doublait la réponse.
        bases = sum(1 for l in champs if l["name"].startswith("Mining Base"))

        lignes.append(f"**{nom}**")
        for texte in _par_systeme(corps):
            lignes.append(f"  - {texte}")
        if champs:
            systemes = sorted({l["system"] for l in champs if l["system"]})
            ou = f" de {enumerate_fr(systemes, limit=3)}" if systemes else ""
            detail = f"{_plural(len(champs), 'gisement')} répertoriés"
            if bases:
                detail += (f", dont {bases} en base minière de la ceinture "
                           "d'Aaron Halo" if bases < len(champs)
                           else " — les bases minières de la ceinture d'Aaron Halo")
            lignes.append(f"  - dans les champs d'astéroïdes{ou} ({detail})")


    texte = chr(10).join(lignes)
    if data["trade"]:
        vendus = sum(1 for t in data["trade"] if t["direction"] == "sold_at")
        texte += (chr(10) * 2 + "Côté commerce, la commodité s'échange "
                  f"dans {vendus} lieux.")
    if raretes:
        texte += (chr(10) * 2
                  + f"*« {enumerate_fr(sorted(raretes), limit=3)} » est le "
                  "palier de rareté du gisement dans les fichiers du jeu : "
                  "plus il est haut, plus le filon est difficile à croiser.*")
    return texte


def _par_systeme(corps: list[dict[str, Any]]) -> list[str]:
    """« Stanton — planètes : microTech · lunes : Calliope (microTech) ».

    L'ancienne forme mélangeait les deux : « Stanton : microTech ; Hurston —
    Arial ; microTech — Calliope » laissait croire, selon la lecture, que le
    gisement était sur la planète microTech **ou** seulement sur Calliope.
    Remarque du journal — « ça veut dire qu'ils sont sur microTech la planète
    ou juste Calliope ? ».

    On sépare donc les deux natures, et chaque lune porte sa planète.
    """
    par_systeme: dict[str, dict[str, list[str]]] = {}
    for lieu in corps:
        morceaux = [m.strip() for m in (lieu.get("path") or "").split("/") if m.strip()]
        systeme = morceaux[0] if morceaux else (lieu.get("system") or "?")
        est_lune = (lieu.get("type_name") or "").split("_")[0] == "Moon"
        familles = par_systeme.setdefault(systeme, {"planetes": [], "lunes": []})
        if est_lune and len(morceaux) >= 3:
            familles["lunes"].append(f"{lieu['name']} ({morceaux[-2]})")
        elif est_lune:
            familles["lunes"].append(lieu["name"])
        else:
            familles["planetes"].append(lieu["name"])

    sorties = []
    for systeme, familles in sorted(par_systeme.items()):
        groupes = []
        if familles["planetes"]:
            noms = sorted(dict.fromkeys(familles["planetes"]))
            groupes.append(f"{_plural(len(noms), 'planète')} : "
                           + ", ".join(noms))
        if familles["lunes"]:
            noms = sorted(dict.fromkeys(familles["lunes"]))
            groupes.append(f"{_plural(len(noms), 'lune')} : " + ", ".join(noms))
        sorties.append(f"**{systeme}** — " + " · ".join(groupes))
    return sorties


def _situer_raffinerie(raffinerie: dict[str, Any]) -> str:
    """« Checkmate — Checkmate Station (Pyro) ». Un nom seul ne situe personne.

    Le surnom UEX répète souvent le corps qui porte la raffinerie, système
    compris : « Nyx Gateway (Pyro) — Nyx Gateway (Pyro) (Pyro) » se lisait
    trois fois. On ne garde le corps que s'il apprend quelque chose.
    """
    nom = raffinerie.get("nickname") or raffinerie.get("name") or "?"
    systeme = raffinerie.get("star_system")
    corps = next((raffinerie.get(c) for c in
                  ("station", "outpost", "city", "moon", "planet", "orbit")
                  if raffinerie.get(c)), None)
    if corps and nom.lower() in corps.lower():
        corps = None
    if corps and systeme:
        return f"**{nom}** — {corps} ({systeme})"
    return f"**{nom}** — {corps or systeme or '?'}"


def _bloc_raffineries(data: dict[str, Any]) -> list[str]:
    au_rendement = data.get("au_rendement")
    lignes = []
    for raffinerie in data.get("raffineries") or []:
        rendement = raffinerie.get("rendement")
        marque = (f"**{rendement:+.0f} %** — " if rendement is not None else "")
        situe = _situer_raffinerie(raffinerie)
        # L'en-tête annonce déjà le système filtré : pas de répétition.
        if data.get("systeme"):
            situe = situe.replace(f" — {data['systeme']}", "")
        lignes.append(f"- {marque}{situe}")
    # **Le reste n'est pas « d'autres réponses ».** Les 15 raffineries qui ne
    # sortent pas sont dans des systèmes où le minerai ne se trouve pas :
    # les annoncer comme une suite de la liste laissait croire qu'elles
    # feraient l'affaire.
    reste = (data.get("total") or 0) - len(data.get("raffineries") or [])
    if reste > 0:
        lignes.append(f"- … et {_plural(reste, 'autre')}")
    if (ailleurs := data.get("ailleurs") or 0) > 0:
        lignes.append(f"- _({_plural(ailleurs, 'autre')} ailleurs, dans des "
                      "systèmes où ce minerai ne s'extrait pas)_")

    # **Dire sur quoi on classe.** Le rendement est publié pour 24 minerais
    # sur 20 terminaux ; pour les autres il n'y a que la géographie, et les
    # deux tris ne répondent pas à la même question.
    if au_rendement:
        lignes.append("\n*Écart de rendement pour ce minerai, relevé par les "
                      "joueurs sur UEX. Un meilleur rendement peut valoir le "
                      "déplacement : à toi d'arbitrer.*")
    else:
        lignes.append("\n*Aucun rendement n'est relevé pour ce minerai — 24 le "
                      "sont, pas celui-ci. Classé par proximité du gisement, "
                      "faute de mieux : raffiner là où l'on mine évite de "
                      "traverser un système la soute pleine.*")

    lignes += _bloc_methodes(data.get("methodes"))
    return lignes


def _bloc_methodes(methodes: list[dict[str, Any]] | None) -> list[str]:
    """La méthode est l'autre moitié du rendement, et elle se choisit sur place.

    Le lieu donne quelques points de pourcentage ; la méthode arbitre entre
    rendement, coût et durée sur une échelle de 1 à 3. Répondre « où » sans
    dire « comment » laisse le joueur à mi-chemin.
    """
    if not methodes:
        return []
    meilleures = [m for m in methodes if (m.get("rendement") or 0) >= 4]
    if not meilleures:
        return []
    noms = enumerate_fr([f"**{m['nom']}**" for m in meilleures], limit=4)
    return ["", f"Côté méthode, le meilleur rendement est {noms} — "
                "les plus lentes, et pas les moins chères. Demande « quelle "
                "technique de raffinage est la plus rapide » pour l'autre "
                "critère."]


def render_raffinage(data: dict[str, Any]) -> str:
    """« Où je raffine du Stileron ? »"""
    minerai = data.get("minerai")
    if not minerai:
        nom = getattr(getattr(data.get("resolution"), "best", None), "name", None)
        return (f"Je ne connais pas de gisement pour « {nom} » : je ne sais "
                "donc pas où le raffiner." if nom else
                "Je n'ai pas reconnu le minerai.")
    if data.get("croises"):
        return _render_raffinage_croise(data)

    systemes = minerai["systemes"]
    ou = enumerate_fr([f"**{s}**" for s in
                       sorted(systemes, key=lambda s: -systemes[s])], limit=3)
    tete = (f"**{minerai['nom']}** est un minerai **{minerai['rarete']}**, "
            f"{_plural(minerai['gisements'], 'gisement')} dans {ou}.")
    if data.get("lieu"):
        tete += f" Les plus proches {_de(data['lieu'])} :"
    elif data.get("systeme"):
        # « À Stanton » a restreint la liste : le dire, sinon le lecteur ne
        # sait pas si sa contrainte a été lue.
        tete += f" Les raffineries de **{data['systeme']}** :"
    else:
        tete += " Les raffineries les mieux placées :"
    return "\n".join([tete, ""] + _bloc_raffineries(data))


def render_raffinage_complet(data: dict[str, Any]) -> str:
    """« La liste entière » — les 21 raffineries, sans troncature.

    Le résumé en montre six et annonce le reste ; cette liste n'est demandée
    que parce que le résumé ne suffisait pas.
    """
    return render_raffinage(dict(
        data, raffineries=data.get("toutes") or data.get("raffineries")))


def _render_raffinage_croise(data: dict[str, Any]) -> str:
    """« Où raffiner de l'iron et de l'hephaestanite » — une seule liste.

    Remarque de l'utilisateur : deux minerais en soute, un seul déplacement.
    Le plus rare commande le classement ; à rareté égale, c'est le cumul des
    rendements — et la règle appliquée se dit, sinon l'ordre a l'air
    arbitraire.
    """
    fiches = data["croises"]
    noms = " + ".join(f"**{f['nom']}**" for f in fiches)
    raretes = enumerate_fr([f"{f['nom']} est {f['rarete']}" for f in fiches],
                           limit=3)
    if data.get("cumul_demande"):
        # « Sans prendre en compte leur rareté » : la règle habituelle est
        # écartée à la demande, et on le dit — sinon le classement semble
        # contredire la doctrine du plus rare.
        regle = ("classement sur le **cumul des rendements**, comme demandé "
                 "— la rareté est ignorée")
    elif data.get("egalite_rarete"):
        regle = (f"les deux sont **{fiches[0]['rarete']}s** : à rareté "
                 "égale, on classe sur le **cumul des rendements** — les "
                 "deux chargements pèsent autant l'un que l'autre"
                 if len(fiches) == 2 else
                 f"tous sont {fiches[0]['rarete']}s : on classe sur le cumul "
                 "des rendements")
    else:
        regle = (f"{raretes} — c'est **{fiches[0]['nom']}**, le plus rare, "
                 "qui commande le classement")
    if data.get("lieu"):
        annonce = f"Les raffineries les plus proches {_de(data['lieu'])} :"
    elif data.get("systeme"):
        annonce = f"Les raffineries de **{data['systeme']}** :"
    else:
        annonce = "Les raffineries à privilégier :"
    lignes = [f"{noms} dans la même soute — {regle}.", "", annonce, ""]

    for raffinerie in data.get("raffineries") or []:
        rends = raffinerie.get("rendements") or {}
        detail = " · ".join(f"{f['nom']} **{rends[f['nom']]:+.0f} %**"
                            for f in fiches if f["nom"] in rends)
        manques = [f["nom"] for f in fiches if f["nom"] not in rends]
        if manques:
            detail += (" · " if detail else "") + \
                ", ".join(manques) + " sans relevé"
        situe = _situer_raffinerie(raffinerie)
        # L'en-tête vient d'annoncer le système : le répéter à chaque ligne
        # — « MIC-L2 — Stanton » sous « Les raffineries de Stanton » — est
        # du bruit. Remarque de l'utilisateur (« pas indiquer stanton »).
        if data.get("systeme"):
            situe = situe.replace(f" — {data['systeme']}", "")
        lignes.append(f"- {situe} — {detail}")

    reste = (data.get("total") or 0) - len(data.get("raffineries") or [])
    if reste > 0:
        lignes.append(f"- … et {_plural(reste, 'autre')}")
    lignes.append("\n*Écarts de rendement par minerai, relevés par les "
                  "joueurs sur UEX. Un terminal sans relevé n'est pas un "
                  "terminal à zéro : il passe après ceux qu'on sait mesurer.*")
    lignes += _bloc_methodes(data.get("methodes"))
    return "\n".join(lignes)


def render_conseil_raffinage(data: dict[str, Any]) -> str:
    """Où raffiner pour une recette — le minerai le plus rare commande."""
    pilote, minerais = data.get("pilote"), data.get("minerais") or []
    nom = ((data.get("blueprint") or {}).get("blueprint") or {}
           ).get("output_name") or "cet objet"
    if not pilote:
        return (f"La recette {_de(nom)} ne demande aucun minerai à raffiner.")

    autres = [m for m in minerais if m["nom"] != pilote["nom"]]
    lignes = [f"Pour **{nom}**, c'est **{pilote['nom']}** qui décide : "
              f"il est **{pilote['rarete']}**"]
    if autres:
        lignes[0] += (", quand " + enumerate_fr(
            [f"{m['nom']} est {m['rarete']}" for m in autres], limit=3))
    classe_sur = data.get("classe_sur") or pilote
    # Ne pas annoncer qu'on se place sur le plus rare si le classement porte
    # ensuite sur un autre : la phrase suivante le dirait le contraire.
    if autres and classe_sur["nom"] == pilote["nom"]:
        lignes[0] += (". On se place donc sur lui — les minerais communs se "
                      "trouvent partout, se déplacer pour eux n'aurait pas de "
                      "sens.")
    else:
        lignes[0] += "."

    # Le plus rare commande le **sujet** ; on ne peut classer que sur ce qui
    # est mesuré. Quand les deux diffèrent, le dire — sinon le lecteur croit
    # que les pourcentages parlent du Stileron.
    systemes = classe_sur["systemes"]
    ou = enumerate_fr([f"**{s}**" for s in
                       sorted(systemes, key=lambda s: -systemes[s])], limit=3)
    if classe_sur["nom"] != pilote["nom"]:
        lignes.append(
            f"\nMais aucun rendement n'est relevé pour {pilote['nom']} : le "
            f"classement ci-dessous porte sur **{classe_sur['nom']}**, le plus "
            "rare de la recette dont le rendement soit mesuré.")
    lignes.append(f"\n{classe_sur['nom']} s'extrait dans {ou}"
                  f" ({_plural(classe_sur['gisements'], 'gisement')}). "
                  + (f"Les raffineries les plus proches {_de(data['lieu'])} :"
                     if data.get("lieu") else "Les raffineries à privilégier :"))
    return "\n".join(lignes + [""] + _bloc_raffineries(data))


_CRITERE_FR = {"rendement": "le rendement", "vitesse": "la vitesse",
               "cout": "le coût"}
# « Vitesse » est féminin, « rendement » et « coût » masculins : l'adjectif
# s'accorde, sinon on lit « vitesse élevé » (R6).
_NIVEAU_FR = {1: "très faible", 2: "faible", 3: "moyen", 4: "élevé"}
_NIVEAU_FR_F = {1: "très faible", 2: "faible", 3: "moyenne", 4: "élevée"}


def render_methode_raffinage(data: dict[str, Any]) -> str:
    """« Quelle technique de raffinage est la meilleure ? » — selon quoi.

    Remarque de l'utilisateur : la plus rapide et la plus efficace ne sont pas
    la même. Le rendu **dit sur quel axe il classe**, et quand la question en
    cite plusieurs, il annonce le compromis et nomme les champions de chaque
    axe — sinon le joueur croit avoir le meilleur partout.
    """
    methodes = data.get("methodes") or []
    if not methodes:
        return "Je n'ai pas les notes des méthodes de raffinage."

    axes = data.get("criteres") or ["rendement"]
    sur = enumerate_fr([_CRITERE_FR[a] for a in axes], limit=3)
    tete = (f"Aucune méthode n'est la meilleure partout — voici le meilleur "
            f"compromis entre {sur} :" if data.get("compromis")
            else f"Pour {sur}, dans l'ordre :")

    lignes = [tete, ""]
    for methode in methodes:
        detail = (f"rendement {_NIVEAU_FR.get(methode['rendement'], '?')}, "
                  f"vitesse {_NIVEAU_FR_F.get(methode['vitesse'], '?')}, "
                  f"coût {_NIVEAU_FR.get(methode['cout'], '?')}")
        lignes.append(f"- **{methode['nom']}** — {detail}")

    if data.get("compromis"):
        par_axe = data.get("par_axe") or {}
        lignes += ["", "Pris séparément : " + enumerate_fr(
            [f"{_CRITERE_FR[a]} va à **{par_axe[a]}**"
             for a in _CRITERE_FR if a in par_axe], limit=3) + "."]

    premier = methodes[0]
    if premier.get("description"):
        lignes += ["", f"_{premier['nom']}_ — {premier['description']}"]
        if premier.get("en_francais"):
            lignes.append("\n*Traduction du Cirque Lisoir (StarTrad).*")
    return "\n".join(lignes)


_RENDERERS["methode_de_raffinage"] = render_methode_raffinage
_RENDERERS["ou_raffiner"] = render_raffinage
_RENDERERS["conseil_de_raffinage"] = render_conseil_raffinage
