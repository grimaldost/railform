"""RR-02: the package has no model client.

No module under `src/` imports a model SDK or reads a model API key from the environment, and
the one runtime dependency is pyyaml. The scanner is itself tested on small samples, so a
scanner that stopped detecting would fail here and not pass silently.
"""

import ast
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

# Top-level import names of model SDKs and the frameworks built on them.
MODEL_SDKS = frozenset(
    {
        'anthropic',
        'openai',
        'google.generativeai',
        'google.genai',
        'google.ai',
        'vertexai',
        'mistralai',
        'cohere',
        'litellm',
        'langchain',
        'langchain_core',
        'langchain_community',
        'langchain_openai',
        'langchain_anthropic',
        'llama_index',
        'ollama',
        'groq',
        'together',
        'replicate',
        'huggingface_hub',
        'transformers',
        'boto3',
        'xai_sdk',
        'dashscope',
        'ai21',
        'instructor',
        'claude_agent_sdk',
        'claude_code_sdk',
    }
)
# Vendors whose `<VENDOR>_API_KEY` (or any `<VENDOR>_*`, for the first three) is a model credential.
KEY_VENDORS = (
    'ANTHROPIC',
    'OPENAI',
    'GOOGLE',
    'GEMINI',
    'MISTRAL',
    'COHERE',
    'XAI',
    'DEEPSEEK',
    'OPENROUTER',
    'GROQ',
    'TOGETHER',
    'PERPLEXITY',
    'FIREWORKS',
    'HUGGINGFACE',
    'HF',
    'AZURE_OPENAI',
    'REPLICATE',
)
_ENV_NAME = re.compile(r'[A-Z_][A-Z0-9_]*')
_VENDOR_PREFIX = re.compile(r'(ANTHROPIC|OPENAI)_[A-Z0-9_]*')
_VENDOR_KEY = re.compile(rf'(?:{"|".join(KEY_VENDORS)})_(?:[A-Z0-9_]*_)?API_KEY')


def _is_model_sdk(dotted: str) -> bool:
    parts = dotted.split('.')
    return any('.'.join(parts[:n]) in MODEL_SDKS for n in range(1, len(parts) + 1))


def findings_in(source: str) -> list[str]:
    """Describe every model-SDK import and model-key variable name in `source`."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f'import {a.name}' for a in node.names if _is_model_sdk(a.name)]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level == 0 and (
                _is_model_sdk(base) or any(_is_model_sdk(f'{base}.{a.name}') for a in node.names)
            ):
                found.append(f'from {base} import ...')
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            # An import by string (`importlib.import_module('anthropic')`).
            if re.fullmatch(r'[A-Za-z_][\w.]*', text) and _is_model_sdk(text):
                found.append(f'module name {text!r}')
            # An environment variable name; a prose docstring never matches in full.
            elif _ENV_NAME.fullmatch(text) and (
                _VENDOR_PREFIX.fullmatch(text)
                or _VENDOR_KEY.fullmatch(text)
                or text in ('_API_KEY', 'API_KEY')
            ):
                found.append(f'environment variable {text}')
    return found


@pytest.mark.parametrize(
    'source',
    [
        'import anthropic',
        'import openai.types',
        'from anthropic import Anthropic',
        'from google import genai',
        'from google.generativeai import configure',
        'import google.genai as g',
        'import mistralai, os',
        'from langchain_openai import ChatOpenAI',
        'import litellm',
        'importlib.import_module("cohere")',
        'os.environ["ANTHROPIC_API_KEY"]',
        'os.getenv("OPENAI_API_KEY")',
        'os.environ.get("ANTHROPIC_BASE_URL")',
        'os.getenv("OPENAI_ORG_ID")',
        'os.environ["GEMINI_API_KEY"]',
        'os.environ["AZURE_OPENAI_API_KEY"]',
        'os.environ["MISTRAL_API_KEY"]',
        'os.environ["DEEPSEEK_API_KEY"]',
        'KEY = "XAI_API_KEY"',
        'suffix = "_API_KEY"',
    ],
)
def test_the_scanner_detects_a_model_client(source: str) -> None:
    assert findings_in(source) != []


@pytest.mark.parametrize(
    'source',
    [
        'import os, sys, tomllib, yaml',
        'from pathlib import Path',
        'from google_calendar import x',
        'os.environ["RAILFORM_LEDGER"]',
        'os.environ.get("GITHUB_OUTPUT")',
        'os.environ["RAILFORM_PRIVATE_NAMES"]',
        'os.environ["GITHUB_TOKEN"]',
        '"""Never reads ANTHROPIC_API_KEY or imports anthropic; this is prose."""',
    ],
)
def test_the_scanner_accepts_ordinary_code(source: str) -> None:
    assert findings_in(source) == []


def test_no_module_under_src_has_a_model_client() -> None:
    modules = sorted(SRC.rglob('*.py'))
    assert modules, 'the scan found no module, so it proves nothing'
    problems = {
        str(path.relative_to(ROOT)).replace('\\', '/'): found
        for path in modules
        if (found := findings_in(path.read_text(encoding='utf-8')))
    }
    assert problems == {}


def test_the_only_runtime_dependency_is_pyyaml() -> None:
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    names = {
        re.split(r'[<>=!~\[; ]', requirement, maxsplit=1)[0].lower()
        for requirement in project['dependencies']
    }
    assert names == {'pyyaml'}
