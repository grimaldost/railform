"""The frame: railform's configuration as data, and the verb `railform frame`.

Development-rails spec, §2, invented here. The default frame (`default_frame.toml`, shipped in
this package) is applied first, then the preset of `project.type`, then the repository's
`railform.toml`; arrays replace and tables merge key by key, except the two guard lists,
`session_loaded` and `private_refs.placeholders`, where the repository's list is joined to the
default (default first, each element once), so they grow and never shrink. A value that departs
from the preset is legal only when an `exceptions` row names its key and its value with a
reason, and `railform frame --check` fails an unlisted departure. The keys in
`meta.parameter_keys` are per repository and never depart, nor do the two guard lists.

`commit.attribution_patterns` in the default frame is keel's set
(https://github.com/grimaldost/keel, `.pre-commit-config.yaml` at c3313fd), and its test cases
come from keel's `tests/test_commit_msg_lane.py` at the same commit.

Other verbs read the frame through `load(root)`.
"""

from __future__ import annotations

import copy
import functools
import json
import math
import os
import re
import shlex
import subprocess
import tomllib
import uuid
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse

VERB = 'frame'
CONFIG_NAME = 'railform.toml'
PROJECT_TYPES = ('python-package', 'python-scripts', 'plugin-marketplace')
GITHUB_OUTPUT_ENV = 'GITHUB_OUTPUT'

# The guard lists: the repository's list is joined to the default (default first, each element
# once), so a repository names only what it adds and can never drop a default element (the
# development-rails spec, §13, from the craft-collection pilot). A safety rule, not a
# preference, so it is not frame data a repository could relax.
EXTENDABLE_KEYS = ('session_loaded', 'private_refs.placeholders')

# Replaced in `hooks.wrapper` by the value of `hooks.pre_commit_version`.
WRAPPER_VERSION_FIELD = '{hooks.pre_commit_version}'

UNKNOWN_KEY = 'unknown-key'
WRONG_TYPE = 'wrong-type'
BAD_EXCEPTION = 'bad-exception'
UNLISTED_DEPARTURE = 'unlisted-departure'
# When several rules fail, the verdict names the first in this order.
CODE_ORDER = (UNKNOWN_KEY, WRONG_TYPE, BAD_EXCEPTION, UNLISTED_DEPARTURE)


class FrameError(Exception):
    """A usage or configuration error: no repository, or a `railform.toml` that cannot be read."""


@dataclass(frozen=True)
class Problem:
    """One rule a frame breaks: its failure code and a one-line message."""

    code: str
    message: str


class FrameSchemaError(Exception):
    """A configuration with unknown keys or values of the wrong type."""

    def __init__(self, problems: list[Problem]) -> None:
        super().__init__('; '.join(problem.message for problem in problems))
        self.problems = problems


# --- the schema --------------------------------------------------------------------------------


def _kind(value: object) -> str:
    if isinstance(value, bool):
        return 'a boolean'
    if isinstance(value, int):
        return 'an integer'
    if isinstance(value, float):
        return 'a float'
    if isinstance(value, str):
        return 'a string'
    if isinstance(value, list):
        return 'an array'
    if isinstance(value, dict):
        return 'a table'
    return f'a {type(value).__name__}'


def _join(path: str, key: str) -> str:
    part = key if re.fullmatch(r'[A-Za-z0-9_-]+', key) else json.dumps(key)
    return f'{path}.{part}' if path else part


class _Node:
    def check(self, value: object, path: str, complete: bool) -> list[Problem]:
        raise NotImplementedError


class _Anything(_Node):
    def check(self, value: object, path: str, complete: bool) -> list[Problem]:
        return []


@dataclass(frozen=True)
class _Str(_Node):
    choices: tuple[str, ...] = ()
    regex: bool = False

    def check(self, value: object, path: str, complete: bool) -> list[Problem]:
        if not isinstance(value, str):
            return [Problem(WRONG_TYPE, f'{path}: expected a string, got {_kind(value)}')]
        if self.choices and value not in self.choices:
            allowed = ', '.join(self.choices)
            return [Problem(WRONG_TYPE, f'{path}: {json.dumps(value)} is not one of {allowed}')]
        if self.regex:
            try:
                re.compile(value)
            except re.error as exc:
                return [Problem(WRONG_TYPE, f'{path}: not a regular expression ({exc})')]
        return []


class _Bool(_Node):
    def check(self, value: object, path: str, complete: bool) -> list[Problem]:
        if isinstance(value, bool):
            return []
        return [Problem(WRONG_TYPE, f'{path}: expected a boolean, got {_kind(value)}')]


class _Count(_Node):
    def check(self, value: object, path: str, complete: bool) -> list[Problem]:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return []
        return [Problem(WRONG_TYPE, f'{path}: expected a non-negative integer, got {_kind(value)}')]


@dataclass(frozen=True)
class _List(_Node):
    item: _Node

    def check(self, value: object, path: str, complete: bool) -> list[Problem]:
        if not isinstance(value, list):
            return [Problem(WRONG_TYPE, f'{path}: expected an array, got {_kind(value)}')]
        problems: list[Problem] = []
        for index, item in enumerate(value):
            problems += self.item.check(item, f'{path}[{index}]', complete)
        return problems


@dataclass(frozen=True)
class _Table(_Node):
    """A table with a closed set of keys.

    `require` None requires every key only when a complete frame is checked; True always
    (rows of a list); False never (exceptions rows, whose missing fields are bad exceptions).
    A key in `optional` may be missing even then; the effective frame fills in its default.
    """

    fields: dict[str, _Node]
    require: bool | None = None
    optional: tuple[str, ...] = ()

    def check(self, value: object, path: str, complete: bool) -> list[Problem]:
        if not isinstance(value, dict):
            return [Problem(WRONG_TYPE, f'{path}: expected a table, got {_kind(value)}')]
        problems: list[Problem] = []
        for key, item in value.items():
            node = self.fields.get(key)
            if node is None:
                problems.append(Problem(UNKNOWN_KEY, f'unknown key {_join(path, key)}'))
            else:
                problems += node.check(item, _join(path, key), complete)
        required = complete if self.require is None else self.require
        if required:
            for key in self.fields:
                if key not in value and key not in self.optional:
                    problems.append(Problem(WRONG_TYPE, f'{_join(path, key)}: missing'))
        return problems


_STRINGS = _List(_Str())

# A version site's `authored` defaults to false: an authored site is checked, never rewritten
# (the development-rails spec, §15).
SITE_DEFAULTS = {'authored': False}
# A tag row's `since` defaults to "": the row's floor is then `release.since` (the
# development-rails spec, §16).
TAG_DEFAULTS = {'since': ''}


def _rows(**fields: _Node) -> _List:
    """An array of rows that each carry every one of `fields`."""
    return _List(_Table(fields, require=True))


SCHEMA = _Table(
    {
        'session_loaded': _STRINGS,
        'exceptions': _List(
            _Table({'key': _Str(), 'value': _Anything(), 'reason': _Str()}, require=False)
        ),
        'meta': _Table({'parameter_keys': _STRINGS}),
        'project': _Table({'type': _Str(choices=PROJECT_TYPES), 'layout': _Str()}),
        'commit': _Table({'types': _STRINGS, 'attribution_patterns': _List(_Str(regex=True))}),
        'changelog': _Table(
            {
                'records': _rows(prefix=_Str(), file=_Str()),
                'contract_surfaces': _STRINGS,
                'trailer': _Str(),
                'consumer_affecting': _Str(choices=('advisory', 'error')),
                'enabled': _Bool(),
            }
        ),
        'hooks': _Table(
            {'dir': _Str(), 'names': _STRINGS, 'wrapper': _Str(), 'pre_commit_version': _Str()}
        ),
        'branch': _Table({'pattern': _Str(regex=True), 'allow': _STRINGS}),
        'private_refs': _Table({'names_file_env': _Str(), 'placeholders': _STRINGS}),
        'ci': _Table(
            {
                'os': _STRINGS,
                'setup': _Table({'fetch_depth': _Count(), 'extra_installs': _STRINGS}),
                'ruff_version': _Str(),
                'extra_workflows': _STRINGS,
                'self_hosted': _Bool(),
            }
        ),
        'python': _Table(
            {
                'versions': _STRINGS,
                'newest': _Str(),
                'package': _Str(),
                'sync_args': _STRINGS,
                'test_command': _Str(),
                'lint_paths': _STRINGS,
                'type_check': _Str(),
            }
        ),
        'version': _Table(
            {
                'sites': _List(
                    _Table(
                        {'path': _Str(), 'pattern': _Str(regex=True), 'authored': _Bool()},
                        require=True,
                        optional=tuple(SITE_DEFAULTS),
                    )
                )
            }
        ),
        'release': _Table(
            {
                'metadata_paths': _STRINGS,
                'since': _Str(),
                'tag_prefix': _List(
                    _Table(
                        {'prefix': _Str(), 'tag': _Str(), 'since': _Str()},
                        require=True,
                        optional=tuple(TAG_DEFAULTS),
                    )
                ),
                'enabled': _Bool(),
            }
        ),
    }
)


def validate(config: object, *, complete: bool = False) -> list[Problem]:
    """Return the unknown keys and wrong types in `config`.

    With `complete`, every key must also be present (an effective frame); a `railform.toml`
    sets only what it changes.
    """
    return SCHEMA.check(config, '', complete)


def leaf_keys() -> list[str]:
    """The dotted keys a departure is judged on: every non-table value except `exceptions`."""
    keys: list[str] = []

    def walk(table: _Table, path: str) -> None:
        for key, node in table.fields.items():
            dotted = _join(path, key)
            if isinstance(node, _Table):
                walk(node, dotted)
            elif dotted != 'exceptions':
                keys.append(dotted)

    walk(SCHEMA, '')
    return keys


# --- loading -----------------------------------------------------------------------------------


def default_frame_text() -> str:
    """The text of the default frame shipped in this package."""
    return resources.files('railform').joinpath('default_frame.toml').read_text(encoding='utf-8')


def _defaults() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The default frame's base tables and its presets: a fresh copy each call."""
    return copy.deepcopy(_parsed_defaults(default_frame_text()))


@functools.cache
def _parsed_defaults(text: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Parse and check the default frame once per text; callers take deep copies."""
    data = tomllib.loads(text)
    presets = data.pop('presets', {})
    problems = validate(data, complete=True)
    if set(presets) != set(PROJECT_TYPES):
        problems.append(Problem(WRONG_TYPE, f'presets: expected {", ".join(PROJECT_TYPES)}'))
    for name, preset in presets.items():
        problems += [Problem(p.code, f'presets.{name}: {p.message}') for p in validate(preset)]
        if 'type' in preset.get('project', {}):
            problems.append(Problem(WRONG_TYPE, f'presets.{name}: sets project.type'))
    if problems:
        raise RuntimeError(
            'the default frame is invalid: ' + '; '.join(problem.message for problem in problems)
        )
    return data, presets


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Tables merge key by key; every other value, arrays included, replaces."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _get(table: dict[str, Any], dotted: str) -> Any:
    value: Any = table
    for part in dotted.split('.'):
        value = value[part]
    return value


def _project_type(config: dict[str, Any]) -> str:
    project = config.get('project', {})
    return project.get('type', _defaults()[0]['project']['type'])


def preset_frame(project_type: str) -> dict[str, Any]:
    """The default frame with the preset of `project_type` applied, before any `railform.toml`."""
    base, presets = _defaults()
    frame = _merge(base, presets[project_type])
    frame['project'] = {**frame['project'], 'type': project_type}
    return frame


def _union(default: list[Any], own: list[Any]) -> list[Any]:
    """The default's elements, then the repository's own that the default lacks, each once."""
    joined: list[Any] = []
    for item in [*default, *own]:
        if not any(_same(item, seen) for seen in joined):
            joined.append(item)
    return joined


def _unrendered(config: dict[str, Any]) -> dict[str, Any]:
    problems = validate(config)
    if problems:
        raise FrameSchemaError(problems)
    preset = preset_frame(_project_type(config))
    frame = _merge(preset, config)
    for key in EXTENDABLE_KEYS:
        *tables, leaf = key.split('.')
        target, default = frame, preset
        for table in tables:
            target[table] = dict(target[table])
            target, default = target[table], default[table]
        target[leaf] = _union(default[leaf], target[leaf])
    return frame


def _render(frame: dict[str, Any]) -> dict[str, Any]:
    """Fill in what the effective frame derives: the wrapper's version and the defaults of the
    rows (a version site's `authored`, a tag row's `since`)."""
    hooks = frame['hooks']
    wrapper = hooks['wrapper'].replace(WRAPPER_VERSION_FIELD, hooks['pre_commit_version'])
    sites = [{**SITE_DEFAULTS, **row} for row in frame['version']['sites']]
    tags = [{**TAG_DEFAULTS, **row} for row in frame['release']['tag_prefix']]
    return {
        **frame,
        'hooks': {**hooks, 'wrapper': wrapper},
        'version': {**frame['version'], 'sites': sites},
        'release': {**frame['release'], 'tag_prefix': tags},
    }


def effective_frame(config: dict[str, Any] | None) -> dict[str, Any]:
    """The effective frame for a parsed `railform.toml` (None when there is none).

    Raises FrameSchemaError when `config` has unknown keys or values of the wrong type.
    """
    return _render(_unrendered(config or {}))


def parse_config(text: str | bytes, source: str = CONFIG_NAME) -> dict[str, Any]:
    """Parse the text of a `railform.toml`; FrameError when it is not TOML."""
    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise FrameError(f'{source}: not UTF-8 ({exc})') from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FrameError(f'{source}: cannot parse: {exc}') from exc


def read_config(root: Path) -> dict[str, Any] | None:
    """The parsed `railform.toml` at `root`, or None when the repository has none."""
    path = root / CONFIG_NAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FrameError(f'{CONFIG_NAME}: cannot read: {exc}') from exc
    return parse_config(data)


def load(root: Path) -> dict[str, Any]:
    """The effective frame of the repository at `root`.

    Raises FrameError when `railform.toml` cannot be read or parsed, and FrameSchemaError when it
    breaks the schema; a verb reports either as a configuration error.
    """
    return effective_frame(read_config(root))


def repository_root() -> Path:
    """The top level of the git repository that contains the current directory."""
    try:
        done = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='surrogateescape',
            check=False,
        )
    except OSError as exc:
        raise FrameError(f'cannot run git: {exc}') from exc
    if done.returncode != 0 or not done.stdout.strip():
        raise FrameError('not inside a git repository')
    return Path(done.stdout.strip())


# --- the check ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Departure:
    """A preset-governed key whose value differs from its preset's."""

    key: str
    preset: Any
    value: Any


def _same(a: object, b: object) -> bool:
    """Equal values of the same TOML type (so `1` never matches `true`)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[key], b[key]) for key in a)
    return a == b


def _is_parameter(key: str, parameter_keys: list[str]) -> bool:
    return any(key == p or key.startswith(p + '.') for p in parameter_keys)


def departures(config: dict[str, Any]) -> list[Departure]:
    """The departures of a schema-valid `config` from the preset of its project type.

    Judged before `hooks.wrapper` is rendered, so moving `hooks.pre_commit_version` departs on
    that key alone.
    """
    preset = preset_frame(_project_type(config))
    frame = _unrendered(config)
    parameter_keys = preset['meta']['parameter_keys']
    found: list[Departure] = []
    for key in leaf_keys():
        # A guard list is the default joined with the repository's own, so it never departs.
        if _is_parameter(key, parameter_keys) or key in EXTENDABLE_KEYS:
            continue
        expected, value = _get(preset, key), _get(frame, key)
        if not _same(expected, value):
            found.append(Departure(key, expected, value))
    return found


def _show(value: object) -> str:
    return json.dumps(value, ensure_ascii=True)


def check(config: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Validate a parsed `railform.toml`: the verdict code (`ok` or the first rule that failed)
    and the findings, each prefixed with the file name."""
    config = config or {}
    problems = validate(config)
    if not problems:
        problems = _exception_problems(config)
    if not problems:
        return 'ok', []
    problems.sort(key=lambda problem: CODE_ORDER.index(problem.code))
    return problems[0].code, [f'{CONFIG_NAME}: {problem.message}' for problem in problems]


def _exception_problems(config: dict[str, Any]) -> list[Problem]:
    found = departures(config)
    project_type = _project_type(config)
    problems: list[Problem] = []
    covering: list[dict[str, Any]] = []
    for index, row in enumerate(config.get('exceptions', [])):
        where = f'exceptions[{index}]'
        missing = [field for field in ('key', 'value', 'reason') if field not in row]
        if missing:
            problems.append(Problem(BAD_EXCEPTION, f'{where}: {", ".join(missing)} is required'))
            continue
        where = f'{where} ({row["key"]})'
        if not row['reason'].strip():
            problems.append(
                Problem(BAD_EXCEPTION, f'{where}: reason is required and must not be empty')
            )
            continue
        if not any(d.key == row['key'] and _same(d.value, row['value']) for d in found):
            problems.append(
                Problem(
                    BAD_EXCEPTION,
                    f'{where}: matches no departure (no key departs from the {project_type} '
                    f'preset with the value {_show(row["value"])})',
                )
            )
            continue
        if any(earlier['key'] == row['key'] for earlier in covering):
            problems.append(
                Problem(
                    BAD_EXCEPTION,
                    f'{where}: duplicates an earlier row that already covers this departure',
                )
            )
            continue
        covering.append(row)
    for departure in found:
        if not any(
            row['key'] == departure.key and _same(row['value'], departure.value) for row in covering
        ):
            problems.append(
                Problem(
                    UNLISTED_DEPARTURE,
                    f'{departure.key} departs from the {project_type} preset '
                    f'(preset {_show(departure.preset)}, here {_show(departure.value)}) '
                    'and no exceptions row lists it',
                )
            )
    return problems


# --- output ------------------------------------------------------------------------------------


def _toml_string(text: str) -> str:
    out = ['"']
    for char in text:
        code = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == '\\':
            out.append('\\\\')
        elif char == '\n':
            out.append('\\n')
        elif char == '\t':
            out.append('\\t')
        elif code < 0x20 or code >= 0x7F:
            out.append(f'\\u{code:04X}' if code <= 0xFFFF else f'\\U{code:08X}')
        else:
            out.append(char)
    out.append('"')
    return ''.join(out)


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r'[A-Za-z0-9_-]+', key) else _toml_string(key)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return 'nan'
        if math.isinf(value):
            return 'inf' if value > 0 else '-inf'
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return '[' + ', '.join(_toml_value(item) for item in value) + ']'
    if isinstance(value, dict):
        if not value:
            return '{}'
        pairs = ', '.join(f'{_toml_key(k)} = {_toml_value(v)}' for k, v in value.items())
        return '{ ' + pairs + ' }'
    isoformat = getattr(value, 'isoformat', None)
    if callable(isoformat):
        return str(isoformat())
    raise TypeError(f'no TOML form for {type(value).__name__}')


def dumps_toml(table: dict[str, Any]) -> str:
    """TOML text for a table: plain values first, then one header per sub-table; ASCII only."""
    lines: list[str] = []

    def emit(current: dict[str, Any], path: tuple[str, ...]) -> None:
        plain = [(k, v) for k, v in current.items() if not isinstance(v, dict)]
        tables = [(k, v) for k, v in current.items() if isinstance(v, dict)]
        if path and (plain or not tables):
            if lines:
                lines.append('')
            lines.append('[' + '.'.join(_toml_key(part) for part in path) + ']')
        lines.extend(f'{_toml_key(k)} = {_toml_value(v)}' for k, v in plain)
        for key, sub in tables:
            emit(sub, (*path, key))

    emit(table, ())
    return '\n'.join(lines) + '\n'


def pipeline(frame: dict[str, Any]) -> list[str]:
    """The type's pipeline: the commands the `checks` job of the reusable workflow runs, in the
    type's fixed order, built from the frame's typed parameters (spec §9 and design decision 3).

    Each element is one bash command line. The workflow runs the lines in order in one bash
    shell, so what a line exports, the directory it enters and the variables it sets hold for
    every line after it (development-rails spec, §13): a test command may install a tool and
    `export PATH=...` on one line and use the tool on the next. The Python version is not in
    them: the workflow sets `UV_PYTHON` for each matrix leg, and uv reads it. A
    `python.test_command` of several lines gives one command per non-blank line.

    - `python-package` and `python-scripts`: the lock check, the sync (with `python.sync_args`),
      `uv pip install` of `ci.setup.extra_installs` when there are any, the ruff format check
      and lint over `python.lint_paths`, `ty` over `python.type_check` when it is set, then the
      test command.
    - `plugin-marketplace` (no uv project): ruff from uvx at `ci.ruff_version`, format check and
      lint, then the test command.

    Raises FrameError when a step cannot be written: ruff at no version, or extra installs for a
    type that has no environment to install them into.
    """
    python = frame['python']
    lint = shlex.join(python['lint_paths'])
    installs = frame['ci']['setup']['extra_installs']
    steps: list[str] = []
    if frame['project']['type'] == 'plugin-marketplace':
        version = frame['ci']['ruff_version']
        if not version:
            raise FrameError(
                'ci.ruff_version is empty: the plugin-marketplace pipeline runs ruff from uvx '
                'at a pinned version'
            )
        if installs:
            raise FrameError(
                'ci.setup.extra_installs is set, but a plugin-marketplace repository has no uv '
                'project to install into; add `--with` to python.test_command instead'
            )
        ruff = shlex.join(['uvx', f'ruff=={version}'])
        steps += [f'{ruff} format --check {lint}', f'{ruff} check {lint}']
    else:
        steps += ['uv lock --check', shlex.join(['uv', 'sync', '--frozen', *python['sync_args']])]
        if installs:
            steps.append(shlex.join(['uv', 'pip', 'install', *installs]))
        steps += [
            f'uv run --frozen ruff format --check {lint}',
            f'uv run --frozen ruff check {lint}',
        ]
        if python['type_check']:
            steps.append(shlex.join(['uv', 'run', '--frozen', 'ty', 'check', python['type_check']]))
    steps += [line for line in python['test_command'].splitlines() if line.strip()]
    return [step.rstrip() for step in steps]


# The output that carries the pipeline, one command per line.
PIPELINE_OUTPUT = 'pipeline'
# The output that names the Python the `hooks` job runs pre-commit on.
HOOKS_PYTHON_OUTPUT = 'hooks_python'


def _version_key(version: str) -> tuple[int, ...]:
    """A version such as `3.13` as numbers, so `3.10` sorts after `3.9`; a part that is not a
    number sorts first."""
    return tuple(int(part) if part.isdigit() else -1 for part in version.split('.'))


def hooks_python(frame: dict[str, Any]) -> str:
    """The Python the `hooks` job runs pre-commit on: the newest of `python.versions`, or
    `python.newest` when the list is empty (development-rails spec, §13, the craft-collection
    pilot). A `default_language_version` that names the repository's newest Python then resolves
    to the interpreter pre-commit runs on."""
    versions = frame['python']['versions']
    if not versions:
        return frame['python']['newest']
    return max(versions, key=_version_key)


def github_outputs(frame: dict[str, Any]) -> list[tuple[str, str]]:
    """One output per key of spec §2: `<table>_<key>`, or the key alone at the top level; then
    `pipeline`, the type's pipeline from `pipeline(frame)`, one command per line; then
    `hooks_python`, the Python the `hooks` job runs pre-commit on (`hooks_python(frame)`).

    A string is written as it is; every other value (a list, a table, a boolean, a number) as
    compact JSON, which a workflow reads with `fromJSON`.

    Raises FrameError when the pipeline cannot be written.
    """
    outputs: list[tuple[str, str]] = []

    def text(value: object) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=True, separators=(',', ':'))

    for key, value in frame.items():
        if isinstance(value, dict):
            outputs += [(f'{key}_{sub}', text(inner)) for sub, inner in value.items()]
        else:
            outputs.append((key, text(value)))
    outputs.append((PIPELINE_OUTPUT, '\n'.join(pipeline(frame))))
    outputs.append((HOOKS_PYTHON_OUTPUT, hooks_python(frame)))
    return outputs


def _output_lines(name: str, value: str) -> str:
    if '\n' not in value and '\r' not in value:
        return f'{name}={value}\n'
    delimiter = f'railform_{uuid.uuid4().hex}'
    return f'{name}<<{delimiter}\n{value}\n{delimiter}\n'


# --- the verb ----------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Print the effective frame (the default frame, then the preset of project.type, then '
        'railform.toml) as TOML, or check railform.toml.'
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        '--check',
        action='store_true',
        help='fail on unknown keys, wrong types, bad exceptions rows and unlisted departures',
    )
    mode.add_argument('--json', action='store_true', help='print the effective frame as JSON')
    mode.add_argument(
        '--github-output',
        action='store_true',
        help='append the frame values as name=value lines to the file $GITHUB_OUTPUT names',
    )


def run(args: argparse.Namespace) -> Verdict:
    """Print, check or export the effective frame.

    The printing modes (`frame`, `--json`, `--github-output`) report an unknown key or a wrong
    type as `--check` does, exit 1: judging `railform.toml` is this verb's own job, so a schema
    error is a violation it found. Another verb that loads the frame (`load`) gets a
    FrameSchemaError instead and exits 2, because for that verb a broken `railform.toml` is a
    configuration error that stops it before its own check runs. Departures and exceptions
    rows are judged only by `--check`.
    """
    target = os.environ.get(GITHUB_OUTPUT_ENV, '')
    if args.github_output and not target:
        return Verdict.usage(VERB, f'{VERB}: ${GITHUB_OUTPUT_ENV} is not set')
    try:
        config = read_config(repository_root())
    except FrameError as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    if args.check:
        code, findings = check(config)
        if code == 'ok':
            return Verdict.ok(VERB)
        return Verdict.fail(VERB, code, tuple(findings))
    try:
        frame = effective_frame(config)
    except FrameSchemaError:
        code, findings = check(config)
        return Verdict.fail(VERB, code, tuple(findings))
    if args.github_output:
        try:
            outputs = github_outputs(frame)
        except FrameError as exc:
            return Verdict.usage(VERB, f'{VERB}: {exc}')
        lines = ''.join(_output_lines(name, value) for name, value in outputs)
        try:
            with open(target, 'a', encoding='utf-8', newline='\n') as output:
                output.write(lines)
        except OSError as exc:
            return Verdict.usage(VERB, f'{VERB}: cannot append to ${GITHUB_OUTPUT_ENV}: {exc}')
        return Verdict.ok(VERB)
    if args.json:
        print(json.dumps(frame, ensure_ascii=True))
    else:
        print(dumps_toml(frame), end='')
    return Verdict.ok(VERB)
