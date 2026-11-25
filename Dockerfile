# syntax=docker/dockerfile:1
# Production image for the `oan_farmer_app_service` Frappe app, adapted from frappe_docker's
# images/layered/Containerfile.
#
# Build context = this repository checkout. It must contain .git: bench installs the app
# by cloning it from the context. Apps oan_farmer_app_service depends on (`required_apps` in
# hooks.py) are fetched from Git via EXTRA_APPS_JSON.
#
# One image runs every Frappe role (gunicorn, websocket, worker, scheduler, nginx); the
# Helm chart helm/oan-farmer-app-service picks the command.
ARG FRAPPE_BRANCH=version-16

FROM frappe/build:${FRAPPE_BRANCH} AS builder

ARG FRAPPE_BRANCH=version-16
ARG FRAPPE_PATH=https://github.com/frappe/frappe
# JSON list of {"url": ..., "branch": ...} installed before the app itself.
ARG EXTRA_APPS_JSON='[{"url": "https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service", "branch": "develop"}]'
# Commit(s) the EXTRA_APPS_JSON branches point at, resolved by CI. Only used to invalidate
# the cached bench layer: without it a new oan_auth_service commit is never picked up.
ARG APPS_REVISION=""
ARG APP_NAME=oan_farmer_app_service

USER frappe
WORKDIR /home/frappe

RUN echo "Extra apps revision: ${APPS_REVISION:-unset}" && \
  echo "${EXTRA_APPS_JSON}" > /tmp/apps.json && \
  bench init \
    --apps_path=/tmp/apps.json \
    --frappe-branch=${FRAPPE_BRANCH} \
    --frappe-path=${FRAPPE_PATH} \
    --no-procfile \
    --no-backups \
    --skip-redis-config-generation \
    --verbose \
    /home/frappe/frappe-bench

COPY --chown=frappe:frappe . /tmp/${APP_NAME}

# The permission and asset changes happen here, not in the final stage: there each one
# would add another full copy of the bench (~1.1 GB) as a new layer.
RUN cd /home/frappe/frappe-bench && \
  bench get-app /tmp/${APP_NAME} && \
  echo "{}" > sites/common_site_config.json && \
  for app in apps/*; do echo "${app#apps/} $(git -C "$app" rev-parse HEAD)"; done > apps-versions.txt && \
  find apps -mindepth 1 -path "*/.git" | xargs rm -fr && \
  mv sites/assets assets && \
  chmod -R g=u /home/frappe/frappe-bench

FROM frappe/base:${FRAPPE_BRANCH}

USER frappe

# Group 0 with g=u (set in the builder) keeps the bench writable for any UID in group 0.
COPY --from=builder --chown=frappe:0 /home/frappe/frappe-bench /home/frappe/frappe-bench

WORKDIR /home/frappe/frappe-bench

USER root
# Assets live in the image; the entrypoint links them into the mounted sites volume.
COPY --chmod=755 <<'EOF' /usr/local/bin/entrypoint.sh
#!/bin/bash
set -e
umask 0002
rm -rf /home/frappe/frappe-bench/sites/assets
ln -s /home/frappe/frappe-bench/assets /home/frappe/frappe-bench/sites/assets
exec "$@"
EOF

COPY --chmod=755 <<'EOF' /usr/local/bin/start.sh
#!/bin/bash
set -e
exec /home/frappe/frappe-bench/env/bin/gunicorn \
  --chdir=/home/frappe/frappe-bench/sites \
  --bind=0.0.0.0:8000 \
  --threads="${GUNICORN_THREADS:-4}" \
  --workers="${GUNICORN_WORKERS:-2}" \
  --worker-class=gthread \
  --worker-tmp-dir=/dev/shm \
  --timeout="${GUNICORN_TIMEOUT:-120}" \
  --preload \
  frappe.app:application
EOF

USER frappe
VOLUME ["/home/frappe/frappe-bench/sites", "/home/frappe/frappe-bench/logs"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["start.sh"]
