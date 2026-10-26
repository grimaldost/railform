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
  `unclosed-fence`: the body ends inside a fenced code block that no block quote or list item
  holds, its block structure read as markdown-it reads CommonMark, and the finding names the
  opening line. `carriage-return`: the body holds a carriage return that no line
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
    from collections.abc import Iterable, Iterator, Sequence
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
# The block starts of CommonMark that decide where a fence sits, each matched on a line's text
# after an indent of at most three spaces, with tabs already expanded to spaces. A fence opens with
# a run of three or more backticks (whose info string holds no backtick) or of three or more
# tildes; a closer is such a run with nothing but spaces after it.
_BACKTICK_OPENER = re.compile(r'(`{3,})[^`]*')
_TILDE_OPENER = re.compile(r'(~{3,}).*')
_CLOSER = re.compile(r'(`{3,}|~{3,}) *')
_ATX_HEADING = re.compile(r'#{1,6}(?: |$)')
_SETEXT_UNDERLINE = re.compile(r'(?:=+|-+) *')
_THEMATIC_BREAK = re.compile(r'(?:\* *){3,}|(?:- *){3,}|(?:_ *){3,}')
# A list marker: a bullet, or a number of up to nine digits and a delimiter; a space or the end of
# the line follows it.
_BULLET = re.compile(r'[-+*](?= |$)')
_ORDERED = re.compile(r'(\d{1,9})[.)](?= |$)')
# The starts of an HTML block, each with the end searched for from its first line on; None ends the
# block before the next blank line. A start of the last kind, a line of block-level tag names, can
# interrupt a paragraph; `_TAG_LINE`, a line holding only one complete tag, cannot.
_BLOCK_TAGS = (
    'address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|'
    'dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|'
    'header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|'
    'param|section|source|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul'
)
_HTML_STARTS: tuple[tuple[re.Pattern[str], re.Pattern[str] | None], ...] = (
    (
        re.compile(r'<(?:script|pre|style|textarea)(?=\s|>|$)', re.IGNORECASE),
        re.compile(r'</(?:script|pre|style|textarea)>', re.IGNORECASE),
    ),
    (re.compile('<!--'), re.compile('-->')),
    (re.compile(r'<\?'), re.compile(r'\?>')),
    (re.compile('<![A-Z]'), re.compile('>')),
    (re.compile(r'<!\[CDATA\['), re.compile(r'\]\]>')),
    (re.compile(rf'</?(?:{_BLOCK_TAGS})(?=\s|/?>|$)', re.IGNORECASE), None),
)
_ATTRIBUTE = (
    r'\s+[A-Za-z_:][A-Za-z0-9_.:-]*'  # a name, then an optional value
    r"""(?:\s*=\s*(?:[^"'=<>`\x00-\x20]+|'[^']*'|"[^"]*"))?"""
)
_TAG_LINE = re.compile(
    rf'(?:<[A-Za-z][A-Za-z0-9-]*(?:{_ATTRIBUTE})*\s*/?>|</[A-Za-z][A-Za-z0-9-]*\s*>)\s*'
)
# A link destination markdown-it refuses, after backslash escapes: a script, file or data URL
# other than an image.
_ESCAPE = re.compile(r'\\([!-/:-@\[-`{-~])')
_UNSAFE_LINK = re.compile(r'(?:vbscript|javascript|file|data):', re.IGNORECASE)
_IMAGE_DATA_LINK = re.compile(r'data:image/(?:gif|png|jpeg|webp);', re.IGNORECASE)
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


def _expand_tabs(line: str) -> str:
    """`line` with each tab written as the spaces that reach the next multiple of four columns,
    the tab stop CommonMark reads block structure with."""
    parts = line.split('\t')
    text = parts[0]
    for part in parts[1:]:
        text += ' ' * (4 - len(text) % 4) + part
    return text


def _spaces(text: str) -> int:
    """How many spaces `text` starts with."""
    return len(text) - len(text.lstrip(' '))


def _opener(text: str) -> tuple[str, int] | None:
    """The marker character and run length when `text` opens a fence, else None."""
    found = _BACKTICK_OPENER.fullmatch(text) or _TILDE_OPENER.fullmatch(text)
    return (found[1][0], len(found[1])) if found else None


def _closes(rest: str, char: str, length: int) -> bool:
    """Whether `rest`, with at most three spaces of indent, closes a fence of `length` `char`s."""
    indent = _spaces(rest)
    found = _CLOSER.fullmatch(rest[indent:])
    return indent < 4 and bool(found) and found[1][0] == char and len(found[1]) >= length


@dataclass
class _Item:
    """An open list item: the columns its content is indented by, and whether it holds a block."""

    width: int
    filled: bool = False


@dataclass(frozen=True)
class _Fence:
    """An open fenced code block: its opening line, its marker, and whether a block quote or a
    list item holds it."""

    number: int
    char: str
    length: int
    nested: bool


@dataclass(frozen=True)
class _Html:
    """An open HTML block: the end that closes it, or None when a blank line does."""

    end: re.Pattern[str] | None


_PARAGRAPH = 'paragraph'
_INDENTED_CODE = 'indented code'
_ONE_LINE = 'one line'  # a heading or a thematic break, closed on the line that opens it


def _continued(containers: Sequence[_Item | None], line: str) -> tuple[int, str]:
    """How many of the open containers `line` continues, outermost first, and what is left of the
    line after their markers. A block quote (None) continues on a line that starts with `>`; a
    list item continues on a line indented to its content, or on a blank line once it holds a
    block, since an item can begin with at most one blank line."""
    rest = line
    for matched, container in enumerate(containers):
        indent = _spaces(rest)
        if container is None:
            if indent > 3 or rest[indent : indent + 1] != '>':
                return matched, rest
            rest = rest[indent + 1 :]
            rest = rest.removeprefix(' ')
        elif not rest.strip(' '):
            if not container.filled:
                return matched, rest
            rest = ''
        elif indent >= container.width:
            rest = rest[container.width :]
        else:
            return matched, rest
    return len(containers), rest


def _list_item(text: str, *, interrupts: bool) -> tuple[int, str] | None:
    """When `text` starts a list item, the columns from its marker to its content and the text
    after them; else None. An item that would interrupt a paragraph must have content on its
    first line and, when ordered, start at 1."""
    found = _BULLET.match(text) or _ORDERED.match(text)
    if found is None:
        return None
    after = text[found.end() :]
    content = after.lstrip(' ')
    if interrupts and (not content or (found.re is _ORDERED and int(found[1]) != 1)):
        return None
    spaces = len(after) - len(content)
    if not content or spaces > 4:  # an empty first line, or indented code after one space
        return found.end() + 1, after[1:]
    return found.end() + spaces, content


def _fill(containers: Sequence[_Item | None]) -> None:
    """Record that the innermost container, when it is a list item, holds a block."""
    if containers and containers[-1] is not None:
        containers[-1].filled = True


def _short_of_items(containers: Sequence[_Item | None], line: str) -> bool:
    """Whether a blank `line` is indented less than the content of the list items that hold the
    innermost block, counted from the last block quote's marker. markdown-it ends an HTML block at
    such a line, where the reference implementations keep it open."""
    quote = max((i for i, container in enumerate(containers) if container is None), default=-1)
    width = sum(item.width for item in containers[quote + 1 :] if item is not None)
    _, rest = _continued(containers[: quote + 1], line)
    return _spaces(rest) < width


def _html_start(text: str, *, lone_tag: bool) -> _Html | None:
    """The HTML block that `text` starts, else None; `lone_tag`: whether a line holding only one
    complete tag may start one, which it may not where it would interrupt a paragraph."""
    for start, end in _HTML_STARTS:
        if start.match(text):
            return _Html(end)
    return _Html(None) if lone_tag and _TAG_LINE.fullmatch(text) else None


def _block_starts(
    rest: str, *, continues_paragraph: bool, in_paragraph: bool
) -> tuple[list[_Item | None], tuple[str, int] | _Html | str | None, str]:
    """The containers that `rest` opens (None for a block quote), the leaf block it opens after
    them (a fence's marker, an HTML block, indented code, a one-line block, or None), and the text
    left.

    `continues_paragraph`: without a block start the line would continue an open paragraph, so a
    list item must be able to interrupt one. `in_paragraph`: a paragraph is the deepest open block,
    so, unless a container opens first, an indented line continues it rather than opening indented
    code, and so does a line holding only one tag, even when the paragraph is continued lazily.
    """
    started: list[_Item | None] = []
    while True:
        indent = _spaces(rest)
        text = rest[indent:]
        if indent > 3:
            code = bool(text) and not (in_paragraph and not started)
            return started, _INDENTED_CODE if code else None, rest
        if text.startswith('>'):
            rest = text[1:].removeprefix(' ')
            started.append(None)
            continues_paragraph = False
            continue
        if fence := _opener(text):
            return started, fence, ''
        if html := _html_start(text, lone_tag=not (in_paragraph and not started)):
            return started, html, text
        underline = continues_paragraph and _SETEXT_UNDERLINE.fullmatch(text)
        if underline or _ATX_HEADING.match(text) or _THEMATIC_BREAK.fullmatch(text):
            return started, _ONE_LINE, ''
        item = _list_item(text, interrupts=continues_paragraph)
        if item is None:
            return started, None, rest
        started.append(_Item(indent + item[0]))
        rest = item[1]
        continues_paragraph = False


class _Text:
    """The text of the lines a link reference definition may span, joined with line feeds and
    read only as far as the definition needs."""

    def __init__(self, first: str, following: Iterator[str]) -> None:
        self.text = first
        self._following = following

    def at(self, pos: int) -> str:
        """The character at `pos`, or '' past the last line."""
        while pos >= len(self.text):
            line = next(self._following, None)
            if line is None:
                return ''
            self.text += '\n' + line
        return self.text[pos]


def _skip(text: _Text, pos: int, chars: str) -> int:
    """The first position from `pos` on whose character is not one of `chars`."""
    while (char := text.at(pos)) and char in chars:
        pos += 1
    return pos


def _destination_end(text: _Text, pos: int) -> int:
    """The position after a link destination that starts at `pos`, else 0: text in angle
    brackets on one line, or a run without spaces or control characters whose parentheses
    balance."""
    if text.at(pos) == '<':
        pos += 1
        while (char := text.at(pos)) not in ('', '\n', '<'):
            if char == '>':
                return pos + 1
            pos += 2 if char == '\\' and text.at(pos + 1) else 1
        return 0
    start, depth = pos, 0
    while (char := text.at(pos)) and ' ' < char != '\x7f':
        if char == '\\' and text.at(pos + 1):
            if text.at(pos + 1) == ' ':
                break
            pos += 2
            continue
        if char == '(':
            depth += 1
            if depth > 32:
                return 0
        elif char == ')':
            if not depth:
                break
            depth -= 1
        pos += 1
    return pos if pos > start and not depth else 0


def _title_end(text: _Text, pos: int) -> int:
    """The position after a link title that starts at `pos`, else 0: text in double quotes, in
    single quotes, or in parentheses without an unescaped `(`."""
    closer = {'"': '"', "'": "'", '(': ')'}.get(text.at(pos))
    if closer is None:
        return 0
    pos += 1
    while char := text.at(pos):
        if char == closer:
            return pos + 1
        if char == '(' and closer == ')':
            return 0
        pos += 2 if char == '\\' and text.at(pos + 1) else 1
    return 0


def _line_ends(text: _Text, pos: int) -> bool:
    """Whether only spaces and tabs follow `pos` on its line."""
    return text.at(_skip(text, pos, ' \t')) in ('', '\n')


def _definition_lines(text: _Text) -> int:
    """How many lines the link reference definition at the start of `text` takes, else 0.

    `[label]:`, a destination and an optional title, with spaces, tabs or a line end between the
    parts, and nothing after the definition on its last line. A title followed by other text is
    dropped when it holds text, leaving the definition to end with its destination.
    """
    pos = 1
    while (char := text.at(pos)) != ']':
        if char in ('', '['):
            return 0
        pos += 2 if char == '\\' else 1
    if not text.text[1:pos].strip() or text.at(pos + 1) != ':':
        return 0
    start = _skip(text, pos + 2, ' \t\n')
    end = _destination_end(text, start)
    url = _ESCAPE.sub(r'\1', text.text[start:end].removeprefix('<').removesuffix('>')).strip()
    if not end or (_UNSAFE_LINK.match(url) and not _IMAGE_DATA_LINK.match(url)):
        return 0
    pos = _skip(text, end, ' \t\n')
    title = _title_end(text, pos) if pos > end else 0
    if title and _line_ends(text, title):
        end = title
    elif (title and title - pos == 2) or not _line_ends(text, end):
        return 0
    return text.text.count('\n', 0, end) + 1


def _paragraph_lines(
    lines: Sequence[str], start: int, containers: Sequence[_Item | None]
) -> Iterator[str]:
    """The text of each line from index `start` on that would continue a paragraph inside
    `containers`, up to the first that would not: a blank line, or a line that starts a block even
    where a list item could not interrupt a paragraph."""
    for index in range(start, len(lines)):
        _, rest = _continued(containers, lines[index])
        if not rest.strip(' '):
            return
        started, opened, _ = _block_starts(rest, continues_paragraph=False, in_paragraph=True)
        if started or opened is not None:
            return
        yield rest


def unclosed_fence_findings(body: str) -> list[tuple[int, str]]:
    """The opening line of a fenced code block still open when `body` ends, as a list of one
    (line number, message), else empty. Lines are numbered in line feeds.

    Follows CommonMark's block structure, reduced to what places a fence: block quotes and list
    items (the containers), paragraphs, link reference definitions, indented code, HTML blocks,
    headings and thematic breaks. Each line first continues the open containers it can, then opens
    new blocks; a line that opens nothing and follows paragraph text continues the paragraph,
    lazily when a container did not continue, and leaves the containers open. A fence inside a
    block quote or a list item is not judged: it ends with its container.

    Where CommonMark parsers differ, the body is read as markdown-it reads it: a link reference
    definition is read where a paragraph would start, over the lines that would continue that
    paragraph, and the line after it starts afresh (cmark and commonmark.js keep the paragraph open
    on that line); a script, file or non-image data URL makes no definition; and an HTML block in
    a list item ends at a blank line indented less than the item's content.
    """
    lines = [_expand_tabs(raw) for raw in _body_lines(body)]
    containers: list[_Item | None] = []  # the open block quotes (None) and list items
    leaf: _Fence | _Html | str | None = None  # the open block the innermost container holds
    definition_ends = 0  # the last line of the link reference definition being read
    for number, line in enumerate(lines, start=1):
        matched, rest = _continued(containers, line)
        all_matched = matched == len(containers)
        if number <= definition_ends:
            if number == definition_ends:
                leaf = None
            continue
        if all_matched and isinstance(leaf, _Fence):
            if _closes(rest, leaf.char, leaf.length):
                leaf = None
            continue
        if all_matched and isinstance(leaf, _Html):
            blank = not rest.strip(' ')
            if (leaf.end.search(rest) if leaf.end else blank) or (
                blank and _short_of_items(containers, line)
            ):
                leaf = None
            continue
        if all_matched and leaf == _INDENTED_CODE and (_spaces(rest) > 3 or not rest.strip(' ')):
            continue
        in_paragraph = leaf == _PARAGRAPH
        started, opened, rest = _block_starts(
            rest,
            continues_paragraph=in_paragraph and all_matched and bool(rest.strip(' ')),
            in_paragraph=in_paragraph,
        )
        if in_paragraph and not started and opened is None and rest.strip(' '):
            continue  # paragraph text; when lazy, the containers it did not continue stay open
        del containers[matched:]
        for container in started:
            _fill(containers)
            containers.append(container)
        if opened is not None or rest.strip(' '):
            _fill(containers)
        if isinstance(opened, tuple):
            leaf = _Fence(number, opened[0], opened[1], nested=bool(containers))
        elif isinstance(opened, _Html):
            leaf = None if opened.end and opened.end.search(rest) else opened
        elif opened == _INDENTED_CODE:
            leaf = _INDENTED_CODE
        elif opened is None and rest.strip(' '):
            leaf = _PARAGRAPH
            if rest.lstrip(' ').startswith('['):
                text = _Text(rest.lstrip(' '), _paragraph_lines(lines, number, containers))
                if size := _definition_lines(text):
                    definition_ends = number + size - 1
                    leaf = None if size == 1 else leaf
        else:
            leaf = None
    if isinstance(leaf, _Fence) and not leaf.nested:
        return [(leaf.number, UNCLOSED_FENCE_MESSAGE)]
    return []


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
