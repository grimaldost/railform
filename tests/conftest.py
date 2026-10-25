"""Shared fixtures: every test runs in its own scratch repository, with git isolated from the
developer's clone, from any repository a parent process points at, and from every configuration
but the scratch repository's own, and with the home directory, the configuration directory and
the pre-commit and uv tool directories moved into temporary directories, so nothing the runner
keeps there (a global git ignore file, a pre-commit cache, an installed uv tool) reaches a test."""

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


def isolate_home(monkeypatch: pytest.MonkeyPatch, outside: dict[str, str]) -> None:
    """Move the home directory, the configuration directory and the pre-commit and uv tool
    directories to the temporary ones in `outside`.

    `GIT_CONFIG_GLOBAL` replaces git's global configuration file only: git still reads a global
    ignore file from `$XDG_CONFIG_HOME/git/ignore` (`~/.config/git/ignore` when that is unset),
    and a runner that ignores a path there made `git add` skip it in a scratch repository."""
    for name, value in outside.items():
        monkeypatch.setenv(name, value)


def new_scratch_repo(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    global_config: Path,
    outside: dict[str, str] | None = None,
) -> Path:
    """Isolate git and, given `outside`, the runner's home; initialise a git repository in a new
    temporary directory and enter it."""
    isolate_git(monkeypatch, global_config)
    if outside is not None:
        isolate_home(monkeypatch, outside)
    repo = tmp_path_factory.mktemp('scratch')
    subprocess.run(['git', 'init', '-q', str(repo)], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture(autouse=True)
def scratch_repo(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    empty_git_config: Path,
    outside: dict[str, str],
) -> Path:
    """Run every test inside its own throwaway git repository, never in the developer's clone.

    Verbs work on the repository that contains the current directory, so a verb that arms
    hooks or writes files must find a scratch repository here. Each test gets a fresh one, so
    a file one test writes never reaches the next.
    """
    return new_scratch_repo(tmp_path_factory, monkeypatch, empty_git_config, outside)


@pytest.fixture(scope='session')
def outside(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """The variables that point git's home-relative files, pre-commit and the hooks' tools away
    from the developer's home and caches, one set per session (`tests/hook_isolation.py`), with
    the directories uv installs tools into. Every test runs with them (`scratch_repo`)."""
    return outside_variables(tmp_path_factory) | {
        'UV_TOOL_DIR': str(tmp_path_factory.mktemp('uv-tools')),
        'UV_TOOL_BIN_DIR': str(tmp_path_factory.mktemp('uv-tool-bin')),
    }
