"""Start the local FastAPI server and a Cloudflare quick tunnel.

Called from run_daily.py before scraping/sending so approve/reject buttons
always have a reachable public URL. If a server or tunnel is already up,
it is reused instead of starting a second one.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

UVICORN_LOG = DATA_DIR / "uvicorn.log"
CLOUDFLARED_LOG = DATA_DIR / "cloudflared.log"


def _server_url() -> str:
    return "http://127.0.0.1:8000"


def _is_local_server_running(timeout: float = 3.0) -> bool:
    try:
        response = requests.get(_server_url(), timeout=timeout)
        return response.status_code < 500
    except Exception:
        return False


def _is_tunnel_reachable(url: str | None, timeout: float = 6.0) -> bool:
    if not url or "replace-me" in url.lower():
        return False
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code < 500
    except Exception:
        return False


def _start_uvicorn() -> subprocess.Popen:
    """Start uvicorn detached so it survives the end of this Python process."""
    print("[tunnel] Starting local server (uvicorn) on port 8000...")
    log = UVICORN_LOG.open("w", encoding="utf-8")
    cmd = [sys.executable, "-m", "uvicorn", "server.server:app", "--port", "8000"]
    kwargs = {
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "cwd": str(ROOT_DIR),
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    return subprocess.Popen(cmd, **kwargs)


def _start_cloudflared() -> subprocess.Popen:
    """Start cloudflared quick tunnel and write its output to a log file."""
    print("[tunnel] Starting Cloudflare quick tunnel...")
    log = CLOUDFLARED_LOG.open("w", encoding="utf-8")
    cloudflared = ROOT_DIR / "cloudflared.exe"
    if not cloudflared.exists():
        raise FileNotFoundError(
            f"cloudflared.exe not found at {cloudflared}. "
            "Download it from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/"
        )
    cmd = [str(cloudflared), "tunnel", "--url", _server_url()]
    kwargs = {
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "cwd": str(ROOT_DIR),
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    return subprocess.Popen(cmd, **kwargs)


def _wait_for_tunnel_url(timeout: float = 60.0) -> str:
    """Poll the cloudflared log until the public URL appears."""
    print("[tunnel] Waiting for Cloudflare URL...")
    pattern = re.compile(r"https://[\w\-]+\.trycloudflare\.com")
    start = time.time()
    while time.time() - start < timeout:
        try:
            log_text = CLOUDFLARED_LOG.read_text(encoding="utf-8")
            match = pattern.search(log_text)
            if match:
                return match.group(0)
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        "Cloudflare tunnel did not produce a public URL within the timeout. "
        f"Check {CLOUDFLARED_LOG} for details."
    )


def _update_tunnel_env(url: str) -> None:
    """Write the new URL to .env and update the in-process settings."""
    from config import settings

    env_file = settings.CONFIG_DIR / ".env"
    if env_file.exists():
        text = env_file.read_text(encoding="utf-8")
        if "TUNNEL_BASE_URL=" in text:
            text = re.sub(
                r"TUNNEL_BASE_URL=.*",
                f"TUNNEL_BASE_URL={url}",
                text,
                flags=re.MULTILINE,
            )
        else:
            text += f"\nTUNNEL_BASE_URL={url}\n"
        env_file.write_text(text, encoding="utf-8")

    os.environ["TUNNEL_BASE_URL"] = url
    settings.TUNNEL_BASE_URL = url


def ensure_server_and_tunnel(url: str | None = None) -> str:
    """Ensure uvicorn and a Cloudflare tunnel are running, return the public URL."""
    from config import settings

    if not _is_local_server_running():
        _start_uvicorn()
        # Give uvicorn a moment to bind before starting cloudflared.
        for _ in range(20):
            if _is_local_server_running():
                break
            time.sleep(0.5)

    current_url = url or settings.TUNNEL_BASE_URL
    if _is_tunnel_reachable(current_url):
        print(f"[tunnel] Reusing existing tunnel: {current_url}")
        return current_url

    _start_cloudflared()
    new_url = _wait_for_tunnel_url()
    _update_tunnel_env(new_url)
    print(f"[tunnel] New public URL: {new_url}")
    return new_url


if __name__ == "__main__":
    print(ensure_server_and_tunnel())
