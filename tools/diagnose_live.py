"""Live diagnosis: why does the DOM probe miss the security check?"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from yuketang_helper.bypass import BYPASS_SCRIPT, VerificationBypass  # noqa: E402
from yuketang_helper.config import Config, RUNTIME  # noqa: E402
from yuketang_helper.verification import verification_state  # noqa: E402

SCAN = r"""() => {
  const norm = value => (value || '').replace(/\s+/g, '');
  const pattern = /^(?:请(?:先)?(?:完成|进行|通过))?(?:安全(?:核验|校验|验证)|人机(?:验证|校验))[：:！!。.]?$/;
  const hits = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length > 3) continue;
    const value = norm(el.textContent);
    if (!pattern.test(value)) continue;
    const box = el.getBoundingClientRect();
    hits.push({tag: el.tagName.toLowerCase(), id: el.id, cls: String(el.className).slice(0, 60),
               text: value.slice(0, 30), rect: [Math.round(box.x), Math.round(box.y),
               Math.round(box.width), Math.round(box.height)],
               visible: !!el.getClientRects().length});
    if (hits.length >= 5) break;
  }
  const dots = [];
  for (const el of document.querySelectorAll('div,span,i,b,em,strong,p,text')) {
    const value = norm(el.textContent);
    if (!/^[起始终止]$/.test(value) && !/^[0-9]$/.test(value)) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 6 || box.height < 6) continue;
    const style = getComputedStyle(el);
    dots.push({text: value, tag: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 40),
               rect: [Math.round(box.x), Math.round(box.y), Math.round(box.width), Math.round(box.height)],
               bg: style.backgroundColor, radius: style.borderRadius});
    if (dots.length >= 10) break;
  }
  return {hits, dots,
    shadows: [...document.querySelectorAll('*')].filter(el => el.shadowRoot).length,
    version: window.__yktBypass?.version || null};
}"""


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
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    lock = acquire_lock()
    if lock is None:
        print("LOCKED")
        return 2
    config = Config.load()
    owner = SimpleNamespace(config=config, emit=lambda kind, data: None)
    report = {"frames": []}
    try:
        with sync_playwright() as playwright:
            options = {"user_data_dir": str(RUNTIME / "browser-profile"), "headless": False,
                       "no_viewport": True,
                       "args": ["--disable-blink-features=AutomationControlled"]}
            if config.browser != "chromium":
                options["channel"] = config.browser
            context = playwright.chromium.launch_persistent_context(**options)
            bypass = VerificationBypass(owner)
            bypass.install(context, stealth=config.stealth)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url or config.course_url, wait_until="domcontentloaded", timeout=60000)
            deadline = time.monotonic() + seconds
            state = {"required": False}
            while time.monotonic() < deadline:
                try:
                    state = verification_state(page)
                except Exception:
                    state = {"required": False}
                if state.get("required"):
                    break
                page.wait_for_timeout(1000)
            report["state"] = state
            for index, frame in enumerate(page.frames):
                entry = {"index": index, "url": frame.url.split("?")[0][:160]}
                try:
                    entry["scan"] = frame.evaluate(SCAN)
                except Exception as exc:
                    entry["scan_error"] = f"{type(exc).__name__}: {exc}"[:300]
                try:
                    frame.evaluate(BYPASS_SCRIPT)
                    entry["detect"] = frame.evaluate(
                        "() => { try { return window.__yktBypass.detect(); } catch (e) { return {error: String(e)}; } }")
                except Exception as exc:
                    entry["detect_error"] = f"{type(exc).__name__}: {exc}"[:300]
                labels, points = bypass._ax_points(page, frame)
                entry["ax"] = {"labels": labels, "points": points[:6]}
                report["frames"].append(entry)
            page.screenshot(path=str(RUNTIME / "diagnose-viewport.png"))
            target = RUNTIME / "diagnose.json"
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("REPORT", target)
            for frame in report["frames"]:
                print("FRAME", frame["index"], frame["url"])
                print("  detect:", json.dumps(frame.get("detect"), ensure_ascii=False)[:400])
                print("  scan:", json.dumps(frame.get("scan"), ensure_ascii=False)[:600])
                print("  ax:", json.dumps(frame.get("ax"), ensure_ascii=False)[:400])
                print("  errors:", frame.get("scan_error"), frame.get("detect_error"))
            context.close()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
