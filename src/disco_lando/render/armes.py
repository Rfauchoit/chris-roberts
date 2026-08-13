"""Armes et objets : fiches, comparaisons, accessoires, variantes."""

from __future__ import annotations

from typing import Any

from .socle import (
    _RENDERERS,
    _mult,
    _nom_stat,
    _nombre,
    _plural,
    dit_stat,
    enumerate_fr,
    question_de_suite,
)


# `filter` arrive en anglais parce que ce sont les valeurs de `item_stats` —
# c'est la base qui parle, et elle a raison de ne pas traduire. Mais ce texte
# part chez Piper : « en ballistic cannon » lu par une voix française est
# inaudible. La traduction est donc ici, au dernier moment, comme le reste de
# la mise en forme.
_FAMILLES = {
    "ballistic": "balistique", "laser": "laser", "plasma": "plasma",
    "tachyon": "tachyon", "distortion": "à distorsion",
    "cannon": "canon", "gatling": "gatling", "repeater": "répéteur",
    "scattergun": "fusil à dispersion",
}


def _famille_fr(brut: str, defaut: str) -> str:
    """« ballistic cannon » → « canon balistique ».

    L'ordre s'inverse au passage : en anglais la classe précède le genre, en
    français l'adjectif suit le nom.
    """
    if not brut:
        return defaut
    mots = [_FAMILLES.get(m, m) for m in brut.split()]
    return " ".join(reversed(mots)) if len(mots) > 1 else mots[0]


def render_compatible(data: dict[str, Any]) -> str:
    ship = data["ship"]
    if not data["items"]:
        return (f"{ship['name']} n'a pas d'emplacement correspondant, ou aucune "
                f"arme du catalogue n'y va.")
    tailles = enumerate_fr([f"S{t}" for t in data["sizes"]])
    famille = _famille_fr(data["filter"], "arme")
    tete = data["items"][:3]
    texte = (f"{ship['name']} accepte des armes en taille {tailles}. "
             f"En {famille}, les plus fortes en DPS sont "
             + enumerate_fr([f"{i['name']} S{i['size']} à {i['dps']:.0f} DPS"
                             for i in tete], limit=3))
    if len(data["items"]) > 3:
        texte += f", sur {len(data['items'])} compatibles"
    return texte + "."


def render_comparison(data: dict[str, Any]) -> str:
    """Classement d'une famille d'armes, en liste — la réponse est écrite.

    L'ancienne version enfilait huit tailles dans une seule phrase, parce
    qu'elle était écrite pour être lue à voix haute. À l'écrit c'est
    illisible, et Discord rend les listes.
    """
    label, unite = data["stat_label"], data["stat_unit"]
    famille = _famille_fr(data["filter"], "armes")
    stat = data["stat"]

    if data.get("size"):
        tete = data["items"][:10]
        lignes = [f"{i + 1}. {obj['name']} — {_nombre(obj[stat], unite)}"
                  for i, obj in enumerate(tete)]
        entete = f"**{famille.capitalize()} de taille {data['size']}**, par {label} :"
        return entete + "\n" + "\n".join(lignes)

    # Sans taille précisée : le meilleur de chaque taille. Comparer un S1 à un
    # S8 ne veut rien dire, et le S8 est souvent un canon de capital-ship.
    lignes = [f"- **S{g['size']}** — {g['best']['name']}, {_nombre(g['best'][stat], unite)}"
              for g in data["by_size"]]
    return (f"**{famille.capitalize()}**, meilleur {label} par taille :\n"
            + "\n".join(lignes)
            + "\n\nPrécise une taille pour le classement complet.")


def note_de_variantes(partagent: list[str] | None,
                      sans_reponse: list[str] | None,
                      ecartes: list[str] | None = None) -> str:
    """Ce que les autres variantes deviennent, quand on n'a pas posé la question.

    On répond pour celle qui a une réponse, mais taire les autres reviendrait
    à effacer des objets qui existent. Trois mentions distinctes : celles qui
    tombent avec, celles qui n'ont rien, et le **chargeur** — écarté de la
    question parce que « le P8-AR » désigne l'arme, mais qui a bel et bien sa
    propre réponse. Ne pas le dire ferait croire qu'il n'existe pas ; en faire
    une question ferait payer au joueur une ambiguïté qui n'est que la nôtre.
    """
    morceaux = []
    if partagent:
        morceaux.append("\n\nMême réponse pour "
                        + enumerate_fr(list(partagent), limit=4) + ".")
    if ecartes:
        verbe = "a sa" if len(ecartes) == 1 else "ont leur"
        morceaux.append("\n\n" + enumerate_fr(list(ecartes), limit=4)
                        + f" {verbe} propre réponse — demande-la si c'est "
                        "elle que tu cherchais.")
    if sans_reponse:
        verbe = "n'a" if len(sans_reponse) == 1 else "n'ont"
        morceaux.append("\n\n⚠️ En revanche "
                        + enumerate_fr(list(sans_reponse), limit=4)
                        + f" {verbe} rien à ce sujet.")
    return "".join(morceaux)


def reserve_de_confiance(doute: bool) -> str:
    """La réserve posée sous une réponse dont le routage n'était pas sûr.

    Idée de l'utilisateur (2026-08-10), et elle remplace un détour qui
    coûtait cher : sous le seuil de confiance, le cœur consultait
    l'analyste d'office — 10 à 100 secondes et du quota — avant de
    retomber le plus souvent sur la réponse déterministe qu'il avait déjà.
    On répond donc tout de suite et on **dit** qu'on n'est pas sûr.

    La proposition doit être exécutable, comme toute suite annoncée :
    « demande à Claude » est exactement le forçage que `dialogue` reconnaît,
    et la fenêtre de contexte lui repasse la question sans la retaper.
    """
    if not doute:
        return ""
    return ("\n\n---\n*Je ne suis pas certain d'avoir bien compris la "
            "question — cette réponse peut porter à côté. Dis « demande à "
            "Claude » pour une vérification.*")


def question_de_variante(noms: list[str]) -> str:
    """« Tu parles duquel ? Avenger Titan, Avenger Stalker, ou … ? »

    Demander vaut mieux que choisir à la place du joueur — mais seulement
    quand la réponse en dépend, sinon la question devient un péage.
    """
    if len(noms) == 1:
        return f"Tu parles bien de {noms[0]} ?"
    return f"Tu parles duquel ? {', '.join(noms[:-1])}, ou {noms[-1]} ?"


def question_de_doute(noms: list[str]) -> str:
    """« Je ne suis pas sûr. Tu veux dire Omnisky XII Cannon, ou … ? »

    Une approximation présentée comme une certitude est le pire cas : elle est
    chiffrée, détaillée, et fausse. Mieux vaut dire qu'on doute.
    """
    if len(noms) == 1:
        return f"Je ne suis pas sûr. Tu veux dire {noms[0]} ?"
    return (f"Je ne suis pas sûr. Tu veux dire {', '.join(noms[:-1])}, "
            f"ou {noms[-1]} ?")


# La colonne `oxygene` reste volontairement hors fiche : deux valeurs
# distinctes — 0,0015 et 0,02 — sur les 672 casques, dans une unité que le
# jeu ne publie pas. Une quasi-constante sans unité n'informe personne ;
# la mesure est dans le commit qui l'a écartée du classement.

# Les six axes de résistance d'une armure, dans l'ordre où on les subit.
_RESISTANCES_ARMURE = (
    ("armor_physical", "physique"), ("armor_energy", "énergie"),
    ("armor_distortion", "distorsion"), ("armor_thermal", "thermique"),
    ("armor_biochemical", "biochimique"), ("armor_stun", "étourdissement"),
)

# Les trois types de dégâts que le jeu distingue, renseignés sur 729 des 924
# fiches d'arme. La distorsion désactive sans détruire — face à un bouclier,
# c'est elle qui compte, et aucun outil ne la lisait.
_TYPES_DEGAT = (
    ("dps_physical", "physiques"),
    ("dps_energy", "énergétiques"),
    ("dps_distortion", "de distorsion"),
)


def render_item_stats(data: dict[str, Any]) -> str:
    """Fiche d'un objet, en Markdown — le bot répond à l'écrit.

    Pas de budget de lecture, pas de troncature à trois : une réponse écrite
    peut être complète, et Discord rend les listes. Les champs vides sont
    omis plutôt qu'affichés à zéro — un pistolet n'a pas de capacitor, et le
    dire vaut mieux que d'annoncer « 0 ».
    """
    item = data["item"]
    nom = item.get("name") or item.get("class_name")
    lignes: list[str] = []

    # Une question ciblée mérite une réponse ciblée. « Combien de balles a un
    # Coda » recevait huit lignes dont une seule répondait.
    if data.get("stat"):
        stat = data["stat"]
        texte = f"**{nom}** a {dit_stat(stat, item[stat])}."
        offres = [_nom_stat(v, article=True)
                  for v in data.get("voisines") or []]
        return texte + " " + question_de_suite(offres + ["la fiche complète"])

    # En-tête : ce qu'est l'objet, avant ce qu'il vaut.
    qualif = []
    # `_FAMILLES` porte aussi les types (canon, gatling…) ; `weapon_class` ne
    # prend que les cinq classes, les clés en trop sont sans effet. Le
    # doublon `_FAMILLE_ARME` a été fusionné ici le 2026-08-07.
    if famille := _FAMILLES.get(item.get("weapon_class") or ""):
        qualif.append(famille)
    if item.get("weapon_kind"):
        qualif.append(item["weapon_kind"])
    if item.get("size") is not None:
        qualif.append(f"taille {item['size']}")
    # 94 objets portent « <= PLACEHOLDER => » comme fabricant : c'est la façon
    # dont le jeu note une case vide, et l'afficher fait passer un trou pour
    # une information.
    if item.get("manufacturer_name") and "PLACEHOLDER" not in item["manufacturer_name"]:
        qualif.append(item["manufacturer_name"])
    entete = f"**{nom}**" + (f" — {', '.join(qualif)}" if qualif else "")

    if item.get("ammo_capacity") is not None:
        munitions = _nombre(item["ammo_capacity"], "coups")
        par_tir = item.get("ammo_per_shot")
        if par_tir and par_tir > 1:
            munitions += f", {_nombre(par_tir)} par tir"
        if data.get("autonomie_secondes"):
            munitions += (f" — soit {_nombre(data['autonomie_secondes'], 's')} "
                          f"de tir continu")
        lignes.append(f"- Munitions : {munitions}")

    if item.get("capacitor_max") is not None:
        detail = [_nombre(item["capacitor_max"], "unités")]
        if item.get("capacitor_regen"):
            detail.append(f"recharge {_nombre(item['capacitor_regen'])}/s")
        if item.get("capacitor_cost"):
            detail.append(f"{_nombre(item['capacitor_cost'])} par tir")
        if item.get("capacitor_cooldown"):
            detail.append(f"reprise après {_nombre(item['capacitor_cooldown'], 's')}")
        lignes.append(f"- Capacitor : {', '.join(detail)}")
    # Pas de ligne « Capacitor : aucun ». Le capacitor est une notion d'arme de
    # vaisseau à énergie ; sur un pistolet elle n'a pas de sens, et l'annoncer
    # absente encombre la réponse d'une non-information.

    if item.get("rounds_per_minute"):
        lignes.append(f"- Cadence : {_nombre(item['rounds_per_minute'], 'coups/min')}")
    if item.get("dps"):
        degats = _nombre(item["dps"], "DPS")
        if item.get("alpha"):
            degats += f", {_nombre(item['alpha'])} par tir"
        if item.get("pellets_per_shot") and item["pellets_per_shot"] > 1:
            degats += f" sur {_nombre(item['pellets_per_shot'])} projectiles"
        lignes.append(f"- Dégâts : {degats}")
    # Le détail par type : trois colonnes en base qu'aucun outil ne lisait.
    # Un zéro y est une **réponse** — une arme balistique ne fait aucun dégât
    # énergétique — donc on ne cite que les types non nuls, et seulement s'ils
    # ne redisent pas le total à eux seuls.
    ventilation = [(libelle, item.get(cle)) for cle, libelle in _TYPES_DEGAT]
    presents = [(l, v) for l, v in ventilation if v]
    if len(presents) > 1:
        lignes.append("- Répartition : "
                      + ", ".join(f"{_nombre(v)} {l}" for l, v in presents))
    if item.get("effective_range"):
        lignes.append(f"- Portée efficace : {_nombre(item['effective_range'], 'm')}")
    if item.get("projectile_speed"):
        lignes.append(f"- Vitesse du projectile : {_nombre(item['projectile_speed'], 'm/s')}")
    if item.get("fire_modes"):
        lignes.append(f"- Modes de tir : {item['fire_modes']}")

    # **Ce que la base sait d'autre que l'armement.** L'audit a ingéré les
    # armures, les missiles, les propulseurs et les réservoirs — et la fiche
    # n'affichait que la masse : plus de 3 600 lignes de statistiques
    # invisibles au joueur. Trouvé par la revue du balayage, qui « passait »
    # ces fiches parce qu'un texte qui ne dit rien n'a rien d'anormal. Même
    # règle que le capacitor : les champs vides sont omis, jamais à zéro.
    if item.get("armor_physical") is not None:
        paires = [(lib, item.get(cle)) for cle, lib in _RESISTANCES_ARMURE]
        presentes = [(lib, v) for lib, v in paires if v is not None]
        if presentes:
            # Les six axes valent souvent pareil sauf l'étourdissement : on
            # groupe les égaux pour ne pas écrire six fois le même nombre.
            par_valeur: dict[float, list[str]] = {}
            for lib, v in presentes:
                par_valeur.setdefault(v, []).append(lib)
            detail = ", ".join(f"×{_mult(v)} {'/'.join(libs)}"
                               for v, libs in par_valeur.items())
            lignes.append(f"- Encaissement des dégâts : {detail} "
                          "(plus bas = mieux protégé)")
    if item.get("temp_min") is not None and item.get("temp_max") is not None:
        lignes.append(f"- Températures supportées : de "
                      f"{_nombre(item['temp_min'], '°C')} à "
                      f"{_nombre(item['temp_max'], '°C')}")
    if item.get("radiation_max"):
        rads = _nombre(item["radiation_max"])
        if item.get("radiation_rate"):
            rads += f", dissipées à {_nombre(item['radiation_rate'])}/s"
        lignes.append(f"- Radiations avant saturation : {rads}")
    if item.get("gforce_resistance") is not None:
        lignes.append("- Modificateur de tenue aux G : "
                      f"{_mult(item['gforce_resistance'])}")
    if item.get("degats_missile"):
        lignes.append(f"- Dégâts du missile : {_nombre(item['degats_missile'])}")
    if item.get("portee_missile"):
        portee = _nombre(item["portee_missile"], "m")
        if item.get("vitesse_missile"):
            portee += f" à {_nombre(item['vitesse_missile'], 'm/s')}"
        lignes.append(f"- Portée : {portee}")
    if item.get("rayon_explosion"):
        lignes.append(f"- Rayon d'explosion : "
                      f"{_nombre(item['rayon_explosion'], 'm')}")
    if item.get("guidage") is not None and item.get("portee_missile"):
        lignes.append("- Guidage : "
                      + ("oui" if item["guidage"] else "non — tir tendu"))
    if item.get("poussee"):
        lignes.append(f"- Poussée : {_nombre(item['poussee'], 'N')}")
    if item.get("conso_par_10kn"):
        lignes.append(f"- Carburant pour 10 kN : "
                      f"{_mult(item['conso_par_10kn'])}")
    if item.get("capacite_scu"):
        lignes.append(f"- Capacité : {_nombre(item['capacite_scu'], 'SCU')}")

    if item.get("item_mass"):
        lignes.append(f"- Masse : {_nombre(item['item_mass'], 'kg')}")

    # « Combien de points d'énergie ? » — remarque du journal : la fiche
    # d'un composant du réseau dit ses pips (les barres), ses paliers et
    # ses signatures. Les armes se comptent en unités standard, jamais
    # converties en pips (docs/ANALYSE_ENERGIE.md §3).
    reseau = data.get("reseau")
    if reseau:
        if reseau.get("pips_conso"):
            pips = round(reseau["pips_conso"])
            detail = f"- Énergie : {_nombre(pips)} pip{'s' if pips > 1 else ''}"
            if reseau.get("mult_low") is not None:
                detail += (" à fond (×0,70 à 1 pip, ×0,85 à 2)"
                           if pips >= 3 else " à fond")
            lignes.append(detail)
        elif reseau.get("std_conso"):
            valeur = f"{reseau['std_conso']:g}".replace(".", ",")
            lignes.append(f"- Énergie : {valeur} unité(s) — hors pips, "
                          "les armes ont leur propre compte")
        if reseau.get("pips_generes"):
            lignes.append(f"- Production : "
                          f"{_nombre(round(reseau['pips_generes']))} pips "
                          "— la qualité de fabrication en ajoute "
                          "(« Power Pips »)")
        signatures = []
        if reseau.get("em"):
            signatures.append(f"EM {_nombre(round(reseau['em']))}")
        if reseau.get("ir"):
            signatures.append(f"IR {_nombre(round(reseau['ir']))}")
        if signatures:
            lignes.append(f"- Signatures : {', '.join(signatures)} "
                          "(plus bas = plus discret)")

    chargeurs = data.get("chargeurs") or []
    if len(chargeurs) > 1:
        noms = [f"{c['name']} ({_nombre(c['ammo_capacity'])})" for c in chargeurs]
        lignes.append(f"- Chargeurs compatibles : {', '.join(noms)}")

    if not lignes:
        return f"{entete}\n\nAucune statistique en base pour cet objet."
    return entete + "\n" + "\n".join(lignes)


def render_accessoires(data: dict[str, Any]) -> str:
    """« Sur un P8-AR : 12 optiques, 8 canons, 4 chargeurs. »

    On groupe par emplacement, parce que c'est ainsi que le joueur équipe son
    arme — un rail à la fois.
    """
    arme, familles = data["arme"], data["familles"]
    nom = arme.get("name") or "cette arme"
    if not familles:
        return (f"**{nom}** n'a aucun emplacement d'accessoire, "
                "ou aucun accessoire connu n'y entre.")

    lignes = [f"**{nom}** — {_plural(data['total'], 'accessoire')} "
              + ("compatibles" if data["total"] > 1 else "compatible") + " :", ""]
    for f in familles:
        noms = [i["name"] for i in f["items"]]
        reste = f["total"] - len(noms)
        suite = f" — et {_plural(reste, 'autre')}" if reste > 0 else ""
        lignes.append(f"**{f['libelle'].capitalize()}** ({_nombre(f['total'])}) : "
                      + ", ".join(noms) + suite)
    lignes.append("\n" + question_de_suite(["où en acheter"]))
    return "\n".join(lignes)


_RENDERERS["accessoires_compatibles"] = render_accessoires


def render_metrique(data: dict[str, Any]) -> str:
    """« En DPS soutenu : taille 1, le Panther à 210. »

    Par taille et jamais globalement : `mount_usable` ne distingue pas ce qu'un
    joueur peut monter, et un classement d'ensemble hissait le canon d'un
    porte-nefs en tête.
    """
    libelle = data["libelle"]
    unite = f" {data['unite']}" if data.get("unite") else ""
    famille = f" — {data['famille']}" if data.get("famille") else ""
    # `capitalize()` écrase les sigles : « DPS soutenu » devenait « Dps ».
    entete = libelle[0].upper() + libelle[1:] if libelle else libelle
    lignes = [f"**{entete}**{famille}, par taille :", ""]
    for groupe in data["par_taille"]:
        meilleur = groupe["best"]
        detail = (f"- **Taille {groupe['size']}** : {meilleur['name']} — "
                  f"{_nombre(round(meilleur['valeur'], 1))}{unite}")
        suivants = [f"{s['name']} ({_nombre(round(s['valeur'], 1))})"
                    for s in groupe["suivants"]]
        if suivants:
            detail += f", devant {enumerate_fr(suivants, limit=2)}"
        lignes.append(detail)
    lignes.append("\n*Classé par taille : le catalogue ne distingue pas ce "
                  "qu'un joueur peut monter d'un canon de capital-ship.*")
    return "\n".join(lignes)


def render_objets_seuil(data: dict[str, Any]) -> str:
    """« 86 armes font plus de 500 DPS. »"""
    sens = "plus de" if data["seuil"] == ">=" else "moins de"
    unite = f" {data['unite']}" if data["unite"] else ""
    # `_weapon_filter` rend le libellé **anglais** de la famille : le rendu
    # traduit, comme partout.
    famille = f" {_famille_fr(data['famille'], data['famille'])}"         if data.get("famille") else ""
    lignes = [f"**{_plural(data['total'], 'arme')}**{famille} font {sens} "
              f"**{_nombre(data['valeur'])}{unite}** de {data['libelle']} :", ""]
    lignes += [f"- {i['name']} — {_nombre(i['valeur'])}{unite}"
               + (f", taille {i['size']}" if i.get("size") is not None else "")
               for i in data["items"]]
    if data["total"] > len(data["items"]):
        lignes.append(f"- … et {_plural(data['total'] - len(data['items']), 'autre')}")
    return "\n".join(lignes)

_RENDERERS["armes_par_metrique"] = render_metrique
_RENDERERS["objets_au_seuil"] = render_objets_seuil


def render_catalogue_objets(data: dict[str, Any], montrer: int = 10) -> str:
    """« C'est quoi les boucliers ? » — le catalogue groupé, avec la marque.

    Format demandé par l'utilisateur : « Sniper : P6-LR (avec la marque),
    arme de poing : Coda… » — puis la boucle habituelle sur un nom.
    """
    lignes = [f"**Les {data['famille']}** — "
              f"{_plural(data['total'], 'modèle')}"
              + (f" ({_nombre(data['total_avec_coloris'])} avec les coloris)"
                 if data["total_avec_coloris"] > data["total"] else "")
              + " :", ""]
    ordre = {"légère": 0, "moyenne": 1, "lourde": 2}
    for groupe in sorted(data["groupes"],
                         key=lambda g: (ordre.get(g, 9), g)):
        entrees = data["groupes"][groupe]
        noms = []
        for entree in list(entrees.values())[:montrer]:
            marque = entree.get("marque")
            marque = (f" ({marque})" if marque
                      and not marque.startswith("<=") else "")
            noms.append(f"**{entree['nom']}**{marque}")
        suite = (f" — … et {_plural(len(entrees) - montrer, 'autre')}"
                 if len(entrees) > montrer else "")
        lignes.append(f"- *{groupe.capitalize()}* : "
                      + ", ".join(noms) + suite)
    lignes += ["", "Demande la fiche d'un nom — ses stats, son prix, son "
               "blueprint — et on continue là-dessus."]
    return "\n".join(lignes)


def render_catalogue_objets_complet(data: dict[str, Any]) -> str:
    """« La liste entière » — tous les modèles de chaque groupe."""
    plafond = max((len(g) for g in data["groupes"].values()), default=0)
    return render_catalogue_objets(data, montrer=plafond)


_RENDERERS["catalogue_objets"] = render_catalogue_objets
