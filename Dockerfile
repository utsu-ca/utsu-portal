# Portal image: runs on Cloud Run, Azure Container Apps, Fly, Kubernetes, or plain Docker. Pick the database at run time
# with PORTAL_STORE (see wiki/Deployment.md); drivers for all supported backends are installed.
# Stage 1: build the core-only frontend bundle that the core wheel ships. The full UI is the committed
# src/core/utsu_core/static bundle and ships in the utsu-portal-frontend wheel.
FROM node:22-slim AS core-frontend
WORKDIR /build
COPY web ./web
COPY src ./src
COPY data ./data
RUN npm --prefix web ci && npm --prefix web run build:core

# Stage 2: build the split distributions and install the utsu-portal aggregate.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends rclone && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tools ./tools
COPY --from=core-frontend /build/web/.core-dist /tmp/core-dist
ARG PORTAL_EXTRA_DEPS=""
ARG PORTAL_VERSION=0.2.0
# The standard wheel is core-only; the aggregate installs core, every program, branding and the full UI. Extras
# belong to utsu-core, so they are requested from it directly.
RUN pip install --no-cache-dir setuptools wheel packaging \
 && python tools/build_split_distributions.py --version "${PORTAL_VERSION}" --out-dir /tmp/wheels --core-static-dir /tmp/core-dist \
 && pip install --no-cache-dir --find-links /tmp/wheels "utsu-portal==${PORTAL_VERSION}" \
      "utsu-core[portal,firestore,sql${PORTAL_EXTRA_DEPS}]==${PORTAL_VERSION}" "psycopg[binary]>=3" pymysql \
 && rm -rf /tmp/wheels /tmp/core-dist /app/src /app/tools
RUN useradd --system --no-create-home --uid 10001 app
RUN mkdir -p /app/receipt-files && chown app /app/receipt-files
USER 10001
ENV PORT=8080 PORTAL_APP=utsu_core:create_app
# PORTAL_APP=utsu_core.authsvc:create_auth_app runs the standalone authentication service from the same image
CMD ["sh", "-c", "exec uvicorn --factory ${PORTAL_APP} --host 0.0.0.0 --port ${PORT} --no-server-header --proxy-headers --forwarded-allow-ips='*'"]
# The full Svelte front end (web/) is committed in src/core/utsu_core/static; only the core-only bundle is built here.
# Rebuild the committed bundle with `npm run build` in web/ after changing the UI.
