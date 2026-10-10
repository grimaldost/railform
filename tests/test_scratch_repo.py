"""Each test runs in its own scratch repository, so files a verb writes never leak, and git never
reaches another repository through an inherited environment."""

import subprocess
from pathlib import Path

import pytest

from conftest import new_scratch_repo

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


def _other_repository(tmp_path_factory: pytest.TempPathFactory) -> Path:
    other = tmp_path_factory.mktemp('other')
    subprocess.run(['git', 'init', '-q', str(other)], cwd=other, check=True)
    assert _bare(other) == 'false'
    return other


def test_a_leaked_git_dir_makes_git_init_mark_another_repository_bare(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The hazard the scratch fixture guards against, shown on a throwaway repository: with
    # GIT_DIR inherited, `git init <dir>` reinitialises GIT_DIR's repository as a bare one.
    other = _other_repository(tmp_path_factory)
    target = tmp_path_factory.mktemp('target')
    monkeypatch.setenv('GIT_DIR', str(other / '.git'))
    subprocess.run(['git', 'init', '-q', str(target)], cwd=target, check=True)
    monkeypatch.delenv('GIT_DIR')
    assert _bare(other) == 'true'
    assert not (target / '.git').exists()


def test_the_scratch_fixture_ignores_a_leaked_git_dir(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    empty_git_config: Path,
) -> None:
    other = _other_repository(tmp_path_factory)
    monkeypatch.setenv('GIT_DIR', str(other / '.git'))
    monkeypatch.setenv('GIT_WORK_TREE', str(other))
    repo = new_scratch_repo(tmp_path_factory, monkeypatch, empty_git_config)
    assert (repo / '.git').is_dir()
    assert Path.cwd() == repo
    assert _bare(other) == 'false'
    assert _bare(repo) == 'false'
