"""The `commit-lane` job of railform's own CI runs the range and text checks on pull requests."""

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'ci.yml'


@pytest.fixture(scope='module')
def workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def job(workflow: dict[Any, Any]) -> dict[str, Any]:
    return workflow['jobs']['commit-lane']


def test_pull_requests_trigger_on_the_four_types_that_can_change_the_text_or_the_range(
    workflow: dict[Any, Any],
) -> None:
    # YAML 1.1 reads the key `on` as the boolean True.
    triggers = workflow.get('on', workflow.get(True))
    assert triggers['pull_request']['types'] == ['opened', 'synchronize', 'reopened', 'edited']
    assert triggers['push']['branches'] == ['main']


def test_the_job_runs_only_on_pull_requests(job: dict[str, Any]) -> None:
    assert job['if'] == "github.event_name == 'pull_request'"


def test_the_checkout_fetches_the_whole_history_without_credentials(job: dict[str, Any]) -> None:
    checkout = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with']['fetch-depth'] == 0
    assert checkout['with']['persist-credentials'] is False


def test_the_job_checks_the_range_then_the_text(job: dict[str, Any]) -> None:
    runs = [step['run'] for step in job['steps'] if 'run' in step]
    assert runs == [
        'uv run --frozen railform commit-range "origin/${BASE_REF}...HEAD"',
        'uv run --frozen railform pr-text',
    ]


def test_the_base_branch_reaches_the_range_through_the_environment(job: dict[str, Any]) -> None:
    step = next(s for s in job['steps'] if 'commit-range' in s.get('run', ''))
    assert step['env'] == {'BASE_REF': '${{ github.base_ref }}'}
    assert step['shell'] == 'bash'
    # Whoever names the branch controls the name: it is never interpolated into the script.
    assert '${{' not in step['run']


def test_an_edit_of_the_title_or_body_reruns_only_the_commit_lane(workflow: dict[Any, Any]) -> None:
    skipped = "github.event.action != 'edited'"
    assert workflow['jobs']['checks']['if'] == skipped
    assert workflow['jobs']['audit']['if'] == skipped
    # The commit lane is the one job that reads the text an edit changes.
    assert workflow['jobs']['commit-lane']['if'] == "github.event_name == 'pull_request'"
