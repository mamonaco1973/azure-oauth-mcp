#Azure #MCP #AzureFunctions #OAuth #ClaudeAI

*Build a Remote MCP Server on Azure with Microsoft Login*

Connect Claude directly to your Azure subscription. No local proxy. No service-principal secret in a config file. You paste one URL, log in with any Microsoft account — work, school, or personal — and the tools work.

In this project we put seven Azure Resource Graph tools behind a single Azure Function App, and secure it with Microsoft Entra. The Function App is public, and it enforces the token itself — the login routes have to be reachable before a token exists, so authentication moves into the code. Claude discovers the login, registers itself, and sends you to Microsoft. Nothing to configure but the URL.

This is the OAuth port of a serverless MCP server that used a local proxy authenticating with a long-lived service-principal client secret embedded in the Claude Desktop config — one static credential for all access, sitting in a JSON file. This version deletes it.

And here is the part that makes Azure stand out: Terraform builds the whole thing in a single apply — including the Entra app you log in through. There is no Azure Portal step at all.

But there is still a catch. Entra does not implement dynamic client registration, so on its own an MCP client has no way to sign itself up. That is the roughly three hundred lines the Function App has to write by hand — and it is the exact gap AWS and Google leave open too. No cloud closes it for you.

We use Azure Resource Graph as the example tool set, but the pattern works for any Function-backed MCP server.

WHAT YOU'LL LEARN
• Exposing an Azure Function App as MCP tools over a remote endpoint Claude connects to directly
• Why the Function App is public, and how authentication is enforced in code instead of by the platform
• Brokering Microsoft Entra OAuth for an MCP client — discovery, dynamic client registration, authorize, callback, token, and refresh
• Signing in with any Microsoft account through a multitenant Entra app on the /common authority
• Why Entra does not serve dynamic client registration, and why the token you validate is the id_token
• The honest comparison — three clouds, and the same RFC none of them will implement for you

INFRASTRUCTURE DEPLOYED
• One Azure Function App (Flex Consumption, Python 3.11) — the OAuth broker, the MCP endpoint, and seven tools, public with auth enforced in code
• Azure Resource Graph access via a System-Assigned Managed Identity with Reader on the subscription — no credentials in code
• A single multitenant Entra app registration — created by Terraform, redirect URI wired to the Function App's own hostname
• Cosmos DB for transient OAuth login state, swept on a short TTL
• Key Vault holding the Entra client secret and the Cosmos connection string — never plaintext app settings
• All provisioned with Terraform in a single apply — no console step — torn down with a single command

GitHub
https://github.com/mamonaco1973/azure-oauth-mcp

README
https://github.com/mamonaco1973/azure-oauth-mcp/blob/main/README.md

TIMESTAMPS
00:00 Introduction
00:39 Architecture
01:35 Securing MCP
02:39 Deploy It Yourself
