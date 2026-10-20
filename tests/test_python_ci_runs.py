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
- §7's acceptance: a failing extra job of the caller turns `gate` red
  (`test_red_proof_a_failing_extra_job_turns_gate_red`);
- the round-6 amendment's pull request from a branch named `foo` turns `gate` red
  (`test_red_proof_a_pull_request_from_foo_turns_gate_red`);
- §14 through its caller: a pull request whose branch commits an unrecorded change under `src/`
  turns `gate` red, and a push run of the same fixture skips `changelog` and stays green
  (`test_red_proof_an_unrecorded_change_turns_gate_red_on_a_pull_request`);
- a home path in a commit message or in the pull request's body turns `gate` red through the
  commit lane's `private-refs`
  (`test_red_proof_a_home_path_in_a_commit_message_turns_gate_red_on_a_pull_request`,
  `test_red_proof_a_home_path_in_the_pull_request_body_turns_gate_red`);
- a pull request that drops the version site that disagrees from its own frame still turns `gate`
  red, through the changelog job's `version-sites --base`
  (`test_red_proof_a_pull_request_cannot_drop_the_version_site_that_disagrees`).

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


def test_red_proof_a_failing_extra_job_turns_gate_red(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # §7: `railform adopt` makes `gate` need every job of the caller, the fixture's own `docs`
    # job too. The reusable workflow is not what this test is about, so `railform` is given.
    root = _adopted(scratch_repo, 'python-package', capsys)
    ci = root / CI
    good = 'run: echo "the documentation builds"'
    text = ci.read_text(encoding='utf-8')
    assert text.count(good) == 1, text
    assert 'docs' in load(ci)['jobs']['gate']['needs']
    given = {'railform': 'success'}
    ci.write_text(text.replace(good, 'run: exit 1'), encoding='utf-8')
    run = _run(root, tmp_path, 'push', given)
    assert run.results() == {'railform': 'success', 'docs': 'failure', 'gate': 'failure'}, (
        run.text()
    )
    # With the job restored, gate is green.
    ci.write_text(text, encoding='utf-8')
    run = _run(root, tmp_path, 'push', given)
    assert run.results() == {'railform': 'success', 'docs': 'success', 'gate': 'success'}, (
        run.text()
    )


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


def test_red_proof_an_unrecorded_change_turns_gate_red_on_a_pull_request(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The development-rails spec, §14, through its caller: the `changelog` job runs
    # `railform changelog-gate "origin/${BASE_REF}...HEAD"` on pull requests only.
    root = _adopted(scratch_repo, 'python-package', capsys)
    _pull_request(root)
    module = root / 'src' / 'demo' / '__init__.py'
    module.write_text(module.read_text(encoding='utf-8') + '\nLOUD = True\n', encoding='utf-8')
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'feat: a loud flag')
    payload = {'pull_request': {'title': 'feat: a loud flag', 'body': 'A flag.'}}
    given = {**AUDIT, 'checks': 'success', 'hooks': 'success'}
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref='feat/loud-flag')
    results = run.results()
    assert results['railform/changelog'] == 'failure', run.text()
    assert 'railform-verdict: changelog-gate fail unrecorded' in run.text()
    assert results['railform/commit-lane'] == 'success', run.text()
    assert results['gate'] == 'failure', run.text()
    # A push run skips the job, and gate stays green.
    run = _run(root, tmp_path, 'push', {**AUDIT, 'checks': 'success', 'hooks': 'success'})
    assert run.results()['railform/changelog'] == 'skipped', run.text()
    assert run.results()['gate'] == 'success', run.text()
    # Recorded, the same pull request passes.
    (root / 'CHANGELOG.md').write_text('# Changelog\n\n- A loud flag.\n', encoding='utf-8')
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'docs: record the loud flag')
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref='feat/loud-flag')
    assert run.results()['railform/changelog'] == 'success', run.text()
    assert 'railform-verdict: changelog-gate pass ok' in run.text()
    assert run.results()['gate'] == 'success', run.text()


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


# --- private references in the text a pull request carries --------------------------------------

# Not a default placeholder, and nobody's name.
STRANGER = 'mallory'


def _home() -> str:
    """A home path, built at run time so that no file of this repository carries one."""
    return '/'.join(['', 'home', STRANGER, 'work'])


def test_red_proof_a_home_path_in_a_commit_message_turns_gate_red_on_a_pull_request(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The checks job scans the tracked files, which carry no home path here; only the commit
    # lane reads the messages.
    root = _adopted(scratch_repo, 'python-package', capsys)
    payload = _pull_request(root)
    given = {**AUDIT, 'checks': 'success', 'hooks': 'success'}
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref='docs/add-a-note')
    assert run.results()['railform/commit-lane'] == 'success', run.text()
    assert 'railform-verdict: private-refs pass ok' in run.text()
    (root / 'NOTES.txt').write_text('a change, again\n', encoding='utf-8')
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', f'docs: copy the note from {_home()}')
    assert main(['private-refs']) == 0
    capsys.readouterr()
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref='docs/add-a-note')
    results = run.results()
    assert results['railform/commit-lane'] == 'failure', run.text()
    assert 'railform-verdict: commit-range pass ok' in run.text()
    assert 'railform-verdict: private-refs fail home-path' in run.text()
    assert STRANGER not in run.text()
    assert results['gate'] == 'failure', run.text()


def test_red_proof_a_home_path_in_the_pull_request_body_turns_gate_red(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _adopted(scratch_repo, 'python-package', capsys)
    payload = _pull_request(root)
    payload['pull_request']['body'] = f'A note.\n\nWritten in {_home()}.\n'
    given = {**AUDIT, 'checks': 'success', 'hooks': 'success'}
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref='docs/add-a-note')
    results = run.results()
    assert results['railform/commit-lane'] == 'failure', run.text()
    assert 'railform-verdict: pr-text pass ok' in run.text()
    assert 'pull request body:3: home path /home/<name>' in run.text()
    assert 'railform-verdict: private-refs fail home-path' in run.text()
    assert STRANGER not in run.text()
    assert results['gate'] == 'failure', run.text()


# --- the version sites a pull request is judged by ----------------------------------------------

PYPROJECT_SITE = (
    '[[version.sites]]\npath = "pyproject.toml"\npattern = \'(?m)^version = "([^"]+)"\'\n'
)
VERSION_SITE = '\n[[version.sites]]\npath = "VERSION"\npattern = \'([0-9.]+)\'\n'


def test_red_proof_a_pull_request_cannot_drop_the_version_site_that_disagrees(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The checks job reads version.sites from the frame the pull request writes; the changelog
    # job reads them again from the frame of the merge base.
    root = _adopted(scratch_repo, 'python-package', capsys)
    _write_frame(root, PYPROJECT_SITE + VERSION_SITE)
    (root / 'VERSION').write_text('0.1.0\n', encoding='utf-8')
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'chore: the base')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    # The pull request lets VERSION fall behind and drops its site from the frame.
    (root / 'VERSION').write_text('0.0.9\n', encoding='utf-8')
    _write_frame(root, PYPROJECT_SITE)
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'chore: drop a version site')
    assert main(['version-sites']) == 0
    capsys.readouterr()
    payload = {'pull_request': {'title': 'chore: drop a version site', 'body': 'A site.'}}
    given = {**AUDIT, 'checks': 'success', 'hooks': 'success'}
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref='chore/drop-a-site')
    results = run.results()
    assert results['railform/changelog'] == 'failure', run.text()
    assert 'railform-verdict: changelog-gate pass ok' in run.text()
    assert 'VERSION: reads 0.0.9, not 0.1.0' in run.text()
    assert 'railform-verdict: version-sites fail mismatch' in run.text()
    assert results['gate'] == 'failure', run.text()
    # With VERSION kept in step, the same pull request passes.
    (root / 'VERSION').write_text('0.1.0\n', encoding='utf-8')
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'chore: keep VERSION in step')
    run = _run(root, tmp_path, 'pull_request', given, payload, head_ref='chore/drop-a-site')
    assert 'railform-verdict: version-sites pass ok' in run.text()
    assert run.results()['railform/changelog'] == 'success', run.text()
    assert run.results()['gate'] == 'success', run.text()


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


# --- the model refuses what it does not read ---------------------------------------------------


def _model_run(tmp_path: Path, text: str, given: dict[str, str] | None = None) -> WorkflowRun:
    path = tmp_path / 'workflow.yml'
    path.write_text(text, encoding='utf-8')
    runner = Runner(
        root=tmp_path, scratch=tmp_path, github=github_context('push'), given=given or {}
    )
    return runner.run(path)


_ON_TWO_SYSTEMS = """\
on: push
{defaults}
jobs:
  checks:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{{{ matrix.os }}}}
    steps:
      - run: echo ran
"""


def test_a_step_with_no_shell_on_a_windows_leg_fails_the_model(tmp_path: Path) -> None:
    # GitHub runs it in pwsh. The leg not selected and a given result are checked too.
    bare = _ON_TWO_SYSTEMS.format(defaults='')
    with pytest.raises(AssertionError, match='runs in pwsh on windows-latest'):
        _model_run(tmp_path, bare)
    with pytest.raises(AssertionError, match='runs in pwsh on windows-latest'):
        _model_run(tmp_path, bare, {'checks': 'success'})
    declared = _ON_TWO_SYSTEMS.format(defaults='defaults:\n  run:\n    shell: bash')
    assert _model_run(tmp_path, declared).results() == {'checks': 'success'}
    # On a Linux runner a step with no shell runs in `bash -e`, without pipefail, as on GitHub.
    linux = 'on: push\njobs:\n  one:\n    runs-on: ubuntu-latest\n    steps:\n'
    assert _model_run(tmp_path, linux + '      - run: false | true\n').conclusion == 'success'
    declared_step = '      - run: false | true\n        shell: bash\n'
    assert _model_run(tmp_path, linux + declared_step).conclusion == 'failure'


@pytest.mark.parametrize(
    ('where', 'text'),
    [
        ('the workflow', 'concurrency: ci\n'),
        ('job one', '    continue-on-error: true\n'),
        ('job one defaults.run', '    defaults:\n      run:\n        working-directory: src\n'),
        ('job one strategy', '    strategy:\n      max-parallel: 1\n'),
        ('job one step 0', '        continue-on-error: true\n'),
        ('job one step 0', '        working-directory: src\n'),
    ],
)
def test_a_key_the_model_does_not_read_fails_the_run(tmp_path: Path, where: str, text: str) -> None:
    job = '    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    permissions: {}\n'
    step = '    steps:\n      - name: one\n        run: exit 0\n        timeout-minutes: 1\n'
    parts = {'the workflow': 0, 'job one': 1, 'job one step 0': 2}
    blocks = ['name: model\non: push\n', 'jobs:\n  one:\n' + job, step]
    blocks[parts.get(where, 1)] += text
    # The ignored keys alone pass.
    plain = ''.join(['name: model\non: push\n', 'jobs:\n  one:\n' + job, step])
    assert _model_run(tmp_path, plain).conclusion == 'success'
    with pytest.raises(AssertionError, match=f'{where}.*: the model does not read'):
        _model_run(tmp_path, ''.join(blocks))


def test_red_proof_an_unreadable_expression_in_a_uses_step_fails_the_run(tmp_path: Path) -> None:
    # Wave A's review (folded at §13): a `uses:` step runs nothing in the model, but its `with:`
    # is evaluated, so an expression the model cannot read fails here as it would on GitHub.
    head = 'name: model\non: push\njobs:\n  one:\n    runs-on: ubuntu-latest\n    steps:\n'
    readable = '      - uses: actions/checkout@x\n        with:\n          ref: ${{ github.sha }}\n'
    assert _model_run(tmp_path, head + readable).conclusion == 'success'
    unreadable = readable.replace('github.sha', 'hashFiles(github.sha)')
    with pytest.raises(ValueError, match='no function hashFiles'):
        _model_run(tmp_path, head + unreadable)


def test_the_model_runs_bash_from_git_on_windows() -> None:
    # The WSL launcher in System32 is never the shell these tests run.
    path = bash()
    assert path is not None
    assert 'system32' not in path.casefold()
    done = subprocess.run([path, '-c', 'echo $BASH_VERSION'], capture_output=True, text=True)
    assert done.returncode == 0
    assert done.stdout.strip()
