#Azure #MCP #AzureFunctions #Serverless #Terraform

*Build a Serverless MCP Backend using Azure Functions*

How do you expose serverless Azure tools to any AI client — securely, without managing servers, and without hardcoding anything in the proxy?

In this project we build a reusable MCP backend pattern on Azure: Function handlers behind an HTTP API with Entra ID Bearer token authorization, bridged to any MCP client by a lightweight stdio proxy that acquires and caches tokens automatically.

The proxy makes the remote Azure backend look like a local tool server. The AI never knows the difference. We use Azure Resource Graph as the example backend — but the pattern works for any Function-backed tool set.

The proxy itself contains zero tool-specific logic. It self-configures at startup by calling a /tools discovery endpoint, so you can add or remove tools without touching the proxy at all. Point it at a different endpoint and you have a completely different tool set.

This pattern works with Claude Desktop, OpenAI Codex, Cursor, and any other MCP client that supports stdio transport.

WHAT YOU'LL LEARN
• The serverless MCP backend pattern — how to make remote Azure Functions appear local to any AI client
• Writing a stdio MCP proxy in Bash (and PowerShell) that acquires and caches Entra ID Bearer tokens via the client-credentials flow
• Securing Azure Functions with in-code JWT validation (RS256, Azure AD JWKS) — required because FC1 Flex Consumption does not support Easy Auth
• Applying Managed Identity — the function queries Resource Graph without credentials in code or app settings
• Building a self-configuring /tools discovery endpoint so the proxy never needs hardcoded tool definitions
• Deploying two Entra app registrations (API audience + proxy service principal) with Terraform and the azuread provider

INFRASTRUCTURE DEPLOYED
• Azure Functions FC1 (Flex Consumption) — 8 Python 3.11 handlers, scales to zero when idle (unsigned requests rejected before any function runs)
• 2 Entra app registrations: serverless-mcp-api (token audience) + serverless-mcp-proxy (proxy service principal with client secret)
• System-Assigned Managed Identity on the Function App with Reader role on the subscription — no credentials in code
• Storage account for Function App code container
• Application Insights for function telemetry
• MCP proxy (proxy.sh / proxy.ps1) — generic stdio bridge with Bearer token management, zero tool-specific logic

GitHub
https://github.com/mamonaco1973/azure-serverless-mcp

README
https://github.com/mamonaco1973/azure-serverless-mcp/blob/main/README.md

TIMESTAMPS
00:00 Introduction
00:17 Architecture
01:09 Build the Code
01:25 Build Results
02:10 Demo
