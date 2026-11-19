// Farmer app (mobile BFF) - build, push and deploy to the oan_dev k3s node.
//
//  develop -> image eoan/oan-farmer-app-service/farmer-app-service:<__version__>-develop.<build>
//             + ci/deploy-helm-dev.sh (release `farmer-app`, namespace `farmer-app`)
//             + ci/deploy-gateway.sh (APISIX routes, chart apisix/k8s, only when they changed)
//  other branches: checks only (Lint is the GitHub workflow's job; nothing to build here).
pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timeout(time: 60, unit: 'MINUTES')
    }

    environment {
        AWS_REGION      = 'ap-south-1'
        IMAGE_REGISTRY  = '379220350808.dkr.ecr.ap-south-1.amazonaws.com'
        ECR_REPO        = 'eoan/oan-farmer-app-service/farmer-app-service'
        // oan_farmer_app_service's required_apps, installed into the bench before it. The branch is
        // resolved to a commit at build time so a new commit there rebuilds the bench layer.
        AUTH_APP_URL    = 'https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service'
        AUTH_APP_BRANCH = 'develop'
        FARMER_APP_NAMESPACE = 'farmer-app'
        // oan_dev's private address: APISIX binds its ports 80/443, Verify calls it from the VPN agent.
        OAN_DEV_EDGE_IP = '10.0.1.131'
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                script {
                    def version = sh(returnStdout: true,
                        script: "sed -n 's/^__version__ = \"\\(.*\\)\"/\\1/p' oan_farmer_app_service/__init__.py").trim()
                    env.IMMUTABLE_TAG = "${version}-develop.${env.BUILD_NUMBER}"
                    env.IMAGE_URI     = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}"
                    echo "Branch ${env.BRANCH_NAME} -> image ${env.IMAGE_URI}:${env.IMMUTABLE_TAG}"
                }
            }
        }

        stage('Build image') {
            when { branch 'develop' }
            steps {
                script {
                    env.AUTH_APP_REV = sh(returnStdout: true,
                        script: 'git ls-remote "$AUTH_APP_URL" "refs/heads/$AUTH_APP_BRANCH" | cut -f1').trim()
                    if (!env.AUTH_APP_REV) {
                        error("Cannot resolve ${env.AUTH_APP_URL} ${env.AUTH_APP_BRANCH}")
                    }
                }
                // The build context is this checkout, .git included (bench clones the app from it).
                sh '''
                    APPS_JSON=$(printf '[{"url": "%s", "branch": "%s"}]' "$AUTH_APP_URL" "$AUTH_APP_BRANCH")
                    docker build --pull \
                        -t "$IMAGE_URI:$IMMUTABLE_TAG" \
                        --build-arg "EXTRA_APPS_JSON=$APPS_JSON" \
                        --build-arg "APPS_REVISION=$AUTH_APP_REV" \
                        --label "org.opencontainers.image.revision=$(git rev-parse HEAD)" \
                        --label "org.opencontainers.image.source=https://github.com/Centre-for-Open-Societal-Systems/oan_farmer_app_service" \
                        --label "oan.auth-service.revision=$AUTH_APP_REV" \
                        .
                '''
            }
        }

        stage('Push image') {
            when { branch 'develop' }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ]]) {
                    sh '''#!/usr/bin/env bash
                        set -euo pipefail
                        aws ecr get-login-password --region "$AWS_REGION" \
                            | docker login --username AWS --password-stdin "$IMAGE_REGISTRY"

                        # ECR does not create a repository on push.
                        aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1 \
                            || aws ecr create-repository --region "$AWS_REGION" --repository-name "$ECR_REPO" >/dev/null

                        docker push "$IMAGE_URI:$IMMUTABLE_TAG"
                        docker rmi "$IMAGE_URI:$IMMUTABLE_TAG" || true
                        echo "Pushed $IMAGE_URI:$IMMUTABLE_TAG"
                    '''
                }
            }
        }

        // develop -> Helm release `farmer-app` on the oan_dev k3s node (chart helm/oan-farmer-app-service).
        // First run installs, later runs upgrade; the chart generates its admin and JWT Secrets and writes
        // the upstream URLs into the site config, so a new site needs nothing set by hand.
        stage('develop → oan_dev (helm)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl that reaches the oan_dev API
            options { timeout(time: 45, unit: 'MINUTES') }
            steps {
                checkout scm
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // develop -> APISIX routes (chart apisix/k8s) on the oan_dev node, after the app deploy.
        // Installs when the merged change touched the routes or the gateway values, or when the release is
        // missing. Not `changeset`: Jenkins compares with the previous build, so a branch's first build has an
        // empty changeset and skipped the install. The diff is against the first parent, as for a merge
        // commit; a shallow clone without it counts as changed (the install is idempotent).
        stage('develop → gateway (apisix)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh '''
                        changed=$(git diff --name-only HEAD^1 HEAD 2>/dev/null || echo apisix/)
                        if echo "$changed" | grep -qE '^(apisix/|oan_farmer_app_service/api/|helm/environments/dev/oan-farmer-app-gateway\\.yaml$)'; then
                            echo "=== gateway files changed: deploying ==="
                        elif ! helm -n "${FARMER_APP_NAMESPACE:-farmer-app}" status farmer-app-gateway >/dev/null 2>&1; then
                            echo "=== farmer-app-gateway is not installed: deploying ==="
                        else
                            echo "=== gateway unchanged and installed: skipping ==="
                            exit 0
                        fi
                        bash ci/deploy-gateway.sh
                    '''
                }
            }
        }

        // Through APISIX on oan_dev by its private address: public DNS may still point elsewhere.
        // Any answer but 404/5xx means APISIX routed it to Frappe (a token-less call answers 401).
        stage('Verify') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }
            steps {
                sh '''
                    host=farmer-app-service.development.oanstaging.com
                    url="https://$host/v1/auth/me"
                    for i in $(seq 1 10); do
                        code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 --resolve "$host:443:$OAN_DEV_EDGE_IP" "$url" || true)
                        echo "attempt $i: $url -> $code"
                        case "$code" in 2??|401|403) exit 0 ;; esac
                        sleep 6
                    done
                    echo "farmer-app did not answer at $url"
                    exit 1
                '''
            }
        }
    }
}
