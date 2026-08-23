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
import re
import sqlite3
from collections import OrderedDict

from rapidfuzz import fuzz, process

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
    # Vrai pour une livrée ou une peinture (`items.type = 'Paints'`) quand
    # la question ne nomme aucun mot de cosmétique : reléguée derrière le
    # vaisseau ou l'arme dont elle porte le nom, jamais retirée.
    cosmetique: bool = False

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
    # **« Mk2 » est explique par « Mk II ».** Les deux ecritures designent la
    # meme marque ; sans cette equivalence, le « 2 » restait orphelin et le
    # controle de certitude posait « tu veux dire… ? » sur une question
    # limpide. Meme raison que la seconde passe du resolveur.
    if (autre := _autre_ecriture_de_marque(" ".join(parties))) is not None:
        parties = parties + autre.split()

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


_MOTS_COSMETIQUES = {
    "livree", "livrees", "livery", "liveries", "peinture", "peintures",
    "skin", "skins", "camo", "coloris", "paint", "paints",
    # `Flair_*` est la meme famille par un autre bout : 159 objets de
    # decoration — serviettes, maquettes murales — qui portent le nom d un
    # vaisseau. « MISC Prospector Schematic » est un tableau accroche au mur,
    # et il concourait au prix du vaisseau.
    "schematic", "schematics", "maquette", "maquettes", "poster", "posters",
    "deco", "decoration", "decorations",
}

_CACHE_COSMETIQUES: dict[tuple, frozenset[str]] = {}


def _uuids_cosmetiques(con: sqlite3.Connection) -> frozenset[str]:
    """Les UUID des objets purement cosmetiques, gardes en memoire.

    La population ne change qu'a la reingestion, qui redemarre le processus —
    meme raisonnement que `_population_fuzzy`. Le resolveur est le cout fixe
    de toute question : une requete de plus par n-gramme se paierait sur
    l'ensemble du bot, pas sur la seule question qui en a besoin.
    """
    # **La cle est l identite des donnees, jamais `id(con)`.** Deux bases
    # `:memory:` distinctes, ou une connexion recyclee par le ramasse-miettes,
    # partageraient l entree — c est exactement le piege que `_POPULATIONS`
    # documente cent lignes plus bas, et il n y a pas de raison de le refaire.
    cle = _identite_des_aliases(con)
    if cle not in _CACHE_COSMETIQUES:
        try:
            peintures = frozenset(
                r[0] for r in con.execute(
                    "SELECT uuid FROM items "
                    "WHERE type = 'Paints' OR type LIKE 'Flair%'"))
        except sqlite3.OperationalError:
            # Base partielle — les tests du resolveur n ont que `aliases`.
            # Sans catalogue d objets, il n y a pas de cosmetique a releguer.
            peintures = frozenset()
        if len(_CACHE_COSMETIQUES) >= _MAX_POPULATIONS:
            _CACHE_COSMETIQUES.clear()
        _CACHE_COSMETIQUES[cle] = peintures
    return _CACHE_COSMETIQUES[cle]


#: « Mk II » et « Mk 2 » sont la meme marque, et la base porte **les deux
#: formes** : 417 alias en chiffres romains (le nom officiel) et 180 en
#: chiffres arabes (derives du `class_name`, « anvl hornet f7 a mk 2 »).
#: Aucun des deux jeux ne couvre toute la flotte, si bien que l ecriture
#: choisie par le joueur decidait de la reponse.
_ROMAIN_VERS_ARABE = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
                      "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
_ARABE_VERS_ROMAIN = {v: k for k, v in _ROMAIN_VERS_ARABE.items()}
_MARQUE = re.compile(r"\bmk (\d+|i{1,3}v?|vi{0,3}|ix|x)\b")


def _autre_ecriture_de_marque(norm: str) -> str | None:
    """La meme designation, marque ecrite dans l autre systeme de chiffres."""
    def bascule(m: re.Match) -> str:
        chiffre = m.group(1)
        autre = (_ARABE_VERS_ROMAIN.get(chiffre)
                 or _ROMAIN_VERS_ARABE.get(chiffre))
        return f"mk {autre}" if autre else m.group(0)

    permute = _MARQUE.sub(bascule, norm)
    return permute if permute != norm else None


def resolve(
    con: sqlite3.Connection,
    text: str,
    *,
    entity_types: tuple[str, ...] | None = None,
    limit: int = 8,
) -> Resolution:
    """Resout un terme, en essayant les deux ecritures d une marque.

    **Une orthographe ne doit pas changer le vaisseau.** Rejeu du journal du
    2026-08-21, deux tours mesures :

        « ... avec un hornet F7A Mk2 ? »   -> Hornet F7A Mk II PYAM Exec
        « ... avec un F7A Hornet Mk II ? » -> Anvil F7A Hornet Mk II

    Deux autonomies — 236,9 contre 142,6 millions de km — pour ce qui est le
    meme vaisseau dans la tete du joueur. C est le defaut le plus corrosif du
    corpus : les deux reponses sont plausibles et rien ne signale laquelle est
    la bonne.

    La seconde passe ne coute que sur les termes qui portent une marque, et
    on garde le meilleur score de chaque entite : la forme du joueur reste
    prioritaire a egalite.
    """
    premiere = _resoudre_une_forme(con, text, entity_types=entity_types,
                                   limit=limit)
    autre = _autre_ecriture_de_marque(normalize(text))
    if autre is None:
        return premiere
    seconde = _resoudre_une_forme(con, autre, entity_types=entity_types,
                                  limit=limit)
    par_entite: dict[tuple[str, str], Candidate] = {}
    for candidat in [*premiere.candidates, *seconde.candidates]:
        cle = (candidat.entity_type, candidat.entity_id)
        garde = par_entite.get(cle)
        if garde is None or candidat.score > garde.score:
            par_entite[cle] = candidat
    fusion = sorted(par_entite.values(),
                    key=lambda c: (c.cosmetique, -c.score, len(c.alias),
                                   c.alias))[:limit]
    return Resolution(text, fusion)


def _resoudre_une_forme(
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
        # **Sauf si le mot est l'alias exact d'un minerai.** « Le prix de
        # l'or » ne routait nulle part : `get_price` accepte quatre types —
        # vaisseau, objet, commodité, ressource — donc l'exception « tous les
        # types demandés sont des minerais » ne s'ouvrait pas, et « or »
        # restait la conjonction. « Le prix du fer » marchait, lui, parce que
        # « fer » n'est pas un mot de grammaire : le trou ne tenait qu'à ça.
        #
        # L'élargissement reste **mesuré, pas concédé** : sur les 40 000
        # alias, exactement deux mots grammaticaux sont aussi des alias
        # exacts — « or », un minerai, et « savoir », un objet. En restrei-
        # gnant la seconde chance aux **types de minerai**, « savoir » reste
        # bloqué, et « je veux savoir » ne peut toujours rien résoudre.
        mineraux = tuple(t for t in (entity_types or ())
                         if t in _TYPES_DE_MINERAI)
        if not mineraux:
            return Resolution(text, [])
        secours = _resoudre_une_forme(con, text, entity_types=mineraux,
                                      limit=limit)
        exacts = [c for c in secours.candidates if c.via == "exact"]
        return Resolution(text, exacts)

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
        # **À score égal, on garde l'alias le plus proche de ce qui a été
        # tapé.** Sans arbitre, c'était l'ordre d'arrivée des lignes qui
        # tranchait : mesuré le 2026-08-16, changer le plan de la requête
        # `flat` déplaçait sept résolutions du corpus — « avenger »,
        # « corsair », « hammerhead »… — sans qu'aucun score ne bouge. Le
        # résolveur dépendait du **chemin d'accès SQLite**, ce qu'aucun test
        # n'aurait vu venir.
        #
        # Le premier arbitre essayé était « le plus court », par analogie avec
        # le tri final. **Faux, et le cahier l'a dit** : *Drake Cutlass Black*
        # porte aussi le surnom de guilde « cutty black », plus court et à
        # score égal — la fiche du vaisseau se mettait à répondre sous un
        # alias qui n'explique pas « cutlass », et trois cas du cahier
        # tombaient. La règle du plus court départage des **entités** entre
        # elles ; à l'intérieur d'une entité, ce qui compte est la proximité
        # au terme tapé.
        if (current is None or weighted > current.score
                or (weighted == current.score
                    and _proximite(norm, row["alias"])
                    > _proximite(norm, current.alias))):
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
        # **Une plage, pas un `LIKE` — la leçon était déjà écrite cent lignes
        # plus bas, et cet étage-ci ne l'avait jamais reçue.** `LIKE` est
        # insensible à la casse par défaut, donc SQLite refuse d'utiliser
        # `ix_aliases_flat` et parcourt les 46 673 alias : mesuré le
        # 2026-08-16, **24,3 ms pour rendre 19 lignes**, soit la quasi-
        # totalité des 26 ms d'une résolution. Le planificateur le dit noir
        # sur blanc — `SCAN a USING COVERING INDEX` contre `SEARCH … (
        # alias_flat>? AND alias_flat<?)`.
        #
        # Le routeur résout un n-gramme après l'autre : ces millisecondes se
        # multiplient par question, et c'était le coût fixe de **tout** le
        # bot, pas une lenteur de `decrire`.
        #
        # La borne haute incrémente le dernier caractère du préfixe. Et
        # l'échappement disparaît : une plage compare littéralement, donc
        # `%` et `_` n'ont plus de sens particulier — c'est exactement la
        # sémantique qu'on voulait. L'égalité stricte est comprise dans la
        # plage, qui commence à `flat` lui-même.
        for row in con.execute(
            "SELECT entity_type, entity_id, alias, alias_norm, weight FROM aliases a "
            "WHERE (alias_flat >= ? AND alias_flat < ?)" + type_filter
            # Et si la plage déborde la limite, on garde les **plus courts**
            # — même règle que partout ailleurs. « casque » ouvre 811 alias
            # et « rsi » 755 : sans tri, les 200 retenus étaient ceux que le
            # plan d'accès amenait en premier, c'est-à-dire personne en
            # particulier.
            + " ORDER BY LENGTH(alias_flat) LIMIT 200",
            (flat, flat[:-1] + chr(ord(flat[-1]) + 1), *type_args),
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
    #
    # **C'est l'étage le plus cher du résolveur, et il ne se retire pas.**
    # Mesuré le 2026-08-16 : 60 ms pour lire les 40 419 alias, puis 99 ms de
    # `WRatio` en boucle Python. Il rattrape pourtant de vraies fautes de
    # frappe qu'aucun autre étage ne voit — « arcorp » sort *ArcCorp* à 90,
    # et le préfixe de l'étage 3 ne peut pas l'atteindre (« arcc » contre
    # « arco »). C'est le rôle qu'il a hérité du phonétique retiré.
    #
    # On le garde donc, et on le paie moins cher : la population est mise en
    # mémoire (elle ne change qu'à la réingestion, qui redémarre le
    # processus), et le balayage passe par `process.extract`, qui fait la
    # boucle en C. Mesuré : 99 → 52 ms. `process.cdist` a été essayé et est
    # **plus lent** (77 ms), y compris en multi-thread.
    if not pool:
        rows, choix = _population_fuzzy(con, entity_types)
        for indice, score in _RETENUS(norm, choix):
            # **Le filet ne consultait pas l'arbitre du projet.** « Qu'est-ce
            # que je peux faire dans le jeu » rendait *Breakfast Dog* : le
            # gramme « jeu » matche « Dog Petit Dé**jeu**ner » — son alias
            # français — à 90 par ressemblance de lettres. Trois caractères
            # qui expliquent un dix-huitième d'un nom.
            #
            # `mots_inexpliques` tranche exactement ce cas depuis toujours —
            # c'est lui qui sépare « gladiousse » (juste, 82) d'« omnisky xi »
            # (faux, 90) — et les quatre autres étages s'y réfèrent en aval.
            # Le fuzzy est le seul à rendre un candidat que rien ne
            # justifie, et il est aussi le seul dont le score ne dit rien de
            # la ressemblance **mot à mot**.
            #
            # Il ne tourne que si aucun autre étage n'a rien trouvé : le
            # contrôle ne se paie donc pas sur le chemin ordinaire.
            if mots_inexpliques(norm, rows[indice]["alias"]):
                continue
            offer(rows[indice], score, "fuzzy")

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
    # **Une livrée n'est pas le vaisseau qu'elle peint.** Rejeu du journal du
    # 2026-08-21 : « combien coûte un prospector » proposait *Prospector Polar
    # Livery* et *MISC Prospector Schematic* à côté du vaisseau, « hurricane »
    # rendait *Hurricane Flashfire Livery*, et « ou trouver les panther »
    # ouvrait sur *Prowler Panthera Livery*. Ce n'est pas un défaut de score :
    # les livrées portent **exactement** le nom du vaisseau plus un mot, donc
    # l'étage `flat` les sort à 93 quand le vaisseau lui-même est à 90.
    #
    # Le marqueur est en base et n'a jamais été lu : `items.type = 'Paints'`,
    # 1 077 objets dont les 928 « Livery » et les peintures de série. On ne le
    # cherche donc pas dans le **nom** — c'est la règle de la convention de
    # nommage, déjà payée deux fois.
    #
    # Reléguer, pas supprimer : « la livrée Flashfire de l'Hurricane » nomme
    # le cosmétique et doit continuer de le trouver. Le mot tapé rouvre le
    # passage, exactement comme pour les variantes.
    if not _MOTS_COSMETIQUES & set(norm.split()):
        peintures = _uuids_cosmetiques(con)
        for indice, candidat in enumerate(retenus):
            if candidat.entity_type == "item" and candidat.entity_id in peintures:
                retenus[indice] = dataclasses.replace(candidat, cosmetique=True)
    # À score égal, l'alias le plus court est le plus précis : « P6 LR » doit
    # sortir « P6-LR Sniper Rifle » avant « P6-LR "Archangel" Sniper Rifle ».
    ranked = sorted(retenus, key=lambda c: (c.cosmetique, -c.score,
                                            len(c.alias), c.alias))[:limit]
    return Resolution(text, _with_canonical(con, ranked))


#: La population du filet fuzzy appartient à une **connexion et une version
#: de ses données**, jamais à un simple chemin. Deux bases `:memory:` avaient
#: toutes deux la clé littérale `:memory:` ; la seconde recevait donc les
#: alias de la première. `data_version` couvre un autre écrivain,
#: `total_changes` les mutations de cette connexion et `schema_version` une
#: reconstruction de table. La petite LRU évite de retenir indéfiniment les
#: connexions de tests, `sqlite3.Connection` n'étant pas weak-référençable.
_POPULATIONS: OrderedDict[tuple, tuple[list, list[str]]] = OrderedDict()
_MAX_POPULATIONS = 16


def _proximite(norm: str, alias: str) -> tuple[float, int, str]:
    """De quoi départager deux alias de la **même** entité, à score égal.

    La ressemblance littérale d'abord — c'est elle qui écarte le surnom au
    profit du nom quand le joueur a tapé le nom. Puis, à ressemblance égale,
    le plus court, puis l'alphabet : il faut un ordre **total**, sinon
    l'arbitraire revient par la porte de derrière.
    """
    ecrit = normalize(alias)
    return (fuzz.ratio(norm, ecrit), -len(ecrit), ecrit)


def _population_fuzzy(con: sqlite3.Connection,
                      entity_types: tuple[str, ...] | None
                      ) -> tuple[list, list[str]]:
    """Les alias candidats au filet, et leurs formes normalisées."""
    cle = (*_identite_des_aliases(con), tuple(entity_types or ()))
    if cle not in _POPULATIONS:
        filtre, args = "", ()
        if entity_types:
            filtre = (" AND entity_type IN ("
                      + ",".join("?" * len(entity_types)) + ")")
            args = tuple(entity_types)
        lignes = con.execute(
            "SELECT entity_type, entity_id, alias, alias_norm, weight "
            "FROM aliases a WHERE 1 = 1" + filtre, args).fetchall()
        _POPULATIONS[cle] = (lignes, [r["alias_norm"] for r in lignes])
        while len(_POPULATIONS) > _MAX_POPULATIONS:
            _POPULATIONS.popitem(last=False)
    else:
        _POPULATIONS.move_to_end(cle)
    return _POPULATIONS[cle]


def _identite_des_aliases(con: sqlite3.Connection) -> tuple:
    """Identité des **données**, pas de l'objet connexion.

    L'ancre était la connexion elle-même, ce qui isolait correctement deux
    bases `:memory:` de tests mais faisait aussi que **deux connexions sur le
    même fichier ne partageaient rien**. Depuis que le cœur ouvre une
    connexion par thread (voir `api._con`, 2026-08-21), cela aurait voulu
    dire reconstruire les 46 000 alias du filet fuzzy une fois par thread de
    travail — et les chasser aussitôt, la petite LRU ne gardant que seize
    entrées.

    On s'ancre donc sur le **fichier** de la base principale. `:memory:` n'en
    a pas : on y retombe sur l'objet, ce qui préserve l'isolation des tests
    que ce détail a coûté une fois.
    """
    fichier = ""
    try:
        for _rang, nom, chemin in con.execute("PRAGMA database_list"):
            if nom == "main":
                fichier = chemin or ""
                break
    except sqlite3.Error:
        fichier = ""
    data_version = int(con.execute("PRAGMA data_version").fetchone()[0])
    schema_version = int(con.execute("PRAGMA schema_version").fetchone()[0])
    return (fichier or con), con.total_changes, data_version, schema_version


def _RETENUS(norm: str, choix: list[str]) -> list[tuple[int, float]]:
    """Les indices de `choix` dont le score atteint le plancher.

    `process.extract` rend `(valeur, score, indice)` ; on ne garde que ce
    dont `offer` a besoin. Le `score_cutoff` fait le filtrage **en C**, ce
    qui est tout l'intérêt par rapport à la boucle Python.
    """
    return [(indice, score) for _valeur, score, indice in process.extract(
        norm, choix, scorer=fuzz.WRatio,
        score_cutoff=config.FUZZY_FLOOR, limit=None)]


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
