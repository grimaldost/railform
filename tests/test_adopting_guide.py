"""The adopting guide names only verbs and flags the command has.

`docs/adopting.md` is prose, so nothing else notices when a verb is renamed or a flag is dropped.
This test reads the guide's code (fenced blocks and inline spans) and checks each
`railform <verb>` against the verb table of the command, and each flag after it against the
arguments that verb's parser defines, so a stale or invented name fails here.

What is checked:

- `railform <word>` in code: `<word>` is a verb with a module, and every `--flag` or `-f` that
  follows it on the line, up to a shell separator or the next `railform`, is one of that verb's
  options.
- `railform --flag`: the flag is one of the top-level options.
- An inline span that is itself a flag (`--pin`): the flag belongs to some verb.

Flags of other commands (`uvx --from`, `git`, `gh`) are not checked: they come before the
`railform <verb>` they run, or in a line with none. A placeholder (`railform <verb>`) is skipped.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from railform import cli

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / 'docs' / 'adopting.md'
README = ROOT / 'README.md'

TOP_LEVEL_FLAGS = frozenset({'--version', '-h', '--help'})

_FENCE = re.compile(r'^```.*?^```[ \t]*$', re.DOTALL | re.MULTILINE)
# A span may wrap onto the next line of a paragraph; its blanks then count as one.
_INLINE = re.compile(r'`([^`]+)`')
# `railform` as a word of its own: not `grimaldost/railform@sha`, `railform.toml`,
# `railform-sha` or `railform:`; what follows is the first token after the blanks.
_CALL = re.compile(r'(?<![\w./@:-])railform[ \t]+(?P<token>[^\s`]+)')
_VERB = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')
_FLAG = re.compile(r'(?<![\w-])(--[a-z][a-z0-9-]*|-[A-Za-z])(?![\w-])')
_SEPARATORS = re.compile(r'&&|\|\||[|;]')
_LINK = re.compile(r'\[[^\]\n]*\]\(([^)\s]+)\)')


def _verb_flags(verb: str) -> frozenset[str] | None:
    """The option strings of `verb`'s parser, or None when it has no module."""
    module = cli._load_verb(verb)
    if module is None:
        return None
    parser = argparse.ArgumentParser(prog=f'railform {verb}')
    module.add_arguments(parser)
    return frozenset(parser._option_string_actions)


def _all_flags() -> frozenset[str]:
    found: set[str] = set(TOP_LEVEL_FLAGS)
    for verb in cli.VERBS:
        found |= _verb_flags(verb) or frozenset()
    return frozenset(found)


def _code_pieces(text: str) -> tuple[list[str], list[str]]:
    """The lines of the fenced blocks, and the inline spans of the rest of the text."""
    lines = [line for block in _FENCE.findall(text) for line in block.splitlines()[1:-1]]
    spans = [' '.join(span.split()) for span in _INLINE.findall(_FENCE.sub('', text))]
    return lines, spans


def _flags_in(text: str) -> list[str]:
    return [match.group(1) for match in _FLAG.finditer(text)]


def check_guide(text: str) -> tuple[set[str], set[str], list[str]]:
    """The verbs and flags the text names, and the problems with them."""
    lines, spans = _code_pieces(text)
    verbs: set[str] = set()
    flags: set[str] = set()
    problems: list[str] = []
    everything = _all_flags()
    for piece in [*lines, *spans]:
        calls = list(_CALL.finditer(piece))
        for number, call in enumerate(calls):
            token = call.group('token').rstrip(',.')
            tail_end = calls[number + 1].start() if number + 1 < len(calls) else len(piece)
            tail = _SEPARATORS.split(piece[call.end() : tail_end])[0]
            if token.startswith('-'):
                for flag in _flags_in(token + ' ' + tail):
                    flags.add(flag)
                    if flag not in TOP_LEVEL_FLAGS:
                        problems.append(f'`railform {flag}` is not a top-level option: {piece!r}')
                continue
            if not _VERB.fullmatch(token):
                continue  # a placeholder such as <verb>, or a word that is not a verb name
            allowed = _verb_flags(token)
            if allowed is None:
                problems.append(f'`railform {token}` is not a verb of the command: {piece!r}')
                continue
            verbs.add(token)
            for flag in _flags_in(tail):
                flags.add(flag)
                if flag not in allowed:
                    problems.append(f'`railform {token}` has no option {flag}: {piece!r}')
    for span in spans:
        if span.lstrip().startswith('-'):
            for flag in _flags_in(span):
                flags.add(flag)
                if flag not in everything:
                    problems.append(f'no verb has the option {flag}: {span!r}')
    return verbs, flags, problems


def _guide_text() -> str:
    return GUIDE.read_text(encoding='utf-8')


def test_the_guide_names_only_verbs_and_flags_the_command_has() -> None:
    _, _, problems = check_guide(_guide_text())
    assert problems == []


def test_the_check_reads_the_guide_and_is_not_vacuous() -> None:
    verbs, flags, _ = check_guide(_guide_text())
    assert {'adopt', 'frame', 'arm', 'hooks-check'} <= verbs
    assert {'--pin', '--check', '--type', '--armed'} <= flags


@pytest.mark.parametrize(
    'added',
    [
        '`railform frobnicate`',
        '`railform adopt --frobnicate`',
        '`railform arm --pin`',
        '`railform frame --check --pin abc`',
        '`--bogus`',
        '`railform --versions`',
        '```\nrailform adopt --check --frobnicate\n```',
        '```\nuvx --from git+https://github.com/grimaldost/railform@<sha> railform nope\n```',
        '`railform hooks-check\n--nope`',
        '```\nrailform frame --check && railform arm --nope\n```',
    ],
)
def test_a_fake_verb_or_flag_added_to_the_guide_fails_the_check(added: str) -> None:
    text = _guide_text() + '\n' + added + '\n'
    assert check_guide(text)[2] != []


@pytest.mark.parametrize(
    'added',
    [
        '`railform adopt --pin <sha> --check`',
        '`railform frame --check`, then `railform adopt --check`',
        '```\nuvx --from git+https://github.com/grimaldost/railform@<40-hex sha> railform arm\n```',
        '```\ngit config --local core.hooksPath .githooks\n```',
        '`railform <verb>` and `uvx --from x railform adopt`',
        '`railform --version` and `railform -h`',
        '`--help`',
        'The railform repository, railform.toml, railform-tag-watch.yml and a railform job.',
        '`grimaldost/railform` and `railform-sha`',
        '```yaml\n  # >>> railform: owned by `railform adopt`\n```',
        '```\nrailform branch-name -- docs/x\n```',
    ],
)
def test_the_check_accepts_real_names_and_other_commands(added: str) -> None:
    assert check_guide(added)[2] == []


def test_the_readme_links_to_the_guide() -> None:
    assert GUIDE.is_file()
    assert '](docs/adopting.md)' in README.read_text(encoding='utf-8')


def test_the_relative_links_of_the_guide_resolve() -> None:
    targets = [t for t in _LINK.findall(_guide_text()) if not t.startswith(('http', '#'))]
    assert targets, 'the guide links to nothing, so the check proves nothing'
    missing = [t for t in targets if not (GUIDE.parent / t.split('#')[0]).exists()]
    assert missing == []
