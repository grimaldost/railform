"""Shared fixtures: every test runs in its own scratch repository, with git isolated from the
developer's clone and configuration."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# The variables that point git at a repository other than the one in the current directory
# (`git rev-parse --local-env-vars`, plus the namespace and the ceiling). Inherited from a git
# hook or a parent git process, `GIT_DIR` would make `git init <dir>` reinitialise that other
# repository, and mark it bare.
LOCATING_VARIABLES = (
    'GIT_DIR',
    'GIT_WORK_TREE',
    'GIT_IMPLICIT_WORK_TREE',
    'GIT_INDEX_FILE',
    'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    'GIT_GRAFT_FILE',
    'GIT_SHALLOW_FILE',
    'GIT_NO_REPLACE_OBJECTS',
    'GIT_REPLACE_REF_BASE',
    'GIT_NAMESPACE',
    'GIT_PREFIX',
    'GIT_CEILING_DIRECTORIES',
)


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
