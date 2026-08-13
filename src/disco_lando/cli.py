"""CLI texte pur — Phase 1. Aucune brique vocale, c'est délibéré (§11)."""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import time
from collections import Counter

import typer
from rich.console import Console
from rich.table import Table

# **Une console Windows en cp1252 ne doit jamais tuer une commande.** Deux
# fois en deux jours, un simple caractère d'affichage a fait mourir un outil
# entier : « ≈ » dans `verifier`, puis « ↔ » dans `banc` — UnicodeEncodeError
# au fin fond de rich, alors que tout le travail était fait. Corriger caractère
# par caractère est une course perdue ; on remplace l'inaffichable par « ? »
# une fois pour toutes. La sortie se dégrade, elle ne meurt plus.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from . import config, db, journal, queries, render, router, unanswered
from .ingest import run as ingest_run
from .ingest import source as ingest_source
from .resolver import resolve as resolve_entity

app = typer.Typer(add_completion=False, help="Chris Roberts — socle données Star Citizen")
source_app = typer.Typer(help="Dépôt scunpacked-data")
app.add_typer(source_app, name="source")

console = Console()
err = Console(stderr=True, style="bold red")


def _con():
    if not config.DB_PATH.exists():
        err.print(f"Pas de base en {config.DB_PATH}. Lance « disco ingest ».")
        raise typer.Exit(1)
    return db.connect(config.DB_PATH, read_only=True)


def _fmt_size(lo, hi) -> str:
    if lo is None and hi is None:
        return "—"
    if lo == hi or hi is None:
        return f"S{lo}"
    return f"S{lo}-S{hi}"


def _duration(seconds) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60:02d} s"


# ------------------------------------------------------------------ source

@source_app.command("clone")
def source_clone():
    """Clone scunpacked-data (complet, ~1,4 Go de transfert)."""
    path = ingest_source.clone()
    console.print(f"[green]cloné[/] dans {path}")


@source_app.command("status")
def source_status():
    """Compare le build local, le build distant et celui de la base."""
    local = ingest_source.state()
    console.print(f"local    {local.subject}  ({local.commit[:10]})")
    stale, why = ingest_run.needs_reingest()
    tag = "[yellow]réingestion nécessaire[/]" if stale else "[green]à jour[/]"
    console.print(f"base     {tag} — {why}")


# ------------------------------------------------------------------ ingestion

@app.command()
def ingest(
    force: bool = typer.Option(False, "--force", help="Réingérer même si le build n'a pas bougé."),
):
    """Reconstruit la base depuis scunpacked-data.

    Toujours complète, jamais incrémentale : construction dans un fichier
    temporaire puis bascule par renommage.
    """
    if not db.fts_available():
        err.print("SQLite sans FTS5 — le résolveur ne peut pas fonctionner.")
        raise typer.Exit(1)
    if not force and config.DB_PATH.exists():
        try:
            stale, why = ingest_run.needs_reingest()
        except ingest_source.SourceError:
            stale, why = True, "état distant indisponible"
        if not stale:
            console.print(f"[green]rien à faire[/] — {why}")
            return
        console.print(f"[yellow]{why}[/]")

    report = ingest_run.ingest()
    console.print(
        f"\n[green]ingestion terminée[/] en {report.seconds:.1f} s "
        f"— {report.state.label}"
    )


@app.command("verifier")
def verifier_cmd() -> None:
    """Contrôle ce qu'un patch du jeu peut avoir cassé en silence.

    Un patch ne prévient pas : il ajoute un système, renomme un type de lieu,
    déplace une statistique. Le code continue de tourner et rend des réponses
    vides ou fausses — le pire cas, parce qu'il est invisible. Ces contrôles
    testent les hypothèses codées en dur contre la base réelle.
    """
    from . import verifier as verif

    if not config.DB_PATH.exists():
        err.print("pas de base — lance « disco ingest » d'abord.")
        raise typer.Exit(1)
    con = db.connect(read_only=True)
    constats = verif.verifier(con)
    verif.journaliser(constats, echo=console.print)
    ligne, code = verif.resume(constats)
    console.print(f"\n[{'red' if code else 'green'}]{ligne}[/]")
    raise typer.Exit(code)


@app.command("archiver-journal")
def archiver_journal_cmd(
    jusqu_a: str = typer.Option(
        None, "--jusqu-a",
        help="Ne ranger que les échanges antérieurs à cet horodatage "
             "(« 2026-08-06 19:50 »). C'est la borne de la dernière analyse."),
    tout: bool = typer.Option(
        False, "--tout",
        help="Ranger la totalité du journal, y compris ce qui vient d'arriver."),
) -> None:
    """Range les échanges analysés dans `data/journal-analyse.md`.

    Le journal se relit et s'annote à la main : y laisser cent trente échanges
    déjà traités oblige à faire défiler pour atteindre les nouveaux. Ce qui est
    analysé part donc dans une archive, et `journal.md` ne garde que ce qui
    attend encore une remarque.

    **Rien n'est supprimé** : les blocs sont déplacés tels quels, commentaires
    compris. L'archive est le journal complet, dans l'ordre.
    """
    import datetime as dt

    from . import archive_journal

    # **Ranger sans borne emporte ce qui vient d'arriver.** Fait une fois :
    # quatre questions posées pendant que je travaillais sont parties à
    # l'archive sans avoir été lues ni commentées. Rien n'était perdu, mais
    # elles n'étaient plus là où l'utilisateur les attendait. La borne est donc
    # explicite, ou le geste total l'est.
    if not jusqu_a and not tout:
        err.print("précise --jusqu-a « <horodatage de la dernière analyse> », "
                  "ou --tout pour ranger la totalité du journal.")
        raise typer.Exit(1)

    borne = None
    if jusqu_a:
        try:
            borne = dt.datetime.fromisoformat(jusqu_a.replace(" ", "T"))
        except ValueError:
            err.print(f"horodatage illisible : {jusqu_a!r} "
                      "— attendu « 2026-08-06 19:50:00 »")
            raise typer.Exit(1)

    journal_md = config.JOURNAL_LOG
    archive = journal_md.with_name("journal-analyse.md")
    ranges, restants = archive_journal.archiver(journal_md, archive, borne)
    if not ranges:
        console.print("[dim]rien à ranger[/]")
        raise typer.Exit(0)
    console.print(f"[green]{ranges} échanges rangés[/] dans {archive}")
    console.print(f"{restants} en attente de commentaire dans {journal_md}")


@app.command("balayage")
def balayage_cmd(
    sortie: str = typer.Option("data/balayage.md", "--sortie", "-o",
                               help="Où écrire le rapport."),
    profond: bool = typer.Option(False, "--profond",
                                 help="Vérifie aussi la résolution des noms "
                                      "(plusieurs minutes)."),
    tout: bool = typer.Option(False, "--tout",
                              help="Détaille toutes les anomalies, sans plafond."),
    reprendre_a_zero: bool = typer.Option(
        False, "--recommencer",
        help="Ignore un balayage interrompu et repart de zéro."),
) -> None:
    """Passe **toute** la base par le rendu et signale ce qui cloche.

    Les tests prennent des échantillons — quatorze armes, douze contrats. Ils
    prouvent qu'un correctif n'est pas taillé pour son cobaye, jamais qu'il
    tient sur les dix mille autres lignes. Ce balayage rend chaque ligne et
    cherche ce qu'un joueur ne devrait pas lire : une exception, un jeton de
    gabarit, une réponse vide, une ponctuation orpheline, un mot anglais.

    Le rapport est fait pour être **envoyé** : chaque anomalie porte son nom,
    son UUID, ce qui l'a déclenchée et le texte fautif.
    """
    from . import balayage as bal

    if not config.DB_PATH.exists():
        err.print("pas de base — lance « disco ingest » d'abord.")
        raise typer.Exit(1)
    con = db.connect(read_only=True)

    ligne = con.execute("SELECT build_id FROM ingest_runs WHERE status = 'ok' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    build = str(ligne[0]) if ligne else "?"
    chemin = pathlib.Path(sortie)
    etat = pathlib.Path(str(chemin) + ".etat.json")

    if reprendre_a_zero:
        etat.unlink(missing_ok=True)
    reprise = bal.Reprise.charger(etat, build)
    if reprise.terminees:
        console.print(f"[dim]reprise : {', '.join(sorted(reprise.terminees))} "
                      "déjà balayé(es)[/]")

    console.print("[dim]balayage en cours — Ctrl-C pour arrêter proprement[/]")
    desarmer = bal.armer_l_arret()
    try:
        rapport = bal.balayer(con, profond=profond, reprise=reprise,
                              echo=lambda t: console.print(f"[dim]{t}[/]"))
    finally:
        desarmer()

    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(
        bal.ecrire(rapport, con, par_categorie=10_000 if tout else 40),
        encoding="utf-8")

    # Les taux de couverture (par exemple un briefing explicitement signalé
    # en anglais) sont des observations, pas des défauts : le rapport les
    # conserve, mais ils ne doivent ni jaunir le bilan ni faire sortir la
    # commande en erreur.
    total = rapport.total_anomalies
    balayees = sum(rapport.balayes.values())
    if rapport.interrompu:
        # Ce qui est fait est gardé : relancer la même commande reprend à la
        # passe suivante. Un balayage nocturne doit survivre à un Ctrl-C.
        reprise.rapport = rapport
        reprise.enregistre()
        console.print(f"\n[yellow]arrêté pendant « {rapport.interrompu} »[/] — "
                      f"{balayees} lignes balayées, {total} anomalies.")
        console.print("relance la même commande pour reprendre.")
    else:
        reprise.efface()
        if total:
            console.print(f"\n[yellow]{total} anomalies[/] sur {balayees} lignes :")
            for categorie, n in rapport.comptes.most_common():
                if "taux" not in categorie:
                    console.print(f"   {categorie:<14} {n}")
        else:
            console.print(f"\n[green]aucune anomalie[/] sur {balayees} lignes.")
        for categorie, n in rapport.comptes.most_common():
            if "taux" in categorie:
                console.print(f"   {categorie:<32} {n}")
    console.print(f"\nrapport écrit dans [bold]{chemin}[/]")
    raise typer.Exit(1 if total else 0)


@app.command("publier-base")
def publier_base_cmd() -> None:
    """Prépare la base de jeu pour que d'autres l'utilisent.

    **scunpacked ne sert qu'à construire la base** : un joueur qui veut
    poser des questions n'a aucune raison de cloner plusieurs gigaoctets
    puis d'attendre une ingestion pour arriver au même fichier. Mesuré :
    390 Mo bruts, 368 après `VACUUM`, **79 Mo compressés** en six
    secondes.

    Le contrôle des tables de guilde est **bloquant**. C'est le point où
    une erreur exposerait des gens, et une conviction ne suffit pas.
    """
    from . import distribution

    try:
        manifeste = distribution.preparer()
    except ValueError as exc:
        console.print(f"[red]publication refusée[/] — {exc}")
        raise typer.Exit(1) from exc
    console.print(
        f"  build               {manifeste['build_id']} "
        f"({manifeste['game_version']})")
    console.print(
        f"  archive             "
        f"{manifeste['octets_compresse'] / 1048576:.1f} Mo "
        f"(depuis {manifeste['octets_decompresse'] / 1048576:.0f} Mo)")
    console.print(f"  empreinte           {manifeste['sha256'][:16]}…")
    console.print(f"\nprêt dans [bold]{config.DATA_DIR / 'publication'}[/]")


@app.command("usages")
def usages_cmd(
    json_: bool = typer.Option(False, "--json", help="Sortie brute."),
) -> None:
    """Ce que les Chris installés chez les joueurs nous ont appris.

    **La feuille de route écrite par les joueurs plutôt que par nous.**
    Le corpus des bancs vient d'un seul joueur et de sa guilde ; ces
    tours-là viennent de gens qui ne connaissent pas les outils, donc qui
    formulent autrement — c'est exactement ce qui manque au corpus.

    Trois angles : ce qui **rate** (à router), ce qui est **lent** (parti
    chez l'analyste alors que le déterministe devrait servir), et la
    répartition par outil (ce qui sert vraiment).
    """
    from . import usages as mod_usages

    mesure = mod_usages.bilan()
    if json_:
        console.print_json(data=mesure)
        raise typer.Exit(0)
    console.print(f"  instances           {mesure['instances']}")
    console.print(f"  tours reçus         {mesure['tours']}")
    if mesure["ratees"]:
        console.print("\n  [bold]Ce qui n'a pas abouti[/] :")
        for ligne in mesure["ratees"][:15]:
            console.print(f"    {ligne['fois']:>3}× « {ligne['question'][:64]} »")
    if mesure["lentes"]:
        console.print("\n  [bold]Ce qui a coûté du quota[/] "
                      "(plus de 5 s, donc l'analyste) :")
        for ligne in mesure["lentes"][:10]:
            console.print(f"    {ligne['ms']:>6} ms — "
                          f"« {ligne['question'][:60]} »")
    if mesure["outils"]:
        console.print("\n  [bold]Outils les plus servis[/] :")
        for ligne in mesure["outils"][:10]:
            console.print(f"    {ligne['fois']:>4}× {ligne['outil']}")


@app.command("questions")
def questions_cmd(
    depuis: str = typer.Option(
        "", "--depuis", help="Date ISO minimale, « 2026-08-13 »."),
    canal: str = typer.Option(
        "", "--canal", help="api, discord, guilde-web, texte…"),
    outil: str = typer.Option("", "--outil", help="Filtre sur l'outil retenu."),
    ratees: bool = typer.Option(
        False, "--ratees", help="Seulement ce qui n'a pas abouti."),
    lentes: int = typer.Option(
        0, "--lentes", help="Seulement au-dessus de N millisecondes."),
    json_: bool = typer.Option(
        False, "--json", help="Sortie brute, pour analyse."),
    limite: int = typer.Option(0, "--limite", help="0 = tout."),
) -> None:
    """Toutes les questions posées, quel que soit le canal.

    **Elles étaient éparpillées en trois endroits** : `journal.md` (ce qui
    attend une annotation), `journal-analyse.md` (l'archivé) et
    `unanswered.jsonl` (les seuls échecs). Trois fichiers, trois formats,
    et il fallait les recouper à la main pour répondre à « montre-moi ce
    qui a mal tourné ». Demande de l'utilisateur, 2026-08-13.

    Rien n'échappe au journal : tous les frontends passent par `/ask`, et
    c'est `api.py` qui consigne — le contrôle en est le test
    `test_tous_les_canaux_ecrivent_au_journal`.
    """
    from . import journal

    entrees = journal.read_all()
    if depuis:
        entrees = [e for e in entrees if (e.get("at") or "") >= depuis]
    if canal:
        entrees = [e for e in entrees
                   if canal.lower() in (e.get("source") or "").lower()]
    if outil:
        entrees = [e for e in entrees
                   if outil.lower() in (e.get("detail") or "").lower()]
    if ratees:
        # Un échec se lit dans le détail — « None », « no_intent » — ou
        # dans la réponse elle-même quand le bot le dit en toutes lettres.
        entrees = [e for e in entrees
                   if "pas compris" in (e.get("answer") or "").lower()
                   or "no_intent" in (e.get("detail") or "")
                   or not (e.get("detail") or "").strip()]
    if lentes:
        def _ms(entree: dict) -> float:
            trouve = re.search(r"(\d+(?:\.\d+)?) ms", entree.get("detail") or "")
            return float(trouve.group(1)) if trouve else 0.0
        entrees = [e for e in entrees if _ms(e) >= lentes]
    if limite:
        entrees = entrees[-limite:]

    if json_:
        console.print_json(data=entrees)
        raise typer.Exit(0)

    console.print(f"[bold]{len(entrees)}[/] question(s)\n")
    for entree in entrees:
        quand = (entree.get("at") or "")[:16]
        source = entree.get("source") or "?"
        console.print(f"[dim]{quand} · {source}[/]")
        console.print(f"  [bold]{entree.get('question', '')}[/]")
        detail = (entree.get("detail") or "").strip()
        if detail:
            console.print(f"  [dim]{detail}[/]")
        commentaire = (entree.get("comment") or "").strip()
        if commentaire:
            console.print(f"  [yellow]remarque : {commentaire}[/]")
        console.print()


@app.command("banc-conversation")
def banc_conversation_cmd() -> None:
    """Rejoue les conversations réelles du journal, et dit où elles cassent.

    **La moitié du modèle qu'on ne mesurait pas.** `disco banc` juge 246
    questions isolées à 100 %, mais les 170 questions de suite du corpus
    n'avaient aucune mesure — 22 tours joués dans toute la suite de tests.
    Les trois défauts du 2026-08-13 étaient tous là. Voir
    `docs/REVUE_DETERMINISTE.md`.

    Ce qui compte n'est pas le routage mais la **cassure** : un tour qui
    retombe après un tour servi, c'est-à-dire un contexte perdu.
    """
    from . import banc

    con = db.connect(read_only=True)
    mesure = banc.rapport_conversations(con, echo=console.print)
    raise typer.Exit(1 if mesure["cassures"] else 0)


@app.command("banc-analyste")
def banc_analyste_cmd(
    limite: int = typer.Option(
        None, "--limite", help="Ne jouer que les N premières questions."),
    filtre: str = typer.Option(
        None, "--filtre", help="Ne jouer que les questions contenant "
                               "cette sous-chaîne."),
) -> None:
    """Mesure l'analyste actif sur treize questions alambiquées vérifiées.

    **Consomme le quota de l'abonnement** — c'est pourquoi il ne se lance
    qu'à la main, jamais en CI ni en pytest. Les attendus numériques se
    recalculent en SQL à l'exécution : le banc survit aux patchs et aux
    rafraîchissements UEX. Compter 20 à 30 s par question avec Sol.
    """
    import os

    from . import banc_analyste

    # La commande est le geste explicite : elle vaut activation, pas
    # besoin d'exiger la variable d'environnement en plus.
    os.environ.setdefault("DISCO_ANALYSTE", "gpt-5.6-sol")
    config.ANALYSTE = config.ANALYSTE or "gpt-5.6-sol"

    con = db.connect(read_only=True)
    rapport = banc_analyste.executer(con, limite=limite, filtre=filtre)
    if "erreur" in rapport:
        console.print(f"[red]{rapport['erreur']}[/]")
        raise typer.Exit(2)
    for r in rapport["resultats"]:
        etat = "[green]ok[/]" if r["ok"] else f"[red]ÉCHEC[/] — {r['detail']}"
        console.print(f"{etat}  ({r['secondes']} s)  {r['question'][:70]}")
        if not r["ok"]:
            console.print(f"    [dim]{r['reponse'][:200]}[/]")
    console.print(f"\n[bold]{rapport['reussis']} / {rapport['total']}[/] "
                  f"en {rapport['secondes']} s")
    raise typer.Exit(0 if rapport["reussis"] == rapport["total"] else 1)


@app.command("banc")
def banc_cmd(
    ombre: bool = typer.Option(
        False, "--ombre",
        help="Route aussi tout le corpus par l'étage 3 local et rapporte "
             "les rattrapages et les désaccords (exige llama-server)."),
) -> None:
    """Mesure le routeur sur tout le corpus connu, au lieu de l'ajuster à l'aveugle.

    Un test dit « ça passe » ; le banc dit « 94 % partent au bon endroit, et
    voici les 6 % ». Il repère aussi les **frontières fragiles** — deux outils
    dont les scores se touchent sur une question réelle se départagent sur
    l'ordre du dictionnaire, c'est-à-dire sur rien.

    À lancer avant et après tout changement de motif ou de poids.
    """
    from . import banc

    if not config.DB_PATH.exists():
        err.print("pas de base — lance « disco ingest » d'abord.")
        raise typer.Exit(1)
    con = db.connect(read_only=True)
    if ombre:
        # L'étage 3 en miroir : les questions qu'il rattrape sont les motifs
        # qui manquent à l'étage 1, et un étage 3 qui ne rattrape plus rien
        # est peut-être mort — il a déjà su mourir deux fois sans un mot.
        banc.ombre(con, echo=console.print)
        raise typer.Exit(0)
    mesure = banc.rapport(con, echo=console.print)
    raise typer.Exit(1 if mesure["erreurs"] else 0)


@app.command("rejouer-journal")
def rejouer_journal_cmd(
    sortie: str = typer.Option(
        "data/rejeu-journal.md", "--sortie", "-o",
        help="Où écrire la trace complète du rejeu."),
) -> None:
    """Rejoue archive et journal courant par la chaîne déterministe.

    Aucun bloc source n'est modifié et aucun analyste n'est appelé. Les
    anciens journaux ne portent pas l'identifiant de conversation : le rapport
    signale explicitement cette limite et utilise la source avec une fenêtre
    de quinze minutes. Les nouveaux blocs portent une empreinte de fil.
    """
    from . import rejeu_journal

    if not config.DB_PATH.exists():
        err.print("pas de base — lance « disco ingest » d'abord.")
        raise typer.Exit(1)
    con = db.connect(read_only=True)
    try:
        resultats = rejeu_journal.rejouer(con)
    finally:
        con.close()
    chemin = pathlib.Path(sortie)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(rejeu_journal.ecrire(resultats), encoding="utf-8")
    comptes = Counter(r.verdict for r in resultats)
    console.print(f"[green]{len(resultats)} échanges rejoués[/]")
    for verdict, n in comptes.most_common():
        console.print(f"  {verdict:<32} {n}")
    console.print(f"rapport écrit dans [bold]{chemin}[/]")
    if comptes[rejeu_journal.A_CORRIGER]:
        raise typer.Exit(1)


# ------------------------------------------------------------------ résolution

@app.command()
def resolve(
    text: str,
    type_: str = typer.Option(None, "--type", "-t", help="Filtrer sur un type d'entité."),
):
    """Montre ce que le résolveur comprend d'un terme. Outil de mise au point."""
    con = _con()
    res = resolve_entity(con, text, entity_types=(type_,) if type_ else None)
    if not res.candidates:
        err.print(f"aucun candidat pour {text!r}")
        raise typer.Exit(1)
    table = Table(title=f"« {text} »", header_style="bold")
    for col in ("entité", "via l'alias", "type", "score", "étage"):
        table.add_column(col)
    for c in res.candidates:
        table.add_row(
            c.name,
            "" if c.alias == c.name else c.alias,
            c.entity_type, f"{c.score:.0f}", c.via,
        )
    console.print(table)
    if res.ambiguous:
        console.print("[yellow]ambigu[/] — plusieurs candidats à portée égale.")


# ------------------------------------------------------------------ questions

@app.command("hardpoints")
def cmd_hardpoints(
    ship: str,
    all_: bool = typer.Option(False, "--all", help="Tous les ports, pas seulement l'armement."),
):
    """« Quels sont les points d'emport d'armes sur un Gladius ? »"""
    con = _con()
    try:
        data = queries.get_ship_hardpoints(con, ship, weapons_only=not all_)
    except queries.NotFound as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None

    s = data["ship"]
    console.print(f"\n[bold]{s['name']}[/]  ({s['manufacturer_name'] or '?'})")
    detail = [s["role"], f"équipage {s['crew']}" if s["crew"] else None,
              f"{s['cargo_scu']:g} SCU" if s["cargo_scu"] else None]
    console.print("  " + " · ".join(d for d in detail if d))

    table = Table(header_style="bold", show_lines=False)
    for col in ("point d'emport", "taille", "monté", "catégorie"):
        table.add_column(col)

    def emit(node, depth=0):
        prefix = "   " * depth + ("└ " if depth else "")
        table.add_row(
            prefix + (node["hardpoint_name"] or node["port_id"]),
            _fmt_size(node["min_size"], node["max_size"]),
            node["installed_name"] or "[dim](vide)[/]",
            node["category"],
        )
        for child in node["children"]:
            emit(child, depth + 1)

    for mount in data["mounts"]:
        emit(mount)
    console.print(table)
    console.print("  " + " · ".join(f"{k} {v}" for k, v in data["totals"].items()))
    if s["pilot_dps"]:
        console.print(f"  DPS pilote {s['pilot_dps']:g} · alpha {s['pilot_alpha']:g}")


@app.command("blueprint")
def cmd_blueprint(item: str):
    """« Comment on fabrique un P6-LR ? »"""
    con = _con()
    try:
        data = queries.get_blueprint(con, item)
    except queries.NotFound as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None

    bp = data["blueprint"]
    console.print(f"\n[bold]{bp['output_name']}[/]  ({bp['output_type'] or '?'})")
    for tier in data["tiers"]:
        console.print(f"  fabrication : {_duration(tier['craft_time_seconds'])}")
        table = Table(header_style="bold")
        for col in ("emplacement", "ingrédient", "quantité"):
            table.add_column(col)
        for group in tier["groups"]:
            for i, opt in enumerate(group["options"]):
                if opt["quantity_scu"] is not None:
                    qty = f"{opt['quantity_scu']:g} SCU"
                elif opt["quantity_units"] is not None:
                    qty = f"×{opt['quantity_units']}"
                else:
                    qty = "—"
                table.add_row(group["name"] if i == 0 else "", opt["ref_name"], qty)
        console.print(table)

    if data["mission_count"]:
        console.print("  blueprint obtenu en récompense de :")
        for groupe in data["mission_groups"]:
            rangs = ""
            if groupe["rank_min"]:
                rangs = (f" · rangs {groupe['rank_min']} → {groupe['rank_max']}"
                         if groupe["rank_max"] != groupe["rank_min"]
                         else f" · rang {groupe['rank_min']}")
            couverture = "" if groupe["complete"] else \
                f" · {groupe['mission_count']}/{groupe['group_total']} des missions"
            console.print(
                f"    · [bold]{groupe['mission_giver']}[/] à "
                f"[bold]{groupe['system'] or 'système indéterminé'}[/]"
                f"{' — ' + groupe['activity'] if groupe['activity'] else ''}"
                f"{rangs}{couverture}"
            )
            for titre in groupe["titles"][:4]:
                console.print(f"        [dim]{titre}[/]")
            if len(groupe["titles"]) > 4:
                console.print(f"        [dim]… et {len(groupe['titles']) - 4} autres[/]")
    elif data["sources"]:
        pools = ", ".join(src["pool_key"] for src in data["sources"])
        console.print(f"  [dim]pool de récompense {pools}, aucune mission sortie ne le distribue[/]")
    elif bp["available_by_default"]:
        console.print("  [dim]disponible par défaut[/]")
    else:
        console.print("  [dim]aucune source de blueprint renseignée dans les données[/]")

    if data["dismantle"]:
        returns = ", ".join(
            f"{d['ref_name']} {d['quantity_scu']:g} SCU" for d in data["dismantle"])
        console.print(f"  démantèlement ({_duration(bp['dismantle_seconds'])}) : {returns}")


@app.command("resource")
def cmd_resource(resource: str):
    """« Où je trouve du Quantanium ? »"""
    con = _con()
    try:
        data = queries.where_to_find_resource(con, resource)
    except queries.NotFound as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None

    for deposit in data["deposits"]:
        r = deposit["resource"]
        console.print(f"\n[bold]{r['name']}[/]  [dim]{r['kind']}"
                      f"{' · ' + r['tier'] if r['tier'] else ''}[/]")
        if deposit["composition"]:
            parts = ", ".join(
                f"{c['part_name']} {c['min_pct']:g}-{c['max_pct']:g} %"
                for c in deposit["composition"] if c["max_pct"] is not None)
            if parts:
                console.print(f"  composition : {parts}")

        table = Table(header_style="bold")
        for col in ("lieu", "système", "groupe", "p. relative"):
            table.add_column(col)
        montres = deposit["locations"][:25]
        for loc in montres:
            table.add_row(loc["name"], loc["system"] or "—",
                          loc["group_name"] or "—",
                          f"{loc['probability']:.2e}" if loc["probability"] else "—")
        console.print(table)
        if deposit["location_count"] > len(montres):
            console.print(f"  [dim]… et {deposit['location_count'] - len(montres)} "
                          f"autres lieux[/]")
        console.print("  [dim]probabilités de spawn, pas un rendement de minage[/]")

    if data["trade"]:
        sold = sum(1 for t in data["trade"] if t["direction"] == "sold_at")
        bought = len(data["trade"]) - sold
        console.print(f"\n  commerce : vendu dans {sold} lieux, racheté dans {bought}")
        console.print("  [dim]les prix ne sont pas dans scunpacked (UEX, phase ultérieure)[/]")


@app.command("mission")
def cmd_mission(
    mission: str,
    include_unreleased: bool = typer.Option(
        False, "--wip", help="Inclure les contrats non sortis."),
):
    """« Il faut quelle réputation pour cette mission ? »"""
    con = _con()
    try:
        data = queries.get_mission_reputation(con, mission,
                                              include_unreleased=include_unreleased)
    except queries.NotFound as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None

    c = data["contract"]
    console.print(f"\n[bold]{c['title'] or c['debug_name']}[/]")
    meta = [c["mission_type"], f"donneur {c['mission_giver']}" if c["mission_giver"] else None,
            "[red]illégale[/]" if c["illegal"] else None]
    console.print("  " + " · ".join(m for m in meta if m))
    if c["not_for_release"] or c["work_in_progress"]:
        console.print("  [yellow]contenu non sorti[/]")

    if data["prerequisites"]:
        console.print("\n  [bold]réputation requise[/]")
        for p in data["prerequisites"]:
            span = f"{p['min_standing_name']} ({p['min_standing_value']})"
            if p["max_standing_name"]:
                span += f" → {p['max_standing_name']} ({p['max_standing_value']})"
            console.print(f"    · {p['faction_name']} [{p['scope']}] : {span}")
    else:
        console.print("\n  [green]aucun prérequis de réputation[/]")

    if data["gained"]:
        console.print("  [bold]réputation gagnée[/]")
        for g in data["gained"]:
            console.print(f"    · {g['faction_name']} [{g['scope']}] : "
                          f"+{g['amount']} ({g['tier'] or '—'})")

    if data["locations"]:
        names = sorted({loc["location_name"] for loc in data["locations"] if loc["location_name"]})
        console.print(f"  disponible : {', '.join(names[:12])}"
                      + (" …" if len(names) > 12 else ""))


@app.command()
def ask(
    question: str,
    show_data: bool = typer.Option(False, "--data", help="Afficher le ToolCall en détail."),
):
    """Pose une question en langage naturel — la chaîne complète de la Phase 2.

    Même chemin que POST /ask : routeur, exécution, mise en forme française.
    """
    con = _con()
    started = time.perf_counter()
    call = router.route(con, question)
    if call is None:
        unanswered.record(question, "no_intent")
        texte = "Je n'ai pas compris la question."
        journal.record(question, texte, source="texte", answered=False,
                       elapsed_ms=(time.perf_counter() - started) * 1000)
        err.print(f"{texte} [consignée dans le journal]")
        raise typer.Exit(1)

    try:
        data = router.execute(con, call)
    except queries.NotFound:
        unanswered.record(question, "no_data", tool=call.tool, entity=str(call.args))
        texte = "Question comprise mais donnée introuvable."
        journal.record(question, texte, source="texte", tool=call.tool,
                       entity=str(call.args), confidence=call.confidence,
                       via=call.via, answered=False,
                       elapsed_ms=(time.perf_counter() - started) * 1000)
        err.print(f"Question comprise ({call.tool}) mais donnée introuvable. "
                  f"[consignée dans le journal]")
        raise typer.Exit(1) from None

    elapsed = (time.perf_counter() - started) * 1000
    reponse = render.render(call.tool, data)
    resolution = data.get("resolution")
    journal.record(question, reponse, source="texte", tool=call.tool,
                   entity=resolution.best.name if resolution and resolution.best else None,
                   confidence=call.confidence, via=call.via, elapsed_ms=elapsed)
    console.print(f"\n[bold]{reponse}[/]")
    console.print(
        f"\n[dim]{call.tool} · entité « {call.args[router.TOOLS[call.tool].arg]} » "
        f"· confiance {call.confidence:.2f} · {call.via} · {elapsed:.0f} ms[/]"
    )
    if show_data:
        console.print(f"[dim]intent {call.intent_score:.1f} · "
                      f"entité {call.entity_score:.1f}[/]")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="127.0.0.1 en local, 0.0.0.0 pour la VM."),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, "--reload"),
):
    """Lance la Core API."""
    try:
        import uvicorn
    except ImportError:
        err.print("uvicorn absent — « pip install -e . » pour installer les dépendances API.")
        raise typer.Exit(1) from None
    if not config.DB_PATH.exists():
        err.print(f"Pas de base en {config.DB_PATH}. Lance « disco ingest ».")
        raise typer.Exit(1)
    console.print(f"Core API sur http://{host}:{port}  ·  doc sur /docs")
    uvicorn.run("disco_lando.api:app", host=host, port=port, reload=reload)


@app.command("guilde-publier")
def guilde_publier(
    port: int = typer.Option(8000, help="Port local du cœur à publier."),
):
    """Ouvre un lien HTTPS de test vers le cockpit et le compagnon.

    La commande reste au premier plan : Ctrl+C referme le tunnel. C'est le
    pendant automatisable du bouton « Ouvrir aux membres » du lanceur, utile
    pour vérifier la vraie URL avant de la partager.
    """
    from . import tunnel

    try:
        ouvert = tunnel.ouvrir(port=port)
    except RuntimeError as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None
    console.print(f"[green]Cockpit[/]        {ouvert.cockpit}")
    console.print(f"[green]Serveur[/]        {ouvert.url}")
    console.print(f"[green]Compagnon[/]      {ouvert.compagnon}")
    if ouvert.rapide:
        console.print("[yellow]Lien de test temporaire : il changera au "
                      "prochain démarrage.[/]")
    console.print("[dim]Ctrl+C referme l'accès.[/]")
    try:
        while ouvert.vivant:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        ouvert.fermer()
    console.print("[dim]accès refermé[/]")


@app.command("guilde-lier")
def guilde_lier(
    code: str = typer.Argument(..., help="Code à usage unique donné par le bot."),
    handle: str = typer.Argument(..., help="Handle RSI lu dans le Game.log."),
    serveur: str = typer.Option(
        None, "--serveur", help="URL du serveur de guilde."),
    appareil: str = typer.Option("PC principal", "--appareil"),
):
    """Lie ce PC au profil de guilde et détecte Star Citizen."""
    from .guilde import collecteur

    try:
        configuration = collecteur.lier(
            code, handle, serveur=serveur, appareil=appareil)
    except collecteur.CollecteurInvalide as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None
    console.print(
        f"[green]PC lié[/] à {configuration.rsi_handle} · "
        f"{len(configuration.installations)} installation(s) détectée(s)")
    console.print("Le jeton reste dans la configuration locale du collecteur.")


@app.command("guilde-importer")
def guilde_importer():
    """Lit l'historique Game.log, remplit la file et synchronise la guilde."""
    from .guilde import collecteur

    try:
        configuration = collecteur.charger()
        con = collecteur.connect()
        try:
            rapport = collecteur.cycle(con, configuration)
        finally:
            con.close()
    except collecteur.CollecteurInvalide as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None
    console.print(
        f"[green]{rapport.evenements_envoyes} événement(s) synchronisé(s)[/] "
        f"· {rapport.evenements_ajoutes} nouveau(x) · "
        f"{rapport.en_attente} en attente")


@app.command("guilde-relire")
def guilde_relire():
    """Oublie les offsets et relit tous les Game.log encore présents."""
    from .guilde import collecteur

    try:
        configuration = collecteur.charger()
        con = collecteur.connect()
        try:
            oublies = collecteur.oublier_offsets(con)
            console.print(f"{oublies} fichier(s) à relire depuis le début")
            rapport = collecteur.cycle(con, configuration)
        finally:
            con.close()
    except collecteur.CollecteurInvalide as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None
    # Le serveur dédoublonne : « ajoutés » compte ce que la file ne
    # connaissait pas encore, pas ce qui sera réellement nouveau en base.
    console.print(
        f"[green]{rapport.lignes} ligne(s) relue(s)[/] · "
        f"{rapport.evenements_detectes} fait(s) détecté(s) · "
        f"{rapport.evenements_envoyes} envoyé(s) · "
        f"{rapport.en_attente} en attente")


@app.command("guilde-surveiller")
def guilde_surveiller(
    intervalle: float = typer.Option(
        None, "--intervalle", min=0.5,
        help="Secondes entre deux contrôles. Par défaut : 2."),
):
    """Surveille passivement les Game.log et résiste aux coupures réseau."""
    from . import verrou
    from .guilde import collecteur

    # Même garde que le bot : deux surveillants relisent les mêmes journaux
    # et doublent chaque envoi (idempotent côté serveur, mais du travail
    # pour rien et deux fenêtres qui prétendent surveiller).
    try:
        verrou.acquerir("guilde-surveillant")
    except verrou.DejaEnMarche as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None

    try:
        configuration = collecteur.charger()
    except collecteur.CollecteurInvalide as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None
    console.print(
        f"Surveillance de {configuration.rsi_handle} · Ctrl+C pour arrêter")
    collecteur.surveiller(
        configuration, intervalle=intervalle, echo=console.print)


@app.command("guilde-compagnon")
def guilde_compagnon(
    port: int = typer.Option(
        0, "--port", help="Port local. Par défaut : un port libre."),
    ouvrir: bool = typer.Option(
        True, "--ouvrir/--sans-ouvrir",
        help="Ouvrir le navigateur sur la page du compagnon."),
):
    """Ouvre le compagnon de guilde : liaison du PC et lecture du Game.log."""
    from .guilde import compagnon

    url, serveur = compagnon.servir(port, ouvrir=ouvrir)
    console.print(
        f"Compagnon de guilde sur [cyan]{url}[/] · Ctrl+C pour arrêter")
    compagnon.attendre_arret(serveur)
    console.print("Compagnon arrêté.")


@app.command("guilde-code")
def guilde_code(
    identifiant: str = typer.Argument(..., help="Identifiant du compte membre."),
    pseudo: str = typer.Argument(..., help="Pseudo public à afficher."),
    minutes: int = typer.Option(10, min=1, max=60),
):
    """Crée localement un code de liaison (secours sans commande du bot)."""
    from .guilde import store

    con = store.connect()
    try:
        store.initialiser(con)
        code = store.creer_code_liaison(
            con, identifiant, pseudo, duree_minutes=minutes)
    finally:
        con.close()
    console.print(f"Code à usage unique : [bold cyan]{code}[/] ({minutes} min)")


@app.command()
def listen(
    core: str = typer.Option(None, help="URL du cœur. Par défaut DISCO_CORE_URL."),
    hotkey: str = typer.Option(None, help="Touche de push-to-talk."),
    device: str = typer.Option(
        None, "--device", "-d",
        help="Micro : index ou fragment de nom. Voir « disco devices »."),
    preroll_ms: int = typer.Option(
        None, "--preroll-ms",
        help="Son gardé avant l'appui, en ms. 0 désactive le pré-tampon."),
    no_overlay: bool = typer.Option(False, "--no-overlay"),
):
    """Frontend local : push-to-talk, whisper.cpp, Piper, overlay (Phase 4).

    Reste externe au process du jeu — hotkey global et overlay transparent,
    aucune lecture mémoire, aucun hook (§8).
    """
    from . import config
    from .frontends.local import LocalFrontend

    raise typer.Exit(
        LocalFrontend(core_url=core, hotkey=hotkey,
                      device=config._input_device(device or ""),
                      preroll_ms=preroll_ms,
                      use_overlay=not no_overlay, echo=console.print).run()
    )


@app.command("devices")
def cmd_devices():
    """Liste les micros disponibles, pour « disco listen --device »."""
    try:
        import sounddevice
    except ImportError:
        err.print('sounddevice absent — pip install -e ".[local]"')
        raise typer.Exit(1)

    try:
        defaut = sounddevice.query_devices(kind="input")["name"]
    except Exception:                                  # noqa: BLE001
        defaut = None

    for index, info in enumerate(sounddevice.query_devices()):
        if info["max_input_channels"] <= 0:
            continue
        marque = "  ← défaut" if info["name"] == defaut else ""
        console.print(f"  [{index:>2}] {info['name']}"
                      f"  ({info['max_input_channels']} ch){marque}")
    console.print("\nChoisir : [cyan]disco listen --device 41[/] "
                  "ou [cyan]--device Logitech[/]")


@app.command("discord-test")
def cmd_discord_test(
    message: str = typer.Option("Chris Roberts est branché.", help="Texte à publier."),
):
    """Publie un message de test dans le salon, pour vérifier le webhook.

    Sans ça, un webhook mal collé ne se remarque qu'au premier échange raté —
    et comme la publication est volontairement silencieuse en cas de panne
    (la réponse orale, elle, a été donnée), rien ne l'aurait signalé.
    """
    from .frontends.discord import webhook

    if not webhook.available():
        err.print("DISCO_DISCORD_WEBHOOK n'est pas défini.")
        err.print("Salon Discord → Modifier le salon → Intégrations → Webhooks")
        err.print("→ Nouveau webhook → Copier l'URL, puis :")
        err.print('  export DISCO_DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."')
        raise typer.Exit(1)

    # L'URL contient un jeton : on n'en affiche que la forme, jamais le contenu.
    url = config.DISCORD_WEBHOOK
    console.print(f"[dim]webhook : {url[:40]}…[/]")
    if webhook.post("Test", message):
        console.print("[green]publié[/] — regarde ton salon Discord.")
    else:
        err.print("échec — vérifie que l'URL est complète et le webhook non supprimé.")
        raise typer.Exit(1)


@app.command("discord-channels")
def cmd_discord_channels():
    """Liste les salons que le bot voit, avec leurs identifiants.

    Vérifier vaut mieux qu'essayer : un identifiant refusé vient presque
    toujours d'une confusion entre serveur, catégorie et salon, et rien dans
    l'interface Discord ne distingue les trois au moment de la copie.
    """
    import os

    import discord

    jeton = os.environ.get(config.DISCORD_TOKEN_ENV)
    if not jeton:
        err.print(f"{config.DISCORD_TOKEN_ENV} absent — voir docs/PHASE5_DISCORD.md")
        raise typer.Exit(1)

    # Aucun intent privilégié : cette commande doit fonctionner même quand
    # « Message Content » n'est pas encore activé — c'est souvent le moment
    # précis où on cherche son identifiant de salon.
    client = discord.Client(intents=discord.Intents.default())
    vise = config.DISCORD_CHANNEL

    @client.event
    async def on_ready():
        for serveur in client.guilds:
            console.print(f"\n[bold]{serveur.name}[/]  (serveur {serveur.id})")
            salons = serveur.text_channels
            if not salons:
                console.print("  [yellow]aucun salon texte visible — "
                              "vérifie la permission « Voir les salons »[/]")
            for salon in salons:
                retenu = vise in (salon.id, salon.name)
                marque = "  [green]← configuré[/]" if retenu else ""
                console.print(f"  {salon.id}  #{salon.name}{marque}")
        if vise is not None and not any(
            vise in (c.id, c.name) for g in client.guilds for c in g.text_channels
        ):
            console.print(f"\n[yellow]DISCO_DISCORD_CHANNEL = {vise} ne "
                          f"correspond à aucun salon ci-dessus.[/]")
        await client.close()

    try:
        client.run(jeton)
    except discord.LoginFailure:
        err.print(f"Jeton refusé. Vérifie {config.DISCORD_TOKEN_ENV}.")
        raise typer.Exit(1) from None


@app.command("discord")
def cmd_discord(
    core: str = typer.Option(None, help="URL du cœur. Par défaut DISCO_CORE_URL."),
):
    """Bot Discord : wake word, pipeline audio, opt-out (Phase 5)."""
    import os

    from . import verrou
    from .frontends.discord import build_bot

    # Six bots en doublon ont répondu en chœur le 2026-08-11 — un seul
    # à la fois, le second s'arrête en le disant.
    try:
        verrou.acquerir("discord-bot")
    except verrou.DejaEnMarche as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None

    jeton = os.environ.get(config.DISCORD_TOKEN_ENV)
    if not jeton:
        err.print(f"{config.DISCORD_TOKEN_ENV} absent — voir docs/PHASE5_DISCORD.md")
        raise typer.Exit(1)
    try:
        bot = build_bot(core_url=core, echo=console.print)
    except RuntimeError as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None

    import discord

    try:
        bot.run(jeton)
    except discord.PrivilegedIntentsRequired:
        # L'échec le plus probable au premier lancement, et une pile d'appels
        # de quarante lignes n'aide personne à cocher une case. Discord ferme
        # la connexion avec le code 4014 quand un intent privilégié est
        # demandé sans avoir été activé.
        err.print("\n[bold]Discord refuse la connexion : intent non activé.[/]\n")
        err.print("Le déclencheur écrit (« Chris Roberts, <question> ») a besoin de")
        err.print("l'intent privilégié [bold]Message Content[/]. À activer à la main :\n")
        err.print("  https://discord.com/developers/applications")
        err.print("  → ton application → [bold]Bot[/]")
        err.print("  → Privileged Gateway Intents")
        err.print("  → [bold]MESSAGE CONTENT INTENT[/] → activer → Save Changes\n")
        err.print("Puis relance. Si tu préfères t'en passer, désactive le")
        err.print("déclencheur — seules les commandes répondront alors :\n")
        err.print("  [cyan]DISCO_DISCORD_TRIGGERS= disco discord[/]")
        raise typer.Exit(1) from None
    except discord.LoginFailure:
        err.print(f"\n[bold]Jeton refusé.[/] Vérifie {config.DISCORD_TOKEN_ENV}.")
        err.print("Un jeton régénéré dans le portail invalide le précédent.")
        raise typer.Exit(1) from None


@app.command("uex")
def cmd_uex():
    """Rapatrie les prix UEX (Phase 6).

    Les prix ne sont plus dans les fichiers du jeu depuis la 3.20 : c'est la
    seule source programmatique. Stockés à part, horodatés, jamais mélangés
    aux données du jeu.
    """
    from .ingest import uex

    if not uex.available():
        err.print(f"{config.UEX_TOKEN_ENV} absent — crée une application sur "
                  "https://uexcorp.space/api/ pour obtenir un jeton.")
        raise typer.Exit(1)
    con = db.connect(config.DB_PATH)
    try:
        uex.sync(con, echo=console.print)
    except uex.UexError as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None
    finally:
        con.close()


@app.command("wiki")
def cmd_wiki(
    force: bool = typer.Option(
        False, "--force",
        help="Ingérer même si le wiki décrit un autre build que le nôtre."),
):
    """Rapatrie le complément du Star Citizen Wiki.

    Surtout les **descriptions en français** — 278 vaisseaux et 7 149 objets,
    là où les fichiers du jeu ne parlent qu'anglais. Plus le statut de
    production, le prix en argent réel, les vaisseaux de prêt et l'affiliation
    des systèmes.

    Refuse si le wiki ne décrit pas le même build que la base, sauf `--force`.
    """
    from .ingest import wiki

    con = db.connect(config.DB_PATH)
    try:
        if wiki.sync(con, echo=console.print, force=force) == 0:
            raise typer.Exit(1)
    finally:
        con.close()


@app.command("trad")
def cmd_trad(
    force: bool = typer.Option(
        False, "--force",
        help="Retélécharger et ingérer même si la traduction vise un autre patch."),
):
    """Rapatrie la traduction française du Cirque Lisoir (StarTrad).

    Le jeu n'a pas de texte français : `labels.json` n'a que l'anglais. Cette
    communauté traduit le `global.ini` du jeu — le même espace de clés — et
    c'est la **seule** source pour les lieux (2 013 sur 2 032) et les missions.

    Le téléchargement est conditionnel : 11 Mo ne repassent pas sur le réseau
    tant que l'`ETag` n'a pas changé.
    """
    from .ingest import traduction

    con = db.connect(config.DB_PATH)
    try:
        if traduction.sync(con, echo=console.print, force=force) == 0:
            raise typer.Exit(1)
    finally:
        con.close()


@app.command("journal")
def cmd_journal(
    commented_only: bool = typer.Option(
        False, "--commente", help="Ne montrer que les échanges annotés."),
    limit: int = typer.Option(20, help="Nombre d'échanges récents à afficher."),
):
    """Toutes les questions posées, réponses comprises — vocal et texte.

    À la différence de `unanswered`, ce journal garde aussi ce qui a marché :
    une réponse exacte peut être trop longue, mal tournée pour l'oreille, ou
    répondre à côté. Annote-le, il est fait pour ça.
    """
    echanges = journal.commented() if commented_only else journal.read()
    if not echanges:
        console.print(f"[yellow]journal vide[/] — {journal.path()}")
        return

    for entree in echanges[-limit:]:
        console.print(f"\n[dim]{entree['at']} · {entree['source']}[/]")
        console.print(f"[bold]Q.[/] {entree['question']}")
        console.print(f"   {entree['answer'][:200]}")
        if entree["comment"]:
            console.print(f"[cyan]   → {entree['comment']}[/]")

    total = len(journal.read())
    annotes = len(journal.commented())
    console.print(f"\n[dim]{total} échanges, {annotes} annotés[/]")
    console.print(f"[dim]{journal.path()}[/]")


@app.command("unanswered")
def cmd_unanswered(
    limit: int = typer.Option(30, help="Nombre de questions récentes à afficher."),
):
    """Questions restées sans réponse, à relire après une session de test.

    C'est la matière première pour décider quoi construire ensuite : un motif
    de routeur, un alias, un champ amont, ou un module entier.
    """
    resume = unanswered.summary()
    if not resume["total"]:
        console.print(f"[green]aucune question sans réponse[/] — {unanswered.path()}")
        return

    console.print(f"[bold]{resume['total']}[/] questions sans réponse "
                  f"· {unanswered.path()}")
    table = Table(header_style="bold")
    table.add_column("raison")
    table.add_column("n", justify="right")
    table.add_column("ce que ça veut dire")
    sens = {
        "no_intent": "aucune intention reconnue — routeur ou hors périmètre",
        "no_entity": "intention comprise, entité introuvable — alias manquant",
        "no_data": "question comprise, donnée absente — module ou source à ajouter",
    }
    for raison, n in resume["by_reason"].items():
        table.add_row(raison, str(n), sens.get(raison, ""))
    console.print(table)

    console.print("\n[bold]les plus fréquentes[/]")
    for question, n in resume["top_questions"][:limit]:
        console.print(f"  {n:>3} × {question}")


@app.command()
def stats():
    """Contenu de la base et provenance."""
    con = _con()
    run = con.execute(
        "SELECT * FROM ingest_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if run:
        console.print(f"[bold]{run['commit_subject']}[/]  "
                      f"ingéré le {run['finished_at']}  ({run['source_commit'][:10]})")
    table = Table(header_style="bold")
    table.add_column("table")
    table.add_column("lignes", justify="right")
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'aliases_fts%' ORDER BY name")]
    for name in tables:
        count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        table.add_row(name, f"{count:,}".replace(",", " "))
    console.print(table)


def main():
    try:
        sys.exit(app())
    except sqlite3.OperationalError as exc:
        # Sans ce filet, « disco stats » sur une base absente ou d'un ancien
        # schéma meurt en traceback brut — le lecteur ne sait ni où est la
        # base ni quoi lancer. Une erreur SQLite autre que « table manquante »
        # reste un vrai bug : elle repart telle quelle.
        if "no such table" not in str(exc):
            raise
        Console(stderr=True).print(
            f"[red]La base en {config.DB_PATH} est absente ou d'un ancien "
            f"schéma ({exc}).[/]\n"
            "Le remède : « disco source clone » puis « disco ingest » — et "
            "après toute réingestion, « disco uex », « disco wiki » et "
            "« disco trad », sinon prix et traductions restent vides.")
        sys.exit(1)


if __name__ == "__main__":
    main()
