"""Contrôle de santé après ingestion : ce qu'un patch peut casser en silence.

Un patch de Star Citizen ne prévient pas. Il ajoute un système, renomme un type
de lieu, déplace une statistique — et le code continue de tourner en rendant
des réponses vides ou fausses. C'est le pire cas, et il est invisible : rien ne
lève, rien ne s'arrête, les tests passent parce qu'ils tournent sur l'ancienne
base.

Ce module teste les **hypothèses codées en dur** contre la base réelle. Chaque
contrôle répond à une question de la forme « est-ce que ce que je crois savoir
est encore vrai ? ». Il ne corrige rien : il dit ce qui a bougé, pour qu'on
décide.

Trois niveaux :

* `ok`    — l'hypothèse tient.
* `avis`  — quelque chose a changé sans casser : un type de lieu inconnu, un
            système nouveau. La réponse reste juste, elle est peut-être
            incomplète.
* `alerte`— une hypothèse est fausse : une table vide, une relation rompue.
            Des réponses sont fausses ou absentes.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from typing import Callable

from . import config, queries


@dataclasses.dataclass(frozen=True)
class Constat:
    niveau: str          # ok | avis | alerte
    sujet: str
    detail: str

    @property
    def signe(self) -> str:
        # Volontairement en ASCII : la console Windows est en cp1252 et une
        # coche Unicode y fait planter l'affichage, donc le contrôle de
        # santé lui-même. Un vérificateur qui casse ne vérifie rien.
        return {"ok": "[ok]", "avis": "[a revoir]",
                "alerte": "[ALERTE]"}.get(self.niveau, "[?]")


#: Systèmes déjà passés en revue, avec une dangerosité tranchée. La liste
#: existe pour qu'un système **nouveau** se signale : c'est le seul moment où
#: quelqu'un doit décider si on peut s'y poser tranquillement.
_SYSTEMES_CONNUS = {"stanton", "pyro", "nyx"}


def _compte(con: sqlite3.Connection, sql: str, *args) -> int:
    ligne = con.execute(sql, args).fetchone()
    return ligne[0] if ligne else 0


# --------------------------------------------------------------- contrôles

def types_de_lieu(con: sqlite3.Connection) -> Constat:
    """Les types de lieu du starmap sont-ils tous connus ?

    Deux listes en dépendent : celle qui donne un alias aux lieux (sans quoi
    ils sont introuvables) et celle des points de ravitaillement. Un type
    nouveau ne lève rien — il rend simplement des lieux muets.
    """
    connus = {
        "Planet", "Moon", "Star", "SolarSystem", "LandingZone",
        "PointOfInterest", "Manmade", "Manmade_VisibleOnInteraction",
        "Outpost", "Outpost_InvalidQT", "Anomaly", "NavPoint",
        "Asteroid", "Asteroid_ValidQT", "JumpPoint",
    }
    # Les marqueurs d'interface — « YouAreHere », « QUANTUM SNARE EVENT » —
    # n'ont ni nom ni coordonnées. Les signaler comme des lieux manquants
    # ferait crier le contrôle à chaque ingestion, et un contrôle qui crie
    # tout le temps ne se lit plus. On n'alerte que sur un type **nommé et
    # positionné**, c'est-à-dire un vrai endroit où l'on peut aller.
    nouveaux = sorted({r[0] for r in con.execute(
        "SELECT DISTINCT type_name FROM starmap "
        "WHERE type_name IS NOT NULL AND name IS NOT NULL AND x IS NOT NULL")}
        - connus)
    if nouveaux:
        return Constat("avis", "types de lieu",
                       f"nouveaux types de lieu, donc sans alias ni "
                       f"ravitaillement : {', '.join(nouveaux)}")
    return Constat("ok", "types de lieu",
                   "aucun type de lieu inconnu parmi les endroits nommés")


def systemes(con: sqlite3.Connection) -> Constat:
    """Quels systèmes existent, et lesquels sont réputés dangereux ?

    Un patch qui ouvre un système ne dit pas s'il est sûr — pas dans les
    fichiers du jeu. Le wiki le dit, lui, par l'affiliation : `Unclaimed`
    signifie sans autorité, et c'est ce que `queries.systemes_risques()` lit
    désormais. Il reste à confirmer à la main qu'un système nouvellement ouvert
    est bien classé, parce que le wiki peut le publier en retard.
    """
    # Un système compte s'il **contient** des lieux : le nœud racine se
    # référence lui-même, ce qui faisait apparaître « Pyro System » à côté de
    # « Pyro » et annoncer six systèmes là où il y en a trois.
    presents = [r[0] for r in con.execute(
        "SELECT system_name FROM starmap WHERE system_name IS NOT NULL "
        "GROUP BY system_name HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC")]
    connus = _SYSTEMES_CONNUS
    nouveaux = [s for s in presents if s.lower() not in connus]
    if nouveaux:
        return Constat("avis", "systèmes",
                       f"système(s) ouvert(s) depuis la dernière revue : "
                       f"{', '.join(nouveaux)} — vérifier leur affiliation "
                       f"dans wiki_systems et ajouter à "
                       f"verifier._SYSTEMES_CONNUS")
    sans_loi = queries.systemes_risques(con)
    risques = sorted(s for s in presents if s.lower() in sans_loi)
    return Constat("ok", "systèmes",
                   f"{', '.join(presents)} — sans loi : "
                   f"{', '.join(risques) or 'aucun'}")


def points_de_saut(con: sqlite3.Connection) -> Constat:
    """Chaque paire de systèmes reliée a-t-elle ses deux points de saut ?

    Le repérage se fait sur le **nom**, faute de relation en base : les libellés
    amont ne sont pas normalisés (« Stanton-Pyro Jump Point » d'un côté,
    « Nyx - Pyro Jump Point » de l'autre). Une nouvelle convention de nommage
    casserait tous les trajets entre systèmes.
    """
    paires = con.execute(
        "SELECT DISTINCT entry_system, exit_system FROM jump_points "
        "WHERE entry_system IS NOT NULL AND exit_system IS NOT NULL").fetchall()
    if not paires:
        return Constat("alerte", "points de saut",
                       "aucune liaison en base : tout trajet entre systèmes échoue")
    manquants = []
    for entree, sortie in paires:
        if queries._point_de_saut(con, entree, sortie) is None:
            manquants.append(f"{entree} → {sortie}")
    if manquants:
        return Constat("alerte", "points de saut",
                       f"introuvables par leur nom : {', '.join(manquants[:4])}")
    return Constat("ok", "points de saut",
                   f"{len(paires)} liaisons, toutes repérées par leur nom")


def quais_de_saut(con: sqlite3.Connection) -> Constat:
    """Les stations posées sur les points de saut sont-elles toujours là ?

    C'est sur elles que repose le plein « sans détour ». Si elles disparaissent
    ou s'éloignent, le planificateur enverra le joueur se poser ailleurs.
    """
    paires = con.execute(
        "SELECT DISTINCT entry_system, exit_system FROM jump_points "
        "WHERE entry_system IS NOT NULL").fetchall()
    avec = 0
    total = 0
    for entree, sortie in paires:
        saut = queries._point_de_saut(con, entree, sortie)
        if saut is None:
            continue
        total += 1
        if queries._station_du_saut(con, saut) is not None:
            avec += 1
    if total and avec < total:
        return Constat("avis", "quais de saut",
                       f"{avec} sauts sur {total} ont une station au pied")
    return Constat("ok", "quais de saut",
                   f"{avec} sauts sur {total} ont une station au pied")


def moteurs(con: sqlite3.Connection) -> Constat:
    """Les deux types de moteur existent-ils encore, et sont-ils montés ?

    `QuantumDrive` et `JumpDrive` sont des chaînes littérales dans les
    requêtes. Un renommage amont les ferait toutes rendre zéro ligne — sans
    erreur, et tous les vaisseaux perdraient leur capacité de saut.
    """
    q = _compte(con, "SELECT COUNT(*) FROM items WHERE type = 'QuantumDrive'")
    j = _compte(con, "SELECT COUNT(*) FROM items WHERE type = 'JumpDrive'")
    montes_q = _compte(con, """
        SELECT COUNT(DISTINCT h.ship_uuid) FROM hardpoints h
        JOIN items i ON i.name = h.installed_name AND i.type = 'QuantumDrive'""")
    montes_j = _compte(con, """
        SELECT COUNT(DISTINCT h.ship_uuid) FROM hardpoints h
        JOIN items i ON i.name = h.installed_name AND i.type = 'JumpDrive'""")
    if not q or not j:
        return Constat("alerte", "moteurs",
                       f"type absent du catalogue : QuantumDrive={q}, JumpDrive={j}")
    if not montes_q or not montes_j:
        return Constat("alerte", "moteurs",
                       f"aucun vaisseau n'en porte : quantique={montes_q}, "
                       f"saut={montes_j}")
    return Constat("ok", "moteurs",
                   f"{q} quantiques et {j} de saut au catalogue ; "
                   f"{montes_q} et {montes_j} vaisseaux équipés")


def portee_quantique(con: sqlite3.Connection) -> Constat:
    """La portée annoncée égale-t-elle encore carburant ÷ consommation ?

    Toute la planification de trajet repose sur cette égalité. Si CIG change
    la formule, les réponses resteront chiffrées et deviendront fausses — le
    pire cas possible.
    """
    lignes = con.execute("""
        SELECT s.qt_range, s.quantum_fuel, st.qt_fuel_rate
        FROM ships s
        JOIN hardpoints h ON h.ship_uuid = s.uuid AND h.category = 'qdrive'
        JOIN items i ON i.name = h.installed_name AND i.type = 'QuantumDrive'
        JOIN item_stats st ON st.item_uuid = i.uuid
        WHERE s.qt_range > 0 AND s.quantum_fuel > 0 AND st.qt_fuel_rate > 0
        GROUP BY s.uuid LIMIT 40""").fetchall()
    if not lignes:
        return Constat("alerte", "portée quantique",
                       "aucun vaisseau ne permet de vérifier la formule")
    ecarts = [abs(r["quantum_fuel"] / r["qt_fuel_rate"] * 1_000_000 - r["qt_range"])
              / r["qt_range"] for r in lignes]
    pire = max(ecarts)
    if pire > 0.01:
        return Constat("alerte", "portée quantique",
                       f"la formule ne tient plus : écart jusqu'à {pire:.1%} "
                       f"sur {len(lignes)} vaisseaux")
    return Constat("ok", "portée quantique",
                   f"vérifiée sur {len(lignes)} vaisseaux, écart max {pire:.3%}")


def prix_uex(con: sqlite3.Connection) -> Constat:
    """Les prix UEX ont-ils survécu à la réingestion ?

    Ils vivent dans la même base, reconstruite de zéro : une ingestion sans
    synchronisation derrière laisse toutes les questions de prix sans réponse.
    """
    try:
        prix = _compte(con, "SELECT COUNT(*) FROM uex_prices")
    except sqlite3.Error:
        return Constat("alerte", "prix UEX", "table absente — lance « disco uex »")
    if not prix:
        return Constat("alerte", "prix UEX",
                       "table vide : la réingestion les a effacés, "
                       "lance « disco uex »")
    return Constat("ok", "prix UEX", f"{prix} relevés")


def couverture_alias(con: sqlite3.Connection) -> Constat:
    """Les lieux nommables ont-ils tous un alias ?

    Sans alias, un lieu est introuvable quelle que soit la question. C'est ce
    qui rendait Grim HEX muet avant qu'on élargisse le filtre d'ingestion.
    """
    nommables = _compte(
        con, "SELECT COUNT(*) FROM starmap WHERE name IS NOT NULL")
    avec = _compte(
        con, "SELECT COUNT(DISTINCT entity_id) FROM aliases "
             "WHERE entity_type = 'starmap'")
    if nommables and avec / nommables < 0.5:
        return Constat("avis", "alias de lieux",
                       f"{avec} lieux nommables sur {nommables} ont un alias")
    return Constat("ok", "alias de lieux",
                   f"{avec} lieux sur {nommables} sont adressables")


def complement_wiki(con: sqlite3.Connection) -> Constat:
    """Le wiki décrit-il bien le même patch que les fichiers du jeu ?

    C'est le contrôle qui manquait le plus, parce que la panne est muette.
    `disco wiki` refuse d'ingérer quand les builds divergent — mais refuser
    laisse en place les **anciennes** lignes du wiki à côté de tables de jeu
    fraîches. Un statut de production de la 4.9 sous une vitesse de la 4.10 se
    lit exactement comme une réponse juste.

    Le wiki suit CIG avec quelques jours de retard : ce constat sera normal le
    lendemain d'un patch, et il doit se résorber seul. D'où « à revoir » et non
    « alerte » — un contrôle qui crie au loup ne se lit plus.
    """
    from .ingest import wiki

    if not _compte(con, "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'wiki_vehicles'"):
        return Constat("avis", "complément wiki",
                       "table absente — lance « disco wiki »")

    ligne = con.execute("SELECT game_version FROM wiki_vehicles "
                        "WHERE game_version IS NOT NULL LIMIT 1").fetchone()
    la_bas = wiki.build(ligne[0] if ligne else None)
    ici = wiki.build_local(con)
    if ici and la_bas and ici != la_bas:
        return Constat("avis", "complément wiki",
                       f"wiki en build {la_bas}, jeu en {ici} — descriptions, "
                       "statuts et prix réels décrivent un autre patch")

    traduits = _compte(con, "SELECT COUNT(*) FROM wiki_vehicles "
                            "WHERE description_fr IS NOT NULL")
    # Le français est la raison d'être de cette source : s'il s'effondre, la
    # table est là et ne sert plus à rien, ce qui ne se voit nulle part.
    if traduits < 100:
        return Constat("avis", "complément wiki",
                       f"seulement {traduits} vaisseaux décrits en français")
    return Constat("ok", "complément wiki",
                   f"build {la_bas or '?'}, {traduits} vaisseaux en français")


def traduction_fr(con: sqlite3.Connection) -> Constat:
    """La traduction couvre-t-elle encore, et vise-t-elle le bon patch ?

    Le contrôle porte sur les **lieux**, et pas au hasard : c'est la seule
    couverture que rien d'autre ne fournit — le wiki n'a aucune description de
    lieu. Si elle tombe, « c'est quoi Grim HEX » repasse en anglais sans que
    rien ne lève, et on l'apprend par un joueur.

    Une traduction qui vise un autre patch reste utilisable — les textes de la
    4.9 décrivent encore l'essentiel en 4.10 — mais elle vieillit, d'où l'avis
    plutôt que l'alerte. Le Cirque Lisoir publie quelques jours après CIG.
    """
    from .ingest import traduction

    if not _compte(con, "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'traductions'"):
        return Constat("avis", "traduction fr",
                       "table absente — lance « disco trad »")

    lieux = _compte(con, "SELECT COUNT(*) FROM traductions "
                         "WHERE entity_type = 'starmap' AND description_fr IS NOT NULL")
    decrits = _compte(con, "SELECT COUNT(*) FROM starmap "
                           "WHERE TRIM(COALESCE(description, '')) <> ''")
    version = traduction.version_en_base(con) or "?"

    ligne = con.execute("SELECT game_version FROM ingest_runs WHERE status = 'ok' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    jeu = ligne[0] if ligne else None
    if jeu and version != "?" and not version.startswith(traduction._majeure(jeu)):
        return Constat("avis", "traduction fr",
                       f"traduction pour {version.split('-')[0]}, base en {jeu} "
                       "— relance « disco trad » quand le Cirque Lisoir aura publié")
    if decrits and lieux / decrits < 0.5:
        return Constat("avis", "traduction fr",
                       f"{lieux} lieux traduits sur {decrits} décrits")
    return Constat("ok", "traduction fr",
                   f"{version}, {lieux} lieux sur {decrits} en français")


def catalogue_llm(con: sqlite3.Connection) -> Constat:
    """Le catalogue d'outils tient-il encore dans le contexte du LLM local ?

    **C'est la panne invisible type**, et elle a duré. Chaque module ajoute son
    outil au catalogue, le catalogue part en entier dans le prompt de l'étage 3,
    et personne ne regardait la somme. Mesuré le 2026-08-06 : 128 tokens par
    outil, soit 5 460 tokens pour 41 outils — contre 4 096 de contexte par
    défaut. llama-server répondait « 400 exceed_context_size » à **chaque**
    question, `route_local` attrapait l'erreur comme un PC éteint, et la
    cascade retombait sur l'étage 1 sans un mot. L'étage 3 était mort depuis le
    33e outil environ, et seul un rejeu du journal l'a révélé.

    Le contrôle ne parle pas au serveur : il n'a pas à être allumé pour qu'on
    sache que le catalogue a grossi. On compare l'estimation à la taille
    configurée, avec la marge du prompt système et de la question.

    **C'est bien le catalogue entier qui part.** Une sélection des huit outils
    les plus pertinents a été essayée puis retirée — mesurée modèle allumé,
    elle routait moins bien et plus lentement (voir `llm.tool_schemas`). Le
    contrôle porte donc sur la somme, et c'est elle qui doit tenir.
    """
    from .router import llm

    # **2,8 caractères par token, pas 4.** La règle des quatre caractères vaut
    # pour de la prose ; du JSON de schéma est saturé d'accolades, de guillemets
    # et de noms techniques, que le tokenizer découpe menu. Mesuré le
    # 2026-08-06 contre `/tokenize` de llama-server sur le catalogue réel :
    # 5 460 tokens pour 15 400 caractères. Avec le diviseur 4, le contrôle
    # annonçait 4 091 et concluait « tout va bien » alors que le serveur
    # refusait déjà — une estimation qui sous-évalue est pire que pas
    # d'estimation, elle rassure à tort.
    schemas = json.dumps(llm.tool_schemas(), ensure_ascii=False)
    estime = int((len(schemas) + len(llm.SYSTEM)) / 2.8) + 200
    contexte = config.LOCAL_LLM_CONTEXT

    if estime > contexte:
        return Constat(
            "alerte", "catalogue llm",
            f"{len(llm.TOOLS)} outils ~{estime} tokens, contexte {contexte} — "
            f"l'étage 3 refuse toutes les requêtes ; relance llama-server avec "
            f"-Context {max(8192, 2 * contexte)}")
    if estime > 0.85 * contexte:
        return Constat(
            "avis", "catalogue llm",
            f"{len(llm.TOOLS)} outils ~{estime} tokens sur {contexte} — "
            "le prochain module fera déborder")
    return Constat("ok", "catalogue llm",
                   f"{len(llm.TOOLS)} outils ~{estime} tokens sur {contexte}")


def prix_observes(con: sqlite3.Connection) -> Constat:
    """Les prix payés s'écartent-ils des cotations UEX ?

    **UEX vieillit en silence.** C'est de la saisie communautaire : une
    boutique dont personne ne relève plus les prix garde sa cotation
    indéfiniment, et rien ne la signale. Le Game.log des membres porte au
    contraire des **preuves horodatées** — mesuré le 2026-08-14, « Double
    Dog » est relevé à 24 aUEC et a été payé 5.

    C'est exactement le profil que ce module surveille ailleurs : rien ne
    lève, les tests passent, et seule une question posée par un joueur
    révèle le mensonge.

    **Le constat reste un avis, jamais une alerte.** Un écart n'est pas
    une erreur de notre côté : les prix du jeu bougent, un achat peut
    dater, et une promotion existe. Ce qui compte est de pouvoir le
    **regarder** — et le contrôle se tait quand aucun membre n'est lié,
    parce qu'un Chris sans compagnon n'a rien à comparer.
    """
    try:
        from .guilde.observations import couverture, divergences
    except ImportError:
        return Constat("ok", "prix observés", "module de guilde absent")
    try:
        vue = couverture(con)
        ecarts = divergences(con)
    except Exception:  # noqa: BLE001  (base de guilde absente ou verrouillée)
        return Constat("ok", "prix observés",
                       "aucune base de guilde — rien à comparer")

    if not vue["achats"]:
        return Constat("ok", "prix observés",
                       "aucun achat observé — le collecteur n'en a pas vu")
    if ecarts:
        tetes = ", ".join(p.nom for p in ecarts[:3])
        return Constat(
            "avis", "prix observés",
            f"{len(ecarts)} cotations UEX s'écartent de plus de 20 % du prix "
            f"payé ({tetes}…) — sur {vue['comparables']} comparables")
    return Constat("ok", "prix observés",
                   f"{vue['achats']} achats observés, {vue['comparables']} "
                   f"comparables à UEX, aucun écart au-delà de 20 %")


def rayons_de_boutique(con: sqlite3.Connection) -> Constat:
    """Le filtre du rayon écarte-t-il encore la bonne chose ?

    `loaders._rayon_propre` refuse un `displayType` de plus de 30
    caractères, parce que 14 objets sur 7 468 y publient la fiche entière
    de l'objet — « Item Type: Missile Rack / Manufacturer: RSI ». La marge
    est mince : la plus longue valeur réelle, « Quantum Enforcement
    Device », en fait 26.

    **Un patch qui ouvre un rayon plus long le verrait donc écarté sans un
    mot** — les objets concernés perdraient leur catégorie, leur famille
    rendrait une liste vide, et rien ne lèverait. C'est le profil exact de
    la panne invisible que ce module surveille ailleurs. On mesure la
    longueur maximale observée et on prévient avant qu'elle ne touche le
    plafond, plutôt qu'après.
    """
    ligne = con.execute(
        "SELECT COUNT(*), MAX(LENGTH(rayon)), COUNT(DISTINCT rayon) "
        "FROM items WHERE rayon IS NOT NULL").fetchone()
    total, plus_long, distincts = ligne[0], ligne[1] or 0, ligne[2]
    if not total:
        return Constat("avis", "rayons de boutique",
                       "aucun rayon en base — `disco ingest` ne les a pas "
                       "lus, les catalogues de famille répondront à vide")
    # Ce que le filtre aurait laissé passer et qui n'en est pas un : la
    # preuve que le nettoyage a bien couru sur cette base-ci.
    sales = con.execute(
        "SELECT COUNT(*) FROM items WHERE rayon IS NOT NULL AND ("
        "rayon LIKE '%' || char(10) || '%' OR rayon LIKE 'Item Type:%' "
        "OR UPPER(rayon) LIKE '%PLACEHOLDER%')").fetchone()[0]
    if sales:
        return Constat(
            "alerte", "rayons de boutique",
            f"{sales} rayons portent une fiche entière au lieu d'un libellé "
            f"— base ingérée avant le filtre, relance `disco ingest`")
    if plus_long > 26:
        return Constat(
            "avis", "rayons de boutique",
            f"le plus long rayon fait {plus_long} caractères, le filtre "
            f"coupe à 30 — vérifier que rien n'est écarté à tort")
    return Constat("ok", "rayons de boutique",
                   f"{total} objets, {distincts} rayons, "
                   f"{plus_long} caractères au plus")


def raffineries_deux_sources(con: sqlite3.Connection) -> Constat:
    """Le jeu et UEX comptent-ils les mêmes raffineries ?

    Demandé par l'audit du 2026-08-06, et pour une raison précise : le jeu
    déclare **31 lieux avec une raffinerie** dans `Amenities`, la table
    `refineries` issue d'UEX en compte **21**. L'écart n'est pas forcément une
    erreur — UEX ne relève que ce qu'il mesure, et un terminal sans relevé
    n'est pas un terminal absent. Mais **personne ne le surveillait**, et c'est
    exactement le profil des deux écarts silencieux qui ont déjà coûté cher :
    `refineries_yields`, qu'on avait déclaré inexistant après avoir lu le
    mauvais endpoint, et le catalogue LLM, qui dépassait le contexte depuis des
    semaines sans qu'un mot ne le dise.

    « À revoir » et non « alerte » : l'écart est attendu et stable. Ce qui
    doit alerter, c'est qu'il **change** — surtout s'il se creuse après un
    patch, ce qui voudrait dire que le jeu a ouvert des raffineries qu'UEX
    n'a pas encore relevées, et que `ou_raffiner` en ignore.
    """
    if not _compte(con, "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'location_amenities'"):
        return Constat("avis", "raffineries", "table absente — réingère")

    jeu = _compte(con, "SELECT COUNT(DISTINCT location_uuid) "
                       "FROM location_amenities WHERE name = 'Refinery'")
    try:
        uex = _compte(con, "SELECT COUNT(*) FROM refineries")
    except Exception:                                     # noqa: BLE001
        return Constat("avis", "raffineries",
                       f"{jeu} selon le jeu, UEX absent — lance « disco uex »")

    if not jeu:
        return Constat("alerte", "raffineries",
                       "le jeu n'en déclare aucune : Amenities n'est plus lu")
    if uex >= jeu:
        return Constat("ok", "raffineries",
                       f"{uex} chez UEX pour {jeu} déclarées par le jeu")
    return Constat("avis", "raffineries",
                   f"{uex} relevées par UEX pour {jeu} déclarées par le jeu — "
                   f"{jeu - uex} sans cotation, donc invisibles pour "
                   "« où raffiner »")


def couverture_liens(con: sqlite3.Connection) -> Constat:
    """Les croisements wiki, UEX et jeu visent-ils encore les mêmes entités ?

    Une table pleine ne prouve pas qu'elle est joignable. C'est ainsi qu'une
    source peut sembler fraîche tout en ne complétant plus aucune réponse après
    un changement d'identifiant. Le registre mesure les relations elles-mêmes,
    sans table de correspondance persistante : les chiffres décrivent donc la
    base ouverte, pas une photographie oubliée.
    """
    from .liens import mesurer_tous

    mesures = mesurer_tous(con)
    indisponibles = [m for m in mesures if not m.disponible]
    if indisponibles:
        detail = ", ".join(
            f"{m.definition.cle} ({'/'.join(m.tables_manquantes)})"
            for m in indisponibles)
        return Constat("avis", "liens inter-sources",
                       f"source(s) optionnelle(s) absente(s) : {detail}")

    rompus = [m for m in mesures if not m.conforme]
    if rompus:
        details = []
        for mesure in rompus:
            morceaux = [f"source {mesure.source_liee}/{mesure.source_total}"]
            if mesure.cible_total is not None:
                morceaux.append(
                    f"cible {mesure.cible_couverte}/{mesure.cible_total}")
            details.append(f"{mesure.definition.cle} ({', '.join(morceaux)})")
        return Constat("alerte", "liens inter-sources", "; ".join(details))

    par_cle = {m.definition.cle: m for m in mesures}
    vaisseaux = par_cle["wiki_vaisseaux"]
    objets = par_cle["wiki_objets"]
    raffineries = par_cle["raffineries_lieux"]
    return Constat(
        "ok", "liens inter-sources",
        f"{len(mesures)} liens conformes ; wiki vaisseaux "
        f"{vaisseaux.cible_couverte}/{vaisseaux.cible_total}, objets "
        f"{objets.cible_couverte}/{objets.cible_total} ; raffineries "
        f"contextualisées {raffineries.source_liee}/{raffineries.source_total}")


def version_uex(con: sqlite3.Connection) -> Constat:
    """UEX décrit-il le même jeu que la base ?

    Le pendant du contrôle wiki, pour la même raison : au jour du patch, un
    catalogue 4.10 sous des prix encore relevés en 4.9 se lit exactement
    comme une réponse juste. UEX suit le jeu en quelques jours — le constat
    est donc « à revoir », il doit se résorber seul.
    """
    try:
        ligne = con.execute("SELECT game_live FROM uex_source "
                            "WHERE url = 'game_versions'").fetchone()
    except sqlite3.Error:
        return Constat("avis", "version UEX",
                       "inconnue — relance « disco uex » pour l'enregistrer")
    if not ligne or not ligne[0]:
        return Constat("avis", "version UEX",
                       "inconnue — relance « disco uex » pour l'enregistrer")
    la_bas = str(ligne[0])
    ici = con.execute("SELECT game_version FROM ingest_runs "
                      "WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
    notre = (ici[0] or "") if ici else ""
    # « 4.9.0 » contre « 4.9 » : on compare à la granularité qu'UEX annonce.
    if notre.startswith(la_bas) or la_bas.startswith(notre[:3]):
        return Constat("ok", "version UEX", f"{la_bas}, comme la base")
    return Constat("avis", "version UEX",
                   f"UEX décrit la {la_bas}, la base est en {notre} — les "
                   "prix parlent d'un autre patch")


def compagnon_grave(con: sqlite3.Connection) -> Constat:
    """L'adresse gravée dans le binaire distribué aux membres.

    Vécu le 2026-08-13 : `dist/Compagnon CCC.exe` — servi par
    `/api/guilde/compagnon.exe`, le téléchargement de la page Commencer —
    était gravé `http://127.0.0.1:8000`. Chez le membre, le compagnon
    frappait à sa propre porte (« WinError 10061 »). Le script de
    compilation avait prévenu, d'un `Write-Warning` noyé dans le
    défilement : un avertissement de build ne se lit pas, une ligne de ce
    contrôle, si.
    """
    chemin = config.PROJECT_ROOT / "dist" / "Compagnon CCC.exe"
    if not chemin.is_file():
        return Constat("ok", "compagnon gravé",
                       "pas de binaire dans dist/ — rien à distribuer")
    try:
        from PyInstaller.archive.readers import CArchiveReader
        adresse = CArchiveReader(str(chemin)).extract("serveur.txt") \
            .decode("utf-8").strip()
    except KeyError:
        return Constat("alerte", "compagnon gravé",
                       "aucune adresse gravée — le binaire pointera sur la "
                       "machine du membre ; recompile avec le tunnel ouvert")
    except ImportError:
        return Constat("avis", "compagnon gravé",
                       "PyInstaller absent — gravure invérifiable ici")
    except Exception as exc:                                  # noqa: BLE001
        return Constat("avis", "compagnon gravé",
                       f"binaire illisible ({type(exc).__name__}) — gravure "
                       "invérifiable")
    if "127.0.0.1" in adresse or "localhost" in adresse:
        return Constat("alerte", "compagnon gravé",
                       f"{adresse} — le binaire distribué pointe sur la "
                       "machine du membre, où rien n'écoute ; recompile "
                       "avec le tunnel ouvert (« Créer l'installeur »)")
    return Constat("ok", "compagnon gravé", adresse)


def armes_soudees(con: sqlite3.Connection) -> Constat:
    """La projection des porteurs correspond-elle encore à son calcul ?

    Une colonne présente mais partiellement calculée ne lève rien : elle rend
    seulement le filtre trop permissif. La cohérence porte donc ligne par
    ligne, ce qui attrape aussi une règle ajoutée après la dernière ingestion.
    """
    colonnes = {ligne[1] for ligne in con.execute("PRAGMA table_info(items)")}
    if "porteur_exclusif" not in colonnes:
        return Constat("alerte", "armes soudées",
                       "colonne absente — ouvre la base en écriture ou "
                       "relance « disco ingest »")
    armes = _compte(con, "SELECT COUNT(*) FROM items WHERE type='WeaponGun'")
    if not armes:
        return Constat("alerte", "armes soudées", "aucune arme de vaisseau")

    from . import porteurs

    ecarts = porteurs.ecarts(con)
    if ecarts:
        exemples = ", ".join(nom for nom, _stocke, _calcule in ecarts[:4])
        return Constat(
            "alerte", "armes soudées",
            f"{len(ecarts)} projection(s) périmée(s) sur {armes} armes : "
            f"{exemples} — lance « disco porteurs »")
    incoherentes = porteurs.incoherences_montage(con)
    if incoherentes:
        exemples = ", ".join(nom for nom, _familles in incoherentes[:4])
        return Constat(
            "alerte", "armes soudées",
            f"{len(incoherentes)} arme(s) écartée(s) sont montées sur "
            f"plusieurs familles : {exemples}")
    marquees = _compte(
        con, "SELECT COUNT(*) FROM items WHERE type='WeaponGun' "
        "AND porteur_exclusif IS NOT NULL")
    return Constat("ok", "armes soudées",
                   f"{marquees} armes réservées sur {armes}, projection cohérente")


def catalogue_activites(con: sqlite3.Connection) -> Constat:
    """Les fiches d'activité décrivent-elles encore le patch installé ?

    Deux pannes muettes, toutes deux invisibles sans ce contrôle :

    - **un lieu que CIG a renommé.** L'ingestion garde la ligne avec un
      `uuid` nul plutôt que de l'effacer, précisément pour qu'on la voie
      ici. Effacée, la fiche continuerait d'afficher un déroulé qui envoie
      le joueur à un endroit qui n'existe plus ;
    - **une fiche qui a vieilli.** Un guide écrit pour la 4.7 se lit
      exactement comme un guide à jour. Le `patch_verifie` la date.

    C'est un **avis**, pas une alerte : le contenu communautaire suit CIG
    avec du retard, comme le wiki, et le constat doit pouvoir se résorber
    seul. Un contrôle qui crie au loup ne se lit plus.
    """
    if not _compte(con, "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'activites'"):
        return Constat("avis", "catalogue d'activités",
                       "table absente — lance « disco activites »")

    total = _compte(con, "SELECT COUNT(*) FROM activites")
    if not total:
        return Constat("avis", "catalogue d'activités",
                       "aucune fiche ingérée — lance « disco activites »")

    perdus = [f"{cle} → {cherche}" for cle, cherche in con.execute(
        "SELECT cle, cherche FROM activite_lieux WHERE uuid IS NULL")]
    if perdus:
        return Constat("avis", "catalogue d'activités",
                       f"{len(perdus)} lieu(x) introuvable(s) dans le "
                       f"starmap : {', '.join(perdus[:4])}")

    ligne = con.execute("SELECT game_version FROM ingest_runs "
                        "WHERE status = 'ok' ORDER BY id DESC LIMIT 1").fetchone()
    build = (ligne[0] if ligne else "") or ""
    # On compare les deux premiers segments — « 4.9.0 » contre « 4.9 ». Une
    # correction de patch (4.9.1) ne périme pas une fiche ; une version
    # mineure, si.
    courant = ".".join(build.split(".")[:2])

    from .activites import version_patch

    if courant:
        # **On ne signale que ce qui est en retard, jamais ce qui est en
        # avance.** Une fiche de contenu à venir porte par construction un
        # patch supérieur au build installé — `siege-of-orison` est vérifiée
        # en 4.10 sur une base 4.9. La première version de ce contrôle
        # l'annonçait « vérifiée avant 4.9 », ce qui est faux et ferait
        # corriger une fiche parfaitement juste. Mesuré à la première
        # exécution.
        seuil = version_patch(courant)
        vieilles = [cle for cle, patch, statut in con.execute(
            "SELECT cle, patch_verifie, statut FROM activites")
            if statut != "a_venir" and version_patch(patch) < seuil]
        if vieilles:
            return Constat("avis", "catalogue d'activités",
                           f"{len(vieilles)} fiche(s) vérifiée(s) avant "
                           f"{courant} : {', '.join(sorted(vieilles)[:4])}")

    contrats = _compte(con, "SELECT COUNT(*) FROM activite_contrats")
    # Une fiche qui déclare des ancrages et n'en résout aucun est le signe
    # qu'une famille de contrats a été renommée en amont.
    #
    # **Sauf pour ce qui n'est pas dans le build.** Deuxième faux positif du
    # même détecteur, mesuré dans la foulée du premier : `siege-of-orison`
    # (à venir) et `xenothreat` (temporaire) n'ont aucun contrat parce que
    # le contenu n'est pas installé, pas parce qu'une famille a bougé. Deux
    # constats faux sur deux, c'est la règle du projet qui s'applique — un
    # détecteur se calibre contre les vraies données avant d'être livré.
    #
    # **Et une boucle s'ancre sur un type, pas sur des contrats nommés.**
    # Troisième calibrage du même détecteur, et celui-ci n'était pas un faux
    # positif de statut mais un trou : il ne regardait qu'`activite_contrats`
    # et déclarait orphelines les treize boucles du lot B, qui sont pourtant
    # rattachées à 19 types de mission. Un ancrage vaut l'autre.
    orphelines = [cle for (cle,) in con.execute(
        "SELECT a.cle FROM activites a "
        "LEFT JOIN activite_contrats c ON c.cle = a.cle "
        "LEFT JOIN activite_types t ON t.cle = a.cle "
        "WHERE a.mission IN ('oui','partiellement') "
        "  AND a.statut = 'vivant' "
        "GROUP BY a.cle "
        "HAVING COUNT(c.uuid) = 0 AND COUNT(t.mission_type) = 0")]
    if orphelines:
        return Constat("avis", "catalogue d'activités",
                       f"{len(orphelines)} fiche(s) sur contrat sans aucun "
                       f"contrat rattaché : {', '.join(sorted(orphelines))}")

    # **Une note de patch « à venir » doit se signaler le jour où il sort.**
    # Sinon elle reste au futur indéfiniment, et le lecteur croit attendre
    # quelque chose qu'il a déjà installé. C'est le pendant temporel de
    # `patch_verifie` : là on surveille une fiche qui vieillit, ici une note
    # qui se réalise.
    notes = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name='nouveautes'").fetchone()[0]
    if notes and courant:
        arrives = [patch for patch, in con.execute(
            "SELECT patch FROM nouveautes WHERE statut='a_venir'")
            if version_patch(patch) <= version_patch(courant)]
        if arrives:
            return Constat("avis", "catalogue d'activités",
                           f"la note du patch {', '.join(arrives)} est encore "
                           f"marquée « à venir » alors que {courant} est "
                           "installé — à relire et à passer en « sorti »")

    return Constat("ok", "catalogue d'activités",
                   f"{total} fiches en {courant or '?'}, "
                   f"{contrats} contrats rattachés")





CONTROLES: tuple[Callable[[sqlite3.Connection], Constat], ...] = (
    types_de_lieu, systemes, points_de_saut, quais_de_saut, moteurs,
    portee_quantique, prix_uex, couverture_alias, complement_wiki, traduction_fr,
    catalogue_llm, raffineries_deux_sources, couverture_liens, version_uex,
    compagnon_grave, catalogue_activites, armes_soudees,
    rayons_de_boutique, prix_observes,
)


def verifier(con: sqlite3.Connection) -> list[Constat]:
    """Tous les contrôles. Un contrôle qui explose est lui-même un constat."""
    resultats = []
    for controle in CONTROLES:
        try:
            resultats.append(controle(con))
        except Exception as exc:                          # noqa: BLE001
            resultats.append(Constat("alerte", controle.__name__,
                                     f"{type(exc).__name__}: {exc}"))
    return resultats


def resume(constats: list[Constat]) -> tuple[str, int]:
    """Une ligne de synthèse et un code de sortie utilisable par le launcher."""
    alertes = [c for c in constats if c.niveau == "alerte"]
    avis = [c for c in constats if c.niveau == "avis"]
    if alertes:
        return (f"{len(alertes)} alerte(s) : "
                + " · ".join(c.sujet for c in alertes), 1)
    if avis:
        return (f"{len(avis)} point(s) à revoir : "
                + " · ".join(c.sujet for c in avis), 0)
    return ("tout est conforme", 0)


def journaliser(constats: list[Constat], echo=print) -> None:
    for constat in constats:
        echo(f"  {constat.signe} {constat.sujet:20} {constat.detail}")
