"""Fabrication : blueprints, recettes, plans complets."""

from __future__ import annotations

from typing import Any

from .missions import (
    _activite,
    _rangs_ordonnes,
    render_mission_group,
    render_mission_groups,
)
from .ressources import _bloc_raffineries
from .socle import (
    _RENDERERS,
    _de,
    _mult,
    _nombre,
    _plural,
    _uec,
    duration_fr,
    enumerate_fr,
    provenance_composee,
    quantity_fr,
    question_de_suite,
    speakable_title,
)


def render_blueprint_missions(data: dict[str, Any]) -> str:
    """Toutes les missions qui distribuent le blueprint, rang par rang.

    Le résumé répond par le groupe — « les missions de mercenariat Headhunters
    à Pyro » — parce que c'est ainsi que le jeu distribue et que le joueur
    pense. Mais « cite-moi **toutes** les missions » demande autre chose, et
    la donnée l'autorise : titre, org, système, rang minimal.

    Deux honnêtetés obligatoires ici. Les titres sont des **gabarits** : le
    serveur y insère la cible à la génération du contrat, d'où « Wanted » plutôt
    que « Wanted: Ludwig Fizzlebang ». Et un pool sans mission sortie existe —
    le blueprint est déclaré en récompense, aucun contrat publié ne le donne.
    """
    bp = data["blueprint"]
    missions = [m for s in data["sources"] for m in s["missions"]]
    if not missions:
        if data.get("sources"):
            return (f"Le blueprint {_de(bp['output_name'])} est déclaré en "
                    f"récompense de mission, mais aucune mission sortie ne le "
                    f"distribue pour l'instant.")
        if bp.get("available_by_default"):
            return f"Le blueprint {_de(bp['output_name'])} est disponible d'emblée."
        return f"Aucune mission connue ne donne le blueprint {_de(bp['output_name'])}."

    orgs = sorted({m["mission_giver"] for m in missions if m.get("mission_giver")})
    systemes = sorted({m["system"] for m in missions if m.get("system")})
    activites = sorted({a for a in (_activite(m) for m in missions) if a})

    # Le compte vient de la requête, qui dédoublonne sur le titre affiché :
    # recompter les lignes brutes ici annonçait douze missions au-dessus d'une
    # liste qui en montrait huit.
    combien = data.get("mission_count") or len(missions)
    verbe = "donne" if combien <= 1 else "donnent"
    entete = (f"**{_plural(combien, 'mission')} {verbe} le blueprint "
              f"{_de(bp['output_name'])}**")
    if activites:
        entete += f"\nType : {enumerate_fr(activites, limit=3)}"
    if orgs:
        entete += f" · {enumerate_fr(orgs, limit=3)}"
    if systemes:
        entete += f" · {enumerate_fr(systemes, limit=3)}"

    # Un rang par ligne : la progression se lit verticalement, elle ne
    # s'enfile pas dans un paragraphe.
    lignes = [entete, ""]
    for rang, titres in _rangs_ordonnes(missions):
        lignes.append(f"- **{rang}** — {', '.join(titres)}")
    lignes.append("")
    lignes.append("Les titres sont ceux des fichiers du jeu ; le serveur y "
                  "insère la cible à la génération du contrat.")
    # La liste des noms appelle sa suite : ce qu'il faut y faire. Sans
    # l'annoncer, le joueur ne sait pas qu'il peut le demander.
    noms = [speakable_title(t) for _, titres in _rangs_ordonnes(missions)
            for t in titres]
    if noms:
        lignes.append("")
        lignes.append(question_de_suite(
            [f"ce qu'il faut faire dans « {noms[0]} »"] if len(noms) == 1
            else ["ce qu'il faut faire dans l'une d'elles"]))
    return "\n".join(lignes)


def render_grind_blueprint(data: dict[str, Any]) -> str:
    """Le chemin de réputation borné jusqu'à une mission source."""
    bp = data["blueprint"]
    grind = data.get("grind") or {}
    meilleur = grind.get("meilleur")
    if meilleur and meilleur.get("disponible_par_defaut"):
        return (f"Le blueprint {_de(bp['output_name'])} est disponible par "
                "défaut : **aucune mission ni réputation à farmer**.")
    if not grind.get("plans"):
        return (f"Aucune mission sortie ne permet de calculer un grind pour "
                f"le blueprint {_de(bp['output_name'])}.")

    rangs = grind.get("rangs_possibles") or []
    if grind.get("besoin_rang"):
        exemple = enumerate_fr(rangs, limit=8) if rangs else "rang inconnu"
        return (f"Pour calculer le grind du blueprint {_de(bp['output_name'])}, "
                "il me manque **ton rang actuel exact**. "
                f"Rangs publiés sur cette voie : {exemple}.")
    if grind.get("rang_inconnu") or meilleur is None:
        exemple = enumerate_fr(rangs, limit=8) if rangs else "aucun"
        return (f"Le rang « {grind.get('rang_actuel') or '?'} » n'appartient "
                f"à aucune voie mesurable de ce blueprint. Rangs publiés : "
                f"{exemple}. Je ne donne pas de nombre approché.")

    org = enumerate_fr(meilleur.get("orgs") or [], limit=2)
    faction = meilleur.get("faction") or org or "la faction concernée"
    finale = speakable_title(meilleur.get("mission_finale"))
    chance = meilleur.get("chance")
    mini = meilleur.get("missions_min")
    maxi = meilleur.get("missions_max")

    if maxi == 0:
        rang = meilleur.get("rang_cible")
        texte = (f"**0 mission de grind de réputation** : le blueprint "
                 f"{_de(bp['output_name'])} est déjà accessible")
        if rang:
            texte += f" au rang {rang}"
        if faction:
            texte += f" chez {faction}"
        texte += "."
    else:
        actuel = meilleur.get("rang_actuel") or grind.get("rang_actuel")
        if mini == maxi:
            borne = _plural(maxi, "mission de réputation")
        else:
            borne = (f"entre {_nombre(mini)} et {_nombre(maxi)} missions "
                     "de réputation")
        nature = ("le minimum théorique est" if mini == maxi
                  else "la borne théorique est")
        texte = (f"Depuis **{actuel}**, {nature} **{borne}** pour atteindre "
                 f"**{meilleur.get('rang_cible') or 'le seuil'}** chez {faction}.")
        if meilleur.get("score_min") is not None:
            texte += (f" Ton rang situe ton score entre "
                      f"{_nombre(meilleur['score_min'])} et "
                      f"{_nombre(meilleur['score_max'])} points ; l'intervalle "
                      "vient de la progression exacte que le jeu ne nous donne pas.")

        etapes = meilleur.get("etapes") or []
        if etapes:
            texte += "\n\nChemin conservateur si tu viens juste de passer le rang :"
            for etape in etapes[:6]:
                ou = f" ({etape['systeme']})" if etape.get("systeme") else ""
                texte += (f"\n- **{_nombre(etape['repetitions'])} ×** "
                          f"{etape['titre']}{ou} — +{_nombre(etape['gain'])} "
                          "points chacune")
            if len(etapes) > 6:
                texte += f"\n- … et {_nombre(len(etapes) - 6)} étapes de palier"

    if finale:
        texte += f"\n\nPuis : **{finale}**"
        if meilleur.get("systemes"):
            texte += " à " + enumerate_fr(meilleur["systemes"], limit=3)
        texte += "."
    if chance == 1:
        if meilleur.get("total_min") == meilleur.get("total_max"):
            texte += (f" Avec cette mission finale, le total est "
                      f"**{_plural(meilleur['total_max'], 'mission')}**.")
        elif meilleur.get("total_max") is not None:
            texte += (f" Avec la mission finale, compte donc **entre "
                      f"{_nombre(meilleur['total_min'])} et "
                      f"{_nombre(meilleur['total_max'])} missions**.")
    elif chance:
        texte += (f" Le pool de récompense est publié à "
                  f"**{_nombre(chance * 100)} %** : le nombre de tentatives "
                  "finales est aléatoire, je ne l'ajoute pas comme un +1 certain.")
    else:
        texte += (" La probabilité du pool n'est pas publiée : je ne transforme "
                  "pas la mission finale en nombre certain.")

    if maxi:
        texte += ("\n\n_Calcul : missions répétables seulement "
                  "(`once_only = 0`), plus court chemin parmi les gains publiés._")
    return texte


def render_blueprint(data: dict[str, Any]) -> str:
    # « Quelles missions donnent le blueprint » ne demande pas la recette. Le
    # résumé reste le groupe — c'est ainsi que le jeu distribue — mais il
    # annonce le compte et propose la liste, que `context.py` sait reprendre.
    if data.get("volet") == "grind":
        return render_grind_blueprint(data)
    if data.get("volet") == "missions":
        groupes = data.get("mission_groups") or []
        if groupes:
            texte = (f"Le blueprint {_de(data['blueprint']['output_name'])} "
                     f"s'obtient dans {render_mission_groups(groupes)}")
            if data.get("mission_count"):
                # « Les 1 mission » ne se dit pas : au singulier, l'article
                # change et le nombre disparaît.
                combien = data["mission_count"]
                libelle = ("la mission" if combien == 1
                           else f"les {_nombre(combien)} missions")
                return texte + ". " + question_de_suite([libelle])
            return texte + "."
        return render_blueprint_missions(data)

    if data.get("volet") == "demantelement":
        bp = data["blueprint"]
        retours = data.get("dismantle") or []
        if not retours:
            return (f"Le jeu ne publie aucun retour de démantèlement pour "
                    f"{bp['output_name']}.")
        morceaux = [
            (f"{str(r['quantity_scu']).replace('.', ',')} SCU de {r['ref_name']}"
             if r.get("quantity_scu") is not None
             else f"{r['ref_name']} en quantité non publiée")
            for r in retours
        ]
        efficacite = bp.get("dismantle_efficiency")
        rendement = (f", avec une efficacité publiée de "
                     f"{_nombre(efficacite * 100)} %" if efficacite is not None
                     else "")
        return (f"Démanteler **{bp['output_name']}** prend "
                f"{duration_fr(bp.get('dismantle_seconds'))}{rendement}. "
                f"Le retour publié est {enumerate_fr(morceaux, limit=6)}.")

    bp = data["blueprint"]
    tiers = data["tiers"]
    if not tiers:
        return f"{bp['output_name']} n'a aucune recette renseignée."

    tier = tiers[0]
    morceaux = []
    for group in tier["groups"]:
        for option in group["options"]:
            morceaux.append(f"{quantity_fr(option)} de {option['ref_name']}")
    texte = (
        f"{bp['output_name']} se fabrique en {duration_fr(tier['craft_time_seconds'])}, "
        f"avec {enumerate_fr(morceaux, limit=6)}"
    )

    groupes = data.get("mission_groups") or []
    if groupes:
        texte += f". Le blueprint s'obtient dans {render_mission_groups(groupes)}"
    elif data.get("sources"):
        # Le pool existe mais aucune mission sortie ne le distribue.
        texte += ". Le blueprint s'obtient en récompense de mission, sans mission sortie identifiée"
    elif bp["available_by_default"]:
        texte += ". Le blueprint est disponible par défaut"
    return texte + "."


def render_meme_serie(data: dict[str, Any]) -> str:
    """Ce que la série débloque en plus de ce qu'on visait.

    Le nombre d'abord, parce que c'est la question posée ; la liste ensuite,
    parce que c'est elle qui décide si ça vaut le farm.
    """
    nom = data["blueprint"]["output_name"]
    if not data["missions"]:
        return (f"Aucune mission sortie ne distribue le blueprint "
                f"{_de(nom)} : il n'y a donc pas de série à partager.")
    if not data["voisins"]:
        return (f"Les {_plural(data['missions'], 'mission')} qui donnent le "
                f"blueprint {_de(nom)} ne distribuent que celui-là.")

    ensemble = [v for v in data["voisins"] if v["toutes"]]
    partiels = [v for v in data["voisins"] if not v["toutes"]]

    lignes = [f"**{_plural(data['total'], 'autre blueprint', 'autres blueprints')}** "
              f"sortent des mêmes missions que {nom} — "
              f"{_plural(data['missions'], 'mission')} au total :"]
    if ensemble:
        lignes.append("")
        lignes.extend(f"- {v['output_name']}" for v in ensemble)
    if partiels:
        lignes.append("")
        lignes.append("Et en partie seulement :")
        lignes.extend(
            f"- {v['output_name']} — {_plural(v['communs'], 'mission')} "
            f"en commun sur {data['missions']}" for v in partiels)
    return "\n".join(lignes)


def render_blueprint_complet(data: dict[str, Any]) -> str:
    """La recette entière, palier par palier.

    Le résumé donne le nombre de composants ; ici on les nomme, avec leurs
    quantités. Les options d'un même groupe sont des alternatives, pas des
    ingrédients cumulés — les dire « ou » plutôt que « et » évite de faire
    croire qu'il faut les deux.
    """
    # « Cite-moi toutes les missions » et « donne-moi toute la recette » sont
    # deux exhaustivités différentes sur le même blueprint.
    if data.get("volet") == "grind":
        return render_grind_blueprint(data)
    if data.get("volet") == "missions":
        return render_blueprint_missions(data)

    bp = data["blueprint"]
    phrases = [f"Recette de {bp['output_name']}"]

    for tier in data["tiers"]:
        groupes = []
        for groupe in tier["groups"]:
            options = [
                f"{quantity_fr(o)} de {o['ref_name'] or o['group_name'] or '—'}"
                for o in groupe["options"]
            ]
            groupes.append(enumerate_fr(options, limit=len(options))
                           if len(options) == 1
                           else " ou ".join(options[:4]))
        if groupes:
            phrases.append(enumerate_fr(groupes, limit=len(groupes)))

    if data.get("mission_groups"):
        noms = [render_mission_group(g) for g in data["mission_groups"][:3]]
        phrases.append("Le blueprint sort " + _de(enumerate_fr(noms, limit=3)))
    return ". ".join(phrases) + "."


def _quantite_ingredient(ing: dict[str, Any]) -> str:
    """La quantité d'un ingrédient, avec assez de décimales pour être vraie.

    Les recettes se comptent en **centièmes de SCU** — 0,04 pour le Stileron
    d'un P8-AR. Arrondi au dixième comme le reste des nombres, la ligne
    devenait « 0,0 SCU × 122 450 aUEC = 4 898 aUEC », qui se lit comme une
    multiplication fausse. On garde donc les décimales tant qu'elles portent
    de l'information, et on ne dépasse pas trois.
    """
    quantite = ing.get("quantite")
    if quantite is None:
        return "?"
    # Un objet se compte en unités entières, une ressource en SCU.
    if ing.get("kind") != "resource":
        return f"×{int(quantite)}"
    for decimales in (1, 2, 3):
        if round(quantite, decimales) == round(quantite, 3):
            break
    return f"{quantite:.{decimales}f}".replace(".", ",") + " SCU"


def render_acheter_ou_fabriquer(data: dict[str, Any]) -> str:
    """« Vaut-il mieux acheter ou fabriquer un Omnisky IX ? »

    Le total est un **plancher** dès qu'un ingrédient n'a pas de cotation, et
    la phrase doit le dire au même endroit que le chiffre. Un « 2 429 aUEC »
    posé seul sous une recette dont la moitié des minerais ne se vend nulle
    part est un mensonge par omission.
    """
    nom = data.get("nom") or "cet objet"
    verdict = data.get("verdict")

    if verdict == "pas_de_recette":
        return (f"**{nom}** n'a pas de recette de fabrication dans les fichiers "
                "du jeu — il ne peut que s'acheter ou se récupérer."
                + provenance_composee(data))

    ingredients = data.get("ingredients") or []
    if not ingredients:
        return (f"Je n'ai pas le détail de la recette {_de(nom)}."
                + provenance_composee(data))

    total = data.get("cout_fabrication")
    achat = data.get("prix_achat")
    manquants = data.get("manquants") or []

    # La conclusion d'abord : c'est la question posée, le détail vient étayer.
    # Forme **nominale** et non verbale : « fabriquer de Frost-Star » ne se dit
    # pas, et `_de` est écrit pour suivre un nom, pas un infinitif.
    if verdict == "fabriquer":
        tete = (f"**La fabrication** {_de(nom)} revient moins cher : "
                f"**{_uec(total)}** de matériaux contre **{_uec(achat)}** à "
                f"l'achat, soit **{_uec(data['ecart'])}** d'économie.")
    elif verdict == "acheter" and not manquants:
        tete = (f"**L'achat** {_de(nom)} revient moins cher : **{_uec(achat)}** "
                f"contre **{_uec(total)}** de matériaux.")
    elif verdict == "acheter":
        # Le plancher suffit à conclure : inutile de chercher les prix manquants.
        tete = (f"**L'achat** {_de(nom)} revient moins cher : **{_uec(achat)}**, "
                f"quand les seuls matériaux que je sais chiffrer coûtent déjà "
                f"**{_uec(total)}**.")
    elif verdict == "pas_en_vente":
        tete = (f"**{nom}** ne se vend à aucun terminal relevé — la question ne "
                "se pose pas, il faut le fabriquer.")
        # Le coût reste la moitié utile de la réponse : « il faut le
        # fabriquer » sans dire combien ça coûte laisse au même point.
        if total and not manquants:
            tete += f" Les matériaux reviennent à **{_uec(total)}**."
        elif total:
            tete += (f" Les matériaux que je sais chiffrer reviennent à "
                     f"**{_uec(total)}**.")
    else:
        tete = (f"Je ne peux pas trancher pour **{nom}** : à l'achat "
                f"**{_uec(achat)}**, mais une partie des matériaux ne se vend "
                "nulle part et n'a donc pas de prix.")

    lignes = [tete, "", "**La recette :**", ""]
    for ing in ingredients:
        quantite = _quantite_ingredient(ing)
        if ing["cout"] is not None:
            lignes.append(f"- {ing['nom']} — {quantite} × "
                          f"{_uec(ing['unitaire'])} = **{_uec(ing['cout'])}**")
        else:
            lignes.append(f"- {ing['nom']} — {quantite}, "
                          "**vendu à aucun terminal** : il faut le miner")

    if manquants:
        # Dire *pourquoi* le total est partiel, et ce que ça change. « Il
        # manque des prix » laisserait croire à une lacune de relevé.
        lignes += ["", f"Le total de **{_uec(total)}** ne couvre donc que ce "
                       f"qui s'achète. {enumerate_fr(sorted(set(manquants)))} "
                       "ne se trouve" + ("nt" if len(set(manquants)) > 1 else "")
                       + " qu'au minage : le vrai coût est celui de "
                         "l'extraction, pas un prix de terminal."]
    return "\n".join(lignes) + provenance_composee(data)


def render_plan_de_fabrication(data: dict[str, Any]) -> str:
    """Le plan complet : ce qu'il faut, où le miner, où le raffiner, à quel prix.

    Quatre étapes numérotées, parce qu'elles se font **dans cet ordre** — et
    c'est la seule numérotation honnête du projet : ailleurs, un numéro
    décorerait une liste qui n'a pas d'ordre.
    """
    nom = data.get("nom") or "cet objet"
    recette = data.get("recette") or {}
    tiers = recette.get("tiers") or []

    lignes = [f"# Fabriquer **{nom}**", ""]

    # 1 — les matériaux
    lignes.append("## 1. Ce qu'il faut")
    lignes.append("")
    ingredients = [ing for tier in tiers for groupe in tier.get("groups") or []
                   for ing in groupe.get("options") or []]
    if not ingredients:
        lignes.append("La recette n'est pas détaillée dans les fichiers du jeu.")
    for ing in ingredients:
        # Le même formateur que `render_acheter_ou_fabriquer` : les recettes se
        # comptent en **centièmes** de SCU, et l'arrondi au dixième affichait
        # « 0,0 SCU » pour le Stileron d'un P8-AR.
        quantite = _quantite_ingredient({
            "quantite": (ing["quantity_scu"] if ing.get("quantity_scu") is not None
                         else ing.get("quantity_units") or 1),
            "kind": ing.get("ingredient_kind"),
        })
        lignes.append(f"- {ing['ref_name']} — {quantite}")
    if data.get("temps_fabrication"):
        lignes += ["", f"Temps d'assemblage publié : "
                   f"**{duration_fr(data['temps_fabrication'])}**."]

    # 2 — où miner. La rareté commande le trajet, donc elle se dit ici.
    gisements = data.get("gisements") or []
    if gisements:
        lignes += ["", "## 2. Où les extraire", ""]
        for gis in gisements:
            systemes = gis["systemes"]
            if not systemes:
                lignes.append(f"- **{gis['nom']}** ({gis['rarete']}) — aucun "
                              "gisement répertorié : il s'achète ou se récupère")
                continue
            ou = enumerate_fr(
                [f"**{s}**" for s in sorted(systemes, key=lambda s: -systemes[s])],
                limit=3)
            lignes.append(f"- **{gis['nom']}** ({gis['rarete']}) — {ou}, "
                          f"{_plural(gis['total'], 'gisement')}")
        # Ce qui n'est pas un minerai ne se mine pas : le dire, plutôt que de
        # laisser un matériau de la recette sans provenance.
        for autre in data.get("hors_minerai") or []:
            lignes.append(f"- **{autre}** — ce n'est pas un minerai : il "
                          "s'achète, se récupère ou se fabrique à part")

    # 3 — où raffiner. Le bloc existant sait déjà le dire, on le réutilise
    # plutôt que d'en réécrire une variante qui divergerait.
    raffinage = data.get("raffinage")
    if raffinage and raffinage.get("raffineries"):
        lignes += ["", "## 3. Où raffiner", ""]
        pilote = raffinage.get("pilote") or {}
        classe_sur = raffinage.get("classe_sur") or pilote
        if pilote:
            lignes.append(f"C'est **{pilote['nom']}** qui commande : il est "
                          f"**{pilote['rarete']}**, et les communs se trouvent "
                          "partout.")
            if classe_sur.get("nom") != pilote.get("nom"):
                lignes.append(f"\nAucun rendement n'est relevé pour lui — le "
                              f"classement porte sur **{classe_sur['nom']}**.")
            lignes.append("")
        lignes += _bloc_raffineries(raffinage)
        lignes += ["", "Les méthodes ne publient qu'un classement de vitesse "
                   "(très lente à rapide), pas une durée absolue de "
                   "raffinage : je ne transforme pas ce rang en heures."]

    # 4 — le coût, et le verdict. `render_acheter_ou_fabriquer` redit la
    # recette ; ici elle est déjà à l'étape 1, donc on ne garde que la tête.
    couts = data.get("couts")
    if couts and couts.get("verdict") not in (None, "pas_de_recette"):
        lignes += ["", "## 4. Est-ce que ça vaut le coup", ""]
        lignes.append(render_acheter_ou_fabriquer(couts).split("\n\n**La recette")[0])

    # Une qualité explicitement demandée appartient au plan. Sans nombre, on
    # garde l'ouverture courte vers la fiche spécialisée.
    effet = data.get("effet_qualite")
    if effet is not None:
        from .personnel import render_fiche_qualite
        lignes += ["", "## 5. Effet de la qualité demandée", "",
                   render_fiche_qualite(effet)]
    elif data.get("qualite") is not None:
        lignes += ["", f"La qualité {_nombre(data['qualite'])} est demandée, "
                   "mais le jeu ne publie aucun effet de qualité pour ce "
                   "blueprint."]
    else:
        lignes += ["", "_La qualité des minerais module les statistiques du "
                   f"résultat — demande « un {nom} 900 » pour l'effet chiffré._"]

    return "\n".join(lignes) + provenance_composee(data)
_RENDERERS["acheter_ou_fabriquer"] = render_acheter_ou_fabriquer
_RENDERERS["plan_de_fabrication"] = render_plan_de_fabrication


# Les types de sortie d'un blueprint, en français. Les valeurs restent
# anglaises en base — seul le rendu traduit.
_TYPE_SORTIE = {
    "WeaponPersonal": "armes personnelles", "WeaponGun": "armes de vaisseau",
    "Shield": "boucliers", "PowerPlant": "générateurs", "Cooler": "refroidisseurs",
    "QuantumDrive": "moteurs quantiques", "Radar": "radars",
    "Char_Armor_Helmet": "casques", "Char_Armor_Torso": "plastrons",
    "Char_Armor_Legs": "jambières", "Char_Armor_Arms": "brassards",
    "Char_Armor_Backpack": "sacs à dos", "Char_Armor_Undersuit": "sous-combinaisons",
    "MissileLauncher": "lance-missiles", "Missile": "missiles",
    "Turret": "tourelles", "WeaponAttachment": "accessoires d'arme",
    "Ordinance": "munitions", "FuelTank": "réservoirs",
}


def render_fabrique_avec(data: dict[str, Any]) -> str:
    """« Avec du Laranite : 341 recettes, surtout des boucliers et des armes. »

    On groupe par type de sortie, comme les missions par organisation : citer
    856 recettes est exact et inutilisable.
    """
    nom, groupes = data["ingredient"], data["groupes"]
    if not groupes:
        return (f"Rien ne se fabrique avec **{nom}**, d'après les recettes du "
                "jeu.")

    entete = (f"**{nom}** entre dans {_plural(data['total'], 'recette')}, "
              f"réparties en {_plural(data['familles'], 'famille')} :")
    lignes = [entete, ""]
    for g in groupes:
        exemples = ", ".join(g["exemples"])
        reste = g["total"] - len(g["exemples"])
        suite = f" et {_nombre(reste)} autres" if reste > 0 else ""
        lignes.append(f"- **{_TYPE_SORTIE.get(g['type'], g['type'])}** — {_nombre(g['total'])} : "
                      f"{exemples}{suite}")
    if data["familles"] > len(groupes):
        lignes.append(f"- … et {data['familles'] - len(groupes)} autres familles")
    return "\n".join(lignes)


_RENDERERS["que_fabrique_t_on_avec"] = render_fabrique_avec


def render_blueprints_par_systeme(data: dict[str, Any]) -> str:
    """« Quels blueprints à Stanton ? » — le résumé par organisation.

    Le jeu raisonne par groupes : 201 blueprints s'y débloquent par la
    progression chez neuf organisations, et c'est le groupe qui répond —
    les énumérer serait exact et illisible.
    """
    ou = f" dans **{data['systeme']}**" if data.get("systeme") else ""
    if data.get("groupes_armes"):
        # « Les blueprints d'armes FPS de Pyro » : le catalogue groupé par
        # classe, au même format que « c'est quoi les X » — la marque avec.
        from .armes import render_catalogue_objets

        texte = render_catalogue_objets(dict(
            data, famille=f"blueprints {_de(data['famille'])}"
            + (f" des missions de {data['systeme']}" if data.get("systeme")
               else ""),
            groupes=data["groupes_armes"]))
        return texte.replace(
            "Demande la fiche d'un nom — ses stats, son prix, son blueprint",
            "Demande « comment fabriquer <nom> » pour la recette et les "
            "missions qui donnent le blueprint")
    lignes = [f"**{_plural(data['total'], 'blueprint')}** s'obtiennent dans "
              f"les missions{ou}, par organisation :", ""]
    for groupe in data["groupes"]:
        d_ou = (f" ({groupe['system']})"
                if not data.get("systeme") and groupe.get("system") else "")
        lignes.append(f"- **{groupe['org']}**{d_ou} — "
                      f"{_plural(groupe['n'], 'blueprint')}")
    lignes += ["", "C'est la progression de réputation qui les débloque. "
               "Demande « que donnent les missions "
               f"{data['groupes'][0]['org']} en blueprint » pour le détail "
               "d'un groupe."]
    return "\n".join(lignes)


def render_blueprints_par_systeme_complet(data: dict[str, Any]) -> str:
    """« La liste entière » — tous les modèles du volet famille."""
    if not data.get("groupes_armes"):
        return render_blueprints_par_systeme(data)
    from .armes import render_catalogue_objets_complet

    texte = render_catalogue_objets_complet(dict(
        data, famille=f"blueprints {_de(data['famille'])}"
        + (f" des missions de {data['systeme']}" if data.get("systeme")
           else ""),
        groupes=data["groupes_armes"]))
    return texte


_RENDERERS["blueprints_par_systeme"] = render_blueprints_par_systeme


# ------------------------------------------------------ le poste de fabrication

def _materiaux_du_poste(poste: dict[str, Any]) -> str:
    """Ce qu'on charge dans un emplacement.

    Les options d'un même emplacement sont des **alternatives** : le « ou »
    est obligatoire, sans quoi la ligne se lit comme une addition et le joueur
    part miner deux minerais au lieu d'un. C'est la règle déjà tenue par la
    recette complète.
    """
    if not poste["options"]:
        return "matériau non publié"
    morceaux = []
    for option in poste["options"][:4]:
        texte = f"{quantity_fr(option)} de {option['nom']}"
        if option["min_quality"] is not None:
            texte += f" (qualité {_nombre(option['min_quality'])} minimum)"
        morceaux.append(texte)
    reste = len(poste["options"]) - len(morceaux)
    ligne = " ou ".join(morceaux)
    return ligne + (f", ou {_plural(reste, 'autre matériau', 'autres matériaux')}"
                    if reste > 0 else "")


def _effets_du_poste(poste: dict[str, Any], qualite: float | None) -> str:
    """Ce que la qualité du matériau chargé ici fait bouger.

    À qualité connue on rend le **multiplicateur**, jamais une valeur : rien
    ne dit à quelle qualité correspond le barème du catalogue, et « 176 dégâts
    → 193 » serait un chiffre que le jeu ne publie pas.
    """
    chiffrables = [e for e in poste["effets"] if e.chiffrable]
    muets = [e.nom for e in poste["effets"] if not e.chiffrable]
    morceaux = []
    for effet in chiffrables[:4]:
        if qualite is None:
            morceaux.append(f"{effet.nom} ×{_mult(effet.mult_min)} → "
                            f"×{_mult(effet.mult_max)}")
        else:
            morceaux.append(f"{effet.nom} ×{_mult(effet.multiplicateur(qualite))}")
    reste = len(chiffrables) - len(morceaux)
    if reste > 0:
        morceaux.append(f"et {_plural(reste, 'autre statistique', 'autres statistiques')}")
    if muets:
        morceaux.append(enumerate_fr([m.lower() for m in muets], limit=3)
                        + " (non chiffrable)")
    return ", ".join(morceaux)


def render_fabricateur(data: dict[str, Any]) -> str:
    """« Je fabrique un P6-LR : je mets quoi dans quel emplacement ? »

    Le fabricateur charge **un matériau par emplacement**, et chaque
    emplacement pilote ses propres statistiques. La réponse suit cet ordre :
    les emplacements d'abord, puis celui qui mérite le bon minerai — avec la
    règle de classement annoncée, parce qu'un classement muet a l'air
    arbitraire.
    """
    nom = data["nom"]
    paliers = data.get("paliers") or []
    if not paliers or not any(p["postes"] for p in paliers):
        # **Une recette sans groupe n'est pas une recette absente.** Le
        # fabricateur n'a alors rien à répartir, et le dire renvoie vers
        # l'outil qui répond vraiment plutôt que de rendre une page vide.
        materiaux = [o["nom"] for p in paliers for o in p.get("hors_poste") or []]
        if materiaux:
            return (f"La recette de **{nom}** ne passe par aucun emplacement de "
                    f"fabrication : ses matériaux — "
                    f"{enumerate_fr(sorted(set(materiaux)), limit=6)} — se "
                    f"chargent sans choix de composant. "
                    + question_de_suite(["la recette complète"]))
        return (f"Le jeu ne publie aucun emplacement de fabrication pour "
                f"**{nom}** : sa recette n'a pas de composant à charger "
                f"séparément.")

    qualite = data.get("qualite")
    lignes = []
    for palier in paliers:
        postes = palier["postes"]
        if not postes:
            continue
        entete = f"**Fabriquer {nom}** — {_plural(len(postes), 'emplacement')}"
        if len(paliers) > 1:
            entete = (f"**Fabriquer {nom}**, palier {palier['index']} — "
                      f"{_plural(len(postes), 'emplacement')}")
        if palier["temps"]:
            entete += f", {duration_fr(palier['temps'])} de fabrication"
        if qualite is not None:
            entete += f" · matériaux de qualité **{_nombre(qualite)}**"
        lignes += [entete, ""]

        for poste in postes:
            ligne = f"- **{poste['nom']}** — {_materiaux_du_poste(poste)}"
            if poste.get("requis") and poste["requis"] > 1:
                ligne += f" · le jeu en demande {_nombre(poste['requis'])}"
            lignes.append(ligne)
            effets = _effets_du_poste(poste, qualite)
            if effets:
                lignes.append(f"    - pilote : {effets}")
            else:
                lignes.append("    - la qualité du matériau n'y change rien "
                              "de publié")
            # **Une barre de qualité qu'on ne franchit pas se dit.**
            # `MinQuality` est publié : rendre un multiplicateur pour un
            # matériau que l'emplacement refuse serait un chiffre inatteignable.
            # Les deux cas ne se confondent pas — plus rien de chargeable, ou
            # un choix qui rétrécit. Les tournures évitent le verbe : « X et Y
            # demande mieux » se trompe d'accord une fois sur deux.
            if poste.get("refuses"):
                refuses = enumerate_fr(poste["refuses"], limit=3)
                if poste.get("ferme"):
                    lignes.append(f"    - **rien de chargeable à cette "
                                  f"qualité** : {refuses} — qualité minimale "
                                  f"non atteinte")
                else:
                    restants = [o["nom"] for o in poste["options"]
                                if o["nom"] not in poste["refuses"]]
                    lignes.append(f"    - refusé à cette qualité : {refuses} "
                                  f"— il reste "
                                  f"{enumerate_fr(restants, limit=3)}")
        # Ce que le jeu ne rattache à aucun emplacement se charge quand même :
        # le taire ferait une recette incomplète, l'inventer un emplacement
        # sans nom.
        if palier.get("hors_poste"):
            morceaux = [f"{quantity_fr(o)} de {o['nom']}"
                        for o in palier["hors_poste"][:6]]
            lignes.append(f"- **Sans emplacement** — {enumerate_fr(morceaux, limit=6)} "
                          f": le jeu ne les rattache à aucun composant.")
        lignes.append("")

    # **La règle de classement s'annonce.** Sans elle, « le canon d'abord » se
    # lit comme un avis ; avec elle, c'est une mesure qu'on peut contester.
    premier = next((p for palier in paliers for p in palier["postes"]
                    if p.get("amplitude")), None)
    if premier:
        lignes.append(
            f"**Le meilleur matériau va dans « {premier['nom']} »** — c'est "
            f"l'emplacement dont le jeu fait le plus varier une statistique "
            f"entre le pire et le meilleur matériau "
            f"({_nombre(premier['amplitude'] * 100)} % d'écart publié). "
            f"Les emplacements sont classés sur cet écart.")

    if data.get("sans_poste"):
        lignes += ["", f"{_plural(len(data['sans_poste']), 'modificateur')} "
                       f"{'ne se rattache' if len(data['sans_poste']) == 1 else 'ne se rattachent'} "
                       f"à aucun emplacement publié : je ne dis pas où les jouer."]

    demantelement = data.get("demantelement") or {}
    if demantelement.get("retours"):
        # **Les retours se comptent en centièmes de SCU.** Arrondi au dixième
        # comme le reste des nombres, « 0,02 SCU de Stileron » devenait
        # « 0,0 SCU » — le piège déjà payé sur les quantités d'ingrédient.
        morceaux = [
            (f"{quantity_fr(r)} de {r['ref_name']}"
             if r.get("quantity_scu") is not None
             else f"{r['ref_name']} en quantité non publiée")
            for r in demantelement["retours"][:4]
        ]
        rendement = demantelement.get("efficacite")
        suffixe = (f", efficacité publiée {_nombre(rendement * 100)} %"
                   if rendement is not None else "")
        lignes += ["", f"Le même fabricateur démantèle : "
                       f"{enumerate_fr(morceaux, limit=4)} en retour"
                       f"{suffixe}."]

    if data.get("variantes"):
        lignes += ["", "Mêmes emplacements sur "
                   + ", ".join(data["variantes"]) + "."]

    suites = ["où miner ces matériaux"]
    if qualite is None:
        suites.insert(0, "ce que ça donne avec des matériaux de qualité 900")
    lignes += ["", question_de_suite(suites)]
    return "\n".join(lignes)


_RENDERERS["fabricateur"] = render_fabricateur
