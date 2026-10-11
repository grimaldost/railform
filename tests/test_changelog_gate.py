"""The changelog gate, `railform changelog-gate BASE...HEAD` (development-rails spec, §14).

Ported from the stack-radar engine (https://github.com/grimaldost/stack-radar),
`tests/test_changelog_gate.py` at 0a48876. The pure rules (the trailer, what records, what a
commit is charged with, how `git merge-tree` is read) are driven directly; everything that
depends on which commits a range holds and what each one contributed (merges above all) runs end
to end, the real verb over a scratch repository built from real commits.

What the port leaves out, with the rule it tested: the Keep a Changelog section vocabulary (not
ported, Wave C contract decision 3), and the engine's own `radar.toml` declaration of watched
prefixes (replaced by `changelog.records` in the frame).

The red proofs of §14's acceptance criterion, each named in its test:

- a whitespace-only edit records nothing (`test_red_proof_whitespace_only_edits_record_nothing`);
- a reordered entry records nothing (`test_red_proof_a_reordered_entry_records_nothing`);
- a CRLF conversion records nothing (`test_red_proof_a_crlf_conversion_records_nothing`);
- a trailer on a neighbouring commit declares nothing
  (`test_red_proof_a_trailer_on_a_neighbour_declares_nothing`);
- a craft-collection-shaped repository is judged correctly (`TestCraftCollectionShape`);
- with `consumer_affecting = "error"`, a contract surface changed without the marker fails
  (`test_red_proof_an_unmarked_surface_fails_when_consumer_affecting_is_an_error`);
- `Changelog: none ()` declares nothing (`test_red_proof_an_empty_reason_declares_nothing`).

Through the caller: `test_red_proof_an_unrecorded_change_turns_gate_red_on_a_pull_request` in
`tests/test_python_ci_runs.py`.
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from railform import changelog_gate, frame
from railform.changelog_gate import (
    GateError,
    auto_merge_tree,
    charged,
    declares,
    diff_side,
    records_something,
    trailer_pattern,
)
from railform.cli import main

if TYPE_CHECKING:
    from pathlib import Path

VERDICT = re.compile(r'railform-verdict: changelog-gate (?P<status>pass|fail) (?P<code>\S+)')
SRC_RECORDS = '[[changelog.records]]\nprefix = "src/"\nfile = "CHANGELOG.md"\n'
BASE_CHANGELOG = (
    '# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- an old line\n- another old line\n'
)
DEFAULT = trailer_pattern('Changelog: none (<reason>)')


# --- helpers -----------------------------------------------------------------------------------


def _git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(
        ['git', *args], cwd=repo, capture_output=True, text=True, encoding='utf-8', check=False
    )
    if check and done.returncode != 0:
        raise AssertionError(f'git {" ".join(args)}: {done.stdout}{done.stderr}')
    return done.stdout


def _write(repo: Path, path: str, content: str | bytes) -> None:
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))


def _commit(repo: Path, message: str, edits: dict[str, str | bytes] | None = None) -> str:
    """Write each `path -> content` edit, stage only those paths, commit; returns the hash."""
    for path, content in (edits or {}).items():
        _write(repo, path, content)
    if edits:
        _git(repo, 'add', '--', *edits)
    _git(repo, 'commit', '-q', '--allow-empty', '-m', message)
    return _git(repo, 'rev-parse', 'HEAD').strip()


def _frame(repo: Path, text: str) -> None:
    """The working tree's `railform.toml`, which the verb reads; never committed here."""
    _write(repo, 'railform.toml', text)


def _start(repo: Path, records: str = SRC_RECORDS, project_type: str = 'python-package') -> Path:
    """A scratch repository with its own identity and a base commit on `main`, branch `base`
    pointing at it: `src/pkg/a.py`, `docs/x.md` and an open `[Unreleased]` with two bullets."""
    _git(repo, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    _git(repo, 'config', 'user.name', 'someone')
    _git(repo, 'config', 'user.email', 'someone@example.com')
    _git(repo, 'config', 'commit.gpgsign', 'false')
    # The CRLF red proof commits a file whose only change is its line endings.
    _git(repo, 'config', 'core.autocrlf', 'false')
    _frame(repo, f'[project]\ntype = "{project_type}"\n\n{records}')
    _commit(
        repo,
        'chore: base',
        {
            'src/pkg/a.py': 'x = 1\n',
            'src/pkg/wide.py': _wide(),
            'docs/x.md': 'docs\n',
            'CHANGELOG.md': BASE_CHANGELOG,
        },
    )
    _git(repo, 'branch', 'base')
    return repo


def _wide(top: str = 'top', bottom: str = 'bottom') -> str:
    """A file long enough that an edit near its top and one near its bottom fall in separate
    hunks, so two branches can each edit it and still merge cleanly."""
    lines = [f'value_{n} = {n}' for n in range(40)]
    lines[1] = f'value_1 = {top!r}'
    lines[38] = f'value_38 = {bottom!r}'
    return '\n'.join(lines) + '\n'


class Findings(list):  # type: ignore[type-arg]
    """The finding lines of a run; shown in an assertion message with the run's stderr."""

    def __init__(self, lines: list[str], stderr: str) -> None:
        super().__init__(lines)
        self.stderr = stderr

    def __repr__(self) -> str:
        return f'{list.__repr__(self)} (stderr: {self.stderr!r})'


class Outcome(tuple):  # type: ignore[type-arg]
    """`(exit code, verdict, findings)`; its repr, and a slice's, carry the run's stderr, so a
    run that ends in `usage` says why (a git older than 2.38, an unreadable revision)."""

    stderr: str

    def __new__(cls, items: tuple[object, ...], stderr: str) -> Outcome:
        made = super().__new__(cls, items)
        made.stderr = stderr
        return made

    def __getitem__(self, index):  # type: ignore[no-untyped-def]
        got = super().__getitem__(index)
        return Outcome(got, self.stderr) if isinstance(index, slice) else got

    def __repr__(self) -> str:
        return f'{tuple.__repr__(self)} (stderr: {self.stderr!r})'


def _gate(capsys: pytest.CaptureFixture[str], spec: str = 'base...HEAD') -> Outcome:
    """Run the verb: its exit code, its verdict (`pass ok`, `fail <code>`) and its findings."""
    code = main(['changelog-gate', spec])
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    found = VERDICT.fullmatch(lines[-1])
    assert found is not None, (lines, captured.err)
    verdict = f'{found["status"]} {found["code"]}'
    return Outcome((code, verdict, Findings(lines[:-1], captured.err)), captured.err)


@pytest.fixture
def repo(scratch_repo: Path) -> Path:
    return _start(scratch_repo)


# --- the trailer -------------------------------------------------------------------------------


class TestTrailer:
    def test_the_declared_exemption_carries_the_change(self) -> None:
        assert declares('refactor: rename\n\nChangelog: none (an internal rename)\n', DEFAULT)

    def test_a_bare_declaration_without_a_reason_does_not_count(self) -> None:
        # The parenthesis is the reviewable half: a bare declaration says nothing about why.
        assert not declares('refactor: x\n\nChangelog: none\n', DEFAULT)

    @pytest.mark.parametrize('reason', ['', '   ', '\t'])
    def test_a_reason_of_only_whitespace_declares_nothing(self, reason: str) -> None:
        assert not declares(f'refactor: x\n\nChangelog: none ({reason})\n', DEFAULT)

    def test_the_declared_exemption_is_case_insensitive(self) -> None:
        assert declares('refactor: x\n\nchangelog: None (an internal rename)\n', DEFAULT)

    def test_trailing_whitespace_is_ignored_and_leading_whitespace_is_not(self) -> None:
        assert declares('refactor: x\n\nChangelog: none (a rename)  \n', DEFAULT)
        assert not declares('refactor: x\n\n  Changelog: none (a rename)\n', DEFAULT)

    def test_the_line_may_sit_anywhere_in_the_message(self) -> None:
        assert declares('Changelog: none (subject line)\n', DEFAULT)
        assert declares('fix: x\n\nChangelog: none (a rename)\n\nmore text\n', DEFAULT)

    def test_the_template_is_the_frames(self) -> None:
        other = trailer_pattern('Skip-changelog: <reason>')
        assert declares('chore: x\n\nSkip-changelog: internal\n', other)
        assert not declares('chore: x\n\nChangelog: none (x)\n', other)
        assert not declares('chore: x\n\nSkip-changelog: \n', other)

    @pytest.mark.parametrize('template', ['Changelog: none', 'A <reason> and <reason>', ''])
    def test_a_template_without_exactly_one_reason_is_refused(self, template: str) -> None:
        with pytest.raises(GateError, match='<reason>'):
            trailer_pattern(template)


# --- what records ------------------------------------------------------------------------------


class TestWhatRecords:
    def test_a_whitespace_only_addition_records_nothing(self) -> None:
        assert not records_something('   \n\n', '')

    def test_an_added_line_the_diff_also_removes_records_nothing(self) -> None:
        # A trailing space on an existing line adds a line that survives strip().
        assert not records_something('- an old line \n', '- an old line\n')

    def test_one_new_line_among_re_added_ones_records(self) -> None:
        assert records_something('  - an old line\n- a new line\n', '- an old line\n')

    def test_an_added_line_whose_text_starts_with_two_plus_signs_is_an_added_line(self) -> None:
        diff = (
            'diff --git a/CHANGELOG.md b/CHANGELOG.md\n--- a/CHANGELOG.md\n+++ b/CHANGELOG.md\n'
            '@@ -1 +1,2 @@\n x\n+++ a new line\n-- gone\n'
        )
        assert diff_side(diff, '+') == '++ a new line'
        assert diff_side(diff, '-') == '- gone'


# --- what a commit is charged with -------------------------------------------------------------


class TestCharged:
    ROWS = (('src/', 'CHANGELOG.md'), ('src/plugins/a/', 'src/plugins/a/CHANGELOG.md'))

    def test_the_longest_prefix_wins(self) -> None:
        found = charged(['src/x.py', 'src/plugins/a/y.py'], self.ROWS)
        assert found == {
            'CHANGELOG.md': ['src/x.py'],
            'src/plugins/a/CHANGELOG.md': ['src/plugins/a/y.py'],
        }

    def test_docs_and_workflows_carry_no_obligation(self) -> None:
        assert charged(['docs/concepts.md', '.github/workflows/ci.yml', 'uv.lock'], self.ROWS) == {}

    def test_windows_path_separators_are_normalized(self) -> None:
        assert charged(['src\\stack\\field_report.py'], self.ROWS) == {
            'CHANGELOG.md': ['src/stack/field_report.py']
        }

    def test_a_records_file_is_never_charged_to_itself(self) -> None:
        # With a "" prefix the changelog would otherwise need a record of its own edits.
        rows = (('', 'CHANGELOG.md'), ('plugins/a/', 'plugins/a/CHANGELOG.md'))
        assert charged(['CHANGELOG.md', 'plugins/a/CHANGELOG.md'], rows) == {}
        assert charged(['README.md'], rows) == {'CHANGELOG.md': ['README.md']}

    def test_several_rows_may_name_one_file(self) -> None:
        rows = (('src/', 'CHANGELOG.md'), ('scripts/', 'CHANGELOG.md'))
        assert charged(['src/a.py', 'scripts/b.py'], rows) == {
            'CHANGELOG.md': ['src/a.py', 'scripts/b.py']
        }


# --- reading git merge-tree --------------------------------------------------------------------


def _merge_tree(
    returncode: int, stdout: str = '', stderr: str = ''
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=['git', 'merge-tree', '--write-tree', 'a', 'b'],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestMergeChargeRefusesToGuess:
    def test_a_merge_tree_that_wrote_no_tree_fails_naming_the_git_version(self) -> None:
        usage = 'usage: git merge-tree [--trivial-merge] <base-tree> <branch1> <branch2>\n'
        with pytest.raises(GateError) as failure:
            auto_merge_tree(_merge_tree(129, stdout=usage), 'git version 2.30.0\n')
        assert 'git version 2.30.0' in str(failure.value)
        assert '2.38' in str(failure.value)

    def test_a_conflicted_merge_tree_is_still_a_tree(self) -> None:
        # Exit 1 is a conflicted automatic merge: the tree still carries the markers.
        written = _merge_tree(1, stdout='0' * 40 + '\n\n100644 ' + '1' * 40 + ' 1\tsrc/a.py\n')
        assert auto_merge_tree(written, 'git version 9.9.9') == '0' * 40

    def test_a_pair_git_refuses_to_merge_is_no_tree_and_no_failure(self) -> None:
        refused = _merge_tree(128, stderr='fatal: refusing to merge unrelated histories\n')
        assert auto_merge_tree(refused, 'git version 9.9.9') is None

    def test_the_message_a_real_pre_2_38_git_prints_here_still_raises(self) -> None:
        old = _merge_tree(128, stderr='fatal: unknown rev --write-tree\n')
        with pytest.raises(GateError, match=r'2\.38 or newer'):
            auto_merge_tree(old, 'git version 2.30.0')

    def test_a_parent_git_cannot_read_raises_rather_than_falling_back(self) -> None:
        unreadable = _merge_tree(
            128, stderr='merge-tree: 0123456789abcdef0123456789abcdef01234567 - not something\n'
        )
        with pytest.raises(GateError, match='wrote no tree'):
            auto_merge_tree(unreadable, 'git version 9.9.9')


# --- record or declare, end to end -------------------------------------------------------------


def _unrecorded_lines(findings: list[str]) -> list[str]:
    return [line for line in findings if line.startswith('CHANGELOG.md: ')]


class TestRecordOrDeclare:
    @pytest.mark.parametrize(
        'edit',
        [
            BASE_CHANGELOG.replace('- an old line', '- an old line '),
            BASE_CHANGELOG + '\n\n',
            BASE_CHANGELOG.replace('- an old line', '  - an old line'),
        ],
        ids=['trailing-space', 'blank-lines', 're-indent'],
    )
    def test_red_proof_whitespace_only_edits_record_nothing(
        self, repo: Path, edit: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sha = _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n', 'CHANGELOG.md': edit})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        lines = _unrecorded_lines(findings)
        assert len(lines) == 1, findings
        assert sha in lines[0]

    def test_red_proof_a_reordered_entry_records_nothing(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        swapped = BASE_CHANGELOG.replace(
            '- an old line\n- another old line\n', '- another old line\n- an old line\n'
        )
        _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n', 'CHANGELOG.md': swapped})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings

    def test_red_proof_a_crlf_conversion_records_nothing(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        crlf = BASE_CHANGELOG.replace('\n', '\r\n').encode('utf-8')
        _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n', 'CHANGELOG.md': crlf})
        shown = subprocess.run(
            ['git', 'show', 'HEAD:CHANGELOG.md'], cwd=repo, capture_output=True, check=True
        )
        assert shown.stdout == crlf, 'the fixture must commit the CRLF bytes'
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings

    def test_deleting_the_changelog_does_not_record_the_change(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _git(repo, 'rm', '-q', 'CHANGELOG.md')
        _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n'})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings

    def test_a_new_bullet_among_whitespace_churn_is_a_record(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        churned = (
            '# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n'
            '  - an old line\n- another old line \n- a genuinely new bullet\n\n'
        )
        _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n', 'CHANGELOG.md': churned})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        # On a pass the verb says what it watched.
        assert any('CHANGELOG.md' in line for line in findings), findings

    def test_a_record_in_a_later_commit_covers_every_commit_of_the_range(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n'})
        _commit(repo, 'fix: change wide', {'src/pkg/wide.py': _wide(top='new')})
        _commit(repo, 'docs: record', {'CHANGELOG.md': BASE_CHANGELOG + '- both changes\n'})
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_a_path_no_row_watches_carries_no_obligation(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(
            repo,
            'docs: tidy',
            {'docs/x.md': 'docs, tidied\n', '.github/workflows/ci.yml': 'name: ci\n'},
        )
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_red_proof_a_trailer_on_a_neighbour_declares_nothing(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        first = _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n'})
        second = _commit(
            repo, 'docs: tidy\n\nChangelog: none (docs only)\n', {'docs/x.md': 'docs, tidied\n'}
        )
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        lines = _unrecorded_lines(findings)
        assert len(lines) == 1, findings
        assert first in lines[0]
        assert second not in '\n'.join(findings)

    def test_the_trailer_on_the_commit_itself_declares_it(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(repo, 'fix: change a\n\nChangelog: none (internal only)\n', {'src/pkg/a.py': 'y\n'})
        _commit(repo, 'docs: tidy', {'docs/x.md': 'docs, tidied\n'})
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_red_proof_an_empty_reason_declares_nothing(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(repo, 'fix: change a\n\nChangelog: none ()\n', {'src/pkg/a.py': 'x = 2\n'})
        assert _gate(capsys)[:2] == (1, 'fail unrecorded')

    def test_a_reason_declares(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _commit(repo, 'fix: change a\n\nChangelog: none (a rename)\n', {'src/pkg/a.py': 'x = 2\n'})
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_a_finding_names_the_subject_and_at_most_five_paths(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        edits: dict[str, str | bytes] = {f'src/pkg/m{n}.py': 'x\n' for n in range(7)}
        sha = _commit(repo, 'feat: seven modules', edits)
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded')
        (line,) = _unrecorded_lines(findings)
        assert sha in line
        assert 'feat: seven modules' in line
        assert 'src/pkg/m4.py' in line
        assert 'src/pkg/m5.py' not in line
        assert '...' in line
        assert 'Changelog: none (<reason>)' in line

    def test_an_empty_range_passes(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_head_may_be_any_revision(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # The range is judged from history, not from the working tree: the checkout here is the
        # base, and the commit judged is only named.
        bad = _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n'})
        _git(repo, 'checkout', '-q', 'base')
        code, verdict, findings = _gate(capsys, f'base...{bad[:12]}')
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        assert bad in '\n'.join(findings)
        assert _gate(capsys, 'base...HEAD')[:2] == (0, 'pass ok')


# --- merges, end to end ------------------------------------------------------------------------


def _conflict(repo: Path, message: str, resolution: dict[str, str]) -> None:
    """Merge `side` into the current branch with a conflict, resolve it by hand and commit."""
    _git(repo, 'merge', '--no-ff', 'side', check=False)
    for path, content in resolution.items():
        _write(repo, path, content)
        _git(repo, 'add', '--', path)
    _git(repo, 'commit', '-q', '-m', message)


def _octopus(repo: Path, message: str, *branches: str) -> None:
    """An octopus merge of `branches` into the current branch, built with plumbing: the tree of
    merging them one at a time, committed with every head as a parent."""
    start = _git(repo, 'rev-parse', 'HEAD').strip()
    for branch in branches:
        _git(repo, 'merge', '-q', '--no-ff', '-m', f'step {branch}', branch)
    tree = _git(repo, 'rev-parse', 'HEAD^{tree}').strip()
    parents = [arg for head in (start, *branches) for arg in ('-p', head)]
    commit = _git(repo, 'commit-tree', tree, *parents, '-m', message).strip()
    _git(repo, 'reset', '-q', '--hard', commit)


DECLARED = '\n\nChangelog: none (internal only)\n'


class TestMerges:
    def test_a_merge_resolution_that_touches_a_watched_path_needs_recording(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _git(repo, 'checkout', '-q', '-b', 'side')
        _commit(repo, 'fix: side tweak' + DECLARED, {'src/pkg/a.py': 'x = 2  # side\n'})
        _git(repo, 'checkout', '-q', 'main')
        _commit(repo, 'fix: main tweak' + DECLARED, {'src/pkg/a.py': 'x = 2  # main\n'})
        _conflict(repo, 'Merge branch side', {'src/pkg/a.py': 'x = 2  # merged\n'})
        merge = _git(repo, 'rev-parse', 'HEAD').strip()
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        (line,) = _unrecorded_lines(findings)
        assert merge in line
        assert 'Merge branch side' in line

    def test_a_merge_resolution_takes_the_trailer_like_any_commit(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _git(repo, 'checkout', '-q', '-b', 'side')
        _commit(repo, 'fix: side tweak' + DECLARED, {'src/pkg/a.py': 'x = 2  # side\n'})
        _git(repo, 'checkout', '-q', 'main')
        _commit(repo, 'fix: main tweak' + DECLARED, {'src/pkg/a.py': 'x = 2  # main\n'})
        _conflict(
            repo,
            'Merge branch side\n\nChangelog: none (resolution only)\n',
            {'src/pkg/a.py': 'x = 2  # merged\n'},
        )
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_a_clean_merge_passes(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _git(repo, 'checkout', '-q', '-b', 'side')
        _commit(repo, 'fix: side tweak' + DECLARED, {'src/pkg/a.py': 'x = 2\n'})
        _git(repo, 'checkout', '-q', 'main')
        _commit(repo, 'docs: unrelated', {'docs/x.md': 'docs, tidied\n'})
        _git(repo, 'merge', '-q', '--no-ff', '-m', 'Merge branch side', 'side')
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_a_clean_automerge_of_the_same_file_is_not_charged(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # `git diff-tree --cc --name-only` would charge it: a clean merge of two hunks of one
        # file differs from both parents.
        _git(repo, 'checkout', '-q', '-b', 'side')
        _commit(repo, 'fix: side tweak' + DECLARED, {'src/pkg/wide.py': _wide(top='side')})
        _git(repo, 'checkout', '-q', 'main')
        _commit(repo, 'fix: main tweak' + DECLARED, {'src/pkg/wide.py': _wide(bottom='main')})
        _git(repo, 'merge', '-q', '--no-ff', '-m', 'Merge branch side', 'side')
        assert (repo / 'src/pkg/wide.py').read_text(encoding='utf-8') == _wide('side', 'main')
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings

    def test_a_github_merge_ref_over_a_moved_base_is_not_charged(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The shape CI checks out on a pull request: a synthetic clean merge of the head into
        # the moved base, which nobody can put a trailer on.
        _git(repo, 'checkout', '-q', '-b', 'pr')
        _commit(repo, 'fix: the change' + DECLARED, {'src/pkg/wide.py': _wide(top='pr')})
        _git(repo, 'checkout', '-q', 'main')
        _commit(repo, 'fix: the base moved', {'src/pkg/wide.py': _wide(bottom='base')})
        _git(repo, 'branch', '-f', 'base', 'main')
        _git(repo, 'checkout', '-q', '--detach', 'main')
        _git(repo, 'merge', '-q', '--no-ff', '-m', 'Merge pr into main', 'pr')
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings

    def test_a_merge_resolution_outside_the_watched_paths_is_not_charged(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _git(repo, 'checkout', '-q', '-b', 'side')
        _commit(repo, 'docs: side wording', {'docs/x.md': 'docs, from the side\n'})
        _git(repo, 'checkout', '-q', 'main')
        _commit(repo, 'docs: main wording', {'docs/x.md': 'docs, from main\n'})
        _conflict(repo, 'Merge branch side', {'docs/x.md': 'docs, merged\n'})
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_an_octopus_merge_keeps_the_combined_diff(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The combined diff lists nothing here (no file differs from every parent), while
        # charging the first two parents as the whole merge would charge the third branch.
        _git(repo, 'checkout', '-q', '-b', 'b')
        _commit(repo, 'fix: b tweak' + DECLARED, {'src/pkg/a.py': 'x = 2\n'})
        _git(repo, 'checkout', '-q', '-b', 'c', 'main')
        _commit(repo, 'fix: c tweak' + DECLARED, {'src/pkg/other.py': 'y = 1\n'})
        _git(repo, 'checkout', '-q', 'main')
        _octopus(repo, 'Merge branches b and c', 'b', 'c')
        assert len(_git(repo, 'log', '-1', '--format=%P').split()) == 3
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_a_clean_octopus_over_one_file_is_over_charged(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Characterization, not a red proof: an octopus is outside merge-tree's two-parent
        # scope, so it keeps the combined diff and is charged though nobody authored the result.
        for name, edit in (('b', _wide(top='b')), ('c', _wide(bottom='c'))):
            _git(repo, 'checkout', '-q', '-b', name, 'main')
            _commit(repo, f'fix: {name} tweak' + DECLARED, {'src/pkg/wide.py': edit})
        _git(repo, 'checkout', '-q', 'main')
        _octopus(repo, 'Merge branches b and c', 'b', 'c')
        assert (repo / 'src/pkg/wide.py').read_text(encoding='utf-8') == _wide('b', 'c')
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        assert 'Merge branches b and c' in '\n'.join(findings)


def _stranger(repo: Path, message: str) -> None:
    """An orphan branch `stranger` whose one commit adds `src/pkg/other.py`."""
    _git(repo, 'checkout', '-q', '--orphan', 'stranger')
    _git(repo, 'rm', '-rq', '--cached', '.')
    for path in ('src/pkg/a.py', 'src/pkg/wide.py', 'docs/x.md', 'CHANGELOG.md'):
        (repo / path).unlink()
    _commit(repo, message, {'src/pkg/other.py': 'y = 1\n'})
    _git(repo, 'checkout', '-q', 'main')


class TestUnrelatedHistories:
    def test_an_unrelated_histories_merge_is_judged_not_crashed(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _stranger(repo, 'feat: a root commit')
        _git(
            repo,
            'merge',
            '-q',
            '--no-ff',
            '--allow-unrelated-histories',
            '-m',
            'Merge it',
            'stranger',
        )
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        assert 'src/pkg/other.py' in '\n'.join(findings)

    def test_an_unrelated_histories_merge_takes_the_trailer(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        trailer = '\n\nChangelog: none (vendored tree)\n'
        _stranger(repo, 'feat: a root commit' + trailer)
        _git(
            repo,
            'merge',
            '-q',
            '--no-ff',
            '--allow-unrelated-histories',
            '-m',
            'Merge it' + trailer,
            'stranger',
        )
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_an_unrelated_histories_resolution_is_charged_to_the_merge(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The root carries the trailer, so only the merge's own hand edit can fail.
        _stranger(repo, 'feat: a root commit\n\nChangelog: none (vendored tree)\n')
        _git(repo, 'merge', '--no-ff', '--no-commit', '--allow-unrelated-histories', 'stranger')
        _write(repo, 'src/pkg/other.py', 'y = 99\n')
        _git(repo, 'add', '--', 'src/pkg/other.py')
        _git(repo, 'commit', '-q', '-m', 'Merge the stranger')
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        assert 'Merge the stranger' in '\n'.join(findings)


# --- the release heading -----------------------------------------------------------------------

PACKAGE_SITES = (
    SRC_RECORDS
    + """
[[version.sites]]
path = "pyproject.toml"
pattern = '(?m)^version = "([^"]+)"'

[[version.sites]]
path = "CHANGELOG.md"
pattern = '(?m)^##\\s*\\[(\\d+\\.\\d+\\.\\d+)\\]'
authored = true
"""
)


def _pyproject(version: str) -> str:
    return f'[project]\nname = "pkg"\nversion = "{version}"\n'


@pytest.fixture
def package(scratch_repo: Path) -> Path:
    repo = _start(scratch_repo, PACKAGE_SITES)
    _commit(repo, 'chore: a version', {'pyproject.toml': _pyproject('0.2.0')})
    _git(repo, 'branch', '-f', 'base', 'main')
    return repo


def _cut(version: str) -> str:
    return BASE_CHANGELOG.replace(
        '## [Unreleased]\n', f'## [Unreleased]\n\n## [{version}] - 2026-10-11\n\n- a cut\n'
    )


class TestReleaseHeading:
    def test_a_real_cut_passes(self, package: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _commit(
            package,
            'release: 0.3.0',
            {'CHANGELOG.md': _cut('0.3.0'), 'pyproject.toml': _pyproject('0.3.0')},
        )
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings

    def test_a_heading_must_match_the_version_of_record(
        self, package: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(package, 'release: 0.3.0', {'CHANGELOG.md': _cut('0.3.0')})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail release-heading'), findings
        lines = [line for line in findings if line.startswith('CHANGELOG.md: ')]
        assert len(lines) == 1, findings
        assert '0.3.0' in lines[0] and '0.2.0' in lines[0]

    def test_a_heading_must_move_the_version_forward(
        self, package: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(package, 'release: 0.2.0', {'CHANGELOG.md': _cut('0.2.0')})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail release-heading'), findings
        assert any('forward from 0.2.0' in line for line in findings), findings

    def test_a_base_version_that_is_not_three_integers_is_not_compared(
        self, package: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # railform's own first release: pyproject.toml reads 0.1.0.dev0 at the base.
        _commit(package, 'chore: dev', {'pyproject.toml': _pyproject('0.1.0.dev0')})
        _git(package, 'branch', '-f', 'base', 'main')
        _commit(
            package,
            'release: 0.1.0',
            {'CHANGELOG.md': _cut('0.1.0'), 'pyproject.toml': _pyproject('0.1.0')},
        )
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_a_line_with_no_site_that_is_not_authored_skips_the_rule_and_says_so(
        self, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _start(scratch_repo)
        _commit(repo, 'release: 9.9.9', {'CHANGELOG.md': _cut('9.9.9')})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        assert any(line.startswith('CHANGELOG.md: ') for line in findings), findings

    def test_every_failing_rule_prints_and_the_first_names_the_verdict(
        self, package: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Two records: the code change goes unrecorded in CHANGELOG.md, while the heading added
        # to docs/CHANGELOG.md (on the root release line) does not move the version forward.
        docs = '[[changelog.records]]\nprefix = "docs/"\nfile = "docs/CHANGELOG.md"\n'
        _frame(package, '[project]\ntype = "python-package"\n\n' + PACKAGE_SITES + docs)
        sha = _commit(package, 'fix: change a', {'src/pkg/a.py': 'x = 2\n'})
        _commit(package, 'docs: a heading', {'docs/CHANGELOG.md': '## [0.2.0]\n'})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        assert any(line.startswith('CHANGELOG.md: ') and sha in line for line in findings)
        assert any(line.startswith('docs/CHANGELOG.md: ') for line in findings), findings


RELEASED = BASE_CHANGELOG.replace(
    '## [Unreleased]\n', '## [Unreleased]\n\n## [0.2.0] - 2026-10-01\n\n- released\n'
)


class TestAnEditedHeadingIsNotACut:
    """Wave C contract, 11.1: the cut is the first added heading whose version no removed heading
    of the same record diff carries."""

    @pytest.fixture
    def released(self, package: Path) -> Path:
        _commit(package, 'release: 0.2.0', {'CHANGELOG.md': RELEASED})
        _git(package, 'branch', '-f', 'base', 'main')
        return package

    @pytest.mark.parametrize(
        'edit',
        [
            RELEASED.replace('2026-10-01', '2026-10-02').encode('utf-8'),
            RELEASED.replace('## [0.2.0] - 2026-10-01', '## [0.2.0] - 2026-10-01 ').encode('utf-8'),
            RELEASED.replace('\n', '\r\n').encode('utf-8'),
        ],
        ids=['new-date', 'trailing-space', 'crlf'],
    )
    def test_red_proof_an_edited_released_heading_is_not_judged(
        self, released: Path, edit: bytes, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Before 11.1 each failed `release-heading` (0.2.0 does not move forward from 0.2.0),
        # and no trailer could clear it.
        _commit(released, 'docs: fix the release date', {'CHANGELOG.md': edit})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        assert not any('## [0.2.0]' in line for line in findings), findings

    def test_a_new_heading_beside_an_edited_one_is_still_judged(
        self, released: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        edit = RELEASED.replace('2026-10-01', '2026-10-02').replace(
            '## [Unreleased]\n', '## [Unreleased]\n\n## [0.3.0] - 2026-10-11\n\n- next\n'
        )
        _commit(released, 'release: 0.3.0', {'CHANGELOG.md': edit})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail release-heading'), findings
        assert any('0.3.0' in line and '0.2.0' in line for line in findings), findings

    def test_the_cut_skips_headings_the_diff_also_removes(self) -> None:
        assert changelog_gate.cut_version('## [0.2.0] - b\n', '## [0.2.0] - a\n') is None
        assert changelog_gate.cut_version('## [0.2.0] - b\n## [0.3.0]\n', '## [0.2.0]\n') == (
            0,
            3,
            0,
        )
        assert changelog_gate.cut_version('## [0.3.0]\n') == (0, 3, 0)


# 1.2.3 in Arabic-Indic digits, built at run time.
OTHER_DIGITS = '.'.join(chr(0x0660 + n) for n in (1, 2, 3))


class TestAsciiDigits:
    """Wave C contract, 11.3: versions and release headings read ASCII digits only."""

    def test_a_heading_in_other_digits_is_no_cut(self) -> None:
        arabic_indic = OTHER_DIGITS
        assert changelog_gate.cut_version(f'## [{arabic_indic}]\n') is None
        assert changelog_gate.cut_version('## [1.2.3]\n') == (1, 2, 3)

    def test_a_site_in_other_digits_reads_no_version(self) -> None:
        site = changelog_gate.Site('v.txt', re.compile(r'v=(\S+)'), authored=False)
        assert changelog_gate.site_version(f'v={OTHER_DIGITS}', site) is None
        assert changelog_gate.site_version('v=1.2.3', site) == (1, 2, 3)


# --- a watch that covers nothing ---------------------------------------------------------------


class TestAWatchThatCoversNothing:
    """Wave C contract, 11.2."""

    WARNING = '::warning::changelog.records: watches no tracked path'

    def test_red_proof_a_flat_python_package_is_told_its_watch_is_empty(
        self, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A flat-layout package inherits the preset's `src/` and would watch nothing in silence.
        repo = _start(scratch_repo, records='')
        _git(repo, 'rm', '-rq', 'src')
        _commit(repo, 'refactor: flat layout', {'pkg/a.py': 'x = 1\n'})
        _git(repo, 'branch', '-f', 'base', 'main')
        _commit(repo, 'fix: change a', {'pkg/a.py': 'x = 2\n'})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        assert self.WARNING in findings, findings

    def test_the_warning_keeps_a_failing_verdict(
        self, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _start(scratch_repo, records='')
        _git(repo, 'rm', '-rq', 'src')
        _commit(repo, 'refactor: flat layout', {'pkg/api.py': 'x = 1\n'})
        _git(repo, 'branch', '-f', 'base', 'main')
        _frame(
            repo,
            '[changelog]\nconsumer_affecting = "error"\ncontract_surfaces = ["pkg/api.py"]\n',
        )
        _commit(repo, 'feat: api', {'pkg/api.py': 'x = 2\n'})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail consumer-affecting'), findings
        assert self.WARNING in findings, findings

    def test_a_watch_that_covers_a_path_says_nothing(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(repo, 'docs: tidy', {'docs/x.md': 'docs, tidied\n'})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        assert self.WARNING not in findings, findings


# --- an older git says why ---------------------------------------------------------------------


def test_a_run_that_needs_a_newer_git_names_it_in_the_assertion_message(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A git older than 2.38 cannot charge a merge; the run ends in `usage`, and the helper keeps
    # stderr in what an assertion shows, so a red run on such a machine says why.
    real = changelog_gate.auto_merge_tree
    old = _merge_tree(128, stderr='fatal: unknown rev --write-tree\n')
    monkeypatch.setattr(
        changelog_gate, 'auto_merge_tree', lambda written, version: real(old, 'git version 2.30.0')
    )
    _git(repo, 'checkout', '-q', '-b', 'side')
    _commit(repo, 'docs: side', {'docs/x.md': 'side\n'})
    _git(repo, 'checkout', '-q', 'main')
    _git(repo, 'merge', '-q', '--no-ff', '-m', 'Merge branch side', 'side')
    outcome = _gate(capsys)
    assert outcome[:2] == (2, 'fail usage')
    assert '2.38' in repr(outcome[:2])
    assert '2.38' in repr(outcome[2])


# --- craft-collection's shape ------------------------------------------------------------------

CRAFT = """\
[[changelog.records]]
prefix = "plugins/a/"
file = "plugins/a/CHANGELOG.md"

[[changelog.records]]
prefix = "plugins/b/"
file = "plugins/b/CHANGELOG.md"

[[release.tag_prefix]]
prefix = "plugins/a/"
tag = "a--v"

[[release.tag_prefix]]
prefix = "plugins/b/"
tag = "b--v"

[[version.sites]]
path = "plugins/a/.claude-plugin/plugin.json"
pattern = '"version":\\s*"([0-9.]+)"'

[[version.sites]]
path = "plugins/a/CHANGELOG.md"
pattern = '(?m)^## \\[(\\d+\\.\\d+\\.\\d+)\\]'
authored = true

[[version.sites]]
path = "plugins/b/.claude-plugin/plugin.json"
pattern = '"version":\\s*"([0-9.]+)"'

[[version.sites]]
path = "plugins/b/CHANGELOG.md"
pattern = '(?m)^## \\[(\\d+\\.\\d+\\.\\d+)\\]'
authored = true
"""


def _manifest(version: str) -> str:
    return '{\n  "name": "p",\n  "version": "' + version + '"\n}\n'


def _plugin_log(version: str) -> str:
    return f'# Changelog\n\n## [Unreleased]\n\n## [{version}]\n\n- the first release\n'


class TestCraftCollectionShape:
    """Per-plugin changelogs and no `pyproject.toml` (the plugin-marketplace type)."""

    @pytest.fixture
    def craft(self, scratch_repo: Path) -> Path:
        repo = _start(scratch_repo, CRAFT, 'plugin-marketplace')
        edits: dict[str, str | bytes] = {'scripts/x.py': 'x = 1\n'}
        for plugin in ('a', 'b'):
            edits[f'plugins/{plugin}/x.md'] = 'x\n'
            edits[f'plugins/{plugin}/.claude-plugin/plugin.json'] = _manifest('0.1.0')
            edits[f'plugins/{plugin}/CHANGELOG.md'] = _plugin_log('0.1.0')
        _commit(repo, 'chore: two plugins', edits)
        _git(repo, 'branch', '-f', 'base', 'main')
        assert not (repo / 'pyproject.toml').exists()
        return repo

    def test_a_plugin_change_recorded_in_its_own_changelog_passes(
        self, craft: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(
            craft,
            'feat(a): more',
            {
                'plugins/a/x.md': 'more\n',
                'plugins/a/CHANGELOG.md': _plugin_log('0.1.0').replace(
                    '## [Unreleased]\n', '## [Unreleased]\n\n- more\n'
                ),
            },
        )
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_red_proof_a_record_in_another_plugins_changelog_records_nothing(
        self, craft: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sha = _commit(
            craft,
            'feat(b): more',
            {
                'plugins/b/x.md': 'more\n',
                'plugins/a/CHANGELOG.md': _plugin_log('0.1.0') + '- a line for a\n',
            },
        )
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail unrecorded'), findings
        lines = [line for line in findings if line.startswith('plugins/b/CHANGELOG.md: ')]
        assert len(lines) == 1 and sha in lines[0], findings
        assert not any(line.startswith('plugins/a/CHANGELOG.md: ') for line in findings)

    def test_a_path_outside_every_plugin_carries_no_obligation(
        self, craft: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(craft, 'chore: script', {'scripts/x.py': 'x = 2\n'})
        assert _gate(capsys)[:2] == (0, 'pass ok')

    def test_a_plugin_release_heading_is_judged_against_its_own_manifest(
        self, craft: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cut = _plugin_log('0.1.0').replace('## [Unreleased]\n', '## [Unreleased]\n\n## [0.2.0]\n')
        _commit(craft, 'release(a): 0.2.0', {'plugins/a/CHANGELOG.md': cut})
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail release-heading'), findings
        lines = [line for line in findings if line.startswith('plugins/a/CHANGELOG.md: ')]
        assert len(lines) == 1 and '0.1.0' in lines[0], findings
        # With plugin a's manifest moved to 0.2.0 as well, plugin a's heading passes.
        _commit(
            craft,
            'release(a): manifest',
            {'plugins/a/.claude-plugin/plugin.json': _manifest('0.2.0')},
        )
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings

    def test_red_proof_a_heading_reads_its_own_lines_site_not_the_first_site(
        self, craft: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The mirror case: plugin a's manifest, the first site overall, moves to 0.2.0, while the
        # heading is added to plugin b, whose own manifest still reads 0.1.0. Reading the first
        # site overall would pass this.
        b_cut = _plugin_log('0.1.0').replace('## [Unreleased]\n', '## [Unreleased]\n\n## [0.2.0]\n')
        _commit(
            craft,
            'release: 0.2.0',
            {
                'plugins/a/.claude-plugin/plugin.json': _manifest('0.2.0'),
                'plugins/a/CHANGELOG.md': _plugin_log('0.1.0') + '- a moves\n',
                'plugins/b/CHANGELOG.md': b_cut,
            },
        )
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail release-heading'), findings
        lines = [line for line in findings if line.startswith('plugins/b/CHANGELOG.md: ')]
        assert len(lines) == 1 and '0.1.0' in lines[0], findings
        assert not any(line.startswith('plugins/a/CHANGELOG.md: ') for line in findings)


# --- contract surfaces -------------------------------------------------------------------------

SURFACE = '[changelog]\ncontract_surfaces = ["src/pkg/api.py"]\n\n'
ERROR_ROW = (
    'consumer_affecting = "error"\n\n'
    '[[exceptions]]\nkey = "changelog.consumer_affecting"\nvalue = "error"\n'
    'reason = "the marker has proven quiet"\n\n'
)


def _surface_change(repo: Path, bullet: str) -> None:
    _commit(repo, 'chore: api', {'src/pkg/api.py': 'API = 1\n'})
    _git(repo, 'branch', '-f', 'base', 'main')
    _commit(
        repo,
        'feat: api',
        {'src/pkg/api.py': 'API = 2\n', 'CHANGELOG.md': BASE_CHANGELOG + f'- {bullet}\n'},
    )


class TestConsumerAffecting:
    def test_an_unmarked_surface_warns_and_passes_by_default(
        self, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _start(scratch_repo, SURFACE + SRC_RECORDS)
        _surface_change(repo, 'a new field')
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        warnings = [line for line in findings if line.startswith('::warning::')]
        assert len(warnings) == 1, findings
        assert warnings[0].startswith('::warning::src/pkg/api.py: consumer-affecting (advisory): ')

    def test_red_proof_an_unmarked_surface_fails_when_consumer_affecting_is_an_error(
        self, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _start(scratch_repo, SURFACE + ERROR_ROW + SRC_RECORDS)
        assert frame.check(frame.read_config(repo))[0] == 'ok'
        _surface_change(repo, 'a new field')
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (1, 'fail consumer-affecting'), findings
        assert any(line.startswith('src/pkg/api.py: consumer-affecting: ') for line in findings)
        assert not any(line.startswith('::warning::') for line in findings)

    @pytest.mark.parametrize('error', [False, True])
    def test_the_marker_silences_it(
        self, scratch_repo: Path, error: bool, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _start(scratch_repo, SURFACE + (ERROR_ROW if error else '') + SRC_RECORDS)
        _surface_change(repo, 'a renamed field (consumer-affecting)')
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        assert not any('consumer-affecting' in line for line in findings)

    def test_a_change_to_another_file_does_not_fire(
        self, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _start(scratch_repo, SURFACE + SRC_RECORDS)
        _commit(
            repo, 'feat: a', {'src/pkg/a.py': 'x = 2\n', 'CHANGELOG.md': BASE_CHANGELOG + '- a\n'}
        )
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok'), findings
        assert not any(line.startswith('::warning::') for line in findings)


# --- the frame changes the verdict (§2) --------------------------------------------------------


def _whitespace_only(repo: Path) -> None:
    edit = BASE_CHANGELOG.replace('- an old line', '- an old line ')
    _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n', 'CHANGELOG.md': edit})


class TestTheFrameChangesTheVerdict:
    def test_changelog_enabled_false_passes_at_once(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _whitespace_only(repo)
        assert _gate(capsys)[:2] == (1, 'fail unrecorded')
        _frame(
            repo,
            '[project]\ntype = "python-package"\n\n[changelog]\nenabled = false\n\n'
            '[[exceptions]]\nkey = "changelog.enabled"\nvalue = false\nreason = "another gate"\n',
        )
        assert frame.check(frame.read_config(repo))[0] == 'ok'
        code, verdict, findings = _gate(capsys)
        assert (code, verdict) == (0, 'pass ok')
        assert len(findings) == 1

    def test_off_reads_no_history(
        self, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No commit at all: the range resolves to nothing, and the gate never asks.
        _frame(scratch_repo, '[changelog]\nenabled = false\n')
        assert _gate(capsys, 'main...HEAD')[:2] == (0, 'pass ok')

    def test_the_trailer_spelling_is_the_frames(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _frame(
            repo,
            '[project]\ntype = "python-package"\n\n[changelog]\n'
            'trailer = "Skip-changelog: <reason>"\n\n'
            '[[exceptions]]\nkey = "changelog.trailer"\nvalue = "Skip-changelog: <reason>"\n'
            'reason = "the old spelling"\n',
        )
        _commit(repo, 'fix: change a\n\nSkip-changelog: internal\n', {'src/pkg/a.py': 'x = 2\n'})
        assert _gate(capsys)[:2] == (0, 'pass ok')
        _commit(repo, 'fix: change a\n\nChangelog: none (x)\n', {'src/pkg/a.py': 'x = 3\n'})
        assert _gate(capsys)[:2] == (1, 'fail unrecorded')

    def test_the_records_decide_what_is_watched(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(repo, 'docs: tidy', {'docs/x.md': 'docs, tidied\n'})
        assert _gate(capsys)[:2] == (0, 'pass ok')
        _frame(repo, '[[changelog.records]]\nprefix = ""\nfile = "CHANGELOG.md"\n')
        assert _gate(capsys)[:2] == (1, 'fail unrecorded')

    def test_the_python_package_preset_watches_src_and_python_scripts_everything(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Wave C contract, decision 2: a package records what it ships.
        package = frame.effective_frame({'project': {'type': 'python-package'}})
        scripts = frame.effective_frame({'project': {'type': 'python-scripts'}})
        assert package['changelog']['records'] == [{'prefix': 'src/', 'file': 'CHANGELOG.md'}]
        assert scripts['changelog']['records'] == [{'prefix': '', 'file': 'CHANGELOG.md'}]
        _commit(repo, 'chore: lock', {'uv.lock': 'version = 1\n'})
        _frame(repo, '[project]\ntype = "python-package"\n')
        assert _gate(capsys)[:2] == (0, 'pass ok')
        _frame(repo, '[project]\ntype = "python-scripts"\n')
        assert _gate(capsys)[:2] == (1, 'fail unrecorded')


# --- exit 2 ------------------------------------------------------------------------------------


class TestUsage:
    @pytest.mark.parametrize(
        'spec', ['base..HEAD', 'base', '...HEAD', 'base...', '-x...HEAD', 'base...-x', 'a...b...c']
    )
    def test_a_range_not_of_the_form_base_dots_head_is_usage(
        self, repo: Path, spec: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _gate(capsys, spec)[:2] == (2, 'fail usage')

    def test_a_revision_git_cannot_resolve_is_usage(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _gate(capsys, 'no-such-branch...HEAD')[:2] == (2, 'fail usage')

    def test_no_merge_base_is_usage(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _stranger(repo, 'feat: a root commit')
        assert _gate(capsys, 'stranger...main')[:2] == (2, 'fail usage')

    @pytest.mark.parametrize(
        'text',
        [
            'changelog.records = []\n',
            '[[changelog.records]]\nprefix = "src/"\nfile = ""\n',
            '[[changelog.records]]\nprefix = "src/"\nfile = "A.md"\n'
            '[[changelog.records]]\nprefix = "src/"\nfile = "B.md"\n',
            '[[release.tag_prefix]]\nprefix = ""\ntag = "v"\n'
            '[[release.tag_prefix]]\nprefix = ""\ntag = "w"\n',
            '[changelog]\ntrailer = "Changelog: none"\n',
            '[[version.sites]]\npath = "pyproject.toml"\npattern = "version"\n',
            '[changelog]\nrecords = 1\n',
        ],
        ids=[
            'no-records',
            'no-file',
            'records-prefix-twice',
            'tag-prefix-twice',
            'trailer-without-reason',
            'site-without-group',
            'wrong-type',
        ],
    )
    def test_a_frame_the_gate_cannot_obey_is_usage(
        self, repo: Path, text: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _commit(repo, 'fix: change a', {'src/pkg/a.py': 'x = 2\n'})
        _frame(repo, text)
        assert _gate(capsys)[:2] == (2, 'fail usage')

    def test_outside_a_repository_is_usage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path.parent))
        monkeypatch.chdir(tmp_path)
        assert _gate(capsys, 'main...HEAD')[:2] == (2, 'fail usage')

    def test_the_watched_records_settings_reject_an_empty_watch(self) -> None:
        effective = frame.effective_frame({'changelog': {'records': []}})
        with pytest.raises(GateError, match='empty'):
            changelog_gate.settings(effective)
