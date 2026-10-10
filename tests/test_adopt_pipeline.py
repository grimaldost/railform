"""The type's pipeline steps pass in each adopted migration fixture (development-rails spec, §7).

The steps are the `checks` job of the project type (spec design decision 3), each built from the
effective frame of the fixture after adoption: for the two uv types the lock check, the sync, the
format check, the lint, `ty` over `python.type_check` when the type sets it, and
`python.test_command`; for `plugin-marketplace` ruff from `uvx` at `ci.ruff_version` and
`python.test_command`. They run under the interpreter of this test run.

These tests resolve and install the fixtures' tools, so they need the network (or a warm uv
cache): they are marked `slow` and still run by default.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

from migration_fixtures import PIN, TYPES, build
from railform import frame
from railform.cli import main

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.slow

TIMEOUT = 600


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')


def _environment() -> dict[str, str]:
    """This run's environment without the variables that would point uv at railform's own venv."""
    dropped = {'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC'}
    return {key: value for key, value in os.environ.items() if key not in dropped}


def pipeline(effective: dict[str, Any], python: str) -> list[list[str]]:
    """The steps of the `checks` job for the fixture's frame, in the type's fixed order."""
    settings = effective['python']
    lint = list(settings['lint_paths'])
    test = shlex.split(settings['test_command'])
    if effective['project']['type'] == 'plugin-marketplace':
        ruff = ['uvx', f'ruff=={effective["ci"]["ruff_version"]}']
        return [[*ruff, 'format', '--check', *lint], [*ruff, 'check', *lint], test]
    steps = [
        ['uv', 'lock', '--check'],
        ['uv', 'sync', '--frozen', '--python', python, *settings['sync_args']],
        ['uv', 'run', '--frozen', 'ruff', 'format', '--check', *lint],
        ['uv', 'run', '--frozen', 'ruff', 'check', *lint],
    ]
    if settings['type_check']:
        steps.append(['uv', 'run', '--frozen', 'ty', 'check', settings['type_check']])
    return [*steps, test]


def _run(root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        env=_environment(),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=TIMEOUT,
        check=False,
    )


@pytest.mark.parametrize('project_type', TYPES)
def test_the_type_pipeline_steps_pass_in_the_adopted_fixture(
    project_type: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, project_type)
    assert main(['adopt', '--pin', PIN]) == 0
    assert main(['adopt', '--check']) == 0
    assert main(['frame', '--check']) == 0
    capsys.readouterr()
    effective = frame.load(scratch_repo)
    assert effective['project']['type'] == project_type
    if project_type != 'plugin-marketplace':
        # The lock is part of the fixture, as in any uv project.
        locked = _run(scratch_repo, ['uv', 'lock'])
        assert locked.returncode == 0, locked.stderr
    python = f'{sys.version_info.major}.{sys.version_info.minor}'
    steps = pipeline(effective, python)
    assert (
        len(steps)
        == {'python-package': 6, 'python-scripts': 5, 'plugin-marketplace': 3}[project_type]
    )
    for step in steps:
        done = _run(scratch_repo, step)
        assert done.returncode == 0, (step, done.stdout[-2000:], done.stderr[-2000:])


def test_a_failing_test_fails_the_pipeline(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The steps above prove something only if a broken fixture fails them.
    build(scratch_repo, 'plugin-marketplace')
    assert main(['adopt', '--pin', PIN]) == 0
    capsys.readouterr()
    test = scratch_repo / 'tests' / 'test_names.py'
    test.write_bytes(test.read_bytes().replace(b'"demo-plugin"', b'"other"'))
    effective = frame.load(scratch_repo)
    done = _run(scratch_repo, pipeline(effective, '3.14')[-1])
    assert done.returncode != 0
