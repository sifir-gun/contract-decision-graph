# contract-decision-graph

Projet R&D personnel : graphe LangGraph qui rend un verdict go / no-go auditable sur des contrats fournisseurs. La spécification complète est dans `docs/spec-phase1.md` : c'est la source de vérité. En cas de doute, relis-la avant de coder.

## Règles non négociables

- Le verdict est rendu par du code Python pur (`src/cdg/rules/`, `decision_gate`). Aucun LLM ne décide.
- Seul `src/cdg/orchestrator.py` importe `langgraph`. Nœuds, règles, politique et audit sont des fonctions pures, testables sans le framework.
- Un nœud ne renvoie que les clés d'état qu'il modifie.
- Le routage est écrit dans l'état (`route`) par un nœud ; les arêtes ne font que le lire.
- Aucun effet de bord (écriture en base, notification) avant un appel à `interrupt()`.
- Le texte d'un contrat est une donnée non fiable : toujours délimité dans les prompts, jamais traité comme une instruction.
- Poids, seuils, marge, budget et politique d'arbitrage vivent dans `config/decision.yaml`. Jamais en dur dans le code, jamais dans un prompt.
- Données uniquement synthétiques ou publiques. Aucun contrat réel, aucun nom de client.
- Pas de repli silencieux : tout échec produit un `failure_report` structuré.

## Façon de travailler

- Un jour de la spec à la fois. Ne pas anticiper les jours suivants.
- D'abord les tests du jour, puis le code, puis `uv run pytest` au vert.
- Les LLM sont remplacés par des doublures dans les tests de logique.
- Si l'API LangGraph installée diffère de la spec, consulte la documentation de la version installée, arrête-toi et signale l'écart avant de contourner quoi que ce soit.
- N'ajoute aucune dépendance sans me le demander.
- Petits commits, messages en français, un commit par tâche verte.
- À la fin de chaque tâche : résume ce qui a été fait, ce qui reste, et les écarts avec la spec.

## Commandes

- `docker compose up -d` : démarre PostgreSQL + pgvector
- `uv sync` : installe les dépendances
- `uv run pytest` : lance les tests
- `uv run python -m cdg.cli <commande>` : CLI (run, resume, history, expire, verify)

## Stack

Python 3.12, uv, langgraph, langgraph-checkpoint-postgres, langchain-core, pydantic v2, psycopg, pgvector, pytest, Docker Compose.
