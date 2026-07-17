# CLAUDE.md — azure-oauth-mcp

An Azure Resource Graph API exposed as a **remote MCP connector** secured with
**Microsoft Entra OAuth**. Claude connects directly to a remote `/mcp` endpoint,
the user logs in with any work or school Microsoft account, and the tools run —
**no local proxy, no service-principal secret on your laptop, nothing to
configure but a URL.**

> This is the OAuth port of `azure-serverless-mcp`, which kept a local proxy that
> authenticated with a long-lived service-principal client secret embedded in the
> Claude Desktop config. The OAuth broker pattern matches `aws-cognito-mcp` and
> `gcp-oauth-mcp`, with Entra as the upstream IdP.

---

## What This Project Does

One Azure Function (Flex Consumption, Python 3.11) is the whole stack. It serves
the OAuth authorization-server endpoints **and** the MCP JSON-RPC endpoint, and
calls the seven Resource Graph tools in-process on `tools/call`.

| Tool | Operation |
|---|---|
| list_virtual_machines | All VMs with size, resource group, location |
| list_resource_groups | All RGs with location and tag count |
| count_resources_by_type | Ranked inventory summary |
| find_resources_by_tag | Resources matching a tag key+value |
| list_public_ip_addresses | All public IPs with allocation method |
| find_resources_by_resource_group | Resources in a named RG |
| find_resources_by_region | Resources in a named region |

---

## Architecture

```
Claude (claude.ai / Claude Desktop) — remote MCP client
     │  1. probe:     POST /mcp with no token → 401 + WWW-Authenticate
     │  2. discover:  GET  /.well-known/oauth-authorization-server   (RFC 8414)
     │  3. register:  POST /oauth/register                           (RFC 7591)
     │  4. login:     GET  /authorize → login.microsoftonline.com → /oauth/callback
     │  5. token:     POST /oauth/token   (az_ code → Entra access token)
     │  6. use:       POST /mcp  (Authorization: Bearer <entra access token>)
     ▼
Azure Function (Flex Consumption) — PUBLIC (AuthLevel.ANONYMOUS); code enforces auth
     ├── function_app.py  route table (7 @app.route)
     ├── oauth.py         OAuth broker  ── Cosmos DB (transient login state, TTL)
     ├── mcp.py           JSON-RPC; validates Bearer via Entra JWKS (audience-pinned)
     └── tools.py         7 Resource Graph tools, called in-process
                    │  DefaultAzureCredential → System-Assigned Managed Identity
                    ▼
     Azure Resource Graph API   (subscription Reader)
```

### Why the routes live at the root

`host.json` sets `extensions.http.routePrefix = ""`. Azure Functions default to
an `/api` prefix, but MCP discovery and the `.well-known` documents must be at
the root, so the prefix is removed. The connector URL is
`https://<app>.azurewebsites.net/mcp`.

### The two gaps the broker exists to close

1. **claude.ai's redirect_uri** is not one Entra will accept, and Entra requires
   an exact allow-list match. The broker registers only its own fixed
   `/oauth/callback` and carries Claude's URL through Cosmos.
2. **Entra does not implement RFC 7591** dynamic client registration. Without
   `/oauth/register`, Claude has no `client_id` — same gap as AWS AgentCore and
   Google. The `/oauth/register` shim closes it.

The token handed to Claude is a **genuine Entra access token**. No custom crypto.

---

## Auth model — read this before changing anything

**The function is public** (`AuthLevel.ANONYMOUS`), and it always was — FC1 has
no Easy Auth, so this project never had platform-enforced auth to invert (unlike
the AWS and GCP ports). Auth is in `mcp.py`:

- The OAuth endpoints **cannot** require a token — obtaining one is their job.
- Claude probes `/mcp` **unauthenticated on purpose**, to read the
  `WWW-Authenticate` header that points at the login.

**Token validation pins the audience, not the issuer.** `mcp._resolve_user`
verifies the RS256 signature against Entra's **common** JWKS and requires
`aud ∈ {client_id, api://client_id}`. It deliberately does **not** pin the
issuer: the app is **multitenant** (`AzureADMultipleOrgs`), so tokens arrive
from many tenants with different issuers. The audience pin is what stops a token
minted for some *other* Entra app from calling these tools.

**AuthN only, no authZ.** Every authenticated Microsoft work/school user is
authorized — from *any* tenant. That is fine for a demo; it is not fine for
anything real. To lock it down, filter on the `tid` (tenant) or
`preferred_username` claim in `_resolve_user`.

---

## Repository Layout

```
01-functions/
  code/
    function_app.py  Router: 7 @app.route wrappers (OAuth + MCP) + CORS
    oauth.py         OAuth broker: discovery, DCR, authorize, callback, token
    mcp.py           MCP JSON-RPC; Bearer validation; tools/call dispatch
    tools.py         TOOL_REGISTRY + 7 Resource Graph handlers + TOOL_FUNCTIONS
    host.json        routePrefix "" so routes are at root
    requirements.txt azure-functions, resourcegraph, identity, cosmos, PyJWT
  main.tf            Providers; resource group; random suffix
  entra.tf           ONE multitenant Entra app: scope, redirect, secret
  functions.tf       Storage, FC1 plan, Function App, MI, Key Vault app settings
  keyvault.tf        Key Vault + access policies + the two secrets
  cosmosdb.tf        Serverless Cosmos account/db/container (TTL 300)
  rbac.tf            Reader on the subscription for the MI
  outputs.tf         mcp_url, function_base_url, entra_client_id, oauth_redirect_uri
check_env.sh         Pre-flight: az/terraform/jq/zip + ARM_ vars; az login
apply.sh             Deploy + code push + validate + print connector URL
destroy.sh           Teardown (Key Vault purged)
validate.sh          Unauthenticated smoke test of handshake + auth boundary
```

---

## The Entra app (one registration, created by Terraform)

Unlike GCP — where the OAuth client is a manual console step — **Terraform
creates the whole Entra app** and wires its redirect URI to the function's own
hostname. There is no Azure Portal step.

- `sign_in_audience = "AzureADMultipleOrgs"` — any work/school tenant.
- Exposes an API scope `api://<client_id>/mcp.access`, `requested_access_token_version = 2`.
- `web.redirect_uris` = the function's `/oauth/callback`.
- A client secret → Key Vault (never a plaintext app setting).

The redirect URI is built from the **random suffix**, not the function resource,
to avoid a dependency cycle (functions.tf reads the app's client_id in turn).

---

## Environment variables (function)

| Var | Source | Used by |
|-----|--------|---------|
| `SUBSCRIPTION_ID` | current subscription | tools.py |
| `MCP_ENTRA_CLIENT_ID` | Entra app client_id | oauth.py + mcp.py (audience check) |
| `MCP_ENTRA_CLIENT_SECRET` | Key Vault reference | oauth.py |
| `COSMOS_CONNECTION` | Key Vault reference | oauth.py |
| `COSMOS_DB` / `COSMOS_CONTAINER` | Cosmos names | oauth.py |

Both secrets are `@Microsoft.KeyVault(SecretUri=...)` references, materialized at
startup — the portal shows the reference, not the value.

---

## Gotchas that have bitten

- **routePrefix must be empty.** Leave the default `/api` and MCP discovery
  breaks — the `.well-known` docs would be under `/api`, where no client looks.
- **Audience may be the GUID or `api://<guid>`.** v2 tokens for a custom scope
  usually carry the client_id GUID; `_resolve_user` accepts both forms.
- **Do not pin the issuer.** The app is multitenant; pinning one issuer breaks
  every tenant but your own.
- **Key Vault soft-delete.** `destroy.sh` purges the vault (main.tf enables
  `purge_soft_delete_on_destroy`) so a re-apply doesn't hit a name conflict.
- **Don't attach Easy Auth / an authorizer to `/mcp`.** Same lesson as the
  siblings: the flow breaks before a token exists.
- The `.drawio` / `.png` diagrams and `00-resources/` still depict the old
  proxy design — regenerate before reusing them.

## Code Commenting Standards

See the workspace-root `.claude/CLAUDE.md`: comment the *why*, not the *what*;
`# ===` section headers; inline comments only for non-obvious intent.
