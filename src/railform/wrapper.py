"""The hook wrapper: the text `railform adopt` writes and `railform hooks-check` compares.

Development-rails spec, §5; interface contract, section 7. The wrapper for a hook is a fixed
template with the frame's `hooks.wrapper` command in it, so the check and the writer cannot
disagree about what a correct wrapper is. A caller that writes the rendered text must write it
as bytes, or with `newline=''`, to keep LF line endings on Windows.

The template follows the wrapper radar-engine tracks in `.githooks/` (the stack-radar engine,
https://github.com/grimaldost/stack-radar), generalized over the hook name and the command.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

_HEAD = (
    '#!/bin/sh\n'
    '# Written by railform adopt: runs the {hook} stage of .pre-commit-config.yaml.\n'
    '# `railform hooks-check` fails if this file is edited; arm a clone with `railform arm`.\n'
    'HERE="$(cd "$(dirname "$0")" && pwd)"\n'
)
_STAGE = '{wrapper} --config=.pre-commit-config.yaml --hook-type={hook} --hook-dir "$HERE" -- "$@"'
TEMPLATE = f'{_HEAD}exec {_STAGE}\n'

# The egress extension point. An egress hook is a Python script that the environment names, in
# the variable RAILFORM_EGRESS_HOOK when it is set and not empty, else in the git configuration
# key railform.egressHook; it always runs as `uv run --no-project python <hook> <mode> ...`, since
# an executable bit means nothing on Windows. With neither set, a wrapper runs pre-commit exactly
# as TEMPLATE does. `printf` rather than `echo`, which some shells read backslashes in.
_EGRESS = (
    'EGRESS="$RAILFORM_EGRESS_HOOK"\n'
    'if [ -z "$EGRESS" ]; then\n'
    '    EGRESS="$(git config --get railform.egressHook)"\n'
    'fi\n'
)

# pre-push: the hook sees the remote name, the remote URL and git's input (one line per pushed
# ref), saved to a temporary file so pre-commit reads the same bytes after it; a hook that exits
# non-zero refuses the push, and a hook path that is not a file refuses it with exit 2.
PRE_PUSH_TEMPLATE = (
    _HEAD + _EGRESS + 'if [ -z "$EGRESS" ]; then\n'
    f'    exec {_STAGE}\n'
    'fi\n'
    'if [ ! -f "$EGRESS" ]; then\n'
    '    printf \'%s\\n\' "railform: egress hook $EGRESS is not a file" >&2\n'
    '    exit 2\n'
    'fi\n'
    'INPUT="$(mktemp)" || exit 1\n'
    'trap \'rm -f "$INPUT"\' EXIT\n'
    "trap 'exit 1' HUP INT TERM\n"
    'cat >"$INPUT"\n'
    'uv run --no-project python "$EGRESS" pre-push "$1" "$2" <"$INPUT"\n'
    'STATUS=$?\n'
    'if [ "$STATUS" -ne 0 ]; then\n'
    '    printf \'%s\\n\' "railform: egress hook $EGRESS refused the push (exit $STATUS)" >&2\n'
    '    exit "$STATUS"\n'
    'fi\n'
    f'{_STAGE} <"$INPUT"\n'
    'exit $?\n'
)

# pre-commit: the hook runs with no input and its exit status is ignored, so it never refuses a
# commit; a hook path that is not a file is reported in one line and the commit goes on.
PRE_COMMIT_TEMPLATE = (
    _HEAD + _EGRESS + 'if [ -n "$EGRESS" ]; then\n'
    '    if [ -f "$EGRESS" ]; then\n'
    '        uv run --no-project python "$EGRESS" pre-commit </dev/null\n'
    '    else\n'
    '        printf \'%s\\n\' "railform: egress hook $EGRESS is not a file; the commit goes on" '
    '>&2\n'
    '    fi\n'
    'fi\n'
    f'exec {_STAGE}\n'
)

# The text of each hook; any other hook, `commit-msg` among them, gets TEMPLATE.
TEMPLATES = {'pre-push': PRE_PUSH_TEMPLATE, 'pre-commit': PRE_COMMIT_TEMPLATE}

# A requirement pinned with nothing after `==`, as `{hooks.pre_commit_version}` leaves it when
# the frame sets no version: `pre-commit== hook-impl`.
_EMPTY_PIN = re.compile(r'==(?=\s|$)')
_HOOK_NAME = re.compile(r'[a-z][a-z0-9]*(-[a-z0-9]+)*')
# Characters Windows refuses in a file or directory name, besides the separators.
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')


class WrapperError(Exception):
    """The frame cannot yield a wrapper: a configuration error, never a violation."""


def command(frame: dict[str, Any]) -> str:
    """The frame's wrapper command, or WrapperError when it cannot be written into a wrapper."""
    hooks = frame['hooks']
    wrapper = hooks['wrapper']
    if not wrapper.strip():
        raise WrapperError('hooks.wrapper is empty')
    if '\n' in wrapper or '\r' in wrapper:
        raise WrapperError('hooks.wrapper must be one line')
    if _EMPTY_PIN.search(wrapper):
        raise WrapperError(
            'hooks.wrapper pins a requirement to no version '
            f'(hooks.pre_commit_version is {hooks["pre_commit_version"]!r}); '
            'set a version or use a wrapper without the placeholder'
        )
    return wrapper.strip()


def render(frame: dict[str, Any], hook: str) -> str:
    """The exact text of the wrapper for `hook`, LF line endings and a final newline: the
    `pre-push` and `pre-commit` texts carry the egress extension point, any other hook's is
    TEMPLATE.

    `frame` is an effective frame (`railform.frame.load`). Raises WrapperError when `hook` is
    not a git hook name, or when the frame's wrapper is empty, spans lines or pins a version
    that is not set.
    """
    if not _HOOK_NAME.fullmatch(hook):
        raise WrapperError(f'hooks.names holds {hook!r}, which is not a hook name')
    return TEMPLATES.get(hook, TEMPLATE).format(hook=hook, wrapper=command(frame))


def hooks_dir(frame: dict[str, Any]) -> str:
    """`hooks.dir` normalised to a repository-relative POSIX path.

    Separators become `/` and `.` segments and doubled or trailing slashes go, so `./.githooks`
    and `.githooks/` name `.githooks`. Raises WrapperError when the path is empty, is the
    repository itself, is absolute, leaves the repository, starts with a `~` or `%(` that git
    expands, or holds in any segment a character Windows forbids in a file name (`<>:"|?*` and
    control characters; so a drive letter too).
    """
    directory = frame['hooks']['dir']
    if directory.startswith(('~', '%(')):
        raise WrapperError(f'hooks.dir {directory!r} starts with a path that git expands')
    slashed = directory.replace('\\', '/')
    path = posixpath.normpath(slashed) if slashed.strip() else '.'
    if path == '.' or path.startswith('/') or path == '..' or path.startswith('../'):
        raise WrapperError(f'hooks.dir {directory!r} is not a directory inside the repository')
    if any(char in _WINDOWS_FORBIDDEN or ord(char) < 0x20 for char in path):
        raise WrapperError(
            f'hooks.dir {directory!r} holds a character Windows forbids in a file name '
            '(<>:"|?* or a control character)'
        )
    return path
