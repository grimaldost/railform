# railform

railform is a set of repository mechanics that several Python repositories share by reference
instead of by copy: a reusable GitHub Actions workflow, a pre-commit hook manifest, a small
command-line tool that runs the checks, and a default configuration (the "frame") for those
checks. A repository that adopts railform pins one commit of this repository and gets the same
CI pipeline, hook wrappers and commit checks as every other adopter at that commit.

The verbs, the hook manifest and the reusable CI workflow exist, and railform's own CI runs
through that workflow. The current release is 0.2.0; an adopter pins the commit the tag
`v0.2.0` points at, the release pull request's merge commit.

## Adopting a repository in five steps

[Adopting a repository](docs/adopting.md) is the full path, with the inventory table, the
choices adopt leaves to the repository and a worked example. Its steps, in order:

1. **Take an inventory** of the workflows, the hooks, the changelog and version scripts and the
   required status checks, before anything changes ([step 1](docs/adopting.md#1-take-an-inventory)).
2. **Run adopt at a release commit,** on a branch. `git ls-remote
   https://github.com/grimaldost/railform 'refs/tags/v0.2.0^{}'` prints the commit, and
   `uvx --from git+https://github.com/grimaldost/railform@<40-hex sha> railform adopt --pin
   <40-hex sha>` writes the owned files. Then delete the jobs and hooks railform replaces
   ([step 2](docs/adopting.md#2-run-railform-adopt)).
3. **Settle the frame** in `railform.toml`: an `exceptions` row, a key and a reason, for each
   setting that loosens a check. Every later `railform <verb>` runs through the same
   `uvx --from` install; `railform frame --check` and `railform adopt --check` must pass
   ([steps 3 to 5](docs/adopting.md#3-write-an-exceptions-row-for-each-departure-that-loosens-a-check)).
4. **Require `gate`:** push the branch, open the pull request, and make `gate` the one required
   status of the default branch's ruleset ([step 6](docs/adopting.md#6-require-gate-in-the-branch-ruleset)).
5. **Arm every clone** with `railform arm`, once per clone ([step 7](docs/adopting.md#7-arm-every-clone)).

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

The version in `pyproject.toml` names the release; a pin is still a commit, never the version.

## The command

`railform --version` and `--help` (or `-h`, at the top level or after a verb) print their text and exit 0 with no verdict line; they are not checks and never reach the ledger. Every other invocation ends its output with
exactly one line:

```
railform-verdict: <verb> <pass|fail> <code>
```

The exit status is 0 for a pass, 1 when the check found a violation, and 2 for a usage or
configuration error (a bad argument, an unknown verb, a verb not written yet, or an unhandled crash, which reads `internal-error`), and for `release-check` also when the GitHub API cannot be read (`api-error`). Findings come
before the verdict line, one per line. When `$RAILFORM_LEDGER` names a file, the verdict line is
also appended to it.

## The frame

The frame is the configuration every check reads, as data. The effective frame of a repository
is built in three layers: the default frame shipped in the package
(`src/railform/default_frame.toml`), then the preset of the repository's project type
(`python-package`, `python-scripts` or `plugin-marketplace`), then the repository's own
`railform.toml`. Arrays replace (except the two guard lists below); tables merge key by key.
Without a `railform.toml`, the frame is the default with the `python-package` preset.

A value that differs from the preset is a departure. A departure that loosens a check is legal
only when an `exceptions` row names its key with a non-empty reason; a stricter or neutral
departure needs no row, though a row may still name it to record why:

```toml
[python]
test_command = "uv run --frozen python -m pytest -q tests/unit"

[[exceptions]]
key = "python.test_command"
reason = "the integration suite runs in a separate workflow during the migration"
```

A row matches its departure by key. It may also carry `value`, which must then equal the
departed value; that is the form every row had before, and it still passes. A row whose key does
not depart, or whose `value` is not the departed one, is a bad row (`bad-exception`).

These departures loosen a check (`LOOSENING` in `src/railform/frame.py`):

| Key | Loosens when |
|---|---|
| `python.test_command`, `python.type_check`, `branch.pattern`, `hooks.wrapper`, `changelog.trailer`, `release.pr_branch_prefix`, `release.pr_title_pattern`, `private_refs.names_file_env`, `ci.ruff_version`, `hooks.pre_commit_version` | the preset sets it and the repository changes it: railform cannot judge a command, a pattern, a variable name or a tool version, a blank or `true` command runs nothing, a trailer of `<reason>` alone declares every commit and so turns `changelog-gate` off, a broad release convention lets any pull request carrying the version count as the release, a variable CI does not set turns off the private-name half of `railform private-refs`, and an older ruff or pre-commit runs fewer rules (only the `plugin-marketplace` preset sets the two versions; a type that leaves one empty never reads it) |
| `changelog.enabled`, `release.enabled` | it is `false` |
| `ci.self_hosted` | it is `true` |
| `ci.extra_workflows`, `changelog.unwatched`, `branch.allow`, `release.metadata_paths`, `commit.types`, `python.sync_args`, `ci.setup.extra_installs` | the list has an element the preset lacks: an argument after `uv sync --frozen` or a requirement installed after it changes the environment the checks run in |
| `ci.os`, `commit.attribution_patterns`, `hooks.names`, `python.lint_paths` | the list leaves out an element the preset has |

Any other departure, and these in the other direction, needs no row: a `ci.setup.fetch_depth`, a
longer `ci.os`, an empty `changelog.unwatched`, `changelog.consumer_affecting = "error"`, a
shorter `commit.types`, another `hooks.dir` or `python.newest`. A key of the first row needs a row
for any change, a stricter one included.

Every key of the frame is classified: in `LOOSENING`, in `NEUTRAL` (the keys above that loosen
nothing, each with its reason), in `meta.parameter_keys` or one of the two guard lists, and in
only one of them. A test fails on a key in none, so a key added to the frame is decided before it
ships and `frame --check` never passes a departure nobody judged.

The keys in `meta.parameter_keys` (the project type and layout, the Python versions and package,
the version sites, the changelog records and contract surfaces, and the release start and tag
prefixes) are per repository and never depart.

The two guard lists, `session_loaded` and `private_refs.placeholders`, are joined to the default
instead of replacing it: the effective list is the default's elements, then the repository's own
that the default lacks, each once. A repository lists only what it adds, its list never goes
stale when a later pin adds a default element, and a list that leaves out a default element keeps
it. For example, `session_loaded = ["docs/prompts/**"]` keeps every default glob and adds one.
`railform frame` and `railform frame --json` print the effective list.

Adding to `session_loaded` guards more paths and needs no row. Adding to
`private_refs.placeholders` loosens the scan, since a home path under an added name passes, so it
needs an `exceptions` row for the key with a reason and no `drop`:

```toml
[private_refs]
placeholders = ["example"]

[[exceptions]]
key = "private_refs.placeholders"
reason = "the tests use example as the neutral user name in a home path"
```

The row may also give `value`, the elements it adds, which must then be exactly the ones the
repository adds, in the order of its own list (`bad-exception` otherwise). Without the row
`frame --check` fails with `unlisted-departure`. A row with a `drop` and no `value` does not cover
an addition: a repository that drops one element and adds another gives `drop` and `value` in the
one row. A default element already in the list is not an addition.

A default element leaves a guard list only through an `exceptions` row that names it in `drop`,
with a reason:

```toml
[[exceptions]]
key = "private_refs.placeholders"
drop = ["shared"]
reason = "no machine here has a shared profile, so a path under it is a real account"
```

`drop` names default elements of that list and is not empty, and the row gives no `value` unless
it is the one for `private_refs.placeholders` and names what the repository adds; a row that breaks
this is `bad-exception` and drops nothing, not even its default elements. One row per
list: a second row for the same list is `bad-exception` too and drops nothing. Dropping a
placeholder makes the private-reference check stricter. Dropping a `session_loaded` glob narrows
the guard, so `railform session-paths` fails the pull request that adds the row with
`guard-narrowed` (see the session-paths check below).

Some keys hold rows rather than plain values:

- `changelog.records` is an array of `{ prefix, file }` rows and `release.tag_prefix` an array of
  `{ prefix, tag, since }` rows. A changed path or a tag belongs to the row with the longest
  matching `prefix` (`""` matches everything). A repository's own array replaces the default
  whole, so a repository whose only changelogs are per plugin lists those rows and has no root
  row. A tag row's `since` is optional and defaults to `""`; `railform frame --json` shows it on
  every row (see the release check below). The `python-package` preset's records row
  watches `src/` only; the other types watch every path, `uv.lock` and `.github/`
  excepted (`changelog.unwatched`, below).
- `changelog.unwatched` is a list of path prefixes (plain, case-sensitive prefixes of a
  repository-relative path, as in `changelog.records`) that no record is charged for,
  whichever row would own them. It defaults to `["uv.lock", ".github/"]` in every type: a
  Dependabot pull request that moves only the lock or a workflow cannot add a trailer, and a
  change to either ships nothing a changelog reader looks for. The more specific line wins: a
  records row whose prefix is at least as long as the entry (a row with the prefix `.github/`,
  say) watches the path again, and an entry longer than the row's prefix takes it out. Unlike
  the records, the list is not a parameter key: a list with an entry the preset lacks leaves more
  unread and needs an `exceptions` row, and a shorter one needs none. A lock in a subdirectory
  (`plugins/a/uv.lock`) is not matched by `uv.lock`; list it.
- `version.sites` is an array of `{ path, pattern, authored }` rows: the pattern's first group is
  the version, and `authored` (default `false`) marks a site that is checked and never rewritten.
- `release.metadata_paths` holds globs of the release metadata (the manifests). The release check
  adds every `changelog.records` file and every `version.sites` path to them by itself, so the
  frame does not repeat them.

- `railform frame` prints the effective frame as TOML, and `--json` prints it as one JSON object.
- `railform frame --check` fails on an unknown key (`unknown-key`), a value of the wrong type
  (`wrong-type`), an exceptions row with no key, an empty reason, matching no departure or with a
  bad `drop` (`bad-exception`), and a departure that loosens a check and that no row lists
  (`unlisted-departure`), naming the first in that order.
- `railform frame --github-output` appends one `name=value` line per key to the file
  `$GITHUB_OUTPUT` names, as `<table>_<key>`; lists, tables, booleans and numbers are written as
  compact JSON. It also writes `pipeline`, the type's pipeline (see the reusable CI workflow
  below), one command per line, and `hooks_python`, the newest of `python.versions` (else
  `python.newest`), which the `hooks` job runs pre-commit on. It exits 2 when a step cannot be
  written: ruff at no `ci.ruff_version`, or `ci.setup.extra_installs` in a `plugin-marketplace`
  repository, which has no uv project to install into.

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
"generated with" stamp that names one of them anywhere later on the line (keel's stamp
arm named Claude only, directly after the words; the wider reach comes from the mantis-research
script and craft-collection's check). Each name is a whole word: ChatGPT and GitHub Copilot
match, an address at `gptools.example` does not, and neither does the file `CLAUDE.md`, so
"Generated with the sync script from CLAUDE.md" passes while "Generated with Claude" does not.
Dependabot bodies quote upstream release notes, and a line
that opens "Generated with" and names one of the products later could, rarely, trip the stamp
pattern.

When both rules fail, the verdict code is `subject`; a failure of the attribution rule alone is
`attribution`. An unreadable message file, a bad range, a missing event payload and an invalid
pattern exit 2.

## The changelog gate

`railform changelog-gate BASE...HEAD` fails a change that the changelog does not record. It
judges every commit HEAD adds to BASE, merges included, and reads the record diff from their
merge base to HEAD. In CI the `changelog` job runs it on pull requests as
`changelog-gate "origin/${BASE_REF}...HEAD"`.

- **Record or declare** (`unrecorded`). Each path a commit changes is charged to the
  `changelog.records` row with the longest matching prefix, and so to that row's file; a path no
  row matches is not watched, and a record file is never charged to itself. The `python-package`
  preset watches `src/` (`CHANGELOG.md`), the other types every path, and a repository with
  per-plugin changelogs lists one row per plugin. A path under `changelog.unwatched` (by
  default `uv.lock` and `.github/`, in every type) is not watched either, so a Dependabot
  pull request that moves only the lock or a workflow passes unrecorded. For each commit and each record file it is
  charged to, the range must add a line to that file, or the commit must carry the trailer
  `changelog.trailer` spells, by default `Changelog: none (<reason>)` with a reason that is not
  blank (`changelog: None (a rename)` counts; `Changelog: none ()` does not). A trailer declares
  its own commit only. A line counts as added when it is not blank once its whitespace is
  collapsed and the same diff does not also remove it, so trailing spaces, re-indenting,
  reordering and a CRLF conversion record nothing.
- **Merges.** A merge is charged only with its resolution: the paths where its tree differs from
  the automatic merge of its parents, from `git merge-tree --write-tree` (git 2.38 or newer). A
  clean merge, such as the merge commit CI checks out for a pull request, is charged nothing; a
  hand resolution of a watched path needs a record or the trailer on the merge. A merge of
  unrelated histories, and an octopus merge, are charged their combined diff.
- **Release headings** (`release-heading`). The first heading `## [X.Y.Z]` that a record file's
  diff adds, among those whose version no heading the diff removes carries, is the cut: a new
  date on a released heading, a trailing space on it or a CRLF conversion of the file is no cut.
  The version must already read `X.Y.Z` at HEAD and must have been lower at the merge base. The
  version is read from the first `version.sites` row that is not `authored` on the record's
  release line (the `release.tag_prefix` row with the longest prefix of the record file), in
  ASCII digits. A side with no readable three-part version, such as `0.1.0.dev0`, is not
  compared; a line with no such site skips the rule and says so.
- **Contract surfaces** (`consumer-affecting`). When the range changes a path of
  `changelog.contract_surfaces` and no record's added lines say `(consumer-affecting)`, the rule
  fires once per surface. By default (`changelog.consumer_affecting = "advisory"`) a firing is a
  `::warning::<path>: consumer-affecting (advisory): ...` line and the verdict stays a pass;
  `"error"`, which is stricter and needs no `exceptions` row, makes it fail.

- **The watch covers something** (`watches-nothing`). When no tracked path at HEAD other than the
  record files falls under a `changelog.records` row of the frame at HEAD and outside
  `changelog.unwatched` (a flat-layout package on the `python-package` preset's `src/`, for
  example), the gate fails with
  `changelog.records: watches no tracked path ...`: such a watch would pass every later pull
  request unread. The watch judged is HEAD's, so the pull request that mends an empty watch
  passes.

When several rules fail, the verdict names the first in the order above and every finding is
printed. `changelog.enabled = false` (with its `exceptions` row) in the frame that judges passes
at once and judges no commit.

**Which frame judges.** The rules read the `railform.toml` committed at the merge base of BASE and
HEAD, not the one the pull request writes, so a pull request cannot switch off or narrow the gate
that judges it by editing `changelog.records`, `version.sites`, `release.tag_prefix`,
`changelog.enabled` or any other key. A merge base with no `railform.toml` (the adoption pull
request) leaves the working tree's frame in force. The verdict's findings name the frame read
(`the frame: railform.toml at the merge base <sha>`). The `railform.toml` of the merge base must
pass the schema of the railform that runs; when it does not, the gate exits 2 and names it. After a
re-pin to a railform that no longer reads a key of the old frame, every pull request would exit 2,
the one that rewrites the frame included, since its merge base still holds the old frame; so a
release that renames or removes a frame key keeps reading the old key in a merge base's frame.

A pull request that changes these keys is therefore judged by the frame it replaces, and the new
frame holds from the next pull request on. Most such changes pass in one pull request: one that adds
a records row or watches a new prefix is judged by the old rows and passes when it records or
declares its commits, and one that moves a records file and its row, with nothing else in it, passes
too (a version site in that file moves by the steps below). A change that the old frame would fail,
such as moving a version site to another file, lands in three pull requests, each judged by the
frame the one before it merged: the first adds the new site beside the old one, with the new file in
step; the second removes the old row and leaves the old file as it is, still in step; the third
removes the old file, or its version, which no frame reads any more. Removing the old row and the
old file together fails, because the frame that judges that pull request still lists the site and
finds the file `unreadable`. There is no `exceptions` row for this: the keys are parameter keys,
which never depart, and a row the pull request wrote would be the same self-judgement the merge
base's frame removes. The steps add pull requests and no mechanism.

These exit 2: an empty `changelog.records`, a records row with no file, a trailer template
without exactly one `<reason>`, a range not of the form `BASE...HEAD`, a revision git cannot
resolve, a range with no merge base, and a git older than 2.38 when a merge must be charged. The
gate reads release lines and version sites through the version-site check's own reading, so it
also exits 2 on that check's configuration errors: two `changelog.records` or
`release.tag_prefix` rows with the same prefix, and a version site whose path is empty, absolute,
holds `..` or a backslash, whose pattern has no group, or that no `release.tag_prefix` row
covers.

## The session-paths check

`railform session-paths --diff BASE...HEAD --frame-from REF [--glob PATTERN ...]` is the guard an
adoption series runs so that no change touches a file a Claude Code session loads (skills,
agents, commands, output styles, a plugin's hooks, plugin manifests, `.claude/`, `CLAUDE.md`,
`AGENTS.md`, `.mcp.json`). It lists the changed paths with
`git diff --no-renames --name-only`, so a file moved out of a loaded path still counts, and fails
with `session-loaded` when one matches a glob of `session_loaded` in the frame at `REF`. Pass the
base as `REF`: the globs are then never the ones the change writes.
`--glob` adds a pattern (a series adds its own) and never removes one.

A glob matches the whole repository-relative path or any suffix of it that starts at a `/`:
`**` spans any number of segments, `*` none or more characters inside one segment, and names
compare case-insensitively. So `**/hooks/**` matches `plugins/x/hooks/hooks.json` and not
`.githooks/pre-commit`. A malformed glob (an empty segment, a `.` or `..` segment, a leading or
trailing `/`, or a backslash), from the frame or from `--glob`, is a configuration error (exit
2), because it would match nothing. The check also fails with `guard-narrowed` when the
`railform.toml` at HEAD removes a glob the frame at `REF` had, drops one with an `exceptions` row
(the only way a default glob leaves the list), or gives `session_loaded` a row with a value that
leaves one out. Since the list is joined to the default, a list that leaves out a default glob
keeps it. So the pull request that drops a glob is seen failing this check, and once the drop is
in the frame at `REF` the glob no longer guards.

The check is blind to the inside of a submodule: git lists a submodule as its own path, so a
change to files in its contents is not seen unless the submodule path itself is changed.

## The private-reference check

`railform private-refs [PATH ...] [--diff BASE...HEAD] [--text FILE]... [--event PATH]
[--require-names]` keeps two kinds of private reference out of a public repository. It checks the
given paths; otherwise, with `--diff`, the files the range changes (read at its tip) and the
range's commit messages; otherwise every tracked file. `--text FILE` (repeatable, one file per
flag) adds a file to any scope. `--event PATH` adds the title and body of the pull request in a
GitHub event payload, read as `pr-text` reads them and reported as `pull request title` and
`pull request body`. Binary files are skipped.

In the reusable workflow, the `checks` job runs it over the tracked files, and the `commit-lane`
job runs `private-refs --diff "origin/${BASE_REF}...HEAD" --event "$GITHUB_EVENT_PATH"` on pull
requests, so a home path in a commit message or in the pull request's text fails too. Without a
names file in CI, the names half is silent there, as below.

- **Home paths.** A home-directory path fails (`home-path`) unless its user segment is in
  `private_refs.placeholders` (default: `me`, `x`, `someone`, `alice`, `user`, `runner`, and the
  system folders `public`, `default`, `shared` and `runneradmin`; a repository adds names to the
  list, and leaves a default one out with an `exceptions` row, see the frame) or plainly a
  placeholder (it
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

## The version-site check

`railform version-sites [--bump X.Y.Z [--prefix PREFIX]] [--base REF]` checks that every place a release
version is written agrees, and with `--bump` writes the version first. The places are the rows of
`version.sites`: `{ path, pattern, authored }`, where `path` is a repository-relative file with
`/` separators, `pattern` is a Python regular expression searched over the whole file text (with
no flags implied; write `(?m)` where the pattern needs it) whose first group is the version, and
`authored` marks a site whose value is written with its own content: the newest changelog
heading arrives with a section under it, so a writer that rewrote the number would rename the
previous release. An authored site is checked and never rewritten. The command reads the working
tree only, and a shallow checkout is enough. The file is read with no newline translation and the
pattern is the repository's, so a group must not capture line-end characters: use `([0-9.]+)` or
`([^"\s]+)`, not `(.*)$`, which on a CRLF checkout (a Windows leg's) captures the `\r`, fails
there only, and has a bump drop that line's `\r`.

- **Release lines.** Sites are grouped by the `release.tag_prefix` row their path falls under (the
  longest matching `prefix`), so two plugins at different versions both pass. Within a line the
  expected version is the one most sites read, and the earliest of them in `version.sites` order
  on a tie. Authored sites take part. Versions compare as exact strings, with the two
  exceptions below.
- **Between releases.** Two states are not failures. A line whose writable site reads a
  development version `X.Y.Z.devN` (or `X.Y.Z.dev`) is on its way to `X.Y.Z`: an authored
  site on that line (the newest changelog heading) that reads `X.Y.Z` or a lower release
  passes and takes no part in the count, since the heading of the release being prepared is
  written when it is cut. The writable sites still have to read one value, the dev version
  included, and an authored site past `X.Y.Z` still fails. And an authored site whose file
  has level-2 headings that are all `## [Unreleased]` has no release heading yet: it is
  skipped, with the finding `<path>: holds no release heading yet, only ## [Unreleased]`.
  A file with another heading the pattern misses (`## 0.1.0`), and a site that is not
  authored, still fails with `unreadable`.
- **Failures.** A site that reads another version fails with `mismatch`
  (`<path>: reads <value>, not <expected>`, with `(authored: write it by hand)` for an authored
  site). A site whose file is missing or is not UTF-8, or whose pattern matches nothing, fails with
  `unreadable` (`<path>: no version (...)`), which wins over `mismatch` when both occur. Every
  failing site is listed in one run. With no `version.sites` the check passes and says so.
- **`--bump X.Y.Z`** replaces the first group's span, and nothing else, in each non-authored,
  readable site of one line whose version differs, keeping its line endings, then runs the check
  over every line. So a bump that leaves an authored heading behind ends `mismatch` and names the
  heading, and a second identical bump writes nothing. With more than one line holding a
  non-authored site, `--prefix` is required and must be a `release.tag_prefix` row's `prefix`
  exactly (`--prefix plugins/humblepowers/`, or `--prefix ""` for the root row). Regenerate a
  lockfile that records the version (`uv lock`) by hand after a bump.
- **`--base REF`** reads `version.sites` and `release.tag_prefix` from the `railform.toml`
  committed at the merge base of REF and HEAD instead of the working tree's, so a pull request
  cannot drop or loosen the sites it is judged by; the files themselves are still read in the
  working tree. A merge base with no `railform.toml` leaves the working tree's frame in force.
  A finding line names the frame read. It does not combine with `--bump`.
- **Configuration errors** (exit 2): a path that is empty, absolute, drive-qualified, or that holds
  `..` or a backslash, a pattern that does not compile or has no group, a site that no
  `release.tag_prefix` row covers, two rows with one prefix in `release.tag_prefix` or
  `changelog.records`, a `--bump` value that is not three integers, `--prefix` without `--bump`,
  a `--prefix` that is no row's prefix, a missing `--prefix` when more than one line has a writable
  site, a site file that cannot be written (the message names it and the sites already
  written), `--base` with `--bump`, a `--base` that is no commit or shares no history with HEAD,
  and a `railform.toml` at the merge base that cannot be read or breaks the schema.

The reusable workflow runs it in the `checks` job on every leg, right after `private-refs`, with
the working tree's frame; on pull requests the `changelog` job, which has the history, runs it
again as `version-sites --base "origin/${BASE_REF}"`. A pull request that changes `version.sites`
lands as the changelog gate describes: one that the old sites would fail, such as a site moved to
another file, takes three pull requests.

## Adopting

For the steps in order, from the inventory to the branch ruleset, see
[Adopting a repository](docs/adopting.md). This section is the reference for what adopt owns.

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
  `.github/workflows/railform-tag-watch.yml`, the caller of the reusable tag watch (see the
  release check below). Adopt writes the caller when it is absent and `release.enabled` is
  true; with the release check off (`release.enabled = false`) it writes none, and `--check`
  does not ask for one. A caller that exists is kept either way: when it exists, adopt writes
  only its two pin sites, the whole `uses:` value and the
  `railform-sha` value, with the current mode's values (the pinned call and the pin, or the call
  by local path and `${{ github.sha }}` when self-hosted), and leaves the rest as it is, line
  endings included. A switch between pinned and self-hosted in either direction therefore needs
  only another `railform adopt`. `--check` renders the template at the file's own `uses:` commit
  (or by local path, when self-hosted) and compares the whole file: a missing file or any other difference is `drift`, which
  deleting the file and running `railform adopt` clears; a pin site that differs from the others
  is `pin-mismatch`. A workflow of the
  repository's own, such as a `tag-watch.yml` it already had, is never touched.
- `railform.toml`, on the first run only, with the project type and `python.versions`. A `src/`
  layout is `python-package`, another `pyproject.toml` is `python-scripts`, and no
  `pyproject.toml` is `plugin-marketplace`; `--type` overrides the guess. The versions are the floor
  of `requires-python` and `python.newest`. A repository that already has release tags (`v` and
  three integers, the default row of `release.tag_prefix`) also gets `release.since` set to the
  tag just past the newest of them: the newest is `v0.4.2`, so `since = "v0.4.3"`. The release
  check judges the floor tag itself, so the tags from before adoption are never judged and the
  first daily run of the tag watch stays green; a repository with other tag prefixes sets
  `since` itself. After the first run the file is the repository's.

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

  A `paths`, `paths-ignore`, `branches` or `branches-ignore` filter on the `pull_request` trigger
  is the repository's, but adopt and `--check` name it in a finding line: a pull request the
  filter leaves out runs no `ci.yml` and reports no `gate`, so a ruleset that requires `gate`
  waits on it.
- Where the hooks lived before, adopt leaves a stub for each hook. That is the local
  `core.hooksPath` when it is not the same directory as `hooks.dir` (a subdirectory of it
  counts), or else the tracked directory, outside `hooks.dir` and outside every `session_loaded`
  glob, with the most `100755` files named after a hook. The stub prints the `railform arm`
  command and fails, so a clone armed to the old directory cannot commit unchecked. The stub is
  meant to be removed one release later: the first railform patch release after an adopter's
  adoption leaves it.
  - Directories are compared as Windows reads them: each segment without its trailing dots and
    spaces, then casefolded. The filesystem decides when both directories exist.
  - A `core.hooksPath` that differs from `hooks.dir` only that way (`.GitHooks`, `.githooks.`)
    is never the previous directory, even on a filesystem that holds both: on Windows they are
    one directory, so a stub there would land on a wrapper. It is reported, and adopt tells the
    clone to arm, as `hooks-check --armed` does, which compares the spelling.
  - A `core.hooksPath` that git expands (`~`, `%(prefix)`), lies outside the repository or inside
    `.git`, or holds a character Windows forbids in a name (`<>:"|?*`) is not a directory adopt
    writes in; it is reported and gets no stub.
  - Every candidate passed over is reported in a finding line.
  - A file there that is tracked but not `100755`, or not tracked at all, is reported and left
    alone.
- Adopt refuses, before writing anything, a target path with a symlink or junction on the way, a
  `hooks.dir` inside `.git` (`.git.` and `.git ` included), starting with a `~` or `%(` that git
  expands, or holding a character Windows forbids,
  an index entry that Windows reads as a wrapper's file but spelled differently (such as a tracked
  `.GitHooks/pre-commit`; adopt names the `git mv` that fixes it), and an `on:` block it would
  have to rewrite as a mapping when that block holds comments.
- railform itself calls its own workflow by local path. With `ci.self_hosted = true` (and an
  `exceptions` row), adopt writes the `railform` job as a call to
  `./.github/workflows/python-ci.yml` with the commit under test as the input, the tag-watch
  caller as a call to `./.github/workflows/tag-watch.yml` with `${{ github.sha }}`, the pre-commit
  entry becomes `repo: local` running `uv run --frozen railform <verb>`, `--pin` rewrites nothing,
  and `--check` skips the pin comparisons and says so. Without the key, a local-path call is
  `drift`.

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
| `checks` | `railform private-refs`, `railform version-sites`, then the type's pipeline, on every leg of `ci.os` by `python.versions` | every run |
| `commit-lane` | `railform commit-range`, `railform pr-text`, `railform branch-name` on the head branch, and `railform private-refs` over the range (its commit messages included) and the pull request's title and body | pull requests |
| `changelog` | `railform changelog-gate "origin/${BASE_REF}...HEAD"` (see the changelog gate above), then `railform version-sites --base "origin/${BASE_REF}"` | pull requests |
| `hooks` | `railform hooks-check`, `railform adopt --check`, and the hygiene hooks over the changed files, with pre-commit on the newest of `python.versions`, else `python.newest` (the `hooks_python` output of `frame --github-output`), so a `default_language_version` naming that Python resolves | every run; the hygiene hooks on pull requests |
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
- **The resolution date.** Every step that runs `uvx` for railform or for a pinned tool (the
  railform verbs, zizmor, and the pre-commit replay of `hooks`) resolves the packages it installs
  as of one date, through uv's `UV_EXCLUDE_NEWER` in that step's `env:`. The date is written
  once per workflow file, as the workflow-level `RAILFORM_EXCLUDE_NEWER` (an RFC 3339 timestamp;
  uv does not read that name), and `python-ci.yml` and `tag-watch.yml` hold the same value. A
  dependency published after the date, a new `pyyaml` say, cannot reach a run of a given railform
  commit. It pins the packages the steps install from an index. It does not pin the commit (the
  `railform-sha` input does that), the uv version (setup-uv's `version` does), or the actions
  (their SHAs do). The environments pre-commit itself builds for the hygiene hooks are installed
  by pip, which does not read the date; they stay pinned only by the hook repository's frozen
  rev. The date never reaches the type's pipeline in `checks`, which runs the adopter's own
  commands (`uv lock --check`, `uv sync --frozen`, the test command, and a `uvx` call in a
  `plugin-marketplace` pipeline), because a lock resolved under another date would read as out
  of date there. Each railform release moves the date to its own day, in a commit of the release
  pull request that is not the `release:` commit, since a workflow file is not release metadata.
  Between releases, a change that pins a tool, or raises a dependency floor of railform, to a
  release newer than the date moves the date in the same pull request.
- **What the workflow allows.** Every action is pinned by a 40-hex commit, every setup-uv step
  pins the same uv version, and the token is read-only (`contents: read`). Every job has a time
  limit (`timeout-minutes`, 45 for `checks` and 10 to 20 for the others), and only `checks`, which
  syncs the caller's lock, keeps setup-uv's cache. It runs from the
  caller's `pull_request` and `push` events, never `pull_request_target`. The jobs that read
  history (`commit-lane`, `changelog` and `hooks`) check out all of it. No `run:`
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

## The release check

`railform release-check` judges the release tags of the repository it runs in. It takes no
arguments and judges every tag anew on each run, so the daily run of the tag watch also stands
for immutability: a tag moved off its release merge commit fails the next day, with no ledger to
keep.

- **Which tags.** A tag belongs to the `release.tag_prefix` row whose `tag` starts its name and
  leaves exactly `X.Y.Z` after it (the longest such `tag` wins), so `v0.2.0` belongs to the
  default row and `humblepowers--v0.19.0` to a row with the tag `humblepowers--v`. Other tags
  (`freeze/x`, `v1.0.0-rc1`) are not judged. A row's floor is its own `since`, else
  `release.since`; both are tag names of that row, compared by version, and the floor tag need
  not exist. Only tags at or above their row's floor are judged, so the tags from before
  adoption never fail. A floor that is not a tag of its row is a configuration error (exit 2),
  so a repository with one row per plugin sets `since` per row.
- **Configuration errors** (exit 2, `usage`): a floor as above, and the errors of the release
  lines and version sites it reads with the version-site check: two `release.tag_prefix` or two
  `changelog.records` rows with one prefix, and a `version.sites` path that is empty, absolute,
  holds `..` or a backslash, a pattern with no group, or a site no tag row covers.
- **The release pull request** of a tag of version V on a line named N (the last segment of the
  row's `prefix`, none for the root row) is a merged pull request whose head branch starts with
  `release.pr_branch_prefix` (default `release/`), or whose title `release.pr_title_pattern`
  matches at its start (default a `release` subject: `release: …`, `release(scope): …`), and that
  carries N as a whole token on a named line and V as its first version: the first `X.Y.Z` of the
  head branch (or of the title), with no pre-release or build suffix after it. A repository with
  another convention sets the two keys, each with an `exceptions` row, since a broader convention
  weakens this check; an empty one identifies nothing by its side.
  `release/0.2.0`, `release/keel-0.2.0` and `release(humblepowers): 0.19.0` identify;
  `docs/after-0.2.0`, `release/0.2.01`, `release/1.0.0-rc1` and `release: 1.1.0 (from 1.0.0)` do
  not identify the versions they mention later or with a suffix.
- **The rules,** each finding beginning `<tag>: `, the verdict naming the first in this order:
  - `lightweight-tag`: the tag is not annotated; lay release tags with
    `git tag -a vX.Y.Z <release merge commit> -m "..."`;
  - `no-release-pr`: no merged pull request is the tag's release pull request;
  - `tag-placement`: the tag's commit is not the merge commit of its release pull request. A tag
    on that merge commit passes, also when the commit is the default branch's tip. A tag that
    resolves to no commit, or a pull request with no merge commit, never passes;
  - `release-commit`: a `release:` commit the tag's merge commit brings in (a squash commit
    titled `release: …` included, a merge commit never) changes a path outside the release
    metadata, which is `release.metadata_paths` (globs over the whole path, `*` within a segment,
    `**` across segments) plus every `changelog.records` file and every `version.sites` path.
- `release.enabled = false` (with an `exceptions` row) turns the check off: it passes at once.
  `railform adopt` then writes no tag-watch caller (one that exists stays and is judged), and
  `adopt --check` does not ask for one; turning the check on again makes the missing caller
  `drift`. With no tag to judge it passes without reading any pull request.
- **Limits.**
  - When two merged pull requests identify the same version, a tag on either merge commit passes.
  - A release merged by rebase has no merge commit of its own: the tag sits on the last rebased
    commit, and only that commit is judged by the release-commit rule.
  - A repository's floor must sit past its last release whose pull request does not identify
    (one released from a branch not under `release/` with a title that is not a `release`
    subject); every judged tag needs an identifying pull request.
  - One pull request identifies one version per line, the first of its branch or title. A pull
    request that releases two lines at once (`release: a 0.2.0, b 0.3.0`) identifies `a` 0.2.0
    only, so the tag of the second line fails `no-release-pr`. Release each line in a pull request
    of its own.

The pull requests come from the GitHub REST API: `$GITHUB_REPOSITORY` (`owner/name`),
`$GITHUB_API_URL` (default `https://api.github.com`), and `$GITHUB_TOKEN` when set, which is
never printed. An API that cannot be read exits 2 with `api-error`, never a pass. To judge
against a saved list of pull requests instead, with no network, point `$RAILFORM_PULLS_FILE` at a
JSON array of entries in the API's shape (`number`, `title`, `head.ref`, `merged_at`,
`merge_commit_sha`).

**The tag watch.** The reusable `.github/workflows/tag-watch.yml` runs `release-check` from the
commit its required input `railform-sha` names, with a read-only token (`contents: read`,
`pull-requests: read`), which the client never sends to another host on a redirect. It checks
out the whole history, which brings the tags, and on a push or a dispatch from a tag the default
branch rather than the tag. Its caller is the
`.github/workflows/railform-tag-watch.yml` that `railform adopt` writes while the release check
is on, the same file for every repository: it runs on every tag push, every day at 07:23 UTC and on demand
(`workflow_dispatch`), which also clears an alarm once the tag is fixed. railform's own caller
calls the workflow by local path.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the gate commands and the commit rules, and
[CHANGELOG.md](CHANGELOG.md) for what changed. The license is MIT.

The tests marked `slow` need the network; the hook-rail tests, which run real `git commit` and
`git push` through pre-commit, need it in every session. Offline, run
`uv run --frozen python -m pytest -q -m "not slow"`.

The workflow tests run the workflows' own scripts in bash, through a local model of a GitHub
Actions run (`tests/workflow_model.py`) in which railform is the code under test. On Windows
they use the bash that ships with Git, never the WSL launcher.
