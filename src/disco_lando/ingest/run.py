"""Orchestration de l'ingestion.

Réingestion toujours complète, jamais incrémentale : la base est reconstruite
dans un fichier temporaire puis basculée par renommage. Pas de demi-état si ça
casse en cours de route — et le fichier en place reste interrogeable pendant la
reconstruction.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import sqlite3
import time
from dataclasses import dataclass, field

import yaml

from .. import config, db
from ..normalize import normalize
from . import loaders, source


@dataclass
class Report:
    state: source.SourceState
    counts: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


def _utcnow() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _load_guild_aliases(con: sqlite3.Connection, aliases: loaders.AliasCollector,
                        path: pathlib.Path) -> int:
    """Argot de guilde, édité à la main.

    Format : une entrée par entité, ciblée par type et par nom exact.

        ship:
          "Aegis Gladius": [glad, gladiouse]
          "Drake Cutlass Black": [cutty, "cutty black"]

    Poids supérieur à 1 pour que l'argot maison gagne sur un nom officiel qui
    matcherait aussi bien.
    """
    if not path.exists():
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    added = 0
    for entity_type, entries in data.items():
        if not isinstance(entries, dict):
            continue
        for canonical, nicknames in entries.items():
            row = con.execute(
                "SELECT entity_id FROM aliases "
                "WHERE entity_type = ? AND alias_norm = ? LIMIT 1",
                (entity_type, normalize(str(canonical))),
            ).fetchone()
            if row is None:
                continue
            for nickname in nicknames or []:
                aliases.add(entity_type, row["entity_id"], str(nickname),
                            source="guild", weight=1.4, lang="fr")
                added += 1
    return added


def ingest(source_dir: pathlib.Path | None = None,
           db_path: pathlib.Path | None = None,
           *, echo=print) -> Report:
    root = source_dir or config.SOURCE_DIR
    target = db_path or config.DB_PATH
    st = source.state(root)
    started = time.perf_counter()

    staging = target.with_suffix(".building.db")
    for leftover in (staging, staging.with_suffix(".db-wal"), staging.with_suffix(".db-shm")):
        leftover.unlink(missing_ok=True)

    con = db.connect(staging)
    db.create_schema(con)
    # Les tables UEX sont créées vides dès l'ingestion. Elles ne font pas
    # partie du schéma du jeu — elles sont remplies par `disco uex` — mais la
    # reconstruction complète les effaçait, et toute requête de prix plantait
    # ensuite sur « no such table ». Vides, elles dégradent proprement : la
    # réponse bascule sur la fabrication ou le butin.
    from . import uex as _uex
    con.executescript(_uex.SCHEMA)
    run = con.execute(
        "INSERT INTO ingest_runs "
        "(started_at, source_commit, commit_subject, game_version, build_id, status) "
        "VALUES (?,?,?,?,?, 'running')",
        (_utcnow(), st.commit, st.subject, st.game_version, st.build_id),
    ).lastrowid

    counts: dict[str, int] = {}
    aliases = loaders.AliasCollector()
    try:
        echo(f"source   {st.label}  ({st.commit[:10]})")

        labels = loaders.load_labels(root)
        counts["labels"] = len(labels)
        echo(f"labels   {len(labels):>7}")

        ships, hardpoints = loaders.load_ships(con, root, aliases, labels)
        counts["ships"], counts["hardpoints"] = ships, hardpoints
        echo(f"ships    {ships:>7}   hardpoints {hardpoints}")

        counts["items"] = loaders.load_items(con, root, aliases)
        # Le soutenu décrit une arme mais s'écrit dans les fichiers de
        # vaisseau : il lui faut les deux familles chargées.
        counts["contenances"] = loaders.load_contenances(con, root)
        counts["dps_soutenu"] = loaders.load_dps_soutenu(con, root)
        counts["composants_exposes"] = loaders.load_composants_exposes(
            con, root)
        echo(f"items    {counts['items']:>7}")

        bp, ingredients = loaders.load_blueprints(con, root, aliases)
        counts["blueprints"], counts["ingredients"] = bp, ingredients
        echo(f"bluepr.  {bp:>7}   ingrédients {ingredients}")

        res, locs, links = loaders.load_resources(con, root, aliases)
        counts["resources"], counts["locations"], counts["resource_locations"] = res, locs, links
        echo(f"ressourc {res:>7}   lieux {locs}   liens {links}")

        commodities, shops = loaders.load_commerce(con, root, aliases)
        # Après le commerce : les flux apparient leurs tags aux commodités.
        counts["trade_flows"] = loaders.load_trade_flows(con, root)
        counts["commodities"], counts["shops"] = commodities, shops
        echo(f"commerce {commodities:>7}   boutiques {shops}")

        counts["starmap"] = loaders.load_starmap(con, root, aliases)
        echo(f"starmap  {counts['starmap']:>7}")

        counts["factions"] = loaders.load_factions(con, root, aliases)
        echo(f"factions {counts['factions']:>7}")

        counts["manufacturers"] = loaders.load_manufacturers(con, root, aliases)
        echo(f"constructeurs {counts['manufacturers']:>2}")

        # Les contrats après le starmap : ils s'en servent pour retrouver leur
        # système quand le nom de debug ne le porte pas.
        contracts, reputation = loaders.load_contracts(con, root, aliases, labels)
        counts["contracts"], counts["reputation"] = contracts, reputation
        counts["mission_sites"] = loaders.load_sites_de_mission(con, labels)
        counts["methodes"] = loaders.load_methodes_de_raffinage(con, labels)
        counts["mission_groups"] = con.execute(
            "SELECT COUNT(*) FROM mission_groups").fetchone()[0]
        echo(f"contrats {contracts:>7}   réputation {reputation}   "
             f"groupes {counts['mission_groups']}")

        counts["aliases"] = aliases.flush(con)
        counts["guild_aliases"] = _load_guild_aliases(con, aliases, config.GUILD_ALIASES)
        if counts["guild_aliases"]:
            aliases.flush(con)
        echo(f"alias    {counts['aliases']:>7}   dont guilde {counts['guild_aliases']}")

        con.execute(
            "UPDATE ingest_runs SET finished_at = ?, status = 'ok' WHERE id = ?",
            (_utcnow(), run),
        )
        con.commit()
        con.execute("PRAGMA optimize")
        con.execute("VACUUM")
    except Exception as exc:
        con.execute(
            "UPDATE ingest_runs SET finished_at = ?, status = 'failed', error = ? "
            "WHERE id = ?",
            (_utcnow(), f"{type(exc).__name__}: {exc}", run),
        )
        con.commit()
        con.close()
        raise
    con.close()

    # Bascule. Le WAL du staging est déjà replié par le VACUUM.
    #
    # Sous Windows, un fichier ouvert par un autre processus ne peut être ni
    # supprimé ni remplacé — et `disco serve` garde la base ouverte en
    # permanence. L'erreur brute est un `PermissionError` sur un `.db-wal`, ce
    # qui n'aide personne à comprendre qu'il faut arrêter un service. Toute
    # l'ingestion est déjà faite à ce stade : le fichier `.building.db` est
    # complet et valide, seule la bascule manque.
    try:
        for suffix in ("-wal", "-shm"):
            pathlib.Path(str(staging) + suffix).unlink(missing_ok=True)
            pathlib.Path(str(target) + suffix).unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        staging.replace(target)
    except PermissionError as exc:
        raise RuntimeError(
            f"La base est ouverte par un autre processus, la bascule est "
            f"impossible.\n"
            f"Arrête « disco serve » et « disco discord », puis relance "
            f"l'ingestion.\n"
            f"Rien n'est perdu : la nouvelle base est complète dans "
            f"{staging.name}.\n"
            f"(détail : {exc})"
        ) from None

    return Report(state=st, counts=counts, seconds=time.perf_counter() - started)


def _colonnes_attendues() -> set[str]:
    """Colonnes déclarées par schema.sql, toutes tables confondues.

    Sert d'empreinte grossière mais suffisante du schéma : ce qui compte est
    de détecter une colonne **ajoutée par nous**, pas un changement cosmétique.
    """
    texte = (pathlib.Path(db.__file__).parent / "schema.sql").read_text(encoding="utf-8")
    return set(re.findall(r"^\s{2}([a-z_]+)\s+(?:TEXT|REAL|INTEGER|BLOB)",
                          texte, re.M))


def needs_reingest(db_path: pathlib.Path | None = None,
                   source_dir: pathlib.Path | None = None) -> tuple[bool, str]:
    """La garde du §4 : comparer le *numéro de build*, pas la version.

    Le build bouge aussi aux mid-patch drops, où CIG livre missions et passes
    d'équilibrage sans changer la version sémantique.

    Mais le build amont n'est pas la seule raison de réingérer : **notre propre
    schéma change aussi**. Le jour où l'extraction des statistiques de bouclier
    a ajouté sept colonnes, l'ingestion a répondu « déjà à jour » et la base
    est restée sans elles — un silence d'autant plus trompeur que tout
    fonctionnait, en moins bien.
    """
    target = db_path or config.DB_PATH
    remote = source.remote_state(source_dir or config.SOURCE_DIR)
    if not target.exists():
        return True, f"aucune base en place, source en {remote.label}"
    con = db.connect(target, read_only=True)
    try:
        row = con.execute(
            "SELECT build_id, commit_subject FROM ingest_runs "
            "WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        presentes = {
            r["name"]
            for (table,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")
            # `table_info` masque les colonnes générées : `autonomie_vol`
            # existait bien après chaque ingestion, mais la garde la déclarait
            # absente et réingérait sans fin. `table_xinfo` rend les mêmes
            # colonnes plus celles dont `hidden` vaut 2 ou 3.
            for r in con.execute(f"PRAGMA table_xinfo({table})")
        }
    finally:
        con.close()
    if row is None:
        return True, "aucune ingestion réussie enregistrée"

    manquantes = _colonnes_attendues() - presentes
    if manquantes:
        apercu = ", ".join(sorted(manquantes)[:4])
        return True, f"schéma incomplet ({len(manquantes)} colonnes : {apercu}…)"

    if row["build_id"] != remote.build_id:
        return True, f"build {row['build_id']} -> {remote.build_id}"
    return False, f"déjà à jour ({row['commit_subject']})"
