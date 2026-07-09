import asyncio
import grap_mcp.server as s


def test_capabilities_cover_all_registered_tools():
    caps = s.list_capabilities()
    listed = {name for names in caps["domains"].values() for name in names}
    registered = {t.name for t in asyncio.run(s.mcp.list_tools())}
    # Every registered tool must appear in exactly one domain.
    assert listed == registered, f"drift: {listed ^ registered}"


def test_kee_review_tools_in_capabilities():
    """Cross-cut test for P3 KEE review tools (list/get/adopt proposed)."""
    caps = s.list_capabilities()
    listed = {name for names in caps["domains"].values() for name in names}
    kee_tools = {"extract_ontology", "list_proposed_ontologies", "get_proposed_ontology", "adopt_proposed_ontology"}
    assert kee_tools.issubset(listed), f"missing KEE review tools: {kee_tools - listed}"
