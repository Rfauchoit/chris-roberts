"""Journal de **toutes** les questions, réponses comprises.

À ne pas confondre avec `unanswered.py`, qui ne retient que les échecs. Celui-ci
garde tout, y compris — et surtout — les réponses qui ont marché : une réponse
exacte peut être inutilisable, trop longue, mal tournée pour l'oreille, ou
répondre à côté de l'intention réelle. C'est invisible dans un journal d'échecs.

Le fichier est écrit pour être **relu et annoté par un humain**, puis renvoyé
tel quel. D'où le Markdown plutôt que du JSON : on ouvre, on écrit sous la
question, on enregistre. Un JSONL demanderait d'échapper les guillemets et de
ne pas casser la ligne, ce qui décourage l'annotation — et une annotation qu'on
ne fait pas ne vaut rien.

Chaque échange se termine donc par un emplacement vide :

    **Commentaire :**

C'est là que ça s'écrit. Le reste — outil retenu, confiance, étage du routeur,
latence — est sur une seule ligne dense, utile au diagnostic sans gêner la
lecture.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import pathlib
import re
import threading

from . import config

_LOCK = threading.Lock()

# Le format a vécu avant d'être stabilisé. Les 90 premiers commentaires ont
# été écrits ``**Commentaire : texte`` (sans les deux étoiles fermantes), puis
# le format actuel est devenu ``**Commentaire :** texte``. Un grand motif qui
# exigeait la variante actuelle sautait silencieusement ces 90 échanges : le
# journal en affichait 138 alors que l'archive en contenait 228. On découpe
# donc d'abord sur l'en-tête — seule frontière historique restée stable — puis
# on lit les champs à l'intérieur avec des marqueurs tolérants.
_ENTETE_BLOC = re.compile(
    r"^## (?P<at>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*·\s*"
    r"(?P<source>[^\r\n]+?)\s*$", re.M)
_QUESTION = re.compile(r"^\*\*Q\.\*\*[ \t]*", re.M)
_REPONSE = re.compile(r"^\*\*R\.\*\*[ \t]*", re.M)
_COMMENTAIRE = re.compile(
    r"^\*\*Commentaire\s*:\s*(?:\*\*)?[ \t]*", re.M)
_DETAIL = re.compile(r"^`(?P<detail>[^`\r\n]*)`[^\r\n]*$", re.M)


def path() -> pathlib.Path:
    return config.JOURNAL_LOG


def record(question: str, answer: str, *, source: str = "cli",
           tool: str | None = None, entity: str | None = None,
           confidence: float | None = None, via: str | None = None,
           elapsed_ms: float | None = None, answered: bool = True,
           session: str | None = None) -> None:
    """Consigne un échange. N'échoue jamais.

    Un journal qui fait tomber le service qu'il observe ne sert à rien : toute
    erreur d'écriture est avalée. Et tout le bloc part en une seule écriture,
    pour que deux frontends qui écrivent en même temps ne s'entrelacent pas.

    `DISCO_JOURNAL_OFF` coupe l'écriture. Les tests sont déjà protégés par un
    fixture, mais un script de mise au point lancé à la main passe à côté :
    77 entrées de débogage se sont retrouvées mêlées aux 79 annotées à la main,
    et il a fallu les trier après coup. Un journal annoté est une donnée
    d'utilisateur, pas un fichier de travail.
    """
    if os.environ.get("DISCO_JOURNAL_OFF"):
        return
    # **Et une garde côté client, parce que celle du dessus s'oublie.**
    # `DISCO_JOURNAL_OFF` doit être posée **avant** le démarrage du cœur ;
    # un cœur lancé pour vérifier une version, puis interrogé en charge,
    # passe à côté. Mesuré deux fois dans la même journée (2026-08-21) :
    # 386 questions de vérification, puis 91 de plus quelques heures après —
    # `data/journal.md` de 16 Ko à 387 Ko, et le corpus du banc faussé au
    # point qu'un test est tombé sur une question que le vérificateur venait
    # lui-même d'écrire.
    #
    # La règle avait été écrite noir sur blanc dans le skill entre les deux.
    # Une consigne en prose ne suffit pas : le client se déclare, et le
    # journal l'écoute — aucun démarrage à prévoir.
    if (source or "").startswith("essai"):
        return
    quand = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    details = []
    if tool:
        details.append(f"{tool}({entity})" if entity else tool)
    if via:
        details.append(via)
    if confidence is not None:
        details.append(f"confiance {confidence:.2f}")
    if elapsed_ms is not None:
        details.append(f"{elapsed_ms:.0f} ms")
    if session:
        # Un fil stable rend les conversations rejouables, sans inscrire un
        # identifiant Discord, un jeton d'appareil ou un pseudo dans le
        # journal. Douze caractères hexadécimaux suffisent à séparer 500 sessions
        # locales ; ce n'est ni une authentification ni une donnée à inverser.
        empreinte = hashlib.blake2s(
            session.encode("utf-8"), digest_size=6,
            person=b"discofil").hexdigest()
        details.append(f"fil {empreinte}")
    if not answered:
        details.append("SANS RÉPONSE")

    bloc = (
        f"\n## {quand} · {source}\n\n"
        f"**Q.** {question.strip() or '(vide)'}\n\n"
        f"**R.** {answer.strip() or '(rien)'}\n\n"
        + (f"`{' · '.join(details)}`\n\n" if details else "")
        + "**Commentaire :**\n\n---\n"
    )
    try:
        with _LOCK:
            fichier = path()
            fichier.parent.mkdir(parents=True, exist_ok=True)
            nouveau = not fichier.exists()
            with fichier.open("a", encoding="utf-8") as sortie:
                if nouveau:
                    sortie.write(_ENTETE)
                sortie.write(bloc)
    except OSError:
        pass


_ENTETE = """# Journal des questions

Écris tes remarques sous « **Commentaire :** », enregistre, et renvoie-moi ce
fichier. Tout est utile : une réponse juste mais trop longue, une réponse à
côté de l'intention, un nom mal prononcé, une info qui manquait.

---
"""


def _read_file(fichier: pathlib.Path) -> list[dict]:
    """Relit un fichier de journal, y compris ses deux formats historiques."""
    if not fichier.exists():
        return []
    texte = fichier.read_text(encoding="utf-8")
    entetes = list(_ENTETE_BLOC.finditer(texte))
    echanges: list[dict] = []
    for rang, entete in enumerate(entetes):
        fin = entetes[rang + 1].start() if rang + 1 < len(entetes) else len(texte)
        bloc = texte[entete.end():fin]
        question = _QUESTION.search(bloc)
        reponse = _REPONSE.search(bloc)
        commentaire = _COMMENTAIRE.search(bloc)
        # Repli conservateur : un bloc abîmé n'est jamais inventé. L'archive
        # garde le texte brut ; le lecteur omet seulement ce qu'il ne sait pas
        # attribuer sans ambiguïté.
        if not question or not reponse or not commentaire:
            continue
        if not question.end() <= reponse.start() <= commentaire.start():
            continue

        zone_reponse = bloc[reponse.end():commentaire.start()]
        detail = _DETAIL.search(zone_reponse)
        answer = zone_reponse[:detail.start() if detail else len(zone_reponse)]
        comment = bloc[commentaire.end():]
        # Le séparateur Markdown termine le bloc, il ne fait partie ni de la
        # remarque humaine ni de la réponse.
        comment = re.sub(r"\n\s*---\s*\Z", "", comment)
        entree = {
            "at": entete.group("at").replace("T", " "),
            "source": entete.group("source").strip(),
            "question": bloc[question.end():reponse.start()].strip(),
            "answer": answer.strip(),
            "detail": detail.group("detail").strip() if detail else None,
            "comment": comment.strip(),
        }
        echanges.append(entree)
    return echanges


def read(fichier: pathlib.Path | None = None) -> list[dict]:
    """Relit le journal courant, commentaires compris.

    Le chemin optionnel sert aux audits qui doivent lire une archive sans
    déplacer la configuration globale du service.
    """
    return _read_file(fichier or path())


def read_all(archive: pathlib.Path | None = None,
             courant: pathlib.Path | None = None) -> list[dict]:
    """L'archive puis le journal courant, sans bloc strictement dupliqué.

    Une même question posée deux fois reste deux échanges. Seule une copie
    identique jusque dans l'horodatage, la réponse et le commentaire est
    retirée — c'est le doublon de fichier, pas la répétition d'un joueur.
    """
    courant = courant or path()
    archive = archive or courant.with_name("journal-analyse.md")
    resultat: list[dict] = []
    vus: set[tuple] = set()
    for entree in [*read(archive), *read(courant)]:
        cle = tuple(entree.get(champ) for champ in (
            "at", "source", "question", "answer", "detail", "comment"))
        if cle in vus:
            continue
        vus.add(cle)
        resultat.append(entree)
    return resultat


_FIL = re.compile(r"(?:^|\s·\s)fil (?P<fil>[0-9a-f]{12})(?:\s·\s|$)")


def fil_de_detail(detail: str | None) -> str | None:
    """L'empreinte de conversation ajoutée aux nouveaux échanges."""
    trouve = _FIL.search(detail or "")
    return trouve.group("fil") if trouve else None


def commented() -> list[dict]:
    """Les seuls échanges qui m'intéressent à la relecture."""
    return [e for e in read() if e["comment"]]
