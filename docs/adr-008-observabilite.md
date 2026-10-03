# ADR-008 : observabilité, OpenTelemetry vers un Langfuse auto-hébergé ; LangSmith écarté

- **Statut** : accepté, le 03/10/2026 (branche `observabilite`, à la demande explicite du propriétaire ; deux PR au plus, périmètre figé, toute idée nouvelle au journal comme piste). Première version, avec la PR 1 (instrumentation) ; la PR 2 (Langfuse dans le cluster de test, règles réseau, scénario du cluster) la complétera.
- **Portée** : les traces et les métriques de l'application (`ports/telemetry.py`, `adapters/otel/`), leur destination, leur confidentialité ; le traçage par des tiers (LangSmith, SDK Mistral), écarté.

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
- **URL contrôlée** : `https`, sauf sur le poste (adresse de bouclage, `localhost`) ou dans le cluster (nom court, ou terminé par `.svc` ou `.svc.cluster.local`) ; une adresse IP littérale, sous quelque forme que ce soit (`134744072` est 8.8.8.8), n'est jamais un nom court ; ni identifiants, ni requête, ni fragment.
- **Sans destination, rien** : aucun objet du SDK n'est créé, et rien n'est envoyé, même si des variables `OTEL_*` sont posées.
- **Avec une destination, quelques variables `OTEL_*` comptent encore.** Le code fixe la destination, les délais, la file, la taille des lots et la ressource ; le SDK 1.45 lit pourtant encore les en-têtes ajoutés (`OTEL_EXPORTER_OTLP_HEADERS`, fusionnés, ceux du code l'emportant), la compression, les certificats, l'échantillonnage, les limites des spans et `OTEL_SDK_DISABLED`. Aucune ne change la destination ni n'ajoute de donnée ; elles ne sont pas à poser. La taille des lots, que le SDK lisait dans `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` et qui pouvait faire refuser une petite file, est fixée par le code.
- **Proxy** : l'exportateur 1.45 passe par urllib3, qui ignore `HTTPS_PROXY`. Il ne joint donc que ce que les règles réseau autorisent en direct (PR 2).
- **Métriques** : `--metriques URL` (ou `CDG_METRIQUES`), à part, aucune par défaut. Le point d'entrée OTLP de métriques de Langfuse répond 200 et les jette ; ses tableaux de coût et de durée se calculent sur les traces.

### Tarifs

Le coût est calculé par le code, aux tarifs de `config/tarifs.yaml` (dollars par million de tokens d'entrée et de sortie, avec leur source et leur date de relevé), puis arrondi par la fonction unique de `domain/numeric.py`. Langfuse n'a aucun tarif Mistral : le coût part tout calculé, dans `gen_ai.usage.cost`.

**Écart avec la règle « tout ce qui se règle vit dans `decision.yaml` »** : les tarifs ne règlent pas l'analyse, et leur changement modifierait l'empreinte de chaque décision archivée. C'est le précédent de `tests/serie.py`, qui lit désormais le même fichier. Dès qu'une destination est configurée, le fichier est validé au démarrage, réglages d'export compris, et un modèle de la configuration sans tarif arrête le démarrage.

### Échec ouvert

- **Export en arrière-plan** : le `BatchSpanProcessor` du SDK exporte dans un fil démon. L'analyse ne fait qu'ajouter ses spans à une file bornée (`file_max`, 2 048), qui abandonne le plus ancien au-delà, avertissement du SDK à l'appui, sans jamais bloquer.
- **Export borné** : `delai_export_s` (2 s) par lot, tentatives comprises.
- **Fermeture bornée** : le `force_flush` du SDK ignore son délai, et son `shutdown` peut attendre 30 s. La CLI ferme donc la télémétrie à la fin de chaque commande dans un fil, attendu au plus `delai_fermeture_s` (2 s) ; au-delà, les derniers envois sont abandonnés, et un avertissement le dit. Un échec de la fermeture est journalisé par son seul type. Le SDK n'a pas de fermeture à la sortie du processus (`shutdown_on_exit=False`).
- **Journaux de l'exportateur** : ils citent le code et la raison HTTP, ou l'erreur de transport (hôte, port, chemin), jamais les clés ni l'en-tête d'autorisation.
- **Mesuré par les tests** : vers un port fermé, puis vers un serveur qui accepte la connexion sans jamais répondre, l'analyse rend le même résultat, en moins de 0,5 s au-delà de la médiane de trois analyses sans télémétrie ; la fermeture rend la main en son délai, alors que l'export resterait suspendu.

### Langfuse, la destination retenue

**Faits vérifiés le 02/10** : v4.50.0 (02/10/2026) ; licence MIT, sauf les dossiers `ee/` (édition commerciale : rétention, journal d'audit, masquage côté serveur). La réception OTLP, le coût et les tableaux de bord sont dans la partie MIT. Quatre avis de sécurité publiés, aucun ne touche la v4. Composants : web, worker, PostgreSQL ≥ 15, ClickHouse ≥ 25.12, Redis ≥ 7 et un stockage S3, obligatoire. Les données se lisent par `/api/public/v2/observations` (l'ancienne API des traces répond 404 en v4). Images amd64 et arm64, ni signées ni attestées. Appels sortants : télémétrie (coupée par `TELEMETRY_ENABLED=false`), vérification de mise à jour (non réglable), assistant.

**Mesuré sur le poste le 02/10** (compose officiel du tag v4.50.0, images figées par empreinte, à côté de sept conteneurs déjà en marche) : Langfuse seul prend 2,8 Go au pic du démarrage, 2,1 Go au repos, 2,3 Go en médiane et 2,5 Go au plus pendant l'envoi de 600 analyses (10 200 spans en 1 min 51 s, environ 40 fois le rythme réel). Au pic : web 951 Mio, ClickHouse 750, worker 647. Disque : 3,5 Go d'images, environ 150 Mo de données après 600 analyses (2,9 Ko par analyse dans ClickHouse).

**Emplacement** (décision du propriétaire, 03/10) :

- un profil compose optionnel sur le poste (`observabilite`), lancé quand le cluster local est arrêté : Langfuse tient à côté de ce qui tourne déjà (4,3 Go en tout pour 6 Go admis), pas à côté du cluster k3d local (7 Go) ;
- dans le cluster de test de la CI seulement, pour le scénario du cluster (runner de 16 Go) ;
- stockage objet sur **SeaweedFS**, déjà utilisé dans le cluster, et non MinIO, écarté en phase Kubernetes (dépôt archivé) ; son bon fonctionnement avec Langfuse se vérifie sur le poste comme en CI ;
- **la seule partie MIT de Langfuse** : aucune clé de licence, et un test vérifiera qu'aucune configuration du projet n'en pose ;
- ClickHouse en un seul nœud, ce qui ne vaut que pour les tests ; ce qu'exigerait une production en haute disponibilité sera écrit avec la PR 2, d'après la documentation officielle ;
- images de Langfuse ni signées ni attestées : l'exception à la vérification des signatures ne vaudra que pour les images de Langfuse utilisées en test (CI et profil du poste), jamais pour les images du produit.

## Limites

- **L'identifiant du contrat sort** (`langfuse.session.id`), pour relier l'analyse et sa revue. Il est choisi par l'opérateur ou, par MCP, par l'assistant ; dans les traces, il doit suivre le format du domaine (lettres non accentuées, chiffres, `.`, `_`, `-`), sinon il est remplacé, même lors d'une revue où il vient de l'URL sans contrôle. Comme dans le journal d'audit, il ne doit pas porter le nom d'un client. Une adresse électronique n'est jamais une valeur admise.
- **Les traces s'accumulent** : la rétention de Langfuse relève de son édition commerciale. Par défaut, elles ne contiennent aucune donnée personnelle ; avec `--traces-identite sub`, il faudrait les purger à la main.
- **Le coût est une estimation** aux tarifs relevés : la facture du fournisseur peut différer (remises, changement de prix). Les tarifs se relèvent avec leur source et leur date.
- **Les variables `OTEL_*` lues par le SDK** quand une destination est configurée ne sont ni refusées ni contrôlées : elles ne sont pas à poser (piste au journal).
- **Les métriques ne vont nulle part par défaut** : elles n'existent qu'avec `--metriques`.

## Tests

- `tests/test_otel.py` : traces, attributs, liste blanche, identité, provider jamais global, ressource, métriques, construction de l'export, fournisseur observé.
- `tests/test_observation.py` : de bout en bout sur le vrai graphe, une trace par opération, une étape par nœud, les analystes dans la même trace, l'interruption qui n'est pas un échec ; les 13 contrats du jeu, sans fuite.
- `tests/test_otel_echec_ouvert.py` : destination injoignable, muette, file pleine, fermeture bornée, journaux sans les clés, taille des lots fixée par le code.
- `tests/test_cli_traces.py` : la destination comme réglage, de bout en bout par `mcp --demo` vers un récepteur OTLP local (chemin, en-têtes, spans décodés, rien du contrat) ; clés absentes, modèle sans tarif, URL refusées ; rien sans destination, même avec des variables `OTEL_*`.
- `tests/test_tracage_tiers.py` : variables de LangSmith et de Mistral refusées au démarrage ; LangSmith coupé dans le moteur, même sous des variables hostiles.
- `tests/test_embeddings.py` : une analyse complète, en sous-processus, ne tente aucune connexion, même sous des variables de LangSmith hostiles (garde sur les sockets de Python, et bac à sable du noyau sur macOS) ; un témoin réactive le traçage et prouve que le test verrait un envoi.
- `tests/test_observation_config.py` : tarifs et réglages d'export validés, chaque modèle de la configuration tarifé.
- `tests/test_isolation.py` : `opentelemetry` confiné à `adapters/otel/`, `langsmith` à `adapters/langgraph/`.
