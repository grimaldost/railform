"""The verb `railform version-sites [--bump X.Y.Z [--prefix PREFIX]]`: the version-site registry.

Development-rails spec, §15; Wave C interface contract, sections 2 and 4. It ports keel's
version-site registry and its bump script (https://github.com/grimaldost/keel, `VERSION_SITES`,
`read_versions` and `disagreement` in `tests/test_plugin_manifest.py`, and `bump` in
`scripts/bump_version.py`, at c3313fd) with the registry read from the frame (`version.sites`)
instead of a constant, and the sites grouped into release lines.

A site is `{path, pattern, authored}`: the file at `path`, a Python regular expression whose first
group is the version written there, and whether the value is authored. An authored site (the
newest changelog heading, which arrives with its own section) is checked and never rewritten, as
a writer that rewrote it would rename the previous release.

The check judges each release line apart (a line is a `release.tag_prefix` row and the paths under
its prefix): the sites of a line agree when they read the value most of them read, and a site that
reads another value, or none, fails. `--bump` writes the version to the writable sites of one line
and then runs the same check, so a bump that leaves an authored heading behind still fails.

This module also owns the reading of release lines and version sites that the changelog gate and
the release check share (`release_lines`, `line_of`, `line_name`, `version_sites`, `site_value`,
`read_site`, `reading_of`, `triple`), so the repository keeps one reading of them. `reading_of`
is the reading over bytes, for a verb that reads a site at a git revision.

A site's pattern is the repository's, and the file is read with no newline translation: a group
that can reach the end of a line (`(.*)$`) captures a `\\r` in a CRLF file, so the site reads
another value there. Write the group so that it stops before the line end (`([0-9.]+)`).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from railform import frame as frame_module
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterable
    from pathlib import Path

VERB = 'version-sites'
UNREADABLE = 'unreadable'
MISMATCH = 'mismatch'
# When several rules fail, the verdict names the first in this order.
CODE_ORDER = (UNREADABLE, MISMATCH)

_VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+', re.ASCII)
_TRIPLE = re.compile(r'([0-9]+)\.([0-9]+)\.([0-9]+)', re.ASCII)

CONFIG_ERRORS = (frame_module.FrameError, frame_module.FrameSchemaError)


class SitesError(Exception):
    """A release line or a version site that the frame states wrongly: a configuration error."""


@dataclass(frozen=True)
class Line:
    """One release line: a `release.tag_prefix` row, whose name only `release-check` uses."""

    prefix: str
    tag: str

    @property
    def name(self) -> str:
        return line_name(self.prefix)


@dataclass(frozen=True)
class Site:
    """One `version.sites` row, validated, with the release line its path belongs to."""

    path: str
    pattern: re.Pattern[str]
    authored: bool
    line: Line


@dataclass(frozen=True)
class Failure:
    """A site that fails the check: the rule, the site's path and the finding line."""

    code: str
    path: str
    message: str


@dataclass(frozen=True)
class Reading:
    """What a site reads in the working tree: a value, or why it reads none."""

    value: str | None
    why: str = ''
    text: str | None = None


# --- release lines (contract section 2) ---------------------------------------------------------


def line_name(prefix: str) -> str:
    """The last non-empty `/`-separated segment of a prefix: `plugins/humblepowers/` gives
    `humblepowers`; the empty prefix has no name."""
    segments = [part for part in prefix.split('/') if part]
    return segments[-1] if segments else ''


def _duplicates(prefixes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for prefix in prefixes:
        if prefix in seen and prefix not in repeated:
            repeated.append(prefix)
        seen.add(prefix)
    return repeated


def release_lines(frame: dict[str, Any]) -> list[Line]:
    """The release lines of the effective frame, in `release.tag_prefix` order.

    Raises SitesError when two `release.tag_prefix` rows, or two `changelog.records` rows, share a
    prefix.
    """
    rows = frame['release']['tag_prefix']
    for label, prefixes in (
        ('release.tag_prefix', [row['prefix'] for row in rows]),
        ('changelog.records', [row['prefix'] for row in frame['changelog']['records']]),
    ):
        repeated = _duplicates(prefixes)
        if repeated:
            shown = ', '.join(repr(prefix) for prefix in repeated)
            raise SitesError(f'{label} has more than one row with the prefix {shown}')
    return [Line(prefix=row['prefix'], tag=row['tag']) for row in rows]


def line_of(lines: Iterable[Line], path: str) -> Line | None:
    """The line a repository-relative POSIX path belongs to: the row with the longest prefix
    that is a plain, case-sensitive prefix of it, or None when no row's prefix is."""
    best: Line | None = None
    for line in lines:
        if path.startswith(line.prefix) and (best is None or len(line.prefix) > len(best.prefix)):
            best = line
    return best


# --- version sites (contract sections 4.1 and 4.2) ----------------------------------------------


def _site_path(path: str) -> str:
    if not path:
        raise SitesError('a version site has an empty path')
    if '\\' in path:
        raise SitesError(f'version site path {path!r} holds a backslash')
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
        raise SitesError(f'version site path {path!r} is absolute')
    if PureWindowsPath(path).drive:
        raise SitesError(f'version site path {path!r} names a drive')
    if '..' in path.split('/'):
        raise SitesError(f'version site path {path!r} holds a ".." segment')
    return path


def version_sites(frame: dict[str, Any], lines: list[Line]) -> list[Site]:
    """The `version.sites` rows of the effective frame, validated, in order.

    Raises SitesError for a path that is empty, absolute, holds `..` or a backslash, a pattern
    that does not compile or has no group, and a path that no release line covers.
    """
    sites: list[Site] = []
    for row in frame['version']['sites']:
        path = _site_path(row['path'])
        try:
            pattern = re.compile(row['pattern'])
        except re.error as exc:
            raise SitesError(f'{path}: the pattern is not a regular expression: {exc}') from exc
        if pattern.groups < 1:
            raise SitesError(f'{path}: the pattern has no group, so it names no version')
        line = line_of(lines, path)
        if line is None:
            raise SitesError(f'{path}: no release.tag_prefix row covers this version site')
        sites.append(Site(path, pattern, bool(row.get('authored', False)), line))
    return sites


def site_value(pattern: re.Pattern[str], text: str) -> str | None:
    """Group 1 of the pattern's first match in `text`, or None when nothing matches or the group
    matched nothing (empty text is no version)."""
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1) or None


def triple(value: str | None) -> tuple[int, int, int] | None:
    """`X.Y.Z` as three integers, or None for anything that is not three dot-separated integers."""
    if value is None:
        return None
    match = _TRIPLE.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def read_site(root: Path, site: Site) -> Reading:
    """What the site's file in the working tree reads.

    The file is read as bytes and decoded as UTF-8 with no newline translation. A missing file, a
    file that is not UTF-8 and a pattern that matches nothing leave the site unreadable.
    """
    file = root / site.path
    try:
        data = file.read_bytes() if file.is_file() else None
    except OSError:
        data = None
    return reading_of(data, site)


def reading_of(data: bytes | None, site: Site) -> Reading:
    """What `data`, the bytes of the site's file, reads; `None` stands for a missing file.

    The reading for a verb that gets the bytes some other way, such as `git show <rev>:<path>`.
    The text is decoded as UTF-8 with no newline translation, and the pattern is the
    repository's: a group that reaches the end of a line captures any `\\r` there.
    """
    if data is None:
        return Reading(None, 'the file is missing')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return Reading(None, 'the file is not UTF-8')
    match = site.pattern.search(text)
    if match is None:
        return Reading(None, 'the pattern matches nothing', text)
    value = match.group(1)
    if not value:
        return Reading(None, "the pattern's first group matched no text", text)
    return Reading(value, text=text)


# --- the check (contract section 4.3) -----------------------------------------------------------


def expected_value(values: Iterable[str]) -> str | None:
    """The value most sites read; on a tie, the earliest of them (keel's `most_common`)."""
    tally = Counter(values)
    return tally.most_common(1)[0][0] if tally else None


def check(root: Path, lines: list[Line], sites: list[Site]) -> list[Failure]:
    """Judge every line apart; return the failures in site order."""
    readings = [read_site(root, site) for site in sites]
    expected: dict[str, str | None] = {}
    for line in lines:
        expected[line.prefix] = expected_value(
            reading.value
            for site, reading in zip(sites, readings, strict=True)
            if site.line.prefix == line.prefix and reading.value is not None
        )
    failures: list[Failure] = []
    for site, reading in zip(sites, readings, strict=True):
        if reading.value is None:
            message = f'{site.path}: no version ({reading.why})'
            failures.append(Failure(UNREADABLE, site.path, message))
            continue
        wanted = expected[site.line.prefix]
        if reading.value != wanted:
            note = ' (authored: write it by hand)' if site.authored else ''
            message = f'{site.path}: reads {reading.value}, not {wanted}{note}'
            failures.append(Failure(MISMATCH, site.path, message))
    return failures


# --- --bump (contract section 4.4) ---------------------------------------------------------------


def _writable_prefixes(sites: list[Site]) -> list[str]:
    prefixes: list[str] = []
    for site in sites:
        if not site.authored and site.line.prefix not in prefixes:
            prefixes.append(site.line.prefix)
    return prefixes


def _chosen_prefix(requested: str | None, lines: list[Line], sites: list[Site]) -> str | None:
    """The prefix of the line `--bump` writes; None when there is no line to write.

    Raises SitesError when `--prefix` names no `release.tag_prefix` row, or is missing while more
    than one line holds a writable site.
    """
    row_prefixes = [line.prefix for line in lines]
    if requested is not None:
        if requested not in row_prefixes:
            raise SitesError(
                f'--prefix {requested!r} is not the prefix of a release.tag_prefix row; '
                f'the prefixes are {_show(row_prefixes)}'
            )
        return requested
    writable = _writable_prefixes(sites)
    if len(writable) > 1:
        raise SitesError(
            f'--prefix is required: {len(writable)} release lines hold a writable version '
            f'site; the prefixes are {_show(writable)}'
        )
    return writable[0] if writable else None


def _show(prefixes: list[str]) -> str:
    return ', '.join(repr(prefix) for prefix in prefixes)


def bump(root: Path, sites: list[Site], prefix: str | None, version: str) -> list[str]:
    """Write `version` to the non-authored sites of the line `prefix`, replacing the span of the
    pattern's first group and nothing else; return one finding per write.

    An authored site and an unreadable one are never written. A site that reads `version`
    already is left alone, so a second identical bump writes nothing.
    """
    moved: list[str] = []
    if prefix is None:
        return moved
    for site in sites:
        if site.authored or site.line.prefix != prefix:
            continue
        # Read afresh: an earlier site of the same file may have just moved.
        reading = read_site(root, site)
        if reading.value is None or reading.text is None or reading.value == version:
            continue
        match = site.pattern.search(reading.text)
        if match is None:  # read_site found a value, so the pattern matches
            continue
        start, end = match.span(1)
        text = reading.text[:start] + version + reading.text[end:]
        try:
            (root / site.path).write_bytes(text.encode('utf-8'))
        except OSError as exc:
            written = ', '.join(entry.split(': ', 1)[0] for entry in moved) or 'none'
            raise SitesError(
                f'cannot write {site.path}: {exc.strerror}; sites written before it: {written}'
            ) from exc
        moved.append(f'{site.path}: {reading.value} -> {version}')
    return moved


def _authored_left(
    root: Path, sites: list[Site], prefix: str | None, version: str, reported: set[str]
) -> list[str]:
    """Findings for the authored sites of the bumped line that still read another value and that
    the check did not already report."""
    notes: list[str] = []
    for site in sites:
        if not site.authored or site.line.prefix != prefix or site.path in reported:
            continue
        value = read_site(root, site).value
        if value is not None and value != version:
            notes.append(
                f'{site.path}: reads {value}, the bump target is {version} '
                '(authored: write it by hand)'
            )
    return notes


# --- the verb -----------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Check that the version sites of version.sites agree within each release line. '
        "With --bump, first write the version to the line's writable sites; an authored site "
        '(the newest changelog heading) is checked and never rewritten.'
    )
    parser.add_argument(
        '--bump',
        metavar='X.Y.Z',
        help='write this version to the non-authored sites of one release line, then check',
    )
    parser.add_argument(
        '--prefix',
        metavar='PREFIX',
        help=(
            'with --bump, the release.tag_prefix row (its prefix, exactly) whose sites to write; '
            'required when more than one line has a writable site'
        ),
    )


def run(args: argparse.Namespace) -> Verdict:
    version = args.bump
    if version is not None and _VERSION.fullmatch(version) is None:
        return Verdict.usage(VERB, f'{VERB}: --bump takes X.Y.Z, three dot-separated integers')
    if args.prefix is not None and version is None:
        return Verdict.usage(VERB, f'{VERB}: --prefix is only meaningful with --bump')
    try:
        root = frame_module.repository_root()
        frame = frame_module.load(root)
        lines = release_lines(frame)
        sites = version_sites(frame, lines)
        prefix = _chosen_prefix(args.prefix, lines, sites) if version is not None else None
    except (*CONFIG_ERRORS, SitesError) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    findings: list[str] = []
    if version is not None:
        try:
            findings += bump(root, sites, prefix, version)
        except SitesError as exc:
            return Verdict.usage(VERB, f'{VERB}: {exc}')
    failures = check(root, lines, sites)
    if not sites:
        findings.append('no version sites are declared in version.sites; nothing to check')
    if version is not None:
        findings += _authored_left(root, sites, prefix, version, {f.path for f in failures})
    if not failures:
        return Verdict.ok(VERB, tuple(findings))
    code = min((failure.code for failure in failures), key=CODE_ORDER.index)
    return Verdict.fail(VERB, code, (*findings, *(failure.message for failure in failures)))
