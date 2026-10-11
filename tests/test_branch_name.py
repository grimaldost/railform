"""The branch rail (development-rails spec, §8 and its round-6 amendment; interface contract,
section 10).

`railform branch-name [-- NAME]` judges the positional name when given, else
`$PRE_COMMIT_REMOTE_BRANCH` without `refs/heads/`, else the current branch. These tests call the
verb directly; `tests/test_hook_rail.py` runs it through a real `git push` in armed fixture
clones. The red proofs of the amendment are the tests named `test_red_proof_*`.
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from railform import branch_name, frame
from railform.cli import main

if TYPE_CHECKING:
    from pathlib import Path

VERDICT = re.compile(r'railform-verdict: branch-name (?P<status>pass|fail) (?P<code>[a-z0-9-]+)')
REMOTE_BRANCH = 'PRE_COMMIT_REMOTE_BRANCH'
IDENT = ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com')
DEPENDABOT_SHAPES = (
    'dependabot/github_actions/actions/checkout-4.2.2',
    'dependabot/uv/ruff-0.14.0',
    'dependabot/pip/Django-4.2',
)


@pytest.fixture(autouse=True)
def _no_remote_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test runs as if outside a pre-push hook unless it sets the variable itself."""
    monkeypatch.delenv(REMOTE_BRANCH, raising=False)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ['git', *IDENT, '-c', 'commit.gpgsign=false', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _on_branch(repo: Path, name: str) -> None:
    """Put the scratch repository on a branch called `name`, with one commit."""
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'chore: start')
    _git(repo, 'switch', '-q', '-c', name)


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str, str]:
    """Run the verb; return the exit code, the verdict status and code, and the whole output."""
    code = main(['branch-name', *args])
    captured = capsys.readouterr()
    last = captured.out.splitlines()[-1]
    match = VERDICT.fullmatch(last)
    assert match is not None, captured.out
    return code, match['status'], match['code'], captured.out + captured.err


# --- the default frame ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'name',
    [
        'feat/hook-rail',
        'fix/a-crash-on-windows',
        'docs/readme',
        'release/1.2.3',
        'test/redproof-something-fails',
        'ci/pin-actions',
        'chore/bump-0.3.5',
        # A category outside commit.types: the measured names use more than the commit types.
        'spike/try-a-thing',
        'feat/nested/part',
        'feat/under_score',
    ],
)
def test_the_default_frame_passes_a_category_and_slug(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, status, verdict_code, _ = _run(capsys, '--', name)
    assert (code, status, verdict_code) == (0, 'pass', 'ok')


@pytest.mark.parametrize(
    'name',
    ['foo', 'main-2', 'Feat/x', 'feat/Upper', 'feat/', 'feat/a b', '-x', 'feat/-x', '/x', '1x/y'],
)
def test_the_default_frame_refuses_a_name_without_category_and_slug(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, status, verdict_code, out = _run(capsys, '--', name)
    assert (code, status, verdict_code) == (1, 'fail', 'branch')
    assert 'branch.pattern' in out


def test_red_proof_foo_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(capsys, 'foo')[:3] == (1, 'fail', 'branch')


@pytest.mark.parametrize('args', [('',), ('--', '')])
def test_red_proof_an_empty_name_fails(
    args: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    code, status, verdict_code, out = _run(capsys, *args)
    assert (code, status, verdict_code) == (1, 'fail', 'branch')
    assert 'empty' in out


@pytest.mark.parametrize('name', DEPENDABOT_SHAPES)
def test_red_proof_dependabot_shapes_pass_by_branch_allow(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # The verdict says which rule passed the name: branch.allow, not the pattern, which the
    # first two shapes also match.
    code, status, verdict_code, out = _run(capsys, '--', name)
    assert (code, status, verdict_code) == (0, 'pass', 'ok')
    assert f'branch {name} matches branch.allow dependabot/**' in out


def test_red_proof_a_dependabot_shape_the_pattern_refuses_passes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # An uppercase dependency name: only branch.allow lets it through.
    name = DEPENDABOT_SHAPES[-1]
    assert re.fullmatch(frame.effective_frame(None)['branch']['pattern'], name) is None
    assert _run(capsys, '--', name)[:3] == (0, 'pass', 'ok')


def test_the_web_editor_branch_needs_its_own_allow_glob(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # GitHub's web editor names its branches `<user>-patch-<N>`. The measured count held none,
    # so the default does not allow them; a repository that uses the web editor declares a glob.
    assert _run(capsys, '--', 'someone-patch-1')[:3] == (1, 'fail', 'branch')
    (scratch_repo / 'railform.toml').write_text(
        '[branch]\nallow = ["dependabot/**", "*-patch-*"]\n\n'
        '[[exceptions]]\nkey = "branch.allow"\nvalue = ["dependabot/**", "*-patch-*"]\n'
        'reason = "this repository takes changes from the web editor"\n',
        encoding='utf-8',
    )
    assert _run(capsys, '--', 'someone-patch-1')[:3] == (0, 'pass', 'ok')


def test_the_default_frame_allows_dependabot_only() -> None:
    assert frame.effective_frame(None)['branch']['allow'] == ['dependabot/**']


# --- where the name comes from ------------------------------------------------------------------


def test_red_proof_the_remote_name_is_judged_not_the_local_one(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `git push origin feat/ok:foo`: pre-commit sets the remote ref, and that is the name judged.
    _on_branch(scratch_repo, 'feat/ok')
    monkeypatch.setenv(REMOTE_BRANCH, 'refs/heads/foo')
    code, status, verdict_code, out = _run(capsys)
    assert (code, status, verdict_code) == (1, 'fail', 'branch')
    assert 'foo' in out


def test_the_remote_name_passes_when_valid_whatever_the_local_branch(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _on_branch(scratch_repo, 'foo')
    monkeypatch.setenv(REMOTE_BRANCH, 'refs/heads/feat/ok')
    assert _run(capsys)[:3] == (0, 'pass', 'ok')


def test_an_empty_remote_branch_variable_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(REMOTE_BRANCH, '')
    assert _run(capsys)[:3] == (1, 'fail', 'branch')


def test_the_positional_name_takes_precedence_over_the_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(REMOTE_BRANCH, 'refs/heads/feat/ok')
    assert _run(capsys, '--', 'foo')[:3] == (1, 'fail', 'branch')
    monkeypatch.setenv(REMOTE_BRANCH, 'refs/heads/foo')
    assert _run(capsys, '--', 'feat/ok')[:3] == (0, 'pass', 'ok')


def test_a_remote_ref_that_is_not_a_branch_passes_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A tag push names refs/tags/...: it is not a branch, so the branch rail has nothing to judge.
    monkeypatch.setenv(REMOTE_BRANCH, 'refs/tags/v1.0.0')
    code, status, verdict_code, out = _run(capsys)
    assert (code, status, verdict_code) == (0, 'pass', 'ok')
    assert 'not a branch' in out


def test_the_current_branch_is_the_fallback(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _on_branch(scratch_repo, 'foo')
    assert _run(capsys)[:3] == (1, 'fail', 'branch')
    _git(scratch_repo, 'switch', '-q', '-c', 'feat/ok')
    assert _run(capsys)[:3] == (0, 'pass', 'ok')


def test_an_unborn_current_branch_is_judged(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(scratch_repo, 'symbolic-ref', 'HEAD', 'refs/heads/foo')
    assert _run(capsys)[:3] == (1, 'fail', 'branch')


def test_a_detached_head_with_no_other_source_passes_and_says_so(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _on_branch(scratch_repo, 'foo')
    _git(scratch_repo, 'switch', '-q', '--detach')
    code, status, verdict_code, out = _run(capsys)
    assert (code, status, verdict_code) == (0, 'pass', 'ok')
    assert 'detached' in out


def test_a_double_dash_lets_a_name_start_with_a_dash(capsys: pytest.CaptureFixture[str]) -> None:
    code, status, verdict_code, out = _run(capsys, '--', '-x')
    assert (code, status, verdict_code) == (1, 'fail', 'branch')
    assert 'branch -x does not match' in out


def test_a_second_name_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(capsys, '--', 'feat/a', 'feat/b')[:3] == (2, 'fail', 'usage')


def test_outside_a_git_repository_the_verb_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    assert _run(capsys, '--', 'feat/ok')[:3] == (2, 'fail', 'usage')


# --- the frame decides --------------------------------------------------------------------------


def test_red_proof_the_pattern_comes_from_the_frame(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(capsys, '--', 'foo')[:3] == (1, 'fail', 'branch')
    (scratch_repo / 'railform.toml').write_text(
        '[branch]\npattern = "^[a-z]+$"\n\n'
        '[[exceptions]]\nkey = "branch.pattern"\nvalue = "^[a-z]+$"\n'
        'reason = "this repository names branches with one word"\n',
        encoding='utf-8',
    )
    assert _run(capsys, '--', 'foo')[:3] == (0, 'pass', 'ok')
    assert _run(capsys, '--', 'feat/ok')[:3] == (1, 'fail', 'branch')


def test_red_proof_the_allow_list_comes_from_the_frame(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(capsys, '--', 'dependabot/pip/Django-4.2')[:3] == (0, 'pass', 'ok')
    assert _run(capsys, '--', 'renovate/Some-Dep')[:3] == (1, 'fail', 'branch')
    (scratch_repo / 'railform.toml').write_text(
        '[branch]\nallow = ["renovate/**"]\n\n'
        '[[exceptions]]\nkey = "branch.allow"\nvalue = ["renovate/**"]\n'
        'reason = "this repository uses another bot"\n',
        encoding='utf-8',
    )
    assert _run(capsys, '--', 'dependabot/pip/Django-4.2')[:3] == (1, 'fail', 'branch')
    assert _run(capsys, '--', 'renovate/Some-Dep')[:3] == (0, 'pass', 'ok')


def test_the_pattern_must_match_the_whole_name(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'railform.toml').write_text(
        '[branch]\npattern = "feat/"\n\n'
        '[[exceptions]]\nkey = "branch.pattern"\nvalue = "feat/"\nreason = "a prefix only"\n',
        encoding='utf-8',
    )
    assert _run(capsys, '--', 'feat/')[:3] == (0, 'pass', 'ok')
    assert _run(capsys, '--', 'feat/x')[:3] == (1, 'fail', 'branch')
    assert _run(capsys, '--', 'my-feat/')[:3] == (1, 'fail', 'branch')


def test_a_pattern_that_does_not_compile_exits_2(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'railform.toml').write_text(
        '[branch]\npattern = "(unclosed"\n', encoding='utf-8'
    )
    assert _run(capsys, '--', 'feat/ok')[:3] == (2, 'fail', 'usage')


# --- the allow globs ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('glob', 'name', 'matches'),
    [
        ('dependabot/**', 'dependabot/uv/ruff-0.14.0', True),
        ('dependabot/**', 'dependabot/x', True),
        ('dependabot/**', 'dependabot', False),
        ('dependabot/**', 'xdependabot/x', False),
        ('*-patch-*', 'someone-patch-1', True),
        ('*-patch-*', 'team/someone-patch-1', False),
        ('renovate/*', 'renovate/a', True),
        ('renovate/*', 'renovate/a/b', False),
        ('bot/?', 'bot/a', True),
        ('bot/?', 'bot/ab', False),
        ('a.b/**', 'aXb/c', False),
        ('feat/[x]', 'feat/[x]', True),
        ('feat/[x]', 'feat/x', False),
    ],
)
def test_allow_globs(glob: str, name: str, matches: bool) -> None:
    assert branch_name.glob_matches(glob, name) is matches
