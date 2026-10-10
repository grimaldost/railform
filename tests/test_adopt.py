"""`railform adopt` (development-rails spec, §7; interface contract, section 9).

The red proofs of §7 that run here, each named in its test:

- one edited owned line fails `--check` with `drift`
  (`test_red_proof_an_edited_owned_line_fails_check_with_drift`);
- a pre-commit `rev` that differs from the workflow pin fails with `pin-mismatch`, and so does a
  `railform-sha` input that differs (`test_red_proof_a_pin_that_differs_fails_pin_mismatch`);
- a second workflow file on `pull_request` fails `--check` with `extra-pr-workflow` until its
  job moves into `ci.yml`, or `ci.extra_workflows` and an exceptions row name it
  (`test_red_proof_a_second_pull_request_workflow_fails_until_its_job_moves`,
  `test_red_proof_a_listed_pull_request_workflow_passes_with_an_exceptions_row`).

The live run in which a failing kept job turns `gate` red belongs to §9 (the PR09 brief's
`test/redproof-extra-job-fails`); here the `gate` job's shape and its condition are checked, and
the condition is evaluated over every result. Git is isolated for every test by
`tests/conftest.py`.
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from migration_fixtures import (
    CI,
    DEPENDABOT,
    OTHER_PIN,
    OWNED_FILES,
    PIN,
    PR_TYPES_LINE,
    PRE_COMMIT,
    TYPES,
    Fixture,
    build,
    git,
    outside_region,
    region,
    write,
)
from railform import adopt as adopt_module
from railform import frame, wrapper
from railform.cli import main

HOOKS = ('pre-commit', 'commit-msg', 'pre-push')
VERDICT = 'railform-verdict: adopt'
NEWEST = frame.preset_frame('python-package')['python']['newest']
SELF_HOSTED_CONFIG = """\
[project]
type = "python-package"

[ci]
self_hosted = true

[[exceptions]]
key = "ci.self_hosted"
value = true
reason = "railform calls its own reusable workflow by local path"
"""


def run_adopt(capsys: pytest.CaptureFixture[str], *flags: str) -> tuple[int, str, str]:
    code = main(['adopt', *flags])
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith('railform-verdict:')]
    assert len(lines) == 1
    assert out.splitlines()[-1] == lines[0]
    return code, lines[0], out


def read(root: Path, path: str) -> str:
    return (root / path).read_bytes().decode('utf-8')


def snapshot(root: Path) -> tuple[dict[str, bytes], str]:
    files = {
        str(path.relative_to(root)).replace('\\', '/'): path.read_bytes()
        for path in sorted(root.rglob('*'))
        if path.is_file() and '.git' not in path.relative_to(root).parts
    }
    return files, git(root, 'ls-files', '-s')


@pytest.fixture(params=TYPES)
def adopted(
    request: pytest.FixtureRequest, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> Fixture:
    """A migration fixture of each type, adopted at PIN."""
    fixture = build(scratch_repo, request.param)
    code, line, _ = run_adopt(capsys, '--pin', PIN)
    assert (code, line) == (0, f'{VERDICT} pass ok')
    return fixture


# --- adopt on the migration fixtures -----------------------------------------------------------


def test_adopt_leaves_every_extra_intact(adopted: Fixture) -> None:
    for path in OWNED_FILES:
        text = read(adopted.root, path)
        assert outside_region(text) == adopted.expected[path], path
        for extra in adopted.extras[path]:
            assert extra in text, (path, extra)


def test_adopt_check_passes_right_after_adopt(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (0, f'{VERDICT} pass ok'), out
    for verb in ('frame', 'hooks-check'):
        assert main([verb, '--check'] if verb == 'frame' else [verb]) == 0
    capsys.readouterr()


def test_a_second_run_changes_nothing(adopted: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    before = snapshot(adopted.root)
    code, line, out = run_adopt(capsys)
    assert (code, line) == (0, f'{VERDICT} pass ok')
    assert 'nothing to change' in out
    assert snapshot(adopted.root) == before


def test_the_first_run_seeds_railform_toml_with_the_detected_type(adopted: Fixture) -> None:
    config = frame.read_config(adopted.root)
    newest = frame.preset_frame(adopted.project_type)['python']['newest']
    versions = [newest] if adopted.project_type == 'plugin-marketplace' else ['3.11', newest]
    assert config == {'project': {'type': adopted.project_type}, 'python': {'versions': versions}}


def test_the_wrappers_are_the_frame_wrappers_and_100755_in_the_index(adopted: Fixture) -> None:
    effective = frame.load(adopted.root)
    for hook in HOOKS:
        path = f'.githooks/{hook}'
        assert read(adopted.root, path) == wrapper.render(effective, hook)
        assert git(adopted.root, 'ls-files', '-s', '--', path).split()[0] == '100755'


def test_each_region_names_the_repository_once_and_the_pin_once_per_site(adopted: Fixture) -> None:
    ci, pre_commit, dependabot = (region(read(adopted.root, path)) for path in OWNED_FILES)
    assert ci.count('grimaldost/railform') == 1
    assert ci.count(PIN) == 2
    assert f'grimaldost/railform/.github/workflows/python-ci.yml@{PIN}\n' in ci
    assert f'railform-sha: {PIN}\n' in ci
    assert pre_commit.count('grimaldost/railform') == 1
    assert pre_commit.count(PIN) == 1
    assert dependabot.count('grimaldost/railform') == 1
    assert PIN not in dependabot


def test_the_hygiene_entry_is_pre_commit_hooks_v5_at_its_sha(adopted: Fixture) -> None:
    repos = yaml.safe_load(read(adopted.root, PRE_COMMIT))['repos']
    hygiene = [repo for repo in repos if repo['repo'].endswith('/pre-commit/pre-commit-hooks')]
    assert len(hygiene) == 1
    assert hygiene[0]['rev'] == 'cef0300fd0fc4d2a87a85fa2093c6b283ea36f4b'
    hooks = {hook['id']: hook.get('args') for hook in hygiene[0]['hooks']}
    assert hooks == {
        'trailing-whitespace': ['--markdown-linebreak-ext=md'],
        'end-of-file-fixer': None,
        'check-merge-conflict': ['--assume-in-merge'],
        'check-added-large-files': None,
        'check-yaml': None,
        'check-toml': None,
        'check-json': None,
    }
    railform = [repo for repo in repos if repo['repo'] == 'https://github.com/grimaldost/railform']
    assert len(railform) == 1
    assert railform[0]['rev'] == PIN


def test_dependabot_ignores_railform_in_the_github_actions_update(adopted: Fixture) -> None:
    updates = yaml.safe_load(read(adopted.root, DEPENDABOT))['updates']
    actions = [update for update in updates if update['package-ecosystem'] == 'github-actions']
    assert len(actions) == 1
    assert {'dependency-name': 'grimaldost/railform'} in actions[0]['ignore']


def test_the_pull_request_trigger_gains_its_types(adopted: Fixture) -> None:
    workflow = yaml.safe_load(read(adopted.root, CI))
    types = workflow[True]['pull_request']['types']
    assert set(types) >= {'opened', 'synchronize', 'reopened', 'edited'}


def test_adopt_writes_no_bare_railform_requirement(adopted: Fixture) -> None:
    # Pre-mortem advisory R5-2: every reference names grimaldost/railform at a 40-hex commit.
    for path in OWNED_FILES:
        for line in read(adopted.root, path).splitlines():
            if 'railform' in line and ('uses:' in line or 'repo:' in line):
                assert 'grimaldost/railform' in line, line
            assert not re.match(r'\s*-?\s*railform\s*([<>=!~].*)?$', line), line
    ci = region(read(adopted.root, CI))
    assert re.search(r'python-ci\.yml@[0-9a-f]{40}\n', ci)


# --- the gate job ------------------------------------------------------------------------------


def _gate(root: Path) -> dict[str, object]:
    return yaml.safe_load(read(root, CI))['jobs']['gate']


def _fails(condition: str, results: tuple[str, ...]) -> bool:
    """Evaluate the gate step's condition, `always() && (<terms>)`, where the terms are
    `contains(needs.*.result, '<r>')` joined by `||`. `always()` holds in every run, a cancelled
    one included, so the step's verdict rests on the terms alone."""
    outer = re.fullmatch(r'always\(\) && \((?P<terms>.*)\)', condition)
    assert outer, condition
    found = False
    for term in outer['terms'].split('||'):
        match = re.fullmatch(r"\s*contains\(needs\.\*\.result, '([a-z]+)'\)\s*", term)
        assert match, term
        found = found or match[1] in results
    return found


def test_the_gate_job_needs_every_other_job_and_runs_always(adopted: Fixture) -> None:
    workflow = yaml.safe_load(read(adopted.root, CI))
    gate = workflow['jobs']['gate']
    others = [job for job in workflow['jobs'] if job != 'gate']
    assert gate['needs'] == others
    assert others[0] == 'railform'
    assert len(others) == 2
    assert gate['if'] == 'always()'
    assert gate['permissions'] == {}


def test_the_gate_fails_on_a_failed_or_cancelled_need_and_passes_otherwise(
    adopted: Fixture,
) -> None:
    steps = _gate(adopted.root)['steps']
    assert isinstance(steps, list)
    assert len(steps) == 1
    step = steps[0]
    assert step['run'] == 'exit 1'
    # Declared, so a workflow-level `defaults.run.shell` cannot change what runs.
    assert step['shell'] == 'bash'
    assert _gate(adopted.root)['runs-on'] == 'ubuntu-latest'
    results = ('success', 'failure', 'cancelled', 'skipped')
    for combination in itertools.product(results, repeat=3):
        bad = any(result in ('failure', 'cancelled') for result in combination)
        assert _fails(step['if'], combination) == bad, combination


def test_a_job_added_later_drifts_until_adopt_needs_it(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    text = read(adopted.root, CI)
    write(
        adopted.root,
        CI,
        text + '  late:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo late\n',
    )
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert 'needs:' in out
    assert run_adopt(capsys)[0] == 0
    assert _gate(adopted.root)['needs'][-1] == 'late'
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'


# --- red proofs --------------------------------------------------------------------------------

EDITS = {
    'gate-condition': (CI, '    if: always()\n', '    if: success()\n'),
    'railform-permissions': (
        CI,
        '      contents: read\n    with:',
        '      contents: write\n    with:',
    ),
    'hygiene-args': (PRE_COMMIT, '[--assume-in-merge]', '[]'),
    'hygiene-rev': (
        PRE_COMMIT,
        'rev: cef0300fd0fc4d2a87a85fa2093c6b283ea36f4b',
        'rev: 2c9f875913ee60ca25ce70243dc24d5b6415598c',
    ),
    'dependabot-ignore': (
        DEPENDABOT,
        'dependency-name: grimaldost/railform',
        'dependency-name: grimaldost/other',
    ),
    'pull-request-types': (CI, f'    {PR_TYPES_LINE}\n', '    types: [opened]\n'),
}


@pytest.mark.parametrize('edit', sorted(EDITS))
def test_red_proof_an_edited_owned_line_fails_check_with_drift(
    edit: str, adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path, old, new = EDITS[edit]
    text = read(adopted.root, path)
    assert text.count(old) == 1
    write(adopted.root, path, text.replace(old, new))
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert f'{path}:' in out
    # adopt puts the owned line back.
    assert run_adopt(capsys)[0] == 0
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'


def test_red_proof_an_edited_wrapper_fails_check_with_drift(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = '.githooks/commit-msg'
    write(adopted.root, path, read(adopted.root, path).replace('exec ', 'exec echo '))
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert path in out


def test_red_proof_a_wrapper_tracked_as_100644_fails_check_with_drift(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    git(adopted.root, 'update-index', '--chmod=-x', '--', '.githooks/pre-push')
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert '.githooks/pre-push: tracked as 100644' in out
    run_adopt(capsys)
    assert git(adopted.root, 'ls-files', '-s', '--', '.githooks/pre-push').startswith('100755')


@pytest.mark.parametrize(
    'site',
    [
        (PRE_COMMIT, f'rev: {PIN}\n', f'rev: {OTHER_PIN}\n'),
        (CI, f'railform-sha: {PIN}\n', f'railform-sha: {OTHER_PIN}\n'),
        (CI, f'python-ci.yml@{PIN}\n', f'python-ci.yml@{OTHER_PIN}\n'),
    ],
    ids=['pre-commit-rev', 'railform-sha-input', 'uses-sha'],
)
def test_red_proof_a_pin_that_differs_fails_pin_mismatch(
    site: tuple[str, str, str], adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path, old, new = site
    text = read(adopted.root, path)
    assert text.count(old) == 1
    write(adopted.root, path, text.replace(old, new))
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail pin-mismatch')
    assert OTHER_PIN in out
    assert 'drift' not in line


def test_a_pin_that_is_not_a_commit_fails_pin_mismatch(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    for path in (CI, PRE_COMMIT):
        write(adopted.root, path, read(adopted.root, path).replace(PIN, 'main'))
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail pin-mismatch')
    assert 'not a 40-hex commit' in out


_LINT_WORKFLOW = """\
name: lint
on:
  pull_request:
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: echo lint
"""


def test_red_proof_a_second_pull_request_workflow_fails_until_its_job_moves(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    write(adopted.root, '.github/workflows/lint.yml', _LINT_WORKFLOW)
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail extra-pr-workflow')
    assert '.github/workflows/lint.yml: runs on pull requests' in out
    # The adoption pull request moves the job into ci.yml as a kept job.
    (adopted.root / '.github/workflows/lint.yml').unlink()
    text = read(adopted.root, CI)
    write(adopted.root, CI, text + _LINT_WORKFLOW.split('jobs:\n', 1)[1])
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} fail drift'
    assert run_adopt(capsys)[0] == 0
    assert 'lint' in _gate(adopted.root)['needs']
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'


def test_red_proof_a_listed_pull_request_workflow_passes_with_an_exceptions_row(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    write(adopted.root, '.github/workflows/lint.yml', _LINT_WORKFLOW)
    config = read(adopted.root, 'railform.toml')
    listed = config + '\n[ci]\nextra_workflows = ["lint.yml"]\n'
    write(adopted.root, 'railform.toml', listed)
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'
    # Listing it is a departure from the preset: frame --check wants the exceptions row.
    assert main(['frame', '--check']) == 1
    assert 'unlisted-departure' in capsys.readouterr().out
    row = (
        '\n[[exceptions]]\nkey = "ci.extra_workflows"\nvalue = ["lint.yml"]\n'
        'reason = "a required context of its own during the migration"\n'
    )
    write(adopted.root, 'railform.toml', listed + row)
    assert main(['frame', '--check']) == 0
    capsys.readouterr()
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'


def test_a_second_workflow_that_does_not_run_on_pull_requests_passes(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        adopted.root,
        '.github/workflows/nightly.yml',
        _LINT_WORKFLOW.replace('  pull_request:\n', '  schedule:\n    - cron: "0 3 * * *"\n'),
    )
    write(adopted.root, '.github/workflows/list.yaml', 'on: [push, pull_request]\njobs: {}\n')
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail extra-pr-workflow')
    assert 'list.yaml' in out
    assert 'nightly.yml' not in out


@pytest.mark.parametrize(
    'text',
    [
        'on: pull_request\njobs: {}\n',
        'on: pull_request_target\njobs: {}\n',
        'on:\n  pull_request_target:\n    types: [labeled]\njobs: {}\n',
        'on: [push\njobs: {\n',
    ],
    ids=['scalar', 'target-scalar', 'target-mapping', 'unparseable'],
)
def test_a_second_workflow_on_a_pull_request_event_fails_in_each_form(
    text: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    write(scratch_repo, '.github/workflows/other.yml', text)
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail extra-pr-workflow')
    assert '.github/workflows/other.yml:' in out


@pytest.mark.parametrize('listed', ['other.yml', '.github/workflows/other.yml'])
def test_a_listed_workflow_passes_by_name_or_by_repository_path(
    listed: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    # An unparseable file too passes once it is listed.
    write(scratch_repo, '.github/workflows/other.yml', 'on: [push\n')
    config = read(scratch_repo, 'railform.toml')
    write(scratch_repo, 'railform.toml', config + f'\n[ci]\nextra_workflows = ["{listed}"]\n')
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'


# --- --pin -------------------------------------------------------------------------------------


def test_pin_rewrites_every_reference_at_once(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    code, line, _ = run_adopt(capsys, '--pin', OTHER_PIN.upper())
    assert (code, line) == (0, f'{VERDICT} pass ok')
    ci = region(read(adopted.root, CI))
    pre_commit = region(read(adopted.root, PRE_COMMIT))
    assert (ci.count(OTHER_PIN), pre_commit.count(OTHER_PIN)) == (2, 1)
    assert PIN not in ci + pre_commit
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'


def test_adopt_without_pin_keeps_the_workflow_pin_and_realigns_the_others(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    text = read(adopted.root, PRE_COMMIT)
    write(adopted.root, PRE_COMMIT, text.replace(f'rev: {PIN}', f'rev: {OTHER_PIN}'))
    assert run_adopt(capsys)[0] == 0
    assert f'rev: {PIN}' in read(adopted.root, PRE_COMMIT)


def test_tag_watch_caller_is_a_whole_file_judged_once_the_template_ships(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    template = (
        'name: railform tag watch\non:\n  push:\n    tags: ["v*"]\npermissions:\n  contents: read\n'
        'jobs:\n  tag-watch:\n'
        '    uses: grimaldost/railform/.github/workflows/tag-watch.yml@__PIN__\n'
    )
    shipped = adopt_module._template_text

    def with_tag_watch(name: str) -> str | None:
        return template if name == adopt_module.TAG_WATCH_NAME else shipped(name)

    build(scratch_repo, 'python-package')
    path = adopt_module.TAG_WATCH_PATH
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    # This package carries no template yet: nothing is written, and nothing is judged.
    assert not (scratch_repo / path).exists()
    write(scratch_repo, path, 'name: the repository own file\n')
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'
    (scratch_repo / path).unlink()
    monkeypatch.setattr(adopt_module, '_template_text', with_tag_watch)
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} fail drift'
    assert run_adopt(capsys)[0] == 0
    assert read(scratch_repo, path) == template.replace('__PIN__', PIN)
    assert run_adopt(capsys, '--pin', OTHER_PIN)[0] == 0
    assert read(scratch_repo, path) == template.replace('__PIN__', OTHER_PIN)
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'
    # The whole file is owned: an edit elsewhere is drift, and adopt never writes it over.
    edited = read(scratch_repo, path).replace('tags: ["v*"]', 'tags: ["release-*"]')
    write(scratch_repo, path, edited)
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} fail drift'
    assert run_adopt(capsys)[0] == 0
    assert read(scratch_repo, path) == edited
    # Only its pin differing is pin-mismatch.
    write(scratch_repo, path, template.replace('__PIN__', PIN))
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail pin-mismatch')
    assert path in out


# --- arguments and refusals --------------------------------------------------------------------


@pytest.mark.parametrize(
    'flags',
    [
        ('--pin', 'abc'),
        ('--pin', PIN + '0'),
        ('--pin', 'g' * 40),
        ('--check', '--pin', PIN),
        ('--check', '--type', 'python-package'),
        ('--type', 'rust-crate'),
    ],
)
def test_bad_arguments_exit_2_usage(
    flags: tuple[str, ...], scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    code, line, _ = run_adopt(capsys, *flags)
    assert (code, line) == (2, f'{VERDICT} fail usage')
    assert not (scratch_repo / 'railform.toml').exists()


def test_the_first_adoption_needs_a_pin_and_writes_nothing_without_one(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = build(scratch_repo, 'python-scripts')
    assert main(['adopt']) == 2
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f'{VERDICT} fail usage']
    assert '--pin' in captured.err
    assert not (scratch_repo / 'railform.toml').exists()
    assert not (scratch_repo / '.githooks').exists()
    for path, text in fixture.original.items():
        assert read(scratch_repo, path) == text


def test_a_job_named_gate_outside_the_region_is_refused(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = build(scratch_repo, 'python-package')
    write(scratch_repo, CI, fixture.original[CI].replace('  docs:\n', '  gate:\n'))
    code, line, _ = run_adopt(capsys, '--pin', PIN)
    assert (code, line) == (2, f'{VERDICT} fail usage')
    assert read(scratch_repo, CI) == fixture.original[CI].replace('  docs:\n', '  gate:\n')


def test_a_broken_marker_pair_is_drift_under_check_and_refused_by_adopt(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    text = read(adopted.root, PRE_COMMIT)
    broken = re.sub(r'^ *# <<< railform\n', '', text, flags=re.MULTILINE)
    assert broken != text
    write(adopted.root, PRE_COMMIT, broken)
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} fail drift'
    assert run_adopt(capsys)[1] == f'{VERDICT} fail usage'


@pytest.mark.parametrize('path', OWNED_FILES)
def test_a_deleted_owned_region_is_drift(
    path: str, adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    text = read(adopted.root, path)
    write(adopted.root, path, text.replace(region(text), ''))
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert f'{path}: has no region owned by railform' in out


def test_a_deleted_wrapper_is_drift(adopted: Fixture, capsys: pytest.CaptureFixture[str]) -> None:
    (adopted.root / '.githooks' / 'pre-push').unlink()
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert '.githooks/pre-push: the hook wrapper is missing' in out


@pytest.mark.parametrize(
    'pattern',
    [r'^ *railform-sha: [0-9a-f]{40}\n', r'^ *rev: [0-9a-f]{40}\n'],
    ids=['railform-sha-input', 'pre-commit-rev'],
)
def test_red_proof_a_missing_pin_site_is_drift(
    pattern: str, adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    path = CI if 'railform-sha' in pattern else PRE_COMMIT
    text = read(adopted.root, path)
    edited = re.sub(pattern, '', text, count=1, flags=re.MULTILINE)
    assert edited != text
    write(adopted.root, path, edited)
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert path in out


def test_drift_is_named_before_pin_mismatch(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    ci = read(adopted.root, CI)
    write(adopted.root, CI, ci.replace('    runs-on: ubuntu-latest\n', '    runs-on: x\n', 1))
    pre = read(adopted.root, PRE_COMMIT)
    write(adopted.root, PRE_COMMIT, pre.replace(f'rev: {PIN}', f'rev: {OTHER_PIN}'))
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert 'differs from what railform adopt writes' in out
    assert f'is {OTHER_PIN}, not the pin {PIN}' in out


# --- the owned jobs are jobs -------------------------------------------------------------------

_DOCS_STEP = '      - run: echo "the documentation builds"\n'


def _shifted(text: str, columns: int) -> str:
    return ''.join(
        ' ' * columns + line if line.strip() else line for line in text.splitlines(keepends=True)
    )


def _moved_region(root: Path, place: str) -> str:
    """ci.yml of the adopted python-package fixture with its region moved, unchanged, under the
    kept job `docs` or into the block scalar of that job's step."""
    text = read(root, CI)
    owned = region(text)
    rest = text.replace(owned, '')
    if place == 'under-a-kept-job':
        return rest + _shifted(owned, 2)
    block = '      - run: |\n          echo "the documentation builds"\n' + _shifted(owned, 8)
    return rest.replace(_DOCS_STEP, block)


@pytest.mark.parametrize('place', ['under-a-kept-job', 'inside-a-block-scalar'])
def test_red_proof_a_region_that_holds_no_real_job_is_drift(
    place: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    moved = _moved_region(scratch_repo, place)
    jobs = yaml.safe_load(moved)['jobs']
    assert 'gate' not in jobs  # the markers and the text are intact, the jobs are gone
    write(scratch_repo, CI, moved)
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert 'does not hold the job railform directly under jobs:' in out
    assert run_adopt(capsys)[1] == f'{VERDICT} fail usage'
    assert read(scratch_repo, CI) == moved


def test_red_proof_a_job_named_railform_outside_the_region_is_drift(
    adopted: Fixture, capsys: pytest.CaptureFixture[str]
) -> None:
    text = read(adopted.root, CI)
    extra = '  railform:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo shadow\n'
    write(adopted.root, CI, text + extra)
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert 'a job named railform sits outside the railform marker lines' in out


# --- refusals before any write -----------------------------------------------------------------


def _link_directory(link: Path, target: Path) -> bool:
    """Create `link` as a symlink to `target`, or a junction on Windows; False when neither can."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == 'nt':
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return True
        except (ImportError, AttributeError, OSError):
            pass
    return False


def test_red_proof_a_target_behind_a_link_is_refused_before_any_write(
    scratch_repo: Path, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = build(scratch_repo, 'python-package')
    outside = tmp_path_factory.mktemp('outside')
    if not _link_directory(scratch_repo / '.githooks', outside):
        pytest.skip('this platform cannot create a directory link here')
    assert main(['adopt', '--pin', PIN]) == 2
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f'{VERDICT} fail usage']
    assert 'a symlink or junction lies on the way' in captured.err
    assert list(outside.iterdir()) == []
    assert not (scratch_repo / 'railform.toml').exists()
    for path, text in fixture.original.items():
        assert read(scratch_repo, path) == text


@pytest.mark.parametrize('directory', ['.git/hooks', '.GIT/hooks', '.Git'])
def test_red_proof_a_hooks_directory_inside_git_is_refused_before_any_write(
    directory: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = build(scratch_repo, 'python-package')
    config = f'[project]\ntype = "python-package"\n\n[hooks]\ndir = "{directory}"\n'
    write(scratch_repo, 'railform.toml', config)
    before = snapshot(scratch_repo)
    assert run_adopt(capsys, '--pin', PIN)[:2] == (2, f'{VERDICT} fail usage')
    assert snapshot(scratch_repo) == before
    assert not (scratch_repo / '.git' / 'hooks' / 'pre-commit').exists()
    for path, text in fixture.original.items():
        assert read(scratch_repo, path) == text


# --- staging the wrappers ----------------------------------------------------------------------


def _blob_id(root: Path, data: bytes) -> str:
    done = subprocess.run(
        ['git', 'hash-object', '--no-filters', '--stdin'],
        cwd=root,
        input=data,
        capture_output=True,
        check=True,
    )
    return done.stdout.decode('ascii').strip()


def test_red_proof_wrappers_are_staged_as_exact_bytes_under_autocrlf_and_safecrlf(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(scratch_repo, 'config', 'core.autocrlf', 'true')
    git(scratch_repo, 'config', 'core.safecrlf', 'true')
    build(scratch_repo, 'python-package')
    code, line, out = run_adopt(capsys, '--pin', PIN)
    assert (code, line) == (0, f'{VERDICT} pass ok'), out
    effective = frame.load(scratch_repo)
    for hook in HOOKS:
        data = wrapper.render(effective, hook).encode('utf-8')
        mode, oid = git(scratch_repo, 'ls-files', '-s', '--', f'.githooks/{hook}').split()[:2]
        assert (mode, oid) == ('100755', _blob_id(scratch_repo, data))
        blob = subprocess.run(
            ['git', 'cat-file', 'blob', oid], cwd=scratch_repo, capture_output=True, check=True
        ).stdout
        assert blob == data
        assert b'\r' not in blob
    assert run_adopt(capsys)[2].splitlines()[0] == 'nothing to change'


# --- ci.self_hosted ----------------------------------------------------------------------------


@pytest.mark.parametrize('preset', TYPES)
def test_ci_self_hosted_is_false_in_every_preset(preset: str) -> None:
    effective = frame.effective_frame({'project': {'type': preset}})
    assert effective['ci']['self_hosted'] is False
    assert frame.validate(effective, complete=True) == []


@pytest.mark.parametrize(
    ('body', 'code'),
    [
        ('[ci]\nself_hosted = "yes"\n', 'wrong-type'),
        ('[ci]\nself_hosted = 1\n', 'wrong-type'),
        ('[ci]\nself_hosted = true\n', 'unlisted-departure'),
        (SELF_HOSTED_CONFIG, 'ok'),
    ],
    ids=['string', 'integer', 'no-exceptions-row', 'with-exceptions-row'],
)
def test_ci_self_hosted_is_a_preset_governed_boolean(
    body: str, code: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(scratch_repo, 'railform.toml', body)
    exit_code = main(['frame', '--check'])
    verdict = capsys.readouterr().out.splitlines()[-1]
    assert verdict.endswith(f' {code}')
    assert exit_code == (0 if code == 'ok' else 1)


def test_type_flag_seeds_the_given_type_and_conflicts_with_an_existing_frame(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    assert run_adopt(capsys, '--pin', PIN, '--type', 'python-scripts')[0] == 0
    assert frame.read_config(scratch_repo)['project'] == {'type': 'python-scripts'}
    seeded = read(scratch_repo, 'railform.toml')
    assert run_adopt(capsys, '--type', 'python-package')[1] == f'{VERDICT} fail usage'
    assert run_adopt(capsys, '--type', 'python-scripts')[0] == 0
    assert read(scratch_repo, 'railform.toml') == seeded


_MAJOR, _MINOR = (int(part) for part in NEWEST.split('.'))
ABOVE_NEWEST = f'{_MAJOR}.{_MINOR + 1}'


@pytest.mark.parametrize(
    ('requires', 'versions'),
    [
        ('>=3.12', ['3.12', NEWEST]),
        (f'>={NEWEST}', [NEWEST]),
        ('~=3.11.4', ['3.11', NEWEST]),
        (f'>={ABOVE_NEWEST}', [ABOVE_NEWEST]),
        ('>=3.10,>=3.12', ['3.12', NEWEST]),
    ],
)
def test_the_seed_reads_the_floor_of_requires_python(
    requires: str, versions: list[str], scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(
        scratch_repo, 'pyproject.toml', f'[project]\nname = "x"\nrequires-python = "{requires}"\n'
    )
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    config = frame.read_config(scratch_repo)
    assert config == {'project': {'type': 'python-scripts'}, 'python': {'versions': versions}}


def test_an_empty_src_directory_is_not_a_src_layout(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(scratch_repo, 'pyproject.toml', '[project]\nname = "x"\nrequires-python = ">=3.11"\n')
    (scratch_repo / 'src').mkdir()
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    assert frame.read_config(scratch_repo)['project'] == {'type': 'python-scripts'}


# --- files that do not exist yet ---------------------------------------------------------------


def test_adopt_creates_the_owned_files_when_they_are_missing(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, line, _ = run_adopt(capsys, '--pin', PIN)
    assert (code, line) == (0, f'{VERDICT} pass ok')
    workflow = yaml.safe_load(read(scratch_repo, CI))
    assert list(workflow['jobs']) == ['railform', 'gate']
    assert workflow['jobs']['gate']['needs'] == ['railform']
    assert workflow[True]['pull_request']['types'] == [
        'opened',
        'synchronize',
        'reopened',
        'edited',
    ]
    updates = yaml.safe_load(read(scratch_repo, DEPENDABOT))['updates']
    assert [update['package-ecosystem'] for update in updates] == ['github-actions']
    assert frame.read_config(scratch_repo)['project'] == {'type': 'plugin-marketplace'}
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'


# --- the shapes of the files adopt edits -------------------------------------------------------


@pytest.mark.parametrize(
    ('before', 'after'),
    [
        (
            'on: [push, pull_request]\n',
            f'on:\n  push:\n  pull_request:\n    {PR_TYPES_LINE}\n',
        ),
        ('on: push\n', f'on:\n  push:\n  pull_request:\n    {PR_TYPES_LINE}\n'),
        (
            'on:\n  push:\n',
            f'on:\n  pull_request:\n    {PR_TYPES_LINE}\n  push:\n',
        ),
        (
            'on:\n  pull_request:\n    types:\n      - opened\n      - labeled\n',
            'on:\n  pull_request:\n    types:\n      - opened\n      - labeled\n'
            '      - synchronize\n      - reopened\n      - edited\n',
        ),
        (
            'on:\n  pull_request:\n    types: [edited, opened, synchronize, reopened]\n',
            'on:\n  pull_request:\n    types: [edited, opened, synchronize, reopened]\n',
        ),
    ],
    ids=['flow-list', 'scalar', 'no-pull-request', 'block-types', 'complete-types'],
)
def test_the_pull_request_trigger_is_edited_in_each_shape(
    before: str, after: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs = 'jobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo x\n'
    write(scratch_repo, CI, before + jobs)
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    assert outside_region(read(scratch_repo, CI)) == after + jobs


def test_crlf_files_keep_crlf_everywhere(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = build(scratch_repo, 'python-package')
    for path, text in fixture.original.items():
        write(scratch_repo, path, text.replace('\n', '\r\n'))
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    for path in OWNED_FILES:
        data = (scratch_repo / path).read_bytes()
        assert data.count(b'\n') == data.count(b'\r\n'), path
        text = data.decode('utf-8')
        assert outside_region(text) == fixture.expected[path].replace('\n', '\r\n'), path
    before = snapshot(scratch_repo)
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'
    assert run_adopt(capsys)[0] == 0
    assert snapshot(scratch_repo) == before


def test_a_file_without_a_final_newline_gains_one_only_where_adopt_appends(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = build(scratch_repo, 'plugin-marketplace')
    write(scratch_repo, DEPENDABOT, fixture.original[DEPENDABOT].rstrip('\n'))
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    assert outside_region(read(scratch_repo, DEPENDABOT)) == fixture.expected[DEPENDABOT]


# --- the previous hooks directory --------------------------------------------------------------


def _commit_refused(root: Path, hooks_path: str) -> subprocess.CompletedProcess[str]:
    (root / 'note.txt').write_text('x\n', encoding='utf-8')
    git(root, 'add', '--', 'note.txt')
    return subprocess.run(
        [
            'git',
            '-c',
            f'core.hooksPath={hooks_path}',
            '-c',
            'user.name=someone',
            '-c',
            'user.email=someone@example.invalid',
            'commit',
            '-q',
            '-m',
            'test: a commit',
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_stub_replaces_each_hook_in_the_tracked_previous_directory(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-scripts')  # its hooks lived in .hooks/
    code, _, out = run_adopt(capsys, '--pin', PIN)
    assert code == 0
    for hook in HOOKS:
        path = f'.hooks/{hook}'
        assert f'{path}: stub written' in out
        assert 'railform arm' in read(scratch_repo, path)
        assert git(scratch_repo, 'ls-files', '-s', '--', path).split()[0] == '100755'
    # A clone still armed to the old directory cannot commit, and is told what to run.
    done = _commit_refused(scratch_repo, '.hooks')
    assert done.returncode != 0
    assert 'railform arm' in done.stderr


def test_a_stub_goes_where_the_local_hooks_path_points(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    git(scratch_repo, 'config', '--local', 'core.hooksPath', 'tools/hooks')
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    for hook in HOOKS:
        assert 'railform arm' in read(scratch_repo, f'tools/hooks/{hook}')


def _track(root: Path, path: str, text: str, *, executable: bool) -> None:
    write(root, path, text)
    git(root, 'add', '--', path)
    git(root, 'update-index', '--chmod=+x' if executable else '--chmod=-x', '--', path)


def test_red_proof_a_100644_file_named_after_a_hook_gets_no_stub_and_is_untouched(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    path = 'tests/fixtures/x/pre-commit'
    _track(scratch_repo, path, 'fixture data, not a hook\n', executable=False)
    code, _, out = run_adopt(capsys, '--pin', PIN)
    assert code == 0
    assert 'stub' not in out
    assert read(scratch_repo, path) == 'fixture data, not a hook\n'
    assert git(scratch_repo, 'ls-files', '-s', '--', path).split()[0] == '100644'
    assert not (scratch_repo / 'tests/fixtures/x/commit-msg').exists()


def test_red_proof_a_100755_match_under_a_session_loaded_path_is_not_chosen(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-scripts')  # its hooks lived in .hooks/: one 100755 entry
    plugin_hook = '#!/bin/sh\necho a plugin hook script\n'
    for hook in HOOKS:  # three 100755 entries under skills/, which a session loads
        _track(scratch_repo, f'skills/demo/hooks-old/{hook}', plugin_hook, executable=True)
    code, _, out = run_adopt(capsys, '--pin', PIN)
    assert code == 0
    assert '.hooks/pre-commit: stub written' in out
    for hook in HOOKS:
        assert read(scratch_repo, f'skills/demo/hooks-old/{hook}') == plugin_hook


def test_a_session_loaded_match_alone_gives_no_stub(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-package')
    _track(scratch_repo, 'plugins/x/hooks/pre-commit', '#!/bin/sh\n', executable=True)
    code, _, out = run_adopt(capsys, '--pin', PIN)
    assert code == 0
    assert 'stub' not in out
    assert read(scratch_repo, 'plugins/x/hooks/pre-commit') == '#!/bin/sh\n'


def test_a_tracked_non_executable_file_in_the_previous_directory_is_reported_and_left(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-scripts')
    _track(scratch_repo, '.hooks/commit-msg', 'notes about the hooks\n', executable=False)
    code, _, out = run_adopt(capsys, '--pin', PIN)
    assert code == 0
    assert '.hooks/commit-msg: tracked as 100644, not a hook wrapper; left as it is' in out
    assert read(scratch_repo, '.hooks/commit-msg') == 'notes about the hooks\n'
    assert git(scratch_repo, 'ls-files', '-s', '--', '.hooks/commit-msg').startswith('100644')
    assert 'railform arm' in read(scratch_repo, '.hooks/pre-commit')


def test_no_stub_without_a_previous_directory(adopted: Fixture) -> None:
    if adopted.project_type == 'python-scripts':
        pytest.skip('this fixture had a previous hooks directory')
    hooks = {
        line.rsplit('/', 1)[-1]
        for line in git(adopted.root, 'ls-files').splitlines()
        if not line.startswith('.githooks/')
    }
    assert not hooks & set(HOOKS)


def _file_mode_is_true(root: Path) -> bool:
    done = subprocess.run(
        ['git', 'config', '--bool', 'core.fileMode'],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() == 'true'


def test_a_later_git_add_keeps_the_wrappers_executable(adopted: Fixture) -> None:
    if os.name == 'nt' and _file_mode_is_true(adopted.root):
        pytest.skip('NTFS has no executable bit, so git add reads every file as 100644')
    git(adopted.root, 'add', '-A')
    for hook in HOOKS:
        mode = git(adopted.root, 'ls-files', '-s', '--', f'.githooks/{hook}').split()[0]
        assert mode == '100755'


@pytest.mark.skipif(os.name == 'nt', reason='the working-tree executable bit exists on POSIX only')
def test_the_wrappers_are_executable_in_the_working_tree(adopted: Fixture) -> None:
    for hook in HOOKS:
        assert os.access(adopted.root / '.githooks' / hook, os.X_OK)


# --- self-hosting ------------------------------------------------------------------------------

_SELF_HOSTED_CI = """\
name: ci
on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened, edited]
permissions:
  contents: read
jobs:
  # >>> railform: owned by `railform adopt`; `railform adopt --check` fails on any edit
  railform:
    uses: ./.github/workflows/python-ci.yml
  # <<< railform
"""


def test_a_self_hosted_repository_keeps_its_local_call(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(scratch_repo, 'pyproject.toml', '[project]\nname = "x"\nrequires-python = ">=3.11"\n')
    (scratch_repo / 'src' / 'x').mkdir(parents=True)
    (scratch_repo / 'src' / 'x' / '__init__.py').write_text('', encoding='utf-8')
    write(scratch_repo, 'railform.toml', SELF_HOSTED_CONFIG)
    write(scratch_repo, CI, _SELF_HOSTED_CI)
    code, line, _ = run_adopt(capsys)
    assert (code, line) == (0, f'{VERDICT} pass ok')
    job = yaml.safe_load(read(scratch_repo, CI))['jobs']['railform']
    assert job['uses'] == './.github/workflows/python-ci.yml'
    assert job['with'] == {
        'railform-sha': '${{ github.event.pull_request.head.sha || github.sha }}'
    }
    repos = yaml.safe_load(read(scratch_repo, PRE_COMMIT))['repos']
    assert repos[0]['repo'] == 'local'
    entries = {hook['id']: hook['entry'] for hook in repos[0]['hooks']}
    assert entries['railform-commit-msg'] == 'uv run --frozen railform commit-msg'
    assert 'grimaldost/railform' not in region(read(scratch_repo, CI))
    # --pin rewrites nothing in a self-hosted repository.
    before = snapshot(scratch_repo)
    code, _, out = run_adopt(capsys, '--pin', PIN)
    assert code == 0
    assert '--pin rewrites nothing' in out
    assert snapshot(scratch_repo) == before
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (0, f'{VERDICT} pass ok')
    assert 'self-hosted' in out
    assert 'pin comparisons are skipped' in out


def test_a_self_hosted_check_still_finds_drift(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(scratch_repo, 'railform.toml', SELF_HOSTED_CONFIG)
    write(scratch_repo, CI, _SELF_HOSTED_CI)
    assert run_adopt(capsys)[0] == 0
    text = read(scratch_repo, CI)
    write(scratch_repo, CI, text.replace('    if: always()\n', ''))
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} fail drift'


def test_red_proof_a_local_path_call_without_ci_self_hosted_is_drift(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Adopted self-hosted, then the frame key removed: the local call is no longer allowed.
    write(scratch_repo, 'railform.toml', SELF_HOSTED_CONFIG)
    write(scratch_repo, CI, _SELF_HOSTED_CI)
    assert run_adopt(capsys)[0] == 0
    assert run_adopt(capsys, '--check')[1] == f'{VERDICT} pass ok'
    write(scratch_repo, 'railform.toml', '[project]\ntype = "python-package"\n')
    code, line, out = run_adopt(capsys, '--check')
    assert (code, line) == (1, f'{VERDICT} fail drift')
    assert 'needs ci.self_hosted = true' in out
    # Without the key adopt does not treat the local call as self-hosting: it wants a pin.
    assert run_adopt(capsys)[1] == f'{VERDICT} fail usage'


# --- the stub runs where a shell exists --------------------------------------------------------


@pytest.mark.skipif(shutil.which('sh') is None, reason='no POSIX shell on this machine')
def test_the_stub_prints_the_arm_command_and_fails(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build(scratch_repo, 'python-scripts')
    assert run_adopt(capsys, '--pin', PIN)[0] == 0
    sh = shutil.which('sh')
    assert sh is not None
    done = subprocess.run(
        [sh, '.hooks/pre-commit'], cwd=scratch_repo, capture_output=True, text=True, check=False
    )
    assert done.returncode == 1
    assert 'railform arm' in done.stderr
    assert 'git config core.hooksPath .githooks' in done.stderr
