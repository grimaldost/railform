# Contributing

## Setup

railform is developed with [uv](https://docs.astral.sh/uv/). From a clone:

```
uv sync --frozen
```

## Gate commands

A change is ready when all of these pass, run from the repository root:

```
uv lock --check
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen ty check src
uv run --frozen python -m pytest -q
uvx zizmor==1.30.0 --min-severity high .github/workflows
```

CI runs the same commands on Ubuntu and Windows with Python 3.11 and 3.14, through railform's own
reusable workflow: `ci.yml` calls `.github/workflows/python-ci.yml` by local path at the commit
under test, and the commands come from `railform.toml` and the frame. Use `uv run --frozen
python`, not a bare `python`, so the interpreter and dependencies are the locked ones.

## Commits and pull requests

- Commits follow [Conventional Commits](https://www.conventionalcommits.org/): a subject of the
  form `type(scope): description`, with `scope` and a `!` for a breaking change optional. The
  types are `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style` and
  `revert`, plus `release: X.Y.Z` for a release commit.
- Keep commits small and coherent: one reason to change per commit.
- No AI attribution in any form: not in commit messages, pull request titles or bodies, the
  README or other documents.
- No machine path, user name or private repository name in any file, commit message or pull
  request text. A test that needs a home path uses a placeholder (`alice`, `someone`) or builds
  the string at run time.
- A module ported from another public repository names that repository and the path in its
  docstring.

## Changelog

Every pull request adds an entry under `## [Unreleased]` in `CHANGELOG.md`, in the
[Keep a Changelog](https://keepachangelog.com/) form: `Added`, `Changed`, `Deprecated`,
`Removed`, `Fixed` or `Security`.

## Tests

Every gate has a red proof: a test that fails without the change it protects. When a section
adds a rule, its tests include a case that must fail, run through the real command or workflow
that applies the rule.

## Changing adopt

`railform adopt` already refuses or reports a long list of edge cases. It takes no more
edge-case hardening unless a real adoption runs into the case: the repository and what adopt did
come first, then the test that reproduces it. Work on adoption goes into `docs/adopting.md` and
the README's five steps instead.

## Dependencies and installing

The only runtime dependency is PyYAML. There is no model client, and a test fails if a module
under `src/` imports a model SDK or reads a model API key variable.

railform is installed only from git, at a full commit SHA:
`git+https://github.com/grimaldost/railform@<40-hex sha>`. Never write a bare `railform`
requirement or run it by name from a package index, in a document, a workflow or a template.
