# OAN Farmer App Service

The backend-for-frontend of the OAN farmer app: one host in front of Grievance, A2C and the Consent Manager. Design: `oan_auth_service/docs/mobile-bff-frappe-architecture.md`.

The public contract for a backend is `/<service>/<version>/...`: `/a2c/v1/...`, `/consent/v1/...`, `/grievance/v1/...`. **The gateway has no API version of its own for these.** The app names the backend version it wants in each path (`/a2c/v2/...` beside `/grievance/v1/...`) and the gateway forwards it as it is, so a backend can ship a new version without a gateway release. Only the shape (`v` and digits) is checked here; whether the backend serves that version is the backend's answer, and its error is passed on. The service prefix keeps two backends from colliding on a path. Login stays at `/v1/auth/*` on the same host and namespace as `oan_auth_service`'s own routes (`/v1/auth/*`, `/v1/admin/*`, `/v1/notifications/*`). Inside Frappe the routes live at `/api/<service>/<version>/...`, because Frappe only dispatches `/api/*`; the APISIX gateway rewrites one to the other (`apisix/`, same layout as the grievance and A2C apps). A route here never repeats one of the library's: the generator refuses to run if it does.

## Install

```bash
bench get-app /path/to/oan_farmer_app_service
bench --site <site> install-app oan_farmer_app_service
```

Needs `oan_auth_service` on the same site, with `OAN IAM Service` rows for the same IAMs the backends trust. Then generate and sync the gateway config, see `apisix/README.md`.

## Configuration

Open **OAN Farmer App Settings** in the desk (System Manager):

- **Minimum App Version**: an older `X-App-Version` gets 426 with code `UPGRADE_REQUIRED`. Empty turns the gate off.
- **Timeouts**: connect 3, read 15 (seconds).
- **Backends**: one row each for `grievance`, `a2c` and `consent`, with `base_url` and optionally `issuer`, `audience` and per-backend timeouts.

`base_url` must be https, or http to a loopback host; the form refuses anything else on save. Everything in front of the version belongs in `base_url`, not in code: a Frappe backend reached directly ends in `/api` (`https://a2c.example/api`, `https://grievance.example/api`), while one reached through its own gateway is just that gateway's address. Switching is a settings change, and the routes below are written without the prefix. The Consent Manager's `/consent` is part of its own routes, so it stays in the path. When `issuer` or `audience` is set, a token that does not match is not forwarded, and the app gets a 502.

`base_url` differs per environment, so `site_config.json` may override it and the row can stay empty:

```json
{
  "oan_farmer_app_service": {
    "upstreams": {
      "a2c": { "base_url": "https://a2c.example" }
    }
  }
}
```

Run `bench migrate` after installing, to create the settings doctype.

## App contract

**Login is `oan_auth_service`'s, unchanged:** `GET /v1/auth/iam/providers`, then `POST /v1/auth/iam/login {key, return_url}` gives `{redirect_url, cookie_prefix}`. Open `redirect_url` with `FlutterWebAuth2.authenticate`. A native app cannot read the cookies the system browser receives from IAM, so the one mobile-specific step is `POST /v1/auth/iam/token {code, state, key}`, which replays the redirect to IAM and gives `{access_token, refresh_token, id_token, token_type, expires_in, iam}`. `GET /v1/auth/me` provisions the user.

**Refresh and logout are the library's too** (`POST /v1/auth/refresh`, `POST /v1/auth/logout`). IAM refreshes from its session cookies, so send the tokens back as the `<cookie_prefix>X-Access-Token`, `<cookie_prefix>X-Session-Id` and `<cookie_prefix>X-ID-Token` cookies and read the new ones from `Set-Cookie`. Do not send `Authorization` there: they are public paths and the middleware rejects an expired bearer token even there.

**Every other call:** `Authorization: Bearer <access_token>`.

**Optional headers:**

- `Idempotency-Key` on `POST /grievances`, `POST /applications` and `POST /applications/<id>/submit`. A new key for each user action, reused only for retries of that action. The gateway forwards it and the backend (`oan_auth_service`'s `@idempotent`) dedupes: a retry gets the original answer with `Idempotency-Replayed: true`, a retry while the first is still running gets 409 (retry in a moment), and the same key with a different body gets 422. Treat a replayed answer as confirmation and refetch state.
- `X-App-Version`. Below `min_app_version` every route answers 426 with code `UPGRADE_REQUIRED`.

**Paging on Consent Manager routes** uses its own query names, `page` and `size`. The `pagination` block in the response has the common shape everywhere.

## Routes

| Mobile                                                                                                                                      | Handled by | Upstream, after `base_url` (`<v>` is the version the app sent) |
| ------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------- |
| `POST /v1/auth/iam/token`                                                                                                                   | Frappe     | IAM `/auth/callback`                                           |
| `GET, POST /grievance/<v>/grievances`                                                                                                       | Frappe     | `/<v>/grievances`                                              |
| `GET /grievance/<v>/grievances/<ticket>/timeline`                                                                                           | Frappe     | same path under `/<v>`                                         |
| `POST /grievance/<v>/grievances/<ticket>/action`                                                                                            | Frappe     | same; `Reopen` and `Close Case` only                           |
| `POST /grievance/<v>/grievances/<ticket>/feedback`                                                                                          | Frappe     | same                                                           |
| `GET /grievance/<v>/submitters/options`                                                                                                     | Frappe     | `/<v>/submitters/options`                                      |
| `GET /grievance/<v>/administrative-areas`, `.../<area>/ancestors`                                                                           | Frappe     | `/<v>/administrative-areas...`                                 |
| `GET, POST /grievance/<v>/grievances/<ticket>/attachments`                                                                                  | APISIX     | `<GRIEVANCE_API_PREFIX>/<v>/grievances/<ticket>/attachments`   |
| `GET /grievance/<v>/attachments/<id>/view`, `/download`; `DELETE .../attachments/<id>`                                                      | APISIX     | `<GRIEVANCE_API_PREFIX>/<v>/attachments/...`                   |
| `GET /grievance/<v>/grievances/<ticket>/export`                                                                                             | APISIX     | `<GRIEVANCE_API_PREFIX>/<v>/grievances/<ticket>/export`        |
| `GET /a2c/<v>/catalog/facets`, `/products`, `/banks/<id>`, `/saved-products`; `PUT, DELETE .../saved-products/<id>`                         | Frappe     | `/<v>/catalog/...`                                             |
| `GET, POST /a2c/<v>/applications`; `GET, PATCH .../applications/<id>`; `POST .../submit`                                                    | Frappe     | `/<v>/applications...`                                         |
| `GET /a2c/<v>/applications/<id>/documents`                                                                                                  | Frappe     | `/<v>/loan-applications/<id>/documents`                        |
| `GET /a2c/<v>/consent-requests/farmers`, `/reasons`, `/allowed-fields`; `POST .../consent-requests/otp`, `/otp/verify`, `/consent-requests` | Frappe     | `/<v>/consent/...`                                             |
| `GET /consent/<v>/consents`, `/consents/<id>`, `/consent-receipts/<id>`; `POST .../consents/<id>/revoke`                                    | Frappe     | `/consent/<v>/my/...`                                          |

Every route needs a bearer token except `/grievance/<v>/submitters/options` and `/grievance/<v>/administrative-areas...`, which the grievance backend serves to guests. Each `Route` carries the `auth` its backend declares (`"public"` for those), and the auth service enforces it.

Deliberately absent: officer login and actions, the OTP inbox helpers, and `POST /consent/<v>/consent-requests`.
