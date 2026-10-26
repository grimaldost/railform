"""The commit lane: the shared logic of `commit-msg`, `commit-range` and `pr-text`.

Development-rails spec, §4. It ports two public sources into frame-driven checks:

- keel (https://github.com/grimaldost/keel), the no-ai-attribution hook of
  `.pre-commit-config.yaml` and `tests/test_commit_msg_lane.py` at c3313fd. The pattern set now
  lives in `commit.attribution_patterns` of the frame.
- mantis-research (https://github.com/grimaldost/mantis-research),
  `scripts/check_commit_message.py` at bbcbc6c, the script form that reads a message file and
  names each offending line.

The rules:

- **Subject.** The first line of a commit message reads `type(scope)!: description`, the scope
  and the `!` optional, the type one of `commit.types`. A commit with more than one parent is
  exempt: git writes its subject. The local `commit-msg` hook also accepts an autosquash
  subject (`fixup! `, `squash! ` or `amend! ` before a valid subject), so `git commit --fixup`
  works while a branch is in progress; `commit-range` refuses it, so it never reaches the base
  branch (development-rails spec, §13, the keel pilot).
- **Attribution.** No line of any message, merges included, matches a pattern of
  `commit.attribution_patterns` (Python `re`, case-insensitive, per line). The pull request's
  title and body are held to the same patterns.
- **Form of a pull request body** (`pr-text` only; the title is one line and gets neither).
  `unclosed-fence`: the body ends inside a fenced code block, as CommonMark reads fences, and the
  finding names the opening line. `carriage-return`: the body holds a carriage return that no line
  feed follows; a CRLF line end passes, since GitHub's web editor submits bodies that way. Both
  name lines counted in line feeds, and both read the raw body, because `str.splitlines` ends a
  line at a lone carriage return and would never show one.

The verb modules (`commit_msg`, `commit_range`, `pr_text`) only parse arguments and call this one.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from railform import frame
from railform.verdict import Verdict

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

SUBJECT = 'subject'
ATTRIBUTION = 'attribution'
UNCLOSED_FENCE = 'unclosed-fence'
CARRIAGE_RETURN = 'carriage-return'
# What follows the comment character in the line `git commit -v` writes before the diff it
# appends to the message template.
SCISSORS_TAIL = ' ------------------------ >8 ------------------------'
DEFAULT_COMMENT_CHAR = '#'
SUBJECT_MESSAGE = 'the subject is not "type(scope)!: description" with a type of the frame'
UNCLOSED_FENCE_MESSAGE = 'a code fence opens on this line and is never closed'
CARRIAGE_RETURN_MESSAGE = 'a carriage return not followed by a line feed'
# A fence opens with at most three spaces of indent and a run of three or more backticks (whose
# info string holds no backtick) or of three or more tildes. A closer is such a run with nothing
# but spaces or tabs after it.
_BACKTICK_OPENER = re.compile(r' {0,3}(`{3,})[^`]*')
_TILDE_OPENER = re.compile(r' {0,3}(~{3,}).*')
_CLOSER = re.compile(r' {0,3}(`{3,}|~{3,})[ \t]*')
# A list item starts with at most three spaces, a bullet or a number of up to nine digits and a
# delimiter, then spaces and content (or the end of the line); a thematic break is not one.
_LIST_ITEM = re.compile(r'( {0,3})([-+*]|\d{1,9}[.)])(?:( +)(\S.*)|[ \t]*)')
_THEMATIC_BREAK = re.compile(r' {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*')
_QUOTE = re.compile(r' {0,3}>')
_HEADING = re.compile(r' {0,3}#{1,6}(?:[ \t]|$)')
_LONE_CARRIAGE_RETURN = re.compile(r'\r(?!\n)')
# What `git commit --fixup`, `--squash` and `--fixup=amend:` put before the subject they target.
AUTOSQUASH_PREFIXES = ('fixup! ', 'squash! ', 'amend! ')


class LaneError(Exception):
    """A usage or configuration error: the verb reports it as exit 2."""


@dataclass(frozen=True)
class Rules:
    """The two rules of the lane, compiled from the frame."""

    subject: re.Pattern[str]
    attribution: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class Commit:
    """A commit of a range: its hash, how many parents it has and its raw message."""

    sha: str
    parents: int
    message: str


def load_rules() -> tuple[Rules, Path]:
    """The rules of the effective frame of the current repository, and the repository's root."""
    try:
        root = frame.repository_root()
        effective = frame.load(root)
    except (frame.FrameError, frame.FrameSchemaError) as exc:
        raise LaneError(str(exc)) from exc
    return rules_from_frame(effective), root


def rules_from_frame(effective: dict[str, Any]) -> Rules:
    commit = effective['commit']
    if not commit['types']:
        raise LaneError('commit.types is empty: no subject could pass')
    types = '|'.join(re.escape(name) for name in commit['types'])
    # `type(scope)!: description`: a non-blank description follows the colon and one space.
    subject = re.compile(rf'(?:{types})(?:\([^()\s][^()]*\))?!?: \S')
    patterns = []
    for pattern in commit['attribution_patterns']:
        try:
            patterns.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:
            raise LaneError(
                f'commit.attribution_patterns: {pattern!r} is not a regex: {exc}'
            ) from exc
    return Rules(subject, tuple(patterns))


def numbered_lines(message: str, *, comment_char: str | None = None) -> list[tuple[int, str]]:
    """The lines of `message` with their 1-based numbers.

    With a `comment_char`, lines starting with it are dropped, and so is everything from the
    scissors line on, as git does before it stores a message.
    """
    scissors = None if comment_char is None else comment_char + SCISSORS_TAIL
    kept = []
    for number, line in enumerate(message.splitlines(), start=1):
        if comment_char is not None:
            if line.rstrip() == scissors:
                break
            if line.startswith(comment_char):
                continue
        kept.append((number, line))
    return kept


def without_autosquash(subject: str) -> str:
    """The subject without the prefixes `git commit --fixup`, `--squash` and `--fixup=amend:`
    write (`fixup! `, `squash! `, `amend! `, repeated when a fixup targets a fixup)."""
    while subject.startswith(AUTOSQUASH_PREFIXES):
        subject = subject.split(' ', 1)[1]
    return subject


def subject_finding(
    lines: Sequence[tuple[int, str]], rules: Rules, *, autosquash: bool = False
) -> tuple[int, str] | None:
    """The line number and message when the subject breaks the rule, else None.

    With `autosquash`, an autosquash prefix is read past and the subject after it is judged.
    """
    for number, line in lines:
        if line.strip():
            subject = without_autosquash(line) if autosquash else line
            if rules.subject.match(subject):
                return None
            return number, SUBJECT_MESSAGE
    return 1, 'the message is empty'


def attribution_findings(lines: Iterable[tuple[int, str]], rules: Rules) -> list[tuple[int, str]]:
    """The lines that attribute authorship to an AI tool, with their numbers."""
    return [
        (number, f'attribution line: {line.strip()}')
        for number, line in lines
        if any(pattern.search(line) for pattern in rules.attribution)
    ]


def _body_lines(body: str) -> list[str]:
    """The lines of `body` split at line feeds, each without its line end, LF or CRLF.

    A carriage return that no line feed follows is part of its line, not a line end.
    """
    lines = body.split('\n')
    return [line.removesuffix('\r') for line in lines[:-1]] + lines[-1:]


def _expand_indent(line: str) -> str:
    """`line` with its leading tabs written as spaces, to the next multiple of four columns."""
    column = 0
    for char in line:
        if char == ' ':
            column += 1
        elif char == '\t':
            column += 4 - column % 4
        else:
            break
    return ' ' * column + line.lstrip(' \t')


def _opener(text: str) -> tuple[str, int] | None:
    """The marker character and run length when `text` is a fence opening line, else None."""
    found = _BACKTICK_OPENER.fullmatch(text) or _TILDE_OPENER.fullmatch(text)
    return (found[1][0], len(found[1])) if found else None


def _closes(text: str, char: str, length: int) -> bool:
    """Whether `text` is a closing line for a fence of `length` copies of `char`."""
    found = _CLOSER.fullmatch(text)
    return bool(found) and found[1][0] == char and len(found[1]) >= length


def _list_item(text: str) -> tuple[int, str] | None:
    """When `text` starts a list item, the columns its content is indented by and what follows
    its marker; else None."""
    if _THEMATIC_BREAK.fullmatch(text):
        return None
    found = _LIST_ITEM.fullmatch(text)
    if not found:
        return None
    width = len(found[1]) + len(found[2])
    if found[4] is None:  # an empty first line
        return width + 1, ''
    if len(found[3]) > 4:  # five or more spaces: the content is indented code
        return width + 1, ' ' * (len(found[3]) - 1) + found[4]
    return width + len(found[3]), found[4]


def _starts_block(text: str) -> bool:
    """Whether `text` starts a block that cannot continue a paragraph lazily."""
    return bool(
        _list_item(text)
        or _QUOTE.match(text)
        or _opener(text)
        or _HEADING.match(text)
        or _THEMATIC_BREAK.fullmatch(text)
    )


def unclosed_fence_findings(body: str) -> list[tuple[int, str]]:
    """The opening line of a fenced code block still open when `body` ends, as a list of one
    (line number, message), else empty. Lines are numbered in line feeds.

    Follows CommonMark: a line indented four or more spaces outside a block is indented code and
    opens nothing. A fence inside a block quote or a list item is not judged: the list items a
    line continues are tracked by their content indent (a continuation line is indented at least
    that far, or follows a paragraph line lazily), so a fence on a continuation line is inside
    its item, and a block quote line is skipped.
    """
    opened: tuple[int, str, int] | None = None  # line number, marker character, marker length
    inner: tuple[str, int, int] | None = None  # in a list item: marker, length, content indent
    items: list[int] = []  # the content indents of the list items the line is in, innermost last
    no_paragraph = False  # the previous line left no paragraph open to continue lazily
    for number, raw in enumerate(_body_lines(body), start=1):
        line = _expand_indent(raw)
        if opened is not None:
            if _closes(line, opened[1], opened[2]):
                opened = None
            continue
        if not line.strip():
            no_paragraph = True
            continue
        if inner is not None:
            if len(line) - len(line.lstrip(' ')) >= inner[2]:
                if _closes(line[inner[2] :], inner[0], inner[1]):
                    inner = None
                    no_paragraph = True
                continue
            inner = None
            no_paragraph = True
        indent = len(line) - len(line.lstrip(' '))
        depth = sum(1 for offset in items if offset <= indent)
        base = items[depth - 1] if depth else 0
        rest = line[base:]
        if depth < len(items) and not no_paragraph and not _starts_block(rest):
            continue  # a lazy continuation of a paragraph in the item
        del items[depth:]
        no_paragraph = False
        while item := _list_item(rest):
            base += item[0]
            rest = item[1]
            items.append(base)
        if _QUOTE.match(rest):
            continue
        if found := _opener(rest):
            if items:
                inner = (found[0], found[1], items[-1])
            else:
                opened = (number, found[0], found[1])
    return [] if opened is None else [(opened[0], UNCLOSED_FENCE_MESSAGE)]


def carriage_return_findings(body: str) -> list[tuple[int, str]]:
    """The lines of `body` that hold a carriage return no line feed follows, with their numbers
    counted in line feeds. Reads the raw body: `splitlines` ends a line at a lone carriage return.
    """
    numbers = sorted(
        {body.count('\n', 0, found.start()) + 1 for found in _LONE_CARRIAGE_RETURN.finditer(body)}
    )
    return [(number, CARRIAGE_RETURN_MESSAGE) for number in numbers]


def check_body_form(label: str, body: str) -> tuple[list[str], list[str]]:
    """Judge the form of a pull request body; returns its fence findings and its
    carriage-return findings, each reading `<label>:<line>: <code>: <message>`."""
    fences = [f'{label}:{n}: {UNCLOSED_FENCE}: {text}' for n, text in unclosed_fence_findings(body)]
    returns = [
        f'{label}:{n}: {CARRIAGE_RETURN}: {text}' for n, text in carriage_return_findings(body)
    ]
    return fences, returns


def check_message(
    label: str,
    message: str,
    rules: Rules,
    *,
    subject_exempt: bool = False,
    comment_char: str | None = None,
    autosquash: bool = False,
) -> tuple[list[str], list[str]]:
    """Judge one message; returns its subject findings and its attribution findings.

    A finding reads `<label>:<line>: <message>`. With `autosquash`, a subject that starts with
    an autosquash prefix is judged on what follows it.
    """
    lines = numbered_lines(message, comment_char=comment_char)
    subject = []
    if not subject_exempt:
        found = subject_finding(lines, rules, autosquash=autosquash)
        if found is not None:
            subject.append(f'{label}:{found[0]}: {found[1]}')
    attribution = [f'{label}:{n}: {text}' for n, text in attribution_findings(lines, rules)]
    return subject, attribution


def verdict(
    verb: str,
    subject: Sequence[str],
    attribution: Sequence[str],
    *,
    fences: Sequence[str] = (),
    carriage_returns: Sequence[str] = (),
) -> Verdict:
    """The verdict of a verb. It names the first rule that has a finding, in the order subject,
    attribution, unclosed-fence, carriage-return, and lists every finding in that order."""
    findings = (*subject, *attribution, *fences, *carriage_returns)
    if subject:
        return Verdict.fail(verb, SUBJECT, findings)
    if attribution:
        return Verdict.fail(verb, ATTRIBUTION, findings)
    if fences:
        return Verdict.fail(verb, UNCLOSED_FENCE, findings)
    if carriage_returns:
        return Verdict.fail(verb, CARRIAGE_RETURN, findings)
    return Verdict.ok(verb)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(['git', *args], cwd=root, capture_output=True, check=False)
    except OSError as exc:
        raise LaneError(f'cannot run git: {exc}') from exc


def split_range(spec: str) -> tuple[str, str]:
    """The two revisions of `BASE...HEAD`."""
    base, dots, head = spec.partition('...')
    if not dots or not base or not head or '...' in head:
        raise LaneError(f'{spec!r} is not a range of the form BASE...HEAD')
    if base.startswith('-') or head.startswith('-'):
        raise LaneError(f'{spec!r}: a revision cannot start with "-"')
    return base, head


def commits_in_range(root: Path, spec: str) -> list[Commit]:
    """The commits of `BASE...HEAD` that HEAD adds to BASE, oldest first.

    This is the commits since the merge base (`git log BASE..HEAD`), the set a pull request adds,
    not git's symmetric difference.
    """
    base, head = split_range(spec)
    # A signature shown by `log.showSignature` would land in the hash field of the output.
    done = _git(
        root,
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
    if done.returncode != 0:
        detail = done.stderr.decode('utf-8', errors='replace').strip()
        raise LaneError(f'git cannot read the range {spec}: {detail}')
    fields = done.stdout.decode('utf-8', errors='replace').split('\x00')
    if fields and fields[-1] == '':
        fields.pop()
    if len(fields) % 3:
        raise LaneError(f'cannot parse the log of {spec}')
    return [
        Commit(fields[i].strip(), len(fields[i + 1].split()), fields[i + 2])
        for i in range(0, len(fields), 3)
    ]


def comment_char(root: Path) -> str:
    """The comment character git strips from a message: `core.commentChar`, else `#`.

    `auto` (git picks a character that the message does not start a line with) reads as `#`.
    """
    done = _git(root, 'config', '--get', 'core.commentChar')
    value = done.stdout.decode('utf-8', errors='replace').strip() if done.returncode == 0 else ''
    return DEFAULT_COMMENT_CHAR if value in ('', 'auto') else value


def is_merging(root: Path) -> bool:
    """True while a merge is being concluded: the message in preparation is a merge's."""
    return _git(root, 'rev-parse', '-q', '--verify', 'MERGE_HEAD').returncode == 0
