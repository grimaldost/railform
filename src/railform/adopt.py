"""The verb `railform adopt`: write, and check, the parts of a repository that railform owns.

Development-rails spec, §7, invented here; interface contract, section 9. Adopt owns only:

- in `.github/workflows/ci.yml`, the jobs `railform` (the call to the reusable workflow at the
  pin, with the pin again as the input `railform-sha`) and `gate` (the one required status: it
  needs every other job and fails when one failed or was cancelled), between marker lines, plus
  the `types` of the `pull_request` trigger;
- in `.pre-commit-config.yaml`, the railform entry at the pin and the hygiene entry, between
  marker lines;
- in `.github/dependabot.yml`, the ignore entry for railform in the `github-actions` update,
  between marker lines (and that update block, when there is none);
- whole files: the hook wrappers in `hooks.dir` (staged as their exact bytes, 100755 in the git
  index), `.github/workflows/railform-tag-watch.yml` (only once the package carries its template,
  and never written over an existing file), and the first `railform.toml`.

Everything outside those parts is kept byte for byte, line endings included: files are read and
written as bytes, and lines adopt adds take the file's own line ending. A stub replaces each hook
in the previous hooks directory, so that a clone armed there fails until it runs `railform arm`;
a tracked file there that is not 100755 is reported and left alone. Adopt refuses, before any
write, a target path with a symlink or junction on the way and a hooks directory inside `.git`.

`railform adopt --check` writes nothing. It fails with `drift` when an owned part differs from
what adopt would write (a missing pin site included), with `pin-mismatch` when the pins differ
from each other (or the only difference in an owned part is a pin), and with
`extra-pr-workflow` when another workflow file runs on `pull_request` or `pull_request_target`,
or cannot be parsed, and `ci.extra_workflows` does not list it. With `ci.self_hosted` true
(railform itself), the `railform` job calls the workflow by local path, `--pin` rewrites
nothing, and `--check` skips the pin comparisons and says so.
"""

from __future__ import annotations

import itertools
import os
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any

import yaml

from railform import frame as frame_module
from railform.hooks_check import CONFIG_ERRORS, EXECUTABLE, GitError, git, hooks_path
from railform.verdict import Verdict
from railform.wrapper import WrapperError, hooks_dir, render

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

VERB = 'adopt'
DRIFT = 'drift'
PIN_MISMATCH = 'pin-mismatch'
EXTRA_PR_WORKFLOW = 'extra-pr-workflow'
# When several rules fail, the verdict names the first in this order.
CODE_ORDER = (DRIFT, PIN_MISMATCH, EXTRA_PR_WORKFLOW)

REPOSITORY = 'grimaldost/railform'
BEGIN = '# >>> railform: owned by `railform adopt`; `railform adopt --check` fails on any edit'
END = '# <<< railform'

CI_PATH = '.github/workflows/ci.yml'
PRE_COMMIT_PATH = '.pre-commit-config.yaml'
DEPENDABOT_PATH = '.github/dependabot.yml'
WORKFLOWS_DIR = '.github/workflows'
TAG_WATCH_NAME = 'railform-tag-watch.yml'
TAG_WATCH_PATH = f'{WORKFLOWS_DIR}/{TAG_WATCH_NAME}'

LOCAL_WORKFLOW = './.github/workflows/python-ci.yml'
REMOTE_WORKFLOW = f'{REPOSITORY}/.github/workflows/python-ci.yml'
# In a self-hosted repository the input is the commit under test.
SELF_HOSTED_SHA = '${{ github.event.pull_request.head.sha || github.sha }}'
PR_TYPES = ('opened', 'synchronize', 'reopened', 'edited')
OWNED_JOBS = ('railform', 'gate')

SHA = re.compile(r'[0-9a-f]{40}')
_PIN_ARGUMENT = re.compile(r'[0-9a-fA-F]{40}')
_USES_LINE = re.compile(r'^\s*uses:\s*' + re.escape(REMOTE_WORKFLOW) + r'@(\S+)\s*$')
_INPUT_LINE = re.compile(r'^\s*railform-sha:\s*(\S+)\s*$')
_RAILFORM_REPO_LINE = re.compile(
    r'^\s*-?\s*repo:\s*https://github\.com/' + re.escape(REPOSITORY) + r'\s*$'
)
_REPO_LINE = re.compile(r'^\s*-?\s*repo:')
_REV_LINE = re.compile(r'^\s*rev:\s*(\S+)')
_TAG_WATCH_USES = re.compile(
    r'(uses:\s*' + re.escape(REPOSITORY) + r'/\.github/workflows/[\w.-]+@)(\S+)'
)
_TOKEN = re.compile(r'__([A-Z][A-Z_]*)__')
_OTHER_BREAKS = re.compile('\r(?!\n)|[\x85\u2028\u2029]')
_PLAIN_JOB_ID = re.compile(r'[A-Za-z_][A-Za-z0-9_-]*')
_YAML_WORDS = frozenset({'y', 'n', 'yes', 'no', 'on', 'off', 'true', 'false', 'null'})
_FLOOR = re.compile(r'(>=|~=|==|>)\s*(\d+)\.(\d+)')


class AdoptError(Exception):
    """A file adopt cannot read or edit safely: a configuration error when writing, and a drift
    finding under `--check`, where the owned part cannot be what adopt writes."""


# --- text --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Doc:
    """A text file as lines that keep their own endings, and the ending new lines take."""

    path: str
    lines: tuple[str, ...]
    newline: str


@dataclass(frozen=True)
class _Edit:
    """Replace lines [start, stop) with `lines` (written without endings)."""

    start: int
    stop: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class _Region:
    """The owned lines of a file, markers included (`end` is the end marker's line)."""

    begin: int
    end: int
    indent: int

    def holds(self, line: int) -> bool:
        return self.begin <= line <= self.end


def _doc(path: str, text: str) -> _Doc:
    # YAML counts these as line breaks too, so its line numbers would not match ours.
    if _OTHER_BREAKS.search(text):
        raise AdoptError(f'{path}: has line breaks other than LF and CRLF')
    lines = tuple(re.findall(r'[^\n]*\n|[^\n]+\Z', text))
    newline = '\r\n' if lines and lines[0].endswith('\r\n') else '\n'
    return _Doc(path, lines, newline)


def _apply(doc: _Doc, edits: list[_Edit]) -> str:
    lines = list(doc.lines)
    ordered = sorted(edits, key=lambda edit: edit.start, reverse=True)
    for later, earlier in itertools.pairwise(ordered):
        if earlier.stop > later.start:
            raise AdoptError(f'{doc.path}: overlapping edits; edit the file by hand')
    for edit in ordered:
        if edit.start > 0 and not lines[edit.start - 1].endswith('\n'):
            lines[edit.start - 1] += doc.newline
        lines[edit.start : edit.stop] = [line + doc.newline for line in edit.lines]
    return ''.join(lines)


def _blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith('#')


def _indent_of(line: str) -> int:
    body = line.lstrip('﻿')
    return len(body) - len(body.lstrip(' '))


def _find_region(doc: _Doc) -> _Region | None:
    begins = [i for i, line in enumerate(doc.lines) if line.strip().startswith('# >>> railform')]
    ends = [i for i, line in enumerate(doc.lines) if line.strip().startswith('# <<< railform')]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise AdoptError(
            f'{doc.path}: the railform markers are broken; one `# >>> railform` line and one '
            '`# <<< railform` line must enclose the owned region'
        )
    return _Region(begins[0], ends[0], _indent_of(doc.lines[begins[0]]))


def _region_lines(body: list[str], indent: int) -> tuple[str, ...]:
    pad = ' ' * indent
    return (pad + BEGIN, *(pad + line if line else line for line in body), pad + END)


def _shift(lines: list[str] | tuple[str, ...], indent: int) -> list[str]:
    return [' ' * indent + line if line else line for line in lines]


# --- templates ---------------------------------------------------------------------------------


def _template_text(name: str) -> str | None:
    """The text of a template shipped in this package, or None when the package has none."""
    file = resources.files('railform').joinpath('templates').joinpath(name)
    if not file.is_file():
        return None
    return file.read_text(encoding='utf-8').replace('\r\n', '\n')


def _fill(name: str, **values: str) -> list[str]:
    """The lines of a template with each `__NAME__` replaced by `values['NAME']`."""
    text = _template_text(name)
    if text is None:
        raise RuntimeError(f'the template {name} is missing from the railform package')
    tokens = set(_TOKEN.findall(text))
    if tokens != set(values):
        raise RuntimeError(f'the template {name} takes {sorted(tokens)}, not {sorted(values)}')
    for key, value in values.items():
        text = text.replace(f'__{key}__', value)
    return text.splitlines()


# --- YAML positions ----------------------------------------------------------------------------


def _compose(doc: _Doc) -> Any:
    try:
        node = yaml.compose(''.join(doc.lines), Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        detail = ' '.join(str(exc).split())
        raise AdoptError(f'{doc.path}: cannot be parsed as YAML: {detail}') from exc
    if not isinstance(node, yaml.MappingNode):
        raise AdoptError(f'{doc.path}: the top level is not a mapping')
    return node


def _entry(node: Any, key: str) -> tuple[Any, Any] | None:
    """The key node and value node of `key` in a mapping node, or None."""
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return key_node, value
    return None


def _scalar(node: Any) -> str | None:
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _is_empty(node: Any) -> bool:
    """A key with nothing after its colon."""
    return isinstance(node, yaml.ScalarNode) and node.value == '' and node.style is None


def _is_block(node: Any, kind: type) -> bool:
    return isinstance(node, kind) and node.flow_style is not True


def _last_line(doc: _Doc, node: Any) -> int:
    """The last line that holds part of `node`, not counting blank or comment lines after it."""
    mark = node.end_mark
    line = mark.line
    if line >= len(doc.lines) or not doc.lines[line][: mark.column].strip():
        line -= 1
    while line > node.start_mark.line and _blank_or_comment(doc.lines[line]):
        line -= 1
    return line


def _require_inside(doc: _Doc, region: _Region, key: Any, value: Any, what: str) -> None:
    after_key = region.begin > key.start_mark.line
    if after_key and (_is_empty(value) or region.begin <= _last_line(doc, value) + 1):
        return
    raise AdoptError(f'{doc.path}:{region.begin + 1}: the owned region is not inside {what}')


# --- ci.yml ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ci:
    doc: _Doc
    root: Any
    region: _Region | None
    jobs_key: Any
    jobs: Any
    uses: str | None
    railform_sha: str | None


def _jobs(doc: _Doc, root: Any) -> tuple[Any, Any]:
    found = _entry(root, 'jobs')
    if found is None:
        raise AdoptError(f'{CI_PATH}: has no jobs: mapping')
    jobs_key, jobs = found
    if not (_is_empty(jobs) or _is_block(jobs, yaml.MappingNode)):
        raise AdoptError(f'{CI_PATH}: write jobs: as a block mapping')
    return jobs_key, jobs


def _read_ci(text: str | None) -> _Ci:
    doc = _doc(CI_PATH, text if text is not None else '\n'.join(_fill('ci.yml')) + '\n')
    root = _compose(doc)
    region = _find_region(doc)
    jobs_key, jobs = _jobs(doc, root)
    pinned = _search(doc, region, _USES_LINE)
    railform_sha = _search(doc, region, _INPUT_LINE)
    return _Ci(doc, root, region, jobs_key, jobs, pinned, railform_sha)


def _confirm_jobs(text: str) -> None:
    """The owned region of `text` holds the jobs `railform` and `gate` themselves: keys of
    `jobs:`, at the jobs' own indentation, on lines inside the region (and once each)."""
    doc = _doc(CI_PATH, text)
    region = _find_region(doc)
    _, jobs = _jobs(doc, _compose(doc))
    keys = [] if _is_empty(jobs) else [key for key, _ in jobs.value]
    for name in OWNED_JOBS:
        found = [key for key in keys if key.value == name]
        if (
            region is None
            or len(found) != 1
            or not region.holds(found[0].start_mark.line)
            or found[0].start_mark.column != region.indent
        ):
            raise AdoptError(
                f'{CI_PATH}: the owned region does not hold the job {name} directly under jobs: '
                '(a region under another job or inside a block scalar is not a job)'
            )


def _search(doc: _Doc, region: _Region | None, pattern: re.Pattern[str]) -> str | None:
    if region is None:
        return None
    for line in doc.lines[region.begin : region.end + 1]:
        match = pattern.match(line.rstrip('\r\n'))
        if match:
            return match.group(1)
    return None


def _flow_item(job: str) -> str:
    if _PLAIN_JOB_ID.fullmatch(job) and job.lower() not in _YAML_WORDS:
        return job
    return "'" + job.replace("'", "''") + "'"


def _trigger_edits(ci: _Ci) -> list[_Edit]:
    """The edits that give the `pull_request` trigger every type in PR_TYPES."""
    doc = ci.doc
    found = _entry(ci.root, 'on')
    if found is None:
        raise AdoptError(f'{CI_PATH}: has no on: block')
    key, value = found
    types_line = 'types: [' + ', '.join(PR_TYPES) + ']'
    if isinstance(value, (yaml.ScalarNode, yaml.SequenceNode)):
        # `on: push`, `on: [push, pull_request]` or a list: rewrite it as a mapping.
        items = [value] if isinstance(value, yaml.ScalarNode) else value.value
        if not all(isinstance(item, yaml.ScalarNode) for item in items):
            raise AdoptError(f'{CI_PATH}: cannot read the on: block; write it as a mapping')
        events = [item.value for item in items if item.value]
        if 'pull_request' not in events:
            events.append('pull_request')
        head = doc.lines[key.start_mark.line].split(':', 1)[0] + ':'
        lines = [head]
        for event in events:
            lines.append(f'  {event}:')
            if event == 'pull_request':
                lines.append(f'    {types_line}')
        return [_Edit(key.start_mark.line, _last_line(doc, value) + 1, tuple(lines))]
    if not _is_block(value, yaml.MappingNode):
        raise AdoptError(f'{CI_PATH}: write the on: block as a block mapping')
    pr = _entry(value, 'pull_request')
    if pr is None:
        column = value.value[0][0].start_mark.column
        lines = (' ' * column + 'pull_request:', ' ' * (column + 2) + types_line)
        return [_Edit(key.start_mark.line + 1, key.start_mark.line + 1, lines)]
    pr_key, pr_value = pr
    after_pr = pr_key.start_mark.line + 1
    if _is_empty(pr_value):
        line = ' ' * (pr_key.start_mark.column + 2) + types_line
        return [_Edit(after_pr, after_pr, (line,))]
    if not _is_block(pr_value, yaml.MappingNode):
        raise AdoptError(f'{CI_PATH}: write the pull_request trigger as a block mapping')
    types = _entry(pr_value, 'types')
    if types is None:
        line = ' ' * pr_value.value[0][0].start_mark.column + types_line
        return [_Edit(after_pr, after_pr, (line,))]
    types_value = types[1]
    if isinstance(types_value, yaml.ScalarNode):
        present = [types_value.value]
    elif isinstance(types_value, yaml.SequenceNode) and all(
        isinstance(item, yaml.ScalarNode) for item in types_value.value
    ):
        present = [item.value for item in types_value.value]
    else:
        raise AdoptError(f'{CI_PATH}: cannot read the pull_request types')
    missing = [kind for kind in PR_TYPES if kind not in present]
    if not missing:
        return []
    start, end = types_value.start_mark, types_value.end_mark
    if isinstance(types_value, yaml.SequenceNode) and types_value.flow_style is not True:
        dash = ' ' * start.column + '- '
        last = _last_line(doc, types_value)
        return [_Edit(last + 1, last + 1, tuple(dash + kind for kind in missing))]
    if start.line != end.line:
        raise AdoptError(f'{CI_PATH}: write the pull_request types on one line or as a list')
    text = doc.lines[start.line].rstrip('\r\n')
    merged = text[: start.column] + '[' + ', '.join(present + missing) + ']' + text[end.column :]
    return [_Edit(start.line, start.line + 1, (merged,))]


def _plan_ci(ci: _Ci, uses: str, railform_sha: str) -> str:
    """The text of ci.yml as adopt writes it, with `uses` and `railform_sha` at the pin sites."""
    doc, region = ci.doc, ci.region
    edits = _trigger_edits(ci)
    keys = [] if _is_empty(ci.jobs) else [key for key, _ in ci.jobs.value]
    others: list[str] = []
    for key in keys:
        if region is not None and region.holds(key.start_mark.line):
            continue
        if key.value in OWNED_JOBS:
            raise AdoptError(
                f'{CI_PATH}:{key.start_mark.line + 1}: a job named {key.value} sits outside the '
                'railform marker lines; put it between them (a railform job that calls '
                f'{LOCAL_WORKFLOW} makes the repository self-hosted), rename it, or remove it so '
                'that adopt writes it'
            )
        others.append(key.value)
    needs = '[' + ', '.join(_flow_item(job) for job in ['railform', *others]) + ']'
    body = _fill('ci-jobs.yml', USES=uses, RAILFORM_SHA=railform_sha, NEEDS=needs)
    if region is not None:
        _require_inside(doc, region, ci.jobs_key, ci.jobs, 'jobs:')
        edits.append(_Edit(region.begin, region.end + 1, _region_lines(body, region.indent)))
    else:
        indent = ci.jobs_key.start_mark.column + 2 if not keys else keys[0].start_mark.column
        after = ci.jobs_key.start_mark.line + 1
        edits.append(_Edit(after, after, _region_lines(body, indent)))
    planned = _apply(doc, edits)
    _confirm_jobs(planned)
    return planned


# --- .pre-commit-config.yaml -------------------------------------------------------------------


@dataclass(frozen=True)
class _PreCommit:
    doc: _Doc
    region: _Region | None
    repos_key: Any
    repos: Any
    rev: str | None


def _read_pre_commit(text: str | None) -> _PreCommit:
    default = '\n'.join(_fill('pre-commit-config.yaml')) + '\n'
    doc = _doc(PRE_COMMIT_PATH, text if text is not None else default)
    root = _compose(doc)
    region = _find_region(doc)
    found = _entry(root, 'repos')
    if found is None:
        raise AdoptError(f'{PRE_COMMIT_PATH}: has no repos: list')
    repos_key, repos = found
    if not (_is_empty(repos) or _is_block(repos, yaml.SequenceNode)):
        raise AdoptError(f'{PRE_COMMIT_PATH}: write repos: as a block list')
    return _PreCommit(doc, region, repos_key, repos, _railform_rev(doc, region))


def _railform_rev(doc: _Doc, region: _Region | None) -> str | None:
    """The `rev` of the railform entry inside the owned region."""
    if region is None:
        return None
    inside = False
    for line in doc.lines[region.begin : region.end + 1]:
        text = line.rstrip('\r\n')
        if _RAILFORM_REPO_LINE.match(text):
            inside = True
        elif _REPO_LINE.match(text):
            inside = False
        elif inside and (match := _REV_LINE.match(text)):
            return match.group(1)
    return None


def _hooks_files(directory: str) -> str:
    """A YAML single-quoted regular expression for the files under `directory`."""
    return "'" + ('^' + re.escape(directory) + '/').replace("'", "''") + "'"


def _plan_pre_commit(pre: _PreCommit, rev: str | None, directory: str) -> str:
    """The text of the pre-commit configuration as adopt writes it; `rev` None is self-hosted."""
    files = _hooks_files(directory)
    if rev is None:
        entry = _fill('pre-commit-local.yaml', HOOKS_FILES=files)
    else:
        entry = _fill('pre-commit-railform.yaml', PIN=rev, HOOKS_FILES=files)
    body = entry + _fill('pre-commit-hygiene.yaml')
    doc, region = pre.doc, pre.region
    if region is not None:
        _require_inside(doc, region, pre.repos_key, pre.repos, 'repos:')
        return _apply(
            doc, [_Edit(region.begin, region.end + 1, _region_lines(body, region.indent))]
        )
    if _is_empty(pre.repos):
        indent = pre.repos_key.start_mark.column + 2
    else:
        indent = pre.repos.start_mark.column
    after = pre.repos_key.start_mark.line + 1
    return _apply(doc, [_Edit(after, after, _region_lines(body, indent))])


# --- .github/dependabot.yml --------------------------------------------------------------------


def _ignore_body(with_key: bool) -> list[str]:
    entry = _fill('dependabot-ignore.yml')
    return ['ignore:', *_shift(entry, 2)] if with_key else entry


def _plan_dependabot(text: str | None) -> str:
    """The text of the Dependabot configuration as adopt writes it.

    The region holds the ignore entry for railform inside the `github-actions` update; it also
    holds the `ignore:` key when that update had none of its own.
    """
    doc = _doc(DEPENDABOT_PATH, text if text is not None else '\n'.join(_fill('dependabot.yml')))
    root = _compose(doc)
    region = _find_region(doc)
    found = _entry(root, 'updates')
    if found is None:
        raise AdoptError(f'{DEPENDABOT_PATH}: has no updates: list')
    updates_key, updates = found
    if not (_is_empty(updates) or _is_block(updates, yaml.SequenceNode)):
        raise AdoptError(f'{DEPENDABOT_PATH}: write updates: as a block list')
    items = [] if _is_empty(updates) else updates.value
    actions = [
        item
        for item in items
        if _scalar((_entry(item, 'package-ecosystem') or (None, None))[1]) == 'github-actions'
    ]
    if region is not None:
        item = next(
            (
                item
                for item in actions
                if item.start_mark.line < region.begin <= _last_line(doc, item) + 1
            ),
            None,
        )
        if item is None:
            raise AdoptError(
                f'{DEPENDABOT_PATH}:{region.begin + 1}: the owned region is not inside the '
                'github-actions update'
            )
        ignore = _entry(item, 'ignore')
        own_key = ignore is not None and not region.holds(ignore[0].start_mark.line)
        body = _ignore_body(not own_key)
        return _apply(
            doc, [_Edit(region.begin, region.end + 1, _region_lines(body, region.indent))]
        )
    if actions:
        item = actions[0]
        ignore = _entry(item, 'ignore')
        if ignore is None:
            after = _last_line(doc, item) + 1
            lines = _region_lines(_ignore_body(True), item.start_mark.column)
            return _apply(doc, [_Edit(after, after, lines)])
        ignore_key, ignore_value = ignore
        if _is_empty(ignore_value):
            indent = ignore_key.start_mark.column + 2
        elif _is_block(ignore_value, yaml.SequenceNode):
            indent = ignore_value.start_mark.column
        else:
            raise AdoptError(f'{DEPENDABOT_PATH}: write the github-actions ignore: as a block list')
        after = ignore_key.start_mark.line + 1
        return _apply(doc, [_Edit(after, after, _region_lines(_ignore_body(False), indent))])
    # No github-actions update yet: add one, whose ignore entry is the owned region.
    block = [*_fill('dependabot-update.yml'), *_region_lines(_ignore_body(True), 2)]
    if items:
        dash, after = updates.start_mark.column, _last_line(doc, updates) + 1
    else:
        dash, after = updates_key.start_mark.column + 2, updates_key.start_mark.line + 1
    return _apply(doc, [_Edit(after, after, tuple(_shift(block, dash)))])


# --- railform-tag-watch.yml --------------------------------------------------------------------


def _render_tag_watch(pin: str) -> str:
    return '\n'.join(_fill(TAG_WATCH_NAME, PIN=pin)) + '\n'


def _plan_tag_watch(text: str | None, pin: str) -> str | None:
    """The tag-watch caller, a whole file adopt owns once the package carries its template (§16):
    written new from the template, never written over an existing file, whose `uses:` commit
    alone moves with the pin. None when there is nothing to write."""
    if _template_text(TAG_WATCH_NAME) is None:
        return None
    if text is None:
        return _render_tag_watch(pin)
    return _TAG_WATCH_USES.sub(lambda match: match.group(1) + pin, text)


# --- the repository ----------------------------------------------------------------------------


def _read(root: Path, path: str) -> str | None:
    """The text of a file in the repository, or None when it does not exist."""
    try:
        data = (root / path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AdoptError(f'{path}: cannot read: {exc}') from exc
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise AdoptError(f'{path}: not UTF-8') from exc


def _write(root: Path, path: str, text: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(text.encode('utf-8'))


def _index_entry(root: Path, path: str) -> tuple[str, str] | None:
    """The mode and object id the git index records for `path`, or None."""
    done = git(root, 'ls-files', '-s', '-z', '--', path)
    if done.returncode != 0:
        raise GitError(f'git ls-files failed: {done.stderr.decode("utf-8", "replace").strip()}')
    for entry in done.stdout.split(b'\0'):
        meta, _, name = entry.partition(b'\t')
        fields = meta.split()
        if len(fields) == 3 and fields[2] == b'0' and name.decode('utf-8', 'replace') == path:
            return fields[0].decode('ascii'), fields[1].decode('ascii')
    return None


def _object_id(root: Path, data: bytes, *, write: bool = False) -> str:
    """The blob id of `data` as it is, with no filter; with `write`, also stored as an object."""
    command = ['git', 'hash-object', '-t', 'blob', '--no-filters', '--stdin']
    if write:
        command.insert(2, '-w')
    try:
        done = subprocess.run(
            command,
            cwd=root,
            input=data,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f'cannot run git: {exc}') from exc
    if done.returncode != 0:
        raise GitError(f'git hash-object failed: {done.stderr.decode("utf-8", "replace").strip()}')
    return done.stdout.decode('ascii').strip()


def _put_executable(root: Path, path: str, text: str) -> bool:
    """Write `text` to `path` and record those exact bytes as 100755 in the index; True when
    anything changed.

    The blob is written with `--no-filters` and staged with `--cacheinfo`, so `core.autocrlf` and
    `core.safecrlf` never rewrite the wrapper or refuse it.
    """
    data = text.encode('utf-8')
    file = root / path
    changed = False
    try:
        current: bytes | None = file.read_bytes()
    except FileNotFoundError:
        current = None
    if current != data:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        changed = True
    if os.name != 'nt':
        # So that a later `git add` with core.fileMode keeps the mode the index records.
        mode = file.stat().st_mode
        if mode & 0o111 != 0o111:
            file.chmod(mode | 0o111)
            changed = True
    if _index_entry(root, path) != (EXECUTABLE, _object_id(root, data)):
        oid = _object_id(root, data, write=True)
        done = git(root, 'update-index', '--add', '--cacheinfo', f'{EXECUTABLE},{oid},{path}')
        if done.returncode != 0:
            detail = done.stderr.decode('utf-8', 'replace').strip()
            raise GitError(f'git update-index failed for {path}: {detail}')
        changed = True
    return changed


def _in_git_dir(directory: str) -> bool:
    return directory.split('/', 1)[0].casefold() == '.git'


def _relative_dir(value: str) -> str | None:
    """A configured hooks directory as a repository-relative path, or None outside the clone."""
    try:
        directory = hooks_dir({'hooks': {'dir': value}})
    except WrapperError:
        return None
    return None if _in_git_dir(directory) else directory


def _index_entries(root: Path) -> dict[str, str]:
    """Every path in the git index (stage 0) and the mode recorded for it."""
    done = git(root, 'ls-files', '-s', '-z')
    if done.returncode != 0:
        raise GitError(f'git ls-files failed: {done.stderr.decode("utf-8", "replace").strip()}')
    entries: dict[str, str] = {}
    for entry in done.stdout.split(b'\0'):
        meta, _, name = entry.partition(b'\t')
        fields = meta.split()
        if len(fields) == 3 and fields[2] == b'0':
            entries[name.decode('utf-8', 'replace')] = fields[0].decode('ascii')
    return entries


def _glob_pattern(glob: str) -> re.Pattern[str]:
    """A `session_loaded` glob (contract section 8): `**` spans segments, `*` stays in one."""
    parts = glob.casefold().split('/')
    pattern = ''
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == '**':
            pattern += '.*' if last else '(?:[^/]*/)*'
            continue
        pattern += ''.join('[^/]*' if char == '*' else re.escape(char) for char in part)
        if not last:
            pattern += '/'
    return re.compile(pattern)


def _session_loaded(path: str, globs: list[str]) -> bool:
    """Whether a glob matches `path`, or a suffix of it that starts at a `/`, casefolded."""
    folded = path.casefold()
    suffixes = [folded] + [folded[i + 1 :] for i, char in enumerate(folded) if char == '/']
    patterns = [_glob_pattern(glob) for glob in globs]
    return any(pattern.fullmatch(suffix) for pattern in patterns for suffix in suffixes)


def _previous_hooks_dir(
    root: Path, frame: dict[str, Any], directory: str, entries: dict[str, str]
) -> str | None:
    """Where the hooks lived before (contract section 9, as decided on PR07's review): the local
    core.hooksPath when it is set and differs from hooks.dir; else the tracked directory, outside
    hooks.dir and outside every `session_loaded` glob, that holds the most `100755` entries named
    after a hook (the first by name on a tie)."""
    done = git(root, 'config', '--local', '--get', 'core.hooksPath')
    if done.returncode not in (0, 1):
        raise GitError(f'git config failed: {done.stderr.decode("utf-8", "replace").strip()}')
    configured = done.stdout.decode('utf-8', 'replace').rstrip('\r\n')
    if configured:
        relative = _relative_dir(configured)
        if relative is not None and relative != directory:
            return relative
    names = frame['hooks']['names']
    counts: dict[str, int] = {}
    for path, mode in entries.items():
        parent, _, base = path.rpartition('/')
        if (
            mode == EXECUTABLE
            and base in names
            and parent
            and parent != directory
            and not parent.startswith(directory + '/')
            and not _session_loaded(path, frame['session_loaded'])
        ):
            counts[parent] = counts.get(parent, 0) + 1
    if not counts:
        return None
    return min(counts, key=lambda parent: (-counts[parent], parent))


def _refuse_links(root: Path, paths: list[str]) -> None:
    """Refuse, before any write, a target path with a symlink or junction on the way."""
    base = os.path.realpath(root)
    for path in paths:
        expected = os.path.join(base, *path.split('/'))
        if os.path.normcase(os.path.realpath(root / path)) != os.path.normcase(expected):
            raise AdoptError(
                f'{path}: a symlink or junction lies on the way to this file; adopt writes only '
                'plain paths inside the repository'
            )


def _stub(directory: str) -> str:
    message = (
        f'railform: the git hooks of this repository moved to {directory}; arm this clone with '
        f'`railform arm` (or: git config core.hooksPath {directory})'
    )
    return '\n'.join(_fill('hook-stub.sh', MESSAGE=shlex.quote(message))) + '\n'


PR_EVENTS = ('pull_request', 'pull_request_target')
_ADVICE = 'move its jobs into ci.yml, or list it in ci.extra_workflows with an exceptions row'


def _runs_on_pull_request(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    triggers = data.get('on', data.get(True))
    if isinstance(triggers, str):
        return triggers in PR_EVENTS
    if isinstance(triggers, (list, dict)):
        return any(event in triggers for event in PR_EVENTS)
    return False


def _extra_pr_workflows(root: Path, allowed: list[str]) -> list[str]:
    """One message per other workflow file that runs on a pull request event, or cannot be
    parsed (so may), and that `ci.extra_workflows` does not list by name or by path."""
    folder = root / WORKFLOWS_DIR
    if not folder.is_dir():
        return []
    found: list[str] = []
    for file in sorted(folder.iterdir(), key=lambda path: path.name):
        if not file.is_file() or file.suffix not in ('.yml', '.yaml') or file.name == 'ci.yml':
            continue
        path = f'{WORKFLOWS_DIR}/{file.name}'
        if file.name in allowed or path in allowed:
            continue
        try:
            data = yaml.safe_load(file.read_bytes().decode('utf-8'))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            found.append(f'{path}: cannot be parsed, so it may run on pull requests; ' + _ADVICE)
            continue
        if _runs_on_pull_request(data):
            found.append(
                f'{path}: runs on pull requests beside ci.yml, where gate cannot need its jobs; '
                + _ADVICE
            )
    return found


# --- seeding -----------------------------------------------------------------------------------


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split('.'))


def _floor(requires: object) -> str | None:
    """The lowest Python minor version a `requires-python` specifier admits, or None."""
    if not isinstance(requires, str):
        return None
    bounds = [(int(major), int(minor)) for _, major, minor in _FLOOR.findall(requires)]
    if not bounds:
        return None
    major, minor = max(bounds)
    return f'{major}.{minor}'


def _seed(root: Path, requested: str | None) -> str:
    """The text of the first `railform.toml`: the project type and the Python versions."""
    pyproject = root / 'pyproject.toml'
    if not pyproject.is_file():
        project_type = requested or 'plugin-marketplace'
        floor = None
    else:
        try:
            data = tomllib.loads(pyproject.read_bytes().decode('utf-8-sig'))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise AdoptError(f'pyproject.toml: cannot read: {exc}') from exc
        src = root / 'src'
        src_layout = src.is_dir() and any(src.rglob('*.py'))
        project_type = requested or ('python-package' if src_layout else 'python-scripts')
        project = data.get('project')
        floor = _floor(project.get('requires-python') if isinstance(project, dict) else None)
    newest = frame_module.preset_frame(project_type)['python']['newest']
    versions = {newest}
    if floor is not None:
        versions = {floor} | ({newest} if _version_key(newest) >= _version_key(floor) else set())
    seed = {
        'project': {'type': project_type},
        'python': {'versions': sorted(versions, key=_version_key)},
    }
    header = (
        "# This repository's frame for railform: `railform frame` prints the effective frame and\n"
        '# `railform frame --check` judges this file.\n'
    )
    return header + frame_module.dumps_toml(seed)


# --- the two modes -----------------------------------------------------------------------------


def _wrappers(frame: dict[str, Any]) -> tuple[str, list[str], dict[str, str]]:
    directory = hooks_dir(frame)
    names = list(frame['hooks']['names'])
    return directory, names, {f'{directory}/{hook}': render(frame, hook) for hook in names}


def _adopt(root: Path, pin: str | None, requested: str | None) -> Verdict:
    """Write every owned part; nothing is written until every file has been planned."""
    findings: list[str] = []
    config = frame_module.read_config(root)
    seed = None
    if config is None:
        seed = _seed(root, requested)
        config = frame_module.parse_config(seed)
    elif requested is not None and frame_module.effective_frame(config)['project']['type'] != (
        requested
    ):
        raise AdoptError(
            f'railform.toml already sets another project.type; --type {requested} applies only '
            'to the first run, so change the type in railform.toml'
        )
    frame = frame_module.effective_frame(config)
    directory, names, wrappers = _wrappers(frame)
    if _in_git_dir(directory):
        raise AdoptError(
            f'hooks.dir {directory!r} lies inside .git, where nothing is tracked; choose a '
            'directory of the repository'
        )

    ci_text = _read(root, CI_PATH)
    ci = _read_ci(ci_text)
    pre_text = _read(root, PRE_COMMIT_PATH)
    pre = _read_pre_commit(pre_text)
    dependabot_text = _read(root, DEPENDABOT_PATH)
    tag_watch_text = _read(root, TAG_WATCH_PATH)
    planned: list[tuple[str, str | None, str | None]] = []
    notes: list[str] = []
    if frame['ci']['self_hosted']:
        if pin is not None:
            notes.append(
                f'self-hosted (ci.self_hosted is true): the railform job calls {LOCAL_WORKFLOW} '
                'and --pin rewrites nothing'
            )
        planned.append((CI_PATH, ci_text, _plan_ci(ci, LOCAL_WORKFLOW, SELF_HOSTED_SHA)))
        planned.append((PRE_COMMIT_PATH, pre_text, _plan_pre_commit(pre, None, directory)))
    else:
        pin = pin or next(
            (
                value
                for value in (ci.uses, ci.railform_sha, pre.rev)
                if value and SHA.fullmatch(value)
            ),
            None,
        )
        if pin is None:
            raise AdoptError('no pin to adopt at: pass --pin with a 40-hex commit of railform')
        planned.append((CI_PATH, ci_text, _plan_ci(ci, f'{REMOTE_WORKFLOW}@{pin}', pin)))
        planned.append((PRE_COMMIT_PATH, pre_text, _plan_pre_commit(pre, pin, directory)))
        planned.append((TAG_WATCH_PATH, tag_watch_text, _plan_tag_watch(tag_watch_text, pin)))
    planned.append((DEPENDABOT_PATH, dependabot_text, _plan_dependabot(dependabot_text)))
    entries = _index_entries(root)
    previous = _previous_hooks_dir(root, frame, directory, entries)
    stubs: dict[str, str] = {}
    for hook in names if previous else []:
        path = f'{previous}/{hook}'
        mode = entries.get(path)
        if mode is not None and mode != EXECUTABLE:
            notes.append(f'{path}: tracked as {mode}, not a hook wrapper; left as it is, no stub')
        else:
            stubs[path] = _stub(directory)
    writes = [(path, new) for path, old, new in planned if new is not None and new != old]
    targets = [path for path, _ in writes] + list(wrappers) + list(stubs)
    _refuse_links(root, [frame_module.CONFIG_NAME, *targets] if seed is not None else targets)

    if seed is not None:
        _write(root, frame_module.CONFIG_NAME, seed)
        findings.append(f'{frame_module.CONFIG_NAME}: written (the first run seeds it)')
    for path, new in writes:
        _write(root, path, new)
        findings.append(f'{path}: written')
    for path, text in wrappers.items():
        if _put_executable(root, path, text):
            findings.append(f'{path}: written, 100755 in the git index')
    for path, text in stubs.items():
        if _put_executable(root, path, text):
            findings.append(f'{path}: stub written; it fails until the clone runs railform arm')
    if not findings:
        findings.append('nothing to change')
    findings += notes
    findings += [
        message + '; railform adopt --check fails until then'
        for message in _extra_pr_workflows(root, frame['ci']['extra_workflows'])
    ]
    configured = hooks_path(root)
    if configured is None or _relative_dir(configured) != directory:
        findings.append(f'arm this clone with `railform arm` (core.hooksPath to {directory})')
    return Verdict.ok(VERB, tuple(findings))


def _differences(path: str, old: str, new: str, region: _Region | None) -> list[str]:
    actual = old.replace('\r\n', '\n').split('\n')
    expected = new.replace('\r\n', '\n').split('\n')
    if actual == expected:
        return []
    if region is None:
        return [_NO_REGION.format(path=path)]
    return _first_difference(path, actual, expected)


_NO_REGION = '{path}: has no region owned by railform (the `# >>> railform` markers); run adopt'


def _first_difference(path: str, actual: list[str], expected: list[str]) -> list[str]:
    if actual == expected:
        return []
    index = next(
        (i for i, (a, e) in enumerate(zip(actual, expected, strict=False)) if a != e),
        min(len(actual), len(expected)),
    )
    found = actual[index] if index < len(actual) else '<end of file>'
    wanted = expected[index] if index < len(expected) else '<end of file>'
    return [
        f'{path}:{index + 1}: differs from what railform adopt writes: '
        f'expected {wanted.strip()!r}, found {found.strip()!r}'
    ]


def _check(root: Path) -> Verdict:
    problems: list[tuple[str, str]] = []
    notes: list[str] = []
    frame = frame_module.load(root)
    directory, _, wrappers = _wrappers(frame)

    def drift(message: str) -> None:
        problems.append((DRIFT, message))

    pins: list[tuple[str, str]] = []
    self_hosted = frame['ci']['self_hosted']
    ci_text = _read(root, CI_PATH)
    if ci_text is None:
        drift(f'{CI_PATH}: missing; run railform adopt')
    else:
        try:
            ci = _read_ci(ci_text)
            if ci.region is None:
                drift(_NO_REGION.format(path=CI_PATH))
            elif self_hosted:
                expected = _plan_ci(ci, LOCAL_WORKFLOW, SELF_HOSTED_SHA)
                for message in _differences(CI_PATH, ci_text, expected, ci.region):
                    drift(message)
            elif ci.uses is None:
                drift(
                    f'{CI_PATH}: the railform job does not call {REMOTE_WORKFLOW} at a pin (a call '
                    'by local path needs ci.self_hosted = true)'
                )
            elif ci.railform_sha is None:
                drift(f'{CI_PATH}: the railform job passes no railform-sha input')
            else:
                expected = _plan_ci(ci, f'{REMOTE_WORKFLOW}@{ci.uses}', ci.railform_sha)
                for message in _differences(CI_PATH, ci_text, expected, ci.region):
                    drift(message)
            if not self_hosted and ci.uses is not None:
                pins.append((f'{CI_PATH}: the uses: SHA', ci.uses))
            if not self_hosted and ci.railform_sha is not None:
                pins.append((f'{CI_PATH}: the railform-sha input', ci.railform_sha))
        except AdoptError as exc:
            drift(str(exc))

    pre_text = _read(root, PRE_COMMIT_PATH)
    if pre_text is None:
        drift(f'{PRE_COMMIT_PATH}: missing; run railform adopt')
    else:
        try:
            pre = _read_pre_commit(pre_text)
            if pre.region is None:
                drift(_NO_REGION.format(path=PRE_COMMIT_PATH))
            elif not self_hosted and pre.rev is None:
                drift(f'{PRE_COMMIT_PATH}: the railform entry has no rev')
            else:
                expected = _plan_pre_commit(pre, None if self_hosted else pre.rev, directory)
                for message in _differences(PRE_COMMIT_PATH, pre_text, expected, pre.region):
                    drift(message)
            if not self_hosted and pre.rev is not None:
                pins.append((f'{PRE_COMMIT_PATH}: the rev of the railform entry', pre.rev))
        except AdoptError as exc:
            drift(str(exc))

    dependabot_text = _read(root, DEPENDABOT_PATH)
    if dependabot_text is None:
        drift(f'{DEPENDABOT_PATH}: missing; run railform adopt')
    else:
        try:
            expected = _plan_dependabot(dependabot_text)
            region = _find_region(_doc(DEPENDABOT_PATH, dependabot_text))
            for message in _differences(DEPENDABOT_PATH, dependabot_text, expected, region):
                drift(message)
        except AdoptError as exc:
            drift(str(exc))

    # The tag-watch caller is judged whole once the package carries its template (§16); until
    # then there is nothing to judge.
    if not self_hosted and _template_text(TAG_WATCH_NAME) is not None:
        tag_watch_text = _read(root, TAG_WATCH_PATH)
        shas = [m.group(2) for m in _TAG_WATCH_USES.finditer(tag_watch_text or '')]
        if tag_watch_text is None:
            drift(f'{TAG_WATCH_PATH}: missing; run railform adopt')
        elif not shas:
            drift(f'{TAG_WATCH_PATH}: calls no railform workflow at a pin')
        else:
            actual = tag_watch_text.replace('\r\n', '\n').split('\n')
            expected_lines = _render_tag_watch(shas[0]).split('\n')
            for message in _first_difference(TAG_WATCH_PATH, actual, expected_lines):
                drift(message)
            pins += [(f'{TAG_WATCH_PATH}: the uses: SHA', sha) for sha in shas]

    for path, text in wrappers.items():
        current = _read_bytes_text(root, path)
        if current is None:
            drift(f'{path}: the hook wrapper is missing; run railform adopt')
            continue
        if current.replace('\r\n', '\n') != text:
            drift(f'{path}: differs from the wrapper railform adopt writes')
        entry = _index_entry(root, path)
        if entry is None or entry[0] != EXECUTABLE:
            state = 'not tracked' if entry is None else f'tracked as {entry[0]}'
            drift(f'{path}: {state}, not {EXECUTABLE}; run railform adopt')

    if self_hosted:
        notes.append(
            f'self-hosted (ci.self_hosted is true, the railform job calls {LOCAL_WORKFLOW}): '
            'the pin comparisons are skipped'
        )
    else:
        problems += [(PIN_MISMATCH, message) for message in _pin_problems(pins)]

    extra = _extra_pr_workflows(root, frame['ci']['extra_workflows'])
    problems += [(EXTRA_PR_WORKFLOW, message) for message in extra]
    findings = (*notes, *(message for _, message in problems))
    if not problems:
        return Verdict.ok(VERB, findings)
    code = min((code for code, _ in problems), key=CODE_ORDER.index)
    return Verdict.fail(VERB, code, findings)


def _read_bytes_text(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_bytes().decode('utf-8', 'replace')
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AdoptError(f'{path}: cannot read: {exc}') from exc


def _pin_problems(pins: list[tuple[str, str]]) -> list[str]:
    """Every pin site present holds one 40-hex commit: the workflow's `uses:` SHA, or the first
    site present when that line is gone (a missing site is drift, reported on its own)."""
    if not pins:
        return []
    reference_site, reference = pins[0]
    if not SHA.fullmatch(reference):
        return [f'{reference_site} is {reference}, not a 40-hex commit']
    return [
        f'{where} is {value}, not the pin {reference} ({reference_site})'
        for where, value in pins[1:]
        if value != reference
    ]


# --- the verb ----------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Write the parts of this repository that railform owns (the railform and gate jobs in '
        'ci.yml, the railform and hygiene entries in .pre-commit-config.yaml, the Dependabot '
        'ignore entry, the hook wrappers and the first railform.toml), or check them.'
    )
    parser.add_argument(
        '--type',
        choices=frame_module.PROJECT_TYPES,
        help='the project type to seed railform.toml with on the first run (default: detected)',
    )
    parser.add_argument(
        '--pin',
        metavar='SHA',
        help='the railform commit (40 hexadecimal characters) every reference moves to',
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='write nothing; fail when an owned part differs from what adopt writes (drift), '
        'when the pins differ (pin-mismatch), or when another workflow runs on pull_request '
        '(extra-pr-workflow)',
    )


def run(args: argparse.Namespace) -> Verdict:
    if args.check and (args.pin is not None or args.type is not None):
        return Verdict.usage(VERB, f'{VERB}: --check takes neither --pin nor --type')
    pin = None
    if args.pin is not None:
        if not _PIN_ARGUMENT.fullmatch(args.pin):
            return Verdict.usage(
                VERB, f'{VERB}: --pin takes a commit SHA of 40 hexadecimal characters'
            )
        pin = args.pin.lower()
    try:
        root = frame_module.repository_root()
        if args.check:
            return _check(root)
        return _adopt(root, pin, args.type)
    except (*CONFIG_ERRORS, GitError, AdoptError) as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
