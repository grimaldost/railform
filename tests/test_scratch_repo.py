"""Each test runs in its own scratch repository, so files a verb writes never leak."""

from pathlib import Path

import pytest

MARK = 'written-by-'


@pytest.mark.parametrize('name', ['first', 'second'])
def test_no_test_sees_a_file_another_test_wrote(name: str, scratch_repo: Path) -> None:
    # Whichever case runs second fails against a shared repository, in any order.
    assert Path.cwd() == scratch_repo
    assert (scratch_repo / '.git').is_dir()
    assert not list(scratch_repo.glob(f'{MARK}*'))
    (scratch_repo / f'{MARK}{name}').write_text('x', encoding='utf-8')
