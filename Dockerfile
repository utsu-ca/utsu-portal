# Portal image: runs on Cloud Run, Azure Container Apps, Fly, Kubernetes, or plain Docker. Pick the database at run time
# with PORTAL_STORE (see wiki/Deployment.md); drivers for all supported backends are installed.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
ARG PORTAL_EXTRA_DEPS=""
RUN pip install --no-cache-dir ".[portal,firestore,sql${PORTAL_EXTRA_DEPS}]" "psycopg[binary]>=3" pymysql
RUN useradd --system --no-create-home --uid 10001 app
USER 10001
ENV PORT=8080 PORTAL_APP=utsu_core:create_app
# PORTAL_APP=utsu_core.authsvc:create_auth_app runs the standalone authentication service from the same image
CMD ["sh", "-c", "exec uvicorn --factory ${PORTAL_APP} --host 0.0.0.0 --port ${PORT} --no-server-header --proxy-headers --forwarded-allow-ips='*'"]
# The Svelte front end (web/) is built into src/utsu_core/static and committed, so this image needs no Node. Rebuild with `npm run build` in web/ after changing it.
