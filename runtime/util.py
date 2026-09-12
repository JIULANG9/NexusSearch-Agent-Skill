"""Shared utilities: config loading, atomic JSON IO, ids, logging, validation."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = SKILL_ROOT / "config"
SCHEMA_DIR = SKILL_ROOT / "schemas"

try:  # optional dependency; a plain-dict fallback keeps the runtime usable
    import yaml  # type: ignore

    _HAS_YAML = True
except Exception:  # pragma: no cover - exercised only on stripped environments
    _HAS_YAML = False

_REDACT_KEYS = re.compile(
    r"(?i)(token|secret|password|passwd|api[_-]?key|authorization|cookie|credential)"
)


# ---------------------------------------------------------------------------
# Private, machine-local settings (secrets live OUTSIDE the repository).
# ---------------------------------------------------------------------------
LOCAL_ENV_PATH = Path(os.environ.get("NEXUS_LOCAL_ENV", str(Path.home() / ".config" / "nexus" / "local.env")))


def load_local_env(path: Path | None = None) -> int:
    """Populate unset environment variables from a private ``KEY=VALUE`` file.

    The repository only ever references ``${GITHUB_PERSONAL_ACCESS_TOKEN}`` and
    ``${NEXUS_PG_URL}`` placeholders; the real values live in
    ``~/.config/nexus/local.env`` (chmod 600) so they are never committed, never
    packaged into the installed skill and never echoed into a report. Values
    already present in the environment win, so an explicit shell export still
    overrides the file. Returns the number of variables added.
    """
    target = path or LOCAL_ENV_PATH
    try:
        if not target.is_file():
            return 0
        added = 0
        for line in target.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, _, value = text.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
                added += 1
        return added
    except Exception:  # noqa: BLE001 - a missing/unreadable private file is never fatal
        return 0


_loaded_local_env = load_local_env()


def now_iso() -> str:
    """Current UTC timestamp in second-precision ISO-8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    """Short, sortable, collision-resistant identifier."""
    return f"{prefix}_{int(time.time()):x}_{uuid.uuid4().hex[:6]}"


ASCII_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{2,}")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]")

# Words that are real in the corpus but useless as search terms.
GENERIC_SEARCH_TERMS = {
    "agent", "agents", "agentic", "ai", "app", "apps", "example", "examples", "demo",
    "project", "projects", "doc", "docs", "src", "test", "tests", "code", "version",
    "api", "sdk", "tool", "tools", "new", "use", "using", "used", "user", "users",
    "the", "and", "for", "with", "how", "what", "why", "is", "are", "to", "in", "of",
    "or", "an", "a", "vs", "one", "two", "based", "this", "that", "it", "its",
    # Auxiliaries that leak out of mixed-language spec bullets ("Depth Model must
    # output ...") and turn a term query into a sentence no engine can rank.
    "must", "should", "could", "would", "might", "shall", "will", "can", "may",
    "need", "needs", "want", "wants", "out", "into", "onto", "upon", "make",
    "makes", "made", "give", "gives", "take", "takes", "lets", "allows",
}

# "Q2"/"Q7" are spec bullet labels, not search terms.
SECTION_LABEL = re.compile(r"q\d+[a-z]?")


def is_cjk_heavy(text: str) -> bool:
    """True when the text is mostly Chinese, i.e. unusable as-is for English engines."""
    text = text or ""
    return len(CJK_RUN.findall(text)) > max(4, len(re.findall(r"[A-Za-z]", text)))


def ascii_terms(text: str, limit: int = 6, *, keep_generic: bool = False) -> list[str]:
    """Lowercased technical tokens from ``text`` that English search engines accept.

    A Chinese research question reaches a pinned English engine (github,
    stackoverflow, microsoft learn, ...) as noise, so planning and gap-closing
    queries are built from these terms instead of from the prose.
    """
    seen: list[str] = []
    for token in ASCII_TERM.findall((text or "").lower()):
        if not keep_generic and (token in GENERIC_SEARCH_TERMS or SECTION_LABEL.fullmatch(token)):
            continue
        if token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def rotate_pick(pool: "Sequence[str]", angle: int, count: int = 3) -> list[str]:
    """Take ``count`` terms starting at ``angle`` so sibling questions differ."""
    if not pool:
        return []
    size = len(pool)
    return [pool[(angle + index) % size] for index in range(min(count, size))]


def short(text: str, limit: int = 60) -> str:
    """One-line, length-bounded rendering for tables, bullets and gate actions."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "\u2026"


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "topic"


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping, falling back to a minimal parser if PyYAML is absent."""
    text = Path(path).read_text(encoding="utf-8")
    if _HAS_YAML:
        return yaml.safe_load(text) or {}
    return _mini_yaml(text)


def _mini_yaml(text: str) -> dict[str, Any]:
    """Very small indentation-based YAML subset reader (mappings + scalars)."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        key, _, value = line.strip().partition(":")
        value = value.strip().strip("'\"")
        if not value:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _coerce_scalar(value)
    return root


def _coerce_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~", ""}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def load_config(name: str) -> dict[str, Any]:
    """Load ``config/<name>.yaml`` from the skill directory."""
    return load_yaml(CONFIG_DIR / f"{name}.yaml")


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` when the file is missing."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        if default is not None:
            return default
        raise


def write_json(path: Path, payload: Any) -> Path:
    """Atomically write pretty-printed JSON so a crash cannot truncate state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)
    return path


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one audit record, redacting anything that looks like a credential."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(redact(record), ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL audit log; malformed lines are skipped."""
    out: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def redact(value: Any) -> Any:
    """Recursively mask secret-looking values before they reach the logs."""
    if isinstance(value, dict):
        return {
            key: ("***" if _REDACT_KEYS.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?i)(github_pat_|ghp_|sk-)[A-Za-z0-9_\-]{6,}", r"\1***", value)
    return value


class NexusError(Exception):
    """A runtime failure carrying a machine-readable error class."""

    def __init__(self, message: str, error_class: str = "unknown") -> None:
        super().__init__(message)
        self.error_class = error_class


_TIMEOUT_HINTS = ("timeout", "timed out", "deadline exceeded", "temporarily unavailable")
_REFUSED_HINTS = ("connection refused", "econnrefused", "connection reset", "unreachable")
_UNAVAILABLE_HINTS = ("no such", "does not exist", "not found", "unknown server")
# "unauthorized" is deliberately *not* a permission hint: MCP servers answer a
# missing or revoked token with 401 Unauthorized, which must route to auth_missing
# (skip + tell the user) rather than permission (silently mark the server down).
_PERMISSION_HINTS = ("permission", "forbidden", "eacces", "eperm", "denied")
_RATE_HINTS = ("rate limit", "too many requests", "429")
_AUTH_HINTS = ("api key", "unauthorized", "unauthenticated", "not authorized", "credential", "token", "auth_missing")


def classify_error(message: str) -> str:
    """Map a raw error string onto the taxonomy in ``config/mcp-map.yaml``."""
    text = (message or "").lower()
    if any(hint in text for hint in _RATE_HINTS):
        return "rate_limited"
    if any(hint in text for hint in _TIMEOUT_HINTS):
        return "timeout"
    if any(hint in text for hint in _REFUSED_HINTS):
        return "unavailable"
    if any(hint in text for hint in _AUTH_HINTS):
        return "auth_missing"
    if any(hint in text for hint in _PERMISSION_HINTS):
        return "permission"
    if "invalid" in text or "schema" in text or "required" in text:
        return "invalid_input"
    if any(hint in text for hint in _UNAVAILABLE_HINTS):
        return "not_found"
    return "unknown"


def get_logger(name: str = "nexus", verbose: bool = False):
    """Return a stderr logger; keeps stdout clean for JSON payloads."""
    import logging

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel("DEBUG" if verbose else "INFO")
    logger.propagate = False
    return logger


def log_event(logger, event: str, **fields: Any) -> None:
    """Structured logging with a consistent ``event key=value`` shape."""
    if logger is None:
        return
    detail = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    logger.info("%s %s", event, detail)


def validate(instance: Any, schema_name: str) -> tuple[bool, str]:
    """Validate against ``schemas/<schema_name>.schema.json``.

    Uses ``jsonschema`` when present. Without it we degrade to a structural
    check (required keys + object type) so the skill still guards contracts.
    """
    schema = read_json(SCHEMA_DIR / f"{schema_name}.schema.json")
    if schema is None:
        return True, f"no schema named {schema_name}"
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return _structural_validate(instance, schema, schema)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.path) or "<root>"
        return False, f"{location}: {first.message}"
    return True, "ok"


def _structural_validate(instance: Any, node: dict, root: dict) -> tuple[bool, str]:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        node = root["$defs"][ref.rsplit("/", 1)[-1]]
    expected = node.get("type")
    if expected == "object" and not isinstance(instance, dict):
        return False, f"expected object, got {type(instance).__name__}"
    if isinstance(instance, dict):
        for required in node.get("required", []):
            if required not in instance:
                return False, f"missing required key: {required}"
    return True, "ok"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` if needed and return it."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return Path(path)


def resolve_env(value: str) -> str:
    """Expand ``${VAR}`` and ``${VAR:-default}`` references from the environment."""
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

    def _sub(match: "re.Match[str]") -> str:
        name, fallback = match.group(1), match.group(2) or ""
        return os.environ.get(name, fallback)

    return pattern.sub(_sub, value)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Constrain ``value`` to the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))
