# Chart Helm contract-decision-graph

Déploiement de contract-decision-graph sur Kubernetes (k3s), selon l'ADR 005 : interface web, sondes de santé, tâches de migration, d'ingestion et de contrôle de configuration, règles réseau. L'interface n'écoute que sur 127.0.0.1 dans le pod ; oauth2-proxy, en conteneur annexe natif, en est le seul chemin, derrière un Ingress Traefik en TLS. L'authentification est obligatoire (PR D2) : l'interface locale, non authentifiée, n'existe jamais dans le cluster ; rôles analyste et relecteur tirés des groupes du jeton, quatre yeux, second facteur réglable.

## Prérequis

- **Kubernetes 1.36** (volume `image` stable). En deçà (1.33 au moins), `modele.montage=copie`.
- **Espace de noms** au niveau « restricted » de Pod Security, créé avant l'installation :

  ```bash
  kubectl create namespace cdg
  ```

  ```bash
  kubectl label namespace cdg pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted pod-security.kubernetes.io/audit=restricted
  ```

- **Secrets**, créés à part (jamais dans les valeurs), montés en fichiers en lecture seule dans `/run/secrets/cdg`, jamais en variables d'environnement : clé de Mistral (`cdg-mistral`, clé `MISTRAL_API_KEY`), mot de passe d'`app_role` (`cdg-base-application`, clé `password` ; avec CloudNativePG, un Secret `kubernetes.io/basic-auth` dont `username` vaut `app_role`, étiqueté `cnpg.io/reload: "true"`, que l'opérateur lit aussi : chart `cdg-postgres`), administrateur de PostgreSQL (`cdg-base-administrateur`, clés `username` et `password` ; avec CloudNativePG, le Secret `cdg-postgres-superuser` qu'il crée, par `base.administrateur.secret`).
- **PostgreSQL** avec pgvector et le **proxy de sortie**, joignables aux adresses des valeurs `base` et `proxy` : charts `chart/cdg-postgres` (CloudNativePG, sauvegardes) et `chart/cdg-proxy` (Smokescreen), installés avant l'application. Le chart de la base exige CloudNativePG, son greffon Barman Cloud et **cert-manager**, que le greffon exige : prérequis de production (`chart/cdg-postgres/README.md`).

- **Authentification, obligatoire, et entrée** (`authentification`, `ingress.active`) : Traefik 3.7.13 au moins (CRD `Middleware` et `TLSOption`, IngressClass `traefik`), un émetteur de cert-manager ou un Secret TLS, un fournisseur d'identité OIDC joignable par le proxy de sortie, le Secret `cdg-oidc` (`client-secret`, `cookie-secret`) et le Secret `cdg-csrf` (`courante`, clé des formulaires partagée par les réplicas) : `docs/exploitation.md`, « Authentification et entrée ».

## Installation et mise à jour

```bash
helm upgrade --install cdg chart/contract-decision-graph --namespace cdg --set authentification.emetteur=https://idp.example.org --set authentification.clientId=cdg-interface --set ingress.hote=cdg.example.org
```

```bash
helm test cdg --namespace cdg
```

Avant chaque mise à jour, la tâche de contrôle de configuration refuse de déployer une configuration qui rendrait des contrats en attente impossibles à trancher (`config-check`, ADR 005). La procédure est dans `docs/exploitation.md` ; `taches.controleConfiguration.passerOutre=true` la contourne, en connaissance de cause.

## Valeurs principales

| Valeur | Défaut | Rôle |
| --- | --- | --- |
| `mode` | `reel` | `reel` (base, modèle, LLM) ou `demo` (en mémoire, sans base ni modèle ni clé). |
| `image.digest` | index publié | Empreinte de l'index amd64/arm64 de l'application, jamais une étiquette. |
| `modele.image.digest` | index publié | Empreinte de l'index de l'image du modèle d'embedding. |
| `modele.montage` | `image` | `image` (volume image, lecture seule) ou `copie` (conteneurs d'initialisation). |
| `replicas` | `2` | Réplicas de l'interface ; pas d'autoscaling (ADR 005). |
| `ressources.reel` | 1 CPU / 2 Gi, limite 2 CPU / 4 Gi | Mesurées (`scripts/mesure_memoire.py`, puis dans le cluster : 2,65 Gio au chargement du modèle) ; la limite CPU fixe les fils de l'embedder. |
| `embedding.lot` | `16` | Taille des lots d'embeddings : 2,9 Gio au pic de l'ingestion (fastembed en prend 256 par défaut). |
| `llm.adresseApi` | vide | Adresse de l'API de Mistral ; vide, celle du SDK. Le proxy ne laisse passer que Mistral. |
| `configuration.decision` | vide | Autre configuration de décision, en texte ; vide, `files/decision.yaml`. |
| `authentification.active` | `true` | oauth2-proxy en conteneur annexe natif, démarré avant l'interface, arrêté après elle ; l'interface vérifie le jeton de chaque requête (`web --identite en-tetes`). Exige `emetteur` (HTTPS), `clientId` (audience) et `ingress.hote` (adresse publique) ; image d'oauth2-proxy : l'index publié par défaut, signature et provenance vérifiées. |
| `authentification.session` | `8h`, revalidée toutes les `5m` | Durée de la session d'oauth2-proxy et revalidation auprès du fournisseur. |
| `authentification.roles` | `cdg-analystes`, `cdg-relecteurs` | Groupes du jeton qui donnent les rôles analyste (analyse) et relecteur (revue, expiration) ; jamais vides. |
| `authentification.secondFacteur` | vide | Valeurs `amr` ou `acr` acceptées comme preuve d'un second facteur pour trancher et expirer ; vide : non exigé, annoncé dans les notes d'installation et au démarrage (prérequis de production). |
| `authentification.secretCsrf` | `cdg-csrf` | Clés des formulaires, partagées par les réplicas : `courante`, et `precedente` pendant une rotation ; relues sans redémarrage. |
| `authentification.accord` | `auto` | Écran d'accord du fournisseur (`approval_prompt` d'oauth2-proxy) : `auto` ne le force pas, `force` le demande à chaque connexion. |
| `authentification.deconnexionFournisseur` | `false` | Fermer aussi la session chez le fournisseur, s'il publie une fin de session. |
| `authentification.autorites` | vide | ConfigMap (clé `ca.crt`) des autorités du fournisseur ; vide, celles du système. |
| `ingress.active` | `false` | Ingress Traefik en TLS ; exige l'authentification et `ingress.hote`. |
| `ingress.emetteurCertificat` | vide | ClusterIssuer de cert-manager pour `ingress.secretTls` ; vide, Secret fourni. |
| `ingress.debit` | 20 par seconde, rafales de 40 | Limite par adresse du client. |
| `taches.*` | actives | Migrations (avant), ingestion (après : plus de 10 minutes ; `taches.ingestion.active=false` pour une mise à jour qui ne touche pas au corpus), contrôle de configuration (avant une mise à jour). |

Le schéma complet est `values.schema.json` : toute valeur inconnue ou mal formée fait échouer l'installation.

## Garanties vérifiées

Par `scripts/chart.py` (helm lint, kubeconform, kube-linter) et `tests/test_chart.py`, en CI et dans `scripts/check.sh` :

- chaque pod : non root (65532), seccomp `RuntimeDefault`, racine en lecture seule, aucune capacité, pas d'élévation, compte de service dédié sans jeton, requêtes et limites ;
- images par empreinte seulement ; aucun Secret rendu ; secrets en fichiers (0440, groupe du pod), chaque conteneur ne recevant que les siens ;
- interface : mise à jour progressive sans interruption, budget d'interruption, répartition sur des nœuds différents (contraintes de topologie, anti-affinité préférée), trois sondes, pause avant l'arrêt et délai de grâce calculé ;
- configuration en ConfigMap, dont l'empreinte relance les pods ;
- tâches et autres ressources de crochet supprimées après leur crochet, journaux des tâches recopiés sur la sortie de Helm : `helm uninstall` ne laisse rien, hormis une tâche en échec gardée pour le diagnostic (nettoyage dans `docs/exploitation.md`) ;
- réseau : tout refusé par défaut ; en sortie, DNS, PostgreSQL et proxy seulement ; jamais le port 443 ouvert largement ;
- authentification (`tests/test_chart_authentification.py`) : oauth2-proxy par empreinte, conteneur annexe natif (conteneur d'initialisation redémarré, sonde de démarrage), seul chemin vers l'interface, jeton transmis et en-têtes du client retirés, cookie `Secure`, `HttpOnly`, `SameSite=Lax`, journal de connexion réduit au `sub`, secrets en fichiers dans son seul conteneur ; clés CSRF partagées dans l'interface seule, montées sans `subPath` (mises à jour sans redémarrage) ; entrée refusée sans authentification ; TLS 1.2 au moins, HSTS, HTTP redirigé, taille maximale alignée sur l'interface, débit par adresse du client ; port d'oauth2-proxy ouvert aux seuls pods de Traefik.
