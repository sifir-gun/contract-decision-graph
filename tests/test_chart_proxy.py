"""Chart du proxy de sortie (ADR 005, PR C3) : Smokescreen, seule sortie vers Internet.

Sans helm : sa liste d'accès par défaut ne laisse passer que l'API de Mistral. Avec helm
(marqueur `chart`) : pods restreints, image par empreinte obligatoire, configuration DNS
recommandée (`ndots: "2"`, le proxy résout api.mistral.ai), liste d'accès et plages
refusées, règles réseau (entrée : les seuls pods de l'application ; sortie : le DNS et le
port 443 hors des adresses privées), disponibilité et relance sur changement.
"""

import ipaddress
import subprocess
from pathlib import Path

import pytest
import yaml
from test_chart import IGNORE, UniqueKeys, named, of_kind

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "chart" / "cdg-proxy"
DIGEST = "sha256:" + "1" * 64  # empreinte d'exemple : l'image se désigne par empreinte
PRIVATE = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
]


def values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))


def render(*options: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "cdg-proxy", str(CHART), "--namespace", "cdg"]
        + ["--set", f"image.digest={DIGEST}", *options],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.load_all(result.stdout, Loader=UniqueKeys) if doc]


@pytest.fixture(scope="module")
def rendu() -> list[dict]:
    return render()


def configuration(docs: list[dict]) -> tuple[dict, dict]:
    data = named(docs, "ConfigMap", "cdg-proxy")["data"]
    return yaml.safe_load(data["smokescreen.yaml"]), yaml.safe_load(data["acl.yaml"])


# --- sans helm ------------------------------------------------------------------------------


def test_seule_l_api_de_mistral_par_defaut():
    assert values()["domainesAutorises"] == ["api.mistral.ai"]
    assert values()["plagesAutorisees"] == []  # aucune adresse privée joignable


def test_empreinte_de_l_image_obligatoire_sans_valeur_par_defaut():
    """Publiée après la fusion (job publication) : l'empreinte se renseigne à
    l'installation ; sans elle, le schéma refuse."""
    assert values()["image"]["digest"] == ""


# --- avec helm -------------------------------------------------------------------------------


@pytest.mark.chart
def test_sans_empreinte_installation_refusee():
    result = subprocess.run(
        ["helm", "template", "cdg-proxy", str(CHART)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "digest" in result.stderr


@pytest.mark.chart
def test_pods_restreints_image_par_empreinte(rendu):
    deployment = named(rendu, "Deployment", "cdg-proxy")
    spec = deployment["spec"]["template"]["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False
    pod = spec["securityContext"]
    assert (pod["runAsNonRoot"], pod["runAsUser"], pod["runAsGroup"]) == (
        True,
        65532,
        65532,
    )
    assert pod["seccompProfile"] == {"type": "RuntimeDefault"}
    [container] = spec["containers"]
    security = container["securityContext"]
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"] == {"drop": ["ALL"]}
    assert container["image"] == (
        f"ghcr.io/sifir-gun/contract-decision-graph/proxy-sortie@{DIGEST}"
    )
    assert container["resources"]["limits"] and container["resources"]["requests"]


@pytest.mark.chart
def test_configuration_dns_recommandee_le_proxy_resout_mistral(rendu):
    spec = named(rendu, "Deployment", "cdg-proxy")["spec"]["template"]["spec"]
    assert spec["dnsConfig"] == {"options": [{"name": "ndots", "value": "2"}]}
    annotations = named(rendu, "Deployment", "cdg-proxy")["metadata"]["annotations"]
    assert f"{IGNORE}dnsconfig-options" not in annotations  # satisfaite


@pytest.mark.chart
def test_liste_d_acces_et_adresses_privees_refusees(rendu):
    config, acl = configuration(rendu)
    assert config["acl_file"] == "/configuration/acl.yaml"
    assert config["allow_missing_role"] is True  # sans certificat client : « default »
    assert "allow_ranges" not in config and "unsafe_allow_private_ranges" not in config
    assert acl["default"]["action"] == "enforce"
    assert acl["default"]["allowed_domains"] == ["api.mistral.ai"]
    assert acl["services"] == []


@pytest.mark.chart
def test_profil_de_test_plage_du_cluster_et_domaine_du_serveur_factice():
    docs = render(
        "--set",
        "plagesAutorisees[0]=10.43.0.0/16",
        "--set",
        "domainesAutorises[0]=mistral-factice.cdg-tests.svc.cluster.local",
    )
    config, acl = configuration(docs)
    assert config["allow_ranges"] == ["10.43.0.0/16"]
    assert acl["default"]["allowed_domains"] == [
        "mistral-factice.cdg-tests.svc.cluster.local"
    ]


@pytest.mark.chart
def test_changement_de_configuration_relance_les_pods(rendu):
    before = named(rendu, "Deployment", "cdg-proxy")["spec"]["template"]["metadata"]
    after = named(
        render("--set", "domainesAutorises[0]=autre.example"), "Deployment", "cdg-proxy"
    )
    checksum = "checksum/configuration"
    assert (
        before["annotations"][checksum]
        != after["spec"]["template"]["metadata"]["annotations"][checksum]
    )


@pytest.mark.chart
def test_sondes_et_service(rendu):
    [container] = named(rendu, "Deployment", "cdg-proxy")["spec"]["template"]["spec"][
        "containers"
    ]
    for probe in ("startupProbe", "livenessProbe", "readinessProbe"):
        assert container[probe]["tcpSocket"] == {"port": "proxy"}, probe
    service = named(rendu, "Service", "cdg-proxy")
    assert (
        service["metadata"]["name"] == "cdg-proxy"
    )  # l'adresse du chart de l'application
    assert service["spec"]["ports"] == [
        {"name": "proxy", "port": 4750, "targetPort": "proxy", "protocol": "TCP"}
    ]


@pytest.mark.chart
def test_regles_reseau_entree_application_sortie_internet_hors_adresses_privees(rendu):
    [policy] = of_kind(rendu, "NetworkPolicy")
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    [ingress] = policy["spec"]["ingress"]
    [client] = ingress["from"]
    assert client == {
        "podSelector": {
            "matchLabels": {
                "app.kubernetes.io/name": "contract-decision-graph",
                "app.kubernetes.io/component": "web",
            }
        }
    }
    assert ingress["ports"] == [{"port": 4750, "protocol": "TCP"}]
    egress = policy["spec"]["egress"]
    dns, internet = egress
    assert {p["port"] for p in dns["ports"]} == {53}
    [block] = internet["to"]
    assert block["ipBlock"]["cidr"] == "0.0.0.0/0"
    assert sorted(block["ipBlock"]["except"]) == sorted(PRIVATE)
    for cidr in block["ipBlock"]["except"]:
        assert ipaddress.ip_network(cidr).is_private or cidr == "100.64.0.0/10"
    assert internet["ports"] == [{"port": 443, "protocol": "TCP"}]


@pytest.mark.chart
def test_profil_de_test_sortie_vers_le_serveur_factice():
    docs = render(
        "--set",
        "sortiesInternes[0].espaceDeNoms=cdg-tests",
        "--set",
        "sortiesInternes[0].selecteur.app=mistral-factice",
        "--set",
        "sortiesInternes[0].port=8080",
    )
    [policy] = of_kind(docs, "NetworkPolicy")
    internal = policy["spec"]["egress"][2]
    assert internal["ports"] == [{"port": 8080, "protocol": "TCP"}]
    [peer] = internal["to"]
    assert peer["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": "cdg-tests"}
    }
    assert peer["podSelector"] == {"matchLabels": {"app": "mistral-factice"}}


@pytest.mark.chart
def test_disponibilite_repartition_sans_bloquer_la_mise_a_jour(rendu):
    deployment = named(rendu, "Deployment", "cdg-proxy")
    assert deployment["spec"]["replicas"] == 2
    strategy = deployment["spec"]["strategy"]["rollingUpdate"]
    assert (strategy["maxSurge"], strategy["maxUnavailable"]) == (1, 0)
    spec = deployment["spec"]["template"]["spec"]
    anti = spec["affinity"]["podAntiAffinity"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in anti
    [spread] = spec["topologySpreadConstraints"]
    assert spread["maxSkew"] == 1
    budget = named(rendu, "PodDisruptionBudget", "cdg-proxy")
    assert budget["spec"]["minAvailable"] == 1


@pytest.mark.chart
def test_exclusions_de_kube_linter_justifiees(rendu):
    ignored = {
        key.removeprefix(IGNORE)
        for doc in rendu
        for key in (doc["metadata"].get("annotations") or {})
        if key.startswith(IGNORE)
    }
    # clients : les pods de l'application, rendus par son propre chart
    assert ignored == {
        "minimum-three-replicas",
        "no-node-affinity",
        "dangling-networkpolicypeer-podselector",
    }
