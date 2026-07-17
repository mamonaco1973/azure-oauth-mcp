#!/bin/bash
# ==============================================================================
# File: destroy.sh
#
# Purpose:
#   Tears down the Azure OAuth MCP stack deployed by apply.sh — Function App,
#   Key Vault (purged, not just soft-deleted), Cosmos DB, the Entra app
#   registration, storage, and the resource group.
#
#   Nothing is generated locally by this build, so there is nothing to clean up
#   on disk. Terraform owns everything, including the Entra app.
# ==============================================================================

set -euo pipefail

./check_env.sh

echo "NOTE: Destroying Azure infrastructure..."

cd 01-functions
terraform init -upgrade
# Run twice — Key Vault purge and Entra propagation occasionally need a second
# pass to fully settle.
terraform destroy -auto-approve || true
terraform destroy -auto-approve
cd ..

echo "NOTE: Infrastructure teardown complete."
