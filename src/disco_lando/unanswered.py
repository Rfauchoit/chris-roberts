"""Journal des questions restées sans réponse.

Prévu pour la phase de test grandeur nature : chaque question que le cœur ne
sait pas traiter est consignée, pour être relue ensuite et décider s'il faut un
nouveau module, un champ amont supplémentaire, ou simplement un motif de plus.

Trois raisons possibles, et elles n'appellent pas les mêmes suites :

- `no_intent` — aucune intention reconnue. C'est un manque du routeur, ou une
  question hors périmètre.
- `no_entity` — intention reconnue mais aucune entité résoluble. Souvent un
  alias manquant.
- `no_data` — question comprise, entité trouvée, mais la donnée n'existe pas.
  C'est le cas qui justifie un nouveau module ou une source externe.

Le fichier est en JSON Lines : une ligne par question, ajoutable en continu,
relisible sans tout charger.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import threading

from . import config

_LOCK = threading.Lock()

REASONS = ("no_intent", "no_entity", "no_data")


def path() -> pathlib.Path:
    return config.UNANSWERED_LOG


def record(question: str, reason: str, *, tool: str | None = None,
           entity: str | None = None, source: str = "cli") -> None:
    """Consigne une question sans réponse. N'échoue jamais.

    Un journal qui fait planter le service qu'il observe ne sert à rien : toute
    erreur d'écriture est avalée.
    """
    if reason not in REASONS:
        reason = "no_intent"
    ligne = {
        "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "question": question,
        "reason": reason,
        "tool": tool,
        "entity": entity,
        "source": source,
    }
    try:
        with _LOCK:
            path().parent.mkdir(parents=True, exist_ok=True)
            with path().open("a", encoding="utf-8") as fichier:
                fichier.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(limit: int | None = None) -> list[dict]:
    fichier = path()
    if not fichier.exists():
        return []
    lignes = []
    for brute in fichier.read_text(encoding="utf-8").splitlines():
        try:
            lignes.append(json.loads(brute))
        except json.JSONDecodeError:
            continue
    return lignes[-limit:] if limit else lignes


def summary() -> dict:
    """Vue d'ensemble, pour décider quoi construire ensuite."""
    lignes = read()
    par_raison: dict[str, int] = {}
    par_question: dict[str, int] = {}
    for ligne in lignes:
        par_raison[ligne["reason"]] = par_raison.get(ligne["reason"], 0) + 1
        cle = ligne["question"].strip().lower()
        par_question[cle] = par_question.get(cle, 0) + 1
    return {
        "total": len(lignes),
        "by_reason": dict(sorted(par_raison.items(), key=lambda kv: -kv[1])),
        "top_questions": sorted(par_question.items(), key=lambda kv: -kv[1])[:30],
    }
