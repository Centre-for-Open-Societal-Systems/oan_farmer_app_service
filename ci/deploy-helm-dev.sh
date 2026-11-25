#!/usr/bin/env bash
# deploy-helm-dev.sh - Install or upgrade the `farmer-app` Helm release on the oan_dev k3s node, or on the QA cluster
# Called by Jenkins after "Package chart": for develop on oan_dev, for staging (DEPLOY_ENV=staging) on QA.
# Same as oan_a2c's ci/deploy-helm-dev.sh.
#
# Runs on the deploy agent with the kubeconfig Jenkins holds as `oan-dev-kubeconfig` or `oan-qa-kubeconfig` (oan-helm
# chart oan-cluster-access, which must list this namespace); the agent needs helm and kubectl, no AWS access:
# the node pulls from ECR with its own IAM role. `helm upgrade --install` makes the first install and
# every upgrade. The chart generates the admin and JWT Secrets once and keeps them across upgrades.
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${IMMUTABLE_TAG:?IMMUTABLE_TAG is required (<__version__>-develop.<build> or <init.yaml version>-staging.<build>)}"

# DEPLOY_ENV picks the values: dev (develop) or staging (the staging branch, a promoted chart).
DEPLOY_ENV="${DEPLOY_ENV:-dev}"

NS="${FARMER_APP_NAMESPACE:-farmer-app}"
RELEASE="${FARMER_APP_RELEASE:-farmer-app}"
CHART_TGZ="built-chart/oan-farmer-app-service-${IMMUTABLE_TAG}.tgz"
VALUES="helm/environments/${DEPLOY_ENV}/oan-farmer-app-service.yaml"

test -f "${CHART_TGZ}" || { echo "${CHART_TGZ} is missing: run package_chart.py package first" >&2; exit 1; }

echo "=== Deploying to ${DEPLOY_ENV}: release ${RELEASE}, namespace ${NS}, image tag ${IMMUTABLE_TAG} ==="

# The postgres password the create-site job uses, copied from the frappe-common release.
kubectl -n "${NS}" create secret generic oan-frappe-common-db \
  --from-literal=password="$(kubectl -n frappe-common get secret oan-frappe-common-db -o jsonpath='{.data.password}' | base64 -d)" \
  --dry-run=client -o yaml | kubectl apply -f -

# No --atomic: a failed first install would uninstall the release, generated Secrets included.
if ! helm upgrade --install "${RELEASE}" "${CHART_TGZ}" --namespace "${NS}" -f "${VALUES}" \
  --set frappe.image.tag="${IMMUTABLE_TAG}" \
  --wait --wait-for-jobs --timeout 30m; then
  echo "=== helm upgrade failed ==="
  helm -n "${NS}" history "${RELEASE}" --max 3 || true
  kubectl -n "${NS}" get jobs,pods || true
  exit 1
fi

helm -n "${NS}" status "${RELEASE}"
kubectl -n "${NS}" get pods

echo "=== ${DEPLOY_ENV} deploy complete: ${RELEASE} ${IMMUTABLE_TAG} ==="
