"""Le réseau d'énergie : budget, efficacité par pip, loadouts, furtivité.

Tout repose sur `item_reseau` (docs/ANALYSE_ENERGIE.md) : le Power circule
en **pips entiers** — les barres de l'interface — pour les gros systèmes,
et en unités standard fractionnaires pour les armes et le quantum drive.
Les deux ne s'additionnent jamais : la passerelle n'est pas publiée.

Le mécanisme du choix est chiffré par composant : trois paliers
d'allocation (1 pip → ×0,70 de performance, 2 → ×0,85, 3 → ×1,00 sur les
S1 mesurés) et un plancher (`min_fraction`, 1 = tout ou rien). Un
générateur stock ne couvre pas la demande d'un chasseur stock — le
Gladius produit 16 pips et en demande 22.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .resultats import fraicheur_jeu, qualite_reponse
from .resolver import resolve

#: Les familles du réseau : catégorie de hardpoint → type d'objet.
#: Fermé comme `hardpoint_categories` — un type neuf s'ajoute à la main.
_FAMILLES = {
    "power_plant": "PowerPlant",
    "shield_generator": "Shield",
    "cooler": "Cooler",
    "radar": "Radar",
    "quantum_drive": "QuantumDrive",
}


def _reseau_installe(con: sqlite3.Connection,
                     ship_uuid: str) -> list[dict[str, Any]]:
    """Les composants montés qui touchent au réseau, avec leur état Online."""
    return [dict(r) for r in con.execute(
        "SELECT h.category, h.max_size, i.uuid, i.name, i.type, "
        "       r.pips_conso, r.std_conso, r.pips_generes, r.ressource, "
        "       r.generation_std, r.min_fraction, "
        "       r.pips_low, r.mult_low, r.pips_med, r.mult_med, "
        "       r.pips_high, r.mult_high, r.em, r.ir "
        "FROM hardpoints h "
        "JOIN items i ON i.uuid = h.installed_uuid "
        "JOIN item_reseau r ON r.item_uuid = i.uuid AND r.etat = 'Online' "
        "WHERE h.ship_uuid = ?", (ship_uuid,))]


def _vaisseau(con: sqlite3.Connection, query: str):
    res = resolve(con, query, entity_types=("ship",))
    if res.best is None:
        raise NotFound(query, res)
    ship = _dict(_row(con, "SELECT uuid, name FROM ships WHERE uuid = ?",
                      res.best.entity_id))
    if ship is None:
        raise NotFound(query, res)
    return ship, res


def _arbitrages(composants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chaque descente de palier possible, du moins douloureux au plus cher.

    Le coût est réel : « boucliers en medium : −1 pip pour ×0,85 de
    performance ». On ne propose jamais de passer sous `min_fraction` — en
    dessous, le composant s'éteint, ce n'est plus un arbitrage.
    """
    options = []
    for c in composants:
        haut = c.get("pips_high") or c.get("pips_conso")
        if not haut or not c.get("pips_conso"):
            continue
        for palier, libelle in (("med", "medium"), ("low", "low")):
            pips = c.get(f"pips_{palier}")
            mult = c.get(f"mult_{palier}")
            if pips is None or mult is None or pips >= haut:
                continue
            plancher = (c.get("min_fraction") or 0) * haut
            if pips < plancher:
                continue
            options.append({
                "nom": c["name"], "type": c["type"], "palier": libelle,
                "pips_gagnes": haut - pips, "performance": mult,
                # Le tri : perdre le moins de performance par pip gagné.
                "cout_par_pip": (1 - mult) / (haut - pips),
            })
    return sorted(options, key=lambda o: (o["cout_par_pip"],
                                          -o["pips_gagnes"], o["nom"]))


def budget_energie(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Est-ce que tout tient sur mon Gladius ? »

    Production contre demande, composant par composant, puis les
    arbitrages chiffrés. Les armes et le quantum drive se comptent à
    part : leurs unités standard ne se convertissent pas en pips (§3 de
    l'analyse), et les additionner fabriquerait un chiffre que le jeu ne
    publie pas.
    """
    ship, res = _vaisseau(con, query)
    composants = _reseau_installe(con, ship["uuid"])
    if not composants:
        raise NotFound(query, res)

    production = sum(c["pips_generes"] or 0 for c in composants)
    consommateurs = [c for c in composants if c.get("pips_conso")]
    demande = sum(c["pips_conso"] for c in consommateurs)
    en_std = [c for c in composants if c.get("std_conso")]
    deficit = demande - production

    arbitrages = _arbitrages(consommateurs) if deficit > 0 else []
    # Ce que les arbitrages peuvent réellement combler, cumulé dans l'ordre.
    comblable = sum(o["pips_gagnes"] for o in arbitrages)

    qualite = qualite_reponse(
        faits={"vaisseau": ship["name"], "production": production,
               "demande": demande,
               "composants_reseau": len(consommateurs) + len(en_std)},
        manques=(["aucun générateur au réseau"] if not production else []),
        sources=["jeu"], fraicheur={"jeu": fraicheur_jeu(con)})
    return {
        "ship": ship, "production": production, "demande": demande,
        "deficit": deficit, "composants": consommateurs,
        "armes_et_quantum": en_std,
        "arbitrages": arbitrages, "comblable": comblable,
        "qualite_reponse": qualite, "complet": qualite["complet"],
        "resolution": res,
    }


def composants_par_pip(con: sqlite3.Connection, query: str, *,
                       type_composant: str | None = None,
                       taille: int | None = None,
                       limit: int = 10) -> dict[str, Any]:
    """« Quel bouclier consomme le moins », « le meilleur bouclier par pip ».

    Le classement est la génération par pip consommé quand la famille
    produit quelque chose (bouclier, refroidisseur), et les pips croissants
    sinon (radar). La règle s'annonce dans le rendu — « la meilleure »
    n'existe pas sans critère.
    """
    type_ = type_composant or "Shield"
    lignes = [dict(r) for r in con.execute(
        "SELECT i.uuid, i.name, i.size, i.grade_lettre, "
        "       r.pips_conso, r.generation_std, r.ressource, r.em, r.ir, "
        "       r.min_fraction "
        "FROM item_reseau r JOIN items i ON i.uuid = r.item_uuid "
        "WHERE r.etat = 'Online' AND i.type = ? "
        "AND (? IS NULL OR i.size = ?) "
        "AND i.name IS NOT NULL",
        (type_, taille, taille))]
    if not lignes:
        raise NotFound(query)

    produit = any(l.get("generation_std") for l in lignes)
    for l in lignes:
        pips = l.get("pips_conso") or 0
        l["par_pip"] = ((l.get("generation_std") or 0) / pips
                        if produit and pips else None)
    if produit:
        # Un composant à 0 pip et production non nulle serait divin ; il
        # n'existe pas dans la 4.9, mais le tri le mettrait devant sans
        # bruit — on trie sur (par_pip désc), les sans-ratio en queue.
        lignes.sort(key=lambda l: (-(l["par_pip"] or 0), l["pips_conso"] or 0,
                                   l["name"]))
        critere = "generation_par_pip"
    else:
        lignes.sort(key=lambda l: (l["pips_conso"] or 0, l["name"]))
        critere = "pips_croissants"

    qualite = qualite_reponse(
        faits={"type": type_, "candidats": len(lignes), "critere": critere},
        manques=[], sources=["jeu"], fraicheur={"jeu": fraicheur_jeu(con)})
    return {
        "type": type_, "taille": taille, "critere": critere,
        "items": lignes[:max(1, min(limit, 20))],
        "total": len(lignes),
        "qualite_reponse": qualite, "complet": qualite["complet"],
    }


def _candidats_par_type(con: sqlite3.Connection, type_: str,
                        taille: int) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(
        "SELECT i.uuid, i.name, r.pips_conso, r.generation_std, r.em, r.ir "
        "FROM item_reseau r JOIN items i ON i.uuid = r.item_uuid "
        "WHERE r.etat = 'Online' AND i.type = ? AND i.size = ? "
        "AND i.name IS NOT NULL", (type_, taille))]


def loadout_energie(con: sqlite3.Connection, query: str, *,
                    mode: str = "econome") -> dict[str, Any]:
    """Les deux optimiseurs : « économe » libère un maximum de pips,
    « puissant » maximise la performance en gardant tout alimenté à fond.

    Le périmètre est honnête et annoncé : on ne remplace que les familles
    du réseau aux emplacements publiés (bouclier, refroidisseur, radar,
    générateur), à taille de port égale. Les armes se signalent à part —
    passer au balistique libère des unités standard (0,1 contre 1,0),
    pas des pips.
    """
    if mode not in ("econome", "puissant"):
        mode = "econome"
    ship, res = _vaisseau(con, query)
    stock = _reseau_installe(con, ship["uuid"])
    if not stock:
        raise NotFound(query, res)

    production = sum(c["pips_generes"] or 0 for c in stock)
    remplacements = []
    for c in stock:
        if c["type"] == "PowerPlant" and mode == "puissant":
            # Le plus de pips au même port — la qualité de fabrication en
            # ajoute encore (« Power Pips », additif entier), le rendu le dit.
            candidats = [dict(x) for x in con.execute(
                "SELECT i.uuid, i.name, r.pips_generes "
                "FROM item_reseau r JOIN items i ON i.uuid = r.item_uuid "
                "WHERE r.etat='Online' AND i.type='PowerPlant' AND i.size=? "
                "AND r.pips_generes IS NOT NULL",
                (c["max_size"] or 0,))]
            meilleur = max(candidats,
                           key=lambda x: x["pips_generes"], default=None)
            if meilleur and meilleur["pips_generes"] > (c["pips_generes"] or 0):
                remplacements.append({
                    "slot": c["type"], "stock": c["name"],
                    "candidat": meilleur["name"],
                    "gain_pips": meilleur["pips_generes"] - (c["pips_generes"] or 0),
                    "axe": "production",
                })
            continue
        if not c.get("pips_conso"):
            continue
        candidats = _candidats_par_type(con, c["type"], c["max_size"] or 0)
        if not candidats:
            continue
        if mode == "econome":
            # Le moins de pips ; à égalité, la meilleure génération par pip.
            meilleur = min(candidats, key=lambda x: (
                x.get("pips_conso") if x.get("pips_conso") is not None else 99,
                -((x.get("generation_std") or 0)
                  / max(x.get("pips_conso") or 1, 1)), x["name"]))
            gain = (c["pips_conso"] or 0) - (meilleur.get("pips_conso") or 0)
            if meilleur["uuid"] != c["uuid"] and gain > 0:
                remplacements.append({
                    "slot": c["type"], "stock": c["name"],
                    "candidat": meilleur["name"], "gain_pips": gain,
                    "perf_stock": c.get("generation_std"),
                    "perf_candidat": meilleur.get("generation_std"),
                    "axe": "pips",
                })
        else:
            # Puissant : la meilleure génération, au même nombre de pips ou
            # moins — monter la conso creuserait le déficit qu'on comble.
            plafond = c.get("pips_conso") or 0
            eligibles = [x for x in candidats
                         if (x.get("pips_conso") or 0) <= plafond
                         and x.get("generation_std")]
            meilleur = max(eligibles,
                           key=lambda x: x["generation_std"], default=None)
            if (meilleur and meilleur["uuid"] != c["uuid"]
                    and (meilleur["generation_std"] or 0)
                    > (c.get("generation_std") or 0)):
                remplacements.append({
                    "slot": c["type"], "stock": c["name"],
                    "candidat": meilleur["name"],
                    "gain_pips": plafond - (meilleur.get("pips_conso") or 0),
                    "perf_stock": c.get("generation_std"),
                    "perf_candidat": meilleur.get("generation_std"),
                    "axe": "performance",
                })

    demande = sum(c["pips_conso"] or 0 for c in stock)
    gain_total = sum(r["gain_pips"] for r in remplacements
                     if r["axe"] != "production")
    gain_production = sum(r["gain_pips"] for r in remplacements
                          if r["axe"] == "production")
    armes_energie = [c["name"] for c in stock
                     if c["type"] == "WeaponGun" and (c.get("std_conso") or 0) >= 0.5]

    qualite = qualite_reponse(
        faits={"vaisseau": ship["name"], "mode": mode,
               "remplacements": len(remplacements)},
        manques=[], sources=["jeu"], fraicheur={"jeu": fraicheur_jeu(con)})
    return {
        "ship": ship, "mode": mode, "production": production,
        "demande": demande, "remplacements": remplacements,
        "gain_pips": gain_total, "gain_production": gain_production,
        "armes_energie": armes_energie,
        "qualite_reponse": qualite, "complet": qualite["complet"],
        "resolution": res,
    }


def loadout_discret(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Le loadout le plus discret » — les signatures, classement inversé.

    Une signature basse vaut mieux qu'une haute. Les vrais projecteurs se
    nomment : le quantum drive en ligne et le refroidisseur (l'IR ne vient
    presque que de lui).
    """
    ship, res = _vaisseau(con, query)
    stock = _reseau_installe(con, ship["uuid"])
    if not stock:
        raise NotFound(query, res)

    emetteurs = sorted(
        (c for c in stock if (c.get("em") or 0) or (c.get("ir") or 0)),
        key=lambda c: -((c.get("em") or 0) + (c.get("ir") or 0)))
    remplacements = []
    for c in emetteurs:
        candidats = _candidats_par_type(con, c["type"], c["max_size"] or 0)
        discrets = [x for x in candidats
                    if ((x.get("em") or 0) + (x.get("ir") or 0))
                    < ((c.get("em") or 0) + (c.get("ir") or 0))]
        meilleur = min(discrets, key=lambda x: ((x.get("em") or 0)
                                                + (x.get("ir") or 0)),
                       default=None)
        if meilleur:
            remplacements.append({
                "slot": c["type"], "stock": c["name"],
                "em_stock": c.get("em"), "ir_stock": c.get("ir"),
                "candidat": meilleur["name"],
                "em_candidat": meilleur.get("em"),
                "ir_candidat": meilleur.get("ir"),
            })

    qualite = qualite_reponse(
        faits={"vaisseau": ship["name"], "emetteurs": len(emetteurs)},
        manques=[], sources=["jeu"], fraicheur={"jeu": fraicheur_jeu(con)})
    return {
        "ship": ship, "emetteurs": emetteurs,
        "remplacements": remplacements,
        "qualite_reponse": qualite, "complet": qualite["complet"],
        "resolution": res,
    }
