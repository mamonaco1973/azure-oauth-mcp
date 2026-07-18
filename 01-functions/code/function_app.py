# ==============================================================================
# function_app.py
#
# Azure Functions entry point and router. Dispatches the public HTTP surface to
# oauth.py (the OAuth broker) and mcp.py (the MCP JSON-RPC endpoint).
#
# host.json sets an empty route prefix, so these routes live at the root
# (https://<app>.azurewebsites.net/mcp), not under /api — which is what an MCP
# client and the OAuth .well-known discovery documents expect.
#
# Every route here is reachable without credentials (AuthLevel.ANONYMOUS). That
# is deliberate:
#   * The OAuth routes ARE the authentication — they cannot require a token,
#     because their whole job is to get the user one.
#   * /mcp is probed by the client with no token, on purpose, to read the
#     WWW-Authenticate header that tells it where to log in.
# So auth lives in the code: mcp.handle() validates the Bearer token on every
# call and 401s otherwise. Nothing reaches Resource Graph without a valid Entra
# identity behind it.
#
# HTTP surface:
#   GET  /.well-known/oauth-authorization-server  — RFC 8414 discovery
#   GET  /.well-known/oauth-protected-resource    — RFC 9728 resource metadata
#   POST /oauth/register                          — RFC 7591 registration
#   GET  /authorize                               — redirect to Entra login
#   GET  /oauth/callback                          — Entra returns here
#   POST /oauth/token                             — code / refresh → tokens
#   POST /mcp                                     — MCP JSON-RPC (auth required)
# ==============================================================================

import azure.functions as func

import mcp
import oauth

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_CORS = {
    "Access-Control-Allow-Origin":   "*",
    "Access-Control-Allow-Methods":  "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers":  "Content-Type, Authorization",
    "Access-Control-Expose-Headers": "WWW-Authenticate",
}


def _respond(result) -> func.HttpResponse:
    """Adapt an (body, status, headers) tuple into a CORS-tagged response."""
    body, status, headers = result
    merged = dict(_CORS)
    merged.update(headers)
    return func.HttpResponse(body, status_code=status, headers=merged)


def _preflight() -> func.HttpResponse:
    return func.HttpResponse("", status_code=204, headers=_CORS)


def _base_url(req: func.HttpRequest) -> str:
    return f"https://{req.headers.get('host', '')}"


# ------------------------------------------------------------------------------
# OAuth broker routes 
# ------------------------------------------------------------------------------

@app.route(route=".well-known/oauth-authorization-server", methods=["GET", "OPTIONS"])
def as_metadata(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _preflight()
    return _respond(oauth.authorization_server_metadata(req))


@app.route(route=".well-known/oauth-protected-resource", methods=["GET", "OPTIONS"])
def pr_metadata(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _preflight()
    return _respond(oauth.protected_resource_metadata(req))


@app.route(route="oauth/register", methods=["POST", "OPTIONS"])
def oauth_register(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _preflight()
    return _respond(oauth.register(req))


@app.route(route="authorize", methods=["GET", "OPTIONS"])
def authorize(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _preflight()
    return _respond(oauth.authorize(req))


@app.route(route="oauth/callback", methods=["GET", "OPTIONS"])
def oauth_callback(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _preflight()
    return _respond(oauth.callback(req))


@app.route(route="oauth/token", methods=["POST", "OPTIONS"])
def oauth_token(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _preflight()
    raw = req.get_body().decode("utf-8", errors="replace")
    return _respond(oauth.token(req, raw))


# ------------------------------------------------------------------------------
# MCP endpoint
# ------------------------------------------------------------------------------

@app.route(route="mcp", methods=["POST", "GET", "OPTIONS"])
def mcp_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _preflight()
    # A GET on /mcp is not a real method, but clients try it during discovery.
    # Answer with the same 401 + WWW-Authenticate pointer rather than a 404.
    if req.method == "GET":
        return _respond(mcp.unauthorized(_base_url(req)))
    auth = req.headers.get("Authorization", "")
    raw  = req.get_body().decode("utf-8", errors="replace")
    return _respond(mcp.handle(auth, raw, _base_url(req)))
