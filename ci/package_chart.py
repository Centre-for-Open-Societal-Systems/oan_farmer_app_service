"""Package helm/oan-farmer-app-service with this build's image, promote it to staging and publish it to oan-helm.

Run by the Jenkins stages of every develop and staging build. The steps are oan-helm's scripts/ci/app_chart.py;
this file only holds the constants of this repository and loads that file at OAN_HELM_REF:

    package_chart.py version   the chart version, also the image tag: <version>-develop.<BUILD_NUMBER> on develop,
                               <init.yaml version>-staging.<BUILD_NUMBER> on staging
    package_chart.py package   develop: writes built-chart/oan-grievance-ui-<version>.tgz with the image pinned by digest
    package_chart.py promote   staging: the develop chart of this source tree, its image also tagged for staging
    package_chart.py publish   adds built-chart/ to repository/<branch>/ of oan-helm
    package_chart.py gateway-package, gateway-publish   the same for the routes chart apisix/k8s (develop only)

See the docstring of app_chart.py for the environment each action needs.
"""

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OAN_HELM = "https://github.com/Centre-for-Open-Societal-Systems/oan-helm.git"
OAN_HELM_REF = "9e65eb8d78801c14d023355cdfc0cae730267423"

CONFIG = {
    "chart": "oan-farmer-app-service",
    "source_repository": "Centre-for-Open-Societal-Systems/oan_farmer_app_service",
    "release_name": "farmer-app",
    "release_namespace": "farmer-app",
    "values_overlay": "helm/environments/dev/oan-farmer-app-service.yaml",
    "image_path": "frappe.image",
    "version": {"file": "oan_farmer_app_service/__init__.py", "pattern": r'(?m)^__version__ = "(\d+\.\d+\.\d+)"$'},
    "promote": {
        "release_name": "farmer-app",
        "release_namespace": "farmer-app",
        "values_overlay": "helm/environments/staging/oan-farmer-app-service.yaml",
    },
    "gateway": {
        "chart": "oan-farmer-app-gateway",
        "source": "apisix/k8s",
        "release_name": "farmer-app-gateway",
        "values_overlay": "helm/environments/dev/oan-farmer-app-gateway.yaml",
    },
}


def load_app_chart(work):
    """scripts/ci of oan-helm at OAN_HELM_REF, without its repository/ archives."""
    target = work / "oan-helm"
    for command in (
        ["git", "clone", "--quiet", "--filter=blob:none", "--sparse", "--no-checkout", OAN_HELM, str(target)],
        ["git", "sparse-checkout", "set", "scripts/ci"],
        ["git", "checkout", "--quiet", OAN_HELM_REF],
    ):
        subprocess.run(command, cwd=str(target if command[1] != "clone" else ROOT), check=True)
    spec = importlib.util.spec_from_file_location("oan_app_chart", target / "scripts/ci/app_chart.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    work = Path(tempfile.mkdtemp(prefix="oan-helm-"))
    try:
        sys.exit(load_app_chart(work).main(CONFIG, ROOT))
    finally:
        shutil.rmtree(str(work), ignore_errors=True)
