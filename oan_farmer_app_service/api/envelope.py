"""Turn any backend's answer into the one envelope the mobile API returns.

The backends answer in three shapes:

- Frappe services: `{status: "success", data, message, pagination}`, and on error
  `{status: "error", message, code, details}`.
- Consent Manager (FastAPI): a bare object, or `{items, total, page, size, pages}`
  for a list, and on error `{detail}` or `{error, detail, trace_id}`.
- Anything else, such as an HTML error page from a proxy in between.

Only named fields are copied out of an upstream error, so a Frappe `exc` or
`_server_messages` traceback cannot reach the app. A 5xx body is dropped whole.
"""

import re
from dataclasses import dataclass

import frappe
from frappe import _
from oan_auth_service.api.utils import error_code_for, error_response, page_meta, success_response

from oan_farmer_app_service.api.upstream import UpstreamError, UpstreamResponse

_CM_PAGE_KEYS = {"items", "total", "page", "size"}
_SNAKE_CASE = re.compile(r"[a-z]+(_[a-z]+)*")


@dataclass
class Outcome:
	ok: bool
	status: int
	data: object = None
	message: str | None = None
	pagination: dict | None = None
	meta: dict | None = None
	code: str | None = None
	details: dict | None = None
	field_codes: dict | None = None


def parse(response: UpstreamResponse) -> Outcome:
	if 200 <= response.status < 300:
		return _parse_success(response)
	if 400 <= response.status < 500:
		return _parse_client_error(response)
	if response.status == 503:
		return Outcome(False, 503, code="SERVICE_UNAVAILABLE", message=_("Service unavailable"))
	if response.status == 504:
		return Outcome(False, 504, code="GATEWAY_TIMEOUT", message=_("Service unavailable"))
	# Other 5xx, or a redirect from a misconfigured base_url or an auth page in between.
	return _bad_gateway()


def from_error(exc: UpstreamError) -> Outcome:
	return Outcome(False, exc.http_status_code, code=exc.code, message=_("Service unavailable"))


def respond(outcome: Outcome) -> dict:
	"""The return value for an endpoint under `handle_api_errors`."""
	frappe.response["http_status_code"] = outcome.status
	if outcome.ok:
		return success_response(
			data=outcome.data,
			message=outcome.message or "Success",
			pagination=outcome.pagination,
			meta=outcome.meta,
		)
	frappe.local.message_log = []
	return error_response(
		outcome.message or _("Request could not be processed"),
		outcome.code or "GENERIC_ERROR",
		details=outcome.details,
		field_codes=outcome.field_codes,
	)


def _bad_gateway() -> Outcome:
	return Outcome(False, 502, code="BAD_GATEWAY", message=_("Service unavailable"))


def _parse_success(response: UpstreamResponse) -> Outcome:
	payload = response.json()
	if payload is None:
		# A JSON route answering with a body that is not JSON is a fault, not "no data".
		return _bad_gateway() if response.body else Outcome(True, response.status)

	if isinstance(payload, dict):
		# Compared to "success" exactly: a Consent Manager artefact has a `status` of its own.
		if payload.get("status") == "success":
			return Outcome(
				True,
				response.status,
				data=payload.get("data"),
				message=payload.get("message"),
				pagination=payload.get("pagination"),
				meta=payload.get("meta"),
			)
		if _CM_PAGE_KEYS <= payload.keys():
			return Outcome(
				True,
				response.status,
				data=payload["items"],
				pagination=page_meta(payload["total"], payload["page"], payload["size"]),
			)
	return Outcome(True, response.status, data=payload)


def _parse_client_error(response: UpstreamResponse) -> Outcome:
	payload = response.json()
	# FastAPI answers 422 for what this API calls a 400.
	outcome = Outcome(
		False, response.status, code=error_code_for(400 if response.status == 422 else response.status)
	)
	if not isinstance(payload, dict):
		return outcome

	detail = payload.get("detail")
	for candidate in (payload.get("message"), detail):
		if isinstance(candidate, str) and candidate:
			outcome.message = candidate
			break

	if isinstance(payload.get("code"), str):
		outcome.code = payload["code"]
	elif isinstance(payload.get("error"), str):
		# Consent Manager's `{error}` is a snake_case code such as `not_found`, or an exception text.
		error = payload["error"]
		if _SNAKE_CASE.fullmatch(error):
			outcome.code = error.upper()
		else:
			outcome.message = outcome.message or error
	if isinstance(payload.get("details"), dict):
		outcome.details = payload["details"]
	if isinstance(payload.get("field_codes"), dict):
		outcome.field_codes = payload["field_codes"]
	if isinstance(detail, list):
		outcome.details = _fastapi_details(detail)
		outcome.message = outcome.message or _("Validation failed")
	return outcome


def _fastapi_details(errors: list) -> dict:
	"""FastAPI's `[{loc: ["body", "reason"], msg}]` as this API's `{field: message}`."""
	details = {}
	for error in errors:
		if isinstance(error, dict):
			loc = [str(part) for part in error.get("loc") or [] if part not in ("body", "query", "path")]
			details[".".join(loc) or "request"] = error.get("msg")
	return details
