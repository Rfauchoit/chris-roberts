"""Client du Core API, partagé par les frontends.

Le §2 impose que les frontends pointent vers un cœur local ou distant via une
variable d'environnement. Ce client est ce point unique : ni le frontend local
ni le bot Discord ne parlent à SQLite, ils parlent à `DISCO_CORE_URL`.

Aucune dépendance externe — `urllib` suffit, et sur la VM Discord c'est une
dépendance de moins à installer.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.request

from . import config


def _detail(exc: urllib.error.HTTPError) -> str:
    """La cause que le cœur a mise dans le corps de l'erreur, si elle s'y lit.

    `exc.read()` consomme le flux : à n'appeler qu'une fois par erreur.
    """
    try:
        corps = json.loads(exc.read().decode("utf-8", errors="replace"))
        return str(corps.get("detail") or "")
    except (ValueError, OSError):
        return ""


@dataclasses.dataclass(frozen=True)
class Answer:
    answered: bool
    answer: str
    tool: str | None = None
    entity: str | None = None
    confidence: float | None = None
    via: str | None = None
    elapsed_ms: float = 0.0
    data: dict | None = None
    error: str | None = None
    # Les propositions que la réponse vient de faire. Vide si elle n'attend
    # rien — c'est ce qui dit à un frontend s'il doit guetter une suite.
    follow_up: tuple[str, ...] = ()

    @property
    def spoken(self) -> str:
        """Ce qu'il faut lire à voix haute."""
        return self.error or self.answer


class CoreClient:
    def __init__(self, base_url: str | None = None,
                 timeout: float | None = None):
        self.base_url = (base_url or config.CORE_URL).rstrip("/")
        # L'analyste distant peut prendre jusqu'à ANALYSTE_TIMEOUT : un
        # client qui abandonne à 15 s transformait chaque question
        # d'analyse en « 30k » — mesuré sur Discord le 2026-08-08, pile
        # pendant que la réponse se calculait. Un cœur mort se détecte par
        # /health (5 s), pas par ce délai.
        self.timeout = (timeout if timeout is not None
                        else config.ANALYSTE_TIMEOUT + 30)

    def _post(self, chemin: str, charge: dict) -> dict:
        entetes = {"Content-Type": "application/json"}
        # **Un frontend qui ne tourne pas sur la machine du cœur présente un
        # jeton.** En local il n'en a pas besoin — la boucle locale est de
        # confiance — mais le §2 prévoit un cœur « en local ou sur la VM », et
        # depuis qu'un tunnel peut le publier, `/ask` se ferme aux inconnus.
        if config.API_TOKEN:
            entetes["Authorization"] = f"Bearer {config.API_TOKEN}"
        requete = urllib.request.Request(
            f"{self.base_url}{chemin}",
            data=json.dumps(charge).encode("utf-8"),
            headers=entetes,
        )
        with urllib.request.urlopen(requete, timeout=self.timeout) as reponse:
            return json.load(reponse)

    def health(self) -> dict | None:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

    def ask(self, question: str, *, include_data: bool = False,
            source: str | None = None, session: str | None = None,
            speaker: str | None = None,
            nouvelle_conversation: bool = False) -> Answer:
        """Pose une question au cœur.

        Une panne du cœur devient une réponse dicible, pas une exception : un
        frontend vocal qui plante en silence est pire qu'un frontend qui dit
        « le service ne répond pas ».
        """
        try:
            corps = self._post("/ask", {"question": question,
                                        "include_data": include_data,
                                        "source": source,
                                        "session": session,
                                        "speaker": speaker,
                                        "nouvelle_conversation":
                                            nouvelle_conversation})
        except urllib.error.HTTPError as exc:
            # Le cœur met sa cause dans `detail` (400 question trop longue,
            # 503 base indisponible) : la relayer telle quelle vaut mieux
            # qu'un code nu que personne ne sait interpréter.
            detail = _detail(exc)
            return Answer(False, "", error=(
                f"Le cœur a répondu {exc.code}"
                + (f" : {detail}" if detail else "")
                + f" ({self.base_url})."))
        except (urllib.error.URLError, TimeoutError, OSError):
            return Answer(False, "", error=(
                f"Le cœur ne répond pas sur {self.base_url} — il n'est "
                f"sans doute pas démarré (bouton du lanceur, ou "
                f"« disco serve »)."))
        except json.JSONDecodeError:
            return Answer(False, "", error=(
                f"Réponse illisible du service sur {self.base_url} — "
                f"est-ce bien un cœur Chris Roberts qui écoute là ?"))

        return Answer(
            answered=bool(corps.get("answered")),
            answer=corps.get("answer") or "",
            tool=corps.get("tool"),
            entity=corps.get("entity"),
            confidence=corps.get("confidence"),
            via=corps.get("via"),
            elapsed_ms=corps.get("elapsed_ms") or 0.0,
            data=corps.get("data"),
            follow_up=tuple(corps.get("follow_up") or ()),
        )
