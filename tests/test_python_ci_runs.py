"""Runs of a fixture caller through the reusable workflow (development-rails spec, §9).

Each test builds a migration fixture, adopts it at a pin, and runs its `ci.yml` in the local model
of `tests/workflow_model.py`: the caller's `railform` job runs `python-ci.yml` with the pin as its
input, and the caller's `gate` job judges the result. The workflows' real text runs; railform is
the code under test.

The red proofs of §9 that run here, each named in its test:

- a fixture caller whose test step fails turns its `gate` red
  (`test_red_proof_a_failing_test_step_turns_gate_red`, which needs the network or a warm uv
  cache);
- a push run, where `commit-lane` and `changelog` are skipped, still turns `gate` green when the
  rest passes (`test_red_proof_a_push_run_skips_the_pull_request_jobs_and_gate_passes`, and the
  green run of the test above);
- a fixture caller that sets `python.test_command` to `true` with no exceptions row turns `gate`
  red (`test_red_proof_a_test_command_of_true_without_an_exceptions_row_turns_gate_red`);
- the round-6 amendment's pull request from a branch named `foo` turns `gate` red
  (`test_red_proof_a_pull_request_from_foo_turns_gate_red`).

The audit job runs zizmor, which needs the network, so it is given as passed; a test that is not
about a job may give it too, and says so. The live proofs are the red-proof branches of the pull
request that added the workflow, run on GitHub's runners.
"""

from __future__ import annotations

import itertools
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from migration_fixtures import CI, PIN, build, git
from railform import frame
from railform.cli import main
from workflow_model import (
    WORKFLOWS,
    Runner,
    WorkflowRun,
    bash,
    github_context,
    legs,
    load,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(bash() is None, reason='no bash on this machine')

IDENT = ('-c', 'user.name=someone', '-c', 'user.email=someone@example.com')
AUDIT = {'audit': 'success'}


def _adopted(root: Path, project_type: str, capsys: pytest.CaptureFixture[str]) -> Path:
    build(root, project_type)
    assert main(['adopt', '--pin', PIN]) == 0
    capsys.readouterr()
    return root


def _run(
    root: Path,
    scratch: Path,
    event: str,
    given: dict[str, str],
    payload: dict[str, Any] | None = None,
    **github: Any,
) -> WorkflowRun:
    runner = Runner(
        root=root,
        scratch=scratch,
        github=github_context(event, **github),
        event=payload or {},
        given=given,
    )
    return runner.run(root / CI)


def _write_frame(root: Path, text: str) -> None:
    """Replace the fixture's `railform.toml` with a python-package frame and `text`."""
    config = '[project]\ntype = "python-package"\n\n' + text
    (root / 'railform.toml').write_text(config, encoding='utf-8')


# --- the push run ------------------------------------------------------------------------------


def test_red_proof_a_push_run_skips_the_pull_request_jobs_and_gate_passes(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _adopted(scratch_repo, 'python-package', capsys)
    # The checks job's pipeline is run for real by the slow test below; here it is given.
    run = _run(root, tmp_path, 'push', {**AUDIT, 'checks': 'success'})
    results = run.results()
    assert results['railform/commit-lane'] == 'skipped', run.text()
    assert results['railform/changelog'] == 'skipped', run.text()
    assert results['railform/frame'] == 'success', run.text()
    assert results['railform/hooks'] == 'success', run.text()
    assert results['railform'] == 'success', run.text()
    assert results['gate'] == 'success', run.text()
    # Not vacuous: the same run with a failed check turns gate red.
    run = _run(root, tmp_path, 'push', {**AUDIT, 'checks': 'failure'})
    assert run.results()['gate'] == 'failure', run.text()


# --- the frame ---------------------------------------------------------------------------------


def test_red_proof_a_test_command_of_true_without_an_exceptions_row_turns_gate_red(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _adopted(scratch_repo, 'python-package', capsys)
    blanked = '[python]\ntest_command = "true"\n'
    _write_frame(root, blanked)
    run = _run(root, tmp_path, 'push', AUDIT)
    results = run.results()
    assert results['railform/frame'] == 'failure', run.text()
    assert 'railform-verdict: frame fail unlisted-departure' in run.text()
    # Everything after the frame is skipped, so the blanked test command runs nowhere.
    for name in ('checks', 'commit-lane', 'changelog', 'hooks'):
        assert results[f'railform/{name}'] == 'skipped', (name, run.text())
    assert results['gate'] == 'failure', run.text()
    # With the exceptions row the departure is declared, and the frame passes.
    _write_frame(
        root,
        blanked + '\n[[exceptions]]\nkey = "python.test_command"\nvalue = "true"\n'
        'reason = "the suite runs elsewhere"\n',
    )
    run = _run(root, tmp_path, 'push', {**AUDIT, 'checks': 'success'})
    assert run.results()['railform/frame'] == 'success', run.text()
    assert run.results()['gate'] == 'success', run.text()


def test_a_pin_that_is_not_a_full_commit_stops_the_run_before_any_install(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _adopted(scratch_repo, 'python-package', capsys)
    ci = root / CI
    ci.write_text(
        ci.read_text(encoding='utf-8').replace(f'railform-sha: {PIN}', 'railform-sha: main'),
        encoding='utf-8',
    )
    run = _run(root, tmp_path, 'push', AUDIT)
    results = run.results()
    assert results['railform/frame'] == 'failure', run.text()
    assert 'railform-sha must be a railform commit' in run.text()
    assert 'railform-verdict' not in run.text()
    assert results['gate'] == 'failure', run.text()


def test_the_checks_matrix_and_pipeline_come_from_the_frame(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _adopted(scratch_repo, 'python-package', capsys)
    workflow = load(WORKFLOWS / 'python-ci.yml')

    def outputs() -> dict[str, str]:
        run = _run(root, tmp_path, 'push', {**AUDIT, 'checks': 'success'})
        assert run.results()['railform/frame'] == 'success', run.text()
        return run.called['railform'].jobs['frame'].outputs

    def matrix(found: dict[str, str]) -> list[dict[str, Any]]:
        return legs(workflow['jobs']['checks'], {'needs': {'frame': {'outputs': found}}})

    effective = frame.load(root)
    found = outputs()
    assert found['pipeline'].splitlines() == frame.pipeline(effective)
    assert matrix(found) == [
        {'os': os_name, 'python': version}
        for os_name, version in itertools.product(
            effective['ci']['os'], effective['python']['versions']
        )
    ]
    # A change of the frame changes the legs and the commands, with no change to the workflow.
    _write_frame(
        root,
        '[ci]\nos = ["ubuntu-latest"]\n\n[python]\nversions = ["3.13"]\n'
        'lint_paths = ["src", "tests"]\n\n'
        '[[exceptions]]\nkey = "ci.os"\nvalue = ["ubuntu-latest"]\nreason = "one system"\n\n'
        '[[exceptions]]\nkey = "python.lint_paths"\nvalue = ["src", "tests"]\n'
        'reason = "only the code"\n',
    )
    found = outputs()
    assert matrix(found) == [{'os': 'ubuntu-latest', 'python': '3.13'}]
    assert 'uv run --frozen ruff check src tests' in found['pipeline'].splitlines()


# --- the pull request's branch -----------------------------------------------------------------


def _pull_request(root: Path) -> dict[str, Any]:
    """Commit the fixture as the base branch, then one change on top, as a pull request has."""
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'chore: the base')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'NOTES.txt').write_text('a change\n', encoding='utf-8')
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'docs: add a note')
    return {'pull_request': {'title': 'docs: add a note', 'body': 'A note.'}}


@pytest.mark.parametrize(('branch', 'result'), [('foo', 'failure'), ('docs/add-a-note', 'success')])
def test_red_proof_a_pull_request_from_foo_turns_gate_red(
    scratch_repo: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    branch: str,
    result: str,
) -> None:
    root = _adopted(scratch_repo, 'python-package', capsys)
    payload = _pull_request(root)
    # The hygiene replay of the hooks job installs pre-commit hooks from the network.
    given = {**AUDIT, 'checks': 'success', 'hooks': 'success'}
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref=branch)
    results = run.results()
    assert results['railform/commit-lane'] == result, run.text()
    assert 'railform-verdict: commit-range pass ok' in run.text()
    assert 'railform-verdict: pr-text pass ok' in run.text()
    verdict = 'pass ok' if result == 'success' else 'fail branch'
    assert f'railform-verdict: branch-name {verdict}' in run.text()
    assert results['gate'] == result, run.text()


def test_a_branch_name_that_looks_like_a_command_is_only_a_name(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _adopted(scratch_repo, 'python-package', capsys)
    payload = _pull_request(root)
    marker = tmp_path / 'ran'
    branch = f'x";touch {marker.as_posix()};"$(touch {marker.as_posix()})'
    given = {**AUDIT, 'checks': 'success', 'hooks': 'success'}
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref=branch)
    assert run.results()['railform/commit-lane'] == 'failure', run.text()
    assert not marker.exists()


# --- the pipeline, run for real ----------------------------------------------------------------


@pytest.mark.slow
def test_red_proof_a_failing_test_step_turns_gate_red(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The plugin-marketplace fixture: its pipeline needs no lock and no sync, only ruff and
    # pytest from uv, so it is the quickest to run for real.
    root = _adopted(scratch_repo, 'plugin-marketplace', capsys)
    run = _run(root, tmp_path, 'push', AUDIT)
    results = run.results()
    assert results['railform/checks'] == 'success', run.text()
    assert results['railform/commit-lane'] == 'skipped'
    assert results['railform/changelog'] == 'skipped'
    assert results['gate'] == 'success', run.text()
    test = root / 'tests' / 'test_names.py'
    test.write_bytes(test.read_bytes().replace(b'"demo-plugin"', b'"other"'))
    run = _run(root, tmp_path, 'push', AUDIT)
    results = run.results()
    assert results['railform/frame'] == 'success', run.text()
    assert results['railform/hooks'] == 'success', run.text()
    assert results['railform/checks'] == 'failure', run.text()
    assert '1 failed' in run.text()
    assert results['railform'] == 'failure'
    assert results['gate'] == 'failure', run.text()


def test_the_model_runs_bash_from_git_on_windows() -> None:
    # The WSL launcher in System32 is never the shell these tests run.
    path = bash()
    assert path is not None
    assert 'system32' not in path.casefold()
    done = subprocess.run([path, '-c', 'echo $BASH_VERSION'], capture_output=True, text=True)
    assert done.returncode == 0
    assert done.stdout.strip()
