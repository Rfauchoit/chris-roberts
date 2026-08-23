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
import re
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

#: **Une clé qui est un identifiant n'est pas un champ.** Premier
#: balayage : 128 145 chemins sur les seuls fichiers racine, parce que
#: `tags.json` est un dictionnaire **indexé par UUID** — un chemin par
#: tag, 18 844 fois deux. Le résultat était illisible et faux : ces
#: 128 145 « champs » n'en sont que deux.
MARQUE_CLE = "{}"
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _est_identifiant(cle: str) -> bool:
    """Un UUID, un nombre, ou un nom de classe manifestement unique."""
    if _UUID.match(cle) or cle.isdigit():
        return True
    # `aegs_avenger_stalker`, `behr_rifle_ballistic_01` : des clés de
    # catalogue, pas des noms de champ. Elles sont longues, en minuscules
    # et pleines de tirets bas — un champ ne l'est jamais.
    return len(cle) > 18 and cle.islower() and cle.count("_") >= 2


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
            nom = MARQUE_CLE if _est_identifiant(cle) else cle
            chemin = f"{prefixe}.{nom}" if prefixe else nom
            yield from _chemins(valeur, chemin, profondeur + 1)
    elif isinstance(noeud, list):
        if noeud:
            # Une liste n'est pas nécessairement homogène. Le premier
            # inventaire ne lisait que ``noeud[0]`` : un champ présent dans
            # le deuxième port, composant ou état disparaissait donc d'un
            # rapport pourtant qualifié d'exhaustif. Les indices restent
            # écrasés, mais l'union des formes de **tous** les éléments est
            # parcourue.
            formes: dict[tuple[str, str], Any] = {}
            for element in noeud:
                for chemin, type_, valeur in _chemins(
                        element, f"{prefixe}{MARQUE_LISTE}", profondeur + 1):
                    cle = (chemin, type_)
                    # Une occurrence décrit la **forme de la liste**, pas le
                    # nombre de ses éléments. Garder un exemple non vide sans
                    # émettre mille fois le même chemin borne le coût des
                    # grands tableaux.
                    if cle not in formes or formes[cle] in (None, "", 0):
                        formes[cle] = valeur
            for (chemin, type_), valeur in formes.items():
                yield chemin, type_, valeur
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
        lus = ignores = erreurs = 0
        agrege: dict[tuple[str, str], dict[str, Any]] = {}

        for source, fichier in _sources(racine):
            cle_fichier = f"{source}/{fichier.name}"
            if cle_fichier in vus:
                ignores += 1
                continue
            try:
                donnees = json.loads(fichier.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                # Illisible n'est pas traité. L'inscrire dans ``fichiers_vus``
                # empêchait de retenter un JSON réparé tant que le build ne
                # changeait pas — exactement le contraire d'une reprise.
                erreurs += 1
                echo(f"    illisible : {cle_fichier} ({exc})")
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
        return {"lus": lus, "deja_vus": ignores, "erreurs": erreurs,
                "chemins": total,
                "secondes": round(time.time() - depart, 1), "build": build}
    finally:
        con.close()


def _vider(con: sqlite3.Connection, agrege: dict) -> None:
    """Fusionne le lot en base — additif, pour que la reprise cumule."""
    for (source, chemin), entree in agrege.items():
        ancienne = con.execute(
            "SELECT types FROM chemins WHERE source = ? AND chemin = ?",
            (source, chemin)).fetchone()
        types = set(entree["types"])
        if ancienne and ancienne[0]:
            types.update(str(ancienne[0]).split(","))
        con.execute(
            "INSERT INTO chemins (source, chemin, occurrences, types, "
            "non_vides, exemple) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(source, chemin) DO UPDATE SET "
            "occurrences = occurrences + excluded.occurrences, "
            "non_vides = non_vides + excluded.non_vides, "
            "types = excluded.types, "
            "exemple = COALESCE(chemins.exemple, excluded.exemple)",
            (source, chemin, entree["occurrences"],
             ",".join(sorted(types)), entree["non_vides"],
             entree["exemple"]))


def lus_par_l_ingestion() -> set[str]:
    """Les fragments de chemin que les chargeurs mentionnent.

    Approximatif **et assumé** : on cherche les noms de champs dans le
    source des chargeurs. Un chemin dont le dernier segment n'y apparaît
    jamais n'est certainement pas lu ; l'inverse n'est pas garanti, et
    c'est la bonne asymétrie — on veut une liste de candidats à examiner,
    pas un verdict automatique.
    """
    import re

    racine = pathlib.Path(__file__).resolve().parent
    # Depuis le découpage de ``loaders.py``, la façade ne contient presque
    # plus aucun accès aux champs amont. Garder une liste de trois fichiers
    # classait donc comme « jamais lus » les champs consommés par les huit
    # domaines. Le dossier ``ingest`` est la frontière architecturale réelle ;
    # un module neuf y entre automatiquement.
    source = "".join(
        chemin.read_text(encoding="utf-8")
        for chemin in sorted((racine / "ingest").glob("*.py")))
    return set(re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]{2,})[\"']", source))


#: **Le bruit du moteur, écarté du rapport.** Un champ non lu n'est pas
#: un manque : `SGeometryResourceParams` est du maillage 3D,
#: `SEntityPhysicsControllerParams` des masses et des frottements de
#: collision. Sans ce crible, le premier rapport était noyé sous 17 058
#: occurrences de paramètres de physique — exactement le défaut d'un
#: détecteur qui crie au loup : on ne le lit plus.
#:
#: Le filtre porte sur le **composant**, pas sur le chemin entier : c'est
#: la granularité à laquelle CIG organise ses données.
BRUIT_MOTEUR = (
    "SGeometryResourceParams", "SEntityPhysicsControllerParams",
    "EntityPhysicalAudioParams", "SAnimationControllerParams",
    "SEntityComponentEffects", "AudioPropagationParams",
    "SurfaceRaindropsComponentParams", "SEntityAudioControllerParams",
    "SInteractionStateMachineParams", "SEntityInteractableParams",
    "SActorUsableParams", "SARDataComponentParams",
    "SEntityComponentSequencerParams", "SAttachableComponentParams",
    "ItemControlComponentParams", "UIBindingsConsumer",
    "SCItemInspectableParams", "Geometry", "Effects", "Audio",
    "MaterialParams", "SEntityComponentPhysicsParams", "Tint", "Icon",
    "Localization", "Emissive",
)


def _est_du_bruit(chemin: str) -> bool:
    return any(marque.lower() in chemin.lower() for marque in BRUIT_MOTEUR)


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
            if _est_du_bruit(chemin):
                continue
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
