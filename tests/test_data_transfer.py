from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.data_transfer import DOMAIN


def test_list_connectors_resolves():
    op = DOMAIN.actions["list_connectors"]
    method, path, path_params, query, body = resolve_request(op, {})
    assert method == "GET"
    assert path == "/public/v2/data_transfer/v3/connectors"
    assert body is None


def test_get_connector_templates_path():
    op = DOMAIN.actions["get_connector"]
    method, path, path_params, _query, _body = resolve_request(
        op, {"connector_type": "postgresql", "id_": "abc"}
    )
    assert method == "GET"
    assert path_params == {"connector_type": "postgresql", "id_": "abc"}


def test_delete_connectors_repeats_ids_and_needs_confirm():
    op = DOMAIN.actions["delete_connectors"]
    _m, _p, _pp, query, _b = resolve_request(
        op, {"ids": ["a", "b"], "confirm": True}
    )
    assert query == [("ids", "a"), ("ids", "b")]


def test_list_connectors_paginates():
    # page/page_size are the only working shrink lever for the 27KB list
    op = DOMAIN.actions["list_connectors"]
    _m, _p, _pp, query, _b = resolve_request(op, {"page": 2, "page_size": 5})
    assert ("page", 2) in query
    assert ("page_size", 5) in query


def test_list_connectors_no_autopage():
    # the wrapper must NOT inject a default page_size (no total_count → would hide data)
    op = DOMAIN.actions["list_connectors"]
    _m, _p, _pp, query, _b = resolve_request(op, {})
    assert query == []
