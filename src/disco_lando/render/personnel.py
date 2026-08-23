"""Équipement personnel : qualité de fabrication, emports, classements."""

from __future__ import annotations


from .socle import (_RENDERERS, _de, _mult, _nombre, _plural,
                    au_pluriel)


# ---------------------------------------------------- qualité de fabrication

def _pourcent(rapport: float) -> str:
    """Un écart relatif, signé. Le lecteur compare deux qualités : c'est le
    sens et l'ampleur qu'il retient, pas le multiplicateur brut."""
    ecart = (rapport - 1.0) * 100
    return f"{'+' if ecart >= 0 else '−'}{abs(ecart):.1f} %".replace(".", ",")


def _grouper(effets) -> list[tuple[str, list]]:
    """Une statistique, puis les composants qui la portent.

    Le canon **et** les pièces de précision agissent sur les dégâts du P6-LR.
    Les cumuler fabriquerait un nombre que le jeu ne publie pas ; les lister
    deux fois à l'identique fait passer une même information pour deux. On
    regroupe donc par statistique, et on ne détaille que si les composants
    **divergent** — 591 couples sur 4 559 sont dans ce cas.
    """
    par_stat: dict[str, list] = {}
    for e in effets:
        par_stat.setdefault(e.nom, []).append(e)
    return list(par_stat.items())


def _composants(effets) -> str:
    noms = [e.composant for e in effets if e.composant]
    if not noms:
        return ""
    vus = list(dict.fromkeys(noms))
    if len(vus) == 1:
        return vus[0].lower()
    return ", ".join(v.lower() for v in vus[:-1]) + f" et {vus[-1].lower()}"


def render_fiche_qualite(data) -> str:
    """« C'est quoi les statistiques d'un P6-LR 900 »."""
    q = data.qualite
    if q > data.borne_max:
        introduction = (
            f"**{data.nom}** fabriqué avec des matériaux de qualité "
            f"**{_nombre(q)}** ; ses effets publiés plafonnent dès "
            f"{_nombre(data.borne_max)}.")
    else:
        introduction = (
            f"**{data.nom}** fabriqué avec des matériaux de qualité "
            f"**{_nombre(q)}** sur {_nombre(data.borne_max)}.")
    lignes = [introduction, ""]

    chiffrables = [(nom, g) for nom, g in _grouper(data.effets)
                   if any(e.chiffrable for e in g)]
    muets = [nom for nom, g in _grouper(data.effets)
             if not any(e.chiffrable for e in g)]

    for nom, groupe in chiffrables:
        parts = {round(e.multiplicateur(q), 6) for e in groupe if e.chiffrable}
        porteurs = _composants(groupe)
        suffixe = f" — {porteurs}" if porteurs else ""
        if len(parts) == 1:
            mult = parts.pop()
            ligne = f"- **{nom}** : ×{_mult(mult)}"
            # Le barème du catalogue n'est pas la valeur à qualité zéro : on
            # dit à quelle qualité le multiplicateur vaut 1, ce qui permet au
            # lecteur de situer le chiffre sans qu'on invente sa provenance.
            neutre = next((e.qualite_neutre() for e in groupe if e.chiffrable), None)
            if neutre is not None:
                ligne += f" (référence à {_nombre(round(neutre))})"
            lignes.append(ligne + suffixe)
        else:
            lignes.append(f"- **{nom}**{suffixe} :")
            for e in groupe:
                if e.chiffrable:
                    lignes.append(f"    - {e.composant or 'sans composant nommé'} : "
                                  f"×{_mult(e.multiplicateur(q))}")

    if muets:
        lignes += ["", "Le jeu fait aussi varier " + ", ".join(muets).lower()
                   + ", sans publier de quoi le chiffrer."]

    if data.variantes:
        lignes += ["", "Mêmes effets sur " + ", ".join(data.variantes) + "."]

    return "\n".join(lignes)


def render_comparer_qualites(data) -> str:
    """« Quelle différence entre un P6-LR 900 et un P6-LR 990 »."""
    a, b = _nombre(data.qualite_a), _nombre(data.qualite_b)
    if not data.ecarts:
        return (f"Aucune différence entre un **{data.nom}** en qualité {a} et "
                f"en qualité {b} : les statistiques que le jeu fait varier sont "
                f"déjà au bout de leur plage.")

    lignes = [f"**{data.nom}**, de la qualité {a} à la qualité {b} :", ""]
    for nom, groupe in _grouper(data.ecarts):
        rapports = {round(e.rapport, 6) for e in groupe}
        porteurs = _composants(groupe)
        suffixe = f" — {porteurs}" if porteurs else ""
        if len(rapports) == 1:
            e = groupe[0]
            ligne = (f"- **{nom}**{suffixe} : ×{_mult(e.mult_a)} → "
                     f"×{_mult(e.mult_b)}, soit {_pourcent(e.rapport)}")
            if e.barème is not None:
                ligne += (f" — sur un barème de {_nombre(e.barème)}, "
                          f"{_nombre(round(e.barème * e.mult_a, 1))} contre "
                          f"{_nombre(round(e.barème * e.mult_b, 1))}")
            lignes.append(ligne)
        else:
            lignes.append(f"- **{nom}**{suffixe} :")
            for e in groupe:
                lignes.append(f"    - {e.composant or 'sans composant nommé'} : "
                              f"{_pourcent(e.rapport)}")

    if data.sans_effet:
        lignes += ["", "Inchangé à ces deux qualités : "
                   + ", ".join(data.sans_effet).lower() + "."]
    if data.non_chiffrables:
        lignes += ["", "Le jeu fait aussi varier " + ", ".join(data.non_chiffrables).lower()
                   + ", sans publier de quoi le chiffrer."]
    return "\n".join(lignes)


def render_echelle_qualite(data) -> str:
    """« Quels sont les types de qualité ? » — il n'y en a pas, et c'est la
    réponse : dire l'échelle continue vaut mieux qu'inventer des paliers."""
    return (
        f"La qualité de fabrication est une **échelle continue de "
        f"{_nombre(data['borne_min'])} à {_nombre(data['borne_max'])}** — le "
        f"jeu ne définit aucun palier fixe, « 900 » ou « 990 » sont des points "
        f"sur cette échelle, pas des grades.\n\n"
        f"Sur les {_nombre(data['blueprints'])} blueprints qui y sont "
        f"sensibles ({_nombre(data['modificateurs'])} statistiques modulées), "
        f"chaque valeur s'interpole linéairement entre ses deux bornes ; le "
        f"barème du catalogue se situe au point où le multiplicateur vaut 1 — "
        f"le plus souvent 500.\n\n"
        f"Je peux te donner la chaîne chiffrée d'un objet précis — « toutes "
        f"les qualités du P6-LR » — ou un point exact — « un P6-LR 900 »."
    )


def render_chaine_qualites(data) -> str:
    """« Les caractéristiques de toutes les qualités du P6-LR » — le format de
    la comparaison, étendu à toute la chaîne (demande de l'utilisateur)."""
    fiche, points = data["fiche"], data["points"]
    entete = " → ".join(_nombre(p) for p in points)
    lignes = [f"**{fiche.nom}**, le long de l'échelle de qualité "
              f"({entete}) :", ""]

    chiffrables = [(nom, g) for nom, g in _grouper(fiche.effets)
                   if any(e.chiffrable for e in g)]
    muets = [nom for nom, g in _grouper(fiche.effets)
             if not any(e.chiffrable for e in g)]

    for nom, groupe in chiffrables:
        series = {tuple(round(e.multiplicateur(p), 6) for p in points)
                  for e in groupe if e.chiffrable}
        porteurs = _composants(groupe)
        suffixe = f" — {porteurs}" if porteurs else ""
        if len(series) == 1:
            serie = series.pop()
            valeurs = " → ".join(f"×{_mult(m)}" for m in serie)
            ligne = f"- **{nom}** : {valeurs}"
            bareme = fiche.barèmes.get(next(e.cle for e in groupe if e.chiffrable))
            if bareme is not None:
                reels = " → ".join(_nombre(round(bareme * m, 1)) for m in serie)
                ligne += f" — soit {reels}"
            lignes.append(ligne + suffixe)
        else:
            # Deux composants portent la même statistique avec des plages
            # différentes : les cumuler fabriquerait un nombre non publié.
            lignes.append(f"- **{nom}**{suffixe} :")
            for e in groupe:
                if e.chiffrable:
                    valeurs = " → ".join(f"×{_mult(e.multiplicateur(p))}"
                                         for p in points)
                    lignes.append(f"    - {e.composant or 'sans composant nommé'} : "
                                  f"{valeurs}")

    lignes += ["", "_L'échelle est continue : tout point intermédiaire "
               "s'interpole linéairement entre ces valeurs._"]
    if muets:
        lignes += ["", "Le jeu fait aussi varier " + ", ".join(muets).lower()
                   + ", sans publier de quoi le chiffrer."]
    if fiche.variantes:
        lignes += ["", "Mêmes effets sur " + ", ".join(fiche.variantes) + "."]
    return "\n".join(lignes)


_RENDERERS["fiche_qualite"] = render_fiche_qualite
_RENDERERS["comparer_qualites"] = render_comparer_qualites
_RENDERERS["echelle_de_qualite"] = render_echelle_qualite
_RENDERERS["chaine_de_qualites"] = render_chaine_qualites


# ------------------------------------------- à partir de quelle qualité je tue

def _verdict(scenario: dict, pv: float) -> str:
    """Une ligne de scénario : la qualité qui suffit, ou de combien on rate.

    **Dire de combien ça rate est le cœur de la réponse.** « Impossible » tout
    court laisse croire que l'arme est hors sujet ; « il te manque 1 % » dit
    qu'un accessoire suffit, et « il t'en manque 88 % » dit de changer d'arme.
    """
    if scenario["qualite"] is not None:
        if scenario["qualite"] <= 0:
            return "oui, à n'importe quelle qualité"
        return f"à partir de la qualité **{_nombre(scenario['qualite'])}**"
    manque = (pv - scenario["degats_au_max"]) / pv * 100
    return (f"jamais — au maximum {_nombre(scenario['degats_au_max'])} dégâts "
            f"sur {_nombre(pv)}, il en manque {_nombre(manque)} %")


def _effet_accessoire(acc: dict) -> str:
    """Ce qu'un accessoire coûte et rapporte, en une parenthèse.

    Les colonnes `damage_multiplier` et `sound_multiplier` d'`item_stats`
    arrivent ici sous les clés `mult` et `bruit` : c'est `qualite.py` qui les
    lit, l'affichage n'en voit que le résultat. Elles sont donc rendues, et
    la sentinelle des colonnes ignorées les trouve nommées ici.

    Même chose pour le détail de l'alpha — `alpha_physical`, `alpha_energy`,
    `alpha_thermal`, `alpha_biochemical`, `alpha_distortion`, `alpha_stun` :
    `qualite._degats_utiles` les oppose chacune à la résistance de son type
    et rend le détail, que `render_qualite_pour_tuer` affiche sous « Le
    calcul ». La distorsion et le stun y sont **nommés pour être écartés** :
    ils ne retirent aucun point de vie.
    """
    if acc is None:
        return ""
    degats = ("dégâts inchangés" if acc["mult"] == 1.0
              else f"{_pourcent(acc['mult'])} de dégâts")
    bruit = (f", bruit {_pourcent(acc['bruit'])}"
             if acc["bruit"] is not None and acc["bruit"] != 1.0 else "")
    return f" _({degats}{bruit})_"


def _plancher(data: dict) -> str:
    """Ce qu'on ne peut pas battre, et ce qu'il faut pour l'atteindre.

    **C'est la ligne qui ferme la question.** Sans elle, un joueur à qui l'on
    annonce « 9 balles » ne sait pas s'il lui manque un accessoire ou si 8 est
    hors d'atteinte, et il cherche. Demande de l'utilisateur, 2026-08-11.
    """
    plancher = data.get("plancher") or {}
    if not plancher:
        return ""
    minimum = plancher["balles"]
    if minimum <= 1:
        return ""
    accessoire = plancher.get("accessoire")
    comment = (f"il faut la qualité {_nombre(plancher['qualite'])} "
               f"**et** {accessoire['nom']}" if accessoire
               else f"il faut la qualité {_nombre(plancher['qualite'])}")
    if plancher["au_bareme"] == minimum:
        comment = ("ni la qualité ni un accessoire n'y changent quoi que ce "
                   "soit")
    return (f"**Le minimum est {_plural(minimum, 'balle')}** — {comment}. "
            f"Tu ne descendras pas à {minimum - 1}.")


def render_qualite_pour_tuer(data: dict) -> str:
    """« Combien de dégâts dans le torse d'une armure lourde avec un CQ7 »,
    « à partir de quelle qualité je tue d'une balle dans la tête ».

    **Trois questions, un calcul, trois attaques différentes.** Mener par « il
    ne tue pas d'une balle » quand on demandait des dégâts, c'est répondre à
    côté avec des chiffres justes — le pire cas. Le volet, lu par le
    préparateur, décide de la première ligne ; le reste suit dans le même
    ordre.
    """
    a = data["armure"]
    classe = data["classe_libelle"]
    volet = data.get("volet")
    gerbe = f" × {_nombre(data['plombs'])} plombs" if data["plombs"] > 1 else ""
    lignes = [
        f"**{data['nom']}**, un tir {data['zone_libelle']} contre une armure "
        f"{classe} :", ""]

    # **Les deux volets sur demande rendent leur seul sujet** (sprint 21) :
    # re-dérouler la fiche entière avant la liste demandée serait le bruit
    # qu'on vient de couper.
    if volet == "alternatives":
        if not data["alternatives"]:
            lignes.append("- Rien au catalogue ne tue d'une balle ici sans "
                          "compter la qualité — la question de la qualité "
                          "reste la bonne.")
        else:
            lignes.append("Ce qui tue d'une balle **sans même compter la "
                          "qualité** :")
            for arme in data["alternatives"]:
                lignes.append(f"- **{arme['nom']}** — "
                              f"{_nombre(arme['degats'])} dégâts au barème "
                              "du catalogue")
        return "\n".join(lignes)
    if volet == "exception":
        if not a["exception"]:
            lignes.append("- Aucune pièce d'exception ici : la résistance "
                          "affichée vaut pour toute la classe.")
            return "\n".join(lignes)
        e = a["exception"]
        pluriel = len(e["noms"]) > 1
        if e["qualite"] is not None and e["qualite"] <= 0:
            verdict = "ça passe quand même, à toute qualité"
        elif e["qualite"] is not None:
            verdict = f"il faut la qualité {_nombre(e['qualite'])}"
        elif e["qualite_avec_accessoire"] is not None:
            verdict = (f"il faut la qualité "
                       f"{_nombre(e['qualite_avec_accessoire'])} **et** le "
                       "meilleur accessoire de dégâts")
        else:
            verdict = (f"aucune qualité ne suffit — "
                       f"{_nombre(e['degats_au_max'])} dégâts au mieux")
        lignes.append(
            f"⚠ {' et '.join(e['noms'])} encaisse{'nt' if pluriel else ''} "
            f"bien mieux ({_mult(e['resistance'])} au lieu de "
            f"{_mult(a['resistance'])}) : contre "
            f"{'eux' if pluriel else 'lui'}, {verdict}.")
        return "\n".join(lignes)

    plancher = data.get("plancher") or {}
    if volet in ("degats", "balles"):
        # **La question porte sur les dégâts ou sur le compte, pas sur le tir
        # unique.** Mener par « il ne tue pas d'une balle » serait répondre à
        # côté avec des chiffres justes — le pire cas.
        au_bareme = data["degats_par_balle"] * data["mult_zone"]
        maxi = max((s["degats_au_max"] for _, s in data["scenarios"]),
                   default=au_bareme)
        if volet == "degats":
            lignes.append(
                f"- **{_nombre(round(au_bareme, 1))} dégâts par balle** au "
                f"barème du catalogue, jusqu'à {_nombre(round(maxi, 1))} au "
                f"mieux (qualité maximale et meilleur accessoire).")
        if plancher:
            lignes.append(
                f"- **{_plural(plancher['au_bareme'], 'balle')}** pour le "
                f"tuer au barème, {_plural(plancher['balles'], 'balle')} au "
                f"mieux.")
        if volet == "balles":
            lignes.append(
                f"- {_nombre(round(au_bareme, 1))} dégâts par balle, "
                f"{_nombre(round(maxi, 1))} au mieux.")

    # **Quand rien ne passe, quatre lignes de « jamais » n'apprennent rien.**
    # On garde le meilleur scénario — celui qui dit de combien ça rate — et on
    # bascule sur ce qui débloquerait la question.
    elif data["alternatives"]:
        meilleur = max(data["scenarios"], key=lambda s: s[1]["degats_au_max"])
        libelle, scenario = meilleur
        lignes.append(f"- **Non, à aucune qualité.** Au mieux ({libelle}, "
                      f"qualité {_nombre(scenario['borne'])}) : "
                      f"{_nombre(scenario['degats_au_max'])} dégâts sur "
                      f"{_nombre(data['pv'])}.")
    else:
        for libelle, scenario in data["scenarios"]:
            lignes.append(f"- **{libelle}** : {_verdict(scenario, data['pv'])}"
                          f"{_effet_accessoire(scenario['accessoire'])}")

    # **Le détail par type, parce que le total ment.** L'Atzkav affiche 165
    # d'alpha et n'en place que 120 dans des points de vie : sans cette ligne,
    # le lecteur qui a vu 165 sur une fiche croit à une erreur de calcul.
    passe = " + ".join(
        f"{_nombre(d['brut'])} {_de(d['type'])} × {_mult(d['resistance'])} "
        f"d'armure {classe}" for d in data["detail_degats"])
    lignes += ["", f"Le calcul : {passe}{gerbe}, × le multiplicateur de "
               f"qualité, × {_mult(data['mult_zone'])} pour la zone — il faut "
               f"atteindre {_nombre(data['pv'])} points de vie."]
    if data["degats_ecartes"]:
        pluriel = "nt" if len(data["degats_ecartes"]) > 1 else ""
        ecarte = ", ".join(f"{_nombre(d['brut'])} de {d['type']}"
                           for d in data["degats_ecartes"])
        lignes.append(f"_Son alpha affiché vaut {_nombre(data['alpha'])} : "
                      f"{ecarte} n'entre{pluriel} pas dans le calcul, ça ne "
                      f"retire aucun point de vie._")

    # **Combien de balles, et où le compte descend.** Demande de
    # l'utilisateur : « à 250 il en faut 8, à partir de 550 sept ». On ne
    # l'affiche que si l'arme ne tue jamais d'une balle — sinon la réponse est
    # déjà donnée plus haut.
    demande = volet in ("balles", "degats")
    if data["paliers"] and (demande
                            or all(p["balles"] > 1 for p in data["paliers"])):
        lignes += ["", "Nombre de balles selon la qualité :"]
        for palier in data["paliers"][:6]:
            # **« de base » et « au barème » ne sont pas la même chose**, et
            # les afficher côte à côte donnait 11 et 10 sans dire pourquoi :
            # le premier est la qualité 0, le second le multiplicateur 1. On
            # nomme la qualité, qui est la vraie variable.
            depart = ("à la qualité 0" if palier["qualite"] <= 0
                      else f"à partir de {_nombre(palier['qualite'])}")
            # **Deux décimales ici, et pas une.** À l'arrondi au dixième,
            # 9,99 et 10,00 s'affichent tous deux « 10,0 » — et deux lignes
            # de comptes différents semblaient porter le même chiffre.
            degats = f"{palier['degats']:.2f}".replace(".", ",")
            # `_plural` **porte déjà le nombre** : le préfixer donnait
            # « 11 11 balles » à l'écran. Défaut signalé par l'utilisateur.
            lignes.append(f"- **{_plural(palier['balles'], 'balle')}** "
                          f"{depart} _({degats} dégâts par balle)_")
        borne_basse = _plancher(data)
        if borne_basse:
            lignes.append(borne_basse)

    # Un fusil à pompe ne place pas ses huit plombs dans une tête à cinquante
    # mètres : le chiffre est exact, la condition ne va pas de soi.
    if data["plombs"] > 1:
        lignes.append(f"_À condition que les {_nombre(data['plombs'])} plombs "
                      f"touchent la tête — c'est un tir à bout portant._")

    if len(data["composants"]) > 1:
        lignes.append(f"_Hypothèse : les {len(data['composants'])} composants "
                      f"qui portent les dégâts ({', '.join(data['composants']).lower()}) "
                      f"sont fabriqués à la même qualité. Leurs écarts au "
                      f"barème s'additionnent comme sur le terminal du P6-LR ; "
                      f"l'application à cette arme reste une extrapolation._")

    if data["sans_casque"]:
        sc = data["sans_casque"]
        if sc["toujours"]:
            lignes.append(f"**Sans casque, c'est acquis** : la tête encaisse six "
                          f"fois, soit {_nombre(sc['degats'])} dégâts, à toute "
                          f"qualité.")
        elif sc["qualite"] is not None:
            lignes.append(f"Sans casque : à partir de la qualité "
                          f"{_nombre(sc['qualite'])}.")
        else:
            lignes.append(f"Même sans casque tu plafonnes à "
                          f"{_nombre(sc['degats'])} dégâts.")

    if data["accessoire_introuvable"]:
        lignes.append(f"_Je n'ai pas trouvé « {data['accessoire_introuvable']} » "
                      f"parmi les accessoires montables sur cette arme._")
    if data["variantes"]:
        lignes.append("_Mêmes chiffres sur " + ", ".join(data["variantes"]) + "._")
    return "\n".join(lignes)


_RENDERERS["qualite_pour_tuer"] = render_qualite_pour_tuer


# ------------------------------------------------------ hors de l'univers

def render_hors_univers(data: dict) -> str:
    """« La distance entre la Terre et Proxima b » — je ne connais pas.

    Dire ce qu'on **sait** vaut mieux que dire ce qu'on ignore : la liste des
    systèmes est courte, elle tient dans la phrase, et elle transforme un
    refus en repère.
    """
    systemes = data["systemes"]
    build = data.get("build") or {}
    version = (f" de la {build['game_version']}" if build.get("game_version")
               else "")
    return (
        f"Je ne connais que **Star Citizen**, et seulement ce que les "
        f"fichiers du jeu{version} publient : "
        f"{', '.join(systemes[:-1])} et {systemes[-1]} — "
        f"{_nombre(len(systemes))} systèmes, leurs planètes, leurs stations, "
        f"leurs vaisseaux et leurs objets.\n\n"
        f"Rien sur l'astronomie réelle : ni la Terre, ni Proxima, ni les "
        f"distances entre étoiles du vrai ciel. Pose-moi la même question "
        f"dans le jeu — « la distance entre Lorville et Orison », par "
        f"exemple — et je la calcule sur le starmap."
    )


_RENDERERS["hors_univers"] = render_hors_univers


def render_hors_donnees(data: dict) -> str:
    """« Quand sort Squadron 42 », « il y a quoi dans la 4.10 ».

    Le LLM répondrait de mémoire — plausible, précis, et daté. Dire de quel
    build on parle est la seule chose vraie qu'on ait, et elle est utile :
    elle dit aussi quand la réponse changera.
    """
    build = data.get("build") or {}
    version = build.get("game_version") or "?"
    numero = build.get("build_id")
    return (
        f"Je ne lis que **les fichiers du jeu installé** — actuellement la "
        f"**{version}**" + (f" (build {numero})" if numero else "") + ". Ils "
        f"portent les vaisseaux, les objets, les missions et la carte de ce "
        f"build, et **rien** sur ce qui n'est pas encore livré : ni date de "
        f"sortie, ni feuille de route, ni notes de patch.\n\n"
        f"Dès que la version suivante est installée et ingérée, je réponds "
        f"dessus — et `disco verifier` dit ce qui a changé. En attendant, je "
        f"préfère te le dire plutôt que te donner une date de mémoire."
    )


_RENDERERS["hors_donnees"] = render_hors_donnees


# ------------------------------------------------- emports d'armure

def render_emports_armure(data) -> str:
    """« Combien je peux porter de chargeur avec une armure moyenne ? »"""
    if not data.emports:
        return (f"**{data.titre}** ne porte aucun emplacement : c'est une pièce "
                f"qui protège, elle ne se charge pas. Le torse et les jambes "
                f"portent l'équipement.")

    lignes = [f"**{data.titre}** :", ""]
    par_piece: dict[str, list] = {}
    for e in data.emports:
        par_piece.setdefault(e.piece, []).append(e)

    for piece, emports in par_piece.items():
        if len(par_piece) > 1:
            lignes.append(f"*{piece.capitalize()}*")
        for e in emports:
            lignes.append(f"- {_nombre(e.nombre)} {e.libelle}")
        if len(par_piece) > 1:
            lignes.append("")

    if data.sac:
        detail = ", ".join(f"{_nombre(e.nombre)} {e.libelle}" for e in data.sac)
        lignes += [f"Un sac à dos personnel ajoute {detail}.", ""]

    # **Dire ce qui ne varie pas.** Un joueur suppose qu'une armure lourde
    # porte plus de medpens ; les jambes sont identiques dans les trois
    # classes, et le silence laisserait croire le contraire.
    if data.invariantes:
        quoi = " et ".join(f"les {i}" if i.endswith("s") else f"le {i}"
                           for i in data.invariantes)
        lignes.append(f"Le nombre d'emplacements sur {quoi} est le même en "
                      f"légère, moyenne et lourde — seul le torse change.")

    if data.autres_classes:
        lignes += ["", "Ce que la classe change :"]
        for famille, par_classe in data.autres_classes.items():
            libelle = _LIBELLE_EMPORT.get(famille, famille)
            detail = ", ".join(f"{_nombre(n)} en {cl}"
                               for cl, n in par_classe.items())
            lignes.append(f"- {libelle.capitalize()} : {detail}")

    return "\n".join(lignes).rstrip()


# Le libellé pluriel d'une famille, pour la comparaison entre classes. Défini
# ici plutôt qu'importé : `render` ne remonte pas vers les modules métier.
_LIBELLE_EMPORT = {
    "wep_stocked": "armes d'épaule", "wep_sidearm": "armes de poing",
    "magazine_attach": "chargeurs de rechange", "magAttach": "chargeurs de rechange",
    "grenade_attach": "lancers de combat", "medPen_attach": "medpens",
    "oxyPen_attach": "oxypens", "utility_attach": "objets utilitaires",
    "gadget_attach": "gadgets", "backpack": "sacs à dos",
}

_RENDERERS["emports_d_armure"] = render_emports_armure


# ------------------------------------------- classement d'équipement personnel

def _pluriel_de_famille(famille: str) -> str:
    """« moteur quantique » → « moteurs quantiques », « sac à dos » → idem.

    Un `+ "s"` sur le groupe entier donnait « les moteur quantiques » : la
    marque du pluriel tombe sur le **premier** mot, et l'adjectif qui suit
    l'accorde aussi. Les compléments introduits par une préposition ne
    prennent rien — « sacs à dos », pas « sacs à doss ».
    """
    mots = (famille or "équipement").split()
    if not mots:
        return "équipement"
    tete, reste = mots[0], mots[1:]
    accorde = [au_pluriel(tete)]
    for mot in reste:
        # Le complément commence à la première préposition : ce qui suit
        # appartient au nom et ne s'accorde pas.
        if accorde[-1] in ("à", "a", "de", "du", "en") or mot in (
                "à", "a", "de", "du", "en"):
            accorde.append(mot)
        else:
            accorde.append(au_pluriel(mot))
    return " ".join(accorde)


def render_classement_equipement(data) -> str:
    """« Quelle armure pour une lune glacée », « quel missile va le plus loin »."""
    rarete = f" {data.rarete.lower()}" if data.rarete else ""
    # La phrase accordait au féminin pluriel quel que soit le sujet — « les
    # points de bouclier les plus élevées », « Les moteur quantiques ». Le
    # classement ne portait que des armures jusqu'aux composants de
    # vaisseau ; depuis, le libellé peut être masculin et la famille un
    # groupe nominal dont le pluriel tombe sur le **premier** mot.
    sens = "le plus bas" if data.moins_est_mieux else "le plus élevé"
    budget = (f" sous {_nombre(data.budget)} aUEC"
              if getattr(data, "budget", None) is not None else "")
    systeme = (f" dans **{data.systeme}**"
               if getattr(data, "systeme", None) else "")
    taille = (f" de taille {data.taille}"
              if getattr(data, "taille", None) is not None else "")
    # Une borne appliquée mais tue est indiscernable d'une borne oubliée.
    borne = ""
    if getattr(data, "seuil", None) and getattr(data, "valeur", None) is not None:
        mot = "plus" if data.seuil == ">=" else "moins"
        borne = f" à {mot} de {_nombre(data.valeur)} {data.libelle}"
    lignes = [f"Les {_pluriel_de_famille(data.famille)}{rarete}{taille}"
              f"{borne}{budget}{systeme}, {data.libelle} {sens} d'abord "
              f"— sur {_nombre(data.total)} mesurés :", ""]

    for titre, groupe in data.groupes:
        if titre:
            lignes.append(f"*{titre.capitalize()}*")
        for l in groupe:
            valeur = _nombre(round(l.valeur, 3))
            unite = f" {data.unite}" if data.unite else ""
            prix = (f" — {_nombre(l.prix)} aUEC"
                    if getattr(l, "prix", None) is not None else "")
            lignes.append(f"- **{l.nom}** : {valeur}{unite}{prix}")
        if titre:
            lignes.append("")

    # Ce que le budget a écarté se dit — un objet sans relevé n'est pas
    # gratuit, il est inconnu (grille, ligne 9).
    if (getattr(data, "budget", None) is not None
            or getattr(data, "systeme", None)):
        ecarts = []
        if data.hors_budget:
            ecarts.append(f"{_nombre(data.hors_budget)} au-dessus du budget")
        if data.sans_prix:
            ecarts.append(f"{_nombre(data.sans_prix)} sans prix relevé — "
                          "sans relevé n'est pas gratuit")
        if getattr(data, "hors_systeme", 0):
            ecarts.append(
                f"{_nombre(data.hors_systeme)} sans point de vente relevé "
                f"dans {data.systeme}")
        if ecarts:
            lignes.append(f"_Écartées : {' ; '.join(ecarts)}. Prix relevés "
                          "par les joueurs sur UEX._")

    # **Un multiplicateur bas protège mieux, et ça ne va pas de soi.** Sans
    # cette phrase, un lecteur lit « 0,125 » comme une mauvaise note.
    if data.moins_est_mieux and data.libelle.startswith("encaissement"):
        lignes.append("Un multiplicateur bas protège mieux : 0,125 arrête "
                      "sept dégâts sur huit.")
    return "\n".join(lignes).rstrip()


_RENDERERS["classer_equipement"] = render_classement_equipement


def _balles(n: int) -> str:
    return f"{_nombre(n)} balle{'s' if n > 1 else ''}"


def render_jalons_de_qualite(data: dict[str, Any]) -> str:
    """« tête, armure légère — dès la qualité 65 (ou 0 avec un Torrent
    Compensator2) » — le format validé par l'utilisateur le 2026-08-13 :
    **une ligne par cible**, la parenthèse portant ce que l'accessoire
    change. Quand l'OS n'existe pas, c'est le **compte de balles** qui
    mène la ligne, avec la qualité qui le réduit — les dégâts seuls ne
    disent pas quoi faire du fusil.
    """
    nom = data["nom"]
    acc = (data.get("accessoire") or {}).get("nom")
    zones = {"tete": "tête", "torse": "torse", "jambes": "jambes"}
    classes = {"legere": "armure légère", "moyenne": "armure moyenne",
               "lourde": "armure lourde"}

    def compte_de_balles(paliers, paliers_avec) -> str:
        # Le minimum, la qualité qui l'atteint, le point de départ — et la
        # parenthèse si l'accessoire fait tomber une balle de plus.
        if not paliers:
            return ""
        mini = paliers[-1]
        texte = f"**{_balles(mini['balles'])}**"
        if len(paliers) > 1:
            texte += (f" dès la qualité {_nombre(int(mini['qualite']))} "
                      f"({_nombre(paliers[0]['balles'])} à la qualité 0)")
        else:
            texte += " à toute qualité"
        if paliers_avec and acc \
                and paliers_avec[-1]["balles"] < mini["balles"]:
            gain = paliers_avec[-1]
            texte += (f" — {_balles(gain['balles'])} avec le {acc}, dès "
                      f"{_nombre(int(gain['qualite']))}")
        return texte

    possibles, jamais = [], []
    for j in data["jalons"]:
        cible = f"{zones[j['zone']]}, {classes[j['classe']]}"
        sans, avec = j["qualite"], j["qualite_avec"]
        if sans is not None:
            texte = f"{cible} — dès la qualité **{_nombre(int(sans))}**"
            if avec is not None and avec < sans and acc:
                texte += f" (ou **{_nombre(int(avec))}** avec le {acc})"
            possibles.append((sans, texte))
        elif avec is not None and acc:
            possibles.append((
                avec,
                f"{cible} — dès **{_nombre(int(avec))}** avec le {acc} "
                "(jamais sans, même à qualité max)"))
        else:
            balles = compte_de_balles(j.get("balles") or [],
                                      j.get("balles_avec") or [])
            queue = (f"{balles} · {j['degats_au_max']:.0f} dégâts max par "
                     "balle sur 100" if balles
                     else f"{j['degats_au_max']:.0f} dégâts au grand maximum "
                          "sur 100")
            jamais.append((j["degats_au_max"], f"{cible} — {queue}"))

    # La tête sans casque en tête de liste : c'est la cible que tout joueur
    # vise, et le ×6 sans résistance change complètement le verdict.
    sc = data.get("sans_casque")
    if sc is not None:
        if sc["qualite"] is not None:
            texte = "tête sans casque — dès la qualité " \
                    f"**{_nombre(int(sc['qualite']))}**"
            possibles.insert(0, (-1.0, texte))
        else:
            balles = compte_de_balles(sc.get("balles") or [], [])
            queue = (f"{balles} · {sc['degats_au_max']:.0f} dégâts max par "
                     "balle sur 100" if balles
                     else f"{sc['degats_au_max']:.0f} dégâts au grand "
                          "maximum sur 100")
            jamais.insert(0, (float("inf"), f"tête sans casque — {queue}"))

    lignes = [f"**Les jalons de qualité du {nom}** — ce que la qualité "
              "d'atelier change :"]
    if possibles:
        lignes.append("\n**One-shot possible** :")
        for _, texte in sorted(possibles, key=lambda x: x[0]):
            lignes.append(f"- {texte}")
        if jamais:
            lignes.append("\n**Jamais d'une balle**, même à qualité 1 000"
                          + (f" avec le {acc}" if acc else "") + " :")
    elif jamais:
        # L'arme n'OS nulle part : le compte de balles est la réponse.
        lignes.append("\n**Aucun one-shot**, à aucune qualité — le compte "
                      "de balles, lui, descend avec la qualité :")
    for _, texte in sorted(jamais, key=lambda x: -x[0]):
        lignes.append(f"- {texte}")
    lignes.append("\n_Règles de terrain : 100 PV, tête ×1,5, torse ×1, "
                  "jambes ×0,8, tête sans casque ×6 sans résistance ; la "
                  "résistance est celle de la pièce la plus répandue de "
                  "chaque classe. Le reste sort des fichiers du jeu._")
    return "\n".join(lignes)


_RENDERERS["jalons_de_qualite"] = render_jalons_de_qualite


_ZONE_MOT = {"tete": "la tête", "torse": "le torse", "jambes": "les jambes"}
_CLASSE_MOT = {"legere": "légère", "moyenne": "moyenne", "lourde": "lourde"}


def _cible_lisible(bloc: dict) -> str:
    """« la tête en armure lourde », « la tête sans casque »."""
    if bloc.get("sans_casque"):
        return "la tête sans casque"
    zone = _ZONE_MOT.get(bloc.get("zone") or "", "la cible")
    classe = _CLASSE_MOT.get(bloc.get("classe") or "")
    return f"{zone} en armure {classe}" if classe else zone


def _ce_que_ca_debloque(bloc: dict) -> str:
    """« pour tuer d'une balle la tête en armure lourde »."""
    balles = bloc.get("balles")
    cible = _cible_lisible(bloc)
    if balles == 1:
        return f"pour tuer d'une balle {cible}"
    if balles:
        return f"pour descendre à {balles} balles sur {cible}"
    return f"pour {cible}"


#: Les libellés des zones et des classes d'armure. **Au niveau du module,
#: pas dans la fonction** : `test_aucun_nom_de_module_ne_manque_dans_le_rendu`
#: le vérifie statiquement, et il a raison — une constante définie dans un
#: corps de fonction se perd à la première réécriture du bloc, ce que le
#: projet a déjà payé deux fois sur `_DIFFICULTE`.
_ZONES_DU_CORPS = {"tete": "la tête", "torse": "le torse",
                   "jambes": "les jambes"}
_CLASSES_D_ARMURE = {"legere": "légère", "moyenne": "moyenne",
                     "lourde": "lourde"}


def _zone_lisible(zone: dict) -> str:
    """Le nom francais d une zone du corps, sinon sa cle telle quelle."""
    return _ZONES_DU_CORPS.get(zone["zone"], zone["zone"])


def _deux_chemins(cible: dict) -> str:
    """« À la qualité 262 avec un Torrent, 380 sans. »

    **Le raccourci par accessoire était calculé et jeté.** Le rendu ne
    listait l'accessoire que pour les cas *impossibles* sans lui ; dès
    qu'un cas passait aussi à nu, seule la qualité nue s'affichait — et
    l'accessoire, qui fait gagner plus de cent points de qualité, restait
    invisible. Demande de l'utilisateur, 2026-08-20 : « tu devais aussi me
    sortir la même ligne si c'était juste une question d'accessoire ».

    Le chiffre le plus **bas** vient en premier : c'est celui qui décide
    de ce qu'on va fabriquer. Un accessoire qui ne fait rien gagner ne se
    mentionne pas — le citer sans gain le ferait passer pour utile.
    """
    nue = int(cible["qualite"])
    avec = cible.get("qualite_avec")
    accessoire = cible.get("accessoire")
    if avec is None or accessoire is None or avec >= cible["qualite"]:
        return f"dès la qualité **{nue}**"
    # Un seuil à zéro veut dire « n'importe quelle qualité » : « à partir
    # de 0 » se lit comme une donnée manquante.
    debut = (f"**d'emblée** avec un {accessoire}" if avec <= 0
             else f"dès la qualité **{int(avec)}** avec un {accessoire}")
    return f"{debut}, **{nue}** sans"


#: En dessous, l'écart se rattrape — c'est le meilleur gain qu'un accessoire
#: de dégâts apporte, mesuré sur les 431 accessoires montables : 419 valent
#: exactement 1, et les rares qui comptent plafonnent autour de +25 %.
#: Au-delà, aucune configuration ne referme l'écart, et le détail n'aide pas.
_ECART_RATTRAPABLE_PCT = 25.0


def _hors_de_portee(jamais: list[dict], il_y_a_mieux: bool) -> list[str]:
    """Ce qui ne tuera jamais d'une balle — en une ligne, pas en neuf.

    **La règle « *jamais* se chiffre » visait le quasi-succès** : dire
    « il te manque 1 %, un accessoire suffit » plutôt que « jamais ».
    Appliquée à une arme qui n'est pas faite pour ça, elle produisait neuf
    lignes disant toutes « jamais » avec des écarts de 42 à 77 % —
    remarque de l'utilisateur, 2026-08-20 : « il faut que le modèle arrête
    de toujours vouloir lister qu'il n'est pas possible d'OS quelqu'un
    avec une arme qui n'est clairement pas faite pour ça ».

    On garde donc le chiffre là où il **décide** — un écart rattrapable
    par un accessoire — et on résume d'une phrase quand rien n'est à
    portée. Le seuil n'est pas arbitraire : il vaut le meilleur gain qu'un
    accessoire de dégâts apporte réellement.
    """
    if not jamais:
        return []
    ecarts = [c["manque_pct"] for c in jamais if c.get("manque_pct") is not None]
    proches = [c for c in jamais
               if (c.get("manque_pct") or 100.0) <= _ECART_RATTRAPABLE_PCT]

    # Un écart à portée reste détaillé : c'est là que le chiffre sert.
    lignes = [f"- {_cible_lisible(c)} : hors de portée de peu — il manque "
              f"{str(c['manque_pct']).replace('.', ',')} % des points de vie"
              for c in sorted(proches, key=lambda c: c["manque_pct"])]
    loin = [c for c in jamais if c not in proches]
    if not loin:
        return lignes
    if not il_y_a_mieux and not proches and ecarts:
        # Aucun cas ne marche : une phrase, et le meilleur écart pour dire
        # de combien on est loin. Neuf lignes de « jamais » ne se lisent pas.
        return [f"- nulle part — cette arme ne tue pas d'une balle, quelle "
                f"que soit la qualité : au mieux il manque "
                f"{str(min(ecarts)).replace('.', ',')} % des points de vie. "
                f"Ce n'est pas ce qu'elle fait."]
    return lignes + [f"- ailleurs, jamais — les {len(loin)} autres cas "
                     f"demandent bien plus que ce qu'une qualité ou un "
                     f"accessoire peut donner."]


def render_qualite_maximale_utile(data: dict) -> str:
    """« Jusqu'à quelle qualité ça vaut le coup ? »

    La question fondatrice du projet. **La réponse s'organise par
    objectif de jeu, pas par chiffre** — remarque de l'utilisateur du
    2026-08-13 : « ça devrait être : 956 si on veut pouvoir OS dans un
    torse léger avec un compensator, et 995 si on veut pouvoir porter un
    silencieux et continuer à OS ».

    Un seuil nu ne dit rien à personne. Ce qu'un joueur décide, c'est
    *ce qu'il veut pouvoir faire* ; la qualité en découle. Chaque ligne
    porte donc son objectif, et les configurations qui aboutissent au
    même seuil se regroupent.

    Le piège que ce rendu doit éviter : **annoncer le seuil de l'arme nue
    comme *la* limite**. Il ferait renoncer à une qualité qui sert dès
    qu'un accessoire est monté — 771 à nu contre 995 avec un silencieux
    sur le P6-LR.
    """
    nom = data["nom"]
    seuil = data.get("seuil")
    borne = int(data.get("borne") or 1000)
    if seuil is None:
        return (f"Je n'ai pas de palier chiffrable pour **{nom}** : son "
                "blueprint ne publie pas de modificateur de dégâts "
                "interpolable, donc rien ne dit ce que la qualité change.")

    seuil = int(seuil)
    plafond = int(data.get("plafond") or seuil)

    # Un objectif par seuil : les configurations qui aboutissent au même
    # chiffre se regroupent (les deux Sion valent ×0,9 toutes les deux).
    objectifs: dict[int, dict] = {
        seuil: {"seuil": seuil, "configs": ["à nu"],
                "gain": _ce_que_ca_debloque(data)}}
    for acc in data.get("accessoires") or []:
        cle = int(acc["seuil"])
        etiquette = ("un silencieux " if acc.get("silencieux") else "un ")             + acc["nom"]
        if cle in objectifs:
            objectifs[cle]["configs"].append(f"avec {etiquette}")
        else:
            objectifs[cle] = {"seuil": cle, "configs": [f"avec {etiquette}"],
                              "gain": _ce_que_ca_debloque(acc)}

    lignes = [f"**{nom} — la qualité à viser dépend de ce que tu veux "
              "faire :**", ""]
    for objectif in sorted(objectifs.values(), key=lambda o: o["seuil"]):
        configs = objectif["configs"]
        # On cite deux configurations au plus : au-delà, c'est une liste
        # d'accessoires, plus un conseil.
        dit = configs[0] if len(configs) == 1 else (
            f"{configs[0]} ou {configs[1]}" if len(configs) == 2
            else f"{configs[0]} (ou {len(configs) - 1} autres)")
        lignes.append(f"- **{objectif['seuil']}** — {dit}, "
                      f"{objectif['gain']}")

    # **Les paliers ci-dessus sont ceux d'une seule zone**, celle qui pousse
    # le plafond le plus haut. Un lecteur qui n'y voit que « sur la tête »
    # en déduit que le reste ne bouge pas — c'est ce qui est arrivé le
    # 2026-08-20 : « ça veut dire qu'aucune qualité ne baisse le nombre de
    # balles pour tuer dans le torse ? ». La réponse était non, et elle
    # était calculée : au torse, le P8-AR passe à 3 balles à 889.
    #
    # Une réponse doit dire **sur quoi elle classe** — la règle du
    # raffinage, où le premier d'un cumul n'est le premier d'aucun axe.
    par_zone = data.get("par_zone") or []
    if len(par_zone) > 1:
        lignes += ["", "**Et pour les autres zones**, la qualité paye "
                       "aussi — les paliers ci-dessus sont ceux qui montent "
                       "le plus haut, pas les seuls :"]
        for zone in par_zone:
            classe = _CLASSES_D_ARMURE.get(zone.get("classe") or "", "")
            lignes.append(
                f"- {_zone_lisible(zone)} : "
                f"**{zone['qualite']:g}** pour descendre à "
                f"{zone['balles']} balle{'s' if zone['balles'] > 1 else ''}"
                + (f" en armure {classe}" if classe else ""))

    perdu = borne - plafond
    if perdu > 0:
        lignes += ["", f"Au-delà de **{plafond}**, plus rien ne bouge quelle "
                       f"que soit ta configuration : les {perdu} derniers "
                       f"points jusqu'à {borne} sont payés pour rien."]

    carte = data.get("os") or []
    possibles = [c for c in carte if c.get("qualite") is not None]
    par_accessoire = [c for c in carte
                      if c.get("qualite") is None
                      and c.get("qualite_avec") is not None]
    jamais = [c for c in carte if c.get("qualite") is None
              and c.get("qualite_avec") is None]
    if carte:
        lignes += ["", "**Où tu tues d'une balle :**"]
        for cible in sorted(possibles, key=lambda c: c["qualite"]):
            lignes.append(f"- {_cible_lisible(cible)} — {_deux_chemins(cible)}")
        for cible in sorted(par_accessoire, key=lambda c: c["qualite_avec"]):
            lignes.append(
                f"- {_cible_lisible(cible)} — **seulement avec un "
                f"{cible['accessoire']}**, et à partir de "
                f"**{int(cible['qualite_avec'])}**")
        lignes += _hors_de_portee(jamais, bool(possibles or par_accessoire))

    demande = data.get("accessoire_demande")
    if demande:
        ecart = int(demande["seuil"]) - seuil
        if ecart < 0:
            lignes += ["", "Un seuil qui descend ne veut pas dire qu'on "
                           "gagne plus tôt : le gain suivant devient "
                           f"hors de portée, il demanderait plus de {borne}."]
    if data.get("accessoire_introuvable"):
        lignes += ["", f"Je ne trouve pas d'accessoire « "
                       f"{data['accessoire_introuvable']} » montable sur "
                       "cette arme — le port a ses propres exigences, la "
                       "taille ne suffit pas."]

    lignes += ["", f"*Calculé sur {data.get('scenarios', 10)} scénarios : "
                   "chaque impact touche une seule pièce, donc les trois "
                   "zones croisées avec les trois classes d'armure, plus la "
                   "tête sans casque. Base : 100 PV, tête ×1,5, torse ×1,0, "
                   "jambes ×0,8, tête nue ×6, et la pièce la plus répandue "
                   "de sa classe.*"]
    return "\n".join(lignes)


_RENDERERS["qualite_maximale_utile"] = render_qualite_maximale_utile
