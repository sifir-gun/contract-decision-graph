"""Commande `web` de la CLI : lance l'interface sur 127.0.0.1 par défaut ; toute autre
adresse exige une option explicite et affiche un avertissement. Le serveur n'est pas
démarré : `server.serve` est remplacé, l'application et l'adresse sont relevées. En fin de
fichier, de vrais ports : l'interface ne s'annonce qu'une fois son port ouvert, et un port
déjà pris l'arrête avant tout message."""

import json
import socket
import threading
import time
from contextlib import contextmanager

import pytest
from web_helpers import memory_service

from cdg import cli
from cdg.adapters import journaux
from cdg.adapters.web import sante, server
from cdg.adapters.web.app import create_app


@pytest.fixture
def served(monkeypatch):
    seen = {}

    def serve(app, host, port, *, log_config, on_started, **_):
        seen.update(app=app, host=host, port=port, log_config=log_config)
        on_started()  # port ouvert : l'interface s'annonce

    monkeypatch.setattr(server, "serve", serve)
    return seen


def test_ecoute_locale_par_defaut(served, capsys):
    assert cli.main(["web"]) == 0
    assert (served["host"], served["port"]) == ("127.0.0.1", 8000)
    out, err = capsys.readouterr()
    assert json.loads(out) == {"web": "arrêtée"}
    assert "http://127.0.0.1:8000" in err and "AVERTISSEMENT" not in err


def test_hote_et_port_en_option(served, capsys):
    assert cli.main(["web", "--host", "::1", "--port", "8123"]) == 0
    assert (served["host"], served["port"]) == ("::1", 8123)
    assert "http://[::1]:8123" in capsys.readouterr().err


def test_ecoute_non_locale_refusee_sans_option(served, capsys):
    assert cli.main(["web", "--host", "0.0.0.0"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["erreur"] == "WebConfigError"
    assert "authentification" in error["detail"]
    assert served == {}


def test_ecoute_non_locale_avec_option_et_avertissement(served, capsys):
    argv = ["web", "--host", "0.0.0.0", "--ecoute-non-locale"]
    assert cli.main(argv) == 0
    assert served["host"] == "0.0.0.0"
    assert "AVERTISSEMENT" in capsys.readouterr().err


def identity_argv(tmp_path, *extra: str) -> list[str]:
    """Interface derrière oauth2-proxy (mode démonstration : ni base ni modèle)."""
    keys = tmp_path / "cles"
    keys.mkdir()
    (keys / "courante").write_text("k" * 40, encoding="utf-8")
    return [
        "web",
        "--demo",
        "--identite",
        "en-tetes",
        "--oidc-emetteur",
        "https://idp.example.org",
        "--oidc-audience",
        "cdg-interface",
        "--adresse-publique",
        "https://cdg.example.org",
        "--cles-csrf",
        str(keys),
        "--groupes-analyste",
        "cdg-analystes",
        "--groupes-relecteur",
        "cdg-relecteurs,cdg-direction",
        *extra,
    ]


def test_second_facteur_non_exige_avertissement_au_demarrage(served, capsys, tmp_path):
    assert cli.main(identity_argv(tmp_path)) == 0
    err = capsys.readouterr().err
    assert "AVERTISSEMENT" in err and "second facteur" in err


def test_second_facteur_exige_ni_avertissement_ni_trou(served, capsys, tmp_path):
    argv = identity_argv(tmp_path, "--second-facteur-amr", "mfa,otp")
    assert cli.main(argv) == 0
    assert "second facteur" not in capsys.readouterr().err


def test_groupes_d_un_role_vides_refuses_au_lancement(served, capsys, tmp_path):
    argv = identity_argv(tmp_path, "--groupes-relecteur", " , ")
    assert cli.main(argv) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["erreur"] == "WebConfigError"
    assert "--groupes-" in error["detail"] and "relecteur" in error["detail"]


def test_rien_n_est_annonce_tant_que_le_port_n_est_pas_ouvert(monkeypatch, capsys):
    """Ni adresse ni avertissement d'écoute avant l'ouverture du port : une relance sur
    un port déjà pris laissait croire que l'interface avait démarré (28/09)."""
    monkeypatch.setattr(server, "serve", lambda *args, **kwargs: None)
    assert cli.main(["web", "--host", "0.0.0.0", "--ecoute-non-locale"]) == 0
    err = capsys.readouterr().err
    assert "http://" not in err and "AVERTISSEMENT" not in err


def test_reprise_des_analyses_interrompues_une_fois_le_port_ouvert(monkeypatch):
    """Une relance ratée ne reprend, ni ne compte, aucune analyse interrompue."""
    resumed = threading.Event()
    monkeypatch.setattr(cli, "resume_periodically", lambda *args: resumed.set())
    before = {}

    def serve(*args, on_started, **kwargs):
        before["reprise"] = resumed.wait(0.3)
        on_started()

    monkeypatch.setattr(server, "serve", serve)
    assert cli.main(["web"]) == 0
    assert before == {"reprise": False}
    assert resumed.wait(5)


# --- de vrais ports ------------------------------------------------------------------------


@contextmanager
def occupied():
    """Port pris par une autre instance, à l'écoute sur 127.0.0.1."""
    with socket.create_server(("127.0.0.1", 0)) as sock:
        yield sock.getsockname()[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_port_occupe_erreur_claire_sans_aucun_message_de_demarrage(capsys):
    """uvicorn 0.54 écrit « Application startup complete » avant d'ouvrir son port, puis
    sort en code 3 : l'ancienne instance restait seule à servir, sans qu'on le voie."""
    with occupied() as port:
        assert cli.main(["web", "--demo", "--port", str(port)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    [line] = err.splitlines()  # l'erreur, rien d'autre
    error = json.loads(line)
    assert error["erreur"] == "PortBusy"
    assert error["detail"].startswith(f"port {port} déjà utilisé")
    assert "--port" in error["detail"]


def test_port_des_sondes_occupe_l_interface_ne_demarre_pas():
    ui = free_port()
    checks = sante.Checks(
        started=lambda: True, database=lambda: True, draining=lambda: False
    )
    failures: list[server.PortBusy] = []
    with occupied() as busy:
        app = sante.create_health_app(checks, instance="pod-de-test")
        probes = server.Probes(app, "127.0.0.1", busy)
        main, health = server.servers(
            create_app(memory_service()),
            "127.0.0.1",
            ui,
            log_config=journaux.config("texte"),
            probes=probes,
        )

        def launch():
            try:
                server.run(main, health)
            except server.PortBusy as exc:
                failures.append(exc)

        runner = threading.Thread(target=launch, daemon=True)
        runner.start()
        runner.join(5)
        main.should_exit = True  # démarrée malgré tout : arrêtée, le test échoue
        runner.join(10)
    assert not main.started
    [failure] = failures
    assert str(failure).startswith(f"port {busy} déjà utilisé")
    assert "--port-sante" in str(failure)
    with socket.create_server(("127.0.0.1", ui)):  # le port de l'interface est rendu
        pass


def test_annonce_une_fois_le_port_ouvert():
    port = free_port()
    seen: list[bool] = []

    def opened():
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            seen.append(main.started)  # le port accepte déjà les connexions

    main, _ = server.servers(
        create_app(memory_service()),
        "127.0.0.1",
        port,
        log_config=journaux.config("texte"),
        on_started=opened,
    )
    runner = threading.Thread(target=server.run, args=(main, None), daemon=True)
    runner.start()
    try:
        for _ in range(250):
            if seen:
                break
            time.sleep(0.02)
    finally:
        main.should_exit = True
        runner.join(10)
    assert seen == [True]


def test_une_socket_par_adresse_resolue_sur_le_meme_port():
    """Comme asyncio : `localhost` peut désigner 127.0.0.1 et ::1 ; chaque adresse a sa
    socket, sur le même port, et une socket IPv6 n'accepte que l'IPv6."""
    port = free_port()
    sockets = server.bind("localhost", port, option="--port")
    try:
        assert sockets
        assert {sock.getsockname()[1] for sock in sockets} == {port}
        for sock in sockets:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
            if sock.family == socket.AF_INET6:
                assert sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
    finally:
        server.close(sockets)


def test_adresse_absente_de_la_machine_erreur_explicite():
    # 192.0.2.1 : réservée à la documentation (RFC 5737), jamais portée par la machine
    with pytest.raises(OSError, match="aucune adresse de 192.0.2.1 disponible"):
        server.bind("192.0.2.1", free_port(), option="--port")
