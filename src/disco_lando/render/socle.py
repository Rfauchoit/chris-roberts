"""Les briques partagées du rendu — nombres, pluriels, énumérations,
titres élagués — et le registre des rendus, que chaque module remplit."""

from __future__ import annotations

import re
from typing import Any

from .. import config


# Rempli par chaque module de rendu, lu par l'aiguillage de `conversation`.
_RENDERERS: dict[str, Any] = {}
# « MineableRock_AsteroidLegendary_Quantainium » n'est pas prononçable. Les noms
# de gisements suivent tous ce moule, autant le défaire.
_DEPOSIT = re.compile(
    r"^MineableRock_(?P<contexte>Asteroid|Surface|Cave)?"
    r"(?P<rarete>Common|Uncommon|Rare|Epic|Legendary)?_?(?P<minerai>\w+)$",
    re.IGNORECASE,
)

_CONTEXTE = {"asteroid": "en astéroïde", "surface": "en surface", "cave": "en grotte"}
_RARETE = {
    "common": "commun", "uncommon": "peu commun", "rare": "rare",
    "epic": "épique", "legendary": "légendaire",
}


def provenance_composee(data: dict[str, Any]) -> str:
    """Rend visibles source et fraîcheur du contrat commun."""
    qualite = data.get("qualite_reponse") or {}
    sources = qualite.get("sources") or []
    fraicheur = qualite.get("fraicheur") or {}
    mentions = []
    if "jeu" in sources:
        jeu = fraicheur.get("jeu") or {}
        mentions.append("fichiers du jeu" + (
            f" build {jeu['build']}" if jeu.get("build") else ""))
    if "uex" in sources:
        uex = fraicheur.get("uex") or {}
        date = (uex.get("releve_le") or "")[:10]
        mentions.append("relevés UEX" + (f" du {date}" if date else
                                         " sans date disponible"))
    return ("\n\n_Source : " + " ; ".join(mentions) + "._"
            if mentions else "")


def deposit_label(name: str) -> str:
    """Nom de gisement prononçable.

    « MineableRock_AsteroidLegendary_Quantainium »
      -> « Quantainium en astéroïde, légendaire »
    """
    match = _DEPOSIT.match(name or "")
    if not match:
        return name
    parts = [match.group("minerai")]
    if contexte := _CONTEXTE.get((match.group("contexte") or "").lower()):
        parts.append(contexte)
    if rarete := _RARETE.get((match.group("rarete") or "").lower()):
        parts.append(rarete)
    return ", ".join(parts)


# Les titres de mission portent des jetons non résolus : « Disable Outlaw
# Stronghold at [Location] », « Deal with [TargetName] ». Le jeu les remplace à
# la génération du contrat ; nous n'avons que le gabarit.
_TOKEN = re.compile(r"\[[^\]]*\]")
# Ce qui trahit une valeur que le serveur remplace à la génération, dans
# n'importe quel champ — titre, commanditaire, lieu.
_GABARIT = re.compile(r"~mission\(|<=|UNINITIALIZED|\[[^\]]*\]")
# Une préposition n'est retirée que si le jeton la laisse en fin de titre :
# « Disable Outlaw Stronghold at » se coupe, « Deal with … and Support Forces »
# garde son « with ».
_TRAILING = re.compile(r"\s+(?:at|a|à|de|du|des|dans|pour|with|avec|in|on|for|to)$", re.I)


def speakable_title(title: str | None) -> str:
    """Titre de mission débarrassé de ses jetons de gabarit.

    **Retirer un jeton laisse la ponctuation qui l'encadrait.** Mesuré :
    « Reclusive Bounty: [TargetName] | Very-High Risk » devenait « Reclusive
    Bounty: | Very-High Risk » — un séparateur orphelin au milieu du titre,
    visible dans la réponse. On recolle donc après coup, comme on le fait déjà
    pour les articles doublés des consignes.
    """
    if not title:
        return ""
    text = _TOKEN.sub("", title)
    # Un séparateur qui n'a plus rien à séparer : « : | », « - , », « ( ) ».
    text = re.sub(r"\s*[:\-–—,|]\s*(?=[:\-–—,|])", " ", text)
    text = re.sub(r"[(\[]\s*[)\]]", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—:,|")
    text = _TRAILING.sub("", text).strip(" -–—:,|")
    # Une ponctuation collée au vide en tête de segment : « : | Very-High ».
    return re.sub(r"(?<=\S)\s+([:,])", r"\1", text).strip(" -–—:,|")


# Une abréviation suivie d'un point n'est pas une fin de phrase. La liste est
# courte parce que `render.py` écrit déjà en toutes lettres — elle est là pour
# les noms propres du jeu, pas pour du texte quelconque.
_FIN_DE_PHRASE = re.compile(r"(?<![A-Z])(?<!\bSt)(?<!\bMr)\.(?=\s|$)")


def for_speech(texte: str, budget: int | None = None) -> str:
    """Réduit une réponse à ce qui s'écoute, sans couper au milieu d'une phrase.

    Mesuré sur la machine cible : « où je trouve du quantanium » rend 417
    caractères, soit **31 secondes** de lecture. Personne n'écoute ça — et
    aucun moteur de synthèse ne rend trente secondes d'énumération agréables.
    Ce n'est pas un défaut de la voix, c'est un défaut de la réponse.

    On garde donc des phrases entières tant qu'on tient dans le budget, et au
    moins la première quoi qu'il arrive : une réponse tronquée en son milieu
    est pire qu'une réponse courte. Le texte complet reste affiché dans
    l'overlay et consigné dans le journal.
    """
    budget = config.SPEECH_BUDGET if budget is None else budget
    texte = texte.strip()
    if budget <= 0 or len(texte) <= budget:
        return texte

    phrases = [p.strip() for p in _FIN_DE_PHRASE.split(texte) if p.strip()]
    if not phrases:
        return texte

    gardees, total = [], 0
    for phrase in phrases:
        if gardees and total + len(phrase) > budget:
            break
        gardees.append(phrase)
        total += len(phrase) + 2
    return ". ".join(gardees).rstrip(".") + "."


def enumerate_fr(items: list[str], limit: int = 4) -> str:
    """« A, B et C », avec troncature honnête au-delà de `limit`."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) > limit:
        reste = len(items) - limit
        return ", ".join(items[:limit]) + (
            " et un autre" if reste == 1 else f" et {reste} autres"
        )
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" et {items[-1]}"


def _mult(valeur: float) -> str:
    """Un multiplicateur garde ses décimales. `_nombre` arrondit au dixième et
    affichait « ×1,1 » pour 1,08 — soit exactement l'information que le joueur
    cherche à comparer entre deux qualités."""
    texte = f"{valeur:.3f}".rstrip("0").rstrip(".")
    return (texte or "1").replace(".", ",")


def duration_fr(seconds: float | None) -> str:
    if not seconds:
        return "durée inconnue"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} secondes"
    minutes, rest = divmod(seconds, 60)
    # « 120 minutes » se lit moins bien que « 2 heures » — trouvé en testant
    # la fonction en direct, personne ne l'avait jamais appelée au-delà de
    # l'heure. Les secondes disparaissent à cette échelle : à deux heures
    # près, elles sont du bruit.
    if minutes >= 60:
        heures, minutes = divmod(minutes, 60)
        tete = f"{heures} heures" if heures > 1 else "une heure"
        return f"{tete} et {minutes} minutes" if minutes else tete
    if not rest:
        return f"{minutes} minutes" if minutes > 1 else "une minute"
    return f"{minutes} minutes et {rest} secondes"


def quantity_fr(ingredient: dict[str, Any]) -> str:
    if ingredient.get("quantity_scu") is not None:
        value = ingredient["quantity_scu"]
        return f"{value:g} SCU".replace(".", ",")
    if ingredient.get("quantity_units") is not None:
        n = ingredient["quantity_units"]
        return "une unité" if n == 1 else f"{n} unités"
    return "quantité inconnue"


# Les noms en -eau, -eu, -au prennent un « x » et non un « s ». « Vaisseaus »
# saute aux yeux dans une réponse française.
_PLURIEL_EN_X = ("eau", "eu", "au")


def au_pluriel(mot: str) -> str:
    """La forme plurielle d'un mot, sans nombre devant.

    Extraite de `_plural` plutôt que recopiée : la règle du « x » après
    -eau, -eu et -au n'a aucune raison d'exister à deux endroits, et c'est
    exactement le genre de duplication qui finit par diverger.
    """
    return mot + ("x" if mot.endswith(_PLURIEL_EN_X) else "s")


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    if plural is None:
        plural = au_pluriel(singular)
    return f"{n} {singular if n <= 1 else plural}"


# Le type de lieu du starmap, au pluriel et en français. Sans lui, tout enfant
# d'un corps céleste était annoncé « lune » — 87 lunes pour un Pyro qui en a six.
_FAMILLE_LIEU = {
    "Moon": "lunes", "Planet": "planètes", "Asteroid": "astéroïdes",
    "AsteroidBelt": "ceintures d'astéroïdes", "Outpost": "avant-postes",
    "Manmade": "stations", "LandingZone": "zones d'atterrissage",
    "PointOfInterest": "points d'intérêt", "Star": "étoiles",
    "Anomaly": "anomalies", "JumpPoint": "points de saut",
    "SolarSystem": "systèmes", "Cluster": "amas",
}


def _uec(valeur: float) -> str:
    """Un prix se dit en groupes lisibles, pas chiffre par chiffre.

    Piper lira « 2 262 330 » ; l'espace insécable évite qu'il coupe le nombre
    au milieu en passant à la ligne.
    """
    return f"{valeur:,.0f}".replace(",", " ") + " aUEC"


def _de(texte: str) -> str:
    """« de » + article contracté. « de les missions » ne se dit pas."""
    if texte.startswith("les "):
        return "des " + texte[4:]
    if texte.startswith("le "):
        return "du " + texte[3:]
    if texte[:1].lower() in "aeiouyéèê":
        return "d'" + texte
    return "de " + texte


def _nombre(valeur: float | int | None, unite: str = "") -> str:
    """Un nombre lisible à l'écran : espace fine, pas de décimale inutile."""
    if valeur is None:
        return "—"
    if float(valeur).is_integer():
        texte = f"{int(valeur):,}".replace(",", " ")
    else:
        texte = f"{valeur:,.1f}".replace(",", " ").replace(".", ",")
    return f"{texte} {unite}".strip()


# Comment se dit une statistique quand on ne rend qu'elle. Le libellé de
# `STATS` sert aux classements (« meilleur DPS ») ; ici il faut une phrase.
_DIT_STAT = {
    "ammo_capacity": ("{v} balles", ""),
    "capacitor_max": ("un capacitor de {v}", "unités"),
    "rounds_per_minute": ("une cadence de {v}", "coups par minute"),
    "dps": ("{v} DPS", ""),
    "alpha": ("{v} de dégâts par tir", ""),
    "effective_range": ("une portée efficace de {v}", "m"),
    "projectile_speed": ("des projectiles à {v}", "m/s"),
    "item_mass": ("une masse de {v}", "kg"),
    "cargo_scu": ("{v} SCU de fret", ""),
    "crew": ("{v}", "PLACES"),
    "max_speed": ("{v} en vitesse maximale", "m/s"),
    "scm_speed": ("{v} en vitesse de combat", "m/s"),
    "boost_speed": ("{v} en boost", "m/s"),
    "shield_hp": ("{v} points de bouclier", ""),
    "health": ("{v} points de coque", ""),
    "mass": ("une masse de {v}", "kg"),
    "length": ("{v} de long", "m"),
    "size": ("une taille de {v}", ""),
    "pilot_dps": ("{v} de DPS pilote", ""),
    # Colonnes en base qu'aucun outil ne lisait avant le 2026-08-05.
    # `insurance_minutes` est un délai d'attente, pas une durée d'assurance :
    # c'est le temps avant de récupérer son vaisseau après une réclamation.
    "insurance_cost": ("une prime d'assurance de {v}", "aUEC"),
    "insurance_minutes": ("{v} d'attente à la réclamation", "min"),
    "ore_capacity": ("{v} de soute à minerai", "SCU"),
    "pitch": ("{v} en tangage", "°/s"),
    "yaw": ("{v} en lacet", "°/s"),
    "roll": ("{v} en roulis", "°/s"),
    "qt_speed": ("{v} en vitesse quantique", "m/s"),
    "qt_range": ("une portée de saut de {v}", "m"),
    "quantum_fuel": ("{v} de carburant quantique", ""),
    "fuel_capacity": ("{v} de carburant", ""),
    "fuel_usage": ("une consommation de {v}", "unités par seconde"),
    "fuel_intake": ("une captation de carburant de {v}", "unités par seconde"),
    "autonomie_vol": ("{v} d'autonomie brute", "s"),
}


def volume_scu(uscu: int | None) -> str | None:
    """Un volume en µSCU, dit dans l'unité où il se lit.

    Le catalogue va de 2 500 µSCU pour un pistolet à 120 SCU pour une pièce de
    vaisseau : une seule unité rendrait l'un ou l'autre illisible — « 0,0025
    SCU » ne se retient pas, « 120 000 000 µSCU » non plus. On bascule au seuil
    où le nombre redevient lisible.
    """
    if not uscu:
        return None
    if uscu >= 1_000_000:
        return f"{_nombre(uscu / 1_000_000)} SCU"
    if uscu >= 10_000:
        return f"{_nombre(uscu / 10_000)} cSCU"
    return f"{_nombre(uscu)} µSCU"


def dit_stat(stat: str, valeur: Any) -> str:
    """Une statistique en toutes lettres, sans le reste de la fiche."""
    if stat == "volume_uscu":
        return f"un volume de {volume_scu(valeur)}"
    defini = _DIT_STAT.get(stat)
    if defini is None:
        # `SHIP_STATS` et `STATS` sont déjà le catalogue humain des colonnes.
        # Le rendu avait pourtant une seconde liste fermée : ajouter `roll`
        # au catalogue produisait « 200 roll » jusqu'à ce qu'on pense aussi à
        # `_DIT_STAT`. Le repli reste moins élégant qu'un gabarit dédié, mais
        # il ne divulgue plus jamais un nom de colonne au joueur.
        from ..queries import SHIP_STATS, STATS

        for table in (SHIP_STATS, STATS):
            if stat in table:
                libelle, unite, _ = table[stat]
                return f"{libelle} : {_nombre(valeur, unite)}"
        defini = ("{v} " + stat, "")
    gabarit, unite = defini
    # « 1 places » ne se dit pas, et l'équipage vaut souvent 1.
    if unite == "PLACES":
        return _plural(int(valeur), "place")
    return gabarit.format(v=_nombre(valeur, unite))


def question_de_suite(propositions: list[str]) -> str:
    """« Tu veux la cadence, le DPS, ou la fiche complète ? »

    Une proposition annoncée à l'indicatif — « je peux te donner… » — n'appelle
    pas de réponse : elle se lit comme une remarque, et l'interlocuteur passe à
    autre chose. Si on attend une réponse, on pose une question.
    """
    propositions = [p for p in propositions if p]
    if not propositions:
        return ""
    if len(propositions) == 1:
        return f"Tu veux {propositions[0]} ?"
    return f"Tu veux {', '.join(propositions[:-1])}, ou {propositions[-1]} ?"


# Genre des libellés de statistique. Sans article, « tu veux cadence, DPS » ne
# se dit pas ; et l'article ne se devine pas depuis la table des libellés, qui
# sert d'abord aux classements (« meilleur DPS », sans article).
_MASCULINS = {
    "dps", "alpha", "bouclier", "carburant", "capacitor", "equipage",
    "chargeur", "poids", "grade", "prix", "tangage", "lacet", "roulis",
}
_PLURIELS = {"munitions", "points", "places", "degats"}


def _avec_article(nom: str) -> str:
    from ..normalize import normalize

    tete = (normalize(nom).split() or [""])[0]
    if tete in _PLURIELS:
        return f"les {nom}"
    if nom[:1].lower() in "aeiouéèêh":
        return f"l'{nom}"
    return f"{'le' if tete in _MASCULINS else 'la'} {nom}"


def _nom_stat(stat: str, *, article: bool = False) -> str:
    from ..queries import SHIP_STATS, STATS

    nom = stat
    for table in (SHIP_STATS, STATS):
        if stat in table:
            nom = table[stat][0]
            break
    return _avec_article(nom) if article else nom
