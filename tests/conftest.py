"""Shared fixtures: every test runs in its own scratch repository, with git isolated from the
developer's clone, from any repository a parent process points at, and from every configuration
but the scratch repository's own."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import TYPE_CHECKING

import pytest

from hook_isolation import outside_variables

if TYPE_CHECKING:
    from pathlib import Path

# Variables git does not list as repository-local but that still redirect it: the ref namespace
# and the ceiling of the repository search.
EXTRA_VARIABLES = ('GIT_NAMESPACE', 'GIT_CEILING_DIRECTORIES')
# `GIT_CONFIG_COUNT` comes with numbered pairs of these.
_CONFIG_PAIR = re.compile(r'GIT_CONFIG_(KEY|VALUE)_\d+')


def _local_env_vars() -> tuple[str, ...]:
    """What `git rev-parse --local-env-vars` lists: the variables that point git at a
    repository, an index or a configuration (`GIT_DIR`, `GIT_CONFIG`, `GIT_CONFIG_COUNT`...).

    Asked once, outside any repository and with no `GIT_*` variable, so the answer cannot be
    bent by what it lists."""
    clean = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    with tempfile.TemporaryDirectory() as directory:
        done = subprocess.run(
            ['git', 'rev-parse', '--local-env-vars'],
            cwd=directory,
            env=clean,
            capture_output=True,
            text=True,
            check=True,
        )
    names = tuple(line.strip() for line in done.stdout.splitlines() if line.strip())
    assert 'GIT_DIR' in names and 'GIT_CONFIG' in names, names
    return names


LOCATING_VARIABLES = (*_local_env_vars(), *EXTRA_VARIABLES)


@pytest.fixture(scope='session')
def empty_git_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An empty file that stands in for the global git configuration."""
    path = tmp_path_factory.mktemp('git-config') / 'empty'
    path.write_bytes(b'')
    return path


def isolate_git(monkeypatch: pytest.MonkeyPatch, global_config: Path) -> None:
    """Make every git process a test starts see only the repository in its own directory and
    no configuration but that repository's."""
    for name in LOCATING_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name in [key for key in os.environ if _CONFIG_PAIR.fullmatch(key)]:
        monkeypatch.delenv(name)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(global_config))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')


def new_scratch_repo(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, global_config: Path
) -> Path:
    """Isolate git, initialise a git repository in a new temporary directory and enter it."""
    isolate_git(monkeypatch, global_config)
    repo = tmp_path_factory.mktemp('scratch')
    subprocess.run(['git', 'init', '-q', str(repo)], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture(autouse=True)
def scratch_repo(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    empty_git_config: Path,
) -> Path:
    """Run every test inside its own throwaway git repository, never in the developer's clone.

    Verbs work on the repository that contains the current directory, so a verb that arms
    hooks or writes files must find a scratch repository here. Each test gets a fresh one, so
    a file one test writes never reaches the next.
    """
    return new_scratch_repo(tmp_path_factory, monkeypatch, empty_git_config)


@pytest.fixture(scope='session')
def outside(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """The variables that point pre-commit and the hooks' tools away from the developer's home
    and caches, one set per session (`tests/hook_isolation.py`)."""
    return outside_variables(tmp_path_factory)
