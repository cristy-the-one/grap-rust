import asyncio
import grap_mcp.server as s


def test_capabilities_cover_all_registered_tools():
    caps = s.list_capabilities()
    listed = {name for names in caps["domains"].values() for name in names}
    registered = {t.name for t in asyncio.run(s.mcp.list_tools())}
    # Every registered tool must appear in exactly one domain.
    assert listed == registered, f"drift: {listed ^ registered}"
