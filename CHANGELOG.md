# Changelog

All notable changes to this project are recorded here, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) form.

## [Unreleased]

## [0.2.0] - 2026-10-24

### Added

- `docs/adopting.md`, an ordered guide to adopting a repository: the inventory, `railform adopt
  --pin`, departures as `exceptions` rows, the frame and adoption checks, pull request workflows
  beside `ci.yml`, `railform arm`, the `gate` ruleset, type detection, a python-package example
  and the move to a newer pin. `tests/test_adopting_guide.py` fails when the guide names a verb or
  a flag the command does not have.
- `railform private-refs --event PATH` adds the title and body of the pull request in a GitHub
  event payload to the scan, reported as `pull request title` and `pull request body`. The
  `commit-lane` job of the reusable workflow runs
  `private-refs --diff "origin/${BASE_REF}...HEAD" --event "$GITHUB_EVENT_PATH"`, so a home path
  (or, with a names file, a private name) in a commit message or in the pull request's text now
  fails CI; before, the `checks` job scanned the tracked files only and `pr-text` checked
  attribution only. A Dependabot pull request whose body quotes upstream release notes can fail
  with `home-path` for a path it did not write: edit the body to drop the path, or add that user
  segment to `private_refs.placeholders`.
- `railform version-sites --base REF` reads `version.sites` and `release.tag_prefix` from the
  `railform.toml` committed at the merge base of REF and HEAD (the working tree's when that commit
  has none) and names the frame it read; it does not combine with `--bump`. The `changelog` job
  of the reusable workflow runs `version-sites --base "origin/${BASE_REF}"` on pull requests, so
  a pull request can no longer drop the version site that disagrees from its own frame.
- `changelog.unwatched`, a list of path prefixes that no changelog record is charged for, by
  default `["uv.lock", ".github/"]` in every type. The `python-scripts` and `plugin-marketplace`
  presets watch every path, so a Dependabot pull request that moved only `uv.lock` or a workflow
  failed `changelog-gate` as unrecorded, and it cannot add a trailer. A records row whose prefix
  is at least as long as an entry watches the path again; the list is not a parameter key, so
  a list with an entry the preset lacks needs an `exceptions` row. A repository whose only
  tracked paths are the record files, the lock and the workflows fails `watches-nothing`.
- An `exceptions` row may leave a default element out of a guard list: a row for
  `session_loaded` or `private_refs.placeholders` names the elements in `drop`, with a reason,
  and gives no `value`. A row whose `drop` is empty or names an element the default lacks, and a
  second row for the same list, is `bad-exception` and drops nothing. A repository can now
  tighten the placeholder floor (`user`, `default`, `public`, `shared` and the rest).
  `railform session-paths` fails the pull request that adds a `session_loaded` drop with
  `guard-narrowed`, so the change is seen; once the drop is in the frame at `--frame-from`, the
  glob no longer guards. Before, a guard list could not shrink at all.
- `release.pr_branch_prefix` (default `release/`) and `release.pr_title_pattern` (default
  `^release(\([^)]*\))?!?: `, matched at the start of the title) set the release pull request
  convention that `railform release-check` looks for; they were fixed in the code. An empty one
  identifies nothing by its side, and the `no-release-pr` finding names the convention it used.
  Changing either needs an `exceptions` row: a broader convention weakens `no-release-pr` and
  `tag-placement`.
- The README opens with the five steps of an adoption, each linked to its section of
  `docs/adopting.md`.

### Changed

- `exceptions` rows match their departure by key, and only a departure that loosens a check
  needs one. A row is a `key` and a `reason`; `value` is optional and, when given, must equal the
  departed value, so every row written before still passes and a long `python.test_command` is
  written once. The departures that need a row (`LOOSENING` in `src/railform/frame.py`, a table
  in the README's section on the frame): a changed `python.test_command`, `python.type_check`,
  `branch.pattern`, `hooks.wrapper`, `changelog.trailer` (a template of `<reason>` alone
  declares every commit), `release.pr_branch_prefix`, `release.pr_title_pattern` or
  `private_refs.names_file_env`; `changelog.enabled` or `release.enabled` off;
  `ci.self_hosted` on; an element added to `ci.extra_workflows`, `changelog.unwatched`,
  `branch.allow`, `release.metadata_paths` or `commit.types`; an element left out of `ci.os`,
  `commit.attribution_patterns`, `hooks.names` or `python.lint_paths`. Every other departure,
  such as `ci.setup.fetch_depth`, a longer `ci.os`, an empty `changelog.unwatched` or
  `changelog.consumer_affecting = "error"`, needs none, and a row may still name it. A row for a
  key that does not depart, and a row with no key, stay `bad-exception`.
- The README documents a limit of `railform release-check`: one pull request identifies one
  version per line, the first of its branch or title, so a pull request that releases two lines
  fails `no-release-pr` for the second line's tag.
- `railform changelog-gate` reads its frame from the `railform.toml` committed at the merge base
  of the range, and from the working tree only when the merge base has none (the adoption pull
  request). A pull request that edits `changelog.records`, `version.sites`,
  `release.tag_prefix`, `changelog.enabled` or another key is judged by the frame it replaces, and
  its frame holds from the next pull request on; an edit the old frame would fail, such as
  moving a version site to another file, lands in three pull requests (README, "The changelog
  gate", and `docs/adopting.md`, "Changing the frame after adoption"). The gate now resolves the range before it reads `changelog.enabled`, and a
  finding line names the frame read. A `railform.toml` at the merge base that breaks the schema
  exits 2.
- A `changelog.records` watch that covers no tracked path at HEAD fails the changelog gate with
  `watches-nothing`, instead of printing `::warning::changelog.records: watches no tracked path`
  and passing. The watch judged is the frame at HEAD, so the pull request that mends an empty
  watch passes.
- The reusable workflows pin `astral-sh/setup-uv` at v10.3.0 (`1c37ad07a6a961277cf70c0d37d6f313000f5884`), up from v10.2.0.
- `railform adopt` writes the tag-watch caller only while `release.enabled` is true: with the
  release check off it writes none, and `--check` does not ask for one (a missing caller is
  `drift` again once the check is on). A caller that exists is kept at the pin and judged either
  way. On the first run it also seeds `release.since` in the new `railform.toml` with the tag
  just past the newest `v` tag of the repository (`v0.4.3` when the newest is `v0.4.2`), since
  the release check judges its floor tag itself; before, an adopter with older tags got a red
  daily run of the tag watch until it set `since` by hand. A repository with no `v` tag gets no
  `release` table.
- `railform version-sites` accepts two states between releases. An authored site (the newest
  changelog heading) that reads `X.Y.Z` or a lower release passes beside a writable site that
  reads `X.Y.Z.devN`, and takes no part in the count; an authored site whose file holds only
  `## [Unreleased]` is skipped with a finding instead of failing `unreadable`. Writable sites
  still have to agree, dev versions included.
- The default `commit.attribution_patterns` name each product as a whole word and cover more of
  them: the stamp arm now catches "Generated with ChatGPT" and "Generated with GitHub Copilot",
  and neither arm refuses an ordinary line that names `CLAUDE.md` (or an address such as
  `bob@gptools.example`).

## [0.1.0] - 2026-10-11

The first release of railform: the shared CI workflow, the commit hooks and the repository checks
of the development-rails spec, referenced at a pinned commit. It has thirteen verbs: `frame`,
`private-refs`, `commit-msg`, `commit-range`, `pr-text`, `hooks-check`, `arm`, `session-paths`,
`adopt`, `branch-name`, `changelog-gate`, `version-sites` and `release-check`.

keel and craft-collection move to this pin with `railform adopt --pin <release SHA>`; convoy and
mantis-research adopt railform for the first time at it.

### Added

- `railform version-sites [--bump X.Y.Z [--prefix PREFIX]]` (development-rails spec, §15), the
  version-site registry ported from keel's `VERSION_SITES` and `scripts/bump_version.py`. The
  sites are `version.sites` rows of `{ path, pattern, authored }`, grouped into release lines by
  `release.tag_prefix`; within a line the sites must agree on the version most of them read, and
  every disagreeing or unreadable site is named in one run (`mismatch`, `unreadable`). `--bump`
  writes the version to the non-authored sites of one line, byte for byte except the span of the
  pattern's first group, and then runs the check; an authored site (the newest changelog heading) is checked and never
  rewritten. The reusable workflow runs it in the `checks` job on every leg. The module also holds
  the reading of release lines and version sites that the changelog gate and the release check
  share.
- railform declares its own `version.sites` in `railform.toml`: the version in `pyproject.toml`
  and the newest `## [X.Y.Z]` heading of this changelog (authored), so `railform version-sites`
  judges its releases.
- The verb table of the command names `changelog-gate`, `version-sites` and `release-check`; a
  name without a module exits 2 with `not-implemented`.
- The `changelog-gate` verb (development-rails spec, §14), ported from the stack-radar engine's
  changelog gate with its tests: `railform changelog-gate BASE...HEAD` fails a commit that
  changes a path a `changelog.records` row watches when the range records nothing in that row's
  file and the commit carries no `changelog.trailer` declaration (`unrecorded`); a merge is
  charged with its resolution only. An added `## [X.Y.Z]` heading must match the version of
  record, read through `version.sites`, and move it forward (`release-heading`). A contract
  surface changed without `(consumer-affecting)` in a record is a `::warning::` by default and
  fails with `changelog.consumer_affecting = "error"` (`consumer-affecting`). An edited released
  heading (a new date, a trailing space, a CRLF conversion) is not a cut, versions read ASCII
  digits only, and a records watch that covers no tracked path prints a `::warning::`. The gate
  reads release lines and version sites through `railform.version_sites`, so the version-site
  check's configuration errors (a duplicated prefix, a site path that is empty, absolute, holds
  `..` or a backslash, a pattern with no group, a site no tag row covers) exit 2 here too.
- `railform release-check` (development-rails spec, §16): every release tag from its row's floor
  on is annotated (`lightweight-tag`, ported from keel's release-flow test), sits on the merge
  commit of the pull request that released it, found by a `release/` head branch or a `release`
  title carrying the version and the line's name (`no-release-pr`, `tag-placement`), and its
  `release:` commits change only release metadata (`release-commit`). The pull requests come from
  the GitHub API, or from the file `$RAILFORM_PULLS_FILE` names, with no network. An API that
  cannot be read exits 2 with the new code `api-error`. It reads release lines and version sites
  through `version-sites`' reading, so it exits 2 with `usage` on the same site configuration
  errors, and on a floor that is not a tag of its row.
- The reusable `.github/workflows/tag-watch.yml`, which runs `release-check` from the commit its
  required input `railform-sha` names, and its caller `railform-tag-watch.yml`, which
  `railform adopt` writes in every adopter: on every tag push, daily and on demand. railform
  runs its own caller by local path.
- An optional `since` on `release.tag_prefix` rows: the row's own floor, a tag name of that row,
  in place of `release.since`. The effective frame fills in `""` on a row that omits it.
- The pilots' folds (development-rails spec, §13). Each of the three pilots, one per project
  type, needed a railform change; each change listed here, under Changed and under Fixed has a
  test that fails without it.
- `railform frame --github-output` writes `hooks_python`, the newest of `python.versions`, and
  the `hooks` job of the reusable workflow runs the hygiene replay's pre-commit on it, so an
  adopter's `default_language_version` naming that Python resolves.
- `**/output-styles/**` in the default `session_loaded` globs: a plugin's output styles are
  loaded by a session.
- `railform adopt` and `adopt --check` report a `paths`, `paths-ignore`, `branches` or
  `branches-ignore` filter on the `pull_request` trigger in a finding line: a pull request the
  filter leaves out reports no `gate`.
- Tests: the pipeline lines share one bash shell (an export on one line holds on the next); every
  hook adopt writes runs at a declared stage; every `uv run` railform ships is frozen; the model
  evaluates a `uses:` step's `with:`; armed fixture clones accept a commit message quoting a
  conflict marker and a `git commit --fixup`, and commit through an interpreter-launch wrapper.
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
- The `private-refs` verb (`src/railform/private_refs.py`): a paths half that fails a
  home-directory path of any of six shapes (`X:\Users\<name>\`, `X:/Users/<name>/`, the
  JSON-escaped form, `X--Users-<name>-`, `/Users/<name>/`, `/home/<name>/`) unless the user segment
  is in `private_refs.placeholders` or plainly a placeholder, and a names half that reads a private
  list only from the file `private_refs.names_file_env` names, matches whole tokens, and is silent
  without the file (`--require-names` fails it with `names-file-missing`). Scope: the given paths,
  else `--diff` (changed files at the tip and the range's commit messages), else every tracked
  file; `--text` adds files. Findings never print a matched name or the user segment of a home path.
- A `private-refs` step in the `checks` job of CI, right after the sync so a failing test never
  hides a leak.
- `private-refs` details settled on review: a segment ends at a separator, whitespace, a quote or
  the end of the line (`cd C:\Users\<name>` and `HOME=/home/<name>` are caught); `/home/` is read
  only where it is not preceded by a word character, `.` or `-`; `--text` takes one file per flag
  and repeats; a usage error redacts a given path like a finding does.
- `private-refs`: a segment also ends at a backtick and at any of `<>(){}[],;:|`, and a trailing
  full stop is dropped before the placeholder comparison, so `C:\Users\<name>>`, `/home/<name><br>`
  and `<code>/home/<name></code>` are caught while `/home/me,` and `(e.g. /home/user)` pass.
- The default `private_refs.placeholders` gains the system folders `public`, `default`, `shared`
  and `runneradmin`, which are not people. This extends the spec's six names.
- The `adopt` verb and its templates in `src/railform/templates/`. `railform adopt [--type T]
  [--pin SHA]` writes, between marker lines, the `railform` and `gate` jobs of `ci.yml` (the call
  to the reusable workflow at the pin with the pin as the input `railform-sha`, and the required
  status that needs every other job), the `pull_request` trigger types, the railform and hygiene
  entries of `.pre-commit-config.yaml` (pre-commit-hooks v5.0.0 at its commit), and the Dependabot
  ignore entry for railform; it writes the hook wrappers as `100755` in the git index, a stub in
  the previous hooks directory, the tag-watch caller once the package carries its template, and
  the first `railform.toml`. Everything outside the owned parts is kept byte for byte, and a
  second run changes nothing. `--pin` moves every reference at once. `--check` fails with
  `drift`, `pin-mismatch` or `extra-pr-workflow`, and skips the pin comparisons in a
  self-hosted repository (`ci.self_hosted`).
- Migration fixtures for the three project types, built at test time, and tests that adopt keeps
  their extras and that each type's pipeline steps pass in the adopted fixture. The pipeline
  tests carry the new `slow` marker (they need the network or a warm uv cache) and run by
  default.
- The frame key `ci.self_hosted`, a preset-governed boolean, false by default. Only with it true
  does adopt write the `railform` job as a call by local path; without it such a call is
  `drift`.
- From PR07's review, `railform adopt` now:
  - confirms that the `ci.yml` region holds the jobs `railform` and `gate` themselves;
  - reports a missing pin site as `drift`;
  - counts `pull_request_target` and unparseable workflow files as extra;
  - picks the previous hooks directory only from `100755` entries outside `hooks.dir` and the
    `session_loaded` globs, and never overwrites a tracked file there that is not `100755`;
  - refuses a target behind a symlink or junction, and a `hooks.dir` inside `.git`, before any
    write;
  - stages the wrappers as their exact bytes;
  - gives the `gate` step `shell: bash` and an `always() &&` condition.
- The test harness removes git's repository-locating variables and isolates the global and
  system configuration for every test, so a `GIT_DIR` inherited from a hook can no longer make
  `git init` mark another repository bare.
- From the second review of PR07, `railform adopt` now:
  - confirms the pre-commit region holds exactly the railform and hygiene items of `repos:`, and
    the Dependabot entry is an element of the github-actions `ignore:` list;
  - reports a job outside the region whose id or `name:` casefolds to `railform` or `gate`;
  - judges wrappers by their index entry, so a wrapper committed with CRLF is `drift`;
  - compares hooks directories case-insensitively, and never overwrites an untracked file in the
    previous hooks directory;
  - refuses to rewrite an `on:` block that holds comments;
  - reads `.git.` and `.git ` as `.git`.
- The test harness takes its variable list from `git rev-parse --local-env-vars` (so
  `GIT_CONFIG`, `GIT_CONFIG_PARAMETERS` and `GIT_CONFIG_COUNT` are included), and also removes
  `GIT_CONFIG_KEY_*` and `GIT_CONFIG_VALUE_*`. The test that shows the bare-repository hazard
  skips where the git in use does not reproduce it.
- From the third review of PR07, `railform adopt` now:
  - refuses a repeated key at the top level of an owned file, in `jobs:` and in a Dependabot
    update (`drift` under `--check`), since YAML keeps the last one;
  - places and confirms the Dependabot entry in the github-actions update whose `directory` is
    `/` (or whose `directories` holds `/`);
  - compares directories as Windows reads them, and lets the filesystem decide when both exist;
  - takes a `core.hooksPath` under `hooks.dir` as the previous hooks directory;
  - reports every candidate it passes over.
- From the fourth review of PR07, `railform adopt` refuses a top-level `files:` or `exclude:` in
  `.pre-commit-config.yaml` that drops `.git/COMMIT_EDITMSG` or a hook wrapper (`drift` under
  `--check`), since pre-commit applies it to every hook, the railform ones included.
- The pre-commit hook manifest, `.pre-commit-hooks.yaml`: `railform-commit-msg` (`commit-msg`),
  `railform-branch-name` (`pre-push`, `always_run: true`, `pass_filenames: false`),
  `railform-hooks-check` (`pre-commit`, `always_run: true`, `pass_filenames: false`, so a
  commit that only deletes a wrapper is still checked) and `railform-private-refs`
  (`pre-commit`, on the staged files), each `language: python` with `entry: railform <verb>`.
  The `repo: local` entry `railform adopt` writes in a self-hosted repository runs
  `railform-hooks-check` with `always_run: true` too. `pre-commit` joins the dev dependency
  group.
- The `branch-name` verb (the branch rail): `railform branch-name [-- NAME]` judges the given
  name, else `$PRE_COMMIT_REMOTE_BRANCH` without `refs/heads/` (the name on the remote), else the
  current branch, and fails with `branch` unless the name matches a `branch.allow` glob or the
  whole of `branch.pattern`. An empty name fails; a detached HEAD and a pushed ref that is not a
  branch pass and say so.
- Tests that run the manifest's hooks through real `git commit` and `git push` in armed clones
  of the three migration fixtures, with the hooks installed by pre-commit from the code under
  test. They need the network in every session and take about five and a half minutes on
  Windows; offline, run `pytest -m "not slow"`.
- The reusable CI workflow, `.github/workflows/python-ci.yml` (development-rails spec, §9): it
  runs on `workflow_call` with the required input `railform-sha`, installs every railform
  command from that commit with `uvx --from git+https://github.com/grimaldost/railform@<sha>`
  (the commit passed through `env:` and checked to be 40-hex first), and runs the jobs `frame`
  (`frame --check`, then `frame --github-output`), `checks` (`private-refs`, then the type's
  pipeline on every leg of `ci.os` by `python.versions`), `commit-lane` (`commit-range`,
  `pr-text` and `branch-name` on the head branch, through `HEAD_REF` in `env:`, on pull requests
  only), `changelog` (a placeholder, skipped until the changelog gate), `hooks`
  (`hooks-check`, `adopt --check`, and the hygiene hooks over a pull request's changed files)
  and `audit` (zizmor 1.30.0). Every action is pinned by SHA, every setup-uv step pins uv
  0.12.19, the token is read-only, and the jobs that read history check out all of it.
- `railform frame --github-output` also writes `pipeline`: the type's pipeline, one bash command
  per line, built from the frame's typed parameters (`railform.frame.pipeline`), which the
  `checks` job runs in order. A step that cannot be written (ruff at no version, or extra
  installs in a `plugin-marketplace` repository) exits 2.
- railform adopts itself: `railform.toml` (`python-package`, with `ci.self_hosted = true` and
  its exceptions row), a `.pre-commit-config.yaml` whose railform entry is `repo: local`, the
  hook wrappers in `.githooks/`, and the Dependabot ignore entry, all written by
  `railform adopt`.
- Tests: the stack-radar engine's workflow test, ported to `python-ci.yml` (SHA pins, one uv
  pin, no write permission) with the frame-driven pipeline, the required input and the
  `branch-name` step added; a local model of a GitHub Actions run (`tests/workflow_model.py`)
  that runs the caller and the reusable workflow through their own text with railform as the
  code under test; and the red proofs through a fixture caller: a failing test step, a
  `python.test_command` of `true` with no exceptions row and a pull request from `foo` each turn
  `gate` red, and a push run with `commit-lane` and `changelog` skipped turns it green.

### Changed

- Every `uvx` step of `python-ci.yml` and `tag-watch.yml` (the railform verbs, zizmor and the
  pre-commit replay) resolves its dependencies as of one date: the workflow-level
  `RAILFORM_EXCLUDE_NEWER`, first value `2026-10-11T00:00:00Z`, is mapped into each of those steps
  as uv's `UV_EXCLUDE_NEWER`, so a dependency published later cannot reach a run of a given
  railform commit. The environments pre-commit builds for the hygiene hooks are installed by pip,
  which does not read the date, so they stay pinned only by the hook repository's frozen rev. The
  `checks` job's pipeline step never sees the date, and no `run:` text changes. Each release moves
  the date in a commit of its pull request that is not the `release:` commit, and a change between
  releases that pins a tool or raises a dependency floor to a newer release moves it too.
- The `changelog` job of the reusable workflow runs `railform changelog-gate` on pull requests
  instead of being skipped on every event.
- The `python-package` preset's `changelog.records` watches `src/` only, so a change that moves
  only the lock or a workflow, such as a Dependabot pull request, is not charged.
- `railform adopt` writes the tag-watch caller from its template, also in a self-hosted
  repository. When it exists, adopt writes both of its pin sites (the whole `uses:` value and the
  `railform-sha` value) with the current mode's values and keeps the rest, so a switch between
  pinned and self-hosted needs only another adopt. `railform adopt --check` compares the whole file with the template rendered at
  its own `uses:` commit: any other difference is `drift`, a pin site that differs
  `pin-mismatch`.
- The guard lists `session_loaded` and `private_refs.placeholders` are joined to the default
  (default first, each element once) instead of replacing it: a repository lists only what it
  adds, never drops a default element, and its list no longer goes stale when a pin adds one.
  They never depart, so an `exceptions` row for them matches nothing, and a `railform.toml` that
  leaves out a default glob no longer narrows the D1 guard. An existing `exceptions` row for
  either list now fails `railform frame --check` with `bad-exception`; delete the row, and the
  default element it dropped is back in the list.
- `railform adopt` writes `stages: [pre-commit]` on every hook of the hygiene entry, so
  `check-merge-conflict --assume-in-merge` no longer judges the commit message and the hygiene
  set no longer runs again at `pre-push`; `adopt --check` holds the stages.
- The `plugin-marketplace` wrapper launches an interpreter,
  `uvx --from pre-commit==<version> python -m pre_commit hook-impl`, instead of pre-commit's
  console-script shim, which an application-control policy may refuse.
- `railform commit-msg` accepts an autosquash subject (`fixup! `, `squash! ` or `amend! ` before
  a valid subject), so `git commit --fixup` works locally; `commit-range` still refuses it.
- The default `commit.attribution_patterns` stamp arm names GPT and Anthropic as well as Claude,
  anywhere later on the line (`Generated with the help of Claude`).
- `private-refs` reads a user segment made only of full stops and ellipsis characters
  (`/home/.../`, `C:\Users\…\`) as a placeholder.
- Every job of the reusable workflow has a time limit, and only `checks` keeps setup-uv's cache.
- `railform adopt`'s arm hint follows `hooks-check --armed`, so a `core.hooksPath` that differs
  from `hooks.dir` only by case or trailing dots now gets the hint the check asks for.
- README: the pipeline lines run in one bash shell; `gate` protects nothing without a ruleset
  that requires it; an edit of a pull request's title or body re-runs the whole workflow.
- railform's own `ci.yml` is a caller of the reusable workflow by local path: the `railform` and
  `gate` jobs that `railform adopt` writes, with the commit under test as `railform-sha`. Its
  former `checks`, `audit` and `commit-lane` jobs are the reusable workflow's.
- An `edited` pull request event runs every job again, not only the commit lane: a run that
  skipped the checks would post a green `gate` over a red one on the same commit.
- The scan for installs by name accepts `@$RAILFORM_SHA` in the reusable workflow (and in the
  test model that stands in for its install line), and nowhere else.
- The pipeline tests of the migration fixtures run `railform.frame.pipeline` in bash, the steps
  the workflow runs, instead of their own copy of the steps.

- The default `branch.pattern` is measured: `^[a-z][a-z0-9-]*/[a-z0-9][a-z0-9._/-]*$` (a
  lowercase category, a slash and a lowercase slug) accepted all 454 head branches counted, where
  the provisional pattern, the commit types as categories, accepted 93%. The default
  `branch.allow` stays `dependabot/**`; a repository that takes changes from GitHub's web editor
  (`<user>-patch-<N>`) declares its own glob with an `exceptions` row.
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

- `railform adopt` no longer stops with exit 2 on a second run where `core.hooksPath` differs
  from `hooks.dir` only by case and the filesystem holds both directories: such a path is
  reported and never takes a stub.
- `railform adopt` refuses, before any write, an index entry that Windows reads as a wrapper's
  file but spelled differently (a tracked `.GitHooks/pre-commit`), and names the `git mv`;
  before, it staged a second entry no Windows checkout can hold.
- A `core.hooksPath` that git expands (`~`, `%(prefix)`) is outside the repository: adopt no
  longer writes stubs under a literal `~` directory.
- A `hooks.dir` that starts with `~` or `%(` is a configuration error (exit 2), as a forbidden
  character is; before, `railform arm` could leave a clone that `hooks-check --armed` reports as
  armed while git runs hooks from the expanded path.
- A `hooks.dir` or `core.hooksPath` holding a character Windows forbids in a name (`<>:"|?*`) is
  judged in every segment, before any write; before, a `:` past the first segment made the stub
  write fail on Windows after the other writes.
- The author name in `pyproject.toml` now matches `LICENSE`.
