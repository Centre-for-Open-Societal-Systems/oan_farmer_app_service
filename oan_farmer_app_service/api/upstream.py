"""HTTP client for the backends behind the gateway.

It never retries and never follows a redirect: a retried POST could create a
second grievance, and a followed redirect could carry the token to another host.
"""

import json
import time
from dataclasses import dataclass, field

import frappe
import requests
from oan_auth_service.api import iam_tokens
from urllib3.exceptions import MaxRetryError, NewConnectionError

CONFIG_KEY = "oan_farmer_app_service"
SETTINGS_DOCTYPE = "OAN Farmer App Settings"


class UpstreamError(Exception):
	"""The backend gave no usable answer. `str(self)` names the backend: log it, never return it."""

	http_status_code = 503
	code = "SERVICE_UNAVAILABLE"
	# False only when the request cannot have reached the backend.
	maybe_processed = True


class UpstreamUnavailable(UpstreamError):
	pass


class UpstreamTimeout(UpstreamError):
	http_status_code = 504
	code = "GATEWAY_TIMEOUT"


class UpstreamTokenRefused(UpstreamError):
	"""The token is not one this backend's configured issuer or audience names, so it was not sent."""

	http_status_code = 502
	code = "BAD_GATEWAY"
	maybe_processed = False


@dataclass(frozen=True)
class UpstreamResponse:
	status: int
	body: bytes
	latency_ms: int = 0
	set_cookies: list[str] = field(default_factory=list)
	cache_control: str | None = None

	def json(self):
		"""The parsed body, or None when it is empty or not JSON."""
		try:
			return json.loads(self.body) if self.body else None
		except ValueError:
			return None


def call(
	name: str, method: str, path: str, token: str | None, params=None, body: bytes | None = None
) -> UpstreamResponse:
	"""Send the caller's request to backend `name`, once. `path` must already be URL-quoted.

	`token` is None on a route the backend serves to guests.

	Raises `UpstreamError` when the backend gives no usable answer.
	"""
	entry = frappe.get_cached_doc(SETTINGS_DOCTYPE).backend(name)

	# The token goes to this backend, so its address must be https or loopback.
	base_url = (entry.get("base_url") or "").rstrip("/")
	if not base_url:
		raise UpstreamUnavailable(f"Upstream {name!r} has no base_url (OAN Farmer App Settings or site_config)")
	if not iam_tokens.is_secure_url(base_url):
		raise UpstreamUnavailable(f"Upstream {name!r} base_url must be https")
	# Refuse to forward a token whose issuer or audience the backend's settings do not name.
	# Both are optional, since whether the backends share one IAM realm is still open. Read
	# unverified: this stops a token leaking to the wrong backend, and the backend verifies
	# whatever it does receive.
	issuer, audience = entry.get("issuer"), entry.get("audience")
	if token and (issuer or audience):
		claims = iam_tokens.peek_claims(token)
		if issuer and claims.get("iss") != issuer:
			raise UpstreamTokenRefused(f"Token issuer is not the one configured for {name}")
		if audience:
			audiences = claims.get("aud")
			if audience not in (audiences if isinstance(audiences, list) else [audiences]):
				raise UpstreamTokenRefused(f"Token audience does not list {name}")

	# The caller's context. `X-Forwarded-For` matters most: the backends rate limit by IP,
	# and without it every farmer shares the gateway's one IP and its one budget.
	headers = {"Accept": "application/json"}
	if ip := getattr(frappe.local, "request_ip", None):
		headers["X-Forwarded-For"] = ip
	for header in ("X-Request-Id", "Accept-Language", "Idempotency-Key"):
		# The backend dedupes on `Idempotency-Key` (oan_auth_service's `@idempotent`).
		if value := frappe.get_request_header(header):
			headers[header] = value

	request_headers = dict(headers)
	if token:
		request_headers["Authorization"] = f"Bearer {token}"
	if body:
		request_headers["Content-Type"] = "application/json"

	started = time.monotonic()
	try:
		response = requests.request(
			method,
			base_url + path,
			params=params,
			data=body or None,
			headers=request_headers,
			timeout=(entry["connect_timeout"], entry["read_timeout"]),
			allow_redirects=False,
		)
	# The type name only: requests' message holds the full URL, query string included,
	# and these errors get logged.
	except requests.Timeout as exc:
		error = UpstreamTimeout(f"{name}: {type(exc).__name__}")
		error.maybe_processed = not isinstance(exc, requests.ConnectTimeout)
		raise error from exc
	except requests.RequestException as exc:
		error = UpstreamUnavailable(f"{name}: {type(exc).__name__}")
		error.maybe_processed = not _never_connected(exc)
		raise error from exc

	return UpstreamResponse(
		status=response.status_code,
		body=response.content,
		latency_ms=int((time.monotonic() - started) * 1000),
		set_cookies=iam_tokens._set_cookie_headers(response),
		cache_control=response.headers.get("Cache-Control"),
	)


def _never_connected(exc: requests.RequestException) -> bool:
	"""Connection refused or DNS failure, as opposed to a connection dropped mid-request."""
	cause = exc.args[0] if exc.args else None
	return isinstance(cause, MaxRetryError) and isinstance(cause.reason, NewConnectionError)


def audit(
	name: str, route: str, status: int | str, latency_ms: int = 0, error: UpstreamError | None = None
) -> None:
	"""One log line per upstream call.

	Logs the route template, never the URL: the query string can hold a Fayda ID.
	`error` is a call that got no usable answer: its text names the backend, so it is
	only logged, as a warning, and never returned.
	"""
	logger = frappe.logger(CONFIG_KEY, allow_site=True)
	logger.info({"upstream": name, "route": route, "status": status, "latency_ms": latency_ms})
	if error is not None:
		logger.warning(str(error))
