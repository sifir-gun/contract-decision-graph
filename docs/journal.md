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

## 2026-09-23 · J1, tâches 1 à 5

### J1 tâche 1 : schémas d'état et modèles métier

**Fait.** `src/cdg/state.py` :
- types `Domain`, `Decision`, `Route`, `RetrievalStatus` ;
- constantes `DOMAINS` et `REQUIRED_KINDS` ;
- modèles `Clause`, `AgentVerdict`, `HumanDecision`, `Usage` ;
- `ContractState` et `AnalystInput`.

Invariants validés par Pydantic :
- clause absente : citation vide ; clause présente : citation non vide ;
- score entre 0 et 1 ;
- tokens et latence positifs ou nuls.

Un test garantit que seuls `verdicts` et `usage` portent un réducteur.

**Pièges.**
- En Pydantic v2, un champ `float | None` **sans valeur par défaut** reste obligatoire. `Clause.value` doit donc toujours être fourni, même à `None`. C'est voulu : l'extracteur doit se prononcer explicitement.

### J1 tâche 2 : arrondi unique et configuration validée

**Fait.**
- `src/cdg/numeric.py` : `rounded` arrondit à 6 décimales, normalise `-0.0` et refuse NaN et l'infini.
- `config/decision.yaml` : poids, seuils, `min_margin`, `conflict_gap`, budget, essais d'extraction, seuils et pénalités des règles.
- `src/cdg/config.py` : modèle Pydantic `DecisionConfig`, strict, immuable, clés inconnues refusées, avec ces contrôles :
  - somme des poids égale à 1 ;
  - `go_reserves` strictement sous `go` ;
  - pénalités entre 0 et 1.
- `load_config` lève `ConfigError` si le fichier est absent, illisible ou invalide.

**Pièges.**
- **Mode strict de Pydantic** : sans lui, `max_tokens_per_contract: true` passe pour `1`, et `"60000"` passe pour un entier. Le mode strict refuse ces conversions silencieuses. Il accepte en revanche un entier YAML (`100`) pour un champ `float`, ce qui nous arrange.
- **Somme des poids** : elle est comparée à 1 après `rounded`. Les flottants rendraient fragile une égalité stricte.
- **`-0.0`** : `round(-1e-7, 6)` vaut `-0.0`, que `json.dumps` écrit `"-0.0"`. Sans normalisation, deux valeurs égales produiraient deux empreintes différentes au J4.

### J1 tâche 3 : règles par domaine

**Fait.**
- `src/cdg/rules/` : une fonction pure par domaine, plus le registre `RULES`.
- Signature : `(clauses, retrieval_status, config) -> AgentVerdict`.
- Score : 1,0 moins les pénalités déclenchées, borné à [0, 1].
- Un blocage dur ne touche pas le score.
- Un statut `INSUFFISANT` ajoute un constat au domaine.
- Chaque comparaison à un seuil passe par `rounded`.
- `tests/doubles.py` : fabrique de 8 clauses synthétiques favorables, surchargeables une par une.

**Pièges et choix.**
- **Clause attendue manquante ou en double** : la règle lève une `ValueError` au lieu de la traiter comme absente. Il n'y a pas de repli silencieux ; `verify_extraction` (J3) garantit la complétude en amont.
- **`value = None` n'a pas le même sens selon la clause.** Il vaut « illimitée » ou « non plafonnée » pour les responsabilités, la révision de prix et les pénalités, comme l'indique la spec. Pour la durée d'engagement et le préavis, la spec ne dit rien. **Choix non tranché par la spec** : une clause présente mais non chiffrée est pénalisée par prudence, avec un constat explicite. À valider.
- **Arrondi** : un plafond fournisseur de 99,9999999 % est lu 100 % après arrondi, donc sans pénalité. Le test le fixe.

### J1 tâche 4 : decision_gate

**Fait.** `src/cdg/nodes/decision_gate.py` : `aggregate`, `conflict`, `total_tokens` et le nœud `decision_gate`, tous en Python pur.
- Ordre : blocage dur, budget, `INSUFFISANT`, conflit, seuils puis marge.
- Un blocage dur donne `NO_GO` et part vers `explain`, et le `failure_report` budget reste renseigné si le plafond est dépassé.
- `final_decision` n'est écrite que sur la route `explain`.

Tests :
- critère d'acceptation n° 2, décliné sur chacun des 4 domaines ;
- scénarios de contrôle de la spec ;
- bornes de budget, de conflit et de marge ;
- seuil `NO_GO` atteint avec une autre configuration (`conflict_gap = 1`).

**Pièges.**
- **Bruit flottant réel.** Les scores (0,41 ; 0,47 ; 0,47 ; 0,71) donnent une somme brute de `0.49999999999999994`. Sans l'arrondi, ce serait `NO_GO` au lieu de `GO_RESERVES`. Le test le fixe. Ma première tentative de test ne montrait rien : les combinaisons « rondes » (0,75 ; 0,79 ; 0,80) tombent juste en binaire, et il a fallu chercher un cas qui déraille vraiment.
- **Ordre de sommation.** Les verdicts arrivent par le réducteur dans l'ordre d'exécution des branches parallèles. La somme pondérée se fait donc dans l'ordre fixe de `DOMAINS`, pour ne pas dépendre de cet ordre (testé).
- **Verdict manquant ou en double** : `aggregate` lève une `ValueError`. Un doublon pourrait venir d'une branche rejouée. Là encore, pas de repli silencieux.
- **Test mal calculé.** Une première version de test visait 0,749999 mais avait oublié un domaine. En la corrigeant, l'écart 0,500003 déclenchait le conflit. Il faut vérifier l'arithmétique de chaque scénario, conflit compris.

### J1 tâche 5 : nœuds bouchonnés, analyste, orchestrateur, graphe compilé

**Fait.**
- `src/cdg/deps.py` : contrats `Extractor` et `Retriever` (CRAG), résultats Pydantic, `Deps`.
- Nœuds purs dans `src/cdg/nodes/`.
  - `validate_input` minimal : texte vide → `reject` (version complète au J3).
  - `extract_clauses` : délègue à l'extracteur injecté et incrémente les essais.
  - `verify_extraction` : bouchon qui renvoie la route `analysts` (J3).
  - `analyst` : CRAG injecté, puis `RULES[domain]`, puis ajout des références ; ne renvoie que `verdicts` et `usage`.
  - `explain`, `audit_seal`, `reject` : bouchons (J4).
- `src/cdg/orchestrator.py` : `read_route`, `route_after_verify` (les 4 `Send`), `human_review` en passe-plat (J2) et `build_graph(config, deps)`. `reject` mène à `audit_seal`.
- Tests sur le graphe compilé :
  - critère n° 1 : 4 verdicts distincts, chacun avec la référence de son domaine ; un seul `decision_gate`, après les 4 analystes ;
  - critère n° 2 de bout en bout ;
  - routes marge faible, `INSUFFISANT` et budget ;
  - rejet scellé ;
  - arêtes du graphe identiques à la spec.

**Pièges.**
- **Paramètre `config` d'un nœud.** LangGraph réserve les noms `config`, `writer`, `store`, `runtime`, `previous` et `error` pour ce qu'il injecte. Un nœud `decision_gate(state, config: DecisionConfig)` déclenche un `UserWarning` : `config` doit être typé `RunnableConfig`. Aujourd'hui, une annotation d'un autre type n'est pas injectée, et une valeur liée par `partial` resterait prioritaire. La version installée annonce toutefois un typage plus strict. Le paramètre s'appelle donc `decision_config` dans les nœuds. Les règles, qui ne sont pas des nœuds, gardent `config`.
- **Canaux à réducteur initialisés à `[]`.** Après un rejet, `verdicts` et `usage` valent `[]` dans l'état final, même si aucun nœud ne les a écrits. Au J4, l'audit ne pourra donc pas distinguer « absent » de « vide » sur ces clés ; il faudra s'appuyer sur `route` et `reject_reason`.
- **`input_schema` explicite pour `analyst`**, à cause du `partial` (voir la tâche 0). Le test `route_after_verify` vérifie qu'un `Send` ne porte que `domain` et `clauses`.
- **Doublures et parallélisme.** Les branches `Send` synchrones tournent dans un pool de threads. Les doublures n'enregistrent leurs appels que par `list.append`, qui est atomique en CPython. Les tests comparent ensuite des listes triées, jamais un ordre d'appel.

## 2026-09-23 · Fin du J1

### Docs : décisions de fin de J1, CRAG, error_handler, télémétrie

**Fait.**
- Spec :
  - durée d'engagement et préavis non chiffrés pénalisés par prudence, avec constat (validé) ;
  - CRAG en fonctions pures dans `crag.py`, sous-graphe compilé dans `orchestrator.py` au J3 ; la règle d'isolation ne change pas ;
  - étude de `error_handler` au J2 pour les `failure_report` d'échec de nœud.
- `.env.example` : `LANGSMITH_TRACING=false` explicite.

**Pour le README.**
- **Aucune télémétrie externe par défaut.** `langsmith` arrive comme dépendance de `langchain-core` et se charge même comme plugin de pytest. Il n'envoie rien tant que `LANGSMITH_TRACING` n'est pas activé.
- **Limite.** Python ne charge pas `.env` tout seul : seul Docker Compose le lit. La variable ne protège donc l'application qu'une fois exportée ou chargée par la CLI (J2). Le traçage reste désactivé quand elle est absente.

**Pièges.**
- **Deux agents sur la même branche.** Un agent Cursor a commité la tâche 1 du J2 (`99e5d3b`) sur `phase1-j1` pendant la clôture du J1, et continuait à modifier le même dossier de travail. Pour que la PR ne porte que le J1, la livraison part d'une branche dédiée, `phase1-j1-livraison`, créée à partir de `9549040` dans un dossier de travail séparé. Le travail du J2 est resté intact.
- **Leçon** : un seul agent par dossier de travail, et une branche par jour de spec.

## 2026-09-23 · J2

### J2 tâche 1 : human_review, interrupt() et politique d'arbitrage

**Fait.**
- `src/cdg/policy.py`, pur : `build_request` (charge utile exposée, sans le texte du contrat, flottants arrondis), `check` (politique), `review` (validation de la réponse brute puis `check`).
- `human_review` dans `orchestrator.py` : boucle `interrupt()` → `policy.review` ; une réponse refusée est redemandée avec `error` dans la charge utile. N'écrit que `human` et `final_decision`.
- `config/decision.yaml` : section `human_policy` (`allowed_decisions`, `allow_block_override`), validée par `HumanPolicy` : liste non vide, sans doublon, sans `ESCALADE`, `NO_GO` obligatoire.
- Politique : relecteur et motif non vides ; `GO` ou `GO_RESERVES` sur un blocage dur exige `overrides_block` (et que la levée soit permise) ; `overrides_block` sans blocage levé est refusé.
- Tests : politique pure, configuration, critère n° 4 (suspension, charge utile), reprise valide, reprise refusée puis acceptée. Les tests J1 des routes vers `human_review` passent désormais par `InMemorySaver`.

**Vérifié dans langgraph 1.2.12.** Plusieurs `interrupt()` dans un même nœud sont appariés aux reprises par ordre d'appel (testé sur trois reprises successives). `interrupt()` a gagné un paramètre `response_schema`, absent de la spec, non utilisé.

**Écarts avec la spec.**
- `policy.check` reçoit la configuration via `partial`, sous le nom `decision_config` (nom `config` réservé).
- La spec valide la réponse par `HumanDecision.model_validate` directement : une réponse mal formée ferait échouer le nœud. Ici, elle est redemandée avec le détail de l'erreur.
- **Critère n° 12 inatteignable par le graphe** : `decision_gate` envoie tout blocage dur vers `explain`, jamais vers `human_review`. La règle de levée n'est donc testée qu'en fonction pure. À trancher dans la spec.

### J2 tâche 1 : interrupt() rétabli dans human_review, réponse mal formée redemandée

**Fait.**
- `human_review` retrouve `interrupt()` et `policy.review`, retirés par `43f7d6b`. La logique est identique à `1329f6e`, qui était vert ; le formatage à 79 colonnes et les docstrings sont conservés.
- La branche redevient verte (172 tests).
- Spec mise à jour : code indicatif de l'adaptateur, appariement des `interrupt()` vérifié, traitement d'une réponse mal formée.

**Décision** (point laissé à mon jugement). Une réponse humaine que `HumanDecision` ne valide pas est **redemandée**, comme une réponse refusée par la politique. Le nœud n'échoue pas, contrairement à la première version de la spec. Raisons :
- Faire échouer le nœud sur une faute de frappe du relecteur transformerait une erreur de saisie en panne du graphe, sans `failure_report` structuré.
- Redemander en exposant l'erreur n'est pas un repli silencieux : l'erreur est montrée, et aucune décision par défaut n'est appliquée.
- Le thread reste suspendu, en échec fermé : si personne ne répond correctement, `expire` rend un `NO_GO` système.

**Pièges.**
- **Commit rouge assumé.** `43f7d6b` avait retiré `interrupt()` en laissant 5 tests rouges, contre la règle « un commit par tâche verte ». Un revert doit défaire aussi les tests, ou ne pas être commité.
- **Commits mélangés.** Ce même revert mêlait style et fonctionnel dans un seul commit. `git revert` aurait donc aussi défait les docstrings. D'où une restauration à la main, dont la logique a été comparée ligne à ligne avec `1329f6e`.
- **Rebase de `phase1-j1` sur `main`.** Seul conflit, dans ce journal : « Fin du J1 » (côté `main`) et la section J2 (côté Cursor) ont été gardées dans l'ordre chronologique. La branche est renommée `phase1-j2`, et l'état d'avant le rebase est sauvegardé dans `phase1-j1-sauvegarde`.

**Reste ouvert.** Le critère d'acceptation n° 12 ne peut pas être atteint dans le graphe, puisqu'un blocage dur va directement à `explain`. Décision d'architecture en attente.

### J2 : hard_block_review, critère n° 12 atteignable dans le graphe

**Fait.**
- `human_policy.hard_block_review`, un booléen **obligatoire**, à `false` dans `decision.yaml`.
  - À `false` : un blocage dur donne `NO_GO` et part vers `explain`, sans humain (comportement du J1).
  - À `true` : `decision_gate` propose `NO_GO` et route vers `human_review`, où seul un humain peut lever le blocage, avec `overrides_block` et un motif.
- Tests :
  - critère n° 12 de bout en bout, avec une configuration de test à `true` : suspension, refus sans `overrides_block`, acceptation avec motif, confirmation du `NO_GO` ;
  - avec la configuration par défaut, un blocage dur (juridique, financier, conformité) n'atteint jamais `human_review` ;
  - rapport budget conservé en revue humaine.
- Spec : schéma, code du gate, politique humaine, critère n° 12, historique.

**Choix.**
- La clé est obligatoire plutôt que dotée d'une valeur par défaut dans le modèle Pydantic. « False par défaut » est donc porté par `decision.yaml`, et une configuration qui l'omet est refusée au démarrage. C'est cohérent avec les autres clés de `human_policy` et avec « tout ce qui se règle vit dans la config ».

**Pièges.**
- Le critère n° 12 était inatteignable avec la décision n° 5 (blocage dur vers `explain`). Cursor l'avait relevé, mais ne l'avait testé que sur la fonction pure de la politique. Un critère d'acceptation doit se tester par le graphe. Sinon, il faut dire explicitement qu'il ne porte que sur une fonction.
- Le scellement de la levée (`overrides_block` et motif) relève d'`audit_seal`, au J4. Au J2, la levée est seulement portée par `human` dans l'état.

### J2 tâche 1 : checkpointer PostgreSQL, sérialiseur strict, setup-db

**Fait.**
- `orchestrator.py` :
  - `StrictSerializer` : liste autorisée `Clause`, `AgentVerdict`, `HumanDecision`, `Usage` ;
  - `open_graph`, qui compile le graphe avec `PostgresSaver` et le sérialiseur strict ;
  - `setup_database`, qui appelle `setup()` puis règle les droits d'`app_role` ;
  - `delete_thread`, réservé au ménage des tests.
- `settings.py` : `.env` lu par `python-dotenv` (`override=False`), chaînes de connexion administrateur et `app_role`, erreur explicite si une variable manque.
- `cli.py` : première commande, `setup-db`.
- Tests :
  - droits exacts d'`app_role` ;
  - cycle complet run, interrupt, resume sur PostgreSQL avec les seuls droits d'`app_role`, relu depuis une nouvelle connexion (critère n° 4 sur base réelle) ;
  - `DELETE` refusé ;
  - sérialiseur (types admis, types bloqués, isolation entre fils).
- Marqueur `pg`. Base arrêtée, ces tests sortent **en erreur** avec le message « PostgreSQL injoignable » (vérifié en arrêtant le conteneur). `-m "not pg"` les exclut explicitement.

**Requêtes de `PostgresSaver` 3.1.2**, lues dans le code installé :
- `SELECT` ;
- `INSERT ... ON CONFLICT DO NOTHING` sur les blobs et les writes ;
- `INSERT ... ON CONFLICT DO UPDATE` sur les checkpoints et les writes, qui exige le droit `UPDATE` ;
- `DELETE` seulement dans `delete_thread`, que l'application n'utilise pas ;
- `checkpoint_migrations` n'est lue que par `setup()`.

`app_role` reçoit donc `SELECT, INSERT, UPDATE` sur les 3 tables de données, et rien d'autre.

**Pièges.**
- **Le sérialiseur dégrade en silence** (risque confirmé). Avec `allowed_msgpack_modules`, un type hors liste ne lève pas d'erreur : il revient en `dict` (`Intrus(x=1)` devient `{'x': 1}`), avec un avertissement journalisé une seule fois. Dans un état typé, un `AgentVerdict` falsifié pourrait ainsi revenir en dict sans que rien ne casse.
- **On ne peut pas lever depuis le hook.** Le hook de lecture de msgpack intercepte **toute** exception et renvoie `None`, et les exceptions des écouteurs d'événements sont avalées et journalisées. `StrictSerializer` collecte donc les événements `msgpack_blocked` dans une variable propre au fil, puis lève `BlockedDeserialization` après `loads_typed`.
- **Limite restante.** Si le constructeur d'un type **autorisé** échoue (une donnée falsifiée mais de type connu), la bibliothèque renvoie `None` sans émettre d'événement. Le sérialiseur ne protège pas du contenu falsifié ; c'est le rôle de la chaîne d'audit (J4).
- **`PostgresSaver.from_conn_string`** n'accepte pas de sérialiseur. La connexion est donc ouverte à la main, avec les mêmes paramètres (`autocommit`, `prepare_threshold=0`, `dict_row`).
- **`prepare_threshold=0`** fait préparer chaque requête, et une requête préparée ne peut contenir qu'une commande (« cannot insert multiple commands into a prepared statement »). `REVOKE` et `GRANT` sont donc exécutés séparément.
- **Base arrêtée** : pytest compte les tests `pg` en *error* (échec de la fixture de session), pas en *failed*. Le code de sortie est non nul dans les deux cas, et aucun test n'est sauté.

### J2 tâche 2 : CLI run, resume, history en mode stub-j2

**Fait.**
- `stub_j2.py` :
  - `JsonClausesExtractor` lit les clauses synthétiques de `--clauses` et les valide, sans LLM ;
  - `no_corpus_crag` répond toujours `INSUFFISANT`, sans référence ;
  - sans `--clauses`, l'extraction lève une erreur explicite.
- `orchestrator.py` : `run_contract`, `resume_thread`, `thread_status` et `thread_history`, les seules fonctions à manipuler `Command` et `StateSnapshot`. `cli.py` n'importe pas LangGraph.
- `cli.py` : `run <contrat> --clauses <json> [--contract-id]`, `resume <thread_id> --decision --reviewer --reason [--overrides-block]` et `history <thread_id>`. Chaque sortie JSON porte `"mode": "stub-j2"`, et l'aide annonce « AUCUNE ANALYSE RÉELLE avant le J3 ».
- Tests :
  - `run` suspend en `ESCALADE`, puisque le CRAG répond `INSUFFISANT` partout ;
  - un blocage dur termine en `NO_GO` ;
  - `resume` finalise, puis `history` est chronologique ;
  - une réponse refusée laisse le thread suspendu, avec le motif ;
  - erreurs explicites : thread inconnu, thread terminé, thread existant, fichier de clauses absent.
- Démo réelle vérifiée à la main, puis thread supprimé.

**Choix.**
- **`run` refuse un thread existant.** Relancer `invoke` sur un thread terminé repartirait de `START` avec l'ancien état. Le réducteur cumulerait alors 8 verdicts, et `aggregate` lèverait une erreur. Mieux vaut refuser tout de suite, avec un message clair.
- **Réponse humaine refusée : code de sortie 0.** Le refus est une issue normale de la politique : le thread reste suspendu, et la sortie montre `statut: suspendu` avec `demande.error`. Le code 1 est réservé aux erreurs d'exécution.
- **Tests du graphe sans base.** Ils utilisent aussi le sérialiseur strict (`InMemorySaver(serde=strict_serializer())`), ce qui supprime les avertissements « unregistered type ».

**Pièges.**
- **Thread inconnu.** `get_state` renvoie un état vide (`values == {}`, `next == ()`) au lieu de lever une erreur. Il faut tester `values` pour distinguer un thread inconnu d'un thread terminé.
- **Horodatage.** `StateSnapshot.created_at` est une chaîne ISO 8601 avec fuseau, qui servira de base à `expire` (tâche 4).

### J2 tâche 3 : critère n° 5, reprise après un processus tué

**Fait.** `tests/test_reprise.py` :
- un sous-processus ouvre le graphe sur PostgreSQL, lance le contrat, affiche `"suspendu"`, puis se tue avec `SIGKILL`, **à l'intérieur** du `with`, donc connexion encore ouverte et sans aucun nettoyage ;
- un second processus reprend par `python -m cdg.cli resume` sur le même `thread_id` ;
- le contrat se termine en `NO_GO` ;
- le test vérifie le code de sortie `-9` et que le processus n'a rien affiché après sa suspension.

**Pièges.**
- **Où tuer le processus.** Le tuer après la fin de `cli.main` ne prouverait rien : le context manager a déjà fermé la connexion proprement. Il faut le tuer dans le `with`. PostgreSQL referme seul la session orpheline, et le checkpoint écrit avant `interrupt()` suffit à reprendre.
- **Environnement du sous-processus.** Il faut `sys.executable`, c'est-à-dire le Python du `.venv` lancé par `uv run`, pour que `cdg` et ses dépendances soient importables sans rien configurer.

### J2 tâche 4 : expire, décision système, critère n° 11

**Fait.**
- `HumanDecision.source` : `humain` par défaut, ou `systeme`. Une décision système n'est valide que si elle vaut `NO_GO` et que son relecteur commence par `systeme:`. Un humain ne peut pas utiliser le préfixe `systeme:`, pour qu'on ne puisse pas se faire passer pour le système.
- `expiry.py`, en fonctions pures :
  - `parse_duration` : `24h`, `30m`, `2d`, `90s` ;
  - `expired` : strictement au-delà du délai, avec une horloge qui doit avoir un fuseau ;
  - `system_decision` : `NO_GO`, relecteur `systeme:expire`, motif « timeout : en attente depuis … ».
- `orchestrator.expire_threads(graph, older_than, now, thread_ids=None)` et la commande `expire --older-than`.
- Tests :
  - critère n° 11 : le thread expiré finit en `NO_GO` système ;
  - un thread pile au délai n'est pas touché ;
  - un thread terminé n'est jamais repris ;
  - un thread qui a reçu une réponse refusée expire aussi ;
  - la CLI avec un délai de 1 000 jours n'a aucun effet ;
  - une durée invalide est rejetée.

**Choix.**
- **Point de départ du délai** : la date du checkpoint de suspension. Une réponse refusée ne remet pas le compteur à zéro, parce que le thread n'a pas avancé.
- **`thread_ids`** limite `expire_threads` aux threads des tests. `expire` agit sur **toute** la base, et les tests tournent sur la base de développement : sans ce filtre, un test avec une horloge avancée passerait en `NO_GO` les vrais contrats en attente. La CLI n'expose pas ce filtre.

**Pièges.**
- **Interblocage dans `PostgresSaver.list()`.** Ce générateur produit ses résultats *à l'intérieur* de `with self._cursor()`, qui détient `self.lock`, un `threading.Lock` non réentrant. Appeler `graph.get_state()` pendant le parcours bloque donc indéfiniment : la suite de tests a gelé. Correctif : épuiser le générateur, pour collecter les identifiants de threads, **puis** interroger chaque thread.
- **Thread orphelin.** Le processus gelé a été tué, et le nettoyage de la fixture n'a pas tourné : un thread `test-…` est resté en base, suspendu. Il a été supprimé à la main. Un test interrompu peut laisser des threads orphelins, et `expire` les verrait.
- **`next` et une réponse refusée.** Après une réponse refusée, `human_review` s'interrompt de nouveau, et la tâche est bien présente dans `snapshot.tasks` avec son interrupt. Pourtant, **`snapshot.next` vaut `()`**. L'ancien critère, `next == ("human_review",)`, faisait donc refuser par `resume` la bonne réponse suivante, et `expire` ignorait le thread. **Bug réel**, invisible tant que les tests ne reprenaient qu'une fois par la CLI. Nouveau critère : une tâche `human_review` porte un interrupt. Un test de non-régression enchaîne, via la CLI, une réponse refusée puis une réponse acceptée.

### J2 tâche 5 : étude d'error_handler (rapport, sans code)

**Sonde** (jetable, hors dépôt) dans langgraph 1.2.12 :

| Cas | Résultat |
| --- | --- |
| Gestionnaire qui renvoie un dict, arête fixe après le nœud en échec | Écritures appliquées, puis **fin d'exécution** (`next = ()`) : l'arête n'est pas suivie |
| Gestionnaire qui renvoie un dict avec `route`, arête conditionnelle qui lit `route` | Même chose : `route` est écrite mais jamais lue, fin d'exécution |
| Gestionnaire qui renvoie `Command(update=..., goto="human_review")`, avec `destinations` | `human_review` est atteint : **seul moyen de continuer** |
| Un `Send` qui échoue, seul | Gestionnaire appelé, exécution terminée sans erreur |
| 4 `Send` en parallèle, dont un qui échoue | Gestionnaire appelé, **mais l'exception remonte** et l'exécution échoue |

**Conclusion.**
- `error_handler` ne s'intègre pas à la règle « un seul mécanisme de routage » : il faut un `Command(goto=...)`.
- Il ne protège pas non plus notre fan-out d'analystes, qui est pourtant le cas le plus probable d'erreur d'API (CRAG au J3).
- Arrêt prévu : décision d'architecture soumise au propriétaire du repo.

**Pièges.**
- La documentation (`NodeError`) donne un exemple qui renvoie un `Command`, sans dire qu'un dict termine l'exécution. Seule une sonde l'a montré.
- Le comportement en parallèle (exception propagée alors que le gestionnaire a tourné) n'est pas documenté. Bogue ou intention : à revérifier à la prochaine version de LangGraph.

### J2 : échec de nœud, erreur JSON et état lisible (option c)

**Fait.**
- Décision du propriétaire du repo : option (c) au J2, option (b) conçue au J3.
- Test ajouté : une clause attendue manque, donc la règle financière lève une erreur pendant le fan-out. On vérifie trois choses :
  - `run` sort avec le code 1 et une erreur JSON (`ValueError`, qui nomme la clause) ;
  - `history` relit l'état, dont le dernier checkpoint attend encore les 4 analystes (`route = analysts`) ;
  - `resume` refuse explicitement, puisque le thread n'attend pas d'humain.
- Spec : la décision du J2 et la conception prévue pour le J3, à savoir des gardes dans l'orchestrateur, une clé `failures` cumulée par réducteur, une escalade par `decision_gate` et `RetryPolicy` sur les analystes.
- Ajouts validés : préfixe `systeme:` réservé, filtre `thread_ids` réservé aux tests, `run` qui refuse un thread existant, `resume` limité aux threads en attente.

**Pièges.**
- Aucun changement de code n'a été nécessaire : l'option (c) était déjà le comportement réel. Le test la verrouille, pour qu'une future garde (J3) ne la change pas sans que ce soit visible.
- Après l'échec d'une branche du fan-out, le dernier checkpoint indique `next = [analyst × 4]`. Les écritures des 3 analystes réussis sont conservées en attente, comme le prévoit la spec (« seul le fautif est rejoué »). Au J3, une reprise après correction ne relancera que l'analyste en échec.

### chore : .vscode retiré du dépôt

**Fait.** `.vscode/settings.json`, commité par Cursor, est retiré de l'index (`git rm --cached`), mais conservé sur le disque. `.vscode/` est ajouté au `.gitignore` : la configuration de l'IDE reste locale.

### style : ruff format et ruff check, line-length 100

**Fait.**
- ruff 0.16.8 en dépendance de dev, avec `[tool.ruff]` : `line-length = 100`, `target-version = "py312"`.
- `ruff format` sur tout le dépôt. Le formatage à 79 colonnes de Cursor laisse place à une seule convention.
- `ruff check --fix`.
- Commandes ajoutées à CLAUDE.md.
- **Aucun changement de comportement** : les 255 tests restent verts avec `-W error`. Côté `src/`, le diff ne contient que du reformatage et des commentaires.

**Pièges.**
- **Le jeu de règles par défaut a changé.** ruff 0.16 active environ 410 règles, pas seulement l'ancien noyau `E4`, `E7`, `E9` et `F`. `ruff check --fix` n'en corrige qu'une partie. Traitement des 40 signalements restants :
  - **C408** (34 cas, `dict(score=…)` dans les tests) : conversion en littéraux par le correctif « unsafe » de ruff, **limité à cette règle**. Les deux formes sont strictement équivalentes.
  - **PLW1510** : `check=False` rendu explicite. Les tests vérifient déjà le code de retour, et le processus tué doit rendre `-9`.
  - **RUF015** (un test) : accès direct à l'élément au lieu d'une liste intermédiaire.
  - **BLE001** (le `except Exception` de la CLI, volontaire), **TRY004** (passer à `TypeError` changerait le comportement testé) et **DTZ001** (un test veut une horloge sans fuseau) : `# noqa`, avec la raison sur la ligne au-dessus.
- **Une correction sûre qui gêne l'audit.** SIM114, pourtant marqué « sûr », avait fusionné les étapes 3 (`INSUFFISANT`) et 4 (conflit) du gate en un seul `or`. Le résultat était équivalent, mais l'ordre de la spec devenait illisible. Les deux branches ont été rétablies, commentées. Il faut relire les corrections automatiques, même sûres, quand elles touchent la logique de décision.
- **Tri des imports (I001).** ruff classe `doubles`, le module de test, parmi les imports tiers, car il ne le connaît pas comme premier parti. C'est sans effet.
- **Commentaires déplacés.** Le formateur avait sorti un commentaire de fin de ligne de son contexte, dans un `parametrize` de `test_rules.py`. Il a été remis à la main.

## 2026-09-24 · J3

### J3 tâche 1 : dépendances, section llm, option --llm

**Fait.**
- Dépendances ajoutées par le propriétaire du repo : `mistralai` 2.10.1, `anthropic` 1.8.0, `fastembed` 0.8.1 (`onnxruntime` 1.30.0) et `pgvector` 0.5.0.
- Section `llm` de `decision.yaml` : fournisseur `mistral` par défaut, `anthropic` en alternative ; température 0 ; modèles par niveau (`main` pour l'extraction, `light` pour le juge CRAG). Le modèle Pydantic **refuse les alias `-latest`**.
- Option pytest `--llm` : les tests `llm` sont exclus par défaut et comptés comme *deselected*. L'en-tête de pytest le signale. Des tests à part (`pytester`) vérifient ce mécanisme.

**Vérification des modèles**, faite le 2026-09-24 dans la documentation officielle, par le navigateur intégré :
- **Mistral** (docs.mistral.ai, fiche de chaque modèle) :
  - Small 4 : `mistral-small-2603` ;
  - Ministral 3 8B : `ministral-8b-2512` ;
  - Medium 3.5 : `mistral-medium-3-5` ;
  - Large 3 : `mistral-large-2512` ;
  - Embed : `mistral-embed-2312`, dimension 1024 (page « Text Embeddings »).
- **Anthropic** (platform.claude.com, « Models overview ») : `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5` et `claude-haiku-4-5-20251001`.

**Pièges.**
- **Page de Mistral illisible en brut.** Le HTML mélange modèles actuels et retirés. Il a fallu lire la page rendue, puis ouvrir chaque fiche pour voir l'identifiant daté.
- **Alias `-latest`.** Ils existent chez Mistral (`mistral-small-latest`…) mais changent de cible sans que la config change. On les refuse pour que le rejeu et l'audit restent fidèles.
- **Pourquoi une option `--llm`.** Avec un simple marqueur exclu par `addopts`, un `-m "not pg"` passé en ligne de commande aurait remplacé l'exclusion, et lancé les tests payants. Le test `test_exclure_pg_n_active_pas_les_tests_llm` le fixe.
- **`onnxruntime`.** La version résolue par `uv add` (1.30.0) diffère de celle de ma résolution de dimensionnement (1.19.2) : la résolution dépend du lock existant. Les tailles annoncées restent un ordre de grandeur.

### J3 tâche 2 : interface des fournisseurs LLM

**Fait.**
- `deps.LLMProvider` expose `structured(tier, system, user, schema, node)`, qui renvoie le modèle Pydantic validé et un `Usage` (tokens et latence).
- `providers/mistral.py` utilise `chat.parse(response_format=…)`, et `providers/anthropic.py` utilise `messages.parse(output_format=…)`.
- `providers.build_provider` choisit le fournisseur d'après la configuration. Si la clé d'API manque, il lève une `SettingsError` qui nomme la variable.
- Une réponse non structurée, ou sans consommation, lève une `LLMOutputError`, jamais un repli.
- Doublure `FakeLLM` : réponses scriptées par nœud, appels enregistrés, ce qui permet de vérifier le texte envoyé au modèle.
- Tests avec de faux clients qui imitent les réponses des SDK : aucun appel réseau.

**Pièges.**
- **Import du client Mistral.** `mistralai` 2.x n'exporte plus `Mistral` à la racine : l'import correct est `from mistralai.client import Mistral`.
- **Sortie structurée de Mistral.** Elle se trouve dans `choices[0].message.parsed`, qui vaut `None` si le contenu est vide. Il faut le vérifier explicitement.
- **Pas de `temperature` chez Anthropic.** `anthropic` 1.8.0 : `messages.parse` n'a plus de paramètre `temperature` (liste lue dans la signature installée). Le réglage `llm.temperature: 0` ne vaut donc que pour Mistral. Le verdict reste déterministe à partir des clauses, mais l'extraction par Claude peut varier d'une exécution à l'autre.
- **Bug corrigé** : le fournisseur Anthropic lisait les modèles du fournisseur *configuré* (Mistral). Chaque fournisseur lit désormais ses propres modèles (`config.model(tier, provider=self.name)`).
- **Aucun essai réel du fournisseur Anthropic** : pas de clé d'API. Seul le test avec faux client le couvre.

### J3 tâche 3 : masquage avant le graphe, validate_input complet

**Fait.**
- `masking.py` : e-mails, téléphones, IBAN, SIRET et SIREN, remplacés par un repère (`[EMAIL]`, `[IBAN]`…). Les noms de parties déclarés deviennent `[PARTIE_n]`, en mot entier, sans tenir compte de la casse. `residual_pii` signale ce qui subsiste.
- `run_contract(…, parties)` masque **avant** `graph.invoke`, et renvoie le nombre de masquages par type. La CLI accepte `--party`, répétable.
- `validate_input`, sur le texte masqué :
  - rejette un texte vide ;
  - rejette un texte trop long (`max_chars`) ;
  - rejette un texte trop court pour juger de la langue (`min_words`) ;
  - rejette un texte dont la part de mots-outils français est trop faible (`min_french_ratio`) ;
  - rejette un texte où subsiste une donnée personnelle.
- `tests/doubles.CONTRACT_TEXT` : contrat synthétique en français, qui contient les citations des clauses de test. Les anciens tests qui envoyaient `"x"` ou `"Contrat synthétique."` l'utilisent désormais.
- Tests :
  - chaque motif est masqué ;
  - montants et références sans clé de Luhn ne sont pas masqués ;
  - le masquage est idempotent ;
  - l'état et l'extracteur ne voient que le texte masqué ;
  - **sur PostgreSQL**, aucune trace du texte original dans `checkpoints`, `checkpoint_blobs` ni `checkpoint_writes`, alors que le texte masqué y est bien.

**Pièges.**
- **Masquer dans le nœud aurait été trop tard.** L'entrée de `graph.invoke` est écrite dans le premier checkpoint *avant* `validate_input`. Masquer dans le nœud aurait laissé le texte en clair dans PostgreSQL. C'était la recommandation 5, et le test sur la base réelle le démontre.
- **Faux positifs SIREN.** `150 000 000 euros` a la forme d'un SIREN. Deux filtres évitent de masquer des montants : la clé de Luhn (vérifiée par un test sur `123 456 789`) et l'absence d'unité juste après (€, euros, %, mois, jours, ans).
- **Langue.** La liste de mots-outils exclut « a » et « on », qui sont aussi anglais. Un texte anglais typique donne une part proche de 0, un contrat français environ 0,3.
- **Recherche dans PostgreSQL.** Un `jsonb` ne se convertit pas en `bytea` par un simple cast (`InvalidTextRepresentation`) : il faut `convert_to(x::text, 'UTF8')`.
