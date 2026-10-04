"""Langfuse dans le cluster de test de la CI (ADR 008, PR 2) : `cluster/langfuse.yaml` et
son installation par `scripts/cluster.py`, sans cluster.

- Pods sans root ni privilège, ressources bornées, images figées par empreinte et
  couvertes, une à une, par l'exception de signatures limitée aux tests.
- Secrets par référence au Secret `langfuse`, jamais en clair.
- Partie libre seule : aucune clé de licence ; télémétrie, assistant, inscription coupés ;
  aucun utilisateur.
- Règles réseau : rien hors du cluster ; entrée des traces depuis l'interface seulement.
- Stockage : identité S3 limitée au seau de Langfuse.
- Profil `ci` seulement : le profil local (8 Go pour Docker) ne l'installe pas.
"""

import re
from datetime import date

import pytest
import yaml
from test_cluster_outillage import cluster

SECRET = re.compile(r"(PASSWORD|SECRET|SALT|ENCRYPTION_KEY|REDIS_AUTH|ACCESS_KEY)")
TODAY = date(2026, 10, 3)


def docs() -> list[dict]:
    return [d for d in yaml.safe_load_all(cluster().langfuse_manifest()) if d]


def of_kind(kind: str) -> list[dict]:
    return [d for d in docs() if d["kind"] == kind]


def pods() -> dict[str, dict]:
    return {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]
        for d in of_kind("Deployment")
    }


def variables(container: dict) -> dict[str, dict]:
    return {e["name"]: e for e in container.get("env", [])}


def test_tout_dans_l_espace_de_l_observabilite():
    module = cluster()
    assert "${" not in module.langfuse_manifest()
    assert {d["metadata"]["namespace"] for d in docs()} == {module.OBSERVABILITY}
    assert set(pods()) == {
        "langfuse-web",
        "langfuse-worker",
        "langfuse-clickhouse",
        "langfuse-valkey",
        "langfuse-postgres",
    }


def test_pods_sans_root_ni_privilege_ressources_bornees():
    module = cluster()
    for name, spec in pods().items():
        assert spec["automountServiceAccountToken"] is False, name
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        for container in spec["containers"]:
            context = container["securityContext"]
            assert context["allowPrivilegeEscalation"] is False, name
            assert context["capabilities"] == {"drop": ["ALL"]}, name
            for bound in ("requests", "limits"):
                assert set(container["resources"][bound]) == {"cpu", "memory"}, name
            assert container["image"] in module.LANGFUSE_IMAGES.values(), name


def test_secrets_par_reference_jamais_en_clair():
    found = 0
    for name, spec in pods().items():
        for container in spec["containers"]:
            for key, variable in variables(container).items():
                if SECRET.search(key):
                    found += 1
                    reference = variable["valueFrom"]["secretKeyRef"]
                    assert reference["name"] == "langfuse", (name, key)
                    assert "value" not in variable, (name, key)
    assert found >= 15
    for name in ("langfuse-web", "langfuse-worker"):
        [container] = pods()[name]["containers"]
        url = variables(container)["DATABASE_URL"]["value"]
        assert "$(POSTGRES_PASSWORD)" in url


def test_langfuse_sans_licence_commerciale_ni_appel_sortant():
    module = cluster()
    assert "LANGFUSE_EE_LICENSE_KEY" not in module.langfuse_manifest()
    for name in ("langfuse-web", "langfuse-worker"):
        [container] = pods()[name]["containers"]
        env = variables(container)
        assert env["TELEMETRY_ENABLED"]["value"] == "false", name
        assert env["LANGFUSE_IN_APP_AGENT_ENABLED"]["value"] == "false", name
        assert env["CLICKHOUSE_CLUSTER_ENABLED"]["value"] == "false", name
        assert not [
            v for v in env if v.startswith(("LANGFUSE_AI_", "LANGFUSE_INIT_USER"))
        ]
        assert "NEXT_PUBLIC_LANGFUSE_CLOUD_REGION" not in env, name
        assert env["LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT"]["value"] == (
            "http://seaweedfs.cdg-stockage.svc.cluster.local:8333"
        )
    [web] = pods()["langfuse-web"]["containers"]
    assert variables(web)["AUTH_DISABLE_SIGNUP"]["value"] == "true"


def test_regles_reseau_aucune_sortie_hors_du_cluster():
    policies = {p["metadata"]["name"]: p["spec"] for p in of_kind("NetworkPolicy")}
    assert policies["refus-par-defaut"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
    }
    allowed_egress = [
        {"podSelector": {}},
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
            },
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        },
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "cdg-stockage"}
            },
            "podSelector": {"matchLabels": {"app": "seaweedfs"}},
        },
    ]
    for name, spec in policies.items():
        for rule in spec.get("egress", []):
            for peer in rule["to"]:
                assert peer in allowed_egress, name
    stockage = policies["stockage"]["egress"]
    assert [p["port"] for rule in stockage for p in rule["ports"]] == [8333]
    [entry] = policies["entree-des-traces"]["ingress"]
    assert entry["ports"] == [{"port": 3000, "protocol": "TCP"}]
    [peer] = entry["from"]
    assert peer["namespaceSelector"] == {
        "matchLabels": {"kubernetes.io/metadata.name": "cdg"}
    }
    assert peer["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "contract-decision-graph",
        "app.kubernetes.io/instance": "cdg",
        "app.kubernetes.io/component": "web",
    }


def test_identite_s3_de_langfuse_limitee_a_son_seau():
    module = cluster()
    config = module.s3_identities(("cdg-a", "cdg-s"), ("lf-a", "lf-s"))
    identities = {i["name"]: i for i in config["identities"]}
    assert identities["cdg"]["actions"] == ["Admin", "Read", "Write", "List", "Tagging"]
    assert identities["langfuse"]["credentials"] == [
        {"accessKey": "lf-a", "secretKey": "lf-s"}
    ]
    assert identities["langfuse"]["actions"] == [
        "Read:langfuse",
        "Write:langfuse",
        "List:langfuse",
    ]
    assert set(module.s3_identities(("a", "s"), None)["identities"][0]) == {
        "name",
        "credentials",
        "actions",
    }
    assert [
        i["name"] for i in module.s3_identities(("a", "s"), None)["identities"]
    ] == ["cdg"]


def test_profil_ci_seul_avec_langfuse():
    module = cluster()
    assert module.PROFILES["ci"].langfuse is True
    assert module.PROFILES["local"].langfuse is False
    assert module.TRACES_URL == (
        "http://langfuse-web.cdg-observabilite.svc.cluster.local:3000/api/public/otel"
    )


def test_images_sans_signature_couvertes_par_l_exception():
    module = cluster()
    unsigned = {k: v for k, v in module.LANGFUSE_IMAGES.items() if k != "POSTGRES"}
    module.check_unsigned(unsigned.values(), TODAY)
    with pytest.raises(module.ClusterError, match="sans signature"):
        module.check_unsigned(["docker.io/library/busybox:1@sha256:" + "0" * 64], TODAY)
    with pytest.raises(module.ClusterError, match="expirée"):
        module.check_unsigned(unsigned.values(), date(2027, 1, 1))
    compose = yaml.safe_load(
        (module.ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    assert (
        module.LANGFUSE_IMAGES["POSTGRES"]
        == "docker.io/" + (compose["services"]["db"]["image"])
    )


def test_disque_ephemere_des_pods_de_langfuse_tire_des_statistiques_du_kubelet():
    summary = {
        "pods": [
            {
                "podRef": {"namespace": "cdg-observabilite", "name": "langfuse-web-1"},
                "ephemeral-storage": {"usedBytes": 52_428_800},
            },
            {
                "podRef": {"namespace": "cdg", "name": "cdg-web-1"},
                "ephemeral-storage": {"usedBytes": 1},
            },
            {"podRef": {"namespace": "cdg-observabilite", "name": "sans-mesure"}},
        ]
    }
    assert cluster().ephemeral_usage(summary, "cdg-observabilite") == {
        "langfuse-web-1": 52_428_800
    }


def test_mesure_de_langfuse_relevee_par_la_ci_au_repos_et_apres_les_scenarios():
    from test_cluster_outillage import SCRIPT, ci_job

    steps = ci_job()["steps"]
    runs = [s.get("run", "") for s in steps]
    measure = f"{SCRIPT} mesure-langfuse"
    rest, after = [i for i, run in enumerate(runs) if run == measure]
    scenarios = next(i for i, run in enumerate(runs) if "pytest -m cluster" in run)
    assert runs.index(f'{SCRIPT} installer --dossier "$RUNNER_TEMP/cluster"') < rest
    assert rest < scenarios < after
    assert steps[rest]["id"].startswith("diagnostic-")
    assert steps[after]["id"].startswith("diagnostic-")
    assert steps[after]["if"] == "always()"


def test_utilisateur_numerique_pour_chaque_pod():
    """Les images de Langfuse déclarent un utilisateur nommé (nextjs, expressjs, UID
    1001) : avec runAsNonRoot sans runAsUser, le kubelet refuse le conteneur (« image
    has non-numeric user »). Chaque pod pose donc un utilisateur numérique."""
    expected = {
        "langfuse-web": 1001,
        "langfuse-worker": 1001,
        "langfuse-clickhouse": 101,
        "langfuse-valkey": 999,
        "langfuse-postgres": 999,
    }
    for name, spec in pods().items():
        context = spec["securityContext"]
        assert context["runAsUser"] == expected[name], name
        assert context["runAsGroup"] == expected[name], name


def test_web_ecoute_sur_toutes_les_adresses_du_pod():
    """Le serveur autonome de Next.js écoute sur $HOSTNAME, que le moteur de conteneurs
    pose au nom du pod : sans 0.0.0.0, ni la redirection de port (localhost) ni les
    sondes ne le joignent."""
    [web] = pods()["langfuse-web"]["containers"]
    assert variables(web)["HOSTNAME"]["value"] == "0.0.0.0"


def test_etiquettes_du_web_celles_que_vise_la_regle_du_chart():
    """La règle de sortie du chart (traces.cible par défaut) désigne le pod web de
    Langfuse par ses étiquettes : elles y sont, dans l'espace attendu."""
    values = yaml.safe_load(
        (
            cluster().ROOT / "chart" / "contract-decision-graph" / "values.yaml"
        ).read_text(encoding="utf-8")
    )
    target = values["traces"]["cible"]
    [web] = [
        d for d in of_kind("Deployment") if d["metadata"]["name"] == "langfuse-web"
    ]
    labels = web["spec"]["template"]["metadata"]["labels"]
    assert target["selecteur"].items() <= labels.items()
    assert web["metadata"]["namespace"] == target["espaceDeNoms"]
    [service] = [
        s for s in of_kind("Service") if s["metadata"]["name"] == "langfuse-web"
    ]
    assert service["spec"]["ports"][0]["port"] == target["port"]


def storage_docs() -> list[dict]:
    module = cluster()
    text = module._manifest("seaweedfs.yaml", SEAWEEDFS=module.SEAWEEDFS)
    return [d for d in yaml.safe_load_all(text) if d]


def test_seaweedfs_sans_telemetrie():
    """SeaweedFS 4.47 envoie par défaut des statistiques à telemetry.seaweedfs.com
    (`-master.telemetry`, vrai par défaut) ; il stocke les sauvegardes et les événements
    bruts de Langfuse : coupée, dans le cluster comme sur le poste."""
    [deployment] = [d for d in storage_docs() if d["kind"] == "Deployment"]
    [container] = deployment["spec"]["template"]["spec"]["containers"]
    assert "-master.telemetry=false" in container["args"]
    compose = yaml.safe_load(
        (cluster().ROOT / "compose.observabilite.yaml").read_text(encoding="utf-8")
    )
    assert (
        "-master.telemetry=false"
        in compose["services"]["langfuse-seaweedfs"]["command"]
    )


def test_stockage_sans_sortie_entree_s3_bornee():
    """Espace cdg-stockage : refus par défaut ; entrée S3 (8333) depuis la base (cdg, et
    le greffon de sauvegarde, cnpg-system) et Langfuse seulement ; sortie : le DNS."""
    policies = {
        d["metadata"]["name"]: d["spec"]
        for d in storage_docs()
        if d["kind"] == "NetworkPolicy"
    }
    assert policies["refus-par-defaut"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
    }
    [entry] = policies["entree-s3"]["ingress"]
    assert entry["ports"] == [{"port": 8333, "protocol": "TCP"}]
    assert sorted(
        peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"]
        for peer in entry["from"]
    ) == ["cdg", "cdg-observabilite", "cnpg-system"]
    egress = [rule for spec in policies.values() for rule in spec.get("egress", [])]
    assert [p["port"] for rule in egress for p in rule["ports"]] == [53, 53]
    for rule in egress:
        for peer in rule["to"]:
            assert peer["podSelector"] == {"matchLabels": {"k8s-app": "kube-dns"}}


def test_aucun_secret_en_argument():
    """`$(VAR)` dans les arguments est développé par le kubelet : le secret se lirait dans
    la table des processus du nœud. Valkey lit son mot de passe dans un fichier de
    configuration écrit au démarrage depuis l'environnement."""
    for name, spec in pods().items():
        for container in spec["containers"]:
            for part in [*container.get("command", []), *container.get("args", [])]:
                assert "$(" not in part, (name, part)


def test_aucune_variable_venue_d_ailleurs():
    """envFrom ferait entrer des variables que les tests ne voient pas (clé de licence
    comprise) : chaque variable est nommée dans le manifeste."""
    for name, spec in pods().items():
        for container in spec["containers"]:
            assert "envFrom" not in container, name


def test_exception_illisible_refusee_par_l_installation(monkeypatch):
    """Toute erreur de l'exception (dates mal typées comprises) arrête l'installation par
    une erreur nommée, jamais une trace brute."""
    module = cluster()

    class Chaine:
        SIGNATURE_EXCEPTIONS = None

        @staticmethod
        def signature_exceptions(path, today):
            raise TypeError("exception x : dates decidee et expire attendues")

    monkeypatch.setattr(module, "_chaine", lambda: Chaine)
    with pytest.raises(module.ClusterError, match="dates"):
        module.check_unsigned(["x"], TODAY)
