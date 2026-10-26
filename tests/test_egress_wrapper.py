"""The egress extension point of the `pre-push` and `pre-commit` hook wrappers.

An adopter repository is written by `railform adopt` and armed by `railform arm`, and pushes to
an empty bare remote with real `git push` and `git commit`. The egress hook is named by the git
configuration key `railform.egressHook`, set in a global configuration file of the test's own, or
by the variable `RAILFORM_EGRESS_HOOK`. The adopter's `.pre-commit-config.yaml` is replaced by one
local hook at the `pre-push` stage that records what pre-commit was handed, so no hook
environment is built.

The red proofs, each named in its test:

1. a key naming a script that exits 1 refuses a two-branch `git push --all`, and the script saw
   the remote URL and every input line
   (`test_red_proof_a_refusing_hook_refuses_a_push_of_every_branch`);
2. a first push, which includes the root commit, reaches the script with its ref line
   (`test_red_proof_a_first_push_reaches_the_hook_with_the_root_commit_s_ref_line`);
3. a key naming a missing file refuses the push, the wrapper exits 2 and prints the path
   (`test_red_proof_a_hook_path_that_is_not_a_file_refuses_the_push_with_exit_2`);
4. a commit runs the script with `pre-commit` and goes on when it exits 1
   (`test_red_proof_a_commit_runs_the_hook_and_goes_on_when_it_exits_1`).

The wrappers before the extension point ran no egress hook, so each of these fails there. The
no-regression tests hold with or without it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from hook_isolation import DROPPED, hook_environment
from migration_fixtures import PIN, PRE_COMMIT
from railform import frame, wrapper
from railform.cli import main

TIMEOUT = 600
Z40 = '0' * 40
HOOKS = ('pre-commit', 'commit-msg', 'pre-push')
VARIABLE = 'RAILFORM_EGRESS_HOOK'
KEY = 'railform.egressHook'
STAGE_RECORD = 'RAILFORM_TEST_STAGE_RECORD'
STAGE_KEYS = (
    'PRE_COMMIT_FROM_REF',
    'PRE_COMMIT_TO_REF',
    'PRE_COMMIT_LOCAL_BRANCH',
    'PRE_COMMIT_REMOTE_BRANCH',
    'PRE_COMMIT_REMOTE_NAME',
    'PRE_COMMIT_REMOTE_URL',
)
# One local hook at the pre-push stage that writes down what pre-commit handed it.
LOCAL_CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: record-stage
        name: record the pre-push stage
        entry: uv run --no-project python record_stage.py
        language: unsupported
        stages: [pre-push]
        always_run: true
        pass_filenames: false
"""
RECORD_STAGE = f"""\
import json
import os

path = os.environ.get({STAGE_RECORD!r})
if path:
    with open(path, 'a', encoding='utf-8') as file:
        file.write(json.dumps({{key: os.environ.get(key) for key in {STAGE_KEYS!r}}}) + '\\n')
"""
# What an egress hook writes down for each run: its arguments, its input, and the temporary
# directory's entries while it runs.
EGRESS_SCRIPT = """\
import json
import os
import sys

data = sys.stdin.buffer.read().decode('utf-8')
temporary = os.environ.get('TMPDIR')
seen = sorted(os.listdir(temporary)) if temporary and os.path.isdir(temporary) else None
with open({record!r}, 'a', encoding='utf-8') as file:
    file.write(json.dumps({{'argv': sys.argv[1:], 'stdin': data, 'tmp': seen}}) + '\\n')
sys.exit({code})
"""


@dataclass
class Adopter:
    """An adopted, armed repository with an empty bare `origin`, and the environment its git
    commands run in."""

    root: Path
    url: str
    global_config: Path
    temporary: Path
    env: dict[str, str]


@dataclass
class Egress:
    """An egress hook script and the file it records its runs in."""

    path: Path
    record: Path

    def runs(self) -> list[dict[str, object]]:
        if not self.record.exists():
            return []
        lines = self.record.read_text(encoding='utf-8').splitlines()
        return [json.loads(line) for line in lines]


def _run(
    cwd: Path, command: list[str], env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=TIMEOUT,
        check=False,
    )


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    done = _run(cwd, ['git', *args], env)
    assert done.returncode == 0, (args, done.stdout, done.stderr)
    return done.stdout.strip()


def _executables(root: Path) -> list[str]:
    entries = _git(root, 'ls-files', '-s').splitlines()
    return sorted(line.split('\t', 1)[1] for line in entries if line.startswith('100755 '))


def _commit_quietly(root: Path, name: str, message: str) -> str:
    """Commit one file without running the hooks; return the new commit."""
    (root / name).write_bytes(f'{message}\n'.encode())
    _git(root, 'add', '--', name)
    _git(root, 'commit', '-q', '--no-verify', '-m', message)
    return _git(root, 'rev-parse', 'HEAD')


@pytest.fixture
def adopter(
    scratch_repo: Path,
    tmp_path: Path,
    outside: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> Adopter:
    root = scratch_repo
    for key, value in (
        ('user.name', 'someone'),
        ('user.email', 'someone@example.com'),
        ('commit.gpgsign', 'false'),
        ('init.defaultBranch', 'main'),
    ):
        _git(root, 'config', key, value)
    _git(root, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    (root / 'README.md').write_bytes(b'# zebrafrog\n')
    assert main(['adopt', '--pin', PIN]) == 0
    (root / PRE_COMMIT).write_bytes(LOCAL_CONFIG.encode('utf-8'))
    (root / 'record_stage.py').write_bytes(RECORD_STAGE.encode('utf-8'))
    executables = _executables(root)
    assert {f'.githooks/{hook}' for hook in HOOKS} <= set(executables), executables
    excluded = [f':(exclude,literal){path}' for path in executables]
    _git(root, 'add', '-A', '--', '.', *excluded)
    _git(root, 'commit', '-q', '--no-verify', '-m', 'chore: adopt railform')
    bare = tmp_path / 'remote.git'
    _git(tmp_path, 'init', '-q', '--bare', str(bare))
    url = bare.as_posix()
    _git(root, 'remote', 'add', 'origin', url)
    assert main(['arm']) == 0
    assert main(['hooks-check', '--armed']) == 0
    capsys.readouterr()
    global_config = tmp_path / 'global.gitconfig'
    global_config.write_bytes(b'')
    temporary = tmp_path / 'tmp'
    temporary.mkdir()
    env = hook_environment(outside) | {
        'GIT_CONFIG_GLOBAL': str(global_config),
        'TMPDIR': str(temporary),
        STAGE_RECORD: str(tmp_path / 'stage.jsonl'),
    }
    return Adopter(root, url, global_config, temporary, env)


def _egress(directory: Path, name: str, code: int) -> Egress:
    path = directory / f'{name}.py'
    record = directory / f'{name}.jsonl'
    path.write_bytes(EGRESS_SCRIPT.format(record=str(record), code=code).encode('utf-8'))
    return Egress(path, record)


def _set_key(adopter: Adopter, value: str) -> None:
    """Name the egress hook in the global git configuration, as the environment does."""
    _git(adopter.root, 'config', '--file', str(adopter.global_config), KEY, value)


def _stage_runs(adopter: Adopter) -> list[dict[str, str | None]]:
    record = Path(adopter.env[STAGE_RECORD])
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]


def _remote_refs(adopter: Adopter) -> str:
    return _git(adopter.root, 'ls-remote', adopter.url)


def _own_lines(done: subprocess.CompletedProcess[str]) -> list[str]:
    """The lines the wrappers print of their own."""
    return [line for line in (done.stdout + done.stderr).splitlines() if 'railform:' in line]


# --- the red proofs ----------------------------------------------------------------------------


@pytest.mark.slow
def test_red_proof_a_refusing_hook_refuses_a_push_of_every_branch(
    adopter: Adopter, tmp_path: Path
) -> None:
    root = adopter.root
    main_sha = _git(root, 'rev-parse', 'HEAD')
    _git(root, 'switch', '-q', '-c', 'feature')
    feature_sha = _commit_quietly(root, 'feature.md', 'docs: add a feature note')
    _git(root, 'switch', '-q', 'main')
    hook = _egress(tmp_path, 'refuse', 1)
    _set_key(adopter, str(hook.path))

    done = _run(root, ['git', 'push', '--all', 'origin'], adopter.env)

    assert done.returncode != 0, done.stdout + done.stderr
    [run] = hook.runs()
    assert run['argv'] == ['pre-push', 'origin', adopter.url]
    stdin = run['stdin']
    assert isinstance(stdin, str) and stdin.endswith('\n')
    assert sorted(stdin.splitlines()) == sorted(
        [
            f'refs/heads/main {main_sha} refs/heads/main {Z40}',
            f'refs/heads/feature {feature_sha} refs/heads/feature {Z40}',
        ]
    )
    assert _own_lines(done) == [f'railform: egress hook {hook.path} refused the push (exit 1)']
    assert _remote_refs(adopter) == ''
    assert _stage_runs(adopter) == []
    # The saved input was in the temporary directory while the hook ran, and is gone after.
    assert run['tmp'], run
    assert list(adopter.temporary.iterdir()) == []


@pytest.mark.slow
def test_red_proof_a_first_push_reaches_the_hook_with_the_root_commit_s_ref_line(
    adopter: Adopter, tmp_path: Path
) -> None:
    root = adopter.root
    root_sha = _git(root, 'rev-parse', 'HEAD')
    assert _git(root, 'rev-list', '--max-parents=0', 'HEAD') == root_sha
    hook = _egress(tmp_path, 'allow', 0)
    _set_key(adopter, str(hook.path))

    done = _run(root, ['git', 'push', 'origin', 'main'], adopter.env)

    assert done.returncode == 0, done.stdout + done.stderr
    [run] = hook.runs()
    assert run['argv'] == ['pre-push', 'origin', adopter.url]
    assert run['stdin'] == f'refs/heads/main {root_sha} refs/heads/main {Z40}\n'
    assert _remote_refs(adopter) == f'{root_sha}\trefs/heads/main'
    # The hook let the push go on, and pre-commit's stage ran for the same ref.
    [stage] = _stage_runs(adopter)
    assert (
        stage['PRE_COMMIT_LOCAL_BRANCH'] == stage['PRE_COMMIT_REMOTE_BRANCH'] == 'refs/heads/main'
    )
    assert _own_lines(done) == []
    assert list(adopter.temporary.iterdir()) == []


@pytest.mark.slow
def test_red_proof_a_hook_path_that_is_not_a_file_refuses_the_push_with_exit_2(
    adopter: Adopter, tmp_path: Path
) -> None:
    root = adopter.root
    missing = tmp_path / 'no-such-hook.py'
    _set_key(adopter, str(missing))
    line = f'railform: egress hook {missing} is not a file'

    done = _run(root, ['git', 'push', 'origin', 'main'], adopter.env)

    assert done.returncode != 0, done.stdout + done.stderr
    assert _own_lines(done) == [line]
    assert _remote_refs(adopter) == ''
    assert _stage_runs(adopter) == []
    # git reports a refused push as its own failure; the wrapper's exit status is read through
    # `git hook run`, which runs the armed wrapper and exits with its status.
    ran = _run(root, ['git', 'hook', 'run', 'pre-push', '--', 'origin', adopter.url], adopter.env)
    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert _own_lines(ran) == [line]


@pytest.mark.slow
def test_red_proof_a_commit_runs_the_hook_and_goes_on_when_it_exits_1(
    adopter: Adopter, tmp_path: Path
) -> None:
    root = adopter.root
    before = _git(root, 'rev-parse', 'HEAD')
    hook = _egress(tmp_path, 'refuse', 1)
    _set_key(adopter, str(hook.path))
    (root / 'note.md').write_bytes(b'A note.\n')
    _git(root, 'add', '--', 'note.md')

    done = _run(root, ['git', 'commit', '-q', '-m', 'docs: add a note'], adopter.env)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(root, 'rev-parse', 'HEAD^') == before
    assert hook.runs() == [{'argv': ['pre-commit'], 'stdin': '', 'tmp': []}]
    assert _own_lines(done) == []


# --- what the extension point adds besides ------------------------------------------------------


@pytest.mark.slow
def test_a_variable_that_is_not_empty_overrides_the_key(adopter: Adopter, tmp_path: Path) -> None:
    refusing = _egress(tmp_path, 'refuse', 1)
    allowing = _egress(tmp_path, 'allow', 0)
    _set_key(adopter, str(refusing.path))
    env = adopter.env | {VARIABLE: str(allowing.path)}

    done = _run(adopter.root, ['git', 'push', 'origin', 'main'], env)

    assert done.returncode == 0, done.stdout + done.stderr
    assert refusing.runs() == []
    [run] = allowing.runs()
    assert run['argv'] == ['pre-push', 'origin', adopter.url]


@pytest.mark.slow
def test_an_empty_variable_leaves_the_key_in_charge(adopter: Adopter, tmp_path: Path) -> None:
    refusing = _egress(tmp_path, 'refuse', 1)
    _set_key(adopter, str(refusing.path))
    env = adopter.env | {VARIABLE: ''}

    done = _run(adopter.root, ['git', 'push', 'origin', 'main'], env)

    assert done.returncode != 0
    assert len(refusing.runs()) == 1
    assert _remote_refs(adopter) == ''


@pytest.mark.slow
def test_the_saved_input_reaches_pre_commit_s_stage_unchanged(
    adopter: Adopter, tmp_path: Path
) -> None:
    root = adopter.root
    old = _git(root, 'rev-parse', 'HEAD')
    _git(root, 'push', '-q', '--no-verify', 'origin', 'main', env=adopter.env)
    new = _commit_quietly(root, 'second.md', 'docs: add a second note')
    hook = _egress(tmp_path, 'allow', 0)
    _set_key(adopter, str(hook.path))

    done = _run(root, ['git', 'push', 'origin', 'main'], adopter.env)

    assert done.returncode == 0, done.stdout + done.stderr
    [run] = hook.runs()
    assert run['stdin'] == f'refs/heads/main {new} refs/heads/main {old}\n'
    [stage] = _stage_runs(adopter)
    assert stage == {
        'PRE_COMMIT_FROM_REF': old,
        'PRE_COMMIT_TO_REF': new,
        'PRE_COMMIT_LOCAL_BRANCH': 'refs/heads/main',
        'PRE_COMMIT_REMOTE_BRANCH': 'refs/heads/main',
        'PRE_COMMIT_REMOTE_NAME': 'origin',
        'PRE_COMMIT_REMOTE_URL': adopter.url,
    }
    assert _remote_refs(adopter) == f'{new}\trefs/heads/main'


# --- no regression -------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize('variable', [None, ''], ids=['unset', 'empty'])
def test_with_no_hook_named_a_push_goes_through_and_the_wrapper_prints_nothing(
    adopter: Adopter, variable: str | None
) -> None:
    env = adopter.env if variable is None else adopter.env | {VARIABLE: variable}
    assert VARIABLE not in adopter.env

    done = _run(adopter.root, ['git', 'push', 'origin', 'main'], env)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _own_lines(done) == []
    assert 'egress' not in done.stdout + done.stderr
    [stage] = _stage_runs(adopter)
    assert stage['PRE_COMMIT_LOCAL_BRANCH'] == 'refs/heads/main'
    assert _remote_refs(adopter).endswith('\trefs/heads/main')


def _commit_with_a_missing_hook(
    adopter: Adopter, missing: Path
) -> subprocess.CompletedProcess[str]:
    root = adopter.root
    _set_key(adopter, str(missing))
    before = _git(root, 'rev-parse', 'HEAD')
    (root / 'note.md').write_bytes(b'A note.\n')
    _git(root, 'add', '--', 'note.md')
    done = _run(root, ['git', 'commit', '-q', '-m', 'docs: add a note'], adopter.env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(root, 'rev-parse', 'HEAD^') == before
    return done


@pytest.mark.slow
def test_a_hook_path_that_is_not_a_file_lets_a_commit_through(
    adopter: Adopter, tmp_path: Path
) -> None:
    _commit_with_a_missing_hook(adopter, tmp_path / 'no-such-hook.py')


@pytest.mark.slow
def test_a_hook_path_that_is_not_a_file_is_reported_in_one_line_at_commit(
    adopter: Adopter, tmp_path: Path
) -> None:
    missing = tmp_path / 'no-such-hook.py'
    done = _commit_with_a_missing_hook(adopter, missing)
    assert _own_lines(done) == [
        f'railform: egress hook {missing} is not a file; the commit goes on'
    ]


# --- the text ------------------------------------------------------------------------------------

COMMANDS = (
    'uv run --frozen python -m pre_commit hook-impl',
    'uvx --from pre-commit=={version} python -m pre_commit hook-impl',
)


def _released_wrapper(hook: str, command: str) -> str:
    """The wrapper text before the extension point, written out apart from the renderer."""
    return (
        '#!/bin/sh\n'
        f'# Written by railform adopt: runs the {hook} stage of .pre-commit-config.yaml.\n'
        '# `railform hooks-check` fails if this file is edited; arm a clone with `railform arm`.\n'
        'HERE="$(cd "$(dirname "$0")" && pwd)"\n'
        f'exec {command} --config=.pre-commit-config.yaml --hook-type={hook} '
        '--hook-dir "$HERE" -- "$@"\n'
    )


@pytest.mark.parametrize('project_type', frame.PROJECT_TYPES)
def test_the_commit_msg_wrapper_is_unchanged(project_type: str) -> None:
    effective = frame.effective_frame({'project': {'type': project_type}})
    command = effective['hooks']['wrapper']
    assert command in [
        text.format(version=effective['hooks']['pre_commit_version']) for text in COMMANDS
    ]
    assert wrapper.render(effective, 'commit-msg') == _released_wrapper('commit-msg', command)


@pytest.mark.parametrize('hook', ['pre-push', 'pre-commit'])
def test_the_pre_push_and_pre_commit_wrappers_name_the_variable_and_the_key(hook: str) -> None:
    effective = frame.effective_frame(None)
    text = wrapper.render(effective, hook)
    released = _released_wrapper(hook, effective['hooks']['wrapper'])
    assert text.splitlines()[:4] == released.splitlines()[:4]
    assert '$RAILFORM_EGRESS_HOOK' in text
    assert 'git config --get railform.egressHook' in text
    assert f'uv run --no-project python "$EGRESS" {hook}' in text
    assert '\r' not in text and text.endswith('\n') and not text.endswith('\n\n')


def test_the_hook_isolation_drops_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VARIABLE, 'zebrafrog.py')
    assert VARIABLE in DROPPED
    assert VARIABLE not in hook_environment({})
