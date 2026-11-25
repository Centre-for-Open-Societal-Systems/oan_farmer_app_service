"""Consent Manager, the long-lived data-sharing record. Beneficiary routes only (`/my/*`).

`POST /consent/v1/consent-requests` is requester-side and deliberately absent.
"""

from oan_farmer_app_service.api.proxy import Route, declare

declare(
	"consent",
	[
		Route(("GET",), "/consents", "/consent/{version}/my/consents", "List my consents"),
		Route(
			("GET",),
			"/consents/<consent_id>",
			"/consent/{version}/my/consents/{consent_id}",
			"Get my consent",
		),
		Route(
			("POST",),
			"/consents/<consent_id>/revoke",
			"/consent/{version}/my/consents/{consent_id}/revoke",
			"Revoke my consent",
		),
		Route(
			("GET",),
			"/consent-receipts/<receipt_id>",
			"/consent/{version}/my/receipts/{receipt_id}",
			"Get my consent receipt",
		),
	],
)
