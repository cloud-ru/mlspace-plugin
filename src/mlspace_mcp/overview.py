"""Bounded read inventories. Fetch coverage is not an attestation of API visibility."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from ._status import JOB_STATUS_CANON
from .config import Settings
from .errors import MLSpaceError
from .resource_refs import response_references

PRIORITIES = ('shared-low', 'shared-medium', 'shared-cluster', 'low', 'medium', 'high')
# Positive field selection: inventory endpoints can contain S3 credentials, env, etc.
_FIELDS = frozenset(('id uid uuid name job_name job_id notebook_uuid namespace workspace '
    'workspace_id region status phase state author job_author user_email node_name nodes '
    'instance_type instanceType creationTimestamp created_at type priority gpu_count '
    'unschedulable_worker_count n_workers launch_order allocation_id queue_id queue_label '
    'allocation_label allocation_name queue_name created_dt updated_dt completed_dt job_desc '
    'unschedulable resource_usage queue gpu_type cpu memory ram gpu used total allocated '
    'requested available limit limits count postfix instance_types jobs notebooks').split())


def _inventory(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _inventory(v) for k, v in value.items() if k in _FIELDS}
    if isinstance(value, list):
        return [_inventory(v) for v in value]
    return value


def _segment(value: str) -> str:
    if not value.strip() or value in {'.', '..'} or re.search(r'[/\\?#%\x00-\x1f\x7f]', value):
        raise MLSpaceError('Invalid opaque resource address.')
    return quote(value, safe='')


def _rows(data: Any, key: str) -> list[dict]:
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise MLSpaceError('malformed_response')
    rows = data[key]
    if not all(isinstance(row, dict) for row in rows):
        raise MLSpaceError('malformed_response')
    return rows


def _node_facts(row: dict) -> None:
    """Validate the operational facts required by NodeOverview before claiming coverage."""
    if any(not isinstance(row.get(key), str) or not row[key].strip()
           for key in ('name', 'status', 'gpu_type')) or type(row.get('unschedulable')) is not bool:
        raise MLSpaceError('malformed_response')
    _rows(row, 'instance_types')
    usage = row.get('resource_usage')
    if not isinstance(usage, dict):
        raise MLSpaceError('malformed_response')
    for key in ('total', 'allocated', 'available'):
        resources = usage.get(key)
        if not isinstance(resources, dict):
            raise MLSpaceError('malformed_response')
        for field in ('cpu', 'ram', 'gpu'):
            value = resources.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise MLSpaceError('malformed_response')


def _identity(row: dict) -> str:
    for key in ('id', 'uid', 'uuid', 'job_name', 'name'):
        if isinstance(row.get(key), str) and row[key]:
            return json.dumps([key, row[key], row.get('workspace_id'), row.get('namespace'),
                               row.get('type')], sort_keys=True)
    # No documented stable identity: exact duplicate pages still must not loop.
    return hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()


class _Read:
    def __init__(self, clients: Any, targets: list[str] | None, page_size: int,
                 max_pages: int, max_requests: int, max_items: int):
        for value, upper in ((page_size, 1000), (max_pages, 200),
                             (max_requests, 1000), (max_items, 1000)):
            if type(value) is not int or not 1 <= value <= upper:
                raise MLSpaceError(f'Budget must be an integer between 1 and {upper}.')
        self.clients = clients
        self.page_size, self.max_pages = page_size, max_pages
        self.remaining, self.max_items = max_requests, max_items
        self.semaphore = asyncio.Semaphore(4)
        self.output_bytes = 0
        self.out: dict[str, Any] = {
            'environment': clients.environment,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'requested_scope': {'targets': targets if targets is not None
                                else [s.id for s in clients.selected]},
            'connected_contexts': [{'id': s.id, 'name': s.name, 'project_name': s.project_name}
                                   for s in clients.selected],
            'checked_scope': [], 'failures': [], 'items': [], 'observed_count': 0,
            'output_truncated': False,
        }
        self.targets: list[str] = []
        for target in self.out['requested_scope']['targets']:
            try:
                ident = clients.select(target).id
                if ident not in self.targets:
                    self.targets.append(ident)
            except MLSpaceError:
                self.fail({'target': target}, 'unconfigured_or_ambiguous_target')
        if not self.targets:
            self.fail({}, 'empty_scope')

    def fail(self, scope: dict, reason: str) -> None:
        self.out['failures'].append({**scope, 'reason': reason})

    async def get(self, target: str, path: str, params: dict | None = None) -> Any:
        async with self.semaphore:
            if self.remaining <= 0:
                raise MLSpaceError('request_budget_exhausted')
            self.remaining -= 1

            async def request() -> Any:
                client = await self.clients.resolve(target)
                return await client.request('GET', path, params=params)
            return await asyncio.wait_for(request(), timeout=30)

    def error(self, scope: dict, exc: Exception) -> None:
        # Provider bodies / exception strings may carry credentials: never echo them.
        safe = {'request_budget_exhausted', 'malformed_response', 'inconsistent_count',
                'repeated_items', 'unknown_regions', 'missing_node_facts', 'queue_identity_mismatch'}
        reason = str(exc) if isinstance(exc, MLSpaceError) and str(exc) in safe else type(exc).__name__
        self.fail(scope, reason)
        status = getattr(exc, 'status', None)
        if type(status) is int:
            self.out['failures'][-1]['http_status'] = status

    def add(self, row: dict, scope: dict, *, owner: bool = False) -> None:
        provenance = {'environment': self.clients.environment,
                      'request_workspace_id': scope['target'],
                      'endpoint': scope['endpoint']}
        if owner:
            provenance['workspace_id'] = row.get('workspace_id', scope['target'])
        if scope.get('region'):
            provenance['region'] = scope['region']
        item = {'source': scope['source'], 'data': _inventory(row), 'provenance': provenance}
        if owner:
            refs = response_references(scope['source'], 'list', [row], environment=self.clients.environment,
                                       target=scope['target'], values=scope, list_cap=1)
            if refs:
                item['resource_ref'] = refs[0]
        if 'priority' in scope:
            item['priority'] = scope['priority']
        self.out['observed_count'] += 1
        size = len(json.dumps(item).encode())
        if len(self.out['items']) >= self.max_items or self.output_bytes + size > 200_000:
            self.out['output_truncated'] = True
        else:
            self.out['items'].append(item)
            self.output_bytes += size

    async def pages(self, target: str, path: str, key: str, *, counted: bool,
                    params: dict | None = None, owner: bool = False, source: str | None = None) -> None:
        query = dict(params or {})
        scope = {'target': target, 'endpoint': path, 'source': source or key, **query,
                 'pages_read': 0, 'next_offset': 0, 'fetch_complete': False}
        self.out['checked_scope'].append(scope)
        seen: set[str] = set()
        expected = None
        try:
            for _ in range(self.max_pages):
                data = await self.get(target, path, {**query, 'limit': self.page_size,
                                                    'offset': scope['next_offset']})
                rows = _rows(data, key)
                scope['pages_read'] += 1
                if counted:
                    count = data.get('count')
                    if type(count) is not int or count < 0 or (expected is not None and count != expected):
                        raise MLSpaceError('inconsistent_count')
                    expected = count
                    if scope['next_offset'] + len(rows) > count or (not rows and scope['next_offset'] != count):
                        raise MLSpaceError('inconsistent_count')
                identities = [_identity(row) for row in rows]
                # Repeated identities make coverage uncertain, but the observed
                # workload must remain visible. add() still applies redaction and
                # output limits; observed_count is not a unique-resource total.
                for row in rows:
                    self.add(row, scope, owner=owner)
                if len(set(identities)) != len(identities) or seen.intersection(identities):
                    raise MLSpaceError('repeated_items')
                seen.update(identities)
                scope['next_offset'] += len(rows)
                if not rows or (counted and scope['next_offset'] == expected):
                    scope['fetch_complete'] = True
                    return
            self.fail(scope, 'page_budget_exhausted')
        except Exception as exc:
            self.error(scope, exc)

    def finish(self) -> dict:
        self.out['fetch_complete'] = not self.out['failures']
        self.out['complete'] = self.out['fetch_complete'] and not self.out['output_truncated']
        self.out['budget_basis'] = 'max_requests counts logical inventory GET calls; client authentication, lazy key discovery and a possible 401 retry are additional transport calls'
        self.out['count_basis'] = 'observations; not a deduplicated resource or capacity total'
        # Metadata (e.g. unexpectedly huge region names) is also provider-controlled.
        # Preserve the truth flags even if a whole section cannot fit in the response.
        for key in ('checked_scope', 'items', 'failures', 'candidates', 'connected_contexts',
                    'requested_scope', 'filtering_basis'):
            if len(json.dumps(self.out).encode()) <= 250_000:
                break
            if key not in self.out:
                continue
            old = self.out[key]
            self.out.setdefault('omitted_output', {})[key] = {
                'entries': len(old), 'reason': 'output_byte_budget'}
            self.out[key] = [] if isinstance(old, list) else {'omitted': True}
            self.out['output_truncated'] = True
            self.out['complete'] = False
        return self.out


async def aggregate(clients: Any, kind: Literal['jobs', 'notebooks'], *,
                    targets: list[str] | None = None, author: str | None = None,
                    status: list[str] | None = None, page_size: int = 100, max_pages: int = 50, max_requests: int = 200,
                    max_items: int = 200) -> dict:
    if kind not in {'jobs', 'notebooks'} or ((author is not None or status is not None) and kind != 'jobs'):
        raise MLSpaceError('Only jobs support an explicit author filter.')
    read = _Read(clients, targets, page_size, max_pages, max_requests, max_items)
    read.out['requested_scope']['kind'] = kind
    normalized_status = [JOB_STATUS_CANON.get(s.casefold(), s) for s in status] if status is not None else None
    if normalized_status is not None:
        read.out['requested_scope']['status'] = normalized_status
    read.out['filtering_basis'] = {'author': author, 'basis': 'explicit_user_filter' if author is not None else 'all_workspace_authors'}

    async def workspace(target: str) -> None:
        if kind == 'notebooks':
            await read.pages(target, '/public/v2/notebooks/v2/notebooks', 'notebooks',
                             counted=False, owner=True)
            return
        # The launch catalog can omit allocated regions that still contain jobs.
        # Read both sources independently so either failure retains known regions
        # while keeping the overall result explicitly partial.
        keys: list[str] = []
        for source, path, query in (
            ('regions', '/public/v2/configs', {'cluster_type': 'MT'}),
            ('allocation_regions', '/public/v2/workspaces/v3/{workspace_id}/allocations', None),
        ):
            scope = {'target': target, 'endpoint': path, 'source': source, 'fetch_complete': False}
            read.out['checked_scope'].append(scope)
            try:
                if source == 'allocation_regions':
                    path = path.format(workspace_id=_segment(target))
                    scope['endpoint'] = path
                data = await read.get(target, path, query)
                rows = _rows(data, 'regions') if source == 'regions' else _rows({'allocations': data}, 'allocations')
                field = 'key' if source == 'regions' else 'region_key'
                discovered: list[str] = []
                for row in rows:
                    key = row.get(field)
                    if not isinstance(key, str) or not key.strip():
                        raise MLSpaceError('unknown_regions')
                    discovered.append(key)
                keys.extend(discovered)
                scope['regions'] = list(dict.fromkeys(discovered))
                scope['fetch_complete'] = True
            except Exception as exc:
                read.error(scope, exc)
        if not keys:
            read.fail({'target': target, 'source': 'regions'}, 'unknown_regions')
        for region in dict.fromkeys(keys):
            params: dict[str, Any] = {'region': region}
            if normalized_status is not None:
                params['status'] = normalized_status
            if author is not None:
                params['job_author'] = [author]
            await read.pages(target, '/public/v2/jobs', 'jobs', counted=True,
                             params=params, owner=True)
    await asyncio.gather(*(workspace(target) for target in read.targets))
    return read.finish()


async def inspect_queue(clients: Any, queue_id: str, *, target: str | None = None,
                        candidate_nodes: list[str] | None = None, targets: list[str] | None = None,
                        page_size: int = 100, max_pages: int = 50, max_requests: int = 200,
                        max_items: int = 200) -> dict:
    primary = clients.select(target).id
    queue_segment = _segment(queue_id)
    read = _Read(clients, targets, page_size, max_pages, max_requests, max_items)
    read.out['requested_scope'].update(queue_id=queue_id, source_target=primary)
    read.out['cross_workspace_coverage'] = {
        'status': 'unknown',
        'reason': 'Queue API does not attest all workspace visibility, even when every connected context responds.',
    }
    read.out['interpretation'] = {
        'active_workload': 'Inspect observed pod status and node load; retain contradictions with notebook status.',
        'pending_demand': 'All six priorities queried separately; duplicate observations are not summed.',
        'paused_notebooks': 'Inventory only; configured GPU does not prove active consumption.',
        'capacity': 'Node facts fetched once from the source context; no capacity summation.',
    }
    path = '/public/v2/queues/' + queue_segment

    async def single(ws: str, endpoint: str, key: str, params: dict | None = None) -> list[dict] | None:
        scope = {'target': ws, 'endpoint': endpoint, 'source': key, 'fetch_complete': False}
        read.out['checked_scope'].append(scope)
        try:
            rows = _rows(await read.get(ws, endpoint, params), key)
            if key == 'nodes':
                for row in rows:
                    _node_facts(row)
            if key == 'loads':
                for row in rows:
                    if not isinstance(row.get('node_name'), str):
                        raise MLSpaceError('malformed_response')
                    _rows(row, 'jobs')
                    _rows(row, 'notebooks')
            for row in rows:
                read.add(row, scope)
            scope['fetch_complete'] = True
            return rows
        except Exception as exc:
            read.error(scope, exc)
            return None

    async def details(ws: str) -> dict | None:
        scope = {'target': ws, 'endpoint': path, 'source': 'queue', 'fetch_complete': False}
        read.out['checked_scope'].append(scope)
        try:
            data = await read.get(ws, path)
            if not isinstance(data, dict) or data.get('id') != queue_id:
                raise MLSpaceError('queue_identity_mismatch')
            if not isinstance(data.get('nodes'), list) or not all(isinstance(n, str) for n in data['nodes']) or not isinstance(data.get('allocation_id'), str):
                raise MLSpaceError('malformed_response')
            _segment(data['allocation_id'])
            read.add(data, scope)
            scope['fetch_complete'] = True
            return data
        except Exception as exc:
            read.error(scope, exc)
            return None

    source = await details(primary)
    nodes = source['nodes'] if source else []
    requested_nodes = list(dict.fromkeys(candidate_nodes if candidate_nodes is not None else nodes))
    valid = [n for n in requested_nodes if n in nodes]
    invalid = [n for n in requested_nodes if n not in nodes]
    read.out['candidates'] = {'validated': valid[:max_items], 'invalid': invalid[:max_items],
                              'membership_known': source is not None}
    if len(valid) > max_items or len(invalid) > max_items:
        read.out['output_truncated'] = True
    if invalid:
        read.fail({'target': primary, 'source': 'candidates'}, 'candidate_not_in_source_queue')

    async def workspace(ws: str) -> None:
        if ws != primary:
            sibling = await details(ws)
            if sibling is None:
                return
            if source and sibling['allocation_id'] != source['allocation_id']:
                read.fail({'target': ws, 'source': 'queue'}, 'queue_identity_mismatch')
                return
        await asyncio.gather(*(single(ws, path + '/' + key, key)
                               for key in ('jobs', 'notebooks', 'pods')))
        # Separate priority pages preserve coverage and never sum shared observations.
        for priority in PRIORITIES:
            await read.pages(ws, path + '/awaiting-launch-resources', 'items', counted=True,
                             params={'priority': priority}, source='pending')

    await asyncio.gather(*(workspace(ws) for ws in read.targets))
    if source and valid:
        allocation = _segment(source['allocation_id'])
        facts = await single(primary, f'/public/v2/allocations/{allocation}/nodes', 'nodes')
        loads: list[dict] = []
        for start in range(0, len(valid), 100):
            batch = await single(primary, '/public/v2/nodes/load', 'loads',
                                 {'node_names': valid[start:start + 100]})
            if batch is not None:
                loads.extend(batch)
        for rows, key in ((facts, 'name'), (loads, 'node_name')):
            if rows is not None and not set(valid).issubset({r.get(key) for r in rows}):
                read.fail({'target': primary, 'source': key}, 'missing_node_facts')
    return read.finish()


def register_overviews(mcp: FastMCP, settings: Settings) -> None:
    def runtime(ctx: Context) -> Any:
        return ctx.request_context.lifespan_context.clients

    async def mlspace_jobs_overview(ctx: Context, targets: list[str] | None = None,
                                    author: str | None = None, status: list[str] | None = None, page_size: int = 100,
                                    max_pages: int = 50, max_requests: int = 200,
                                    max_items: int = 200) -> str:
        return json.dumps(await aggregate(runtime(ctx), 'jobs', targets=targets, author=author, status=status,
            page_size=page_size, max_pages=max_pages, max_requests=max_requests, max_items=max_items))

    async def mlspace_notebooks_overview(ctx: Context, targets: list[str] | None = None,
                                         page_size: int = 100, max_pages: int = 50,
                                         max_requests: int = 200, max_items: int = 200) -> str:
        return json.dumps(await aggregate(runtime(ctx), 'notebooks', targets=targets,
            page_size=page_size, max_pages=max_pages, max_requests=max_requests, max_items=max_items))

    async def mlspace_queue_inspect(queue_id: str, ctx: Context, target: str | None = None,
                                    candidate_nodes: list[str] | None = None,
                                    targets: list[str] | None = None, page_size: int = 100,
                                    max_pages: int = 50, max_requests: int = 200,
                                    max_items: int = 200) -> str:
        return json.dumps(await inspect_queue(runtime(ctx), queue_id, target=target,
            candidate_nodes=candidate_nodes, targets=targets, page_size=page_size,
            max_pages=max_pages, max_requests=max_requests, max_items=max_items))

    enabled = settings.enabled_domain_set
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                  idempotentHint=True, openWorldHint=True)
    for domain, fn, description in (
        ('jobs', mlspace_jobs_overview, 'Read jobs across all saved contexts (or explicit targets), regions discovered from both the MT launch catalog and workspace allocations, and pages. Optional status filters jobs before pagination; author is an explicit filter, not verified personal identity.'),
        ('notebooks', mlspace_notebooks_overview, 'Read notebook inventory across all saved contexts (or explicit targets), including paused notebooks and their status. Configured GPUs do not prove activity.'),
        ('queues', mlspace_queue_inspect, 'Inspect a source queue before considering a compute-node move: jobs/notebooks/pods, all six pending priorities, candidate membership and node facts. target selects source; targets selects observation contexts (default all saved). Cross-workspace visibility remains unknown; no safety-to-move conclusion.'),
    ):
        if enabled is None or domain in enabled:
            mcp.add_tool(fn, description=description + ' Bounded reads report requested/checked scope, failures, fetch_complete and output_truncated; complete requires both full fetch and full output. max_pages applies per stream, max_requests globally; max_items bounds output only.', annotations=annotations)
