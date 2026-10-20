"""The resolution date of the tools `uvx` installs (interface contract, Wave C, section 12).

Every workflow step that runs `uvx` resolves its dependencies as of one date, through uv's
`UV_EXCLUDE_NEWER` in that step's `env:`, mapped from the workflow-level `RAILFORM_EXCLUDE_NEWER`.
`python-ci.yml` and `tag-watch.yml` each carry the date once and hold the same value. The
`checks` job's pipeline step, which runs the adopter's own commands, never sees it: a lock
resolved under another date would read as out of date there.

The date moves with every release, so no test here names its value; it must be an RFC 3339 UTC
timestamp and the same in both files.

Each rule is a function that returns the problems of a workflow. The tests run it over the real
files (no problems) and over copies broken in the one way the rule forbids (a problem each), so
a rule that stopped looking fails here instead of passing on a file it no longer reads.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest

from workflow_model import WORKFLOWS, load

FILES = ('python-ci.yml', 'tag-watch.yml')
# The `uvx` steps of each file, counted so that a rule that finds none cannot pass.
UVX_STEPS = {'python-ci.yml': 14, 'tag-watch.yml': 1}
CONSTANT = 'RAILFORM_EXCLUDE_NEWER'
VARIABLE = 'UV_EXCLUDE_NEWER'
MAPPING = '${{ env.RAILFORM_EXCLUDE_NEWER }}'
# `uvx` and its long form `uv tool run`.
_UVX = re.compile(r'(?:^|[\s;&|(])(?:uvx|uv\s+tool\s+run)\s')
_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


@pytest.fixture(params=FILES)
def name(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _steps(workflow: dict[Any, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f'{job_name}: {step.get("name") or step["run"].splitlines()[0]}', step)
        for job_name, job in workflow['jobs'].items()
        for step in job.get('steps') or []
        if 'run' in step
    ]


def _runs_uvx(step: dict[str, Any]) -> bool:
    code = [line for line in step['run'].splitlines() if not line.lstrip().startswith('#')]
    return any(_UVX.search(line) for line in code)


# --- the rules ---------------------------------------------------------------------------------


def uvx_problems(workflow: dict[Any, Any]) -> list[str]:
    """Every step that runs `uvx` without the mapping to the workflow's date."""
    problems = []
    for label, step in _steps(workflow):
        if not _runs_uvx(step):
            continue
        if (step.get('env') or {}).get(VARIABLE) != MAPPING:
            problems.append(f'{label}: no {VARIABLE} mapping')
    return problems


def scope_problems(workflow: dict[Any, Any]) -> list[str]:
    """Every place but a `uvx` step that carries the variable: the pipeline step above all."""
    problems = []
    if VARIABLE in (workflow.get('env') or {}):
        problems.append('the workflow env carries it')
    for job_name, job in workflow['jobs'].items():
        if VARIABLE in (job.get('env') or {}):
            problems.append(f'job {job_name} env carries it')
    for label, step in _steps(workflow):
        if not _runs_uvx(step) and VARIABLE in (step.get('env') or {}):
            problems.append(f'{label}: a step that runs no uvx carries it')
    return problems


def date_problems(workflows: dict[str, dict[Any, Any]]) -> list[str]:
    """The date of each file: one RFC 3339 UTC string, the same in every file."""
    problems = []
    dates = {name: (flow.get('env') or {}).get(CONSTANT) for name, flow in workflows.items()}
    for name, date in dates.items():
        # A bare timestamp would be read by YAML as a datetime, and by uv as nothing.
        if not isinstance(date, str) or not _TIMESTAMP.fullmatch(date):
            problems.append(f'{name}: {date!r} is not an RFC 3339 UTC timestamp')
    if len(set(map(repr, dates.values()))) != 1:
        problems.append(f'the dates differ: {dates}')
    return problems


# --- the real files ----------------------------------------------------------------------------


def _workflows() -> dict[str, dict[Any, Any]]:
    return {name: load(WORKFLOWS / name) for name in FILES}


def _pipeline_step(workflow: dict[Any, Any]) -> dict[str, Any]:
    found = [step for _, step in _steps(workflow) if 'PIPELINE' in (step.get('env') or {})]
    assert len(found) == 1
    return found[0]


def test_each_uvx_step_maps_the_date_and_no_other_scope_carries_it(name: str) -> None:
    workflow = load(WORKFLOWS / name)
    # Non-vacuity: the steps are found, and they are all of them.
    uvx = [label for label, step in _steps(workflow) if _runs_uvx(step)]
    assert len(uvx) == UVX_STEPS[name], uvx
    assert uvx_problems(workflow) == []
    assert scope_problems(workflow) == []


def test_each_file_carries_the_date_once_and_the_two_agree() -> None:
    assert date_problems(_workflows()) == []
    for name in FILES:
        assert (WORKFLOWS / name).read_text(encoding='utf-8').count(f'{CONSTANT}:') == 1


def test_the_pipeline_step_runs_no_uvx_and_carries_no_date() -> None:
    step = _pipeline_step(load(WORKFLOWS / 'python-ci.yml'))
    assert VARIABLE not in step['env']
    assert not _runs_uvx(step)


def test_no_run_text_names_the_date(name: str) -> None:
    for label, step in _steps(load(WORKFLOWS / name)):
        assert 'EXCLUDE_NEWER' not in step['run'], label


# --- red proofs: each rule fails on a file broken the way it forbids ---------------------------


@pytest.mark.parametrize('file', FILES)
def test_red_proof_a_uvx_step_without_the_mapping_is_a_problem(file: str) -> None:
    workflow = load(WORKFLOWS / file)
    labels = [label for label, step in _steps(workflow) if _runs_uvx(step)]
    assert len(labels) == UVX_STEPS[file]
    for label in labels:
        broken = copy.deepcopy(workflow)
        dict(_steps(broken))[label]['env'].pop(VARIABLE)
        problems = uvx_problems(broken)
        assert len(problems) == 1 and label in problems[0], problems


def test_red_proof_a_mapping_to_another_name_is_a_problem() -> None:
    broken = load(WORKFLOWS / 'tag-watch.yml')
    _steps(broken)[-1][1]['env'][VARIABLE] = '${{ env.RAILFORM_SHA }}'
    assert len(uvx_problems(broken)) == 1


@pytest.mark.parametrize(
    ('run', 'expected'),
    [
        ('uvx zizmor==1.30.0 .', True),
        ('uv tool run zizmor==1.30.0 .', True),
        ('set -e\nuv  tool  run --from x y', True),
        ('# uvx is not run here\nuv lock --check', False),
        ('uv tool install zizmor', False),
        ('echo uvxy', False),
    ],
)
def test_the_uvx_pattern_sees_uvx_and_uv_tool_run(run: str, expected: bool) -> None:
    assert _runs_uvx({'run': run}) is expected


def test_red_proof_the_pipeline_step_carrying_the_variable_is_a_problem() -> None:
    broken = load(WORKFLOWS / 'python-ci.yml')
    _pipeline_step(broken)['env'][VARIABLE] = MAPPING
    problems = scope_problems(broken)
    assert len(problems) == 1 and 'pipeline' in problems[0].lower(), problems


def test_red_proof_the_variable_in_a_wider_scope_is_a_problem() -> None:
    broken = load(WORKFLOWS / 'python-ci.yml')
    broken['jobs']['checks'].setdefault('env', {})[VARIABLE] = MAPPING
    assert len(scope_problems(broken)) == 1
    broken = load(WORKFLOWS / 'python-ci.yml')
    broken['env'][VARIABLE] = '2026-10-11T00:00:00Z'
    assert len(scope_problems(broken)) == 1


def test_red_proof_two_files_with_different_dates_are_a_problem() -> None:
    broken = _workflows()
    broken['tag-watch.yml']['env'][CONSTANT] = '2025-01-01T00:00:00Z'
    problems = date_problems(broken)
    assert len(problems) == 1 and 'differ' in problems[0], problems


@pytest.mark.parametrize('bad', ['2026-10-11', '2026-10-11T00:00:00+00:00', '', None, 5])
def test_red_proof_a_date_that_is_not_an_rfc_3339_utc_string_is_a_problem(bad: object) -> None:
    broken = _workflows()
    broken['python-ci.yml']['env'][CONSTANT] = bad
    broken['tag-watch.yml']['env'][CONSTANT] = bad
    assert date_problems(broken)
