from grap_mcp.auth import ROLES, role_allows


def test_role_ordering():
    assert set(ROLES) == {"viewer", "operator", "admin"}
    assert role_allows("admin", "operator")
    assert role_allows("operator", "operator")
    assert not role_allows("viewer", "operator")
    assert not role_allows("operator", "admin")
    assert role_allows("admin", "admin")


def test_unknown_role_denied():
    assert not role_allows("nobody", "viewer")
