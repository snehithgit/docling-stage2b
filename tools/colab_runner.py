#!/usr/bin/env python3
"""
colab_runner.py - run the KoboldCpp Colab notebook behind a hidden browser and
expose only what you need: Start, Stop, Base URL, API key.

Setup (once):
    pip install playwright
    playwright install chromium          # only needed if you use --channel ""

First run for each Google account (visible browser, sign in, then press Enter):
    python colab_runner.py --email you@gmail.com --notebook <COLAB_URL> --login

Normal use (browser hidden, small UI opens on http://127.0.0.1:8765):
    python colab_runner.py --email you@gmail.com --notebook <COLAB_URL>

<COLAB_URL> examples:
    https://colab.research.google.com/drive/<FILE_ID>          (notebook saved in Drive)
    https://colab.research.google.com/github/<user>/<repo>/blob/main/<file>.ipynb

Each email gets its own persistent browser profile, so the account you pass in
--email is the one that runs the notebook.
"""
from __future__ import annotations

import argparse
import html
import json
import queue
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

# These only match the notebook's *output*, never its source code:
# the source has `"API key:", KCPP_API_KEY` (quote right after the colon).
URL_RE = re.compile(r"Tunnel:\s*(https://[a-z0-9-]+\.trycloudflare\.com)")
KEY_RE = re.compile(r"API key:\s+([A-Za-z0-9_-]{30,})")

GPU_ERRORS = (
    "Cannot connect to GPU backend",
    "cannot currently connect to a GPU",
)


# --------------------------------------------------------------------------- state
class State:
    def __init__(self, email: str):
        self._lock = threading.Lock()
        self._d = {
            "status": "idle",  # idle | starting | running | stopping | error
            "message": "Not running",
            "base_url": "",
            "url": "",
            "api_key": "",
            "email": email,
        }

    def set(self, **kw):
        with self._lock:
            self._d.update(kw)
            if "url" in kw:
                self._d["base_url"] = (kw["url"] + "/v1") if kw["url"] else ""
            msg = self._d["message"]
            status = self._d["status"]
        print(f"[{time.strftime('%H:%M:%S')}] {status}: {msg}", flush=True)

    def get(self) -> dict:
        with self._lock:
            return dict(self._d)


# --------------------------------------------------------------------------- browser worker
class Runner(threading.Thread):
    """Owns Playwright. All browser calls happen on this one thread."""

    def __init__(self, args, state: State):
        super().__init__(daemon=True)
        self.args = args
        self.state = state
        self.cmds: "queue.Queue[str]" = queue.Queue()
        self.stop_flag = threading.Event()
        self.pw = None
        self.ctx = None
        self.page = None

    # ---- thread loop
    def run(self):
        self.pw = sync_playwright().start()
        try:
            while True:
                try:
                    cmd = self.cmds.get_nowait()
                except queue.Empty:
                    self._idle()
                    continue
                if cmd == "quit":
                    if self.state.get()["status"] in ("starting", "running"):
                        self._stop()
                    break
                if cmd == "start":
                    self._start()
                elif cmd == "stop":
                    self._stop()
        finally:
            self._close()
            try:
                self.pw.stop()
            except Exception:
                pass

    def _idle(self):
        # keep Playwright's event loop pumping while the browser is open
        if self.page is not None:
            try:
                self.page.wait_for_timeout(500)
                return
            except Exception:
                pass
        time.sleep(0.3)

    # ---- browser helpers
    def _launch(self, headless: bool):
        safe = re.sub(r"[^A-Za-z0-9._@-]", "_", self.args.email)
        profile = Path(self.args.profile_dir).expanduser() / safe
        profile.mkdir(parents=True, exist_ok=True)
        kw = dict(
            user_data_dir=str(profile),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        if self.args.channel:
            kw["channel"] = self.args.channel
        self.ctx = self.pw.chromium.launch_persistent_context(**kw)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def _close(self):
        try:
            if self.ctx:
                self.ctx.close()
        except Exception:
            pass
        self.ctx = None
        self.page = None

    def _page_text(self) -> str:
        parts = []
        for f in self.page.frames:  # notebook outputs can live in child frames
            try:
                parts.append(f.inner_text("body", timeout=2000))
            except Exception:
                pass
        return "\n".join(parts)

    def _menu(self, menu: str, item_pattern: str):
        p = self.page
        btn = p.locator(f"#{menu.lower()}-menu-button")
        target = btn.first if btn.count() else p.get_by_text(menu, exact=True).first
        target.click(timeout=8000)
        p.get_by_text(re.compile(item_pattern)).first.click(timeout=8000)

    def _dismiss_dialogs(self):
        try:
            b = self.page.get_by_role("button", name="Run anyway")
            if b.count() and b.first.is_visible():
                b.first.click()
        except Exception:
            pass

    def _check_gpu_errors(self, text: str):
        for hint in GPU_ERRORS:
            if hint.lower() in text.lower():
                raise RuntimeError("Colab has no GPU available for this account right now (quota/limits).")

    # ---- commands
    def _start(self):
        if self.state.get()["status"] in ("starting", "running"):
            return
        self.state.set(status="starting", message="Opening browser...", url="", api_key="")
        try:
            if not self.ctx:
                self._launch(headless=not self.args.show)
            page = self.page
            page.goto(self.args.notebook, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            if "accounts.google.com" in page.url:
                raise RuntimeError(
                    f"Not signed in as {self.args.email}. Run once with --login, then try again."
                )

            self.state.set(message="Running all cells (model download can take a few minutes)...")
            try:
                self._menu("Runtime", r"^Run all\b")
            except Exception:
                page.keyboard.press("Control+F9")  # fallback shortcut

            deadline = time.time() + self.args.timeout
            while time.time() < deadline:
                if self.stop_flag.is_set():
                    return  # the queued "stop" command finishes the job
                self._dismiss_dialogs()
                text = self._page_text()
                self._check_gpu_errors(text)
                u, k = URL_RE.search(text), KEY_RE.search(text)
                if u and k:
                    self.state.set(
                        status="running",
                        message="Ready",
                        url=u.group(1),
                        api_key=k.group(1),
                    )
                    self._write_out()
                    return
                page.wait_for_timeout(2000)
            raise RuntimeError("Timed out waiting for the tunnel URL and API key.")
        except Exception as e:
            self.state.set(status="error", message=str(e))

    def _stop(self):
        self.state.set(status="stopping", message="Stopping runtime...")
        note = "Stopped"
        try:
            if self.page is not None and not self.page.is_closed():
                try:
                    self._menu("Runtime", r"^Disconnect and delete runtime")
                    self.page.get_by_role("button", name=re.compile(r"^Yes$")).click(timeout=6000)
                    self.page.wait_for_timeout(2500)
                except Exception:
                    note = "Browser closed, but Colab runtime may not have been deleted (it will idle out)"
        finally:
            self._close()
            self.state.set(status="idle", message=note, url="", api_key="")
            self._write_out(clear=True)

    def _write_out(self, clear: bool = False):
        if not self.args.out:
            return
        d = self.state.get()
        payload = {"base_url": "", "api_key": ""} if clear else {
            "base_url": d["base_url"],
            "api_key": d["api_key"],
        }
        try:
            Path(self.args.out).expanduser().write_text(json.dumps(payload, indent=2))
        except Exception as e:
            print(f"Could not write {self.args.out}: {e}", file=sys.stderr)


# --------------------------------------------------------------------------- web UI
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KoboldCpp Colab</title>
<style>
  :root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--mut:#6b6b6b;--bd:#d8d8d8;--ac:#2f6feb}
  @media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eee;--mut:#9a9a9a;--bd:#333}}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
  main{max-width:520px;margin:8vh auto;padding:0 20px}
  h1{font-size:18px;margin:0 0 2px} .mut{color:var(--mut);font-size:13px}
  .row{display:flex;gap:8px;margin:14px 0}
  button{font:inherit;padding:8px 18px;border-radius:8px;border:1px solid var(--bd);background:transparent;color:var(--fg);cursor:pointer}
  button.p{background:var(--ac);border-color:var(--ac);color:#fff}
  button:disabled{opacity:.4;cursor:not-allowed}
  label{display:block;margin-top:14px;font-size:13px;color:var(--mut)}
  .f{display:flex;gap:8px;margin-top:4px}
  input{flex:1;font:13px ui-monospace,monospace;padding:8px;border:1px solid var(--bd);border-radius:8px;background:transparent;color:var(--fg)}
  #st{margin-top:6px;font-size:13px}
  .running{color:#1a9b4b}.error{color:#d33}.starting,.stopping{color:#c78a00}
</style></head><body><main>
<h1>KoboldCpp on Colab</h1>
<div class="mut">Account: __EMAIL__</div>
<div class="row">
  <button id="go" class="p">Start</button>
  <button id="no">Stop</button>
</div>
<div id="st" class="mut">...</div>
<label>Base URL</label>
<div class="f"><input id="u" readonly><button data-c="u">Copy</button></div>
<label>API key</label>
<div class="f"><input id="k" readonly><button data-c="k">Copy</button></div>
</main>
<script>
const $=id=>document.getElementById(id);
async function post(p){await fetch(p,{method:'POST'});poll()}
$('go').onclick=()=>post('/api/start');
$('no').onclick=()=>post('/api/stop');
document.querySelectorAll('[data-c]').forEach(b=>b.onclick=()=>{
  const v=$(b.dataset.c).value; if(v) navigator.clipboard.writeText(v);
});
async function poll(){
  try{
    const s=await (await fetch('/api/state')).json();
    $('u').value=s.base_url; $('k').value=s.api_key;
    $('st').className=s.status; $('st').textContent=s.status.toUpperCase()+' - '+s.message;
    const busy=s.status==='starting'||s.status==='running'||s.status==='stopping';
    $('go').disabled=busy;
    $('no').disabled=s.status==='idle'||s.status==='stopping';
  }catch(e){$('st').textContent='Controller not reachable'}
}
poll(); setInterval(poll,1500);
</script></body></html>
"""


def make_handler(state: State, runner: Runner, port: int):
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body, ctype: str = "application/json"):
            if not isinstance(body, str):
                body = json.dumps(body)
            b = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path == "/":
                page = PAGE.replace("__EMAIL__", html.escape(state.get()["email"]))
                self._send(200, page, "text/html")
            elif self.path == "/api/state":
                self._send(200, state.get())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin not in allowed_origins:
                return self._send(403, {"error": "forbidden origin"})
            if self.path == "/api/start":
                if state.get()["status"] in ("idle", "error"):
                    runner.stop_flag.clear()
                    runner.cmds.put("start")
                self._send(200, state.get())
            elif self.path == "/api/stop":
                runner.stop_flag.set()
                runner.cmds.put("stop")
                self._send(200, state.get())
            else:
                self._send(404, {"error": "not found"})

    return Handler


# --------------------------------------------------------------------------- main
def do_login(args):
    """Visible browser so you can sign in to the Google account once."""
    with sync_playwright() as pw:
        safe = re.sub(r"[^A-Za-z0-9._@-]", "_", args.email)
        profile = Path(args.profile_dir).expanduser() / safe
        profile.mkdir(parents=True, exist_ok=True)
        kw = dict(user_data_dir=str(profile), headless=False, viewport={"width": 1280, "height": 900})
        if args.channel:
            kw["channel"] = args.channel
        ctx = pw.chromium.launch_persistent_context(**kw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://accounts.google.com/")
        input(f"Sign in as {args.email} in the browser window, then press Enter here... ")
        ctx.close()
    print("Profile saved. Run again without --login.")


def main():
    ap = argparse.ArgumentParser(description="Run the KoboldCpp Colab notebook behind a hidden browser.")
    ap.add_argument("--email", required=True, help="Google account that runs the notebook (selects the browser profile)")
    ap.add_argument("--notebook", help="Colab notebook URL (Drive or GitHub)")
    ap.add_argument("--login", action="store_true", help="Open a visible browser once to sign in, then exit")
    ap.add_argument("--port", type=int, default=8765, help="Local UI/API port (default 8765)")
    ap.add_argument("--show", action="store_true", help="Show the Colab browser instead of hiding it")
    ap.add_argument("--channel", default="chrome", help='Browser channel; "chrome" uses installed Chrome, "" uses Playwright Chromium')
    ap.add_argument("--profile-dir", default="~/.colab-runner/profiles", help="Where per-account browser profiles live")
    ap.add_argument("--timeout", type=int, default=1200, help="Seconds to wait for URL + key (default 1200)")
    ap.add_argument("--out", help="Optional JSON file to write {base_url, api_key} to when ready")
    ap.add_argument("--no-open", action="store_true", help="Do not open the UI in your browser")
    args = ap.parse_args()

    if args.login:
        return do_login(args)
    if not args.notebook:
        ap.error("--notebook is required (unless using --login)")

    state = State(args.email)
    runner = Runner(args, state)
    runner.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state, runner, args.port))
    ui = f"http://127.0.0.1:{args.port}/"
    print(f"UI: {ui}   (Ctrl+C to quit; this also stops the Colab runtime)", flush=True)
    if not args.no_open:
        webbrowser.open(ui)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runner.cmds.put("quit")
        runner.join(timeout=60)


if __name__ == "__main__":
    main()
