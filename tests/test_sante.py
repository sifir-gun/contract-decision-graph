"""Sondes de santé (phase Kubernetes, point 3) : vie, démarrage, disponibilité, sur un
port à part, sans logique métier ni donnée, en GET comme en HEAD. L'interface répondait
405 à HEAD / et n'avait aucun point de santé."""

import threading

import pytest
from fastapi.testclient import TestClient

from cdg.adapters.web import sante

PROBES = ["/sante/vie", "/sante/demarrage", "/sante/pret"]


class State:
    """État lu par les sondes : réglé par le test."""

    def __init__(self, started=True, database=True, draining=False):
        self.started, self.database, self.draining = started, database, draining

    def checks(self) -> sante.Checks:
        return sante.Checks(
            started=lambda: self.started,
            database=lambda: self.database,
            draining=lambda: self.draining,
        )


def probe(state: State) -> TestClient:
    # l'hôte des sondes du kubelet est l'adresse du pod : aucune liste d'hôtes admis
    return TestClient(
        sante.create_health_app(state.checks()), base_url="http://10.42.0.7:8081"
    )


@pytest.mark.parametrize("path", PROBES)
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_sondes_en_get_et_en_head(path, method):
    response = probe(State()).request(method, path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_vie_repond_meme_pendant_le_chargement_et_l_arret():
    web = probe(State(started=False, database=False, draining=True))
    assert web.get("/sante/vie").status_code == 200


def test_demarrage_attend_le_modele():
    state = State(started=False)
    web = probe(state)
    assert web.get("/sante/demarrage").status_code == 503
    state.started = True
    assert web.get("/sante/demarrage").status_code == 200


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (State(started=False), "modele non charge"),
        (State(database=False), "base injoignable"),
        (State(draining=True), "arret en cours"),
    ],
)
def test_disponibilite_dit_pourquoi_sans_donnee(state, reason):
    response = probe(state).get("/sante/pret")
    assert response.status_code == 503
    assert response.json() == {"statut": "pas_pret", "raisons": [reason]}


def test_une_base_qui_echoue_rend_pas_pret_sans_le_message(caplog):
    def broken() -> bool:
        raise RuntimeError("mot de passe de la base : ne doit jamais sortir")

    checks = sante.Checks(started=lambda: True, database=broken, draining=lambda: False)
    web = TestClient(sante.create_health_app(checks))
    response = web.get("/sante/pret")
    assert response.status_code == 503
    assert "mot de passe" not in response.text + caplog.text
    assert response.json()["raisons"] == ["base injoignable"]


def test_aucune_autre_route_sur_le_port_des_sondes():
    web = probe(State())
    for path in ["/", "/analyse", "/journal", "/contrats/c1", "/static/style.css"]:
        assert web.get(path).status_code == 404
    assert web.post("/sante/pret").status_code == 405


def test_sondes_simultanees_sans_blocage():
    gate = threading.Event()

    def slow_database() -> bool:
        gate.wait(5)
        return True

    checks = sante.Checks(
        started=lambda: True, database=slow_database, draining=lambda: False
    )
    web = TestClient(sante.create_health_app(checks))
    # la vie ne dépend pas de la base : elle répond pendant qu'une disponibilité attend
    ready = threading.Thread(target=lambda: web.get("/sante/pret"))
    ready.start()
    assert web.get("/sante/vie").status_code == 200
    gate.set()
    ready.join(5)


# --- deux serveurs : l'interface et les sondes, chacun sur son port ---------------------------


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_deux_serveurs_reels_chacun_ses_routes():
    import time

    import httpx
    from web_helpers import memory_service

    from cdg.adapters import journaux
    from cdg.adapters.web import server
    from cdg.adapters.web.app import create_app

    ui, health = free_port(), free_port()
    probes = server.Probes(
        sante.create_health_app(State().checks()), "127.0.0.1", health
    )
    main, probe_server = server.servers(
        create_app(memory_service()),
        "127.0.0.1",
        ui,
        log_config=journaux.config("texte"),
        probes=probes,
    )
    runner = threading.Thread(target=server.run, args=(main, probe_server), daemon=True)
    runner.start()
    try:
        for _ in range(200):
            if main.started and probe_server is not None and probe_server.started:
                break
            time.sleep(0.02)
        with httpx.Client() as web:
            assert web.get(f"http://127.0.0.1:{health}/sante/pret").status_code == 200
            assert web.head(f"http://127.0.0.1:{health}/sante/vie").status_code == 200
            assert web.get(f"http://127.0.0.1:{health}/").status_code == 404
            assert web.get(f"http://127.0.0.1:{ui}/sante/vie").status_code == 404
            assert web.get(f"http://127.0.0.1:{ui}/").status_code == 200
    finally:
        main.should_exit = True
        runner.join(10)
    assert not runner.is_alive()
    assert (
        probe_server is not None and probe_server.should_exit
    )  # arrêté avec l'interface


def test_serveurs_sans_mandataire_ni_banniere_sondes_sans_journal_d_acces():
    from cdg.adapters import journaux
    from cdg.adapters.web import server

    probes = server.Probes(sante.create_health_app(State().checks()), "0.0.0.0", 8081)
    main, health = server.servers(
        object(), "127.0.0.1", 8000, log_config=journaux.config("texte"), probes=probes
    )
    assert (main.config.host, main.config.port) == ("127.0.0.1", 8000)
    assert main.config.proxy_headers is False and main.config.server_header is False
    assert health is not None and (health.config.host, health.config.port) == (
        "0.0.0.0",
        8081,
    )
    assert health.config.access_log is False  # une sonde toutes les quelques secondes


# --- CLI -------------------------------------------------------------------------------------


@pytest.fixture
def served(monkeypatch):
    from cdg import cli

    seen = {}

    def serve(app, host, port, *, log_config, probes=None):
        seen.update(app=app, probes=probes)

    monkeypatch.setattr(cli.web_server, "serve", serve)
    return seen


def test_cli_sans_port_de_sonde_aucune_sonde(served):
    from cdg import cli

    assert cli.main(["web", "--demo"]) == 0
    assert served["probes"] is None


def test_cli_demo_sondes_pretes_sans_base_ni_modele(served):
    from cdg import cli

    assert (
        cli.main(["web", "--demo", "--port-sante", "8081", "--hote-sante", "0.0.0.0"])
        == 0
    )
    probes = served["probes"]
    assert (probes.host, probes.port) == ("0.0.0.0", 8081)
    web = TestClient(probes.app)
    assert web.get("/sante/demarrage").status_code == 200
    assert web.get("/sante/pret").status_code == 200


def test_cli_reel_pret_apres_chargement_du_modele_et_base_joignable(
    served, monkeypatch
):
    from cdg import cli

    loaded = threading.Event()
    monkeypatch.setattr(cli, "process_embedder", lambda config: loaded.wait(5))
    monkeypatch.setattr(cli.connexions, "ping", lambda source: True)
    monkeypatch.setattr(cli, "app_pool", lambda: "pool")
    assert cli.main(["web", "--port-sante", "8081"]) == 0
    web = TestClient(served["probes"].app)
    assert (
        web.get("/sante/demarrage").status_code == 503
    )  # modèle en cours de chargement
    loaded.set()
    for _ in range(100):
        if web.get("/sante/pret").status_code == 200:
            break
        threading.Event().wait(0.02)
    assert web.get("/sante/pret").status_code == 200


def test_embedder_du_processus_charge_une_seule_fois(monkeypatch, tmp_path):
    from conftest import PROCESS_EMBEDDER

    from cdg import cli
    from cdg.domain.config import load_config

    loads = []

    class Loaded:
        def __init__(self, config, cache_dir):
            loads.append(cache_dir)

    monkeypatch.setattr(cli.fastembed, "FastembedEmbedder", Loaded)
    monkeypatch.setattr(cli.settings, "embedding_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_EMBEDDERS", {})
    config = load_config()
    first = PROCESS_EMBEDDER(config)
    assert PROCESS_EMBEDDER(config) is first and loads == [tmp_path]
