"""Queue observations must not invent ownership or prove unoccupied capacity."""
import pytest
from test_overview import Clients

from mlspace_mcp.overview import PRIORITIES, inspect_queue
from mlspace_mcp.tools.queues import ACTIONS


def queue(ws, path, params):
    if path.endswith('/q'):
        return {'id': 'q', 'allocation_id': 'allocation', 'nodes': ['node-1'], 'type': 'shared'}
    if path.endswith('/awaiting-launch-resources'):
        return {'count': 1, 'items': [{'id': 'pending-B', 'workspace_id': 'B', 'status': 'Pending'}]} if params['priority'] == 'high' else {'count': 0, 'items': []}
    if path.endswith('/jobs'):
        return {'jobs': [{'id': 'job-B', 'workspace': 'friendly-B', 'status': 'Running'}]}
    if path.endswith('/notebooks'):
        return {'notebooks': [{'id': 'paused', 'status': 'paused', 'node_name': 'node-1', 'instance_type': 'a100.8gpu'}]}
    if path.endswith('/pods'):
        return {'pods': [{'name': 'live-pod', 'status': 'Running', 'node_name': 'node-1'}]}
    if path.endswith('/load'):
        assert params['node_names'] == ['node-1']
        return {'loads': [{'node_name': 'node-1', 'jobs': [{'id': 'job-B'}], 'notebooks': []}]}
    if path.endswith('/nodes'):
        return {'nodes': [{'name': 'node-1', 'status': 'Ready', 'unschedulable': False,
                           'gpu_type': 'a100', 'instance_types': [], 'resource_usage': {
                               'total': {'cpu': 64, 'ram': 512, 'gpu': 8},
                               'allocated': {'cpu': 8, 'ram': 64, 'gpu': 1},
                               'available': {'cpu': 56, 'ram': 448, 'gpu': 7}}}]}
    raise AssertionError(path)


async def test_cross_workspace_observations_unknown_coverage_no_capacity_sum():
    clients = Clients(queue)
    out = await inspect_queue(clients, 'q', target='A', candidate_nodes=['node-1'])
    assert out['fetch_complete']
    assert out['cross_workspace_coverage']['status'] == 'unknown'
    assert 'safe_to_move' not in str(out)
    assert {p['priority'] for _, path, p in clients.calls if path.endswith('awaiting-launch-resources')} == set(PRIORITIES)
    job = next(i for i in out['items'] if i['source'] == 'jobs')
    assert job['provenance']['request_workspace_id'] == 'A'
    assert 'workspace_id' not in job['provenance']
    assert job['data']['workspace'] == 'friendly-B'
    assert any(i['source'] == 'pods' for i in out['items'])
    assert any(i['source'] == 'notebooks' and i['data']['status'] == 'paused' for i in out['items'])
    assert len([c for c in clients.calls if '/allocations/' in c[1]]) == 1


async def test_unavailable_sibling_and_invalid_candidate_are_explicit():
    def respond(ws, path, params):
        return PermissionError('private') if ws == 'B' else queue(ws, path, params)
    out = await inspect_queue(Clients(respond), 'q', target='A', candidate_nodes=['other'])
    assert not out['fetch_complete'] and out['failures']
    assert out['candidates']['invalid'] == ['other']
    assert out['cross_workspace_coverage']['status'] == 'unknown'


async def test_empty_nodes_still_checks_all_pending_priorities():
    def respond(ws, path, params):
        result = queue(ws, path, params)
        if path.endswith('/q'):
            result['nodes'] = []
        return result
    out = await inspect_queue(Clients(respond), 'q', target='A')
    assert any(i['source'] == 'pending' and i['data']['workspace_id'] == 'B' for i in out['items'])


def test_pending_declaration_has_all_required_query_parameters():
    op = ACTIONS['awaiting_launch_resources']
    assert set(op.required) == {'queue_id', 'priority', 'limit', 'offset'}
    assert op.query_params == {'priority': 'priority', 'limit': 'limit', 'offset': 'offset'}


async def test_each_priority_failure_retained():
    def respond(ws, path, params):
        if path.endswith('awaiting-launch-resources') and params['priority'] == 'low':
            return TimeoutError()
        return queue(ws, path, params)
    out = await inspect_queue(Clients(respond), 'q', target='A')
    assert not out['fetch_complete']
    assert len([f for f in out['failures'] if f.get('priority') == 'low']) == 2


@pytest.mark.parametrize('bad_response', [{'loads': []}, {'loads': [{'node_name': 'node-1'}]}])
async def test_missing_or_malformed_candidate_load_is_unknown(bad_response):
    def respond(ws, path, params):
        return bad_response if path.endswith('/load') else queue(ws, path, params)
    out = await inspect_queue(Clients(respond), 'q', target='A', candidate_nodes=['node-1'])
    assert not out['fetch_complete']
    assert out['cross_workspace_coverage']['status'] == 'unknown'


async def test_pending_actual_schema_pagination_uses_stable_identity():
    def respond(ws, path, params):
        if path.endswith('awaiting-launch-resources') and params['priority'] == 'high':
            return {'count': 3, 'items': [{'name': 'pending-B', 'workspace_id': 'B',
                        'type': 'job', 'status': 'Pending', 'launch_order': params['offset']}]}
        return queue(ws, path, params)
    out = await inspect_queue(Clients(respond), 'q', target='A')
    assert not out['fetch_complete']
    assert any(f['reason'] == 'repeated_items' for f in out['failures'])


@pytest.mark.parametrize('max_items', [100, 1])
async def test_repeated_pending_names_preserve_observations_and_partial_status(max_items):
    """Production returned two same-name PodGroups differing only in launch order."""
    def respond(ws, path, params):
        if path.endswith('/awaiting-launch-resources') and params['priority'] == 'high':
            return {'count': 2, 'items': [
                {'name': 'waiting-notebook', 'workspace_id': 'A', 'type': 'Notebook',
                 'status': 'Pending', 'launch_order': order, 'gpu_count': 8,
                 'created_at': '2026-08-26T20:39:58Z', 'secret': 'never-return-this'}
                for order in (1, 2)
            ]}
        return queue(ws, path, params)

    out = await inspect_queue(Clients(respond), 'q', target='A', targets=['A'],
                              max_items=max_items)
    assert not out['fetch_complete'] and not out['complete']
    assert any(f['source'] == 'pending' and f['reason'] == 'repeated_items'
               for f in out['failures'])
    # queue, job, notebook, pod, two pending observations, node facts and node load
    assert out['observed_count'] == 8
    assert 'never-return-this' not in str(out)
    if max_items == 100:
        pending = [i['data'] for i in out['items'] if i['source'] == 'pending']
        assert [row['launch_order'] for row in pending] == [1, 2]
        assert all(row['status'] == 'Pending' for row in pending)
    else:
        assert len(out['items']) == 1 and out['output_truncated']


@pytest.mark.parametrize('queue_id', ['.', '..', 'bad/segment', '%2e%2e'])
async def test_invalid_queue_address_fails_without_requests(queue_id):
    from mlspace_mcp.errors import MLSpaceError
    clients = Clients(queue)
    with pytest.raises(MLSpaceError):
        await inspect_queue(clients, queue_id, target='A')
    assert not clients.calls


async def test_pending_handler_sends_priority_limit_offset():
    from types import SimpleNamespace

    from conftest import make_settings

    from mlspace_mcp.registry import _build_handler, available_actions
    from mlspace_mcp.tools.queues import DOMAIN
    clients = Clients(lambda ws, path, params: queue(ws, path, dict(params)))
    ctx = SimpleNamespace(request_context=SimpleNamespace(
        lifespan_context=SimpleNamespace(clients=clients)))
    settings = make_settings()
    handler = _build_handler(DOMAIN, available_actions(DOMAIN, settings.readonly), settings)
    await handler(action='awaiting_launch_resources', target='A', queue_id='q',
                  priority='high', limit=2, offset=0, ctx=ctx)
    assert dict(clients.calls[-1][2]) == {'priority': 'high', 'limit': 2, 'offset': 0}


async def test_bad_allocation_address_does_not_escape_queue_reads():
    def respond(ws, path, params):
        result = queue(ws, path, params)
        if path.endswith('/q'):
            result['allocation_id'] = '..'
        return result
    clients = Clients(respond)
    out = await inspect_queue(clients, 'q', target='A', candidate_nodes=['node-1'])
    assert not out['fetch_complete']
    assert all('/allocations/' not in path for _, path, _ in clients.calls)


@pytest.mark.parametrize('node', [
    {'name': 'node-1'},
    {'name': []},
    {'name': 'node-1', 'status': 'Ready', 'unschedulable': 'false',
     'resource_usage': {}},
    {'name': 'node-1', 'status': 'Ready', 'unschedulable': False,
     'resource_usage': {'total': {'cpu': 64, 'ram': 512, 'gpu': 8}}},
])
async def test_incomplete_allocation_node_facts_are_partial_without_losing_observations(node):
    def respond(ws, path, params):
        return {'nodes': [node]} if '/allocations/' in path else queue(ws, path, params)
    out = await inspect_queue(Clients(respond), 'q', target='A', candidate_nodes=['node-1'])
    assert not out['fetch_complete'] and not out['complete']
    assert any(f['source'] == 'nodes' and f['reason'] == 'malformed_response' for f in out['failures'])
    assert any(item['source'] == 'pods' for item in out['items'])
    assert any(item['source'] == 'pending' for item in out['items'])


async def test_documented_notebook_memory_limits_preserved():
    def respond(ws, path, params):
        result = queue(ws, path, params)
        if path.endswith('/load'):
            result['loads'][0]['notebooks'] = [{'id': 'nb', 'limits': {
                'cpu': '8', 'gpu': '1', 'memory': {'count': '64', 'postfix': 'Gi'}}}]
        return result
    out = await inspect_queue(Clients(respond), 'q', target='A', candidate_nodes=['node-1'])
    load = next(item for item in out['items'] if item['source'] == 'loads')
    assert load['data']['notebooks'][0]['limits']['memory'] == {'count': '64', 'postfix': 'Gi'}


@pytest.mark.parametrize('field,value', [
    ('status', None), ('unschedulable', 'false'), ('name', []),
    ('resource_usage', {'total': {'cpu': 1, 'ram': 1, 'gpu': 1}}),
    ('instance_types', None),
])
async def test_required_node_field_types_cannot_claim_complete(field, value):
    def respond(ws, path, params):
        result = queue(ws, path, params)
        if '/allocations/' in path:
            result['nodes'][0][field] = value
        return result
    out = await inspect_queue(Clients(respond), 'q', target='A', candidate_nodes=['node-1'])
    assert not out['fetch_complete']
    assert any(f['source'] == 'nodes' for f in out['failures'])


@pytest.mark.parametrize('value', ['8', True, -1, float('nan')])
async def test_invalid_usage_numbers_are_partial(value):
    def respond(ws, path, params):
        result = queue(ws, path, params)
        if '/allocations/' in path:
            result['nodes'][0]['resource_usage']['allocated']['gpu'] = value
        return result
    out = await inspect_queue(Clients(respond), 'q', target='A', candidate_nodes=['node-1'])
    assert not out['fetch_complete']
    assert any(f['source'] == 'nodes' for f in out['failures'])
