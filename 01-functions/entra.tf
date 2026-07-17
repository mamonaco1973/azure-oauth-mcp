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
# sign_in_audience = AzureADandPersonalMicrosoftAccount → any work, school, OR
# personal Microsoft account can sign in, via the /common authority in oauth.py.
#
# Personal accounts cannot consent to a custom api:// scope, so this app exposes
# no API scope. The broker requests only OIDC scopes and hands Claude the
# id_token (aud = this client_id), which mcp.py validates. That is the one path
# that works uniformly across work, school, and personal accounts.
# ==============================================================================

resource "azuread_application" "mcp" {
  display_name     = "oauth-mcp"
  sign_in_audience = "AzureADandPersonalMicrosoftAccount"

  # Confidential client: Entra returns the auth code to our own fixed callback.
  # The hostname is derived from the random suffix (not the function resource) to
  # avoid a dependency cycle — functions.tf reads this app's client_id in turn.
  web {
    redirect_uris = [
      "https://oauth-mcp-func-${random_id.suffix.hex}.azurewebsites.net/oauth/callback"
    ]
  }
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
