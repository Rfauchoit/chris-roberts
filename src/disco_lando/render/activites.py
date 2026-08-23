"""Le rendu des activités — « quoi faire ce soir », en Markdown.

Sprint 38. Le bot est **écrit**, pas parlé (décision du 2026-08-04) : listes,
gras, et pas de budget de lecture. Deux règles du cahier commandent ce
fichier :

- **une réponse qui ne passe pas doit dire ce qui bloque.** Une liste vide
  est inutilisable ; « accepte le combat en vaisseau et tu as trois
  activités » est actionnable. C'est `debloquerait` qui le porte ;
- **on dit sur quel axe on classe.** « La meilleure » n'existe pas sans
  critère, et une note mesurée sur les contrats du jeu ne s'affiche pas
  comme une estimation de fiche : la ligne le dit à chaque fois.
"""

from __future__ import annotations

from typing import Any

from .socle import _RENDERERS, _nombre, _plural, enumerate_fr

_ETOILES = {1: "très accessible", 2: "accessible", 3: "exigeante",
            4: "difficile", 5: "très difficile"}

_AXES_FR = {
    "diff_risque": "risque",
    "diff_charge": "charge mentale",
    "diff_pilotage": "pilotage",
    "diff_connaissance": "connaissance du jeu",
}


def duree_fr(lo: int | None, hi: int | None) -> str | None:
    """« 1 h 30 à 3 h » — et rien du tout quand la fiche ne sait pas.

    Afficher « 0 min » pour une durée absente serait pire que se taire : le
    tri par longueur mettrait l'inconnu en tête.
    """
    def _un(minutes: int) -> str:
        if minutes < 60:
            return f"{minutes} min"
        heures, reste = divmod(minutes, 60)
        return f"{heures} h" if not reste else f"{heures} h {reste:02d}"

    if not lo and not hi:
        return None
    if lo and hi and lo != hi:
        return f"{_un(lo)} à {_un(hi)}"
    return _un(lo or hi)


def _joueurs_fr(fiche: dict[str, Any]) -> str:
    bas, haut = fiche.get("joueurs_min") or 1, fiche.get("joueurs_max")
    conseille = fiche.get("joueurs_conseilles")
    if haut and bas != haut:
        socle = f"{bas} à {haut} joueurs"
    elif haut:
        socle = _plural(haut, "joueur")
    else:
        socle = f"{bas} joueur ou plus"
    if conseille and (haut is None or conseille != haut):
        socle += f", idéalement {conseille}"
    return socle


def _pastilles(fiche: dict[str, Any]) -> str:
    bouts = [_joueurs_fr(fiche), fiche["combat_libelle"], fiche["mission_libelle"]]
    duree = duree_fr(fiche.get("duree_min_minutes"), fiche.get("duree_max_minutes"))
    if duree:
        bouts.append(duree)
    if fiche["pvp"] != "non":
        bouts.append(fiche["pvp_libelle"])
    if fiche.get("systeme"):
        bouts.append(fiche["systeme"])
    return " · ".join(b for b in bouts if b)


#: Les intertitres du tri par type. **L'événement dit qu'il est daté**, et
#: c'est une demande explicite de l'utilisateur (2026-08-14) : « événement
#: c'est tout en haut, et ça doit être précisé que c'est temporaire ». Sans
#: cette phrase, la première ligne d'une liste se lit comme une
#: recommandation alors que c'est un compte à rebours.
_INTERTITRES = {
    "evenement": ("Événements",
                  "datés — ils ouvrent et ferment, à faire tant qu'ils durent"),
    "chaine": ("Chaînes de missions",
               "une histoire suivie, du début à la fin"),
    "activite_libre": ("Activités libres",
                       "aucun contrat à prendre : on y va et on se sert"),
    "boucle": ("Boucles de jeu",
               "le fond de roulement, à pratiquer quand on veut"),
}


def render_activites(data: dict[str, Any]) -> str:
    """La liste filtrée, ou ce qui empêche d'en avoir une."""
    if not data["activites"]:
        return _rien_ne_passe(data)

    demande = enumerate_fr(data["criteres"], limit=4) if data["criteres"] else None
    entete = f"**{_plural(data['total'], 'activité')}**"
    if demande:
        entete += f" — {demande}"
    lignes = [f"{entete}, {data['tri_libelle']} :", ""]

    if data.get("debutant"):
        # **Sans cette phrase, le parcours se lit comme un refus.** Sept des
        # dix étapes portent la mention « difficile » ou « exigeante » — la
        # difficulté mesurée par CIG décrit la mission, pas l'accessibilité,
        # et les deux se contredisent à l'œil sur la première ligne. (Neuf
        # sur dix avant que la note ne passe du pire axe à leur moyenne, le
        # 2026-08-14 ; le constat tient, en moins criant.)
        lignes = [
            "**Les chaînes de missions d'abord, les activités libres "
            "ensuite.** Une chaîne te guide — le contrat, le marqueur, "
            "l'ordre des étapes, et la suivante ne s'ouvre qu'une fois la "
            "précédente finie. Une activité libre ne guide rien : il faut "
            "savoir où aller et quoi emporter avant même de partir.",
            "",
            "Les boucles de jeu — minage, fret, primes — se pratiquent quand "
            "tu veux et ne sont pas dans cette liste : elle ne garde que ce "
            "qui se **traverse dans un ordre**.",
            "",
            "La mention de difficulté est celle du jeu et décrit la mission, "
            "pas le niveau qu'il faut pour s'y mettre — c'est l'ordre qui "
            "porte cette réponse.",
            "",
        ] + lignes

    # **Un tri par type se lit en groupes, un parcours se lit numéroté.** À
    # plat, l'ordre des natures est invisible : rien ne distingue la dernière
    # chaîne de la première activité libre, et le classement demandé ne se
    # voit pas. La numérotation joue le même rôle pour le parcours — une
    # puce ne dit pas qu'il y a un ordre.
    groupe = data["tri"] == "type"
    nature_courante = None
    for rang, fiche in enumerate(data["activites"], start=1):
        if groupe and fiche["nature"] != nature_courante:
            nature_courante = fiche["nature"]
            titre, precision = _INTERTITRES.get(
                nature_courante, (fiche["nature_libelle"], None))
            if lignes[-1]:
                lignes.append("")
            lignes.append(f"**{titre}** — *{precision}*" if precision
                          else f"**{titre}**")
            lignes.append("")

        note = fiche.get("difficulte_note")
        difficulte = f" — *{_ETOILES.get(note, 'difficulté inconnue')}*" if note else ""
        avenir = ""
        if fiche["statut"] != "vivant":
            avenir = f" **[{fiche['statut_libelle']}]**"
        puce = f"{rang}." if data["tri"] == "debutant" else "-"
        # La continuation d'une puce s'aligne sur la **largeur de la puce**.
        # « 10. » en fait quatre, « - » en fait deux : deux espaces fixes
        # sortaient la ligne de la liste dès le dixième élément.
        creux = " " * (len(puce) + 1)
        lignes.append(f"{puce} **{fiche['nom']}**{avenir}{difficulte}")
        lignes.append(f"{creux}{_pastilles(fiche)}")
        lignes.append(f"{creux}{fiche['resume']}")
    return "\n".join(lignes)


def _rien_ne_passe(data: dict[str, Any]) -> str:
    """**Dire ce qui bloque, pas « aucun résultat ».**

    On relâche chaque critère à tour de rôle et on nomme celui dont l'abandon
    débloque le plus. Le joueur veut savoir laquelle de ses exigences coûte
    les autres — c'est la même règle que `vaisseaux_multi_criteres`.
    """
    demande = enumerate_fr(data["criteres"], limit=4) if data["criteres"] else "ces critères"
    if not data["debloquerait"]:
        return (f"Aucune activité ne correspond à {demande}, et je n'ai pas "
                "trouvé de critère à relâcher qui en débloque. Le catalogue "
                "ne couvre encore que les grandes activités du jeu.")
    premier = data["debloquerait"][0]
    lignes = [f"Aucune activité ne correspond à {demande}.", ""]
    # L'accord suit le nombre — « 2 activités deviennent possible » saute aux
    # yeux, et le projet écrit du français jusqu'au bout.
    pluriel = premier["activites"] > 1
    lignes.append(f"En renonçant à « {premier['critere']} », "
                  f"{_plural(premier['activites'], 'activité')} "
                  f"{'deviennent possibles' if pluriel else 'devient possible'}.")
    autres = data["debloquerait"][1:3]
    if autres:
        lignes.append("Autres pistes : " + enumerate_fr(
            [f"sans « {d['critere']} », {d['activites']}" for d in autres],
            limit=2) + ".")
    return "\n".join(lignes)


def render_fiche_activite(data: dict[str, Any]) -> str:
    """Le détail d'une activité : résumé, matériel, déroulé, récompenses.

    Le déroulé complet est rendu ici parce que la question l'a demandé
    explicitement ; c'est le site qui le replie derrière un dépliant.
    """
    lignes = [f"**{data['nom']}** — {data['nature_libelle']}"]
    if data["statut"] != "vivant":
        lignes[0] += f" **[{data['statut_libelle']}]**"
    lignes.append(f"*{_pastilles(data)}*")
    lignes += ["", data["resume"]]

    if data.get("pourquoi"):
        lignes += ["", f"**Pourquoi y aller** — {data['pourquoi']}"]

    note = data.get("difficulte_note")
    if note:
        detail = f"**Difficulté** : {_ETOILES.get(note, '?')} ({data['difficulte_origine']})"
        cig = data.get("difficulte_cig")
        if cig and cig.get("axes"):
            etendue = cig.get("etendue") or {}
            morceaux = []
            for axe, rang in sorted(cig["axes"].items(), key=lambda kv: -kv[1]):
                texte = f"{_AXES_FR[axe]} {rang}/7"
                # **Sur une boucle, l'étendue vaut autant que la médiane** :
                # elle dit qu'on peut viser plus facile. Sans elle, « 4/7 »
                # se lit comme un plancher imposé alors que c'est un choix.
                bornes = etendue.get(axe)
                if bornes and bornes[0] != bornes[1]:
                    texte += f" (de {bornes[0]} à {bornes[1]})"
                morceaux.append(texte)
            detail += f" — {', '.join(morceaux)}"
            # L'agrégat se dit : un maximum de chaîne et une médiane de
            # catalogue ne se comparent pas, et rien d'autre ne le signale.
            if cig.get("agregat_libelle"):
                detail += f"\n*{cig['agregat_libelle'].capitalize()}, "
                detail += f"sur {_nombre(cig['contrats_lus'])} contrats lus.*"
            if cig.get("solo_possible") is False:
                detail += "\nLe jeu classe ces contrats comme **non faisables seul**."
        lignes += ["", detail]

    if data.get("prerequis"):
        lignes += ["", f"**Prérequis** — {data['prerequis']}"]
    if data.get("rangs_exiges"):
        lignes.append(f"Rangs exigés par les contrats en base : "
                      f"{enumerate_fr(data['rangs_exiges'], limit=4)}.")

    essentiel = [m for m in data.get("materiel", ()) if m.get("essentiel")]
    reste = [m for m in data.get("materiel", ()) if not m.get("essentiel")]
    if essentiel or reste:
        lignes += ["", "**À emporter**", ""]
        for item in essentiel + reste:
            marque = "" if item.get("essentiel") else " *(optionnel)*"
            pourquoi = f" — {item['pourquoi']}" if item.get("pourquoi") else ""
            lignes.append(f"- {item['objet']}{marque}{pourquoi}")

    if data.get("etapes"):
        lignes += ["", "**Le déroulé**", ""]
        for etape in data["etapes"]:
            lignes.append(f"{etape['rang']}. **{etape['titre']}**")
            if etape.get("detail"):
                lignes.append(f"   {etape['detail']}")

    # **La moitié vivante de la fiche.** Elle vient de la base, pas du
    # rédacteur, et elle porte sa date : un prix relevé il y a trois semaines
    # doit se voir comme tel.
    calcule = data.get("calcule")
    if calcule and calcule.get("lignes"):
        lignes += ["", f"**{calcule['titre']}**", ""]
        for item in calcule["lignes"]:
            texte = f"- **{item['nom']}**"
            if item.get("valeur") is not None:
                texte += f" — {_nombre(round(item['valeur']))} {item['unite']}"
            if item.get("detail"):
                texte += f", {item['detail']}"
            if item.get("ou"):
                texte += f" ({item['ou']})"
            if item.get("gisements"):
                texte += f", {_plural(item['gisements'], 'gisement')}"
            lignes.append(texte)
        if calcule.get("sans_cotation"):
            lignes.append(
                f"- *Sans cotation, donc à miner sans espoir de revente "
                f"directe : {enumerate_fr(calcule['sans_cotation'], limit=4)}.*")
        pied = calcule.get("note", "")
        if calcule.get("releve"):
            pied += f" Relevé du {calcule['releve'][:10]}."
        if pied:
            lignes += ["", f"*{pied.strip()}*"]

    if data.get("recompenses"):
        lignes += ["", "**Ce que ça rapporte**", ""]
        lignes += [f"- {r['libelle']}" for r in data["recompenses"]]

    if data.get("avertissements"):
        lignes += ["", "**À savoir avant de partir**", ""]
        lignes += [f"- {a['texte']}" for a in data["avertissements"]]

    # **Le pont vers le mesuré.** Le compte porte sur les titres distincts,
    # pas sur les UUID : un joueur compte ce qu'il lit.
    if data.get("contrats"):
        lignes += ["", f"**{_plural(data['contrats'], 'contrat')}** "
                       "de cette activité dans le catalogue du jeu"]
        if data.get("contrats_bruts", 0) > data["contrats"]:
            lignes[-1] += (f" ({_nombre(data['contrats_bruts'])} entrées, "
                           "doublons de titre compris)")
        lignes[-1] += " :"
        lignes.append("")
        lignes += [f"- {c['title'] or c['debug_name']}"
                   for c in data["contrats_lies"][:8]]
        if len(data["contrats_lies"]) > 8:
            lignes.append(f"- … et {_plural(len(data['contrats_lies']) - 8, 'autre')}")

    lieux = [lieu["nom"] for lieu in data.get("lieux", ()) if lieu.get("nom")]
    if lieux:
        lignes += ["", f"**Où** — {enumerate_fr(lieux, limit=6)}."]

    liens = [lien["nom"] or lien["vers"] for lien in data.get("liens", ())]
    if liens:
        lignes.append(f"**À enchaîner avec** — {enumerate_fr(liens, limit=3)}.")

    # **Une fiche éditoriale dit d'où elle vient et quand elle a été vue
    # juste.** Sans ça, elle se lit comme de la donnée de jeu.
    sources = []
    for source in data.get("sources", ()):
        nom = source["nom"]
        if source.get("auteur"):
            nom += f" ({source['auteur']})"
        sources.append(nom)
    if sources:
        lignes += ["", f"*Sources : {enumerate_fr(sources, limit=4)}. "
                       f"Vérifié en {data.get('patch_verifie', '?')}.*"]
    return "\n".join(lignes)


def render_quoi_de_neuf(data: dict[str, Any]) -> str:
    """« Qu'est-ce qui arrive au prochain patch ? »

    **Le niveau de preuve se lit ligne par ligne, pas en note de bas de
    page.** Une annonce de guide et un objet déjà présent dans les fichiers
    ne valent pas la même chose ; les mélanger ferait passer l'un pour
    l'autre. C'est la même discipline que la difficulté mesurée contre la
    difficulté estimée, appliquée aux notes de patch.
    """
    entete = f"**{data['titre']}** — patch {data['patch']}"
    if data.get("statut") == "a_venir":
        entete += " *(à venir"
        if data.get("sortie_annoncee"):
            entete += f", annoncé pour {data['sortie_annoncee']}"
        entete += ")*"
    lignes = [entete, "", data["resume"]]

    if data.get("build_installe"):
        lignes += ["", f"*Tu es en {data['build_installe']}. "
                       f"{_plural(data['total'], 'nouveauté')} listée"
                       f"{'s' if data['total'] > 1 else ''}, dont "
                       f"**{data['deja_en_base']} déjà présente"
                       f"{'s' if data['deja_en_base'] > 1 else ''} dans tes "
                       "fichiers de jeu**.*"]

    for categorie in data.get("categories", ()):
        lignes += ["", f"**{categorie['nom']}**", ""]
        for item in categorie["lignes"]:
            # La marque de preuve précède le texte : on sait ce qu'on lit
            # avant de le lire.
            marque = "✔" if item["preuve"] == "fichiers" else "•"
            lignes.append(f"{marque} **{item['titre']}**")
            if item.get("detail"):
                lignes.append(f"   {item['detail']}")
            if item.get("consequence"):
                lignes.append(f"   *Pour nous : {item['consequence']}*")
            if item.get("voir_nom"):
                lignes.append(f"   *Voir la fiche « {item['voir_nom']} ».*")

    if data.get("avertissements"):
        lignes += ["", "**À savoir**", ""]
        lignes += [f"- {a['texte']}" for a in data["avertissements"]]

    lignes += ["", "*✔ = déjà dans les fichiers du jeu, donc nommable "
                   "exactement. • = annoncé, pas encore vérifiable ici.*"]
    sources = [s["nom"] + (f" ({s['auteur']})" if s.get("auteur") else "")
               for s in data.get("sources", ())]
    if sources:
        lignes.append(f"*Sources : {enumerate_fr(sources, limit=4)}.*")
    return "\n".join(lignes)


_RENDERERS["activites_a_faire"] = render_activites
# Le parcours du débutant rend la même liste ; c'est le drapeau `debutant`
# porté par les données qui change l'en-tête, pas un second rendu.
_RENDERERS["par_ou_commencer"] = render_activites
_RENDERERS["fiche_activite"] = render_fiche_activite
_RENDERERS["quoi_de_neuf"] = render_quoi_de_neuf
