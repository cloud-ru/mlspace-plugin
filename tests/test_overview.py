"""Bounded aggregate coverage; every provider response is a local fake."""
from types import SimpleNamespace

import pytest

from mlspace_mcp.overview import aggregate
from mlspace_mcp.workspace_context import WorkspaceSelection, select_target


class Clients:
    selected = (WorkspaceSelection('A', 'alpha', 'p'), WorkspaceSelection('B', 'beta', 'p'))
    environment = 'test-env'

    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def select(self, target):
        return select_target(self.selected, target)

    async def resolve(self, target):
        ws = self.select(target).id

        async def request(method, path, params=None, path_params=None, json_body=None):
            assert json_body is None
            if path_params:
                path = path.format(**path_params)
            assert method == 'GET'
            self.calls.append((ws, path, params or {}))
            result = self.respond(ws, path, params or {})
            if isinstance(result, Exception):
                raise result
            return result
        return SimpleNamespace(request=request)


def jobs(ws, path, params):
    if path.endswith('configs'):
        return {'regions': [{'key': 'SR004'}, {'key': 'new-region'}]}
    if path.endswith('/allocations'):
        return []
    off = params['offset']
    return {'count': 2, 'jobs': [{'id': f'{ws}-{params["region"]}-{off}', 'name': 'train'}]}


async def test_all_workspaces_dynamic_regions_and_server_capped_pages():
    clients = Clients(jobs)
    out = await aggregate(clients, 'jobs', page_size=20)
    assert out['complete'] and out['fetch_complete'] and not out['output_truncated']
    assert out['observed_count'] == 8
    assert {i['provenance']['workspace_id'] for i in out['items']} == {'A', 'B'}
    assert len(clients.calls) == 12
    assert all('job_author' not in params for _, _, params in clients.calls)


async def test_jobs_include_allocation_regions_missing_from_launch_catalog():
    def respond(ws, path, params):
        if path.endswith('/configs'):
            return {'regions': [{'key': 'SR006'}]}
        if path == f'/public/v2/workspaces/v3/{ws}/allocations':
            return [{'region_key': 'SR006'}, {'region_key': 'A100-MT'},
                    {'region_key': 'A100-MT'}, {'region_key': 'SR009'}]
        assert path == '/public/v2/jobs'
        assert params['status'] == ['Running', 'Pending']
        rows = [{'job_name': 'waiting-training', 'status': 'Pending'}] if params['region'] == 'A100-MT' else []
        return {'count': len(rows), 'jobs': rows}

    clients = Clients(respond)
    out = await aggregate(clients, 'jobs', targets=['A'], status=['Running', 'Pending'])
    assert [item['data']['job_name'] for item in out['items']] == ['waiting-training']
    assert out['complete'] and out['fetch_complete']
    assert out['items'][0]['resource_ref']['address']['region'] == 'A100-MT'
    assert [p['region'] for _, path, p in clients.calls if path == '/public/v2/jobs'] == [
        'SR006', 'A100-MT', 'SR009']
    assert any(scope['source'] == 'allocation_regions' for scope in out['checked_scope'])


@pytest.mark.parametrize('unavailable', ['configs', 'allocations'])
async def test_region_source_failure_retains_jobs_but_cannot_claim_complete(unavailable):
    def respond(ws, path, params):
        if path.endswith('/configs'):
            return PermissionError('private') if unavailable == 'configs' else {'regions': [{'key': 'SR006'}]}
        if path.endswith('/allocations'):
            return PermissionError('private') if unavailable == 'allocations' else [{'region_key': 'A100-MT'}]
        return {'count': 1, 'jobs': [{'job_name': 'visible-job', 'status': 'Pending'}]}

    out = await aggregate(Clients(respond), 'jobs', targets=['A'])
    assert len(out['items']) == 1
    assert not out['complete'] and not out['fetch_complete']
    assert out['failures'] and 'private' not in str(out)


@pytest.mark.parametrize('allocations', [{}, [None], [{'region_key': None}], [{'region_key': ' '}]] )
async def test_malformed_allocation_regions_keep_catalog_observations_partial(allocations):
    def respond(ws, path, params):
        if path.endswith('/allocations'):
            return allocations
        return jobs(ws, path, params)

    out = await aggregate(Clients(respond), 'jobs', targets=['A'])
    assert out['observed_count'] == 4
    assert not out['complete'] and not out['fetch_complete']
    assert any(f['source'] == 'allocation_regions' for f in out['failures'])


async def test_allocations_supply_regions_when_launch_catalog_is_empty():
    def respond(ws, path, params):
        if path.endswith('/configs'):
            return {'regions': []}
        if path.endswith('/allocations'):
            return [{'region_key': 'A100-MT'}]
        return {'count': 1, 'jobs': [{'job_name': 'pending-job'}]}

    out = await aggregate(Clients(respond), 'jobs', targets=['A'])
    assert out['complete'] and out['observed_count'] == 1


async def test_invalid_workspace_path_retains_healthy_workspace_observations():
    clients = Clients(jobs)
    clients.selected = (WorkspaceSelection('A', 'alpha', 'p'),
                        WorkspaceSelection('bad/id', 'invalid', 'p'))
    out = await aggregate(clients, 'jobs')
    assert any(i['provenance']['workspace_id'] == 'A' for i in out['items'])
    assert not out['complete'] and not out['fetch_complete']
    assert any(f['target'] == 'bad/id' and f['source'] == 'allocation_regions'
               for f in out['failures'])
    assert not any('/bad/id/' in path for _, path, _ in clients.calls)


@pytest.mark.parametrize('problem', ['denied', 'timeout', 'regions', 'repeat', 'shape', 'count'])
async def test_incomplete_never_becomes_complete_zero(problem):
    def respond(ws, path, params):
        if ws == 'A':
            return jobs(ws, path, params)
        if problem == 'denied':
            return PermissionError('secret must not leak')
        if problem == 'timeout':
            return TimeoutError()
        if path.endswith('configs'):
            return {} if problem == 'regions' else jobs(ws, path, params)
        if problem == 'shape':
            return {'count': 0}
        if problem == 'count':
            return {'count': 0, 'jobs': [{'id': 'bad'}]}
        return {'count': 20, 'jobs': [{'id': 'same'}]}
    out = await aggregate(Clients(respond), 'jobs')
    assert not out['complete'] and not out['fetch_complete'] and out['failures']
    assert 'total' not in out and 'secret' not in str(out)


async def test_budget_output_limit_and_invalid_scope_are_visible():
    out = await aggregate(Clients(jobs), 'jobs', max_requests=2)
    assert not out['fetch_complete']
    out = await aggregate(Clients(jobs), 'jobs', max_items=1)
    assert out['fetch_complete'] and out['output_truncated'] and not out['complete']
    assert out['observed_count'] == 8 and len(out['items']) == 1
    out = await aggregate(Clients(jobs), 'jobs', targets=['A', 'unconfigured'])
    assert not out['complete'] and out['requested_scope']['targets'] == ['A', 'unconfigured']
    assert out['failures'][0]['target'] == 'unconfigured'


async def test_notebooks_until_empty_keep_paused_and_status():
    def respond(ws, path, params):
        off = params['offset']
        return {'notebooks': [{'uid': f'{ws}-{off}', 'status': 'paused',
                              'instanceType': 'a100.8gpu'}] if off < 2 else []}
    clients = Clients(respond)
    out = await aggregate(clients, 'notebooks', page_size=100)
    assert out['complete'] and out['observed_count'] == 4
    assert all(i['data']['status'] == 'paused' for i in out['items'])
    assert len(clients.calls) == 6


async def test_explicit_author_only_jobs():
    clients = Clients(jobs)
    out = await aggregate(clients, 'jobs', author='human@example.test')
    assert out['filtering_basis'] == {'author': 'human@example.test', 'basis': 'explicit_user_filter'}
    assert all(p['job_author'] == ['human@example.test'] for _, path, p in clients.calls
               if path.endswith('/jobs'))


async def test_status_normalization_refs_and_secret_exclusion():
    def respond(ws, path, params):
        if path.endswith('configs'):
            return {'regions': [{'key': 'SR004'}]}
        if path.endswith('/allocations'):
            return []
        assert params['status'] == ['Running', 'Pending']
        return {'count': 1, 'jobs': [{'uid': f'{ws}-job', 'job_name': 'train',
            'status': 'Running', 's3_credentials': {'secret': 'forbidden-secret'},
            'env': {'name': 'forbidden-env'}}]}
    out = await aggregate(Clients(respond), 'jobs', status=['running', 'Pending'])
    assert out['requested_scope']['status'] == ['Running', 'Pending']
    assert out['complete'] and 'forbidden' not in str(out)
    assert out['items'][0]['resource_ref']['address'] == {'job_name': 'train', 'region': 'SR004'}


async def test_page_and_byte_budgets_disclosed():
    out = await aggregate(Clients(jobs), 'jobs', max_pages=1)
    assert not out['fetch_complete'] and any(f['reason'] == 'page_budget_exhausted' for f in out['failures'])
    def respond(ws, path, params):
        return {'notebooks': [{'uid': ws, 'name': 'x' * 210_000}] if params['offset'] == 0 else []}
    out = await aggregate(Clients(respond), 'notebooks')
    assert out['fetch_complete'] and not out['complete'] and out['output_truncated']
    assert out['observed_count'] == 2 and out['items'] == []


@pytest.mark.parametrize('domain,names', [
    ('jobs', {'mlspace_jobs_overview'}),
    ('notebooks', {'mlspace_notebooks_overview'}),
    ('queues', {'mlspace_queue_inspect'}),
    ('resources', set()),
])
async def test_helper_registration_respects_enabled_domains(domain, names):
    from conftest import make_settings

    from mlspace_mcp.server import build_server
    mcp = build_server(make_settings(enabled_domains=domain))
    tools = await mcp.list_tools()
    helper_names = {'mlspace_jobs_overview', 'mlspace_notebooks_overview', 'mlspace_queue_inspect'}
    assert {t.name for t in tools} & helper_names == names
    assert all(t.annotations.readOnlyHint for t in tools if t.name in names)


async def test_concurrent_workspaces_stay_within_request_parallelism():
    import asyncio
    clients = Clients(jobs)
    active = 0
    peak = 0
    resolve = clients.resolve
    async def slow_resolve(target):
        client = await resolve(target)
        request = client.request
        async def measured(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            try:
                return await request(*args, **kwargs)
            finally:
                active -= 1
        client.request = measured
        return client
    clients.resolve = slow_resolve
    out = await aggregate(clients, 'jobs')
    assert out['complete'] and 1 < peak <= 4


async def test_oversized_scope_metadata_is_explicitly_truncated():
    def respond(ws, path, params):
        if path.endswith('configs'):
            return {'regions': [{'key': 'r' * 300_000}]}
        if path.endswith('/allocations'):
            return []
        return {'count': 0, 'jobs': []}
    import json
    out = await aggregate(Clients(respond), 'jobs')
    assert out['fetch_complete'] and out['output_truncated'] and not out['complete']
    assert len(json.dumps(out).encode()) <= 250_000
    assert out['omitted_output']
