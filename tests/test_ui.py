"""UI integrity tests.

Two layers:
1. Static — every onclick handler in index.html must resolve to a defined
   function (catches stale references after refactors).
2. Live — headless Chromium loads /dash against a real server seeded with a
   journaled session, clicks through every page, exercises the Check form,
   and FAILS on any console error or unhandled page error. This is the test
   for "buttons don't work".
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HTML = REPO / "backend" / "app" / "dashboards" / "static" / "index.html"


# ------------------------------------------------------------------ static


def test_every_onclick_handler_is_defined() -> None:
    text = HTML.read_text(encoding="utf-8")
    used = set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\s*\(', text))
    used |= set(re.findall(r'onkeydown="if\(event\.key===\'Enter\'\)([A-Za-z_$][\w$]*)\(', text))
    defined = set(re.findall(r'(?:async\s+)?function\s+([A-Za-z_$][\w$]*)', text))
    missing = used - defined
    assert not missing, f"onclick references undefined functions: {missing}"


def test_no_unescaped_template_artifacts() -> None:
    text = HTML.read_text(encoding="utf-8")
    assert "undefined" not in re.sub(r"//.*", "", text).split("<script>")[0], \
        "literal 'undefined' leaked into static HTML"


# ------------------------------------------------------------------ live


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory):
    playwright = pytest.importorskip("playwright.sync_api")
    root = tmp_path_factory.mktemp("ui")
    journal_dir = root / "journal"
    journal_dir.mkdir()
    # Seed a session so pages have data.
    sys.path.insert(0, str(REPO / "backend"))
    from app.engine.runner import PaperSession

    PaperSession(["RELIANCE"], n_bars=200, seed=3,
                 journal_path=journal_dir / "seed.jsonl").run()

    port = _free_port()
    env = {"ETB_JOURNAL_DIR": str(journal_dir), "PYTHONPATH": str(REPO / "backend"),
           "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
           "PATH": __import__("os").environ.get("PATH", "")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--port", str(port)],
        cwd=str(REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    import httpx

    for _ in range(60):
        try:
            if httpx.get(base + "/health", timeout=2).status_code == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("ui server did not start")
    yield base
    proc.kill()


def test_ui_click_through_no_console_errors(ui_server) -> None:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(ui_server + "/dash", wait_until="networkidle")
        assert "AI Trading Assistant" in page.content()

        # Click through every nav page; each must render without JS errors.
        for name in ["Check a Strategy", "Brokers", "Risk", "AI & Model", "Costs",
                     "Replay", "System", "Today"]:
            page.click(f"nav button:has-text('{name}')")
            page.wait_for_timeout(900)

        # Check-a-Strategy form: typed symbol must SURVIVE a period click
        # (the reported state-loss bug).
        page.click("nav button:has-text('Check a Strategy')")
        page.wait_for_timeout(600)
        page.fill("#ck-symbols", "INFY")
        page.click(".seg button:has-text('1 year')")
        page.wait_for_timeout(600)
        assert page.input_value("#ck-symbols") == "INFY", "form state lost on re-render"

        # Replay action button works end-to-end against the seeded journal.
        page.click("nav button:has-text('Replay')")
        page.wait_for_timeout(600)
        page.click("button:has-text('Replay (simple rules)')")
        page.wait_for_selector("text=MATCH", timeout=60_000)

        browser.close()

    benign = ("favicon", "net::ERR_FAILED")  # favicon 404 etc.
    real = [e for e in errors if not any(b in e for b in benign)]
    assert not real, f"console/page errors: {real}"
