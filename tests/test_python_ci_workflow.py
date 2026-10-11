"""The reusable workflow `.github/workflows/python-ci.yml` has the shape the development-rails
spec, §9, and its round-6 amendment give it.

Ported from the stack-radar engine (https://github.com/grimaldost/stack-radar),
`tests/test_engine_ci.py` at 0a48876: every action is pinned to a 40-hex commit, every setup-uv
step pins one uv version, and no permission writes. The source reads its workflow as text, having
no YAML parser; railform has PyYAML, so the jobs are read as data and the pins as text. Added here:

- the pipeline steps come from the frame (`railform frame --github-output`), never from the
  workflow text;
- `railform-sha` is a required input, and every railform command is installed from it, the
  commit passed through `env:` and checked to be 40-hex before anything is installed;
- the `commit-lane` step that runs `branch-name` declares `shell: bash`, takes `HEAD_REF` from
  `github.head_ref` in its `env:`, puts `github.head_ref` in no `run:` text, and sits under the
  job's pull-request condition;
- every step runs in bash on every system: the workflow's `defaults` declare it, since a step
  that names no shell runs in pwsh on a `windows-latest` leg.

Runs of the workflow through a fixture caller are in `tests/test_python_ci_runs.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
from importlib import resources
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from hook_isolation import hook_environment
from railform import frame
from railform.cli import VERBS
from workflow_model import (
    DECLARED_BASH,
    INSTALL,
    WORKFLOWS,
    Status,
    bash,
    github_context,
    holds,
    interpolate,
    legs,
    load,
    step_shell,
    triggers,
)

if TYPE_CHECKING:
    from pathlib import Path

WORKFLOW = WORKFLOWS / 'python-ci.yml'
JOBS = ('frame', 'checks', 'commit-lane', 'changelog', 'hooks', 'audit')
PR_CONDITION = "github.event_name == 'pull_request'"
_USES = re.compile(r'^\s*(?:-\s+)?uses:\s*(\S+)', re.MULTILINE)
_SHA = re.compile(r'[0-9a-f]{40}')
# The commands of the type's pipeline, which only the frame may write.
PIPELINE_TOOLS = re.compile(r'\b(?:ruff|pytest|ty\s+check|uv\s+sync|uv\s+lock|uv\s+run|uv\s+pip)\b')


@pytest.fixture(scope='module')
def workflow() -> dict[Any, Any]:
    return load(WORKFLOW)


def _job(workflow: dict[Any, Any], name: str) -> dict[str, Any]:
    return workflow['jobs'][name]


def _runs(job: dict[str, Any]) -> list[str]:
    return [step['run'] for step in job['steps'] if 'run' in step]


def _step(job: dict[str, Any], text: str) -> dict[str, Any]:
    found = [step for step in job['steps'] if text in step.get('run', '')]
    assert len(found) == 1, (text, found)
    return found[0]


def _all_runs(workflow: dict[Any, Any]) -> list[str]:
    return [run for job in workflow['jobs'].values() for run in _runs(job)]


# --- ported: the jobs, the pins and the permissions --------------------------------------------


def test_the_workflow_has_the_jobs_of_the_invocation_table(workflow: dict[Any, Any]) -> None:
    # Non-vacuity: every assertion below reads a job by name.
    assert tuple(workflow['jobs']) == JOBS


def test_it_runs_only_when_called_never_on_pull_request_target(workflow: dict[Any, Any]) -> None:
    assert set(triggers(workflow)) == {'workflow_call'}
    assert 'pull_request_target' not in WORKFLOW.read_text(encoding='utf-8')


def test_railform_sha_is_a_required_string_input(workflow: dict[Any, Any]) -> None:
    inputs = triggers(workflow)['workflow_call']['inputs']
    assert set(inputs) == {'railform-sha'}
    assert inputs['railform-sha']['required'] is True
    assert inputs['railform-sha']['type'] == 'string'


def test_every_action_is_pinned_to_a_commit() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    refs = _USES.findall(text)
    assert refs, 'no `uses:` line found'
    for uses in refs:
        name, _, ref = uses.partition('@')
        assert _SHA.fullmatch(ref), uses
        assert re.fullmatch(r'[\w.-]+/[\w./-]+', name), uses
    # Each pin names its release in a `# vX.Y.Z` comment, which Dependabot updates with it.
    lines = [line for line in text.splitlines() if _USES.match(line)]
    assert len(lines) == len(refs)
    for line in lines:
        assert re.search(r'# v\d+(\.\d+)*\s*$', line), line


def test_every_setup_uv_step_pins_one_uv_version(workflow: dict[Any, Any]) -> None:
    # Without a `version:` input, setup-uv installs whatever uv release is current at run time,
    # so every later uv step drifts without a commit. Each setup-uv step pins the same one.
    steps = [
        step
        for job in workflow['jobs'].values()
        for step in job['steps']
        if step.get('uses', '').startswith('astral-sh/setup-uv@')
    ]
    assert steps
    versions = {str((step.get('with') or {}).get('version', '')) for step in steps}
    assert len(versions) == 1, versions
    assert re.fullmatch(r'\d+\.\d+\.\d+', versions.pop())


def test_red_proof_every_job_has_a_time_limit(workflow: dict[Any, Any]) -> None:
    # Wave A's review (folded at §13): a hung step would otherwise hold a runner for GitHub's
    # six-hour default before `gate` could report.
    for name, job in workflow['jobs'].items():
        limit = job.get('timeout-minutes')
        assert isinstance(limit, int) and 0 < limit <= 60, (name, limit)


def test_red_proof_only_the_checks_job_caches_uv(workflow: dict[Any, Any]) -> None:
    # Wave A's review (folded at §13): only `checks` syncs the caller's lock; the other jobs
    # install railform or a pinned tool, and a cache saved from them buys nothing.
    for name, job in workflow['jobs'].items():
        for step in job['steps']:
            if not step.get('uses', '').startswith('astral-sh/setup-uv@'):
                continue
            enabled = (step.get('with') or {}).get('enable-cache', 'auto')
            assert enabled == ('auto' if name == 'checks' else False), (name, enabled)


def test_no_permission_writes(workflow: dict[Any, Any]) -> None:
    assert workflow['permissions'] == {'contents': 'read'}
    for name, job in workflow['jobs'].items():
        block = job.get('permissions')
        assert block is None or 'write' not in block.values(), (name, block)
    # Nor anywhere else in the text: no `contents: write`, no `permissions: write-all`.
    assert not re.search(r':\s*write\b', WORKFLOW.read_text(encoding='utf-8'))


def test_checkout_does_not_persist_credentials(workflow: dict[Any, Any]) -> None:
    checkouts = [
        step
        for job in workflow['jobs'].values()
        for step in job['steps']
        if step.get('uses', '').startswith('actions/checkout@')
    ]
    assert len(checkouts) == 6
    assert all(step['with']['persist-credentials'] is False for step in checkouts)


@pytest.mark.parametrize('name', ['commit-lane', 'changelog', 'hooks'])
def test_the_jobs_that_read_history_check_out_all_of_it(
    workflow: dict[Any, Any], name: str
) -> None:
    # The commit range, the changelog gate and the changed files need the base branch's history.
    checkout = next(
        step
        for step in _job(workflow, name)['steps']
        if step.get('uses', '').startswith('actions/checkout@')
    )
    assert checkout['with']['fetch-depth'] == 0


# --- the pin is always an input ----------------------------------------------------------------


def test_every_railform_command_is_installed_from_the_input(workflow: dict[Any, Any]) -> None:
    assert workflow['env'] == {'RAILFORM_SHA': '${{ inputs.railform-sha }}'}
    commands = 0
    for run in _all_runs(workflow):
        for verb in VERBS:
            invoked = len(re.findall(rf'\brailform {re.escape(verb)}\b', run))
            installed = run.count(f'{INSTALL} {verb}')
            assert invoked == installed, (verb, run)
            commands += invoked
    assert commands == 9


def test_no_run_text_interpolates_an_expression(workflow: dict[Any, Any]) -> None:
    # Values reach the scripts through `env:` only, so no branch name, title or frame value is
    # ever pasted into a script.
    runs = _all_runs(workflow)
    assert runs
    for run in runs:
        assert '${{' not in run, run


def test_every_job_that_installs_railform_follows_the_frame_job(workflow: dict[Any, Any]) -> None:
    # The frame job checks the pin before anything is installed from it.
    for name, job in workflow['jobs'].items():
        if name == 'frame':
            continue
        if any(INSTALL in run for run in _runs(job)) or name == 'changelog':
            assert job['needs'] == 'frame', name
        else:
            assert 'needs' not in job, name


def test_the_frame_job_checks_the_pin_then_the_frame_then_exports_it(
    workflow: dict[Any, Any],
) -> None:
    job = _job(workflow, 'frame')
    runs = _runs(job)
    assert '$RAILFORM_SHA' in runs[0] and '[0-9a-f]{40}' in runs[0]
    assert runs[1:] == [f'{INSTALL} frame --check', f'{INSTALL} frame --github-output']
    assert _step(job, 'frame --github-output')['id'] == 'frame'
    names = ('ci_os', 'ci_setup', 'python_versions', 'pipeline', 'hooks_python')
    assert job['outputs'] == {name: f'${{{{ steps.frame.outputs.{name} }}}}' for name in names}
    # Each output the job passes on is one `frame --github-output` writes.
    written = {name for name, _ in frame.github_outputs(frame.effective_frame(None))}
    assert set(names) <= written


@pytest.mark.skipif(bash() is None, reason='no bash on this machine')
@pytest.mark.parametrize(
    ('value', 'passes'),
    [
        ('0123456789abcdef0123456789abcdef01234567', True),
        ('main', False),
        ('', False),
        ('0123456789ABCDEF0123456789ABCDEF01234567', False),
        ('0123456789abcdef0123456789abcdef0123456', False),
        ('0123456789abcdef0123456789abcdef01234567\nx', False),
    ],
)
def test_the_pin_check_passes_only_a_full_commit(
    workflow: dict[Any, Any], value: str, passes: bool
) -> None:
    script = _runs(_job(workflow, 'frame'))[0]
    executable = bash()
    assert executable is not None
    done = subprocess.run(
        [executable, '--noprofile', '--norc', '-eo', 'pipefail', '-c', script],
        env={**os.environ, 'RAILFORM_SHA': value},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode == 0) == passes, done.stdout + done.stderr


# --- the pipeline comes from the frame ---------------------------------------------------------


def test_no_run_text_writes_a_pipeline_step(workflow: dict[Any, Any]) -> None:
    for run in _all_runs(workflow):
        assert not PIPELINE_TOOLS.search(run), run


def test_the_checks_job_runs_the_frame_pipeline_on_the_frame_matrix(
    workflow: dict[Any, Any],
) -> None:
    job = _job(workflow, 'checks')
    assert job['needs'] == 'frame'
    assert job['strategy']['fail-fast'] is False
    assert job['strategy']['matrix'] == {
        'os': '${{ fromJSON(needs.frame.outputs.ci_os) }}',
        'python': '${{ fromJSON(needs.frame.outputs.python_versions) }}',
    }
    assert job['runs-on'] == '${{ matrix.os }}'
    assert job['env'] == {'UV_PYTHON': '${{ matrix.python }}'}
    checkout = job['steps'][0]
    assert (
        checkout['with']['fetch-depth']
        == '${{ fromJSON(needs.frame.outputs.ci_setup).fetch_depth }}'
    )
    runs = _runs(job)
    assert runs[0] == f'{INSTALL} private-refs'
    assert runs[1] == f'{INSTALL} version-sites'
    # The step has no condition of its own, so it runs on every leg, on pull requests and pushes.
    assert set(_step(job, f'{INSTALL} version-sites')) == {'name', 'run'}
    assert len(runs) == 3
    assert _step(job, 'PIPELINE')['env'] == {'PIPELINE': '${{ needs.frame.outputs.pipeline }}'}


def test_every_step_runs_in_bash_on_every_system(workflow: dict[Any, Any]) -> None:
    # The frame's commands are bash. A step that names no shell runs in pwsh on a windows-latest
    # leg, so the workflow declares bash for every step, and no job or step names another shell.
    assert workflow['defaults'] == {'run': {'shell': 'bash'}}
    for name, job in workflow['jobs'].items():
        assert 'defaults' not in job, name
        for step in job['steps']:
            assert step.get('shell', 'bash') == 'bash', (name, step)


@pytest.mark.parametrize('project_type', ['python-package', 'python-scripts'])
def test_the_model_runs_every_step_in_bash_on_every_leg_of_the_preset_matrix(
    workflow: dict[Any, Any], project_type: str
) -> None:
    # The model resolves each step's shell from its leg as GitHub does, and refuses pwsh.
    outputs = dict(frame.github_outputs(frame.effective_frame({'project': {'type': project_type}})))
    context = {'needs': {'frame': {'outputs': outputs}}}
    job = _job(workflow, 'checks')
    all_legs = legs(job, context)
    assert {leg['os'] for leg in all_legs} == {'ubuntu-latest', 'windows-latest'}
    shell = workflow['defaults']['run']['shell']
    for leg in all_legs:
        runs_on = interpolate(job['runs-on'], {**context, 'matrix': leg})
        for step in job['steps']:
            if 'run' in step:
                assert step_shell(step, job, shell, runs_on) == DECLARED_BASH, (leg, step)


def _pipeline_step(workflow: dict[Any, Any], pipeline: str) -> subprocess.CompletedProcess[str]:
    script = _step(_job(workflow, 'checks'), 'PIPELINE')['run']
    executable = bash()
    assert executable is not None
    return subprocess.run(
        [executable, '--noprofile', '--norc', '-eo', 'pipefail', '-c', script],
        env={**os.environ, 'PIPELINE': pipeline},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(bash() is None, reason='no bash on this machine')
def test_the_pipeline_step_runs_each_line_in_order_and_stops_at_the_first_failure(
    workflow: dict[Any, Any],
) -> None:
    passed = _pipeline_step(workflow, "echo one\n\necho 'two words'")
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.splitlines() == [
        '::group::echo one',
        'one',
        '::endgroup::',
        "::group::echo 'two words'",
        'two words',
        '::endgroup::',
    ]
    failed = _pipeline_step(workflow, 'echo first\nexit 3\necho never')
    assert failed.returncode == 3
    assert 'first' in failed.stdout
    assert 'never' not in failed.stdout.replace('::group::echo never', '')
    assert '::group::echo never' not in failed.stdout


@pytest.mark.skipif(bash() is None, reason='no bash on this machine')
def test_the_pipeline_lines_share_one_shell(workflow: dict[Any, Any]) -> None:
    # The development-rails spec, §13 (a pilot): a test command that installs a tool and puts it
    # on PATH on one line uses it on the next, because the lines run in one bash shell. An
    # export, a shell variable and the working directory all carry over.
    lines = 'export RAILFORM_ONE=1\nTWO=2\ncd ..\ntest "$RAILFORM_ONE" = 1\ntest "$TWO" = 2\npwd'
    done = _pipeline_step(workflow, lines)
    assert done.returncode == 0, done.stdout + done.stderr
    from_frame = frame.pipeline(
        frame.effective_frame({'python': {'test_command': 'export X=1\ntest "$X" = 1'}})
    )
    assert from_frame[-2:] == ['export X=1', 'test "$X" = 1']
    done = _pipeline_step(workflow, '\n'.join(from_frame[-2:]))
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(bash() is None, reason='no bash on this machine')
def test_a_pipeline_step_cannot_read_the_remaining_steps(workflow: dict[Any, Any]) -> None:
    # A command that reads standard input must not swallow the steps after it.
    done = _pipeline_step(workflow, 'cat\necho after')
    assert done.returncode == 0, done.stderr
    assert 'after' in done.stdout.splitlines()


# --- commit-lane -------------------------------------------------------------------------------


def test_the_commit_lane_runs_on_pull_requests_only(workflow: dict[Any, Any]) -> None:
    job = _job(workflow, 'commit-lane')
    assert job['if'] == PR_CONDITION
    for event, runs in (('pull_request', True), ('push', False)):
        context = {'github': github_context(event)}
        assert holds(job['if'], context, Status()) is runs, event


def test_the_commit_lane_checks_the_range_the_text_and_the_branch(
    workflow: dict[Any, Any],
) -> None:
    job = _job(workflow, 'commit-lane')
    assert _runs(job) == [
        f'{INSTALL} commit-range "origin/${{BASE_REF}}...HEAD"',
        f'{INSTALL} pr-text',
        f'{INSTALL} branch-name -- "$HEAD_REF"',
    ]
    assert _step(job, 'commit-range')['env'] == {'BASE_REF': '${{ github.base_ref }}'}


def test_red_proof_the_branch_name_step_takes_the_head_branch_through_the_environment(
    workflow: dict[Any, Any],
) -> None:
    # The round-6 amendment: whoever pushed the branch chose its name, so the name reaches the
    # command through `env:` and is never interpolated into the script.
    job = _job(workflow, 'commit-lane')
    step = _step(job, 'branch-name')
    assert step['shell'] == 'bash'
    assert step['env'] == {'HEAD_REF': '${{ github.head_ref }}'}
    assert step['run'] == f'{INSTALL} branch-name -- "$HEAD_REF"'
    assert 'if' not in step  # it runs under the job's condition, nothing narrower
    assert job['if'] == PR_CONDITION
    for run in _all_runs(workflow):
        assert 'head_ref' not in run, run


# --- changelog, hooks, audit -------------------------------------------------------------------


def test_the_changelog_job_runs_the_gate_on_pull_requests_only(workflow: dict[Any, Any]) -> None:
    # The development-rails spec, §14, and the Wave C contract, section 6.1: the placeholder's
    # `false &&` is gone, and the job runs the gate over the pull request's range.
    job = _job(workflow, 'changelog')
    assert job['if'] == PR_CONDITION
    for event, runs in (('pull_request', True), ('push', False)):
        context = {'github': github_context(event)}
        assert holds(job['if'], context, Status()) is runs, event
    assert job['needs'] == 'frame'
    assert _runs(job) == [f'{INSTALL} changelog-gate "origin/${{BASE_REF}}...HEAD"']
    step = _step(job, 'changelog-gate')
    assert step['name'] == 'railform changelog-gate'
    assert step['env'] == {'BASE_REF': '${{ github.base_ref }}'}
    assert 'if' not in step  # it runs under the job's condition, nothing narrower


def _hygiene_ids() -> list[str]:
    text = (resources.files('railform') / 'templates' / 'pre-commit-hygiene.yaml').read_text(
        encoding='utf-8'
    )
    return [hook['id'] for hook in yaml.safe_load(text)[0]['hooks']]


def test_the_hooks_job_checks_the_wrappers_and_the_owned_parts(workflow: dict[Any, Any]) -> None:
    job = _job(workflow, 'hooks')
    runs = _runs(job)
    assert runs[:2] == [f'{INSTALL} hooks-check', f'{INSTALL} adopt --check']
    assert len(runs) == 3


def test_the_hooks_job_replays_the_hygiene_hooks_over_the_changed_files_only(
    workflow: dict[Any, Any],
) -> None:
    step = _step(_job(workflow, 'hooks'), 'pre-commit')
    assert step['if'] == PR_CONDITION
    assert step['env']['BASE_REF'] == '${{ github.base_ref }}'
    script = step['run']
    assert '--all-files' not in script
    assert '--from-ref "origin/${BASE_REF}" --to-ref HEAD' in script
    # The hooks it replays are the ones `railform adopt` writes in the hygiene entry.
    listed = re.search(r'for hook in ([^;]+);', script)
    assert listed, script
    assert listed[1].replace('\\', ' ').split() == _hygiene_ids()
    # One pre-commit version for the replay and for the wrapper that runs it through uvx.
    pinned = frame.preset_frame('plugin-marketplace')['hooks']['pre_commit_version']
    assert f'uvx --python "$HOOKS_PYTHON" pre-commit=={pinned} run "$hook"' in script


def test_red_proof_the_hooks_job_runs_pre_commit_on_the_frames_newest_python(
    workflow: dict[Any, Any],
) -> None:
    # The development-rails spec, §13 (the craft-collection pilot): pre-commit ran on the
    # runner's own Python, so a `default_language_version` naming the repository's Python had no
    # interpreter. The replay now runs pre-commit on the newest of `python.versions`.
    step = _step(_job(workflow, 'hooks'), 'pre-commit')
    assert step['env'] == {
        'BASE_REF': '${{ github.base_ref }}',
        'HOOKS_PYTHON': '${{ needs.frame.outputs.hooks_python }}',
    }
    assert _job(workflow, 'frame')['outputs']['hooks_python'] == (
        '${{ steps.frame.outputs.hooks_python }}'
    )


@pytest.mark.parametrize(
    ('versions', 'expected'),
    [
        (['3.13'], '3.13'),
        (['3.9', '3.10'], '3.10'),
        (['3.11', '3.14', '3.12'], '3.14'),
        ([], '3.14'),
    ],
)
def test_hooks_python_is_the_newest_listed_version(versions: list[str], expected: str) -> None:
    effective = frame.effective_frame({'python': {'versions': versions}})
    assert frame.hooks_python(effective) == expected
    assert ('hooks_python', expected) in frame.github_outputs(effective)


@pytest.mark.skipif(bash() is None, reason='no bash on this machine')
def test_the_replay_passes_the_frames_python_to_uvx(
    workflow: dict[Any, Any], tmp_path: Path
) -> None:
    # A stand-in `uvx`, a shell function, records its arguments, so the replay's real script
    # runs offline.
    script = _step(_job(workflow, 'hooks'), 'pre-commit')['run']
    record = tmp_path / 'calls.txt'
    executable = bash()
    assert executable is not None
    prefix = 'uvx() { echo "$*" >> "$RECORD"; }\n'
    done = subprocess.run(
        [executable, '--noprofile', '--norc', '-eo', 'pipefail', '-c', prefix + script],
        env={**os.environ, 'BASE_REF': 'main', 'HOOKS_PYTHON': '3.13', 'RECORD': str(record)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    calls = record.read_text(encoding='utf-8').splitlines()
    assert len(calls) == len(_hygiene_ids())
    assert all(call.startswith('--python 3.13 pre-commit==') for call in calls), calls


_IDENT = ('-c', 'user.name=someone', '-c', 'user.email=someone@example.com')


def _git(root: Path, *args: str) -> None:
    subprocess.run(['git', *_IDENT, *args], cwd=root, check=True, capture_output=True)


@pytest.mark.slow
@pytest.mark.skipif(bash() is None, reason='no bash on this machine')
def test_red_proof_the_hygiene_replay_fails_a_changed_file_with_trailing_whitespace(
    workflow: dict[Any, Any], scratch_repo: Path, outside: dict[str, str]
) -> None:
    # Needs the network: pre-commit installs the hygiene hooks from their repository, into the
    # session's own pre-commit home, away from the developer's (`tests/hook_isolation.py`).
    script = _step(_job(workflow, 'hooks'), 'pre-commit')['run']
    hygiene = (resources.files('railform') / 'templates' / 'pre-commit-hygiene.yaml').read_text(
        encoding='utf-8'
    )
    config = 'repos:\n' + ''.join(f'  {line}\n' for line in hygiene.splitlines())
    (scratch_repo / '.pre-commit-config.yaml').write_text(config, encoding='utf-8')
    # A file the pull request does not change may break a hook: only changed files are judged.
    (scratch_repo / 'old.txt').write_text('trailing \n', encoding='utf-8')
    _git(scratch_repo, 'add', '-A')
    _git(scratch_repo, 'commit', '-q', '-m', 'chore: the base')
    _git(scratch_repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    executable = bash()
    assert executable is not None
    env = hook_environment(outside)

    def replay() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [executable, '--noprofile', '--norc', '-eo', 'pipefail', '-c', script],
            cwd=scratch_repo,
            env={**env, 'BASE_REF': 'main'},
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=900,
            check=False,
        )

    (scratch_repo / 'clean.txt').write_text('clean\n', encoding='utf-8')
    _git(scratch_repo, 'add', '-A')
    _git(scratch_repo, 'commit', '-q', '-m', 'docs: add a clean note')
    passed = replay()
    assert passed.returncode == 0, passed.stdout[-3000:] + passed.stderr[-3000:]
    (scratch_repo / 'notes.txt').write_text('a line with a trailing space \n', encoding='utf-8')
    _git(scratch_repo, 'add', '-A')
    _git(scratch_repo, 'commit', '-q', '-m', 'docs: add a note')
    failed = replay()
    assert failed.returncode == 1, failed.stdout[-3000:] + failed.stderr[-3000:]
    assert 'notes.txt' in failed.stdout
    assert 'old.txt' not in failed.stdout


def test_the_audit_job_runs_zizmor_at_a_pinned_version(workflow: dict[Any, Any]) -> None:
    job = _job(workflow, 'audit')
    assert _runs(job) == ['uvx zizmor==1.30.0 --min-severity high .github/workflows']
    assert 'if' not in job


def test_every_job_runs_on_every_pull_request_event(workflow: dict[Any, Any]) -> None:
    # An edit of the title or body re-runs everything: a run that skipped the checks would post
    # a green gate over a red one on the same commit.
    edited = {'github': {**github_context('pull_request'), 'event': {'action': 'edited'}}}
    for name in ('frame', 'checks', 'hooks', 'audit', 'commit-lane'):
        assert holds(_job(workflow, name).get('if'), edited, Status()), name
