"""Bounded, shareable diagnostic events. Never attach raw logs or exception text.

This deliberately does not subscribe to the root logger: httpx, MCP and client
wrappers can log credentials or payloads. Only explicit, allowlisted metadata is
recorded. One file per process avoids cross-process rotation races.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import stat
import sys
import time
import traceback
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from . import __version__

_handler: RotatingFileHandler | None = None
_path: Path | None = None
_FIELDS = {'phase', 'client', 'command', 'reason', 'status', 'returncode', 'duration_ms', 'version',
           'method', 'route', 'request_id', 'domain', 'action', 'count', 'mode'}


class _PrivateHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                     0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise OSError('Unsafe diagnostic file')
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, 'a', encoding='utf-8')
        except BaseException:
            os.close(fd)
            raise

    def handleError(self, record):
        # Disk-full/rotation errors must not interrupt work or print log records.
        pass


def close() -> None:
    global _handler, _path
    try:
        if _handler is not None:
            _handler.close()
    except OSError:
        pass  # A full disk must not replace setup's result or an original error.
    finally:
        _handler, _path = None, None


def current_path() -> Path | None:
    return _path


def _open_handler(path: Path) -> RotatingFileHandler:
    return _PrivateHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')


def start(mode: str) -> Path | None:
    global _handler, _path
    close()
    try:
        # Stable location also when the native client's environment filters XDG.
        root = Path.home() / '.config/mlspace-plugin/logs'
        if root.is_symlink():
            raise OSError('Symlink log directory')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        path = root / f'{mode}-{stamp}-{os.getpid()}-{uuid4().hex[:8]}.log'
        _handler = _open_handler(path)
        _path = path
        _write({'event': 'session.start', 'version': __version__, 'mode': mode,
                'python': platform.python_version(), 'platform': sys.platform,
                'pid': os.getpid(), 'parent_pid': os.getppid()})
        _prune(root)
        print(f'Лог диагностики: {path}', file=sys.stderr)
        return path
    except OSError:
        close()
        print('Файл диагностики создать не удалось. Проверьте права и свободное место '
              'в ~/.config/mlspace-plugin/logs; работа продолжается без файла лога.', file=sys.stderr)
        return None


def _prune(root: Path) -> None:
    """Keep 20 completed sessions, never remove a still-running process's logs."""
    try:
        logs = sorted(root.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in logs[20:]:
            match = re.fullmatch(r'(?:setup|server|test)-\d{8}T\d{6}-(\d+)-[a-f0-9]{8}\.log', path.name)
            if not match or path.is_symlink():
                continue
            try:
                os.kill(int(match[1]), 0)
            except ProcessLookupError:
                for item in (path, Path(str(path) + '.1'), Path(str(path) + '.2')):
                    if not item.is_symlink():
                        item.unlink(missing_ok=True)
    except OSError:
        pass


def _write(data: dict[str, Any]) -> None:
    if _handler is None:
        return
    data = {'time': datetime.now(timezone.utc).isoformat(), **data}
    text = json.dumps(data, ensure_ascii=False)
    _handler.handle(logging.LogRecord('mlspace.diagnostics', logging.INFO, '', 0, text, (), None))


def event(name: str, **fields: Any) -> None:
    _write({'event': name, **{k: v for k, v in fields.items() if k in _FIELDS}})


def failure(phase: str, exc: BaseException, **fields: Any) -> None:
    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 10:
        seen.add(id(current))
        chain.append({'type': type(current).__name__, 'frames': [
            {'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
            for f in traceback.extract_tb(current.__traceback__)[-20:]]})
        current = current.__cause__ or current.__context__
    _write({'event': 'failure', 'phase': phase, 'error_chain': chain,
            **{k: v for k, v in fields.items() if k in _FIELDS}})


@lru_cache(maxsize=1)
def _routes() -> list[tuple[re.Pattern[str], str]]:
    spec = json.loads(files('mlspace_mcp').joinpath('spec/openapi.json').read_text())
    return [(re.compile('^' + re.sub(r'\\\{[^}]+\\\}', '[^/]+', re.escape(path)) + '$'), path)
            for path in spec['paths']]


async def request(http: Any, method: str, url: str, **kwargs: Any) -> Any:
    """Log every API attempt, including auth and transport failures, without values."""
    path = urlsplit(url).path
    route = next((template for pattern, template in _routes() if pattern.fullmatch(path)),
                 '<unrecognized route>')
    meta = {'method': method, 'route': route, 'request_id': uuid4().hex[:12]}
    event('http.start', **meta)
    started = time.monotonic()
    try:
        response = await http.request(method, url, **kwargs)
    except Exception as exc:
        failure('http', exc, **meta, duration_ms=round((time.monotonic() - started) * 1000),
                reason='tls' if 'CERTIFICATE_VERIFY_FAILED' in str(exc) else 'transport')
        raise
    event('http.end', **meta, status=response.status_code,
          duration_ms=round((time.monotonic() - started) * 1000))
    return response
