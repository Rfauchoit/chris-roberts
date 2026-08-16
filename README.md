# Chris Roberts

Un assistant Star Citizen en français, qui tourne **sur ta machine** et
répond à partir des fichiers du jeu — pas d'un site, pas d'une mémoire
approximative.

```
— jusqu'à quelle qualité ça vaut le coup pour un P6-LR ?

P6-LR Sniper Rifle — la qualité à viser dépend de ce que tu veux faire :
- 771 — à nu, pour tuer d'une balle la tête en armure lourde
- 956 — avec un Torrent Compensator2, pour tuer d'une balle le torse léger
- 995 — avec un silencieux Tacit, pour continuer à OS la tête en lourde
```

## Ce qu'il sait faire

Soixante et onze outils, qui répondent en **millisecondes** et sans rien
consommer : prix et points de vente, recettes de fabrication et missions
qui donnent les blueprints, trajets quantiques avec escales, duels entre
vaisseaux, minage et raffinage, qualité de fabrication, emports d'armure.

Quand la question sort de ce que ces outils savent formuler, il peut la passer
à **ton** abonnement Claude ou ChatGPT — uniquement après que tu as choisi le
CLI installé. Celui-ci n'a accès qu'à la porte de données en lecture seule.

## Installer

1. Télécharge la dernière version dans **Releases**.
2. Lance-la. Elle vérifie puis récupère la base de jeu (~79 Mo) si son build
   ou son schéma a changé.
3. Dans l'onglet **Mon abonnement**, installe Claude ou Codex, connecte-toi
   une fois, puis sélectionne-le. C'est ton compte qui paie ; aucune clé ne
   transite ni ne se stocke.

À partir de la 0.2.0, les mises à jour sont téléchargées au lancement,
contrôlées par SHA-256, puis remplacent l'exécutable précédent avant de se
relancer. Le passage d'une version antérieure à la 0.2.0 reste manuel.

## Ce qui remonte, et ce qui ne remonte pas

Chris s'améliore avec les questions qu'on lui pose. Les tiennes sont
envoyées à l'auteur — **la question, la réponse reçue, et l'outil qui a
servi** — pour corriger ce qui répond mal.

Ce qui identifierait ta machine ou ton compte est **retiré avant
l'envoi** : chemins de fichiers, adresses, courriels, pseudos. Ce n'est
pas une promesse, c'est un filtre dans le code, avec ses propres tests.

L'annonce et un aperçu pseudonymisé apparaissent avant la première question.
Tu peux couper ce partage dans **Réglages**, à tout moment.

## D'où viennent les chiffres

Des fichiers du jeu, via [scunpacked](https://github.com/StarCitizenWiki/scunpacked),
complétés par l'API publique du Star Citizen Wiki, les cotations
communautaires d'UEX, et la traduction française du Cirque Lisoir.

Aucune valeur n'est estimée, complétée ni arrondie. Quand la donnée
manque, Chris le dit — c'est la règle qui tient tout le projet.

## Ce qu'il ne fait pas

Il ne connaît pas les prix des boutiques du jeu : CIG les a retirés des
fichiers depuis la 3.20, et ils viennent désormais des relevés UEX. Il ne
sait pas quel vaisseau convient à une mission donnée — aucune donnée ne
relie les deux, et deviner serait pire que se taire.

---

*Projet personnel, sans lien avec Cloud Imperium Games.*
