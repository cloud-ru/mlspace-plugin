import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput


def test_search_preserves_selection_across_300_workspaces():
    from mlspace_mcp.setup_ui import choose_workspaces
    rows = [{'id': f'id-{i:03}', 'name': f'workspace-{i:03}', 'project_name': 'project'}
            for i in range(300)]
    with create_pipe_input() as pipe:
        pipe.send_text('workspace-299 \x15workspace-001 \r')
        result = choose_workspaces(rows, input=pipe, output=DummyOutput())
    assert {r['id'] for r in result} == {'id-299', 'id-001'}


def test_picker_never_renders_300_rows_and_keeps_duplicate_names_distinct():
    from mlspace_mcp.setup_ui import WorkspaceSelection
    state = WorkspaceSelection([{'id': str(i), 'name': 'same', 'project_name': 'p'}
                                for i in range(300)])
    assert len(state.lines()) <= 12
    state.toggle()
    state.cursor = 1
    state.toggle()
    assert state.selected == {'0', '1'}
    state.query = 'not-found'
    state.toggle()
    assert state.selected == {'0', '1'}


def test_picker_cancel():
    from mlspace_mcp.setup_ui import choose_workspaces
    with create_pipe_input() as pipe:
        pipe.send_text('\x03')
        with pytest.raises(KeyboardInterrupt):
            choose_workspaces([{'id': '1', 'name': 'a'}], input=pipe, output=DummyOutput())


def test_masked_prompt_preserves_secret_characters():
    from mlspace_mcp.setup_ui import masked_prompt
    with create_pipe_input() as pipe:
        pipe.send_text('  value=$foo # "quotes"  \r')
        assert masked_prompt('Key: ', input=pipe, output=DummyOutput()) == '  value=$foo # "quotes"  '


def test_real_terminal_masks_bracketed_paste():
    import os
    import select
    import subprocess
    import sys
    import time
    master, slave = os.openpty()
    code = ('from mlspace_mcp.setup_ui import masked_prompt; '
            'value=masked_prompt("Key: "); '
            'assert value == "dummy-secret-paste"; print("MASK_TEST_OK")')
    env = dict(os.environ, TERM='xterm', PROMPT_TOOLKIT_NO_CPR='1')
    process = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    transcript = b''
    sent = False
    deadline = time.monotonic() + 8
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                transcript += chunk
                if b'Key:' in transcript and not sent:
                    os.write(master, b'\x1b[200~dummy-secret-paste\x1b[201~\r')
                    sent = True
            if process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0
        assert b'MASK_TEST_OK' in transcript
        assert b'dummy-secret-paste' not in transcript
        assert b'***' in transcript
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
