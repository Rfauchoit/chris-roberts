"""La fabrication : blueprints, recettes, méthodes de raffinage.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
import pathlib
import re
import sqlite3
from collections.abc import Iterator
from .source import read_json
from .socle import AliasCollector, PLACEHOLDER


# ------------------------------------------------------------------ blueprints

def _walk_requirements(node: dict, group: dict | None = None) -> Iterator[tuple[dict, dict | None]]:
    kind = node.get("Kind")
    if kind in ("resource", "item"):
        yield node, group
        return
    nxt = node if kind == "group" else group
    for child in node.get("Children") or []:
        yield from _walk_requirements(child, nxt)


def _walk_modifiers(node: dict, group: dict | None = None) -> Iterator[tuple[dict, dict | None]]:
    """Les modificateurs pendent au **groupe** — c'est le composant (FRAME,
    EMITTER) dont la qualité fait varier la statistique, pas l'ingrédient."""
    nxt = node if node.get("Kind") == "group" else group
    for mod in node.get("Modifiers") or []:
        yield mod, nxt
    for child in node.get("Children") or []:
        yield from _walk_modifiers(child, nxt)


def load_blueprints(con: sqlite3.Connection, root: pathlib.Path,
                    aliases: AliasCollector) -> tuple[int, int]:
    """`blueprints.json`, l'index racine — pas le répertoire `blueprints/`,
    qui n'est qu'un dump XML->JSON brut aux références non résolues."""
    blueprints = ingredients = 0
    for bp in read_json(root / "blueprints.json"):
        uuid = bp.get("UUID")
        if not uuid:
            continue
        out = bp.get("Output") or {}
        dismantle = bp.get("Dismantle") or {}
        availability = bp.get("Availability") or {}
        con.execute(
            "INSERT OR REPLACE INTO blueprints "
            "(uuid, key, kind, category_uuid, output_uuid, output_class, "
            " output_name, output_type, output_subtype, output_grade, "
            " available_by_default, dismantle_seconds, dismantle_efficiency) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid, bp.get("Key"), bp.get("Kind"), bp.get("CategoryUUID"),
                out.get("UUID"), out.get("Class"), out.get("Name") or bp.get("Key"),
                out.get("Type"), out.get("Subtype"), out.get("Grade"),
                int(bool(availability.get("Default"))),
                dismantle.get("TimeSeconds"), dismantle.get("Efficiency"),
            ),
        )
        blueprints += 1

        # Le joueur nomme le produit, pas le blueprint : « comment on fabrique
        # un P6-LR », jamais « BP_CRAFT_behr_sniper_ballistic_01 ».
        aliases.add_variants("blueprint", uuid, out.get("Name"), out.get("Class"))

        for tier in bp.get("Tiers") or []:
            cur = con.execute(
                "INSERT OR REPLACE INTO blueprint_tiers "
                "(blueprint_uuid, tier_index, craft_time_seconds) VALUES (?,?,?)",
                (uuid, tier.get("TierIndex"), tier.get("CraftTimeSeconds")),
            )
            tier_id = cur.lastrowid
            requirements = tier.get("Requirements")
            if not requirements:
                continue
            rows = []
            for pos, (node, group) in enumerate(_walk_requirements(requirements)):
                kind = node["Kind"]
                rows.append((
                    tier_id, pos,
                    (group or {}).get("Key"), (group or {}).get("Name"),
                    (group or {}).get("RequiredCount"),
                    kind, node.get("UUID"), node.get("Name") or "?",
                    node.get("QuantityScu") if kind == "resource" else None,
                    node.get("Quantity") if kind == "item" else None,
                    node.get("MinQuality"),
                ))
            con.executemany(
                "INSERT INTO blueprint_ingredients "
                "(tier_id, position, group_key, group_name, required_count, "
                " ingredient_kind, ref_uuid, ref_name, quantity_scu, "
                " quantity_units, min_quality) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            ingredients += len(rows)

            # Ce qui change entre un « P6-LR 900 » et un « P6-LR 990 » : la
            # qualité du matériau interpole le multiplicateur entre ses deux
            # bornes. On garde `mult_min` et `mult_max` dans l'ordre du jeu
            # sans les réordonner — 462 modificateurs sur 5 695 décroissent,
            # et ce sont ceux dont la baisse est un gain (le recul d'une arme).
            mods = []
            for mod, group in _walk_modifiers(requirements):
                cle = mod.get("Key")
                if not cle:
                    continue
                q = mod.get("QualityRange") or {}
                r = mod.get("ModifierRange") or {}
                mods.append((
                    tier_id, (group or {}).get("Key"), (group or {}).get("Name"),
                    mod.get("UUID"), cle, mod.get("Name"),
                    q.get("Min"), q.get("Max"),
                    r.get("AtMinQuality"), r.get("AtMaxQuality"),
                    mod.get("ValueRangeType"),
                ))
            if mods:
                con.executemany(
                    "INSERT INTO blueprint_modifiers "
                    "(tier_id, group_key, group_name, uuid, cle, nom, "
                    " quality_min, quality_max, mult_min, mult_max, "
                    " interpolation) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    mods,
                )

        for pool in availability.get("RewardPools") or []:
            con.execute(
                "INSERT OR IGNORE INTO blueprint_sources "
                "(blueprint_uuid, pool_uuid, pool_key) VALUES (?,?,?)",
                (uuid, pool.get("UUID"), pool.get("Key") or "?"),
            )
        # Les blueprints sont chargés avant les contrats : c'est ce dernier qui
        # remplira contract_reward_pools et reward_pool_contents.
        for ret in dismantle.get("Returns") or []:
            con.execute(
                "INSERT INTO blueprint_dismantle_returns "
                "(blueprint_uuid, ref_uuid, ref_name, quantity_scu) VALUES (?,?,?,?)",
                (uuid, ret.get("UUID"), ret.get("Name") or "?", ret.get("QuantityScu")),
            )
    return blueprints, ingredients


# « Very Low » avant « Low » : sans cet ordre, l'alternation retiendrait
# « Low » dans « Very Low » et confondrait le plus lent avec le simplement lent.
_NIVEAUX = {"very low": 1, "low": 2, "moderate": 3, "high": 4}
_DETAIL_METHODE = re.compile(
    r"(very low|low|moderate|high)\s+speed\s*//\s*"
    r"(very low|low|moderate|high)\s+cost\s*//\s*"
    r"(very low|low|moderate|high)\s+yield", re.IGNORECASE)


def load_methodes_de_raffinage(con: sqlite3.Connection,
                               labels: dict[str, str]) -> int:
    """Les neuf méthodes de raffinage et leurs trois notes.

    Demande de l'utilisateur : « quelle est la meilleure technique de
    raffinerie selon des critères ». Le jeu publie les notes lui-même, sous
    `refinery_ui_ProcessingType_<clé>_Details` — « Low Speed // High Cost //
    High Yield ».

    **Le segment de clé ment.** `FastCareful` porte « Low Speed » : le nom a
    vieilli, le détail a été mis à jour. On n'analyse donc que `_Details`.
    """
    lignes = []
    for cle, valeur in labels.items():
        nu = cle.removesuffix(",P")
        if not nu.startswith("refinery_ui_ProcessingType_") or "_" in nu[27:]:
            continue
        segment = nu[len("refinery_ui_ProcessingType_"):]
        if not valeur or valeur == PLACEHOLDER:
            continue

        def libelle(suffixe: str) -> str | None:
            for essai in (f"{nu}_{suffixe}", f"{nu}_{suffixe},P"):
                if labels.get(essai) and labels[essai] != PLACEHOLDER:
                    return labels[essai]
            return None

        notes = _DETAIL_METHODE.search(libelle("Details") or "")
        lignes.append((
            segment, valeur, libelle("Desc"),
            *( [_NIVEAUX[g.lower()] for g in notes.groups()] if notes
               else [None, None, None] ),
        ))

    con.executemany(
        "INSERT OR REPLACE INTO refinery_methods "
        "(cle, nom_en, description_en, vitesse, cout, rendement) "
        "VALUES (?,?,?,?,?,?)", lignes)
    return len(lignes)
