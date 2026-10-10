"""The hooks of `.pre-commit-hooks.yaml`, run by real `git commit` and `git push` in armed clones
of the migration fixtures (development-rails spec, §8 and its round-6 amendment).

For each project type the migration fixture of §7 is built and adopted, and its railform entry
in `.pre-commit-config.yaml` is pointed at a git repository holding this working tree as one
commit (`repo:` a local path, `rev:` that commit), so pre-commit installs the hooks from the
code under test. Each test then clones the fixture's bare copy, arms the clone with the
wrappers adopt wrote (`railform arm`), and commits or pushes there; `origin` is the bare copy.

pre-commit runs no `pre-push` hook for a ref whose commits are all on the remote already. The
amendment withdrew the clause that asked the rail to refuse such a push, and
`test_the_limit_a_push_of_foo_with_no_new_commit_is_accepted` asserts the limit, so that an
upgrade of pre-commit that changes it is noticed.

The first run needs the network: pre-commit clones the hygiene hooks from GitHub and builds
each hook environment with pip, and uv resolves the fixtures' tools. These tests are marked
`slow` and run by default, in CI too. Git is isolated by `tests/conftest.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from migration_fixtures import PIN, PRE_COMMIT, TYPES, build
from railform.cli import main

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 600
RAILFORM_REPO_LINE = 'repo: https://github.com/grimaldost/railform'
IDENTITY = {
    'user.name': 'Fixture',
    'user.email': 'fixture@example.com',
    'commit.gpgsign': 'false',
}
DEPENDABOT_BRANCH = 'dependabot/github_actions/actions/checkout-4.2.2'
NOTE = 'notes.md'
HOOKS = ('pre-commit', 'commit-msg', 'pre-push')
# Variables that would point uv at railform's own environment, or make pre-commit skip hooks.
DROPPED = {'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC', 'SKIP'}


def _environment(pre_commit_home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in DROPPED}
    env.pop('PRE_COMMIT_REMOTE_BRANCH', None)
    env['PRE_COMMIT_HOME'] = str(pre_commit_home)
    return env


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


def _identify(repo: Path) -> None:
    for key, value in IDENTITY.items():
        _git(repo, 'config', key, value)


@pytest.fixture(scope='session')
def pre_commit_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """pre-commit's cache for this session: each hook environment is built once."""
    return tmp_path_factory.mktemp('pre-commit-home')


@pytest.fixture(scope='session')
def _session_cache() -> dict[str, object]:
    return {}


@pytest.fixture
def railform_under_test(
    tmp_path_factory: pytest.TempPathFactory, _session_cache: dict[str, object]
) -> tuple[Path, str]:
    """A git repository whose one commit is this working tree, and that commit.

    The working tree, not HEAD: the hooks run the code being tested even before it is
    committed. The files are the ones git would track (tracked or untracked, not ignored).
    """
    if 'railform' not in _session_cache:
        snapshot = tmp_path_factory.mktemp('railform-under-test')
        listed = _run(ROOT, ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'])
        assert listed.returncode == 0, listed.stderr
        for name in sorted(set(filter(None, listed.stdout.split('\0')))):
            source = ROOT / name
            if source.is_file():
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        _git(snapshot, 'init', '-q')
        _identify(snapshot)
        _git(snapshot, 'add', '-A')
        _git(snapshot, 'commit', '-q', '--no-verify', '-m', 'test: the railform under test')
        _session_cache['railform'] = (snapshot, _git(snapshot, 'rev-parse', 'HEAD'))
    return _session_cache['railform']  # type: ignore[return-value]


@pytest.fixture
def fixture_origin(
    tmp_path_factory: pytest.TempPathFactory,
    pre_commit_home: Path,
    railform_under_test: tuple[Path, str],
    _session_cache: dict[str, object],
) -> Callable[[str], Path]:
    """Build, once per session and type, the adopted migration fixture as a committed repository
    whose railform hooks come from the railform under test."""

    def origin(project_type: str) -> Path:
        key = f'origin:{project_type}'
        if key not in _session_cache:
            _session_cache[key] = _build_origin(
                tmp_path_factory.mktemp(f'origin-{project_type}'),
                project_type,
                railform_under_test,
                pre_commit_home,
            )
        return _session_cache[key]  # type: ignore[return-value]

    return origin


def _build_origin(
    root: Path, project_type: str, railform: tuple[Path, str], pre_commit_home: Path
) -> Path:
    _git(root, 'init', '-q', '-b', 'main')
    _identify(root)
    build(root, project_type)
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(root)
        assert main(['adopt', '--pin', PIN]) == 0
    config = root / PRE_COMMIT
    text = config.read_text(encoding='utf-8')
    path, sha = railform
    assert text.count(RAILFORM_REPO_LINE) == 1 and text.count(f'rev: {PIN}') == 1, text
    text = text.replace(RAILFORM_REPO_LINE, f"repo: '{path.as_posix()}'")
    text = text.replace(f'rev: {PIN}', f'rev: {sha}')
    config.write_bytes(text.encode('utf-8'))
    if project_type != 'plugin-marketplace':
        # The wrapper runs pre-commit from the fixture's own locked dev group.
        locked = _run(root, ['uv', 'lock'], _environment(pre_commit_home))
        assert locked.returncode == 0, locked.stderr
    # Stage the rest, never re-staging from the working tree what adopt staged as executable:
    # its index entries are the ones a clone checks out, whatever the working tree's mode reads.
    executables = _executables(root)
    assert {f'.githooks/{hook}' for hook in HOOKS} <= executables, executables
    excluded = [f':(exclude,literal){path}' for path in sorted(executables)]
    _git(root, 'add', '-A', '--', '.', *excluded)
    _git(root, 'commit', '-q', '--no-verify', '-m', 'chore: adopt railform')
    assert _executables(root) == executables
    return root


def _executables(root: Path) -> set[str]:
    """The paths the git index tracks as mode 100755."""
    entries = _git(root, 'ls-files', '-s').splitlines()
    return {line.split('\t', 1)[1] for line in entries if line.startswith('100755 ')}


@pytest.fixture(params=TYPES)
def clone(
    request: pytest.FixtureRequest,
    fixture_origin: Callable[[str], Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """A fresh clone of the adopted fixture, armed, whose `origin` is a local bare copy."""
    origin = fixture_origin(request.param)
    bare = tmp_path / 'remote.git'
    work = tmp_path / 'clone'
    _git(tmp_path, 'clone', '-q', '--bare', str(origin), str(bare))
    _git(tmp_path, 'clone', '-q', str(bare), str(work))
    _identify(work)
    monkeypatch.chdir(work)
    assert main(['arm']) == 0
    assert main(['hooks-check', '--armed']) == 0
    return work


@pytest.fixture
def env(pre_commit_home: Path) -> dict[str, str]:
    return _environment(pre_commit_home)


def _commit(
    repo: Path, env: dict[str, str], message: str, name: str = NOTE, text: str = ''
) -> subprocess.CompletedProcess[str]:
    """Stage one file and run a real `git commit`, hooks and all."""
    (repo / name).write_bytes((text or f'A note for {message}.\n').encode('utf-8'))
    _git(repo, 'add', '--', name)
    return _run(repo, ['git', 'commit', '-q', '-m', message], env)


def _committed(repo: Path, env: dict[str, str], message: str) -> None:
    done = _commit(repo, env, message)
    assert done.returncode == 0, done.stdout + done.stderr


def _head(repo: Path) -> str:
    return _git(repo, 'rev-parse', 'HEAD')


def _remote_has(repo: Path, branch: str) -> bool:
    return bool(_git(repo, 'ls-remote', '--heads', 'origin', f'refs/heads/{branch}'))


def _push(repo: Path, env: dict[str, str], refspec: str) -> subprocess.CompletedProcess[str]:
    return _run(repo, ['git', 'push', '-q', 'origin', refspec], env)


# --- commit --------------------------------------------------------------------------------------


def test_red_proof_a_non_conventional_message_is_refused(clone: Path, env: dict[str, str]) -> None:
    before = _head(clone)
    refused = _commit(clone, env, 'add a note')
    assert refused.returncode != 0
    assert 'railform-verdict: commit-msg fail subject' in refused.stdout + refused.stderr
    assert _head(clone) == before
    # The same change with a conventional subject goes through every hook of the commit.
    _committed(clone, env, 'docs: add a note')
    assert _head(clone) != before


def test_red_proof_a_staged_merge_conflict_marker_is_refused(
    clone: Path, env: dict[str, str]
) -> None:
    before = _head(clone)
    marked = '<<<<<<< ours\nleft\n=======\nright\n>>>>>>> theirs\n'
    refused = _commit(clone, env, 'docs: keep both sides', text=marked)
    assert refused.returncode != 0
    assert '- hook id: check-merge-conflict' in refused.stdout + refused.stderr
    assert _head(clone) == before


def test_red_proof_a_staged_home_path_is_refused_and_only_staged_files_are_judged(
    clone: Path, env: dict[str, str]
) -> None:
    home = '/' + 'home' + '/' + 'sam' + '/notes'
    # A tracked file that already holds one, committed past the hooks, is not judged again.
    (clone / 'old.md').write_bytes(f'Kept in {home}.\n'.encode())
    _git(clone, 'add', '--', 'old.md')
    _git(clone, 'commit', '-q', '--no-verify', '-m', 'docs: an old note')
    _committed(clone, env, 'docs: add a note')
    before = _head(clone)
    refused = _commit(clone, env, 'docs: add another note', name='new.md', text=f'See {home}.\n')
    assert refused.returncode != 0
    assert 'railform-verdict: private-refs fail home-path' in refused.stdout + refused.stderr
    assert _head(clone) == before


# --- push ----------------------------------------------------------------------------------------


def test_red_proof_a_push_of_foo_with_a_new_commit_is_refused(
    clone: Path, env: dict[str, str]
) -> None:
    _git(clone, 'switch', '-q', '-c', 'foo')
    _committed(clone, env, 'docs: add a note')
    refused = _push(clone, env, 'foo')
    assert refused.returncode != 0
    assert 'railform-verdict: branch-name fail branch' in refused.stdout + refused.stderr
    assert not _remote_has(clone, 'foo')


def test_red_proof_a_valid_branch_pushed_as_foo_is_refused(
    clone: Path, env: dict[str, str]
) -> None:
    _git(clone, 'switch', '-q', '-c', 'docs/a-note')
    _committed(clone, env, 'docs: add a note')
    refused = _push(clone, env, 'docs/a-note:foo')
    assert refused.returncode != 0
    assert 'railform-verdict: branch-name fail branch' in refused.stdout + refused.stderr
    assert not _remote_has(clone, 'foo')
    # The same commit under its own, valid name is accepted: the remote name was the reason.
    accepted = _push(clone, env, 'docs/a-note')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert _remote_has(clone, 'docs/a-note')


def test_the_limit_a_push_of_foo_with_no_new_commit_is_accepted(
    clone: Path, env: dict[str, str]
) -> None:
    # Every commit of foo is on origin/main already, so pre-commit runs no pre-push hook at all.
    # The spec's round-6 amendment records this limit; the commit-lane job of the reusable
    # workflow judges the head branch of every pull request instead.
    _git(clone, 'branch', 'foo')
    accepted = _push(clone, env, 'foo')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert 'railform-verdict' not in accepted.stdout + accepted.stderr
    assert _remote_has(clone, 'foo')


def test_red_proof_a_dependabot_branch_with_a_new_commit_is_accepted(
    clone: Path, env: dict[str, str]
) -> None:
    _git(clone, 'switch', '-q', '-c', DEPENDABOT_BRANCH)
    _committed(clone, env, 'ci: bump actions/checkout')
    accepted = _push(clone, env, DEPENDABOT_BRANCH)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert _remote_has(clone, DEPENDABOT_BRANCH)
