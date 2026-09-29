"""Chart Helm (phase Kubernetes, PR C2 ; ADR 005).

Deux familles de tests :
- sans helm, avec la suite : la configuration du chart est celle du dépôt, la version de
  l'application celle de pyproject.toml ;
- avec helm (marqueur `chart`, option `--chart`) : le chart rendu tient ses garanties —
  sécurité de chaque pod, images par empreinte, aucun secret en clair, sondes, arrêt
  propre, relance sur changement de configuration, règles réseau sans ouverture large,
  tâches Helm, mode démonstration, repli par copie, et refus des valeurs mal formées.
  Lancés par le job `chart` de la CI et par `scripts/check.sh`.
"""

import importlib.util
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "chart" / "contract-decision-graph"
DIGEST = re.compile(r"^[a-z0-9.-]+(:\d+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$")


# --- sans helm ------------------------------------------------------------------------------


def test_configuration_du_chart_identique_a_celle_du_depot():
    chart = (CHART / "files" / "decision.yaml").read_text(encoding="utf-8")
    assert chart == (ROOT / "config" / "decision.yaml").read_text(encoding="utf-8")


def test_version_de_l_application_celle_du_projet():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert chart["appVersion"] == project["project"]["version"]
    assert chart["apiVersion"] == "v2"
    assert re.fullmatch(r"\d+\.\d+\.\d+", chart["version"])


def leaves(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, (*path, str(key)))
    else:
        yield path, node


def test_aucun_secret_dans_les_valeurs():
    """Les Secrets sont créés à part : les valeurs ne portent que leurs noms, et les
    noms de leurs clés (`cle…`), jamais une valeur secrète."""
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    secret = re.compile(
        r"(password|motdepasse|apikey|api_key|token|secretvalue)", re.IGNORECASE
    )
    for path, _ in leaves(values):
        key = path[-1]
        assert key.startswith("cle") or not secret.search(key), ".".join(path)


# --- avec helm --------------------------------------------------------------------------------


class UniqueKeys(yaml.SafeLoader):
    """Refuse une clé en double : PyYAML garde la dernière sans rien dire, kubeconform
    refuse le manifeste (étiquettes des pods en double, vu le 28/09)."""


def _unique_mapping(loader: UniqueKeys, node: yaml.MappingNode) -> dict:
    keys = [loader.construct_object(key) for key, _ in node.value]
    doubles = sorted({str(k) for k in keys if keys.count(k) > 1})
    if doubles:
        raise yaml.constructor.ConstructorError(
            None, None, f"clés en double : {', '.join(doubles)}", node.start_mark
        )
    return loader.construct_mapping(node, deep=True)


UniqueKeys.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def render(*options: str) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "cdg", str(CHART), "--namespace", "cdg", *options],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.load_all(result.stdout, Loader=UniqueKeys) if doc]


def refused(*options: str) -> str:
    result = subprocess.run(
        ["helm", "template", "cdg", str(CHART), *options],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "valeurs acceptées à tort"
    return result.stderr


def of_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d["kind"] == kind]


def named(docs: list[dict], kind: str, suffix: str) -> dict:
    [doc] = [d for d in of_kind(docs, kind) if d["metadata"]["name"].endswith(suffix)]
    return doc


def pod_specs(docs: list[dict]) -> list[tuple[str, dict]]:
    specs = []
    for doc in docs:
        if doc["kind"] in {"Deployment", "Job"}:
            specs.append((doc["metadata"]["name"], doc["spec"]["template"]["spec"]))
        elif doc["kind"] == "Pod":
            specs.append((doc["metadata"]["name"], doc["spec"]))
    return specs


def containers(spec: dict) -> list[dict]:
    return spec.get("initContainers", []) + spec["containers"]


def env(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


@pytest.fixture(scope="module")
def reel() -> list[dict]:
    return render()


@pytest.mark.chart
@pytest.mark.parametrize("variant", ["reel", "demo", "copie"])
def test_aucune_cle_en_double_dans_aucune_variante(variant):
    render(*chart_script().VARIANTS[variant])  # UniqueKeys lève sur une clé en double


def test_chargeur_du_rendu_refuse_une_cle_en_double():
    with pytest.raises(yaml.constructor.ConstructorError, match="clés en double : a"):
        yaml.load("a: 1\nb: 2\na: 3\n", Loader=UniqueKeys)


IGNORE = "ignore-check.kube-linter.io/"
# exclusions de kube-linter posées sur chaque objet, justifiées ici et dans l'ADR 005 :
# des tâches qui s'exécutent jusqu'au bout, sans sonde, un nouveau pod par tentative ; des
# Secrets créés hors du chart ; des noms à résoudre tous internes au cluster
IGNORED_BY_KIND = {
    "Deployment": {"dnsconfig-options", "minimum-three-replicas", "no-node-affinity"},
    "Job": {
        "dnsconfig-options",
        "no-liveness-probe",
        "no-node-affinity",
        "no-readiness-probe",
        "restart-policy",
    },
    "Pod": {
        "dnsconfig-options",
        "no-liveness-probe",
        "no-node-affinity",
        "no-readiness-probe",
        "restart-policy",
    },
}
# règles réseau vers le proxy de sortie et PostgreSQL, rendus hors du chart (PR C3)
IGNORED_BY_NAME = {
    ("NetworkPolicy", f"cdg-contract-decision-graph-{name}"): {
        "dangling-networkpolicypeer-podselector"
    }
    for name in ("web", "taches")
}


@pytest.mark.chart
def test_exclusions_de_kube_linter_par_objet_et_justifiees(reel):
    for doc in reel:
        annotations = doc["metadata"].get("annotations") or {}
        ignored = {
            key.removeprefix(IGNORE): reason
            for key, reason in annotations.items()
            if key.startswith(IGNORE)
        }
        name = doc["metadata"]["name"]
        by_kind = IGNORED_BY_KIND.get(doc["kind"], set())
        expected = IGNORED_BY_NAME.get((doc["kind"], name), by_kind)
        assert set(ignored) == expected, name
        assert all(len(reason.split()) >= 5 for reason in ignored.values()), name
    # deux réplicas, justifiés par la mémoire mesurée d'un réplica
    deployment = named(reel, "Deployment", "graph")["metadata"]["annotations"]
    assert "Mio" in deployment[f"{IGNORE}minimum-three-replicas"]


def test_configuration_de_kube_linter_sans_exclusion_globale_d_un_objet():
    config = yaml.safe_load((ROOT / "chart" / ".kube-linter.yaml").read_text())
    assert config["checks"]["addAllBuiltIn"] is True
    # seules restent globales les exclusions qui ne visent aucun objet : conventions d'une
    # organisation, ordre des clés, et la validation des schémas, que fait kubeconform
    assert set(config["checks"]["exclude"]) == {
        "required-annotation-email",
        "required-label-owner",
        "sorted-keys",
        "schema-validation",
    }


SANS_TACHES = (
    "--set",
    "taches.migrations.active=false",
    "--set",
    "taches.ingestion.active=false",
    "--set",
    "taches.controleConfiguration.active=false",
)


@pytest.mark.chart
@pytest.mark.parametrize("variant", ["reel", "demo", "copie", "sans-taches"])
def test_chaque_regle_reseau_designe_des_pods_du_rendu(variant):
    """Une règle qui ne désigne aucun pod est orpheline (kube-linter,
    dangling-networkpolicy) : la démonstration n'a pas de tâches (vu le 28/09)."""
    options = (
        SANS_TACHES if variant == "sans-taches" else chart_script().VARIANTS[variant]
    )
    docs = render(*options)
    pods = [
        (d["spec"]["template"] if d["kind"] != "Pod" else d)["metadata"]["labels"]
        for d in docs
        if d["kind"] in {"Deployment", "Job", "Pod"}
    ]
    for policy in of_kind(docs, "NetworkPolicy"):
        selector = policy["spec"]["podSelector"].get("matchLabels") or {}
        # un sélecteur vide désigne tous les pods : le refus par défaut
        assert not selector or any(selector.items() <= p.items() for p in pods), policy[
            "metadata"
        ]["name"]
    accounts = [a["metadata"]["name"] for a in of_kind(docs, "ServiceAccount")]
    assert any(a.endswith("-taches") for a in accounts) == bool(of_kind(docs, "Job"))


@pytest.mark.chart
def test_replicas_sur_des_noeuds_differents_sans_bloquer_la_mise_a_jour(reel):
    """Anti-affinité préférée (kube-linter, no-anti-affinity) : exigée, elle bloquerait la
    mise à jour progressive quand le cluster a autant de nœuds que de réplicas (le pod en
    plus ne trouverait aucun nœud libre) ; la répartition stricte reste celle des
    contraintes de topologie, qui tolèrent ce pod en plus."""
    deployment = named(reel, "Deployment", "graph")
    spec = deployment["spec"]["template"]["spec"]
    anti = spec["affinity"]["podAntiAffinity"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in anti
    [term] = anti["preferredDuringSchedulingIgnoredDuringExecution"]
    assert term["weight"] == 100
    assert term["podAffinityTerm"]["topologyKey"] == "kubernetes.io/hostname"
    selector = term["podAffinityTerm"]["labelSelector"]["matchLabels"]
    assert selector == deployment["spec"]["selector"]["matchLabels"]
    [spread] = spec["topologySpreadConstraints"]
    assert (spread["maxSkew"], spread["whenUnsatisfiable"]) == (1, "DoNotSchedule")


@pytest.mark.chart
def test_interface_redemarree_toujours_ecrit_explicitement(reel):
    deployment = named(reel, "Deployment", "graph")
    assert deployment["spec"]["template"]["spec"]["restartPolicy"] == "Always"


@pytest.mark.chart
def test_chaque_pod_restreint_sans_jeton_ni_privilege(reel):
    assert len(pod_specs(reel)) >= 4  # interface, trois tâches, test Helm
    for name, spec in pod_specs(reel):
        security = spec["securityContext"]
        assert security["runAsNonRoot"] is True, name
        assert security["seccompProfile"] == {"type": "RuntimeDefault"}, name
        assert spec["automountServiceAccountToken"] is False, name
        assert spec["serviceAccountName"].startswith("cdg-contract-decision-graph"), (
            name
        )
        for container in containers(spec):
            context = container["securityContext"]
            assert context["allowPrivilegeEscalation"] is False, name
            assert context["readOnlyRootFilesystem"] is True, name
            assert context["capabilities"] == {"drop": ["ALL"]}, name
            resources = container["resources"]
            assert set(resources) == {"requests", "limits"}, name
            assert set(resources["limits"]) == {"cpu", "memory"}, name


@pytest.mark.chart
def test_images_par_empreinte_jamais_par_etiquette(reel):
    for name, spec in pod_specs(reel):
        for container in containers(spec):
            assert DIGEST.fullmatch(container["image"]), (name, container["image"])
        for volume in spec.get("volumes", []):
            if "image" in volume:
                assert DIGEST.fullmatch(volume["image"]["reference"]), name


@pytest.mark.chart
def secret_files(spec: dict, container: dict) -> set[str]:
    """Fichiers de secrets que voit un conteneur : le volume projeté monté, en lecture
    seule, au dossier des secrets de l'application."""
    from conftest import SECRETS_DIR

    mounts = {m["name"]: m for m in container.get("volumeMounts", [])}
    files: set[str] = set()
    for volume in spec.get("volumes", []):
        mount = mounts.get(volume["name"])
        if mount is None or mount["mountPath"] != str(SECRETS_DIR):
            continue
        assert mount["readOnly"] is True
        assert volume["projected"]["defaultMode"] == 0o440
        for source in volume["projected"]["sources"]:
            files |= {item["path"] for item in source["secret"]["items"]}
    return files


SECRET_FILES = {
    "cdg-contract-decision-graph": {"APP_DB_PASSWORD", "MISTRAL_API_KEY"},
    "cdg-contract-decision-graph-migrations": {
        "APP_DB_PASSWORD",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    },
    "cdg-contract-decision-graph-ingestion": {
        "APP_DB_PASSWORD",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    },
    "cdg-contract-decision-graph-controle-configuration": {"APP_DB_PASSWORD"},
    "cdg-contract-decision-graph-test-sante": set(),
}


@pytest.mark.chart
def test_secrets_en_fichiers_en_lecture_seule_jamais_en_variables(reel):
    """CIS 5.4.1 : des fichiers plutôt que des variables d'environnement (kube-linter,
    read-secret-from-env-var) ; même dossier et mêmes noms que l'application. Le kubelet
    crée les fichiers d'un Secret projeté pour root : 0440, lus par le groupe du pod."""
    from cdg import settings

    assert not of_kind(reel, "Secret")
    assert {name for name, _ in pod_specs(reel)} == set(SECRET_FILES)
    for name, spec in pod_specs(reel):
        assert spec["securityContext"]["fsGroup"] == 65532, name
        for container in containers(spec):
            for variable in env(container).values():
                assert "secretKeyRef" not in variable.get("valueFrom", {}), name
                assert variable["name"] not in settings.SECRETS, name
        [main] = spec["containers"]
        assert secret_files(spec, main) == SECRET_FILES[name]
        assert SECRET_FILES[name] <= set(settings.SECRETS)
        for init in spec.get("initContainers", []):
            assert not secret_files(spec, init), (name, init["name"])


@pytest.mark.chart
def test_etiquettes_recommandees_sur_chaque_ressource(reel):
    for doc in reel:
        labels = doc["metadata"]["labels"]
        for key in ("name", "instance", "version", "managed-by", "part-of"):
            assert f"app.kubernetes.io/{key}" in labels, (doc["kind"], key)


@pytest.mark.chart
def test_interface_disponible_arret_propre_et_repartie(reel):
    deployment = of_kind(reel, "Deployment")[0]
    spec = deployment["spec"]
    assert spec["strategy"]["rollingUpdate"] == {"maxSurge": 1, "maxUnavailable": 0}
    pod = spec["template"]["spec"]
    [spread] = pod["topologySpreadConstraints"]
    assert spread["topologyKey"] == "kubernetes.io/hostname"
    assert spread["whenUnsatisfiable"] == "DoNotSchedule"
    [web] = pod["containers"]
    assert web["lifecycle"]["preStop"] == {"sleep": {"seconds": 10}}
    # pause avant l'arrêt + délai laissé aux requêtes + marge
    assert pod["terminationGracePeriodSeconds"] == 10 + 60 + 10
    assert web["startupProbe"]["httpGet"]["path"] == "/sante/demarrage"
    assert web["livenessProbe"]["httpGet"]["path"] == "/sante/vie"
    assert web["readinessProbe"]["httpGet"]["path"] == "/sante/pret"
    pdb = of_kind(reel, "PodDisruptionBudget")[0]
    assert pdb["spec"]["minAvailable"] == 1


@pytest.mark.chart
def test_lots_de_l_embedder_et_memoire_au_dessus_des_pics_mesures(reel):
    """Mesure du 28/09 (ADR 005) : 2,55 à 2,65 Gio au chargement du modèle, 2,9 Gio pour
    un lot de 16 passages longs, plus de 3,7 Gio pour 64 ; fastembed prend 256 par défaut.
    Les limites de 3 et 3,5 Gio tuaient l'interface au démarrage et l'ingestion."""
    web = of_kind(reel, "Deployment")[0]["spec"]["template"]["spec"]["containers"][0]
    assert web["args"][web["args"].index("--lot-embedding") + 1] == "16"
    assert web["resources"]["limits"]["memory"] == "4Gi"
    ingestion = named(reel, "Job", "-ingestion")["spec"]["template"]["spec"]
    job = ingestion["containers"][0]
    assert job["args"][job["args"].index("--lot-embedding") + 1] == "16"
    assert job["resources"]["limits"]["memory"] == "4Gi"


@pytest.mark.chart
def test_fils_de_l_embedder_egaux_a_la_limite_cpu(reel):
    web = of_kind(reel, "Deployment")[0]["spec"]["template"]["spec"]["containers"][0]
    args = web["args"]
    threads = args[args.index("--fils-embedding") + 1]
    assert threads == web["resources"]["limits"]["cpu"] == "2"
    ingestion = named(reel, "Job", "-ingestion")["spec"]["template"]["spec"]
    args = ingestion["containers"][0]["args"]
    assert args[args.index("--fils-embedding") + 1] == "2"


@pytest.mark.chart
def test_changement_de_configuration_relance_les_pods(reel):
    before = of_kind(reel, "Deployment")[0]["spec"]["template"]["metadata"]
    other = render("--set", "configuration.decision=min_margin: 0.06")
    after = of_kind(other, "Deployment")[0]["spec"]["template"]["metadata"]
    key = "checksum/configuration"
    assert before["annotations"][key] != after["annotations"][key]
    configuration = named(reel, "ConfigMap", "-configuration")["data"]["decision.yaml"]
    expected = (ROOT / "config" / "decision.yaml").read_text(encoding="utf-8")
    assert configuration.strip() == expected.strip()


@pytest.mark.chart
def test_sortie_par_le_proxy_seulement(reel):
    web = of_kind(reel, "Deployment")[0]["spec"]["template"]["spec"]["containers"][0]
    variables = env(web)
    assert variables["HTTPS_PROXY"]["value"] == "http://cdg-proxy:4750"
    assert "MISTRAL_SERVER_URL" not in variables  # l'adresse du SDK, par défaut
    other = render("--set", "llm.adresseApi=http://mistral-factice.test:8080")
    web = of_kind(other, "Deployment")[0]["spec"]["template"]["spec"]["containers"][0]
    assert env(web)["MISTRAL_SERVER_URL"]["value"] == "http://mistral-factice.test:8080"


@pytest.mark.chart
def test_regles_reseau_refus_par_defaut_sans_ouverture_large(reel):
    policies = of_kind(reel, "NetworkPolicy")
    default = named(reel, "NetworkPolicy", "-refus-par-defaut")
    assert default["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
    for policy in policies:
        for rule in policy["spec"].get("egress", []):
            for peer in rule.get("to", []):
                assert "ipBlock" not in peer, policy["metadata"]["name"]
            ports = {p["port"] for p in rule.get("ports", [])}
            assert 443 not in ports, policy["metadata"]["name"]
    web = named(reel, "NetworkPolicy", "-web")["spec"]
    targets = [rule["ports"][0]["port"] for rule in web["egress"]]
    assert targets == [53, 5432, 4750]  # DNS, PostgreSQL, proxy de sortie


@pytest.mark.chart
def test_taches_helm_ordonnees_limitees_et_nettoyees(reel):
    jobs = {job["metadata"]["name"].split("-")[-1]: job for job in of_kind(reel, "Job")}
    hooks = {
        name: job["metadata"]["annotations"]["helm.sh/hook"]
        for name, job in jobs.items()
    }
    assert hooks == {
        "migrations": "pre-install,pre-upgrade",
        "ingestion": "post-install,post-upgrade",
        "configuration": "pre-upgrade",
    }
    weights = {
        name: int(job["metadata"]["annotations"]["helm.sh/hook-weight"])
        for name, job in jobs.items()
    }
    assert weights["configuration"] < weights["migrations"]  # contrôle d'abord
    for job in jobs.values():
        assert job["spec"]["backoffLimit"] == 2
        assert job["spec"]["ttlSecondsAfterFinished"] == 3600
        assert "activeDeadlineSeconds" in job["spec"]
    migrations = jobs["migrations"]["spec"]["template"]["spec"]
    assert "POSTGRES_PASSWORD" in secret_files(migrations, migrations["containers"][0])
    control = jobs["configuration"]["spec"]["template"]["spec"]
    # app_role suffit au contrôle
    assert "POSTGRES_PASSWORD" not in secret_files(control, control["containers"][0])
    forced = render("--set", "taches.controleConfiguration.passerOutre=true")
    assert not [
        j for j in of_kind(forced, "Job") if "configuration" in j["metadata"]["name"]
    ]


@pytest.mark.chart
def test_regle_reseau_des_taches_posee_avant_elles(reel):
    """Les tâches d'avant mise à jour tournent avant les ressources ordinaires du chart :
    leur règle réseau est un crochet, comme leur compte, posé avant elles. Sinon une mise à
    jour qui change de base (restauration) les laisse sous l'ancienne règle, qui ne mène
    qu'à l'ancienne base (scénario du cluster, 28/09)."""
    annotations = named(reel, "NetworkPolicy", "-taches")["metadata"]["annotations"]
    account = named(reel, "ServiceAccount", "-taches")["metadata"]["annotations"]
    assert annotations["helm.sh/hook"] == account["helm.sh/hook"]
    assert annotations["helm.sh/hook-delete-policy"] == (
        "before-hook-creation,hook-succeeded"
    )
    weights = [
        int(job["metadata"]["annotations"]["helm.sh/hook-weight"])
        for job in of_kind(reel, "Job")
    ]
    assert int(annotations["helm.sh/hook-weight"]) < min(weights)


@pytest.mark.chart
@pytest.mark.parametrize("variant", ["reel", "demo", "copie", "sans-taches"])
def test_aucune_ressource_de_crochet_ne_survit_a_un_deploiement_reussi(variant):
    """Helm 4.3.0 ne supprime à la désinstallation que les ressources ordinaires de la
    release (pkg/action/uninstall.go) : une ressource de crochet laissée après son crochet
    survivrait à `helm uninstall`. Chacune est donc supprimée après succès, et les
    journaux d'une tâche sont recopiés par Helm avant (hook-output-log-policy). Une tâche
    en échec reste pour le diagnostic, jusqu'à son délai de conservation ; son nettoyage
    après désinstallation est documenté (docs/exploitation.md) et testé (scénario du
    cluster)."""
    options = (
        SANS_TACHES if variant == "sans-taches" else chart_script().VARIANTS[variant]
    )
    docs = render(*options)
    hooks = [
        d for d in docs if "helm.sh/hook" in (d["metadata"].get("annotations") or {})
    ]
    assert hooks
    for doc in hooks:
        annotations = doc["metadata"]["annotations"]
        policies = annotations.get("helm.sh/hook-delete-policy", "").split(",")
        name = f"{doc['kind']}/{doc['metadata']['name']}"
        assert "before-hook-creation" in policies and "hook-succeeded" in policies, name
        assert "hook-failed" not in policies, name  # diagnostic d'une tâche en échec
    for job in of_kind(docs, "Job"):
        annotations = job["metadata"]["annotations"]
        assert annotations["helm.sh/hook-output-log-policy"] == (
            "hook-succeeded,hook-failed"
        )


@pytest.mark.chart
def test_volume_image_du_modele_en_lecture_seule(reel):
    pod = of_kind(reel, "Deployment")[0]["spec"]["template"]["spec"]
    [volume] = [v for v in pod["volumes"] if v["name"] == "modele"]
    assert volume["image"]["reference"].startswith(
        "ghcr.io/sifir-gun/contract-decision-graph/modele-embedding@sha256:"
    )
    [mount] = [m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "modele"]
    assert mount["readOnly"] is True and mount["mountPath"] == "/modele"
    assert "initContainers" not in pod


@pytest.mark.chart
def test_repli_par_copie_sans_outil_dans_l_image_du_modele():
    docs = render("--set", "modele.montage=copie")
    pod = of_kind(docs, "Deployment")[0]["spec"]["template"]["spec"]
    first, second = pod["initContainers"]
    assert first["image"].startswith("docker.io/library/busybox@sha256:")
    assert second["image"].startswith(
        "ghcr.io/sifir-gun/contract-decision-graph/modele-embedding@sha256:"
    )
    assert second["command"][0] == "/outils/busybox"  # le programme vient de busybox
    assert "tar" in second["command"][-1]  # liens physiques conservés
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert "emptyDir" in volumes["modele"] and "image" not in volumes["modele"]


@pytest.mark.chart
def test_mode_demonstration_sans_base_ni_modele_ni_cle():
    docs = render("--set", "mode=demo", "--set", "replicas=1")
    assert not of_kind(docs, "Job")
    assert not of_kind(docs, "PodDisruptionBudget")  # un seul réplica
    pod = of_kind(docs, "Deployment")[0]["spec"]["template"]["spec"]
    web = pod["containers"][0]
    assert "--demo" in web["args"] and "env" not in web
    assert not {"modele", "secrets"} & {v["name"] for v in pod["volumes"]}
    assert web["resources"]["limits"]["memory"] == "256Mi"


@pytest.mark.chart
@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--set", "image.digest=latest"], "digest"),
        (["--set", "inconnue=1"], "inconnue"),
        (["--set", "ressources.reel.limits.cpu=1500m"], "cpu"),
        (["--set", "modele.montage=disque"], "montage"),
        (["--set", "llm.adresseApi=http://u:p@mistral-factice.test"], "adresseApi"),
    ],
)
def test_valeurs_mal_formees_refusees_par_le_schema(options, message):
    assert message in refused(*options)


# --- validation statique : scripts/chart.py -------------------------------------------------


TOOL = re.compile(r"[^@\s]+:v(?P<version>\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}")


def chart_script():
    path = ROOT / "scripts" / "chart.py"
    spec = importlib.util.spec_from_file_location("chart_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    def __init__(self, outputs=None):
        self.commands, self.outputs = [], outputs or {}

    def __call__(self, command, **_):
        self.commands.append(command)
        out = self.outputs.get(command[1] if command[0] == "helm" else "", "")
        return subprocess.CompletedProcess(command, 0, out, "")


def test_outils_de_validation_figes():
    module = chart_script()
    assert module.HELM_VERSION == "v4.3.0"  # au-delà des avis publiés (≤ 4.1.3)
    assert TOOL.fullmatch(module.KUBECONFORM)["version"] == "0.8.0"
    assert TOOL.fullmatch(module.KUBE_LINTER)["version"] == "0.8.3"
    assert module.KUBERNETES == "1.36.4"  # canal stable de k3s (ADR 005)


def test_version_de_helm_verifiee(tmp_path, capsys):
    module = chart_script()
    wrong = Recorder({"version": "v3.18.0"})
    assert module.verify(tmp_path, run=wrong) == 1
    assert "v4.3.0" in capsys.readouterr().err
    assert wrong.commands == [["helm", "version", "--template", "{{.Version}}"]]


def test_lint_rendu_schemas_et_bonnes_pratiques(tmp_path):
    module = chart_script()
    run = Recorder({"version": "v4.3.0", "template": "kind: ConfigMap\n"})
    assert module.verify(tmp_path, run=run) == 0
    tools = [c[0] if c[0] == "helm" else c[c.index("--rm") + 1 :] for c in run.commands]
    # le chart de l'application et celui du proxy de sortie (PR C3), chacun ses variantes
    renders = [
        (chart, name)
        for chart, variants, prefix in module.CHARTS
        for name in (f"{prefix}{variant}" for variant in variants)
    ]
    assert {chart.name for chart, _ in renders} == {
        "contract-decision-graph",
        "cdg-proxy",
        "cdg-postgres",
    }
    lints = [c for c in run.commands if c[:2] == ["helm", "lint"]]
    assert [c[3] for c in lints] == [str(chart) for chart, _ in renders]
    assert all("--strict" in c for c in lints)
    templates = [c for c in run.commands if c[:2] == ["helm", "template"]]
    assert len(templates) == len(renders)
    for _, name in renders:
        assert (tmp_path / f"{name}.yaml").read_text() == "kind: ConfigMap\n"
    [kubeconform] = [c for c in run.commands if module.KUBECONFORM in c]
    assert {"-strict", "-summary"} <= set(kubeconform)
    assert kubeconform[kubeconform.index("-kubernetes-version") + 1] == "1.36.4"
    # ressources propres à CloudNativePG et au greffon : schémas inconnus de kubeconform,
    # validées par le serveur d'API du cluster de test (tests/test_cluster.py)
    skipped = kubeconform[kubeconform.index("-skip") + 1].split(",")
    assert skipped == ["Cluster", "ObjectStore", "ScheduledBackup"]
    # kube-linter relie les objets d'un même lot : chaque variante à part, sinon le
    # budget d'interruption du rendu réel est rapproché du Deployment de la démo (28/09)
    linters = [c for c in run.commands if module.KUBE_LINTER in c]
    assert [c[-1] for c in linters] == [f"/rendu/{name}.yaml" for _, name in renders]
    assert all("lint" in c and "/configuration/.kube-linter.yaml" in c for c in linters)
    assert tools  # la liste des commandes n'est pas vide


# --- CI : job chart, mêmes vérifications que check.sh -----------------------------------------

HELM_SHA256 = "86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb"


def ci_chart_job() -> dict:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    return workflow["jobs"]["chart"]


def test_helm_installe_en_ci_depuis_l_archive_officielle_verifiee():
    job = ci_chart_job()
    [install] = [s for s in job["steps"] if s.get("id") == "installation-helm"]
    run = install["run"]
    version = chart_script().HELM_VERSION
    assert f"https://get.helm.sh/helm-{version}-linux-amd64.tar.gz" in run
    assert f"{HELM_SHA256}  " in run and "sha256sum --check --strict" in run


def test_validation_et_rendu_en_ci_et_en_local():
    runs = [s.get("run", "") for s in ci_chart_job()["steps"]]
    verify = 'uv run --no-sync python scripts/chart.py verifier --dossier "$RUNNER_TEMP/chart"'
    tests = "uv run --no-sync pytest -m chart --chart"
    assert runs.index(verify) < runs.index(tests)
    script = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert verify in script and tests in script
