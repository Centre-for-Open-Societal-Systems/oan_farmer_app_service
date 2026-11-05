#!/usr/bin/env python3
"""
generate_postman_collections.py

Writes one Postman collection per backend behind the farmer app gateway (Grievance, A2C,
Consent) and a shared local environment. The requests are listed here, and `check_coverage`
fails if a route declared in the app is missing from them, so the collections cannot drift.

    PYTHONPATH=. python3 postman/generate_postman_collections.py

Run against the Frappe server directly (`api_prefix` = `/api`) or through APISIX (`api_prefix` empty).
Each collection's paths are `/<service>/{{<service>_version}}/...`: the app picks the backend version.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

ENVELOPE = """
const body = pm.response.json();
pm.test("answers in the mobile envelope", () => {
  pm.expect(body).to.be.an("object");
  pm.expect(body.status).to.be.oneOf(["success", "error"]);
});
if (body.status === "error") {
  pm.test("error carries a code and a message", () => {
    pm.expect(body.code).to.be.a("string");
    pm.expect(body.message).to.be.a("string");
  });
  pm.test("error does not leak a traceback or a backend URL", () => {
    const text = pm.response.text();
    pm.expect(text).to.not.include("Traceback");
    pm.expect(text).to.not.match(/https?:\\/\\/(?:127\\.0\\.0\\.1|localhost)/);
  });
}
"""


def status_test(allowed):
    return f'pm.test("status is one of {allowed}", () => pm.expect(pm.response.code).to.be.oneOf({allowed}));\n'


def request(name, method, path, allowed, *, body=None, query=None, auth=True, idempotent=False, tests="", pre="", envelope=True):
    headers = [{"key": "Accept", "value": "application/json"}, {"key": "X-App-Version", "value": "{{app_version}}"}]
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json"})
    if idempotent:
        headers.append({"key": "Idempotency-Key", "value": "{{idem_key}}"})
    item = {
        "name": name,
        "event": [
            {"listen": "test", "script": {"type": "text/javascript", "exec": (status_test(allowed) + (ENVELOPE if envelope else "") + tests).split("\n")}}
        ],
        "request": {
            "method": method,
            "header": headers,
            "url": {
                "raw": "{{base_url}}{{api_prefix}}" + path,
                "host": ["{{base_url}}"],
                "path": (["{{api_prefix}}"] + path.strip("/").split("/")),
                "query": [{"key": k, "value": v} for k, v in (query or {}).items()],
            },
            "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}]}
            if auth
            else {"type": "noauth"},
        },
    }
    if body is not None:
        item["request"]["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    if pre:
        item["event"].insert(0, {"listen": "prerequest", "script": {"type": "text/javascript", "exec": pre.split("\n")}})
    return item


def folder(name, items):
    return {"name": name, "item": items}


def gate(path):
    """A call with no token is refused by oan_auth_service's middleware before any route or backend runs.

    That refusal is Frappe's plain `{message, exc_type}` body, not the mobile envelope.
    """
    return request(
        "Without a token is refused", "GET", path, [401], auth=False, envelope=False,
        tests='pm.test("refused as an authentication error", () => pm.expect(pm.response.json().exc_type).to.eql("AuthenticationError"));',
    )


NEW_KEY = 'pm.collectionVariables.set("idem_key", "run-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10));'

# --------------------------------------------------------------------------- Grievance
GRIEVANCE = [
    folder("00 Gate", [gate("/submitters/options")]),
    folder("Reference", [
        request("Submitter options", "GET", "/submitters/options", [200]),
        request("Administrative areas", "GET", "/administrative-areas", [200]),
        request("Administrative area ancestors", "GET", "/administrative-areas/{{area}}/ancestors", [200, 404]),
    ]),
    folder("Grievances", [
        request(
            "Submit a grievance", "POST", "/grievances", [200, 201],
            body={"description": "Postman: irrigation canal blocked", "consent_given": 1, "submission_channel": "Mobile App"},
            idempotent=True, pre=NEW_KEY,
            tests='const d = pm.response.json().data || {};\n'
                  'const ticket = d.ticket_number || d.name || d.ticket;\n'
                  'pm.test("returns a ticket", () => pm.expect(ticket, JSON.stringify(d)).to.be.ok);\n'
                  'if (ticket) pm.collectionVariables.set("ticket", ticket);\n'
                  'pm.collectionVariables.set("first_ticket", ticket || "");',
        ),
        request(
            "Retry with the same Idempotency-Key replays the first answer", "POST", "/grievances", [200, 201],
            body={"description": "Postman: irrigation canal blocked", "consent_given": 1, "submission_channel": "Mobile App"},
            idempotent=True,
            tests='const d = pm.response.json().data || {};\n'
                  'const ticket = d.ticket_number || d.name || d.ticket;\n'
                  'pm.test("same ticket as the first call", () => pm.expect(ticket).to.eql(pm.collectionVariables.get("first_ticket")));',
        ),
        request(
            "Same Idempotency-Key with a different body is refused", "POST", "/grievances", [422],
            body={"description": "Postman: a DIFFERENT complaint", "consent_given": 1, "submission_channel": "Mobile App"},
            idempotent=True,
        ),
        request("List my grievances", "GET", "/grievances", [200], query={"page_size": "5"}),
        request("Grievance timeline", "GET", "/grievances/{{ticket}}/timeline", [200, 404]),
        request("Submit feedback", "POST", "/grievances/{{ticket}}/feedback", [200, 201, 400, 404],
                body={"rating": 5, "comment": "Postman"}),
        request("Reopen is allowed", "POST", "/grievances/{{ticket}}/action", [200, 400, 404],
                body={"action": "Reopen", "comment": "Postman"}),
        # The gateway does not filter actions; the grievance workflow decides which a caller may take.
        # Against a real backend this checks that decision. Against a mock it checks only the mock.
        request("The backend refuses an officer action", "POST", "/grievances/{{ticket}}/action", [400, 403],
                body={"action": "Resolve", "reason": "Postman"}),
    ]),
]

# --------------------------------------------------------------------------------- A2C
A2C = [
    folder("00 Gate", [gate("/catalog/facets")]),
    folder("Catalog", [
        request("Facets", "GET", "/catalog/facets", [200]),
        request("Products", "GET", "/catalog/products", [200]),
        request("Bank details", "GET", "/catalog/banks/{{bank_id}}", [200, 404]),
        request("Saved products", "GET", "/catalog/saved-products", [200]),
        request("Save a product", "PUT", "/catalog/saved-products/{{product}}", [200, 201, 404]),
        request("Unsave a product", "DELETE", "/catalog/saved-products/{{product}}", [200, 204, 404]),
    ]),
    folder("Consent and farmer profile", [
        request("Find farmer by Fayda ID", "GET", "/consent-requests/farmers", [200, 400, 404], query={"fayda_id": "{{fayda_id}}"}),
        request("Consent reasons", "GET", "/consent-requests/reasons", [200]),
        request("Allowed fields", "GET", "/consent-requests/allowed-fields", [200]),
        request("Request consent OTP", "POST", "/consent-requests/otp", [200, 400, 404], body={"fayda_id": "{{fayda_id}}"}),
        request("Verify consent OTP", "POST", "/consent-requests/otp/verify", [200, 400, 404], body={"otp": "000000", "transaction_id": "x"}),
        request("Submit consent", "POST", "/consent-requests", [200, 201, 400, 404], body={"fayda_id": "{{fayda_id}}"}),
    ]),
    folder("Applications", [
        request(
            "Create an application", "POST", "/applications", [200, 201],
            body={"loan_product": "{{product}}", "requested_amount": 10000, "loan_reason": "Postman"},
            idempotent=True, pre=NEW_KEY,
            tests='const d = pm.response.json().data || {};\n'
                  'pm.test("returns an application id", () => pm.expect(d.application_id, JSON.stringify(d)).to.be.ok);\n'
                  'if (d.application_id) { pm.collectionVariables.set("application_id", d.application_id); pm.collectionVariables.set("first_application", d.application_id); }',
        ),
        request(
            "Retry with the same Idempotency-Key replays the first answer", "POST", "/applications", [200, 201],
            body={"loan_product": "{{product}}", "requested_amount": 10000, "loan_reason": "Postman"},
            idempotent=True,
            tests='const d = pm.response.json().data || {};\n'
                  'pm.test("same application as the first call", () => pm.expect(d.application_id).to.eql(pm.collectionVariables.get("first_application")));',
        ),
        request("List my applications", "GET", "/applications", [200]),
        request("Get my application", "GET", "/applications/{{application_id}}", [200, 404]),
        request("Update my application", "PATCH", "/applications/{{application_id}}", [200, 400, 404], body={"loan_reason": "Postman update"}),
        request("List documents", "GET", "/applications/{{application_id}}/documents", [200, 404]),
        request("Submit my application", "POST", "/applications/{{application_id}}/submit", [200, 400, 404],
                idempotent=True, pre=NEW_KEY, body={}),
    ]),
]

# ------------------------------------------------------------------------------ Consent
CONSENT = [
    folder("00 Gate", [gate("/consents")]),
    folder("My consents", [
        request("List my consents", "GET", "/consents", [200], query={"page": "1", "size": "5"},
                tests='const b = pm.response.json();\n'
                      'if (b.status === "success") pm.test("has pagination", () => pm.expect(b.pagination).to.be.an("object"));\n'
                      'const first = (b.data || [])[0]; if (first && first.id) pm.collectionVariables.set("consent_id", first.id);'),
        request("Get a consent", "GET", "/consents/{{consent_id}}", [200, 404]),
        request("Get a receipt", "GET", "/consent-receipts/{{receipt_id}}", [200, 404]),
        request("Revoke a consent", "POST", "/consents/{{consent_id}}/revoke", [200, 404, 409], body={}),
    ]),
]

COLLECTIONS = {
    "OAN Farmer App - Grievance": ("oan-farmer-app.grievance.postman_collection.json", GRIEVANCE,
                                   "Grievance routes of the farmer app gateway: /grievances, /administrative-areas, /submitters."),
    "OAN Farmer App - A2C": ("oan-farmer-app.a2c.postman_collection.json", A2C,
                             "A2C routes of the farmer app gateway: /catalog, /applications, /consent-requests."),
    "OAN Farmer App - Consent": ("oan-farmer-app.consent.postman_collection.json", CONSENT,
                                 "Consent Manager routes of the farmer app gateway: /consents, /consent-receipts."),
}

SERVICE = {"OAN Farmer App - Grievance": "grievance", "OAN Farmer App - A2C": "a2c", "OAN Farmer App - Consent": "consent"}


def scope(items, service):
    """Put a collection's requests under `/<service>/{{<service>_version}}`."""
    version = "{{" + service + "_version}}"
    for item in items:
        if "item" in item:
            scope(item["item"], service)
            continue
        url = item["request"]["url"]
        rest = url["raw"][len("{{base_url}}{{api_prefix}}"):]
        url["raw"] = f"{{{{base_url}}}}{{{{api_prefix}}}}/{service}/{version}" + rest
        url["path"] = ["{{api_prefix}}", service, version] + rest.strip("/").split("/")


for _title, (_f, _items, _d) in COLLECTIONS.items():
    scope(_items, SERVICE[_title])

ENVIRONMENT = {
    "name": "OAN Farmer App - local",
    "values": [
        {"key": "base_url", "value": "http://127.0.0.1:8010", "enabled": True},
        {"key": "api_prefix", "value": "/api", "enabled": True},
        {"key": "grievance_version", "value": "v1", "enabled": True},
        {"key": "a2c_version", "value": "v1", "enabled": True},
        {"key": "consent_version", "value": "v1", "enabled": True},
        {"key": "access_token", "value": "", "type": "secret", "enabled": True},
        {"key": "app_version", "value": "1.0.0", "enabled": True},
        {"key": "area", "value": "", "enabled": True},
        {"key": "bank_id", "value": "", "enabled": True},
        {"key": "product", "value": "", "enabled": True},
        {"key": "fayda_id", "value": "", "enabled": True},
        {"key": "receipt_id", "value": "", "enabled": True},
    ],
    "_postman_variable_scope": "environment",
}


def check_coverage():
    """Every route the app declares, except the login exchange, is in a collection."""
    from oan_auth_service.api.router import declared_routes
    from oan_farmer_app_service.api.v1 import a2c, auth, consent, grievance  # noqa: F401

    def norm(p):
        import re

        return re.sub(r"\{\{\w+\}\}|<[^>]+>", "*", p)

    have = set()
    stack = [i for _, (_, items, _) in COLLECTIONS.items() for i in items]
    while stack:
        item = stack.pop()
        if "item" in item:
            stack.extend(item["item"])
            continue
        raw = item["request"]["url"]["raw"].replace("{{base_url}}{{api_prefix}}", "")
        have.add((item["request"]["method"], norm(raw)))
    missing = [
        (m, d["path"].removeprefix("/api"))
        for d in declared_routes("oan_farmer_app_service")
        for m in d["methods"]
        if not d["path"].endswith("/auth/iam/token") and (m, norm(d["path"].removeprefix("/api"))) not in have
    ]
    if missing:
        raise SystemExit("Declared in the app but in no collection:\n" + "\n".join(f"  {m} {p}" for m, p in missing))


def main():
    check_coverage()
    for title, (filename, items, description) in COLLECTIONS.items():
        doc = {
            "info": {"name": title, "description": description, "schema": SCHEMA},
            "item": items,
            "variable": [
                {"key": "ticket", "value": ""}, {"key": "application_id", "value": ""}, {"key": "consent_id", "value": ""},
                {"key": "idem_key", "value": ""}, {"key": "first_ticket", "value": ""}, {"key": "first_application", "value": ""},
            ],
        }
        (OUT / filename).write_text(json.dumps(doc, indent=2) + "\n")
    (OUT / "oan-farmer-app.local.postman_environment.json").write_text(json.dumps(ENVIRONMENT, indent=2) + "\n")
    print("Wrote", len(COLLECTIONS), "collections and the environment")


if __name__ == "__main__":
    main()
