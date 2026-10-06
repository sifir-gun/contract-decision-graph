"""Interface web : adaptateur entrant, au même titre que la CLI (`cdg.cli web`).

Pages rendues côté serveur (Jinja2, échappement automatique), HTMX pour l'interactivité,
aucune ressource externe. Chaque action appelle le service des contrats, par la même
méthode que sa commande de la CLI (`application/service.py`, `tests/test_parite.py`) :
aucune logique métier ici, seulement la lecture des formulaires et la mise en page.

Le texte d'un contrat reçu n'est ni journalisé, ni renvoyé, ni gardé en session : il est
passé au service, qui le masque avant le graphe, comme la CLI.

Derrière oauth2-proxy (`Authentication`, PR D1) : chaque requête porte le jeton
d'identité signé, vérifié avant tout (zéro confiance) ; sans jeton valide, 401, tracé au
journal des accès. Les en-têtes d'identité seuls ne comptent pas.

Les pages sont des fonctions ordinaires : FastAPI les exécute dans son pool de threads, et
une analyse en cours (appels au LLM, plusieurs secondes) ne bloque pas les autres pages.
Seule la lecture du formulaire, avec son contrôle CSRF, reste asynchrone (`Depends`). Le
service fait passer les modifications l'une après l'autre ; les lectures restent
concurrentes.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import DictLoader, Environment, StrictUndefined
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException

from cdg.adapters.web import acces, presentation, security
from cdg.application import demo_set, saisie
from cdg.application.saisie import InputError  # formulaire refusé : 400, message
from cdg.application.service import ContractService, FourEyesRefused
from cdg.domain import audit, authorization
from cdg.domain.authorization import Actor
from cdg.domain.identifiers import ContractIdError
from cdg.ports.connections import ConnectionsExhausted
from cdg.ports.engine import ThreadError
from cdg.ports.identity import IdentityRejected, IdentityVerifier, ProviderUnavailable
from cdg.ports.locks import ContractBusy
from cdg.settings import SettingsError

WEB_ROOT = Path(__file__).parent
# RFC 9110 : HEAD accepté là où GET l'est (FastAPI ne l'ajoute pas de lui-même)
PAGE_METHODS = ["GET", "HEAD"]
log = logging.getLogger(__name__)


class Forbidden(Exception):
    """Jeton CSRF absent ou faux, ou origine étrangère : code 403."""


@dataclass(frozen=True)
class Authentication:
    """Interface derrière oauth2-proxy (`web --identite en-tetes`)."""

    verifier: IdentityVerifier
    public_origin: str  # https://hôte : origine des formulaires, cookies Secure
    client_id: str  # audience du jeton ; client annoncé à la fin de session
    # rôle -> groupes du jeton qui le donnent (PR D2)
    roles: Mapping[str, Sequence[str]] = field(default_factory=dict)
    # second facteur exigé pour trancher et expirer ; non exigé par défaut
    second_factor: authorization.SecondFactor = field(
        default_factory=authorization.SecondFactor
    )
    provider_logout: bool = (
        False  # déconnexion : fermer aussi la session du fournisseur
    )


BEARER = "bearer "
SIGN_OUT = "/oauth2/sign_out"  # fin de session d'oauth2-proxy, même origine


def bearer(header: str | None) -> str | None:
    """Jeton d'un en-tête `Authorization: Bearer …`, ou None."""
    if header and header[: len(BEARER)].lower() == BEARER:
        return header[len(BEARER) :].strip() or None
    return None


def logout_target(auth: Authentication) -> tuple[str, str]:
    """Suite de la déconnexion (fin de session d'oauth2-proxy, puis celle du fournisseur
    quand il la publie et que le chart la demande) et l'état de la session du fournisseur.
    Le jeton n'y figure jamais : le client est désigné par son identifiant."""
    back, state = "/", "desactivee"
    if auth.provider_logout:
        try:
            endpoint = auth.verifier.end_session_endpoint()
        except ProviderUnavailable:
            endpoint, state = None, "fournisseur_injoignable"
        else:
            state = "fermee" if endpoint else "non_publiee"
        if endpoint:
            query = urlencode(
                {
                    "client_id": auth.client_id,
                    "post_logout_redirect_uri": f"{auth.public_origin}/",
                }
            )
            back = f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"
    return f"{SIGN_OUT}?rd={quote(back, safe='')}", state


def create_app(
    service: ContractService,
    *,
    demo: bool = False,
    hosts: Sequence[str] | None = security.LOOPBACK_NAMES,
    csrf_keys: Callable[[], security.CsrfKeys] | None = None,
    draining: Callable[[], bool] = lambda: False,
    authentication: Authentication | None = None,
) -> FastAPI:
    """`hosts` : noms admis dans l'en-tête Host (None : tous, écoute non locale
    explicite). `demo` : bandeau permanent, contrats du jeu seulement.
    `authentication` : derrière oauth2-proxy, jeton d'identité exigé et vérifié."""
    limit = security.check_body_limit(service.config)
    csrf = security.Csrf(csrf_keys)
    env = Environment(
        loader=DictLoader(_templates(WEB_ROOT / "templates")),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        demo=demo,
        authenticated=authentication is not None,
        decisions=presentation.DECISION_LABELS,
        states=presentation.STATE_LABELS,
        domains=presentation.DOMAIN_LABELS,
        retrievals=presentation.RETRIEVAL_LABELS,
        highlight=presentation.highlight,
        quotes_of=presentation.quotes_of,
        reference_rows=presentation.reference_rows,
        short_hash=presentation.short_hash,
        actor_label=presentation.actor_label,
        human_label=presentation.human_label,
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
        identity = getattr(request.state, "identity", None)
        response = templates.TemplateResponse(
            request,
            name,
            {**context, "csrf": csrf.token(cookie), "identity": identity},
            status_code=status,
        )
        if fresh:
            response.set_cookie(
                csrf.COOKIE,
                cookie,
                httponly=True,
                samesite="strict",
                path="/",
                secure=authentication is not None,  # derrière TLS
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
        origin = request.headers.get("origin")
        if authentication is not None:  # derrière le proxy : l'origine publique
            same = origin is None or origin == authentication.public_origin
        else:
            same = security.same_origin(
                origin, request.url.scheme, request.headers.get("host", "")
            )
        if not valid or not same:
            raise Forbidden
        return form

    # formulaire lu et contrôlé (CSRF, origine) sur la boucle, avant la page
    CheckedForm = Annotated[FormData, Depends(checked_form)]

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        name = security.host_name(request.headers.get("host", ""))
        length = request.headers.get("content-length", "")
        posted = request.method == "POST"
        refused = None if authentication is None else await authenticate(request)
        if refused is None and authentication is not None:
            refused = authorize(request)
        if hosts is not None and name not in hosts:
            response: Response = HTMLResponse("Hôte non admis.", status_code=400)
        elif refused is not None:
            response = refused
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

    def action_of(request: Request) -> str | None:
        """Action demandée, au sens des rôles ; None : ouverte à tout utilisateur
        authentifié (déconnexion, ressources de la page d'erreur)."""
        path = request.url.path
        if (
            path == "/deconnexion"
            or path == "/favicon.ico"
            or path.startswith("/static/")
        ):
            return None
        if request.method == "POST":
            # relancer, c'est analyser : rôle d'analyste, comme une analyse
            if path == "/analyse" or (
                path.startswith("/contrats/") and path.endswith("/relance")
            ):
                return "analyser"
            if path.startswith("/contrats/") and path.endswith("/decision"):
                return "trancher"
            if path == "/administration/expiration":
                return "expirer"
        return "lire"

    def authorize(request: Request) -> Response | None:
        """Rôle requis par l'action, puis second facteur du relecteur ; refus 403,
        tracé au journal des accès."""
        assert authentication is not None
        identity = request.state.identity
        granted = authorization.roles(identity, authentication.roles)
        request.state.roles = granted
        action = action_of(request)
        if action is None:
            return None
        where = {
            "iss": identity.issuer,
            "sub": identity.subject,
            "methode": request.method,
            "chemin": request.url.path,
        }
        if not authorization.permitted(granted, action):
            required = authorization.required_roles(action)
            acces.event(
                "acces_interdit",
                motif="role_manquant",
                role=",".join(required),
                **where,
                statut=403,
            )
            return page(
                request,
                "erreur.html",
                {
                    "title": "Accès refusé",
                    "message": f"Rôle requis : {' ou '.join(required)}.",
                },
                403,
            )
        second = authentication.second_factor
        if action in ("trancher", "expirer") and not (
            authorization.second_factor_proven(identity, second)
        ):
            acces.event("second_facteur_manquant", **where, statut=403)
            return page(
                request,
                "erreur.html",
                {
                    "title": "Second facteur requis",
                    "message": "Trancher ou expirer exige une connexion avec un second "
                    "facteur : reconnectez-vous en le fournissant.",
                },
                403,
            )
        return None

    def can_decide(request: Request) -> bool:
        if authentication is None:
            return True  # interface locale : un seul utilisateur, sur son poste
        return "relecteur" in getattr(request.state, "roles", set())

    def can_analyse(request: Request) -> bool:
        if authentication is None:
            return True
        return "analyste" in getattr(request.state, "roles", set())

    def actor(request: Request) -> Actor:
        """Qui agit : l'identité vérifiée derrière oauth2-proxy ; sinon l'interface
        locale, non authentifiée (poste seulement : refusée au démarrage dans le
        cluster)."""
        if authentication is None:
            return Actor(canal="locale", authentifie=False)
        return authorization.interface_actor(request.state.identity)

    async def authenticate(request: Request) -> Response | None:
        """Jeton d'identité vérifié, ou la réponse de refus (401, 503), tracée. Le
        vérificateur peut lire les clés du fournisseur (jusqu'à 5 s) : appelé dans un
        fil, jamais dans la boucle du serveur, la seule du pod (06/10)."""
        assert authentication is not None
        where = {"methode": request.method, "chemin": request.url.path}
        token = bearer(request.headers.get("authorization"))
        try:
            if token is None:
                raise IdentityRejected("jeton_absent")
            request.state.identity = await run_in_threadpool(
                authentication.verifier.verify, token
            )
        except IdentityRejected as exc:
            acces.event("acces_refuse", motif=exc.reason, **where, statut=401)
            response = page(
                request,
                "erreur.html",
                {
                    "title": "Authentification requise",
                    "message": "Identité non vérifiée : reconnectez-vous.",
                },
                401,
            )
            response.headers["www-authenticate"] = (
                'Bearer realm="contract-decision-graph"'
            )
            return response
        except ProviderUnavailable as exc:
            acces.event("fournisseur_injoignable", **where, cause=exc.cause, statut=503)
            response = page(
                request,
                "erreur.html",
                {
                    "title": "Fournisseur d'identité injoignable",
                    "message": "L'identité ne peut pas être vérifiée pour le moment : "
                    "réessayez dans quelques instants.",
                },
                503,
            )
            response.headers["Retry-After"] = "30"
            return response
        return None

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

    # --- déconnexion (derrière oauth2-proxy) -------------------------------------------

    if authentication is not None:

        @app.post("/deconnexion")
        def deconnexion(request: Request, form: CheckedForm) -> Response:
            """Page de transition vers la fin de session d'oauth2-proxy : une redirection
            après le formulaire serait soumise à form-action 'self', redirections
            comprises, et bloquée vers le fournisseur."""
            identity = request.state.identity
            target, state = logout_target(authentication)
            acces.event(
                "deconnexion",
                iss=identity.issuer,
                sub=identity.subject,
                session_fournisseur=state,
            )
            return page(request, "deconnexion.html", {"target": target})

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
            entry = _contract_input(form, demo)
            contract_id = saisie.contract_id(
                _text(form, "identifiant"), entry.base, service.now()
            )
            on = saisie.analysis_date(_text(form, "date"))
        except InputError as exc:
            return analysis_page(request, str(exc), 400)
        try:
            service.analyse(
                entry.text,
                contract_id=contract_id,
                actor=actor(request),
                parties=entry.parties,
                analysis_date=on,
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
                "can_decide": can_decide(request),
                "can_analyse": can_analyse(request),
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
            "acteur": actor(request).model_dump(mode="json"),
            "reason": _text(form, "motif"),
            "overrides_block": form.get("levee") == "oui",
        }
        try:
            status = service.decide(thread_id, answer)
        except FourEyesRefused as exc:
            identity = request.state.identity
            acces.event(
                "quatre_yeux_refuse",
                iss=identity.issuer,
                sub=identity.subject,
                thread_id=thread_id,
                methode=request.method,
                chemin=request.url.path,
                statut=403,
            )
            return page(
                request,
                "erreur.html",
                {"title": "Revue refusée (quatre yeux)", "message": str(exc)},
                403,
            )
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

    @app.post("/contrats/{thread_id}/relance")
    def relance(request: Request, thread_id: str, form: CheckedForm) -> Response:
        """Relance, sous la configuration actuelle, d'un contrat escaladé pour
        changement de configuration : un nouveau contrat ; l'escaladé reste en attente."""
        wanted = _text(form, "identifiant").strip() or None
        try:
            status = service.relaunch(
                thread_id, actor=actor(request), contract_id=wanted
            )
        except ContractIdError as exc:
            return dossier_page(request, thread_id, str(exc), 400)
        except (ThreadError, ContractBusy) as exc:
            return page(
                request,
                "erreur.html",
                {"title": "Relance impossible", "message": str(exc)},
                409,
            )
        except SettingsError as exc:
            return page(
                request,
                "erreur.html",
                {"title": "Relance impossible", "message": str(exc)},
                503,
            )
        return RedirectResponse(
            presentation.contract_path(status["thread_id"]), status_code=303
        )

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
        at, expired = service.expire(timedelta(hours=hours), actor(request))
        return page(
            request,
            "expiration_resultat.html",
            {"hours": hours, "at": at, "expired": expired},
        )

    return app


def _templates(directory: Path) -> dict[str, str]:
    """Gabarits lus une fois, à la création de l'application : ceux du code lancé. Relus
    sur disque (`FileSystemLoader`, rechargé par défaut à chaque modification), ceux d'un
    autre commit servaient au code resté en mémoire : nom inconnu, 500 sur toutes les
    pages (28/09)."""
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*.html"))
    }


# --- lecture des formulaires ---------------------------------------------------------


def _text(form: FormData, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _contract_input(form: FormData, demo: bool) -> saisie.ContractInput:
    """Texte du contrat, parties à masquer, base de l'identifiant par défaut : la saisie
    commune avec le serveur MCP (`application/saisie.py`)."""
    parties = _text(form, "parties").splitlines()
    source = _text(form, "source")
    if source == "jeu":
        return saisie.from_demo_set(_text(form, "contrat"), parties, demo=demo)
    if source == "texte":
        return saisie.from_text(lambda: _text(form, "texte"), parties, demo=demo)
    if source == "fichier":
        return saisie.from_text(
            lambda: _uploaded_text(form.get("fichier")), parties, demo=demo
        )
    raise InputError("source inconnue : contrat du jeu, texte collé ou fichier")


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
