# Image de l'application (ADR 005, « Image ») : deux étapes.
#
# 1. Construction : uv 0.12.19 (celui de la CI), Python géré par uv (python-build-standalone,
#    empreinte SHA-256 vérifiée par uv), dépendances de uv.lock seules, sans le groupe dev,
#    sans rien construire depuis des sources.
# 2. Exécution : distroless cc (glibc, libstdc++, certificats), sans shell ni gestionnaire
#    de paquets ; utilisateur non root 65532 ; code et Python appartenant à root, en lecture
#    seule pour le processus ; système de fichiers racine en lecture seule dans le pod.
#
# Bases figées par empreinte (index multi-architecture : amd64 et arm64), mises à jour à la
# main, délibérément. Pas de directive « syntax » : elle tirerait un frontal non figé.
#
#   docker build --build-arg CDG_COMMIT="$(uv run --no-sync python scripts/chaine.py revision)" --tag cdg:verification .
#   uv run pytest -m image --image cdg:verification

ARG PYTHON_VERSION=3.12.14

FROM ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:c40e42de0e1516439b5139d7a214657dbffbbd3d2366661347efae7b8337c197 AS construction
ARG PYTHON_VERSION
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PYTHON=${PYTHON_VERSION}

# Python, puis retrait de ce qui ne sert qu'à développer ou à installer : pip, ensurepip,
# IDLE, Tk, 2to3, la suite de tests de CPython, les en-têtes C et les scripts associés
RUN uv python install --no-bin "${PYTHON_VERSION}" \
 && cd /python/cpython-"${PYTHON_VERSION}"-linux-* \
 && rm -rf include share \
    lib/python3.12/ensurepip lib/python3.12/idlelib lib/python3.12/lib2to3 \
    lib/python3.12/site-packages/pip lib/python3.12/site-packages/pip-* \
    lib/python3.12/test lib/python3.12/tkinter lib/python3.12/turtledemo \
    lib/python3.12/lib-dynload/_tkinter.* lib/tcl* lib/tk* lib/itcl* lib/thread* \
    bin/2to3* bin/idle3* bin/pip* bin/pydoc3* bin/python3*-config

WORKDIR /app

# dépendances d'abord (couche réutilisée tant que uv.lock ne change pas) ; le projet n'est
# pas installé : son code est lu depuis /app/src (PYTHONPATH), sans construire de paquet ;
# aucun groupe, ni dev ni mcp (le serveur MCP, local, n'existe pas dans le cluster)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-default-groups --no-build --no-install-project

COPY LICENSE /app/LICENSE
# licences tierces absentes des roues et de Python (licences/PROVENANCE.md)
COPY licences /app/licences
COPY config /app/config
COPY data /app/data
COPY migrations /app/migrations
COPY src /app/src
# bytecode du projet, validé par empreinte du source et non par date de modification
RUN /app/.venv/bin/python -m compileall -q --invalidation-mode checked-hash /app/src

FROM gcr.io/distroless/cc-debian13:nonroot@sha256:54df941ed0d06a1bd95ef5e0ce391fd8d9f94b64782dc9a60062727849ee3f97

# la source relie le paquet ghcr.io au dépôt ; la provenance attestée relie l'image publiée
# à son commit
LABEL org.opencontainers.image.source="https://github.com/sifir-gun/contract-decision-graph" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.title="contract-decision-graph" \
      org.opencontainers.image.description="Verdict go / no-go auditable sur des contrats fournisseurs"

# propriétaire root : le processus (65532) lit le code, ne peut pas le modifier
COPY --from=construction /python /python
COPY --from=construction /app /app

# commit de la construction, scellé avec chaque décision (PR D2, ADR 005) : fourni par
# `scripts/chaine.py revision`, en CI comme sur le poste, jamais deviné ; « inconnu » si le
# contexte diffère du commit ou sans argument. Après les copies : seule la configuration de
# l'image en dépend. L'empreinte de l'image est fournie au lancement, par le chart.
ARG CDG_COMMIT=inconnu

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CDG_JOURNAUX=json \
    HF_HUB_OFFLINE=1 \
    ORT_DISABLE_TELEMETRY=1 \
    LANGSMITH_TRACING=false \
    CDG_COMMIT=${CDG_COMMIT}

USER 65532:65532
WORKDIR /app

# forme exec : Python est le processus 1 et reçoit SIGTERM (arrêt propre d'uvicorn) ;
# aucun sous-processus à récolter, donc pas d'init
ENTRYPOINT ["python", "-m", "cdg.cli"]
CMD ["web"]
