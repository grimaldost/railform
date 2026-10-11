"""The reusable `.github/workflows/tag-watch.yml` and railform's own caller of it
(development-rails spec, §16; interface contract, Wave C, sections 6.3 and 6.4).

The ported checks of `tests/test_python_ci_workflow.py` (from the stack-radar engine,
https://github.com/grimaldost/stack-radar, `tests/test_engine_ci.py` at 0a48876) extended to this
file: every action pinned to a 40-hex commit, one uv version, no permission that writes. Added
here: the pin is a required input checked before anything is installed, the checkout takes the
whole history and, on a tag push, the default branch, and the token reaches `release-check`
through `env:` only.

Runs of a fixture caller through this workflow are in `tests/test_release_check.py`.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from railform import adopt
from workflow_model import INSTALL, WORKFLOWS, interpolate, load, triggers

WORKFLOW = WORKFLOWS / 'tag-watch.yml'
PYTHON_CI = WORKFLOWS / 'python-ci.yml'
OWN_CALLER = WORKFLOWS / 'railform-tag-watch.yml'
_USES = re.compile(r'^\s*(?:-\s+)?uses:\s*(\S+)', re.MULTILINE)
_SHA = re.compile(r'[0-9a-f]{40}')


@pytest.fixture(scope='module')
def workflow() -> dict[Any, Any]:
    return load(WORKFLOW)


def _steps(workflow: dict[Any, Any]) -> list[dict[str, Any]]:
    return workflow['jobs']['release-check']['steps']


def _uses(workflow: dict[Any, Any], prefix: str) -> dict[str, Any]:
    found = [step for step in _steps(workflow) if step.get('uses', '').startswith(prefix)]
    assert len(found) == 1, (prefix, found)
    return found[0]


def test_one_job_runs_only_when_called(workflow: dict[Any, Any]) -> None:
    assert list(workflow['jobs']) == ['release-check']
    assert set(triggers(workflow)) == {'workflow_call'}
    assert 'pull_request_target' not in WORKFLOW.read_text(encoding='utf-8')


def test_railform_sha_is_a_required_string_input(workflow: dict[Any, Any]) -> None:
    inputs = triggers(workflow)['workflow_call']['inputs']
    assert set(inputs) == {'railform-sha'}
    assert inputs['railform-sha']['required'] is True
    assert inputs['railform-sha']['type'] == 'string'
    # The resolution date is the other workflow-level constant (tests/test_resolution_date.py).
    assert set(workflow['env']) == {'RAILFORM_SHA', 'RAILFORM_EXCLUDE_NEWER'}
    assert workflow['env']['RAILFORM_SHA'] == '${{ inputs.railform-sha }}'
    assert workflow['defaults'] == {'run': {'shell': 'bash'}}


def test_every_action_is_pinned_to_the_commits_python_ci_pins() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    refs = _USES.findall(text)
    assert refs, 'no `uses:` line found'
    for uses in refs:
        _, _, ref = uses.partition('@')
        assert _SHA.fullmatch(ref), uses
    lines = [line for line in text.splitlines() if _USES.match(line)]
    assert len(lines) == len(refs)
    for line in lines:
        assert re.search(r'# v\d+(\.\d+)*\s*$', line), line
    # The same pins as the reusable CI workflow, so one Dependabot update moves both.
    pinned = set(_USES.findall(PYTHON_CI.read_text(encoding='utf-8')))
    assert set(refs) <= pinned


def test_setup_uv_pins_the_uv_of_python_ci_and_keeps_no_cache(workflow: dict[Any, Any]) -> None:
    setup = _uses(workflow, 'astral-sh/setup-uv@')
    versions = {
        str(step['with']['version'])
        for job in load(PYTHON_CI)['jobs'].values()
        for step in job['steps']
        if step.get('uses', '').startswith('astral-sh/setup-uv@')
    }
    assert {str(setup['with']['version'])} == versions
    assert setup['with']['enable-cache'] is False


def test_no_permission_writes(workflow: dict[Any, Any]) -> None:
    assert workflow['permissions'] == {'contents': 'read', 'pull-requests': 'read'}
    for job in workflow['jobs'].values():
        assert 'permissions' not in job
    assert not re.search(r':\s*write\b', WORKFLOW.read_text(encoding='utf-8'))


def test_the_job_has_a_time_limit(workflow: dict[Any, Any]) -> None:
    limit = workflow['jobs']['release-check']['timeout-minutes']
    assert isinstance(limit, int) and 0 < limit <= 60


def test_the_pin_is_checked_before_anything_is_installed(workflow: dict[Any, Any]) -> None:
    first = _steps(workflow)[0]
    frame_check = load(PYTHON_CI)['jobs']['frame']['steps'][0]
    assert first == frame_check


def test_the_checkout_takes_the_whole_history_and_the_default_branch_on_a_tag_push(
    workflow: dict[Any, Any],
) -> None:
    checkout = _uses(workflow, 'actions/checkout@')['with']
    assert checkout['fetch-depth'] == 0
    assert checkout['persist-credentials'] is False
    repository = {'repository': {'default_branch': 'main'}}

    def ref(event: str, github_ref: str) -> object:
        github = {'event_name': event, 'ref': github_ref, 'event': repository}
        return interpolate(checkout['ref'], {'github': github})

    assert ref('push', 'refs/tags/v0.2.0') == 'main'
    # Contract (Wave C) 11.6: a dispatch from a tag checks out the default branch too.
    assert ref('workflow_dispatch', 'refs/tags/v0.2.0') == 'main'
    assert ref('workflow_dispatch', 'refs/heads/main') == ''
    assert ref('schedule', 'refs/heads/main') == ''


def test_release_check_runs_from_the_input_with_the_token_in_its_environment(
    workflow: dict[Any, Any],
) -> None:
    runs = [step for step in _steps(workflow) if 'run' in step]
    assert runs[-1]['run'] == f'{INSTALL} release-check'
    assert runs[-1]['env'] == {
        'GITHUB_TOKEN': '${{ github.token }}',
        'UV_EXCLUDE_NEWER': '${{ env.RAILFORM_EXCLUDE_NEWER }}',
    }
    for step in runs:
        assert '${{' not in step['run'], step


def test_railform_runs_its_own_tag_watch_by_local_path() -> None:
    # Written by railform's own `railform adopt` (ci.self_hosted), so §17's tag is judged by it.
    text = OWN_CALLER.read_text(encoding='utf-8')
    assert text == adopt._render_tag_watch(None)
    job = load(OWN_CALLER)['jobs']['release-check']
    assert job['uses'] == './.github/workflows/tag-watch.yml'
    assert job['with'] == {'railform-sha': '${{ github.sha }}'}
