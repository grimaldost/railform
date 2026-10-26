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
  holds, its block structure read as the CommonMark reference implementation reads it, and the
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
# The fence check reads a body's block structure as the CommonMark reference implementation does
# (commonmark.js, as ported to Python in the `commonmark` package 0.9.1). Each block start is
# matched on a line's text from its first character that is not a space or a tab. A fence opens
# with a run of three or more backticks, with no backtick after it on the line, or of three or more
# tildes. It closes on a run of its character at least as long, with nothing after the run but
# spaces or tabs (the reference implementation, written to CommonMark 0.29, takes spaces only; 0.30
# takes both).
_FENCE_OPENER = re.compile(r'`{3,}(?!.*`)|~{3,}')
_FENCE_CLOSER = re.compile(r'(?:`{3,}|~{3,})(?=[ \t]*$)')
_ATX_HEADING = re.compile(r'#{1,6}(?:[ \t]+|$)')
_SETEXT_UNDERLINE = re.compile(r'(?:=+|-+)[ \t]*$')
_THEMATIC_BREAK = re.compile(r'(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})[ \t]*$')
_BULLET = re.compile(r'[*+-]')
_ORDERED = re.compile(r'(\d{1,9})[.)]')
_NON_SPACE = re.compile(r'[^ \t\f\v\r\n]')
# The seven kinds of HTML block, each as (start, end): the end is searched for from the block's
# first line on, and None ends the block before the next blank line. The last kind, a line holding
# only one complete tag, cannot interrupt a paragraph. The block-level tag names are the reference
# implementation's, whose list of headings holds only h1.
_BLOCK_TAGS = (
    'address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|'
    'dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h1|head|header|hr|'
    'html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|'
    'section|source|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul'
)
_ATTRIBUTE = (
    r'\s+[a-zA-Z_:][a-zA-Z0-9:._-]*'  # a name, then an optional value
    r"""(?:\s*=\s*(?:[^"'=<>`\x00-\x20]+|'[^']*'|"[^"]*"))?"""
)
_HTML_BLOCKS: tuple[tuple[re.Pattern[str], re.Pattern[str] | None], ...] = (
    (
        re.compile(r'<(?:script|pre|style)(?:\s|>|$)', re.IGNORECASE),
        re.compile(r'</(?:script|pre|style)>', re.IGNORECASE),
    ),
    (re.compile('<!--'), re.compile('-->')),
    (re.compile(r'<\?'), re.compile(r'\?>')),
    (re.compile('<![A-Z]'), re.compile('>')),
    (re.compile(r'<!\[CDATA\['), re.compile(r'\]\]>')),
    (re.compile(rf'</?(?:{_BLOCK_TAGS})(?:\s|/?>|$)', re.IGNORECASE), None),
    (
        re.compile(
            rf'(?:<[A-Za-z][A-Za-z0-9-]*(?:{_ATTRIBUTE})*\s*/?>|</[A-Za-z][A-Za-z0-9-]*\s*>)\s*$',
            re.IGNORECASE,
        ),
        None,
    ),
)
# A link reference definition's label, and its destination in angle brackets. A backslash escapes
# an ASCII punctuation character, and nothing else.
_LINK_LABEL = re.compile(r'\[(?:[^\\\[\]]|\\.){0,1000}\]')
_BRACKETED_DESTINATION = re.compile(r'<(?:[^<>\n\\\x00]|\\.)*>')
_ESCAPABLE = re.compile(r"""[!"#$%&'()*+,./:;<=>?@[\\\]^_`{|}~-]""")
_SPACES_TO_LINE_END = re.compile(r' *(?:\n|$)')
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


class _Line:
    """A line being read, with the reference implementation's cursor over it: `offset` counts
    characters and `column` counts columns, a tab reaching the next multiple of four. When an
    indent takes only part of a tab, `offset` stays on the tab and its other columns are left for
    the next reader."""

    def __init__(self, text: str) -> None:
        self.text = text.replace('\0', '�')
        self.offset = 0
        self.column = 0
        # Set by `find_next_nonspace`: the first character from `offset` on that is not a space or
        # a tab, its column, the indent before it counted from `column`, and whether the line
        # holds nothing more.
        self.next_nonspace = 0
        self.next_nonspace_column = 0
        self.indent = 0
        self.blank = False

    def at(self, pos: int) -> str:
        """The character at `pos`, or '' past the end of the line."""
        return self.text[pos : pos + 1]

    @property
    def indented(self) -> bool:
        """Whether the indent is four columns or more: indented code, which opens no other block."""
        return self.indent >= 4

    def rest(self) -> str:
        """The line from its next character that is not a space or a tab."""
        return self.text[self.next_nonspace :]

    def find_next_nonspace(self) -> None:
        pos, column, char = self.offset, self.column, self.at(self.offset)
        while char in (' ', '\t'):
            column += 1 if char == ' ' else 4 - column % 4
            pos += 1
            char = self.at(pos)
        self.next_nonspace, self.next_nonspace_column = pos, column
        self.indent = column - self.column
        self.blank = char in ('', '\r')

    def advance_next_nonspace(self) -> None:
        self.offset, self.column = self.next_nonspace, self.next_nonspace_column

    def advance(self, count: int, *, columns: bool) -> None:
        """Move past `count` characters, or past `count` columns when `columns` is set, which may
        take part of a tab."""
        while count > 0 and (char := self.at(self.offset)):
            to_stop = 4 - self.column % 4 if char == '\t' else 1
            if columns and to_stop > count:
                self.column += count
                return
            self.column += to_stop
            self.offset += 1
            count -= to_stop if columns else 1


_QUOTE = 'block quote'
_ITEM = 'list item'
_PARAGRAPH = 'paragraph'
_FENCE = 'fence'
_INDENTED_CODE = 'indented code'
_HTML = 'html block'
_HEADING = 'heading'
_BREAK = 'thematic break'
_CONTAINERS = (_QUOTE, _ITEM)
# The leaf blocks that take every line they continue on as content, so no block starts there.
_CONTENT_BLOCKS = (_FENCE, _INDENTED_CODE, _HTML)


@dataclass
class _Block:
    """An open block: its kind, the line it opens on, and what its kind needs to read later lines.

    A list item has `width`, the columns from its container's content to its own content, and
    `children`, how many blocks it holds; a fence has its marker `char`, its run `length` and the
    `indent` of its opener; an HTML block has its `end` (None: the next blank line ends it); a
    paragraph has its `text`, each line followed by a line feed.
    """

    kind: str
    line: int
    width: int = 0
    children: int = 0
    char: str = ''
    length: int = 0
    indent: int = 0
    end: re.Pattern[str] | None = None
    text: str = ''


def _closes(fence: _Block, line: _Line) -> bool:
    """Whether `line` closes `fence`: at most three columns of indent, then a run of its character
    at least as long as its opener's, with nothing after the run but spaces or tabs."""
    found = _FENCE_CLOSER.match(line.text, line.next_nonspace)
    return (
        line.indent <= 3
        and line.at(line.next_nonspace) == fence.char
        and found is not None
        and len(found[0]) >= fence.length
    )


def _continues(block: _Block, line: _Line) -> bool:
    """Whether `line` continues the open `block`, the cursor moved past the block's marker or
    indent when it does. A fence's closer is read before, by `_closes`."""
    if block.kind == _QUOTE:
        if line.indented or line.at(line.next_nonspace) != '>':
            return False
        line.advance_next_nonspace()
        line.advance(1, columns=False)
        if line.at(line.offset) in (' ', '\t'):
            line.advance(1, columns=True)
        return True
    if block.kind == _ITEM:
        if line.blank and not block.children:
            return False  # an item that holds nothing yet ends at a blank line
        if line.blank:
            line.advance_next_nonspace()
        elif line.indent >= block.width:
            line.advance(block.width, columns=True)
        else:
            return False
        return True
    if block.kind == _FENCE:
        for _ in range(block.indent):
            if line.at(line.offset) not in (' ', '\t'):
                break
            line.advance(1, columns=True)
        return True
    if block.kind == _INDENTED_CODE:
        if line.indented:
            line.advance(4, columns=True)
        elif line.blank:
            line.advance_next_nonspace()
        else:
            return False
        return True
    if block.kind == _HTML:
        return not (line.blank and block.end is None)
    return block.kind == _PARAGRAPH and not line.blank  # a heading or a break takes one line


def _list_item(line: _Line, number: int, *, interrupts: bool) -> _Block | None:
    """The list item that starts on `line`, the cursor moved to its content, else None.

    `interrupts`: the line would otherwise continue a paragraph, so the item must hold something
    on its first line and, when ordered, start at 1.
    """
    if line.indented:
        return None
    rest = line.rest()
    found = _BULLET.match(rest)
    if found is None:
        found = _ORDERED.match(rest)
        if found is None or (interrupts and found[1] != '1'):
            return None
    marker = len(found[0])
    if line.at(line.next_nonspace + marker) not in ('', ' ', '\t'):
        return None
    if interrupts and not _NON_SPACE.search(rest, marker):
        return None
    width = line.indent + marker
    line.advance_next_nonspace()
    line.advance(marker, columns=True)
    start_column, start_offset = line.column, line.offset
    while True:
        line.advance(1, columns=True)
        if line.column - start_column >= 5 or line.at(line.offset) not in (' ', '\t'):
            break
    spaces = line.column - start_column
    if spaces >= 5 or spaces < 1 or not line.at(line.offset):
        # Nothing on the line, or indented code after the marker: the content starts one column
        # after the marker.
        line.column, line.offset = start_column, start_offset
        if line.at(line.offset) in (' ', '\t'):
            line.advance(1, columns=True)
        spaces = 1
    return _Block(_ITEM, number, width=width + spaces)


def _past_spaces_and_a_line_end(text: str, pos: int) -> int:
    """`pos` moved past spaces, at most one line feed and the spaces after it. A tab stops it."""
    while text[pos : pos + 1] == ' ':
        pos += 1
    if text[pos : pos + 1] == '\n':
        pos += 1
        while text[pos : pos + 1] == ' ':
            pos += 1
    return pos


def _destination_end(text: str, pos: int) -> int | None:
    """The end of the link destination that starts at `pos`, else None: text in angle brackets on
    one line, or a run up to a space, a tab, a line end or a `)` that closes no `(`. A backslash
    escapes only an ASCII punctuation character, so before a line end it is part of the
    destination, and the line end still ends it."""
    if found := _BRACKETED_DESTINATION.match(text, pos):
        return found.end()
    if text[pos : pos + 1] == '<':
        return None
    start, depth, char = pos, 0, text[pos : pos + 1]
    while char:
        if char == '\\' and _ESCAPABLE.match(text, pos + 1):
            pos += 2
        elif char == '(':
            depth += 1
            pos += 1
        elif char == ')' and depth:
            depth -= 1
            pos += 1
        elif char == ')' or char in ' \t\n\v\f\r':
            break
        else:
            pos += 1
        char = text[pos : pos + 1]
    return None if pos == start and char != ')' else pos


def _title_end(text: str, pos: int) -> int | None:
    """The end of the link title that starts at `pos`, else None: text in double quotes, in single
    quotes, or in parentheses without an unescaped `(`.

    A backslash before a punctuation character can be read as an escape or as itself, and the end
    is the one the reference implementation's regular expression finds first: escapes first, then
    backtracking. It is computed here from the end back, so a long title costs no backtracking.
    """
    closer = {'"': '"', "'": "'", '(': ')'}.get(text[pos : pos + 1])
    if closer is None:
        return None
    stops = '()\0' if closer == ')' else closer + '\0'  # never title text
    # Every reading of the title reaches the first stop that no backslash precedes, so the title
    # ends there at the latest.
    limit = pos + 1
    while limit < len(text) and (text[limit] not in stops or text[limit - 1] == '\\'):
        limit += 1
    ends = [-1] * (limit - pos + 2)  # ends[k - pos]: the end when the title's text resumes at k
    if text[limit : limit + 1] == closer:
        ends[limit - pos] = limit + 1
    for k in range(limit - 1, pos, -1):
        char = text[k]
        if char == closer:
            ends[k - pos] = k + 1
        elif char == '\\' and _ESCAPABLE.match(text, k + 1) and ends[k + 2 - pos] >= 0:
            ends[k - pos] = ends[k + 2 - pos]
        elif char not in stops:
            ends[k - pos] = ends[k + 1 - pos]
    return ends[1] if ends[1] >= 0 else None


def _definition_end(text: str, start: int) -> int:
    """The end of the link reference definition at `start` of a paragraph's `text`, else 0.

    A label, a colon, a destination and an optional title, with spaces and at most one line end
    between the parts (a tab ends a definition), and nothing after it on its last line but spaces.
    A title followed by other text is dropped, leaving the definition to end with its destination,
    unless the title is empty.
    """
    label = _LINK_LABEL.match(text, start)
    if label is None or len(label[0]) == 2 or len(label[0]) > 1001 or not label[0][1:-1].strip():
        return 0
    if text[label.end() : label.end() + 1] != ':':
        return 0
    destination = _destination_end(text, _past_spaces_and_a_line_end(text, label.end() + 1))
    if destination is None:
        return 0
    spaced = _past_spaces_and_a_line_end(text, destination)
    title = _title_end(text, spaced) if spaced > destination else None
    if title is not None and (found := _SPACES_TO_LINE_END.match(text, title)):
        return found.end()
    if title is not None and title - spaced == 2:
        return 0
    found = _SPACES_TO_LINE_END.match(text, destination)
    return found.end() if found else 0


def _without_definitions(text: str) -> str:
    """A paragraph's `text` without the link reference definitions it starts with."""
    start = 0
    while text.startswith('[', start) and (end := _definition_end(text, start)):
        start = end
    return text[start:]


class _Blocks:
    """The open blocks of a body, outermost first, read line by line as the reference
    implementation's block parser reads them. The document that holds them and the lists that
    hold list items are left implicit: a list always continues and holds only list items, so
    neither moves a fence."""

    def __init__(self) -> None:
        self.open: list[_Block] = []
        self._matched = 0  # how many open blocks the line being read continues
        self._unmatched_closed = True

    def read(self, line: _Line, number: int) -> None:
        """Read one line: continue the open blocks it can, open the blocks that start on it, and
        give the rest of it to the block that takes it."""
        self._matched = 0
        for block in self.open:
            line.find_next_nonspace()
            if block.kind == _FENCE and _closes(block, line):
                self.open.pop()
                return
            if not _continues(block, line):
                break
            self._matched += 1
        self._unmatched_closed = self._matched == len(self.open)
        container = self._open_blocks(
            line, number, self.open[self._matched - 1] if self._matched else None
        )
        tip = self.open[-1] if self.open else None
        if (
            tip is not None
            and tip.kind == _PARAGRAPH
            and not (self._unmatched_closed or line.blank)
        ):
            tip.text += line.text[line.offset :] + '\n'  # a lazy continuation line
            return
        self._close_unmatched()
        kind = container.kind if container is not None else None
        if container is not None and kind == _PARAGRAPH:
            container.text += line.text[line.offset :] + '\n'
        elif container is not None and kind == _HTML:
            if container.end is not None and container.end.search(line.text, line.offset):
                self._close_top()
        elif kind not in _CONTENT_BLOCKS and line.offset < len(line.text) and not line.blank:
            paragraph = self._add(_Block(_PARAGRAPH, number))
            line.advance_next_nonspace()
            paragraph.text = line.text[line.offset :] + '\n'

    def _open_blocks(self, line: _Line, number: int, container: _Block | None) -> _Block | None:
        """Open the blocks that start on `line` inside `container`, trying each start in the
        reference implementation's order, and return the block the rest of the line belongs to."""
        while container is None or container.kind not in _CONTENT_BLOCKS:
            line.find_next_nonspace()
            rest = line.rest()
            starts = not line.indented
            paragraph = (
                container if container is not None and container.kind == _PARAGRAPH else None
            )
            if starts and rest.startswith('>'):
                line.advance_next_nonspace()
                line.advance(1, columns=False)
                if line.at(line.offset) in (' ', '\t'):
                    line.advance(1, columns=True)
                container = self._add(_Block(_QUOTE, number))
                continue
            if starts and _ATX_HEADING.match(rest):
                line.offset = len(line.text)
                return self._add(_Block(_HEADING, number))
            if starts and (found := _FENCE_OPENER.match(rest)):
                fence = _Block(
                    _FENCE, number, char=rest[0], length=len(found[0]), indent=line.indent
                )
                return self._add(fence)
            if starts and rest.startswith('<'):
                for kind, (start, end) in enumerate(_HTML_BLOCKS, start=1):
                    if start.match(rest) and (kind < 7 or paragraph is None):
                        return self._add(_Block(_HTML, number, end=end))
            if starts and paragraph is not None and _SETEXT_UNDERLINE.match(rest):
                # A paragraph made only of link reference definitions takes no underline, and
                # the line is read on like any other.
                paragraph.text = _without_definitions(paragraph.text)
                if paragraph.text:
                    self.open[-1] = _Block(_HEADING, paragraph.line)
                    line.offset = len(line.text)
                    return self.open[-1]
            if starts and _THEMATIC_BREAK.match(rest):
                line.offset = len(line.text)
                return self._add(_Block(_BREAK, number))
            if item := _list_item(line, number, interrupts=paragraph is not None):
                container = self._add(item)
                continue
            tip = self.open[-1] if self.open else None
            if line.indented and not line.blank and (tip is None or tip.kind != _PARAGRAPH):
                line.advance(4, columns=True)
                return self._add(_Block(_INDENTED_CODE, number))
            line.advance_next_nonspace()
            return container
        return container

    def _add(self, block: _Block) -> _Block:
        """Open `block` in the innermost open container, after closing the blocks the line did
        not continue and the leaf block in the way."""
        self._close_unmatched()
        while self.open and self.open[-1].kind not in _CONTAINERS:
            self._close_top()
        if self.open:
            self.open[-1].children += 1
        self.open.append(block)
        return block

    def _close_unmatched(self) -> None:
        """Close the blocks the line being read did not continue, once it opens a block or
        turns out not to be a lazy continuation line."""
        if not self._unmatched_closed:
            while len(self.open) > self._matched:
                self._close_top()
            self._unmatched_closed = True

    def _close_top(self) -> None:
        """Close the innermost open block. A paragraph made only of link reference definitions
        leaves its container, so a list item it was the only block of holds nothing again."""
        block = self.open.pop()
        if block.kind == _PARAGRAPH and self.open and self.open[-1].kind == _ITEM:
            rest = _without_definitions(block.text)
            if rest != block.text and not _NON_SPACE.search(rest):
                self.open[-1].children -= 1


def unclosed_fence_findings(body: str) -> list[tuple[int, str]]:
    """The opening line of a fenced code block still open when `body` ends, as a list of one
    (line number, message), else empty. Lines are numbered in line feeds.

    The body's block structure is read as the CommonMark reference implementation reads it:
    block quotes and list items (the containers), paragraphs with their lazy continuation lines,
    the link reference definitions a paragraph starts with (read when it meets a setext underline
    and when it ends), indented code, the seven kinds of HTML block, headings and thematic breaks.
    A fence inside a block quote or a list item is not judged: it ends with its container. The one
    departure: a closer may be followed by tabs as well as spaces, as CommonMark 0.30 has it.
    """
    blocks = _Blocks()
    for number, text in enumerate(_body_lines(body), start=1):
        blocks.read(_Line(text), number)
    if blocks.open and blocks.open[0].kind == _FENCE:
        return [(blocks.open[0].line, UNCLOSED_FENCE_MESSAGE)]
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
