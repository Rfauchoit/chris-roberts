"""Les questions que personne n'a pensé à poser.

Sprint 40 (voir [docs/SPRINT_40_BANC_ADVERSARIAL.md](../../docs/SPRINT_40_BANC_ADVERSARIAL.md)).

Le corpus de `banc.py` vient de trois sources — le cahier, la matrice de
croisement, et `data/journal.md`. C'est sa force : ce sont des questions
**réellement posées**. C'est aussi sa limite : **il ne contient que ce
qu'on a pensé à demander.** Les 79 outils y sont tous couverts, mais
couverts *par les questions qu'on connaît*.

Le journal dit ce qui **a raté**. Ce module dit ce qui **raterait** — et
c'est une différence de nature, parce que le joueur qui ne reçoit pas de
réponse ne revient pas le signaler.

## Le piège qui a dicté la conception

**Un générateur qui recopie les gabarits du routeur ne trouve rien.** Il
ferait exactement l'erreur du motif qui recopie son vocabulaire : ne
produire que ce que le routeur sait déjà lire, et se déclarer satisfait.

Les gabarits ci-dessous viennent donc de la **langue** — des tournures
qu'un joueur emploie — et non de `_INTENTS`. Ils sont volontairement
écrits sans regarder les motifs, et plusieurs sont des formulations que
le corpus n'a jamais vues : la négation, l'ordinal, trois contraintes
d'affilée.

## Ce qu'on mesure, et ce qu'on ne mesure pas

Une question générée **n'a pas de réponse attendue** : on ne mesure donc
pas la justesse mais la **couverture**, avec trois compteurs et pas un —
un routeur trop permissif répondrait 100 % en mentant, ce qui est pire
qu'un silence.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Any

#: La version du générateur. **Elle entre dans le rapport** : un cliquet
#: sur un corpus qui change ne compare rien, et changer les gabarits doit
#: remettre les planchers à zéro explicitement.
VERSION = 2

#: Les tournures, écrites depuis la **langue** et non depuis `_INTENTS`.
#: `{e}` = une entité tirée de la population nommée en second.
_GABARITS: tuple[tuple[str, str], ...] = (
    # Les fiches, sous les formes qu'un joueur tape vraiment.
    ("c'est quoi {e}", "item"),
    ("ça sert à quoi {e}", "item"),
    ("parle-moi {e}", "ship"),
    ("les stats {e}", "item"),
    ("combien coûte {e}", "item"),
    ("où acheter {e}", "item"),
    ("où trouver {e}", "item"),
    # Les questions de lieu.
    ("c'est où {e}", "lieu"),
    ("qu'est-ce qu'il y a {e}", "lieu"),
    ("comment aller {e}", "lieu"),
    # Les missions.
    ("c'est quoi la mission {e}", "contrat"),
    ("où prendre la mission {e}", "contrat"),
    ("ça paye combien {e}", "contrat"),
    # La fabrication.
    ("comment fabriquer {e}", "blueprint"),
    # **v2** : « il faut quoi pour du X » n'était pas du français, et sa
    # muette n'était donc pas un trou du routeur mais un défaut du
    # générateur. La vraie tournure porte le verbe, et elle répondait
    # déjà. Un banc qui fabrique des questions qu'aucun joueur ne poserait
    # mesure sa propre maladresse.
    ("il faut quoi pour fabriquer {e}", "blueprint"),
    # **Les formes que le corpus ne contient pas.** C'est là que le banc
    # gagne son nom : la négation, l'ordinal, la comparaison à trois.
    ("quel est le deuxième meilleur {e}", "famille"),
    ("qu'est-ce qui ne se vend pas comme {e}", "item"),
    ("y a-t-il mieux que {e}", "item"),
    ("{e} ou son concurrent", "ship"),
    ("est-ce que {e} vaut le coup", "ship"),
)

#: Les compléments qui empilent des contraintes — le cas où une réponse
#: partielle se fait passer pour complète.
_CONTRAINTES: tuple[str, ...] = (
    "", "", "",                       # le plus souvent, aucune
    " à Stanton", " dans Pyro",
    " sous 50000 aUEC", " de taille 2",
    " sous 50000 aUEC à Stanton",
    " de taille 2 dans Pyro sous 100000 aUEC",
)


@dataclass(frozen=True)
class CasEtiquete:
    """Une petite question dont le bon comportement a été arbitré humainement."""

    texte: str
    attendus: tuple[str, ...]


#: La génération aléatoire mesure si **quelque chose** répond. Ce petit oracle
#: séparé mesure si la réponse part au bon endroit. Il contient aussi deux
#: opinions sous-spécifiées : leur réponse juste est le `repli` qui demande
#: l'axe, pas un outil choisi au hasard.
VERSION_ORACLE = 1
ECHANTILLON_ETIQUETE: tuple[CasEtiquete, ...] = (
    CasEtiquete("c'est quoi le Aegis Gladius", ("decrire",)),
    CasEtiquete("parle-moi du Drake Cutlass Black", ("decrire",)),
    CasEtiquete("combien coûte le Aegis Gladius", ("get_price",)),
    CasEtiquete("les stats du P8-AR Rifle", ("get_item_stats",)),
    CasEtiquete("où acheter le P8-AR Rifle", ("get_price",)),
    CasEtiquete("c'est où Orison", ("where_is_location",)),
    CasEtiquete("qu'est-ce qu'il y a à Orison", ("que_trouve_t_on",)),
    CasEtiquete("comment aller à Orison", ("peut_voyager",)),
    CasEtiquete("c'est quoi la mission The Price of Freedom", ("decrire",)),
    CasEtiquete("ça paye combien The Price of Freedom",
                ("get_mission_reputation",)),
    CasEtiquete("comment fabriquer P6-LR Sniper Rifle", ("get_blueprint",)),
    CasEtiquete("quel est le deuxième meilleur canon", ("compare_items",)),
    CasEtiquete("P8-AR Rifle ou son concurrent", ("repli",)),
    CasEtiquete("est-ce que le Aegis Gladius vaut le coup", ("repli",)),
)


@dataclass(frozen=True)
class QuestionGeneree:
    """Une question plausible, et de quoi la reproduire."""

    texte: str
    gabarit: str
    population: str


def _populations(con: sqlite3.Connection) -> dict[str, list[str]]:
    """Les noms réels, par famille. Rien n'est inventé — §7."""
    def noms(sql: str) -> list[str]:
        return [r[0] for r in con.execute(sql) if r[0]]

    return {
        "item": noms("SELECT name FROM items WHERE name IS NOT NULL "
                     "AND name NOT LIKE 'PLACEHOLDER%' AND name NOT LIKE '[PH]%'"),
        "ship": noms("SELECT name FROM ships WHERE name IS NOT NULL"),
        "lieu": noms("SELECT name FROM starmap WHERE name IS NOT NULL"),
        "contrat": noms("SELECT title FROM contracts WHERE title IS NOT NULL "
                        "AND not_for_release = 0 AND title NOT LIKE '%[%' "
                        "AND title NOT LIKE '%UNINITIALIZED%'"),
        "blueprint": noms("SELECT output_name FROM blueprints "
                          "WHERE output_name IS NOT NULL"),
        # Les familles ne sont pas des entités : ce sont les mots du
        # vocabulaire fermé, tirés de la source unique.
        "famille": _familles(),
    }


def _familles() -> list[str]:
    from .armurerie import _FAMILLES_D_OBJETS
    from .equipement import _FAMILLES

    return ([libelle for libelle, *_ in _FAMILLES_D_OBJETS]
            + [nom for nom, *_ in _FAMILLES])


def generer(con: sqlite3.Connection, *, combien: int = 500,
            graine: int = 7) -> list[QuestionGeneree]:
    """`combien` questions plausibles, **déterministes**.

    La graine est fixe : un banc qui change de questions à chaque
    exécution ne se compare pas à lui-même — c'est la règle de
    l'échantillon à graine fixe, déjà payée sur `test_generalisation`.
    """
    tirage = random.Random(graine)
    populations = _populations(con)
    questions: list[QuestionGeneree] = []
    for _ in range(combien):
        gabarit, famille = tirage.choice(_GABARITS)
        noms = populations.get(famille) or []
        if not noms:
            continue
        nom = tirage.choice(noms)
        # L'article se colle au gabarit, pas au nom : « c'est quoi le
        # Gladius » et « parle-moi du Gladius » ne prennent pas le même.
        texte = gabarit.format(e=_avec_article(gabarit, nom))
        questions.append(QuestionGeneree(
            texte=texte + tirage.choice(_CONTRAINTES),
            gabarit=gabarit, population=famille))
    return questions


def _avec_article(gabarit: str, nom: str) -> str:
    """« parle-moi **du** Gladius », « c'est quoi **le** Gladius ».

    Approximation assumée : on ne cherche pas le genre du nom, qui est un
    nom propre anglais dans l'immense majorité des cas. Ce qui compte est
    que la question **ressemble à une question**, pas qu'elle soit
    parfaite — le routeur doit la lire comme il lirait un joueur pressé.
    """
    if gabarit.startswith("parle-moi"):
        return f"du {nom}"
    if gabarit.startswith("il faut quoi pour fabriquer"):
        return f"un {nom}"
    if gabarit.startswith(("c'est où", "qu'est-ce qu'il y a")):
        return f"à {nom}"
    if gabarit.startswith("comment aller"):
        return f"à {nom}"
    if gabarit.startswith(("ça sert à quoi", "les stats", "ça paye combien")):
        return f"du {nom}"
    return nom


@dataclass
class Verdict:
    """Ce qu'une question générée est devenue."""

    question: QuestionGeneree
    outil: str | None
    confiance: float
    servie: bool
    echec: str | None = None


@dataclass(frozen=True)
class VerdictExactitude:
    cas: CasEtiquete
    obtenu: str | None
    executable: bool

    @property
    def juste(self) -> bool:
        return self.executable and self.obtenu in self.cas.attendus


def jouer(con: sqlite3.Connection,
          questions: list[QuestionGeneree]) -> list[Verdict]:
    """Chaque question passe par le **routeur**, puis par l'outil.

    Le chemin complet, pas `route()` seul : un routage juste qui lève à
    l'exécution est un échec pour le joueur. C'est la leçon du harnais de
    balayage — emprunter le même chemin que l'outil réel.
    """
    from . import router
    from .router.deterministic import route

    from . import repli

    verdicts: list[Verdict] = []
    for question in questions:
        appel = route(con, question.texte)
        if appel is None:
            # **Muet et sans réponse ne sont plus la même chose.** Depuis
            # l'US-5, une question que le routeur ne sait pas router reçoit
            # les axes qu'on sait servir sur ce qu'elle a nommé. Le banc
            # doit voir ce que le joueur reçoit, pas ce que le routeur
            # rend — sinon il compte un trou là où il y a une réponse.
            genre = "repli" if repli.proposer(con, question.texte) else "muet"
            verdicts.append(Verdict(question, None, 0.0, False, genre))
            continue
        try:
            router.execute(con, appel)
        except Exception as exc:                              # noqa: BLE001
            # Une absence **expliquée** est une réponse, pas un echec :
            # c'est tout le sens du champ pose au sprint 39.
            if getattr(exc, "explication", None):
                verdicts.append(Verdict(question, appel.tool,
                                        appel.confidence, True))
            else:
                verdicts.append(Verdict(
                    question, appel.tool, appel.confidence, False,
                    type(exc).__name__))
            continue
        verdicts.append(Verdict(question, appel.tool, appel.confidence, True))
    return verdicts


def jouer_exactitude(
        con: sqlite3.Connection,
        cas: tuple[CasEtiquete, ...] = ECHANTILLON_ETIQUETE,
        ) -> list[VerdictExactitude]:
    """Joue l'oracle étiqueté, distinct de la génération de couverture."""
    from . import repli, router
    from .router.deterministic import route

    verdicts = []
    for question in cas:
        appel = route(con, question.texte)
        if appel is None:
            obtenu = "repli" if repli.proposer(con, question.texte) else None
            verdicts.append(VerdictExactitude(
                question, obtenu, executable=obtenu == "repli"))
            continue
        try:
            router.execute(con, appel)
            executable = True
        except Exception as exc:                              # noqa: BLE001
            executable = bool(getattr(exc, "explication", None))
        verdicts.append(VerdictExactitude(question, appel.tool, executable))
    return verdicts


def bilan_exactitude(verdicts: list[VerdictExactitude]) -> dict[str, Any]:
    """La justesse de l'échantillon arbitré — jamais la couverture générée."""
    justes = [verdict for verdict in verdicts if verdict.juste]
    total = len(verdicts)
    return {
        "version_oracle": VERSION_ORACLE,
        "etiquetees": total,
        "justes": len(justes),
        "taux_exactitude": 100.0 * len(justes) / total if total else 0.0,
        "erreurs": [
            {"question": verdict.cas.texte,
             "attendus": verdict.cas.attendus,
             "obtenu": verdict.obtenu,
             "executable": verdict.executable}
            for verdict in verdicts if not verdict.juste
        ],
    }


def bilan(verdicts: list[Verdict]) -> dict[str, Any]:
    """La **couverture** générée, avec trois compteurs et pas un.

    Un taux de reponse brut ne suffit pas : un routeur trop permissif
    repondrait 100 % en mentant, et c'est pire qu'un silence — la regle
    est ecrite noir sur blanc dans `CLAUDE.md`.

    - **servies** : une reponse est rendue ;
    - **muettes** : « je n'ai pas compris » — le trou honnete ;
    - **suspectes** : servies, mais sous le seuil de reserve. C'est la
      plus precieuse des trois : la reponse assuree sur le mauvais outil
      ne se voit nulle part ailleurs.
    """
    from . import config

    servies = [v for v in verdicts if v.servie]
    muettes = [v for v in verdicts if not v.servie and v.echec == "muet"]
    # Un repli n'est **pas** une réponse servie — aucun outil n'a répondu, et
    # le trou de routage reste entier. Il se compte à part parce qu'il change
    # ce que le joueur reçoit : trois questions posables au lieu du vide.
    replis = [v for v in verdicts if v.echec == "repli"]
    plantees = [v for v in verdicts
                if not v.servie and v.echec not in ("muet", "repli")]
    suspectes = [v for v in servies
                 if v.confiance < config.ROUTER_MIN_CONFIDENCE]
    total = len(verdicts) or 1
    return {
        "total": len(verdicts), "version": VERSION,
        "servies": len(servies), "muettes": len(muettes),
        "replis": len(replis),
        "plantees": len(plantees), "suspectes": len(suspectes),
        "taux_servi": 100.0 * len(servies) / total,
        "taux_couverture_outil": 100.0 * len(servies) / total,
        "taux_suspect": 100.0 * len(suspectes) / total,
        # Ce que le joueur reçoit vraiment : une réponse d'outil, ou à
        # défaut de quoi reformuler. Le vrai silence est ce qui reste.
        "taux_avec_quelque_chose": 100.0 * (len(servies) + len(replis)) / total,
        "taux_couverture_utile": (
            100.0 * (len(servies) + len(replis)) / total),
        # **On echantillonne pour lecture humaine.** Une question generee
        # peut etre absurde (« la portee du Stanton System ») : un taux de
        # muettes ne veut rien dire seul, il faut les lire.
        "exemples_muets": [v.question.texte for v in muettes[:20]],
        "exemples_suspects": [(round(v.confiance, 2), v.outil,
                               v.question.texte) for v in suspectes[:20]],
        "exemples_plantes": [(v.echec, v.outil, v.question.texte)
                             for v in plantees[:20]],
    }
