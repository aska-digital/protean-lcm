"""Shared helpers for the Lane Gate suite.

Naming follows the locked architecture lock §5 (acceptance tests) and §3.0 (the
owned file set).  Nothing here talks to a network, a provider, or the user's
Hermes home: every test runs against tmp paths and fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from plugins.lane_gate import manifest

#: The absolute root the fixtures are written against.  No test reads the
#: filesystem at these paths -- they are glob-matching inputs only.
LANE_ROOT = "/lane"

OWNED_WRITE_GLOBS: List[str] = [
    f"{LANE_ROOT}/plugins/lane_gate/*",
    f"{LANE_ROOT}/tests/lane_gate/*",
    f"{LANE_ROOT}/pyproject.toml",
    f"{LANE_ROOT}/PROVENANCE-LANE-GATE.md",
]
FORBIDDEN_GLOBS: List[str] = [f"{LANE_ROOT}/protean-team/*", f"{LANE_ROOT}/LICENSE"]
FORBIDDEN_TOOLS: List[str] = ["delegate_task", "kanban_create"]

ENV_VARS = ("PROTEAN_LANE_MANIFEST", "PROTEAN_LANE_RECEIPTS", "PROTEAN_LANE_RECEIPT_DETAIL")

#: Base commit of the slice (lock §6).  Linear feature branches diff against it.
BASE_COMMIT = "9d3c4cb8dd7c58670ea9525b08391390ca8c0521"


def ownership_diff_range(
    parent_line: str,
    *,
    base: str = BASE_COMMIT,
    upstream: Optional[str] = None,
) -> str:
    """Return the range containing this lane's changes.

    A merge commit's first-parent delta is the lane-owned boundary.  A linear
    PR branch uses the live default-branch ref when available, so unrelated
    files that were already on main are not re-counted.  The locked feature
    base remains the fallback for an isolated feature checkout.
    """
    parents = parent_line.split()
    if len(parents) >= 3:
        return f"{parents[1]}..HEAD"
    if upstream:
        return f"{upstream}..HEAD"
    return f"{base}..HEAD"

#: Lock §3.0 owned file set, as fnmatch patterns over repository-relative paths.
ALLOWED_CHANGED_PATTERNS: List[str] = [
    "plugins/lane_gate",
    "plugins/lane_gate/__init__.py",
    "plugins/lane_gate/plugin.yaml",
    "plugins/lane_gate/manifest.py",
    "plugins/lane_gate/gate.py",
    "plugins/lane_gate/receipts.py",
    "plugins/lane_gate/config.py",
    "plugins/lane_gate/skills/lane-discipline/SKILL.md",
    "tests/lane_gate",
    "tests/lane_gate/*",
    "pyproject.toml",
    "PROVENANCE-LANE-GATE.md",
]

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

#: Repository root (the lane-gate plugin lives under ``plugins/``).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The plugin package directory.
PLUGIN_DIR = REPO_ROOT / "plugins" / "lane_gate"


def fixture_json(name: str) -> Dict[str, Any]:
    """Load one JSON fixture."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def valid_manifest_dict(**overrides: Any) -> Dict[str, Any]:
    """The valid fixture manifest, with optional field overrides."""
    data = fixture_json("lane-manifest.valid.json")
    data.update(overrides)
    return data


def manifest_of(data: Dict[str, Any], *, source: str = "fixture") -> manifest.LaneManifest:
    return manifest.LaneManifest.from_obj(data, source=source)


def valid_manifest(**overrides: Any) -> manifest.LaneManifest:
    return manifest_of(valid_manifest_dict(**overrides))


def binding_of(value: Any, *, status: str = "ok") -> manifest.ManifestBinding:
    """Wrap a manifest (or raw dict) in a binding as the resolver would."""
    if isinstance(value, dict):
        value = manifest_of(value)
    return manifest.ManifestBinding(status=status, sha256=value.sha256, manifest=value)


def unbound() -> manifest.ManifestBinding:
    return manifest.ManifestBinding.absent()


def allowed_change_paths() -> List[str]:
    return list(ALLOWED_CHANGED_PATTERNS)


def is_allowed_change(path: str) -> bool:
    import fnmatch

    return any(fnmatch.fnmatchcase(path, pattern) for pattern in ALLOWED_CHANGED_PATTERNS)


#: Tool-run artifacts, matched on their first path component.  These are never
#: part of a commit (the repository's own ``.gitignore`` covers most of them);
#: the list exists so a pytest run inside the working tree cannot masquerade as a
#: source change.  It is deliberately exact: no entry may overlap the owned set.
GENERATED_ARTIFACTS = (
    ".pytest_cache",
    ".pytest-cache",
    "__pycache__",
    "build",
    "dist",
    ".mypy_cache",
    ".ruff_cache",
)


def is_generated_path(path: str) -> bool:
    """Whether *path* is a tool-run artifact rather than a source change."""
    head = path.split("/", 1)[0]
    if head in GENERATED_ARTIFACTS:
        return True
    return head.endswith(".egg-info")


def generated_artifacts_are_disjoint_from_owned() -> bool:
    """No generated-artifact name may also be an owned source path."""
    return not any(is_allowed_change(name) for name in GENERATED_ARTIFACTS)


class FakeCtx:
    """A minimal plugin context: the host's registration surface, nothing else.

    ``_hooks`` mirrors the shape of the real plugin manager's hook registry so the
    gate's re-registration guard can be exercised without a Hermes checkout.
    ``with_hook_api=False`` models a host whose context object has no
    ``register_hook`` at all.
    """

    def __init__(self, *, with_hook_api: bool = True) -> None:
        self._hooks: Dict[str, List[Any]] = {}
        self.skills: List[tuple] = []
        if with_hook_api:
            self.register_hook = self._register_hook

    def _register_hook(self, hook_name: str, callback: Any) -> None:
        self._hooks.setdefault(hook_name, []).append(callback)

    def register_skill(self, name: str, path: Any, description: str = "", frontmatter: Any = None) -> None:
        self.skills.append((name, path, description))

    @property
    def pre_tool_call_hooks(self) -> List[Any]:
        return list(self._hooks.get("pre_tool_call") or [])

    def disable(self) -> None:
        """Model ``hermes plugins disable``: the host drops our callbacks."""
        self._hooks.clear()

    def invoke_pre_tool_call(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        for callback in self.pre_tool_call_hooks:
            directive = callback(**kwargs)
            if directive:
                return directive
        return None


def describe_directive(directive: Any) -> str:
    if directive is None:
        return "proceed"
    return f"{directive.get('action')}: {str(directive.get('message'))[:90]}"


def flatten(items: Iterable[Any]) -> List[Any]:
    out: List[Any] = []
    for item in items:
        out.extend(item if isinstance(item, (list, tuple)) else [item])
    return out
