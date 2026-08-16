"""Les lieux : starmap, positions, points de saut, commerce.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
import json
import pathlib
import sqlite3
from .source import read_json
from .socle import AliasCollector, PLACEHOLDER, _prose_utile


# ------------------------------------------------------------------ commerce

def load_starmap(con: sqlite3.Connection, root: pathlib.Path,
                 aliases: AliasCollector) -> int:
    """`starmap.json` : la hiérarchie des objets célestes, jamais ouverte au
    premier passage.

    Chaque objet pointe son parent ; on remonte la chaîne jusqu'à la racine pour
    savoir de quel système il relève. C'est ce qui permet de dire « à Pyro »
    sans le deviner d'après un nom de lieu.
    """
    path = root / "starmap.json"
    if not path.exists():
        return 0

    nodes = {}
    for entry in read_json(path):
        uuid = entry.get("UUID")
        if not uuid:
            continue
        name = entry.get("Name")
        if name in (PLACEHOLDER, "<= UNINITIALIZED =>"):
            name = None
        # `Description` est renseignée sur 2 032 lieux sur 2 054 et n'était
        # pas lue : « c'est quoi Grim HEX » n'avait aucune source alors que la
        # réponse est écrite par CIG dans ce fichier. `Jurisdiction` dit qui
        # tient l'endroit — « Rough & Ready » ou « XenoThreat » ne promettent
        # pas la même tranquillité que « UEE ».
        juridiction = entry.get("Jurisdiction")
        nodes[uuid] = {
            "uuid": uuid,
            "name": name,
            "parent": entry.get("ParentUUID"),
            "type": (entry.get("Type") or {}).get("Name"),
            # « <= UNINITIALIZED => » n'est pas une description : 80 lieux le
            # portent, et le rendu l'affichait comme une prose du jeu.
            "description": _prose_utile(entry.get("Description")),
            "jurisdiction": (juridiction or {}).get("Name")
                            if isinstance(juridiction, dict) else juridiction,
            # 281 lieux portent la liste de ce qu'on y trouve, et le mot
            # `Amenities` n'apparaissait nulle part dans le projet.
            "amenities": entry.get("Amenities") or [],
        }

    def ascendance(uuid: str) -> list[dict]:
        """Chaîne du nœud jusqu'à la racine, incluse."""
        out, current, seen = [], uuid, set()
        while current and current in nodes and current not in seen:
            seen.add(current)
            out.append(nodes[current])
            current = nodes[current]["parent"]
        return out

    def racine(uuid: str) -> tuple[str | None, str | None, int]:
        seen, current, depth = set(), uuid, 0
        while True:
            node = nodes.get(current)
            if node is None or current in seen:
                break
            seen.add(current)
            parent = node["parent"]
            if not parent or parent not in nodes:
                return current, node["name"], depth
            current, depth = parent, depth + 1
        return None, None, depth

    # Un système, c'est une étoile ou un système stellaire — pas n'importe quel
    # nœud sans parent. Sans ce garde-fou, les 993 avant-postes orphelins du
    # starmap deviennent chacun leur propre « système », et l'on se retrouve à
    # annoncer « 51 lieux dans Mining Base #UU6-1EI ».
    RACINES_VALIDES = {"Star", "SolarSystem"}

    rows = []
    for uuid, node in nodes.items():
        sys_uuid, sys_name, depth = racine(uuid)
        if sys_uuid and nodes[sys_uuid]["type"] not in RACINES_VALIDES:
            sys_uuid = sys_name = None
        chaine = ascendance(uuid)
        parent = chaine[1] if len(chaine) > 1 else None
        # « Stanton / Crusader / Yela » : de la racine vers la feuille, comme
        # le joueur se représente l'espace.
        path = " / ".join(n["name"] for n in reversed(chaine) if n["name"]) or None
        rows.append((uuid, node["name"], node["parent"],
                     parent["name"] if parent else None,
                     parent["type"] if parent else None,
                     node["type"], sys_uuid, sys_name, path, depth,
                     node.get("description"), node.get("jurisdiction")))
        # Les stations, avant-postes et points de saut n'avaient pas d'alias :
        # seuls les corps célestes et les zones d'atterrissage en recevaient.
        # Conséquence mesurée — « où se trouve Grim HEX » ne résolvait aucun
        # lieu et partait chercher une commodité, comme Klescher, Everus Harbor
        # et Ruin Station. Ce sont pourtant les endroits qu'un joueur nomme le
        # plus souvent : c'est là qu'il atterrit.
        #
        # Le risque de pollution est nul pour les autres outils : le résolveur
        # filtre par type d'entité, et seuls les outils de lieu demandent
        # « starmap ». Les `Anomaly` sont les points de saut, qui manquaient
        # aussi.
        if node["name"] and node["type"] in (
            "Planet", "Moon", "Star", "SolarSystem", "LandingZone",
            "PointOfInterest", "Manmade", "Manmade_VisibleOnInteraction",
            "Outpost", "Outpost_InvalidQT", "Anomaly", "NavPoint",
            "Asteroid", "Asteroid_ValidQT",
        ):
            aliases.add("starmap", uuid, node["name"], source="canonical")

    con.executemany(
        "INSERT OR REPLACE INTO starmap "
        "(uuid, name, parent_uuid, parent_name, parent_type, type_name, "
        " system_uuid, system_name, path, depth, description, jurisdiction) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )

    # Les services d'un lieu. Le jeu écrit « Hangar L » dans `Name` et
    # « Hangar (L) » dans `DisplayName` : on garde les deux, la seconde forme
    # étant celle qui s'affiche.
    services = []
    for uuid, node in nodes.items():
        for a in node.get("amenities") or []:
            if not isinstance(a, dict) or not a.get("Name"):
                continue
            services.append((uuid, a.get("UUID"), a["Name"],
                             a.get("DisplayName") or a["Name"]))
    if services:
        con.executemany(
            "INSERT OR REPLACE INTO location_amenities "
            "(location_uuid, amenity_uuid, name, display_name) VALUES (?,?,?,?)",
            services)

    _load_positions(con, root)

    # Les lieux de `resources/locations.json` n'ont pas d'UUID : on les
    # rattache au starmap par leur nom, ce qui leur donne leur position dans
    # l'arborescence.
    con.execute(
        """
        UPDATE locations SET system = COALESCE(
          (SELECT s.system_name FROM starmap s
            WHERE s.name = locations.name AND s.system_name IS NOT NULL LIMIT 1),
          system)
        """
    )
    return len(rows)


def _load_positions(con: sqlite3.Connection, root: pathlib.Path) -> int:
    """Coordonnées et points de saut, depuis `starmap_positions.json`.

    Ce fichier était **entièrement ignoré** : 1 771 positions et les liaisons
    inter-systèmes, sur le disque depuis le premier clone. Sans lui, « c'est
    loin ? » n'a pas de réponse, et une route commerciale ne peut pas être
    départagée d'une autre par le trajet qu'elle impose.

    L'appariement se fait par UUID avec `starmap`, à 98 % — les 2 % restants
    sont des entités positionnées que la hiérarchie ne connaît pas, et qu'on
    laisse tomber plutôt que d'inventer un parent.
    """
    path = root / "starmap_positions.json"
    if not path.exists():
        return 0
    donnees = read_json(path)
    if not isinstance(donnees, dict):
        return 0

    positions = [
        (e.get("x"), e.get("y"), e.get("z"),
         1 if e.get("qt_valid") else 0, e["uuid"])
        for e in donnees.get("entities", []) if e.get("uuid")
    ]
    con.executemany(
        "UPDATE starmap SET x = ?, y = ?, z = ?, qt_valid = ? WHERE uuid = ?",
        positions,
    )

    con.executemany(
        "INSERT OR REPLACE INTO jump_points "
        "(entry_uuid, exit_uuid, entry_system, exit_system, fuel_cost) "
        "VALUES (?,?,?,?,?)",
        [(c.get("entry_uuid"), c.get("exit_uuid"),
          (c.get("entry_system") or "").capitalize(),
          (c.get("exit_system") or "").capitalize(),
          c.get("fuel_cost"))
         for c in donnees.get("connections", [])
         if c.get("entry_system") and c.get("exit_system")],
    )
    _completer_sauts_par_gateways(con)
    return len(positions)


def _completer_sauts_par_gateways(con: sqlite3.Connection) -> None:
    """Les liaisons que les stations prouvent et que la source ne liste pas.

    **Le saut Stanton ↔ Nyx existe en jeu et `connections` ne le porte
    pas** — signalé par l'utilisateur le 2026-08-12, vérifié : la liste de
    scunpacked s'arrête à Nyx→Pyro et Pyro→Stanton, mais le starmap place
    « Nyx Gateway » dans Stanton et « Stanton Gateway » dans Nyx, deux
    destinations quantiques valides. Une paire de Gateways réciproques —
    « X Gateway » dans Y **et** « Y Gateway » dans X, les deux systèmes
    jouables — matérialise le saut : ce sont les quais des deux bouts.

    Le coût de carburant n'est pas publié pour ces liaisons ; les deux
    sauts publiés valent 0,24 chacun, même mécanique — on aligne, et le
    choix est écrit ici plutôt que déduit en silence. Terra Gateway
    n'apparie rien : Terra n'est pas un système de la base, la liaison
    reste dehors sans liste à tenir à la main.
    """
    cout_publie = con.execute(
        "SELECT MAX(fuel_cost) FROM jump_points").fetchone()[0] or 0.24
    gateways = con.execute(
        "SELECT s.uuid, s.name, sys.name AS systeme FROM starmap s "
        "JOIN starmap sys ON sys.uuid = s.system_uuid "
        "WHERE s.name LIKE '% Gateway' AND s.type_name = 'Manmade'"
    ).fetchall()
    par_cle = {(g["name"].rsplit(" Gateway", 1)[0].lower(),
                g["systeme"].lower()): g for g in gateways}
    systemes = {r["name"].lower(): r["name"] for r in con.execute(
        "SELECT name FROM starmap WHERE type_name IN ('Star', 'SolarSystem') "
        "AND name IS NOT NULL")}
    for (destination, systeme), quai in par_cle.items():
        retour = par_cle.get((systeme, destination))
        if retour is None or destination not in systemes:
            continue
        existe = con.execute(
            "SELECT 1 FROM jump_points WHERE "
            "(LOWER(entry_system)=? AND LOWER(exit_system)=?) OR "
            "(LOWER(entry_system)=? AND LOWER(exit_system)=?)",
            (systeme, destination, destination, systeme)).fetchone()
        if existe:
            continue
        con.execute(
            "INSERT OR REPLACE INTO jump_points "
            "(entry_uuid, exit_uuid, entry_system, exit_system, fuel_cost) "
            "VALUES (?,?,?,?,?)",
            (quai["uuid"], retour["uuid"],
             systemes.get(systeme, systeme.capitalize()),
             systemes.get(destination, destination.capitalize()),
             cout_publie))


def load_commerce(con: sqlite3.Connection, root: pathlib.Path,
                  aliases: AliasCollector) -> tuple[int, int]:
    commodities = shops = 0
    for c in read_json(root / "resources" / "commodities.json"):
        uuid = c.get("UUID")
        if not uuid:
            continue
        name = c.get("Name")
        if name == PLACEHOLDER:
            name = c.get("Key")
        con.execute(
            "INSERT OR REPLACE INTO commodities "
            "(uuid, key, name, description, density_g_per_cc, groups, "
            " refined_name, refined_uuid) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uuid, c.get("Key"), name or c.get("Key"),
             None if c.get("Description") == PLACEHOLDER else c.get("Description"),
             c.get("DensityGPerCc"),
             json.dumps(c.get("CommodityGroups") or [], ensure_ascii=False),
             # Le rapprochement brut → raffiné se faisait par le nom ; le jeu
             # le dit, avec un UUID. « Raw Ice » → « Pressurized Ice » est
             # justement le cas que le nom seul ne donne pas.
             c.get("RefinedVersionName"), c.get("RefinedVersionUUID")),
        )
        commodities += 1
        # Le jeu s'appelle lui-même « Quantanium » en clé et « Quantainium » en
        # nom : les deux méritent d'être des alias.
        aliases.add_variants("commodity", uuid, name, c.get("Key"))
        aliases.add("commodity", uuid, c.get("Key"), source="derived", weight=0.9)

    path = root / "resources" / "commodity_trade_locations.json"
    if not path.exists():
        return commodities, shops

    seen_shops: set[str] = set()
    for entry in read_json(path):
        cuuid = entry.get("CommodityUUID")
        for direction, key in (("sold_at", "SoldAt"), ("bought_at", "BoughtAt")):
            for place in entry.get(key) or []:
                suuid = place.get("TradeLocationUUID")
                if not suuid:
                    continue
                if suuid not in seen_shops:
                    con.execute(
                        "INSERT OR REPLACE INTO shops "
                        "(uuid, class_name, display_name, starmap_object_uuid) "
                        "VALUES (?,?,?,?)",
                        (suuid, place.get("TradeLocationClassName"),
                         place.get("TradeLocationDisplayName"),
                         place.get("StarmapObjectUUID")),
                    )
                    seen_shops.add(suuid)
                    shops += 1
                    aliases.add_variants("shop", suuid,
                                         place.get("TradeLocationDisplayName"),
                                         place.get("TradeLocationClassName"))
                if cuuid:
                    con.execute(
                        "INSERT OR IGNORE INTO commodity_shops "
                        "(commodity_uuid, shop_uuid, direction) VALUES (?,?,?)",
                        (cuuid, suuid, direction),
                    )
    return commodities, shops


def load_trade_flows(con: sqlite3.Connection, root: pathlib.Path) -> int:
    """`trade_locations.json` — ce qu'un lieu produit et ce qu'il **consomme**.

    Le fichier avait été écarté sur ses `ProducesTags`, et l'argument tenait.
    Mais `ConsumesTags` couvre 849 lieux et répond à une question que rien ne
    couvrait : **où écouler une cargaison**. La production dit où ça sort, la
    consommation dit où ça se vend.

    Deux prudences mesurées :

    - sur 102 tags de consommation distincts, **36 seulement portent un nom de
      commodité** ; les plus fréquents sont des catégories (Supplies 913,
      Common 679, Food 642). On garde le tag brut et on ne résout la commodité
      que lorsqu'elle existe, plutôt que de faire passer une catégorie pour une
      marchandise ;
    - la liste `Negative` — 276 lieux en consommation — dit ce qu'un site
      **refuse**. La confondre avec `Positive` enverrait vendre là où personne
      n'achète.

    Appelée après `load_commerce`, dont elle a besoin pour apparier les noms.
    """
    path = root / "trade_locations.json"
    if not path.exists():
        return 0

    from ..normalize import normalize

    commodites = {normalize(nom): uuid for nom, uuid in con.execute(
        "SELECT name, uuid FROM commodities WHERE name IS NOT NULL")}

    lignes = []
    for lieu in read_json(path):
        uuid = lieu.get("UUID")
        if not uuid or lieu.get("Disabled"):
            continue
        nom = lieu.get("DisplayName") or lieu.get("ClassName")
        for champ, sens in (("ConsumesTags", "consomme"), ("ProducesTags", "produit")):
            bloc = lieu.get(champ) or {}
            for liste, refuse in (("Positive", 0), ("Negative", 1)):
                for tag in bloc.get(liste) or []:
                    tag_nom = tag.get("Name")
                    if not tag_nom:
                        continue
                    lignes.append((uuid, nom, sens, refuse, tag.get("UUID"),
                                   tag_nom, commodites.get(normalize(tag_nom))))
    if not lignes:
        return 0
    con.executemany(
        "INSERT OR REPLACE INTO trade_flows "
        "(location_uuid, location_name, sens, refuse, tag_uuid, tag_name, "
        " commodity_uuid) VALUES (?,?,?,?,?,?,?)", lignes)
    # Relire ce qui est entré : la clé primaire dédoublonne, et le compte de
    # `lignes` masquerait l'écart.
    return con.execute("SELECT COUNT(*) FROM trade_flows").fetchone()[0]
