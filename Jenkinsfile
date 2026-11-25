// Farmer app (mobile BFF) - build, push and deploy to the oan_dev k3s node; staging promotes it to the QA cluster.
//
//  develop -> image eoan/oan-farmer-app-service/farmer-app-service:<__version__>-develop.<build> (= chart version)
//             + ci/package_chart.py package (chart helm/oan-farmer-app-service with that version and image)
//             + ci/deploy-helm-dev.sh (release `farmer-app`, namespace `farmer-app`, from that archive)
//             + ci/deploy-gateway.sh (APISIX routes, chart apisix/k8s, only when they changed)
//             + ci/package_chart.py publish (the same archives to repository/develop/ of oan-helm)
//  staging -> ci/package_chart.py promote (develop's chart of this very source tree; its image also gets the
//             <init.yaml version>-staging.<build> tag, nothing is rebuilt)
//             + ci/deploy-helm-dev.sh with DEPLOY_ENV=staging (release `farmer-app`, namespace `farmer-app`, QA cluster
//               through `oan-qa-kubeconfig`) + ci/deploy-gateway.sh (the routes of apisix/k8s, values of staging)
//             + Verify (farmer-app-service.qa.oanstaging.com) + ci/package_chart.py publish (repository/staging/)
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
                    // develop: <__version__>-develop.<build>; staging: <init.yaml version>-staging.<build>.
                    if (env.BRANCH_NAME == 'develop' || env.BRANCH_NAME == 'staging') {
                        env.IMMUTABLE_TAG = sh(returnStdout: true, script: 'python3 ci/package_chart.py version').trim()
                    }
                    env.IMAGE_URI = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}"
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

        // Flow (develop): Checkout (version) -> Build image -> Push image -> Package chart -> deploy -> Publish chart.
        // helm/oan-farmer-app-service is zipped with this build's image before the deploy; the deploy installs
        // that archive, and the same archive is published to oan-helm afterwards.
        // staging: `promote` instead of `package`: develop's chart for this source tree, its image tagged for staging.
        stage('Package chart') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                script {
                    env.IMAGE_TAG = env.IMMUTABLE_TAG
                    env.SOURCE_COMMIT = sh(returnStdout: true, script: 'git rev-parse --verify HEAD').trim()
                    env.COMMIT_SUBJECT = sh(returnStdout: true, script: 'git log -1 --pretty=%s').trim()
                    env.IMAGE_REPOSITORY = env.IMAGE_URI
                }
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ]]) {
                    script {
                        if (env.BRANCH_NAME == 'develop') {
                            // The chart pins the image by digest, which the staging promotion needs to tag the same image.
                            env.IMAGE_DIGEST = sh(returnStdout: true, script: 'aws ecr describe-images --region "$AWS_REGION" --repository-name "$ECR_REPO" --image-ids imageTag="$IMMUTABLE_TAG" --query "imageDetails[0].imageDigest" --output text').trim()
                        } else {
                            // The workspace holds the staging branch only; promote needs the develop commits the chart index names.
                            withCredentials([gitUsernamePassword(credentialsId: scm.userRemoteConfigs[0].credentialsId, gitToolName: 'Default')]) {
                                sh 'git fetch --quiet --no-tags origin +refs/heads/develop:refs/remotes/origin/develop'
                            }
                        }
                    }
                    sh "python3 ci/package_chart.py ${env.BRANCH_NAME == 'staging' ? 'promote' : 'package'}"
                }
                // The deploy stages run on the VPN deploy agent, which gets the archive from here.
                stash name: 'built-chart', includes: 'built-chart/**'
            }
        }

        // The routes chart (apisix/k8s) is packaged and published like the app chart, but only for a build that
        // changed the routes or the gateway values: those are the builds that install it.
        stage('Package gateway chart') {
            when {
                beforeAgent true
                allOf {
                    branch 'develop'
                    anyOf {
                        changeset 'apisix/**'
                        changeset 'oan_farmer_app_service/api/**'
                        changeset 'helm/environments/dev/oan-farmer-app-gateway.yaml'
                    }
                }
            }
            steps {
                sh 'python3 ci/package_chart.py gateway-package'
                stash name: 'built-gateway-chart', includes: 'built-gateway-chart/**'
            }
        }

        // develop -> Helm release `farmer-app` on the oan_dev k3s node (the archive 'Package chart' built).
        // First run installs, later runs upgrade; the chart generates its admin and JWT Secrets and writes
        // the upstream URLs into the site config, so a new site needs nothing set by hand.
        stage('develop → oan_dev (helm)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl that reaches the oan_dev API
            options { timeout(time: 45, unit: 'MINUTES') }
            steps {
                checkout scm
                unstash 'built-chart'
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // staging -> Helm release `farmer-app` on the QA cluster from the promoted archive, then its APISIX routes.
        stage('staging → QA cluster (helm)') {
            when { beforeAgent true; branch 'staging' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl; must reach the QA API
            options { timeout(time: 45, unit: 'MINUTES') }
            steps {
                checkout scm
                unstash 'built-chart'
                withCredentials([file(credentialsId: 'oan-qa-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'DEPLOY_ENV=staging bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // The routes are the committed chart folder: develop already built and published its archive from this tree.
        stage('staging → QA cluster (apisix)') {
            when { beforeAgent true; branch 'staging' }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                withCredentials([file(credentialsId: 'oan-qa-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'GATEWAY_VALUES=helm/environments/staging/oan-farmer-app-gateway.yaml bash ci/deploy-gateway.sh'
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
                sh 'rm -rf built-gateway-chart'
                unstash 'built-gateway-chart'
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
        // staging: the QA names are public DNS, no --resolve needed.
        stage('Verify') {
            when { beforeAgent true; anyOf { branch 'develop'; branch 'staging' } }
            agent { label 'oan-ci-deploy' }
            steps {
                sh '''
                    host=farmer-app-service.development.oanstaging.com
                    resolve="--resolve $host:443:$OAN_DEV_EDGE_IP"
                    if [ "$BRANCH_NAME" = "staging" ]; then host=farmer-app-service.qa.oanstaging.com; resolve=""; fi
                    url="https://$host/v1/auth/me"
                    for i in $(seq 1 10); do
                        code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 $resolve "$url" || true)
                        echo "attempt $i: $url -> $code"
                        case "$code" in 2??|401|403) exit 0 ;; esac
                        sleep 6
                    done
                    echo "farmer-app did not answer at $url"
                    exit 1
                '''
            }
        }

        // Adds the archive built by 'Package chart' to repository/<branch>/ of oan-helm, only after
        // the deploy and Verify succeeded.
        stage('Publish chart') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                // UNSTABLE, not failed: the image is already deployed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py publish'
                    }
                }
            }
        }

        stage('Publish gateway chart') {
            when {
                beforeAgent true
                allOf {
                    branch 'develop'
                    anyOf {
                        changeset 'apisix/**'
                        changeset 'oan_farmer_app_service/api/**'
                        changeset 'helm/environments/dev/oan-farmer-app-gateway.yaml'
                    }
                }
            }
            steps {
                // UNSTABLE, not failed: the routes are already installed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py gateway-publish'
                    }
                }
            }
        }
    }
}
