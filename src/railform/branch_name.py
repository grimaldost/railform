"""The verb `railform branch-name [-- NAME]`: the branch rail.

Development-rails spec, §8 and its round-6 amendment; interface contract, section 10. Invented
here (ADR-0015 row 14 names the rail; no repository had one to port).

The name judged is, in this order: the positional `NAME`; else `$PRE_COMMIT_REMOTE_BRANCH`
without `refs/heads/`, which pre-commit's `pre-push` stage sets, so the hook judges the name the
branch gets on the remote; else the current branch. A name passes when it matches a glob of
`branch.allow`, or when the whole name matches the regular expression `branch.pattern`. An empty
name fails. A detached HEAD with no other source passes, and so does a remote ref that is not a
branch (a tag): there is no branch name to judge.

pre-commit runs no `pre-push` hook for a ref whose commits are all on the remote already, and in
a push of several refs it hands the hooks only the first ref with new commits; the README names
both blind cases. The `commit-lane` job of the reusable workflow (§9) judges every pull request's
head branch with this verb, so a branch no pre-push saw is still judged.
"""

from __future__ import annotations

import os
import re
import subprocess
from functools import cache
from typing import TYPE_CHECKING, Any

from railform import frame as frame_module
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

VERB = 'branch-name'
CODE = 'branch'
REMOTE_BRANCH_ENV = 'PRE_COMMIT_REMOTE_BRANCH'
HEADS = 'refs/heads/'


class _GitError(Exception):
    """git could not say what the current branch is."""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Check a branch name against branch.allow and branch.pattern: the given NAME, else '
        f'${REMOTE_BRANCH_ENV} (set by pre-commit on pre-push), else the current branch.'
    )
    parser.add_argument(
        'name',
        nargs='?',
        metavar='NAME',
        help='the name to judge; put -- before it when it starts with a dash',
    )


@cache
def _glob_regex(glob: str) -> re.Pattern[str]:
    """`**` spans any characters, `/` included; `*` any characters but `/`; `?` one such
    character. Every other character stands for itself."""
    parts: list[str] = []
    index = 0
    while index < len(glob):
        if glob.startswith('**', index):
            parts.append('.*')
            index += 2
        elif glob[index] == '*':
            parts.append('[^/]*')
            index += 1
        elif glob[index] == '?':
            parts.append('[^/]')
            index += 1
        else:
            parts.append(re.escape(glob[index]))
            index += 1
    return re.compile(''.join(parts), re.DOTALL)


def glob_matches(glob: str, name: str) -> bool:
    """Whether the `branch.allow` glob `glob` matches the whole of `name`."""
    return _glob_regex(glob).fullmatch(name) is not None


def judge(name: str, branch: dict[str, Any]) -> tuple[bool, str]:
    """Whether `name` passes the frame's `branch` table, and a line saying why.

    The frame schema holds `branch.allow` to a list of strings and `branch.pattern` to a
    regular expression that compiles.
    """
    if not name:
        return False, 'the branch name is empty'
    for glob in branch['allow']:
        if glob_matches(glob, name):
            return True, f'branch {name} matches branch.allow {glob}'
    pattern = branch['pattern']
    if re.fullmatch(pattern, name) is not None:
        return True, f'branch {name} matches branch.pattern'
    return False, f'branch {name} does not match branch.pattern {pattern} or branch.allow'


def _current_branch(root: Path) -> str | None:
    """The current branch, born or not; None on a detached HEAD."""
    try:
        done = subprocess.run(
            ['git', 'symbolic-ref', '--quiet', 'HEAD'],
            cwd=root,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='surrogateescape',
            check=False,
        )
    except OSError as exc:
        raise _GitError(f'cannot run git: {exc}') from exc
    if done.returncode == 1:
        return None
    if done.returncode != 0:
        raise _GitError(f'git symbolic-ref failed: {done.stderr.strip()}')
    ref = done.stdout.strip()
    return ref.removeprefix(HEADS) if ref.startswith(HEADS) else None


def run(args: argparse.Namespace) -> Verdict:
    try:
        root = frame_module.repository_root()
        branch = frame_module.load(root)['branch']
        if args.name is not None:
            name, source = args.name, 'the argument'
        elif (remote := os.environ.get(REMOTE_BRANCH_ENV)) is not None:
            source = f'${REMOTE_BRANCH_ENV}'
            if remote.startswith('refs/') and not remote.startswith(HEADS):
                return Verdict.ok(VERB, (f'{source} names {remote}, which is not a branch',))
            name = remote.removeprefix(HEADS)
        else:
            current = _current_branch(root)
            if current is None:
                return Verdict.ok(VERB, ('HEAD is detached: there is no branch name to judge',))
            name, source = current, 'the current branch'
        passed, why = judge(name, branch)
    except (frame_module.FrameError, frame_module.FrameSchemaError, _GitError, re.error) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    finding = f'{why} (from {source})'
    return Verdict.ok(VERB, (finding,)) if passed else Verdict.fail(VERB, CODE, (finding,))
