"""Aides des tests d'observabilité : service des contrats en mémoire (vrai graphe,
extraction et explication réelles sur des LLM en doublure), observé par la télémétrie
donnée ; tarifs réels, plus celui du modèle « double » des doublures."""

from doubles import (
    ANALYSIS_DATE,
    CODE,
    FIXED_NOW,
    FakeCrag,
    FakeLLM,
    MemoryAuditStore,
    clauses,
    faithful_explanation,
    make_deps,
)

from cdg.adapters.demo.locks import LocalContractLocks
from cdg.adapters.demo.resumes import LocalResumeCounter
from cdg.adapters.langgraph.engine import EngineDeps, LangGraphEngine, memory_opener
from cdg.application.explanation import LLMExplainer
from cdg.application.extraction import LLMExtractor
from cdg.application.observation import (
    ObservabilityConfig,
    ObservedProvider,
    Tarif,
    load_observability_config,
)
from cdg.application.service import ContractService
from cdg.domain.authorization import Actor
from cdg.domain.config import load_config

CONFIG = load_config()
ANALYSTE = Actor(canal="cli", authentifie=False, operateur="lot-1")


def tarifs_de_test() -> ObservabilityConfig:
    """Tarifs réels, plus celui du modèle « double » des doublures."""
    config = load_observability_config()
    double = Tarif(
        entree_usd_par_mtoken=1.0,
        sortie_usd_par_mtoken=2.0,
        source="https://example.org/tarifs-de-test",
        releve_le=ANALYSIS_DATE,
    )
    return config.model_copy(update={"tarifs": {**config.tarifs, "double": double}})


def extraction(found) -> dict:
    return {"clauses": [c.model_dump() for c in found]}


def service(telemetry, *, found=None, crag=None, today=ANALYSIS_DATE):
    """Service en mémoire, extraction et explication réelles sur des LLM en doublure,
    observés ; renvoie aussi les doublures, pour lire ce qui leur a été envoyé."""
    extract_llm = FakeLLM(
        {"extract_clauses": extraction(found or clauses())}, tokens=(1200, 300)
    )
    explain_llm = FakeLLM({"explain": faithful_explanation}, tokens=(800, 200))
    store = MemoryAuditStore()
    deps = make_deps(
        LLMExtractor(ObservedProvider(extract_llm, telemetry)),
        crag or FakeCrag(),
        audit_store=store,
        explainer=LLMExplainer(ObservedProvider(explain_llm, telemetry)),
        telemetry=telemetry,
    )
    engine = LangGraphEngine(
        CONFIG,
        memory_opener(CONFIG),
        EngineDeps(
            run=lambda: deps,
            resume=lambda: deps,
            expire=lambda: deps,
            read=lambda: deps,
        ),
        LocalContractLocks(),
        LocalResumeCounter(),
    )
    built = ContractService(
        engine=engine,
        audit_store=lambda: store,
        config=CONFIG,
        today=lambda: today,
        now=lambda: FIXED_NOW,
        code_version=CODE,
        telemetry=telemetry,
    )
    return built, (extract_llm, explain_llm)
