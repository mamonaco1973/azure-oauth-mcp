# ==============================================================================
# Cosmos DB — transient OAuth login state
#
# This is the Azure analog of the DynamoDB table (AWS) and Firestore (GCP) in
# the sibling builds. It holds nothing durable: only the two short-lived
# documents an in-flight login needs.
#
#   id=<session>   kind=pending  Claude's redirect_uri + state, before login
#   id=az_<code>   kind=code     Entra tokens, one-time use, after login
#
# Both are deleted the moment they are consumed. The container TTL below is the
# backstop for logins that are abandoned halfway through.
#
# Serverless capacity mode — no provisioned RUs, billed per request. At demo
# volume this is effectively free.
# ==============================================================================

resource "azurerm_cosmosdb_account" "mcp" {
  name                = "oauth-mcp-cosmos-${random_id.suffix.hex}"
  resource_group_name = azurerm_resource_group.mcp.name
  location            = azurerm_resource_group.mcp.location
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.mcp.location
    failover_priority = 0
  }
}

resource "azurerm_cosmosdb_sql_database" "mcp" {
  name                = "oauthdb"
  resource_group_name = azurerm_resource_group.mcp.name
  account_name        = azurerm_cosmosdb_account.mcp.name
}

resource "azurerm_cosmosdb_sql_container" "mcp" {
  name                = "state"
  resource_group_name = azurerm_resource_group.mcp.name
  account_name        = azurerm_cosmosdb_account.mcp.name
  database_name       = azurerm_cosmosdb_sql_database.mcp.name

  partition_key_paths = ["/id"]

  # Auto-delete documents 5 minutes after their last write — sweeps abandoned
  # logins. Consumed records are deleted immediately by oauth.py; this is only
  # the backstop.
  default_ttl = 300
}
