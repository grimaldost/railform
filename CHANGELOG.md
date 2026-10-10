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
- The frame: `src/railform/default_frame.toml` with every key of the development-rails spec,
  §2, plus `ci.extra_workflows`, `python.newest`, `hooks.pre_commit_version` and
  `meta.parameter_keys`, and the three presets `python-package`, `python-scripts` and
  `plugin-marketplace`. `commit.attribution_patterns` is keel's no-ai-attribution set at
  `c3313fd`, with keel's test cases ported over the frame value. `branch.pattern` is provisional
  until the branch rail measures it.
- The `frame` verb: `railform frame` prints the effective frame as TOML, `--json` as JSON,
  `--check` validates `railform.toml` (`unknown-key`, `wrong-type`, `bad-exception`,
  `unlisted-departure`), and `--github-output` appends the frame values to `$GITHUB_OUTPUT`.
- Frame shapes that later checks read: `changelog.records` and `release.tag_prefix` are arrays of
  `{prefix, file}` and `{prefix, tag}` rows (the longest prefix wins, and a repository's array
  replaces the default whole); `version.sites` rows are `{path, pattern, authored}`, `authored`
  defaulting to false; `release.metadata_paths` holds globs, to which the release check adds the
  record files and version-site paths. `hooks.pre_commit_version` is set only in the
  `plugin-marketplace` preset, whose test command pins the pytest railform locks. Two exceptions
  rows that cover the same departure fail `bad-exception`.
- The `hooks-check` and `arm` verbs and the wrapper template they share (`railform.wrapper`).
  `hooks-check` fails a hook wrapper that is missing (`missing`), not tracked as `100755`
  (`mode`) or not the rendered wrapper once CRLF is read as LF (`content`); `--armed` also fails
  unless `core.hooksPath` equals `hooks.dir` (`not-armed`). `arm` sets `core.hooksPath` in the
  local configuration. The hook directory, hook names and wrapper command come from the frame.
  A wrapper that pins a requirement to no version, such as `pre-commit== hook-impl`, is refused
  as a configuration error (exit 2). Ported from the commit-lane tests of the stack-radar engine;
  the stage, dev-group and frozen-`uv run` rules of that source are not ported yet.
  `hooks.dir` is read as a normalised path (`./.githooks`, `.githooks/` and a backslash path name
  the same directory), and `railform.wrapper` is the one module a caller needs: it renders the
  wrapper and validates `hooks.dir`.
- The commit lane: `railform commit-msg FILE`, `railform commit-range BASE...HEAD` and
  `railform pr-text [--event PATH]`, ported from keel's no-attribution hook and from
  mantis-research's commit-message script, with their tests. The subject follows
  `type(scope)!: description` with the frame's `commit.types`, and a commit with more than one
  parent is exempt; every message, merges and a pull request's title and body included, is
  checked against `commit.attribution_patterns`. Git comment lines and the scissors line are
  ignored by `commit-msg`; a `null` or absent pull request body is empty text.
- A `commit-lane` job in railform's own CI that runs `commit-range` and `pr-text` on pull
  requests, which now also trigger on `edited`.
- `commit-msg` reads git's comment character from `core.commentChar` (falling back to `#`, and
  reading `auto` as `#`); `commit-range` asks `git log` for no signatures, so `log.showSignature`
  cannot put gpg text in the hash field; an empty `commit.types` is a configuration error (exit 2).
- On an `edited` pull request event only the `commit-lane` job runs; `checks` and `audit` skip.
- The `session-paths` verb (the D1 guard): `railform session-paths --diff BASE...HEAD
  --frame-from REF [--glob PATTERN ...]` fails with `session-loaded` when the change, read with
  `git diff --no-renames`, touches a path matching a `session_loaded` glob of the frame at
  `REF` (the default frame if `REF` has no `railform.toml`), so a rename out of a loaded path
  still counts and the change cannot narrow its own guard. `--glob` adds a pattern, such as a
  series brief's, and never removes one. It also fails with `guard-narrowed` when the
  `railform.toml` at HEAD removes or excepts a glob that the frame at `REF` had.
  Names compare case-insensitively, and a malformed glob (an empty segment, a `.` or `..`
  segment, a leading or trailing `/`, or a backslash) is a configuration error, exit 2.

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
- Each test gets its own scratch git repository instead of one shared by the whole session, so a
  verb that writes files never leaks state into the next test.
- The help tests no longer skip once `frame` exists: the case for a verb not written yet uses a
  stand-in name, and the top-level and found-verb cases keep running.

### Removed

- The unused `pytest-cov` test dependency.

### Fixed

- The author name in `pyproject.toml` now matches `LICENSE`.
