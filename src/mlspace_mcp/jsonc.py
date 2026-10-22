"""Small JSONC editor: validate fully, replace only the requested object's member."""
from __future__ import annotations

import json
import re
from typing import Any

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*[\s\S]*?\*/|\s+|[^\s"{}\[\],:]+|[{}\[\],:]')


def _tokens(text: str) -> list[re.Match[str]]:
    tokens = list(_TOKEN.finditer(text))
    if ''.join(t.group() for t in tokens) != text:
        raise ValueError('Invalid JSONC')
    return [t for t in tokens if not t.group().isspace() and not t.group().startswith(('//', '/*'))]


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate configuration key; resolve it before setup.')
        result[key] = value
    return result


def loads(text: str) -> dict[str, Any]:
    tokens = _tokens(text)
    cleaned = ''.join(t.group() for i, t in enumerate(tokens)
                      if not (t.group() == ',' and i + 1 < len(tokens)
                              and tokens[i+1].group() in ('}', ']')))
    value = json.loads(cleaned, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError('Configuration must be an object.')
    return value


def _members(text: str) -> tuple[dict[str, tuple[int, int]], int, bool]:
    tokens = _tokens(text)
    members = {}
    i = 1
    while i < len(tokens) and tokens[i].group() != '}':
        key = json.loads(tokens[i].group())
        i += 2  # key and colon
        start = tokens[i].start()
        depth = 0
        while i < len(tokens):
            token = tokens[i].group()
            if token in ('{', '['):
                depth += 1
            elif token in ('}', ']'):
                if depth == 0:
                    break
                depth -= 1
            elif token == ',' and depth == 0:
                break
            end = tokens[i].end()
            i += 1
        members[key] = (start, end)
        if tokens[i].group() == ',':
            i += 1
    return members, tokens[i].start(), tokens[i-1].group() == ','


def set_member(text: str, keys: list[str], value: Any) -> str:
    data = loads(text)  # validates nesting, duplicate keys, syntax before any modification
    key = keys[0]
    members, closing, trailing_comma = _members(text)
    if key in members:
        start, end = members[key]
        if len(keys) > 1:
            replacement = set_member(text[start:end], keys[1:], value)
        else:
            if data[key] == value:
                return text
            replacement = json.dumps(value, ensure_ascii=False, indent=2)
        return text[:start] + replacement + text[end:]
    nested = value
    for part in reversed(keys[1:]):
        nested = {part: nested}
    prefix = ',' if members and not trailing_comma else ''
    # Insert comma immediately after the preceding value, before any // comment.
    if prefix:
        last_end = list(members.values())[-1][1]
        text = text[:last_end] + ',' + text[last_end:]
        closing += 1
    insertion = '\n  ' + json.dumps(key) + ': ' + json.dumps(nested, ensure_ascii=False, indent=2) + '\n'
    result = text[:closing] + insertion + text[closing:]
    loads(result)
    return result
