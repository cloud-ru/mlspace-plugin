"""User-visible regressions found by the OpenCode acceptance journey."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx

from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.tools.data_transfer import DOMAIN


@pytest.mark.parametrize('invalid', [b'\r', b'MLSPACE_CLIENT_SECRET=wrong\r'])
@pytest.mark.parametrize('cancel', [False, True])
def test_early_secret_input_is_masked_during_retry_and_echo_is_restored(invalid, cancel):
    master, slave = os.openpty()
    code = '''
import termios
from mlspace_mcp.init_cli import _ask
before = termios.tcgetattr(0)
try:
    assert _ask('Secret') == 'SYNTHETIC-EARLY-PASTE'
    print('ACCEPTED')
except KeyboardInterrupt:
    print('CANCELLED')
finally:
    after = termios.tcgetattr(0)
    # macOS sets PENDIN itself when canonical input is restored.
    before[3] &= ~getattr(termios, 'PENDIN', 0)
    after[3] &= ~getattr(termios, 'PENDIN', 0)
    assert after == before
    print('TTY_RESTORED')
'''
    process = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave,
                               stderr=slave, env=dict(os.environ, TERM='xterm',
                                                     PROMPT_TOOLKIT_NO_CPR='1'))
    os.close(slave)
    transcript = b''
    stage = 0
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                transcript += chunk
                if stage == 0 and b'Secret:' in transcript:
                    os.write(master, invalid)
                    stage = 1
                if stage == 1 and any(x.encode() in transcript for x in
                                     ('Поле обязательно', 'Вставьте только значение')):
                    # Type ahead immediately, before the next PromptSession is ready.
                    os.write(master, b'\x03' if cancel else
                             b'\x1b[200~SYNTHETIC-EARLY-PASTE\x1b[201~\r')
                    stage = 2
            if process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0, transcript.decode(errors="replace")
        assert b'TTY_RESTORED' in transcript
        assert (b'CANCELLED' if cancel else b'ACCEPTED') in transcript
        assert b'SYNTHETIC-EARLY-PASTE' not in transcript
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


@pytest.mark.parametrize('action,body,kwargs', [
    ('list_history', {'data': [{'history_id': 'h', 'status': 'failed', 'size_bytes': 0,
                               'rows_count': 0, 'progress': 0}], 'total_count': 1}, {}),
    ('list_history', {'data': [{'history_id': 'h', 'status': 'failed', 'size_bytes': 512}],
                      'total_count': 1}, {}),
    ('get_history_status', [{'history_id': 'h', 'status': 'cancelled'}], {'ids': ['h']}),
])
async def test_failed_run_returns_explicit_evidence_limits_without_changing_observations(action, body, kwargs):
    handler = _build_handler(DOMAIN, available_actions(DOMAIN, True), make_settings())
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        mock.get(BASE_URL + DOMAIN.actions[action].path).respond(200, json=body)
        async with httpx.AsyncClient() as http:
            result = json.loads(await handler(action=action, ctx=runtime_ctx(http), **kwargs))
    original_row = body['data'][0] if isinstance(body, dict) else body[0]
    row = result['data'][0] if isinstance(result, dict) else result[0]
    assert {k: row[k] for k in original_row} == original_row
    assert row['_mcp_evidence']['data_impact'] == 'unknown'
    assert row['_mcp_evidence']['failure_phase'] == 'unknown'
    assert 'Counters/empty logs alone' in row['_mcp_evidence']['basis']
    if isinstance(body, dict):
        assert result['total_count'] == body['total_count']


@pytest.mark.parametrize('action,kwargs', [
    ('get_event_logs', {'history_id': 'h', 'offset': 0, 'limit': 20}),
    ('get_connector_logs', {'connector_type': 's3custom', 'id_': 'c'}),
])
async def test_empty_diagnostic_logs_do_not_imply_no_data_changes(action, kwargs):
    handler = _build_handler(DOMAIN, available_actions(DOMAIN, True), make_settings())
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        mock.get(url__regex=BASE_URL + '/public/v2/data_transfer/.*').respond(200, json=[])
        async with httpx.AsyncClient() as http:
            result = json.loads(await handler(action=action, ctx=runtime_ctx(http), **kwargs))
    if action == 'get_connector_logs':
        assert result['metadata']['content_trust'] == 'untrusted'
        result = result['log']
    assert result['result'] == []
    assert result['_mcp_evidence']['data_impact'] == 'unknown'
    assert result['_mcp_evidence']['failure_phase'] == 'unknown'


@pytest.mark.parametrize('payload', [
    {'data': [], 'total_count': 0},
    {'data': [{'history_id': 'ok', 'status': 'success', 'size_bytes': 42}], 'total_count': 1},
    {'unexpected': [{'status': 'failed'}]},
    'unexpected upstream text',
])
def test_history_evidence_leaves_unrelated_shapes_and_success_unchanged(payload):
    from copy import deepcopy

    from mlspace_mcp.data_transfer_hooks import history_evidence
    before = deepcopy(payload)
    assert history_evidence(payload, {}) == before
    assert payload == before


@pytest.mark.parametrize('payload', [
    {'items': [{'message': 'Access denied'}], 'count': 1},
    {'items': [], 'error_message': 'Access denied'},
    {'items': [], 'total_count': 4},
    'Access denied',
    ['Access denied'],
    {'status': 'failed', 'logs': ['Access denied']},
])
def test_log_evidence_preserves_real_errors_and_nonempty_or_unknown_shapes(payload):
    from mlspace_mcp.data_transfer_hooks import log_evidence
    result = log_evidence(payload, {})
    if isinstance(payload, dict):
        assert {key: result[key] for key in payload} == payload
        assert result["_mcp_evidence"]["data_impact"] == "unknown"
        assert "failure_cause" not in result["_mcp_evidence"]
    elif isinstance(payload, list):
        assert result["result"] == payload
        assert result["_mcp_evidence"]["data_impact"] == "unknown"
    else:
        assert result == payload


@pytest.mark.parametrize('payload', [None, '', {}, {'items': [], 'count': 0},
                                      {'status': 'failed', 'logs': []}])
def test_log_evidence_handles_other_empty_shapes(payload):
    from mlspace_mcp.data_transfer_hooks import log_evidence
    result = log_evidence(payload, {})
    assert result['result'] == payload
    assert result['_mcp_evidence']['data_impact'] == 'unknown'


async def test_known_error_log_preserves_cause_but_does_not_certify_data_impact():
    body = {'items': [{'last_log': 'Destination returned AccessDenied during PutObject.'}], 'count': 1}
    handler = _build_handler(DOMAIN, available_actions(DOMAIN, True), make_settings())
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        mock.get(BASE_URL + DOMAIN.actions['get_event_logs'].path).respond(200, json=body)
        async with httpx.AsyncClient() as http:
            result = json.loads(await handler(action='get_event_logs', history_id='h', offset=0,
                                              limit=20, ctx=runtime_ctx(http)))
    assert result['items'] == body['items']
    assert result['count'] == 1
    assert result['_mcp_evidence']['data_impact'] == 'unknown'
    assert 'failure_cause' not in result['_mcp_evidence']
    assert 'failure_phase' not in result['_mcp_evidence']
