"""The version-site registry (development-rails spec, §15; Wave C contract, sections 2 and 4).

Ported from keel (https://github.com/grimaldost/keel) at c3313fd: the tests of
`tests/test_plugin_manifest.py` that hold its `VERSION_SITES` registry (versions agree across all
sites, the disagreement is named whole in one report, each pattern reads the field its parser
would) and the bump tests of `tests/test_release_flow.py` (the bump writes every writable site,
leaves the changelog heading to its author, is idempotent, refuses anything that is not a
version). keel's registry is a constant of its test module; here it is `version.sites` of the
frame, so the tests write the same eight sites into a fixture `railform.toml`.

The red proofs of §15 are the tests named `test_red_proof_*`:

- `test_red_proof_a_site_behind_fails_and_bump_makes_it_pass`: a fixture with one site behind
  fails, and `--bump` then makes it pass;
- `test_red_proof_a_bump_leaves_the_authored_heading_untouched`: a bump over a fixture whose
  newest `## [X.Y.Z]` heading is authored leaves that heading untouched;
- `test_red_proof_a_site_behind_turns_gate_red_through_the_caller`: the same defect, through a
  fixture caller that runs the reusable workflow.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import TYPE_CHECKING, Any

import pytest

from migration_fixtures import CI, PIN, build
from railform import version_sites
from railform.cli import main
from workflow_model import Runner, bash, github_context

if TYPE_CHECKING:
    from pathlib import Path

VERDICT = re.compile(
    r'railform-verdict: version-sites (?P<status>pass|fail) (?P<code>[a-z0-9-]+)\Z'
)

# The sites of the contract's section 4.6, unless a case says otherwise.
PYPROJECT = ('pyproject.toml', '(?m)^version = "([^"]+)"', False)
INIT = ('src/pkg/__init__.py', "__version__ = '([^']+)'", False)
HEADING = ('CHANGELOG.md', r'(?m)^##\s*\[(\d+\.\d+\.\d+)\]', True)
STANDARD = (PYPROJECT, INIT, HEADING)


def _toml(value: object) -> str:
    return json.dumps(value)


def _frame(
    sites: tuple[tuple[str, str, bool], ...],
    prefixes: tuple[tuple[str, str], ...] = (),
    project_type: str = 'python-scripts',
) -> str:
    """A `railform.toml` declaring `sites`, and `prefixes` as the release.tag_prefix rows."""
    parts = [f'[project]\ntype = "{project_type}"\n']
    for path, pattern, authored in sites:
        parts.append(
            f'\n[[version.sites]]\npath = {_toml(path)}\npattern = {_toml(pattern)}\n'
            f'authored = {_toml(authored)}\n'
        )
    for prefix, tag in prefixes:
        parts.append(f'\n[[release.tag_prefix]]\nprefix = {_toml(prefix)}\ntag = {_toml(tag)}\n')
    return ''.join(parts)


def _write(root: Path, path: str, text: str | bytes) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text if isinstance(text, bytes) else text.encode('utf-8'))


def _configure(root: Path, *args: Any, **kwargs: Any) -> None:
    _write(root, 'railform.toml', _frame(*args, **kwargs))


def _standard_tree(root: Path, pyproject: str, init: str, heading: str) -> None:
    _write(root, 'pyproject.toml', f'[project]\nname = "pkg"\nversion = "{pyproject}"\n')
    _write(root, 'src/pkg/__init__.py', f"__version__ = '{init}'\n")
    _write(root, 'CHANGELOG.md', f'# Changelog\n\n## [{heading}] - 2026-10-11\n\n- a change\n')
    _configure(root, STANDARD)


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, list[str], str]:
    """(exit code, status+code, the finding lines, standard error) of `railform version-sites`."""
    code = main(['version-sites', *args])
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    match = VERDICT.fullmatch(lines[-1])
    assert match is not None, captured.out
    assert [line for line in lines if line.startswith('railform-verdict:')] == [lines[-1]]
    return code, f'{match["status"]} {match["code"]}', lines[:-1], captured.err


# --- the red proofs of §15 -----------------------------------------------------------------------


def test_red_proof_a_site_behind_fails_and_bump_makes_it_pass(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _standard_tree(scratch_repo, '0.2.0', '0.1.0', '0.2.0')
    # CRLF in the site that is behind: the bump rewrites the version and nothing else.
    behind = b"\xef\xbb\xbf# a module\r\n__version__ = '0.1.0'\r\n\r\nNAME = 'pkg'\r\n"
    _write(scratch_repo, 'src/pkg/__init__.py', behind)
    code, verdict, findings, _ = _run(capsys)
    assert (code, verdict) == (1, 'fail mismatch')
    assert len(findings) == 1
    assert findings[0].startswith('src/pkg/__init__.py: ')
    assert '0.1.0' in findings[0]
    assert '0.2.0' in findings[0]
    assert (scratch_repo / 'src/pkg/__init__.py').read_bytes() == behind  # checking writes nothing

    code, verdict, findings, _ = _run(capsys, '--bump', '0.2.0')
    assert (code, verdict) == (0, 'pass ok')
    assert findings == ['src/pkg/__init__.py: 0.1.0 -> 0.2.0']
    assert (scratch_repo / 'src/pkg/__init__.py').read_bytes() == behind.replace(b'0.1.0', b'0.2.0')
    assert _run(capsys)[:2] == (0, 'pass ok')


def test_red_proof_a_bump_leaves_the_authored_heading_untouched(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _standard_tree(scratch_repo, '0.1.0', '0.1.0', '0.1.0')
    changelog = (scratch_repo / 'CHANGELOG.md').read_bytes()
    code, verdict, findings, _ = _run(capsys, '--bump', '0.2.0')
    assert (code, verdict) == (1, 'fail mismatch')
    assert (scratch_repo / 'CHANGELOG.md').read_bytes() == changelog
    assert 'version = "0.2.0"' in (scratch_repo / 'pyproject.toml').read_text(encoding='utf-8')
    assert "'0.2.0'" in (scratch_repo / 'src/pkg/__init__.py').read_text(encoding='utf-8')
    assert findings[:2] == ['pyproject.toml: 0.1.0 -> 0.2.0', 'src/pkg/__init__.py: 0.1.0 -> 0.2.0']
    heading = [line for line in findings if line.startswith('CHANGELOG.md: ')]
    assert heading
    assert all('write it by hand' in line for line in heading)
    # The author writes the heading; the next bump has nothing to write, and passes.
    _write(scratch_repo, 'CHANGELOG.md', b'## [0.2.0] - 2026-10-11\n\n## [0.1.0] - 2026-09-01\n')
    assert _run(capsys, '--bump', '0.2.0')[:3] == (0, 'pass ok', [])


# --- keel's registry, ported ---------------------------------------------------------------------

_V = r'([0-9]+\.[0-9]+\.[0-9]+)'
# keel's eight sites at c3313fd, as the rows `version.sites` holds.
KEEL_SITES = (
    ('.claude-plugin/plugin.json', rf'"version":\s*"{_V}"', False),
    ('pyproject.toml', rf"(?m)^version = ['\"]{_V}['\"]", False),
    ('src/keel/__init__.py', rf"__version__ = '{_V}'", False),
    ('CHANGELOG.md', rf'(?m)^##\s*\[{_V}\]', True),
    ('agents/pre-mortem-review.md', rf'bundled `pre-mortem-review` agent from keel {_V}', False),
    ('src/keel/templates/spec-template.md', rf'(?m)^-\s*\*\*Kit:\*\*\s*{_V}\s*$', False),
    ('src/keel/templates/core/spec-template.md', rf'(?m)^-\s*\*\*Kit:\*\*\s*{_V}\s*$', False),
    ('skills/apply-method/SKILL.md', rf'ships with keel {_V}', False),
)


def _keel_tree(root: Path, version: str = '0.19.0') -> None:
    _write(
        root, '.claude-plugin/plugin.json', f'{{\n  "name": "keel",\n  "version": "{version}"\n}}\n'
    )
    _write(root, 'pyproject.toml', f"[project]\nname = 'keel'\nversion = '{version}'\n")
    _write(root, 'src/keel/__init__.py', f"__version__ = '{version}'\n")
    _write(
        root,
        'CHANGELOG.md',
        f'# Changelog\n\n## [{version}] - 2026-10-01\n\n## [0.18.1] - 2026-09-01\n',
    )
    _write(
        root,
        'agents/pre-mortem-review.md',
        f'bundled `pre-mortem-review` agent from keel {version}.\n',
    )
    stamp = f'- **Kit:** {version}\n'
    _write(root, 'src/keel/templates/spec-template.md', stamp)
    _write(root, 'src/keel/templates/core/spec-template.md', stamp)
    _write(root, 'skills/apply-method/SKILL.md', f'This skill ships with keel {version}.\n')
    _configure(root, KEEL_SITES)


def test_version_is_consistent_across_all_sites(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _keel_tree(scratch_repo)
    assert _run(capsys)[:3] == (0, 'pass ok', [])


def test_the_disagreement_names_every_site_in_one_message(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The property that ends the five-round loop: one run, the whole truth.
    _keel_tree(scratch_repo)
    _write(scratch_repo, 'pyproject.toml', "[project]\nversion = '0.18.1'\n")
    _write(scratch_repo, 'src/keel/__init__.py', "__version__ = '0.17.0'\n")
    _write(scratch_repo, 'agents/pre-mortem-review.md', 'no version here\n')
    (scratch_repo / 'skills/apply-method/SKILL.md').unlink()
    code, verdict, findings, _ = _run(capsys)
    assert (code, verdict) == (1, 'fail unreadable')  # `unreadable` wins over `mismatch`
    assert sorted(findings) == sorted(
        [
            'agents/pre-mortem-review.md: no version (the pattern matches nothing)',
            'pyproject.toml: reads 0.18.1, not 0.19.0',
            'skills/apply-method/SKILL.md: no version (the file is missing)',
            'src/keel/__init__.py: reads 0.17.0, not 0.19.0',
        ]
    )


def test_each_patterned_site_reads_the_field_its_parser_would(scratch_repo: Path) -> None:
    # The registry is regexes, because a bump must write them; a regex that reads the wrong field
    # would be invisible to every assertion above. The two structured sites are parsed properly
    # here and compared, so the pattern cannot quietly point at some other version literal.
    _keel_tree(scratch_repo)
    sites = {site.path: site for site in _sites(scratch_repo)}
    plugin = json.loads((scratch_repo / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))
    pyproject = tomllib.loads((scratch_repo / 'pyproject.toml').read_text(encoding='utf-8'))
    read = version_sites.read_site(scratch_repo, sites['.claude-plugin/plugin.json'])
    assert read.value == plugin['version']
    read = version_sites.read_site(scratch_repo, sites['pyproject.toml'])
    assert read.value == pyproject['project']['version']
    # A pattern that points at some other version literal reads a different value: here the
    # description's, which comes first.
    _write(
        scratch_repo,
        '.claude-plugin/plugin.json',
        '{"description": "needs 9.9.9", "version": "0.19.0"}\n',
    )
    decoy = version_sites.Site(
        '.claude-plugin/plugin.json',
        re.compile(r'needs ([0-9.]+)'),
        False,
        version_sites.Line('', 'v'),
    )
    assert (
        version_sites.read_site(scratch_repo, decoy).value
        != json.loads((scratch_repo / '.claude-plugin/plugin.json').read_text(encoding='utf-8'))[
            'version'
        ]
    )


def _sites(root: Path) -> list[version_sites.Site]:
    from railform import frame

    loaded = frame.load(root)
    return version_sites.version_sites(loaded, version_sites.release_lines(loaded))


def test_the_bump_writes_every_writable_site(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _keel_tree(scratch_repo, '0.18.1')
    code, verdict, findings, _ = _run(capsys, '--bump', '9.9.9')
    assert (code, verdict) == (1, 'fail mismatch')  # the authored heading is left behind
    writable = [path for path, _, authored in KEEL_SITES if not authored]
    assert [line.split(':')[0] for line in findings if '->' in line] == writable
    for site in _sites(scratch_repo):
        value = version_sites.read_site(scratch_repo, site).value
        assert value == ('0.18.1' if site.authored else '9.9.9'), site.path
    assert any(line.startswith('CHANGELOG.md: ') for line in findings)


def test_the_bump_leaves_the_changelog_heading_to_its_author(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Rewriting the newest heading's number renames the previous release when no section has been
    # written yet: the one destructive edit a bump could make.
    _keel_tree(scratch_repo)
    before = (scratch_repo / 'CHANGELOG.md').read_bytes()
    _run(capsys, '--bump', '9.9.9')
    assert (scratch_repo / 'CHANGELOG.md').read_bytes() == before


def test_the_bump_is_idempotent(scratch_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _keel_tree(scratch_repo)
    _run(capsys, '--bump', '9.9.9')
    snapshot = {
        p: p.read_bytes() for p in scratch_repo.rglob('*') if p.is_file() and '.git' not in p.parts
    }
    code, verdict, findings, _ = _run(capsys, '--bump', '9.9.9')
    assert (code, verdict) == (1, 'fail mismatch')
    assert not [line for line in findings if '->' in line]
    assert snapshot == {p: p.read_bytes() for p in snapshot}


@pytest.mark.parametrize(
    'bad', ['0.19', '1.2.3.4', 'v1.2.3', ' 1.2.3', '1.2.3\n', 'a.b.c', '1.2.x', '']
)
def test_the_bump_refuses_anything_that_is_not_a_version(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    _keel_tree(scratch_repo)
    before = (scratch_repo / 'pyproject.toml').read_bytes()
    code, verdict, findings, err = _run(capsys, '--bump', bad)
    assert (code, verdict, findings) == (2, 'fail usage', [])
    assert '--bump' in err
    assert (scratch_repo / 'pyproject.toml').read_bytes() == before


# --- reading a site (contract section 4.2) -------------------------------------------------------


def test_a_pattern_is_searched_over_the_whole_text_with_no_implied_flags(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'name\nversion 1.0.0\n')
    # Without `(?m)` the caret anchors the start of the text only.
    _configure(scratch_repo, (('a.txt', r'^version (\d+\.\d+\.\d+)', False),))
    assert _run(capsys)[:2] == (1, 'fail unreadable')
    _configure(scratch_repo, (('a.txt', r'(?m)^version (\d+\.\d+\.\d+)', False),))
    assert _run(capsys)[:2] == (0, 'pass ok')


def test_the_first_match_gives_the_version(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'v 1.0.0\n')
    _write(scratch_repo, 'b.txt', 'v 2.0.0\nv 1.0.0\n')
    _configure(scratch_repo, (('a.txt', r'v (\S+)', False), ('b.txt', r'v (\S+)', False)))
    code, verdict, findings, _ = _run(capsys)
    assert (code, verdict, findings) == (1, 'fail mismatch', ['b.txt: reads 2.0.0, not 1.0.0'])


def test_versions_compare_as_exact_strings(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'v 0.1.0\n')
    _write(scratch_repo, 'b.txt', 'v 0.1.0.dev0\n')
    _write(scratch_repo, 'c.txt', 'v 0.1.0\n')
    pattern = r'v (\S+)'
    _configure(scratch_repo, tuple((f'{n}.txt', pattern, False) for n in 'abc'))
    code, verdict, findings, _ = _run(capsys)
    assert (code, verdict, findings) == (1, 'fail mismatch', ['b.txt: reads 0.1.0.dev0, not 0.1.0'])


def test_the_value_most_sites_read_is_expected_and_a_tie_goes_to_the_earliest(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in (('a', '1.0.0'), ('b', '2.0.0'), ('c', '2.0.0')):
        _write(scratch_repo, f'{name}.txt', f'v {value}\n')
    pattern = r'v (\S+)'
    _configure(scratch_repo, tuple((f'{n}.txt', pattern, False) for n in 'abc'))
    assert _run(capsys)[2] == ['a.txt: reads 1.0.0, not 2.0.0']
    # A tie between two values: the value of the earliest site in version.sites order.
    for name, value in (('a', '1.0.0'), ('b', '2.0.0')):
        _write(scratch_repo, f'{name}.txt', f'v {value}\n')
    _configure(scratch_repo, tuple((f'{n}.txt', pattern, False) for n in 'ab'))
    assert _run(capsys)[2] == ['b.txt: reads 2.0.0, not 1.0.0']
    _configure(scratch_repo, tuple((f'{n}.txt', pattern, False) for n in 'ba'))
    assert _run(capsys)[2] == ['a.txt: reads 1.0.0, not 2.0.0']


def test_an_authored_site_takes_part_in_the_count_and_is_told_apart(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'v 1.0.0\n')
    _write(scratch_repo, 'b.txt', 'v 2.0.0\n')
    _write(scratch_repo, 'c.txt', 'v 2.0.0\n')
    pattern = r'v (\S+)'
    _configure(
        scratch_repo,
        (('a.txt', pattern, False), ('b.txt', pattern, True), ('c.txt', pattern, True)),
    )
    assert _run(capsys)[2] == ['a.txt: reads 1.0.0, not 2.0.0']
    _configure(
        scratch_repo,
        (('a.txt', pattern, True), ('b.txt', pattern, False), ('c.txt', pattern, False)),
    )
    assert _run(capsys)[2] == ['a.txt: reads 1.0.0, not 2.0.0 (authored: write it by hand)']


def test_an_unreadable_site_says_why(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'ok.txt', 'v 1.0.0\n')
    _write(scratch_repo, 'binary.txt', b'v 1.0.0 \xff\xfe\n')
    _write(scratch_repo, 'empty.txt', 'v \n')
    (scratch_repo / 'folder.txt').mkdir()
    pattern = r'v (\S*)'
    names = ('ok.txt', 'binary.txt', 'nothing.txt', 'empty.txt', 'folder.txt')
    _configure(scratch_repo, tuple((n, pattern, False) for n in names))
    code, verdict, findings, _ = _run(capsys)
    assert (code, verdict) == (1, 'fail unreadable')
    assert findings == [
        'binary.txt: no version (the file is not UTF-8)',
        'nothing.txt: no version (the file is missing)',
        'empty.txt: no version (the pattern matches nothing)',
        'folder.txt: no version (the file is missing)',
    ]


def test_an_unreadable_site_is_never_written(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'v 1.0.0\n')
    _write(scratch_repo, 'b.txt', 'nothing\n')
    _configure(scratch_repo, (('a.txt', r'v (\S+)', False), ('b.txt', r'v (\S+)', False)))
    code, verdict, findings, _ = _run(capsys, '--bump', '2.0.0')
    assert (code, verdict) == (1, 'fail unreadable')
    assert findings[0] == 'a.txt: 1.0.0 -> 2.0.0'
    assert (scratch_repo / 'b.txt').read_bytes() == b'nothing\n'


def test_two_sites_in_one_file_are_both_written(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'one 1.0.0\ntwo 1.0.0\n')
    _configure(scratch_repo, (('a.txt', r'one (\S+)', False), ('a.txt', r'two (\S+)', False)))
    assert _run(capsys, '--bump', '1.1.0')[:2] == (0, 'pass ok')
    assert (scratch_repo / 'a.txt').read_bytes() == b'one 1.1.0\ntwo 1.1.0\n'


def test_no_sites_passes_and_says_so(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, verdict, findings, _ = _run(capsys)
    assert (code, verdict) == (0, 'pass ok')
    assert len(findings) == 1
    assert 'no version sites' in findings[0]
    # With nothing to write, a bump is the check.
    assert _run(capsys, '--bump', '1.0.0')[:2] == (0, 'pass ok')


# --- release lines (contract section 2) ----------------------------------------------------------

CRAFT_ROWS = (('plugins/a/', 'a--v'), ('plugins/b/', 'b--v'))
CRAFT_SITES = tuple(
    site
    for plugin in 'ab'
    for site in (
        (f'plugins/{plugin}/.claude-plugin/plugin.json', r'"version":\s*"([0-9.]+)"', False),
        (f'plugins/{plugin}/CHANGELOG.md', r'(?m)^##\s*\[(\d+\.\d+\.\d+)\]', True),
    )
)


def _craft_tree(root: Path, a: str = '0.6.1', b: str = '0.18.0') -> None:
    for plugin, version in (('a', a), ('b', b)):
        _write(
            root, f'plugins/{plugin}/.claude-plugin/plugin.json', f'{{"version": "{version}"}}\n'
        )
        _write(root, f'plugins/{plugin}/CHANGELOG.md', f'## [{version}] - 2026-10-11\n')
    _configure(root, CRAFT_SITES, CRAFT_ROWS)


def test_lines_are_judged_apart(scratch_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _craft_tree(scratch_repo)
    assert _run(capsys)[:3] == (0, 'pass ok', [])
    # A site behind in one line is judged against its own line, not the repository's.
    _write(scratch_repo, 'plugins/b/CHANGELOG.md', '## [0.17.0] - 2026-10-11\n')
    code, verdict, findings, _ = _run(capsys)
    assert (code, verdict) == (1, 'fail mismatch')
    assert findings == [
        'plugins/b/CHANGELOG.md: reads 0.17.0, not 0.18.0 (authored: write it by hand)'
    ]


def test_a_bump_needs_a_prefix_when_more_than_one_line_has_a_writable_site(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _craft_tree(scratch_repo)
    before = {p: p.read_bytes() for p in scratch_repo.rglob('*.json')}
    code, verdict, findings, err = _run(capsys, '--bump', '0.7.0')
    assert (code, verdict, findings) == (2, 'fail usage', [])
    assert "'plugins/a/'" in err
    assert "'plugins/b/'" in err
    assert before == {p: p.read_bytes() for p in before}

    code, verdict, findings, _ = _run(capsys, '--bump', '0.7.0', '--prefix', 'plugins/a/')
    assert (code, verdict) == (1, 'fail mismatch')  # plugin a's heading is authored
    assert findings[0] == 'plugins/a/.claude-plugin/plugin.json: 0.6.1 -> 0.7.0'
    assert (scratch_repo / 'plugins/a/.claude-plugin/plugin.json').read_text(encoding='utf-8') == (
        '{"version": "0.7.0"}\n'
    )
    for path, content in before.items():
        if 'plugins/a/' not in path.as_posix():
            assert path.read_bytes() == content


@pytest.mark.parametrize('prefix', ['plugins/a', 'plugins/', 'A/', '', 'plugins/c/'])
def test_a_prefix_is_the_exact_prefix_of_a_row(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str], prefix: str
) -> None:
    _craft_tree(scratch_repo)
    code, verdict, _, err = _run(capsys, '--bump', '0.7.0', '--prefix', prefix)
    assert (code, verdict) == (2, 'fail usage')
    assert 'plugins/a/' in err


def test_the_prefix_of_the_root_row_is_the_empty_string(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'v 1.0.0\n')
    _write(scratch_repo, 'sub/b.txt', 'v 1.0.0\n')
    pattern = r'v (\S+)'
    _configure(
        scratch_repo,
        (('a.txt', pattern, False), ('sub/b.txt', pattern, False)),
        (('', 'v'), ('sub/', 'sub--v')),
    )
    assert _run(capsys, '--bump', '1.1.0')[:2] == (2, 'fail usage')
    assert _run(capsys, '--bump', '1.1.0', '--prefix', '')[:2] == (0, 'pass ok')
    assert (scratch_repo / 'a.txt').read_bytes() == b'v 1.1.0\n'
    assert (scratch_repo / 'sub/b.txt').read_bytes() == b'v 1.0.0\n'


def test_a_site_belongs_to_the_row_with_the_longest_prefix(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'a.txt', 'v 1.0.0\n')
    _write(scratch_repo, 'sub/b.txt', 'v 2.0.0\n')
    pattern = r'v (\S+)'
    sites = (('a.txt', pattern, False), ('sub/b.txt', pattern, False))
    _configure(scratch_repo, sites, (('', 'v'), ('sub/', 'sub--v')))
    assert _run(capsys)[:3] == (0, 'pass ok', [])  # two lines, each agreeing with itself
    _configure(scratch_repo, sites, (('', 'v'),))
    assert _run(capsys)[2] == ['sub/b.txt: reads 2.0.0, not 1.0.0']


def test_a_prefix_without_a_bump_is_a_usage_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(capsys, '--prefix', '')[:3] == (2, 'fail usage', [])


def test_the_default_frame_has_one_line_covering_every_path(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'deep/er/a.txt', 'v 1.0.0\n')
    _write(scratch_repo, 'b.txt', 'v 1.0.0\n')
    pattern = r'v (\S+)'
    _write(
        scratch_repo,
        'railform.toml',
        _frame((('deep/er/a.txt', pattern, False), ('b.txt', pattern, False))),
    )
    assert _run(capsys, '--bump', '2.0.0')[:2] == (0, 'pass ok')  # `--prefix` is optional


# --- exit 2 (contract sections 2 and 4.5) --------------------------------------------------------


@pytest.mark.parametrize(
    ('path', 'pattern'),
    [
        ('/etc/version', r'v (\S+)'),
        ('C:/version', r'v (\S+)'),
        ('C:version', r'v (\S+)'),
        ('//server/share/v', r'v (\S+)'),
        ('../outside.txt', r'v (\S+)'),
        ('a/../b.txt', r'v (\S+)'),
        ('a\\b.txt', r'v (\S+)'),
        ('', r'v (\S+)'),
        ('a.txt', r'v \S+'),
        ('a.txt', r'v ((\S+)'),
    ],
)
def test_a_site_that_cannot_be_read_safely_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str], path: str, pattern: str
) -> None:
    _write(scratch_repo, 'a.txt', 'v 1.0.0\n')
    _configure(scratch_repo, ((path, pattern, False),))
    code, verdict, findings, err = _run(capsys)
    assert (code, verdict, findings) == (2, 'fail usage', [])
    assert err.strip()
    # A bump stops before any write.
    code, verdict, _, _ = _run(capsys, '--bump', '2.0.0')
    assert (code, verdict) == (2, 'fail usage')
    assert (scratch_repo / 'a.txt').read_bytes() == b'v 1.0.0\n'


def test_a_site_that_no_row_covers_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'other/a.txt', 'v 1.0.0\n')
    _configure(scratch_repo, (('other/a.txt', r'v (\S+)', False),), (('plugins/a/', 'a--v'),))
    code, verdict, findings, err = _run(capsys)
    assert (code, verdict, findings) == (2, 'fail usage', [])
    assert 'other/a.txt' in err


@pytest.mark.parametrize('rows', [(('a/', 'a--v'), ('a/', 'b--v')), (('', 'v'), ('', 'x'))])
def test_two_tag_rows_with_one_prefix_are_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str], rows: tuple[tuple[str, str], ...]
) -> None:
    _configure(scratch_repo, (), rows)
    assert _run(capsys)[:3] == (2, 'fail usage', [])


def test_two_changelog_records_with_one_prefix_are_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        'railform.toml',
        '[[changelog.records]]\nprefix = "a/"\nfile = "A.md"\n\n'
        '[[changelog.records]]\nprefix = "a/"\nfile = "B.md"\n',
    )
    assert _run(capsys)[:3] == (2, 'fail usage', [])


def test_a_railform_toml_that_cannot_be_read_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, 'railform.toml', '[version\n')
    assert _run(capsys)[:3] == (2, 'fail usage', [])


# --- the frame changes the verdict (§2's per-gate test) ------------------------------------------


def test_the_frame_changes_the_verdict(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _standard_tree(scratch_repo, '0.2.0', '0.1.0', '0.2.0')
    assert _run(capsys)[:2] == (1, 'fail mismatch')
    # Removing the site that is behind from version.sites turns the case into a pass.
    _configure(scratch_repo, (PYPROJECT, HEADING))
    assert _run(capsys)[:2] == (0, 'pass ok')
    # Marking it authored leaves the verdict a fail, and the bump leaves it untouched.
    _configure(scratch_repo, (PYPROJECT, (INIT[0], INIT[1], True), HEADING))
    behind = (scratch_repo / 'src/pkg/__init__.py').read_bytes()
    code, verdict, findings, _ = _run(capsys, '--bump', '0.2.0')
    assert (code, verdict) == (1, 'fail mismatch')
    assert (scratch_repo / 'src/pkg/__init__.py').read_bytes() == behind
    assert any(line.startswith('src/pkg/__init__.py: ') for line in findings)
    # A different pattern reads a different field.
    _configure(scratch_repo, (PYPROJECT, (INIT[0], r"NAME = '([^']+)'", False), HEADING))
    assert _run(capsys)[:2] == (1, 'fail unreadable')


def test_checking_writes_nothing(scratch_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _standard_tree(scratch_repo, '0.2.0', '0.1.0', '0.2.0')
    before = {
        p: p.read_bytes() for p in scratch_repo.rglob('*') if p.is_file() and '.git' not in p.parts
    }
    _run(capsys)
    assert before == {p: p.read_bytes() for p in before}


# --- the shared reading, for the changelog gate and the release check ----------------------------


def test_line_of_gives_the_longest_prefix_and_none_when_no_row_covers_a_path() -> None:
    lines = [
        version_sites.Line('', 'v'),
        version_sites.Line('plugins/', 'p--v'),
        version_sites.Line('plugins/a/', 'a--v'),
    ]
    assert version_sites.line_of(lines, 'x.txt') == lines[0]
    assert version_sites.line_of(lines, 'plugins/b/x') == lines[1]
    assert version_sites.line_of(lines, 'plugins/a/x') == lines[2]
    assert version_sites.line_of(lines, 'Plugins/a/x') == lines[0]  # case-sensitive
    assert version_sites.line_of(lines[1:], 'x.txt') is None


@pytest.mark.parametrize(
    ('prefix', 'name'),
    [
        ('', ''),
        ('/', ''),
        ('plugins/humblepowers/', 'humblepowers'),
        ('plugins/humblepowers', 'humblepowers'),
        ('keel', 'keel'),
        ('a//b/', 'b'),
    ],
)
def test_the_name_of_a_line_is_the_last_segment_of_its_prefix(prefix: str, name: str) -> None:
    assert version_sites.line_name(prefix) == name
    assert version_sites.Line(prefix, 'v').name == name


@pytest.mark.parametrize(
    ('value', 'parsed'),
    [
        ('1.2.3', (1, 2, 3)),
        ('0.10.0', (0, 10, 0)),
        ('1.2', None),
        ('1.2.3.4', None),
        ('1.2.3-rc1', None),
        ('0.1.0.dev0', None),
        ('1.2.x', None),
        ('', None),
        (None, None),
    ],
)
def test_a_version_is_three_dot_separated_integers(value: str | None, parsed: object) -> None:
    assert version_sites.triple(value) == parsed


def test_full_width_digits_are_not_a_version() -> None:
    digits = ''.join(chr(0xFF10 + n) for n in (1, 2, 3))
    assert version_sites.triple(f'{digits}.{digits}.{digits}') is None


def test_site_value_is_group_one_of_the_first_match() -> None:
    import re

    pattern = re.compile(r'v(\d+)(?:-(x))?')
    assert version_sites.site_value(pattern, 'a v12-x v3') == '12'
    assert version_sites.site_value(pattern, 'nothing') is None
    assert version_sites.site_value(re.compile(r'v(\d*)'), 'v') is None


# --- the verb in the workflow --------------------------------------------------------------------

pytestmark_model = pytest.mark.skipif(bash() is None, reason='no bash on this machine')


def _adopt(root: Path, project_type: str, capsys: pytest.CaptureFixture[str]) -> None:
    build(root, project_type)
    assert main(['adopt', '--pin', PIN]) == 0
    capsys.readouterr()


@pytestmark_model
def test_red_proof_a_site_behind_turns_gate_red_through_the_caller(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The python-package fixture: the `checks` job stops at the version-sites step on every leg,
    # before its pipeline, so no network is needed. The audit job needs one, so it is given.
    _adopt(scratch_repo, 'python-package', capsys)
    _write(scratch_repo, 'src/demo/__version__.txt', 'version 0.0.9\n')
    _write(
        scratch_repo,
        'railform.toml',
        _frame(
            (
                ('pyproject.toml', '(?m)^version = "([^"]+)"', False),
                ('src/demo/__version__.txt', r'version (\S+)', False),
            ),
            project_type='python-package',
        ),
    )
    runner = Runner(
        root=scratch_repo,
        scratch=tmp_path,
        github=github_context('push'),
        event={},
        given={'audit': 'success'},
    )
    run = runner.run(scratch_repo / CI)
    results = run.results()
    assert results['railform/checks'] == 'failure', run.text()
    assert 'railform-verdict: version-sites fail mismatch' in run.text()
    assert 'src/demo/__version__.txt: reads 0.0.9, not 0.1.0' in run.text()
    assert results['railform'] == 'failure', run.text()
    assert results['gate'] == 'failure', run.text()


@pytest.mark.slow
@pytestmark_model
def test_a_site_behind_then_bumped_turns_gate_red_then_green_through_the_caller(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The plugin-marketplace fixture runs its pipeline for real (see test_python_ci_runs.py).
    _adopt(scratch_repo, 'plugin-marketplace', capsys)
    _write(scratch_repo, 'VERSION', '0.1.0\n')
    _write(scratch_repo, 'docs/VERSION', '0.0.9\n')
    _write(
        scratch_repo,
        'railform.toml',
        _frame(
            (('VERSION', r'(\S+)', False), ('docs/VERSION', r'(\S+)', False)),
            project_type='plugin-marketplace',
        ),
    )

    def run() -> Any:
        runner = Runner(
            root=scratch_repo,
            scratch=tmp_path,
            github=github_context('push'),
            event={},
            given={'audit': 'success'},
        )
        return runner.run(scratch_repo / CI)

    red = run()
    assert red.results()['railform/checks'] == 'failure', red.text()
    assert 'railform-verdict: version-sites fail mismatch' in red.text()
    assert red.results()['gate'] == 'failure', red.text()
    assert main(['version-sites', '--bump', '0.1.0']) == 0
    capsys.readouterr()
    green = run()
    assert 'railform-verdict: version-sites pass ok' in green.text()
    assert green.results()['railform/checks'] == 'success', green.text()
    assert green.results()['gate'] == 'success', green.text()
