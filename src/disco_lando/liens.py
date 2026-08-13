"""Registre auditable des liens entre les sources et le catalogue du jeu.

Les UUID du jeu, du wiki et d'UEX sont déjà les bonnes clés. Les recopier dans
une table de correspondance créerait une seconde vérité, susceptible de rester
ancienne après une ingestion. Ce registre ne stocke donc **aucune donnée** : il
décrit les jointures autorisées et mesure leur couverture sur la base ouverte.

Un rapprochement par nom n'est admis que quand l'amont ne publie pas d'UUID.
La méthode est alors écrite dans la définition, et les cas ambigus ne comptent
jamais comme liés. C'est notamment indispensable pour les passerelles : « Nyx
Gateway » existe dans Stanton et Pyro, le système fait partie de la clé.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any


@dataclasses.dataclass(frozen=True)
class DefinitionLien:
    """Une relation calculée entre une source et une entité canonique."""

    cle: str
    source: str
    cible: str
    methode: str
    tables: tuple[str, ...]
    sql_source_total: str
    sql_source_liee: str
    seuil_source: float | None = None
    sql_cible_total: str | None = None
    sql_cible_couverte: str | None = None
    seuil_cible: float | None = None
    sql_par_cible: str | None = None
    sql_cible_par_source: str | None = None


@dataclasses.dataclass(frozen=True)
class MesureLien:
    """Couverture vivante d'une :class:`DefinitionLien`."""

    definition: DefinitionLien
    source_total: int
    source_liee: int
    cible_total: int | None
    cible_couverte: int | None
    tables_manquantes: tuple[str, ...] = ()

    @property
    def disponible(self) -> bool:
        return not self.tables_manquantes

    @property
    def couverture_source(self) -> float | None:
        if not self.source_total:
            return None
        return self.source_liee / self.source_total

    @property
    def couverture_cible(self) -> float | None:
        if not self.cible_total:
            return None
        return (self.cible_couverte or 0) / self.cible_total

    @property
    def conforme(self) -> bool:
        if not self.disponible:
            return False
        d = self.definition
        if d.seuil_source is not None:
            taux = self.couverture_source
            if taux is None or taux < d.seuil_source:
                return False
        if d.seuil_cible is not None:
            taux = self.couverture_cible
            if taux is None or taux < d.seuil_cible:
                return False
        return True


_SYSTEMES_CANONIQUES = """
    SELECT system_name nom FROM starmap
    WHERE system_name IS NOT NULL
    GROUP BY system_name HAVING COUNT(*) > 1
"""

# Mesuré le 2026-08-09 : les liens UUID déclarés sont intègres à 100 %. Le
# wiki des objets couvre un univers plus large que l'ingestion DataCore
# (12 282 contre 10 804 objets), d'où un seuil porté sur la **cible** et non
# sur la source. De même, le catalogue générique UEX contient 39 marchandises
# qui ne sont pas des `commodities` du jeu ; on surveille ce qu'il couvre sans
# prétendre que ces 39 lignes sont orphelines.
LIENS: tuple[DefinitionLien, ...] = (
    DefinitionLien(
        "wiki_vaisseaux", "wiki_vehicles.uuid", "ships.uuid", "UUID direct",
        ("wiki_vehicles", "ships"),
        "SELECT COUNT(*) FROM wiki_vehicles",
        "SELECT COUNT(*) FROM wiki_vehicles w "
        "WHERE EXISTS (SELECT 1 FROM ships s WHERE s.uuid = w.uuid)",
        1.0,
        "SELECT COUNT(*) FROM ships",
        "SELECT COUNT(*) FROM ships s WHERE EXISTS "
        "(SELECT 1 FROM wiki_vehicles w WHERE w.uuid = s.uuid)",
        0.90,
        "SELECT w.* FROM wiki_vehicles w JOIN ships s ON s.uuid = w.uuid "
        "WHERE s.uuid = ?",
    ),
    DefinitionLien(
        "wiki_objets", "wiki_items.uuid", "items.uuid", "UUID direct",
        ("wiki_items", "items"),
        "SELECT COUNT(*) FROM wiki_items",
        "SELECT COUNT(*) FROM wiki_items w "
        "WHERE EXISTS (SELECT 1 FROM items i WHERE i.uuid = w.uuid)",
        None,
        "SELECT COUNT(*) FROM items",
        "SELECT COUNT(*) FROM items i WHERE EXISTS "
        "(SELECT 1 FROM wiki_items w WHERE w.uuid = i.uuid)",
        0.70,
        "SELECT w.* FROM wiki_items w JOIN items i ON i.uuid = w.uuid "
        "WHERE i.uuid = ?",
    ),
    DefinitionLien(
        "wiki_systemes", "wiki_systems.name", "starmap.system_name",
        "nom exact, systèmes ouverts seulement",
        ("wiki_systems", "starmap"),
        "SELECT COUNT(*) FROM wiki_systems",
        "SELECT COUNT(*) FROM wiki_systems w WHERE EXISTS "
        f"(SELECT 1 FROM ({_SYSTEMES_CANONIQUES}) c "
        "WHERE c.nom = w.name COLLATE NOCASE)",
        None,
        f"SELECT COUNT(*) FROM ({_SYSTEMES_CANONIQUES})",
        f"SELECT COUNT(*) FROM ({_SYSTEMES_CANONIQUES}) c WHERE EXISTS "
        "(SELECT 1 FROM wiki_systems w WHERE w.name = c.nom COLLATE NOCASE)",
        1.0,
        "SELECT w.* FROM wiki_systems w WHERE w.name = ? COLLATE NOCASE "
        f"AND EXISTS (SELECT 1 FROM ({_SYSTEMES_CANONIQUES}) c "
        "WHERE c.nom = w.name COLLATE NOCASE)",
    ),
    DefinitionLien(
        "uex_prix_objets", "uex_prices.ref_uuid[kind=item]", "items.uuid",
        "UUID direct", ("uex_prices", "items"),
        "SELECT COUNT(DISTINCT ref_uuid) FROM uex_prices "
        "WHERE kind = 'item' AND TRIM(COALESCE(ref_uuid, '')) <> ''",
        "SELECT COUNT(DISTINCT p.ref_uuid) FROM uex_prices p "
        "WHERE p.kind = 'item' AND TRIM(COALESCE(p.ref_uuid, '')) <> '' "
        "AND EXISTS (SELECT 1 FROM items i WHERE i.uuid = p.ref_uuid)",
        1.0,
    ),
    DefinitionLien(
        "uex_prix_vaisseaux", "uex_prices.ref_uuid[kind=vehicle|rental]",
        "ships.uuid", "UUID direct", ("uex_prices", "ships"),
        "SELECT COUNT(DISTINCT kind || ':' || ref_uuid) FROM uex_prices "
        "WHERE kind IN ('vehicle', 'rental') "
        "AND TRIM(COALESCE(ref_uuid, '')) <> ''",
        "SELECT COUNT(DISTINCT p.kind || ':' || p.ref_uuid) FROM uex_prices p "
        "WHERE p.kind IN ('vehicle', 'rental') "
        "AND TRIM(COALESCE(p.ref_uuid, '')) <> '' "
        "AND EXISTS (SELECT 1 FROM ships s WHERE s.uuid = p.ref_uuid)",
        1.0,
    ),
    DefinitionLien(
        "uex_objets_exclusifs", "uex_items.uuid", "items.uuid", "UUID direct",
        ("uex_items", "items"),
        "SELECT COUNT(*) FROM uex_items",
        "SELECT COUNT(*) FROM uex_items u "
        "WHERE EXISTS (SELECT 1 FROM items i WHERE i.uuid = u.uuid)",
        1.0,
    ),
    DefinitionLien(
        "uex_cotations_commodites", "uex_commodity_prices.commodity",
        "commodities.name", "nom exact (repli annoncé)",
        ("uex_commodity_prices", "commodities"),
        "SELECT COUNT(DISTINCT commodity) FROM uex_commodity_prices",
        "SELECT COUNT(DISTINCT u.commodity) FROM uex_commodity_prices u "
        "WHERE EXISTS (SELECT 1 FROM commodities c "
        "WHERE c.name = u.commodity COLLATE NOCASE)",
        1.0,
    ),
    DefinitionLien(
        "uex_catalogue_commodites", "uex_prices.name[kind=commodity]",
        "commodities.name", "nom exact (périmètre UEX plus large)",
        ("uex_prices", "commodities"),
        "SELECT COUNT(DISTINCT name) FROM uex_prices WHERE kind = 'commodity'",
        "SELECT COUNT(DISTINCT p.name) FROM uex_prices p "
        "WHERE p.kind = 'commodity' AND EXISTS "
        "(SELECT 1 FROM commodities c WHERE c.name = p.name COLLATE NOCASE)",
        None,
        "SELECT COUNT(*) FROM commodities",
        "SELECT COUNT(*) FROM commodities c WHERE EXISTS "
        "(SELECT 1 FROM uex_prices p WHERE p.kind = 'commodity' "
        "AND p.name = c.name COLLATE NOCASE)",
        0.75,
    ),
    DefinitionLien(
        "boutiques_lieux", "shops.starmap_object_uuid", "starmap.uuid",
        "UUID direct quand UEX le publie", ("shops", "starmap"),
        "SELECT COUNT(*) FROM shops "
        "WHERE TRIM(COALESCE(starmap_object_uuid, '')) <> ''",
        "SELECT COUNT(*) FROM shops s "
        "WHERE TRIM(COALESCE(s.starmap_object_uuid, '')) <> '' AND EXISTS "
        "(SELECT 1 FROM starmap sm WHERE sm.uuid = s.starmap_object_uuid)",
        1.0,
    ),
    DefinitionLien(
        "traductions_catalogue", "traductions.uuid", "catalogue du jeu",
        "UUID direct ; code pour les constructeurs",
        ("traductions", "ships", "items", "starmap", "contracts",
         "commodities", "resources", "manufacturers", "factions"),
        "SELECT COUNT(*) FROM traductions WHERE entity_type IN "
        "('ship','item','starmap','contract','commodity','resource',"
        "'manufacturer','org')",
        "SELECT COUNT(*) FROM traductions t WHERE "
        "(t.entity_type = 'ship' AND EXISTS "
        " (SELECT 1 FROM ships x WHERE x.uuid = t.uuid)) OR "
        "(t.entity_type = 'item' AND EXISTS "
        " (SELECT 1 FROM items x WHERE x.uuid = t.uuid)) OR "
        "(t.entity_type = 'starmap' AND EXISTS "
        " (SELECT 1 FROM starmap x WHERE x.uuid = t.uuid)) OR "
        "(t.entity_type = 'contract' AND EXISTS "
        " (SELECT 1 FROM contracts x WHERE x.uuid = t.uuid)) OR "
        "(t.entity_type = 'commodity' AND EXISTS "
        " (SELECT 1 FROM commodities x WHERE x.uuid = t.uuid)) OR "
        "(t.entity_type = 'resource' AND EXISTS "
        " (SELECT 1 FROM resources x WHERE x.uuid = t.uuid)) OR "
        "(t.entity_type = 'manufacturer' AND EXISTS "
        " (SELECT 1 FROM manufacturers x WHERE x.code = t.uuid)) OR "
        "(t.entity_type = 'org' AND EXISTS "
        " (SELECT 1 FROM factions x WHERE x.uuid = t.uuid))",
        1.0,
    ),
    DefinitionLien(
        "raffineries_lieux", "refineries.station|nickname+star_system",
        "starmap.uuid", "nom exact puis surnom contextualisé et non ambigu",
        ("refineries", "starmap"),
        "SELECT COUNT(*) FROM refineries",
        "WITH r AS (SELECT *, TRIM(CASE WHEN INSTR(nickname, ' (') > 0 "
        "THEN SUBSTR(nickname, 1, INSTR(nickname, ' (') - 1) "
        "ELSE nickname END) canon FROM refineries) "
        "SELECT COUNT(*) FROM r WHERE (SELECT COUNT(DISTINCT sm.uuid) "
        "FROM starmap sm WHERE sm.system_name = r.star_system COLLATE NOCASE "
        "AND (sm.name = COALESCE(r.station, r.outpost, r.city, r.name) "
        "COLLATE NOCASE OR sm.name = r.canon COLLATE NOCASE)) = 1",
        1.0,
        sql_cible_par_source=(
            "WITH r AS (SELECT *, TRIM(CASE WHEN INSTR(nickname, ' (') > 0 "
            "THEN SUBSTR(nickname, 1, INSTR(nickname, ' (') - 1) "
            "ELSE nickname END) canon FROM refineries WHERE id = ?), "
            "candidats AS (SELECT sm.* FROM r JOIN starmap sm "
            "ON sm.system_name = r.star_system COLLATE NOCASE AND "
            "(sm.name = COALESCE(r.station, r.outpost, r.city, r.name) "
            "COLLATE NOCASE OR sm.name = r.canon COLLATE NOCASE)) "
            "SELECT * FROM candidats WHERE "
            "(SELECT COUNT(DISTINCT uuid) FROM candidats) = 1"
        ),
    ),
)

_PAR_CLE = {lien.cle: lien for lien in LIENS}


def _tables_presentes(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def _compter(con: sqlite3.Connection, sql: str | None) -> int | None:
    if sql is None:
        return None
    ligne = con.execute(sql).fetchone()
    return int(ligne[0] or 0) if ligne else 0


def mesurer(con: sqlite3.Connection, definition: DefinitionLien) -> MesureLien:
    """Mesure un lien sans modifier la base et sans masquer une table absente."""
    presentes = _tables_presentes(con)
    manquantes = tuple(t for t in definition.tables if t not in presentes)
    if manquantes:
        return MesureLien(definition, 0, 0, None, None, manquantes)
    return MesureLien(
        definition,
        _compter(con, definition.sql_source_total) or 0,
        _compter(con, definition.sql_source_liee) or 0,
        _compter(con, definition.sql_cible_total),
        _compter(con, definition.sql_cible_couverte),
    )


def mesurer_tous(con: sqlite3.Connection) -> list[MesureLien]:
    """Mesure le registre dans son ordre documentaire."""
    return [mesurer(con, definition) for definition in LIENS]


def ligne_source(con: sqlite3.Connection, cle: str,
                 identifiant_cible: str | None) -> dict[str, Any] | None:
    """Lit une source via une jointure déclarée, jamais via du SQL appelant.

    La requête est attachée au lien : un domaine ne peut donc pas inventer un
    repli par nom différent de celui dont `disco verifier` mesure la couverture.
    Une source optionnelle absente reste un complément absent, pas une panne du
    cœur déterministe.
    """
    if not identifiant_cible:
        return None
    definition = _PAR_CLE.get(cle)
    if definition is None:
        raise KeyError(f"lien inconnu : {cle}")
    if definition.sql_par_cible is None:
        raise ValueError(f"le lien {cle} ne permet pas de lecture individuelle")
    try:
        ligne = con.execute(definition.sql_par_cible,
                            (identifiant_cible,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(ligne) if ligne is not None else None


def ligne_cible(con: sqlite3.Connection, cle: str,
                identifiant_source: Any) -> dict[str, Any] | None:
    """Retrouve la cible canonique par la relation déclarée et mesurée."""
    definition = _PAR_CLE.get(cle)
    if definition is None:
        raise KeyError(f"lien inconnu : {cle}")
    if definition.sql_cible_par_source is None:
        raise ValueError(f"le lien {cle} ne permet pas la lecture de sa cible")
    try:
        ligne = con.execute(definition.sql_cible_par_source,
                            (identifiant_source,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(ligne) if ligne is not None else None
