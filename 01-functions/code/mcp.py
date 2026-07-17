# ==============================================================================
# mcp.py
#
# The MCP endpoint: JSON-RPC 2.0 over streamable HTTP at POST /mcp.
#
# Auth is enforced here, in code. The Function App runs at AuthLevel.ANONYMOUS
# because the OAuth handshake and Claude's first unauthenticated /mcp probe both
# have to reach us before any token exists. (On FC1 there is no Easy Auth to
# enforce it anyway — so unlike the AWS and GCP ports, there was no platform auth
# to invert. It was always in-code here.)
#
# Token validation, multitenant edition:
#   * Signature — verified against Entra's common JWKS.
#   * Audience  — MUST be this app (client_id GUID or api://client_id). This is
#     what stops a token minted for some other Entra app from calling our tools.
#   * Issuer    — deliberately NOT pinned. The app is multitenant, so tokens
#     arrive from many tenants, each with a different issuer. Pinning one issuer
#     would defeat "sign in with any work or school account".
#
# Methods handled:
#   initialize                 — capability handshake
#   notifications/initialized  — client ack, no response body
#   tools/list                 — TOOL_REGISTRY from tools.py
#   tools/call                 — dispatch to a Python callable in TOOL_FUNCTIONS
# ==============================================================================

import json
import logging
import os

import jwt
from jwt.algorithms import RSAAlgorithm
import requests

import tools

logger = logging.getLogger(__name__)

# Common keys endpoint — serves signing keys usable across all tenants for v2
# tokens, which is what a multitenant app needs.
JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"

SERVER_NAME      = "azure-resource-mcp"
SERVER_VERSION   = "2.0.0"
DEFAULT_PROTOCOL = "2025-06-18"

_jwks_cache = None


def _get_jwks() -> dict:
    """Fetch and cache Entra's JWKS for signature validation."""
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = requests.get(JWKS_URL, timeout=10).json()
    return _jwks_cache


# ==============================================================================
# Authentication
# ==============================================================================

def _resolve_user(token: str) -> dict:
    """Validate an Entra access token and return its claims.

    Args:
        token: The raw Bearer token from the Authorization header.

    Returns:
        The token claims, or {} if the token is invalid, expired, or was issued
        to a different application.
    """
    client_id = os.environ.get("MCP_ENTRA_CLIENT_ID", "")
    # v2 access tokens for our custom scope carry aud = client_id GUID; some
    # configurations carry the App ID URI. Accept either — both mean "us".
    allowed_aud = [client_id, f"api://{client_id}"]
    try:
        header   = jwt.get_unverified_header(token)
        jwks     = _get_jwks()
        key_data = next(
            (k for k in jwks["keys"] if k["kid"] == header.get("kid")), None
        )
        if key_data is None:
            return {}
        public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
        # audience pinned; issuer intentionally not verified (multitenant).
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=allowed_aud,
            options={"verify_iss": False},
        )
        return claims
    except Exception:
        logger.info("Token validation failed")
        return {}


def _get_auth_user(auth_header: str) -> dict:
    """Extract and validate the Bearer token from the Authorization header."""
    if not auth_header.lower().startswith("bearer "):
        return {}
    return _resolve_user(auth_header[7:].strip())


def unauthorized(base_url: str):
    """401 with the RFC 9728 pointer to our protected-resource metadata.

    Claude probes /mcp with no token precisely to read this header, so this
    response is part of the happy path, not just an error case.
    """
    resource_metadata = f"{base_url}/.well-known/oauth-protected-resource"
    return (
        json.dumps({"error": "unauthorized"}),
        401,
        {
            "Content-Type":     "application/json",
            "WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata}"',
        },
    )


# ==============================================================================
# JSON-RPC helpers
# ==============================================================================

def _result(req_id, result):
    return (
        json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}),
        200,
        {"Content-Type": "application/json"},
    )


def _rpc_error(req_id, code, message):
    return (
        json.dumps({
            "jsonrpc": "2.0",
            "id":      req_id,
            "error":   {"code": code, "message": message},
        }),
        200,
        {"Content-Type": "application/json"},
    )


# ==============================================================================
# Entry point — POST /mcp
# ==============================================================================

def handle(auth_header: str, raw_body: str, base_url: str):
    """Handle one MCP JSON-RPC request.

    Args:
        auth_header: The request's Authorization header value.
        raw_body:    The raw request body (a JSON-RPC 2.0 message).
        base_url:    This function's public base URL, for the 401 pointer.

    Returns:
        (body, status, headers) tuple. 401 when unauthenticated.
    """
    claims = _get_auth_user(auth_header)
    if not claims:
        return unauthorized(base_url)

    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        payload = {}

    method = payload.get("method", "")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    user = claims.get("preferred_username") or claims.get("oid", "unknown")
    logger.info("mcp: user=%s method=%s", user, method)

    # Notifications carry no id and expect no response body.
    if req_id is None and method.startswith("notifications/"):
        return ("", 202, {})

    if method == "initialize":
        # Echo the client's protocol version so a client on a different revision
        # of the spec does not give up on us.
        protocol = params.get("protocolVersion", DEFAULT_PROTOCOL)
        return _result(req_id, {
            "protocolVersion": protocol,
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name":    SERVER_NAME,
                                "version": SERVER_VERSION},
        })

    if method == "tools/list":
        return _result(req_id, {"tools": tools.TOOL_REGISTRY})

    if method == "tools/call":
        return _call_tool(req_id, params)

    return _rpc_error(req_id, -32601, f"Method not found: {method}")


def _call_tool(req_id, params: dict):
    """Dispatch tools/call to the matching Python callable, in-process."""
    name = params.get("name", "")
    args = params.get("arguments") or {}

    handler = tools.TOOL_FUNCTIONS.get(name)
    if handler is None:
        return _rpc_error(req_id, -32602, f"Unknown tool: {name}")

    try:
        text = handler(args)
    except tools.ToolInputError as exc:
        return _rpc_error(req_id, -32602, str(exc))
    except Exception:
        logger.exception("Tool %s failed", name)
        # Deliberately generic: exception text from the Azure SDKs can carry
        # subscription and resource detail we don't want to hand back.
        return _rpc_error(req_id, -32603, f"Tool {name} failed")

    return _result(req_id, {"content": [{"type": "text", "text": text}]})
