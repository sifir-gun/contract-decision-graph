# ADR-002 : ports et adaptateurs

- **Statut** : accepté, le 25/09/2026 (J3, avant la tâche 9).
- **Portée** : organisation de `src/cdg/` et règles de dépendance entre modules.

## Contexte

Jusqu'au J2, une seule dépendance externe était isolée : LangGraph, derrière la règle « seul `orchestrator.py` importe `langgraph` ». Le J3 en ajoute trois (fournisseur LLM, modèle d'embedding local, recherche pgvector) et le J4 une quatrième (journal d'audit). Avant cette décision :

- psycopg était importé par `settings.py` et `rag_store.py`, fastembed par `embeddings.py`, les SDK LLM par `providers/` ;
- les modules de logique et les adaptateurs voisinaient à plat dans `src/cdg/`, sans règle vérifiable entre eux ;
- le CRAG (tâche 9) allait être écrit : il devait dépendre d'une interface dès sa création, pas de pgvector.

## Décision

Architecture **inspirée de l'architecture hexagonale** (ports et adaptateurs), dans une version pragmatique. Quatre couches, plus une racine de composition :

| Couche | Contenu | Peut importer |
| --- | --- | --- |
| `domain/` | état, configuration, règles par domaine, politique d'arbitrage, expiration, masquage, partie pure du corpus (nettoyage, découpage, fiches), audit (J4) | rien d'autre que lui-même |
| `ports/` | interfaces des dépendances externes et types échangés | `domain/` |
| `application/` | nœuds du graphe, extraction, CRAG, ingestion, dépendances injectées (`Extractor`, `Crag`, `Deps`) | `domain/`, `ports/` |
| `adapters/` | `langgraph/` (orchestrateur, checkpointer), `postgres/`, `llm/` (Mistral, Anthropic), `fastembed.py` | `domain/`, `ports/`, `application/`, `settings` ; jamais `cli`, ni une autre famille d'adaptateurs |
| `cli.py` | racine de composition : lit `.env` et la configuration, instancie les adaptateurs, lance le graphe | tout |

Les quatre ports :

- `LLMProvider.structured(*, tier, system, user, schema, node) -> (schema, Usage)` : sortie structurée validée par Pydantic, consommation mesurée. Adaptateurs : Mistral, Anthropic.
- `Embedder` (`model`, `dimension`, `embed_passages`, `embed_query`). Adaptateur : fastembed, qui ajoute les préfixes propres au modèle.
- `Retriever.search(domain, query, *, k) -> list[Passage]`. La requête est un **texte** : l'adaptateur PostgreSQL calcule le vecteur et filtre sur son propre modèle d'embedding. Le CRAG ne manipule jamais de vecteur et ne peut pas mélanger deux modèles.
- `AuditStore` (implémenté au J4) : `append(seal)` lit la tête de chaîne, appelle `seal(tête)` et insère, dans une même transaction sous verrou ; `entries()` rend le journal pour `verify`. Le chaînage reste correct même si deux contrats sont scellés en même temps, et le calcul des empreintes reste dans le domaine. Le journal est en ajout seul.

`Extractor` et `Crag` ne sont pas des ports : ce sont des services de l'application, injectés dans les nœuds (doublures en test), eux-mêmes clients des ports.

Chaque bibliothèque externe n'est importée que dans son adaptateur : langgraph dans `adapters/langgraph/`, psycopg et pgvector dans `adapters/postgres/`, fastembed dans `adapters/fastembed.py`, mistralai dans `adapters/llm/mistral.py`, anthropic dans `adapters/llm/anthropic.py`.

Vérification :
- `tests/test_isolation.py` analyse les imports de chaque fichier, y compris les imports locaux dans une fonction, pour le confinement des bibliothèques et le sens des dépendances. Chaque vérification est aussi testée sur une arborescence fictive, pour prouver qu'elle détecte une violation ;
- `tests/test_ports.py` vérifie que chaque adaptateur et chaque doublure expose les attributs et méthodes de son port, avec la même signature. Un `Protocol` n'est pas contrôlé à l'exécution, et le projet n'a pas de vérificateur de types.

## Écart assumé : le flux vit dans le graphe

Dans une architecture hexagonale stricte, le déroulé d'une analyse (validation, extraction, analystes, décision, arbitrage humain) serait un service de l'application, et LangGraph un simple exécutant derrière un port. Ici, routes, fan-out et interruption sont câblés dans l'adaptateur LangGraph, ainsi que les cas d'usage liés à un thread (`run_contract`, `resume_thread`, `thread_history`, `expire_threads`).

C'est un choix, pas un oubli : LangGraph apporte les checkpoints par étape, la reprise après interruption, l'`interrupt()` natif et l'historique par contrat, que ce projet veut montrer. Les en extraire reviendrait à les réécrire. Ce qui est préservé :

- les nœuds restent des fonctions pures, testables sans le framework ;
- la décision reste dans le domaine (règles) et dans `decision_gate`, jamais dans le câblage ;
- changer d'orchestrateur voudrait dire réécrire le câblage, pas le domaine ni l'application.

## Autres choix

- **Pas de port pour l'écriture du corpus.** L'ingestion est une commande d'administration : la CLI appelle directement l'adaptateur PostgreSQL. Un port s'ajouterait si un second magasin apparaissait.
- **psycopg est aussi permis dans `adapters/langgraph/checkpointer.py`.** `PostgresSaver` exige une connexion psycopg : le checkpointer est l'adaptateur de LangGraph sur PostgreSQL.

## Alternatives écartées

- **Sortir le flux du graphe** dans un service de l'application : perdrait les checkpoints par étape et l'interruption native, ou obligerait à les réécrire. Hors périmètre de la phase 1.
- **Une seule couche intérieure**, avec nœuds et CRAG dans `domain/` : proposée, puis écartée par le propriétaire du repo au profit d'une couche `application/` distincte, pour que `domain/` reste sans port.

## Conséquences

- Les règles d'architecture sont vérifiées à chaque exécution de `pytest`, et plus seulement écrites.
- Le CRAG s'écrit contre le port `Retriever` : il se teste avec une doublure, sans base.
- Changer de fournisseur LLM, de modèle d'embedding ou de magasin vectoriel revient à écrire un adaptateur.
- En contrepartie, un niveau de paquets de plus et des imports plus longs. La conformité aux ports est vérifiée par signature, pas par un vérificateur de types.
