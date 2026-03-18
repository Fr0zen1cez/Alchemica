"""
Alchemica — Game Builder
========================
Run from the project root to produce a distributable build.

    python build_game.py

You will be asked whether to build a Windows EXE or a Linux AppImage.

Output (EXE):
  dist/Alchemica/           <- folder with EXE + all DLLs
  dist/Alchemica_win.zip    <- ready-to-upload ZIP for Windows

Output (AppImage):
  dist/Alchemica-x86_64.AppImage  <- self-contained Linux AppImage
  dist/Alchemica_linux.zip         <- ready-to-upload ZIP for Linux

Notes:
  - Uses --onedir (NOT --onefile) so Qt's DLLs and GPU helpers sit
    beside the executable.
  - saves/, backups/, logs/, plugins/ and config.json are created next
    to the binary at runtime — they are NOT bundled (user data stays
    with the user).
  - AppImage builds require Linux. appimagetool is downloaded
    automatically if it isn't already on PATH.
"""

from __future__ import annotations

import os
import sys
import stat
import shutil
import zipfile
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Optional

VERSION = "1.0"

KOFI_URL    = "https://ko-fi.com/fr0zen1cez"
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def banner(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def step(n: int, total: int, msg: str):
    print(f"\n[{n}/{total}] {msg}")


def ask_choice(prompt: str, choices: list[str]) -> str:
    """
    Print a numbered menu and return the user's selected string.
    Keeps asking until a valid choice is made.
    """
    print(f"\n{prompt}")
    for i, c in enumerate(choices, 1):
        print(f"  [{i}] {c}")
    while True:
        raw = input("\nEnter choice number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print(f"  Please enter a number between 1 and {len(choices)}.")


def create_icon_from_png() -> Optional[Path]:
    """Convert assets/logo.png → assets/icon.ico (Windows) if needed."""
    assets = Path(__file__).parent / "assets"
    logo   = assets / "logo.png"
    icon   = assets / "icon.ico"

    if icon.exists():
        print("      Using existing assets/icon.ico")
        return icon

    if not logo.exists():
        print("      Warning: assets/logo.png not found — skipping icon.")
        return None

    try:
        from PIL import Image
        img = Image.open(logo).resize((256, 256), Image.Resampling.LANCZOS)
        img.save(icon, format="ICO",
                 sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        print(f"      Created {icon}")
        return icon
    except Exception as e:
        print(f"      Warning: Could not create icon.ico — {e}")
        return None


def install_deps(extras: Optional[List[str]] = None):
    """pip-install core build dependencies (quietly)."""
    pkgs = ["flask", "requests", "pillow", "pywebview", "pystray", "pyinstaller"]
    if extras:
        pkgs += extras
    subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs, "-q"])


def run_pyinstaller(extra_args: List[str], name: str = "Alchemica"):
    """Shared PyInstaller invocation; raises on failure."""
    sep = ";" if sys.platform == "win32" else ":"

    hidden = [
        "flask", "flask.templating",
        "werkzeug", "werkzeug.serving", "werkzeug.routing",
        "werkzeug.middleware.shared_data",
        "jinja2", "jinja2.ext", "jinja2.runtime", "jinja2.loaders",
        "requests", "urllib3", "certifi", "charset_normalizer",
        "pkg_resources",
        "webview", "pystray", "PIL",
        "multiprocessing", "multiprocessing.freeze_support",
        "email.mime.multipart", "email.mime.text",
        "queue", "importlib.util",
    ]
    hi_flags = []
    for h in hidden:
        hi_flags += ["--hidden-import", h]

    # --noconsole hides the Windows console window
    platform_flags = ["--noconsole"] if sys.platform == "win32" else []

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "--clean",
        "--noconfirm",
        f"--name={name}",
        f"--add-data=templates{sep}templates",
        f"--add-data=assets{sep}assets",
        f"--add-data=core{sep}core",
        "--collect-all=webview",
        "--collect-all=flask",
        "--collect-all=jinja2",
        "--collect-all=werkzeug",
    ] + platform_flags + hi_flags + extra_args + ["desktop_app.py"]

    print("      (This may take 2–4 minutes on the first run)\n")
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        print(f"\n  BUILD FAILED: {e}")
        print("  Check PyInstaller output above for the root cause.")
        sys.exit(1)


def zip_folder(src: Path, zip_path: Path, prefix: str = ""):
    """Zip all files under *src* into *zip_path*."""
    if zip_path.exists():
        zip_path.unlink()

    file_count = total_bytes = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in src.rglob("*"):
            if f.is_file():
                arcname = Path(prefix) / f.relative_to(src.parent) if prefix else f.relative_to(src.parent)
                zf.write(f, arcname)
                file_count  += 1
                total_bytes += f.stat().st_size

    return file_count, total_bytes


# ── EXE build ─────────────────────────────────────────────────────────────────

def build_exe():
    STEPS = 4
    banner("Alchemica — Windows EXE Build")

    step(0, STEPS, "Preparing icon…")
    icon = create_icon_from_png()

    step(1, STEPS, "Installing / verifying dependencies…")
    install_deps()

    step(2, STEPS, "Running PyInstaller…")
    icon_arg = ["--icon=assets/icon.ico"] if (icon and icon.exists()) else []
    run_pyinstaller(icon_arg)

    dist_path = Path("dist") / "Alchemica"
    exe_path  = dist_path / "Alchemica.exe"

    step(3, STEPS, "Verifying build output…")
    if not dist_path.exists():
        print(f"  ERROR: Expected dist folder not found: {dist_path}")
        sys.exit(1)
    files = list(dist_path.rglob("*"))
    print(f"  Found {len(files)} files in dist folder.")

    step(4, STEPS, "Creating distributable ZIP…")
    zip_path = Path("dist") / "Alchemica_win.zip"
    count, raw_bytes = zip_folder(dist_path, zip_path)
    zip_mb = zip_path.stat().st_size / 1024 / 1024

    banner("BUILD COMPLETE — Windows EXE")
    print(f"\n  EXE      : {exe_path.resolve()}")
    print(f"  ZIP      : {zip_path.resolve()}")
    print(f"  Files    : {count:,}  ({raw_bytes / 1024 / 1024:.1f} MB uncompressed)")
    print(f"  ZIP size : {zip_mb:.1f} MB")
    print()
    print("  To distribute:")
    print("    Upload 'Alchemica_win.zip' as the Windows download.")
    print("    Players unzip the folder and run 'Alchemica.exe'.")
    print("    The EXE must stay inside the folder alongside its DLLs.")
    print("=" * 60)


# ── AppImage build ────────────────────────────────────────────────────────────

def ensure_appimagetool() -> Path:
    """
    Return a path to appimagetool.
    Prefers the system version on PATH; otherwise downloads it to dist/.
    """
    # Check PATH first
    result = subprocess.run(["which", "appimagetool"], capture_output=True, text=True)
    if result.returncode == 0:
        tool = Path(result.stdout.strip())
        print(f"      Found appimagetool at {tool}")
        return tool

    # Download to dist/
    local = Path("dist") / "appimagetool-x86_64.AppImage"
    if not local.exists():
        print(f"      Downloading appimagetool…")
        local.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(APPIMAGETOOL_URL, local)

    # Make executable
    local.chmod(local.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"      appimagetool ready: {local}")
    return local


def build_appdir(pyinstaller_dist: Path, appdir: Path):
    """
    Assemble a valid AppDir from a PyInstaller --onedir output:

        AppDir/
          AppRun              ← launcher shell script
          Alchemica.desktop   ← FreeDesktop .desktop entry
          alchemica.png       ← icon (copied from assets)
          usr/
            bin/
              Alchemica       ← symlink → ../../Alchemica (the real binary)
            lib/              ← all PyInstaller-bundled files
    """
    if appdir.exists():
        shutil.rmtree(appdir)

    usr_lib = appdir / "usr" / "lib" / "alchemica"
    usr_lib.mkdir(parents=True)

    # Copy entire PyInstaller bundle into usr/lib/alchemica
    print("      Copying PyInstaller bundle into AppDir…")
    shutil.copytree(pyinstaller_dist, usr_lib / "Alchemica", dirs_exist_ok=True)

    # usr/bin symlink
    usr_bin = appdir / "usr" / "bin"
    usr_bin.mkdir(parents=True, exist_ok=True)
    launcher_link = usr_bin / "Alchemica"
    launcher_link.symlink_to("../../lib/alchemica/Alchemica/Alchemica")

    # Icon
    icon_src = Path("assets") / "logo.png"
    icon_dst = appdir / "alchemica.png"
    if icon_src.exists():
        shutil.copy2(icon_src, icon_dst)
        # Also required at usr/share/icons path for some tools
        icons_dir = appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
        icons_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_src, icons_dir / "alchemica.png")
    else:
        # Placeholder 1×1 PNG so appimagetool doesn't complain
        icon_dst.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    # .desktop file  (must sit at the root of AppDir AND at usr/share/applications)
    desktop_content = (
        "[Desktop Entry]\n"
        "Name=Alchemica\n"
        "Exec=Alchemica\n"
        "Icon=alchemica\n"
        "Type=Application\n"
        "Categories=Game;\n"
        "Comment=Alchemica — the alchemy crafting game\n"
    )
    (appdir / "Alchemica.desktop").write_text(desktop_content)
    apps_dir = appdir / "usr" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "Alchemica.desktop").write_text(desktop_content)

    # AppRun launcher
    apprun = appdir / "AppRun"
    apprun.write_text(
        "#!/bin/sh\n"
        'HERE="$(dirname "$(readlink -f "$0")")"\n'
        'export LD_LIBRARY_PATH="$HERE/usr/lib/alchemica/Alchemica:$LD_LIBRARY_PATH"\n'
        'exec "$HERE/usr/lib/alchemica/Alchemica/Alchemica" "$@"\n'
    )
    apprun.chmod(apprun.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"      AppDir assembled at {appdir}")


def build_appimage():
    if sys.platform != "linux":
        print("\n  ERROR: AppImage builds require Linux.")
        print("  Please run this script on a Linux machine or WSL2.")
        sys.exit(1)

    STEPS = 5
    banner("Alchemica — Linux AppImage Build")

    step(1, STEPS, "Installing / verifying dependencies…")
    install_deps()

    step(2, STEPS, "Running PyInstaller (Linux build)…")
    run_pyinstaller([])   # No --icon or --noconsole quirks on Linux

    pyinstaller_dist = Path("dist") / "Alchemica"
    if not pyinstaller_dist.exists():
        print(f"  ERROR: Expected dist folder not found: {pyinstaller_dist}")
        sys.exit(1)
    print(f"      PyInstaller bundle: {pyinstaller_dist}")

    step(3, STEPS, "Assembling AppDir…")
    appdir = Path("dist") / "Alchemica.AppDir"
    build_appdir(pyinstaller_dist, appdir)

    step(4, STEPS, "Building AppImage with appimagetool…")
    tool       = ensure_appimagetool()
    appimage   = Path("dist") / "Alchemica-x86_64.AppImage"

    env = os.environ.copy()
    env["ARCH"] = "x86_64"
    # FUSE may not be available in CI/containers — use --appimage-extract-and-run
    try:
        subprocess.check_call(
            [str(tool), str(appdir), str(appimage)],
            env=env,
        )
    except subprocess.CalledProcessError:
        print("      Retrying with --appimage-extract-and-run (no FUSE needed)…")
        env2 = env.copy()
        env2["APPIMAGETOOL_APP_IMAGE_EXTRACT_AND_RUN"] = "1"
        subprocess.check_call(
            [str(tool), str(appdir), str(appimage)],
            env=env2,
        )

    if not appimage.exists():
        print(f"  ERROR: AppImage not created at {appimage}")
        sys.exit(1)

    appimage.chmod(appimage.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"      AppImage created: {appimage}")

    step(5, STEPS, "Creating distributable ZIP…")
    zip_path = Path("dist") / "Alchemica_linux.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(appimage, "Alchemica-x86_64.AppImage")

    zip_mb      = zip_path.stat().st_size  / 1024 / 1024
    appimage_mb = appimage.stat().st_size  / 1024 / 1024

    banner("BUILD COMPLETE — Linux AppImage")
    print(f"\n  AppImage : {appimage.resolve()}  ({appimage_mb:.1f} MB)")
    print(f"  ZIP      : {zip_path.resolve()}   ({zip_mb:.1f} MB)")
    print()
    print("  To distribute:")
    print("    Upload 'Alchemica_linux.zip' as the Linux download.")
    print("    Players unzip, then run:  chmod +x Alchemica-x86_64.AppImage")
    print("                              ./Alchemica-x86_64.AppImage")
    print("=" * 60)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    banner("Alchemica — Game Builder")
    print(f"\n  Support the project: {KOFI_URL}")

    if sys.platform == "win32":
        print("  Platform: Windows — building EXE\n")
        build_exe()
    else:
        print("  Platform: Linux — building AppImage\n")
        build_appimage()
