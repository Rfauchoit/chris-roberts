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


def _ligne_agilite(agilite: dict[str, Any] | None,
                   nom_a: str, nom_b: str) -> list[str]:
    """L'agilité v2 en une ligne comparée par facteur, réserve annoncée.

    Le modèle existait dans `bataille()` sans jamais être montré — c'est le
    deuxième défaut de la séquence Scorpius/Hurricane. Elle éclaire, elle
    ne tranche pas : le verdict chiffré reste le temps de feu.
    """
    if not agilite:
        return []
    a, b = agilite.get("attaquant"), agilite.get("defenseur")
    if not a or not b:
        return []
    facteurs = (("poursuite", "poursuite"), ("suivi", "suivi de cible"),
                ("evasion", "évasion"), ("profil", "discrétion de silhouette"))
    parts, avantages_a = [], 0
    for cle, libelle in facteurs:
        va, vb = a.get(cle), b.get(cle)
        if va is None or vb is None:
            continue
        chiffres = f"{va:.2f} / {vb:.2f}".replace(".", ",")
        parts.append(f"{libelle} {chiffres}")
        if va > vb:
            avantages_a += 1
    if not parts:
        return []
    meneur = (nom_a if avantages_a > len(parts) - avantages_a else
              nom_b if avantages_a < len(parts) - avantages_a else None)
    bilan = (f" — avantage {meneur} sur {max(avantages_a, len(parts) - avantages_a)} "
             f"facteurs sur {len(parts)}" if meneur else " — équilibré")
    return [
        f"- agilité ({nom_a} / {nom_b}, plus haut = mieux) : "
        + ", ".join(parts) + bilan
        + ". Modèle maison sur les chiffres publiés ; il éclaire le duel, "
        "le verdict reste le temps de feu ;"]


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
    agilite = _ligne_agilite(data.get("agilite"), att, cible)
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

    if b.get("modele_agilite") == 2:
        agi_att, agi_cible = b["agilite"]
        lignes.append(
            "- Agilité v2 attaquant / cible — poursuite "
            f"{agi_att['poursuite']:.2f} / {agi_cible['poursuite']:.2f}, "
            f"suivi {agi_att['suivi']:.2f} / {agi_cible['suivi']:.2f}, "
            f"évasion {agi_att['evasion']:.2f} / {agi_cible['evasion']:.2f}, "
            f"petit profil {agi_att['profil']:.2f} / {agi_cible['profil']:.2f}.")
        if all(agi_att.get("dimensions") or ()) and all(
                agi_cible.get("dimensions") or ()):
            da, dc = agi_att["dimensions"], agi_cible["dimensions"]
            lignes.append(
                "- Enveloppes L × l × h : "
                f"{_nombre(da[0])} × {_nombre(da[1])} × {_nombre(da[2])} m "
                f"contre {_nombre(dc[0])} × {_nombre(dc[1])} × "
                f"{_nombre(dc[2])} m ; surfaces frontale, latérale et dessus "
                "servent d'approximation de silhouette.")
        if agi_att.get("g_manoeuvre") or agi_cible.get("g_manoeuvre"):
            lignes.append(
                "- Accélération de manœuvre publiée, en équivalent g : "
                f"{_nombre(round(agi_att.get('g_manoeuvre') or 0, 1))} / "
                f"{_nombre(round(agi_cible.get('g_manoeuvre') or 0, 1))} ; "
                "ce n'est pas une limite de blackout du pilote.")

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
        "(déflexion, bouclier, budget) sont la physique publiée, mais la "
        "mobilité v2 (poursuite, suivi, évasion, accélérations et silhouette "
        "— poids maison), le surnombre et "
        "la riposte au DPS soutenu sont mon modèle, pas celui du jeu. Le "
        f"talent des pilotes n'est dans aucune colonne.{hypothese_tourelles}*")


_RENDERERS["bataille"] = render_bataille


_LIMITE_MATCHUP = (
    "\n\n*Comparaison théorique de l'armement stock : bouclier, déflexion, "
    "armure/coque et munitions viennent du jeu. Les missiles sont exclus "
    "car leur interception n'est pas chiffrée. Le talent, le taux de touche "
    "et la géométrie des arcs ne sont publiés nulle part : ce classement ne "
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
            if (analyse.get("facteur_aller") or 1.0) != 1.0 or \
                    (analyse.get("facteur_retour") or 1.0) != 1.0:
                f_a = f"{analyse['facteur_aller']:.2f}".replace(".", ",")
                f_r = f"{analyse['facteur_retour']:.2f}".replace(".", ",")
                lignes.append(
                    f"- Ces temps sont **pondérés par la mobilité et la "
                    f"poursuite des armes** (vitesse de projectile, cadence "
                    f"contre l'évasion — modèle maison, ×{f_a} à l'aller, "
                    f"×{f_r} au retour) ; au feu brut seul : "
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
        lignes += _ligne_agilite(analyse.get("agilite"), nom, cible["name"])
        lignes += [
            f"- mobilité {nom} / {cible['name']} : SCM "
            f"{_nombre(source.get('scm_speed') or 0)} / "
            f"{_nombre(cible.get('scm_speed') or 0)} m/s, tangage "
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
