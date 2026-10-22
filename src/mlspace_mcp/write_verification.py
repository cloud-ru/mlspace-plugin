"""Read back consequential ETL fields after writes; never repeat a mutation."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID

from .errors import MLSpaceError


def _unverified(resource: str, detail: str, action: str) -> MLSpaceError:
    return MLSpaceError(
        f'Write accepted for {resource}, but {detail}.',
        hint=f'Do not repeat the write automatically. Inspect {action} in the same workspace '
             'before deciding how to proceed; the resource may already have changed.',
    )


async def _read(client: Any, path: str, resource: str, action: str) -> dict[str, Any]:
    try:
        saved = await client.request('GET', path)
    except Exception as exc:
        # Do not rethrow a GET error as if the preceding write failed. Raw API
        # error bodies may also contain submitted credentials or labels.
        raise _unverified(resource, 'read-back verification is unavailable', action) from exc
    if not isinstance(saved, dict):
        raise _unverified(resource, 'read-back response has an unexpected shape', action)
    return saved


def _same_schedule(requested: Any, saved: Any) -> bool:
    if requested is None:
        return saved is None
    if not isinstance(requested, dict) or not isinstance(saved, dict):
        return False
    for key, expected in requested.items():
        if key not in saved:
            return False
        actual = saved[key]
        if key == 'start_at' and isinstance(expected, str) and isinstance(actual, str):
            try:
                # Python 3.10 needs +00:00 instead of a trailing Z.
                expected = datetime.fromisoformat(expected.replace('Z', '+00:00'))
                actual = datetime.fromisoformat(actual.replace('Z', '+00:00'))
            except ValueError:
                return False
        elif key in ('weekdays', 'monthdays') and isinstance(expected, list):
            if not isinstance(actual, list) or not all(type(x) is int for x in expected + actual):
                return False
            expected, actual = sorted(expected), sorted(actual)
        if expected != actual:
            return False
    return True


async def verify_transfer_schedule(client: Any, data: Any, values: dict[str, Any], *,
                                   update: bool = False) -> None:
    body = values.get('body')
    if not isinstance(body, dict) or 'crontab' not in body:
        return
    transfer_id = values.get('id_') if update else None
    if not transfer_id:
        # A create response is an external input; never use it to construct an
        # arbitrary URL. The API contract specifies a UUID for transfer_id.
        try:
            transfer_id = str(UUID(data['transfer_id']))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise _unverified('new transfer', 'the response has no valid transfer_id',
                              'list_transfers to find the created transfer') from exc
    resource = f'transfer {transfer_id}'
    saved = await _read(client, '/public/v2/data_transfer/v4/transfer/' + quote(transfer_id, safe=''),
                        resource, 'get_transfer')
    if (saved.get('transfer_id') != transfer_id or 'crontab' not in saved
            or not _same_schedule(body['crontab'], saved['crontab'])):
        raise _unverified(resource, 'the requested schedule was not confirmed', 'get_transfer')


async def verify_repository_labels(client: Any, data: Any, values: dict[str, Any]) -> None:
    body = values.get('body')
    if not isinstance(body, dict) or not (body.get('labels') or body.get('labels_to_delete')):
        return
    repository_id = values['repository_id']
    resource = f'repository {repository_id}'
    saved = await _read(client, '/public/v2/docker_registry/v2/repositories/'
                        + quote(repository_id, safe=''), resource, 'get_repo')
    labels = saved.get('labels')
    if (saved.get('id') != repository_id or not isinstance(labels, list)
            or not all(isinstance(label, str) for label in labels)
            or not set(body.get('labels') or []).issubset(labels)
            or set(body.get('labels_to_delete') or []).intersection(labels)):
        raise _unverified(resource, 'the requested label changes were not confirmed', 'get_repo')
