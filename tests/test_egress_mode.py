"""The egress mode of `railform private-refs`: `--names-file` with exactly the input that leaves.

Each `test_red_proof_*` fails before the egress mode exists. The listed terms are invented words,
the names files live under pytest's temporary directories, and no test reads a real names list.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hook_isolation import hook_environment
from railform.cli import main

ROOT = Path(__file__).resolve().parents[1]
NAMES_ENV = 'RAILFORM_PRIVATE_NAMES'
TERM = 'zebrafrog'
OTHER_TERM = 'quillmoss'
VERDICT = re.compile(r'railform-verdict: private-refs (pass|fail) [a-z-]+')
PASSED = 'railform-verdict: private-refs pass ok'
FAIL_NAME = 'railform-verdict: private-refs fail private-name'
FAIL_UNSCANNABLE = 'railform-verdict: private-refs fail unscannable'
FAIL_USAGE = 'railform-verdict: private-refs fail usage'


def git(repo: Path, *args: str, author: str = 'alice', email: str = 'alice@example.com') -> str:
    done = subprocess.run(
        [
            'git',
            '-c',
            f'user.name={author}',
            '-c',
            f'user.email={email}',
            '-c',
            'commit.gpgsign=false',
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )
    assert done.returncode == 0, (args, done.stdout, done.stderr)
    return done.stdout.strip()


def write(repo: Path, name: str, data: str | bytes) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode('utf-8')
    path.write_bytes(data)


def stage(repo: Path, name: str, data: str | bytes) -> None:
    write(repo, name, data)
    git(repo, 'add', '--', name)


def commit(repo: Path, message: str = 'chore: change', **identity: str) -> str:
    git(repo, 'commit', '-q', '--allow-empty', '-m', message, **identity)
    return git(repo, 'rev-parse', 'HEAD')


def short(sha: str) -> str:
    return sha[:12]


def check(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, list[str], str]:
    """Run the verb: the exit code, the verdict line, the findings before it, and stderr."""
    code = main(['private-refs', *args])
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines and VERDICT.fullmatch(lines[-1]), captured.out
    return code, lines[-1], lines[:-1], captured.err


@pytest.fixture(autouse=True)
def no_names_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own names file must never change what these tests see."""
    monkeypatch.delenv(NAMES_ENV, raising=False)


@pytest.fixture
def names(tmp_path: Path) -> Path:
    """A names file holding one invented term, on its second line."""
    path = tmp_path / 'egress-terms.txt'
    path.write_text(f'# invented terms\n{TERM}\n', encoding='utf-8')
    return path


@pytest.fixture
def commit_list(tmp_path: Path) -> Path:
    return tmp_path / 'commits.txt'


def listing(path: Path, *shas: str) -> str:
    path.write_text(''.join(f'{sha}\n' for sha in shas), encoding='utf-8')
    return str(path)


# --- the red proofs ----------------------------------------------------------------------------


def test_red_proof_a_clean_file_at_a_path_holding_a_term_is_found_by_commits(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, f'docs/{TERM}-notes.md', 'nothing to see here\n')
    sha = commit(scratch_repo, 'docs: add notes')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [
        f'commit {short(sha)} path docs/<name>-notes.md: private name (names file line 2)'
    ]


def test_red_proof_a_term_one_commit_adds_and_a_later_one_removes_names_the_first_commit(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'notes.md', 'first\n')
    base = commit(scratch_repo, 'docs: notes')
    stage(scratch_repo, 'notes.md', f'first\nthanks to {TERM}\n')
    added = commit(scratch_repo, 'docs: thanks')
    stage(scratch_repo, 'notes.md', 'first\n')
    removed = commit(scratch_repo, 'docs: drop the thanks')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, added, removed)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [f'commit {short(added)} notes.md:2: private name (names file line 2)']
    # A list without the commit that added the term passes.
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, base, removed)
    )
    assert (code, verdict, findings) == (0, PASSED, [])


def test_red_proof_a_tracked_or_uncommitted_file_outside_the_list_is_ignored(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'old.md', f'{TERM}\n')
    commit(scratch_repo, 'docs: an old note')
    stage(scratch_repo, 'new.md', 'clean\n')
    listed = commit(scratch_repo, 'docs: a new note')
    write(scratch_repo, 'untracked.md', f'{TERM}\n')
    write(scratch_repo, 'new.md', f'clean\n{TERM}\n')  # modified, not staged
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, listed)
    )
    assert (code, verdict, findings) == (0, PASSED, [])


def test_red_proof_the_names_variable_is_not_read(
    scratch_repo: Path,
    names: Path,
    commit_list: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    other = tmp_path / 'other-terms.txt'
    other.write_text(f'{OTHER_TERM}\n', encoding='utf-8')
    monkeypatch.setenv(NAMES_ENV, str(other))
    stage(scratch_repo, 'a.md', f'one {OTHER_TERM}\ntwo {TERM}\n')
    sha = commit(scratch_repo, 'docs: a')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [f'commit {short(sha)} a.md:2: private name (names file line 2)']
    # The variable naming a file that does not exist changes nothing either.
    monkeypatch.setenv(NAMES_ENV, str(tmp_path / 'no-such-file.txt'))
    assert check(capsys, '--names-file', str(names), '--commits', str(commit_list))[2] == findings


def test_red_proof_a_utf16_file_and_a_binary_file_in_a_listed_commit_are_unscannable(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'wide.txt', 'plain words\n'.encode('utf-16'))
    stage(scratch_repo, 'blob.bin', b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')
    stage(scratch_repo, 'fine.md', 'fine\n')
    sha = commit(scratch_repo, 'feat: add data')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_UNSCANNABLE)
    assert sorted(findings) == [
        f'commit {short(sha)} blob.bin: unscannable (binary)',
        f'commit {short(sha)} wide.txt: unscannable (binary)',
    ]


def test_red_proof_a_term_introduced_only_in_a_merge_resolution_is_found(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'f.md', 'one\n')
    commit(scratch_repo, 'docs: f')
    git(scratch_repo, 'checkout', '-q', '-b', 'side')
    stage(scratch_repo, 'f.md', 'one\nside\n')
    side = commit(scratch_repo, 'docs: side')
    git(scratch_repo, 'checkout', '-q', '-')
    stage(scratch_repo, 'f.md', 'one\nmain\n')
    main_tip = commit(scratch_repo, 'docs: main')
    subprocess.run(
        ['git', 'merge', '-q', 'side', '-m', 'merge'],
        cwd=scratch_repo,
        capture_output=True,
        check=False,
    )
    stage(scratch_repo, 'f.md', f'one\nresolved by {TERM}\n')
    merge = commit(scratch_repo, 'Merge branch side')
    # Neither parent holds the term: only the resolution does.
    code, verdict, _, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, side, main_tip)
    )
    assert (code, verdict) == (0, PASSED)
    code, verdict, findings, _ = check(
        capsys,
        '--names-file',
        str(names),
        '--commits',
        listing(commit_list, side, main_tip, merge),
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [f'commit {short(merge)} f.md:2: private name (names file line 2)']


def test_red_proof_staged_reports_path_and_line(
    scratch_repo: Path, names: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'one\n')
    commit(scratch_repo, 'docs: a')
    stage(scratch_repo, 'a.md', f'one\ntwo\nby {TERM}\n')
    stage(scratch_repo, 'b.md', f'{TERM.upper()}\n')
    code, verdict, findings, _ = check(capsys, '--names-file', str(names), '--staged')
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [
        'a.md:3: private name (names file line 2)',
        'b.md:1: private name (names file line 2)',
    ]


def test_red_proof_a_missing_names_file_exits_2(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    sha = commit(scratch_repo, 'docs: a')
    given = listing(commit_list, sha)
    assert check(capsys, '--names-file', str(names), '--commits', given)[:2] == (0, PASSED)
    missing = names.parent / 'no-such-names.txt'
    code, verdict, findings, err = check(capsys, '--names-file', str(missing), '--commits', given)
    assert (code, verdict, findings) == (2, FAIL_USAGE, [])
    assert 'names file' in err
    # --commits without the names file is no egress run either.
    code, verdict, _, err = check(capsys, '--commits', given)
    assert (code, verdict) == (2, FAIL_USAGE)
    assert '--names-file' in err


def test_red_proof_a_frame_that_does_not_load_changes_nothing(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(scratch_repo, 'railform.toml', '[hooks]\nno_such_key = 1\n')
    assert check(capsys)[:2] == (2, FAIL_USAGE)  # the other modes do load the frame
    stage(scratch_repo, 'railform.toml', '[hooks]\nno_such_key = 1\n')
    clean = commit(scratch_repo, 'chore: a frame')
    stage(scratch_repo, 'a.md', f'{TERM}\n')
    dirty = commit(scratch_repo, 'docs: a')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, clean)
    )
    assert (code, verdict, findings) == (0, PASSED, [])
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, dirty)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [f'commit {short(dirty)} a.md:1: private name (names file line 2)']


def _tracked_in_railform() -> list[str]:
    done = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip('not a git checkout')
    return [os.fsdecode(item) for item in done.stdout.split(b'\0') if item]


def test_red_proof_no_names_list_is_tracked_and_the_egress_mode_has_no_default_list(
    capsys: pytest.CaptureFixture[str],
) -> None:
    suspicious = re.compile(
        r'(private|owner|secret|deny|block|banned|forbidden|egress)[-_.]?'
        r'(names?|terms?|words?|list)'
        r'|(names?|terms?)[-_.]?(file|list)'
        r'|^names?\.',
        re.IGNORECASE,
    )
    tracked = _tracked_in_railform()
    assert tracked
    assert [name for name in tracked if suspicious.search(Path(name).name)] == []
    # The egress mode reads the list it is given and nothing else: no default path or list.
    code, verdict, _, err = check(capsys, '--text', str(ROOT / 'README.md'), '--names-file', '')
    assert (code, verdict) == (2, FAIL_USAGE)
    assert 'names file' in err
    assert main(['private-refs', '--help']) == 0
    help_text = ' '.join(capsys.readouterr().out.split())
    assert '--names-file' in help_text
    assert 'egress mode' in help_text


# --- what a commit carries besides its lines ---------------------------------------------------


def test_an_empty_file_at_a_term_path_is_found(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, f'{TERM}.md', b'')
    sha = commit(scratch_repo, 'docs: an empty file')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [f'commit {short(sha)} path <name>.md: private name (names file line 2)']


def test_a_rename_to_a_term_path_is_found(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'notes.md', 'a long enough body\nfor rename detection\n')
    commit(scratch_repo, 'docs: notes')
    git(scratch_repo, 'mv', 'notes.md', f'{TERM}.md')
    sha = commit(scratch_repo, 'docs: rename')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [f'commit {short(sha)} path <name>.md: private name (names file line 2)']


def test_the_author_the_committer_and_the_message_are_scanned(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sha = commit(
        scratch_repo,
        f'chore: nothing\n\nwith thanks to {TERM}\n',
        author=f'Alice {TERM.title()}',
        email=f'someone@{TERM}.example',
    )
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == [
        f'commit {short(sha)} author: private name (names file line 2)',
        f'commit {short(sha)} author: private name (names file line 2)',
        f'commit {short(sha)} committer: private name (names file line 2)',
        f'commit {short(sha)} committer: private name (names file line 2)',
        f'commit {short(sha)} message:3: private name (names file line 2)',
    ]


def test_the_committer_alone_is_scanned(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    commit(scratch_repo, 'docs: a')
    subprocess.run(
        ['git', 'commit', '-q', '--amend', '--no-edit'],
        cwd=scratch_repo,
        env={
            **os.environ,
            'GIT_AUTHOR_NAME': 'alice',
            'GIT_AUTHOR_EMAIL': 'alice@example.com',
            'GIT_COMMITTER_NAME': 'someone',
            'GIT_COMMITTER_EMAIL': f'{TERM}@example.com',
        },
        capture_output=True,
        check=True,
    )
    sha = git(scratch_repo, 'rev-parse', 'HEAD')
    code, _, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, findings) == (
        1,
        [f'commit {short(sha)} committer: private name (names file line 2)'],
    )


def test_an_empty_commit_list_passes(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', f'{TERM}\n')
    commit(scratch_repo, 'docs: a')
    assert check(capsys, '--names-file', str(names), '--commits', listing(commit_list)) == (
        0,
        PASSED,
        [],
        '',
    )
    commit_list.write_text('\n\n', encoding='utf-8')
    assert check(capsys, '--names-file', str(names), '--commits', str(commit_list))[:3] == (
        0,
        PASSED,
        [],
    )


def test_an_unknown_commit_exits_2_without_printing_a_term(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    sha = commit(scratch_repo, 'docs: a')
    for unknown in ('0' * 40, f'{TERM}-branch', '--all'):
        code, verdict, findings, err = check(
            capsys, '--names-file', str(names), '--commits', listing(commit_list, sha, unknown)
        )
        assert (code, verdict, findings) == (2, FAIL_USAGE, []), unknown
        assert 'unknown commit' in err
        assert TERM not in err.lower()


def test_no_home_path_finding_is_made_in_the_egress_mode(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = '/' + 'home' + '/' + 'mallory' + '/project'
    stage(scratch_repo, 'a.md', f'see {home}\n')
    sha = commit(scratch_repo, f'docs: see {home}')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict, findings) == (0, PASSED, [])
    # The other modes still make it.
    assert check(capsys, 'a.md')[1] == 'railform-verdict: private-refs fail home-path'


def test_a_term_in_a_root_commit_is_found_and_a_list_line_may_be_any_commit_name(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(scratch_repo, 'config', 'log.showRoot', 'false')
    stage(scratch_repo, 'a.md', f'{TERM}\n')
    root = commit(scratch_repo, 'docs: a')
    commit_list.write_text(f'  {short(root)}  \r\nHEAD\n', encoding='utf-8')
    code, _, findings, _ = check(capsys, '--names-file', str(names), '--commits', str(commit_list))
    # A commit listed twice is scanned once.
    assert (code, findings) == (
        1,
        [f'commit {short(root)} a.md:1: private name (names file line 2)'],
    )


def test_a_private_name_outranks_unscannable(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'blob.bin', b'\0\1')
    stage(scratch_repo, 'a.md', f'{TERM}\n')
    sha = commit(scratch_repo, 'feat: both')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert sorted(findings) == [
        f'commit {short(sha)} a.md:1: private name (names file line 2)',
        f'commit {short(sha)} blob.bin: unscannable (binary)',
    ]


def test_a_latin1_file_is_unscannable_and_a_bom_is_not_a_defect(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'latin.txt', 'caf\xe9\n'.encode('latin-1'))
    stage(scratch_repo, 'bom.md', '﻿clean\n'.encode())
    sha = commit(scratch_repo, 'docs: two encodings')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict) == (1, FAIL_UNSCANNABLE)
    assert findings == [f'commit {short(sha)} latin.txt: unscannable (not UTF-8)']


def test_a_file_marked_binary_by_attributes_is_still_read(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, '.gitattributes', 'data.txt -diff\n')
    stage(scratch_repo, 'data.txt', f'thanks to {TERM}\n')
    sha = commit(scratch_repo, 'docs: data')
    code, _, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, findings) == (
        1,
        [f'commit {short(sha)} data.txt:1: private name (names file line 2)'],
    )


def test_a_deleted_file_adds_nothing(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, f'{TERM}.md', f'{TERM}\n')
    commit(scratch_repo, 'docs: a')
    git(scratch_repo, 'rm', '-q', '--', f'{TERM}.md')
    sha = commit(scratch_repo, 'docs: drop a')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--commits', listing(commit_list, sha)
    )
    assert (code, verdict, findings) == (0, PASSED, [])


# --- --staged and --text -----------------------------------------------------------------------


def test_staged_without_head_reads_every_staged_line(
    scratch_repo: Path, names: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', f'one\n{TERM}\n')
    code, _, findings, _ = check(capsys, '--names-file', str(names), '--staged')
    assert (code, findings) == (1, ['a.md:2: private name (names file line 2)'])


def test_staged_reads_only_the_index_and_reports_unscannable_files(
    scratch_repo: Path, names: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    commit(scratch_repo, 'docs: a')
    stage(scratch_repo, 'blob.bin', b'\0\0')
    write(scratch_repo, 'a.md', f'clean\n{TERM}\n')  # not staged
    write(scratch_repo, 'untracked.md', f'{TERM}\n')
    code, verdict, findings, _ = check(capsys, '--names-file', str(names), '--staged')
    assert (code, verdict, findings) == (1, FAIL_UNSCANNABLE, ['blob.bin: unscannable (binary)'])


def test_text_scans_the_whole_file_and_works_outside_a_repository(
    tmp_path: Path, names: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / 'plain'
    plain.mkdir()
    monkeypatch.chdir(plain)
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    (plain / 'body.md').write_text(f'title\n\nby {TERM}\n', encoding='utf-8')
    (plain / 'ref.txt').write_text('refs/heads/feat/clean\n', encoding='utf-8')
    code, verdict, findings, _ = check(
        capsys, '--names-file', str(names), '--text', 'body.md', '--text', 'ref.txt'
    )
    assert (code, verdict) == (1, FAIL_NAME)
    assert findings == ['body.md:3: private name (names file line 2)']
    (plain / 'wide.txt').write_bytes('x\n'.encode('utf-16'))
    code, verdict, findings, _ = check(capsys, '--names-file', str(names), '--text', 'wide.txt')
    assert (code, verdict, findings) == (1, FAIL_UNSCANNABLE, ['wide.txt: unscannable (binary)'])
    # --commits and --staged need a repository.
    (plain / 'commits.txt').write_text('', encoding='utf-8')
    for scope in (['--commits', 'commits.txt'], ['--staged']):
        assert check(capsys, '--names-file', str(names), *scope)[:2] == (2, FAIL_USAGE)
    # A --text file that does not exist exits 2.
    assert check(capsys, '--names-file', str(names), '--text', 'gone.md')[:2] == (2, FAIL_USAGE)


def test_the_scopes_combine(
    scratch_repo: Path, names: Path, commit_list: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', f'{TERM}\n')
    sha = commit(scratch_repo, 'docs: a')
    stage(scratch_repo, 'b.md', f'{TERM}\n')
    ref = 'ref.txt'  # untracked, and relative: a temporary path may hold a home path
    write(scratch_repo, ref, f'refs/heads/{TERM}\n')
    _, _, findings, _ = check(
        capsys,
        '--names-file',
        str(names),
        '--commits',
        listing(commit_list, sha),
        '--staged',
        '--text',
        ref,
    )
    assert findings == [
        f'commit {short(sha)} a.md:1: private name (names file line 2)',
        'b.md:1: private name (names file line 2)',
        f'{ref}:1: private name (names file line 2)',
    ]


def test_a_label_holding_a_term_is_redacted(
    scratch_repo: Path,
    names: Path,
    commit_list: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stage(scratch_repo, f'{TERM}/a.md', f'{TERM}\n')
    sha = commit(scratch_repo, 'docs: a')
    stage(scratch_repo, f'{TERM}/b.bin', b'\0')
    text = tmp_path / f'{TERM}.txt'
    text.write_text(f'{TERM}\n', encoding='utf-8')
    code, _, findings, err = check(
        capsys,
        '--names-file',
        str(names),
        '--commits',
        listing(commit_list, sha),
        '--staged',
        '--text',
        str(text),
    )
    assert code == 1
    assert findings[:2] == [
        f'commit {short(sha)} path <name>/a.md: private name (names file line 2)',
        f'commit {short(sha)} <name>/a.md:1: private name (names file line 2)',
    ]
    assert '<name>/b.bin: unscannable (binary)' in findings
    assert all(TERM not in line.lower() for line in [*findings, err])


@pytest.mark.parametrize('segment', [TERM, f'{TERM}-work'])
def test_a_label_with_a_term_in_a_home_path_hides_the_user_segment_once(
    segment: str, scratch_repo: Path, names: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The home path and the term overlap, and one `<name>` covers both. A term that is only part
    of the segment still hides the whole segment."""
    # The doubled slash puts a real file under the home shape in a relative path.
    given = f'sub//home/{segment}/a.md'
    write(scratch_repo, given, f'{TERM}\n')
    code, _, findings, err = check(capsys, '--names-file', str(names), '--text', given)
    assert code == 1
    assert findings == ['sub//home/<name>/a.md:1: private name (names file line 2)']
    assert all(TERM not in line.lower() for line in [*findings, err])


# --- the arguments -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    'extra',
    [
        ['a.md'],
        ['--diff', 'HEAD~1...HEAD'],
        ['--history'],
        ['--event', 'e.json'],
    ],
)
def test_nothing_else_is_in_scope_of_the_egress_mode(
    extra: list[str], scratch_repo: Path, names: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    commit(scratch_repo, 'docs: a')
    code, verdict, _, err = check(capsys, '--names-file', str(names), '--staged', *extra)
    assert (code, verdict) == (2, FAIL_USAGE)
    assert 'egress mode' in err


def test_the_names_file_alone_is_no_scope(
    scratch_repo: Path, names: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, verdict, _, err = check(capsys, '--names-file', str(names))
    assert (code, verdict) == (2, FAIL_USAGE)
    assert '--commits' in err


def test_staged_without_the_names_file_exits_2(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(capsys, '--staged')[:2] == (2, FAIL_USAGE)


def test_a_names_file_without_a_term_or_that_cannot_be_read_exits_2(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / 'empty-terms.txt'
    empty.write_text('# only a comment\n\n   \n', encoding='utf-8')
    for given in (empty, tmp_path):  # a file with no term, and a directory
        code, verdict, _, err = check(capsys, '--names-file', str(given), '--staged')
        assert (code, verdict) == (2, FAIL_USAGE), given
        assert 'names file' in err


def test_the_names_file_is_utf_8_with_one_term_per_line(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listed = tmp_path / 'bom-terms.txt'
    listed.write_bytes(f'﻿# comment\r\n\r\n  {TERM}  \r\na.b\r\n'.encode())
    stage(scratch_repo, 'a.md', f'{TERM}x {TERM.upper()}_y x{TERM} 9{TERM} _{TERM} aXb a.b\n')
    code, _, findings, _ = check(capsys, '--names-file', str(listed), '--staged')
    assert (code, findings) == (
        1,
        [
            # An underscore is a boundary on either side; a letter or a digit is on neither.
            'a.md:1: private name (names file line 3)',
            'a.md:1: private name (names file line 3)',
            'a.md:1: private name (names file line 4)',
        ],
    )


def test_text_without_the_names_file_keeps_its_old_meaning(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = '/' + 'home' + '/' + 'mallory' + '/x'
    body = tmp_path / 'body.md'
    body.write_text(f'at {home}\n', encoding='utf-8')
    code, verdict, _, _ = check(capsys, '--text', str(body))
    assert (code, verdict) == (1, 'railform-verdict: private-refs fail home-path')


# --- railform's own pre-commit hook, the paths half only ---------------------------------------


@pytest.mark.slow
def test_red_proof_railforms_own_pre_commit_hook_passes_a_staged_term(
    tmp_path_factory: pytest.TempPathFactory, outside: dict[str, str]
) -> None:
    """With the names variable set, railform's own `railform-private-refs` hook lets a staged file
    holding a term through, and still refuses a staged home-directory path. The hook is taken from
    railform's own `.pre-commit-config.yaml` and run by pre-commit in a copy of this working
    tree."""
    listed = subprocess.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        pytest.skip('not a git checkout')
    copy = tmp_path_factory.mktemp('railform-copy')
    for name in sorted({os.fsdecode(item) for item in listed.stdout.split(b'\0') if item}):
        if (ROOT / name).is_file():
            (copy / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, copy / name)
    config = yaml.safe_load((ROOT / '.pre-commit-config.yaml').read_text(encoding='utf-8'))
    hooks = [
        {**repo, 'hooks': [hook for hook in repo['hooks'] if hook['id'] == 'railform-private-refs']}
        for repo in config['repos']
        if repo['repo'] == 'local'
    ]
    assert [len(repo['hooks']) for repo in hooks] == [1]
    # Only that hook: the hygiene hooks would need the network to install.
    (copy / '.pre-commit-config.yaml').write_text(
        yaml.safe_dump({'repos': hooks}), encoding='utf-8'
    )
    names_file = tmp_path_factory.mktemp('terms') / 'hook-terms.txt'
    names_file.write_text(f'{TERM}\n', encoding='utf-8')
    env = hook_environment(outside) | {NAMES_ENV: str(names_file)}
    # The copy's own files are committed first, so only the note is staged when the hook runs.
    git(copy, 'init', '-q')
    git(copy, 'add', '-A')
    git(copy, 'commit', '-q', '--no-verify', '-m', 'test: the copy')

    def run_hook() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, '-m', 'pre_commit', 'run', 'railform-private-refs'],
            cwd=copy,
            env=env,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=900,
            check=False,
        )

    stage(copy, 'notes.md', f'a note by {TERM}\n')
    passed = run_hook()
    assert passed.returncode == 0, passed.stdout + passed.stderr
    home = '/' + 'home' + '/' + 'mallory' + '/x'
    stage(copy, 'notes.md', f'a note at {home}\n')
    refused = run_hook()
    assert refused.returncode != 0
    assert 'railform-verdict: private-refs fail home-path' in refused.stdout + refused.stderr
