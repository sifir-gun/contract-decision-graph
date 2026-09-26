# contract-decision-graph

[![CI](https://github.com/sifir-gun/contract-decision-graph/actions/workflows/ci.yml/badge.svg)](https://github.com/sifir-gun/contract-decision-graph/actions/workflows/ci.yml)
[![couverture minimale](https://img.shields.io/badge/couverture-%E2%89%A5%2096%20%25-brightgreen)](https://github.com/sifir-gun/contract-decision-graph/actions/workflows/ci.yml)

Graphe LangGraph qui rend un verdict go / no-go auditable sur des contrats fournisseurs. Le verdict est rendu par du code déterministe ; les LLM se limitent à l'extraction et à l'explication. Spécification : [docs/spec-phase1.md](docs/spec-phase1.md).

Données uniquement synthétiques ou publiques.

## Démarrage

```bash
cp .env.example .env        # puis remplacer chaque valeur
docker compose up -d        # PostgreSQL 16.11 + pgvector 0.8.1
uv sync
uv run python -m cdg.cli setup-db   # une fois : tables du checkpointer et du corpus, droits d'app_role
uv run pytest                       # -m "not pg" pour exclure volontairement les tests PostgreSQL
```

Analyse d'un contrat (synthétique), avec le fournisseur LLM de `config/decision.yaml` (appels payants, clé dans `.env`) :

```bash
uv run python -m cdg.cli fetch-embedding-model   # une fois : poids du modèle d'embedding (2,2 Go)
uv run python -m cdg.cli ingest                  # indexe le corpus ; rejouable
uv run python -m cdg.cli run contrat.txt --party "Nom de la partie"
uv run python -m cdg.cli resume <thread_id> --decision NO_GO --reviewer … --reason …
```

`run` rend un statut JSON : décision proposée ou finale, verdicts par domaine avec le résumé du CRAG, rapport d'échec le cas échéant. Une revue humaine suspend le thread jusqu'à `resume`. `--analysis-date AAAA-MM-JJ` juge les versions des textes à une autre date que celle du jour.

### Historique et `git blame`

Les commits de reformatage massif (passage à 88 colonnes) sont listés dans `.git-blame-ignore-revs`. GitHub les ignore d'office dans sa vue *blame* ; en local, une commande par clone suffit :

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

## Architecture

Architecture inspirée de l'architecture hexagonale (ports et adaptateurs), dans une version pragmatique :

- `domain/` : règles pures (état, configuration, règles par domaine, politique d'arbitrage, masquage, nettoyage du corpus). Aucun port, aucune bibliothèque externe ;
- `ports/` : interfaces des dépendances externes (LLM, embedding, recherche dans le corpus, journal d'audit) ;
- `application/` : nœuds du graphe, extraction, CRAG, ingestion. Passe par les ports, jamais par un adaptateur ;
- `adapters/` : LangGraph (orchestration, checkpointer), PostgreSQL, Mistral et Anthropic, fastembed ;
- `cli.py` : racine de composition, qui assemble adaptateurs et graphe.

Le sens des dépendances et le confinement de chaque bibliothèque dans son adaptateur sont vérifiés sur les imports (`tests/test_isolation.py`). La conformité de chaque adaptateur et de chaque doublure à son port est vérifiée par `tests/test_ports.py`.

**Écart assumé : le flux vit dans le graphe.** Dans une architecture hexagonale stricte, le déroulé d'une analyse (validation, extraction, analystes, décision, arbitrage humain) serait un service de l'application, et LangGraph un simple exécutant. Ici, routes, fan-out et interruption sont câblés dans l'adaptateur LangGraph : c'est ce qui apporte checkpoints, reprise après interruption et historique par contrat. Les nœuds restent des fonctions pures, testables sans le framework ; changer d'orchestrateur voudrait dire réécrire le câblage, pas le domaine ni l'application. Détails : [docs/adr-002-ports-et-adaptateurs.md](docs/adr-002-ports-et-adaptateurs.md).

## Migrations

Les fichiers `migrations/*.sql` sont appliqués par `docker/initdb/00_migrate.sh`, monté dans `docker-entrypoint-initdb.d`.

- **Ils ne s'exécutent que sur un volume vide**, au tout premier démarrage du conteneur. Une migration ajoutée ensuite n'est pas appliquée à une base existante.
- Le script échoue explicitement si `APP_DB_PASSWORD` est absent ou vide.
- Si l'initialisation échoue, le volume n'est plus vide et l'init ne sera pas rejouée : il faut recréer le volume (`docker compose down -v`, qui **détruit toutes les données** de la base).

Le rôle applicatif `app_role` n'a que `SELECT` et `INSERT` sur `audit_decisions` : le journal d'audit est en ajout seul.

## Corpus et versions des textes

Le corpus (`data/corpus/`) réunit des textes publics (RGPD, Code de commerce, Code civil, Code monétaire et financier) et des fiches rédigées pour le projet. `SOURCES.md` liste chaque source, sa licence, sa date de récupération et la raison de sa présence (règle de périmètre). `uv run python -m cdg.cli ingest` nettoie, découpe et indexe le corpus. La commande est rejouable : elle supprime les extraits disparus et remplace ceux dont une métadonnée a changé (fin de validité, note…).

**Exemple de gestion des versions : C. com., art. L441-10.** Légifrance indique « Version en vigueur du 26 avril 2019 au 01 janvier 2027 ».
- À l'ingestion, cette ligne ne devient pas du texte indexé, mais des métadonnées : `valid_from = 2019-04-26`, `valid_until = 2027-01-01`, avec le texte modificateur.
- À l'analyse, la date d'analyse (jour légal en France, écrit dans l'état du contrat) départage les versions. À partir du 1er janvier 2027, un extrait de L441-10 jugé pertinent par le CRAG n'est plus retenu : il est signalé dans les constats du domaine (« référence expirée à la date d'analyse … »). Si aucune autre référence en vigueur n'étaye un constat qui pénalise ou bloque, le domaine passe à `INSUFFISANT` et le contrat part en revue humaine (`ESCALADE`) : une version expirée ne justifie jamais seule un verdict. Le corpus ne sert qu'à justifier des constats : une clause qui ne déclenche aucune règle n'est pas recherchée.
- La fiche « Délais de paiement entre professionnels », qui paraphrase L441-10, expire à la même date : une fiche prend la plus proche des fins de validité des articles qu'elle cite.
- Pour mettre à jour : récupérer la nouvelle version, reporter la date dans `SOURCES.md`, relancer `ingest`.

## Limites connues

- **Périmètre des règles.** Seuls 10 types de clauses sont évalués : responsabilités de l'acheteur et du fournisseur, révision de prix, pénalités d'exécution dues par le fournisseur, délai de paiement par l'acheteur, durée, préavis, données personnelles, accord de traitement, transfert hors UE. Une clause d'un autre type n'est pas évaluée. Sa détection, signalée comme « clause non couverte par les règles », est prévue en phase 2.
- **Transferts hors UE.** La règle juge la garantie que nomme le contrat, jamais la liste des pays adéquats ni la validité effective de la garantie. Des clauses contractuelles ad hoc sans mention d'autorisation de l'autorité de contrôle donnent une pénalité et un constat « à vérifier », pas un blocage.
- **Dérogations de l'art. 49 du RGPD non couvertes** (consentement explicite, exécution d'un contrat, motifs d'intérêt public…) : l'article n'est pas dans le corpus. Un contrat qui fonde un transfert sur une dérogation est classé « aucune garantie », donc bloqué : erreur dans le sens prudent, à lever par un humain.
- **Renvois non suivis.** Un article est admis s'il sert une règle, s'il est cité directement par un article qui en sert une, ou s'il définit un terme utilisé par une règle (voir `SOURCES.md`). Ne sont donc pas dans le corpus :
  - RGPD, art. 79 (renvoi de second degré, fichier conservé mais non ingéré) ;
  - RGPD, art. 34, 35, 43, 47 à 49, 63 et 93 ;
  - C. com., art. L441-1, L441-3, L441-4, L441-16 et L441-17 ;
  - C. civ., art. 759 ;
  - le règlement (UE) 2019/1150.
- **Fiches de référence.** Ce sont des synthèses rédigées pour le projet, non constitutives d'un avis juridique. Chacune sépare « Ce que dit le texte », des paraphrases fidèles vérifiées mot à mot et sourcées, de « Comment le projet l'applique », les seuils du projet présentés comme des choix de politique d'achat. Les conséquences que seule la jurisprudence tire des textes (plafond et faute lourde, articulation des art. 1171 C. civ. et L442-1 C. com.) sont signalées comme hors corpus.
