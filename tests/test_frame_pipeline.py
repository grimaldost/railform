"""The type's pipeline, built from the frame (development-rails spec, §9 and design decision 3).

`railform frame --github-output` writes it as the output `pipeline`, one command per line, and
the `checks` job of the reusable workflow runs those lines in order: the workflow names no step
of its own. These tests pin each type's commands and show that a change of a frame value
changes them, so no step is a constant in the workflow or in the code.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from railform import frame
from railform.cli import main

if TYPE_CHECKING:
    from pathlib import Path

PACKAGE = [
    'uv lock --check',
    'uv sync --frozen',
    'uv run --frozen ruff format --check .',
    'uv run --frozen ruff check .',
    'uv run --frozen ty check src',
    'uv run --frozen python -m pytest -q',
]


def _pipeline(**tables: dict[str, Any]) -> list[str]:
    return frame.pipeline(frame.effective_frame(dict(tables)))


def test_python_package_runs_the_lock_check_sync_format_lint_ty_and_tests() -> None:
    assert _pipeline() == PACKAGE
    assert _pipeline(project={'type': 'python-package'}) == PACKAGE


def test_python_scripts_runs_the_same_without_ty() -> None:
    assert _pipeline(project={'type': 'python-scripts'}) == [
        step for step in PACKAGE if 'ty check' not in step
    ]


def test_plugin_marketplace_runs_ruff_from_uvx_at_its_pin_and_its_test_command() -> None:
    preset = frame.preset_frame('plugin-marketplace')
    ruff = f'uvx ruff=={preset["ci"]["ruff_version"]}'
    assert _pipeline(project={'type': 'plugin-marketplace'}) == [
        f'{ruff} format --check .',
        f'{ruff} check .',
        preset['python']['test_command'],
    ]


@pytest.mark.parametrize(
    ('tables', 'expected'),
    [
        ({'python': {'test_command': 'uv run --frozen pytest -x'}}, 'uv run --frozen pytest -x'),
        ({'python': {'type_check': 'src/pkg'}}, 'uv run --frozen ty check src/pkg'),
        (
            {'python': {'lint_paths': ['src', 'my tests']}},
            "uv run --frozen ruff check src 'my tests'",
        ),
        ({'python': {'sync_args': ['--group', 'docs']}}, 'uv sync --frozen --group docs'),
        (
            {'ci': {'setup': {'extra_installs': ['coverage>=7']}}},
            "uv pip install 'coverage>=7'",
        ),
        (
            {'project': {'type': 'plugin-marketplace'}, 'ci': {'ruff_version': '0.15.0'}},
            'uvx ruff==0.15.0 check .',
        ),
    ],
)
def test_red_proof_a_frame_value_changes_the_pipeline(
    tables: dict[str, dict[str, Any]], expected: str
) -> None:
    # The red proof against hard-coding: the step follows the frame value.
    assert expected not in _pipeline(project=tables.get('project', {}))
    assert expected in _pipeline(**tables)


def test_extra_installs_come_right_after_the_sync() -> None:
    steps = _pipeline(ci={'setup': {'extra_installs': ['a==1', 'b']}})
    assert steps[1:3] == ['uv sync --frozen', 'uv pip install a==1 b']


def test_an_empty_type_check_runs_no_ty() -> None:
    assert not any('ty check' in step for step in _pipeline(python={'type_check': ''}))


def test_a_test_command_of_several_lines_is_one_command_per_line() -> None:
    steps = _pipeline(
        python={'test_command': 'uv run --frozen pytest -q\n\n  \nbash scripts/x.sh\n'}
    )
    assert steps[-2:] == ['uv run --frozen pytest -q', 'bash scripts/x.sh']


def test_an_empty_test_command_runs_no_test_step() -> None:
    assert _pipeline(python={'test_command': ''}) == PACKAGE[:-1]


@pytest.mark.parametrize(
    'tables',
    [
        {'project': {'type': 'plugin-marketplace'}, 'ci': {'ruff_version': ''}},
        {'project': {'type': 'plugin-marketplace'}, 'ci': {'setup': {'extra_installs': ['x']}}},
    ],
)
def test_a_step_that_cannot_be_written_is_a_frame_error(tables: dict[str, Any]) -> None:
    with pytest.raises(frame.FrameError):
        _pipeline(**tables)


# --- through the verb --------------------------------------------------------------------------


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


def test_github_output_writes_the_pipeline_one_command_per_line(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / 'github_output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(target))
    (scratch_repo / 'railform.toml').write_text(
        '[project]\ntype = "python-scripts"\n', encoding='utf-8'
    )
    assert main(['frame', '--github-output']) == 0
    capsys.readouterr()
    outputs = _outputs(target)
    effective = frame.load(scratch_repo)
    assert outputs['pipeline'].splitlines() == frame.pipeline(effective)
    # The matrix and the checkout depth the workflow reads, beside it.
    assert json.loads(outputs['ci_os']) == effective['ci']['os']
    assert json.loads(outputs['python_versions']) == effective['python']['versions']
    assert json.loads(outputs['ci_setup'])['fetch_depth'] == 1


def test_github_output_exits_2_when_the_pipeline_cannot_be_written(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / 'github_output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(target))
    (scratch_repo / 'railform.toml').write_text(
        '[project]\ntype = "plugin-marketplace"\n\n[ci]\nruff_version = ""\n\n'
        '[[exceptions]]\nkey = "ci.ruff_version"\nvalue = ""\nreason = "no ruff"\n',
        encoding='utf-8',
    )
    assert main(['frame', '--check']) == 0
    assert main(['frame', '--github-output']) == 2
    captured = capsys.readouterr()
    assert captured.out.splitlines()[-1] == 'railform-verdict: frame fail usage'
    assert 'ci.ruff_version' in captured.err
    # Nothing is written when the pipeline cannot be.
    assert not target.exists() or target.read_text(encoding='utf-8') == ''


# --- the paths of the type check ---------------------------------------------------------------


def _ty(**tables: dict[str, Any]) -> list[str]:
    return [step for step in _pipeline(**tables) if 'ty check' in step]


def test_a_type_check_of_two_paths_in_one_string_is_two_arguments() -> None:
    # The value acme-tool wrote before the move to railform: it must not become one quoted
    # argument, which `ty` would take for a single path that does not exist.
    assert _ty(python={'type_check': 'acme_tool tool.py'}) == [
        'uv run --frozen ty check acme_tool tool.py'
    ]


def test_a_type_check_list_is_one_argument_per_path() -> None:
    assert _ty(python={'type_check': ['acme_tool', 'tool.py']}) == [
        'uv run --frozen ty check acme_tool tool.py'
    ]
    assert _ty(python={'type_check': ['my pkg', 'x.py']}) == [
        "uv run --frozen ty check 'my pkg' x.py"
    ]


@pytest.mark.parametrize('value', ['src', '.', ['src'], ['.']])
def test_the_string_and_list_forms_of_one_path_give_one_line(value: str | list[str]) -> None:
    path = value if isinstance(value, str) else value[0]
    assert _ty(python={'type_check': value}) == [f'uv run --frozen ty check {path}']


def test_an_empty_type_check_list_runs_no_ty() -> None:
    assert _ty(python={'type_check': []}) == []


def test_a_flat_layout_python_package_type_checks_its_package() -> None:
    flat = {'project': {'layout': 'flat'}, 'python': {'package': 'acme_tool'}}
    assert _ty(**flat) == ['uv run --frozen ty check acme_tool']
    # Red proof: without the layout the preset's src stays.
    assert _ty(python={'package': 'acme_tool'}) == ['uv run --frozen ty check src']
    # A flat layout with no package has nothing to follow.
    assert _ty(project={'layout': 'flat'}) == ['uv run --frozen ty check src']
    # An explicit type_check is never overridden by the package.
    explicit = {'package': 'acme_tool', 'type_check': 'x'}
    assert _ty(project={'layout': 'flat'}, python=explicit) == ['uv run --frozen ty check x']


def test_acme_tool_frame_end_to_end(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The acceptance of the move: acme-tool's own value, through the verb the workflow runs.
    (scratch_repo / 'acme_tool').mkdir()
    (scratch_repo / 'acme_tool' / '__init__.py').write_text('', encoding='utf-8')
    (scratch_repo / 'tool.py').write_text('', encoding='utf-8')
    config = (
        '[project]\ntype = "python-package"\nlayout = "flat"\n[python]\npackage = "acme_tool"\n'
    )
    row = '[[exceptions]]\nkey = "python.type_check"\nreason = "a script beside the package"\n'
    target = tmp_path / 'github_output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(target))

    def run(text: str) -> list[str]:
        (scratch_repo / 'railform.toml').write_text(text, encoding='utf-8')
        target.unlink(missing_ok=True)
        assert main(['frame', '--check']) == 0
        assert main(['frame', '--github-output']) == 0
        capsys.readouterr()
        return [s for s in _outputs(target)['pipeline'].splitlines() if 'ty check' in s]

    # No type_check and no exceptions row: the package is followed.
    assert run(config) == ['uv run --frozen ty check acme_tool']
    # The original string value, with the row that declares it, and the list form.
    both = 'uv run --frozen ty check acme_tool tool.py'
    string = config.replace('[python]\n', '[python]\ntype_check = "acme_tool tool.py"\n')
    assert run(string + row) == [both]
    listed = config.replace('[python]\n', '[python]\ntype_check = ["acme_tool", "tool.py"]\n')
    assert run(listed + row) == [both]
