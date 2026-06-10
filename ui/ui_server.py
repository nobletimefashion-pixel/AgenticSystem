# ui/ui_server.py
"""
Nexus Agent — Web UI Server  (full-featured edition)
══════════════════════════════════════════════════════
Handles:  chat · tool streaming · confirmations · sessions · checkpoints
          hooks · MCP server management · loop-detector events · /commands
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import mimetypes
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

# ── locate project root ───────────────────────────────────────────────────────
_HERE = Path(__file__).parent.resolve()
_ROOT = _HERE
for _c in [_HERE, _HERE.parent, _HERE.parent.parent]:
    if (_c / "Agent").is_dir() and (_c / "Tools").is_dir():
        _ROOT = _c
        break
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from Agent.agent import Agent
from Agent.events import AgentEventType
from Agent.persistence import PersistenceManager, SessionSnapshot
from Agent.session import Session
from config.config import ApprovalPolicy, Config
from config.loader import load_config
from Tools.base import ToolConfirmation

# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Nexus Agent UI", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_UI_HTML = Path(__file__).parent.parent / "docs" / "index.html"

# ─────────────────────────────────────────────────────────────────────────────
# STATIC ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    if _UI_HTML.exists():
        return HTMLResponse(_UI_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>nexus_ui.html not found next to ui_server.py</h1>")


# ─────────────────────────────────────────────────────────────────────────────
# TOKEN HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _token_ok(token: str) -> bool:
    req = os.environ.get("UI_SECRET", "")
    return not req or token == req


# ─────────────────────────────────────────────────────────────────────────────
# FILE BROWSER + DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/files")
async def list_files(path: str = Query("."), token: str = Query(""), cwd: str = Query(None)):
    if not _token_ok(token):
        raise HTTPException(401, "Unauthorized")
    base   = Path(cwd).resolve() if cwd else Path.cwd()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(403, "Access denied")
    if not target.exists():
        return {"error": f"Not found: {path}", "items": []}
    items = []
    if target.is_file():
        items.append({"name": target.name, "path": str(target.relative_to(base)),
                      "type": "file", "size": target.stat().st_size})
    else:
        for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
            try:
                items.append({"name": item.name, "path": str(item.relative_to(base)),
                               "type": "file" if item.is_file() else "dir",
                               "size": item.stat().st_size if item.is_file() else None})
            except Exception:
                pass
    return {"cwd": str(base), "path": str(target.relative_to(base)), "items": items}


@app.get("/download")
async def download_file(path: str = Query(...), token: str = Query(""), cwd: str = Query(None)):
    if not _token_ok(token):
        raise HTTPException(401, "Unauthorized")
    base   = Path(cwd).resolve() if cwd else Path.cwd()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(403, "Access denied")
    if not target.exists():
        raise HTTPException(404, f"Not found: {path}")
    if target.is_file():
        mt = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(str(target), filename=target.name, media_type=mt)

    def _gen():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in target.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(target))
        buf.seek(0)
        yield buf.read()

    return StreamingResponse(_gen(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{target.name}.zip"'})


# ─────────────────────────────────────────────────────────────────────────────
# SESSIONS REST (for sidebar listing)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/sessions")
async def get_sessions(token: str = Query("")):
    if not _token_ok(token):
        raise HTTPException(401, "Unauthorized")
    pm = PersistenceManager()
    return {"sessions": pm.list_sessions()}


# ─────────────────────────────────────────────────────────────────────────────
# PER-CONNECTION STATE
# ─────────────────────────────────────────────────────────────────────────────

class Connection:
    def __init__(self, ws: WebSocket):
        self.ws           = ws
        self.config       = load_config(cwd=Path.cwd())
        self._confirm_evt = asyncio.Event()
        self._confirm_ans = True
        self._interrupt   = False

    # outbound ----------------------------------------------------------------

    async def send(self, msg: dict):
        try:
            await self.ws.send_text(json.dumps(msg, default=str))
        except Exception:
            logger.exception("Failed to send WebSocket message")

    # confirmation bridge -----------------------------------------------------

    async def ask_confirm(self, conf: ToolConfirmation) -> bool:
        self._confirm_evt.clear()
        await self.send({
            "type":        "confirm_request",
            "tool_name":   conf.tool_name,
            "description": conf.description,
            "command":     conf.command,
            "diff":        conf.diff.create_diff() if conf.diff else None,
            "is_dangerous": conf.is_dangerous,
        })
        await self._confirm_evt.wait()
        return self._confirm_ans

    # system info -------------------------------------------------------------

    async def send_system_info(self, agent: Agent | None = None):
        try:
            from Tools.builtin import get_all_builtin_tools
            tool_names = [t.name for t in get_all_builtin_tools()]
        except Exception:
            logger.exception("Failed to get builtin tools")
            tool_names = []

        mcp_servers: list[dict] = []
        if agent and agent.session:
            try:
                mcp_servers = agent.session.mcp_manager.get_all_servers()
            except Exception:
                logger.exception("Failed to get MCP servers")

        # Sessions list for sidebar
        pm = PersistenceManager()
        sessions = pm.list_sessions()

        # Hooks list
        hooks = []
        if self.config.hooks_enabled:
            hooks = [
                {"name": h.name, "trigger": h.trigger.value, "enabled": h.enabled}
                for h in self.config.hooks
            ]

        await self.send({
            "type":        "system_info",
            "cwd":         str(self.config.cwd),
            "model":       self.config.model_name,
            "tools":       tool_names,
            "mcp_servers": mcp_servers,
            "sessions":    sessions,
            "hooks":       hooks,
            "approval":    self.config.approval.value,
            "max_turns":   self.config.max_turns,
        })

    # run agent ---------------------------------------------------------------

    async def run_message(self, content: str, agent: Agent):
        self._interrupt = False
        async for event in agent.run(content):
            if self._interrupt:
                break
            t = event.type

            if t == AgentEventType.TEXT_DELTA:
                await self.send({"type": "text_delta",
                                 "content": event.data.get("content", "")})

            elif t == AgentEventType.TEXT_COMPLETE:
                await self.send({"type": "text_complete",
                                 "content": event.data.get("content", "")})

            elif t == AgentEventType.TOOL_CALL_START:
                kind = None
                try:
                    tool = agent.session.tool_registry.get(event.data["name"])
                    if tool:
                        kind = tool.kind.value
                except Exception:
                    pass
                await self.send({
                    "type":    "tool_start",
                    "call_id": event.data.get("call_id", ""),
                    "name":    event.data.get("name", ""),
                    "kind":    kind,
                    "args":    event.data.get("arguments", {}),
                })

            elif t == AgentEventType.TOOL_CALL_COMPLETE:
                await self.send({
                    "type":      "tool_complete",
                    "call_id":   event.data.get("call_id", ""),
                    "name":      event.data.get("name", ""),
                    "success":   event.data.get("success", False),
                    "output":    (event.data.get("output", "") or "")[:4000],
                    "error":     event.data.get("error"),
                    "diff":      event.data.get("diff"),
                    "metadata":  event.data.get("metadata", {}),
                    "truncated": event.data.get("truncated", False),
                    "exit_code": event.data.get("exit_code"),
                })

            elif t == AgentEventType.LOOP_DETECTOR:
                await self.send({"type": "loop_detected",
                                 "content": event.data.get("content", "")})

            elif t == AgentEventType.AGENT_ERROR:
                await self.send({"type": "agent_error",
                                 "error": event.data.get("error", "Unknown error")})

            elif t == AgentEventType.AGENT_END:
                await self.send({"type": "agent_end"})


# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    # auth
    req_token = os.environ.get("UI_SECRET", "")
    if req_token and ws.query_params.get("token", "") != req_token:
        await ws.send_text(json.dumps({
            "type":  "agent_error",
            "error": "❌ Unauthorized — invalid or missing token. "
                     "Add ?token=YOUR_SECRET to the WebSocket URL in Settings."
        }))
        await ws.close(code=4001, reason="Unauthorized")
        return

    conn        = Connection(ws)
    agent_task: asyncio.Task | None = None
    # Persistent agent across messages within one WS connection
    agent_ctx: Agent | None = None

    async def _ensure_agent() -> Agent:
        nonlocal agent_ctx
        if agent_ctx is None:
            agent_ctx = Agent(conn.config, confirmation_callback=conn.ask_confirm)
            await agent_ctx.__aenter__()
        return agent_ctx

    async def _close_agent():
        nonlocal agent_ctx
        if agent_ctx:
            try:
                await agent_ctx.__aexit__(None, None, None)
            except Exception:
                logger.exception("Failed to close agent")
            agent_ctx = None

    await conn.send_system_info()

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            kind = msg.get("type")

            # ── chat ─────────────────────────────────────────────────────────
            if kind == "chat":
                content = msg.get("content", "").strip()
                if not content:
                    continue
                if agent_task and not agent_task.done():
                    agent_task.cancel()
                agent = await _ensure_agent()
                agent_task = asyncio.create_task(conn.run_message(content, agent))

            # ── confirm ───────────────────────────────────────────────────────
            elif kind == "confirm":
                conn._confirm_ans = bool(msg.get("approved", True))
                conn._confirm_evt.set()

            # ── interrupt ─────────────────────────────────────────────────────
            elif kind == "interrupt":
                conn._interrupt = True
                if agent_task and not agent_task.done():
                    agent_task.cancel()
                await conn.send({"type": "agent_end"})

            # ── set_model ─────────────────────────────────────────────────────
            elif kind == "set_model":
                model = msg.get("model", "").strip()
                if model:
                    conn.config.model_name = model
                    await _close_agent()     # force new agent with new model
                    await conn.send_system_info()

            # ── set_cwd ───────────────────────────────────────────────────────
            elif kind == "set_cwd":
                cwd = Path(msg.get("cwd", ".")).expanduser().resolve()
                if cwd.is_dir():
                    conn.config = load_config(cwd=cwd)
                    await _close_agent()
                    await conn.send_system_info()
                else:
                    await conn.send({"type": "agent_error",
                                     "error": f"Directory not found: {cwd}"})

            # ── set_approval ──────────────────────────────────────────────────
            elif kind == "set_approval":
                policy = msg.get("policy", "").strip()
                if policy:
                    try:
                        conn.config.approval = ApprovalPolicy(policy)
                        await _close_agent()
                        await conn.send_system_info()
                    except Exception as e:
                        await conn.send({"type": "agent_error",
                                         "error": f"Invalid approval policy: {e}"})

            # ── save_session ──────────────────────────────────────────────────
            elif kind == "save_session":
                try:
                    agent = await _ensure_agent()
                    pm = PersistenceManager()
                    snap = SessionSnapshot(
                        session_id  = agent.session.session_id,
                        created_at  = agent.session.created_at,
                        updated_at  = agent.session.updated_at,
                        turn_count  = agent.session.turn_count,
                        messages    = agent.session.context_manager.get_messages(),
                        total_usage = agent.session.context_manager.total_usage,
                    )
                    pm.save_session(snap)
                    await conn.send({"type": "session_saved",
                                     "session_id": snap.session_id})
                    await conn.send_system_info(agent)
                except Exception as e:
                    await conn.send({"type": "agent_error", "error": f"Save failed: {e}"})

            # ── load_session ──────────────────────────────────────────────────
            elif kind == "load_session":
                session_id = msg.get("session_id", "")
                try:
                    pm   = PersistenceManager()
                    snap = pm.load_session(session_id)
                    if not snap:
                        await conn.send({"type": "agent_error",
                                         "error": f"Session not found: {session_id}"})
                        continue
                    await _close_agent()
                    # Build a fresh session and replay messages
                    session = Session(config=conn.config)
                    await session.initialize()
                    session.session_id = snap.session_id
                    session.created_at = snap.created_at
                    session.updated_at = snap.updated_at
                    session.turn_count = snap.turn_count
                    session.context_manager.total_usage = snap.total_usage
                    session.replay_messages(snap.messages)
                    # Wrap in Agent shell
                    new_agent          = Agent(conn.config, confirmation_callback=conn.ask_confirm)
                    new_agent.session  = session
                    agent_ctx          = new_agent
                    await conn.send({"type": "session_loaded", "session_id": session_id})
                    await conn.send_system_info(new_agent)
                    # Send history to frontend
                    await conn.send({"type": "session_history",
                                     "messages": snap.messages})
                except Exception as e:
                    await conn.send({"type": "agent_error", "error": f"Load failed: {e}"})

            # ── checkpoint ────────────────────────────────────────────────────
            elif kind == "checkpoint":
                try:
                    agent = await _ensure_agent()
                    pm   = PersistenceManager()
                    snap = SessionSnapshot(
                        session_id  = agent.session.session_id,
                        created_at  = agent.session.created_at,
                        updated_at  = agent.session.updated_at,
                        turn_count  = agent.session.turn_count,
                        messages    = agent.session.context_manager.get_messages(),
                        total_usage = agent.session.context_manager.total_usage,
                    )
                    cp_id = pm.save_checkpoint(snap)
                    await conn.send({"type": "checkpoint_saved", "checkpoint_id": cp_id})
                except Exception as e:
                    await conn.send({"type": "agent_error", "error": f"Checkpoint failed: {e}"})

            # ── add_hook ──────────────────────────────────────────────────────
            elif kind == "add_hook":
                try:
                    from config.config import HookConfig, HookTrigger
                    hook = HookConfig(
                        name    = msg.get("name", "custom_hook"),
                        trigger = HookTrigger(msg.get("trigger", "after_tool")),
                        command = msg.get("command") or None,
                        script  = msg.get("script")  or None,
                        enabled = True,
                    )
                    conn.config.hooks.append(hook)
                    conn.config.hooks_enabled = True
                    await _close_agent()    # new agent picks up new hooks
                    await conn.send({"type": "hook_added", "name": hook.name})
                    await conn.send_system_info()
                except Exception as e:
                    await conn.send({"type": "agent_error", "error": f"Hook error: {e}"})

            # ── remove_hook ───────────────────────────────────────────────────
            elif kind == "remove_hook":
                name = msg.get("name", "")
                conn.config.hooks = [h for h in conn.config.hooks if h.name != name]
                if not conn.config.hooks:
                    conn.config.hooks_enabled = False
                await _close_agent()
                await conn.send({"type": "hook_removed", "name": name})
                await conn.send_system_info()

            # ── add_mcp ───────────────────────────────────────────────────────
            elif kind == "add_mcp":
                try:
                    from config.config import MCPServerConfig
                    server_name = msg.get("name", "").strip()
                    url         = msg.get("url",  "").strip() or None
                    command     = msg.get("command", "").strip() or None
                    args        = msg.get("args", [])

                    if not server_name:
                        await conn.send({"type": "agent_error", "error": "MCP server name required"})
                        continue

                    cfg = MCPServerConfig(url=url, command=command, args=args)
                    conn.config.mcp_server[server_name] = cfg
                    await _close_agent()     # reconnect with new MCP
                    agent = await _ensure_agent()
                    await conn.send({"type": "mcp_added", "name": server_name})
                    await conn.send_system_info(agent)
                except Exception as e:
                    await conn.send({"type": "agent_error", "error": f"MCP error: {e}"})

            # ── remove_mcp ────────────────────────────────────────────────────
            elif kind == "remove_mcp":
                name = msg.get("name", "")
                conn.config.mcp_server.pop(name, None)
                await _close_agent()
                agent = await _ensure_agent()
                await conn.send({"type": "mcp_removed", "name": name})
                await conn.send_system_info(agent)

            # ── get_stats ─────────────────────────────────────────────────────
            elif kind == "get_stats":
                try:
                    agent = await _ensure_agent()
                    stats = agent.session.get_stats()
                    await conn.send({"type": "stats", "data": stats})
                except Exception as e:
                    await conn.send({"type": "agent_error", "error": str(e)})

            # ── clear_context ─────────────────────────────────────────────────
            elif kind == "clear_context":
                try:
                    agent = await _ensure_agent()
                    agent.session.context_manager.clear()
                    agent.session.loop_detector_obj.clear()
                    await conn.send({"type": "context_cleared"})
                except Exception as e:
                    await conn.send({"type": "agent_error", "error": str(e)})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await conn.send({"type": "agent_error", "error": str(e)})
        except Exception:
            logger.exception("Failed to send error over WebSocket")
    finally:
        if agent_task and not agent_task.done():
            agent_task.cancel()
        await _close_agent()


# ─────────────────────────────────────────────────────────────────────────────
# LAUNCH
# ─────────────────────────────────────────────────────────────────────────────

def launch(host: str = "127.0.0.1", port: int = 7860, open_browser: bool = True):
    if open_browser:
        import threading, webbrowser
        def _open():
            import time; time.sleep(1.2)
            webbrowser.open(f"http://{host}:{port}")
        threading.Thread(target=_open, daemon=True).start()
    print(f"\n🌐  Nexus Agent UI  →  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Nexus Agent Web UI Server")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7860)))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    launch(args.host, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    _cli()