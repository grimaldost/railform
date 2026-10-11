"""The type's pipeline steps pass in each adopted migration fixture (development-rails spec, §7).

These tests show that the migration fixtures are valid projects of their type once adopted. The
steps are the ones the `checks` job of the reusable workflow runs (§9): `frame.pipeline`, built
from the effective frame of the fixture after adoption, one command per step, with the Python
version in `UV_PYTHON` as the workflow sets it per matrix leg. For the two uv types they are the
lock check, the sync, the format check, the lint, `ty` over `python.type_check` when the type
sets it, and `python.test_command`; for `plugin-marketplace`, ruff from `uvx` at
`ci.ruff_version` and `python.test_command`. They run under the interpreter of this test run;
the fixture's lock is written here with `uv lock`, as any uv project commits one.
`tests/test_python_ci_runs.py` runs the same steps through the workflow's own text.

These tests resolve and install the fixtures' tools, so they need the network (or a warm uv
cache): they are marked `slow` and still run by default. Git is isolated by `tests/conftest.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest

from migration_fixtures import PIN, TYPES, build
from railform import frame
from railform.cli import main
from workflow_model import bash

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bash() is None, reason='no bash on this machine'),
]

TIMEOUT = 600
PYTHON = f'{sys.version_info.major}.{sys.version_info.minor}'


def _environment() -> dict[str, str]:
    """This run's environment without the variables that would point uv at railform's own venv,
    and with the Python version of this run, as the workflow sets it per matrix leg."""
    dropped = {'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC'}
    kept = {key: value for key, value in os.environ.items() if key not in dropped}
    return {**kept, 'UV_PYTHON': PYTHON}


def _run(root: Path, step: str) -> subprocess.CompletedProcess[str]:
    """Run one pipeline step, a bash command line, as the workflow's bash does."""
    return subprocess.run(
        [*_shell(), step],
        cwd=root,
        env=_environment(),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=TIMEOUT,
        check=False,
    )


def _shell() -> list[str]:
    executable = bash()
    assert executable is not None, 'no bash on this machine'
    return [executable, '--noprofile', '--norc', '-eo', 'pipefail', '-c']


def _adopted(root: Path, project_type: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    build(root, project_type)
    assert main(['adopt', '--pin', PIN]) == 0
    assert main(['adopt', '--check']) == 0
    assert main(['frame', '--check']) == 0
    capsys.readouterr()
    effective = frame.load(root)
    assert effective['project']['type'] == project_type
    if project_type != 'plugin-marketplace':
        # The lock is part of the fixture, as in any uv project.
        locked = _run(root, 'uv lock')
        assert locked.returncode == 0, locked.stderr
    return effective


@pytest.mark.parametrize('project_type', TYPES)
def test_the_type_pipeline_steps_pass_in_the_adopted_fixture(
    project_type: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    effective = _adopted(scratch_repo, project_type, capsys)
    steps = frame.pipeline(effective)
    expected = {'python-package': 6, 'python-scripts': 5, 'plugin-marketplace': 3}
    assert len(steps) == expected[project_type]
    for step in steps:
        done = _run(scratch_repo, step)
        assert done.returncode == 0, (step, done.stdout[-2000:], done.stderr[-2000:])


def test_red_proof_a_failing_test_fails_the_test_step(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The steps above prove something only if a broken fixture fails them.
    effective = _adopted(scratch_repo, 'plugin-marketplace', capsys)
    step = frame.pipeline(effective)[-1]
    passed = _run(scratch_repo, step)
    assert passed.returncode == 0, passed.stdout[-2000:]
    test = scratch_repo / 'tests' / 'test_names.py'
    test.write_bytes(test.read_bytes().replace(b'"demo-plugin"', b'"other"'))
    failed = _run(scratch_repo, step)
    assert failed.returncode == 1, failed.stdout[-2000:]
    assert '1 failed' in failed.stdout
