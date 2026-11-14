# Optional company sign-in

OIDC adds a company sign-in button alongside local username/password sign-in. It maps a
provider's stable subject to an **existing** Evlet account. Evlet's active flag, role, realm,
record permissions, two-step requirements and Controls verification remain authoritative.
Provider email, groups, roles and MFA claims grant no local access. No accounts are created.

Install the optional extra in the instance environment:

```sh
python -m pip install 'evlet[oidc]'
```

Create the local accounts first, and register one confidential authorization-code client at
the provider. Require S256 PKCE and register the callback **exactly**. Add beside `instance`
in `instances/acme/config.py`:

```python
import os
from evlet import OIDC


oidc = OIDC(
    issuer="https://identity.example.com/realms/company",
    client_id="evlet-acme",
    client_secret=os.environ["EVLET_OIDC_CLIENT_SECRET"],
    redirect_uri="https://acme.example.com/_/oidc/callback",
    subjects={
        "provider-issued-immutable-subject": "alice",
    },
    label="Company sign-in",
)
```

Python-built applications pass the same declaration as `LocalAuth(..., oidc=oidc)`.
The callback origin must be in `LocalAuth.origins` and belong to an authenticated realm.
The built-in desk login is `/login`; custom frontends must implement the finish flow below.
One provider and callback origin are configured per instance. There is no dynamic issuer,
return URL, email linking, account provisioning, role synchronization, or provider-token API.
A public PKCE client may omit `client_secret` when the provider explicitly advertises the
`none` token authentication method; confidential clients use `client_secret_basic`.

Use HTTPS in production. HTTP issuer and callback URLs are allowed only for local loopback
development. Keep an active local administrator and its local password/two-step recovery
codes available for identity-provider outages. Store the client secret outside source
control, protect instance configuration, and restart after changing mappings or credentials.

## Provider and session boundaries

The provider must publish OIDC discovery, authorization-code response support, S256 PKCE,
and an RSA/EC signing JWKS. Authlib and its maintained JOSE dependency validate the signed
ID token with only RS256/ES256 permitted, issuer, audience/authorized party, expiry, issue
time and nonce. Tokens, authorization codes and client secrets are never sent to the UI or
written to application logs/database. The access token is used only to validate an optional
ID-token hash, then discarded; there is no refresh token storage or UserInfo request.

Each attempt has a five-minute one-use state record plus a host-only HttpOnly SameSite=Lax
browser cookie. State is consumed before code exchange. The validated identity becomes a
one-use, browser-bound 60-second finish receipt. Finishing rechecks local account activity,
realm and revocation generation before issuing the normal session or local MFA challenge.
The provider's own MFA does not bypass local MFA or Controls step-up.

Disabling an Evlet account or revoking its sessions invalidates pending finish receipts and
existing sessions. Disabling the account **only at the provider** prevents future provider
logins; it does not revoke already-issued Evlet sessions. Revoke them in Evlet too. This
initial integration does not implement back-channel logout or SCIM synchronization.

## HTTP flow and limits

- `GET /api/auth/session` advertises optional `oidc: {label, start}` on the configured origin.
- Navigate to `GET /_/oidc/start`; the server sets its cookie and redirects to the provider.
- `GET /_/oidc/callback` consumes the provider response and redirects to `/login?oidc=finish`
  or `/login?oidc=error`. These markers contain no credentials. The UI removes the marker.
- `POST /_/oidc/finish` requires same-origin cookies, the exact `Origin`,
  `X-Evlet-Request: 1`, and an empty JSON object. Its answer is the existing login result or
  two-step challenge; complete two-step through `POST /api/auth/login` as usual.

Provider HTTP requests refuse redirects and environment proxies. Each has a ten-second
socket timeout and a response watchdog, and accepts at most 128 KiB. DNS resolution follows
the host operating system's resolver timeout. Four exchanges per instance/process run at
once; further callers get 503. Discovery/JWKS caches last five minutes; key-refresh retries
are bounded to once per thirty seconds. One hundred starts per client network per fifteen
minutes and 5,000 unexpired attempts per instance bound storage and repeated discovery.
No optional OIDC tables or imports are created by disabled instances. Existing local sign-in
continues during provider failure.

Provider URLs come only from the operator-configured issuer and its discovery metadata;
no user-supplied URL is fetched. Treat that issuer as trusted infrastructure. Discovery
endpoints must use HTTPS, or loopback HTTP in the explicit local development configuration.
Reverse proxies generated by Evlet expose exactly the three OIDC paths; never expose other
private `/_/` routes to enable sign-in. Evlet removes callback query strings before Uvicorn
access logging and generated nginx proxies suppress callback access logs. Configure any
other reverse proxy or tracing service to omit callback query strings as well.

The extra adds Authlib (OAuth, PKCE, OIDC claims) and its JOSE implementation. The default
Evlet install and browser bundle gain no required authentication dependency. Upgrade the
extra with normal dependency/advisory checks; tests use a local signed-token IdP without
external credentials.
