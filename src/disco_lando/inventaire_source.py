"""Lire scunpacked **en entier**, et dire ce qu'il y a dedans.

Reproche de l'utilisateur, 2026-08-13, et il est mérité : les trois
audits précédents ont procédé par **échantillon** — 600 fichiers sur
21 849, puis 800. Un sondage ne prouve rien sur ce qu'il n'a pas tiré, et
c'est ainsi que la contenance de 2 073 objets a dormi jusqu'à hier.

## Ce que ce module fait, et pourquoi c'est différent

Les audits passés répondaient à « **cette** donnée existe-t-elle ? » : on
sait quoi chercher, un échantillon suffit à conclure. Celui-ci répond à
« **qu'y a-t-il ?** », et pour cette question-là il n'existe pas de
raccourci — un champ présent sur trois objets sur 21 849 est invisible
à tout sondage, et peut être exactement celui qui manquait.

On aplatit donc chaque JSON en **chemins** (`Raw.Entity.Components.X.y`),
et on compte. Les indices de liste sont écrasés en `[]` : sans ça, un
tableau de mille entrées produirait mille chemins distincts et noierait
le résultat.

## Les bornes, mesurées

`items/` seul fait 3,4 Go pour 21 849 fichiers. L'aplatissement complet
coûte bien plus cher que la lecture, d'où deux précautions : un plafond
de profondeur (le maillage 3D descend très loin sans rien apprendre), et
une **reprise sur interruption** — un contrôle qu'on n'ose pas lancer est
un contrôle qu'on ne lance pas, règle écrite pour `disco balayage`.

L'état est lié au **build** : une réingestion repart de zéro, parce qu'un
inventaire à cheval sur deux versions du jeu est pire qu'aucun.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import time
from typing import Any, Iterator

from . import config

#: Au-delà, on est dans le maillage, les matrices de transformation et
#: les machines à états d'animation. Mesuré sur les fichiers d'items :
#: la donnée de jeu exploitable ne dépasse jamais huit niveaux.
PROFONDEUR_MAX = 10

#: Les listes sont écrasées : `Ports[0].Name` et `Ports[7].Name`
#: décrivent la même chose.
MARQUE_LISTE = "[]"


def _chemins(noeud: Any, prefixe: str = "",
             profondeur: int = 0) -> Iterator[tuple[str, str, Any]]:
    """Aplatit un JSON en (chemin, type, valeur d'exemple)."""
    if profondeur > PROFONDEUR_MAX:
        return
    if isinstance(noeud, dict):
        for cle, valeur in noeud.items():
            # Les clés techniques de scunpacked (`__ref`, `__path`) sont
            # des pointeurs internes, jamais de la donnée de jeu.
            if cle.startswith("__"):
                continue
            chemin = f"{prefixe}.{cle}" if prefixe else cle
            yield from _chemins(valeur, chemin, profondeur + 1)
    elif isinstance(noeud, list):
        if noeud:
            yield from _chemins(noeud[0], f"{prefixe}{MARQUE_LISTE}",
                                profondeur + 1)
        else:
            yield (prefixe, "liste vide", None)
    else:
        yield (prefixe, type(noeud).__name__, noeud)


def _base(chemin: pathlib.Path | None = None) -> sqlite3.Connection:
    chemin = chemin or pathlib.Path(config.DATA_DIR) / "inventaire-source.db"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(chemin)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS chemins (
            source      TEXT NOT NULL,   -- items, contracts, ships…
            chemin      TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 0,
            types       TEXT,
            non_vides   INTEGER NOT NULL DEFAULT 0,
            exemple     TEXT,
            PRIMARY KEY (source, chemin));
        CREATE TABLE IF NOT EXISTS fichiers_vus (
            fichier TEXT PRIMARY KEY,
            build   TEXT);
        CREATE TABLE IF NOT EXISTS etat (
            cle TEXT PRIMARY KEY, valeur TEXT);
    """)
    return con


def _build_courant() -> str:
    try:
        jeu = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        ligne = jeu.execute("SELECT build_id FROM ingest_runs "
                            "ORDER BY id DESC LIMIT 1").fetchone()
        jeu.close()
        return str(ligne[0]) if ligne else "inconnu"
    except sqlite3.Error:
        return "inconnu"


def _sources(racine: pathlib.Path) -> Iterator[tuple[str, pathlib.Path]]:
    """Tout ce que scunpacked contient, dossiers **et** fichiers racine."""
    for dossier in sorted(p for p in racine.iterdir() if p.is_dir()):
        for fichier in sorted(dossier.glob("*.json")):
            yield (dossier.name, fichier)
    for fichier in sorted(racine.glob("*.json")):
        yield ("racine", fichier)


def balayer(racine: pathlib.Path | None = None, limite: int = 0,
            echo=lambda _m: None) -> dict[str, Any]:
    """Parcourt scunpacked et range chaque chemin rencontré.

    Reprend où il s'était arrêté : `fichiers_vus` porte le build, donc
    une réingestion invalide l'inventaire et le relance de zéro.
    """
    racine = racine or pathlib.Path(config.SOURCE_DIR)
    if not racine.is_dir():
        raise FileNotFoundError(f"source absente : {racine}")
    build = _build_courant()
    con = _base()
    try:
        ancien = con.execute(
            "SELECT valeur FROM etat WHERE cle = 'build'").fetchone()
        if ancien and ancien[0] != build:
            # Un inventaire à cheval sur deux builds est pire qu'aucun.
            con.executescript("DELETE FROM chemins; DELETE FROM fichiers_vus;")
        con.execute("INSERT OR REPLACE INTO etat VALUES ('build', ?)", (build,))
        con.commit()

        vus = {ligne[0] for ligne in con.execute("SELECT fichier FROM fichiers_vus")}
        depart = time.time()
        lus = ignores = 0
        agrege: dict[tuple[str, str], dict[str, Any]] = {}

        for source, fichier in _sources(racine):
            cle_fichier = f"{source}/{fichier.name}"
            if cle_fichier in vus:
                ignores += 1
                continue
            try:
                donnees = json.loads(fichier.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                con.execute("INSERT OR REPLACE INTO fichiers_vus VALUES (?, ?)",
                            (cle_fichier, build))
                continue
            for chemin, type_, valeur in _chemins(donnees):
                entree = agrege.setdefault((source, chemin), {
                    "occurrences": 0, "types": set(), "non_vides": 0,
                    "exemple": None})
                entree["occurrences"] += 1
                entree["types"].add(type_)
                if valeur not in (None, "", 0, [], {}):
                    entree["non_vides"] += 1
                    if entree["exemple"] is None:
                        entree["exemple"] = str(valeur)[:120]
            con.execute("INSERT OR REPLACE INTO fichiers_vus VALUES (?, ?)",
                        (cle_fichier, build))
            lus += 1
            if lus % 2000 == 0:
                _vider(con, agrege)
                agrege.clear()
                con.commit()
                echo(f"    {lus} fichiers, {time.time() - depart:.0f} s")
            if limite and lus >= limite:
                break

        _vider(con, agrege)
        con.commit()
        total = con.execute("SELECT COUNT(*) FROM chemins").fetchone()[0]
        return {"lus": lus, "deja_vus": ignores, "chemins": total,
                "secondes": round(time.time() - depart, 1), "build": build}
    finally:
        con.close()


def _vider(con: sqlite3.Connection, agrege: dict) -> None:
    """Fusionne le lot en base — additif, pour que la reprise cumule."""
    for (source, chemin), entree in agrege.items():
        con.execute(
            "INSERT INTO chemins (source, chemin, occurrences, types, "
            "non_vides, exemple) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(source, chemin) DO UPDATE SET "
            "occurrences = occurrences + excluded.occurrences, "
            "non_vides = non_vides + excluded.non_vides, "
            "types = excluded.types, "
            "exemple = COALESCE(chemins.exemple, excluded.exemple)",
            (source, chemin, entree["occurrences"],
             ",".join(sorted(entree["types"])), entree["non_vides"],
             entree["exemple"]))


def lus_par_l_ingestion() -> set[str]:
    """Les fragments de chemin que `loaders.py` mentionne.

    Approximatif **et assumé** : on cherche les noms de champs dans le
    source des chargeurs. Un chemin dont le dernier segment n'y apparaît
    jamais n'est certainement pas lu ; l'inverse n'est pas garanti, et
    c'est la bonne asymétrie — on veut une liste de candidats à examiner,
    pas un verdict automatique.
    """
    import re

    racine = pathlib.Path(__file__).resolve().parent
    source = ""
    for nom in ("ingest/loaders.py", "ingest/source.py", "ingest/run.py"):
        chemin = racine / nom
        if chemin.exists():
            source += chemin.read_text(encoding="utf-8")
    return set(re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]{2,})[\"']", source))


def rapport(minimum: int = 20, echo=print) -> dict[str, Any]:
    """Ce qui existe, ce qui est lu, ce qui ne l'est pas.

    Le seuil écarte le bruit : un chemin vu sur trois fichiers est le
    plus souvent une bizarrerie d'un objet de test. Il se baisse quand on
    cherche une aiguille précise.
    """
    con = _base()
    try:
        total = con.execute("SELECT COUNT(*) FROM chemins").fetchone()[0]
        if not total:
            echo("  inventaire vide — lance « disco inventaire-source »")
            return {"chemins": 0}
        connus = lus_par_l_ingestion()
        candidats = []
        for source, chemin, occ, non_vides, exemple in con.execute(
                "SELECT source, chemin, occurrences, non_vides, exemple "
                "FROM chemins WHERE non_vides >= ? ORDER BY non_vides DESC",
                (minimum,)):
            dernier = chemin.replace(MARQUE_LISTE, "").split(".")[-1]
            if dernier in connus:
                continue
            candidats.append({"source": source, "chemin": chemin,
                              "occurrences": occ, "non_vides": non_vides,
                              "exemple": exemple})
        echo(f"  chemins inventoriés   {total}")
        echo(f"  jamais lus (≥{minimum} objets)  {len(candidats)}\n")
        for entree in candidats[:40]:
            echo(f"  {entree['non_vides']:6}  {entree['source']:10} "
                 f"{entree['chemin'][:64]}")
            if entree["exemple"]:
                echo(f"          → {entree['exemple'][:88]}")
        return {"chemins": total, "candidats": candidats}
    finally:
        con.close()
