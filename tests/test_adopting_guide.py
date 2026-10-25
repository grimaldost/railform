"""The adopting guide names only verbs and flags the command has.

`docs/adopting.md` is prose, so nothing else notices when a verb is renamed or a flag is dropped.
This test reads the guide's code (fenced blocks, with ``` or ~~~, and inline spans) and its plain
prose, and checks each `railform <verb>` against the verb table of the command, and each flag after
it against the arguments that verb's parser defines, so a stale or invented name fails here.

What is checked:

- `railform <word>` in code: `<word>` is a verb with a module, and every `--flag` or `-f` that
  follows it on the line, up to a shell separator or the next `railform`, is one of that verb's
  options.
- `railform --flag`: the flag is one of the top-level options.
- An inline span that is itself a flag (`--pin`): the flag belongs to some verb.
- An inline span that starts with a verb and no `railform` (`adopt --check`): read as if the
  prefix were there.
- Plain prose: `railform <word>` outside code is judged when the word is a verb or is followed by
  a flag; any other word after `railform` is English (the railform job) and is not judged.
- A combination of flags the command rejects: the verb's own parser refuses it (`frame --check
  --json`), or `RUN_LEVEL_CONFLICTS` lists it, and a test runs each listed combination through
  the command so that the table cannot go stale.
- Step 4 of the guide lists every `railform` command the jobs of `python-ci.yml` run, read from
  the workflow, with the flags it passes.

Flags of other commands (`uvx --from`, `git`, `gh`) are not checked: they come before the
`railform <verb>` they run, or in a line with none. A placeholder (`railform <verb>`) is skipped.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import NoReturn, TypeAlias

import pytest

from railform import cli

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / 'docs' / 'adopting.md'
README = ROOT / 'README.md'
WORKFLOW = ROOT / '.github' / 'workflows' / 'python-ci.yml'

TOP_LEVEL_FLAGS = frozenset({'--version', '-h', '--help'})

# Combinations of flags that a verb's `run` refuses after its parser has accepted them, so no
# parser can say so: (verb, flags present, flags that must be absent, a fragment of the
# diagnostic). `test_each_run_level_conflict_is_refused_by_the_command` runs every row.
RUN_LEVEL_CONFLICTS = (
    ('adopt', frozenset({'--check', '--pin'}), frozenset(), 'takes neither'),
    ('adopt', frozenset({'--check', '--type'}), frozenset(), 'takes neither'),
    ('version-sites', frozenset({'--base', '--bump'}), frozenset(), 'does not bump'),
    ('version-sites', frozenset({'--prefix'}), frozenset({'--bump'}), 'only meaningful'),
)
SAMPLE_VALUES = {
    '--pin': 'a' * 40,
    '--type': 'python-package',
    '--base': 'HEAD',
    '--bump': '1.2.3',
    '--prefix': 'x',
}

# A fence of three or more backticks or tildes, closed by the same run; it may be indented.
_FENCE = re.compile(
    r'^[ \t]*(?P<fence>`{3,}|~{3,}).*?^[ \t]*(?P=fence)[ \t]*$', re.DOTALL | re.MULTILINE
)
# A span may wrap onto the next line of a paragraph; its blanks then count as one.
_INLINE = re.compile(r'`([^`]+)`')
# `railform` as a word of its own: not `grimaldost/railform@sha`, `railform.toml`,
# `railform-sha` or `railform:`; what follows is the first token after the blanks.
_CALL = re.compile(r'(?<![\w./@:-])railform[ \t]+(?P<token>[^\s`]+)')
_VERB = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*')
_FLAG = re.compile(r'(?<![\w-])(--[a-z][a-z0-9-]*|-[A-Za-z])(?![\w-])')
_SEPARATORS = re.compile(r'&&|\|\||[|;]')
# Where the flags of a command written in plain prose stop.
_PROSE_END = re.compile(r'&&|\|\||[|;,:()]|\.(?=\s|$)')
_LINK = re.compile(r'\[[^\]\n]*\]\(([^)\s]+)\)')
# A `railform <verb> ...` call in a `run:` line of the reusable workflow.
_WORKFLOW_CALL = re.compile(
    r'railform@\$RAILFORM_SHA"[ \t]+railform[ \t]+(?P<verb>[a-z][a-z0-9-]*)(?P<rest>[^\n]*)'
)

Found: TypeAlias = tuple[set[str], set[str], list[str]]


class _RefusedError(Exception):
    """The parser of a verb refused the arguments."""


class _RaisingParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _RefusedError(message)


def _verb_parser(verb: str) -> argparse.ArgumentParser | None:
    module = cli._load_verb(verb)
    if module is None:
        return None
    parser = _RaisingParser(prog=f'railform {verb}', allow_abbrev=False)
    module.add_arguments(parser)
    return parser


def _verb_flags(verb: str) -> frozenset[str] | None:
    """The option strings of `verb`'s parser, or None when it has no module."""
    parser = _verb_parser(verb)
    return None if parser is None else frozenset(parser._option_string_actions)


def _all_flags() -> frozenset[str]:
    found: set[str] = set(TOP_LEVEL_FLAGS)
    for verb in cli.VERBS:
        found |= _verb_flags(verb) or frozenset()
    return frozenset(found)


def _code_pieces(text: str) -> tuple[list[str], list[str], list[str]]:
    """The lines of the fenced blocks, the inline spans of the rest of the text, and the plain
    prose left once both are taken out, a paragraph to the item."""
    lines = [line for block in _FENCE.finditer(text) for line in block.group(0).splitlines()[1:-1]]
    rest = _FENCE.sub('\n\n', text)
    spans = [' '.join(span.split()) for span in _INLINE.findall(rest)]
    paragraphs = [' '.join(part.split()) for part in re.split(r'\n\s*\n', _INLINE.sub(' ', rest))]
    return lines, spans, [paragraph for paragraph in paragraphs if paragraph]


def _flags_in(text: str) -> list[str]:
    return [match.group(1) for match in _FLAG.finditer(text)]


def _refused_combination(verb: str, tail: str, flags: list[str]) -> str | None:
    """Why the command refuses `flags` together, or None: the parser's own refusal of flags that
    exclude one another, or a row of RUN_LEVEL_CONFLICTS."""
    parser = _verb_parser(verb)
    if parser is not None:
        try:
            parser.parse_known_args(tail.split())
        except _RefusedError as exc:
            if 'not allowed with' in str(exc):
                return str(exc)
        except SystemExit:  # `-h` printed the help
            pass
    given = set(flags)
    for name, present, absent, fragment in RUN_LEVEL_CONFLICTS:
        if name == verb and present <= given and not absent & given:
            return f'{fragment}: {" ".join(sorted(present))}'
    return None


def _judge_verb(verb: str, tail: str, piece: str, found: Found) -> None:
    """Judge the flags `tail` gives `verb`; a verb with no module is a problem."""
    verbs, flags, problems = found
    allowed = _verb_flags(verb)
    if allowed is None:
        problems.append(f'`railform {verb}` is not a verb of the command: {piece!r}')
        return
    verbs.add(verb)
    named = _flags_in(tail)
    for flag in named:
        flags.add(flag)
        if flag not in allowed:
            problems.append(f'`railform {verb}` has no option {flag}: {piece!r}')
    refusal = _refused_combination(verb, tail, named)
    if refusal is not None:
        problems.append(f'`railform {verb}` refuses these options together ({refusal}): {piece!r}')


def _judge_calls(piece: str, end: re.Pattern[str], found: Found, *, prose: bool) -> None:
    """Judge every `railform <word>` in `piece`; the flags of a call stop at `end`. In plain
    prose a word that is not a verb is English (the railform job), judged only when a flag
    follows it."""
    _, flags, problems = found
    calls = list(_CALL.finditer(piece))
    for number, call in enumerate(calls):
        token = call.group('token').rstrip(',.')
        tail_end = calls[number + 1].start() if number + 1 < len(calls) else len(piece)
        tail = end.split(piece[call.end() : tail_end])[0]
        if token.startswith('-'):
            for flag in _flags_in(token + ' ' + tail):
                flags.add(flag)
                if flag not in TOP_LEVEL_FLAGS:
                    problems.append(f'`railform {flag}` is not a top-level option: {piece!r}')
            continue
        if not _VERB.fullmatch(token):
            continue  # a placeholder such as <verb>, or a word that is not a verb name
        if prose and token not in cli.VERBS and not tail.lstrip().startswith('-'):
            continue
        _judge_verb(token, tail, piece, found)


def check_guide(text: str) -> Found:
    """The verbs and flags the text names, and the problems with them."""
    lines, spans, prose = _code_pieces(text)
    found: Found = (set(), set(), [])
    verbs, flags, problems = found
    everything = _all_flags()
    for piece in [*lines, *spans]:
        _judge_calls(piece, _SEPARATORS, found, prose=False)
    for paragraph in prose:
        _judge_calls(paragraph, _PROSE_END, found, prose=True)
    for span in spans:
        words = span.split(maxsplit=1)
        if span.lstrip().startswith('-'):
            for flag in _flags_in(span):
                flags.add(flag)
                if flag not in everything:
                    problems.append(f'no verb has the option {flag}: {span!r}')
        elif words and words[0] in cli.VERBS and not _CALL.search(span):
            tail = _SEPARATORS.split(words[1] if len(words) > 1 else '')[0]
            _judge_verb(words[0], tail, span, found)
    return verbs, flags, problems


def _guide_text() -> str:
    return GUIDE.read_text(encoding='utf-8')


def _section(text: str, number: int) -> str:
    """The text of the numbered step `## <number>. ...` up to the next heading."""
    match = re.search(rf'^## {number}\. .*?(?=^## |\Z)', text, re.DOTALL | re.MULTILINE)
    assert match is not None, f'the guide has no step {number}'
    return match.group(0)


def _workflow_calls() -> list[tuple[str, frozenset[str]]]:
    """Each `railform <verb>` the jobs of the reusable workflow run, with the flags it passes;
    `frame --github-output` writes the job outputs and is no check for a person to run."""
    calls = [
        (match.group('verb'), frozenset(_flags_in(match.group('rest'))))
        for match in _WORKFLOW_CALL.finditer(WORKFLOW.read_text(encoding='utf-8'))
    ]
    return [(verb, flags) for verb, flags in calls if '--github-output' not in flags]


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
        # combinations the command rejects: its own parser, or its run
        '`railform adopt --pin <sha> --check`',
        '`railform adopt --check --type python-package`',
        '`railform frame --check --json`',
        '`railform version-sites --base origin/main --bump 1.2.3`',
        '`railform version-sites --prefix x`',
        # ~~~ fences
        '~~~\nrailform adopt --frobnicate\n~~~',
        '~~~\nrailform adopt --pin <sha> --check\n~~~',
        '~~~sh\nrailform nope --check\n~~~',
        # spans without the railform prefix
        '`adopt --frobnicate`',
        '`adopt --pin <sha> --check`',
        '`frame --check --pin abc`',
        '`hooks-check\n--nope`',
        # plain prose
        'Run railform adopt --frobnicate before the pull request.',
        'Run railform adopt --pin <sha> --check before the pull request.',
        'Then railform frobnicate --check fails.',
        'The line reads railform arm --pin, which arm lacks.',
        'It prints\nrailform frame --nope\nwhen run.',
        'Run railform --versions to see it.',
    ],
)
def test_a_fake_verb_or_flag_added_to_the_guide_fails_the_check(added: str) -> None:
    text = _guide_text() + '\n' + added + '\n'
    assert check_guide(text)[2] != []


@pytest.mark.parametrize(
    'added',
    [
        '`railform adopt --pin <sha>` and `railform adopt --check`',
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
        '~~~\nrailform adopt --pin <sha>\n~~~',
        '`adopt --check`, `frame --json` and `version-sites --base origin/main`',
        '`frame` and `arm` are verbs; `gate` and `checks` are jobs.',
        'Run railform adopt --pin <sha> first, then railform adopt --check.',
        'The railform job runs railform frame --check, railform hooks-check and nothing else.',
        'A railform release; railform adopt (the verb) writes the files.',
    ],
)
def test_the_check_accepts_real_names_and_other_commands(added: str) -> None:
    assert check_guide(added)[2] == []


@pytest.mark.parametrize(
    ('verb', 'present', 'absent', 'fragment'),
    RUN_LEVEL_CONFLICTS,
    ids=[f'{row[0]} {" ".join(sorted(row[1]))}' for row in RUN_LEVEL_CONFLICTS],
)
def test_each_run_level_conflict_is_refused_by_the_command(
    verb: str,
    present: frozenset[str],
    absent: frozenset[str],
    fragment: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert not present & absent
    monkeypatch.chdir(tmp_path)
    argv = [verb]
    for flag in sorted(present):
        argv += [flag, SAMPLE_VALUES[flag]] if flag in SAMPLE_VALUES else [flag]
    assert cli.main(argv) == 2
    assert fragment in capsys.readouterr().err


def test_step_4_lists_every_command_the_workflow_runs() -> None:
    calls = _workflow_calls()
    assert {verb for verb, _ in calls} >= {
        'frame',
        'private-refs',
        'commit-range',
        'pr-text',
        'branch-name',
        'changelog-gate',
        'version-sites',
        'hooks-check',
        'adopt',
    }
    assert ('version-sites', frozenset({'--base'})) in calls
    words = [line.split() for line in _code_pieces(_section(_guide_text(), 4))[0]]
    listed = [
        (line[1], frozenset(_flags_in(' '.join(line[2:]))))
        for line in words
        if len(line) > 1 and line[0] == 'railform'
    ]
    missing = [
        f'railform {verb} {" ".join(sorted(flags))}'.strip()
        for verb, flags in calls
        if not any(name == verb and flags <= given for name, given in listed)
    ]
    assert missing == [], 'step 4 does not list them, so a pull request fails what it never ran'


def _words(text: str) -> str:
    return ' '.join(text.split()).lower()


@pytest.mark.parametrize(
    'sentence',
    [
        # the release tag
        'a release tag goes on the merge commit of the release pull request',
        # the session-paths line
        'ci does not run `railform session-paths`; run it by hand',
        # the trailer, and a plugin's README
        'a `changelog: none (<reason>)` trailer declares its own commit only',
        "a plugin's readme is charged to that plugin's changelog",
        # a body edit
        'editing the title or body of a pull request starts a new run',
        "the earlier run's red check-run can be ignored",
        # edits outside the marker lines
        "edits outside the marker lines are the repository's",
        '`adopt --check` does not compare them with what adopt would write',
        'both `adopt` and `adopt --check` refuse a top-level `files:` or `exclude:`',
        # the free plan
        'http 403',
        'upgrade to github pro or make this repository public',
        # billing
        'under three seconds',
        'actions billing',
    ],
)
def test_the_guide_carries_the_points_the_adoptions_met(sentence: str) -> None:
    assert sentence in _words(_guide_text())


def test_the_readme_links_to_the_guide() -> None:
    assert GUIDE.is_file()
    assert '](docs/adopting.md)' in README.read_text(encoding='utf-8')


def test_the_relative_links_of_the_guide_resolve() -> None:
    targets = [t for t in _LINK.findall(_guide_text()) if not t.startswith(('http', '#'))]
    assert targets, 'the guide links to nothing, so the check proves nothing'
    missing = [t for t in targets if not (GUIDE.parent / t.split('#')[0]).exists()]
    assert missing == []
