"""Le garde-fou et les arguments calculés de chaque outil — une
fonction par outil, enregistrée à côté de ses motifs. Voir route()."""

from __future__ import annotations

import dataclasses
import re
import sqlite3

from .. import armure, qualite, queries
from ..normalize import normalize
from ..resolver import mots_inexpliques, resolve
from .base import Tool
from collections.abc import Callable
from .analyse import (
    _VOCABULAIRE_PAYE,
    _fragment_de_vaisseau,
    _hors_armement,
    _lieux_nommes,
    _mots_d_intention,
    _nomme_une_org,
    coupe_sur_proximite,
    detect_nombre_de_combats,
    detect_portee,
    detect_volet,
    extract_entity,
    extract_system,
)

#: Ce qui se compte en chargeur, pas en soute.
#:
#: « Combien de balles dans un Coda » répondait le **volume** de l'arme —
#: 2 500 µSCU — au lieu de ses 6 balles, mesuré en service le 2026-08-13.
#: Des munitions ne se rangent pas dans une cale : sans vaisseau nommé, la
#: question porte sur l'arme.
_MUNITIONS = re.compile(
    r"\b(?:balles?|munitions?|cartouches?|projectiles?|coups?|"
    r"chargeurs?|billes?)\b")


# ---------------------------------------------------------------- préparateurs
#
# **Le garde-fou et les arguments calculés d'un outil vivent avec lui, pas
# dans `route()`.** Cette section remplace ce qui était une chaîne de
# vingt-huit blocs `elif tool_name == …` dans une seule fonction : chaque
# outil ajouté l'allongeait, et pendant la seule journée du 2026-08-06 trois
# outils neufs ont chacun volé une question à un autre avant d'être bridés —
# le coût marginal d'un ajout croissait avec la taille de la chaîne. Un outil
# déclare désormais ses motifs dans `_PATTERNS` et son préparateur ici ;
# `route()` est un moteur générique qui ne change plus.
#
# Le contrat : un préparateur reçoit la candidature — la question, l'entité
# retenue, les arguments en construction — et rend **False pour abandonner
# l'intention** (c'est le garde-fou : la suivante prend la main) ou True pour
# la laisser continuer, après avoir posé ses arguments calculés. Deux
# libertés de plus, chacune née d'un cas réel : remplacer l'entité retenue
# (`combien_dans_la_soute` la ré-extrait après avoir retiré le vaisseau) et
# conclure l'appel sur-le-champ (`peut_voyager`, quand il ne manque que le
# départ et que la bonne réponse est une question).


@dataclasses.dataclass
class Candidature:
    """Ce que le routeur sait au moment de préparer un outil."""

    con: sqlite3.Connection
    question: str
    contexte: str | None
    tool: Tool
    gram: str
    entity_score: float
    args: dict
    conclure: bool = False  # rendre l'appel sans passer par le contrôle de doute
    #: Le préparateur a résolu **lui-même** toutes ses entités.
    #:
    #: `_entite_douteuse` vérifie que les mots tapés sont expliqués par
    #: l'alias d'**une** entité. C'est juste pour un outil qui en consomme
    #: une, et structurellement faux pour un outil qui en consomme trois :
    #: sur « aller-retour Crusader microTech dans un Gladius », l'alias
    #: *microTech* n'explique ni « crusader » ni « gladius », l'entité est
    #: déclarée douteuse et un routage parfait tombe de 1,00 à 0,60 —
    #: c'est-à-dire sous le seuil qui appelait le LLM. Onze questions du
    #: corpus étaient dans ce cas, mesurées le 2026-08-10.
    entites_maitrisees: bool = False


PREPARATEURS: dict[str, Callable[[Candidature], bool]] = {}


def preparateur(*noms: str):
    """Enregistre la fonction comme préparateur des outils nommés."""

    def poser(fn):
        for nom in noms:
            PREPARATEURS[nom] = fn
        return fn

    return poser


@preparateur("get_ship_hardpoints")
def _prep_hardpoints(c: Candidature) -> bool:
    # L'outil résout un vaisseau, donc il se déclenche sur n'importe quelle
    # question qui en nomme un — mais son rendu ne parle que d'armement.
    # « Quel bouclier sur un Cutlass » recevait la liste des canons et des
    # missiles : pas un mensonge, mais pas une réponse.
    return not _hors_armement(c.question)


@preparateur("get_blueprint")
def _prep_blueprint(c: Candidature) -> bool:
    # Une recette inventée à partir d'un débris de nom de vaisseau est le
    # pire cas : elle est détaillée, chiffrée, et fausse.
    if _fragment_de_vaisseau(c.con, c.question, c.gram):
        return False
    # « La recette » et « quelles missions donnent le blueprint » visent le
    # même blueprint et deux moitiés opposées de la réponse.
    c.args["volet"] = detect_volet(c.question)
    if c.args["volet"] == "grind":
        rang = queries.extraire_rang_actuel(c.con, c.question)
        if rang:
            c.args["rang_actuel"] = rang
    return True


@preparateur("get_mission_group")
def _prep_mission_group(c: Candidature) -> bool:
    # « Quels sont les missions faciles à stanton » : « stanton » se faisait
    # passer pour l'organisation du groupe et la question mourait en
    # NotFound. Une question de difficulté sans organisation nommée n'est
    # pas une vue d'ensemble — c'est `missions_payantes` qui la sert. Et le
    # nom du système ne compte pas pour une organisation : « Stanton
    # System » résout en org à 85,5, c'est le piège documenté.
    if queries.detect_difficulte(c.question):
        org = resolve(c.con, c.question, entity_types=("org",), limit=1).best
        systeme_nomme = extract_system(c.question) or ""
        if (org is None or org.score < 85.0
                or normalize(org.name or "").startswith(
                    normalize(systeme_nomme) or "\0")):
            return False
    systeme = extract_system(c.question)
    if systeme:
        c.args["system"] = systeme
    # « Que donnent les missions Eckhart en blueprint » : la question ne veut
    # que la liste, pas la vue d'ensemble qui la propose.
    if re.search(r"\bblueprints?\b", normalize(c.question)):
        c.args["volet"] = "blueprints"
    return True


@preparateur("get_mission_reputation")
def _prep_mission_reputation(c: Candidature) -> bool:
    # « Que donne la mission Secure Site en blueprint » — mesuré : aucun pool
    # de récompense sur ce contrat, et la fiche complète noyait la seule
    # chose demandée. Le volet cible la réponse.
    if re.search(r"\bblueprints?\b", normalize(c.question)):
        c.args["volet"] = "blueprints"
    return True


@preparateur("compare_items")
def _prep_compare_items(c: Candidature) -> bool:
    # `compare_items` n'a pas d'entité à résoudre : sans garde-fou, il avale
    # tout ce qui contient « compare » ou « meilleur ». Mesuré : « quel est le
    # vaisseau le plus rapide » répondait par un classement d'armes, avec de
    # vrais chiffres et aucune hésitation. Sans étage LLM derrière, un routeur
    # trop permissif ne rate pas la question — il ment. On exige donc une
    # famille d'arme reconnue.
    _, _, famille = queries._weapon_filter(c.question)
    stat = queries.detect_stat(c.question)
    # « Capacitor » tient lieu de famille à lui seul : 115 objets en ont un,
    # tous des armes de vaisseau à énergie. Exiger en plus le mot « laser »
    # laissait « combien de capacitor dans une arme de vaisseau » sans
    # aucune réponse.
    if (not famille and stat != "capacitor_max") or _hors_armement(c.question):
        return False
    c.args["stat"] = stat
    taille = re.search(r"\b(?:taille|size|s)\s?([1-9]|1[0-2])\b",
                       normalize(c.question))
    if taille:
        c.args["size"] = int(taille.group(1))
    return True


@preparateur("peut_voyager")
def _prep_peut_voyager(c: Candidature) -> bool:
    # « J'ai 13 % de carburant dans mon Avenger » : la jauge de départ fait
    # partie de la question — remarque de l'utilisateur, le trajet répondait
    # réservoir plein. Sur la question **brute** : la normalisation retire le
    # « % », et le motif ne matchait plus rien. Le pourcentage doit côtoyer
    # un mot de carburant, sinon « 50 % de réduction » deviendrait une jauge.
    # Pas de `\b` après le « % » : entre deux non-mots il n'y a pas de
    # frontière, et le motif ne matchait rien — mesuré à l'écriture.
    pct = re.search(
        r"(\d{1,3})\s*(?:%|pour ?cents?)"
        r".{0,25}?\b(?:carburant|fuel|essence|r[ée]servoir|jauge)"
        r"|\b(?:carburant|fuel|essence|r[ée]servoir|jauge)\w*\b"
        r".{0,25}?(\d{1,3})\s*(?:%|pour ?cents?)",
        c.question.lower())
    if pct:
        c.args["carburant_pct"] = float(pct.group(1) or pct.group(2))
    # **L'aller-retour double le trajet** — quatre questions du journal du
    # 2026-08-08 restaient sans vraie réponse : le calcul s'arrêtait à
    # l'aller, la panne au retour n'existait pas.
    if re.search(r"\balle?rs?[- ]retours?\b", normalize(c.question)):
        c.args["aller_retour"] = True
        # **« 3 aller-retours » n'est pas « un aller-retour ».** Le compte
        # n'était pas lu : la réponse portait sur un seul circuit et sortait
        # fausse en ayant l'air complète. Le nombre doit précéder la
        # tournure, sinon « 10 % de carburant » deviendrait dix tours.
        combien = re.search(r"\b(\d{1,2})\s+alle?rs?[- ]retours?\b",
                            normalize(c.question))
        if combien:
            c.args["tours"] = int(combien.group(1))
    # « Toutes les planètes de Stanton » : la tournée se construit depuis
    # le starmap — les planètes du système, dans l'ordre du ciel — et il ne
    # manque que le départ, que la suite demandera.
    tournee = re.search(r"\btoutes? les plan[eè]tes? d[eu]? ?(\w+)",
                        normalize(c.question))
    if tournee:
        # Une planète sans un seul enfant est une coquille : « Green »
        # traîne dans le starmap de Stanton à côté des quatre vraies
        # (14 à 74 enfants chacune) — la règle de la richesse, comme pour
        # les homonymes.
        planetes = [r[0] for r in c.con.execute(
            "SELECT name FROM starmap s WHERE system_name = ? "
            "AND type_name = 'Planet' AND EXISTS "
            "(SELECT 1 FROM starmap e WHERE e.parent_uuid = s.uuid) "
            "ORDER BY name",
            (tournee.group(1).capitalize(),))]
        if len(planetes) >= 2:
            c.args[c.tool.arg], c.args["to"] = None, planetes[-1]
            c.args["vias"] = planetes[:-1]
            mots_l = {m for p in planetes for m in normalize(p).split()}
            reste_t = " ".join(m for m in normalize(c.question).split()
                               if m not in mots_l)
            navires_t = queries._ships_nommes(c.con, reste_t)
            if navires_t:
                nom = c.con.execute("SELECT name FROM ships WHERE uuid = ?",
                                    (navires_t[0],)).fetchone()
                c.args["ship"] = nom[0] if nom else None
            c.conclure = True
            return True
    # Deux lieux et un vaisseau, sinon la question n'est pas celle-là.
    lieux = _lieux_nommes(c.con, c.question)
    # **Et un vaisseau ne peut pas être un lieu.** « Avenger Titan » :
    # « titan » résolvait un lieu, devenait un détour de la tournée, et
    # l'aller-retour Grim HEX ⇄ Ruin Station passait par nulle part —
    # mesuré au rejeu du journal. Les mots d'un vaisseau nommé dans la
    # phrase sortent de la liste des lieux.
    navires_avant = queries._ships_nommes(c.con, c.question)
    if navires_avant and lieux:
        mots_question = set(normalize(c.question).split())
        # Un lieu ne se retire que si le vaisseau qui l'explique apporte
        # STRICTEMENT plus de mots que le lieu : « avenger titan » ⊋
        # « titan » (on retire), mais « Crusader » la planète face à
        # « Crusader C1 Spirit » n'a que « crusader » en commun — c'est le
        # lieu qui reste, sinon toute tournée perdait son départ.
        empreintes = []
        for uuid in navires_avant[:3]:
            nom = c.con.execute("SELECT name FROM ships WHERE uuid = ?",
                                (uuid,)).fetchone()
            if nom:
                empreintes.append(set(normalize(nom[0]).split())
                                  & mots_question)
        lieux = [l for l in lieux
                 if not any(set(normalize(l).split()) < e
                            for e in empreintes)]
    # **Un mot déjà pris pour un lieu ne peut pas être le vaisseau.** Mesuré
    # sur une remarque du journal : « je peux aller de Crusader jusque
    # Levski » — sans vaisseau nommé — sortait *Crusader C1 Spirit Wikelo
    # Special* et répondait « oui », alors que la bonne réaction est de
    # demander avec quel vaisseau.
    mots_lieux = {m for lieu in lieux for m in normalize(lieu).split()}
    reste = " ".join(m for m in normalize(c.question).split()
                     if m not in mots_lieux)
    # Un trajet ne demande qu'un vaisseau. `_ships_nommes`, conçu pour les
    # duels, parcourt tous les n-grammes et peut donc accumuler plusieurs
    # variantes du même nom : dans « puis je aller … dans un gladius », le
    # grand n-gramme approximatif sortait *Gladius Pirate* avant le gramme
    # exact « gladius ». Le résolveur d'entité choisit ici le meilleur gramme,
    # puis sa variante canonique — le Gladius de base pour « Gladius ».
    navires = []
    fragment_navire = extract_entity(
        c.con, reste, ("ship",), exclus=_mots_d_intention(None, reste))
    if fragment_navire:
        navire = resolve(c.con, fragment_navire[0],
                         entity_types=("ship",), limit=1).best
        if navire is not None and navire.score >= 85.0:
            navires = [navire.entity_id]
    # **Un seul lieu et un vaisseau : c'est le départ qui manque.** « Je peux
    # aller avec un Gladius jusque Orbituary » ne nomme pas d'où l'on part,
    # et la réponse en dépend entièrement. On laissait tomber la question ;
    # deux remarques du journal réclament la bonne réaction — « la question
    # ça devrait être : d'où pars-tu ».
    if len(lieux) == 1 and navires:
        c.args[c.tool.arg], c.args["to"] = None, lieux[0]
        nom = c.con.execute("SELECT name FROM ships WHERE uuid = ?",
                            (navires[0],)).fetchone()
        c.args["ship"] = nom[0] if nom else None
        c.args["drive"] = queries.drive_nomme_dans(c.con, c.question)
        c.conclure = True
        return True
    # **Un lieu seul : il manque le départ ET le vaisseau, et la question
    # reste quand même la bonne.** Mesuré en service le 2026-08-13 : sur
    # douze formulations de trajet, **onze** tombaient dans le vide —
    # « comment aller à Levski », « comment on va à Levski », « comment se
    # rendre à Levski ». Seule celle qui nommait le départ passait, alors
    # que le cas voisin (un lieu + un vaisseau) savait déjà demander ce
    # qui manque depuis une remarque du journal.
    #
    # Le silence coûtait cher deux fois : le joueur n'avait rien, et la
    # question partait ensuite chez l'analyste — 26 s de quota mesurées
    # pour un trajet que le déterministe calcule dès qu'on lui donne le
    # départ. « Une question à laquelle il manque une pièce reste
    # ouverte » : on demande, et le tour suivant n'a plus qu'à répondre.
    #
    # Le garde-fou tient au motif d'intention : cette branche n'est
    # atteinte que si un motif de trajet a déjà matché, donc « c'est quoi
    # Levski » ne passe pas par ici.
    if len(lieux) == 1:
        c.args[c.tool.arg], c.args["to"] = None, lieux[0]
        c.args["ship"] = None
        c.args["drive"] = queries.drive_nomme_dans(c.con, c.question)
        c.conclure = True
        return True
    if len(lieux) < 2:
        return False
    # **La tournée exige une énumération** — virgules ou « puis ». Sans
    # elle, un troisième « lieu » est du bruit d'extraction (« ruin » seul
    # sortait *Ruin Clinic* sur un simple aller-retour) et on garde
    # l'ancien couple départ → arrivée. Avec elle, le premier lieu cité
    # est le départ (remarque du journal), les intermédiaires des détours
    # dans l'ordre donné — et la réponse propose l'ordre plus court.
    enumeration = ("," in c.question
                   or re.search(r"\bpuis\b", normalize(c.question)))
    if len(lieux) > 2 and enumeration:
        c.args[c.tool.arg], c.args["to"] = lieux[0], lieux[-1]
        c.args["vias"] = lieux[1:-1]
    else:
        c.args[c.tool.arg], c.args["to"] = lieux[0], lieux[1]
    # Départ, arrivée, détours et vaisseau viennent tous d'ici : le contrôle
    # de doute générique, qui juge la phrase entière contre l'alias d'**une**
    # entité, jugerait un terme que l'outil ne reçoit même pas.
    c.entites_maitrisees = True
    # **Sans vaisseau, on demande plutôt que de se taire.** Deux lieux nommés
    # suffisent à savoir de quelle question il s'agit ; l'outil sait dire
    # « je n'ai pas reconnu le vaisseau », ce qui appelle une précision là où
    # le silence laisse en plan. Remarque du journal : « me demander avec
    # quel vaisseau ».
    nom = c.con.execute("SELECT name FROM ships WHERE uuid = ?",
                        (navires[0],)).fetchone() if navires else None
    c.args["ship"] = nom[0] if nom else None
    c.args["drive"] = queries.drive_nomme_dans(c.con, c.question)
    return True


@preparateur("get_distance")
def _prep_distance(c: Candidature) -> bool:
    # Deux lieux à trouver, pas un. Le premier n-gramme résolu sert de
    # départ, le second d'arrivée — sans ça l'outil recevrait deux fois la
    # même entité et répondrait « zéro mètre ».
    lieux = _lieux_nommes(c.con, c.question)
    if len(lieux) < 2:
        return False
    c.args[c.tool.arg], c.args["to"] = lieux[0], lieux[1]
    # Départ **et** arrivée viennent d'ici : le contrôle de doute générique,
    # qui juge la phrase entière contre l'alias d'une seule entité, voyait
    # le second lieu comme un mot inexpliqué. « distance yela crusader »
    # sortait donc à 0,60 — sous le seuil — et partait au LLM.
    c.entites_maitrisees = True
    return True


@preparateur("vaisseaux_par_metier")
def _prep_par_metier(c: Candidature) -> bool:
    # Le garde-fou d'un outil sans entité : un métier du vocabulaire fermé.
    metier = queries.detect_metier(c.question)
    if metier is None:
        return False
    c.args["role"], c.args["libelle"] = metier
    return True


@preparateur("get_trade_route")
def _prep_trade_route(c: Candidature) -> bool:
    c.args["system"] = extract_system(c.question)
    if not c.args["system"]:
        # « Depuis Lorville » ne nomme pas un système, mais Lorville en a un :
        # le starmap le porte. Sans ce détour, le filtre tombait et la route
        # partait de n'importe où.
        lieux = _lieux_nommes(c.con, c.question)
        if lieux:
            ligne = c.con.execute(
                "SELECT system_name FROM starmap WHERE name = ? "
                "AND system_name IS NOT NULL LIMIT 1", (lieux[0],)).fetchone()
            if ligne:
                c.args["system"] = ligne["system_name"]
    # **La route dépend du vaisseau et du budget, et ils se lisent dans la
    # phrase.** « Avec un Freelancer et 500k depuis Lorville » plafonnait la
    # charge à un défaut de 96 SCU et ignorait le budget — la réponse
    # conseillait des routes que le joueur ne peut ni charger ni payer.
    navires = queries._ships_nommes(c.con, c.question)
    if navires:
        ligne = c.con.execute(
            "SELECT name, cargo_scu FROM ships WHERE uuid = ?",
            (navires[0],)).fetchone()
        if ligne and ligne["cargo_scu"]:
            c.args["ship"] = ligne["name"]
            c.args["cargo"] = ligne["cargo_scu"]
    montant = queries.detect_montant(c.question)
    if montant:
        c.args["budget"] = montant
    return True


@preparateur("decrire")
def _prep_decrire(c: Candidature) -> bool:
    # Wikelo est à la fois une personne et trois stations : c'est le pronom
    # qui tranche. Sans lui, le meilleur score décide, ce qui revient à
    # répondre au hasard sur les noms qui se recouvrent.
    norm = normalize(c.question)
    # « C'est quoi la distance entre la Terre et Proxima b » résolvait
    # *Morozov-SH Arms Terrene* sur « terre » après l'abandon légitime de
    # `get_distance` (ces lieux ne sont pas dans le starmap du jeu). Une
    # demande de distance n'est jamais une demande de fiche : on passe la main
    # à l'analyste, qui saura dire que la base Star Citizen ne la couvre pas.
    if re.search(r"\bdistance\b|\bcombien de (?:km|kilometres?)\b", norm):
        return False
    if re.search(r"\bc est qui\b|\bqui est\b|\bqui sont\b", norm):
        c.args["prefere"] = "personne"
    elif re.search(r"\bc est quoi\b|\bqu est ce que?\b", norm):
        c.args["prefere"] = "chose"
    return True


@preparateur("missions_payantes")
def _prep_missions_payantes(c: Candidature) -> bool:
    # « Dans Pyro » : le nom d'un système se filtre en système — le passer
    # en lieu le faisait résoudre en *Pyro Gateway*, qui est dans **Nyx**
    # (le piège documenté des deux Gateways), et « plus de 100k dans
    # pyro » répondait sur Nyx.
    syst = extract_system(c.question)
    if syst:
        c.args["system"] = syst
    # « Les mieux payées sur Yela » : le lieu se lit dans la phrase. La
    # donnée ne descend pas sous le système, et c'est la réponse qui le
    # dira — ici on se contente de transmettre ce qui est nommé.
    lieux = _lieux_nommes(c.con, c.question)
    if lieux and (not syst
                  or normalize(lieux[0]) != normalize(syst)):
        c.args["lieu"] = lieux[0]
    # « Les missions faciles qui paient le plus » : l'étiquette de
    # difficulté du jeu est un filtre — la seule note qui recouvre des
    # contrats payés, mesuré le 2026-08-07.
    difficulte = queries.detect_difficulte(c.question)
    if difficulte:
        c.args["difficulte"] = difficulte
    # « Mission de combat qui paye le plus » : l'activité filtre le
    # classement — la contrainte était perdue en silence (grille, l. 1).
    trouve_activite = queries.detect_activite(c.question)
    if trouve_activite:
        c.args["activite"], c.args["types"] = trouve_activite
    # « Combien de missions rapportent plus de 50 000 aUEC » : le plancher
    # se lit en déterministe — la question partait chez l'analyste (~30 s)
    # pour un simple filtre. Le montant accepte « 50000 », « 50k »
    # et « 1M », comme les budgets de vaisseau.
    seuil = re.search(r"\b(?P<sens>plus|au moins|au dela|au-dela) d?e?\s*"
                      r"(?P<valeur>\d[\d\s]*\d|\d)\s*"
                      r"(?P<echelle>k|m)?\b",
                      normalize(c.question))
    if seuil:
        montant = float(seuil.group("valeur").replace(" ", ""))
        montant *= {"k": 1e3, "m": 1e6}.get(
            seuil.group("echelle") or "", 1)
        c.args["plancher"] = montant
        c.args["plancher_strict"] = seuil.group("sens") != "au moins"
    # **Le garde-fou de l'outil sans entité.** `quel(le)s? missions?`
    # suffisait à déclencher un classement de payes : « quelle mission
    # avant ? » — une question sur une chaîne — répondait « les missions les
    # mieux payées : 711 750 aUEC… ». Exact, chiffré, et sans rapport. Il
    # faut donc que la question porte son propre vocabulaire (paye,
    # réputation, disponibilité) **ou** de quoi filtrer — une organisation,
    # un lieu, une difficulté.
    if not (lieux or difficulte
            or _VOCABULAIRE_PAYE.search(normalize(c.question))
            or _nomme_une_org(c.con, c.question)):
        return False
    # « Quelle mission rapporte le plus de réputation » : même outil, autre
    # monnaie. Les deux ensembles ne se recouvrent qu'en partie — 2 345
    # contrats portent un montant en aUEC, 5 491 lignes un gain de
    # réputation.
    if re.search(r"\breputation\b|\breput\b", normalize(c.question)):
        c.args["critere"] = "reputation"
    return True


@preparateur("blueprints_par_systeme")
def _prep_blueprints_par_systeme(c: Candidature) -> bool:
    # Une organisation nommée renvoie au **groupe** — « que donnent les
    # missions Eckhart en blueprint » n'est pas un résumé par système. Le nom
    # du système ne compte pas pour une organisation (piège documenté).
    org = resolve(c.con, c.question, entity_types=("org",), limit=1).best
    systeme = extract_system(c.question)
    if org is not None and org.score >= 85.0 and not (
            systeme and normalize(org.name or "").startswith(
                normalize(systeme))):
        return False
    # « Les blueprints d'armes FPS de Pyro » : la famille filtre — et elle
    # passe **avant** la garde du blueprint nommé, parce que « armes FPS de
    # Pyro » résolvait le PyroBurst Scattergun au hasard.
    fam = queries.detect_famille_objets(c.question)
    if fam:
        c.args["famille"], c.args["clause"], c.args["mode"] = fam
    elif extract_entity(c.con, c.question, ("blueprint",),
                        exclus=_mots_d_intention(None, c.question)):
        # Un blueprint précis nommé renvoie à sa fiche : « le blueprint du
        # P8-AR » n'est pas un panorama.
        return False
    if systeme:
        c.args["systeme"] = systeme
    return True


@preparateur("catalogue_objets")
def _prep_catalogue_objets(c: Candidature) -> bool:
    # Le garde-fou de l'outil sans entité : une famille du vocabulaire
    # fermé — et pas de blueprint dans la question, qui a son catalogue.
    fam = queries.detect_famille_objets(c.question)
    if fam is None or re.search(r"\bblueprints?\b", normalize(c.question)):
        return False
    # **Le catalogue d'un composant ne répond pas d'une flotte.** « Quels
    # vaisseaux ont des boucliers de plus de 5 000 » sortait le catalogue
    # des boucliers : question sur les vaisseaux, réponse sur les objets —
    # défaut mesuré, antérieur au rayon. Le mot se cherche **hors du nom
    # de la famille**, sans quoi « armes de vaisseau » et « canons de
    # vaisseau » se saborderaient eux-mêmes.
    hors_famille = queries.question_hors_famille(c.question, fam[0])
    if re.search(r"\bvaisseaux?\b|\bships?\b", hors_famille):
        return False
    # **Une contrainte chiffrée ne se perd jamais en silence.** « Quels
    # boucliers font plus de 5 000 de capacité » sortait le catalogue
    # entier, seuil ignoré, avec l'air d'avoir répondu — la pire forme.
    # Le catalogue laisse donc la main dès qu'un seuil **et** une
    # statistique sont lus : `objets_au_seuil` sait les honorer. Sans
    # statistique reconnue, il garde la question — le catalogue vaut mieux
    # que le silence.
    lu = queries.detect_seuil(c.question)
    if lu is not None:
        if queries.detect_item_stat(c.question) is not None:
            return False
        # Un seuil lu que personne ne sait honorer — « plus de 5 000 de
        # capacité » sur des boucliers, dont la statistique n'est pas au
        # catalogue de `objets_au_seuil`. Se taire serait pire que la
        # liste, mais rendre la liste **sans le dire** ferait passer une
        # réponse partielle pour une réponse complète.
        c.args["seuil_ignore"] = lu
    c.args["famille"], c.args["clause"], c.args["mode"] = fam
    # « De taille 2 » filtre — grille, ligne 1.
    taille = re.search(r"\btailles? (\d{1,2})\b|\bs(\d{1,2})\b",
                       normalize(c.question))
    if taille:
        c.args["taille"] = int(taille.group(1) or taille.group(2))
    return True


@preparateur("rentabilite_minage")
def _prep_rentabilite_minage(c: Candidature) -> bool:
    # Ligne 2 de la grille : « à Stanton » restreint aux minerais qu'on peut
    # y extraire, et au meilleur point de vente du système.
    systeme = extract_system(c.question)
    if systeme:
        c.args["systeme"] = systeme
    return True


@preparateur("ou_miner")
def _prep_ou_miner(c: Candidature) -> bool:
    # Ligne 2 de la grille : « quels filons contiennent de l'or à Stanton »
    # classait aussi les filons de Pyro, sans un mot.
    systeme = extract_system(c.question)
    if systeme:
        c.args["systeme"] = systeme
    return True


#: Ce qui, dans une question, désigne un **contrat** et non une matière.
#: Trouvé par le banc adversarial du 2026-08-16 : « où prendre la mission
#: Rookie Rank - Direct Extra Small Cargo Haul » partait chercher une
#: **ressource**, et son titre en contient assez (« cargo », « small »)
#: pour qu'une résolution faible aboutisse. Une réponse fausse, pas un
#: silence — le pire cas.
_DIT_UNE_MISSION = re.compile(r"\bla mission\b|\ble contrat\b|\bce contrat\b")


@preparateur("where_to_find_resource")
def _prep_where_to_find(c: Candidature) -> bool:
    # Le mot « mission » désigne le sujet, pas un décor : la question
    # appartient à `decrire`, qui rend déjà « où la prendre » avec ses
    # terminaux. Se taire ici suffit à la lui laisser.
    if _DIT_UNE_MISSION.search(normalize(c.question)):
        return False
    # « Où miner de l'iron et de l'héphaestanite à stanton » répondait sur un
    # seul minerai, sans un mot sur l'autre ni sur Stanton — journal du
    # 2026-08-07. La conjonction se coupe, chaque morceau se résout, et le
    # système filtre les gisements.
    morceaux = re.split(r"\bet\b|,", normalize(c.question))
    if len(morceaux) >= 2:
        exclus = _mots_d_intention(None, c.question)
        autres: list[str] = []
        for morceau in morceaux:
            trouve = extract_entity(c.con, morceau, c.tool.entity_types,
                                    exclus=exclus)
            if trouve and normalize(trouve[0]) != normalize(c.gram) \
                    and trouve[0] not in autres:
                autres.append(trouve[0])
        if autres:
            c.args["minerais"] = autres
    systeme = extract_system(c.question)
    if systeme:
        c.args["systeme"] = systeme
    return True


@preparateur("ligne_de_vie")
def _prep_ligne_de_vie(c: Candidature) -> bool:
    # **Une ligne de vie est une phrase courte.** « Es-tu là pour me dire où
    # trouver du quantainium » est une vraie question et garde ses outils —
    # au-delà de sept mots, ce n'est plus un ping, c'est une demande.
    return len(normalize(c.question).split()) <= 7


@preparateur("panorama_missions")
def _prep_panorama_missions(c: Candidature) -> bool:
    # Le menu ne sert que les questions **larges** : un système nommé, et
    # aucun filtre plus précis — l'activité, la paye, la difficulté et
    # l'organisation ont leurs outils, qui répondent mieux.
    systeme = extract_system(c.question)
    if not systeme:
        return False
    if queries.detect_activite(c.question) \
            or queries.detect_difficulte(c.question) \
            or _VOCABULAIRE_PAYE.search(normalize(c.question)):
        return False
    # L'organisation se cherche par n-grammes, pas sur la phrase entière :
    # « que donne les missions eckart security de stanton » diluait le score
    # sous 85 et le panorama volait la question au groupe.
    org = extract_entity(c.con, c.question, ("org",),
                         exclus=_mots_d_intention(None, c.question))
    if org and not normalize(org[0]).startswith(normalize(systeme)):
        return False
    c.args["systeme"] = systeme
    if re.search(r"\btous les (?:types|genres)\b|\btypes? de missions?\b",
                 normalize(c.question)):
        c.args["volet"] = "types"
    elif re.search(r"\b(?:donneurs?|missionnaires?|commanditaires?)\b",
                   normalize(c.question)):
        c.args["volet"] = "donneurs"
    return True


@preparateur("missions_par_activite")
def _prep_missions_par_activite(c: Candidature) -> bool:
    # **Le garde-fou de l'outil sans entité : une activité reconnue.** Sans
    # lui, « quelles missions » suffirait — c'est la leçon de
    # `missions_payantes`, qui répondait un classement de payes à « quelle
    # mission avant ? ».
    trouve = queries.detect_activite(c.question)
    if trouve is None:
        return False
    # Une question de **paye** appartient au classement : « mission de
    # combat qui paye le plus » rendait la liste brute des missions de
    # combat, sans un montant.
    if _VOCABULAIRE_PAYE.search(normalize(c.question)):
        return False
    c.args["activite"], c.args["types"] = trouve
    systeme = extract_system(c.question)
    if systeme:
        c.args["system"] = systeme
    return True


@preparateur("missions_du_site")
def _prep_missions_du_site(c: Candidature) -> bool:
    # **Le garde-fou : un site nommé.** Le site se cherche **avant** le lieu :
    # « Onyx Facility » est aussi dans le starmap (120 clones), et laisser la
    # carte primer renvoyait « à Onyx » chez `missions_payantes`, qui filtrait
    # sur le système d'un seul des 120 exemplaires.
    site = queries.detect_site(c.con, c.question,
                               exclus=_mots_d_intention(None, c.question))
    if site is None:
        return False
    # « Quelles missions se passent dans Stanton » est une question de
    # système, pas de complexe : si le mot retenu nomme un système, on
    # s'efface — `missions_payantes` sait déjà filtrer par système.
    systeme = extract_system(c.question)
    if systeme and normalize(systeme) == site["terme"]:
        return False
    c.args["query"] = c.question
    return True


# Les familles de `equipement._FAMILLES` qui se montent **sur un vaisseau**.
# Elles seules cèdent la main quand un vaisseau est nommé : un casque ne se
# monte nulle part, et la question « quelle armure pour un Cutlass » n'a pas
# de sens là où « quel bouclier pour un Cutlass » en a un.
_FAMILLES_DE_VAISSEAU = frozenset({
    "bouclier", "refroidisseur", "générateur", "moteur quantique", "radar",
    "missile", "propulseur", "réservoir",
})


@preparateur("classer_equipement")
def _prep_classer_equipement(c: Candidature) -> bool:
    # **Le garde-fou : une famille nommée.** Sans elle, « quel est le
    # meilleur canon balistique » lui reviendrait — c'est le même mécanisme
    # que `compare_items`, qui avalait tout ce qui contenait « compare » ou
    # « meilleur ».
    famille = queries.detect_famille(c.question)
    if famille is None:
        return False
    # **« Pour un Cutlass » n'est pas un palmarès, c'est un montage.** Depuis
    # que les composants de vaisseau entrent dans cette famille, « quel
    # refroidisseur avec le meilleur refroidissement **pour un cutlass** »
    # lui revenait au lieu d'aller chez `get_ship_components` — qui, lui,
    # sait ce qui rentre sur ce vaisseau. Cinq cas du cahier, dont la
    # question des axes de bouclier « pour un wolf ».
    #
    # Le vaisseau se **résout**, il ne se devine pas à un mot : c'est le §6,
    # et « quelle armure pour Pyro » nomme un lieu, pas un appareil.
    # Le garde-fou ne vaut **que** pour ce qui se monte sur un vaisseau : une
    # armure personnelle ne se monte nulle part, et « quelle armure pour une
    # lune **glacée** » résout un vaisseau au passage — faux positif mesuré,
    # qui faisait taire une question du cahier.
    if famille[0] in _FAMILLES_DE_VAISSEAU:
        # **Le nom de la famille ne nomme pas un vaisseau non plus.** Mesuré :
        # « generateur » résout *Drake Caterpillar*, et « le meilleur
        # générateur » se taisait donc au profit d'un montage sur un
        # vaisseau que personne n'a cité. Un garde-fou ne doit pas dépendre
        # d'une résolution approximative sur les mots qu'il vient lui-même
        # de reconnaître — troisième occurrence de la même leçon dans ce
        # préparateur, d'où le passage systématique par cette fonction.
        hors = queries.question_hors_famille_equipement(c.question, famille[0])
        if queries._ships_nommes(c.con, hors):
            return False
        # « Quels **vaisseaux** ont des boucliers de plus de 5 000 » porte
        # sur la flotte, pas sur le catalogue de composants — le pendant
        # exact du garde-fou de `catalogue_objets`.
        if re.search(r"\bvaisseaux?\b|\bships?\b", hors):
            return False
    # « La meilleure armure sous 5 000 aUEC » : le budget est une contrainte,
    # pas un décor — grille, ligne 1.
    montant = queries.detect_montant(c.question)
    if montant is not None:
        c.args["budget"] = montant
    # Le système contraint les **points de vente**, pas les statistiques de
    # l'objet. Un équipement sans relevé dans le système n'est pas proposé
    # comme s'il était disponible sur place.
    systeme = extract_system(c.question)
    if systeme:
        c.args["systeme"] = systeme
    # **Il faut quelque chose à classer.** « Les missiles de l'Avenger
    # Titan » nomme un vaisseau et demande son emport, pas un palmarès — et
    # le mot « missile » à 3,5 le lui volait. Le critère n'est pas le
    # superlatif : « quelle armure pour une lune glacée » n'en contient
    # aucun et reste une vraie demande de classement. C'est **l'axe** qui
    # décide — soit la question nomme une statistique, soit elle demande
    # explicitement un palmarès.
    # **Le nom de la famille n'est pas un axe, ici non plus.** Le repli
    # générique lit la question entière, et « re**froid**isseur » y contient
    # « froid » — mesuré, « le meilleur refroidisseur » sortait `temp_min`,
    # colonne vide sur les 81 refroidisseurs, donc « je n'ai pas trouvé la
    # donnée » sur une question limpide. Les deux détecteurs travaillent
    # donc sur la question privée du nom de sa famille.
    hors_famille = queries.question_hors_famille_equipement(
        c.question, famille[0])
    trouve = (queries.stat_de_famille(famille[0], c.question)
              or queries.detect_component_stat(hors_famille))
    superlatif = re.search(
        r"\bmeilleure?s?\b|\bplus\b|\bmoins\b|\bpire\b|\bclasse\w*|"
        r"\bcompare\w*|\btop\b", normalize(c.question))
    # « Quel casque sous 2 000 aUEC » : un budget vaut palmarès — « le
    # meilleur sous X » est implicite, la statistique par défaut classe.
    if (not trouve and not superlatif and c.args.get("budget") is None
            and c.args.get("systeme") is None):
        return False
    # Un emport d'armure n'est pas un classement d'armure : « combien je
    # peux porter » a son outil, et il doit garder la main.
    if queries.detect_classe(c.question) and re.search(
            r"\bporter\b|\bemplacement\w*|\bmedpens?\b|\bchargeurs?\b",
            normalize(c.question)):
        return False
    c.args["famille"], c.args["types"] = famille
    if trouve:
        c.args["stat"], c.args["libelle"] = trouve
    c.args["rarete"] = queries.detect_rarete(c.question)
    # « Le meilleur bouclier **taille 2** » : pour un composant de vaisseau
    # c'est la contrainte première — ce qui ne rentre pas dans le port ne
    # se compare pas. Elle se lisait dans la question et se perdait ici.
    taille = re.search(r"\btailles? (\d{1,2})\b|\bs(\d{1,2})\b",
                       normalize(c.question))
    if taille:
        c.args["taille"] = int(taille.group(1) or taille.group(2))
    # « Plus de 5 000 de capacité » borne le classement au lieu de le
    # décorer — grille, ligne 1 : une contrainte lue s'applique.
    #
    # **Mais « sous 5 000 aUEC » est un budget, pas une caractéristique.**
    # Les deux tournures s'écrivent pareil et `detect_seuil` les lit
    # pareil : sans ce test, « la meilleure armure sous 5 000 aUEC » filtrait
    # l'encaissement à moins de 5 000 — trois cas du cahier. Le montant a
    # déjà été reconnu plus haut ; il gagne, comme le veut la règle « une
    # échelle sans unité est un budget bien avant d'être une statistique ».
    lu = queries.detect_seuil(c.question)
    if lu is not None and c.args.get("budget") is None:
        c.args["seuil"], c.args["valeur"] = lu
    return True


@preparateur("emports_d_armure")
def _prep_emports_armure(c: Candidature) -> bool:
    # **Le garde-fou d'un outil qui n'a pas toujours d'entité.** Sans le mot
    # « armure », « combien je peux porter dans un Cutlass » lui
    # reviendrait, alors que c'est la soute qu'on demande. Une classe
    # suffit — « une armure moyenne » ne nomme aucun objet — mais il faut
    # alors que le mot y soit.
    if not queries.nomme_une_armure(c.question):
        return False
    c.args["classe"] = queries.detect_classe(c.question)
    if not c.args["classe"] and not c.args.get("query"):
        return False
    return True


@preparateur("fiche_qualite")
def _prep_fiche_qualite(c: Candidature) -> bool:
    # **Le garde-fou de l'outil : une qualité lisible.** Sans nombre,
    # « c'est quoi les stats du P6-LR » est la question de `get_item_stats`
    # et doit le rester. Deux nombres désignent une comparaison, pas une
    # fiche — on laisse l'autre outil prendre.
    # Un plan « de A à Z » qui cite sa qualité veut toute la chaîne, pas la
    # seule fiche d'effet. Le préparateur du plan transmettra le nombre.
    if re.search(r"\b(?:de a a z|de bout en bout|etape par etape|pas a pas|"
                 r"plan (?:de |pour )?(?:fabrication|fabriquer|craft))\b",
                 normalize(c.question)):
        return False
    lues = qualite.qualites_lues(c.question)
    if len(lues) != 1:
        return False
    c.args["qualite"] = lues[0]
    c.args["question"] = c.question
    return True


#: Les mots qui décrivent la **cible**, à retirer avant de chercher l'arme.
#: Ils portent l'intention — la zone touchée, la classe d'armure — et
#: ressemblent tous à des noms du catalogue pour un résolveur lexical.
_SANS_CIBLE = re.compile(
    r"\btetes?\b|\bcranes?\b|\bhead ?shots?\b|\bcasques?\b|\btorses?\b|"
    r"\bbustes?\b|\bpoitrines?\b|\bjambes?\b|\bpieds?\b|\bcuisses?\b|"
    r"\barmures?\b|\blourdes?\b|\blegeres?\b|\bmoyennes?\b|\bleger\b|"
    r"\bheavy\b|\blight\b|\bmedium\b")


@preparateur("marges_d_upgrade")
def _prep_marges_d_upgrade(c: Candidature) -> bool:
    """L'outil resout son vaisseau lui-meme ; rien d'autre a poser."""
    return True


@preparateur("qualite_maximale_utile")
def _prep_qualite_maximale_utile(c: Candidature) -> bool:
    """« Jusqu'à quelle qualité ça vaut le coup pour un P6-LR ? »

    L'outil balaie lui-même les dix scénarios : une zone ou une classe
    nommée ne le restreint pas, elle **change de question** — « à partir
    de quelle qualité je tue dans la tête » appartient à
    `qualite_pour_tuer`. On abandonne donc, comme les jalons.

    L'accessoire, lui, est transmis : « jusqu'à quelle qualité avec un
    silencieux » est exactement le cas que l'utilisateur a soulevé le
    2026-08-13, et le seuil change dans les deux sens selon la pièce.
    """
    if qualite.detect_zone(c.question) or armure.detect_classe(c.question):
        return False
    c.entites_maitrisees = True
    c.args["question"] = c.question
    accessoire = qualite.detect_accessoire_cite(c.question)
    if accessoire:
        c.args["accessoire"] = accessoire
    return True


@preparateur("jalons_de_qualite")
def _prep_jalons_de_qualite(c: Candidature) -> bool:
    """« Les jalons de qualité du P6-LR » — le balayage, pas le point.

    Une zone ou une classe nommée abandonne l'intention : le point unique
    appartient à `qualite_pour_tuer`, et lui voler « OS dans la tête une
    armure lourde » rendrait neuf lignes là où une suffit.
    """
    if qualite.detect_zone(c.question) or armure.detect_classe(c.question):
        return False
    c.entites_maitrisees = True
    c.args["question"] = c.question
    return True


@preparateur("qualite_pour_tuer")
def _prep_qualite_pour_tuer(c: Candidature) -> bool:
    """« À partir de quelle qualité de P6-LR je le tue d'une balle dans la
    tête, en armure lourde ? »

    Le garde-fou est le vocabulaire de mise à mort : « les stats d'un P6-LR
    900 » cite la qualité et une arme, et c'est `fiche_qualite` qui répond.
    Le reste — zone, classe d'armure, accessoire — est **facultatif**, et ce
    qui manque est annoncé dans la réponse plutôt que réclamé au joueur.
    """
    zone = qualite.detect_zone(c.question)
    classe = armure.detect_classe(c.question)
    # **« Combien de balles a un Coda » n'est pas cette question.** Le compte
    # ne vaut que s'il vise **quelqu'un** : une zone du corps ou une classe
    # d'armure. Sans cible nommée, c'est la réserve de munitions qu'on
    # demande, et `get_item_stats` répond — le motif la lui volait.
    compte = (qualite.demande_un_compte_de_balles(c.question)
              and (zone or classe))
    # **« Combien de dégâts dans le torse lourd d'un CQ7 » n'est pas « peut-il
    # tuer d'une balle ».** Le calcul est le même, la réponse ne l'est pas :
    # on mène par les dégâts, le compte de balles suit. Remarque de
    # l'utilisateur, 2026-08-11. La cible reste exigée, sinon « combien de
    # dégâts fait un P4-AR » est la fiche de l'arme, et `get_item_stats`
    # répond mieux.
    degats = qualite.demande_des_degats(c.question) and (zone or classe)
    # **Une mise à mort sans cible n'invente plus « tête, armure lourde »**
    # (journal du 2026-08-13 : « combien de balles pour tuer avec un F55 »
    # répondait sur une cible que personne n'avait nommée — la signature
    # porte ces défauts). Le mot « armure » nu compte comme une cible :
    # « quelle qualité pour one shot une armure » en désigne une, classe
    # non dite — c'est le banc qui a tranché, la version stricte envoyait
    # cette question chez les accessoires. Sans rien du tout, le point
    # unique s'efface et le balayage des jalons répond, balles comprises.
    vise_quelquun = bool(zone or classe
                         or re.search(r"\barmures?\b", c.question))
    if not compte and not degats and not (
            qualite.demande_une_mise_a_mort(c.question) and vise_quelquun):
        return False
    if compte:
        c.args["volet"] = "balles"
    elif degats:
        c.args["volet"] = "degats"
    # L'outil résout son arme lui-même, par le blueprint : il écarte le
    # chargeur, retient les livrées comme variantes et le **dit** dans la
    # réponse. Le doute générique posait par-dessus « tu veux dire P6-LR
    # Sniper Rifle, P6-LR Magazine, ou P6-LR "Rime" ? » sur une question
    # limpide — c'est exactement la question que `_blueprint` a déjà tranchée.
    c.entites_maitrisees = True
    c.args["question"] = c.question
    if zone:
        c.args["zone"] = zone
    if classe:
        c.args["classe"] = classe

    # **La cible n'est pas l'arme, et « torse » ressemble à un nom.** Mesuré :
    # « combien de dégâts dans le torse lourd avec un CQ7 » retenait « torse »
    # comme entité, qui résout *Dust Devil Armor Core*. Les mots de zone et de
    # classe portent l'intention, pas le sujet : on les retire et on
    # ré-extrait, comme `combien_dans_la_soute` le fait pour son vaisseau.
    reste = _SANS_CIBLE.sub(" ", normalize(c.question))
    trouve = extract_entity(c.con, reste, c.tool.entity_types,
                            exclus=_mots_d_intention(c.tool, c.question))
    if trouve is not None:
        c.gram, c.entity_score = trouve
        c.args[c.tool.arg] = c.gram
    famille = qualite.detect_accessoire_cite(c.question)
    if famille:
        c.args["accessoire"] = famille
    return True


@preparateur("plan_de_fabrication")
def _prep_plan_de_fabrication(c: Candidature) -> bool:
    """La qualité éventuelle fait partie du plan composé."""
    lues = qualite.qualites_lues(c.question)
    if len(lues) > 1:
        return False
    if lues:
        c.args["qualite"] = lues[0]
    return True


@preparateur("comparer_qualites")
def _prep_comparer_qualites(c: Candidature) -> bool:
    # Deux qualités, sinon il n'y a rien à comparer. On les garde dans
    # l'ordre de la phrase : « entre un 900 et un 990 » se lit du premier
    # vers le second, comme un trajet se lit de son départ.
    lues = qualite.qualites_lues(c.question)
    # **Un nombre qui appartient à un nom d'objet n'est pas une qualité.**
    # « Un P6-LR de qualité 956 avec un torrent compensator 2 » comparait
    # le moteur quantique Torrent de la qualité 956 à la qualité… 2 —
    # journal du 2026-08-12. Chaque nombre se vérifie dans sa fenêtre de
    # mots : si « torrent compensator 2 » résout franchement un objet dont
    # l'alias porte le 2, le nombre nomme l'objet.
    if len(lues) >= 2:
        mots = normalize(c.question).split()
        restantes = []
        for v in lues:
            jeton = str(int(v)) if v == int(v) else None
            if jeton and jeton in mots:
                i = mots.index(jeton)
                fenetre = " ".join(mots[max(0, i - 2):i + 1])
                best = resolve(c.con, fenetre,
                               entity_types=("item", "blueprint")).best
                # L'alias écrit « compensator2 » collé : le nombre s'y
                # lit en suffixe d'un mot alphabétique, pas en jeton.
                jetons_alias = normalize(best.alias or "").split() \
                    if best is not None else []
                if (best is not None and best.score >= 90
                        and any(t == jeton
                                or (t.endswith(jeton)
                                    and t[:-len(jeton)].isalpha())
                                for t in jetons_alias)):
                    continue
            restantes.append(v)
        lues = restantes
    if len(lues) < 2:
        return False
    c.args["qualite_a"], c.args["qualite_b"] = lues[0], lues[1]
    c.args["question"] = c.question
    return True


@preparateur("chaine_de_qualites")
def _prep_chaine_de_qualites(c: Candidature) -> bool:
    # Pas de nombre exigé : « toutes les qualités » **est** la demande. Un ou
    # deux nombres cités n'invalident rien — la chaîne les contient — mais on
    # s'efface alors, la fiche ou la comparaison répondent plus précisément.
    if qualite.qualites_lues(c.question):
        return False
    # **Mais le mot « qualité » doit être là.** Le motif capte aussi
    # « stats » et « caractéristiques » à 3,0 — un filet posé pour rattraper
    # « les caractéristiques de toutes les qualités ». Sans garde-fou, il
    # avalait « les stats du Novikov Backpack » : `fiche_qualite` abandonne
    # faute de nombre, et la chaîne récupérait une question qui demandait
    # simplement une fiche d'objet. Mesuré — la réponse rendait même le
    # *casque* Novikov, l'outil résolvant son entité sans contrainte de
    # type. Un outil sans entité exige son vocabulaire.
    if not re.search(r"\bqualites?\b|\bchaine\b|\bgrades?\b|\bniveaux?\b|"
                     r"\bmateriaux\b|\bcrafte?\w*|\bfabriqu\w*",
                     normalize(c.question)):
        return False
    c.args["question"] = c.question
    return True


@preparateur("echelle_de_qualite")
def _prep_echelle_de_qualite(c: Candidature) -> bool:
    # L'explication de l'échelle ne se donne que si la question ne vise ni un
    # point précis (un nombre) ni un objet nommé : « les types de qualité du
    # P6-LR » doit rendre la chaîne du P6-LR, pas un cours. L'outil est sans
    # entité — c.gram vaut la question — donc on cherche l'objet nous-mêmes.
    if qualite.qualites_lues(c.question):
        return False
    return extract_entity(c.con, c.question, ("blueprint", "item"),
                          exclus=_mots_d_intention(None, c.question)) is None


@preparateur("vaisseau_pour_budget")
def _prep_vaisseau_pour_budget(c: Candidature) -> bool:
    # Le garde-fou d'un outil sans entité : un montant lisible. « Le
    # meilleur vaisseau de combat » sans budget reste une comparaison.
    montant = queries.detect_montant(c.question)
    if montant is None:
        return False
    c.args["budget"] = montant
    c.args["carriere"] = queries.detect_carriere(c.question)
    return True


@preparateur("accessoires_compatibles")
def _prep_accessoires(c: Candidature) -> bool:
    # « Quelles optiques » ne demande pas les chargeurs : la famille nommée
    # filtre, son absence rend tout.
    famille = queries.detect_famille_accessoire(c.question)
    if famille:
        c.args["famille"] = famille[0]
    return True


@preparateur("objets_au_seuil")
def _prep_objets_au_seuil(c: Candidature) -> bool:
    # Le même garde-fou que son homologue vaisseau, en miroir : un seuil,
    # une statistique d'objet, et pas de mot de vaisseau.
    lu = queries.detect_seuil(c.question)
    stat = queries.detect_item_stat(c.question)
    if lu is None or stat is None:
        return False
    # Le mot « vaisseau » rend la main à `vaisseaux_au_seuil` — sauf quand
    # il qualifie l'objet et non le sujet : « quelles armes **de vaisseau**
    # font plus de 500 DPS » interroge le catalogue d'armes. La famille
    # d'objets tranche, plutôt qu'un motif de plus.
    if (re.search(r"\bvaisseaux?\b|\bships?\b", normalize(c.question))
            and queries.detect_famille_objets(c.question) is None):
        return False
    c.args["seuil"], c.args["valeur"] = lu
    c.args["stat"] = stat
    return True


@preparateur("armes_par_metrique")
def _prep_armes_par_metrique(c: Candidature) -> bool:
    metrique = queries.detect_metrique(c.question)
    if metrique is None:
        return False
    c.args["metrique"] = metrique
    taille = re.search(r"\b(?:taille|size|s)\s?([1-9]|1[0-2])\b",
                       normalize(c.question))
    if taille:
        c.args["size"] = int(taille.group(1))
    return True


@preparateur("vaisseaux_sans_composant")
def _prep_sans_composant(c: Candidature) -> bool:
    # Sans composant nommé, « quels vaisseaux n'ont pas… » ne veut rien
    # dire — et « sans » est un mot trop courant pour se passer de ce second
    # garde-fou.
    composant = queries.detect_composant(c.question)
    if composant is None:
        return False
    # « 5 Gladius peuvent-ils détruire un Hammerhead sans bouclier » n'est
    # pas un inventaire : le verbe de destruction dit que « sans bouclier »
    # est l'état d'une cible, pas un filtre de catalogue. Mesuré — la
    # bataille partait en doute (variantes du Gladius) et cet outil, sûr de
    # lui, répondait 26 vaisseaux sans bouclier à une question de duel.
    if re.search(r"\b(?:detruire|tuer|abattre|vaincre|exploser)\b",
                 normalize(c.question)):
        return False
    c.args["type_item"] = composant
    return True


@preparateur("vaisseaux_multi_criteres")
def _prep_multi_criteres(c: Candidature) -> bool:
    # Le même garde-fou de vocabulaire que `vaisseaux_au_seuil` — sans lui,
    # une question à deux seuils sur des armes lui reviendrait.
    if not re.search(r"\bvaisseaux?\b|\bships?\b|\bappareils?\b",
                     normalize(c.question)):
        return False
    criteres, budget = queries.detect_contraintes(c.question)
    carriere = queries.detect_carriere(c.question)
    # **Les critères qualitatifs comptent aussi.** « Un vaisseau rapide avec
    # du fret » ne porte aucun nombre et restait sans réponse — question non
    # routée du balayage du 2026-08-07. « Rapide » classe par la vitesse,
    # « avec du fret » exige une soute : `valeur=None` dit à l'outil
    # « présent, et on trie dessus » — aucun seuil inventé.
    deja = {stat for stat, _, _ in criteres}
    norm = normalize(c.question)
    for motif, stat in ((r"\brapides?\b", "max_speed"),
                        (r"\b(?:avec|et) (?:du|de la|un peu de) "
                         r"(?:fret|cargo|soute)\b", "cargo_scu")):
        if stat not in deja and re.search(motif, norm):
            criteres.append((stat, ">=", None))
            deja.add(stat)
    # **Deux contraintes au moins**, sinon les outils simples font mieux :
    # `vaisseaux_au_seuil` sait dire combien de vaisseaux dépassent un
    # seuil, `vaisseau_pour_budget` sait ce qu'on peut s'offrir. Cet outil
    # n'existe que pour ce qu'aucun ne sait faire — le croisement.
    if len(criteres) + (1 if budget else 0) + (1 if carriere else 0) < 2:
        return False
    if not criteres and budget is None:
        return False
    c.args["criteres"] = criteres
    c.args["budget"] = budget
    c.args["carriere"] = carriere
    return True


@preparateur("vaisseaux_au_seuil")
def _prep_au_seuil(c: Candidature) -> bool:
    # **Une question à plusieurs critères ne lui appartient pas.** Il n'en
    # lirait qu'un et répondrait juste sur un tiers de la question, ce qui
    # est pire qu'un silence : la réponse a l'air complète.
    criteres, budget = queries.detect_contraintes(c.question)
    if len(criteres) + (1 if budget else 0) >= 2:
        return False
    # Le garde-fou d'un outil sans entité : un seuil **et** une statistique
    # connue, sinon « plus de 3 vaisseaux » lui reviendrait. Le vocabulaire
    # de vaisseau est exigé, comme pour `compare_ships` : sans lui,
    # « quelles armes font plus de 500 DPS » répondait **240 vaisseaux**,
    # « dps » étant aussi une statistique de vaisseau. Un seuil sur les
    # armes n'a pas encore d'outil ; se taire vaut mieux que répondre à
    # côté.
    if not re.search(r"\bvaisseaux?\b|\bships?\b|\bappareils?\b",
                     normalize(c.question)):
        return False
    lu = queries.detect_seuil(c.question)
    stat = queries.detect_ship_stat_ou_rien(c.question)
    if lu is None or stat is None:
        return False
    c.args["seuil"], c.args["valeur"] = lu
    c.args["stat"] = stat
    return True


@preparateur("bataille")
def _prep_bataille(c: Candidature) -> bool:
    # La bataille ne prend la main que s'il y a du **nombre** ou une
    # **modulation** — sinon le duel sérieux répond, et sa physique est
    # 100 % données publiées.
    from ..combat import _MODULATIONS

    q = f" {normalize(c.question)} "
    coupe = re.search(r"\b(?:detruire|tuer|abattre|battre|vaincre|casser|"
                      r"exploser|tomber|contre|versus|vs)\b", q)
    if coupe is None:
        return False
    avant, apres = q[:coupe.start()], q[coupe.end():]

    def _moduler(segment: str) -> tuple[str, list[str]]:
        lues = []
        for motif, nom in _MODULATIONS:
            m = re.search(motif, segment)
            if m:
                lues.append(nom)
                segment = segment[:m.start()] + " " + segment[m.end():]
        return segment, lues

    avant, mods_att = _moduler(avant)
    apres, mods_cible = _moduler(apres)

    # « … qui ne peut plus aller vers la gauche » : une modulation qu'on ne
    # sait pas chiffrer se **dit**, elle ne s'avale pas — règle 1.
    incomprise_motif = r"\bqui ne \w[\w ]{3,40}?(?= peu(?:t|vent)\b|$)"
    incomprises = []
    for segment in (avant, apres):
        m = re.search(incomprise_motif, segment)
        if m:
            incomprises.append(m.group(0).strip())
    avant = re.sub(incomprise_motif, " ", avant)
    apres = re.sub(incomprise_motif, " ", apres)

    # « Avec uniquement son S10 » ne signifie pas « ajoute un S10 » : tout
    # le reste, missiles compris, sort du calcul. Le journal montrait le
    # contraire — la restriction était avalée puis le stock reprenait.
    seule = re.search(
        r"\bavec\s+(?:uniquement|seulement|juste)\s+"
        r"(?:(?:son|sa|ses|le|la|les|un|une|des|du|de|d)\s+)?(.+?)"
        r"(?:\s+(?:peut|peuvent|pourraient?|vont?))?\s*$", avant) or re.search(
        r"\bavec\s+(?:(?:son|sa|ses|le|la|les|un|une|des|du|de|d)\s+)?"
        r"(.+?)\s+(?:uniquement|seulement|juste)"
        r"(?:\s+(?:peut|peuvent|pourraient?|vont?))?\s*$", avant)
    # L'arme et la qualité, comme au duel.
    m = seule or re.search(
        r"\b(?:equipee?s? (?:de|d|avec)|arme[es]? (?:de|avec)|"
        r"avec (?:des|du|d|une?)) (.+?)"
        r"(?:\s+(?:peut|peuvent|pourraient?|vont?))?\s*$", avant)
    if m:
        terme_arme = m.group(1).strip()
        ql = re.search(r"\b(?:qualite|quality|q)\s*(\d{1,4})\b", terme_arme)
        if ql:
            c.args["qualite"] = float(ql.group(1))
            terme_arme = (terme_arme[:ql.start()] + terme_arme[ql.end():]).strip()
        c.args["arme"] = terme_arme
        if seule:
            c.args["arme_seule"] = True
        avant = avant[:m.start()]

    nombre = re.search(r"\b([2-9]|1[0-9])\b", avant)
    n = int(nombre.group(1)) if nombre else 1
    if n == 1 and not mods_att and not mods_cible and not incomprises:
        return False    # pas de bataille : le duel sérieux prend la main

    attaquant = extract_entity(c.con, avant.strip(), ("ship",),
                               exclus=_mots_d_intention(None, avant))
    cible = extract_entity(c.con, apres.strip(), ("ship",),
                           exclus=_mots_d_intention(None, apres))
    if attaquant is None or cible is None:
        return False
    c.args[c.tool.arg] = attaquant[0]
    c.args["cible"] = cible[0]
    c.args["n"] = n
    if mods_att:
        c.args["modulations"] = mods_att
    if mods_cible:
        c.args["modulations_cible"] = mods_cible
    if incomprises:
        c.args["incomprises"] = incomprises
    c.gram, c.entity_score = attaquant
    return True


#: Le vocabulaire fermé des familles du réseau — un mot par type, comme
#: `hardpoint_categories` : un type neuf s'ajoute à la main.
_FAMILLES_RESEAU = (
    (r"\bbouclier\w*|\bshields?\b", "Shield"),
    (r"\brefroidisseur\w*|\bcoolers?\b", "Cooler"),
    (r"\bgenerateur\w*|\bcentrale\w*|\bpower ?plants?\b", "PowerPlant"),
    (r"\bradars?\b", "Radar"),
    (r"\b(?:moteur\w* )?quanti\w+|\bquantum\b", "QuantumDrive"),
)


@preparateur("budget_energie")
def _prep_budget_energie(c: Candidature) -> bool:
    """Un vaisseau résolu, rien d'autre à calculer."""
    q = normalize(c.question)
    source = extract_entity(c.con, q, ("ship",),
                            exclus=_mots_d_intention(None, q))
    if source is None:
        return False
    c.args[c.tool.arg] = source[0]
    c.gram, c.entity_score = source
    c.entites_maitrisees = True
    return True


@preparateur("composants_par_pip")
def _prep_composants_par_pip(c: Candidature) -> bool:
    """Un outil sans entité exige son vocabulaire : la famille du réseau.

    « Quelle arme consomme le moins » n'a pas d'outil ici — les armes se
    comptent en unités standard, pas en pips, et leur classement existe
    déjà ailleurs. Sans famille reconnue, l'intention s'abandonne.
    """
    q = normalize(c.question)
    for motif, type_ in _FAMILLES_RESEAU:
        if re.search(motif, q):
            c.args["type_composant"] = type_
            break
    else:
        return False
    taille = re.search(r"\b(?:taille|s)\s*[- ]?(\d{1,2})\b", q)
    if taille:
        c.args["taille"] = int(taille.group(1))
    c.args[c.tool.arg] = c.question
    c.entites_maitrisees = True
    return True


@preparateur("loadout_energie")
def _prep_loadout_energie(c: Candidature) -> bool:
    q = normalize(c.question)
    source = extract_entity(c.con, q, ("ship",),
                            exclus=_mots_d_intention(None, q))
    if source is None:
        return False
    c.args[c.tool.arg] = source[0]
    c.args["mode"] = ("puissant" if re.search(
        r"\bpuissant\w*|\ba fond\b|\bperformance\b|\bmeilleure? perf",
        q) else "econome")
    c.gram, c.entity_score = source
    c.entites_maitrisees = True
    return True


@preparateur("loadout_discret")
def _prep_loadout_discret(c: Candidature) -> bool:
    q = normalize(c.question)
    source = extract_entity(c.con, q, ("ship",),
                            exclus=_mots_d_intention(None, q))
    if source is None:
        return False
    c.args[c.tool.arg] = source[0]
    c.gram, c.entity_score = source
    c.entites_maitrisees = True
    return True


@preparateur("matchups_vaisseau")
def _prep_matchups_vaisseau(c: Candidature) -> bool:
    """Résout un vaisseau, ou les deux côtés d'une comparaison explicite."""
    q = normalize(c.question)
    # « Qui gagne entre un wolf et un arrow » : la coupe est « entre … et »,
    # pas un mot de duel. Les deux côtés doivent résoudre un vaisseau, sinon
    # l'intention s'abandonne — « entre Lorville et Yela » reste un trajet.
    entre = re.search(r"\bentre\b(.+?)\bet\b(.+)$", q)
    if entre is not None:
        source = extract_entity(
            c.con, entre.group(1), ("ship",),
            exclus=_mots_d_intention(None, entre.group(1)))
        cible = extract_entity(
            c.con, entre.group(2), ("ship",),
            exclus=_mots_d_intention(None, entre.group(2)))
        if source is not None and cible is not None:
            c.args[c.tool.arg] = source[0]
            c.args["cible"] = cible[0]
            c.gram, c.entity_score = source
            c.entites_maitrisees = True
            return True
    coupe = re.search(
        r"\b(?:par rapport\s+(?:a|au|aux)?|face\s+a|contre|versus|vs)\b", q)
    if coupe is not None:
        avant, apres = q[:coupe.start()], q[coupe.end():]
        source = extract_entity(
            c.con, avant, ("ship",),
            exclus=_mots_d_intention(None, avant))
        cible = extract_entity(
            c.con, apres, ("ship",),
            exclus=_mots_d_intention(None, apres))
        if source is None or cible is None:
            return False
        c.args[c.tool.arg] = source[0]
        c.args["cible"] = cible[0]
        c.gram, c.entity_score = source
        c.entites_maitrisees = True
        return True

    source = extract_entity(
        c.con, q, ("ship",), exclus=_mots_d_intention(None, q))
    if source is None:
        return False
    c.args[c.tool.arg] = source[0]
    c.args["mode"] = (
        "destruction" if re.search(
            r"\bque (?:peu(?:t|vent)|sait|savent) "
            r"(?:detruire|abattre|tuer)\b", q)
        else "matchups")
    c.gram, c.entity_score = source
    c.entites_maitrisees = True
    return True


@preparateur("simuler_duel")
def _prep_simuler_duel(c: Candidature) -> bool:
    """Deux vaisseaux, sinon rien : une simulation à un seul bord n'existe pas.

    Le garde-fou est strict à dessein. « Combien de fois » et « sur 10
    combats » sont des tournures courantes ; sans les deux vaisseaux, la
    question parle d'autre chose, et un outil sans entité qui avale du
    vocabulaire commun est le défaut le plus cher du routeur
    (`compare_items`, `vaisseaux_au_seuil`). En reprise, c'est
    `dialogue._reprise_de_duel` qui fournit la paire du duel précédent —
    c'est d'ailleurs la forme réelle de la question au journal.
    """
    q = normalize(c.question)
    for decoupe in (r"\bentre\b(.+?)\bet\b(.+)$",
                    r"^(.+?)\b(?:face\s+a|contre|versus|vs)\b(.+)$"):
        trouve = re.search(decoupe, q)
        if trouve is None:
            continue
        source = extract_entity(
            c.con, trouve.group(1), ("ship",),
            exclus=_mots_d_intention(None, trouve.group(1)))
        cible = extract_entity(
            c.con, trouve.group(2), ("ship",),
            exclus=_mots_d_intention(None, trouve.group(2)))
        if source is None or cible is None:
            continue
        c.args[c.tool.arg] = source[0]
        c.args["cible"] = cible[0]
        c.args["passes"] = detect_nombre_de_combats(c.question)
        c.gram, c.entity_score = source
        c.entites_maitrisees = True
        return True
    return False


@preparateur("peut_detruire")
def _prep_peut_detruire(c: Candidature) -> bool:
    # Deux vaisseaux, un verbe entre les deux : l'attaquant est avant, la
    # cible après — la même coupe que « dans » pour la soute. Chercher les
    # deux dans la phrase entière les ferait se voler la résolution.
    q = f" {normalize(c.question)} "
    coupe = re.search(r"\b(?:detruire|tuer|abattre|battre|vaincre|casser|"
                      r"exploser|tomber|contre|versus|vs)\b", q)
    if coupe is None:
        return False
    avant, apres = q[:coupe.start()], q[coupe.end():]

    # **« Avec le meilleur loadout »** (sprint 21) : on résout l'armement
    # optimal pour ce duel au lieu du stock. Les critères explicites — un
    # grade, une famille militaire, « pour toucher une cible rapide » — se
    # lisent au passage, et la spec est retirée de la phrase pour ne pas
    # parasiter la résolution des vaisseaux.
    m_load = re.search(
        r"\bavec (?:le |son |un )?(?:meilleur|meilleure|optimal\w*|top) "
        r"(?:loadout|equipement|armement|setup|stuff)\b", apres) or re.search(
        r"\bavec (?:le |son |un )?(?:meilleur|meilleure|optimal\w*|top) "
        r"(?:loadout|equipement|armement|setup|stuff)\b", avant)
    if m_load:
        c.args["loadout"] = "meilleur"
        criteres: dict = {}
        if re.search(r"cible\w* (?:rapide|vive|agile|mobile)|"
                     r"toucher\w* (?:une |un )?(?:cible )?"
                     r"(?:rapide|vive|agile|mobile)", q):
            criteres["cible_rapide"] = True
        grade = re.search(r"\bgrade ([a-d])\b", q)
        if grade:
            criteres["grade_lettre"] = grade.group(1).upper()
        if re.search(r"\bballistique\w*\b", q):
            criteres["weapon_class"] = "ballistic"
        elif re.search(r"\blaser\w*\b", q):
            criteres["weapon_class"] = "energy"
        if criteres:
            c.args["loadout_criteres"] = criteres
        avant = avant.replace(m_load.group(0), " ")
        apres = apres.replace(m_load.group(0), " ")

    # « Avec uniquement son S10 » : une sélection exclusive, pas un ajout.
    # Elle doit arriver au métier jusque dans ce détail ; l'ignorer faisait
    # répondre avec quatre C-07T à deux questions explicites du journal.
    seule = re.search(
        r"\bavec\s+(?:uniquement|seulement|juste)\s+"
        r"(?:(?:son|sa|ses|le|la|les|un|une|des|du|de|d)\s+)?(.+?)"
        r"(?:\s+(?:peut|pourrait|pourra|sait|serait|va))?\s*$", avant) or re.search(
        r"\bavec\s+(?:(?:son|sa|ses|le|la|les|un|une|des|du|de|d)\s+)?"
        r"(.+?)\s+(?:uniquement|seulement|juste)"
        r"(?:\s+(?:peut|pourrait|pourra|sait|serait|va))?\s*$", avant)
    # « … équipé de Deadbolt III » : l'arme de l'attaquant, avant le verbe.
    # La capture s'arrête avant la queue d'intention — « deadbolt iii peut »
    # résolvait *Sol-III Core Bombardier* à 85,5, le mensonge type.
    m = seule or re.search(
        r"\b(?:equipee?s? (?:de|d|avec)|arme[es]? (?:de|avec)|"
        r"avec (?:des|du|d|une?)) (.+?)"
        r"(?:\s+(?:peut|pourrait|pourra|sait|serait|va))?\s*$", avant)
    if m:
        terme_arme = m.group(1).strip()
        # « Revenant en tourelles » ne remplace que ces postes. Sans cette
        # portée, le même nom aurait aussi écrasé les canons fixes du pilote.
        en_tourelles = re.search(
            r"\s+(?:en|dans (?:les|ses))\s+tourelles?\b", terme_arme)
        if en_tourelles:
            c.args["arme_tourelles"] = True
            terme_arme = (terme_arme[:en_tourelles.start()]
                          + terme_arme[en_tourelles.end():]).strip()
        # « Deadbolt III qualité 900 » : la qualité de fabrication module
        # les dégâts. Seule la forme explicite est lue — un nombre nu
        # ferait prendre le « 337 » d'un CF-337 pour une qualité.
        q = re.search(r"\b(?:qualite|quality|q)\s*(\d{1,4})\b", terme_arme)
        if q:
            c.args["qualite"] = float(q.group(1))
            terme_arme = (terme_arme[:q.start()] + terme_arme[q.end():]).strip()
        c.args["arme"] = terme_arme
        if seule:
            c.args["arme_seule"] = True
        avant = avant[:m.start()]

    # « … avec un bouclier qualité 950 » : la qualité porte sur la défense,
    # pas sur l'arme lue avant le verbe. Elle peut qualifier le stock sans
    # nom de modèle ; dans ce cas on retire toute la queue « avec un
    # bouclier » pour ne pas essayer de résoudre le mot bouclier comme objet.
    q_bouclier = re.search(
        r"\bavec\s+(?:(?:un|le|des|une|du)\s+)?"
        r"(?:(?:autre\s+)?(?:boucliers?|shields?)\s+)?(.+?\s+)?"
        r"(?:qualite|quality|q)\s*(\d{1,4})\b", apres)
    if q_bouclier:
        c.args["qualite_bouclier"] = float(q_bouclier.group(2))
        apres = (apres[:q_bouclier.start()]
                 + " avec " + (q_bouclier.group(1) or "")
                 + apres[q_bouclier.end():])

    # « … avec un autre bouclier », « avec des FR-86 » : côté cible. Sans
    # nom de modèle, on ne devine pas — le stock reste en place.
    m = re.search(r"\bavec (?:un |le |des |une )?(?:autre )?"
                  r"(?:boucliers? |shields? )?(.+)$", apres)
    # **Un vaisseau n'est pas un bouclier.** « Détruire un Hammerhead avec
    # un Scorpius » posait `bouclier="scorpius"` et tronquait la phrase, si
    # bien que l'attaquant restait introuvable et que la question tombait
    # chez `get_ship_hardpoints`. La queue ne se lit comme une défense que
    # si elle n'est pas un vaisseau — le catalogue tranche, pas une
    # tournure.
    queue_est_un_vaisseau = False
    if m and m.group(1).strip():
        vaisseau_queue = resolve(c.con, m.group(1).strip(),
                                 entity_types=("ship",), limit=1).best
        queue_est_un_vaisseau = (
            vaisseau_queue is not None and vaisseau_queue.score >= 88
            and not mots_inexpliques(m.group(1).strip(), vaisseau_queue.alias))
    if (m and m.group(1).strip() and "stock" not in m.group(1)
            and not queue_est_un_vaisseau):
        c.args["bouclier"] = m.group(1).strip()
        apres = apres[:m.start()]
    elif q_bouclier:
        apres = apres[:q_bouclier.start()]

    attaquant = extract_entity(c.con, avant.strip(), ("ship",),
                               exclus=_mots_d_intention(None, avant))
    cible = extract_entity(c.con, apres.strip(), ("ship",),
                           exclus=_mots_d_intention(None, apres))

    # **« Avec un Scorpius » désigne l'attaquant, même après le verbe.**
    # « Quel armement pour détruire un Hammerhead **avec un Scorpius** » ne
    # nomme personne avant le verbe : la coupe rendait un attaquant vide et
    # la question tombait chez `get_ship_hardpoints`, qui répondait
    # l'armement d'origine du Hammerhead. C'est la règle « la préposition
    # désigne », déjà appliquée aux lieux et à la soute.
    if attaquant is None and cible is not None:
        avec = re.search(r"\bavec (?:un |une |le |la |mon |ma |son |sa )?(.+)$",
                         apres)
        if avec:
            candidat = extract_entity(
                c.con, avec.group(1).strip(), ("ship",),
                exclus=_mots_d_intention(None, avec.group(1)))
            # Il faut que ce soit un **autre** vaisseau : « détruire un
            # Hammerhead avec un Hammerhead » n'a pas de sens, et un
            # gramme qui déborde résoudrait deux fois le même.
            if candidat is not None and normalize(candidat[0]) != normalize(cible[0]):
                attaquant, cible2 = candidat, extract_entity(
                    c.con, apres[:avec.start()].strip(), ("ship",),
                    exclus=_mots_d_intention(None, apres[:avec.start()]))
                if cible2 is not None:
                    cible = cible2

    if attaquant is None or cible is None:
        return False
    c.args[c.tool.arg] = attaquant[0]
    c.args["cible"] = cible[0]
    c.gram, c.entity_score = attaquant
    # Attaquant et cible sont résolus ici, chacun sur sa moitié de phrase :
    # le doute générique jugerait l'un contre les mots de l'autre.
    c.entites_maitrisees = True
    return True


@preparateur("combien_dans_la_soute")
def _prep_dans_la_soute(c: Candidature) -> bool:
    # Un objet **et** un vaisseau : même piège que `ou_acheter_pres`, les
    # deux se volent la résolution dans la phrase entière. Le vaisseau se
    # cherche d'abord — il est mieux nommé, et le retirer dégage l'objet.
    # « Combien de Coda dans un Cutlass Black » résolvait *Cutlass* en objet
    # sans cette précaution.
    navire = extract_entity(c.con, c.question, ("ship",),
                            exclus=_mots_d_intention(None, c.question))
    reste = c.question
    mots_navire: set[str] = set()
    if navire is not None:
        # Le gramme retenu déborde souvent sur l'objet : « coda cutlass
        # black » résout en *Drake Cutlass Black* à 95, et retirer tous ses
        # mots emportait « coda » avec. On ne retire que ce que le nom du
        # vaisseau **explique** — le reste appartient à l'autre entité, par
        # construction.
        #
        # L'alias qui a matché ne suffit pas : « cutlass black » sort par le
        # surnom **« cutty black »**, qui n'explique pas « cutlass » — le
        # mot restait, et l'objet devenait un « Cutlass T-Shirt ». Un mot
        # appartient au vaisseau s'il est expliqué par l'alias **ou** par le
        # nom officiel.
        res_navire = resolve(c.con, navire[0], entity_types=("ship",),
                             limit=1)
        refs = ([res_navire.best.alias, res_navire.best.name]
                if res_navire.best else [navire[0]])
        de_trop = set.intersection(*(set(mots_inexpliques(navire[0], r))
                                     for r in refs))
        mots_navire = {m for m in normalize(navire[0]).split()
                       if m not in de_trop}
        reste = " ".join(m for m in normalize(c.question).split()
                         if m not in mots_navire)
    # **« Combien de balles dans un Coda » n'est pas une question de
    # soute.** Mesuré en service le 2026-08-13 : la réponse était « Coda
    # Pistol occupe 2 500 µSCU, dis-moi dans quel vaisseau » — plausible,
    # chiffrée, et à côté. Un mot d'écart avec « combien de balles **a**
    # un Coda », qui répond juste (6 balles).
    #
    # Ce qui tranche n'est pas le verbe mais l'**unité comptée** : des
    # munitions ne se rangent pas dans une soute, elles se comptent dans
    # un chargeur. Sans vaisseau nommé, la question porte donc sur l'arme
    # et appartient à `get_item_stats` ; avec un vaisseau (« combien de
    # caisses de balles dans un C2 »), la soute reprend la main.
    if navire is None and _MUNITIONS.search(normalize(c.question)):
        return False
    objet = extract_entity(c.con, reste, c.tool.entity_types,
                           exclus=_mots_d_intention(None, reste))
    if objet is None:
        return False
    # « Combien de Dragonfly dans un C2 » : le nom nu désigne le véhicule,
    # jamais sa livrée ni sa peluche — la règle du P8-AR, transposée. On ne
    # sait pas répondre pour un véhicule (la baie n'est pas publiée, l'outil
    # d'emport a été retiré le 2026-08-07) : chiffrer la peinture homonyme
    # serait pire qu'un silence.
    homonyme = resolve(c.con, objet[0], entity_types=("ship",), limit=1).best
    if (homonyme is not None and homonyme.score >= 90
            and not mots_inexpliques(objet[0], homonyme.alias)):
        return False
    c.args[c.tool.arg] = objet[0]
    if navire is not None:
        # On transmet le vaisseau **élagué** de ce qui appartenait à
        # l'objet : « coda cutlass black » résout juste, mais le journal et
        # les questions de suite liraient un nom qui n'existe pas.
        c.args["to"] = " ".join(
            m for m in normalize(navire[0]).split()
            if m in mots_navire) or navire[0]
    c.gram, c.entity_score = objet
    # L'objet **et** le vaisseau sont résolus ici : le doute générique lisait
    # le nom du vaisseau comme un mot que l'alias de l'objet n'explique pas.
    # « combien de finley passe dans un cutlass black » sortait à 0,57.
    c.entites_maitrisees = True
    return True


@preparateur("methode_de_raffinage")
def _prep_methode_raffinage(c: Candidature) -> bool:
    # « La plus rapide **et** la plus efficace » cite deux axes : on les
    # transmet tous, l'outil dira que c'est un compromis. `detect_criteres`
    # — les axes de raffinage — et non `detect_contraintes`, qui lit des
    # seuils de vaisseau.
    c.args["criteres"] = queries.detect_criteres(c.question)
    return True


@preparateur("ou_raffiner", "conseil_de_raffinage")
def _prep_raffinage(c: Candidature) -> bool:
    # « Où raffiner du Stileron près de Checkmate » : même piège que
    # `ou_acheter_pres` — cherchés dans la phrase entière, le minerai et le
    # lieu se volent mutuellement. On coupe sur la préposition, le lieu est
    # après, le minerai avant.
    reste = c.question
    coupe = coupe_sur_proximite(c.question)
    if coupe is not None:
        avant, apres = coupe
        lieux = _lieux_nommes(c.con, apres)
        if lieux:
            c.args["lieu"] = lieux[0]
            cible = extract_entity(
                c.con, avant, c.tool.entity_types,
                exclus=_mots_d_intention(None, avant))
            if cible is None:
                return False
            # Le **gramme extrait**, pas la phrase entière : `query = avant`
            # envoyait « ou raffiner de l'iron et de l'or » au contrôle de
            # certitude, qui doutait entre « Iron » et « Iron (Ore) » sur le
            # mot « or » — journal du 2026-08-07.
            c.args["query"] = cible[0]
            reste = avant
    # **« De l'iron et de l'or » nomme deux minerais, et le second se
    # perdait en silence** — journal du 2026-08-07 : la réponse portait sur
    # le seul Iron, sans un mot sur l'or. On coupe sur la conjonction et on
    # résout chaque morceau ; `ou_raffiner` croise les rendements.
    if c.tool.name == "ou_raffiner":
        retenu = c.args.get("query") or c.gram
        morceaux = re.split(r"\bet\b|,", normalize(reste))
        if len(morceaux) >= 2:
            exclus = _mots_d_intention(None, reste)
            autres: list[str] = []
            for morceau in morceaux:
                trouve = extract_entity(c.con, morceau, c.tool.entity_types,
                                        exclus=exclus)
                if trouve and normalize(trouve[0]) != normalize(retenu) \
                        and trouve[0] not in autres:
                    autres.append(trouve[0])
            if autres:
                c.args["minerais"] = autres
        # **« À Stanton » est un filtre, pas un décor.** Mesuré au journal :
        # « où raffiner de l'iron et de l'or à stanton » listait Levski (Nyx)
        # en deuxième ligne — la contrainte était perdue en silence, la pire
        # des formes. Un système nommé restreint la liste, et le rendu le dit.
        systeme = extract_system(c.question)
        if systeme:
            c.args["systeme"] = systeme
        # « Sans prendre en compte leur rareté, en additionnant les % » —
        # demande de l'utilisateur : le cumul remplace la règle du plus rare.
        if re.search(r"\bsans .{0,30}\brarete\b|\badditionn\w*|\bcumul\w*",
                     normalize(c.question)):
            c.args["classement"] = "cumul"
    return True


@preparateur("ou_acheter_pres")
def _prep_ou_acheter_pres(c: Candidature) -> bool:
    # La question nomme deux entités de nature différente, et elles se
    # disputent la résolution : « le plus proche de microTech pour un
    # P4-AR » prenait *microTech* pour l'objet. On repère donc les lieux
    # d'abord, puis on cherche l'objet **en les excluant**.
    coupe = coupe_sur_proximite(c.question)
    if coupe is None:
        return False
    avant, apres = coupe
    # Le lieu est ce qui suit la préposition, l'objet ce qui la précède.
    # Chercher les deux dans la phrase entière les faisait se voler
    # mutuellement.
    lieu = _lieux_nommes(c.con, apres)
    objet = extract_entity(c.con, avant, c.tool.entity_types,
                           exclus=_mots_d_intention(None, avant))
    if objet is None:
        # « le point de vente le plus proche de microTech pour un P4-AR »
        # met l'objet après le lieu : on cherche alors dans ce qui reste,
        # une fois le lieu retiré.
        reste = " ".join(m for m in apres.split()
                         if m not in {x for l in lieu
                                      for x in normalize(l).split()})
        objet = extract_entity(c.con, reste, c.tool.entity_types,
                               exclus=_mots_d_intention(None, reste))
    # « Où **les** acheter le plus proche de Lorville » : le pronom remplace
    # l'objet, qui vient d'être nommé par la réponse précédente. Sans ce
    # repli, la question partait chez `nearest_locations` et rendait les
    # lieux autour de Lorville — exact, et sans rapport.
    if objet is None and c.contexte:
        repris = resolve(c.con, c.contexte, entity_types=c.tool.entity_types,
                         limit=1)
        if repris.best is not None and repris.best.score >= 85.0:
            objet = (c.contexte, repris.best.score)
    if not lieu or objet is None:
        return False
    c.args[c.tool.arg] = objet[0]
    c.args["depuis"] = lieu[0]
    # « Où **louer** un Prospector proche de Crusader » : mêmes relevés UEX,
    # autre liste d'offres — remarque de l'utilisateur, la question limpide
    # tombait sur les livrées faute de chemin.
    if re.search(r"\blou[ée]?[rs]?\b|\blocation\b", normalize(c.question)):
        c.args["portee"] = "location"
    return True


@preparateur("get_price")
def _prep_price(c: Candidature) -> bool:
    # Le verbe décide de ce qu'on montre. Sans ça, « où acheter » rendait
    # aussi la recette et le butin — exact, mais noyé.
    c.args["portee"] = detect_portee(c.question)
    # Ligne 2 de la grille : « où acheter un Coda à Stanton » listait les
    # boutiques de Pyro sans un mot.
    systeme = extract_system(c.question)
    if systeme:
        c.args["systeme"] = systeme
    # Ligne 3 : « le prix de l'iron et de l'or » jetait l'or en silence.
    morceaux = re.split(r"\bet\b|,", normalize(c.question))
    if len(morceaux) >= 2:
        exclus = _mots_d_intention(None, c.question)
        autres: list[str] = []
        for morceau in morceaux:
            # « L'or » est refusé sur les types mixtes — la garde des grammes
            # courts ne s'ouvre qu'aux appels tout-minerai. On retente donc
            # le morceau en (resource, commodity), où l'exception mesurée
            # s'applique.
            trouve = (extract_entity(c.con, morceau, c.tool.entity_types,
                                     exclus=exclus)
                      or extract_entity(c.con, morceau,
                                        ("resource", "commodity"),
                                        exclus=exclus))
            if trouve and normalize(trouve[0]) != normalize(c.gram) \
                    and trouve[0] not in autres:
                autres.append(trouve[0])
        if autres:
            c.args["autres"] = autres
    return True


@preparateur("get_item_stats")
def _prep_item_stats(c: Candidature) -> bool:
    # Une question ciblée mérite une réponse ciblée : « combien de balles »
    # rend les balles, pas les huit lignes de la fiche.
    c.args["stat"] = queries.detect_item_stat(c.question)
    return True


@preparateur("get_ship_stats")
def _prep_ship_stats(c: Candidature) -> bool:
    c.args["stat"] = queries.detect_ship_stat_ou_rien(c.question)
    return True


@preparateur("get_ship_components")
def _prep_ship_components(c: Candidature) -> bool:
    # L'outil ne reçoit que l'entité extraite — « cutlass » — et ne peut
    # donc pas deviner quel composant est demandé. La question entière lui
    # sert à le détecter, comme pour `get_compatible_items`.
    c.args["category"] = c.question
    return True


@preparateur("compare_ships")
def _prep_compare_ships(c: Candidature) -> bool:
    # Comme `compare_items`, cet outil n'a pas d'entité à résoudre : il lui
    # faut son propre garde-fou, sinon « le meilleur canon » lui revient. Le
    # vocabulaire de vaisseau doit être présent, ou deux vaisseaux doivent
    # être nommés.
    norm = normalize(c.question)
    parle_vaisseau = any(
        mot in norm for mot in
        ("vaisseau", "ship", "scu", "fret", "cargo", "equipage", "soute")
    )
    if not parle_vaisseau and len(queries._ships_nommes(c.con, c.question)) < 2:
        return False
    c.args["stat"] = queries.detect_ship_stat(c.question)
    return True


@preparateur("get_compatible_items")
def _prep_compatible_items(c: Candidature) -> bool:
    # Le vaisseau se résout, donc l'outil se déclenche — mais il ne sait
    # parler que d'armes. « Quel bouclier sur un Cutlass » répondait par la
    # liste des canons.
    if _hors_armement(c.question):
        return False
    c.args["category"] = c.question
    return True


# --------------------------------------------------------- 38. les activités

#: Le vocabulaire que `activites_a_faire` **exige**. C'est le garde-fou des
#: outils sans entité, écrit après `vaisseaux_au_seuil` (qui répondait 240
#: vaisseaux à une question sur les armes) et `missions_les_mieux_payees`
#: (qui classait des payes sur « quelle mission avant ? »). Sans lui,
#: « qu'est-ce que je fabrique avec ça » lui reviendrait.
_MOTS_D_ACTIVITE = re.compile(
    r"\bactivites?\b|\bon fait quoi\b|\bquoi faire\b|\bque faire\b|"
    r"\bque fait ?on\b|\bqu est ce qu on (?:fait|peut faire)\b|"
    r"\bquoi jouer\b|\bon joue a quoi\b|\bs (?:amuser|occuper)\b|"
    r"\bpropose\w* (?:moi )?(?:une|des)\b"
)

#: « à trois », « on est 4 », « en solo ». Les nombres écrits en toutes
#: lettres comptent : c'est la forme la plus tapée en français au-delà de
#: « on est 3 ».
_NOMBRES_ECRITS = {"seul": 1, "solo": 1, "un": 1, "une": 1, "deux": 2,
                   "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
                   "huit": 8, "neuf": 9, "dix": 10}


def _joueurs_dans(question: str) -> int | None:
    """Combien on est, lu dans la phrase — jamais deviné.

    Décision de l'utilisateur (2026-08-13) : « c'est le joueur qui indique
    combien de joueurs, et s'il ne le dit pas tu dois demander ». Il n'y a
    donc **aucune valeur par défaut** ici, et le `None` est un résultat.
    """
    norm = normalize(question)
    # « à 3 », « on est 4 », « à 3 joueurs », « en duo ». On borne à deux
    # chiffres : un nombre plus long est une paye ou un seuil, pas un groupe.
    lu = re.search(r"\b(?:a|on est|nous sommes|groupe de|equipe de)\s+(\d{1,2})\b"
                   r"|\b(\d{1,2})\s+(?:joueurs?|personnes?|potes?|copains?)\b",
                   norm)
    if lu:
        valeur = int(lu.group(1) or lu.group(2))
        return valeur if 1 <= valeur <= 20 else None
    if re.search(r"\bduo\b|\ba deux\b", norm):
        return 2
    if re.search(r"\btrio\b", norm):
        return 3
    if re.search(r"\bsolo\b|\btout seul\b|\bseul\b", norm):
        return 1
    for mot, valeur in _NOMBRES_ECRITS.items():
        if re.search(rf"\ba {mot}\b|\bon est {mot}\b", norm):
            return valeur
    return None


def _envies_dans(question: str) -> dict:
    """Ce que la phrase demande d'autre : combat, mission, PvP, durée."""
    norm = normalize(question)
    args: dict = {}

    if re.search(r"\bsans (?:se )?(?:battre|combat|combattre|tirer)\b|"
                 r"\bpas de combat\b|\btranquille\b|\bcalme\b|\bpepere\b", norm):
        args["combat"] = "aucun"
    elif re.search(r"\ba pied\b|\bfps\b|\binfanterie\b|\bau sol\b", norm):
        args["combat"] = "fps"
    elif re.search(r"\ben vaisseau\b|\bcombat spatial\b|\bdogfight\b|"
                   r"\bvol\b", norm):
        args["combat"] = "vaisseau"

    if re.search(r"\bsans mission\b|\bsans contrat\b|\blibre\b", norm):
        args["mission"] = "non"
    elif re.search(r"\bavec (?:une |des |)missions?\b|\bsur contrat\b|"
                   r"\bdes contrats\b", norm):
        args["mission"] = "oui"

    if re.search(r"\bsans pvp\b|\bpas de pvp\b|\bsans (?:les )?autres joueurs\b|"
                 r"\bpve\b", norm):
        args["pvp"] = "non"

    # « en une heure », « on a deux heures », « rapide ». La durée lue est un
    # **plafond**, jamais une cible.
    heures = re.search(r"\b(?:en|dans|on a|j ai)\s+(\d{1,2})\s*h(?:eures?)?\b", norm)
    if heures:
        args["duree_max_minutes"] = int(heures.group(1)) * 60
    elif re.search(r"\b(?:en )?une heure\b", norm):
        args["duree_max_minutes"] = 60
    elif re.search(r"\brapide\b|\bcourt\b|\bpas longtemps\b|\bvite fait\b", norm):
        args["duree_max_minutes"] = 60

    for systeme in ("stanton", "pyro", "nyx"):
        if re.search(rf"\b{systeme}\b", norm):
            args["systeme"] = systeme.capitalize()
            break
    return args


@preparateur("activites_a_faire")
def _prep_activites(c: Candidature) -> bool:
    # Le garde-fou de vocabulaire, obligatoire pour un outil sans entité.
    if not _MOTS_D_ACTIVITE.search(normalize(c.question)):
        return False
    c.args.update(_envies_dans(c.question))
    c.args["joueurs"] = _joueurs_dans(c.question)
    # Il résout ses propres critères : le contrôle de doute sur l'entité ne
    # s'applique pas — il n'y en a aucune à expliquer.
    c.entites_maitrisees = True
    return True


#: Le vocabulaire que `par_ou_commencer` **exige**. Il ne recoupe aucun mot
#: de `_MOTS_D_ACTIVITE` : « on fait quoi ce soir » et « par où je commence »
#: sont deux questions, et un garde-fou commun avalerait les deux.
#:
#: **« Débutant » y est, « nouveau » n'y est pas.** Mesuré en écrivant :
#: « nouveau » seul attrape « quoi de neuf », « les nouveaux vaisseaux » et
#: « nouvelle mission » — c'est un mot de patch bien plus souvent qu'un mot
#: de joueur. Il n'entre qu'accolé à « joueur ».
_MOTS_DE_DEBUT = re.compile(
    r"\bpar (?:ou|quoi) (?:je |on |tu |)(?:commenc|demarr)\w*|"
    r"\b(?:je|on) (?:debute|commence)\b|\bdebutant\w*\b|"
    r"\bnouveau\w* joueur\w*\b|\bje viens de (?:commencer|debuter)\b|"
    r"\bpremiere fois que je joue\b|\bjamais joue\b|"
    r"\bquand on debute\b|\bpour bien demarrer\b"
)


@preparateur("par_ou_commencer")
def _prep_par_ou_commencer(c: Candidature) -> bool:
    if not _MOTS_DE_DEBUT.search(normalize(c.question)):
        return False
    # Le parcours accepte les mêmes envies que « quoi faire » — « je débute,
    # je veux du combat à pied » est une question complète. Le nombre de
    # joueurs aussi : un débutant qui joue avec deux amis n'a pas le même
    # point d'entrée. Il reste `None` quand la phrase ne le dit pas.
    c.args.update(_envies_dans(c.question))
    c.args["joueurs"] = _joueurs_dans(c.question)
    c.entites_maitrisees = True
    return True


#: Le vocabulaire que `quoi_de_neuf` exige. Même discipline que les autres
#: outils sans entité : « neuf », « nouveau » et « patch » traînent dans
#: trop de questions pour servir de garde-fou à eux seuls.
_MOTS_DE_PATCH = re.compile(
    r"\bquoi de (?:neuf|nouveau)\b|\bnouveautes?\b|\bpatch\b|"
    r"\bprochaine? (?:version|mise a jour)\b|\bnotes? de version\b|"
    r"\bqu est ce qui (?:arrive|change|sort)\b|"
    # **« Il y a quoi dans la 4.10 » : c'est le chiffre qui tranche.**
    # La locution est partagée avec `que_trouve_t_on` (« il y a quoi dans
    # la soute »), et les deux sortaient à 4,0 pile. Un nombre après
    # « dans la » désigne une version, jamais un lieu — aucun lieu du
    # starmap ne s'appelle par un nombre. `normalize` retire le point, donc
    # « 4.10 » arrive en « 4 10 » : on ne cherche que le premier chiffre.
    r"\bil y a quoi dans la \d")


@preparateur("quoi_de_neuf")
def _prep_quoi_de_neuf(c: Candidature) -> bool:
    if not _MOTS_DE_PATCH.search(normalize(c.question)):
        return False
    # Il ne consomme aucune entité : rien à expliquer au contrôle de doute.
    c.entites_maitrisees = True
    return True


@preparateur("fiche_activite")
def _prep_fiche_activite(c: Candidature) -> bool:
    # L'activité n'est pas dans le résolveur d'entités : elle a son propre
    # index d'alias, minuscule et strict. Sans correspondance, on abandonne
    # l'intention — « c'est quoi Grim HEX » doit rester chez `decrire`.
    cle = queries.resoudre_activite(c.con, c.question)
    if cle is None:
        return False
    c.args["query"] = cle
    c.entites_maitrisees = True
    return True
