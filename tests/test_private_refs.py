"""The private-reference check, development-rails spec §3: each test is a red proof.

Home paths are built at run time from parts, so no file of this repository carries one.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from railform import frame
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
    assert findings == [f'a.txt:2: home path C:{BACKSLASH}Users{BACKSLASH}<name>']
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
    # `home("slash")` is a drive path with a slash. The drive shape and the bare `/Users/` shape
    # inside it both match it, and it counts once, so two paths give two findings.
    stage(scratch_repo, 'a.txt', f'{home("slash")} and {home("linux")}\n')
    _, _, findings = check(capsys)
    assert len(findings) == 2


@pytest.mark.parametrize(
    ('line', 'fails'),
    [
        (f'cd C:{BACKSLASH}Users{BACKSLASH}{STRANGER}', True),
        (f'cd C:{BACKSLASH}Users{BACKSLASH}me', False),
        (f'HOME=/home/{STRANGER}', True),
        ('HOME=/home/me', False),
        (f'"/Users/{STRANGER}"', True),
        (f"'D:/Users/{STRANGER}'", True),
        (f'open /home/{STRANGER} and go', True),
        ('open /home/runner and go', False),
        (f'file:///home/{STRANGER}/', True),
    ],
)
def test_a_segment_also_ends_at_whitespace_a_quote_or_the_end_of_the_line(
    line: str, fails: bool, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', line + '\n')
    code, out, _ = check(capsys)
    assert code == int(fails)
    assert STRANGER not in out


@pytest.mark.parametrize(
    ('line', 'fails'),
    [
        ('../home/components/Button', False),
        ('import x from "src/home/views/index"', False),
        ('https://docs.example.com/home/getting-started/', False),
        ('x.com/home/y/ and my-app/home/z/', False),
        (f'file:///home/{STRANGER}/', True),
        (f'cd /home/{STRANGER}/code', True),
        (f'/mnt/c/Users/{STRANGER}/code', True),
        (f'app/Users/{STRANGER}/code', True),
    ],
)
def test_the_home_shape_is_not_read_inside_a_relative_path_or_a_url(
    line: str, fails: bool, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', line + '\n')
    assert check(capsys)[0] == int(fails)


@pytest.mark.parametrize('name', ['Public', 'Default', 'shared', 'RunnerAdmin'])
def test_the_system_folders_are_default_placeholders(
    name: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for shape in SHAPE_NAMES:
        stage(scratch_repo, f'{shape}.md', home(shape, name) + '\n')
    assert check(capsys)[0] == 0


@pytest.mark.parametrize(
    'line',
    [
        f'PS C:{BACKSLASH}Users{BACKSLASH}{STRANGER}>',
        f'C:{BACKSLASH}Users{BACKSLASH}{STRANGER}> dir',
        f'<code>/home/{STRANGER}</code>',
        f'/home/{STRANGER}<br>',
        f'(see /home/{STRANGER})',
        f'[/Users/{STRANGER}]',
        f'"/home/{STRANGER}",',
        f'/home/{STRANGER}; /home/{STRANGER}|',
        f'`/home/{STRANGER}`',
        f'`C:/Users/{STRANGER}`',
        f'/Users/{STRANGER}:',
        f'/Users/{STRANGER}.',
    ],
)
def test_punctuation_next_to_a_name_does_not_hide_a_leak(
    line: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', line + '\n')
    code, out, _ = check(capsys)
    assert code == 1
    assert STRANGER not in out


@pytest.mark.parametrize(
    'line',
    [
        '/home/me,',
        '/Users/me.',
        '/Users/me..',
        '(e.g. /home/user)',
        'HOME=/home/me;',
        '[/Users/me]',
        '/Users/me:',
        'the `/home/` folder and the `/Users/` folder',
        '<code>/home/<name></code>',
        '/home/{user}/ and /Users/<name>/',
        '/home/...',
    ],
)
def test_punctuation_next_to_a_placeholder_is_not_part_of_the_name(
    line: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', line + '\n')
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (0, 'railform-verdict: private-refs pass ok')


def test_a_slug_segment_also_stops_at_punctuation(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.txt', f'`C--Users-{STRANGER}-x` and C--Users-(me)-x\n')
    code, out, findings = check(capsys)
    assert code == 1
    assert len(findings) == 1
    assert STRANGER not in out


def test_a_frame_load_error_does_not_print_a_home_path(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(root: Path) -> dict[str, object]:
        raise frame.FrameError(f'railform.toml: cannot read: {home("linux")}')

    monkeypatch.setattr(frame, 'load', fail)
    code = main(['private-refs'])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == 'railform-verdict: private-refs fail usage\n'
    assert STRANGER not in captured.err
    assert '/home/<name>/' in captured.err


def test_a_private_name_inside_a_home_path_shape_is_hidden_there(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The term is not the user segment but sits in the shape that is shown: `clip` hides it.
    names_file.write_text('users\n', encoding='utf-8')
    stage(scratch_repo, 'a.txt', f'{home("backslash")}\n')
    code, out, findings = check(capsys)
    assert code == 1
    assert findings == [
        f'a.txt:1: home path C:{BACKSLASH}<name>{BACKSLASH}<name>',
        'a.txt:1: private name (names file line 1)',
    ]
    assert 'Users' not in out
    assert STRANGER not in out


def test_crlf_input_is_read_line_by_line(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'win.txt').write_bytes(f'first\r\ncd {home("backslash")}\r\n'.encode())
    git(scratch_repo, 'add', 'win.txt')
    code, out, findings = check(capsys)
    assert code == 1
    assert findings == [f'win.txt:2: home path C:{BACKSLASH}Users{BACKSLASH}<name>']
    assert '\r' not in out


def test_a_usage_error_never_prints_the_user_segment_of_a_given_path(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = f'{home("linux")}.missing'
    for argv in (['--text', missing], [missing]):
        code = main(['private-refs', *argv])
        captured = capsys.readouterr()
        assert code == 2
        assert captured.out == 'railform-verdict: private-refs fail usage\n'
        assert STRANGER not in captured.err
        assert '/home/<name>/' in captured.err


def test_overlapping_hits_are_hidden_once(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The private term is the user segment itself: the name is replaced once, not garbled.
    names_file.write_text(f'{STRANGER}\n', encoding='utf-8')
    stage(scratch_repo, 'a.txt', f'{home("linux")}\n')
    code, out, findings = check(capsys)
    assert code == 1
    assert STRANGER not in out
    assert findings == [
        'a.txt:1: home path /home/<name>',
        'a.txt:1: private name (names file line 1)',
    ]


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


ELLIPSIS = chr(0x2026)


@pytest.mark.parametrize('name', [ELLIPSIS, ELLIPSIS * 2, '...', '.', f'.{ELLIPSIS}.'])
def test_red_proof_a_segment_made_only_of_dots_is_a_placeholder(
    name: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The development-rails spec, §13 (the pilots): `C:\Users\…\` and `/home/.../` are written
    # as placeholders, a user segment of full stops or ellipsis characters only.
    for shape in SHAPE_NAMES:
        stage(scratch_repo, f'{shape}.md', home(shape, name) + '\n')
    assert check(capsys)[0] == 0


@pytest.mark.parametrize('name', [f'bob{ELLIPSIS}', f'{ELLIPSIS}bob', 'b.o.b'])
def test_a_segment_with_dots_and_letters_is_still_a_name(
    name: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', home('linux', name) + '\n')
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail home-path')


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


def test_the_names_half_is_silent_without_the_variable(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'quillfeather\n')
    code, out, findings = check(capsys)
    assert (code, verdict(out), findings) == (0, 'railform-verdict: private-refs pass ok', [])


def test_red_proof_a_variable_that_names_a_missing_file_fails_closed(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A stale path in the variable would otherwise turn the names half off without a word: the
    # run would pass on exactly the machine that was meant to be checking.
    stage(scratch_repo, 'a.md', 'clean\n')
    missing = scratch_repo / 'quillfeather-names.txt'
    monkeypatch.setenv(NAMES_ENV, str(missing))
    code, out, findings = check(capsys)
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail names-file-missing'
    assert findings == [f'the names file named by ${NAMES_ENV} is not readable']
    assert 'quillfeather' not in out


def test_a_variable_that_names_a_directory_fails_closed_too(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    monkeypatch.setenv(NAMES_ENV, str(scratch_repo))
    assert verdict(check(capsys)[1]).endswith('fail names-file-missing')


def test_a_names_file_that_cannot_be_read_is_a_configuration_error(
    scratch_repo: Path,
    names_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The file is there, so this is not names-file-missing: it is a usage error (exit 2), and the
    # run still fails closed.
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'clean\n')
    real = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self == names_file:
            raise PermissionError('denied')
        return real(self)

    monkeypatch.setattr(Path, 'read_bytes', refuse)
    code, out, _ = check(capsys)
    assert code == 2
    assert verdict(out) == 'railform-verdict: private-refs fail usage'


def test_an_empty_variable_is_unset(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    monkeypatch.setenv(NAMES_ENV, '')
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


def test_a_names_file_with_crlf_lines_finds_every_case(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_bytes(b'zebrawood\r\n')
    stage(scratch_repo, 'a.md', 'one zebrawood\ntwo Zebrawood\nthree ZEBRAWOOD, four\nclean\n')
    code, out, findings = check(capsys)
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail private-name'
    assert findings == [f'a.md:{n}: private name (names file line 1)' for n in (1, 2, 3)]
    assert 'zebrawood' not in out.lower()


# --- the switch for the names half -------------------------------------------------------------

SWITCH_OFF = (
    '[private_refs]\nnames_enabled = false\n'
    '[[exceptions]]\nkey = "private_refs.names_enabled"\n'
    'reason = "tracked files carry the listed names by design"\n'
)
BASE_FRAME = '[project]\ntype = "python-package"\n'


@pytest.mark.parametrize('flags', [(), ('--require-names',)])
def test_red_proof_a_reasoned_switch_turns_the_names_half_off(
    flags: tuple[str, ...],
    scratch_repo: Path,
    names_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    names_file.write_bytes(b'zebrawood\r\n')
    (scratch_repo / 'railform.toml').write_text(SWITCH_OFF, encoding='utf-8')
    stage(scratch_repo, 'a.md', 'thanks to zebrawood\n')
    code, out, findings = check(capsys, *flags, 'a.md')
    assert (code, verdict(out)) == (0, 'railform-verdict: private-refs pass ok')
    assert len(findings) == 1
    assert 'private_refs.names_enabled' in findings[0]
    assert 'zebrawood' not in out.lower()


def test_with_the_switch_off_the_file_is_not_consulted_and_home_paths_are_still_judged(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(NAMES_ENV, str(scratch_repo / 'no-such-file.txt'))
    (scratch_repo / 'railform.toml').write_text(SWITCH_OFF, encoding='utf-8')
    stage(scratch_repo, 'a.md', 'clean\n')
    assert check(capsys, '--require-names')[0] == 0
    stage(scratch_repo, 'b.md', home('linux') + '\n')
    code, out, _ = check(capsys)
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail home-path')


def test_the_switch_without_a_row_fails_the_frame_check_and_with_one_passes(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'railform.toml').write_text(
        '[private_refs]\nnames_enabled = false\n', encoding='utf-8'
    )
    assert main(['frame', '--check']) == 1
    assert capsys.readouterr().out.splitlines()[-1].endswith('fail unlisted-departure')
    (scratch_repo / 'railform.toml').write_text(SWITCH_OFF, encoding='utf-8')
    assert main(['frame', '--check']) == 0


def test_red_proof_a_range_that_adds_the_switch_is_judged_by_the_merge_base(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_bytes(b'zebrawood\r\n')
    stage(scratch_repo, 'railform.toml', BASE_FRAME)
    base = commit(scratch_repo)
    stage(scratch_repo, 'railform.toml', BASE_FRAME + SWITCH_OFF)
    stage(scratch_repo, 'notes.md', 'thanks to zebrawood\n')
    commit(scratch_repo)
    code, out, findings = check(capsys, '--diff', f'{base}...HEAD')
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail private-name')
    assert any(line.startswith('notes.md:1: private name') for line in findings)
    # The working tree, outside the range, honours its own frame.
    assert check(capsys, 'notes.md')[0] == 0


def test_red_proof_a_placeholder_added_by_the_range_does_not_hide_a_home_path(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'railform.toml', BASE_FRAME)
    base = commit(scratch_repo)
    stage(
        scratch_repo,
        'railform.toml',
        BASE_FRAME + f'[private_refs]\nplaceholders = ["{STRANGER}"]\n',
    )
    stage(scratch_repo, 'notes.md', home('linux') + '\n')
    commit(scratch_repo)
    code, out, _ = check(capsys, '--diff', f'{base}...HEAD')
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail home-path')
    assert STRANGER not in out


def test_a_placeholder_the_merge_base_already_has_still_passes_in_the_range(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(
        scratch_repo,
        'railform.toml',
        BASE_FRAME + f'[private_refs]\nplaceholders = ["{STRANGER}"]\n',
    )
    base = commit(scratch_repo)
    stage(scratch_repo, 'notes.md', home('linux') + '\n')
    commit(scratch_repo)
    assert check(capsys, '--diff', f'{base}...HEAD')[0] == 0


def test_a_merge_base_without_a_frame_leaves_the_working_trees_frame_in_force(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_bytes(b'zebrawood\r\n')
    stage(scratch_repo, 'a.txt', 'one\n')
    base = commit(scratch_repo)
    stage(scratch_repo, 'railform.toml', SWITCH_OFF)
    stage(scratch_repo, 'notes.md', 'zebrawood\n')
    commit(scratch_repo)
    code, out, findings = check(capsys, '--diff', f'{base}...HEAD')
    assert (code, verdict(out)) == (0, 'railform-verdict: private-refs pass ok')
    assert any('names_enabled' in line for line in findings)


def test_a_merge_base_frame_that_cannot_be_read_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'railform.toml', 'this is = = not toml')
    base = commit(scratch_repo)
    stage(scratch_repo, 'railform.toml', BASE_FRAME)
    stage(scratch_repo, 'a.txt', 'two\n')
    commit(scratch_repo)
    code, out, _ = check(capsys, '--diff', f'{base}...HEAD')
    assert (code, verdict(out)) == (2, 'railform-verdict: private-refs fail usage')


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
    assert check(capsys, 'a.txt', '--text', str(title), '--text', str(body))[0] == 1


def test_text_takes_one_file_per_flag_so_trailing_names_stay_paths(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # pre-commit appends the staged file names after the options.
    stage(scratch_repo, 'dirty.txt', home('linux') + '\n')
    title = tmp_path / 'title.txt'
    title.write_text('feat: something\n', encoding='utf-8')
    code, _, findings = check(capsys, '--text', str(title), 'dirty.txt')
    assert code == 1
    assert [line.split(':')[0] for line in findings] == ['dirty.txt']


def test_a_home_path_inside_a_label_is_redacted(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = tmp_path / f'{home("slug")}.txt'
    body.write_text(f'at {home("linux")}\n', encoding='utf-8')
    code, out, findings = check(capsys, '--text', str(body))
    assert code == 1
    assert STRANGER not in out
    assert len(findings) == 1
    assert '<name>' in findings[0].split(':1:')[0]


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


# --- the pull request's title and body (--event) -----------------------------------------------


def _event(tmp_path: Path, payload: object) -> str:
    """Each payload in a file of its own, so a test can hold several."""
    path = tmp_path / f'event-{len(list(tmp_path.glob("event-*.json")))}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return str(path)


def _pull_request(tmp_path: Path, title: object, body: object) -> str:
    return _event(tmp_path, {'pull_request': {'title': title, 'body': body}})


def test_red_proof_a_home_path_in_the_pull_request_body_fails(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _pull_request(tmp_path, 'docs: a note', 'A note.\n')
    assert check(capsys, '--event', clean)[0] == 0
    leaked = _pull_request(tmp_path, 'docs: a note', f'A note.\n\nRun from {home("linux")}\n')
    code, out, findings = check(capsys, '--event', leaked)
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail home-path'
    assert findings == ['pull request body:3: home path /home/<name>']
    assert STRANGER not in out


def test_a_home_path_in_the_pull_request_title_fails_under_its_own_label(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leaked = _pull_request(tmp_path, f'fix: read {home("slash")}', None)
    code, out, findings = check(capsys, '--event', leaked)
    assert code == 1
    assert findings == ['pull request title:1: home path C:/Users/<name>']
    assert STRANGER not in out


def test_a_private_name_in_the_pull_request_text_fails(
    scratch_repo: Path, tmp_path: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text(f'{STRANGER}\n', encoding='utf-8')
    leaked = _pull_request(tmp_path, 'docs: a note', f'Asked by {STRANGER}.')
    code, out, findings = check(capsys, '--event', leaked)
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail private-name'
    assert findings == ['pull request body:1: private name (names file line 1)']
    assert STRANGER not in out


def test_a_null_or_absent_title_or_body_is_empty_text(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(capsys, '--event', _pull_request(tmp_path, 'docs: x', None))[0] == 0
    assert check(capsys, '--event', _event(tmp_path, {'pull_request': {}}))[0] == 0


@pytest.mark.parametrize(
    'payload',
    [{'action': 'push'}, {'pull_request': None}, {'pull_request': {'title': 3}}, ['x']],
    ids=['no-pull-request', 'null-pull-request', 'title-not-text', 'not-an-object'],
)
def test_an_event_without_a_readable_pull_request_is_a_usage_error(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: object
) -> None:
    code, out, _ = check(capsys, '--event', _event(tmp_path, payload))
    assert (code, verdict(out)) == (2, 'railform-verdict: private-refs fail usage')


def test_an_event_file_that_cannot_be_read_is_a_usage_error(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(capsys, '--event', str(tmp_path / 'missing.json'))[0] == 2
    broken = tmp_path / 'broken.json'
    broken.write_text('{', encoding='utf-8')
    assert check(capsys, '--event', str(broken))[0] == 2


def test_the_event_joins_the_diff_scope(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # What the commit-lane job runs: the range's files and messages, and the pull request text.
    stage(scratch_repo, 'a.txt', 'one\n')
    base = commit(scratch_repo)
    stage(scratch_repo, 'a.txt', 'two\n')
    commit(scratch_repo, 'fix: two')
    clean = _pull_request(tmp_path, 'fix: two', 'Two.')
    leaked = _pull_request(tmp_path, 'fix: two', f'Two, from {home("macos")}.')
    passed = check(capsys, '--diff', f'{base}...HEAD', '--event', clean)
    assert passed[0] == 0, passed[1]
    code, out, findings = check(capsys, '--diff', f'{base}...HEAD', '--event', leaked)
    assert code == 1
    assert [line.split(':')[0] for line in findings] == ['pull request body']
    assert STRANGER not in out
    stage(scratch_repo, 'a.txt', 'three\n')
    commit(scratch_repo, f'fix: the log said {home("linux")}')
    code, _, findings = check(capsys, '--diff', f'{base}...HEAD', '--event', clean)
    assert code == 1
    assert [line.split(':')[0][:7] for line in findings] == ['commit ']


# --- railform itself ---------------------------------------------------------------------------


def _tracked_in_railform() -> list[str]:
    done = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=False)
    if done.returncode != 0:
        pytest.skip('not a git checkout')
    return [item.decode() for item in done.stdout.split(b'\0') if item]


def test_no_names_list_is_tracked_in_railform() -> None:
    suspicious = re.compile(
        r'(private|owner|secret|deny|block|banned|forbidden)[-_.]?(names?|terms?|words?|list)'
        r'|(names?|terms?)[-_.]?(file|list)'
        r'|^names?\.',
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


# --- explicit files outside a repository -------------------------------------------------------


@pytest.fixture
def outside_repo(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A plain directory under no repository, entered."""
    outside = tmp_path_factory.mktemp('plain')
    monkeypatch.chdir(outside)
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(outside.parent))
    return outside


def test_a_text_file_outside_a_repository_is_checked_against_the_names_file(
    outside_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('# the private names\nquillfeather\n', encoding='utf-8')
    (outside_repo / 'report.md').write_text('a clean report\n', encoding='utf-8')
    code, out, findings = check(capsys, '--require-names', '--text', 'report.md')
    assert (code, findings) == (0, []), out
    (outside_repo / 'report.md').write_text('clean\nthanks, Quillfeather\n', encoding='utf-8')
    code, out, findings = check(capsys, '--require-names', '--text', 'report.md')
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail private-name'
    assert findings == ['report.md:2: private name (names file line 2)']
    assert 'quillfeather' not in out.lower()


def test_outside_a_repository_a_missing_names_file_still_fails_when_required(
    outside_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (outside_repo / 'report.md').write_text('clean\n', encoding='utf-8')
    code, out, _ = check(capsys, '--require-names', '--text', 'report.md')
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail names-file-missing')


def test_outside_a_repository_the_default_frame_judges_home_paths(
    outside_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (outside_repo / 'ok.md').write_text(f'at {home("linux", name="me")}\n', encoding='utf-8')
    (outside_repo / 'bad.md').write_text(f'at {home("linux")}\n', encoding='utf-8')
    assert check(capsys, '--text', 'ok.md')[0] == 0
    code, out, findings = check(capsys, '--text', 'bad.md')
    assert code == 1
    assert findings == ['bad.md:1: home path /home/<name>']
    assert STRANGER not in out


def test_outside_a_repository_a_file_given_as_a_path_is_checked(
    outside_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    (outside_repo / 'a.md').write_text('clean\n', encoding='utf-8')
    (outside_repo / 'b.md').write_text('quillfeather\n', encoding='utf-8')
    assert check(capsys, 'a.md')[0] == 0
    code, _, findings = check(capsys, 'a.md', 'b.md')
    assert (code, findings) == (1, ['b.md:1: private name (names file line 1)'])


@pytest.mark.parametrize(
    'args',
    [[], ['--diff', 'main...HEAD'], ['--history'], ['.']],
    ids=['tracked-files', 'diff', 'history', 'directory'],
)
def test_outside_a_repository_a_scope_that_needs_one_exits_2(
    args: list[str], outside_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = check(capsys, *args)
    assert (code, verdict(out)) == (2, 'railform-verdict: private-refs fail usage')


@pytest.mark.parametrize('how', ['inside-git-dir', 'dubious-owner'])
def test_a_repository_git_refuses_to_read_is_a_usage_error_not_no_repository(
    how: str,
    scratch_repo: Path,
    names_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The frame must not fall back to the default when a repository exists but cannot be read:
    # the names variable of its railform.toml would be skipped and the check would pass.
    names_file.write_text('quillfeather\n', encoding='utf-8')
    report = scratch_repo / 'rep.md'
    report.write_text('thanks, quillfeather\n', encoding='utf-8')
    if how == 'inside-git-dir':
        monkeypatch.chdir(scratch_repo / '.git')
    else:
        monkeypatch.setenv('GIT_TEST_ASSUME_DIFFERENT_OWNER', '1')
    code, out, _ = check(capsys, '--require-names', '--text', str(report))
    assert (code, verdict(out)) == (2, 'railform-verdict: private-refs fail usage')


def test_outside_a_repository_the_event_alone_is_a_scope(
    outside_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = tmp_path / 'event.json'
    event.write_text(
        json.dumps({'pull_request': {'title': 'feat: x', 'body': home('linux')}}), encoding='utf-8'
    )
    code, _, findings = check(capsys, '--event', str(event))
    assert (code, findings) == (1, ['pull request body:1: home path /home/<name>'])


def test_outside_a_repository_a_stale_names_path_fails_closed_without_the_flag(
    outside_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (outside_repo / 'report.md').write_text('clean\n', encoding='utf-8')
    monkeypatch.setenv(NAMES_ENV, str(outside_repo / 'no-such-names.txt'))
    code, out, _ = check(capsys, '--text', 'report.md')
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail names-file-missing')


def test_history_with_a_stale_names_path_fails_closed_without_the_flag(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', 'clean\n')
    commit(scratch_repo)
    monkeypatch.setenv(NAMES_ENV, str(scratch_repo / 'no-such-names.txt'))
    code, out, _ = check(capsys, '--history')
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail names-file-missing')


def test_history_goes_through_the_names_switch(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'thanks to quillfeather\n')
    commit(scratch_repo)
    stage(scratch_repo, 'a.md', 'clean\n')
    commit(scratch_repo)
    assert check(capsys, '--history')[0] == 1
    (scratch_repo / 'railform.toml').write_text(SWITCH_OFF, encoding='utf-8')
    code, out, findings = check(capsys, '--history')
    assert (code, verdict(out)) == (0, 'railform-verdict: private-refs pass ok')
    assert any('private_refs.names_enabled' in line for line in findings)


def test_history_with_a_range_is_judged_by_the_merge_base_frame(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'railform.toml', BASE_FRAME)
    base = commit(scratch_repo, 'chore: base')
    stage(scratch_repo, 'railform.toml', BASE_FRAME + SWITCH_OFF)
    stage(scratch_repo, 'a.md', 'thanks to quillfeather\n')
    commit(scratch_repo, 'feat: switch off and a term')
    code, out, _ = check(capsys, '--diff', f'{base}...HEAD', '--history')
    assert (code, verdict(out)) == (1, 'railform-verdict: private-refs fail private-name')


# --- the history scope (--history) -------------------------------------------------------------


def short(sha: str) -> str:
    return sha[:12]


def test_history_names_the_commits_that_added_a_term_or_carry_it_in_the_message(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('# the private names\nquillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'notes.md', 'first\nsecond\n')
    commit(scratch_repo, 'docs: notes')
    stage(scratch_repo, 'notes.md', 'first\nsecond\nthanks to quillfeather\n')
    added = commit(scratch_repo, 'docs: thanks')
    stage(scratch_repo, 'notes.md', 'first\nsecond\n')
    removed = commit(scratch_repo, 'docs: drop the thanks')
    stage(scratch_repo, 'other.md', 'fine\n')
    messaged = commit(scratch_repo, 'chore: other\n\nbody\nbody mentions Quillfeather\n')
    # The tip is clean and so is the range of the last commit: only the history sees the term.
    assert check(capsys)[0] == 0
    code, out, findings = check(capsys, '--history')
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail private-name'
    assert findings == [
        f'commit {short(messaged)}:4: private name (names file line 2)',
        f'commit {short(added)} notes.md:3: private name (names file line 2)',
    ]
    assert short(removed) not in out
    assert 'quillfeather' not in out.lower()


def test_history_checks_home_paths_in_lines_that_were_later_removed(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, 'a.md', f'one\n{home("macos")}\nthree\n')
    added = commit(scratch_repo)
    stage(scratch_repo, 'a.md', 'one\nthree\n')
    commit(scratch_repo)
    assert check(capsys)[0] == 0
    code, out, findings = check(capsys, '--history')
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail home-path'
    assert findings == [f'commit {short(added)} a.md:2: home path /Users/<name>']
    assert STRANGER not in out


def test_history_gives_the_line_of_each_hunk_in_the_new_file(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    body = [f'line {n}' for n in range(1, 31)]
    stage(scratch_repo, 'long.txt', '\n'.join(body) + '\n')
    commit(scratch_repo)
    body[1] = 'quillfeather early'
    body.insert(10, 'new line')
    body[25] = '++ quillfeather late'  # an added line that reads like a diff header
    body[29] = '-- not a removal'
    stage(scratch_repo, 'long.txt', '\n'.join(body) + '\n')
    changed = commit(scratch_repo)
    code, out, findings = check(capsys, '--history')
    assert code == 1
    assert verdict(out) == 'railform-verdict: private-refs fail private-name'
    # The tip file is read too: its lines come first, then the lines the commit wrote.
    assert findings == [
        'long.txt:2: private name (names file line 1)',
        'long.txt:26: private name (names file line 1)',
        f'commit {short(changed)} long.txt:2: private name (names file line 1)',
        f'commit {short(changed)} long.txt:26: private name (names file line 1)',
    ]


def test_history_ignores_a_config_that_merges_hunks_with_context_lines(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    git(scratch_repo, 'config', 'diff.interHunkContext', '5')
    stage(scratch_repo, 'f.md', 'one\ntwo\nthree\nfour\nfive\nsix\n')
    commit(scratch_repo)
    stage(scratch_repo, 'f.md', 'one\nTWO\nthree\nfour\nthanks to quillfeather\nsix\n')
    added = commit(scratch_repo)
    stage(scratch_repo, 'f.md', 'one\ntwo\nthree\nfour\nfive\nsix\n')
    commit(scratch_repo)
    code, _, findings = check(capsys, '--history')
    assert (code, findings) == (
        1,
        [f'commit {short(added)} f.md:5: private name (names file line 1)'],
    )


def test_history_sees_the_root_commit_when_the_config_hides_it(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    git(scratch_repo, 'config', 'log.showRoot', 'false')
    stage(scratch_repo, 'f.md', 'thanks to quillfeather\n')
    root = commit(scratch_repo)
    stage(scratch_repo, 'f.md', 'clean\n')
    commit(scratch_repo)
    code, _, findings = check(capsys, '--history')
    assert (code, findings) == (
        1,
        [f'commit {short(root)} f.md:1: private name (names file line 1)'],
    )


def test_history_with_diff_reports_a_range_message_once(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'one\n')
    commit(scratch_repo)
    stage(scratch_repo, 'b.md', 'two\n')
    messaged = commit(scratch_repo, 'chore: b\n\nmentions quillfeather\n')
    code, _, findings = check(capsys, '--history', '--diff', 'HEAD~1...HEAD')
    assert (code, findings) == (
        1,
        [f'commit {short(messaged)}:3: private name (names file line 1)'],
    )


def test_history_keeps_two_identical_findings_on_one_added_line(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    line = f'{home("linux")} and {home("linux")}'
    stage(scratch_repo, 'a.md', f'{line}\n')
    added = commit(scratch_repo)
    stage(scratch_repo, 'a.md', 'clean\n')
    commit(scratch_repo)
    code, _, findings = check(capsys, '--history')
    assert (code, findings) == (1, [f'commit {short(added)} a.md:1: home path /home/<name>'] * 2)


def test_history_covers_every_commit_reachable_from_the_tip_including_merged_branches(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'one\n')
    commit(scratch_repo)
    git(scratch_repo, 'checkout', '-q', '-b', 'side')
    stage(scratch_repo, 'side.md', 'quillfeather\n')
    side = commit(scratch_repo, 'feat: side')
    git(scratch_repo, 'checkout', '-q', '-')
    stage(scratch_repo, 'b.md', 'two\n')
    commit(scratch_repo)
    git(scratch_repo, '-c', 'commit.gpgsign=false', 'merge', '-q', '--no-ff', 'side', '-m', 'merge')
    _, _, findings = check(capsys, '--history')
    assert findings == [
        'side.md:1: private name (names file line 1)',
        f'commit {short(side)} side.md:1: private name (names file line 1)',
    ]


def test_history_skips_binary_files_and_deleted_files(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    (scratch_repo / 'blob.bin').write_bytes(b'\0\1quillfeather')
    git(scratch_repo, 'add', 'blob.bin')
    commit(scratch_repo)
    git(scratch_repo, 'rm', '-q', 'blob.bin')
    commit(scratch_repo)
    assert check(capsys, '--history')[0] == 0


def test_history_of_a_repository_without_commits_passes(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, findings = check(capsys, '--history')
    assert (code, findings) == (0, []), out


def test_history_adds_to_the_tracked_files_and_to_the_other_scopes(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'a.md', 'quillfeather\n')
    old = commit(scratch_repo)
    stage(scratch_repo, 'a.md', 'clean\n')
    commit(scratch_repo)
    stage(scratch_repo, 'staged.md', 'quillfeather\n')  # tracked, not committed
    # Alone it reads the tracked files and the history, so it is never weaker than the default.
    assert check(capsys, '--history')[2] == [
        'staged.md:1: private name (names file line 1)',
        f'commit {short(old)} a.md:1: private name (names file line 1)',
    ]
    # With a path, only that path is read besides the history.
    stage(scratch_repo, 'other.md', 'quillfeather\n')
    _, _, findings = check(capsys, '--history', 'staged.md')
    assert findings == [
        'staged.md:1: private name (names file line 1)',
        f'commit {short(old)} a.md:1: private name (names file line 1)',
    ]


def test_history_is_never_weaker_than_the_default_for_a_file_git_does_not_diff(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, '.gitattributes', 'data.txt -diff\n')
    stage(scratch_repo, 'data.txt', 'thanks to quillfeather\n')
    commit(scratch_repo)
    # git log -p prints "Binary files differ" for the file, so only the tracked files show it.
    code, _, findings = check(capsys)
    assert (code, findings) == (1, ['data.txt:1: private name (names file line 1)'])
    code, out, findings = check(capsys, '--history')
    assert (code, findings) == (1, ['data.txt:1: private name (names file line 1)'])
    assert verdict(out) == 'railform-verdict: private-refs fail private-name'


def test_history_redacts_a_home_path_in_a_file_name(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage(scratch_repo, f'{home("slug")}.md', f'at {home("linux")}\n')
    commit(scratch_repo)
    _, out, findings = check(capsys, '--history')
    assert len(findings) == 2  # the tip file, then the commit that wrote it
    assert all('<name>' in finding.split(':1:')[0] for finding in findings)
    assert findings[1].startswith('commit ')
    assert STRANGER not in out


def test_history_reads_non_ascii_lines_and_paths(
    scratch_repo: Path, names_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names_file.write_text('quillfeather\n', encoding='utf-8')
    stage(scratch_repo, 'café.md', 'éé quillfeather\n')
    sha = commit(scratch_repo)
    _, _, findings = check(capsys, '--history')
    assert findings == [
        'café.md:1: private name (names file line 1)',
        f'commit {short(sha)} café.md:1: private name (names file line 1)',
    ]
