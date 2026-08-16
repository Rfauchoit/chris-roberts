"""Reconnaître et recalculer les armes soudées à un porteur.

`flightReady` et `weaponMountUsable` disent qu'une arme est montée, pas qu'un
joueur peut la monter ailleurs. Le Reign-3 du Tumbril Storm et les canons du
Bengal satisfont ces deux drapeaux. La convention de nommage apporte la
distinction : porteur nommé dans un tag, emplacement réservé, préfixe `$`,
classe de capital-ship ou nom affiché du Vanduul Mauler.

Le calcul vit hors du chargeur pour rester rejouable sur une base existante :
une migration qui ajoute seulement la colonne vide rendrait le filtre neutre
jusqu'à la prochaine réingestion.
"""

from __future__ import annotations

import re
import sqlite3

MARQUEURS_DE_PORTEUR = ("nose", "turretgun")
CLASSES_DE_PORTEUR = ("bengal", "javelin", "idris", "kingship", "vanduul")
SUFFIXES_DE_PORTEUR = ("_turret",)
_DRAPEAUX = ("flightready", "weaponmountusable")

# Le jeu écrit `AEGS_Vanguard_Base` et `VanguardNose`. Sans découpage des
# majuscules, la seconde forme reste un seul mot et masque EVSD/GVSR.
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def _mots_du_tag(brut: str) -> set[str]:
    nu = brut.strip("$ ")
    mots = {nu.lower()}
    mots |= {m.lower() for m in re.split(r"[^A-Za-z0-9]+", nu) if m}
    mots |= {m.lower() for m in _CAMEL.findall(nu)}
    return mots


def jetons_de_vaisseaux(con: sqlite3.Connection) -> set[str]:
    """Mesure les noms de porteurs et écarte fabricants et mots communs."""
    communs = {"mk", "ii", "iii", "star", "fighter", "edition", "special",
               "the", "and", "class", "series", "base", "gun", "type"}
    for code, nom in con.execute("SELECT code, name FROM manufacturers"):
        for champ in (code, nom):
            for mot in re.split(r"[^A-Za-z0-9]+", champ or ""):
                if len(mot) >= 3:
                    communs.add(mot.lower())

    jetons: set[str] = set()
    for (nom,) in con.execute(
            "SELECT DISTINCT name FROM ships WHERE name IS NOT NULL"):
        for mot in re.split(r"[^A-Za-z0-9]+", nom):
            bas = mot.lower()
            if len(bas) >= 4 and bas not in communs and not bas.isdigit():
                jetons.add(bas)
    return jetons


def porteur_exclusif(tags: str | None, jetons: set[str],
                     class_name: str | None = None,
                     type_objet: str | None = None,
                     nom: str | None = None) -> str | None:
    """Rend le signe explicite qui réserve l'arme, sinon ``None``."""
    # `$Color_01` montre que `$` n'est pas une exclusivité universelle. La
    # propriété ne se pose qu'aux armes de vaisseau.
    if type_objet != "WeaponGun":
        return None

    bas_classe = (class_name or "").lower()
    bas_nom = (nom or "").lower()
    for marque in CLASSES_DE_PORTEUR:
        if marque in bas_classe or marque in bas_nom:
            return marque.capitalize()
    for suffixe in SUFFIXES_DE_PORTEUR:
        if bas_classe.endswith(suffixe) or f"{suffixe}_" in bas_classe:
            return "tourelle de capital-ship"

    for brut in (tags or "").split():
        if brut.lower() in _DRAPEAUX:
            continue
        # Les véhicules terrestres Tumbril sont absents de `ships` : leur
        # nom ne peut pas entrer dans les jetons mesurés, mais `$TMBL_Nova`
        # reste une déclaration directe de la source.
        if brut.startswith("$"):
            return brut.strip("$ ")
        mots = _mots_du_tag(brut)
        if mots & jetons or mots & set(MARQUEURS_DE_PORTEUR):
            return brut.strip("$ ")
    return None


def _lignes(con: sqlite3.Connection) -> list[tuple]:
    return con.execute(
        "SELECT uuid, tags, class_name, type, name, porteur_exclusif "
        "FROM items WHERE type = 'WeaponGun'").fetchall()


def remplir(con: sqlite3.Connection) -> int:
    """Recalcule toute la colonne et rend le nombre d'armes réservées."""
    jetons = jetons_de_vaisseaux(con)
    marquees = 0
    for uuid, tags, class_name, type_objet, nom, _ancien in _lignes(con):
        porteur = porteur_exclusif(
            tags, jetons, class_name, type_objet, nom)
        con.execute("UPDATE items SET porteur_exclusif = ? WHERE uuid = ?",
                    (porteur, uuid))
        marquees += bool(porteur)
    return marquees


def ecarts(con: sqlite3.Connection) -> list[tuple[str, str | None, str | None]]:
    """Compare la projection stockée à son calcul, sans modifier la base."""
    jetons = jetons_de_vaisseaux(con)
    resultat = []
    for uuid, tags, class_name, type_objet, nom, stocke in _lignes(con):
        calcule = porteur_exclusif(
            tags, jetons, class_name, type_objet, nom)
        if stocke != calcule:
            resultat.append((nom or uuid, stocke, calcule))
    return resultat


def incoherences_montage(con: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    """Les armes écartées pourtant montées sur plusieurs familles distinctes.

    Les éditions Wikelo et les variantes d'un même châssis comptent comme un
    porteur : les deux premiers mots gardent `RSI Meteor`, `Aegis Vanguard`
    ou `Kruger L-21`, sans confondre L-21 et L-22.
    """
    try:
        lignes = con.execute(
            "SELECT i.uuid, i.name, sh.name FROM items i "
            "JOIN hardpoints h ON h.installed_uuid = i.uuid "
            "JOIN ships sh ON sh.uuid = h.ship_uuid "
            "WHERE i.type='WeaponGun' AND i.porteur_exclusif IS NOT NULL "
            "AND sh.name IS NOT NULL").fetchall()
    except sqlite3.DatabaseError:
        return []

    familles: dict[str, tuple[str, set[str]]] = {}
    for uuid, nom_arme, nom_vaisseau in lignes:
        morceaux = nom_vaisseau.lower().split()
        famille = " ".join(morceaux[:2])
        familles.setdefault(uuid, (nom_arme or uuid, set()))[1].add(famille)
    return [(nom, sorted(porteurs)) for nom, porteurs in familles.values()
            if len(porteurs) > 1]
