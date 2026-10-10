"""Shared fixtures."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def scratch_repo(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run every test inside its own throwaway git repository, never in the developer's clone.

    Verbs work on the repository that contains the current directory, so a verb that arms
    hooks or writes files must find a scratch repository here. Each test gets a fresh one, so
    a file one test writes never reaches the next.
    """
    repo = tmp_path_factory.mktemp('scratch')
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    monkeypatch.chdir(repo)
    return repo
