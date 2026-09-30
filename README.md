# Backend — Django 5.2 + Django REST Framework + PostgreSQL 16

Phase-one backend baseline: environment-driven Django project on PostgreSQL,
JWT authentication with registration / login / password reset, an external-API
integration layer with one working provider plus stubs, OpenAPI 3 docs, and a
test suite.

The domain CRUD endpoints are intentionally not here yet — they are built from
your data model once it lands. Everything they need (base models, base
viewsets, pagination, filtering, error envelope, auth) is in place, so each
resource becomes a serializer + a viewset + a router line.

---

## Quick start — Docker

```bash
cp .env.example .env
# edit .env: set DJANGO_SECRET_KEY and POSTGRES_PASSWORD, and POSTGRES_HOST=db
docker compose up --build
```

`docker compose` runs the migrations and `collectstatic` for you, then starts
gunicorn. When it is up:

| URL | What |
|---|---|
| http://localhost:8000/api/v1/docs/ | Swagger UI |
| http://localhost:8000/api/v1/redoc/ | ReDoc |
| http://localhost:8000/api/v1/schema/ | OpenAPI 3 document (YAML/JSON) |
| http://localhost:8000/health/live/ | liveness probe |
| http://localhost:8000/health/ready/ | readiness probe (checks PostgreSQL) |
| http://localhost:8000/admin/ | Django admin |

Create the first admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

> **In `.env` for Docker, `POSTGRES_HOST` must be `db`** — the compose service
> name. `127.0.0.1` inside the web container points at the container itself,
> not at Postgres.

## Quick start — local, no Docker

Needs Python 3.12 and a reachable PostgreSQL 16.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # set DJANGO_SECRET_KEY + the POSTGRES_* values
createdb app_db               # or use an existing database

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

---

## Layout

```
config/          settings (env-driven), root urls, wsgi/asgi
accounts/        custom user model, auth endpoints, admin
core/            shared plumbing: base models, pagination, error envelope,
                 request-id middleware, base viewsets, health probes
integrations/    external API layer: base HTTP client, live provider, stubs
```

### Why these choices

**DRF, not GraphQL.** For CRUD-shaped resources consumed by one front-end team,
DRF gives you OpenAPI docs generated from the code, simple auth, per-endpoint
caching and throttling, and no extra concept for the front end to learn.
GraphQL pays off when clients need to compose arbitrary queries over a deep,
interconnected graph — if that turns out to be the case for some part of the
app, a GraphQL layer can be added on top of these models without rework.

**Email as the login identity.** Changing `USERNAME_FIELD` later rewrites the
auth table and every foreign key pointing at it, so it is decided up front.
`username` is kept as an optional display name.

**JWT with rotation and blacklisting.** Access tokens are short (15 min),
refresh tokens rotate on use, and the old refresh token is blacklisted — so a
stolen refresh token is single-use and logout genuinely ends the session
server-side.

**Argon2 password hashing.** Stronger than Django's PBKDF2 default; existing
PBKDF2 hashes still verify and upgrade transparently on next login.

**One error envelope.** Every API error, whatever its cause, comes back as:

```json
{
  "error": {
    "type": "validation_error",
    "message": "Invalid input.",
    "detail": { "email": ["This field is required."] },
    "request_id": "a1b2c3d4e5f6a7b8"
  }
}
```

The front end writes one error handler instead of three. `request_id` also
appears in the `X-Request-ID` response header and in every server log line for
that request, so a bug report maps straight onto the log.

---

## API

All endpoints are under `/api/v1/`. Full, always-current reference is the
Swagger UI — the table below is the shape of it.

### Auth

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register/` | public | email, password, password_confirm |
| POST | `/auth/login/` | public | returns `access`, `refresh`, `user` |
| POST | `/auth/refresh/` | public | rotates; old refresh is blacklisted |
| POST | `/auth/verify/` | public | validate a token |
| POST | `/auth/logout/` | Bearer | blacklists the supplied refresh token |
| GET/PATCH | `/auth/me/` | Bearer | current user; PATCH edits name fields only |
| POST | `/auth/password/change/` | Bearer | current + new password |
| POST | `/auth/password-reset/` | public | always 200, see below |
| POST | `/auth/password-reset/confirm/` | public | uid + token + new_password |

Send the access token as `Authorization: Bearer <access>`.

### Integrations

| Method | Path | Notes |
|---|---|---|
| GET | `/integrations/` | lists integrations, flags stubs and missing config |
| POST | `/integrations/exchange-rates/convert/` | live conversion via the external provider |
| GET | `/integrations/<slug>/health/` | probes one vendor (real network call) |

---

## Password reset — how the front end plugs in

1. The SPA POSTs the address to `/auth/password-reset/`.
2. The API emails a link to `FRONTEND_PASSWORD_RESET_URL?uid=…&token=…`. That
   URL is a **front-end route**, set in `.env`.
3. The SPA collects the new password on that page and POSTs
   `{uid, token, new_password}` to `/auth/password-reset/confirm/`.

Two deliberate behaviours:

- **The request endpoint always returns the same 200 body**, whether or not the
  address exists. A different response for unknown addresses would let anyone
  enumerate your user list.
- **No email actually leaves the server until SMTP is configured.** The default
  `EMAIL_BACKEND` is Django's console backend, which prints the message —
  including the reset link — to the server log. Set `EMAIL_BACKEND`,
  `EMAIL_HOST`, `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` in `.env` to send
  real mail.

---

## Adding a domain resource

When the data model is ready, each resource is roughly:

```python
# myapp/models.py
from core.models import BaseModel          # UUID pk + created_at/updated_at
from core.models import SoftDeleteModel    # optional: deleted_at + managers

class Widget(BaseModel):
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

# myapp/views.py
from core.viewsets import OwnedModelViewSet   # rows scoped to request.user

class WidgetViewSet(OwnedModelViewSet):
    queryset = Widget.objects.all()
    serializer_class = WidgetSerializer
    select_related_fields = ("owner",)        # kills the N+1 on list
    owner_field = "owner"
    filterset_fields = ("name",)
    search_fields = ("name",)
```

then one router line in `config/urls.py`. Pagination, filtering, ordering,
auth, throttling, the error envelope and the Swagger entry all come along
automatically.

---

## Adding an external integration

1. Add a config block to `INTEGRATIONS` in `config/settings.py`, reading its
   URL and key from the environment.
2. Subclass `integrations.clients.base.BaseAPIClient`, set `config_key`, and
   override `auth_headers()` plus the two or three methods you need.
3. Register it in `integrations/clients/registry.py`.

You inherit a pooled session, a hard timeout, bounded retries on idempotent
methods only, optional response caching, and translation of every failure into
`UpstreamServiceError` → HTTP 502.

`integrations/clients/stubs.py` holds declared-but-unimplemented integrations
(payments, CRM, notifications). Their methods raise `NotImplementedError`
rather than returning empty data — a stub that returned `{}` would let a bug
ship looking like a working feature.

---

## Tests

```bash
python manage.py test        # Django runner
pytest                       # same tests, pytest runner
```

Both need a reachable PostgreSQL (the test DB is created and dropped
automatically). No test makes a real outbound HTTP call — the integration tests
intercept requests with `responses`, so the suite does not go red when a vendor
rate-limits you, and failures that cannot be triggered on demand (timeouts,
HTML error pages, malformed rates) can still be tested.

What is covered: registration and its validation, case-insensitive email
handling, login and JWT lifecycle including refresh rotation and logout
blacklisting, password change, the full password-reset cycle plus reuse /
tamper / expiry rejection, user-enumeration resistance, rate limiting at the
real configured limits, the soft-delete base model (including queryset-level
delete), the error envelope, the integration client's failure modes, and
OpenAPI schema generation with zero warnings.

---

## Security settings

Hardened by default and keyed off `DJANGO_DEBUG`, so a deployed instance with
`DJANGO_DEBUG=False` gets HTTPS redirect, HSTS, secure + HttpOnly cookies,
`nosniff`, `X-Frame-Options: DENY` and a same-origin referrer policy without
any extra configuration. The health probes are exempt from the HTTPS redirect
so in-network container healthchecks still work.

Also in place: CORS restricted to an explicit origin list (never a wildcard),
scoped rate limits on login / registration / password reset, a movable admin
URL (`DJANGO_ADMIN_URL`), and `CONN_MAX_AGE` connection reuse so 30-50
concurrent users do not each pay for a fresh Postgres handshake.

Before going live: set a real `DJANGO_SECRET_KEY`, set `DJANGO_DEBUG=False`,
list your real `DJANGO_ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS`, configure
SMTP, and run `python manage.py check --deploy`.

---

## Phase two (not in this baseline)

Redis caching (`REDIS_URL` is already read by the settings — set it and the
cache backend switches over), Celery for background tasks, and performance
tuning once there are real query patterns to measure.
