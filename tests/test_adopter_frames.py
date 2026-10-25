"""The `railform.toml` of the public adopters stays readable by the frame schema.

`tests/fixtures/adopter_frames/` holds a copy of each public adopter's frame, taken at the commit
its header names. A change to the schema (a renamed key, a stricter type) that would break one of
those repositories at its next pull request fails here first. The copies are refreshed by hand
when an adopter's frame changes; only public repositories are copied.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from railform import frame
from railform.cli import main

FIXTURES = Path(__file__).parent / 'fixtures' / 'adopter_frames'
# Each adopter's `origin/main` on 2026-10-25, the commit its fixture was copied at.
COMMITS = {
    'keel': 'bf7c1e1',
    'convoy': '8de89f8',
    'fathom': 'df07ba0',
    'mantis-research': 'da71b8c',
    'craft-collection': 'f1c47e3',
}
ADOPTERS = tuple(COMMITS)
HEADER = re.compile(
    r'# Copied from github\.com/grimaldost/(?P<repository>[a-z-]+) '
    r'at commit (?P<commit>[0-9a-f]{7,40})'
)
_IDENTITY = ('-c', 'user.name=someone', '-c', 'user.email=someone@example.com')


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ['git', *_IDENTITY, *args], cwd=root, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def _lay_out_the_named_paths(root: Path, adopter: str) -> None:
    """Create in `root` the package and the type-check paths the adopter's frame names.

    `frame --check` also asks that these exist in the tree (`missing-path`); the scratch
    repository is empty, so the paths the fixture names are made here, the fixture itself
    staying as it was copied.
    """
    python = tomllib.loads((FIXTURES / f'{adopter}.toml').read_text(encoding='utf-8')).get(
        'python', {}
    )
    entries = [python['package']] if python.get('package') else []
    entries += frame.type_check_paths(python.get('type_check', []))
    for entry in entries:
        target = root / entry
        if target.suffix:
            target.touch()
        else:
            target.mkdir(parents=True, exist_ok=True)


def _verdict(capsys: pytest.CaptureFixture[str]) -> tuple[str, str, str]:
    captured = capsys.readouterr()
    return captured.out, captured.err, captured.out.splitlines()[-1]


def test_the_fixtures_are_the_five_public_adopters_and_nothing_else() -> None:
    assert sorted(path.stem for path in FIXTURES.glob('*.toml')) == sorted(ADOPTERS)


@pytest.mark.parametrize('adopter', ADOPTERS)
def test_each_fixture_names_its_repository_and_commit(adopter: str) -> None:
    text = (FIXTURES / f'{adopter}.toml').read_text(encoding='utf-8')
    match = HEADER.match(text)
    assert match is not None, 'the first line must name the repository and the commit'
    assert match['repository'] == adopter
    assert match['commit'] == COMMITS[adopter]


@pytest.mark.parametrize('adopter', ADOPTERS)
def test_frame_check_passes_the_frame_of_each_adopter(
    adopter: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copyfile(FIXTURES / f'{adopter}.toml', scratch_repo / 'railform.toml')
    _lay_out_the_named_paths(scratch_repo, adopter)
    code = main(['frame', '--check'])
    out, err, last = _verdict(capsys)
    assert (code, last) == (0, 'railform-verdict: frame pass ok'), out + err


@pytest.mark.parametrize('adopter', ADOPTERS)
def test_the_frame_of_each_adopter_reads_as_a_merge_base_frame(
    adopter: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `changelog-gate` and `version-sites --base` judge a pull request by the railform.toml
    # committed at the merge base; a frame the schema rejects there is a usage error (exit 2)
    # that stops every pull request of the adopter.
    shutil.copyfile(FIXTURES / f'{adopter}.toml', scratch_repo / 'railform.toml')
    _git(scratch_repo, 'add', '-A')
    _git(scratch_repo, 'commit', '-q', '-m', 'chore: the base')
    base = _git(scratch_repo, 'rev-parse', 'HEAD')
    (scratch_repo / 'notes.txt').write_text('a change\n', encoding='utf-8')
    _git(scratch_repo, 'add', '-A')
    _git(scratch_repo, 'commit', '-q', '-m', 'docs: a change')

    assert frame.effective_frame_at(scratch_repo, base) is not None

    code = main(['changelog-gate', f'{base}...HEAD'])
    out, err, _ = _verdict(capsys)
    assert code != 2, out + err
    assert f'railform.toml at the merge base {base[:12]}' in out, out

    code = main(['version-sites', '--base', base])
    out, err, _ = _verdict(capsys)
    assert code != 2, out + err
    assert f'version.sites: read from railform.toml at the merge base {base[:12]}' in out, out
