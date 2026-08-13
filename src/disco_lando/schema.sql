-- Schéma Disco Lando — Phase 1
-- Voir docs/SCHEMA.md pour la justification de chaque choix.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- ingestion

CREATE TABLE ingest_runs (
  id             INTEGER PRIMARY KEY,
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  source_commit  TEXT NOT NULL,
  commit_subject TEXT NOT NULL,   -- "4.9.0-LIVE.12232306"
  game_version   TEXT,            -- "4.9.0"
  build_id       TEXT,            -- "12232306"  <- la garde de réingestion
  status         TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
  error          TEXT
);

-- ---------------------------------------------------------------- résolution

CREATE TABLE aliases (
  id          INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id   TEXT NOT NULL,
  alias       TEXT NOT NULL,
  alias_norm  TEXT NOT NULL,
  -- Même forme, espaces retirés. On tape les sigles collés : « P6-LR » sort
  -- volontiers en « p6lr », qui ne partage aucun token avec « p6 lr sniper
  -- rifle » et n'a donc aucune chance en FTS.
  alias_flat  TEXT NOT NULL,
  lang        TEXT NOT NULL DEFAULT 'en',
  source      TEXT NOT NULL
              -- Vocabulaire fermé, comme les types de port : une source
              -- inconnue doit lever, pas s'insérer discrètement. « circuspes »
              -- est la traduction française — ses 4 883 noms se sont fait
              -- rejeter en silence tant qu'il manquait ici, parce que
              -- `INSERT OR IGNORE` avale aussi les violations de CHECK.
              CHECK (source IN ('canonical', 'labels', 'derived', 'guild',
                                'api_wiki', 'circuspes')),
  weight      REAL NOT NULL DEFAULT 1.0,
  UNIQUE (entity_type, entity_id, alias_norm, source)
);

CREATE INDEX ix_aliases_norm ON aliases (alias_norm);
CREATE INDEX ix_aliases_flat ON aliases (alias_flat);
CREATE INDEX ix_aliases_ent  ON aliases (entity_type, entity_id);

CREATE VIRTUAL TABLE aliases_fts USING fts5 (
  alias_norm,
  content = 'aliases',
  content_rowid = 'id',
  tokenize = "unicode61 remove_diacritics 2"
);

-- Découpage en mots. Sans lui, « quantanium » ne rejoint jamais
-- « MineableRock_AsteroidLegendary_Quantainium », dont il n'est qu'un mot sur
-- cinq — et écrit à une lettre près : le jeu s'écrit « Quantanium » dans une
-- entrée morte et « Quantainium » dans les vrais gisements. Le résolveur
-- élargit par préfixe sur cette table, puis tranche sur la ressemblance des
-- lettres.
--
-- La colonne `phonetic` a été retirée le 2026-08-04 avec l'étage du même nom :
-- elle servait à rattraper Whisper, et le bot est écrit.
CREATE TABLE alias_tokens (
  alias_id INTEGER NOT NULL REFERENCES aliases(id) ON DELETE CASCADE,
  token    TEXT NOT NULL
);

CREATE INDEX ix_atok_tok  ON alias_tokens (token);

-- ---------------------------------------------------------------- vaisseaux

CREATE TABLE ships (
  uuid              TEXT PRIMARY KEY,
  class_name        TEXT NOT NULL UNIQUE,
  name              TEXT NOT NULL,
  manufacturer_code TEXT,
  manufacturer_name TEXT,
  career            TEXT,
  role              TEXT,
  size              INTEGER,
  crew              INTEGER,
  length            REAL,
  width             REAL,
  height            REAL,
  mass              REAL,
  cargo_scu         REAL,
  health            REAL,
  shield_hp         REAL,
  pilot_dps         REAL,
  pilot_alpha       REAL,
  qt_speed          REAL,
  qt_range          REAL,
  -- Champs ignorés au premier passage, alors qu'ils répondent aux questions
  -- les plus banales : « il va à combien le Gladius ? », « ça coûte combien
  -- de le réclamer ? ».
  scm_speed         REAL,
  max_speed         REAL,
  boost_speed       REAL,
  pitch             REAL,
  yaw               REAL,
  roll              REAL,
  -- Agilité v2 : la vitesse plafond ne dit pas à quelle vitesse le navire
  -- change de vecteur. Valeurs brutes de FlightCharacteristics.
  boost_backward    REAL,
  pitch_boosted     REAL,
  yaw_boosted       REAL,
  roll_boosted      REAL,
  accel_main        REAL,
  accel_retro       REAL,
  accel_maneuver    REAL,
  accel_main_boosted REAL,
  accel_retro_boosted REAL,
  accel_maneuver_boosted REAL,
  zero_to_scm       REAL,
  scm_to_zero       REAL,
  boost_capacity    REAL,
  boost_regen       REAL,
  boost_regen_time  REAL,
  fuel_capacity     REAL,
  -- **La moitié manquante de l'autonomie en vol.** `fuel_capacity` était
  -- ingéré depuis le début, la consommation jamais : la symétrie avec le
  -- quantique est parfaite, où `qt_fuel_rate` était exactement la moitié
  -- absente du même calcul et où sa mesure a débloqué tout le planificateur
  -- de trajet. Renseigné sur les 316 vaisseaux (40 à zéro), 143 valeurs
  -- distinctes : 276 vaisseaux deviennent calculables, de 44 s sur l'Esperia
  -- Glaive à 2 632 s sur l'Anvil Terrapin Medic.
  fuel_usage        REAL,
  -- Non nul sur 164 vaisseaux : ceux qui refont leur plein en vol, et pour
  -- lesquels l'autonomie ne veut donc pas dire la même chose.
  fuel_intake       REAL,
  -- **L'autonomie en vol, en secondes.** Colonne **générée** plutôt
  -- qu'ingérée : elle n'est pas publiée, c'est la division de deux colonnes
  -- qui le sont. La calculer à la volée la laisserait hors de portée de
  -- `compare_ships`, dont les statistiques sont des noms de colonne ; la
  -- stocker en dur la ferait diverger au prochain patch. 276 vaisseaux sur
  -- 316 la portent, de 44 s sur l'Esperia Glaive à 2 632 s sur l'Anvil
  -- Terrapin Medic.
  autonomie_vol     REAL GENERATED ALWAYS AS (
                      CASE WHEN fuel_usage > 0
                           THEN fuel_capacity / fuel_usage END) VIRTUAL,
  quantum_fuel      REAL,
  ore_capacity      REAL,
  insurance_cost    REAL,
  insurance_minutes REAL,
  is_spaceship      INTEGER NOT NULL DEFAULT 0,
  is_vehicle        INTEGER NOT NULL DEFAULT 0,
  is_gravlev        INTEGER NOT NULL DEFAULT 0,
  description       TEXT
);

-- **Le duel — « un Scorpius peut-il détruire un Hammerhead ? ».** Depuis le
-- système d'armure de 2026 (4.7), un vaisseau porte une armure à **seuil de
-- déflexion** : un projectile dont l'alpha est sous le seuil ricoche, sans
-- rien user — c'est ce qui rend un Hammerhead intouchable aux armes légères
-- (déflexion 531 physique / 380 énergie contre 43,7 d'alpha pour un CF-337).
-- Mesuré : 307 vaisseaux sur 316 ont leur bloc Armor, 305 une déflexion,
-- 290 un bouclier total. Les constantes communes aux 73 générateurs de
-- bouclier (absorption 45 % du physique, interruption de régén dès 0,5 % des
-- PV par impact) vivent dans combat.py — identiques partout, mesurées.
CREATE TABLE ship_combat (
  ship_uuid       TEXT PRIMARY KEY REFERENCES ships(uuid) ON DELETE CASCADE,
  hull_health     REAL,     -- Health à la racine du fichier
  armor_health    REAL,     -- Armor.Health : l'armure s'use, puis tombe
  defl_physical   REAL,     -- Armor.Deflection : sous ce seuil, ricochet
  defl_energy     REAL,
  mult_physical   REAL,     -- Armor.DamageMultipliers : la part qui passe
  mult_energy     REAL,
  shield_hp       REAL,     -- ShieldsTotal.Hp — somme des générateurs montés
  shield_regen    REAL,     -- ShieldsTotal.Regen, par seconde
  missiles_count  INTEGER,  -- Weaponry.Missiles : la théorie des missiles
  missile_damage  REAL      -- dégâts d'un missile (Damage.Total)
);

-- L'armement stock, arme par arme — pilote et tourelles. Depuis le build
-- 12232306, les trois listes de tourelles publient elles aussi leurs armes :
-- 402 en tourelles habitées, 211 télécommandées et 110 PDC. Le poste reste
-- indispensable : une PDC n'entre pas dans le feu offensif du duel, et « en
-- tourelles » ne doit pas remplacer les canons fixes du pilote.
CREATE TABLE ship_armes (
  ship_uuid   TEXT NOT NULL REFERENCES ships(uuid) ON DELETE CASCADE,
  weapon_uuid TEXT,
  name        TEXT,
  n           INTEGER NOT NULL DEFAULT 1,   -- même arme sur plusieurs affûts
  poste       TEXT NOT NULL DEFAULT 'pilote'
              CHECK (poste IN ('pilote', 'habitee', 'telecommandee', 'pdc'))
);

CREATE INDEX ix_ship_armes ON ship_armes (ship_uuid);

-- **Les grilles de soute — « est-ce que ma caisse rentre ? ».** Un outil
-- communautaire entier s'appelle « Cargo — Will It Fit » : la capacité en SCU
-- ne suffit pas, c'est la **taille de caisse** que la grille accepte qui
-- décide. Le jeu la publie en mètres (`MaxSize`), sur 149 vaisseaux ; les
-- caisses standard, elles, n'ont pas de géométrie dans les fichiers — on rend
-- donc les mètres, on ne devine pas la caisse.
CREATE TABLE cargo_grids (
  id         INTEGER PRIMARY KEY,
  ship_uuid  TEXT NOT NULL REFERENCES ships(uuid) ON DELETE CASCADE,
  scu        REAL,
  max_x      REAL,       -- la plus grande caisse acceptée, en mètres
  max_y      REAL,
  max_z      REAL,
  -- Le hors-tout de la grille, en mètres — publié sur les 528 grilles des
  -- 149 vaisseaux (mesuré le 2026-08-07). C'est ce qui répond à « combien
  -- de Cyclone dans un C2 » : X·Y au sol, Z en hauteur (vérifié sur le C2,
  -- 10 × 18,75 × 5 m = 937 m³ = 480 SCU, le compte tombe juste).
  dim_x      REAL,
  dim_y      REAL,
  dim_z      REAL,
  -- La cellule de pose (MinSize), par axe : 504 grilles sur 528 sont en
  -- 1,25 m³, mais 16 posent en 2,5 × 10 × 2,5 — une constante mentirait.
  min_x      REAL,
  min_y      REAL,
  min_z      REAL,
  ouverte    INTEGER NOT NULL DEFAULT 0,   -- plateau ouvert (IsOpenContainer)
  externe    INTEGER NOT NULL DEFAULT 0    -- grille extérieure
);

CREATE INDEX ix_grids_ship ON cargo_grids (ship_uuid);

-- Un port peut être vide : sa capacité se lit alors dans CompatibleTypes et non
-- dans le Type de l'objet monté. Mesuré : 21 175 nœuds sur 57 759 sont des ports
-- vides, dont 20 450 déclarent leurs CompatibleTypes. Un point d'emport d'arme
-- libre doit apparaître dans la réponse — d'où deux jeux de colonnes distincts.
CREATE TABLE hardpoints (
  id               INTEGER PRIMARY KEY,
  ship_uuid        TEXT NOT NULL REFERENCES ships(uuid) ON DELETE CASCADE,
  port_id          TEXT NOT NULL,
  parent_port_id   TEXT,
  root_port_id     TEXT,
  depth            INTEGER NOT NULL,
  path             TEXT NOT NULL,
  hardpoint_name   TEXT,
  accepted_types   TEXT,          -- JSON : contrat du port (CompatibleTypes)
  min_size         INTEGER,
  max_size         INTEGER,
  editable         INTEGER,
  installed_uuid   TEXT,
  installed_class  TEXT,
  installed_name   TEXT,
  installed_type   TEXT,          -- nature de l'objet monté
  installed_subtype TEXT,
  installed_grade  INTEGER,
  -- Classification publiée dans MannedTurrets / RemoteTurrets / PdcTurrets.
  -- Elle qualifie le nœud de tourelle seulement ; ses descendants restent
  -- reliés par parent_port_id, sans recopier une valeur dérivée sur chacun.
  turret_kind      TEXT CHECK (turret_kind IN ('habitee', 'telecommandee', 'pdc')),
  turret_type      TEXT,          -- valeur amont : TurretBase.MannedTurret, etc.
  category         TEXT NOT NULL,
  UNIQUE (ship_uuid, port_id)
);

CREATE INDEX ix_hp_ship_cat ON hardpoints (ship_uuid, category);

-- Composants extérieurs que l'armure de coque ne rend pas invulnérables.
-- Le hardpoint dit **quoi est monté et où** ; le fichier `items/<class>.json`
-- dit combien de PV et quel seuil possède cette pièce. Les relier ferme le
-- « on peut désarmer » qualitatif du duel : une tourelle de Hammerhead a
-- 15 000 PV et des seuils physique/énergie à 0 dans le build 12232306.
CREATE TABLE ship_composants_exposes (
  ship_uuid       TEXT NOT NULL REFERENCES ships(uuid) ON DELETE CASCADE,
  port_id         TEXT NOT NULL,
  genre           TEXT NOT NULL CHECK (genre IN ('tourelle', 'propulseur')),
  poste           TEXT,          -- habitee | telecommandee | pdc pour tourelle
  nom             TEXT,
  class_name      TEXT,
  pv              REAL,
  mult_physical   REAL,
  mult_energy     REAL,
  seuil_physical  REAL,
  seuil_energy    REAL,
  PRIMARY KEY (ship_uuid, port_id)
);

CREATE INDEX ix_exposes_ship_genre
ON ship_composants_exposes (ship_uuid, genre);

-- ---------------------------------------------------------------- objets

CREATE TABLE items (
  uuid              TEXT PRIMARY KEY,
  class_name        TEXT NOT NULL,
  name              TEXT,
  type              TEXT,
  subtype           TEXT,
  size              INTEGER,
  grade             TEXT,
  manufacturer_name TEXT,
  classification    TEXT,             -- « Ship.Weapon.Gun », hiérarchie amont
  tags              TEXT,             -- « GATS BallisticGatling flightReady »
  -- 5 230 objets sur 10 804, cinq paliers : Common 3 818, Uncommon 829,
  -- Rare 506, Epic 75, **Legendary 2**. Aucune colonne ne le portait, et
  -- c'est un mot que le joueur emploie — « une armure légendaire » ne
  -- résolvait rien.
  rarity            TEXT,
  -- **Le grade que le jeu affiche est une lettre, pas notre nombre.**
  -- `items.grade` est l'indice interne, et il vaut 1 sur 4 941 objets qui
  -- n'ont aucun grade réel : le rendre tel quel annonçait « grade 3 » sans
  -- que personne puisse dire ce que ça veut dire — remarque de
  -- l'utilisateur le 2026-08-10. `stdItem.DescriptionData.Grade` porte la
  -- lettre, A à D plus « Bespoke », sur 336 composants ; la correspondance
  -- avec le nombre est exacte et mesurée (1→A, 2→B, 3→C, 4→D).
  grade_lettre      TEXT,
  -- Militaire, civil, industriel, furtif, compétition — 333 composants.
  -- C'est ce qu'un joueur veut dire par « un bouclier militaire », et rien
  -- ne le portait : j'ai failli conclure que la donnée n'existait pas,
  -- c'est la conclusion qui coûte le plus cher.
  item_class        TEXT,
  -- Place occupée en inventaire, en **µSCU entiers**. Renseigné sur les 10 804
  -- objets. On ne stocke pas le champ `SCU` amont : il est arrondi à 0 pour
  -- tout ce qui tient sous le SCU, c'est-à-dire pour presque tout — un
  -- pistolet Coda vaut 2 500 µSCU, soit 0,0025 SCU. La valeur exacte est
  -- `SCUConverted` multiplié par son `Unit` (µSCU sur 10 775 objets, cSCU sur
  -- 25, SCU sur 4). En entier pour que les additions restent justes.
  volume_uscu       INTEGER,
  -- **Combien on range DEDANS**, en µSCU — à ne pas confondre avec
  -- `volume_uscu`, qui est la place que l'objet prend.
  --
  -- Trouvée le 2026-08-13 en auditant scunpacked à la demande de
  -- l'utilisateur (docs/AUDIT_SCUNPACKED_2026-08-13.md) : **2 073 objets**
  -- la publient, et **0 sur 10 804** l'ont dans les index racine. Elle vit
  -- uniquement dans les fichiers individuels de `items/`, deux niveaux
  -- sous le `Raw` du moteur.
  --
  -- Elle varie vraiment : 6 valeurs distinctes sur 135 sacs à dos, de
  -- 0,054 à 0,180 SCU — plus du triple entre le plus petit et le Novikov.
  contenance_uscu   INTEGER,
  -- Seules 241 des 788 armes sont montables par un joueur. Les autres sont des
  -- pièces de capital-ship, d'IA ou de test. Sans ce filtre, « le meilleur
  -- canon balistique » répond le canon du porte-nefs Bengal.
  flight_ready      INTEGER NOT NULL DEFAULT 0,
  mount_usable      INTEGER NOT NULL DEFAULT 0,
  -- Peut apparaître en butin. Vient de `entity_tag_map` — `CanGenerateAsLoot`
  -- ou `LootableFromSuit` — que le premier passage n'ouvrait pas.
  --
  -- **La donnée dit SI, jamais OÙ.** Aucune source du dépôt ne relie un objet
  -- lootable à un lieu : ni les contrats, ni le starmap. Promettre « on en
  -- trouve à tel endroit » serait une invention.
  lootable          INTEGER NOT NULL DEFAULT 0,
  loot_source       TEXT,       -- suit | generic
  is_dev            INTEGER NOT NULL DEFAULT 0,
  description       TEXT
);

CREATE INDEX ix_items_usable ON items (flight_ready, mount_usable, is_dev);

CREATE INDEX ix_items_class ON items (class_name);
CREATE INDEX ix_items_type  ON items (type, subtype);

-- `stdItem` est un objet imbriqué que le premier audit, limité au premier
-- niveau, n'avait jamais ouvert. Il porte les statistiques d'arme — sans
-- lesquelles « quel est le meilleur canon balistique » n'a pas de réponse.
CREATE TABLE item_stats (
  item_uuid         TEXT PRIMARY KEY REFERENCES items(uuid) ON DELETE CASCADE,
  weapon_class      TEXT,       -- ballistic | laser | plasma | tachyon | distortion
  weapon_kind       TEXT,       -- cannon | repeater | gatling | scattergun
  dps               REAL,
  -- **Le DPS soutenu est publié, il ne se calcule pas.** On le déduisait du
  -- capacitor (`alpha × regen / cost`), et c'était faux sur 103 armes sur 114 :
  -- l'Omnisky IX sortait 3,25 là où CIG publie 290,3. La cause est un mélange
  -- d'échelles dans le bloc `Capacitor` — `MaxAmmoLoad` vaut 25 quand
  -- `RequestedAmmoLoad` vaut 20 200, et 80 armes sur 115 ont donc un coût par
  -- tir supérieur à leur réservoir entier. La valeur vraie vit dans les
  -- armes fixes et les trois listes de tourelles des fichiers de vaisseau :
  -- 91 armes en 4.9, dont 14 publiées uniquement en tourelle, aucune
  -- contradiction entre vaisseaux. Le `Dps` du même bloc vaut celui de cette
  -- table à l'arrondi près — l'appariement est donc prouvé.
  --
  -- La leçon dépasse la colonne : le §7 interdit d'inventer un plafond, et
  -- celui-ci sortait bien de deux colonnes. Mais **deux colonnes de la même
  -- source ne sont pas forcément commensurables**. Une formule maison se
  -- confronte à une valeur publiée avant d'être livrée.
  dps_soutenu       REAL,
  alpha             REAL,
  rounds_per_minute REAL,
  effective_range   REAL,
  projectile_speed  REAL,
  ammo_capacity     INTEGER,
  pellets_per_shot  INTEGER,
  ammo_per_shot     REAL,       -- AmmoPerShot du mode retenu
  -- Réserve d'énergie des armes de vaisseau à énergie, dans `Weapon.Capacitor`.
  -- Mesuré sur ship-items.json : 45 armes balistiques ont une capacité de
  -- munitions et **aucun** capacitor, 92 armes à énergie ont les deux. Les
  -- deux questions — « combien de balles » et « combien de capacitor » — ne
  -- s'adressent donc jamais à la même arme, et la réponse doit le dire.
  capacitor_max     REAL,       -- MaxAmmoLoad, taille du réservoir
  capacitor_regen   REAL,       -- MaxRegenPerSec, recharge par seconde
  capacitor_cost    REAL,       -- CostPerBullet, coût d'un tir
  capacitor_cooldown REAL,      -- Cooldown avant reprise de la recharge
  dps_physical      REAL,
  dps_energy        REAL,
  dps_distortion    REAL,
  -- **L'alpha publie son detail par type de degats, et le total ment.**
  -- L'Atzkav sort 165 d'AlphaTotal : 120 d'energie, 35 de distorsion, 10 de
  -- stun. La distorsion et le stun ne retirent aucun point de vie, et
  -- l'armure resiste type par type (`armor_energy` n'est pas
  -- `armor_physical`). Compter le total contre la seule resistance physique
  -- annoncait un sniper qui tue d'une balle ce qu'il ne tue pas.
  -- Remarque de l'utilisateur, 2026-08-10.
  alpha_physical    REAL,
  alpha_energy      REAL,
  alpha_thermal     REAL,
  alpha_biochemical REAL,
  alpha_distortion  REAL,
  alpha_stun        REAL,
  health            REAL,
  item_mass         REAL,
  fire_modes        TEXT,
  -- Composants hors armement. Les colonnes sont dans la même table parce que
  -- la question posée est la même — « lequel est le meilleur » — et qu'un
  -- objet n'appartient jamais qu'à une famille : un bouclier n'a pas de DPS,
  -- un canon n'a pas de portée de saut. Les colonnes vides sont le cas normal.
  --
  -- Ces statistiques existaient depuis le début dans `stdItem.Shield` et
  -- `stdItem.QuantumDrive` ; le premier passage ne lisait que les armes.
  shield_health     REAL,       -- MaxShieldHealth
  shield_regen      REAL,       -- MaxShieldRegen, points par seconde
  shield_downed     REAL,       -- délai avant reprise après effondrement
  qt_jump_range     REAL,       -- JumpRange, mètres
  qt_drive_speed    REAL,       -- StandardJump.DriveSpeed
  qt_cooldown       REAL,       -- StandardJump.CooldownTime
  qt_fuel_rate      REAL,
  -- Refroidisseurs et générateurs. Un premier passage avait conclu que leur
  -- performance « vit dans `ResourceNetwork`, dont le format n'est pas
  -- comparable d'un type à l'autre » et les avait laissés de côté : mesuré le
  -- 2026-08-06, c'était trop pessimiste. On ne compare **jamais** un
  -- refroidisseur à un générateur ; à l'intérieur d'un type, un seul nombre
  -- sort de `States[Online].Deltas` et il est parfaitement comparable —
  -- 38 de fluide pour un Glacier, 16 de puissance pour un OverDrive.
  --
  -- 81 refroidisseurs et 88 générateurs n'avaient aucune statistique quand les
  -- 73 boucliers et 63 moteurs quantiques avaient les leurs : l'asymétrie
  -- était invisible pour le joueur, qui obtenait un classement pour les uns et
  -- une simple liste pour les autres.
  cooling_rate      REAL,       -- Coolant produit par seconde (GeneratedRate)
  power_rate        REAL,       -- Puissance produite par seconde (Rate)
  -- Signatures, présentes sur les deux familles. Elles ouvrent le sujet
  -- furtivité, que la communauté traite dans des guides dédiés.
  -- **L'armure FPS : 2 416 objets, zéro statistique jusqu'ici.** C'est le
  -- piège des refroidisseurs à trente fois l'échelle — « le meilleur bouclier
  -- taille 2 » se classait, « la meilleure armure pour Pyro » ne rendait rien
  -- et rien ne disait pourquoi, parce qu'aucune erreur ne se produit : la
  -- liste est simplement vide.
  --
  -- Les six axes de résistance demandaient de **descendre d'un niveau** : la
  -- valeur n'est pas un nombre mais un objet `{Multiplier, Threshold}`. Un
  -- audit qui s'arrête au premier niveau conclut « aucune valeur » — la même
  -- erreur que `stdItem.Weapon` au premier passage. Les six `Threshold` valent
  -- 0 sur les 2 271 et ne sont donc pas ingérés.
  --
  -- **Un multiplicateur bas protège mieux** (0,125 arrête sept dégâts sur
  -- huit), comme une signature basse vaut mieux qu'une haute : deuxième
  -- famille de statistiques où le classement s'inverse.
  armor_physical    REAL,       -- multiplicateur, 0,125 à 1,00
  armor_energy      REAL,
  armor_distortion  REAL,
  armor_thermal     REAL,
  armor_biochemical REAL,
  armor_stun        REAL,
  temp_min          REAL,       -- −225 à −5 : la lune glacée
  temp_max          REAL,       -- +35 à +225 : Pyro
  radiation_max     REAL,       -- capacité avant saturation, 0 à 52 800
  radiation_rate    REAL,       -- vitesse d'évacuation
  gforce_resistance REAL,
  -- **Trois familles à zéro statistique, mesurées par l'audit du 2026-08-06.**
  -- 1 266 propulseurs, 363 réservoirs, 68 missiles. Le missile est le cas le
  -- plus net : « quel missile a la plus grande portée » n'avait **aucune**
  -- source, et la donnée est là depuis toujours.
  poussee           REAL,      -- Thruster.ThrustCapacity, en newtons
  conso_par_10kn    REAL,      -- FuelBurnRatePer10KNewton
  capacite_scu      REAL,      -- ResourceContainer.Capacity.Value
  degats_missile    REAL,      -- Missile.DamageTotal
  rayon_explosion   REAL,      -- Missile.ExplosionRadius
  portee_missile    REAL,      -- Missile.Distance
  vitesse_missile   REAL,      -- Missile.GCS.LinearSpeed
  guidage           INTEGER,   -- 0 = non guidé (IsDumbMissile)
  oxygene           REAL,       -- casques : `Helmet.AtmosphereCapacity`
  signature_em      REAL,
  signature_ir      REAL,
  -- **Ce qu'un accessoire fait aux dégâts de l'arme.** 116 accessoires en
  -- publient un dans `stdItem.WeaponModifier.WeaponStats.Base`, et rien ne
  -- le lisait : « et si je mets un silencieux » était donc sans réponse.
  -- Mesuré le 2026-08-10 : neuf valeurs distinctes, de 0,9 à 1,175, et
  -- **tous les silencieux ne se valent pas** — le Tacit coûte 8 %, le
  -- Stoic rien du tout. Un joueur qui dit « le silencieux » suppose le
  -- contraire.
  damage_multiplier REAL,
  -- Le bruit, qui est la raison d'être d'un silencieux. 0,66 sur le Stoic.
  sound_multiplier  REAL
);

CREATE INDEX ix_stats_class ON item_stats (weapon_class, weapon_kind);
CREATE INDEX ix_stats_dps   ON item_stats (dps DESC);

-- --------------------------------------------------------- réseau d'énergie

-- **Le réseau de ressources, une ligne par (objet, état).** Mesuré le
-- 2026-08-12 sur 21 849 fichiers (docs/ANALYSE_ENERGIE.md) : chaque
-- composant déclare des états (Online, Idle) qui consomment et génèrent
-- des ressources par seconde. Le Power circule sous DEUX unités que rien
-- ne relie : les **pips entiers** (`SPowerSegmentResourceUnit`, les barres
-- de l'interface — radar 5, bouclier 0-6) et les **unités standard**
-- fractionnaires (armes : 1,0 à l'énergie, 0,1 au balistique ; quantum
-- drive 2-3). Les additionner serait inventer une conversion que le jeu
-- ne publie pas — deux colonnes, jamais une somme.
--
-- `pips_low/med/high` et leurs multiplicateurs sont les paliers
-- d'allocation publiés par composant (mesuré : 1 pip → ×0,70 de
-- performance, 2 → ×0,85, 3 → ×1,00 sur l'AllStop comme sur le Bracer),
-- avec `min_fraction` comme plancher (⅓ typique ; 1 = tout ou rien, cas
-- du balistique et du quantum drive). C'est le mécanisme du choix : le
-- Gladius stock produit 16 pips et en demande 22.
--
-- Les signatures sont PAR ÉTAT : l'EM vient du générateur (9 900) et du
-- quantum drive en ligne (21 600), l'IR du refroidisseur (7 260). Une
-- signature basse vaut mieux qu'une haute — classement inversé, comme
-- l'armure.
CREATE TABLE item_reseau (
  item_uuid       TEXT NOT NULL REFERENCES items(uuid) ON DELETE CASCADE,
  etat            TEXT NOT NULL,     -- Online | Idle | …
  pips_conso      REAL,              -- Power consommé, en pips entiers
  std_conso       REAL,              -- Power consommé, en unités standard
  pips_generes    REAL,              -- Power produit (générateurs, batteries)
  ressource       TEXT,              -- Shield | Coolant | LifeSupport | …
  generation_std  REAL,              -- production de `ressource`, unités std/s
  min_fraction    REAL,              -- plancher d'alimentation (1 = tout ou rien)
  pips_low        REAL, mult_low   REAL,
  pips_med        REAL, mult_med   REAL,
  pips_high       REAL, mult_high  REAL,
  em              REAL, em_decay   REAL,
  ir              REAL, ir_decay   REAL,
  -- Batteries : ce que le tampon stocke (ResourceContainer.Capacity).
  stockage        REAL,
  PRIMARY KEY (item_uuid, etat)
);

CREATE INDEX ix_reseau_pips ON item_reseau (etat, pips_conso);

-- ---------------------------------------------------------------- blueprints

CREATE TABLE blueprints (
  uuid                 TEXT PRIMARY KEY,
  key                  TEXT NOT NULL UNIQUE,
  kind                 TEXT NOT NULL,
  category_uuid        TEXT,
  output_uuid          TEXT,
  output_class         TEXT,
  output_name          TEXT NOT NULL,
  output_type          TEXT,
  output_subtype       TEXT,
  output_grade         TEXT,
  available_by_default INTEGER NOT NULL DEFAULT 0,
  dismantle_seconds    REAL,
  dismantle_efficiency REAL
);

CREATE INDEX ix_bp_output ON blueprints (output_uuid);

CREATE TABLE blueprint_tiers (
  id                 INTEGER PRIMARY KEY,
  blueprint_uuid     TEXT NOT NULL REFERENCES blueprints(uuid) ON DELETE CASCADE,
  tier_index         INTEGER NOT NULL,
  craft_time_seconds INTEGER,
  UNIQUE (blueprint_uuid, tier_index)
);

-- Les ressources sont en SCU (flottant), les objets en unités (entier).
-- Confondre les deux ferait passer « Hadanite x7 » pour 7 SCU.
CREATE TABLE blueprint_ingredients (
  id              INTEGER PRIMARY KEY,
  tier_id         INTEGER NOT NULL REFERENCES blueprint_tiers(id) ON DELETE CASCADE,
  position        INTEGER NOT NULL,
  group_key       TEXT,
  group_name      TEXT,
  required_count  INTEGER,
  ingredient_kind TEXT NOT NULL CHECK (ingredient_kind IN ('resource', 'item')),
  ref_uuid        TEXT,
  ref_name        TEXT NOT NULL,
  quantity_scu    REAL,
  quantity_units  INTEGER,
  min_quality     INTEGER,
  CHECK (
    (ingredient_kind = 'resource' AND quantity_units IS NULL) OR
    (ingredient_kind = 'item'     AND quantity_scu   IS NULL)
  )
);

-- **Ce que la qualité des minerais change sur l'objet fabriqué.** Remarque de
-- l'utilisateur : « les objets craftés ont des statistiques améliorées selon
-- la qualité des minerais ». C'est vrai, et le jeu le publie — 5 695
-- modificateurs sur 1 546 blueprints, soit 97 % d'entre eux — mais rien ne le
-- lisait. C'est ce qui explique qu'un joueur parle d'un « P6-LR 900 » : 900
-- est la **qualité** du matériau, sur une échelle de 0 à 1000.
--
-- Le multiplicateur s'interpole linéairement entre les deux bornes. Et il
-- **décroît** dans 462 cas sur 5 695 : le recul d'une arme s'améliore en
-- baissant, et lire la plage à l'envers ferait conseiller le pire matériau.
CREATE TABLE blueprint_modifiers (
  id              INTEGER PRIMARY KEY,
  tier_id         INTEGER NOT NULL REFERENCES blueprint_tiers(id) ON DELETE CASCADE,
  group_key       TEXT,       -- le composant concerné : FRAME, EMITTER…
  group_name      TEXT,
  uuid            TEXT,
  cle             TEXT NOT NULL,   -- weapon_damage, armor_damagemitigation…
  nom             TEXT,            -- le libellé du jeu : « Integrity »
  nom_fr          TEXT,            -- rempli par `disco trad`
  group_name_fr   TEXT,
  quality_min     REAL,
  quality_max     REAL,
  mult_min        REAL,       -- multiplicateur à la qualité minimale
  mult_max        REAL,       -- multiplicateur à la qualité maximale
  interpolation   TEXT        -- linear, à ce jour la seule valeur rencontrée
);

CREATE INDEX ix_bpm_tier ON blueprint_modifiers (tier_id);

CREATE INDEX ix_bpi_tier ON blueprint_ingredients (tier_id);
CREATE INDEX ix_bpi_ref  ON blueprint_ingredients (ref_uuid);

-- Vu depuis le blueprint : « dans quels pools de récompense suis-je ? »
CREATE TABLE blueprint_sources (
  id             INTEGER PRIMARY KEY,
  blueprint_uuid TEXT NOT NULL REFERENCES blueprints(uuid) ON DELETE CASCADE,
  pool_uuid      TEXT,
  pool_key       TEXT NOT NULL,
  UNIQUE (blueprint_uuid, pool_key)
);

CREATE INDEX ix_bps_pool ON blueprint_sources (pool_uuid);

-- Vu depuis la mission : « quels pools est-ce que je distribue ? »
-- C'est ce qui répond à « où je choppe le blueprint de tel équipement ».
-- 768 contrats en distribuent, et les 1013 références de pool retombent
-- toutes sur un pool déclaré côté blueprint.
CREATE TABLE contract_reward_pools (
  id            INTEGER PRIMARY KEY,
  contract_uuid TEXT NOT NULL REFERENCES contracts(uuid) ON DELETE CASCADE,
  pool_uuid     TEXT NOT NULL,
  chance        REAL,
  UNIQUE (contract_uuid, pool_uuid)
);

CREATE INDEX ix_crp_pool ON contract_reward_pools (pool_uuid);

-- Contenu d'un pool. Redondant avec blueprint_sources par construction, mais
-- il porte l'ItemName lisible, que le blueprint seul ne donne pas toujours.
CREATE TABLE reward_pool_contents (
  id             INTEGER PRIMARY KEY,
  pool_uuid      TEXT NOT NULL,
  blueprint_uuid TEXT,
  item_uuid      TEXT,
  item_name      TEXT,
  UNIQUE (pool_uuid, blueprint_uuid)
);

CREATE INDEX ix_rpc_bp ON reward_pool_contents (blueprint_uuid);

CREATE TABLE blueprint_dismantle_returns (
  id             INTEGER PRIMARY KEY,
  blueprint_uuid TEXT NOT NULL REFERENCES blueprints(uuid) ON DELETE CASCADE,
  ref_uuid       TEXT,
  ref_name       TEXT NOT NULL,
  quantity_scu   REAL
);

-- ---------------------------------------------------------------- ressources

CREATE TABLE resources (
  uuid TEXT PRIMARY KEY,
  key  TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL
       CHECK (kind IN ('mineable', 'cave_harvestable', 'salvageable', 'harvestable')),
  tier TEXT
);

CREATE TABLE resource_composition (
  id           INTEGER PRIMARY KEY,
  deposit_uuid TEXT NOT NULL REFERENCES resources(uuid) ON DELETE CASCADE,
  part_uuid    TEXT,
  part_key     TEXT,
  part_name    TEXT NOT NULL,
  min_pct      REAL,
  max_pct      REAL,
  probability  REAL
);

CREATE INDEX ix_rc_deposit ON resource_composition (deposit_uuid);
CREATE INDEX ix_rc_part    ON resource_composition (part_uuid);

CREATE TABLE locations (
  uuid          TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  system        TEXT,
  loc_type      TEXT,
  provider_name TEXT
);

CREATE INDEX ix_loc_system ON locations (system);

-- `starmap.json` : 2 054 objets célestes reliés par ParentUUID. C'est la
-- hiérarchie qui permet de dire de quel système relève un lieu, au lieu de
-- deviner d'après son nom.
-- L'arborescence spatiale, telle que le joueur se la représente :
-- Stanton (étoile) -> Crusader (planète) -> Yela (lune).
-- **Ce qu'on trouve dans un lieu — 281 lieux, 22 services.** Le mot
-- `Amenities` n'apparaissait nulle part dans le code, alors que les 281 lieux
-- étaient déjà tous dans `starmap` : un ajout pur, sans problème
-- d'appariement.
--
-- Deux usages qu'aucune table ne couvrait : la **taille de hangar et de
-- plateforme** — « est-ce que je peux poser mon Hammerhead ici ? » — et les
-- **31 raffineries selon le jeu**, à croiser avec les 21 que connaît UEX. Le
-- projet a déjà appris avec `refineries_yields` ce que coûte de ne regarder
-- qu'une source.
--
-- On garde `display_name` en plus de `name` : le jeu écrit « Hangar L » d'un
-- côté et « Hangar (L) » de l'autre, et c'est la seconde forme qui s'affiche.
CREATE TABLE location_amenities (
  location_uuid TEXT NOT NULL REFERENCES starmap(uuid) ON DELETE CASCADE,
  amenity_uuid  TEXT,
  name          TEXT NOT NULL,
  display_name  TEXT,
  PRIMARY KEY (location_uuid, name)
);

CREATE INDEX ix_amenities_nom ON location_amenities (name);

CREATE TABLE starmap (
  uuid          TEXT PRIMARY KEY,
  name          TEXT,
  parent_uuid   TEXT,
  parent_name   TEXT,               -- Crusader
  parent_type   TEXT,               -- Planet
  type_name     TEXT,               -- Planet, Moon, Star, Outpost…
  system_uuid   TEXT,
  system_name   TEXT,               -- Stanton
  path          TEXT,               -- « Stanton / Crusader / Yela »
  depth         INTEGER,
  -- Le texte officiel du jeu, présent sur 2 032 lieux sur 2 054 et jamais lu
  -- jusqu'ici. « C'est quoi Grim HEX » n'avait aucune source, alors que la
  -- réponse était écrite par CIG dans le fichier qu'on ingère déjà.
  description   TEXT,
  -- L'autorité qui règne sur le lieu, quand il y en a une. Elle dit mieux que
  -- le système si l'endroit est tenu : « Rough & Ready » et « XenoThreat » ne
  -- promettent pas la même tranquillité que « UEE ».
  jurisdiction  TEXT,
  -- Coordonnées, en mètres depuis l'étoile du système. Elles viennent de
  -- `starmap_positions.json`, que le premier passage d'ingestion ignorait
  -- complètement — 1 771 positions inutilisées. Sans elles, impossible de
  -- répondre à « c'est loin ? », qui est pourtant la question qui décide si
  -- une route commerciale vaut le déplacement.
  x             REAL,
  y             REAL,
  z             REAL,
  -- Le jeu distingue les points atteignables en saut quantique des autres :
  -- un astéroïde sans balise se rejoint au vol, ce qui n'a pas le même coût.
  qt_valid      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_starmap_parent ON starmap (parent_uuid);
CREATE INDEX ix_starmap_system ON starmap (system_name);
CREATE INDEX ix_starmap_name   ON starmap (name);

-- Points de saut entre systèmes, avec leur coût en carburant quantique.
-- Deux entrées seulement aujourd'hui (Nyx-Pyro, Pyro-Stanton), mais c'est
-- ce qui rend une route inter-système comparable à une route locale.
CREATE TABLE jump_points (
  entry_uuid    TEXT,
  exit_uuid     TEXT,
  entry_system  TEXT NOT NULL,
  exit_system   TEXT NOT NULL,
  fuel_cost     REAL,
  PRIMARY KEY (entry_system, exit_system)
);

CREATE TABLE resource_locations (
  id            INTEGER PRIMARY KEY,
  resource_uuid TEXT NOT NULL REFERENCES resources(uuid) ON DELETE CASCADE,
  location_uuid TEXT NOT NULL REFERENCES locations(uuid) ON DELETE CASCADE,
  group_name    TEXT,
  probability   REAL,
  UNIQUE (resource_uuid, location_uuid, group_name)
);

CREATE INDEX ix_rl_res ON resource_locations (resource_uuid, probability DESC);

-- ---------------------------------------------------------------- commerce

CREATE TABLE commodities (
  uuid             TEXT PRIMARY KEY,
  key              TEXT NOT NULL,
  name             TEXT NOT NULL,
  description      TEXT,
  density_g_per_cc REAL,
  groups           TEXT,
  -- **Le lien brut → raffiné, écrit par le jeu.** 30 commodités sur 206 :
  -- `Quantainium (Raw)` → `Quantainium`, `Raw Ice` → `Pressurized Ice`. Le
  -- module de raffinage rapprochait les deux formes **par le nom** ; le champ
  -- le dit explicitement, avec un identifiant — et « Raw Ice » →
  -- « Pressurized Ice » est précisément le cas que le nom seul ne donne pas.
  refined_name     TEXT,
  refined_uuid     TEXT
);

-- **Où écouler une cargaison** — 849 lieux, la moitié du fichier que l'audit
-- précédent n'avait pas regardée. `DONNEES_NON_UTILISEES.md` avait écarté
-- `trade_locations.json` sur ses `ProducesTags`, et l'argument tenait :
-- 13 avant-postes producteurs contre un `where_to_find_resource` qui répond
-- déjà mieux. Mais **`ConsumesTags` est l'information symétrique**, et le
-- fichier est un sur-ensemble strict de `shops` — 845 UUID sur 845, plus
-- 120 lieux.
--
-- Réserve mesurée, et c'est elle qui décide de la forme : sur 102 tags de
-- consommation distincts, **36 seulement portent un nom de commodité**. Les
-- plus fréquents sont des **catégories** — Supplies 913, Common 679, Food 642.
-- On garde donc le tag brut et on résout la commodité **quand elle existe**,
-- plutôt que de faire passer une catégorie pour une marchandise.
--
-- `Negative` est gardé à part : 276 lieux déclarent ce qu'ils **refusent**,
-- et confondre les deux listes ferait envoyer un joueur vendre là où on ne
-- lui achètera rien.
CREATE TABLE trade_flows (
  location_uuid   TEXT NOT NULL,
  location_name   TEXT,
  sens            TEXT NOT NULL CHECK (sens IN ('consomme', 'produit')),
  refuse          INTEGER NOT NULL DEFAULT 0,   -- la liste `Negative`
  tag_uuid        TEXT,
  tag_name        TEXT NOT NULL,
  commodity_uuid  TEXT,                          -- NULL si le tag est une catégorie
  PRIMARY KEY (location_uuid, sens, refuse, tag_name)
);

CREATE INDEX ix_flux_tag  ON trade_flows (tag_name, sens);
CREATE INDEX ix_flux_comm ON trade_flows (commodity_uuid);

CREATE TABLE shops (
  uuid                TEXT PRIMARY KEY,
  class_name          TEXT,
  display_name        TEXT,
  starmap_object_uuid TEXT
);

CREATE TABLE commodity_shops (
  id             INTEGER PRIMARY KEY,
  commodity_uuid TEXT NOT NULL REFERENCES commodities(uuid) ON DELETE CASCADE,
  shop_uuid      TEXT NOT NULL REFERENCES shops(uuid) ON DELETE CASCADE,
  direction      TEXT NOT NULL CHECK (direction IN ('sold_at', 'bought_at')),
  UNIQUE (commodity_uuid, shop_uuid, direction)
);

-- ---------------------------------------------------------------- missions

-- Les emplacements d'un **objet** — `stdItem.Ports`, jamais ingéré jusqu'ici.
-- Le pendant de `hardpoints`, qui ne couvre que les vaisseaux : « quelles
-- optiques vont sur un P8-AR » n'avait donc aucune donnée, alors qu'un port
-- `optics_attach` déclare `WeaponAttachment.IronSight` en tailles 1 à 2.
--
-- 4 383 objets sur 5 420 en portent au moins un, et 174 accessoires existent
-- au catalogue — 56 optiques, 38 canons, 62 chargeurs.
CREATE TABLE item_ports (
  id          INTEGER PRIMARY KEY,
  item_uuid   TEXT NOT NULL,
  port_name   TEXT,             -- « optics_attach », « barrel_attach »
  -- Le type accepté, tel que le jeu l'écrit : « WeaponAttachment.IronSight ».
  -- On le garde entier et on découpe à la lecture — le préfixe est le type de
  -- l'objet, le suffixe son sous-type.
  accepted    TEXT NOT NULL,
  min_size    INTEGER,
  max_size    INTEGER,
  -- **Le type et la taille ne suffisent pas à dire ce qui se monte.** Le port
  -- `barrel_attach` du P6-LR exige `FPS_Barrel` **et** `ballistic_attach` :
  -- l'Emod « Tweaker » Stabilizer, qui n'est que `energy_attach`, a la bonne
  -- famille et la bonne taille et ne se monte pourtant pas. Sans cette
  -- colonne, on proposait un accessoire impossible avec un chiffre à l'appui.
  -- Remarque de l'utilisateur, 2026-08-10.
  required_tags TEXT
);

CREATE INDEX ix_item_ports_item ON item_ports (item_uuid);
CREATE INDEX ix_item_ports_type ON item_ports (accepted);

-- Les constructeurs. `manufacturers.json` était l'un des deux fichiers du
-- dépôt jamais ouverts (point 5 de l'audit). 141 entrées, 116 décrites.
--
-- **Les deux clés servent** : les vaisseaux citent leur constructeur par son
-- nom complet (« Aegis Dynamics », 19 sur 19 retrouvés), les objets par son
-- code (« KSAR », 115 valeurs distinctes). D'où la fiche du Coda qui
-- s'affichait « Coda Pistol — KSAR ».
CREATE TABLE manufacturers (
  uuid        TEXT PRIMARY KEY,
  code        TEXT,
  name        TEXT NOT NULL,
  description TEXT
);

CREATE INDEX ix_manufacturers_code ON manufacturers (code);
CREATE INDEX ix_manufacturers_name ON manufacturers (name);

CREATE TABLE factions (
  uuid             TEXT PRIMARY KEY,
  key              TEXT,
  name             TEXT NOT NULL,
  faction_type     TEXT,
  default_reaction TEXT,
  description      TEXT,
  -- État civil, dans `Reputation.Properties` : 38 factions sur 74. C'est ce
  -- que « c'est qui les Headhunters » attend vraiment — la description dit ce
  -- qu'ils font, pas où ils sont ni qui les dirige.
  headquarters     TEXT,          -- « Quinton, Angeli, Croshaw System »
  founded          TEXT,          -- une année, parfois une phrase
  leadership       TEXT,          -- « Narumi Arai, CEO »
  area             TEXT,          -- « UEE », « Banu Protectorate »…
  focus            TEXT,          -- « Communication Services »
  lawful           INTEGER,       -- NULL quand la faction n'a pas de Properties
  -- Les quatre drapeaux de la racine, sur les 74. Ils ne disent **pas** si une
  -- zone est dangereuse : `AbleToArrest` désigne les forces de police (UEE
  -- Advocacy, Crusader Security, Hurston Security…) et `NoLegalRights` les
  -- hors-la-loi (XenoThreat, Outlaws, Vanduul). C'est un état civil, pas une
  -- géographie — voir docs/DONNEES_NON_UTILISEES.md.
  able_to_arrest       INTEGER,
  polices_criminality  INTEGER,
  polices_trespass     INTEGER,
  no_legal_rights      INTEGER
);

CREATE TABLE contracts (
  uuid               TEXT PRIMARY KEY,
  debug_name         TEXT,
  title              TEXT,
  description        TEXT,
  mission_type       TEXT,
  mission_giver      TEXT,              -- l'org : « Foxwell Enforcement »
  faction_uuid       TEXT,
  faction_name       TEXT,
  reputation_scope   TEXT,
  -- L'arborescence du jeu : une org, un système, une famille de missions, et
  -- un palier de réputation. C'est ce qui permet de répondre « les missions
  -- Foxwell Enforcement à Pyro » plutôt que d'énumérer six titres.
  family             TEXT,              -- GeneratorClass : FoxwellEnforcement_Patrol
  system             TEXT,              -- Stanton | Pyro | Nyx
  difficulty_label   TEXT,              -- Easy | Medium | Hard | Super…
  rank_index         INTEGER,
  min_standing_name  TEXT,
  min_standing_value INTEGER,
  max_standing_name  TEXT,
  max_standing_value INTEGER,
  reward_uec         INTEGER,
  reward_calculated  INTEGER NOT NULL DEFAULT 0,
  crime_min          INTEGER,
  crime_max          INTEGER,
  deadline_seconds   INTEGER,
  illegal            INTEGER NOT NULL DEFAULT 0,
  shareable          INTEGER NOT NULL DEFAULT 0,
  once_only          INTEGER NOT NULL DEFAULT 0,
  time_to_complete   INTEGER,
  difficulty_profile TEXT,
  -- **Quatre axes de difficulté, sur 2 346 contrats.** `difficulty_profile`,
  -- déjà en base, ne dit pas la même chose : il vaut General, Logistics ou
  -- Discovery — une **catégorie** — et il est NULL sur 2 763 contrats.
  --
  -- Le libellé porte son propre rang dans son suffixe
  -- (`Insane_complexity_NOT_soloable_7`), donc le classement ne demande aucune
  -- table de correspondance. Les sept niveaux sont **tous peuplés** sur les
  -- quatre axes : la variance est réelle, ce n'est pas une colonne décorative.
  --
  -- Deux libellés répondent littéralement à « est-ce que je peux la faire
  -- seul ? » — `Insane_complexity_NOT_soloable_7` (123 contrats) et
  -- `Extremely_hard_to_manage_alone_6` (125). Jusqu'ici seul `shareable`
  -- était lu, et il dit si la mission se **partage**, pas si elle l'exige.
  diff_connaissance  TEXT,      -- GameKnowledge
  diff_pilotage      TEXT,      -- MechanicalSkill
  diff_charge        TEXT,      -- MentalLoad : la solitude se lit ici
  diff_risque        TEXT,      -- RiskOfLoss
  not_for_release    INTEGER NOT NULL DEFAULT 0,
  work_in_progress   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_contracts_live   ON contracts (not_for_release, work_in_progress);
CREATE INDEX ix_contracts_type   ON contracts (mission_type);
CREATE INDEX ix_contracts_group  ON contracts (mission_giver, system, min_standing_value);
CREATE INDEX ix_contracts_family ON contracts (family, system);

-- Arborescence explicite entre missions : RequiredMissions d'un côté,
-- CompletionTags.UnlocksMissions de l'autre.
CREATE TABLE contract_links (
  id            INTEGER PRIMARY KEY,
  contract_uuid TEXT NOT NULL REFERENCES contracts(uuid) ON DELETE CASCADE,
  kind          TEXT NOT NULL CHECK (kind IN ('requires', 'unlocks')),
  other_uuid    TEXT NOT NULL,
  other_name    TEXT,
  UNIQUE (contract_uuid, kind, other_uuid)
);

CREATE INDEX ix_clink_other ON contract_links (other_uuid);

-- Regroupement précalculé : une org, un système. C'est l'unité de réponse
-- naturelle — « les missions Foxwell Enforcement à Pyro » — et la calculer à
-- l'ingestion évite de la recalculer à chaque question.
CREATE TABLE mission_groups (
  id                 INTEGER PRIMARY KEY,
  mission_giver      TEXT NOT NULL,
  system             TEXT,
  contract_count     INTEGER NOT NULL,
  family_count       INTEGER NOT NULL,
  min_standing_name  TEXT,
  min_standing_value INTEGER,
  max_standing_name  TEXT,
  max_standing_value INTEGER,
  UNIQUE (mission_giver, system)
);

CREATE TABLE contract_reputation (
  id                 INTEGER PRIMARY KEY,
  contract_uuid      TEXT NOT NULL REFERENCES contracts(uuid) ON DELETE CASCADE,
  direction          TEXT NOT NULL CHECK (direction IN ('prerequisite', 'gained')),
  faction_uuid       TEXT,
  faction_name       TEXT,
  scope              TEXT,
  scope_uuid         TEXT,
  min_standing_name  TEXT,
  min_standing_value INTEGER,
  max_standing_name  TEXT,
  max_standing_value INTEGER,
  amount             INTEGER,
  tier               TEXT
);

CREATE INDEX ix_crep_contract ON contract_reputation (contract_uuid, direction);
CREATE INDEX ix_crep_scope    ON contract_reputation (scope);

CREATE TABLE contract_locations (
  id            INTEGER PRIMARY KEY,
  contract_uuid TEXT NOT NULL REFERENCES contracts(uuid) ON DELETE CASCADE,
  role          TEXT NOT NULL CHECK (role IN ('availability', 'required', 'mission')),
  location_uuid TEXT,
  location_name TEXT,
  -- **Ces noms sont traduits, et on ne les lisait pas.** Un lieu de mission
  -- porte souvent une périphrase — « the clinic inside Megumi Refueling at the
  -- L5 Lagrange of Pyro VI » — et non un nom propre. Le balayage en signalait
  -- 1 535 comme de l'anglais laissé dans une réponse française, et j'ai
  -- d'abord conclu à une lacune de la source. Vérifié : les 40 testés sont
  -- **tous** dans `labels.json`, sous des clés propres
  -- (`mission_location_pyro_105a`), et le Cirque Lisoir les traduit —
  -- « la clinique dans Megumi Refueling au point Lagrange L5 de Pyro VI ».
  --
  -- Le nom n'étant pas une clé mais du texte, la résolution passe par l'index
  -- anglais inversé, comme pour les fiches d'objet.
  location_name_fr TEXT,
  pool_name     TEXT
);

CREATE INDEX ix_cloc_contract ON contract_locations (contract_uuid, role);

-- Les objectifs d'un contrat — `ObjectiveTokens`, jamais ingéré. 2 520
-- contrats sur 5 108 en portent.
--
-- **Ce ne sont pas des phrases.** Le jeu ne stocke ici qu'un nom de debug
-- (« HijackedShip_Caterpillar ») et un type de gestionnaire
-- (`ObjectiveHandler_Hauling`). Le second est net et se traduit ; le premier
-- se lit en le découpant, et le rendu doit dire que c'est approximatif plutôt
-- que de faire passer un identifiant pour une consigne.
CREATE TABLE contract_objectives (
  id            INTEGER PRIMARY KEY,
  contract_uuid TEXT NOT NULL REFERENCES contracts(uuid) ON DELETE CASCADE,
  position      INTEGER NOT NULL,
  debug_name    TEXT,
  handler       TEXT,
  -- La consigne rédigée, quand le jeu en écrit une. `labels.json` porte
  -- 1 306 clés `<famille>_obj_long_NN` ; elles ne couvrent que 496 contrats
  -- sur les 2 520 qui ont des objectifs — les missions générées (courrier,
  -- fret, prime) n'ont que des noms de debug. On garde la clé pour que
  -- `disco trad` y accroche le français : CircusPES les traduit **toutes**.
  cle_texte     TEXT,
  texte_en      TEXT,
  texte_fr      TEXT
);

CREATE INDEX ix_cobj_contract ON contract_objectives (contract_uuid);

-- Les complexes où se jouent les missions, et leurs salles. Le contrat ne cite
-- que la salle (« Engineering Wing ») ; `labels.json` nomme les deux dans une
-- même famille de clés — `FacilityDelve_WingA_name` pour la salle,
-- `FacilityDelve_Stanton4a_name` pour le site. Sans cette table, une fiche de
-- mission annonce une pièce sans dire dans quel bâtiment elle se trouve.
CREATE TABLE mission_sites (
  famille    TEXT NOT NULL,   -- préfixe partagé, ex. « FacilityDelve »
  segment    TEXT NOT NULL,   -- ex. « WingA », « Stanton4a »
  nom        TEXT NOT NULL,   -- ex. « Engineering Wing », « Onyx Facility »
  est_salle  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (famille, segment)
);

CREATE INDEX ix_msites_nom ON mission_sites (nom);

-- Les neuf méthodes de raffinage, avec leurs trois notes. Le jeu les publie
-- lui-même — « Low Speed // High Cost // High Yield » — et le Cirque Lisoir
-- les traduit toutes, description comprise.
--
-- **Le nom de la clé ment, le détail dit vrai** : `FastCareful` porte « Low
-- Speed ». On analyse donc `_Details`, jamais le segment de clé.
--
-- Aucune méthode n'est la meilleure partout, et c'est tout l'intérêt de la
-- question : « la plus rapide » et « la plus efficace » ne désignent pas la
-- même.
CREATE TABLE refinery_methods (
  cle             TEXT PRIMARY KEY,   -- ex. « SlowCareful »
  nom_en          TEXT NOT NULL,
  nom_fr          TEXT,
  description_en  TEXT,
  description_fr  TEXT,
  vitesse         INTEGER,            -- 1 très lent … 4 rapide
  cout            INTEGER,            -- 1 bon marché … 4 cher
  rendement       INTEGER             -- 1 faible … 4 élevé
);
