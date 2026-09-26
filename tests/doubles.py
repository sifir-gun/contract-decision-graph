"""Fabriques de données synthétiques et doublures pour les tests."""

import json
from datetime import UTC, date, datetime

from cdg.application.deps import Deps, ExtractionResult, RetrievalResult, TemplateOnly
from cdg.domain import audit
from cdg.domain.audit import StoredAuditEntry
from cdg.domain.config import load_config
from cdg.domain.models import (
    DOMAINS,
    REQUIRED_KINDS,
    AgentVerdict,
    Clause,
    ClauseRetrieval,
    RetrievalTrace,
    Usage,
)
from cdg.ports.audit_store import AuditStoreError
from cdg.ports.retriever import Passage

# date d'analyse fixe des tests : avant la fin de validité de L441-10 (2027-01-01)
ANALYSIS_DATE = date(2026, 9, 25)

# Contrat synthétique favorable : aucune règle déclenchée.
FAVORABLE = {
    "responsabilite_acheteur": 100.0,  # plafonnée à 100 % du montant annuel
    "responsabilite_fournisseur": 150.0,  # plafond fournisseur au-dessus du minimum
    "revision_prix": 3.0,  # révision plafonnée à 3 %
    "penalites_execution": 10.0,  # pénalités du fournisseur plafonnées à 10 % du contrat
    "delai_paiement": 30.0,  # jours, à compter de la date de facture
    "duree_engagement": 24.0,  # mois
    "preavis_resiliation": 3.0,  # mois
    "donnees_personnelles": None,  # traitement présent
    "accord_traitement_donnees": None,  # accord présent
    "transfert_hors_ue": None,  # stipulation de localisation présente
}

ABSENT = object()  # marqueur : la clause ne figure pas dans le contrat

# Une pénalité par domaine, sans blocage : chaque domaine a une clause à justifier par le
# corpus (juridique 0,5 ; financier 0,6 ; conformité 0,7 ; opérationnel 0,7 ; GO_RESERVES).
PENALIZED = {
    "responsabilite_fournisseur": 50.0,  # plafond fournisseur sous le minimum
    "penalites_execution": ABSENT,  # pénalités d'exécution absentes
    "transfert_hors_ue": ABSENT,  # localisation des données non précisée
    "duree_engagement": 48.0,  # engagement au-delà de 36 mois
}

# Contrat synthétique en français, sans donnée réelle : contient la citation de chaque
# clause rendue par `clauses()` (« Article synthétique : <kind>. »).
CONTRACT_TEXT = (
    "CONTRAT DE PRESTATION DE SERVICES (document synthétique)\n\n"
    "Entre la société cliente, ci-après dénommée l'Acheteur, et la société prestataire, "
    "ci-après dénommée le Fournisseur, il est convenu ce qui suit.\n\n"
    + "".join(f"Article synthétique : {kind}.\n" for kind in REQUIRED_KINDS)
    + "\nLe présent contrat est soumis au droit français. Les parties s'engagent à exécuter "
    "leurs obligations de bonne foi et dans les délais convenus. Toute modification du "
    "présent contrat fera l'objet d'un avenant écrit signé par les deux parties.\n"
)


CATEGORIES = {
    "transfert_hors_ue": "sans_transfert",  # données hébergées dans l'UE
    "delai_paiement": "date_facture",
}


def clauses(categories: dict | None = None, **overrides) -> list[Clause]:
    """Les clauses attendues, favorables par défaut.

    `kind=valeur` remplace la valeur d'une clause présente (None compris) ;
    `kind=ABSENT` la rend absente ; `categories={kind: catégorie}` remplace une catégorie.
    """
    categories = CATEGORIES | (categories or {})
    unknown = set(overrides) - set(REQUIRED_KINDS)
    if unknown:
        raise TypeError(f"types de clauses inconnus : {sorted(unknown)}")
    result = []
    for kind in REQUIRED_KINDS:
        value = overrides.get(kind, FAVORABLE[kind])
        if value is ABSENT:
            result.append(Clause(kind=kind, present=False, quote="", value=None))
        else:
            result.append(
                Clause(
                    kind=kind,
                    present=True,
                    quote=f"Article synthétique : {kind}.",
                    value=value,
                    category=categories.get(kind),
                )
            )
    return result


def verdict(domain, score=1.0, hard_block=False, status="OK") -> AgentVerdict:
    return AgentVerdict(
        domain=domain,
        score=score,
        hard_block=hard_block,
        findings=[],
        evidence_ids=[],
        retrieval_status=status,
    )


def verdicts(**by_domain) -> list[AgentVerdict]:
    """Un verdict favorable par domaine ; `domaine=dict(...)` en surcharge un."""
    return [verdict(d, **by_domain.get(d, {})) for d in DOMAINS]


def usage(tokens_in=0, tokens_out=0, node="double") -> Usage:
    return Usage(
        node=node,
        model="double",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=0,
    )


class FixedExtractor:
    """Doublure de l'extracteur LLM : rend toujours les mêmes clauses."""

    def __init__(self, clauses: list[Clause], tokens_in=0, tokens_out=0):
        self.clauses, self.tokens = clauses, (tokens_in, tokens_out)
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, raw_text: str, feedback: list[str]) -> ExtractionResult:
        self.calls.append((raw_text, list(feedback)))
        return ExtractionResult(
            clauses=self.clauses, usage=[usage(*self.tokens, node="extract_clauses")]
        )


class FakeCrag:
    """Doublure du CRAG : une référence par clause reçue (celles qui portent un constat),
    aucune pour les domaines de `empty`, où le corpus ne justifie rien."""

    def __init__(self, empty=(), tokens_in=0, tokens_out=0):
        self.empty, self.tokens = set(empty), (tokens_in, tokens_out)
        self.calls: list[str] = []
        self.kinds: dict[str, list[str]] = {}  # clauses reçues, par domaine

    def __call__(
        self, domain, clauses: list[Clause], analysis_date: date
    ) -> RetrievalResult:
        self.calls.append(domain)
        self.kinds[domain] = [c.kind for c in clauses]
        retained = [] if domain in self.empty else [f"{domain}-ref-1"]
        trace = RetrievalTrace(
            clauses=[
                ClauseRetrieval(
                    kind=c.kind,
                    queries=[f"requête {c.kind}"],
                    passes=1,
                    retained=retained,
                    expired=[],
                )
                for c in clauses
            ]
        )
        return RetrievalResult(
            trace=trace, usage=[usage(*self.tokens, node=f"crag:{domain}")]
        )


class FakeLLM:
    """Doublure d'un fournisseur LLM : réponses scriptées par nœud, appels enregistrés."""

    name = "fake"

    def __init__(self, responses: dict | None = None, tokens=(0, 0)):
        # responses : nœud -> liste de réponses (consommées dans l'ordre) ou réponse fixe
        self.responses, self.tokens = responses or {}, tokens
        self.calls: list[dict] = []

    def structured(self, *, tier, system, user, schema, node):
        self.calls.append(
            {
                "tier": tier,
                "system": system,
                "user": user,
                "schema": schema,
                "node": node,
            }
        )
        # réponse du nœud exact, sinon du préfixe : « crag_grade:financier » vaut pour
        # « crag_grade:financier:revision_prix » et les autres clauses du domaine
        key = node
        while key not in self.responses and ":" in key:
            key = key.rsplit(":", 1)[0]
        scripted = self.responses[key]
        answer = scripted.pop(0) if isinstance(scripted, list) else scripted
        answer = answer(user) if callable(answer) else answer
        return schema.model_validate(answer), usage(*self.tokens, node=node)


class HashEmbedder:
    """Doublure d'embedding, déterministe : sac de mots haché, normalisé.

    Deux textes qui partagent des mots ont une similarité positive, sans modèle.
    """

    model = "hash-test"

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self.calls: list[str] = []

    def _vector(self, text: str) -> list[float]:
        import hashlib
        import math
        import re

        vector = [0.0] * self.dimension
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest, "big") % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        self.calls.extend(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        return self._vector(text)


def passage(
    reference: str, domain="financier", valid_until=None, text=None, id=1
) -> Passage:
    return Passage(
        id=id,
        domain=domain,
        source_id="test",
        reference=reference,
        text=text or f"Texte de {reference}.",
        distance=0.1,
        valid_until=valid_until,
    )


class FakeRetriever:
    """Doublure du port Retriever : extraits fixes par domaine, requêtes enregistrées."""

    def __init__(self, passages: dict | None = None):
        self.passages = passages or {}
        self.calls: list[tuple[str, str, int]] = []

    def search(self, domain, query: str, *, k: int) -> list[Passage]:
        self.calls.append((domain, query, k))
        return list(self.passages.get(domain, []))[:k]


class MemoryAuditStore:
    """Doublure du port AuditStore : journal en mémoire, mêmes règles que PostgreSQL
    (un enregistrement par thread, ajout rejoué idempotent à décision égale)."""

    def __init__(self):
        self.stored: list[StoredAuditEntry] = []

    def append(self, seal) -> StoredAuditEntry:
        entry = seal(self.stored[-1].chain_hash if self.stored else None)
        for stored in self.stored:
            if stored.thread_id == entry.thread_id:
                if stored.decision_hash != entry.decision_hash:
                    raise AuditStoreError(
                        f"thread {entry.thread_id} déjà scellé avec une autre décision"
                    )
                return stored
        created_at = datetime.fromisoformat(entry.record["sealed_at"])
        stored = StoredAuditEntry(
            **entry.model_dump(), id=len(self.stored) + 1, created_at=created_at
        )
        self.stored.append(stored)
        return stored

    def entries(self) -> list[StoredAuditEntry]:
        return list(self.stored)


# horloge fixe des tests : l'horodatage scellé ne varie pas d'une exécution à l'autre
FIXED_NOW = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return FIXED_NOW


# explication des tests par défaut : le gabarit, sans LLM, motif scellé
TEMPLATE = TemplateOnly("tests : explication par le gabarit")


def make_deps(
    extractor=None, crag=None, audit_store=None, clock=fixed_clock, explainer=TEMPLATE
) -> Deps:
    """Dépendances de test : doublures, journal d'audit en mémoire, horloge fixe,
    explication par le gabarit."""
    return Deps(
        extractor=extractor if extractor is not None else FixedExtractor(clauses()),
        crag=crag if crag is not None else FakeCrag(),
        audit_store=audit_store if audit_store is not None else MemoryAuditStore(),
        clock=clock,
        explainer=explainer,
    )


def faithful_explanation(user: str) -> dict:
    """Réponse fidèle d'un LLM d'explication (FakeLLM, nœud `explain`) : chaque constat du
    dossier reçu, avec sa clause et ses références, et une synthèse qui nomme la décision
    finale."""
    data, _ = json.JSONDecoder().raw_decode(user, user.index("{"))
    return {
        "findings": [
            {
                "id": f["id"],
                "kind": f["kind"],
                "references": f["references"],
                "text": f["text"],
            }
            for f in data["findings"]
        ],
        "synthesis": f"Décision finale : {data['final_decision']}.",
    }


def context(config=None) -> dict:
    """Contexte d'analyse (config_hash, modèles), tel que run_contract le pose dans l'état
    initial : à joindre à toute entrée passée directement au graphe."""
    return audit.analysis_context(config if config is not None else load_config())
