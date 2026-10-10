"""The repository's own CI runs the gate commands on both systems and pins every action."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'ci.yml'
USES = re.compile(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}')


@pytest.fixture(scope='module')
def workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the key `on` as the boolean True.
    return workflow.get('on', workflow.get(True))


def test_it_runs_on_push_to_main_and_on_pull_requests(workflow: dict[Any, Any]) -> None:
    triggers = _triggers(workflow)
    assert triggers['push']['branches'] == ['main']
    assert 'pull_request' in triggers


def test_the_token_is_read_only(workflow: dict[Any, Any]) -> None:
    assert workflow['permissions'] == {'contents': 'read'}


def test_the_checks_job_covers_two_systems_and_two_pythons(workflow: dict[Any, Any]) -> None:
    matrix = workflow['jobs']['checks']['strategy']['matrix']
    assert matrix['os'] == ['ubuntu-latest', 'windows-latest']
    assert matrix['python'] == ['3.11', '3.14']


def test_the_checks_job_runs_the_gate_commands_in_order(workflow: dict[Any, Any]) -> None:
    runs = [step['run'] for step in workflow['jobs']['checks']['steps'] if 'run' in step]
    assert runs == [
        'uv lock --check',
        'uv sync --frozen --python ${{ matrix.python }}',
        'uv run --frozen ruff format --check .',
        'uv run --frozen ruff check .',
        'uv run --frozen ty check src',
        'uv run --frozen python -m pytest -q',
    ]


def test_the_audit_job_runs_zizmor_at_a_pinned_version(workflow: dict[Any, Any]) -> None:
    runs = [step['run'] for step in workflow['jobs']['audit']['steps'] if 'run' in step]
    assert runs == ['uvx zizmor==1.30.0 --min-severity high .github/workflows']


def test_every_action_is_pinned_by_a_full_sha_with_a_version_comment() -> None:
    lines = [line for line in WORKFLOW.read_text(encoding='utf-8').splitlines() if 'uses:' in line]
    assert lines
    for line in lines:
        assert USES.search(line), line
        assert re.search(r'# v\d+(\.\d+)*\s*$', line), line


def test_checkout_does_not_persist_credentials(workflow: dict[Any, Any]) -> None:
    checkouts = [
        step
        for job in workflow['jobs'].values()
        for step in job['steps']
        if step.get('uses', '').startswith('actions/checkout@')
    ]
    assert checkouts
    assert all(step['with']['persist-credentials'] is False for step in checkouts)


def test_dependabot_watches_github_actions_weekly() -> None:
    config = yaml.safe_load((ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8'))
    assert config['updates'] == [
        {
            'package-ecosystem': 'github-actions',
            'directory': '/',
            'schedule': {'interval': 'weekly'},
        }
    ]
