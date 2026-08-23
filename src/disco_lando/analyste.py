"""L'analyste — Sol ou Sonnet sur abonnement, derrière le déterministe.

Demande de l'utilisateur (2026-08-07) : répondre aux questions que la donnée
sait servir mais qu'aucun outil déterministe ne formule — « le ratio
DPS/coût de la balle entre un P4-AR et un P6-LR ». L'étage interroge un CLI
headless via l'abonnement du compte connecté : Codex avec GPT-5.6 Sol, ou
Claude avec Sonnet. Aucune clé d'API ; le CLI retenu doit être loggé une fois
sur la machine qui héberge le cœur.

Le §7 tient par construction : Sonnet n'autorise qu'une commande ; Sol n'a
ni shell ni web et ne reçoit qu'un outil MCP. Les deux portes aboutissent à
`scripts/requete.py`, SQLite en lecture seule. Le modèle ne complète pas,
il n'estime pas — s'il ne trouve pas, il le dit.

`DISCO_ANALYSTE=gpt-5.6-sol` l'active. Éteint par défaut : les tests, le banc et
le balayage ne doivent jamais consommer le quota — même politique que
l'épinglage `ROUTER=deterministic` de conftest.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from . import config

#: Les appels en cours, pour que la même question posée deux fois en
#: parallèle ne parte qu'une fois chez le fournisseur configuré.
_EN_VOL: dict[str, threading.Event] = {}
_EN_VOL_VERROU = threading.Lock()


@dataclasses.dataclass
class _SessionCodex:
    """Un fil Codex réutilisable, jamais une instance de modèle locale."""

    thread_id: str | None = None
    au: float = dataclasses.field(default_factory=time.monotonic)
    tours: int = 0
    verrou: threading.Lock = dataclasses.field(
        default_factory=threading.Lock, repr=False)


# Un fil par conversation et par modèle : deux guildeux, ou Sol et un autre
# modèle, ne doivent jamais partager leur historique. La borne mémoire reprend
# celle du contexte métier ; les rollouts eux-mêmes appartiennent au CLI Codex.
_SESSIONS_CODEX: dict[tuple[str, str], _SessionCodex] = {}
_SESSIONS_CODEX_VERROU = threading.Lock()
_SESSIONS_CODEX_MAX = 500

_LOG = logging.getLogger(__name__)

#: Ce que le modèle répond quand la question n'est pas une question de
#: données — le mot est improbable dans une vraie réponse française.
_PASSE = "HORS-SUJET"

#: Le fournisseur et la forme font partie du résultat mis en cache. Sans
#: version, le passage de Sonnet à Sol resservirait indéfiniment les anciennes
#: réponses, précisément au moment où l'on cherche un autre calibrage.
#: La 5 retire l'étiquette « logique : » de la phrase d'explication
#: (demande de l'utilisateur, 2026-08-10). Monter la version périme le
#: cache : sans ça, les réponses déjà servies ressortiraient étiquetées,
#: et le correctif aurait l'air de ne pas marcher. La version 6 expose
#: l'alpha par projectile dans le catalogue : l'ancienne réponse Sonnet
#: écartait à tort le Sledge III parce que sa famille amont est vide. La 7
#: fixe la sémantique des comptes de missions (titres jouables, pas lignes).
#: La 8 expose les arguments optionnels de chaque outil : sans eux, Sonnet
#: appelait `missions_payantes` sans seuil puis recomptait sa limite de 8. La
#: 9 interdit de généraliser une propriété de l'extrait limité à son total.
#: La 10 empêche qu'un accessoire trouvé par `LIKE` masque un vaisseau dont
#: le nom correspond exactement à la question. La 11 expose aussi les
#: homonymes typés de `decrire` pour les formulations courtes comme « Wolf ».
#: La 12 distingue enfin le grain « contrat UUID » du grain « titre de
#: mission » au lieu d'appliquer la règle des missions aux deux mots. La 13
#: interdit un verdict de matchup tiré d'un duel calculé dans un seul sens.
#: La 14 impose le feu à équipage complet sur un vaisseau à tourelles —
#: l'analyste a brodé sur le seul `pilot_dps` de deux biplaces (journal du
#: 2026-08-12, Scorpius/Hurricane). La 15 apprend le réseau d'énergie
#: (`item_reseau`) et ses deux pièges : pips et unités standard ne
#: s'additionnent pas, et la passerelle pips → capacitor n'est pas publiée.
#: La 16 : le duel accepte un `loadout="meilleur"` résolu, et ses temps
#: sont pondérés par la mobilité ET la poursuite des armes (un canon lent
#: rate un chasseur vif) — préférer l'outil au calcul à la main.
_VERSION_PROMPT = 16

_ACCES_CLAUDE = """- Tu n'as qu'UNE commande, écrite EXACTEMENT sous ces \
deux formes (tu es déjà dans le bon répertoire ; toute variante — cd ou \
PowerShell — sera bloquée) :
  {porte} "SELECT …"
  {porte} outil <nom> '{{"query": "…"}}'
  (le JSON entre quotes SIMPLES — exemple complet :
  {porte} outil peut_detruire '{{"query": "avenger titan", \
"cible": "hammerhead", "arme": "deadbolt iii"}}')"""

_ACCES_CODEX = """- Tu n'as qu'UN outil MCP : `interroger`. Le shell, le \
web et les fichiers sont volontairement indisponibles. Pour une agrégation, \
appelle `interroger` avec `sql` (SELECT ou WITH). Pour une règle métier déjà \
validée, appelle-le avec `outil` et l'objet `arguments`."""

_STYLE_SOL = """

Calibrage de la réponse pour GPT-5.6 Sol :
- Commence par une phrase qui donne le résultat exact et répond littéralement \
au critère demandé.
- Ajoute le calcul ou la méthode qui permet de le vérifier, puis UN détail \
utile directement présent dans le même résultat : taille de l'échantillon, \
couverture, limite ou conséquence pratique. N'ajoute rien si aucun détail ne \
sert la décision.
- N'ajoute pas spontanément le second d'un classement. Tout chiffre \
secondaire doit avoir été explicitement récupéré avec exactement le même \
critère que le résultat principal ; sinon, omets-le.
- Une réserve ne remplace jamais le classement demandé : donne d'abord le \
résultat brut, puis la réserve et le résultat alternatif qu'elle produirait.
- Vise normalement 4 à 8 lignes. Une question de compte très simple peut \
tenir en 2 ou 3 lignes ; un duel complexe peut utiliser au plus 3 puces.
- Ne donne ni liste exhaustive ni détails de SQL sauf si la question les \
demande."""

_CONSIGNES = """Tu es l'analyste de données d'un assistant Star Citizen \
francophone. Tu réponds à UNE question en interrogeant une base SQLite en \
lecture seule.

Règles absolues :
- Tout chiffre de ta réponse vient d'une commande que tu as exécutée — tu \
ne complètes jamais de mémoire, tu n'estimes pas, tu n'arrondis pas une \
donnée de jeu. Si la donnée manque, dis-le.
{acces}
- Le mode « outil » rejoue les fonctions déjà validées du projet — \
PRÉFÈRE-LE au SQL quand un outil du catalogue couvre la question ou un \
morceau : le duel (peut_detruire) sait la déflexion, le capacitor, le \
meilleur loadout résolu (`loadout="meilleur"`) et pondère ses temps par \
la mobilité et la poursuite des armes ; les \
matchups (matchups_vaisseau) calculent la riposte dans les deux sens, les \
fiches savent les unités et les pièges. Le SQL sert pour combiner, agréger \
ou croiser ce qu'aucun outil ne fait.
- Réponds en français, court et direct — c'est un joueur qui lit, sur \
Discord. Donne le calcul quand tu combines des chiffres (« 375,7 DPS ÷ \
26 aUEC la balle = 14,4 »), et termine par une phrase d'explication quand \
elle éclaire le résultat (« un fusil d'assaut contre un fusil de précision, \
dont chaque balle coûte cher ») — les joueurs la lisent, et c'est ce qui \
distingue une réponse d'un tableau de chiffres. **N'annonce jamais cette \
phrase par une étiquette** — pas de « logique : », « explication : », \
« note : » : on comprend sans qu'on le dise, et l'étiquette fait \
administratif. Demande de l'utilisateur, 2026-08-10.
- Si la question n'est PAS une question de données Star Citizen (météo, \
politique, bavardage), réponds exactement : {passe}

Pièges mesurés du projet (chacun a coûté un incident) :
- un prix à 0 n'est pas un prix : l'objet ne se vend pas, il se mine ou se \
fabrique ;
- uex_prices se rapproche des ingrédients par le NOM, jamais par ref_uuid ;
- l'ammo_capacity d'une arme de vaisseau est la réserve entière, pas un \
chargeur ; l'alpha se juge PAR PLOMB (alpha ÷ pellets_per_shot) ;
- le volume d'un objet est volume_uscu en µSCU (le champ SCU arrondit à 0) ;
- UEX écrit « GrimHEX », le jeu « Grim HEX » — essayer les deux formes ;
- une valeur NULL n'est pas un zéro, et une colonne pleine peut être vide \
de sens ;
- ships porte des lignes EN DOUBLE par nom : tout compte ou somme se fait \
sur des noms DISTINCTS (SELECT DISTINCT name, …) ;
- un matchup, une domination ou « qui gagne » ne se déduit JAMAIS d'un seul \
sens : appelle matchups_vaisseau, ou calcule attaquant → cible ET cible → \
attaquant avec les mêmes hypothèses. Si un sens manque, dis qu'il manque ; \
ne le transforme ni en victoire ni en domination ;
- le `pilot_dps` de `ships` n'est PAS le feu d'un vaisseau à tourelles : \
il ampute un biplace de la moitié de son armement. Somme `ship_armes` \
(hors poste 'pdc') ou lis le champ `feu` des profils de matchup, cite le \
feu total à équipage complet ET le nombre de joueurs qu'il exige — une \
tourelle habitée ou télécommandée est un joueur de plus ;
- le réseau d'énergie vit dans `item_reseau` (état 'Online') : \
`pips_conso` compte en pips ENTIERS (les barres de l'interface), \
`std_conso` en unités standard fractionnaires (armes ~1,0 à l'énergie, \
~0,1 au balistique ; quantum drive 2-3). Les deux unités ne \
s'additionnent JAMAIS — la passerelle n'est pas publiée. Le lien pips → \
régénération du capacitor d'arme n'est pas publié non plus : dis-le au \
lieu de l'inventer. Préfère les outils budget_energie, \
composants_par_pip, loadout_energie et loadout_discret ;
- quand plusieurs relevés de prix existent, chiffre au prix MOYEN par \
objet et dis-le (« au prix moyen relevé ») — pas au minimum d'un terminal.
- pour compter des missions, compte les titres DISTINCTS publiés \
(`not_for_release = 0`, `work_in_progress = 0`) et écarte les titres qui ne \
sont que des jetons entre crochets ; préfère l'outil `missions_payantes` \
pour un seuil de récompense, car il applique exactement « plus de » ou \
« au moins » et la règle compte = liste affichable.
- « mission » et « contrat » ne sont pas le même grain : si la question \
demande des contrats qui donnent un blueprint, compte les `contract_uuid` \
DISTINCTS publiés dans `contract_reward_pools` ; donne séparément le nombre \
de titres de missions affichables si cela aide, sans remplacer le compte \
demandé. L'outil `combien_y_a_t_il` applique déjà cette distinction.
- Les outils rendent souvent un `total` et seulement un extrait borné \
(`missions`, `items`, etc.). Si le total dépasse la longueur de l'extrait, \
ne dis JAMAIS « tous/toutes » ni ne généralise une organisation, une famille \
ou une propriété vue dans l'extrait ; fais une agrégation SQL séparée ou \
omets cette affirmation.
- Un même mot peut nommer un vaisseau, un objet ou un lieu. Ne conclus JAMAIS \
au type depuis le premier `LIKE` trouvé dans une seule table. Commence par \
l'outil `decrire` : son champ `homonymes` expose les meilleurs candidats des \
autres types. Choisis celui dont le type est compatible avec la question, puis \
appelle l'outil typé adapté (`get_ship_stats` pour un vaisseau). Tu peux aussi \
vérifier les correspondances exactes dans toutes les tables candidates. Un \
nom exact bat un accessoire qui ne fait que le contenir ; si plusieurs types \
compatibles subsistent, annonce l'ambiguïté au lieu d'en inventer un.

Vise 1 à 3 appels de données au total. Le schéma complet est ci-dessous — NE PERDS \
PAS de requête à le redécouvrir. Les noms sont en anglais ; les libellés \
français vivent dans traductions (cle, texte). item_stats se joint sur \
item_uuid = items.uuid. Les vues préfixées `guilde_` viennent du registre \
multi-membres : leurs sources et confiances doivent toujours être citées. \
Elles forment la surface publique ; l'authentification n'est pas accessible.

Schéma :
{schema}

Catalogue des outils (mode « outil » — argument principal "query", certains \
acceptent des options citées dans leur description) :
{catalogue}"""


import re

# Le vocabulaire qui trahit une question d'**analyse** — un calcul entre
# colonnes qu'aucun outil déterministe ne formule. Mesuré contre les 300
# questions du corpus : une seule collision, « armure moyenne » (la classe
# d'armure) — d'où la forme analytique exigée sur « moyenne ».
_VOCABULAIRE_ANALYSE = re.compile(
    r"\bratios?\b|\bmoyenne des?\b|\ben moyenne\b|\bmedianes?\b|"
    r"\bcorrelation\b|\becart[- ]type\b|\bsur (?:le )?cout\b|"
    r"\bcout par\b|\bpar auec\b|\bpar credit\b")


def question_d_analyse(question: str) -> bool:
    """La question demande un calcul entre colonnes, pas un outil.

    « Le ratio DPS/coût de la balle entre un P4-AR et un P6-LR » partait
    chez `get_item_stats` par le mot « dps » et recevait la fiche d'une
    seule arme : le vocabulaire d'analyse doit passer devant le routeur.
    """
    from .normalize import normalize
    return bool(_VOCABULAIRE_ANALYSE.search(normalize(question)))


#: Les noms de modèle que le joueur emploie, ramenés à leur porte. Le
#: dialogue les traduit déjà, mais `basculer()` est une API publique : un
#: appelant qui dit « sonnet » ne doit pas se voir servir Codex en silence.
_ALIAS_FOURNISSEUR = {
    "claude": "claude", "sonnet": "claude",
    "codex": "codex", "sol": "codex",
}


def _fournisseur(force: str | None = None) -> str:
    """Le fournisseur forcé, explicite, ou déduit du nom du modèle."""
    if force and force.strip().lower() in _ALIAS_FOURNISSEUR:
        return _ALIAS_FOURNISSEUR[force.strip().lower()]
    explicite = config.ANALYSTE_FOURNISSEUR.strip().lower()
    if explicite in ("claude", "codex"):
        return explicite
    return "codex" if config.ANALYSTE.lower().startswith("gpt-") else "claude"


def _modele(fournisseur: str | None = None) -> str:
    """Le modèle du fournisseur demandé, sans modifier le réglage global."""
    choisi = _fournisseur(fournisseur)
    if choisi == _fournisseur() and config.ANALYSTE:
        return config.ANALYSTE
    return (config.ANALYSTE_CODEX if choisi == "codex"
            else config.ANALYSTE_CLAUDE)


def libelle(fournisseur: str | None = None) -> str:
    """Le nom court affiché au joueur dans la provenance de la réponse."""
    connus = {"gpt-5.6-sol": "GPT-5.6 Sol", "sonnet": "Sonnet"}
    modele = _modele(fournisseur)
    return connus.get(modele.lower(), modele)


def _commande_claude() -> list[str] | None:
    """Le CLI Claude, appelé sans passer par cmd.exe.

    `claude` sous Windows est un `.CMD` : le lancer fait réinterpréter les
    arguments par cmd.exe, qui mange les `%` — et les consignes contiennent
    « LIKE '%P4-AR%' ». Mesuré : l'allowlist en sortait corrompue et chaque
    requête restait bloquée à l'approbation, alors que la même commande
    sans consignes passait. npm dépose à côté du `.CMD` un vrai
    `claude.exe` (bin/ du paquet) : un binaire natif ne réinterprète rien.
    """
    from pathlib import Path

    cli = shutil.which("claude")
    if cli is None:
        return None
    if cli.lower().endswith((".cmd", ".bat")):
        exe = (Path(cli).parent / "node_modules" / "@anthropic-ai"
               / "claude-code" / "bin" / "claude.exe")
        if exe.exists():
            return [str(exe)]
    return [cli]


def _commande_codex() -> list[str] | None:
    """Le binaire Codex natif, en préférant le cache exécutable de l'app.

    Le raccourci du paquet WindowsApps est visible dans PATH mais Windows le
    refuse à un sous-processus du cœur. L'app copie le même binaire dans
    ``%LOCALAPPDATA%/OpenAI/Codex/bin/<version>`` ; c'est cette copie qui a
    servi le banc mesuré du 2026-08-08.
    """
    local = os.environ.get("LOCALAPPDATA")
    if local:
        racine = Path(local) / "OpenAI" / "Codex" / "bin"
        if racine.exists():
            candidats = sorted(racine.glob("*/codex.exe"),
                               key=lambda p: p.stat().st_mtime,
                               reverse=True)
            if candidats:
                return [str(candidats[0])]
    cli = shutil.which("codex")
    return [cli] if cli else None


def _commande_cli(fournisseur: str | None = None) -> list[str] | None:
    return (_commande_codex() if _fournisseur(fournisseur) == "codex"
            else _commande_claude())


def disponible(fournisseur: str | None = None) -> bool:
    """L'étage n'existe que si on l'a demandé ET que le CLI est là."""
    return bool(config.ANALYSTE) and _commande_cli(fournisseur) is not None


def basculer(fournisseur: str) -> dict[str, object]:
    """Change le fournisseur par défaut **sans redémarrer le cœur**.

    Demande de l'utilisateur (2026-08-10) : « changer le modèle à la volée
    selon où j'ai des tokens ». Le forçage par question (« demande à
    Sonnet : … ») existait déjà mais ne valait que pour elle ; ici on
    déplace le défaut, pour toutes les questions suivantes.

    Le réglage vit dans le processus du cœur : il ne survit pas à un
    redémarrage, où `DISCO_ANALYSTE` reprend la main. C'est voulu — un
    interrupteur d'ambiance ne doit pas réécrire la configuration de la
    machine dans le dos du joueur.

    Rendre l'état plutôt que rien : l'appelant annonce le nouveau
    fournisseur et **dit si son CLI répond**, sinon la bascule serait un
    silence qui ressemble à une panne au tour suivant.
    """
    choisi = _fournisseur(fournisseur)
    config.ANALYSTE_FOURNISSEUR = choisi
    # `ANALYSTE` reste l'interrupteur d'activation : on ne l'allume pas ici
    # (les tests et le balayage ne doivent jamais consommer de quota), on
    # ne fait que remplacer le modèle quand il était déjà allumé.
    if config.ANALYSTE:
        config.ANALYSTE = (config.ANALYSTE_CODEX if choisi == "codex"
                           else config.ANALYSTE_CLAUDE)
    return {"fournisseur": choisi, "modele": _modele(choisi),
            "libelle": libelle(choisi), "actif": bool(config.ANALYSTE),
            "cli_present": _commande_cli(choisi) is not None}


def etat() -> dict[str, object]:
    """État observable de l'analyste, sans chemin local ni secret.

    Le voyant du cœur disait seulement « API vivante ». Or une installation
    peut répondre en déterministe tout en ayant perdu précisément son filet de
    sécurité LLM. Le diagnostic distingue donc activation, fournisseur par
    défaut et présence de chaque CLI — notamment Codex présent / Claude absent
    sur la machine actuelle.
    """
    fournisseur = _fournisseur()
    active = bool(config.ANALYSTE)
    maintenant = time.monotonic()
    with _SESSIONS_CODEX_VERROU:
        sessions_actives = sum(
            maintenant - fil.au < config.ANALYSTE_SESSION_TTL
            for fil in _SESSIONS_CODEX.values())
    return {
        "active": active,
        "fournisseur": fournisseur,
        "modele": _modele(fournisseur),
        "disponible": disponible(fournisseur),
        "fournisseurs": {
            nom: {"modele": _modele(nom),
                  "cli_disponible": _commande_cli(nom) is not None}
            for nom in ("codex", "claude")
        },
        "reprise_codex": {
            "ttl_inactivite_secondes": config.ANALYSTE_SESSION_TTL,
            "max_tours": config.ANALYSTE_SESSION_MAX_TOURS,
            "sessions_actives": sessions_actives,
        },
    }


_SCHEMA_CACHE: str | None = None


def _schema() -> str:
    """Le schéma de la base, compact, pour l'inclure dans les consignes.

    La moitié du temps de réponse partait en requêtes de découverte —
    sqlite_master, PRAGMA table_info, tâtonnements sur les noms de
    colonnes. Le schéma tient en ~1 500 tokens : le donner d'entrée fait
    passer le modèle de 4-6 requêtes à 1-2. Construit une fois par
    processus ; la base ne change qu'à la réingestion, qui redémarre tout.
    """
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    lignes = []
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'aliases_fts%' "
            "ORDER BY name")]
        for table in tables:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            lignes.append(f"{table}({', '.join(cols)})")
        if config.GUILD_DB_PATH.exists():
            from .guilde import exposition
            exposition.installer(con, config.GUILD_DB_PATH)
            lignes.extend(exposition.schema(con))
    except sqlite3.Error:
        return ""
    finally:
        con.close()
    _SCHEMA_CACHE = "\n".join(lignes)
    return _SCHEMA_CACHE


_CATALOGUE_CACHE: str | None = None


def _catalogue() -> str:
    """Les outils validés, une ligne chacun, pour le mode « outil »."""
    global _CATALOGUE_CACHE
    if _CATALOGUE_CACHE is None:
        from . import router

        def ligne(t) -> str:
            parametres = []
            for p in inspect.signature(t.fn).parameters.values():
                if p.name == "con" or p.kind in {
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD}:
                    continue
                optionnel = p.default is not inspect.Parameter.empty
                parametres.append(p.name + ("?" if optionnel else ""))
            arguments = ", ".join(parametres)
            return (f"- {t.name} (arguments JSON : {arguments}) : "
                    f"{t.description}")

        _CATALOGUE_CACHE = "\n".join(
            ligne(t) for t in router.TOOLS.values())
    return _CATALOGUE_CACHE


#: Les réponses déjà rendues vivent dans un **fichier**, sans expiration de
#: temps : les questions d'une guilde reviennent d'une soirée à l'autre,
#: pas dans l'heure (remarque de l'utilisateur, 2026-08-08). Une entrée
#: reste valable tant que la base n'a pas changé — l'**époque** (build
#: ingéré + dernier relevé UEX + compte de traductions) remplace la durée,
#: et tout rafraîchissement invalide tout : une réponse exacte sur des
#: prix périmés serait un mensonge à retardement.
_CACHE: dict[str, dict] = {}
_CACHE_CHARGE = False
_CACHE_MAX = 500


#: Ce qui, dans une question ou une réponse, trahit une dépendance aux
#: relevés UEX : les prix bougent à chaque `disco uex`, le reste de la
#: base ne bouge qu'au build. Remarque de l'utilisateur (2026-08-08) : un
#: rafraîchissement de prix ne doit pas périmer « combien de vaisseaux
#: Anvil ». Approximation assumée : dans le doute (vocabulaire présent),
#: on périme — jamais l'inverse.
_VOCABULAIRE_UEX = re.compile(
    r"\bprix\b|\bcout\w*|\bco[uû]te\w*|\bauec\b|\buec\b|\bachat\b|"
    r"\bacheter\b|\bvendre?\b|\bvente\b|\blouer\b|\blocation\b|"
    r"\brentab\w*|\bmarge\b|\brelev\w*|\bterminal\b|\braffiner\w*")

# Une réponse inter-membres se périme dès qu'une preuve Game.log, une
# déclaration ou une réputation change. L'empreinte est séparée du build du
# jeu : une synchronisation de guilde ne doit pas jeter les réponses purement
# encyclopédiques, et une nouvelle version du jeu invalide toujours les deux.
_VOCABULAIRE_GUILDE = re.compile(
    r"\bguilde\b|\bmembres?\b|\bblueprints?\b|\bsch[eé]mas?\b|"
    r"\binventaire\b|\br[eé]putation\b|\bqui (?:a|peut|poss[eè]de)\b")


def _epoque() -> dict[str, str] | None:
    """L'empreinte de l'état de la base, par facette.

    `build` couvre tout ce qui vient des fichiers du jeu ; `uex` ne couvre
    que les relevés de prix. Une entrée du cache ne se compare qu'aux
    facettes dont elle dépend — la traduction n'en est pas une : ses
    rafraîchissements sont rares, cosmétiques, et l'analyste écrit ses
    propres phrases.
    """
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
        try:
            build, uex = con.execute(
                "SELECT IFNULL((SELECT build_id FROM ingest_runs "
                "  WHERE status = 'ok' ORDER BY id DESC LIMIT 1), ''), "
                "IFNULL((SELECT MAX(fetched_at) FROM uex_prices), '')"
            ).fetchone()
            epoque = {"build": str(build), "uex": str(uex), "guilde": ""}
        finally:
            con.close()
        if config.GUILD_DB_PATH.exists():
            guilde = sqlite3.connect(
                f"file:{config.GUILD_DB_PATH}?mode=ro", uri=True)
            try:
                compte, evenement, reputation = guilde.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM evenements), "
                    "IFNULL((SELECT MAX(recu_le) FROM evenements), ''), "
                    "IFNULL((SELECT MAX(observe_le) "
                    " FROM reputations_membres), '')"
                ).fetchone()
                epoque["guilde"] = f"{compte}:{evenement}:{reputation}"
            finally:
                guilde.close()
        return epoque
    except sqlite3.Error:
        # Sans base lisible, pas d'époque stable — donc pas de cache.
        return None


def _cache_sync() -> dict[str, str] | None:
    """Charge le fichier une fois, rend l'époque courante de la base.

    Les entrées ne se jettent plus en bloc : chacune porte les facettes
    d'époque dont elle dépend, et se compare à la lecture. « Combien de
    vaisseaux Anvil » survit à un `disco uex` ; « le prix moyen des
    Aegis » y meurt — c'est sa facette `uex` qui a bougé.
    """
    global _CACHE, _CACHE_CHARGE
    import json
    epoque = _epoque()
    if epoque is None:
        return None
    if not _CACHE_CHARGE:
        _CACHE_CHARGE = True
        try:
            brut = json.loads(
                config.CACHE_ANALYSTE.read_text(encoding="utf-8"))
            if brut.get("version") == 3:
                _CACHE = dict(brut.get("reponses") or {})
        except (OSError, ValueError):
            pass
    return epoque


def _cache_lire(cle: str) -> str | None:
    epoque = _cache_sync()
    if epoque is None:
        return None
    entree = _CACHE.get(cle)
    if entree is None:
        return None
    for facette, valeur in (entree.get("facettes") or {}).items():
        if epoque.get(facette) != valeur:
            del _CACHE[cle]
            return None
    return entree.get("texte")


def _cache_ecrire(cle: str, question: str, texte: str) -> None:
    import json
    epoque = _cache_sync()
    if epoque is None:
        return
    if len(_CACHE) >= _CACHE_MAX:
        # L'ordre d'insertion des dict est garanti : le plus ancien sort.
        del _CACHE[next(iter(_CACHE))]
    facettes = {"build": epoque["build"]}
    if _VOCABULAIRE_UEX.search(cle) or _VOCABULAIRE_UEX.search(
            texte.lower()):
        facettes["uex"] = epoque["uex"]
    if _VOCABULAIRE_GUILDE.search(cle) or _VOCABULAIRE_GUILDE.search(
            texte.lower()):
        facettes["guilde"] = epoque.get("guilde", "")
    _CACHE[cle] = {"texte": texte, "question": question,
                   "facettes": facettes}
    try:
        provisoire = config.CACHE_ANALYSTE.with_suffix(".tmp")
        provisoire.write_text(json.dumps(
            {"version": 3, "reponses": _CACHE},
            ensure_ascii=False), encoding="utf-8")
        provisoire.replace(config.CACHE_ANALYSTE)
    except OSError as exc:
        _LOG.warning("cache analyste non écrit : %s", exc)


def _consigner(demandeur: str | None, issue: str, fournisseur: str,
               modele: str, duree_ms: int | None = None) -> None:
    """Consigne la demande dans le carnet — sans jamais faire échouer une
    réponse.

    **Import différé et échec silencieux voulu**, comme `_prix_observe` du
    commerce : la base de guilde peut être absente (un Chris public sans
    compagnon), verrouillée, ou vide. Le §2 interdit au cœur de dépendre
    d'un frontend, et une comptabilité manquante n'est pas une panne — la
    réponse doit partir dans tous les cas.
    """
    try:
        from .guilde import store, usage

        con = store.connect()
        try:
            usage.enregistrer(con, demandeur, issue=issue,
                              fournisseur=fournisseur, modele=modele,
                              duree_ms=duree_ms)
        finally:
            con.close()
    except Exception:  # noqa: BLE001  (base absente, verrouillée, vide)
        pass


def repondre(question: str, *, fournisseur: str | None = None,
             session: str | None = None,
             demandeur: str | None = None) -> str | None:
    """Une réponse d'analyste, ou None — jamais une exception.

    None couvre tout : étage éteint, CLI absent, délai dépassé, refus,
    hors-sujet. L'appelant retombe alors sur « je n'ai pas compris », comme
    si l'étage n'existait pas — le même contrat que les étages LLM du
    routeur : en panne, on passe la main, on ne casse pas la question.
    """
    fournisseur = _fournisseur(fournisseur)
    modele = _modele(fournisseur)
    if not disponible(fournisseur):
        return None
    from .normalize import normalize
    cle_question = normalize(question)
    cle = (f"v{_VERSION_PROMPT}:{fournisseur}:"
           f"{modele.lower()}:{cle_question}")
    en_cache = _cache_lire(cle)
    if en_cache is not None:
        # Servi sans rien dépenser — c'est la colonne qui rassure au
        # panneau, et la confondre avec un appel ferait passer un cache
        # qui travaille pour une dépense.
        _consigner(demandeur, "cache", fournisseur, modele)
        return en_cache
    # **Deux guildeux qui posent la même question en même temps ne paient
    # qu'un appel.** Le second attend le premier puis lit le cache — le
    # scénario d'une soirée où la question fuse dans le salon.
    with _EN_VOL_VERROU:
        attente = _EN_VOL.get(cle)
        if attente is None:
            _EN_VOL[cle] = threading.Event()
    if attente is not None:
        attente.wait(config.ANALYSTE_TIMEOUT)
        _consigner(demandeur, "attente", fournisseur, modele)
        return _cache_lire(cle)
    try:
        debut = time.monotonic()
        reponse = _interroger(question, cle, fournisseur, modele, session)
        # Le seul cas qui consomme du quota. La durée est mesurée ici
        # plutôt que dans `_interroger` : c'est le temps qu'un membre a
        # réellement attendu, CLI, réseau et relance compris.
        _consigner(demandeur, "appel", fournisseur, modele,
                   int((time.monotonic() - debut) * 1000))
        return reponse
    finally:
        with _EN_VOL_VERROU:
            _EN_VOL.pop(cle).set()


def _porte_analyste_texte() -> str:
    """Commande unique permise à Claude, atelier ou exécutable figé."""
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--porte-analyste"])
    return ".venv/Scripts/python.exe scripts/requete.py"


def _porte_mcp() -> tuple[str, list[str], str]:
    """Serveur MCP réel, sans dépendance externe dans le Chris figé."""
    if getattr(sys, "frozen", False):
        return (sys.executable, ["--mcp-analyste"], str(config.DATA_DIR))
    python = config.PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    serveur = config.PROJECT_ROOT / "scripts" / "mcp_analyste.py"
    return str(python), [str(serveur)], str(config.PROJECT_ROOT)


def _consignes(acces: str) -> str:
    return _CONSIGNES.format(
        acces=acces.format(porte=_porte_analyste_texte()),
        passe=_PASSE, schema=_schema(), catalogue=_catalogue())


def _interroger_claude(cli: list[str], question: str,
                       modele: str | None = None) -> subprocess.CompletedProcess:
    consignes = _consignes(_ACCES_CLAUDE)
    return subprocess.run(
        [*cli, "-p", question,
         "--model", modele or config.ANALYSTE,
         "--append-system-prompt", consignes,
         # UN seul motif, celui que les consignes imposent mot pour mot.
         # Plusieurs valeurs cassent le parsing ; des drapeaux répétés
         # s'écrasent — deux pannes silencieuses mesurées le 2026-08-07.
         "--allowedTools", f"Bash({_porte_analyste_texte()}:*)",
         "--max-turns", "12"],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=config.ANALYSTE_TIMEOUT,
        cwd=str(config.PROJECT_ROOT),
        # Réflexion bridée, jamais coupée : ~2 s de gain mesuré, sans
        # rouvrir le piège où l'appel d'outil sort en texte visible.
        env={**os.environ, "MAX_THINKING_TOKENS": "1024"})


def _option_toml(cle: str, valeur) -> list[str]:
    """Une surcharge Codex dont les chemins Windows restent du TOML valide."""
    return ["-c", f"{cle}={json.dumps(valeur, ensure_ascii=False)}"]


def _commande_codex_exec(cli: list[str], question: str,
                         modele: str | None = None, *,
                         session_codex: str | None = None,
                         persistante: bool = False) -> tuple[list[str], str]:
    commande_mcp, arguments_mcp, dossier_mcp = _porte_mcp()
    reprise = session_codex is not None
    commande = [*cli, "exec"]
    if reprise:
        commande.append("resume")
    commande.extend(["--model", modele or config.ANALYSTE])
    # `exec resume` restaure le répertoire et le sandbox du fil initial et
    # n'accepte pas leurs drapeaux. On répète tout de même `sandbox_mode` dans
    # la configuration ci-dessous : la barrière lecture seule reste explicite
    # à chaque processus, même si le format du rollout évolue.
    if not reprise:
        commande.extend([
            "--sandbox", "read-only", "--cd", str(config.PROJECT_ROOT)])
    commande.extend([
        "--skip-git-repo-check", "--ignore-user-config",
        "--ignore-rules", "--json",
    ])
    if not reprise and not persistante:
        commande.append("--ephemeral")
    # Le shell disparaît réellement du catalogue de Sol. Le seul outil qui
    # reste est le MCP ci-dessous, lui-même fermé sur scripts/requete.py.
    for cle, valeur in (
        ("approval_policy", "never"),
        ("sandbox_mode", "read-only"),
        ("model_reasoning_effort", "medium"),
        ("project_doc_max_bytes", 0),
        ("web_search", "disabled"),
        ("features.shell_tool", False),
        ("features.apps", False),
        ("features.plugins", False),
        ("features.skill_mcp_dependency_install", False),
        ("features.multi_agent", False),
        ("features.goals", False),
        ("features.hooks", False),
        ("features.memories", False),
        ("features.remote_plugin", False),
        ("mcp_servers.disco_analyste.command", commande_mcp),
        ("mcp_servers.disco_analyste.args", arguments_mcp),
        ("mcp_servers.disco_analyste.cwd", dossier_mcp),
        ("mcp_servers.disco_analyste.required", True),
        ("mcp_servers.disco_analyste.enabled_tools", ["interroger"]),
        ("mcp_servers.disco_analyste.default_tools_approval_mode", "auto"),
    ):
        commande.extend(_option_toml(cle, valeur))
    # Le schéma complet n'entre qu'au premier tour. Une reprise relit le
    # rollout du CLI et ne reçoit que la nouvelle question : c'est précisément
    # le gain mesuré, sans recopier 8 000 tokens à chaque fois.
    consignes = _consignes(_ACCES_CODEX) + _STYLE_SOL
    if reprise:
        commande.append(session_codex)
    commande.append("-")
    prompt = (question if reprise else
              f"{consignes}\n\nQuestion du joueur :\n{question}")
    return commande, prompt


def _texte_codex(sortie: str) -> str:
    """La dernière réponse naturelle d'un flux JSONL de ``codex exec``."""
    texte = ""
    for ligne in sortie.splitlines():
        try:
            evenement = json.loads(ligne)
        except ValueError:
            continue
        item = evenement.get("item") or {}
        if (evenement.get("type") == "item.completed"
                and item.get("type") == "agent_message"):
            texte = str(item.get("text") or "").strip()
    return texte


def _id_session_codex(sortie: str) -> str | None:
    """L'identifiant annoncé par ``thread.started`` dans le flux JSONL."""
    for ligne in sortie.splitlines():
        try:
            evenement = json.loads(ligne)
        except ValueError:
            continue
        if evenement.get("type") == "thread.started":
            thread_id = str(evenement.get("thread_id") or "").strip()
            return thread_id or None
    return None


def oublier_session(session: str | None) -> None:
    """Coupe les reprises Sol d'une conversation, notamment au nouveau Chris.

    Le processus Codex est déjà terminé après chaque réponse. Oublier signifie
    donc seulement « ne plus reprendre ce rollout » ; aucune tâche de fond ne
    reste allumée et aucun identifiant n'est exposé au frontend.
    """
    if not session:
        return
    with _SESSIONS_CODEX_VERROU:
        for cle in [cle for cle in _SESSIONS_CODEX if cle[0] == session]:
            del _SESSIONS_CODEX[cle]


def _session_codex(session: str | None,
                   modele: str) -> tuple[tuple[str, str], _SessionCodex] | None:
    """Le fil frais et borné de cette conversation, ou aucun fil."""
    if not session or config.ANALYSTE_SESSION_TTL <= 0:
        return None
    cle = (session, modele.lower())
    maintenant = time.monotonic()
    with _SESSIONS_CODEX_VERROU:
        perimees = [
            k for k, fil in _SESSIONS_CODEX.items()
            if (maintenant - fil.au >= config.ANALYSTE_SESSION_TTL
                and not fil.verrou.locked())
        ]
        for ancienne in perimees:
            del _SESSIONS_CODEX[ancienne]
        fil = _SESSIONS_CODEX.get(cle)
        if fil is None:
            if len(_SESSIONS_CODEX) >= _SESSIONS_CODEX_MAX:
                ancienne = min(_SESSIONS_CODEX,
                               key=lambda k: _SESSIONS_CODEX[k].au)
                del _SESSIONS_CODEX[ancienne]
            fil = _SessionCodex()
            _SESSIONS_CODEX[cle] = fil
        return cle, fil


def _lancer_codex(commande: list[str], prompt: str
                   ) -> subprocess.CompletedProcess:
    return subprocess.run(
        commande, input=prompt,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=config.ANALYSTE_TIMEOUT, cwd=str(config.PROJECT_ROOT),
        env={**os.environ, "PYTHONUTF8": "1"})


def _interroger_codex(cli: list[str], question: str,
                      modele: str | None = None,
                      session: str | None = None) -> subprocess.CompletedProcess:
    modele = modele or config.ANALYSTE
    selection = _session_codex(session, modele)
    if selection is None:
        commande, prompt = _commande_codex_exec(cli, question, modele)
        return _lancer_codex(commande, prompt)

    cle, fil = selection
    with fil.verrou:
        maintenant = time.monotonic()
        trop_long = (config.ANALYSTE_SESSION_MAX_TOURS > 0
                     and fil.tours >= config.ANALYSTE_SESSION_MAX_TOURS)
        if (maintenant - fil.au >= config.ANALYSTE_SESSION_TTL or trop_long):
            fil.thread_id, fil.tours = None, 0
        reprise = fil.thread_id
        commande, prompt = _commande_codex_exec(
            cli, question, modele, session_codex=reprise,
            persistante=True)
        resultat = _lancer_codex(commande, prompt)
        if reprise is not None and resultat.returncode != 0:
            # Rollout supprimé, version de CLI incompatible, session devenue
            # illisible : une reprise est une optimisation, jamais un point
            # de panne. On repart une fois d'un fil propre.
            fil.thread_id, fil.tours = None, 0
            commande, prompt = _commande_codex_exec(
                cli, question, modele, persistante=True)
            resultat = _lancer_codex(commande, prompt)
        if resultat.returncode == 0:
            thread_id = _id_session_codex(resultat.stdout or "") or reprise
            with _SESSIONS_CODEX_VERROU:
                # Un nouveau « Chris » peut avoir oublié le fil pendant
                # l'appel : sa réponse reste valable, mais ne le ressuscite pas.
                if _SESSIONS_CODEX.get(cle) is fil and thread_id:
                    fil.thread_id = thread_id
                    fil.au = time.monotonic()
                    fil.tours += 1
        return resultat


def _interroger(question: str, cle: str, fournisseur: str,
                modele: str, session: str | None = None) -> str | None:
    cli = _commande_cli(fournisseur)
    if cli is None:
        return None
    try:
        resultat = (_interroger_codex(cli, question, modele, session)
                    if fournisseur == "codex"
                    else _interroger_claude(cli, question, modele))
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LOG.warning("analyste indisponible : %s", exc)
        return None
    if resultat.returncode != 0:
        _LOG.warning("analyste en échec (%s) : %s", resultat.returncode,
                     (resultat.stderr or "")[:300])
        return None
    texte = (_texte_codex(resultat.stdout or "")
             if fournisseur == "codex"
             else (resultat.stdout or "").strip())
    if not texte or _PASSE in texte[:40]:
        return None
    texte = _sans_etiquette(texte)
    _cache_ecrire(cle, question, texte)
    return texte


#: « logique : », « explication : » en tête de la phrase finale. Le prompt
#: demande de ne pas en poser, mais un prompt n'est qu'une consigne : le
#: modèle en remet. Le retrait est donc mécanique, comme la garantie du §7.
_ETIQUETTE = re.compile(
    r"(^|[\n—-]\s*)(logique|explication|note|raison|pourquoi|analyse)\s*:\s*",
    re.IGNORECASE | re.MULTILINE)


def _sans_etiquette(texte: str) -> str:
    """Retire les étiquettes qui annoncent la phrase d'explication.

    Demande de l'utilisateur (2026-08-10) : « on comprend bien que la
    deuxième phrase correspond à une explication ». L'étiquette fait
    administratif et n'apprend rien.

    **Ce qui la précède est conservé tel quel** — un tiret de liste, un
    tiret cadratin, et surtout les sauts de ligne. Les jeter recollait le
    paragraphe précédent au suivant (« …de Crusader.Yela orbite… ») : c'est
    le défaut de la ponctuation orpheline, déjà payé sur les jetons de
    gabarit, retrouvé ici à l'écriture.
    """
    return _ETIQUETTE.sub(lambda m: m.group(1), texte).strip()
