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

### J3 tâche 4 : extraction réelle, contrat délimité comme donnée

**Fait.**
- `extraction.py` : `LLMExtractor` appelle le modèle `main` avec un schéma `ExtractionOutput`, dont `kind` est limité aux 8 types attendus. Chaque élément est ensuite validé en `Clause`.
- Prompt système dans `prompts/extraction_system.md` : le contrat est une donnée, jamais une instruction ; pour chaque type, l'unité de `value` et le sens de `null` ; interdiction d'inventer une citation. **Aucune règle de décision** (seuils, pénalités, poids, verdicts), ce que vérifie un test.
- Délimitation : `<<<CONTRAT-jeton>>> … <<<FIN-CONTRAT-jeton>>>`, avec un jeton aléatoire (`secrets.token_hex`) régénéré s'il figure déjà dans le texte. Le retour de vérification d'un nouvel essai est placé **après** la balise de fin.
- Test du graphe : ce que reçoit le fournisseur LLM est le texte masqué (`[EMAIL]`, `[PARTIE_1]`), sans aucune donnée d'origine (précision 5).

**Choix.**
- **Clause incohérente.** Une clause présente sans citation, ou absente avec une citation, lève une erreur explicite au lieu d'être corrigée en silence. Au J3, la garde d'échec de nœud (tâche 10) en fera un rapport d'échec et une escalade.
- **Types dupliqués.** Le type `Kind` recopie `REQUIRED_KINDS`, parce qu'un `Literal` ne se construit pas dynamiquement proprement. Un test vérifie que l'énumération du schéma JSON reste identique à `REQUIRED_KINDS`.

**Pièges.**
- **Faux positif possible dans le test « aucune règle de décision ».** Il cherche `GO` dans le prompt : un mot en capitales comme « CATÉGORIE » le déclencherait. Le prompt est écrit en minuscules.
- **Prompt livré avec le paquet.** Le fichier `.md` doit partir avec le paquet. Vérifié : il est bien présent dans la wheel construite par hatchling.

### J3 tâche 5 : verify_extraction réel, critère n° 10

**Fait.**
- `verify_extraction`, sans LLM :
  - normalisation : NFKC, apostrophes, guillemets et tirets typographiques unifiés, espaces (insécables compris) réduits, **casse conservée** ;
  - problèmes détectés : clause manquante, clause en double, type inconnu, citation introuvable ;
  - essais : retour ciblé tant qu'il en reste, puis `ESCALADE` avec `failure_report` (`stage`, `attempts`, `problems`).
- Tests :
  - normalisation ;
  - citation typographiquement différente mais équivalente, acceptée ;
  - clause absente non vérifiée ;
  - ré-extraction puis escalade ;
  - citations comparées **au texte masqué** : une citation qui porte la donnée d'origine est introuvable (précision 5) ;
  - **critère n° 10** sur le graphe : deux citations inventées donnent une escalade avec rapport d'échec, **aucun analyste** ne tourne, et le second essai reçoit le retour ciblé. Une citation corrigée au second essai mène aux 4 analystes.

**Pièges.**
- **Le test d'échec de nœud du J2 ne provoquait plus de panne.** Il retirait une clause attendue pour faire échouer une règle pendant le fan-out. Désormais, `verify_extraction` intercepte ce cas en amont : un nouvel essai, puis une escalade. Le test provoque maintenant la panne par une clause présente sans citation, qui fait échouer le nœud d'extraction lui-même. C'est aussi une preuve que la vérification protège les règles.
- **Normalisation et guillemets français.** « » devient `"`, mais l'espace insécable qui suit le guillemet français reste un espace. Une citation copiée sans ces espaces serait déclarée introuvable. Ce cas est volontairement strict ; le retour ciblé laisse au modèle une chance de corriger.

### J3 tâche 6 : migration 002, table rag_chunks

**Fait.**
- `migrations/002_rag.sql`, idempotente (`IF NOT EXISTS`) : table `rag_chunks`, domaine contrôlé par `CHECK`, unicité (`domain`, `content_hash`, `embedding_model`), index sur `domain`, `GRANT SELECT` à `app_role`.
- `rag_store.setup` : appliquée par `setup-db` sur une base existante, puis contrôle de la dimension de la colonne par rapport à `embedding.dimension`. En cas d'écart, `RagStoreError`, qui nomme les deux valeurs.
- Section `embedding` de `decision.yaml` : `intfloat/multilingual-e5-large`, 1024 dimensions, préfixes e5.
- Vérifié :
  - sur la base existante (tests `pg`) : lecture seule, dimension, migration rejouable, écriture refusée à `app_role` ;
  - sur un **volume vide** (instance jetable), par l'init Docker : les deux migrations passent, dimension 1024, `SELECT` seul.

**Écart avec mon plan : pas d'index HNSW.** La spec veut le filtre `domain` *avant* la recherche vectorielle. Or un index HNSW filtre après son parcours, et peut rendre moins de `k` résultats (pgvector propose des parcours itératifs, mais c'est de la complexité inutile ici). Pour quelques centaines d'extraits, une recherche exacte `WHERE domain = … ORDER BY embedding <=> …` est fidèle à la spec et rapide.

**Pièges.**
- **Lire la dimension d'une colonne `vector`.** Elle se trouve dans `pg_attribute.atttypmod` : ici 1024, sans décalage, contrairement à `varchar`.
- **ruff et le Markdown.** `ruff format` (0.16) reformate aussi les blocs de code Python des fichiers Markdown, donc ceux de la spec. C'est sans effet sur le sens, mais il faut le savoir en lisant un diff de la spec.
- **Plusieurs commandes SQL en une fois.** psycopg les accepte dans un seul `execute`, mais seulement sans paramètre et sur une connexion ordinaire. Celle du checkpointer (`prepare_threshold=0`) les refuserait (voir J2).

### J3 tâche 7 : embedding local et recherche filtrée

**Fait.**
- `embeddings.FastembedEmbedder` :
  - `embed_passages` et `embed_query` ajoutent les préfixes e5 (`passage: ` et `query: `) ;
  - la dimension de chaque vecteur est contrôlée ;
  - à l'exécution, `local_files_only=True` : sans poids en cache, `EmbeddingError` cite la commande à lancer.
- `embeddings.fetch_model` et la commande `cdg.cli fetch-embedding-model` : le seul chemin qui télécharge, lancé explicitement.
- `settings.embedding_cache_dir()` : `EMBEDDING_CACHE_DIR` est obligatoire, et un chemin relatif part de la racine du dépôt. `.cache/` est ignoré par git, et `.env.example` est complété.
- `rag_store` :
  - `insert` : idempotent grâce à `ON CONFLICT` sur (`domain`, hash, modèle) ;
  - `search` : recherche exacte `WHERE domain = … AND embedding_model = … ORDER BY embedding <=> …`.
- Doublure `HashEmbedder` : sac de mots haché, normalisé et déterministe, sans modèle.
- Tests `pg` :
  - le filtre par domaine s'applique **avant** la distance : un extrait d'un autre domaine, pourtant plus proche de la requête, ne sort jamais ;
  - `k` est respecté ;
  - un autre modèle d'embedding ne voit pas ces extraits.

**Pièges.**
- **Pas de préfixes e5 par fastembed.** `query_embed` et `passage_embed` appellent simplement `embed`, alors que les modèles e5 attendent `query: ` et `passage: `. Sans eux, la qualité de la recherche chute sans aucune erreur.
- **Cache temporaire par défaut.** fastembed range les poids dans un dossier temporaire du système (`tempfile.gettempdir()/fastembed_cache`), qui peut être vidé : 2,2 Go à retélécharger à chaque fois. Le chemin est donc explicite et persistant.
- **Aucun téléchargement à l'exécution.** Sans `local_files_only`, le premier embedding lancé déclencherait un téléchargement de 2,2 Go en plein graphe. Le paramètre passe par les `kwargs` de `TextEmbedding`.
- **Filtre par modèle.** On filtre aussi sur `embedding_model` : des vecteurs de deux modèles différents ne sont pas comparables, même à dimension égale.

### J3 tâche 7 (suite) : chargement ONNX depuis le cache Hugging Face

**Incident.** `fetch-embedding-model`, lancé par le propriétaire du repo, a bien téléchargé les 2,25 Go, puis le chargement a échoué : « External data path validation failed … External data path escapes model directory ».

**Cause.**
- **Le cache.** Hugging Face, avec le stockage `hf-xet`, range les fichiers dans un stockage dédupliqué, avec un sous-dossier par préfixe de hash (`blobs/29/…`, `blobs/9e/…`). L'instantané du modèle n'est qu'une série de liens symboliques vers ces blobs.
- **onnxruntime 1.30.** Un contrôle de sécurité exige que `model.onnx_data` (les poids externes) soit dans le même dossier que `model.onnx` *une fois les liens résolus*. Ici, les deux fichiers résolvent vers `blobs/29` et `blobs/9e` : refus.

**Correctif** (sans nouvelle dépendance ni copie) :
- `embeddings.materialize` crée `EMBEDDING_CACHE_DIR/flat/<modèle>/` avec des **liens physiques** vers les fichiers réels, que fastembed charge par `specific_model_path`. Aucun espace disque supplémentaire : le cache pèse toujours 2,1 Go.
- Si le cache et ce dossier ne sont pas sur le même système de fichiers, une erreur explicite est levée, plutôt qu'une copie silencieuse de 2 Go.
- La mise à plat est locale et rejouable. L'exécution la refait au besoin, sans aucun accès réseau (`local_files_only`). Il n'a donc pas fallu relancer le téléchargement.

**Vérifié** avec le vrai modèle, hors ligne (`HF_HUB_OFFLINE=1`, sandbox sans réseau) :
- chargement en 6,7 s, vecteurs de dimension 1024 ;
- sur une requête « obligations du sous-traitant », la phrase RGPD sur le sous-traitant score 0,872, contre 0,783 pour une phrase sur les pénalités de retard.

**Pièges.**
- Cet échec n'apparaissait dans aucun test à doublures : il fallait les vrais poids. Un test reproduit désormais la structure du cache, avec des blobs dans des sous-dossiers différents et un instantané en liens symboliques.
- Les similarités cosinus de e5 sont resserrées (environ 0,78 à 0,87). Un seuil fixe de pertinence serait fragile. Le tri reste bon, et c'est le juge du CRAG, pas un seuil, qui décidera de la pertinence.

### J3 : règle de transfert hors UE (avant la tâche 8)

**Fait.**
- Nouveau type de clause `transfert_hors_ue`, et un champ `Clause.category`, optionnel et nul par défaut, obligatoire pour les types de `CATEGORY_KINDS`.
- Catégories possibles, figées dans le schéma d'extraction (`TransferCategory`) : `sans_transfert`, cinq garanties nommées, et `aucune_garantie`.
- Règle de conformité :
  - transfert avec une garantie de `rules.conformite.transfer_safeguards` : aucune pénalité, un constat informatif ;
  - données dans l'UE (`sans_transfert`) : rien ;
  - toute autre catégorie (dont `aucune_garantie`, ou catégorie absente) : **blocage dur** ;
  - clause absente alors que des données personnelles sont traitées : pénalité `unlocated_data_score_penalty` (0,3) et constat « à vérifier ».
- `verify_extraction` signale une catégorie manquante sur un transfert et une catégorie inattendue sur un autre type. Le prompt d'extraction décrit les catégories.
- Configuration validée : liste non vide, sans doublon, et ni `sans_transfert` ni `aucune_garantie` ne peuvent y figurer.

**Choix, à valider.**
- **Pourquoi un champ `category` plutôt que `value` :** un transfert se décrit par une catégorie (quelle garantie ?), pas par une quantité. C'est un changement du schéma des clauses, compatible avec les 8 autres types (nul par défaut).
- **Pénalité de localisation : 0,3.** La valeur n'était pas fixée ; c'est celle que je propose, réglable dans la config.
- **Localisation non précisée sans données personnelles : aucune pénalité**, puisqu'il n'y a alors rien à localiser. En revanche, un transfert annoncé sans garantie bloque même sans clause de données personnelles, car la règle juge ce que dit le contrat.
- **Catégorie absente : blocage, par prudence.** `verify_extraction` la signale d'abord et relance l'extraction ; la règle ne la voit donc qu'en dernier recours.

**Piège.** `set -e && commande` ne protège rien : dans une liste `&&`, bash n'interrompt pas le script sur l'échec d'une commande qui n'est pas la dernière. Deux commits de documentation sont ainsi passés sans leur mise à jour de la spec (corrigés par `--amend`). `set -e` se met désormais seul sur sa ligne.

**Vérification du choix de conception « `NO_GO` seulement sur blocage dur ».** La conformité peut désormais perdre 0,3. Le pire cumul de toutes les pénalités donne 0,555, sans conflit, donc au pire `GO_RESERVES`. Le choix tient toujours, mais sa justification change ; la spec est mise à jour.

### J3 tâche 8 : corpus, nettoyage, fiches, ingestion

**Fait.**
- `corpus.py` : analyse des fichiers Légifrance et EUR-Lex, règles de nettoyage, `expired`, découpage (paragraphes regroupés jusqu'à `corpus.chunk_max_words = 300` mots), manifeste et fiches.
- `data/corpus/manifest.yaml` : pour chaque article admis, son domaine d'indexation. RGPD art. 79 est explicitement exclu. Un test vérifie que **chaque fichier de `raw/` est ingéré ou exclu**, jamais ignoré en silence.
- Migration `003_rag_versions.sql`, idempotente : `article`, `chunk_index`, `valid_from`, `valid_until`, `amendment`, `note`, `retrieved_at`. `setup-db` applique désormais toutes les migrations du corpus (002, 003).
- `rag_store.sync` aligne la base sur le corpus : il supprime les extraits disparus, par exemple après un changement de règle de nettoyage, et insère les nouveaux. La commande `ingest` s'en sert.
- **6 fiches** (`data/corpus/fiches/`), listées dans `SOURCES.md`. Un test vérifie que chacune commence par l'avertissement demandé, et que chaque ligne d'affirmation cite un article admis du corpus.
- Ingestion réelle (vrai modèle, hors ligne) : 57 extraits, en 1 min 28 s. Les recherches de contrôle tombent juste dans chaque domaine (voir le rapport de tâche).
- README : exemple de gestion des versions (L441-10), et section « Limites connues ».

**Règles de nettoyage, testées sur les fichiers réels :**
1. Métadonnées de version : L441-10 (du 26/04/2019 au 01/01/2027, « Modifié par ») et 1231-3 (« Création », version ouverte).
2. Lignes d'interface : **aucun fichier réel n'en contient**. Le test les ajoute au texte réel de L441-10 et vérifie qu'on retrouve exactement le texte d'origine.
3. Note « Conformément aux dispositions … » : 1171, sortie du texte et stockée dans `note` (vérifié aussi en base).
4. Validité : `expired(valid_until, date)` est vrai à partir du 01/01/2027. La date est stockée ; son exploitation à l'analyse arrive à la tâche 9 (CRAG).

**Choix.**
- **Périmètre, art. 4 du RGPD.** Il est admis comme « servant la règle conformité », au titre des définitions. Aucun article servant une règle ne le cite : c'est une interprétation, à valider.
- **Texte embarqué et texte stocké.** Le texte embarqué est précédé de la référence et de l'intitulé (`RGPD, art. 28 — Sous-traitant`), ce qui améliore la recherche. Le texte stocké reste celui de l'article, pour être cité tel quel.
- **Un article dans plusieurs domaines.** C'est le cas de L442-1 (opérationnel et juridique) : une ligne par domaine, pour que le filtre avant recherche reste simple.

**Pièges.**
- **Faux renvoi.** « articles 32 à 36 » (art. 28) désigne aussi 34 et 35, qui n'ont pas été récupérés. Ils sont listés dans les limites connues du README.
- **Format EUR-Lex.** L'intitulé suit le titre, parfois après une ligne vide (art. 4). Les points `a)`, `b)` sont sur une ligne à part, et le découpage par paragraphes les conserve dans l'ordre (vérifié : aucun mot perdu sur l'art. 28).
- **Règles de nettoyage modifiées.** Une ingestion seulement additive laisserait les anciens extraits en base, avec des doublons contradictoires. D'où `sync`, qui supprime les extraits obsolètes (testé).

### J3 tâche 9 : sonde du sous-graphe CRAG (point d'arrêt)

La spec demande de vérifier, dans la version installée, si un sous-graphe hérite du checkpointer.

**Sonde** (jetable, hors dépôt), dans langgraph 1.2.12 : un sous-graphe invoqué à l'intérieur de `analyst`, lancé 4 fois en parallèle par `Send`, dans un graphe parent muni d'un checkpointer à sérialiseur strict.

| Compilation du sous-graphe | Résultat |
| --- | --- |
| `compile()`, par défaut (`checkpointer=None`) | **Il hérite du checkpointer du parent.** Chaque étape du sous-graphe est écrite dans les checkpoints du parent, sous un espace de noms par analyste (`analyst:<id de tâche>`). Son état contient un type hors de la liste autorisée : `StrictSerializer` lève `BlockedDeserialization` et **l'exécution échoue**. |
| `compile(checkpointer=False)` | Aucun checkpoint du sous-graphe. Les 4 analystes aboutissent, et l'état du parent se relit normalement. |
| Par défaut, avec le type du sous-graphe ajouté à la liste autorisée | Les 4 analystes aboutissent, avec 4 espaces de noms de checkpoints en plus de celui du parent. |

**Conclusion.** L'héritage est le comportement par défaut. Il touche à trois choses : la liste des types autorisés du sérialiseur, le volume de la base, et la reprise (un CRAG interrompu reprend-il en cours de route, ou repart-il de zéro ?). Décision soumise au propriétaire du repo.

**Piège.** Sans le sérialiseur strict du J2, l'héritage serait passé inaperçu : les états du CRAG se seraient retrouvés en `dict` dans les checkpoints, avec seulement un avertissement.

### J3 : décisions du 25/09/2026

1. **Sous-graphe CRAG : `compile(checkpointer=False)`.** Aucun checkpoint du CRAG ; un analyste relancé refait son CRAG de zéro. Le CRAG renvoie un résumé (requêtes, passes, références retenues et expirées), porté par le verdict de l'analyste. Reporté dans la spec.
2. **RGPD, art. 4 : admis.** La règle de périmètre de `SOURCES.md` est complétée : un article est aussi admis s'il définit un terme utilisé par une règle.
3. **RGPD, art. 79 : exclusion validée.** Le fichier reste dans `raw/`, non ingéré.
4. **Tests `llm` (tâche 12) : lancés par l'agent**, avec la clé Mistral du propriétaire du repo.
5. **Refonte en ports et adaptateurs avant la tâche 9**, pour que le CRAG dépende d'un port dès sa création. Plan soumis avant tout code.

### J3 : refonte en ports et adaptateurs (avant la tâche 9)

Refonte sans changement de comportement, décidée pour que le CRAG dépende d'un port dès sa création. Détails et écart assumé (le flux vit dans le graphe) : `docs/adr-002-ports-et-adaptateurs.md`.

- **Couches** : `domain/` (règles pures), `ports/` (`LLMProvider`, `Embedder`, `Retriever`, `AuditStore`), `application/` (nœuds, extraction, CRAG, ingestion, `deps.py`), `adapters/` (`langgraph/`, `postgres/`, `llm/`, `fastembed.py`) ; `cli.py`, `settings.py` et `stub_j2.py` restent à la racine du paquet.
- **Fichiers scindés** : `deps.py` (ports LLM et embedding d'un côté, dépendances injectées de l'autre), `orchestrator.py` (câblage d'un côté, checkpointer et sérialiseur de l'autre), `corpus.py` (partie pure dans le domaine, lecture des fichiers et construction des `ChunkRow` dans `application/ingestion.py`), `settings.py` (chaînes de connexion vers `adapters/postgres/conninfo.py`, qui seul importe psycopg).
- **Renommages** : `deps.Retriever` → `application.deps.Crag` (le nom `Retriever` passe au port de recherche) ; `rag_store.Chunk` → `ports.retriever.Passage` ; `settings._require` → `settings.require` ; `providers.LLMOutputError` → `ports.llm.LLMOutputError` (supprime l'import circulaire local des deux fournisseurs) ; `orchestrator._saver` → `checkpointer.open_saver`.
- **Tests** : `test_isolation.py` réécrit (confinement de 6 bibliothèques, sens des dépendances pour 4 couches, indépendance des familles d'adaptateurs, chaque vérification prouvée sur une arborescence fictive) ; `test_ports.py` ajouté (signatures, 10 implémentations). Imports des tests mis à jour, aucune assertion modifiée. 368 → 405 tests.
- **Vérification de bout en bout** : `setup-db` puis `ingest` réel après la refonte : 57 extraits inchangés, 0 inséré, 0 supprimé. Le corpus produit est identique.

**Fichiers qui ne se rangent pas proprement dans une couche** (laissés comme demandé, soumis au propriétaire du repo) :
1. `domain/state.py` mêle les modèles métier (`Clause`, `AgentVerdict`, `HumanDecision`, `Usage`) et la forme de l'état du graphe (`ContractState` avec ses réducteurs, `route`, `AnalystInput` pour les `Send`), qui relève de l'orchestration.
2. `domain/config.py` lit un fichier (`load_config`), et porte des réglages d'adaptateurs : identifiants de modèles LLM, modèle et préfixes d'embedding.
3. `application/nodes/decision_gate.py`, `verify_extraction.py` et `validate_input.py` contiennent de la logique de décision ou de vérification pure (ordre des gardes, agrégat, conflit, marge ; normalisation des citations ; part de mots-outils), sans aucun port : c'est du domaine logé dans des nœuds.
4. `application/ingestion.py` lit les fichiers du corpus sans port, comme `load_config`.
5. `domain/corpus.py` porte `ChunkRow`, qui est la ligne à écrire en base (vecteur, modèle d'embedding) : faute de port d'écriture, le domaine est le seul endroit commun à l'application et à l'adaptateur.

**Pièges.**
- **Module homonyme d'une variable.** `test_providers.py` avait une variable locale `llm` qui masquait le module `cdg.adapters.llm` importé sous ce nom : import direct de `build_provider`.
- **`from a import b` dans l'AST.** On ne sait pas sans l'exécuter si `b` est un module ou un nom : le test considère `a` et `a.b`, ce qui suffit pour les deux règles.
- **`corpus.py` n'était pas formaté** depuis la tâche 8 (une liste de mois sur une ligne) : reformaté par `ruff format` dans ce commit, sans changement de contenu.

### J3 tâche 9 : CRAG

- **`application/crag.py`**, fonctions pures sur les ports `Retriever` et `LLMProvider` : `retrieve`, `grade` (juge, modèle léger), `rewrite` (modèle léger), `generate` (sans LLM). Sous-graphe compilé dans `adapters/langgraph/orchestrator.py` avec `checkpointer=False` (`build_crag_graph`), injecté dans les analystes par `crag_runner`.
- **Requêtes** à partir des seuls types, valeurs et catégories des clauses du domaine (`DOMAIN_KINDS`, nouvelle partition des `REQUIRED_KINDS`), jamais des citations : aucun texte du contrat n'atteint le CRAG (testé avec une citation piégée).
- **Juge** : extraits délimités (`<<<EXTRAIT n>>>`), réponse en numéros d'extraits. Un numéro hors liste ou répété lève `LLMOutputError`, jamais ignoré. Sans extrait, pas d'appel.
- **Versions** : `analysis_date` dans l'état (fixée par `run_contract`, exigée par `validate_input`) et dans `AnalystInput`. `generate` écarte toute référence expirée à cette date et la signale dans les constats ; si toutes les références pertinentes ont expiré, `INSUFFISANT`. Une fiche prend la plus proche des fins de validité des articles qu'elle cite.
- **Résumé pour l'audit** : `RetrievalTrace` (requêtes, passes, références retenues, références expirées) dans `AgentVerdict.retrieval`, ajouté à la liste des types autorisés du sérialiseur ; constats du CRAG ajoutés à ceux des règles.
- **Configuration** : section `crag` (`top_k` 4, `max_passes` 2).
- **Adaptateur `PgvectorRetriever`** (port `Retriever`) : requête en texte, vecteur par l'`Embedder`, filtre sur son modèle.
- **Critère 3** avec doublures, sur le graphe complet avec sérialiseur strict : domaine hors corpus → 2 passes, `INSUFFISANT`, `ESCALADE`, aucune référence qui n'ait été rendue par la recherche ; corpus vide → `INSUFFISANT` partout, juge jamais appelé.

**Défauts corrigés.**
- `rag_store.search` sélectionnait `valid_until` et `note` sans les transmettre (défaut signalé au plan de la refonte) : corrigé, testé.
- `sync` ne comparait que le texte : une fin de validité modifiée sans changement de texte restait en base. Il compare désormais toutes les métadonnées stockées (testé).

**Choix.**
- `evidence_ids` = références citables (« RGPD, art. 28 »), dédoublonnées, et non les identifiants de ligne : ceux-ci changent à chaque réingestion, les références restent lisibles et stables dans l'audit.
- Validité des fiches : la plus proche fin de validité de **toute** la fiche, pas extrait par extrait (plus prudent).
- Date d'analyse de la CLI : jour légal en France (`Europe/Paris`).

**Piège.** LangGraph déduit un schéma de l'annotation de type d'une fonction de routage. `read_route`, annotée `ContractState`, ajoutait les canaux du graphe principal au sous-graphe (« Channel 'usage' already exists with a different type ») : fonction de routage propre au CRAG, annotée `CragState`.

**Vérification réelle** (modèle d'embedding réel, hors ligne, sans LLM) :
- `ingest` après la tâche : 1 extrait remplacé (la fiche « pénalités de retard », désormais valable jusqu'au 2027-01-01), 56 inchangés ;
- `PgvectorRetriever` avec les requêtes initiales du CRAG (contrat favorable de test), 4 premiers extraits par domaine : juridique → fiche responsabilité, L442-1, 1231-3 ; financier → fiche pénalités (fin 2027-01-01), fiche révision des prix, L441-10 (fin 2027-01-01) ; conformité → fiche transferts, RGPD art. 46, fiche sous-traitance ; opérationnel → fiche durée et préavis, L442-1, 1211, 1210. La fin de validité remonte bien jusqu'au CRAG.

### J3 tâche 10 : gardes d'échec de nœud

- **`guard`** (`orchestrator.py`) enveloppe chaque nœud sauf `human_review`. Une exception devient un `NodeFailure` (nœud, type et message, tentatives, domaine d'un analyste) dans `failures`, clé à réducteur. `GraphBubbleUp` (interruption, commande) passe au travers.
- **Selon le nœud** : les nœuds à plusieurs sorties (`validate_input`, `verify_extraction`, `decision_gate`) routent vers l'humain en `ESCALADE`, d'où une nouvelle arête `validate_input → human_review` ; l'échec d'`extract_clauses` est escaladé par `verify_extraction`, qui le lit en premier ; un analyste en échec laisse les autres finir et `decision_gate` escalade ; `explain`, `audit_seal` et `reject` consignent l'échec et vont à leur terme.
- **`decision_gate`** : l'analyste en échec vient en 2ᵉ position, après le blocage dur. `failure_report` : `{"stage": "noeuds", "failures": [...]}`, budget compris s'il est dépassé.
- **Reprise avant la garde** : `RetryPolicy` de LangGraph sur le nœud `analyst`, réglée par la section `analyst_retry` (3 tentatives, 1 s, facteur 2, plafond 10 s, sans gigue). La garde lit `get_runtime().execution_info.node_attempt` : une erreur que la politique reprend est relancée tant qu'il reste des tentatives, et n'est consignée qu'après la dernière (testé : 2 échecs réseau puis réussite ; échec réseau persistant, 3 tentatives ; `ValueError`, 1 seule).
- **Statut d'un thread** : `failures` exposé par `thread_status`, donc par la CLI.
- **Changement de comportement voulu** : au J2, une exception de nœud arrêtait l'exécution (erreur JSON, code 1). Le test CLI correspondant vérifie désormais l'escalade avec rapport, puis la reprise par `resume`.

**Choix.**
- **Blocage dur avant l'analyste en échec.** La spec dit « `decision_gate` escalade dès que `failures` n'est pas vide », mais aussi que le blocage établi suffit (étape 1, « un `INSUFFISANT` simultané ne change rien »). J'ai gardé l'ordre des étapes : un blocage dur établi par un autre verdict donne `NO_GO`, avec le rapport d'échec tracé. À valider.
- **Pas de garde sur `human_review`** : tous les échecs y aboutissent, il ne peut pas se router vers lui-même. Une exception y arrête l'exécution, comme au J2.
- **Pas de `RetryPolicy` sur `extract_clauses`** (la spec la prévoit pour les analystes seulement). Les SDK reprennent déjà certaines erreurs (Anthropic : 2 reprises par défaut). Une extraction en échec part donc chez l'humain. À reconsidérer si les tests réels le montrent utile.

**Piège.** Une garde qui capte l'exception empêcherait `RetryPolicy` de la voir : la reprise de LangGraph enveloppe le nœud, donc la garde. D'où la lecture du numéro de tentative dans la garde, plutôt qu'une boucle de reprise maison.

### J3 tâche 11 : CLI sans le mode stub-j2

- `stub_j2.py` et ses tests supprimés ; plus d'option `--clauses` ni de clé `mode` dans les sorties.
- **`build_deps`** (racine de composition) : fournisseur LLM de la configuration, puis embedding local (`FastembedEmbedder`), puis `PgvectorRetriever` sur `rag_chunks` avec le rôle applicatif ; extracteur `LLMExtractor`, CRAG `crag_runner`. Le fournisseur d'abord : une clé absente échoue avant tout chargement de modèle et avant la création du thread (testé : aucun thread en base).
- **`resume`, `history`, `expire`** reçoivent des dépendances qui échouent explicitement si on les appelle : le graphe n'y repasse ni par l'extraction ni par le CRAG, donc aucune clé ni aucun modèle ne sont exigés (testé : `resume` ne construit pas les dépendances d'analyse).
- **`--analysis-date AAAA-MM-JJ`**, par défaut le jour légal en France ; `analysis_date` dans le statut.
- **Tests** : les dépendances réelles sont remplacées par des doublures (`cli.build_deps` substitué) ; `ingest` testé de bout en bout avec l'embedder de test ; le test de reprise après `SIGKILL` construit ses doublures dans le processus tué.
- README : démarrage complet (poids, ingestion, analyse, reprise).

### J3 tâche 12 : tests llm, série 1 (en cours, décision attendue)

`tests/test_llm_criteres.py`, marqueur `llm`, lancés par `uv run pytest --llm -m llm -s`. Chaque critère est paramétré 5 fois, sans relance automatique ; chaque essai imprime une ligne `LLM-RESULT`.
- **Critère 10** : extraction réelle (modèle `main`) d'un contrat synthétique réaliste (`tests/fixtures/contrat-synthetique-llm.txt`, parties masquées, **sans clause de pénalités de retard**, pour tenter une invention). Réussite : aucun échec de nœud ; si les analystes ont tourné, toutes les citations sont retrouvées dans le texte masqué ; sinon `ESCALADE` avec rapport d'extraction après 2 essais.
- **Critère 3** : vrai juge et vraie réécriture (modèle `light`) ; la recherche du domaine financier ne rend que des extraits réels hors sujet (RGPD art. 32 et 33, C. civ. 1211). Réussite : `INSUFFISANT` après 2 passes, sans référence, puis `ESCALADE` ; aucune référence retenue qui n'ait été rendue par la recherche. Témoin : le juridique, avec des extraits pertinents, doit rester `OK`.

**Série 1 : 2026-09-25, 09:18 UTC, fournisseur Mistral, `main` = `mistral-small-2603`, `light` = `ministral-8b-2512`.**

| Critère | Résultat | Détail |
| --- | --- | --- |
| 3 | **5/5** | Financier : 2 passes, `INSUFFISANT`, aucune référence, `ESCALADE` à chaque essai. Réécritures variées (« clauses contractuelles : révision des prix… »). Témoin juridique `OK` à chaque essai, en ne retenant que la fiche (jamais C. civ. 1231-3 ni 1170). 3 388 à 4 357 tokens par essai. |
| 10 | **0/5** | Chaque essai : HTTP 429 « Rate limit exceeded » dès le premier appel d'extraction. La garde l'a transformé en `NodeFailure` (`SDKError`, 1 tentative) et le contrat est parti en `ESCALADE` : comportement correct, mais le vrai modèle n'a jamais répondu, donc le test refuse de compter l'essai. |

**Analyse de l'échec du critère 10** (sans relancer la série) : liste des modèles et appels de 1 token, en lisant les en-têtes `x-ratelimit-*` :
- `mistral-small-2603` : listé, mais `x-ratelimit-limit-req-minute = 0` ;
- `mistral-medium-3-5` : 0 requête par minute aussi ; `mistral-large-2512` : 403, absent de la liste ;
- `ministral-8b-2512` : 188 requêtes et 625 000 tokens par minute ; `ministral-14b-2512` : 30 requêtes et 937 500 tokens par minute.

Ce n'est ni une erreur transitoire (aucune reprise n'y changerait rien) ni un défaut du code : le compte n'accorde aucun quota au modèle principal configuré. Correction soumise au propriétaire du repo : relever la limite de `mistral-small-2603` dans la console Mistral, ou changer `llm.models.mistral.main`. Série 2 du critère 10 ensuite.

**Observation.** Le juge léger est prudent : sur le témoin juridique, il ne retient que la fiche, jamais les articles 1231-3 et 1170, pourtant liés à la responsabilité. Sans effet sur les critères, mais à suivre au J4 (qualité des références citées par `explain`).

### J3 : rangement des couches (décision du 25/09, point 4)

Sans changement de comportement : les tests existants ne changent que par leurs imports, plus 3 tests qui appellent directement la nouvelle API du domaine.
- **`domain/state.py` scindé** : modèles métier (`Clause`, `AgentVerdict`, `HumanDecision`, `Usage`, `RetrievalTrace`, `NodeFailure`, types et constantes) dans `domain/models.py` ; `ContractState`, réducteurs, `Route` et `AnalystInput` dans `application/state.py`.
- **Logique des nœuds sortie vers le domaine** :
  - `domain/decision.py` : `aggregate`, `conflict`, `total_tokens`, `failure_report`, et `decide`, qui rend un `GateOutcome` (décision proposée, revue humaine ou non, marge, rapport d'échec ; décision finale sans revue humaine) ;
  - `domain/verification.py` : `normalize`, `problems_of`, et `check_extraction`, qui rend `verified`, `retry` ou `escalate` avec son rapport ;
  - `domain/input_checks.py` : mots-outils, `french_ratio`, et `rejection`, qui rend un motif de rejet ou `None`.
  Les nœuds `decision_gate`, `verify_extraction` et `validate_input` ne gardent que l'adaptation état → domaine → état ; la route reste un concept du graphe, le domaine rend une issue.
- **`domain/policy.build_request`** lit l'état comme un `Mapping` : le domaine n'importe plus `ContractState`.
- **ADR 002** : nouvelles couches, et écarts assumés (`config.py` qui lit un fichier et porte des réglages techniques, `ingestion.py` qui lit le corpus sans port, `ChunkRow` dans le domaine faute de port d'écriture, `build_request` sur un `Mapping`).

**Piège évité.** Un checkpoint enregistre le module de chaque objet sérialisé (`cdg.domain.state.Clause`, puis `cdg.domain.models.Clause`). Déplacer un modèle rend illisibles les threads écrits avant. Vérifié avant chacune des deux refontes : aucun thread en base. À retenir pour la suite : tout déplacement de modèle après le J4 exigera une migration des checkpoints, ou un alias de module.

### J3 : reprise de l'extraction sur erreur passagère (décision du 25/09, point 3)

- **Port** : `LLMTransientError` dans `ports/llm.py`, pour les erreurs passagères du fournisseur (429, 5xx, délai dépassé).
- **Adaptateurs** : Mistral la lève sur `MistralError` de statut 429 ou 5xx, et sur un délai dépassé ; Anthropic sur `APIStatusError` de statut 429 ou 5xx (dont 529, surcharge), et sur `APITimeoutError`. L'erreur du SDK reste la cause (`from`). Pour un 429 Mistral, le message reprend la limite du compte lue dans `x-ratelimit-limit-req-minute` (« limite du compte : 0 requête par minute » : le cas de la série 1).
- **Reprise** : `RetryPolicy` sur `extract_clauses` (section `extraction_retry` : 3 tentatives, 2 s, facteur 2, plafond 20 s, sans gigue), avec pour seul `retry_on` le test `isinstance(exc, LLMTransientError)`. La garde relance l'erreur tant qu'il reste des tentatives, comme pour les analystes. Une erreur non passagère (400, 401, connexion refusée, sortie non structurée) est consignée dès le premier essai. Le modèle de configuration `AnalystRetry` devient `RetrySettings`, commun aux deux sections.
- **Pas de reprise cachée** : les SDK ne reprennent rien (Anthropic `max_retries=0`, au lieu de 2 par défaut ; Mistral `retry_config=None`, déjà sans reprise par défaut). Les reprises ne se règlent que dans la configuration.
- **Tests** : traduction testée pour chaque statut et chaque SDK, sans réseau ; graphe : 2 erreurs passagères puis réussite (3 appels, aucun échec) ; erreur passagère persistante (3 tentatives, puis `ESCALADE`) ; `ConnectionError` et `ValueError` sans reprise.

**Choix.**
- **Connexion refusée non reprise.** Elle n'est pas dans la liste demandée (429, 5xx, délai dépassé), donc l'extraction part chez l'humain dès le premier essai. À élargir si les tests réels le justifient.
- **Délai dépassé côté Mistral reconnu par sa classe.** Le SDK transmet l'exception httpx telle quelle. Importer httpx, dépendance du SDK mais pas du projet, reviendrait à s'appuyer sur une dépendance non déclarée : l'adaptateur reconnaît `httpx.TimeoutException` dans la hiérarchie de l'exception, par son nom et son module.
- **Un 429 à quota nul est quand même repris.** Il est passager par son statut, mais permanent dans les faits : les 3 tentatives coûtent 6 s d'attente, puis l'échec est consigné avec la limite du compte dans le message.

### J3 tâche 12 : mesure du critère 10 (décision du 25/09, point 2)

Un système qui escaladerait toujours passerait le critère 10 : l'invariant (« aucun analyste sur une citation non vérifiée ») est vrai aussi quand rien n'aboutit. D'où une mesure, sans seuil jusqu'à la série 2 :
- **Taux d'aboutissement** : sur le contrat valide de test, nombre d'essais sur 5 qui aboutissent aux analystes avec toutes les citations vérifiées. Le contrat est valide : chaque clause stipulée se cite mot pour mot, et les pénalités de retard n'y figurent pas, donc une extraction correcte les déclare absentes.
- **Ligne `LLM-SERIE`** imprimée en fin de série : taux, aboutissements au premier essai, issue de chaque essai (`analystes`, `escalade`, `echec_de_noeud`), et nombre d'extractions exactes.
- **Écarts de valeur**, à titre d'information : chaque essai compare présence, valeur et catégorie de chaque clause aux valeurs attendues du contrat (par exemple `duree_engagement` = 24). Ils ne comptent pas dans le critère, qui porte sur les citations.
- **Vérifié sans réseau**, avec un faux fournisseur : une extraction exacte aboutit aux analystes sans écart ; une citation inventée pour les pénalités donne une ré-extraction avec le retour « citation introuvable: penalites_retard », puis `ESCALADE`. La ligne de série indique bien un taux de 1/2.

La série 1 (0/5, quota nul) n'entre pas dans la mesure : le vrai modèle n'a jamais répondu. Le taux sera consigné à partir de la série 2.

### J3 : connexion refusée reprise, quota nul non repris (décisions du 25/09)

- **Connexion refusée ou impossible** : ajoutée aux erreurs passagères. Mistral : `httpx.ConnectError`, reconnue par sa classe comme le délai dépassé ; Anthropic : `APIConnectionError` (hors `APITimeoutError`, testée d'abord).
- **Quota nul** : nouvelle erreur du port, `LLMQuotaError`, pour un 429 dont une limite du compte vaut 0. Mistral : `x-ratelimit-limit-req-minute` ou `x-ratelimit-limit-tokens-minute` ; Anthropic : `anthropic-ratelimit-requests-limit` ou `anthropic-ratelimit-tokens-limit`, noms vérifiés dans la documentation officielle (« Rate limits », platform.claude.com, 2026-09-25). Le message cite l'en-tête à 0 et dit de vérifier l'offre du compte, ou de changer de modèle dans la configuration. Elle n'est jamais reprise, ni sur l'extraction ni sur un analyste : la `RetryPolicy` des analystes garde le prédicat par défaut de LangGraph, qui reprendrait presque toute exception, mais l'exclut explicitement.
- **Tests** : traduction par en-tête et par fournisseur ; limite non nulle toujours passagère ; graphe : quota nul consigné dès la première tentative, sur l'extraction comme sur un analyste.

**Écart signalé, non traité.** La même page de la documentation Anthropic décrit un autre 429 non passager : le plafond de dépenses mensuel atteint (`error.details.error_code = enforced_spend_limit_reached`, sans en-tête `retry-after`). Il reste traité comme passager (3 tentatives, puis échec consigné). À traduire en `LLMQuotaError` si le propriétaire du repo le souhaite.

**Fichier en cours hors commit.** `data/corpus/raw/code-civil/1231-5.txt`, non suivi, contient la commande de création au lieu du texte de l'article ; `test_manifeste_couvre_tout_le_corpus` échoue tant qu'il n'est ni corrigé et admis dans le manifeste, ni retiré. Sans lui : 508 tests au vert.

### J3 : pénalités d'exécution et délai de paiement (décisions du 25/09)

**Ce que désignait `penalites_retard`.** Trois lectures incompatibles :
- **Règle financière** : « pénalités absentes ou plafond < 5 % → pénalité de score », une valeur nulle (non plafonnées) étant favorable. C'est l'intérêt de l'acheteur, donc des pénalités dues par le **fournisseur** qui exécute en retard.
- **Prompt d'extraction** : « plafond des pénalités de retard, en pourcentage », sans dire qui les doit ni en pourcentage de quoi. Dans un contrat qui prévoit aussi des intérêts de retard de paiement (obligatoires selon L441-10, II), le modèle pouvait extraire l'une ou l'autre clause.
- **Fiche et corpus** : la fiche « pénalités de retard » et la requête du CRAG (« pénalités de retard ») menaient à L441-10, qui vise les pénalités dues par l'**acheteur** en retard de paiement. Le domaine financier était donc étayé par un texte sur l'autre partie.

**Correction.**
- **`penalites_execution`** remplace `penalites_retard` : pénalités du fournisseur (clause pénale, C. civ. 1231-5), plafond en % du montant du contrat, règle inchangée (clés de configuration `execution_penalties_*`).
- **`delai_paiement`** : délai de paiement par l'acheteur, en jours, catégorie `date_facture` ou `fin_de_mois`. Pénalité de 0,2 au-delà de 60 jours après la facture ou de 45 jours fin de mois, ou si le délai est présent mais non chiffré, avec le constat « délai non conforme, à renégocier ». Absent : aucune pénalité, mais un constat qui cite le délai supplétif du texte.
- **Délai supplétif** : L441-10, I, 1er alinéa, « sauf dispositions contraires […], le délai de règlement des sommes dues ne peut dépasser trente jours après la date de réception des marchandises ou d'exécution de la prestation demandée ». Cité dans le constat (constante de la règle, liée à la version en vigueur jusqu'au 01/01/2027).
- **Catégories par type** (`KIND_CATEGORIES`) : un délai chiffré exige son point de départ ; un délai non chiffré peut s'en passer. Une catégorie d'un autre type est signalée « catégorie invalide ». Sans point de départ (impossible après vérification), la règle applique le seuil le plus strict (45 jours).
- **Corpus** : C. civ. 1231-5 admis dans le manifeste (financier, récupéré le 25/09/2026 : nouvelle clé `retrieved_at_overrides`, date propre à un article) et dans `SOURCES.md`.

**Pire cumul recalculé : 0,505** (juridique 0,5, financier 0,4, conformité 0,7, opérationnel 0,4), soit `GO_RESERVES` avec une marge de 0,005, donc en revue humaine. `NO_GO` reste réservé aux blocages durs, mais de justesse ; un test fige ce calcul.

**Écart relevé dans L441-10, non couvert par la règle** : « en cas de facture périodique […], le délai convenu […] ne peut dépasser quarante-cinq jours après la date d'émission de la facture » (I, 4e alinéa). Un délai de 50 jours date de facture sur factures périodiques passe la règle.

### J3 : transferts, clauses types et clauses ad hoc (relecture des fiches, 25/09)

- **Deux sortes de clauses contractuelles**, que la règle confondait :
  - les clauses types de protection des données, adoptées par la Commission, ou par une autorité de contrôle puis approuvées par la Commission, qui valent garantie sans autorisation (RGPD, art. 46, par. 2, c et d) : catégorie `clauses_contractuelles_types`, inchangée ;
  - les clauses contractuelles ad hoc, soumises à l'autorisation de l'autorité de contrôle (par. 3, a) : deux nouvelles catégories, `clauses_contractuelles_ad_hoc` (autorisation non mentionnée) et `clauses_contractuelles_ad_hoc_autorisees` (autorisation mentionnée).
- **Règle** : les clauses ad hoc autorisées sont une garantie reconnue (`transfer_safeguards`) ; les clauses ad hoc sans mention d'autorisation (`transfer_authorization_to_verify`) donnent une pénalité et le constat « autorisation de l'autorité de contrôle à vérifier ». La configuration refuse une catégorie à la fois reconnue et à vérifier.
- **Montant de la pénalité : 0,3**, non fixé par le propriétaire du repo. Choisi égal à celui de la localisation non précisée, autre constat « à vérifier » du même domaine ; les deux portent sur la même clause et ne se cumulent pas, donc le pire cumul (0,505) ne change pas. À valider.
- **Dérogations de l'art. 49** (hors corpus) : le prompt les classe en `aucune_garantie`, donc bloquées ; limite ajoutée au README.

### J3 : relecture des fiches, deux sections, vérification mot à mot (25/09)

**Structure.** Chaque fiche a deux sections : « Ce que dit le texte », uniquement des paraphrases fidèles, chacune sourcée ; « Comment le projet l'applique », les règles et seuils du projet, présentés comme des choix de politique d'achat. Tests : structure exacte (deux sections, dans cet ordre, aucune affirmation hors section), source admise obligatoire dans la première section, citations facultatives mais admises dans la seconde. 7 fiches : `penalites-retard.md` devient `delais-paiement.md` (L441-10), et `penalites-execution.md` est créée (C. civ. 1231-5).

**Écarts trouvés à la vérification mot à mot** (au-delà de ceux signalés par le propriétaire du repo) :
- **sous-traitance-rgpd** : la fin de prestation omettait la destruction des copies existantes et la réserve d'une conservation exigée par le droit (art. 28, par. 3, g) ; l'exception « à moins qu'il ne soit tenu d'y procéder » manquait pour les instructions (par. 3, a) ; l'obligation d'informer immédiatement d'une instruction illicite (fin du par. 3), rattachée par le texte au point h, n'était pas reprise ; la définition du sous-traitant était abrégée (art. 4, point 8).
- **transferts-hors-ue** : en plus du « destinataire », la liste des garanties sans autorisation (art. 46, par. 2) est limitative dans le texte ; la paraphrase ne dit plus « notamment » (le « notamment » du par. 3 est, lui, dans le texte).
- **responsabilite-plafonds** : « entre professionnels » ne figure pas dans L442-1, qui vise « toute personne exerçant des activités de production, de distribution ou de services », « dans le cadre de la négociation commerciale, de la conclusion ou de l'exécution d'un contrat » : la paraphrase reprend ces termes.
- **duree-preavis, L442-1 (version du 20/08/2026)** : la paraphrase du II omettait la référence aux usages du commerce ou aux accords interprofessionnels, la détermination du prix pendant le préavis par référence aux conditions économiques du marché, et le nouvel alinéa sur la réduction substantielle des volumes de commandes ; les trois sont repris.
- **penalites-retard (devenue delais-paiement)** : « un délai de quarante-cinq jours fin de mois » au lieu d'« un délai maximal » ; le délai supplétif de trente jours (I, 1er alinéa) et le plafond de quarante-cinq jours pour les factures périodiques (I, 4e alinéa) manquaient ; « une analyse postérieure doit s'appuyer sur la version suivante » n'est pas dans le texte et passe dans l'application.
- **revision-prix** : conforme ; seulement restructurée. La 2e puce (« doit donc s'appuyer sur un indice… ») est une déduction du texte plutôt qu'une paraphrase ; laissée dans la première section, puisque la fiche a été jugée conforme.

**Formulations demandées, rendues autrement par fidélité au texte** :
- « un plafond ne joue pas en cas de faute lourde ou dolosive (art. 1231-3) » : l'article écarte la limitation aux dommages prévisibles en cas de faute lourde ou dolosive ; il ne dit rien d'un plafond conventionnel. La fiche le dit ainsi, et renvoie la conséquence sur un plafond à la jurisprudence, hors corpus.
- « L442-1 vise les relations entre professionnels » : rendu par les termes du texte (personnes exerçant des activités de production, de distribution ou de services).
- « engagement de confidentialité » : le texte ajoute « ou soient soumises à une obligation légale appropriée de confidentialité », repris.

**Défaut de corpus signalé, non corrigé** : `raw/rgpd/art-32.txt` répète sa ligne de titre (« Article 32 » deux fois). L'intitulé ingéré est donc « Article 32 », et « Sécurité du traitement » passe dans le texte. Correction du fichier par le propriétaire du repo, ou règle de nettoyage testée, à décider.

**Réingestion réelle** (modèle réel, hors ligne) : 67 extraits (17 insérés, 7 supprimés, 50 inchangés). Recherches de contrôle avec les requêtes initiales du CRAG : juridique → fiche responsabilité, L442-1, 1231-3 ; financier → fiche délais de paiement (fin 2027-01-01), fiche pénalités d'exécution, L441-10 ; conformité → fiches transferts et sous-traitance, RGPD art. 46 ; opérationnel → fiche durée et préavis, L442-1, 1211.

**Observation** : le domaine financier porte désormais trois sujets dans une seule requête. Avec `crag.top_k` = 4, la fiche sur la révision des prix et L112-2 sortent des 4 premiers extraits, et l'art. 1231-5 n'y entre pas (sa fiche, si). À suivre dans les séries `llm` : relever `top_k` ou faire une requête par clause, si le juge manque de références.

### J3 : délai de paiement, factures périodiques (décision du 25/09, point 3)

Nouvelle catégorie `facture_periodique` pour `delai_paiement` : plafond de 45 jours après la facture (L441-10, I, 4e alinéa), réglé par `payment_delay_max_days_periodic_invoice`, même pénalité et même constat. Le prompt la place en premier : un délai sur facture périodique est classé ainsi, même s'il est compté « fin de mois ». Point de départ inconnu : le plus bas des trois seuils. Fiche `delais-paiement` mise à jour (application).

### J3 : titre en double dans RGPD art. 32 (décision du 25/09, point 4)

`raw/rgpd/art-32.txt` commençait par deux lignes « Article 32 ». Seule la seconde est supprimée : diff d'une ligne, texte inchangé. L'intitulé ingéré redevient « Sécurité du traitement ». Nouveau test : `test_aucun_fichier_brut_ne_repete_sa_ligne_de_titre` échoue si la première ligne d'un fichier brut y apparaît une seconde fois (vérifié rouge sur l'ancien fichier, et seulement sur lui).

### J3 : CRAG, une requête par type de clause (décision du 25/09, point 5)

- **Une requête par type de clause** du domaine, construite à partir de la seule clause (type, valeur, catégorie). Le sous-graphe traite une clause ; `crag.per_clause` l'invoque pour chaque type de `DOMAIN_KINDS`, dans l'ordre, et `crag.combine` rassemble les résultats. Le juge et la réécriture voient le sujet de la clause ; leurs nœuds de consommation sont nommés par clause (`crag_grade:financier:delai_paiement`).
- **Rattachement** : `RetrievalTrace` contient une entrée `ClauseRetrieval` par type de clause (requêtes, passes, références retenues, références expirées). Les `evidence_ids` du verdict sont l'union des références retenues. `ClauseRetrieval` est ajouté à la liste des types autorisés du sérialiseur.
- **Statut du domaine** (non précisé par la décision, choix à valider) : `INSUFFISANT` dès qu'une clause du domaine n'a aucune référence en vigueur, avec un constat qui nomme la clause. C'est la lecture prudente : chaque clause jugée par une règle doit être étayée. Conséquence : plus d'escalades qu'avant, où une seule référence suffisait pour tout le domaine.
- **`top_k` par requête** : inchangé dans la configuration (4) ; commentaire précisé.
- **Coût** : 10 recherches par contrat au lieu de 4, donc jusqu'à 10 appels au juge, et 20 en cas de réécriture partout.
- **Doublure `FakeLLM`** : une réponse prévue pour un nœud vaut pour ses sous-nœuds (`crag_grade:financier` pour chaque clause du domaine) ; une réponse propre à une clause reste possible.
- **Test `llm` du critère 3** adapté (2 passes par clause financière ; témoin juridique justifié clause par clause), vérifié sans réseau avec un faux juge.

**Réingestion réelle après les corrections du corpus** (art. 32, fiches `delais-paiement` et `revision-prix`) : 3 extraits remplacés, 64 inchangés, 67 au total.

### J3 tâche 12 : second contrat de mesure du critère 10 (décision du 25/09)

- **`tests/fixtures/contrat-synthetique-complet.txt`** : les 10 types présents. Valeurs attendues : responsabilités 100 % et 120 %, révision 2 %, pénalités d'exécution plafonnées à 8 % du montant du contrat, paiement à 45 jours fin de mois, durée 36 mois, préavis 6 mois, données personnelles et accord de traitement présents, transfert encadré par les clauses types de la Commission.
- **Un piège** : des pénalités de retard de paiement dues par l'acheteur (trois fois le taux d'intérêt légal, indemnité forfaitaire), qu'une extraction correcte ne prend ni pour des pénalités d'exécution ni pour un délai de paiement.
- **Mesure séparée** : le test du critère 10 est paramétré par contrat (`valide`, `complet`) ; une ligne `LLM-SERIE` par contrat. 15 tests `llm` en tout.
- **Vérifié sans réseau** : une extraction exacte simulée du contrat complet aboutit aux analystes au premier essai, sans écart de valeur ; le masquage des parties ne casse aucune citation.

### J3 tâche 12 : diagnostic du quota après activation du paiement à l'usage, série 2

**Diagnostic (25/09/2026, 12:52 UTC).** Le propriétaire du repo a activé le paiement à l'usage (limite de dépenses de 5 € par mois ; console : 20 000 tokens par minute et 1 requête par seconde pour `mistral-small-2603`). Un appel d'un token répond : HTTP 200. Les en-têtes de l'API annoncent 100 requêtes et 100 000 tokens par minute. **Option A : la configuration ne change pas.** Les calculs retiennent la limite la plus stricte, celle de la console.

**Taille d'une extraction** (vrais prompts avec leur schéma, un seul token de sortie) : 1 535 tokens d'entrée pour le contrat valide, 1 706 pour le contrat complet. `max_tokens` n'est pas réservé : l'API ne compte que les tokens consommés (23 pour une réponse courte avec `max_tokens` = 4096). Une extraction coûte donc environ 2 100 à 2 400 tokens, bien sous la limite. Pour ne pas provoquer soi-même un 429 en enchaînant les essais, le critère 10 est cadencé : après un essai qui a consommé t tokens, attente de t × 60 / 20 000 s.

**Série 2 : 2026-09-25, 12:53 à 12:55 UTC, fournisseur Mistral, `main` = `mistral-small-2603`, `light` = `ministral-8b-2512`. 15 réussites sur 15, sans relance, en 1 min 59 s.**

| Critère | Résultat | Détail |
| --- | --- | --- |
| 10, contrat valide | **5/5** | Taux d'aboutissement : **5/5**, tous au premier essai, 5 extractions exactes (pénalités d'exécution et délai de paiement déclarés absents). 2 063 à 2 080 tokens par essai. |
| 10, contrat complet | **5/5** | Taux d'aboutissement : **5/5**, tous au premier essai, 5 extractions exactes, piège évité à chaque essai (les pénalités de retard de paiement de l'acheteur ne sont prises ni pour des pénalités d'exécution ni pour un délai de paiement). 2 356 à 2 365 tokens par essai. |
| 3 | **5/5** | Financier : chacune des 3 clauses fait 2 passes, aucune référence, `INSUFFISANT`, puis `ESCALADE`. Témoin juridique `OK` : responsabilité de l'acheteur justifiée par la fiche, C. civ. 1231-3 et 1170 ; responsabilité du fournisseur par la fiche. Environ 10 200 tokens par essai (11 appels au modèle léger). |

Consommation de la série : environ 22 000 tokens du modèle principal et 51 000 du modèle léger, moins de 0,02 €.

**Observations.**
- La requête par clause règle l'observation de la série 1 : le juge retient désormais les articles 1231-3 et 1170 pour la responsabilité de l'acheteur.
- **La réécriture comprend « domaine : financier » comme « services financiers »** (« prestataire de services financiers », « contrat financier »). C'est sans effet sur le critère, mais la reformulation part hors sujet. Piste : nommer le domaine par ce qu'il couvre (« conditions financières du contrat ») dans la requête et dans le prompt de réécriture.
- **Seuil du taux d'aboutissement** : à fixer par le propriétaire du repo, maintenant que la série 2 est faite.

### J3 : seuil du taux d'aboutissement du critère 10 (décision du 26/09, point 1)

- **Seuil** : au moins 4 essais sur 5 aboutissent aux analystes, par contrat de mesure. L'invariant de sûreté (aucun analyste sur une citation non vérifiée) reste exigé à chaque essai : 5 sur 5.
- **Test** : `test_10_taux_d_aboutissement_aux_analystes`, paramétré par contrat, défini après les essais et donc exécuté après eux ; il juge la série qui vient de tourner. Une série incomplète (essais désélectionnés ou interrompus) échoue explicitement, « taux non jugé ». 17 tests `llm` au lieu de 15.
- **Vérifié sans réseau** : 5/5 et 4/5 passent, 3/5 échoue avec le taux et le seuil dans le message ; lancé seul, le test échoue sur une série vide.

### J3 : domaines nommés par ce qu'ils couvrent dans le CRAG (décision du 26/09, point 2)

- **`crag.DOMAIN_LABELS`**, dans la requête (« libellé ; sujet de la clause : valeur ») et dans les messages du juge et de la réécriture (« Domaine : libellé ») :
  - financier : « conditions financières du contrat : prix, paiement, pénalités » (libellé de la décision) ;
  - juridique : « responsabilité contractuelle des parties : plafonds de responsabilité » ;
  - conformité : « protection des données personnelles : sous-traitance, transferts hors de l'Union européenne » ;
  - opérationnel : « durée et fin du contrat : engagement, préavis de résiliation ».
- **Les quatre noms sont jugés ambigus** : dans un corpus entièrement juridique, « juridique » ne dit rien du sujet ; « conformité » ne dit pas à quoi ; « opérationnel » peut désigner l'exploitation ou la logistique.
- **Étendu au juge** (non demandé explicitement) : il recevait aussi « Domaine : financier », avec le même risque de lecture.
- Les noms des nœuds de consommation gardent la clé du domaine (`crag_grade:financier:delai_paiement`).

### J3 : règles d'abord, CRAG sur les seules clauses qui portent un constat (décision du 26/09, point 3)

**Constat de départ.** La règle `INSUFFISANT` du 25/09 exigeait une référence pour chaque clause du domaine, même sans constat. Mesurée sur le contrat valide, elle escaladait un contrat sans aucun constat en juridique ni en opérationnel : le juge ne retenait rien pour la responsabilité de l'acheteur ni pour la durée d'engagement.

**Étude de l'inversion.** Les règles lisaient le statut de récupération pour deux choses seulement : ajouter le constat « référentiel insuffisant » et remplir `retrieval_status`. Aucun score, aucune pénalité, aucun blocage n'en dépendait. L'inversion est donc propre :
- **Règles** : signature `(clauses, config) -> Assessment`, sans statut. Chaque constat (`RuleFinding`) porte la clause qui le déclenche et son effet : `blocage`, `penalite` (avec son montant) ou `information`. Score et blocage se déduisent des constats. Rattachements choisis : « données personnelles sans accord » va à `accord_traitement_donnees`, la clause manquante ; « localisation non précisée » va à `transfert_hors_ue`.
- **Analyste** : règles, puis CRAG sur les clauses qui portent un constat (dans l'ordre des clauses), puis `justification.justify`. Le CRAG est toujours appelé : avec aucune clause, il ne fait aucune recherche et rend un résumé vide. Les tests des gardes, qui injectent les pannes par le CRAG, en dépendent.
- **Justification** (`domain/justification.py`, pur) :
  - un constat qui bloque ou pénalise, sans référence en vigueur : `INSUFFISANT`, avec « référentiel insuffisant : aucune référence en vigueur pour justifier le constat de la clause … » ;
  - une clause à justifier absente du résumé du CRAG compte comme non justifiée, par prudence ;
  - une clause sans constat n'exige rien.
- **Choix à valider** : **constat d'information** (délai supplétif de L441-10, transfert encadré par une garantie). La décision ne tranche pas ce cas. Il est recherché, puisqu'il a un constat. Sans référence, il est signalé (« information seule, sans effet sur le statut ») mais ne rend pas le domaine `INSUFFISANT`, car il ne déclenche ni pénalité ni blocage.
- **CRAG** : il ne décide plus de statut. `RetrievalResult` perd `status` et `evidence_ids` ; le résumé devient obligatoire. `per_clause` cherche les clauses reçues, dans l'ordre reçu ; une clause hors du domaine ou répétée lève une erreur.
- **Doublure `FakeCrag`** : une référence par clause reçue, aucune pour les domaines `empty` ; elle enregistre les clauses reçues. Les tests qui voulaient un domaine `INSUFFISANT` avec un contrat favorable passent désormais une clause qui porte un constat (`PENALIZED`, ou pénalités d'exécution absentes) : sans constat, plus d'`INSUFFISANT` possible.
- **Critère 1** : fan-out avec une pénalité par domaine, pour que chaque verdict porte sa propre référence (`GO_RESERVES`, 0,615). Nouveau test : un contrat sans constat, corpus vide partout, rend `GO` sans aucune recherche.
- **Critère 3** : libellé « constat sans référence ». Test `llm` adapté : financier avec deux pénalités (pénalités d'exécution absentes, délai de 90 jours date de facture), la révision n'étant pas recherchée ; témoin juridique sur le plafond fournisseur à 50 %. Vérifié sans réseau avec un faux juge sur les extraits réels.

**Nombre de recherches sur les deux contrats de mesure** (CRAG réel : pgvector, e5, juge `ministral-8b-2512`, clauses attendues, date d'analyse 26/09/2026) :

| Contrat | Avant (une recherche par type) | Après (clauses à constat) |
| --- | --- | --- |
| valide | **15** recherches, 24 573 tokens ; juridique et opérationnel `INSUFFISANT`, donc `ESCALADE` | **2** recherches (pénalités d'exécution absentes, délai de paiement absent), 3 695 tokens ; tout `OK` |
| complet | **13** recherches, 22 335 tokens ; tout `OK` | **1** recherche (transfert encadré par les clauses types), 1 854 tokens ; tout `OK` |

La mesure « avant » précède aussi les libellés de domaine : les deux changements jouent sur les références retenues, pas sur le nombre de clauses recherchées. Sur le contrat complet, le juge retient l'art. 28 et la fiche sur la sous-traitance pour la clause de transfert, un rattachement lâche.

### J3 tâche 12 : série 3 des tests `llm`, critère 3 seul (CRAG modifié)

**Série 3 : 2026-09-26, 03:54:45 à 03:55:00 UTC, fournisseur Mistral, `light` = `ministral-8b-2512` (seul modèle appelé : l'extraction est en doublure). 5 réussites sur 5, sans relance, en 15 s.**

| Critère | Résultat | Détail |
| --- | --- | --- |
| 3 | **5/5** | Financier : seules les deux clauses qui portent un constat sont recherchées (pénalités d'exécution absentes, délai de 90 jours date de facture), la révision de prix ne l'est pas. Chacune fait 2 passes, aucune référence, d'où `INSUFFISANT` avec un constat par clause, puis `ESCALADE`. Témoin juridique `OK` : plafond fournisseur à 50 %, justifié par la fiche sur les plafonds de responsabilité. Environ 6 600 tokens par essai (7 appels au modèle léger), contre 10 200 en série 2. |

**Observations.**
- **Réécriture dans le sujet** : avec les libellés de domaine, plus aucune dérive vers « services financiers ». Reformulations obtenues : « sanctions contractuelles pour retard ou inexécution par le fournisseur… » et « modalités de règlement contractuel : échéance de paiement à la charge de l'acquéreur… ».
- **Stabilité** : quatre essais sur cinq donnent des reformulations identiques mot pour mot (température 0) ; l'essai 3 varie légèrement.
- **Témoin juridique** : pour le plafond du fournisseur, le juge ne retient que la fiche, pas les articles 1231-3 ni 1170, comme en série 2 pour cette clause.

### Décision du 26/09 : constat d'information

- **Traitement validé** : un constat d'information (délai supplétif de L441-10, transfert encadré par une garantie) est recherché ; sans référence, il est signalé (« information seule, sans effet sur le statut ») et ne rend jamais le domaine `INSUFFISANT`.
- **Noté pour le J4** : le juge léger rattache l'art. 28 du RGPD à la clause de transfert, un rattachement lâche. C'est à surveiller pour `explain`, qui ne doit citer que des références pertinentes.

### Test d'`ingest` : environnement déclaré par le test

- **Symptôme** : en environnement de CI simulé (arbre de travail propre, sans `.env`), `test_ingest_indexe_le_corpus_puis_rejouable` échoue : `SettingsError`, `EMBEDDING_CACHE_DIR` absent. Il passe sur le poste de développement.
- **Cause** : la commande `ingest` lit `EMBEDDING_CACHE_DIR` avant de construire l'embedder. Le test remplaçait l'embedder par une doublure, mais la variable venait en silence du `.env` du poste. C'est une dépendance cachée du test, pas un défaut de l'application : la variable absente produit bien une erreur explicite.
- **Correction** : le test déclare lui-même `EMBEDDING_CACHE_DIR` (dossier temporaire) et vérifie que la commande le transmet à la fabrique de l'embedder. La CI ne définit pas cette variable : un autre test qui en dépendrait en silence échouerait aussi.

### Intégration continue GitHub Actions (branche `ci`)

**Fait.** `.github/workflows/ci.yml`, sur push vers `main` et sur chaque pull request, avec deux jobs sur `ubuntu-24.04` :
- **`lint`** : `ruff format --check` et `ruff check`, avec le seul groupe `dev` installé (`uv sync --locked --only-group dev`, puis `uv run --no-sync`). Aucune dépendance lourde n'est installée.
- **`tests`** :
  - service PostgreSQL : image de `docker-compose.yml`, même empreinte ;
  - migrations par le script du dépôt, `docker/initdb/00_migrate.sh`, copié et exécuté dans le conteneur ;
  - `setup-db`, puis `uv run --no-sync pytest`, tests `pg` compris, tests `llm` exclus par défaut.
- Badge en tête du README ; section « Intégration continue » dans CLAUDE.md et dans la spec.

**Choix.**
- **Actions épinglées par empreinte de commit**, version en commentaire : `actions/checkout` v7.0.1 et `astral-sh/setup-uv` v10.2.0, dernières versions publiées, vérifiées par l'API GitHub le 26/09. Une étiquette de version peut être déplacée, une empreinte non.
- **Entrées vérifiées** dans les `action.yml` à ces empreintes : `persist-credentials`, `version`, `python-version`, `enable-cache`.
- **uv 0.6.10**, la version du poste de développement, qui a écrit `uv.lock` (révision 1) : `--locked` juge le lock avec le même outil.
- **Migrations** : un conteneur de service démarre avant le checkout, il ne peut donc pas monter `docker/initdb` comme `docker-compose.yml`. Le script est exécuté tel quel dans le conteneur (`docker cp`, puis `docker exec … bash -s`), par le socket local, comme à l'initialisation d'un volume vide.
- **Mots de passe de la base en clair dans le workflow** : la base est jetable, détruite avec le job, et joignable seulement depuis le runner. Ce ne sont pas des secrets. Aucune clé d'API.
- **`HF_HUB_OFFLINE=1`** : garde-fou, un téléchargement du modèle d'embedding échouerait au lieu de tirer 2,2 Go.
- **`EMBEDDING_CACHE_DIR` volontairement absent**, ainsi que tout ce qui vient d'un `.env`, pour que les dépendances cachées à l'environnement du poste se voient.
- **`timeout-minutes`** : 10 pour le lint, 20 pour les tests.

**Vérifié avant de pousser**, par une simulation locale de la CI :
- un arbre de travail propre, sans `.env` ni poids du modèle, et `env -i` avec les seules variables du workflow ;
- une base jetable, même image et même empreinte, migrée par le même `docker cp` et `docker exec`.

Résultats :
- premier passage : 580 réussites, 1 échec, dû à la dépendance cachée du test d'`ingest` (entrée précédente) ;
- après la correction du test : 581 réussites ;
- lint : dans un environnement neuf avec le seul groupe `dev`, `ruff format --check` et `ruff check` passent.

La simulation tourne sur macOS : un écart propre à Linux ne peut se voir que sur GitHub.

## 2026-09-26 · CI, qualité (branche `ci-qualite`)

### Vérification des types : mypy

**Choix de mypy plutôt que pyright.**
- **Installation figée** : mypy est un paquet Python pur (compilé par mypyc), figé dans `uv.lock` comme le reste. Le paquet PyPI de pyright, lui, cherche Node.js, le télécharge au besoin, puis installe le paquet npm de pyright à la première exécution (documentation du paquet sur PyPI) : ce téléchargement échappe à `uv.lock`, en CI comme en local.
- **Pydantic** : son plugin mypy officiel type les `__init__` synthétisés (`init_typed`, `init_forbid_extra`, lus dans la documentation de pydantic).
- **Limite de mypy** : `strict` ne se règle que globalement (documentation de mypy 2.3.1). Les options qu'il active (liste de `mypy --help`) sont donc reprises une à une pour `cdg.domain.*`, `cdg.ports.*` et `cdg.application.*`. Deux d'entre elles ne se règlent que globalement : `warn_redundant_casts` et `extra_checks`. Elles valent donc aussi pour `adapters/` et `cli.py`, au-delà du mode de base.

**Erreurs trouvées** : 74 dans 26 fichiers.
- 42 dans les couches strictes : 28 paramètres génériques manquants (`dict` au lieu de `dict[str, Any]`, `re.Match` sans type), des annotations manquantes, et quelques valeurs `None` que mypy ne pouvait pas exclure (catégorie d'une clause, fin de validité d'un extrait).
- 32 dans les adaptateurs, dont 25 dans l'orchestrateur :
  - typage de LangGraph (nœuds gardés, `RunnableConfig`) ;
  - valeurs `None` que LangGraph, psycopg ou le SDK Mistral déclarent possibles.

Toutes sont corrigées sans `# type: ignore` et sans changer le comportement nominal. Un seul `cast` : la clé du fournisseur dans `LLMConfig.model`, où un fournisseur inconnu lève toujours `KeyError`.

**Chemins d'erreur rendus explicites.** Chacun échouait avant par une erreur Python générique, sur un cas qui ne se produit pas dans l'usage actuel :
- `MistralProvider` : un choix sans message (le SDK le type optionnel) lève `LLMOutputError` au lieu d'`AttributeError` (testé) ;
- `FastembedEmbedder` sans modèle injecté ni dossier de cache : `EmbeddingError` qui cite `EMBEDDING_CACHE_DIR`, au lieu d'un `EmbeddingError` citant « None » (testé) ;
- `rag_store.column_dimension` : colonne absente, `RagStoreError` ;
- orchestrateur :
  - garde hors d'une exécution de nœud : `RuntimeError` ;
  - `expire` sur un graphe sans checkpointer : `ThreadError` ;
  - checkpoint sans métadonnées ou sans date : `ThreadError` ;
- `setup_database` : `TypeError` si le checkpointer tenait un pool au lieu d'une connexion.

**Écart réel trouvé par le typage : la règle de reprise de la garde.**
- La garde appelait `retry.retry_on(exc)`, alors que LangGraph accepte aussi une classe ou une liste d'exceptions, qu'il ne faut pas appeler. Nos politiques passent toujours un prédicat, donc rien ne cassait.
- `orchestrator.retries` reprend la règle de LangGraph (`pregel/_retry.py`, `_should_retry_on`). Un test la compare à la fonction de LangGraph sur 16 combinaisons : politique × erreur.

**Autres points.**
- `langchain_core` (pour `RunnableConfig`) est importé dans `adapters/langgraph/` : ajouté à la liste des bibliothèques confinées du test d'isolation.
- **PyYAML sans annotations** : `ignore_missing_imports` pour `yaml`. `yaml.safe_load` rend `Any` de toute façon. Les stubs de typeshed (`types-PyYAML`) seraient une dépendance de plus : non ajoutés sans accord.
- **Domaines d'un extrait** : lus dans le manifeste et les fiches, ils sont validés par `ChunkRow.model_validate` au lieu du constructeur typé (même validation pydantic).
- **Job `types`** dans la CI : tout le projet est installé, car mypy lit les types des bibliothèques.

### Audit de sécurité des dépendances : pip-audit

**Outil** : pip-audit 2.10.1, dernière version publiée, maintenue par la PyPA. Son README a été lu à cette étiquette.
- **Pas de lecture de `uv.lock`** : `--locked` ne lit que `pyproject.toml` et `pylock.*.toml`. La voie documentée pour un projet déjà résolu est un fichier de requirements entièrement figé, avec les empreintes : `--require-hashes`, plus `--disable-pip`, qui évite toute résolution par pip.
- **Codes de sortie documentés** : 0 sans faille connue, 1 si au moins une faille est trouvée. Le code de sortie ne peut pas être supprimé.
- **Base consultée** : par défaut, les vulnérabilités publiées par l'API JSON de PyPI.

**Mise en œuvre.**
- Nouveau groupe de dépendances `audit` (`pip-audit`), figé dans `uv.lock` et installé seul dans le job (`uv sync --locked --only-group audit`) : ni le projet ni ses dépendances n'y sont installés.
- `uv export --locked --all-groups --no-emit-project` : tous les groupes, sans le projet lui-même, qui n'est pas publié. Le résultat compte 104 paquets, avec leurs empreintes.
- `pip-audit --require-hashes --disable-pip --strict` : `--strict` fait aussi échouer l'audit si un paquet est introuvable, plutôt que de le passer sous silence.

**Vérifié** :
- sur `uv.lock` : « No known vulnerabilities found », code 0 ;
- sur `requests==2.19.1` : failles listées avec leur version corrigée, code 1.

**Limite** : l'audit tourne sur chaque pull request et chaque push vers `main`. Une faille publiée entre deux commits n'est vue qu'au suivant. Un déclenchement planifié le couvrirait (non ajouté, à décider).

### Dependabot

**Fait.** `.github/dependabot.yml`, deux écosystèmes :
- **`uv`** : `pyproject.toml` et `uv.lock` ;
- **`github-actions`** : `.github/workflows`.

Réglages communs : chaque semaine, le lundi à 6 h, heure de Paris ; préfixes de commit « Dépendances : » et « CI : ».

**Vérifié dans la documentation de GitHub** (source du dépôt `github/docs`) :
- `uv` est un écosystème pris en charge, mis à jour par Dependabot avec uv v0.11, mises à jour de sécurité comprises ;
- pour `github-actions`, `directory: "/"` couvre `.github/workflows` ;
- `day`, `time` et `timezone` valent pour un intervalle hebdomadaire ;
- un préfixe terminé par une espace évite le deux-points ajouté.

**Compatibilité de `uv.lock`.** Dependabot réécrit le lock avec uv 0.11, alors que le poste et la CI sont en uv 0.6.10. Essai sur une copie du dépôt :
- uv 0.11.33 met à jour `ruff` et réécrit le lock en révision 3 ;
- uv 0.6.10 le relit : `uv lock --check`, puis `uv sync --locked` passent.

Les pull requests de Dependabot ne casseront donc pas la CI pour une question de version d'uv. Aligner uv (poste, CI) sur une version récente reste à décider : Dependabot ne met pas à jour la version d'uv fixée dans le workflow, qui est une entrée d'action et non une dépendance.

**Non couvert** : l'image PostgreSQL + pgvector, figée par empreinte dans `docker-compose.yml` et dans le workflow. Dependabot sait mettre à jour `docker-compose.yml`, mais pas l'image d'un conteneur de service : les deux empreintes divergeraient. Hors demande, à décider.

