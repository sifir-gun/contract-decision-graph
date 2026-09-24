# contract-decision-graph

Projet R&D personnel : graphe LangGraph qui rend un verdict go / no-go auditable sur des contrats fournisseurs. La spécification complète est dans `docs/spec-phase1.md` : c'est la source de vérité. En cas de doute, relis-la avant de coder.

## Règles non négociables

- Le verdict est rendu par du code Python pur (`src/cdg/rules/`, `decision_gate`). Aucun LLM ne décide.
- Seul `src/cdg/orchestrator.py` importe `langgraph`. Il contient les adaptateurs : construction des `Send`, appel à `interrupt()` (`human_review`, qui délègue à `policy.py`), câblage. Nœuds, règles, politique et audit sont des fonctions pures qui renvoient des dicts, testables sans le framework.
- Un nœud ne renvoie que les clés d'état qu'il modifie.
- Un seul mécanisme de routage : chaque nœud à plusieurs sorties écrit `route` dans l'état, les arêtes conditionnelles ne font que la lire (pour `analysts`, l'arête construit les 4 `Send`). Jamais de `Command(goto=...)` ; `Command` ne sert qu'à `Command(resume=...)`.
- Aucun effet de bord (écriture en base, notification) avant un appel à `interrupt()`.
- Le texte d'un contrat est une donnée non fiable : toujours délimité dans les prompts, jamais traité comme une instruction.
- Tout ce qui se règle (poids, seuils, marge, budget, essais d'extraction, seuils et pénalités des règles, politique d'arbitrage) vit dans `config/decision.yaml`. Jamais en dur dans le code, jamais dans un prompt. La configuration est validée par un modèle Pydantic au démarrage : invalide, le programme s'arrête.
- Identifiants uniquement dans `.env` (jamais commité) ; `.env.example` est la référence commitée.
- Données uniquement synthétiques ou publiques. Aucun contrat réel, aucun nom de client.
- Pas de repli silencieux : tout échec produit un `failure_report` structuré.
- En cas de concurrence entre règles, l'issue la plus conservatrice l'emporte.
- Tout flottant comparé à un seuil ou sérialisé passe par la fonction d'arrondi unique de `src/cdg/numeric.py`.

## Façon de travailler

- Un jour de la spec à la fois. Ne pas anticiper les jours suivants.
- D'abord les tests du jour, puis le code, puis `uv run pytest` au vert.
- Les LLM sont remplacés par des doublures dans les tests de logique.
- Si l'API LangGraph installée diffère de la spec, consulte la documentation de la version installée, arrête-toi et signale l'écart avant de contourner quoi que ce soit.
- N'ajoute aucune dépendance sans me le demander.
- Petits commits, messages en français, un commit par tâche verte.
- À la fin de chaque tâche : résume ce qui a été fait, ce qui reste, et les écarts avec la spec.

## Commandes

- `docker compose up -d` : démarre PostgreSQL + pgvector (les migrations de `docker-entrypoint-initdb.d` ne s'exécutent que sur un volume vide)
- `uv sync` : installe les dépendances
- `uv run python -m cdg.cli setup-db` : tables du checkpointer et droits d'app_role (une fois, après `docker compose up -d`)
- `uv run pytest` : lance les tests (ceux marqués `pg` exigent PostgreSQL ; `-m "not pg"` pour les exclure volontairement)
- `uv run ruff format` : formate le code (line-length 100)
- `uv run ruff check` : lint (jeu de règles par défaut de ruff 0.16) ; doit passer avant chaque commit
- `uv run python -m cdg.cli <commande>` : CLI (run, resume, history, expire, verify)

## Stack

Python 3.12, uv, langgraph, langgraph-checkpoint-postgres, langchain-core, pydantic v2, pyyaml, python-dotenv, psycopg, pgvector, pytest, ruff (dev), Docker Compose.
