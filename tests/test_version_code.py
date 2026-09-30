"""Version du code scellée avec chaque enregistrement v2 (PR D2, ADR 005).

- Fournie, jamais devinée : le commit à la construction de l'image (argument de
  construction, variable CDG_COMMIT de l'image), l'empreinte de l'image au lancement,
  par le chart (CDG_EMPREINTE_IMAGE). La CLI les lit au démarrage : ni git, ni registre.
- Sans commit (poste de développement), le champ le dit : « inconnu ». Mal formée, le
  programme s'arrête. Dans le cluster, l'empreinte de l'image est exigée.
- Scellées toutes deux : la version de l'analyse (posée par run_contract, comme la
  configuration) et celle du processus qui scelle ; un constat si elles diffèrent.
"""

import html
import json
import subprocess
import uuid

import pytest
from doubles import ACTEUR_ANALYSTE, CODE, CONTRACT_TEXT, answer, make_deps
from pydantic import ValidationError
from web_helpers import PENDING_TEXT, client, csrf, memory_service
from web_helpers import analyse as web_analyse

from cdg import cli
from cdg.application import demo_set
from cdg.domain import audit
from cdg.domain.version import UNKNOWN, CodeVersion

COMMIT = "deadbeef" * 5
IMAGE = "sha256:" + "ab" * 32
AUTRE_CODE = CodeVersion(commit="0" * 40, image="sha256:" + "1" * 64)


# --- modèle ----------------------------------------------------------------------------------


def test_version_connue_commit_et_empreinte_de_l_image():
    version = CodeVersion(commit=COMMIT, image=IMAGE)
    assert version.model_dump(mode="json") == {"commit": COMMIT, "image": IMAGE}


def test_commit_inconnu_dit_explicitement():
    version = CodeVersion(commit=UNKNOWN, image=None)
    assert version.model_dump(mode="json") == {"commit": "inconnu", "image": None}


@pytest.mark.parametrize(
    "commit", ["", "0123456", COMMIT.upper(), COMMIT + "0", "main", "v1.0.0", None]
)
def test_commit_mal_forme_refuse(commit):
    with pytest.raises(ValidationError, match="commit"):
        CodeVersion(commit=commit, image=None)


@pytest.mark.parametrize(
    "image",
    [
        "",
        "cdg:verification",
        f"ghcr.io/sifir-gun/contract-decision-graph@{IMAGE}",
        IMAGE.upper(),
        "sha256:abc",
        "inconnu",
    ],
)
def test_empreinte_d_image_mal_formee_refusee(image):
    with pytest.raises(ValidationError, match="image"):
        CodeVersion(commit=COMMIT, image=image)


def test_champ_inconnu_refuse():
    with pytest.raises(ValidationError):
        CodeVersion(commit=COMMIT, image=None, branche="main")


# --- lue au lancement, jamais devinée ----------------------------------------------------------


@pytest.fixture
def hors_cluster(monkeypatch):
    for variable in ("KUBERNETES_SERVICE_HOST", cli.COMMIT_VAR, cli.IMAGE_VAR):
        monkeypatch.delenv(variable, raising=False)


def test_noms_des_variables():
    assert (cli.COMMIT_VAR, cli.IMAGE_VAR) == ("CDG_COMMIT", "CDG_EMPREINTE_IMAGE")


def test_sans_variable_commit_inconnu_jamais_devine(hors_cluster):
    """Le dépôt a un commit, mais il n'est pas lu : seule la valeur fournie compte."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert len(head) == 40
    assert cli.code_version() == CodeVersion(commit=UNKNOWN, image=None)


def test_version_fournie_au_lancement(hors_cluster, monkeypatch):
    monkeypatch.setenv(cli.COMMIT_VAR, COMMIT)
    monkeypatch.setenv(cli.IMAGE_VAR, IMAGE)
    assert cli.code_version() == CodeVersion(commit=COMMIT, image=IMAGE)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("CDG_COMMIT", ""),
        ("CDG_COMMIT", "e30ae27"),
        ("CDG_EMPREINTE_IMAGE", ""),
        ("CDG_EMPREINTE_IMAGE", "cdg:verification"),
    ],
)
def test_version_mal_formee_arrete_le_programme(
    hors_cluster, monkeypatch, variable, value
):
    """Jamais de repli sur « inconnu » : une valeur fournie mais fausse est une erreur."""
    monkeypatch.setenv(variable, value)
    with pytest.raises(cli.VersionError, match=variable):
        cli.code_version()


def test_dans_le_cluster_l_empreinte_de_l_image_est_exigee(hors_cluster, monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    monkeypatch.setenv(cli.COMMIT_VAR, COMMIT)
    with pytest.raises(cli.VersionError, match="CDG_EMPREINTE_IMAGE.*chart"):
        cli.code_version()
    monkeypatch.setenv(cli.IMAGE_VAR, IMAGE)
    assert cli.code_version() == CodeVersion(commit=COMMIT, image=IMAGE)


@pytest.mark.parametrize("commit", [None, UNKNOWN])
def test_dans_le_cluster_un_commit_inconnu_empeche_le_demarrage(
    hors_cluster, monkeypatch, commit
):
    """Absent de l'image ou « inconnu » (contexte de construction différent du commit) :
    dans le cluster, aucune décision ne se scelle sans le commit qui l'a prise."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    monkeypatch.setenv(cli.IMAGE_VAR, IMAGE)
    if commit is not None:
        monkeypatch.setenv(cli.COMMIT_VAR, commit)
    with pytest.raises(cli.VersionError, match="CDG_COMMIT.*inconnu.*construction"):
        cli.code_version()


def test_hors_du_cluster_un_commit_inconnu_reste_accepte(hors_cluster, monkeypatch):
    monkeypatch.setenv(cli.COMMIT_VAR, UNKNOWN)
    assert cli.code_version() == CodeVersion(commit=UNKNOWN, image=None)


def test_dans_le_cluster_commande_refusee_sans_version(
    hors_cluster, monkeypatch, capsys
):
    """Contrôlée au démarrage du service, avant toute connexion à la base ; l'erreur
    nomme tout ce qui manque."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.43.0.1")
    assert cli.main(["list"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["erreur"] == "VersionError"
    assert "CDG_EMPREINTE_IMAGE" in error["detail"] and "CDG_COMMIT" in error["detail"]


def test_demonstration_scelle_la_version_lue_au_lancement(hors_cluster, monkeypatch):
    monkeypatch.setenv(cli.COMMIT_VAR, COMMIT)
    service = cli.demo_service(cli.load_config())
    _, contracts = demo_set.load()
    go = contracts["demo-01-go-maintenance"]
    service.analyse(
        go.text(), contract_id=go.id, actor=ACTEUR_ANALYSTE, parties=go.parties
    )
    [entry] = service.audit_store().entries()
    assert entry.record["code_version"] == {"commit": COMMIT, "image": None}


# --- scellées par le graphe --------------------------------------------------------------------


def test_contrat_scelle_avec_la_version_de_son_analyse_et_de_son_scellement():
    service = memory_service()  # dépendances de test : version CODE
    service.analyse(CONTRACT_TEXT, contract_id="c-go", actor=ACTEUR_ANALYSTE)
    [entry] = service.audit_store().entries()
    expected = CODE.model_dump(mode="json")
    assert (
        entry.record["code_version"] == entry.record["sealing_code_version"] == expected
    )
    assert entry.record["sealing_findings"] == []


def test_mise_a_jour_entre_l_analyse_et_la_revue_constat_au_scellement():
    """Analysé par une version, tranché après une mise à jour : les deux sont scellées,
    avec le constat ; le rejeu saura que l'enregistrement couvre deux versions."""
    service = memory_service(run=lambda: make_deps(code_version=AUTRE_CODE))
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    service.decide("c-attente", answer())
    [entry] = service.audit_store().entries()
    assert entry.record["code_version"] == AUTRE_CODE.model_dump(mode="json")
    assert entry.record["sealing_code_version"] == CODE.model_dump(mode="json")
    assert entry.record["sealing_findings"] == [audit.CODE_CHANGED]


@pytest.mark.pg
def test_run_par_la_cli_scelle_la_version_lue_au_lancement(
    hors_cluster, monkeypatch, analysis, audit_journal, contract
):
    monkeypatch.setenv(cli.COMMIT_VAR, COMMIT)
    analysis()
    thread = f"version-{uuid.uuid4().hex[:8]}"
    argv = ["run", contract, "--contract-id", thread, "--operateur", "lot-version"]
    assert cli.main(argv) == 0
    [entry] = [e for e in audit_journal.entries() if e.thread_id == thread]
    expected = {"commit": COMMIT, "image": None}
    assert entry.record["code_version"] == expected
    assert entry.record["sealing_code_version"] == expected


# --- constat affiché au réviseur, au moment de la revue ----------------------------------------


def test_constat_de_code_modifie_dans_la_sortie_de_resume(
    hors_cluster, monkeypatch, capsys
):
    """Analysé par une version, tranché par la CLI sous une autre : la sortie de la
    commande montre le constat au réviseur, pas seulement l'enregistrement."""
    service = memory_service(run=lambda: make_deps(code_version=AUTRE_CODE))
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    argv = ["resume", "c-attente", "--decision", "NO_GO", "--reason", "revu"]
    assert cli.main([*argv, "--operateur", "relecteur-poste"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["statut"], out["sealing_findings"]) == ("termine", [audit.CODE_CHANGED])


def test_meme_code_aucun_constat_dans_la_sortie_de_resume(
    hors_cluster, monkeypatch, capsys
):
    service = memory_service()
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    argv = ["resume", "c-attente", "--decision", "NO_GO", "--reason", "revu"]
    assert cli.main([*argv, "--operateur", "relecteur-poste"]) == 0
    assert json.loads(capsys.readouterr().out)["sealing_findings"] == []


def test_constat_de_code_modifie_affiche_dans_le_dossier_de_l_interface():
    """Même constat, même moment, par l'autre porte : le dossier tranché l'affiche."""
    service = memory_service(run=lambda: make_deps(code_version=AUTRE_CODE))
    web = client(service)
    thread = web_analyse(web, PENDING_TEXT, identifiant="c-attente")
    token = csrf(web, f"/contrats/{thread}")
    decided = web.post(
        f"/contrats/{thread}/decision",
        data={"csrf": token, "decision": "NO_GO", "motif": "revu"},
    )
    assert decided.status_code == 303
    page = html.unescape(web.get(f"/contrats/{thread}").text)  # texte échappé
    assert f"Constats du scellement : {audit.CODE_CHANGED}." in page
