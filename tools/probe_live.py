"""Open the assistant's browser profile once, wait for a security check and dump it.

Run it only while the assistant window is closed: it takes the same profile lock
the engine uses, so a running assistant makes it exit immediately.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from yuketang_helper.bypass import VerificationBypass  # noqa: E402
from yuketang_helper.config import Config, RUNTIME  # noqa: E402
from yuketang_helper.verification import verification_state  # noqa: E402


def acquire_lock():
    RUNTIME.mkdir(exist_ok=True)
    handle = (RUNTIME / "profile.lock").open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    import msvcrt
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="", help="page to open; defaults to config.course_url")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--solve", action="store_true")
    args = parser.parse_args()

    lock = acquire_lock()
    if lock is None:
        print("LOCKED: 助手正在运行，请先关闭整个助手窗口。")
        return 2
    config = Config.load()
    url = args.url or config.course_url
    print("OPEN", url.split("?")[0])
    owner = SimpleNamespace(config=config, emit=lambda kind, data: print("LOG", kind, str(data)[:200]))
    try:
        with sync_playwright() as playwright:
            options = {
                "user_data_dir": str(RUNTIME / "browser-profile"),
                "headless": False,
                "no_viewport": True,
                "args": [
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-blink-features=AutomationControlled",
                ],
            }
            if config.browser != "chromium":
                options["channel"] = config.browser
            context = playwright.chromium.launch_persistent_context(**options)
            bypass = VerificationBypass(owner)
            bypass.install(context, stealth=config.stealth)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                print("NAV-FAIL", type(exc).__name__)
            deadline = time.monotonic() + args.seconds
            state = {"required": False}
            while time.monotonic() < deadline:
                try:
                    state = verification_state(page)
                except Exception:
                    state = {"required": False}
                if state.get("required"):
                    break
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
            print("STATE", json.dumps(state, ensure_ascii=False))
            if state.get("required"):
                target = bypass.probe(page)
                print("PROBE", target)
                if args.solve:
                    print("HANDLE", bypass.handle(page))
                    print("AFTER", verification_state(page))
            else:
                print("NO_CHALLENGE")
            context.close()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
