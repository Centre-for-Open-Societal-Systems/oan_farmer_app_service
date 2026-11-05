#!/usr/bin/env python3
"""
generate_apisix_config.py

Generates the ADC gateway configuration (adc.yaml) and the APISIX Ingress Controller
resources (k8s/templates, the oan-farmer-app-gateway Helm chart) for the farmer app's
mobile API. Shared logic lives in oan_auth_service/gateway_apisix.py.

Unlike the other OAN apps there is no OpenAPI spec to read: the routes are the ones
declared with `router.mobile_route`, read back from `oan_auth_service.api.router.declared_routes`. The in-memory spec built
from them is the input the shared helpers expect.

Usage:
    python3 apisix/generate_apisix_config.py
"""

import os
import re
from pathlib import Path

from oan_auth_service import gateway_apisix as gw

os.environ.setdefault("APISIX_REDIS_HOST", "redis")
os.environ.setdefault("APISIX_REDIS_PORT", "6379")
os.environ.setdefault("CORS_ORIGINS_REGEX", r"https?://localhost(:[0-9]+)?")

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SCRIPT_DIR / "adc.yaml"
K8S_DIR = SCRIPT_DIR / "k8s" / "templates"
APP = "oan-farmer-app"
SITE_NAME = "${FRAPPE_SITE_NAME}"
# False: Frappe's auth_hooks middleware verifies bearer tokens, and it also accepts
# OpenG2P IAM tokens, which a gateway jwt-auth check would refuse.
GATEWAY_JWT = False

API_UPSTREAM = {
	"scheme": "http",
	"nodes": [{"host": "${OAN_FARMER_APP_UPSTREAM_HOST}", "port": 8000, "weight": 1}],
	# Frappe picks the site from the Host header, so send the site name, not the gateway's.
	"pass_host": "rewrite",
	"upstream_host": SITE_NAME,
	"timeout": {"connect": 5, "send": 20, "read": 20},
	"retries": 2,
}

# Attachments and exports go to the grievance backend and not through Frappe, so a slow
# upload never holds a gunicorn worker. That backend verifies the token itself.
GRIEVANCE_UPSTREAM = {
	"scheme": "https",
	"nodes": [{"host": "${OAN_GRIEVANCE_UPSTREAM_HOST}", "port": 443, "weight": 1}],
	"pass_host": "rewrite",
	"upstream_host": "${OAN_GRIEVANCE_UPSTREAM_HOST}",
	"timeout": {"connect": 3, "send": 120, "read": 120},
	"retries": 0,
}

TIERS = {
	"farmer-public": {
		"limit_by": "ip",
		"minute": 60,
		"hour": 600,
		"policy": "redis",
		"fail_open": False,
		"note": "Routes that take no token: the IAM token exchange and the guest reference data. Per-IP, generous because rural carriers put many farmers behind one address.",
	},
	"farmer-read": {
		"limit_by": "ip",
		"minute": 240,
		"hour": 6000,
		"policy": "redis",
		"note": "Farmer reads.",
	},
	"farmer-write": {
		"limit_by": "ip",
		"minute": 120,
		"hour": 2000,
		"policy": "redis",
		"note": "Farmer writes: applications, grievances, consent.",
	},
	"farmer-files": {
		"limit_by": "ip",
		"minute": 60,
		"hour": 600,
		"policy": "redis",
		"note": "Attachment upload, download and export, served by the grievance backend.",
	},
}

# What sits in front of the version on the grievance host. `/api` when the host is the Frappe
# backend itself, as it is today; empty if it is reached through the backend's own gateway.
# Read with `os.environ.get`, since `gw.env` refuses an empty value.
GRIEVANCE_API_PREFIX = os.environ.get("GRIEVANCE_API_PREFIX", "/api").rstrip("/")

# Attachment and export paths, as `(methods, public path, path on the grievance backend)`.
# `:version` is the backend version the app asked for, passed on as it is.
FILE_ROUTES = [
	(("GET", "POST"), "/grievance/:version/grievances/:ticket/attachments", r"/$1/grievances/$2/attachments"),
	(("GET",), "/grievance/:version/attachments/:id/view", r"/$1/attachments/$2/view"),
	(("GET",), "/grievance/:version/attachments/:id/download", r"/$1/attachments/$2/download"),
	(("DELETE",), "/grievance/:version/attachments/:id", r"/$1/attachments/$2"),
	(("GET",), "/grievance/:version/grievances/:ticket/export", r"/$1/grievances/$2/export"),
]


def declared_routes() -> list[dict]:
	"""`(method, public path)` and auth for every route the app declares."""
	from oan_auth_service.api.router import declared_routes as declared

	# Importing the endpoint modules is what declares the routes.
	from oan_farmer_app_service.api.v1 import a2c, auth, consent, grievance  # noqa: F401

	routes = []
	for item in declared("oan_farmer_app_service"):
		public = item["auth"] == "public"
		path = gw.public_path(item["path"])
		for method in item["methods"]:
			if public:
				tier = "farmer-public"
			else:
				tier = "farmer-read" if method == "GET" else "farmer-write"
			routes.append(
				{
					"method": method,
					"path": re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path),
					"auth": "public" if public else "bearer",
					"domain": "d00",
					"operation_id": None,
					"tier": tier,
				}
			)
	return routes


def _path_regex(path: str) -> str:
	"""`/grievance/:version/attachments/:id/view` as `^/grievance/(v[0-9]+)/attachments/([^/]+)/view$`."""
	path = path.replace(":version", "(v[0-9]+)")
	return "^" + re.sub(r":\w+", "([^/]+)", path) + "$"


def files_service() -> dict:
	"""The grievance backend's heavy routes, one ADC route per rewrite."""
	routes = []
	for methods, path, target in FILE_ROUTES:
		routes.append(
			{
				"name": f"{APP}-files-{path.rsplit('/', 1)[-1]}-{'-'.join(m.lower() for m in methods)}",
				"description": TIERS["farmer-files"]["note"],
				"labels": {"app": APP, "tier": "farmer-files", "auth": "bearer"},
				"uris": [path],
				"methods": list(methods),
				"plugins": {
					"limit-count": {
						**gw.limit_count(TIERS["farmer-files"], GATEWAY_JWT, "bearer"),
						"group": f"{APP}:farmer-files",
					},
					"proxy-rewrite": {"regex_uri": [_path_regex(path), GRIEVANCE_API_PREFIX + target]},
					"client-control": {"max_body_size": 25 * 1024 * 1024},
				},
			}
		)
	return {
		"name": f"{APP}-files-v1",
		"labels": {"app": APP},
		"upstream": GRIEVANCE_UPSTREAM,
		"routes": routes,
	}


def add_file_preflight(doc: dict) -> None:
	"""Let a browser's CORS preflight reach the service's cors plugin on the file paths too.

	The shared builder lists a preflight root for every declared route, and `FILE_ROUTES`
	are not among them, so their first segments are added here.
	"""
	roots = {"/" + path.split("/")[1] for _, path, _ in FILE_ROUTES}
	for route in doc["services"][0]["routes"]:
		if route["name"] == f"{APP}-cors-preflight":
			route["uris"] = sorted(set(route["uris"]) | roots | {root + "/*" for root in roots})


HELM_VALUES = {
	"OAN_FARMER_APP_UPSTREAM_HOST": ".Values.web.serviceName",
	"OAN_GRIEVANCE_UPSTREAM_HOST": ".Values.grievance.host",
	"FRAPPE_SITE_NAME": ".Values.siteName",
	"WEB_PORT": ".Values.web.port",
	"GRIEVANCE_PORT": ".Values.grievance.port",
}
HELM_REQUIRED = {
	"OAN_FARMER_APP_UPSTREAM_HOST",
	"OAN_GRIEVANCE_UPSTREAM_HOST",
	"FRAPPE_SITE_NAME",
	"APISIX_REDIS_HOST",
}
HELM_BACKENDS = {
	f"{APP}-service-v1": ("${OAN_FARMER_APP_UPSTREAM_HOST}", "${WEB_PORT}"),
	f"{APP}-files-v1": ("${OAN_GRIEVANCE_UPSTREAM_HOST}", "${GRIEVANCE_PORT}"),
}


def keep_public_path(doc: dict) -> None:
	"""Rewrite any public path by putting `/api` in front of it.

	The shared builder assumes a path starts at its version (`/v1/...`). Here the app picks
	the backend version, so the version follows a service prefix (`/a2c/v1/...`).
	"""
	doc["services"][0]["plugins"]["proxy-rewrite"] = {"regex_uri": ["^/(.*)", "/api/$1"]}


def main():
	# oan_auth_service's own endpoints are served by this bench too. `with_auth_routes`
	# refuses a (method, path) that both declare, so a route here cannot shadow the library.
	routes = gw.with_auth_routes(declared_routes())
	doc = gw.build_document(
		routes,
		app=APP,
		tiers={**TIERS, **gw.AUTH_TIERS},
		api_upstream=API_UPSTREAM,
		service_plugins=gw.service_plugins(
			methods="GET,POST,PUT,PATCH,DELETE,OPTIONS",
			credentials=False,
			max_body_mb=1,
			cors_origins=gw.env("CORS_ORIGINS_REGEX", r"https?://localhost(:[0-9]+)?"),
		),
		extra_services=[files_service()],
		gateway_jwt=GATEWAY_JWT,
	)
	add_file_preflight(doc)
	keep_public_path(doc)
	provenance = [
		"Generated by generate_apisix_config.py -- do not hand-edit;",
		"change an endpoint in oan_farmer_app_service/api/v1 or TIERS and re-run.",
	]
	gw.write_document(doc, OUTPUT_PATH, ["Farmer app ADC config (adc lint|validate|diff|sync)", *provenance])
	gw.write_helm_templates(
		doc,
		K8S_DIR,
		["Farmer app APISIX Ingress Controller resources", *provenance],
		app=APP,
		backends=HELM_BACKENDS,
		values=HELM_VALUES,
		required=HELM_REQUIRED,
		ints={"WEB_PORT", "GRIEVANCE_PORT"},
	)
	print(f"Wrote {OUTPUT_PATH.name}: {len(routes)} operations")


if __name__ == "__main__":
	gw.cli(main)
