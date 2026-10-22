"""SR008 isolation applies to NFS-to-NFS only, not every transfer using NFS."""
import httpx
import pytest
import respx
from conftest import make_settings, runtime_ctx
from mcp.server.fastmcp.exceptions import ToolError

from mlspace_mcp.errors import MLSpaceError
from mlspace_mcp.registry import _build_handler, available_actions, resolve_request
from mlspace_mcp.tools.data_transfer import DOMAIN


def _body(route, source='nfs', destination='nfs'):
    return dict(name='copy', cluster_name=route, connector_id='source',
                destination_connector_id='destination', connector_type=source,
                destination_connector_type=destination, strategy='write_all',
                favorite=False, query={'source': ['/input/'], 'destination': '/output/'})


@pytest.mark.parametrize('action', ['create_transfer', 'update_transfer'])
@pytest.mark.parametrize('route', ['sr008->sr009', 'sr009->sr008.nfs2',
                                 'sr008.fs2->pd30', 'pd42.nfs3->sr008.nfs3'])
@pytest.mark.parametrize('source,destination', [('nfs', 'nfs'), ('nfsprivate', 'nfsshared'),
                                              ('nfsshared', 'nfsprivate')])
def test_sr008_external_nfs_route_is_refused(action, route, source, destination):
    with pytest.raises(MLSpaceError, match='SR008'):
        resolve_request(DOMAIN.actions[action],
                        {'id_': 'transfer', 'body': _body(route, source, destination)})


@pytest.mark.parametrize('route,source,destination', [
    ('sr008.nfs2->sr008.nfs3', 'nfs', 'nfs'),
    ('sr008.fs2->sr008.nfs2', 'nfsshared', 'nfsprivate'),
    ('sr008', 'nfs', 'nfs'),
    ('sr008.nfs3', 'nfsprivate', 'nfsshared'),
    ('pd30->sr009', 'nfs', 'nfs'),
    ('sr009->christofari-1', 'nfs', 'nfs'),  # enum absence is not a local veto
    ('future-region->sr009', 'nfs', 'nfs'),
    ('sr008->sr009', 's3custom', 'nfs'),
    ('sr009->sr008', 'nfs', 's3evolution'),
    ('sr008->sr009', 'postgresql', 'nfsprivate'),
    ('sr008->sr009', 's3custom', 's3evolution'),
])
def test_other_routes_are_left_to_api_validation(route, source, destination):
    body = _body(route, source, destination)
    assert resolve_request(DOMAIN.actions['create_transfer'], {'body': body})[-1] == body


async def test_sr008_refusal_happens_before_auth_or_api_calls():
    settings = make_settings(readonly=False)
    handler = _build_handler(DOMAIN, available_actions(DOMAIN, False), settings)
    with respx.mock as mock:
        async with httpx.AsyncClient() as http:
            with pytest.raises(ToolError, match='SR008'):
                await handler(action='create_transfer', body=_body('sr009->sr008.nfs2'),
                              ctx=runtime_ctx(http, settings))
        assert not mock.calls
