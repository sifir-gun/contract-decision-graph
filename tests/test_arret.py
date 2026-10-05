"""Arrêt propre et reprise (phase Kubernetes, point 2) : à l'ordre d'arrêt, le réplica
n'est plus prêt et refuse toute modification (503), laisse finir les requêtes en cours
dans un délai configuré ; une analyse interrompue reprend depuis son dernier checkpoint,
sans rien perdre ni sceller deux fois."""

import signal
import socket
import threading
import time

import httpx
import pytest
from doubles import ACTEUR_ANALYSTE, CONTRACT_TEXT, FixedExtractor, clauses
from fastapi.testclient import TestClient
from test_concurrence import SLOW_TEXT, BlockingExtractor
from test_service import make_service
from web_helpers import BASE_URL, csrf, memory_service

from cdg import cli
from cdg.adapters import journaux
from cdg.adapters.web import sante, server
from cdg.adapters.web.app import create_app

WAIT = 10


class Death(BaseException):
    """Mort du processus au milieu d'une analyse : rien ne l'intercepte, comme un
    SIGKILL ; le dernier checkpoint reste tel quel."""


class DyingOnce:
    """Extraction qui « meurt » au premier appel, puis extrait normalement."""

    def __init__(self):
        self.inner, self.calls = FixedExtractor(clauses()), 0

    def __call__(self, raw_text, feedback):
        self.calls += 1
        if self.calls == 1:
            raise Death()
        return self.inner(raw_text, feedback)


# --- reprise d'une analyse interrompue ------------------------------------------------------


def test_analyse_interrompue_reprise_depuis_son_checkpoint_scellee_une_fois():
    extractor = DyingOnce()
    service = make_service(extractor)
    with pytest.raises(Death):
        service.analyse(
            CONTRACT_TEXT, contract_id="c-interrompu", actor=ACTEUR_ANALYSTE
        )
    [row] = service.contracts()
    assert row["etat"] == "en_cours" and service.journal() == []
    [resumed] = service.resume_interrupted()
    assert (resumed["thread_id"], resumed["statut"]) == ("c-interrompu", "termine")
    assert extractor.calls == 2  # l'extraction interrompue est refaite, rien d'autre
    assert [e["thread_id"] for e in service.journal()] == ["c-interrompu"]
    assert (
        service.resume_interrupted() == []
    )  # rien à reprendre : aucun double scellement
    assert [e["thread_id"] for e in service.journal()] == ["c-interrompu"]


def test_reprise_laisse_un_contrat_en_cours_ailleurs():
    from cdg.adapters.demo.locks import LocalContractLocks

    locks = LocalContractLocks()
    service = make_service(DyingOnce(), locks=locks)
    with pytest.raises(Death):
        service.analyse(CONTRACT_TEXT, contract_id="c-ailleurs", actor=ACTEUR_ANALYSTE)
    with locks.hold("c-ailleurs"):  # son réplica le tient encore
        assert service.resume_interrupted() == []
    assert [s["thread_id"] for s in service.resume_interrupted()] == ["c-ailleurs"]


def test_reprise_ne_touche_ni_aux_contrats_finis_ni_a_ceux_en_attente():
    from test_service import PENDING_TEXT

    service = make_service()
    service.analyse(CONTRACT_TEXT, contract_id="c-fini", actor=ACTEUR_ANALYSTE)
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    assert service.resume_interrupted() == []


# --- ordre d'arrêt : plus prêt, plus de modification -----------------------------------------


def test_ordre_d_arret_rend_pas_pret():
    stopping = threading.Event()
    main, _ = server.servers(
        object(),
        "127.0.0.1",
        8000,
        log_config=journaux.config("texte"),
        on_exit=stopping.set,
        grace_seconds=30,
    )
    assert main.config.timeout_graceful_shutdown == 30
    main.handle_exit(signal.SIGTERM, None)
    assert stopping.is_set() and main.should_exit
    checks = sante.Checks(
        started=lambda: True, database=lambda: True, draining=stopping.is_set
    )
    response = TestClient(sante.create_health_app(checks, instance="pod")).get(
        "/sante/pret"
    )
    assert (
        response.status_code == 503 and "arret en cours" in response.json()["raisons"]
    )


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/analyse", {"source": "texte", "texte": CONTRACT_TEXT}),
        ("/contrats/c1/decision", {"decision": "NO_GO"}),
        ("/administration/expiration", {"heures": "24", "confirme": "oui"}),
    ],
)
def test_pendant_l_arret_toute_modification_refusee_en_503(path, data):
    stopping = threading.Event()
    web = TestClient(
        create_app(memory_service(), draining=stopping.is_set), base_url=BASE_URL
    )
    token = csrf(web)
    stopping.set()
    response = web.post(path, data={"csrf": token, **data})
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "Arrêt en cours" in response.text
    assert web.get("/").status_code == 200  # les lectures restent servies


# --- un vrai serveur : la requête en cours finit, le serveur s'arrête ---------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(app, *, grace: int):
    port = free_port()
    stopping = threading.Event()
    main, _ = server.servers(
        app,
        "127.0.0.1",
        port,
        log_config=journaux.config("texte"),
        on_exit=stopping.set,
        grace_seconds=grace,
    )
    thread = threading.Thread(target=server.run, args=(main, None), daemon=True)
    thread.start()
    for _ in range(200):
        if main.started:
            break
        time.sleep(0.02)
    return main, thread, f"http://127.0.0.1:{port}", stopping


def post_slow(base: str, token: str, cookies, results: list):
    with httpx.Client(base_url=base, cookies=cookies, timeout=WAIT) as web:
        data = {
            "csrf": token,
            "source": "texte",
            "texte": SLOW_TEXT,
            "identifiant": "c-lent",
        }
        results.append(web.post("/analyse", data=data, headers={"origin": base}))


def test_serveur_reel_laisse_finir_l_analyse_en_cours_puis_s_arrete():
    extractor = BlockingExtractor()
    service = make_service(extractor)
    main, thread, base, _ = start(create_app(service), grace=WAIT)
    with httpx.Client(base_url=base) as web:
        page = web.get("/analyse")
        token = page.text.split('name="csrf" value="')[1].split('"')[0]
        cookies = web.cookies
    results: list = []
    poster = threading.Thread(target=post_slow, args=(base, token, cookies, results))
    poster.start()
    assert extractor.started.wait(WAIT), "l'analyse n'a pas commencé"
    main.handle_exit(signal.SIGTERM, None)  # ordre d'arrêt pendant l'analyse
    time.sleep(0.3)
    with pytest.raises(httpx.ConnectError):  # plus aucune nouvelle connexion
        httpx.get(f"{base}/", timeout=2)
    extractor.release.set()
    poster.join(WAIT)
    thread.join(WAIT)
    assert results and results[0].status_code == 303  # l'analyse en cours a fini
    assert not thread.is_alive()
    assert [r["etat"] for r in service.contracts()] == ["termine"]


def test_serveur_reel_delai_depasse_s_arrete_quand_meme():
    extractor = BlockingExtractor()
    service = make_service(extractor)
    main, thread, base, _ = start(create_app(service), grace=1)
    with httpx.Client(base_url=base) as web:
        page = web.get("/analyse")
        token = page.text.split('name="csrf" value="')[1].split('"')[0]
        cookies = web.cookies
    poster = threading.Thread(target=post_slow, args=(base, token, cookies, []))
    poster.start()
    assert extractor.started.wait(WAIT)
    main.handle_exit(signal.SIGTERM, None)
    thread.join(WAIT)
    assert not thread.is_alive()  # arrêté au bout du délai, analyse encore en cours
    extractor.release.set()
    poster.join(WAIT)


# --- CLI : délai d'arrêt, reprise périodique ---------------------------------------------------


@pytest.fixture
def served(monkeypatch):
    seen = {}

    def serve(
        app, host, port, *, log_config, probes=None, on_exit, grace_seconds, on_started
    ):
        seen.update(app=app, probes=probes, on_exit=on_exit, grace=grace_seconds)

    monkeypatch.setattr(cli.web_server, "serve", serve)
    return seen


def test_cli_delai_d_arret_par_defaut_et_reglable(served):
    assert cli.main(["web", "--demo"]) == 0
    assert served["grace"] == cli.DEFAULT_GRACE_SECONDS
    assert cli.main(["web", "--demo", "--delai-arret", "45"]) == 0
    assert served["grace"] == 45


def test_cli_ordre_d_arret_rend_l_interface_non_prete(served):
    assert cli.main(["web", "--demo", "--port-sante", "8081"]) == 0
    probes = TestClient(served["probes"].app)
    assert probes.get("/sante/pret").status_code == 200
    served["on_exit"]()
    assert probes.get("/sante/pret").status_code == 503


def test_reprise_periodique_s_arrete_avec_le_serveur():
    calls = []
    stopping = threading.Event()

    class Service:
        def resume_interrupted(self):
            calls.append(time.monotonic())
            return []

    from conftest import RESUME_PERIODICALLY

    worker = threading.Thread(
        target=RESUME_PERIODICALLY, args=(Service(), 0.05, stopping), daemon=True
    )
    worker.start()
    time.sleep(0.3)
    stopping.set()
    worker.join(WAIT)
    assert not worker.is_alive() and len(calls) >= 3


# --- checkpoints écrits avant chaque étape suivante ---------------------------------------------


def test_chaque_appel_au_graphe_ecrit_ses_checkpoints_avant_l_etape_suivante():
    """LangGraph 1.2.12 écrit par défaut ses checkpoints en arrière-plan, pendant l'étape
    suivante (durability « async ») : un processus tué à ce moment perdait ou tronquait
    son dernier checkpoint, et l'analyse ne pouvait plus reprendre (vu le 28/09 dans
    tests/test_verrous.py). Chaque appel passe « sync »."""
    from contextlib import contextmanager

    from test_service import PENDING_TEXT, answer

    from cdg.adapters.langgraph.engine import memory_opener
    from cdg.domain.config import load_config

    seen = []
    base = memory_opener(load_config())

    class Spy:
        def __init__(self, graph):
            self._graph = graph

        def __getattr__(self, name):
            return getattr(self._graph, name)

        def invoke(self, *args, **kwargs):
            seen.append(kwargs.get("durability"))
            return self._graph.invoke(*args, **kwargs)

    @contextmanager
    def spying(deps):
        with base(deps) as graph:
            yield Spy(graph)

    service = make_service(DyingOnce(), opener=spying)
    with pytest.raises(Death):
        service.analyse(CONTRACT_TEXT, contract_id="c1", actor=ACTEUR_ANALYSTE)
    service.resume_interrupted()
    service.analyse(PENDING_TEXT, contract_id="c2", actor=ACTEUR_ANALYSTE)
    service.decide("c2", answer())
    assert seen and set(seen) == {"sync"}
