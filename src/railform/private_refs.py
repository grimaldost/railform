"""The private-reference check: the verb `railform private-refs`.

Development-rails spec, §3, in two halves.

The paths half is invented for railform and generic. It flags a home-directory path in text
unless the user segment is a placeholder, so it catches a developer's home on a CI runner as well
as on the developer's machine. The six shapes, with any drive letter and without regard to case
for the Windows ones (a backslash and a doubled backslash are the JSON-escaped form):

    X:\\Users\\<name>      X:/Users/<name>      X:\\\\Users\\\\<name>
    X--Users-<name>-     /Users/<name>        /home/<name>

A segment ends at a separator, whitespace, a quote, a backtick, one of `<>(){}[],;:|` or the end of
the line (the slug form also ends at the next dash), so `cd C:\\Users\\<name>` and `<name><br>` are
caught while `(see /home/me)` passes; a trailing full stop is dropped before the comparison.
`/home/` counts only where it is not preceded by a word character, `.` or `-`, so
`src/home/views/` and a URL's `/home/` pass.

A user segment passes when it is in `private_refs.placeholders` (case-insensitive) or is plainly a
placeholder: it contains one of `<`, `>`, `$`, `%`, `{`, `}` or `*`, or it is made only of full
stops and ellipsis characters (`...`, `…`).

The names half reads a private list of terms from the one file that the variable named by
`private_refs.names_file_env` points to, and nowhere else. It matches whole tokens, case
-insensitively: the characters on either side of a term are not letters or digits. With the
variable unset it is silent, which is why it is silent in CI; `--require-names` makes an unset
variable a failure. A variable set to a path that is not a file fails without the flag
(`names-file-missing`): a stale path would otherwise turn the half off unnoticed on the very
machine meant to be checking; a file that exists but cannot be read is a usage error (exit 2).
No list of names is part of this package.

`private_refs.names_enabled = false` in the frame turns the names half off, for a private
repository whose tracked files carry the listed names by design, on machines where the variable is
set (where it is unset the half is already silent); the frame check lists it as a loosening that
needs an `exceptions` row with a reason. The paths half stays on, and a finding line says the
names half is off.

With `--diff`, the private-reference settings (the switch, the variable name and the
placeholders) are read from the `railform.toml` committed at the merge base of the range, so the
range cannot switch the names half off or add a placeholder for the path it carries. A merge base
without a `railform.toml` leaves the working tree's frame in force. Without `--diff` the working
tree's frame judges.

`--event PATH` adds the title and body of the pull request in a GitHub event payload, read as
`railform pr-text` reads them (a `null` or absent value is empty text) and labelled
`pull request title` and `pull request body`. The `commit-lane` job of the reusable workflow runs
it with `--diff` over the pull request's range, so the commit messages and the pull request text
are checked as well as the tracked files.

`--history` checks every commit message reachable from `HEAD` and every line any of those commits
added, including a line a later commit removed. It adds to the scope the other arguments choose
(the given paths, `--diff`, else the tracked files), so it never reads less than the default does.
It does not replace that scope, because `git log -p` shows no line for a file marked `-diff` or
binary, for a merge commit's own conflict resolution, or for a change not yet committed. A finding
is labelled `commit SHA PATH` (`commit SHA` for a message), SHA being the first 12 characters.

Outside a git repository the verb checks the files named as `PATH` or `--text`, and `--event`,
under the default frame; the tracked files, `--diff`, `--history` and a directory exit 2.

No output contains a matched name or the user segment of a matched home path. A finding shows the
shape with the segment replaced by `<name>`, and a term only by its line number in the names file.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from railform import commit_lane, frame, pr_text
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable, Iterator

VERB = 'private-refs'
HOME_PATH = 'home-path'
PRIVATE_NAME = 'private-name'
NAMES_FILE_MISSING = 'names-file-missing'
NAMES_OFF_NOTE = 'private_refs.names_enabled is false: the names half is off'

REDACTED = '<name>'
# A user segment containing one of these is a placeholder, whatever the list says.
_PLACEHOLDER_MARKS = frozenset('<>$%{}*')
# A segment made only of these is a placeholder: a full stop and the ellipsis character.
_DOTS = '.…'

# A segment runs to the next separator, whitespace, quote, backtick, bracket or punctuation mark,
# or the end of the line: `<>(){}[],;:|` end it, so a prompt `C:\Users\x>`, HTML `/home/x<br>` and
# prose `(see /home/me)` read right, and `<name>` or `{user}` simply make no match. `$`, `%` and
# `*` stay in a segment and mark a placeholder.
_STOP = r'\\/\s"\'`<>(){}\[\],;:|'
_SEGMENT = f'[^{_STOP}]+'
# The six shapes. The Windows ones ignore case; `/Users/` and `/home/` are exact.
_SHAPES = (
    re.compile(rf'[A-Za-z]:\\Users\\(?P<name>{_SEGMENT})', re.IGNORECASE),
    re.compile(rf'[A-Za-z]:/Users/(?P<name>{_SEGMENT})', re.IGNORECASE),
    re.compile(rf'[A-Za-z]:\\\\Users\\\\(?P<name>{_SEGMENT})', re.IGNORECASE),
    re.compile(rf'[A-Za-z]--Users-(?P<name>[^-{_STOP}]+)-', re.IGNORECASE),
    re.compile(rf'/Users/(?P<name>{_SEGMENT})'),
    # Not after a word character, `.` or `-`: `src/home/views/` and `https://x.test/home/a/` pass.
    re.compile(rf'(?<![\w.-])/home/(?P<name>{_SEGMENT})'),
)


class UsageError(Exception):
    """A bad argument or an unreadable input, reported as exit 2."""


@dataclass(frozen=True)
class Span:
    """A hit in one line: where it starts and ends, and the part to hide."""

    start: int
    end: int
    hidden_start: int
    hidden_end: int
    term_line: int = 0  # the line of the names file, for a private name; 0 for a home path


@dataclass(frozen=True)
class Source:
    """A text to scan and the label its findings carry."""

    label: str
    text: str


# --- the two halves ----------------------------------------------------------------------------


def _is_placeholder(name: str, placeholders: frozenset[str]) -> bool:
    # A segment of full stops and ellipsis characters only (`/home/.../`, `C:\Users\…\`) stands
    # for a name left out.
    if not name.strip(_DOTS):
        return True
    # A full stop at the end is prose (`/Users/me.`), not part of the name.
    name = name.rstrip('.')
    if any(char in _PLACEHOLDER_MARKS for char in name):
        return True
    return name.lower() in placeholders


def home_path_hits(line: str, placeholders: frozenset[str]) -> list[Span]:
    """The home paths in `line` whose user segment is not a placeholder, overlaps merged."""
    found: list[Span] = []
    for shape in _SHAPES:
        for match in shape.finditer(line):
            if _is_placeholder(match['name'], placeholders):
                continue
            found.append(Span(match.start(), match.end(), match.start('name'), match.end('name')))
    found.sort(key=lambda span: (span.start, -span.end))
    kept: list[Span] = []
    for span in found:
        if kept and span.start < kept[-1].end:
            continue
        kept.append(span)
    return kept


@dataclass(frozen=True)
class Names:
    """The terms of a names file, compiled into one whole-token pattern."""

    pattern: re.Pattern[str] | None
    lines: tuple[int, ...]  # the line of the names file for each capture group

    @classmethod
    def parse(cls, text: str) -> Names:
        terms: list[tuple[int, str]] = []
        for number, raw in enumerate(text.splitlines(), start=1):
            term = raw.strip()
            if term and not term.startswith('#'):
                terms.append((number, term))
        if not terms:
            return cls(None, ())
        alternatives = '|'.join(f'({re.escape(term)})' for _, term in terms)
        # Not preceded or followed by a letter or a digit.
        pattern = re.compile(rf'(?<![^\W_])(?:{alternatives})(?![^\W_])', re.IGNORECASE)
        return cls(pattern, tuple(number for number, _ in terms))

    def hits(self, line: str) -> list[Span]:
        if self.pattern is None:
            return []
        return [
            Span(m.start(), m.end(), m.start(), m.end(), self.lines[(m.lastindex or 1) - 1])
            for m in self.pattern.finditer(line)
        ]


def redact(text: str, spans: Iterable[Span]) -> str:
    """`text` with the hidden part of each span replaced by `<name>`.

    Overlapping or touching hidden parts are merged first, so each stretch is replaced once.
    """
    merged: list[list[int]] = []
    for span in sorted(spans, key=lambda s: (s.hidden_start, s.hidden_end)):
        if merged and span.hidden_start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span.hidden_end)
        else:
            merged.append([span.hidden_start, span.hidden_end])
    for start, end in reversed(merged):
        text = text[:start] + REDACTED + text[end:]
    return text


@dataclass(frozen=True)
class Checker:
    placeholders: frozenset[str]
    names: Names

    def label(self, label: str) -> str:
        """A path or label with anything the check would flag replaced by `<name>`."""
        spans = [*home_path_hits(label, self.placeholders), *self.names.hits(label)]
        return redact(label, spans)

    def scan(self, source: Source) -> list[tuple[str, str]]:
        """The findings in one text, as (code, finding line), in reading order."""
        return self.scan_lines(source.label, enumerate(source.text.split('\n'), start=1))

    def scan_lines(self, label: str, numbered: Iterable[tuple[int, str]]) -> list[tuple[str, str]]:
        """The findings in the given (line number, line) pairs of one text called `label`."""
        label = self.label(label)
        findings: list[tuple[str, str]] = []
        for number, line in numbered:
            line = line.rstrip('\r')
            name_hits = self.names.hits(line)
            for span in home_path_hits(line, self.placeholders):
                # The shown part hides a private name as well, if one falls inside it.
                window = [clip(hit, span.start, span.end) for hit in [span, *name_hits]]
                shape = redact(line[span.start : span.end], [w for w in window if w is not None])
                findings.append((HOME_PATH, f'{label}:{number}: home path {shape}'))
            for span in name_hits:
                findings.append(
                    (
                        PRIVATE_NAME,
                        f'{label}:{number}: private name (names file line {span.term_line})',
                    )
                )
        return findings


def clip(span: Span, start: int, end: int) -> Span | None:
    """The part of `span`'s hidden stretch inside [start, end), moved to index from `start`."""
    low = max(span.hidden_start, start)
    high = min(span.hidden_end, end)
    if low >= high:
        return None
    return Span(low - start, high - start, low - start, high - start)


# --- git and the files -------------------------------------------------------------------------


def _git(root: Path, *args: str) -> bytes:
    try:
        done = subprocess.run(['git', *args], cwd=root, capture_output=True, check=False)
    except OSError as exc:
        raise UsageError(f'cannot run git: {exc}') from exc
    if done.returncode != 0:
        detail = done.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise UsageError(f'git {args[0]} failed: {detail[0] if detail else "no output"}')
    return done.stdout


def _names_from(output: bytes) -> list[str]:
    return [os.fsdecode(item) for item in output.split(b'\0') if item]


def _is_binary(data: bytes) -> bool:
    return b'\0' in data[:8000]


def _source(label: str, data: bytes) -> Source | None:
    if _is_binary(data):
        return None
    return Source(label, data.decode('utf-8', errors='replace'))


def _read_file(path: Path, label: str) -> Source | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return _source(label, data)


def _tracked_files(root: Path, *pathspec: str) -> list[str]:
    output = _git(root, '-c', 'core.quotepath=off', 'ls-files', '-z', '--', *pathspec)
    return _names_from(output)


def sources_for_paths(root: Path | None, paths: list[str]) -> list[Source]:
    """The given paths; a directory stands for the tracked files under it.

    Outside a repository (`root` is None) only files can be given, and a file's label is the
    path as given.
    """
    found: list[Source] = []
    for given in paths:
        path = Path(given)
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.is_dir():
            if root is None:
                raise UsageError(
                    f'{given}: a directory stands for tracked files, {frame.NO_REPOSITORY}'
                )
            try:
                relative = path.resolve().relative_to(root.resolve())
            except ValueError as exc:
                raise UsageError(f'{given}: outside the repository') from exc
            found.extend(_tracked_sources(root, relative.as_posix()))
        elif path.is_file():
            label = given
            if root is not None:
                try:
                    label = path.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    pass
            source = _read_file(path, label)
            if source is not None:
                found.append(source)
        else:
            raise UsageError(f'{given}: no such file')
    return found


def _tracked_sources(root: Path, *pathspec: str) -> list[Source]:
    found: list[Source] = []
    for name in _tracked_files(root, *pathspec):
        source = _read_file(root / name, name)
        if source is not None:
            found.append(source)
    return found


def split_range(spec: str) -> tuple[str, str]:
    """The base and the tip (default HEAD) of a `BASE...TIP` or `BASE..TIP` range."""
    if '...' in spec:
        base, _, tip = spec.partition('...')
    elif '..' in spec:
        base, _, tip = spec.partition('..')
    else:
        raise UsageError(f'--diff expects BASE...HEAD, not {spec!r}')
    if not base:
        raise UsageError(f'--diff expects BASE...HEAD, not {spec!r}')
    return base, tip or 'HEAD'


def sources_for_diff(root: Path, spec: str) -> list[Source]:
    """The files a range changes, read at its tip, and the range's commit messages."""
    base, tip = split_range(spec)
    diff = ['diff', '--no-renames', '--name-only', '-z', '--diff-filter=d', spec]
    changed = _git(root, '-c', 'core.quotepath=off', *diff)
    found: list[Source] = []
    for name in _names_from(changed):
        try:
            done = subprocess.run(
                ['git', 'cat-file', 'blob', f'{tip}:{name}'],
                cwd=root,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise UsageError(f'cannot run git: {exc}') from exc
        if done.returncode != 0:  # a submodule has no blob
            continue
        source = _source(name, done.stdout)
        if source is not None:
            found.append(source)
    found.extend(_commit_messages(root, f'{base}..{tip}'))
    return found


def _commit_messages(root: Path, revisions: str) -> list[Source]:
    """The message of each commit in `revisions`, labelled with its short sha."""
    found: list[Source] = []
    log = _git(root, 'log', '-z', '--format=%H%n%B', revisions)
    for entry in log.split(b'\0'):
        sha, _, message = entry.decode('utf-8', 'replace').partition('\n')
        if sha.strip():
            found.append(Source(f'commit {sha.strip()[:12]}', message))
    return found


# A hunk header; `-U0` hunks carry exactly the removed and the added lines the header counts
# (context lines are counted too, should a hunk hold any).
_HUNK = re.compile(rb'@@ -\d+(?:,(?P<old>\d+))? \+(?P<start>\d+)(?:,(?P<new>\d+))? @@')
_COMMIT_MARK = b'\x01'
_ESCAPE = re.compile(rb'\\(?:([abfnrtv"\\])|([0-7]{3}))')
_SIMPLE_ESCAPES = {
    b'a': b'\a',
    b'b': b'\b',
    b'f': b'\f',
    b'n': b'\n',
    b'r': b'\r',
    b't': b'\t',
    b'v': b'\v',
    b'"': b'"',
    b'\\': b'\\',
}


def _header_path(field: bytes) -> str | None:
    """The path in a `+++ b/PATH` header, or None for `/dev/null`."""
    field = field.rstrip(b'\t')
    if field.startswith(b'"') and field.endswith(b'"') and len(field) >= 2:
        field = _ESCAPE.sub(
            lambda m: _SIMPLE_ESCAPES[m[1]] if m[1] else bytes([int(m[2], 8)]), field[1:-1]
        )
    if not field.startswith(b'b/'):
        return None
    return field[2:].decode('utf-8', 'replace')


def _added_lines(root: Path) -> Iterator[tuple[str, str, list[tuple[int, str]]]]:
    """Every line any commit reachable from HEAD added, as (full sha, path, [(number, text)]).

    The lines come from the patch of each commit read with no context (`-U0`), so a line is named
    by its number in the file the commit wrote. A merge commit shows no patch of its own, a
    binary file shows no hunk, and a deleted file adds nothing.
    """
    command = [
        'git',
        '-c',
        'core.quotepath=off',
        'log',
        '-p',
        '-U0',
        '--inter-hunk-context=0',  # a config that merges hunks would add context lines
        '--root',  # log.showRoot=false would hide the first commit's lines
        '-M',
        '--no-color',
        '--no-ext-diff',
        '--no-textconv',
        '--src-prefix=a/',
        '--dst-prefix=b/',
        '--format=%x01%H',
        'HEAD',
    ]
    return _patch_added_lines(root, command)


def _patch_added_lines(
    root: Path, command: list[str], given: bytes | None = None
) -> Iterator[tuple[str, str, list[tuple[int, str]]]]:
    """The lines a patch printed by `command` adds, as (full sha, path, [(number, text)]).

    `command` prints `-U0` hunks, each commit introduced by `%x01%H` (none for a plain diff,
    whose sha is then empty); `given` is written to its standard input.
    """
    with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as feed:
        if given is not None:
            feed.write(given)
            feed.seek(0)
        try:
            proc = subprocess.Popen(
                command,
                cwd=root,
                stdin=feed if given is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=errors,
            )
        except OSError as exc:
            raise UsageError(f'cannot run git: {exc}') from exc
        if proc.stdout is None:
            raise UsageError(f'cannot read the output of git {command[3]}')
        sha = ''
        path: str | None = None
        lines: list[tuple[int, str]] = []
        old_left = new_left = number = 0
        try:
            for raw in proc.stdout:
                line = raw.rstrip(b'\n')
                if old_left or new_left:
                    if line.startswith(b'-'):
                        old_left -= 1
                        continue
                    if line.startswith(b'+'):
                        new_left -= 1
                        if path is not None:
                            lines.append((number, line[1:].decode('utf-8', 'replace')))
                        number += 1
                        continue
                    if line.startswith(b' '):  # a context line, in case git still prints one
                        old_left -= 1
                        new_left -= 1
                        number += 1
                        continue
                    if line.startswith(b'\\'):  # "No newline at end of file"
                        continue
                    old_left = new_left = 0  # a header that miscounted: read this line afresh
                if line.startswith((_COMMIT_MARK, b'diff --git ')):
                    if lines and path is not None:
                        yield sha, path, lines
                    path, lines = None, []
                    if line.startswith(_COMMIT_MARK):
                        sha = line[1:].decode('ascii', 'replace')
                elif line.startswith(b'+++ '):
                    path = _header_path(line[4:])
                elif (hunk := _HUNK.match(line)) is not None:
                    old_left = int(hunk['old'] if hunk['old'] is not None else 1)
                    new_left = int(hunk['new'] if hunk['new'] is not None else 1)
                    number = int(hunk['start'])
            if lines and path is not None:
                yield sha, path, lines
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
            proc.wait()
        if proc.returncode != 0:
            errors.seek(0)
            detail = errors.read().decode('utf-8', 'replace').strip().splitlines()
            raise UsageError(f'git {command[3]} failed: {detail[0] if detail else "no output"}')


def _has_commits(root: Path) -> bool:
    try:
        done = subprocess.run(
            ['git', 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}'],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise UsageError(f'cannot run git: {exc}') from exc
    return done.returncode == 0


def history_findings(
    root: Path, checker: Checker, scanned: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """The findings in every commit message reachable from HEAD and in every line any of those
    commits added, whether or not a later commit removed it.

    A message is labelled `commit SHA`, an added line `commit SHA PATH`, SHA being the first 12
    characters. The messages come first, then the lines, both newest commit first. A message
    whose label is in `scanned` was read already (by `--diff`) and is left out.
    """
    if not _has_commits(root):
        return []
    findings: list[tuple[str, str]] = []
    for source in _commit_messages(root, 'HEAD'):
        if source.label not in scanned:
            findings.extend(checker.scan(source))
    for sha, path, lines in _added_lines(root):
        findings.extend(checker.scan_lines(f'commit {sha[:12]} {path}', lines))
    return findings


def sources_for_event(path: str | None) -> list[Source]:
    """The title and body of the pull request in the event payload at `path`, if one is given."""
    if path is None:
        return []
    try:
        title, body = pr_text.read_pull_request(path)
    except commit_lane.LaneError as exc:
        raise UsageError(f'--event: {exc}') from exc
    return [Source('pull request title', title), Source('pull request body', body)]


def sources_for_text(files: list[str]) -> list[Source]:
    found: list[Source] = []
    for given in files:
        path = Path(given)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise UsageError(f'{given}: cannot read: {exc.strerror or exc}') from exc
        source = _source(given, data)
        if source is not None:
            found.append(source)
    return found


# --- the verb ----------------------------------------------------------------------------------


def _variable_value(variable: str) -> str:
    return os.environ.get(variable, '') if variable else ''


def _read_names(variable: str) -> Names | None:
    """The compiled names file, or None when the variable is unset or does not name a file."""
    value = _variable_value(variable)
    if not value:
        return None
    path = Path(value)
    if not path.is_file():
        return None
    try:
        text = path.read_bytes().decode('utf-8-sig', errors='replace')
    except OSError as exc:
        raise UsageError(f'cannot read the names file named by ${variable}') from exc
    return Names.parse(text)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Fail when text carries a home-directory path (unless its user segment is a '
        'placeholder) or a term from the private names file. Scope: the given paths; else '
        'the files and commit messages of --diff; else every tracked file. --history adds every '
        'commit message and every line any commit added. '
        '--text and --event add to any scope. Outside a git repository only files (PATH, '
        '--text) and --event can be checked, under the default frame.'
    )
    parser.add_argument('paths', nargs='*', metavar='PATH', help='files to check (staged names)')
    parser.add_argument(
        '--diff', metavar='BASE...HEAD', help='check the files the range changes and its messages'
    )
    parser.add_argument(
        '--history',
        action='store_true',
        help=(
            'check every commit message reachable from HEAD and every line any of those '
            'commits added, including lines a later commit removed'
        ),
    )
    parser.add_argument(
        '--text',
        metavar='FILE',
        action='append',
        default=[],
        help='also check this file, such as a pull request title or body (repeatable)',
    )
    parser.add_argument(
        '--event',
        metavar='PATH',
        help='also check the pull request title and body in this GitHub event payload',
    )
    parser.add_argument(
        '--require-names',
        action='store_true',
        help='also fail with names-file-missing when the names variable is unset',
    )


def _repository_root() -> Path | None:
    """The repository that contains the current directory, or None outside any repository."""
    try:
        return frame.repository_root()
    except frame.NoRepositoryError:
        return None


def _need(root: Path | None) -> Path:
    if root is None:
        raise UsageError(frame.NO_REPOSITORY)
    return root


def _scope_sources(root: Path | None, args: argparse.Namespace) -> list[Source]:
    """The files and messages of the chosen scope, before --text and --event are added."""
    if args.paths:
        return sources_for_paths(root, list(args.paths))
    if args.diff:
        return sources_for_diff(_need(root), args.diff)
    if root is not None:
        return _tracked_sources(root)  # --history adds to this scope
    if args.text or args.event:
        return []  # outside a repository the explicit files are the whole scope
    raise UsageError(frame.NO_REPOSITORY)


def run(args: argparse.Namespace) -> Verdict:
    try:
        root = _repository_root()
        effective = frame.load(root) if root is not None else frame.effective_frame(None)
    except (frame.FrameError, frame.FrameSchemaError) as exc:
        # No frame yet, so no placeholder list: every home-shaped path in the message is hidden.
        return Verdict.usage(
            VERB, f'{VERB}: {Checker(frozenset(), Names(None, ())).label(str(exc))}'
        )
    settings = effective['private_refs']
    try:
        if args.diff:
            settings = _settings_at_merge_base(_need(root), args.diff, settings)
    except (UsageError, frame.FrameError) as exc:
        return Verdict.usage(
            VERB, f'{VERB}: {Checker(frozenset(), Names(None, ())).label(str(exc))}'
        )
    placeholders = frozenset(str(name).lower() for name in settings['placeholders'])
    variable = str(settings['names_file_env'])
    names_enabled = bool(settings['names_enabled'])
    names: Names | None = None
    checker = Checker(placeholders, Names(None, ()))
    history: list[tuple[str, str]] = []
    try:
        names = _read_names(variable) if names_enabled else None
        checker = Checker(placeholders, names or Names(None, ()))
        sources = _scope_sources(root, args)
        sources += sources_for_text(list(args.text))
        sources += sources_for_event(args.event)
        if args.history:
            history = history_findings(
                _need(root), checker, frozenset(source.label for source in sources)
            )
    except UsageError as exc:
        # A message may carry a path the user gave: it is redacted like a finding's label.
        return Verdict.usage(VERB, f'{VERB}: {checker.label(str(exc))}')
    findings: list[tuple[str, str]] = []
    notes: list[str] = []
    if not names_enabled:
        notes.append(NAMES_OFF_NOTE)
    elif names is None and (args.require_names or _variable_value(variable)):
        # Unset: only --require-names asks for the file. Set to a path that is not a file: a
        # stale path fails closed, whatever the flags say.
        shown = f'${variable}' if variable else 'private_refs.names_file_env'
        findings.append((NAMES_FILE_MISSING, f'the names file named by {shown} is not readable'))
    for source in sources:
        findings.extend(checker.scan(source))
    findings.extend(history)
    if not findings:
        return Verdict.ok(VERB, tuple(notes))
    for code in (NAMES_FILE_MISSING, HOME_PATH, PRIVATE_NAME):
        if any(found == code for found, _ in findings):
            return Verdict.fail(VERB, code, (*notes, *(line for _, line in findings)))
    raise AssertionError('unreachable: every finding carries one of the three codes')


def _settings_at_merge_base(root: Path, spec: str, current: dict[str, Any]) -> dict[str, Any]:
    """The `private_refs` settings that judge the range `spec`: the merge base's, so the range
    cannot loosen the check it is judged by; `current` when that commit has no `railform.toml`."""
    base, tip = split_range(spec)
    commit = frame.merge_base(root, base, tip)
    at_base = frame.effective_frame_at(root, commit)
    return current if at_base is None else at_base['private_refs']
