"""Each test runs in its own scratch repository, so files a verb writes never leak, and git never
reaches another repository or configuration through an inherited environment."""

import os
import subprocess
from pathlib import Path

import pytest

from conftest import LOCATING_VARIABLES, new_scratch_repo

MARK = 'written-by-'


@pytest.mark.parametrize('name', ['first', 'second'])
def test_no_test_sees_a_file_another_test_wrote(name: str, scratch_repo: Path) -> None:
    # Whichever case runs second fails against a shared repository, in any order.
    assert Path.cwd() == scratch_repo
    assert (scratch_repo / '.git').is_dir()
    assert not list(scratch_repo.glob(f'{MARK}*'))
    (scratch_repo / f'{MARK}{name}').write_text('x', encoding='utf-8')


def _bare(repo: Path) -> str:
    done = subprocess.run(
        ['git', 'config', '--get', 'core.bare'],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _other_repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    other = tmp_path_factory.mktemp('other')
    subprocess.run(['git', 'init', '-q', str(other)], cwd=other, check=True)
    assert _bare(other) == 'false'
    return other


def test_the_variables_come_from_git_and_cover_the_configuration() -> None:
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        assert name in LOCATING_VARIABLES
    for name in ('GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'):
        assert name in LOCATING_VARIABLES
    assert 'GIT_NAMESPACE' in LOCATING_VARIABLES
    assert 'GIT_CEILING_DIRECTORIES' in LOCATING_VARIABLES


def test_a_leaked_git_dir_makes_git_init_mark_another_repository_bare(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The hazard the scratch fixture guards against, shown on a throwaway repository: with
    # GIT_DIR inherited, `git init <dir>` reinitialises GIT_DIR's repository as a bare one.
    # Whether it does depends on the git in use, so this only documents it where it happens;
    # the isolation test below holds everywhere.
    other = _other_repository(tmp_path_factory)
    target = tmp_path_factory.mktemp('target')
    monkeypatch.setenv('GIT_DIR', str(other / '.git'))
    subprocess.run(['git', 'init', '-q', str(target)], cwd=target, check=True)
    monkeypatch.delenv('GIT_DIR')
    if _bare(other) != 'true':
        version = _git(target, '--version')
        pytest.skip(f'{version} does not mark the repository GIT_DIR names as bare here')
    assert not (target / '.git').exists()


def test_the_scratch_fixture_ignores_leaked_git_variables(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    empty_git_config: Path,
) -> None:
    other = _other_repository(tmp_path_factory)
    outside_config = tmp_path_factory.mktemp('outside') / 'config'
    outside_config.write_bytes(b'[user]\n\tname = someone\n')
    before = outside_config.read_bytes()
    leaked = {
        'GIT_DIR': str(other / '.git'),
        'GIT_WORK_TREE': str(other),
        'GIT_CONFIG': str(outside_config),
        'GIT_CONFIG_PARAMETERS': "'core.bare'='true'",
        'GIT_CONFIG_COUNT': '1',
        'GIT_CONFIG_KEY_0': 'core.bare',
        'GIT_CONFIG_VALUE_0': 'true',
    }
    for name, value in leaked.items():
        monkeypatch.setenv(name, value)
    repo = new_scratch_repo(tmp_path_factory, monkeypatch, empty_git_config)
    assert not set(leaked) & set(os.environ)
    assert (repo / '.git').is_dir()
    assert Path.cwd() == repo
    # What tests do in their scratch repository lands there, and nowhere else.
    _git(repo, 'config', 'core.autocrlf', 'true')
    _git(repo, 'config', 'core.safecrlf', 'true')
    assert _git(repo, 'config', '--local', '--get', 'core.autocrlf') == 'true'
    assert outside_config.read_bytes() == before
    assert _bare(other) == 'false'
    assert _bare(repo) == 'false'
