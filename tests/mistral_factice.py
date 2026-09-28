"""Serveur factice de l'API de Mistral, pour les tests du cluster (ADR 005, PR C3). Jamais
dans l'image de l'application : il a sa propre image de test (docker/mistral-factice/), et
tests/test_image.py vérifie son absence de l'image publiée.

`POST /v1/chat/completions`, comme l'appelle le SDK mistralai (`chat.parse`), répond selon
le schéma demandé (`response_format.json_schema.name`) :
- `ExtractionOutput` : les clauses d'une extraction correcte du contrat du jeu reconnu à son
  texte masqué (data/contracts/attendus.yaml), comme l'extraction simulée de la
  démonstration ; un texte hors du jeu est refusé (422), explicitement ;
- `GradeOutput` : tous les extraits proposés, jugés pertinents ;
- `RewriteOutput` : la dernière requête essayée ;
- `Draft` : chaque constat reçu, sous son identifiant, sa clause et ses références, avec un
  texte neutre qui ne nomme ni décision ni article.

Pour les scénarios : `POST /controle` règle un délai avant chaque réponse
(`{"delai": secondes}`), `GET /controle` rend le délai, les requêtes reçues (comptées à l'arrivée), les appels
réussis par schéma et les refus.
Bibliothèque standard seulement.
"""

import argparse
import json
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cdg.application import demo_set
from cdg.domain import masking

CONTRACT = re.compile(r"<<<CONTRAT-(\S+)>>>\n(.*)\n<<<FIN-CONTRAT-\1>>>", re.DOTALL)
EXCERPT = re.compile(r"^<<<EXTRAIT (\d+)>>>$", re.MULTILINE)
TRIED = "Requêtes déjà essayées :\n"
NEUTRAL = "Explication du serveur factice pour ce constat, rédigée pour les tests."
CLAUSE_FIELDS = ("kind", "present", "quote", "value", "category")


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
        self.by_text = {
            masking.mask(c.text(), c.parties).text: c.clauses
            for c in contracts.values()
            if c.clauses
        }


def _extraction(state: State, user: str) -> dict[str, Any]:
    match = CONTRACT.search(user)
    clauses = state.by_text.get(match[2]) if match else None
    if clauses is None:
        raise Refused("texte hors du jeu de démonstration")
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
    if schema == "ExtractionOutput":
        return _extraction(state, user)
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
