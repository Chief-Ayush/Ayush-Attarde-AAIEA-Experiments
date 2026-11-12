"""Helpers shared by the MCP servers: risk annotations, tagged errors, blocking-work offloading and fault injection for tests."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import anyio
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ...config import PATHS
from ...errors import InputError, SecurityError, ToolFailure

_LOCK = threading.Lock()

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITES_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SENDS = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


def risk_meta(level: str) -> dict:
    return {"risk": level}


def tagged(category: str, message: str) -> ToolError:
    """Errors carry a [CATEGORY] prefix so the client can classify them (input / security / timeout / tool / execution)."""
    return ToolError(f"[{category}] {message}")


def consume_fault(tool: str) -> None:
    """Fault injection for the failure-handling tests: storage/mcp_logs/faults.json maps tool -> {kind, count, delay_s}."""
    f_path = PATHS.faults
    if not f_path.exists():
        return
    with _LOCK:
        try:
            faults = json.loads(f_path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            return
        f = faults.get(tool)
        if not f or f.get("count", 0) <= 0:
            return
        f["count"] -= 1
        try:
            f_path.write_text(json.dumps(faults), encoding="utf-8")
        except OSError:
            pass
    kind = f.get("kind")
    if kind == "slow":
        time.sleep(float(f.get("delay_s", 2.0)))
    elif kind == "transient":
        raise tagged("EXECUTION", f"{tool}: transient failure (injected)")
    elif kind == "tool_error":
        raise tagged("TOOL", f"{tool}: tool returned an error result (injected)")
    elif kind == "crash":
        sys.stderr.write(f"{tool}: server crash (injected)\n")
        sys.stderr.flush()
        os._exit(17)


async def run_blocking(tool: str, fn, *args, **kwargs):
    """Run blocking work in a worker thread; map our exceptions to tagged errors."""
    def call():
        consume_fault(tool)
        return fn(*args, **kwargs)
    try:
        return await anyio.to_thread.run_sync(call)
    except ToolError:
        raise
    except SecurityError as exc:
        raise tagged("SECURITY", str(exc)) from exc
    except ToolFailure as exc:
        raise tagged("TOOL", str(exc)) from exc
    except (InputError, FileNotFoundError, KeyError, ValueError) as exc:
        raise tagged("INPUT", str(exc).strip("'\"")) from exc
    except TimeoutError as exc:
        raise tagged("TIMEOUT", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise tagged("EXECUTION", f"{type(exc).__name__}: {exc}") from exc


def safe_input_path(path: str):
    """Only files inside the project folder may be read (path traversal -> SecurityError)."""
    from pathlib import Path
    p = Path(path)
    p = (PATHS.root / p) if not p.is_absolute() else p
    rp = p.resolve()
    if PATHS.root.resolve() not in rp.parents and rp != PATHS.root.resolve():
        raise SecurityError("Path is outside the project folder")
    return rp
