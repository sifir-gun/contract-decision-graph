# ADR-008 : observabilité, OpenTelemetry vers un Langfuse auto-hébergé ; LangSmith écarté

- **Statut** : accepté, le 03/10/2026, à la demande explicite du propriétaire ; deux PR, périmètre figé, toute idée nouvelle au journal comme piste. PR 1 (branche `observabilite`, PR #33) : l'instrumentation. PR 2 (branche `observabilite-langfuse`) : Langfuse sur le poste et dans le cluster de test, règles réseau, scénarios du cluster, chart.
- **Portée** : les traces et les métriques de l'application (`ports/telemetry.py`, `adapters/otel/`), leur destination, leur confidentialité ; le traçage par des tiers (LangSmith, SDK Mistral), écarté ; Langfuse auto-hébergé, pour les tests (poste, cluster de la CI).

## Contexte

Le propriétaire veut suivre chaque analyse : ses étapes, ses appels au LLM (modèle, tokens, coût, latence, tentatives), sa durée et son coût. Contraintes de la spécification du 02/10 :

- **le code ne dépend que de l'API OpenTelemetry**, confinée à son adaptateur ; la destination est un réglage ;
- **confidentialité** : aucune trace ne contient de texte de contrat, même masqué, ni prompt, ni réponse du LLM, ni identité ;
- **souveraineté** : aucune exportation hors du cluster ; sans destination, rien n'est envoyé nulle part ;
- **échec ouvert** : une destination absente ou lente ne change ni le résultat ni, sensiblement, la durée d'une analyse ;
- **LangSmith écarté**, par écrit, son traçage désactivé, test à l'appui.

## Décision

### OpenTelemetry, derrière un port

Le port `ports/telemetry.py` a trois points d'entrée et une fermeture : `operation` (une trace par opération), `step` (une étape par nœud), `llm_call` (un appel au modèle), `close`. L'application n'en connaît que le port ; sa doublure neutre, `NoTelemetry`, est la valeur par défaut partout.

- **Une trace par opération** : le service des contrats ouvre `cdg.analyse`, `cdg.revue`, `cdg.expiration` ou `cdg.relance` autour de sa méthode. Les trois portes passant par le service, la CLI, l'interface et le serveur MCP sont tracés de la même façon. La reprise des analyses interrompues, lancée chaque minute par l'interface en mode réel, ouvre une trace `cdg.reprise` par contrat repris, sous son verrou, et aucune quand il n'y a rien à reprendre : c'est le moteur qui parcourt les contrats.
- **Une étape par nœud** : la garde de chaque nœud, dans l'adaptateur LangGraph, ouvre un span au nom du nœud, avec sa tentative (`node_attempt` de LangGraph) et, pour un analyste, son domaine. L'interruption de la revue humaine n'est pas un échec. LangGraph copie le contexte dans ses fils : les quatre analystes, en parallèle, restent dans la trace de l'analyse.
- **Chaque appel au LLM** : un fournisseur observé (`ObservedProvider`) enveloppe le fournisseur réel et ouvre un span `chat <modèle>` par requête : fournisseur, modèle, tokens d'entrée et de sortie, coût, latence, numéro de tentative dans le nœud. La trace de l'opération en porte les totaux.
- **Métriques** : durée des appels et tokens selon les conventions GenAI (`gen_ai.client.operation.duration`, `gen_ai.client.token.usage`), coût (`cdg.llm.cout`, en dollars), durée des opérations (`cdg.operation.duree`).

**Adaptateur** (`adapters/otel/`), seul à importer `opentelemetry` (`tests/test_isolation.py`). OpenTelemetry Python **1.45.0** (25/09/2026), licence Apache-2.0, aucun avis de sécurité au 02/10 : API, SDK, exportateur OTLP en HTTP. Il a son propre `TracerProvider` et son propre `MeterProvider`, **jamais installés comme globaux** : ni la télémétrie du SDK Mistral, qui tracerait prompts et réponses, ni le middleware du SDK MCP, qui enregistre le texte des exceptions, ne trouvent où émettre. La ressource est construite par le code (nom du service, commit) : ni hôte, ni processus, ni `OTEL_RESOURCE_ATTRIBUTES`.

**Chaque opération est une racine.** Le middleware du SDK MCP, actif par défaut, installe comme contexte courant le `traceparent` et le `tracestate` que le client met dans le `_meta` de sa requête, même sans provider global : repris, ce contexte rattacherait l'analyse à une trace choisie par le client, copierait son `tracestate` (un texte libre de plusieurs kilo-octets, hors de toute liste blanche) dans chaque span exporté, et un drapeau « non échantillonné » couperait l'enregistrement sans rien dire. Le span d'une opération s'ouvre donc toujours sur un contexte vide (relecture de la branche, 03/10).

### Confidentialité des traces

Langfuse range dans ses métadonnées tous les attributs qu'il reçoit : ce qui part doit donc être sûr en entier.

- **Liste blanche, par sorte de span** (`adapters/otel/attributes.py`) : `langfuse.*`, `gen_ai.*`, `cdg.*` et `error.type`, rien d'autre. Une clé hors de la liste lève une erreur, que les tests attrapent. Une valeur de texte doit avoir la forme d'un identifiant (nœud, modèle, décision, type d'exception, identifiant du contrat) ; sinon, elle est remplacée par `<refusé>`, et l'avertissement nomme la clé, jamais la valeur.
- **Jamais émis** : le texte d'un contrat, même masqué ; un prompt, une réponse du LLM, une citation ; les attributs de contenu des conventions GenAI (`gen_ai.input.messages`…) ; le motif d'un rejet (seule sa présence) ; le message d'une exception. Une exception ne laisse que son type (`error.type`) et un statut d'erreur sans description : ni `record_exception`, ni statut posé par le SDK.
- **Aucune identité par défaut, pas même le `sub`** (décision du propriétaire, 03/10). Le canal (`cdg.canal`) suffit à l'observabilité. Langfuse, sans sa partie commerciale, ne supprime pas les traces : un `sub`, même pseudonyme, s'y accumulerait sans fin, ce qui pose un problème de conservation au sens du RGPD. Le réglage `--traces-identite sub` (ou `CDG_TRACES_IDENTITE=sub`) émet le `sub` de l'interface authentifiée (`langfuse.user.id`) ; il est désactivé par défaut, et le restera dans le chart. L'émetteur et l'opérateur de la CLI ou de MCP ne sont jamais émis.
- **Preuve** : les 13 contrats du jeu passent par l'extraction et l'explication réelles (LLM en doublure), puis par la revue humaine ; aucune trace ni métrique ne contient une fenêtre de six mots de leur texte, brut ou masqué, de leurs citations, des prompts, ni le nom d'une partie. Le même détecteur trouve ces textes dans ce qui part au modèle : le test ne passe pas à vide.

### LangSmith écarté

LangSmith, l'outil de traçage de LangChain, enverrait les traces chez un tiers, hors du cluster : contraire à la souveraineté demandée. Il est écarté, et son traçage est coupé en trois défenses, dont aucune n'est retirée parce qu'une autre semble suffire :

- **langsmith 0.14.0 est installé** : dépendance de `langchain-core`, donc de LangGraph. Son traçage s'active par variable d'environnement, et `LANGCHAIN_TRACING_V2=true` l'emporte sur `LANGSMITH_TRACING=false`, valeur que pose le `Dockerfile`.
- **Refus au démarrage** : toute variable de traçage (`LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING`, `LANGCHAIN_TRACING`, `LANGSMITH_TRACING_MODE`, `LANGSMITH_OTEL_ENABLED`, `LANGSMITH_OTEL_ONLY`) autre qu'absente, vide ou `false` arrête la CLI (`TracageTiersRefuse`), qui nomme la variable, jamais sa valeur.
- **Coupure dans le moteur** : `langsmith.configure(enabled=False)`, qui l'emporte sur l'environnement, à la construction du moteur LangGraph ; seul `adapters/langgraph/` importe `langsmith`.
- **Télémétrie du SDK Mistral** (mistralai 2.10.1) : `MISTRAL_SDK_TELEMETRY` tracerait prompts et réponses, vers le provider global ou vers `api.mistral.ai`. Toute valeur autre qu'absente, vide ou `false` est refusée au démarrage, comme les variables de LangSmith. Seconde défense, puisque le SDK relit la variable à chaque requête : l'adaptateur coupe la télémétrie dans la configuration du client qu'il construit (réglage `telemetry`, que le SDK lit sans le déclarer) ; un test le vérifie par une vraie requête vers le serveur factice de Mistral, sous la variable.

### Destination : un réglage de lancement

- **`--traces URL`** avant la commande, ou `CDG_TRACES` : le point d'entrée OTLP (`…/api/public/otel` pour Langfuse). Clés `LANGFUSE_PUBLIC_KEY` et `LANGFUSE_SECRET_KEY`, comme les autres secrets : fichier monté, sinon `.env`. Sans elles, refus au démarrage. L'export part en HTTP vers `<url>/v1/traces`, avec une authentification `Basic` et l'en-tête `x-langfuse-ingestion-version: 4`.
- **Destination sur le poste ou dans le cluster seulement** (décision du propriétaire, 03/10, après la relecture : la spécification exclut toute exportation hors du cluster, et les règles réseau de la PR 2 ne doivent pas être la seule défense) : sur le poste (adresse de bouclage, `localhost`) ou dans le cluster (nom court, ou terminé par `.svc` ou `.svc.cluster.local`) seulement, en http ou en https ; une adresse IP littérale, sous quelque forme que ce soit (`134744072` est 8.8.8.8), n'est jamais un nom court ; ni identifiants, ni requête, ni fragment. Une destination externe, même en https (`cloud.langfuse.com`), est refusée au lancement ; un refus ne cite jamais l'URL.
- **Sans destination, rien** : aucun objet du SDK n'est créé, et rien n'est envoyé, même si des variables `OTEL_*` sont posées.
- **Avec une destination, quelques variables `OTEL_*` comptent encore.** Le code fixe la destination, les délais, la file, la taille des lots et la ressource ; le SDK 1.45 lit pourtant encore les en-têtes ajoutés (`OTEL_EXPORTER_OTLP_HEADERS`, fusionnés, ceux du code l'emportant), la compression, les certificats, l'échantillonnage (`OTEL_TRACES_SAMPLER` ; par défaut, tout, puisque chaque opération est une racine), les limites des spans, le filtre des exemplaires des métriques, `OTEL_SDK_DISABLED`, et un fournisseur d'identifiants d'export (`_OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER`), qui ferait passer l'export par requests et ses proxys : aucun n'est installé, et la variable seule fait échouer le démarrage. `OTEL_PROPAGATORS` ne règle que le contexte que le SDK MCP tire du `_meta`, que les opérations ne reprennent pas. Aucune ne change la destination ni n'ajoute de donnée ; elles ne sont pas à poser. La taille des lots, que le SDK lisait dans `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` et qui pouvait faire refuser une petite file, est un réglage (`lot_max`), passé par le code.
- **Proxy** : l'exportateur 1.45 passe par urllib3, qui ignore `HTTPS_PROXY`. Il ne joint donc que ce que les règles réseau autorisent en direct (PR 2).
- **Métriques** : `--metriques URL` (ou `CDG_METRIQUES`), à part, aucune par défaut. Le point d'entrée OTLP de métriques de Langfuse répond 200 et les jette ; ses tableaux de coût et de durée se calculent sur les traces.

### Tarifs

Le coût est calculé par le code, aux tarifs de `config/tarifs.yaml` (dollars par million de tokens d'entrée et de sortie, avec leur source et leur date de relevé), puis arrondi par la fonction unique de `domain/numeric.py`. Langfuse n'a aucun tarif Mistral : le coût part tout calculé, dans `gen_ai.usage.cost`.

**Écart avec la règle « tout ce qui se règle vit dans `decision.yaml` »** : les tarifs ne règlent pas l'analyse, et leur changement modifierait l'empreinte de chaque décision archivée. C'est le précédent de `tests/serie.py`, qui lit désormais le même fichier. Dès qu'une destination est configurée, le fichier est validé au démarrage, réglages d'export compris (délais, file, lots, intervalle des métriques : aucun en dur dans le code), et un modèle de la configuration sans tarif arrête le démarrage. Chaque valeur émise, attribut ou mesure d'une métrique, passe par l'arrondi unique ; le total d'une trace arrondit la somme exacte de ses appels.

### Échec ouvert

- **Export en arrière-plan** : le `BatchSpanProcessor` du SDK exporte dans un fil démon. L'analyse ne fait qu'ajouter ses spans à une file bornée (`file_max`, 2 048), qui abandonne le plus ancien au-delà, avertissement du SDK à l'appui, sans jamais bloquer. Les lots exportés comptent au plus `lot_max` spans (512, la valeur du SDK) ; un lot plus grand que la file est refusé au chargement des réglages.
- **Export borné** : `delai_export_s` (2 s) par lot, tentatives comprises.
- **Fermeture bornée** : le `force_flush` du SDK ignore son délai, et son `shutdown` peut attendre 30 s. La CLI ferme donc la télémétrie à la fin de chaque commande dans un fil, attendu au plus `delai_fermeture_s` (2 s) ; au-delà, les derniers envois sont abandonnés, et un avertissement le dit. Un échec de la fermeture est journalisé par son seul type. Le SDK n'a pas de fermeture à la sortie du processus (`shutdown_on_exit=False`).
- **Redirection** : l'exportateur ne suit pas une redirection (l'en-tête d'autorisation ne part donc jamais ailleurs), mais il compte un code 3xx comme un succès : derrière une entrée qui redirige (de http vers https, par exemple), les spans seraient perdus sans un mot. La destination se donne par son URL finale.
- **La revue humaine** : la garde du nœud `human_review` ouvre une étape autour de l'appel à `interrupt()`. Ce n'est pas un effet de bord rejoué au sens du graphe (rien n'est écrit en base ni notifié) : l'étape apparaît dans la trace de l'analyse, qui s'arrête là, puis dans celle de la revue, qui reprend le nœud.
- **Journaux de l'exportateur** : ils citent le code et la raison HTTP, ou l'erreur de transport (hôte, port, chemin), jamais les clés ni l'en-tête d'autorisation.
- **Mesuré par les tests** : vers un port fermé, puis vers un serveur qui accepte la connexion sans jamais répondre, l'analyse rend le même résultat, en moins de 0,5 s au-delà de la médiane de trois analyses sans télémétrie ; la fermeture rend la main en son délai, alors que l'export resterait suspendu.

### Langfuse, la destination retenue

**Faits vérifiés le 02/10, puis dans le code du tag v4.50.0 le 03/10** : v4.50.0 (02/10/2026). Quatre avis de sécurité publiés, aucun ne touche la v4. Composants : web, worker, PostgreSQL ≥ 15, ClickHouse ≥ 25.12 (26.4 recommandée), Redis ≥ 7 ou Valkey ≥ 8 et un stockage S3, obligatoire ; la documentation de dimensionnement cite SeaweedFS parmi les stockages possibles. Les données se lisent par `/api/public/v2/observations` (l'ancienne API des traces répond 404 en v4). Images amd64 et arm64, ni signées ni attestées.

**La seule partie libre de Langfuse** (décision du propriétaire, 03/10). Le dépôt est sous licence MIT, sauf les dossiers `ee/`, `web/src/ee/` et `worker/src/ee/`, sous la licence « Enterprise » de `ee/LICENSE` : son usage exige une licence commerciale, sauf « for development and testing purposes » ; sa note précise que le cœur MIT « can be used and run without infringing » cette licence. Les images publiées contiennent ce code ; il reste inactif sans clé (`LANGFUSE_EE_LICENSE_KEY`, que le projet ne pose nulle part, tests à l'appui sur le poste et dans le cluster). La réception OTLP, le calcul du coût, les observations et les tableaux de bord sont dans la partie MIT. Restent écartées les fonctions de l'édition commerciale, dont **la rétention des données** (droit « data-retention », traitement dans `worker/src/ee/dataRetention/`) : sans elle, les traces s'accumulent (Limites).

**Appels sortants, relus dans le code du tag** : la télémétrie (PostHog) ne part plus avec `TELEMETRY_ENABLED=false` et sans clé de licence (`web/src/features/telemetry/index.ts`) ; l'état de `status.langfuse.com` n'est lu qu'en mode cloud ; l'assistant est coupé (`LANGFUSE_IN_APP_AGENT_ENABLED=false`) ; aucun SMTP, aucun fournisseur d'IA. **La vérification de mise à jour, elle, n'est pas réglable** : `checkUpdate` (`web/src/server/api/routers/public.ts`) interroge `https://langfuse.com/api/latest-releases` avec la version, à chaque page authentifiée, hors mode cloud seulement (aucune trace n'y part, mais l'adresse du poste et la version, oui). Une première relecture du code l'avait manquée ; la relecture de la branche l'a trouvée. Dans le cluster, les pods de Langfuse n'ont aucune sortie hors du cluster, et l'appel échoue ; sur le poste, l'interface de Langfuse est publiée sans sortie vers Internet. Le navigateur qui affiche l'interface de Langfuse peut, lui, charger des vidéos de `static.langfuse.com` sur les pages vides.

**Composition, la même sur le poste et dans le cluster de test** :

| Composant | Image (figée par empreinte) | Licence |
| --- | --- | --- |
| Langfuse web et worker | `langfuse/langfuse` et `langfuse/langfuse-worker` 4.50.0 | MIT hors `ee/` |
| ClickHouse | `clickhouse/clickhouse-server` 25.12.11.4, variante distroless | Apache-2.0 |
| Valkey | `valkey/valkey` 8.1.10, alpine | BSD-3 |
| PostgreSQL | l'image de la base du projet (`pgvector/pgvector:pg16`) | PostgreSQL |
| Stockage S3 | SeaweedFS 4.47, celui du cluster de test | Apache-2.0 |

- **Valkey plutôt que Redis** : depuis la 7.4, Redis est sous RSALv2 ou SSPLv1, licences non libres ; Langfuse accepte Valkey ≥ 8. Lancé sans root, sans le script d'entrée de l'image.
- **ClickHouse sans root** (101), journaux système réduits (`docker/observabilite/clickhouse-journaux.xml`, le même fichier sur le poste et dans le cluster).
- **Secrets en variables d'environnement, jamais en argument** : Langfuse, ClickHouse et Valkey ne lisent leurs secrets que dans l'environnement, quand l'application les lit en fichiers (CIS 5.4.1, ADR 005) ; écart admis pour ces composants de test. Aucun secret en argument ni dans une sonde (la table des processus et `docker inspect` le montreraient) : Valkey écrit sa configuration au démarrage depuis l'environnement, `valkey-cli` lit `REDISCLI_AUTH`, le client de ClickHouse `CLICKHOUSE_PASSWORD` (relecture de la branche).
- **Réglages** : inscription fermée, organisation, projet et clés posés au démarrage (`LANGFUSE_INIT_*`) ; dans le cluster, aucun utilisateur ; sur le poste, un compte pour l'interface de Langfuse. Événements bruts dans le seau `langfuse` (préfixe `events/`), ni médias ni exports.

**Sur le poste : `compose.observabilite.yaml`**, à part de `docker-compose.yml`. La décision du 03/10 parlait d'un profil compose ; mais docker compose (5.0.2) exige les variables d'un profil même inactif : un profil dans le fichier de la base obligerait à poser les secrets de Langfuse pour lancer la base seule, ou à leur donner des valeurs vides, ce qui serait un repli silencieux (ClickHouse et Valkey sans mot de passe). Le fichier à part exige chaque secret. Seule l'interface de Langfuse est publiée, sur `127.0.0.1:3100` ; les autres services n'ont qu'un réseau interne, sans sortie ; SeaweedFS tire son identité S3 de `AWS_ACCESS_KEY_ID` et `AWS_SECRET_ACCESS_KEY` (SeaweedFS 4.47, `auth_credentials.go`). À lancer quand le cluster local est arrêté.

**Dans le cluster de test de la CI** (`cluster/langfuse.yaml`, installé par `scripts/cluster.py` en profil `ci` seulement ; le profil local, 8 Go pour Docker, ne l'installe pas, et le dit) :

- espace `cdg-observabilite`, Pod Security « restricted » : pods sans root ni privilège, ressources bornées, secrets par référence, données éphémères ;
- règles réseau : refus par défaut ; DNS ; trafic interne ; web et worker vers SeaweedFS (8333) ; entrée des traces (3000) depuis l'interface de l'application seulement ; **aucune sortie hors du cluster** ;
- dans SeaweedFS, un seau `langfuse` et une identité S3 limitée à ce seau (`Read:langfuse`, `Write:langfuse`, `List:langfuse`). SeaweedFS 4.47 envoie par défaut des statistiques anonymes à `telemetry.seaweedfs.com` (`-master.telemetry`, vrai par défaut) : celui du cluster le faisait depuis la PR C3, sans règle réseau dans son espace. Télémétrie coupée (`-master.telemetry=false`, sur le poste aussi), et règles réseau dans `cdg-stockage` : refus par défaut, entrée S3 depuis la base, son greffon de sauvegarde et Langfuse seulement, sortie vers le DNS seulement (relecture de la branche) ;
- l'application reçoit `traces.destination` (chart : un service du cluster seulement, en mode réel, identité « aucune ») et ses clés en fichiers ; une règle réseau du chart ouvre la seule cible ;
- deux scénarios : la trace d'une analyse lue par l'API de Langfuse, sans texte du contrat, avec ses événements bruts dans le seau ; les traces hors du cluster refusées par l'application, bloquées par le réseau (depuis l'interface, les pods de Langfuse et SeaweedFS) et par le proxy de sortie ;
- mémoire de chaque conteneur (`kubectl top`) et disque éphémère de chaque pod relevés au repos et après les scénarios (`scripts/cluster.py mesure-langfuse`, étapes de diagnostic du job `cluster`) ; besoin disque du cluster au pire porté de 29,9 à 44,7 Go.

**Exception aux signatures, pour les tests seulement** (décision du propriétaire, 03/10). Les images de Langfuse, de ClickHouse et de Valkey ne publient ni signature ni attestation cosign (vérifié sur Docker Hub le 03/10 : aucune étiquette `sha256-….sig` ni `.att`). `securite/exceptions-signatures.yaml` les nomme une à une, par étiquette et empreinte, avec la portée « tests » (CI et poste), un motif et 90 jours au plus ; `scripts/chaine.py` la contrôle, et l'installation du cluster refuse toute image sans signature qui n'y figure pas, ou dont l'exception a expiré. **Elle ne vaut jamais pour une image du produit** : l'application, ses images annexes et leurs bases restent vérifiées par leur signature, et un test refuse toute image des charts ou des `Dockerfile` dans l'exception. PostgreSQL et SeaweedFS sont les images déjà utilisées par le projet.

**ClickHouse en un seul nœud : pour les tests seulement.** Sans Keeper ni réplication, `CLICKHOUSE_CLUSTER_ENABLED=false`, données éphémères. Ce qu'exigerait une production en haute disponibilité (documentations officielles relues le 03/10/2026 : langfuse.com, pages ClickHouse et Scaling ; clickhouse.com, ClickHouse Keeper) :

- **ClickHouse en cluster** (`CLICKHOUSE_CLUSTER_ENABLED=true`, cluster nommé `default`), tables répliquées, **au moins trois réplicas** selon Langfuse ; le nombre de réplicas ne s'augmente pas en marche sans intervention ni interruption ;
- **ClickHouse Keeper** pour coordonner la réplication : un quorum de trois nœuds, qui tolère la perte d'un seul ;
- **dimensionnement** donné par Langfuse : ClickHouse 2 CPU et 8 Gio au moins (16 Gio de limite dans son chart), un grand volume dès le départ ; ClickHouse 26.4 recommandée ; sauvegardes et copie de données d'après la documentation de ClickHouse ;
- **le reste de la composition aussi** : web et worker répliqués (2 CPU et 4 Gio chacun au minimum), PostgreSQL géré ou en haute disponibilité (CloudNativePG, comme la base de l'application), Valkey ou Redis en mode cluster sous forte charge, un stockage S3 lui-même redondant (SeaweedFS répliqué, ou un service S3) ;
- **une rétention** : celle de Langfuse relève de son édition commerciale ; Langfuse suggère aussi un TTL de ClickHouse sur ses tables, et une durée de vie des événements dans le stockage (piste au journal).

**Mesures.** Sur le poste, le 02/10 (compose officiel du tag v4.50.0, MinIO et Redis, à côté de sept conteneurs déjà en marche) : Langfuse seul prend 2,8 Go au pic du démarrage, 2,1 Go au repos, 2,3 Go en médiane et 2,5 Go au plus pendant l'envoi de 600 analyses (10 200 spans en 1 min 51 s, environ 40 fois le rythme réel) ; au pic, web 951 Mio, ClickHouse 750, worker 647 ; 3,5 Go d'images, environ 150 Mo de données après 600 analyses (2,9 Ko par analyse dans ClickHouse). Images de la composition retenue, compressées (registre, linux/amd64) : 1,14 Go (web 0,37, worker 0,36, ClickHouse 0,21, PostgreSQL 0,19, Valkey 0,02).

## Limites

- **Un nom court** (sans point) est admis comme un service du cluster ; sur un poste, le domaine de recherche du résolveur DNS pourrait le compléter vers un hôte d'un autre réseau. Dans le cluster, les règles réseau bornent ce qui est joignable.
- **L'identifiant du contrat sort** (`langfuse.session.id`), pour relier l'analyse et sa revue. Il est choisi par l'opérateur ou, par MCP, par l'assistant ; dans les traces, il doit suivre le format du domaine (lettres non accentuées, chiffres, `.`, `_`, `-`), sinon il est remplacé, même lors d'une revue où il vient de l'URL sans contrôle. Comme dans le journal d'audit, il ne doit pas porter le nom d'un client. Une adresse électronique n'est jamais une valeur admise.
- **Les traces s'accumulent** : la rétention de Langfuse relève de son édition commerciale. Par défaut, elles ne contiennent aucune donnée personnelle ; avec `--traces-identite sub`, il faudrait les purger à la main. Un TTL de ClickHouse sur les tables de Langfuse, et une durée de vie des événements dans le stockage, sont des pistes.
- **Langfuse n'est éprouvé que pour les tests** : un nœud ClickHouse, des données éphémères dans le cluster, un PostgreSQL sans réplication ; une production demanderait la composition en haute disponibilité décrite plus haut.
- **Les deux scénarios des traces ne tournent que dans la CI** : le cluster local (près de 7 Go) et Langfuse (2,5 Go) ne tiennent pas ensemble dans les 8 Go de Docker du poste ; ils y sont sautés, motif à l'appui.
- **Images de Langfuse, de ClickHouse et de Valkey sans signature** : une exception datée, revue au plus tard tous les 90 jours, limitée aux tests.
- **Le coût est une estimation** aux tarifs relevés : la facture du fournisseur peut différer (remises, changement de prix). Les tarifs se relèvent avec leur source et leur date.
- **Les variables `OTEL_*` lues par le SDK** quand une destination est configurée ne sont ni refusées ni contrôlées : elles ne sont pas à poser (piste au journal).
- **Les métriques ne vont nulle part par défaut** : elles n'existent qu'avec `--metriques`.

## Tests

- `tests/test_otel.py` : traces, attributs, liste blanche, identité, provider jamais global, ressource, métriques, construction de l'export, fournisseur observé.
- `tests/test_observation.py` : de bout en bout sur le vrai graphe, une trace par opération, une étape par nœud, les analystes dans la même trace, l'interruption qui n'est pas un échec ; les 13 contrats du jeu, sans fuite.
- `tests/test_otel_echec_ouvert.py` : destination injoignable, muette, file pleine, fermeture bornée, journaux sans les clés, taille des lots réglée, jamais lue dans l'environnement.
- `tests/test_cli_traces.py` : la destination comme réglage, de bout en bout par `mcp --demo` vers un récepteur OTLP local (chemin, en-têtes, spans décodés, rien du contrat) ; clés absentes, modèle sans tarif, URL refusées ; rien sans destination, même avec des variables `OTEL_*`.
- `tests/test_tracage_tiers.py` : variables de LangSmith et de Mistral refusées au démarrage ; LangSmith coupé dans le moteur, même sous des variables hostiles.
- `tests/test_embeddings.py` : une analyse complète, en sous-processus, ne tente aucune connexion, même sous des variables de LangSmith hostiles (garde sur les sockets de Python, et bac à sable du noyau sur macOS) ; un témoin réactive le traçage et prouve que le test verrait un envoi.
- `tests/test_observation_config.py` : tarifs et réglages d'export validés, chaque modèle de la configuration tarifé.
- `tests/test_isolation.py` : `opentelemetry` confiné à `adapters/otel/`, `langsmith` à `adapters/langgraph/`.
- `tests/test_chart_traces.py` : configuration montée complète (plus de `tarifs.yaml` masqué) ; aucune trace par défaut ; destination limitée à un service du cluster, en mode réel ; identité « aucune » par défaut ; clés en fichiers pour l'interface seule ; une sortie réseau vers la seule cible.
- `tests/test_observabilite_poste.py` : le fichier compose du poste (images figées et communes, partie libre seule, secrets exigés, interface seule publiée, réseau interne).
- `tests/test_langfuse_cluster.py` : le manifeste et l'installation dans le cluster (pods restreints, secrets par référence, partie libre seule, règles réseau sans sortie, identité S3 limitée, profil ci seulement, relevés de la CI).
- `tests/test_exceptions_signatures.py` : l'exception aux signatures, exactement les images de la composition, jamais une image du produit, entrées mal formées ou expirées refusées.
- `tests/test_cluster.py` (job `cluster`) : `test_trace_d_une_analyse_dans_langfuse`, `test_traces_hors_du_cluster_bloquees`.
