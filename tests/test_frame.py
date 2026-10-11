"""The frame (development-rails spec, §2; interface contract, sections 3 and 4)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from railform import frame
from railform.cli import main

PRESETS = ('python-package', 'python-scripts', 'plugin-marketplace')

# Every key spec §2 lists, plus the keys the interface contract adds (section 3). Written out
# here, apart from the module, so the schema test is not the module checking itself.
SPEC_KEYS = (
    'project.type',
    'project.layout',
    'commit.types',
    'commit.attribution_patterns',
    'changelog.records',
    'changelog.contract_surfaces',
    'changelog.trailer',
    'changelog.consumer_affecting',
    'changelog.enabled',
    'hooks.dir',
    'hooks.names',
    'hooks.wrapper',
    'branch.pattern',
    'branch.allow',
    'session_loaded',
    'private_refs.names_file_env',
    'private_refs.placeholders',
    'ci.os',
    'ci.setup',
    'ci.ruff_version',
    'python.versions',
    'python.package',
    'python.sync_args',
    'python.test_command',
    'python.lint_paths',
    'python.type_check',
    'version.sites',
    'release.metadata_paths',
    'release.since',
    'release.tag_prefix',
    'release.enabled',
    'exceptions',
)
CONTRACT_KEYS = (
    'ci.extra_workflows',
    'python.newest',
    'hooks.pre_commit_version',
    'meta.parameter_keys',
)
PARAMETER_KEYS = [
    'project.type',
    'project.layout',
    'python.versions',
    'python.package',
    'version.sites',
    'changelog.records',
    'changelog.contract_surfaces',
    'release.since',
    'release.tag_prefix',
]

VERDICT = re.compile(r'railform-verdict: frame (?P<status>pass|fail) (?P<code>[a-z0-9-]+)')


def _get(table: dict[str, Any], dotted: str) -> Any:
    value: Any = table
    for part in dotted.split('.'):
        assert isinstance(value, dict), dotted
        assert part in value, f'{dotted} is missing'
        value = value[part]
    return value


def _write(repo: Path, text: str) -> None:
    (repo / 'railform.toml').write_text(text, encoding='utf-8')


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, list[str]]:
    """Run `railform frame ARGS`; return the exit code, the verdict code and the findings."""
    code = main(['frame', *args])
    lines = capsys.readouterr().out.splitlines()
    match = VERDICT.fullmatch(lines[-1])
    assert match is not None, lines[-1]
    return code, match['code'], lines[:-1]


def _check(capsys: pytest.CaptureFixture[str]) -> tuple[int, str, list[str]]:
    return _run(capsys, '--check')


def _effective(repo: Path, text: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    _write(repo, text)
    code, verdict, lines = _run(capsys, '--json')
    assert (code, verdict) == (0, 'ok')
    assert len(lines) == 1
    return json.loads(lines[0])


# --- the schema --------------------------------------------------------------------------------


@pytest.mark.parametrize('preset', PRESETS)
def test_every_key_is_present_in_every_presets_effective_frame(preset: str) -> None:
    effective = frame.effective_frame({'project': {'type': preset}})
    for key in SPEC_KEYS + CONTRACT_KEYS:
        _get(effective, key)
    assert frame.validate(effective, complete=True) == []
    assert 'presets' not in effective


@pytest.mark.parametrize('preset', PRESETS)
def test_the_schema_of_each_preset(preset: str) -> None:
    effective = frame.effective_frame({'project': {'type': preset}})
    assert effective['project']['type'] == preset
    strings = (
        'project.layout',
        'changelog.trailer',
        'hooks.dir',
        'hooks.wrapper',
        'hooks.pre_commit_version',
        'branch.pattern',
        'private_refs.names_file_env',
        'ci.ruff_version',
        'python.newest',
        'python.package',
        'python.test_command',
        'python.type_check',
        'release.since',
    )
    for key in strings:
        assert isinstance(_get(effective, key), str), key
    string_lists = (
        'commit.types',
        'commit.attribution_patterns',
        'changelog.contract_surfaces',
        'hooks.names',
        'branch.allow',
        'session_loaded',
        'private_refs.placeholders',
        'ci.os',
        'ci.extra_workflows',
        'python.versions',
        'python.sync_args',
        'python.lint_paths',
        'release.metadata_paths',
        'meta.parameter_keys',
    )
    for key in string_lists:
        value = _get(effective, key)
        assert isinstance(value, list), key
        assert all(isinstance(item, str) for item in value), key
    for key in ('changelog.enabled', 'release.enabled'):
        assert isinstance(_get(effective, key), bool), key
    assert effective['changelog']['consumer_affecting'] in ('advisory', 'error')
    assert isinstance(effective['ci']['setup'], dict)
    for key, fields in (
        ('changelog.records', {'prefix', 'file'}),
        ('release.tag_prefix', {'prefix', 'tag'}),
    ):
        rows = _get(effective, key)
        assert isinstance(rows, list), key
        for row in rows:
            assert set(row) == fields, key
            assert all(isinstance(value, str) for value in row.values()), key
    assert isinstance(effective['version']['sites'], list)
    versions = effective['python']['versions']
    assert versions
    newest = max(versions, key=lambda v: tuple(int(part) for part in v.split('.')))
    assert newest == effective['python']['newest']
    assert effective['exceptions'] == []
    re.compile(effective['branch']['pattern'])
    for pattern in effective['commit']['attribution_patterns']:
        re.compile(pattern)


def test_the_default_frame_values_the_spec_names() -> None:
    effective = frame.effective_frame(None)
    assert effective['project']['type'] == 'python-package'
    assert effective['commit']['types'] == [
        'feat',
        'fix',
        'docs',
        'chore',
        'refactor',
        'test',
        'ci',
        'build',
        'perf',
        'style',
        'revert',
        'release',
    ]
    assert effective['changelog']['consumer_affecting'] == 'advisory'
    assert effective['changelog']['trailer'] == 'Changelog: none (<reason>)'
    assert effective['hooks']['dir'] == '.githooks'
    assert effective['hooks']['names'] == ['pre-commit', 'commit-msg', 'pre-push']
    assert effective['private_refs']['names_file_env'] == 'RAILFORM_PRIVATE_NAMES'
    assert effective['private_refs']['placeholders'] == [
        'me',
        'x',
        'someone',
        'alice',
        'user',
        'runner',
        'public',
        'default',
        'shared',
        'runneradmin',
    ]
    assert effective['python']['lint_paths'] == ['.']
    assert effective['python']['newest'] == '3.14'
    assert effective['ci']['extra_workflows'] == []
    assert effective['meta']['parameter_keys'] == PARAMETER_KEYS
    assert 'dependabot/**' in effective['branch']['allow']
    for glob in (
        '**/skills/**',
        '**/agents/**',
        '**/commands/**',
        '**/hooks/**',
        '**/.claude-plugin/**',
        '.claude/**',
        '**/CLAUDE.md',
        '**/AGENTS.md',
        '.mcp.json',
    ):
        assert glob in effective['session_loaded']


def test_the_uv_types_run_pre_commit_through_uv() -> None:
    for preset in ('python-package', 'python-scripts'):
        effective = frame.effective_frame({'project': {'type': preset}})
        assert effective['hooks']['wrapper'] == 'uv run --frozen python -m pre_commit hook-impl'


def test_only_the_plugin_marketplace_preset_pins_a_pre_commit_version(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for preset in ('python-package', 'python-scripts'):
        effective = frame.effective_frame({'project': {'type': preset}})
        assert effective['hooks']['pre_commit_version'] == ''
        _write(scratch_repo, f'[project]\ntype = "{preset}"\n')
        assert _check(capsys) == (0, 'ok', [])
    plugins = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    assert plugins['hooks']['pre_commit_version'] != ''


def test_the_plugin_marketplace_test_command_pins_the_locked_pytest() -> None:
    lock_path = Path(__file__).resolve().parents[1] / 'uv.lock'
    lock = tomllib.loads(lock_path.read_text(encoding='utf-8'))
    locked = next(p['version'] for p in lock['package'] if p['name'] == 'pytest')
    effective = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    assert f'--with pytest=={locked} ' in effective['python']['test_command']


def test_the_plugin_marketplace_metadata_globs_name_the_manifests() -> None:
    effective = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    assert effective['release']['metadata_paths'] == [
        '.claude-plugin/marketplace.json',
        'plugins/*/.claude-plugin/plugin.json',
    ]


def test_the_plugin_marketplace_wrapper_reads_the_pinned_pre_commit_version() -> None:
    effective = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    version = effective['hooks']['pre_commit_version']
    assert re.fullmatch(r'\d+\.\d+\.\d+', version)
    assert effective['hooks']['wrapper'] == f'uvx pre-commit=={version} hook-impl'
    moved = frame.effective_frame(
        {'project': {'type': 'plugin-marketplace'}, 'hooks': {'pre_commit_version': '9.9.9'}}
    )
    assert moved['hooks']['wrapper'] == 'uvx pre-commit==9.9.9 hook-impl'


def test_the_type_of_each_preset_shapes_its_pipeline() -> None:
    package = frame.effective_frame({'project': {'type': 'python-package'}})
    scripts = frame.effective_frame({'project': {'type': 'python-scripts'}})
    plugins = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    assert package['python']['type_check'] != ''
    assert scripts['python']['type_check'] == ''
    assert plugins['python']['type_check'] == ''
    assert plugins['ci']['ruff_version'] != ''
    assert package['ci']['ruff_version'] == ''
    assert len(package['ci']['os']) == 2
    assert len(plugins['ci']['os']) == 1
    for effective in (package, scripts, plugins):
        assert effective['python']['test_command'].strip() not in ('', 'true')


# --- the verb: printing ------------------------------------------------------------------------


def test_without_a_railform_toml_the_frame_is_the_python_package_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _check(capsys) == (0, 'ok', [])
    code, verdict, lines = _run(capsys, '--json')
    assert (code, verdict) == (0, 'ok')
    assert json.loads(lines[0]) == frame.effective_frame(None)


def test_frame_prints_the_effective_frame_as_toml(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        '[project]\ntype = "python-scripts"\n[python]\nversions = ["3.12"]\n'
        '[[exceptions]]\nkey = "ci.os"\nvalue = ["ubuntu-latest"]\nreason = "one leg"\n'
        '[ci]\nos = ["ubuntu-latest"]\n',
    )
    code, verdict, lines = _run(capsys)
    assert (code, verdict) == (0, 'ok')
    printed = tomllib.loads('\n'.join(lines))
    code, verdict, json_lines = _run(capsys, '--json')
    assert printed == json.loads(json_lines[0])
    assert printed['python']['versions'] == ['3.12']
    assert printed['exceptions'] == [
        {'key': 'ci.os', 'value': ['ubuntu-latest'], 'reason': 'one leg'}
    ]


def test_the_toml_printer_round_trips_awkward_strings() -> None:
    value = {
        'a': 'quote " back \\ tab \t newline \n del \x7f é \U0001f600',
        'b': {'': 'empty key', 'with space': [1, True, 1.5]},
        'rows': [{'key': 'k', 'value': {'x': ['y']}}],
    }
    assert tomllib.loads(frame.dumps_toml(value)) == value


def test_json_nests_by_table_and_lists_exceptions_as_objects(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    effective = _effective(
        scratch_repo,
        '[python]\ntest_command = "true"\n'
        '[[exceptions]]\nkey = "python.test_command"\nvalue = "true"\nreason = "migrating"\n',
        capsys,
    )
    assert effective['python']['test_command'] == 'true'
    assert effective['ci']['setup'] == frame.effective_frame(None)['ci']['setup']
    assert effective['exceptions'] == [
        {'key': 'python.test_command', 'value': 'true', 'reason': 'migrating'}
    ]


def test_tables_merge_key_by_key_and_arrays_replace(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    default = frame.effective_frame(None)
    effective = _effective(
        scratch_repo,
        '[python]\nversions = ["3.13"]\n[ci.setup]\nextra_installs = ["graphviz"]\n'
        '[[exceptions]]\nkey = "ci.setup.extra_installs"\nvalue = ["graphviz"]\nreason = "docs"\n',
        capsys,
    )
    assert effective['python']['versions'] == ['3.13']
    assert effective['python']['test_command'] == default['python']['test_command']
    assert effective['ci']['setup']['extra_installs'] == ['graphviz']
    assert effective['ci']['setup']['fetch_depth'] == default['ci']['setup']['fetch_depth']


def test_the_flags_are_exclusive(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(capsys, '--check', '--json')[:2] == (2, 'usage')


def test_help_prints_no_verdict_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(['frame', '--help']) == 0
    out = capsys.readouterr().out
    assert '--github-output' in out
    assert 'railform-verdict:' not in out


def test_an_unparsable_railform_toml_exits_2(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, '[python\n')
    for args in ((), ('--check',), ('--json',)):
        code = main(['frame', *args])
        captured = capsys.readouterr()
        assert code == 2
        assert captured.out.splitlines()[-1] == 'railform-verdict: frame fail usage'
        assert 'railform.toml' in captured.err


def test_outside_a_git_repository_frame_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / 'not-a-repo'
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    assert _check(capsys)[:2] == (2, 'usage')


def test_frame_reads_the_railform_toml_at_the_top_level(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, '[python]\ntest_command = ""\n')
    nested = scratch_repo / 'sub' / 'dir'
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert _check(capsys)[:2] == (1, 'unlisted-departure')


# --- the red proof of §2's acceptance criterion -------------------------------------------------


@pytest.mark.parametrize('preset', PRESETS)
@pytest.mark.parametrize('command', ['', 'true', '   '])
def test_a_blanked_or_no_op_test_command_without_an_exceptions_row_fails(
    preset: str, command: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, f'[project]\ntype = "{preset}"\n[python]\ntest_command = "{command}"\n')
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'unlisted-departure')
    assert any('python.test_command' in finding for finding in findings)
    assert all(finding.startswith('railform.toml: ') for finding in findings)


@pytest.mark.parametrize('preset', PRESETS)
@pytest.mark.parametrize('command', ['', 'true'])
def test_the_same_test_command_passes_with_a_matching_exceptions_row(
    preset: str, command: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        f'[project]\ntype = "{preset}"\n[python]\ntest_command = "{command}"\n'
        f'[[exceptions]]\nkey = "python.test_command"\nvalue = "{command}"\n'
        'reason = "the suite runs in another workflow during the migration"\n',
    )
    assert _check(capsys) == (0, 'ok', [])


def test_a_row_for_another_value_does_not_cover_the_departure(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        '[python]\ntest_command = "true"\n'
        '[[exceptions]]\nkey = "python.test_command"\nvalue = ""\nreason = "stale"\n',
    )
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'bad-exception')
    assert any('python.test_command' in f and 'no exceptions row' in f for f in findings)


@pytest.mark.parametrize('preset', PRESETS)
def test_each_preset_alone_passes_the_check(
    preset: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, f'[project]\ntype = "{preset}"\n')
    assert _check(capsys) == (0, 'ok', [])


def test_a_preset_value_restated_is_not_a_departure(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command = frame.effective_frame(None)['python']['test_command']
    _write(scratch_repo, f'[python]\ntest_command = "{command}"\n')
    assert _check(capsys) == (0, 'ok', [])


def test_a_departure_is_judged_against_the_repositorys_own_preset(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package_type_check = frame.effective_frame(None)['python']['type_check']
    _write(
        scratch_repo,
        f'[project]\ntype = "python-scripts"\n[python]\ntype_check = "{package_type_check}"\n',
    )
    assert _check(capsys)[:2] == (1, 'unlisted-departure')


# --- parameter keys and the guard lists ---------------------------------------------------------


def test_parameter_keys_are_never_departures(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        '[project]\ntype = "python-package"\nlayout = "flat"\n'
        '[python]\nversions = ["3.12", "3.13"]\npackage = "something"\n'
        '[changelog]\ncontract_surfaces = ["src/x/api.py"]\n'
        '[[changelog.records]]\nprefix = ""\nfile = "CHANGELOG.md"\n'
        '[[changelog.records]]\nprefix = "plugins/a/"\nfile = "plugins/a/CHANGELOG.md"\n'
        '[release]\nsince = "v1.0.0"\n'
        '[[release.tag_prefix]]\nprefix = "plugins/a/"\ntag = "a-v"\n'
        '[[version.sites]]\npath = "src/x/__init__.py"\npattern = "__version__ = \\"(.+)\\""\n',
    )
    assert _check(capsys) == (0, 'ok', [])


@pytest.mark.parametrize('key', ['session_loaded', 'private_refs.placeholders'])
def test_a_guard_list_may_grow_without_an_exception(
    key: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    default = _get(frame.effective_frame(None), key)
    grown = json.dumps([*default, 'extra/**'])
    table, _, leaf = key.rpartition('.')
    _write(scratch_repo, f'[{table}]\n{leaf} = {grown}\n' if table else f'{leaf} = {grown}\n')
    assert _check(capsys) == (0, 'ok', [])


def _guard_list_toml(key: str, items: list[str]) -> str:
    table, _, leaf = key.rpartition('.')
    value = json.dumps(items)
    return f'[{table}]\n{leaf} = {value}\n' if table else f'{leaf} = {value}\n'


@pytest.mark.parametrize('key', ['session_loaded', 'private_refs.placeholders'])
def test_red_proof_a_guard_list_with_one_extra_element_keeps_every_default_element(
    key: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The development-rails spec, §13 (the craft-collection pilot): the repository's list is
    # joined to the default, default first, so a repository names only what it adds and its copy
    # never goes stale when a later pin adds a default element.
    default = _get(frame.effective_frame(None), key)
    config = tomllib.loads(_guard_list_toml(key, ['extra/**']))
    assert _get(frame.effective_frame(config), key) == [*default, 'extra/**']
    _write(scratch_repo, _guard_list_toml(key, ['extra/**']))
    assert _check(capsys) == (0, 'ok', [])
    assert main(['frame', '--json']) == 0
    printed = json.loads(capsys.readouterr().out.splitlines()[0])
    assert _get(printed, key) == [*default, 'extra/**']


@pytest.mark.parametrize('key', ['session_loaded', 'private_refs.placeholders'])
def test_a_guard_list_cannot_drop_a_default_element(
    key: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    default = _get(frame.effective_frame(None), key)
    # A restated list without the first default element, and a repeated element: the union keeps
    # every default element once, in the default's order, then the repository's own.
    listed = [*default[1:], 'extra/**', default[2], 'extra/**']
    config = tomllib.loads(_guard_list_toml(key, listed))
    assert _get(frame.effective_frame(config), key) == [*default, 'extra/**']
    _write(scratch_repo, _guard_list_toml(key, listed))
    assert _check(capsys) == (0, 'ok', [])
    # Nothing departs, so an exceptions row for the key matches no departure.
    row = f'[[exceptions]]\nkey = "{key}"\nvalue = {json.dumps(listed)}\nreason = "measured"\n'
    _write(scratch_repo, _guard_list_toml(key, listed) + row)
    assert _check(capsys)[:2] == (1, 'bad-exception')


def test_ci_extra_workflows_is_preset_governed(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = '[ci]\nextra_workflows = ["docs.yml"]\n'
    _write(scratch_repo, body)
    assert _check(capsys)[:2] == (1, 'unlisted-departure')
    _write(
        scratch_repo,
        body + '[[exceptions]]\nkey = "ci.extra_workflows"\nvalue = ["docs.yml"]\n'
        'reason = "the docs build stays a separate required context"\n',
    )
    assert _check(capsys) == (0, 'ok', [])


def test_a_version_site_may_be_authored_and_defaults_to_not_authored(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    effective = _effective(
        scratch_repo,
        '[[version.sites]]\npath = "a.py"\npattern = "v(.+)"\nauthored = true\n'
        '[[version.sites]]\npath = "b.py"\npattern = "v(.+)"\n',
        capsys,
    )
    assert effective['version']['sites'] == [
        {'path': 'a.py', 'pattern': 'v(.+)', 'authored': True},
        {'path': 'b.py', 'pattern': 'v(.+)', 'authored': False},
    ]
    assert _check(capsys) == (0, 'ok', [])


def test_per_plugin_records_replace_the_root_row(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    effective = _effective(
        scratch_repo,
        '[project]\ntype = "plugin-marketplace"\n'
        '[[changelog.records]]\nprefix = "plugins/a/"\nfile = "plugins/a/CHANGELOG.md"\n'
        '[[changelog.records]]\nprefix = "plugins/b/"\nfile = "plugins/b/CHANGELOG.md"\n'
        '[[release.tag_prefix]]\nprefix = "plugins/a/"\ntag = "a-v"\n'
        '[[release.tag_prefix]]\nprefix = "plugins/b/"\ntag = "b-v"\n',
        capsys,
    )
    records = effective['changelog']['records']
    tags = effective['release']['tag_prefix']
    assert [row['prefix'] for row in records] == ['plugins/a/', 'plugins/b/']
    assert [row['prefix'] for row in tags] == ['plugins/a/', 'plugins/b/']
    assert not any(row['prefix'] in ('', '.', './') for row in records + tags)
    assert _check(capsys) == (0, 'ok', [])


# --- unknown-key -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'text',
    [
        'colour = "blue"\n',
        '[python]\ntest_cmd = "pytest"\n',
        '[ci.setup]\ndepth = 1\n',
        '[presets.python-package.python]\ntest_command = "true"\n',
        '[[version.sites]]\npath = "a"\npattern = "b"\nline = 3\n',
        '[[exceptions]]\nkey = "ci.os"\nvalue = []\nreason = "x"\nexpires = "never"\n',
    ],
)
def test_an_unknown_key_at_any_depth_fails(
    text: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, text)
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'unknown-key')
    assert findings
    assert all(finding.startswith('railform.toml: ') for finding in findings)


# --- wrong-type --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    'text',
    [
        'python = "3.11"\n',
        '[python]\nversions = "3.11"\n',
        '[python]\nversions = [3.11]\n',
        '[python]\ntest_command = ["pytest"]\n',
        '[hooks]\ndir = 1\n',
        '[changelog]\nenabled = "yes"\n',
        '[changelog]\nconsumer_affecting = "loud"\n',
        '[project]\ntype = "rust-crate"\n',
        '[ci.setup]\nfetch_depth = true\n',
        '[ci.setup]\nfetch_depth = "0"\n',
        '[branch]\npattern = "(unclosed"\n',
        '[commit]\nattribution_patterns = ["[bad"]\n',
        'session_loaded = "**/skills/**"\n',
        '[[changelog.records]]\nprefix = ""\nfile = 1\n',
        '[[changelog.records]]\nprefix = ""\n',
        'changelog.records = {"" = "CHANGELOG.md"}\n',
        '[[version.sites]]\npath = "a"\npattern = "(.+)"\nauthored = "yes"\n',
        '[[version.sites]]\npath = "a"\n',
        'exceptions = {key = "a"}\n',
        '[[exceptions]]\nkey = 1\nvalue = 1\nreason = "x"\n',
    ],
)
def test_a_value_of_the_wrong_type_fails(
    text: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, text)
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'wrong-type')
    assert findings


def test_a_type_error_is_never_reported_as_a_departure(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, '[python]\nversions = "3.11"\ntest_command = "true"\n')
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'wrong-type')
    assert not any('test_command' in finding for finding in findings)


def test_unknown_key_comes_before_wrong_type(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, '[python]\nversions = "3.11"\nnope = 1\n')
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'unknown-key')
    assert len(findings) == 2


def test_a_schema_error_fails_the_printing_modes_too(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, '[python]\nversions = "3.11"\n')
    assert _run(capsys, '--json')[:2] == (1, 'wrong-type')
    assert _run(capsys)[:2] == (1, 'wrong-type')


# --- bad-exception -----------------------------------------------------------------------------

_DEPARTURE = '[python]\ntest_command = "true"\n'


@pytest.mark.parametrize('reason', ['reason = ""\n', 'reason = "   \\t "\n', ''])
def test_an_exceptions_row_without_a_reason_fails(
    reason: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        _DEPARTURE + f'[[exceptions]]\nkey = "python.test_command"\nvalue = "true"\n{reason}',
    )
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'bad-exception')
    assert any('exceptions[0]' in finding and 'reason' in finding for finding in findings)


@pytest.mark.parametrize(
    'row',
    [
        'key = "ci.os"\nvalue = ["ubuntu-latest"]\n',
        'key = "python.versions"\nvalue = ["3.12"]\n',
        'key = "no.such.key"\nvalue = 1\n',
        'key = "python.test_command"\nvalue = "uv run pytest"\n',
    ],
)
def test_an_exceptions_row_that_matches_no_departure_fails(
    row: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        '[python]\nversions = ["3.12"]\n' + f'[[exceptions]]\n{row}reason = "left over"\n',
    )
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'bad-exception')
    assert any('matches no departure' in finding for finding in findings)


def test_an_exceptions_row_value_must_match_by_type_not_only_by_equality(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    depth = frame.effective_frame(None)['ci']['setup']['fetch_depth']
    other = 0 if depth else 1
    _write(
        scratch_repo,
        f'[ci.setup]\nfetch_depth = {other}\n'
        f'[[exceptions]]\nkey = "ci.setup.fetch_depth"\nvalue = {str(bool(other)).lower()}\n'
        'reason = "history"\n',
    )
    assert _check(capsys)[:2] == (1, 'bad-exception')


def test_two_rows_covering_the_same_departure_fail(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = '[[exceptions]]\nkey = "python.test_command"\nvalue = "true"\nreason = "migrating"\n'
    _write(scratch_repo, _DEPARTURE + row)
    assert _check(capsys) == (0, 'ok', [])
    _write(scratch_repo, _DEPARTURE + row + row)
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'bad-exception')
    assert findings == [
        'railform.toml: exceptions[1] (python.test_command): duplicates an earlier row that '
        'already covers this departure'
    ]


def test_bad_exception_comes_before_unlisted_departure(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(
        scratch_repo,
        _DEPARTURE + '[[exceptions]]\nkey = "ci.os"\nvalue = []\nreason = "stale"\n',
    )
    code, verdict, findings = _check(capsys)
    assert (code, verdict) == (1, 'bad-exception')
    assert any('python.test_command' in finding for finding in findings)


# --- --github-output ---------------------------------------------------------------------------


def _outputs(path: Path) -> dict[str, str]:
    outputs: dict[str, str] = {}
    lines = iter(path.read_text(encoding='utf-8').splitlines())
    for line in lines:
        if '<<' in line and '=' not in line.split('<<', 1)[0]:
            name, delimiter = line.split('<<', 1)
            body = []
            for inner in lines:
                if inner == delimiter:
                    break
                body.append(inner)
            outputs[name] = '\n'.join(body)
        else:
            name, value = line.split('=', 1)
            outputs[name] = value
    return outputs


def test_github_output_appends_name_value_lines(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / 'github_output'
    target.write_text('earlier=kept\n', encoding='utf-8')
    monkeypatch.setenv('GITHUB_OUTPUT', str(target))
    _write(scratch_repo, '[project]\ntype = "python-scripts"\n[python]\nversions = ["3.12"]\n')
    assert _run(capsys, '--github-output')[:2] == (0, 'ok')
    text = target.read_text(encoding='utf-8')
    assert text.startswith('earlier=kept\n')
    assert text.endswith('\n')
    outputs = _outputs(target)
    effective = frame.effective_frame(
        {'project': {'type': 'python-scripts'}, 'python': {'versions': ['3.12']}}
    )
    assert outputs['earlier'] == 'kept'
    assert outputs['project_type'] == 'python-scripts'
    assert outputs['python_versions'] == '["3.12"]'
    assert outputs['python_test_command'] == effective['python']['test_command']
    assert outputs['python_type_check'] == ''
    assert json.loads(outputs['ci_os']) == effective['ci']['os']
    assert json.loads(outputs['ci_setup']) == effective['ci']['setup']
    assert json.loads(outputs['python_sync_args']) == effective['python']['sync_args']
    assert json.loads(outputs['python_lint_paths']) == ['.']
    assert outputs['changelog_enabled'] == 'true'
    for name, value in outputs.items():
        assert re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', name), name
        if value.startswith(('[', '{')):
            assert ', ' not in value and ': ' not in value, f'{name} is not compact JSON'


def test_github_output_writes_a_multi_line_value_with_a_delimiter(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / 'github_output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(target))
    _write(
        scratch_repo,
        '[python]\ntest_command = "pytest\\nother=1"\n'
        '[[exceptions]]\nkey = "python.test_command"\nvalue = "pytest\\nother=1"\n'
        'reason = "two steps"\n',
    )
    assert _run(capsys, '--github-output')[:2] == (0, 'ok')
    outputs = _outputs(target)
    assert outputs['python_test_command'] == 'pytest\nother=1'
    assert 'other' not in outputs


@pytest.mark.parametrize('value', [None, ''])
def test_github_output_without_the_variable_exits_2(
    value: str | None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if value is None:
        monkeypatch.delenv('GITHUB_OUTPUT', raising=False)
    else:
        monkeypatch.setenv('GITHUB_OUTPUT', value)
    code = main(['frame', '--github-output'])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == 'railform-verdict: frame fail usage\n'
    assert 'GITHUB_OUTPUT' in captured.err


# --- the frame-value test: the verdict follows the data, not a constant -------------------------


def _patched_default(monkeypatch: pytest.MonkeyPatch, old: str, new: str) -> None:
    text = frame.default_frame_text()
    assert text.count(old) >= 1, old
    monkeypatch.setattr(frame, 'default_frame_text', lambda: text.replace(old, new))


def test_changing_the_presets_test_command_changes_the_verdict(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, '[python]\ntest_command = "make test"\n')
    assert _check(capsys)[:2] == (1, 'unlisted-departure')
    preset = frame.effective_frame(None)['python']['test_command']
    _patched_default(monkeypatch, f'test_command = "{preset}"', 'test_command = "make test"')
    assert _check(capsys) == (0, 'ok', [])


def test_the_parameter_key_list_is_read_from_the_frame(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(scratch_repo, '[python]\ntest_command = ""\n')
    assert _check(capsys)[:2] == (1, 'unlisted-departure')
    _patched_default(monkeypatch, '"python.package",', '"python.package", "python.test_command",')
    assert _check(capsys) == (0, 'ok', [])


def test_the_wrapper_version_is_read_from_the_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    version = plugins['hooks']['pre_commit_version']
    _patched_default(
        monkeypatch, f'pre_commit_version = "{version}"', 'pre_commit_version = "1.2.3"'
    )
    effective = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    assert effective['hooks']['wrapper'] == 'uvx pre-commit==1.2.3 hook-impl'


# --- commit.attribution_patterns: keel's set and keel's test cases ------------------------------
# Ported from keel (https://github.com/grimaldost/keel), tests/test_commit_msg_lane.py at
# c3313fd: the same lines, held against the frame value instead of the hook's grep.


def _attributed(line: str) -> bool:
    patterns = frame.effective_frame(None)['commit']['attribution_patterns']
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns)


@pytest.mark.parametrize(
    'line',
    [
        'Co-Authored-By: Claude <noreply@anthropic.com>',
        'Signed-off-by: Claude',
        'Assisted-by: GPT-5',
        'Generated-by: Copilot',
        'Generated with [Claude Code](https://claude.com/claude-code)',
        'Generated with Claude',
    ],
)
def test_attribution_trailers_and_stamps_are_rejected(line: str) -> None:
    assert _attributed(line), f'the frame patterns miss: {line!r}'


@pytest.mark.parametrize(
    'line',
    [
        'Generated with GPT-5',
        'Generated with [GPT](https://example.com)',
        'Generated with Anthropic tools',
        'generated with anthropic',
    ],
)
def test_red_proof_a_stamp_that_names_gpt_or_anthropic_is_rejected(line: str) -> None:
    # The development-rails spec, §13 (the craft-collection pilot): the "generated with" stamp
    # arm names GPT and Anthropic as well as Claude, as craft-collection's own check did.
    assert _attributed(line), f'the frame patterns miss: {line!r}'


@pytest.mark.parametrize(
    'line',
    [
        'fix(release): hold the tag to its section',
        'The fold follows ADR-0018; co-authored the spec with the operator.',
        'Generated fixtures with the staging script.',
        'Signed-off-by: Alice Example',
    ],
)
def test_an_ordinary_message_passes(line: str) -> None:
    assert not _attributed(line), f'the frame patterns false-fire on: {line!r}'


def test_the_patterns_hold_in_every_preset() -> None:
    expected = frame.effective_frame(None)['commit']['attribution_patterns']
    for preset in PRESETS:
        effective = frame.effective_frame({'project': {'type': preset}})
        assert effective['commit']['attribution_patterns'] == expected


# --- the module entry point -------------------------------------------------------------------


def test_the_module_entry_point_checks_the_frame(scratch_repo: Path) -> None:
    _write(scratch_repo, '[python]\ntest_command = "true"\n')
    done = subprocess.run(
        [sys.executable, '-m', 'railform', 'frame', '--check'],
        capture_output=True,
        text=True,
        check=False,
        cwd=scratch_repo,
    )
    assert done.returncode == 1, done.stderr
    assert done.stdout.splitlines()[-1] == 'railform-verdict: frame fail unlisted-departure'


def test_the_default_frame_is_parsed_once_and_never_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame.effective_frame(None)
    calls: list[str] = []
    real = frame.tomllib.loads
    monkeypatch.setattr(frame.tomllib, 'loads', lambda text: calls.append(text) or real(text))
    first = frame.effective_frame(None)
    first['commit']['types'].append('mutated')
    first['changelog']['records'][0]['file'] = 'mutated'
    second = frame.effective_frame(None)
    assert 'mutated' not in second['commit']['types']
    assert second['changelog']['records'][0]['file'] == 'CHANGELOG.md'
    assert calls == []
