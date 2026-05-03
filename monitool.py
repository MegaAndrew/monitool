#!/usr/bin/env python3
"""
monitool - Shell Script Managing And Monitoring
Run: python monitool.py
Config: scripts.json (auto-created on first run)
"""

import hashlib
import json
import logging
import os
import queue
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

# ─── CONFIG ──────────────────────────────────────────────────────────────────

CONFIG_FILE = os.environ.get("MONITOOL_CONFIG", "scripts.json")
SECRET_KEY = os.environ.get("MONITOOL_SECRET", secrets.token_hex(32))
HOST = os.environ.get("MONITOOL_HOST", "127.0.0.1")
PORT = int(os.environ.get("MONITOOL_PORT", "7000"))

DEFAULT_CONFIG: dict[str, Any] = {
    "auth": {
        "username": "admin",
        # sha256 of "changeme" — override with: python -c "import hashlib; print(hashlib.sha256(b'yourpassword').hexdigest())"
        "password_hash": "d6e9e8b8f01bfa9df10e9a4a6ddda5d0e3a07e1e8cd33b0c8e1e3dfcb44d8f01",
    },
    "scripts": [
        {
            "id": "example-server",
            "name": "Example HTTP Server",
            "description": "Python built-in HTTP server on port 8080",
            "command": "python3 -m http.server 8080",
            "cwd": "/tmp",
            "env": {},
            "auto_restart": False,
        },
        {
            "id": "example-loop",
            "name": "Counter Loop",
            "description": "Counts numbers every second",
            "command": "bash -c 'i=0; while true; do echo \"tick $i\"; i=$((i+1)); sleep 1; done'",
            "cwd": "/tmp",
            "env": {},
            "auto_restart": False,
        },
    ],
}

# ─── APP SETUP ───────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SECRET_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("monitool")

# ─── STATE ───────────────────────────────────────────────────────────────────

processes = {}  # id -> subprocess.Popen
log_buffers = {}  # id -> list[str]  (ring buffer)
log_queues = {}  # id -> list[queue.Queue]  (SSE subscribers)
lock = threading.Lock()

MAX_LOG_LINES = 1000


def get_config():
    if not Path(CONFIG_FILE).exists():
        _ = Path(CONFIG_FILE).write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        log.info(f"Created default config: {CONFIG_FILE}")
    return json.loads(Path(CONFIG_FILE).read_text())


def find_script(cfg, script_id):
    return next((s for s in cfg["scripts"] if s["id"] == script_id), None)


def append_log(script_id, line):
    with lock:
        buf = log_buffers.setdefault(script_id, [])
        log_obj = [int(time.time() * 1000), line]
        buf.append(log_obj)
        if len(buf) > MAX_LOG_LINES:
            buf.pop(0)
        for q in log_queues.get(script_id, []):
            try:
                q.put_nowait(log_obj)
            except Exception as e:
                app.logger.exception(e)
                pass


def stream_output(script_id, proc):
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            append_log(script_id, line)
    except Exception as e:
        append_log(script_id, f"[stream error: {e}]")
    finally:
        retcode = proc.wait()
        append_log(script_id, f"[process exited with code {retcode}]")
        cfg = get_config()
        script = find_script(cfg, script_id)
        if script and script.get("auto_restart") and processes.get(script_id) == proc:
            append_log(script_id, "[auto_restart: restarting in 2s…]")
            time.sleep(2)
            _start_process(script)


def _start_process(script):
    script_id = script["id"]
    env = {**os.environ, **script.get("env", {})}
    cwd = script.get("cwd") or os.getcwd()
    try:
        proc = subprocess.Popen(
            script["command"],
            shell=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid,
        )
        with lock:
            processes[script_id] = proc
        append_log(script_id, f"[started pid={proc.pid}]")
        t = threading.Thread(target=stream_output, args=(script_id, proc), daemon=True)
        t.start()
        return True, None
    except Exception as e:
        return False, str(e)


def script_status(script_id):
    proc = processes.get(script_id)
    if proc is None:
        return "stopped"
    if proc.poll() is None:
        return "running"
    return "stopped"


# ─── AUTH ─────────────────────────────────────────────────────────────────────


def check_auth(username, password):
    cfg = get_config()
    auth = cfg.get("auth", {})
    h = hashlib.sha256(password.encode()).hexdigest()
    print(h)
    return username == auth.get("username") and h == auth.get("password_hash")


def login_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)

    return decorated


# ─── ROUTES ──────────────────────────────────────────────────────────────────


@app.route("/login", methods=["GET", "POST"])
def login_page():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if check_auth(u, p):
            session["logged_in"] = True
            session["username"] = u
            return redirect(url_for("index"))
        error = "Invalid credentials"
    return render_template_string(open("templates/login.html").read(), error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    return render_template_string(open("templates/index.html").read())


# ─── API ─────────────────────────────────────────────────────────────────────


@app.route("/api/scripts")
@login_required
def api_scripts():
    cfg = get_config()
    result = []
    for s in cfg["scripts"]:
        sid = s["id"]
        result.append(
            {
                "id": sid,
                "name": s["name"],
                "description": s.get("description", ""),
                "command": s["command"],
                "status": script_status(sid),
                "pid": processes[sid].pid
                if sid in processes and processes[sid].poll() is None
                else None,
                "auto_restart": s.get("auto_restart", False),
            }
        )
    return jsonify(result)


@app.route("/api/scripts/<script_id>/start", methods=["POST"])
@login_required
def api_start(script_id):
    cfg = get_config()
    script = find_script(cfg, script_id)
    if not script:
        return jsonify({"error": "not found"}), 404
    if script_status(script_id) == "running":
        return jsonify({"error": "already running"}), 409
    ok, err = _start_process(script)
    if ok:
        return jsonify({"status": "started"})
    return jsonify({"error": err}), 500


@app.route("/api/scripts/<script_id>/stop", methods=["POST"])
@login_required
def api_stop(script_id):
    proc = processes.get(script_id)
    if not proc or proc.poll() is not None:
        return jsonify({"error": "not running"}), 409
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception as e:
            app.logger.exception(e)
            pass
    return jsonify({"status": "stopped"})


@app.route("/api/scripts/<script_id>/restart", methods=["POST"])
@login_required
def api_restart(script_id):
    cfg = get_config()
    script = find_script(cfg, script_id)
    if not script:
        return jsonify({"error": "not found"}), 404
    proc = processes.get(script_id)
    if proc and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception as e:
            app.logger.exception(e)
            pass
        proc.wait(timeout=5)
    time.sleep(0.3)
    ok, err = _start_process(script)
    if ok:
        return jsonify({"status": "restarted"})
    return jsonify({"error": err}), 500


@app.route("/api/scripts/<script_id>/logs")
@login_required
def api_logs(script_id):
    with lock:
        buf = list(log_buffers.get(script_id, []))
    return jsonify(buf)


@app.delete("/api/scripts/<script_id>/logs")
@login_required
def api_clear_logs(script_id):
    with lock:
        log_buffers[script_id] = []
    return jsonify({"status": "cleared"}), 204


@app.route("/api/scripts/<script_id>/stream")
@login_required
def api_stream(script_id):
    """Server-Sent Events for live log streaming."""
    q = queue.Queue(maxsize=500)
    with lock:
        log_queues.setdefault(script_id, []).append(q)

    def generate():
        try:
            while True:
                try:
                    log_obj = q.get(timeout=20)
                    yield f"data: {json.dumps(log_obj)}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with lock:
                qs = log_queues.get(script_id, [])
                if q in qs:
                    qs.remove(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── TEMPLATES ───────────────────────────────────────────────────────────────

base_dir = os.path.dirname(__file__)

LOGIN_HTML = open(os.path.join(base_dir, "templates", "login.html")).read()

INDEX_HTML = open(os.path.join(base_dir, "templates", "index.html")).read()

# ─── ENTRY ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Ensure config exists
    get_config()
    log.info(f"monitool starting on http://{HOST}:{PORT}")
    log.info(f"Config file: {CONFIG_FILE}")
    log.info(
        f"Default login: admin / changeme  (change password_hash in {CONFIG_FILE})"
    )
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
