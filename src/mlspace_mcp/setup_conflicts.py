"""Explicit choices and recoverable backups for existing OpenCode files."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .setup_ui import display

Resolutions = dict[str, tuple[str, str]]


def fingerprint(path: Path) -> str:
    """Include supporting skill files; never follow symlinks while inspecting them."""
    rows = []
    paths = [path, *sorted(path.rglob('*'))] if path.is_dir() and not path.is_symlink() else [path]
    for item in paths:
        name = str(item.relative_to(path))
        if item.is_symlink():
            value = 'link:' + str(item.readlink())
        elif item.is_file():
            value = hashlib.sha256(item.read_bytes()).hexdigest()
        elif item.is_dir():
            value = 'directory'
        else:
            raise ValueError(f'Файл настройки отсутствует или имеет неподдерживаемый тип: {item}. Проверьте путь и повторите setup.')
        rows.append((name, value))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


@dataclass(frozen=True)
class Conflict:
    path: Path
    kind: str
    fingerprint: str

    @property
    def source(self) -> Path:
        return self.path.parent if self.kind == 'skill' else self.path

    def verify_unchanged(self) -> None:
        if fingerprint(self.source) != self.fingerprint:
            raise ValueError(f'Файл изменился после вашего выбора; запустите setup снова: {self.path}')

    @classmethod
    def inspect(cls, path: Path, kind: str) -> Conflict:
        source = path.parent if kind == 'skill' else path
        return cls(path, kind, fingerprint(source))


def conflict_message(conflicts: list[Conflict]) -> str:
    lines = ['Существующие файлы MLSpace отличаются от версии плагина; файлы ещё не перезаписаны:']
    lines.extend(f'  {item.kind}: {item.path}' for item in conflicts)
    lines.append('Запустите setup в терминале и выберите для каждого файла: сохранить пользовательский skill либо создать резервную копию и заменить его версией плагина. '
                 'Флаг --force не разрешает заменять эти файлы без отдельного выбора.')
    return '\n'.join(lines)


def choose_resolutions(conflicts: list[Conflict], *, non_interactive: bool) -> Resolutions:
    if not conflicts:
        return {}
    if non_interactive:
        raise ValueError(conflict_message(conflicts))
    for line in conflict_message(conflicts).splitlines():
        print(display(line), file=sys.stderr)
    print('Перед заменой резервная копия сохраняется вне каталога skills; путь будет показан в терминале. '
          'Другие подключения MCP и дополнительные пользовательские skills сохраняются.', file=sys.stderr)
    choices = {}
    for item in conflicts:
        print(display(str(item.path)), file=sys.stderr)
        prompt = ('[k] Сохранить мой skill (исключить из обновлений плагина), [r] Создать резервную копию и заменить версией плагина, '
                  '[Enter] Отменить настройку: ' if item.kind == 'skill' else
                  '[r] Создать резервную копию конфига и заменить только mcp.mlspace, [Enter] Отменить настройку: ')
        while True:
            choice = input(prompt).strip().lower()
            if not choice:
                raise ValueError('Настройка отменена; конфликтующие файлы не изменены.')
            if choice == 'r' or (choice == 'k' and item.kind == 'skill'):
                choices[str(item.path)] = ('keep' if choice == 'k' else 'replace', item.fingerprint)
                break
            print('Введите указанную букву: k или r для skill, r для конфига. Enter отменяет настройку.', file=sys.stderr)
    return choices


def backup_replacements(conflicts: list[Conflict], root: Path) -> Path | None:
    if not conflicts:
        return None
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix='setup-', dir=root))
    print(f'Каталог резервной копии: {display(str(backup))}', file=sys.stderr)
    for item in conflicts:
        item.verify_unchanged()
        destination = backup / 'skills' / item.path.parent.name if item.kind == 'skill' else backup / item.path.name
        if item.kind == 'skill':
            shutil.copytree(item.source, destination, symlinks=True)
        else:
            shutil.copy2(item.source, destination)
            destination.chmod(0o600)
        if fingerprint(destination) != item.fingerprint or fingerprint(item.source) != item.fingerprint:
            raise ValueError(f'Файл изменился при создании резервной копии; оригиналы не заменены: {item.path}. Повторите setup.')
    return backup
