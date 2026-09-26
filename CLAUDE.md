# contract-decision-graph

Projet R&D personnel : graphe LangGraph qui rend un verdict go / no-go auditable sur des contrats fournisseurs. La spécification complète est dans `docs/spec-phase1.md` : c'est la source de vérité. En cas de doute, relis-la avant de coder.

## Règles non négociables

- Le verdict est rendu par du code Python pur (`src/cdg/domain/rules/`, `src/cdg/domain/justification.py`, `src/cdg/domain/decision.py`). Aucun LLM ne décide.
- Architecture inspirée de l'architecture hexagonale (`docs/adr-002-ports-et-adaptateurs.md`). Couches : `domain/` (modèles métier et règles pures), `ports/` (interfaces des dépendances externes), `application/` (état du graphe, nœuds, extraction, CRAG, ingestion : orchestre le domaine à travers les ports), `adapters/` ; `cli.py` est la racine de composition. La logique vit dans le domaine ; un nœud ne fait qu'adapter l'état au domaine, puis le résultat à l'état.
- Sens des dépendances, testé (`tests/test_isolation.py`) : `domain/` n'importe ni `ports/`, ni `application/`, ni `adapters/` ; `ports/` n'importe que `domain/` ; `application/` importe `domain/` et `ports/`, jamais `adapters/` ; `adapters/` importe `ports/`, `domain/`, `application/` et `settings`, jamais `cli` ni une autre famille d'adaptateurs.
- Chaque bibliothèque externe n'est importée que dans son adaptateur : langgraph dans `adapters/langgraph/`, psycopg et pgvector dans `adapters/postgres/` (psycopg aussi dans `adapters/langgraph/checkpointer.py`), fastembed dans `adapters/fastembed.py`, mistralai et anthropic dans `adapters/llm/`. Une dépendance externe utilisée par l'application passe par un port (exception validée : l'écriture du corpus, commande d'administration câblée dans la CLI). Chaque adaptateur et chaque doublure respecte la signature de son port (`tests/test_ports.py`).
- Seul `src/cdg/adapters/langgraph/` importe `langgraph`. `orchestrator.py` y contient les adaptateurs : construction des `Send`, appel à `interrupt()` (`human_review`, qui délègue à `domain/policy.py`), câblage. Nœuds, règles, politique et audit sont des fonctions pures qui renvoient des dicts, testables sans le framework.
- Un nœud ne renvoie que les clés d'état qu'il modifie.
- Un seul mécanisme de routage : chaque nœud à plusieurs sorties écrit `route` dans l'état, les arêtes conditionnelles ne font que la lire (pour `analysts`, l'arête construit les 4 `Send`). Jamais de `Command(goto=...)` ; `Command` ne sert qu'à `Command(resume=...)`.
- Aucun effet de bord (écriture en base, notification) avant un appel à `interrupt()`.
- Le texte d'un contrat est une donnée non fiable : toujours délimité dans les prompts, jamais traité comme une instruction.
- Tout ce qui se règle (poids, seuils, marge, budget, essais d'extraction, seuils et pénalités des règles, politique d'arbitrage) vit dans `config/decision.yaml`. Jamais en dur dans le code, jamais dans un prompt. La configuration est validée par un modèle Pydantic au démarrage : invalide, le programme s'arrête.
- Identifiants uniquement dans `.env` (jamais commité) ; `.env.example` est la référence commitée.
- Données uniquement synthétiques ou publiques. Aucun contrat réel, aucun nom de client.
- Pas de repli silencieux : tout échec produit un `failure_report` structuré.
- En cas de concurrence entre règles, l'issue la plus conservatrice l'emporte.
- Tout flottant comparé à un seuil ou sérialisé passe par la fonction d'arrondi unique de `src/cdg/domain/numeric.py`.

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
- `uv run pytest --cov` : tests avec couverture (lignes et branches) ; échoue sous le seuil `fail_under` de `pyproject.toml`
- `uv run pytest --llm -m llm` : tests avec le vrai modèle (payants, 5 réussites sur 5, résultat consigné au journal)
- `uv run ruff format` : formate le code (line-length 88, valeur par défaut de Ruff et de Black) ; un reformatage massif va dans son propre commit, ajouté à `.git-blame-ignore-revs`
- `uv run ruff check` : lint (jeu de règles par défaut de ruff 0.16) ; doit passer avant chaque commit
- `uv run mypy` : vérification des types (strict sur `domain/`, `ports/`, `application/`) ; doit passer avant chaque commit
- `uv run python -m cdg.cli <commande>` : CLI (run, resume, history, expire, verify)

## Intégration continue

`.github/workflows/ci.yml`, sur chaque push vers `main` et sur chaque pull request, plus un audit hebdomadaire. Permissions minimales (`contents: read`) ; actions épinglées par empreinte de commit, version en commentaire ; uv 0.6.10 avec cache, installation stricte depuis `uv.lock` (`uv sync --locked`).
- **Job `lint`** : `ruff format --check` et `ruff check`, avec le seul groupe `dev` installé.
- **Job `types`** : mypy, configuré dans `pyproject.toml` : strict sur `domain/`, `ports/` et `application/`, mode de base sur `adapters/` et `cli.py`, plugin pydantic. Tout le projet est installé : mypy lit les types des bibliothèques.
- **Job `audit`** : pip-audit (PyPA), installé depuis le groupe `audit` de `uv.lock`, audite toutes les dépendances de `uv.lock`, groupes compris, exportées avec leurs empreintes par `uv export` : pip-audit 2.10 ne lit pas `uv.lock`. Aucune résolution de dépendances (`--require-hashes`, `--disable-pip`). Le job échoue sur toute faille connue (base de PyPI) et sur tout paquet introuvable (`--strict`). Il tourne aussi chaque lundi à 7 h 17 (heure de Paris) sur `main`, seul job de ce déclenchement planifié : une faille publiée entre deux commits est vue sans attendre le suivant. GitHub désactive un déclenchement planifié après 60 jours sans activité sur un dépôt public.
- **Job `tests`** : service PostgreSQL avec l'image de `docker-compose.yml`, figée par la même empreinte ; migrations par `docker/initdb/00_migrate.sh`, exécuté dans le conteneur (un conteneur de service démarre avant le checkout et ne peut pas monter le script) ; `setup-db` ; puis toute la suite, tests `pg` compris, avec la couverture (`pytest --cov`, lignes et branches) : le job échoue sous le seuil `fail_under` de `pyproject.toml` (96 %, pour 96,81 % mesurés le 26/09). Le badge de couverture du README est statique et affiche ce seuil ; `tests/test_couverture.py` échoue s'il en diverge.
- **Dependabot** (`.github/dependabot.yml`) : chaque lundi à 6 h (heure de Paris), pull requests de mise à jour des dépendances Python (`uv.lock`) et des actions GitHub (empreintes et commentaires de version). Chacune passe par la CI.
- **Image PostgreSQL + pgvector** : hors de Dependabot, sa mise à jour reste manuelle et délibérée. `tests/test_ci.py` échoue si elle n'est pas figée par empreinte, ou si l'empreinte diffère entre `docker-compose.yml` et le workflow.
- **Tests `llm` exclus** : ils sont payants, exigent une clé d'API alors que la CI n'a aucun secret, et dépendent d'un service externe (quotas, disponibilité, modèle). Leur échec ne dirait rien du code. On les lance à la main, et chaque série est consignée au journal.
- **Aucun téléchargement du modèle d'embedding** : les tests utilisent des doublures, et `HF_HUB_OFFLINE=1` ferait échouer tout téléchargement.
- **Pas de `.env`** : la CI ne définit que les variables de la base jetable. Un test qui dépend en silence de l'environnement du poste y échoue : on corrige le test, on ne l'exclut pas.

## Stack

Python 3.12, uv, langgraph, langgraph-checkpoint-postgres, langchain-core, pydantic v2, pyyaml, python-dotenv, psycopg, pgvector, mistralai, anthropic, fastembed, pytest, pytest-cov, ruff, mypy et types-PyYAML (dev), pip-audit (groupe audit), Docker Compose.
