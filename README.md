# contract-decision-graph

Graphe LangGraph qui rend un verdict go / no-go auditable sur des contrats fournisseurs. Le verdict est rendu par du code déterministe ; les LLM se limitent à l'extraction et à l'explication. Spécification : [docs/spec-phase1.md](docs/spec-phase1.md).

Données uniquement synthétiques ou publiques.

## Démarrage

```bash
cp .env.example .env        # puis remplacer chaque valeur
docker compose up -d        # PostgreSQL 16.11 + pgvector 0.8.1
uv sync
uv run pytest
```

## Migrations

Les fichiers `migrations/*.sql` sont appliqués par `docker/initdb/00_migrate.sh`, monté dans `docker-entrypoint-initdb.d`.

- **Ils ne s'exécutent que sur un volume vide**, au tout premier démarrage du conteneur. Une migration ajoutée ensuite n'est pas appliquée à une base existante.
- Le script échoue explicitement si `APP_DB_PASSWORD` est absent ou vide.
- Si l'initialisation échoue, le volume n'est plus vide et l'init ne sera pas rejouée : il faut recréer le volume (`docker compose down -v`, qui **détruit toutes les données** de la base).

Le rôle applicatif `app_role` n'a que `SELECT` et `INSERT` sur `audit_decisions` : le journal d'audit est en ajout seul.
