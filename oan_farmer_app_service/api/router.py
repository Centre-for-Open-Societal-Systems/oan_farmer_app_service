"""Mobile routes on top of oan_auth_service's router, plus the checks every one of them shares.

The public contract is `/<service>/<version>/...` for a backend (`/a2c/v1/...`,
`/consent/v1/...`, `/grievance/v1/...`). The gateway owns no version of its own there: the
app names the backend version it wants in each path, and that version is forwarded
as it is. The service prefix keeps two backends from colliding on a path.

Frappe only dispatches `/api/*` to its URL map and the auth middleware only accepts
`/api/*` namespaces, so the routes live at `/api/<service>/<version>/...` and the
APISIX gateway rewrites one to the other (apisix/). The login helper stays at
`/v1/auth/...`, beside oan_auth_service's own routes, so a path there must not repeat
one of theirs (`gateway_apisix.with_auth_routes` refuses the clash).
"""

from collections.abc import Callable
from functools import wraps

import frappe
from frappe import _
from oan_auth_service.api.router import ensure_routes_registered as ensure_auth_routes
from oan_auth_service.api.router import make_route_registrar, prefixed
from oan_auth_service.api.utils import error_response, handle_api_errors, validate_request

from oan_farmer_app_service.api.upstream import SETTINGS_DOCTYPE
from oan_farmer_app_service.farmer_app.doctype.oan_farmer_app_settings.oan_farmer_app_settings import (
	version_tuple,
)

NAMESPACE = "/api"
# The backends the gateway proxies. Each owns `/api/<name>/` and the app picks its version.
SERVICES = ("a2c", "consent", "grievance")
# The login helper shares oan_auth_service's own namespace.
AUTH_NAMESPACE = "/api/v1"

route = prefixed(NAMESPACE)


def mobile_route(
	path: str,
	methods: tuple[str, ...] = ("GET",),
	auth: str | None = None,
	roles: tuple[str, ...] | None = None,
	status: int = 200,
	schema=None,
	summary: str | None = None,
) -> Callable:
	"""Declare a mobile route with the gateway's checks and error envelope around `fn`.

	This calls oan_auth_service's `rest` rather than replacing it. The `endpoint`
	inside `rest` stays the function on the URL rule and calls the layers built here.
	"""

	def decorator(fn: Callable) -> Callable:
		# Built inside out. A request runs `rest`'s endpoint, then `validate_request`
		# when there is a schema, then `handle_api_errors`, `_guarded` and `fn`.
		guarded = _guarded(fn)
		# The gateway has no version of its own; the app chooses the backend's. Otherwise
		# `handle_api_errors` guesses one from the handler's module path.
		guarded.api_version = "gateway"
		wrapped = handle_api_errors(guarded)
		if schema:
			wrapped = validate_request(schema)(wrapped)
		route(path, methods=methods, auth=auth, roles=roles, status=status, summary=summary)(wrapped)
		# Only the URL rule gets the layers: calling `fn` directly skips all of them.
		return fn

	return decorator


def _guarded(fn: Callable) -> Callable:
	@wraps(fn)
	def wrapper(*args, **kwargs):
		refusal = _upgrade_refusal()
		if refusal:
			return refusal
		return fn(*args, **kwargs)

	return wrapper


def _upgrade_refusal() -> dict | None:
	"""426 for an app older than `min_app_version`.

	A missing or unparseable `X-App-Version` passes: this gate exists to move
	released apps forward, and every released app sends the header.
	"""
	minimum = version_tuple(frappe.get_cached_doc(SETTINGS_DOCTYPE).min_app_version)
	current = version_tuple(frappe.get_request_header("X-App-Version"))
	if minimum is None or current is None or current >= minimum:
		return None
	frappe.response["http_status_code"] = 426
	frappe.local.message_log = []
	return error_response(_("Please update the app to continue."), "UPGRADE_REQUIRED")


_MODULES = [f"oan_farmer_app_service.api.v1.{name}" for name in ("a2c", "auth", "consent", "grievance")]
# One claim per prefix, not one for all of `/api/`, which other apps on the bench share.
_register_routes = [
	make_route_registrar(_MODULES, prefix)
	for prefix in (AUTH_NAMESPACE + "/", *(f"{NAMESPACE}/{name}/" for name in SERVICES))
]


def ensure_routes_registered() -> None:
	"""Add every declared rule to Frappe's URL map and claim the namespaces, once per worker."""
	ensure_auth_routes()
	for register in _register_routes:
		register()
