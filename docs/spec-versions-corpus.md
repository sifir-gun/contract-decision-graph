# Spec : versions successives des textes du corpus

Statut : validée section par section par le propriétaire le 16/10/2026, à relire avant le
plan d'implémentation. Source de vérité de ce chantier ; `docs/spec-phase1.md` reste celle
du projet.

## Contexte

Le corpus (`data/corpus/`) ne tient qu'une version par article. L'article L441-10 du Code
de commerce indexé est « en vigueur du 26 avril 2019 au 01 janvier 2027 ». Le contrôle des
échéances de la CI (`scripts/echeances_corpus.py --jours 60`, job « audit (pip-audit) »,
exigé par la protection de `main`) échoue donc à partir du 02/11/2026, à chaque pull
request, tant que l'article et sa fiche ne sont pas mis à jour.

Lu sur Légifrance le 16/10/2026, page par page, dans un navigateur :

- version suivante : LEGIARTI000054567689, « Version en vigueur à partir du 01 janvier
  2027 », « Modifié par Ordonnance n°2026-671 du 27 juillet 2026 - art. 2 », marquée « À
  VENIR » ; note « Conformément à l'article 49 de l'ordonnance n° 2025-1247 du 17 décembre
  2025 dans sa rédaction résultant de l'article 17 de l'ordonnance n° 2026-671 du 27
  juillet 2026, ces dispositions, dans leur rédaction issue de ladite ordonnance
  n° 2025-1247, entrent en vigueur le 1er janvier 2027. » ;
- comparaison officielle des deux versions (« Comparer les versions ») : une seule
  modification, au 4e alinéa du I ; la facture périodique n'est plus définie « au sens du
  3 du I de l'article 289 du code général des impôts » mais « permise en application de
  l'article L. 216-34 du code des impositions sur les biens et services » ;
- une version intermédiaire (LEGIARTI000053151445, ordonnance n° 2025-1247), remplacée
  avant d'entrer en vigueur, n'apparaît pas dans la frise des versions ;
- la page de l'ordonnance a déclenché la vérification anti-robots : non contournée, non
  lue.

Les délais (30 jours sauf stipulation contraire ; 60 jours ; 45 jours fin de mois ; 45
jours après une facture périodique) ne changent pas : ni `payment_delay_*` de
`config/decision.yaml`, ni la règle financière.

Remplacer le fichier ferait citer, jusqu'au 31/12/2026, un texte pas encore en vigueur,
sans le signaler : `valid_from` est stocké mais jamais lu. Garder les deux versions est
impossible en l'état : un fichier par article au manifeste, clé d'unicité
`(domain, content_hash, embedding_model)` qui écarte en silence les paragraphes communs
(`ON CONFLICT DO NOTHING`), recherche groupée par référence (`DISTINCT ON (reference)`)
qui peut garder l'extrait de la version périmée.

## Décisions du propriétaire (16/10/2026)

1. Option A : le corpus garde les versions successives d'un article ; la date d'analyse
   choisit la version.
2. Même référence pour toutes les versions (« C. com., art. L441-10 ») : la version se
   déduit de la date d'analyse, scellée ; les décisions déjà scellées gardent leur nom.
3. Une seule fiche « Délais de paiement », fidèle aux deux versions, qui déclare les
   versions contre lesquelles elle a été vérifiée.
4. Le fichier brut de la version 2027 est copié à la main par le propriétaire.
5. Approche 1 : recherche datée (la requête écarte les versions supplantées avant le
   regroupement et la limite) et même règle, pure, dans le CRAG.
6. Le contrôle « pas encore en vigueur » s'applique à tout le corpus, pas seulement aux
   références à plusieurs versions.

## Objectifs

- Analyse datée jusqu'au 31/12/2026 : la version 2019 de L441-10 est retenue ; à partir
  du 01/01/2027 : la version 2027, sans constat « référence expirée » pour L441-10.
- Une version pas encore en vigueur à la date d'analyse n'est jamais retenue, pour toute
  source ; elle est signalée quand aucune version de sa référence n'est en vigueur (une
  version à venir dont la devancière est en vigueur est écartée sans constat).
- Le contrôle des échéances passe après le 01/11/2026 ; il garde sa défense : une fiche
  non relue contre une nouvelle version expire à l'entrée en vigueur de celle-ci.
- Le dossier d'un contrat montre le texte de la version en vigueur à sa date d'analyse.

## Hors périmètre

- Aucun autre article mis à jour ; aucune veille des versions à venir des autres
  articles (la CI ne connaît que les fins déjà annoncées) : piste au journal.
- Aucune règle, aucun seuil, aucun réglage de `config/decision.yaml` ne change.
- Ni le schéma d'état, ni le format v2 du journal d'audit, ni le rejeu ne changent ;
  aucun champ ajouté à `ClauseRetrieval` ni à `RetrievalTrace` (scellés).
- Aucun affichage des bornes de version (dossier, web, MCP).
- La version intermédiaire jamais entrée en vigueur reste hors du corpus.
- Aucune récupération automatique depuis Légifrance.

## Conception

### 1. Fichiers, manifeste, lecture

- `raw/code-commerce/L441-10.txt` (version 2019) reste tel quel. La version 2027 est
  copiée par le propriétaire dans `raw/code-commerce/L441-10_2027-01-01.txt` : le suffixe
  est la date de début de validité.
- Manifeste : l'entrée `articles` (rattachement) ne change pas ; une clé `later_versions`
  par source déclare les versions suivantes, avec leur provenance :

  ```yaml
      # versions suivantes d'un article, de la plus ancienne à la plus récente ; la
      # première est le fichier de `file` (date de récupération de la source)
      later_versions:
        "L441-10":
          - file: "L441-10_2027-01-01.txt"
            url: https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000054567689/2027-01-01
            retrieved_at: AAAA-MM-JJ   # date de la copie du propriétaire
  ```

- `Manifest.files()` rend aussi les fichiers des versions suivantes (tout fichier de
  `raw/` reste déclaré, ingéré ou exclu) ; un article de `later_versions` absent de
  `articles` est une erreur.
- Contrôles à la lecture (`ingestion.articles`), chacun par une erreur explicite qui nomme
  le fichier : titre de l'article attendu (existant) ; date du suffixe égale au début de
  validité lu ; versions enchaînées (fin de l'une = début de la suivante) ; seule la
  dernière peut être ouverte.
- Parseur (`domain/corpus.py`, `_VERSION`) : accepte aussi « Version en vigueur à partir
  du {date} » (début à cette date, sans fin). La note « Conformément à … » est déjà
  reconnue.
- `articles()` rend une `Article` par version : même `reference`, même rattachement
  (toutes les versions d'un article ont donc les mêmes domaines et types de clause).
  Date de récupération : pour la première version, celle de la source ou de
  `retrieved_at_overrides`, comme aujourd'hui ; pour une version suivante, son
  `retrieved_at`, **obligatoire** (absent : erreur, jamais la date de la source).
- Toute ligne d'interface présente dans une copie (« À VENIR »…) est retirée par une
  règle testée de `domain/corpus.py` (`_INTERFACE`), jamais à la main (`SOURCES.md`,
  « Récupération »).
- La hiérarchie déclarée (`hierarchy`) ne change pas : même sous-section.

### 2. Base de données et ingestion

- Migration `migrations/009_rag_versions.sql`, idempotente comme les migrations 002 et
  suivantes :

  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS rag_chunks_version_key
      ON rag_chunks (domain, content_hash, embedding_model, valid_from)
      NULLS NOT DISTINCT;
  ALTER TABLE rag_chunks
      DROP CONSTRAINT IF EXISTS rag_chunks_domain_content_hash_embedding_model_key;
  ```

  Une source sans début de validité (articles EUR-Lex) reste dédoublonnée comme
  aujourd'hui (`NULLS NOT DISTINCT`) ; les paragraphes communs à deux versions ont chacun
  leur ligne. Nom de la contrainte d'origine lu dans la base locale le 16/10/2026.
- `rag_store.insert` : `ON CONFLICT (domain, content_hash, embedding_model, valid_from) DO
  NOTHING`. `sync` ne change pas : `valid_from` est déjà dans `_METADATA`.
- Les extraits des fiches reçoivent un début de validité (section 4) en plus de leur fin.
- `Passage` (port `retriever`) porte `valid_from` ; `_ranked` le lit.
- `indexed()` (port `CorpusSearch`) rend (référence, début de validité, texte, empreinte du
  texte embarqué) ; `evaluation.check_index` compare sur ces quatre valeurs : une base
  indexée sous l'ancienne clé (extraits de la version 2027 manquants) est détectée,
  « relancer ingest ».
- Ordre : `setup-db` (009), puis `ingest`, sur le poste comme dans le cluster (tâches du
  chart inchangées) ; la ligne `setup-db` de CLAUDE.md cite la 009.

### 3. Recherche et CRAG

- Règle pure (`domain/corpus.py`) :
  - `in_force(valid_from, valid_until, on)` : commencée (aucun début, ou début ≤ `on`)
    et non expirée (`expired`, inchangé) ;
  - `drop_superseded(items, on)` : pour chaque référence qui a au moins une version en
    vigueur à `on`, écarte ses autres versions ; une référence sans version en vigueur
    garde toutes les siennes (signalées par le CRAG) ; ordre conservé. Elle vaut pour
    tout objet qui porte `reference`, `valid_from` et `valid_until` (`Protocol` du
    domaine, qui n'importe pas `Passage`).
- Port : `Retriever.search(domain, query, *, kind, k, on)` et
  `CorpusSearch.search_unfiltered(query, *, k, on)`, `on` obligatoire. Adaptateurs et
  doublures suivent (`tests/test_ports.py`).
- Adaptateur PostgreSQL : la condition s'ajoute au filtre, avant `DISTINCT ON` et
  `LIMIT` ; un extrait est gardé s'il est en vigueur à `on`, ou si aucune ligne de même
  référence et même modèle ne l'est (`NOT EXISTS`). Même règle que `drop_superseded`,
  vérifiée par un test de parité.
- `mesure-recherche` : date du jour (heure de Paris, `cli.today`), écrite dans le
  résultat (`date`) ; la mesure voit ce que verrait le CRAG ce jour-là.
- CRAG (`application/crag.py`) : `retrieve` passe `analysis_date` ; `generate` applique
  d'abord `drop_superseded` (la démonstration lui donne tous les extraits déclarés),
  puis, référence par référence, dans l'ordre de première apparition parmi les extraits
  pertinents (comme aujourd'hui) :
  - une version en vigueur : retenue (inchangé) ;
  - aucune version en vigueur, versions expirées : constat inchangé mot pour mot, avec la
    fin de la **dernière** version ; référence ajoutée à `expired` (inchangé) ;
  - aucune version en vigueur, versions à venir : constat « référence pas encore en
    vigueur à la date d'analyse (AAAA-MM-JJ) : {référence}, version en vigueur à partir
    du AAAA-MM-JJ, non retenue ({clause}) », avec le début de la **première** version ;
    la référence n'est ajoutée ni à `retained` ni à `expired` de `ClauseRetrieval` (le
    constat va, comme les autres, dans `RetrievalTrace.findings`, champ existant).
  Versions enchaînées sans trou (section 1) : une référence sans version en vigueur a
  toutes ses versions expirées, ou toutes à venir, jamais un mélange.
- Constat de clôture, quand il y a **au moins une** référence pertinente et **aucune** en
  vigueur : texte inchangé si toutes ont expiré ; sinon « références pertinentes toutes
  hors de leur période de vigueur pour la clause {clause} : elles ne peuvent pas
  justifier seules le verdict ». Aucun extrait pertinent : aucun constat (inchangé).
- Justification inchangée : sans référence en vigueur, `INSUFFISANT`, puis `ESCALADE`.
- Démonstration (`adapters/demo/references.py`) : `valid_from` passé au `Passage`.
- Effet de la portée (décision 6) : une analyse antidatée avant le 20/08/2026 ne retient
  plus L442-1 (version en vigueur depuis cette date), ni les deux fiches qui la citent
  (« Plafonds et clauses de responsabilité », « Durée d'engagement et préavis de
  résiliation » : leur fenêtre commence le 20/08/2026, section 4) ; elle les signale.
  Les articles du Code civil, en vigueur depuis 2016 et 2018, restent retenus : les
  clauses restent justifiées. Le jeu de démonstration et les tests (25/09/2026) ne sont
  pas concernés.
- Test de parité SQL/Python : `distinct_references: false` (un extrait par ligne), k au
  moins égal au nombre de lignes du domaine, comparaison des **ensembles** de couples
  (référence, début de validité).

### 4. Fiche et échéances

- En-tête facultatif d'une fiche :

  ```yaml
  versions_verifiees:
    code-commerce:
      L441-10: [2019-04-26, 2027-01-01]   # débuts de validité des versions relues
  ```

- Période couverte (`application/ingestion.py`, remplace `_fiche_validity`) : pour chaque
  article cité (citations des lignes d'affirmation, comme aujourd'hui), les versions
  déclarées, sinon la première version seule ; fenêtre de l'article : du début de la
  première version couverte à la fin de la dernière ; fenêtre de la fiche : intersection
  (plus tardif des débuts, plus proche des fins) ; ses extraits portent ce début et cette
  fin.
- Erreurs explicites : article déclaré non cité ; version déclarée absente du corpus ;
  versions déclarées non consécutives ; intersection vide.
- Résultat attendu : « Délais de paiement » du 26/04/2019, sans fin ; les fiches qui
  citent L442-1 commencent le 20/08/2026 ; les autres, au plus tardif de leurs articles.
- Fiche `fiches/delais-paiement.md`, relue mot à mot contre les deux fichiers :
  - 4e puce : « En cas de facture périodique, le délai convenu entre les parties ne peut
    dépasser quarante-cinq jours après la date d'émission de la facture (source :
    code-commerce, art. L441-10, I). » ;
  - la puce de version devient deux puces sourcées : version du 26 avril 2019 au 1er
    janvier 2027, facture périodique au sens du code général des impôts ; version à
    partir du 1er janvier 2027, facture périodique permise en application du code des
    impositions sur les biens et services, le reste de l'article inchangé ;
  - « Comment le projet l'applique » : la dernière puce (expiration au 1er janvier 2027)
    devient la règle des versions (la date d'analyse choisit la version ; fiche vérifiée
    contre les deux) ; les trois premières puces restent mot pour mot (le jeu
    d'évaluation figé cite les deux premières).
- Échéances (`ingestion.source_validities`, `scripts/echeances_corpus.py`) : une par
  référence ; pour un article, la fin de sa dernière version ; pour une fiche, la fin de
  sa fenêtre. 29 références (22 articles, 7 fiches).

### 5. Dossier et documentation

- `ingestion.reference_texts(on)` : pour chaque référence, le texte de sa version en
  vigueur à `on`, rien si aucune ne l'est (le dossier affiche alors une référence
  absente, comme aujourd'hui) ; `service.dossier` passe la date d'analyse de l'état.
  Ancien fil sans date d'analyse (`None`) : une référence à une seule version garde son
  texte ; une référence à plusieurs versions n'en a pas (affichée absente), jamais une
  version choisie au hasard. Web et MCP inchangés.
- Jeu d'évaluation figé (`data/evaluation/recherche.yaml`, `test_jeu_fige_le_2026_10_01`) :
  inchangé. Ses citations sont vérifiées contre les textes en vigueur le 01/10/2026, date
  où il a été figé (`reference_texts(date(2026, 10, 1))`) : la citation de la facture
  périodique « au sens du 3 du I de l'article 289 du code général des impôts » n'existe
  que dans la version 2019, et le test ne doit pas casser le 01/01/2027.
- `data/corpus/SOURCES.md` : ligne de la version 2027 (date de la copie), ligne actuelle
  « version en vigueur jusqu'au 01/01/2027 », rubrique « Versions successives » (un
  fichier par version, enchaînement, la date d'analyse choisit, fiches et versions
  vérifiées).
- `docs/exploitation.md`, « Corpus et versions des textes » : exemple L441-10 et
  procédure de mise à jour réécrits (copier la version, déclarer `later_versions`, relire
  la fiche, déclarer `versions_verifiees`, `setup-db`, `ingest`).
- `docs/spec-phase1.md`, source de vérité du projet : signature de `Retriever.search`,
  cas « pas encore en vigueur » de `generate`, validité des fiches, « la recherche rend
  la fin de validité » ; renvoi à cette spec.
- `docs/exploitation.md` : liste des migrations (« `001` à `008` » devient `009`) et
  rubrique « Migrations » ; `docs/mise-en-production.md` : cases « Processus de mise à
  jour » et « Première échéance ».
- `docs/adr-009-versions-du-corpus.md` : port daté, clé d'unicité, règle des versions
  supplantées, portée du contrôle du début de validité ; **retour arrière** : une image
  antérieure à la 009 sur une base migrée échoue à l'ingestion (son `ON CONFLICT` à trois
  colonnes n'a plus d'arbitre : erreur explicite), et sa recherche peut rendre en 2026
  l'extrait de la version 2027 sans le signaler. Le crochet d'ingestion ne tourne pas au
  retour arrière : limite consignée ; conduite à tenir dans `docs/exploitation.md` : un
  retour arrière durable supprime les extraits des versions suivantes et rétablit
  l'ancienne clé, sinon revenir à l'image courante.
- `docs/journal.md` : entrée du chantier, avec la piste « veille des versions à venir »
  et le rappel que `mesure-recherche`, désormais datée, ne se compare qu'à date égale de
  part et d'autre d'un changement de version.

## Critères d'acceptation

1. `uv run --no-sync python scripts/echeances_corpus.py --jours 60 --date 2026-11-02` et
   `--date 2027-03-01` : code 0, `"echeances": []`.
2. Démonstration (`DeclaredCrag`), clause `delai_paiement`, au 31/12/2026 et au
   01/01/2027 : références retenues, L441-10 et sa fiche ; aucun constat (ni « expirée »,
   ni « pas encore en vigueur »). `drop_superseded` appliqué aux extraits déclarés de la
   démonstration garde, pour L441-10, les seuls extraits de début 2019-04-26 au
   31/12/2026, et de début 2027-01-01 au 01/01/2027.
3. Après `setup-db` et `ingest` (base de test) : pour L441-10, autant de lignes par
   domaine que d'extraits des deux versions réunis ; une seconde ingestion n'insère ni ne
   supprime rien.
4. Recherche SQL datée : aux dates 19/08/2026, 31/12/2026 et 01/01/2027, l'ensemble des
   couples (référence, début de validité) rendus est celui que garde `drop_superseded`
   (corpus réel, `HashEmbedder`, `distinct_references: false`, k couvrant tout le
   domaine).
5. Insertion d'un extrait sans début de validité déjà présent : ignorée
   (`NULLS NOT DISTINCT` arbitre de `ON CONFLICT`).
6. Dossier d'une analyse de démonstration datée du 31/12/2026 : le texte de L441-10
   contient « code général des impôts » ; datée du 01/01/2027 : « code des impositions
   sur les biens et services ».
7. Fiche synthétique citant un article à deux versions, sans `versions_verifiees` : fin de
   validité au début de la seconde version, signalée par le contrôle des échéances.
8. Analyses de démonstration antidatées :
   - au 01/06/2026, clause de responsabilité qui porte un constat : C. civ. 1170, 1171
     et 1231-3 retenus ; L442-1 et la fiche « Plafonds et clauses de responsabilité »
     signalées « pas encore en vigueur » ; la clause reste justifiée (pas d'`INSUFFISANT`
     de son fait) ;
   - au 01/01/2019, clause `delai_paiement` qui porte un constat qui pénalise : L441-10
     (début cité : 2019-04-26) et sa fiche signalées « pas encore en vigueur », constat de
     clôture « … toutes hors de leur période de vigueur … », domaine financier
     `INSUFFISANT`.
9. Versions mal enchaînées (trou, chevauchement, version ouverte avant la dernière,
   suffixe différent du début) : `ingest` échoue avec une erreur qui nomme le fichier.
10. Fichier copié : seul le 4e alinéa du I diffère entre les deux versions (comparaison
    des paragraphes nettoyés).
11. `./scripts/check.sh` se termine par « check.sh : SUCCÈS » ; couverture ≥ 98 %.

## Dépendances vérifiées

- PostgreSQL 16 (poste : 16.11, image `pgvector/pgvector:pg16` de `docker-compose.yml` ;
  cluster : 16.15, CloudNativePG `16.15-standard-trixie`), documentation officielle lue le
  16/10/2026 :
  - `CREATE [ UNIQUE ] INDEX [ IF NOT EXISTS ] … ( … ) [ NULLS [ NOT ] DISTINCT ]`
    (https://www.postgresql.org/docs/16/sql-createindex.html) ;
  - `ON CONFLICT ( index_column_name, … )` : tout index unique qui porte exactement ces
    colonnes est retenu comme arbitre (https://www.postgresql.org/docs/16/sql-insert.html).
    Le comportement d'un index `NULLS NOT DISTINCT` en arbitre n'y est pas décrit : il est
    vérifié par le critère 5, test écrit d'abord.
- Aucune dépendance ajoutée.

## Réalisation

Tests d'abord, un commit par tâche au vert, dans cet ordre.

**Corpus synthétique.** Avant le fichier du propriétaire, les versions multiples se
testent sur un corpus de test (un article fictif à deux versions, une fiche qui le cite),
écrit dans `tmp_path`. Les fonctions de lecture d'`application/ingestion.py`
(`load_manifest`, `articles`, `load_fiches`, `pending_chunks`, `source_validities`,
`reference_texts`) lisent `RAW`, `FICHES` et `MANIFEST` à l'appel, et non plus en valeur
par défaut figée à la définition. Un fixture `corpus_synthetique` les remplace
(`monkeypatch`) et vide le cache de `reference_texts`. Aucun réglage, aucune option de
CLI n'est ajouté pour cela.

1. Règle pure : `in_force`, `drop_superseded`.
2. Parseur « à partir du » ; manifeste `later_versions` et contrôles d'enchaînement ;
   fixture du corpus synthétique (critère 9).
3. Migration 009 ; insertion des deux versions synthétiques (critère 5, tests `pg`).
4. Port daté, filtre SQL, `indexed()` avec début de validité, mesure datée ; parité
   SQL/Python sur le corpus synthétique (tests `pg`).
5. CRAG : versions, constats « pas encore en vigueur » et de clôture, sur le corpus
   synthétique ; critère 8 sur le corpus réel (L442-1 et la version 2019 de L441-10
   suffisent).
6. Fenêtre des fiches (`versions_verifiees`) et échéances par référence (critère 7).
7. Fichier du propriétaire, demandé au début de cette tâche : version 2027 au corpus,
   manifeste et `SOURCES.md`, fiche réécrite ; critères 1, 2, 3, 4 (corpus réel) et 10.
8. Dossier daté (critère 6) ; jeu d'évaluation daté ; ADR 009, `spec-phase1.md`,
   exploitation, mise en production, journal, CLAUDE.md ; `./scripts/check.sh`
   (critère 11).

Rien n'est poussé : le propriétaire lance `git push` et `gh pr create`.

**Tests existants touchés par construction** (relevés à la relecture, à confirmer au
plan) :
- `tests/test_web_demo.py` (L441-10 expirée au 01/03/2027) : le cas « expirée » passe
  sur le corpus synthétique ;
- `tests/test_echeances_corpus.py` : il ne reste aucune échéance réelle ; la branche
  d'échec du script se teste sur le corpus synthétique ;
- `tests/test_corpus.py` (fin de validité héritée par les fiches) ;
- `tests/test_ingest.py` (une seule fin de validité attendue pour L441-10) ;
- `tests/test_migrations.py` et `tests/test_cli.py` (dernière migration : la 008) ;
- `tests/test_service.py` (texte des références du dossier) ;
- `tests/test_evaluation_recherche.py` (citations vérifiées au 01/10/2026) ;
- `tests/test_llm_criteres.py` (dictionnaire par (source, article) qui garderait la
  dernière version sans le dire) ;
- doublures du port (`tests/doubles.py`, `tests/test_crag.py`) : paramètre `on`.
