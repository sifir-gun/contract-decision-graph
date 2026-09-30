"""Jeton CSRF partagé entre réplicas (PR D1, ADR 005).

Un secret tiré par chaque processus faisait refuser (403) un formulaire servi par un
réplica et envoyé à l'autre. Les clés sont montées en fichiers et relues à chaque usage :
`courante` signe et vérifie, `precedente` ne fait que vérifier, le temps d'une rotation.
Le jeton porte son heure d'émission, sous la signature : un formulaire vit au plus
FORM_LIFETIME_SECONDS, le délai à attendre avant de retirer l'ancienne clé. Aucune clé
dans les journaux ni dans les messages d'erreur.
"""

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from web_helpers import CONTRACT_TEXT, memory_service

from cdg.adapters.web import security
from cdg.adapters.web.app import create_app

ANCIENNE = "ancienne-cle-" + "a" * 32
NOUVELLE = "nouvelle-cle-" + "b" * 32
TOKEN = re.compile(r'name="csrf" value="([0-9]+\.[0-9a-f]{64})"')
ORIGIN = {"origin": "http://127.0.0.1:8000"}


def keys(folder: Path, courante: str | None, precedente: str | None = None) -> Path:
    """Dossier des clés, comme le Secret monté : un fichier par clé présente."""
    folder.mkdir(exist_ok=True)
    for name, value in (("courante", courante), ("precedente", precedente)):
        path = folder / name
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(value + "\n", encoding="utf-8")
    return folder


def csrf(folder: Path, clock=None) -> security.Csrf:
    loader = lambda: security.read_csrf_keys(folder)
    return (
        security.Csrf(loader) if clock is None else security.Csrf(loader, clock=clock)
    )


# --- clés et rotation --------------------------------------------------------------------


def test_jeton_signe_par_la_cle_courante_accepte(tmp_path):
    guard = csrf(keys(tmp_path / "cles", ANCIENNE))
    cookie = guard.new_cookie()
    assert guard.valid(cookie, guard.token(cookie))
    assert not guard.valid(guard.new_cookie(), guard.token(cookie))  # autre cookie


def test_ancienne_cle_acceptee_pendant_la_transition_refusee_apres_son_retrait(
    tmp_path,
):
    """Clés relues à chaque usage : la rotation s'applique sans redémarrage."""
    folder = keys(tmp_path / "cles", ANCIENNE)
    guard = csrf(folder)
    cookie = guard.new_cookie()
    opened = guard.token(cookie)  # formulaire ouvert avant la rotation
    keys(folder, NOUVELLE, precedente=ANCIENNE)
    assert guard.valid(cookie, opened)  # transition : l'ancienne vérifie encore
    fresh = guard.token(cookie)  # signé par la nouvelle
    keys(folder, NOUVELLE)  # l'ancienne retirée
    assert not guard.valid(cookie, opened)
    assert guard.valid(cookie, fresh)


def test_rotation_sans_coupure_entre_replicas_mis_a_jour_a_des_instants_differents(
    tmp_path,
):
    """Le kubelet met à jour le fichier monté pod par pod. La nouvelle clé est d'abord
    annoncée (en précédente, vérification seulement), puis elle signe : un réplica en
    avance et un réplica en retard acceptent les formulaires l'un de l'autre. Sans
    l'annonce, le réplica en retard refuserait ceux du réplica en avance."""
    late = csrf(keys(tmp_path / "en-retard", ANCIENNE, precedente=NOUVELLE))  # annoncée
    ahead = csrf(keys(tmp_path / "en-avance", NOUVELLE, precedente=ANCIENNE))  # signe
    cookie = late.new_cookie()
    assert late.valid(cookie, ahead.token(cookie))
    assert ahead.valid(cookie, late.token(cookie))
    unannounced = csrf(keys(tmp_path / "sans-annonce", ANCIENNE))
    assert not unannounced.valid(cookie, ahead.token(cookie))


def test_formulaire_expire_apres_sa_duree_de_vie(tmp_path):
    now = [1_000_000.0]
    guard = csrf(keys(tmp_path / "cles", ANCIENNE), clock=lambda: now[0])
    cookie = guard.new_cookie()
    token = guard.token(cookie)
    now[0] += security.FORM_LIFETIME_SECONDS - 1
    assert guard.valid(cookie, token)
    now[0] += 2
    assert not guard.valid(cookie, token)


def test_jeton_venu_du_futur_ou_mal_forme_refuse(tmp_path):
    now = [1_000_000.0]
    guard = csrf(keys(tmp_path / "cles", ANCIENNE), clock=lambda: now[0])
    cookie = guard.new_cookie()
    now[0] += security.CLOCK_SKEW_SECONDS + 1
    future = guard.token(cookie)
    now[0] -= security.CLOCK_SKEW_SECONDS + 1
    assert not guard.valid(cookie, future)
    signature = guard.token(cookie).partition(".")[2]
    for token in ("", "abc", "12", "12.", f"x.{signature}", f"²{signature}"):
        assert not guard.valid(cookie, token), token
    assert not guard.valid(None, guard.token(cookie))


def test_precedente_facultative_vide_comme_absente(tmp_path):
    folder = keys(tmp_path / "cles", ANCIENNE)
    assert security.read_csrf_keys(folder).previous is None
    (folder / "precedente").write_text("\n", encoding="utf-8")
    assert security.read_csrf_keys(folder).previous is None


@pytest.mark.parametrize(
    ("courante", "precedente", "expected"),
    [
        (None, None, "courante"),
        ("zzzz-secret-zzzz", None, "32"),
        (ANCIENNE, "zzzz-secret-zzzz", "32"),
    ],
)
def test_cle_absente_ou_trop_courte_refusee_sans_la_montrer(
    tmp_path, courante, precedente, expected
):
    folder = keys(tmp_path / "cles", courante, precedente)
    with pytest.raises(security.CsrfKeyError) as error:
        security.read_csrf_keys(folder)
    message = str(error.value)
    assert expected in message
    assert "zzzz-secret-zzzz" not in message and ANCIENNE not in message


def test_cle_illisible_refusee_explicitement(tmp_path):
    folder = keys(tmp_path / "cles", ANCIENNE)
    (folder / "precedente").mkdir()  # illisible comme un fichier
    with pytest.raises(security.CsrfKeyError, match="illisible.*IsADirectoryError"):
        security.read_csrf_keys(folder)


def test_cles_jamais_dans_leur_representation(tmp_path):
    loaded = security.read_csrf_keys(keys(tmp_path / "cles", ANCIENNE, NOUVELLE))
    assert ANCIENNE not in repr(loaded) and NOUVELLE not in repr(loaded)


# --- deux réplicas -------------------------------------------------------------------------


def replica(folder: Path) -> TestClient:
    app = create_app(
        memory_service(), csrf_keys=lambda: security.read_csrf_keys(folder)
    )
    return TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False)


def submit(source: TestClient, target: TestClient):
    """Formulaire obtenu d'un réplica, envoyé à l'autre avec le même cookie."""
    page = source.get("/analyse")
    token = TOKEN.search(page.text)[1]
    cookie = source.cookies.get(security.Csrf.COOKIE)
    return target.post(
        "/analyse",
        data={"csrf": token, "source": "texte", "texte": CONTRACT_TEXT},
        headers={**ORIGIN, "cookie": f"{security.Csrf.COOKIE}={cookie}"},
    )


def test_formulaire_d_un_replica_accepte_par_l_autre(tmp_path):
    folder = keys(tmp_path / "cles", ANCIENNE)
    assert submit(replica(folder), replica(folder)).status_code == 303


def test_sans_cle_partagee_le_formulaire_d_un_replica_est_refuse_par_l_autre(tmp_path):
    first = replica(keys(tmp_path / "a", ANCIENNE))
    second = replica(keys(tmp_path / "b", NOUVELLE))
    assert submit(first, second).status_code == 403


def test_aucune_cle_dans_les_journaux(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    folder = keys(tmp_path / "cles", NOUVELLE, precedente=ANCIENNE)
    first, second = replica(folder), replica(keys(tmp_path / "autre", ANCIENNE))
    submit(first, replica(folder))
    submit(first, second)  # refusé
    assert NOUVELLE not in caplog.text and ANCIENNE not in caplog.text
