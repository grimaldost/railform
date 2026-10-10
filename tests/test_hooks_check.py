"""`railform hooks-check` and `railform arm` (development-rails spec, §5; contract section 7).

The red proofs of §5 are marked in the test names: a wrapper tracked as 100644 fails, a wrapper
running another command fails, a clone armed to an old directory fails `--armed` and passes
after `railform arm`.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from railform import frame, wrapper
from railform.cli import main

NL = chr(10)
HOOKS = ('pre-commit', 'commit-msg', 'pre-push')
UV_WRAPPER = 'uv run --frozen python -m pre_commit hook-impl'


def expected_wrapper(hook: str, command: str = UV_WRAPPER) -> str:
    """The wrapper text of contract section 7, written out here apart from the renderer."""
    return (
        '#!/bin/sh\n'
        f'# Written by railform adopt: runs the {hook} stage of .pre-commit-config.yaml.\n'
        '# `railform hooks-check` fails if this file is edited; arm a clone with `railform arm`.\n'
        'HERE="$(cd "$(dirname "$0")" && pwd)"\n'
        f'exec {command} --config=.pre-commit-config.yaml --hook-type={hook} '
        '--hook-dir "$HERE" -- "$@"\n'
    )


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def put(repo: Path, path: str, text: str, *, executable: bool = True, track: bool = True) -> None:
    """Write a wrapper with LF endings; track it with the mode 100755 or 100644."""
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode('utf-8'))
    if track:
        git(repo, 'add', '--', path)
        git(repo, 'update-index', '--chmod=+x' if executable else '--chmod=-x', '--', path)


def put_all(repo: Path, directory: str = '.githooks', names: tuple[str, ...] = HOOKS) -> None:
    for hook in names:
        put(repo, f'{directory}/{hook}', expected_wrapper(hook))


def mode_of(repo: Path, path: str) -> str:
    return git(repo, 'ls-files', '-s', '--', path).split()[0]


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own git configuration (a global core.hooksPath) out of the verdicts."""
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')


def verdict_of(out: str) -> str:
    lines = [line for line in out.splitlines() if line.startswith('railform-verdict:')]
    assert len(lines) == 1
    assert out.splitlines()[-1] == lines[0]
    return lines[0]


def check(
    capsys: pytest.CaptureFixture[str], *flags: str, verb: str = 'hooks-check'
) -> tuple[int, str, str]:
    code = main([verb, *flags])
    captured = capsys.readouterr()
    return code, verdict_of(captured.out), captured.out


def write_config(repo: Path, text: str) -> None:
    (repo / 'railform.toml').write_text(text, encoding='utf-8')


# --- the template ------------------------------------------------------------------------------


def test_the_renderer_writes_the_text_of_the_contract_for_each_wrapper_command() -> None:
    uv = frame.effective_frame({'project': {'type': 'python-scripts'}})
    for hook in HOOKS:
        assert wrapper.render(uv, hook) == expected_wrapper(hook)
    marketplace = frame.effective_frame({'project': {'type': 'plugin-marketplace'}})
    pinned = f'uvx pre-commit=={marketplace["hooks"]["pre_commit_version"]} hook-impl'
    assert re.fullmatch(r'uvx pre-commit==\d+\.\d+\.\d+ hook-impl', pinned)
    assert wrapper.render(marketplace, 'commit-msg') == expected_wrapper('commit-msg', pinned)


def test_the_renderer_ends_in_one_newline_and_has_no_carriage_return() -> None:
    text = wrapper.render(frame.effective_frame(None), 'pre-push')
    assert text.endswith('"$@"\n')
    assert not text.endswith('\n\n')
    assert '\r' not in text


# --- the checks --------------------------------------------------------------------------------


def test_correct_executable_wrappers_pass(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    code, line, out = check(capsys)
    assert (code, line) == (0, 'railform-verdict: hooks-check pass ok')
    assert out.splitlines() == [line]


def test_a_wrapper_tracked_as_100644_fails_mode(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Red proof 1 of §5.
    put_all(scratch_repo)
    put(scratch_repo, '.githooks/commit-msg', expected_wrapper('commit-msg'), executable=False)
    assert mode_of(scratch_repo, '.githooks/commit-msg') == '100644'
    code, line, out = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail mode')
    assert '.githooks/commit-msg: tracked as 100644' in out
    assert '.githooks/pre-commit' not in out


def test_a_wrapper_that_is_not_tracked_fails_mode(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    put(scratch_repo, '.githooks/pre-push', expected_wrapper('pre-push'), track=False)
    # `-f`: on Linux the index entry (100755) differs from the 0644 file the test wrote, and git
    # refuses to untrack an entry that differs from both HEAD and the file without it.
    git(scratch_repo, 'rm', '--cached', '-f', '-q', '--', '.githooks/pre-push')
    code, line, out = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail mode')
    assert 'not tracked' in out


def test_a_wrapper_that_runs_another_command_fails_content(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Red proof 2 of §5.
    put_all(scratch_repo)
    put(
        scratch_repo,
        '.githooks/pre-commit',
        expected_wrapper('pre-commit', 'python3 -m pre_commit hook-impl'),
    )
    code, line, out = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail content')
    assert '.githooks/pre-commit' in out


@pytest.mark.parametrize(
    'edit',
    [
        lambda text: text.replace('--hook-type=commit-msg', '--hook-type=pre-commit'),
        lambda text: text.replace('--config=.pre-commit-config.yaml', '--config=other.yaml'),
        lambda text: text.replace('-- "$@"', ''),
        lambda text: text.rstrip('\n'),
        lambda text: text + '\n',
        lambda text: text.replace('#!/bin/sh', '#!/bin/bash'),
        lambda text: text + 'echo done\n',
    ],
    ids=['stage', 'config', 'arguments', 'no-final-newline', 'extra-newline', 'shebang', 'extra'],
)
def test_any_edit_of_a_wrapper_fails_content(
    edit, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    put(scratch_repo, '.githooks/commit-msg', edit(expected_wrapper('commit-msg')))
    code, line, _ = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail content')


def test_a_missing_hook_fails_missing(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo, names=('pre-commit', 'commit-msg'))
    code, line, out = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail missing')
    assert '.githooks/pre-push' in out


def test_a_repository_without_the_hooks_directory_fails_missing(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, line, out = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail missing')
    assert len([ln for ln in out.splitlines() if ln.startswith('.githooks/')]) == 3


def test_a_tracked_hook_deleted_from_the_working_tree_fails_missing(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    (scratch_repo / '.githooks' / 'pre-push').unlink()
    code, line, _ = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail missing')


def test_a_crlf_copy_of_a_correct_wrapper_passes(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    crlf = expected_wrapper('pre-commit').replace('\n', '\r\n')
    (scratch_repo / '.githooks' / 'pre-commit').write_bytes(crlf.encode('utf-8'))
    code, line, _ = check(capsys)
    assert (code, line) == (0, 'railform-verdict: hooks-check pass ok')


def test_the_verdict_names_the_first_rule_that_fails_in_the_order_of_the_contract(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo, names=('pre-commit', 'commit-msg'))  # pre-push is missing
    put(scratch_repo, '.githooks/pre-commit', 'echo\n', executable=False)  # mode and content
    code, line, out = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail missing')
    assert 'tracked as 100644' in out  # the other findings are listed too
    assert 'not the wrapper' in out


def test_the_working_tree_mode_does_not_stand_in_for_the_index(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    git(scratch_repo, 'update-index', '--chmod=-x', '--', '.githooks/pre-push')
    (scratch_repo / '.githooks' / 'pre-push').chmod(0o755)
    code, line, _ = check(capsys)
    assert (code, line) == (1, 'railform-verdict: hooks-check fail mode')


# --- the frame ---------------------------------------------------------------------------------


def test_changing_hooks_dir_in_the_frame_changes_the_verdict(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')
    write_config(scratch_repo, '[hooks]\ndir = "tools/hooks"\n')
    assert check(capsys)[:2] == (1, 'railform-verdict: hooks-check fail missing')
    put_all(scratch_repo, directory='tools/hooks')
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_changing_hooks_names_in_the_frame_changes_the_verdict(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    write_config(scratch_repo, '[hooks]\nnames = ["pre-commit", "post-merge"]\n')
    assert check(capsys)[:2] == (1, 'railform-verdict: hooks-check fail missing')
    write_config(scratch_repo, '[hooks]\nnames = ["pre-commit"]\n')
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_changing_the_project_type_changes_the_wrapper_command(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    write_config(scratch_repo, '[project]\ntype = "plugin-marketplace"\n')
    assert check(capsys)[:2] == (1, 'railform-verdict: hooks-check fail content')
    pinned = wrapper.command(frame.load(scratch_repo))
    assert re.fullmatch(r'uvx pre-commit==\S+ hook-impl', pinned)
    for hook in HOOKS:
        put(scratch_repo, f'.githooks/{hook}', expected_wrapper(hook, pinned))
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_changing_hooks_wrapper_in_the_frame_changes_the_verdict(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    write_config(scratch_repo, '[hooks]\nwrapper = "pre-commit hook-impl"\n')
    assert check(capsys)[:2] == (1, 'railform-verdict: hooks-check fail content')
    for hook in HOOKS:
        put(scratch_repo, f'.githooks/{hook}', expected_wrapper(hook, 'pre-commit hook-impl'))
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')


# --- a wrapper that pins no version ------------------------------------------------------------

UNPINNED = '[hooks]\nwrapper = "uvx pre-commit=={hooks.pre_commit_version} hook-impl"\n'


def test_the_renderer_refuses_a_placeholder_with_no_version() -> None:
    config = {'hooks': {'wrapper': 'uvx pre-commit=={hooks.pre_commit_version} hook-impl'}}
    unpinned = frame.effective_frame(config)
    assert unpinned['hooks']['pre_commit_version'] == ''
    with pytest.raises(wrapper.WrapperError, match='no version'):
        wrapper.render(unpinned, 'pre-commit')


@pytest.mark.parametrize('command', ['', '   ', 'uvx pre-commit== hook-impl', 'a\nb hook-impl'])
def test_the_renderer_refuses_a_command_that_cannot_be_a_wrapper(command: str) -> None:
    broken = frame.effective_frame({'hooks': {'wrapper': command}})
    with pytest.raises(wrapper.WrapperError):
        wrapper.render(broken, 'pre-commit')


def test_hooks_check_reports_a_placeholder_with_no_version_as_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    # The tracked wrappers carry the unpinned text a naive renderer would write.
    for hook in HOOKS:
        put(scratch_repo, f'.githooks/{hook}', expected_wrapper(hook, 'uvx pre-commit== hook-impl'))
    write_config(scratch_repo, UNPINNED)
    code, line, _ = check(capsys)
    assert (code, line) == (2, 'railform-verdict: hooks-check fail usage')
    assert check(capsys, '--armed')[:2] == (2, 'railform-verdict: hooks-check fail usage')


def test_a_placeholder_with_a_version_set_renders_the_pin(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(scratch_repo, UNPINNED + 'pre_commit_version = "4.0.1"\n')
    for hook in HOOKS:
        put(
            scratch_repo,
            f'.githooks/{hook}',
            expected_wrapper(hook, 'uvx pre-commit==4.0.1 hook-impl'),
        )
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')


@pytest.mark.parametrize(
    'config',
    [
        '[hooks]\ndir = "../outside"\n',
        '[hooks]\ndir = "/etc/hooks"\n',
        '[hooks]\ndir = ""\n',
        '[hooks]\nnames = ["pre-commit/../x"]\n',
        '[hooks\n',
        '[hooks]\nunknown = 1\n',
    ],
)
def test_a_frame_that_cannot_yield_wrappers_is_a_configuration_error(
    config: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(scratch_repo, config)
    assert check(capsys)[:2] == (2, 'railform-verdict: hooks-check fail usage')


@pytest.mark.parametrize(
    'config',
    [
        '[hooks]\ndir = "../outside"\n',
        '[hooks]\ndir = ""\n',
        '[hooks\n',
        '[hooks]\nunknown = 1\n',
    ],
)
def test_arm_refuses_a_frame_with_no_usable_hooks_directory(
    config: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(scratch_repo, config)
    assert check(capsys, verb='arm')[:2] == (2, 'railform-verdict: arm fail usage')
    assert config_value(scratch_repo) is None


def test_outside_a_git_repository_both_verbs_exit_2(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp('plain'))
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(Path.cwd().parent))
    assert check(capsys)[:2] == (2, 'railform-verdict: hooks-check fail usage')
    assert check(capsys, verb='arm')[:2] == (2, 'railform-verdict: arm fail usage')


# --- arming ------------------------------------------------------------------------------------


def config_value(repo: Path, key: str = 'core.hooksPath') -> str | None:
    done = subprocess.run(
        ['git', 'config', '--local', '--get', key], cwd=repo, capture_output=True, text=True
    )
    return done.stdout.strip() if done.returncode == 0 else None


def test_a_clone_armed_to_an_old_directory_fails_armed_and_passes_after_arm(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Red proof 3 of §5.
    put_all(scratch_repo)
    git(scratch_repo, 'config', 'core.hooksPath', '.old-hooks')
    # Without --armed the wrappers are fine; with it the clone is not armed.
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')
    code, line, out = check(capsys, '--armed')
    assert (code, line) == (1, 'railform-verdict: hooks-check fail not-armed')
    assert '.old-hooks' in out
    code, line, _ = check(capsys, verb='arm')
    assert (code, line) == (0, 'railform-verdict: arm pass ok')
    assert config_value(scratch_repo) == '.githooks'
    assert check(capsys, '--armed')[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_a_clone_with_no_hooks_path_fails_armed(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    code, line, out = check(capsys, '--armed')
    assert (code, line) == (1, 'railform-verdict: hooks-check fail not-armed')
    assert 'unset' in out


def test_arm_follows_hooks_dir_of_the_frame(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(scratch_repo, '[hooks]\ndir = "tools/hooks"\n')
    put_all(scratch_repo, directory='tools/hooks')
    assert check(capsys, verb='arm')[:2] == (0, 'railform-verdict: arm pass ok')
    assert config_value(scratch_repo) == 'tools/hooks'
    assert check(capsys, '--armed')[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_arm_is_idempotent_and_writes_only_the_local_configuration(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for _ in range(2):
        assert check(capsys, verb='arm')[:2] == (0, 'railform-verdict: arm pass ok')
    assert config_value(scratch_repo) == '.githooks'
    assert git(scratch_repo, 'config', '--get-all', 'core.hooksPath') == '.githooks'
    assert not (scratch_repo / '.githooks').exists()


def test_an_armed_path_with_a_trailing_slash_counts_as_armed(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    git(scratch_repo, 'config', 'core.hooksPath', '.githooks/')
    assert check(capsys, '--armed')[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_an_absolute_hooks_path_to_the_hooks_directory_is_not_armed(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    git(scratch_repo, 'config', 'core.hooksPath', str(scratch_repo / '.githooks'))
    assert check(capsys, '--armed')[:2] == (1, 'railform-verdict: hooks-check fail not-armed')


@pytest.mark.parametrize('directory', ['./.githooks', '.githooks/', r'tools\.\..\.githooks'])
def test_a_non_canonical_hooks_dir_names_the_same_directory(
    directory: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo)
    write_config(scratch_repo, f"[hooks]{NL}dir = '{directory}'{NL}")
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')
    assert check(capsys, verb='arm')[:2] == (0, 'railform-verdict: arm pass ok')
    assert config_value(scratch_repo) == '.githooks'
    assert check(capsys, '--armed')[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_a_backslash_hooks_dir_is_read_as_a_path(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put_all(scratch_repo, directory='tools/hooks')
    write_config(scratch_repo, f"[hooks]{NL}dir = 'tools" + chr(92) + f"hooks'{NL}")
    assert check(capsys)[:2] == (0, 'railform-verdict: hooks-check pass ok')


def test_the_repository_itself_is_not_a_hooks_directory(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(scratch_repo, f"[hooks]{NL}dir = '.'{NL}")
    assert check(capsys)[:2] == (2, 'railform-verdict: hooks-check fail usage')


def test_arm_in_a_git_worktree_arms_the_main_clone(
    scratch_repo: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    put_all(scratch_repo)
    identity = ('-c', 'user.name=alice', '-c', 'user.email=alice@example.com')
    git(scratch_repo, *identity, 'commit', '-qm', 'x')
    linked = tmp_path_factory.mktemp('linked') / 'tree'
    git(scratch_repo, 'worktree', 'add', '-q', '--detach', str(linked))
    monkeypatch.chdir(linked)
    assert check(capsys, verb='arm')[:2] == (0, 'railform-verdict: arm pass ok')
    assert check(capsys, '--armed')[:2] == (0, 'railform-verdict: hooks-check pass ok')
    monkeypatch.chdir(scratch_repo)
    assert check(capsys, '--armed')[:2] == (0, 'railform-verdict: hooks-check pass ok')
