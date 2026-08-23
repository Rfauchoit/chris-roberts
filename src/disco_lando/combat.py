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
# divergent pas (le contrôle est dans les tests unitaires du module). Elles
# vivent dans `engagement.py` depuis le 2026-08-13 : la simulation doit
# opposer exactement les mêmes verrous que le duel, et deux copies auraient
# fini par diverger. Réexportées ici, les appelants ne changent pas de porte.
from . import engagement  # noqa: E402
from .engagement import (  # noqa: E402  (constantes, pas du code exécuté)
    ABSORPTION_PHYSIQUE, RESISTANCE_PHYSIQUE, SEUIL_INTERRUPTION)


#: **Les postes qu'un équipage réel ne tient pas.** Règle de terrain
#: donnée par l'utilisateur le 2026-08-14, et volontairement **nominative**
#: — elle vise un vaisseau, pas une famille : le **Vanguard** vole à deux,
#: mais « il n'y a personne dans sa tourelle, ça serait contre-productif ».
#: Un joueur de plus y vaut moins qu'un chasseur de plus.
#:
#: Le rattachement passe par le **hardpoint racine** publié, pas par le nom
#: de l'arme : le Vanguard perd les 2 Sawbuck de `hardpoint_turret` et
#: garde son Deadbolt V S5 plus ses 4 MVSA S2 de poste pilote.
#:
#: **Le Paladin n'y figure pas, et c'est une correction.** Il y a d'abord
#: été inscrit — ses deux tourelles latérales retirées — avant que
#: l'utilisateur précise : « les tourelles sont slaved par le pilote pour
#: le Paladin, c'est juste qu'il ne peut tirer que vers l'avant du coup ».
#: Asservies, elles restent donc **toutes** servies, et leur seule limite
#: est l'arc — ce que le modèle traite déjà comme un tir fixe, sans
#: accorder d'avantage d'arc aux tourelles.
#:
#: **Ne pas généraliser.** Mesuré avant d'écrire : « tout vaisseau qui a
#: des armes pilote perd ses tourelles habitées » toucherait 19 appareils,
#: dont l'Idris et ses 28 hommes d'équipage, et désarmerait les 6 qui n'ont
#: que des tourelles (Polaris, Hammerhead, Perseus…).
EQUIPAGE_ABSENT: dict[str, tuple[str, ...]] = {
    "aegis vanguard": ("hardpoint_turret",),
}


def _racines_inoccupees(con: sqlite3.Connection,
                        ship_uuid: str) -> tuple[str, ...]:
    """Les hardpoints racines que personne ne sert, pour ce vaisseau."""
    nom = con.execute("SELECT name FROM ships WHERE uuid = ? LIMIT 1",
                      (ship_uuid,)).fetchone()
    if not nom or not nom[0]:
        return ()
    minuscule = nom[0].lower()
    return next((v for cle, v in EQUIPAGE_ABSENT.items()
                 if minuscule.startswith(cle)), ())


def _famille_de_coque(class_name: str | None) -> str:
    """La famille d'un `class_name` : constructeur et modèle, sans variante.

    `ANVL_Hornet_F7C_Mk2` et `ANVL_Hornet_F7A` partagent `ANVL_Hornet`, et
    le jeu monte bien la même tourelle de nez sur les deux — mesuré, la
    `ANVL_Hornet_F7A_Nose_Turret` est installée sur cinq appareils.
    """
    if not class_name:
        return ""
    morceaux = class_name.split("_")
    return "_".join(morceaux[:2]) if len(morceaux) >= 2 else class_name


def _avec_socles_vides(con: sqlite3.Connection, ship_uuid: str,
                       affuts: list[dict[str, Any]],
                       exclues: tuple[str, ...]) -> list[dict[str, Any]]:
    """Ajoute les affûts qu'apporterait une tourelle montée dans un socle vide.

    **Un socle vide n'est pas un socle absent**, et le modèle le comptait
    pour rien. Sept chasseurs sur la flotte publient un `weapon_mount` de
    profondeur 0 sans rien dedans, acceptant un `Turret` : le F7C Hornet
    Mk I, le Mk II, le Wildfire, les deux Ghost, le Stinger et le Scorpius
    Antares. Le meilleur armement leur comptait **deux** armes là où le
    F7A, qui a le même nez rempli, en aligne quatre — d'où « le Hornet Mk II
    n'a que 2 armes » du banc, sur un appareil qui monte une tourelle de nez
    à deux canons S3. Remarque de l'utilisateur, 2026-08-16, capture
    d'écran du configurateur à l'appui.

    **La tourelle se choisit dans la famille de la coque, jamais ailleurs.**
    Mesuré sur les 443 tourelles nommées réellement installées : 302
    partagent les deux premiers segments du `class_name` de leur vaisseau,
    et les 141 restantes sont des tourelles de **catalogue**
    (`Turret_PDC_BEHR_A`, `BEHR_PC2_Dual_S3`, `Mount_Gimbal_*`) — jamais la
    tourelle sur mesure d'un autre appareil. Sans cette règle, le nez d'un
    Hornet recevait la `RSI_Scorpius_SCItem_Remote_Turret` et ses quatre
    ports S3.
    """
    socles = [dict(r) for r in con.execute(
        "SELECT h.min_size, h.max_size FROM hardpoints h "
        "WHERE h.ship_uuid = ? AND h.depth = 0 "
        "  AND h.category = 'weapon_mount' AND h.installed_uuid IS NULL "
        "  AND h.accepted_types LIKE '%Turret%' "
        "  AND h.max_size IS NOT NULL "
        + ("AND COALESCE(h.hardpoint_name, '') NOT IN ("
           + ",".join("?" * len(exclues)) + ") " if exclues else ""),
        (ship_uuid, *exclues))]
    if not socles:
        return affuts
    coque = con.execute("SELECT class_name FROM ships WHERE uuid = ? LIMIT 1",
                        (ship_uuid,)).fetchone()
    famille = _famille_de_coque(coque[0] if coque else None)
    if not famille:
        return affuts

    par_taille = {a["max_size"]: a["n"] for a in affuts}
    for socle in socles:
        candidates = con.execute(
            "SELECT p.item_uuid, COUNT(*) AS n, MAX(p.max_size) AS taille "
            "FROM item_ports p JOIN items i ON i.uuid = p.item_uuid "
            # Le tiret bas est un joker dans LIKE, et la famille en porte
            # un : sans l'échappement, `ANVL_Hornet` matcherait
            # `ANVLxHornet`. Sans conséquence ici, mais la requête doit
            # dire ce qu'elle cherche.
            "WHERE i.type = 'Turret' AND i.class_name LIKE ? ESCAPE '\\' "
            "  AND i.size BETWEEN ? AND ? "
            "  AND p.accepted LIKE '%WeaponGun%' AND p.max_size IS NOT NULL "
            "GROUP BY p.item_uuid ORDER BY COUNT(*) * MAX(p.max_size) DESC "
            "LIMIT 1",
            (famille.replace("_", "\\_") + "\\_%",
             socle["min_size"] or socle["max_size"],
             socle["max_size"])).fetchone()
        if not candidates:
            continue
        _, combien, taille = candidates
        par_taille[taille] = par_taille.get(taille, 0) + combien
    return [{"max_size": t, "n": n} for t, n in sorted(par_taille.items())]


#: Pool de distorsion d'un composant critique de chasseur, servi quand le
#: générateur monté n'est pas chiffré. **Mesuré** : les générateurs de
#: bouclier taille 1 tiennent 1 650 à 2 500, les centrales 1 950, les
#: refroidisseurs 900 à 1 900. On prend la médiane des boucliers de chasseur,
#: c'est le composant dont l'extinction décide du duel.
POOL_DISTORSION_MEDIAN = 1900.0

#: Délai avant que la distorsion accumulée ne commence à retomber, et
#: vitesse à laquelle elle retombe. `DecayRate` vaut à peu près
#: `Maximum / 15` sur tout le catalogue.
DELAI_DISTORSION_MEDIAN = 1.5


def _emp_porte(con: sqlite3.Connection, ship_uuid: str) -> dict[str, Any] | None:
    """L'EMP monté sur ce vaisseau, avec ses paramètres publiés.

    Sept vaisseaux en portent un : Hawk, Sabre Raven, Scorpius Antares,
    Avenger Warlock, Vanguard Sentinel, Mantis et Cyclone AA. Le porteur se
    lit dans `hardpoints.installed_type = 'EMP'`, jamais dans un nom.
    """
    ligne = _row(
        con,
        "SELECT e.charge_time, e.distortion_damage, e.emp_radius, "
        "       e.emp_radius_min, e.unleash_time, e.cooldown_time, "
        "       h.installed_name AS nom "
        "FROM hardpoints h "
        "JOIN emp_devices e ON e.item_uuid = h.installed_uuid "
        "WHERE h.ship_uuid = ? AND h.installed_type = 'EMP' "
        "ORDER BY e.distortion_damage DESC LIMIT 1",
        ship_uuid)
    return _dict(ligne) if ligne else None


def _pool_de_distorsion(con: sqlite3.Connection,
                        ship_uuid: str) -> dict[str, float]:
    """Ce qu'il faut délivrer pour éteindre les systèmes de ce vaisseau.

    C'est le **générateur de bouclier** qu'on regarde : c'est lui dont
    l'extinction décide d'un duel, puisqu'elle supprime la régénération. Les
    armes ne sont pas concernées — leur pool vaut 500 000, elles ne
    s'éteignent jamais.
    """
    ligne = _row(
        con,
        "SELECT d.maximum, d.decay_rate, d.decay_delay "
        "FROM hardpoints h "
        "JOIN distortion_params d ON d.item_uuid = h.installed_uuid "
        "WHERE h.ship_uuid = ? AND h.installed_type = 'Shield' "
        "ORDER BY d.maximum DESC LIMIT 1",
        ship_uuid)
    trouve = _dict(ligne) if ligne else {}
    maximum = trouve.get("maximum") or POOL_DISTORSION_MEDIAN
    return {
        "maximum": maximum,
        "decay_rate": trouve.get("decay_rate") or maximum / 15.0,
        "decay_delay": trouve.get("decay_delay") or DELAI_DISTORSION_MEDIAN,
    }


def _postes_inoccupes(con: sqlite3.Connection, ship_uuid: str) -> set[str]:
    """Les noms d'armes que personne ne sert sur ce vaisseau, en duel solo."""
    racines = _racines_inoccupees(con, ship_uuid)
    if not racines:
        return set()
    marques = ",".join("?" * len(racines))
    return {r[0] for r in con.execute(
        "SELECT DISTINCT h.installed_name FROM hardpoints h "
        "JOIN hardpoints r ON r.ship_uuid = h.ship_uuid "
        "                 AND r.port_id = h.root_port_id "
        f"WHERE h.ship_uuid = ? AND r.hardpoint_name IN ({marques}) "
        "  AND h.installed_name IS NOT NULL",
        (ship_uuid, *racines))}


def _armes_stock(con: sqlite3.Connection, ship_uuid: str) -> list[dict[str, Any]]:
    """L'armement offensif stock, pilote et tourelles, avec ses statistiques.

    Les PDC sont publiées dans la même famille, mais leur rôle anti-missile ne
    permet pas de les compter honnêtement comme feu anti-vaisseau. Et les
    postes que personne ne tient — voir `EQUIPAGE_ABSENT` — sont écartés :
    compter une tourelle vide gonfle le feu d'un vaisseau qu'on affronte
    seul.
    """
    inoccupes = _postes_inoccupes(con, ship_uuid)
    if inoccupes:
        return [dict(r) for r in con.execute(
            "SELECT a.name, a.n, a.poste, s.alpha, s.pellets_per_shot, s.dps, "
            "       s.dps_soutenu, s.ammo_capacity, s.weapon_class, "
            "       s.fire_modes, s.dps_physical, s.dps_energy, i.size, "
            "       s.projectile_speed, s.rounds_per_minute, s.effective_range "
            "FROM ship_armes a "
            "LEFT JOIN item_stats s ON s.item_uuid = a.weapon_uuid "
            "LEFT JOIN items i ON i.uuid = a.weapon_uuid "
            "WHERE a.ship_uuid = ? AND a.poste <> 'pdc'", (ship_uuid,))
            if r["name"] not in inoccupes]
    return [dict(r) for r in con.execute(
        "SELECT a.name, a.n, a.poste, s.alpha, s.pellets_per_shot, s.dps, "
        "       s.dps_soutenu, s.ammo_capacity, s.weapon_class, s.fire_modes, "
        "       s.dps_physical, s.dps_energy, i.size, "
        "       s.projectile_speed, s.rounds_per_minute, s.effective_range "
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
        # Publiées sur 142 armes sur 142, et jamais lues avant le
        # 2026-08-14 : la portée efficace et le nombre de plombs disent
        # à quelle distance une arme travaille encore.
        "effective_range": arme.get("effective_range"),
        "plombs": plombs,
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
    # L'EMP que ce vaisseau porte, et le pool que ses propres systèmes
    # opposent à celui d'en face. Les deux voyagent avec la défense parce
    # que c'est elle que la simulation reçoit ; l'absence de table (base
    # d'avant l'EMP) laisse simplement les deux à None.
    try:
        ligne["emp"] = _emp_porte(con, ship_uuid)
        ligne["pool_distorsion"] = _pool_de_distorsion(con, ship_uuid)
    except sqlite3.OperationalError:
        ligne["emp"] = None
        ligne["pool_distorsion"] = None
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
        "  AND i.mount_usable = 1 AND i.flight_ready = 1 "
        # Conseiller une arme de char à un chasseur n'aide personne — et une
        # arme que le jeu ne **nomme** pas n'est pas dans le catalogue du
        # joueur : cinq armes sur 140 sont dans ce cas, dont trois canons
        # Vanduul, et le meilleur loadout en montait sur un Hawk.
        "  AND i.porteur_exclusif IS NULL AND i.name IS NOT NULL "
        "  AND i.size <= ?",
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

    # --- verrou 3 : l'armure **puis** la coque, deux couches distinctes
    #
    # Le jeu publie deux multiplicateurs par armure, et ils ne s'appliquent
    # pas à la même étape (mesuré le 2026-08-14 sur l'objet d'armure lui-même,
    # qui porte `Durability.Health` et `Durability.Resistance.Multiplier` à
    # côté de `Armor.DamageMultipliers`) :
    #
    # - `ResistanceMultipliers` — ce que **l'armure encaisse**, sur ses
    #   propres PV. 0,81 en physique, **1,15 en énergie** : au-dessus de 1,
    #   l'armure souffre plus des lasers ;
    # - `DamageMultipliers` — ce qui la **traverse** vers la coque, 0,75 et
    #   0,60. L'inverse : le laser passe mal.
    #
    # Le modèle additionnait les deux pools et n'appliquait aucun des deux.
    # Un laser use donc vite l'armure et fait peu à la coque ; un balistique
    # fait l'inverse — et c'est exactement l'arbitrage que la meta décrit.
    armure_pv = defense.get("armor_health") or 0
    coque_pv = defense.get("hull_health") or 0
    a_livrer = armure_pv + coque_pv

    def _dps_sur(couche: str) -> float:
        """Le DPS utile sur l'armure (`res_`) ou sur la coque (`mult_`)."""
        prefixe = "res_" if couche == "armure" else "mult_"
        total = 0.0
        for p in passantes:
            # Bouclier levé, seul le balistique qui traverse travaille.
            if not bouclier["tombe"]:
                if p["type"] != "physical":
                    continue
                part = 1 - ABSORPTION_PHYSIQUE
            else:
                part = 1.0
            facteur = defense.get(f"{prefixe}{p['type']}")
            total += p["dps"] * part * (1.0 if facteur is None else facteur)
        return total

    dps_armure = _dps_sur("armure")
    dps_coque = _dps_sur("coque")
    # Le temps total est celui des deux couches enchaînées ; `dps_utile`
    # reste la vitesse moyenne, pour que les appelants ne changent pas de
    # porte et que le rendu garde un chiffre lisible.
    t_armure = armure_pv / dps_armure if dps_armure > 0 else None
    t_coque = coque_pv / dps_coque if dps_coque > 0 else None
    if t_armure is None or t_coque is None:
        dps_utile = 0.0
    else:
        total = t_armure + t_coque
        dps_utile = a_livrer / total if total > 0 else 0.0
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

#: Les colonnes de vol qu'un duel a besoin de lire — rien de plus qu'une
#: liste de champs, que `_charger_profil_vol` filtre sur ce que la base
#: contient réellement. Le modèle qui les interprète vit dans
#: `engagement.profil_de_vol`.
_CHAMPS_DE_VOL = (
    # **`max_speed` est la vitesse du mode NAV**, celle qui décide d'une
    # fuite — armes et boucliers coupés. Elle manquait à cette liste, si
    # bien que `profil_de_vol` retombait sur `boost_speed` : le mode NAV
    # existait dans le code et **ne changeait rien**, un fuyard décrochant
    # à 480 m/s au lieu de 1 125. Trouvé en relisant des fiches où « NAV »
    # affichait exactement la même valeur que « boost » sur les seize
    # duels d'affilée.
    "max_speed",
    # Le couple correcteur réellement appliqué : c'est lui qui distingue un
    # vaisseau qui s'arrête où il visait d'un vaisseau qui dépasse.
    "torque_imbalance",
    "scm_speed", "boost_speed", "boost_backward", "pitch", "yaw", "roll",
    "pitch_boosted", "yaw_boosted", "roll_boosted", "accel_main",
    "accel_retro", "accel_maneuver", "accel_main_boosted",
    "accel_retro_boosted", "accel_maneuver_boosted", "zero_to_scm",
    "scm_to_zero", "boost_capacity", "boost_regen", "boost_regen_time",
    "length", "width", "height", "mass", "size",
    # La section présentée, ingérée le 2026-08-13. `_charger_profil_vol`
    # filtre sur les colonnes réellement présentes : une base d'avant
    # l'ajout se lit encore, et le modèle retombe sur la boîte englobante
    # en le disant.
    # **`cross_y` est la face avant**, et c'est elle qui est présentée en
    # combat. Les trois axes sont nommés par SPViewer, qui publie pour
    # l'Arrow « 1 910 front × 5 647 side × 7 473 top » — exactement nos
    # `cross_y`, `cross_x`, `cross_z`. Elle manquait à cette liste.
    "cross_x", "cross_y", "cross_z",
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


# **L'ancien modele d'agilite v2 a ete retire le 2026-08-14.** Quatre
# scores normalises sans unite — poursuite, suivi, evasion, silhouette —
# dont l'evasion se lisait sur `accel_maneuver`, un total de poussee
# installee : l'Aurora Mk II en sortait « plus evasive » qu'un Arrow
# parce qu'elle a deux tuyeres de plus. Le modele d'engagement le
# remplace entierement (`engagement.profil_de_vol`), en grandeurs
# physiques verifiables.
#
# `_facteur_tir` et `_facteur_poursuite_armes` sont partis avec lui :
# ils bornaient l'effet de l'agilite a x0,55-1,45 sur un temps final,
# donc incapables de renverser un rapport de PV. Le taux de touche
# multiplie desormais le DPS **avant** les verrous.
#
# Deux modeles d'agilite qui cohabitent, c'est l'incident fondateur :
# le rendu affichait encore les scores v2 quand le verdict venait deja
# du nouveau. On ne garde donc pas l'ancien « au cas ou ».


def _charger_profil_vol(con: sqlite3.Connection,
                        ship_uuid: str) -> dict[str, Any]:
    """Lit aussi une base pré-v2 : les colonnes absentes deviennent NULL."""
    presentes = {r[1] for r in con.execute("PRAGMA table_xinfo(ships)")}
    champs = [c for c in ("pilot_dps",) + _CHAMPS_DE_VOL
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
    # La riposte suit la même source que l'attaquant : armes fixes, tourelles
    # habitées et télécommandées, hors PDC. Elle est lue **avant** les
    # facteurs, qui en dépendent.
    profils_cible = [p for arme in _armes_stock(con, duel["defenseur"]["uuid"])
                     if (p := _profil(arme)) is not None]
    defense_att = _dict(_row(con, "SELECT shield_hp, shield_regen, "
                                  "armor_health, hull_health, defl_physical, "
                                  "defl_energy FROM ship_combat "
                                  "WHERE ship_uuid = ?",
                             duel["attaquant"]["uuid"])) or {}
    # Le jeu de guerre passe au modèle d'engagement comme le reste du combat
    # (2026-08-13) : les facteurs ne sont plus des scores normalisés mais des
    # **taux de touche** tirés du temps de vol, de l'esquive et de la taille.
    camp_att = engagement.camp(
        duel["attaquant"]["name"], groupe, att_row, defense_att, defense)
    camp_cible = engagement.camp(
        duel["defenseur"]["name"], profils_cible, cible_row,
        defense, defense_att)
    # Une cible à l'arrêt n'esquive plus rien : son accélération d'esquive
    # tombe, sa rotation aussi. On touche au **profil physique**, ce qui se
    # lit — l'ancien modèle écrasait deux scores sans unité.
    if "a_l_arret" in mods_cible:
        camp_cible["profil"]["acceleration_evasion"] = 0.0
        camp_cible["profil"]["scm"] = 0.0
    if "a_l_arret" in mods_att:
        camp_att["profil"]["acceleration_evasion"] = 0.0
        camp_att["profil"]["scm"] = 0.0
    pv_att = camp_att["bouclier_max"] + camp_att["coque"]
    pv_cible = camp_cible["bouclier_max"] + camp_cible["coque"]
    distance = engagement.distance_d_equilibre(
        camp_att, camp_cible, pv_att, pv_cible)
    # Les deux sens ne sont pas réciproques : une petite silhouette et une
    # bonne rotation ne se compensent pas à l'identique.
    facteur = engagement.taux_moyen(camp_att, camp_cible, distance)
    facteur_retour = engagement.taux_moyen(camp_cible, camp_att, distance)

    # Le temps pour tomber la cible, mobilité comprise.
    t_cible = None
    if verrous["verdict"]:
        a_livrer = verrous["coque"]["a_livrer"] + (
            verrous["bouclier"]["hp"] if verrous["bouclier"]["tombe"] else 0)
        dps = verrous["coque"]["dps_utile"] * facteur
        t_cible = a_livrer / dps if dps > 0 else None

    # Le `pilot_dps` reste un repli pour les rares profils non chiffrés.
    # Avant la 4.9, ne lire que ce champ amputait notamment les canonnières
    # de leurs postes d'équipage.
    riposte = (sum(p["dps"] for p in profils_cible)
               if profils_cible else cible_row.get("pilot_dps") or 0)
    riposte_tourelles = sum(
        p.get("n") or 1 for p in profils_cible
        if p.get("poste") in ("habitee", "telecommandee"))
    if "moitie_armes" in mods_cible:
        riposte /= 2
    ehp_att = sum(defense_att.get(k) or 0
                  for k in ("shield_hp", "armor_health", "hull_health"))
    if "moitie_vie" in mods_att:
        ehp_att /= 2
    # **`facteur_retour` peut valoir zéro, et il manquait à la garde.**
    # `taux_moyen` rend 0 quand la cible ne touche jamais à la distance
    # d'équilibre — c'est un cas réel, pas une anomalie : trois Corsair
    # contre un Hammerhead le produisent, et le balayage du 2026-08-15 l'a
    # levé en `ZeroDivisionError`. Un taux nul veut dire que le groupe
    # n'est **jamais** abattu, donc `None` — la même valeur que « pas de
    # riposte », et le rendu la traite déjà.
    t_groupe = (n * ehp_att / (riposte * facteur_retour)
                if riposte and ehp_att and facteur_retour else None)

    victoire = t_cible is not None and (t_groupe is None or t_cible < t_groupe)
    duel.update(
        n=n, duel_verrous=verrous,
        bataille={"victoire": victoire, "t_cible": t_cible,
                  "t_groupe": t_groupe, "facteur_mobilite": facteur,
                  "facteur_riposte": facteur_retour,
                  "modele_agilite": 3,
                  "agilite": (camp_att["profil"], camp_cible["profil"]),
                  "distance_engagement": round(distance), "riposte": riposte,
                  "riposte_armes_tourelles": riposte_tourelles,
                  "mods_att": sorted(mods_att),
                  "mods_cible": sorted(mods_cible)},
        incomprises=incomprises or [])
    return _qualifier_combat(con, duel)


def _meilleur_armement(con: sqlite3.Connection, ship_uuid: str,
                       defense: dict[str, Any],
                       vol_attaquant: dict[str, Any],
                       vol_cible: dict[str, Any],
                       defense_attaquant: dict[str, Any],
                       criteres: dict[str, Any]) -> tuple[list[dict], str]:
    """Le loadout optimal pour ce duel : par affût, l'arme qui **touche le
    plus** à la distance où le duel se jouera.

    « Meilleur » n'est ni le plus de DPS brut — une arme qui ricoche ne vaut
    rien — ni le plus de DPS qui passe : c'est le plus de DPS qui **porte**.
    Deux corrections du 2026-08-14, demandées par l'utilisateur.

    **La taille de la cible décide du type d'arme.** « Un Paladin tu peux
    lui tirer dessus de plus loin avec des canons et toucher quand même vu
    sa taille ; c'est pour ça que c'est inutile de prendre des repeaters. »
    Le tri passe donc par `engagement.taux_de_touche`, qui connaît le temps
    de vol du projectile, la section présentée par la cible et son esquive.
    L'ancien tri pondérait par `_poursuite_d_arme` — cadence et vitesse
    seules, sans la cible — et **seulement si** le joueur avait dit « cible
    rapide » : dans le cas courant, c'était un classement au DPS brut.

    **La distance et l'armement se choisissent ensemble.** « La distance
    d'engagement change si les armes sont différentes. » On itère donc :
    choisir les armes à une distance, recalculer la distance qu'elles
    imposent, recommencer. Trois tours suffisent — le point fixe est atteint
    en un ou deux sur tout le catalogue.
    """
    # **Un socle n'est pas un affût, et c'était faux sur 45 vaisseaux
    # sur 109.** Les ports d'arme sont publiés sur **deux niveaux** :
    #
    #   depth 0, `category='weapon_mount'` — le socle ou le gimbal, qui
    #     accepte un `Turret` ou une `TurretBase` ;
    #   depth 1, `category='weapon'` — l'affût réel, qui accepte un
    #     `WeaponGun` et porte l'arme.
    #
    # Le modèle comptait les **socles**. Un socle en porte souvent
    # plusieurs, et pas à sa taille : celui de l'Arrow est publié S3 et
    # porte **deux** canons S1, si bien que le meilleur armement lui
    # montait 3 armes S3 au lieu de 2 S3 et 2 S1. Le F7A Hornet Mk II
    # sortait avec 2 affûts S4 quand il en a 4 en S3 et 2 en S4 ; l'Ares
    # Star Fighter n'avait **aucun** affût et se retrouvait désarmé ; et le
    # socle de tourelle du Vanguard, publié S6 parce qu'une tourelle S6 s'y
    # monte, lui faisait porter un Deadbolt VI de 1 537 d'alpha par plomb
    # là où sa plus grosse arme est un S5.
    #
    # On compte donc les ports d'arme, à leur propre taille.
    # Et un poste que personne ne tient ne se rééquipe pas non plus : la
    # règle valait pour l'armement d'origine et pas pour le meilleur, si
    # bien que le Vanguard récupérait ses deux affûts de tourelle dès qu'on
    # lui cherchait un loadout.
    exclues = _racines_inoccupees(con, ship_uuid)
    hors = ""
    if exclues:
        hors = ("AND COALESCE((SELECT r.hardpoint_name FROM hardpoints r "
                "WHERE r.ship_uuid = h.ship_uuid "
                "  AND r.port_id = h.root_port_id), '') NOT IN ("
                + ",".join("?" * len(exclues)) + ") ")
    affuts = [dict(r) for r in con.execute(
        "SELECT h.max_size, COUNT(*) AS n FROM hardpoints h "
        "WHERE h.ship_uuid = ? AND h.category = 'weapon' "
        "AND h.max_size IS NOT NULL "
        + hors + "GROUP BY h.max_size",
        (ship_uuid, *exclues))]
    affuts = _avec_socles_vides(con, ship_uuid, affuts, exclues)
    if not affuts:
        return [], ""

    profil_att = engagement.profil_de_vol(vol_attaquant)
    profil_cible = engagement.profil_de_vol(vol_cible)
    pv_cible = sum(defense.get(k) or 0 for k in
                   ("shield_hp", "armor_health", "hull_health"))
    pv_att = sum(defense_attaquant.get(k) or 0 for k in
                 ("shield_hp", "armor_health", "hull_health"))

    candidats_par_taille: dict[int, list[dict]] = {}
    for taille_row in {a["max_size"] for a in affuts}:
        candidats = [dict(r) for r in con.execute(
            "SELECT i.uuid, i.name, i.size, s.alpha, s.pellets_per_shot, "
            "       s.dps, s.dps_soutenu, s.ammo_capacity, s.weapon_class, "
            "       s.weapon_kind, "
            "       s.fire_modes, s.dps_physical, s.dps_energy, "
            "       s.projectile_speed, s.rounds_per_minute, "
            "       s.effective_range "
            "FROM item_stats s JOIN items i ON i.uuid = s.item_uuid "
            "WHERE i.type = 'WeaponGun' AND i.size = ? AND s.alpha > 0 "
            # **Une arme soudée à un porteur n'est pas un loadout.** Sans ce
            # filtre, le meilleur armement d'un Hornet sortait « 1× Reign-3
            # + 2× Deadbolt IV + 1× Slayer » — le Reign-3 est l'arme du char
            # Tumbril Storm, le Slayer celle du char Nova (18 500 d'alpha
            # par plomb). Mesuré le 2026-08-14 sur la question réelle
            # « quel est le meilleur loadout d'un Hornet F7A contre un
            # Paladin ».
            # Et une arme sans nom n'est pas équipable non plus : le jeu ne
            # la publie pas au catalogue. `VNCL_Gen2_PlasmaCannon_S2` se
            # montait sur le Hawk, le Merlin et le Buccaneer, à 1 831 DPS,
            # affiché « ? » dans le loadout rendu au joueur.
            #
            # **Les armes vanduul ne s'achètent pas**, et le filtre du nom
            # ne suffisait pas à les écarter : sept d'entre elles en ont un
            # — 'WASP', 'WAR', 'WRATH', 'WEAK', 'WARLORD' — et le meilleur
            # loadout les montait sur des vaisseaux humains dans six des
            # seize duels du banc. Remarque de l'utilisateur, 2026-08-16 :
            # « c'est quoi des wasp ? jamais entendu parler ». Le préfixe
            # `VNCL_` du `class_name` les désigne sans ambiguïté ; les
            # autres préfixes du catalogue sont humains ou alliés (Banu,
            # Aopoa), donc achetables. Le **stock** d'un Scythe ou d'un
            # Glaive les garde : on ne retire que ce qu'on propose.
            "AND i.mount_usable = 1 AND i.flight_ready = 1 "
            "AND i.porteur_exclusif IS NULL AND i.name IS NOT NULL "
            "AND i.class_name NOT LIKE 'VNCL\\_%' ESCAPE '\\'",
            (taille_row,))]
        # Filtre de critères explicites (grade, famille) quand donnés.
        if criteres.get("weapon_class"):
            candidats = [c for c in candidats
                         if c.get("weapon_class") == criteres["weapon_class"]]
        # **Le genre est le vocabulaire du joueur, la classe ne l'est pas.**
        # `weapon_class` sépare laser de balistique ; `weapon_kind` sépare
        # canon (62), repeater (35), gatling (19) et scattergun (13), et
        # c'est ainsi qu'un duel se cadre — « à loadout égal, des repeaters
        # ou gatling contre une petite cible, peut-être des canons contre
        # une grosse » (utilisateur, 2026-08-14). Une liste est acceptée,
        # parce que « repeaters/gatling » en désigne deux.
        if criteres.get("weapon_kind"):
            voulus = criteres["weapon_kind"]
            if isinstance(voulus, str):
                voulus = [voulus]
            voulus = {v.lower() for v in voulus}
            candidats = [c for c in candidats
                         if (c.get("weapon_kind") or "").lower() in voulus]
        candidats_par_taille[taille_row] = candidats

    # Les deux couches de la cible, sous la forme qu'`engagement` attend :
    # c'est leur rapport de points de vie qui décide du rendement d'un type
    # de dégât, et il va de 1 % d'armure sur un Polaris à 35 % sur un
    # Gladius.
    structure = {
        "armure": float(defense.get("armor_health") or 0.0),
        "coque_nue": float(defense.get("hull_health") or 0.0),
        "res": {"physical": float(defense.get("res_physical") or 1.0),
                "energy": float(defense.get("res_energy") or 1.0)},
        "mult": {"physical": float(defense.get("mult_physical") or 1.0),
                 "energy": float(defense.get("mult_energy") or 1.0)},
    }

    #: **Fenêtre de vitesse de projectile d'un loadout cohérent.**
    #: « Il ne faut pas d'armes avec des vitesses différentes ensemble, si
    #: jamais ça fonctionne c'est que ton modèle ne fonctionne pas »
    #: (utilisateur, 2026-08-16). La raison est de terrain : deux vitesses
    #: différentes, ce sont deux solutions de tir, et le réticule ne peut
    #: en afficher qu'une — on rate avec la moitié de ses armes.
    #:
    #: Le choix affût par affût ignorait complètement ce point, et sortait
    #: par exemple un Havoc Scattergun à 900 m/s à côté d'une Revenant
    #: Gatling à 1 345, soit un écart de moitié.
    FENETRE_VITESSE = 1.15

    def _choisir_avec_reference(distance: float,
                                reference: float | None) -> list[dict]:
        """Le meilleur armement à cette distance, autour de cette vitesse.

        `reference` borne les projectiles à une fenêtre commune : c'est ce
        qui rend le loadout **tirable**, une seule solution de tir servant
        toutes les armes.
        """
        retenues = []
        for taille_row, candidats in candidats_par_taille.items():
            if reference is not None:
                candidats = [
                    c for c in candidats
                    if not c.get("projectile_speed")
                    or (1 / FENETRE_VITESSE
                        <= c["projectile_speed"] / reference
                        <= FENETRE_VITESSE)] or candidats
            n = sum(a["n"] for a in affuts if a["max_size"] == taille_row)
            passantes, toutes = [], []
            for c in candidats:
                profil = _profil(dict(c, n=n))
                if profil is None:
                    continue
                # Ce qui porte vraiment : le DPS multiplié par la
                # probabilité de toucher cette cible-là, à cette
                # distance-là — **et par ce que cette cible-là encaisse**.
                #
                # Le critère s'arrêtait au DPS porté, ce qui était le
                # même défaut que les deux corrigés avant lui : le duel
                # avait appris que l'armure et la coque n'encaissent pas
                # les deux types de dégâts pareil (0,81/1,15 puis
                # 0,75/0,60), et le choix d'armement l'ignorait. Il
                # conseillait donc un loadout que sa propre simulation ne
                # jugeait pas le meilleur.
                porte = (profil["dps"]
                         * engagement.taux_de_touche(profil, profil_att,
                                                     profil_cible, distance)
                         * engagement._rendement_structure(profil, structure))
                # Départage stable : jamais l'ordre du dictionnaire.
                toutes.append((porte, c["name"], c))
                seuil = (defense.get("defl_physical")
                         if profil["type"] == "physical"
                         else defense.get("defl_energy")) or 0
                if profil["par_plomb"] is not None and profil["par_plomb"] < seuil:
                    continue
                passantes.append((porte, c["name"], c))
            # Rien ne passe à cette taille : on prend le meilleur quand même,
            # le rendu dira que la déflexion le bloque.
            classement = passantes or toutes
            if not classement:
                continue
            meilleure = max(classement, key=lambda x: (x[0], x[1]))[2]
            retenues.append(dict(meilleure, n=n, poste="pilote"))
        return retenues

    def _valeur(retenues: list[dict], distance: float) -> float:
        """Ce que vaut un loadout entier, au même critère qu'un affût.

        **Une arme qui ricoche vaut zéro**, et l'oublier a coûté une
        régression immédiate : contraint à une vitesse commune, le modèle
        préférait pour l'Arrow contre un Corsair deux Bulldog et deux
        Revenant — 18 et 63 par plomb contre un seuil de 95 — à des canons
        qui percent mais tirent moins vite. Un loadout cohérent qui
        n'entame rien n'est pas un loadout.
        """
        total = 0.0
        for a in retenues:
            profil = _profil(a)
            if profil is None:
                continue
            seuil = (defense.get("defl_physical")
                     if profil["type"] == "physical"
                     else defense.get("defl_energy")) or 0
            if profil["par_plomb"] is not None and profil["par_plomb"] < seuil:
                continue
            total += (profil["dps"]
                      * engagement.taux_de_touche(profil, profil_att,
                                                  profil_cible, distance)
                      * engagement._rendement_structure(profil, structure))
        return total

    def _choisir(distance: float) -> list[dict]:
        """Le meilleur armement **cohérent** à cette distance.

        On compose un loadout autour de chaque vitesse de projectile
        disponible, et on garde le meilleur ensemble — plutôt que le
        meilleur de chaque affût pris isolément, qui produisait des
        attelages intirables.
        """
        vitesses = sorted({
            c["projectile_speed"] for cands in candidats_par_taille.values()
            for c in cands if c.get("projectile_speed")})
        # Sans l'essai libre : il n'a aucune contrainte et gagnerait
        # toujours, ce qui reviendrait à ne rien imposer. Il ne sert que de
        # repli quand aucune vitesse n'est publiée (armes à faisceau).
        essais = [_choisir_avec_reference(distance, v) for v in vitesses]
        if not vitesses:
            essais = [_choisir_avec_reference(distance, None)]
        return max((e for e in essais if e),
                   key=lambda e: _valeur(e, distance), default=[])

    def _distance_pour(retenues: list[dict]) -> float:
        """La distance que cet armement conduit l'attaquant à chercher."""
        profils = [p for a in retenues if (p := _profil(a)) is not None]
        if not profils:
            return engagement.DISTANCE_MIN
        camp_a = engagement.camp("attaquant", profils, vol_attaquant,
                                 defense_attaquant, defense)
        camp_b = engagement.camp("cible", [], vol_cible, defense,
                                 defense_attaquant)
        voulue, _ = engagement.distance_preferee(
            camp_a, camp_b, max(pv_att, 1.0), max(pv_cible, 1.0))
        return min(voulue, engagement.DISTANCE_MAX)

    # Point fixe entre l'armement et la distance. On part du contact, la
    # distance la plus discriminante : c'est là que les cadences comptent.
    distance = engagement.DISTANCE_MIN
    armes = _choisir(distance)
    for _ in range(3):
        suivante = _distance_pour(armes)
        if abs(suivante - distance) < 1e-6:
            break
        distance = suivante
        armes = _choisir(distance)

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
        # **La cible entre toujours dans le choix**, plus seulement quand le
        # joueur a dit « cible rapide » : sa taille et son esquive décident
        # autant que sa déflexion de ce qui vaut la peine d'être monté.
        meilleures, nom = _meilleur_armement(
            con, attaquant["uuid"], defense,
            _charger_profil_vol(con, attaquant["uuid"]),
            _charger_profil_vol(con, defenseur["uuid"]),
            _defense(con, attaquant["uuid"]) or {}, criteres)
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
    # Modèle 3 depuis le 2026-08-13 : des grandeurs physiques (m/s², °/s, m²)
    # au lieu de quatre scores normalisés dont l'un, l'évasion, se lisait sur
    # une colonne qui compte les tuyères plutôt que l'accélération.
    agilite = {
        "modele": 3,
        "attaquant": engagement.profil_de_vol(
            _charger_profil_vol(con, attaquant["uuid"])),
        "defenseur": engagement.profil_de_vol(
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
    # **Un duel dont la défense est inconnue rend une forme courte.**
    # L'Argo ATLS IKTI est un exosquelette de manutention : il n'a ni
    # bouclier ni coque publiés, et `peut_detruire` rend alors
    # `sans_defense_connue` sans la clé `defense`. Le matchup la lisait
    # d'office et sortait en `KeyError` — deux cas au balayage du
    # 2026-08-15. On ne peut pas comparer ce qu'on ne sait pas chiffrer :
    # on rend None, et l'appelant l'écarte comme les autres cas muets.
    if "defense" not in aller or "defense" not in retour:
        return None
    t_aller_meca = _temps_de_destruction(aller)
    t_retour_meca = _temps_de_destruction(retour)
    defenseur = _profil_matchup(con, aller["defenseur"]["uuid"])

    # **Le modèle d'engagement remplace les facteurs de mobilité** depuis le
    # 2026-08-13. Les anciens (`_facteur_tir`, `_facteur_poursuite_armes`)
    # bornaient l'effet de l'agilité à ×0,55–1,45 sur un temps final : face
    # à un rapport de PV de 2,15×, ils ne pouvaient pas renverser un
    # verdict, et l'Aurora Mk II sortait gagnante contre un Arrow. Ici le
    # taux de touche s'applique au **DPS**, à la distance que le plus rapide
    # impose, et il peut donc faire passer un attaquant sous la
    # régénération du bouclier adverse — c'est-à-dire rendre sa victoire
    # impossible, pas seulement lente.
    camp_att = engagement.camp(
        aller["attaquant"]["name"], aller.get("armes") or [],
        _charger_profil_vol(con, aller["attaquant"]["uuid"]),
        retour["defense"], aller["defense"])
    camp_def = engagement.camp(
        aller["defenseur"]["name"], retour.get("armes") or [],
        _charger_profil_vol(con, aller["defenseur"]["uuid"]),
        aller["defense"], retour["defense"])
    pv_att = camp_att["bouclier_max"] + camp_att["coque"]
    pv_def = camp_def["bouclier_max"] + camp_def["coque"]
    distance = engagement.distance_d_equilibre(
        camp_att, camp_def, pv_att, pv_def)

    f_aller = engagement.taux_moyen(camp_att, camp_def, distance)
    f_retour = engagement.taux_moyen(camp_def, camp_att, distance)
    # Un bouclier qui se recharge plus vite qu'on ne le vide ne tombe
    # jamais : le sens devient impossible, pas long. C'est le verrou qui
    # fait décrocher l'Arrow devant un Corsair au lieu de le gratter.
    perce_aller = engagement.dps_net(camp_att, camp_def, distance) > 0
    perce_retour = engagement.dps_net(camp_def, camp_att, distance) > 0

    t_aller = (t_aller_meca / f_aller
               if t_aller_meca is not None and f_aller > 0 and perce_aller
               else None)
    t_retour = (t_retour_meca / f_retour
                if t_retour_meca is not None and f_retour > 0 and perce_retour
                else None)
    # Un siège n'est pas un duel : au-delà de la durée que la simulation
    # accorde à un combat, le sens n'est pas concluant. Sans cette borne
    # partagée, « qui gagne » et « sur 10 combats » se contredisaient.
    if not engagement.conclut(t_aller):
        t_aller = None
    if not engagement.conclut(t_retour):
        t_retour = None

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
        # La distance explique le verdict mieux que les facteurs : c'est
        # elle que le plus rapide impose, et elle doit rester vérifiable.
        "distance_engagement": round(distance),
        "rapport_temps": rapport, "verdict": verdict,
        "agilite": aller.get("agilite"),
        # Le modèle d'engagement, tel qu'il a réellement servi : les profils
        # physiques des deux bords, la distance retenue et les taux de
        # touche. Le rendu doit pouvoir montrer **ce qui a décidé**.
        "engagement": {
            "attaquant": camp_att["profil"], "defenseur": camp_def["profil"],
            "distance": round(distance),
            "taux": {"aller": f_aller, "retour": f_retour},
            "perce": {"aller": perce_aller, "retour": perce_retour},
            "armes_deviees": {"aller": camp_att["armes_deviees"],
                              "retour": camp_def["armes_deviees"]},
            "regen": {"attaquant": camp_att["regen"],
                      "defenseur": camp_def["regen"]},
        },
        "source_peut_detruire": t_aller is not None,
        "cible_peut_detruire": t_retour is not None,
        "armes_source": len(aller.get("armes") or []),
        "armes_cible": len(retour.get("armes") or []),
        "tourelles_source": aller.get("armes_en_tourelles") or 0,
        "tourelles_cible": retour.get("armes_en_tourelles") or 0,
    }


def simuler_duel(con: sqlite3.Connection, query: str, *,
                 cible: str | None = None,
                 passes: int = 10) -> dict[str, Any]:
    """« Sur 10 combats, combien en gagne le Arrow ? »

    Demandée deux fois au journal — le 2026-08-12 puis le 2026-08-13 — et
    refusée les deux fois, faute d'un modèle : `peut_detruire` rend un temps
    de destruction déterministe, pas une issue. Le refus était honnête mais
    la question est légitime, et la donnée permettait d'y répondre.

    Ce que la simulation ajoute au duel, et qu'aucun temps de feu ne dit :
    la **distance** est choisie par chacun et tranchée par la vitesse, le
    **taux de touche** dépend de l'agilité des deux et de la vitesse des
    projectiles, et la **fuite** est une issue — « un Arrow ne pourra pas
    forcément se faire détruire par un Hammerhead, il va pouvoir s'enfuir ».

    Les verrous du duel restent souverains : ce qui ricoche sur l'armure ne
    touche pas davantage parce qu'on a bien manœuvré, et un bouclier qui se
    recharge plus vite qu'on ne le vide ne tombe jamais.
    """
    if not cible:
        raise NotFound(query, resolve(con, query, entity_types=("ship",)))
    aller = peut_detruire(con, query, cible=cible)
    retour = peut_detruire(con, cible, cible=query)
    attaquant, defenseur = aller["attaquant"], aller["defenseur"]

    manques = []
    if aller.get("sans_defense_connue") or retour.get("sans_defense_connue"):
        manques.append("défense publiée absente pour l'un des deux")
    # **Le constat était fait, la conséquence n'était pas tirée.** Sans
    # défense publiée, `peut_detruire` rend une forme courte sans la clé
    # `defense`, et la simulation partait quand même — `KeyError` au
    # balayage du 2026-08-15 sur l'Argo ATLS IKTI, un exosquelette de
    # manutention. Une donnée indispensable qui manque se **nomme**, comme
    # le duel le fait déjà pour l'arme qui ne rentre sur aucun affût.
    if "defense" not in aller or "defense" not in retour:
        # **Les deux sens ne portent pas la défense du même appareil.**
        # `aller` est « query attaque cible », donc son `defense` est celle
        # de la **cible** ; `retour` est l'inverse, et son `defense` est
        # celle de l'attaquant. Nommer le mauvais des deux fait dire au bot
        # que le Hammerhead n'a pas de coque — mesuré en écrivant ce
        # correctif, et exactement le genre d'erreur plausible qu'on ne
        # relit pas.
        muet = (attaquant if "defense" not in retour else defenseur)["name"]
        raise NotFound(
            query,
            explication=(
                f"Le jeu ne publie ni bouclier ni coque pour **{muet}** — "
                f"c'est le cas des exosquelettes et de quelques véhicules. "
                f"Sans défense chiffrée, une simulation de duel ne "
                f"reposerait sur rien."))
    vol_a = _charger_profil_vol(con, attaquant["uuid"])
    vol_b = _charger_profil_vol(con, defenseur["uuid"])
    for ship, vol in ((attaquant, vol_a), (defenseur, vol_b)):
        profil = engagement.profil_de_vol(vol)
        if profil["sans_dimensions"]:
            manques.append(f"dimensions absentes pour {ship['name']}")
        if profil["sans_acceleration"]:
            manques.append(f"accélérations absentes pour {ship['name']}")

    tirage = engagement.simuler(
        attaquant["name"], aller["armes"], vol_a, retour["defense"],
        defenseur["name"], retour["armes"], vol_b, aller["defense"],
        passes=min(max(int(passes), 1), 50))

    contrat = qualite_reponse(
        faits={"attaquant": attaquant["name"], "cible": defenseur["name"],
               "passes": tirage["passes"]},
        manques=manques,
        # Le modèle d'engagement est maison : il se **déclare** comme source
        # distincte du jeu, pour qu'aucune réponse ne le fasse passer pour
        # une mécanique publiée.
        sources=["jeu", "modele_engagement"],
        fraicheur={"jeu": fraicheur_jeu(con)})
    return {
        "attaquant": attaquant, "defenseur": defenseur,
        "simulation": tirage,
        "duel": aller.get("duel"), "duel_retour": retour.get("duel"),
        "qualite_reponse": contrat, "complet": contrat["complet"],
        "resolution": aller.get("resolution"),
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
        # Défense inconnue d'un côté ou de l'autre : le matchup rend None
        # plutôt que de lever. Il se compte comme un `NotFound`, puisque
        # c'est la même chose du point de vue du joueur — on ne sait pas.
        if analyse is None:
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
