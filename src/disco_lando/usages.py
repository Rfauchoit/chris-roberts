"""La remontée des usages : ce que les Chris installés nous apprennent.

**C'est la raison d'être du Chris public.** L'utilisateur, 2026-08-13 :
« je veux que les data du Chris de chacun me soient renvoyées à MOI et
que TU puisses les analyser pour améliorer le bot pour tous, c'est
d'ailleurs le but premier, d'avoir des vrais use cases ouverts ».

Le corpus qui alimente `disco banc` et `disco banc-conversation` vient
aujourd'hui d'un seul joueur et de sa guilde. Chaque installation en
devient une source, et les questions qui ratent chez un inconnu valent
exactement autant que celles du journal — davantage, même, parce que
personne ne les a formulées en connaissant les outils.

## Ce qui ne part jamais

Le §9 du brief vaut ici comme ailleurs, et **le code le garantit plutôt
que le prompt le promette** : `epurer()` retire ce qui identifie une
personne ou sa machine avant tout envoi, et un test le vérifie sur des
charges réelles. Un joueur qui active la remontée partage ses questions
de jeu, pas son identité ni son disque.

## Pourquoi la route est ouverte

Elle n'exige aucun jeton — en graver un dans un binaire distribué
reviendrait à le publier. Elle est donc bornée par ce qu'elle accepte :
des tours, rien d'autre, jamais d'exécution, et un débit plafonné par
instance. C'est le seul point d'entrée public en écriture du cœur.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3
import threading
import time
from typing import Any

from . import config

_VERROU = threading.Lock()

#: Un tour dépassant cette taille est tronqué : une réponse de duel fait
#: quelques kilo-octets, au-delà c'est une anomalie ou un abus.
TAILLE_MAX_TEXTE = 8_000

#: Ce qu'une instance peut envoyer par fenêtre, pour que la route ouverte
#: ne devienne pas un déversoir.
TOURS_MAX_PAR_ENVOI = 200
FENETRE_DEBIT = 60.0
ENVOIS_MAX_PAR_FENETRE = 20

_debits: dict[str, list[float]] = {}

#: Les champs qu'une instance a le droit de transmettre. Tout le reste est
#: jeté — liste blanche, pas liste noire : un champ ajouté en amont sans
#: qu'on y pense ne doit pas passer.
CHAMPS_AUTORISES = frozenset({
    "question", "reponse", "outil", "confiance", "via", "duree_ms",
    "aboutie", "fil", "rang", "horodatage", "version",
})

#: Ce qui identifie une personne ou sa machine, et ne franchit jamais la
#: porte. Le contrôle porte sur le **texte** des champs conservés, pas
#: seulement sur les clés : un chemin Windows glissé dans une question en
#: sort tout autant.
#: **Sans l'espace dans la classe** : la première version acceptait les
#: espaces et avalait la fin de la phrase — « C:\\Users\\Randy\\Game.log
#: et mon ip 192.168.1.4 » devenait un seul masque, question comprise. On
#: masque ce qui identifie, on ne détruit pas ce qui informe.
_CHEMIN_WINDOWS = re.compile(r"[A-Za-z]:\\[\\\w.()-]{3,}")
_CHEMIN_UNIX = re.compile(r"/(?:home|Users)/[\w.-]+")
_ADRESSE_IP = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-f]{0,4}:){4,}[0-9a-f]{0,4}\b")
_COURRIEL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_MENTION_DISCORD = re.compile(r"<@!?\d{5,}>|\b\d{17,20}\b")

_MASQUE = "[retiré]"


def epurer(tour: dict[str, Any]) -> dict[str, Any]:
    """Ne garder que ce qui aide à améliorer le bot.

    Deux passes, parce qu'aucune ne suffit seule : la **liste blanche**
    des champs écarte ce qu'une version future ajouterait sans qu'on y
    pense, et le **masquage du texte** attrape ce qui se glisse dans une
    question — un chemin de Game.log, une adresse, une mention Discord.

    Rien n'est deviné ni corrigé : ce qui ressemble à une donnée
    personnelle est remplacé, jamais interprété.
    """
    propre: dict[str, Any] = {}
    for cle, valeur in tour.items():
        if cle not in CHAMPS_AUTORISES:
            continue
        if isinstance(valeur, str):
            valeur = valeur[:TAILLE_MAX_TEXTE]
            for motif in (_CHEMIN_WINDOWS, _CHEMIN_UNIX, _COURRIEL,
                          _ADRESSE_IP, _MENTION_DISCORD):
                valeur = motif.sub(_MASQUE, valeur)
        propre[cle] = valeur
    return propre


def _base() -> pathlib.Path:
    return pathlib.Path(config.DATA_DIR) / "usages.db"


def _connexion() -> sqlite3.Connection:
    con = sqlite3.connect(_base())
    con.execute("""
        CREATE TABLE IF NOT EXISTS tours (
            id INTEGER PRIMARY KEY,
            instance TEXT NOT NULL,
            recu_le TEXT NOT NULL,
            horodatage TEXT,
            fil TEXT,
            rang INTEGER,
            question TEXT NOT NULL,
            reponse TEXT,
            outil TEXT,
            confiance REAL,
            via TEXT,
            duree_ms REAL,
            aboutie INTEGER,
            version TEXT
        )""")
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_tours_instance ON tours(instance)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_tours_fil ON tours(fil, rang)")
    return con


def debit_depasse(instance: str, origine: str | None = None) -> bool:
    """Ni une instance ni une origine ne peut noyer la route ouverte.

    L'identifiant d'instance est fourni par le client et se renouvelle donc
    à volonté. L'origine est observée par le serveur ; les deux plafonds sont
    indépendants afin qu'une rotation d'identifiants ne contourne pas la
    borne, sans qu'un client bavard ne pénalise toutes les autres origines.
    """
    maintenant = time.monotonic()
    cles = [f"instance:{instance}"]
    if origine:
        cles.append(f"origine:{origine}")
    with _VERROU:
        depasse = False
        for cle in cles:
            recents = [t for t in _debits.get(cle, [])
                       if maintenant - t < FENETRE_DEBIT]
            recents.append(maintenant)
            _debits[cle] = recents
            depasse = depasse or len(recents) > ENVOIS_MAX_PAR_FENETRE
        return depasse


def enregistrer(instance: str, tours: list[dict[str, Any]]) -> int:
    """Range les tours reçus. Rend le nombre effectivement gardé.

    **L'identifiant d'instance est anonyme par construction** : il est
    tiré au hasard à l'installation et n'a aucun lien avec un compte RSI
    ou Discord. Il ne sert qu'à recoudre les conversations — sans lui, le
    banc de conversation ne peut rien faire des tours reçus.
    """
    instance = re.sub(r"[^a-zA-Z0-9-]", "", str(instance))[:64] or "inconnue"
    gardes = 0
    con = _connexion()
    try:
        with con:
            for tour in tours[:TOURS_MAX_PAR_ENVOI]:
                if not isinstance(tour, dict):
                    continue
                propre = epurer(tour)
                if not (propre.get("question") or "").strip():
                    continue
                con.execute(
                    "INSERT INTO tours (instance, recu_le, horodatage, fil, "
                    "rang, question, reponse, outil, confiance, via, "
                    "duree_ms, aboutie, version) VALUES "
                    "(?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (instance, propre.get("horodatage"), propre.get("fil"),
                     propre.get("rang"), propre["question"],
                     propre.get("reponse"), propre.get("outil"),
                     propre.get("confiance"), propre.get("via"),
                     propre.get("duree_ms"),
                     1 if propre.get("aboutie") else 0,
                     propre.get("version")))
                gardes += 1
    finally:
        con.close()
    return gardes


def bilan() -> dict[str, Any]:
    """Ce que les instances nous ont appris — la feuille de route écrite
    par les joueurs plutôt que par nous."""
    if not _base().exists():
        return {"instances": 0, "tours": 0, "ratees": [], "outils": [],
                "lentes": []}
    con = _connexion()
    try:
        instances = con.execute(
            "SELECT COUNT(DISTINCT instance) FROM tours").fetchone()[0]
        tours = con.execute("SELECT COUNT(*) FROM tours").fetchone()[0]
        ratees = [dict(question=q, fois=n) for q, n in con.execute(
            "SELECT question, COUNT(*) n FROM tours WHERE aboutie = 0 "
            "GROUP BY LOWER(question) ORDER BY n DESC LIMIT 30")]
        outils = [dict(outil=o or "aucun", fois=n) for o, n in con.execute(
            "SELECT outil, COUNT(*) n FROM tours GROUP BY outil "
            "ORDER BY n DESC LIMIT 30")]
        # Ce qui part chez l'analyste alors que le déterministe devrait
        # servir : c'est ce qui a révélé les 30 s de quota sur « comment
        # je fais un p6lr ».
        lentes = [dict(question=q, ms=round(d or 0)) for q, d in con.execute(
            "SELECT question, MAX(duree_ms) d FROM tours "
            "WHERE duree_ms > 5000 GROUP BY LOWER(question) "
            "ORDER BY d DESC LIMIT 30")]
        return {"instances": instances, "tours": tours, "ratees": ratees,
                "outils": outils, "lentes": lentes}
    finally:
        con.close()
