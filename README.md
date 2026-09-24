# contract-decision-graph

Graphe LangGraph qui rend un verdict go / no-go auditable sur des contrats fournisseurs. Le verdict est rendu par du code déterministe ; les LLM se limitent à l'extraction et à l'explication. Spécification : [docs/spec-phase1.md](docs/spec-phase1.md).

Données uniquement synthétiques ou publiques.

## Démarrage

```bash
cp .env.example .env        # puis remplacer chaque valeur
docker compose up -d        # PostgreSQL 16.11 + pgvector 0.8.1
uv sync
uv run python -m cdg.cli setup-db   # une fois : tables du checkpointer, droits d'app_role
uv run pytest                       # -m "not pg" pour exclure volontairement les tests PostgreSQL
```

## Migrations

Les fichiers `migrations/*.sql` sont appliqués par `docker/initdb/00_migrate.sh`, monté dans `docker-entrypoint-initdb.d`.

- **Ils ne s'exécutent que sur un volume vide**, au tout premier démarrage du conteneur. Une migration ajoutée ensuite n'est pas appliquée à une base existante.
- Le script échoue explicitement si `APP_DB_PASSWORD` est absent ou vide.
- Si l'initialisation échoue, le volume n'est plus vide et l'init ne sera pas rejouée : il faut recréer le volume (`docker compose down -v`, qui **détruit toutes les données** de la base).

Le rôle applicatif `app_role` n'a que `SELECT` et `INSERT` sur `audit_decisions` : le journal d'audit est en ajout seul.

## Corpus et versions des textes

Le corpus (`data/corpus/`) réunit des textes publics (RGPD, Code de commerce, Code civil, Code monétaire et financier) et des fiches rédigées pour le projet. `SOURCES.md` liste chaque source, sa licence, sa date de récupération et la raison de sa présence (règle de périmètre). `uv run python -m cdg.cli ingest` nettoie, découpe et indexe le corpus. La commande est rejouable, et supprime les extraits disparus.

**Exemple de gestion des versions : C. com., art. L441-10.** Légifrance indique « Version en vigueur du 26 avril 2019 au 01 janvier 2027 ».
- À l'ingestion, cette ligne ne devient pas du texte indexé, mais des métadonnées : `valid_from = 2019-04-26`, `valid_until = 2027-01-01`, avec le texte modificateur.
- À l'analyse, une référence dont la version a expiré à la date d'analyse est signalée dans les constats, et ne peut pas justifier seule un verdict.
- Pour mettre à jour : récupérer la nouvelle version, reporter la date dans `SOURCES.md`, relancer `ingest`.

## Limites connues

- **Périmètre des règles.** Seuls 9 types de clauses sont évalués : responsabilités, révision de prix, pénalités de retard, durée, préavis, données personnelles, accord de traitement, transfert hors UE. Une clause d'un autre type n'est pas évaluée. Sa détection, signalée comme « clause non couverte par les règles », est prévue en phase 2.
- **Transferts hors UE.** La règle juge la garantie que nomme le contrat, jamais la liste des pays adéquats ni la validité effective de la garantie.
- **Renvois non suivis.** Un article est admis s'il sert une règle, ou s'il est cité directement par un article qui en sert une (voir `SOURCES.md`). Ne sont donc pas dans le corpus :
  - RGPD, art. 79 (renvoi de second degré, fichier conservé mais non ingéré) ;
  - RGPD, art. 34, 35, 43, 47 à 49, 63 et 93 ;
  - C. com., art. L441-1, L441-3, L441-4, L441-16 et L441-17 ;
  - C. civ., art. 759 ;
  - le règlement (UE) 2019/1150.
- **Fiches de référence.** Ce sont des synthèses rédigées pour le projet, non constitutives d'un avis juridique ; chaque affirmation cite l'article qu'elle paraphrase.
