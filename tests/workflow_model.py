"""A local model of a GitHub Actions run of a caller and the reusable workflow it calls.

The red proofs of the development-rails spec, §9, go through the caller: a fixture repository's
`ci.yml`, written by `railform adopt`, calls `python-ci.yml`, and its `gate` job turns red or
green. A test cannot start GitHub Actions, so this module runs the real text of both workflows
locally, modelling only what they use:

- Jobs run in `needs` order. A job runs when its `if:` holds, with `success() &&` added when the
  condition calls no status function, so a job whose needs did not all succeed is skipped unless
  its condition says otherwise. Steps likewise, judged on the earlier steps of their job.
- A job that calls a reusable workflow runs it with its `with:` inputs and takes its conclusion:
  `failure` when a job failed, else `success` (a skipped job fails nothing).
- `run:` steps run in bash, in the repository's working tree, with the workflow, job and step
  `env:` and a fresh `$GITHUB_OUTPUT` per step, whose values become the step's outputs and,
  through the job's `outputs:`, its job outputs. A step's shell is resolved as GitHub resolves
  it, from the step's `shell:`, the job's `defaults.run.shell`, the workflow's, then the leg's
  runner: a declared `bash` runs with `-eo pipefail`; with none declared, a Linux or macOS
  runner's default is `bash -e`, and a `windows-*` runner's is pwsh, which the model refuses. It
  checks every leg of a job that is not skipped, given jobs and legs not selected included, so a
  workflow whose Windows legs would run pwsh fails here.
- The model reads only the workflow, job, step and strategy keys listed in `_KEYS`, and ignores
  only `name`, `permissions` and `timeout-minutes`, which change no result of a run that ends.
  Any other key (`continue-on-error`, `working-directory`, `concurrency`, a matrix `include`...)
  fails the run, so a workflow never passes here on a key the model does not read.
- `${{ }}` expressions: string, number and boolean literals and null; the contexts `github`,
  `inputs`, `needs`, `matrix`, `steps` and `env`; `!`, `==`, `!=`, `&&` and `||` with their
  GitHub values; property access with `.` and the `.*` filter; and the functions `always`,
  `success`, `failure`, `cancelled`, `contains`, `startsWith`, `endsWith`, `fromJSON` and `toJSON`.

Where a local run differs from a real one:

- `uses:` steps run nothing (their `with:` is still evaluated): the checkout is the repository's
  working tree as it stands, and uv is already on PATH.
- railform is the code under test. The one install every railform command in the workflow
  starts with (`INSTALL`) runs this interpreter's `python -m railform` instead, and a call to
  `grimaldost/railform/.github/workflows/<file>@<sha>` runs this checkout's `<file>`.
- A matrix job runs only the legs a test selects (`Runner.select`, by default the first leg),
  since one host cannot be every runner; it fails when a leg that ran failed.
- A job named in `given` does not run and takes the result given: for jobs that need the network
  (the zizmor audit) or that a test is not about. Its `if:` is still judged first.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'
INSTALL = 'uvx --from "git+https://github.com/grimaldost/railform@$RAILFORM_SHA" railform'
_REMOTE_WORKFLOW = re.compile(r'grimaldost/railform/(\.github/workflows/[\w.-]+)@[0-9a-f]{40}')
# What would point uv at railform's own environment instead of the repository's.
_DROPPED_ENV = ('VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT', 'UV_FROZEN', 'UV_LOCKED', 'UV_NO_SYNC')
STEP_TIMEOUT = 900
# What `shell: bash` runs with, and what a Linux or macOS runner runs a step that names no shell
# with (GitHub's `bash -e {0}`).
DECLARED_BASH = ('-eo', 'pipefail')
DEFAULT_BASH = ('-e',)
# Ignored: they change no result of a run that ends.
IGNORED = frozenset({'name', 'permissions', 'timeout-minutes'})
# Every key the model reads, by where it sits; any other key fails the run.
_KEYS = {
    'workflow': frozenset({'on', True, 'env', 'defaults', 'jobs'}) | IGNORED,
    'job': frozenset(
        {
            'needs',
            'if',
            'uses',
            'with',
            'runs-on',
            'strategy',
            'defaults',
            'env',
            'outputs',
            'steps',
        }
    )
    | IGNORED,
    'step': frozenset({'id', 'if', 'uses', 'with', 'run', 'shell', 'env'}) | IGNORED,
    'strategy': frozenset({'matrix', 'fail-fast'}),
    'defaults': frozenset({'run'}),
    'defaults.run': frozenset({'shell'}),
}


def _check_keys(kind: str, block: dict[Any, Any], where: str) -> None:
    unknown = set(block) - _KEYS[kind]
    assert not unknown, f'{where}: the model does not read {sorted(map(str, unknown))}'


def _default_shell(block: dict[str, Any], where: str) -> str | None:
    """The `defaults.run.shell` of a workflow or a job, its keys checked."""
    defaults = block.get('defaults') or {}
    _check_keys('defaults', defaults, f'{where} defaults')
    run = defaults.get('run') or {}
    _check_keys('defaults.run', run, f'{where} defaults.run')
    return run.get('shell')


def check_workflow(workflow: dict[Any, Any]) -> None:
    """Fail on any key of the workflow, its jobs, strategies or steps the model does not read."""
    _check_keys('workflow', workflow, 'the workflow')
    _default_shell(workflow, 'the workflow')
    for name, job in workflow['jobs'].items():
        _check_keys('job', job, f'job {name}')
        _default_shell(job, f'job {name}')
        _check_keys('strategy', job.get('strategy') or {}, f'job {name} strategy')
        for number, step in enumerate(job.get('steps') or []):
            _check_keys('step', step, f'job {name} step {number}')


def step_shell(
    step: dict[str, Any], job: dict[str, Any], workflow_shell: str | None, runs_on: object
) -> tuple[str, ...]:
    """The bash options a `run:` step runs with on the runner `runs_on`, resolved as GitHub
    resolves its shell; a step that would not run in bash fails."""
    declared = step.get('shell') or _default_shell(job, 'the job') or workflow_shell
    labels = [runs_on] if isinstance(runs_on, str) else runs_on
    assert isinstance(labels, list) and labels, f'runs-on {runs_on!r} is not a runner'
    label = step.get('name') or step.get('run', '').splitlines()[0]
    if declared is None:
        windows = [runner for runner in labels if str(runner).startswith('windows')]
        assert not windows, f'{label}: no shell declared, so it runs in pwsh on {windows[0]}'
        return DEFAULT_BASH
    assert declared == 'bash', f'{label}: the model runs bash steps only, not {declared}'
    return DECLARED_BASH


def bash() -> str | None:
    """The bash that `shell: bash` runs: on Windows the one that ships with git (never the WSL
    launcher in System32), elsewhere the one on PATH."""
    if os.name != 'nt':
        return shutil.which('bash')
    done = subprocess.run(['git', '--exec-path'], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return None
    # <git>/mingw64/libexec/git-core
    top = Path(done.stdout.strip()).parents[2]
    for candidate in (top / 'bin' / 'bash.exe', top / 'usr' / 'bin' / 'bash.exe'):
        if candidate.is_file():
            return str(candidate)
    return None


def load(path: Path) -> dict[Any, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def triggers(workflow: dict[Any, Any]) -> Any:
    # YAML 1.1 reads the key `on` as the boolean True.
    return workflow.get('on', workflow.get(True))


# --- expressions -------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""\s*(?:
    (?P<string>'(?:[^']|'')*')
    |(?P<number>\d+(?:\.\d+)?)
    |(?P<op>==|!=|&&|\|\||[!().,*])
    |(?P<name>[A-Za-z_][A-Za-z0-9_-]*)
    )""",
    re.VERBOSE,
)
_EXPRESSION = re.compile(r'\$\{\{(.*?)\}\}', re.DOTALL)
_STATUS_CALL = re.compile(r'\b(?:success|failure|always|cancelled)\s*\(')


class _Filtered(list):  # type: ignore[type-arg]
    """The values an object filter (`.*`) selects; a property after it applies to each."""


def truthy(value: object) -> bool:
    if value is None or value is False or value == '':
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not math.isnan(value)
    return True


def _equal(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def _text(value: object) -> str:
    """A value as a string, the way GitHub writes it into `env:` and `run:`."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _property(value: object, name: str) -> object:
    if isinstance(value, _Filtered):
        return _Filtered(_property(item, name) for item in value)
    if isinstance(value, dict):
        if name in value:
            return value[name]
        folded = {str(key).casefold(): item for key, item in value.items()}
        return folded.get(name.casefold())
    return None


@dataclass(frozen=True)
class Status:
    """What the status functions answer: whether everything before succeeded, or one failed."""

    success: bool = True
    failure: bool = False


class _Parser:
    def __init__(self, text: str, context: dict[str, Any], status: Status) -> None:
        self.tokens: list[tuple[str, str]] = []
        position, text = 0, text.strip()
        while position < len(text):
            match = _TOKEN.match(text, position)
            if match is None or match.end() == position:
                raise ValueError(f'cannot read the expression {text!r} at {position}')
            kind = match.lastgroup
            assert kind is not None
            self.tokens.append((kind, match.group(kind)))
            position = match.end()
        self.index = 0
        self.context = context
        self.status = status

    def _peek(self, *ops: str) -> bool:
        if self.index >= len(self.tokens):
            return False
        kind, text = self.tokens[self.index]
        return kind == 'op' and text in ops

    def _take(self) -> tuple[str, str]:
        if self.index >= len(self.tokens):
            raise ValueError('the expression ends too early')
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _expect(self, op: str) -> None:
        kind, text = self._take()
        if (kind, text) != ('op', op):
            raise ValueError(f'expected {op!r}, found {text!r}')

    def parse(self) -> object:
        value = self._or()
        if self.index != len(self.tokens):
            raise ValueError(f'unexpected {self.tokens[self.index][1]!r}')
        return value

    def _or(self) -> object:
        value = self._and()
        while self._peek('||'):
            self._take()
            right = self._and()
            value = value if truthy(value) else right
        return value

    def _and(self) -> object:
        value = self._equality()
        while self._peek('&&'):
            self._take()
            right = self._equality()
            value = right if truthy(value) else value
        return value

    def _equality(self) -> object:
        value = self._unary()
        while self._peek('==', '!='):
            op = self._take()[1]
            right = self._unary()
            same = _equal(value, right)
            value = same if op == '==' else not same
        return value

    def _unary(self) -> object:
        if self._peek('!'):
            self._take()
            return not truthy(self._unary())
        return self._postfix()

    def _postfix(self) -> object:
        value = self._primary()
        while self._peek('.'):
            self._take()
            if self._peek('*'):
                self._take()
                if isinstance(value, dict):
                    value = _Filtered(value.values())
                else:
                    value = _Filtered(value if isinstance(value, list) else [])
                continue
            kind, name = self._take()
            if kind != 'name':
                raise ValueError(f'expected a property name, found {name!r}')
            value = _property(value, name)
        return value

    def _primary(self) -> object:
        kind, text = self._take()
        if kind == 'string':
            return text[1:-1].replace("''", "'")
        if kind == 'number':
            return float(text) if '.' in text else int(text)
        if kind == 'op' and text == '(':
            value = self._or()
            self._expect(')')
            return value
        if kind != 'name':
            raise ValueError(f'unexpected {text!r}')
        literals = {'true': True, 'false': False, 'null': None}
        if text in literals:
            return literals[text]
        if self._peek('('):
            self._take()
            args: list[object] = []
            while not self._peek(')'):
                args.append(self._or())
                if not self._peek(')'):
                    self._expect(',')
            self._expect(')')
            return self._call(text, args)
        return _property(self.context, text)

    def _call(self, name: str, args: list[object]) -> object:
        folded = name.casefold()
        if folded == 'always':
            return True
        if folded == 'success':
            return self.status.success
        if folded == 'failure':
            return self.status.failure
        if folded == 'cancelled':
            return False
        if folded == 'contains':
            haystack, needle = args
            if isinstance(haystack, list):
                return any(_equal(item, needle) for item in haystack)
            return _text(needle).casefold() in _text(haystack).casefold()
        if folded == 'startswith':
            return _text(args[0]).casefold().startswith(_text(args[1]).casefold())
        if folded == 'endswith':
            return _text(args[0]).casefold().endswith(_text(args[1]).casefold())
        if folded == 'fromjson':
            return json.loads(_text(args[0]))
        if folded == 'tojson':
            return json.dumps(args[0])
        raise ValueError(f'the model has no function {name}()')


def evaluate(text: str, context: dict[str, Any], status: Status | None = None) -> object:
    """The value of one expression, written with or without `${{ }}`."""
    whole = _EXPRESSION.fullmatch(text.strip())
    return _Parser(whole[1] if whole else text, context, status or Status()).parse()


def interpolate(value: object, context: dict[str, Any], status: Status | None = None) -> object:
    """`value` with its `${{ }}` expressions evaluated: a string that is one expression keeps the
    expression's value (a list stays a list); expressions inside text become text."""
    if isinstance(value, str):
        whole = _EXPRESSION.fullmatch(value.strip())
        if whole:
            return evaluate(whole[1], context, status)
        return _EXPRESSION.sub(lambda match: _text(evaluate(match[1], context, status)), value)
    if isinstance(value, dict):
        return {key: interpolate(item, context, status) for key, item in value.items()}
    if isinstance(value, list):
        return [interpolate(item, context, status) for item in value]
    return value


def holds(condition: object, context: dict[str, Any], status: Status) -> bool:
    """Whether a job's or a step's `if:` holds; no condition means `success()`."""
    if condition is None:
        text = 'success()'
    elif isinstance(condition, bool):
        text = 'true' if condition else 'false'
    else:
        text = str(condition).strip()
        whole = _EXPRESSION.fullmatch(text)
        if whole:
            text = whole[1]
    if not _STATUS_CALL.search(text):
        text = f'success() && ({text})'
    return truthy(evaluate(text, context, status))


def legs(job: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    """Every leg of a job's matrix (no `include` or `exclude`), or one empty leg."""
    matrix = interpolate((job.get('strategy') or {}).get('matrix') or {}, context)
    assert isinstance(matrix, dict)
    assert not {'include', 'exclude'} & set(matrix), 'the model has no include or exclude'
    names = list(matrix)
    values = [matrix[name] for name in names]
    for name, items in zip(names, values, strict=True):
        assert isinstance(items, list), (name, items)
    return [
        dict(zip(names, combination, strict=True)) for combination in itertools.product(*values)
    ]


# --- runs --------------------------------------------------------------------------------------


@dataclass
class JobRun:
    result: str
    outputs: dict[str, str] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)


@dataclass
class WorkflowRun:
    conclusion: str
    jobs: dict[str, JobRun]
    called: dict[str, WorkflowRun] = field(default_factory=dict)

    def results(self) -> dict[str, str]:
        """Every job's result, a called workflow's jobs included as `<caller job>/<job>`."""
        found = {name: job.result for name, job in self.jobs.items()}
        for name, run in self.called.items():
            found.update({f'{name}/{inner}': result for inner, result in run.results().items()})
        return found

    def text(self) -> str:
        lines = [f'{name}: {job.result}\n' + '\n'.join(job.log) for name, job in self.jobs.items()]
        lines += [f'--- {name} calls ---\n{run.text()}' for name, run in self.called.items()]
        return '\n'.join(lines)


def _outputs(path: Path) -> dict[str, str]:
    """The `name=value` and `name<<DELIMITER` entries of a `$GITHUB_OUTPUT` file."""
    outputs: dict[str, str] = {}
    lines = iter(path.read_text(encoding='utf-8').splitlines())
    for line in lines:
        name, sep, value = line.partition('=')
        if sep and '<<' not in name:
            outputs[name] = value
            continue
        name, _, delimiter = line.partition('<<')
        body: list[str] = []
        for inner in lines:
            if inner == delimiter:
                break
            body.append(inner)
        outputs[name] = '\n'.join(body)
    return outputs


@dataclass
class Runner:
    """Runs workflows of the repository at `root` for one event."""

    root: Path
    scratch: Path
    github: dict[str, Any]
    event: dict[str, Any] = field(default_factory=dict)
    given: dict[str, str] = field(default_factory=dict)
    select: Callable[[dict[str, Any]], bool] | None = None
    _count: itertools.count[int] = field(default_factory=itertools.count)

    def run(self, path: Path, inputs: dict[str, Any] | None = None) -> WorkflowRun:
        workflow = load(path)
        check_workflow(workflow)
        base = {'github': self.github, 'inputs': inputs or {}, 'vars': {}, 'secrets': {}}
        env = {
            key: _text(interpolate(value, base))
            for key, value in (workflow.get('env') or {}).items()
        }
        shell = _default_shell(workflow, 'the workflow')
        jobs: dict[str, JobRun] = {}
        called: dict[str, WorkflowRun] = {}
        pending = list(workflow['jobs'])
        while pending:
            ready = [
                name
                for name in pending
                if all(need in jobs for need in _needs(workflow['jobs'][name]))
            ]
            assert ready, f'the needs of {pending} never resolve'
            for name in ready:
                jobs[name] = self._job(name, workflow['jobs'][name], jobs, base, env, shell, called)
                pending.remove(name)
        results = [job.result for job in jobs.values()]
        conclusion = (
            'failure'
            if 'failure' in results
            else ('cancelled' if 'cancelled' in results else 'success')
        )
        return WorkflowRun(conclusion, jobs, called)

    def _job(
        self,
        name: str,
        job: dict[str, Any],
        done: dict[str, JobRun],
        base: dict[str, Any],
        env: dict[str, str],
        shell: str | None,
        called: dict[str, WorkflowRun],
    ) -> JobRun:
        needs = _needs(job)
        context = {
            **base,
            'needs': {
                need: {'result': done[need].result, 'outputs': done[need].outputs} for need in needs
            },
        }
        status = Status(
            success=all(done[need].result == 'success' for need in needs),
            failure=any(done[need].result in ('failure', 'cancelled') for need in needs),
        )
        if not holds(job.get('if'), context, status):
            return JobRun('skipped')
        all_legs = [] if 'uses' in job else legs(job, context)
        # Every leg's shells, before a given result or a leg selection could hide one.
        for leg in all_legs:
            runs_on = interpolate(job.get('runs-on'), {**context, 'matrix': leg})
            for step in job['steps']:
                if 'run' in step:
                    step_shell(step, job, shell, runs_on)
        if name in self.given:
            return JobRun(self.given[name], log=[f'{name}: given {self.given[name]}'])
        if 'uses' in job:
            inputs = interpolate(job.get('with') or {}, context)
            assert isinstance(inputs, dict)
            run = self.run(self._workflow(job['uses']), inputs)
            called[name] = run
            return JobRun(run.conclusion)
        chosen = [leg for leg in all_legs if self.select(leg)] if self.select else all_legs[:1]
        assert chosen, f'{name}: no matrix leg selected out of {all_legs}'
        runs = [self._steps(job, {**context, 'matrix': leg}, env, shell) for leg in chosen]
        failed = any(run.result == 'failure' for run in runs)
        return JobRun(
            'failure' if failed else 'success',
            runs[-1].outputs,
            [line for run in runs for line in run.log],
        )

    def _workflow(self, uses: str) -> Path:
        if uses.startswith('./'):
            return self.root / uses[2:]
        remote = _REMOTE_WORKFLOW.fullmatch(uses)
        assert remote, f'the model calls only railform workflows, not {uses}'
        return ROOT / remote[1]

    def _steps(
        self,
        job: dict[str, Any],
        context: dict[str, Any],
        workflow_env: dict[str, str],
        shell: str | None,
    ) -> JobRun:
        executable = bash()
        assert executable, 'no bash on this machine'
        job_env = {
            **workflow_env,
            **{
                key: _text(interpolate(value, context))
                for key, value in (job.get('env') or {}).items()
            },
        }
        steps: dict[str, Any] = {}
        failed = False
        log: list[str] = []
        for step in job['steps']:
            here = {**context, 'env': job_env, 'steps': steps}
            label = step.get('name') or step.get('uses') or step.get('run', '').splitlines()[0]
            if not holds(step.get('if'), here, Status(success=not failed, failure=failed)):
                log.append(f'  {label}: skipped')
                continue
            if 'uses' in step:
                # Not run, but its inputs are evaluated, so an expression the model cannot read
                # fails here as it would on GitHub.
                interpolate(step.get('with') or {}, here)
                log.append(f'  {label}: not run (a uses: step)')
                continue
            step_env = {
                **job_env,
                **{
                    key: _text(interpolate(value, here))
                    for key, value in (step.get('env') or {}).items()
                },
            }
            runs_on = interpolate(job.get('runs-on'), context)
            options = step_shell(step, job, shell, runs_on)
            code, output, outputs = self._bash(executable, options, step['run'], step_env)
            if step.get('id'):
                steps[step['id']] = {
                    'outputs': outputs,
                    'outcome': 'success' if code == 0 else 'failure',
                }
            log.append(f'  {label}: exit {code}\n{output}')
            if code != 0:
                failed = True
        outputs = {
            key: _text(interpolate(value, {**context, 'steps': steps}))
            for key, value in (job.get('outputs') or {}).items()
        }
        return JobRun('failure' if failed else 'success', outputs, log)

    def _bash(
        self, executable: str, options: tuple[str, ...], script: str, step_env: dict[str, str]
    ) -> tuple[int, str, dict[str, str]]:
        number = next(self._count)
        local = shlex.quote(Path(sys.executable).as_posix()) + ' -m railform'
        path = self.scratch / f'step-{number}.sh'
        path.write_text(script.replace(INSTALL, local), encoding='utf-8', newline='\n')
        output_file = self.scratch / f'step-{number}.output'
        output_file.write_text('', encoding='utf-8')
        event_file = self.scratch / f'event-{number}.json'
        event_file.write_text(json.dumps(self.event), encoding='utf-8')
        env = {key: value for key, value in os.environ.items() if key not in _DROPPED_ENV}
        env.update(step_env)
        env['GITHUB_OUTPUT'] = output_file.as_posix()
        env['GITHUB_EVENT_PATH'] = event_file.as_posix()
        env['GITHUB_EVENT_NAME'] = str(self.github.get('event_name', ''))
        done = subprocess.run(
            [executable, '--noprofile', '--norc', *options, path.as_posix()],
            cwd=self.root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=STEP_TIMEOUT,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr, _outputs(output_file)


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get('needs') or []
    return [needs] if isinstance(needs, str) else list(needs)


def github_context(event_name: str, **values: Any) -> dict[str, Any]:
    """The `github` context of a run: `event_name`, `sha`, and for a pull request `base_ref`,
    `head_ref` and `event.pull_request.head.sha`."""
    context: dict[str, Any] = {'event_name': event_name, 'sha': values.pop('sha', '0' * 40)}
    if event_name == 'pull_request':
        context['base_ref'] = values.pop('base_ref', 'main')
        context['head_ref'] = values.pop('head_ref', 'feat/change')
        head = values.pop('head_sha', '1' * 40)
        context['event'] = {'pull_request': {'head': {'sha': head}}}
    context.update(values)
    return context
