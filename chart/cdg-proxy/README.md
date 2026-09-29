# Chart Helm cdg-proxy

Proxy de sortie de contract-decision-graph, selon l'ADR 005 : Smokescreen, construit par le projet (`docker/proxy-sortie/`), seule sortie vers Internet. Il ne laisse passer que l'API de Mistral, refuse toute adresse privée, et n'accepte que les pods de l'application.

## Installation

À installer sous le nom `cdg-proxy`, dans l'espace de noms de l'application, avant elle : le service s'appelle alors `cdg-proxy`, l'adresse que l'application attend par défaut (`proxy.url`).

```bash
helm upgrade --install cdg-proxy chart/cdg-proxy --namespace cdg --set image.digest=sha256:…
```

L'empreinte de l'image est obligatoire : celle de l'index publié par la CI (résumé du job `publication`, image `ghcr.io/sifir-gun/contract-decision-graph/proxy-sortie`). Sans elle, l'installation est refusée.

## Valeurs principales

| Valeur | Défaut | Rôle |
| --- | --- | --- |
| `image.digest` | aucune | Empreinte de l'index amd64/arm64 du proxy ; obligatoire. |
| `domainesAutorises` | `api.mistral.ai` | Seuls domaines joignables (Smokescreen, « enforce »). |
| `plagesAutorisees` | vide | Adresses privées admises ; profil de test seulement (serveur factice). |
| `clients` | pods web de l'application | Seuls pods admis en entrée. |
| `sortiesInternes` | vide | Sorties vers des pods du cluster ; profil de test seulement. |
| `replicas` | `2` | Réplicas ; budget d'interruption d'un pod disponible. |
| `ressources` | 50m / 32 Mi, limite 500m / 128 Mi | Mesure du 28/09 : 6 Mio après 43 requêtes. |

## Garanties vérifiées

Par `scripts/chart.py` (helm lint, kubeconform, kube-linter) et `tests/test_chart_proxy.py`, en CI et dans `scripts/check.sh` :

- pod non root (65532), seccomp `RuntimeDefault`, racine en lecture seule, aucune capacité, sans jeton de compte de service ; image par empreinte ;
- configuration DNS recommandée (`ndots: "2"`) : le proxy résout `api.mistral.ai` sans passer par les domaines de recherche du cluster ;
- liste d'accès en « enforce », adresses privées refusées ; tout changement relance les pods ;
- réseau : en entrée, les pods de l'application sur le port 4750 ; en sortie, le DNS du cluster et le port 443 vers Internet, jamais vers les adresses privées ;
- trois sondes TCP ; mise à jour progressive sans interruption ; répartition sur des nœuds différents.
