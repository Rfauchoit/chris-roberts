"""La fiche « décris-moi » : prose, lieux, pratique d'une mission."""

from __future__ import annotations

import re
from typing import Any

from .socle import (
    _FAMILLE_LIEU,
    _GABARIT,
    _RENDERERS,
    _de,
    _nombre,
    _plural,
    enumerate_fr,
    question_de_suite,
    speakable_title,
)
from .vaisseaux import role_fr


# Ce qu'est un lieu, et la préposition qui va avec. La distinction demandée
# dans le journal : « faire la différence entre un POI SUR une planète, ou
# autour ». Elle se lit dans le type amont — un avant-poste est posé au sol,
# une station orbite.
_TYPE_LIEU = {
    "Manmade": ("une station", "en orbite autour de"),
    "Manmade_VisibleOnInteraction": ("une installation", "en orbite autour de"),
    "Outpost": ("un avant-poste", "sur"),
    "Outpost_InvalidQT": ("un avant-poste", "sur"),
    "LandingZone": ("une zone d'atterrissage", "sur"),
    "Moon": ("une lune", "de"),
    "Planet": ("une planète", "de"),
    "Star": ("une étoile", "de"),
    "Asteroid": ("un astéroïde", "dans"),
    "Asteroid_ValidQT": ("un astéroïde", "dans"),
    "PointOfInterest": ("un point d'intérêt", "près de"),
    "NavPoint": ("un point de navigation", "près de"),
    "Anomaly": ("une anomalie", "près de"),
}


# Nommer la source sert à quelque chose de concret : le joueur qui a installé
# StarTrad retrouvera ce texte mot pour mot dans son jeu, celui qui ne l'a pas
# verra l'anglais. La phrase ne serait pas la même.
_SOURCES_FR = {
    "circuspes": "du Cirque Lisoir (StarTrad)",
    "wiki": "du Star Citizen Wiki",
}

# L'ordre compte : on lit une fiche d'organisation comme on la présenterait —
# d'abord où elle est, puis depuis quand, puis qui la mène.
_ETAT_CIVIL = {
    "headquarters": "Siège",
    "founded": "Fondée en",
    "leadership": "Direction",
    "area": "Territoire",
    "focus": "Activité",
}

_ARTICLE_GENRE = {"personne": "", "lieu": "un lieu", "vaisseau": "un vaisseau",
                  "objet": "un objet", "mission": "une mission",
                  "constructeur": "un constructeur",
                  "commodite": "une marchandise", "minerai": "un minerai"}

_STATUT_PRODUCTION = {
    "flight-ready": "prêt à voler",
    "in-concept": "encore en concept",
    "in-production": "en production",
}

_TYPE_FACTION = {
    "Lawful": "légale", "Unlawful": "hors-la-loi",
    "PrivateSecurity": "sécurité privée", "LawEnforcement": "force de l'ordre",
}
_REACTION_FACTION = {
    "Friendly": "amicale", "Neutral": "neutre", "Hostile": "hostile",
}


# Ce que le type de gestionnaire dit d'un objectif. C'est **tout** ce que le
# jeu publie de lisible : le reste est un nom de debug.
_OBJECTIF_FR = {
    "ObjectiveHandler_Hauling": "convoyer une cargaison",
    "ObjectiveHandler_NearLocation": "se rendre sur place",
    "ObjectiveHandler_MeetAndTalk": "rencontrer quelqu'un",
    "ObjectiveHandler_PlayerAttached": "une cible qui te suit",
    "ObjectiveHandler_EntityAttached": "une cible liée à un objet",
    "ObjectiveHandler_Local": "intervenir sur zone",
}


def _lisible(debug: str | None) -> str | None:
    """« HijackedShip_Caterpillar » → « Hijacked Ship Caterpillar ».

    Un nom de debug n'est pas une consigne, mais découpé il oriente : le rendu
    le présente comme un repère, pas comme une phrase du jeu.
    """
    if not debug:
        return None
    # Découpage **maison** et non `split_class_name` : celui-ci écarte les mots
    # de bruit de scunpacked — « Item », « SC » — et met tout en minuscules.
    # Utile pour résoudre une entité, néfaste ici : « Rayari_RecoverItem »
    # devenait « rayari recover », qui perd le verbe de l'objectif.
    mots = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", debug.replace("_", " "))
    mots = " ".join(mots.split())
    return mots or None


def _situation(lieu: dict[str, Any]) -> str:
    """« Hickes Research Outpost, sur Cellin (lune de Crusader), Stanton ».

    Un nom d'avant-poste seul ne situe personne : la réponse citait « Hickes
    Research Outpost » sans dire ni le système ni la planète. Le chemin complet
    est en base — « Stanton / Crusader / Cellin / Hickes Research Outpost » —
    il suffisait de le lire.
    """
    nom = lieu.get("nom") or "?"
    morceaux = [m.strip() for m in (lieu.get("chemin") or "").split("/")
                if m.strip()]
    if len(morceaux) < 2:
        return nom
    systeme, corps = morceaux[0], morceaux[1:-1]
    if not corps:
        return f"{nom} ({systeme})"
    if len(corps) == 1:
        return f"{nom}, sur {corps[0]} ({systeme})"
    # Deux niveaux : la planète porte la lune, la lune porte l'avant-poste.
    return f"{nom}, sur {corps[-1]} (lune de {corps[-2]}, {systeme})"


def _singulier_de_lieu(genre: str) -> str:
    """« Outpost » → « avant-poste », pour que `_plural` fasse son travail.

    `_FAMILLE_LIEU` est déjà au pluriel : lui retirer un « s » donnait
    « lieuxs » sur les types absents de la table. Les singuliers existent dans
    `_TYPE_LIEU`, article compris — il suffit de le retirer.
    """
    article, _ = _TYPE_LIEU.get(genre, ("", ""))
    if article:
        return article.split(" ", 1)[1]
    return _FAMILLE_LIEU.get(genre, "lieux").removesuffix("s") or "lieu"


def _bloc_lieux(bloc: dict[str, Any], etiquette: str,
                precision: str) -> list[str]:
    """Un bloc de lieux, situé et sans ambiguïté sur son rôle.

    « Où ça se passe » et « où la prendre » se confondaient : les deux
    étiquettes se ressemblaient et rien ne disait laquelle était laquelle.
    Chacune porte donc sa précision entre parenthèses.
    """
    if not bloc or not bloc["total"]:
        return []

    # **Couvrir tout un système, c'est une réponse, pas une liste.** Remarque
    # de l'utilisateur : « c'est dans l'intégralité de Stanton qu'on peut la
    # prendre donc pas besoin de tout mettre ». Trente-quatre avant-postes
    # énumérés laissent croire à une contrainte alors qu'il n'y en a aucune.
    lignes = [f"- **{etiquette}** — _{precision}_"]
    partout = bloc.get("partout") or []
    manquants = bloc.get("manquants") or {}

    for systeme in partout:
        # « Tu peux dire partout dans Stanton **sauf** » — nommer les trous
        # vaut mieux qu'arrondir : c'est là qu'un joueur perd son temps.
        trous = manquants.get(systeme) or []
        sauf = (f", sauf {enumerate_fr(trous, limit=4)}"
                if trous and len(trous) <= 4 else "")
        lignes.append(f"    - partout dans **{systeme}**{sauf}")

    # Le reste se groupe par type plutôt que de s'énumérer : « 8 planètes,
    # 20 astéroïdes » se lit, trente-six noms non.
    for systeme, genres in (bloc.get("familles") or {}).items():
        # On groupe sur le **libellé rendu**, pas sur le type brut :
        # `Asteroid` et `Asteroid_ValidQT` se disent tous deux « astéroïde »,
        # et sortaient « 12 astéroïdes, 9 astéroïdes ».
        cumul: dict[str, int] = {}
        for genre, compte in genres.items():
            mot = _singulier_de_lieu(genre)
            cumul[mot] = cumul.get(mot, 0) + compte
        detail = enumerate_fr(
            [_plural(n, mot)
             for mot, n in sorted(cumul.items(), key=lambda x: -x[1])], limit=5)
        lignes.append(f"    - dans **{systeme}** : {detail}")

    if not partout and not bloc.get("familles"):
        for lieu in bloc.get("lieux") or []:
            lignes.append(f"    - {_situation(lieu)}")
        reste = bloc["total"] - len(bloc.get("lieux") or [])
        if reste > 0:
            lignes.append(f"    - … et {_plural(reste, 'autre')}")

    # Le compte ne s'annonce que quand on a résumé : sous une liste qui
    # montre déjà tout, « 1 point au total » est du bruit.
    if bloc["total"] > len(lignes) - 1:
        lignes.append(f"    - _{_plural(bloc['total'], 'point')} au total_")
    lignes += _bloc_complexes(bloc.get("complexes"))
    return lignes


def _bloc_complexes(complexes: dict[str, Any] | None) -> list[str]:
    """Dans quels complexes se trouve la salle où se joue la mission.

    « Engineering Wing » se présentait comme un lieu et n'en est pas un : le
    starmap ne le connaît pas, c'est une **salle** à l'intérieur d'un
    complexe. Remarque de l'utilisateur : « Engineering Wing c'est pas
    suffisant, c'est dans les complexes Onyx que ça se passe ».

    Le rattachement vient de `labels.json`, qui nomme le site et ses salles
    dans une même famille de clés ; le nombre de complexes et les corps qui
    les portent viennent du starmap. Le contrat, lui, ne cite que la salle —
    c'est le serveur qui choisit le complexe à la génération, et le rendu le
    dit pour qu'on ne parte pas en chercher un en particulier.
    """
    if not complexes:
        return []
    porteurs = complexes.get("porteurs") or []
    lunes = complexes.get("lunes") or 0
    ou = (f", répartis sur {_plural(lunes, 'lune')} "
          f"{_de(enumerate_fr(porteurs, limit=6))}") if porteurs and lunes else ""
    return [f"    - dans un complexe **{complexes['famille']}** — "
            f"{_plural(complexes['total'], 'complexe')} identiques{ou}",
            "    - _le contrat ne nomme que la salle : le complexe est tiré à "
            "la génération, n'importe lequel fait l'affaire_"]


def _bloc_chaine(avant: list[str], apres: list[str]) -> list[str]:
    """La chaîne de missions, dans l'ordre où on la joue.

    Remarque de l'utilisateur : « il y a aussi une notion de mission à faire
    avant qui ouvre une autre mission ». Une liste à plat ne dit pas ça — elle
    se lit comme des conditions cumulées, alors que ce sont des **étapes**.
    On les numérote, et on place la mission demandée à son rang pour que le
    joueur voie où il en est.

    Ce qui suit se dit aussi : la mission du blueprint est rarement la
    dernière, et savoir qu'on n'a pas fini vaut mieux que de le découvrir.
    """
    # Une seule étape avant : la ligne « Prérequis » la nomme déjà en entier,
    # le bloc ne ferait que la répéter.
    if len(avant) < 2 and not apres:
        return []
    lignes: list[str] = []

    if len(avant) >= 2:
        rang = len(avant) + 1
        lignes.append(f"- **La chaîne** — _celle-ci est la {_ordinal(rang)} "
                      f"étape_")
        for numero, titre in enumerate(avant[:_CHAINE_MAX], 1):
            lignes.append(f"    {numero}. {speakable_title(titre)}")
        if len(avant) > _CHAINE_MAX:
            lignes.append(f"    - … et {_plural(len(avant) - _CHAINE_MAX, 'étape')} "
                          "avant d'arriver à celle-ci")
        lignes.append(f"    {rang}. **cette mission**")

    if apres:
        lignes.append(f"- **Ensuite** — _ce que cette mission ouvre_")
        for titre in apres[:_CHAINE_MAX]:
            lignes.append(f"    - {speakable_title(titre)}")
        if len(apres) > _CHAINE_MAX:
            lignes.append(f"    - … et {_plural(len(apres) - _CHAINE_MAX, 'autre')}")

    return lignes


# Une chaîne de vingt-cinq titres est exacte et illisible ; le compte annoncé
# reste celui de la donnée complète (R4).
_CHAINE_MAX = 8

_ORDINAUX = {1: "1re", 2: "2e", 3: "3e"}


def _ordinal(n: int) -> str:
    return _ORDINAUX.get(n, f"{n}e")


# Les mêmes jetons, pour un briefing resté anglais. Le remplacement unique
# « quelqu'un » faisait deux dégâts, trouvés par le balayage : un jeton de
# **lieu** devenait « quelqu'un », et un briefing non traduit devenait du
# franglais — « quelqu'un and kill everyone who's there ». La langue du
# remplissage suit celle du texte.
_JETON_EN = {
    "location": "the marked location", "address": "the given address",
    "destination": "the destination", "ship": "the target ship",
    "ambushtarget": "the target ship", "targetname": "the target",
    "target": "the target", "creature": "the creature",
    "objectivesetupitem": "the item to recover",
    "item": "the goods", "commodity": "the goods",
    "cargo": "the cargo", "itemstodestroy": "the items to destroy",
    "mineabletype": "the requested ore",
    "resourcetype": "the requested resource",
    "amount": "the amount", "total": "the total",
    "quantity": "the quantity", "contractor": "the contractor",
    "player": "you",
}


def _jetons_du_briefing(texte: str, francais: bool) -> str:
    """Les jetons d'un briefing, remplacés dans la langue du briefing.

    À la différence de `_consigne`, on ne touche ni aux sauts de ligne ni à
    la ponctuation : un briefing est un texte en paragraphes, pas une ligne
    de consigne. Le balisage d'ATH (`<EM4>…`) saute aussi — il fuyait tel
    quel dans les briefings de fret : « (<EM4>32 SCU… ».
    """
    propre = re.sub(r"</?[A-Za-z0-9]+>", "", texte)
    propre = re.sub(r"%l?s", "", propre)
    table = _JETON_FR if francais else _JETON_EN
    defaut = "l'endroit indiqué" if francais else "the objective"

    def remplacer(trouve: re.Match) -> str:
        quoi = trouve.group(1).split("|")[0].strip().lower()
        return table.get(quoi, defaut)

    propre = re.sub(r"~mission\(([^)]*)\)s?", remplacer, propre)
    propre = re.sub(r"\[([^\[\]]*)\]s?", remplacer, propre)
    if francais:
        propre = _ARTICLE_DOUBLE.sub(_garder_un_article, propre)
        for motif, remplacement in _CONTRACTIONS:
            propre = motif.sub(remplacement, propre)
    return propre


def _consigne(texte: str) -> str:
    """Une consigne de mission, débarrassée de son balisage de jeu.

    Le texte brut porte trois choses qui ne se lisent pas : les jetons que le
    serveur remplace à la génération (`~mission(Location|Address)`), le
    balisage de couleur de l'ATH (`<EM4>…</EM4>`) et les marqueurs de format
    (`%ls`). On remplace le jeton par ce qu'il désigne — « le lieu », « la
    cible » — plutôt que de le retirer : sans lui la phrase perd son
    complément (« Débarquez à. »).
    """
    propre = re.sub(r"</?[A-Za-z0-9]+>", "", texte)
    propre = re.sub(r"%l?s", "", propre)

    def remplacer(trouve: re.Match) -> str:
        quoi = trouve.group(1).split("|")[0].strip().lower()
        return _JETON_FR.get(quoi, "l'endroit indiqué")

    # **Deux écritures pour le même jeton.** L'anglais du jeu écrit
    # `~mission(Location)`, le fichier français du Cirque Lisoir écrit
    # `[Location]` — c'est la même correspondance que sur les titres de
    # mission. Ne traiter qu'une des deux laissait des crochets dans la
    # réponse française, qui est justement celle qu'on affiche.
    # Le « s » collé au jeton (« [MineableType]s ») marque un pluriel que nos
    # tournures portent déjà : le garder donne « le minerai demandés ».
    propre = re.sub(r"~mission\(([^)]*)\)s?", remplacer, propre)
    propre = re.sub(r"\[([^\[\]]*)\]s?", remplacer, propre)
    propre = _ARTICLE_DOUBLE.sub(_garder_un_article, propre)
    for motif, remplacement in _CONTRACTIONS:
        propre = motif.sub(remplacement, propre)
    return re.sub(r"\s{2,}", " ", propre).strip(" .") + "."


# La consigne source porte souvent déjà l'article — « récupérer la [Item] » —
# et nos tournures aussi, d'où « la la marchandise ». On n'en garde qu'un.
# **Le plus long d'abord.** Écrite « le|la|les », l'alternation retenait « le »
# dans « les » et laissait un « s » orphelin : « saboter le s objets ».
_ARTICLE_DOUBLE = re.compile(
    r"\b(aux|au|des|du|les|le|la|l')\s+(les|le|la|l')\b\s*", re.IGNORECASE)


def _garder_un_article(trouve: re.Match) -> str:
    """Quel des deux articles survit.

    Après une forme contractée — « au », « du » — l'article suivant disparaît :
    « au le vaisseau » devient « au vaisseau ». Entre deux articles simples,
    c'est celui de **notre** tournure qui gagne, parce que lui s'accorde avec
    ce qu'on a écrit : « la [ItemsToDestroy] » doit donner « les objets ».
    """
    premier, second = trouve.group(1).lower(), trouve.group(2)
    if premier in ("au", "aux", "du", "des"):
        return trouve.group(1) + " "
    return second + ("" if second.endswith("'") else " ")


# Ce que le serveur substitue à la génération du contrat. On nomme la **nature**
# de ce qui viendra : un joueur qui lit « rendez-vous au lieu indiqué » sait
# quoi chercher, là où « rendez-vous à ~mission(Location) » ne veut rien dire.
# Dix-sept jetons couvrent la totalité des consignes — comptés en base, pas
# devinés. Un défaut générique aurait donné « tendre une embuscade à l'endroit
# indiqué » là où le jeton désigne un **vaisseau**.
_JETON_FR = {
    "location": "le lieu indiqué", "address": "l'adresse indiquée",
    "destination": "la destination", "ship": "le vaisseau visé",
    "ambushtarget": "le vaisseau visé", "targetname": "la cible",
    "target": "la cible", "creature": "la créature visée",
    "objectivesetupitem": "l'objet à récupérer",
    "item": "la marchandise", "commodity": "la marchandise",
    "cargo": "la cargaison", "itemstodestroy": "les objets à détruire",
    "mineabletype": "le minerai demandé",
    "resourcetype": "la ressource demandée",
    "amount": "la quantité", "total": "le total",
    "quantity": "la quantité", "contractor": "le commanditaire",
    "player": "toi",
}

# « à le lieu » ne se dit pas. Une consigne est écrite autour d'un jeton qu'on
# remplace par un groupe nominal : la préposition qui le précède doit se
# contracter, comme partout ailleurs dans le rendu (R6). L'ordre compte — on
# contracte, puis on répare le cas où le groupe commence par une voyelle.
_CONTRACTIONS = (
    (re.compile(r"\bà les\b"), "aux"), (re.compile(r"\bà le\b"), "au"),
    (re.compile(r"\bde les\b"), "des"), (re.compile(r"\bde le\b"), "du"),
    (re.compile(r"\bau (l')"), r"à \1"), (re.compile(r"\bdu (l')"), r"de \1"),
    # « la quantité/le total SCU » est un compteur de progression, pas deux
    # choses : le jeu y affiche « 12/40 ».
    (re.compile(r"\bla quantité/le total\b"), "la quantité demandée"),
)


def _bloc_vecu(data: dict[str, Any]) -> list[str]:
    """Ce que nos parties ont fait de cette mission.

    **On ne rend pas un taux de réussite, et c'est mesuré.** Sur les 30
    contrats rapprochés : 94 réussies, **2 échouées**, 19 abandonnées. Un
    taux de réussite sortirait à 98 % partout et ne séparerait rien —
    exactement le piège du classement de progression, où ni la difficulté
    de CIG ni le rang exigé ne discriminaient.

    Ce qui sépare, c'est l'**abandon** : de 0 à 4 fois sur 6 selon la
    mission. Deux garde-fous avant de le dire :

    - **une part d'un quart au moins.** En dessous c'est du bruit, et le
      signaler ferait douter d'une mission que personne ne fuit ;
    - **quatre tentatives au moins.** Un abandon sur une seule partie ne
      décrit rien — « la mission qu'on laisse tomber le plus souvent »
      sur un échantillon de un serait un chiffre faux et plausible.
    """
    vecu = data.get("vecu")
    if vecu is None or not getattr(vecu, "tentatives", 0):
        return []
    faites = f"{vecu.tentatives} fois" if vecu.tentatives > 1 else "une fois"
    phrase = f"_Dans nos parties : prise **{faites}**"
    part = vecu.part_abandon
    if vecu.tentatives >= 4 and part and part >= 0.25:
        phrase += (f", abandonnée {vecu.abandonnees} fois — c'est une de "
                   f"celles qu'on laisse tomber")
    elif vecu.echouees:
        phrase += f", échouée {vecu.echouees} fois"
    if getattr(vecu, "sans_verdict", 0):
        phrase += (f", terminée {vecu.sans_verdict} fois sans verdict "
                   "dans le journal")
    return ["", phrase + ". Observé, pas un barème du jeu._"]


def _bloc_pratique(data: dict[str, Any]) -> list[str]:
    """Où prendre la mission, où elle se passe, ce qu'on y fait, et à quelles
    conditions.

    Demandé explicitement, puis précisé après relecture du journal : « je ne
    sais pas où ils se situent, ni système ni planète », « c'est là où se passe
    la mission ou bien là où il faut la prendre ? », « impossible de savoir
    s'il y a des prérequis ».
    """
    pratique = data.get("pratique")
    if not pratique:
        return _bloc_vecu(data)
    lignes: list[str] = []

    # Les conditions d'abord : inutile de chercher une mission qu'on ne peut
    # pas encore accepter.
    conditions = []
    if pratique.get("rang"):
        chez = f" chez {pratique['chez']}" if pratique.get("chez") else ""
        conditions.append(f"rang **{pratique['rang']}**{chez}")
    prealables = pratique.get("prealables") or []
    if len(prealables) == 1:
        conditions.append(f"avoir fait « {speakable_title(prealables[0])} »")
    elif prealables:
        # Au-delà d'une, on annonce le compte et on déroule la chaîne en
        # dessous : « A, B et C » sur une liste tronquée à trois cachait
        # quatre étapes et laissait croire à trois missions indépendantes.
        conditions.append(
            f"avoir fait les **{len(prealables)}** missions qui la précèdent")
    if conditions:
        lignes.append("- **Prérequis** : " + enumerate_fr(conditions, limit=4))
    else:
        lignes.append("- **Prérequis** : aucun")


    lignes += _bloc_chaine(prealables, pratique.get("debloque") or [])
    lignes += bloc_de_difficulte(pratique.get("difficulte"))

    lignes += _bloc_lieux(pratique.get("ou_prendre"), "Où la prendre",
                          "les terminaux où le contrat est proposé")
    lignes += _bloc_lieux(pratique.get("ou_faire"), "Où ça se passe",
                          "les lieux où il faut se rendre")

    objectifs = pratique.get("objectifs") or []
    if objectifs:
        # **La consigne rédigée quand elle existe.** Le jeu en écrit une pour
        # 496 contrats sur les 2 520 qui ont des objectifs, et le Cirque
        # Lisoir les traduit toutes. « Récupérez les données d'anomalie
        # énergétique dans l'aile d'ingénierie » vaut mieux que « intervenir
        # sur zone (Get Radiation Readings) ».
        consignes = [o for o in objectifs if o.get("consigne")]
        if consignes:
            lignes.append("- **Ce qu'on y fait**")
            for rang, objectif in enumerate(consignes, 1):
                lignes.append(f"    {rang}. {_consigne(objectif['consigne'])}")
            if not all(o.get("en_francais") for o in consignes):
                lignes.append("    - _consignes non traduites : elles "
                              "apparaissent en anglais dans le jeu aussi_")
        else:
            # Sinon, le nom de debug et le type de gestionnaire — tout ce que
            # le jeu publie pour les missions générées.
            vus: list[str] = []
            for objectif in objectifs:
                genre = _OBJECTIF_FR.get(objectif.get("handler") or "")
                repere = _lisible(objectif.get("debug_name"))
                morceau = genre or "objectif"
                if repere:
                    morceau += f" ({repere})"
                if morceau not in vus:
                    vus.append(morceau)
            lignes.append(
                f"- **Ce qu'on y fait** : {enumerate_fr(vus, limit=4)}")
    return lignes + _bloc_vecu(data)


def _entete_description(data: dict[str, Any]) -> str:
    """« **Nyx** — une étoile du système Nyx », « **X** — Mercenary, Y ».

    Factorisé : le même en-tête sert la description avec prose et celle qui
    n'en a pas, et les laisser diverger produirait deux fiches différentes
    pour la même entité selon que CIG a écrit un texte ou non.
    """
    nom = data["name"]
    ligne = data.get("ligne") or {}
    entete = f"**{nom}**"
    if data["genre"] == "lieu" and ligne.get("type_name"):
        quoi, _ = _TYPE_LIEU.get(ligne["type_name"], ("un lieu", ""))
        entete += f" — {quoi}"
        if ligne.get("system_name") and ligne["system_name"] != nom:
            entete += f" du système {ligne['system_name']}"
    elif data["genre"] == "vaisseau":
        role = role_fr(ligne.get("role") or ligne.get("career"))
        if role:
            entete += f" — {role} de {ligne.get('manufacturer_name') or 'constructeur inconnu'}"
    elif data["genre"] == "objet" and ligne.get("manufacturer_name"):
        entete += f" — {ligne['manufacturer_name']}"
    elif data["genre"] == "mission":
        # **Un jeton de gabarit n'est pas un commanditaire.** Deux
        # `mission_giver` sur 76 valent « ~mission(Contractor|BountyFrom) » —
        # le serveur choisit à la génération, nous n'avons que le gabarit. Le
        # résolveur l'écartait déjà depuis longtemps ; l'affichage, non, et le
        # balayage a trouvé 393 fiches qui le montraient en en-tête.
        detail = [d for d in (ligne.get("mission_type"),
                              ligne.get("mission_giver"),
                              ligne.get("system")) if d and not _GABARIT.search(d)]
        if detail:
            entete += " — " + ", ".join(detail)
    return entete


# Comment on récolte une ressource, et ce que ça change pour le joueur : on ne
# mine pas une épave et on ne récupère pas un filon.
_RECOLTE_FR = {
    "mineable": "se mine dans les filons et les astéroïdes",
    "cave_harvestable": "se ramasse à la main dans les grottes",
    "harvestable": "se ramasse à la main",
    "salvageable": "se récupère sur les épaves",
}

# Les cinq paliers de rareté que le jeu écrit dans le nom du gisement.
_RARETE_FR = {"common": "commun", "uncommon": "peu commun", "rare": "rare",
              "epic": "épique", "legendary": "légendaire"}


def _bloc_minerai(data: dict[str, Any]) -> list[str]:
    """Un minerai n'a aucun texte officiel, mais on sait des choses de lui.

    Le jeu ne publie **aucune** description pour les 557 ressources. Répondre
    « je n'ai pas de description » à « c'est quoi le Stileron » était exact et
    inutile : sa rareté et sa façon de se récolter sont en base, et c'est
    précisément ce que la question demande.
    """
    ligne = data.get("ligne") or {}
    lignes = []
    recolte = _RECOLTE_FR.get(ligne.get("kind") or "")
    if recolte:
        lignes.append(f"- **Récolte** : {recolte}")
    rarete = _RARETE_FR.get((ligne.get("tier") or "").lower())
    if rarete:
        lignes.append(f"- **Rareté** : {rarete}")
    return lignes


# Marqueurs d'état que CIG met en tête de la description d'un lieu. **Ce ne
# sont pas des jetons de gabarit** : 306 lieux sont fermés et 3 inaccessibles,
# et c'est une information que le joueur doit avoir avant de s'y rendre. Ce
# qui n'allait pas, c'était de l'afficher telle quelle — en anglais, entre
# crochets, au milieu d'une phrase française.
#
# **Les deux langues, comme pour les jetons de mission.** Le texte affiché est
# la traduction du Cirque Lisoir, qui écrit « [ FERMÉ ] » là où le jeu écrit
# « [ CLOSED ] ». Ne traiter que l'anglais laissait les crochets dans la
# réponse française — celle qu'on affiche justement. Le projet s'était déjà
# fait prendre sur `~mission(Location)` contre `[Location]`.
_STATUT_LIEU = (
    (re.compile(r"^\s*\[\s*(?:CLOSED|FERM[EÉ]E?)\s*\]\s*", re.I),
     "⚠️ **Fermé** — ce lieu n'est plus accessible en jeu."),
    (re.compile(r"^\s*\[\s*(?:NOT CURRENTLY ACCESSIBLE"
                r"|(?:ACTUELLEMENT )?INACCESSIBLE)\s*\]\s*", re.I),
     "⚠️ **Inaccessible pour l'instant** — le jeu ne l'ouvre pas encore."),
)


def statut_de_lieu(description: str | None) -> tuple[str | None, str]:
    """Sépare le marqueur d'état du texte qui le suit.

    Rend `(phrase d'état ou None, description nettoyée)`. Le marqueur passe en
    **tête** de la réponse : savoir qu'un endroit est fermé change la décision
    d'y aller, et le lire après trois lignes de prose est trop tard.
    """
    texte = description or ""
    for motif, phrase in _STATUT_LIEU:
        if motif.match(texte):
            return phrase, motif.sub("", texte, count=1).strip()
    return None, texte


# Les 22 services d'un lieu, en français. Vocabulaire **fermé** : le jeu n'en
# publie pas d'autres, et un service inconnu se rendrait tel quel plutôt que de
# disparaître. Les valeurs de la base restent anglaises par construction ;
# c'est ici qu'on traduit, au dernier moment.
_SERVICES_FR = {
    "Vehicle Services": "services véhicule",
    "Commodity Trading": "négoce de marchandises",
    "Loading Dock": "quai de chargement",
    "Buy Armor": "vente d'armures",
    "Buy Weapons": "vente d'armes",
    "Buy Clothing": "vente de vêtements",
    "Buy Ship Items/Weapons": "vente de composants et d'armes de vaisseau",
    "Buy Vehicles": "vente de véhicules",
    "Rent Vehicles": "location de véhicules",
    "Buy/Rent Vehicles": "vente et location de véhicules",
    "Food Court": "restauration",
    "Clinic": "clinique",
    "Hospital": "hôpital",
    "Docking": "amarrage",
    "Garage": "garage",
    "Refinery": "raffinerie",
    "Hangar (L)": "hangar L", "Hangar (XL)": "hangar XL",
    "Landing Pad (S)": "plateforme S", "Landing Pad (M)": "plateforme M",
    "Landing Pad (L)": "plateforme L", "Landing Pad (XL)": "plateforme XL",
}


def service_fr(nom: str) -> str:
    return _SERVICES_FR.get(nom, nom)


def _bloc_raffine(data: dict[str, Any]) -> list[str]:
    """Ce que devient une matière première une fois raffinée.

    Écrit par le jeu sur 30 commodités, avec un UUID. Le module de raffinage
    rapprochait les deux formes par le **nom** : « Raw Ice » → « Pressurized
    Ice » est justement le couple que le nom seul ne donne pas.
    """
    raffine = data.get("raffine_en")
    if not raffine or raffine == data.get("name"):
        return []
    return [f"- **Une fois raffiné** : {raffine}"]


def _bloc_lieu(data: dict[str, Any]) -> list[str]:
    """Ce qu'on sait d'un système : ce qu'il contient, et s'il est sûr.

    Ordre choisi pour un joueur qui arrive : d'abord **peut-on y aller sans
    risque**, ensuite **qu'y a-t-il**, enfin **par où l'on entre**. Le danger
    passe en premier parce qu'il change la décision, pas la culture générale.
    """
    geo = data.get("geographie") or {}
    services = geo.get("services") or []
    if not geo.get("systeme"):
        # **Une station n'est pas un système, et c'est là que la question se
        # pose.** « Je peux poser mon Hammerhead à Grim HEX ? » se lit dans la
        # taille du hangar ; la fiche géographique, elle, ne décrit que les
        # systèmes et rendait donc une liste vide pour tous les lieux où l'on
        # atterrit vraiment.
        return (["- **Sur place** : "
                 + enumerate_fr([service_fr(s) for s in services], limit=10)]
                if services else [])

    lignes: list[str] = []
    if geo.get("description_wiki"):
        # **Ce texte est en anglais, et on le dit.** Le wiki ne traduit pas son
        # champ `description` — seul `game_description` l'est — et le Cirque
        # Lisoir n'a rien sur les systèmes. Laisser un paragraphe anglais au
        # milieu d'une réponse française sans le signaler ferait croire à un
        # oubli ; c'est la seule prose qui existe sur Nyx.
        lignes += [geo["description_wiki"],
                   "*Fiche du star-citizen.wiki, non traduite.*", ""]

    if services:
        lignes.append("- **Sur place** : "
                      + enumerate_fr([service_fr(x) for x in services], limit=10))

    if geo.get("sans_loi"):
        lignes.append("- ⚠️ **Système sans loi** — aucune sécurité UEE, "
                      "les stations Gateway restent sûres.")
    elif geo.get("affiliation"):
        lignes.append(f"- **Juridiction** : {geo['affiliation']}")

    planetes, lunes = geo.get("planetes") or [], geo.get("lunes") or []
    if planetes:
        lignes.append("- **%s** : %s"
                      % (_plural(len(planetes), "planète"),
                         enumerate_fr(planetes, limit=8)))
    if lunes:
        lignes.append("- **%s** : %s" % (_plural(len(lunes), "lune"),
                                         enumerate_fr(lunes, limit=8)))

    villes = geo.get("villes") or []
    if villes:
        lignes.append("- **%s** : %s"
                      % (_plural(len(villes), "ville"),
                         enumerate_fr([f"{v} (sur {p})" if p else v
                                       for v, p in villes], limit=6)))

    sauts = geo.get("sauts") or []
    if sauts:
        # Un saut vers un système qu'on n'a pas ingéré se dit quand même : le
        # jeu le nomme, et taire une sortie ferait croire qu'elle n'existe pas.
        rendus = [s if connu else f"{s} (hors périmètre)" for s, connu in sauts]
        # Le pluriel se met sur le **nom**, pas à la fin du groupe : `_plural`
        # ajoutait un « s » à « point de saut » et écrivait « point de sauts ».
        lignes.append("- **%s** : vers %s"
                      % (_plural(len(sauts), "point de saut",
                                 "points de saut"),
                         enumerate_fr(rendus, limit=8)))

    stations = geo.get("stations") or []
    if stations:
        # Elles sont nombreuses — 54 dans Stanton — et leur intérêt est
        # d'être **nommables** : on en cite huit et on donne le compte,
        # comme les lunes. C'est la règle du groupement, pas de la liste.
        lignes.append("- **%s** : %s"
                      % (_plural(len(stations), "station"),
                         enumerate_fr(stations, limit=8)))

    if geo.get("avant_postes"):
        lignes.append("- **Avant-postes** : %d recensés" % geo["avant_postes"])
    return lignes


def render_description(data: dict[str, Any]) -> str:
    """Le texte officiel, et rien d'autre — puis une proposition s'il y a lieu.

    « Décris-moi » n'est pas « donne-moi les stats » : on répond ce que c'est,
    et on propose les chiffres seulement s'ils existent.
    """
    nom, texte = data["name"], data.get("description")
    ligne = data.get("ligne") or {}
    # Le marqueur d'état sort du texte avant tout le reste : « fermé » se lit
    # en tête, pas au détour d'un paragraphe. Et une description qui n'était
    # *que* ce marqueur devient vide, donc le repli sur les données joue.
    statut, texte = statut_de_lieu(texte)
    if statut:
        texte = texte or None
    if not texte:
        # **Une absence de prose n'est pas une absence de savoir.** L'étoile
        # *Nyx* n'a aucun texte officiel, et « nyx » répondait « je n'ai pas de
        # description » alors qu'on connaît ses trois planètes, ses six points
        # de saut et le fait qu'elle soit sans loi. Même chose pour une
        # mission : ses prérequis, ses lieux et ses objectifs sont en base, et
        # ce sont eux qu'on demande en disant « décris-moi ».
        secours = (_bloc_lieu(data) if data.get("geographie")
                   else _bloc_pratique(data) if data["genre"] == "mission"
                   else _bloc_minerai(data) if data["genre"] == "minerai"
                   else [])
        if secours or statut:
            tete = [_entete_description(data), ""]
            if statut:
                tete += [statut, ""]
            return "\n".join(tete + secours).strip()
        quoi = _ARTICLE_GENRE.get(data["genre"], "")
        return (f"Je n'ai pas de description officielle pour **{nom}**"
                + (f", {quoi} du jeu." if quoi else "."))

    entete = _entete_description(data)

    if data["genre"] == "mission":
        # Même gabarit que les titres : le serveur remplace « [TargetName] »
        # à la génération du contrat. Le laisser tel quel donne un texte qui
        # a l'air cassé.
        texte = _jetons_du_briefing(texte, francais=data.get("traduite", False))

    # **Le concret avant la couleur.** Pour une mission, le briefing du
    # commanditaire ne dit ni où la trouver ni ce qu'elle demande : on donne
    # d'abord la fiche pratique, et le texte devient une proposition.
    # `volet="texte"` demande explicitement le briefing : on saute la fiche.
    pratique = ([] if data.get("volet") == "texte"
                else _bloc_pratique(data) if data["genre"] == "mission" else [])
    if pratique:
        morceaux = [entete, ""] + pratique
        if texte:
            morceaux.append("")
            morceaux.append(question_de_suite(["le texte de la mission"]))
        return chr(10).join(morceaux)

    # L'état passe **avant** la prose : qu'un lieu soit fermé change la
    # décision d'y aller, et le lire après trois lignes d'ambiance est trop
    # tard. 306 lieux sont dans ce cas.
    morceaux = [entete, ""] + ([statut, ""] if statut else []) + [texte]
    # **La prose et les données ne s'excluent pas.** Pyro et Stanton ont un
    # texte officiel, Nyx non : sans cette ligne, les deux premiers rendaient
    # une phrase d'ambiance sans une planète ni un point de saut, et le
    # troisième la liste complète. La même question doit rendre la même sorte
    # de fiche, que CIG ait écrit un paragraphe ou pas.
    lieu = _bloc_lieu(data)
    if lieu:
        morceaux += [""] + lieu
    raffine = _bloc_raffine(data)
    if raffine:
        morceaux += [""] + raffine
    # Le bloc de lieu porte déjà la juridiction : la répéter donnait
    # « **Juridiction** : UEE » puis « Sous juridiction UEE. » à trois lignes
    # d'écart.
    if ligne.get("jurisdiction") and not lieu:
        morceaux.append(f"\nSous juridiction {ligne['jurisdiction']}.")
    civil = data.get("etat_civil")
    if civil:
        # En liste et non en phrase : ce sont des faits indépendants, et le bot
        # est écrit. Une phrase les enchaînerait avec des « et » pour rien.
        morceaux.append("")
        morceaux += [f"- {_ETAT_CIVIL[cle]} : **{civil[cle]}**"
                     for cle in _ETAT_CIVIL if cle in civil]
    profil = data.get("profil_faction") or {}
    types = profil.get("types") or []
    reactions = profil.get("reactions") or []
    if types or reactions:
        morceaux.append("")
        if types:
            morceaux.append("- **Type publié** : "
                            + enumerate_fr([_TYPE_FACTION.get(t, t) for t in types]))
        if reactions:
            libelle = ("Réactions par défaut publiées" if len(reactions) > 1
                       else "Réaction par défaut publiée")
            morceaux.append(f"- **{libelle}** : " + enumerate_fr([
                _REACTION_FACTION.get(r, r) for r in reactions]))
            if len(reactions) > 1:
                morceaux.append("  _Plusieurs fiches du jeu portent ce nom et "
                                "se contredisent ; je conserve les deux valeurs._")
            morceaux.append("  _C'est une disposition générale, pas la preuve "
                            "d'un tir à vue dans toutes les situations._")
    production = data.get("production") or {}
    if production.get("statut"):
        statut_prod = _STATUT_PRODUCTION.get(
            production["statut"], production["statut"])
        version = (f" pour la version {production['version']}"
                   if production.get("version") else "")
        morceaux.append(f"\n- **Statut de production** : {statut_prod}{version}")
        if production.get("foci"):
            morceaux.append("- **Rôle annoncé** : "
                            + enumerate_fr(production["foci"], limit=3))
        if production.get("loaners"):
            morceaux.append("- **Vaisseaux de prêt associés** : "
                            + enumerate_fr(production["loaners"], limit=4))
        date_wiki = ((production.get("fetched_at") or "")[:10]
                     or "date inconnue")
        morceaux.append(f"  _Données du Star Citizen Wiki relevées le "
                        f"{date_wiki}, distinctes des fichiers du jeu._")
    if data.get("role_legal") == "police":
        morceaux.append("\nC'est une force de l'ordre : elle peut t'arrêter.")
    elif data.get("role_legal") == "hors-la-loi":
        morceaux.append("\nC'est un groupe hors-la-loi : aucun droit légal "
                        "reconnu.")

    argent = data.get("argent_reel")
    if argent and argent.get("dollars"):
        # Le prix au pledge store, que les fichiers du jeu ne portent plus
        # depuis la 3.20. Distinct des aUEC : on le dit, sinon les deux
        # chiffres se confondent.
        morceaux.append(f"\nEn argent réel : **{_nombre(argent['dollars'])} $** "
                        "au pledge store.")
    if data.get("traduite"):
        # Le texte est de CIG, la traduction est communautaire. Ne pas le dire
        # laisserait croire que le jeu est localisé en français : il ne l'est
        # pas, et un joueur qui cherche cette phrase en jeu ne la trouvera pas.
        # Sauf s'il a installé StarTrad, justement — d'où le nom de la source.
        morceaux.append("\n*Traduction " + _SOURCES_FR.get(
            data.get("source_traduction") or "", "communautaire")
            + " ; le jeu n'a pas de texte français.*")
    elif texte and data["genre"] == "mission":
        # Le briefing d'une mission non couverte par le Cirque Lisoir reste
        # en anglais — même règle que la fiche de Nyx : un paragraphe anglais
        # au milieu d'une réponse française sans un mot ferait croire à un
        # oubli. Mesuré au balayage du 2026-08-07 : c'est un trou de
        # **couverture**, qui se signale et se compte, pas 1 159 bugs.
        morceaux.append("\n*Briefing en anglais — pas de traduction "
                        "communautaire pour cette mission.*")
    elif texte and data.get("entity_type"):
        # Même règle pour tout le reste — un lieu, un objet, une faction dont
        # la prose n'est pas couverte : CIG n'écrit pas de français, donc un
        # texte non traduit **est** anglais, et on le dit — comme le fait déjà
        # la fiche d'un constructeur sans traduction.
        morceaux.append("\n*Texte officiel en anglais — pas de traduction "
                        "communautaire.*")
    if data.get("stats_disponibles"):
        morceaux.append("")
        morceaux.append(question_de_suite(["ses caractéristiques"]))
    return "\n".join(morceaux)


def render_constructeur(data: dict[str, Any]) -> str:
    """« Quelle est la marque du Vanguard ? »"""
    entite = data.get("entite")
    if not entite or not data.get("marque"):
        nom = getattr(getattr(data.get("resolution"), "best", None), "name", None)
        return (f"Les fichiers du jeu ne donnent pas de constructeur pour "
                f"« {nom} »." if nom else "Je n'ai pas reconnu ce dont tu parles.")

    code = f" ({data['code']})" if data.get("code") else ""
    lignes = [f"**{entite['name']}** est construit par "
              f"**{data['marque']}**{code}."]

    if data.get("description"):
        lignes += ["", data["description"]]
        # R3 : les trois sources n'ont pas la même valeur de vérité, et le
        # joueur qui a installé StarTrad retrouvera ce texte dans son jeu.
        lignes.append("\n*Traduction du Cirque Lisoir (StarTrad).*"
                      if data.get("en_francais") else
                      "\n*Texte officiel, en anglais : pas de traduction pour "
                      "ce constructeur.*")

    autres, total = data.get("autres") or [], data.get("total_autres") or 0
    if autres:
        suite = enumerate_fr(autres, limit=6)
        reste = total - len(autres)
        lignes += ["", f"Ils construisent aussi {_plural(total, 'vaisseau')} : "
                       f"{suite}" + (f", et {_nombre(reste)} autres." if reste
                                     else ".")]
    return "\n".join(lignes)
_RENDERERS["constructeur_de"] = render_constructeur


def bloc_de_difficulte(diff: dict | None) -> list[str]:
    """Les quatre axes de difficulté d'une mission.

    Ingérés le 2026-08-06 : 2 346 contrats, sept niveaux tous peuplés. Deux
    libellés répondent littéralement à « est-ce que je peux la faire seul ? »,
    question que `shareable` ne savait pas traiter — il dit si la mission se
    **partage**, pas si elle l'exige. On la met donc en tête quand la réponse
    est non : c'est ce qui change la décision d'y aller.
    """
    if not diff:
        return []
    lignes = ["", "**Difficulté**"]
    if diff.get("solo") is False:
        lignes.append("- ⚠️ à ne pas tenter seul")
    for axe in diff["axes"]:
        lignes.append(f"- {axe['axe'].capitalize()} — {axe['rang']}/7 : {axe['texte']}")
    return lignes
