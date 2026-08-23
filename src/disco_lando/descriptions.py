"""Dire ce qu'une chose **est** — la prose, pas les chiffres.

« C'est quoi Grim HEX » veut savoir ce que c'est, pas combien ça pèse. Le
texte officiel existe pour 2 032 lieux sur 2 054, 7 780 objets, 315 vaisseaux
sur 316, plus les factions — qui portent aussi les personnages, Wikelo et
Recco Battaglia compris.

Les arbitrages de ce module, tous éprouvés et deux fois annulés :

- **L'ordre des types prime.** Sauter les descriptions qui répètent le nom
  faisait répondre la *planète Crusader* à « c'est quoi Crusader Security » ;
  arbitrer au score faisait gagner le bibelot « Gladius Model » contre le
  vaisseau. Le score dit à quel point le nom colle, pas quelle **sorte** de
  chose on cherche.
- **Un score de 100 passe devant l'ordre des types, et `prefere` devant les
  deux.** Un 100 signifie que le terme tapé **est** le nom de l'entité.
- **« Qui » et « quoi » ne cherchent pas au même endroit.** Wikelo est à la
  fois une personne et trois stations.
- **Le concret d'une mission avant sa couleur** : où la prendre, où ça se
  passe, ce qu'on y fait. Le briefing devient une proposition.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .constructeurs import _constructeur
from .liens import ligne_source
from .normalize import normalize
from .resolver import resolve


#: Où chercher une description, et comment lire la ligne trouvée. L'ordre sert
#: de départage à score égal ; `prefere` le réordonne selon que la question dit
#: « qui » ou « quoi ».
_SOURCES_DESCRIPTION = (
    ("contract", "contracts", "mission"),
    ("org", "factions", "personne"),
    ("starmap", "starmap", "lieu"),
    ("ship", "ships", "vaisseau"),
    ("item", "items", "objet"),
    # En dernier : « Aegis » désigne aussi bien le constructeur qu'un préfixe
    # de vaisseau, et « décris-moi un Aegis Gladius » parle du vaisseau. Un
    # constructeur ne gagne que si rien d'autre ne répond.
    ("manufacturer", "manufacturers", "constructeur"),
    # **Les commodités et les minerais manquaient, et ça se voyait.** Le
    # balayage a trouvé 359 entités que `decrire` ne savait pas décrire alors
    # que le résolveur les trouve à 100 exact : « c'est quoi le Titanium »
    # levait `NotFound`. Les commodités portent 200 descriptions officielles
    # sur 206 ; les minerais n'en ont aucune, mais leur rareté et leur famille
    # sont en base — ce qui répond à la question posée.
    #
    # En **dernier**, comme le constructeur : « Hadanite » est à la fois un
    # minerai et un objet, et l'objet reste la lecture par défaut. Ces deux
    # sources ne répondent que si rien d'autre ne sait.
    ("commodity", "commodities", "commodite"),
    ("resource", "resources", "minerai"),
)


def _ligne_decrite(con: sqlite3.Connection, entity_type: str, table: str,
                   entity_id: str) -> dict[str, Any] | None:
    """La ligne décrivant une entité, quelle que soit sa clé.

    Les organisations n'ont pas d'UUID côté résolveur : leur identifiant **est
    leur nom**, celui que portent les contrats (`mission_giver`). Et ce nom
    n'est pas toujours celui de la faction — « Wikelo » donne des missions,
    « Wikelo Emporium » a la fiche. On rattrape par préfixe, ce qui est sûr
    ici : les deux noms se recouvrent par construction.
    """
    if entity_type != "org":
        ligne = _dict(_row(con, f"SELECT * FROM {table} WHERE uuid = ?", entity_id))
        # Les objets citent leur constructeur par son code : « Coda Pistol —
        # KSAR ». Le nom complet est en base depuis que `manufacturers.json`
        # est ingéré.
        if ligne is not None and "manufacturer_name" in ligne:
            ligne["manufacturer_name"] = _constructeur(con, ligne["manufacturer_name"])
        return ligne

    # Plusieurs factions portent le même nom, et l'une d'elles est souvent une
    # coquille vide : deux « XenoThreat » existent, l'une sans description ni
    # état civil, et c'était elle que le premier venu ramenait. Même piège que
    # les deux « Pyro Gateway » du starmap — on départage sur la **richesse**
    # de la fiche, jamais sur l'ordre d'insertion.
    mieux = ("ORDER BY (description IS NOT NULL AND TRIM(description) <> '') DESC, "
             "(headquarters IS NOT NULL) DESC, LENGTH(COALESCE(description, '')) DESC")
    ligne = _dict(_row(con, f"SELECT * FROM factions WHERE name = ? {mieux} LIMIT 1",
                       entity_id))
    if ligne is not None:
        return ligne
    ligne = _dict(_row(
        con, f"SELECT * FROM factions WHERE name LIKE ? || '%' {mieux}, "
             "LENGTH(name) LIMIT 1", entity_id))
    if ligne is not None:
        return ligne
    # **Un surnom au milieu du nom échappe au préfixe.** Les contrats disent
    # « Tecia Pacheco », la fiche s'appelle « Tecia "Twitch" Pacheco » : ni
    # l'égalité ni le préfixe ne la trouvent, et « c'est qui Tecia Pacheco »
    # rendait NotFound sur un personnage qu'on connaît — trouvé par le
    # balayage. On rattrape en laissant un trou entre chaque mot du nom.
    motif = "%".join(entity_id.split())
    return _dict(_row(
        con, f"SELECT * FROM factions WHERE name LIKE ? {mieux}, "
             "LENGTH(name) LIMIT 1", f"{motif}%"))


_LIEN_WIKI_TRADUIT = {"ship": "wiki_vaisseaux", "item": "wiki_objets"}

# Le jeu note ainsi les cases qu'il ne remplit pas. Les afficher revient à
# annoncer une information qu'on n'a pas.
_NON_RENSEIGNE = {"n/a", "na", "none", "unknown", "tbd", "-", "?"}


def _description_francaise(con: sqlite3.Connection, entity_type: str,
                           uuid: str | None) -> tuple[str, str] | None:
    """La description en français, et **d'où elle vient**.

    Les fichiers du jeu ne parlent qu'anglais : `labels.json` n'a pas de
    français, et servir la version originale à un joueur francophone était le
    défaut le plus visible de `decrire`. Deux sources le comblent, et elles
    sont complémentaires plutôt que concurrentes :

    * **CircusPES** traduit le `global.ini` du jeu, donc le même espace de
      clés que `labels.json`. C'est la **seule** à couvrir les lieux (2 013 sur
      2 032) et les missions (2 119) ; elle prend 3 610 objets et 237
      vaisseaux.
    * **le wiki** couvre plus d'objets (7 149) et de vaisseaux (278), mais
      **aucun** lieu ni mission.

    CircusPES passe devant là où les deux répondent : traduction humaine des
    chaînes réelles du jeu contre traduction plus littérale. Comparé côte à
    côte — « les moindres recoins » contre « chaque centimètre ».

    Les tables peuvent manquer, si `disco trad` ou `disco wiki` n'ont jamais
    tourné. Ce n'est pas une erreur : on rend l'anglais, comme avant.
    """
    if not uuid:
        return None
    try:
        ligne = _row(con, "SELECT description_fr FROM traductions "
                          "WHERE entity_type = ? AND uuid = ?", entity_type, uuid)
        if ligne and (ligne["description_fr"] or "").strip():
            return ligne["description_fr"].strip(), "circuspes"
    except sqlite3.OperationalError:
        pass

    lien = _LIEN_WIKI_TRADUIT.get(entity_type)
    if not lien:
        return None
    ligne = ligne_source(con, lien, uuid)
    if ligne and (ligne["description_fr"] or "").strip():
        return ligne["description_fr"].strip(), "wiki"
    return None


def _complexes_du_contrat(con: sqlite3.Connection, debug_name: str | None,
                          salles: list[str]) -> dict[str, Any] | None:
    """Les complexes où se joue une mission dont le lieu n'est qu'une salle.

    **Le lien est en base, il n'a jamais été une règle de terrain.** Je
    l'avais d'abord codé en dur en concluant, à tort, qu'il n'existait pas :
    aucun contrat ne nomme d'Onyx Facility, c'est vrai, mais `labels.json`
    nomme le site et ses salles dans une même famille de clés —
    `FacilityDelve_WingA_name` pour « Engineering Wing »,
    `FacilityDelve_Stanton4a_name` pour « Onyx Facility ». La famille est le
    préfixe du `DebugName` du contrat.

    Les avant-postes eux-mêmes, et les corps qui les portent, viennent du
    starmap : c'est lui qui dit qu'il y en a 120, sur douze lunes.
    """
    if not debug_name or not salles:
        return None
    familles = [r[0] for r in con.execute(
        "SELECT DISTINCT famille FROM mission_sites WHERE est_salle = 1 "
        "AND nom IN (%s)" % ",".join("?" * len(salles)), salles)]
    familles = [f for f in familles if f.lower() in debug_name.lower()]
    for famille in familles:
        sites = [r[0] for r in con.execute(
            "SELECT nom FROM mission_sites "
            "WHERE famille = ? AND est_salle = 0 ORDER BY nom", (famille,))]
        if not sites:
            continue
        prefixe = sites[0]
        lignes = [dict(r) for r in con.execute(
            "SELECT name nom, path chemin FROM starmap "
            "WHERE name LIKE ? ORDER BY name", (prefixe + "%",))]
        if not lignes:
            continue
        # On remonte à la **planète**, pas à la lune : les 120 complexes sont
        # sur douze lunes, qu'on ne peut citer sans tronquer, mais sur quatre
        # planètes seulement — la liste tient alors en entier, ce qui situe
        # mieux qu'une moitié de liste de lunes.
        planetes: list[str] = []
        lunes: set[str] = set()
        for ligne in lignes:
            morceaux = [m.strip() for m in (ligne["chemin"] or "").split("/")
                        if m.strip()]
            if len(morceaux) >= 4:
                lunes.add(morceaux[-2])
                if morceaux[-3] not in planetes:
                    planetes.append(morceaux[-3])
        return {"famille": prefixe, "total": len(lignes),
                "porteurs": sorted(planetes), "lunes": len(lunes)}
    return None


# Le compte est **global** — « combien de lieux de ce rôle chaque système
# compte, tous contrats confondus » — donc identique pour les 4 097 contrats.
# On le recalculait à chaque fiche : un balayage de `contract_locations`
# (1,4 million de lignes) joint au starmap, **292 ms**, deux fois par mission.
# Mesuré : 845 ms pour décrire une mission, dont 700 ici. Le cache ramène la
# fiche sous les 10 ms.
#
# La clé porte le **fichier de base** : les tests montent des bases
# différentes, et une mémoire indexée sur le seul rôle les mélangerait. La
# réingestion, elle, bascule par renommage et redémarre le processus — il n'y
# a donc pas d'invalidation à prévoir en cours de route.
_COUVERTURES: dict[tuple[str, str], dict[str, int]] = {}


def _base_de(con: sqlite3.Connection) -> str:
    for _, nom, fichier in con.execute("PRAGMA database_list"):
        if nom == "main":
            return fichier or ":memory:"
    return "?"


def _couverture_par_systeme(con: sqlite3.Connection,
                            role: str) -> dict[str, int]:
    """Combien de lieux d'un rôle chaque système compte, tous contrats confondus.

    C'est l'étalon qui manquait pour dire « partout ». Un compte brut ne le
    dit pas : 34 lieux, c'est beaucoup dans l'absolu et c'est **tout Stanton**,
    qui en compte 35 ; les mêmes 34 dans Pyro, qui en compte 106, ne seraient
    qu'un tiers.
    """
    cle = (_base_de(con), role)
    if cle not in _COUVERTURES:
        _COUVERTURES[cle] = {r[0]: r[1] for r in con.execute(
            "SELECT sm.system_name, COUNT(DISTINCT cl.location_name) "
            "FROM contract_locations cl "
            "JOIN starmap sm ON sm.name = cl.location_name "
            "WHERE cl.role = ? AND sm.system_name IS NOT NULL "
            "  AND sm.type_name NOT IN ('SolarSystem', 'Star') "
            "GROUP BY 1", (role,)) if r[1]}
    return _COUVERTURES[cle]


def _lieux_manquants(con: sqlite3.Connection, role: str, systeme: str,
                     presents: set[str]) -> list[str]:
    """Ce qui manque à une couverture presque totale.

    « Tu peux dire partout dans Stanton **sauf** » — demande de
    l'utilisateur : nommer les trous vaut mieux qu'arrondir à « partout »,
    parce que c'est là qu'un joueur perd son temps.

    Le **système lui-même** ne compte pas : le starmap porte une entrée
    « Stanton » de type `SolarSystem`, et sans cette exclusion la mission du
    Zenith sortait « partout dans Stanton sauf Stanton ».
    """
    return sorted(r[0] for r in con.execute(
        "SELECT DISTINCT cl.location_name FROM contract_locations cl "
        "JOIN starmap sm ON sm.name = cl.location_name "
        "WHERE cl.role = ? AND sm.system_name = ? "
        "  AND sm.type_name NOT IN ('SolarSystem', 'Star')", (role, systeme))
        if r[0] not in presents)


def _systemes_couverts(con: sqlite3.Connection, lignes: list[dict[str, Any]],
                       role: str) -> list[str]:
    """Les systèmes que cette liste de lieux couvre **en entier**.

    Remarque de l'utilisateur : « c'est dans l'intégralité de Stanton qu'on
    peut la prendre donc pas besoin de tout mettre ». Énumérer 34 avant-postes
    quand la réponse tient en deux mots est illisible, et pire, ça cache la
    réponse — le joueur cherche une contrainte là où il n'y en a aucune.

    Mesuré sur la base : la distribution est franchement bimodale, **1 737
    couples (contrat, système) couvrent 100 %** des lieux connus de leur
    système, contre une poignée entre 40 et 80 %. Le seuil à 0,8 tombe donc
    dans un creux, il ne coupe pas une population en deux.
    """
    total = _couverture_par_systeme(con, role)
    par_systeme: dict[str, int] = {}
    for ligne in lignes:
        if ligne["systeme"] and ligne["genre"] not in ("SolarSystem", "Star"):
            par_systeme[ligne["systeme"]] = par_systeme.get(
                ligne["systeme"], 0) + 1
    return sorted(nom for nom, compte in par_systeme.items()
                  if total.get(nom) and compte / total[nom] >= 0.8)


def _vecu_de_mission(con: sqlite3.Connection, uuid: str):
    """Ce que nos parties disent de cette mission, ou None.

    **Import différé et échec silencieux voulu** : la base de guilde peut
    être absente (Chris public sans compagnon), verrouillée ou vide, et
    une fiche de mission doit répondre dans tous ces cas. Le §2 interdit
    au cœur de dépendre d'un frontend.
    """
    try:
        from .guilde.observations import reussite_observee

        for ligne in reussite_observee(con).missions:
            if ligne.contrat == uuid:
                return ligne
    except Exception:  # noqa: BLE001  (base absente, verrouillée, vide)
        return None
    return None


def _fiche_de_mission(con: sqlite3.Connection,
                      uuid: str) -> dict[str, Any] | None:
    """Le concret d'une mission : où la prendre, où ça se passe, ce qu'on y fait.

    `decrire` ne rendait que le briefing — le texte d'ambiance que le
    commanditaire écrit. C'est agréable et ça ne dit ni où trouver la mission
    ni ce qu'elle demande. Les trois sont en base :

    * **où la prendre** — `contract_locations` en rôle `availability`,
      53 382 lignes nommées ;
    * **où ça se passe** — le même en rôle `mission`, 1 073 208 ;
    * **ce qu'on y fait** — `contract_objectives`, 2 520 contrats sur 5 108.

    Les objectifs ne sont **pas des phrases** : le jeu ne stocke qu'un nom de
    debug et un type de gestionnaire. Le rendu doit le dire plutôt que de faire
    passer un identifiant pour une consigne.
    """
    def lieux(role: str) -> dict[str, Any]:
        """Les lieux d'un rôle, **résumés par système** quand ils sont nombreux.

        « The Price of Freedom » se prend dans une trentaine d'endroits, soit
        tout Stanton : les énumérer est exact et illisible. On rend donc le
        compte, les systèmes concernés, et quelques noms.
        """
        # **La jointure se fait sur le nom, pas sur l'UUID.** Les lieux de
        # mission ne portent pas d'identifiant starmap : par UUID, le chemin
        # sortait vide et la réponse citait « Hickes Research Outpost » sans
        # dire ni le système ni la planète. Remarque du journal.
        lignes = [dict(r) for r in con.execute(
            # Le nom français d'abord : ces lieux sont des périphrases —
            # « la clinique dans Megumi Refueling » — et le Cirque Lisoir les
            # traduit toutes. `COALESCE` plutôt qu'un test, pour que les noms
            # propres, qui n'ont pas de traduction, passent inchangés.
            "SELECT DISTINCT COALESCE(cl.location_name_fr, cl.location_name) nom, "
            "       cl.location_name nom_vo, "
            "       sm.system_name systeme, "
            "       sm.path chemin, sm.type_name genre "
            "FROM contract_locations cl "
            "LEFT JOIN starmap sm ON sm.name = cl.location_name "
            "WHERE cl.contract_uuid = ? AND cl.role = ? "
            "  AND cl.location_name IS NOT NULL "
            "ORDER BY cl.location_name", (uuid, role))]
        # **Un lieu de mission porte les mêmes jetons qu'un titre.** Mesuré au
        # balayage : 82 705 lignes de `contract_locations` valent « Remote
        # Outpost near ~mission(NearbyLocation) », et 489 missions l'affichaient
        # tel quel. Les objectifs avaient leur nettoyage depuis longtemps
        # (`_consigne`), les lieux non — et c'est le même gabarit.
        for ligne_lieu in lignes:
            ligne_lieu["nom"] = _lieu_lisible(ligne_lieu["nom"])
        systemes = sorted({l["systeme"] for l in lignes if l["systeme"]})
        partout = _systemes_couverts(con, lignes, role)
        presents = {l["nom"] for l in lignes}
        manquants = {s: _lieux_manquants(con, role, s, presents)
                     for s in partout}
        # **Le point commun qui raccourcit une liste longue.** Demande de
        # l'utilisateur pour les cas non couvrants : trente-six noms ne se
        # lisent pas, « 8 planètes, 20 astéroïdes, 2 avant-postes » se lit et
        # dit davantage. Le type est en base, il n'y avait qu'à grouper.
        #
        # Mais **seulement au-delà de ce qui se lit**. En deçà, R8 s'applique
        # : un lieu cité doit être situé, et « 3 planètes » ne situe rien. Le
        # seuil est celui de l'ancienne troncature — huit lignes.
        familles: dict[str, dict[str, int]] = {}
        for ligne in (lignes if len(lignes) > 8 else []):
            # Le système lui-même n'est pas un lieu de prise : le starmap
            # porte une entrée « Pyro System » de type `SolarSystem`, qui
            # sortait « dans Pyro System : 1 système ».
            if (ligne["systeme"] and ligne["systeme"] not in partout
                    and ligne["genre"] not in ("SolarSystem", "Star")):
                genre = ligne["genre"] or "?"
                familles.setdefault(ligne["systeme"], {})
                familles[ligne["systeme"]][genre] = (
                    familles[ligne["systeme"]].get(genre, 0) + 1)
        return {"total": len(lignes), "systemes": systemes,
                "lieux": lignes[:8], "partout": partout,
                "manquants": {s: m for s, m in manquants.items() if m},
                "familles": familles}

    ou_prendre, ou_faire = lieux("availability"), lieux("mission")
    objectifs = [dict(r) for r in con.execute(
        "SELECT debug_name, handler, "
        "       COALESCE(texte_fr, texte_en) consigne, "
        "       texte_fr IS NOT NULL en_francais "
        "FROM contract_objectives "
        "WHERE contract_uuid = ? ORDER BY position", (uuid,))]
    # **Les prérequis, que rien ne montrait.** « Y a-t-il des prérequis ? »
    # répondait « Tu veux dire Built on S71 rifle ? » — remarque du journal.
    # Deux choses les composent : le rang exigé chez le commanditaire, et les
    # missions à avoir faites avant.
    fiche = _dict(_row(con, "SELECT title, mission_giver, min_standing_name, "
                            "faction_name, debug_name, "
                            "diff_connaissance, diff_pilotage, diff_charge, "
                            "diff_risque "
                            "FROM contracts WHERE uuid = ?", uuid))
    # « Engineering Wing » n'est pas un lieu de la carte, c'est une salle : le
    # rendu doit dire dans quels complexes on la trouve.
    ou_faire["complexes"] = _complexes_du_contrat(
        con, (fiche or {}).get("debug_name"),
        # **Le nom d'origine, pas le traduit.** `mission_sites` indexe les
        # salles sous leur libellé anglais — « Engineering Wing » — et lui
        # passer « Section Ingénierie » faisait disparaître le rattachement
        # aux complexes Onyx sans un mot. Traduire une valeur qui sert de
        # clé ailleurs casse la clé.
        [l.get("nom_vo") or l["nom"] for l in ou_faire["lieux"]
         if l.get("nom_vo") or l.get("nom")])
    # Le même calcul que `get_mission_reputation`, et pour la même raison : le
    # lien s'écrit dans les deux sens.
    # Import différé pour la même raison qu'ailleurs : `queries` réimporte ce
    # module. Une fiche de mission a besoin de la chaîne, la chaîne n'a besoin
    # d'aucune fiche.
    from .queries import _chaine_de_missions

    chaine = _chaine_de_missions(con, dict(fiche or {}, uuid=uuid))
    # **La chaîne cite des titres, donc elle porte les mêmes gabarits.** Le
    # balayage a trouvé 397 missions listant « <= UNINITIALIZED => » parmi ce
    # qu'elles débloquent : le filtre posé sur le titre *décrit* ne valait pas
    # pour les titres *cités*. Une étape sans nom lisible n'apprend rien au
    # joueur — on la retire plutôt que d'afficher un identifiant technique.
    prealables = _etapes_lisibles(chaine["prealables"])
    debloque = _etapes_lisibles(chaine["debloque"])

    if not (ou_prendre["total"] or ou_faire["total"] or objectifs
            or prealables or (fiche or {}).get("min_standing_name")):
        return None
    return {"ou_prendre": ou_prendre, "ou_faire": ou_faire,
            "objectifs": objectifs, "prealables": prealables,
            "debloque": debloque,
            "difficulte": _difficulte(fiche),
            "rang": (fiche or {}).get("min_standing_name"),
            "chez": (fiche or {}).get("mission_giver")
                    or (fiche or {}).get("faction_name")}


# Les quatre axes de difficulté, dans l'ordre où ils intéressent le joueur :
# ce qu'il risque d'abord, ce qu'il doit savoir ensuite.
_AXES_DIFFICULTE = (
    ("diff_risque", "risque"),
    ("diff_charge", "charge mentale"),
    ("diff_pilotage", "pilotage"),
    ("diff_connaissance", "connaissance du jeu"),
)

# **Le libellé porte son propre rang dans son suffixe** — aucune table de
# correspondance à tenir, seulement une traduction. Deux d'entre eux répondent
# littéralement à « est-ce que je peux la faire seul ? », et c'est la question
# que `shareable` ne savait pas traiter : il dit si la mission se **partage**,
# pas si elle l'exige.
_DIFFICULTE_FR = {
    "diff_risque": {
        1: "aucun risque", 2: "à peine de quoi transpirer",
        3: "danger au sol, pas pour le vaisseau",
        4: "le vaisseau peut être endommagé, la cargaison perdue",
        5: "tu peux mourir, le vaisseau peut exploser",
        6: "tu vas probablement mourir, le vaisseau aussi",
        7: "sans aide, toi et le vaisseau y passez",
    },
    "diff_charge": {
        1: "on peut jouer les mains dans les poches", 2: "presque aucune réflexion",
        3: "travail de routine", 4: "des moments de concentration",
        5: "comme faire tourner dix assiettes à la fois",
        6: "très difficile à gérer seul",
        7: "complexité folle — **pas faisable en solo**",
    },
    "diff_pilotage": {
        1: "sans les mains", 2: "aucun risque à l'action",
        3: "action PvE facile", 4: "action PvE normale",
        5: "PvE difficile ou PvP facile", 6: "PvE à plusieurs ou PvP d'expert",
        7: "PvE et PvP en grand groupe, type warzone",
    },
    "diff_connaissance": {
        1: "débutant complet ou tutoriel", 2: "les bases au sol",
        3: "les bases du vol : décoller, s'amarrer, sauter",
        4: "compréhension standard du jeu",
        5: "compréhension experte", 6: "tactiques optimales",
        7: "niveau développeur",
    },
}


def _difficulte(fiche: dict | None) -> dict[str, Any] | None:
    """Les quatre axes de difficulté, traduits, avec leur rang.

    Ingérés le 2026-08-06 : 2 346 contrats, sept niveaux **tous peuplés** sur
    chaque axe. `difficulty_profile`, déjà en base, ne disait pas la même
    chose — General, Logistics, Discovery sont des **catégories**, et la
    colonne est NULL sur 2 763 contrats.
    """
    if not fiche:
        return None
    axes, solo = [], None
    for colonne, libelle in _AXES_DIFFICULTE:
        brut = fiche.get(colonne)
        if not brut:
            continue
        # Le rang est le dernier segment du libellé — c'est le jeu qui l'écrit.
        rang = brut.rsplit("_", 1)[-1]
        if not rang.isdigit():
            continue
        rang = int(rang)
        axes.append({"axe": libelle, "rang": rang,
                     "texte": _DIFFICULTE_FR.get(colonne, {}).get(rang, "")})
        if colonne == "diff_charge":
            # Les deux seuls libellés qui parlent explicitement de solitude.
            solo = False if rang >= 6 else True
    if not axes:
        return None
    return {"axes": axes, "solo": solo,
            "pire": max(a["rang"] for a in axes)}


def _prix_argent_reel(con: sqlite3.Connection,
                      uuid: str | None) -> dict[str, Any] | None:
    """Le prix au pledge store, en dollars. 229 vaisseaux sur 295.

    `get_price` savait dire « il s'obtient à l'achat en argent réel » sans
    jamais donner le montant, faute de l'avoir : les fichiers du jeu ne
    portent aucun prix depuis la 3.20.
    """
    if not uuid:
        return None
    ligne = ligne_source(con, "wiki_vaisseaux", uuid)
    if ligne is None or ligne.get("msrp") is None:
        return None
    return {"dollars": ligne["msrp"], "url": ligne["pledge_url"]}


# Ce qui trahit un titre de gabarit plutôt qu'un nom de mission. Mesuré :
# **1 138 contrats sur 5 105** portent un titre que le serveur remplace à la
# génération, et on les rendait tels quels — « [Contractor|BountyTitle] » ou
# « <= UNINITIALIZED => » présentés comme des noms de mission.
_TITRE_MORT = re.compile(r"^\s*(<=|~mission\()|UNINITIALIZED|PLACEHOLDER",
                         re.IGNORECASE)


def _titre_utilisable(titre: str | None) -> bool:
    """Ce titre nomme-t-il vraiment quelque chose que le joueur peut lire ?

    Un contrat dont le titre n'est **qu'un** jeton n'a pas de nom : le serveur
    le remplace à la génération, et nous n'avons que le gabarit. Le proposer
    comme réponse revient à donner un identifiant technique pour un titre.
    C'est la même règle que « un jeton de gabarit n'est pas une organisation ».
    """
    from .render import speakable_title

    if not titre or _TITRE_MORT.search(titre):
        return False
    return bool(speakable_title(titre).strip())


def _etapes_lisibles(etapes):
    """Les étapes d'une chaîne dont le titre veut dire quelque chose.

    Les entrées sont tantôt des chaînes, tantôt des lignes portant `title` :
    on traite les deux plutôt que d'imposer une forme aux appelants.
    """
    gardees = []
    for etape in etapes or []:
        titre = etape if isinstance(etape, str) else (etape or {}).get("title")
        if not _titre_utilisable(titre):
            continue
        propre = _titre_affichable(titre)
        gardees.append(propre if isinstance(etape, str)
                       else {**etape, "title": propre})
    return gardees


def _lieu_lisible(nom: str | None) -> str:
    """Un nom de lieu de mission, jetons de gabarit retirés.

    « Remote Outpost near ~mission(NearbyLocation) » devient « Remote Outpost » :
    le serveur choisit le lieu voisin à la génération, nous n'avons que le
    gabarit. On coupe aussi la préposition que le jeton laisse orpheline —
    « near », « at » — sans quoi la phrase finit sur un vide.
    """
    if not nom:
        return nom or ""
    propre = re.sub(r"~mission\([^)]*\)s?|\[[^\[\]]*\]s?", "", nom)
    propre = re.sub(r"\s+(?:near|at|in|on|from|de|du|des|à|au)\s*$", "",
                    propre.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", propre).strip(" -–—:,") or nom


def _titre_affichable(titre: str | None) -> str:
    """Le titre tel qu'un joueur peut le lire, jetons retirés."""
    from .render import speakable_title

    return speakable_title(titre).strip() or (titre or "")


def _a_dire(con: sqlite3.Connection, entity_type: str, ligne: dict) -> bool:
    """A-t-on **quelque chose à dire** de cette entité — pas seulement un texte ?

    Le critère était « une description non vide », et il départageait les
    candidats. Il datait de l'époque où `decrire` ne savait rendre que de la
    prose. Depuis qu'une mission rend ses prérequis et un système ses planètes,
    il écarte des candidats qui répondraient très bien.

    Mesuré : « décris-moi la mission Assist People's Alliance Vessel
    Tranquility » sortait **ARGO MOLE Alliance**, un vaisseau. Le contrat est
    pourtant premier dans l'ordre des types et résout à 95 contre 85,5 — mais
    il n'a pas de briefing, donc il était réputé muet, et le vaisseau décrit
    passait devant. Répondre par un vaisseau à une question qui dit « la
    mission » est le pire cas : c'est faux, et ça a l'air juste.
    """
    if (ligne.get("description") or "").strip():
        return True
    if entity_type == "contract":
        return _fiche_de_mission(con, ligne["uuid"]) is not None
    if entity_type == "starmap":
        return _fiche_de_lieu(con, ligne) is not None
    return False


def _services_du_lieu(con: sqlite3.Connection, uuid: str | None) -> list[str]:
    """Ce qu'on trouve sur place — 281 lieux, 22 services.

    Ingéré le 2026-08-06 : le mot `Amenities` n'apparaissait nulle part dans le
    projet alors que les 281 lieux étaient tous déjà dans `starmap`. C'est ce
    qui répond à « je peux poser mon Hammerhead ici ? » — les tailles de hangar
    et de plateforme y sont — et à « où réparer ? ».

    On rend le **libellé d'affichage** : le jeu écrit « Hangar L » d'un côté et
    « Hangar (L) » de l'autre, et c'est la seconde forme qu'il montre.
    """
    if not uuid:
        return []
    try:
        return [r[0] for r in con.execute(
            "SELECT display_name FROM location_amenities "
            " WHERE location_uuid = ? ORDER BY display_name", (uuid,))]
    except sqlite3.OperationalError:
        # La table peut manquer sur une base d'avant l'audit : ce n'est pas une
        # erreur, c'est simplement une fiche sans services.
        return []


def _fiche_de_lieu(con: sqlite3.Connection, ligne: dict) -> dict[str, Any] | None:
    """Ce qu'on sait d'un lieu quand le jeu n'en dit rien.

    L'étoile *Nyx* n'a **aucun** texte officiel — 0 caractère — et « nyx »
    répondait « je n'ai pas de description ». C'était exact et inutile : on
    connaît ses trois planètes, ses points de saut, son affiliation, et le fait
    qu'elle est sans loi. Une absence de prose n'est pas une absence de savoir.

    Deux niveaux, parce que la question n'est pas la même : pour un **système**
    on décrit ce qu'il contient, pour un **lieu** on décrit où il se trouve.
    """
    systeme = (ligne.get("type_name") or "") in ("Star", "SolarSystem")
    nom = ligne.get("name")
    fiche: dict[str, Any] = {"systeme": systeme}

    if not systeme:
        return None

    # **Le starmap nomme chaque système deux fois.** « Pyro » (une `Star`) et
    # « Pyro System » (un `SolarSystem`) désignent le même endroit, mais les
    # enfants ne sont rattachés qu'au premier : la fiche de « Pyro System »
    # sortait vide. On retombe sur le nom court, qui est celui que portent les
    # lignes filles.
    if nom and nom.endswith(" System"):
        court = nom[: -len(" System")]
        if con.execute("SELECT 1 FROM starmap WHERE system_name = ? LIMIT 1",
                       (court,)).fetchone():
            nom = court

    # Le wiki décrit les systèmes que les fichiers du jeu laissent muets —
    # c'est exactement la valeur annoncée au §4 : il complète, il ne double pas.
    wiki = ligne_source(con, "wiki_systemes", nom)
    if wiki:
        fiche["affiliation"] = wiki["affiliation"]
        fiche["description_wiki"] = (wiki["description"] or "").strip() or None

    # « Sans loi » est mesuré, pas codé en dur : le wiki publie l'affiliation,
    # et `Unclaimed` désigne Pyro et Nyx.
    from .queries import systemes_risques

    fiche["sans_loi"] = normalize(nom or "") in systemes_risques(con)

    corps = con.execute(
        "SELECT name, type_name, parent_name FROM starmap "
        "WHERE system_name = ? AND type_name IN ('Planet', 'Moon') "
        "ORDER BY type_name DESC, name", (nom,)).fetchall()
    fiche["planetes"] = [r["name"] for r in corps if r["type_name"] == "Planet"]
    fiche["lunes"] = [r["name"] for r in corps if r["type_name"] == "Moon"]

    # Les villes : ce sont les zones d'atterrissage, seuls endroits où l'on
    # sort du vaisseau à pied dans une vraie ville.
    fiche["villes"] = [
        (r["name"], r["parent_name"]) for r in con.execute(
            "SELECT name, parent_name FROM starmap WHERE system_name = ? "
            "AND type_name = 'LandingZone' ORDER BY name", (nom,))]

    # Les points de saut portent le nom de leurs deux extrémités : « Nyx -
    # Pyro Jump Point ». On ne garde que la destination, et seulement les
    # systèmes qu'on connaît vraiment — le starmap en nomme vers des systèmes
    # qui ne sont pas ingérés.
    connus = {r[0] for r in con.execute(
        "SELECT DISTINCT system_name FROM starmap WHERE system_name IS NOT NULL")}
    sauts = []
    for r in con.execute(
        "SELECT name FROM starmap WHERE system_name = ? "
        "AND name LIKE '%Jump Point%' ORDER BY name", (nom,)
    ):
        entete = re.split(r"\s*Jump Point", r["name"])[0]
        cibles = [m.strip() for m in re.split(r"\s*-\s*", entete) if m.strip()]
        for cible in cibles:
            if cible.lower() != (nom or "").lower() and cible not in sauts:
                sauts.append(cible)
    fiche["sauts"] = [(s, s in connus) for s in sauts]

    # **Les stations manquaient, et ce sont elles qu'un joueur nomme.** Rejeu
    # du journal du 2026-08-21 : « cite-moi les lieux de Stanton » rendait
    # planètes, lunes, villes, sauts et le compte d'avant-postes — soit tout
    # sauf Baijini Point, Everus Harbor, Grim HEX et les stations de Lagrange.
    # 54 dans Stanton, 31 dans Nyx, 25 dans Pyro : c'est là qu'on s'amarre,
    # qu'on achète et qu'on raffine.
    #
    # `Manmade` et `Manmade_VisibleOnInteraction` sont le même genre de lieu —
    # le suffixe dit seulement quand la carte l'affiche, pas ce que c'est.
    fiche["stations"] = [r[0] for r in con.execute(
        "SELECT name FROM starmap WHERE system_name = ? "
        "AND type_name LIKE 'Manmade%' AND name IS NOT NULL "
        "ORDER BY name", (nom,))]

    fiche["avant_postes"] = con.execute(
        "SELECT COUNT(*) FROM starmap WHERE system_name = ? "
        "AND type_name = 'Outpost'", (nom,)).fetchone()[0]
    return fiche


def decrire(con: sqlite3.Connection, query: str,
            *, prefere: str | None = None,
            volet: str | None = None,
            type_prefere: str | None = None) -> dict[str, Any]:
    """« C'est quoi Grim HEX ? », « c'est qui Wikelo ? »

    Décrire n'est pas donner les statistiques : le joueur qui demande « c'est
    quoi » veut savoir **ce que c'est**, pas combien ça pèse. Le texte existe
    et il est officiel — CIG l'écrit dans les fichiers qu'on ingère déjà :
    7 780 objets, 315 vaisseaux sur 316, 2 032 lieux sur 2 054, et les
    factions, qui portent aussi les personnages.

    « Qui » et « quoi » ne cherchent pas au même endroit. Wikelo est à la fois
    une personne et trois stations ; c'est le pronom qui tranche, et à défaut
    le meilleur score.
    """
    ordre = list(_SOURCES_DESCRIPTION)
    if prefere == "personne":
        ordre.sort(key=lambda s: s[2] != "personne")
    elif prefere == "chose":
        ordre.sort(key=lambda s: s[2] == "personne")

    candidats = []
    for entity_type, table, genre in ordre:
        res = resolve(con, query, entity_types=(entity_type,), limit=1)
        if res.best is None or res.best.score < 85.0:
            continue
        ligne = _ligne_decrite(con, entity_type, table, res.best.entity_id)
        if ligne is None:
            continue
        # Un candidat sans nom lisible n'en est pas un : on n'aurait que le
        # gabarit que le serveur remplacera, pas un nom. Le contrôle vaut pour
        # **tous** les types — mesuré, « <= UNINITIALIZED => » est indexé comme
        # une *organisation*, pas comme un contrat, et un filtre limité aux
        # contrats l'aurait laissé passer.
        if not _titre_utilisable(ligne.get("title") or ligne.get("name")):
            continue
        candidats.append({
            "genre": genre, "entity_type": entity_type,
            "ligne": ligne, "score": res.best.score, "resolution": res,
            "decrit": bool((ligne.get("description") or "").strip()),
            # Ce qu'on saurait dire **sans** prose : les prérequis d'une
            # mission, les planètes d'un système.
            "fiche": _a_dire(con, entity_type, ligne),
        })

    # **Une fiche sans prose ne l'emporte que si le nom colle strictement
    # mieux.** Avoir des données structurées est une preuve plus faible
    # qu'avoir un texte : il faut que la correspondance la soutienne.
    #
    # Mesuré sur les deux cas qui s'opposent : « assist people alliance
    # tranquility » sort le contrat à **95** contre un vaisseau décrit à 85,5
    # — la mission gagne, et répondre *ARGO MOLE Alliance* était absurde. Mais
    # « Wikelo » sort un contrat à **93** et la station à **93** : à égalité,
    # la prose et l'ordre des types tranchent, et « c'est quoi Wikelo » doit
    # rendre la station.
    meilleur_decrit = max((c["score"] for c in candidats if c["decrit"]),
                          default=0.0)
    for candidat in candidats:
        if candidat["fiche"] and candidat["score"] > meilleur_decrit:
            candidat["decrit"] = True

    # **L'ordre des types prime, sauf sur une correspondance exacte.**
    #
    # Deux tentatives d'arbitrage au score ont été annulées ici : elles
    # faisaient gagner la *planète Crusader* sur « c'est quoi Crusader
    # Security », et le bibelot « Gladius Model » sur le vaisseau. Le score ne
    # dit pas quelle **sorte** de chose on cherche.
    #
    # Un **100** est autre chose qu'un score élevé : il signifie que le terme
    # tapé *est* le nom de l'entité, sans un mot inexpliqué. Mesuré — « kastak
    # arms » sort le constructeur à 100 et un objet à 85,5 ; sans cette règle,
    # le constructeur perdait contre un contrat homonyme. Et elle ne déplace
    # rien d'autre : « gladius » ne sort aucun 100 (vaisseau 90, objet 93),
    # donc l'ordre des types tranche comme avant.
    # **`prefere` passe devant tout**, y compris devant une correspondance
    # exacte : « qui » et « quoi » ne cherchent pas au même endroit, et Wikelo
    # est à la fois une personne et trois stations. Sans cette priorité, la
    # fiche de la personne — exacte à 100 — répondait aussi à « c'est quoi ».
    groupes = [candidats]
    if type_prefere:
        vises = [c for c in candidats if c["entity_type"] == type_prefere]
        groupes = [vises, [c for c in candidats if c not in vises]]
    elif prefere:
        vises = [c for c in candidats if (c["genre"] == "personne") == (prefere == "personne")]
        groupes = [vises, [c for c in candidats if c not in vises]]

    # **Un 100 sans description bat quand même un autre type décrit.** Mesuré :
    # l'étoile *Nyx* n'a aucun texte officiel (0 caractère), donc « nyx »
    # sautait au premier candidat décrit — le contrat « ENTRY LVL. COURIER
    # NEEDED IN NYX », à 85 par l'étage `token`. Répondre par une mission de
    # courrier à quelqu'un qui tape le nom d'un système est faux quoi qu'on ait
    # à dire, et n'avoir aucune prose sur Nyx ne fait pas de Nyx autre chose
    # que Nyx. Le rendu sait très bien dire « Nyx est un système » sans texte.
    trouve = None
    for groupe in groupes:
        trouve = next(
            (c for c in groupe if c["decrit"] and c["score"] >= 100.0),
            next((c for c in groupe if c["score"] >= 100.0),
                 next((c for c in groupe if c["decrit"]),
                      groupe[0] if groupe else None)))
        if trouve is not None:
            break

    if trouve is None:
        raise NotFound(query)

    ligne = trouve["ligne"]
    # Ce qu'on peut proposer ensuite : des statistiques n'existent que pour un
    # objet ou un vaisseau, et seulement s'il y en a vraiment.
    stats = None
    if trouve["entity_type"] == "ship":
        stats = "ship"
    elif trouve["entity_type"] == "item":
        if _row(con, "SELECT 1 FROM item_stats WHERE item_uuid = ? "
                     "AND (dps IS NOT NULL OR ammo_capacity IS NOT NULL "
                     "     OR shield_health IS NOT NULL)", ligne["uuid"]):
            stats = "item"

    # L'état civil d'une organisation : QG, fondation, direction, secteur.
    # C'est ce que « c'est qui les Headhunters » attend vraiment — la
    # description dit ce qu'ils font, pas où ils sont ni qui les dirige.
    # Le jeu remplit les cases qu'il ne connaît pas avec « N/A » plutôt que de
    # les laisser vides : « Siège : N/A » est pire qu'une ligne absente.
    etat_civil = {cle: ligne[cle] for cle in
                  ("headquarters", "founded", "leadership", "area", "focus")
                  if (ligne.get(cle) or "").strip()
                  and ligne[cle].strip().lower() not in _NON_RENSEIGNE} or None

    pratique = (_fiche_de_mission(con, ligne["uuid"])
                if trouve["entity_type"] == "contract" else None)
    # **Ce que nos parties en disent.** Le catalogue donne la difficulté
    # que CIG publie ; l'observation dit ce qu'on en a fait — et surtout
    # combien de fois on l'a **abandonnée**, le seul axe qui sépare
    # vraiment (94 réussies contre 2 échouées, mesuré). Absence de base de
    # guilde = absence de mention, jamais une panne.
    vecu = (_vecu_de_mission(con, ligne["uuid"])
            if trouve["entity_type"] == "contract" else None)
    geographie = (_fiche_de_lieu(con, ligne)
                  if trouve["entity_type"] == "starmap" else None)
    # **Le lien brut → raffiné, écrit par le jeu.** 30 commodités sur 206 le
    # portent, avec un UUID : le module de raffinage rapprochait les deux
    # formes par le **nom**, et « Raw Ice » → « Pressurized Ice » est
    # précisément le cas que le nom seul ne donne pas.
    raffine = None
    if trouve["entity_type"] == "commodity":
        raffine = (ligne.get("refined_name") or "").strip() or None

    if trouve["entity_type"] == "starmap":
        # **Une station n'a pas de fiche géographique et a pourtant des
        # services.** `_fiche_de_lieu` ne décrit que les systèmes et rendait
        # donc `None` pour Grim HEX, Lorville et Orison — précisément les
        # lieux où l'on atterrit, et où « je peux poser mon Hammerhead ici ? »
        # se pose. On crée la fiche pour les porter.
        services = _services_du_lieu(con, ligne.get("uuid"))
        if services:
            geographie = dict(geographie or {}, services=services)
    texte = (ligne.get("description") or "").strip() or None
    # **Un placeholder n'est pas une prose.** 80 lieux portent
    # « <= UNINITIALIZED => » en guise de description — l'échafaudage de CIG,
    # trouvé par le balayage : « QV Extraction Station — un astéroïde du
    # système Nyx » suivi du gabarit brut. L'afficher revient à annoncer une
    # information qu'on n'a pas ; sans lui, la fiche retombe sur ce qu'on
    # **sait** — le type, le système, les services — comme pour Nyx.
    if texte and texte.startswith("<="):
        texte = None
    traduction = _description_francaise(con, trouve["entity_type"], ligne.get("uuid"))
    traduit, source_fr = traduction if traduction else (None, None)
    argent_reel = (_prix_argent_reel(con, ligne.get("uuid"))
                   if trouve["entity_type"] == "ship" else None)
    production = None
    if trouve["entity_type"] == "ship":
        wiki = ligne_source(con, "wiki_vaisseaux", ligne.get("uuid"))
        if wiki:
            def _liste_json(valeur: str | None) -> list[Any]:
                try:
                    lu = json.loads(valeur) if valeur else []
                except (json.JSONDecodeError, TypeError):
                    return []
                return lu if isinstance(lu, list) else []

            loaners = [v.get("name") for v in _liste_json(wiki.get("loaner"))
                       if isinstance(v, dict) and v.get("name")]
            foci = [v for v in _liste_json(wiki.get("foci"))
                    if isinstance(v, str) and v]
            production = {
                "statut": wiki.get("production_status"),
                "note": wiki.get("production_note"),
                "loaners": loaners,
                "foci": foci,
                "version": wiki.get("game_version"),
                "fetched_at": wiki.get("fetched_at"),
            }
    # L'objet a-t-il une recette ? La fiche d'un nom nu doit accepter « le
    # blueprint » en reprise — remarque de l'utilisateur sur « Deadbolt III
    # Cannon » : demander ce qu'il veut, stats, blueprint ou prix.
    fabricable = bool(
        trouve["entity_type"] == "item" and ligne.get("uuid")
        and _row(con, "SELECT 1 FROM blueprints WHERE output_uuid = ?",
                 ligne["uuid"]))

    # Le rendu conversationnel doit choisir une fiche, mais l'outil est aussi
    # la porte de résolution de l'analyste. Ne lui cacher les autres types
    # faisait transformer « les forces du Wolf » en fiche de *Wolf Point Aid
    # Shelter* puis affirmer qu'aucun vaisseau ne correspondait, alors que le
    # candidat `Kruger L-21 Wolf` était bien trouvé à 90. On expose un seul
    # meilleur candidat par type, déjà filtré par les mêmes seuils que la
    # fiche. Le site ignore ce champ additionnel ; l'analyste peut, lui,
    # choisir le type compatible avec la question puis appeler l'outil typé.
    homonymes = []
    for candidat in sorted(candidats, key=lambda c: -c["score"]):
        if candidat is trouve:
            continue
        autre = candidat["ligne"]
        nom_autre = (autre.get("name")
                     or _titre_affichable(autre.get("title")) or query)
        homonymes.append({
            "name": nom_autre,
            "entity_type": candidat["entity_type"],
            "genre": candidat["genre"],
            "score": round(candidat["score"], 1),
        })

    profil_faction = None
    if trouve["entity_type"] == "org":
        # Un même nom peut désigner plusieurs lignes amont. XenoThreat en a
        # deux, toutes deux décrites, qui se contredisent sur la réaction par
        # défaut (`Hostile` / `Neutral`). La fiche la plus riche reste le socle
        # de la prose, mais une contradiction ne se tranche pas en silence.
        profils = list(con.execute(
            "SELECT DISTINCT faction_type, default_reaction FROM factions "
            "WHERE name = ?", (ligne.get("name"),)))
        profil_faction = {
            "types": sorted({r["faction_type"] for r in profils
                             if r["faction_type"]}),
            "reactions": sorted({r["default_reaction"] for r in profils
                                 if r["default_reaction"]}),
            "fiches": len(profils),
        }

    return {
        # Un contrat n'a pas de colonne `name` : son nom d'affichage est son
        # titre, **débarrassé de ses jetons ici** et pas seulement dans les
        # listes. « Missing: [TargetName] » s'affichait tel quel en en-tête de
        # fiche, alors que `speakable_title` existe depuis longtemps.
        "name": (ligne.get("name")
                 or _titre_affichable(ligne.get("title")) or query),
        "genre": trouve["genre"],
        "entity_type": trouve["entity_type"],
        "description": traduit or texte,
        "etat_civil": etat_civil,
        # Le jeu publie la famille et la réaction initiale de chaque faction.
        # « Hostile » n'est pas transformé en comportement (« tire à vue ») :
        # aucun script d'IA ni règle d'engagement n'est présent dans la source.
        "profil_faction": profil_faction,
        # Les drapeaux amont ne disent pas si un lieu est dangereux — ils
        # classent l'organisation. `able_to_arrest` désigne les forces de
        # police, `no_legal_rights` les hors-la-loi. Voir la note de
        # `docs/DONNEES_NON_UTILISEES.md` : le croisement avec les juridictions
        # du starmap n'apporte rien de plus que la règle par système.
        # `lawful` vient en dernier et vaut pour les gangs que le jeu ne prive
        # pas formellement de droits : les Headhunters sont `lawful = False`
        # sans être `no_legal_rights`. Un `None` n'est pas un « non » — 36
        # factions sur 74 n'ont pas de `Properties` du tout.
        "role_legal": ("police" if ligne.get("able_to_arrest")
                       else "hors-la-loi" if (ligne.get("no_legal_rights")
                                              or ligne.get("lawful") == 0)
                       else None),
        # Le concret d'abord : où la prendre, où ça se passe, ce qu'on y fait.
        # Le briefing est de la couleur, il vient après.
        "pratique": pratique,
        # Ce que nos parties en ont fait — une **observation**, jamais une
        # donnée de jeu, et le rendu est tenu de le dire.
        "vecu": vecu,
        # Ce qu'on sait d'un lieu quand on n'a pas de prose sur lui : ses
        # planètes, ses villes, ses points de saut, et s'il est sûr.
        "geographie": geographie,
        # Ce que devient une matière première une fois raffinée.
        "raffine_en": raffine,
        # « le texte de la mission » : la reprise demande le briefing, pas la
        # fiche pratique qui vient d'être donnée.
        "volet": volet,
        "argent_reel": argent_reel,
        "production": production,
        # Le rendu doit pouvoir citer sa source : « lu dans les fichiers du
        # jeu » et « traduit par une communauté » n'ont pas la même valeur de
        # vérité, et les deux traductions n'ont pas la même origine.
        "traduite": traduit is not None,
        "source_traduction": source_fr,
        "description_vo": texte if traduit else None,
        "ligne": ligne,
        "stats_disponibles": stats,
        "fabricable": fabricable,
        "homonymes": homonymes,
        "resolution": trouve["resolution"],
    }
