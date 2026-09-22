"""Suite setup for the Resource Budget tests.

Three jobs:

* put the repository root on ``sys.path`` so ``plugins.resource_budget`` imports
  without an installed distribution (the layout mirrors the lane-gate suite,
  which imports ``plugins.lane_gate`` the same way);
* isolate the process-global callback state between tests (the entry point's
  hook/status-handler globals and the resolved settings holder);
* provide host-free doubles for the two host surfaces the slice touches: the
  config bridge (``ctx.get_config``) and the plugin-state bridge (``ctx.state``).

``HERMES_HOME`` and the host plugin-manager reset already come from
``tests/conftest.py``.  No Hermes checkout is needed: the slice is driven through
the doubles below, which record every ``register_*`` call and every state write.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
for _entry in (str(_REPO_ROOT), str(_HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from plugins.resource_budget import config as rb_config  # noqa: E402

import plugins.resource_budget as resource_budget  # noqa: E402

PLUGIN_DIR = _REPO_ROOT / "plugins" / "resource_budget"
RUNTIME_PATH = PLUGIN_DIR / "hxrb_runtime.py"
UPSTREAM_LICENSE_PATH = PLUGIN_DIR / "UPSTREAM-LICENSE.md"
PLUGIN_YAML_PATH = PLUGIN_DIR / "plugin.yaml"
PROVENANCE_PATH = _REPO_ROOT / "PROVENANCE-RESOURCE-BUDGET.md"
README_PATH = _REPO_ROOT / "README.md"
CHANGELOG_PATH = _REPO_ROOT / "CHANGELOG.md"

#: The slice's .py files: the three plugin modules and the five test modules.
SLICE_PYTHON_PATHS = (
    PLUGIN_DIR / "__init__.py",
    PLUGIN_DIR / "config.py",
    PLUGIN_DIR / "hxrb_runtime.py",
    _HERE / "conftest.py",
    _HERE / "test_resource_budget_config.py",
    _HERE / "test_resource_budget_gate.py",
    _HERE / "test_resource_budget_provenance.py",
    _HERE / "test_resource_budget_authority.py",
)

#: The slice-owned text files scanned for boundary language (W1): the 11 added
#: files plus the two additive document edits.  The two vendored files are
#: scanned too, but their occurrences are the pinned source's own wording.
SLICE_TEXT_PATHS = (
    RUNTIME_PATH,
    UPSTREAM_LICENSE_PATH,
    PLUGIN_YAML_PATH,
    PLUGIN_DIR / "__init__.py",
    PLUGIN_DIR / "config.py",
    PROVENANCE_PATH,
    README_PATH,
    CHANGELOG_PATH,
) + tuple(
    _HERE / name
    for name in (
        "conftest.py",
        "test_resource_budget_config.py",
        "test_resource_budget_gate.py",
        "test_resource_budget_provenance.py",
        "test_resource_budget_authority.py",
    )
)

# --------------------------------------------------------------------------- #
# Approved boundary language (locked §7, W1)
# --------------------------------------------------------------------------- #

#: S-a: the source's own Boundary sentence, with its markdown emphasis.
S_A = "It is **not a sandbox or complete host-resource security boundary**."

#: S-a without emphasis: the same sentence as it appears in a runtime message.
S_A_PLAIN = "It is not a sandbox or complete host-resource security boundary."

#: S-b: the vendored module docstring's sentence.
S_B = "This is intentionally a *policy plugin*, not a sandbox or process governor."

#: S-c: the classifier's declared limit.
S_C = (
    "It is intentionally not advertised as a complete shell AST, malware detector, "
    "PowerShell parser, or process sandbox."
)

#: S-d: the source's own block-message sentences (plain prose form).
S_D_PLAIN = (
    "This is a categorical best-effort terminal policy rule, not a measured "
    "transfer/runtime threshold or host sandbox. No command contents or "
    "credentials were persisted."
)

#: The literal line carrying the S-d middle inside a Python source file.
S_D_FILE_FRAGMENT = (
    '"transfer/runtime threshold or host sandbox. No command contents or "'
)

#: F: the false-flag enumeration (the status payload's own field names).
FALSE_FLAGS = (
    "sandbox",
    "hard_cpu_cap",
    "hard_memory_cap",
    "hard_gpu_cap",
    "hard_disk_io_cap",
    "hard_network_cap",
)

#: Contiguous passages that may carry boundary words.  An occurrence of a claim
#: word is approved only when it sits inside one of these exact passages (an F
#: field-name spelling in quoted/colon form, or a pinned test name that
#: enumerates them).  Deliberately strict: a stray occurrence fails the scan.
#: Note the quoted spellings: the bare word alone is NOT an approved passage.
APPROVED_PASSAGES = (
    S_A,
    S_A_PLAIN,
    S_B,
    S_C,
    S_D_FILE_FRAGMENT,
    '"PowerShell parser, or process sandbox."',
    # The vendored docstring's sentence, which wraps across two source lines.
    "not a complete shell parser or host security\nboundary",
    '"sandbox"',
    'r"sandbox"',
) + tuple(
    spelling
    for flag in FALSE_FLAGS
    for spelling in (f'"{flag}"', f"{flag}: false", f"{flag}=false")
) + (
    "test_status_payload_reports_no_sandbox_and_no_hard_caps",
    "test_only_approved_boundary_words",
    "test_boundary_language_is_verbatim",
)

_BOUNDARY_PATTERNS = (
    re.compile(r"sandbox", re.I),
    re.compile(r"security[ \t-]*\n?[ \t-]*boundary", re.I),
    re.compile(r"hard[ _a-z]*cap", re.I),
)


def boundary_occurrences(text: str):
    """Yield ``(offset, matched_text)`` for every boundary-word hit in *text*."""
    for pattern in _BOUNDARY_PATTERNS:
        for match in pattern.finditer(text):
            yield match.start(), match.group(0)


def unapproved_boundary_occurrences(text: str):
    """Boundary-word occurrences that do not sit inside an approved passage."""
    approved = []
    for passage in APPROVED_PASSAGES:
        start = text.find(passage)
        while start != -1:
            approved.append((start, start + len(passage)))
            start = text.find(passage, start + 1)
    return [
        (offset, word)
        for offset, word in boundary_occurrences(text)
        if not any(low <= offset and offset + len(word) <= high for low, high in approved)
    ]




class FakeState:
    """Records every ``get``/``set`` on the host plugin-state bridge."""

    def __init__(self, initial: Optional[Dict[str, Any]] = None) -> None:
        self._values: Dict[str, Any] = dict(initial or {})
        self.set_calls: List[Any] = []
        self.get_calls: List[Any] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.get_calls.append((key, default))
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.set_calls.append((key, value))
        self._values[key] = value

    @property
    def keys_written(self) -> List[str]:
        return [key for key, _value in self.set_calls]


class FakeCtx:
    """Minimal host context: config bridge, state bridge, and registries.

    Every ``register_*`` call is recorded, and the two registry shapes the host
    exposes (``_hooks`` / ``_tools``) are filled the way the host fills them, so
    re-registration can be shown to be idempotent.
    """

    def __init__(
        self,
        settings: Optional[Dict[str, Any]] = None,
        profile_name: str = "probe",
    ) -> None:
        self.settings: Dict[str, Any] = dict(settings or {})
        self.profile_name = profile_name
        self.state = FakeState()
        self._hooks: Dict[str, List[Any]] = {}
        self._tools: Dict[str, Any] = {}
        self.hook_calls: List[Any] = []
        self.tool_calls: List[Any] = []
        self.skill_calls: List[Any] = []

    # -- config bridge ------------------------------------------------------ #
    def get_config(self, key: str, default: Any = None, **_kwargs: Any) -> Any:
        return self.settings.get(key, default)

    # -- registration surface ---------------------------------------------- #
    def register_hook(self, name: str, callback: Any) -> None:
        self.hook_calls.append((name, callback))
        self._hooks.setdefault(name, []).append(callback)

    def register_tool(self, **kwargs: Any) -> None:
        self.tool_calls.append(kwargs)
        self._tools[kwargs.get("name")] = kwargs

    def register_skill(self, *args: Any, **kwargs: Any) -> None:
        self.skill_calls.append((args, kwargs))

    # -- helpers ------------------------------------------------------------ #
    @property
    def hook_names(self) -> List[str]:
        return [name for name, _callback in self.hook_calls]

    @property
    def tool_names(self) -> List[str]:
        return [call.get("name") for call in self.tool_calls]

    def hook(self):
        """The single registered ``pre_tool_call`` callback."""
        assert self.hook_calls, "no pre_tool_call hook was registered"
        return self.hook_calls[0][1]

    def status_json(self) -> Dict[str, Any]:
        """The status tool's payload, decoded."""
        assert self.tool_calls, "no status tool was registered"
        handler = self.tool_calls[0]["handler"]
        return json.loads(handler())


@pytest.fixture(autouse=True)
def isolated_resource_budget_state(monkeypatch):
    """Reset the entry point's globals and clear the unattended marker."""
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    resource_budget._reset_for_tests()
    yield
    resource_budget._reset_for_tests()


@pytest.fixture
def make_ctx():
    """Build a recording fake context with a given settings block."""

    def _make(settings: Optional[Dict[str, Any]] = None, **kwargs: Any) -> FakeCtx:
        return FakeCtx(settings, **kwargs)

    return _make


@pytest.fixture
def patch_settings(make_ctx):
    """Pin the plugin's resolved settings for one test.

    The settings block on the fake context is the plugin's only configuration
    source, so ``apply`` returns a context whose block can be mutated in place:
    the per-call resolution tests rely on exactly that.
    """

    def _apply(settings: Optional[Dict[str, Any]] = None, **values: Any) -> FakeCtx:
        merged: Dict[str, Any] = dict(settings or {})
        merged.update(values)
        return make_ctx(merged)

    return _apply


@pytest.fixture
def enabled_ctx(make_ctx):
    """A context with the slice enabled in ``block_all``."""

    def _make(settings: Optional[Dict[str, Any]] = None, **kwargs: Any) -> FakeCtx:
        merged: Dict[str, Any] = {"mode": "block_all"}
        merged.update(settings or {})
        ctx = make_ctx(merged, **kwargs)
        resource_budget.register(ctx)
        return ctx

    return _make
