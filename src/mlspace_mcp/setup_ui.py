"""Bounded terminal setup UI, also usable over SSH without a browser."""
from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from typing import Any

from prompt_toolkit import Application, PromptSession
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.widgets import TextArea


def display(value: str) -> str:
    """API-provided labels must not control the user's terminal."""
    return ''.join(char if char.isprintable() else ' ' for char in value)


def masked_prompt(label: str, **kwargs: Any) -> str:
    return str(PromptSession(is_password=True,
                      output=kwargs.pop('output', None) or create_output(stdout=sys.stderr),
                      **kwargs).prompt(label))


@contextmanager
def hidden_input() -> Iterator[None]:
    """Keep type-ahead hidden between credential prompts, restoring the TTY on exit.

    PromptSession restores its caller's terminal mode after each prompt. Keep an
    outer raw mode across validation/retries and both credentials so it restores
    to a non-echoing mode, not the shell's echoing mode. Nested scopes are safe.
    """
    if not sys.stdin.isatty():
        yield
        return
    with closing(create_input()) as terminal, terminal.raw_mode():
        yield


@dataclass
class WorkspaceSelection:
    rows: list[dict[str, str]]
    selected: set[str] = field(default_factory=set)
    query: str = ''
    cursor: int = 0

    def matches(self) -> list[dict[str, str]]:
        terms = self.query.casefold().split()
        return [row for row in self.rows if all(term in ' '.join(
            row.get(k, '') for k in ('id', 'name', 'project_name')).casefold() for term in terms)]

    def toggle(self) -> None:
        rows = self.matches()
        if rows:
            key = rows[self.cursor % len(rows)]['id']
            self.selected.symmetric_difference_update({key})

    def lines(self) -> list[str]:
        rows = self.matches()
        if not rows:
            return ['Совпадений нет. Измените поиск или очистите его (Ctrl-U); отмеченные строки остаются выбранными.']
        self.cursor %= len(rows)
        start = self.cursor // 12 * 12
        return [display(f"{'>' if start+i == self.cursor else ' '} "
                        f"[{'x' if r['id'] in self.selected else ' '}] {r['name']} "
                        f"| {r.get('project_name', '')} | {r['id']}")
                for i, r in enumerate(rows[start:start+12])]


def choose_workspaces(rows: list[dict[str, str]], selected_ids: list[str] | None = None,
                      **kwargs: Any) -> list[dict[str, str]]:
    state = WorkspaceSelection(rows, set(selected_ids or []) & {r['id'] for r in rows})
    search = TextArea(height=1, prompt='Поиск по названию / проекту / ID: ', multiline=False)
    bindings = KeyBindings()

    def changed(_buffer: Any) -> None:
        state.query = search.text
        state.cursor = 0

    search.buffer.on_text_changed += changed

    @bindings.add('up')
    def up(event: Any) -> None:
        state.cursor = (state.cursor - 1) % max(1, len(state.matches()))

    @bindings.add('down')
    def down(event: Any) -> None:
        state.cursor = (state.cursor + 1) % max(1, len(state.matches()))

    @bindings.add(' ')
    def toggle(event: Any) -> None:
        state.toggle()

    @bindings.add('enter')
    def done(event: Any) -> None:
        if state.selected:
            event.app.exit(result=[row for row in rows if row['id'] in state.selected])

    @bindings.add('c-c')
    @bindings.add('c-d')
    def cancel(event: Any) -> None:
        event.app.exit(exception=KeyboardInterrupt())

    app: Application[list[dict[str, str]]] = Application(
        layout=Layout(HSplit([
            search,
            Window(FormattedTextControl(lambda: '\n'.join(state.lines())), height=12),
            Window(FormattedTextControl(lambda: (
                f'Найдено: {len(state.matches())} из {len(rows)}; выбрано: {len(state.selected)}.\n'
                '↑/↓ курсор | Space выбор | Ctrl-U сброс | Enter готово (≥1) | Ctrl-C отмена'
            )), height=2),
        ]), focused_element=search),
        key_bindings=bindings, full_screen=False, erase_when_done=True,
        output=kwargs.pop('output', None) or create_output(stdout=sys.stderr), **kwargs,
    )
    return app.run()
