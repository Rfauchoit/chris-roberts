"""Raffiner un minerai — où, comment, et pour quelle recette.

Trois questions qui se ressemblent et n'ont pas la même réponse : « où
raffiner du Stileron » demande un lieu, « quelle méthode est la plus rapide »
demande un procédé, « où raffiner pour fabriquer un P8-AR » demande les deux
en partant d'une recette.

Les règles qui comptent, toutes mesurées :

- **« La meilleure » n'existe pas sans critère.** Le jeu publie trois notes par
  méthode — vitesse, coût, rendement — et elles s'opposent. On dit sur quel axe
  on classe, et quand la question en cite plusieurs on annonce un compromis.
- **Le segment d'une clé peut mentir, le détail dit vrai.**
  `…_FastCareful_Details` vaut « Low Speed // High Cost // High Yield » : le nom
  de la clé a vieilli, la donnée a été mise à jour.
- **Le plus rare commande le sujet, le plus rare mesuré commande le
  classement.** Le Stileron décide où l'on va, mais son rendement n'est pas
  relevé : on classe sur l'Hephaestanite et on **le dit**.
- **Un « près de X » explicite passe devant le rendement**, et « près de X » se
  mesure sur le chemin, pas sur le nom — les raffineries sont des stations de
  Lagrange nommées d'après la planète.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .liens import ligne_cible
from .normalize import normalize
from .resolver import resolve
from .resultats import fraicheur_jeu, qualite_reponse


# **La rareté d'un minerai est écrite par le jeu, dans le nom du gisement.**
# `MineableRock_AsteroidLegendary_Stileron` : contexte, palier, minerai. Cinq
# paliers, et chaque minerai n'en porte qu'un seul — mesuré sur les 65 entrées
# de `resources` qui suivent ce gabarit, la seule exception étant une entrée
# `TEMPLATE` qui les porte tous et qui est du gabarit, pas un minerai.
#
# On ne compte **pas** les gisements pour en déduire la rareté : Stileron
# (légendaire) et Riccite (épique) en ont 87 chacun, le compte ne les sépare
# pas. Le palier, lui, est la classification du jeu.
_PALIERS = {"common": 1, "uncommon": 2, "rare": 3, "epic": 4, "legendary": 5}
_PALIER_FR = {1: "commun", 2: "peu commun", 3: "rare", 4: "épique",
              5: "légendaire"}
_GABARIT_MINERAI = re.compile(
    r"^MineableRock_\w*?(Common|Uncommon|Rare|Epic|Legendary)_(\w+)$",
    re.IGNORECASE)


def _minerai(con: sqlite3.Connection, nom: str) -> dict[str, Any] | None:
    """Le palier de rareté d'un minerai et les systèmes où on l'extrait.

    `nom` est le nom courant — « Stileron » —, celui que porte une recette.
    Les gisements, eux, s'appellent `MineableRock_AsteroidLegendary_Stileron`.
    """
    lignes = [dict(r) for r in con.execute(
        "SELECT uuid, name FROM resources WHERE name LIKE 'MineableRock%'")]
    palier, uuids = 0, []
    for ligne in lignes:
        trouve = _GABARIT_MINERAI.match(ligne["name"])
        if trouve and trouve.group(2).lower() == nom.lower():
            palier = max(palier, _PALIERS[trouve.group(1).lower()])
            uuids.append(ligne["uuid"])
    if not uuids:
        return None

    trous = ",".join("?" * len(uuids))
    systemes: dict[str, int] = {}
    for ligne in con.execute(
            f"SELECT l.system, COUNT(*) FROM resource_locations rl "
            f"JOIN locations l ON l.uuid = rl.location_uuid "
            f"WHERE rl.resource_uuid IN ({trous}) AND l.system IS NOT NULL "
            f"GROUP BY 1", uuids):
        systemes[ligne[0]] = systemes.get(ligne[0], 0) + ligne[1]
    return {"nom": nom, "palier": palier,
            "rarete": _PALIER_FR.get(palier, "inconnu"),
            "systemes": systemes,
            "gisements": sum(systemes.values())}


def _methodes_de_raffinage(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Les neuf méthodes, les meilleurs rendements d'abord.

    Le lieu n'est que la moitié de la question : la **méthode** se choisit au
    terminal, et c'est elle qui arbitre rendement contre coût et durée.
    """
    return [dict(r) for r in con.execute(
        "SELECT COALESCE(nom_fr, nom_en) nom, rendement, cout, vitesse "
        "FROM refinery_methods ORDER BY rendement DESC, vitesse DESC, nom")]


# Les trois axes d'une méthode, et le sens dans lequel « meilleur » va. Le coût
# est le seul où la note basse gagne.
_CRITERES = {
    "rendement": ("rendement", 1, "rendement"),
    "vitesse": ("vitesse", 1, "vitesse"),
    "cout": ("cout", -1, "coût"),
}

_MOTS_DE_CRITERE = (
    # « Efficace » désigne le rendement : c'est ce qu'on retire d'un même
    # chargement de minerai. Le mot est ambigu en français courant, et c'est
    # justement pour ça qu'il faut dire dans la réponse ce qu'on a compris.
    ("efficac", "rendement"), ("rendement", "rendement"),
    ("rentab", "rendement"), ("quantite", "rendement"),
    ("perte", "rendement"), ("gaspill", "rendement"),
    ("rapide", "vitesse"), ("vitesse", "vitesse"), ("vite", "vitesse"),
    ("rapidite", "vitesse"), ("temps", "vitesse"), ("court", "vitesse"),
    ("lent", "vitesse"), ("duree", "vitesse"),
    ("cout", "cout"), ("prix", "cout"), ("cher", "cout"),
    ("economi", "cout"), ("bon marche", "cout"), ("gratuit", "cout"),
)


def detect_criteres(question: str) -> list[str]:
    """Sur quels axes la question demande de classer les méthodes.

    Remarque de l'utilisateur : « si je te demande laquelle est la plus
    efficace ce n'est pas forcément la même » que la plus rapide. Les trois
    axes s'opposent — aucune des neuf méthodes n'est la meilleure partout — et
    une question peut en citer plusieurs à la fois (« la plus rapide **et** la
    plus efficace »), ce qui appelle un compromis, pas un classement.
    """
    norm = normalize(question)
    trouves: list[str] = []
    for mot, critere in _MOTS_DE_CRITERE:
        if mot in norm and critere not in trouves:
            trouves.append(critere)
    return trouves


def methode_de_raffinage(con: sqlite3.Connection, query: str, *,
                         criteres: list[str] | None = None) -> dict[str, Any]:
    """« Quelle est la meilleure technique de raffinage ? »

    Et surtout : **selon quel critère**. Le jeu publie trois notes par méthode
    — vitesse, coût, rendement — et elles s'opposent : Dinyx Solventation a le
    meilleur rendement et la pire vitesse, Gaskin Process l'inverse. Répondre
    « la meilleure » sans dire sur quoi serait un choix déguisé en fait.

    Quand la question cite plusieurs axes, on classe sur leur **somme** et on
    annonce que c'est un compromis : le premier d'un cumul n'est en général le
    premier d'aucun des axes pris seul.
    """
    axes = [c for c in (criteres or []) if c in _CRITERES] or ["rendement"]
    methodes = [dict(r) for r in con.execute(
        "SELECT COALESCE(nom_fr, nom_en) nom, "
        "       COALESCE(description_fr, description_en) description, "
        "       vitesse, cout, rendement, description_fr IS NOT NULL en_francais "
        "FROM refinery_methods WHERE vitesse IS NOT NULL")]

    def note(methode: dict[str, Any], axe: str) -> int:
        colonne, sens, _ = _CRITERES[axe]
        return sens * (methode.get(colonne) or 0)

    for methode in methodes:
        methode["total"] = sum(note(methode, axe) for axe in axes)
    methodes.sort(key=lambda m: (-m["total"], m["nom"]))

    # Le meilleur de **chaque** axe pris seul : c'est ce qui montre que le
    # compromis n'est le premier d'aucun, et ça répond à la question d'après.
    par_axe = {axe: max(methodes, key=lambda m: (note(m, axe), m["nom"]))["nom"]
               for axe in _CRITERES}
    return {"criteres": axes, "methodes": methodes[:5],
            "total_methodes": len(methodes), "par_axe": par_axe,
            "compromis": len(axes) > 1}


def _raffineries_pres(con: sqlite3.Connection, systemes: dict[str, int],
                      lieu: str | None = None,
                      minerai: str | None = None) -> list[dict[str, Any]]:
    """Les raffineries, les meilleures d'abord.

    **Le rendement dépend du minerai, et il est publié** — 215 relevés, 24
    minerais sur 20 terminaux, de -9 % à +13 %. J'avais d'abord conclu le
    contraire en ne regardant que `refineries_audits` (3 lignes) : c'était
    faux, et l'utilisateur l'a signalé. Le rendement passe donc devant tout le
    reste, parce que c'est lui qu'on vient chercher.

    Quand il n'est pas relevé pour ce minerai — le Stileron n'y figure pas —
    on retombe sur la géographie : raffiner là où l'on mine évite de traverser
    un système la soute pleine. Le rendu **dit** sur lequel des deux il
    classe, sinon les deux se confondent.
    """
    rendements: dict[str, float] = {}
    if minerai:
        rendements = {r[0]: r[1] for r in con.execute(
            "SELECT terminal, yield_pct FROM refinery_yields "
            "WHERE commodity = ? COLLATE NOCASE", (minerai,)) if r[0]}
    raffineries = [dict(r) for r in con.execute(
        "SELECT id, name, nickname, star_system, planet, orbit, moon, station, "
        "       outpost, city FROM refineries WHERE available = 1 "
        "ORDER BY star_system, name")]
    # **On ne propose pas une raffinerie d'un système où le minerai n'existe
    # pas.** Le Stileron ne sort que de Pyro : citer les douze raffineries de
    # Stanton après les six de Pyro donne une liste de dix-huit dont deux
    # tiers sont hors sujet. Elles se comptent, elles ne se listent pas.
    # Sauf si le rendement est connu : là, on veut la meilleure raffinerie,
    # même à un système de distance. Un +8 % vaut le déplacement, et c'est au
    # joueur d'arbitrer — on lui donne les deux informations.
    if systemes and not rendements:
        raffineries = [r for r in raffineries
                       if (r.get("star_system") or "") in systemes]
    # **Un lieu ne se compare pas à un nom, mais à son chemin.** « Près de
    # Lorville » classait ARC-L1 devant HUR-L1 : « Lorville » n'apparaît dans
    # aucun champ des raffineries, qui sont des stations de Lagrange nommées
    # d'après la **planète**. Le starmap porte le chemin — « Stanton / Hurston
    # / Lorville » — et c'est « Hurston » qui rapproche.
    voisinage: list[str] = []
    if lieu:
        chemin = _row(con, "SELECT path FROM starmap WHERE name = ? "
                           "ORDER BY LENGTH(path) LIMIT 1", lieu)
        morceaux = [m.strip() for m in ((chemin[0] if chemin else "") or
                                        "").split("/") if m.strip()]
        # Du plus précis au plus large : le lieu lui-même, puis sa lune, sa
        # planète, son système. Un chemin vide laisse le nom tapé.
        voisinage = [normalize(m) for m in reversed(morceaux or [lieu])]

    for raffinerie in raffineries:
        raffinerie["rendement"] = rendements.get(raffinerie.get("name") or "")
        # UEX donne tantôt le nom complet de la station, tantôt un surnom avec
        # le système entre parenthèses. Le registre impose l'unicité avant de
        # raccrocher le starmap : deux « Nyx Gateway » ne doivent jamais être
        # départagées par l'ordre d'insertion.
        lieu_canonique = ligne_cible(
            con, "raffineries_lieux", raffinerie.get("id"))
        if lieu_canonique:
            raffinerie["lieu_uuid"] = lieu_canonique.get("uuid")
            raffinerie["lieu_canonique"] = lieu_canonique.get("name")
            raffinerie["chemin"] = lieu_canonique.get("path")

    def rang(raffinerie: dict[str, Any]) -> tuple:
        champs = [raffinerie.get(c) for c in
                  ("star_system", "planet", "orbit", "moon", "station",
                   "outpost", "city", "nickname", "name")]
        colle = 0
        for poids, cible in enumerate(voisinage):
            if any(valeur and cible in normalize(valeur) for valeur in champs):
                colle = max(colle, len(voisinage) - poids)
        # **Un « près de » explicite passe devant le rendement.** Le joueur
        # qui demande « près de Lorville » pose une contrainte, pas une
        # préférence : lui répondre Levski, à un système de là, parce que le
        # rendement y est meilleur de 5 %, c'est ignorer sa question. Le
        # rendement classe **à l'intérieur** du voisinage.
        #
        # Sans lieu demandé, le rendement mène : c'est ce qu'on vient
        # chercher. Un terminal sans relevé n'est pas un terminal à zéro — il
        # passe après ceux qu'on sait mesurer.
        rendement = raffinerie.get("rendement")
        return (-colle, 0 if rendement is not None else 1,
                -(rendement or 0.0),
                -systemes.get(raffinerie.get("star_system") or "", 0),
                raffinerie.get("name") or "")

    return sorted(raffineries, key=rang)


def _minerai_du_terme(con: sqlite3.Connection,
                      terme: str) -> tuple[Any, dict[str, Any] | None]:
    """Résout un terme en fiche de minerai — la résolution et la fiche."""
    res = resolve(con, terme, entity_types=("resource", "commodity"), limit=30)
    if res.best:
        for cand in res.candidates:
            if cand.score < res.best.score - 25:
                break
            nom = (_dict(_row(con, "SELECT name FROM resources WHERE uuid = ?",
                              cand.entity_id)) or {}).get("name") or cand.name
            trouve = _GABARIT_MINERAI.match(nom or "")
            fiche = _minerai(con, trouve.group(2) if trouve else (nom or ""))
            if fiche:
                return res, fiche
    return res, None


def ou_raffiner(con: sqlite3.Connection, query: str, *,
                lieu: str | None = None,
                minerais: list[str] | None = None,
                systeme: str | None = None,
                classement: str | None = None) -> dict[str, Any]:
    """« Où je dois raffiner du Stileron ? »

    Demande de l'utilisateur. Les fichiers du jeu ne portent pas les
    raffineries ; UEX les publie avec leur position — 21 terminaux sur
    Stanton, Pyro et Nyx.

    **Plusieurs minerais se croisent** — « où raffiner de l'iron et de
    l'hephaestanite, c'est ce que j'ai dans ma soute » : la réponse est
    **une** liste de raffineries, chacune avec son rendement par minerai. Le
    plus rare commande le classement ; à rareté égale, c'est le cumul des
    rendements — et on dit laquelle des deux règles s'applique. Avant ça,
    « de l'iron et de l'or » jetait l'or en silence, la pire des formes.
    """
    res, premier = _minerai_du_terme(con, query)
    fiches = [premier] if premier else []
    for terme in minerais or []:
        _, fiche = _minerai_du_terme(con, terme)
        if fiche and all(f["nom"] != fiche["nom"] for f in fiches):
            fiches.append(fiche)
    fiches.sort(key=lambda f: (-f["palier"], f["nom"]))

    if len(fiches) >= 2:
        return _raffiner_croise(con, res, fiches, lieu, systeme, classement)

    minerai = fiches[0] if fiches else None
    systemes = minerai["systemes"] if minerai else {}
    nom = minerai["nom"] if minerai else None
    retenues = _raffineries_pres(con, systemes, lieu, nom)
    toutes = _raffineries_pres(con, {}, lieu, nom)
    # « À Stanton » restreint la liste — mesuré au journal, la contrainte se
    # perdait en silence et Levski (Nyx) restait en deuxième ligne.
    if systeme:
        retenues = [r for r in retenues
                    if (r.get("star_system") or "").lower() == systeme.lower()]
    return {"resolution": res, "minerai": minerai, "lieu": lieu,
            "systeme": systeme,
            "raffineries": retenues[:6], "toutes": retenues,
            "total": len(retenues),
            "ailleurs": len(toutes) - len(retenues),
            "au_rendement": any(r.get("rendement") is not None
                                for r in retenues),
            "methodes": _methodes_de_raffinage(con)}


def _raffiner_croise(con: sqlite3.Connection, res, fiches: list[dict[str, Any]],
                     lieu: str | None,
                     systeme: str | None = None,
                     classement: str | None = None) -> dict[str, Any]:
    """Le croisement : une liste de raffineries, un rendement par minerai."""
    par_terminal: dict[str, dict[str, Any]] = {}
    for fiche in fiches:
        for raffinerie in _raffineries_pres(con, fiche["systemes"], lieu,
                                            fiche["nom"]):
            slot = par_terminal.setdefault(
                raffinerie["name"], {**raffinerie, "rendements": {}})
            if raffinerie.get("rendement") is not None:
                slot["rendements"][fiche["nom"]] = raffinerie["rendement"]

    # Le plus rare commande ; à rareté égale — Iron et Hephaestanite sont
    # tous deux communs — c'est le cumul des rendements qui classe, parce que
    # les deux chargements pèsent autant l'un que l'autre. « Sans prendre en
    # compte leur rareté » force le cumul — demande de l'utilisateur.
    cumul_demande = classement == "cumul"
    egalite = cumul_demande or len({f["palier"] for f in fiches}) == 1
    pilote = fiches[0]

    def rang(slot: dict[str, Any]) -> tuple:
        rends = slot["rendements"]
        cumul = sum(rends.values()) if rends else None
        if egalite:
            return (cumul is None, -(cumul or 0), -len(rends))
        du_pilote = rends.get(pilote["nom"])
        # Un terminal sans relevé n'est pas un terminal à zéro : il passe
        # après ceux qu'on sait mesurer.
        return (du_pilote is None, -(du_pilote or 0),
                -(cumul or 0))

    retenues = sorted(par_terminal.values(), key=rang)
    if systeme:
        retenues = [r for r in retenues
                    if (r.get("star_system") or "").lower() == systeme.lower()]
    return {"resolution": res, "minerai": pilote, "croises": fiches,
            "egalite_rarete": egalite, "cumul_demande": cumul_demande,
            "lieu": lieu, "systeme": systeme,
            "raffineries": retenues[:6], "toutes": retenues,
            "total": len(retenues),
            "ailleurs": 0,
            "au_rendement": any(s["rendements"] for s in retenues),
            "methodes": _methodes_de_raffinage(con)}


def conseil_de_raffinage(con: sqlite3.Connection, query: str, *,
                         lieu: str | None = None) -> dict[str, Any]:
    """Où raffiner ce qu'il faut pour fabriquer un objet.

    Règle donnée par l'utilisateur, et c'est elle qui fait tout l'intérêt :
    **c'est le minerai le plus rare qui commande le choix**. Une recette qui
    demande du Stileron (légendaire) et du Fer (commun) s'organise autour du
    Stileron — le fer se trouve partout, se déplacer pour lui n'a pas de sens.
    Et on **dit** lequel commande, sinon le conseil a l'air arbitraire.
    """
    # Import différé : `queries` réimporte ce module, un import en tête
    # fabriquerait un cycle. Même motif que `_ships_nommes` avec le routeur et
    # `get_blueprint` avec le rendu — le conseil part d'une recette, la recette
    # ne sait rien du raffinage, et la dépendance ne va donc que dans un sens.
    from .queries import get_blueprint

    blueprint = get_blueprint(con, query)
    minerais: list[dict[str, Any]] = []
    vus: set[str] = set()
    for tier in blueprint.get("tiers") or []:
        for groupe in tier.get("groups") or []:
            for option in groupe.get("options") or []:
                nom = option.get("ref_name")
                if not nom or nom in vus:
                    continue
                vus.add(nom)
                if (fiche := _minerai(con, nom)):
                    minerais.append(fiche)

    minerais.sort(key=lambda m: (-m["palier"], m["nom"]))
    for fiche in minerais:
        fiche["mesure"] = bool(_row(
            con, "SELECT 1 FROM refinery_yields WHERE commodity = ? "
                 "COLLATE NOCASE LIMIT 1", fiche["nom"]))
    pilote = minerais[0] if minerais else None
    # **Le plus rare commande — mais on ne peut classer que ce qui est
    # mesuré.** Le Stileron est légendaire et n'a aucun rendement relevé : s'y
    # tenir rendrait un classement purement géographique alors que les deux
    # autres minerais de la recette, eux, sont mesurés. On garde donc le plus
    # rare comme sujet, et on classe sur le plus rare **mesuré**, en disant
    # que ce n'est pas le même.
    mesures = [m for m in minerais if m["mesure"]]
    classe_sur = mesures[0] if mesures else pilote

    systemes = (classe_sur or {}).get("systemes") or {}
    nom = (classe_sur or {}).get("nom")
    retenues = _raffineries_pres(con, systemes, lieu, nom)
    toutes = _raffineries_pres(con, {}, lieu, nom)
    return {"blueprint": blueprint, "minerais": minerais, "pilote": pilote,
            "classe_sur": classe_sur,
            "lieu": lieu, "raffineries": retenues[:6], "total": len(retenues),
            "ailleurs": len(toutes) - len(retenues),
            "au_rendement": any(r.get("rendement") is not None
                                for r in retenues),
            "methodes": _methodes_de_raffinage(con)}


# ------------------------------------------------------------ où miner, groupé

def _lieux_du_minerai(con: sqlite3.Connection, nom: str) -> list[dict[str, Any]]:
    """Les lieux d'extraction d'un minerai, tous gisements confondus."""
    lignes = [dict(r) for r in con.execute(
        "SELECT uuid, name FROM resources WHERE name LIKE 'MineableRock%'")]
    uuids = [l["uuid"] for l in lignes
             if (t := _GABARIT_MINERAI.match(l["name"]))
             and t.group(2).lower() == nom.lower()]
    if not uuids:
        return []
    trous = ",".join("?" * len(uuids))
    return [dict(r) for r in con.execute(
        f"SELECT l.name, l.system, l.loc_type, "
        f"       s.parent_name, s.parent_type, s.type_name "
        f"  FROM resource_locations rl "
        f"  JOIN locations l ON l.uuid = rl.location_uuid "
        f"  LEFT JOIN starmap s ON s.name = l.name AND s.system_name IS NOT NULL "
        f" WHERE rl.resource_uuid IN ({trous}) "
        f" GROUP BY l.uuid", uuids)]


def ou_miner_pour(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Où miner pour fabriquer un P6-LR ? » — un plan, pas trois listes.

    Remarque de l'utilisateur : trois blocs de gisements séparés obligent le
    joueur à faire l'interpolation lui-même. Ce qu'il veut, c'est le **coin**
    — « va du côté de microTech, tout s'y trouve » — puis le détail. L'unité
    de regroupement est le **système planétaire** : une planète et ses lunes,
    parce que c'est l'échelle d'un déplacement de minage. Le plus rare
    commande le classement des coins, comme pour le raffinage.
    """
    from .queries import get_blueprint

    blueprint = get_blueprint(con, query)
    minerais: list[dict[str, Any]] = []
    vus: set[str] = set()
    for tier in blueprint.get("tiers") or []:
        for groupe in tier.get("groups") or []:
            for option in groupe.get("options") or []:
                nom = option.get("ref_name")
                if not nom or nom in vus:
                    continue
                vus.add(nom)
                if (fiche := _minerai(con, nom)):
                    minerais.append(fiche)
    if not minerais:
        raise NotFound(f"aucun minerai à extraire dans la recette de {query}")
    plan = plan_de_minage(con, minerais)
    plan["sortie"] = ((blueprint.get("blueprint") or {}).get("output_name")
                      or query)
    plan["resolution"] = blueprint.get("resolution")
    return plan


def plan_de_minage(con: sqlite3.Connection, minerais: list[dict[str, Any]],
                   systeme_filtre: str | None = None) -> dict[str, Any]:
    """Le croisement des coins pour une liste de minerais — le cœur du plan.

    Factorisé hors de la recette : « où miner de l'iron et de
    l'héphaestanite » pose la même question sans blueprint, et le journal du
    2026-08-07 montrait la réponse rendue minerai par minerai — le joueur
    faisait l'interpolation lui-même.
    """
    minerais = sorted(minerais, key=lambda m: (-m["palier"], m["nom"]))

    # Un coin = (système, planète), peuplé du minerai qu'on y trouve et où.
    coins: dict[tuple[str, str], dict[str, list[str]]] = {}
    asteroides: dict[str, dict[str, int]] = {}
    sans_lieu: list[str] = []
    for fiche in minerais:
        lieux = _lieux_du_minerai(con, fiche["nom"])
        if systeme_filtre:
            lieux = [l for l in lieux
                     if (l.get("system") or "").lower() == systeme_filtre.lower()]
        if not lieux:
            sans_lieu.append(fiche["nom"])
            continue
        for lieu in lieux:
            systeme = lieu.get("system") or "?"
            if lieu.get("type_name") == "Moon" or lieu.get("loc_type") == "moon":
                ancre = (systeme, lieu.get("parent_name") or "?")
                label = lieu["name"]
            elif (lieu.get("type_name") == "Planet"
                  or lieu.get("loc_type") == "planet"):
                ancre = (systeme, lieu["name"])
                label = None  # la planète elle-même
            else:
                # Champ d'astéroïdes ou point de Lagrange : pas un système
                # planétaire, on le compte à part — c'est un complément, pas
                # un coin où tout faire.
                asteroides.setdefault(fiche["nom"], {})
                asteroides[fiche["nom"]][systeme] = (
                    asteroides[fiche["nom"]].get(systeme, 0) + 1)
                continue
            posé = coins.setdefault(ancre, {})
            labels = posé.setdefault(fiche["nom"], [])
            # Deux gisements distincts sur la même planète produisaient
            # « Terminus même et Terminus même » : le lieu se dédoublonne.
            if label is None:
                if ancre[1] not in labels:
                    labels.insert(0, ancre[1])
            elif label not in labels:
                labels.append(label)

    # Le plus rare commande : un coin sans le minerai le plus rare localisé
    # en surface passe derrière, quel que soit son compte.
    en_coin = {nom for parts in coins.values() for nom in parts}
    commande = next((m["nom"] for m in minerais if m["nom"] in en_coin), None)
    classement = sorted(
        coins.items(),
        key=lambda kv: (commande not in kv[1], -len(kv[1]),
                        -sum(len(v) for v in kv[1].values())))
    contrat = qualite_reponse(
        faits={
            "minerais": len(minerais),
            "coins": len(classement),
            "systeme": systeme_filtre,
            "minerai_qui_commande": commande,
        },
        manques=(f"gisement localisé absent pour {nom}"
                 for nom in sans_lieu),
        sources=("jeu",), fraicheur={"jeu": fraicheur_jeu(con)})
    return {"sortie": None,
            "minerais": minerais, "commande": commande,
            "systeme": systeme_filtre,
            "coins": [{"systeme": s, "planete": p, "minerais": parts}
                      for (s, p), parts in classement[:3]],
            "total_coins": len(classement),
            "asteroides": asteroides, "sans_lieu": sans_lieu,
            "resolution": None, "complet": contrat["complet"],
            "qualite_reponse": contrat}
