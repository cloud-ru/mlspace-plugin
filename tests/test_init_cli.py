"""Literal secret handling and per-workspace verification invariants."""
from __future__ import annotations

import json
import stat

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL

from mlspace_mcp.config import read_dotenv_file
from mlspace_mcp.init_cli import _ask, _verify, _write

TOKEN_BODY = {"token": {"access_token": "AT-1", "expires_in": 3600}}


def test_write_preserves_literal_secret_and_permissions(tmp_path):
    target = tmp_path / 'private' / '.env'
    secret = '  secret=value # "quoted" \\ ${LITERAL}  '
    _write(target, {'CLIENT_ID': 'id', 'CLIENT_SECRET': secret})
    assert read_dotenv_file(target)['MLSPACE_CLIENT_SECRET'] == secret
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_write_failure_preserves_old_file(tmp_path, monkeypatch):
    target = tmp_path / '.env'
    target.write_text('old')
    def fail(*a):
        raise OSError('disk full')
    monkeypatch.setattr('os.replace', fail)
    with pytest.raises(OSError):
        _write(target, {'CLIENT_SECRET': 'new'})
    assert target.read_text() == 'old'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('assignment', ['MLSPACE_CLIENT_ID=pasted-id', 'export MLSPACE_CLIENT_SECRET=pasted-secret'])
def test_assignments_and_blank_values_are_reasked_without_echo(monkeypatch, capsys, assignment):
    values = iter(['', '  ', assignment, '  value=$foo  '])
    monkeypatch.setattr('mlspace_mcp.init_cli.masked_prompt', lambda _: next(values))
    assert _ask('Secret') == '  value=$foo  '
    output = capsys.readouterr().err
    assert 'только значение' in output
    assert 'pasted-secret' not in output and 'pasted-id' not in output


@respx.mock
def test_verify_checks_only_selected_workspaces_with_their_own_headers(capsys):
    respx.post(AUTH_URL).respond(200, json=TOKEN_BODY)
    seen = []

    def detail(request):
        workspace = request.url.path.rsplit("/", 1)[-1]
        seen.append((workspace, request.headers["x-workspace-id"], request.headers["x-api-key"]))
        return httpx.Response(200, json={"id": workspace})

    for workspace in ("ws-demo", "ws-v100"):
        respx.get(f"{BASE_URL}/public/v2/workspaces/v3/{workspace}").mock(side_effect=detail)
    values = {"CLIENT_ID": "cid-1", "CLIENT_SECRET": "csecret-1", "WORKSPACES": json.dumps([
        {"id": "ws-demo", "name": "demo", "project_name": "p", "api_key": "key-demo"},
        {"id": "ws-v100", "name": "v100", "project_name": "p", "api_key": "key-v100"},
    ])}
    result = _verify(values, base_url=BASE_URL)
    assert not result.errors
    assert result.verified_ids == ["ws-demo", "ws-v100"]
    assert seen == [("ws-demo", "ws-demo", "key-demo"), ("ws-v100", "ws-v100", "key-v100")]
    assert "Проверено выбранных воркспейсов: 2/2" in capsys.readouterr().err
    assert not any(call.request.url.path.endswith("/v3/") for call in respx.calls)


@pytest.mark.parametrize("bad_detail", [None, {}, {"id": "some-other-workspace"}])
@respx.mock
def test_verify_reports_partial_coverage_and_continues_after_failure(capsys, bad_detail):
    respx.post(AUTH_URL).respond(200, json=TOKEN_BODY)
    respx.get(f"{BASE_URL}/public/v2/workspaces/v3/ws-demo").respond(
        403 if bad_detail is None else 200,
        json={"detail": "echo csecret-1 key-demo"} if bad_detail is None else bad_detail)
    healthy = respx.get(f"{BASE_URL}/public/v2/workspaces/v3/ws-v100").respond(
        200, json={"id": "ws-v100"})
    values = {"CLIENT_ID": "cid-1", "CLIENT_SECRET": "csecret-1", "WORKSPACES": json.dumps([
        {"id": "ws-demo", "name": "demo", "project_name": "p", "api_key": "key-demo"},
        {"id": "ws-v100", "name": "v100", "project_name": "p", "api_key": "key-v100"},
    ])}
    result = _verify(values, base_url=BASE_URL)
    assert result.verified_ids == ["ws-v100"]
    problem = "; ".join(result.errors)
    assert "ws-demo" in problem
    assert "csecret-1" not in problem
    assert "key-demo" not in problem
    assert healthy.called
    assert "Проверено выбранных воркспейсов: 1/2" in capsys.readouterr().err
