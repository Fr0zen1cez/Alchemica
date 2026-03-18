"""
Alchemica — Launcher
Just run:  python run.py
"""

import subprocess
import sys
import os
import time
import socket
import threading
import webbrowser
import shutil

# ── 1. Auto-install dependencies ──────────────────────────────────────────────
REQUIRED = ['flask', 'requests']

def install_missing():
    try:
        import flask, requests
        return True
    except ImportError:
        pass
    print("Installing dependencies...")
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', 'flask', 'requests', '--quiet'],
        capture_output=True
    )
    if result.returncode != 0:
        print("Failed to install dependencies. Try: pip install flask requests")
        return False
    print("Done.")
    return True

# ── 2. Flask server ───────────────────────────────────────────────────────────
def _get_port() -> int:
    """Read server_port from config, falling back to 5000."""
    try:
        from core.config import load_config
        cfg = load_config()
        port = int(cfg.get("server_port") or 5000)
        return port if 1024 <= port <= 65535 else 5000
    except Exception:
        return 5000

def start_flask():
    # Import here so auto-install above runs first
    from app import app
    port = _get_port()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)

def wait_for_server(timeout=15):
    port = _get_port()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.08)
    return False

# ── 3. Browser app-mode launcher ─────────────────────────────────────────────
WIN_SIZE = '--window-size=1280,800'

def get_app_url() -> str:
    return f'http://127.0.0.1:{_get_port()}'

def find_browser():
    """Return (browser_name, path) for the best available browser, or None."""

    # Windows: check registry paths + common install locations
    if sys.platform == 'win32':
        candidates = [
            # Chrome
            ('Chrome', r'C:\Program Files\Google\Chrome\Application\chrome.exe'),
            ('Chrome', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe'),
            ('Chrome', os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe')),
            # Edge (ships with all Windows 10/11 — almost certainly present)
            ('Edge',   r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
            ('Edge',   r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
            ('Edge',   os.path.expandvars(r'%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe')),
            # Brave
            ('Brave',  r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe'),
        ]
        for name, path in candidates:
            if os.path.isfile(path):
                return name, path

    # macOS
    elif sys.platform == 'darwin':
        candidates = [
            ('Chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
            ('Chromium', '/Applications/Chromium.app/Contents/MacOS/Chromium'),
            ('Edge',   '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
            ('Brave',  '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'),
        ]
        for name, path in candidates:
            if os.path.isfile(path):
                return name, path

    # Linux
    else:
        for name, cmd in [('Chrome', 'google-chrome'), ('Chrome', 'google-chrome-stable'),
                          ('Chromium', 'chromium-browser'), ('Chromium', 'chromium'),
                          ('Edge', 'microsoft-edge'), ('Brave', 'brave-browser')]:
            path = shutil.which(cmd)
            if path:
                return name, path

    return None, None


def launch_app_window(browser_path, app_url):
    """Launch browser in --app mode: no address bar, looks like a desktop app."""
    args = [
        browser_path,
        f'--app={app_url}',
        WIN_SIZE,
        '--disable-extensions',
        '--no-first-run',
        '--no-default-browser-check',
    ]
    # Suppress console output from browser process
    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        return True
    except Exception as e:
        print(f"Failed to launch {browser_path}: {e}")
        return False


# ── 4. Main ───────────────────────────────────────────────────────────────────
def main():
    print("⚗️  Alchemica")
    print("─" * 30)

    if not install_missing():
        input("Press Enter to exit.")
        sys.exit(1)

    print("Starting server...")
    server_thread = threading.Thread(target=start_flask, daemon=True)
    server_thread.start()

    if not wait_for_server():
        port = _get_port()
        print(f"Server failed to start. Check for port conflicts on {port}.")
        input("Press Enter to exit.")
        sys.exit(1)

    print("Server ready.")

    app_url = get_app_url()
    browser_name, browser_path = find_browser()

    if browser_path:
        print(f"Launching via {browser_name}...")
        if launch_app_window(browser_path, app_url):
            print(f"Running at {app_url}  (close the game window to exit)\n")
        else:
            # Fallback: open in default browser
            webbrowser.open(app_url)
    else:
        # No Chromium browser found — open whatever the system default is
        print(f"No Chrome/Edge found. Opening in default browser at {app_url}")
        print("Tip: Install Chrome or Edge for the best app-like experience.\n")
        webbrowser.open(app_url)

    # Keep Flask alive until user presses Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down.")
        sys.exit(0)


if __name__ == '__main__':
    main()

