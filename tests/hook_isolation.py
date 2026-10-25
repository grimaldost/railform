"""The environment of every test that runs pre-commit hooks, isolated from the developer's home.

Git is isolated by `tests/conftest.py`. pre-commit runs its own clones under `no_git_env()`,
which drops `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM`, so the hooks also run with `HOME`,
`USERPROFILE` and `XDG_CONFIG_HOME` pointing at a temporary directory that holds an empty
`.gitconfig`, and with a `PRE_COMMIT_HOME` that is fresh each session and shared by every test
of the session, so pre-commit builds each hook environment once. The hook rail's network probe
(`tests/test_hook_rail.py`) runs in that same environment.

What still reaches the tests:
- every inherited environment variable the tests do not drop or set (`DROPPED`,
  `PRE_COMMIT_REMOTE_BRANCH`, git's locating variables), for example `PIP_INDEX_URL`,
  `UV_INDEX_URL` or a proxy, passed on to git, pip and uv;
- the system git configuration, in pre-commit's clones and the network probe;
- on Windows (checked), uv's user configuration `%APPDATA%\\uv\\uv.toml`, as `APPDATA` is not
  redirected, and pip's site-wide `%PROGRAMDATA%\\pip\\pip.ini`; pip's user configuration and
  cache, and virtualenv's app-data, resolve inside the temporary home;
- on Linux and macOS (derived from the tools' documented paths, not run), the swap stops the
  user-level pip and uv configuration and `~/.netrc` from reaching the hooks and the fixtures'
  `uv lock`; system-wide pip and uv configuration still reaches them.
`tests/conftest.py` applies the same variables, plus `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR`, to every
test through `scratch_repo`, not only to those that run hooks.
A developer behind a private package mirror sets `PIP_INDEX_URL` and `UV_INDEX_URL` in the
environment.

What the tests write outside the temporary directories: the uv cache (`UV_CACHE_DIR`, pinned to
the developer's own so uv stays warm); uv-managed Python installs (`UV_PYTHON_INSTALL_DIR`, pinned
likewise), an interpreter install rather than a cache, when a fixture asks for a Python uv has
not installed; and whatever pip and virtualenv write outside the home directory. On Windows
(checked) pip's cache and virtualenv's app-data land in the temporary home; on Linux and macOS
(derived) they follow it too unless `XDG_CACHE_HOME` or `XDG_DATA_HOME` is set.

The `outside` fixture of `tests/conftest.py` holds the variables; `hook_environment` puts them
on top of the test's own environment.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

# Variables that would point uv at railform's own environment, or make pre-commit skip hooks.
DROPPED = frozenset(
    {'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC', 'SKIP'}
)


def _uv_dir(kind: str) -> str:
    """Where uv keeps its `cache` or its managed `python` interpreters for the developer."""
    done = subprocess.run(
        ['uv', kind, 'dir'], capture_output=True, text=True, encoding='utf-8', check=True
    )
    return done.stdout.strip()


def outside_variables(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """The variables that point the hooks' tools away from the developer's home and caches."""
    home = tmp_path_factory.mktemp('home')
    (home / '.gitconfig').write_bytes(b'')
    (home / '.config').mkdir()
    return {
        'PRE_COMMIT_HOME': str(tmp_path_factory.mktemp('pre-commit-home')),
        'HOME': str(home),
        'USERPROFILE': str(home),
        'XDG_CONFIG_HOME': str(home / '.config'),
        'UV_CACHE_DIR': _uv_dir('cache'),
        'UV_PYTHON_INSTALL_DIR': _uv_dir('python'),
    }


def hook_environment(outside: dict[str, str]) -> dict[str, str]:
    """The environment of every hook-running command: the test's own, git already isolated by
    `tests/conftest.py`, with `outside` on top."""
    env = {key: value for key, value in os.environ.items() if key not in DROPPED}
    env.pop('PRE_COMMIT_REMOTE_BRANCH', None)
    return env | outside
