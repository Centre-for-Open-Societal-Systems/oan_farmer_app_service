"""NMIS Public API backend (read-only validated agricultural market prices).

The gateway verifies the farmer's JWT, but does not forward it (`forward_token=False`)
because NMIS is a public, unauthenticated third-party API.
"""

from oan_farmer_app_service.api.proxy import Route, declare

declare(
	"nmis",
	[
		Route(
			("GET",),
			"/health",
			"/{version}/health",
			"NMIS service health check",
			forward_token=False,
		),
		Route(
			("GET",),
			"/market-prices/commodities",
			"/{version}/market-prices/commodities",
			"List or search commodities",
			forward_token=False,
		),
		Route(
			("GET",),
			"/market-prices/markets",
			"/{version}/market-prices/markets",
			"List or search markets",
			forward_token=False,
		),
		Route(
			("GET",),
			"/market-prices/marketplaces",
			"/{version}/market-prices/marketplaces",
			"List or search marketplaces (alias for /markets)",
			forward_token=False,
		),
		Route(
			("GET",),
			"/market-prices/latest",
			"/{version}/market-prices/latest",
			"Latest validated market price",
			forward_token=False,
		),
		Route(
			("GET",),
			"/market-prices",
			"/{version}/market-prices",
			"Historical validated market price by date (or latest when omitted)",
			forward_token=False,
		),
	],
)
