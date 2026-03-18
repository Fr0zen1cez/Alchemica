"""
Alchemica — Desktop launcher (pywebview + Flask)

Uses the OS-native browser engine (WebView2 on Windows, WebKit on macOS/Linux)
instead of bundling Chromium. This eliminates all PyQt6/QWebEngineView flicker,
DPI scaling, and GPU driver issues that appear only in PyInstaller builds.

Install:  pip install pywebview flask pystray pillow
Build:    python build_game.py
"""

import multiprocessing
if __name__ == '__main__':
    multiprocessing.freeze_support()

import sys
import os
import time
import socket
import threading
import logging
from pathlib import Path
from collections import deque

import webview

import app as flask_module
from app import app as flask_app
from core.config import load_config, save_config

# ── Logging ───────────────────────────────────────────────────────────────────
log_queue = deque(maxlen=500)

class _LogHandler(logging.Handler):
    def emit(self, record):
        try:
            log_queue.append(self.format(record))
        except Exception:
            pass

_handler = _LogHandler()
_handler.setFormatter(logging.Formatter('%(asctime)s  %(levelname)s  %(message)s'))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)

# ── Server startup ────────────────────────────────────────────────────────────
_server_started = False

def _determine_binding():
    """Read config and return (host, port) for Flask binding."""
    try:
        cfg = load_config(force_reload=True)
        host = "0.0.0.0" if cfg.get("server_mode") else "127.0.0.1"
        port = int(cfg.get("server_port") or 5000)
        if not (1024 <= port <= 65535):
            port = 5000
    except Exception:
        host, port = "127.0.0.1", 5000
    return host, port

def _run_flask(host, port):
    global _server_started
    if _server_started:
        return
    _server_started = True
    flask_module._DESKTOP_APP = True
    flask_app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)

def start_server(host, port):
    if _server_started:
        return
    threading.Thread(target=_run_flask, args=(host, port), daemon=True).start()

def wait_for_server(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.08)
    return False

# ── System tray ───────────────────────────────────────────────────────────────
def _setup_tray(window, icon_path):
    """Set up a system tray icon using pystray. Optional — app works without it."""
    try:
        import pystray
        from PIL import Image as PilImage

        img = PilImage.open(icon_path) if icon_path else PilImage.new("RGB", (64, 64), "#4a9eff")

        def on_show(icon, item):
            window.show()

        def on_quit(icon, item):
            icon.stop()
            window.destroy()

        tray = pystray.Icon(
            "Alchemica",
            img,
            "Alchemica",
            menu=pystray.Menu(
                pystray.MenuItem("Open Alchemica", on_show, default=True),
                pystray.MenuItem("Quit", on_quit),
            ),
        )
        threading.Thread(target=tray.run, daemon=True).start()
        return tray
    except ImportError:
        return None
    except Exception as e:
        logging.warning(f"Tray icon unavailable: {e}")
        return None

# ── Resolve LAN URL for server mode ──────────────────────────────────────────
def _resolve_lan_url(port):
    try:
        cfg = load_config()
        port = int(cfg.get("server_port") or port)
        if cfg.get("server_custom_url_enabled") and cfg.get("server_custom_url"):
            return cfg["server_custom_url"].rstrip("/")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:{port}"
    except Exception:
        return f"http://YOUR_IP:{port}"

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    host, port = _determine_binding()
    game_url    = f"http://127.0.0.1:{port}"
    server_mode = (host == "0.0.0.0")

    # Find icon
    icon_path = None
    assets = Path(__file__).parent / "assets"
    for name in ("icon.ico", "logo.png", "icon_256.png"):
        p = assets / name
        if p.exists():
            icon_path = str(p)
            break

    # Start Flask before creating the window so the first page load is instant
    start_server(host, port)
    if not wait_for_server(port):
        webview.create_window(
            "Alchemica — Error",
            html=(
                "<body style='background:#0a0a1a;color:#ff6666;font-family:sans-serif;"
                "display:flex;align-items:center;justify-content:center;height:100vh'>"
                f"<h2>Could not start on port {port}.<br>"
                "Is something else using that port?</h2></body>"
            ),
        )
        webview.start()
        return

    title = "Alchemica"
    if server_mode:
        lan   = _resolve_lan_url(port)
        title = f"Alchemica  \u00b7  Server: {lan}"

    window = webview.create_window(
        title,
        url=game_url,
        width=1400,
        height=900,
        min_size=(800, 600),
        background_color="#0a0a1a",
    )

    _setup_tray(window, icon_path)

    webview.start(debug=False, private_mode=False)


if __name__ == "__main__":
    main()
