# ADR-005 : déploiement sur Kubernetes (k3s, Helm)

- **Statut** : en cours, depuis le 28/09/2026 (branche `kubernetes`), en quatre PR : A, application prête ; B, chaîne d'approvisionnement ; C, chart et cluster, sans Ingress ; D, authentification et Ingress. Chaque PR complète cet ADR.
- **Portée** : le déploiement sur k3s (k3d en local et en CI, plusieurs nœuds) par un chart Helm de niveau production, testé par un vrai cluster à chaque pull request. Aucun déploiement public sur Internet dans cette phase.

## Contexte

Le système tournait sur un poste, en un seul processus : CLI et interface web locale (ADR 004), PostgreSQL par Docker Compose. Le déploiement sur Kubernetes ajoute plusieurs réplicas, des arrêts et des redémarrages de pods, une configuration montée de l'extérieur, et des journaux lus par la plateforme. Toute bonne pratique écartée l'est ici, par écrit ; rien n'est omis en silence.

## Références

Consultées dans leur version courante ; chaque choix d'outil fondé sur un fait daté cite sa source.

| Source | Version, date | Ce qu'on en retient |
| --- | --- | --- |
| [NSA et CISA, *Kubernetes Hardening Guide*](https://media.defense.gov/2022/Aug/29/2003066362/-1/-1/0/CTR_KUBERNETES_HARDENING_GUIDANCE_1.2_20220829.PDF) ([page de la CISA](https://www.cisa.gov/news-events/alerts/2022/03/15/updated-kubernetes-hardening-guide)) | v1.2, révisée le 30/08/2022 | Pods non root, système de fichiers en lecture seule, séparation réseau, moindre privilège, journalisation, mises à jour. Le guide cite encore les PodSecurityPolicy, retirées depuis Kubernetes 1.25 : on applique à leur place les Pod Security Standards. |
| [Kubernetes, feature gate `ImageVolume`](https://github.com/kubernetes/website/blob/main/content/en/docs/reference/command-line-tools-reference/feature-gates/ImageVolume.md) et [volume `image`](https://kubernetes.io/docs/concepts/storage/volumes/#image) | bêta active par défaut en 1.35, stable depuis 1.36 ; consulté le 27/09/2026 | Monter une image OCI en volume, en lecture seule. |
| [k3s, canaux de version](https://update.k3s.io/v1-release/channels) | canal stable : v1.36.4+k3s1, le 27/09/2026 | La version stable de k3s a le volume `image` stable. |
| [k3s, chiffrement des Secrets](https://docs.k3s.io/security/secrets-encryption) | consulté le 27/09/2026 | `--secrets-encryption` ; fournisseurs `aescbc` et `secretbox` (ce dernier depuis les versions d'avril 2025) ; rotation par `k3s secrets-encrypt`. |
| [k3s, guide de durcissement (CIS)](https://docs.k3s.io/security/hardening-guide) | consulté le 27/09/2026 | Pod Security Admission en `restricted`, chiffrement des Secrets, NetworkPolicy, journal d'audit de l'API, paramètres du noyau. |
| [CloudNativePG](https://github.com/cloudnative-pg/cloudnative-pg/releases) | v1.30.1, le 23/09/2026 | Opérateur PostgreSQL retenu. |
| [Images PostgreSQL de CloudNativePG](https://github.com/cloudnative-pg/postgres-containers) | consulté le 27/09/2026 | L'image `standard` contient pgvector ; PostgreSQL 16 est construit. |
| [Sauvegardes de CloudNativePG](https://cloudnative-pg.io/docs/devel/backup/) et [greffon Barman Cloud](https://github.com/cloudnative-pg/plugin-barman-cloud/releases) | voie native dépréciée depuis 1.26 ; greffon v0.15.0, le 03/09/2026 | Sauvegardes vers un stockage objet par le greffon, planifiées (`ScheduledBackup`). |
| [Dépôt de MinIO](https://github.com/minio/minio) | archivé (API de GitHub : `archived: true`, dernier push le 24/04/2026) ; consulté le 27/09/2026 | MinIO écarté ; SeaweedFS (Apache-2.0) retenu pour le stockage S3 local et en CI. |
| Avis de sécurité de GitHub sur [Trivy](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23) et [trivy-action](https://github.com/aquasecurity/trivy-action/security/advisories/GHSA-9p44-j4g5-cfx5) | GHSA-69fq-xp46-6x23, critique, 21/03/2026 : chaîne d'approvisionnement de Trivy compromise ; GHSA-9p44-j4g5-cfx5, moyen, 18/02/2026 : injection de script dans l'action | Trivy écarté ; Syft et Grype (Anchore, Apache-2.0), sans avis publié le 27/09/2026, retenus. |
| Code installé : mistralai 2.10.1 (`client/sdk.py`), httpx 0.28.1 | vérifié le 27/09/2026 | Le client Mistral crée un `httpx.Client` qui lit `HTTPS_PROXY` (`trust_env`, vrai par défaut) : un proxy de sortie fonctionne sans changer le code. |

## Décisions

### Journaux structurés (PR A)

- **JSON sur la sortie standard** dans Kubernetes (`--journaux json`, ou `CDG_JOURNAUX=json` pour tous les conteneurs et les Jobs), texte sur le poste par défaut. Une configuration unique (`adapters/journaux.py`) pour la CLI et pour uvicorn ; aucune dépendance ajoutée (bibliothèque standard).
- **Jamais le texte d'un contrat ni un secret** :
  - une exception n'y laisse que son type et les lignes de code traversées, jamais son message. Starlette relance l'exception après avoir rendu la page d'erreur, et uvicorn la journalisait, message compris ; ce message pouvait citer une donnée reçue ;
  - le journal d'accès : méthode, chemin et code ; ni le corps, ni l'adresse du client ;
  - les bibliothèques ne passent qu'à partir des avertissements.
- **Vérifié** par un vrai serveur uvicorn et un texte témoin dans le contrat : toutes les lignes sont du JSON, le témoin n'apparaît nulle part (`tests/test_journaux.py`).
- **Limite** : les messages d'avertissement des bibliothèques (mistralai, LangGraph, httpx) passent tels quels. Aucun ne cite le contrat dans les tests, mais le code ne peut pas le prouver pour tous leurs chemins.

### Création, revue et expiration sûres entre réplicas (PR A)

- **Un verrou par contrat, dans PostgreSQL** (`ports/locks.py`, `adapters/postgres/locks.py`) : verrou consultatif de session, pris sans attendre (`pg_try_advisory_lock`), clé de 64 bits dérivée de l'identifiant par SHA-256, dans un espace de noms distinct de celui du journal d'audit. Le moteur le prend pour créer un contrat, trancher une revue et expirer un contrat en attente. La seconde demande, où qu'elle vienne, reçoit aussitôt « le contrat … est en cours de traitement » (409 dans l'interface, erreur JSON dans la CLI).
- **Au-delà de la création** : sans le verrou, deux réplicas qui tranchent le même contrat en même temps reprenaient tous deux le graphe. Le journal refusait le second scellement, mais l'état du thread pouvait garder la décision du second, contraire au journal. L'expiration relit chaque contrat sous son verrou et laisse, avec un avertissement, un contrat en cours de revue ailleurs.
- **Si le processus meurt**, sa connexion se ferme et PostgreSQL relâche le verrou (verrou de session, [documentation de PostgreSQL 16](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS)) : un autre réplica peut reprendre l'analyse.
- **Une connexion par verrou**, tenue le temps de l'opération : un verrou consultatif est réentrant pour la session qui le tient ([fonctions de verrou, PostgreSQL 16](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)) ; deux opérations ne partagent donc jamais une session.
- **Écarté : un regroupeur de connexions en mode transaction** (PgBouncer, ou le `Pooler` de CloudNativePG en mode `transaction`) entre l'application et la base. Il changerait la session sous l'application et ferait tenir le verrou par une autre.
- **Vérifié** par deux vrais processus (`multiprocessing`, méthode spawn), chacun avec son moteur, son checkpointer et ses verrous PostgreSQL : un seul contrat créé et scellé, la seconde demande refusée clairement, puis « existe déjà » ; un processus tué pendant l'analyse relâche le verrou (`tests/test_verrous.py`).

**Connexions à PostgreSQL, par réplica**, avant le pool (état au 28/09, commit du verrou) : chaque opération ouvrait sa connexion et la fermait.

| Opération | Connexions tenues en même temps |
| --- | --- |
| Analyse (une à la fois par réplica, verrou du service) | 1 verrou + 1 checkpointer + jusqu'à 4 recherches (les quatre analystes en parallèle) = 6 ; puis 1 au scellement |
| Revue humaine, expiration | 1 verrou + 1 checkpointer, puis 1 au scellement |
| Lecture (liste, dossier, parcours) | 1 par requête |
| Journal, vérification, rejeu | 1 par requête |

Au plus, par réplica : 6 pour l'analyse en cours, plus une par lecture simultanée. Le nombre de lectures simultanées n'est borné que par le pool de threads de FastAPI (40 par défaut, bibliothèque AnyIO) : jusqu'à environ 46 connexions par réplica. **Décision du 28/09** : un pool de connexions psycopg par processus, de taille réglable, où le verrou compte (section suivante).

### Pool de connexions (PR A, décision du 28/09)

- **Un pool psycopg par processus** pour `app_role` (`adapters/postgres/connexions.py`, psycopg-pool 3.3.3, LGPL-3.0, déclaré en dépendance directe le 28/09 ; il était déjà dans `uv.lock`, tiré par langgraph-checkpoint-postgres). Le checkpointer (`PostgresSaver` accepte un pool), le journal d'audit, la recherche, les verrous de contrat et la sonde de disponibilité y empruntent leurs connexions. Les commandes d'administration (migrations, ingestion) gardent une connexion directe, avec les identifiants administrateur.
- **Réglages** : ceux qu'exige `PostgresSaver` (autocommit, lignes en dictionnaires, pas de requêtes préparées côté serveur) ; les autres usagers ouvrent leurs transactions eux-mêmes et lisent leurs lignes en tuples. Chaque connexion est vérifiée avant d'être prêtée (`check`), pour survivre à un redémarrage ou une bascule de la base. Au retour de chaque connexion, `pg_advisory_unlock_all()` : un verrou de session ne suit jamais une connexion rendue, même si son usager a échoué (vérifié par un test).
- **Pool épuisé** : attente bornée (10 s), puis `ConnectionsExhausted`, rendue en 503 par l'interface, avec `Retry-After: 5`. Jamais une attente sans fin, jamais une erreur 500 sans explication.
- **Taille** : `--connexions` (ou `CDG_CONNEXIONS`), 10 par défaut. Mesure du 28/09 sur la base locale, trois essais, vraie recherche pgvector, vrais poids d'embedding, LLM et extraction en doublures, les quatre domaines à justifier : 29 connexions empruntées par analyse, au plus 4 en même temps. Plafond théorique : 9 (verrou, quatre recherches, quatre écritures du checkpointer en parallèle). Avec 10, une analyse (une seule à la fois par réplica) laisse de la place aux lectures, qui tiennent chacune une connexion quelques millisecondes et attendent leur tour plutôt que d'échouer.
- **Côté PostgreSQL** : `max_connections` ≥ réplicas × taille du pool + tâches (migrations, ingestion) + connexions réservées au superutilisateur. Avec 3 réplicas : 30 + 2 + 3 = 35, sous la valeur par défaut de 100 (réglée dans le chart, PR C).

### Sondes de santé (PR A)

- **Trois sondes, sans logique métier** (`adapters/web/sante.py`), en GET et en HEAD, jamais en cache : `/sante/vie` (le processus répond), `/sante/demarrage` (le modèle d'embedding est chargé ; la sonde de démarrage lui laisse ce temps), `/sante/pret` (démarré, base joignable par le pool, pas en cours d'arrêt). Un échec ne rend que sa raison, jamais son message ; le journal n'en garde que le type.
- **Un port à part** (`web --port-sante`, `--hote-sante 0.0.0.0` dans un pod), servi par un second serveur uvicorn dans un thread à lui : aucune route de l'interface n'y est servie, aucune sonde sur le port de l'interface. Hors du thread principal, uvicorn n'installe pas de gestionnaire de signaux (`Server.capture_signals`, uvicorn 0.54) : seul le serveur de l'interface reçoit l'ordre d'arrêt, les sondes s'arrêtent après lui. Pas de journal d'accès pour les sondes.
- **Pourquoi pas le port de l'interface** : dans la PR D, l'interface n'écoutera que sur 127.0.0.1 dans le pod, derrière oauth2-proxy ; le kubelet appelle l'adresse du pod. Et l'interface contrôle l'en-tête Host, que les sondes n'ont pas à connaître.
- **Écarté : `http.server` de la bibliothèque standard** pour les sondes : sa documentation le déconseille en production.
- **Modèle chargé une fois par processus** : en mode réel, `web` le charge en arrière-plan dès le lancement, et chaque analyse le réutilise. Avant, chaque analyse de l'interface rechargeait les poids depuis le disque.
- **HEAD sur les pages de l'interface** : la RFC 9110 demande à un serveur généraliste d'accepter HEAD là où il accepte GET ; FastAPI ne l'ajoute pas de lui-même, l'interface répondait 405 à `HEAD /`.
