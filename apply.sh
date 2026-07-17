#!/bin/bash
# ==============================================================================
# File: apply.sh
#
# Purpose:
#   Deploys the Azure OAuth MCP stack: environment validation -> Terraform ->
#   function code deploy -> validation -> connector instructions.
#
#   There is no proxy config generation and no secret handed to the user. That
#   is the point of this build: users authenticate as themselves through Entra,
#   so there is nothing to hand them but a URL. Terraform even creates the Entra
#   app registration and wires its redirect URI — no console step at all.
# ==============================================================================

set -euo pipefail

echo "NOTE: Running environment validation..."
./check_env.sh

# ==============================================================================
# Deploy infrastructure
# ==============================================================================

echo "NOTE: Deploying Azure infrastructure..."

cd 01-functions
terraform init -upgrade
terraform apply -auto-approve

RESOURCE_GROUP=$(terraform output -raw resource_group_name)
FUNC_APP_NAME=$(terraform output -raw function_app_name)
MCP_URL=$(terraform output -raw mcp_url)
CLIENT_ID=$(terraform output -raw entra_client_id)
cd ..

echo "NOTE: Resource group: ${RESOURCE_GROUP}"
echo "NOTE: Function app:   ${FUNC_APP_NAME}"

# ==============================================================================
# Deploy function code
# ==============================================================================

echo "NOTE: Packaging and deploying function code..."

cd 01-functions/code
rm -f app.zip
zip -r app.zip . \
  -x "*__pycache__*" \
  -x "*.pyc" \
  -x "*.DS_Store"

az functionapp deployment source config-zip \
  --name           "$FUNC_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --src            app.zip \
  --build-remote   true

rm -f app.zip
cd ../..

# ==============================================================================
# Post-deployment validation
# ==============================================================================

echo "NOTE: Running post-deployment validation..."
./validate.sh

# ==============================================================================
# Connector instructions
# ==============================================================================

cat <<EOF

================================================================================
  Deployment complete.
================================================================================

  Connect Claude
  --------------
  Settings -> Connectors -> Add custom connector, and paste:

      ${MCP_URL}

  That is the whole configuration. No client ID, no secret, no key file, and
  no local proxy. Claude discovers the authorization server, registers itself,
  and sends you to Microsoft to log in.

  The Entra app (client ${CLIENT_ID}) is multitenant, so anyone with a work or
  school Microsoft account can sign in. Terraform created it and wired its
  redirect URI — there is no Azure Portal step.

================================================================================
EOF
