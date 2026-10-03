"""Environnement : .env chargé sans écraser les variables exportées, chaînes de connexion,
secrets lus dans un fichier monté, sinon dans une variable d'environnement."""

import logging
import os

import pytest
from psycopg.conninfo import conninfo_to_dict

from cdg import cli, settings
from cdg.adapters.postgres import conninfo

VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_DB_PASSWORD",
    "LANGSMITH_TRACING",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _env_file(tmp_path, **values):
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


FULL = {
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "cdg",
    "POSTGRES_USER": "cdg_admin",
    "POSTGRES_PASSWORD": "admin secret",
    "APP_DB_PASSWORD": "app'secret",
    "LANGSMITH_TRACING": "false",
}


def test_env_charge_le_fichier(clean_env, tmp_path):
    assert settings.load_env(_env_file(tmp_path, **FULL))
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_variable_exportee_prioritaire_sur_le_fichier(clean_env, tmp_path):
    clean_env.setenv("POSTGRES_DB", "exportee")
    settings.load_env(_env_file(tmp_path, **FULL))
    assert os.environ["POSTGRES_DB"] == "exportee"


def test_chaines_de_connexion_admin_et_app_role(clean_env, tmp_path):
    settings.load_env(_env_file(tmp_path, **FULL))
    admin = conninfo_to_dict(conninfo.admin_conninfo())
    app = conninfo_to_dict(conninfo.app_conninfo())
    assert (admin["user"], admin["password"], admin["host"]) == (
        "cdg_admin",
        "admin secret",
        "localhost",
    )
    assert (app["user"], app["password"], app["dbname"], app["port"]) == (
        "app_role",
        "app'secret",
        "cdg",
        "5432",
    )


def test_negociation_kerberos_desactivee_dans_toute_chaine_de_connexion(
    clean_env, tmp_path
):
    """Le projet n'utilise pas Kerberos : libpq ne tente jamais de chiffrement GSSAPI
    (gssencmode=disable). Le chemin de code de la copie d'OpenSSL 1.1.1k que la roue
    arm64 de psycopg-binary embarque pour Kerberos n'est ainsi jamais pris (journal,
    01/10/2026)."""
    settings.load_env(_env_file(tmp_path, **FULL))
    for chaine in (conninfo.admin_conninfo(), conninfo.app_conninfo()):
        assert conninfo_to_dict(chaine)["gssencmode"] == "disable"


@pytest.mark.parametrize(
    "missing", ["POSTGRES_PORT", "POSTGRES_PASSWORD", "APP_DB_PASSWORD"]
)
def test_variable_manquante_leve_une_erreur_explicite(clean_env, tmp_path, missing):
    settings.load_env(
        _env_file(tmp_path, **{k: v for k, v in FULL.items() if k != missing})
    )
    with pytest.raises(settings.SettingsError, match=missing):
        conninfo.admin_conninfo() if missing != "APP_DB_PASSWORD" else conninfo.app_conninfo()


# --- secrets : fichier monté en lecture seule, sinon variable d'environnement ------------------

CANARY = "canari-9f3b-valeur-secrete"


@pytest.fixture
def secrets_dir(tmp_path, monkeypatch):
    folder = tmp_path / "secrets"
    folder.mkdir()
    monkeypatch.setattr(settings, "SECRETS_DIR", folder)
    return folder


def test_secrets_du_chart_et_de_l_application_memes_noms():
    from conftest import SECRETS_DIR

    assert settings.SECRETS == (
        "MISTRAL_API_KEY",
        "ANTHROPIC_API_KEY",
        "APP_DB_PASSWORD",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        # destination des traces (ADR 008)
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
    )
    assert str(SECRETS_DIR) == "/run/secrets/cdg"


def test_fichier_prioritaire_sur_la_variable(secrets_dir, monkeypatch):
    (secrets_dir / "APP_DB_PASSWORD").write_text("du fichier", encoding="utf-8")
    monkeypatch.setenv("APP_DB_PASSWORD", "de la variable")
    assert settings.secret("APP_DB_PASSWORD") == "du fichier"


def test_sans_fichier_la_variable_d_environnement(secrets_dir, monkeypatch):
    monkeypatch.setenv("APP_DB_PASSWORD", "de la variable")
    assert settings.secret("APP_DB_PASSWORD") == "de la variable"


def test_ni_fichier_ni_variable(secrets_dir, monkeypatch):
    monkeypatch.delenv("APP_DB_PASSWORD", raising=False)
    assert settings.secret("APP_DB_PASSWORD") is None
    with pytest.raises(settings.SettingsError, match="APP_DB_PASSWORD") as raised:
        settings.require_secret("APP_DB_PASSWORD")
    assert str(secrets_dir / "APP_DB_PASSWORD") in str(raised.value)


def test_secret_inconnu_refuse(secrets_dir):
    with pytest.raises(ValueError, match="EMBEDDING_CACHE_DIR"):
        settings.secret("EMBEDDING_CACHE_DIR")


@pytest.mark.parametrize(
    "content", [b"mot de passe\n", b"  mot de passe \r\n", b"\tmot de passe\n\n"]
)
def test_espaces_et_fin_de_ligne_retires(secrets_dir, content):
    (secrets_dir / "POSTGRES_PASSWORD").write_bytes(content)
    assert settings.secret("POSTGRES_PASSWORD") == "mot de passe"


@pytest.mark.parametrize("case", ["vide", "blanc", "dossier", "non-utf8"])
def test_fichier_inexploitable_erreur_explicite_sans_valeur(
    secrets_dir, monkeypatch, caplog, case
):
    """Un fichier présent mais inexploitable est une erreur, jamais un repli sur la
    variable ; ni le message ni le journal ne reprennent une valeur."""
    monkeypatch.setenv("MISTRAL_API_KEY", CANARY)
    path = secrets_dir / "MISTRAL_API_KEY"
    if case == "dossier":
        path.mkdir()
    else:
        content = {"vide": b"", "blanc": b" \n", "non-utf8": CANARY.encode() + b"\xff"}
        path.write_bytes(content[case])
    caplog.set_level(logging.DEBUG)
    with pytest.raises(settings.SettingsError) as raised:
        settings.secret("MISTRAL_API_KEY")
    message = str(raised.value)
    assert str(path) in message
    assert CANARY not in message and CANARY not in caplog.text
    # aucune exception chaînée affichée : son message pourrait citer la valeur
    error = raised.value
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__


def test_cli_erreur_de_secret_sans_valeur(secrets_dir, monkeypatch, capsys):
    for name, value in {"POSTGRES_PORT": "5432", "POSTGRES_DB": "cdg"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_USER", CANARY)
    monkeypatch.setenv("POSTGRES_PASSWORD", CANARY)
    (secrets_dir / "POSTGRES_PASSWORD").write_bytes(b"\n")
    assert cli.main(["--journaux", "json", "setup-db"]) == 1
    out, err = capsys.readouterr()
    assert "POSTGRES_PASSWORD" in err and "SettingsError" in err
    assert CANARY not in out + err
