"""`railform release-check` (development-rails spec, §16; interface contract, Wave C, section 5).

The red proofs of §16's acceptance criterion, each named in its test:

- a lightweight tag fails (`test_red_proof_a_lightweight_tag_fails`);
- a tag on a commit that is not its release pull request's merge commit fails, while a tag on
  that merge commit passes even when it is also main's tip
  (`test_red_proof_a_tag_off_the_release_merge_commit_fails`);
- a per-plugin tag (`<plugin>--vX.Y.Z`) is judged against its own record
  (`test_red_proof_a_per_plugin_tag_is_judged_against_its_own_record`);
- a release commit touching `src/` fails (`test_red_proof_a_release_commit_touching_src_fails`);
- across two runs in a fixture, a tag re-pointed between them fails the second
  (`test_red_proof_a_tag_repointed_between_two_runs_fails_the_second`, and through the caller
  that adopt writes, `test_red_proof_the_tag_watch_caller_fails_the_run_after_a_tag_moves`).

Every repository is a scratch repository built here, with git isolated by `tests/conftest.py`;
the pull requests come from `$RAILFORM_PULLS_FILE`, and the one test of the real client points
`$GITHUB_API_URL` at an unreachable local port, so nothing reaches the network.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from migration_fixtures import PIN, build
from railform import release_check
from railform.cli import main
from workflow_model import Runner, bash, github_context

if TYPE_CHECKING:
    from pathlib import Path

IDENT = ('-c', 'user.name=someone', '-c', 'user.email=someone@example.com')
UNREACHABLE = 'http://127.0.0.1:9'
CALLER = '.github/workflows/railform-tag-watch.yml'
CHANGELOG = '# Changelog\n\n## [Unreleased]\n\n## [0.1.0]\n\n- **The start.**\n'
RELEASED = '# Changelog\n\n## [Unreleased]\n\n## [0.2.0]\n\n- **A change.**\n\n## [0.1.0]\n'


@pytest.fixture(autouse=True)
def _no_github(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run on GitHub sets these; every test starts without them."""
    for name in ('GITHUB_REPOSITORY', 'GITHUB_API_URL', 'GITHUB_TOKEN', 'RAILFORM_PULLS_FILE'):
        monkeypatch.delenv(name, raising=False)


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ['git', *IDENT, '-c', 'core.autocrlf=false', *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def commit(root: Path, message: str, files: dict[str, str]) -> str:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode('utf-8'))
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', message)
    return git(root, 'rev-parse', 'HEAD')


def merge(root: Path, branch: str, number: int) -> str:
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--no-ff', branch, '-m', f'Merge pull request #{number} from {branch}')
    return git(root, 'rev-parse', 'HEAD')


def tag(root: Path, name: str, target: str, *, annotated: bool = True) -> None:
    if annotated:
        git(root, 'tag', '-f', '-a', name, target, '-m', name)
    else:
        git(root, 'tag', '-f', name, target)


def pull(number: int, title: str, ref: str, merged: str | None) -> dict[str, Any]:
    return {
        'number': number,
        'title': title,
        'head': {'ref': ref},
        'merged_at': '2026-10-11T12:00:00Z' if merged else None,
        'merge_commit_sha': merged,
    }


def pulls_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[dict[str, Any]]
) -> Path:
    path = tmp_path / 'pulls.json'
    path.write_text(json.dumps(entries), encoding='utf-8')
    monkeypatch.setenv('RAILFORM_PULLS_FILE', str(path))
    return path


def check(capsys: pytest.CaptureFixture[str]) -> tuple[int, str, list[str]]:
    """Run `railform release-check`: the exit code, `<status> <code>` and the findings."""
    code = main(['release-check'])
    lines = capsys.readouterr().out.splitlines()
    verdicts = [line for line in lines if line.startswith('railform-verdict:')]
    assert verdicts == [lines[-1]]
    assert lines[-1].startswith('railform-verdict: release-check ')
    return code, lines[-1].removeprefix('railform-verdict: release-check '), lines[:-1]


def write_frame(root: Path, text: str) -> None:
    (root / 'railform.toml').write_text(text, encoding='utf-8')


@dataclass(frozen=True)
class Repo:
    """The base fixture: main holds a root commit and the merge commit M of `release/0.2.0`."""

    root: Path
    start: str
    tip: str
    merge: str


def base(root: Path, release_files: dict[str, str] | None = None, *, fix: bool = False) -> Repo:
    """The base fixture of the contract (section 5.6). `release_files` adds paths to the
    `release: 0.2.0` commit; `fix` moves `src/pkg/x.py` into a `fix: x` commit after it."""
    git(root, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    start = commit(
        root,
        'chore: start',
        {
            'pyproject.toml': '[project]\nname = "pkg"\nversion = "0.1.0"\n',
            'CHANGELOG.md': CHANGELOG,
            'src/pkg/x.py': 'X = 1\n',
        },
    )
    git(root, 'checkout', '-q', '-b', 'release/0.2.0')
    commit(
        root,
        'release: 0.2.0',
        {
            'CHANGELOG.md': RELEASED,
            'pyproject.toml': '[project]\nname = "pkg"\nversion = "0.2.0"\n',
            **(release_files or {}),
        },
    )
    if fix:
        commit(root, 'fix: x', {'src/pkg/x.py': 'X = 2\n'})
    tip = git(root, 'rev-parse', 'HEAD')
    return Repo(root, start, tip, merge(root, 'release/0.2.0', 1))


def base_pulls(repo: Repo) -> list[dict[str, Any]]:
    return [pull(1, 'release: 0.2.0', 'release/0.2.0', repo.merge)]


# --- the red proofs ----------------------------------------------------------------------------


def test_red_proof_a_lightweight_tag_fails(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.merge, annotated=False)
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (1, 'fail lightweight-tag')
    assert any(line.startswith('v0.2.0: ') for line in findings), findings
    tag(scratch_repo, 'v0.2.0', repo.merge)
    assert check(capsys)[:2] == (0, 'pass ok')


@pytest.mark.parametrize('where', ['second parent', 'first parent'])
def test_red_proof_a_tag_off_the_release_merge_commit_fails(
    where: str,
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.tip if where == 'second parent' else repo.start)
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (1, 'fail tag-placement')
    finding = next(line for line in findings if line.startswith('v0.2.0: '))
    assert repo.merge in finding, finding
    # On M, which is also main's tip, the tag passes.
    assert git(scratch_repo, 'rev-parse', 'main') == repo.merge
    tag(scratch_repo, 'v0.2.0', repo.merge)
    assert check(capsys)[:2] == (0, 'pass ok')


def _plugins(root: Path) -> tuple[str, str]:
    """craft-collection's shape: two plugins, each released by its own pull request."""
    git(root, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    write_frame(
        root,
        '[project]\ntype = "plugin-marketplace"\n'
        '[[changelog.records]]\nprefix = "plugins/a/"\nfile = "plugins/a/CHANGELOG.md"\n'
        '[[changelog.records]]\nprefix = "plugins/b/"\nfile = "plugins/b/CHANGELOG.md"\n'
        '[[release.tag_prefix]]\nprefix = "plugins/a/"\ntag = "a--v"\n'
        '[[release.tag_prefix]]\nprefix = "plugins/b/"\ntag = "b--v"\n',
    )
    files: dict[str, str] = {}
    for plugin in ('a', 'b'):
        files[f'plugins/{plugin}/CHANGELOG.md'] = CHANGELOG
        files[f'plugins/{plugin}/.claude-plugin/plugin.json'] = '{"version": "0.1.0"}\n'
    commit(root, 'chore: start', files)
    merges = []
    for number, plugin in enumerate(('a', 'b'), start=1):
        branch = f'release/{plugin}-0.2.0'
        git(root, 'checkout', '-q', '-b', branch)
        commit(
            root,
            f'release({plugin}): 0.2.0',
            {
                f'plugins/{plugin}/CHANGELOG.md': RELEASED,
                f'plugins/{plugin}/.claude-plugin/plugin.json': '{"version": "0.2.0"}\n',
            },
        )
        merges.append(merge(root, branch, number))
    return merges[0], merges[1]


def test_red_proof_a_per_plugin_tag_is_judged_against_its_own_record(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ma, mb = _plugins(scratch_repo)
    pulls_file(
        tmp_path,
        monkeypatch,
        [
            pull(1, 'release(a): 0.2.0', 'release/a-0.2.0', ma),
            pull(2, 'release(b): 0.2.0', 'release/b-0.2.0', mb),
        ],
    )
    tag(scratch_repo, 'a--v0.2.0', ma)
    tag(scratch_repo, 'b--v0.2.0', mb)
    assert check(capsys)[:2] == (0, 'pass ok')
    # b's tag on a's merge commit: b's release pull request merged elsewhere.
    tag(scratch_repo, 'b--v0.2.0', ma)
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (1, 'fail tag-placement')
    assert [line.split(':')[0] for line in findings] == ['b--v0.2.0'], findings


def test_red_proof_a_release_commit_touching_src_fails(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo, {'src/pkg/x.py': 'X = 2\n'})
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.merge)
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (1, 'fail release-commit')
    finding = next(line for line in findings if line.startswith('v0.2.0: '))
    assert 'src/pkg/x.py' in finding
    assert 'pyproject.toml' not in finding and 'CHANGELOG.md' not in finding


def test_a_release_split_from_its_fix_passes(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The same change as above, with the `src/` edit in a `fix: x` commit of its own.
    repo = base(scratch_repo, fix=True)
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.merge)
    assert check(capsys)[:2] == (0, 'pass ok')


def test_red_proof_a_tag_repointed_between_two_runs_fails_the_second(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.merge)
    assert check(capsys)[:2] == (0, 'pass ok')
    git(scratch_repo, 'tag', '-f', '-a', 'v0.2.0', f'{repo.merge}^1', '-m', '0.2.0')
    assert check(capsys)[:2] == (1, 'fail tag-placement')


@pytest.mark.skipif(bash() is None, reason='no bash on this machine')
def test_red_proof_the_tag_watch_caller_fails_the_run_after_a_tag_moves(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The caller adopt writes, run twice as a `schedule` event in the offline model of a run.
    root = scratch_repo
    git(root, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    build(root, 'python-package')
    assert main(['adopt', '--pin', PIN]) == 0
    capsys.readouterr()
    commit(root, 'chore: adopt railform', {})
    git(root, 'checkout', '-q', '-b', 'release/0.2.0')
    commit(root, 'release: 0.2.0', {'CHANGELOG.md': RELEASED, 'uv.lock': 'version = 1\n'})
    merged = merge(root, 'release/0.2.0', 1)
    pulls_file(tmp_path, monkeypatch, [pull(1, 'release: 0.2.0', 'release/0.2.0', merged)])
    tag(root, 'v0.2.0', merged)
    scratch = tmp_path / 'runs'
    scratch.mkdir()

    def run() -> tuple[str, str]:
        done = Runner(root, scratch, github_context('schedule')).run(root / CALLER)
        return done.conclusion, done.text()

    conclusion, text = run()
    assert conclusion == 'success', text
    assert 'railform-verdict: release-check pass ok' in text
    git(root, 'tag', '-f', '-a', 'v0.2.0', f'{merged}^1', '-m', '0.2.0')
    conclusion, text = run()
    assert conclusion == 'failure', text
    assert 'railform-verdict: release-check fail tag-placement' in text


def test_a_tag_on_no_commit_never_matches_a_release_with_no_merge_commit(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Contract (Wave C) 11.5: an annotated tag on a tree resolves to no commit, and a merged
    # release pull request without a merge commit names none; the two never match.
    repo = base(scratch_repo)
    entry = pull(1, 'release: 0.2.0', 'release/0.2.0', None)
    entry['merged_at'] = '2026-10-11T12:00:00Z'
    pulls_file(tmp_path, monkeypatch, [entry])
    tree = git(scratch_repo, 'rev-parse', f'{repo.merge}^{{tree}}')
    tag(scratch_repo, 'v0.2.0', tree)
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (1, 'fail tag-placement')
    assert any(line.startswith('v0.2.0: ') for line in findings), findings


# --- which tags are judged ---------------------------------------------------------------------


def test_no_release_pull_request_fails_and_a_follow_up_does_not_count(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    entries = base_pulls(repo)
    pulls_file(tmp_path, monkeypatch, entries)
    tag(scratch_repo, 'v0.3.0', repo.merge)
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (1, 'fail no-release-pr')
    assert any(line.startswith('v0.3.0: ') and '0.3.0' in line for line in findings), findings
    entries.append(pull(2, 'docs: after 0.3.0', 'docs/after-0.3.0', repo.merge))
    pulls_file(tmp_path, monkeypatch, entries)
    assert check(capsys)[:2] == (1, 'fail no-release-pr')
    # A pull request that was closed without merging is no release either.
    entries.append(pull(3, 'release: 0.3.0', 'release/0.3.0', None))
    pulls_file(tmp_path, monkeypatch, entries)
    assert check(capsys)[:2] == (1, 'fail no-release-pr')


def test_tags_that_map_to_no_row_are_not_judged(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    for name in ('freeze/x', 'v1.0.0-rc1', 'release-0.2.0', 'v0.2'):
        tag(scratch_repo, name, repo.start, annotated=False)
    # No judged tag: the verb needs neither a pull requests file nor a repository name.
    assert check(capsys)[:2] == (0, 'pass ok')
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.merge)
    assert check(capsys)[:2] == (0, 'pass ok')


def test_a_tag_of_another_shape_under_a_row_is_not_judged(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ma, mb = _plugins(scratch_repo)
    pulls_file(tmp_path, monkeypatch, [pull(1, 'release(a): 0.2.0', 'release/a-0.2.0', ma)])
    frame = (scratch_repo / 'railform.toml').read_text(encoding='utf-8')
    write_frame(
        scratch_repo,
        frame + '[[release.tag_prefix]]\nprefix = "plugins/sw/"\ntag = "session-workflow--v"\n',
    )
    # `session-workflow-v0.1.1` is not a tag of the row `session-workflow--v`.
    tag(scratch_repo, 'session-workflow-v0.1.1', mb, annotated=False)
    tag(scratch_repo, 'a--v0.2.0', ma)
    assert check(capsys)[:2] == (0, 'pass ok')


def test_a_tag_maps_to_the_row_with_the_longest_tag() -> None:
    frame = {
        'release': {
            'since': '',
            'tag_prefix': [
                {'prefix': '', 'tag': 'v', 'since': ''},
                {'prefix': 'plugins/one/', 'tag': 'v1', 'since': ''},
            ],
        },
        'changelog': {'records': []},
    }
    lines = release_check.release_lines(frame)
    # `v10.2.0` fits both rows: `v` with 10.2.0 and `v1` with 0.2.0; the longer tag wins.
    mapped = release_check.map_tag(lines, 'v10.2.0')
    assert mapped is not None
    assert (mapped[0].prefix, mapped[0].name, mapped[1]) == ('plugins/one/', 'one', (0, 2, 0))
    mapped = release_check.map_tag(lines, 'v2.0.0')
    assert mapped is not None
    assert (mapped[0].prefix, mapped[0].name, mapped[1]) == ('', '', (2, 0, 0))
    assert release_check.map_tag(lines, 'v2.0.0-rc1') is None


def test_floors_leave_older_tags_unjudged(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.1.0', repo.start, annotated=False)
    tag(scratch_repo, 'v0.2.0', repo.merge)
    assert check(capsys)[:2] == (1, 'fail lightweight-tag')
    write_frame(scratch_repo, '[release]\nsince = "v0.2.0"\n')
    assert check(capsys)[:2] == (0, 'pass ok')
    # The floor tag need not exist: a floor above every tag judges none.
    write_frame(scratch_repo, '[release]\nsince = "v9.0.0"\n')
    assert check(capsys)[:2] == (0, 'pass ok')


def test_a_rows_own_since_is_its_floor(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ma, mb = _plugins(scratch_repo)
    pulls_file(
        tmp_path,
        monkeypatch,
        [
            pull(1, 'release(a): 0.2.0', 'release/a-0.2.0', ma),
            pull(2, 'release(b): 0.2.0', 'release/b-0.2.0', mb),
        ],
    )
    frame = (scratch_repo / 'railform.toml').read_text(encoding='utf-8')
    tag(scratch_repo, 'a--v0.1.0', f'{ma}^1', annotated=False)
    tag(scratch_repo, 'a--v0.2.0', ma)
    tag(scratch_repo, 'b--v0.2.0', mb)
    assert check(capsys)[:2] == (1, 'fail lightweight-tag')
    write_frame(
        scratch_repo,
        frame.replace('tag = "a--v"\n', 'tag = "a--v"\nsince = "a--v0.2.0"\n'),
    )
    assert check(capsys)[:2] == (0, 'pass ok')


@pytest.mark.parametrize(
    'frame',
    [
        '[release]\nsince = "x0.2.0"\n',
        '[[release.tag_prefix]]\nprefix = ""\ntag = "v"\nsince = "x0.2.0"\n',
        '[[release.tag_prefix]]\nprefix = ""\ntag = "v"\nsince = "v0.2"\n',
        '[[release.tag_prefix]]\nprefix = ""\ntag = "v"\n[[release.tag_prefix]]\nprefix = ""\n'
        'tag = "w"\n',
        '[[changelog.records]]\nprefix = ""\nfile = "A.md"\n[[changelog.records]]\nprefix = ""\n'
        'file = "B.md"\n',
    ],
)
def test_a_floor_that_does_not_map_or_a_repeated_prefix_exits_2(
    frame: str, scratch_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_frame(scratch_repo, frame)
    assert check(capsys)[:2] == (2, 'fail usage')


# --- the frame changes the verdict -------------------------------------------------------------

DISABLED = (
    '[release]\nenabled = false\n\n[[exceptions]]\nkey = "release.enabled"\nvalue = false\n'
    'reason = "this repository does not tag releases"\n'
)


@pytest.mark.parametrize('case', ['lightweight', 'placement', 'no-pr', 'src'])
def test_release_enabled_false_turns_every_failure_into_a_pass(
    case: str,
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo, {'src/pkg/x.py': 'X = 2\n'} if case == 'src' else None)
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    name = 'v0.3.0' if case == 'no-pr' else 'v0.2.0'
    target = repo.tip if case == 'placement' else repo.merge
    tag(scratch_repo, name, target, annotated=case != 'lightweight')
    assert check(capsys)[0] == 1
    write_frame(scratch_repo, DISABLED)
    assert main(['frame', '--check']) == 0
    capsys.readouterr()
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (0, 'pass ok')
    assert len(findings) == 1


def test_disabled_reads_neither_git_nor_the_api(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = base(scratch_repo)
    tag(scratch_repo, 'v0.2.0', repo.merge)
    write_frame(scratch_repo, DISABLED)
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/r')
    monkeypatch.setenv('GITHUB_API_URL', UNREACHABLE)
    assert check(capsys)[:2] == (0, 'pass ok')


def test_metadata_paths_widen_what_a_release_commit_may_change(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo, {'src/pkg/x.py': 'X = 2\n'})
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.merge)
    assert check(capsys)[:2] == (1, 'fail release-commit')
    write_frame(
        scratch_repo,
        '[release]\nmetadata_paths = ["pyproject.toml", "uv.lock", "src/**"]\n\n'
        '[[exceptions]]\nkey = "release.metadata_paths"\n'
        'value = ["pyproject.toml", "uv.lock", "src/**"]\n'
        'reason = "the version module is release metadata here"\n',
    )
    assert main(['frame', '--check']) == 0
    capsys.readouterr()
    assert check(capsys)[:2] == (0, 'pass ok')


def test_a_squash_release_commit_is_judged_and_a_merge_commit_is_not(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    git(scratch_repo, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    commit(scratch_repo, 'chore: start', {'CHANGELOG.md': CHANGELOG, 'src/pkg/x.py': 'X = 1\n'})
    squash = commit(
        scratch_repo,
        'release: 0.2.0 (#4)',
        {'CHANGELOG.md': RELEASED, 'src/pkg/x.py': 'X = 2\n'},
    )
    pulls_file(tmp_path, monkeypatch, [pull(4, 'release: 0.2.0', 'release/0.2.0', squash)])
    tag(scratch_repo, 'v0.2.0', squash)
    code, verdict, findings = check(capsys)
    assert (code, verdict) == (1, 'fail release-commit')
    assert squash in next(line for line in findings if line.startswith('v0.2.0: '))


# --- the pull requests -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('version', 'name', 'head', 'title', 'identifies'),
    [
        ('0.2.0', '', 'release/0.2.0', 'chore: x', True),
        ('0.2.0', '', 'release/keel-0.2.0', 'chore: x', True),
        ('0.2.0', '', 'feat/x', 'release: keel 0.2.0 — notes', True),
        ('0.2.0', '', 'docs/after-0.2.0', 'docs: update for 0.2.0', False),
        ('0.2.0', '', 'release/0.2.01', 'chore: x', False),
        ('0.2.0', '', 'release/10.2.0', 'chore: x', False),
        ('0.2.0', '', 'release/0.2.0.1', 'chore: x', False),
        ('0.2.0', '', 'Release/0.2.0', 'Release: 0.2.0', False),
        ('0.2.0', '', 'feat/x', 'release!: 0.2.0', True),
        ('0.19.0', 'humblepowers', 'release/humblepowers-0.19.0', 'chore: x', True),
        ('0.19.0', 'humblepowers', 'feat/x', 'release(humblepowers): 0.19.0', True),
        ('0.19.0', 'humblepowers', 'release/session-workflow-0.19.0', 'chore: x', False),
        ('0.19.0', 'humblepowers', 'release/0.19.0', 'release: 0.19.0', False),
        ('0.2.0', 'a', 'release/a-0.2.0', 'chore: x', True),
        ('0.2.0', 'a', 'release/b-0.2.0', 'release(b): 0.2.0', False),
        ('1.0.0', '', 'release/1.0.0-rc1', 'chore: x', False),
        ('1.0.0', '', 'release/1.0.0+build.5', 'chore: x', False),
        ('1.0.0', '', 'feat/x', 'release: 1.1.0 (from 1.0.0)', False),
        ('1.0.0', '', 'feat/x', 'release: 1.0.0-rc1', False),
        ('1.0.0', '', 'release/1.1.0-after-1.0.0', 'chore: x', False),
        ('1.0.0', '', 'release/1.0.0', 'chore: x', True),
        ('1.0.0', '', 'feat/x', 'release: 1.0.0 (from 0.9.0)', True),
        ('1.0.0', '', 'release/1.0.0-', 'chore: x', True),
    ],
)
def test_a_release_pull_request_carries_the_version_and_the_name_as_tokens(
    version: str, name: str, head: str, title: str, identifies: bool
) -> None:
    candidate = release_check.Pull(1, title, head, '0' * 40)
    assert release_check.is_release_pull(candidate, version, name) is identifies


@pytest.mark.parametrize(
    'content',
    [
        '{"number": 1}',
        '[{"number": 1, "title": "release: 0.2.0", "merged_at": "x"}]',
        '[{"number": 1, "title": null, "head": {"ref": "release/0.2.0"}}]',
        '[1]',
        'not json',
    ],
)
def test_a_bad_pull_requests_file_exits_2(
    content: str,
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    tag(scratch_repo, 'v0.2.0', repo.merge)
    path = tmp_path / 'pulls.json'
    path.write_text(content, encoding='utf-8')
    monkeypatch.setenv('RAILFORM_PULLS_FILE', str(path))
    assert check(capsys)[:2] == (2, 'fail usage')
    monkeypatch.setenv('RAILFORM_PULLS_FILE', str(tmp_path / 'missing.json'))
    assert check(capsys)[:2] == (2, 'fail usage')


def test_the_stand_in_never_reaches_the_network(
    scratch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    pulls_file(tmp_path, monkeypatch, base_pulls(repo))
    tag(scratch_repo, 'v0.2.0', repo.merge)
    monkeypatch.setenv('GITHUB_API_URL', UNREACHABLE)
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/r')
    assert check(capsys)[:2] == (0, 'pass ok')


def test_an_unreachable_api_exits_2_api_error_and_never_prints_the_token(
    scratch_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = base(scratch_repo)
    tag(scratch_repo, 'v0.2.0', repo.merge)
    secret = 'tok' + 'en-' + 'sentinel-4242'
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/r')
    monkeypatch.setenv('GITHUB_API_URL', UNREACHABLE)
    monkeypatch.setenv('GITHUB_TOKEN', secret)
    code = main(['release-check'])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out.splitlines()[-1] == 'railform-verdict: release-check fail api-error'
    assert 'release-check' in captured.err
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize('repository', [None, '', 'just-a-name', 'o/r/x', 'o/r x'])
def test_the_api_needs_a_repository_named_owner_slash_name(
    repository: str | None,
    scratch_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = base(scratch_repo)
    tag(scratch_repo, 'v0.2.0', repo.merge)
    monkeypatch.setenv('GITHUB_API_URL', UNREACHABLE)
    if repository is not None:
        monkeypatch.setenv('GITHUB_REPOSITORY', repository)
    assert check(capsys)[:2] == (2, 'fail usage')


class _Opener:
    def __init__(self, open_: Any) -> None:
        self.open = open_


class _Response:
    def __init__(self, body: object, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(body).encode('utf-8')

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_the_client_reads_every_page_with_the_api_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict[str, str], float]] = []
    pages = {
        1: [pull(n, f'chore: {n}', f'feat/{n}', None) for n in range(100)],
        2: [pull(7, 'release: 0.2.0', 'release/0.2.0', 'a' * 40)],
    }

    def urlopen(request: Any, timeout: float) -> _Response:
        seen.append((request.full_url, dict(request.header_items()), timeout))
        return _Response(pages[int(request.full_url.rsplit('=', 1)[1])])

    monkeypatch.setattr(release_check, '_opener', lambda: _Opener(urlopen))
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/r')
    monkeypatch.setenv('GITHUB_API_URL', 'https://api.example.test/')
    monkeypatch.setenv('GITHUB_TOKEN', 'abc')
    pulls = release_check.fetch_pulls()
    assert [p.number for p in pulls] == [7]
    assert [url for url, _, _ in seen] == [
        f'https://api.example.test/repos/o/r/pulls?state=closed&per_page=100&page={n}'
        for n in (1, 2)
    ]
    headers = {key.lower(): value for key, value in seen[0][1].items()}
    assert headers['accept'] == 'application/vnd.github+json'
    assert headers['x-github-api-version'] == '2022-11-28'
    assert headers['user-agent'] == 'railform'
    assert headers['authorization'] == 'Bearer abc'
    assert seen[0][2] == 30
    monkeypatch.delenv('GITHUB_TOKEN')
    seen.clear()
    release_check.fetch_pulls()
    assert 'authorization' not in {key.lower() for key in seen[0][1]}


@pytest.mark.parametrize(('body', 'status'), [({'message': 'x'}, 200), ([], 503)])
def test_a_body_that_is_not_an_array_or_a_bad_status_is_an_api_error(
    body: object, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release_check,
        '_opener',
        lambda: _Opener(lambda request, timeout: _Response(body, status)),
    )
    monkeypatch.setenv('GITHUB_REPOSITORY', 'o/r')
    with pytest.raises(release_check.ApiError):
        release_check.fetch_pulls()


@pytest.mark.parametrize(
    ('target', 'keeps'),
    [
        ('https://api.github.com/repositories/1/pulls?page=2', True),
        ('https://elsewhere.example/pulls', False),
        ('http://api.github.com/repos/o/r/pulls', False),
    ],
)
def test_the_token_stays_on_its_host_across_a_redirect(target: str, keeps: bool) -> None:
    # Contract (Wave C) 11.7: a redirect to another host never carries the token.
    request = urllib.request.Request(
        'https://api.github.com/repos/o/r/pulls', headers={'Authorization': 'Bearer abc'}
    )
    handler = release_check.SameHostRedirects()
    followed = handler.redirect_request(request, None, 302, 'Found', {}, target)
    assert followed is not None
    assert followed.full_url == target
    assert followed.has_header('Authorization') is keeps
    # The real client uses the handler.
    opener = release_check._opener()
    assert any(isinstance(h, release_check.SameHostRedirects) for h in opener.handlers)


# --- release metadata --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('glob', 'path', 'matches'),
    [
        ('plugins/*/.claude-plugin/plugin.json', 'plugins/a/.claude-plugin/plugin.json', True),
        ('plugins/*/.claude-plugin/plugin.json', 'plugins/a/b/.claude-plugin/plugin.json', False),
        ('pyproject.toml', 'pyproject.toml', True),
        ('pyproject.toml', 'sub/pyproject.toml', False),
        ('pyproject.toml', 'PyProject.toml', False),
        ('src/**', 'src/pkg/x.py', True),
        ('src/**', 'srcs/x.py', False),
        ('**/version.py', 'version.py', True),
        ('**/version.py', 'a/b/version.py', True),
        ('a/**/b.txt', 'a/b.txt', True),
        ('a/**/b.txt', 'a/x/y/b.txt', True),
        ('*.lock', 'uv.lock', True),
        ('*.lock', 'a/uv.lock', False),
    ],
)
def test_metadata_globs_match_the_whole_path(glob: str, path: str, matches: bool) -> None:
    assert (release_check.glob_pattern(glob).fullmatch(path) is not None) is matches
