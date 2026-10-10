"""`railform --version` prints the version in pyproject.toml and no verdict line."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import railform
from railform.cli import main

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    return data['project']['version']


def test_version_flag_prints_the_pyproject_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(['--version']) == 0
    assert capsys.readouterr().out == f'railform {_pyproject_version()}\n'


def test_version_flag_prints_no_verdict_line(capsys: pytest.CaptureFixture[str]) -> None:
    main(['--version'])
    assert 'railform-verdict:' not in capsys.readouterr().out


def test_package_version_is_the_pyproject_version() -> None:
    assert railform.__version__ == _pyproject_version()


def test_module_entry_point_prints_the_version() -> None:
    done = subprocess.run(
        [sys.executable, '-m', 'railform', '--version'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0
    assert done.stdout == f'railform {_pyproject_version()}\n'
