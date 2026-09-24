"""Fabriques de données synthétiques et doublures pour les tests."""

from cdg.deps import ExtractionResult, RetrievalResult
from cdg.state import DOMAINS, REQUIRED_KINDS, AgentVerdict, Clause, Usage

# Contrat synthétique favorable : aucune règle déclenchée.
FAVORABLE = {
    "responsabilite_acheteur": 100.0,  # plafonnée à 100 % du montant annuel
    "responsabilite_fournisseur": 150.0,  # plafond fournisseur au-dessus du minimum
    "revision_prix": 3.0,  # révision plafonnée à 3 %
    "penalites_retard": 10.0,  # pénalités plafonnées à 10 %
    "duree_engagement": 24.0,  # mois
    "preavis_resiliation": 3.0,  # mois
    "donnees_personnelles": None,  # traitement présent
    "accord_traitement_donnees": None,  # accord présent
}

ABSENT = object()  # marqueur : la clause ne figure pas dans le contrat

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


def clauses(**overrides) -> list[Clause]:
    """Les 8 clauses attendues, favorables par défaut.

    `kind=valeur` remplace la valeur d'une clause présente (None compris) ;
    `kind=ABSENT` la rend absente.
    """
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
                Clause(kind=kind, present=True, quote=f"Article synthétique : {kind}.", value=value)
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
        node=node, model="double", tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=0
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
    """Doublure du CRAG : statut par domaine (OK par défaut), une référence si OK."""

    def __init__(self, statuses: dict | None = None, tokens_in=0, tokens_out=0):
        self.statuses, self.tokens = statuses or {}, (tokens_in, tokens_out)
        self.calls: list[str] = []

    def __call__(self, domain, clauses: list[Clause]) -> RetrievalResult:
        self.calls.append(domain)
        status = self.statuses.get(domain, "OK")
        return RetrievalResult(
            status=status,
            evidence_ids=[f"{domain}-ref-1"] if status == "OK" else [],
            usage=[usage(*self.tokens, node=f"crag:{domain}")],
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
            {"tier": tier, "system": system, "user": user, "schema": schema, "node": node}
        )
        scripted = self.responses[node]
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
