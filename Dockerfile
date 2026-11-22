# Portal image: runs on Cloud Run, Azure Container Apps, Fly, Kubernetes, or plain Docker. Pick the database at run time
# with PORTAL_STORE (see wiki/Deployment.md); drivers for all supported backends are installed.
#
# Build it with scripts/build.sh, which reads config/portal.yaml and passes PORTAL_PROGRAMS and the frontend
# UI_COMPOSITION and UI_PROGRAMS below. Core is always installed; programs are installed only when listed.

# Stage 1: build the frontends. The core-only bundle ships inside the core wheel; the bundle that is served is built
# for exactly the selected programs.
FROM node:22-slim AS frontend
WORKDIR /repo
COPY src/core/frontend/package.json src/core/frontend/package-lock.json /repo/src/core/frontend/
RUN npm --prefix src/core/frontend ci
COPY src ./src
COPY data ./data
# Named so that they are not exported to Vite, which reads PORTAL_FRONTEND_COMPOSITION and PORTAL_FRONTEND_PROGRAMS.
ARG UI_COMPOSITION=full
ARG UI_PROGRAMS=
RUN npm --prefix src/core/frontend run build:core \
 && case "${UI_COMPOSITION}" in \
      core-only) cp -r src/core/frontend/.core-dist /repo/served ;; \
      full) npm --prefix src/core/frontend run build && cp -r src/core/static /repo/served ;; \
      selected) PORTAL_FRONTEND_PROGRAMS="${UI_PROGRAMS}" npm --prefix src/core/frontend run build:subset \
                && cp -r src/core/frontend/.subset-dist /repo/served ;; \
      *) echo "unknown UI_COMPOSITION=${UI_COMPOSITION}" >&2; exit 1 ;; \
    esac

# Stage 2: build the distributions and install core plus the selected programs.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends rclone && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tools ./tools
COPY --from=frontend /repo/src/core/frontend/.core-dist /tmp/core-dist
COPY --from=frontend /repo/served /opt/portal-static
ARG PORTAL_EXTRA_DEPS=""
ARG PORTAL_VERSION=0.2.0
ARG PORTAL_PROGRAMS="agm,sabp,clubs,senate,chat,voting"
# Extras belong to utsu-core, so they are requested from it directly. Branding is a separate distribution that is
# always installed; the theme actually shown comes from PORTAL_BRANDING_DIR when one is mounted.
RUN pip install --no-cache-dir setuptools wheel \
 && python tools/build_split_distributions.py --version "${PORTAL_VERSION}" --out-dir /tmp/wheels --core-static-dir /tmp/core-dist \
 && programs="$(echo "${PORTAL_PROGRAMS}" | tr ',' '\n' | grep -v '^$' | sed "s/.*/utsu-program-&==${PORTAL_VERSION}/" | tr '\n' ' ')" \
 && pip install --no-cache-dir --find-links /tmp/wheels \
      "utsu-core[portal,firestore,sql${PORTAL_EXTRA_DEPS}]==${PORTAL_VERSION}" "utsu-branding==${PORTAL_VERSION}" \
      ${programs} "psycopg[binary]>=3" pymysql \
 && rm -rf /tmp/wheels /tmp/core-dist /app/src /app/tools
RUN useradd --system --no-create-home --uid 10001 app
RUN mkdir -p /app/receipt-files && chown app /app/receipt-files
USER 10001
LABEL ca.utsu.portal.programs="${PORTAL_PROGRAMS}"
ENV PORT=8080 PORTAL_APP=core:create_app PORTAL_STATIC_DIR=/opt/portal-static
# PORTAL_APP=core.authsvc:create_auth_app runs the standalone authentication service from the same image
CMD ["sh", "-c", "exec uvicorn --factory ${PORTAL_APP} --host 0.0.0.0 --port ${PORT} --no-server-header --proxy-headers --forwarded-allow-ips='*'"]
