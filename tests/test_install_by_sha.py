"""Pre-mortem advisory R5-2: railform is never installed from a package index.

Every install in a tracked text file names `git+https://github.com/grimaldost/railform@<sha>`.
No file writes a bare `railform` requirement or an `uvx` call without `--from git+...@<sha>`.

The reusable workflows are the files that name the commit by a variable (with the local model
of their runs, which replaces that install line with the code under test): they install railform
at `$RAILFORM_SHA`, the required input `railform-sha` passed through `env:`, which a step checks
to be 40 hexadecimal characters before anything is installed (development-rails spec, §9 and its
round-6 amendment; §16 for the tag watch).
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
# The reusable workflow's install, at the commit its caller passes as `railform-sha`.
VARIABLE_URL = re.compile(re.escape(GIT_URL) + r'@\$RAILFORM_SHA(?![0-9A-Za-z_])')
REUSABLE_WORKFLOW = '.github/workflows/python-ci.yml'
TAG_WATCH_WORKFLOW = '.github/workflows/tag-watch.yml'
# The files that may name it: the two reusable workflows, and the local model of a run, which
# replaces that exact install line with the code under test.
VARIABLE_PIN_FILES = (REUSABLE_WORKFLOW, TAG_WATCH_WORKFLOW, 'tests/workflow_model.py')


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


def violations_in(text: str, *, variable_pin: bool = False) -> list[str]:
    """The unpinned installs in `text`; with `variable_pin`, `@$RAILFORM_SHA` is a pin."""
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        if INDEX_INSTALL.search(line):
            found.append(f'{number}: installs railform by name: {line.strip()}')
        elif BARE_REQUIREMENT.search(line):
            found.append(f'{number}: bare railform requirement: {line.strip()}')
        for match in re.finditer(re.escape(GIT_URL), line):
            if PINNED_URL.match(line, match.start()):
                continue
            if variable_pin and VARIABLE_URL.match(line, match.start()):
                continue
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
    problems: dict[str, list[str]] = {}
    for path in files:
        name = str(path.relative_to(ROOT)).replace('\\', '/')
        text = path.read_text(encoding='utf-8')
        found = violations_in(text, variable_pin=name in VARIABLE_PIN_FILES)
        if found:
            problems[name] = found
    assert problems == {}


def test_the_variable_pin_is_accepted_only_where_it_is_allowed() -> None:
    line = 'run: uvx --from "git+https://github.com/grimaldost/railform@$RAILFORM_SHA" railform x'
    assert violations_in(line, variable_pin=True) == []
    assert violations_in(line) != []
    # Another variable, the same name run on, or a branch is no pin.
    for other in ('$OTHER_SHA', '$RAILFORM_SHA_X', '${RAILFORM_SHA}', 'main'):
        changed = line.replace('$RAILFORM_SHA', other)
        assert violations_in(changed, variable_pin=True) != [], other


@pytest.mark.parametrize('workflow', [REUSABLE_WORKFLOW, TAG_WATCH_WORKFLOW])
def test_the_reusable_workflow_installs_railform_at_the_variable_pin(workflow: str) -> None:
    text = (ROOT / workflow).read_text(encoding='utf-8')
    assert VARIABLE_URL.search(text)
    assert violations_in(text, variable_pin=True) == []
