"""The verb `pr-text [--event PATH]`: judge a pull request's title and body for attribution.

Development-rails spec, §4; the rules live in `railform.commit_lane`. The text is read from the
event payload: `pull_request.title` and `pull_request.body`, a `null` or absent value being empty
text (GitHub sends `null` for an empty body). Only the attribution rule applies: a title is not a
commit subject until a squash merge makes it one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from railform import commit_lane
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse

VERB = 'pr-text'
EVENT_ENV = 'GITHUB_EVENT_PATH'


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Check that no line of the pull request title or body attributes authorship to an AI '
        f'tool. Reads the event payload at --event, else at ${EVENT_ENV}.'
    )
    parser.add_argument('--event', metavar='PATH', help=f'the event payload (default ${EVENT_ENV})')


def _text(pull_request: dict[str, Any], key: str) -> str:
    value = pull_request.get(key)
    if value is None:
        return ''
    if not isinstance(value, str):
        raise commit_lane.LaneError(f'pull_request.{key} is not text')
    return value


def _read_event(args: argparse.Namespace) -> tuple[str, str]:
    path = args.event or os.environ.get(EVENT_ENV, '')
    if not path:
        raise commit_lane.LaneError(f'neither --event nor ${EVENT_ENV} is set')
    return read_pull_request(path)


def read_pull_request(path: str) -> tuple[str, str]:
    """The title and body of the pull request in the event payload at `path`; a `null` or absent
    value is empty text. LaneError when the payload cannot be read or holds no pull request."""
    try:
        payload = json.loads(Path(path).read_bytes().decode('utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise commit_lane.LaneError(f'cannot read the event payload: {exc}') from exc
    pull_request = payload.get('pull_request') if isinstance(payload, dict) else None
    if not isinstance(pull_request, dict):
        raise commit_lane.LaneError('the event payload has no pull_request object')
    return _text(pull_request, 'title'), _text(pull_request, 'body')


def run(args: argparse.Namespace) -> Verdict:
    try:
        rules, _ = commit_lane.load_rules()
        title, body = _read_event(args)
    except commit_lane.LaneError as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    _, in_title = commit_lane.check_message('pull request title', title, rules, subject_exempt=True)
    _, in_body = commit_lane.check_message('pull request body', body, rules, subject_exempt=True)
    return commit_lane.verdict(VERB, [], [*in_title, *in_body])
