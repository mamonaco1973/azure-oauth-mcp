resource "azurerm_storage_account" "functions" {
  name                     = "oauthmcpsa${random_id.suffix.hex}"
  resource_group_name      = azurerm_resource_group.mcp.name
  location                 = azurerm_resource_group.mcp.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
}

resource "azurerm_storage_container" "func_code" {
  name                  = "func-code"
  storage_account_id    = azurerm_storage_account.functions.id
  container_access_type = "private"
}

resource "azurerm_service_plan" "mcp" {
  name                = "oauth-mcp-plan"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
  os_type             = "Linux"
  sku_name            = "FC1"
}

resource "azurerm_application_insights" "mcp" {
  name                = "oauth-mcp-ai"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
  application_type    = "web"
}

resource "azurerm_function_app_flex_consumption" "mcp" {
  # Name is fixed (only the random suffix varies) and MUST match the hostname
  # baked into the Entra redirect URI in entra.tf.
  name                = "oauth-mcp-func-${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location

  service_plan_id = azurerm_service_plan.mcp.id
  https_only      = true

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.functions.primary_blob_endpoint}${azurerm_storage_container.func_code.name}"
  storage_authentication_type = "StorageAccountConnectionString"
  storage_access_key          = azurerm_storage_account.functions.primary_access_key

  runtime_name    = "python"
  runtime_version = "3.11"

  maximum_instance_count = 10
  instance_memory_in_mb  = 2048

  site_config {}

  # System-assigned identity queries Resource Graph via DefaultAzureCredential
  # and reads the two Key Vault secrets below — no credentials in app settings.
  identity {
    type = "SystemAssigned"
  }

  app_settings = {
    FUNCTIONS_EXTENSION_VERSION           = "~4"
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.mcp.connection_string
    AzureWebJobsFeatureFlags              = "EnableWorkerIndexing"

    SUBSCRIPTION_ID = data.azurerm_client_config.current.subscription_id

    # The Entra OAuth app: client_id is public; the secret and the Cosmos
    # connection string are Key Vault references — the portal shows a reference
    # URI, not the value.
    MCP_ENTRA_CLIENT_ID     = azuread_application.mcp.client_id
    MCP_ENTRA_CLIENT_SECRET = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.entra_client_secret.versionless_id})"
    COSMOS_CONNECTION       = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.cosmos_connection.versionless_id})"
    COSMOS_DB               = azurerm_cosmosdb_sql_database.mcp.name
    COSMOS_CONTAINER        = azurerm_cosmosdb_sql_container.mcp.name
  }

  lifecycle {
    ignore_changes = [
      app_settings["APPLICATIONINSIGHTS_CONNECTION_STRING"],
      app_settings["FUNCTIONS_EXTENSION_VERSION"],
      app_settings["SCM_DO_BUILD_DURING_DEPLOYMENT"],
      site_config,
    ]
  }
}
