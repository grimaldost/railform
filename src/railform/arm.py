"""The verb `railform arm`: point a clone's git at the tracked hook wrappers.

Development-rails spec, §5; interface contract, section 7. It sets `core.hooksPath` to
`hooks.dir` in the clone's local configuration, for a person to run once in each clone;
worktrees share the clone's configuration. `railform hooks-check --armed` reads the same value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from railform import frame as frame_module
from railform.hooks_check import CONFIG_ERRORS, GitError, git, hooks_dir
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse

VERB = 'arm'


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = "Set core.hooksPath to hooks.dir in this clone's local git configuration."


def run(args: argparse.Namespace) -> Verdict:
    del args
    try:
        root = frame_module.repository_root()
        directory = hooks_dir(frame_module.load(root))
        done = git(root, 'config', '--local', 'core.hooksPath', directory)
        if done.returncode != 0:
            detail = done.stderr.decode('utf-8', 'replace').strip()
            raise GitError(f'git config failed: {detail}')
    except (*CONFIG_ERRORS, GitError) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    return Verdict.ok(VERB, (f'core.hooksPath is now {directory}',))
