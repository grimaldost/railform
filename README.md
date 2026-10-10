# railform

railform is a set of repository mechanics that several Python repositories share by reference
instead of by copy: a reusable GitHub Actions workflow, a pre-commit hook manifest, a small
command-line tool that runs the checks, and a default configuration (the "frame") for those
checks. A repository that adopts railform pins one commit of this repository and gets the same
CI pipeline, hook wrappers and commit checks as every other adopter at that commit.

Nothing in railform is usable yet. The package skeleton and the `frame` and `session-paths`
verbs exist; every other verb exits with `not-implemented` until the section that adds it lands.
Nothing is usable before 0.1.0.

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
`railform.toml`. Arrays replace; tables merge key by key. Without a `railform.toml`, the frame is
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
prefixes) are per repository and never depart. `session_loaded` and `private_refs.placeholders`
may grow without an exception; dropping a default element is a departure.

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
  compact JSON.

## Hook wrappers

A repository tracks one small shell script per git hook in `hooks.dir` (`.githooks` by default),
each of which runs that stage of `.pre-commit-config.yaml` through the frame's `hooks.wrapper`
command. The text is fixed by a template, so a wrapper is correct or it is not.

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
git writes its subject; `commit-msg` treats a merge in progress the same way. The title of a pull
request is not held to the subject rule. Git's own `Revert "..."` subject, and GitHub's, fail the
subject rule; write the revert as `revert: ...`.

The attribution rule applies to every message, merges and the pull request's title and body
included: no line may match a pattern of `commit.attribution_patterns` (Python regular
expressions, matched case-insensitively per line). The default set is keel's. It rejects a trailer
that names Claude, GPT, Anthropic or Copilot as author, co-author, signer or assistant, but its
"generated with" stamp pattern names Claude only, so another tool's stamp passes. The second arm of
the mantis-research script (a stamp that names GPT or Anthropic) was not added. Dependabot bodies
quote upstream release notes, which could, rarely, trip the stamp pattern.

When both rules fail, the verdict code is `subject`; a failure of the attribution rule alone is
`attribution`. An unreadable message file, a bad range, a missing event payload and an invalid
pattern exit 2.

## The session-paths check

`railform session-paths --diff BASE...HEAD --frame-from REF [--glob PATTERN ...]` is the guard an
adoption series runs so that no change touches a file a Claude Code session loads (skills,
agents, commands, a plugin's hooks, plugin manifests, `.claude/`, `CLAUDE.md`, `AGENTS.md`,
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
`railform.toml` at HEAD removes or excepts a glob the frame at `REF` had.

The check is blind to the inside of a submodule: git lists a submodule as its own path, so a
change to files in its contents is not seen unless the submodule path itself is changed.

## The private-reference check

`railform private-refs [PATH ...] [--diff BASE...HEAD] [--text FILE ...] [--require-names]` keeps
two kinds of private reference out of a public repository. It checks the given paths; otherwise,
with `--diff`, the files the range changes (read at its tip) and the range's commit messages;
otherwise every tracked file. `--text` adds files to any scope, such as a pull request's title and
body. Binary files are skipped. Put the paths before the options.

- **Home paths.** A home-directory path fails (`home-path`) unless its user segment is in
  `private_refs.placeholders` (default: `me`, `x`, `someone`, `alice`, `user`, `runner`) or
  plainly a placeholder (it contains `<`, `>`, `$`, `%`, `{`, `}` or `*`). The six shapes are
  `X:\Users\<name>\`, `X:/Users/<name>/`, the JSON-escaped `X:\Users\<name>`, the project-slug
  form `X--Users-<name>-`, `/Users/<name>/` and `/home/<name>/`, with any drive letter and
  without regard to case for the Windows ones.
- **Private names.** The file that the variable named by `private_refs.names_file_env` (default
  `RAILFORM_PRIVATE_NAMES`) points to lists one term per line; blank lines and lines starting
  with `#` are ignored. A term matches case-insensitively as a whole token, so a term `alice`
  matches `by alice.` and not `aliceworks`. Without the variable or the file this half is silent,
  which is why it is silent in CI; `--require-names` turns a missing file into a failure
  (`names-file-missing`). railform tracks no names list and ports none.

No output contains a matched name or the user segment of a matched home path: a home-path finding
shows the shape with `<name>` in its place, and a private-name finding gives only the term's line
number in the names file.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the gate commands and the commit rules, and
[CHANGELOG.md](CHANGELOG.md) for what changed. The license is MIT.
