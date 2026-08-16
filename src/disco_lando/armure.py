"""Ce qu'une armure permet de porter.

Demande du journal (2026-08-06) : « combien je peux porter de chargeur avec une
armure moyenne ? », commentée « question d'ordre général sur les points
d'emport d'armure par taille et type — quel sac à dos, nombre de medpen, nombre
d'armes ».

**La donnée était déjà en base et personne ne l'interrogeait.** `item_ports`
compte 33 762 lignes, ingérées pour les accessoires d'arme ; les armures y sont
depuis le début. C'est le pendant de « une colonne pleine peut être vide de
sens » — ici une table pleine, vide d'usage.

Trois mesures qui font la réponse :

- **Les emplacements ne varient que sur le torse.** Léger 1 arme / 4 chargeurs
  / 2 lancers, moyen 2 / 6 / 3, lourd 2 / 8 / 4. C'est ce qui répond à la
  question posée.
- **Les jambes ne varient pas du tout** : 2 medpens, 2 oxypens, 2 utilitaires
  et une arme de poing, à l'identique sur les 460 paires des trois classes. Un
  joueur suppose spontanément qu'une armure lourde en porte plus — le dire est
  une information, se taire en laisse une fausse.
- **Le sac à dos ajoute ses propres emplacements** — 4 chargeurs, 2 armes et un
  gadget sur les 129 sacs personnels — et c'est le torse qui décide s'il y a un
  sac.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3

from ._socle import NotFound
from .resolver import resolve

# Une famille d'emplacement se lit dans le nom du port, dont le jeu numérote
# les exemplaires : `magazine_attach_1`, `magazine_attach_2`… On agrège sur le
# nom sans son numéro, jamais sur le compte de lignes — `item_ports` répète le
# port autant de fois qu'il accepte de types.
_NUMERO = re.compile(r"_?\d+$")

# L'ordre décide de l'affichage : ce qu'un joueur compte avant de partir.
_FAMILLES: tuple[tuple[str, str, str], ...] = (
    ("wep_stocked", "arme d'épaule", "armes d'épaule"),
    ("wep_sidearm", "arme de poing", "armes de poing"),
    ("magazine_attach", "chargeur de rechange", "chargeurs de rechange"),
    ("magAttach", "chargeur de rechange", "chargeurs de rechange"),
    ("grenade_attach", "lancer de combat", "lancers de combat"),
    ("medPen_attach", "medpen", "medpens"),
    ("oxyPen_attach", "oxypen", "oxypens"),
    ("utility_attach", "objet utilitaire", "objets utilitaires"),
    ("gadget_attach", "gadget", "gadgets"),
    ("backpack", "sac à dos", "sacs à dos"),
)

_LIBELLE = {cle: (sing, plur) for cle, sing, plur in _FAMILLES}
_ORDRE = {cle: i for i, (cle, _, _) in enumerate(_FAMILLES)}

# Les pièces d'une tenue, dans l'ordre où elles se portent. **Le sac à dos n'y
# est pas** : il ne suit pas la classe de l'armure — 129 sacs sur 137 sont
# classés « Personal », un seul porte « Medium », et le compter comme une pièce
# de la tenue moyenne aurait annoncé les emplacements d'un objet unique pour
# ceux de la famille entière. C'est un objet qu'on choisit, et il a sa ligne.
_PIECES: tuple[tuple[str, str], ...] = (
    ("Char_Armor_Helmet", "casque"),
    ("Char_Armor_Torso", "torse"),
    ("Char_Armor_Arms", "bras"),
    ("Char_Armor_Legs", "jambes"),
)

# **La combinaison n'est pas une pièce de plus.** Les 26 combinaisons lourdes
# portent exactement les emplacements du torse *et* des jambes : ce sont des
# tenues d'une pièce, qui les remplacent au lieu de s'y ajouter. Les cumuler
# annonçait 16 chargeurs sur une armure lourde qui en porte 8. Elle reste
# consultable en la nommant.
_COMBINAISON = "Char_Armor_Undersuit"

# Le sac à dos, lui, se compte sur sa propre famille.
_SAC = ("Char_Armor_Backpack", "Personal")

_NOM_DE_PIECE = dict(_PIECES) | {_COMBINAISON: "combinaison",
                                 _SAC[0]: "sac à dos"}

# Comment un joueur nomme une classe d'armure, et ce que le jeu écrit.
_CLASSES: tuple[tuple[str, str, str], ...] = (
    (r"\bleger\w*|\blight\b|\blegere?s?\b", "Light", "légère"),
    (r"\bmoyen\w*|\bmedium\b|\bintermediaires?\b", "Medium", "moyenne"),
    (r"\blourd\w*|\bheavy\b", "Heavy", "lourde"),
)

_CLASSE_FR = {jeu: fr for _, jeu, fr in _CLASSES}


def detect_classe(question: str) -> str | None:
    """« Une armure moyenne » — la classe se lit dans la phrase."""
    from .normalize import normalize

    norm = normalize(question or "")
    for motif, jeu, _ in _CLASSES:
        if re.search(motif, norm):
            return jeu
    return None


def nomme_une_armure(question: str) -> bool:
    """Le garde-fou d'un outil qui n'a pas toujours d'entité à résoudre : sans
    le mot, « quelque chose de moyen » lui reviendrait."""
    from .normalize import normalize

    return bool(re.search(r"\barmures?\b|\btenues?\b|\bcombinaisons?\b|"
                          r"\bequipements?\b|\bsac a dos\b|\bsacs? a dos\b",
                          normalize(question or "")))


@dataclasses.dataclass(frozen=True)
class Emport:
    famille: str
    libelle: str
    nombre: int
    piece: str          # torse, jambes, casque…


@dataclasses.dataclass(frozen=True)
class FicheEmports:
    titre: str
    classe: str | None          # « moyenne », ou None si une armure est nommée
    emports: tuple[Emport, ...]
    pieces: tuple[str, ...]     # les pièces effectivement lues
    invariantes: tuple[str, ...] = ()   # pièces identiques dans les trois classes
    autres_classes: dict | None = None  # famille -> {classe: nombre}
    sac: tuple[Emport, ...] = ()        # ce qu'un sac à dos personnel ajoute


def _emports_de(con: sqlite3.Connection, uuids: list[str]) -> dict[str, int]:
    """Les emplacements d'un ou plusieurs objets, comptés **par port distinct**.

    `item_ports` porte une ligne par type accepté : un `magazine_attach_1` qui
    accepte chargeur et roquette y compte deux lignes pour un seul
    emplacement. Compter les lignes annoncerait douze chargeurs là où il y en
    a six.
    """
    if not uuids:
        return {}
    marques = ",".join("?" * len(uuids))
    lignes = con.execute(
        f"SELECT DISTINCT item_uuid, port_name FROM item_ports "
        f"WHERE item_uuid IN ({marques})", uuids).fetchall()
    par_objet: dict[str, dict[str, int]] = {}
    for r in lignes:
        famille = _NUMERO.sub("", r["port_name"])
        if famille not in _LIBELLE:
            continue
        par_objet.setdefault(r["item_uuid"], {}).setdefault(famille, 0)
        par_objet[r["item_uuid"]][famille] += 1
    if not par_objet:
        return {}
    # Les exemplaires d'une même classe sont identiques — vérifié sur les 194
    # torses légers, les 135 moyens et les 135 lourds. On prend donc le compte
    # **le plus fréquent**, qui n'invente rien et absorbe une pièce aberrante.
    familles: dict[str, list[int]] = {}
    for compte in par_objet.values():
        for famille, n in compte.items():
            familles.setdefault(famille, []).append(n)
    return {f: max(set(v), key=v.count) for f, v in familles.items()}


def _uuids(con: sqlite3.Connection, type_: str, classe: str) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT uuid FROM items WHERE type = ? AND subtype = ?", (type_, classe))]


def emports_d_armure(con: sqlite3.Connection, query: str,
                     classe: str | None = None) -> FicheEmports:
    """« Combien je peux porter de chargeur avec une armure moyenne ? »

    Deux entrées : une **classe** d'armure, et alors on rend la tenue entière,
    ou une armure **nommée**, et on rend ses seuls emplacements.
    """
    if classe:
        emports, pieces, invariantes = [], [], []
        for type_, nom_piece in _PIECES:
            uuids = _uuids(con, type_, classe)
            if not uuids:
                continue
            compte = _emports_de(con, uuids)
            if not compte:
                continue
            pieces.append(nom_piece)
            # Cette pièce varie-t-elle d'une classe à l'autre ? Les jambes ne
            # varient pas, et le supposer serait la fausse évidence.
            ailleurs = [_emports_de(con, _uuids(con, type_, autre))
                        for autre in _CLASSE_FR if autre != classe]
            if ailleurs and all(a == compte for a in ailleurs if a):
                invariantes.append(nom_piece)
            for famille, n in compte.items():
                emports.append(Emport(famille=famille,
                                      libelle=_LIBELLE[famille][0 if n == 1 else 1],
                                      nombre=n, piece=nom_piece))
        if not emports:
            raise NotFound(f"je n'ai pas d'armure {_CLASSE_FR.get(classe, classe)}")

        # De quoi dire ce que les autres classes changent, sans reposer la
        # question : c'est la comparaison qui répond à « et en lourde ? ».
        autres: dict[str, dict[str, int]] = {}
        for type_, _ in _PIECES:
            for autre, autre_fr in _CLASSE_FR.items():
                for famille, n in _emports_de(con, _uuids(con, type_, autre)).items():
                    autres.setdefault(famille, {})[autre_fr] = n

        # Ce qu'un sac à dos ajoute par-dessus, sur les 129 sacs personnels.
        sac = tuple(
            Emport(famille=f, libelle=_LIBELLE[f][0 if n == 1 else 1],
                   nombre=n, piece="sac à dos")
            for f, n in sorted(_emports_de(con, _uuids(con, *_SAC)).items(),
                               key=lambda kv: _ORDRE.get(kv[0], 99))
        )

        emports.sort(key=lambda e: (_ORDRE.get(e.famille, 99), e.piece))
        return FicheEmports(
            sac=sac,
            titre=f"Une armure {_CLASSE_FR.get(classe, classe)}",
            classe=_CLASSE_FR.get(classe, classe),
            emports=tuple(emports), pieces=tuple(pieces),
            invariantes=tuple(invariantes),
            autres_classes={f: v for f, v in autres.items() if len(set(v.values())) > 1},
        )

    # Sans classe, on cherche l'armure nommée. `query` est ici la **question
    # entière** — l'outil n'a pas d'entité déclarée, donc le routeur ne l'a pas
    # découpée en n-grammes.
    candidats = resolve(con, query, entity_types=("item",), limit=12).candidates
    # **Un casque ne porte rien, et c'est une réponse.** « Pembroke » sort le
    # casque en premier ; s'arrêter là aurait rendu « je ne connais pas » sur
    # une armure qu'on connaît très bien. On préfère donc la pièce qui porte
    # quelque chose, et à défaut on rend la fiche vide — au rendu de dire que
    # ce morceau-là ne se charge pas.
    vide = None
    for c in candidats:
        r = con.execute("SELECT uuid, name, type, subtype FROM items WHERE uuid = ?",
                        (c.entity_id,)).fetchone()
        if not r or not (r["type"] or "").startswith("Char_Armor"):
            continue
        piece = _NOM_DE_PIECE.get(r["type"], "armure")
        compte = _emports_de(con, [r["uuid"]])
        fiche = FicheEmports(
            titre=r["name"], classe=_CLASSE_FR.get(r["subtype"] or ""),
            pieces=(piece,),
            emports=tuple(sorted(
                (Emport(famille=f, libelle=_LIBELLE[f][0 if n == 1 else 1],
                        nombre=n, piece=piece) for f, n in compte.items()),
                key=lambda e: _ORDRE.get(e.famille, 99))),
        )
        if fiche.emports:
            return fiche
        vide = vide or fiche

    if vide is not None:
        return vide
    raise NotFound(f"je ne connais pas d'armure « {query} »")
