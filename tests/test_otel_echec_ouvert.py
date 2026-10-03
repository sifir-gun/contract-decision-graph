"""Échec ouvert (point 6 de la spec, ADR 008) : une destination des traces injoignable,
muette ou débordée ne change ni le résultat d'une analyse ni, sensiblement, sa durée ; à
l'arrêt, la fermeture de la télémétrie rend la main en `delai_fermeture_s` au plus.

Durée de référence : médiane de trois analyses sans télémétrie, après une première
d'échauffement ; marge admise, 0,5 s. Export par le vrai SDK (OTLP en HTTP), vers un port
fermé ou vers un serveur qui accepte la connexion sans jamais répondre."""

import base64
import logging
import socket
import statistics
import threading
import time

import pytest
from doubles import CODE, CONTRACT_TEXT
from observation_helpers import ANALYSTE, service, tarifs_de_test
from opentelemetry.sdk.trace import SpanProcessor

from cdg.adapters.otel.telemetry import Destination, OtelTelemetry, build
from cdg.application.observation import NoTelemetry

MARGE_S = 0.5
CLES = ("pk-lf-echec-ouvert", "sk-lf-echec-ouvert")
EXPORTER = "opentelemetry.exporter"
# fils du SDK (lots) et de la fermeture, que `close()` abandonne au-delà de son délai
EXPORT_THREADS = ("OtelBatch", "telemetrie-fermeture")


@pytest.fixture(autouse=True)
def fils_d_export_termines():
    """Chaque test attend la fin des fils d'export qu'il a lancés : abandonnés par
    `close()`, ils journaliseraient dans la sortie capturée d'un test suivant."""
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + 10
    started = [
        t
        for t in set(threading.enumerate()) - before
        if t.name.startswith(EXPORT_THREADS)
    ]
    for thread in started:
        thread.join(max(deadline - time.monotonic(), 0))
    assert [t.name for t in started if t.is_alive()] == []


def config(**export):
    base = tarifs_de_test()
    return base.model_copy(update={"export": base.export.model_copy(update=export)})


def analysed(telemetry):
    """Résultat d'une analyse (statut, état, verdicts) et sa durée ; un service neuf à
    chaque fois, donc le même identifiant."""
    contract_id = "c-echec"
    svc, _ = service(telemetry)
    start = time.monotonic()
    svc.analyse(CONTRACT_TEXT, contract_id=contract_id, actor=ANALYSTE)
    elapsed = time.monotonic() - start
    dossier = svc.dossier(contract_id)
    return {key: dossier[key] for key in ("status", "etat", "verdicts")}, elapsed


@pytest.fixture(scope="module")
def reference():
    analysed(NoTelemetry())
    runs = [analysed(NoTelemetry()) for _ in range(3)]
    return runs[0][0], statistics.median(elapsed for _, elapsed in runs)


def traced(port: int, cfg):
    destination = Destination(f"http://127.0.0.1:{port}/api/public/otel", *CLES)
    return build(cfg, traces=destination, metrics=None, code=CODE)


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Mute:
    """Destination muette : accepte chaque connexion, ne lit ni ne répond jamais."""

    def __init__(self):
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.listener.settimeout(0.05)
        self.port = self.listener.getsockname()[1]
        self.connected = threading.Event()
        self.stopped = threading.Event()
        self.connections: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections.append(connection)
            self.connected.set()

    def close(self):
        self.stopped.set()
        for connection in self.connections:
            connection.close()
        self.listener.close()


@pytest.fixture
def mute():
    server = Mute()
    yield server
    server.close()


def exporter_logged(caplog, timeout: float = 5.0) -> list[logging.LogRecord]:
    """Attend qu'un export ait été tenté (l'exportateur journalise son échec)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = [r for r in caplog.records if r.name.startswith(EXPORTER)]
        if records:
            return records
        time.sleep(0.02)
    return []


def test_destination_injoignable_analyse_inchangee(reference, caplog):
    expected, median = reference
    cfg = config(delai_lot_ms=1, delai_export_s=0.5, delai_fermeture_s=0.5)
    telemetry = traced(closed_port(), cfg)
    with caplog.at_level(logging.WARNING, logger="opentelemetry"):
        try:
            result, elapsed = analysed(telemetry)
            assert exporter_logged(caplog), "aucun export tenté : test à vide"
        finally:
            telemetry.close()
    assert result == expected
    assert elapsed < median + MARGE_S


def test_echec_d_export_journalise_sans_les_cles(caplog):
    cfg = config(delai_lot_ms=1, delai_export_s=0.5, delai_fermeture_s=0.5)
    telemetry = traced(closed_port(), cfg)
    with caplog.at_level(logging.DEBUG, logger="opentelemetry"):
        try:
            analysed(telemetry)
            assert exporter_logged(caplog), "aucun export tenté : test à vide"
        finally:
            telemetry.close()
    logged = "\n".join(r.getMessage() for r in caplog.records)
    basic = base64.b64encode(":".join(CLES).encode()).decode()
    assert [s for s in (*CLES, basic) if s in logged] == []


def test_destination_muette_analyse_et_arret_bornes(reference, mute, caplog):
    expected, median = reference
    # l'export resterait suspendu 2 s ; la fermeture n'en attend que 0,3
    cfg = config(delai_lot_ms=1, delai_export_s=2.0, delai_fermeture_s=0.3)
    telemetry = traced(mute.port, cfg)
    first, before = analysed(telemetry)
    assert mute.connected.wait(5), "aucun export tenté : test à vide"
    # pendant que l'export est suspendu
    second, during = analysed(telemetry)
    start = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="cdg"):
        telemetry.close()
    closing = time.monotonic() - start
    assert first == second == expected
    assert max(before, during) < median + MARGE_S
    assert closing < 0.3 + MARGE_S
    assert "derniers envois abandonnés" in caplog.text


def test_file_pleine_spans_abandonnes_sans_blocage(reference, mute, caplog):
    expected, median = reference
    cfg = config(delai_lot_ms=1, delai_export_s=2.0, delai_fermeture_s=0.3, file_max=2)
    telemetry = traced(mute.port, cfg)
    with caplog.at_level(logging.WARNING, logger="opentelemetry"):
        try:
            analysed(telemetry)
            assert mute.connected.wait(5), "aucun export tenté : test à vide"
            # l'export est suspendu : la file de deux spans déborde
            result, elapsed = analysed(telemetry)
        finally:
            telemetry.close()
    assert result == expected
    assert elapsed < median + MARGE_S
    assert "Queue full" in caplog.text


def test_taille_des_lots_fixee_par_le_code(monkeypatch):
    """Le SDK lit la taille des lots dans `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` et refuse un
    lot plus grand que la file : le code la fixe, aucune variable `OTEL_*` n'y touche."""
    monkeypatch.setenv("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "100000")
    telemetry = traced(closed_port(), config(delai_fermeture_s=0.5))
    telemetry.close()


class _Failing(SpanProcessor):
    def shutdown(self):
        raise RuntimeError("message qui citerait une donnée")


def test_fermeture_en_echec_journalisee_par_type(caplog):
    telemetry = OtelTelemetry(
        tarifs_de_test(), span_processor=_Failing(), metric_reader=None, code=CODE
    )
    with caplog.at_level(logging.ERROR, logger="cdg"):
        telemetry.close()
    assert "RuntimeError" in caplog.text
    assert "citerait" not in caplog.text
