# Azure OAuth MCP — a remote MCP server on Azure Functions, secured with Entra

Connect Claude directly to your Azure subscription. No local proxy. No
service-principal secret in a config file. You paste one URL, log in with any
work or school Microsoft account, and the tools work.

This is the OAuth port of `azure-serverless-mcp`, which kept a local proxy that
authenticated with a **long-lived service-principal client secret embedded in
the Claude Desktop config** — one static credential for all access, sitting in a
JSON file. This version deletes it.

| | Proxy build | This build |
|---|---|---|
| Client setup | Install proxy, edit JSON config, hold a secret | Paste one URL |
| Credential on disk | SP client secret, never expires | None |
| Who is the caller? | Always the same service principal | The actual human, via Entra |
| Auth enforced by | The code (in-code JWT) | The code (in-code JWT) |
| Identity model | one tenant, one SP | any work/school tenant, real users |

---

## Architecture

```
Claude (claude.ai / Claude Desktop)
     │  1. probe:     POST /mcp with no token → 401 + WWW-Authenticate
     │  2. discover:  GET  /.well-known/oauth-authorization-server   (RFC 8414)
     │  3. register:  POST /oauth/register                           (RFC 7591)
     │  4. login:     GET  /authorize → login.microsoftonline.com → /oauth/callback
     │  5. token:     POST /oauth/token
     │  6. use:       POST /mcp  (Bearer <entra access token>)
     ▼
Azure Function (Flex Consumption) — one function, public, auth enforced in code
     ├── oauth.py   OAuth broker  ── Cosmos DB (transient login state, 5-min TTL)
     ├── mcp.py     JSON-RPC; validates the Bearer token against Entra's JWKS
     └── tools.py   7 Resource Graph tools, called in-process
                    ▼
     Azure Resource Graph API   (Managed Identity, subscription Reader)
```

The function plays **two roles at once**. To Claude it *is* the OAuth
authorization server. To Microsoft Entra it is an ordinary OAuth *client*.

### Why a broker, and not just "point Claude at Entra"?

Two gaps, neither ours to fix upstream:

1. **claude.ai's redirect URI** isn't one Entra will accept, and Entra requires
   exact-match redirect URIs. The broker registers only its own fixed
   `/oauth/callback` and carries Claude's URL through Cosmos.
2. **Entra has no dynamic client registration** (RFC 7591). Without a
   `/oauth/register` endpoint, Claude has no `client_id` — and the user ends up
   pasting a client ID and secret by hand. That is the exact gap AWS AgentCore
   and Google leave open too. No cloud closes it for you.

### Multitenant — "sign in with any Microsoft work account"

The Entra app is registered `AzureADMultipleOrgs` and the broker uses the
`/organizations` authority, so a user from **any** work or school Entra tenant
can sign in. Token validation pins the **audience** (this app) but not the
**issuer** — because with many tenants there is no single issuer to pin.

---

## The tools

| Tool | Operation |
|---|---|
| `list_virtual_machines` | All VMs with size, resource group, location |
| `list_resource_groups` | All RGs with location and tag count |
| `count_resources_by_type` | Ranked inventory summary |
| `find_resources_by_tag` | Resources matching a tag key+value |
| `list_public_ip_addresses` | All public IPs with allocation method |
| `find_resources_by_resource_group` | Resources in a named RG |
| `find_resources_by_region` | Resources in a named region |

Responses are pre-formatted plain text — Resource Graph returns nested JSON, and
the model narrates a text table better than it parses one.

---

## Prerequisites

- `az`, `terraform`, `jq`, `zip` in PATH
- An Azure subscription, and a service principal with rights to create the
  resources below (Contributor + the ability to create Entra app registrations)
- Environment variables: `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`,
  `ARM_SUBSCRIPTION_ID`, `ARM_TENANT_ID`

---

## Deploy

```bash
./apply.sh     # full deploy + smoke test + prints the connector URL
./validate.sh  # re-run the handshake / auth-boundary checks
./destroy.sh   # tear it down (Key Vault purged)
```

**There is no console step.** Terraform creates the Entra app registration,
exposes its API scope, wires its redirect URI to the function's own hostname,
and stores its secret in Key Vault. `apply.sh` prints the `/mcp` URL when it
finishes.

Then in Claude: **Settings → Connectors → Add custom connector**, paste the URL.
Claude discovers the authorization server, registers itself, and sends you to
Microsoft to log in. That is the entire configuration.

---

## Security — what this does and does not do

**It authenticates. It does not authorize.**

Every authenticated Microsoft work/school user is authorized — from *any*
tenant, because the app is multitenant. There is no allow-list. That is an
acceptable trade in a demo and a bad one anywhere else — to lock it down, filter
on the `tid` (tenant) or `preferred_username` claim in `mcp._resolve_user`.

Three things this build gets right:

**The token's audience is pinned.** `_resolve_user` rejects any token whose
`aud` is not this app (`client_id` or `api://client_id`). Without it, a valid
Entra token minted for a *different* app would pass — the multitenant JWKS
validates the signature regardless of which app requested the token.

**Both secrets live in Key Vault** — the Entra client secret and the Cosmos
connection string — as `@Microsoft.KeyVault(...)` references, not plaintext app
settings.

**The function is public, and that is correct.** The OAuth endpoints cannot
require a token, and Claude probes `/mcp` unauthenticated on purpose to read the
`WWW-Authenticate` pointer. So the door opens, and `mcp.py` enforces the token.

---

## Gotchas

- **`host.json` sets `routePrefix: ""`** so the routes are at the root. With the
  default `/api` prefix, MCP discovery breaks.
- **Don't pin the issuer** in token validation — the app is multitenant.
- **Key Vault soft-delete** — `destroy.sh` purges the vault so a re-apply
  doesn't hit a name conflict.
- **Entra token audience** can be the client_id GUID or `api://<guid>` depending
  on configuration; the validator accepts both.
