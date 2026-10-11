"""The verb `railform release-check`: every release tag is annotated, sits on the merge commit of
the pull request that released it, and that release's `release:` commits change only release
metadata.

Development-rails spec, §16; interface contract (Wave C), section 5. The annotated-tag rule is
ported from keel (https://github.com/grimaldost/keel), `tests/test_release_flow.py` at c3313fd
(`test_release_tags_are_annotated` and its `TAG_SHAPE`); the release pull request's
identification, the placement rule and the release-commit rule are invented here.

Which tags are judged: a tag maps to the `release.tag_prefix` row whose `tag` prefixes its name
with exactly `X.Y.Z` after it (the longest such `tag` wins). A mapped tag is judged when its
version is at or above its row's floor: the row's own `since`, else `release.since`, both tag
names of that row. Tags are judged anew on every run, so the daily run of the tag watch also
stands for immutability: a tag moved off its release merge commit fails the next day.

Per judged tag of version V on a release line named N (the last segment of the row's prefix):

- `lightweight-tag`: `git cat-file -t` does not say `tag`;
- `no-release-pr`: no merged pull request is a release pull request of the tag, that is, one
  whose head branch starts with `release/` or whose title is a `release` subject, and which
  names V as its first version and, on a named line, N as a name token;
- `tag-placement`: the tag's commit is not the merge commit of any of its release pull requests;
- `release-commit`: a commit the tag's commit brings in (`git rev-list <tag>^1..<tag>`) with at
  most one parent, whose subject is a `release` subject, changes a path outside the release
  metadata (`release.metadata_paths` globs, the `changelog.records` files and the
  `version.sites` paths).

The pull requests come from the GitHub API (`$GITHUB_REPOSITORY`, `$GITHUB_API_URL`, an optional
`$GITHUB_TOKEN`), or, when `$RAILFORM_PULLS_FILE` names a file, from that file, with no network.
An API that cannot be read exits 2 with `api-error`.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from railform import frame as frame_module
from railform.hooks_check import GitError, git
from railform.verdict import API_ERROR_CODE, Verdict
from railform.version_sites import Line, Site, SitesError, release_lines, triple, version_sites

if TYPE_CHECKING:
    import argparse

VERB = 'release-check'
LIGHTWEIGHT_TAG = 'lightweight-tag'
NO_RELEASE_PR = 'no-release-pr'
TAG_PLACEMENT = 'tag-placement'
RELEASE_COMMIT = 'release-commit'
# When several rules fail, the verdict names the first in this order.
CODE_ORDER = (LIGHTWEIGHT_TAG, NO_RELEASE_PR, TAG_PLACEMENT, RELEASE_COMMIT)

PULLS_FILE_ENV = 'RAILFORM_PULLS_FILE'
DEFAULT_API = 'https://api.github.com'
PAGE_SIZE = 100
TIMEOUT_SECONDS = 30

# The `release` type of the commit lane, any scope, with or without `!`.
RELEASE_SUBJECT = re.compile(r'^release(\([^)]*\))?!?: ')
RELEASE_BRANCH = 'release/'
_REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')

Version = tuple[int, int, int]


class ConfigError(Exception):
    """A configuration or usage error: exit 2 with `usage`."""


class ApiError(Exception):
    """The GitHub API could not be read: exit 2 with `api-error`."""


# --- release lines and floors ----------------------------------------------------------------
#
# The reading of release lines is `version_sites.py`'s (interface contract, Wave C, sections 1.1,
# 2 and 11.3); this module adds each line's floor and maps tags to lines.


@dataclass(frozen=True)
class Floored:
    """A release line and its floor: the row's own `since`, else `release.since` ("" for none)."""

    line: Line
    floor: str


def version_of(line: Line, tag: str) -> Version | None:
    """The version a tag names through `line`: the line's tag, then exactly `X.Y.Z`."""
    if not tag.startswith(line.tag):
        return None
    return triple(tag[len(line.tag) :])


def floored_lines(frame: dict[str, Any]) -> list[Floored]:
    """The release lines of the frame with their floors.

    Raises SitesError on two rows with one prefix (the shared reading), and ConfigError on a
    floor that is not a tag of its own row.
    """
    lines = release_lines(frame)
    floored: list[Floored] = []
    for line, row in zip(lines, frame['release']['tag_prefix'], strict=True):
        floor = row.get('since') or frame['release']['since']
        if floor and version_of(line, floor) is None:
            where = 'its since' if row.get('since') else 'release.since'
            prefix, tag = json.dumps(line.prefix), json.dumps(line.tag)
            raise ConfigError(
                f'release.tag_prefix row {prefix} (tag {tag}): {where} {json.dumps(floor)} is '
                f'not a tag of this row ({line.tag}X.Y.Z)'
            )
        floored.append(Floored(line, floor))
    return floored


def map_tag(lines: list[Floored], tag: str) -> tuple[Floored, Version] | None:
    """The line a tag belongs to and its version: the row with the longest fitting `tag`."""
    best: tuple[Floored, Version] | None = None
    for item in lines:
        version = version_of(item.line, tag)
        if version is not None and (best is None or len(item.line.tag) > len(best[0].line.tag)):
            best = (item, version)
    return best


# --- release pull requests ---------------------------------------------------------------------


@dataclass(frozen=True)
class Pull:
    """The fields of a merged pull request the check reads."""

    number: object
    title: str
    head: str
    merge_commit: str


def pulls_from(data: object) -> list[Pull]:
    """The merged pull requests of a list in the API's shape; ValueError on a bad shape."""
    if not isinstance(data, list):
        raise ValueError('not a JSON array')
    pulls: list[Pull] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f'entry {index} is not an object')
        head = entry.get('head')
        title = entry.get('title')
        ref = head.get('ref') if isinstance(head, dict) else None
        if not isinstance(title, str) or not isinstance(ref, str):
            raise ValueError(f'entry {index} has no string title and head.ref')
        if not entry.get('merged_at'):
            continue
        merge_commit = entry.get('merge_commit_sha')
        pulls.append(
            Pull(
                entry.get('number'),
                title,
                ref,
                merge_commit if isinstance(merge_commit, str) else '',
            )
        )
    return pulls


# A version-shaped token: X.Y.Z not preceded by a digit or `.`, and not followed by a digit or by
# `.` and a digit, so `release/0.2.01` holds 0.2.01, never 0.2.0.
_VERSION_TOKEN = re.compile(r'(?<![0-9.])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9]|\.[0-9])')
# A pre-release or build suffix: the token it follows is not a release version.
_SUFFIX = re.compile(r'[-+][A-Za-z0-9]')


def _has_version(text: str, version: str) -> bool:
    """V is the first version-shaped token of `text`, with no pre-release or build suffix:
    `release/0.2.0` names 0.2.0; `release/1.0.0-rc1` and `release: 1.1.0 (from 1.0.0)` do not
    name 1.0.0 (interface contract, Wave C, section 11.4)."""
    first = _VERSION_TOKEN.search(text)
    if first is None or first.group(1) != version:
        return False
    return _SUFFIX.match(text, first.end()) is None


def _has_name(text: str, name: str) -> bool:
    """N occurs not preceded or followed by an ASCII letter or digit."""
    return re.search(rf'(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])', text) is not None


def _carries(text: str, version: str, name: str) -> bool:
    return _has_version(text, version) and (not name or _has_name(text, name))


def is_release_pull(pull: Pull, version: str, name: str) -> bool:
    """Whether a merged pull request released `version` of the line named `name`."""
    by_branch = pull.head.startswith(RELEASE_BRANCH) and _carries(pull.head, version, name)
    by_title = RELEASE_SUBJECT.match(pull.title) is not None and _carries(pull.title, version, name)
    return by_branch or by_title


def read_pulls_file(path: str) -> list[Pull]:
    try:
        data = json.loads(Path(path).read_bytes().decode('utf-8'))
        return pulls_from(data)
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f'${PULLS_FILE_ENV}: cannot read the pull requests file: {exc}') from exc
    except ValueError as exc:
        raise ConfigError(f'${PULLS_FILE_ENV}: not a list of pull requests: {exc}') from exc


def fetch_pulls() -> list[Pull]:
    """Every closed pull request of `$GITHUB_REPOSITORY`, page by page; the merged ones."""
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    if not _REPOSITORY.fullmatch(repository):
        raise ConfigError(
            '$GITHUB_REPOSITORY must name the repository as owner/name to read its pull requests '
            f'(or set ${PULLS_FILE_ENV} to a saved list)'
        )
    api = (os.environ.get('GITHUB_API_URL') or DEFAULT_API).rstrip('/')
    if urllib.parse.urlsplit(api).scheme not in ('http', 'https'):
        raise ConfigError('$GITHUB_API_URL must be an http or https URL')
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'railform',
    }
    token = os.environ.get('GITHUB_TOKEN', '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    entries: list[object] = []
    page = 1
    while True:
        url = f'{api}/repos/{repository}/pulls?state=closed&per_page={PAGE_SIZE}&page={page}'
        batch = _get_page(url, headers)
        entries += batch
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    try:
        return pulls_from(entries)
    except ValueError as exc:
        raise ApiError(f'the pull requests the API returned are not in its shape: {exc}') from exc


class SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect as urllib does, but drops the token when it leads to another host or
    scheme, so the token never leaves the API's host."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and _origin(newurl) != _origin(req.full_url):
            new.remove_header('Authorization')
        return new


def _origin(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower()


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SameHostRedirects())


def _get_page(url: str, headers: dict[str, str]) -> list[object]:
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (the scheme is checked)
    try:
        with _opener().open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise ApiError(f'GET {url}: HTTP status {exc.code}') from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, 'reason', exc)
        raise ApiError(f'GET {url}: no response ({reason})') from None
    if not 200 <= status < 300:
        raise ApiError(f'GET {url}: HTTP status {status}')
    try:
        data = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(f'GET {url}: HTTP status {status}, but the body is not JSON') from None
    if not isinstance(data, list):
        raise ApiError(f'GET {url}: HTTP status {status}, but the body is not a JSON array')
    return data


# --- git ---------------------------------------------------------------------------------------


def _git_text(root: Path, *args: str) -> str:
    done = git(root, *args)
    if done.returncode != 0:
        detail = done.stderr.decode('utf-8', 'replace').strip()
        raise GitError(f'git {" ".join(args)} failed: {detail}')
    return done.stdout.decode('utf-8', 'replace')


def tag_names(root: Path) -> list[str]:
    text = _git_text(root, 'for-each-ref', '--format=%(refname:strip=2)', 'refs/tags')
    return [name for name in text.splitlines() if name]


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    subject: str


def read_commit(root: Path, sha: str) -> Commit:
    """A commit's parents and subject, read from the raw object, so no log configuration (a
    signature display, for one) can change what is read."""
    raw = _git_text(root, 'cat-file', 'commit', sha)
    header, _, message = raw.partition('\n\n')
    parents = tuple(line.split()[1] for line in header.splitlines() if line.startswith('parent '))
    subject = message.splitlines()[0] if message.strip() else ''
    return Commit(sha, parents, subject)


def changed_paths(root: Path, sha: str) -> list[str]:
    text = _git_text(
        root,
        'diff-tree',
        '--no-commit-id',
        '--name-only',
        '-r',
        '--root',
        '--no-renames',
        '-z',
        sha,
    )
    return [path for path in text.split('\0') if path]


# --- release metadata --------------------------------------------------------------------------


def glob_pattern(glob: str) -> re.Pattern[str]:
    """A glob over the whole repository-relative path, case-sensitive: `*` is any run of
    characters within one segment, `**` any number of whole segments."""
    parts = glob.split('/')
    pattern = ''
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == '**':
            pattern += '.*' if last else '(?:[^/]+/)*'
            continue
        pattern += ''.join('[^/]*' if char == '*' else re.escape(char) for char in part)
        if not last:
            pattern += '/'
    return re.compile(pattern, re.DOTALL)


def metadata_rule(
    frame: dict[str, Any], sites: list[Site]
) -> tuple[list[re.Pattern[str]], set[str]]:
    globs = [glob_pattern(glob) for glob in frame['release']['metadata_paths']]
    exact = {row['file'] for row in frame['changelog']['records']}
    exact |= {site.path for site in sites}
    return globs, exact


def is_metadata(path: str, rule: tuple[list[re.Pattern[str]], set[str]]) -> bool:
    globs, exact = rule
    return path in exact or any(glob.fullmatch(path) for glob in globs)


# --- the check ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Judged:
    tag: str
    line: Line
    version: Version

    @property
    def text(self) -> str:
        """The version as the tag spells it, `X.Y.Z` after the row's tag."""
        return self.tag[len(self.line.tag) :]


def judged_tags(root: Path, lines: list[Floored]) -> list[Judged]:
    found: list[Judged] = []
    for tag in tag_names(root):
        mapped = map_tag(lines, tag)
        if mapped is None:
            continue
        item, version = mapped
        floor = version_of(item.line, item.floor) if item.floor else None
        if floor is not None and version < floor:
            continue
        found.append(Judged(tag, item.line, version))
    return found


def _judge(
    root: Path,
    item: Judged,
    pulls: list[Pull],
    rule: tuple[list[re.Pattern[str]], set[str]],
) -> list[tuple[str, str]]:
    tag = item.tag
    failures: list[tuple[str, str]] = []
    kind = _git_text(root, 'cat-file', '-t', f'refs/tags/{tag}').strip()
    if kind != 'tag':
        failures.append(
            (
                LIGHTWEIGHT_TAG,
                f'{tag}: not an annotated tag (git cat-file -t says {kind}); lay release tags '
                f'with `git tag -a {tag} <release merge commit> -m "..."`',
            )
        )
    done = git(root, 'rev-parse', '--verify', '--quiet', f'refs/tags/{tag}^{{commit}}')
    commit = done.stdout.decode('ascii', 'replace').strip() if done.returncode == 0 else ''
    version = item.text
    looked_for = f'{version}' + (f' of {item.line.name}' if item.line.name else '')
    candidates = [pull for pull in pulls if is_release_pull(pull, version, item.line.name)]
    if not candidates:
        failures.append(
            (
                NO_RELEASE_PR,
                f'{tag}: no merged pull request releases {looked_for} (a head branch release/... '
                'or a release: title carrying the version'
                + (' and the name' if item.line.name else '')
                + ')',
            )
        )
    # A real merge commit only: an unresolved tag or a candidate with no merge commit never
    # satisfies the rule (interface contract, Wave C, section 11.5).
    elif not commit or commit not in {
        pull.merge_commit for pull in candidates if pull.merge_commit
    }:
        merges = ', '.join(
            f'#{pull.number} merged as {pull.merge_commit or "no merge commit"}'
            for pull in candidates
        )
        failures.append(
            (
                TAG_PLACEMENT,
                f'{tag}: points at {commit or "no commit"}, not the merge commit of its release '
                f'pull request ({merges})',
            )
        )
    if commit:
        failures += _release_commits(root, tag, commit, rule)
    return failures


def _release_commits(
    root: Path, tag: str, commit: str, rule: tuple[list[re.Pattern[str]], set[str]]
) -> list[tuple[str, str]]:
    """The `release:` commits the tag's commit brings in that change more than metadata."""
    top = read_commit(root, commit)
    if top.parents:
        listed = _git_text(root, 'rev-list', f'{commit}^1..{commit}').split()
    else:
        listed = [commit]
    failures: list[tuple[str, str]] = []
    for sha in listed:
        judged = top if sha == commit else read_commit(root, sha)
        if len(judged.parents) > 1 or not RELEASE_SUBJECT.match(judged.subject):
            continue
        outside = [path for path in changed_paths(root, sha) if not is_metadata(path, rule)]
        if outside:
            failures.append(
                (
                    RELEASE_COMMIT,
                    f'{tag}: the release commit {sha} ({judged.subject}) changes paths outside '
                    f'the release metadata: {", ".join(outside)}',
                )
            )
    return failures


def check(root: Path) -> Verdict:
    frame = frame_module.load(root)
    if not frame['release']['enabled']:
        return Verdict.ok(VERB, ('release.enabled is false: the frame turns the check off',))
    lines = floored_lines(frame)
    # The site configuration errors of the shared reading are this verb's too (contract 11.3).
    sites = version_sites(frame, [item.line for item in lines])
    judged = judged_tags(root, lines)
    if not judged:
        return Verdict.ok(VERB, ('no release tag to judge',))
    pulls_file = os.environ.get(PULLS_FILE_ENV, '')
    pulls = read_pulls_file(pulls_file) if pulls_file else fetch_pulls()
    rule = metadata_rule(frame, sites)
    failures: list[tuple[str, str]] = []
    for item in judged:
        failures += _judge(root, item, pulls, rule)
    if not failures:
        count = len(judged)
        return Verdict.ok(VERB, (f'{count} release tag{"s" if count != 1 else ""} judged',))
    code = min((code for code, _ in failures), key=CODE_ORDER.index)
    return Verdict.fail(VERB, code, tuple(message for _, message in failures))


# --- the verb ----------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Judge every release tag from the floor on: annotated, on the merge commit of the pull '
        'request that released it, and with release commits that change only release metadata. '
        f'The pull requests come from the GitHub API, or from the file ${PULLS_FILE_ENV} names.'
    )


def run(args: argparse.Namespace) -> Verdict:
    try:
        return check(frame_module.repository_root())
    except ApiError as exc:
        return Verdict(VERB, 'fail', API_ERROR_CODE, diagnostic=f'{VERB}: {exc}')
    except (
        ConfigError,
        SitesError,
        GitError,
        frame_module.FrameError,
        frame_module.FrameSchemaError,
    ) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
