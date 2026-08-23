"""Le catalogue d'activités — ce qu'on fait le soir, pas ce que la base contient.

Sprint 38. Le projet sait répondre à « quelles missions rapportent le plus » ;
il ne savait pas répondre à « on est trois ce soir, on fait quoi ». La réponse
n'est dans aucun fichier de CIG : c'est de l'**éditorial**, rédigé depuis les
guides communautaires et versionné dans `data/activites/`.

Trois règles tiennent ce module, et chacune répond à un piège du projet :

- **le socle vit dans le dépôt, pas dans la base.** Une réingestion
  reconstruit `disco_lando.db` de zéro ; une fiche rangée là et nulle part
  ailleurs disparaîtrait au prochain patch. Les fichiers sont la source, la
  base n'en est que la projection — d'où une étape dans la chaîne du
  launcher, comme `disco wiki` et `disco trad`.
- **une fiche sans source ne s'ingère pas.** Un guide, wiki ou une vidéo sans
  URL ne peut pas être re-vérifié ; un relevé du jeu ou une observation locale
  dit au contraire qu'il ne possède pas de page publique. La contrainte est
  mécanique (`ValueError`), pas écrite dans un commentaire.
- **aucun chiffre de jeu n'est figé dans une fiche.** Prix, rendements et
  payes se calculent à l'affichage depuis la base. Une fiche qui écrit « le
  Quantainium se vend 27 aUEC » fabrique un mensonge à retardement — c'est
  la leçon du cache d'analyste périmé par l'horloge.

**L'ancrage se fait sur la famille d'abord, le titre en secours.** Mesuré le
2026-08-13 en croisant la liste de contrats du wiki Onyx avec la base : le
jeu écrit « Jorrit Dossier: Power Usage Data » là où le wiki écrit
« Retrieve Power Usage Data », et un `LIKE` sur le titre du wiki ne rend
donc rien alors que le contrat existe. La famille `Hockrow_FacilityDelve`,
elle, rend les **13** contrats d'un coup — dont `P1M3` que le wiki oublie.
"""

from __future__ import annotations

import pathlib
import sqlite3
from typing import Any

import yaml

SCHEMA = """
CREATE TABLE IF NOT EXISTS activites (
    cle                TEXT PRIMARY KEY,
    nom                TEXT NOT NULL,
    nature             TEXT NOT NULL,   -- chaine|activite_libre|boucle|evenement
    statut             TEXT NOT NULL,   -- vivant|a_venir|temporaire|retire
    systeme            TEXT,
    combat             TEXT NOT NULL,   -- aucun|fps|vaisseau|les_deux
    pvp                TEXT NOT NULL,   -- non|possible|au_coeur
    mission            TEXT NOT NULL,   -- oui|non|partiellement
    joueurs_min        INTEGER,
    joueurs_conseilles INTEGER,
    joueurs_max        INTEGER,
    instancie          INTEGER NOT NULL DEFAULT 0,
    duree_min_minutes  INTEGER,
    duree_max_minutes  INTEGER,
    difficulte         INTEGER,         -- 1..5 déclarée, NULL si CIG la publie
    patch_introduction TEXT,
    patch_verifie      TEXT,
    resume             TEXT NOT NULL,
    pourquoi           TEXT,            -- « ce qu'on y gagne comme soirée »
    prerequis          TEXT,
    -- **Le bloc vivant.** Certaines fiches ont une moitié qui se calcule au
    -- lieu de se rédiger : quels minerais rares rapportent le plus, où les
    -- trouver, ce que valent RMC et CMAS. Le nom d'un calcul connu, jamais
    -- son résultat — un prix écrit ici serait faux au prochain relevé UEX.
    calcul             TEXT,            -- minage|salvage|commerce
    -- **La correction manuelle du parcours debutant.** Le rang d'entree se
    -- calcule depuis les contrats ; ce champ le remplace quand le calcul se
    -- trompe. Contested Zones n'exige aucun rang mais se joue en tir libre :
    -- aucune colonne de la base ne le dira jamais.
    ordre_debutant     INTEGER,
    ordre              INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activite_aliases (
    cle   TEXT NOT NULL REFERENCES activites(cle),
    alias TEXT NOT NULL,
    PRIMARY KEY (cle, alias)
);
CREATE TABLE IF NOT EXISTS activite_etapes (
    cle    TEXT NOT NULL REFERENCES activites(cle),
    rang   INTEGER NOT NULL,
    titre  TEXT NOT NULL,
    detail TEXT,
    PRIMARY KEY (cle, rang)
);
CREATE TABLE IF NOT EXISTS activite_materiel (
    cle       TEXT NOT NULL REFERENCES activites(cle),
    rang      INTEGER NOT NULL,
    objet     TEXT NOT NULL,
    pourquoi  TEXT,
    essentiel INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cle, rang)
);
CREATE TABLE IF NOT EXISTS activite_recompenses (
    cle     TEXT NOT NULL REFERENCES activites(cle),
    rang    INTEGER NOT NULL,
    libelle TEXT NOT NULL,
    nature  TEXT,                       -- auec|blueprint|composant|armure|arme|materiau|vaisseau|inconnu
    PRIMARY KEY (cle, rang)
);
CREATE TABLE IF NOT EXISTS activite_avertissements (
    cle   TEXT NOT NULL REFERENCES activites(cle),
    rang  INTEGER NOT NULL,
    texte TEXT NOT NULL,
    PRIMARY KEY (cle, rang)
);
CREATE TABLE IF NOT EXISTS activite_sources (
    cle    TEXT NOT NULL REFERENCES activites(cle),
    rang   INTEGER NOT NULL,
    nom    TEXT NOT NULL,
    auteur TEXT,
    url    TEXT,
    genre  TEXT,                        -- wiki|guide|video|jeu|utilisateur
    PRIMARY KEY (cle, rang)
);
CREATE TABLE IF NOT EXISTS activite_liens (
    cle      TEXT NOT NULL REFERENCES activites(cle),
    vers     TEXT NOT NULL,
    relation TEXT,
    PRIMARY KEY (cle, vers)
);
-- Les ancrages **résolus**. On garde le terme cherché à côté du résultat :
-- sans lui, un lieu renommé par CIG disparaît sans qu'on sache lequel.
CREATE TABLE IF NOT EXISTS activite_lieux (
    cle       TEXT NOT NULL REFERENCES activites(cle),
    cherche   TEXT NOT NULL,
    uuid      TEXT,
    nom       TEXT,
    type_name TEXT,
    PRIMARY KEY (cle, cherche)
);
CREATE TABLE IF NOT EXISTS activite_contrats (
    cle        TEXT NOT NULL REFERENCES activites(cle),
    uuid       TEXT NOT NULL,
    debug_name TEXT,
    title      TEXT,
    via        TEXT,                    -- famille|titre|debug
    -- **Tous les contrats d'un commanditaire ne définissent pas sa
    -- difficulté.** Recco en porte 38 : six missions d'histoire, et
    -- trente-deux offres répétables que le jeu gradue lui-même dans leur
    -- nom (`Battaglia_ScanRocks_VeryEasy` … `_VeryHard`). Les secondes se
    -- **choisissent** ; mesurer la difficulté dessus revient à mélanger
    -- deux populations. Décision de l'utilisateur, 2026-08-14 : « ce qui
    -- compte c'est les histoires de Battaglia ». La fiche déclare alors
    -- `ancrages.difficulte`, et seules ces lignes portent le drapeau.
    pour_difficulte INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (cle, uuid)
);
-- **Les boucles s'ancrent sur le type, et on ne stocke que le type.**
-- « Mercenary » couvre 1 253 contrats : les rattacher un par un ferait
-- une fiche de 1 253 lignes que personne ne lit, et une réponse d'API à
-- l'avenant. Une chaîne se parcourt, une boucle se compte — on garde donc
-- le type et on compte à l'affichage.
CREATE TABLE IF NOT EXISTS activite_types (
    cle          TEXT NOT NULL REFERENCES activites(cle),
    mission_type TEXT NOT NULL,
    PRIMARY KEY (cle, mission_type)
);
CREATE INDEX IF NOT EXISTS idx_activite_aliases ON activite_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_activite_contrats ON activite_contrats(cle);

-- « Quoi de neuf » — les notes d'un patch, rédigées.
--
-- Même nature que les fiches d'activité : de l'éditorial projeté depuis le
-- dépôt, daté, sourcé, et vidé avant réécriture. Elles partagent donc la
-- même commande et la même étape de chaîne.
--
-- **La colonne `preuve` est ce qui distingue ces notes d'un guide.** Un
-- patch à venir n'est vérifiable contre rien : notre base décrit le patch
-- installé. Mais une partie du contenu annoncé est **déjà dans les
-- fichiers** — le matériel arrive avant d'être activé, mesuré sur la BUL-H4
-- et la Vendetta. Ces lignes-là, on peut les nommer exactement ; les
-- autres restent des annonces, et le rendu le dit.
CREATE TABLE IF NOT EXISTS nouveautes (
    patch            TEXT PRIMARY KEY,
    titre            TEXT NOT NULL,
    statut           TEXT NOT NULL,   -- a_venir|sorti
    sortie_annoncee  TEXT,
    resume           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nouveaute_lignes (
    patch       TEXT NOT NULL REFERENCES nouveautes(patch),
    rang        INTEGER NOT NULL,
    categorie   TEXT NOT NULL,
    titre       TEXT NOT NULL,
    detail      TEXT,
    preuve      TEXT NOT NULL,        -- annonce|fichiers
    consequence TEXT,                 -- ce que ça change pour nous
    voir        TEXT,                 -- clé d'activité liée
    PRIMARY KEY (patch, rang)
);
CREATE TABLE IF NOT EXISTS nouveaute_avertissements (
    patch TEXT NOT NULL REFERENCES nouveautes(patch),
    rang  INTEGER NOT NULL,
    texte TEXT NOT NULL,
    PRIMARY KEY (patch, rang)
);
CREATE TABLE IF NOT EXISTS nouveaute_sources (
    patch  TEXT NOT NULL REFERENCES nouveautes(patch),
    rang   INTEGER NOT NULL,
    nom    TEXT NOT NULL,
    auteur TEXT,
    url    TEXT,
    genre  TEXT,
    PRIMARY KEY (patch, rang)
);
"""

#: Vocabulaires fermés. Comme `hardpoint_categories`, on lève sur l'inconnu
#: plutôt que de ranger en « autre » : une valeur mal orthographiée dans une
#: fiche ferait disparaître l'activité d'un filtre, sans rien casser ailleurs.
NATURES = {"chaine", "activite_libre", "boucle", "evenement"}
STATUTS = {"vivant", "a_venir", "temporaire", "retire"}
COMBATS = {"aucun", "fps", "vaisseau", "les_deux"}
PVP = {"non", "possible", "au_coeur"}
MISSION = {"oui", "non", "partiellement"}
#: Les calculs branchables. Fermé comme les autres vocabulaires : un nom
#: inconnu ferait une fiche muette là où on croyait avoir des chiffres.
CALCULS = {"minage", "salvage", "commerce"}

#: Ces sources sont externes et doivent rester rouvrables. Les fichiers du
#: jeu et les observations locales n'ont, eux, pas nécessairement de page.
SOURCES_AVEC_URL = {"wiki", "guide", "video"}

#: Les champs de premier niveau d'une fiche. Fermé pour la même raison que
#: les vocabulaires ci-dessus, mais contre un défaut plus discret : une
#: valeur inconnue lève, une **clé** inconnue ne levait pas. `_ecrire` lit
#: par `fiche.get(...)`, donc un champ mal orthographié s'ingère en NULL et
#: la fiche reste valide à l'œil.
CHAMPS_DE_FICHE = {
    "cle", "nom", "aliases", "nature", "statut", "systeme", "combat", "pvp",
    "mission", "joueurs_min", "joueurs_conseilles", "joueurs_max",
    "instancie", "duree_min_minutes", "duree_max_minutes", "difficulte",
    "patch_introduction", "patch_verifie", "resume", "pourquoi", "prerequis",
    "calcul", "ordre_debutant", "ordre", "ancrages", "lieux", "etapes",
    "materiel", "recompenses", "avertissements", "liens", "sources",
}

_TABLES = ("activite_contrats", "activite_types", "activite_lieux",
           "activite_liens", "activite_sources", "activite_avertissements",
           "activite_recompenses", "activite_materiel", "activite_etapes",
           "activite_aliases", "activites",
           "nouveaute_sources", "nouveaute_avertissements",
           "nouveaute_lignes", "nouveautes")

#: Les deux niveaux de preuve d'une note de patch. Fermé comme les autres :
#: un niveau inconnu ferait passer une annonce pour un fait mesuré.
PREUVES = {"annonce", "fichiers"}
STATUTS_PATCH = {"a_venir", "sorti"}


class FicheInvalide(ValueError):
    """Une fiche que l'on refuse d'ingérer, en disant laquelle et pourquoi."""


def dossier() -> pathlib.Path:
    """Où vivent les fiches — **à côté du code, pas à côté de la base**.

    Première version : `DB_PATH.parent / "activites"`. Faux dès que la base
    n'est pas dans le dépôt — mesuré dans un worktree pointant sur la base
    du checkout principal, où l'ingestion relisait les fiches de l'autre
    copie sans rien dire. Les fiches sont du **source versionné** : elles
    suivent le dépôt, comme `schema.sql`.
    """
    from .. import config

    return config.DATA_DIR / "activites"


def _exiger(fiche: dict[str, Any], champ: str, nom: str) -> Any:
    valeur = fiche.get(champ)
    if valeur in (None, "", [], {}):
        raise FicheInvalide(f"{nom} : le champ « {champ} » est obligatoire")
    return valeur


def _vocabulaire(valeur: str, permis: set[str], champ: str, nom: str) -> str:
    if valeur not in permis:
        raise FicheInvalide(
            f"{nom} : « {valeur} » n'est pas un {champ} connu "
            f"({', '.join(sorted(permis))})")
    return valeur


def _valider_sources(sources: Any, nom: str) -> None:
    """Rend mécanique la différence entre citation et observation."""
    for rang, source in enumerate(sources, start=1):
        if not isinstance(source, dict) or not source.get("nom"):
            raise FicheInvalide(f"{nom} : source {rang} sans nom")
        if source.get("genre") in SOURCES_AVEC_URL and not source.get("url"):
            raise FicheInvalide(
                f"{nom} : la source {rang} ({source['genre']}) exige une URL")


def valider(fiche: dict[str, Any], nom: str) -> dict[str, Any]:
    """Refuse une fiche incomplète plutôt que d'en ingérer une moitié.

    Le contrôle porte sur ce qui rend une fiche **utilisable** : sans clé on
    ne peut pas y rattacher un retour de membre, sans source on ne peut pas
    la créditer ni la re-vérifier, sans `patch_verifie` on ne peut pas dire
    qu'elle a vieilli.
    """
    for champ in ("cle", "nom", "resume", "nature", "statut", "combat",
                  "pvp", "mission", "patch_verifie"):
        _exiger(fiche, champ, nom)
    _vocabulaire(fiche["nature"], NATURES, "nature", nom)
    _vocabulaire(fiche["statut"], STATUTS, "statut", nom)
    _vocabulaire(fiche["combat"], COMBATS, "combat", nom)
    _vocabulaire(fiche["pvp"], PVP, "pvp", nom)
    _vocabulaire(fiche["mission"], MISSION, "mission", nom)
    if fiche.get("calcul"):
        _vocabulaire(fiche["calcul"], CALCULS, "calcul", nom)

    # **Un champ mal orthographié doit crier.** `_ecrire` fait des
    # `fiche.get(...)` : un `orde_debutant` ne lèverait rien, ne s'ingérerait
    # pas, et le parcours du débutant serait faux sans que rien ne le dise.
    # C'est la famille de l'`INSERT OR IGNORE` qui avale une contrainte.
    inconnus = sorted(set(fiche) - CHAMPS_DE_FICHE)
    if inconnus:
        raise FicheInvalide(
            f"{nom} : champ inconnu {', '.join(inconnus)} — "
            f"les champs connus sont ({', '.join(sorted(CHAMPS_DE_FICHE))})")

    if fiche.get("ordre_debutant") is not None:
        if not isinstance(fiche["ordre_debutant"], int):
            raise FicheInvalide(f"{nom} : ordre_debutant doit être un entier")

    sources = _exiger(fiche, "sources", nom)
    _valider_sources(sources, nom)

    # Un nom seul est autorisé quand il est unique. Le système qualifie les
    # homonymes du starmap (`Pyro Gateway`, `Nyx`) sans faire porter un UUID
    # opaque au fichier éditorial.
    for rang, lieu in enumerate(fiche.get("lieux", ()) or (), start=1):
        if isinstance(lieu, str) and lieu.strip():
            continue
        if isinstance(lieu, dict) and lieu.get("nom"):
            inconnus_lieu = set(lieu) - {"nom", "systeme"}
            if not inconnus_lieu:
                continue
        raise FicheInvalide(
            f"{nom} : lieu {rang} invalide — employer un nom ou "
            f"{{nom: ..., systeme: ...}}")

    # **Une durée qui s'inverse est une durée fausse**, et le tri par
    # longueur la classerait sans broncher.
    lo, hi = fiche.get("duree_min_minutes"), fiche.get("duree_max_minutes")
    if lo and hi and lo > hi:
        raise FicheInvalide(f"{nom} : durée minimale supérieure à la maximale")

    # Même contrôle sur les joueurs : « 4 à 2 » passerait tous les filtres.
    bas, haut = fiche.get("joueurs_min"), fiche.get("joueurs_max")
    if bas and haut and bas > haut:
        raise FicheInvalide(f"{nom} : joueurs_min supérieur à joueurs_max")
    return fiche


def _resoudre_contrats(con: sqlite3.Connection,
                       ancrages: dict[str, Any]) -> list[tuple]:
    """Les contrats réels d'une activité, famille d'abord.

    L'ordre n'est pas cosmétique. Le titre publié par un guide est une
    paraphrase dans au moins un cas mesuré ; la famille est le nom que le jeu
    se donne à lui-même.
    """
    trouves: dict[str, tuple] = {}
    for famille in ancrages.get("familles", ()):
        for uuid, debug, titre in con.execute(
                "SELECT uuid, debug_name, title FROM contracts WHERE family=?",
                (famille,)):
            trouves.setdefault(uuid, (uuid, debug, titre, "famille"))
    for prefixe in ancrages.get("debug_names", ()):
        for uuid, debug, titre in con.execute(
                "SELECT uuid, debug_name, title FROM contracts "
                "WHERE debug_name LIKE ?", (f"{prefixe}%",)):
            trouves.setdefault(uuid, (uuid, debug, titre, "debug"))
    for titre_cherche in ancrages.get("titres", ()):
        for uuid, debug, titre in con.execute(
                "SELECT uuid, debug_name, title FROM contracts "
                "WHERE title LIKE ? COLLATE NOCASE", (f"%{titre_cherche}%",)):
            trouves.setdefault(uuid, (uuid, debug, titre, "titre"))
    return list(trouves.values())


def _contrats_de_difficulte(con: sqlite3.Connection,
                            ancrages: dict[str, Any],
                            lignes: list[tuple]) -> set[str]:
    """Les UUID qui définissent la difficulté — tous, sauf déclaration.

    **Un commanditaire n'est pas une chaîne.** Recco porte 38 contrats :
    six missions d'histoire qui s'enchaînent, et trente-deux offres
    répétables que le jeu gradue lui-même — `Battaglia_ScanRocks_VeryEasy`,
    `_Easy`, `_Medium`, `_Hard`, `_VeryHard`, sept versions du même job de
    scan, toutes à risque 1. Les secondes se **choisissent** : c'est la
    définition d'une boucle, et le maximum n'y décrit qu'un cas qu'on peut
    ne pas prendre.

    La convention de graduation est générale — **840 contrats sur 5 105**,
    dans 275 familles — mais elle ne suffit pas à trancher : mesuré,
    `Battaglia_ScanMineRocks_MoleyBoy` et `_Prosp` sont des offres du même
    tableau sans porter de palier dans leur nom. On ne devine donc pas : la
    fiche **déclare** `ancrages.difficulte`, avec la même grammaire que les
    ancrages ordinaires.

    Sans déclaration, tout compte — c'est le cas des chaînes qui n'ont que
    des étapes, et le comportement d'avant.
    """
    demande = ancrages.get("difficulte")
    if not demande:
        return {ligne[0] for ligne in lignes}

    # Les contrats du sous-ensemble, résolus par le même chemin — puis
    # **intersectés** avec ceux de la fiche : un ancrage de difficulté ne
    # doit pas faire entrer un contrat que la fiche ne porte pas.
    connus = {ligne[0] for ligne in lignes}
    retenus = {ligne[0] for ligne in _resoudre_contrats(con, demande)} & connus
    if not retenus:
        raise FicheInvalide(
            "ancrages.difficulte ne retient aucun des contrats de la fiche — "
            "une difficulté mesurée sur rien retomberait en silence sur "
            "l'estimation de la fiche")
    return retenus


def _resoudre_lieux(con: sqlite3.Connection,
                    noms: list[Any]) -> list[tuple]:
    """Les lieux d'une fiche, contre le starmap.

    On garde la ligne même quand elle ne résout pas (`uuid` à NULL) : c'est
    ce que `disco verifier` lira pour signaler un lieu que CIG a renommé.
    Effacer la ligne effacerait le signal.
    """
    lignes = []
    for demande in noms:
        if isinstance(demande, dict):
            cherche = str(demande["nom"]).strip()
            systeme = str(demande.get("systeme") or "").strip() or None
        else:
            cherche = str(demande).strip()
            systeme = None
        etiquette = f"{cherche} ({systeme})" if systeme else cherche

        filtre_systeme = " AND system_name = ? COLLATE NOCASE" if systeme else ""
        parametres: tuple[Any, ...] = ((cherche, systeme) if systeme
                                      else (cherche,))
        candidats = con.execute(
            "SELECT uuid, name, type_name, system_name, path FROM starmap "
            "WHERE name = ? COLLATE NOCASE" + filtre_systeme
            + " ORDER BY uuid", parametres).fetchall()
        if not candidats:
            parametres = ((f"{cherche}%", systeme) if systeme
                          else (f"{cherche}%",))
            candidats = con.execute(
                "SELECT uuid, name, type_name, system_name, path FROM starmap "
                "WHERE name LIKE ? COLLATE NOCASE" + filtre_systeme
                + " ORDER BY LENGTH(name), uuid", parametres).fetchall()
            if candidats:
                longueur = len(candidats[0][1])
                candidats = [c for c in candidats if len(c[1]) == longueur]

        # Deux UUID peuvent décrire le même objet amont (Moraine Base est
        # dupliquée à signature identique dans le build 4.9). Ce doublon est
        # sans conséquence ; deux signatures distinctes, elles, exigent une
        # qualification et ne sont jamais tranchées par l'ordre SQLite.
        signatures = {(c[1].lower(), c[2], (c[3] or "").lower(), c[4] or "")
                      for c in candidats}
        if len(signatures) > 1:
            precisions = ", ".join(sorted(
                f"{c[4] or c[1]} — {c[3] or 'système inconnu'} — {c[2]}"
                for c in candidats))
            raise FicheInvalide(
                f"lieu ambigu « {cherche} » : {precisions} ; "
                f"préciser le système dans la fiche")
        if not candidats:
            lignes.append((etiquette, None, None, None))
        else:
            ligne = candidats[0]
            lignes.append((etiquette, ligne[0], ligne[1], ligne[2]))
    return lignes


def dossier_nouveautes() -> pathlib.Path:
    from .. import config

    return config.DATA_DIR / "nouveautes"


def valider_nouveaute(note: dict[str, Any], nom: str) -> dict[str, Any]:
    """Une note de patch qui ne dit pas son niveau de preuve n'en est pas une.

    Un patch à venir n'est vérifiable contre rien : la base décrit le patch
    **installé**. Sans `preuve` sur chaque ligne, une annonce de guide se
    lirait comme un fait mesuré — c'est précisément la confusion que ce
    module doit rendre impossible.
    """
    for champ in ("patch", "titre", "statut", "resume"):
        _exiger(note, champ, nom)
    _vocabulaire(note["statut"], STATUTS_PATCH, "statut de patch", nom)
    sources = _exiger(note, "sources", nom)
    _valider_sources(sources, nom)
    for ligne in note.get("lignes", ()):
        if not ligne.get("titre") or not ligne.get("categorie"):
            raise FicheInvalide(f"{nom} : une ligne sans titre ou sans catégorie")
        _vocabulaire(ligne.get("preuve", ""), PREUVES, "niveau de preuve", nom)
    return note


def _ecrire_nouveaute(con: sqlite3.Connection, note: dict[str, Any]) -> None:
    patch = note["patch"]
    con.execute(
        "INSERT INTO nouveautes (patch, titre, statut, sortie_annoncee, resume) "
        "VALUES (?,?,?,?,?)",
        (patch, note["titre"], note["statut"], note.get("sortie_annoncee"),
         note["resume"]))
    con.executemany(
        "INSERT INTO nouveaute_lignes "
        "(patch, rang, categorie, titre, detail, preuve, consequence, voir) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(patch, rang, l["categorie"], l["titre"], l.get("detail"),
          l["preuve"], l.get("consequence"), l.get("voir"))
         for rang, l in enumerate(note.get("lignes", ()), start=1)])
    con.executemany(
        "INSERT INTO nouveaute_avertissements (patch, rang, texte) VALUES (?,?,?)",
        [(patch, rang, t)
         for rang, t in enumerate(note.get("avertissements", ()), start=1)])
    con.executemany(
        "INSERT INTO nouveaute_sources (patch, rang, nom, auteur, url, genre) "
        "VALUES (?,?,?,?,?,?)",
        [(patch, rang, s["nom"], s.get("auteur"), s.get("url"), s.get("genre"))
         for rang, s in enumerate(note["sources"], start=1)])


def _lire(chemin: pathlib.Path) -> dict[str, Any]:
    with chemin.open(encoding="utf-8") as flux:
        fiche = yaml.safe_load(flux)
    if not isinstance(fiche, dict):
        raise FicheInvalide(f"{chemin.name} : le fichier n'est pas une fiche")
    return valider(fiche, chemin.name)


def _lire_nouveaute(chemin: pathlib.Path) -> dict[str, Any]:
    """Lit une note avec le même refus explicite qu'une fiche."""
    with chemin.open(encoding="utf-8") as flux:
        note = yaml.safe_load(flux)
    if not isinstance(note, dict):
        raise FicheInvalide(f"{chemin.name} : le fichier n'est pas une note")
    return valider_nouveaute(note, chemin.name)


def _ecrire(con: sqlite3.Connection, fiche: dict[str, Any]) -> None:
    cle = fiche["cle"]
    con.execute(
        "INSERT INTO activites (cle, nom, nature, statut, systeme, combat, "
        " pvp, mission, joueurs_min, joueurs_conseilles, joueurs_max, "
        " instancie, duree_min_minutes, duree_max_minutes, difficulte, "
        " patch_introduction, patch_verifie, resume, pourquoi, prerequis, "
        " calcul, ordre_debutant, ordre) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cle, fiche["nom"], fiche["nature"], fiche["statut"],
         fiche.get("systeme"), fiche["combat"], fiche["pvp"], fiche["mission"],
         fiche.get("joueurs_min"), fiche.get("joueurs_conseilles"),
         fiche.get("joueurs_max"), 1 if fiche.get("instancie") else 0,
         fiche.get("duree_min_minutes"), fiche.get("duree_max_minutes"),
         fiche.get("difficulte"), fiche.get("patch_introduction"),
         fiche["patch_verifie"], fiche["resume"], fiche.get("pourquoi"),
         fiche.get("prerequis"), fiche.get("calcul"),
         fiche.get("ordre_debutant"), fiche.get("ordre", 0)))

    con.executemany("INSERT OR IGNORE INTO activite_aliases (cle, alias) VALUES (?,?)",
                    [(cle, a) for a in fiche.get("aliases", ())])
    con.executemany(
        "INSERT INTO activite_etapes (cle, rang, titre, detail) VALUES (?,?,?,?)",
        [(cle, rang, e["titre"] if isinstance(e, dict) else e,
          e.get("detail") if isinstance(e, dict) else None)
         for rang, e in enumerate(fiche.get("etapes", ()), start=1)])
    con.executemany(
        "INSERT INTO activite_materiel (cle, rang, objet, pourquoi, essentiel) "
        "VALUES (?,?,?,?,?)",
        [(cle, rang, m["objet"] if isinstance(m, dict) else m,
          m.get("pourquoi") if isinstance(m, dict) else None,
          1 if isinstance(m, dict) and m.get("essentiel") else 0)
         for rang, m in enumerate(fiche.get("materiel", ()), start=1)])
    con.executemany(
        "INSERT INTO activite_recompenses (cle, rang, libelle, nature) "
        "VALUES (?,?,?,?)",
        [(cle, rang, r["libelle"] if isinstance(r, dict) else r,
          r.get("nature") if isinstance(r, dict) else None)
         for rang, r in enumerate(fiche.get("recompenses", ()), start=1)])
    con.executemany(
        "INSERT INTO activite_avertissements (cle, rang, texte) VALUES (?,?,?)",
        [(cle, rang, t) for rang, t in
         enumerate(fiche.get("avertissements", ()), start=1)])
    con.executemany(
        "INSERT INTO activite_sources (cle, rang, nom, auteur, url, genre) "
        "VALUES (?,?,?,?,?,?)",
        [(cle, rang, s["nom"], s.get("auteur"), s.get("url"), s.get("genre"))
         for rang, s in enumerate(fiche["sources"], start=1)])
    con.executemany(
        "INSERT OR IGNORE INTO activite_liens (cle, vers, relation) VALUES (?,?,?)",
        [(cle, lien["vers"], lien.get("relation"))
         for lien in fiche.get("liens", ())])


def _aligner_schema(con: sqlite3.Connection) -> None:
    """Rebâtit les tables quand le schéma a changé — il n'y a rien à migrer.

    `CREATE TABLE IF NOT EXISTS` ne rattrape pas une colonne ajoutée : la
    table existante reste telle quelle et l'`INSERT` casse à la première
    ingestion. Ailleurs dans le projet on écrirait une migration ; ici on
    **rebâtit**, et c'est la conséquence directe de la règle qui fonde ce
    module — le socle est une **projection** des fichiers du dépôt, pas un
    registre. Aucune donnée d'utilisateur ne vit dans ces tables : les
    retours des membres sont dans `guilde.db`, précisément pour ça.
    """
    attendu = {
        "activites": {
            "cle", "nom", "nature", "statut", "systeme", "combat", "pvp",
            "mission", "joueurs_min", "joueurs_conseilles", "joueurs_max",
            "instancie", "duree_min_minutes", "duree_max_minutes",
            "difficulte", "patch_introduction", "patch_verifie", "resume",
            "pourquoi", "prerequis", "calcul", "ordre_debutant", "ordre"},
        "activite_contrats": {"cle", "uuid", "debug_name", "title", "via",
                              "pour_difficulte"},
        "activite_types": {"cle", "mission_type"},
        "nouveautes": {"patch", "titre", "statut",
                       "sortie_annoncee", "resume"},
    }
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, colonnes in attendu.items():
        if table not in tables:
            continue
        presentes = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if presentes == colonnes:
            continue
        # Une seule table qui diverge suffit à tout rebâtir : les autres la
        # référencent, et une reconstruction partielle laisserait des lignes
        # orphelines que personne ne verrait.
        for cible in _TABLES:
            con.execute(f"DROP TABLE IF EXISTS {cible}")
        return


def _executer_schema(con: sqlite3.Connection) -> None:
    """Exécute le schéma sans le commit implicite d'`executescript`.

    `sqlite3.Connection.executescript` valide d'abord la transaction en
    cours. Pour une projection, ce détail transforme une reconstruction de
    schéma en suppression irréversible si une écriture suivante lève. On
    découpe donc les instructions avec le parseur SQLite et on les garde
    dans le même point de sauvegarde que les données.
    """
    bloc: list[str] = []
    for caractere in SCHEMA:
        bloc.append(caractere)
        if caractere != ";":
            continue
        instruction = "".join(bloc)
        if not sqlite3.complete_statement(instruction):
            continue
        con.execute(instruction)
        bloc.clear()
    if "".join(bloc).strip():
        raise RuntimeError("instruction SQL incomplète dans le schéma activités")


def sync(con: sqlite3.Connection, *, echo=print) -> int:
    """(Ré)ingère toutes les fiches. Idempotent : on vide puis on réécrit.

    Vider est volontaire — une fiche retirée du dépôt doit disparaître de la
    base, sinon le catalogue garderait des activités que plus personne ne
    relit. Le socle est projeté, pas accumulé.
    """
    racine = dossier()
    if not racine.is_dir():
        echo(f"Aucun dossier {racine} — rien à ingérer.")
        return 0

    fichiers = sorted(racine.glob("*.yaml")) + sorted(racine.glob("*.yml"))
    if not fichiers:
        echo(f"Aucune fiche dans {racine}.")
        return 0

    # Toute entrée éditoriale est lue **avant** la première mutation. Le cas
    # mesuré était un schéma ancien suivi d'un YAML invalide : la projection
    # précédente était supprimée puis la validation levait, laissant zéro
    # activité alors que les anciennes données étaient encore valides.
    fiches = [_lire(chemin) for chemin in fichiers]
    notes: list[dict[str, Any]] = []
    racine_notes = dossier_nouveautes()
    if racine_notes.is_dir():
        notes = [_lire_nouveaute(chemin) for chemin in
                 sorted(racine_notes.glob("*.yaml"))]

    vus: set[str] = set()
    for f in fiches:
        if f["cle"] in vus:
            raise FicheInvalide(f"la clé « {f['cle']} » est utilisée deux fois")
        vus.add(f["cle"])

    # **Un alias ne peut désigner qu'une fiche.** Mesuré le 2026-08-14 :
    # « ghilly » était resté dans les alias de `mercenaire` après que la
    # série Ghilly ait eu sa propre fiche. `resoudre_activite` tranche en
    # faveur du nom, donc l'alias de `mercenaire` ne gagnait jamais — une
    # ligne de données morte que rien ne signalait. Le même genre de silence
    # que l'`INSERT OR IGNORE` qui avale une violation de CHECK.
    proprietaire: dict[str, str] = {}
    for f in fiches:
        noms = {(f["nom"] or "").lower(), f["cle"]}
        for alias in list(f.get("aliases", ())) :
            alias = (alias or "").strip().lower()
            if not alias:
                continue
            if alias in proprietaire and proprietaire[alias] != f["cle"]:
                raise FicheInvalide(
                    f"l'alias « {alias} » est revendiqué par "
                    f"{proprietaire[alias]} et par {f['cle']}")
            proprietaire[alias] = f["cle"]
        for autre in fiches:
            if autre["cle"] == f["cle"]:
                continue
            collision = noms & {
                (a or "").strip().lower() for a in autre.get("aliases", ())}
            if collision:
                raise FicheInvalide(
                    f"{autre['cle']} déclare en alias le nom de {f['cle']} : "
                    f"{', '.join(sorted(collision))}")

    # Le schéma et sa projection forment une seule opération atomique. Un
    # SAVEPOINT fonctionne aussi si l'appelant possède déjà une transaction,
    # contrairement à `BEGIN`, et restaure jusqu'aux DROP TABLE de l'alignement.
    con.execute("SAVEPOINT projection_activites")
    try:
        _aligner_schema(con)
        _executer_schema(con)
        for table in _TABLES:
            con.execute(f"DELETE FROM {table}")
        for fiche in fiches:
            _ecrire(con, fiche)

        contrats = lieux = types = 0
        for fiche in fiches:
            cle = fiche["cle"]
            # Les types déclarés sont vérifiés **contre la base**, pas crus
            # sur parole : « Bounty Hunting » au lieu de « Bounty Hunter »
            # ne rattacherait rien et ne se verrait nulle part.
            declares = list((fiche.get("ancrages", {}) or {}).get("types", ()))
            for mission_type in declares:
                connu = con.execute(
                    "SELECT COUNT(*) FROM contracts WHERE mission_type=?",
                    (mission_type,)).fetchone()[0]
                if not connu:
                    echo(f"  {cle} : type de mission inconnu — {mission_type}")
                    continue
                con.execute(
                    "INSERT OR IGNORE INTO activite_types (cle, mission_type) "
                    "VALUES (?,?)", (cle, mission_type))
                types += 1

            ancrages = fiche.get("ancrages", {}) or {}
            lignes = _resoudre_contrats(con, ancrages)
            # **Le sous-ensemble qui définit la difficulté**, quand la fiche
            # le déclare. Sans déclaration, tout compte — c'est le cas des
            # chaînes qui n'ont que des étapes.
            pour_diff = _contrats_de_difficulte(con, ancrages, lignes)
            con.executemany(
                "INSERT OR IGNORE INTO activite_contrats "
                "(cle, uuid, debug_name, title, via, pour_difficulte) "
                "VALUES (?,?,?,?,?,?)",
                [(cle, *ligne, 1 if ligne[0] in pour_diff else 0)
                 for ligne in lignes])
            contrats += len(lignes)

            resolus = _resoudre_lieux(con, list(fiche.get("lieux", ()) or ()))
            con.executemany(
                "INSERT OR IGNORE INTO activite_lieux "
                "(cle, cherche, uuid, nom, type_name) VALUES (?,?,?,?,?)",
                [(cle, *ligne) for ligne in resolus])
            lieux += sum(1 for ligne in resolus if ligne[1])
            manquants = [ligne[0] for ligne in resolus if not ligne[1]]
            if manquants:
                echo(f"  {cle} : lieu introuvable dans le starmap — "
                     f"{', '.join(manquants)}")

        # Les notes de patch — même projection, même transaction.
        for note in notes:
            _ecrire_nouveaute(con, note)
    except Exception:
        con.execute("ROLLBACK TO projection_activites")
        con.execute("RELEASE projection_activites")
        raise
    else:
        con.execute("RELEASE projection_activites")

    echo(f"{len(fiches)} activités ingérées, {contrats} contrats rattachés, "
         f"{types} types de mission, {lieux} lieux résolus.")
    if notes:
        echo(f"{len(notes)} note(s) de patch : "
             + ", ".join(f"{n['patch']} ({len(n.get('lignes', ()))} lignes)"
                         for n in notes))
    return len(fiches)
