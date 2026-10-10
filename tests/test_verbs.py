"""Every verb prints exactly one verdict line, last, on every exit path."""

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from railform import cli
from railform.cli import main
from railform.verdict import Verdict

# The verb table of the interface contract, section 2 (wave A).
CONTRACT_VERBS = (
    'frame',
    'private-refs',
    'commit-msg',
    'commit-range',
    'pr-text',
    'hooks-check',
    'arm',
    'session-paths',
    'adopt',
    'branch-name',
)
VERDICT = re.compile(r'railform-verdict: (?P<verb>\S+) (?P<status>pass|fail) (?P<code>[a-z0-9-]+)')


def _implemented(verb: str) -> bool:
    return importlib.util.find_spec(f'railform.{verb.replace("-", "_")}') is not None


def _verdict_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith('railform-verdict:')]


def test_the_cli_knows_exactly_the_contract_verbs() -> None:
    assert cli.VERBS == CONTRACT_VERBS


@pytest.mark.parametrize('verb', CONTRACT_VERBS)
def test_a_verb_prints_exactly_one_verdict_line_and_it_is_last(
    verb: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([verb])
    out = capsys.readouterr().out
    lines = _verdict_lines(out)
    assert len(lines) == 1
    assert out.splitlines()[-1] == lines[0]
    match = VERDICT.fullmatch(lines[0])
    assert match is not None
    assert match['verb'] == verb
    assert code in (0, 1, 2)
    assert (code == 0) == (match['status'] == 'pass')


@pytest.mark.parametrize('verb', CONTRACT_VERBS)
def test_a_verb_without_a_module_exits_2_not_implemented(
    verb: str, capsys: pytest.CaptureFixture[str]
) -> None:
    if _implemented(verb):
        pytest.skip('the verb has landed; its own tests cover its arguments')
    # Whatever the arguments are, a missing module is the only thing reported.
    assert main([verb, '--no-such-flag', 'x']) == 2
    assert capsys.readouterr().out == f'railform-verdict: {verb} fail not-implemented\n'


def test_an_unknown_verb_exits_2_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(['no-such-verb']) == 2
    captured = capsys.readouterr()
    assert captured.out == 'railform-verdict: no-such-verb fail usage\n'
    assert 'unknown verb' in captured.err


@pytest.mark.parametrize('name', ['cli', 'verdict', 'commit_msg', '../x', 'a b', '', '--bogus'])
def test_a_name_that_is_not_a_verb_exits_2_usage(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([name]) == 2
    lines = _verdict_lines(capsys.readouterr().out)
    assert len(lines) == 1
    match = VERDICT.fullmatch(lines[0])
    assert match is not None
    assert (match['status'], match['code']) == ('fail', 'usage')


def test_no_verb_exits_2_usage_with_one_verdict_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert capsys.readouterr().out == 'railform-verdict: railform fail usage\n'


def test_the_module_entry_point_ends_with_the_verdict_line() -> None:
    done = subprocess.run(
        [sys.executable, '-m', 'railform', 'no-such-verb'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert done.stdout == 'railform-verdict: no-such-verb fail usage\n'


class _FakeVerb:
    """A stand-in verb module, to show the lookup by name needs no edit of cli.py."""

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument('--fail', action='store_true')
        parser.add_argument('name')

    @staticmethod
    def run(args: argparse.Namespace) -> Verdict:
        if args.fail:
            return Verdict.fail('ignored', 'some-rule', ('a.txt:3: broken',))
        return Verdict.ok('ignored')


@pytest.fixture
def fake_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, 'railform.fake_verb', _FakeVerb)  # ty: ignore[invalid-argument-type]


def test_a_module_found_by_name_runs_and_the_cli_names_the_verb(
    fake_verb: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(['fake-verb', 'x']) == 0
    assert capsys.readouterr().out == 'railform-verdict: fake-verb pass ok\n'


def test_findings_come_before_the_verdict_line_and_a_failure_exits_1(
    fake_verb: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(['fake-verb', '--fail', 'x']) == 1
    assert capsys.readouterr().out == (
        'a.txt:3: broken\nrailform-verdict: fake-verb fail some-rule\n'
    )


def test_a_bad_argument_to_a_found_verb_exits_2_usage_with_one_verdict_line(
    fake_verb: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(['fake-verb']) == 2
    captured = capsys.readouterr()
    assert captured.out == 'railform-verdict: fake-verb fail usage\n'
    assert 'required' in captured.err


def test_help_of_a_found_verb_still_ends_with_one_verdict_line(
    fake_verb: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(['fake-verb', '--help']) == 0
    out = capsys.readouterr().out
    assert _verdict_lines(out) == ['railform-verdict: fake-verb pass ok']
    assert out.splitlines()[-1] == 'railform-verdict: fake-verb pass ok'


class _CrashingVerb:
    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        pass

    @staticmethod
    def run(args: argparse.Namespace) -> Verdict:
        raise RuntimeError('boom')


class _CrashingParserVerb:
    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        raise RuntimeError('boom while adding arguments')

    @staticmethod
    def run(args: argparse.Namespace) -> Verdict:
        return Verdict.ok('ignored')


@pytest.mark.parametrize('module', [_CrashingVerb, _CrashingParserVerb])
def test_an_unhandled_exception_is_one_internal_error_verdict_and_exit_2(
    module: type, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, 'railform.crash_verb', module)  # ty: ignore[invalid-argument-type]
    assert main(['crash-verb']) == 2
    captured = capsys.readouterr()
    assert captured.out == 'railform-verdict: crash-verb fail internal-error\n'
    assert 'Traceback (most recent call last)' in captured.err
    assert 'boom' in captured.err


def test_a_crash_is_appended_to_the_ledger_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / 'ledger.txt'
    monkeypatch.setenv('RAILFORM_LEDGER', str(ledger))
    monkeypatch.setitem(sys.modules, 'railform.crash_verb', _CrashingVerb)  # ty: ignore[invalid-argument-type]
    main(['crash-verb'])
    assert (
        ledger.read_text(encoding='utf-8') == 'railform-verdict: crash-verb fail internal-error\n'
    )
