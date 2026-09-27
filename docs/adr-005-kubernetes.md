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
