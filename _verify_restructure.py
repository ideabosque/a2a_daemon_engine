import ast
import pathlib
import sys
import importlib.util

root = pathlib.Path("a2a_daemon_engine")
files = list((root / "models").rglob("*.py")) + list((root / "utils").rglob("*.py"))

# 1) Syntax check every file
bad = []
for f in files:
    try:
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
    except SyntaxError as e:
        bad.append((str(f), str(e)))
print("SYNTAX_ISSUES=", len(bad))
for b in bad:
    print("  ", b)

# 2) Verify the relative-import fixes landed in the moved model files
checks = {
    "a2a_daemon_engine/models/dynamodb/a2a_agent.py": [
        "from ...handlers.config import Config",
        "from ...types.a2a_agent import",
        "from .cache import purge_entity_cascading_cache",
    ],
    "a2a_daemon_engine/models/dynamodb/a2a_task.py": [
        "from ...handlers.config import Config",
        "from ...types.a2a_task import",
        "from .cache import purge_entity_cascading_cache",
    ],
    "a2a_daemon_engine/models/dynamodb/a2a_message.py": [
        "from ...handlers.config import Config",
        "from ...types.a2a_message import",
        "from .cache import purge_entity_cascading_cache",
    ],
    "a2a_daemon_engine/models/dynamodb/a2a_setting.py": [
        "from ...handlers.config import Config",
        "from ...types.a2a_setting import",
        "from .cache import purge_entity_cascading_cache",
    ],
    "a2a_daemon_engine/models/dynamodb/cache.py": [
        "from ...handlers.config import Config",
    ],
}
imp_bad = []
for f, needles in checks.items():
    txt = pathlib.Path(f).read_text(encoding="utf-8")
    for n in needles:
        if n not in txt:
            imp_bad.append((f, n))
print("IMPORT_FIX_MISSING=", len(imp_bad))
for b in imp_bad:
    print("  ", b)

# 3) Confirm NO stale relative imports remain in moved files
stale_markers = [
    "from ..handlers.config import Config",
    "from ..types.a2a_",
    "from ..models.cache import",
]
stale_hits = []
for f in [p for p in files if "repositories" not in str(p)]:
    txt = f.read_text(encoding="utf-8")
    for m in stale_markers:
        if m in txt:
            stale_hits.append((str(f), m))
print("STALE_IMPORTS=", len(stale_hits))
for h in stale_hits:
    print("  ", h)

# 4) Confirm the new repo/utils infrastructure files all parse and expose expected symbols
expected = [
    "a2a_daemon_engine/models/repositories/__init__.py",
    "a2a_daemon_engine/models/repositories/base.py",
    "a2a_daemon_engine/models/repositories/dispatch.py",
    "a2a_daemon_engine/models/repositories/dynamodb/__init__.py",
    "a2a_daemon_engine/models/repositories/dynamodb/_base.py",
    "a2a_daemon_engine/models/repositories/dynamodb/a2a_agent_repo.py",
    "a2a_daemon_engine/models/repositories/dynamodb/a2a_task_repo.py",
    "a2a_daemon_engine/models/repositories/dynamodb/a2a_message_repo.py",
    "a2a_daemon_engine/models/repositories/dynamodb/a2a_setting_repo.py",
    "a2a_daemon_engine/models/repositories/postgresql/__init__.py",
    "a2a_daemon_engine/utils/__init__.py",
    "a2a_daemon_engine/utils/normalization.py",
    "a2a_daemon_engine/utils/exceptions.py",
]
missing = [f for f in expected if not pathlib.Path(f).exists()]
print("MISSING_INFRA_FILES=", len(missing))
for m in missing:
    print("  ", m)

# 5) AST-extract class names from the 4 repo files to confirm they wrap the right fns
for repo, klass, ent, rkey in [
    ("dynamodb/a2a_agent_repo.py", "A2AAgentRepository", "a2a_agent", "agent_id"),
    ("dynamodb/a2a_task_repo.py", "A2ATaskRepository", "a2a_task", "task_id"),
    ("dynamodb/a2a_message_repo.py", "A2AMessageRepository", "a2a_message", "message_id"),
    ("dynamodb/a2a_setting_repo.py", "A2ASettingRepository", "a2a_setting", "setting_id"),
]:
    p = pathlib.Path("a2a_daemon_engine/models/repositories") / repo
    tree = ast.parse(p.read_text(encoding="utf-8"))
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    txt = p.read_text(encoding="utf-8")
    ok = klass in classes and f'"{ent}"' in txt and rkey in txt
    print(f"REPO_OK {repo}: {ok} (classes={classes})")

ok = not bad and not imp_bad and not stale_hits and not missing
print("ALL_OK=", ok)
sys.exit(0 if ok else 1)