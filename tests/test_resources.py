from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.resources import DOMAIN


def test_resources_domain_is_read_only():
    assert all(not op.write for op in DOMAIN.actions.values())


def test_nodes_read_resolves():
    op = DOMAIN.actions["nodes"]
    method, path, path_params, query, body = resolve_request(
        op, {"region": "SR008"}
    )
    assert method == "GET"
    assert path == "/public/v2/nodes/{region}/nodes"
    assert path_params == {"region": "SR008"}
    assert body is None


def test_nodes_load_repeats_node_names():
    op = DOMAIN.actions["nodes_load"]
    _, _, _, query, _ = resolve_request(op, {"node_names": ["a", "b"]})
    assert ("node_names", "a") in query and ("node_names", "b") in query
