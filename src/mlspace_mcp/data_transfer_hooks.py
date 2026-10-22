"""Transfer preflight and evidence boundaries, without additional API calls."""
from __future__ import annotations

import math
from typing import Any

from .errors import MLSpaceError


def preflight_transfer_write(values: dict[str, Any]) -> None:
    """Check NFS isolation and refuse intervals that may be silently truncated."""
    body = values.get('body')
    if not isinstance(body, dict):
        return
    # Service-team clarification (2026-10-19): isolation is NFS -> NFS only.
    # Do not infer restrictions on S3/database transfers from a region label.
    nfs_types = ('nfs', 'nfsprivate', 'nfsshared')
    route = body.get('cluster_name')
    if (body.get('connector_type') in nfs_types
            and body.get('destination_connector_type') in nfs_types
            and isinstance(route, str)):
        ends = route.split('->')
        if len(ends) == 2:
            in_sr008 = [end.strip().split('.')[0] == 'sr008' for end in ends]
            if in_sr008[0] != in_sr008[1]:
                raise MLSpaceError(
                    'SR008 NFS storage is isolated: NFS-to-NFS transfers cannot cross '
                    'between SR008 and another region.',
                    hint='Within SR008, use an established internal NFS route (nfs2, nfs3, fs2). '
                         'Do not substitute another destination or stage through S3 automatically.',
                )
    cron = body.get('crontab')
    period = cron.get('period') if isinstance(cron, dict) else None
    if period is None:
        return
    if (isinstance(period, bool) or not isinstance(period, (int, float))
            or not math.isfinite(period) or period < 0 or period != int(period)):
        raise MLSpaceError(
            'crontab.period must be a finite, non-negative whole number.',
            hint='Fractional periods can be silently truncated by the API. '
                 'Do not round or invent units; clarify the intended schedule before retrying.',
        )

_DATA_IMPACT_GUIDANCE = (
    'In the final diagnosis explicitly state: Data impact is unverified. '
    'Do not claim that the source was read, nothing was written, or destination '
    'data is unchanged without an independent contents check. A failed PutObject '
    'describes that request, not other objects or earlier attempts in the run.'
)


def _unknown_evidence() -> dict[str, str]:
    return {
        'failure_cause': 'unknown',
        'failure_phase': 'unknown',
        'data_impact': 'unknown',
        'reporting_requirement': _DATA_IMPACT_GUIDANCE,
        'basis': 'Counters/empty logs alone; independent evidence may establish more.',
        'interpretation': (
            'Reported counters (including zero) and absent logs do not prove that '
            'the run stopped before reading/writing or left destination data unchanged.'
        ),
    }


def history_evidence(data: Any, values: dict[str, Any]) -> Any:
    """Preserve history shape and counters; failed/cancelled entries lack a diagnosis."""
    if isinstance(data, list):
        return [history_evidence(row, values) for row in data]
    if isinstance(data, dict):
        if isinstance(data.get('data'), list):
            return {**data, 'data': history_evidence(data['data'], values)}
        if data.get('status') in ('failed', 'cancelled'):
            return {**data, '_mcp_evidence': _unknown_evidence()}
    return data


def log_evidence(data: Any, values: dict[str, Any]) -> Any:
    """Keep log evidence intact; individual events are not a destination-data audit."""
    empty = data is None or data == [] or data == {} or data == ''
    recognized = isinstance(data, list)
    if isinstance(data, dict) and data:
        collections = {'items', 'data', 'events', 'logs'}
        metadata = {'count', 'total_count', 'offset', 'limit', 'page', 'page_size', 'status'}
        present = collections & data.keys()
        recognized = any(isinstance(data[key], list) for key in present)
        empty = bool(present) and all(data[key] == [] for key in present) and (
            not (data.keys() - collections - metadata)
        ) and all(not data.get(key) for key in ('count', 'total_count'))
    if empty:
        return {'result': data, '_mcp_evidence': _unknown_evidence()}
    if recognized:
        evidence = {
            'data_impact': 'unknown',
            'reporting_requirement': _DATA_IMPACT_GUIDANCE,
            'basis': 'Logs describe individual events, not a complete source/destination audit.',
            'interpretation': (
                'Use explicit log messages to identify cause and failure phase. '
                'Report data impact separately as unknown until source/destination contents '
                'are independently checked. Even AccessDenied and all-zero counters do not '
                'establish that this entire run left destination data unchanged.'
            ),
        }
        result = data if isinstance(data, dict) else {'result': data}
        return {**result, '_mcp_evidence': evidence}
    return data
