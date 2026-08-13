"""Se déplacer dans l'univers — lieux, distances, trajets.

Tout ce qui répond à « c'est loin ? », « je peux y aller ? » et « qu'est-ce
qu'il y a à côté ? ». C'est le bloc le plus autonome de l'ancien `queries.py`
et le plus dense en règles : les coordonnées sont relatives à l'étoile de
chaque système, la portée quantique se **calcule** au lieu de se lire, et deux
règles de terrain données par l'utilisateur — Pyro est sans loi, franchir un
saut ne remplit pas le réservoir — n'existent dans aucune colonne.

Les pièges qui ont coûté le plus cher, et qui sont tous ici :

- `0.0 or défaut` vaut le défaut. Une distance **nulle** — le cas co-localisé,
  exactement celui qu'on cherche — devenait l'infini. Tester `is None`.
- `WHERE name = ? LIMIT 1` sur le starmap est piégeux : « Pyro Gateway »
  existe dans Stanton **et** dans Nyx.
- Il n'existe pas de saut direct entre tous les systèmes. Stanton et Nyx ne
  sont pas voisins, la route passe par Pyro — d'où le parcours en largeur.
"""

from __future__ import annotations

import math
import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .normalize import normalize
from .resolver import resolve


# ---------------------------- Trajets, sauts, escales et carburant quantique

# ------------------------------------------------------------ 12. distances

# Une unité de distance de Star Citizen est le mètre. Les ordres de grandeur
# sont tels que le mètre ne se dit pas : Yela est à 20 millions de kilomètres
# de Lorville. On parle donc en Gm, puis en km.
def _ecart(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Distance entre deux lieux **du même système**, en mètres."""
    if a is None or b is None or None in (a.get("x"), b.get("x")):
        return None
    return math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))


def _quantum_drive_monte(con: sqlite3.Connection, ship_uuid: str) -> dict[str, Any] | None:
    """Le moteur quantique d'origine, ou None si le vaisseau n'en a pas.

    Le Mirai Fury n'a aucun port `qdrive` : il ne saute pas, et ce n'est pas
    une lacune d'extraction. La réponse doit le dire.
    """
    return _dict(_row(con, """
        SELECT DISTINCT i.name, i.size, st.qt_fuel_rate, st.qt_drive_speed
        FROM hardpoints h
        JOIN items i ON i.name = h.installed_name AND i.type = 'QuantumDrive'
        JOIN item_stats st ON st.item_uuid = i.uuid
        WHERE h.ship_uuid = ? AND h.category = 'qdrive'
          AND st.qt_fuel_rate IS NOT NULL
        LIMIT 1""", ship_uuid))


def _jump_drive_monte(con: sqlite3.Connection, ship_uuid: str) -> dict[str, Any] | None:
    """Le **jump drive**, à ne pas confondre avec le moteur quantique.

    Deux objets distincts dans les fichiers, et deux fonctions distinctes dans
    le jeu : le moteur quantique fait les sauts **dans** un système, le jump
    drive franchit les **points de saut** entre systèmes. Ils se montent dans
    le même port `qdrive`, ce qui rend la confusion facile — c'est
    `items.type` qui les sépare, `QuantumDrive` d'un côté, `JumpDrive` de
    l'autre. Mesuré : 12 jump drives au catalogue, 251 vaisseaux sur 316 en
    portent un.
    """
    return _dict(_row(con, """
        SELECT DISTINCT i.name, i.size FROM hardpoints h
        JOIN items i ON i.name = h.installed_name AND i.type = 'JumpDrive'
        WHERE h.ship_uuid = ? LIMIT 1""", ship_uuid))


def _drive_nomme(con: sqlite3.Connection, nom: str) -> dict[str, Any] | None:
    res = resolve(con, nom, entity_types=("item",), limit=6)
    for candidat in res.candidates:
        ligne = _dict(_row(con, """
            SELECT i.name, i.size, st.qt_fuel_rate, st.qt_drive_speed
            FROM items i JOIN item_stats st ON st.item_uuid = i.uuid
            WHERE i.uuid = ? AND i.type = 'QuantumDrive'
              AND st.qt_fuel_rate IS NOT NULL""", candidat.entity_id))
        if ligne is not None:
            return ligne
    return None


def _point_de_saut(con: sqlite3.Connection, depuis: str, vers: str) -> dict[str, Any] | None:
    """Le point de saut du système `depuis` vers le système `vers`.

    Les noms amont ne sont pas normalisés — « Stanton-Pyro Jump Point » et
    « Nyx - Pyro Jump Point ». On cherche donc les deux noms de système dans
    le libellé plutôt qu'un format précis.

    **Le saut Stanton ↔ Nyx n'a aucune entrée « Jump Point » nommée** —
    signalé par l'utilisateur le 2026-08-12, vérifié dans le starmap : le
    tunnel n'y figure que par ses quais, « Nyx Gateway » dans Stanton et
    « Stanton Gateway » dans Nyx. Le repli est la règle déjà écrite « les
    Gateways sont à zéro mètre des sauts » : la station nommée d'après la
    destination tient lieu de point de saut, position comprise.
    """
    for ligne in con.execute(
        "SELECT * FROM starmap WHERE system_name = ? AND x IS NOT NULL "
        "AND name LIKE '%Jump Point%' AND name NOT LIKE '%Wreck%'", (depuis,)
    ):
        nom = normalize(ligne["name"])
        if normalize(vers) in nom and normalize(depuis) in nom:
            return dict(ligne)
    quai = _row(con,
                "SELECT * FROM starmap WHERE system_name = ? "
                "AND x IS NOT NULL AND type_name = 'Manmade' "
                "AND LOWER(name) = LOWER(?)", depuis, f"{vers} Gateway")
    return _dict(quai) if quai else None


def route_de_systemes(con: sqlite3.Connection, depuis: str,
                      vers: str) -> list[str] | None:
    """La suite de systèmes à traverser, sauts compris.

    **Il n'existe pas de saut direct entre tous les systèmes**, et le
    planificateur n'en franchissait qu'un seul : « d'Orison à Nyx » répondait
    « je n'ai pas de point de saut Stanton → Nyx », ce qui est exact et
    inutile — la route passe par Pyro. Remarque du journal, trois fois.

    Le graphe est **non orienté** : la base ne porte que `Nyx → Pyro` et
    `Pyro → Stanton`, mais on franchit un point de saut dans les deux sens.

    Parcours en largeur : avec trois systèmes reliés, le plus court chemin est
    le seul raisonnable, et il le restera tant que la galaxie ouverte tiendra
    dans une poignée de nœuds.
    """
    if not depuis or not vers:
        return None
    if depuis == vers:
        return [depuis]

    voisins: dict[str, set[str]] = {}
    for entree, sortie in con.execute(
            "SELECT entry_system, exit_system FROM jump_points "
            "WHERE entry_system IS NOT NULL AND exit_system IS NOT NULL"):
        voisins.setdefault(entree, set()).add(sortie)
        voisins.setdefault(sortie, set()).add(entree)

    file = [[depuis]]
    vus = {depuis}
    while file:
        chemin = file.pop(0)
        for voisin in sorted(voisins.get(chemin[-1], ())):
            if voisin == vers:
                return chemin + [voisin]
            if voisin not in vus:
                vus.add(voisin)
                file.append(chemin + [voisin])
    return None


def drive_nomme_dans(con: sqlite3.Connection, question: str) -> str | None:
    """Le moteur quantique nommé dans la question, s'il y en a un.

    « Avec un Gladius équipé d'un Atlas » : le moteur se reconnaît au fait
    qu'il **est** un moteur, pas à une tournure. On exige un score franc —
    n'importe quel mot finit par ressembler à quelque chose.
    """
    from .router.deterministic import _ngrams

    for gram in _ngrams(question):
        if len(gram.replace(" ", "")) < 4:
            continue
        res = resolve(con, gram, entity_types=("item",), limit=3)
        for candidat in res.candidates:
            if candidat.score < 90:
                continue
            if _row(con, "SELECT 1 FROM items i JOIN item_stats st "
                         "ON st.item_uuid = i.uuid WHERE i.uuid = ? "
                         "AND i.type = 'QuantumDrive' AND st.qt_fuel_rate IS NOT NULL",
                    candidat.entity_id):
                return candidat.name
    return None


# Systèmes où s'arrêter est risqué. Les fichiers du jeu ne codent pas la
# dangerosité ; c'était donc une règle de terrain donnée par l'utilisateur —
# Pyro est sans loi — et un ensemble figé, qui aurait ignoré tout système
# ajouté par un patch. Le wiki, lui, publie l'autorité de chaque système :
# UEE, Vanduul, Xi'an, Banu, Developing, **Unclaimed**. Pyro et Nyx sont
# `Unclaimed`, ce qui recoupe exactement la règle donnée et l'étend au reste
# de la galaxie sans rien coder à la main. Le repli garde Pyro : sans la table
# du wiki, la règle de l'utilisateur vaut mieux que rien.
SYSTEMES_RISQUES = {"pyro"}

# Une autorité qui ne protège personne. « Developing » n'y est pas : un système
# en cours de colonisation a une présence UEE, il n'est pas sans loi.
_AUTORITES_ABSENTES = ("Unclaimed",)


def systemes_risques(con: sqlite3.Connection) -> set[str]:
    """Les systèmes sans autorité, en minuscules. Mesuré, pas codé en dur."""
    try:
        lignes = con.execute(
            "SELECT name FROM wiki_systems WHERE affiliation IN "
            f"({','.join('?' * len(_AUTORITES_ABSENTES))})",
            _AUTORITES_ABSENTES).fetchall()
    except sqlite3.OperationalError:
        return SYSTEMES_RISQUES
    trouves = {(l[0] or "").lower() for l in lignes if l[0]}
    # Une table présente mais vide ne doit pas rendre Pyro subitement sûr.
    return trouves | SYSTEMES_RISQUES

# Types de lieux où l'on peut se poser et refaire le plein. Un avant-poste de
# surface n'a pas de quoi ravitailler un vaisseau ; une station et une zone
# d'atterrissage, oui.
_TYPES_RAVITAILLEMENT = ("Manmade", "Manmade_VisibleOnInteraction", "LandingZone")


def _ravitaillements(con: sqlite3.Connection, systeme: str) -> list[dict[str, Any]]:
    marques = ",".join("?" * len(_TYPES_RAVITAILLEMENT))
    return [dict(r) for r in con.execute(
        f"SELECT name, x, y, z, system_name, type_name FROM starmap "
        f"WHERE system_name = ? AND x IS NOT NULL AND name IS NOT NULL "
        f"  AND type_name IN ({marques})",
        (systeme, *_TYPES_RAVITAILLEMENT))]


def _station_du_saut(con: sqlite3.Connection, saut: dict[str, Any]
                     ) -> dict[str, Any] | None:
    """La station posée sur le point de saut, s'il y en a une.

    Mesuré : Pyro Gateway est à **zéro mètre** du Stanton-Pyro Jump Point, et
    Stanton Gateway à zéro du Pyro-Stanton. On s'y arrête de toute façon pour
    franchir le saut, donc le plein qu'on y fait ne coûte pas un arrêt de plus.
    Ne pas en tenir compte revenait à compter deux arrêts là où il y en a un.
    """
    for ligne in _ravitaillements(con, saut.get("system_name")):
        ecart = _ecart(saut, ligne)
        if ecart is not None and ecart < 1e7:      # 10 000 km : au pied du saut
            return ligne
    return None


def _station_en_orbite(con: sqlite3.Connection, planete: dict[str, Any]
                       ) -> dict[str, Any] | None:
    """La station de services directement rattachée à une planète.

    Le starmap aplati classe stations, relais et comm arrays sous `Manmade`.
    La relation parentale seule donnerait donc plusieurs faux choix. Une vraie
    station de services est en plus reliée à une boutique du jeu ou à un
    terminal UEX. Ce croisement retrouve les quatre stations orbitales de
    Stanton et Orbituary/Ruin Station dans Pyro, sans liste de noms codée.
    """
    if not planete or planete.get("type_name") != "Planet":
        return None
    return _dict(_row(con, """
        SELECT s.* FROM starmap s
        WHERE s.parent_uuid = ? AND s.type_name = 'Manmade'
          AND s.name IS NOT NULL
          AND (
            EXISTS (SELECT 1 FROM shops sh
                    WHERE sh.starmap_object_uuid = s.uuid)
            OR EXISTS (
                SELECT 1 FROM uex_commodity_prices u
                WHERE u.star_system = s.system_name
                  AND instr(lower(u.terminal),
                            lower(replace(s.name, ' Station', ''))) > 0
            )
          )
        ORDER BY
          EXISTS (SELECT 1 FROM shops sh
                  WHERE sh.starmap_object_uuid = s.uuid) DESC,
          s.name
        LIMIT 1""", planete["uuid"]))


def _etape(con: sqlite3.Connection, a: dict[str, Any],
           b: dict[str, Any]) -> dict[str, Any]:
    """Un segment, enrichi de l'alternative orbitale à son arrivée."""
    etape = {"de": a["name"], "a": b["name"], "metres": _ecart(a, b),
             "de_obj": a, "a_obj": b}
    station = _station_en_orbite(con, b)
    if station is not None:
        etape["station_orbite"] = station["name"]
    return etape


def _escales(con: sqlite3.Connection, depart: dict[str, Any],
             arrivee: dict[str, Any], portee: float,
             reserve: float | None = None) -> tuple[list[dict[str, Any]], float] | None:
    """Les arrêts nécessaires pour couvrir une étape, et ce qui reste après.

    Problème classique de la station-service : pour **minimiser le nombre
    d'arrêts**, on va à chaque fois le plus loin possible. L'algorithme glouton
    est optimal pour ce critère, et c'est le bon critère — se poser coûte du
    temps, et un plein de moins vaut mieux qu'un plein plus court.

    `reserve` est le carburant restant en arrivant, exprimé en mètres
    parcourables. Il ne se remet pas à plein tout seul : franchir un point de
    saut ne remplit pas le réservoir, il faut se poser pour ça — et se poser
    coûte du temps, donc on ne le fait que si c'est nécessaire.

    À portée égale on préfère le système sûr : deux arrêts qui font également
    l'affaire ne se valent pas si l'un est à Pyro.
    """
    candidats = _ravitaillements(con, depart.get("system_name"))
    risques = systemes_risques(con)
    position, escales = depart, []
    jauge = portee if reserve is None else reserve

    # On est peut-être **déjà** sur une station : au sortir d'un point de saut,
    # le quai est là. Se ravitailler sur place ne coûte aucun détour, et c'est
    # le meilleur endroit où le faire quand il faut le faire. Sans ce cas, le
    # planificateur repartait le réservoir presque vide et s'imposait deux
    # arrêts de plus en chemin.
    debut = _ecart(depart, arrivee)
    if debut is not None and debut > jauge and jauge < portee:
        # Le test doit être explicite sur `None` : une distance **nulle** —
        # le cas co-localisé, exactement celui qu'on cherche — est fausse au
        # sens booléen, et `_ecart(...) or 1e12` la transformait en infini.
        sur_place = next(
            (c for c in candidats
             if (lambda e: e is not None and e < 1e7)(_ecart(depart, c))), None)
        if sur_place is not None:
            escales.append(sur_place)
            jauge = portee

    for _ in range(12):                      # borne dure contre une boucle
        reste = _ecart(position, arrivee)
        if reste is None:
            return None
        if reste <= jauge:
            return escales, jauge - reste
        # Même précaution qu'au-dessus : `or` avale un zéro légitime, et une
        # distance manquante n'est pas une distance nulle.
        atteignables = []
        for c in candidats:
            aller, fin = _ecart(position, c), _ecart(c, arrivee)
            if aller is None or fin is None or aller > jauge:
                continue
            if c["name"] == position.get("name") or fin >= reste:
                continue
            atteignables.append((c, fin))
        if not atteignables:
            return None
        # Le plus proche de l'arrivée d'abord ; à distance comparable, le
        # système sûr. La marge de 5 % évite de préférer un détour dangereux
        # pour quelques milliers de kilomètres.
        meilleur = min(a[1] for a in atteignables)
        atteignables.sort(key=lambda a: (
            a[1] > meilleur * 1.05,
            (a[0].get("system_name") or "").lower() in risques,
            a[1],
        ))
        etape = atteignables[0][0]
        escales.append(etape)
        position, jauge = etape, portee     # on se pose, donc on fait le plein
    return None


def _drive_qui_suffirait(con: sqlite3.Connection, vaisseau: dict[str, Any],
                         actuel: dict[str, Any] | None,
                         besoin_m: float) -> dict[str, Any] | None:
    """Le plus modeste moteur quantique qui couvrirait `besoin_m` d'un trait.

    Conseiller un moteur n'est pas inventer une donnée : la portée se calcule
    exactement, donc on peut dire lequel supprime un arrêt. On se limite à la
    **taille du port** occupé — proposer un S3 sur un vaisseau qui n'a qu'un
    S1 serait un conseil inapplicable.
    """
    carburant = (vaisseau or {}).get("quantum_fuel")
    if not carburant or not besoin_m:
        return None
    taux_max = carburant / besoin_m * 1_000_000
    taille = (actuel or {}).get("size")
    lignes = con.execute(
        "SELECT i.name, i.size, st.qt_fuel_rate FROM items i "
        "JOIN item_stats st ON st.item_uuid = i.uuid "
        "WHERE i.type = 'QuantumDrive' AND st.qt_fuel_rate IS NOT NULL "
        "  AND st.qt_fuel_rate <= ? " + ("AND i.size = ? " if taille else "")
        + "AND i.name IS NOT NULL AND i.name NOT LIKE '%PLACEHOLDER%' "
        "ORDER BY st.qt_fuel_rate DESC LIMIT 1",
        (taux_max, taille) if taille else (taux_max,),
    ).fetchone()
    if lignes is None:
        return None
    propose = dict(lignes)
    if actuel and propose["name"] == actuel.get("name"):
        return None
    propose["portee"] = carburant / propose["qt_fuel_rate"] * 1_000_000
    return propose


def _vaisseau_nomme(con: sqlite3.Connection, ship: str | None) -> dict[str, Any] | None:
    """Le vaisseau nommé, s'il l'est. Sert au cas « il manque le départ »."""
    if not ship:
        return None
    trouve = resolve(con, ship, entity_types=("ship",)).best
    if trouve is None:
        return None
    return _dict(_row(con, "SELECT * FROM ships WHERE uuid = ?", trouve.entity_id))


def _etapes_entre(con: sqlite3.Connection, a: dict[str, Any],
                  b: dict[str, Any]
                  ) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Les étapes d'un trajet A → B — direct dans un système, par les
    quais des points de saut sinon. Factorisé pour s'enchaîner : un
    aller-retour ou une tournée est une suite de ces segments."""
    etapes: list[dict[str, Any]] = []
    sans_detour: list[str] = []
    if a["system_name"] == b["system_name"]:
        return ([_etape(con, a, b)], [], None)
    # **Plusieurs sauts, s'il le faut.** Stanton et Nyx ne sont pas voisins
    # — on passe par Pyro. Le planificateur n'en franchissait qu'un et
    # déclarait le trajet impossible.
    route = route_de_systemes(con, a["system_name"], b["system_name"])
    if route is None or len(route) < 2:
        return [], [], f"{a['system_name']} → {b['system_name']}"
    position = a
    for depart_systeme, arrivee_systeme in zip(route, route[1:]):
        sortie = _point_de_saut(con, depart_systeme, arrivee_systeme)
        entree = _point_de_saut(con, arrivee_systeme, depart_systeme)
        if sortie is None or entree is None:
            return [], [], f"{depart_systeme} → {arrivee_systeme}"
        # La station du saut remplace le saut lui-même comme point
        # d'arrêt : on s'y pose pour franchir.
        quai_sortie = _station_du_saut(con, sortie) or sortie
        quai_entree = _station_du_saut(con, entree) or entree
        etapes.append(_etape(con, position, quai_sortie))
        # Ces stations sont *disponibles* au pied du saut : s'y ravitailler
        # ne coûte aucun détour. Mais s'y poser coûte du temps comme
        # ailleurs — ce n'est pas un plein gratuit, c'est le meilleur
        # endroit où le faire **si** on doit le faire.
        sans_detour += [q["name"] for q in (quai_sortie, quai_entree)
                        if q.get("type_name") in _TYPES_RAVITAILLEMENT]
        position = quai_entree
    etapes.append(_etape(con, position, b))
    return etapes, sans_detour, None


def peut_voyager(con: sqlite3.Connection, query: str, *, to: str | None = None,
                 ship: str | None = None, drive: str | None = None,
                 carburant_pct: float | None = None,
                 vias: list[str] | None = None,
                 aller_retour: bool = False,
                 tours: int = 1) -> dict[str, Any]:
    """« Je peux aller de microTech à Ruin Station dans un Gladius ? »

    La portée quantique n'est pas lue, elle se **calcule** — et la relation est
    exacte, vérifiée sur la base au dixième de pourcent près :

        portée en mètres = carburant quantique ÷ consommation × 1 000 000

    C'est ce qui permet de répondre « et avec un Atlas ? » : on remplace le
    taux du moteur d'origine par celui du moteur nommé. Aucun chiffre n'est
    inventé — les deux termes viennent de la base, la division est de nous.
    """
    # **Un départ manquant est une question, pas un échec.** « Je peux aller
    # avec un Gladius jusque Orbituary » nomme la destination et le vaisseau,
    # jamais le point de départ — et la réponse en dépend entièrement. Deux
    # remarques du journal disent la même chose : « la question ça devrait être
    # d'où pars-tu ». On rend donc ce qu'on a reconnu, en le disant, plutôt que
    # de lever et de laisser le joueur devant un silence.
    if not query and to:
        res_b = resolve(con, to, entity_types=("starmap",))
        if res_b.best is None:
            raise NotFound(to, res_b)
        b = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?",
                       res_b.best.entity_id))
        return {"depart_manquant": True, "from": None, "to": b,
                "ship": _vaisseau_nomme(con, ship), "resolution": res_b,
                "etapes": [], "escales": [], "sans_detour": [],
                # La tournée et l'aller-retour survivent à la question du
                # départ : la suite les garde, le complément les rejoue.
                "vias": list(vias or []), "aller_retour": aller_retour,
                "tours": tours, "manque_saut": None}

    depart = resolve(con, query, entity_types=("starmap",))
    if depart.best is None:
        raise NotFound(query, depart)
    a = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?", depart.best.entity_id))
    b = None
    if to:
        res_b = resolve(con, to, entity_types=("starmap",))
        if res_b.best is not None:
            b = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?", res_b.best.entity_id))
    if a is None or b is None:
        raise NotFound(to or query)

    # Un trajet dont les deux extrémités désignent le même objet est valide,
    # mais ne comporte aucun segment. Le laisser suivre le calcul normal
    # produisait ``metres=None`` puis faisait tomber le rendu au moment de
    # formater la distance. Répondre explicitement évite aussi de faire croire
    # qu'un moteur ou du carburant sont nécessaires pour rester sur place.
    if a["uuid"] == b["uuid"]:
        return {
            "from": a, "to": b, "ship": _vaisseau_nomme(con, ship),
            "deja_sur_place": True, "etapes": [], "metres": 0,
            "escales": [], "sans_detour": [], "vias": [],
            "aller_retour": aller_retour, "tours": max(1, int(tours)),
            "manque_saut": None, "resolution": depart,
        }

    # Le trajet : direct dans un système, par point de saut sinon. Les
    # coordonnées sont relatives à l'étoile de chaque système, donc les
    # soustraire d'un système à l'autre donnerait un nombre faux.
    # **Le trajet peut enchaîner des points** : un aller-retour est A→B→A,
    # une tournée A→B→C→D — remarques du journal du 2026-08-08, quatre
    # questions restées sans vraie réponse. La jauge suit d'un bout à
    # l'autre, chaque arrivée est une occasion de plein comme avant.
    points = [a]
    vias_resolus: list[dict[str, Any]] = []
    for terme in vias or []:
        res_v = resolve(con, terme, entity_types=("starmap",))
        if res_v.best is not None:
            obj = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?",
                             res_v.best.entity_id))
            if obj and obj["name"] not in (p["name"] for p in points):
                points.append(obj)
                vias_resolus.append(obj)
    if b["name"] not in (p["name"] for p in points):
        points.append(b)
    if aller_retour and len(points) > 1:
        # Le retour repasse par les mêmes étapes, en miroir.
        boucle = points + list(reversed(points[:-1]))
        # **« 3 aller-retours » n'est pas « un aller-retour ».** Le
        # multiplicateur n'était pas lu : la réponse portait sur un seul
        # circuit et sortait donc **fausse en ayant l'air complète**, ce qui
        # est le pire cas. Le circuit se répète, départ compris, et la jauge
        # continue de courir d'un bout à l'autre — c'est bien la question
        # posée : « est-ce que je tiens trois fois sans refaire le plein ».
        points = boucle
        for _ in range(max(1, int(tours)) - 1):
            points = points + boucle[1:]

    etapes, sans_detour, manque_saut = [], [], None
    for de_pt, a_pt in zip(points, points[1:]):
        seg, sd, ms = _etapes_entre(con, de_pt, a_pt)
        if ms:
            manque_saut, etapes = ms, []
            break
        etapes += seg
        sans_detour += sd

    total = sum(e["metres"] for e in etapes if e["metres"] is not None) if etapes else None

    # **L'ordre cité est le sien ; le meilleur se propose.** « Crusader,
    # microTech, ArcCorp, Hurston » : Crusader premier est le départ, et la
    # remarque du journal demande de dire si un autre ordre coûte moins.
    # ≤ 4 détours = ≤ 24 permutations, la force brute est exacte.
    meilleur_ordre = None
    if vias_resolus and not aller_retour and total:
        import itertools
        fin = points[len(vias_resolus) + 1] if len(points) > len(vias_resolus) + 1 else None
        candidats = vias_resolus + ([fin] if fin is not None else [])
        meilleur, meilleur_total = None, total
        for perm in itertools.permutations(candidats):
            t, ok = 0.0, True
            for p1, p2 in zip([a] + list(perm), list(perm)):
                seg, _, ms = _etapes_entre(con, p1, p2)
                if ms:
                    ok = False
                    break
                t += sum(e["metres"] for e in seg if e["metres"] is not None)
            if ok and t < meilleur_total * 0.99:
                meilleur, meilleur_total = perm, t
        if meilleur is not None:
            meilleur_ordre = {"noms": [p["name"] for p in meilleur],
                              "total": meilleur_total}

    vaisseau = moteur = None
    if ship:
        res_s = resolve(con, ship, entity_types=("ship",))
        if res_s.best is not None:
            vaisseau = _dict(_row(con, "SELECT * FROM ships WHERE uuid = ?",
                                  res_s.best.entity_id))
    saut_drive = None
    if vaisseau is not None:
        moteur = (_drive_nomme(con, drive) if drive
                  else _quantum_drive_monte(con, vaisseau["uuid"]))
        saut_drive = _jump_drive_monte(con, vaisseau["uuid"])

    portee = None
    if vaisseau and moteur and vaisseau.get("quantum_fuel") and moteur.get("qt_fuel_rate"):
        portee = vaisseau["quantum_fuel"] / moteur["qt_fuel_rate"] * 1_000_000
    elif vaisseau and not drive:
        portee = vaisseau.get("qt_range")

    # La plus longue étape décide : on refait le plein de chaque côté d'un
    # point de saut, ce n'est pas le total qu'il faut couvrir d'un trait.
    plus_longue = max((e["metres"] for e in etapes if e["metres"] is not None),
                      default=None)

    # Sans moteur quantique, c'est non. Avec, c'est oui — la seule question
    # est combien de pleins. Répondre « non » à un trajet qui demande une
    # escale était faux : le joueur y va, il s'arrête en route.
    possible = None
    escales_par_etape: list[list[dict[str, Any]]] = []
    jauge = portee
    plein_avant_saut: str | None = None
    plein_au_depart: str | None = None

    def _planifier(depart_jauge: float) -> tuple[bool, list, float, str | None]:
        """Le trajet depuis une jauge donnée — la boucle d'escales."""
        ok, jauge_locale, plein_local = True, depart_jauge, None
        par_etape: list[list[dict[str, Any]]] = []
        for index, etape in enumerate(etapes):
            depuis, arrivee_etape = etape.get("de_obj"), etape.get("a_obj")
            if arrivee_etape is None or depuis is None:
                par_etape.append([])
                continue
            trouvees = _escales(con, depuis, arrivee_etape, portee, jauge_locale)
            if trouvees is None:
                ok = False
                par_etape.append([])
                continue
            arrets, jauge_locale = trouvees
            par_etape.append(arrets)

            # On fait le plein **avant** de franchir, pas après : le quai de
            # départ est dans le système d'origine, celui d'arrivée est déjà
            # de l'autre côté. Sur Stanton → Pyro, ça revient à remplir en
            # zone sûre et à entrer dans le système sans loi réservoir plein —
            # ce qui supprime souvent l'arrêt qu'on y aurait fait.
            saut_suivant = index + 1 < len(etapes)
            if saut_suivant and jauge_locale < portee:
                quai = arrivee_etape
                if (quai.get("type_name") in _TYPES_RAVITAILLEMENT
                        and (etapes[index + 1]["metres"] or 0) > jauge_locale):
                    plein_local = quai["name"]
                    jauge_locale = portee
        return ok, par_etape, jauge_locale, plein_local

    carburant_insuffisant = False
    if portee and plus_longue is not None:
        # « J'ai 13 % de carburant » : la jauge part de là, pas du plein —
        # remarque de l'utilisateur, le trajet répondait réservoir plein sans
        # un mot sur l'état réel.
        depart_jauge = portee
        if carburant_pct is not None:
            depart_jauge = portee * max(0.0, min(100.0, carburant_pct)) / 100.0
        possible, escales_par_etape, jauge, plein_avant_saut = _planifier(
            depart_jauge)
        # **S'il est posé sur une station, le plein se fait avant de
        # décoller.** Deuxième remarque de l'utilisateur : le plan envoyait
        # faire le plein à Comm Array en route alors que Grim HEX, le point
        # de départ, ravitaille — remplir sur place ne coûte ni détour ni
        # posé de plus, c'est strictement mieux que toute escale.
        depart_ravitaille = a.get("type_name") in _TYPES_RAVITAILLEMENT
        arrets_prevus = sum(len(e) for e in escales_par_etape)
        if (carburant_pct is not None and carburant_pct < 100.0
                and depart_ravitaille and (not possible or arrets_prevus)):
            ok_plein, escales_pleines, jauge_pleine, plein_p = _planifier(portee)
            if ok_plein:
                possible = True
                plein_au_depart = a["name"]
                escales_par_etape, jauge = escales_pleines, jauge_pleine
                plein_avant_saut = plein_p
        elif not possible and carburant_pct is not None:
            # Le départ ne ravitaille pas et la jauge ne mène nulle part :
            # conseiller un plein qu'on ne peut pas y faire serait inventer.
            # On le dit — c'est un transfert de carburant ou rien.
            if _planifier(portee)[0]:
                carburant_insuffisant = True

    return {
        "from": a, "to": b, "ship": vaisseau, "drive": moteur,
        "etapes": etapes, "metres": total, "plus_longue": plus_longue,
        "portee": portee, "possible": possible, "manque_saut": manque_saut,
        "vias": [v["name"] for v in vias_resolus],
        "aller_retour": aller_retour, "tours": max(1, int(tours)),
        "meilleur_ordre": meilleur_ordre,
        "jump_drive": saut_drive,
        # Franchir un point de saut demande un jump drive ; circuler dans un
        # système demande un moteur quantique. Sans le premier, le trajet
        # inter-système est impossible même avec un réservoir plein.
        "saut_requis": len(etapes) > 1 or bool(manque_saut),
        "escales": escales_par_etape,
        "pleins": sum(len(e) for e in escales_par_etape),
        "sans_detour": sans_detour,
        "plein_avant_saut": plein_avant_saut,
        "carburant_pct": carburant_pct,
        "plein_au_depart": plein_au_depart,
        "carburant_insuffisant": carburant_insuffisant,
        # De quoi conseiller un moteur : la plus longue étape à couvrir d'un
        # trait pour n'avoir aucun arrêt.
        "conseil_drive": (_drive_qui_suffirait(con, vaisseau, moteur, plus_longue)
                          if (vaisseau and plus_longue and portee
                              and plus_longue > portee) else None),
        # Un arrêt dans un système sans loi se signale : la donnée ne le dit
        # pas, c'est une règle de terrain, mais elle change la décision.
        # Les stations de point de saut échappent à l'avertissement : elles
        # sont sûres des deux côtés, y compris à Pyro. Règle de terrain donnée
        # par l'utilisateur, comme la dangerosité elle-même.
        "escales_risquees": [e["name"] for etape in escales_par_etape
                             for e in etape
                             if (e.get("system_name") or "").lower()
                             in systemes_risques(con)
                             and e["name"] not in sans_detour],
        # Ce qui reste vraiment dans le réservoir à l'arrivée : la jauge suit
        # tout le trajet, elle ne se remet pas à plein au passage d'un saut.
        "restant_pct": (round(jauge / portee * 100, 1)
                        if portee and possible else None),
        "resolution": depart,
    }


# « Armor - Port Tressler », « Platinum Bay - Pyro Gateway (Stanton) » : le
# libellé UEX colle l'enseigne au lieu, et met parfois le système entre
# parenthèses. Le lieu est ce qui suit le dernier tiret.
_PARENTHESE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")


def lieu_du_terminal(con: sqlite3.Connection, terminal: str,
                     systeme: str | None = None) -> dict[str, Any] | None:
    """Le lieu où se trouve un terminal marchand, coordonnées comprises.

    UEX ne donne pas de position, seulement un libellé et un système. Le
    libellé porte pourtant le lieu — mesuré : **507 terminaux sur 507** se
    résolvent par le suffixe, dont 393 au mot près.

    Le système compte pour trancher : « Pyro Gateway (Stanton) » désigne la
    station *Pyro Gateway*, qui est **dans Stanton**, et sans ce filtre le
    résolveur rendait *Stanton Gateway*, qui est dans Pyro. Deux stations
    différentes, à un système d'écart.
    """
    if not terminal:
        return None
    bout = terminal.split(" - ")[-1].strip()
    entre_parentheses = _PARENTHESE.match(bout)
    if entre_parentheses:
        bout, systeme = entre_parentheses.group(1), entre_parentheses.group(2)

    res = resolve(con, bout, entity_types=("starmap",), limit=6)
    candidats = [c for c in res.candidates if c.score >= 85.0]
    if not candidats:
        return None
    lignes = []
    for candidat in candidats:
        ligne = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?",
                           candidat.entity_id))
        if ligne is not None and ligne.get("x") is not None:
            lignes.append(ligne)
    if not lignes:
        return None
    if systeme:
        dans_le_systeme = [l for l in lignes
                           if (l.get("system_name") or "").lower()
                           == systeme.strip().lower()]
        if dans_le_systeme:
            return dans_le_systeme[0]
    return lignes[0]


# ------------------ Situer un lieu, mesurer une distance, lister les voisins

def where_is_location(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Où se situe Grim HEX ? » — la chaîne parentale, jusqu'au système.

    Six questions du journal tombaient ici, et pas dans le silence : faute
    d'outil, « où se trouve Grim HEX » partait chez `where_to_find_resource`
    et répondait sur une commodité. La donnée était pourtant complète —
    1 957 lieux sur 2 054 déclarent un parent, 1 967 un système.

    On remonte les parents plutôt que de lire `system_name` seul : « il est
    dans Stanton » n'aide personne à le trouver. « Sur Aberdeen, une lune de
    Hurston » situe vraiment.
    """
    res = resolve(con, query, entity_types=("starmap",))
    if res.best is None:
        raise NotFound(query, res)
    lieu = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?", res.best.entity_id))
    if lieu is None:
        raise NotFound(query, res)

    # Borne dure sur la remontée : une boucle dans les données amont ne doit
    # pas devenir une boucle infinie dans le service.
    chaine: list[dict[str, Any]] = []
    vus = {lieu["uuid"]}
    courant = lieu
    while courant.get("parent_uuid") and len(chaine) < 8:
        parent = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?",
                            courant["parent_uuid"]))
        if parent is None or parent["uuid"] in vus:
            break
        vus.add(parent["uuid"])
        chaine.append(parent)
        courant = parent

    # **Les homonymes existent, et taire leur existence induit en erreur.**
    # Remarque du journal : « il n'y a pas qu'une seule station Wikelo à
    # Stanton ». Il y en a trois — Dasi, Selo et Kinga — et n'en nommer qu'une
    # laisse croire qu'on a la bonne. Le critère est le **préfixe commun** :
    # une station « Wikelo Emporium … » est une sœur, pas un homonyme lointain.
    prefixe = " ".join((lieu["name"] or "").split()[:2])
    freres = [dict(r) for r in con.execute(
        "SELECT name, type_name, parent_name, system_name FROM starmap "
        "WHERE name LIKE ? || '%' AND uuid <> ? AND name IS NOT NULL "
        "ORDER BY name LIMIT 8", (prefixe, lieu["uuid"]))] if prefixe else []

    return {"location": lieu, "chaine": chaine, "freres": freres,
            "resolution": res}


def distance_fr(metres: float) -> str:
    """« 74 000 km », pas « 74 milliers de kilomètres ».

    L'échelle « milliers de » venait d'un rendu destiné à l'oreille, où lire
    « soixante-quatorze mille » se dit mieux qu'un nombre à chiffres. À
    l'écrit, le chiffre exact est plus utile et plus court — on ne bascule en
    millions que quand il cesse d'être lisible.
    """
    if metres >= 1e9:
        return f"{metres / 1e9:.1f} millions de km".replace(".", ",")
    if metres >= 1000:
        return f"{metres / 1000:,.0f} km".replace(",", " ")
    return f"{metres:.0f} m"


def get_distance(con: sqlite3.Connection, query: str,
                 *, to: str | None = None) -> dict[str, Any]:
    """« C'est loin, Yela depuis Lorville ? »

    Les coordonnées sont **relatives à l'étoile de chaque système**. Deux
    lieux de systèmes différents n'ont donc pas de distance euclidienne
    commune : les soustraire donnerait un nombre, et ce nombre serait faux.
    On renvoie dans ce cas le trajet par point de saut, avec son coût en
    carburant, qui est la vraie réponse à « c'est loin ».
    """
    depart = resolve(con, query, entity_types=("starmap",))
    if depart.best is None:
        raise NotFound(query, depart)
    arrivee = resolve(con, to or "", entity_types=("starmap",)) if to else None
    if arrivee is None or arrivee.best is None:
        raise NotFound(to or query, arrivee)

    a = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?", depart.best.entity_id))
    b = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?", arrivee.best.entity_id))
    if a is None or b is None:
        raise NotFound(query)

    if a["system_name"] != b["system_name"]:
        saut = _dict(_row(
            con,
            "SELECT * FROM jump_points WHERE (entry_system = ? AND exit_system = ?) "
            "OR (entry_system = ? AND exit_system = ?)",
            a["system_name"], b["system_name"], b["system_name"], a["system_name"],
        ))
        return {"from": a, "to": b, "same_system": False,
                "jump": saut, "metres": None, "resolution": depart}

    if None in (a["x"], b["x"]):
        return {"from": a, "to": b, "same_system": True,
                "jump": None, "metres": None, "resolution": depart}

    metres = math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))
    return {"from": a, "to": b, "same_system": True, "jump": None,
            "metres": metres, "resolution": depart}


def nearest_locations(con: sqlite3.Connection, query: str,
                      *, limit: int = 5) -> dict[str, Any]:
    """« Qu'est-ce qu'il y a près de Daymar ? »

    Restreint au système : comparer des coordonnées de systèmes différents
    n'aurait aucun sens, elles ne partagent pas d'origine.
    """
    res = resolve(con, query, entity_types=("starmap",))
    if res.best is None:
        raise NotFound(query, res)
    centre = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?", res.best.entity_id))
    if centre is None or centre["x"] is None:
        raise NotFound(query, res)

    voisins = []
    for r in con.execute(
        "SELECT * FROM starmap WHERE system_name = ? AND uuid != ? "
        "AND x IS NOT NULL AND name IS NOT NULL AND qt_valid = 1",
        (centre["system_name"], centre["uuid"]),
    ):
        d = math.dist((centre["x"], centre["y"], centre["z"]), (r["x"], r["y"], r["z"]))
        voisins.append({**dict(r), "metres": d})

    voisins.sort(key=lambda v: v["metres"])
    if not voisins:
        raise NotFound(query, res)
    return {"centre": centre, "voisins": voisins[:limit], "resolution": res}
