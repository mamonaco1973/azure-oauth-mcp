output "function_app_name" {
  value = azurerm_function_app_flex_consumption.mcp.name
}

output "function_base_url" {
  description = "Root URL of the function (routePrefix is empty, so no /api)."
  value       = "https://${azurerm_function_app_flex_consumption.mcp.default_hostname}"
}

output "mcp_url" {
  description = "The URL to paste into Claude when adding the connector."
  value       = "https://${azurerm_function_app_flex_consumption.mcp.default_hostname}/mcp"
}

output "oauth_redirect_uri" {
  description = "Redirect URI registered on the Entra app (informational)."
  value       = "https://${azurerm_function_app_flex_consumption.mcp.default_hostname}/oauth/callback"
}

output "entra_client_id" {
  description = "The multitenant Entra app client ID (public; not a secret)."
  value       = azuread_application.mcp.client_id
}

output "resource_group_name" {
  value = azurerm_resource_group.mcp.name
}
