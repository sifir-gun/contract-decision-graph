"""Traces dans le chart (ADR 008, PR 2 de l'observabilité).

- La configuration montée sur `/app/config` est complète : `decision.yaml` et
  `tarifs.yaml`, sans quoi `--traces` ferait échouer le démarrage dans le cluster.
- Aucune destination par défaut : ni `--traces`, ni clé de Langfuse, ni sortie réseau.
- Avec une destination (un service du cluster seulement) : `--traces` et
  `--traces-identite aucune`, sauf réglage explicite ; clés en fichiers pour l'interface
  seule ; une sortie réseau vers la seule cible, sur son seul port.
"""

from pathlib import Path

import pytest
import yaml
from test_chart import (
    CHART,
    ROOT,
    chart_script,
    named,
    of_kind,
    pod_specs,
    refused,
    render,
    secret_files,
)

DESTINATION = (
    "http://langfuse-web.cdg-observabilite.svc.cluster.local:3000/api/public/otel"
)
TRACES = ["--set", f"traces.destination={DESTINATION}"]
LANGFUSE_KEYS = {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"}


def test_tarifs_du_chart_identiques_a_ceux_du_depot():
    chart = (CHART / "files" / "tarifs.yaml").read_text(encoding="utf-8")
    assert chart == (ROOT / "config" / "tarifs.yaml").read_text(encoding="utf-8")


def web(docs: list[dict]) -> dict:
    deployment = of_kind(docs, "Deployment")[0]["spec"]["template"]["spec"]
    [container] = [c for c in deployment["containers"] if c["name"] == "web"]
    return container


def global_options(container: dict) -> list[str]:
    """Options de la CLI avant la commande `web`."""
    args = container["args"]
    return args[: args.index("web")]


@pytest.mark.chart
@pytest.mark.parametrize("suffix", ["-configuration", "-configuration-a-venir"])
def test_configuration_montee_complete_rien_de_masque(suffix):
    """Le montage remplace tout `/app/config` de l'image : la ConfigMap porte chaque
    fichier de `config/`, à l'identique."""
    data = named(render(), "ConfigMap", suffix)["data"]
    config = ROOT / "config"
    assert set(data) == {p.name for p in config.iterdir() if p.suffix == ".yaml"}
    for name, text in data.items():
        assert yaml.safe_load(text) == yaml.safe_load((config / name).read_text())


@pytest.mark.chart
def test_sans_destination_aucune_trace():
    docs = render()
    for name, spec in pod_specs(docs):
        for container in spec["containers"]:
            args = container.get("args", [])
            assert "--traces" not in args and "--traces-identite" not in args, name
            assert not secret_files(spec, container) & LANGFUSE_KEYS, name
    egress = named(docs, "NetworkPolicy", "-web")["spec"]["egress"]
    assert [rule["ports"][0]["port"] for rule in egress] == [53, 5432, 4750]


@pytest.mark.chart
def test_destination_rendue_identite_aucune_par_defaut():
    docs = render(*TRACES)
    options = global_options(web(docs))
    assert options[options.index("--traces") + 1] == DESTINATION
    assert options[options.index("--traces-identite") + 1] == "aucune"
    for name, spec in pod_specs(docs):
        [main] = spec["containers"]
        if name == "cdg-contract-decision-graph":
            assert secret_files(spec, main) >= LANGFUSE_KEYS
        else:  # tâches et test : ni traces, ni clés
            assert "--traces" not in main.get("args", []), name
            assert not secret_files(spec, main) & LANGFUSE_KEYS, name


@pytest.mark.chart
def test_identite_sub_seulement_si_posee():
    docs = render(*TRACES, "--set", "traces.identite=sub")
    options = global_options(web(docs))
    assert options[options.index("--traces-identite") + 1] == "sub"


@pytest.mark.chart
def test_sortie_vers_la_seule_cible_des_traces():
    docs = render(*TRACES)
    egress = named(docs, "NetworkPolicy", "-web")["spec"]["egress"]
    assert [rule["ports"][0]["port"] for rule in egress] == [53, 5432, 4750, 3000]
    [peer] = egress[-1]["to"]
    assert peer == {
        "namespaceSelector": {
            "matchLabels": {"kubernetes.io/metadata.name": "cdg-observabilite"}
        },
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "langfuse-web"}},
    }
    assert egress[-1]["ports"] == [{"port": 3000, "protocol": "TCP"}]
    tasks = named(docs, "NetworkPolicy", "-taches")["spec"]["egress"]
    assert 3000 not in {rule["ports"][0]["port"] for rule in tasks}


@pytest.mark.chart
@pytest.mark.parametrize(
    "options",
    [
        # hors du cluster, même en https : refusé (souveraineté, ADR 008)
        ["--set", "traces.destination=https://cloud.langfuse.com/api/public/otel"],
        ["--set", "traces.destination=http://langfuse.example.org/api/public/otel"],
        ["--set", "traces.destination=http://pk:sk@langfuse.example.org/otel"],
        [*TRACES, "--set", "traces.identite=nom"],
    ],
)
def test_valeurs_de_traces_mal_formees_refusees(options):
    assert "traces" in refused(*chart_script().AUTH, *options)


@pytest.mark.chart
def test_traces_en_demonstration_refusees():
    message = refused(*chart_script().VARIANTS["demo"], *TRACES)
    assert "traces" in message and "réel" in message


@pytest.mark.chart
def test_variante_traces_verifiee_par_le_script():
    assert chart_script().VARIANTS["traces"] == [*chart_script().AUTH, *TRACES]


def test_fichiers_du_chart_suivent_config():
    """Chaque fichier de `config/` a sa copie dans le chart."""
    copies = {p.name for p in (CHART / "files").iterdir()}
    assert copies == {p.name for p in Path(ROOT / "config").iterdir()}
