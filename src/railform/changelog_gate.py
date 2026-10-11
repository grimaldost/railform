"""The verb `railform changelog-gate BASE...HEAD`: every change is recorded or declared.

Development-rails spec, §14. Ported from the stack-radar engine
(https://github.com/grimaldost/stack-radar), `src/stack_radar/changelog_gate.py` at 0a48876, with
its tests (`tests/test_changelog_gate.py`).

The judged commits are those HEAD adds to BASE (`git rev-list BASE..HEAD`), merges included, and
the record diff is taken from the merge base of BASE and HEAD to HEAD. Three rules:

- **Record or declare** (`unrecorded`). Each path a commit changes is charged to the record file
  of the `changelog.records` row with the longest matching prefix; a path no row matches is not
  watched, and a records file is never charged to itself. For each commit and each record file it
  is charged to, the range must record something in that file, or the commit must declare itself
  with the trailer `changelog.trailer` spells (`Changelog: none (<reason>)` by default, the reason
  holding a character other than whitespace). A record is an added line that is not blank once
  its whitespace runs are collapsed and that the same diff does not also remove in collapsed
  form, so a whitespace-only edit, a re-indent, a reordering of existing lines and a CRLF
  conversion record nothing. A declaration covers its own commit only, never a neighbour.
  A merge is charged with its resolution only: the paths where its tree differs from the
  automatic merge of its two parents (`git merge-tree --write-tree`, git 2.38 or newer). A merge
  of unrelated histories falls back to the combined diff; an octopus keeps the combined diff,
  which over-charges; any other outcome of `merge-tree` is a configuration error naming the git
  that ran, never a silent fallback. A root commit contributes its paths (`diff-tree --root`).
- **A release heading moves the version forward** (`release-heading`). When a record file's diff
  adds a `## [X.Y.Z]` heading whose version no heading it removes carries (an edited heading is
  not a cut), the version of record is read at HEAD and at the merge base from
  the first non-authored `version.sites` row of that record's release line (the
  `release.tag_prefix` row with the longest prefix of the record file). The heading must equal
  the version at HEAD and be greater than the version at the merge base; a side with no readable
  version is not compared. A line with no non-authored site skips the rule and says so.
- **Contract surfaces carry the marker** (`consumer-affecting`). When the range changes a path of
  `changelog.contract_surfaces` and no record file's added lines hold `(consumer-affecting)`, the
  rule fires once per surface. With `changelog.consumer_affecting = "advisory"` (the default) a
  firing is a `::warning::` finding with the fixed text `consumer-affecting (advisory)` and the
  verdict stays a pass; with `"error"` it fails.

When no tracked path at HEAD other than the record files falls under any `changelog.records` row,
the gate prints `::warning::changelog.records: watches no tracked path` and keeps its verdict: a
watch that covers nothing would otherwise pass every run in silence.

What the port changes from its source: the record files and their prefixes come from
`changelog.records` instead of one `CHANGELOG.md` over `src/`; the version comes from
`version.sites` instead of `pyproject.toml`, which a `plugin-marketplace` repository does not
have; the trailer is the frame's template; the range is `BASE...HEAD`; and the source's rule
that an added `###` heading belongs to the Keep a Changelog vocabulary is not ported, since the
spec does not name it and adopters use other section headings.

`changelog.enabled = false` passes at once and reads no history.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from railform import commit_lane, frame
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

VERB = 'changelog-gate'
UNRECORDED = 'unrecorded'
RELEASE_HEADING = 'release-heading'
CONSUMER_AFFECTING = 'consumer-affecting'
# When several rules fail, the verdict names the first in this order.
CODE_ORDER = (UNRECORDED, RELEASE_HEADING, CONSUMER_AFFECTING)

MARKER = '(consumer-affecting)'
ADVISORY = 'consumer-affecting (advisory)'
# A records watch that covers no tracked path passes every run in silence, so it is announced.
WATCHES_NOTHING = 'watches no tracked path'
REASON_FIELD = '<reason>'
LISTED_PATHS = 5

Version = tuple[int, int, int]

# ASCII digits only: `\d` would also read other scripts' digits, which no version carries.
_ADDED_HEADING = re.compile(r'^## \[([0-9]+)\.([0-9]+)\.([0-9]+)\]', re.MULTILINE)
_VERSION = re.compile(r'([0-9]+)\.([0-9]+)\.([0-9]+)')
# What `git merge-tree --write-tree` prints on its first line when it wrote a tree: a SHA-1 or a
# SHA-256 object id.
_OBJECT_ID = re.compile(r'[0-9a-f]{40}(?:[0-9a-f]{24})?')
# The one outcome without a tree that is a fact about the merge rather than about the git that
# ran: there is no automatic merge of unrelated histories to compare the merge with. Matched on
# the message (git runs under LC_ALL=C), because git exits 128 for this, for a bad revision and
# for an unreadable repository alike.
_SAFE_REFUSAL = re.compile(r'refusing to merge unrelated histories', re.IGNORECASE)


class GateError(Exception):
    """A usage or configuration error, or a git command that failed: exit 2."""


# --- the frame ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """A row of `version.sites`."""

    path: str
    pattern: re.Pattern[str]
    authored: bool


@dataclass(frozen=True)
class Settings:
    """What the gate reads from the effective frame."""

    records: tuple[tuple[str, str], ...]
    trailer: re.Pattern[str]
    template: str
    surfaces: tuple[str, ...]
    consumer_affecting: str
    lines: tuple[str, ...]
    sites: tuple[Site, ...]

    @property
    def files(self) -> tuple[str, ...]:
        """The record files, each once, in the order of their first row."""
        return tuple(dict.fromkeys(file for _, file in self.records))


def trailer_pattern(template: str) -> re.Pattern[str]:
    """The pattern a declaring message line matches: the template's text around `<reason>`,
    literally and case-insensitively, with the reason captured."""
    if template.count(REASON_FIELD) != 1:
        raise GateError(
            f'changelog.trailer {template!r} must hold {REASON_FIELD} exactly once, where the '
            'reason goes'
        )
    before, after = template.split(REASON_FIELD)
    return re.compile(f'{re.escape(before)}(.*){re.escape(after)}', re.IGNORECASE)


def _unique_prefixes(key: str, prefixes: Iterable[str]) -> None:
    seen: set[str] = set()
    for prefix in prefixes:
        if prefix in seen:
            raise GateError(f'{key}: two rows have the prefix {prefix!r}')
        seen.add(prefix)


def settings(effective: Mapping[str, Any]) -> Settings:
    """The gate's settings from an effective frame; GateError when they cannot be obeyed."""
    changelog = effective['changelog']
    records = tuple((row['prefix'], row['file']) for row in changelog['records'])
    if not records:
        raise GateError(
            'changelog.records is empty: a gate that watches nothing passes every run in silence; '
            'turn it off with changelog.enabled = false and an exceptions row instead'
        )
    for prefix, file in records:
        if not file:
            raise GateError(f'changelog.records: the row with the prefix {prefix!r} names no file')
    _unique_prefixes('changelog.records', (prefix for prefix, _ in records))
    lines = tuple(row['prefix'] for row in effective['release']['tag_prefix'])
    _unique_prefixes('release.tag_prefix', lines)
    sites = []
    for row in effective['version']['sites']:
        try:
            pattern = re.compile(row['pattern'])
        except re.error as exc:
            raise GateError(f'version.sites: {row["pattern"]!r} is not a regex: {exc}') from exc
        if pattern.groups < 1:
            raise GateError(f'version.sites: the pattern of {row["path"]} has no group')
        sites.append(Site(row['path'], pattern, bool(row.get('authored', False))))
    template = changelog['trailer']
    return Settings(
        records=records,
        trailer=trailer_pattern(template),
        template=template,
        surfaces=tuple(changelog['contract_surfaces']),
        consumer_affecting=changelog['consumer_affecting'],
        lines=lines,
        sites=tuple(sites),
    )


def owner(path: str, prefixes: Sequence[str]) -> int | None:
    """The index of the row whose prefix is the longest that `path` starts with, or None."""
    best: int | None = None
    for index, prefix in enumerate(prefixes):
        if path.startswith(prefix) and (best is None or len(prefix) > len(prefixes[best])):
            best = index
    return best


# --- the decision, pure ------------------------------------------------------------------------


def declares(message: str, trailer: re.Pattern[str]) -> bool:
    """Whether a line of `message`, trailing whitespace removed, is the trailer with a reason
    that holds a character other than whitespace."""
    for line in message.splitlines():
        found = trailer.fullmatch(line.rstrip())
        if found is not None and found.group(1).strip():
            return True
    return False


def charged(paths: Iterable[str], records: Sequence[tuple[str, str]]) -> dict[str, list[str]]:
    """The record files a commit's paths are charged to, each with its paths in order.

    A path belongs to the row with the longest matching prefix; a path no row matches is not
    watched, and a path that is one of the record files is the record, never charged.
    """
    files = {file for _, file in records}
    prefixes = [prefix for prefix, _ in records]
    found: dict[str, list[str]] = {}
    for raw in paths:
        path = raw.replace('\\', '/')
        if not path or path in files:
            continue
        index = owner(path, prefixes)
        if index is not None:
            found.setdefault(records[index][1], []).append(path)
    return found


def _collapsed(blob: str) -> list[str]:
    """Each line with its whitespace runs collapsed to one space and its ends trimmed, so a
    re-indent, a trailing space and a `\\r` line ending fold onto the line they churn."""
    return [' '.join(line.split()) for line in blob.splitlines()]


def records_something(added: str, removed: str) -> bool:
    """Whether one side of a record file's diff records anything: an added line that is not
    blank once collapsed and that the same diff does not also remove in collapsed form."""
    gone = set(_collapsed(removed))
    return any(line and line not in gone for line in _collapsed(added))


def diff_side(diff: str, sign: str) -> str:
    """The `+` (added) or `-` (removed) lines of a unified diff, without their prefix.

    Only hunk lines count: everything before the first `@@` is the file header, so an added line
    whose own text starts with `++` is still an added line.
    """
    kept: list[str] = []
    in_hunk = False
    for line in diff.split('\n'):
        if line.startswith('@@'):
            in_hunk = True
        elif in_hunk and line.startswith(sign):
            kept.append(line[1:])
    return '\n'.join(kept)


def _headings(side: str) -> list[Version]:
    found: list[Version] = []
    for match in _ADDED_HEADING.finditer(side):
        major, minor, patch = (int(part) for part in match.groups())
        found.append((major, minor, patch))
    return found


def cut_version(added: str, removed: str = '') -> Version | None:
    """The version of the first `## [X.Y.Z]` heading among the added lines that no removed
    heading carries, or None.

    An edited heading is not a cut: a new date on a released heading, a trailing space on it or a
    CRLF conversion of the file removes the heading and adds it back with the same version.
    """
    gone = set(_headings(removed))
    return next((version for version in _headings(added) if version not in gone), None)


def site_version(text: str | None, site: Site) -> Version | None:
    """The version a site's text reads: the first match's group 1, as three integers."""
    if text is None:
        return None
    found = site.pattern.search(text)
    if found is None or found.group(1) is None:
        return None
    parts = _VERSION.fullmatch(found.group(1))
    if parts is None:
        return None
    major, minor, patch = (int(part) for part in parts.groups())
    return (major, minor, patch)


def site_of_record(file: str, settings: Settings) -> Site | None:
    """The first non-authored site of the release line that `file` belongs to, or None."""
    line = owner(file, settings.lines)
    if line is None:
        return None
    for site in settings.sites:
        if not site.authored and owner(site.path, settings.lines) == line:
            return site
    return None


def dotted(version: Version) -> str:
    return '.'.join(str(part) for part in version)


@dataclass(frozen=True)
class CommitChange:
    """One judged commit: its hash, its message and the paths it is charged with."""

    sha: str
    message: str
    paths: tuple[str, ...]


def unrecorded(
    commits: Sequence[CommitChange], recorded: Iterable[str], settings: Settings
) -> list[str]:
    """One finding per commit and record file it is charged to when the range records nothing
    in that file and the commit does not declare itself."""
    done = set(recorded)
    findings: list[str] = []
    for commit in commits:
        if declares(commit.message, settings.trailer):
            continue
        for file, paths in charged(commit.paths, settings.records).items():
            if file in done:
                continue
            listed = ', '.join(paths[:LISTED_PATHS]) + (' ...' if len(paths) > LISTED_PATHS else '')
            subject = next(iter(commit.message.splitlines()), '') or '(empty message)'
            findings.append(
                f'{file}: commit {commit.sha} "{subject}" changes {listed} and the range records '
                f'nothing in {file}; add a line to {file}, or declare the commit with the trailer '
                f'{settings.template}'
            )
    return findings


def heading_findings(
    file: str, cut: Version, site: Site, head: Version | None, base: Version | None
) -> list[str]:
    """The release-heading failures of one record file's added heading."""
    heading = f'## [{dotted(cut)}]'
    found: list[str] = []
    if head is not None and cut != head:
        found.append(
            f'{file}: the diff adds the release heading {heading}, but {site.path} reads '
            f'{dotted(head)} at HEAD; the heading and the version move together'
        )
    if base is not None and cut <= base:
        found.append(
            f'{file}: the diff adds the release heading {heading}, which does not move the '
            f'version forward from {dotted(base)} ({site.path} at the merge base)'
        )
    return found


def surface_findings(changed: Iterable[str], added: Iterable[str], settings: Settings) -> list[str]:
    """One finding per contract surface the range changes when no record file's added lines
    hold the marker: a workflow warning when advisory, a plain finding when an error."""
    if any(MARKER in text for text in added):
        return []
    changed_set = set(changed)
    found: list[str] = []
    for surface in dict.fromkeys(settings.surfaces):
        if surface not in changed_set:
            continue
        message = (
            f'the range changes this contract surface and no record says {MARKER}; if the '
            'change moves what a consumer reads, mark its entry'
        )
        if settings.consumer_affecting == 'error':
            found.append(f'{surface}: {CONSUMER_AFFECTING}: {message}')
        else:
            found.append(f'::warning::{surface}: {ADVISORY}: {message}')
    return found


def auto_merge_tree(written: subprocess.CompletedProcess[str], git_version: str) -> str | None:
    """The tree `git merge-tree --write-tree` wrote, None when git refused the pair as unrelated
    histories, or a GateError naming the git that ran.

    Exit 1 is a conflicted automatic merge and still writes a tree, the conflict markers in it;
    that tree is what tells a hand resolution from an automatic one. Any other outcome without a
    tree, a git older than 2.38 included (it dies `unknown rev --write-tree`), fails loudly:
    falling back to the combined diff there would give a wrong answer on exactly the machines
    that cannot tell.
    """
    first = next(iter(written.stdout.splitlines()), '').strip()
    if _OBJECT_ID.fullmatch(first):
        return first
    said = written.stderr.strip() or written.stdout.strip() or '(no output)'
    if _SAFE_REFUSAL.search(said):
        return None
    headline = next(iter(said.splitlines()), said)
    raise GateError(
        f'git merge-tree --write-tree wrote no tree (exit {written.returncode}) under '
        f'{git_version.strip()}; charging a merge with its resolution needs git 2.38 or newer. '
        f'It said: {headline}'
    )


# --- git ---------------------------------------------------------------------------------------


@dataclass
class _Git:
    root: Path
    _version: str = ''

    def run(
        self, *args: str, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(
                ['git', *args], cwd=self.root, capture_output=True, check=False, env=env
            )
        except OSError as exc:
            raise GateError(f'cannot run git: {exc}') from exc

    def out(self, *args: str) -> str:
        done = self.run(*args)
        if done.returncode != 0:
            said = done.stderr.decode('utf-8', errors='replace').strip()
            headline = next(iter(said.splitlines()), f'exit {done.returncode}')
            raise GateError(f'`git {" ".join(args)}` failed: {headline}')
        return done.stdout.decode('utf-8', errors='replace')

    def names(self, *args: str) -> list[str]:
        """A NUL-separated path list (`-z`), so no path comes back quoted."""
        return [name for name in self.out(*args).split('\0') if name]

    def version(self) -> str:
        if not self._version:
            self._version = self.out('--version').strip()
        return self._version

    def commit(self, revision: str) -> str:
        done = self.run(
            'rev-parse', '--verify', '--quiet', '--end-of-options', f'{revision}^{{commit}}'
        )
        sha = done.stdout.decode('utf-8', errors='replace').strip()
        if done.returncode != 0 or not sha:
            raise GateError(f'git cannot resolve the revision {revision!r} to a commit')
        return sha

    def merge_base(self, base: str, head: str) -> str:
        done = self.run('merge-base', base, head)
        sha = done.stdout.decode('utf-8', errors='replace').strip()
        if done.returncode != 0 or not sha:
            raise GateError('the range has no merge base: BASE and HEAD share no history')
        return sha

    def commits(self, base: str, head: str) -> list[tuple[str, list[str], str]]:
        """`(sha, parents, message)` of each commit HEAD adds to BASE, oldest first."""
        raw = self.out(
            '-c',
            'log.showSignature=false',
            'log',
            '--no-show-signature',
            '-z',
            '--reverse',
            '--format=%H%x00%P%x00%B',
            f'{base}..{head}',
            '--',
        )
        fields = raw.split('\0')
        if fields and fields[-1] == '':
            fields.pop()
        if len(fields) % 3:
            raise GateError(f'cannot parse the log of {base}..{head}')
        return [
            (fields[i].strip(), fields[i + 1].split(), fields[i + 2])
            for i in range(0, len(fields), 3)
        ]

    def own_paths(self, sha: str, parents: Sequence[str]) -> list[str]:
        """The paths a commit is charged with: its own diff, or a merge's resolution."""
        if len(parents) == 2:
            done = self.run(
                'merge-tree',
                '--write-tree',
                parents[0],
                parents[1],
                env={**os.environ, 'LC_ALL': 'C'},
            )
            written = subprocess.CompletedProcess(
                done.args,
                done.returncode,
                done.stdout.decode('utf-8', errors='replace'),
                done.stderr.decode('utf-8', errors='replace'),
            )
            tree = auto_merge_tree(written, self.version())
            if tree is not None:
                return self.names(
                    'diff-tree', '--no-commit-id', '--name-only', '-r', '-z', tree, sha
                )
        if len(parents) >= 2:
            return self.names('diff-tree', '--cc', '--no-commit-id', '--name-only', '-r', '-z', sha)
        return self.names('diff-tree', '--no-commit-id', '--name-only', '-r', '--root', '-z', sha)

    def record_diff(self, base: str, head: str, file: str) -> str:
        # A content diff: an external diff or a textconv driver would otherwise run on the
        # branch under judgement.
        return self.out(
            'diff', '--no-ext-diff', '--no-textconv', '--no-color', base, head, '--', file
        )

    def watches_something(self, head: str, settings: Settings) -> bool:
        """Whether a tracked path at `head`, other than the record files, falls under a row."""
        files = set(settings.files)
        prefixes = [prefix for prefix, _ in settings.records]
        return any(
            path not in files and owner(path, prefixes) is not None
            for path in self.names('ls-tree', '-r', '--name-only', '-z', head)
        )

    def changed(self, base: str, head: str) -> list[str]:
        return self.names('diff', '--no-renames', '--name-only', '-z', base, head)

    def show(self, revision: str, path: str) -> str | None:
        """A file's text at a revision, or None when it is missing or not UTF-8."""
        done = self.run('show', '--no-textconv', f'{revision}:{path}')
        if done.returncode != 0:
            return None
        try:
            return done.stdout.decode('utf-8')
        except UnicodeDecodeError:
            return None


# --- the verb ----------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Fail a change that the changelog does not record: every commit HEAD adds to BASE that '
        'changes a path a changelog.records row watches must be recorded in that file by the '
        'range, or carry the changelog.trailer declaration.'
    )
    parser.add_argument('range', metavar='BASE...HEAD', help='for example origin/main...HEAD')


def _judge(git: _Git, spec: str, settings: Settings) -> Verdict:
    base_rev, head_rev = commit_lane.split_range(spec)
    base, head = git.commit(base_rev), git.commit(head_rev)
    merge_base = git.merge_base(base, head)

    commits = [
        CommitChange(sha, message, tuple(git.own_paths(sha, parents)))
        for sha, parents, message in git.commits(base, head)
    ]
    sides = {}
    for file in settings.files:
        diff = git.record_diff(merge_base, head, file)
        sides[file] = (diff_side(diff, '+'), diff_side(diff, '-'))
    recorded = [
        file for file, (added, removed) in sides.items() if records_something(added, removed)
    ]

    found: dict[str, list[str]] = {code: [] for code in CODE_ORDER}
    notes: list[str] = []
    found[UNRECORDED] = unrecorded(commits, recorded, settings)

    if not git.watches_something(head, settings):
        notes.append(f'::warning::changelog.records: {WATCHES_NOTHING}')

    for file, (added, removed) in sides.items():
        cut = cut_version(added, removed)
        if cut is None:
            continue
        site = site_of_record(file, settings)
        if site is None:
            notes.append(
                f'{file}: the release heading ## [{dotted(cut)}] is not compared with a version: '
                'its release line has no version site that is not authored'
            )
            continue
        head_version = site_version(git.show(head, site.path), site)
        base_version = site_version(git.show(merge_base, site.path), site)
        found[RELEASE_HEADING] += heading_findings(file, cut, site, head_version, base_version)

    surfaces: list[str] = []
    if settings.surfaces:
        added_sides = [added for added, _ in sides.values()]
        surfaces = surface_findings(git.changed(merge_base, head), added_sides, settings)
    if settings.consumer_affecting == 'error':
        found[CONSUMER_AFFECTING] = surfaces
    else:
        notes = surfaces + notes

    failures = [line for code in CODE_ORDER for line in found[code]]
    first = next((code for code in CODE_ORDER if found[code]), None)
    if first is not None:
        return Verdict.fail(VERB, first, (*failures, *notes))
    watched = ', '.join(
        f'{file} ({", ".join(repr(p) for p, f in settings.records if f == file)})'
        for file in settings.files
    )
    summary = (
        f'{len(commits)} commit(s) judged; every change is recorded, declared, or outside the '
        f'watched prefixes (watched: {watched})'
    )
    return Verdict.ok(VERB, (*notes, summary))


def run(args: argparse.Namespace) -> Verdict:
    try:
        root = frame.repository_root()
        effective = frame.load(root)
    except (frame.FrameError, frame.FrameSchemaError) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    if not effective['changelog']['enabled']:
        return Verdict.ok(
            VERB, ('the frame turns the changelog gate off (changelog.enabled = false)',)
        )
    try:
        return _judge(_Git(root), args.range, settings(effective))
    except (GateError, commit_lane.LaneError) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
