"""The verb `railform session-paths`: the D1 guard of an adoption series.

Development-rails spec, §6, invented here (no public source). A change fails when it touches a
path that a Claude Code session loads (skills, agents, commands, plugin hooks, instruction
files). The globs are the `session_loaded` globs of the frame at `--frame-from`, normally the
base, so the change under review cannot narrow its own guard by editing `railform.toml`. A
`--glob` adds a pattern to them (a series brief adds its own) and never removes one.

The change is the file list of `git diff --no-renames --name-only BASE...HEAD`: without rename
detection, a file moved out of a loaded path still shows up under its old name.

A glob matches a path when it matches the whole repository-relative path or any suffix of it
that starts at a `/` boundary. `**` spans any number of path segments (none included), `*` spans
none or more characters inside one segment, and every other character compares exactly, except
that case is ignored (casefolded), because the filesystems sessions run on are often
case-insensitive. So `**/hooks/**` matches `plugins/x/hooks/hooks.json` and not
`.githooks/pre-commit`. A malformed glob (an empty segment, a `.` or `..` segment, a leading or
trailing `/`, or a backslash) would match nothing and switch the guard off unseen, so it is a
configuration error (exit 2), from the frame or from `--glob`.

The check also fails (`guard-narrowed`) when `railform.toml` at HEAD removes a glob that the
frame at `--frame-from` had, drops one with an `exceptions` row (a default glob leaves the list
only that way), or gives `session_loaded` an `exceptions` row with a value that leaves one out.
Since the repository's list is joined to the default, a list that leaves out a default glob
keeps it. Once a drop is in the frame at `--frame-from`, the glob no longer guards.
"""

from __future__ import annotations

import functools
import re
import subprocess
from typing import TYPE_CHECKING, Any

from railform import frame
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

VERB = 'session-paths'
SESSION_LOADED = 'session_loaded'
SESSION_LOADED_CODE = 'session-loaded'
GUARD_NARROWED_CODE = 'guard-narrowed'
RANGE_SEPARATOR = '...'


class _UsageError(Exception):
    """A bad argument or an unreadable repository, reported as exit 2."""


# --- glob matching -----------------------------------------------------------------------------


@functools.cache
def _segment_pattern(segment: str) -> re.Pattern[str]:
    parts = (re.escape(piece) for piece in re.split(r'\*+', segment.casefold()))
    return re.compile('[^/]*'.join(parts))


def _match_from(glob: tuple[str, ...], path: tuple[str, ...]) -> bool:
    """Whether the glob segments match the path segments from their start to their end."""
    if not glob:
        return not path
    head, rest = glob[0], glob[1:]
    if head == '**':
        return any(_match_from(rest, path[skip:]) for skip in range(len(path) + 1))
    if not path:
        return False
    return _segment_pattern(head).fullmatch(path[0].casefold()) is not None and _match_from(
        rest, path[1:]
    )


def malformed(glob: str) -> str | None:
    """Why `glob` is malformed, or None: such a glob matches nothing and leaves the guard open."""
    if '\\' in glob:
        return 'contains a backslash'
    if glob.startswith('/') or glob.endswith('/'):
        return 'starts or ends with a slash'
    for segment in glob.split('/'):
        if segment == '':
            return 'has an empty segment'
        if segment in ('.', '..'):
            return f'has a {segment!r} segment'
    return None


def matches(glob: str, path: str) -> bool:
    """Whether `glob` matches the whole `path` or a suffix of it that starts at a `/` boundary."""
    glob_segments = tuple(glob.split('/'))
    segments = tuple(path.split('/'))
    return any(_match_from(glob_segments, segments[start:]) for start in range(len(segments)))


# --- git ---------------------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(['git', *args], cwd=root, capture_output=True, check=False)
    except OSError as exc:
        raise _UsageError(f'cannot run git: {exc}') from exc


def _text(data: bytes) -> str:
    return data.decode('utf-8', errors='replace').strip()


def _split_range(value: str) -> tuple[str, str]:
    base, separator, head = value.partition(RANGE_SEPARATOR)
    if not separator or RANGE_SEPARATOR in head or not base:
        raise _UsageError(f'--diff expects BASE{RANGE_SEPARATOR}HEAD, not {value!r}')
    return base, head or 'HEAD'


def _changed_paths(root: Path, base: str, head: str) -> list[str]:
    done = _git(root, 'diff', '--no-renames', '--name-only', '-z', f'{base}{RANGE_SEPARATOR}{head}')
    if done.returncode != 0:
        raise _UsageError(f'git diff failed: {_text(done.stderr)}')
    return [name.decode('utf-8', errors='replace') for name in done.stdout.split(b'\0') if name]


def _config_at(root: Path, ref: str) -> dict[str, Any] | None:
    """The parsed `railform.toml` as committed at `ref`, or None when `ref` has none."""
    commit = _git(root, 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}')
    if commit.returncode != 0:
        raise _UsageError(f'{ref}: not a commit')
    spec = f'{_text(commit.stdout)}:{frame.CONFIG_NAME}'
    if _git(root, 'cat-file', '-e', spec).returncode != 0:
        return None
    done = _git(root, 'cat-file', 'blob', spec)
    if done.returncode != 0:
        raise _UsageError(f'cannot read {ref}:{frame.CONFIG_NAME}: {_text(done.stderr)}')
    try:
        return frame.parse_config(done.stdout, f'{ref}:{frame.CONFIG_NAME}')
    except frame.FrameError as exc:
        raise _UsageError(str(exc)) from exc


def _frame_at(root: Path, ref: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The effective frame at `ref` and the `railform.toml` it was built from."""
    config = _config_at(root, ref)
    try:
        return frame.effective_frame(config), config
    except frame.FrameSchemaError as exc:
        raise _UsageError(f'{ref}:{frame.CONFIG_NAME}: {exc}') from exc


# --- the check ---------------------------------------------------------------------------------


def _narrowed(
    guard: list[str], head_frame: dict[str, Any], head_config: dict[str, Any] | None
) -> list[str]:
    """Findings for the globs of `guard` that the frame at HEAD no longer holds."""
    rows = [row for row in (head_config or {}).get('exceptions', []) if isinstance(row, dict)]
    dropped = {
        glob
        for row in rows
        if row.get('key') == SESSION_LOADED and isinstance(row.get('drop'), list)
        for glob in row['drop']
    }
    findings: list[str] = []
    for glob in guard:
        if glob not in head_frame[SESSION_LOADED]:
            how = 'an exceptions row drops' if glob in dropped else 'removes'
            findings.append(
                f'{frame.CONFIG_NAME}: {how} the {SESSION_LOADED} glob {glob} '
                f'that the frame it is judged against has'
            )
    # A row that gives the list a value is a bad row (frame --check), but it states an intent to
    # narrow the guard, so it is reported here too.
    for row in rows:
        if row.get('key') != SESSION_LOADED or 'value' not in row:
            continue
        value = row.get('value')
        kept = value if isinstance(value, list) else []
        findings += [
            f'{frame.CONFIG_NAME}: excepts the {SESSION_LOADED} glob {glob} '
            f'that the frame it is judged against has'
            for glob in guard
            if glob not in kept
        ]
    return list(dict.fromkeys(findings))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Fail when a change touches a path a session loads (the D1 guard). The globs are the '
        'session_loaded globs of the frame at --frame-from, never the ones the change writes.'
    )
    parser.add_argument('--diff', required=True, metavar='BASE...HEAD', help='the change to judge')
    parser.add_argument(
        '--frame-from',
        required=True,
        metavar='REF',
        help='the commit whose railform.toml supplies the globs (the default frame if it has none)',
    )
    parser.add_argument(
        '--glob',
        action='append',
        default=[],
        metavar='PATTERN',
        help='add a glob to those read from REF (repeatable); never removes one',
    )


def run(args: argparse.Namespace) -> Verdict:
    """Judge the change `args.diff` against the globs of the frame at `args.frame_from`."""
    extra: list[str] = list(args.glob)
    try:
        if any(not pattern.strip() for pattern in extra):
            raise _UsageError('--glob: the pattern is empty')
        base, head = _split_range(args.diff)
        root = frame.repository_root()
        guard_frame, _ = _frame_at(root, args.frame_from)
        guard: list[str] = list(guard_frame[SESSION_LOADED])
        for glob in [*guard, *extra]:
            if (reason := malformed(glob)) is not None:
                raise _UsageError(f'the glob {glob!r} {reason}')
        globs = list(dict.fromkeys([*guard, *extra]))
        paths = _changed_paths(root, base, head)
        head_frame, head_config = _frame_at(root, head)
    except (_UsageError, frame.FrameError) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    loaded = [
        f'{path}: matches the {SESSION_LOADED} glob {glob}'
        for path in paths
        if (glob := next((g for g in globs if matches(g, path)), None)) is not None
    ]
    narrowed = _narrowed(guard, head_frame, head_config)
    if loaded:
        return Verdict.fail(VERB, SESSION_LOADED_CODE, tuple(loaded + narrowed))
    if narrowed:
        return Verdict.fail(VERB, GUARD_NARROWED_CODE, tuple(narrowed))
    return Verdict.ok(VERB)
