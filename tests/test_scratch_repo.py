"""Each test runs in its own scratch repository, so files a verb writes never leak."""

from pathlib import Path

LEAK = 'leaked-by-an-earlier-test.txt'


def test_a_file_written_in_one_test(scratch_repo: Path) -> None:
    (scratch_repo / LEAK).write_text('x', encoding='utf-8')
    assert Path.cwd() == scratch_repo


def test_is_not_seen_by_the_next(scratch_repo: Path) -> None:
    assert not (scratch_repo / LEAK).exists()
    assert (scratch_repo / '.git').is_dir()
