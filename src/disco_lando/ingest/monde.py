"""Le monde : ressources, constructeurs, factions.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
import pathlib
import sqlite3
from .source import read_json
from .socle import AliasCollector, _bool, _location_id


def load_resources(con: sqlite3.Connection, root: pathlib.Path,
                   aliases: AliasCollector) -> tuple[int, int, int]:
    resources = locations = links = 0

    for res in read_json(root / "resources" / "resources.json"):
        uuid = res.get("UUID")
        if not uuid:
            continue
        name = res.get("Name")
        con.execute(
            "INSERT OR REPLACE INTO resources (uuid, key, name, kind, tier) "
            "VALUES (?,?,?,?,?)",
            (uuid, res.get("Key"), name or res.get("Key"), res.get("Kind"), res.get("Tier")),
        )
        resources += 1
        # **Une entrée d'outillage n'a pas d'alias.** 27 gisements
        # `MineableRock_test_*` et 10 `_TEMPLATE` sont l'échafaudage de CIG,
        # pas des filons : indexés, ils ressortaient comme **choix** — le
        # journal a montré « Tu parles duquel ? Iron, …, MineableRock_test_
        # Iron ? ». La ligne reste en base (le palier de rareté se lit sur les
        # vraies entrées), seul l'alias saute : ce qu'un joueur ne peut pas
        # nommer n'a pas à être nommable.
        nom_bas = (name or "").lower()
        if "_test_" not in nom_bas and not nom_bas.endswith(("_test",
                                                            "template")):
            aliases.add_variants("resource", uuid, name, res.get("Key"))

        composition = res.get("Composition") or {}
        rows = [
            (uuid, part.get("UUID"), part.get("Key"), part.get("Name") or "?",
             part.get("MinPercentage"), part.get("MaxPercentage"), part.get("Probability"))
            for part in composition.get("Parts") or []
        ]
        if rows:
            con.executemany(
                "INSERT INTO resource_composition "
                "(deposit_uuid, part_uuid, part_key, part_name, min_pct, max_pct, "
                " probability) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            # Le nom du composant (« Quantainium (Raw) ») est une entrée de
            # résolution valable, rattachée au gisement qui le porte.
            for part in composition.get("Parts") or []:
                aliases.add("resource", uuid, part.get("Name"),
                            source="derived", weight=0.7)

    known = {r[0] for r in con.execute("SELECT uuid FROM resources")}

    for provider in read_json(root / "resources" / "locations.json"):
        pname = (provider.get("Provider") or {}).get("Name")
        loc_ids = []
        for loc in provider.get("Locations") or []:
            name = loc.get("Name")
            if not name:
                continue
            lid = _location_id(loc.get("System"), name)
            con.execute(
                "INSERT OR REPLACE INTO locations "
                "(uuid, name, system, loc_type, provider_name) VALUES (?,?,?,?,?)",
                (lid, name, loc.get("System"), loc.get("Type"), pname),
            )
            loc_ids.append(lid)
            locations += 1
            aliases.add("location", lid, name, source="canonical")

        for group in provider.get("Groups") or []:
            gname = group.get("GroupName")
            gprob = group.get("GroupProbability") or 0.0
            for deposit in group.get("Deposits") or []:
                ruuid = deposit.get("ResourceUUID")
                if ruuid not in known:
                    continue
                prob = gprob * (deposit.get("RelativeProbability") or 0.0)
                for lid in loc_ids:
                    con.execute(
                        "INSERT INTO resource_locations "
                        "(resource_uuid, location_uuid, group_name, probability) "
                        "VALUES (?,?,?,?) "
                        "ON CONFLICT (resource_uuid, location_uuid, group_name) "
                        "DO UPDATE SET probability = MAX(probability, excluded.probability)",
                        (ruuid, lid, gname, prob),
                    )
                    links += 1
    return resources, locations, links


def load_manufacturers(con: sqlite3.Connection, root: pathlib.Path,
                       aliases: AliasCollector) -> int:
    """141 constructeurs, dont 116 décrits. Fichier jamais ouvert jusqu'ici.

    Le code est un alias à part entière : « KSAR » est ce que porte la fiche
    d'un objet, et un joueur peut très bien le taper. Les deux se résolvent.
    """
    total = 0
    for m in read_json(root / "manufacturers.json"):
        uuid = m.get("Reference")
        nom = m.get("Name")
        if not uuid or not nom:
            continue
        con.execute(
            "INSERT OR REPLACE INTO manufacturers (uuid, code, name, description) "
            "VALUES (?,?,?,?)",
            (uuid, m.get("Code"), nom, (m.get("Description") or "").strip() or None))
        aliases.add_variants("manufacturer", uuid, nom)
        # Certains constructeurs ont un code identique à leur nom (« 987 ») :
        # `add` dédoublonne, inutile de tester.
        if m.get("Code"):
            aliases.add("manufacturer", uuid, m["Code"],
                        source="derived", weight=0.9)
        total += 1
    return total


def load_factions(con: sqlite3.Connection, root: pathlib.Path,
                  aliases: AliasCollector) -> int:
    total = 0
    for path in sorted((root / "factions").glob("*.json")):
        f = read_json(path)
        uuid = f.get("UUID")
        if not uuid:
            continue
        # `Reputation.Properties` porte l'état civil — et une **seconde**
        # description, plus longue que celle de la racine sur 12 factions.
        # Prendre la plus riche des deux plutôt que la première venue.
        proprietes = ((f.get("Reputation") or {}).get("Properties") or {})
        descriptions = [d for d in (f.get("Description"),
                                    proprietes.get("Description")) if d]
        description = max(descriptions, key=len) if descriptions else None

        con.execute(
            "INSERT OR REPLACE INTO factions "
            "(uuid, key, name, faction_type, default_reaction, description, "
            " headquarters, founded, leadership, area, focus, lawful, "
            " able_to_arrest, polices_criminality, polices_trespass, "
            " no_legal_rights) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, path.stem, f.get("Name") or path.stem, f.get("FactionType"),
             f.get("DefaultReaction"), description,
             proprietes.get("Headquarters"), proprietes.get("Founded"),
             proprietes.get("Leadership"), proprietes.get("Area"),
             proprietes.get("Focus"), _bool(proprietes.get("Lawful")),
             _bool(f.get("AbleToArrest")), _bool(f.get("PolicesCriminality")),
             _bool(f.get("PolicesLawfulTrespass")), _bool(f.get("NoLegalRights"))),
        )
        total += 1
        aliases.add_variants("faction", uuid, f.get("Name"), path.stem)
    return total
