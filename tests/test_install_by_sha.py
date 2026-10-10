"""Pre-mortem advisory R5-2: railform is never installed from a package index.

Every install in a tracked text file names `git+https://github.com/grimaldost/railform@<sha>`.
No file writes a bare `railform` requirement or an `uvx` call without `--from git+...@<sha>`.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GIT_URL = 'git+https://github.com/grimaldost/railform'
TEXT_SUFFIXES = {'.md', '.yml', '.yaml', '.toml', '.py', '.txt', '.sh', '.cfg', '.json'}

# The commands that would fetch railform by name from an index.
INDEX_INSTALL = re.compile(
    r'(?:uvx|uv\s+tool\s+(?:install|run)|pipx\s+(?:install|run)|'
    r'(?:uv\s+)?pip\s+install|uv\s+add)\s+'
    # Flags, each with at most one value token; `--from` is the pinned form and stops the match.
    r'(?:(?!--from\b)-\S+\s+(?:[^-\s]\S*\s+)?)*railform\b'
)
# A railform requirement written as a requirements line: `railform`, `railform>=1`, `railform==1`.
BARE_REQUIREMENT = re.compile(
    r'^\s*[-*]?\s*railform\s*(?:[<>=!~]=?\s*[\w.]+)?\s*$'
    r'|(?:^|dependencies\s*=\s*\[)\s*["\']railform\s*(?:[<>=!~][^"\']*)?["\']\s*(?:[\],]|$)'
)
PINNED_URL = re.compile(re.escape(GIT_URL) + r'@(?:[0-9a-f]{40}|<[^>]+>)(?![0-9A-Za-z])')


def _tracked_text_files() -> list[Path]:
    listed = subprocess.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = [ROOT / name for name in listed.split('\0') if name]
    return [p for p in paths if p.suffix in TEXT_SUFFIXES and p.name != Path(__file__).name]


def violations_in(text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if INDEX_INSTALL.search(line):
            found.append(f'{number}: installs railform by name: {line.strip()}')
        elif BARE_REQUIREMENT.search(line):
            found.append(f'{number}: bare railform requirement: {line.strip()}')
        for match in re.finditer(re.escape(GIT_URL), line):
            if not PINNED_URL.match(line, match.start()):
                found.append(f'{number}: git URL without a commit pin: {line.strip()}')
    return found


@pytest.mark.parametrize(
    'line',
    [
        'uvx railform frame',
        'uvx railform',
        'uvx --python 3.12 railform frame',
        'uv tool install --python 3.12 railform',
        'uvx -q --python=3.12 railform',
        'pip install railform',
        'uv pip install railform==0.1.0',
        'uv add railform',
        'pipx run railform',
        'uv tool install railform',
        '    railform>=0.1',
        '  - railform',
        'dependencies = ["railform"]',
        'uvx --from git+https://github.com/grimaldost/railform railform frame',
        'uvx --from git+https://github.com/grimaldost/railform@main railform frame',
    ],
)
def test_the_scanner_flags_an_unpinned_install(line: str) -> None:
    assert violations_in(line) != []


@pytest.mark.parametrize(
    'line',
    [
        'uvx --from git+https://github.com/grimaldost/railform@' + 'a' * 40 + ' railform frame',
        'pip install git+https://github.com/grimaldost/railform@<40-hex sha>',
        'uvx --python 3.12 --from git+https://github.com/grimaldost/railform@'
        + 'a' * 40
        + ' railform',
        'railform --version',
        'A shared workflow called railform.',
        'The railform repository is public.',
    ],
)
def test_the_scanner_accepts_a_pinned_install_and_plain_prose(line: str) -> None:
    assert violations_in(line) == []


def test_no_tracked_file_installs_railform_without_a_commit_pin() -> None:
    files = _tracked_text_files()
    assert files, 'the scan found no file, so it proves nothing'
    problems = {
        str(path.relative_to(ROOT)).replace('\\', '/'): found
        for path in files
        if (found := violations_in(path.read_text(encoding='utf-8')))
    }
    assert problems == {}
