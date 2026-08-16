"""Les objets : catalogue, porteur exclusif, ports, contenances.

Découpé de `loaders.py` au sprint 43 (US-4) : 2 499 lignes
mélangeaient une trentaine de chargeurs sans frontière, et
l'ingestion est l'endroit où une erreur coûte le plus cher —
elle vide des réponses sans rien lever.
"""

from __future__ import annotations
import json
import pathlib
import sqlite3
from ..porteurs import CLASSES_DE_PORTEUR as _CLASSES_DE_PORTEUR  # noqa: F401
from ..porteurs import MARQUEURS_DE_PORTEUR as _MARQUEURS_DE_PORTEUR  # noqa: F401
from ..porteurs import SUFFIXES_DE_PORTEUR as _SUFFIXES_DE_PORTEUR  # noqa: F401
from ..porteurs import jetons_de_vaisseaux
from ..porteurs import porteur_exclusif as _porteur_exclusif
from .source import read_json
from .socle import AliasCollector, PLACEHOLDER
from .statistiques import _armor_stats, _charger_modificateur_accessoire, _component_stats, _lignes_reseau, _mecanique_stats, _weapon_stats

# ------------------------------------------------------------------ objets

# Le champ `tags` d'un objet porte sa famille : « GATS BallisticGatling
# flightReady weaponMountUsable ». C'est là, et nulle part ailleurs, que se lit
# la différence entre un canon balistique et un canon laser.
_WEAPON_CLASSES = {
    "ballistic": "ballistic", "laser": "laser", "plasma": "plasma",
    "tachyon": "tachyon", "distortion": "distortion", "emp": "emp",
}
_WEAPON_KINDS = {
    "gatling": "gatling", "repeater": "repeater", "scattergun": "scattergun",
    "cannon": "cannon", "shotgun": "scattergun",
}


_DEV_MARQUEURS = ("test", "template", "placeholder", "debug", "_wip")


def _weapon_family(tags: str | None, class_name: str | None) -> tuple[str | None, str | None]:
    """Famille d'une arme, le nom de classe faisant foi.

    Les deux sources se contredisent parfois : `BEHR_JavelinBallisticCannon_S7`
    porte le tag `BEHR LaserCannon`. Le nom de classe est généré
    systématiquement à partir du fichier de définition, le tag est saisi à la
    main — on croit le premier et on se rabat sur le second.
    """
    def cherche(source: str) -> tuple[str | None, str | None]:
        bas = source.lower()
        return (
            next((v for k, v in _WEAPON_CLASSES.items() if k in bas), None),
            next((v for k, v in _WEAPON_KINDS.items() if k in bas), None),
        )

    classe, genre = cherche(class_name or "")
    secours_classe, secours_genre = cherche(tags or "")
    return classe or secours_classe, genre or secours_genre


def _usability(tags: str | None, class_name: str | None) -> tuple[int, int, int]:
    """Montable par un joueur, ou pièce de décor technique ?"""
    bas_tags = (tags or "").lower()
    bas_class = (class_name or "").lower()
    return (
        int("flightready" in bas_tags),
        int("weaponmountusable" in bas_tags),
        int(any(m in bas_class for m in _DEV_MARQUEURS)),
    )


def _lootabilite(etiquettes: list | None) -> tuple[int, str | None]:
    """Un objet peut-il apparaître en butin, et de quelle provenance.

    `entity_tag_map` porte un vocabulaire fermé : `CanGenerateAsLoot`,
    `LootableFromSuit`, `CannotGenerateAsLoot`, `Unlootable`. Mesuré — 3 823
    objets sur 6 266 étiquetés sont lootables, mais **presque tous sont de
    l'équipement personnel** : 2 boucliers de vaisseau seulement, aucun moteur
    quantique, aucune arme de vaisseau.

    L'interdiction l'emporte sur l'autorisation : un objet portant les deux
    n'est pas lootable.
    """
    noms = {t.get("name") for t in (etiquettes or []) if t.get("name")}
    if not noms:
        return 0, None
    if {"CannotGenerateAsLoot", "Unlootable", "unlootable"} & noms:
        return 0, None
    if "LootableFromSuit" in noms:
        return 1, "suit"
    if "CanGenerateAsLoot" in noms:
        return 1, "generic"
    return 0, None


def load_items(con: sqlite3.Connection, root: pathlib.Path,
               aliases: AliasCollector) -> int:
    """`fps-items.json` + `ship-items.json`.

    Pas `items/` : ses 11 045 fichiers exclusifs sont du mobilier (cf. audit).
    Le nom affichable vit dans `stdItem.Name` ; le `name` de premier niveau est
    souvent un placeholder.
    """
    total = 0
    seen: set[str] = set()
    # Les vaisseaux sont chargés avant les objets : on peut donc reconnaître
    # un tag qui nomme un porteur au lieu d'écrire la liste à la main.
    jetons_vaisseaux = jetons_de_vaisseaux(con)
    for filename in ("ship-items.json", "fps-items.json"):
        path = root / filename
        if not path.exists():
            continue
        for entry in read_json(path):
            uuid = entry.get("reference")
            if not uuid or uuid in seen:
                continue
            seen.add(uuid)
            std = entry.get("stdItem") or {}
            name = std.get("Name") or entry.get("name")
            if name == PLACEHOLDER:
                name = None
            std_manu = std.get("Manufacturer")
            # Le fabricant de premier niveau est renseigné à 80 %, celui de
            # stdItem beaucoup moins. On prend le meilleur des deux.
            manufacturer = entry.get("manufacturer") or (
                std_manu.get("Name") if isinstance(std_manu, dict) else std_manu
            )
            vol, mount, dev = _usability(entry.get("tags"), entry.get("className"))
            butin, provenance = _lootabilite(entry.get("entity_tag_map"))
            # La lettre de grade et la classe d'usage vivent dans le bloc
            # descriptif, pas à la racine — et seulement sur les composants.
            descriptif = std.get("DescriptionData") or {}
            con.execute(
                "INSERT OR REPLACE INTO items "
                "(uuid, class_name, name, type, subtype, size, grade, "
                " manufacturer_name, classification, tags, rarity, "
                " grade_lettre, item_class, volume_uscu, porteur_exclusif, "
                " flight_ready, mount_usable, is_dev, description, lootable, "
                " loot_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid, entry.get("className"), name,
                    entry.get("type"), entry.get("subType"),
                    entry.get("size"), str(entry.get("grade") or ""),
                    manufacturer, entry.get("classification"), entry.get("tags"),
                    # « Une armure légendaire » — il y en a deux.
                    std.get("Rarity"),
                    # La lettre du jeu et la classe d'usage. Elles ne sont
                    # renseignées que sur les composants — 336 objets — et
                    # c'est justement là qu'un joueur les cherche.
                    str(descriptif.get("Grade") or "") or None,
                    str(descriptif.get("Class") or "") or None,
                    _volume_uscu(std.get("InventoryOccupancy")),
                    _porteur_exclusif(entry.get("tags"), jetons_vaisseaux,
                                      entry.get("className"),
                                      entry.get("type"), name),
                    vol, mount, dev,
                    std.get("DescriptionText") or std.get("Description"),
                    butin, provenance,
                ),
            )
            total += 1
            # **L'échafaudage de CIG n'a pas d'alias** — même règle que les
            # gisements `MineableRock_test_*` : cinq objets « PLACEHOLDER - … »
            # et « [PH] … » au balayage du 2026-08-07, du gabarit de vêtement.
            # La ligne reste en base, mais un joueur ne peut pas la nommer.
            nom_bas = (name or "").upper()
            if not nom_bas.startswith(("PLACEHOLDER", "[PH]")):
                aliases.add_variants("item", uuid, name, entry.get("className"))
            _charger_ports(con, uuid, std.get("Ports"))
            _charger_modificateur_accessoire(con, uuid, std)

            mecanique = _mecanique_stats(std)
            if mecanique:
                colonnes = ", ".join(mecanique)   # fermés par _mecanique_stats
                marques = ",".join("?" * (len(mecanique) + 1))
                con.execute(
                    f"INSERT OR REPLACE INTO item_stats (item_uuid, {colonnes}) "
                    f"VALUES ({marques})", (uuid, *mecanique.values()))

            armure = _armor_stats(std)
            if armure:
                # Les noms de colonne sont fermés par `_armor_stats`, jamais
                # construits depuis la source.
                colonnes = ", ".join(armure)
                marques = ",".join("?" * (len(armure) + 1))
                con.execute(
                    f"INSERT OR REPLACE INTO item_stats (item_uuid, {colonnes}) "
                    f"VALUES ({marques})", (uuid, *armure.values()))

            composant = _component_stats(std)
            if composant:
                con.execute(
                    "INSERT OR REPLACE INTO item_stats "
                    "(item_uuid, shield_health, shield_regen, shield_downed, "
                    " qt_jump_range, qt_drive_speed, qt_cooldown, qt_fuel_rate, "
                    " health, item_mass, cooling_rate, power_rate, "
                    " signature_em, signature_ir) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid, composant["shield_health"], composant["shield_regen"],
                     composant["shield_downed"], composant["qt_jump_range"],
                     composant["qt_drive_speed"], composant["qt_cooldown"],
                     composant["qt_fuel_rate"], composant["health"],
                     composant["mass"], composant.get("cooling_rate"),
                     composant.get("power_rate"), composant.get("signature_em"),
                     composant.get("signature_ir")),
                )

            for ligne_reseau in _lignes_reseau(std):
                # Colonnes fermées par `_lignes_reseau`, jamais par la source.
                etat = ligne_reseau.pop("etat")
                colonnes = ", ".join(ligne_reseau)
                marques = ",".join("?" * (len(ligne_reseau) + 2))
                con.execute(
                    f"INSERT OR REPLACE INTO item_reseau "
                    f"(item_uuid, etat, {colonnes}) VALUES ({marques})",
                    (uuid, etat, *ligne_reseau.values()))

            stats = _weapon_stats(std)
            if stats:
                classe, genre = _weapon_family(entry.get("tags"), entry.get("className"))
                con.execute(
                    "INSERT OR REPLACE INTO item_stats "
                    "(item_uuid, weapon_class, weapon_kind, dps, alpha, "
                    " rounds_per_minute, effective_range, projectile_speed, "
                    " ammo_capacity, pellets_per_shot, dps_physical, dps_energy, "
                    " dps_distortion, health, item_mass, fire_modes, "
                    " ammo_per_shot, capacitor_max, capacitor_regen, "
                    " capacitor_cost, capacitor_cooldown, "
                    " alpha_physical, alpha_energy, alpha_thermal, "
                    " alpha_biochemical, alpha_distortion, alpha_stun) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "?,?,?,?,?,?)",
                    (uuid, classe, genre, stats["dps"], stats["alpha"], stats["rpm"],
                     stats["range"], stats["speed"], stats["capacity"],
                     stats["pellets"], stats["physical"], stats["energy"],
                     stats["distortion"], stats["health"], stats["mass"],
                     stats["modes"], stats["ammo_per_shot"], stats["cap_max"],
                     stats["cap_regen"], stats["cap_cost"], stats["cap_cooldown"],
                     stats["alpha_physical"], stats["alpha_energy"],
                     stats["alpha_thermal"], stats["alpha_biochemical"],
                     stats["alpha_distortion"], stats["alpha_stun"]),
                )
                # « canon balistique », « gatling » : des façons de désigner
                # une arme qui ne figurent dans aucun nom propre.
                if classe and name:
                    aliases.add("item", uuid, f"{genre or 'arme'} {classe}",
                                source="derived", weight=0.5)
    return total


# ------------------------------------------------------------------ missions

# 1 SCU = 100 cSCU = 1 000 mSCU = 1 000 000 µSCU. Le jeu emploie surtout µSCU.
_UNITES_SCU = {"SCU": 1_000_000, "cSCU": 10_000, "mSCU": 1_000, "µSCU": 1}


def _volume_uscu(occupancy) -> int | None:
    """La place qu'occupe un objet, en µSCU entiers.

    On lit `SCUConverted` et son `Unit`, jamais le champ `SCU` : celui-ci est
    arrondi à **0** pour tout ce qui tient sous le SCU, c'est-à-dire pour
    presque tout le catalogue. Un pistolet Coda y vaut 0 alors qu'il occupe
    2 500 µSCU. Prendre `SCU` aurait donné « tu en mets une infinité ».

    Une unité inconnue rend `None` plutôt qu'un chiffre faux : mieux vaut ne
    pas répondre que répondre à côté d'un facteur mille.
    """
    volume = (occupancy or {}).get("Volume") if isinstance(occupancy, dict) else None
    if not isinstance(volume, dict):
        return None
    facteur = _UNITES_SCU.get(volume.get("Unit"))
    valeur = volume.get("SCUConverted")
    if facteur is None or not isinstance(valeur, (int, float)):
        return None
    return round(valeur * facteur)


def _charger_ports(con: sqlite3.Connection, uuid: str, ports) -> None:
    """Les emplacements déclarés par un objet, un par type accepté.

    Un port peut accepter plusieurs types — on écrit une ligne par type plutôt
    qu'une chaîne à découper à la lecture : c'est ce qui rend la question
    inverse (« qu'est-ce qui va dans ce port ») indexable.

    Les ports sans type (`item_grab`, la prise en main) ne servent à rien ici
    et ne sont pas écrits.
    """
    if not isinstance(ports, list):
        return
    for port in ports:
        if not isinstance(port, dict):
            continue
        for accepte in (port.get("Types") or []):
            if not accepte:
                continue
            # Les tags exigés partent en une chaîne séparée par des espaces,
            # comme `items.tags` : les deux se comparent, autant qu'ils
            # s'écrivent pareil.
            exiges = [t for t in (port.get("RequiredTags") or []) if t]
            con.execute(
                "INSERT INTO item_ports (item_uuid, port_name, accepted, "
                " min_size, max_size, required_tags) VALUES (?,?,?,?,?,?)",
                (uuid, port.get("PortName"), accepte,
                 port.get("MinSize"), port.get("MaxSize"),
                 " ".join(exiges) or None))


def _rayon_propre(brut: object) -> str | None:
    """Le rayon de boutique, ou None si le champ porte autre chose.

    **`displayType` n'est pas toujours un rayon.** Mesuré sur les 7 468
    objets renseignés : 14 portent la **fiche entière** de l'objet à la
    place — « Item Type: Missile Rack / Manufacturer: RSI / Size: 2 » pour
    les racks RSI, le texte d'ambiance complet pour quatre livrées du 600i,
    et « HEI: 16 / Effects: Energizing… » pour trois boissons. C'est le
    piège « une colonne pleine peut être vide de sens » : 0,2 % de lignes
    suffisent à faire apparaître un paragraphe entier comme nom de
    catégorie dans un catalogue.

    Un rayon est un libellé court, d'une seule ligne — les 62 valeurs
    réelles vont de « SMG » à « Quantum Enforcement Device », 26
    caractères au plus. Les gabarits `PLACEHOLDER` sortent par la même
    porte : un gabarit n'est le nom de rien.
    """
    if not isinstance(brut, str):
        return None
    rayon = brut.strip()
    if not rayon or "PLACEHOLDER" in rayon.upper():
        return None
    if "\n" in rayon or len(rayon) > 30 or rayon.startswith("Item Type:"):
        return None
    return rayon


def load_contenances(con: sqlite3.Connection, root: Path) -> int:
    """Combien on peut ranger **dans** un objet, en µSCU.

    **La trouvaille de l'audit du 2026-08-13**
    (`docs/AUDIT_SCUNPACKED_2026-08-13.md`), demandé par l'utilisateur :
    « j'ai bien l'impression qu'il y a encore des secrets à livrer ». Il
    avait raison. 2 073 objets publient leur contenance, et **aucun index
    racine ne la porte** — mesuré, 0 sur 10 804. Elle n'existe que dans
    les fichiers individuels de `items/`, sous le `Raw` du moteur :

        Raw.Entity.Components.SCItemInventoryContainerComponentParams
           .inventoryContainer.inventoryType.InventoryClosedContainerType
           .capacity.SMicroCargoUnit.microSCU

    **À ne pas confondre avec `volume_uscu`**, qui est la place que
    l'objet prend. Ici c'est ce qu'il avale : un sac Novikov porte
    0,180 SCU quand le plus petit en porte 0,054 — plus du triple, donc
    un vrai critère de choix.

    On filtre sur la sous-chaîne avant de parser : 21 849 fichiers pour
    3,4 Go, et le JSON complet de chacun coûterait cent fois plus cher
    que le test. Mesuré : 10 s pour le lot entier.
    """
    dossier = root / "items"
    if not dossier.is_dir():
        return 0
    connus = {ligne[0].lower(): ligne[1] for ligne in con.execute(
        "SELECT LOWER(class_name), uuid FROM items WHERE class_name IS NOT NULL")}
    total = 0
    for chemin in dossier.glob("*.json"):
        try:
            brut = chemin.read_text(encoding="utf-8")
        except OSError:
            continue
        # **Une seule passe pour les deux champs.** 21 849 fichiers et
        # 3,4 Go ne se relisent pas deux fois : le filtre par
        # sous-chaîne accepte l'un ou l'autre, et le parsing ne se fait
        # qu'ensuite.
        veut_contenance = "microSCU" in brut and "InventoryContainer" in brut
        veut_rayon = "SCItemPurchasableParams" in brut
        if not (veut_contenance or veut_rayon):
            continue
        try:
            donnees = json.loads(brut)
        except ValueError:
            continue
        item = donnees.get("Item") or {}
        classe = str(item.get("className") or chemin.stem).lower()
        uuid = connus.get(classe)
        if uuid is None:
            continue          # objet hors catalogue : mobilier, décor, test
        composants = ((donnees.get("Raw") or {}).get("Entity") or {}
                      ).get("Components") or {}

        if veut_contenance:
            conteneur = (composants.get(
                "SCItemInventoryContainerComponentParams") or {}
            ).get("inventoryContainer") or {}
            capacite = (((conteneur.get("inventoryType") or {}).get(
                "InventoryClosedContainerType") or {}).get("capacity") or {})
            micro = (capacite.get("SMicroCargoUnit") or {}).get("microSCU")
            if micro:
                con.execute(
                    "UPDATE items SET contenance_uscu = ? WHERE uuid = ?",
                    (int(micro), uuid))
                total += 1

        # **Le rayon de boutique dit ce que `type` confond.** Retenu par
        # le crible du sprint 38 (docs/SPRINT_2026-08-15.md) : mesuré,
        # 34 couples (type, sous-type) sur 135 portent **plusieurs**
        # rayons — `WeaponGun/Gun` se décompose en Beam, Cannon, Gatling
        # et Repeater, et 62 armes sur 202 n'ont pas cette famille dans
        # leurs tags texte.
        if veut_rayon:
            rayon = _rayon_propre(
                (composants.get("SCItemPurchasableParams") or {}
                 ).get("displayType"))
            if rayon:
                con.execute("UPDATE items SET rayon = ? WHERE uuid = ?",
                            (rayon, uuid))
    return total
