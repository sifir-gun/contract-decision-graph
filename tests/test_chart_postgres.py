"""Chart de la base PostgreSQL (ADR 005, PR C3) : un cluster CloudNativePG avec pgvector,
sauvegardé vers un stockage objet par le greffon Barman Cloud (WAL archivés, sauvegardes
planifiées), et ses règles réseau (la règle « tout refusé » du chart de l'application
couvre tout l'espace de noms).

Avec helm (marqueur `chart`) : image par empreinte, superutilisateur pour les migrations,
ressources explicites (sinon, la limite par défaut de l'espace de noms), sauvegardes,
règles réseau ; le profil de test vise SeaweedFS dans le cluster.
"""

import subprocess
from pathlib import Path

import pytest
import yaml
from test_chart import UniqueKeys, named, of_kind

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "chart" / "cdg-postgres"
PLUGIN = "barman-cloud.cloudnative-pg.io"
# étiquette et empreinte : le webhook de CloudNativePG refuse une empreinte seule (il lit
# la version dans l'étiquette pour détecter les mises à jour ; vu dans le cluster, 28/09)
IMAGE = (
    "ghcr.io/cloudnative-pg/postgresql:16.15-standard-trixie@"
    "sha256:46ee4bd3d36f4cbbc95a883487a596f17471e6bc9da2e8218eafdde8d20c3270"
)
TEST = [
    "--set",
    "sauvegardes.adresse=http://seaweedfs.cdg-stockage.svc.cluster.local:8333",
    "--set",
    "reseau.stockageInterne.espaceDeNoms=cdg-stockage",
    "--set",
    "reseau.stockageInterne.selecteur.app=seaweedfs",
    "--set",
    "reseau.stockageInterne.port=8333",
]


def render(*options: str) -> list[dict]:
    result = subprocess.run(
        [
            "helm",
            "template",
            "cdg-postgres",
            str(CHART),
            "--namespace",
            "cdg",
            *options,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.load_all(result.stdout, Loader=UniqueKeys) if doc]


@pytest.fixture(scope="module")
def rendu() -> list[dict]:
    return render()


@pytest.mark.chart
def test_cluster_pgvector_par_empreinte_et_superutilisateur_pour_les_migrations(rendu):
    [cluster] = of_kind(rendu, "Cluster")
    assert cluster["apiVersion"] == "postgresql.cnpg.io/v1"
    assert cluster["metadata"]["name"] == "cdg-postgres"  # services cdg-postgres-rw…
    spec = cluster["spec"]
    assert spec["imageName"] == IMAGE  # 16.15-standard-trixie : pgvector compris
    assert spec["instances"] == 2
    # CREATE EXTENSION vector : les migrations (setup-db) passent par le superutilisateur
    assert spec["enableSuperuserAccess"] is True
    assert spec["bootstrap"]["initdb"]["database"] == "cdg"
    assert spec["storage"]["size"] == "10Gi"


@pytest.mark.chart
def test_ressources_explicites_de_postgresql_et_de_l_annexe_de_sauvegarde(rendu):
    [cluster] = of_kind(rendu, "Cluster")
    resources = cluster["spec"]["resources"]
    assert resources["requests"]["memory"] and resources["limits"]["memory"]
    [store] = of_kind(rendu, "ObjectStore")
    sidecar = store["spec"]["instanceSidecarConfiguration"]["resources"]
    assert sidecar["requests"]["memory"] and sidecar["limits"]["memory"]


@pytest.mark.chart
def test_wal_archives_et_sauvegardes_planifiees_par_le_greffon(rendu):
    [cluster] = of_kind(rendu, "Cluster")
    [plugin] = cluster["spec"]["plugins"]
    assert plugin == {
        "name": PLUGIN,
        "isWALArchiver": True,
        "parameters": {"barmanObjectName": "cdg-postgres-sauvegardes"},
    }
    store = named(rendu, "ObjectStore", "sauvegardes")
    assert store["apiVersion"] == "barmancloud.cnpg.io/v1"
    configuration = store["spec"]["configuration"]
    assert configuration["destinationPath"] == "s3://cdg-sauvegardes/"
    assert "endpointURL" not in configuration  # AWS par défaut
    credentials = configuration["s3Credentials"]
    assert credentials["accessKeyId"] == {"name": "cdg-s3", "key": "ACCESS_KEY_ID"}
    assert credentials["secretAccessKey"] == {
        "name": "cdg-s3",
        "key": "ACCESS_SECRET_KEY",
    }
    assert configuration["wal"]["compression"] == "gzip"
    assert store["spec"]["retentionPolicy"] == "30d"
    [scheduled] = of_kind(rendu, "ScheduledBackup")
    assert scheduled["spec"]["method"] == "plugin"
    assert scheduled["spec"]["pluginConfiguration"] == {"name": PLUGIN}
    assert scheduled["spec"]["cluster"] == {"name": "cdg-postgres"}
    assert scheduled["spec"]["schedule"] == "0 0 3 * * *"  # chaque nuit, 3 h


@pytest.mark.chart
def test_profil_de_test_stockage_objet_dans_le_cluster():
    docs = render(*TEST)
    store = named(docs, "ObjectStore", "sauvegardes")
    assert store["spec"]["configuration"]["endpointURL"] == (
        "http://seaweedfs.cdg-stockage.svc.cluster.local:8333"
    )
    [policy] = of_kind(docs, "NetworkPolicy")
    storage = policy["spec"]["egress"][-1]
    assert storage["ports"] == [{"port": 8333, "protocol": "TCP"}]
    [peer] = storage["to"]
    assert peer["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": "cdg-stockage"}
    }


@pytest.mark.chart
def test_regles_reseau_de_postgresql(rendu):
    [policy] = of_kind(rendu, "NetworkPolicy")
    assert policy["spec"]["podSelector"] == {
        "matchLabels": {"cnpg.io/cluster": "cdg-postgres"}
    }
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    ingress = {
        tuple(p["port"] for p in rule["ports"]): rule["from"]
        for rule in policy["spec"]["ingress"]
    }
    # 5432 : l'application (interface et tâches) et les autres instances (réplication)
    assert ingress[(5432,)] == [
        {
            "podSelector": {
                "matchLabels": {"app.kubernetes.io/name": "contract-decision-graph"}
            }
        },
        {"podSelector": {"matchLabels": {"cnpg.io/cluster": "cdg-postgres"}}},
    ]
    # 8000 : état de l'instance, lu par l'opérateur
    assert ingress[(8000,)] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "cnpg-system"}
            }
        }
    ]
    egress = policy["spec"]["egress"]
    ports = [sorted(p["port"] for p in rule["ports"]) for rule in egress]
    assert [53, 53] in ports  # DNS, UDP et TCP
    assert [5432] in ports  # réplication vers les autres instances
    assert [443, 6443] in ports  # serveur d'API de Kubernetes (gestionnaire d'instance)
    assert ports[-1] == [443]  # stockage objet, par défaut sur Internet (S3)
    [block] = egress[-1]["to"]
    assert block["ipBlock"]["cidr"] == "0.0.0.0/0"
    assert "10.0.0.0/8" in block["ipBlock"]["except"]


@pytest.mark.chart
def test_image_par_empreinte_obligatoire():
    result = subprocess.run(
        ["helm", "template", "cdg-postgres", str(CHART), "--set", "image.digest=16"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "digest" in result.stderr


@pytest.mark.chart
def test_exclusions_de_kube_linter_justifiees(rendu):
    from test_chart import IGNORE

    [policy] = of_kind(rendu, "NetworkPolicy")
    ignored = {
        key.removeprefix(IGNORE): reason
        for key, reason in policy["metadata"]["annotations"].items()
        if key.startswith(IGNORE)
    }
    # instances créées par l'opérateur, clients rendus par le chart de l'application
    assert set(ignored) == {
        "dangling-networkpolicy",
        "dangling-networkpolicypeer-podselector",
    }
    assert all(len(reason.split()) >= 5 for reason in ignored.values())


@pytest.mark.chart
def test_role_applicatif_gere_par_cloudnativepg(rendu):
    """app_role : déclaré dans le Cluster, mot de passe lu dans le Secret basic-auth que
    l'application lit aussi (clé password), haché par l'opérateur, rotation comprise
    (étiquette cnpg.io/reload du Secret) ; setup-db le trouve et ne le crée pas."""
    [cluster] = of_kind(rendu, "Cluster")
    assert cluster["spec"]["managed"]["roles"] == [
        {
            "name": "app_role",
            "ensure": "present",
            "login": True,
            "superuser": False,
            "createdb": False,
            "createrole": False,
            "replication": False,
            "passwordSecret": {"name": "cdg-base-application"},
        }
    ]
