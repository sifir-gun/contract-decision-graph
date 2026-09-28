"""CLI : sorties JSON, erreurs structurées, dépendances réelles remplacées par des doublures."""

from contextlib import contextmanager
from datetime import date

import psycopg
import pytest
from cli_helpers import INSUFFICIENT, run_cli
from doubles import (
    CONTRACT_TEXT,
    FakeLLM,
    FixedExtractor,
    HashEmbedder,
    clauses,
    faithful_explanation,
)

from cdg import cli
from cdg.application.explanation import LLMExplainer
from cdg.domain.config import load_config
from cdg.domain.models import REQUIRED_KINDS, Clause


@pytest.mark.pg
def test_setup_db(pg, capsys):
    code, out = run_cli(capsys, "setup-db")
    assert code == 0
    assert out == {
        "setup_db": "ok",
        "role": "app_role",
        "role_cree": False,  # base existante : rôle déjà là, rien ne change
        "tables": ["checkpoints", "checkpoint_blobs", "checkpoint_writes"],
        "droits": ["SELECT", "INSERT", "UPDATE"],
        "migrations": [
            "002_rag.sql",
            "003_rag_versions.sql",
            "004_audit_integrite.sql",
            "005_rag_clauses.sql",
            "006_reprises.sql",
        ],
        "corpus": {"table": "rag_chunks", "droits": ["SELECT"]},
        "journal": {"table": "audit_decisions", "droits": ["SELECT", "INSERT"]},
        "reprises": {
            "table": "contract_resumes",
            "droits": ["SELECT", "INSERT", "UPDATE"],
        },
    }


def test_commande_obligatoire(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


# --- run, resume, history ---------------------------------------------------------------


def test_aide_de_run(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    help_text = capsys.readouterr().out
    assert "--analysis-date" in help_text and "stub" not in help_text
    assert "payant" in help_text  # l'appel au fournisseur LLM est annoncé


@pytest.mark.pg
def test_run_insuffisant_suspend_en_escalade(pg, thread_id, contract, analysis, capsys):
    analysis(*INSUFFICIENT)
    code, out = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    assert code == 0 and "mode" not in out
    assert (out["thread_id"], out["statut"], out["proposed_decision"]) == (
        thread_id,
        "suspendu",
        "ESCALADE",
    )
    assert out["final_decision"] is None
    assert out["demande"]["proposed_decision"] == "ESCALADE"


@pytest.mark.pg
def test_run_favorable_termine_en_go(
    pg, thread_id, contract, analysis, audit_journal, capsys
):
    extractor, crag = analysis()
    code, out = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    assert (code, out["statut"], out["final_decision"]) == (0, "termine", "GO")
    # scellé dans le journal (jetable), empreintes exposées par la sortie
    [entry] = audit_journal.entries()
    assert (entry.thread_id, entry.record["decision"]["final_decision"]) == (
        thread_id,
        "GO",
    )
    assert (out["decision_hash"], out["chain_hash"]) == (
        entry.decision_hash,
        entry.chain_hash,
    )
    assert len(extractor.calls) == 1 and sorted(crag.calls) == sorted(
        ["juridique", "financier", "conformite", "operationnel"]
    )


@pytest.mark.pg
def test_run_blocage_dur_termine_en_no_go(pg, thread_id, contract, analysis, capsys):
    analysis(FixedExtractor(clauses(responsabilite_acheteur=None)))
    code, out = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    assert (code, out["statut"], out["final_decision"], out["demande"]) == (
        0,
        "termine",
        "NO_GO",
        None,
    )


@pytest.mark.pg
def test_run_masque_les_parties_declarees(pg, thread_id, tmp_path, analysis, capsys):
    extractor, _ = analysis()
    path = tmp_path / "c.txt"
    path.write_text(CONTRACT_TEXT + "Signé par Acme Industrie.\n", encoding="utf-8")
    code, out = run_cli(
        capsys,
        "run",
        str(path),
        "--contract-id",
        thread_id,
        "--party",
        "Acme Industrie",
    )
    assert code == 0 and out["masquage"] == {"PARTIE": 1}
    assert "Acme" not in extractor.calls[0][0]


@pytest.mark.pg
def test_run_date_d_analyse(pg, thread_id, contract, analysis, capsys):
    analysis()
    code, out = run_cli(
        capsys,
        "run",
        contract,
        "--contract-id",
        thread_id,
        "--analysis-date",
        "2027-02-01",
    )
    assert code == 0 and out["analysis_date"] == "2027-02-01"


@pytest.mark.pg
def test_run_date_d_analyse_par_defaut_aujourd_hui_a_paris(
    pg, thread_id, contract, analysis, capsys, monkeypatch
):
    analysis()
    monkeypatch.setattr(cli, "today", lambda: date(2026, 12, 31))
    code, out = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    assert code == 0 and out["analysis_date"] == "2026-12-31"
    assert cli.LEGAL_TIMEZONE.key == "Europe/Paris"


@pytest.mark.parametrize(
    ("name", "options"),
    [("mon contrat.txt", []), ("contrat.txt", ["--contract-id", "a/b"])],
)
def test_run_refuse_un_identifiant_invalide_avant_toute_analyse(
    tmp_path, capsys, name, options
):
    """Identifiant tiré du nom du fichier ou donné par `--contract-id` : même règle
    que l'interface, vérifiée avant toute connexion à la base ou au LLM."""
    path = tmp_path / name
    path.write_text(CONTRACT_TEXT, encoding="utf-8")
    code, out = run_cli(capsys, "run", str(path), *options)
    assert code == 1 and out["erreur"] == "ContractIdError"
    assert out["detail"].startswith("identifiant de contrat invalide")


def test_run_date_d_analyse_invalide(contract, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", contract, "--analysis-date", "31/12/2026"])
    assert exc.value.code == 2


@pytest.mark.pg
def test_resume_finalise_puis_history(
    pg, thread_id, contract, analysis, audit_journal, capsys
):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "relecteur-synth",
        "--reason",
        "référentiel insuffisant",
    )
    assert (code, out["statut"], out["final_decision"]) == (0, "termine", "NO_GO")
    assert out["human"]["reviewer"] == "relecteur-synth"
    # rien de scellé pendant la suspension, un enregistrement après la reprise
    [entry] = audit_journal.entries()
    assert entry.record["decision"]["human"]["reviewer"] == "relecteur-synth"

    code, out = run_cli(capsys, "history", thread_id)
    steps = out["checkpoints"]
    assert code == 0
    assert steps[0]["source"] == "input" and steps[-1]["next"] == []
    assert [s["step"] for s in steps] == sorted(
        s["step"] for s in steps
    )  # chronologique
    assert steps[-1]["final_decision"] == "NO_GO"


@pytest.mark.pg
def test_resume_sans_cle_n_appelle_ni_llm_ni_corpus(
    pg, thread_id, contract, analysis, capsys, monkeypatch
):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)

    def forbidden(config):
        raise AssertionError("resume ne doit pas construire les dépendances d'analyse")

    monkeypatch.setattr(cli, "build_deps", forbidden)
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "r",
        "--reason",
        "m",
    )
    assert (code, out["final_decision"]) == (0, "NO_GO")


@pytest.mark.pg
def test_resume_refuse_reste_suspendu_avec_le_motif(
    pg, thread_id, contract, analysis, capsys
):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "ESCALADE",
        "--reviewer",
        "relecteur-synth",
        "--reason",
        "je ne sais pas",
    )
    assert (code, out["statut"]) == (0, "suspendu")
    assert "ESCALADE" in out["demande"]["error"]
    # la reprise suivante doit rester possible après un refus
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "relecteur-synth",
        "--reason",
        "référentiel insuffisant",
    )
    assert (code, out["statut"], out["final_decision"]) == (0, "termine", "NO_GO")


@pytest.mark.pg
def test_resume_thread_inconnu(pg, thread_id, capsys):
    code, err = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "r",
        "--reason",
        "m",
    )
    assert code == 1 and "inconnu" in err["detail"]


@pytest.mark.pg
def test_resume_thread_termine_refuse(pg, thread_id, contract, analysis, capsys):
    analysis(FixedExtractor(clauses(responsabilite_acheteur=None)))
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, err = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "GO",
        "--reviewer",
        "r",
        "--reason",
        "m",
    )
    assert code == 1 and "attente" in err["detail"]


@pytest.mark.pg
def test_run_refuse_un_thread_existant(pg, thread_id, contract, analysis, capsys):
    analysis()
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, err = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    assert code == 1 and "existe déjà" in err["detail"]


def test_run_contrat_absent(tmp_path, analysis, capsys):
    analysis()
    code, err = run_cli(capsys, "run", str(tmp_path / "absent.txt"))
    assert code == 1 and err["erreur"] == "FileNotFoundError"


@pytest.mark.pg
def test_run_sans_cle_d_api_erreur_avant_tout_thread(
    pg, thread_id, contract, capsys, monkeypatch
):
    # variable exportée vide : .env ne la remplace pas (override=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    code, err = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    assert (
        code == 1
        and err["erreur"] == "SettingsError"
        and "MISTRAL_API_KEY" in err["detail"]
    )
    code, err = run_cli(capsys, "history", thread_id)  # aucun thread créé
    assert code == 1 and "inconnu" in err["detail"]


# --- Échec de nœud (J3 tâche 10) : garde, rapport d'échec, escalade vers l'humain ------


class InvalidExtractor(FixedExtractor):
    """Extraction qui rend une clause présente sans citation : ValidationError."""

    def __call__(self, raw_text, feedback):
        self.calls.append((raw_text, list(feedback)))
        Clause(kind=REQUIRED_KINDS[0], present=True, quote="", value=None)
        raise AssertionError("inatteignable")


@pytest.mark.pg
def test_echec_de_noeud_escalade_avec_rapport_puis_resume(
    pg, thread_id, contract, analysis, capsys
):
    analysis(InvalidExtractor([]))
    code, out = run_cli(capsys, "run", contract, "--contract-id", thread_id)
    assert code == 0 and out["statut"] == "suspendu"
    assert (out["proposed_decision"], out["route"]) == ("ESCALADE", "human_review")
    [failure] = out["failures"]
    assert (failure["node"], failure["error"]) == ("extract_clauses", "ValidationError")
    assert "citation" in failure["message"]
    assert out["failure_report"]["stage"] == "noeuds"

    # l'échec est dans l'historique, et l'humain tranche
    code, history = run_cli(capsys, "history", thread_id)
    assert code == 0 and history["checkpoints"][-1]["next"] == ["human_review"]
    code, out = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "r",
        "--reason",
        "m",
    )
    assert code == 0 and out["final_decision"] == "NO_GO"


# --- ingest -------------------------------------------------------------------------------


@pytest.mark.pg
def test_ingest_indexe_le_corpus_puis_rejouable(pg, capsys, monkeypatch, tmp_path):
    # environnement déclaré par le test, jamais lu dans le .env du poste : la commande exige
    # EMBEDDING_CACHE_DIR avant de construire l'embedder, même remplacé par une doublure
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path))
    embedder, cache_dirs, batches = HashEmbedder(), [], []

    def factory(config, cache_dir, *, threads=None, batch_size=None):
        cache_dirs.append(cache_dir)
        batches.append(batch_size)
        return embedder

    monkeypatch.setattr(cli.fastembed, "FastembedEmbedder", factory)
    try:
        code, first = run_cli(capsys, "--lot-embedding", "16", "ingest")
        assert code == 0 and first["model"] == "hash-test"
        assert cache_dirs == [tmp_path] and batches == [16]
        assert first["inserted"] == first["chunks"] > 0 and first["deleted"] == 0
        code, again = run_cli(capsys, "ingest")
        assert (again["inserted"], again["deleted"], again["unchanged"]) == (
            0,
            0,
            first["chunks"],
        )
    finally:
        with psycopg.connect(pg.admin) as conn:
            conn.execute(
                "DELETE FROM rag_chunks WHERE embedding_model = %s", (embedder.model,)
            )


@pytest.mark.pg
def test_resume_refuse_si_la_configuration_a_change(
    pg, thread_id, contract, analysis, audit_journal, capsys, monkeypatch
):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    changed = load_config().model_copy(update={"min_margin": 0.06})
    monkeypatch.setattr(cli, "load_config", lambda: changed)
    code, err = run_cli(
        capsys,
        "resume",
        thread_id,
        "--decision",
        "NO_GO",
        "--reviewer",
        "r",
        "--reason",
        "m",
    )
    assert (code, err["erreur"]) == (1, "ThreadError")
    assert "relancer l'analyse" in err["detail"].lower()
    assert "restaurer la configuration" in err["detail"]
    assert audit_journal.entries() == []  # rien de repris, rien de scellé


# --- Explication (J4) : LLM pour run, et pour resume si la clé est présente ; gabarit sinon,
# et toujours pour expire ----------------------------------------------------------------------

RESUME = ["--decision", "NO_GO", "--reviewer", "relecteur-synth", "--reason", "m"]


@pytest.mark.pg
def test_resume_sans_cle_explication_par_le_gabarit_scellee(
    pg, thread_id, contract, analysis, audit_journal, capsys
):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    code, out = run_cli(capsys, "resume", thread_id, *RESUME)
    assert code == 0
    explanation = out["explanation"]
    assert (explanation["source"], explanation["decision"]) == ("gabarit", "NO_GO")
    assert explanation["reasons"] == [
        "clé d'API absente (MISTRAL_API_KEY) : explication par le gabarit"
    ]
    [entry] = audit_journal.entries()
    assert entry.record["explanation"] == explanation


@pytest.mark.pg
def test_resume_avec_cle_explication_par_le_llm(
    pg, thread_id, contract, analysis, audit_journal, capsys, monkeypatch
):
    analysis(*INSUFFICIENT)
    run_cli(capsys, "run", contract, "--contract-id", thread_id)
    monkeypatch.setenv("MISTRAL_API_KEY", "cle-de-test")
    llm = FakeLLM({"explain": faithful_explanation})
    monkeypatch.setattr(cli, "build_provider", lambda config: llm)
    code, out = run_cli(capsys, "resume", thread_id, *RESUME)
    assert code == 0 and [c["node"] for c in llm.calls] == ["explain"]
    assert (out["explanation"]["source"], out["explanation"]["reasons"]) == ("llm", [])


def test_expire_explication_toujours_par_le_gabarit(capsys, monkeypatch):
    # clé présente : expire ne construit pourtant aucun fournisseur
    monkeypatch.setenv("MISTRAL_API_KEY", "cle-de-test")

    def forbidden(config):
        raise AssertionError("expire n'appelle jamais le LLM")

    monkeypatch.setattr(cli, "build_provider", forbidden)
    seen = []

    @contextmanager
    def graph(config, deps):
        seen.append(deps)
        yield "graphe"

    monkeypatch.setattr(cli, "_graph", graph)
    monkeypatch.setattr(
        cli.orchestrator, "expire_threads", lambda graph, older_than, now, *, hold: []
    )
    assert cli.main(["expire", "--older-than", "1d"]) == 0
    [deps] = seen
    assert deps.explainer == cli.EXPIRE_EXPLAINER
    assert "expire" in deps.explainer.reason


def test_run_explication_par_le_llm_de_l_analyse(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path))
    llm = FakeLLM()
    monkeypatch.setattr(cli, "build_provider", lambda config: llm)
    monkeypatch.setattr(
        cli.fastembed, "FastembedEmbedder", lambda config, cache_dir: HashEmbedder()
    )
    deps = cli.build_deps(load_config())
    assert isinstance(deps.explainer, LLMExplainer) and deps.explainer.provider is llm


def test_tests_sans_cle_d_api_par_defaut():
    # garde-fou de conftest : aucun test (hors llm) n'appelle le vrai fournisseur
    import os

    assert os.environ["MISTRAL_API_KEY"] == os.environ["ANTHROPIC_API_KEY"] == ""
