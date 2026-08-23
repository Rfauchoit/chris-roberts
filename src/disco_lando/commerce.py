"""Le commerce — prix, routes, loueurs, coûts de fabrication comparés.

Découpé de `queries.py` le 2026-08-07, mécaniquement — même règle que les
missions : l'ordre du fichier d'origine, la façade en ré-export, et les
imports différés quand une fonction traverse les familles (la recette vit
chez la fabrication, les payes chez les missions).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ._socle import NotFound, _dict, _row
from .missions import group_missions, missions_payantes
from .resultats import fraicheur_jeu, fraicheur_uex, qualite_reponse
from .resolver import Resolution, mots_inexpliques, resolve
from .voyage import _ecart, lieu_du_terminal, systemes_risques


def _prix_observe(con: sqlite3.Connection, nom: str):
    """Ce que les membres ont payé, ou None.

    **Import différé, et échec silencieux voulu.** La base de guilde est
    une base de *membres* : elle peut être absente (un Chris public sans
    compagnon), verrouillée, ou vide. Le prix du catalogue doit répondre
    dans tous ces cas — une observation manquante n'est pas une panne, et
    le §2 interdit au cœur de dépendre d'un frontend.
    """
    try:
        from .guilde.observations import prix_observe

        return prix_observe(con, nom)
    except Exception:  # noqa: BLE001  (base absente, verrouillée, vide)
        return None


def get_price(con: sqlite3.Connection, query: str,
              *, portee: str = "tout", systeme: str | None = None,
              autres: list[str] | None = None) -> dict[str, Any]:
    """Le prix d'un article — et de ses compagnons de question.

    « Le prix de l'iron et de l'or » jetait l'or en silence — ligne 3 de la
    grille : une conjonction ne perd jamais son second terme. Chaque article
    supplémentaire reçoit sa propre réponse, enchaînée à la première.
    """
    data = _prix_d_un(con, query, portee=portee, systeme=systeme)
    if autres:
        data["autres_prix"] = []
        for terme in autres[:3]:
            try:
                data["autres_prix"].append(
                    _prix_d_un(con, terme, portee=portee, systeme=systeme))
            except NotFound:
                continue
    return data


def _prix_d_un(con: sqlite3.Connection, query: str,
               *, portee: str = "tout",
               systeme: str | None = None) -> dict[str, Any]:
    """« Combien coûte un Gladius ? », « où acheter un Panther Repeater ? »

    Les prix ne sont **pas** dans les fichiers du jeu : CIG les en a retirés à
    partir de la 3.20. Ils viennent d'UEX, alimenté par des relevés de joueurs,
    et vivent dans une table à part — horodatée, jamais jointe en dur aux
    tables statiques. La différence entre « lu dans les fichiers » et
    « rapporté par un joueur » doit rester visible jusque dans la réponse.

    **Les types sont essayés dans l'ordre, pas mélangés.** « Gladius » désigne
    un vaisseau, mais le catalogue contient aussi « Gladius Model », « Gladius
    Ship Armor » et « Gladius Pirate Livery » — des bibelots qui sortent au
    même score et volaient la question. On prend le premier type qui donne un
    prix : c'est le seul arbitre qui ne se trompe pas, puisqu'une réponse sans
    prix n'est pas une réponse à « combien ça coûte ».
    """
    # Résolution par type, puis arbitrage au score — avec une marge. Mesuré :
    #
    #   « fr 76 »            ship  51   item 100   -> l'objet, sans hésiter
    #   « panther repeater » ship  68   item  95   -> l'objet
    #   « gladius »          ship  90   item  93   -> le VAISSEAU
    #
    # Le dernier cas interdit de suivre le meilleur score : « Gladius Model »,
    # un bibelot, bat le vaisseau de trois points. En deçà de la marge, on
    # garde l'ordre de priorité, où le vaisseau passe devant.
    candidats = []
    for rang, type_entite in enumerate(("ship", "item", "commodity", "resource")):
        res = resolve(con, query, entity_types=(type_entite,))
        if res.best is not None:
            candidats.append((rang, res))

    if not candidats:
        raise NotFound(query)

    # Arbitrage par **qualité de correspondance** avant le score. Mesuré :
    #
    #   « coda »     ship = rien          item = flat/93   -> l'objet
    #   « gladius »  ship = fts/90        item = flat/93   -> le VAISSEAU
    #
    # Un seul point sépare les deux cas, mais ils n'appellent pas la même
    # réponse : « coda » ressemble phonétiquement à « cutty », ce qui est un
    # à-peu-près, tandis que « gladius » colle au nom du vaisseau. On écarte
    # donc d'abord tout type dont la correspondance est franchement moins
    # bonne, et le score ne tranche qu'entre égaux.
    # La qualité **ordonne**, elle n'écarte pas : écarter faisait perdre le
    # repli, et « gladius » ne trouvait plus rien du tout — le bibelot
    # « Gladius Model » colle mieux au nom mais n'a ni prix ni recette, il
    # faut donc pouvoir redescendre sur le vaisseau.
    #
    # À qualité égale, l'écart de score départage, et en deçà de la marge
    # c'est l'ordre de priorité qui tranche — vaisseau devant objet.
    meilleur = max(c[1].best.score for c in candidats)

    def _qualite(res: Resolution) -> int:
        # **Un candidat qui n'explique pas tous les mots tapés est un
        # à-peu-près, quel que soit son étage.** Journal du 2026-08-19 :
        # « panther repeater » hésitait avec *Prowler Panthera Livery* —
        # « repeater » ne correspond à rien dans ce nom, et c'est ça qui le
        # disqualifie, pas son score. C'est la règle d'« omnisky xi »,
        # appliquée à l'arbitrage des types.
        if mots_inexpliques(query, res.best.alias):
            return 1
        return QUALITE.get(res.best.via, 0)

    candidats.sort(key=lambda c: (
        -_qualite(c[1]),
        # **Un 100 exact passe devant l'ordre des types** — la règle de
        # `decrire` : un 100 n'est pas « un score élevé », il signifie que
        # le terme tapé EST le nom. Journal du 2026-08-19 : « le prix du
        # quantainium » rendait la *"Quantainium" Water Bottle* (flat, 93)
        # parce que l'ordre mettait `item` avant `commodity` — devant le
        # minerai lui-même, exact à 100.
        c[1].best.score < 100,
        c[1].best.score < meilleur - MARGE_TYPE,
        c[0],
    ))

    # **Un candidat sans aucun prix ne doit pas court-circuiter celui qui en a.**
    # Mesuré sur une question réelle du journal : « combien d'UEC coûte un Star
    # Runner » sortait la *Mercury Star Runner Flight Jacket*, déclarée
    # exclusive donc rendue immédiatement — alors que le *Crusader Mercury Star
    # Runner*, à égalité de score, est vendu. On repère donc d'avance qui a des
    # cotations, et le raccourci « c'est un objet exclusif » ne s'applique que
    # si personne d'autre ne peut répondre mieux.
    achetables = {res.best.entity_id for _, res in candidats
                  if con.execute(
                      "SELECT 1 FROM uex_prices WHERE ref_uuid = ? "
                      "AND price_buy IS NOT NULL LIMIT 1",
                      (res.best.entity_id,)).fetchone()}

    # **Un candidat moins bien classé ne répond pas à la place du premier quand
    # les deux collent aussi bien au nom.** Mesuré sur une question réelle du
    # journal : « combien d'UEC coûte un Star Runner » rendait la *Mercury Star
    # Runner Flight Jacket* — ramassable en butin, donc « quelque chose à
    # dire » — alors que le *Crusader Mercury Star Runner*, même score et même
    # étage, n'a simplement aucun relevé UEX. Ne rien savoir du bon vaisseau
    # vaut mieux que tout savoir d'une veste.
    #
    # Le repli reste ouvert dès que le premier colle **moins bien** : c'est le
    # cas « Gladius Model » (93, flat) contre *Aegis Gladius* (90, fts), où il
    # faut pouvoir redescendre sur le vaisseau.
    tete = candidats[0][1].best
    egaux = {res.best.entity_id for _, res in candidats
             if res.best.score == tete.score and res.best.via == tete.via}

    derniere: Resolution | None = candidats[0][1]
    for _, res in candidats:
        # **Le repli ne descend jamais sur un à-peu-près.** Mesuré :
        # « 'WARLORD' Cannon » (exact, 100) n'a ni relevé, ni recette, ni
        # butin — et la boucle glissait jusqu'à *Neon* (fuzzy, 77), une
        # drogue, parce qu'elle a un prix. La règle du Star Runner vaut ici
        # aussi : ne rien savoir du bon objet vaut mieux que tout savoir
        # d'un à-peu-près. Le frein ne joue que contre les qualités
        # token/fuzzy : descendre de « Gladius Model » (flat) au vaisseau
        # (fts) reste permis — c'est le repli documenté qui donne le prix
        # du Gladius.
        if (res.best.entity_id != tete.entity_id
                and QUALITE.get(res.best.via, 0) <= 1
                and QUALITE.get(tete.via, 0) >= 3):
            break
        derniere = res
        if res.best.entity_id in egaux and res.best.entity_id != tete.entity_id:
            continue

        # L'exclusivité se vérifie sur l'UUID **exact**, avant tout repli.
        # « Venture Arms Envy » est une déclinaison d'abonnement ; le repli sur
        # le modèle de base rendait le prix de « Venture Arms », un autre
        # coloris. Dire « c'est un objet d'abonnement » est plus juste que
        # donner le tarif d'un voisin.
        exact = con.execute(
            "SELECT 1 FROM uex_prices WHERE ref_uuid = ? LIMIT 1",
            (res.best.entity_id,)).fetchone()
        if not exact:
            exclusif_direct = _dict(_row(
                con, "SELECT exclusive FROM uex_items WHERE uuid = ?",
                res.best.entity_id))
            # …sauf si un autre candidat, lui, est réellement en vente.
            autre_vendu = achetables - {res.best.entity_id}
            if exclusif_direct and exclusif_direct["exclusive"] and not autre_vendu:
                return {"resolution": res, "name": res.best.name,
                        "offers": [], "rentals": [], "craft": None, "loot": None,
                        "exclusive": exclusif_direct["exclusive"],
                        "portee": portee, "fetched_at": None, "source": "jeu"}

        lignes = _cotations(con, res)
        # Fabrication et butin sont calculés **dans tous les cas**, pas
        # seulement en l'absence de prix. Un Coda s'achète, se loue et se
        # fabrique : n'en dire qu'un tiers parce qu'un prix existe serait une
        # réponse tronquée. Les voies d'obtention s'additionnent.
        fabrication_tj = _fabrication(con, res)
        butin_tj = _dict(_row(
            con, "SELECT lootable, loot_source FROM items WHERE uuid = ?",
            res.best.entity_id))
        butin_tj = butin_tj if butin_tj and butin_tj["lootable"] else None

        if not lignes:
            # Un composant sans prix n'est pas forcément une lacune de relevé :
            # dans Star Citizen, une partie de l'équipement **ne s'achète pas**
            # et se fabrique. 1 294 objets de notre catalogue sont dans ce cas.
            # Répondre « je n'ai pas le prix » serait exact et trompeur ; la
            # vraie réponse est « ça ne s'achète pas, voilà la recette ».
            # Un objet de souscription n'est ni une lacune de relevé, ni un
            # oubli : il ne s'obtient tout simplement pas en jeu. C'est la
            # réponse la plus utile qu'on puisse donner à « où acheter ça ».
            exclusif = _dict(_row(
                con, "SELECT exclusive FROM uex_items WHERE uuid = ?",
                res.best.entity_id))
            # **Un vaisseau sans relevé UEX a souvent un prix quand même —
            # en dollars.** Journal du 2026-08-19 : « combien coûte un
            # aurora » répondait « je n'ai pas de prix », alors que le wiki
            # porte le prix pledge de 229 vaisseaux (l'Aurora ne se vend
            # pas en aUEC : aucun relevé, et ce n'est pas une lacune). Dire
            # « 20 $ au pledge store » est la réponse ; se taire faisait
            # passer une réalité du jeu pour un trou de données.
            pledge = _dict(_row(
                con, "SELECT msrp, pledge_url FROM wiki_vehicles "
                     "WHERE uuid = ? AND msrp IS NOT NULL",
                res.best.entity_id))
            if fabrication_tj or butin_tj or exclusif or pledge:
                return {"resolution": res, "name": res.best.name,
                        "offers": [], "rentals": [], "craft": fabrication_tj,
                        "loot": butin_tj,
                        "exclusive": (exclusif or {}).get("exclusive"),
                        "msrp": (pledge or {}).get("msrp"),
                        "portee": portee, "fetched_at": None, "source": "jeu"}
            continue
        if lignes:
            # La location est séparée de l'achat : « où louer un Cutlass »
            # et « combien coûte un Cutlass » n'ont pas la même réponse, et
            # mélanger deux millions d'aUEC avec cinquante mille serait
            # trompeur.
            offres = [l for l in lignes if l.get("kind") != "rental"]
            locations = [l for l in lignes if l.get("kind") == "rental"]
            hors_systeme = 0
            if systeme:
                # « À Stanton » filtre les relevés — ligne 2 de la grille :
                # « où acheter un Coda à Stanton » listait les boutiques de
                # Pyro sans un mot.
                avant = len(offres) + len(locations)
                offres = [l for l in offres
                          if (l.get("star_system") or "").lower()
                          == systeme.lower()]
                locations = [l for l in locations
                             if (l.get("star_system") or "").lower()
                             == systeme.lower()]
                hors_systeme = avant - len(offres) - len(locations)
            return {
                "resolution": res,
                "name": lignes[0]["name"],
                "offers": offres,
                "rentals": locations,
                "craft": fabrication_tj,
                "loot": butin_tj,
                "exclusive": None,
                "portee": portee,
                "systeme": systeme,
                "hors_systeme": hors_systeme,
                "fetched_at": lignes[0]["fetched_at"],
                "source": "uex",
                # **Le prix payé, quand on l'a vu passer.** UEX est de la
                # saisie communautaire qui vieillit ; un achat du Game.log
                # est une preuve horodatée. Mesuré : « Double Dog » est
                # relevé à 24 aUEC et a été payé 5. C'est une
                # **observation**, jamais une donnée de jeu — le rendu est
                # tenu de le dire (sprint 39, US-1).
                "observe": _prix_observe(con, lignes[0]["name"]),
            }
    # Rien à dire d'aucun candidat. Nommer quand même celui qu'on a reconnu :
    # « je n'ai pas de relevé pour le Crusader Mercury Star Runner » situe le
    # trou, là où `NotFound` laisse croire qu'on ne connaît pas le vaisseau.
    if tete is not None:
        return {"resolution": candidats[0][1], "name": tete.name,
                "offers": [], "rentals": [], "craft": None, "loot": None,
                "exclusive": None, "portee": portee,
                "fetched_at": None, "source": "uex"}
    raise NotFound(query, derniere)


def _fabrication(con: sqlite3.Connection, res: Resolution) -> dict[str, Any] | None:
    """Le blueprint qui produit cette entité, s'il existe.

    Contrairement aux prix, cette information vient des **fichiers du jeu** :
    elle ne vieillit pas et ne dépend d'aucun relevé de joueur.
    """
    bp = _dict(_row(con, "SELECT * FROM blueprints WHERE output_uuid = ?",
                    res.best.entity_id))
    if bp is None:
        return None

    # Les missions qui distribuent le blueprint : « ça ne s'achète pas » sans
    # dire où l'obtenir laisserait le joueur au même point.
    missions = [dict(r) for r in con.execute(
        "SELECT DISTINCT c.title, c.mission_giver, c.system, c.family, "
        "       c.min_standing_name, c.min_standing_value "
        "FROM blueprint_sources bs "
        "JOIN contract_reward_pools crp ON crp.pool_uuid = bs.pool_uuid "
        "JOIN contracts c ON c.uuid = crp.contract_uuid "
        "WHERE bs.blueprint_uuid = ? AND c.title IS NOT NULL "
        "  AND c.not_for_release = 0 AND c.work_in_progress = 0",
        (bp["uuid"],),
    )]
    # Distinguer « aucune source connue » de « source connue mais missions
    # pas encore sorties » : le jeu référence des pools dont les contrats sont
    # marqués non publiés. Dire « je ne sais pas » dans ce cas serait faux.
    sources = con.execute(
        "SELECT COUNT(*) FROM blueprint_sources WHERE blueprint_uuid = ?",
        (bp["uuid"],)).fetchone()[0]
    ingredients = con.execute(
        "SELECT COUNT(*) FROM blueprint_ingredients bi "
        "JOIN blueprint_tiers bt ON bt.id = bi.tier_id "
        "WHERE bt.blueprint_uuid = ?", (bp["uuid"],)).fetchone()[0]

    return {"blueprint": bp, "groups": group_missions(con, missions),
            "mission_count": len(missions), "source_count": sources,
            "ingredient_count": ingredients}


# Écart de score en deçà duquel deux types se valent, et où l'ordre de
# priorité tranche. Trois points séparaient « Gladius Model » du vaisseau.
MARGE_TYPE = 8.0


# Qualité des étages de résolution, du plus sûr au plus approximatif. Coller
# au nom vaut mieux que lui ressembler, et l'écart de score ne le dit pas :
# un match phonétique à 92 n'a pas la valeur d'un match exact à 93.
#
# **`fts` vaut `flat` depuis le 2026-08-19, et c'est le journal qui l'a
# montré.** « Combien coûte un arrow » répondait le missile « 'Arrow' I »
# à 240 aUEC — le joueur voulait l'Anvil Arrow. Les deux étages disent la
# même chose ici : « arrow » est *entièrement expliqué* par les deux noms,
# `flat` l'a trouvé en préfixe collé et `fts` en mot entier — deux
# implémentations d'une même correspondance, pas deux qualités. En les
# hiérarchisant, le bibelot ou le missile homonyme court-circuitait la
# marge et l'ordre des types, et le commentaire de `_prix_d_un` qui
# promettait « gladius → le VAISSEAU » décrivait un chemin mort : le
# vaisseau ne gagnait que parce que « Gladius Model » n'a rien à vendre.
# Dès que l'homonyme avait un prix (le missile Arrow), il répondait à la
# place du vaisseau. La vraie frontière de qualité est entre « le nom
# colle » (exact, flat, fts) et « le nom ressemble » (token, fuzzy).
QUALITE = {"exact": 5, "flat": 5, "fts": 5, "token": 1, "fuzzy": 1}


_COTATION = ("SELECT kind, name, terminal, star_system, price_buy, price_sell, "
             "fetched_at FROM uex_prices ")


def _cotations(con: sqlite3.Connection, res: Resolution) -> list[dict[str, Any]]:
    """Cotations d'une entité résolue, avec deux replis successifs."""
    lignes = [dict(r) for r in con.execute(
        _COTATION + "WHERE ref_uuid = ? ORDER BY price_buy IS NULL, price_buy",
        (res.best.entity_id,),
    )]
    if lignes:
        return lignes

    # Repli par nom exact : une commodité peut porter un UUID différent d'un
    # côté et de l'autre, là où vaisseaux et objets sont fiables.
    lignes = [dict(r) for r in con.execute(
        _COTATION + "WHERE name = ? COLLATE NOCASE "
        "ORDER BY price_buy IS NULL, price_buy",
        (res.best.name,),
    )]
    if lignes:
        return lignes

    # Repli par modèle de base. Notre catalogue contient des variantes que le
    # marché ne référence pas — « Cutlass Black PYAM Exec » est une édition
    # spéciale, seul « Cutlass Black » se vend. On raccourcit le nom mot à mot.
    mots = (res.best.name or "").split()
    for coupe in range(len(mots) - 1, 1, -1):
        # `LIKE '%' || base` et non l'égalité : UEX préfixe du constructeur —
        # « Drake Cutlass Black » — là où notre variante ne le porte pas.
        lignes = [dict(r) for r in con.execute(
            _COTATION + "WHERE name LIKE '%' || ? COLLATE NOCASE "
            "ORDER BY price_buy IS NULL, price_buy",
            (" ".join(mots[:coupe]),),
        )]
        if lignes:
            return lignes
    return []


def get_trade_route(con: sqlite3.Connection, query: str, *,
                    system: str | None = None, cargo: float | None = None,
                    budget: float | None = None, ship: str | None = None,
                    limit: int = 5) -> dict[str, Any]:
    """« Quelle est la meilleure route commerciale ? »

    Une route se calcule, elle ne se lit pas : acheter une commodité là où
    elle est la moins chère, la revendre là où elle est la mieux payée, et
    prendre la différence par SCU. C'est le seul outil de ce projet qui
    produise un chiffre absent de toute source — mais il ne l'invente pas, il
    le **soustrait**, et les deux opérandes sont dans la base.

    Quatre filtres, chacun pour une raison :

    - `price_buy > 0` — un terminal qui affiche 0 ne vend pas, il rachète.
    - `scu_available > 0` — une marge sur un stock vide n'est pas une route.
    - `scu_demand > 0` — un acheteur sans demande ne prend aucune cargaison.
    - même commodité des deux côtés, évidemment.

    Le classement se fait sur le gain **réalisable** — marge multipliée par ce
    qu'on peut effectivement charger — et non sur la marge unitaire. `cargo`
    est la soute supposée, 96 SCU par défaut, l'ordre de grandeur d'un cargo
    moyen. Sans ce plafond, une route à 250 000 SCU de stock écraserait tout.
    """
    lignes = [dict(r) for r in con.execute(
        """
        SELECT a.commodity,
               a.terminal      AS from_terminal,
               a.star_system   AS from_system,
               a.price_buy     AS buy_price,
               a.scu_available AS stock,
               b.terminal      AS to_terminal,
               b.star_system   AS to_system,
               b.price_sell    AS sell_price,
               b.scu_demand    AS demand,
               b.price_sell - a.price_buy AS margin,
               (b.price_sell - a.price_buy)
                 * MIN(a.scu_available, b.scu_demand, ?) AS realisable
        FROM uex_commodity_prices a
        JOIN uex_commodity_prices b ON b.commodity = a.commodity
        WHERE a.price_buy > 0 AND b.price_sell > 0
          AND a.scu_available > 0
          AND b.scu_demand > 0
          AND b.price_sell > a.price_buy
          AND a.terminal <> b.terminal
          AND (? IS NULL OR a.star_system = ?)
        -- Classer sur le gain **réalisable**, pas sur la marge unitaire. Une
        -- marge de 586 500 aUEC sur 4 SCU de peaux d'Osoian arrivait en tête
        -- et ne vaut pas le déplacement ; le stock disponible fait la
        -- différence entre une curiosité et une route.
        ORDER BY realisable DESC
        LIMIT ?
        """,
        (cargo if cargo else 96, system, system, limit * 4),
    )]
    if not lignes:
        raise NotFound(query)

    # **Le budget borne la charge, et il se paie à l'achat.** « Avec 500k
    # depuis Lorville » ne peut charger que ce que 500 000 aUEC achètent :
    # ignorer la contrainte classait des routes que le joueur ne peut pas
    # payer — la même famille que « une contrainte perdue en silence ».
    # Recalculé en Python plutôt qu'en SQL : la formule se lit, et le
    # re-classement porte sur le gain réellement réalisable.
    # **Sans vaisseau nommé, la soute est supposée — et le rendu le dira.**
    # 96 SCU, l'ordre de grandeur d'un cargo moyen : sans ce plafond, la
    # première réponse annonçait « 250 000 SCU de Waste chargés », exacte sur
    # le stock et absurde sur le chargement.
    if not cargo:
        cargo = 96
    for l in lignes:
        charge = min(l["stock"], l["demand"], cargo)
        if budget:
            charge = min(charge, int(budget // l["buy_price"]))
        l["charge"] = charge
        l["investissement"] = charge * l["buy_price"]
        l["realisable"] = charge * l["margin"]
        capacites = {"stock vendeur": l["stock"],
                     "demande acheteur": l["demand"], "soute": cargo}
        if budget:
            capacites["budget"] = int(budget // l["buy_price"])
        l["limites"] = [nom for nom, maximum in capacites.items()
                        if maximum == charge]
    lignes = [l for l in lignes if l["charge"] > 0]
    lignes.sort(key=lambda l: -l["realisable"])
    if not lignes:
        raise NotFound(query)

    # **Le danger se dit, il ne filtre pas.** Une route par Pyro peut être la
    # plus rentable — c'est au joueur de décider si elle vaut le risque, pas
    # à nous de la lui cacher. Règle mesurée : `systemes_risques` lit
    # l'affiliation publiée par le wiki, avec repli sur Pyro.
    risques = {s.lower() for s in systemes_risques(con)}
    for l in lignes:
        l["sans_loi"] = bool(
            {(l.get("from_system") or "").lower(),
             (l.get("to_system") or "").lower()} & risques)

    # Une commodité n'apparaît qu'une fois : sinon les cinq meilleures routes
    # sont cinq variantes du même trajet, ce qui n'aide pas à choisir.
    vues: set[str] = set()
    routes = [l for l in lignes
              if not (l["commodity"] in vues or vues.add(l["commodity"]))][:limit]

    horodatage = _row(con, "SELECT MAX(fetched_at) AS t FROM uex_commodity_prices")
    return {"routes": routes, "system": system, "cargo": cargo,
            "budget": budget, "ship": ship,
            "fetched_at": horodatage["t"] if horodatage else None,
            "source": "uex", "resolution": None}


def ou_acheter_pres(con: sqlite3.Connection, query: str, *,
                    depuis: str | None = None,
                    limit: int = 3,
                    portee: str = "achat") -> dict[str, Any]:
    """« Où acheter un Coda le plus proche de Lorville ? »

    Le prix seul ne dit pas où aller : vingt-huit points de vente, c'est vingt-
    huit trajets. Ce qui décide, c'est la distance depuis là où on est.

    Les terminaux d'un autre système sont classés après, quel que soit le
    chiffre : les coordonnées sont relatives à l'étoile de chaque système, donc
    les comparer d'un système à l'autre n'a pas de sens — il faut d'abord
    franchir un point de saut.

    `portee="location"` répond « où **louer** un Prospector proche de
    Crusader » — remarque de l'utilisateur : la question était limpide et
    tombait sur les livrées. Les loueurs sont dans les mêmes relevés UEX,
    seule la liste d'offres change.
    """
    prix = get_price(con, query, portee="achat")
    source = (prix.get("rentals") if portee == "location"
              else prix.get("offers"))
    offres = [o for o in source or [] if o.get("price_buy")]
    if not offres:
        return {**prix, "depuis": None, "proches": [], "portee_pres": portee}

    origine = None
    if depuis:
        res = resolve(con, depuis, entity_types=("starmap",))
        if res.best is not None:
            origine = _dict(_row(con, "SELECT * FROM starmap WHERE uuid = ?",
                                 res.best.entity_id))

    proches = []
    for offre in offres:
        lieu = lieu_du_terminal(con, offre.get("terminal"),
                                offre.get("star_system"))
        distance = _ecart(origine, lieu) if origine and lieu else None
        meme_systeme = bool(
            origine and lieu
            and (origine.get("system_name") or "") == (lieu.get("system_name") or ""))
        proches.append({
            "terminal": offre.get("terminal"),
            "prix": offre.get("price_buy"),
            "systeme": (lieu or {}).get("system_name") or offre.get("star_system"),
            "lieu": (lieu or {}).get("name"),
            "metres": distance if meme_systeme else None,
            "meme_systeme": meme_systeme,
        })

    # Le même système d'abord, puis la distance, puis le prix. Une distance
    # inconnue passe en dernier plutôt que de se faire prendre pour zéro.
    proches.sort(key=lambda p: (
        not p["meme_systeme"],
        p["metres"] if p["metres"] is not None else float("inf"),
        p["prix"] or float("inf"),
    ))
    return {**prix, "depuis": origine, "proches": proches[:limit],
            "total_points": len(offres), "portee_pres": portee}


# **Le rapprochement se fait par le nom, pas par l'UUID.** Mesuré sur la base :
# les `ref_uuid` des ingrédients ne recoupent **aucun** `ref_uuid` d'`uex_prices`
# — 0 sur 4 274 — alors que le nom en rapproche **4 273 sur 4 274** (3 976
# ressources sur 3 976, 297 objets sur 298). Les deux sources numérotent le même
# minerai différemment ; seul le libellé leur est commun.
_PRIX_INGREDIENT = (
    "SELECT MIN(price_buy) FROM uex_prices "
    "WHERE name = ? COLLATE NOCASE AND price_buy > 0")


def _cout_ingredients(con: sqlite3.Connection,
                      blueprint_uuid: str) -> tuple[list[dict[str, Any]], float, list[str]]:
    """Le coût d'achat de chaque ingrédient d'une recette.

    Rend la liste détaillée, le total de ce qu'on sait chiffrer, et le nom de
    ce qu'on ne sait pas. Les trois sont nécessaires : un total amputé de la
    moitié des lignes, présenté seul, se lit comme un prix complet.
    """
    lignes: list[dict[str, Any]] = []
    total = 0.0
    manquants: list[str] = []
    for ing in con.execute(
        "SELECT bi.ref_name, bi.ingredient_kind, bi.quantity_scu, "
        "       bi.quantity_units, bi.group_name "
        "FROM blueprint_tiers bt JOIN blueprint_ingredients bi ON bi.tier_id = bt.id "
        "WHERE bt.blueprint_uuid = ? ORDER BY bt.tier_index, bi.position",
        (blueprint_uuid,),
    ):
        # Les ressources se comptent en SCU (flottant), les objets en unités
        # (entier) — la contrainte de schéma garantit qu'un seul des deux est
        # renseigné. Confondre les deux ferait passer « Hadanite x7 » pour 7 SCU.
        quantite = (ing["quantity_scu"] if ing["ingredient_kind"] == "resource"
                    else ing["quantity_units"])
        unitaire = con.execute(_PRIX_INGREDIENT, (ing["ref_name"],)).fetchone()[0]

        ligne = {"nom": ing["ref_name"], "kind": ing["ingredient_kind"],
                 "groupe": ing["group_name"], "quantite": quantite,
                 "unitaire": unitaire, "cout": None}
        if unitaire is not None and quantite is not None:
            ligne["cout"] = quantite * unitaire
            total += ligne["cout"]
        else:
            manquants.append(ing["ref_name"])
        lignes.append(ligne)
    return lignes, total, manquants


def acheter_ou_fabriquer(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Vaut-il mieux acheter ou fabriquer un Omnisky IX ? »

    Les deux moitiés de la réponse existaient déjà — `get_blueprint` donne la
    recette, `get_price` donne le tarif — et personne ne les additionnait. Le
    joueur, lui, pose la question dans ce sens-là.

    **Un prix de 0 n'est pas un prix.** Mesuré : 119 des 204 commodités cotées
    sortent à `price_buy = 0`, et 18 minerais d'ingrédient sont dans ce cas —
    Quantainium, Riccite, Hadanite, Aphorite, les rares et les légendaires. Ils
    ne se vendent à aucun terminal : il faut les miner. Les compter pour zéro
    ferait passer la recette la plus chère du jeu pour la moins chère, et
    c'est exactement la ligne que le joueur veut voir. On chiffre donc ce
    qu'on sait, on **dit** ce qui manque, et le total devient un plancher.

    2 118 des 4 274 lignes d'ingrédient sont concernées : le cas incomplet est
    majoritaire, pas marginal.
    """
    res = resolve(con, query, entity_types=("blueprint", "item", "ship"))
    if not res.best:
        raise NotFound(query, res)

    # L'entité reconnue est soit la recette, soit ce qu'elle produit.
    bp = _dict(_row(con, "SELECT * FROM blueprints WHERE uuid = ?",
                    res.best.entity_id))
    if bp is None:
        bp = _dict(_row(con, "SELECT * FROM blueprints WHERE output_uuid = ?",
                        res.best.entity_id))
    if bp is None:
        # Pas de recette : la question a une réponse, et c'est « ça ne se
        # fabrique pas ». Aucun vaisseau n'a de recette, par exemple.
        qualite = qualite_reponse(
            faits={"objet": res.best.name, "recette_presente": False},
            sources=("jeu",), fraicheur={"jeu": fraicheur_jeu(con)})
        return {"resolution": res, "nom": res.best.name, "blueprint": None,
                "ingredients": [], "cout_fabrication": None, "manquants": [],
                "prix_achat": None, "ecart": None,
                "verdict": "pas_de_recette", "complet": True,
                "qualite_reponse": qualite}

    lignes, total, manquants = _cout_ingredients(con, bp["uuid"])

    # Le prix d'achat de la sortie, au meilleur terminal. `> 0` pour la même
    # raison que côté ingrédients : un zéro veut dire « pas en vente ».
    achat = con.execute(
        "SELECT MIN(price_buy) FROM uex_prices "
        "WHERE name = ? COLLATE NOCASE AND price_buy > 0",
        (bp["output_name"],)).fetchone()[0]

    # Le verdict n'est prononcé que quand les deux chiffres sont entiers. Un
    # total partiel peut déjà dépasser le prix d'achat — là, « fabriquer coûte
    # plus cher » est vrai malgré les trous, puisque le plancher suffit à
    # conclure. C'est le seul cas où l'on tranche sur une somme incomplète.
    if achat is None:
        verdict = "pas_en_vente"
    elif not manquants:
        verdict = "fabriquer" if total < achat else "acheter"
    elif total >= achat:
        verdict = "acheter"
    else:
        verdict = "indecis"

    fraicheur = {
        "jeu": fraicheur_jeu(con),
        "uex": fraicheur_uex(con, "uex_prices"),
    }
    qualite = qualite_reponse(
        faits={
            "objet": bp["output_name"],
            "ingredients": len(lignes),
            "ingredients_chiffres": len(lignes) - len(manquants),
            "cout_fabrication_connu": total if lignes else None,
            "prix_achat": achat,
            "verdict": verdict,
        },
        manques=(f"prix d'achat positif absent pour {nom}"
                 for nom in manquants),
        sources=("jeu", "uex"), fraicheur=fraicheur)
    return {
        "resolution": res,
        "nom": bp["output_name"],
        "blueprint": bp,
        "ingredients": lignes,
        "cout_fabrication": total if lignes else None,
        "complet": qualite["complet"],
        "manquants": manquants,
        "prix_achat": achat,
        "ecart": (achat - total) if achat is not None and not manquants else None,
        "verdict": verdict,
        "qualite_reponse": qualite,
    }


def ou_consomme(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """Quelles installations consomment une marchandise.

    Ouvert après l'audit, et **pas comme il le proposait**. Il recommandait
    `ConsumesTags` pour répondre « où écouler une cargaison » — l'inférence
    était raisonnable, la donnée ne la porte pas. Mesuré : sur 10 531 lignes de
    consommation, **zéro** se rattache au starmap par UUID, et les libellés
    sont des **salles** — « Lobby », « Security Compound », « Shipping Area »,
    quand ce n'est pas un nom de classe brut. Répondre « vends ton Laranite au
    Lobby » aurait été pire que se taire. C'est la règle « une salle n'est pas
    un lieu de la carte », rencontrée une deuxième fois.

    Ce qui reste vrai et utilisable : **144 des 471 noms** sont de vraies
    installations du starmap — Dupree Industrial, Cry-Astro Processing, les
    complexes Greycat. Elles disent où part une matière première, pas où on la
    vend : pour vendre, c'est UEX qui a les prix et les terminaux. On rend donc
    la question à laquelle la donnée répond, et on **dit** laquelle.
    """
    trouve = resolve(con, query, entity_types=("commodity", "resource"), limit=6)
    if not trouve.candidates:
        raise NotFound(query, trouve)

    for candidat in trouve.candidates:
        lignes = [dict(r) for r in con.execute(
            """SELECT DISTINCT f.location_name AS nom, s.system_name AS systeme,
                      s.path AS chemin, f.refuse
                 FROM trade_flows f
                 JOIN starmap s ON s.name = f.location_name
                WHERE f.sens = 'consomme'
                  AND (f.commodity_uuid = ? OR f.tag_name = ?)
                ORDER BY f.refuse, s.system_name, f.location_name""",
            (candidat.entity_id, candidat.name))]
        if lignes:
            break
    else:
        raise NotFound(f"aucune installation ne consomme « {query} »", trouve)

    acceptent = [l for l in lignes if not l["refuse"]]
    refusent = [l for l in lignes if l["refuse"]]
    par_systeme: dict[str, list[str]] = {}
    for l in acceptent:
        par_systeme.setdefault(l["systeme"] or "système inconnu", []).append(l["nom"])
    return {
        "commodite": candidat.name,
        "par_systeme": par_systeme,
        "total": len(acceptent),
        "refusent": [l["nom"] for l in refusent][:5],
    }


def comment_gagner(con: sqlite3.Connection, query: str) -> dict[str, Any]:
    """« Comment gagner de l'argent ? » — composer au lieu de refuser.

    La question la plus posée par les joueurs — une industrie de guides
    entière s'est formée dessus — et elle rendait `None` : ce n'est pas une
    question de base de données, ses réponses sont des boucles de jeu
    chronométrées qu'aucun fichier ne porte. Mais ses trois réponses
    **honnêtes** sont en base : ce que payent les missions, ce que rapportent
    les routes, ce que valent les minerais. On les donne toutes les trois,
    et le rendu dit ce qu'on ne sait pas — les rythmes de farm des guides.

    Chaque volet est optionnel : un `NotFound` sur l'un n'éteint pas les
    autres. Une composition qui exige ses trois morceaux retombe à zéro dès
    que UEX manque, ce qui est la moitié des installations.
    """
    volets: dict[str, Any] = {}
    try:
        volets["missions"] = missions_payantes(con, query, limit=3)
    except NotFound:
        pass
    try:
        volets["routes"] = get_trade_route(con, query, limit=2)
    except NotFound:
        pass
    # Les minerais qui se vendent le mieux — le relevé UEX, jamais un baréme
    # inventé. `price_sell` et non `price_buy` : c'est ce qu'un terminal paye.
    try:
        volets["minerais"] = [dict(r) for r in con.execute(
            """SELECT commodity, MAX(price_sell) AS prix, terminal
                 FROM uex_commodity_prices
                WHERE price_sell > 0
                  AND commodity IN (SELECT name FROM commodities
                                    WHERE refined_name IS NOT NULL
                                       OR name IN (SELECT name FROM resources))
                GROUP BY commodity ORDER BY prix DESC LIMIT 4""")]
    except sqlite3.Error:
        pass
    if not volets:
        raise NotFound(query)
    return {"volets": volets, "resolution": None}
