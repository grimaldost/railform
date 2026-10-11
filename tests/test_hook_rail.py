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

Every session needs the network: `PRE_COMMIT_HOME` is fresh each session, so pre-commit clones
the hygiene hooks from GitHub and builds each hook environment with pip, and uv resolves the
fixtures' tools. Without the network every test fails at once with one message; run
`pytest -m "not slow"` offline. These tests are marked `slow` and run by default, in CI too.

Isolation. Git is isolated by `tests/conftest.py`. pre-commit runs its own clones without
`GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM`, so the hooks also run with `HOME`, `USERPROFILE`
and `XDG_CONFIG_HOME` pointing at a temporary directory that holds an empty `.gitconfig`. The
system git configuration still reaches pre-commit's clones. Outside the temporary directories
the tests write caches only: pip's cache and virtualenv's app-data where the platform keeps them
outside the home directory, and uv's cache and managed Pythons, pinned to the developer's own
(`UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`) so uv stays warm.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pre_commit.git import no_git_env

from migration_fixtures import PIN, PRE_COMMIT, TYPES, build
from railform.adopt import HYGIENE_URL
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
DEPENDABOT_PATTERN_ACCEPTS = 'dependabot/github_actions/actions/checkout-4.2.2'
DEPENDABOT_PATTERN_REFUSES = 'dependabot/pip/Django-4.2'
NOTE = 'notes.md'
HOOKS = ('pre-commit', 'commit-msg', 'pre-push')
# Variables that would point uv at railform's own environment, or make pre-commit skip hooks.
DROPPED = {'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC', 'SKIP'}
OFFLINE = 'these tests need the network; run `pytest -m "not slow"` offline'


def _uv_dir(kind: str) -> str:
    """Where uv keeps its `cache` or its managed `python` interpreters for the developer."""
    done = subprocess.run(
        ['uv', kind, 'dir'], capture_output=True, text=True, encoding='utf-8', check=True
    )
    return done.stdout.strip()


@pytest.fixture(scope='session')
def outside(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """The variables that point the hooks' tools away from the developer's home and caches."""
    home = tmp_path_factory.mktemp('home')
    (home / '.gitconfig').write_bytes(b'')
    (home / '.config').mkdir()
    return {
        'PRE_COMMIT_HOME': str(tmp_path_factory.mktemp('pre-commit-home')),
        'HOME': str(home),
        'USERPROFILE': str(home),
        'XDG_CONFIG_HOME': str(home / '.config'),
        'UV_CACHE_DIR': _uv_dir('cache'),
        'UV_PYTHON_INSTALL_DIR': _uv_dir('python'),
    }


def _environment(outside: dict[str, str]) -> dict[str, str]:
    """The environment of every hook-running command: the test's own, git already isolated by
    `tests/conftest.py`, with `outside` on top."""
    env = {key: value for key, value in os.environ.items() if key not in DROPPED}
    env.pop('PRE_COMMIT_REMOTE_BRANCH', None)
    return env | outside


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


def _require_network(cache: dict[str, object], cwd: Path, outside: dict[str, str]) -> None:
    """Fail at once, with one message, when the first network step of the session failed.

    The first step is reaching the hygiene hooks' repository, which pre-commit clones; asked
    once per session, so an offline run never waits through the retries of pre-commit or uv.
    The probe runs in the environment of pre-commit's clones (`no_git_env`, which reads the
    system git configuration), and a probe that times out is cached like any other failure.
    """
    if 'network' not in cache:
        command = ['git', 'ls-remote', '--quiet', HYGIENE_URL, 'HEAD']
        try:
            done = _run(cwd, command, no_git_env(_environment(outside)))
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr or b''
            if isinstance(stderr, bytes):
                stderr = stderr.decode('utf-8', 'replace')
            cache['network'] = f'no answer in {TIMEOUT} s {stderr.strip()}'.strip()
        else:
            cache['network'] = '' if done.returncode == 0 else done.stderr.strip()
    if cache['network']:
        pytest.fail(f'{OFFLINE} (git ls-remote {HYGIENE_URL}: {cache["network"]})', pytrace=False)


def test_the_network_probe_runs_in_the_environment_of_pre_commit_s_clones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # pre-commit clones with `no_git_env()`, which drops conftest's `GIT_CONFIG_NOSYSTEM` and
    # `GIT_CONFIG_GLOBAL`: a machine that reaches GitHub through its system git configuration
    # must not be told it is offline.
    seen: list[dict[str, str]] = []

    def answer(cwd: Path, command: list[str], env: dict[str, str] | None = None) -> object:
        seen.append(env or {})
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(f'{__name__}._run', answer)
    assert os.environ['GIT_CONFIG_NOSYSTEM'] == '1' and 'GIT_CONFIG_GLOBAL' in os.environ
    home = {'HOME': str(tmp_path), 'USERPROFILE': str(tmp_path)}
    _require_network({}, tmp_path, home)
    [env] = seen
    assert 'GIT_CONFIG_NOSYSTEM' not in env and 'GIT_CONFIG_GLOBAL' not in env, env
    assert env['HOME'] == env['USERPROFILE'] == str(tmp_path)
    assert env['PATH'] == os.environ['PATH']


def test_a_network_probe_that_times_out_fails_every_test_after_one_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def hang(cwd: Path, command: list[str], env: dict[str, str] | None = None) -> object:
        calls.append(command)
        raise subprocess.TimeoutExpired(command, TIMEOUT, stderr=b'still connecting')

    monkeypatch.setattr(f'{__name__}._run', hang)
    cache: dict[str, object] = {}
    for _ in range(2):
        with pytest.raises(pytest.fail.Exception, match='still connecting'):
            _require_network(cache, tmp_path, {})
    assert len(calls) == 1


@pytest.fixture
def fixture_origin(
    tmp_path_factory: pytest.TempPathFactory,
    outside: dict[str, str],
    railform_under_test: tuple[Path, str],
    _session_cache: dict[str, object],
) -> Callable[[str], Path]:
    """Build, once per session and type, the adopted migration fixture as a committed repository
    whose railform hooks come from the railform under test. A build that failed fails every
    later test of its type at once, with the first failure."""

    def origin(project_type: str) -> Path:
        _require_network(_session_cache, tmp_path_factory.getbasetemp(), outside)
        key = f'origin:{project_type}'
        if key not in _session_cache:
            try:
                _session_cache[key] = _build_origin(
                    tmp_path_factory.mktemp(f'origin-{project_type}'),
                    project_type,
                    railform_under_test,
                    outside,
                )
            except Exception as exc:
                _session_cache[key] = exc
                raise
        built = _session_cache[key]
        if isinstance(built, Exception):
            pytest.fail(f'building the {project_type} fixture failed earlier: {built}')
        return built  # type: ignore[return-value]

    return origin


def _build_origin(
    root: Path, project_type: str, railform: tuple[Path, str], outside: dict[str, str]
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
        locked = _run(root, ['uv', 'lock'], _environment(outside))
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
def env(outside: dict[str, str]) -> dict[str, str]:
    return _environment(outside)


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


def test_red_proof_a_commit_that_only_deletes_a_wrapper_is_refused(
    clone: Path, env: dict[str, str]
) -> None:
    # pre-commit leaves a deleted path out of the staged files, so the commit stages no file a
    # hook could match: only `always_run: true` makes the hooks check run.
    before = _head(clone)
    _git(clone, 'rm', '-q', '--', '.githooks/pre-push')
    refused = _run(clone, ['git', 'commit', '-q', '-m', 'chore: drop the pre-push wrapper'], env)
    assert refused.returncode != 0
    assert 'railform-verdict: hooks-check fail missing' in refused.stdout + refused.stderr
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
    # workflow (§9, not yet built) will judge the head branch of every pull request instead.
    _git(clone, 'branch', 'foo')
    accepted = _push(clone, env, 'foo')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert 'railform-verdict' not in accepted.stdout + accepted.stderr
    assert _remote_has(clone, 'foo')


def _push_accepted(clone: Path, env: dict[str, str], branch: str) -> None:
    _git(clone, 'switch', '-q', '-c', branch)
    _committed(clone, env, 'ci: bump a dependency')
    accepted = _push(clone, env, branch)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert _remote_has(clone, branch)


def test_red_proof_a_dependabot_branch_the_pattern_refuses_is_accepted_by_branch_allow(
    clone: Path, env: dict[str, str]
) -> None:
    # The uppercase dependency name fails branch.pattern: only `dependabot/**` passes it.
    _push_accepted(clone, env, DEPENDABOT_PATTERN_REFUSES)


def test_a_dependabot_branch_the_pattern_accepts_is_accepted(
    clone: Path, env: dict[str, str]
) -> None:
    # Not a red proof of branch.allow: the pattern accepts this name too.
    _push_accepted(clone, env, DEPENDABOT_PATTERN_ACCEPTS)


def test_red_proof_an_empty_commit_on_foo_is_refused_on_push(
    clone: Path, env: dict[str, str]
) -> None:
    # The pushed commit changes no file, so only `always_run: true` makes pre-commit run the
    # branch rail on this push.
    _git(clone, 'switch', '-q', '-c', 'foo')
    done = _run(clone, ['git', 'commit', '-q', '--allow-empty', '-m', 'docs: an empty commit'], env)
    assert done.returncode == 0, done.stdout + done.stderr
    refused = _push(clone, env, 'foo')
    assert refused.returncode != 0
    assert 'railform-verdict: branch-name fail branch' in refused.stdout + refused.stderr
    assert not _remote_has(clone, 'foo')
