"""Migration fixtures for `railform adopt` (development-rails spec, §7), built at test time.

One small populated repository per project type, each with a module, a test, and its own extras
that adopt must leave intact: an extra workflow trigger and an extra job in `ci.yml`, an extra
pre-commit hook and an extra Dependabot ecosystem. The three fixtures write their files in
different but common YAML styles (an indented and an indentless `repos:` list, a github-actions
update with and without an `ignore:` list, and none at all), so the edits adopt makes are
exercised on each shape.

`Fixture.expected` holds, per owned file, the text adopt must leave once its owned region is
taken out: the original bytes plus the edits adopt makes outside the region (the
`pull_request` types, and a github-actions update block where there was none).
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = '0123456789abcdef0123456789abcdef01234567'
OTHER_PIN = 'fedcba9876543210fedcba9876543210fedcba98'
TYPES = ('python-package', 'python-scripts', 'plugin-marketplace')
CI = '.github/workflows/ci.yml'
PRE_COMMIT = '.pre-commit-config.yaml'
DEPENDABOT = '.github/dependabot.yml'
OWNED_FILES = (CI, PRE_COMMIT, DEPENDABOT)
PR_TYPES_LINE = 'types: [opened, synchronize, reopened, edited]'


def locked_version(name: str) -> str:
    """The version railform's own lock pins for `name`, so a fixture resolves from the cache."""
    lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))
    return next(package['version'] for package in lock['package'] if package['name'] == name)


def _dev_group() -> str:
    tools = ', '.join(f'"{name}=={locked_version(name)}"' for name in ('pytest', 'ruff', 'ty'))
    return f'[dependency-groups]\ndev = [{tools}, "pre-commit==4.6.2"]\n'


@dataclass
class Fixture:
    """A built fixture: its root, its type, and what must survive adopt."""

    root: Path
    project_type: str
    original: dict[str, str] = field(default_factory=dict)
    expected: dict[str, str] = field(default_factory=dict)
    extras: dict[str, list[str]] = field(default_factory=dict)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout


def write(root: Path, path: str, text: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode('utf-8'))


def outside_region(text: str) -> str:
    """`text` without the lines between the railform markers, the markers included."""
    kept: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith('# >>> railform'):
            inside = True
            continue
        if stripped.startswith('# <<< railform'):
            inside = False
            continue
        if not inside:
            kept.append(line)
    return ''.join(kept)


def region(text: str) -> str:
    """The lines between the railform markers, the markers included."""
    lines = text.splitlines(keepends=True)
    begin = next(i for i, line in enumerate(lines) if line.strip().startswith('# >>> railform'))
    end = next(i for i, line in enumerate(lines) if line.strip().startswith('# <<< railform'))
    return ''.join(lines[begin : end + 1])


# --- python-package: src layout -----------------------------------------------------------------

_PACKAGE_CI = """\
name: ci
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - run: echo "the documentation builds"
"""
_PACKAGE_PRE_COMMIT = """\
repos:
  - repo: local
    hooks:
      - id: no-todo
        name: no TODO markers
        entry: "TODO"
        language: pygrep
        types: [python]
"""
_PACKAGE_DEPENDABOT = """\
version: 2
updates:
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: uv
    directory: "/"
    schedule:
      interval: weekly
"""


def _python_package(root: Path) -> Fixture:
    pyproject = (
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n\n'
        + _dev_group()
        + '\n[build-system]\nrequires = ["uv_build>=0.12.19,<0.13"]\nbuild-backend = "uv_build"\n'
        + '\n[tool.ruff]\nline-length = 100\n'
    )
    write(root, 'pyproject.toml', pyproject)
    write(
        root,
        'src/demo/__init__.py',
        '"""A demo package."""\n\n\ndef greet(name: str) -> str:\n    return f"hello, {name}"\n',
    )
    write(
        root,
        'tests/test_demo.py',
        'from demo import greet\n\n\ndef test_greet() -> None:\n'
        '    assert greet("x") == "hello, x"\n',
    )
    fixture = Fixture(root, 'python-package')
    fixture.original = {
        CI: _PACKAGE_CI,
        PRE_COMMIT: _PACKAGE_PRE_COMMIT,
        DEPENDABOT: _PACKAGE_DEPENDABOT,
    }
    fixture.expected = {
        CI: _PACKAGE_CI.replace('  pull_request:\n', f'  pull_request:\n    {PR_TYPES_LINE}\n'),
        PRE_COMMIT: _PACKAGE_PRE_COMMIT,
        DEPENDABOT: _PACKAGE_DEPENDABOT,
    }
    fixture.extras = {
        CI: [
            '  workflow_dispatch:\n',
            '  docs:\n    runs-on: ubuntu-latest\n    steps:\n'
            '      - run: echo "the documentation builds"\n',
        ],
        PRE_COMMIT: [_PACKAGE_PRE_COMMIT.removeprefix('repos:\n')],
        DEPENDABOT: [
            '  - package-ecosystem: uv\n    directory: "/"\n    schedule:\n      interval: weekly\n'
        ],
    }
    return fixture


# --- python-scripts: a project with no package ---------------------------------------------------

_SCRIPTS_CI = """\
name: ci
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    - cron: "0 6 * * 1"
permissions:
  contents: read
jobs:
  links:
    runs-on: ubuntu-latest
    steps:
      - run: echo "the links resolve"
"""
_SCRIPTS_PRE_COMMIT = """\
repos:
- repo: local
  hooks:
  - id: no-print
    name: no print calls
    entry: 'print\\('
    language: pygrep
    types: [python]
"""
_SCRIPTS_DEPENDABOT = """\
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: monthly
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
    ignore:
      - dependency-name: actions/checkout
        versions: ["5.x"]
"""
_OLD_HOOK = '#!/bin/sh\nexec pre-commit hook-impl --hook-type=pre-commit -- "$@"\n'


def _python_scripts(root: Path) -> Fixture:
    pyproject = (
        '[project]\nname = "scripts"\nversion = "0"\nrequires-python = ">=3.11"\n\n'
        + _dev_group()
        + '\n[tool.ruff]\nline-length = 100\n'
    )
    write(root, 'pyproject.toml', pyproject)
    write(
        root,
        'report.py',
        '"""A demo script."""\n\n\ndef total(values: list[int]) -> int:\n    return sum(values)\n',
    )
    write(
        root,
        'tests/test_report.py',
        'from report import total\n\n\ndef test_total() -> None:\n    assert total([1, 2]) == 3\n',
    )
    # The hooks lived in another tracked directory before adoption.
    write(root, '.hooks/pre-commit', _OLD_HOOK)
    git(root, 'add', '--', '.hooks/pre-commit')
    git(root, 'update-index', '--chmod=+x', '--', '.hooks/pre-commit')
    fixture = Fixture(root, 'python-scripts')
    fixture.original = {
        CI: _SCRIPTS_CI,
        PRE_COMMIT: _SCRIPTS_PRE_COMMIT,
        DEPENDABOT: _SCRIPTS_DEPENDABOT,
    }
    fixture.expected = {
        CI: _SCRIPTS_CI.replace(
            '  pull_request:\n    branches: [main]\n',
            f'  pull_request:\n    {PR_TYPES_LINE}\n    branches: [main]\n',
        ),
        PRE_COMMIT: _SCRIPTS_PRE_COMMIT,
        DEPENDABOT: _SCRIPTS_DEPENDABOT,
    }
    fixture.extras = {
        CI: [
            '  schedule:\n    - cron: "0 6 * * 1"\n',
            '  links:\n    runs-on: ubuntu-latest\n    steps:\n'
            '      - run: echo "the links resolve"\n',
        ],
        PRE_COMMIT: [_SCRIPTS_PRE_COMMIT.removeprefix('repos:\n')],
        DEPENDABOT: [
            '  - package-ecosystem: pip\n    directory: "/"\n'
            '    schedule:\n      interval: monthly\n',
            '      - dependency-name: actions/checkout\n        versions: ["5.x"]\n',
        ],
    }
    return fixture


# --- plugin-marketplace: no uv project -----------------------------------------------------------

_MARKETPLACE_CI = """\
name: ci
on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize]
  release:
    types: [published]
permissions:
  contents: read
jobs:
  manifests:
    runs-on: ubuntu-latest
    steps:
      - run: echo "the manifests parse"
"""
_MARKETPLACE_PRE_COMMIT = """\
repos:
  - repo: local
    hooks:
      - id: plugin-json
        name: plugin manifests are JSON
        entry: python -m json.tool
        language: system
        files: plugin\\.json$
"""
_MARKETPLACE_DEPENDABOT = """\
version: 2
updates:
  - package-ecosystem: npm
    directory: "/"
    schedule:
      interval: weekly
"""
# What adopt adds to a Dependabot configuration that has no github-actions update.
ADDED_ACTIONS_UPDATE = """\
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
    commit-message:
      prefix: ci
"""


def _plugin_marketplace(root: Path) -> Fixture:
    write(root, 'ruff.toml', 'line-length = 100\n')
    write(root, '.claude-plugin/marketplace.json', '{"name": "demo", "plugins": []}\n')
    write(
        root,
        'scripts/names.py',
        '"""A demo helper."""\n\n\ndef slug(name: str) -> str:\n'
        '    return name.strip().lower().replace(" ", "-")\n',
    )
    write(
        root,
        'tests/test_names.py',
        'from scripts.names import slug\n\n\ndef test_slug() -> None:\n'
        '    assert slug(" Demo Plugin ") == "demo-plugin"\n',
    )
    fixture = Fixture(root, 'plugin-marketplace')
    fixture.original = {
        CI: _MARKETPLACE_CI,
        PRE_COMMIT: _MARKETPLACE_PRE_COMMIT,
        DEPENDABOT: _MARKETPLACE_DEPENDABOT,
    }
    fixture.expected = {
        CI: _MARKETPLACE_CI.replace('    types: [opened, synchronize]\n', f'    {PR_TYPES_LINE}\n'),
        PRE_COMMIT: _MARKETPLACE_PRE_COMMIT,
        DEPENDABOT: _MARKETPLACE_DEPENDABOT + ADDED_ACTIONS_UPDATE,
    }
    fixture.extras = {
        CI: [
            '  release:\n    types: [published]\n',
            '  manifests:\n    runs-on: ubuntu-latest\n    steps:\n'
            '      - run: echo "the manifests parse"\n',
        ],
        PRE_COMMIT: [_MARKETPLACE_PRE_COMMIT.removeprefix('repos:\n')],
        DEPENDABOT: [
            '  - package-ecosystem: npm\n    directory: "/"\n'
            '    schedule:\n      interval: weekly\n'
        ],
    }
    return fixture


_BUILDERS = {
    'python-package': _python_package,
    'python-scripts': _python_scripts,
    'plugin-marketplace': _plugin_marketplace,
}


def build(root: Path, project_type: str) -> Fixture:
    """Populate the git repository at `root` as the migration fixture of `project_type`."""
    fixture = _BUILDERS[project_type](root)
    for path, text in fixture.original.items():
        write(root, path, text)
    return fixture
