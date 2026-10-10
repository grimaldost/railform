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
  `not-implemented` until their modules land; an unknown verb exits 2 with `usage`.
- Tests: the version, the verdict line of every verb, the ledger, no model SDK or model API key
  variable under `src/`, no install of railform by name, and the shape of the CI workflow.
- CI on Ubuntu and Windows with Python 3.11 and 3.14 (lock check, sync, format, lint, `ty`,
  pytest), a zizmor audit job, and Dependabot for GitHub Actions.
- `README.md`, `CONTRIBUTING.md` and this changelog.
