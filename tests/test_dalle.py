from mlspace_mcp.registry import resolve_request
from mlspace_mcp.tools.dalle import DOMAIN


def test_predict_resolves():
    op = DOMAIN.actions["predict"]
    method, path, path_params, query, body = resolve_request(
        op, {"model_name": "kandinsky", "body": {"prompt": "a cat"}}
    )
    assert method == "POST"
    assert path == "/public/v2/dalle/v1/predict/{model_name}"
    assert path_params == {"model_name": "kandinsky"}
    assert body == {"prompt": "a cat"}


def test_result_is_binary():
    op = DOMAIN.actions["result"]
    assert op.kind == "binary"
    assert not op.write


def test_model_name_documents_not_discoverable():
    # stop the blind paid perge: say plainly the model name is not fetchable via API
    desc = next(p.description for p in DOMAIN.params if p.name == "model_name").lower()
    assert "not discoverable" in desc or "docs/console" in desc
