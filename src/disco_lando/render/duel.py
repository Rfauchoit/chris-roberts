"""Le duel : « est-ce qu'un Scorpius peut détruire un Hammerhead ? »

Le verdict d'abord, puis **le verrou qui bloque** — c'est lui que le joueur
veut comprendre, pas la liste des chiffres. La règle du calcul s'annonce en
fin de réponse, comme tout classement annonce la sienne.
"""

from __future__ import annotations

from typing import Any

from ..combat import _MODULATION_FR
from .socle import _RENDERERS, _nombre, enumerate_fr


def _secondes(t: float) -> str:
    if t < 60:
        return f"{t:.0f} s"
    return f"{int(t // 60)} min {int(t % 60):02d} s"


_POSTES_FR = {"pilote": "pilote", "habitee": "tourelle habitée",
              "telecommandee": "tourelle télécommandée"}


def _ligne_feu(feu: dict[str, Any] | None) -> str | None:
    """« 2 231 DPS à équipage complet (2 joueurs : pilote 1 116 + tourelle
    télécommandée 1 116) » — le feu pilote seul ampute un biplace de la
    moitié de son armement (journal du 2026-08-12)."""
    if not feu or not feu.get("par_poste"):
        return None
    parts = []
    postes_t = feu.get("postes_tourelles") or {}
    for poste in ("pilote", "habitee", "telecommandee"):
        entree = feu["par_poste"].get(poste)
        if not entree:
            continue
        libelle = _POSTES_FR.get(poste, poste)
        n_postes = postes_t.get(poste)
        if poste != "pilote" and n_postes and n_postes > 1:
            libelle = f"{_nombre(n_postes)} {libelle}s"
        parts.append(f"{libelle} {_nombre(round(entree['dps']))}")
    joueurs = feu.get("joueurs_feu_complet") or 1
    total = _nombre(round(feu.get("dps_total") or 0))
    if joueurs <= 1:
        return f"{total} DPS ({', '.join(parts)}, 1 joueur)"
    return (f"{total} DPS à équipage complet ({_nombre(joueurs)} joueurs : "
            f"{' + '.join(parts)})")


def _ligne_agilite(engagement: dict[str, Any] | None,
                   nom_a: str, nom_b: str) -> list[str]:
    """L'agilité en grandeurs physiques, pas en scores normalisés.

    **Réécrite le 2026-08-13.** L'ancienne ligne rendait quatre scores sans
    unité — « évasion 0,85 / 1,08 » —, et l'utilisateur a eu raison de
    demander comment une Aurora Mk II pouvait esquiver mieux qu'un Arrow :
    le facteur se lisait sur `accel_maneuver`, une somme de poussées
    installées qui récompense le nombre de tuyères. On rend désormais ce
    que le modèle emploie vraiment, avec des unités qui se vérifient :
    l'accélération d'esquive, la vitesse de rotation, la surface présentée.

    Et elle **ne se contente plus d'éclairer** : ces chiffres produisent le
    taux de touche qui décide du verdict.
    """
    if not engagement:
        return []
    a, b = engagement.get("attaquant"), engagement.get("defenseur")
    if not a or not b:
        return []
    mesures = (
        ("acceleration_evasion", "esquive", "m/s²", 0, True),
        ("rotation", "rotation", "°/s", 0, True),
        # Plus la surface est petite, plus on est difficile à toucher :
        # c'est la seule mesure du lot où le petit chiffre gagne, et le
        # taire ferait lire l'avantage à l'envers.
        ("surface", "surface présentée", "m²", 0, False),
    )
    parts, avantages_a, comptees = [], 0, 0
    for cle, libelle, unite, decimales, haut_mieux in mesures:
        va, vb = a.get(cle), b.get(cle)
        if va is None or vb is None:
            continue
        chiffres = f"{va:.{decimales}f} / {vb:.{decimales}f}".replace(".", ",")
        parts.append(f"{libelle} {chiffres} {unite}")
        comptees += 1
        if (va > vb) is haut_mieux:
            avantages_a += 1
    if not parts:
        return []
    meneur = (nom_a if avantages_a > comptees - avantages_a else
              nom_b if avantages_a < comptees - avantages_a else None)
    bilan = (f" — avantage {meneur} sur "
             f"{max(avantages_a, comptees - avantages_a)} mesures sur "
             f"{comptees}" if meneur else " — équilibré")
    return [
        f"- agilité ({nom_a} / {nom_b}) : " + ", ".join(parts) + bilan
        + ". Une surface plus petite est un avantage ; ces trois mesures "
        "produisent le taux de touche du verdict ;"]


def _description_armes(profils: list[dict[str, Any]]) -> str:
    """« 4 CF-337 Panther, des armes à énergie » — le type d'abord.

    Journal du 2026-08-08 : « aux canons » portait à confusion (le canon
    est un type d'arme), et le fait que l'énergétique soit absorbé par le
    bouclier arrivait trop tard dans la réponse.
    """
    noms = {}
    for p in profils:
        if p["name"] not in noms:
            noms[p["name"]] = dict(p)
        else:
            noms[p["name"]]["n"] += p.get("n") or 1
    parties = []
    for p in noms.values():
        n = p.get("n") or 1
        if p.get("continu"):
            parties.append(
                f"{_nombre(n)} {p['name']} (faisceau continu à énergie)")
        elif p["type"] == "physical":
            parties.append(f"{_nombre(n)} {p['name']} (du balistique)")
        else:
            parties.append(f"{_nombre(n)} {p['name']} (armes à énergie)")
    return enumerate_fr(parties, limit=3)


def _bloc_armes_contre_defense(duel: dict[str, Any],
                               profils: list[dict[str, Any]]) -> str:
    """Ce que les armes font — bouclier puis armure, dans l'ordre du tir."""
    b = duel["bouclier"]
    lignes = [f"**Ses armes** ({_description_armes(profils)}) :"]

    energie = any(p["type"] == "energy" for p in profils)
    balistique = any(p["type"] == "physical" for p in profils)
    faisceaux = [p for p in profils if p.get("continu")]
    projectiles = [p for p in profils if not p.get("continu")]
    if b["hp"]:
        if b["tombe"]:
            if faisceaux and not projectiles:
                lignes.append(
                    f"- ses **{_nombre(round(b['dps']))} DPS continus** "
                    "maintiennent le bouclier sous dégâts : sa régénération "
                    f"nominale de {_nombre(round(b['regen']))} PV/s ne "
                    "redémarre pas tant que le faisceau touche. Ses "
                    f"{_nombre(round(b['hp']))} PV tombent en "
                    f"~{_secondes(b['temps'])} ;")
            else:
                lignes.append(
                    f"- le bouclier tombe en ~{_secondes(b['temps'])} "
                    "de tir continu ;")
        else:
            absorbe = ("l'énergie est entièrement absorbée par le bouclier"
                       if energie and not balistique else
                       "le bouclier absorbe l'énergie en entier et 45 % du "
                       "balistique")
            impact = ("aucun projectile n'est assez fort"
                      if faisceaux else "chaque impact est trop faible")
            lignes.append(
                f"- {absorbe}, et {impact} pour "
                f"suspendre sa régénération (il faudrait "
                f"{_nombre(round(b['seuil_stun']))} de dégâts par tir) : "
                f"il se recharge plus vite qu'il n'encaisse et **ne "
                f"tombera jamais** ;")
    if duel["deviees"] and not duel["passantes"]:
        pire = max(duel["deviees"], key=lambda a: a["par_plomb"])
        lignes.append(
            f"- et même bouclier tombé, **l'armure dévierait chaque tir** : "
            f"sous {_nombre(round(pire['seuil']))} de dégâts par impact, ça "
            f"ricoche sans rien user — ses tirs plafonnent à "
            f"{pire['par_plomb']:.0f}.")
    else:
        beams_passants = [p for p in duel["passantes"]
                          if p.get("continu")]
        tirs_passants = [p for p in duel["passantes"]
                         if not p.get("continu")]
        if beams_passants:
            noms_beams = enumerate_fr(
                list(dict.fromkeys(p["name"] for p in beams_passants)),
                limit=3)
            suite = (" ; il n'atteint toutefois pas l'armure tant que le "
                     "bouclier tient" if not b["tombe"] else "")
            lignes.append(
                "- le seuil de déflexion vise les projectiles et ne "
                f"s'applique donc pas au faisceau continu {noms_beams}"
                f"{suite}.")
        if tirs_passants:
            detail = enumerate_fr(
                [f"{a['name']} ({a['par_plomb']:.0f} par tir contre un "
                 f"seuil de {_nombre(round(a['seuil']))})"
                 for a in {p['name']: p for p in tirs_passants}.values()],
                limit=3)
            lignes.append(f"- passent le seuil de l'armure : {detail}.")
    return "\n".join(lignes)


def _bloc_bilan(data: dict[str, Any]) -> str:
    """Armes + missiles, additionnés — jamais « soit l'un, soit l'autre »."""
    m, bilan = data.get("missiles"), data.get("bilan")
    if not m or not bilan or bilan.get("par") == "armes":
        return ""
    tete = (f"\n\n**Ses missiles**, eux, passent tout — bouclier compris : "
            f"{m['n']} × {_nombre(round(m['unitaire']))} = "
            f"{_nombre(round(m['total']))} de dégâts possibles, pour "
            f"{_nombre(round(m['requis']))} à raser (bouclier, armure et "
            f"coque). ")
    if bilan["suffit"]:
        return tete + ("**En les plaçant tous, ça passe** — c'est la seule "
                       "voie : la cible peut les intercepter, il faudra "
                       "les placer.")
    complement = ""
    if bilan.get("armes_livrent"):
        complement = (f" — même en ajoutant les "
                      f"{_nombre(round(bilan['armes_livrent']))} que ses "
                      f"armes savent livrer")
    return tete + (f"En les plaçant tous, il manque encore "
                   f"**{_nombre(round(bilan['deficit']))}**{complement}. "
                   "Aucune combinaison ne suffit.")


def _bloc_conseils(data: dict[str, Any]) -> str:
    """Ce qui permettrait de gagner — la partie que le joueur retient.

    Réécrit sur remarque du journal (2026-08-08) : « tout le pour y
    arriver est à revoir, on ne comprend rien ». La règle d'abord — chaque
    tir doit dépasser le seuil de l'armure — puis les armes qui la
    remplissent, sur SES affûts.
    """
    c = data.get("conseils") or {}
    defense = data.get("defense") or {}
    if not c:
        return ""
    seuils = []
    if defense.get("defl_physical"):
        seuils.append(f"{_nombre(round(defense['defl_physical']))} en "
                      "balistique")
    if defense.get("defl_energy"):
        seuils.append(f"{_nombre(round(defense['defl_energy']))} en énergie")
    regle = (f"il faut des armes dont **chaque tir** dépasse le seuil de "
             f"son armure — {' ou '.join(seuils)}" if seuils else
             "il faut des armes qui passent le seuil de son armure")
    if c.get("armes"):
        lignes = [f"{a['name']} ({_nombre(round(a['par_plomb']))} par tir, "
                  f"taille {a['size']})"
                  for a in c["armes"]]
        return (f"\n\n**Pour espérer le détruire**, {regle}. Sur tes "
                f"affûts (taille {c['taille_max']} max), ça existe : "
                f"{enumerate_fr(lignes, limit=4)} — et les missiles "
                f"complètent. Demande-moi où en acheter ou comment les "
                f"fabriquer.")
    if c.get("par_qualite"):
        lignes = [f"{a['name']} fabriqué en qualité "
                  f"{_nombre(a['qualite_min'])} au moins"
                  for a in c["par_qualite"]]
        return (f"\n\n**Pour espérer le détruire**, {regle}. Rien au "
                f"catalogue n'y arrive en taille {c['taille_max']} — seule "
                f"la fabrication : {enumerate_fr(lignes, limit=3)}. "
                f"*(Cumul des composants de dégâts : hypothèse, le jeu ne "
                f"publie pas leur combinaison.)*")
    if c.get("taille_max"):
        return (f"\n\n**Pour espérer le détruire**, {regle} — et aucune "
                f"arme de taille {c['taille_max']} ou moins n'y arrive, "
                f"même fabriquée. Il faut un vaisseau aux affûts plus "
                f"gros, ou tout miser sur les missiles.")
    return ""


def _bloc_desarmement(data: dict[str, Any]) -> str:
    """La solution mesurée quand la coque dévie les armes légères."""
    exposes = data.get("composants_exposes") or {}
    groupes = [g for g in exposes.get("groupes") or [] if g.get("possible")]
    if not groupes:
        return ""
    lignes = ["\n\n**Désarmement ciblé — composants extérieurs :**"]
    postes = {
        "habitee": "tourelle habitée",
        "telecommandee": "tourelle télécommandée",
        "pdc": "tourelle PDC",
    }
    for groupe in groupes[:5]:
        nom = (postes.get(groupe.get("poste"))
               if groupe["genre"] == "tourelle" else "propulseur")
        nom = nom or "tourelle"
        seuils = (groupe.get("seuil_physical") or 0,
                  groupe.get("seuil_energy") or 0)
        seuil = ("aucun seuil de déflexion"
                 if seuils == (0, 0) else
                 f"seuils {seuils[0]:.0f} physique / {seuils[1]:.0f} énergie")
        lignes.append(
            f"- **1 {nom} sur {_nombre(groupe['n'])}** : "
            f"{_nombre(round(groupe['pv']))} PV, {seuil} → "
            f"~{_secondes(groupe['temps'])} de tir effectivement reçu "
            f"({_nombre(round(groupe['dps']))} DPS utiles).")
    if exposes.get("sans_pv"):
        lignes.append(
            f"_{_nombre(exposes['sans_pv'])} autres composants exposés n'ont "
            "pas de PV publiés ; ils ne sont pas estimés._")
    lignes.append(
        "_Temps par composant, hors acquisition de cible et géométrie de tir. "
        "Le bouclier garde sa mécanique propre ; l'armure de coque, elle, ne "
        "prête pas son seuil à ces pièces extérieures._")
    return "\n".join(lignes)


_REGLE = ("\n\n*Calcul selon le système d'armure 4.7 : déflexion par "
          "projectile, bouclier qui n'absorbe que 45 % du balistique (le "
          "reste traverse), régénération interrompue seulement par des "
          "impacts au-dessus de 0,5 % des PV du générateur. Un faisceau "
          "continu n'a pas d'alpha par tir : tant qu'il touche, ses dégâts "
          "maintiennent la régénération suspendue. Les tourelles et "
          "propulseurs exposés restent atteignables même sous le seuil — "
          "désarmer est possible là où détruire ne l'est pas.*")


def render_duel(data: dict[str, Any]) -> str:
    att, cible = data["attaquant"]["name"], data["defenseur"]["name"]

    if data.get("arme_refusee"):
        return (f"Je ne peux pas appliquer **{data['arme_refusee']}** à "
                f"**{att}** : je ne l'ai pas reliée à une arme compatible "
                f"avec ses affûts. Je ne calcule pas le duel avec son "
                f"armement stock à la place.")

    if data.get("bouclier_refuse"):
        return (f"Je ne peux pas appliquer **{data['bouclier_refuse']}** à "
                f"**{cible}** : je ne l'ai pas relié à un générateur de "
                "bouclier chiffré. Je ne calcule pas le duel avec son "
                "bouclier stock à la place.")

    if data.get("sans_defense_connue"):
        return (f"Le jeu ne publie pas la défense de **{cible}** (ni armure, "
                f"ni bouclier, ni coque) — je ne peux pas trancher ce duel.")

    equipement = ""
    if data.get("loadout_nomme"):
        equipement = f" avec son meilleur loadout ({data['loadout_nomme']})"
    if data.get("arme_nommee"):
        equipement = f" équipé de {data['arme_nommee']}"
        if data.get("arme_tourelles"):
            equipement += " en tourelles"
        if data.get("qualite") is not None and data.get("mult_qualite"):
            equipement += (f" en qualité {_nombre(int(data['qualite']))} "
                           f"(dégâts ×{data['mult_qualite']:.2f})")
    notes_defense = []
    if data.get("bouclier_nomme"):
        notes_defense.append(f"bouclier remplacé par {data['bouclier_nomme']}")
    if data.get("qualite_bouclier") is not None:
        q = data["qualite_bouclier"]
        mult = data.get("mult_qualite_bouclier")
        if mult is None:
            notes_defense.append(
                f"qualité de bouclier {_nombre(int(q))} non chiffrable")
        else:
            note = (f"bouclier qualité {_nombre(int(q))}, PV ×{mult:.2f} selon "
                    f"{_nombre(data.get('composants_qualite_bouclier') or 1)} "
                    "composants publiés")
            borne = data.get("borne_qualite_bouclier")
            if borne is not None and q > borne:
                note += f", gain plafonné dès {_nombre(int(borne))}"
            notes_defense.append(note)
    defense_note = f" ({'; '.join(notes_defense)})" if notes_defense else ""

    duel = data.get("duel")
    if duel is None:
        non_chiffrees = data.get("armes_non_chiffrees") or []
        if data.get("arme_nommee") and non_chiffrees:
            arme = non_chiffrees[0]
            dps = arme.get("dps_soutenu") or arme.get("dps")
            mesure = (f" Le jeu publie **{_nombre(round(dps))} DPS**,"
                      if dps else "")
            return (f"Je ne peux pas trancher **{att} uniquement avec "
                    f"{data['arme_nommee']} contre {cible}**.{mesure} mais "
                    f"aucune valeur de dégâts par impact ni aucun mode "
                    f"continu exploitable pour cette arme : impossible de "
                    f"tester honnêtement l'interruption du bouclier et la "
                    f"déflexion de l'armure. "
                    f"Les autres armes et les missiles sont bien exclus du "
                    f"calcul demandé.")
        texte = (f"Je n'ai aucun profil de dégâts exploitable pour "
                 f"l'armement publié de **{att}**. Je ne peux pas trancher "
                 f"sur ses armes sans inventer les valeurs manquantes.")
        m = data.get("missiles")
        if m:
            texte += (f"\n\n**Ses missiles**, eux, passent tout : {m['n']} "
                      f"× {_nombre(round(m['unitaire']))} = "
                      f"{_nombre(round(m['total']))} de dégâts possibles "
                      f"pour {_nombre(round(m['requis']))} à raser — "
                      + ("**ça suffit**, à condition de les placer : la "
                         "cible peut les intercepter."
                         if m["suffisent"] else "même tous placés, ça ne "
                         "suffit pas."))
        return texte + _REGLE

    verdict = duel["verdict"]
    bilan = data.get("bilan") or {}
    gagne = verdict or bilan.get("suffit")
    if data.get("arme_seule"):
        maniere = " avec cette arme seule"
    elif verdict:
        maniere = ""
    elif bilan.get("suffit"):
        maniere = ", mais uniquement aux missiles"
    else:
        maniere = ", par aucune combinaison d'armes et de missiles"
    tete = (f"**{'Oui' if gagne else 'Non'} — {att}{equipement} "
            f"{'peut détruire' if gagne else 'ne peut pas détruire'} "
            f"{cible}{defense_note}{maniere}.**\n\n")

    corps = _bloc_armes_contre_defense(duel, data.get("armes") or [])
    coque = duel["coque"]
    if verdict:
        detail = (f"\n- armure et coque : "
                  f"{_nombre(round(coque['a_livrer']))} PV à livrer, "
                  f"~{_secondes(coque['temps'])} de tir efficace")
        if coque["budget"] is not None:
            detail += (f" — sa réserve de munitions "
                       f"({_nombre(round(coque['budget']))}) suffit")
        corps += detail + "."
    elif duel["passantes"] and not coque["possible"]:
        raison = (f"sa réserve de munitions ne couvre pas les "
                  f"{_nombre(round(coque['a_livrer']))} PV à livrer"
                  if coque["budget"] is not None else
                  "rien ne travaille la coque tant que le bouclier tient")
        corps += f"\n- mais {raison}."

    # « Il n'a pas reconnu que le Scorpius et le Hurricane étaient 2
    # joueurs » (journal du 2026-08-12) : quand une tourelle existe d'un
    # côté ou de l'autre, le duel dit qui tient les manches.
    equipage = ""
    feu_att = _ligne_feu(data.get("feu_attaquant"))
    feu_cible = _ligne_feu(data.get("feu_defenseur"))
    joueurs = {(data.get("feu_attaquant") or {}).get("joueurs_feu_complet"),
               (data.get("feu_defenseur") or {}).get("joueurs_feu_complet")}
    if feu_att and feu_cible and any(j and j > 1 for j in joueurs):
        equipage = (f"\n\n**Qui tient les manches** — {att} : {feu_att} ; "
                    f"{cible} : {feu_cible}. Seul à bord, chacun retombe à "
                    "son feu pilote.")
    agilite = _ligne_agilite(data.get("agilite"), att, cible)  # modèle 3
    if agilite:
        ligne = agilite[0].removeprefix("- agilité").rstrip("; ")
        equipage += "\n\n**Agilité**" + ligne + "."

    restriction = ("\n\n*Comme demandé, les autres armes et les missiles "
                   "sont bien exclus du calcul.*"
                   if data.get("arme_seule") else "")
    non_chiffrees = data.get("armes_non_chiffrees") or []
    manque_armes = (
        "\n\n*Calcul partiel : "
        + ", ".join(dict.fromkeys(
            a.get("name") or a.get("item_name") or "une arme sans nom"
            for a in non_chiffrees))
        + " n'a pas de profil de dégâts exploitable et n'est donc pas "
          "compté dans le verdict.*" if non_chiffrees else "")
    n_tourelles = data.get("armes_en_tourelles") or 0
    hypothese_tourelles = (
        f"\n\n*Hypothèse de feu maximal : les {_nombre(n_tourelles)} armes "
        "en tourelles sont servies et ont toutes la cible dans leur arc ; "
        "la géométrie de tir et la présence de l'équipage ne sont pas "
        "publiées dans ces données.*" if n_tourelles else "")
    return (tete + corps + _bloc_bilan(data) + _bloc_desarmement(data)
            + _bloc_conseils(data) + equipage
            + restriction + manque_armes + hypothese_tourelles + _REGLE)


_RENDERERS["peut_detruire"] = render_duel


def render_bataille(data: dict[str, Any]) -> str:
    """« 5 Gladius contre un C2 » — le verdict du jeu de guerre.

    L'outil est **pour le fun** et l'annonce : les verrous du duel gardent
    la physique publiée, mais mobilité, surnombre et riposte pèsent par des
    poids maison. Toute modulation incomprise est dite, jamais avalée.
    """
    b = data.get("bataille")
    n = data.get("n") or 1
    att, cible = data["attaquant"]["name"], data["defenseur"]["name"]
    tete_att = f"{_nombre(n)} {att}" if n > 1 else att
    if data.get("arme_nommee"):
        tete_att += (f" équipés de {data['arme_nommee']}" if n > 1
                     else f" équipé de {data['arme_nommee']}")
        if data.get("arme_tourelles"):
            tete_att += " en tourelles"
        if data.get("qualite") is not None and data.get("mult_qualite"):
            tete_att += f" en qualité {_nombre(int(data['qualite']))}"

    if b is None:
        return render_duel(data)

    mods_a = ", ".join(_MODULATION_FR[m] for m in b["mods_att"])
    mods_c = ", ".join(_MODULATION_FR[m] for m in b["mods_cible"])
    etat_a = f" ({mods_a})" if mods_a else ""
    etat_c = f" ({mods_c})" if mods_c else ""

    if b["victoire"]:
        tete = (f"**Oui — {tete_att}{etat_a} "
                f"{'viennent' if n > 1 else 'vient'} à bout de "
                f"{cible}{etat_c}**, sur le papier.\n\n")
    else:
        tete = (f"**Non — {tete_att}{etat_a} ne "
                f"{'tombent' if n > 1 else 'tombe'} pas "
                f"{cible}{etat_c}**, même sur le papier.\n\n")

    lignes = []
    if b["t_cible"] is not None:
        lignes.append(f"- Le groupe tombe la cible en **~{_secondes(b['t_cible'])}** "
                      f"de feu efficace (mobilité ×{b['facteur_mobilite']:.2f}).")
    else:
        verrous = data.get("duel_verrous") or {}
        if verrous and not verrous.get("passantes"):
            lignes.append("- Rien ne passe la déflexion de l'armure — le "
                          "surnombre n'y change rien, zéro fois cinq reste "
                          "zéro.")
        else:
            lignes.append("- Le groupe ne vient pas à bout de la cible "
                          "(bouclier, budget ou déflexion — voir le duel).")
    if b["t_groupe"] is not None:
        lignes.append(f"- La riposte ({_nombre(round(b['riposte']))} DPS "
                      f"offensifs soutenus, visée ×{b.get('facteur_riposte', 1):.2f}) "
                      "rase le groupe en "
                      f"~{_secondes(b['t_groupe'])}.")
    elif b["riposte"] == 0:
        lignes.append("- La cible n'a aucun armement offensif chiffré ; elle "
                      "ne riposte pas dans ce modèle.")

    # Modèle 3 : des grandeurs physiques comparables, plus quatre scores sans
    # unité. Voir `_ligne_agilite` pour l'incident qui a motivé le changement.
    if b.get("modele_agilite") == 3:
        agi_att, agi_cible = b["agilite"]
        lignes.append(
            "- Agilité attaquant / cible — esquive "
            f"{agi_att['acceleration_evasion']:.0f} / "
            f"{agi_cible['acceleration_evasion']:.0f} m/s², rotation "
            f"{agi_att['rotation']:.0f} / {agi_cible['rotation']:.0f} °/s"
            + (f", surface présentée {agi_att['surface']:.0f} / "
               f"{agi_cible['surface']:.0f} m²"
               if agi_att.get("surface") and agi_cible.get("surface") else "")
            + f", combat à {_nombre(b.get('distance_engagement') or 0)} m.")
        if agi_att.get("masse") and agi_cible.get("masse"):
            lignes.append(
                f"- Masses : {_nombre(round(agi_att['masse']))} contre "
                f"{_nombre(round(agi_cible['masse']))} kg — c'est par elle "
                "que la poussée devient une accélération d'esquive.")

    texte = tete + "\n".join(lignes)
    if data.get("incomprises"):
        cites = " ; ".join(f"« {m} »" for m in data["incomprises"])
        texte += (f"\n\nJe n'ai pas compris {cites} — c'est ignoré dans le "
                  "calcul, et je préfère te le dire.")
    tourelles = ((data.get("armes_en_tourelles") or 0)
                 + (b.get("riposte_armes_tourelles") or 0))
    hypothese_tourelles = (
        " Les armes en tourelles supposent leurs servants présents et la "
        "cible dans tous leurs arcs." if tourelles else "")
    return texte + (
        "\n\n*Jeu de guerre théorique, pour le fun : les verrous du duel "
        "(déflexion, bouclier, budget) sont la physique publiée, mais le "
        "taux de touche (temps de vol des projectiles, esquive, surface "
        "présentée, rotation), la distance retenue, le surnombre et "
        "la riposte au DPS soutenu sont mon modèle, pas celui du jeu. Le "
        f"talent des pilotes n'est dans aucune colonne.{hypothese_tourelles}*")


_RENDERERS["bataille"] = render_bataille


_LIMITE_MATCHUP = (
    "\n\n*Comparaison de l'armement stock : bouclier, déflexion, "
    "armure/coque et munitions viennent du jeu. Les missiles sont exclus "
    "car leur interception n'est pas chiffrée. Le taux de touche, lui, est "
    "un **modèle maison** — temps de vol des projectiles, accélération "
    "d'esquive, surface présentée et vitesse de rotation — et la distance "
    "est celle que le plus rapide peut imposer. Le talent des pilotes et la "
    "géométrie des arcs ne sont publiés nulle part : ce classement ne "
    "prédit pas un résultat PvP.*"
)


def _ligne_temps_matchup(nom: str, temps: float | None) -> str:
    if temps is None:
        return (f"**{nom}** ne démontre pas la destruction avec ses canons "
                "stock dans les profils publiés")
    return f"**{nom}** : ~{_secondes(temps)} de feu continu"


def render_matchups_vaisseau(data: dict[str, Any]) -> str:
    """Classement de matchups ou duel direct, toujours bidirectionnel."""
    source = data["vaisseau"]
    nom = source["name"]
    matchups = data.get("matchups") or []
    if data.get("direct"):
        analyse = matchups[0]
        cible = analyse["cible"]
        verdicts = {
            "favorable": f"avantage théorique pour {nom}",
            "favorable_mecanique": (
                f"avantage mécanique pour {nom}, retour non démontré"),
            "defavorable": f"avantage théorique pour {cible['name']}",
            "defavorable_mecanique": (
                f"avantage mécanique pour {cible['name']}, aller non démontré"),
            "serre": "matchup théorique serré",
            "indetermine": "matchup indéterminé avec les profils publiés",
        }
        lignes = [
            f"**{nom} face à {cible['name']} : "
            f"{verdicts[analyse['verdict']]}.**",
            "",
            "- Aller — " + _ligne_temps_matchup(
                nom, analyse["temps_source"]) + ";",
            "- Retour — " + _ligne_temps_matchup(
                cible["name"], analyse["temps_cible"]) + ".",
        ]
        if (analyse.get("temps_source") is not None
                and analyse.get("temps_cible") is not None):
            ecart = abs(analyse["temps_cible"] - analyse["temps_source"])
            lignes.append(
                f"- Écart : **~{_secondes(ecart)}** ; rapport des temps "
                f"**×{analyse['rapport_temps']:.2f}**. Le seuil de classement "
                "« favorable » est fixé à 15 % pour ne pas surinterpréter "
                "un écart marginal.")
            # Révision du 2026-08-12 (demande de l'utilisateur) : la
            # mobilité pèse dans le verdict, plus seulement dans une ligne
            # d'ambiance. Le poids est maison — il s'annonce, et les temps
            # bruts restent lisibles.
            engage = analyse.get("engagement") or {}
            if engage.get("taux"):
                f_a = f"{100 * engage['taux']['aller']:.0f}"
                f_r = f"{100 * engage['taux']['retour']:.0f}"
                lignes.append(
                    f"- Ces temps tiennent compte de **ce qui touche "
                    f"vraiment** à {_nombre(engage['distance'])} m, la "
                    f"distance que le plus rapide impose : {f_a} % des tirs "
                    f"portent à l'aller, {f_r} % au retour. Au feu brut, "
                    "tous coups au but : "
                    f"~{_secondes(analyse['temps_source_meca'])} contre "
                    f"~{_secondes(analyse['temps_cible_meca'])}.")
        ehp_source = sum(source.get(c) or 0 for c in (
            "shield_hp", "armor_health", "hull_health"))
        ehp_cible = sum(cible.get(c) or 0 for c in (
            "shield_hp", "armor_health", "hull_health"))
        # Le feu s'annonce par poste, avec l'équipage qu'il exige — « feu
        # pilote 4 365 contre 1 636 » sur deux biplaces a fait brodé
        # l'analyste sur le mauvais chiffre (journal du 2026-08-12).
        feu_source = _ligne_feu(source.get("feu"))
        feu_cible = _ligne_feu(cible.get("feu"))
        lignes += ["", "**Différences publiées :**"]
        if feu_source and feu_cible:
            lignes.append(f"- feu : {nom} {feu_source} contre "
                          f"{cible['name']} {feu_cible} ;")
        else:
            lignes.append(
                f"- feu pilote : {_nombre(round(source.get('pilot_dps') or 0))} "
                f"contre {_nombre(round(cible.get('pilot_dps') or 0))} DPS ;")
        lignes += [
            f"- défense totale : {_nombre(round(ehp_source))} contre "
            f"{_nombre(round(ehp_cible))} PV (bouclier + armure + coque) ;",
        ]
        lignes += _ligne_agilite(analyse.get("engagement"), nom, cible["name"])
        # **Le boost manquait**, et c'est un grief répété de l'utilisateur :
        # la ligne ne citait que le SCM, si bien que l'analyste devait le
        # ressortir à la main (journal du 2026-08-13). C'est pourtant lui
        # qui décide du contrôle de l'engagement — le plus rapide impose la
        # distance et peut rompre le contact quand l'autre ne le peut pas.
        lignes += [
            f"- mobilité {nom} / {cible['name']} : SCM "
            f"{_nombre(source.get('scm_speed') or 0)} / "
            f"{_nombre(cible.get('scm_speed') or 0)} m/s, boost "
            f"{_nombre(source.get('boost_speed') or 0)} / "
            f"{_nombre(cible.get('boost_speed') or 0)} m/s, tangage "
            f"{_nombre(source.get('pitch') or 0)} / "
            f"{_nombre(cible.get('pitch') or 0)}°/s, lacet "
            f"{_nombre(source.get('yaw') or 0)} / "
            f"{_nombre(cible.get('yaw') or 0)}°/s.",
        ]
        equipages = {(source.get("feu") or {}).get("joueurs_feu_complet"),
                     (cible.get("feu") or {}).get("joueurs_feu_complet")}
        if any(j and j > 1 for j in equipages):
            lignes.append(
                "\nLe feu à équipage complet suppose un joueur par tourelle, "
                "habitée ou télécommandée — seul à bord, chacun retombe à "
                "son feu pilote.")
        return "\n".join(lignes) + _LIMITE_MATCHUP

    if not matchups:
        return (f"Je n'ai aucun matchup classable pour **{nom}** avec les "
                "profils de combat publiés." + _LIMITE_MATCHUP)
    if data.get("mode") == "destruction":
        tete = (
            f"**{nom} peut mécaniquement détruire "
            f"{_nombre(data['destructibles_total'])} vaisseaux de combat "
            f"sur {_nombre(data['analyses_total'])} analysés.**\n\n"
            "Exemples les plus gros d'abord :")
    else:
        tete = (
            f"**{_nombre(data['favorables_total'])} matchups théoriques "
            f"favorables pour {nom}** sur "
            f"{_nombre(data['analyses_total'])} vaisseaux de combat analysés "
            f"({_nombre(data['destructibles_total'])} destructibles par ses "
            "canons stock).")
    lignes = [tete]
    for analyse in matchups:
        cible = analyse["cible"]
        retour = (f"retour ~{_secondes(analyse['temps_cible'])}"
                  if analyse["temps_cible"] is not None else
                  "retour non démontré")
        lignes.append(
            f"- **{cible['name']}** — taille "
            f"{_nombre(cible.get('size') or 0)}, {cible.get('role') or 'rôle non publié'} "
            f"— aller ~{_secondes(analyse['temps_source'])}, {retour}.")
    # **Un compte annoncé doit égaler ce qui est listé.** L'en-tête annonce
    # 32 matchups favorables et la liste en montre 8 : le lecteur compte ce
    # qu'il lit. Trouvé le 2026-08-21 en confrontant le corpus aux règles
    # écrites dans les skills — 1 écart sur 435 tours, celui-ci. Les autres
    # rendus qui tronquent le disent déjà (« et 46 autres ») ; celui-ci non.
    #
    # Et le total repris est **celui que l'en-tête a annoncé**, pas un autre :
    # le mode « destruction » compte les destructibles (66), l'autre les
    # matchups favorables (32). Mon premier jet prenait toujours le second et
    # écrivait « 24 autres » sous un en-tête qui en annonçait 66 — un compte
    # qui contredit son propre titre est pire que pas de compte du tout.
    montres = len(matchups)
    total_annonce = (data.get("destructibles_total")
                     if data.get("mode") == "destruction"
                     else data.get("favorables_total")) or 0
    if total_annonce > montres:
        reste = total_annonce - montres
        lignes.append(
            f"\n_{_nombre(montres)} montrés ici" +
            (" ; un autre suit" if reste == 1
             else f" ; {_nombre(reste)} autres suivent") +
            ", demande-les si tu veux la liste entière._")

    if data.get("non_calculables"):
        lignes.append(
            f"\n{_nombre(data['non_calculables'])} fiche(s) n'ont pas permis "
            "de calculer au moins un sens ; elles ne deviennent pas des "
            "victoires par défaut.")
    if any((a.get("tourelles_source") or a.get("tourelles_cible"))
           for a in matchups):
        lignes.append(
            "\nLes armes en tourelles supposent leurs servants présents et "
            "la cible dans leurs arcs.")
    return "\n".join(lignes) + _LIMITE_MATCHUP


_RENDERERS["matchups_vaisseau"] = render_matchups_vaisseau


#: **L'échelle des issues, donnée par l'utilisateur** (2026-08-14) :
#: « serré c'est jusque 12-8, le reste jusque 16-4 c'est une victoire
#: facile, et au dessus c'est un écrasement ». Soit 60 % et 80 % des
#: combats. Elle a corrigé un défaut réel du classement : le modèle rendait
#: « serré » sur un 16-4, et l'utilisateur l'a relevé — « 16-4 ce n'est pas
#: serré ». Sur les douze duels rejugés à armement égal, l'échelle et la
#: fuite comptée comme défaite font passer le banc de 5/12 à 10/12.
SERRE = 0.60
ECRASEMENT = 0.80


def _issue_dominante(sim: dict[str, Any], a: str, b: str) -> str:
    """La phrase de tête : ce que le joueur retient en une ligne."""
    v_a, v_b = sim["victoires"][a], sim["victoires"][b]
    f_a, f_b = sim["fuites"][a], sim["fuites"][b]
    n = sim["passes"]
    if f_a >= n - f_b and (f_a or f_b):
        # **Le vainqueur du combat se nomme, la fuite est la nuance.**
        # La phrase disait « X ne peut pas gagner — mais il s'en va », sans
        # jamais désigner de vainqueur. Mesuré sur les douze duels rejugés
        # à armement égal : l'utilisateur répond « Terrapin », « Corsair »,
        # « Polaris », « F8C » là où le modèle rendait « fuite Pitbull »,
        # « fuite Arrow », « fuite Fury » — quatre écarts sur sept, tous du
        # même genre. « Ton modèle doit supposer la victoire » : celui qui
        # tient la zone l'emporte, et le fait que l'autre puisse s'en aller
        # reste dit, parce qu'il change ce qu'un joueur décide.
        fuyard, reste = (a, b) if f_a >= f_b else (b, a)
        return (f"**{reste} l'emporte — mais {fuyard} peut s'en aller.** "
                f"Il ne perce pas, et décroche dans "
                f"{_nombre(max(f_a, f_b))} combats sur {_nombre(n)} sans se "
                f"faire rattraper.")
    if v_a or v_b:
        meneur, mv, autre, av = ((a, v_a, b, v_b) if v_a >= v_b
                                 else (b, v_b, a, v_a))
        part = mv / n if n else 0.0
        if part > ECRASEMENT:
            return (f"**{meneur} écrase {autre}** — {_nombre(mv)} victoires "
                    f"sur {_nombre(n)}" +
                    (f", {autre} {_nombre(av)}." if av else
                     f", {autre} ne l'emporte jamais."))
        if part > SERRE:
            return (f"**{meneur} l'emporte facilement** — {_nombre(mv)} "
                    f"victoires sur {_nombre(n)}" +
                    (f", {autre} {_nombre(av)}." if av else
                     f", {autre} ne l'emporte jamais."))
        return (f"**Duel serré.** {meneur} l'emporte {_nombre(mv)} fois sur "
                f"{_nombre(n)}, {autre} {_nombre(av)}"
                + (f", {_nombre(sim['nuls'])} sans vainqueur."
                   if sim.get("nuls") else "."))
    if not v_a and not v_b:
        # **Un duel sans vainqueur est un duel serré, et il faut le dire.**
        # « Ni l'un ni l'autre ne vient à bout de son adversaire » est exact
        # et se lit comme un aveu d'impuissance du modèle : le joueur croit
        # à un calcul raté. C'est pourtant le verdict de terrain sur les
        # duels que l'utilisateur qualifie lui-même de serrés — Talon contre
        # Gladius, Mustang Gamma contre Sabre. Trois correctifs ont été
        # écrits pour « réparer » ce cas avant qu'on mesure qu'il était
        # juste, dont un qui rendait l'Aurora gagnante contre l'Arrow.
        d = sim.get("distance_voulue") or {}
        pourquoi = ""
        if d.get(a) is not None and d.get(b) is not None:
            ecart = abs(d[a] - d[b])
            pourquoi = (
                f" Chacun cherche une distance différente — "
                f"{_nombre(round(d[a]))} m contre {_nombre(round(d[b]))} m — "
                f"et aucun n'impose la sienne assez longtemps pour conclure."
                if ecart >= 100 else
                " Ils se battent à la même distance et s'y usent au même "
                "rythme : rien ne départage.")
        return (f"**Duel serré : aucun des deux ne l'emporte** sur "
                f"{_nombre(n)} combats.{pourquoi}")


def _chance_fr(probabilite: float) -> str:
    """« une chance sur 200 000 » — un ordre de grandeur, pas une décimale.

    Demande de l'utilisateur (2026-08-13) : un outsider écrasé mérite son
    chiffre, « si par exemple un Aurora a 1/1000 d'y arriver ça peut être
    amusant à donner ». On arrondit donc à un seul chiffre significatif
    au-delà de la centaine — annoncer « 1 sur 200 705 » donnerait à un
    modèle maison une précision qu'il n'a pas.
    """
    sur = 1.0 / probabilite
    if sur < 100:
        return f"une chance sur {_nombre(round(sur))}"
    exposant = 10 ** (len(str(int(sur))) - 1)
    return f"environ une chance sur {_nombre(round(sur / exposant) * exposant)}"


def render_simulation_duel(data: dict[str, Any]) -> str:
    """« Sur 10 combats, combien en gagne le Arrow ? »

    Le compte d'abord, puis **pourquoi** — la distance que chacun cherche et
    ce qui l'empêche de percer. Un score seul serait le pire des rendus : il
    a l'air d'un fait mesuré alors qu'il sort d'un modèle, et il n'apprend
    rien sur le combat.
    """
    sim = data["simulation"]
    a = data["attaquant"]["name"]
    b = data["defenseur"]["name"]
    lignes = [_issue_dominante(sim, a, b), ""]

    detail = []
    for nom in (a, b):
        v, f = sim["victoires"][nom], sim["fuites"][nom]
        part = f"{_nombre(v)} victoire" + ("s" if v > 1 else "")
        if f:
            part += f", {_nombre(f)} fuite" + ("s" if f > 1 else "")
        detail.append(f"**{nom}** {part}")
    lignes.append("- " + " contre ".join(detail)
                  + (f", {_nombre(sim['nuls'])} sans vainqueur"
                     if sim["nuls"] else "") + " ;")
    if sim.get("duree_moyenne"):
        lignes.append(f"- combat moyen : **{_secondes(sim['duree_moyenne'])}** "
                      "de feu ;")

    # La distance est le cœur du modèle : celui qui l'impose gagne. La taire
    # rendrait le compte incompréhensible.
    d = sim["distance_voulue"]
    rompt = sim.get("cherche_a_rompre") or {}
    coins = []
    for nom in (a, b):
        if rompt.get(nom):
            coins.append(f"{nom} cherche à rompre le contact")
        else:
            coins.append(f"{nom} veut le combat à **{_nombre(round(d[nom]))} m**")
    lignes.append("- " + ", ".join(coins) + " ;")

    # L'ordre de grandeur d'une issue rare, quand elle existe. Un « jamais »
    # n'est pas de la malchance : c'est un verrou, et il se dit autrement.
    for nom, chance in (sim.get("chance_de_renverser") or {}).items():
        if chance:
            lignes.append(f"- **{nom}** a {_chance_fr(chance)} de renverser "
                          "le duel, avec un pilote nettement meilleur ;")

    net = sim["dps_net"]
    regen = sim["regen"]
    for nom, adverse in ((a, b), (b, a)):
        if net[nom] <= 0:
            devie = (sim.get("armes_deviees") or {}).get(nom) or 0
            if devie:
                cause = (f"{_nombre(devie)} de ses groupes d'armes ricochent "
                         f"sur l'armure de {adverse}")
            else:
                cause = (f"son feu utile reste sous les "
                         f"{_nombre(round(regen[adverse]))} PV/s que le "
                         f"bouclier de {adverse} regagne")
            lignes.append(f"- **{nom} ne perce pas** : {cause}.")

    lignes.append(
        "\n*Simulation maison, pas une mécanique du jeu : le taux de touche "
        "vient du temps de vol des projectiles, de la taille présentée et "
        "des vitesses de rotation ; la distance est choisie par chacun et "
        "tranchée par la vitesse. Les verrous publiés — déflexion d'armure, "
        "seuil d'interruption du bouclier, réserve de munitions — gardent "
        "leur droit de veto. Le talent des pilotes n'est dans aucune "
        f"colonne : il est tiré au sort à chaque combat. Graine "
        f"{sim['graine']}, donc le même compte à chaque appel.*")
    return "\n".join(lignes)


_RENDERERS["simuler_duel"] = render_simulation_duel
