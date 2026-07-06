"""Runtime import-resolution check for the restructured a2a_daemon_engine.

Loads each new/modified module by file path so we exercise the real relative
imports WITHOUT triggering the stale top-level models/__init__.py (which still
points at the pre-move flat layout and is out of scope for this task).
"""
import importlib.util
import pathlib
import sys
import types


def _load_dotted(dotted_name: str, path: pathlib.Path):
    """Load a module from a file path and register it under dotted_name."""
    # Pre-create parent package entries in sys.modules so relative imports
    # inside the loaded module resolve correctly.
    parts = dotted_name.split(".")
    # Build the synthetic package chain a2a_daemon_engine.models.repositories...
    for i in range(1, len(parts)):
        pkg = ".".join(parts[:i])
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            m.__path__ = [str(pathlib.Path(*parts[:i]).resolve())]
            # mark as package
            sys.modules[pkg] = m
    spec = importlib.util.spec_from_file_location(
        dotted_name, str(path), submodule_search_locations=[]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


root = pathlib.Path("a2a_daemon_engine")

# base.py has no relative imports -> safe to load directly
base = _load_dotted(
    "a2a_daemon_engine.models.repositories.base", root / "models/repositories/base.py"
)
assert issubclass(base.EntityRepository, object)
assert issubclass(base.RepositoryError, Exception)
assert issubclass(base.EntityNotFoundError, base.RepositoryError)
assert issubclass(base.DependencyExistsError, base.RepositoryError)
print("OK base.py ->", [base.EntityRepository.__name__])

# utils/normalization.py (no relative imports)
norm = _load_dotted(
    "a2a_daemon_engine.utils.normalization", root / "utils/normalization.py"
)
assert callable(norm.normalize_to_json)
print("OK utils/normalization.py -> normalize_to_json")

# exceptions.py (no relative imports)
exc = _load_dotted(
    "a2a_daemon_engine.utils.exceptions", root / "utils/exceptions.py"
)
assert issubclass(exc.A2ADaemonError, Exception)
print("OK utils/exceptions.py ->", [exc.A2ADaemonError.__name__])

# _base.py imports ....utils.normalization (relative) -> confirms the 4-dot path
_b = _load_dotted(
    "a2a_daemon_engine.models.repositories.dynamodb._base",
    root / "models/repositories/dynamodb/_base.py",
)
assert callable(_b._normalize)
print("OK dynamodb/_base.py -> _normalize (resolves ....utils.normalization)")

# The 4 repo files import: from ..base import EntityRepository  AND  from ._base import _normalize  AND  from ...dynamodb import a2a_* as _fn_mod
# The last one pulls in the ACTUAL moved model file (a2a_agent.py etc.) which now
# uses ...handlers.config / ...types.* / .cache -> real import resolution test.
repo_specs = [
    ("a2a_agent_repo.py", "A2AAgentRepository", "a2a_agent"),
    ("a2a_task_repo.py", "A2ATaskRepository", "a2a_task"),
    ("a2a_message_repo.py", "A2AMessageRepository", "a2a_message"),
    ("a2a_setting_repo.py", "A2ASettingRepository", "a2a_setting"),
]
for fname, klass, ent in repo_specs:
    m = _load_dotted(
        f"a2a_daemon_engine.models.repositories.dynamodb.{fname[:-3]}",
        root / "models/repositories/dynamodb" / fname,
    )
    cls = getattr(m, klass)
    inst = cls()
    assert inst.entity_type == ent, f"{klass}.entity_type={inst.entity_type!r} expected {ent!r}"
    # all 7 wrapped methods present
    for meth in ["get", "count", "list", "insert_update", "delete", "get_type", "resolve_single"]:
        assert hasattr(inst, meth), f"{klass} missing {meth}"
    print(f"OK {fname} -> {klass}(entity_type={inst.entity_type}) 7 methods present")

print("ALL_RUNTIME_IMPORTS_OK")
sys.exit(0)