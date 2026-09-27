# ADR-004 : interface web rendue côté serveur, avec HTMX

- **Statut** : accepté, le 27/09/2026 (branche `interface-web`, avant la mise en public). L'écran de revue humaine prévu en phase 2 est avancé.
- **Portée** : l'interface web, second adaptateur entrant du projet à côté de la CLI ; son mode démonstration.

## Contexte

Tout doit pouvoir se faire soit par la CLI, soit par une interface web, au choix : lancer une analyse, lire le dossier d'un contrat, trancher une revue humaine, vérifier le journal d'audit, rejouer une décision, expirer les contrats en attente. Trois contraintes :

- **aucune logique métier dans l'interface** : le verdict reste rendu par du code du domaine, comme pour la CLI (ADR 001, ADR 002) ;
- **souveraineté** : LangGraph Studio a été écarté parce que son interface hébergée envoyait à Datadog le texte qu'elle affichait (ADR 003). L'interface du projet ne doit contacter aucun service tiers ;
- **les contrats sont des données sensibles** : le texte original n'est jamais conservé ; un texte venu d'un contrat ou d'un LLM ne doit jamais s'exécuter dans le navigateur.

## Décision

### Même moteur, deux portes

Un service applicatif (`application/service.py`) porte les actions communes : `analyse`, `decide`, `contracts`, `dossier`, `history`, `expire`, `journal`, `verify`, `replay`. Il passe par un port d'exécution (`ports/engine.py`, adaptateur `adapters/langgraph/engine.py`) qui ouvre le graphe avec les dépendances de chaque opération, comme le faisait chaque commande. La liste des contrats, elle, se lit en une seule ouverture, quel que soit leur nombre (`overview`, audit de publication du 27/09 : auparavant 1 + 2 × M ouvertures). La CLI et l'interface appellent les mêmes méthodes ; `tests/test_parite.py` le vérifie action par action. Pour que chaque écran ait sa commande, la CLI gagne `list`, `show`, `journal` et `replay`.

L'interface (`adapters/web/`) ne fait que lire des formulaires et mettre en page le dossier que rend le service. FastAPI, Starlette, uvicorn, Jinja2 et MarkupSafe n'y sont importés que là (`tests/test_isolation.py`).

### Rendu côté serveur, HTMX, sans application séparée

Les pages sont rendues par Jinja2, avec l'échappement automatique ; HTMX ajoute l'interactivité. Toutes les actions fonctionnent sans JavaScript, par des formulaires et des liens ordinaires. HTMX ajoute l'indicateur pendant l'analyse, le rejeu et la vérification affichés dans la page, et la navigation sans rechargement complet.

- **Écartée : une application séparée (React ou autre) et une API JSON.** Elle demanderait une chaîne de compilation Node, un second modèle des données côté client et une API à maintenir en parallèle du service. Tout ce qu'elle montrerait existe déjà côté serveur. Une API JSON, prévue en phase 2, sera un autre adaptateur entrant sur le même service.
- **Écarté : LangGraph Studio**, pour les raisons de l'ADR 003.
- **HTMX 2.0.11 est copié dans le dépôt** (`adapters/web/static/`), avec sa licence (0BSD). Son empreinte a été vérifiée deux fois le 27/09/2026 : l'intégrité sha512 de l'archive publiée par le registre npm, puis le sha384 que publie la documentation d'HTMX pour ce fichier. `tests/test_web_securite.py` recalcule ce sha384.

### Souveraineté

Aucune ressource externe : ni CDN, ni police web, ni outil de mesure d'audience. Polices du système ; thèmes clair et sombre selon le réglage du système. L'icône est un SVG écrit à la main, servi par l'interface (`static/favicon.svg`, et à `/favicon.ico`, que les navigateurs demandent d'office). Un test échoue si un gabarit ou un fichier statique, SVG compris, référence une URL externe ; seul le nom de l'espace de noms SVG, jamais chargé, est admis. La politique de sécurité du contenu (`default-src 'none'`, puis `'self'` pour les scripts, les styles, les images et les connexions) l'impose aussi au navigateur.

### Sécurité

- **Écoute locale par défaut** (127.0.0.1). Toute autre adresse est refusée au démarrage, sauf option explicite (`--ecoute-non-locale`), et un avertissement s'affiche alors.
- **Hôtes admis** : l'en-tête `Host` doit nommer l'adresse d'écoute. Sans ce contrôle, une page tierce pourrait faire résoudre son propre nom vers 127.0.0.1 (rebond DNS) et lire l'interface. Il est écrit à la main, parce que celui de Starlette découpe mal une adresse IPv6 entre crochets.
- **En-têtes** : CSP stricte sans script ni style en ligne ; `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin`, `Cache-Control: no-store`. HTMX est réglé pour tenir cette CSP : pas d'`eval`, pas de styles injectés, pas de scripts dans les réponses. Il ne garde pas non plus de copie des pages dans le stockage local du navigateur.
- **CSRF** sur tout formulaire qui modifie (analyse, décision humaine, expiration) :
  - un cookie aléatoire (`HttpOnly`, `SameSite=Strict`) ;
  - dans le formulaire, son HMAC par un secret propre au processus ;
  - l'en-tête `Origin`, s'il est présent, doit être celui de l'interface.
- **Échappement** : tout texte issu d'un contrat ou d'un LLM est échappé par Jinja2 ; les gabarits n'emploient jamais le filtre `safe`, et un test le vérifie. Le surlignage des citations est le seul HTML construit en Python : texte et citations sont échappés d'abord, puis entourés de balises fixes. Des tests avec un contrat, une citation, une explication et un nom de relecteur qui contiennent du HTML et du JavaScript le vérifient.
- **Texte original jamais conservé** :
  - il est lu en mémoire, puis passé au service, qui le masque avant le graphe, comme la CLI ;
  - la taille d'un envoi est bornée avant toute lecture (d'après `input.max_chars`), sous le seuil au-delà duquel l'analyseur de formulaires écrirait un fichier sur disque ; l'interface refuse de démarrer si la configuration dépasse ce seuil ;
  - un fichier doit être du texte brut en UTF-8, sans caractère de contrôle ;
  - le texte n'est ni journalisé (le journal d'accès ne note ni corps ni formulaire ; une erreur inattendue n'y laisse que son type), ni renvoyé dans une page d'erreur, ni gardé en session.
- **Adresse d'un contrat, toujours interne** (alertes CodeQL `py/url-redirection`, 27/09) : une seule fonction (`presentation.contract_path`) forme les liens des pages et les redirections vers un contrat. Elle garde un préfixe fixe et encode l'identifiant comme un seul segment de chemin : « / », « \ », « ? », « # », « : », blancs et fins de ligne compris. Une redirection ne peut donc mener hors de l'interface, et l'identifiant revient intact. Avant, une redirection gardait « # » et « ? » tels quels : après la revue d'un contrat « revue 1#é », le navigateur ouvrait un autre dossier. Depuis, l'identifiant d'un nouveau contrat suit une seule règle, dans le domaine (`domain/identifiers.py`), pour la CLI comme pour l'interface ; l'encodage protège les contrats plus anciens, qui restent lisibles.
- **Revue humaine** : la même politique que la CLI, dans le graphe (`domain/policy.py`) :
  - motif obligatoire, préfixe `systeme:` interdit au relecteur ;
  - levée d'un blocage dur seulement si la configuration l'autorise : la case n'est proposée que dans ce cas, et la politique refuse de toute façon une levée non permise ;
  - une réponse refusée est redemandée, avec son motif.

### Pas d'authentification tant que l'écoute reste locale

L'interface est un outil local, pour une seule personne, sur son poste. **L'authentification est obligatoire avant toute exposition réseau** : elle est notée pour l'étape du déploiement Kubernetes (phase 2), avec le chiffrement des échanges et la séparation des rôles (relecteur, administration). D'ici là, l'option d'écoute non locale existe, mais elle est refusée par défaut et assortie d'un avertissement.

### Accès concurrents (audit de publication, 27/09)

Relevé par le second avis ECC : les pages étaient des coroutines qui appelaient le service, synchrone. FastAPI 0.141.1 exécute une coroutine sur la boucle d'événements (`fastapi/routing.py`, `run_endpoint_function`) : pendant une analyse réelle, environ 10 s, toute autre page attendait. Décision du 27/09 :

- **Pages en fonctions ordinaires** : FastAPI les exécute dans son pool de threads. Seule la lecture du formulaire, avec son contrôle CSRF, reste asynchrone, en dépendance (`Depends`).
- **Un verrou unique pour les modifications** : l'analyse, la décision humaine et l'expiration passent l'une après l'autre, sous le verrou du service des contrats, en mode réel comme en démonstration, pour la CLI comme pour l'interface. Sans lui, deux analyses du même contrat passaient ensemble la vérification d'existence de `run_contract`, puis lançaient toutes deux le graphe sur le même thread (reproduit par `tests/test_concurrence.py`). La seconde reçoit désormais « le thread … existe déjà ». Les lectures (liste, dossier, parcours, journal, vérification, rejeu) restent concurrentes, y compris pendant une analyse.
- **Stockages en mémoire du mode démonstration sous verrou** : le checkpointer, car `InMemorySaver` (langgraph-checkpoint 4.2.0) n'en a aucun et la liste des threads pendant une écriture levait « dictionary changed size during iteration » ; et le journal d'audit, où deux ajouts sur la même tête fourchaient la chaîne. En mode réel, chaque opération ouvre sa propre connexion, et le journal PostgreSQL a son verrou consultatif et ses index uniques.
- **Limite : un seul processus.** Le verrou ne vaut que dans le processus qui sert l'interface (uvicorn, un seul processus : `cdg.cli web` lui passe l'application, non une chaîne d'import). En multi-réplicas (phase Kubernetes), ce sont les garanties de la base qui protègent. Le verrou consultatif et les index uniques du journal d'audit sont déjà en place. En revanche, la vérification d'existence de `run_contract` n'est pas atomique entre deux processus. Décision du 27/09 : plusieurs réplicas, avec une création de contrat rendue sûre en base dans la phase Kubernetes (`docs/mise-en-production.md`).

### Mode démonstration

`cdg.cli web --demo` : sans clé d'API, sans coût, sans PostgreSQL. Un bandeau permanent le dit sur chaque page. Ses adaptateurs vivent dans `adapters/demo/`, jamais dans le domaine.

- **Simulé** : l'extraction, rendue à partir des clauses attendues du jeu (`data/contracts/attendus.yaml`), pour les contrats du jeu seulement ; les références, choisies par rattachement déclaré dans le manifeste, sans recherche vectorielle ni juge LLM ; l'explication, par le gabarit.
- **Réel** : masquage, détection des tentatives d'instruction, vérification de l'extraction, règles, justification (la vraie étape `generate` du CRAG, validité des versions comprise), décision, revue humaine, scellement, vérification de la chaîne et rejeu. Le checkpointer et le journal d'audit sont en mémoire.
- **Limites** :
  - la démonstration ne montre jamais d'erreur d'extraction, puisqu'elle rend la bonne ;
  - elle retient toutes les sources déclarées pour une clause, là où le juge du CRAG en trie ;
  - le contexte d'analyse scellé en mémoire nomme les modèles de la configuration, alors qu'aucun n'est appelé (la consommation affiche « simulation ») ;
  - l'état disparaît à l'arrêt du serveur ;
  - la date d'analyse par défaut est celle des attendus (25/09/2026), pour que le jeu rende ses issues documentées quel que soit le jour.

  Les 13 contrats du jeu y rendent leur issue attendue (`tests/test_web_demo.py`).

## Mesure

Le 27/09/2026, une analyse réelle de bout en bout par l'interface a été conduite dans le navigateur intégré de l'application de bureau Claude, sur le contrat piégé du jeu (`demo-11`) :

- analyse en 9,9 s, jusqu'à la revue humaine (NO GO proposé, tentative d'instruction détectée) ;
- décision prise dans l'interface, puis scellée ;
- rejeu identique ;
- chaîne vérifiée par l'interface et par la CLI, tête attendue comprise ;
- coût de 0,00107 $ (5 265 tokens), explication par le LLM comprise.

Détail au journal.

## Conséquences

- **Dépendances** : quatre directes (fastapi, uvicorn, jinja2, python-multipart), trois transitives (starlette, markupsafe, annotated-doc). Licences MIT, BSD-3-Clause et Apache-2.0. Aucune dépendance du produit rétrogradée, aucune faille connue selon pip-audit, le 27/09/2026.
- **L'interface affiche le texte masqué du contrat**, que le checkpointer conserve par conception ; jamais le texte original.
- **Pour la suite** : le rapport HTML par contrat pourra reprendre les gabarits du dossier ; l'API JSON et le déploiement Kubernetes viendront avec l'authentification. *Ordre décidé le 27/09 : la phase 2 commence par le déploiement Kubernetes, le rapport HTML vient ensuite.*
