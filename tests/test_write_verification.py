"""A successful write response must not hide lost labels or changed schedules."""
import json

import httpx
import pytest
import respx
from conftest import AUTH_URL, BASE_URL, make_settings, runtime_ctx
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.registry import _build_handler, available_actions
from mlspace_mcp.tools.data_transfer import DOMAIN as TRANSFER
from mlspace_mcp.tools.docker_registry import DOMAIN as REGISTRY

TRANSFER_ID = '11111111-1111-4111-8111-111111111111'
BODY = dict(name='test', cluster_name='pd30', connector_id='source',
            destination_connector_id='destination', connector_type='s3custom',
            destination_connector_type='nfs', strategy='write_all', favorite=False,
            query={'source': ['/input/'], 'destination': '/output/'})


@pytest.mark.parametrize('action', ['create_transfer', 'update_transfer'])
@pytest.mark.parametrize('period', [0.5, 1.5, -1, float('nan'), float('inf')])
async def test_unsafe_period_is_refused_before_any_http(action, period):
    settings = make_settings(readonly=False)
    handler = _build_handler(TRANSFER, available_actions(TRANSFER, False), settings)
    with respx.mock as mock:
        async with httpx.AsyncClient() as http:
            with pytest.raises(ToolError, match='crontab.period'):
                await handler(action=action, id_=TRANSFER_ID,
                              body={**BODY, 'crontab': {'period': period}},
                              ctx=runtime_ctx(http, settings))
        assert not mock.calls


@pytest.mark.parametrize('action', ['create_transfer', 'update_transfer'])
@pytest.mark.parametrize('readback', ['match', 'changed', 'unavailable', 'missing'])
async def test_schedule_is_checked_after_one_write_without_retry(action, readback):
    settings = make_settings(readonly=False)
    handler = _build_handler(TRANSFER, available_actions(TRANSFER, False), settings)
    requested = {'period': 2.0, 'start_at': '2030-01-01T13:00:00+03:00', 'weekdays': [2, 1]}
    saved = {'period': 2, 'start_at': '2030-01-01T10:00:00Z', 'weekdays': [1, 2]}
    if readback == 'changed':
        saved['period'] = 0
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        path = TRANSFER.actions[action].path.format(id_=TRANSFER_ID)
        write = mock.post(BASE_URL + path).respond(200, json={'transfer_id': TRANSFER_ID})
        get = mock.get(BASE_URL + '/public/v2/data_transfer/v4/transfer/' + TRANSFER_ID).respond(
            403 if readback == 'unavailable' else 200,
            json={} if readback == 'missing' else {'transfer_id': TRANSFER_ID, 'crontab': saved})
        async with httpx.AsyncClient() as http:
            async def call():
                return await handler(action=action, id_=TRANSFER_ID,
                                     body={**BODY, 'crontab': requested},
                                     ctx=runtime_ctx(http, settings))
            if readback == 'match':
                result = json.loads(await call())
                assert result['transfer_id'] == TRANSFER_ID
            else:
                with pytest.raises(ToolError, match='Do not repeat') as error:
                    await call()
                assert TRANSFER_ID in str(error.value)
        assert write.call_count == get.call_count == 1
        assert json.loads(write.calls[0].request.content)['crontab'] == requested


@pytest.mark.parametrize('saved_labels,success', [(['new', 'retained'], True),
                                               ([], False), (['new', 'old'], False),
                                               (None, False)])
async def test_repository_labels_are_read_back_without_mutating_again(saved_labels, success):
    settings = make_settings(readonly=False)
    handler = _build_handler(REGISTRY, available_actions(REGISTRY, False), settings)
    body = {'labels': ['new'], 'labels_to_delete': ['old']}
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        url = BASE_URL + '/public/v2/docker_registry/v2/repositories/repo-id'
        write = mock.put(url).respond(200, json={'id': 'repo-id', 'labels': ['new']})
        get = mock.get(url).respond(200, json={'id': 'repo-id', 'labels': saved_labels})
        async with httpx.AsyncClient() as http:
            async def call():
                return await handler(action='update_repo', repository_id='repo-id', body=body,
                                     ctx=runtime_ctx(http, settings))
            if success:
                await call()
            else:
                with pytest.raises(ToolError, match='Do not repeat'):
                    await call()
        assert write.call_count == get.call_count == 1
        assert json.loads(write.calls[0].request.content) == body


async def test_schedule_dry_run_does_not_write_or_read_back():
    settings = make_settings(readonly=False, dry_run=True)
    handler = _build_handler(TRANSFER, available_actions(TRANSFER, False), settings)
    with respx.mock as mock:
        async with httpx.AsyncClient() as http:
            result = json.loads(await handler(action='create_transfer',
                               body={**BODY, 'crontab': {'period': 1}},
                               ctx=runtime_ctx(http, settings)))
        assert result['dry_run']
        assert not mock.calls


@pytest.mark.parametrize('domain,action,kwargs,method,path', [
    (TRANSFER, 'create_transfer', {'body': BODY}, 'POST', '/public/v2/data_transfer/v5/transfer'),
    (REGISTRY, 'update_repo', {'repository_id': 'repo-id', 'body': {'description': 'updated'}},
     'PUT', '/public/v2/docker_registry/v2/repositories/repo-id'),
])
async def test_unrelated_writes_do_not_add_readback(domain, action, kwargs, method, path):
    settings = make_settings(readonly=False)
    handler = _build_handler(domain, available_actions(domain, False), settings)
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        write = mock.request(method, BASE_URL + path).respond(200, json={})
        async with httpx.AsyncClient() as http:
            await handler(action=action, **kwargs, ctx=runtime_ctx(http, settings))
        assert write.call_count == 1
        assert len(mock.calls) == 2


@pytest.mark.parametrize('response_id', [None, '../docker_registry', 'not-a-uuid'])
async def test_missing_or_invalid_create_id_does_not_use_an_unrelated_input_id(response_id):
    settings = make_settings(readonly=False)
    handler = _build_handler(TRANSFER, available_actions(TRANSFER, False), settings)
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        write = mock.post(BASE_URL + '/public/v2/data_transfer/v5/transfer').respond(
            200, json={'transfer_id': response_id})
        async with httpx.AsyncClient() as http:
            with pytest.raises(ToolError, match='Do not repeat'):
                await handler(action='create_transfer', id_=TRANSFER_ID,
                    body={**BODY, 'crontab': {'period': 2}}, ctx=runtime_ctx(http, settings))
        assert write.call_count == 1
        assert len(mock.calls) == 2


async def test_explicit_schedule_removal_is_read_back():
    settings = make_settings(readonly=False)
    handler = _build_handler(TRANSFER, available_actions(TRANSFER, False), settings)
    with respx.mock as mock:
        mock.post(AUTH_URL).respond(200, json={'token': {'access_token': 't'}})
        write = mock.post(BASE_URL + '/public/v2/data_transfer/v5/transfer/' + TRANSFER_ID).respond(
            200, json={'transfer_id': TRANSFER_ID})
        get = mock.get(BASE_URL + '/public/v2/data_transfer/v4/transfer/' + TRANSFER_ID).respond(
            200, json={'transfer_id': TRANSFER_ID, 'crontab': None})
        async with httpx.AsyncClient() as http:
            await handler(action='update_transfer', id_=TRANSFER_ID,
                          body={**BODY, 'crontab': None}, ctx=runtime_ctx(http, settings))
        assert write.call_count == get.call_count == 1
