# Adopting a repository

This guide takes a repository that has its own CI and hooks to the point where railform runs them,
in the order the steps depend on each other. The [README](../README.md) is the reference for what
each part does; this page is the path through it.

At the end, the repository has:

- one `ci.yml` whose `railform` job calls the shared workflow at a pinned commit, and whose `gate`
  job is the single status a branch ruleset requires;
- tracked hook wrappers in `.githooks`, run by every clone after `railform arm`;
- a `railform.toml` that holds the repository's frame, with every departure from the project
  type's defaults written down with a reason.

The two pull requests that adopted railform first are worked examples, with their inventory tables
written out: [keel #56](https://github.com/grimaldost/keel/pull/56) (a Python package) and
[craft-collection #165](https://github.com/grimaldost/craft-collection/pull/165) (a plugin
marketplace).

## 1. Take an inventory

Adopting replaces CI and hook steps with railform's, so write down what exists before changing
anything. A pull request description with a before and after column is enough; the reviewer needs
it to see what stops running and what keeps running.

| Item | Where to look | Why it matters |
|---|---|---|
| Workflows and jobs | `.github/workflows/*.yml` | Each job is replaced by a railform job, kept inside `ci.yml`, or left as a workflow that never runs on pull requests |
| Pull request triggers | the `on:` block of each workflow | A workflow that runs on `pull_request` beside `ci.yml` is judged by `railform adopt --check` (step 5) |
| Hooks | `.pre-commit-config.yaml`, the hook directory, `git config core.hooksPath` | Adopt writes the wrappers into `hooks.dir` (`.githooks`) and leaves a stub in the directory they came from |
| Changelog and version scripts | `CHANGELOG.md`, release or bump scripts | Their paths become `changelog.records` and `version.sites` rows in `railform.toml` |
| Python floor and commands | `requires-python`, the test and lint commands CI runs | The floor seeds `python.versions`; a command that differs from the type's preset is a departure (step 3) |
| Required status checks | the branch ruleset, or branch protection | These move to the single status `gate` (step 6) |

The required checks are readable with the GitHub CLI: `gh api repos/<owner>/<repo>/rulesets` lists
the rulesets, and `gh api repos/<owner>/<repo>/rulesets/<id>` shows the `required_status_checks`
rule of one. A repository on branch protection reads
`gh api repos/<owner>/<repo>/branches/main/protection`.

## 2. Run `railform adopt`

A pin is the full 40-hexadecimal commit of a railform release, never a branch or a version. The tag
of a release is annotated, so this prints the commit the tag points at:

```
git ls-remote https://github.com/grimaldost/railform 'refs/tags/v0.1.0^{}'
```

Work on a branch, with the repository's `ci.yml` and hooks as they are, and run adopt from the
repository root. railform is installed from git at the same commit, never by name from a package
index:

```
uvx --from git+https://github.com/grimaldost/railform@<40-hex sha> railform adopt --pin <40-hex sha>
```

The first run needs `--pin`; later runs keep the pin the workflow already names. railform is not a
dependency of the repository, so every command of this guide that reads `railform <verb>` is run
the same way, as `uvx --from git+https://github.com/grimaldost/railform@<40-hex sha> railform
<verb>`. Adopt plans every
file before writing any, and writes these:

| Path | What adopt writes |
|---|---|
| `.github/workflows/ci.yml` | Two jobs between marker lines. `railform` calls the shared workflow at the pin and passes the pin as `railform-sha`. `gate` needs every other job in the file and fails when one failed or was cancelled. The `pull_request` trigger gains `types: [opened, synchronize, reopened, edited]`. The file is created when absent; every other line is kept |
| `.pre-commit-config.yaml` | Two entries between marker lines: railform's hooks at the pin, and the hygiene hooks of `pre-commit/pre-commit-hooks` |
| `.github/dependabot.yml` | An ignore entry for `grimaldost/railform` in the `github-actions` update, so Dependabot leaves the pin to `railform adopt --pin` |
| `.githooks/pre-commit`, `commit-msg`, `pre-push` | The hook wrappers, tracked as `100755` in the git index |
| `.github/workflows/railform-tag-watch.yml` | The caller of the release-tag watch, pinned |
| `railform.toml` | On the first run only: the project type and `python.versions`. After that the file is the repository's |

Everything outside the marker lines is kept byte for byte, and a second run changes nothing. If the
hooks lived in another directory, adopt leaves a stub for each hook there, which prints the
`railform arm` command and fails, so a clone still armed to the old directory cannot commit
unchecked. The wrappers are staged by adopt; `git add` the other files, which are written to the
working tree only.

Adopt does not decide what to do with the repository's own jobs and hooks. Do that now:

- A job that duplicates the railform pipeline (a lint, test or workflow-audit job) is deleted. The
  `checks` job of the shared workflow runs the type's pipeline and `audit` runs zizmor.
- A job with no railform equivalent stays in `ci.yml`. Run `railform adopt` again, and `gate` now
  needs it.
- A commit-message or branch-name hook that `railform-commit-msg` and `railform-branch-name`
  replace is deleted from `.pre-commit-config.yaml`, outside the marker lines.
- In the two uv types the wrappers run `uv run --frozen python -m pre_commit`, so `pre-commit` must
  be in the project's locked dependencies (railform keeps it in its `dev` group). Add it and run
  `uv lock`.
- A repository with release tags from before adoption sets `release.since` in `railform.toml` to
  the first tag name railform should judge (`release.since = "v0.4.0"`, say), so the daily tag
  watch does not fail on older ones.

## 3. Write departures as `exceptions` rows

The effective frame is the default frame, then the preset of the project type, then
`railform.toml`. `railform frame` prints it and `railform frame --json` prints it as JSON. A value
in `railform.toml` that differs from the preset is a departure, and a departure is legal only when
an `exceptions` row names the same key and value and gives a reason:

```toml
[ci.setup]
fetch_depth = 0

[[exceptions]]
key = "ci.setup.fetch_depth"
value = 0
reason = "the suite diffs against the merge base with origin/main"
```

keel's pull request declares one row, this one (its tests need history and tags); craft-collection's
declares three, for its test command, its ruff version and the same checkout depth. Keys that are per repository by nature
(`python.versions`, `python.package`, `version.sites`, `changelog.records`, `release.since`,
`release.tag_prefix` and the others in `meta.parameter_keys`) never need a row.

## 4. Check the frame and the adoption

```
railform frame --check
railform adopt --check
```

`frame --check` fails with `unknown-key`, `wrong-type`, `bad-exception` (an empty reason, or a row
that matches no departure) or `unlisted-departure`. `adopt --check` writes nothing and fails with
`drift` when an owned part differs from what adopt would write, `pin-mismatch` when the pin sites
disagree, and `extra-pr-workflow` (step 5). The `frame` job of the shared workflow runs
`frame --check` and the `hooks` job runs `adopt --check`, on every pull request, so a hand edit
inside a marker line fails CI. Before opening the pull request,
run the checks that CI will run on it:

```
railform hooks-check
railform private-refs
railform private-refs --diff origin/main...HEAD
railform commit-range origin/main...HEAD
railform branch-name
```

The second `private-refs` reads the branch's commit messages as well as the files it changes; in
CI the `commit-lane` job also passes the pull request's title and body (`--event`).

A repository with files a coding-assistant session loads (`session_loaded` in the frame) also runs
`railform session-paths --diff origin/main...HEAD --frame-from origin/main`.

## 5. Put every pull request workflow under `gate`

A job's `needs:` cannot reach another workflow file, so `gate` cannot wait on a job in a second
workflow that runs on `pull_request`. While one exists, `railform adopt --check` fails with
`extra-pr-workflow` and names the file. There are two ways out:

- Move its jobs into `ci.yml` and run `railform adopt`, which adds them to `gate`'s `needs`. This
  is the default choice: the single required status then covers them.
- Leave the file and list it, by name or by path, in `ci.extra_workflows`, with an `exceptions`
  row. Its result is then not part of `gate`; a ruleset has to require its status separately, or
  it is advice only.

```toml
[ci]
extra_workflows = ["lint.yml"]

[[exceptions]]
key = "ci.extra_workflows"
value = ["lint.yml"]
reason = "lint.yml is a required status of its own"
```

A workflow that never runs on pull requests (a schedule, a tag push, `workflow_dispatch`) needs
neither. A `paths`, `paths-ignore`, `branches` or `branches-ignore` filter on the `pull_request`
trigger of `ci.yml` is reported by adopt: a pull request the filter leaves out runs no `ci.yml`,
reports no `gate`, and a ruleset that requires `gate` waits on it forever.

## 6. Require `gate` in the branch ruleset

`gate` is the one status to require. A pull request runs the `ci.yml` of its own branch, so the
adoption pull request reports `gate` and none of the old contexts, and a ruleset that still
requires them cannot merge it. The order is:

1. In the repository settings, open the ruleset (or branch protection rule) of the default branch,
   under "Require status checks to pass".
2. Add `gate`. GitHub offers a status in this list after a run has reported it, so push the
   adoption branch first.
3. Remove the old contexts, such as the matrix job names of the CI that railform replaces.
4. Pull requests opened before the merge report no `gate` until they are rebased onto the new
   `ci.yml`.

`gate` protects nothing by itself. On a plan that offers no rulesets or branch protection for the
repository (a private repository on a free plan, for example) every check still runs and a red
`gate` is shown on the pull request, but nothing blocks the merge; there it is advice, and merging
a red pull request is a decision of the person merging.

## 7. Arm every clone

The wrappers are tracked, but git runs them only when `core.hooksPath` names their directory, and
that setting is local to a clone. In each clone, once, from the repository root:

```
uvx --from git+https://github.com/grimaldost/railform@<40-hex sha> railform arm
```

Worktrees share their clone's configuration, so arming the clone arms them. `railform hooks-check
--armed` fails with `not-armed` in a clone that has not run it. Put the command in the
contributing guide.

## How the type is detected

The first `railform adopt` writes `railform.toml` with a guessed `project.type`:

| The repository has | Type | Pipeline of `checks` |
|---|---|---|
| no `pyproject.toml` | `plugin-marketplace` | ruff from `uvx`, then the test command |
| a `pyproject.toml` and a `src/` directory with Python files | `python-package` | `uv lock --check`, `uv sync --frozen`, ruff, `ty`, the test command |
| any other `pyproject.toml` | `python-scripts` | the same without `ty` |

`--type python-package`, `--type python-scripts` or `--type plugin-marketplace` overrides the guess,
as a plugin repository that has a `pyproject.toml` needs. The flag applies to the first run only:
after that the type is the one in `railform.toml`, and a different `--type` is refused. The seeded
`python.versions` are the floor of `requires-python` and `python.newest` (3.14 at this writing),
so `>=3.11` gives `["3.11", "3.14"]`; with no `pyproject.toml` it is `python.newest` alone.

## Worked example: a python-package repository

`acme` is a small package: `src/acme/`, `requires-python = ">=3.11"`, a `ci.yml` with one `test`
job on pull requests, a separate `lint.yml` that also runs on pull requests, and a tracked
`.githooks/pre-commit` with `core.hooksPath` set to it. The output below is from a scratch
repository of this shape.

Inventory (step 1):

| Item | Before |
|---|---|
| CI | `ci.yml` job `test`; `lint.yml` job `lint` on `pull_request` |
| Hooks | `.githooks/pre-commit`, armed with `core.hooksPath` |
| Python floor | `>=3.11` |
| Required status | `test`, `lint` |

Adopt (step 2):

```
$ uvx --from git+https://github.com/grimaldost/railform@<40-hex sha> railform adopt --pin <40-hex sha>
railform.toml: written (the first run seeds it)
.github/workflows/ci.yml: written
.pre-commit-config.yaml: written
.github/workflows/railform-tag-watch.yml: written
.github/dependabot.yml: written
.githooks/pre-commit: written, 100755 in the git index
.githooks/commit-msg: written, 100755 in the git index
.githooks/pre-push: written, 100755 in the git index
.github/workflows/lint.yml: runs on pull requests beside ci.yml, where gate cannot need its jobs; move its jobs into ci.yml, or list it in ci.extra_workflows with an exceptions row; railform adopt --check fails until then
railform-verdict: adopt pass ok
```

The type was detected as `python-package`, the seeded `railform.toml` holds the type and
`python.versions = ["3.11", "3.14"]`, and `ci.yml` now has the marked `railform` and `gate` jobs
above the old `test` job, with `needs: [railform, test]`. The directory did not move, so no stub
was written.

The `test` job runs what `checks` now runs, so it is deleted; `lint.yml` is deleted and its steps
go into a `lint` job in `ci.yml`, which the next run puts under `gate`:

```
$ railform adopt --check
.github/workflows/ci.yml:17: differs from what railform adopt writes: expected 'needs: [railform, lint]', found 'needs: [railform, test]'
railform-verdict: adopt fail drift
$ railform adopt
.github/workflows/ci.yml: written
railform-verdict: adopt pass ok
```

The suite needs history, so `railform.toml` gets the `ci.setup.fetch_depth` departure and its
`exceptions` row from step 3, and the checks pass:

```
$ railform frame --check
railform-verdict: frame pass ok
$ railform adopt --check
railform-verdict: adopt pass ok
```

The ruleset then requires `gate` and drops `test` and `lint` (step 6), `pre-commit` is added to the
dev dependencies, and each contributor runs `railform arm` in their clone (step 7).

## Changing the frame after adoption

On a pull request, the changelog gate and the `changelog` job's `version-sites --base` read the
`railform.toml` of the merge base, not the one the pull request writes. A pull request that
edits `changelog.records`, `version.sites`, `release.tag_prefix` or `changelog.enabled` is judged
by the frame it replaces, and its own frame holds from the next pull request on. The adoption pull
request is the exception: its merge base has no `railform.toml`, so the frame it writes judges it.

Most edits still land in one pull request, because the old frame passes them: a new records row, a
records file moved to another path with its row in a pull request of its own, a new version site
whose file already agrees, a prefix that watches more. An edit that the old frame would fail, such
as moving a version site to another file, takes three, each judged by the frame the step before it
merged:

1. Add the new version site beside the old one, with the new file in step, and merge.
2. Remove the old site's row and leave the old file as it is, still in step, and merge.
3. Remove the old file, or the version in it, which no frame reads any more.

Steps 2 and 3 cannot be one pull request: its judge is the frame of step 1, which still lists the
old site, and a missing file or one with no version fails `version-sites` as `unreadable`.

There is no `exceptions` row for these keys: they are parameter keys, which never depart, and a row
written by the pull request it excuses would be the self-judgement the merge base's frame removes.
The changelog gate also fails, with `watches-nothing`, a pull request whose own frame watches no
tracked path, since every pull request after it would pass unread.

## Moving to a newer pin

A new release is adopted by running that release's own `adopt`, because the templates ship in the
package; the old install would write the old text. Read the entries of
[CHANGELOG.md](../CHANGELOG.md) between the two pins first, then:

```
uvx --from git+https://github.com/grimaldost/railform@<new 40-hex sha> railform adopt --pin <new 40-hex sha>
```

`--pin` moves every reference at once: the `uses:` commit, the `railform-sha` input, the
pre-commit `rev` and the tag-watch caller. Then run `railform frame --check` and
`railform adopt --check`. A new default or preset can change what counts as a departure: an
`exceptions` row whose value the preset now has fails as `bad-exception` and is deleted, and a
repository setting that the new frame rejects fails as `unknown-key` or `wrong-type`. Commit the
result as one pull request that touches only the pin sites and, if needed, `railform.toml`.
A stub left in a former hooks directory is deleted once every clone has run `railform arm`.
