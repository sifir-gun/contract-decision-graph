# ADR-003 : LangGraph Studio écarté

- **Statut** : accepté, le 27/09/2026 (branche `visuels`, avant la mise en public).
- **Portée** : LangGraph Studio comme outil de développement du projet, et ses captures dans le README.

## Contexte

LangGraph Studio (titre de sa page : « LangSmith Studio ») est l'interface graphique des serveurs LangGraph : elle affiche le graphe, lance des exécutions et montre l'état de chaque thread. Il était prévu de s'en servir comme outil de développement, et d'en tirer des captures pour le README : le graphe, les quatre analystes en parallèle, la pause de revue humaine. Studio aurait affiché le contenu de nos analyses : texte masqué du contrat, clauses, citations, verdicts.

Avant de l'installer, on a comparé ce que dit la documentation à ce qu'on observe.

## Ce que dit la documentation

Pages lues le 27/09/2026 :

- **« Data storage and privacy »** (https://docs.langchain.com/langsmith/data-storage-and-privacy) :
  - section *Studio* : l'interface est servie par smith.langchain.com, mais s'exécute dans le navigateur et se connecte directement au serveur local ; les données envoyées au serveur ne sont pas envoyées à LangSmith. Connecté, LangSmith collecte des statistiques d'usage (pages visitées, clics, navigateur, taille d'écran), sans données ni code de l'application. En usage anonyme : « When using Studio anonymously, […] usage analytics are not collected » ;
  - section *In-memory development server* : hors la télémétrie du CLI, aucune donnée ne quitte la machine, sauf si le traçage est activé ou si le code du graphe contacte lui-même un service externe ;
  - section *CLI* : la plupart des commandes du CLI envoient un événement de statistiques à chaque appel (système, versions, nom de la commande, options passées ou non) ; `LANGGRAPH_CLI_NO_ANALYTICS=1` les coupe toutes.
- **« Get started with Studio »** (https://docs.langchain.com/langsmith/quick-start-studio) : avec `LANGSMITH_TRACING=false` dans le `.env` de l'application, aucune donnée ne quitte le serveur local.
- **Datadog, « Session Replay Browser Privacy Options »** (https://docs.datadoghq.com/real_user_monitoring/session_replay/browser/privacy_options/) : le mode `mask-user-input` masque la plupart des champs de formulaire et enregistre tel quel tout autre texte ; une saisie est remplacée par trois astérisques.

## Ce qui a été observé le 27/09/2026

Versions : langgraph 1.2.12, langgraph-api 0.15.1, langgraph-cli 0.4.32, langgraph-runtime-inmem 0.35.1, dans un environnement jetable hors du dépôt, avec un graphe minimal sans aucune donnée du projet. Serveur lancé par `langgraph dev --no-browser --no-reload --port 2024`. Heures en UTC.

### Interface de Studio

Ouverte sans compte ni connexion, dans le navigateur intégré de l'application de bureau Claude, à l'adresse que donne la documentation (`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`).

**Hôtes contactés au chargement de la page de 06:19:38**, relevés dans les entrées de chronométrage des ressources de la page (hors fichiers de l'application sur smith.langchain.com) :

| Hôte | Chemins | Envois |
| --- | --- | --- |
| `browser-intake-us5-datadoghq.com` | `/api/v2/rum`, `/api/v2/replay`, `/api/v2/logs` | 25, 2 et 1, statut 202 |
| `cdn.segment.com`, `cdn.edgefn.segment.com` | réglages du projet, scripts | 4 |
| `api.segment.io` | `/v1/b` | 1, statut 200 |
| `www.googletagmanager.com` | `/gtag/js` | 2 |
| `region1.google-analytics.com`, `region1.analytics.google.com` | `/g/collect` | 2 et 2 |
| `stats.g.doubleclick.net` | `/g/collect` | 1 |
| `www.google.fr` | `/ads/ga-audiences` | 1 |

**Réglages de Datadog lus dans la page** (`DD_RUM.getInitConfiguration()`) : site `us5.datadoghq.com`, `sessionSampleRate` 100, `sessionReplaySampleRate` 100, `defaultPrivacyLevel` `mask-user-input`. Cookies présents : `_dd_s`, `ajs_anonymous_id`, `_ga` et deux cookies `_ga_…`.

**Contenu des envois**, lu à partir de 06:20:55 : les fonctions d'envoi de la page (`fetch`, `navigator.sendBeacon`, `XMLHttpRequest`) ont été enveloppées pour copier chaque corps de requête, décompressé quand c'était possible (deflate, gzip). Le dialogue de connexion au serveur a ensuite été ouvert, pour faire apparaître du texte absent au chargement.

- **Envoi `replay` de 06:21:22** (formulaire en plusieurs parties) : un segment de 3 603 octets, 16 170 octets une fois décompressé (deflate), qui décrit la page nœud par nœud. Il contient mot pour mot les textes du dialogue ouvert après 06:20:55, dont « Configure Studio connection » et « Enter the endpoint info of your Agent Server », dans des nœuds de texte. La valeur du champ de saisie y est remplacée par `***`.
- **Envois `rum` de 06:21:01 et 06:21:34** : des événements `resource` avec l'adresse des requêtes de la page vers le serveur local (`http://127.0.0.1:2024/assistants/search`), un événement `view` avec l'adresse de la page (paramètre `baseUrl` compris), des événements `long_task`, et un identifiant `usr.anonymous_id`.
- Aucun envoi à Segment ni à Google pendant cette fenêtre.

Le navigateur intégré a bloqué lui-même chaque requête de Studio vers `127.0.0.1:2024` (`net::ERR_BLOCKED_BY_CLIENT`) : Studio ne s'est jamais connecté au serveur local, et n'a affiché que son écran d'échec de connexion et ce dialogue.

### Serveur local (`langgraph dev`)

Même méthode que le test réseau des embeddings (`tests/test_embeddings.py`) : le serveur tourne dans un bac à sable du noyau macOS (`sandbox-exec`) qui tue le processus (SIGKILL) à sa première connexion sortante hors de la machine ; les connexions locales sont permises. Témoins : une connexion vers 192.0.2.1 et une vers example.com sont tuées (code 137) ; des connexions vers 127.0.0.1 et ::1 passent.

- **Avec `LANGSMITH_TRACING=false` et `LANGGRAPH_CLI_NO_ANALYTICS=1`** : à 06:16:52, le serveur est tué (code 137) juste après sa bannière de démarrage. Relancé à 06:17:28 dans un bac à sable qui refuse sans tuer : le journal du noyau compte 8 tentatives de connexion du serveur vers un port 443 en 8 secondes, adresse masquée par le journal.
- **Vérification de version** : le code installé (`langgraph_api/cli.py`, 0.15.1) lance au démarrage de `langgraph dev` une requête vers `https://pypi.org/pypi/langgraph-api/json`, pour signaler une version plus récente, sauf si la variable `LANGGRAPH_NO_VERSION_CHECK` vaut `true` ou `1`. Cette variable n'apparaît pas dans la documentation publique ; un commentaire du code la réserve au développement. Ce jour-là, pypi.org avait 8 adresses (4 IPv4, 4 IPv6). L'attribution des 8 tentatives à cette requête repose sur le code, sur ce nombre d'adresses et sur leur disparition avec la variable (point suivant) : le journal du noyau ne donne pas l'adresse.
- **Avec `LANGGRAPH_NO_VERSION_CHECK=true` en plus des deux variables** : de 06:19:00 à 06:22:40, serveur vivant, aucune connexion refusée par le noyau. Le graphe minimal a été exécuté deux fois par l'API HTTP du serveur, sortie comprise.
- **Statistiques du CLI** : le code installé (`langgraph_cli/analytics.py`) les envoie à un service Supabase, sauf si `LANGGRAPH_CLI_NO_ANALYTICS` vaut `1`. La variable était posée à chaque lancement ; leur envoi n'a pas été mesuré.
- **Métadonnées d'usage du serveur** : le serveur a journalisé qu'il ne lançait pas leur envoi, faute de clé de licence.

## Ce qui n'a pas été mesuré

- **Le canari.** Le test prévu faisait afficher par Studio la sortie d'un graphe contenant une chaîne unique, puis cherchait cette chaîne dans chaque envoi. Studio ne s'étant jamais connecté au serveur local, la chaîne n'a jamais été affichée : on n'a pas observé directement qu'un résultat de graphe part chez Datadog. On a observé que le texte affiché par l'interface y part.
- **Le contenu des envois à Segment et à Google** : aucun n'a eu lieu pendant la fenêtre de lecture.
- **Le contenu des envois antérieurs à 06:20:55**, dont le premier segment `replay` de la page.
- L'usage connecté à un compte, d'autres navigateurs, et les statistiques du CLI sans `LANGGRAPH_CLI_NO_ANALYTICS`.

## Les autres coûts

- **Dépendances du produit rétrogradées.** Le 27/09/2026, dans une copie jetable du projet, `uv lock` avec `langgraph-cli[inmem]` dans le groupe `dev` a rétrogradé trois dépendances utilisées à l'exécution : protobuf 7.36.2 → 6.33.6 (onnxruntime, embeddings), opentelemetry-api 1.44.0 → 1.42.1 et opentelemetry-semantic-conventions 0.65b0 → 0.63b1 (mistralai). Toutes les versions de `langgraph-api` compatibles avec `langgraph-cli` 0.4.32 exigent `protobuf<7`. Un groupe séparé, déclaré incompatible avec `dev`, aurait laissé le produit inchangé, au prix d'un second audit des dépendances dans la CI.
- **Licence du serveur.** Selon leurs métadonnées sur PyPI, `langgraph-api` 0.15.1 et `langgraph-runtime-inmem` 0.35.1 sont sous licence Elastic 2.0, qui n'est pas une licence open source au sens de l'OSI. Le projet est sous AGPL-3.0.
- **Entrée non masquée.** La CLI masque le contrat et pose le contexte d'analyse (empreinte de la configuration, modèles, date) avant d'appeler le graphe (`run_contract`). Studio appelle le graphe avec ce qu'on saisit dans l'interface : il aurait fallu un point d'entrée propre à Studio pour préparer l'entrée de la même façon.

## Décision

**Studio est écarté**, y compris comme outil de développement. En usage anonyme, son interface a envoyé à Datadog, mot pour mot, le texte qu'elle affichait. Ce que Studio afficherait de nos analyses le serait par cette même page, avec les réglages de Datadog observés.

Les visuels du README n'en dépendent pas : le schéma est dessiné par LangGraph depuis le graphe réel (`scripts/schema_graphe.py`), et l'enregistrement du terminal montre une analyse réelle (`scripts/demo_terminal.sh`).

**Alternative prévue** : un rapport HTML par contrat, puis un écran de revue humaine en phase 3. Ni l'un ni l'autre n'existe encore.

## Conséquences

- Aucune dépendance ajoutée : `pyproject.toml` et `uv.lock` sont inchangés. L'environnement jetable des essais a été supprimé le 27/09/2026.
- Le README le signale dans le paragraphe sur la souveraineté, avec un renvoi ici.
- Ces observations valent pour les versions et la date indiquées. Une nouvelle évaluation devrait refaire les mêmes mesures, et mener le test du canari à son terme.
