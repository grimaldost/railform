"""The hook wrapper: the text `railform adopt` writes and `railform hooks-check` compares.

Development-rails spec, §5; interface contract, section 7. The wrapper for a hook is a fixed
template with the frame's `hooks.wrapper` command in it, so the check and the writer cannot
disagree about what a correct wrapper is.

The template follows the wrapper radar-engine tracks in `.githooks/` (the stack-radar engine,
https://github.com/grimaldost/stack-radar), generalized over the hook name and the command.
"""

from __future__ import annotations

import re
from typing import Any

TEMPLATE = (
    '#!/bin/sh\n'
    '# Written by railform adopt: runs the {hook} stage of .pre-commit-config.yaml.\n'
    '# `railform hooks-check` fails if this file is edited; arm a clone with `railform arm`.\n'
    'HERE="$(cd "$(dirname "$0")" && pwd)"\n'
    'exec {wrapper} --config=.pre-commit-config.yaml --hook-type={hook} --hook-dir "$HERE" '
    '-- "$@"\n'
)

# A requirement pinned with nothing after `==`, as `{hooks.pre_commit_version}` leaves it when
# the frame sets no version: `pre-commit== hook-impl`.
_EMPTY_PIN = re.compile(r'==(?=\s|$)')
_HOOK_NAME = re.compile(r'[a-z][a-z0-9]*(-[a-z0-9]+)*')


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
    """The exact text of the wrapper for `hook`, LF line endings and a final newline.

    `frame` is an effective frame (`railform.frame.load`). Raises WrapperError when `hook` is
    not a git hook name, or when the frame's wrapper is empty, spans lines or pins a version
    that is not set.
    """
    if not _HOOK_NAME.fullmatch(hook):
        raise WrapperError(f'hooks.names holds {hook!r}, which is not a hook name')
    return TEMPLATE.format(hook=hook, wrapper=command(frame))
