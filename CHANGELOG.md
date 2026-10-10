# Changelog

All notable changes to this project are recorded here, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form.

## [Unreleased]

### Added

- Repository and package skeleton: `pyproject.toml` (built with `uv_build`, Python 3.11 or
  newer, PyYAML as the only runtime dependency), the committed `uv.lock`, and the `railform`
  package with its console script and `python -m railform`.
- The command-line entry point: `railform --version`, verb lookup by module name, the verdict
  line `railform-verdict: <verb> <pass|fail> <code>` on every exit path, the exit codes 0, 1 and
  2, and the `$RAILFORM_LEDGER` append. The ten verbs of the first wave exit 2 with
  `not-implemented` until their modules land; an unknown verb exits 2 with `usage`; an exception a
  verb does not handle is caught by the entry point, which prints the traceback on standard error,
  the verdict line `fail internal-error` and exits 2.
- Tests: the version, the verdict line of every verb, the ledger, no model SDK or model API key
  variable under `src/`, no install of railform by name, and the shape of the CI workflow.
- CI on Ubuntu and Windows with Python 3.11 and 3.14 (lock check, sync, format, lint, `ty`,
  pytest), a zizmor audit job, and Dependabot for GitHub Actions.
- `README.md`, `CONTRIBUTING.md` and this changelog.
- `.gitattributes` with `* text=auto eol=lf`, so wrappers and the files `railform adopt` owns keep
  LF line endings on every system.

### Changed

- `--help` and `-h`, at the top level or after a verb, print the help text, exit 0, print no
  verdict line and never write to `$RAILFORM_LEDGER`, like `--version`.
- `SystemExit` raised inside a verb (at import, in `add_arguments` or in `run`) is reported as
  `internal-error` with exit 2; `KeyboardInterrupt` still propagates.
- Standard output and standard error write characters they cannot encode as backslash escapes, so
  a finding outside the console encoding no longer hides the verdict line.
- Dependabot subjects for GitHub Actions start with `ci:`, so they pass the Conventional Commits
  rule.
- The tests run in a throwaway git repository, never in the developer's clone, and the scanner for
  installs by name also flags a flag with a value before the name (`uvx --python 3.12 ...`).

### Removed

- The unused `pytest-cov` test dependency.

### Fixed

- The author name in `pyproject.toml` now matches `LICENSE`.
