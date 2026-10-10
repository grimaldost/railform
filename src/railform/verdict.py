"""The verdict a verb returns, and the one line that reports it.

Every verb ends its standard output with exactly one line of the form
`railform-verdict: <verb> <pass|fail> <code>` (development-rails spec, design decision 9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PREFIX = 'railform-verdict:'
OK_CODE = 'ok'
USAGE_CODE = 'usage'
NOT_IMPLEMENTED_CODE = 'not-implemented'
INTERNAL_ERROR_CODE = 'internal-error'

# A usage or configuration error exits 2 (an unimplemented verb and a crash included); any
# other failure is a violation the check found and exits 1.
_EXIT_2_CODES = frozenset({USAGE_CODE, NOT_IMPLEMENTED_CODE, INTERNAL_ERROR_CODE})
_TOKEN = re.compile(r'[a-z0-9]+(-[a-z0-9]+)*')


@dataclass(frozen=True)
class Verdict:
    """The outcome of one verb run.

    `findings` are printed one per line before the verdict line. `diagnostic` goes to standard
    error and is meant for exit 2.
    """

    verb: str
    status: str
    code: str
    findings: tuple[str, ...] = ()
    diagnostic: str = ''

    def __post_init__(self) -> None:
        if self.status not in ('pass', 'fail'):
            raise ValueError(f'status must be pass or fail, not {self.status!r}')
        if (self.status == 'pass') != (self.code == OK_CODE):
            raise ValueError(f'code {self.code!r} does not fit status {self.status!r}')
        if not _TOKEN.fullmatch(self.code):
            raise ValueError(f'code must be a kebab-case token, not {self.code!r}')

    @classmethod
    def ok(cls, verb: str, findings: tuple[str, ...] = ()) -> Verdict:
        return cls(verb, 'pass', OK_CODE, findings)

    @classmethod
    def fail(cls, verb: str, code: str, findings: tuple[str, ...] = ()) -> Verdict:
        return cls(verb, 'fail', code, findings)

    @classmethod
    def usage(cls, verb: str, message: str) -> Verdict:
        return cls(verb, 'fail', USAGE_CODE, diagnostic=message)

    @classmethod
    def not_implemented(cls, verb: str) -> Verdict:
        return cls(verb, 'fail', NOT_IMPLEMENTED_CODE, diagnostic=f'{verb}: not implemented yet')

    @classmethod
    def internal_error(cls, verb: str) -> Verdict:
        return cls(verb, 'fail', INTERNAL_ERROR_CODE, diagnostic=f'{verb}: unhandled exception')

    @property
    def exit_code(self) -> int:
        if self.status == 'pass':
            return 0
        return 2 if self.code in _EXIT_2_CODES else 1

    @property
    def line(self) -> str:
        return f'{PREFIX} {self.verb} {self.status} {self.code}'
