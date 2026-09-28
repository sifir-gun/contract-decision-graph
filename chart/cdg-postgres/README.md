# Chart Helm cdg-postgres

Base PostgreSQL de contract-decision-graph, selon l'ADR 005 : un cluster CloudNativePG avec pgvector (PostgreSQL 16.15, image « standard » de CloudNativePG), sauvegardé en continu vers un stockage objet compatible S3, et ses règles réseau.

## Prérequis du cluster (production comme test)

- **CloudNativePG** 1.26 au moins (testé : 1.30.1), opérateur installé dans `cnpg-system`.
- **Greffon Barman Cloud** (testé : 0.15.0), dans le même espace de noms que l'opérateur : il archive les WAL et fait les sauvegardes (la voie intégrée à CloudNativePG est dépréciée depuis la 1.26).
- **cert-manager** (testé : 1.21.2) : le greffon l'exige, pour les certificats de ses échanges avec l'opérateur (documentation du greffon, « Installation »). C'est donc un prérequis de production, pas seulement du cluster de test.
- **Stockage objet** compatible S3, et un Secret `cdg-s3` (clés `ACCESS_KEY_ID`, `ACCESS_SECRET_KEY`) dans l'espace de noms de la base.

`scripts/cluster.py` installe ces trois composants, figés par version et empreinte, dans le cluster de test.

## Installation

Dans l'espace de noms de l'application, avant elle, sous le nom `cdg-postgres` :

```bash
helm upgrade --install cdg-postgres chart/cdg-postgres --namespace cdg
```

Le cluster s'appelle `cdg-postgres` : ses services `cdg-postgres-rw` (écriture), `-ro`, `-r`, et son superutilisateur, dans le Secret `cdg-postgres-superuser` (clés `username`, `password`). Les migrations de l'application (`setup-db`) passent par ce superutilisateur ; l'application elle-même se connecte en `app_role`.

## Valeurs principales

| Valeur | Défaut | Rôle |
| --- | --- | --- |
| `instances` | `2` | Instances PostgreSQL (une primaire, des réplicas). |
| `image.digest` | 16.15-standard-trixie | Empreinte de l'image PostgreSQL, jamais une étiquette. |
| `stockage.taille` | `10Gi` | Volume de chaque instance. |
| `sauvegardes.destination` | `s3://cdg-sauvegardes/` | Seau et préfixe des sauvegardes. |
| `sauvegardes.adresse` | vide (AWS) | Adresse du stockage objet ; SeaweedFS dans le cluster de test. |
| `sauvegardes.planification` | `0 0 3 * * *` | Sauvegarde complète chaque nuit (format de CloudNativePG, secondes comprises). |
| `sauvegardes.retention` | `30d` | Durée de conservation. |

## Règles réseau

La règle « tout refusé » du chart de l'application couvre tout l'espace de noms : ce chart ouvre ce que les instances exigent, rien de plus. En entrée : l'application et les autres instances sur 5432, l'opérateur sur 8000. En sortie : le DNS, les autres instances, le serveur d'API de Kubernetes (443 et 6443, son adresse dépendant du cluster) et le stockage objet (443 hors adresses privées, ou le service interne du profil de test).

## Restauration

Une restauration crée un nouveau cluster, amorcé depuis le stockage objet (`bootstrap.recovery`, source déclarée dans `externalClusters` avec le greffon). La procédure, et sa vérification par `verify --expect-head` contre la tête du journal conservée hors de la base, sont dans `docs/exploitation.md` ; le scénario du cluster de test la rejoue à chaque pull request.
