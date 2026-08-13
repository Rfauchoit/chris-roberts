"""Le duel — un vaisseau peut-il en détruire un autre ?

Le système d'armure de 2026 (4.7) a changé la réponse : elle ne se lit plus
dans les DPS, mais dans trois verrous successifs, chacun publié par le jeu.

1. **Le bouclier.** Il absorbe tout l'énergétique, mais au plus 45 % du
   physique — le balistique travaille l'armure bouclier levé. Sa
   régénération ne s'interrompt que si un impact atteint 0,5 % des PV du
   générateur (`StunParams`, identique sur les 73 générateurs) : en
   dessous, il régénère **sous le feu** — l'observation de l'utilisateur
   sur l'Idris, retrouvée dans les fichiers — et il faut alors un DPS
   supérieur à la régénération brute.
2. **La déflexion.** Un projectile dont l'alpha — par plomb, pas par
   rafale — est sous le seuil de l'armure ricoche : zéro usure, quel que
   soit le volume de tir. Un faisceau continu n'est pas un projectile et
   n'a donc pas d'alpha à comparer ; tant qu'il touche, ses dégâts continus
   maintiennent la régénération du bouclier suspendue. Les tourelles et
   propulseurs exposés restent atteignables, mais ni la coque ni les
   composants internes.
3. **La coque.** L'armure s'use d'abord — et sa chute emporte la
   déflexion — puis la coque. Le budget compte : une arme balistique n'a
   que `ammo_capacity × alpha` de dégâts à livrer, une énergétique tire
   sans fin au rythme du capacitor (`dps_soutenu`).

Les missiles sont une théorie à part : leur alpha passe tout, mais la cible
peut les intercepter — ils se signalent, ils ne font pas le verdict.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .normalize import normalize
from .qualite import multiplicateur_cumule
from .resultats import fraicheur_jeu, qualite_reponse
from .resolver import mots_inexpliques, resolve

# Constantes de version, identiques sur les 73 générateurs de bouclier —
# mesurées le 2026-08-07, pas de colonne à ingérer tant qu'elles ne
# divergent pas (le contrôle est dans les tests unitaires du module).
ABSORPTION_PHYSIQUE = 0.45   # part du physique que le bouclier absorbe (max)
RESISTANCE_PHYSIQUE = 0.25   # réduction sur ce que le bouclier encaisse (max)
SEUIL_INTERRUPTION = 0.005   # part des PV d'un générateur qu'un impact doit
                             # atteindre pour suspendre la régénération


def _armes_stock(con: sqlite3.Connection, ship_uuid: str) -> list[dict[str, Any]]:
    """L'armement offensif stock, pilote et tourelles, avec ses statistiques.

    Les PDC sont publiées dans la même famille, mais leur rôle anti-missile ne
    permet pas de les compter honnêtement comme feu anti-vaisseau.
    """
    return [dict(r) for r in con.execute(
        "SELECT a.name, a.n, a.poste, s.alpha, s.pellets_per_shot, s.dps, "
        "       s.dps_soutenu, s.ammo_capacity, s.weapon_class, s.fire_modes, "
        "       s.dps_physical, s.dps_energy, i.size, "
        "       s.projectile_speed, s.rounds_per_minute "
        "FROM ship_armes a "
        "LEFT JOIN item_stats s ON s.item_uuid = a.weapon_uuid "
        "LEFT JOIN items i ON i.uuid = a.weapon_uuid "
        "WHERE a.ship_uuid = ? AND a.poste <> 'pdc'", (ship_uuid,))]


def _armes_installees(con: sqlite3.Connection, ship_uuid: str,
                      terme: str) -> list[dict[str, Any]]:
    """L'arme déjà montée que désigne « uniquement son S10 ».

    `ship_armes` vient de `Weaponry.FixedWeapons`, mais certains armements
    spéciaux n'y figurent pas : le Supremacy-10T du Tiburon est pourtant un
    hardpoint `weapon` installé. La sélection exclusive part donc du loadout
    réel, par taille générique ou par objet résolu, sans inventer un montage.
    """
    taille = re.search(r"\b(?:s|taille)\s*[- ]?(\d{1,2})\b",
                       normalize(terme))
    params: list[Any] = [ship_uuid]
    filtre = ""
    if taille:
        filtre = " AND i.size = ?"
        params.append(int(taille.group(1)))
    else:
        res = resolve(con, terme, entity_types=("item",))
        if (res.best is None
                or mots_inexpliques(terme, res.best.alias)):
            return []
        filtre = " AND h.installed_uuid = ?"
        params.append(res.best.entity_id)
    return [dict(r) for r in con.execute(
        "SELECT h.installed_uuid AS item_uuid, i.name, i.size, "
        "       COUNT(*) AS n, s.alpha, s.pellets_per_shot, s.dps, "
        "       s.dps_soutenu, s.ammo_capacity, s.weapon_class, "
        "       s.dps_physical, s.dps_energy, s.fire_modes "
        "FROM hardpoints h JOIN items i ON i.uuid = h.installed_uuid "
        "LEFT JOIN item_stats s ON s.item_uuid = h.installed_uuid "
        "WHERE h.ship_uuid = ? AND h.category = 'weapon'" + filtre +
        " GROUP BY h.installed_uuid, i.name, i.size "
        "ORDER BY i.size DESC, i.name", params)]


def _feu_par_poste(con: sqlite3.Connection,
                   ship_uuid: str) -> dict[str, Any]:
    """Le feu stock par poste, et l'équipage qu'il exige.

    Journal du 2026-08-12 : « il n'a pas reconnu que le Scorpius et le
    Hurricane étaient 2 joueurs » — le rendu mettait en avant le seul feu
    pilote. Le nombre de postes vient de `ship_composants_exposes` (une
    ligne par tourelle, publiée), jamais du nombre de canons : la tourelle
    du Hurricane porte 4 canons et un seul artilleur. L'hypothèse est
    celle du duel — un joueur par tourelle, habitée ou télécommandée — et
    `ships.crew` la borne (2 = 2 sur les deux biplaces mesurés).
    """
    par_poste: dict[str, dict[str, Any]] = {}
    for arme in _armes_stock(con, ship_uuid):
        profil = _profil(arme)
        if profil is None:
            continue
        poste = profil.get("poste") or "pilote"
        entree = par_poste.setdefault(poste, {"dps": 0.0, "canons": 0})
        entree["dps"] += profil["dps"]
        entree["canons"] += profil.get("n") or 1
    postes = {p: int(n) for p, n in con.execute(
        "SELECT poste, COUNT(*) FROM ship_composants_exposes "
        "WHERE ship_uuid = ? AND genre = 'tourelle' GROUP BY poste",
        (ship_uuid,))}
    joueurs = (1 + postes.get("habitee", 0)
               + postes.get("telecommandee", 0))
    return {
        "par_poste": par_poste,
        "postes_tourelles": postes,
        "dps_total": sum(e["dps"] for e in par_poste.values()),
        "dps_pilote": (par_poste.get("pilote") or {}).get("dps", 0.0),
        "joueurs_feu_complet": joueurs,
    }


def _est_faisceau(arme: dict[str, Any]) -> bool:
    """Un mode Beam qui publie un DPS, pas un projectile d'alpha nul."""
    modes = str(arme.get("fire_modes") or "").lower()
    return "beam" in modes and bool(arme.get("dps_soutenu") or arme.get("dps"))


def _profil(arme: dict[str, Any]) -> dict[str, Any] | None:
    """Ce que le duel a besoin de savoir d'une arme.

    L'alpha se juge **par plomb** : la déflexion s'applique à chaque
    projectile, et l'alpha d'item_stats est celui de la rafale entière.
    """
    n = arme.get("n") or 1
    plombs = arme.get("pellets_per_shot") or 1
    continu = _est_faisceau(arme)
    if not arme.get("alpha") and not continu:
        return None
    balistique = (arme.get("weapon_class") == "ballistic"
                  or (arme.get("dps_physical") or 0) > (arme.get("dps_energy") or 0))
    dps = (arme.get("dps_soutenu") or arme.get("dps") or 0) * n
    return {
        "name": arme.get("name"),
        "n": n,
        "poste": arme.get("poste"),
        "type": "physical" if balistique else "energy",
        "continu": continu,
        "par_plomb": None if continu else arme["alpha"] / plombs,
        # La poursuite d'arme (sprint 21) se juge sur ces deux colonnes
        # publiées : un projectile lent et une cadence basse ratent une
        # cible vive.
        "projectile_speed": arme.get("projectile_speed"),
        "rpm": arme.get("rounds_per_minute"),
        "dps": dps,
        # Une balistique livre au plus sa réserve ; une énergétique n'a pas
        # de fin, le capacitor est déjà dans dps_soutenu.
        "budget": (arme["alpha"] * (arme.get("ammo_capacity") or 0) * n
                   if not continu and balistique
                   and arme.get("ammo_capacity") else None),
    }


def _defense(con: sqlite3.Connection, ship_uuid: str) -> dict[str, Any] | None:
    ligne = _dict(_row(con, "SELECT * FROM ship_combat WHERE ship_uuid = ?",
                       ship_uuid))
    if ligne is None:
        return None
    # Le seuil d'interruption se calcule par **générateur** : le jeu publie
    # le ratio sur les PV d'un générateur, pas sur la somme. Le compte des
    # générateurs se lit dans les points d'emport.
    n_gen = con.execute(
        "SELECT COUNT(*) FROM hardpoints WHERE ship_uuid = ? "
        "AND (installed_type = 'Shield' OR category = 'shield')",
        (ship_uuid,)).fetchone()[0] or 1
    ligne["n_generateurs"] = n_gen
    return ligne


def _remplacer_bouclier(con: sqlite3.Connection, defense: dict[str, Any],
                        terme: str, qualite: float | None = None
                        ) -> dict[str, Any] | None:
    """« … avec un autre bouclier que le stock » : même nombre de
    générateurs, les PV et la régénération du modèle nommé."""
    res = resolve(con, terme, entity_types=("item",))
    # Le score seul ne protège pas : « deadbolt iii peut » sortait un
    # *Sol-III* à 85,5. Un terme d'équipement doit être entièrement
    # expliqué par ce qu'il résout — la leçon d'« omnisky xi ».
    if res.best is None or mots_inexpliques(terme, res.best.alias):
        return None
    stats = _dict(_row(con, "SELECT s.shield_health, s.shield_regen, i.name "
                            "FROM item_stats s JOIN items i ON i.uuid = s.item_uuid "
                            "WHERE s.item_uuid = ?", res.best.entity_id))
    if not stats or not stats.get("shield_health"):
        return None
    n = defense.get("n_generateurs") or 1
    defense = dict(defense)
    defense["shield_hp"] = stats["shield_health"] * n
    defense["shield_regen"] = (stats.get("shield_regen") or 0) * n
    defense["bouclier_nomme"] = stats["name"]
    if qualite is not None:
        lu = multiplicateur_cumule(
            con, res.best.entity_id, "shield_maxhealth", qualite)
        if lu is not None:
            defense["shield_hp"] *= lu[0]
            defense["qualite_bouclier"] = qualite
            defense["mult_qualite_bouclier"] = lu[0]
            defense["composants_qualite_bouclier"] = lu[1]
            defense["borne_qualite_bouclier"] = lu[2]
    return defense


def _qualifier_boucliers_stock(con: sqlite3.Connection, ship_uuid: str,
                                defense: dict[str, Any], qualite: float
                                ) -> dict[str, Any]:
    """Applique une qualité aux générateurs installés, un par point d'emport.

    On recalcule les PV depuis chaque objet monté : un vaisseau peut mélanger
    plusieurs modèles de bouclier. Si un seul d'entre eux n'a pas de plage
    publiée, aucun multiplicateur n'est appliqué au total.
    """
    lignes = con.execute(
        "SELECT h.installed_uuid, s.shield_health "
        "FROM hardpoints h JOIN item_stats s ON s.item_uuid=h.installed_uuid "
        "WHERE h.ship_uuid=? "
        "  AND (h.installed_type='Shield' OR h.category='shield') "
        "  AND s.shield_health IS NOT NULL",
        (ship_uuid,),
    ).fetchall()
    if not lignes:
        return defense
    total_base = 0.0
    total_qualite = 0.0
    composants = 0
    bornes = []
    for ligne in lignes:
        lu = multiplicateur_cumule(
            con, ligne["installed_uuid"], "shield_maxhealth", qualite)
        if lu is None:
            return defense
        total_base += ligne["shield_health"]
        total_qualite += ligne["shield_health"] * lu[0]
        composants = max(composants, lu[1])
        bornes.append(lu[2])
    if not total_base:
        return defense
    defense = dict(defense)
    defense["shield_hp"] = total_qualite
    defense["qualite_bouclier"] = qualite
    defense["mult_qualite_bouclier"] = total_qualite / total_base
    defense["composants_qualite_bouclier"] = composants
    defense["borne_qualite_bouclier"] = min(bornes)
    return defense


def _mult_qualite(con: sqlite3.Connection, weapon_uuid: str,
                  qualite: float) -> tuple[float, int] | None:
    """Le multiplicateur de dégâts d'une arme fabriquée à cette qualité.

    Le jeu publie l'effet **par composant** (Cycler et Barrel portent chacun
    leur plage sur `weapon_damage`) sans dire comment ils se combinent — le
    produit est une **hypothèse**, et le rendu l'annonce. Comparer deux
    qualités reste exact : c'est le rapport de deux valeurs publiées.
    """
    lu = multiplicateur_cumule(con, weapon_uuid, "weapon_damage", qualite)
    return (lu[0], lu[1]) if lu is not None else None


def _remplacer_armes(con: sqlite3.Connection, armes: list[dict[str, Any]],
                     terme: str, qualite: float | None = None
                     ) -> tuple[list[dict[str, Any]], str | None, float | None]:
    """« … équipé de Deadbolt III (qualité 900) » : la même arme sur chaque
    affût qui l'accepte, aux dégâts de la qualité demandée."""
    res = resolve(con, terme, entity_types=("item",))
    if res.best is None or mots_inexpliques(terme, res.best.alias):
        return armes, None, None
    neuve = _dict(_row(con, "SELECT s.*, i.name, i.size FROM item_stats s "
                            "JOIN items i ON i.uuid = s.item_uuid "
                            "WHERE s.item_uuid = ?", res.best.entity_id))
    if not neuve or (not neuve.get("alpha") and not _est_faisceau(neuve)):
        return armes, None, None
    mult = None
    if qualite is not None:
        lu = _mult_qualite(con, res.best.entity_id, qualite)
        if lu is not None:
            mult = lu[0]
            for champ in ("alpha", "dps", "dps_soutenu"):
                if neuve.get(champ):
                    neuve[champ] = neuve[champ] * mult
    remplacees = []
    nombre_remplace = 0
    for arme in armes:
        # L'affût de l'arme d'origine accepte sa taille : une arme neuve
        # plus grosse n'y rentre pas, elle garde alors l'arme stock.
        if arme.get("size") is not None and (neuve.get("size") or 0) <= arme["size"]:
            garde = dict(neuve)
            garde["n"] = arme.get("n") or 1
            garde["poste"] = arme.get("poste")
            remplacees.append(garde)
            nombre_remplace += 1
        else:
            remplacees.append(arme)
    # Dire « équipé de X » alors qu'aucun affût ne l'accepte est pire qu'un
    # refus : le calcul continuerait silencieusement avec le stock.
    if not nombre_remplace:
        return armes, None, None
    return remplacees, neuve["name"], mult


def _conseils(con: sqlite3.Connection, defense: dict[str, Any],
              taille_max: int | None) -> dict[str, Any]:
    """Ce qui permettrait de passer : les armes au-dessus des seuils.

    On ne conseille que ce qui **rentre sur les affûts de l'attaquant** —
    citer un canon de taille 5 à un chasseur en taille 3 n'aide personne —
    et on classe par DPS parmi ce qui passe la déflexion.
    """
    if not taille_max:
        return {}
    armes = [dict(r) for r in con.execute(
        "SELECT i.name, i.size, s.alpha, s.pellets_per_shot, s.dps, "
        "       s.weapon_class, s.item_uuid AS uuid "
        "FROM item_stats s JOIN items i ON i.uuid = s.item_uuid "
        "WHERE i.type = 'WeaponGun' AND s.alpha > 0 "
        "  AND i.mount_usable = 1 AND i.flight_ready = 1 AND i.size <= ?",
        (taille_max,))]
    passantes, presque = [], []
    for a in armes:
        par_plomb = a["alpha"] / (a.get("pellets_per_shot") or 1)
        seuil = (defense.get("defl_physical")
                 if a.get("weapon_class") == "ballistic"
                 else defense.get("defl_energy")) or 0
        a["par_plomb"], a["seuil"] = par_plomb, seuil
        if par_plomb >= seuil:
            passantes.append(a)
        elif seuil:
            presque.append(a)
    passantes.sort(key=lambda a: -(a.get("dps") or 0))

    # Aucune arme au catalogue ne passe : la fabrication peut-elle aider ?
    # On cherche la plus petite qualité dont le multiplicateur suffit —
    # par pas de 10, l'échelle du jeu allant de 0 à 1000.
    par_qualite = []
    if not passantes:
        presque.sort(key=lambda a: a["seuil"] / a["par_plomb"])
        for a in presque[:6]:
            for q in range(500, 1001, 10):
                lu = _mult_qualite(con, a["uuid"], q)
                if lu is None:
                    break
                if a["par_plomb"] * lu[0] >= a["seuil"]:
                    par_qualite.append({**a, "qualite_min": q})
                    break
            if len(par_qualite) >= 3:
                break

    return {"armes": passantes[:4], "par_qualite": par_qualite,
            "taille_max": taille_max}


def _verrous(profils: list[dict[str, Any]],
             defense: dict[str, Any]) -> dict[str, Any]:
    """Les trois verrous, dans l'ordre où le jeu les oppose."""
    shield_hp = defense.get("shield_hp") or 0
    regen = defense.get("shield_regen") or 0
    hp_par_gen = shield_hp / (defense.get("n_generateurs") or 1)
    seuil_stun = SEUIL_INTERRUPTION * hp_par_gen

    # --- verrou 1 : le bouclier
    dps_bouclier = sum(
        p["dps"] * (1.0 if p["type"] == "energy"
                    else ABSORPTION_PHYSIQUE * (1 - RESISTANCE_PHYSIQUE))
        for p in profils)
    # Le seuil d'interruption 4.7 est formulé par projectile. Un faisceau
    # continu n'a aucun impact discret auquel attribuer un alpha, mais tant
    # qu'il inflige des dégâts il maintient le bouclier sous feu et empêche
    # sa régénération de reprendre.
    interrompt = any(
        not p.get("continu") and p["par_plomb"] >= seuil_stun
        for p in profils)
    sous_feu_continu = any(p.get("continu") and p["dps"] > 0
                           for p in profils)
    if shield_hp <= 0:
        bouclier = {"tombe": True, "temps": 0.0}
    elif (interrompt or sous_feu_continu) and dps_bouclier > 0:
        bouclier = {"tombe": True, "temps": shield_hp / dps_bouclier}
    elif dps_bouclier > regen:
        bouclier = {"tombe": True,
                    "temps": shield_hp / (dps_bouclier - regen)}
    else:
        bouclier = {"tombe": False, "temps": None}
    bouclier.update(interrompt=interrompt, seuil_stun=seuil_stun,
                    sous_feu_continu=sous_feu_continu,
                    regen=regen, hp=shield_hp, dps=dps_bouclier)

    # --- verrou 2 : la déflexion, arme par arme
    passantes, deviees = [], []
    for p in profils:
        seuil = defense.get(f"defl_{p['type']}") or 0
        # La déflexion publiée vise elle aussi les projectiles : classer un
        # beam alpha 0 comme un projectile dévié inventerait une mécanique.
        (passantes if p.get("continu") or p["par_plomb"] >= seuil
         else deviees).append(
            {**p, "seuil": seuil})

    # --- verrou 3 : armure puis coque, avec le budget
    a_livrer = (defense.get("armor_health") or 0) + (defense.get("hull_health") or 0)
    # Bouclier levé, seul le balistique traversant travaille ; bouclier
    # tombé, tout ce qui passe la déflexion compte.
    if bouclier["tombe"]:
        dps_utile = sum(p["dps"] for p in passantes)
    else:
        dps_utile = sum(p["dps"] * (1 - ABSORPTION_PHYSIQUE)
                        for p in passantes if p["type"] == "physical")
    budget = sum(p["budget"] for p in passantes if p["budget"] is not None)
    sans_fin = any(p["budget"] is None for p in passantes)
    budget_ok = sans_fin or budget >= a_livrer + (
        0 if bouclier["tombe"] else 0)  # le bouclier prend sa part au passage
    coque = {"possible": dps_utile > 0 and budget_ok,
             "a_livrer": a_livrer, "dps_utile": dps_utile,
             "budget": None if sans_fin else budget,
             "temps": (a_livrer / dps_utile) if dps_utile > 0 else None}

    verdict = coque["possible"] and (bouclier["tombe"] or dps_utile > 0)
    return {"verdict": verdict, "bouclier": bouclier,
            "passantes": passantes, "deviees": deviees, "coque": coque}


def _desarmement(con: sqlite3.Connection, ship_uuid: str,
                 profils: list[dict[str, Any]]) -> dict[str, Any]:
    """Temps de tir ciblé sur les tourelles et propulseurs extérieurs.

    Ces pièces ont leurs propres PV, résistances et seuils. Elles ne prennent
    donc pas la déflexion de la coque par procuration : chaque profil est
    retesté contre **leur** seuil. Le calcul porte sur une pièce à la fois ;
    il ne prétend ni que toutes sont dans le même arc, ni que le ciblage est
    automatique.
    """
    lignes = con.execute(
        "SELECT genre, poste, pv, mult_physical, mult_energy, "
        "       seuil_physical, seuil_energy, COUNT(*) AS n "
        "FROM ship_composants_exposes WHERE ship_uuid = ? "
        "GROUP BY genre, poste, pv, mult_physical, mult_energy, "
        "         seuil_physical, seuil_energy "
        "ORDER BY CASE genre WHEN 'tourelle' THEN 0 ELSE 1 END, pv",
        (ship_uuid,)).fetchall()
    groupes = []
    sans_pv = 0
    for ligne in lignes:
        composant = dict(ligne)
        if not composant.get("pv"):
            sans_pv += composant["n"]
            continue
        dps = budget = 0.0
        sans_fin = False
        passent = 0
        for profil in profils:
            type_degats = profil["type"]
            seuil = composant.get(f"seuil_{type_degats}") or 0
            if (not profil.get("continu")
                    and (profil.get("par_plomb") or 0) < seuil):
                continue
            passent += profil.get("n") or 1
            mult = composant.get(f"mult_{type_degats}")
            mult = 1.0 if mult is None else mult
            dps += profil["dps"] * mult
            if profil.get("budget") is None:
                sans_fin = True
            else:
                budget += profil["budget"] * mult
        possible = dps > 0 and (sans_fin or budget >= composant["pv"])
        composant.update(
            dps=dps, budget=None if sans_fin else budget,
            possible=possible,
            temps=composant["pv"] / dps if possible else None,
            armes_passantes=passent)
        groupes.append(composant)
    return {
        "groupes": groupes,
        "connus": sum(g["n"] for g in groupes),
        "sans_pv": sans_pv,
    }


# ------------------------------------------------------------ la bataille
#
# « 5 Gladius contre un C2 » — l'outil est **pour le fun** et le dit : les
# verrous du duel gardent leur droit de veto (la physique publiée), mais la
# mobilité, le surnombre et les états (« sans bouclier », « à l'arrêt »)
# pèsent par des poids maison, annoncés dans la réponse. Demande de
# l'utilisateur du 2026-08-07, assumée comme un jeu.

#: Le score v2 ne met plus vitesse, rotation et silhouette dans une moyenne
#: opaque. Chaque sous-score est normalisé sur un chasseur typique, puis les
#: rapports sont bornés : l'agilité module un tir, elle ne remplace jamais les
#: verrous physiques. Poids maison, donc rendus et versionnés.
_CHAMPS_AGILITE_V2 = (
    "scm_speed", "boost_speed", "boost_backward", "pitch", "yaw", "roll",
    "pitch_boosted", "yaw_boosted", "roll_boosted", "accel_main",
    "accel_retro", "accel_maneuver", "accel_main_boosted",
    "accel_retro_boosted", "accel_maneuver_boosted", "zero_to_scm",
    "scm_to_zero", "boost_capacity", "boost_regen", "boost_regen_time",
    "length", "width", "height", "mass", "size",
)

#: Les modulations comprises, par côté. Tout le reste est **dit incompris**
#: plutôt qu'ignoré en silence — règle 1 de la grille.
_MODULATIONS = (
    (r"sans (?:bouclier|boucliers|shield|shields)", "sans_bouclier"),
    (r"a l arret|immobile|statique|qui ne bouge pas", "a_l_arret"),
    (r"(?:avec |a )?(?:la )?moitie de (?:sa |la )?vie", "moitie_vie"),
    (r"(?:avec |a )?(?:la )?moitie de(?:s| ses) armes", "moitie_armes"),
)

_MODULATION_FR = {
    "sans_bouclier": "sans bouclier",
    "a_l_arret": "à l'arrêt",
    "moitie_vie": "à la moitié de sa vie",
    "moitie_armes": "avec la moitié de ses armes",
}


def _moyenne_normalisee(
        ship: dict[str, Any],
        termes: tuple[tuple[str, float, float, bool], ...]) -> float | None:
    """Moyenne des seuls faits présents ; un NULL ne devient jamais zéro."""
    total, poids_lus = 0.0, 0.0
    for champ, poids, norme, inverse in termes:
        valeur = ship.get(champ)
        if valeur is None or valeur <= 0:
            continue
        rapport = norme / valeur if inverse else valeur / norme
        total += poids * rapport
        poids_lus += poids
    return total / poids_lus if poids_lus else None


def _profil_agilite_v2(ship: dict[str, Any]) -> dict[str, Any]:
    """Quatre facteurs séparés : poursuite, suivi, évasion et silhouette."""
    poursuite = _moyenne_normalisee(ship, (
        ("scm_speed", 0.15, 220.0, False),
        ("boost_speed", 0.10, 500.0, False),
        ("boost_backward", 0.10, 250.0, False),
        ("accel_main", 0.25, 150.0, False),
        ("accel_retro", 0.20, 60.0, False),
        ("zero_to_scm", 0.20, 1.5, True),
    ))
    suivi = _moyenne_normalisee(ship, (
        ("pitch", 0.35, 60.0, False),
        ("yaw", 0.45, 55.0, False),
        ("pitch_boosted", 0.08, 72.0, False),
        ("yaw_boosted", 0.10, 66.0, False),
        # Le roulis prépare une rotation mais ne place pas le nez sur la cible.
        ("roll", 0.02, 180.0, False),
    ))
    evasion = _moyenne_normalisee(ship, (
        ("accel_maneuver", 0.30, 150.0, False),
        ("accel_maneuver_boosted", 0.15, 220.0, False),
        ("accel_retro", 0.15, 60.0, False),
        ("scm_to_zero", 0.15, 4.0, True),
        ("boost_backward", 0.10, 250.0, False),
        ("boost_capacity", 0.05, 20.0, False),
        ("boost_regen", 0.05, 0.75, False),
        ("boost_regen_time", 0.05, 27.0, True),
    ))
    # Repli pour une ancienne base : rotations et SCM valent mieux qu'un zéro,
    # mais la couverture dira que les accélérations n'ont pas été lues.
    if evasion is None:
        evasion = _moyenne_normalisee(ship, (
            ("scm_speed", 0.35, 220.0, False),
            ("pitch", 0.30, 60.0, False),
            ("yaw", 0.35, 55.0, False),
        ))

    longueur, largeur, hauteur = (
        ship.get("length"), ship.get("width"), ship.get("height"))
    surfaces = None
    if all(v is not None and v > 0 for v in (longueur, largeur, hauteur)):
        surfaces = {
            "frontale": largeur * hauteur,
            "laterale": longueur * hauteur,
            "dessus": longueur * largeur,
        }
        surface_moyenne = (
            surfaces["frontale"] * surfaces["laterale"]
            * surfaces["dessus"]) ** (1 / 3)
        # Plus la silhouette est petite, plus sa difficulté de touche dépasse 1.
        profil = min(max((120.0 / surface_moyenne) ** 0.5, 0.5), 2.0)
    else:
        taille = ship.get("size")
        profil = min(max(2.0 / taille, 0.5), 2.0) if taille else 1.0

    presents = sum(ship.get(c) is not None for c in _CHAMPS_AGILITE_V2)
    return {
        "poursuite": poursuite or 1.0,
        "suivi": suivi or 1.0,
        "evasion": evasion or 1.0,
        "profil": profil,
        "surfaces": surfaces,
        "dimensions": (longueur, largeur, hauteur),
        "masse": ship.get("mass"),
        "g_main": ((ship.get("accel_main") or 0) / 9.80665 or None),
        "g_manoeuvre": ((ship.get("accel_maneuver") or 0) / 9.80665 or None),
        "couverture": presents / len(_CHAMPS_AGILITE_V2),
    }


def _facteur_tir(attaquant: dict[str, Any], cible: dict[str, Any]) -> float:
    """Capacité de suivi contre poursuite, évasion et profil de la cible."""
    pression = 0.35 * attaquant["poursuite"] + 0.65 * attaquant["suivi"]
    difficulte = (0.20 * cible["poursuite"] + 0.60 * cible["evasion"]
                  + 0.20 * cible["profil"])
    return min(max((pression / max(difficulte, 0.1)) ** 0.5, 0.55), 1.45)


def _poursuite_d_arme(profil: dict[str, Any]) -> float | None:
    """L'arme aussi doit toucher (sprint 21, demande de l'utilisateur) :
    « un canon lent a peu de chances de toucher un Arrow, contrairement à
    un repeater rapide ».

    Deux colonnes publiées, ancrées sur les médianes mesurées du catalogue
    (1 196 m/s de projectile, 150 c/min) : canon médian 1 112 / 100,
    repeater 1 440 / 500, gatling 1 332 / 1 200. La cadence passe en
    racine — 1 200 c/min ne valent pas huit fois 150. Un faisceau continu
    touche instantanément : score maximal.
    """
    if profil.get("continu"):
        return 1.3
    vitesse, cadence = profil.get("projectile_speed"), profil.get("rpm")
    if not vitesse and not cadence:
        return None
    norm_v = min((vitesse or 1196.0) / 1196.0, 1.25)
    norm_c = min(((cadence or 150.0) / 150.0) ** 0.5, 1.3)
    return 0.55 * norm_v + 0.45 * norm_c


def _facteur_poursuite_armes(profils: list[dict[str, Any]],
                             cible: dict[str, Any] | None) -> float:
    """Le facteur du bord entier, pondéré par la part de DPS de chaque arme,
    opposé à l'évasion de la cible.

    Bornes 0,75–1,10, plus serrées que la mobilité : le modèle module des
    temps, il n'écrase ni la déflexion ni le budget de munitions — et un
    poids maison discret se corrige plus facilement qu'un poids qui a tout
    recouvert.
    """
    couples = [(p["dps"], _poursuite_d_arme(p)) for p in profils
               if p.get("dps")]
    couples = [(d, s) for d, s in couples if s is not None]
    if not couples or cible is None:
        return 1.0
    total = sum(d for d, _ in couples)
    moyen = sum(d * s for d, s in couples) / total
    exigence = max(cible.get("evasion") or 1.0, 0.5)
    return min(max((moyen / exigence) ** 0.5, 0.75), 1.10)


def _charger_profil_vol(con: sqlite3.Connection,
                        ship_uuid: str) -> dict[str, Any]:
    """Lit aussi une base pré-v2 : les colonnes absentes deviennent NULL."""
    presentes = {r[1] for r in con.execute("PRAGMA table_xinfo(ships)")}
    champs = [c for c in ("pilot_dps",) + _CHAMPS_AGILITE_V2
              if c in presentes]
    return _dict(_row(
        con, f"SELECT {','.join(champs)} FROM ships WHERE uuid = ?",
        ship_uuid)) or {}


def _moduler_defense(defense: dict[str, Any],
                     modulations: set[str]) -> dict[str, Any]:
    d = dict(defense)
    if "sans_bouclier" in modulations:
        d["shield_hp"], d["shield_regen"] = 0, 0
    if "moitie_vie" in modulations:
        for champ in ("hull_health", "armor_health"):
            if d.get(champ):
                d[champ] = d[champ] / 2
    return d


def _qualifier_combat(con: sqlite3.Connection,
                      resultat: dict[str, Any]) -> dict[str, Any]:
    """Ajoute le contrat commun sans confondre limite et verdict négatif."""
    manques = []
    if resultat.get("sans_defense_connue"):
        manques.append("défense publiée absente pour la cible")
    if resultat.get("arme_refusee"):
        manques.append(
            f"arme non résolue ou incompatible : {resultat['arme_refusee']}")
    if resultat.get("bouclier_refuse"):
        manques.append(
            "bouclier non résolu ou sans statistiques compatibles : "
            f"{resultat['bouclier_refuse']}")

    non_chiffrees = resultat.get("armes_non_chiffrees") or []
    noms_non_chiffres = list(dict.fromkeys(
        a.get("name") or a.get("item_name") or "arme sans nom"
        for a in non_chiffrees))
    if noms_non_chiffres:
        manques.append("profil de dégâts absent pour "
                       + ", ".join(noms_non_chiffres))
    if (resultat.get("qualite") is not None
            and resultat.get("arme_nommee")
            and resultat.get("mult_qualite") is None):
        manques.append("effet de qualité de l'arme non chiffrable")
    if (resultat.get("qualite_bouclier") is not None
            and resultat.get("mult_qualite_bouclier") is None):
        manques.append("effet de qualité du bouclier non chiffrable")
    manques.extend(f"modulation de combat non comprise : {texte}"
                   for texte in resultat.get("incomprises") or [])
    if (resultat.get("duel") is None and not manques
            and not resultat.get("sans_defense_connue")):
        manques.append("duel non calculable avec les profils publiés")

    attaquant = resultat.get("attaquant") or {}
    defenseur = resultat.get("defenseur") or {}
    bataille_calculee = resultat.get("bataille")
    duel = resultat.get("duel") or {}
    bilan = resultat.get("bilan") or {}
    verdict = (bataille_calculee.get("victoire")
               if isinstance(bataille_calculee, dict)
               else (bilan.get("suffit")
                     if "suffit" in bilan else duel.get("verdict")))
    sources = ["jeu"]
    if "bataille" in resultat:
        sources.append("modele_bataille")
    contrat = qualite_reponse(
        faits={
            "attaquant": attaquant.get("name"),
            "cible": defenseur.get("name"),
            "armes_chiffrees": len(resultat.get("armes") or []),
            "verdict": verdict,
            "arme_seule": bool(resultat.get("arme_seule")),
        },
        manques=manques, sources=sources,
        fraicheur={"jeu": fraicheur_jeu(con)})
    resultat["qualite_reponse"] = contrat
    resultat["complet"] = contrat["complet"]
    return resultat


def bataille(con: sqlite3.Connection, query: str, *,
             cible: str | None = None, n: int = 1,
             arme: str | None = None, arme_seule: bool = False,
             qualite: float | None = None,
             modulations: list[str] | None = None,
             modulations_cible: list[str] | None = None,
             incomprises: list[str] | None = None) -> dict[str, Any]:
    """« Est-ce que 5 Gladius peuvent détruire un C2 ? » — pour le fun.

    Les verrous du duel restent la physique (déflexion, bouclier, budget) ;
    par-dessus, le surnombre multiplie le feu, la mobilité relative le
    module, et la cible riposte de son DPS pilote. Poids maison, annoncés.
    """
    duel = peut_detruire(con, query, cible=cible, arme=arme,
                         arme_seule=arme_seule, qualite=qualite)
    if duel.get("sans_defense_connue") or duel.get("duel") is None:
        duel.update(n=n, bataille=None,
                    incomprises=incomprises or [])
        return _qualifier_combat(con, duel)

    mods_att = set(modulations or [])
    mods_cible = set(modulations_cible or [])
    defense = _moduler_defense(duel["defense"], mods_cible)

    profils = duel["armes"]
    if "moitie_armes" in mods_att:
        profils = [dict(p, dps=p["dps"] / 2,
                        budget=(p["budget"] / 2 if p.get("budget") else None))
                   for p in profils]
    # Le surnombre : N fois le feu et le budget.
    groupe = [dict(p, dps=p["dps"] * n,
                   budget=(p["budget"] * n if p.get("budget") else None))
              for p in profils]
    verrous = _verrous(groupe, defense)

    att_row = _charger_profil_vol(con, duel["attaquant"]["uuid"])
    cible_row = _charger_profil_vol(con, duel["defenseur"]["uuid"])
    profil_att = _profil_agilite_v2(att_row)
    profil_cible = _profil_agilite_v2(cible_row)
    # Une cible à l'arrêt n'esquive plus rien.
    if "a_l_arret" in mods_cible:
        profil_cible["poursuite"] = 0.1
        profil_cible["evasion"] = 0.1
    if "a_l_arret" in mods_att:
        profil_att["poursuite"] = 0.1
        profil_att["evasion"] = 0.1
    # Les deux sens ne sont plus supposés réciproques : une petite silhouette
    # et un bon suivi ne se compensent pas nécessairement à l'identique.
    facteur = _facteur_tir(profil_att, profil_cible)
    facteur_retour = _facteur_tir(profil_cible, profil_att)

    # Le temps pour tomber la cible, mobilité comprise.
    t_cible = None
    if verrous["verdict"]:
        a_livrer = verrous["coque"]["a_livrer"] + (
            verrous["bouclier"]["hp"] if verrous["bouclier"]["tombe"] else 0)
        dps = verrous["coque"]["dps_utile"] * facteur
        t_cible = a_livrer / dps if dps > 0 else None

    # La riposte suit désormais la même source que l'attaquant : armes fixes,
    # tourelles habitées et télécommandées, hors PDC. Le `pilot_dps` reste un
    # repli pour les rares profils non chiffrés. Avant la 4.9, ne lire que ce
    # champ amputait notamment les canonnières de leurs postes d'équipage.
    profils_cible = [p for arme in _armes_stock(con, duel["defenseur"]["uuid"])
                     if (p := _profil(arme)) is not None]
    riposte = (sum(p["dps"] for p in profils_cible)
               if profils_cible else cible_row.get("pilot_dps") or 0)
    riposte_tourelles = sum(
        p.get("n") or 1 for p in profils_cible
        if p.get("poste") in ("habitee", "telecommandee"))
    if "moitie_armes" in mods_cible:
        riposte /= 2
    ehp_att_ligne = _dict(_row(con, "SELECT shield_hp, armor_health, "
                                    "hull_health FROM ship_combat "
                                    "WHERE ship_uuid = ?",
                               duel["attaquant"]["uuid"])) or {}
    ehp_att = sum(ehp_att_ligne.get(k) or 0
                  for k in ("shield_hp", "armor_health", "hull_health"))
    if "moitie_vie" in mods_att:
        ehp_att /= 2
    t_groupe = (n * ehp_att / (riposte * facteur_retour)
                if riposte and ehp_att else None)

    victoire = t_cible is not None and (t_groupe is None or t_cible < t_groupe)
    duel.update(
        n=n, duel_verrous=verrous,
        bataille={"victoire": victoire, "t_cible": t_cible,
                  "t_groupe": t_groupe, "facteur_mobilite": facteur,
                  "facteur_riposte": facteur_retour,
                  "modele_agilite": 2,
                  "agilite": (profil_att, profil_cible), "riposte": riposte,
                  "riposte_armes_tourelles": riposte_tourelles,
                  "mods_att": sorted(mods_att),
                  "mods_cible": sorted(mods_cible)},
        incomprises=incomprises or [])
    return _qualifier_combat(con, duel)


def _meilleur_armement(con: sqlite3.Connection, ship_uuid: str,
                       defense: dict[str, Any],
                       cible_profil: dict[str, Any] | None,
                       criteres: dict[str, Any]) -> tuple[list[dict], str]:
    """Le loadout optimal pour ce duel (sprint 21) : par affût d'arme, la
    meilleure arme compatible qui **passe la déflexion de la cible**.

    « Meilleur » n'est pas « le plus de DPS brut » : une arme qui ricoche
    ne vaut rien. On classe donc parmi les passantes, par DPS soutenu
    pondéré de la poursuite d'arme quand un profil de cible est fourni —
    « les armes les plus adaptées pour toucher une cible rapide » devient
    exactement ce tri. Chaque affût prend la meilleure de sa taille ; le
    loadout retenu se nomme dans la réponse.
    """
    affuts = [dict(r) for r in con.execute(
        "SELECT max_size, COUNT(*) AS n FROM hardpoints "
        "WHERE ship_uuid = ? AND category = 'weapon_mount' "
        "AND max_size IS NOT NULL GROUP BY max_size", (ship_uuid,))]
    if not affuts:
        return [], ""
    armes = []
    for taille_row in {a["max_size"] for a in affuts}:
        candidats = [dict(r) for r in con.execute(
            "SELECT i.uuid, i.name, i.size, s.alpha, s.pellets_per_shot, "
            "       s.dps, s.dps_soutenu, s.ammo_capacity, s.weapon_class, "
            "       s.fire_modes, s.dps_physical, s.dps_energy, "
            "       s.projectile_speed, s.rounds_per_minute "
            "FROM item_stats s JOIN items i ON i.uuid = s.item_uuid "
            "WHERE i.type = 'WeaponGun' AND i.size = ? AND s.alpha > 0 "
            "AND i.mount_usable = 1 AND i.flight_ready = 1",
            (taille_row,))]
        # Filtre de critères explicites (grade, famille) quand donnés.
        if criteres.get("weapon_class"):
            candidats = [c for c in candidats
                         if c.get("weapon_class") == criteres["weapon_class"]]
        passantes = []
        for c in candidats:
            profil = _profil(dict(c, n=1))
            if profil is None:
                continue
            seuil = (defense.get("defl_physical")
                     if profil["type"] == "physical"
                     else defense.get("defl_energy")) or 0
            if profil["par_plomb"] is not None and profil["par_plomb"] < seuil:
                continue
            score = profil["dps"]
            if cible_profil is not None:
                pa = _poursuite_d_arme(profil) or 1.0
                score *= pa
            passantes.append((score, c))
        if not passantes:
            # Rien ne passe à cette taille : on prend le meilleur DPS brut,
            # le rendu dira que la déflexion le bloque.
            passantes = [((c.get("dps") or 0), c) for c in candidats]
        if not passantes:
            continue
        meilleure = max(passantes, key=lambda x: x[0])[1]
        n = sum(a["n"] for a in affuts if a["max_size"] == taille_row)
        armes.append(dict(meilleure, n=n, poste="pilote"))
    nom = " + ".join(f"{a['n']}× {a['name']}" for a in armes)
    return armes, nom


def _meilleur_bouclier(con: sqlite3.Connection, ship_uuid: str,
                       criteres: dict[str, Any]) -> str | None:
    """Le bouclier au plus de PV que ce vaisseau peut monter (sprint 21)."""
    tailles = [r[0] for r in con.execute(
        "SELECT DISTINCT max_size FROM hardpoints WHERE ship_uuid = ? "
        "AND category = 'shield_generator' AND max_size IS NOT NULL",
        (ship_uuid,))]
    if not tailles:
        return None
    filtre = ""
    params: list[Any] = [max(tailles)]
    if criteres.get("grade_lettre"):
        filtre = " AND i.grade_lettre = ?"
        params.append(criteres["grade_lettre"])
    ligne = _row(con,
                 "SELECT i.name FROM item_stats s "
                 "JOIN items i ON i.uuid = s.item_uuid "
                 "WHERE i.type = 'Shield' AND i.size <= ? "
                 "AND s.shield_health IS NOT NULL" + filtre +
                 " ORDER BY s.shield_health DESC LIMIT 1", *params)
    return ligne["name"] if ligne else None


def peut_detruire(con: sqlite3.Connection, query: str, *,
                  cible: str | None = None,
                  arme: str | None = None,
                  arme_seule: bool = False,
                  arme_tourelles: bool = False,
                  bouclier: str | None = None,
                  loadout: str | None = None,
                  loadout_criteres: dict[str, Any] | None = None,
                  qualite: float | None = None,
                  qualite_bouclier: float | None = None) -> dict[str, Any]:
    """« Est-ce qu'un Scorpius peut détruire un Hammerhead ? »

    `arme` remplace l'armement stock de l'attaquant (« équipé de Deadbolt
    III »). Avec `arme_seule`, elle sélectionne exclusivement l'arme déjà
    montée que le joueur nomme (« uniquement son S10 ») et écarte aussi les
    missiles. Avec `arme_tourelles`, elle ne remplace que les armes des
    tourelles (« Revenant en tourelles »). `bouclier` remplace celui de la
    cible, `qualite` module l'arme et `qualite_bouclier` les générateurs de
    la cible, y compris le stock quand aucun modèle de remplacement n'est
    nommé.
    """
    att_res = resolve(con, query, entity_types=("ship",))
    if att_res.best is None or cible is None:
        raise NotFound(query, att_res)
    cible_res = resolve(con, cible, entity_types=("ship",))
    if cible_res.best is None:
        raise NotFound(cible, cible_res)

    attaquant = _dict(_row(con, "SELECT uuid, name FROM ships WHERE uuid = ?",
                           att_res.best.entity_id))
    defenseur = _dict(_row(con, "SELECT uuid, name FROM ships WHERE uuid = ?",
                           cible_res.best.entity_id))
    if attaquant is None or defenseur is None:
        raise NotFound(query, att_res)

    defense = _defense(con, defenseur["uuid"])
    if defense is None or not any(defense.get(k) for k in
                                  ("hull_health", "armor_health", "shield_hp")):
        # 9 vaisseaux sur 316 n'ont pas de bloc de combat : le dire vaut
        # mieux qu'inventer un duel.
        return _qualifier_combat(con, {
            "attaquant": attaquant, "defenseur": defenseur,
            "sans_defense_connue": True, "resolution": att_res})

    armes = _armes_stock(con, attaquant["uuid"])
    arme_nommee, mult_qualite = None, None
    loadout_nomme = None
    # **Le meilleur loadout, résolu pour ce duel** (sprint 21). On construit
    # l'armement de l'attaquant depuis les affûts, en visant ce qui passe la
    # déflexion de la cible ; le bouclier au plus de PV suit. Le loadout
    # retenu se nomme, sans quoi il serait invérifiable.
    if loadout == "meilleur" and not arme:
        criteres = loadout_criteres or {}
        cible_profil = _profil_agilite_v2(
            _charger_profil_vol(con, defenseur["uuid"])) \
            if criteres.get("cible_rapide") else None
        meilleures, nom = _meilleur_armement(
            con, attaquant["uuid"], defense, cible_profil, criteres)
        if meilleures:
            armes = meilleures
            loadout_nomme = nom
        if not bouclier:
            meilleur_bou = _meilleur_bouclier(con, attaquant["uuid"], criteres)
            # Le bouclier optimal est celui de l'attaquant : il n'entre pas
            # dans ce duel offensif, mais on le nomme pour la transparence.
            if meilleur_bou:
                loadout_nomme = (loadout_nomme or "") + \
                    f", bouclier {meilleur_bou}"
    if arme:
        if arme_seule:
            installees = _armes_installees(con, attaquant["uuid"], arme)
            if installees:
                armes = installees
                arme_nommee = " + ".join(a["name"] for a in armes)
                if qualite is not None and len(armes) == 1:
                    lu = _mult_qualite(con, armes[0]["item_uuid"], qualite)
                    if lu is not None:
                        mult_qualite = lu[0]
                        for champ in ("alpha", "dps", "dps_soutenu"):
                            if armes[0].get(champ):
                                armes[0][champ] *= mult_qualite
            else:
                armes, arme_nommee, mult_qualite = _remplacer_armes(
                    con, armes, arme, qualite)
                if arme_nommee:
                    # Une hypothèse « uniquement X » ne garde aucun autre
                    # groupe d'armes compatible ou stock.
                    armes = [a for a in armes if a.get("name") == arme_nommee]
        elif arme_tourelles:
            fixes = [a for a in armes if a.get("poste") == "pilote"]
            en_tourelles = [a for a in armes
                            if a.get("poste") in ("habitee", "telecommandee")]
            remplacees, arme_nommee, mult_qualite = _remplacer_armes(
                con, en_tourelles, arme, qualite)
            armes = fixes + remplacees
        else:
            armes, arme_nommee, mult_qualite = _remplacer_armes(
                con, armes, arme, qualite)
        if arme_nommee is None:
            return _qualifier_combat(con, {
                "attaquant": attaquant, "defenseur": defenseur,
                "defense": defense, "armes": [], "duel": None,
                "arme_nommee": None, "arme_refusee": arme,
                "armes_non_chiffrees": [], "qualite": qualite,
                "mult_qualite": None, "conseils": {},
                "bouclier_nomme": None, "missiles": None, "bilan": None,
                "arme_seule": arme_seule,
                "arme_tourelles": arme_tourelles,
                "resolution": att_res,
            })
    if bouclier:
        remplacee = _remplacer_bouclier(
            con, defense, bouclier, qualite_bouclier)
        if remplacee is None:
            return _qualifier_combat(con, {
                "attaquant": attaquant, "defenseur": defenseur,
                "defense": defense, "armes": [], "duel": None,
                "arme_nommee": arme_nommee, "arme_refusee": None,
                "bouclier_refuse": bouclier,
                "armes_non_chiffrees": [], "qualite": qualite,
                "mult_qualite": mult_qualite, "conseils": {},
                "bouclier_nomme": None, "missiles": None, "bilan": None,
                "arme_seule": arme_seule,
                "arme_tourelles": arme_tourelles,
                "qualite_bouclier": qualite_bouclier,
                "resolution": att_res,
            })
        defense = remplacee
    elif qualite_bouclier is not None:
        defense = _qualifier_boucliers_stock(
            con, defenseur["uuid"], defense, qualite_bouclier)

    profils, armes_non_chiffrees = [], []
    for arme_lue in armes:
        profil = _profil(arme_lue)
        if profil is None:
            armes_non_chiffrees.append(arme_lue)
        else:
            profils.append(profil)
    resultat = _verrous(profils, defense) if profils else None
    desarmement = (_desarmement(con, defenseur["uuid"], profils)
                   if profils else {"groupes": [], "connus": 0,
                                    "sans_pv": 0})

    # Le verdict est non : dire ce qui permettrait de passer — les armes
    # au-dessus des seuils qui rentrent sur les affûts, et à défaut la
    # qualité de fabrication qui y suffirait.
    conseils = {}
    if (not arme_seule and resultat is not None
            and not resultat["verdict"]):
        taille_max = con.execute(
            "SELECT MAX(max_size) FROM hardpoints "
            "WHERE ship_uuid = ? AND category = 'weapon_mount'",
            (attaquant["uuid"],)).fetchone()[0]
        conseils = _conseils(con, defense, taille_max)

    # La théorie des missiles : leur alpha passe tout (interruption comme
    # déflexion), la seule question est la somme — et le fait de toucher.
    missiles = None
    combat_att = _dict(_row(con, "SELECT missiles_count, missile_damage "
                                 "FROM ship_combat WHERE ship_uuid = ?",
                            attaquant["uuid"])) or {}
    requis = ((defense.get("shield_hp") or 0)
              + (defense.get("armor_health") or 0)
              + (defense.get("hull_health") or 0))
    if (not arme_seule and combat_att.get("missiles_count")
            and combat_att.get("missile_damage")):
        total = combat_att["missiles_count"] * combat_att["missile_damage"]
        missiles = {"n": combat_att["missiles_count"],
                    "unitaire": combat_att["missile_damage"],
                    "total": total, "requis": requis,
                    "suffisent": total >= requis}

    # **« Ce n'est pas soit les armes, soit les missiles : ça
    # s'additionne. »** (journal du 2026-08-08). Le bilan global cumule ce
    # que les canons livrent réellement (rien s'ils ricochent, leur
    # réserve s'ils passent, sans fin pour l'énergétique passante) et la
    # totalité des missiles — qui, eux, passent tout, à condition d'être
    # placés.
    bilan = None
    if resultat is not None:
        if resultat["verdict"]:
            bilan = {"suffit": True, "par": "armes"}
        else:
            # Ce que les armes livrent VRAIMENT quand le verdict aux armes
            # est non. Relecture du 2026-08-08 : une énergétique passant la
            # déflexion était comptée « sans fin » — victoire annoncée
            # alors que le bouclier, jamais interrompu, absorbe 100 % de
            # ses tirs. Bouclier debout : l'énergie livre zéro, le
            # balistique livre ses 55 % qui traversent. Bouclier tombé
            # avec une énergétique passante, le verdict aux armes aurait
            # déjà été oui — ce chemin est donc balistique à sec.
            b_tient = not resultat["bouclier"]["tombe"]
            armes_livrent = 0.0
            for p in resultat["passantes"]:
                if p.get("budget") is None:
                    continue
                part = ((1 - ABSORPTION_PHYSIQUE)
                        if b_tient and p["type"] == "physical" else
                        0.0 if b_tient else 1.0)
                armes_livrent += p["budget"] * part
            livrable = (missiles["total"] if missiles else 0) + armes_livrent
            if livrable >= requis:
                bilan = {"suffit": True, "par": "missiles",
                         "livrable": livrable, "requis": requis,
                         "armes_livrent": armes_livrent}
            else:
                bilan = {"suffit": False, "livrable": livrable,
                         "requis": requis, "deficit": requis - livrable,
                         "armes_livrent": armes_livrent}

    # Le feu par poste (stock) et l'agilité v2 accompagnent chaque duel :
    # le journal du 2026-08-12 a montré un rendu qui brodait sur le seul
    # feu pilote de deux biplaces, et une agilité jamais montrée alors que
    # le modèle existait dans `bataille()`.
    feu_attaquant = _feu_par_poste(con, attaquant["uuid"])
    feu_defenseur = _feu_par_poste(con, defenseur["uuid"])
    agilite = {
        "modele": 2,
        "attaquant": _profil_agilite_v2(
            _charger_profil_vol(con, attaquant["uuid"])),
        "defenseur": _profil_agilite_v2(
            _charger_profil_vol(con, defenseur["uuid"])),
    }

    return _qualifier_combat(con, {
            "attaquant": attaquant, "defenseur": defenseur,
            "defense": defense, "armes": profils, "duel": resultat,
            "arme_nommee": arme_nommee, "qualite": qualite,
            "feu_attaquant": feu_attaquant, "feu_defenseur": feu_defenseur,
            "agilite": agilite,
            "arme_refusee": None,
            "armes_non_chiffrees": armes_non_chiffrees,
            "mult_qualite": mult_qualite, "conseils": conseils,
            "bouclier_nomme": defense.get("bouclier_nomme"),
            "qualite_bouclier": defense.get("qualite_bouclier"),
            "mult_qualite_bouclier": defense.get("mult_qualite_bouclier"),
            "composants_qualite_bouclier":
                defense.get("composants_qualite_bouclier"),
            "borne_qualite_bouclier": defense.get("borne_qualite_bouclier"),
            "missiles": missiles, "bilan": bilan,
            "arme_seule": arme_seule,
            "loadout_nomme": loadout_nomme,
            "arme_tourelles": arme_tourelles,
            "armes_en_tourelles": sum(
                p.get("n") or 1 for p in profils
                if p.get("poste") in ("habitee", "telecommandee")),
            "composants_exposes": desarmement,
            "bouclier_refuse": None,
            "resolution": att_res})


# ---------------------------------------------------------- les matchups

def _temps_de_destruction(resultat: dict[str, Any]) -> float | None:
    """Temps de feu du duel, bouclier puis armure/coque, sans missiles."""
    duel = resultat.get("duel") or {}
    if not duel.get("verdict"):
        return None
    bouclier = duel.get("bouclier") or {}
    coque = duel.get("coque") or {}
    return float((bouclier.get("temps") or 0) + (coque.get("temps") or 0))


def _profil_matchup(con: sqlite3.Connection,
                    ship_uuid: str) -> dict[str, Any]:
    """Les seules colonnes utiles pour expliquer un avantage direct."""
    profil = _dict(_row(
        con,
        "SELECT s.uuid,s.name,s.role,s.size,s.crew,s.scm_speed,"
        "s.boost_speed,s.pitch,s.yaw,s.roll,s.pilot_dps,c.shield_hp,"
        "c.armor_health,c.hull_health FROM ships s LEFT JOIN ship_combat c "
        "ON c.ship_uuid=s.uuid WHERE s.uuid=?", ship_uuid,
    )) or {"uuid": ship_uuid, "name": "Vaisseau inconnu"}
    # Le feu par poste remplace le seul `pilot_dps` dans ce qu'on montre :
    # sur un biplace, ce champ ampute la moitié de l'armement (journal du
    # 2026-08-12, Scorpius/Hurricane).
    if profil.get("name") != "Vaisseau inconnu":
        profil["feu"] = _feu_par_poste(con, ship_uuid)
    return profil


def _analyser_matchup(con: sqlite3.Connection, attaquant: str,
                      cible: str) -> dict[str, Any]:
    """Même duel dans les deux sens ; aucun sens ne vaut l'autre par défaut.

    **Les temps sont pondérés par la mobilité depuis le 2026-08-12** —
    révision par l'utilisateur de la décision du sprint 17 : « arrête de
    tout calculer sur les DPS, surtout entre chasseurs ». `_facteur_tir`
    (poursuite/suivi/évasion/silhouette, ×0,55 à ×1,45) module chaque
    sens, exactement comme la bataille le fait depuis toujours. C'est un
    poids maison : le rendu l'annonce, et les temps mécaniques restent
    dans les données pour que le périmètre soit vérifiable. Les verrous
    (déflexion, budget) restent des verrous — la mobilité module un
    temps, elle ne fait pas passer un tir qui ricoche.
    """
    aller = peut_detruire(con, attaquant, cible=cible)
    retour = peut_detruire(con, cible, cible=attaquant)
    t_aller_meca = _temps_de_destruction(aller)
    t_retour_meca = _temps_de_destruction(retour)
    defenseur = _profil_matchup(con, aller["defenseur"]["uuid"])

    agilite = aller.get("agilite") or {}
    profil_att = agilite.get("attaquant")
    profil_def = agilite.get("defenseur")
    if profil_att and profil_def:
        f_aller = _facteur_tir(profil_att, profil_def)
        f_retour = _facteur_tir(profil_def, profil_att)
        # Sprint 21 : l'arme aussi doit toucher — la poursuite d'arme
        # (cadence, vitesse de projectile) s'oppose à l'évasion de la
        # cible et se multiplie au facteur de mobilité.
        f_aller *= _facteur_poursuite_armes(aller.get("armes") or [],
                                            profil_def)
        f_retour *= _facteur_poursuite_armes(retour.get("armes") or [],
                                             profil_att)
    else:
        f_aller = f_retour = 1.0
    t_aller = t_aller_meca / f_aller if t_aller_meca is not None else None
    t_retour = t_retour_meca / f_retour if t_retour_meca is not None else None

    if t_aller is not None and t_retour is None:
        verdict = "favorable_mecanique"
        rapport = None
    elif t_aller is None and t_retour is not None:
        verdict = "defavorable_mecanique"
        rapport = None
    elif t_aller is None:
        verdict = "indetermine"
        rapport = None
    else:
        rapport = t_retour / t_aller
        if rapport >= 1.15:
            verdict = "favorable"
        elif rapport <= (1 / 1.15):
            verdict = "defavorable"
        else:
            verdict = "serre"

    return {
        "cible": defenseur,
        "temps_source": t_aller, "temps_cible": t_retour,
        # Les temps mécaniques (avant pondération par la mobilité) restent
        # dans les données : le périmètre du poids maison doit se vérifier.
        "temps_source_meca": t_aller_meca, "temps_cible_meca": t_retour_meca,
        "facteur_aller": round(f_aller, 3), "facteur_retour": round(f_retour, 3),
        "rapport_temps": rapport, "verdict": verdict,
        "agilite": aller.get("agilite"),
        "source_peut_detruire": t_aller is not None,
        "cible_peut_detruire": t_retour is not None,
        "armes_source": len(aller.get("armes") or []),
        "armes_cible": len(retour.get("armes") or []),
        "tourelles_source": aller.get("armes_en_tourelles") or 0,
        "tourelles_cible": retour.get("armes_en_tourelles") or 0,
    }


def matchups_vaisseau(con: sqlite3.Connection, query: str, *,
                      cible: str | None = None, mode: str = "matchups",
                      limit: int = 8) -> dict[str, Any]:
    """Matchups stock, calculés dans les deux sens sur la flotte de combat.

    Le temps de tir est une capacité mécanique, pas une prédiction PvP : le
    jeu ne publie ni le talent, ni la géométrie des arcs, ni le taux de touche.
    Les missiles sont écartés car leur interception n'est pas chiffrée.
    """
    resolution = resolve(con, query, entity_types=("ship",))
    if resolution.best is None:
        raise NotFound(query, resolution)
    source_ligne = _dict(_row(
        con, "SELECT uuid,name FROM ships WHERE uuid=?",
        resolution.best.entity_id))
    if source_ligne is None:
        raise NotFound(query, resolution)
    source = _profil_matchup(con, source_ligne["uuid"])
    limite = min(max(int(limit), 1), 12)

    if cible:
        cible_res = resolve(con, cible, entity_types=("ship",))
        if cible_res.best is None:
            raise NotFound(cible, cible_res)
        cible_nom = con.execute(
            "SELECT name FROM ships WHERE uuid=?",
            (cible_res.best.entity_id,),
        ).fetchone()[0]
        analyse = _analyser_matchup(con, source["name"], cible_nom)
        manques = []
        if analyse["temps_source"] is None:
            manques.append(
                f"destruction non démontrée pour {source['name']}")
        if analyse["temps_cible"] is None:
            manques.append(
                f"destruction non démontrée pour {analyse['cible']['name']}")
        qualite = qualite_reponse(
            faits={
                "source": source["name"],
                "cible": analyse["cible"]["name"],
                "sens_calcules": 2,
            },
            manques=manques, sources=["jeu"],
            fraicheur={"jeu": fraicheur_jeu(con)},
        )
        return {
            "vaisseau": source, "cible": analyse["cible"],
            "direct": True, "mode": "comparaison", "matchups": [analyse],
            "analyses_total": 1, "favorables_total": int(
                analyse["verdict"].startswith("favorable")),
            "destructibles_total": int(analyse["source_peut_detruire"]),
            "non_calculables": int(
                analyse["temps_source"] is None
                or analyse["temps_cible"] is None),
            "qualite_reponse": qualite, "complet": qualite["complet"],
            "resolution": resolution,
        }

    # Le classement général ne compare pas le Wolf à un Hull C désarmé :
    # uniquement les vaisseaux publiés comme Combat, avec défense et armement
    # offensif chiffrables. Mesuré sur le build 12232306 : 108 adversaires
    # distincts pour le Wolf.
    candidats = [r[0] for r in con.execute(
        "SELECT DISTINCT s.name FROM ships s "
        "WHERE s.is_spaceship=1 AND s.career='Combat' "
        "AND s.name IS NOT NULL AND s.name<>? COLLATE NOCASE "
        "AND EXISTS (SELECT 1 FROM ship_combat c WHERE c.ship_uuid=s.uuid) "
        "AND EXISTS (SELECT 1 FROM ship_armes a WHERE a.ship_uuid=s.uuid "
        "            AND a.poste<>'pdc') "
        "ORDER BY s.name COLLATE NOCASE", (source["name"],))]
    analyses = []
    non_calculables = 0
    for cible_nom in candidats:
        try:
            analyse = _analyser_matchup(con, source["name"], cible_nom)
        except NotFound:
            non_calculables += 1
            continue
        analyses.append(analyse)
        if (analyse["temps_source"] is None
                or analyse["temps_cible"] is None):
            non_calculables += 1

    destructibles = [a for a in analyses if a["source_peut_detruire"]]
    favorables = [
        a for a in analyses if a["verdict"].startswith("favorable")
    ]
    if mode == "destruction":
        selection = sorted(
            destructibles,
            key=lambda a: (-(a["cible"].get("size") or 0),
                           a["temps_source"] or float("inf"),
                           a["cible"]["name"]),
        )[:limite]
    else:
        selection = sorted(
            favorables,
            key=lambda a: (
                0 if a["temps_cible"] is None else 1,
                -(a["rapport_temps"] or 0),
                -(a["cible"].get("size") or 0),
                a["temps_source"] or float("inf"),
                a["cible"]["name"],
            ),
        )[:limite]
    manques = ([] if selection else
               ["aucun matchup classable avec les profils publiés"])
    qualite = qualite_reponse(
        faits={
            "source": source["name"], "candidats": len(candidats),
            "destructibles": len(destructibles),
            "favorables": len(favorables),
        },
        manques=manques, sources=["jeu"],
        fraicheur={"jeu": fraicheur_jeu(con)},
    )
    return {
        "vaisseau": source, "cible": None, "direct": False, "mode": mode,
        "matchups": selection, "analyses_total": len(analyses),
        "favorables_total": len(favorables),
        "destructibles_total": len(destructibles),
        "non_calculables": non_calculables,
        "qualite_reponse": qualite, "complet": qualite["complet"],
        "resolution": resolution,
    }
