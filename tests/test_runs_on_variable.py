"""Every Linux job routes by the repository variable `LINUX_RUNS_ON`.

A repository that sets the variable to a JSON array of runner labels, such as
`["self-hosted","linux","example"]`, runs its Linux jobs on a runner with those labels; with the
variable unset, every job runs on `ubuntu-latest` or on its matrix value, as before. A run whose
actor is Dependabot, and a run on a pull request Dependabot opened, whoever started the run, stay
on GitHub's runners: `github.actor` names the actor of a run's first attempt, so a run someone
else starts on a Dependabot pull request (an edited title, a reopen) is caught by the author.

Every job that runs on a runner of its own takes one of two expressions, exactly: the Linux form,
or the matrix form for a matrix with Windows legs, which sends every leg that is not `ubuntu*` to
its `matrix.os`. The jobs are those of the two reusable workflows, the gate job of adopt's
template, and railform's own `ci.yml`, whose owned region `railform adopt` writes from that
template. Each `runs-on` is evaluated through the workflow model (`tests/workflow_model.py`) in
four cases: the variable unset, the variable set, the actor Dependabot, and a pull request whose
author is Dependabot.

Two rules go with the variable. A job that reads it holds no `write` permission, no `id-token`
and no `secrets.` reference, so a runner the variable names never holds a credential that writes.
And every `setup-uv` step sets `enable-cache: false`, written out, so no run restores a cache
another run saved.
"""

from __future__ import annotations

import copy
import json
from importlib import resources
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from railform import frame
from workflow_model import WORKFLOWS, Runner, bash, github_context, load, runners

if TYPE_CHECKING:
    from pathlib import Path

VARIABLE = 'LINUX_RUNS_ON'
LINUX = (
    "${{ github.actor != 'dependabot[bot]' && "
    "github.event.pull_request.user.login != 'dependabot[bot]' && "
    "fromJSON(vars.LINUX_RUNS_ON || '\"ubuntu-latest\"') || 'ubuntu-latest' }}"
)
MATRIX = (
    "${{ startsWith(matrix.os, 'ubuntu') && github.actor != 'dependabot[bot]' && "
    "github.event.pull_request.user.login != 'dependabot[bot]' && "
    'fromJSON(vars.LINUX_RUNS_ON || \'"ubuntu-latest"\') || matrix.os }}'
)
# A neutral label array: what an adopter would set the variable to.
LABELS = ['self-hosted', 'linux', 'example']
DEPENDABOT = 'dependabot[bot]'
SOMEONE = 'alice'

TEMPLATE = 'ci-jobs.yml'
# Every job with a runner of its own, by source, and the form it takes.
JOBS = {
    'python-ci.yml': {
        'frame': LINUX,
        'checks': MATRIX,
        'commit-lane': LINUX,
        'changelog': LINUX,
        'hooks': LINUX,
        'audit': LINUX,
    },
    'tag-watch.yml': {'release-check': LINUX},
    TEMPLATE: {'gate': LINUX},
    'ci.yml': {'gate': LINUX},
}


def _template() -> dict[str, Any]:
    """Adopt's template of the owned jobs, as a workflow: its jobs, and no workflow-level key."""
    text = resources.files('railform').joinpath('templates', TEMPLATE).read_text(encoding='utf-8')
    return {'jobs': yaml.safe_load(text)}


def _source(name: str) -> dict[str, Any]:
    return _template() if name == TEMPLATE else load(WORKFLOWS / name)


SOURCES = tuple(JOBS)


@pytest.fixture(scope='module')
def needs() -> dict[str, Any]:
    """What `checks` needs: the `frame` job's outputs for the `python-package` preset, whose
    matrix has an Ubuntu and a Windows leg."""
    outputs = dict(
        frame.github_outputs(frame.effective_frame({'project': {'type': 'python-package'}}))
    )
    assert {'ubuntu-latest', 'windows-latest'} <= set(json.loads(outputs['ci_os']))
    return {'frame': {'result': 'success', 'outputs': outputs}}


# --- the expression is on every job ------------------------------------------------------------


def routing_faults(workflow: dict[str, Any], expected: dict[str, str]) -> list[str]:
    """The jobs whose `runs-on` is not the expression `expected` gives them, and any job with a
    runner of its own that `expected` does not name."""
    faults = []
    for name, job in workflow['jobs'].items():
        if 'runs-on' not in job:
            assert 'uses' in job, name
            continue
        if name not in expected:
            faults.append(f'{name}: a job with a runner that the routing does not cover')
        elif job['runs-on'] != expected[name]:
            faults.append(f'{name}: runs-on is {job["runs-on"]!r}')
    faults += [f'{name}: no such job' for name in expected if name not in workflow['jobs']]
    return faults


@pytest.mark.parametrize('source', SOURCES)
def test_every_job_takes_the_routing_expression(source: str) -> None:
    assert routing_faults(_source(source), JOBS[source]) == []


def test_red_proof_a_literal_runner_or_an_uncovered_job_is_refused() -> None:
    workflow = _source('python-ci.yml')
    literal = copy.deepcopy(workflow)
    literal['jobs']['frame']['runs-on'] = 'ubuntu-latest'
    assert routing_faults(literal, JOBS['python-ci.yml']) == ["frame: runs-on is 'ubuntu-latest'"]
    # The Linux form on a matrix job would send its Windows legs to Linux.
    linux_form = copy.deepcopy(workflow)
    linux_form['jobs']['checks']['runs-on'] = LINUX
    assert routing_faults(linux_form, JOBS['python-ci.yml']) == [f'checks: runs-on is {LINUX!r}']
    added = copy.deepcopy(workflow)
    added['jobs']['late'] = {'runs-on': LINUX, 'steps': []}
    assert routing_faults(added, JOBS['python-ci.yml']) == [
        'late: a job with a runner that the routing does not cover'
    ]


# --- the four cases, through the model ---------------------------------------------------------


def _github(event: str, actor: str, author: str | None) -> dict[str, Any]:
    if event == 'pull_request':
        return github_context(event, actor=actor, author=author)
    return github_context(event, actor=actor)


# Each case: the event, the actor, the pull request's author, the variable's value (None when
# unset), and where the Linux jobs run: on GitHub's runners or on the label array.
CASES = [
    pytest.param('push', SOMEONE, None, None, 'github', id='unset-push'),
    pytest.param('pull_request', SOMEONE, SOMEONE, None, 'github', id='unset-pull-request'),
    pytest.param('push', SOMEONE, None, '', 'github', id='empty-push'),
    pytest.param('push', SOMEONE, None, json.dumps(LABELS), 'labels', id='set-push'),
    pytest.param(
        'pull_request', SOMEONE, SOMEONE, json.dumps(LABELS), 'labels', id='set-pull-request'
    ),
    pytest.param('push', DEPENDABOT, None, json.dumps(LABELS), 'github', id='dependabot-push'),
    pytest.param(
        'pull_request',
        DEPENDABOT,
        DEPENDABOT,
        json.dumps(LABELS),
        'github',
        id='dependabot-pull-request',
    ),
    pytest.param(
        'pull_request',
        SOMEONE,
        DEPENDABOT,
        json.dumps(LABELS),
        'github',
        id='someone-on-a-dependabot-pull-request',
    ),
]


def evaluated(
    workflow: dict[str, Any], runner: Runner, needs: dict[str, Any]
) -> dict[str, list[tuple[dict[str, Any], object]]]:
    """Each job with a runner of its own, and each of its legs with the runner it names in
    `runner`'s run."""
    context = {**runner.context(), 'needs': needs}
    return {
        name: runners(job, context) for name, job in workflow['jobs'].items() if 'runs-on' in job
    }


@pytest.mark.parametrize('source', SOURCES)
@pytest.mark.parametrize(('event', 'actor', 'author', 'value', 'where'), CASES)
def test_the_runner_each_job_takes(
    source: str,
    event: str,
    actor: str,
    author: str | None,
    value: str | None,
    where: str,
    needs: dict[str, Any],
    tmp_path: Path,
) -> None:
    variables = {} if value is None else {VARIABLE: value}
    runner = Runner(tmp_path, tmp_path, _github(event, actor, author), vars=variables)
    found = evaluated(_source(source), runner, needs)
    assert set(found) == set(JOBS[source])
    for name, job_legs in found.items():
        for leg, runs_on in job_legs:
            if 'os' in leg and not leg['os'].startswith('ubuntu'):
                # A Windows leg runs on its matrix value in every case.
                assert runs_on == leg['os'], (name, leg)
            elif where == 'labels':
                assert runs_on == LABELS, (name, leg)
            else:
                assert runs_on == leg.get('os', 'ubuntu-latest'), (name, leg)
    legs = [leg for job_legs in found.values() for leg, _ in job_legs]
    if source == 'python-ci.yml':
        # The matrix job's legs are all there, Windows included.
        assert {leg.get('os') for leg in legs} == {None, 'ubuntu-latest', 'windows-latest'}


def test_a_run_through_the_model_takes_the_variable(tmp_path: Path) -> None:
    # The variable reaches a whole run: the gate of railform's own ci.yml, its needed job given,
    # runs on the label array, whose labels name no Windows runner, so its step's shell is bash.
    if bash() is None:
        pytest.skip('no bash on this machine')
    runner = Runner(
        tmp_path,
        tmp_path,
        github_context('pull_request', actor=SOMEONE, author=SOMEONE),
        given={'railform': 'success'},
        vars={VARIABLE: json.dumps(LABELS)},
    )
    done = runner.run(WORKFLOWS / 'ci.yml')
    assert done.results() == {'railform': 'success', 'gate': 'success'}, done.text()


# --- what a job that reads the variable may hold -----------------------------------------------


def credential_faults(workflow: dict[str, Any]) -> list[str]:
    """The jobs that read `LINUX_RUNS_ON` and hold a `write` permission, `id-token`, or a
    `secrets` reference. A job with no `permissions:` takes the workflow's, and with neither it
    takes the repository's default, which may write."""
    faults = []
    for name, job in workflow['jobs'].items():
        text = json.dumps(job)
        if VARIABLE not in text:
            continue
        permissions = job.get('permissions', workflow.get('permissions'))
        if permissions is None:
            faults.append(f'{name}: no permissions declared')
        elif isinstance(permissions, str):
            if permissions != 'read-all':
                faults.append(f'{name}: permissions {permissions}')
        else:
            faults += [
                f'{name}: {scope}: {level}'
                for scope, level in permissions.items()
                if level == 'write' or (scope == 'id-token' and level != 'none')
            ]
        if 'secrets.' in text or 'secrets[' in text or 'secrets' in job:
            faults.append(f'{name}: a secrets reference')
    return faults


@pytest.mark.parametrize('source', SOURCES)
def test_no_job_that_reads_the_variable_holds_a_credential(source: str) -> None:
    assert credential_faults(_source(source)) == []


@pytest.mark.parametrize(
    ('edit', 'fault'),
    [
        pytest.param(
            lambda w: w['jobs']['audit'].update(permissions={'contents': 'write'}),
            'audit: contents: write',
            id='write',
        ),
        pytest.param(
            lambda w: w['jobs']['frame'].update(permissions={'id-token': 'write'}),
            'frame: id-token: write',
            id='id-token',
        ),
        pytest.param(
            lambda w: (
                w['jobs']['hooks'].setdefault('env', {}).update(PUBLISH='${{ secrets.PUBLISH }}')
            ),
            'hooks: a secrets reference',
            id='secrets',
        ),
        pytest.param(
            lambda w: w['jobs']['frame'].update(permissions='write-all'),
            'frame: permissions write-all',
            id='write-all',
        ),
        pytest.param(
            lambda w: w.pop('permissions'),
            'frame: no permissions declared',
            id='none-declared',
        ),
    ],
)
def test_red_proof_a_job_that_reads_the_variable_and_holds_a_credential_is_refused(
    edit: Any, fault: str
) -> None:
    workflow = _source('python-ci.yml')
    edit(workflow)
    assert fault in credential_faults(workflow)


def test_a_job_that_does_not_read_the_variable_is_not_judged() -> None:
    workflow = _source('python-ci.yml')
    workflow['jobs']['frame']['runs-on'] = 'ubuntu-latest'
    workflow['jobs']['frame']['permissions'] = {'contents': 'write'}
    assert credential_faults(workflow) == []


# --- the cache is off on every setup-uv --------------------------------------------------------


def caching_setups(workflow: dict[str, Any]) -> list[str]:
    """The jobs with a `setup-uv` step that does not set `enable-cache: false`."""
    return [
        name
        for name, job in workflow['jobs'].items()
        for step in job.get('steps') or []
        if step.get('uses', '').startswith('astral-sh/setup-uv@')
        and (step.get('with') or {}).get('enable-cache') is not False
    ]


def _setups(workflow: dict[str, Any]) -> int:
    return sum(
        step.get('uses', '').startswith('astral-sh/setup-uv@')
        for job in workflow['jobs'].values()
        for step in job.get('steps') or []
    )


@pytest.mark.parametrize('source', SOURCES)
def test_no_setup_uv_keeps_a_cache(source: str) -> None:
    assert caching_setups(_source(source)) == []


def test_both_reusable_workflows_set_up_uv() -> None:
    # Six jobs in python-ci.yml and one in tag-watch.yml, so the rule above judges something.
    assert _setups(_source('python-ci.yml')) == 6
    assert _setups(_source('tag-watch.yml')) == 1


@pytest.mark.parametrize('value', [None, True, 'false', 'auto'], ids=repr)
def test_red_proof_a_setup_uv_that_may_cache_is_refused(value: object) -> None:
    workflow = _source('python-ci.yml')
    setup = next(
        step
        for step in workflow['jobs']['checks']['steps']
        if step.get('uses', '').startswith('astral-sh/setup-uv@')
    )
    if value is None:
        setup['with'].pop('enable-cache', None)
    else:
        setup['with']['enable-cache'] = value
    assert caching_setups(workflow) == ['checks']
