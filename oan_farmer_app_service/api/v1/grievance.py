"""Grievance backend. Attachments and exports are not here: APISIX carries them (apisix/)."""

from oan_farmer_app_service.api.proxy import Route, declare

declare(
	"grievance",
	[
		Route(
			("GET", "POST"),
			"/grievances",
			"/{version}/grievances",
			"List my grievances, or submit one",
		),
		Route(
			("GET",),
			"/grievances/<ticket>/timeline",
			"/{version}/grievances/{ticket}/timeline",
			"Grievance timeline",
		),
		Route(
			("POST",),
			"/grievances/<ticket>/action",
			"/{version}/grievances/{ticket}/action",
			"Reopen a grievance, or close it with a rating",
		),
		Route(
			("POST",),
			"/grievances/<ticket>/feedback",
			"/{version}/grievances/{ticket}/feedback",
			"Submit grievance feedback",
		),
		Route(
			("GET",),
			"/submitters/options",
			"/{version}/submitters/options",
			"Submitter options",
			auth="public",
		),
		Route(
			("GET",),
			"/administrative-areas",
			"/{version}/administrative-areas",
			"Administrative areas",
			auth="public",
		),
		Route(
			("GET",),
			"/administrative-areas/<area>/ancestors",
			"/{version}/administrative-areas/{area}/ancestors",
			"Administrative area ancestors",
			auth="public",
		),
	],
)
