"""Chemins et réglages. Tout est surchargeable par variable d'environnement :
le §2 du brief impose que les frontends pointent vers un cœur local ou distant
sans changer de code, et l'ingestion suit la même règle.
"""

from __future__ import annotations

import os
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

DATA_DIR = pathlib.Path(os.environ.get("DISCO_DATA_DIR", PROJECT_ROOT / "data"))
SOURCE_DIR = pathlib.Path(
    os.environ.get("DISCO_SOURCE_DIR", DATA_DIR / "raw" / "scunpacked-data")
)
DB_PATH = pathlib.Path(os.environ.get("DISCO_DB_PATH", DATA_DIR / "disco_lando.db"))
GUILD_DB_PATH = pathlib.Path(os.environ.get(
    "DISCO_GUILD_DB_PATH", DATA_DIR / "guilde.db"
))
GUILD_CLIENT_CONFIG = pathlib.Path(os.environ.get(
    "DISCO_GUILD_CLIENT_CONFIG", DATA_DIR / "guilde_client.json"
))
GUILD_SPOOL_PATH = pathlib.Path(os.environ.get(
    "DISCO_GUILD_SPOOL_PATH", DATA_DIR / "guilde_spool.db"
))
GUILD_SERVER_URL = os.environ.get(
    "DISCO_GUILD_SERVER_URL", os.environ.get(
        "DISCO_CORE_URL", "http://127.0.0.1:8000"))
GUILD_WATCH_INTERVAL = float(os.environ.get(
    "DISCO_GUILD_WATCH_INTERVAL", "2"))
GUILD_ALIASES = pathlib.Path(
    os.environ.get("DISCO_GUILD_ALIASES", DATA_DIR / "aliases_guild.yaml")
)
# Journal des questions sans réponse, alimenté pendant les tests en conditions
# réelles. Voir unanswered.py.
UNANSWERED_LOG = pathlib.Path(
    os.environ.get("DISCO_UNANSWERED_LOG", DATA_DIR / "unanswered.jsonl")
)

SOURCE_REPO = "https://github.com/StarCitizenWiki/scunpacked-data.git"

# ------------------------------------------------------------ routeur (§7)

# `deterministic` seul, ou une cascade : « deterministic,local ». L'étage 2
# (cloud) a été retiré le 2026-08-07 — décision de l'utilisateur, pas de clé.
# L'étage 1 reste toujours en tête — c'est le chemin rapide, et le §3 rappelle
# pourquoi : pendant une session de jeu, la VRAM est prise par Star Citizen.
ROUTER = os.environ.get("DISCO_ROUTER", "deterministic")

# Seuil au-dessous duquel un étage passe la main au suivant plutôt que
# d'imposer sa réponse. La cascade prenait le premier résultat non nul, quelle
# que soit sa qualité.
#
# Mesuré sur la machine : les routages corrects de l'étage 1 sortent entre 0,86
# et 1,00, et le seul routage erroné relevé était à 0,70. Un cas correct
# tombait à 0,69 — il partira donc au LLM sans nécessité, ce qui coûte une
# seconde et rien d'autre. L'inverse, imposer un routage douteux, coûte une
# réponse fausse.
#
# Sans étage LLM configuré, ce seuil ne change rien : faute de suivant, le
# doute est renvoyé tel quel.
ROUTER_MIN_CONFIDENCE = float(os.environ.get("DISCO_ROUTER_MIN_CONFIDENCE", "0.80"))
# **Deux bandes sous le seuil, et elles ne se traitent pas pareil.** Demande
# de l'utilisateur (2026-08-10) : « il faut que tu sois capable de déterminer
# si le déterministe est incapable de répondre pour passer sur Claude ».
#
# Entre les deux, l'outil a compris la question mais pas parfaitement : on
# répond tout de suite et on pose la réserve (« demande à Claude »). En
# dessous, l'entité elle-même est douteuse — « le meilleur loadout pour mon
# vaisseau chromé dont j'ai oublié le nom » sortait la fiche du *C.O. Nomad*,
# résolu sur le mot « nom ». Répondre là serait un mensonge assuré ; on passe
# la main, et on retombe sur la réponse déterministe si l'analyste décline.
ROUTER_INCAPABLE_CONFIDENCE = float(
    os.environ.get("DISCO_ROUTER_INCAPABLE_CONFIDENCE", "0.65"))

# Étage 3 — LLM local. `llama-server` sur 8080 par défaut, servi par
# scripts/serve_llama.ps1 ; Ollama écouterait sur 11434 et fonctionne aussi,
# les deux exposant /v1/chat/completions.
LOCAL_LLM_URL = os.environ.get("DISCO_LOCAL_LLM_URL", "http://127.0.0.1:8080/v1")
# llama-server ne sert qu'un modèle à la fois et ignore ce champ ; il reste
# utile pour Ollama, qui s'en sert pour choisir.
LOCAL_MODEL = os.environ.get("DISCO_LOCAL_MODEL", "local")
# Le §3 fixe le budget total à moins de 5 s entre la fin de la question et le
# début de la réponse. L'étage 3 n'est atteint que si l'étage 1 a renoncé, et
# la transcription en a déjà consommé deux : il ne reste pas grand-chose.
LOCAL_LLM_TIMEOUT = float(os.environ.get("DISCO_LOCAL_LLM_TIMEOUT", "8"))

# Contexte servi par llama-server, en tokens. **Ce n'est pas un réglage du
# cœur : c'est une copie de ce que `scripts/serve_llama.ps1` lance**, et elle
# existe pour que `disco verifier` puisse dire « le catalogue ne rentre plus »
# sans avoir à interroger un serveur qui n'est peut-être pas allumé.
# Les deux doivent bouger ensemble ; le contrôle crie si l'on oublie.
# 12288 depuis le 2026-08-07 : le catalogue a atteint 54 outils, soit
# ~7 200 tokens estimés, et la garde des 85 % de 8192 a sonné — c'est
# exactement le rôle qu'on lui avait donné. Le surcoût VRAM d'un 4B reste
# de l'ordre de 150 Mo.
LOCAL_LLM_CONTEXT = int(os.environ.get("DISCO_LOCAL_LLM_CONTEXT", "12288"))
# Plafond de génération. Un appel d'outil tient en une centaine de tokens ;
# au-delà, le modèle n'appelle rien et divague. Voir router/llm.py.
LOCAL_MAX_TOKENS = int(os.environ.get("DISCO_LOCAL_MAX_TOKENS", "256"))

# L'analyste — Codex ou Claude en headless sur l'abonnement du compte connecté,
# derrière l'échec du routeur déterministe. Vide = éteint (le défaut : les
# tests et le balayage ne consomment jamais le quota). Le fournisseur se déduit
# de `gpt-*` pour rester simple, et peut être forcé pour un modèle au nom neuf.
# Voir analyste.py : Sol n'a ni shell ni web, seulement le MCP lecture seule.
ANALYSTE = os.environ.get("DISCO_ANALYSTE", "")
ANALYSTE_FOURNISSEUR = os.environ.get("DISCO_ANALYSTE_FOURNISSEUR", "")
# Modèles de repli quand le joueur force un fournisseur différent de celui
# actif. Le lanceur sert Sol par défaut, mais « demande à Claude » doit savoir
# quel modèle lancer sans transformer le réglage global pour les questions
# suivantes. Ces deux valeurs ne valent pas activation : `DISCO_ANALYSTE`
# reste l'interrupteur qui interdit aux tests de consommer un abonnement.
ANALYSTE_CODEX = os.environ.get("DISCO_ANALYSTE_CODEX", "gpt-5.6-sol")
ANALYSTE_CLAUDE = os.environ.get("DISCO_ANALYSTE_CLAUDE", "sonnet")
ANALYSTE_TIMEOUT = float(os.environ.get("DISCO_ANALYSTE_TIMEOUT", "300"))
# Une reprise Codex évite de renvoyer le schéma et le catalogue à chaque
# question d'une même conversation. Mesuré le 2026-08-09 : 9,3 s en reprise
# contre 15,6 s en fil neuf sur le même compte SQL. Ce n'est pas un processus
# gardé en mémoire : le fil est simplement réutilisable pendant cette durée
# d'inactivité, puis abandonné.
ANALYSTE_SESSION_TTL = float(os.environ.get(
    "DISCO_ANALYSTE_SESSION_TTL", "900"))
ANALYSTE_SESSION_MAX_TOURS = int(os.environ.get(
    "DISCO_ANALYSTE_SESSION_MAX_TOURS", "12"))
# Le cache des réponses d'analyste — un fichier, pas une durée : une
# réponse reste valable tant que la base n'a pas changé. Voir analyste.py.
CACHE_ANALYSTE = pathlib.Path(
    os.environ.get("DISCO_CACHE_ANALYSTE", DATA_DIR / "cache_analyste.json"))

# ------------------------------------------------------------ frontends (§2)

# Les frontends pointent vers un cœur local ou distant sans changer de code.
CORE_URL = os.environ.get("DISCO_CORE_URL", "http://127.0.0.1:8000")

MODELS_DIR = pathlib.Path(os.environ.get("DISCO_MODELS_DIR", PROJECT_ROOT / "models"))

# --- frontend local (Phase 4)
# whisper.cpp compilé maison, backend Vulkan (§3). Pas de faster-whisper :
# CTranslate2 est CUDA-only et la machine a une RX 9070 XT.
WHISPER_BINARY = pathlib.Path(os.environ.get(
    "DISCO_WHISPER_BINARY", MODELS_DIR / "whisper.cpp" / "whisper-cli.exe"))
WHISPER_MODEL = pathlib.Path(os.environ.get(
    "DISCO_WHISPER_MODEL", MODELS_DIR / "ggml-large-v3-turbo-q5_0.bin"))
WHISPER_THREADS = int(os.environ.get("DISCO_WHISPER_THREADS", "8"))

# Piper s'installe comme paquet Python depuis que le projet est passé sous
# OHF-Voice/piper1-gpl : plus de binaire autonome. `PIPER_VOICE` est un *nom*
# de voix, pas un chemin — le module la cherche dans `PIPER_DATA_DIR`.
# upmc plutôt que siwis : retenue à l'écoute comparative des quatre voix
# françaises disponibles. Deux locuteurs, d'où DISCO_PIPER_SPEAKER.
PIPER_VOICE = os.environ.get("DISCO_PIPER_VOICE", "fr_FR-upmc-medium")
PIPER_DATA_DIR = pathlib.Path(os.environ.get(
    "DISCO_PIPER_DATA_DIR", MODELS_DIR / "piper"))
# Interpréteur portant le module `piper`. Vide = celui du frontend.
PIPER_PYTHON = os.environ.get("DISCO_PIPER_PYTHON", "")

# Locuteur, pour les voix multi-locuteurs (upmc en a 2, mls 125). None = le
# premier, ce qui est le seul choix possible sur une voix mono-locuteur.
_speaker = os.environ.get("DISCO_PIPER_SPEAKER", "")
PIPER_SPEAKER = int(_speaker) if _speaker.strip().isdigit() else None

# Prosodie. Les défauts de Piper sonnent plat sur une phrase d'information :
# 1.0 de durée et 0.8 de variation donnent une scansion très régulière.
# On ralentit un peu — la diction gagne sur les noms propres — et on augmente
# la variation de durée des phonèmes, qui est ce qui casse le débit mécanique.
PIPER_LENGTH_SCALE = float(os.environ.get("DISCO_PIPER_LENGTH_SCALE", "1.08"))
PIPER_NOISE_W = float(os.environ.get("DISCO_PIPER_NOISE_W", "0.95"))
PIPER_SENTENCE_SILENCE = float(os.environ.get("DISCO_PIPER_SENTENCE_SILENCE", "0.35"))

# Budget de lecture à voix haute, en caractères. Débit mesuré sur upmc avec les
# réglages ci-dessus : **10,2 caractères/seconde**, donc 220 ≈ 21 s.
#
# C'est encore long, et c'est un compromis assumé : sur « quel canon balistique
# sur un Gladius », la réponse *est* l'énumération, et elle occupe la phrase la
# plus longue. Descendre le budget la supprimerait et ne laisserait que
# « Aegis Gladius accepte des armes en taille S3 » — grammaticalement propre et
# parfaitement inutile. Mieux vaut trop long que hors sujet.
#
# Le vrai remède est de raccourcir les énumérations dans render.py, ce que les
# retours du journal diront. Le texte intégral reste affiché et journalisé ;
# seul le parlé est raccourci. 0 désactive.
SPEECH_BUDGET = int(os.environ.get("DISCO_SPEECH_BUDGET", "220"))

# Ré-orthographe des noms propres anglais. Voir frontends/pronunciation.py.
PIPER_RESPELL = os.environ.get("DISCO_PIPER_RESPELL", "1") not in ("0", "false", "")

# Touche libre, hors des raccourcis de Star Citizen.
PUSH_TO_TALK = os.environ.get("DISCO_PUSH_TO_TALK", "f9")


def _input_device(brut: str):
    """Micro de capture : index numérique, fragment de nom, ou défaut Windows.

    Vide = on laisse Windows choisir. Une machine de joueur expose facilement
    dix entrées — casque, micro de bureau, plus les périphériques virtuels de
    Steam et d'OBS — et le défaut système n'est pas forcément celui qu'on porte
    en jeu. `sounddevice` accepte les deux formes ; on ne convertit en entier
    que si c'en est un, sinon « Logitech » serait cassé en route.
    """
    brut = brut.strip()
    if not brut:
        return None
    return int(brut) if brut.lstrip("-").isdigit() else brut


INPUT_DEVICE = _input_device(os.environ.get("DISCO_INPUT_DEVICE", ""))

# Son gardé avant l'appui sur la touche. Sans lui, chaque question perd son
# premier mot : le flux met 100 à 300 ms à s'ouvrir et on parle avant d'avoir
# fini d'enfoncer la touche. 0 désactive — le micro ne s'ouvre alors qu'à
# l'appui. Voir docs/PHASE4_LOCAL.md.
PREROLL_MS = int(os.environ.get("DISCO_PREROLL_MS", "400"))

# Journal de toutes les questions, réponses comprises — distinct de
# UNANSWERED_LOG, qui ne garde que les échecs. En Markdown parce qu'il est fait
# pour être annoté à la main. Voir journal.py.
JOURNAL_LOG = pathlib.Path(os.environ.get(
    "DISCO_JOURNAL_LOG", DATA_DIR / "journal.md"))

# --- bot Discord (Phase 5)
DISCORD_TOKEN_ENV = "DISCORD_TOKEN"


def _salon(brut: str):
    """Salon texte où le bot écrit ses réponses : identifiant ou nom.

    Vide = le salon d'où la commande a été lancée. Un identifiant est plus sûr
    qu'un nom — les noms de salon changent, et deux catégories peuvent avoir un
    « #général ». Clic droit sur le salon, « Copier l'identifiant », mode
    développeur activé dans Discord.
    """
    brut = brut.strip().lstrip("#")
    if not brut:
        return None
    return int(brut) if brut.isdigit() else brut


DISCORD_CHANNEL = _salon(os.environ.get("DISCO_DISCORD_CHANNEL", ""))

# Webhook de publication des échanges depuis le frontend local. L'URL contient
# un jeton : c'est un secret, jamais dans le dépôt. Salon Discord → Modifier →
# Intégrations → Webhooks → Nouveau webhook → Copier l'URL.
DISCORD_WEBHOOK = os.environ.get("DISCO_DISCORD_WEBHOOK", "")

# Mots qui déclenchent une réponse quand un message du salon commence par eux.
# Vide = pas de déclencheur, seules les commandes répondent.
#
# Les variantes sont essayées de la plus longue à la plus courte, et l'ordre
# n'est pas décoratif : « jésus chris » doit passer avant « jésus » et avant
# « chris », sinon la question commencerait par le reste du déclencheur.
#
# Les titres — créateur, seigneur, roi, jésus — sont la plaisanterie maison de
# la communauté SC autour du fondateur du jeu. Ils restent surchargeables.
DISCORD_TRIGGERS = tuple(
    mot.strip().lower()
    for mot in os.environ.get(
        "DISCO_DISCORD_TRIGGERS",
        "jésus chris,chris roberts,chrisroberts,créateur,seigneur,jésus,chris,roberts,roi",
    ).split(",")
    if mot.strip()
)

# Nombre de mots minimum après le déclencheur. Le §8 appelle ça la
# « confirmation implicite » : ne répondre que si une question suit vraiment.
# Un titre lâché seul dans une conversation ne doit rien déclencher : « chris »
# ou « seigneur » sortent naturellement dans une discussion de guilde.
DISCORD_MIN_WORDS = int(os.environ.get("DISCO_DISCORD_MIN_WORDS", "2"))
WAKEWORD_MODEL = pathlib.Path(os.environ.get(
    "DISCO_WAKEWORD_MODEL", MODELS_DIR / "wakeword" / "chris_roberts.onnx"))
WAKEWORD_THRESHOLD = float(os.environ.get("DISCO_WAKEWORD_THRESHOLD", "0.6"))
# Le §8 : « Le cooldown n'est pas optionnel » — dans une guilde SC, quelqu'un
# dira « Chris Roberts » en parlant d'une news CIG.
WAKEWORD_COOLDOWN = float(os.environ.get("DISCO_WAKEWORD_COOLDOWN", "8"))
# §9 : buffer circulaire de quelques secondes en RAM, rien sur disque.
DISCORD_BUFFER_SECONDS = float(os.environ.get("DISCO_BUFFER_SECONDS", "3"))
DISCORD_MAX_UTTERANCE = float(os.environ.get("DISCO_MAX_UTTERANCE", "8"))
OPTOUT_FILE = pathlib.Path(os.environ.get(
    "DISCO_OPTOUT_FILE", DATA_DIR / "optout.json"))

# --- Phase 6, UEX (prix et inventaires — absents des fichiers du jeu depuis
# la 3.20, cf. docs/SOURCES_EXTERNES.md)
UEX_API_URL = os.environ.get("DISCO_UEX_URL", "https://api.uexcorp.space/2.0")
UEX_TOKEN_ENV = "UEX_API_TOKEN"

# Seuil rapidfuzz au-delà duquel un candidat est retenu. Mesuré : « gladiousse »
# tombe à 82 contre « Gladius », d'où un seuil bas compensé par le phonétique
# et par le tri sur le score combiné (cf. resolver.py).
FUZZY_FLOOR = 72

# En dessous de cet écart entre le meilleur et le deuxième candidat, on
# considère la résolution ambiguë et on remonte les deux.
AMBIGUITY_MARGIN = 6

# --- Qui a le droit de poser une question au cœur
#
# **Le jour où le cœur sort de la machine, `/ask` doit se fermer.** Il est
# gratuit en local et coûteux dès qu'il passe à l'analyste : ouvert sur
# Internet, n'importe qui connaissant l'URL consomme l'abonnement de l'hôte.
# Les autres routes ne rendent que de la donnée de jeu en lecture.
#
# La règle : **la boucle locale est de confiance, le reste présente un
# jeton** — le même jeton d'appareil que `/api/guilde/*`, ou le jeton de
# service ci-dessous. Un tunnel (cloudflared) relaie depuis 127.0.0.1 : c'est
# pourquoi la présence d'un en-tête de relais (`X-Forwarded-For`,
# `CF-Connecting-IP`) suffit à faire d'une requête locale une requête
# extérieure. Sans ce détail, le tunnel rendrait tout le monde « local ».
API_TOKEN = os.environ.get("DISCO_API_TOKEN", "")
# Pour qui veut assumer un `/ask` ouvert malgré tout — ce n'est pas le défaut.
API_OUVERTE = os.environ.get("DISCO_API_OUVERTE", "") not in ("", "0", "false")
