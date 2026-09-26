"""Environnement : .env chargé sans écraser les variables exportées, chaînes de connexion."""

import os

import pytest
from psycopg.conninfo import conninfo_to_dict

from cdg import settings
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


@pytest.mark.parametrize(
    "missing", ["POSTGRES_PORT", "POSTGRES_PASSWORD", "APP_DB_PASSWORD"]
)
def test_variable_manquante_leve_une_erreur_explicite(clean_env, tmp_path, missing):
    settings.load_env(
        _env_file(tmp_path, **{k: v for k, v in FULL.items() if k != missing})
    )
    with pytest.raises(settings.SettingsError, match=missing):
        conninfo.admin_conninfo() if missing != "APP_DB_PASSWORD" else conninfo.app_conninfo()
