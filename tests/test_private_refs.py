"""The private-reference check, development-rails spec §3: each test is a red proof.

Home paths are built at run time from parts, so no file of this repository carries one.
"""

import re
import subprocess
from pathlib import Path

import pytest

from railform.cli import main

ROOT = Path(__file__).resolve().parents[1]
NAMES_ENV = 'RAILFORM_PRIVATE_NAMES'
BACKSLASH = chr(92)
# Not a default placeholder, and nobody's name.
STRANGER = 'mallory'

SHAPE_NAMES = ('backslash', 'slash', 'escaped', 'slug', 'macos', 'linux')


def home(shape: str, name: str = STRANGER, drive: str = 'C') -> str:
    """A path of one of the six shapes, with `name` as the user segment."""
    parts = {
        'backslash': f'{drive}:{BACKSLASH}Users{BACKSLASH}{name}{BACKSLASH}project{BACKSLASH}x.py',
        'slash': f'{drive}:/Users/{name}/project/x.py',
        'escaped': f'{drive}:{BACKSLASH * 2}Users{BACKSLASH * 2}{name}{BACKSLASH * 2}project',
        'slug': f'{drive}--Users-{name}-project',
        'macos': f'/Users/{name}/project/x.py',
        'linux': f'/home/{name}/project/x.py',
    }
    return parts[shape]


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def stage(repo: Path, name: str, text: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding='utf-8', newline='\n')
    git(repo, 'add', name)


def commit(repo: Path, message: str = 'chore: change') -> str:
    git(repo, '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


def check(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, list[str]]:
    """Run the verb: the exit code, the whole output, and the findings before the verdict."""
    code = main(['private-refs', *args])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert re.fullmatch(r'railform-verdict: private-refs (pass|fail) [a-z-]+', lines[-1])
    return code, out, lines[:-1]


def verdict(out: str) -> str:
    return out.splitlines()[-1]


@pytest.fixture(autouse=True)
def no_names_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own names file must never change what these tests see."""
    monkeypatch.delenv(NAMES_ENV, raising=False)


@pytest.fixture
def names_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / 'names-under-test.txt'
    monkeypatch.setenv(NAMES_ENV, str(path))
    return path


# --- the paths half ----------------------------------------------------------------------------


@pytest.mark.parametrize('drive', ['C', 'd'])
@pytest.mark.parametrize('shape', SHAPE_NAMES)
def test_each_of_the_six_shapes_fails_with_any_drive_letter(
    shape: str, drive: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'notes.md', f'see {home(shape, drive=drive)} for details\n')
    code, out, findings = check(capsys)
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail home-path'
    assert len(findings) == 1
    assert findings[0].startswith('notes.md:1: home path ')
    assert STRANGER not in out


@pytest.mark.parametrize('shape', ['backslash', 'slash', 'escaped', 'slug'])
def test_the_windows_shapes_ignore_case(
    shape: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'notes.md', home(shape).replace('Users', 'USERS') + '\n')
    code, out, _ = check(capsys)
    assert code == 1
    assert verdict(out).endswith('fail home-path')
    assert STRANGER not in out


def test_the_posix_shapes_are_exact(scratch_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stage(scratch_repo, 'notes.md', f'/USERS/{STRANGER}/x /HOME/{STRANGER}/x\n')
    assert check(capsys)[0] == 0


def test_a_finding_shows_the_shape_and_hides_the_user_segment(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', f'first\nat {home("backslash")}\n')
    _, out, findings = check(capsys)
    assert findings == [f'a.txt:2: home path C:{BACKSLASH}Users{BACKSLASH}<name>{BACKSLASH}']
    assert STRANGER not in out


def test_a_tracked_file_carrying_a_home_path_fails_a_run_over_the_tracked_files(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'docs/a.md', 'clean\n')
    stage(scratch_repo, 'docs/b.md', f'cwd: {home("linux")}\n')
    commit(scratch_repo)
    code, out, findings = check(capsys)
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail home-path'
    assert [line.split(':')[0] for line in findings] == ['docs/b.md']


def test_an_untracked_file_is_not_in_the_tracked_scope(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', 'clean\n')
    (scratch_repo / 'scratch.txt').write_text(home('linux'), encoding='utf-8')
    assert check(capsys)[0] == 0


def test_paths_given_are_the_whole_scope(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'dirty.txt', home('macos') + '\n')
    stage(scratch_repo, 'clean.txt', 'nothing here\n')
    assert check(capsys, 'clean.txt')[0] == 0
    code, _, findings = check(capsys, 'clean.txt', 'dirty.txt')
    assert code == 1
    assert [line.split(':')[0] for line in findings] == ['dirty.txt']


def test_a_path_that_does_not_exist_is_a_usage_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = check(capsys, 'missing.txt')
    assert code == 2
    assert verdict(out) == 'railform-verdict: private-refs fail usage'


def test_binary_files_are_skipped(scratch_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = b'\0\1\2' + home('linux').encode()
    (scratch_repo / 'blob.bin').write_bytes(data)
    git(scratch_repo, 'add', 'blob.bin')
    assert check(capsys)[0] == 0


def test_two_hits_in_one_line_and_the_same_path_in_two_shapes_are_reported_once_each(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `D:/Users/<name>/` also contains `/Users/<name>/`: one hit, not two.
    stage(scratch_repo, 'a.txt', f'{home("slash")} and {home("linux")}\n')
    _, _, findings = check(capsys)
    assert len(findings) == 2


@pytest.mark.parametrize('name', ['me', 'x', 'someone', 'alice', 'user', 'runner', 'Alice', 'ME'])
@pytest.mark.parametrize('shape', SHAPE_NAMES)
def test_a_default_placeholder_passes_in_every_shape(
    shape: str, name: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', home(shape, name) + '\n')
    assert check(capsys)[0] == 0


@pytest.mark.parametrize('name', ['<name>', '%USERNAME%', '$USER', '${USER}', '{user}', '*'])
def test_a_plainly_placeholder_segment_passes(
    name: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for shape in SHAPE_NAMES:
        stage(scratch_repo, f'{shape}.md', home(shape, name) + '\n')
    assert check(capsys)[0] == 0


def test_the_placeholder_paths_craft_collection_uses_under_its_skills_pass(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (
        f'Windows: C:{BACKSLASH}Users{BACKSLASH}me{BACKSLASH}.claude{BACKSLASH}feedback\n'
        'macOS: /Users/me/.claude/feedback\n'
        'Linux: /home/me/.claude/feedback\n'
        'Slug: C--Users-me-Documents-project\n'
    )
    stage(scratch_repo, 'skills/tool-feedback/SKILL.md', text)
    code, out, findings = check(capsys)
    assert (code, findings) == (0, [])
    assert verdict(out) == 'railform-verdict: private-refs pass ok'


def test_adding_a_name_to_the_placeholders_turns_a_failing_path_into_a_pass(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', home('linux') + '\n')
    assert check(capsys)[0] == 1
    defaults = ['me', 'x', 'someone', 'alice', 'user', 'runner']
    names = ', '.join(f'"{name}"' for name in [*defaults, STRANGER])
    (scratch_repo / 'railform.toml').write_text(
        f'[private_refs]\nplaceholders = [{names}]\n', encoding='utf-8'
    )
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (0, 'railform-verdict: private-refs pass ok')


def test_a_frame_that_cannot_be_read_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'railform.toml').write_text('this is = = not toml', encoding='utf-8')
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (2, 'railform-verdict: private-refs fail usage')


# --- the names half ----------------------------------------------------------------------------


def test_a_names_file_makes_a_matching_staged_file_fail(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('# the private names\n\nquillfeather\nzebrawood\n', encoding='utf-8')
    stage(scratch_repo, 'staged.md', 'a line\nthanks to Zebrawood for the review\n')
    code, out, findings = check(capsys, 'staged.md')
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail private-name'
    assert findings == ['staged.md:2: private name (names file line 4)']
    assert 'zebrawood' not in out.lower()


def test_a_name_in_a_commit_message_of_the_range_fails(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'a.txt', 'one\n')
    base = commit(scratch_repo)
    stage(scratch_repo, 'a.txt', 'two\n')
    commit(scratch_repo, 'fix: thanks quillfeather')
    code, out, findings = check(capsys, '--diff', f'{base}...HEAD')
    assert code == 1
    assert len(findings) == 1
    assert findings[0].startswith('commit ')
    assert 'quillfeather' not in out


def test_a_names_term_matches_whole_tokens_only(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('alice\n', encoding='utf-8')
    stage(scratch_repo, 'prose.md', 'written by alice.\n')
    code, _, findings = check(capsys)
    assert code == 1
    assert findings == ['prose.md:1: private name (names file line 1)']
    (scratch_repo / 'prose.md').unlink()
    git(scratch_repo, 'rm', '-q', '--cached', 'prose.md')
    stage(scratch_repo, 'other.md', 'see aliceworks/railform and malice and alice2 and 9alice\n')
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (0, 'railform-verdict: private-refs pass ok')


@pytest.mark.parametrize(
    'line', ['alice', 'ALICE', '(alice)', 'alice_x', 'a/alice/b', 'alice-b', '"Alice"']
)
def test_a_term_is_bounded_by_anything_but_a_letter_or_digit(
    line: str, scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('alice\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', line + '\n')
    assert check(capsys)[0] == 1


def test_a_term_with_regular_expression_characters_is_literal(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('a.b+c\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'axb+c and a.b+c\n')
    _, _, findings = check(capsys)
    assert findings == ['a.md:1: private name (names file line 1)']


def test_blank_and_comment_lines_in_the_names_file_are_not_terms(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('\n   \n# secretterm\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'secretterm and # secretterm\n')
    assert check(capsys)[0] == 0


def test_the_names_half_is_silent_without_the_variable_or_the_file(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'quillfeather\n')
    assert check(capsys)[0] == 0
    monkeypatch.setenv(NAMES_ENV, str(scratch_repo / 'no-such-file.txt'))
    assert check(capsys)[0] == 0


def test_require_names_without_a_file_fails_names_file_missing(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    code, out, findings = check(capsys, '--require-names')
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail names-file-missing'
    assert findings == [f'the names file named by ${NAMES_ENV} is not readable']


def test_require_names_with_a_missing_file_fails_without_printing_its_path(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = scratch_repo / 'quillfeather-names.txt'
    monkeypatch.setenv(NAMES_ENV, str(missing))
    code, out, _ = check(capsys, '--require-names')
    assert code == 1
    assert verdict(out).endswith('fail names-file-missing')
    assert 'quillfeather' not in out


def test_require_names_with_a_file_passes_a_clean_scope(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'clean\n')
    assert check(capsys, '--require-names')[0] == 0


def test_the_variable_comes_from_the_frame(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / 'other.txt'
    path.write_text('quillfeather\n', encoding='utf-8')
    monkeypatch.setenv('SOME_OTHER_VARIABLE', str(path))
    stage(scratch_repo, 'a.md', 'quillfeather\n')
    assert check(capsys)[0] == 0
    (scratch_repo / 'railform.toml').write_text(
        '[private_refs]\nnames_file_env = "SOME_OTHER_VARIABLE"\n', encoding='utf-8'
    )
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail private-name')


def test_a_path_that_carries_a_name_is_hidden_in_the_finding(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'quillfeather.md', 'quillfeather\n')
    code, out, findings = check(capsys)
    assert code == 1
    assert findings == ['<name>.md:1: private name (names file line 1)']
    assert 'quillfeather' not in out


# --- the scopes --------------------------------------------------------------------------------


def test_a_diff_checks_the_files_the_range_changes_at_head_and_not_the_rest(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'old.md', home('linux') + '\n')
    base = commit(scratch_repo)
    stage(scratch_repo, 'new.md', 'clean\n')
    commit(scratch_repo)
    assert check(capsys, '--diff', f'{base}...HEAD')[0] == 0
    assert check(capsys)[0] == 1  # the whole tracked scope still sees old.md
    stage(scratch_repo, 'new.md', home('macos') + '\n')
    commit(scratch_repo)
    code, _, findings = check(capsys, '--diff', f'{base}...HEAD')
    assert code == 1
    assert [line.split(':')[0] for line in findings] == ['new.md']


def test_a_deleted_file_in_the_range_is_not_read(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'old.md', home('linux') + '\n')
    base = commit(scratch_repo)
    git(scratch_repo, 'rm', '-q', 'old.md')
    commit(scratch_repo, 'chore: drop it')
    assert check(capsys, '--diff', f'{base}...HEAD')[0] == 0


def test_a_diff_checks_the_commit_messages_of_the_range(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', 'one\n')
    base = commit(scratch_repo)
    stage(scratch_repo, 'a.txt', 'two\n')
    commit(scratch_repo, f'fix: log said {home("linux")}')
    code, out, findings = check(capsys, '--diff', f'{base}...HEAD')
    assert code == 1
    assert verdict(out).endswith('fail home-path')
    assert findings[0].startswith('commit ')
    assert STRANGER not in out


def test_a_bad_range_is_a_usage_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', 'one\n')
    commit(scratch_repo)
    assert check(capsys, '--diff', 'HEAD')[0] == 2
    assert check(capsys, '--diff', 'nosuchref...HEAD')[0] == 2


def test_text_files_join_any_scope(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', 'clean\n')
    title = tmp_path / 'title.txt'
    body = tmp_path / 'body.txt'
    title.write_text('feat: something\n', encoding='utf-8')
    body.write_text(f'Tested on {home("slash")}\n', encoding='utf-8')
    assert check(capsys, '--text', str(title))[0] == 0
    code, out, findings = check(capsys, '--text', str(title), '--text', str(body))
    assert code == 1
    assert len(findings) == 1
    assert STRANGER not in out
    assert check(capsys, 'a.txt', '--text', str(title), str(body))[0] == 1


def test_a_text_file_that_cannot_be_read_is_a_usage_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(capsys, '--text', 'missing.txt')[0] == 2


def test_outside_a_git_repository_it_exits_2(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path_factory.mktemp('outside')
    monkeypatch.chdir(outside)
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(outside.parent))
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (2, 'railform-verdict: private-refs fail usage')


# --- railform itself ---------------------------------------------------------------------------


def _tracked_in_railform() -> list[str]:
    done = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip('not a git checkout')
    return [item.decode() for item in done.stdout.split(b'\0') if item]


def test_no_names_list_is_tracked_in_railform() -> None:
    suspicious = re.compile(
        r'(private|owner|secret|deny|block|banned|forbidden)[-_.]?(names?|terms?|words?|list)'
        r'|(names?|terms?)[-_.]?(file|list)',
        re.IGNORECASE,
    )
    tracked = _tracked_in_railform()
    assert tracked
    assert [name for name in tracked if suspicious.search(Path(name).name)] == []
    # The default frame names the variable, never a list.
    frame_text = (ROOT / 'src' / 'railform' / 'default_frame.toml').read_text(encoding='utf-8')
    assert 'names_file_env = "RAILFORM_PRIVATE_NAMES"' in frame_text


def test_railforms_own_tracked_files_pass_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tracked_in_railform()
    monkeypatch.chdir(ROOT)
    code, out, findings = check(capsys)
    assert (code, findings) == (0, []), out
