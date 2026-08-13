"""Le réseau d'énergie : budget, efficacité par pip, loadouts, furtivité.

Les pips sont les barres de l'interface — on les appelle « pips » avec
« barres » en première mention, pour que le joueur raccroche. Les armes et
le quantum drive se comptent en unités standard que rien ne convertit en
pips : les deux comptes restent séparés et le rendu le dit.
"""

from __future__ import annotations

from typing import Any

from .socle import _RENDERERS, _nombre

#: Libellés français des types du réseau, fermés comme la table du routeur.
_TYPES_FR = {
    "Shield": "bouclier", "Cooler": "refroidisseur",
    "PowerPlant": "générateur", "Radar": "radar",
    "QuantumDrive": "moteur quantique",
    "FlightController": "contrôleur de vol",
    "LifeSupportGenerator": "support de vie", "WeaponGun": "arme",
    "QuantumInterdictionGenerator": "brouilleur quantique",
    "WeaponMining": "laser de minage", "TractorBeam": "rayon tracteur",
}

#: Ce que produit chaque famille, pour lire « 602/s » sans ambiguïté.
_RESSOURCES_FR = {"Shield": "bouclier", "Coolant": "refroidissement",
                  "LifeSupport": "air"}


def _type_fr(type_: str) -> str:
    return _TYPES_FR.get(type_, type_)


def render_budget_energie(data: dict[str, Any]) -> str:
    ship = data["ship"]["name"]
    production = data["production"]
    demande = data["demande"]
    deficit = data["deficit"]
    lignes = []
    if deficit > 0:
        lignes.append(
            f"**Non — le {ship} stock ne peut pas tout alimenter à fond : "
            f"{_nombre(round(production))} pips d'énergie produits pour "
            f"{_nombre(round(demande))} demandés (il manque "
            f"{_nombre(round(deficit))}).**")
    else:
        lignes.append(
            f"**Oui — le {ship} stock alimente tout à fond : "
            f"{_nombre(round(production))} pips produits pour "
            f"{_nombre(round(demande))} demandés.**")
    lignes.append("")
    lignes.append("**Ce que chaque composant demande** (pips = les barres "
                  "d'énergie de l'interface) :")
    for c in sorted(data["composants"], key=lambda x: -(x["pips_conso"] or 0)):
        pips = round(c["pips_conso"])
        lignes.append(f"- {c['name']} ({_type_fr(c['type'])}) : "
                      f"{_nombre(pips)} pip{'s' if pips > 1 else ''}")
    if data.get("armes_et_quantum"):
        armes = ", ".join(
            f"{c['name']} ({c['std_conso']:g})".replace(".", ",")
            for c in data["armes_et_quantum"])
        lignes.append(
            f"\nÀ part, en unités d'énergie que le jeu ne convertit pas en "
            f"pips : {armes} — une balistique tourne à 0,1 là où une arme à "
            "énergie demande 1,0.")
    if data.get("arbitrages"):
        lignes.append("\n**Pour combler le déficit, du moins douloureux au "
                      "plus coûteux :**")
        restant = deficit
        for a in data["arbitrages"]:
            if restant <= 0:
                break
            lignes.append(
                f"- {a['nom']} en {a['palier']} : "
                f"−{_nombre(round(a['pips_gagnes']))} pip(s) pour "
                f"×{a['performance']:.2f} de performance".replace(".", ","))
            restant -= a["pips_gagnes"]
        if restant > 0:
            lignes.append(
                f"- même tout baissé, il manque encore "
                f"{_nombre(round(restant))} pip(s) : il faut éteindre un "
                "composant ou monter un générateur plus productif — la "
                "qualité de fabrication en ajoute (« Power Pips »).")
    return "\n".join(lignes)


def render_composants_par_pip(data: dict[str, Any]) -> str:
    type_fr = _type_fr(data["type"])
    taille = (f" taille {_nombre(data['taille'])}"
              if data.get("taille") else "")
    if data["critere"] == "generation_par_pip":
        tete = (f"**Les {type_fr}s{taille} classés par production par pip "
                "consommé** — le meilleur rendement énergétique d'abord :")
    else:
        tete = (f"**Les {type_fr}s{taille} classés par consommation** — "
                "le plus sobre d'abord :")
    lignes = [tete]
    for i in data["items"]:
        pips = _nombre(round(i["pips_conso"] or 0))
        if i.get("par_pip"):
            ressource = _RESSOURCES_FR.get(i.get("ressource") or "", "produit")
            lignes.append(
                f"- **{i['name']}** — {_nombre(round(i['par_pip']))} de "
                f"{ressource}/s par pip ({pips} pips"
                + (f", grade {i['grade_lettre']}" if i.get("grade_lettre")
                   else "") + ")")
        else:
            lignes.append(f"- **{i['name']}** — {pips} pips"
                          + (f", grade {i['grade_lettre']}"
                             if i.get("grade_lettre") else ""))
    if data["total"] > len(data["items"]):
        lignes.append(f"\n({_nombre(data['total'])} au catalogue, les "
                      f"{_nombre(len(data['items']))} premiers affichés.)")
    return "\n".join(lignes)


def render_loadout_energie(data: dict[str, Any]) -> str:
    ship = data["ship"]["name"]
    mode = data["mode"]
    lignes = []
    if mode == "econome":
        lignes.append(f"**Le loadout le plus économe pour le {ship}** — "
                      "libérer des pips à taille de port égale :")
    else:
        lignes.append(f"**Le loadout le plus puissant pour le {ship} sans "
                      "creuser le déficit** — meilleure performance à pips "
                      "égaux ou moindres, et le générateur le plus "
                      "productif :")
    if not data["remplacements"]:
        lignes.append("Le loadout stock est déjà l'optimum sur ce critère "
                      "au catalogue de la 4.9.")
    for r in data["remplacements"]:
        detail = f"−{_nombre(round(r['gain_pips']))} pip(s)"
        if r["axe"] == "production":
            detail = f"+{_nombre(round(r['gain_pips']))} pips de production"
        elif r.get("perf_candidat"):
            detail += (f", {_nombre(round(r['perf_candidat']))}/s contre "
                       f"{_nombre(round(r.get('perf_stock') or 0))}/s")
        lignes.append(f"- {_type_fr(r['slot'])} : {r['stock']} → "
                      f"**{r['candidat']}** ({detail})")
    if data.get("gain_pips"):
        lignes.append(f"\nGain total : **{_nombre(round(data['gain_pips']))} "
                      f"pips libérés** sur les {_nombre(round(data['demande']))} "
                      f"demandés ({_nombre(round(data['production']))} produits).")
    if data.get("gain_production"):
        lignes.append(f"Production : **+{_nombre(round(data['gain_production']))} "
                      "pips** — et la qualité de fabrication du générateur en "
                      "ajoute encore (modificateur « Power Pips », publié).")
    if mode == "econome" and data.get("armes_energie"):
        lignes.append(
            "\nCôté armes (hors pips) : passer "
            + ", ".join(data["armes_energie"][:4])
            + " en balistique divise leur demande par dix (0,1 contre 1,0 "
            "unité) — au prix des munitions à racheter.")
    return "\n".join(lignes)


def render_loadout_discret(data: dict[str, Any]) -> str:
    ship = data["ship"]["name"]
    lignes = [f"**Ce qui rend le {ship} visible, du plus bruyant au plus "
              "discret** (signature basse = mieux) :"]
    for c in data["emetteurs"]:
        parties = []
        if c.get("em"):
            parties.append(f"EM {_nombre(round(c['em']))}")
        if c.get("ir"):
            parties.append(f"IR {_nombre(round(c['ir']))}")
        lignes.append(f"- {c['name']} ({_type_fr(c['type'])}) : "
                      + ", ".join(parties))
    if data["remplacements"]:
        lignes.append("\n**Plus discret au même port :**")
        for r in data["remplacements"]:
            avant = (r.get("em_stock") or 0) + (r.get("ir_stock") or 0)
            apres = (r.get("em_candidat") or 0) + (r.get("ir_candidat") or 0)
            lignes.append(
                f"- {_type_fr(r['slot'])} : {r['stock']} → **{r['candidat']}** "
                f"({_nombre(round(avant))} → {_nombre(round(apres))} de "
                "signature cumulée)")
    lignes.append(
        "\n_Le moteur quantique n'émet qu'en ligne — le couper au mouillage "
        "éteint sa signature — et l'IR vient presque entièrement du "
        "refroidisseur._")
    return "\n".join(lignes)


_RENDERERS["budget_energie"] = render_budget_energie
_RENDERERS["composants_par_pip"] = render_composants_par_pip
_RENDERERS["loadout_energie"] = render_loadout_energie
_RENDERERS["loadout_discret"] = render_loadout_discret
