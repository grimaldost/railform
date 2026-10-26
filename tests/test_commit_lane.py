"""The commit lane: `commit-msg`, `commit-range` and `pr-text` and the logic they share.

The attribution cases are ported from keel's `tests/test_commit_msg_lane.py` at c3313fd (run here
over the frame's pattern set) and from mantis-research's
`tests/unit/test_commit_message_gate.py` at bbcbc6c. The model names in them are adversarial
fixtures for the gate, not attributions.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from railform import commit_lane, frame
from railform.cli import main

ATTRIBUTED_TRAILER = 'Co-Authored-By: Claude <noreply@anthropic.com>'
GENERATION_STAMP = 'Generated with [Claude Code](https://claude.com/claude-code)'
SCISSORS = '# ------------------------ >8 ------------------------'
GIT_ENV = {
    'GIT_AUTHOR_NAME': 'Alice Example',
    'GIT_AUTHOR_EMAIL': 'alice@example.com',
    'GIT_COMMITTER_NAME': 'Alice Example',
    'GIT_COMMITTER_EMAIL': 'alice@example.com',
}
VERDICT = re.compile(r'railform-verdict: (?P<verb>\S+) (?P<status>pass|fail) (?P<code>\S+)')


# --- helpers -----------------------------------------------------------------------------------


def git(*args: str) -> str:
    done = subprocess.run(
        ['git', '-c', 'commit.gpgsign=false', *args],
        capture_output=True,
        text=True,
        encoding='utf-8',
        check=True,
        env={**os.environ, **GIT_ENV},
    )
    return done.stdout.strip()


def commit(message: str, name: str = 'COMMIT_MSG') -> str:
    """An empty commit with exactly `message` (hooks off); returns its hash."""
    Path(name).write_text(message, encoding='utf-8')
    git('commit', '--allow-empty', '--no-verify', '--cleanup=verbatim', '-F', name)
    Path(name).unlink()
    return git('rev-parse', 'HEAD')


def write_event(path: Path, pull_request: object) -> Path:
    path.write_text(json.dumps({'pull_request': pull_request}), encoding='utf-8')
    return path


def run_verb(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, list[str], tuple[str, str]]:
    """Run a verb; returns the exit code, its findings and the (status, code) of its verdict."""
    code = main(list(argv))
    lines = capsys.readouterr().out.splitlines()
    match = VERDICT.fullmatch(lines[-1])
    assert match is not None, lines
    assert match['verb'] == argv[0]
    assert sum(1 for line in lines if line.startswith('railform-verdict:')) == 1
    return code, lines[:-1], (match['status'], match['code'])


def message_file(text: str, name: str = 'msg.txt') -> str:
    Path(name).write_text(text, encoding='utf-8')
    return name


@pytest.fixture
def history(scratch_repo: Path) -> Path:
    """A scratch repository with one conventional commit tagged `base`."""
    commit('chore: start the history')
    git('tag', 'base')
    return scratch_repo


def rules() -> commit_lane.Rules:
    return commit_lane.rules_from_frame(frame.effective_frame(None))


# --- the attribution patterns, ported from keel -------------------------------------------------

# The two body lines of craft-collection #165 that failed pr-text, verbatim.
CRAFT_COLLECTION_165_LINES = (
    '  - The local `no-ai-attribution` hook stays, because it also rejects a "Generated with"'
    ' stamp that names GPT or Anthropic.',
    '5. **The default `commit.attribution_patterns` names only Claude in its "Generated with"'
    ' stamp arm.** Adding the GPT and Anthropic arm would let craft-collection retire'
    ' `scripts/check_commit_msg.py`.',
)

REJECTED = (
    'Co-Authored-By: Claude <noreply@anthropic.com>',
    'Signed-off-by: Claude',
    'Assisted-by: GPT-5',
    'Generated-by: Copilot',
    'Generated with [Claude Code](https://claude.com/claude-code)',
    'Generated with Claude',
    'Generated with ChatGPT',
    'Generated with [ChatGPT](https://chatgpt.com)',
    'Generated with GitHub Copilot',
    'Generated with Copilot',
    'Generated with GPT-5',
    'Co-authored-by: GitHub Copilot <copilot@github.com>',
    'Co-authored-by: ChatGPT <noreply@openai.com>',
    'Assisted-by: Anthropic',
    '🤖 Generated with [Claude Code](https://claude.com/claude-code)',
    '> Generated with Claude',
    '- Generated with Claude',
    '* Generated with [Claude Code](https://claude.com/claude-code)',
    '> - Generated with Copilot',
    '**Generated with Claude**',
    '1. Generated with Claude',
)
ORDINARY = (
    'fix(release): hold the tag to its section',
    'The fold follows ADR-0018; co-authored the spec with the operator.',
    'Generated fixtures with the staging script.',
    'Signed-off-by: Alice Example',
    'Generated with the sync script from CLAUDE.md.',
    'Generated with scripts/render.py; the rules live in claude.md',
    'Signed-off-by: Alice Example (reviewed CLAUDE.md)',
    'Co-authored-by: Bob Example <bob@gptools.example>',
    'Generated with gptools',
    'The stamp rule rejects a line that says Generated with followed by Claude Code.',
    'A line such as "Generated with Claude" is what the stamp arm looks for; Copilot too.',
    'The arm that matches Generated with and a product name (Anthropic, GPT) is anchored.',
    *CRAFT_COLLECTION_165_LINES,
)


@pytest.mark.parametrize('line', REJECTED)
def test_commit_msg_rejects_trailers_and_stamps(
    line: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = message_file(f'feat: add a thing\n\n{line}\n')
    code, findings, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert findings == [f'msg.txt:3: attribution line: {line}']


@pytest.mark.parametrize('line', ORDINARY)
def test_commit_msg_passes_an_ordinary_line(line: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = message_file(f'feat: add a thing\n\n{line}\n')
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))


def test_an_attributed_subject_line_fails_too(capsys: pytest.CaptureFixture[str]) -> None:
    path = message_file('feat: Generated with Claude\n')
    code, _, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'attribution'))


# --- the same cases, ported from mantis-research ----------------------------------------


def test_the_co_author_trailer_is_the_one_line_reported() -> None:
    message = f'feat: add a thing\n\n{ATTRIBUTED_TRAILER}\n'
    lines = commit_lane.numbered_lines(message)
    assert commit_lane.attribution_findings(lines, rules()) == [
        (3, f'attribution line: {ATTRIBUTED_TRAILER}')
    ]


def test_a_standalone_generated_with_line_is_reported() -> None:
    lines = commit_lane.numbered_lines('feat: add a thing\n\nGenerated with Claude Code\n')
    assert commit_lane.attribution_findings(lines, rules()) == [
        (3, 'attribution line: Generated with Claude Code')
    ]


def test_the_generation_stamp_with_a_link_is_reported() -> None:
    lines = commit_lane.numbered_lines(f'feat: add a thing\n\n{GENERATION_STAMP}\n')
    assert commit_lane.attribution_findings(lines, rules()) == [
        (3, f'attribution line: {GENERATION_STAMP}')
    ]


def test_naming_the_claude_cli_as_subject_matter_is_not_attribution() -> None:
    message = 'fix(adapter): retry the claude CLI on rate limit\n\nThe adapter now waits.\n'
    lines = commit_lane.numbered_lines(message)
    assert commit_lane.attribution_findings(lines, rules()) == []


def test_a_human_co_author_passes() -> None:
    message = 'feat: add a thing\n\nCo-Authored-By: Ada Lovelace <ada@example.com>\n'
    lines = commit_lane.numbered_lines(message)
    assert commit_lane.attribution_findings(lines, rules()) == []


def test_each_offending_line_is_reported_with_its_number() -> None:
    message = f'feat: x\n\n{ATTRIBUTED_TRAILER}\nfine\n{GENERATION_STAMP}\n'
    found = commit_lane.attribution_findings(commit_lane.numbered_lines(message), rules())
    assert [number for number, _ in found] == [3, 5]


# --- the subject rule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'subject',
    [
        'feat: add a thing',
        'fix(commit-lane): handle a merge',
        'feat!: drop the old flag',
        'refactor(frame)!: rename a key',
        'release: 1.2.3',
        'docs(spec): a description that ends in a question mark?',
        'ci: x',
    ],
)
def test_a_conventional_subject_passes(subject: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = message_file(f'{subject}\n\nA body.\n')
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize(
    'subject',
    [
        'add a thing',
        'Feat: add a thing',
        'feat add a thing',
        'feat:add a thing',
        'feat: ',
        'feat():',
        'feat(): nothing',
        'wip: not a type',
        'feat(a)(b): two scopes',
        'Merge branch main',
    ],
)
def test_a_subject_outside_the_rule_fails(subject: str, capsys: pytest.CaptureFixture[str]) -> None:
    path = message_file(f'{subject}\n')
    code, findings, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'subject'))
    assert findings == [f'msg.txt:1: {commit_lane.SUBJECT_MESSAGE}']


def test_an_empty_message_fails_the_subject_rule(capsys: pytest.CaptureFixture[str]) -> None:
    path = message_file('\n\n')
    code, _, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'subject'))


def test_the_subject_is_the_first_line_that_is_not_blank(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = message_file('\n\nfeat: after two blank lines\n')
    assert run_verb(capsys, 'commit-msg', path)[0] == 0


def test_a_merge_in_progress_is_exempt_from_the_subject_rule(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (history / '.git' / 'MERGE_HEAD').write_text(git('rev-parse', 'HEAD') + '\n', encoding='utf-8')
    path = message_file("Merge branch 'topic'\n")
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))
    # The exemption is the subject rule's only: a merge message is still checked for attribution.
    path = message_file(f"Merge branch 'topic'\n\n{ATTRIBUTED_TRAILER}\n")
    code, _, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'attribution'))


def test_when_both_rules_fail_the_verdict_names_the_subject(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = message_file(f'add a thing\n\n{ATTRIBUTED_TRAILER}\n')
    code, findings, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'subject'))
    assert len(findings) == 2


# --- git comments and the scissors line ----------------------------------------------------------


def test_comment_lines_are_ignored(capsys: pytest.CaptureFixture[str]) -> None:
    text = f'feat: add a thing\n\n# {ATTRIBUTED_TRAILER}\n#{GENERATION_STAMP}\nA body.\n'
    path = message_file(text)
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))


def test_a_comment_before_the_subject_is_not_the_subject(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = message_file('# Please enter the commit message\nfeat: add a thing\n')
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))


def test_everything_after_the_scissors_line_is_ignored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    diff = f'diff --git a/x b/x\n+{ATTRIBUTED_TRAILER}\n+Generated with Claude\n'
    path = message_file(
        f'feat: add a thing\n\n{SCISSORS}\n# Do not modify or remove the line.\n{diff}'
    )
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))


def test_the_scissors_line_hides_nothing_above_it(capsys: pytest.CaptureFixture[str]) -> None:
    path = message_file(f'feat: add a thing\n\n{ATTRIBUTED_TRAILER}\n{SCISSORS}\nrest\n')
    code, findings, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert findings == [f'msg.txt:3: attribution line: {ATTRIBUTED_TRAILER}']


def test_a_line_that_only_looks_like_the_scissors_line_hides_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    near = SCISSORS.replace('>8', '>9')
    path = message_file(f'feat: add a thing\n\n{near}\n{ATTRIBUTED_TRAILER}\n')
    assert run_verb(capsys, 'commit-msg', path)[2] == ('fail', 'attribution')


def test_the_number_of_a_finding_counts_the_comment_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = message_file(f'# a comment\nfeat: add a thing\n\n{ATTRIBUTED_TRAILER}\n')
    assert run_verb(capsys, 'commit-msg', path)[1] == [
        f'msg.txt:4: attribution line: {ATTRIBUTED_TRAILER}'
    ]


# --- commit-msg: arguments -----------------------------------------------------------------------


def test_commit_msg_reports_a_missing_file_as_usage(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, verdict = run_verb(capsys, 'commit-msg', 'no-such-file')
    assert (code, verdict) == (2, ('fail', 'usage'))


def test_commit_msg_needs_a_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_verb(capsys, 'commit-msg')[2] == ('fail', 'usage')


def test_commit_msg_names_a_file_inside_the_repository_by_its_relative_path(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / '.git' / 'COMMIT_EDITMSG').write_text(f'feat: x\n\n{GENERATION_STAMP}\n')
    code, findings, _ = run_verb(
        capsys, 'commit-msg', str(scratch_repo / '.git' / 'COMMIT_EDITMSG')
    )
    assert code == 1
    assert findings[0].startswith('.git/COMMIT_EDITMSG:3: ')


def test_commit_msg_does_not_print_the_path_of_a_file_outside_the_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / 'elsewhere.txt'
    outside.write_text('no type here\n', encoding='utf-8')
    _, findings, _ = run_verb(capsys, 'commit-msg', str(outside))
    assert findings[0].startswith('elsewhere.txt:1: ')


def test_a_verb_outside_a_git_repository_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bare_dir = tmp_path / 'not-a-repository'
    bare_dir.mkdir()
    (bare_dir / 'msg.txt').write_text('feat: x\n', encoding='utf-8')
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    monkeypatch.chdir(bare_dir)
    for argv in (['commit-msg', 'msg.txt'], ['commit-range', 'a...b'], ['pr-text', '--event', 'e']):
        code, _, verdict = run_verb(capsys, *argv)
        assert (code, verdict) == (2, ('fail', 'usage'))


# --- the frame decides --------------------------------------------------------------------------


def test_dropping_a_type_from_the_frame_makes_that_subject_fail(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = message_file('feat: add a thing\n')
    assert run_verb(capsys, 'commit-msg', path)[0] == 0
    types = [t for t in frame.effective_frame(None)['commit']['types'] if t != 'feat']
    quoted = ', '.join(json.dumps(t) for t in types)
    (scratch_repo / 'railform.toml').write_text(f'[commit]\ntypes = [{quoted}]\n', encoding='utf-8')
    code, _, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'subject'))
    # The other types still pass.
    assert run_verb(capsys, 'commit-msg', message_file('fix: add a thing\n'))[0] == 0


def test_a_pattern_added_in_the_frame_is_enforced(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    default = frame.effective_frame(None)['commit']['attribution_patterns']
    patterns = json.dumps([*default, '^Reviewed-by:'])
    (scratch_repo / 'railform.toml').write_text(
        f'[commit]\nattribution_patterns = {patterns}\n', encoding='utf-8'
    )
    path = message_file('feat: x\n\nreviewed-by: someone\n')
    assert run_verb(capsys, 'commit-msg', path)[2] == ('fail', 'attribution')


def test_a_pattern_that_is_not_a_regex_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'railform.toml').write_text(
        "[commit]\nattribution_patterns = ['(unclosed']\n", encoding='utf-8'
    )
    code, _, verdict = run_verb(capsys, 'commit-msg', message_file('feat: x\n'))
    assert (code, verdict) == (2, ('fail', 'usage'))


def test_a_railform_toml_that_cannot_be_parsed_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'railform.toml').write_text('[commit\n', encoding='utf-8')
    code, _, verdict = run_verb(capsys, 'commit-msg', message_file('feat: x\n'))
    assert (code, verdict) == (2, ('fail', 'usage'))


# --- commit-range: the red proofs of the spec, section 4 ---------------------------------------


def test_red_proof_a_range_with_an_attributed_trailer_and_a_loose_subject_fails(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    attributed = commit(f'feat: add a thing\n\n{ATTRIBUTED_TRAILER}\n')
    loose = commit('add another thing\n')
    code, findings, verdict = run_verb(capsys, 'commit-range', 'base...HEAD')
    assert code == 1
    assert verdict == ('fail', 'subject')
    assert findings == [
        f'{loose[:12]}:1: {commit_lane.SUBJECT_MESSAGE}',
        f'{attributed[:12]}:3: attribution line: {ATTRIBUTED_TRAILER}',
    ]


def test_each_half_of_that_range_fails_on_its_own_rule(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(f'feat: add a thing\n\n{ATTRIBUTED_TRAILER}\n')
    assert run_verb(capsys, 'commit-range', 'base...HEAD')[2] == ('fail', 'attribution')
    git('reset', '--hard', 'base', '-q')
    commit('add another thing\n')
    assert run_verb(capsys, 'commit-range', 'base...HEAD')[2] == ('fail', 'subject')


# --- autosquash subjects: accepted locally, refused in a range (spec §13, the keel pilot) -------


@pytest.mark.parametrize(
    'subject',
    [
        'fixup! feat: add a thing',
        'squash! fix(frame): repair it',
        'amend! docs: say more',
        'fixup! fixup! feat: add a thing',
    ],
)
def test_red_proof_commit_msg_accepts_an_autosquash_subject(
    subject: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # `git commit --fixup`, `--squash` and `--fixup=amend:` write these subjects, and
    # `git rebase --autosquash` folds them away before the branch is pushed.
    path = message_file(f'{subject}\n\nA body.\n')
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize(
    'subject',
    [
        'fixup! add a thing',
        'fixup!feat: add a thing',
        'fixup! ',
        'Fixup! feat: add a thing',
        'feat: x fixup! feat: y',
    ],
)
def test_commit_msg_still_judges_what_follows_the_autosquash_prefix(
    subject: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = message_file(f'{subject}\n')
    code, _, verdict = run_verb(capsys, 'commit-msg', path)
    if subject.startswith('feat: '):
        assert (code, verdict) == (0, ('pass', 'ok'))
    else:
        assert (code, verdict) == (1, ('fail', 'subject'))


def test_commit_msg_still_checks_an_autosquash_message_for_attribution(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = message_file(f'fixup! feat: add a thing\n\n{ATTRIBUTED_TRAILER}\n')
    code, _, verdict = run_verb(capsys, 'commit-msg', path)
    assert (code, verdict) == (1, ('fail', 'attribution'))


@pytest.mark.parametrize(
    'subject', ['fixup! feat: add a thing', 'squash! feat: add a thing', 'amend! feat: add a thing']
)
def test_red_proof_commit_range_refuses_an_autosquash_subject(
    subject: str, history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit('feat: add a thing\n')
    fixup = commit(f'{subject}\n')
    code, findings, verdict = run_verb(capsys, 'commit-range', 'base...HEAD')
    assert (code, verdict) == (1, ('fail', 'subject'))
    assert findings == [f'{fixup[:12]}:1: {commit_lane.SUBJECT_MESSAGE}']


def test_a_clean_range_passes(history: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit('feat: add a thing\n\nA body.\n')
    commit('fix(frame): repair it\n')
    assert run_verb(capsys, 'commit-range', 'base...HEAD') == (0, [], ('pass', 'ok'))


def test_an_empty_range_passes(history: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_verb(capsys, 'commit-range', 'base...HEAD') == (0, [], ('pass', 'ok'))


def _merge_topic(message: str) -> None:
    """Merge a conventional topic branch into a conventional mainline, with `message`."""
    default = git('rev-parse', '--abbrev-ref', 'HEAD')
    git('checkout', '-q', '-b', 'topic')
    commit('feat: work on the topic\n')
    git('checkout', '-q', default)
    commit('fix: work on the mainline\n')
    git('merge', '--no-ff', '--no-verify', '-m', message, 'topic')


def test_red_proof_a_generated_merge_subject_passes_the_subject_check(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _merge_topic("Merge branch 'topic'")
    assert len(git('rev-list', '--merges', 'base..HEAD').split()) == 1
    assert run_verb(capsys, 'commit-range', 'base...HEAD') == (0, [], ('pass', 'ok'))


def test_red_proof_the_same_merge_fails_when_it_carries_an_attributed_trailer(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _merge_topic(f"Merge branch 'topic'\n\n{ATTRIBUTED_TRAILER}")
    merge = git('rev-parse', 'HEAD')
    code, findings, verdict = run_verb(capsys, 'commit-range', 'base...HEAD')
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert findings == [f'{merge[:12]}:3: attribution line: {ATTRIBUTED_TRAILER}']


def test_a_loose_subject_on_a_single_parent_commit_still_fails_beside_a_merge(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _merge_topic("Merge branch 'topic'")
    commit('wip')
    assert run_verb(capsys, 'commit-range', 'base...HEAD')[2] == ('fail', 'subject')


def test_the_range_is_what_head_adds_since_the_merge_base(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A commit that only the base side has is not the branch's, whatever its message.
    default = git('rev-parse', '--abbrev-ref', 'HEAD')
    git('checkout', '-q', '-b', 'topic')
    commit('feat: the topic\n')
    git('checkout', '-q', default)
    commit(f'not conventional\n\n{ATTRIBUTED_TRAILER}\n')
    git('tag', 'moved')
    git('checkout', '-q', 'topic')
    assert run_verb(capsys, 'commit-range', 'moved...HEAD') == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize('spec', ['base', 'base..HEAD', '...HEAD', 'base...', '', '-x...HEAD'])
def test_a_range_that_is_not_base_dots_head_exits_2(
    spec: str, history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, verdict = run_verb(capsys, 'commit-range', spec)
    assert (code, verdict) == (2, ('fail', 'usage'))


def test_an_unknown_revision_exits_2(history: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, verdict = run_verb(capsys, 'commit-range', 'no-such-ref...HEAD')
    assert (code, verdict) == (2, ('fail', 'usage'))


def test_commit_range_needs_a_range(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_verb(capsys, 'commit-range')[2] == ('fail', 'usage')


def test_commit_range_reads_every_line_of_a_message_it_does_not_strip_comments(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A stored message has had its comments stripped; a `#` line in it is text (a heading).
    commit(f'feat: a thing\n\n# {GENERATION_STAMP}\n')
    assert run_verb(capsys, 'commit-range', 'base...HEAD')[2] == ('fail', 'attribution')


# --- pr-text -----------------------------------------------------------------------------------


def test_red_proof_a_body_carrying_a_generation_stamp_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = write_event(
        tmp_path / 'event.json',
        {'title': 'feat: add a thing', 'body': f'## Summary\n\nDone.\n\n{GENERATION_STAMP}\n'},
    )
    code, findings, verdict = run_verb(capsys, 'pr-text', '--event', str(event))
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert findings == [f'pull request body:5: attribution line: {GENERATION_STAMP}']


def test_the_stamp_in_the_plain_words_of_the_spec_fails_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = write_event(tmp_path / 'e.json', {'title': 't', 'body': 'Generated with Claude Code'})
    assert run_verb(capsys, 'pr-text', '--event', str(event))[2] == ('fail', 'attribution')


PROSE_ABOUT_THE_RULE = (
    *CRAFT_COLLECTION_165_LINES,
    'The stamp rule rejects a line that says Generated with followed by Claude Code.',
    'A line such as "Generated with Claude" is what the stamp arm looks for; Copilot too.',
)


def test_red_proof_a_body_describing_the_stamp_rule_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = '## Summary\n\n' + '\n'.join(PROSE_ABOUT_THE_RULE) + '\n'
    event = write_event(tmp_path / 'e.json', {'title': 'fix: anchor the arm', 'body': body})
    assert run_verb(capsys, 'pr-text', '--event', str(event)) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize('prefix', ['', '🤖 ', '> ', '- ', '* '])
def test_the_stamp_line_fails_pr_text_behind_a_marker(
    prefix: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    line = f'{prefix}Generated with [Claude Code](https://claude.com/claude-code)'
    event = write_event(tmp_path / 'e.json', {'title': 't', 'body': f'Done.\n\n{line}\n'})
    code, findings, verdict = run_verb(capsys, 'pr-text', '--event', str(event))
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert findings == [f'pull request body:3: attribution line: {line}']


def test_an_attributed_title_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    event = write_event(tmp_path / 'e.json', {'title': 'Generated with Claude', 'body': 'ok'})
    code, findings, verdict = run_verb(capsys, 'pr-text', '--event', str(event))
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert findings == ['pull request title:1: attribution line: Generated with Claude']


def test_a_markdown_heading_in_a_body_is_text_not_a_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = write_event(tmp_path / 'e.json', {'title': 't', 'body': '# Generated with Claude'})
    assert run_verb(capsys, 'pr-text', '--event', str(event))[2] == ('fail', 'attribution')


def test_a_clean_title_and_body_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    event = write_event(
        tmp_path / 'e.json',
        {'title': 'Add the commit lane', 'body': 'Co-Authored-By: Ada Lovelace <ada@example.com>'},
    )
    # A title is not held to the subject rule, only to the attribution rule.
    assert run_verb(capsys, 'pr-text', '--event', str(event)) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize(
    'pull_request',
    [{'title': 'feat: x', 'body': None}, {'title': 'feat: x'}, {'body': None}, {}],
)
def test_a_null_or_absent_title_or_body_is_empty_text(
    pull_request: dict[str, object], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = write_event(tmp_path / 'e.json', pull_request)
    assert run_verb(capsys, 'pr-text', '--event', str(event)) == (0, [], ('pass', 'ok'))


def test_the_event_path_defaults_to_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = write_event(tmp_path / 'e.json', {'title': 't', 'body': GENERATION_STAMP})
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(event))
    assert run_verb(capsys, 'pr-text')[2] == ('fail', 'attribution')


def test_the_event_argument_wins_over_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dirty = write_event(tmp_path / 'dirty.json', {'title': 't', 'body': GENERATION_STAMP})
    clean = write_event(tmp_path / 'clean.json', {'title': 't', 'body': 'fine'})
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(dirty))
    assert run_verb(capsys, 'pr-text', '--event', str(clean))[2] == ('pass', 'ok')


def test_without_an_event_or_the_variable_it_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv('GITHUB_EVENT_PATH', raising=False)
    code, _, verdict = run_verb(capsys, 'pr-text')
    assert (code, verdict) == (2, ('fail', 'usage'))


@pytest.mark.parametrize('content', ['', 'not json', '[]', '{}', '{"pull_request": null}'])
def test_an_event_that_is_not_a_pull_request_payload_exits_2(
    content: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = tmp_path / 'e.json'
    event.write_text(content, encoding='utf-8')
    code, _, verdict = run_verb(capsys, 'pr-text', '--event', str(event))
    assert (code, verdict) == (2, ('fail', 'usage'))


def test_an_event_file_that_does_not_exist_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_verb(capsys, 'pr-text', '--event', 'missing.json')[0] == 2


def test_a_body_that_is_not_text_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = write_event(tmp_path / 'e.json', {'title': 't', 'body': 5})
    assert run_verb(capsys, 'pr-text', '--event', str(event))[0] == 2


# --- review round: frame values, git configuration and edge cases ---------------------------------

REVIEWED_BY_PATTERN = '^Reviewed-by:'


def add_reviewed_by_pattern(repo: Path) -> None:
    """A `railform.toml` that adds one attribution pattern to the default set."""
    default = frame.effective_frame(None)['commit']['attribution_patterns']
    patterns = json.dumps([*default, REVIEWED_BY_PATTERN])
    (repo / 'railform.toml').write_text(
        f'[commit]\nattribution_patterns = {patterns}\n', encoding='utf-8'
    )


def test_a_pattern_added_in_the_frame_turns_a_clean_range_into_an_attribution_failure(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit('feat: add a thing\n\nReviewed-by: someone\n')
    assert run_verb(capsys, 'commit-range', 'base...HEAD') == (0, [], ('pass', 'ok'))
    add_reviewed_by_pattern(history)
    code, _, verdict = run_verb(capsys, 'commit-range', 'base...HEAD')
    assert (code, verdict) == (1, ('fail', 'attribution'))


def test_a_pattern_added_in_the_frame_turns_a_clean_body_into_an_attribution_failure(
    scratch_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = write_event(tmp_path / 'e.json', {'title': 't', 'body': 'Reviewed-by: someone'})
    assert run_verb(capsys, 'pr-text', '--event', str(event)) == (0, [], ('pass', 'ok'))
    add_reviewed_by_pattern(scratch_repo)
    code, _, verdict = run_verb(capsys, 'pr-text', '--event', str(event))
    assert (code, verdict) == (1, ('fail', 'attribution'))


def test_commit_range_asks_git_for_no_signatures(
    history: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, ...]] = []
    real = commit_lane._git

    def spy(root: Path, *args: str):
        seen.append(args)
        return real(root, *args)

    monkeypatch.setattr(commit_lane, '_git', spy)
    commit_lane.commits_in_range(history, 'base...HEAD')
    (call,) = [args for args in seen if 'log' in args]
    assert call[:2] == ('-c', 'log.showSignature=false')
    assert '--no-show-signature' in call


def test_log_show_signature_in_the_configuration_leaves_the_range_readable(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The spy test above holds the flags; this one holds that the setting does no harm. A commit
    # with a real signature needs a key, which a test cannot assume.
    git('config', 'log.showSignature', 'true')
    commit('feat: add a thing\n')
    assert run_verb(capsys, 'commit-range', 'base...HEAD') == (0, [], ('pass', 'ok'))
    assert [len(c.sha) for c in commit_lane.commits_in_range(history, 'base...HEAD')] == [
        len(git('rev-parse', 'HEAD'))
    ]


def test_an_empty_list_of_types_is_a_configuration_error(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (scratch_repo / 'railform.toml').write_text('[commit]\ntypes = []\n', encoding='utf-8')
    code, _, verdict = run_verb(capsys, 'commit-msg', message_file(': anything\n'))
    assert (code, verdict) == (2, ('fail', 'usage'))


def test_the_comment_character_of_the_git_configuration_is_the_one_ignored(
    scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git('config', 'core.commentChar', ';')
    tail = commit_lane.SCISSORS_TAIL
    text = f'feat: add a thing\n\n; {ATTRIBUTED_TRAILER}\n# {GENERATION_STAMP}\n'
    # With `;` as the comment character, the `#` line is text and is judged.
    code, findings, verdict = run_verb(capsys, 'commit-msg', message_file(text))
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert findings == [f'msg.txt:4: attribution line: # {GENERATION_STAMP}']
    # The scissors line follows the same character.
    after = f'feat: add a thing\n\n;{tail}\n{ATTRIBUTED_TRAILER}\n'
    assert run_verb(capsys, 'commit-msg', message_file(after)) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize('setting', [None, 'auto'])
def test_without_a_comment_character_in_the_configuration_hash_is_the_marker(
    setting: str | None, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if setting is not None:
        git('config', 'core.commentChar', setting)
    assert commit_lane.comment_char(scratch_repo) == '#'
    path = message_file(f'feat: add a thing\n\n# {ATTRIBUTED_TRAILER}\n')
    assert run_verb(capsys, 'commit-msg', path) == (0, [], ('pass', 'ok'))


# --- pr-text: the form of the body -------------------------------------------------------------

UNCLOSED_FENCE_MESSAGE = 'a code fence opens on this line and is never closed'
CARRIAGE_RETURN_MESSAGE = 'a carriage return not followed by a line feed'
FENCE_FAIL = ('fail', 'unclosed-fence')


def body_verdict(
    body: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], title: str = 'feat: x'
) -> tuple[int, list[str], tuple[str, str]]:
    event = write_event(tmp_path / 'event.json', {'title': title, 'body': body})
    return run_verb(capsys, 'pr-text', '--event', str(event))


def test_red_proof_a_body_that_opens_a_fence_on_its_first_line_and_never_closes_it_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, findings, verdict = body_verdict('```python\nprint(1)\n\nDone.\n', tmp_path, capsys)
    assert (code, verdict) == (1, ('fail', 'unclosed-fence'))
    assert findings == [f'pull request body:1: unclosed-fence: {UNCLOSED_FENCE_MESSAGE}']


def test_red_proof_a_tilde_fence_followed_only_by_a_backtick_line_of_its_length_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, findings, verdict = body_verdict('~~~\ncode\n```\n', tmp_path, capsys)
    assert (code, verdict) == (1, ('fail', 'unclosed-fence'))
    assert findings == [f'pull request body:1: unclosed-fence: {UNCLOSED_FENCE_MESSAGE}']


def test_red_proof_a_lone_carriage_return_inside_a_table_cell_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = '| name | note |\n|---|---|\n| a | first\rsecond |\n'
    code, findings, verdict = body_verdict(body, tmp_path, capsys)
    assert (code, verdict) == (1, ('fail', 'carriage-return'))
    assert findings == [f'pull request body:3: carriage-return: {CARRIAGE_RETURN_MESSAGE}']


def test_red_proof_a_body_with_both_defects_fails_as_unclosed_fence_and_lists_both(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = '```\nprint(1)\n| a | b\rc |\n'
    code, findings, verdict = body_verdict(body, tmp_path, capsys)
    assert (code, verdict) == (1, ('fail', 'unclosed-fence'))
    assert findings == [
        f'pull request body:1: unclosed-fence: {UNCLOSED_FENCE_MESSAGE}',
        f'pull request body:3: carriage-return: {CARRIAGE_RETURN_MESSAGE}',
    ]


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        # The item is empty, so the fence after the blank line is a top-level fence, closed by
        # its last line.
        pytest.param('- \n\n  ```\n---\n   ```\n', (0, [], ('pass', 'ok')), id='closed'),
        pytest.param(
            '-\n\n  ```\n',
            (1, [f'pull request body:3: unclosed-fence: {UNCLOSED_FENCE_MESSAGE}'], FENCE_FAIL),
            id='open',
        ),
    ],
)
def test_red_proof_a_list_item_that_begins_with_a_blank_line_ends_at_the_next_blank_line(
    body: str,
    expected: tuple[int, list[str], tuple[str, str]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert body_verdict(body, tmp_path, capsys) == expected


@pytest.mark.parametrize(
    'body',
    [
        pytest.param('Steps:\n2. build\n   ```\nmake\n', id='ordered-list-not-starting-at-1'),
        pytest.param('Steps:\n*\n  ```\nmake\n', id='empty-item'),
    ],
)
def test_red_proof_a_list_marker_that_cannot_interrupt_a_paragraph_starts_no_item(
    body: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The marker line continues the paragraph, so the fence after it is a top-level fence.
    code, findings, verdict = body_verdict(body, tmp_path, capsys)
    assert (code, verdict) == (1, FENCE_FAIL)
    assert findings == [f'pull request body:3: unclosed-fence: {UNCLOSED_FENCE_MESSAGE}']


@pytest.mark.parametrize(
    'body',
    [
        pytest.param('[d]: /a\\\nb\n===\n2. x\n   ```\n', id='bare-destination'),
        pytest.param('[d]: <a\\\nb>\n===\n2. x\n   ```\n', id='destination-in-angle-brackets'),
    ],
)
def test_red_proof_a_backslash_before_a_line_end_does_not_carry_a_destination_on(
    body: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A backslash escapes only punctuation, so the line end ends the destination (or, in angle
    # brackets, leaves it unclosed). `b` is then paragraph text that `===` turns into a heading,
    # and the fence sits in the list item after it.
    assert body_verdict(body, tmp_path, capsys) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize(
    'body',
    [
        pytest.param(
            '   - Steps:\n    ```\n    run\n    ```\n<br>\n```\n', id='after-indented-fence-lines'
        ),
        pytest.param('   - x\n    ```\n<t>\n```', id='after-an-indented-fence-line'),
        pytest.param(
            ' 1.  x\n    <summary>s</summary>\n<custom-tag/>\n```py\n[x]\n',
            id='after-an-indented-tag',
        ),
        pytest.param('- a\n<b>\n```\n', id='after-a-list-item'),
        pytest.param('> a\n<b>\n```\n', id='after-a-block-quote'),
    ],
)
def test_red_proof_a_lone_tag_after_a_container_it_leaves_starts_an_html_block(
    body: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The tag line continues neither the list item nor the block quote, so the paragraph it would
    # continue lazily is not its container, and a line holding only one tag starts an HTML block
    # there. The block runs to the next blank line and holds the fence line.
    assert body_verdict(body, tmp_path, capsys) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize(
    'body',
    [
        pytest.param('```\ncode\n```\n', id='closed-backticks'),
        pytest.param('~~~\ncode\n~~~\n', id='closed-tildes'),
        pytest.param('```python\ncode\n```\t\n', id='closer-followed-by-a-tab'),
        pytest.param('```\ncode\n```  \t \n', id='closer-followed-by-spaces-and-tabs'),
        pytest.param('````\n```\ninner\n```\n````\n', id='fence-inside-a-longer-fence'),
        pytest.param('~~~~\n~~~\ninner\n~~~\n~~~~\n', id='tilde-fence-inside-a-longer-fence'),
        pytest.param('````\ncode\n``````\n', id='closer-longer-than-the-opener'),
        pytest.param('```\ncode\n   ```\n', id='closer-indented-three-spaces'),
        pytest.param('   ```\ncode\n```\n', id='opener-indented-three-spaces'),
        pytest.param('```x```\n', id='inline-code-line'),
        pytest.param('Use ```x``` here.\n', id='inline-code-in-prose'),
        pytest.param('    ```\ncode\n', id='opener-indented-four-spaces'),
        pytest.param('\t```\ncode\n', id='opener-indented-by-a-tab'),
        pytest.param('```\n    ```\n```\n', id='indented-line-inside-a-block-is-content'),
        pytest.param('```\n~~~\n```\n', id='other-marker-inside-a-block-is-content'),
        pytest.param('> ```\n> code\n', id='fence-in-a-block-quote-is-not-judged'),
        pytest.param('- ```\n  code\n', id='fence-in-a-list-item-is-not-judged'),
        pytest.param('1. ```\n   code\n', id='fence-in-an-ordered-list-item-is-not-judged'),
        pytest.param('- a\n\n  ```\n  x\n', id='fence-on-a-list-item-continuation-line'),
        pytest.param(
            '- item\n\n  ```\n  code\n    ```\n', id='list-item-closer-two-columns-into-the-item'
        ),
        pytest.param('- item\n\n  ```\n  code\n  ```\n', id='closed-fence-on-a-continuation-line'),
        pytest.param('1. a\n\n   ```\n   x\n', id='fence-on-an-ordered-item-continuation-line'),
        pytest.param('- a\n  - b\n\n    ```\n    x\n', id='fence-in-a-nested-list-item'),
        pytest.param('- a\ntext\n  ```\n  x\n', id='fence-after-a-lazy-continuation-line'),
        pytest.param('> a\n>\n> ```\n> x\n', id='fence-on-a-block-quote-continuation-line'),
        pytest.param('- a\n\n      ```\n      x\n', id='indented-code-in-a-list-item'),
        pytest.param('-\n  a\n\n  ```\n  x\n', id='fence-in-an-item-whose-content-starts-below'),
        pytest.param('- - \n\n  ```\n  x\n', id='fence-in-an-item-after-its-empty-nested-item'),
        pytest.param('- a\n  2. b\n\n  ```\n  x\n', id='a-later-number-continues-the-item-text'),
        pytest.param('``\ncode\n', id='two-backticks-open-nothing'),
        pytest.param('~~\ncode\n', id='two-tildes-open-nothing'),
        pytest.param('```\na\n```\n\n```\nb\n```\n', id='two-closed-blocks'),
        pytest.param('## Summary\n\nDone.\nno fences here, only LF\n', id='line-feeds-only'),
        pytest.param('', id='empty-body'),
    ],
)
def test_a_well_formed_body_passes(
    body: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert body_verdict(body, tmp_path, capsys) == (0, [], ('pass', 'ok'))


def test_a_crlf_body_with_a_closed_fence_and_a_table_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = '## Summary\r\n\r\n| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n\r\n```sh\r\nmake\r\n```\r\n'
    assert body_verdict(body, tmp_path, capsys) == (0, [], ('pass', 'ok'))


def test_a_crlf_closer_followed_by_spaces_is_a_closer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert body_verdict('```\r\ncode\r\n``` \t\r\n', tmp_path, capsys)[2] == ('pass', 'ok')


@pytest.mark.parametrize(
    ('body', 'opening'),
    [
        pytest.param('```\ncode\n', 1, id='backticks'),
        pytest.param('~~~\ncode\n', 1, id='tildes'),
        pytest.param('Intro\n\n```sh\nmake\n', 3, id='after-prose'),
        pytest.param('```\na\n```\n\n```\nb\n', 5, id='second-block-open'),
        pytest.param('  ```\ncode\n', 1, id='opener-indented-two-spaces'),
        pytest.param('~~~ a `tick`\ncode\n', 1, id='tilde-info-string-may-hold-a-backtick'),
        pytest.param('```\ncode\n``` text\n', 1, id='closer-with-text-after-it'),
        pytest.param('````\ncode\n```\n', 1, id='closer-shorter-than-the-opener'),
        pytest.param('```\ncode\n~~~\n', 1, id='closer-of-the-other-character'),
        pytest.param('```\ncode\n    ```\n', 1, id='closer-indented-four-spaces'),
        pytest.param('```\ncode\n> ```\n', 1, id='closer-in-a-block-quote'),
        pytest.param('```\r\ncode\r\n', 1, id='crlf-body'),
        pytest.param('```', 1, id='opener-is-the-last-byte'),
        pytest.param('- a\n\n```\nx\n', 3, id='fence-after-a-list-has-ended'),
        pytest.param('- ```\n  code\n```\nx\n', 3, id='fence-after-a-list-item-fence-ended'),
        pytest.param('- a\n\n  b\n\n```\nx\n', 5, id='fence-after-an-item-with-two-paragraphs'),
        pytest.param('> a\n\n```\nx\n', 3, id='fence-after-a-block-quote-has-ended'),
        pytest.param(
            '- ```\n  code\ntext\n```\nx\n', 4, id='fence-after-text-that-ends-a-list-item-fence'
        ),
        pytest.param('- # h\ntext\n  ```\n', 3, id='fence-after-text-that-follows-an-item-heading'),
        pytest.param('text\n-\n  ```\n', 3, id='fence-after-an-underline-that-looks-like-an-item'),
        pytest.param('1. a\n2.\n\n   ```\n', 4, id='fence-after-an-empty-sibling-item'),
    ],
)
def test_a_block_still_open_at_the_end_names_the_opening_line(body: str, opening: int) -> None:
    assert commit_lane.unclosed_fence_findings(body) == [(opening, UNCLOSED_FENCE_MESSAGE)]


@pytest.mark.parametrize(
    'body',
    [
        pytest.param('<details>\n```\ncode\n</details>\n', id='details-before-a-blank-line'),
        pytest.param('<!--\n```\n-->\n', id='comment'),
        pytest.param('<!--\n```\n\nx\n-->\n', id='comment-across-a-blank-line'),
        pytest.param('<pre>\n```\n</pre>\n', id='pre'),
        pytest.param('<PRE>\n\n```\n</pre>\n', id='pre-across-a-blank-line'),
        pytest.param('<script>\n```\n</script>\n', id='script'),
        pytest.param('<style type="x">\n```\n</style>\n', id='style'),
        pytest.param('<textarea>\n```\n</textarea>\n', id='textarea'),
        pytest.param('<?php\n```\n?>\n', id='processing-instruction'),
        pytest.param('<!DOCTYPE x\n```\n>\n', id='declaration'),
        pytest.param('<![CDATA[\n```\n]]>\n', id='cdata'),
        pytest.param('<div>\n```\n\nx\n', id='block-tag-up-to-a-blank-line'),
        pytest.param('</div>\n```\n', id='closing-block-tag'),
        pytest.param('<div class="x"\n```\n', id='block-tag-left-open'),
        pytest.param('text\n<div>\n```\n', id='block-tag-interrupts-a-paragraph'),
        pytest.param('<span>\n```\n', id='lone-inline-tag'),
        pytest.param('<a href="x" title=\'y\'>\n```\n', id='lone-tag-with-attributes'),
        pytest.param('</custom-tag >\n```\n', id='lone-closing-tag'),
        pytest.param('> <div>\n> ```\n', id='in-a-block-quote'),
        pytest.param('  <!--\n```\n  -->\n', id='indented-two-spaces'),
    ],
)
def test_a_fence_line_inside_an_html_block_is_html(
    body: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert body_verdict(body, tmp_path, capsys) == (0, [], ('pass', 'ok'))


@pytest.mark.parametrize(
    ('body', 'opening'),
    [
        pytest.param('<!--\nx\n-->\n```\n', 4, id='after-a-comment-closes'),
        pytest.param('<!-- x -->\n```\n', 2, id='after-a-comment-closed-on-its-first-line'),
        pytest.param('<pre>\nx</pre>\n```\n', 3, id='after-the-end-tag-of-pre'),
        pytest.param('<!DOCTYPE html>\n```\n', 2, id='after-a-one-line-declaration'),
        pytest.param('<div>\n\n```\n', 3, id='after-the-blank-line-that-ends-a-block-tag'),
        pytest.param('<span>\n\n```\n', 3, id='after-the-blank-line-that-ends-a-lone-tag'),
        pytest.param('text\n<span>\n```\n', 3, id='a-lone-tag-does-not-interrupt-a-paragraph'),
        pytest.param('<span> text\n```\n', 2, id='a-tag-followed-by-text-is-not-a-lone-tag'),
        pytest.param('<!doctype html>\n```\n', 2, id='a-declaration-starts-with-a-capital'),
        pytest.param('<divx>\n\n```\n', 3, id='not-a-block-tag-name'),
        pytest.param('    <!--\n```\n', 2, id='indented-four-spaces-is-code'),
        pytest.param('```\n<!--\n```\n```\n', 4, id='html-inside-a-fence-is-content'),
        pytest.param('> <div>\ntext\n```\n', 3, id='a-block-quote-html-block-takes-no-lazy-line'),
        pytest.param('1. ><!--\ntext\n===\n   ~~~ a `b`\n', 4, id='after-a-list-item-comment'),
    ],
)
def test_a_fence_after_an_html_block_is_judged(body: str, opening: int) -> None:
    assert commit_lane.unclosed_fence_findings(body) == [(opening, UNCLOSED_FENCE_MESSAGE)]


@pytest.mark.parametrize(
    ('body', 'opening'),
    [
        # A paragraph made only of definitions takes no setext underline: `===` under it is
        # text, so the marker lines after it continue that text and start no item.
        pytest.param('[a]: /u\n===\n2. x\n   ```\n', 4, id='ordered-item-not-starting-at-1'),
        pytest.param('[a]: /u\n===\n-\n  ```\n', 4, id='empty-dash-item'),
        pytest.param('[a]: /u\n===\n*\n  ```\n', 4, id='empty-star-item'),
        pytest.param(
            'See the notes.\n\n[docs]: https://example.com\n===\n2. Run it:\n   ```\n',
            6,
            id='after-a-paragraph',
        ),
        pytest.param('[a]: /u "t"\n===\n2. x\n   ```\n', 4, id='with-a-title'),
        pytest.param('[a]: /u\n"t"\n===\n2. x\n   ```\n', 5, id='with-a-title-on-the-next-line'),
        pytest.param('[a]:\n/u\n===\n2. x\n   ```\n', 5, id='destination-on-the-next-line'),
        pytest.param('[a]: /u "t\nx"\n===\n2. x\n   ```\n', 5, id='title-across-two-lines'),
        pytest.param('[a]: /u\n[b]: <v>\n===\n2. x\n   ```\n', 5, id='two-definitions'),
        # Not a definition: a paragraph, which `===` underlines.
        pytest.param('[a]: /u x\n===\n2. x\n   ```\n', None, id='text-after-the-destination'),
        pytest.param('[a]\n===\n2. x\n   ```\n', None, id='no-colon'),
        pytest.param('[ ]: /u\n===\n2. x\n   ```\n', None, id='blank-label'),
        pytest.param('text\n[a]: /u\n===\n2. x\n   ```\n', None, id='inside-a-paragraph'),
    ],
)
def test_a_setext_underline_under_a_paragraph_of_definitions_is_text(
    body: str, opening: int | None
) -> None:
    expected = [] if opening is None else [(opening, UNCLOSED_FENCE_MESSAGE)]
    assert commit_lane.unclosed_fence_findings(body) == expected


@pytest.mark.parametrize(
    ('body', 'opening'),
    [
        # The line after a definition goes on with the paragraph, which is read for definitions
        # only when it ends or meets a setext underline: a marker that cannot interrupt a
        # paragraph, a lone tag or an indented line is then text, and the fence after it is
        # judged.
        pytest.param('[a]: /u\n2. x\n   ```\n', 3, id='an-ordered-item-not-starting-at-1'),
        pytest.param('[a]: /u\n-\n  ```\n', 3, id='an-empty-item'),
        pytest.param('[a]: /u\n<span>\n```\n', 3, id='a-lone-tag'),
        pytest.param('[a]: /u\n    x\n2. y\n   ```\n', 4, id='an-indented-line'),
        pytest.param('[a]: data:image/png;x\n2. y\n   ```\n', 3, id='an-image-data-url'),
        pytest.param('[d]: /a\\\n<b>\n```\n', 3, id='a-destination-ending-in-a-backslash'),
        pytest.param('[d]:\n\\\n<a>\n```', 4, id='a-backslash-as-the-destination'),
        # A lazy line after a definition in a block quote goes on with the quote's paragraph,
        # and the item after it closes the quote.
        pytest.param('> [a]: /u\nfoo\n2. x\n   ```\n', None, id='after-a-definition-in-a-quote'),
        # Definitions as the reference implementation reads them: a script URL, an unmatched `(`
        # and a backslash before a title's closing quote make one; a tab, a backslash before a
        # line end in a label and a label over 1000 characters make none.
        pytest.param('[a]: javascript:x\n===\n2. y\n   ```\n', 4, id='a-script-url'),
        pytest.param('[a]: /u(b\n===\n2. y\n   ```\n', 4, id='an-unmatched-parenthesis'),
        pytest.param('[a]: /u "a\\"\n===\n2. y\n   ```\n', 4, id='a-backslash-before-a-quote'),
        pytest.param('[a]:\t/u\n===\n2. y\n   ```\n', None, id='a-tab-after-the-colon'),
        pytest.param('[a]: /u\t\n===\n2. y\n   ```\n', None, id='a-tab-after-the-destination'),
        pytest.param('[a\\\nb]: /u\n===\n2. y\n   ```\n', None, id='a-backslash-in-a-label'),
        pytest.param(
            '[' + 'x' * 1001 + ']: /u\n===\n2. y\n   ```\n', None, id='a-label-over-1000-characters'
        ),
        # A list item whose only block is a paragraph of definitions holds nothing once that
        # paragraph ends, so a second blank line ends the item.
        pytest.param('- [d]: /a\n\n\n  ```\n', 4, id='an-item-of-definitions-only'),
        # An HTML block in a list item goes on past a blank line indented less than the item's
        # content, and ends with the item.
        pytest.param('- <!--\n\n  x\ny\n2. z\n   ```\n', 6, id='html-past-a-short-blank'),
        pytest.param('- - <!--\n   \n    x\ny\n2. z\n   ```\n', 6, id='html-in-a-nested-item'),
        pytest.param('> - <!--\n> \n>   x\ny\n2. z\n   ```\n', 6, id='html-in-an-item-in-a-quote'),
        # The block-level tag names hold h1 but not h2 to h6, and textarea is no raw-text tag.
        pytest.param('<h2>Title</h2>\n```\n', 2, id='an-h2-tag'),
        pytest.param('<textarea\n\n```\n', 3, id='textarea-starts-no-raw-text-block'),
        pytest.param('<pre>\n</textarea>\n```\n', None, id='textarea-ends-no-pre-block'),
        # A line indented four or more spaces after a container that holds an open paragraph is
        # too far in to start a block, so it goes on with that paragraph lazily.
        pytest.param('   - x\n    ---\ny\n-\n  ```\n', None, id='after-a-wide-list-item'),
        pytest.param('>x\n    >\nx\n-\n  ```\n', None, id='after-a-block-quote'),
        # After a block quote that holds no paragraph, such a line is indented code outside the
        # quote: nothing is open for it to continue lazily.
        pytest.param(
            '>\n    > x\ny\n2. z\n   ~~~\n', 5, id='after-a-block-quote-without-a-paragraph'
        ),
        # An ordered marker interrupts a paragraph only when its number is written `1`.
        pytest.param('x\n01. y\n-\n  ```\n', 4, id='a-number-with-a-leading-zero'),
        # List items nested ten deep are read like any other.
        pytest.param('- ' * 10 + 'a\n```\n', 2, id='items-nested-ten-deep'),
    ],
)
def test_where_commonmark_parsers_differ_the_body_is_read_as_the_reference_implementation(
    body: str, opening: int | None
) -> None:
    expected = [] if opening is None else [(opening, UNCLOSED_FENCE_MESSAGE)]
    assert commit_lane.unclosed_fence_findings(body) == expected


def test_lines_are_numbered_in_line_feeds_not_in_carriage_returns() -> None:
    body = 'a\rb\rc\n```\ncode\n'
    assert commit_lane.unclosed_fence_findings(body) == [(2, UNCLOSED_FENCE_MESSAGE)]
    assert commit_lane.carriage_return_findings(body) == [(1, CARRIAGE_RETURN_MESSAGE)]


def test_a_line_with_several_lone_carriage_returns_is_one_finding() -> None:
    body = 'ok\na\rb\rc\n\r\rd\nfine\r\n'
    assert commit_lane.carriage_return_findings(body) == [
        (2, CARRIAGE_RETURN_MESSAGE),
        (3, CARRIAGE_RETURN_MESSAGE),
    ]


@pytest.mark.parametrize(
    ('body', 'lines'),
    [
        pytest.param('', [], id='empty'),
        pytest.param('a\nb\n', [], id='line-feeds'),
        pytest.param('a\r\nb\r\n', [], id='crlf'),
        pytest.param('a\r', [1], id='carriage-return-at-the-end'),
        pytest.param('a\r\r\nb', [1], id='lone-before-a-crlf'),
        pytest.param('a\r\n\rb\r\n', [2], id='lone-after-a-crlf'),
        pytest.param('\r', [1], id='only-a-carriage-return'),
    ],
)
def test_a_carriage_return_passes_only_when_a_line_feed_follows_it(
    body: str, lines: list[int]
) -> None:
    found = commit_lane.carriage_return_findings(body)
    assert [number for number, _ in found] == lines


def test_the_title_is_not_judged_for_form(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert body_verdict('fine', tmp_path, capsys, title='```x\ry') == (0, [], ('pass', 'ok'))


def test_attribution_is_named_before_the_form_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = f'```\n{GENERATION_STAMP}\nx\ry\n'
    code, findings, verdict = body_verdict(body, tmp_path, capsys)
    assert (code, verdict) == (1, ('fail', 'attribution'))
    assert [line.split(': ')[1] for line in findings] == [
        'attribution line',
        'unclosed-fence',
        'carriage-return',
    ]


def test_a_body_with_only_a_carriage_return_and_a_closed_fence_names_the_carriage_return(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, verdict = body_verdict('```\nx\n```\na\rb\n', tmp_path, capsys)
    assert (code, verdict) == (1, ('fail', 'carriage-return'))


def test_verdict_takes_the_form_findings_as_keyword_sequences_after_attribution() -> None:
    fence, cr = 'body:1: unclosed-fence: m', 'body:2: carriage-return: m'
    assert commit_lane.verdict('v', [], []).line == 'railform-verdict: v pass ok'
    both = commit_lane.verdict('v', [], [], fences=[fence], carriage_returns=[cr])
    assert both.line == 'railform-verdict: v fail unclosed-fence'
    assert both.findings == (fence, cr)
    only = commit_lane.verdict('v', [], [], carriage_returns=[cr])
    assert only.line == 'railform-verdict: v fail carriage-return'
    named = commit_lane.verdict('v', ['s'], ['a'], fences=[fence], carriage_returns=[cr])
    assert named.line == 'railform-verdict: v fail subject'
    assert named.findings == ('s', 'a', fence, cr)
    attributed = commit_lane.verdict('v', [], ['a'], fences=[fence])
    assert attributed.line == 'railform-verdict: v fail attribution'


def test_the_commit_message_verbs_apply_no_form_findings(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    message = 'feat: add a thing\n\n```\nunclosed\nand a\rlone return\n'
    assert run_verb(capsys, 'commit-msg', message_file(message)) == (0, [], ('pass', 'ok'))
    commit(message)
    assert run_verb(capsys, 'commit-range', 'base...HEAD') == (0, [], ('pass', 'ok'))
