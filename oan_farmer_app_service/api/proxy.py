"""JSON pass-through routes, declared as data so each module reads like the route map."""

import re
import string
from dataclasses import dataclass
from urllib.parse import quote

import frappe
from frappe import _
from oan_auth_service.api.middleware import bearer_token
from werkzeug.routing import Map, Rule

from oan_farmer_app_service.api import envelope
from oan_farmer_app_service.api.router import mobile_route
from oan_farmer_app_service.api.upstream import UpstreamError, audit, call

_BODY_METHODS = {"POST", "PUT", "PATCH"}
# The app names the backend version in the path (`/a2c/v1/...`). Only its shape is checked
# here: whether the backend serves that version is the backend's answer to give.
_VERSION = re.compile(r"v[1-9][0-9]{0,2}")


class UnsupportedMediaType(frappe.ValidationError):
	http_status_code = 415


@dataclass(frozen=True)
class Route:
	"""One mobile path. `target` names the same `{params}` as `path` names `<params>`.

	`path` is under `/<service>/<version>`, and `target` takes the version as `{version}`.
	"""

	methods: tuple[str, ...]
	path: str
	target: str
	summary: str
	# What the backend declares for this route. `"public"` takes no token; anything else needs a JWT.
	auth: str | None = None


def declare(upstream_name: str, routes: list[Route]) -> None:
	for item in routes:
		_declare_route(upstream_name, item)


def _declare_route(upstream_name: str, item: Route) -> None:
	path = f"/{upstream_name}/<version>{item.path}"
	rule = Rule(path)
	Map([rule])  # a rule only parses its arguments once it is bound to a map
	if _target_params(item.target) != set(rule.arguments):
		raise ValueError(f"{path} and {item.target} name different parameters")

	# Takes **kwargs because the router passes the form dict with the path
	# arguments on top. Only the params the target names are read from it.
	def handler(**kwargs):
		if not _VERSION.fullmatch(str(kwargs.get("version", ""))):
			raise frappe.DoesNotExistError(_("Resource not found"))
		return forward(
			upstream_name,
			path,
			frappe.request.method,
			_target_path(item.target, kwargs),
		)

	mobile_route(path, methods=item.methods, auth=item.auth, summary=item.summary)(handler)


def _target_params(target: str) -> set[str]:
	"""The `{name}` fields of a backend path template."""
	return {name for _, name, _, _ in string.Formatter().parse(target) if name}


def _target_path(template: str, args: dict) -> str:
	values = {}
	for name in _target_params(template):
		value = str(args[name])
		# quote() leaves dots alone and urllib3 resolves dot segments, so `..` would
		# climb out of the path this route allows.
		if value in (".", ".."):
			raise frappe.DoesNotExistError(_("Resource not found"))
		values[name] = quote(value, safe="")
	return template.format(**values)


def forward(upstream_name: str, route: str, method: str, path: str) -> dict:
	"""Send the current request to `path` on the backend, and answer in the mobile envelope."""
	# The auth middleware has already refused a request this route needs a token for.
	token = bearer_token()
	body = None
	if method in _BODY_METHODS:
		body = frappe.request.get_data()
		if body and not frappe.request.is_json:
			# Multipart is the gateway's path (see apisix/); Frappe has already parsed it here.
			raise UnsupportedMediaType(_("This route takes a JSON body."))
	params = list(frappe.request.args.items(multi=True))

	try:
		response = call(upstream_name, method, path, token, params=params, body=body)
	except UpstreamError as exc:
		audit(upstream_name, route, type(exc).__name__, error=exc)
		return envelope.respond(envelope.from_error(exc))

	audit(upstream_name, route, response.status, response.latency_ms)
	outcome = envelope.parse(response)
	# The backend decides how long an answer may be reused; only a successful GET passes it on.
	if outcome.ok and method == "GET" and response.cache_control:
		frappe.local.response_headers.set("Cache-Control", response.cache_control)
	return envelope.respond(outcome)
