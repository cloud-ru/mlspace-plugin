import json
import sys

import pytest

from mlspace_mcp import client_setup as setup
from mlspace_mcp.setup_verification import verify_client


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / '.config'))
    monkeypatch.delenv('OPENCODE_CONFIG', raising=False)
    monkeypatch.setattr(setup, 'detect_clients', lambda: {'opencode': '/usr/bin/true'})
    credentials = tmp_path / 'fake.env'
    credentials.write_text('MLSPACE_CLIENT_ID=dummy\nMLSPACE_CLIENT_SECRET=dummy\n'
                           'MLSPACE_WORKSPACE_ID=dummy\nMLSPACE_BASE_URL=https://127.0.0.1:9\n')
    command = [sys.executable, '-m', 'mlspace_mcp', '--transport', 'stdio']
    return tmp_path / '.config/opencode', ({'opencode': '/usr/bin/true'}, credentials, command)


def write_skill(root, name, content):
    path = root / 'skills' / name / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_additional_user_skill_survives_install_repeat_and_real_verification(profile):
    root, args = profile
    custom = write_skill(root, 'mlspace-my-own-workflow', 'my workflow')
    setup.install_clients(*args)
    setup.install_clients(*args)
    verify_client('opencode', '/usr/bin/true', args[1], args[2])
    assert custom.read_text() == 'my workflow'


def test_all_conflicts_reported_together_without_writes(profile):
    root, args = profile
    first = write_skill(root, 'mlspace-list-running-jobs', 'custom one')
    second = write_skill(root, 'mlspace-diagnose-job-launch-rejection', 'custom two')
    config = root / 'opencode.jsonc'
    config.write_text('{"mcp":{"mlspace":{"command":["old-wrapper"]}}}')
    original = config.read_bytes()
    with pytest.raises(ValueError) as error:
        setup.install_clients(*args)
    assert all(str(p) in str(error.value) for p in [config, first, second])
    assert 'local changes' not in str(error.value)  # unknown provenance is not proof of edits
    assert config.read_bytes() == original
    assert first.read_text() == 'custom one'
    assert not setup.install_state_file().exists()


def test_explicit_replacement_backs_up_full_skill_and_config_preserving_extras(profile):
    root, args = profile
    custom = write_skill(root, 'mlspace-my-own-workflow', 'user-owned')
    skill = write_skill(root, 'mlspace-list-running-jobs', 'edited packaged skill')
    helper = skill.parent / 'scripts' / 'helper.py'
    helper.parent.mkdir()
    helper.write_text('user helper')
    config = root / 'opencode.jsonc'
    original = '{ // keep comment\n "mcp":{"mlspace":{"command":["old-wrapper"]},"other":{"command":["other"]}}}'
    config.write_text(original)
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    resolutions = {str(c.path): ('replace', c.fingerprint) for c in plan['conflicts']}
    setup.install_clients(*args, resolutions=resolutions)
    backups = list((setup.install_state_file().parent / 'backups').iterdir())
    assert len(backups) == 1
    backup = backups[0]
    assert (backup / 'opencode.jsonc').read_text() == original
    assert (backup / 'skills' / skill.parent.name / 'SKILL.md').read_text() == 'edited packaged skill'
    assert (backup / 'skills' / skill.parent.name / 'scripts/helper.py').read_text() == 'user helper'
    assert backup.stat().st_mode & 0o777 == 0o700
    assert not backup.is_relative_to(root / 'skills')
    assert custom.read_text() == 'user-owned'
    assert helper.read_text() == 'user helper'
    assert skill.read_text() != 'edited packaged skill'
    assert '// keep comment' in config.read_text()
    assert setup._read_config(config)['mcp']['other'] == {'command': ['other']}
    verify_client('opencode', '/usr/bin/true', args[1], args[2])


def test_keep_user_override_survives_updates_and_verifies_truthfully(profile, capsys):
    root, args = profile
    skill = write_skill(root, 'mlspace-list-running-jobs', 'my modified workflow')
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    resolutions = {str(c.path): ('keep', c.fingerprint) for c in plan['conflicts']}
    setup.install_clients(*args, resolutions=resolutions)
    state = json.loads(setup.install_state_file().read_text())
    assert str(skill) not in state.get('skills', {})
    skill.write_text('further user edits')
    setup.install_clients(*args)
    verify_client('opencode', '/usr/bin/true', args[1], args[2])
    assert skill.read_text() == 'further user edits'
    assert 'user' in capsys.readouterr().err.lower()


def test_file_changed_after_conflict_choice_is_not_overwritten(profile):
    root, args = profile
    skill = write_skill(root, 'mlspace-list-running-jobs', 'first edit')
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    resolutions = {str(c.path): ('replace', c.fingerprint) for c in plan['conflicts']}
    skill.write_text('new edit while user entered keys')
    with pytest.raises(ValueError, match='изменил'):
        setup.install_clients(*args, resolutions=resolutions)
    assert skill.read_text() == 'new edit while user entered keys'
    assert not (root / 'opencode.json').exists()


def test_backup_failure_prevents_replacement(profile, monkeypatch):
    root, args = profile
    skill = write_skill(root, 'mlspace-list-running-jobs', 'precious edit')
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    resolutions = {str(c.path): ('replace', c.fingerprint) for c in plan['conflicts']}
    def denied(*a, **kw):
        raise PermissionError('backup denied')
    monkeypatch.setattr('shutil.copytree', denied)
    with pytest.raises(ValueError, match='Не удалось создать резервную копию'):
        setup.install_clients(*args, resolutions=resolutions)
    assert skill.read_text() == 'precious edit'
    assert not (root / 'opencode.json').exists()


@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_custom_extras_do_not_hide_broken_packaged_skills(profile, damage):
    root, args = profile
    setup.install_clients(*args)
    write_skill(root, 'mlspace-my-own-workflow', 'extra workflow')
    required = root / 'skills/mlspace-list-running-jobs/SKILL.md'
    if damage == 'missing':
        required.unlink()
    else:
        required.write_text('unexpected edit')
    with pytest.raises(ValueError, match='skills'):
        verify_client('opencode', '/usr/bin/true', args[1], args[2])


def test_support_file_changed_after_choice_prevents_replacement(profile):
    root, args = profile
    skill = write_skill(root, 'mlspace-list-running-jobs', 'custom workflow')
    helper = skill.parent / 'helper.py'
    helper.write_text('before')
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    choices = {str(c.path): ('replace', c.fingerprint) for c in plan['conflicts']}
    helper.write_text('after')
    with pytest.raises(ValueError, match='изменил'):
        setup.install_clients(*args, resolutions=choices)
    assert helper.read_text() == 'after'
    assert skill.read_text() == 'custom workflow'


def test_backup_does_not_follow_support_symlinks(profile):
    root, args = profile
    skill = write_skill(root, 'mlspace-list-running-jobs', 'custom workflow')
    link = skill.parent / 'reference'
    link.symlink_to('/nonexistent-external-file')
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    choices = {str(c.path): ('replace', c.fingerprint) for c in plan['conflicts']}
    setup.install_clients(*args, resolutions=choices)
    backup = next((setup.install_state_file().parent / 'backups').iterdir())
    saved_link = backup / 'skills' / skill.parent.name / 'reference'
    assert saved_link.is_symlink() and str(saved_link.readlink()) == '/nonexistent-external-file'
    assert link.is_symlink()


def test_keep_choice_survives_a_release_matching_user_content(profile, monkeypatch):
    root, args = profile
    name = 'mlspace-list-running-jobs'
    skill = write_skill(root, name, 'user content')
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    choices = {str(c.path): ('keep', c.fingerprint) for c in plan['conflicts']}
    setup.install_clients(*args, resolutions=choices)
    monkeypatch.setattr(setup, '_skill_sources', lambda: {name: 'user content'})
    setup.install_clients(*args)
    monkeypatch.setattr(setup, '_skill_sources', lambda: {name: 'next release'})
    setup.install_clients(*args)
    assert skill.read_text() == 'user content'


@pytest.mark.parametrize('kind', ['skill', 'connection'])
def test_edit_after_backup_is_never_overwritten(profile, monkeypatch, kind):
    root, args = profile
    if kind == 'skill':
        target = write_skill(root, 'mlspace-list-running-jobs', 'approved content')
        new_text = 'edit after backup'
    else:
        root.mkdir(parents=True)
        target = root / 'opencode.jsonc'
        target.write_text('{"mcp":{"mlspace":{"command":["approved"]}}}')
        new_text = '{"mcp":{"mlspace":{"command":["edit-after-backup"]}}}'
    plan = setup.prepare_clients(*args, collect_conflicts=True)
    choices = {str(c.path): ('replace', c.fingerprint) for c in plan['conflicts']}
    original = setup.backup_replacements
    def backup_then_edit(*a, **kw):
        backup = original(*a, **kw)
        target.write_text(new_text)
        return backup
    monkeypatch.setattr(setup, 'backup_replacements', backup_then_edit)
    with pytest.raises(ValueError, match='изменил'):
        setup.install_clients(*args, resolutions=choices)
    assert target.read_text() == new_text
