# Journal de bord

Tenu à jour à chaque commit : ce qui a été fait, et surtout les pièges découverts. Il servira de matière pour le README et l'ADR. Chaque entrée porte le titre de son commit, puisque l'empreinte d'un commit ne peut pas figurer dans son propre contenu.

## 2026-09-23 · Cadrage avant le J1

### Spec : intégration des décisions prises avant le J1

**Fait.** Treize décisions reportées dans la spec et dans CLAUDE.md :
- nœuds purs, adaptateurs LangGraph dans `orchestrator.py` ;
- routage unique par `route` ;
- seuils et poids de décision, ordre de `decision_gate` ;
- `Clause.present` et règles par domaine ;
- configuration validée par Pydantic ;
- migration `001` et `app_role`.

**Pièges.**
- La première version de la spec contredisait CLAUDE.md sur deux points :
  - des nœuds renvoyaient `Command` et `Send`, donc importaient `langgraph` ;
  - le routage passait par `Command(goto=...)` alors que la règle impose `route` dans l'état.

  Tranché ainsi : les nœuds renvoient des dicts, et l'orchestrateur traduit.

### Spec : ordre conservateur du gate, pénalités, arrondi, rejet scellé

**Fait.**
- Ordre final de `decision_gate` : blocage dur, budget, `INSUFFISANT`, conflit, seuils. L'issue la plus conservatrice l'emporte.
- Pénalités de score fixées.
- Arrondi unique à 6 décimales.
- `reject` scellé via `audit_seal`.
- Nombre d'essais d'extraction placé dans la config.

**Pièges.**
- Avec ces règles, la conformité n'a que des blocages, donc son score reste à 1,0. Un `NO_GO` par seuil est alors inatteignable : tout score assez bas déclenche d'abord le conflit. C'est assumé comme choix de conception : `NO_GO` n'est rendu que sur blocage dur, avec une raison nommée. **À reprendre dans l'ADR.**
- Sans arrondi, un score calculé à 0,7499999… tomberait en `GO_RESERVES` au lieu de `GO`.

## 2026-09-23 · J1, tâche 0 : socle

### Socle base de données : Compose PostgreSQL 16 + pgvector, migration 001

**Fait.**
- Compose PostgreSQL + pgvector, migration `001` (extension `vector`, `audit_decisions`, `app_role`).
- Script d'init `psql -v`, `.env.example`.
- Droits vérifiés sur le conteneur :
  - `app_role` : INSERT et SELECT acceptés ;
  - UPDATE, DELETE, TRUNCATE et CREATE refusés ;
  - un `id` explicite est rejeté.

**Pièges.**
- **`BIGSERIAL` et `GRANT SELECT, INSERT`** : un INSERT exige aussi `USAGE` sur la séquence. `BIGINT GENERATED ALWAYS AS IDENTITY` n'en a pas besoin (vérifié), et interdit en plus d'imposer un `id`.
- **`docker-entrypoint-initdb.d` ne tourne que sur un volume vide.** Si l'init échoue, le volume n'est plus vide et l'init ne sera jamais rejouée : il faut `docker compose down -v`.
- **Un `.sql` d'init ne lit pas les variables d'environnement.** Un script shell passe le mot de passe par `psql -v app_password=...`, et le SQL utilise `:'app_password'`. Le script échoue explicitement si la variable est vide ; le conteneur sort alors en code 1 (vérifié).
- **Réseau** :
  - pas d'accès sortant depuis la session de l'agent ;
  - proxy auto-configuré `wpad` introuvable ;
  - `pgvector/pgvector:pg17` impossible à télécharger.

  On a gardé pg16, déjà présent en local, figé par empreinte `sha256` : c'est vérifiable hors ligne et reproductible.

### Socle Python : projet uv, dépendances, test d'isolation LangGraph

**Fait.**
- Projet uv avec le paquet `cdg` (hatchling).
- Dépendances installées par le propriétaire du repo depuis son terminal.
- Test d'architecture par analyse AST : seul `orchestrator.py` importe `langgraph`.

**Pièges.**
- **`.python-version` et pyenv** : `uv` est lancé via un shim pyenv. Avec `.python-version = 3.12`, que pyenv ne connaît pas, toute commande `uv` échoue avant même de démarrer. Fichier supprimé : `requires-python` suffit.
- Vérification de l'API LangGraph 1.2.12, par lecture du code installé et par une sonde jetable :
  - **`functools.partial` et `input_schema`** : `add_node` ne déduit le schéma d'entrée d'un nœud à partir des annotations que pour une vraie fonction ou méthode (`isfunction` / `ismethod`). Avec un `partial`, il retombe sans prévenir sur le schéma d'état du graphe. Le contenu d'un `Send` arrive quand même tel quel au nœud, mais on passe `input_schema=AnalystInput` explicitement pour ne pas en dépendre.
  - Une arête conditionnelle peut renvoyer une liste de `Send`. Les chaînes passent par `path_map`, les `Send` passent tels quels. Un `Send` vers `END` est refusé.
  - Un nœud peut renvoyer `{}` ou `None`.
  - Après un fan-out par `Send`, le nœud de fan-in s'exécute une seule fois, au superstep suivant.
- **Sérialiseur des checkpoints** (`langgraph-checkpoint` 4.2.0) : `JsonPlusSerializer` est permissif par défaut, il désérialise tout type avec un simple avertissement. Un accès en écriture à la base des checkpoints permettrait une exécution de code. Il se verrouille par `LANGGRAPH_STRICT_MSGPACK=true` ou `allowed_msgpack_modules`. Reporté dans la spec au J2.
- **`interrupt()`** (documentation seulement, à tester au J2) : le nœud est réexécuté depuis son début. Plusieurs `interrupt()` dans un même nœud sont appariés par ordre d'appel, tâche par tâche.

### Docs : journal de bord, verrouillage du sérialiseur au J2

**Fait.**
- Création de ce journal.
- Verrouillage du sérialiseur des checkpoints inscrit dans la spec (points à maîtriser et ligne J2).
