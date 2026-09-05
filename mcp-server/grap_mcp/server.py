"""MCP transport binding for the shared Grap application."""
from mcp.server.fastmcp import FastMCP

from .application import GrapApplication, ApplicationState, _TIERS
from .tiers import tier_includes


application = GrapApplication()
mcp = FastMCP("Grap Engine")

# Preserve direct tool-name imports; orchestration and state live in application.
for _name in application.operation_names():
    _method = getattr(application, _name)
    globals()[_name] = _method
    if tier_includes(_TIERS.get(_name, "standard"), application.active_tier):
        mcp.tool()(_method)

# Legacy state alias. New callers should create their own GrapApplication.
_state = application._state
GrapServerState = ApplicationState


def main():
    mcp.run()


if __name__ == "__main__":
    main()
