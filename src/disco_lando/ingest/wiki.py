"""Star Citizen Wiki — le français, et ce que les fichiers du jeu ne portent pas.

Source **secondaire autorisée** par le §4 du brief : « API REST […] la plupart
des endpoints sont publics sans authentification. Utile en complément ou en
secours ». Elle n'avait jamais été branchée. sc-craft.tools, lui, répond 403 à
un accès automatisé et le §4 l'interdit — on ne le contourne pas ; ce module
est la voie légitime vers la même matière.

Ce qu'il apporte, mesuré le 2026-08-04 sur l'API réelle :

* **Des descriptions en vrai français.** `game_description` est traduit pour
  **278 vaisseaux sur 295** et **74 % des objets** (893 sur 1 200 échantillonnés).
  C'est le plus gros apport, et de loin : `decrire` sert aujourd'hui du texte
  anglais à un assistant francophone. Attention, `description` — sans le
  préfixe `game_` — n'est **pas** traduit : son `fr_FR` recopie l'anglais mot
  pour mot. Seul `game_description` porte du français.
* `msrp` et `pledge_url` — le prix en **argent réel** (229 vaisseaux sur 295).
  `get_price` savait dire « il s'obtient en argent réel » sans jamais donner le
  montant.
* `loaner` — le vaisseau prêté en attendant qu'un concept sorte (82 vaisseaux).
* `production_status` — volable ou concept. **Beaucoup moins utile qu'espéré** :
  sur les 295 vaisseaux joints, 237 sont `flight-ready`, 57 sans valeur, et
  **un seul** est `in-concept` (le Javelin). C'est logique après coup — les
  fichiers du jeu ne contiennent que ce qui vole. On l'ingère quand même,
  parce que le rapport s'inversera au prochain lot de concepts, mais aucun
  rendu n'en dépend aujourd'hui.
* `signature` et `cross_section` — signatures EM et IR, section radar.
  Ingérées, pas encore exploitées : aucun outil ne parle de furtivité.
* `affiliation` des systèmes — **UEE, Vanduul, Unclaimed, Xi'an, Banu**. Pyro et
  Nyx sont `Unclaimed` : c'est le signal de non-droit, mesuré, qui remplace le
  `SYSTEMES_RISQUES = {"pyro"}` codé en dur dans `queries.py`.

**La synchronisation est vérifiée, pas supposée** — c'est la condition posée par
l'utilisateur. Le wiki publie sa version de jeu par défaut
(`/game-versions` → `4.9.0-LIVE.12232306`) et nous connaissons la nôtre
(`ingest_runs.build_id` → `12232306`, dernier segment du même code). Si les deux
divergent, on **refuse d'ingérer** au lieu de mélanger deux patchs : un statut
de production de la 4.10 posé à côté d'une vitesse de la 4.9 serait
indétectable dans la réponse. C'est exactement le cas que
`docs/INGESTION.md` appelle « le pire, parce qu'il est invisible ».

Comme pour UEX, ces données vivent dans leurs propres tables, horodatées, et le
rendu dit d'où elles viennent : « lu dans les fichiers du jeu » et « traduit par
le wiki » n'ont pas la même valeur de vérité.
"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.star-citizen.wiki/api/v2"

# Même leçon qu'avec UEX : un agent par défaut se fait refuser. On se nomme.
ENTETES = {
    "Accept": "application/json",
    "User-Agent": "chris-roberts/0.3 (+assistant Star Citizen, usage privé)",
}

SCHEMA = """
-- Complément du wiki. Séparé des tables du jeu, comme les prix UEX : ces
-- champs viennent d'une source communautaire et le rendu doit le dire.
CREATE TABLE IF NOT EXISTS wiki_vehicles (
  uuid              TEXT PRIMARY KEY,
  fetched_at        TEXT NOT NULL,
  game_version      TEXT,
  name              TEXT,
  description_fr    TEXT,     -- traduite ; NULL si le wiki n'a que l'anglais
  production_status TEXT,     -- flight-ready | in-concept | in-production…
  production_note   TEXT,
  msrp              REAL,     -- argent réel, en dollars
  pledge_url        TEXT,
  loaner            TEXT,     -- JSON : vaisseaux prêtés en attendant
  foci              TEXT,     -- JSON : rôles déclarés
  signature_em      REAL,     -- boucliers levés, le cas courant
  signature_ir      REAL,
  section_longueur  REAL,
  section_largeur   REAL,
  section_hauteur   REAL,
  web_url           TEXT
);

CREATE INDEX IF NOT EXISTS ix_wiki_v_statut ON wiki_vehicles (production_status);

CREATE TABLE IF NOT EXISTS wiki_items (
  uuid            TEXT PRIMARY KEY,
  fetched_at      TEXT NOT NULL,
  name            TEXT,
  description_fr  TEXT,
  classification  TEXT,
  is_craftable    INTEGER,
  is_lootable     INTEGER
);

-- Un système et son autorité. `affiliation` vaut Unclaimed pour Pyro et Nyx :
-- c'est la donnée qui dit « sans loi », au lieu de la coder en dur.
CREATE TABLE IF NOT EXISTS wiki_systems (
  code         TEXT PRIMARY KEY,
  fetched_at   TEXT NOT NULL,
  name         TEXT,
  status       TEXT,
  affiliation  TEXT,
  description  TEXT           -- anglais : le wiki n'a pas de français ici
);
"""


def _appel(chemin: str, **parametres) -> dict:
    url = f"{BASE}/{chemin}"
    if parametres:
        url += "?" + urllib.parse.urlencode(parametres)
    requete = urllib.request.Request(url, headers=ENTETES)
    with urllib.request.urlopen(requete, timeout=30) as reponse:  # noqa: S310
        return json.loads(reponse.read().decode("utf-8"))


def available() -> bool:
    """Le wiki répond-il ? Comme UEX : l'absence de réseau n'empêche rien."""
    return version_du_wiki() is not None


def version_du_wiki() -> str | None:
    """La version de jeu que le wiki sert par défaut, ex. `4.9.0-LIVE.12232306`."""
    try:
        corps = _appel("game-versions", **{"page[size]": 50})
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    versions = corps.get("data") or []
    defaut = next((v for v in versions if v.get("is_default")), None)
    code = (defaut or (versions[0] if versions else {}))
    return code.get("code") if isinstance(code, dict) else None


def build(code: str | None) -> str | None:
    """Le numéro de build d'un code de version.

    `4.9.0-LIVE.12232306` → `12232306`. C'est lui qui identifie la livraison —
    CIG livre du contenu entre les patchs majeurs, cf. docs/INGESTION.md — donc
    c'est sur lui qu'on compare, pas sur la chaîne entière.
    """
    if not code:
        return None
    dernier = code.rsplit(".", 1)[-1]
    return dernier if dernier.isdigit() else None


def build_local(con: sqlite3.Connection) -> str | None:
    ligne = con.execute(
        "SELECT build_id FROM ingest_runs WHERE status = 'ok' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    return ligne[0] if ligne else None


def _fr(champ) -> str | None:
    """Le français d'un champ localisé, **seulement s'il en est vraiment un**.

    Le wiki remplit `fr_FR` avec l'anglais quand la traduction manque. Rendre
    ce texte serait pire que rien : on croirait avoir traduit. On ne garde donc
    que ce qui diffère de `en_EN`.
    """
    if not isinstance(champ, dict):
        return None
    fr = (champ.get("fr_FR") or "").strip()
    en = (champ.get("en_EN") or "").strip()
    return fr if fr and fr != en else None


def _texte(champ) -> str | None:
    """N'importe quel texte lisible d'un champ localisé, français d'abord."""
    if isinstance(champ, str):
        return champ.strip() or None
    if not isinstance(champ, dict):
        return None
    for cle in ("fr_FR", "en_EN"):
        valeur = (champ.get(cle) or "").strip()
        if valeur:
            return valeur
    return None


# « Fabricant : Aegis Dynamics\nType : Chasseur Léger\n\n » ouvre 271 des 278
# descriptions traduites. L'en-tête du rendu dit déjà les deux, et le lecteur
# n'a pas besoin de l'entendre une seconde fois trois mots plus loin.
_PREAMBULE = re.compile(r"^\s*Fabricant\s*:.*?\n\s*\n", re.DOTALL)


def _sans_preambule(texte: str | None) -> str | None:
    if not texte:
        return None
    return _PREAMBULE.sub("", texte).strip() or None


def _nombre(valeur) -> float | None:
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    return None


def _pages(chemin: str, echo, taille: int = 200):
    """Itère sur toutes les pages d'un endpoint, en s'arrêtant proprement.

    Une page illisible interrompt le parcours au lieu de lever : une ingestion
    partielle se voit dans le compte affiché, une exception ferait échouer tout
    l'enchaînement du launcher pour un incident réseau.
    """
    page = 1
    while True:
        try:
            corps = _appel(chemin, **{"page[size]": taille, "page[number]": page})
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            echo(f"[yellow]{chemin} page {page} illisible "
                 f"({type(exc).__name__})[/] — on s'arrête là.")
            return
        lignes = corps.get("data") or []
        if not lignes:
            return
        yield from lignes
        derniere = (corps.get("meta") or {}).get("last_page")
        if derniere and page >= derniere:
            return
        page += 1


def _vaisseaux(con, horodatage, version, echo) -> int:
    n = 0
    for v in _pages("vehicles", echo):
        uuid = v.get("uuid")
        if not uuid:
            continue
        signature = v.get("signature") or {}
        section = v.get("cross_section") or {}
        con.execute(
            "INSERT OR REPLACE INTO wiki_vehicles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, horodatage, v.get("version") or version, v.get("name"),
             # `description` n'est pas traduite — son fr_FR recopie l'anglais.
             # Seule `game_description` porte du français. Mesuré.
             _sans_preambule(_fr(v.get("game_description"))),
             _texte(v.get("production_status")), _texte(v.get("production_note")),
             _nombre(v.get("msrp")), v.get("pledge_url"),
             json.dumps(v["loaner"], ensure_ascii=False) if v.get("loaner") else None,
             json.dumps([_texte(f) for f in v["foci"]], ensure_ascii=False)
             if v.get("foci") else None,
             _nombre(signature.get("em_shields")), _nombre(signature.get("ir_shields")),
             _nombre(section.get("length")), _nombre(section.get("width")),
             _nombre(section.get("height")), v.get("web_url")))
        n += 1
    return n


def _objets(con, horodatage, echo) -> int:
    n = 0
    for o in _pages("items", echo):
        uuid = o.get("uuid")
        if not uuid:
            continue
        con.execute(
            "INSERT OR REPLACE INTO wiki_items VALUES (?,?,?,?,?,?,?)",
            (uuid, horodatage, _texte(o.get("name")), _fr(o.get("description")),
             o.get("classification_label") or o.get("type_label"),
             1 if o.get("is_craftable") else 0,
             1 if o.get("is_lootable") else 0))
        n += 1
    return n


def _systemes(con, horodatage, echo) -> int:
    n = 0
    for s in _pages("starsystems", echo, taille=100):
        code = s.get("code")
        if not code:
            continue
        affiliations = [a.get("name") for a in (s.get("affiliation") or [])
                        if isinstance(a, dict) and a.get("name")]
        con.execute(
            "INSERT OR REPLACE INTO wiki_systems VALUES (?,?,?,?,?,?)",
            (code, horodatage, s.get("name"), s.get("status"),
             ", ".join(affiliations) or None, _texte(s.get("description"))))
        n += 1
    return n


def sync(con: sqlite3.Connection, *, echo=print, force: bool = False) -> int:
    """Récupère le complément du wiki. Rend le nombre total de lignes écrites.

    Refuse si les builds divergent, sauf `force` : mélanger deux patchs produit
    des réponses cohérentes en apparence et fausses en substance.
    """
    distante = version_du_wiki()
    if distante is None:
        echo("[red]wiki injoignable[/] — rien n'est modifié.")
        return 0

    ici, la_bas = build_local(con), build(distante)
    echo(f"  wiki   {distante}")
    echo(f"  ingéré build {ici or '(aucun)'}")
    if ici and la_bas and ici != la_bas:
        if not force:
            echo("[yellow]builds différents — ingestion du wiki refusée.[/] "
                 "Réingère les fichiers du jeu d'abord (`disco source clone` "
                 "puis `disco ingest`), ou force en sachant que les champs "
                 "viendront de deux patchs distincts.")
            return 0
        echo("[yellow]builds différents, forcé[/] — les champs du wiki "
             "décrivent un autre patch que les tables du jeu.")

    con.executescript(SCHEMA)
    horodatage = con.execute("SELECT datetime('now')").fetchone()[0]
    for table in ("wiki_vehicles", "wiki_items", "wiki_systems"):
        con.execute(f"DELETE FROM {table}")

    vaisseaux = _vaisseaux(con, horodatage, distante, echo)
    objets = _objets(con, horodatage, echo)
    systemes = _systemes(con, horodatage, echo)
    con.commit()

    traduits = con.execute(
        "SELECT (SELECT COUNT(*) FROM wiki_vehicles WHERE description_fr IS NOT NULL)"
        " + (SELECT COUNT(*) FROM wiki_items WHERE description_fr IS NOT NULL)"
    ).fetchone()[0]
    echo(f"wiki  {vaisseaux} vaisseaux, {objets} objets, {systemes} systèmes "
         f"— {traduits} descriptions en français")
    return vaisseaux + objets + systemes
