"""Interface web : adaptateur entrant, au même titre que la CLI (`cdg.cli web`).

Pages rendues côté serveur (Jinja2, échappement automatique), HTMX pour l'interactivité,
aucune ressource externe. Chaque action appelle le service des contrats, par la même
méthode que sa commande de la CLI (`application/service.py`, `tests/test_parite.py`) :
aucune logique métier ici, seulement la lecture des formulaires et la mise en page.

Le texte d'un contrat reçu n'est ni journalisé, ni renvoyé, ni gardé en session : il est
passé au service, qui le masque avant le graphe, comme la CLI.

Les pages sont des fonctions ordinaires : FastAPI les exécute dans son pool de threads, et
une analyse en cours (appels au LLM, plusieurs secondes) ne bloque pas les autres pages.
Seule la lecture du formulaire, avec son contrôle CSRF, reste asynchrone (`Depends`). Le
service fait passer les modifications l'une après l'autre ; les lectures restent
concurrentes.
"""

import logging
import re
import secrets
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException

from cdg.adapters.web import presentation, security
from cdg.application import demo_set
from cdg.application.service import ContractService
from cdg.domain import audit
from cdg.domain.identifiers import ContractIdError, check_contract_id
from cdg.ports.connections import ConnectionsExhausted
from cdg.ports.engine import ThreadError
from cdg.ports.locks import ContractBusy
from cdg.settings import SettingsError

WEB_ROOT = Path(__file__).parent
# RFC 9110 : HEAD accepté là où GET l'est (FastAPI ne l'ajoute pas de lui-même)
PAGE_METHODS = ["GET", "HEAD"]
# caractères de contrôle hors tabulation et fins de ligne : pas du texte brut
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
log = logging.getLogger(__name__)


class InputError(Exception):
    """Formulaire refusé : message montré à l'utilisateur, code 400."""


class Forbidden(Exception):
    """Jeton CSRF absent ou faux, ou origine étrangère : code 403."""


def create_app(
    service: ContractService,
    *,
    demo: bool = False,
    hosts: Sequence[str] | None = security.LOOPBACK_NAMES,
    csrf_secret: bytes | None = None,
    draining: Callable[[], bool] = lambda: False,
) -> FastAPI:
    """`hosts` : noms admis dans l'en-tête Host (None : tous, écoute non locale
    explicite). `demo` : bandeau permanent, contrats du jeu seulement."""
    limit = security.check_body_limit(service.config)
    csrf = security.Csrf(csrf_secret)
    env = Environment(
        loader=FileSystemLoader(WEB_ROOT / "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        demo=demo,
        decisions=presentation.DECISION_LABELS,
        states=presentation.STATE_LABELS,
        domains=presentation.DOMAIN_LABELS,
        retrievals=presentation.RETRIEVAL_LABELS,
        highlight=presentation.highlight,
        quotes_of=presentation.quotes_of,
        reference_rows=presentation.reference_rows,
        short_hash=presentation.short_hash,
        contract_path=presentation.contract_path,
    )
    templates = Jinja2Templates(env=env)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    @app.api_route("/favicon.ico", methods=PAGE_METHODS)
    def favicon() -> FileResponse:
        """Demandée d'office par les navigateurs : l'icône des pages, servie localement."""
        return FileResponse(
            WEB_ROOT / "static" / "favicon.svg", media_type="image/svg+xml"
        )

    def page(
        request: Request, name: str, context: dict[str, Any], status: int = 200
    ) -> Response:
        cookie = request.cookies.get(csrf.COOKIE)
        fresh = cookie is None
        if cookie is None:
            cookie = csrf.new_cookie()
        response = templates.TemplateResponse(
            request,
            name,
            {**context, "csrf": csrf.token(cookie)},
            status_code=status,
        )
        if fresh:
            response.set_cookie(
                csrf.COOKIE, cookie, httponly=True, samesite="strict", path="/"
            )
        return response

    def fragment_or_page(
        request: Request, name: str, context: dict[str, Any], status: int = 200
    ) -> Response:
        """Fragment pour HTMX ; sans JavaScript, la page complète qui l'inclut."""
        if request.headers.get("hx-request") == "true":
            return templates.TemplateResponse(
                request, f"_{name}", context, status_code=status
            )
        return page(request, name, context, status)

    async def checked_form(request: Request) -> FormData:
        form = await request.form(max_files=1, max_fields=16, max_part_size=limit)
        token = form.get(csrf.FIELD)
        valid = csrf.valid(
            request.cookies.get(csrf.COOKIE),
            token if isinstance(token, str) else None,
        )
        if not valid or not security.same_origin(
            request.headers.get("origin"),
            request.url.scheme,
            request.headers.get("host", ""),
        ):
            raise Forbidden
        return form

    # formulaire lu et contrôlé (CSRF, origine) sur la boucle, avant la page
    CheckedForm = Annotated[FormData, Depends(checked_form)]

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        name = security.host_name(request.headers.get("host", ""))
        length = request.headers.get("content-length", "")
        posted = request.method == "POST"
        if hosts is not None and name not in hosts:
            response: Response = HTMLResponse("Hôte non admis.", status_code=400)
        elif posted and not length:
            response = HTMLResponse("Longueur de l'envoi requise.", status_code=411)
        elif posted and draining():
            # arrêt en cours : toute modification est à rejouer sur un autre réplica
            response = page(
                request,
                "erreur.html",
                {
                    "title": "Arrêt en cours",
                    "message": "Ce serveur s'arrête : réessayez dans quelques secondes.",
                },
                503,
            )
            response.headers["Retry-After"] = "5"
        elif posted and (not length.isdigit() or int(length) > limit):
            response = page(
                request,
                "erreur.html",
                {
                    "title": "Envoi trop volumineux",
                    "message": f"Au plus {limit // 1024} Ko par envoi : le plus long "
                    "contrat admis à l'analyse, en UTF-8.",
                },
                413,
            )
        else:
            response = await call_next(request)
        response.headers.update(security.HEADERS)
        return response

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden) -> Response:
        return page(
            request,
            "erreur.html",
            {
                "title": "Formulaire refusé",
                "message": "Jeton de formulaire absent ou périmé, ou envoi depuis un "
                "autre site. Rechargez la page, puis recommencez.",
            },
            403,
        )

    @app.exception_handler(ConnectionsExhausted)
    async def exhausted(request: Request, exc: ConnectionsExhausted) -> Response:
        response = page(
            request,
            "erreur.html",
            {"title": "Base de données saturée", "message": str(exc)},
            503,
        )
        response.headers["Retry-After"] = "5"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        title = "Page introuvable" if exc.status_code == 404 else "Requête refusée"
        return page(
            request,
            "erreur.html",
            {"title": title, "message": str(exc.detail)},
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> Response:
        # le type seulement : un message pourrait citer une donnée reçue
        log.error("erreur inattendue (%s) sur %s", type(exc).__name__, request.url.path)
        response = page(
            request,
            "erreur.html",
            {
                "title": "Erreur",
                "message": f"Erreur inattendue : {type(exc).__name__}.",
            },
            500,
        )
        # servie hors du middleware (ServerErrorMiddleware) : en-têtes posés ici
        response.headers.update(security.HEADERS)
        return response

    # --- liste des contrats (list) ----------------------------------------------------

    @app.api_route("/", methods=PAGE_METHODS, response_class=HTMLResponse)
    def contracts(request: Request, attente: str = "") -> Response:
        rows = service.contracts(pending_only=bool(attente))
        return page(
            request, "liste.html", {"rows": rows, "pending_only": bool(attente)}
        )

    # --- nouvelle analyse (run) -------------------------------------------------------

    def analysis_page(
        request: Request, error: str | None = None, status: int = 200
    ) -> Response:
        _, contracts = demo_set.load()
        return page(
            request,
            "analyse.html",
            {"contracts": list(contracts.values()), "error": error},
            status,
        )

    @app.api_route("/analyse", methods=PAGE_METHODS, response_class=HTMLResponse)
    def analysis_form(request: Request) -> Response:
        return analysis_page(request)

    @app.post("/analyse")
    def analyse(request: Request, form: CheckedForm) -> Response:
        try:
            text, parties, base = _contract_input(form, demo)
            contract_id = _identifier(form, base, service)
            on = _analysis_date(form)
        except InputError as exc:
            return analysis_page(request, str(exc), 400)
        try:
            service.analyse(
                text, contract_id=contract_id, parties=parties, analysis_date=on
            )
        except (ThreadError, ContractBusy) as exc:
            return analysis_page(request, str(exc), 409)
        except SettingsError as exc:
            message = (
                f"{exc}. Renseignez la clé dans .env, puis relancez l'interface ; ou "
                "lancez le mode démonstration, sans clé ni coût."
            )
            return analysis_page(request, message, 503)
        return RedirectResponse(
            presentation.contract_path(contract_id), status_code=303
        )

    # --- dossier (show), revue humaine (resume), rejeu (replay) -----------------------

    def dossier_page(
        request: Request, thread_id: str, error: str | None = None, status: int = 200
    ) -> Response:
        try:
            dossier = service.dossier(thread_id)
        except ThreadError as exc:
            raise HTTPException(404, str(exc)) from exc
        return page(
            request,
            "dossier.html",
            {
                "d": dossier,
                "s": dossier["status"],
                "allowed": service.config.human_policy.allowed_decisions,
                "error": error,
            },
            status,
        )

    @app.api_route(
        "/contrats/{thread_id}", methods=PAGE_METHODS, response_class=HTMLResponse
    )
    def dossier(request: Request, thread_id: str) -> Response:
        return dossier_page(request, thread_id)

    @app.post("/contrats/{thread_id}/decision")
    def decision(request: Request, thread_id: str, form: CheckedForm) -> Response:
        answer = {
            "decision": _text(form, "decision"),
            "reviewer": _text(form, "relecteur"),
            "reason": _text(form, "motif"),
            "overrides_block": form.get("levee") == "oui",
        }
        try:
            status = service.decide(thread_id, answer)
        except (ThreadError, ContractBusy) as exc:
            return page(
                request,
                "erreur.html",
                {"title": "Revue impossible", "message": str(exc)},
                409,
            )
        refused = status["demande"] and status["demande"].get("error")
        if refused:
            return dossier_page(request, thread_id, refused, 422)
        return RedirectResponse(presentation.contract_path(thread_id), status_code=303)

    @app.api_route(
        "/contrats/{thread_id}/rejeu", methods=PAGE_METHODS, response_class=HTMLResponse
    )
    def replay(request: Request, thread_id: str) -> Response:
        context: dict[str, Any] = {"thread_id": thread_id}
        try:
            context["report"] = service.replay(thread_id)
        except audit.ReplayError as exc:
            context |= {"report": None, "error": str(exc)}
            return fragment_or_page(request, "rejeu.html", context, 409)
        return fragment_or_page(request, "rejeu.html", context | {"error": None})

    # --- journal d'audit (journal, verify) --------------------------------------------

    @app.api_route("/journal", methods=PAGE_METHODS, response_class=HTMLResponse)
    def journal(request: Request) -> Response:
        return page(request, "journal.html", {"entries": service.journal()})

    @app.api_route(
        "/journal/verification", methods=PAGE_METHODS, response_class=HTMLResponse
    )
    def verification(request: Request, tete: str = "") -> Response:
        expected = tete.strip() or None
        context: dict[str, Any]
        if expected is not None and not audit.is_hash(expected):
            context = {
                "report": None,
                "expected": None,
                "error": "empreinte invalide : 64 caractères hexadécimaux minuscules",
            }
            return fragment_or_page(request, "verification.html", context, 400)
        report = service.verify(expected)
        context = {"report": report, "expected": expected, "error": None}
        return fragment_or_page(request, "verification.html", context)

    # --- administration : expiration (expire) -----------------------------------------

    def admin_context(error: str | None) -> dict[str, Any]:
        return {"error": error, "check": service.config_check()}

    @app.api_route("/administration", methods=PAGE_METHODS, response_class=HTMLResponse)
    def administration(request: Request) -> Response:
        return page(request, "administration.html", admin_context(None))

    @app.post("/administration/expiration")
    def expiration(request: Request, form: CheckedForm) -> Response:
        raw = _text(form, "heures").strip()
        if not raw.isdigit() or int(raw) <= 0:
            return page(
                request,
                "administration.html",
                admin_context(
                    "nombre d'heures invalide : un entier positif est attendu"
                ),
                400,
            )
        hours = int(raw)
        if form.get("confirme") != "oui":
            return page(request, "expiration_confirmation.html", {"hours": hours})
        at, expired = service.expire(timedelta(hours=hours))
        return page(
            request,
            "expiration_resultat.html",
            {"hours": hours, "at": at, "expired": expired},
        )

    return app


# --- lecture des formulaires ---------------------------------------------------------


def _text(form: FormData, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _contract_input(form: FormData, demo: bool) -> tuple[str, list[str], str]:
    """Texte du contrat, parties à masquer, base de l'identifiant par défaut."""
    parties = [p.strip() for p in _text(form, "parties").splitlines() if p.strip()]
    source = _text(form, "source")
    if source == "jeu":
        _, contracts = demo_set.load()
        chosen = contracts.get(_text(form, "contrat"))
        if chosen is None:
            raise InputError("contrat inconnu du jeu de démonstration")
        # démonstration : les parties déclarées, dont dépend l'extraction simulée
        declared = list(chosen.parties)
        return chosen.text(), declared if demo else parties or declared, chosen.id
    if source in ("texte", "fichier") and demo:
        raise InputError(
            "démonstration : l'extraction est simulée pour les contrats du jeu "
            "seulement ; choisissez-en un"
        )
    if source == "texte":
        text = _text(form, "texte")
    elif source == "fichier":
        text = _uploaded_text(form.get("fichier"))
    else:
        raise InputError("source inconnue : contrat du jeu, texte collé ou fichier")
    if not text.strip():
        raise InputError("le texte du contrat est vide")
    if CONTROL.search(text):
        raise InputError(
            "le texte contient un caractère de contrôle : ce n'est pas du texte brut"
        )
    return text, parties, "contrat"


def _uploaded_text(upload: object) -> str:
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise InputError("aucun fichier envoyé")
    if (upload.content_type or "").split(";")[0].strip() != "text/plain":
        raise InputError("le fichier doit être du texte brut (text/plain, .txt)")
    # lecture directe, dans le pool de threads : le fichier reste en mémoire, la taille
    # d'un envoi étant bornée sous le seuil d'écriture sur disque (check_body_limit)
    data = upload.file.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError("le fichier n'est pas du texte encodé en UTF-8") from exc


def _identifier(form: FormData, base: str, service: ContractService) -> str:
    wanted = _text(form, "identifiant").strip()
    if not wanted:
        return f"{base}-{service.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    try:
        return check_contract_id(wanted)  # la règle du domaine, commune avec la CLI
    except ContractIdError as exc:
        raise InputError(str(exc)) from exc


def _analysis_date(form: FormData) -> date | None:
    raw = _text(form, "date").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise InputError("date invalide : AAAA-MM-JJ attendu") from exc
