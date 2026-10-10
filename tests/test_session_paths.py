"""The D1 guard (development-rails spec, §6; interface contract, section 8).

Each case builds a fixture repository with a base commit and a head commit and runs the verb on
the range. The red proofs of §6 are the tests named `test_red_proof_*`.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from typing import TYPE_CHECKING

import pytest

from railform import frame, session_paths
from railform.cli import main

if TYPE_CHECKING:
    from pathlib import Path

VERDICT = re.compile(r'railform-verdict: session-paths (?P<status>pass|fail) (?P<code>[a-z0-9-]+)')

IDENT = ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.com')
SKILLS_GLOB = '**/skills/**'


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ['git', *IDENT, '-c', 'commit.gpgsign=false', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _write(repo: Path, name: str, text: str = 'x\n') -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def _commit(repo: Path, message: str = 'step') -> str:
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '--allow-empty', '-m', message)
    return _git(repo, 'rev-parse', 'HEAD')


def _toml_without(glob: str) -> str:
    globs = [g for g in frame.effective_frame(None)['session_loaded'] if g != glob]
    items = ', '.join(f'"{g}"' for g in globs)
    return f'session_loaded = [{items}]\n'


def _toml_with(*extra: str) -> str:
    globs = [*frame.effective_frame(None)['session_loaded'], *extra]
    items = ', '.join('"' + g.replace('\\', '\\\\') + '"' for g in globs)
    return f'session_loaded = [{items}]\n'


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str, list[str]]:
    """Run `railform session-paths ARGS`; return the exit code, status, code and findings."""
    code = main(['session-paths', *args])
    lines = capsys.readouterr().out.splitlines()
    match = VERDICT.fullmatch(lines[-1])
    assert match is not None, lines[-1]
    return code, match['status'], match['code'], lines[:-1]


def _judge(
    capsys: pytest.CaptureFixture[str],
    base: str,
    head: str,
    *extra: str,
    frame_from: str | None = None,
) -> tuple[int, str, list[str]]:
    code, status, verdict, findings = _run(
        capsys, '--diff', f'{base}...{head}', '--frame-from', frame_from or base, *extra
    )
    assert (status == 'pass') == (code == 0)
    return code, verdict, findings


@pytest.fixture
def repo(scratch_repo: Path) -> Path:
    _write(scratch_repo, 'README.md')
    _write(scratch_repo, 'skills/review/SKILL.md', 'one\n')
    _write(scratch_repo, '.githooks/pre-commit', 'one\n')
    _write(scratch_repo, 'src/keel/templates/spec.md', 'one\n')
    _commit(scratch_repo, 'base')
    return scratch_repo


def _base(repo: Path) -> str:
    return _git(repo, 'rev-parse', 'HEAD')


# --- the red proofs of §6 ----------------------------------------------------------------------


def test_red_proof_a_nested_plugin_hook_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'plugins/x/hooks/hooks.json', '{}\n')
    head = _commit(repo)
    code, verdict, findings = _judge(capsys, base, head)
    assert (code, verdict) == (1, 'session-loaded')
    assert [f.split(':')[0] for f in findings] == ['plugins/x/hooks/hooks.json']


def test_red_proof_a_rename_out_of_skills_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    (repo / 'archive').mkdir()
    _git(repo, 'mv', 'skills/review/SKILL.md', 'archive/SKILL.md')
    head = _commit(repo)
    # The moved file is identical, so rename detection reports only the new path and the old
    # one, under skills/, is listed only by the diff without it. Asserted here so that the proof
    # cannot pass vacuously under a global diff.renames=false.
    detected = _git(repo, 'diff', '-M', '--name-only', base, head)
    assert detected.splitlines() == ['archive/SKILL.md']
    code, verdict, findings = _judge(capsys, base, head)
    assert (code, verdict) == (1, 'session-loaded')
    assert [f.split(':')[0] for f in findings] == ['skills/review/SKILL.md']


def test_red_proof_a_change_under_keel_templates_fails_with_keels_extra_glob(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # keel has no railform.toml yet: the series brief supplies the glob.
    assert not (repo / 'railform.toml').exists()
    base = _base(repo)
    _write(repo, 'src/keel/templates/spec.md', 'two\n')
    head = _commit(repo)
    assert _judge(capsys, base, head)[:2] == (0, 'ok')
    code, verdict, findings = _judge(capsys, base, head, '--glob', 'src/keel/templates/**')
    assert (code, verdict) == (1, 'session-loaded')
    assert [f.split(':')[0] for f in findings] == ['src/keel/templates/spec.md']


def test_red_proof_a_change_to_the_githooks_directory_passes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, '.githooks/pre-commit', 'two\n')
    _write(repo, 'tests/test_hooks.py')
    head = _commit(repo)
    assert _judge(capsys, base, head) == (0, 'ok', [])


def test_red_proof_dropping_the_skills_glob_together_with_a_skills_edit_fails(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'railform.toml', _toml_without(SKILLS_GLOB))
    _write(repo, 'skills/review/SKILL.md', 'two\n')
    head = _commit(repo)
    # Judged by the head's own frame this change would pass; the base's frame catches it.
    code, verdict, findings = _judge(capsys, base, head)
    assert code == 1
    assert verdict == 'session-loaded'
    assert any(f.startswith('skills/review/SKILL.md:') for f in findings)
    assert any(SKILLS_GLOB in f and f.startswith('railform.toml:') for f in findings)


def test_red_proof_dropping_the_skills_glob_alone_fails_guard_narrowed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'railform.toml', _toml_without(SKILLS_GLOB))
    head = _commit(repo)
    code, verdict, findings = _judge(capsys, base, head)
    assert (code, verdict) == (1, 'guard-narrowed')
    assert len(findings) == 1
    assert SKILLS_GLOB in findings[0]


def test_red_proof_an_exceptions_row_for_the_guard_fails_guard_narrowed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    kept = ['**/agents/**', '**/commands/**']
    toml = textwrap.dedent(f"""\
        session_loaded = {kept!r}

        [[exceptions]]
        key = "session_loaded"
        value = {kept!r}
        reason = "a fixture"
        """).replace("'", '"')
    _write(repo, 'railform.toml', toml)
    head = _commit(repo)
    code, verdict, findings = _judge(capsys, base, head)
    assert (code, verdict) == (1, 'guard-narrowed')
    assert any('excepts' in f for f in findings)


def test_red_proof_an_exceptions_row_alone_narrows_the_guard(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    # session_loaded itself is untouched: only the exceptions row excepts globs.
    kept = frame.effective_frame(None)['session_loaded'][1:]
    items = ', '.join(f'"{g}"' for g in kept)
    toml = f'[[exceptions]]\nkey = "session_loaded"\nvalue = [{items}]\nreason = "a fixture"\n'
    _write(repo, 'railform.toml', toml)
    head = _commit(repo)
    code, verdict, findings = _judge(capsys, base, head)
    assert (code, verdict) == (1, 'guard-narrowed')
    assert any('excepts' in f and SKILLS_GLOB in f for f in findings)


def test_red_proof_the_frame_comes_from_frame_from_never_from_the_head(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    # The head adds a glob of its own and edits a path under it: the guard is the base's, so
    # the new glob guards nothing yet and the change passes.
    _write(repo, 'railform.toml', _toml_with('extra/**'))
    _write(repo, 'extra/file.txt')
    head = _commit(repo)
    assert _judge(capsys, base, head) == (0, 'ok', [])
    # Read from the head, the same range fails.
    code, verdict, _ = _judge(capsys, base, head, frame_from=head)
    assert (code, verdict) == (1, 'session-loaded')


def test_red_proof_frame_from_is_not_tied_to_the_base(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'railform.toml', _toml_with('extra/**'))
    guard = _commit(repo)
    _git(repo, 'checkout', '-q', '-b', 'topic', base)
    _write(repo, 'extra/file.txt')
    head = _commit(repo)
    assert _judge(capsys, base, head, frame_from=base) == (0, 'ok', [])
    code, verdict, findings = _judge(capsys, base, head, frame_from=guard)
    assert (code, verdict) == (1, 'session-loaded')
    # The head also lacks the guard's extra glob, which is a narrowing finding of its own.
    assert [f.split(':')[0] for f in findings] == ['extra/file.txt', 'railform.toml']


# --- the frame value ---------------------------------------------------------------------------


def test_red_proof_the_verdict_follows_the_frame_value(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Change one frame value and the verdict changes: the globs are data, not code."""
    plain = _base(repo)
    _write(repo, 'railform.toml', _toml_with('docs/prompts/**'))
    guarded = _commit(repo)
    _write(repo, 'docs/prompts/a.md')
    head = _commit(repo)
    assert _judge(capsys, guarded, head)[:2] == (1, 'session-loaded')
    # The same change judged against the frame without the extra glob.
    assert _judge(capsys, plain, head, frame_from=plain)[:2] == (0, 'ok')


def test_red_proof_the_default_frame_supplies_the_globs(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no railform.toml at the ref, the default frame decides; editing it flips the verdict."""
    base = _base(repo)
    _write(repo, 'scripts/run.sh')
    head = _commit(repo)
    assert _judge(capsys, base, head)[:2] == (0, 'ok')
    text = frame.default_frame_text().replace(
        'session_loaded = [', 'session_loaded = [\n    "scripts/**",', 1
    )
    monkeypatch.setattr(frame, 'default_frame_text', lambda: text)
    assert _judge(capsys, base, head)[:2] == (1, 'session-loaded')


# --- more behaviour ----------------------------------------------------------------------------


def test_every_default_glob_guards_its_path(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    guarded = [
        'skills/a/SKILL.md',
        'plugins/p/agents/a.md',
        'commands/go.md',
        'plugins/p/commands/go.md',
        'hooks/hooks.json',
        'plugins/p/.claude-plugin/plugin.json',
        '.claude/settings.json',
        'CLAUDE.md',
        'sub/dir/CLAUDE.md',
        'AGENTS.md',
        'pkg/AGENTS.md',
        '.mcp.json',
    ]
    for name in guarded:
        _write(repo, name)
    head = _commit(repo)
    code, verdict, findings = _judge(capsys, base, head)
    assert (code, verdict) == (1, 'session-loaded')
    assert sorted(f.split(':')[0] for f in findings) == sorted(guarded)


def test_globs_match_anywhere_and_only_at_segment_boundaries(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    for name in ('myskills/a.md', 'skills.md', 'a/b/sub.mcp.json', 'docs/CLAUDE.md.bak'):
        _write(repo, name)
    head = _commit(repo)
    assert _judge(capsys, base, head) == (0, 'ok', [])


@pytest.mark.parametrize(
    ('glob', 'path', 'expected'),
    [
        ('**/hooks/**', 'plugins/x/hooks/hooks.json', True),
        ('**/hooks/**', 'hooks/hooks.json', True),
        ('**/hooks/**', '.githooks/pre-commit', False),
        ('.mcp.json', 'a/b/.mcp.json', True),
        ('.mcp.json', 'a/b/x.mcp.json', False),
        ('.claude/**', 'pkg/.claude/settings.json', True),
        ('src/keel/templates/**', 'src/keel/templates/a/b.md', True),
        ('src/keel/templates/**', 'x/src/keel/templates/b.md', True),
        ('src/keel/templates/**', 'src/keel/other/b.md', False),
        ('a/*/c', 'a/b/c', True),
        ('a/*/c', 'a/b/d/c', False),
        ('*.md', 'docs/guide.md', True),
        ('doc*.md', 'docs/guide.md', False),
        ('doc*.md', 'docs/docs.md', True),
        ('a.b', 'aXb', False),
        ('a+b', 'a+b', True),
        ('**/skills/**', '.claude/Skills/x/SKILL.md', True),
        ('**/CLAUDE.md', 'claude.md', True),
        ('src/Keel/*', 'SRC/keel/a.md', True),
    ],
)
def test_matching(glob: str, path: str, expected: bool) -> None:
    assert session_paths.matches(glob, path) is expected


def test_an_unchanged_range_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    assert _judge(capsys, base, base) == (0, 'ok', [])


def test_an_empty_head_side_means_head(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    _write(repo, 'skills/new.md')
    _commit(repo)
    code, _, verdict, _ = _run(capsys, '--diff', f'{base}...', '--frame-from', base)
    assert (code, verdict) == (1, 'session-loaded')


def test_a_deleted_path_counts(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    _git(repo, 'rm', '-q', 'skills/review/SKILL.md')
    head = _commit(repo)
    assert _judge(capsys, base, head)[:2] == (1, 'session-loaded')


def test_the_diff_is_taken_from_the_merge_base(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'skills/on-main.md')
    main_tip = _commit(repo, 'main moved')
    _git(repo, 'checkout', '-q', '-b', 'topic', base)
    _write(repo, 'notes.md')
    head = _commit(repo)
    # The skill file changed only on the other side of the merge base.
    assert _judge(capsys, main_tip, head) == (0, 'ok', [])


def test_extra_globs_add_and_never_remove(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    _write(repo, 'skills/a.md')
    _write(repo, 'tools/run.sh')
    head = _commit(repo)
    code, verdict, findings = _judge(capsys, base, head, '--glob', 'tools/**', '--glob', 'x/**')
    assert (code, verdict) == (1, 'session-loaded')
    assert sorted(f.split(':')[0] for f in findings) == ['skills/a.md', 'tools/run.sh']


def test_extra_globs_are_not_part_of_the_narrowing_check(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'railform.toml', 'session_loaded = ["**/skills/**"]\n')
    head = _commit(repo)
    # Dropping defaults narrows the guard; an extra glob on the command line does not hide it.
    code, verdict, _ = _judge(capsys, base, head, '--glob', '**/agents/**')
    assert (code, verdict) == (1, 'guard-narrowed')


def test_adding_a_glob_does_not_narrow(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    defaults = frame.effective_frame(None)['session_loaded']
    items = ', '.join(f'"{g}"' for g in [*defaults, 'extra/**'])
    _write(repo, 'railform.toml', f'session_loaded = [{items}]\n')
    head = _commit(repo)
    assert _judge(capsys, base, head) == (0, 'ok', [])


def test_deleting_a_railform_toml_that_added_a_glob_narrows_the_guard(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, 'railform.toml', _toml_with('extra/**'))
    guarded = _commit(repo)
    (repo / 'railform.toml').unlink()
    head = _commit(repo)
    code, verdict, findings = _judge(capsys, guarded, head)
    assert (code, verdict) == (1, 'guard-narrowed')
    assert any('extra/**' in f for f in findings)


def test_the_working_tree_is_not_read(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    _write(repo, 'notes.md')
    head = _commit(repo)
    _write(repo, 'railform.toml', _toml_without(SKILLS_GLOB))
    assert _judge(capsys, base, head) == (0, 'ok', [])


# --- usage and configuration errors ------------------------------------------------------------


@pytest.mark.parametrize(
    'argv',
    [
        [],
        ['--diff', 'a...b'],
        ['--frame-from', 'a'],
        ['--diff', 'a...b', '--frame-from', 'a', '--bogus'],
    ],
)
def test_missing_or_bad_arguments_exit_2(
    argv: list[str], repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, status, verdict, _ = _run(capsys, *argv)
    assert (code, status, verdict) == (2, 'fail', 'usage')


@pytest.mark.parametrize('value', ['abc', 'a..b', '...b', 'a...b...c'])
def test_a_diff_that_is_not_a_three_dot_range_exits_2(
    value: str, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, verdict, _ = _run(capsys, '--diff', value, '--frame-from', 'HEAD')
    assert (code, verdict) == (2, 'usage')


def test_an_unknown_ref_exits_2(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    assert _run(capsys, '--diff', f'{base}...nope', '--frame-from', base)[::2] == (2, 'usage')
    assert _run(capsys, '--diff', f'{base}...{base}', '--frame-from', 'nope')[::2] == (2, 'usage')


@pytest.mark.parametrize(
    'glob',
    [
        'src/keel/templates/',
        '/src/keel/templates/**',
        './src/keel/templates/**',
        'src/./templates/**',
        'src/../templates/**',
        'src//templates/**',
        r'src\keel\templates\**',
        '..',
        '/',
    ],
)
def test_a_malformed_extra_glob_exits_2(
    glob: str, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'src/keel/templates/spec.md', 'two\n')
    head = _commit(repo)
    argv = ['--diff', f'{base}...{head}', '--frame-from', base, '--glob', glob]
    assert _run(capsys, *argv)[::2] == (2, 'usage')


@pytest.mark.parametrize('glob', ['docs/', '/docs/**', './docs/**', r'docs\**', 'a//b'])
def test_a_malformed_glob_in_the_frame_exits_2(
    glob: str, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, 'railform.toml', _toml_with(glob))
    guarded = _commit(repo)
    assert _run(capsys, '--diff', f'{guarded}...{guarded}', '--frame-from', guarded)[::2] == (
        2,
        'usage',
    )


def test_an_empty_glob_exits_2(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = _base(repo)
    argv = ['--diff', f'{base}...{base}', '--frame-from', base, '--glob', '']
    assert _run(capsys, *argv)[::2] == (2, 'usage')


@pytest.mark.parametrize('broken', ['not = [toml', 'bogus_key = 1\n'])
def test_an_unreadable_railform_toml_at_the_ref_exits_2(
    broken: str, repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, 'railform.toml', broken)
    bad = _commit(repo)
    assert _run(capsys, '--diff', f'{bad}...{bad}', '--frame-from', bad)[::2] == (2, 'usage')


def test_an_unreadable_railform_toml_at_the_head_exits_2(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _base(repo)
    _write(repo, 'railform.toml', 'not = [toml')
    head = _commit(repo)
    assert _judge_exit(capsys, base, head) == 2


def _judge_exit(capsys: pytest.CaptureFixture[str], base: str, head: str) -> int:
    return _run(capsys, '--diff', f'{base}...{head}', '--frame-from', base)[0]


def test_outside_a_repository_exits_2(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp('plain'))
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path_factory.getbasetemp().parent))
    code, _, verdict, _ = _run(capsys, '--diff', 'a...b', '--frame-from', 'a')
    assert (code, verdict) == (2, 'usage')
