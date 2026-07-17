# ==============================================================================
# tools.py
#
# The seven Azure Resource Graph tools exposed over MCP, plus the registry that
# describes them to the model.
#
# Ported from the proxy-based version, with one structural change: handlers now
# take a plain `args` dict and return a plain string. They are no longer HTTP
# routes. mcp.py calls them directly in-process on tools/call, so there is no
# internal HTTP hop and no per-tool URL to secure.
#
# Responses are pre-formatted plain text on purpose — Resource Graph returns
# nested JSON, and the model narrates a text table far better than it parses one.
# ==============================================================================

import logging
import os

from azure.identity import DefaultAzureCredential
from azure.mgmt.resourcegraph import ResourceGraphClient
from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

# ==============================================================================
# Module-level singletons
# Instantiated once per warm instance. DefaultAzureCredential resolves to the
# Function App's System-Assigned Managed Identity at runtime — no credentials in
# code. The MI holds Reader on the subscription.
# ==============================================================================

SUBSCRIPTION_ID = os.environ["SUBSCRIPTION_ID"]

_credential = DefaultAzureCredential()
_rg_client  = ResourceGraphClient(_credential)


class ToolInputError(ValueError):
    """Raised when a tool is called without a required argument.

    mcp.py turns this into a JSON-RPC invalid_params error rather than a 500.
    """


# ==============================================================================
# Tool registry — single source of truth for what the model sees
# Served verbatim on tools/list. The tool name maps straight to a callable in
# TOOL_FUNCTIONS.
# ==============================================================================

TOOL_REGISTRY = [
    {
        "name": "list_virtual_machines",
        "description": (
            "Lists all virtual machines in the subscription with "
            "name, resource group, location, and VM size."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_resource_groups",
        "description": (
            "Lists all resource groups in the subscription with "
            "name, location, and tag count."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "count_resources_by_type",
        "description": (
            "Returns a ranked count of all resource types deployed "
            "in the subscription."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_resources_by_tag",
        "description": "Finds all resources matching a specific tag key and value.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag_key": {
                    "type": "string",
                    "description": "Tag key to search for",
                },
                "tag_value": {
                    "type": "string",
                    "description": "Tag value to match",
                },
            },
            "required": ["tag_key", "tag_value"],
        },
    },
    {
        "name": "list_public_ip_addresses",
        "description": (
            "Lists all public IP addresses in the subscription with "
            "their assigned resource and allocation method."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_resources_by_resource_group",
        "description": "Lists all resources deployed in a specific resource group.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_group": {
                    "type": "string",
                    "description": "Resource group name, e.g. 'my-rg'",
                },
            },
            "required": ["resource_group"],
        },
    },
    {
        "name": "find_resources_by_region",
        "description": (
            "Lists all resources deployed in a specific Azure region "
            "(e.g. 'eastus', 'westeurope')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "description": "Azure region name, e.g. 'eastus'",
                },
            },
            "required": ["region"],
        },
    },
]


# ==============================================================================
# Resource Graph helper
# ==============================================================================

def _rg_query(kql: str) -> list:
    """Execute a Resource Graph KQL query and return rows as a list of dicts.

    Uses objectArray result format so each row is a plain dict — no
    column-index lookup required.

    Args:
        kql: KQL query string.

    Returns:
        List of dicts, one per result row. Empty list if no results.
    """
    request = QueryRequest(
        subscriptions=[SUBSCRIPTION_ID],
        query=kql,
        options=QueryRequestOptions(result_format="objectArray"),
    )
    result = _rg_client.resources(request)
    return result.data or []


# ==============================================================================
# Tool handlers
# Each takes an `args` dict (the MCP tools/call arguments) and returns a string.
# Exceptions propagate — mcp.py wraps them into a JSON-RPC error.
# ==============================================================================

def list_virtual_machines(args: dict) -> str:
    """List all virtual machines in the subscription."""
    rows = _rg_query("""
        Resources
        | where type =~ 'microsoft.compute/virtualmachines'
        | project name, resourceGroup, location,
            vmSize = tostring(properties.hardwareProfile.vmSize)
        | order by name asc
    """)
    lines = [f"Virtual machines ({len(rows)} total):", ""]
    for r in rows:
        lines.append(
            f"  {r['name']:<30}  {r['vmSize']:<20}  "
            f"{r['resourceGroup']}  ({r['location']})"
        )
    if not rows:
        lines.append("  (none found)")
    return "\n".join(lines)


def list_resource_groups(args: dict) -> str:
    """List all resource groups in the subscription."""
    rows = _rg_query("""
        ResourceContainers
        | where type =~ 'microsoft.resources/subscriptions/resourcegroups'
        | project name, location,
            tagCount = array_length(bag_keys(tags))
        | order by name asc
    """)
    lines = [f"Resource groups ({len(rows)} total):", ""]
    for r in rows:
        # tagCount is None when a resource group has no tags at all.
        tc   = r.get("tagCount") or 0
        tags = f"  ({tc} tag{'s' if tc != 1 else ''})" if tc else ""
        lines.append(f"  {r['name']:<40}  {r['location']}{tags}")
    if not rows:
        lines.append("  (none found)")
    return "\n".join(lines)


def count_resources_by_type(args: dict) -> str:
    """Return a ranked count of all resource types in the subscription."""
    rows  = _rg_query("""
        Resources
        | summarize count() by type
        | order by count_ desc
    """)
    total = sum(r["count_"] for r in rows)
    lines = [f"Resources by type ({total} total):", ""]
    for r in rows:
        lines.append(f"  {r['count_']:>5}  {r['type']}")
    if not rows:
        lines.append("  (none found)")
    return "\n".join(lines)


def find_resources_by_tag(args: dict) -> str:
    """Find all resources matching a specific tag key and value.

    Raises:
        ToolInputError: If tag_key or tag_value is missing.
    """
    tag_key   = str(args.get("tag_key",   "")).strip()
    tag_value = str(args.get("tag_value", "")).strip()
    if not tag_key or not tag_value:
        raise ToolInputError("tag_key and tag_value are required")

    # Escape single quotes so user input cannot break out of the KQL string
    # literal — Resource Graph has no parameterized query API.
    kql_key = tag_key.replace("'", "''")
    kql_val = tag_value.replace("'", "''")
    rows = _rg_query(f"""
        Resources
        | where tags['{kql_key}'] =~ '{kql_val}'
        | project name, type, resourceGroup, location
        | order by name asc
    """)
    lines = [f"Resources tagged {tag_key}={tag_value} ({len(rows)} found):", ""]
    for r in rows:
        lines.append(
            f"  {r['name']:<30}  {r['type']:<50}  "
            f"{r['resourceGroup']}  ({r['location']})"
        )
    if not rows:
        lines.append("  (none found)")
    return "\n".join(lines)


def list_public_ip_addresses(args: dict) -> str:
    """List all public IP addresses in the subscription."""
    rows = _rg_query("""
        Resources
        | where type =~ 'microsoft.network/publicipaddresses'
        | project name, resourceGroup, location,
            ipAddress        = tostring(properties.ipAddress),
            allocationMethod = tostring(properties.publicIPAllocationMethod)
        | order by name asc
    """)
    lines = [f"Public IP addresses ({len(rows)} total):", ""]
    for r in rows:
        # ipAddress is empty string when the IP is reserved but unassigned.
        ip     = r.get("ipAddress") or "(unassigned)"
        method = r.get("allocationMethod", "")
        lines.append(
            f"  {r['name']:<30}  {ip:<18}  {method:<10}  "
            f"{r['resourceGroup']}  ({r['location']})"
        )
    if not rows:
        lines.append("  (none found)")
    return "\n".join(lines)


def find_resources_by_resource_group(args: dict) -> str:
    """List all resources deployed in a specific resource group.

    Raises:
        ToolInputError: If resource_group is missing.
    """
    resource_group = str(args.get("resource_group", "")).strip()
    if not resource_group:
        raise ToolInputError("resource_group is required")

    # Escape single quotes — same KQL injection risk as by-tag.
    kql_rg = resource_group.replace("'", "''")
    rows = _rg_query(f"""
        Resources
        | where resourceGroup =~ '{kql_rg}'
        | project name, type, location
        | order by type asc, name asc
    """)
    lines = [f"Resources in {resource_group} ({len(rows)} total):", ""]
    for r in rows:
        lines.append(f"  {r['name']:<30}  {r['type']:<50}  {r['location']}")
    if not rows:
        lines.append(f"  (no resources found in {resource_group})")
    return "\n".join(lines)


def find_resources_by_region(args: dict) -> str:
    """List all resources deployed in a specific Azure region.

    Raises:
        ToolInputError: If region is missing.
    """
    # Normalise to lowercase — Resource Graph location values are lowercase and
    # =~ is case-insensitive, but consistent input is safer.
    region = str(args.get("region", "")).strip().lower()
    if not region:
        raise ToolInputError("region is required")

    kql_region = region.replace("'", "''")
    rows = _rg_query(f"""
        Resources
        | where location =~ '{kql_region}'
        | project name, type, resourceGroup
        | order by type asc, name asc
    """)
    lines = [f"Resources in {region} ({len(rows)} total):", ""]
    for r in rows:
        lines.append(f"  {r['name']:<30}  {r['type']:<50}  {r['resourceGroup']}")
    if not rows:
        lines.append(f"  (no resources found in {region})")
    return "\n".join(lines)


# ==============================================================================
# Name → callable map used by mcp.py on tools/call.
# Adding a tool means: write the handler, add it to TOOL_REGISTRY, add it here.
# ==============================================================================

TOOL_FUNCTIONS = {
    "list_virtual_machines":            list_virtual_machines,
    "list_resource_groups":             list_resource_groups,
    "count_resources_by_type":          count_resources_by_type,
    "find_resources_by_tag":            find_resources_by_tag,
    "list_public_ip_addresses":         list_public_ip_addresses,
    "find_resources_by_resource_group": find_resources_by_resource_group,
    "find_resources_by_region":         find_resources_by_region,
}
