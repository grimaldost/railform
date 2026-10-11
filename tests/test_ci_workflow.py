"""railform's own CI is a caller of its reusable workflow by local path (development-rails spec,
§9): `ci.yml` holds the `railform` and `gate` jobs that `railform adopt` writes, with
`ci.self_hosted` declared in `railform.toml`, so every change runs through the workflow it ships.

The workflow itself is tested in `tests/test_python_ci_workflow.py`.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any

import pytest
import yaml

from workflow_model import ROOT, WORKFLOWS, load, triggers

CALLER = WORKFLOWS / 'ci.yml'
COMMIT_UNDER_TEST = '${{ github.event.pull_request.head.sha || github.sha }}'


@pytest.fixture(scope='module')
def caller() -> dict[Any, Any]:
    return load(CALLER)


def test_it_runs_on_push_to_main_and_on_every_pull_request_change(caller: dict[Any, Any]) -> None:
    on = triggers(caller)
    assert on['push']['branches'] == ['main']
    # `edited` re-runs the commit lane when the title or body changes.
    assert on['pull_request']['types'] == ['opened', 'synchronize', 'reopened', 'edited']
    assert set(on) == {'push', 'pull_request'}


def test_the_token_is_read_only(caller: dict[Any, Any]) -> None:
    assert caller['permissions'] == {'contents': 'read'}
    for name, job in caller['jobs'].items():
        block = job.get('permissions')
        assert block is None or 'write' not in block.values(), (name, block)
    assert not re.search(r':\s*write\b', CALLER.read_text(encoding='utf-8'))


def test_it_never_runs_on_pull_request_target() -> None:
    assert 'pull_request_target' not in CALLER.read_text(encoding='utf-8')


def test_every_remote_action_is_pinned_to_a_commit() -> None:
    for uses in re.findall(r'^\s*(?:-\s+)?uses:\s*(\S+)', CALLER.read_text(encoding='utf-8'), re.M):
        if uses.startswith('./'):
            continue
        assert re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}', uses), uses


def test_no_run_text_interpolates_an_expression(caller: dict[Any, Any]) -> None:
    for job in caller['jobs'].values():
        for step in job.get('steps', []):
            assert '${{' not in step.get('run', ''), step


def test_the_railform_job_calls_the_reusable_workflow_by_local_path(
    caller: dict[Any, Any],
) -> None:
    job = caller['jobs']['railform']
    assert job['uses'] == './.github/workflows/python-ci.yml'
    # The pin is always an input; in railform itself it is the commit under test.
    assert job['with'] == {'railform-sha': COMMIT_UNDER_TEST}
    assert job['permissions'] == {'contents': 'read'}


def test_the_gate_job_needs_every_other_job(caller: dict[Any, Any]) -> None:
    gate = caller['jobs']['gate']
    assert gate['needs'] == [name for name in caller['jobs'] if name != 'gate']
    assert gate['needs'][0] == 'railform'
    assert gate['if'] == 'always()'


def test_railform_declares_that_it_hosts_its_own_workflow() -> None:
    config = tomllib.loads((ROOT / 'railform.toml').read_text(encoding='utf-8'))
    assert config['project']['type'] == 'python-package'
    assert config['ci']['self_hosted'] is True
    rows = [row for row in config['exceptions'] if row['key'] == 'ci.self_hosted']
    assert len(rows) == 1
    assert rows[0]['value'] is True
    assert 'local path' in rows[0]['reason']


def test_the_pre_commit_railform_entry_runs_the_code_of_this_checkout() -> None:
    config = yaml.safe_load((ROOT / '.pre-commit-config.yaml').read_text(encoding='utf-8'))
    local = [repo for repo in config['repos'] if repo['repo'] == 'local']
    assert len(local) == 1
    entries = {hook['id']: hook['entry'] for hook in local[0]['hooks']}
    assert entries == {
        f'railform-{verb}': f'uv run --frozen railform {verb}'
        for verb in ('commit-msg', 'branch-name', 'hooks-check', 'private-refs')
    }


def test_dependabot_watches_github_actions_weekly_and_ignores_railform() -> None:
    config = yaml.safe_load((ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8'))
    assert config['updates'] == [
        {
            'package-ecosystem': 'github-actions',
            'directory': '/',
            'schedule': {'interval': 'weekly'},
            'commit-message': {'prefix': 'ci'},
            'ignore': [{'dependency-name': 'grimaldost/railform'}],
        }
    ]
