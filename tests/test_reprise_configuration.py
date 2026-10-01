"""Analyse interrompue reprise sous une autre configuration (défaut connu du 30/09 ; ADR 005).

- La reprise automatique ne reprend jamais une analyse sous une autre configuration que
  celle de son début : elle l'escalade vers la revue humaine, comptée avant, avec l'échec
  `ConfigurationModifiee` et le marqueur `configuration_changee` ; si le maximum de
  reprises est aussi atteint, le rapport d'échec cite les deux causes.
- Le relecteur peut trancher sous la configuration actuelle (`resume` ne l'accepte que
  dans ce cas) : le scellement porte l'empreinte de la configuration d'analyse (archivée)
  et celle du processus qui scelle, avec le constat « configuration changée pendant
  l'analyse ». Quatre yeux et rôles comme d'habitude ; le rejeu fidèle part de la
  configuration d'analyse archivée.
- Politique de revue : celle du processus qui scelle, sauf la levée d'un blocage dur,
  permise seulement si la configuration d'analyse et l'actuelle l'autorisent toutes deux.
- Relance : nouvelle analyse du texte masqué, sous la configuration actuelle, nouveau
  contrat ; le contrat escaladé reste en attente.
- `config-check` ne compte pas ces contrats comme à trancher : il les liste à part.
- Rejeu d'une escalade écrite par la reprise : le gate n'a pas tourné, il n'est pas
  recalculé ; la cause enregistrée (rang de reprise, maximum, empreintes) est vérifiée,
  cohérente avec le rapport et la décision. Sans cause valide : anomalie.
"""

import json
import logging
from contextlib import contextmanager

import pytest
from doubles import (
    ACTEUR_ANALYSTE,
    ACTEUR_RELECTEUR,
    CONTRACT_TEXT,
    FakeCrag,
    FixedExtractor,
    answer,
    clauses,
)
from test_arret import Death, DyingOnce
from test_policy import BLOCKED, human
from test_reprises import AlwaysDying, CrashingAnalysts
from test_service import make_service

from cdg import cli
from cdg.adapters.demo.audit_store import MemoryAuditStore
from cdg.adapters.demo.locks import LocalContractLocks
from cdg.adapters.demo.resumes import LocalResumeCounter
from cdg.adapters.langgraph import orchestrator
from cdg.adapters.langgraph.checkpointer import strict_serializer
from cdg.adapters.langgraph.engine import LockedMemorySaver
from cdg.application.service import FourEyesRefused
from cdg.domain import audit, policy, resumption
from cdg.domain.config import load_config
from cdg.ports.engine import ThreadError

CONFIG = load_config()
LIMIT = CONFIG.interrupted.max_resumes
NEW = CONFIG.model_copy(update={"min_margin": 0.06})  # la configuration actuelle
A, B = audit.config_hash(CONFIG), audit.config_hash(NEW)


def with_override(config, allowed: bool, *, review: bool = False):
    rules = config.human_policy.model_copy(
        update={"allow_block_override": allowed, "hard_block_review": review}
    )
    return config.model_copy(update={"human_policy": rules})


# --- domaine : causes de l'escalade ----------------------------------------------------------


def test_meme_configuration_rien_a_escalader_avant_le_maximum():
    assert resumption.escalation_failures(["analyst"], 1, LIMIT, A, A) == []


def test_configuration_modifiee_escalade_avec_les_deux_empreintes():
    [failure] = resumption.escalation_failures(["analyst"], 1, LIMIT, A, B)
    assert failure.error == resumption.CONFIG_CHANGED == "ConfigurationModifiee"
    assert A in failure.message and B in failure.message
    assert "revue humaine" in failure.message and failure.attempts == 1


def test_empreinte_d_analyse_absente_traitee_comme_modifiee():
    [failure] = resumption.escalation_failures(["analyst"], 1, LIMIT, None, B)
    assert failure.error == resumption.CONFIG_CHANGED and "inconnue" in failure.message


def test_maximum_atteint_et_configuration_modifiee_les_deux_causes():
    """Aucune cause n'efface l'autre (décision du propriétaire, 01/10)."""
    failures = resumption.escalation_failures(["analyst"], LIMIT + 1, LIMIT, A, B)
    assert [f.error for f in failures] == [
        resumption.CONFIG_CHANGED,
        resumption.INTERRUPTED,
    ]


def test_marqueur_des_deux_configurations():
    assert resumption.config_change(A, A) is None
    assert resumption.config_change(A, B) == {"analyse": A, "reprise": B}


# --- domaine : levée d'un blocage dur après un changement de configuration -------------------


@pytest.mark.parametrize(
    ("analysis", "current", "refused"),
    [
        (False, True, "configuration d'analyse"),  # seule la nouvelle la permet
        (True, False, "interdite par la configuration"),  # seule l'ancienne la permet
        (True, True, None),  # les deux la permettent
    ],
)
def test_levee_permise_seulement_si_les_deux_configurations_l_autorisent(
    analysis, current, refused
):
    state = {
        "configuration_changee": {"analyse": A, "reprise": B},
        "analysis_config": with_override(CONFIG, analysis).model_dump(mode="json"),
    }
    config = with_override(NEW, current)
    error = policy.check(
        human("GO", overrides_block=True),
        BLOCKED,
        config,
        ACTEUR_ANALYSTE,
        analysis_allows_override=policy.analysis_allows_override(state, config),
    )
    if refused is None:
        assert error is None
    else:
        assert refused in error


def test_sans_changement_la_configuration_courante_seule_decide():
    for allowed in (True, False):
        config = with_override(CONFIG, allowed)
        assert policy.analysis_allows_override({}, config) is allowed


def test_changement_sans_configuration_d_analyse_connue_levee_refusee():
    state = {"configuration_changee": {"analyse": A, "reprise": B}}
    assert policy.analysis_allows_override(state, NEW) is False


def test_demande_au_relecteur_porte_le_marqueur():
    state = {"configuration_changee": {"analyse": A, "reprise": B}}
    request = policy.build_request(state, NEW)
    assert request["configuration_changee"] == {"analyse": A, "reprise": B}


def test_scellement_constat_de_configuration_changee_pendant_l_analyse():
    from test_audit import analysed, record

    state = analysed() | {"configuration_changee": {"analyse": A, "reprise": B}}
    data = record(state, config=NEW).model_dump(mode="json")
    assert data["decision"]["config_hash"] == A and data["sealing_config_hash"] == B
    assert data["sealing_findings"] == [
        audit.CONFIG_CHANGED,
        audit.CONFIG_CHANGED_DURING_ANALYSIS,
    ]
    assert audit.CONFIG_CHANGED_DURING_ANALYSIS == (
        "configuration changée pendant l'analyse"
    )


# --- deux processus sur le même checkpointer : l'ancienne et la nouvelle configuration -------


class Processes:
    """Deux générations de processus sur le même checkpointer, le même journal et le
    même compteur (la base, en réel) ; chacune construit son graphe sous sa
    configuration."""

    def __init__(self, extractor=None, crag=None):
        self.saver = LockedMemorySaver(serde=strict_serializer())
        self.store, self.counter = MemoryAuditStore(), LocalResumeCounter()
        self.locks = LocalContractLocks()
        self.extractor = extractor or FixedExtractor(clauses())
        self.crag = crag or FakeCrag()

    def opener(self, config):
        @contextmanager
        def open_graph(deps):
            yield orchestrator.build_graph(config, deps).compile(
                checkpointer=self.saver
            )

        return open_graph

    def service(self, config):
        return make_service(
            self.extractor,
            store=self.store,
            config=config,
            opener=self.opener(config),
            locks=self.locks,
            resumes=self.counter,
            crag=self.crag,
        )


def interrupted(processes, contract_id="c-coupe", config=CONFIG):
    with pytest.raises(Death):
        processes.service(config).analyse(
            CONTRACT_TEXT, contract_id=contract_id, actor=ACTEUR_ANALYSTE
        )


def test_reprise_sous_une_autre_configuration_escaladee_jamais_reprise(caplog):
    extractor = DyingOnce()
    processes = Processes(extractor)
    interrupted(processes)
    with caplog.at_level(logging.WARNING, logger="cdg"):
        [escalated] = processes.service(NEW).resume_interrupted()
    assert extractor.calls == 1  # aucun nœud refait sous la nouvelle configuration
    assert (escalated["statut"], escalated["proposed_decision"]) == (
        "suspendu",
        "ESCALADE",
    )
    assert escalated["configuration_changee"] == {"analyse": A, "reprise": B}
    [failure] = escalated["failure_report"]["failures"]
    assert failure["error"] == "ConfigurationModifiee"
    assert processes.counter.record("c-coupe") == 2  # comptée une fois, avant
    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "c-coupe" in warning.getMessage() and "configuration" in warning.getMessage()
    # en attente d'un humain : plus jamais reprise
    assert processes.service(NEW).resume_interrupted() == []


def test_meme_configuration_reprise_normale():
    processes = Processes(DyingOnce())
    interrupted(processes)
    [resumed] = processes.service(CONFIG).resume_interrupted()
    assert resumed["statut"] == "termine" and resumed["configuration_changee"] is None


def test_maximum_atteint_puis_configuration_modifiee_deux_causes_au_rapport():
    extractor = AlwaysDying()
    processes = Processes(extractor)
    interrupted(processes)
    for _ in range(LIMIT):
        with pytest.raises(Death):
            processes.service(CONFIG).resume_interrupted()
    [escalated] = processes.service(NEW).resume_interrupted()
    errors = sorted(f["error"] for f in escalated["failure_report"]["failures"])
    assert errors == ["AnalyseInterrompue", "ConfigurationModifiee"]
    assert extractor.calls == 1 + LIMIT


def test_verdicts_rendus_sous_l_ancienne_configuration_gardes():
    crag = CrashingAnalysts()
    processes = Processes(crag=crag)
    interrupted(processes, "c-analystes")
    [escalated] = processes.service(NEW).resume_interrupted()
    assert sorted(v["domain"] for v in escalated["verdicts"]) == [
        "financier",
        "juridique",
    ]
    assert crag.calls.count("conformite") == 1  # pas refait sous la nouvelle


def escalated_contract(extractor=None, crag=None, config=CONFIG, current=NEW):
    processes = Processes(extractor or DyingOnce(), crag)
    interrupted(processes, config=config)
    processes.service(current).resume_interrupted()
    return processes


# --- revue sous la configuration actuelle ------------------------------------------------------


def test_le_relecteur_tranche_sous_la_configuration_actuelle():
    processes = escalated_contract()
    service = processes.service(NEW)
    status = service.decide("c-coupe", answer("NO_GO", reason="revu après changement"))
    assert (status["statut"], status["final_decision"]) == ("termine", "NO_GO")
    assert status["sealing_findings"] == [
        audit.CONFIG_CHANGED,
        audit.CONFIG_CHANGED_DURING_ANALYSIS,
    ]
    [entry] = processes.store.entries()
    assert entry.config_hash == A and entry.record["sealing_config_hash"] == B
    assert A in processes.store.configurations()  # configuration d'analyse archivée
    assert service.verify().ok


def test_rejeu_fidele_sur_la_configuration_d_analyse_archivee():
    processes = escalated_contract()
    service = processes.service(NEW)
    service.decide("c-coupe", answer("NO_GO", reason="revu"))
    replayed = service.replay("c-coupe")
    assert (replayed["configuration"], replayed["sens"]) == ("archivee", "fidele")
    assert replayed["identique"]


def test_quatre_yeux_comme_d_habitude():
    processes = escalated_contract()
    with pytest.raises(FourEyesRefused):
        processes.service(NEW).decide(
            "c-coupe", answer("NO_GO", reason="revu", acteur=ACTEUR_ANALYSTE)
        )


def test_resume_refuse_toujours_un_contrat_non_escalade_sous_une_autre_configuration():
    from test_service import PENDING_TEXT

    processes = Processes()
    processes.service(CONFIG).analyse(
        PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE
    )
    with pytest.raises(ThreadError, match="configuration modifiée"):
        processes.service(NEW).decide("c-attente", answer("NO_GO", reason="revu"))


def test_levee_refusee_quand_seule_la_nouvelle_configuration_la_permet():
    """Câblage du nœud de revue : la configuration d'analyse l'interdisait."""
    crag = CrashingAnalysts()
    blocked = FixedExtractor(clauses(responsabilite_acheteur=None))  # blocage dur
    processes = Processes(blocked, crag)
    old = with_override(CONFIG, False, review=True)
    new = with_override(NEW, True, review=True)
    interrupted(processes, "c-bloque", config=old)
    processes.service(new).resume_interrupted()
    service = processes.service(new)
    assert service.dossier("c-bloque")["levee_possible"] is False
    status = service.decide(
        "c-bloque", answer("GO", reason="levée", overrides_block=True)
    )
    assert status["statut"] == "suspendu"  # redemandée
    assert "configuration d'analyse" in status["demande"]["error"]


# --- dossier, revue et interface ---------------------------------------------------------------


def test_marqueur_visible_dans_le_dossier_et_la_demande():
    service = escalated_contract().service(NEW)
    dossier = service.dossier("c-coupe")
    marker = {"analyse": A, "reprise": B}
    assert dossier["status"]["configuration_changee"] == marker
    assert dossier["status"]["demande"]["configuration_changee"] == marker


def test_formulaire_de_revue_avertit_et_propose_la_relance():
    from web_helpers import client

    service = escalated_contract().service(NEW)
    page = client(service).get("/contrats/c-coupe").text
    assert "Configuration changée pendant l'analyse" in page
    assert A in page and B in page
    assert 'action="/contrats/c-coupe/relance"' in page


def test_show_par_la_cli_montre_le_marqueur(monkeypatch, capsys):
    service = escalated_contract().service(NEW)
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    assert cli.main(["show", "c-coupe"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"]["configuration_changee"] == {"analyse": A, "reprise": B}


def relance_par_l_interface(service, **fields):
    from web_helpers import client, csrf

    web = client(service)
    token = csrf(web, "/contrats/c-coupe")
    return web.post("/contrats/c-coupe/relance", data={"csrf": token, **fields})


def test_relance_par_l_interface_redirige_vers_le_nouveau_contrat():
    service = escalated_contract().service(NEW)
    response = relance_par_l_interface(service, identifiant="c-web")
    assert (response.status_code, response.headers["location"]) == (
        303,
        "/contrats/c-web",
    )


def test_relance_par_l_interface_identifiant_invalide_400():
    service = escalated_contract().service(NEW)
    response = relance_par_l_interface(service, identifiant="../hors")
    assert response.status_code == 400


def test_relance_par_l_interface_sans_cle_d_api_503(monkeypatch):
    from cdg.settings import SettingsError

    service = escalated_contract().service(NEW)

    def missing(*args, **kwargs):
        raise SettingsError("MISTRAL_API_KEY absente")

    monkeypatch.setattr(type(service), "relaunch", missing)
    response = relance_par_l_interface(service)
    assert response.status_code == 503 and "MISTRAL_API_KEY" in response.text


# --- relance sous la configuration actuelle ----------------------------------------------------


def test_relance_nouveau_contrat_sous_la_configuration_actuelle():
    processes = escalated_contract()
    service = processes.service(NEW)
    old = service.engine.values("c-coupe")
    status = service.relaunch("c-coupe", actor=ACTEUR_RELECTEUR)
    assert status["thread_id"] == "c-coupe-relance"
    values = service.engine.values("c-coupe-relance")
    assert values["config_hash"] == B  # sous la configuration actuelle
    assert values["raw_text"] == old["raw_text"]  # le texte masqué conservé
    assert values["analysis_date"] == old["analysis_date"]
    assert values["relance_de"] == "c-coupe"
    assert values["analyse_par"] == ACTEUR_RELECTEUR  # quatre yeux pour la suite
    # le contrat escaladé reste en attente, avec le lien vers sa relance
    dossier = service.dossier("c-coupe")
    assert dossier["etat"] == "en_attente" and dossier["relances"] == [
        "c-coupe-relance"
    ]


def test_relance_sous_l_identifiant_choisi():
    service = escalated_contract().service(NEW)
    status = service.relaunch(
        "c-coupe", actor=ACTEUR_RELECTEUR, contract_id="c-nouveau"
    )
    assert status["thread_id"] == "c-nouveau"


def test_relance_refusee_hors_d_une_escalade_pour_changement_de_configuration():
    from test_service import PENDING_TEXT

    service = make_service()
    service.analyse(PENDING_TEXT, contract_id="c-attente", actor=ACTEUR_ANALYSTE)
    with pytest.raises(ThreadError, match="changement de configuration"):
        service.relaunch("c-attente", actor=ACTEUR_RELECTEUR)


def test_relance_refusee_une_fois_le_contrat_tranche():
    service = escalated_contract().service(NEW)
    service.decide("c-coupe", answer("NO_GO", reason="revu"))
    with pytest.raises(ThreadError, match="en attente"):
        service.relaunch("c-coupe", actor=ACTEUR_RELECTEUR)


def test_relance_par_la_cli(monkeypatch, capsys):
    service = escalated_contract().service(NEW)
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    argv = [
        "relaunch",
        "c-coupe",
        "--identifiant",
        "c-cli",
        "--operateur",
        "lot-relance",
    ]
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["thread_id"] == "c-cli"
    assert service.engine.values("c-cli")["analyse_par"].operateur == "lot-relance"


# --- config-check ------------------------------------------------------------------------------


def test_config_check_ne_compte_pas_les_escalades_pour_changement_de_configuration():
    service = escalated_contract().service(NEW)
    check = service.config_check()
    assert check["a_trancher"] == []
    assert [c["thread_id"] for c in check["escalades_configuration"]] == ["c-coupe"]


def test_config_check_par_la_cli_reussit_avec_une_escalade(monkeypatch, capsys):
    service = escalated_contract().service(NEW)
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    assert cli.main(["config-check"]) == 0


def test_administration_liste_les_escalades_pour_changement_de_configuration():
    from web_helpers import client

    service = escalated_contract().service(NEW)
    page = client(service).get("/administration").text
    assert "Escaladés par la reprise automatique" in page
    assert 'href="/contrats/c-coupe"' in page


# --- PostgreSQL : un vrai processus tué, repris sous une autre configuration ----------------


@pytest.mark.pg
def test_processus_tue_repris_sous_une_autre_configuration_escalade(
    pg, thread_id, journal
):
    from datetime import UTC, datetime, timedelta

    from doubles import make_deps
    from test_verrous import SPAWN, WAIT, Replica, sealed

    from cdg.adapters.postgres.audit_store import PostgresAuditStore
    from cdg.adapters.postgres.locks import PostgresContractLocks
    from cdg.adapters.postgres.resumes import PostgresResumeCounter

    results = SPAWN.Queue()  # gardée en vie : le réplica la reprend (mode spawn)
    replica = Replica(pg, journal, thread_id, results)  # sous la configuration A
    assert replica.started.wait(WAIT), "le réplica n'a pas atteint l'extraction"
    replica.process.kill()
    replica.process.join(WAIT)
    extractor = AlwaysDying()  # ne doit pas être appelé sous la nouvelle configuration
    deps = make_deps(
        extractor, FakeCrag(()), audit_store=PostgresAuditStore(pg.app, table=journal)
    )
    options = {
        "hold": PostgresContractLocks(lambda: pg.app).hold,
        "record": PostgresResumeCounter(lambda: pg.app).record,
        "limit": LIMIT,
        "config": NEW,
        "thread_ids": {thread_id},
    }
    with orchestrator.open_graph(NEW, deps, pg.app) as graph:
        deadline = datetime.now(UTC) + timedelta(seconds=10)
        resumed = orchestrator.resume_interrupted(graph, **options)
        while not resumed and datetime.now(UTC) < deadline:  # verrou vu relâché
            resumed = orchestrator.resume_interrupted(graph, **options)
    [escalated] = resumed
    assert (escalated["statut"], escalated["proposed_decision"]) == (
        "suspendu",
        "ESCALADE",
    )
    assert escalated["configuration_changee"] == {"analyse": A, "reprise": B}
    assert extractor.calls == 0 and sealed(pg, journal) == []


# --- rejeu d'une escalade écrite par la reprise : cause vérifiée, gate non recalculé --------

BLOCKING = clauses(responsabilite_acheteur=None)  # blocage dur, juridique


def sealed_escalation(*, changed: bool, tokens: int = 0, review: bool = True):
    """Contrat dont deux analystes meurent à chaque fois, après que le juridique (blocage
    dur) et le financier ont rendu leur verdict ; escaladé par la reprise, après le
    maximum de reprises (même configuration) ou pour changement de configuration, puis
    tranché NO_GO. Rend le service qui a tranché et l'enregistrement scellé."""
    processes = Processes(
        FixedExtractor(BLOCKING, tokens_in=tokens), CrashingAnalysts()
    )
    old = with_override(CONFIG, True, review=review)
    interrupted(processes, "c-escalade", config=old)
    if changed:
        current = with_override(NEW, True, review=review)
    else:
        current = old
        for _ in range(LIMIT):
            with pytest.raises(Death):
                processes.service(old).resume_interrupted()
    service = processes.service(current)
    [escalated] = service.resume_interrupted()
    assert ("juridique", True) in [
        (v["domain"], v["hard_block"]) for v in escalated["verdicts"]
    ]
    service.decide("c-escalade", answer("NO_GO", reason="revu"))
    [entry] = processes.store.entries()
    return service, entry.record, processes.store.configurations()[entry.config_hash]


@pytest.mark.parametrize("changed", [False, True], ids=["maximum", "configuration"])
def test_rejeu_fidele_d_une_escalade_de_reprise_avec_blocage_dur_garde(changed):
    """Avant la correction : le rejeu recalculait le gate, proposait NO_GO (le blocage
    établi suffit) au lieu de l'ESCALADE scellée, et concluait à tort à une anomalie."""
    service, _record, _ = sealed_escalation(changed=changed)
    replayed = service.replay("c-escalade")
    assert (replayed["sens"], replayed["identique"], replayed["anomalie"]) == (
        "fidele",
        True,
        False,
    )
    assert replayed["defaut"] is None and replayed["recalcule"]  # verdicts recalculés


@pytest.mark.parametrize("changed", [False, True], ids=["maximum", "configuration"])
def test_rejeu_d_une_escalade_de_reprise_avec_budget_depasse(changed):
    service, record, _ = sealed_escalation(changed=changed, tokens=70_000)
    assert (
        sum(u["tokens_in"] for u in record["usage"])
        > CONFIG.budget.max_tokens_per_contract
    )
    replayed = service.replay("c-escalade")
    assert replayed["identique"] and not replayed["anomalie"]


@pytest.mark.parametrize(
    ("changed", "reprises"),
    [(False, LIMIT + 1), (True, 1)],
    ids=["maximum", "configuration"],
)
def test_escalade_de_reprise_scellee_avec_sa_cause(changed, reprises):
    _, record, _ = sealed_escalation(changed=changed)
    old = audit.config_hash(with_override(CONFIG, True, review=True))
    new = audit.config_hash(with_override(NEW, True, review=True))
    assert record["resume_escalation"] == {
        "reprises": reprises,
        "maximum": LIMIT,
        "analyse": old,
        "reprise": new if changed else old,
    }
    assert record["decision"]["config_hash"] == old


def tampered(record: dict, **changes) -> dict:
    data = json.loads(json.dumps(record))
    if changes.pop("remove", False):
        data["resume_escalation"] = None
    data["resume_escalation"] = (
        (data["resume_escalation"] or {}) | changes
        if changes
        else data["resume_escalation"]
    )
    return data


@pytest.mark.parametrize(
    ("changed", "changes", "reason"),
    [
        (False, {"remove": True}, "sans cause enregistrée"),
        (False, {"reprises": LIMIT}, "sans cause valide"),
        (
            False,
            {"maximum": LIMIT + 5, "reprises": LIMIT + 6},
            "maximum de reprises enregistré",
        ),
        (True, {"analyse": "e" * 64}, "configuration d'analyse enregistrée"),
        (True, {"reprise": None}, "mal formée"),
        (False, {"reprises": LIMIT + 2}, "rang de reprise"),
    ],
    ids=[
        "absente",
        "maximum-non-atteint",
        "autre-maximum",
        "autre-analyse",
        "mal-formee",
        "autre-rang",
    ],
)
def test_escalade_de_reprise_sans_cause_valide_reste_une_anomalie(
    changed, changes, reason
):
    _, record, configuration = sealed_escalation(changed=changed)
    report = audit.replay(tampered(record, **changes), configuration)
    assert not report.identical and reason in report.fault


def test_causes_du_rapport_incoherentes_avec_la_reprise_enregistree():
    """Le rapport cite un changement de configuration, la cause enregistrée non."""
    _, record, configuration = sealed_escalation(changed=False)
    data = json.loads(json.dumps(record))
    data["decision"]["failure_report"]["failures"][-1]["error"] = (
        "ConfigurationModifiee"
    )
    report = audit.replay(data, configuration)
    assert not report.identical and "incohérentes" in report.fault


def test_anomalie_de_cause_signalee_par_la_cli(monkeypatch, capsys):
    service, _record, _ = sealed_escalation(changed=False)
    processes_store = service.audit_store()
    processes_store.stored[0].record["resume_escalation"] = None  # journal altéré
    monkeypatch.setattr(cli, "build_service", lambda config: service)
    assert cli.main(["replay", "c-escalade"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["erreur"] == "RejeuAnomalie" and "sans cause" in error["defaut"]
