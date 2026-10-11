"""The commit lane: the shared logic of `commit-msg`, `commit-range` and `pr-text`.

Development-rails spec, §4. It ports two public sources into frame-driven checks:

- keel (https://github.com/grimaldost/keel), the no-ai-attribution hook of
  `.pre-commit-config.yaml` and `tests/test_commit_msg_lane.py` at c3313fd. The pattern set now
  lives in `commit.attribution_patterns` of the frame.
- mantis-research (https://github.com/grimaldost/mantis-research),
  `scripts/check_commit_message.py` at bbcbc6c, the script form that reads a message file and
  names each offending line.

Two rules:

- **Subject.** The first line of a commit message reads `type(scope)!: description`, the scope
  and the `!` optional, the type one of `commit.types`. A commit with more than one parent is
  exempt: git writes its subject. The local `commit-msg` hook also accepts an autosquash
  subject (`fixup! `, `squash! ` or `amend! ` before a valid subject), so `git commit --fixup`
  works while a branch is in progress; `commit-range` refuses it, so it never reaches the base
  branch (development-rails spec, §13, the keel pilot).
- **Attribution.** No line of any message, merges included, matches a pattern of
  `commit.attribution_patterns` (Python `re`, case-insensitive, per line). The pull request's
  title and body are held to the same patterns.

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
# What follows the comment character in the line `git commit -v` writes before the diff it
# appends to the message template.
SCISSORS_TAIL = ' ------------------------ >8 ------------------------'
DEFAULT_COMMENT_CHAR = '#'
SUBJECT_MESSAGE = 'the subject is not "type(scope)!: description" with a type of the frame'
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


def verdict(verb: str, subject: Sequence[str], attribution: Sequence[str]) -> Verdict:
    """The verdict of a verb: the subject rule is named before the attribution rule."""
    findings = (*subject, *attribution)
    if subject:
        return Verdict.fail(verb, SUBJECT, findings)
    if attribution:
        return Verdict.fail(verb, ATTRIBUTION, findings)
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
