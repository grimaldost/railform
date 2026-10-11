"""The `railform` command: finds the module of a verb by name and runs it.

The verb `x-y` lives in `railform/x_y.py`, which exposes `add_arguments(parser)` and
`run(args) -> Verdict`. A verb in `VERBS` whose module does not exist yet exits 2 with the code
`not-implemented`, so adding a verb module never edits this file.
"""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
import traceback
from dataclasses import replace
from typing import TYPE_CHECKING, NoReturn

from railform import __version__
from railform.verdict import Verdict

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

# The verbs of the interface contract (wave A). A name in this table without a module is a verb
# not written yet; a name outside it, without a module, is an unknown verb.
VERBS = (
    'frame',
    'private-refs',
    'commit-msg',
    'commit-range',
    'pr-text',
    'hooks-check',
    'arm',
    'session-paths',
    'adopt',
    'branch-name',
    'changelog-gate',
    'version-sites',
    'release-check',
)

LEDGER_ENV = 'RAILFORM_LEDGER'
PROG = 'railform'

_VERB_NAME = re.compile(r'[a-z][a-z0-9]*(-[a-z0-9]+)*')
_NOT_TOKEN = re.compile(r'[^A-Za-z0-9._-]')


class _UsageError(Exception):
    """A bad or missing argument, reported as a verdict instead of argparse's own exit."""


class _VerbParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(f'{self.prog}: {message}')


def _load_verb(verb: str) -> ModuleType | None:
    """Return the module that implements `verb`, or None when there is none."""
    if not _VERB_NAME.fullmatch(verb):
        return None
    name = f'railform.{verb.replace("-", "_")}'
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise
    if callable(getattr(module, 'run', None)) and callable(getattr(module, 'add_arguments', None)):
        return module
    return None


def _usage_text() -> str:
    return f'usage: {PROG} [--version] <verb> [args ...]\nverbs: {", ".join(VERBS)}'


def _append_to_ledger(line: str) -> None:
    path = os.environ.get(LEDGER_ENV)
    if not path:
        return
    try:
        with open(path, 'a', encoding='utf-8', newline='\n') as ledger:
            ledger.write(line + '\n')
    except OSError as exc:
        print(f'{PROG}: cannot append to {LEDGER_ENV}: {exc}', file=sys.stderr)


def _finish(verdict: Verdict) -> int:
    """Write the diagnostic, the findings and the verdict line, and append to the ledger."""
    if verdict.diagnostic:
        print(verdict.diagnostic, file=sys.stderr)
    for finding in verdict.findings:
        print(' '.join(finding.splitlines()))
    print(verdict.line, flush=True)
    _append_to_ledger(verdict.line)
    return verdict.exit_code


_HELP_FLAGS = ('-h', '--help')


def _run_verb(verb: str, args: list[str]) -> Verdict | None:
    """Run `verb`; None means the help text was printed, which is not a check."""
    module = _load_verb(verb)
    if module is None:
        if verb in VERBS:
            if any(arg in _HELP_FLAGS for arg in args):
                print(f'{PROG} {verb}: not implemented yet\n{_usage_text()}')
                return None
            return Verdict.not_implemented(verb)
        return Verdict.usage(verb, f'{PROG}: unknown verb\n{_usage_text()}')
    parser = _VerbParser(prog=f'{PROG} {verb}')
    module.add_arguments(parser)
    try:
        namespace = parser.parse_args(args)
    except _UsageError as exc:
        return Verdict.usage(verb, str(exc))
    except SystemExit as exc:  # only `--help` gets here: argparse printed it and asked to exit
        if exc.code in (0, None):
            return None
        return Verdict.usage(verb, f'{parser.prog}: bad arguments')
    verdict = module.run(namespace)
    if not isinstance(verdict, Verdict):
        raise TypeError(f'{verb}: run() returned {type(verdict).__name__}, not a Verdict')
    return verdict


def _backslashreplace_output() -> None:
    """Make stdout and stderr write what they cannot encode as backslash escapes."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            reconfigure(errors='backslashreplace')


def main(argv: Sequence[str] | None = None) -> int:
    _backslashreplace_output()
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _finish(Verdict.usage(PROG, f'{PROG}: a verb is required\n{_usage_text()}'))
    first = args[0]
    if first == '--version':
        print(f'{PROG} {__version__}')
        return 0
    if first in _HELP_FLAGS:
        print(_usage_text())
        return 0
    # The verdict line is one token per field, so a verb typed with odd characters is reported
    # with them replaced.
    verb = _NOT_TOKEN.sub('?', first) or '?'
    try:
        verdict = _run_verb(first, args[1:])
    except (Exception, SystemExit):
        # A crash must still end in one verdict line, and must never read as a pass. A verb has
        # no business exiting by itself; KeyboardInterrupt is not caught.
        traceback.print_exc()
        verdict = Verdict.internal_error(verb)
    if verdict is None:
        return 0
    return _finish(replace(verdict, verb=verb))
