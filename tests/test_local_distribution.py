"""Distribution must never resolve the MLSpace package from a package index."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


@pytest.mark.parametrize('filename,root', [('mcp.json', '${PLUGIN_ROOT}'),
                                         ('.mcp.json', '${CLAUDE_PLUGIN_ROOT}')])
def test_native_manifest_runs_the_bundled_source(filename, root):
    import gen_plugin
    server = json.loads(gen_plugin.build()[filename])['mcpServers']['mlspace']
    assert server['command'] == 'uvx'
    assert server['args'] == ['--python', '>=3.10', '--from', root, 'mlspace-plugin', '--transport', 'stdio']


def test_release_has_an_installable_runtime_at_its_plugin_root(tmp_path):
    import build_plugin
    root, _ = build_plugin.build_release(tmp_path / 'path with spaces')
    assert (root / 'pyproject.toml').is_file()
    assert (root / 'src/mlspace_mcp/__main__.py').is_file()
    assert (root / 'skills/mlspace-list-running-jobs/SKILL.md').is_file()
    assert (root / 'plugin.json').is_file()


def cached_install(tmp_path, monkeypatch, provenance=None):
    from mlspace_mcp import client_setup
    source = tmp_path / 'archive-v0/env/lib/site-packages/mlspace_mcp/client_setup.py'
    source.parent.mkdir(parents=True)
    source.touch()
    monkeypatch.setattr(client_setup, '__file__', str(source))
    monkeypatch.setattr('shutil.which', lambda name: '/bin/uvx' if name == 'uvx' else None)
    if provenance is not None:
        meta = source.parent.parent / 'mlspace_plugin-0.3.7.dist-info'
        meta.mkdir()
        (meta / 'direct_url.json').write_text(json.dumps(provenance))
    return client_setup


def test_missing_cache_provenance_never_falls_back_to_index(tmp_path, monkeypatch):
    client_setup = cached_install(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='источник'):
        client_setup.server_command()


def test_git_bootstrap_records_immutable_source_not_cache_path(tmp_path, monkeypatch):
    sha = 'a' * 40
    client_setup = cached_install(tmp_path, monkeypatch, {
        'url': 'https://example.invalid/mlspace.git',
        'vcs_info': {'vcs': 'git', 'commit_id': sha, 'requested_revision': 'main'},
    })
    assert client_setup.server_command() == [
        '/bin/uvx', '--python', '>=3.10', '--from', f'git+https://example.invalid/mlspace.git@{sha}',
        'mlspace-plugin', '--transport', 'stdio',
    ]


def test_provenance_with_credentials_is_not_written_to_client_config(tmp_path, monkeypatch):
    client_setup = cached_install(tmp_path, monkeypatch, {
        'url': 'https://user:private-test-token@example.invalid/mlspace.git',
        'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40},
    })
    with pytest.raises(ValueError) as error:
        client_setup.server_command()
    assert 'private-test-token' not in str(error.value)


def test_local_plugin_path_with_spaces_survives_cache_eviction(tmp_path, monkeypatch):
    root = tmp_path / 'installed plugin'
    root.mkdir()
    (root / 'pyproject.toml').write_text('[project]\nname="mlspace-plugin"\n')
    client_setup = cached_install(tmp_path, monkeypatch, {'url': root.as_uri(), 'dir_info': {}})
    # Model a persistent checkout even when pytest stores fixtures under /tmp on Linux.
    monkeypatch.setattr(client_setup, '_temporary_source', lambda path: False)
    command = client_setup.server_command()
    assert command == ['/bin/uvx', '--python', '>=3.10', '--from', str(root), 'mlspace-plugin', '--transport', 'stdio']
    assert 'archive-v0' not in ' '.join(command)


def test_release_excludes_local_credentials_and_rejects_source_symlink(tmp_path, monkeypatch):
    import build_plugin
    checkout, _ = build_plugin.build_release(tmp_path / 'seed')
    monkeypatch.setattr(build_plugin, 'ROOT', checkout)
    (checkout / '.env').write_text('private-test-credential')
    (checkout / 'src/mlspace_mcp/.hidden.py').write_text('private-test-credential')
    (checkout / 'src/mlspace_mcp/private.secret').write_text('private-test-credential')
    bundle, _ = build_plugin.build_release(tmp_path / 'clean')
    assert not (bundle / '.env').exists()
    assert not (bundle / 'src/mlspace_mcp/.hidden.py').exists()
    assert not (bundle / 'src/mlspace_mcp/private.secret').exists()
    (checkout / 'src/mlspace_mcp/link.py').symlink_to(checkout / '.env')
    with pytest.raises(ValueError, match='Symlinks'):
        build_plugin.build_release(tmp_path / 'rejected')
