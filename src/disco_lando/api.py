"""Core API — §2 du brief.

« Service HTTP. Entrée = une question en texte. Sortie = réponse texte +
données structurées brutes. **Aucune notion d'audio.** »

Rien ici ne connaît le micro, le wake word ni Discord. Les frontends pointent
vers ce service via variable d'environnement, qu'il tourne en local ou sur la VM.
"""

from __future__ import annotations

import hmac
import logging
import pathlib
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (analyste, config, context, dialogue, erreurs, journal,
               router, unanswered, usages)
from .guilde import api as api_guilde
from .guilde import store as store_guilde
from .resolver import resolve as resolve_entity

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not config.DB_PATH.exists():
        raise RuntimeError(
            f"pas de base en {config.DB_PATH} — lance « disco ingest » d'abord"
        )
    # check_same_thread=False : la base est ouverte en lecture seule et
    # l'ingestion écrit dans un fichier séparé avant bascule. Aucun écrivain
    # concurrent, donc aucun verrou à craindre.
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True,
                          check_same_thread=False)
    con.row_factory = sqlite3.Row
    _state["con"] = con
    row = con.execute(
        "SELECT commit_subject, build_id, finished_at FROM ingest_runs "
        "WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    _state["build"] = dict(row) if row else None
    _state["donnees"] = _completude_donnees(con)
    _state["code"] = _identite_code()
    api_guilde.preparer(con)
    yield
    con.close()


def _completude_donnees(con: sqlite3.Connection) -> dict[str, Any]:
    """Ce que la base servie sait vraiment servir — mesuré au démarrage.

    La bascule v2 du 2026-08-11 a mis en production une base **sans prix
    UEX ni traductions** pendant des heures, et rien ne le disait :
    `get_price` levait NotFound sur tout et `decrire` repassait en anglais —
    le pire cas documenté, parce qu'il est invisible. On compte, on dit.
    """
    comptes: dict[str, int] = {}
    for cle, table in (("uex_prices", "uex_prices"),
                       ("traductions", "traductions"),
                       ("wiki", "wiki_items")):
        try:
            comptes[cle] = int(con.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            comptes[cle] = 0
    manques = sorted(cle for cle, n in comptes.items() if not n)
    return {**comptes, "complet": not manques, "manques": manques}


def _identite_code() -> dict[str, Any]:
    """Le commit du code réellement chargé, et l'heure du démarrage.

    Un cœur resté d'une session précédente répond « ok » avec du code
    périmé, et rien ne le distingue — vécu le 2026-08-11, deux fois dans la
    même soirée. Hors dépôt (exe compilé), le commit est « inconnu » :
    c'est déjà une information.
    """
    import subprocess
    from datetime import UTC, datetime

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=pathlib.Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "inconnu"
    except (OSError, subprocess.SubprocessError):
        commit = "inconnu"
    return {"commit": commit,
            "demarre_le": datetime.now(UTC).isoformat(
                timespec="seconds").replace("+00:00", "Z")}


app = FastAPI(
    title="Chris Roberts — Core API",
    version="0.2.0",
    summary="Questions sur Star Citizen en langage naturel. Entrée texte, sortie texte.",
    lifespan=lifespan,
)
app.include_router(api_guilde.router)


@app.middleware("http")
async def revalider_cockpit_apres_mise_en_production(request, call_next):
    """Empêche un ancien JavaScript de survivre à une nouvelle MEP."""
    response = await call_next(request)
    chemin = request.url.path
    # app.js a été découpé en trois scripts le 2026-08-11 ; chacun doit se
    # revalider, sinon un ancien morceau survit à la mise en production.
    if chemin == "/guilde" or chemin.endswith((".js", "/styles.css")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response

_GUILDE_WEB = pathlib.Path(__file__).with_name("guilde") / "web"
# **Le cockpit n'existe pas dans le Chris public**, et son absence ne doit
# pas empêcher le cœur de démarrer : `StaticFiles` lève à la construction
# sur un dossier manquant, ce qui tuait le binaire distribué avant même
# la première question (mesuré le 2026-08-13). Le cockpit appartient à
# l'atelier ; un joueur n'a ni guilde, ni Game.log, ni membres à suivre.
if _GUILDE_WEB.is_dir():
    app.mount(
        "/guilde/assets", StaticFiles(directory=_GUILDE_WEB),
        name="guilde-assets",
    )


class TourRemonte(BaseModel):
    """Un tour vécu chez un joueur, tel qu'il nous parvient.

    Les champs sont **facultatifs sauf la question** : une instance d'une
    version antérieure doit pouvoir remonter ce qu'elle sait, sans qu'un
    champ ajouté depuis fasse tout rejeter.
    """

    question: str = Field(..., min_length=1, max_length=2000)
    reponse: str | None = None
    outil: str | None = None
    confiance: float | None = None
    via: str | None = None
    duree_ms: float | None = None
    aboutie: bool | None = None
    fil: str | None = Field(None, max_length=64)
    rang: int | None = None
    horodatage: str | None = Field(None, max_length=40)
    version: str | None = Field(None, max_length=32)

    model_config = {"extra": "ignore"}


class RemonteeRequest(BaseModel):
    """Ce qu'une instance envoie : son identifiant anonyme et ses tours."""

    instance: str = Field(..., min_length=4, max_length=64)
    tours: list[TourRemonte] = Field(..., max_length=usages.TOURS_MAX_PAR_ENVOI)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500,
                          examples=["Quels sont les points d'emport du Gladius ?"])
    router_name: str | None = Field(
        None, alias="router",
        description="Force un étage de routeur. Par défaut, DISCO_ROUTER.",
    )
    include_data: bool = Field(
        True, description="Joindre les données structurées brutes."
    )
    # Le cœur ignore la voix et Discord (§2) : il ne reçoit qu'une étiquette
    # libre, qu'il recopie dans le journal. Savoir d'où venait une question
    # change tout à la relecture — une réponse trop longue est un défaut à
    # l'oral et pas à l'écrit.
    # Fil de conversation. Deux questions du même interlocuteur partagent une
    # entité : « où trouver un P4-AR » puis « liste-moi tous les points de
    # vente ». Sans cet identifiant, la seconde n'a aucune entité et échoue.
    session: str | None = Field(
        None, max_length=64,
        description="Identifiant de conversation, pour les questions de suite.",
    )
    source: str | None = Field(
        None, max_length=20,
        description="Étiquette d'origine, reportée telle quelle dans le journal.",
    )
    speaker: str | None = Field(
        None, max_length=80,
        description="Identité liée du demandeur, pour résoudre « moi ».",
    )
    nouvelle_conversation: bool = Field(
        False,
        description="Oublier le fil précédent avant de traiter la question.",
    )


class AskResponse(BaseModel):
    question: str
    answered: bool
    answer: str
    tool: str | None = None
    args: dict[str, Any] | None = None
    confidence: float | None = None
    via: str | None = None
    entity: str | None = None
    elapsed_ms: float
    data: dict[str, Any] | None = None
    # Ce que la réponse vient de proposer. Un frontend qui veut accepter une
    # suite sans mot-clé a besoin de savoir qu'il en attend une — le cœur
    # reste sans notion de Discord, il dit juste « j'ai posé une question ».
    follow_up: list[str] = Field(default_factory=list)


# L'orchestration conversationnelle vit dans `dialogue.py` — une seule
# implémentation, partagée avec le harnais de test. Les ré-exports gardent
# les appelants historiques (tests, scripts) sans copie.
from .dialogue import (  # noqa: E402, F401
    MAX_VARIANTES,
    certitude_insuffisante,
    meme_question_autre_sujet,
    precedent_entite,
    variantes_ambigues,
)


def _con() -> sqlite3.Connection:
    con = _state.get("con")
    if con is None:
        raise HTTPException(503, "base indisponible")
    return con


def _jsonable(value: Any) -> Any:
    """Les objets du résolveur ne sont pas sérialisables tels quels."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "resolution"}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(getattr(value, k)) for k in value.__dataclass_fields__}
    return value


@app.get("/", include_in_schema=False)
def racine() -> RedirectResponse:
    """L'adresse partagée aux membres ouvre l'interface qui leur est destinée.

    La documentation technique reste disponible explicitement sur `/docs`.
    Un tunnel publiait auparavant sa racine, qui y redirigeait : le membre
    recevait donc Swagger au lieu du cockpit et croyait avoir le mauvais lien.
    """
    return RedirectResponse("/guilde")


@app.get("/guilde", include_in_schema=False)
def interface_guilde() -> FileResponse:
    return FileResponse(_GUILDE_WEB / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    routeurs_disponibles = list(router.available())
    routeurs_actifs = [
        nom.strip() for nom in config.ROUTER.split(",") if nom.strip()
    ]
    return {
        "status": "ok",
        "build": _state.get("build"),
        # Complétude mesurée au démarrage : une base sans prix ni français a
        # déjà tourné des heures en production sans que rien ne le dise.
        "donnees": _state.get("donnees"),
        # L'identité du code chargé : démasque un cœur d'une session
        # précédente qui répond « ok » avec un catalogue périmé.
        "code": _state.get("code"),
        # `routers` est gardé pour les anciens frontends. La distinction évite
        # surtout de faire croire que le banc local est actif simplement parce
        # que son implémentation reste importable.
        "routers": routeurs_disponibles,
        "routing": {
            "actifs": routeurs_actifs,
            "disponibles": routeurs_disponibles,
        },
        "tools": list(router.TOOLS),
        "analyste": analyste.etat(),
    }


@app.get("/tools")
def tools() -> list[dict[str, Any]]:
    """Catalogue des outils.

    Servira de descriptions de fonctions au function calling des étages 2 et 3.
    """
    return [
        {"name": t.name, "description": t.description,
         "argument": t.arg, "entity_types": list(t.entity_types)}
        for t in router.TOOLS.values()
    ]


@app.get("/resolve")
def resolve(
    q: str = Query(..., min_length=1),
    type: str | None = Query(None, description="Filtrer sur un type d'entité."),
    limit: int = Query(8, ge=1, le=50),
) -> dict[str, Any]:
    """Mise au point : ce que le résolveur comprend d'un terme."""
    res = resolve_entity(_con(), q, entity_types=(type,) if type else None, limit=limit)
    return {
        "query": q,
        "ambiguous": res.ambiguous,
        "candidates": [
            {"name": c.name, "alias": c.alias, "entity_type": c.entity_type,
             "entity_id": c.entity_id, "score": round(c.score, 1), "via": c.via}
            for c in res.candidates
        ],
    }


#: Les en-têtes qu'un relais ajoute. Leur **présence** suffit à dire que la
#: requête ne vient pas de la machine, même quand l'adresse est 127.0.0.1 :
#: c'est exactement le cas d'un tunnel cloudflared, qui proxifie en local.
_ENTETES_DE_RELAIS = ("x-forwarded-for", "cf-connecting-ip", "x-real-ip",
                      "forwarded")

#: « testclient » est l'hôte que Starlette donne à un appel **en processus** :
#: il n'y a pas de socket, donc rien de plus local. La valeur vient du scope
#: ASGI, posé par le serveur d'après le pair TCP — aucun en-tête ne peut la
#: fabriquer, et l'admettre ici n'ouvre donc rien en production.
_BOUCLE_LOCALE = ("127.0.0.1", "::1", "localhost", "testclient")


def _requete_locale(request: Request) -> bool:
    """La requête vient-elle de cette machine, sans relais ?"""
    if any(h in request.headers for h in _ENTETES_DE_RELAIS):
        return False
    hote = request.client.host if request.client else None
    return hote in _BOUCLE_LOCALE


def _jeton_externe_valide(request: Request) -> bool:
    """Le porteur est-il un appareil lié, ou l'hôte avec son jeton de service ?"""
    presente = (request.headers.get("authorization") or "")
    presente = presente.split(" ", 1)[-1].strip() if presente else ""
    if not presente:
        return False
    if config.API_TOKEN and hmac.compare_digest(presente, config.API_TOKEN):
        return True
    con = store_guilde.connect()
    try:
        return store_guilde.authentifier(con, presente) is not None
    finally:
        con.close()


def _autoriser_question(request: Request) -> None:
    """Qui a le droit de poser une question au cœur.

    **La boucle locale est de confiance** : le bot Discord, le lanceur, la
    voix locale et les tests tournent sur la machine, et quiconque y est a
    déjà la base sous la main. Tout le reste présente un jeton — celui d'un
    appareil lié, ou le jeton de service de l'hôte.
    """
    if config.API_OUVERTE or _requete_locale(request):
        return
    if not _jeton_externe_valide(request):
        raise HTTPException(401, "jeton requis pour poser une question")


#: Ce qui reste public **hors de la machine**, mesuré au besoin réel d'un
#: membre pas encore lié : vérifier l'adresse (`version`), télécharger le
#: compagnon pour se lier, et se lier. Tout le reste des lectures de guilde
#: expose des données de membres — journaux, inventaires, pseudos — et
#: exige donc un appareil lié. Décision de l'utilisateur du 2026-08-11 :
#: l'« acceptation » d'un membre est la liaison Discord, pas un proxy tiers.
_GUILDE_PUBLIQUES = frozenset({
    "/api/guilde/version",
    "/api/guilde/compagnon.exe",
    "/api/guilde/lier",
})


def _lecture_de_jeu_publique(request: Request) -> bool:
    """Les fiches de jeu sont publiques, jamais leur progression membre."""
    if request.method != "GET":
        return False
    chemin = request.url.path
    if chemin in {
            "/api/guilde/activites",
            "/api/guilde/activites/options",
            "/api/guilde/nouveautes"}:
        return True
    prefixe = "/api/guilde/activites/"
    if not chemin.startswith(prefixe):
        return False
    suffixe = chemin.removeprefix(prefixe)
    return bool(suffixe and "/" not in suffixe and suffixe != "avancement")


@app.middleware("http")
async def _garde_lectures_guilde(request: Request, call_next):
    """Hors de la machine, les données de guilde se méritent.

    La page `/guilde` et ses scripts restent servis — c'est l'écran de
    liaison — mais chaque appel `/api/guilde/*` extérieur présente un jeton
    d'appareil. Les routes d'écriture revalident ensuite le leur : ce garde
    ajoute la porte, il ne remplace aucune serrure.
    """
    chemin = request.url.path
    if (chemin.startswith("/api/guilde")
            and chemin not in _GUILDE_PUBLIQUES
            and not _lecture_de_jeu_publique(request)
            and not config.API_OUVERTE
            and not _requete_locale(request)
            and not _jeton_externe_valide(request)):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"detail": "liaison requise — entre ton code Discord pour "
                       "accéder au cockpit"},
            status_code=401)
    return await call_next(request)


def _origine_remontee(request: Request) -> str:
    """Origine observée, sans croire un en-tête fourni depuis Internet.

    Le tunnel Cloudflare appelle Uvicorn en boucle locale et y pose
    `CF-Connecting-IP`. On ne croit cet en-tête que dans ce cas ; un client
    distant qui joint directement le port est borné sur son adresse socket.
    """
    hote = request.client.host if request.client else "inconnue"
    if hote in {"127.0.0.1", "::1"}:
        return request.headers.get("cf-connecting-ip") or hote
    return hote


@app.post("/api/usages")
def remonter_usages(payload: RemonteeRequest,
                    request: Request) -> dict[str, object]:
    """Reçoit les tours d'un Chris installé chez un joueur.

    **C'est la raison d'être du Chris public** : le corpus qui alimente
    `disco banc` et `disco banc-conversation` vient d'un seul joueur et de
    sa guilde ; chaque installation en devient une source, et les
    questions qui ratent chez un inconnu valent davantage que les nôtres —
    personne ne les a formulées en connaissant les outils.

    **La route est ouverte sans jeton**, et c'est délibéré : en graver un
    dans un binaire distribué reviendrait à le publier. Elle est bornée
    autrement — elle n'accepte que des tours, n'exécute rien, plafonne le
    débit par instance, et `usages.epurer` retire ce qui identifie une
    personne ou sa machine **avant** l'écriture.

    Contrairement à `/ask`, elle reste accessible depuis l'extérieur : une
    remontée qui exigerait la boucle locale ne servirait à rien.
    """
    if usages.debit_depasse(payload.instance, _origine_remontee(request)):
        raise HTTPException(
            status_code=429,
            detail="trop d'envois — réessaie dans une minute")
    gardes = usages.enregistrer(
        payload.instance, [tour.model_dump() for tour in payload.tours])
    return {"recus": len(payload.tours), "gardes": gardes}


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, request: Request) -> AskResponse:
    """Un tour de conversation. La mécanique vit dans `dialogue.traiter` —
    ici, seulement ce qui est propre au HTTP : le chrono, le journal, les
    modèles de réponse."""
    _autoriser_question(request)
    started = time.perf_counter()
    con = _con()
    if payload.nouvelle_conversation:
        context.oublier(payload.session)
        analyste.oublier_session(payload.session)
    try:
        echange = dialogue.traiter(
            con, payload.session, payload.question,
            router_name=payload.router_name, speaker=payload.speaker)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        # Dernier recours : une panne hors exécution d'outil (routage,
        # contexte, rendu) sortait en 500 générique — le joueur lisait « Le
        # service a répondu 500 » sans rien d'exploitable. On la transforme
        # en réponse dicible et on garde le traceback dans le journal du cœur.
        logging.getLogger(__name__).exception(
            "panne hors outil sur « %s »", payload.question)
        echange = dialogue.Echange(
            question=payload.question, answered=False, echec="crash",
            texte=erreurs.message_de_crash(None, None, exc))

    elapsed = round((time.perf_counter() - started) * 1000, 2)
    if echange.echec:
        unanswered.record(payload.question, echange.echec,
                          tool=echange.tool,
                          entity=str(echange.args) if echange.args else None,
                          source="api")
    journal.record(payload.question, echange.texte,
                   source=payload.source or "api", tool=echange.tool,
                   entity=echange.entity, confidence=echange.confidence,
                   via=echange.via, elapsed_ms=elapsed,
                   answered=echange.answered, session=payload.session)
    return AskResponse(
        question=payload.question,
        answered=echange.answered,
        answer=echange.texte,
        tool=echange.tool,
        args=echange.args,
        confidence=echange.confidence,
        via=echange.via,
        entity=echange.entity,
        elapsed_ms=elapsed,
        data=(_jsonable(echange.data)
              if payload.include_data and echange.data is not None else None),
        follow_up=echange.follow_up,
    )
