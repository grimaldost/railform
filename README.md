# railform

railform is a set of repository mechanics that several Python repositories share by reference
instead of by copy: a reusable GitHub Actions workflow, a pre-commit hook manifest, a small
command-line tool that runs the checks, and a default configuration (the "frame") for those
checks. A repository that adopts railform pins one commit of this repository and gets the same
CI pipeline, hook wrappers and commit checks as every other adopter at that commit.

Nothing in railform is usable yet. This repository holds the package skeleton, and every verb
exits with `not-implemented` until the section that adds it lands. Nothing is usable before
0.1.0.

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

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the gate commands and the commit rules, and
[CHANGELOG.md](CHANGELOG.md) for what changed. The license is MIT.
