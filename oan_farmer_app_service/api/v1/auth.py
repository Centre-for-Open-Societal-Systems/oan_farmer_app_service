"""Finish an IAM login for an app that cannot read the system browser's cookies.

Login itself is oan_auth_service's: `GET /v1/auth/iam/providers`, `POST /v1/auth/iam/login`,
and `/v1/auth/refresh`, `/v1/auth/logout` and `/v1/auth/me` afterwards. This module adds the
one step it cannot do for a native app. The system browser completes IAM's callback and
receives the tokens as `Set-Cookie` in a place the app cannot read, so the app hands the
authorization `code` and `state` here, and this replays them to IAM server-to-server and
returns the tokens in the body. The app then sends them back as the cookies IAM expects
when it calls refresh and logout.

Whether the replay works depends on IAM not binding `state` to a browser cookie (open
question 7 of the architecture doc). If it is bound, IAM answers without an access cookie
and this answers 401.
"""

import hashlib

import frappe
import requests
from frappe import _
from frappe.rate_limiter import rate_limit
from oan_auth_service.api import iam_tokens
from oan_auth_service.api.utils import success_response
from oan_auth_service.config import settings
from pydantic import BaseModel, Field

from oan_farmer_app_service.api import envelope
from oan_farmer_app_service.api.router import mobile_route
from oan_farmer_app_service.api.upstream import audit

# How long a used authorization code is remembered. eSignet's codes live far less.
USED_CODE_TTL = 600


class CallbackSchema(BaseModel):
	code: str = Field(min_length=1, max_length=4096)
	state: str = Field(min_length=1, max_length=4096)
	key: str | None = Field(None, max_length=140)


def _iam_service(key: str | None):
	"""The enabled IAM login option `key`, or the only one if `key` is empty, else None."""
	if key:
		service = settings.iam_service(key)
		return None if service is None or service.key == settings.LOCAL_LOGIN_KEY else service
	services = [s for s in settings.iam_services() if s.key != settings.LOCAL_LOGIN_KEY]
	return services[0] if len(services) == 1 else None


def _session_cookies(set_cookies: list[str]) -> dict[str, str]:
	"""Name to value for each cookie being set. One being cleared has no value and is left out."""
	cookies = {}
	for header in set_cookies:
		name, _sep, value = header.split(";", 1)[0].partition("=")
		value = value.strip().strip('"')
		if value:
			cookies[name.strip()] = value
	return cookies


def _tokens(cookies: dict[str, str], prefix: str, fallback_refresh: str | None = None) -> dict:
	access_token = cookies[iam_tokens.cookie_name(iam_tokens.ACCESS_COOKIE, prefix)]
	session_cookie = iam_tokens.cookie_name(iam_tokens.SESSION_COOKIE, prefix)
	id_cookie = iam_tokens.cookie_name(iam_tokens.ID_TOKEN_COOKIE, prefix)
	return {
		"access_token": access_token,
		"refresh_token": cookies.get(session_cookie) or fallback_refresh,
		"id_token": cookies.get(id_cookie),
		"token_type": "Bearer",
		"expires_in": iam_tokens.seconds_until_expiry(access_token),
	}


def _refused(status: int, code: str, message: str) -> dict:
	return envelope.respond(envelope.Outcome(False, status, code=code, message=message))


@mobile_route(
	"/v1/auth/iam/token", methods=("POST",), auth="public", schema=CallbackSchema, summary="Finish a login"
)
@rate_limit(limit=30, seconds=60)
def callback(code: str, state: str, key: str | None = None):
	"""Replay the eSignet redirect to IAM server-to-server and return its tokens in the body.

	The code is single-use, so it is marked before IAM is called and stays marked
	whatever happens next. After a timeout we cannot know whether IAM consumed it,
	and the user starts over rather than the gateway spending it twice.
	"""
	service = _iam_service(key)
	if service is None:
		return _refused(400, "VALIDATION_ERROR", _("Unknown login option."))
	if not iam_tokens.is_secure_url(service.base_url):
		frappe.log_error(title="oan_farmer_app_service", message=f"IAM {service.key} base_url is not https")
		return _refused(503, "SERVICE_UNAVAILABLE", _("Identity service unavailable"))

	marker = frappe.cache.make_key("oan_mobile:used_code:" + hashlib.sha256(code.encode()).hexdigest())
	if not frappe.cache.set(marker, 1, nx=True, ex=USED_CODE_TTL):
		return _refused(409, "DUPLICATE_ENTRY", _("This sign-in was already used. Please sign in again."))

	try:
		response = requests.get(
			f"{service.base_url}/auth/callback",
			params={"code": code, "state": state},
			timeout=service.request_timeout,
			allow_redirects=False,
		)
	except requests.RequestException as exc:
		frappe.logger("oan_farmer_app_service", allow_site=True).warning(
			f"IAM callback failed: {type(exc).__name__}"
		)
		return _refused(503, "SERVICE_UNAVAILABLE", _("Identity service unavailable"))
	audit(service.key, "/auth/iam/token", response.status_code)

	if response.status_code >= 500:
		return _refused(503, "SERVICE_UNAVAILABLE", _("Identity service unavailable"))

	cookies = _session_cookies(iam_tokens._set_cookie_headers(response))
	if iam_tokens.cookie_name(iam_tokens.ACCESS_COOKIE, service.cookie_prefix) not in cookies:
		return _refused(401, "AUTHENTICATION_ERROR", _("Sign-in could not be completed. Please try again."))

	return success_response(data={**_tokens(cookies, service.cookie_prefix), "iam": service.key})
