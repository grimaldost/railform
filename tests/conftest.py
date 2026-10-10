"""Shared fixtures."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope='session')
def _scratch_repo_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp('scratch')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    return repo


@pytest.fixture(autouse=True)
def scratch_repo(_scratch_repo_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run every test inside a throwaway git repository, never in the developer's clone.

    Verbs work on the repository that contains the current directory, so a verb that arms
    hooks or writes files must find a scratch repository here.
    """
    monkeypatch.chdir(_scratch_repo_path)
    return _scratch_repo_path
