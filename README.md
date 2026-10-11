# railform

railform is a set of repository mechanics that several Python repositories share by reference
instead of by copy: a reusable GitHub Actions workflow, a pre-commit hook manifest, a small
command-line tool that runs the checks, and a default configuration (the "frame") for those
checks. A repository that adopts railform pins one commit of this repository and gets the same
CI pipeline, hook wrappers and commit checks as every other adopter at that commit.

Nothing in railform is usable yet. The verbs, the hook manifest and the reusable CI workflow
exist, and railform's own CI runs through that workflow; nothing is usable before 0.1.0.

## What it is

- A shared CI workflow, one fixed pipeline per project type, with typed parameters.
- Commit hooks and checks written as a command, `railform <verb>`, that other repositories call
  from pre-commit and from CI: commit-message rules, a private-reference check, a hook-wrapper
  check, a branch-name rail and a few more.
- A default frame: the shared defaults, as data, overridden per repository by a `railform.toml`.
- Plain Python: the standard library plus PyYAML. Python 3.11 or newer.

## What it is not

- It has no model inside. The package never imports a model SDK and never reads a model API key;
  it may call `git` and GitHub's REST API, nothing else over the network. A test enforces this.
- It is not published to a package index and is not versioned for general use. There is no
  `pip install` of it by name.
- It does not change what a Claude Code session loads (skills, agents, commands, hooks,
  instruction files). Those are out of scope.

## Referencing it

Every reference names a commit, never a branch or a version range. A CI caller uses
`grimaldost/railform/.github/workflows/<file>@<40-hex sha>`, and a command-line install uses

```
uvx --from git+https://github.com/grimaldost/railform@<40-hex sha> railform <verb>
```

The version in `pyproject.toml` stays `0.1.0.dev0` until the first release.

## The command

`railform --version` and `--help` (or `-h`, at the top level or after a verb) print their text and exit 0 with no verdict line; they are not checks and never reach the ledger. Every other invocation ends its output with
exactly one line:

```
railform-verdict: <verb> <pass|fail> <code>
```

The exit status is 0 for a pass, 1 when the check found a violation, and 2 for a usage or
configuration error (a bad argument, an unknown verb, a verb not written yet, or an unhandled crash, which reads `internal-error`). Findings come
before the verdict line, one per line. When `$RAILFORM_LEDGER` names a file, the verdict line is
also appended to it.

## The frame

The frame is the configuration every check reads, as data. The effective frame of a repository
is built in three layers: the default frame shipped in the package
(`src/railform/default_frame.toml`), then the preset of the repository's project type
(`python-package`, `python-scripts` or `plugin-marketplace`), then the repository's own
`railform.toml`. Arrays replace (except the two guard lists below); tables merge key by key. Without a `railform.toml`, the frame is
the default with the `python-package` preset.

A value that differs from the preset is a departure, and a departure is legal only when an
`exceptions` row names its key and its value with a non-empty reason:

```toml
[python]
test_command = "true"

[[exceptions]]
key = "python.test_command"
value = "true"
reason = "the suite runs in a separate workflow during the migration"
```

The keys in `meta.parameter_keys` (the project type and layout, the Python versions and package,
the version sites, the changelog records and contract surfaces, and the release start and tag
prefixes) are per repository and never depart.

The two guard lists, `session_loaded` and `private_refs.placeholders`, are joined to the default
instead of replacing it: the effective list is the default's elements, then the repository's own
that the default lacks, each once. A repository lists only what it adds, its list never goes
stale when a later pin adds a default element, and it cannot drop a default element. These lists
never depart, so an `exceptions` row for them matches nothing. `railform frame` and
`railform frame --json` print the joined list. For example, `session_loaded = ["docs/prompts/**"]`
keeps every default glob and adds one.

Some keys hold rows rather than plain values:

- `changelog.records` is an array of `{ prefix, file }` rows and `release.tag_prefix` an array of
  `{ prefix, tag }` rows. A changed path or a tag belongs to the row with the longest matching
  `prefix` (`""` matches everything). A repository's own array replaces the default whole, so a
  repository whose only changelogs are per plugin lists those rows and has no root row.
- `version.sites` is an array of `{ path, pattern, authored }` rows: the pattern's first group is
  the version, and `authored` (default `false`) marks a site that is checked and never rewritten.
- `release.metadata_paths` holds globs of the release metadata (the manifests). The release check
  adds every `changelog.records` file and every `version.sites` path to them by itself, so the
  frame does not repeat them.

- `railform frame` prints the effective frame as TOML, and `--json` prints it as one JSON object.
- `railform frame --check` fails on an unknown key (`unknown-key`), a value of the wrong type
  (`wrong-type`), an exceptions row with an empty reason or matching no departure
  (`bad-exception`), and a departure no row lists (`unlisted-departure`), naming the first in
  that order.
- `railform frame --github-output` appends one `name=value` line per key to the file
  `$GITHUB_OUTPUT` names, as `<table>_<key>`; lists, tables, booleans and numbers are written as
  compact JSON. It also writes `pipeline`, the type's pipeline (see the reusable CI workflow
  below), one command per line, and exits 2 when a step cannot be written: ruff at no
  `ci.ruff_version`, or `ci.setup.extra_installs` in a `plugin-marketplace` repository, which has
  no uv project to install into.

## Hook wrappers

A repository tracks one small shell script per git hook in `hooks.dir` (`.githooks` by default),
each of which runs that stage of `.pre-commit-config.yaml` through the frame's `hooks.wrapper`
command. The text is fixed by a template, so a wrapper is correct or it is not. The command is
`uv run --frozen python -m pre_commit hook-impl` for the two uv types and
`uvx --from pre-commit==<hooks.pre_commit_version> python -m pre_commit hook-impl` for
`plugin-marketplace`. Both launch an interpreter, never pre-commit's console-script shim, which an
application-control policy may refuse to launch.

- `railform hooks-check` checks every hook in `hooks.names`: the file exists (`missing`), the git
  index tracks it as `100755` (`mode`; git skips a hook that is not executable on Linux and
  macOS), and its text, with CRLF read as LF, is the rendered wrapper (`content`). It judges
  the index and the file, never the working-tree executable bit.
- `railform hooks-check --armed` also fails unless `git config core.hooksPath` equals `hooks.dir`
  (`not-armed`).
- `railform arm` sets `core.hooksPath` to `hooks.dir` in the clone's local configuration. Run it
  once in each clone; worktrees share the clone's configuration.
- A frame whose `hooks.wrapper` pins a requirement to no version (the `{hooks.pre_commit_version}`
  placeholder with `hooks.pre_commit_version` empty) is a configuration error, exit 2.

## The commit lane

Three verbs judge commit text, with the rules read from the frame (`commit.types` and
`commit.attribution_patterns`):

- `railform commit-msg FILE` checks the message file git hands the `commit-msg` hook. Lines that
  start with git's comment character (`core.commentChar`, else `#`) are git comments and are
  ignored, as is everything after the scissors line.
- `railform commit-range BASE...HEAD` checks every commit that HEAD adds to BASE since their merge
  base.
- `railform pr-text [--event PATH]` checks a pull request's title and body, read from the event
  payload (`--event`, else `$GITHUB_EVENT_PATH`). A `null` or absent title or body is empty text.

The subject rule: the first line reads `type(scope)!: description`, the scope and the `!` optional
and the type one of `commit.types`. A commit with more than one parent (a merge) is exempt, since
git writes its subject; `commit-msg` treats a merge in progress the same way. `commit-msg` also
accepts an autosquash subject, `fixup! `, `squash! ` or `amend! ` before a valid subject, so
`git commit --fixup` and `git rebase --autosquash` work while a branch is in progress;
`commit-range` refuses such a subject, so it never reaches the base branch. The title of a pull
request is not held to the subject rule. Git's own `Revert "..."` subject, and GitHub's, fail the
subject rule; write the revert as `revert: ...`.

The attribution rule applies to every message, merges and the pull request's title and body
included: no line may match a pattern of `commit.attribution_patterns` (Python regular
expressions, matched case-insensitively per line). The default set is keel's. It rejects a trailer
that names Claude, GPT, Anthropic or Copilot as author, co-author, signer or assistant, and a
"generated with" stamp that names Claude, GPT or Anthropic (keel's stamp arm named Claude only;
the other two come from the mantis-research script and craft-collection's check). Dependabot bodies
quote upstream release notes, which could, rarely, trip the stamp pattern.

When both rules fail, the verdict code is `subject`; a failure of the attribution rule alone is
`attribution`. An unreadable message file, a bad range, a missing event payload and an invalid
pattern exit 2.

## The session-paths check

`railform session-paths --diff BASE...HEAD --frame-from REF [--glob PATTERN ...]` is the guard an
adoption series runs so that no change touches a file a Claude Code session loads (skills,
agents, commands, output styles, a plugin's hooks, plugin manifests, `.claude/`, `CLAUDE.md`, `AGENTS.md`,
`.mcp.json`). It lists the changed paths with `git diff --no-renames --name-only`, so a file
moved out of a loaded path still counts, and fails with `session-loaded` when one matches a glob
of `session_loaded` in the frame at `REF`. Pass the base as `REF`: the globs are then never the
ones the change writes. `--glob` adds a pattern (a series adds its own) and never removes one.

A glob matches the whole repository-relative path or any suffix of it that starts at a `/`:
`**` spans any number of segments, `*` none or more characters inside one segment, and names
compare case-insensitively. So `**/hooks/**` matches `plugins/x/hooks/hooks.json` and not
`.githooks/pre-commit`. A malformed glob (an empty segment, a `.` or `..` segment, a leading or
trailing `/`, or a backslash), from the frame or from `--glob`, is a configuration error (exit
2), because it would match nothing. The check also fails with `guard-narrowed` when the
`railform.toml` at HEAD removes or excepts a glob the frame at `REF` had. Since the list is joined
to the default, only a glob the repository added itself can be removed.

The check is blind to the inside of a submodule: git lists a submodule as its own path, so a
change to files in its contents is not seen unless the submodule path itself is changed.

## The private-reference check

`railform private-refs [PATH ...] [--diff BASE...HEAD] [--text FILE]... [--require-names]` keeps
two kinds of private reference out of a public repository. It checks the given paths; otherwise,
with `--diff`, the files the range changes (read at its tip) and the range's commit messages;
otherwise every tracked file. `--text FILE` (repeatable, one file per flag) adds a file to any
scope, such as a pull request's title or body. Binary files are skipped.

- **Home paths.** A home-directory path fails (`home-path`) unless its user segment is in
  `private_refs.placeholders` (default: `me`, `x`, `someone`, `alice`, `user`, `runner`, and the
  system folders `public`, `default`, `shared` and `runneradmin`) or plainly a placeholder (it
  contains `<`, `>`, `$`, `%`, `{`, `}` or `*`, or is made only of full stops and ellipsis
  characters, as in `/home/.../` or `C:\Users\…\`). The six shapes are `X:\Users\<name>\`,
  `X:/Users/<name>/`, the JSON-escaped `X:\\Users\\<name>`, the project-slug form
  `X--Users-<name>-`, `/Users/<name>/` and `/home/<name>/`, with any drive letter and without
  regard to case for the Windows ones. A segment ends at a separator, whitespace, a quote or the
  end of the line, so `cd C:\Users\<name>` is caught. `/home/` counts only where it is not
  preceded by a word character, `.` or `-`, so `src/home/views/` and the `/home/` of a URL pass.
- **Private names.** The file that the variable named by `private_refs.names_file_env` (default
  `RAILFORM_PRIVATE_NAMES`) points to lists one term per line; blank lines and lines starting
  with `#` are ignored. A term matches case-insensitively as a whole token, so a term `alice`
  matches `by alice.` and not `aliceworks`. Without the variable or the file this half is silent,
  which is why it is silent in CI; `--require-names` turns a missing file into a failure
  (`names-file-missing`). railform tracks no names list and ports none.

No output contains a matched name or the user segment of a matched home path: a home-path finding
shows the shape with `<name>` in its place, and a private-name finding gives only the term's line
number in the names file.

## Adopting

`railform adopt` writes the parts of a repository that railform owns, and `railform adopt --check`
fails when any of them differs from what adopt would write. Adopt owns only these:

- In `.github/workflows/ci.yml`, two jobs. `railform` calls
  `grimaldost/railform/.github/workflows/python-ci.yml` at the pin and passes the same pin as the
  input `railform-sha`. `gate` needs every other job in the file, runs with `if: always()`, and
  fails when a needed job failed or was cancelled; it is the one status a branch ruleset requires.
  `gate` protects nothing on its own: only a ruleset (or branch protection) that names `gate` as a
  required status stops a red pull request from merging. A repository without one, such as a
  private repository on a plan that offers no rulesets, still runs every check, but a red `gate`
  is advice there, not a block.
  The `pull_request` trigger also gains `types: [opened, synchronize, reopened, edited]`.
- In `.pre-commit-config.yaml`, the railform entry at the pin and the hygiene entry
  (`pre-commit/pre-commit-hooks` v5.0.0, pinned by its commit). Each hygiene hook carries
  `stages: [pre-commit]`: without it a hook runs at every stage a wrapper is installed for, so
  `check-merge-conflict --assume-in-merge` would refuse a commit message that quotes a conflict
  marker, and the whole set would run again at `pre-push`.
- In `.github/dependabot.yml`, an ignore entry for `grimaldost/railform` in the `github-actions`
  update. Adopt adds that update when there is none.
- Whole files: the hook wrappers in `hooks.dir`, staged as their exact bytes and tracked as
  `100755` (so `core.autocrlf` and `core.safecrlf` never touch them), and
  `.github/workflows/railform-tag-watch.yml` once the package carries its template. Adopt never
  writes that file over an existing one and `--pin` moves its `uses:` commit; `--check` compares
  the whole file with the template. Until a template ships there is nothing to judge.
- `railform.toml`, on the first run only, with the project type and `python.versions`. A `src/`
  layout is `python-package`, another `pyproject.toml` is `python-scripts`, and no
  `pyproject.toml` is `plugin-marketplace`; `--type` overrides the guess. The versions are the floor
  of `requires-python` and `python.newest`. After the first run the file is the repository's.

Each owned part of the three shared files sits between two marker lines:

```yaml
# >>> railform: owned by `railform adopt`; `railform adopt --check` fails on any edit
# <<< railform
```

Everything outside them is kept byte for byte, line endings included, and a second run changes
nothing.

- `railform adopt --pin <sha>` moves every reference at once: the `uses:` commit, the
  `railform-sha` input, the pre-commit `rev` and the tag-watch caller. Without `--pin`, adopt keeps
  the pin the workflow already names, so the first run needs `--pin`.
- `railform adopt --check` fails with `drift` when an owned part differs or is missing (a pin site
  included). It is also `drift` when a region does not hold its parts structurally:
  - the jobs `railform` and `gate` themselves under `jobs:`, with no other job whose id or `name:`
    reads `railform` or `gate`;
  - exactly the two entries of `repos:`;
  - the railform element of the `ignore:` list of the github-actions update whose `directory` is
    `/` (or whose `directories` list holds `/`).

  A mapping adopt reads that repeats a key (the top level of an owned file, `jobs:`, an update)
  is `drift`, and adopt refuses to write it, because YAML keeps the last one: a second `repos:`
  would silence every railform hook.

  Wrappers are judged by their index entry, mode `100755` and the exact LF bytes, so a wrapper
  committed with CRLF is `drift`. The check fails with `pin-mismatch` when the pins differ from
  each other, and with `extra-pr-workflow` while another workflow file runs on `pull_request` or
  `pull_request_target`, or cannot be parsed. A job's `needs:` cannot reach another workflow
  file, so move such a job into `ci.yml` (the next adopt adds it to `gate`), or list the file in
  `ci.extra_workflows`, by name or by path, with an `exceptions` row.
- Where the hooks lived before, adopt leaves a stub for each hook. That is the local
  `core.hooksPath` when it is not the same directory as `hooks.dir` (a subdirectory of it
  counts), or else the tracked directory, outside `hooks.dir` and outside every `session_loaded`
  glob, with the most `100755` files named after a hook. The stub prints the `railform arm`
  command and fails, so a clone armed to the old directory cannot commit unchecked.
  - Directories are compared as Windows reads them: each segment without its trailing dots and
    spaces, then casefolded. The filesystem decides when both directories exist.
  - Every candidate passed over is reported in a finding line.
  - A file there that is tracked but not `100755`, or not tracked at all, is reported and left
    alone.
- Adopt refuses, before writing anything, a target path with a symlink or junction on the way, a
  `hooks.dir` inside `.git` (`.git.` and `.git ` included), and an `on:` block it would have to
  rewrite as a mapping when that block holds comments.
- railform itself calls its own workflow by local path. With `ci.self_hosted = true` (and an
  `exceptions` row), adopt writes the `railform` job as a call to
  `./.github/workflows/python-ci.yml` with the commit under test as the input, the pre-commit entry
  becomes `repo: local` running `uv run --frozen railform <verb>`, `--pin` rewrites nothing, and
  `--check` skips the pin comparisons and says so. Without the key, a local-path call is `drift`.

## The reusable CI workflow

`.github/workflows/python-ci.yml` runs only when another workflow calls it (`workflow_call`). A
repository calls it from the `railform` job that `railform adopt` writes in its `ci.yml`, at a
pinned commit, and passes the same commit as the required input `railform-sha`:

```yaml
railform:
  uses: grimaldost/railform/.github/workflows/python-ci.yml@<40-hex sha>
  with:
    railform-sha: <the same 40-hex sha>
```

Every railform command in the workflow is installed from that commit
(`uvx --from git+https://github.com/grimaldost/railform@<40-hex sha>`), and the `frame` job
checks that the input is 40 hexadecimal characters before anything is installed. The jobs:

| Job | Runs | On |
|---|---|---|
| `frame` | `railform frame --check`, then `railform frame --github-output` | every run |
| `checks` | `railform private-refs`, then the type's pipeline, on every leg of `ci.os` by `python.versions` | every run |
| `commit-lane` | `railform commit-range`, `railform pr-text`, and `railform branch-name` on the head branch | pull requests |
| `changelog` | nothing yet: a placeholder, skipped until the changelog gate exists | none |
| `hooks` | `railform hooks-check`, `railform adopt --check`, and the hygiene hooks over the changed files, with pre-commit on the newest of `python.versions` (the `hooks_python` output of `frame --github-output`), so a `default_language_version` naming that Python resolves | every run; the hygiene hooks on pull requests |
| `audit` | zizmor 1.30.0 at high severity over the caller's workflows | every run |

- **The pipeline comes from the frame.** The workflow names no step of the type's pipeline.
  `frame --github-output` writes it as the output `pipeline`, built from the frame's typed
  parameters, and `checks` runs each line in bash, in order, stopping at the first that fails.
  For `python-package` it is `uv lock --check`, `uv sync --frozen` (plus `python.sync_args`),
  `uv pip install` of `ci.setup.extra_installs` when there are any, `ruff format --check` and
  `ruff check` over `python.lint_paths`, `ty check` over `python.type_check`, and
  `python.test_command`; `python-scripts` is the same without `ty` (its `python.type_check` is
  empty); `plugin-marketplace` runs ruff from `uvx` at `ci.ruff_version`, then
  `python.test_command`. A test command of several lines is one command per line. The lines run
  in order in one bash shell, so a line can prepare the next: an
  `export PATH="$HOME/.local/bin:$PATH"` on one line holds for every line after it, and so do
  `cd` and shell variables. The Python
  version reaches uv through `UV_PYTHON`, set per matrix leg, and the checkout depth of `checks`
  is `ci.setup.fetch_depth`.
- **What the workflow allows.** Every action is pinned by a 40-hex commit, every setup-uv step
  pins the same uv version, and the token is read-only (`contents: read`). Every job has a time
  limit (`timeout-minutes`, 45 for `checks` and 10 to 20 for the others), and only `checks`, which
  syncs the caller's lock, keeps setup-uv's cache. It runs from the
  caller's `pull_request` and `push` events, never `pull_request_target`. The jobs that read
  history (`commit-lane`, `hooks`, and `changelog` once it runs) check out all of it. No `run:`
  text holds an expression: values such as the head branch's name reach the scripts through
  `env:`.
- **What passes.** A job counts as passing when it succeeded or was skipped by its own condition
  (`commit-lane` and `changelog` on a push). The caller's `gate` job is the one required status.
  Every job runs on every pull request event, `edited` included: a run that skipped the checks
  on an edit of the title would post a green `gate` over a red one on the same commit. The cost:
  each edit of a pull request's title or body runs the whole workflow again, the full `checks`
  matrix included, though only `commit-lane` reads the text that changed.
- **The jobs after `frame`.** `checks`, `commit-lane`, `changelog` and `hooks` need `frame`, so a
  pin that is not a full commit, or a `railform.toml` that `frame --check` fails, stops them
  before they install anything. `audit` installs no railform and runs on its own.

railform itself calls the workflow by local path, `./.github/workflows/python-ci.yml`, with
`ci.self_hosted = true` in its `railform.toml`, and passes the commit under test
(`github.event.pull_request.head.sha || github.sha`) as `railform-sha`; its pre-commit entry is
`repo: local`, running `uv run --frozen railform <verb>`.

## The hook manifest

`.pre-commit-hooks.yaml` declares four hooks, each with `language: python` and
`entry: railform <verb>`, so pre-commit installs the command from the same commit as the
manifest:

| Hook | Stage | Runs on |
|---|---|---|
| `railform-commit-msg` | `commit-msg` | the message file |
| `railform-branch-name` | `pre-push` | every push pre-commit runs hooks for (`always_run: true`, `pass_filenames: false`) |
| `railform-hooks-check` | `pre-commit` | every commit (`always_run: true`, `pass_filenames: false`), since a commit that only deletes a wrapper stages no file a filter could match |
| `railform-private-refs` | `pre-commit` | the staged files only |

The hygiene hooks (trailing whitespace, end of file, merge-conflict markers and the rest) are
`pre-commit/pre-commit-hooks`, which `railform adopt` references beside these, pinned by commit.

## The branch rail

`railform branch-name [-- NAME]` judges a branch name. The name is the given `NAME`; else
`$PRE_COMMIT_REMOTE_BRANCH` without `refs/heads/`, which pre-commit sets on `pre-push`, so the
name judged is the one the branch gets on the remote (`git push origin feat/x:foo` is judged as
`foo`); else the current branch. Put `--` before a name that starts with a dash.

- A name passes when it matches a glob of `branch.allow` (`**` spans `/`, `*` and `?` do not),
  or when the whole name matches the regular expression `branch.pattern`. Otherwise it fails
  with `branch`, and so does an empty name.
- The default pattern is `^[a-z][a-z0-9-]*/[a-z0-9][a-z0-9._/-]*$`: a lowercase category, a slash
  and a lowercase slug, such as `feat/hook-rail` or `release/1.2.3`. It was measured over the
  head branches of 454 merged pull requests: with the commit types as the only categories, 93% of
  them passed; with this shape, all of them.
- The default refuses `main` and every other name without a slash: a direct
  `git push origin main` that carries new commits is refused. Work reaches the default branch
  through pull requests.
- The default `branch.allow` is `dependabot/**`, the branches Dependabot creates. GitHub's web
  editor names its branches `<user>-patch-<N>`, which the default refuses; a repository that
  takes changes from the web editor adds a glob such as `*-patch-*` with an `exceptions` row.
- A detached HEAD with no other source passes, and so does a pushed ref that is not a branch
  (a tag): there is no branch name to judge.

The `pre-push` hook cannot see every push. pre-commit runs no `pre-push` hook for a ref whose
commits are all reachable from the push remote's tracking refs already, and hands the hooks
only the first ref with new commits. So the rail is blind to:

- a new branch with no commit outside the push remote's tracking refs, such as `git branch foo`
  on `main` and `git push origin foo`;
- every ref after the first in a push of several refs.

The `commit-lane` job of the reusable CI workflow closes both gaps on the server: it runs
`railform branch-name` on the head branch of every pull request, so a misnamed branch is refused
once it opens a pull request, however it was pushed. A fork's pull request from its `main` is
refused there too.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the gate commands and the commit rules, and
[CHANGELOG.md](CHANGELOG.md) for what changed. The license is MIT.

The tests marked `slow` need the network; the hook-rail tests, which run real `git commit` and
`git push` through pre-commit, need it in every session. Offline, run
`uv run --frozen python -m pytest -q -m "not slow"`.

The workflow tests run the workflows' own scripts in bash, through a local model of a GitHub
Actions run (`tests/workflow_model.py`) in which railform is the code under test. On Windows
they use the bash that ships with Git, never the WSL launcher.
