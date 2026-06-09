"""Tool-tier model: shrink the agent-facing MCP surface to core/standard/full.

The active tier is read once from GRAP_TOOL_TIER (default 'full' = every tool).
Tiers are cumulative: core ⊆ standard ⊆ full. A tool registers only if its tier
is included in the active tier.
"""
import os

TIER_ORDER = ("core", "standard", "full")
_RANK = {name: i for i, name in enumerate(TIER_ORDER)}
DEFAULT_TIER = "full"


def active_tier() -> str:
    """The tier this server run advertises. From GRAP_TOOL_TIER; invalid → default."""
    t = os.environ.get("GRAP_TOOL_TIER", DEFAULT_TIER)
    return t if t in _RANK else DEFAULT_TIER


def tier_includes(tool_tier: str, active: str) -> bool:
    """True if a tool tagged `tool_tier` should be visible under `active`.
    Cumulative: a 'core' tool shows in core/standard/full; a 'full' tool only in full."""
    return _RANK.get(tool_tier, _RANK["standard"]) <= _RANK.get(active, _RANK[DEFAULT_TIER])
