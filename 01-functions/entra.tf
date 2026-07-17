# ==============================================================================
# Entra App Registration — one multitenant OAuth app
#
# This single registration plays both roles the broker needs:
#   * The confidential client the function authenticates AS to Entra (it has a
#     secret and a redirect URI).
#   * The resource/audience the tokens are issued FOR (it exposes an API scope,
#     and mcp.py validates that the token's audience is this app).
#
# The proxy build had two registrations (an API audience + a client-credentials
# caller). Collapsing to one is what makes the OAuth flow a real user login
# instead of a service-principal secret. The proxy app and its long-lived
# secret are gone.
#
# sign_in_audience = AzureADMultipleOrgs → any work or school Entra tenant can
# sign in. That is the whole point of the /organizations authority in oauth.py.
# ==============================================================================

resource "random_uuid" "mcp_scope" {}

resource "azuread_application" "mcp" {
  display_name     = "oauth-mcp"
  sign_in_audience = "AzureADMultipleOrgs"

  # Issue v2 access tokens so the audience is this app's client ID.
  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      id                         = random_uuid.mcp_scope.id
      value                      = "mcp.access"
      type                       = "User"
      enabled                    = true
      admin_consent_display_name = "Access the MCP server"
      admin_consent_description  = "Allow the signed-in user to call the MCP tools."
      user_consent_display_name  = "Access the MCP server"
      user_consent_description   = "Allow this app to call the MCP tools on your behalf."
    }
  }

  # Confidential client: Entra returns the auth code to our own fixed callback.
  # The hostname is derived from the random suffix (not the function resource) to
  # avoid a dependency cycle — functions.tf reads this app's client_id in turn.
  web {
    redirect_uris = [
      "https://oauth-mcp-func-${random_id.suffix.hex}.azurewebsites.net/oauth/callback"
    ]
  }
}

# App ID URI = api://<client_id>, so the exposed scope resolves to
# api://<client_id>/mcp.access. Set as a separate resource to avoid a self-
# reference on the application's own client_id.
resource "azuread_application_identifier_uri" "mcp" {
  application_id = azuread_application.mcp.id
  identifier_uri = "api://${azuread_application.mcp.client_id}"
}

resource "azuread_service_principal" "mcp" {
  client_id = azuread_application.mcp.client_id
}

# The client secret the broker uses in the Entra token exchange. Stored in Key
# Vault (see keyvault.tf), never a plaintext app setting.
resource "azuread_application_password" "mcp" {
  application_id = azuread_application.mcp.id
  display_name   = "oauth-mcp-broker-secret"
  end_date       = "2099-01-01T00:00:00Z"
}
