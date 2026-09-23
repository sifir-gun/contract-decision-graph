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
