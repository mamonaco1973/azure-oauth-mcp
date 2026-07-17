# Video Script — Serverless CRUD API on AWS with Lambda and DynamoDB

---

---

Do you need a clean way to run MCP backends on Azure?

In this project, we implement a reusable MCP pattern using Azure Functions and JWT based authorization.

Follow along, and in minutes you’ll have a working backend that any AI client can use to call your serverless tools on Azure.

---

## Architecture

[ Full diagram ]

"Let's walk through the architecture before we build."

[ Highlight: Claude Desktop ]

We start with the AI client — in this case, Claude Desktop - issuing MCP tool calls over standard JSON-RPC.

[ Highlight: MCP Proxy ]

Those calls are handled by a lightweight MCP proxy.

The proxy acts as a bridge — translating local MCP requests into HTTPS calls.

[ Highlight: Entra ID ]

It uses an Entra ID service principal to acquire a Bearer token, ensuring every request is authenticated before it ever reaches Azure.

[ Highlight: Azure Function App ]

On the backend, each MCP tool is implemented as an Azure Function endpoint.


[ Highlight: Arrow Resouce Graph]

Those functions use a managed identity to query Azure services — in this case, Azure Resource Graph — and return the results.

[ Full diagram highlight ]

So from the AI’s perspective, this looks like a local tool server.

But in reality, every request is securely routed to a serverless backend in Azure.

That’s the core pattern.
---


## Build the Code

[ Terminal — running ./apply.sh ]

"The whole deployment is one script — apply.sh. Two phases."

[ Terminal — Phase 1: Terraform apply in 01-lambdas ]

"Phase one: Terraform provisions DynamoDB, all five Lambda functions, their IAM roles, and the API Gateway — everything wired together with least-privilege permissions."

[ Terminal — API endpoint discovery and envsubst ]

"Between phases, the script looks up the API Gateway endpoint and injects it into the HTML template using envsubst."

[ Terminal — Phase 2: Terraform apply in 02-webapp ]

"Phase two: Terraform creates the S3 bucket and uploads the generated index.html. The site is live."

[ Terminal — validate.sh running smoke tests ]

"Finally, validate.sh runs an end-to-end smoke test — creates five notes, lists them, fetches one, updates it, and deletes it."

[ Terminal — deployment complete, URLs printed ]

"API URL. Website URL. Done."

---

## Build Results

[ Show Function App ]

A serverless Azure Function App is deployed as the entry point for all MCP tool calls.

[ Show Code ]

All routes are secured with an Entra ID Bearer token on every request.

[Show Service Principal ]

A dedicated service principal is created for the proxy to authenticate against the API.

[ Show Proxy Config / Env]

The proxy uses these credentials to acquire and cache tokens for request authentication.

[ Show Functions ]

Multiple Function endpoints are deployed — one per MCP tool, plus a discovery endpoint.

[ Show Python Code ]

 All tool logic is implemented in Python, with each handler querying Azure Resource Graph.

[ Show Tool Registry ]

A central tool registry defines all available tools, which the proxy loads dynamically at startup.

[ Show Desktop JSON ]

 Finally, client configuration files are generated, allowing the MCP client to connect to the backend.
 
---

## Demo

First, update your AI client configuration — here I’m using Claude Desktop.

Restart the client and confirm it recognizes the serverless MCP.

Now let’s try it — show me all my resource groups.

You’ll get a complete list across the subscription.

Next, drill into this project’s resource group.

Now we get a full inventory of everything deployed there.

Finally, ask it to interpret what this resource group is for.

Here it correctly identifies this as an AI assistant backend.
