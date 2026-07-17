# ==============================================================================
# oauth.py
#
# OAuth 2.0 authorization-server broker for the MCP connector.
#
# This function plays two roles at once:
#   * To Claude, it IS the authorization server — it serves discovery, dynamic
#     client registration, /authorize and /oauth/token.
#   * To Microsoft Entra, it is an ordinary OAuth *client* — it redirects to the
#     Entra login, receives the code at a fixed callback, and exchanges it.
#
# Why broker at all? Two gaps, and neither is ours to fix upstream:
#   1. claude.ai's redirect_uri is not one Entra will let us register freely, and
#      Entra requires an exact allow-list match. We register only our own fixed
#      /oauth/callback and carry Claude's URL through Cosmos, keyed by `state`.
#   2. Entra does not implement RFC 7591 dynamic client registration. Without
#      /oauth/register, Claude would have no client_id and the user would have to
#      paste credentials by hand.
#
# The multitenant angle: the Entra app is registered
# AzureADandPersonalMicrosoftAccount and we use the /common authority, so ANY
# Microsoft account — work, school, or personal — can sign in. We request only
# OIDC scopes (personal accounts cannot consent to a custom api:// scope) and
# hand Claude the id_token. mcp.py pins the token audience (this client_id) but
# deliberately does not pin the issuer — that is what "any Microsoft account"
# means.
#
# Flow:
#   1. GET  /.well-known/oauth-authorization-server — we are the auth server
#   2. POST /oauth/register  — hand back our shared client_id (RFC 7591)
#   3. GET  /authorize       — stash Claude's redirect_uri + state, 302 to Entra
#   4. GET  /oauth/callback  — Entra returns here; swap code for tokens, mint a
#                              one-time az_ code, 302 back to Claude
#   5. POST /oauth/token     — az_ code → Entra access token (+ refresh token)
#   6. POST /mcp             — Bearer is a real Entra access token, validated in
#                              mcp.py against the Entra JWKS with an audience pin
#
# The token handed to Claude is a genuine Entra access token. We mint no JWTs and
# hold no signing keys — there is no custom crypto in this file.
#
# Cosmos records (container TTL sweeps them; also deleted on use):
#   id=<session>   kind=pending  — Claude's redirect_uri + state, pre-login
#   id=az_<code>   kind=code     — Entra tokens, one-time use, post-login
# ==============================================================================

import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request

from azure.cosmos import CosmosClient

logger = logging.getLogger(__name__)

# Entra endpoints. /common = any Microsoft account (work, school, personal).
# Fixed, public, not user-controlled — the urlopen calls are safe.
AUTHORITY        = "https://login.microsoftonline.com/common"
ENTRA_AUTH_URL   = f"{AUTHORITY}/oauth2/v2.0/authorize"
ENTRA_TOKEN_URL  = f"{AUTHORITY}/oauth2/v2.0/token"

# OIDC scopes only. offline_access yields a refresh token; the id_token this
# returns has aud = our client_id, which is what mcp.py validates. No custom
# api:// scope, because personal Microsoft accounts cannot consent to one.
OAUTH_SCOPE = "openid profile offline_access"

PENDING_TTL_SECONDS = 300   # 5 minutes, for both pending-auth and auth-code docs

_cosmos_container = None


def _client_id() -> str:
    return os.environ.get("MCP_ENTRA_CLIENT_ID", "")


def _client_secret() -> str:
    return os.environ.get("MCP_ENTRA_CLIENT_SECRET", "")


def _container():
    """Cosmos container holding transient login state.

    Connection string comes from a Key Vault reference in app settings, so the
    secret is never a plaintext environment variable in the portal.
    """
    global _cosmos_container
    if _cosmos_container is None:
        client = CosmosClient.from_connection_string(
            os.environ["COSMOS_CONNECTION"]
        )
        db = client.get_database_client(os.environ.get("COSMOS_DB", "oauthdb"))
        _cosmos_container = db.get_container_client(
            os.environ.get("COSMOS_CONTAINER", "state")
        )
    return _cosmos_container


# ==============================================================================
# Response helpers — (body, status, headers) tuples; function_app.py adapts them
# ==============================================================================

def _json(body: dict, status: int = 200):
    return (json.dumps(body), status, {"Content-Type": "application/json"})


def _error(msg: str, status: int = 400):
    return _json({"error": msg}, status)


def _redirect(location: str):
    return ("", 302, {"Location": location})


def _api_base(req) -> str:
    """Public base URL of this function, from the incoming request host.

    Azure terminates TLS upstream, so hard-code https rather than trust scheme.
    """
    host = req.headers.get("host", "")
    return f"https://{host}"


def _expiry_epoch() -> int:
    return int(time.time()) + PENDING_TTL_SECONDS


def _is_expired(doc: dict) -> bool:
    return int(time.time()) > int(doc.get("expires_at", 0))


def _post_form(url: str, fields: dict) -> dict:
    """POST a form-encoded body and parse the JSON response.

    Args:
        url:    Target endpoint (always a fixed Entra URL — see module header).
        fields: Form fields to send.

    Returns:
        Parsed JSON response, or {} on any failure.
    """
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:  # nosec B310
            return json.loads(resp.read())
    except Exception:
        logger.exception("Token request to %s failed", url)
        return {}


def parse_form_body(raw: str) -> dict:
    """Parse an OAuth request body as form-encoded, falling back to JSON."""
    raw = raw or ""
    if raw.lstrip().startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


# ==============================================================================
# Discovery — GET /.well-known/oauth-authorization-server  (RFC 8414)
# ==============================================================================

def authorization_server_metadata(req):
    """Advertise this function as the OAuth authorization server.

    Every endpoint points at us, not at Entra. Claude never learns Entra is
    behind the curtain, which is what lets us paper over Entra's missing dynamic
    client registration.
    """
    base = _api_base(req)
    return _json({
        "issuer":                                base,
        "authorization_endpoint":                f"{base}/authorize",
        "token_endpoint":                        f"{base}/oauth/token",
        "registration_endpoint":                 f"{base}/oauth/register",
        "grant_types_supported":                 ["authorization_code",
                                                  "refresh_token"],
        "response_types_supported":              ["code"],
        "scopes_supported":                      ["openid", "profile"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


# ==============================================================================
# Protected-resource metadata — GET /.well-known/oauth-protected-resource
# ==============================================================================

def protected_resource_metadata(req):
    """Point the MCP client at the authorization server guarding /mcp."""
    base = _api_base(req)
    return _json({
        "resource":              base,
        "authorization_servers": [base],
        "scopes_supported":      ["openid", "profile"],
    })


# ==============================================================================
# Dynamic client registration — POST /oauth/register  (RFC 7591)
# ==============================================================================

def register(req):
    """Hand back the shared client_id so Claude can self-register.

    Entra has no DCR endpoint of its own, so this is the shim that keeps the user
    experience to "paste a URL". We return auth method "none": the real Entra
    client secret stays server-side and is never sent to the client.
    """
    base = _api_base(req)
    return _json({
        "client_id":                  _client_id(),
        "token_endpoint_auth_method": "none",
        "grant_types":                ["authorization_code", "refresh_token"],
        "response_types":             ["code"],
        "redirect_uris":              [f"{base}/oauth/callback"],
    }, status=201)


# ==============================================================================
# Authorization — GET /authorize
# ==============================================================================

def authorize(req):
    """Stash Claude's callback details, then send the browser to Entra.

    Entra only ever sees our own fixed /oauth/callback as the redirect_uri.
    Claude's URL rides along in Cosmos, keyed by the session id we pass to Entra
    as `state`.
    """
    params        = req.params
    redirect_uri  = (params.get("redirect_uri")  or "").strip()
    state         = params.get("state")          or ""
    response_type = params.get("response_type")  or ""

    if response_type != "code":
        return _error("unsupported_response_type", 400)
    if not redirect_uri:
        return _error("invalid_request", 400)

    # PKCE params from Claude are accepted and ignored: the code we hand back is
    # single-use and consumed server-side, so there is no interception window
    # for PKCE to close. The Entra leg is protected by the client secret.
    session_id = secrets.token_urlsafe(16)
    _container().upsert_item({
        "id":           session_id,
        "kind":         "pending",
        "redirect_uri": redirect_uri,
        "state":        state,
        "expires_at":   _expiry_epoch(),
    })

    entra_auth = f"{ENTRA_AUTH_URL}?" + urllib.parse.urlencode({
        "client_id":     _client_id(),
        "response_type": "code",
        "scope":         OAUTH_SCOPE,
        "redirect_uri":  f"{_api_base(req)}/oauth/callback",
        "state":         session_id,
        "response_mode": "query",
    })

    logger.info("authorize: session=%s", session_id)
    return _redirect(entra_auth)


# ==============================================================================
# Callback — GET /oauth/callback
# ==============================================================================

def callback(req):
    """Exchange Entra's code for tokens, then hand Claude a one-time code."""
    params      = req.params
    entra_code  = (params.get("code")  or "").strip()
    session_id  = (params.get("state") or "").strip()

    if not entra_code or not session_id:
        return _error("invalid_request", 400)

    container = _container()
    try:
        pending = container.read_item(item=session_id, partition_key=session_id)
    except Exception:
        return _error("invalid_state", 400)

    if _is_expired(pending):
        _safe_delete(container, session_id)
        return _error("invalid_state", 400)

    tokens = _post_form(ENTRA_TOKEN_URL, {
        "grant_type":    "authorization_code",
        "code":          entra_code,
        "client_id":     _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri":  f"{_api_base(req)}/oauth/callback",
        "scope":         OAUTH_SCOPE,
    })

    # We hand Claude the id_token — its audience is our client_id, and unlike a
    # custom-scope access token it is issued uniformly to personal accounts too.
    if "id_token" not in tokens:
        logger.error("Entra token exchange returned no id_token")
        return _error("entra_exchange_failed", 502)

    auth_code = "az_" + secrets.token_urlsafe(32)
    container.upsert_item({
        "id":            auth_code,
        "kind":          "code",
        "bearer":        tokens["id_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_in":    tokens.get("expires_in", 3600),
        "expires_at":    _expiry_epoch(),
    })
    _safe_delete(container, session_id)

    dest     = pending["redirect_uri"]
    sep      = "&" if "?" in dest else "?"
    location = (
        f"{dest}{sep}code={auth_code}"
        f"&state={urllib.parse.quote(pending.get('state', ''), safe='')}"
    )

    logger.info("callback: issued auth code for session=%s", session_id)
    return _redirect(location)


# ==============================================================================
# Token — POST /oauth/token
# ==============================================================================

def token(req, raw_body: str):
    """Issue tokens to Claude.

    Two grants:
      authorization_code — trade the one-time az_ code for the Entra tokens
      refresh_token      — Entra access tokens are short-lived, so refresh is
                           mandatory here (the Cognito build could skip it).
    """
    params     = parse_form_body(raw_body)
    grant_type = params.get("grant_type", "")

    if grant_type == "refresh_token":
        return _refresh(params)
    if grant_type != "authorization_code":
        return _error("unsupported_grant_type", 400)

    code = (params.get("code") or "").strip()
    if not code:
        return _error("invalid_request", 400)

    container = _container()
    try:
        doc = container.read_item(item=code, partition_key=code)
    except Exception:
        return _error("invalid_grant", 400)

    # One-time use — burn the code before returning, valid or not.
    _safe_delete(container, code)

    if _is_expired(doc):
        return _error("invalid_grant", 400)

    # No client authentication check: clients registered via /oauth/register use
    # auth method "none". The security boundary is the single-use code above.
    # The bearer we return is the Entra id_token.
    body = {
        "access_token": doc["bearer"],
        "token_type":   "Bearer",
        "expires_in":   doc.get("expires_in", 3600),
    }
    if doc.get("refresh_token"):
        body["refresh_token"] = doc["refresh_token"]

    return _json(body)


def _refresh(params: dict):
    """Exchange an Entra refresh token for a fresh id_token."""
    refresh_token = (params.get("refresh_token") or "").strip()
    if not refresh_token:
        return _error("invalid_request", 400)

    tokens = _post_form(ENTRA_TOKEN_URL, {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     _client_id(),
        "client_secret": _client_secret(),
        "scope":         OAUTH_SCOPE,
    })

    if "id_token" not in tokens:
        return _error("invalid_grant", 400)

    return _json({
        "access_token":  tokens["id_token"],
        "token_type":    "Bearer",
        "expires_in":    tokens.get("expires_in", 3600),
        # Entra may or may not rotate the refresh token; echo whichever we hold.
        "refresh_token": tokens.get("refresh_token", refresh_token),
    })


def _safe_delete(container, item_id: str) -> None:
    try:
        container.delete_item(item=item_id, partition_key=item_id)
    except Exception:
        # Already gone (TTL swept it, or a concurrent request consumed it).
        pass
