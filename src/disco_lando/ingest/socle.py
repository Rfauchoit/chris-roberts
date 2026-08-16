"""Le socle de l'ingestion : alias, labels, conversions.

Ce que tous les chargeurs partagent, et rien d'autre. Il ne dépend
d'aucun domaine — c'est ce qui garantit l'absence de cycle.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
import pathlib
import sqlite3
from ..normalize import normalize, split_class_name
from .source import read_json


PLACEHOLDER = "<= PLACEHOLDER =>"


def _prose_utile(texte) -> str | None:
    """Un texte, ou rien — jamais l'échafaudage de CIG.

    Le jeu écrit « <= UNINITIALIZED => » et « <= PLACEHOLDER => » dans les
    cases qu'il n'a pas remplies. Les stocker comme prose les faisait
    ressortir dans les fiches, où ils se lisent comme une réponse.
    """
    texte = (texte or "").strip()
    if not texte or texte.startswith("<="):
        return None
    return texte


# ------------------------------------------------------------------ alias

class AliasCollector:
    """Accumule les alias et les écrit en une passe.

    Le nom canonique est inséré comme un alias parmi d'autres : le résolveur
    n'interroge qu'une table, jamais huit (cf. docs/SCHEMA.md).
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], tuple] = {}

    def add(
        self,
        entity_type: str,
        entity_id: str,
        alias: str | None,
        *,
        source: str = "canonical",
        weight: float = 1.0,
        lang: str = "en",
    ) -> None:
        if not alias or not entity_id:
            return
        text = alias.strip()
        if not text or text == PLACEHOLDER:
            return
        norm = normalize(text)
        if not norm or len(norm) < 2:
            return
        key = (entity_type, entity_id, norm, source)
        if key in self._rows:
            return
        self._rows[key] = (
            entity_type, entity_id, text, norm, norm.replace(" ", ""),
            lang, source, weight,
        )

    def add_variants(self, entity_type: str, entity_id: str, name: str | None,
                     class_name: str | None = None, *, drop_prefix: str | None = None) -> None:
        """Nom canonique + variantes dérivées mécaniquement.

        Le joueur dit « Gladius », jamais « Aegis Gladius » ni
        « AEGS_Gladius ». Les variantes portent un poids inférieur pour que le
        nom officiel gagne à score égal.
        """
        self.add(entity_type, entity_id, name, source="canonical", weight=1.0)

        if name and drop_prefix and name.lower().startswith(drop_prefix.lower()):
            trimmed = name[len(drop_prefix):].strip(" -—")
            self.add(entity_type, entity_id, trimmed, source="derived", weight=0.9)

        if name and '"' in name:
            self.add(entity_type, entity_id, name.replace('"', " "),
                     source="derived", weight=0.9)

        if class_name:
            words = split_class_name(class_name)
            if words:
                self.add(entity_type, entity_id, " ".join(words),
                         source="derived", weight=0.8)

        # Le jeton de modèle est le nom que tape un joueur : « un C2 »,
        # « le 300i », « la M2 ». Mesuré le 2026-08-07 : aucun alias « c2 »
        # n'existait, et « combien de Cyclone dans un C2 » perdait son
        # porteur (60, sous le seuil). Un jeton qui mêle lettres et chiffres
        # est distinctif par construction ; un pur chiffre reste écarté —
        # « un nombre nu n'est pas une entité » — et un alias de trois
        # lettres ou moins ne vaut déjà que par égalité stricte.
        if entity_type == "ship" and name:
            for mot in normalize(name).split():
                if (len(mot) >= 2 and any(c.isdigit() for c in mot)
                        and any(c.isalpha() for c in mot)):
                    self.add(entity_type, entity_id, mot,
                             source="derived", weight=0.9)

    def flush(self, con: sqlite3.Connection) -> int:
        rows = list(self._rows.values())
        con.executemany(
            "INSERT OR IGNORE INTO aliases "
            "(entity_type, entity_id, alias, alias_norm, alias_flat, "
            " lang, source, weight) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        # Table FTS5 à contenu externe : elle se reconstruit par commande, pas
        # par DELETE + INSERT (qui corrompt l'index). « rebuild » est
        # idempotent, ce qui rend le second appel de flush() sans danger.
        con.execute("INSERT INTO aliases_fts (aliases_fts) VALUES ('rebuild')")

        # Découpage en mots, pour retrouver un terme dans un nom à rallonge.
        # Une seule passe de
        # relecture : c'est moins cher que de tenir les identifiants côté
        # Python à travers un executemany.
        con.execute("DELETE FROM alias_tokens")
        tokens = []
        for row in con.execute("SELECT id, alias_norm FROM aliases"):
            for word in dict.fromkeys(row["alias_norm"].split()):
                if len(word) < 3:
                    continue
                tokens.append((row["id"], word))
        con.executemany(
            "INSERT INTO alias_tokens (alias_id, token) VALUES (?,?)",
            tokens,
        )
        self._rows.clear()
        return len(rows)


# ------------------------------------------------------------------ labels

SYSTEMS = ("Stanton", "Pyro", "Nyx", "Terra", "Odin", "Castra")

# « FoxwellEnforcement_Patrol_Pyro_Super » — le nom de debug porte le système et
# la difficulté, que rien d'autre ne donne de façon fiable.
_DIFFICULTIES = ("VeryEasy", "Easy", "Medium", "Hard", "VeryHard", "Super", "Extreme")


def parse_debug_name(debug_name: str | None) -> tuple[str | None, str | None]:
    """Système et difficulté lus dans le nom de debug d'un contrat."""
    if not debug_name:
        return None, None
    parts = debug_name.split("_")
    system = next((s for s in SYSTEMS if s in parts), None)
    difficulty = next((d for d in _DIFFICULTIES if d in parts), None)
    return system, difficulty


def load_labels(root: pathlib.Path) -> dict[str, str]:
    """`labels.json` : 90 000 clés, anglais uniquement (pas de FR, cf. audit)."""
    path = root / "labels.json"
    if not path.exists():
        return {}
    raw = read_json(path)
    return {k.lstrip("﻿"): v for k, v in raw.items() if isinstance(v, str)}


# ------------------------------------------------------------------ ressources

def _location_id(system: str | None, name: str) -> str:
    """`locations.json` ne donne aucun UUID de lieu (vérifié : 0 sur 330).
    On fabrique une clé déterministe, stable d'une réingestion à l'autre."""
    return f"loc:{(system or '?').strip()}:{name.strip()}"


def _bool(valeur) -> int | None:
    """Un drapeau amont en 0/1, et **None quand il est absent**.

    La distinction compte : 36 factions sur 74 n'ont pas de `Properties`, donc
    pas de `Lawful`. Les ranger en « pas lawful » les ferait passer pour des
    hors-la-loi — ce que Rough & Ready n'est pas, faute d'information.
    """
    return None if valeur is None else int(bool(valeur))


def _nombre(valeur) -> float | None:
    """Un nombre, ou rien. La source n'a pas toujours la forme attendue :
    `Missile.ExplosionRadius` est un couple `{Minimum, Maximum}` sur 67
    missiles sur 68 et un nombre nu sur le dernier. Un `INSERT` reçoit alors
    un dict et lève au milieu de l'ingestion — mieux vaut refuser ce qu'on ne
    sait pas lire que parier sur une forme."""
    return valeur if isinstance(valeur, (int, float)) and not isinstance(valeur, bool) else None
