#!/usr/bin/env bash
# deploy-gateway.sh - Install or upgrade the farmer app APISIX routes (chart apisix/k8s) on the oan_dev k3s node
# Same as oan_a2c's ci/deploy-gateway.sh.
#
# The routes are generated from the declared endpoints (apisix/generate_apisix_config.py) and committed, so
# this installs what is in git. Needs a kubeconfig whose identity may deploy to FARMER_APP_NAMESPACE
# (oan-helm chart oan-cluster-access), and helm and kubectl. APISIX and its Ingress Controller are a separate
# release (namespace GATEWAY_APISIX_NAMESPACE); this chart only holds ApisixRoute/ApisixUpstream objects.
# Manage the gateway with this chart or with `adc sync`, never both (apisix/README.md).
#
# Optional overrides: FARMER_APP_NAMESPACE, GATEWAY_RELEASE, GATEWAY_APISIX_NAMESPACE, GATEWAY_CHART, GATEWAY_VALUES.
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG is required}"

NS="${FARMER_APP_NAMESPACE:-farmer-app}"
RELEASE="${GATEWAY_RELEASE:-farmer-app-gateway}"
APISIX_NS="${GATEWAY_APISIX_NAMESPACE:-api6-gateway}"
# The archive "Package gateway chart" built for this commit (unstashed into built-gateway-chart/), else the chart folder.
PACKAGED=""
if [ -n "${IMMUTABLE_TAG:-}" ] && [ -f "built-gateway-chart/oan-farmer-app-gateway-${IMMUTABLE_TAG}.tgz" ]; then
  PACKAGED="built-gateway-chart/oan-farmer-app-gateway-${IMMUTABLE_TAG}.tgz"
elif [ -d "built-gateway-chart" ]; then
  PACKAGED="$(ls -v built-gateway-chart/oan-farmer-app-gateway-*.tgz 2>/dev/null | tail -n 1 || true)"
fi
CHART="${GATEWAY_CHART:-${PACKAGED:-apisix/k8s}}"
VALUES="${GATEWAY_VALUES:-helm/environments/dev/oan-farmer-app-gateway.yaml}"

# The committed routes must match the declared endpoints. Needs the bench's python; skipped otherwise.
if python3 -c 'import oan_auth_service, oan_farmer_app_service' 2>/dev/null; then
  PYTHONPATH=. python3 apisix/generate_apisix_config.py
  git diff --exit-code -- apisix/ \
    || { echo "apisix/ is out of date with the declared routes: run the generator and commit" >&2; exit 1; }
else
  echo "=== oan_auth_service not importable: skipping the generator drift check ==="
fi

echo "=== Gateway ${RELEASE}: namespace ${NS}, APISIX in ${APISIX_NS} ==="

# The rate limiter's Redis ships with the APISIX release; find its Service by labels.
REDIS_SVC="$(kubectl -n "${APISIX_NS}" get svc \
  -l app.kubernetes.io/name=redis,app.kubernetes.io/component=master -o jsonpath='{.items[*].metadata.name}')"
case "${REDIS_SVC}" in
  ''|*' '*) echo "expected exactly one Redis master Service in ${APISIX_NS}, found: '${REDIS_SVC}'" >&2; exit 1 ;;
esac
REDIS_HOST="${REDIS_SVC}.${APISIX_NS}.svc.cluster.local"

helm lint "${CHART}" -f "${VALUES}" --set redis.host="${REDIS_HOST}"

if ! helm upgrade --install "${RELEASE}" "${CHART}" --namespace "${NS}" -f "${VALUES}" \
  --set redis.host="${REDIS_HOST}" --wait --timeout 5m; then
  echo "=== helm upgrade failed ==="
  helm -n "${NS}" history "${RELEASE}" --max 3 || true
  exit 1
fi

helm -n "${NS}" status "${RELEASE}"
kubectl -n "${NS}" get apisixroute,apisixupstream

echo "=== gateway deploy complete: ${RELEASE} ==="
