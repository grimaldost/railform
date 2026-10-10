"""The verb `commit-msg FILE`: judge the message file git passes to the commit-msg hook.

Development-rails spec, §4; the rules live in `railform.commit_lane`. Lines starting with the
comment character (`core.commentChar`, else `#`) are git comments and are ignored, and so is
everything after the scissors line.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from railform import commit_lane
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse

VERB = 'commit-msg'


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Check a commit message file: the subject is "type(scope)!: description" with a type '
        'of the frame (a merge in progress is exempt), and no line attributes authorship to an '
        'AI tool.'
    )
    parser.add_argument('file', metavar='FILE', help='the message file, as the hook receives it')


def _label(path: Path, root: Path) -> str:
    """The path as the repository sees it; a file outside it is named by its file name only."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def run(args: argparse.Namespace) -> Verdict:
    try:
        rules, root = commit_lane.load_rules()
        path = Path(args.file)
        try:
            message = path.read_bytes().decode('utf-8', errors='replace')
        except OSError as exc:
            raise commit_lane.LaneError(f'cannot read the message file: {exc}') from exc
        subject, attribution = commit_lane.check_message(
            _label(path, root),
            message,
            rules,
            subject_exempt=commit_lane.is_merging(root),
            comment_char=commit_lane.comment_char(root),
        )
    except commit_lane.LaneError as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    return commit_lane.verdict(VERB, subject, attribution)
