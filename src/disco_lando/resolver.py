"""Résolution d'entité — le point critique du §6.

Le besoin initial était « un dictionnaire de synonymes pour que ça marche même
si la phrase n'est pas exacte ». La décision prise est que **les synonymes
portent sur les entités, pas sur la phrase** : la variabilité des formulations
est combinatoire et sans fin, la liste des entités du jeu est finie.

Quatre étages, du moins cher au plus cher. Aucun ne suffit seul — mesuré sur des
déformations réalistes : phonétique 14/17, fuzzy 13/17, union 15/17.
"""

from __future__ import annotations

import dataclasses
import sqlite3

from rapidfuzz import fuzz

from . import config
from .normalize import est_grammatical, normalize

# Les types dont le vocabulaire ne recoupe pas la grammaire française — hors
# « or », qui est précisément le métal qu'on cherche à retrouver.
_TYPES_DE_MINERAI = frozenset({"resource", "commodity"})


def _vocabulaire_de_minerai(entity_types: tuple[str, ...] | None) -> bool:
    return bool(entity_types) and set(entity_types) <= _TYPES_DE_MINERAI


@dataclasses.dataclass(frozen=True)
class Candidate:
    entity_type: str
    entity_id: str
    alias: str        # l'alias qui a matché — « cutty », pas forcément le nom
    score: float
    via: str          # exact | flat | fts | token | fuzzy
    weight: float
    canonical: str | None = None   # le nom officiel de l'entité visée

    @property
    def name(self) -> str:
        """Ce qu'il faut afficher : le nom officiel, pas le surnom qui a matché."""
        return self.canonical or self.alias

    def __str__(self) -> str:
        via_alias = "" if self.name == self.alias else f" (via « {self.alias} »)"
        return f"{self.name}{via_alias} [{self.entity_type}] {self.score:.0f} ({self.via})"


@dataclasses.dataclass(frozen=True)
class Resolution:
    query: str
    candidates: list[Candidate]

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def ambiguous(self) -> bool:
        """Deux candidats trop proches pour trancher.

        Le cas réel : « Hadanite » existe comme ressource *et* comme objet, avec
        deux UUID différents. Le résolveur ne doit pas choisir à la place de
        l'appelant — c'est l'intention détectée qui filtre sur entity_type.
        """
        if len(self.candidates) < 2:
            return False
        return (self.candidates[0].score - self.candidates[1].score) < config.AMBIGUITY_MARGIN


def _fts_query(norm: str) -> str:
    """Requête FTS5 en préfixe sur chaque mot, échappée en littéral."""
    words = [w for w in norm.split() if w]
    return " OR ".join(f'"{w}"*' for w in words)


# Score maximal atteignable par étage. L'ordre reflète la qualité de la
# correspondance : coller au nom vaut mieux que lui ressembler.
PLAFOND = {
    "exact": 100.0, "flat": 100.0, "fts": 95.0,
    "token": 85.0, "fuzzy": 90.0,
}


def mots_inexpliques(terme: str, alias: str) -> list[str]:
    """Les mots du terme tapé que le nom trouvé ne justifie pas.

    Le score ne dit pas si une correspondance est fiable : « omnisky xi »,
    qui est faux, sort à 90. Ce qui le trahit est ailleurs — le « xi » ne
    correspond à rien dans « Omnisky XII Cannon », c'est un autre modèle.

    Un mot de moins de trois caractères exige l'**égalité** : c'est là que se
    jouent les numéros de version, et « xi » n'est pas « xii ».
    """
    mots = normalize(terme).split()
    parties = normalize(alias).split()
    if not mots or not parties:
        return []

    # Forme collée : « p6lr » face à « p6 lr sniper rifle », « quantanium »
    # face à « QuantaniumMineableRock » — c'est la raison d'être de l'étage
    # `flat`.
    colle = "".join(parties)
    if len(mots) == 1 and colle.startswith(mots[0]):
        return []

    restants = []
    for mot in mots:
        if mot in parties:
            continue
        # **Un mot de trois lettres ne s'explique que par égalité.** « nom »
        # était jugé expliqué par *C.O. Nomad* — il en est un préfixe — si
        # bien que « le meilleur loadout pour mon vaisseau chromé (je me
        # souviens plus de son **nom**) » sortait la fiche du Nomad à 0,94
        # de confiance. Une question sans vaisseau nommé recevait donc une
        # réponse assurée sur un vaisseau au hasard : le mensonge type, et
        # le pire cas puisque rien ne le signale.
        #
        # C'est la règle « un alias de trois lettres ou moins ne vaut que
        # par égalité », transposée au terme **tapé**. Quatre lettres
        # suffisent à porter du sens ; trois ne sont qu'un début de mot.
        if len(mot) >= 4 and any(
                p.startswith(mot) or mot.startswith(p) or mot in p for p in parties):
            continue
        # Une faute de frappe se rattrape par la ressemblance des lettres, pas
        # par le son : le bot est écrit. Le seuil est haut — on cherche à
        # tolérer « gladuis » pour « gladius », pas à tout accepter.
        if len(mot) >= 4 and any(fuzz.ratio(mot, p) >= 82 for p in parties):
            continue
        restants.append(mot)
    return restants


def resolve(
    con: sqlite3.Connection,
    text: str,
    *,
    entity_types: tuple[str, ...] | None = None,
    limit: int = 8,
) -> Resolution:
    """Résout un terme en candidats typés.

    L'exception au refus grammatical est **mesurée, pas concédée** : sur les
    40 000 alias de la base, exactement **deux** mots grammaticaux sont aussi
    des alias exacts — « or » (le métal) et « savoir ». Le second est un
    objet, et « je veux savoir » est une phrase courante : il doit rester
    bloqué. Le premier est un minerai, et un outil qui ne demande que des
    minerais ne peut pas le confondre avec la conjonction.

    D'où la règle : on ne rouvre le passage que pour les appels dont **tous**
    les types demandés relèvent du vocabulaire des minerais.
    """
    norm = normalize(text)
    if not norm:
        return Resolution(text, [])
    # Le point de passage unique : un terme entièrement grammatical ne désigne
    # rien, et le laisser passer donnait *Gilick Boots* pour « elles » ou
    # *Castra* pour « ça ». Refuser ici plutôt que dans chaque appelant — ils
    # étaient quatre à filtrer, chacun oubliant une part différente.
    if est_grammatical(norm) and not _vocabulaire_de_minerai(entity_types):
        return Resolution(text, [])

    type_filter = ""
    type_args: tuple = ()
    if entity_types:
        placeholders = ",".join("?" * len(entity_types))
        type_filter = f" AND a.entity_type IN ({placeholders})"
        type_args = tuple(entity_types)

    pool: dict[tuple[str, str], Candidate] = {}

    def offer(row: sqlite3.Row, score: float, via: str) -> None:
        key = (row["entity_type"], row["entity_id"])
        # Le poids départage **à l'intérieur** d'un étage, il ne fait jamais
        # franchir un palier. Sans ce plafond, un alias de guilde pondéré 1,4
        # sur un match phonétique atteignait 129 et écrasait une
        # correspondance exacte à 93 : « coda » rendait « Drake Cutlass
        # Black », parce que « coda » et « cutty » partagent une clé
        # phonétique. Un à-peu-près ne doit pas battre un nom qui colle.
        weighted = min(score * row["weight"], PLAFOND.get(via, 100.0))
        current = pool.get(key)
        if current is None or weighted > current.score:
            pool[key] = Candidate(
                row["entity_type"], row["entity_id"], row["alias"],
                weighted, via, row["weight"],
            )

    # -- étage 1 : égalité stricte sur la forme normalisée
    for row in con.execute(
        "SELECT entity_type, entity_id, alias, weight FROM aliases a "
        "WHERE alias_norm = ?" + type_filter,
        (norm, *type_args),
    ):
        offer(row, 100.0, "exact")

    # -- étage 1 bis : forme collée. « p6lr » ne partage aucun token avec
    #    « p6 lr sniper rifle » : ni le FTS ni le phonétique ne peuvent le
    #    rattraper, seule la comparaison sans espaces le peut.
    flat = norm.replace(" ", "")
    if len(flat) >= 3:
        for row in con.execute(
            "SELECT entity_type, entity_id, alias, alias_norm, weight FROM aliases a "
            "WHERE (alias_flat = ? OR alias_flat LIKE ? ESCAPE '\\')" + type_filter
            + " LIMIT 200",
            (flat, flat.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
             *type_args),
        ):
            alias_flat = row["alias_norm"].replace(" ", "")
            if alias_flat == flat:
                offer(row, 100.0, "flat")
                continue
            # Un préfixe ne vaut 93 que s'il **s'arrête sur une frontière de
            # mot** de l'alias. Sinon il coupe un mot en deux, et deux modèles
            # différents se confondent : « omnisky xi » est un préfixe de
            # « omniskyxiicannon » et sortait à 93, donc « les dégâts d'un
            # Omnisky XI » répondait l'Omnisky **XII** sans le signaler.
            # « coda » s'arrête pile après « coda » dans « coda pistol », et
            # « p6lr » entre « lr » et « sniper » : ceux-là restent à 93.
            frontieres, position = {0}, 0
            for mot in row["alias_norm"].split():
                position += len(mot)
                frontieres.add(position)
            offer(row, 93.0 if len(flat) in frontieres else 82.0, "flat")

    # -- étage 2 : FTS5, pour les correspondances partielles
    #    (« gladius » dans « Aegis Gladius »)
    match = _fts_query(norm)
    if match:
        try:
            rows = con.execute(
                "SELECT a.entity_type, a.entity_id, a.alias, a.alias_norm, a.weight "
                "FROM aliases_fts f JOIN aliases a ON a.id = f.rowid "
                "WHERE aliases_fts MATCH ?" + type_filter + " LIMIT 600",
                (match, *type_args),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            offer(row, fuzz.WRatio(norm, row["alias_norm"]), "fts")

    # -- étage 3 : le mot dans un nom à rallonge. « quantanium » contre
    #    « MineableRock_AsteroidLegendary_Quantainium » — ni l'égalité ni le
    #    FTS ne les rapprochent, seul le découpage en mots le fait.
    #
    #    **Il n'y a plus d'étage phonétique.** Il existait pour rattraper la
    #    transcription vocale : Whisper rendait « gladiousse », et le double
    #    metaphone le ramenait sur *Gladius*. Le bot est écrit, la voix n'est
    #    plus la source des termes, et le phonétique ne produisait plus que
    #    des faux positifs — « coda » et « guide » partagent la clé KT, d'où
    #    « How to Play Guide » en réponse à une question sur le Coda ; « coda »
    #    et « cutty » aussi, d'où le Cutlass. Une faute de frappe ne se
    #    prononce pas comme le mot juste : c'est le fuzzy qui la rattrape, pas
    #    la phonétique.
    #    Le mot cherché n'est pas toujours écrit comme celui de la base : le
    #    jeu s'écrit lui-même « Quantanium » dans une entrée morte et
    #    « Quantainium » dans les vrais gisements. Une lettre d'écart, que le
    #    phonétique franchissait et que l'égalité de token ne franchit pas. On
    #    élargit donc par **préfixe** — quatre lettres, assez pour rester peu
    #    coûteux — et on tranche sur la ressemblance des lettres. C'est la
    #    bonne mesure pour du texte tapé : une faute de frappe déplace des
    #    lettres, elle ne change pas la prononciation.
    words = [w for w in norm.split() if len(w) >= 3]
    if words:
        word_marks = ",".join("?" * len(words))
        # Une **plage** et non un `LIKE` : `LIKE` est insensible à la casse par
        # défaut, donc SQLite refuse d'utiliser l'index de `token` et parcourt
        # la table entière — mesuré, la résolution passait de 0,5 à 13 ms, et
        # le routeur en fait une par n-gramme. Un intervalle borné, lui, se
        # sert de l'index.
        bornes: list[str] = []
        for mot in words:
            if len(mot) >= 5:
                debut = mot[:4]
                bornes += [debut, debut[:-1] + chr(ord(debut[-1]) + 1)]
        plage = " OR ".join("(t.token >= ? AND t.token < ?)"
                            for _ in range(len(bornes) // 2))
        for row in con.execute(
            "SELECT DISTINCT a.entity_type, a.entity_id, a.alias, a.alias_norm, "
            "       a.weight, t.token FROM alias_tokens t "
            "JOIN aliases a ON a.id = t.alias_id "
            f"WHERE (t.token IN ({word_marks})"
            + (f" OR {plage}" if bornes else "") + ")"
            + type_filter + " LIMIT 2000",
            (*words, *bornes, *type_args),
        ):
            # Un voisinage de préfixe ne suffit pas : « quantanium » et
            # « quantite » commencent pareil. On exige que le mot trouvé
            # ressemble vraiment à l'un des mots cherchés.
            if row["token"] not in words and not any(
                    fuzz.ratio(w, row["token"]) >= 85 for w in words):
                continue
            # Le plafond de l'étage (85) fait le reste : un vrai mot retrouvé
            # dans un nom long vaut ses 85, le bruit tombe. Le ratio partiel
            # sépare franchement les deux populations — 90 pour
            # « quantanium » contre « MineableRock_…_Quantainium », 33 pour
            # une coïncidence.
            offer(row, max(fuzz.WRatio(norm, row["alias_norm"]),
                           fuzz.partial_ratio(norm, row["alias_norm"])),
                  "token")

    # -- étage 4 : filet fuzzy global, seulement si rien n'a mordu
    if not pool:
        rows = con.execute(
            "SELECT entity_type, entity_id, alias, alias_norm, weight FROM aliases a "
            "WHERE 1 = 1" + type_filter,
            type_args,
        ).fetchall()
        for row in rows:
            score = fuzz.WRatio(norm, row["alias_norm"])
            if score >= config.FUZZY_FLOOR:
                offer(row, score, "fuzzy")

    # **Un alias de trois lettres ou moins ne vaut que par égalité.** « Or » et
    # « Fer » sont de vrais noms de minerais, mais si peu de lettres se
    # retrouvent partout : mesuré, « z**or**glubium » — un mot inventé —
    # sortait *Gold* à 90, et « star runner » sortait *Iron* à 72 par « Fer »,
    # ce qui faisait répondre le prix du fer à une question sur un vaisseau.
    # C'est le raisonnement de `mots_inexpliques`, où un mot court exige
    # l'égalité : en dessous de quatre caractères, il n'y a pas assez de
    # matière pour qu'une ressemblance veuille dire quelque chose. Ils restent
    # trouvables — « du fer », « de l'or » — par la seule voie qui ait un sens.
    retenus = [c for c in pool.values()
               if len(normalize(c.alias).replace(" ", "")) >= 4
               or normalize(c.alias) == norm]
    # À score égal, l'alias le plus court est le plus précis : « P6 LR » doit
    # sortir « P6-LR Sniper Rifle » avant « P6-LR "Archangel" Sniper Rifle ».
    ranked = sorted(retenus, key=lambda c: (-c.score, len(c.alias), c.alias))[:limit]
    return Resolution(text, _with_canonical(con, ranked))


def _with_canonical(con: sqlite3.Connection, candidates: list[Candidate]) -> list[Candidate]:
    """Attache le nom officiel de chaque entité retenue.

    Un surnom de guilde résout correctement mais s'affiche mal : « cutty » doit
    répondre « Drake Cutlass Black ». Une seule requête pour les quelques
    candidats retournés.
    """
    if not candidates:
        return candidates
    pairs = {(c.entity_type, c.entity_id) for c in candidates}
    clause = " OR ".join(["(entity_type = ? AND entity_id = ?)"] * len(pairs))
    args = [v for pair in pairs for v in pair]
    names = {
        (r["entity_type"], r["entity_id"]): r["alias"]
        for r in con.execute(
            "SELECT entity_type, entity_id, alias FROM aliases "
            f"WHERE source = 'canonical' AND ({clause})",
            args,
        )
    }
    return [
        dataclasses.replace(c, canonical=names.get((c.entity_type, c.entity_id)))
        for c in candidates
    ]
