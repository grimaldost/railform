"""The `railform.toml` of the public adopters stays readable by the frame schema.

`tests/fixtures/adopter_frames/` holds a copy of each public adopter's frame: a header of three
lines, then the adopter's file byte for byte. Line 1 of the header names the repository, the
commit the copy was taken at and the blob id of `<commit>:railform.toml`; those are the only
record of the commit and the blob, and `tests/fixtures/adopter_verdicts.toml` is the only
record of what railform is expected to say about each copy. A hermetic check proves the bytes
from line 4 on hash to the header's blob id, and a network check (marked `slow`, so it fails
offline and `pytest -m "not slow"` stays the offline suite) proves GitHub holds that blob at that
commit. A change to the schema (a renamed key, a stricter type) that would break one of those
repositories at its next pull request shows up here as a verdict that no longer matches its
row: the row is changed to the new, expected failure, and the fixture is never edited to pass.
Refreshing a fixture is editing its file and its verdict row, nothing else. Only public
repositories are copied.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pre_commit.git import no_git_env

from hook_isolation import hook_environment
from railform import frame
from railform.cli import main

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURES = Path(__file__).parent / 'fixtures' / 'adopter_frames'
VERDICTS = Path(__file__).parent / 'fixtures' / 'adopter_verdicts.toml'
ADOPTERS = ('keel', 'convoy', 'fathom', 'mantis-research', 'craft-collection')
HEADER_LINES = 3
LINE_1 = re.compile(
    r'# Copied from github\.com/grimaldost/(?P<repository>[a-z-]+) '
    r'at commit (?P<commit>[0-9a-f]{40}), blob (?P<blob>[0-9a-f]{40}) of railform\.toml'
)
LINE_3_ENDING = 'readable by every verb that reads a frame.'
TIMEOUT = 300
OFFLINE = 'this check needs the network; run `pytest -m "not slow"` offline'
_IDENTITY = ('-c', 'user.name=someone', '-c', 'user.email=someone@example.com')
# Two breakages of the schema step at once, and a frame that the schema step accepts.
UNKNOWN_KEY = 'zebrafrog = 1\n'
UNKNOWN_KEY_AND_WRONG_TYPE = 'zebrafrog = 1\nmeta.parameter_keys = 1\n'
NOTHING_WRONG = '[project]\ntype = "python-package"\n'


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ['git', *_IDENTITY, *args], cwd=root, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _split(data: bytes) -> tuple[list[str], bytes]:
    """The three header lines, and everything from line 4 on, byte for byte."""
    parts = data.split(b'\n', HEADER_LINES)
    assert len(parts) == HEADER_LINES + 1, f'the header is {HEADER_LINES} lines and a body'
    return [part.decode('utf-8') for part in parts[:HEADER_LINES]], parts[HEADER_LINES]


def _header(adopter: str) -> re.Match[str]:
    lines, _ = _split((FIXTURES / f'{adopter}.toml').read_bytes())
    match = LINE_1.fullmatch(lines[0])
    assert match is not None, 'line 1 must name the repository, the commit and the blob'
    return match


def _expected(adopter: str) -> dict[str, Any]:
    return tomllib.loads(VERDICTS.read_text(encoding='utf-8'))[adopter]


def _blob_id(content: bytes) -> str:
    # From standard input, so no attribute or end-of-line conversion touches the bytes.
    done = subprocess.run(
        ['git', 'hash-object', '--stdin'], input=content, capture_output=True, check=True
    )
    return done.stdout.decode('ascii').strip()


def _assert_copy_of_the_blob(data: bytes) -> None:
    """The hermetic check: the bytes from line 4 on hash to the blob id of line 1."""
    lines, body = _split(data)
    match = LINE_1.fullmatch(lines[0])
    assert match is not None, 'line 1 must name the repository, the commit and the blob'
    assert _blob_id(body) == match['blob'], (
        'the bytes below the header are not the blob the header names'
    )


def _assert_blob_at_github(data: bytes, tmp_path: Path, outside: dict[str, str]) -> None:
    """The network check: a shallow fetch of the header's commit from the adopter's repository
    holds `railform.toml` as the header's blob. A fetch that fails fails the check; it never
    skips. It runs in the environment of `tests/test_hook_rail.py`'s probe, which keeps the
    system git configuration that conftest's isolation drops."""
    lines, _ = _split(data)
    match = LINE_1.fullmatch(lines[0])
    assert match is not None, 'line 1 must name the repository, the commit and the blob'
    url = f'https://github.com/grimaldost/{match["repository"]}'
    scratch = tmp_path / 'fetched'
    scratch.mkdir()
    env = no_git_env(hook_environment(outside))
    for command in (['init', '-q'], ['fetch', '-q', '--depth', '1', url, match['commit']]):
        try:
            done = subprocess.run(
                ['git', *command],
                cwd=scratch,
                env=env,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                f'{OFFLINE} (git {command[0]} {url}: no answer in {TIMEOUT} s)', pytrace=False
            )
        if done.returncode != 0:
            pytest.fail(f'{OFFLINE} (git {command[0]} {url}: {done.stderr.strip()})', pytrace=False)
    done = subprocess.run(
        ['git', 'rev-parse', f'{match["commit"]}:railform.toml'],
        cwd=scratch,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=False,
    )
    assert done.returncode == 0, f'{url} holds no railform.toml at {match["commit"]}: {done.stderr}'
    assert done.stdout.strip() == match['blob'], (
        f"{url} holds blob {done.stdout.strip()} at {match['commit']}, not the header's"
    )


def _lay_out_the_named_paths(root: Path, config: dict[str, Any]) -> None:
    """Create in `root` the package and the type-check paths the frame names.

    `frame --check` also asks that these exist in the tree (`missing-path`); the scratch
    repository is empty, so the paths the frame names are made here, the frame itself
    staying as it was copied.
    """
    python = config.get('python', {})
    entries = [python['package']] if python.get('package') else []
    entries += frame.type_check_paths(python.get('type_check', []))
    for entry in entries:
        target = root / entry
        if target.suffix:
            target.touch()
        else:
            target.mkdir(parents=True, exist_ok=True)


def _check_codes(config: dict[str, Any], root: Path) -> list[str]:
    """The verdict codes of the step `frame.check` stops at: the schema step's problems, else
    the exception rows' and the named paths'. `frame.check` itself names only the first."""
    problems = frame.validate(config)
    if not problems:
        problems = frame._exception_problems(config) + frame._path_problems(config, root)
    return sorted({problem.code for problem in problems})


def _merge_base_codes(root: Path, commit: str) -> list[str]:
    """The codes of the schema error behind the `FrameError` that `effective_frame_at` raises
    for the `railform.toml` committed at `commit`; none when it reads."""
    try:
        frame.effective_frame_at(root, commit)
    except frame.FrameError as error:
        cause = error.__cause__
        assert isinstance(cause, frame.FrameSchemaError), f'not a schema error: {error}'
        return sorted({problem.code for problem in cause.problems})
    return []


def _wanted(expected: str | Sequence[str]) -> list[str]:
    """An expected verdict as the sorted codes it names: `pass` is none."""
    return [] if expected == 'pass' else sorted(expected)


def _judge_frame_check(
    text: str, expected: str | Sequence[str], root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`frame --check` on `text` in the scratch repository `root`, against the expectation."""
    config = tomllib.loads(text)
    (root / 'railform.toml').write_text(text, encoding='utf-8', newline='\n')
    _lay_out_the_named_paths(root, config)
    codes = _check_codes(config, root)
    assert codes == _wanted(expected), f'frame --check: expected {expected}, found {codes}'
    code = main(['frame', '--check'])
    captured = capsys.readouterr()
    last = captured.out.splitlines()[-1]
    if codes:
        first = min(codes, key=frame.CODE_ORDER.index)
        assert (code, last) == (1, f'railform-verdict: frame fail {first}'), captured.out
    else:
        assert (code, last) == (0, 'railform-verdict: frame pass ok'), captured.out


def _judge_merge_base(text: str, expected: str | Sequence[str], root: Path) -> str:
    """The merge-base reading of `text`, committed in the scratch repository `root`, against the
    expectation; returns the commit."""
    (root / 'railform.toml').write_text(text, encoding='utf-8', newline='\n')
    _git(root, 'add', '-A')
    _git(root, 'commit', '-q', '--allow-empty', '-m', 'chore: the base')
    base = _git(root, 'rev-parse', 'HEAD')
    codes = _merge_base_codes(root, base)
    assert codes == _wanted(expected), f'merge-base reading: expected {expected}, found {codes}'
    return base


def _verdict(capsys: pytest.CaptureFixture[str]) -> tuple[str, str, str]:
    captured = capsys.readouterr()
    return captured.out, captured.err, captured.out.splitlines()[-1]


def _fixture_text(adopter: str) -> str:
    _, body = _split((FIXTURES / f'{adopter}.toml').read_bytes())
    return body.decode('utf-8')


def test_the_fixtures_are_the_five_public_adopters_and_nothing_else() -> None:
    assert sorted(path.stem for path in FIXTURES.glob('*.toml')) == sorted(ADOPTERS)


@pytest.mark.parametrize('adopter', ADOPTERS)
def test_each_fixture_has_a_three_line_header_that_names_its_repository(adopter: str) -> None:
    lines, body = _split((FIXTURES / f'{adopter}.toml').read_bytes())
    match = _header(adopter)
    assert match['repository'] == adopter
    assert len(re.findall(r'[0-9a-f]{40}', lines[0])) == 2, 'only the commit and the blob'
    assert all(line.startswith('# ') for line in lines)
    assert lines[2].endswith(LINE_3_ENDING), lines[2]
    assert b'\r' not in body, "the blob is the adopter's, and .gitattributes would turn CRLF to LF"


@pytest.mark.parametrize('adopter', ADOPTERS)
def test_each_fixture_is_a_byte_copy_of_the_blob_its_header_names(adopter: str) -> None:
    _assert_copy_of_the_blob((FIXTURES / f'{adopter}.toml').read_bytes())


def test_a_line_edited_below_the_header_fails_the_hermetic_check() -> None:
    data = (FIXTURES / 'keel.toml').read_bytes()
    _assert_copy_of_the_blob(data)
    lines, body = _split(data)
    first, _, rest = body.partition(b'\n')
    # Line 4 is the adopter's own first line, a comment among them.
    edited = '\n'.join(lines).encode('utf-8') + b'\n' + first + b' (edited)\n' + rest
    with pytest.raises(AssertionError, match='not the blob the header names'):
        _assert_copy_of_the_blob(edited)
    # Nor does a trailing byte, a removed last line or a header that grew by one line pass.
    for changed in (
        data + b'\n',
        data[: data.rstrip(b'\n').rfind(b'\n') + 1],
        data.replace(b'\n', b'\n# a fourth header line\n', 3),
    ):
        with pytest.raises(AssertionError, match='not the blob the header names'):
            _assert_copy_of_the_blob(changed)


@pytest.mark.slow
@pytest.mark.parametrize('adopter', ADOPTERS)
def test_github_holds_the_blob_each_header_names_at_the_commit_it_names(
    adopter: str, tmp_path: Path, outside: dict[str, str]
) -> None:
    _assert_blob_at_github((FIXTURES / f'{adopter}.toml').read_bytes(), tmp_path, outside)


@pytest.mark.slow
def test_a_header_blob_id_that_differs_from_githubs_fails_the_network_check(
    tmp_path: Path, outside: dict[str, str]
) -> None:
    data = (FIXTURES / 'keel.toml').read_bytes()
    lines, _ = _split(data)
    blob = _header('keel')['blob']
    other = ('0' if blob[0] != '0' else '1') + blob[1:]
    forged = data.replace(blob.encode('ascii'), other.encode('ascii'), 1)
    assert forged != data and _split(forged)[0][1:] == lines[1:]
    with pytest.raises(AssertionError, match="not the header's"):
        _assert_blob_at_github(forged, tmp_path, outside)


def test_the_verdict_file_has_a_row_of_two_keys_for_each_adopter() -> None:
    rows = tomllib.loads(VERDICTS.read_text(encoding='utf-8'))
    assert sorted(rows) == sorted(ADOPTERS)
    known = {*frame.CODE_ORDER}
    for adopter, row in rows.items():
        assert sorted(row) == ['frame_check', 'merge_base'], adopter
        for key, value in row.items():
            if value != 'pass':
                assert isinstance(value, list) and value, (adopter, key, value)
                assert set(value) <= known and len(set(value)) == len(value), (adopter, key)


@pytest.mark.parametrize('adopter', ADOPTERS)
def test_frame_check_judges_the_frame_of_each_adopter_as_expected(
    adopter: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _judge_frame_check(
        _fixture_text(adopter), _expected(adopter)['frame_check'], scratch_repo, capsys
    )


@pytest.mark.parametrize('adopter', ADOPTERS)
def test_the_frame_of_each_adopter_reads_at_the_merge_base_as_expected(
    adopter: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `changelog-gate` and `version-sites --base` judge a pull request by the railform.toml
    # committed at the merge base; a frame the schema rejects there is a usage error (exit 2)
    # that stops every pull request of the adopter.
    expected = _expected(adopter)['merge_base']
    base = _judge_merge_base(_fixture_text(adopter), expected, scratch_repo)
    if expected != 'pass':
        return
    (scratch_repo / 'notes.txt').write_text('a change\n', encoding='utf-8')
    _git(scratch_repo, 'add', '-A')
    _git(scratch_repo, 'commit', '-q', '-m', 'docs: a change')

    code = main(['changelog-gate', f'{base}...HEAD'])
    out, err, _ = _verdict(capsys)
    assert code != 2, out + err
    assert f'railform.toml at the merge base {base[:12]}' in out, out

    code = main(['version-sites', '--base', base])
    out, err, _ = _verdict(capsys)
    assert code != 2, out + err
    assert f'version.sites: read from railform.toml at the merge base {base[:12]}' in out, out


def test_a_frame_that_breaks_the_schema_is_an_expected_failure_when_its_row_says_so(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _judge_frame_check(UNKNOWN_KEY, ['unknown-key'], scratch_repo, capsys)
    _judge_merge_base(UNKNOWN_KEY, ['unknown-key'], scratch_repo)
    _judge_frame_check(NOTHING_WRONG, 'pass', scratch_repo, capsys)
    _judge_merge_base(NOTHING_WRONG, 'pass', scratch_repo)


def test_a_fixture_expected_to_fail_that_passes_fails_the_test(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(AssertionError, match='frame --check: expected'):
        _judge_frame_check(NOTHING_WRONG, ['unknown-key'], scratch_repo, capsys)
    with pytest.raises(AssertionError, match='merge-base reading: expected'):
        _judge_merge_base(NOTHING_WRONG, ['unknown-key'], scratch_repo)


def test_a_fixture_expected_to_pass_that_fails_fails_the_test(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(AssertionError, match='frame --check: expected'):
        _judge_frame_check(UNKNOWN_KEY, 'pass', scratch_repo, capsys)
    with pytest.raises(AssertionError, match='merge-base reading: expected'):
        _judge_merge_base(UNKNOWN_KEY, 'pass', scratch_repo)


def test_a_second_code_of_the_same_step_changes_the_set_and_fails_the_test(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    both = ['unknown-key', 'wrong-type']
    with pytest.raises(AssertionError, match='frame --check: expected'):
        _judge_frame_check(UNKNOWN_KEY_AND_WRONG_TYPE, ['unknown-key'], scratch_repo, capsys)
    with pytest.raises(AssertionError, match='merge-base reading: expected'):
        _judge_merge_base(UNKNOWN_KEY_AND_WRONG_TYPE, ['unknown-key'], scratch_repo)
    # The same frame passes the test when its row lists both codes, in either order.
    _judge_frame_check(UNKNOWN_KEY_AND_WRONG_TYPE, both, scratch_repo, capsys)
    _judge_frame_check(UNKNOWN_KEY_AND_WRONG_TYPE, both[::-1], scratch_repo, capsys)
    _judge_merge_base(UNKNOWN_KEY_AND_WRONG_TYPE, both, scratch_repo)
