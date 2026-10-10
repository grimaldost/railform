"""`$RAILFORM_LEDGER` receives a copy of the verdict line (design decision 9)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from railform.cli import main


def test_the_verdict_line_is_appended_to_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / 'ledger.txt'
    monkeypatch.setenv('RAILFORM_LEDGER', str(ledger))
    main(['no-such-verb'])
    line = 'railform-verdict: no-such-verb fail usage'
    assert capsys.readouterr().out == line + '\n'
    assert ledger.read_bytes() == (line + '\n').encode()


def test_each_run_appends_and_nothing_is_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / 'ledger.txt'
    ledger.write_text('earlier line\n', encoding='utf-8')
    monkeypatch.setenv('RAILFORM_LEDGER', str(ledger))
    main(['another-verb'])
    main(['no-such-verb'])
    assert ledger.read_text(encoding='utf-8').splitlines() == [
        'earlier line',
        'railform-verdict: another-verb fail usage',
        'railform-verdict: no-such-verb fail usage',
    ]


def test_the_ledger_is_created_when_it_does_not_exist_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / 'new.txt'
    monkeypatch.setenv('RAILFORM_LEDGER', str(ledger))
    main(['no-such-verb'])
    assert ledger.is_file()


def test_nothing_is_written_when_the_variable_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv('RAILFORM_LEDGER', raising=False)
    monkeypatch.chdir(tmp_path)
    main(['no-such-verb'])
    assert list(tmp_path.iterdir()) == []


def test_version_appends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / 'ledger.txt'
    monkeypatch.setenv('RAILFORM_LEDGER', str(ledger))
    main(['--version'])
    assert not ledger.exists()


def test_an_unwritable_ledger_does_not_change_the_verdict_or_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv('RAILFORM_LEDGER', str(tmp_path))  # a directory cannot be opened to append
    assert main(['no-such-verb']) == 2
    captured = capsys.readouterr()
    assert captured.out == 'railform-verdict: no-such-verb fail usage\n'
    assert 'RAILFORM_LEDGER' in captured.err


def test_the_ledger_works_through_the_real_command(tmp_path: Path) -> None:
    ledger = tmp_path / 'ledger.txt'
    env = {'RAILFORM_LEDGER': str(ledger)}
    done = subprocess.run(
        [sys.executable, '-m', 'railform', 'no-such-verb'],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env},
    )
    assert done.returncode == 2
    assert ledger.read_text(encoding='utf-8') == 'railform-verdict: no-such-verb fail usage\n'


def test_a_verb_typed_with_a_newline_cannot_forge_a_ledger_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / 'ledger.txt'
    monkeypatch.setenv('RAILFORM_LEDGER', str(ledger))
    main(['x\nrailform-verdict: frame pass ok'])
    assert len(ledger.read_text(encoding='utf-8').splitlines()) == 1
