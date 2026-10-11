"""The verb `railform hooks-check`: every hook wrapper exists, is executable and is the wrapper.

Development-rails spec, §5; interface contract, section 7. It ports the rules of radar-engine's
`tests/test_commit_lane.py` (https://github.com/grimaldost/stack-radar) that concern the
wrapper files, with the hook directory, the hook names and the wrapper command read from the
frame:

- `missing`: the file `<hooks.dir>/<hook>` does not exist;
- `mode`: the git index does not track it as `100755`. On Linux and macOS git skips a hook that
  is not executable, so a clone would look armed while no check ran. The mode is read from the
  index, because a working-tree executable bit means nothing on Windows;
- `content`: its text, with CRLF read as LF, is not the rendered wrapper;
- `not-armed` (`--armed` only): `git config core.hooksPath` is not `hooks.dir`.

The source's other rules (each hook's configuration declares its stage, pre-commit is in the dev
group, every `uv run` is frozen) are not ported here.
"""

from __future__ import annotations

import posixpath
import subprocess
from typing import TYPE_CHECKING, Any

from railform import frame as frame_module
from railform.verdict import Verdict
from railform.wrapper import WrapperError, hooks_dir, render

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

VERB = 'hooks-check'
EXECUTABLE = '100755'
MISSING = 'missing'
MODE = 'mode'
CONTENT = 'content'
NOT_ARMED = 'not-armed'
# When several rules fail, the verdict names the first in this order.
CODE_ORDER = (MISSING, MODE, CONTENT, NOT_ARMED)

CONFIG_ERRORS = (frame_module.FrameError, frame_module.FrameSchemaError, WrapperError)


class GitError(Exception):
    """git could not answer; reported as a configuration error."""


def git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run git in `root`, reading pathspecs literally."""
    try:
        return subprocess.run(
            ['git', '--literal-pathspecs', *args], cwd=root, capture_output=True, check=False
        )
    except OSError as exc:
        raise GitError(f'cannot run git: {exc}') from exc


def _stderr(done: subprocess.CompletedProcess[bytes]) -> str:
    return done.stderr.decode('utf-8', 'replace').strip()


def index_mode(root: Path, path: str) -> str | None:
    """The mode the git index records for `path`, or None when it does not track it."""
    done = git(root, 'ls-files', '-s', '-z', '--', path)
    if done.returncode != 0:
        raise GitError(f'git ls-files failed: {_stderr(done)}')
    for entry in done.stdout.split(b'\0'):
        meta, _, name = entry.partition(b'\t')
        fields = meta.split()
        if len(fields) == 3 and fields[2] == b'0' and name.decode('utf-8', 'replace') == path:
            return fields[0].decode('ascii')
    return None


def hooks_path(root: Path) -> str | None:
    """The value of `git config core.hooksPath`, or None when it is unset."""
    done = git(root, 'config', '--get', 'core.hooksPath')
    if done.returncode == 1:
        return None
    if done.returncode != 0:
        raise GitError(f'git config failed: {_stderr(done)}')
    return done.stdout.decode('utf-8', 'replace').rstrip('\r\n')


def is_armed(current: str | None, directory: str) -> bool:
    """Whether a `core.hooksPath` value names `directory`, spelled as `hooks.dir` is (separators
    and `.` segments aside): the rule of `--armed`, which `railform adopt`'s arm hint follows."""
    return current is not None and posixpath.normpath(current.replace('\\', '/')) == directory


def _check_hook(
    root: Path, frame: dict[str, Any], directory: str, hook: str
) -> list[tuple[str, str]]:
    path = f'{directory}/{hook}'
    file = root / path
    if not file.is_file():
        return [(MISSING, f'{path}: the hook wrapper does not exist')]
    problems: list[tuple[str, str]] = []
    mode = index_mode(root, path)
    if mode != EXECUTABLE:
        state = 'not tracked' if mode is None else f'tracked as {mode}'
        problems.append(
            (
                MODE,
                f'{path}: {state}, not {EXECUTABLE}; git skips a hook that is not executable '
                f'on Linux and macOS. Fix with: git update-index --add --chmod=+x {path}',
            )
        )
    try:
        actual: str | None = file.read_bytes().decode('utf-8').replace('\r\n', '\n')
    except (OSError, UnicodeDecodeError):
        actual = None
    if actual != render(frame, hook):
        problems.append(
            (
                CONTENT,
                f'{path}: not the wrapper of the frame for the {hook} stage; '
                'regenerate it with `railform adopt`',
            )
        )
    return problems


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Check that every hook in hooks.names exists in hooks.dir, is tracked with git mode '
        '100755 and is the wrapper the frame renders.'
    )
    parser.add_argument(
        '--armed',
        action='store_true',
        help='also fail unless git config core.hooksPath equals hooks.dir',
    )


def run(args: argparse.Namespace) -> Verdict:
    try:
        root = frame_module.repository_root()
        frame = frame_module.load(root)
        directory = hooks_dir(frame)
        names = frame['hooks']['names']
        for hook in names:  # a frame that cannot yield a wrapper is refused before any check
            render(frame, hook)
        problems: list[tuple[str, str]] = []
        for hook in names:
            problems += _check_hook(root, frame, directory, hook)
        if args.armed:
            current = hooks_path(root)
            if not is_armed(current, directory):
                shown = 'unset' if current is None else repr(current)
                problems.append(
                    (
                        NOT_ARMED,
                        f'core.hooksPath is {shown}, not {directory!r}; arm this clone with '
                        '`railform arm`',
                    )
                )
    except (*CONFIG_ERRORS, GitError) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    if not problems:
        return Verdict.ok(VERB)
    code = min((code for code, _ in problems), key=CODE_ORDER.index)
    return Verdict.fail(VERB, code, tuple(message for _, message in problems))
