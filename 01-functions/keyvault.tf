# ==============================================================================
# Key Vault — holds the two secrets the broker needs at runtime
#
#   entra-client-secret  the confidential-client secret for the Entra exchange
#   cosmos-connection    the Cosmos DB connection string
#
# Both reach the function as Key Vault references in app_settings (functions.tf),
# so the portal shows a reference URI, not the secret value, and neither secret
# is a plaintext app setting. This mirrors Secret Manager (GCP) and is the
# reason main.tf enables purge-on-destroy for the vault.
#
# Access is via access policies (not RBAC) because policies take effect
# immediately — RBAC role assignments need propagation time that would make the
# secret writes below flaky on a fresh apply.
# ==============================================================================

resource "azurerm_key_vault" "mcp" {
  name                       = "oauthmcp${random_id.suffix.hex}"
  resource_group_name        = azurerm_resource_group.mcp.name
  location                   = azurerm_resource_group.mcp.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7

  # Allow destroy.sh to fully remove the vault so re-deploys don't hit a
  # soft-deleted name conflict (paired with purge_soft_delete_on_destroy).
  purge_protection_enabled = false
}

# The deploying service principal — can write the secrets below.
resource "azurerm_key_vault_access_policy" "deployer" {
  key_vault_id = azurerm_key_vault.mcp.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = data.azurerm_client_config.current.object_id

  secret_permissions = ["Get", "List", "Set", "Delete", "Purge", "Recover"]
}

# The Function App's managed identity — can only read.
resource "azurerm_key_vault_access_policy" "func" {
  key_vault_id = azurerm_key_vault.mcp.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_function_app_flex_consumption.mcp.identity[0].principal_id

  secret_permissions = ["Get"]
}

resource "azurerm_key_vault_secret" "entra_client_secret" {
  name         = "entra-client-secret"
  value        = azuread_application_password.mcp.value
  key_vault_id = azurerm_key_vault.mcp.id

  depends_on = [azurerm_key_vault_access_policy.deployer]
}

resource "azurerm_key_vault_secret" "cosmos_connection" {
  name         = "cosmos-connection"
  value        = azurerm_cosmosdb_account.mcp.primary_sql_connection_string
  key_vault_id = azurerm_key_vault.mcp.id

  depends_on = [azurerm_key_vault_access_policy.deployer]
}
