"""Fabriques de données synthétiques et doublures pour les tests."""

import json
from datetime import UTC, date, datetime

# journal d'audit en mémoire des tests : celui du mode démonstration (adapters/demo)
from cdg.adapters.demo.audit_store import MemoryAuditStore
from cdg.application.deps import Deps, ExtractionResult, RetrievalResult, TemplateOnly
from cdg.domain import audit
from cdg.domain.authorization import Actor, interface_actor
from cdg.domain.config import load_config
from cdg.domain.identity import Identity
from cdg.domain.models import (
    DOMAIN_KINDS,
    DOMAINS,
    REQUIRED_KINDS,
    AgentVerdict,
    Clause,
    ClauseRetrieval,
    RetrievalTrace,
    Usage,
)
from cdg.domain.verification import VALUE_UNITS
from cdg.ports.identity import IdentityRejected, ProviderUnavailable
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


def quote_of(kind: str, value) -> str:
    """Citation synthétique d'une clause présente : avec sa valeur et son unité quand elle
    est chiffrée, comme l'exige la vérification (correction 4 de la série 4)."""
    unit = VALUE_UNITS.get(kind)
    if value is None or unit is None:
        return f"Article synthétique : {kind}."
    return f"Article synthétique : la clause {kind} est fixée à {value:g} {unit}."


# Valeurs d'essai des tests qui passent par la vérification de l'extraction : le contrat
# synthétique en contient la citation. Une autre valeur donne « citation introuvable » :
# l'ajouter ici.
TEST_VALUES = {
    "responsabilite_fournisseur": (50.0,),
    "delai_paiement": (90.0,),
    "duree_engagement": (48.0,),
    "preavis_resiliation": (12.0,),
}

# Contrat synthétique en français, sans donnée réelle : contient la citation de chaque
# clause rendue par `clauses()`, sans valeur puis pour chaque valeur d'essai.
CONTRACT_TEXT = (
    "CONTRAT DE PRESTATION DE SERVICES (document synthétique)\n\n"
    "Entre la société cliente, ci-après dénommée l'Acheteur, et la société prestataire, "
    "ci-après dénommée le Fournisseur, il est convenu ce qui suit.\n\n"
    + "".join(
        f"{quote_of(kind, value)}\n"
        for kind in REQUIRED_KINDS
        for value in dict.fromkeys((None, FAVORABLE[kind], *TEST_VALUES.get(kind, ())))
    )
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
                    quote=quote_of(kind, value),
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
    reference: str, domain="financier", valid_until=None, text=None, id=1, kinds=None
) -> Passage:
    """Extrait de test ; rattaché par défaut à toutes les clauses de son domaine."""
    return Passage(
        id=id,
        domain=domain,
        source_id="test",
        reference=reference,
        text=text or f"Texte de {reference}.",
        distance=0.1,
        valid_until=valid_until,
        kinds=list(DOMAIN_KINDS[domain]) if kinds is None else kinds,
    )


class FakeRetriever:
    """Doublure du port Retriever : extraits fixes par domaine, filtrés par clause comme
    l'adaptateur ; requêtes et clauses enregistrées."""

    def __init__(self, passages: dict | None = None):
        self.passages = passages or {}
        self.calls: list[tuple[str, str, int]] = []
        self.searched_kinds: list[str] = []

    def search(self, domain, query: str, *, kind: str, k: int) -> list[Passage]:
        self.calls.append((domain, query, k))
        self.searched_kinds.append(kind)
        return [p for p in self.passages.get(domain, []) if kind in p.kinds][:k]


# horloge fixe des tests : l'horodatage scellé ne varie pas d'une exécution à l'autre
FIXED_NOW = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


ISSUER = "https://idp.example.org"
ANALYSTE = Identity(
    issuer=ISSUER,
    subject="sub-analyste-1",
    groups=("cdg-analystes",),
    display_name="analyste.affiche",
)
RELECTEUR = Identity(
    issuer=ISSUER,
    subject="sub-relecteur-1",
    groups=("cdg-relecteurs",),
    display_name="relecteur.affiche",
)
# acteurs scellés (PR D2) : l'analyste et le relecteur sont deux personnes (quatre yeux)
ACTEUR_ANALYSTE = interface_actor(ANALYSTE)
ACTEUR_RELECTEUR = interface_actor(RELECTEUR)
OPERATEUR = Actor(canal="cli", authentifie=False, operateur="relecteur-synth")


def answer(decision="NO_GO", reason="motif", acteur=ACTEUR_RELECTEUR, **extra) -> dict:
    """Réponse humaine brute à la reprise, au format v2 (acteur, jamais de nom)."""
    return {
        "decision": decision,
        "acteur": acteur.model_dump(mode="json"),
        "reason": reason,
        **extra,
    }


class FakeVerifier:
    """Vérificateur d'identité de test : quelques jetons connus, les autres refusés
    (signature invalide) ; fournisseur injoignable sur demande."""

    def __init__(
        self,
        identities: dict[str, Identity] | None = None,
        end_session: str | None = None,
        unavailable: bool = False,
    ):
        self.identities = (
            identities
            if identities is not None
            else {"jeton-analyste": ANALYSTE, "jeton-relecteur": RELECTEUR}
        )
        self.end_session = end_session
        self.unavailable = unavailable

    def verify(self, token: str) -> Identity:
        if self.unavailable:
            raise ProviderUnavailable("fournisseur d'identité injoignable (doublure)")
        if token not in self.identities:
            raise IdentityRejected("signature_invalide")
        return self.identities[token]

    def end_session_endpoint(self) -> str | None:
        return self.end_session


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
    dossier reçu, avec sa clause et ses références. La synthèse est écrite par le code."""
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
        ]
    }


def context(config=None) -> dict:
    """Contexte d'analyse (config_hash, modèles) et acteur de l'analyse, tels que
    run_contract les pose dans l'état initial : à joindre à toute entrée passée
    directement au graphe."""
    return {
        **audit.analysis_context(config if config is not None else load_config()),
        "analyse_par": ACTEUR_ANALYSTE,
    }
