"""Les motifs d'intention — pures données : quels mots appellent quel outil,
et avec quel poids. Voir docs/ROUTER.md."""

from __future__ import annotations

import re

# Motifs d'intention. Le poids reflète la spécificité : « blueprint » ne veut
# dire qu'une chose, « armes » peut apparaître partout.
_INTENTS: dict[str, list[tuple[str, float]]] = {
    # « Combien de blueprints sortent des mêmes missions que le P6-LR ? »
    # La question porte sur le **voisinage** d'un blueprint, pas sur lui. Les
    # poids passent devant `get_blueprint`, qui répondrait la recette — juste,
    # et à côté.
    "blueprints_de_la_meme_serie": [
        (r"\bmemes? missions?\b", 4.5),
        (r"\bmeme serie\b|\bmeme farm\b", 4.5),
        (r"\ben meme temps que\b", 4.0),
        (r"\bquoi d autre\b|\bque?ls? autres?\b.{0,25}\bblue ?print", 4.0),
        (r"\bautres? blue ?prints?\b.{0,30}\bmemes?\b", 4.5),
        (r"\bdebloque\w*\b.{0,25}\ben plus\b", 4.0),
    ],
    "get_blueprint": [
        # Une estimation de grind reste une facette du blueprint : même
        # entité, mêmes missions sources. Le motif spécifique passe devant le
        # générique « missions » sans créer un 62e outil.
        (r"\bcombien (?:de )?(?:missions?|contrats?)\b.{0,35}"
         r"\b(?:blue ?print|debloqu\w*)\b", 5.0),
        (r"\b(?:grind\w*|farm\w*)\b.{0,35}\bblue ?print", 4.5),
        (r"\bblue ?print", 3.0),
        # « Les missions qui permettent d'obtenir le P8-AR » partait chez
        # `decrire`, qui répondait la fiche de l'arme. La tournure vise le
        # blueprint et rien d'autre : elle doit passer devant la description.
        (r"\bmissions? (?:qui|pour)\b[^?]{0,22}?\b(?:obtenir|debloquer|donnent?|avoir)\b", 4.5),
        (r"\bpour (?:obtenir|debloquer|avoir) le blue ?print\b", 4.5),
        # **« Où se trouve le blueprint de X » n'est pas une question de
        # lieu.** Mesuré : « où se trouve » pèse 4,0 chez `where_is_location`
        # contre 3,0 pour le seul mot « blueprint », et la question partait
        # chercher un endroit sur la carte. Le mot spécifique doit peser plus
        # lourd que le tour générique qui l'englobe — même mécanique que
        # « vitesse quantique » contre « vitesse ».
        (r"\bou (?:se |je |on |tu )?(?:trouve\w*|situe|est|chope|chercher)\b"
         r".{0,14}\bblue ?print\b", 3.0),
        (r"\bcomment (?:obtenir|debloquer|avoir)\b", 3.5),
        (r"\bfabriqu\w*", 3.0),
        # **« Comment je fais un P6-LR »** — la formulation orale, et elle
        # partait chez l'analyste : 30 à 50 s de quota pour une recette
        # que le déterministe sert en 100 ms. C'est la règle « le
        # déterministe garde son terrain », prise en défaut par une
        # tournure qu'aucun motif ne couvrait (mesuré en service le
        # 2026-08-13, journal des questions sans réponse).
        #
        # Le verbe « faire » est trop courant pour peser seul : il n'entre
        # qu'accompagné du pronom qui en fait une demande de recette.
        # Le lookahead écarte « comment je fais **pour aller** à Levski »,
        # qui est un trajet : mesuré, il partait ici à 0,89. « Comment je
        # fais pour fabriquer » reste couvert par `\bfabriqu\w*`.
        (r"\bcomment (?:je |on |tu |j'?)?(?:fai[ts]|fabrique)\b(?!\s+pour\b)",
         3.5),
        (r"\bcomment (?:ca |ça )?se (?:fait|fabrique|craft\w*)\b", 3.5),
        (r"\bcraft\w*", 3.0),
        (r"\brecette\b", 3.0),
        (r"\bconstru\w+", 2.0),
        (r"\bingredient\w*", 2.5),
        (r"\bcomposant\w* (?:pour|de|d)\b", 2.0),
        # **« composants p6 lr » — sans préposition, personne ne répondait.**
        # `get_ship_components` matchait seul à 2,0 et abandonnait faute de
        # vaisseau ; l'intention suivante n'existait pas. À 1,5, le blueprint
        # entre dans la course sans jamais passer devant les composants de
        # vaisseau sur « les composants du Gladius » : c'est le **type
        # d'entité** qui départage, exactement le mécanisme prévu.
        #
        # **1,5 et non 1,4** : 1,4 tombait sous `MIN_INTENT_SCORE`, donc
        # l'intention n'entrait jamais dans la course. À 1,5 elle reste à un
        # demi-point exact de `get_ship_components`, hors de la frontière
        # fragile ; le type d'entité tranche ensuite comme prévu.
        (r"\bcomposant\w*", 1.5),
        (r"\bfaire (?:un|une|le|la|de)\b", 1.5),
        (r"\bmateria\w+", 1.5),
        (r"\bdemantel\w+|\bdemont\w+|\brecycl\w+", 3.5),
        # Question de suite : « liste-moi toutes les missions » ne nomme
        # rien et ne dit pas « blueprint ». Poids bas — `get_mission_group`
        # passe devant et ne cède que s'il ne résout aucune organisation.
        (r"\bmissions?\b|\bcontrats?\b", 1.5),
    ],
    # « Quel canon balistique je peux monter sur un Gladius ? » — il faut un
    # vaisseau *et* une intention de montage, sinon c'est la question
    # « quels sont ses points d'emport » qui répond.
    "get_compatible_items": [
        (r"\bque?ls? \w*(?:canon|arme|repeater|gatling)\w*", 3.0),
        (r"\bcompatible\w*", 3.0),
        (r"\bje peux (?:mettre|monter|installer)\b", 3.0),
        (r"\b(?:mettre|monter|installer) sur\b", 2.5),
        # **« Le meilleur équipement à mettre sur ce vaisseau » n'est pas
        # « qu'est-ce qui est monté dessus », ni un classement d'armes sans
        # vaisseau.** Question réelle du 2026-08-10 : elle sortait à égalité
        # parfaite entre `get_ship_hardpoints` (l'armement d'origine — exact
        # et à côté) et `compare_items` (le catalogue entier, hors du
        # vaisseau). C'est bien ici que ça se joue : cet outil rend ce qui
        # **rentre** sur les affûts du vaisseau, classé par DPS.
        # Le motif ne consomme que le vocabulaire ; le vaisseau est laissé
        # à l'extraction d'entité.
        # Le garde négatif est indispensable : « le meilleur loadout **pour
        # battre** un Hammerhead » est un duel, pas un catalogue de ce qui
        # rentre — c'est `peut_detruire` qui sait la déflexion.
        (r"\b(?:meilleur\w*|bon)\s+(?:equipement|loadout|armement|arme\w*|"
         r"canon\w*)\b(?=.{0,40}?\b(?:mettre|monter|installer|pour|sur)\b)"
         r"(?!.{0,70}?\b(?:battre|detruire|tuer|abattre|vaincre|affronter)\b)",
         4.0),
        (r"\bbalistique\w*|\bballistic\w*", 2.0),
        (r"\blaser\w*|\bplasma\w*|\btachyon\w*", 2.0),
        (r"\bgatling\w*|\brepeater\w*|\brepeteur\w*|\bscatter\w*", 2.0),
    ],
    # « Le meilleur canon balistique en DPS » — aucune entité à résoudre, la
    # famille d'armes suffit.
    "compare_items": [
        (r"\bmeilleur\w*", 3.5),
        # « Combien de balles dans une arme de vaisseau balistique » : une
        # question de *famille*, pas d'objet. `get_item_stats` la reçoit
        # d'abord, n'y résout aucune entité et passe la main — encore
        # faut-il que le classement ait de quoi se déclencher. Sans ce motif
        # il plafonnait à 1,0, sous le seuil exigé d'un outil sans entité.
        # **Cette égalité avec `get_item_stats` est voulue, ne pas la « corriger ».**
        # Les deux outils tirent 3,5 sur le même vocabulaire de munitions, et
        # c'est le §7 qui tranche, pas le score : `get_item_stats` reçoit la
        # question d'abord, n'y résout aucune entité quand elle nomme une
        # **famille** — « une arme de vaisseau balistique » — et passe la main.
        #
        # Tentative annulée le 2026-08-06 : exiger un superlatif ici rendait
        # « combien de balles a un Coda » à la fiche, mais faisait tomber
        # « combien de balles dans une arme de vaisseau balistique », qui ne
        # nomme rien et attend bien un classement. Une égalité de score n'est
        # pas toujours un défaut — ici c'est le mécanisme qui travaille.
        (r"\bcombien de (?:balles?|cartouches?|munitions?|pruneaux?|bastos|capacitor\w*)", 3.5),
        (r"\bcompare\w*|\bcomparaison\b", 3.5),
        (r"\bclasse?ment\b", 3.0),
        (r"\ble plus (?:puissant|rapide|leger|lourd|precis)\w*", 3.0),
        (r"\bplus (?:gros|fort) \w*(?:dps|degat)", 2.5),
        (r"\btop \d+\b", 2.5),
        (r"\bbalistique\w*|\bballistic\w*", 1.0),
        (r"\bgatling\w*|\brepeater\w*|\bscatter\w*", 1.0),
    ],
    # Fiche d'un objet nommé. « Combien de balles a un Coda » n'avait aucun
    # outil : le catalogue savait classer les armes entre elles, pas en
    # décrire une. L'entité tranche — sans objet résoluble, « combien de
    # balles dans une arme balistique » retombe sur le classement, qui est
    # bien la réponse à une question de famille.
    "get_item_stats": [
        (r"\bspecs?\b|\bspecificites?\b", 2.5),
        # « Pruneaux », « bastos » : le joueur ne dit pas toujours « balles ».
        # C'est du vocabulaire d'intention, pas un nom d'entité — il se
        # range donc ici et pas dans les alias du résolveur.
        (r"\bballes?\b|\bcartouches?\b|\bpruneaux?\b|\bbastos\b", 3.5),
        (r"\bmunitions?\b|\bchargeur\w*", 3.0),
        (r"\bcapacitor\w*|\bcapaciteur\w*|\bcondensateur\w*", 3.5),
        (r"\bcadence\b|\brpm\b", 2.5),
        (r"\bfiche\b|\bcaracteristiques?\b|\bstats?\b", 2.0),
        (r"\bportee\b", 1.5),
        (r"\bdps\b|\bdegat\w*", 1.5),
        # « Ça prend combien de place ? » — le volume est une statistique
        # d'objet comme une autre. Poids modeste : « dans la soute d'un X »
        # doit rester la question de capacité, pas celle du volume unitaire.
        (r"\bvolume\b|\bencombrement\b", 2.5),
        (r"\bde place\b|\bplace prend\b", 2.5),
    ],
    # **La qualité des matériaux, qui n'est pas une statistique de plus.** Un
    # joueur dit « un P6-LR 900 » : 900 est la qualité du minerai qui a servi à
    # le fabriquer, et elle module les statistiques de l'arme. Les motifs
    # recouvrent volontairement ceux de `get_item_stats` — c'est le **nombre**
    # qui discrimine, pas le vocabulaire, et le garde-fou refuse l'outil sans
    # lui. D'où un demi-point au-dessus : à vocabulaire égal, la présence
    # d'une qualité rend la question plus précise, comme « vitesse quantique »
    # l'est de « vitesse ».
    # **Qui consomme une matière première.** Ouvert après l'audit, et pas
    # comme il le proposait : la donnée dit où la matière part, pas où on la
    # vend — pour vendre, UEX a les prix.
    "ou_consomme": [
        (r"\bconsomme\w*|\bconsommation de\b", 4.0),
        (r"\bqui (?:achete|utilise|prend)\b", 3.5),
        (r"\ba quoi (?:ca |cela )?sert\b|\bou (?:ca |cela )?part\b", 3.0),
        (r"\busines?\b|\binstallations?\b|\bcomplexes?\b", 2.0),
    ],
    # **Classer l'équipement personnel.** Ouvert après l'audit de source :
    # 2 416 armures et 68 missiles n'avaient aucune statistique, et « la
    # meilleure armure pour Pyro » rendait une liste vide sans qu'aucune erreur
    # ne se produise. Le garde-fou est la **famille** — sans elle, l'outil
    # avalerait toute question contenant « le meilleur ».
    "classer_equipement": [
        # Ce signal ne vaut qu'avec une famille ci-dessous. Sous le seuil seul,
        # « quelles sont les forces du Wolf » ne crée plus un faux candidat.
        (r"\bmeilleure?s?\b|\bplus\b|\bmoins\b|\bquelle?s?\b", 1.4),
        (r"\barmures?\b|\bcasques?\b|\bplastrons?\b|\bjambieres?\b|"
         r"\bcombinaisons?\b|\btenues?\b", 3.5),
        (r"\bmissiles?\b|\btorpilles?\b|\broquettes?\b", 3.5),
        # Propulseurs et réservoirs entrent aussi : l'outil ne les classe pas,
        # mais il sait **dire pourquoi** — 6 noms distincts de propulseur dans
        # tout le catalogue —, et se taire laisserait croire à une lacune.
        (r"\bpropulseurs?\b|\bthrusters?\b|\breservoirs?\b", 3.5),
        (r"\bfroid\b|\bglace\w*|\bchaleur\b|\bradiations?\b|\birradie\w*", 2.5),
        (r"\blegendaires?\b|\bepiques?\b|\brares?\b", 1.5),
    ],
    # **Ce qu'une armure permet de porter.** Demande du journal, commentée
    # « points d'emport d'armure par taille et type — quel sac à dos, nombre de
    # medpen, nombre d'armes ». La donnée dormait dans `item_ports`, ingérée
    # pour les accessoires d'arme et jamais interrogée pour les armures.
    "emports_d_armure": [
        # **Pas « emport ».** Le mot appartient déjà aux vaisseaux — « les
        # emports du Gladius » est la question de `get_ship_hardpoints`, et le
        # prendre à 3,5 contre ses 3,0 faisait dépendre la bonne réponse du
        # seul garde-fou. Deux frontières fragiles au banc pour un mot qui
        # n'était pas nécessaire.
        (r"\bemplacement\w*|\bslots?\b", 3.5),
        # **Pas « transporter »** : le mot appartient au fret — « que
        # transporter depuis Lorville » est la question des routes
        # commerciales, et le partager créait une frontière fragile au banc.
        # « Porter » et « emporter » couvrent les tournures d'armure.
        (r"\bporter\b|\bporte\b|\bemporter\b", 3.0),
        (r"\bmedpens?\b|\boxypens?\b", 4.0),
        (r"\bsacs? a dos\b|\bbackpacks?\b", 3.5),
        (r"\barmures?\b|\btenues?\b", 2.0),
    ],
    # Les poids passent **au-dessus** de `decrire` (4,0 sur « c'est quoi ») :
    # sans qualité lisible, le garde-fou abandonne l'intention avant même que
    # le score serve, donc un poids élevé ne peut pas déborder sur une question
    # qui ne cite aucun nombre. « C'est quoi les statistiques d'un P6-LR 900 »
    # est plus précis que « c'est quoi un P6-LR », et doit gagner pour la même
    # raison que « vitesse quantique » gagne sur « vitesse ».
    # **Une marge d'un point pleine, pas d'un demi.** Les trois questions de
    # fabrication sont entrées au banc en créant trois frontières fragiles ;
    # monter le cliquet aurait fait passer l'ajout au lieu de le corriger. À
    # 5,0 contre les 4,0 de `decrire`, un motif ajouté ailleurs ne les retourne
    # plus par accident.
    "fiche_qualite": [
        (r"\bfiche\b|\bcaracteristiques?\b|\bstats?\b|\bstatistiques?\b", 5.0),
        (r"\bqualites?\b|\bniveaux?\b|\bgrades?\b", 5.5),
        (r"\bcrafte\w*|\bfabrique\w*|\bfabrication\b", 2.0),
    ],
    # Le mot « qualité » n'est ici qu'une **preuve d'appoint** : c'est
    # « différence » qui porte l'intention. Lui donner le même poids que sur la
    # fiche fabriquait une égalité parfaite sur « un P8-AR de qualité 750 »,
    # où il n'y a rien à comparer.
    "comparer_qualites": [
        (r"\bdifference\w*|\becart\b", 5.0),
        (r"\bcompare\w*|\bcomparaison\b", 3.5),
        (r"\bqualites?\b|\bniveaux?\b|\bgrades?\b", 3.0),
        (r"\bmieux\b|\bvaut le coup\b|\bapporte\b", 1.5),
    ],
    # « Toutes les qualités du P6-LR » : la chaîne entière, au format de la
    # comparaison — demande de l'utilisateur. « Toutes » est le mot qui la
    # sépare de la fiche à un point et de la comparaison à deux.
    "chaine_de_qualites": [
        # 7,5 et non 6,5 : à 6,5, « toutes les qualités du P6-LR fabriqué »
        # faisait 12,5 partout — « fabriqué » ajoute 2,0 à la fiche — et
        # l'ordre du dictionnaire tranchait. Mesuré au banc.
        (r"\btoute?s? (?:les|ses|leurs?) qualites?\b", 7.5),
        (r"\bchaine de qualites?\b", 6.5),
        (r"\bcaracteristiques?\b|\bstats?\b|\bstatistiques?\b", 3.0),
        (r"\bqualites?\b", 3.0),
    ],
    # « À partir de quelle qualité de P6-LR je le tue d'une balle dans la
    # tête ? » Les poids passent devant `fiche_qualite` (5,5 sur « qualité »)
    # parce que le vocabulaire de mise à mort est **plus spécifique** que celui
    # de la fiche : la question porte sur un seuil, pas sur un barème. Le
    # préparateur exige ce vocabulaire, faute de quoi l'outil volerait toutes
    # les questions de qualité.
    "qualite_pour_tuer": [
        # « OS » est le verbe du joueur pour one-shot — « quelle qualité de
        # p6-lr pour OS dans la tête », question du journal, restait à un
        # demi-point de la fiche. Seul le mot entier compte : « os » n'est
        # l'alias d'aucune entité et n'apparaît dans aucun autre motif.
        (r"\bone ?shots?\b|\bos\b|"
         r"\bd(?:'|e )une? balle\b|\bd(?:'|e )un (?:tir|coup)\b",
         6.5),
        (r"\ba partir de quelle qualite\b|\bquelle qualite (?:il )?(?:me )?faut\b",
         6.0),
        (r"\btue\w*\b|\bkill\w*\b|\babattre\b|\bdescendre\b", 4.0),
        # « Combien de balles dans la tête pour un P4-AR » : le même calcul,
        # rendu par paliers de qualité. Le poids passe devant
        # `combien_y_a_t_il` — « combien de » y appelle le catalogue — et
        # devant `get_item_stats`, qui rendrait la seule fiche de l'arme.
        (r"\bcombien de (?:balles?|tirs?|coups?|cartouches?)\b|"
         r"\bnombre de (?:balles?|tirs?|coups?)\b", 6.5),
        # « Combien de dégâts dans le torse lourd d'un CQ7 » : la même
        # mécanique, menée par les dégâts. Le préparateur exige une cible —
        # sans zone ni classe d'armure, c'est la fiche de l'arme qu'on veut.
        (r"\bcombien de degats?\b|\bdegats? (?:dans|contre|sur) (?:le|la|un|une)\b",
         6.0),
        (r"\bqualites?\b", 3.0),
        (r"\btetes?\b|\bhead ?shots?\b", 2.0),
    ],
    # « Les jalons de qualité du P6-LR » — demande de l'utilisateur du
    # 2026-08-12 : les seuils d'OS de toutes les zones et classes d'un
    # coup. Le préparateur abandonne dès qu'une cible est nommée — le
    # point unique reste à `qualite_pour_tuer`.
    # **« Jusqu'à quelle qualité ça vaut le coup ? »** — la question
    # fondatrice du projet. Elle se distingue de `jalons_de_qualite` par
    # l'idée d'**inutilité au-delà** : « maximale », « ça sert à rien »,
    # « vaut le coup ». Les poids sont plus hauts que ceux des jalons sur
    # ces formes précises, parce qu'un mot d'intention spécifique doit
    # peser plus lourd que le générique qui l'englobe (règle du projet,
    # éprouvée sur « assurance » contre « coûte »).
    "qualite_maximale_utile": [
        (r"\bqualite (?:maxi?male?|max)\b.{0,20}\butile\b", 9.0),
        (r"\bjusqu(?:'|e )?a quelle qualite\b", 8.5),
        (r"\bquelle qualite (?:maxi?male?|max)\b", 8.0),
        (r"\b(?:ca |c'est )?(?:vaut|valent) le coup\b.{0,30}\bqualite\b", 8.0),
        (r"\bqualite\b.{0,30}\b(?:vaut le coup|sert a rien|inutile)\b", 8.0),
        (r"\bau dela de quelle qualite\b", 8.0),
        (r"\bqualite (?:maxi?male?|max)\b", 6.0),
        (r"\bplafond de qualite\b", 6.0),
        # « Quelle est la **limite utile** de qualité d'un P6-LR » — la
        # formulation de l'utilisateur (2026-08-13). Elle partait chez
        # `chaine_de_qualites`, qui déroule l'échelle sans jamais dire où
        # elle cesse de servir : exact, et à côté de la question.
        (r"\blimite utile\b", 8.5),
        (r"\blimite de qualite\b", 7.0),
        (r"\bqualite utile\b", 7.0),
    ],
    "jalons_de_qualite": [
        (r"\bjalons?\b|\bmilestones?\b", 7.0),
        (r"\bpaliers? de qualite\b|\bseuils? de qualite\b", 6.5),
        (r"\ba partir de quelle qualite\b", 5.5),
        # La mise à mort **sans cible** (journal du 2026-08-13) : « combien
        # de balles pour tuer avec un F55 » inventait « tête, armure
        # lourde ». Les deux outils partagent ce vocabulaire et se
        # départagent au préparateur — cible nommée : `qualite_pour_tuer` ;
        # absente : le balayage, compte de balles compris.
        (r"\bballes? pour (?:tuer|abattre|descendre)\b", 6.0),
        (r"\btuer d(?:'|e )une? balle\b", 4.0),
        (r"\bos\b|\bone ?shots?\b", 2.0),
    ],
    # « Quels sont les types de qualité ? » — il n'y en a pas, et le dire est
    # la réponse. Sans nombre et sans entité : le préparateur s'en assure.
    "echelle_de_qualite": [
        (r"\b(?:types?|niveaux?|paliers?|sortes?|grades?) de qualites?\b", 6.5),
        (r"\bqualites? (?:possibles?|existantes?|de craft|de fabrication)\b", 5.5),
        (r"\bechelle de qualites?\b", 6.5),
    ],
    # Composants hors armement. Poids élevés : ces mots ne veulent dire qu'une
    # chose, et sans cet outil ils partaient chez `get_ship_hardpoints` qui
    # répondait armement.
    "get_ship_components": [
        # « C'est quoi le bouclier du Gladius » partait en `decrire` (4,0
        # contre 3,5) : le tour interrogatif pèse quand un composant est
        # nommé — sans composant reconnu, le préparateur s'efface de toute
        # façon.
        (r"\bc est quoi (?:le|la|l|les)\b", 1.0),
        (r"\bbouclier\w*|\bshield\w*", 3.5),
        (r"\bquantum\w*|\bquantique\w*", 3.5),
        (r"\brefroidisseur\w*|\bcooler\w*", 3.5),
        (r"\bgenerateur\w*|\bcentrale\w*", 3.0),
        (r"\bradar\w*", 3.0),
        (r"\breservoir\w*", 3.0),
        # 2,1 garde plus d'un demi-point avec le filet recette à 1,5. Le
        # préparateur typé fait gagner le vaisseau ici et s'efface devant une
        # arme personnelle comme P6-LR.
        (r"\bcomposant\w*", 2.1),
    ],
    # Caractéristiques d'un vaisseau nommé. L'entité tranche : sans vaisseau
    # résoluble, l'intention est abandonnée au profit de la suivante.
    "get_ship_stats": [
        # Un cheveu au-dessus du même motif chez `get_item_stats` : « décris-moi
        # un Gladius » doit parler du vaisseau, pas du « Gladius Model », un
        # bibelot de vitrine qui porte le même nom. Si aucun vaisseau ne se
        # résout, l'objet reprend la main — c'est le mécanisme habituel.
        (r"\bspecs?\b|\bspecificites?\b", 2.6),
        (r"\bscu\b|\bfret\b|\bsoute\b", 3.0),
        (r"\bcargo\b", 2.5),
        (r"\bequipage\b|\bplaces?\b|\bsi[eè]ges?\b", 3.0),
        # « statistiques » en toutes lettres : « et sur les statistiques qui
        # comptent comme la taille, la vitesse » (journal, session Wolf)
        # faisait une égalité parfaite à 5,0 avec `fiche_qualite`, qui porte
        # le mot depuis toujours. `\bstats?\b` ne matche pas le mot entier.
        (r"\bcaracteristiques?\b|\bstats?\b|\bstatistiques?\b|\bfiche\b", 3.0),
        (r"\bvitesse\b|\bscm\b", 2.5),
        (r"\bmasse\b|\bpoids\b", 2.5),
        (r"\bcombien de (?:personnes?|joueurs?|monde)\b", 3.0),
        # **L'autonomie en vol**, ouverte après l'audit : la capacité de
        # carburant était lue depuis le début, la consommation jamais.
        (r"\bautonomie\b|\bvoler combien de temps\b|\btient en vol\b|"
         r"\bcombien de temps.{0,20}\bvol", 3.5),
        (r"\bcapacite\b|\bcontenance\b", 2.5),
        (r"\bautonomie\b|\bcarburant\b", 2.5),
        # Colonnes déjà en base, jamais interrogées jusqu'au 2026-08-05.
        # « L'assurance » doit passer devant `get_price` : « combien coûte
        # l'assurance d'un Gladius » répondait le prix du **vaisseau**, avec
        # aplomb — 2 262 330 aUEC au lieu de 2 500.
        (r"\bassurance\b|\bassurer\b|\breclamation\b", 4.0),
        (r"\bmaniabilite\b|\bmaniable\b|\btangage\b|\blacet\b|"
         r"\broulis\b|\broll\b", 3.5),
        (r"\bcaptation\b|\bcollecte\w*\b.{0,15}\bcarburant\b|"
         r"\bfuel intake\b", 3.5),
        (r"\bminerai\b|\bsoute a minerai\b", 3.0),
        # « Quelle taille fait un Gladius » n'avait aucune intention : le
        # mot manquait, alors que la colonne existe et est renseignée sur
        # les 316 vaisseaux.
        (r"\btaille\b|\bdimension\w*|\blongueur\b|\benvergure\b", 2.5),
        (r"\bboost\b|\bpostcombustion\b", 2.5),
    ],
    # Classement ou duel. Aucune entité à résoudre : le garde-fou est la
    # présence d'un vocabulaire de vaisseau, sinon `compare_items` répondrait.
    "compare_ships": [
        # « **La** plus **grosse** soute » ne matchait pas le masculin seul —
        # même leçon que « quels ne couvre pas quelles » : en contexte, la
        # question volait la valeur du vaisseau précédent (40 SCU au lieu du
        # classement, journal du 2026-08-10). 4,0 et non 3,5 : « soute »
        # donne 3,0 à la fiche du vaisseau, et le premier réglage laissait
        # la frontière à un demi-point pile — le cliquet du banc a mordu.
        (r"\b(?:le|la) plus (?:rapide|gros(?:se)?|grande?|lourde?|"
         r"leger|legere|spacieuse?)\w*", 4.0),
        # « Qui est le plus fort entre un Gladius et un Arrow » — question
        # réelle du journal, qui répondait la **fiche du Gladius** : « qui
        # est » déclenchait `decrire` à 3,5 et rien ne le concurrençait. Le
        # garde-fou des deux vaisseaux nommés reste en place ; s'il n'y en a
        # qu'un, la description reprend la main.
        (r"\ble plus (?:fort|puissant|resistant|solide|costaud)\w*", 3.5),
        (r"\bqui (?:est|serait|gagne|l emporte)\b", 2.0),
        (r"\bmeilleur\w* vaisseau\w*", 3.5),
        (r"\bquel vaisseau\b", 3.0),
        (r"\bplus de (?:scu|fret|cargo|place)\w*", 3.0),
        # « compare » sans le mot « vaisseau » : c'est le garde-fou plus bas
        # qui tranche, en exigeant deux vaisseaux nommés. Sans ce motif,
        # « compare le Cutlass et le Freelancer » n'atteignait jamais cet
        # outil — seul `compare_items` se déclenchait, puis se bloquait.
        #
        # Le poids doit atteindre 3,0 : un outil sans entité à résoudre exige
        # ce seuil, faute de second garde-fou. À 2,0 la question était écartée
        # avant même d'arriver au contrôle des deux vaisseaux nommés.
        (r"\bcompare\w*|\bcomparaison\b", 3.0),
        (r"\bvaisseau\w*", 1.0),
    ],
    # « C'est loin, Yela depuis Lorville ? » — deux lieux à résoudre, d'où le
    # second passé en argument `to`.
    # « Je peux aller de microTech à Ruin Station dans un Gladius ? » — une
    # question de faisabilité, pas de distance. Le garde-fou est double : deux
    # lieux **et** un vaisseau, sinon c'est `get_distance` qui répond.
    # « Combien de Coda je peux mettre dans un Cutlass ? » — un objet et un
    # vaisseau, donc deux entités qui se volent la résolution. Les poids
    # passent devant `get_item_stats`, qui répondrait le volume unitaire :
    # juste, et à côté de la question.
    # **Aucun joker qui enjambe l'entité.** `_mots_d_intention` retire de la
    # recherche d'entité *tout* le texte apparié, et pour *tous* les outils :
    # un `\bcombien\b.{0,30}\bdans\b` avalait « coda » au passage, et pas
    # seulement ici — l'exclusion est calculée sur l'union des motifs. Chaque
    # motif ne couvre donc que ses propres mots.
    # « Qu'est-ce qu'on vend à Lorville ? » — le **lieu** est le sujet, pas
    # l'objet. Toutes ces questions tombaient chez `get_price`, qui résolvait
    # ce qu'il pouvait dans la phrase et répondait un T-shirt. Les poids
    # passent donc devant lui.
    #
    # Aucun joker qui enjambe : `_mots_d_intention` retire tout le texte
    # apparié, pour tous les outils.
    # « Qu'est-ce qu'on fabrique avec du Laranite ? » — l'inverse de la
    # recette. Les poids passent devant `get_blueprint`, qui répondrait « la
    # recette du Laranite », et devant `get_price`, qui répondait le prix
    # d'une gourde. Motifs serrés : `_mots_d_intention` retire tout le texte
    # apparié, pour tous les outils.
    # « Quels vaisseaux peuvent monter un Omnisky XII ? » — l'inverse de
    # `get_compatible_items`, qui ne sait dire que « ce qui monte **sur** ce
    # vaisseau ». La question dans l'autre sens n'avait aucun outil.
    # « Quelles optiques vont sur un P8-AR ? » — les emplacements d'une **arme**,
    # que rien ne lisait : `hardpoints` ne couvre que les vaisseaux. Les poids
    # passent devant `get_compatible_items`, qui parle d'armes **de vaisseau**.
    # « C'est quoi le meilleur vaisseau de combat pour 17M de crédits ? »
    # Remarque du journal : la question partait chez `compare_ships`, qui
    # classait sur la vitesse — juste, et sans rapport avec un budget. Les
    # poids passent devant, et le garde-fou est la présence d'un montant.
    "vaisseau_pour_budget": [
        (r"\bpour \d[\d\s]*\s*(?:m\b|millions?\b|k\b|aeuc|auec|credits?)", 4.5),
        (r"\bavec \d[\d\s]*\s*(?:m\b|millions?\b|k\b|aeuc|auec|credits?)", 4.5),
        (r"\bbudget\b", 4.0),
        (r"\bje peux (?:m offrir|acheter quoi)\b", 4.0),
        (r"\bmeilleur\w* vaisseau\w*", 1.5),
    ],
    "accessoires_compatibles": [
        (r"\bque?ls? optiques?\b|\bquel(?:le)?s? (?:lunettes?|visees?)\b", 4.5),
        (r"\boptiques?\b|\blunettes?\b|\bviseurs?\b|\bscopes?\b", 3.0),
        (r"\bquel(?:le)?s? accessoires?\b", 4.5),
        (r"\baccessoires?\b|\battachements?\b", 2.5),
        (r"\bsilencieux\b|\bcompensateur\w*|\bsous canon\b", 3.0),
        (r"\bvont sur\b|\bva sur\b", 2.0),
    ],
    "qui_peut_monter": [
        (r"\bque?ls? vaisseaux? (?:peuvent|peut)\b", 4.5),
        (r"\bquel(?:le)?s? vaisseaux?\b.{0,8}\b(?:monter|equiper|porter)\b", 4.5),
        (r"\bqui peut (?:monter|equiper|porter)\b", 4.5),
        (r"\bsur que?ls? vaisseaux?\b", 4.0),
        (r"\bmontable sur\b|\bcompatible avec que?ls?\b", 4.0),
    ],
    # « Quels vaisseaux ont plus de 100 SCU ? » Le nombre est un **seuil**, pas
    # un nom — le routeur écarte désormais les grammes numériques, encore
    # fallait-il que quelqu'un lise le seuil. Outil sans entité : le garde-fou
    # est la présence effective d'un seuil, vérifiée plus bas.
    # « Quelles armes font plus de 500 DPS ? » — le pendant de
    # `vaisseaux_au_seuil` côté catalogue. Sans lui, la question tombait chez
    # son homologue vaisseau, « dps » étant une statistique des deux, et
    # répondait 240 vaisseaux. Le vocabulaire d'arme tranche.
    "objets_au_seuil": [
        (r"\bque?ls? armes?\b|\bquel(?:le)?s? armes?\b", 3.0),
        (r"\barmes?\b|\bcanons?\b|\bfusils?\b|\bpistolets?\b", 2.0),
    ],
    # « Quelle arme fait le plus de dégâts avant d'être à sec ? » — une valeur
    # qui n'est dans aucune colonne et se déduit de deux.
    "armes_par_metrique": [
        (r"\bpar chargeur\b|\bavant d etre a sec\b|\bavant la panne seche\b", 4.5),
        (r"\bdps soutenu\b|\ben continu\b|\bsur la duree\b", 4.5),
        (r"\btirs? par capacitor\b|\btirs? avant\b", 4.5),
        (r"\bautonomie de tir\b|\btir continu\b", 4.0),
        (r"\b(?:alpha|degats?) par (?:projectile|plomb)\b", 4.5),
    ],
    # « Quels vaisseaux n'ont pas de jump drive ? » — un filtre par **absence**,
    # que rien ne savait faire : tous les outils répondaient « ce qui a ».
    "vaisseaux_sans_composant": [
        (r"\bn ont pas de\b|\bn a pas de\b|\bsans\b", 3.0),
        (r"\bdepourvus?\b|\bmanquent?\b", 3.5),
        (r"\bque?ls? vaisseaux?\b", 1.5),
    ],
    "vaisseaux_au_seuil": [
        (r"\bplus de \d", 3.5),
        (r"\bmoins de \d", 3.5),
        (r"\bau moins \d|\bau plus \d", 3.5),
        (r"\bsuperieur\w* a \d|\binferieur\w* a \d", 3.5),
    ],
    # Les mêmes motifs que le seuil simple, au même poids : les deux outils se
    # départagent sur le **nombre de contraintes lues**, pas sur les mots. Une
    # question à un seul critère laisse passer celui-ci (moins de deux
    # contraintes), une question à plusieurs fait reculer l'autre. Écrire des
    # poids différents serait un second arbitrage, qui contredirait le premier.
    "vaisseaux_multi_criteres": [
        (r"\bplus de \d", 3.5),
        (r"\bmoins de \d", 3.5),
        (r"\bau moins \d|\bau plus \d", 3.5),
        (r"\bsuperieur\w* a \d|\binferieur\w* a \d", 3.5),
        (r"\bavec\b.{0,40}\bet\b", 1.5),
        (r"\bsous \d|\bpour \d", 2.0),
        # « Un vaisseau rapide avec du fret » : deux critères sans un seul
        # nombre — question non routée du balayage du 2026-08-07. Le
        # préparateur exige toujours deux contraintes lues, qualitatives
        # comprises.
        (r"\bvaisseau\w*\b.{0,20}\brapides?\b", 3.5),
        (r"\bavec (?:du|de la|un peu de) (?:fret|cargo|soute)\b", 3.5),
    ],
    # « Combien de vaisseaux dans le jeu ? » — la base le sait par
    # construction. Vocabulaire fermé : on ne compte que ce dont on sait dire
    # honnêtement ce qu'il recouvre.
    "combien_y_a_t_il": [
        # Ici on compte les contrats, pas les recettes. Sans cette intention
        # spécifique, `blueprints_par_systeme` répondait 655 blueprints à une
        # question qui demande les 708 contrats publiés qui en distribuent.
        (r"\bcombien de contrats?\b.{0,45}\bblueprints?\b", 5.5),
        (r"\bcombien (?:de|d)\b.{0,14}\b(?:dans le jeu|en tout|au total|existe\w*)\b", 4.5),
        (r"\bil existe combien\b|\bil y a combien\b", 4.0),
        (r"\bnombre total\b", 4.0),
        # **La tournure la plus courante n'était pas couverte.** Trouvé au
        # balayage : « combien y a-t-il de vaisseaux » ne déclenchait **rien**,
        # l'outil exigeant « en tout », « au total » ou « existe ». Le motif
        # reste serré — il faut la construction impersonnelle entière, sans
        # quoi « combien de balles a un Coda » viendrait ici au lieu d'aller
        # chercher la fiche de l'arme.
        (r"\bcombien (?:y a t il|il y a)\b", 4.5),
    ],
    "que_fabrique_t_on_avec": [
        (r"\bfabrique t on avec\b|\bfabriquer avec\b", 5.0),
        (r"\bse fabriquent? avec\b|\bcraft\w* avec\b", 5.0),
        (r"\bsert (?:a quoi|a fabriquer)\b|\ba quoi sert\b", 4.5),
        (r"\bqu est ce qu on (?:fabrique|craft)\w*", 5.0),
        (r"\bavec du\b|\bavec de la\b|\bavec de l\b", 1.5),
    ],
    # **Le constructeur.** « Quelle est la marque du Vanguard » restait sans
    # réponse, et « qui construit le Vanguard » partait sur les blueprints —
    # « construit » y menait tout seul. Les motifs sont donc plus lourds que
    # ceux de la fabrication, comme « vitesse quantique » l'est sur « vitesse ».
    "constructeur_de": [
        (r"\bque?l(?:le)?s? (?:est|sont)? ?(?:la |le )?"
         r"(?:marque|constructeur|fabricant)\b", 5.5),
        (r"\bmarque\b|\bconstructeur\b|\bfabricant\b|\bmanufacturier\b", 4.5),
        (r"\bqui (?:construit|fabrique|produit|fait)\b", 5.0),
        (r"\b(?:construit|fabrique|produit) par\b", 5.0),
        (r"\bde que?l(?:le)? (?:marque|constructeur)\b", 5.5),
    ],
    # **La méthode, pas le lieu.** « Quelle est la meilleure technique de
    # raffinage » ne demande pas où aller. Le mot qui tranche est
    # « technique » / « méthode » / « procédé » ; sans lui, la question part
    # chercher une raffinerie.
    "methode_de_raffinage": [
        (r"\b(?:technique|methode|procede|processus)\b.{0,24}\braffin\w*", 5.5),
        (r"\braffin\w*.{0,24}\b(?:technique|methode|procede|processus)\b", 5.5),
        (r"\b(?:technique|methode|procede)s? de raffin\w*", 6.0),
    ],
    # Raffinage. Deux outils, mêmes mots : c'est l'entité qui tranche — un
    # minerai mène à `ou_raffiner`, une recette à `conseil_de_raffinage`.
    # C'est le mécanisme du §6, et il évite deux jeux de motifs qui se
    # marcheraient dessus.
    "ou_raffiner": [
        (r"\bo[uù] .{0,20}raffin\w+", 4.5),
        (r"\braffin\w+", 3.5),
        (r"\brefiner\w*\b|\brefinery\b", 3.0),
    ],
    "conseil_de_raffinage": [
        # **Plus lourds que « où raffiner » tout court (8,0).** « Raffiner pour
        # une recette » est strictement plus spécifique que « raffiner » ; le
        # mot d'intention le plus précis doit peser plus que le générique qui
        # l'englobe, comme « vitesse quantique » contre « vitesse ».
        #
        # Sans ça, l'outil ne gagnait que parce que `ou_raffiner` échouait à
        # résoudre « P8-AR » comme minerai. C'est une victoire par défaut : le
        # jour où un objet porte un nom de minerai, elle s'inverse.
        (r"\braffin\w+.{0,30}\b(?:recette|fabriquer|craft\w*)\b", 8.5),
        (r"\b(?:recette|fabriquer|craft\w*)\b.{0,30}\braffin\w+", 8.5),
        # **Les deux motifs génériques ont été retirés.** Ils étaient recopiés
        # mot pour mot depuis `ou_raffiner` — « où … raffiner » à 4,5 et
        # « raffin… » à 3,5 — si bien que « où dois-je raffiner du fer »
        # sortait **8,0 pour les deux outils** et se départageait sur l'ordre
        # du dictionnaire. Un motif partagé ne discrimine rien : il ne fait
        # qu'ajouter la même constante des deux côtés.
        #
        # Ce qui distingue vraiment cet outil est au-dessus : raffiner **pour
        # une recette**. Ses propres questions gardent 10,0, largement au-
        # dessus du seuil, et « où raffiner du fer » revient à `ou_raffiner`
        # par la seule force des motifs.
    ],
    "que_trouve_t_on": [
        (r"\bqu est ce qu (?:on|il y a)\b", 4.0),
        (r"\bqu on (?:vend|trouve|achete)\b", 4.5),
        (r"\bse vend\w*\b|\bsont vendus\b|\best vendu\b", 4.0),
        (r"\bque?ls? (?:commerces?|boutiques?|magasins?)\b", 4.5),
        (r"\bqu y a t il\b|\bil y a quoi\b", 4.0),
        (r"\bcommerces?\b|\bboutiques?\b", 2.0),
    ],
    "matchups_vaisseau": [
        # Un matchup est un **duel bidirectionnel**, pas une fiche ni le duel
        # unidirectionnel historique. Le terme anglais suffit, car c'est celui
        # employé deux fois dans le journal. Les « forces » ne suffisent que
        # si une comparaison explicite nomme un second vaisseau : « forces du
        # Wolf » reste une demande d'analyse générale, hors déterministe.
        (r"\bmatchups?\b", 7.0),
        (r"\bque (?:peu(?:t|vent)|sait|savent) "
         r"(?:detruire|abattre|tuer)\b", 7.0),
        # « Qui gagne entre un wolf et un arrow » restait sans intention et
        # partait à l'analyste — 20 s de Sonnet pour un duel que le
        # déterministe calcule en millisecondes (journal du 2026-08-12).
        (r"\bqui gagne\b|\bqui l emporte\b|\bqui bat\b", 7.0),
        (r"\bforces?\b(?=.{0,60}\b(?:par rapport|face a|contre|versus|vs)\b)",
         5.5),
        # 3,5 et non 3,0 : « et par rapport à leur taille ? » (reprise du
        # journal) restait à un demi-point pile de `get_ship_stats`, qui
        # score 2,5 sur « taille ».
        (r"\b(?:par rapport|face a)\b", 3.5),
        # « Un scorpius contre un hurricane ? » n'a que ce mot d'intention.
        # 3,0 le sort de la bande de réserve (0,84 mesuré contre 0,79 à
        # 2,5) ; le poids peut rester discret parce que le préparateur
        # exige que les DEUX côtés de la coupe résolvent un vaisseau —
        # « contre une armure lourde » abandonne l'intention sans voler la
        # question.
        (r"\bcontre\b|\bduels?\b", 3.0),
    ],
    # Le réseau d'énergie (sprint 19, docs/ANALYSE_ENERGIE.md). Les mots
    # sont spécifiques — « pips », « barres d'énergie », « budget énergie » —
    # parce que « énergie » nu appartient déjà aux armes (dps_energy) et
    # aux boucliers. Le préparateur exige le vaisseau là où il en faut un.
    "budget_energie": [
        (r"\bbudget (?:d )?energ\w*", 7.0),
        (r"\btout (?:tient|alimente\w*|fonctionne)\b", 5.0),
        (r"\bassez d energie\b|\bmanque d energie\b", 6.0),
        (r"\b(?:pips?|barres?) d energie\b", 5.5),
        (r"\balimenter\b", 3.0),
        (r"\benergie\b", 2.0),
    ],
    "composants_par_pip": [
        (r"\bconsomm\w+ le moins\b|\bmoins d energie\b|"
         r"\bplus econome\b", 6.5),
        (r"\bpar pip\b|\bpar barre\b", 6.5),
        (r"\bconsommation\b", 3.0),
    ],
    "loadout_energie": [
        (r"\bloadout (?:econome|le plus econome)\b", 7.0),
        (r"\bplus d energie possible\b|\bmaximum d energie\b|"
         r"\blibere\w* (?:le plus d |des )?(?:pips?|energie)\b", 6.5),
        (r"\b(?:le plus )?puissant\b(?=.{0,50}\benergie\b)", 6.0),
        (r"\benergie a fond\b|\btout a fond\b", 6.0),
        (r"\bloadout\b(?=.{0,40}\benergie\b)|"
         r"\benergie\b(?=.{0,40}\bloadout\b)", 5.0),
    ],
    "loadout_discret": [
        (r"\bloadout (?:le plus )?(?:discret|furtif)\b", 7.0),
        (r"\b(?:plus |le plus )?(?:discret|furtif|furtive)\b"
         r"(?=.{0,50}\b(?:vaisseau|loadout|possible)\b)", 5.5),
        (r"\bsignatures? (?:em|ir|la plus basse|les plus basses)\b", 6.0),
        (r"\bfurtivite\b", 5.0),
    ],
    "bataille": [
        # Le **jeu complet** des motifs de `peut_detruire`, chacun un
        # point au-dessus : les scores se cumulent par outil, et une
        # première version qui ne reflétait que les verbes laissait le duel
        # à 8,5 contre 5,5 — la bataille ne prenait jamais la main. C'est
        # le préparateur qui tranche : sans nombre ni modulation, il rend
        # la main au duel sérieux.
        (r"\bpeu(?:t|vent) (?:le )?(?:detruire|tuer|abattre|battre|"
         r"vaincre|casser|exploser|tomber)\b", 6.0),
        (r"\bcapables? de (?:detruire|tuer|tomber|abattre|casser)\b", 6.0),
        (r"\b(?:detruire|abattre|vaincre) (?:un|une|le|la|l)\b", 4.5),
        (r"\bpasser l ?armure\b|\btomber le (?:bouclier|shield)\b", 5.0),
        (r"\bversus\b|\bvs\b", 3.5),
        # Les modulations pèsent : « 5 Gladius contre un Hammerhead sans
        # bouclier » partait chez vaisseaux_sans_composant (« sans
        # bouclier » est aussi son vocabulaire) — le verbe de destruction
        # plus l'état doivent l'emporter.
        (r"\bsans (?:bouclier|boucliers|shield|shields)\b", 2.0),
        (r"\ba l arret\b|\bimmobile\b", 2.0),
        (r"\bmoitie de\b", 1.5),
    ],
    "peut_detruire": [
        # Le duel : « est-ce qu'un Scorpius peut détruire un Hammerhead ».
        # Aucun motif n'enjambe les entités — les verbes seuls, la coupe se
        # fait dans le préparateur.
        (r"\bpeut (?:le )?(?:detruire|tuer|abattre|battre|vaincre|casser|"
         r"exploser|tomber)\b", 5.0),
        (r"\bcapable de (?:detruire|tuer|tomber|abattre|casser)\b", 5.0),
        (r"\b(?:detruire|abattre|vaincre) (?:un|une|le|la|l)\b", 3.5),
        (r"\bpasser l ?armure\b|\btomber le (?:bouclier|shield)\b", 4.0),
        (r"\bversus\b|\bvs\b", 2.5),
        # **« Le meilleur loadout pour battre un Hammerhead » n'est pas
        # « qu'est-ce qui est monté dessus ».** Tapé deux fois le
        # 2026-08-10 : `get_ship_hardpoints` répondait l'armement d'origine
        # du Hammerhead — exact, et à côté. Le duel calcule pourtant
        # exactement ça (`_conseils` : ce qui passe la déflexion, sur les
        # affûts de l'attaquant). Le poids doit dépasser « loadout » (2,5)
        # plus « mettre sur » (2,0) de l'autre outil.
        # Le motif **ne consomme que le vocabulaire**, jamais le trajet
        # jusqu'au verbe : `_mots_d_intention` retire le texte apparié de la
        # recherche d'entité, sur l'union des motifs de **tous** les outils.
        # Ma première version couvrait « quel … loadout … pour battre » d'un
        # bout à l'autre et emportait « un wolf » avec elle — le piège
        # « un motif d'intention ne doit pas enjamber l'entité », retrouvé
        # à l'écriture, et le cliquet du banc l'a signalé aussitôt.
        (r"\b(?:meilleur\w*|quel\w*|bon)\s+(?:loadout|equipement|armement|"
         r"arme\w*|canon\w*)\b"
         r"(?=.{0,60}?\b(?:pour|contre)\b.{0,30}?"
         r"\b(?:battre|detruire|tuer|abattre|vaincre|affronter)\b)", 6.0),
        # **« Avec le meilleur loadout » est une spec de duel** (sprint 21) :
        # « un Gladius avec le meilleur loadout contre un Arrow ». La coupe
        # « contre »/« vs » et les deux vaisseaux sont exigés par le
        # préparateur, donc le poids peut être franc sans voler les
        # questions d'équipement pur (« quelles armes sur un Gladius »).
        (r"\bavec (?:le |son |un )?(?:meilleur\w*|optimal\w*|top) "
         r"(?:loadout|equipement|armement|setup|stuff)\b", 5.5),
    ],
    "combien_dans_la_soute": [
        (r"\bdans (?:la|ma|sa|une|le|mon) (?:soute|cargo|cale)\b", 4.5),
        (r"\bje peux (?:en )?(?:mettre|emporter|charger|transporter)\b", 4.0),
        (r"\bj en (?:mets|met|mettrais|transporte|emporte)\b", 4.0),
        (r"\bcombien (?:il |on )?(?:en )?(?:tient|tiennent|rentre|rentrent)\b", 4.0),
        (r"\bcapacite de (?:la )?soute\b", 3.5),
        # « Combien de Finley passe dans un Cutlass » : demande de
        # l'utilisateur (2026-08-07), la tournure sans « soute » ne routait
        # pas. Le lookahead exige un « dans » plus loin **sans l'inclure
        # dans le match** : seul « combien de » part dans l'exclusion des
        # mots d'intention, l'objet entre les deux reste résoluble — la
        # première version couvrante avalait l'entité, piège documenté du
        # `combien.{0,30}dans`, vécu deux fois le même jour.
        (r"\bcombien de(?=\b.{0,40}\bdans\b)", 4.0),
        (r"\bpassent? dans\b|\brentrent? dans\b|\btiennent? dans\b", 4.0),
    ],
    "peut_voyager": [
        # « Puis-je aller… » est la même demande que « je peux aller… ».
        # La normalisation retire le trait d'union ; sans cette variante, le
        # routeur trouvait pourtant les deux lieux et le Gladius, mais aucun
        # motif d'intention, et dépensait 15 s chez Sol pour un calcul déjà
        # couvert par `peut_voyager`.
        (r"\b(?:je peux|puis je) (?:aller|me rendre|voyager|faire)\b", 4.0),
        (r"\bpeut on (?:aller|se rendre)\b", 4.0),
        # « Comment aller de Nyx à Pyro » ne déclenchait **rien** : la question
        # de trajet la plus directe qui soit n'était couverte par aucun motif.
        # Remarque du journal.
        (r"\bcomment (?:aller|se rendre|faire pour aller|rejoindre)\b", 4.0),
        (r"\bcomment y aller\b|\bquel (?:trajet|itineraire|chemin)\b", 4.0),
        # Les tournures orales du même besoin, mesurées le 2026-08-13 sur
        # les questions restées sans réponse : « comment on va à Levski »,
        # « je veux aller à Levski », « trajet vers Levski ». Onze
        # formulations sur douze tombaient dans le vide, et le silence
        # renvoyait ensuite la question à l'analyste.
        (r"\bcomment (?:on|je|tu) (?:va|vais|vas)\b", 4.0),
        (r"\bje (?:veux|voudrais|souhaite) (?:aller|me rendre)\b", 4.0),
        (r"\b(?:trajet|itineraire|route) (?:vers|pour|jusqu)\b", 4.0),
        (r"\bpour aller (?:de|a|jusqu)\b|\bpour rejoindre\b", 3.5),
        (r"\best ce que (?:je peux|on peut)\b", 3.5),
        # « Je dois faire un aller-retour A/B, est-ce que je le tiens d'une
        # traite ? » décrit bien un trajet sans employer « je peux aller ».
        # Le journal en contient un : lieux et vaisseau étaient tous reconnus,
        # seul ce signal d'intention manquait.
        (r"\b(?:faire|tenir) (?:un |des |l )?alle?rs?[- ]retours?\b", 4.0),
        (r"\bautonomie\b.*\baller\b", 3.5),
        (r"\bassez de (?:carburant|fuel|quantum)\b", 4.0),
        (r"\ben un (?:seul )?saut\b", 4.0),
        (r"\bavec un\b|\bdans un\b", 1.0),
    ],
    "get_distance": [
        # « C'est quoi la distance entre la Terre et Proxima b » révélait
        # une collision avec `decrire` (4,0), qui répondait la fiche d'une
        # armure résolue sur « terre ». Une distance explicite est une
        # intention plus précise que « c'est quoi » ; un point de marge plein
        # évite aussi une nouvelle frontière fragile au banc.
        # La forme verbale et le pluriel comptent aussi. « distancer yela
        # crusader » — tapé en vrai le 2026-08-10 — ne matchait rien et
        # partait chez l'analyste pour 58 secondes, sur une question que
        # l'outil règle en millisecondes. Un joueur tape vite ; exiger le
        # substantif exact facture sa faute de frappe en quota.
        (r"\bdistanc\w*\b", 5.0),
        # `normalize` remplace l'apostrophe par une espace : « c'est » devient
        # « c est ». Écrire `c'?est` ne matche donc jamais — piège à retenir
        # pour tous les motifs contenant une élision.
        (r"\bc est loin\b|\bcombien de temps pour aller\b", 3.5),
        (r"\bloin\b", 2.5),
        (r"\bcombien de km\b|\bcombien de kilometres\b", 3.0),
        (r"\btrajet\b", 2.5),
        (r"\bdepuis\b", 1.5),
    ],
    "nearest_locations": [
        (r"\bplus proche\w*", 3.5),
        # Même piège d'élision : « qu'est-ce » se normalise en « qu est ce ».
        (r"\b(?:qu est ce qu il y a |quoi )?(?:autour|pres) de\b", 3.0),
        (r"\ba proximite\b|\bdans le coin\b", 3.0),
        (r"\bvoisin\w*", 2.5),
    ],
    # Routes commerciales. Aucune entité à résoudre : le vocabulaire est assez
    # spécifique pour se passer d'un second garde-fou, et les poids atteignent
    # les 3,0 qu'un tel outil exige.
    # **La question n°1 des joueurs, composée au lieu de refusée.** Poids
    # élevés : le vocabulaire de l'argent-à-gagner ne veut dire que ça, et
    # l'outil n'a pas d'entité — l'intention doit être franche.
    # **« Quel vaisseau pour le salvage »** partait en classement de vitesse —
    # exact, chiffré, sans rapport. Le rôle est en base ; le garde-fou du
    # préparateur exige un métier reconnu.
    "vaisseaux_par_metier": [
        (r"\bque?ls? vaisseaux?\b", 2.5),
        (r"\b(?:salvage|recuperation|ferrailla\w*|minage|minier|racing|medical|ravitaillement|debarquement|passagers|tourisme|bombardier)\b", 3.0),
        (r"\bpour (?:le|la|du|de la|faire du)\b", 1.5),
    ],
    "comment_gagner": [
        (r"\bcomment (?:gagner|se faire|faire) \b", 4.0),
        (r"\bgagner (?:de l argent|des? (?:auec|credits?|sous|thunes?))\b", 4.5),
        (r"\bfarm\w*\b|\bdu fric\b|\bdes ronds\b", 3.0),
        (r"\bargent (?:facile|rapide)\b", 4.0),
        # « Comment je peux me faire des crédits » : le verbe n'est pas collé
        # au « comment », le pronom s'intercale.
        (r"\b(?:se|me|te) faire des? (?:credits?|auec|sous|thunes?|ronds)\b", 4.5),
        (r"\bm enrichir\b|\bdevenir riche\b", 4.0),
    ],
    "get_trade_route": [
        (r"\broutes? commercial\w*", 4.0),
        (r"\btrade ?routes?\b", 4.0),
        (r"\bque (?:transporter|commercer)\b", 3.5),
        (r"\bcommerce rentable\b|\bplus rentable\b", 3.5),
        (r"\bquoi (?:acheter|transporter) pour\b", 3.5),
        (r"\bmarge\w*\b|\bbenefice\w*", 3.0),
        (r"\bfret rentable\b", 3.0),
        # « Quelle route rentable avec un Freelancer » n'avait aucun motif :
        # « route » seul est trop commun (routage, en route), mais « route »
        # suivi de « rentable » ne parle que de commerce.
        (r"\broutes? rentables?\b", 4.0),
    ],
    # « Où acheter un Coda le plus proche de Lorville » — le prix ne décide
    # pas, la distance si. Poids élevés : il faut passer devant `get_price`
    # **et** devant `nearest_locations`, qui répondent chacun à une moitié de
    # la question. Le garde-fou est double : un objet **et** un lieu.
    "ou_acheter_pres": [
        (r"\bach[eè]te?\w*\b.{0,40}\bplus proche\b", 5.0),
        (r"\bplus proche\b.{0,40}\bach[eè]te?\w*\b", 5.0),
        (r"\bpoints? de vente\b.{0,30}\b(?:proche|pres|cote)\b", 5.0),
        (r"\bou ach[eè]te?\w*\b.{0,40}\b(?:pres|proche|cote) de\b", 4.5),
        (r"\bplus proche\b.{0,30}\b(?:vente|magasin|boutique)\w*", 4.5),
        (r"\bacheter\b.{0,20}\bpres de\b", 4.5),
        # « Où louer un Prospector proche de Crusader » — même outil, autre
        # liste d'offres. Le verbe seul ne suffit pas : c'est la proximité
        # qui fait basculer ici plutôt que chez `get_price`.
        (r"\blou[ée]?[rs]?\b.{0,40}\b(?:pres|proche|cote)\b", 5.5),
        (r"\blocation\b.{0,40}\b(?:pres|proche|cote)\b", 5.5),
    ],
    "get_price": [
        # « Où vendre mon RMC » partait aux gisements, qui répondaient — avec
        # raison mais à côté — « cette commodité ne se mine pas ». Le verbe de
        # la vente pèse comme celui de l'achat.
        (r"\b(?:re)?vendre\b|\becouler\b|\bfourguer\b", 3.5),
        (r"\bprix\b|\btarif\b", 3.5),
        (r"\bco[uû]te?\w*\b", 3.5),
        (r"\bach[eè]te?\w*\b|\bacquerir\b", 3.0),
        (r"\bvend\w*\b|\brevend\w*\b", 3.0),
        (r"\blou(?:er|e|ation)\w*\b", 3.5),
        (r"\bcombien\b.*\b(?:auec|uec|credit\w*)\b", 3.0),
        # « où trouver un P4-AR » : le joueur ne dit pas « acheter », il dit
        # « trouver ». Sans ce motif la question partait en recherche de
        # ressource minable, puis nulle part. Le poids reste sous celui de
        # `where_to_find_resource` : pour du quantanium, c'est bien le gisement
        # qu'on veut, et c'est le type d'entité qui tranchera.
        (r"\bou (?:je peux |on peut )?(?:trouver|avoir|choper)\b", 2.0),
        # Questions de suite : elles ne nomment rien, elles reprennent le
        # sujet précédent. « Liste-moi tous les points de vente » n'avait
        # aucune intention et tombait en échec.
        (r"\bpoints? de vente\b|\bvendeurs?\b|\bboutiques?\b", 3.5),
        (r"\bmagasins?\b|\bterminaux\b|\bterminals?\b", 3.0),
        (r"\bo[uù]\b.*\b(?:trouver|acheter)\b.*\bvaisseau\b", 2.5),
        (r"\bauec\b|\bcredit\w*\b", 1.5),
        # Filet pour « où fabriquer un Gladius » : un vaisseau n'a pas de
        # recette, donc `get_blueprint` n'y résout aucune entité et la question
        # ne trouvait plus aucune intention — « je n'ai pas compris ». Le poids
        # est bas exprès : dès qu'un blueprint existe, c'est lui qui répond.
        # Ici la portée « fabrication » fait dire l'essentiel : pas de recette,
        # mais un prix.
        (r"\bfabriqu\w*|\bcraft\w*|\brecette\b", 1.5),
    ],
    # « Vaut-il mieux acheter ou fabriquer un Omnisky IX ? » — la question
    # nomme les deux verbes, donc `get_price` (3,0 sur « acheter ») et
    # `get_blueprint` (3,0 sur « fabriquer ») se déclenchent tous les deux et
    # répondent chacun une moitié. C'est la **comparaison** qui distingue, pas
    # les verbes : les motifs ci-dessous exigent qu'elle soit formulée, et
    # pèsent assez pour passer devant la somme des deux.
    # « Comment je progresse chez Foxwell » — `get_mission_group` répond déjà
    # « dix paliers, quarante blueprints », mais en agrégat : il ne dit pas à
    # quel rang chaque chose s'ouvre. Les motifs exigent donc un mot de
    # **progression**, sinon le groupe reste la bonne réponse.
    "progression_dans": [
        (r"\bprogress\w+", 5.0),
        (r"\bechelle\b.{0,20}\b(?:rang|reputation|standing)\w*", 5.0),
        (r"\bque?ls? rangs?\b|\bpaliers?\b", 4.0),
        (r"\bmonter (?:en |ma |la )?(?:reputation|rang|standing)\w*", 4.5),
        (r"\bcomment (?:je |on )?(?:monte|grimpe|avance)\b", 4.0),
        (r"\ba que?l rang\b|\bquel rang (?:pour|faut)\b", 4.5),
    ],
    # « Compare l'armement d'un Gladius et d'un Arrow » — `compare_ships`
    # compare **une** statistique et ne dit pas avec quoi le DPS est fait.
    # Les motifs exigent un mot d'équipement : sans lui, « compare un Gladius
    # et un Arrow » reste une comparaison de caractéristiques, et c'est bien.
    "comparer_loadouts": [
        (r"\bcompare\w*\b.{0,30}\b(?:armement|equipement|loadout|armes?)\b", 5.5),
        (r"\b(?:armement|equipement|loadout)\b.{0,30}\bcompare\w*", 5.5),
        (r"\bque?ls? armes? (?:sur|a|ont|de)\b.{0,20}\bet\b", 4.5),
        (r"\barmement d origine\b|\bequipement d origine\b", 4.5),
        (r"\bmieux arme\b|\bmieux equipe\b", 4.5),
    ],
    # « Comment je fabrique un P8-AR de A à Z » — la recette seule ne suffit
    # pas, le joueur veut la suite. Les motifs exigent une marque d'**enchaîne-
    # ment** (« de A à Z », « tout ce qu'il faut », « étape par étape ») :
    # sans elle, `get_blueprint` répond, et il a raison.
    "plan_de_fabrication": [
        (r"\bde a a z\b|\bde bout en bout\b", 5.5),
        (r"\betapes? par etapes?\b|\bpas a pas\b", 5.5),
        (r"\btout ce qu il faut (?:pour|savoir)\b", 5.0),
        (r"\bplan (?:de |pour )?(?:fabrication|fabriquer|craft)\w*", 5.5),
        (r"\bcomment (?:je |on )?(?:fais|fait|fabrique)\b.{0,30}\bcomplet\w*", 5.0),
        (r"\bexplique moi tout\b|\bdetaille moi tout\b", 4.5),
        (r"\bguide (?:de |pour )?(?:fabrication|craft)\w*", 5.0),
        # « Comment obtenir un P8-AR de zéro » partait chez la seule recette —
        # audit du 2026-08-07. Pas de motif « comment obtenir … de zéro » :
        # il enjamberait l'entité, la règle documentée des motifs.
        (r"\bde zero\b|\bdepuis (?:le debut|rien)\b", 5.5),
    ],
    # « Les blueprints des missions à Stanton » partait sur une fiche de
    # mission au hasard — audit du 2026-08-07. Le résumé par organisation
    # répond ; le détail d'un groupe reste chez `get_mission_group`.
    "blueprints_par_systeme": [
        (r"\bblueprints? (?:des|de|dans les) missions?\b", 5.5),
        (r"\bquels? blueprints?\b", 4.5),
        (r"\bblueprints? (?:disponibles?|possibles?|a debloquer)\b", 5.0),
        # « Donne-moi les blueprints d'armes FPS de Pyro » répondait UNE
        # recette (PyroBurst résolu au hasard) — le catalogue par famille
        # répond, groupé par classe avec la marque.
        (r"\bblueprints? (?:d|de|des) ?(?:armes?|boucliers?|casques?|"
         r"armures?|generateurs?|refroidisseurs?)", 6.0),
        (r"\b(?:donne|liste|montre)\w*.{0,10}\bblueprints?\b", 4.5),
    ],
    # « C'est quoi les boucliers ? » — le catalogue d'une famille d'objets,
    # groupé, avec la marque. Le vocabulaire fermé des familles est le
    # garde-fou ; le singulier reste aux fiches et aux composants de
    # vaisseau.
    "catalogue_objets": [
        (r"\bboucliers\b|\brefroidisseurs\b|\bgenerateurs\b|\bradars\b|"
         r"\bmoteurs quantiques\b|\barmes (?:fps|personnelles|de vaisseaux?|"
         r"de minage)\b|\bcasques\b|\barmures\b", 4.0),
        # Ce tour interrogatif ne porte aucune intention sans une famille de
        # la ligne précédente. Le garder sous 1,5 retire le faux candidat sur
        # « quelles sont les forces du Wolf » ; avec une famille le total
        # reste largement au-dessus du seuil.
        (r"\bc est quoi les\b|\bquel(?:le)?s? sont les\b|\bliste\w* les\b",
         1.4),
    ],
    # « Quel minerai rapporte le plus » : le croisement prix de vente ×
    # gisements — aucun outil ne l'assemblait, le mineur le faisait à la main.
    "rentabilite_minage": [
        (r"\bminerais? (?:le plus|les plus) (?:rentables?|chers?|"
         r"lucratifs?)\b", 6.0),
        (r"\bquels? minerais?\b.{0,30}\brapporte\w*", 6.0),
        (r"\bminerais?\b.{0,25}\b(?:rapporte\w*|vaut|valent)\b", 5.0),
        (r"\bque miner pour\b.{0,25}\b(?:gagner|argent|credits?|uec)\b", 6.0),
        (r"\bminage (?:rentable|lucratif)\w*\b", 5.5),
        (r"\brentabilite\b.{0,20}\bminage\b|\bminage\b.{0,20}\brentabilite\b",
         5.5),
    ],
    "acheter_ou_fabriquer": [
        (r"\bachet\w+ ou (?:de |le |la |les )?(?:fabriqu|craft)\w*", 6.0),
        (r"\b(?:fabriqu|craft)\w+ ou (?:de |l )?achet\w*", 6.0),
        (r"\bvaut il mieux\b", 4.0),
        (r"\bmieux vaut\b", 3.5),
        (r"\bplus (?:rentable|interessant|economique|avantageux)\b", 3.5),
        (r"\bmoins cher\b.{0,30}\b(?:fabriqu|craft|achet)", 4.0),
        (r"\b(?:fabriqu|craft|achet)\w*\b.{0,30}\bmoins cher\b", 4.0),
        (r"\bcout de (?:fabrication|production|revient)\b", 4.5),
        (r"\bprix des (?:materiaux|ingredients|composants)\b", 4.5),
        # 5,5 et non 5,0 : « combien ça coûte de fabriquer un Frost-Star »
        # donne exactement 5,0 à `get_price` (3,5 sur « coûte » + 1,5 sur
        # « fabriqu »), et à égalité c'est lui qui gagnait, l'ordre du
        # dictionnaire le plaçant avant.
        (r"\bcombien (?:ca )?coute (?:de |a )?(?:fabriqu|craft)\w*", 5.5),
        (r"\bca vaut le cou[pt]\b", 3.0),
        (r"\brentab\w+\b.{0,25}\b(?:fabriqu|craft)", 4.0),
    ],
    "get_ship_hardpoints": [
        (r"\bemport\w*", 3.0),
        (r"\bhard ?point\w*", 3.0),
        (r"\barmement\b", 2.5),
        (r"\btourelle\w*", 2.5),
        (r"\bcanon\w*", 2.0),
        (r"\barme\w*\b", 1.5),
        (r"\bpylone\w*", 2.0),
        (r"\bequip\w+", 1.5),
        (r"\bmettre sur\b", 2.0),
        (r"\bmonter sur\b", 2.0),
        (r"\bloadout\b", 2.5),
        (r"\bmissile\w*", 1.5),
        (r"\bbouclier\w*", 1.0),
    ],
    # « Où se situe Grim HEX ? » — localiser un lieu, pas chercher un minerai.
    # Les deux se disent presque pareil ; c'est le **type d'entité** qui
    # tranche, comme partout ailleurs. Les poids passent devant
    # `where_to_find_resource` parce que « se situe » et « se trouve » ne
    # veulent dire qu'une chose, alors qu'« où trouver » est ambigu.
    # « C'est quoi Grim HEX », « c'est qui Wikelo » — décrire n'est pas donner
    # les statistiques. Le joueur qui demande « c'est quoi » veut savoir ce que
    # c'est ; les chiffres se proposent après, et seulement s'ils existent. Les
    # poids passent devant les fiches, qui répondaient à côté.
    "decrire": [
        (r"\bc est (?:quoi|qui)\b", 4.0),
        # Statut et prêt viennent du wiki, la fiche descriptive est leur
        # point d'entrée naturel : ce ne sont ni des stats de vol ni un prix.
        (r"\best (?:il |elle )?(?:sorti|sortie|disponible|volable)\b", 4.5),
        (r"\b(?:encore |en )?(?:concept|production)\b", 4.0),
        (r"\bloaners?\b|\bvaisseaux? de pret\b|\bprete\w* en attendant\b", 4.5),
        # « Les lieux majeurs de Stanton » partait chez le groupe de missions
        # (« cite » compte pour l'exhaustivité) : c'est la fiche du système
        # qui porte planètes, villes et stations — remarque de l'utilisateur.
        (r"\blieux (?:majeurs|principaux|importants|cles)\b", 4.5),
        # Et « lieux stanton » tout court restait sans réponse — quatre fois
        # au journal du 2026-08-07. Le mot nu suffit : l'entité fait le reste.
        # 2,5 et non 3,0 : à 3,0, « les lieux les plus proches de Lorville »
        # frôlait `nearest_locations` (3,5) à un demi-point — mesuré au banc.
        (r"\blieux\b", 2.5),
        # « Les lieux qui se trouvent à » : le complément rapproche du verbe
        # « trouver », donc des gisements — le préciser écarte la paire.
        (r"\blieux (?:de|d|qui|a|dans)\b", 1.0),
        (r"\bqu est ce que?\b", 4.0),
        (r"\bdecri\w+", 4.0),
        (r"\bqui est\b|\bqui sont\b", 3.5),
        (r"\bparle moi (?:de|du|d)\b", 3.5),
        (r"\bpresente (?:moi|nous)\b", 3.5),
        (r"\bhistoire (?:de|du|d)\b|\blore\b", 3.0),
        # Une faction publie une réaction par défaut. Elle ne permet pas de
        # promettre un comportement précis (« tire à vue »), mais répond à la
        # disposition générale si l'organisation est bien résolue.
        (r"\b(?:hostile|neutre|amical)\w*\b|\breaction\b|"
         r"\b(?:tirent?|attaquent?) (?:a )?vue\b", 3.5),
        # « Ce qu'il faut faire dans X », « les objectifs de X » : c'est le
        # briefing qu'on demande, et c'est ce que `decrire` rend pour une
        # mission. Sans ces motifs, la suite proposée juste au-dessus de la
        # réponse n'avait aucun moyen d'être formulée à la main.
        (r"\bce qu il faut faire\b|\bqu est ce qu il faut faire\b", 4.5),
        (r"\bobjectifs?\b|\bconsignes?\b|\bbriefing\b", 3.5),
        (r"\ben quoi (?:ca|elle) consiste\b|\bça consiste en quoi\b", 4.0),
    ],
    "where_is_location": [
        (r"\bou (?:se )?(?:situe|trouve|est)\b", 4.0),
        (r"\bou est\b|\bc est ou\b", 3.5),
        (r"\blocalisation\b|\bsitue\b", 3.5),
        (r"\bdans quel (?:systeme|coin)\b", 3.5),
        (r"\bautour de quoi\b|\bsur quelle (?:lune|planete)\b", 4.0),
        (r"\bou ca se trouve\b", 4.0),
    ],
    # **Cet outil répond « dans quoi », pas « où ».** Première version trop
    # gourmande : ses motifs prenaient « où miner de l'Agricium » à
    # `where_to_find_resource`, et le cahier l'a signalé. Comparé côte à côte,
    # le voisin répond mieux à cette question-là — il **nomme les lieux**,
    # « Cellin, Daymar, Yela », quand celui-ci ne rend que des comptes et des
    # teneurs. Savoir où aller prime sur savoir en quelle concentration.
    #
    # Ce qui reste ici est ce que le voisin ne sait pas dire : la
    # **composition** d'un filon, et les minerais qu'on récolte en prime dans
    # des filons portant un autre nom. L'or apparaît dans 157 gisements dont
    # quatre seulement s'appellent Gold.
    # « Où miner pour fabriquer un P6-LR » : le plan groupé d'une recette —
    # le coin où tout extraire — pas trois listes de gisements. La question
    # partait chez `get_blueprint`, qui répondait la recette sans les lieux.
    "ou_miner_pour": [
        (r"\bminer\b.{0,30}\b(?:fabriquer|crafter|craft|recette)\b", 6.5),
        (r"\b(?:fabriquer|crafter)\b.{0,30}\bminer\b", 6.5),
        (r"\bminer\b", 2.0),
        (r"\bextraire\b", 2.0),
    ],
    "ou_miner": [
        (r"\bquel\w* (?:filons?|gisements?|rochers?)\b", 5.0),
        # « Quel type de filon comporte le plus d'or » : « type de » s'intercale
        # entre « quel » et « filon », et la question partait aux gisements
        # simples — qui ne classent pas par teneur. Remarque de l'utilisateur :
        # c'est la composition qui répond, avec son espérance chiffrée.
        (r"\btypes? de filons?\b", 5.0),
        (r"\bcomporte\w*\b", 2.5),
        (r"\bcomposition (?:du|des|d un|d une)?\s*(?:filon|gisement|rocher)", 5.5),
        (r"\bcontien\w+ (?:du|de l|des|de la)\b", 4.5),
        (r"\bteneur\b|\bconcentration\b", 4.0),
        (r"\ben prime\b|\bsous.produit\b|\ben plus dans\b", 4.0),
    ],
    "where_to_find_resource": [
        (r"\bc est quoi\b|\bqu est ce que?\b|\bdecri\w+", 2.0),
        (r"\bou (?:je |on |tu )?(?:peux |peut |trouve|trouv)", 3.0),
        # « où » nu, une fois les accents retombés, se confond avec la
        # conjonction « ou ». On l'accepte quand même : une intention sans
        # entité résoluble est écartée juste après, donc « le Gladius ou le
        # Hornet » ne produira jamais une recherche de ressource.
        (r"\bou\b", 1.5),
        (r"\btrouv\w+", 2.0),
        (r"\bmine\w*\b", 2.0),
        (r"\bminage\b", 2.5),
        (r"\bminer\b", 2.5),
        (r"\bgisement\w*", 3.0),
        (r"\bextrai\w+", 2.0),
        (r"\brecolt\w+", 2.0),
        (r"\bfilon\w*", 2.5),
        (r"\bacheter\b", 1.5),
        (r"\bvendre\b", 1.5),
        (r"\bcherch\w+", 1.5),
    ],
    # Même vocabulaire que la réputation, avec un bonus aux tournures
    # collectives. L'arbitrage réel ne vient pas des motifs mais du type
    # d'entité : si une *organisation* se résout, c'est une question de groupe ;
    # si c'est un titre de contrat, c'est une question de mission précise.
    # « Combien rapporte cette mission », « les missions les mieux payées ».
    # Le montant est en base depuis le premier jour — 2 345 contrats sur
    # 5 108 — et aucune formulation n'y menait. L'outil n'a pas d'entité à
    # résoudre : l'organisation est facultative, le classement se fait sur
    # tout le catalogue à défaut.
    # « Quelles missions se passent à Onyx » — la liste des missions d'un
    # complexe. Au-dessus de `missions_payantes` sur son vocabulaire : ces
    # missions n'ont aucun montant fixe (mesuré : 0 sur 20), un classement ne
    # peut pas répondre. Le préparateur exige le site.
    "missions_du_site": [
        (r"\bse ?(?:passent?|deroulent?)\b", 5.0),
        # 4,5 : la baisse de « missions » à 2,0 avait posé cet outil à un
        # quart de point de `get_mission_reputation` sur « que donne les
        # missions secure site en blueprint » — le mot spécifique remonte.
        (r"\b(?:complexes?|sites?|installations?)\b", 4.5),
        # 2,0 : à 2,5, « donne la mission » mettait cet outil à un demi-point
        # de `missions_par_activite` (3,0) — mesuré au banc. Les questions de
        # site gardent 8,0 et plus par leurs mots spécifiques.
        (r"\bmissions?\b", 2.0),
        (r"\bquel(?:le)?s? missions?\b", 2.0),
    ],
    # « Donne-moi les missions de Pyro » mourait en incompris : huit cents
    # titres n'ont pas de liste, ils ont un **menu** — par type ou par
    # donneur. Le préparateur exige un système et l'absence de tout filtre
    # plus précis (activité, paye, difficulté, organisation).
    "panorama_missions": [
        # `(?<!que )` : « que donne les missions secure site » est une
        # question de récompense, pas une demande de liste — le motif
        # impératif la frôlait à un demi-point de `missions_du_site`.
        (r"(?<!que )(?<!qui )\b(?:donne|fais|liste|montre)\w*"
         r".{0,14}\bmissions?\b", 5.0),
        (r"\bles missions? (?:de|d|a|dans)\b", 4.0),
        # 2,0 : à 2,5, « missions » nu frôlait `missions_par_activite` à un
        # demi-point — mesuré au banc.
        (r"\bmissions?\b", 2.0),
        (r"\btous les (?:types|donneurs|missionnaires|commanditaires)\b", 5.0),
    ],
    # « Y a-t-il des missions de minage à Stanton ? » — le jeu type ses
    # missions, et aucune formulation ne menait à ce typage : la question
    # tombait entre `vaisseaux_par_metier` (qui exige un mot de vaisseau) et
    # les gisements. Le préparateur exige une activité reconnue.
    "missions_par_activite": [
        (r"\bmissions? (?:de|d)\b", 4.5),
        (r"\bmissions?\b", 3.0),
        (r"\b(?:types?|genres?) de missions?\b", 3.0),
    ],
    # « Chris, t'es là ? » — la ligne de vie. Un joueur sans réponse ne sait
    # pas si le bot est éteint ou s'il n'a pas compris : cette question-là
    # doit **toujours** répondre. Les apostrophes tombent à la normalisation
    # (« t'es » → « t es »).
    "ligne_de_vie": [
        # « CHRIS » tout seul — un appel, pas une question : il mourait en
        # « je n'ai pas compris », ce qui est exactement ce qu'un appel
        # cherche à lever.
        (r"^chris(?: roberts)?$", 5.0),
        (r"\b(?:tu es|t es|es tu|est tu|vous etes) (?:la|toujours la|"
         r"encore la|vivant|allume|operationnel|en ligne|reveille|dispo\w*)\b",
         6.0),
        (r"\btu (?:m entends|nous entends|me recois|nous recois)\b", 6.0),
        (r"\btu (?:reponds|fonctionnes?|marches?|tournes?)\b", 5.0),
        (r"\bping\b", 5.0),
        (r"\bare you (?:there|alive|on|up)\b", 5.0),
        # **Les formes réellement tapées, relevées au journal.** « t'es
        # offline ? » coûtait 10,2 s d'analyste puis 12,1 s sans réponse ;
        # « ça marche là ? » n'était routé nulle part. Le garde-fou de cet
        # outil est la brièveté, pas le motif : « est-ce que le minage
        # marche là-bas » nomme une activité et part ailleurs.
        (r"\b(?:t es|tu es|es tu|vous etes) (?:offline|hors ligne|mort|"
         r"parti|absent|down|ko|en panne)\b", 6.0),
        (r"^(?:ca|c est|ça) marche(?: la| toujours| encore| bien)?\s*\??$", 6.0),
        (r"^(?:la )?on est bien\s*\??$", 5.0),
        (r"^(?:tu dors|tu es la|coucou|hello|salut|yo)\s*\??$", 5.0),
    ],
    "missions_payantes": [
        # « Combien de missions rapportent plus de 50 000 aUEC » — le
        # plancher en déterministe, la question partait chez l'analyste.
        # Le motif ne couvre que le verbe : le montant reste résoluble.
        # 5,5 et non 4,5 : à 4,5 la question faisait égalité parfaite avec
        # get_price et frontière fragile avec combien_dans_la_soute —
        # mesuré au banc, l'écart d'un point sort des deux zones.
        (r"\brapportent? (?:plus|au moins|au dela|au-dela)\b", 5.5),
        (r"\bmissions? les (?:mieux|plus) pay\w+", 4.5),
        (r"\b(?:mieux|plus) pay\w+", 3.5),
        (r"\bqui (?:rapporte|paye) le plus\b", 4.5),
        # « Qui paie le plus / le mieux » : la graphie « paie » n'était pas
        # couverte, et la question partait chez la fiche de réputation —
        # journal du 2026-08-07. « Paye » non plus — « combien paye les
        # missions faciles de pyro » sortait une fiche au hasard.
        (r"\bpa[iy]en?t?\b.{0,15}\b(?:le plus|le mieux|bien)\b", 4.5),
        (r"\bcombien pa[iy]en?t?\w*\b", 4.5),
        (r"\bpa[iy]en?t?s?\b", 3.0),
        # « Les missions faciles à Stanton » : l'étiquette de difficulté est
        # un axe de cet outil — classées par paye parmi les étiquetées.
        # 4,0 : à 3,5, « mission facile à stanton » frôlait
        # `missions_par_activite` à un demi-point — mesuré au banc.
        (r"\bfaciles?\b|\bdifficiles?\b|\btranquilles?\b", 4.0),
        (r"\bmissions? rentables?\b", 4.0),
        (r"\brapporte\w* le plus\b", 4.0),
        (r"\bfarm\w*\b.{0,20}\b(?:uec|argent|credits?)\b", 3.5),
        # « Quelles missions sont disponibles dans Pyro » n'avait aucun outil :
        # `get_mission_group` demande une organisation, et « pyro » n'en est
        # pas une — la question tombait chez `get_blueprint`, qui résolvait
        # *PyroBurst Scattergun* et répondait à côté. Le lieu est déjà un
        # argument de cet outil, il ne manquait que le chemin pour l'atteindre.
        # `que?ls?` ne couvre pas « quelles » — il vaut « qu » + « e » facultatif
        # + « l » + « s » facultatif, soit quel/quels/qul/quls. Le féminin
        # pluriel demande son propre groupe, et c'est la forme la plus tapée.
        (r"\bquel(?:le)?s? (?:missions?|contrats?)\b", 3.0),
        (r"\bmissions? (?:disponibles?|dispos?|proposees?)\b", 3.5),
        (r"\bsont disponibles?\b|\bil y a comme missions?\b", 3.0),
    ],
    "get_mission_group": [
        (r"\bles missions\b", 3.0),
        (r"\bmissions\b", 2.0),
        # « C'est quoi les blueprints pour Eckhart Security » — la question
        # nomme l'organisation : c'est sa vue d'ensemble, volet blueprints.
        # 5,0 : à 4,5, `decrire` restait à un demi-point — mesuré au banc.
        (r"\bblueprints? (?:pour|chez)\b", 5.0),
        (r"\bpatrouille\w*", 2.0),
        (r"\borga\w*", 2.0),
        (r"\bfaction\b", 2.0),
        (r"\breputation\b", 2.0),
        (r"\brepu\w*", 1.5),
        (r"\brang\w*", 1.5),
        (r"\bpalier\w*", 2.5),
        (r"\bdebloqu\w+", 2.0),
        (r"\bprogress\w+", 2.0),
        (r"\bchez\b", 1.0),
    ],
    "get_mission_reputation": [
        # « Combien rapporte The Price of Freedom » : la paye est sur la fiche
        # de la mission, encore fallait-il un mot pour y mener.
        (r"\brapporte\w*\b|\bpaye\b|\bpaie\b|\bgain\w*\b|\brecompense\w*", 3.0),
        # « Que donne la mission Secure Site en blueprint » partait chez
        # `get_blueprint`, qui résolvait « SecureHyde » — un objet — au lieu du
        # contrat homonyme à 100. C'est l'entité qui départage du groupe :
        # « Secure Site » est un contrat, « Eckhart Security » une org.
        # `donnent?` ne matche **pas** « donne » — c'est « donnen » + t
        # facultatif. Même famille que `que?ls?` qui ne couvrait pas
        # « quelles » : les terminaisons s'écrivent en toutes lettres.
        (r"\bque (?:donne\w*|rapporte\w*)\b", 3.5),
        # 0,75 est mesuré au banc, pas esthétique : à 1,5, « que donne les
        # missions eckart security » faisait 5,0 partout contre le groupe ;
        # à 1,0, la question « secure site » restait à un demi-point du
        # groupe. Les trois questions du corpus exigent la fourchette
        # 3,5 + 0,75 (+ 1,5) — soit 4,25 et 5,75, à plus d'un demi-point de
        # tous les voisins.
        (r"\bmissions?\b", 0.75),
        (r"\bblueprints?\b", 1.5),
        (r"\breputation\b", 3.0),
        # « répu », « réput », « reputation » — l'abréviation orale est la
        # forme la plus fréquente en vocal.
        (r"\brepu\w*", 2.5),
        (r"\bstanding\b", 2.5),
        (r"\brang\b", 2.0),
        (r"\bprerequis\b", 2.5),
        (r"\bdebloqu\w+", 2.0),
        (r"\bcontrat\b", 1.5),
        (r"\bacces (?:a|au)\b", 1.5),
    ],
}

_COMPILED = {
    tool: [(re.compile(pattern), weight) for pattern, weight in patterns]
    for tool, patterns in _INTENTS.items()
}

# Mots qui portent l'intention : ils ne peuvent pas désigner l'entité
# cherchée. Sans ça, « armes » se résout en un objet nommé « Arms ».
_INTENT_WORDS = re.compile(
    r"\b(?:emport\w*|hard ?point\w*|armement|arme\w*|tourelle\w*|canon\w*|"
    r"blue ?print\w*|fabriqu\w*|craft\w*|recette|construi\w+|ingredient\w*|"
    r"trouv\w+|mine\w*|minage|miner|gisement\w*|extrai\w+|recolt\w+|filon\w*|"
    # « repu », « réput », « réputation » : l'abréviation orale doit être
    # retirée elle aussi, sinon elle résout la mission « Reputation Management ».
    r"repu\w*|standing|rang|prerequis|debloqu\w+|mission\w*|contrat\w*|"
    r"loadout|equip\w+|materiau\w*|composant\w*|missile\w*|bouclier\w*|"
    r"vaisseau\w*|ship|point\w*|acheter|vendre|chercher|besoin)\b"
)

MIN_INTENT_SCORE = 1.5
