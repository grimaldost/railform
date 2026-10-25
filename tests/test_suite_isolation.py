"""The suite is hermetic: a runner's global git configuration never reaches a test.

`GIT_CONFIG_GLOBAL` does not cover git's global ignore file, `$XDG_CONFIG_HOME/git/ignore` (or
`~/.config/git/ignore` when that is unset), so a runner that ignores `/.mcp.json` there made
`git add -A` skip the file inside a scratch repository and failed
`test_session_paths.py::test_every_default_glob_guards_its_path`. Each case below runs that test
in a pytest subprocess whose outer environment carries such an ignore file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARDED_TEST = 'tests/test_session_paths.py::test_every_default_glob_guards_its_path'
IGNORED = '/.mcp.json\n'
# Variables that, set in the outer environment, would carry the runner's own configuration.
HOME_VARIABLES = ('HOME', 'USERPROFILE', 'XDG_CONFIG_HOME')


def _write_ignore(config_home: Path) -> None:
    ignore = config_home / 'git' / 'ignore'
    ignore.parent.mkdir(parents=True)
    ignore.write_text(IGNORED, encoding='utf-8', newline='\n')


def _outer_environment(*, via: str, base: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in HOME_VARIABLES}
    if via == 'xdg':
        _write_ignore(base / 'xdg')
        env['XDG_CONFIG_HOME'] = str(base / 'xdg')
    else:
        _write_ignore(base / 'home' / '.config')
        env['HOME'] = env['USERPROFILE'] = str(base / 'home')
    return env


def _run_guarded_test(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', GUARDED_TEST],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )


@pytest.mark.parametrize('via', ['xdg', 'home'])
def test_a_global_git_ignore_does_not_reach_the_scratch_repository(
    via: str, tmp_path: Path
) -> None:
    done = _run_guarded_test(_outer_environment(via=via, base=tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert '1 passed' in done.stdout, done.stdout


def test_the_control_run_without_a_global_ignore_passes() -> None:
    env = {key: value for key, value in os.environ.items() if key not in HOME_VARIABLES}
    done = _run_guarded_test(env)
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_test_runs_with_its_home_and_tool_directories_in_temporary_directories(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    session = tmp_path_factory.getbasetemp().resolve()
    for key in (
        'HOME',
        'USERPROFILE',
        'XDG_CONFIG_HOME',
        'PRE_COMMIT_HOME',
        'UV_TOOL_DIR',
        'UV_TOOL_BIN_DIR',
    ):
        directory = Path(os.environ[key])
        assert directory.is_dir(), key
        assert directory.resolve().is_relative_to(session), key
    assert os.environ['HOME'] == os.environ['USERPROFILE']
    assert Path(os.environ['XDG_CONFIG_HOME']).parent == Path(os.environ['HOME'])


def test_tests_that_list_the_clones_tracked_files_skip_in_a_copy_without_git(
    tmp_path: Path,
) -> None:
    copy = tmp_path / 'tree'
    shutil.copytree(
        ROOT,
        copy,
        ignore=shutil.ignore_patterns(
            '.git', '.venv', '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache'
        ),
    )
    list_tracked_files = [
        'tests/test_install_by_sha.py::test_no_tracked_file_installs_railform_without_a_commit_pin',
        'tests/test_hook_rail.py::test_red_proof_a_non_conventional_message_is_refused'
        '[python-package]',
        'tests/test_private_refs.py::test_railforms_own_tracked_files_pass_the_check',
    ]
    done = subprocess.run(
        [
            sys.executable,
            '-m',
            'pytest',
            '-q',
            '-rs',
            '-p',
            'no:cacheprovider',
            *list_tracked_files,
        ],
        cwd=copy,
        env={key: value for key, value in os.environ.items() if key not in HOME_VARIABLES},
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert '3 skipped' in done.stdout, done.stdout
    assert 'error' not in done.stdout.splitlines()[-1], done.stdout
