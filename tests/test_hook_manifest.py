"""The pre-commit hook manifest at the repository root (development-rails spec, §8; interface
contract, section 10).

`.pre-commit-hooks.yaml` declares the four hooks a repository that pins railform runs. Each runs
`railform <verb>` from the same commit (`language: python`). These tests read the file; the hooks
run for real, through armed fixture clones, in `tests/test_hook_rail.py`.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from railform import cli

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / '.pre-commit-hooks.yaml'
TEMPLATES = ROOT / 'src' / 'railform' / 'templates'
HOOK_IDS = (
    'railform-commit-msg',
    'railform-branch-name',
    'railform-hooks-check',
    'railform-private-refs',
)
STAGES = {
    'railform-commit-msg': ['commit-msg'],
    'railform-branch-name': ['pre-push'],
    'railform-hooks-check': ['pre-commit'],
    'railform-private-refs': ['pre-commit'],
}


@pytest.fixture(scope='module')
def hooks() -> dict[str, dict[str, Any]]:
    entries = yaml.safe_load(MANIFEST.read_text(encoding='utf-8'))
    assert isinstance(entries, list)
    return {entry['id']: entry for entry in entries}


def test_the_manifest_declares_exactly_the_four_hooks_in_order() -> None:
    entries = yaml.safe_load(MANIFEST.read_text(encoding='utf-8'))
    assert tuple(entry['id'] for entry in entries) == HOOK_IDS


@pytest.mark.parametrize('hook_id', HOOK_IDS)
def test_each_hook_runs_its_verb_from_the_same_commit(
    hook_id: str, hooks: dict[str, dict[str, Any]]
) -> None:
    hook = hooks[hook_id]
    verb = hook_id.removeprefix('railform-')
    assert hook['language'] == 'python'
    assert hook['entry'] == f'railform {verb}'
    assert verb in cli.VERBS
    assert cli._load_verb(verb) is not None, f'{verb} has no module'
    assert hook['stages'] == STAGES[hook_id]


def test_branch_name_always_runs_and_takes_no_file_names(
    hooks: dict[str, dict[str, Any]],
) -> None:
    hook = hooks['railform-branch-name']
    assert hook['always_run'] is True
    assert hook['pass_filenames'] is False


def test_private_refs_judges_the_staged_files_only(hooks: dict[str, dict[str, Any]]) -> None:
    # pre-commit's defaults: the staged file names are passed, and the hook is skipped when no
    # staged file is left to judge.
    hook = hooks['railform-private-refs']
    assert hook.get('pass_filenames', True) is True
    assert hook.get('always_run', False) is False
    assert 'files' not in hook


def test_red_proof_hooks_check_always_runs_and_takes_no_file_names(
    hooks: dict[str, dict[str, Any]],
) -> None:
    # pre-commit leaves deleted paths out of the staged files: a commit that only deletes a
    # wrapper, or only changes railform.toml, must still run the check.
    hook = hooks['railform-hooks-check']
    assert hook['always_run'] is True
    assert hook['pass_filenames'] is False
    # The default hooks.dir is still named, though it no longer decides whether the hook runs.
    assert re.search(hook['files'], '.githooks/pre-commit')
    assert not re.search(hook['files'], 'src/.githooks/pre-commit')
    assert not re.search(hook['files'], 'githooks.md')


@pytest.fixture(scope='module')
def local_hooks() -> dict[str, dict[str, Any]]:
    """The hooks of the `repo: local` entry `railform adopt` writes in a self-hosted repository."""
    text = (TEMPLATES / 'pre-commit-local.yaml').read_text(encoding='utf-8')
    entries = yaml.safe_load(text.replace('__HOOKS_FILES__', "'^\\.githooks/'"))
    assert [entry['repo'] for entry in entries] == ['local']
    return {hook['id']: hook for hook in entries[0]['hooks']}


@pytest.mark.parametrize('hook_id', HOOK_IDS)
def test_red_proof_the_local_entry_runs_each_hook_like_the_manifest(
    hook_id: str, hooks: dict[str, dict[str, Any]], local_hooks: dict[str, dict[str, Any]]
) -> None:
    hook, local = hooks[hook_id], local_hooks[hook_id]
    assert local['stages'] == hook['stages']
    assert local.get('always_run', False) is hook.get('always_run', False)
    assert local.get('pass_filenames', True) is hook.get('pass_filenames', True)


def test_the_manifest_is_valid_for_pre_commit() -> None:
    done = subprocess.run(
        [sys.executable, '-m', 'pre_commit', 'validate-manifest', str(MANIFEST)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_pre_commit_is_in_the_dev_group() -> None:
    pyproject = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    dev = [item for item in pyproject['dependency-groups']['dev'] if isinstance(item, str)]
    assert any(item.split('>')[0].split('=')[0].strip() == 'pre-commit' for item in dev)


# The frozen rule of the source `railform hooks-check` was ported from (the stack-radar engine,
# tests/test_commit_lane.py), held over every command railform ships (development-rails spec,
# §13): a `uv run` that may re-lock would let a hook or a CI step change `uv.lock` or install
# what the lock does not name. `--no-project` runs outside any project and has no lock to keep.
_UNFROZEN = re.compile(r'\buv run\b(?! --frozen\b)(?! --no-project\b)')
SHIPPED = (
    ROOT / 'src' / 'railform' / 'default_frame.toml',
    *sorted(TEMPLATES.iterdir()),
    *sorted((ROOT / '.github' / 'workflows').glob('*.yml')),
    MANIFEST,
    ROOT / '.pre-commit-config.yaml',
)


def test_red_proof_every_uv_run_railform_ships_is_frozen() -> None:
    assert _UNFROZEN.search('entry: uv run railform commit-msg')
    assert not _UNFROZEN.search('uv run --frozen python -m pytest -q')
    for path in SHIPPED:
        text = path.read_text(encoding='utf-8')
        for number, line in enumerate(text.splitlines(), start=1):
            assert not _UNFROZEN.search(line), f'{path.name}:{number}: {line.strip()}'
