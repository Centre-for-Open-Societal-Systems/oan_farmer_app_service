"""A2C backend. Its `/consent-requests/*` creates the farmer profile and is not the Consent Manager's `/consents`."""

from oan_farmer_app_service.api.proxy import Route, declare

declare(
	"a2c",
	[
		Route(("GET",), "/catalog/facets", "/{version}/catalog/facets", "Catalog facets"),
		Route(("GET",), "/catalog/products", "/{version}/catalog/products", "List catalog products"),
		Route(("GET",), "/catalog/banks/<bank_id>", "/{version}/catalog/banks/{bank_id}", "Bank details"),
		Route(("GET",), "/catalog/saved-products", "/{version}/catalog/saved-products", "List saved products"),
		Route(
			("PUT", "DELETE"),
			"/catalog/saved-products/<product>",
			"/{version}/catalog/saved-products/{product}",
			"Save or unsave a product",
		),
		Route(
			("GET", "POST"),
			"/applications",
			"/{version}/applications",
			"List my applications, or create a draft",
		),
		Route(
			("GET", "PATCH"),
			"/applications/<application_id>",
			"/{version}/applications/{application_id}",
			"Get or update my application",
		),
		Route(
			("POST",),
			"/applications/<application_id>/submit",
			"/{version}/applications/{application_id}/submit",
			"Submit my application",
		),
		Route(
			("GET",),
			"/applications/<application_id>/documents",
			"/{version}/loan-applications/{application_id}/documents",
			"List application documents",
		),
		Route(("GET",), "/consent-requests/farmers", "/{version}/consent/farmers", "Search farmer by Fayda ID"),
		Route(("GET",), "/consent-requests/reasons", "/{version}/consent/reasons", "Consent reasons"),
		Route(
			("GET",), "/consent-requests/allowed-fields", "/{version}/consent/allowed-fields", "Consent allowed fields"
		),
		Route(("POST",), "/consent-requests/otp", "/{version}/consent/otp", "Request consent OTP"),
		Route(("POST",), "/consent-requests/otp/verify", "/{version}/consent/otp/verify", "Verify consent OTP"),
		Route(
			("POST",),
			"/consent-requests",
			"/{version}/consent/requests",
			"Submit consent, which creates the farmer profile",
		),
	],
)
