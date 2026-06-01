"""Role model for the MCP capability surface (POC simulation; no identity backend)."""

ROLES = {"viewer": 0, "operator": 1, "admin": 2}


def role_allows(current: str, required: str) -> bool:
    """True if `current` role meets or exceeds `required`."""
    return ROLES.get(current, -1) >= ROLES.get(required, 99)
