"""Serveur factice de l'API de Mistral, pour les tests du cluster (ADR 005, PR C3). Jamais
dans l'image de l'application : il a sa propre image de test (docker/mistral-factice/), et
tests/test_image.py vérifie son absence de l'image publiée.

`POST /v1/chat/completions`, comme l'appelle le SDK mistralai (`chat.parse`), répond selon
le schéma demandé (`response_format.json_schema.name`) :
- le schéma d'extraction d'un profil (`ExtractionOutput` pour `fournisseur`,
  `ExtractionOutputSousTraitance` pour `sous_traitance` : la table vient de
  `extraction.SCHEMAS`, sans liste à tenir ici) : les clauses d'une extraction correcte du
  contrat du jeu reconnu à son profil et à son texte masqué (data/contracts/attendus.yaml),
  comme l'extraction simulée de la démonstration ; refus explicites (422) d'un texte hors du
  jeu, et d'un contrat du jeu reçu sous le schéma d'un autre profil ;
- `GradeOutput` : tous les extraits proposés, jugés pertinents ;
- `RewriteOutput` : la dernière requête essayée ;
- `Draft` : chaque constat reçu, sous son identifiant, sa clause et ses références, avec un
  texte neutre qui ne nomme ni décision ni article.

Pour les scénarios : `POST /controle` règle un délai avant chaque réponse
(`{"delai": secondes}`), `GET /controle` rend le délai, les requêtes reçues (comptées à l'arrivée), les appels
réussis par schéma et les refus.
Bibliothèque standard seulement, et le paquet `cdg` (l'image de test part de celle de
l'application : `PYTHONPATH=/app/src`).
"""

import argparse
import json
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cdg.application import demo_set, extraction
from cdg.domain import masking
from cdg.domain.models import Clause

OPEN, HEAD_END, CLOSE = "<<<CONTRAT-", ">>>\n", "\n<<<FIN-CONTRAT-"
EXCERPT = re.compile(r"^<<<EXTRAIT (\d+)>>>$", re.MULTILINE)
TRIED = "Requêtes déjà essayées :\n"
NEUTRAL = "Explication du serveur factice pour ce constat, rédigée pour les tests."
CLAUSE_FIELDS = ("kind", "present", "quote", "value", "category")
# nom du schéma d'extraction (`response_format.json_schema.name`, que le SDK mistralai forme du
# `__name__` du modèle) -> profil : un schéma de profil ajouté à `SCHEMAS` y est reconnu
PROFILE_OF_SCHEMA = {schema.__name__: p for p, schema in extraction.SCHEMAS.items()}


class Refused(Exception):
    """Requête à laquelle le serveur factice ne sait pas répondre : 422."""


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.delay = 0.0
        self.calls: dict[str, int] = {}
        self.received = 0  # à l'arrivée, avant le délai : une analyse en cours se voit
        self.refused = 0
        _, contracts = demo_set.load()
        # (profil, texte masqué) -> clauses attendues, comme l'extraction simulée de la
        # démonstration ; un contrat rejeté à l'entrée, sans clause, n'y figure pas
        self.by_text: dict[tuple[str, str], list[Clause]] = {
            (c.profile, masking.mask(c.text(), c.parties).text): list(c.clauses)
            for c in contracts.values()
            if c.clauses
        }

    def declared(self, text: str) -> list[str]:
        """Profils sous lesquels ce texte masqué est un contrat du jeu."""
        return sorted(profile for profile, known in self.by_text if known == text)


def _contract(user: str) -> str | None:
    """Texte entre les balises du contrat et leur jeton (application/extraction.py), par
    découpage de chaîne : temps linéaire, là où une expression à référence arrière
    revenait en arrière sur une entrée hostile (CodeQL)."""
    start = user.find(OPEN)
    head_end = user.find(HEAD_END, start + len(OPEN)) if start >= 0 else -1
    if head_end < 0:
        return None
    token = user[start + len(OPEN) : head_end]
    if not token or any(c.isspace() for c in token):
        return None
    body = head_end + len(HEAD_END)
    end = user.find(f"{CLOSE}{token}>>>", body)
    return user[body:end] if end >= 0 else None


def _extraction(state: State, profile: str, user: str) -> dict[str, Any]:
    text = _contract(user)
    if text is None:
        raise Refused("texte hors du jeu de démonstration")
    clauses = state.by_text.get((profile, text))
    if clauses is None:
        declared = state.declared(text)
        if not declared:
            raise Refused("texte hors du jeu de démonstration")
        # jamais les clauses d'un autre genre de contrat : refus qui nomme les profils,
        # tous deux connus du jeu et des schémas
        raise Refused(
            f"contrat du jeu du profil {', '.join(declared)}, reçu sous le schéma du "
            f"profil {profile}"
        )
    return {
        "clauses": [
            {k: v for k, v in c.model_dump(mode="json").items() if k in CLAUSE_FIELDS}
            for c in clauses
        ]
    }


def _grade(user: str) -> dict[str, Any]:
    return {"relevant": [int(n) for n in EXCERPT.findall(user)]}


def _rewrite(user: str) -> dict[str, Any]:
    tried = user.split(TRIED, 1)[-1].splitlines()
    queries = [q.removeprefix("- ").strip() for q in tried if q.startswith("- ")]
    if not queries:
        raise Refused("aucune requête essayée")
    return {"query": queries[-1]}


def _draft(user: str) -> dict[str, Any]:
    start, end = user.find("{"), user.rfind("}")
    if start < 0 or end < start:
        raise Refused("données de l'explication absentes")
    payload = json.loads(user[start : end + 1])
    return {
        "findings": [
            {
                "id": f["id"],
                "kind": f["kind"],
                "references": f["references"],
                "text": NEUTRAL,
            }
            for f in payload["findings"]
        ]
    }


def answer(state: State, schema: str, user: str) -> dict[str, Any]:
    if schema in PROFILE_OF_SCHEMA:
        return _extraction(state, PROFILE_OF_SCHEMA[schema], user)
    if schema == "GradeOutput":
        return _grade(user)
    if schema == "RewriteOutput":
        return _rewrite(user)
    if schema == "Draft":
        return _draft(user)
    raise Refused(f"schéma inconnu : {schema}")


def completion(model: str, content: dict[str, Any], user: str) -> dict[str, Any]:
    text = json.dumps(content, ensure_ascii=False)
    tokens_in, tokens_out = max(1, len(user) // 4), max(1, len(text) // 4)
    return {
        "id": f"factice-{time.monotonic_ns()}",
        "object": "chat.completion",
        "model": model,
        "created": int(time.time()),
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }


def handler(state: State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            if self.path != "/controle":
                self._send(HTTPStatus.NOT_FOUND, {"erreur": self.path})
                return
            with state.lock:
                body = {
                    "delai": state.delay,
                    "recues": state.received,
                    "appels": dict(state.calls),
                    "refus": state.refused,
                }
            self._send(HTTPStatus.OK, body)

        def do_POST(self) -> None:
            if self.path == "/controle":
                delay = float(self._body()["delai"])
                with state.lock:
                    state.delay = delay
                self._send(HTTPStatus.OK, {"delai": delay})
                return
            if self.path != "/v1/chat/completions":
                self._send(HTTPStatus.NOT_FOUND, {"erreur": self.path})
                return
            request = self._body()
            schema = request["response_format"]["json_schema"]["name"]
            user = next(
                m["content"] for m in request["messages"] if m["role"] == "user"
            )
            with state.lock:
                state.received += 1
                delay = state.delay
            time.sleep(delay)
            try:
                content = answer(state, schema, user)
            except Refused as exc:
                with state.lock:
                    state.refused += 1
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"message": str(exc)})
                return
            with state.lock:
                state.calls[schema] = state.calls.get(schema, 0) + 1
            self._send(HTTPStatus.OK, completion(request["model"], content, user))

        def log_message(self, format: str, *args: Any) -> None:
            pass  # aucun texte de contrat dans les journaux du serveur factice

    return Handler


def serve(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), handler(State()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hote", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = serve(args.hote, args.port)
    print(f"serveur factice de Mistral : {args.hote}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
