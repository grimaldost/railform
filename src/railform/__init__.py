"""railform: shared CI workflow, commit hooks and repository checks, with no model inside."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version('railform')
except PackageNotFoundError:  # pragma: no cover - only outside an installed environment
    __version__ = '0+unknown'

__all__ = ['__version__']
