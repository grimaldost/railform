"""The verb `commit-range BASE...HEAD`: judge every commit a branch adds.

Development-rails spec, §4; the rules live in `railform.commit_lane`. A commit with more than one
parent is exempt from the subject rule; every commit, merges included, is checked for
attribution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from railform import commit_lane
from railform.verdict import Verdict

if TYPE_CHECKING:
    import argparse

VERB = 'commit-range'
SHORT = 12


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        'Check the commits that HEAD adds to BASE (since their merge base): the subject rule '
        'for every commit with at most one parent, the attribution rule for every commit.'
    )
    parser.add_argument('range', metavar='BASE...HEAD', help='for example origin/main...HEAD')


def run(args: argparse.Namespace) -> Verdict:
    try:
        rules, root = commit_lane.load_rules()
        commits = commit_lane.commits_in_range(root, args.range)
    except commit_lane.LaneError as exc:
        return Verdict.usage(VERB, f'{VERB}: {exc}')
    subject: list[str] = []
    attribution: list[str] = []
    for commit in commits:
        found_subject, found_attribution = commit_lane.check_message(
            commit.sha[:SHORT], commit.message, rules, subject_exempt=commit.parents > 1
        )
        subject += found_subject
        attribution += found_attribution
    return commit_lane.verdict(VERB, subject, attribution)
