"""Read-only check: does the exercise page already carry the correct answer?"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from yuketang_helper.config import Config, RUNTIME  # noqa: E402

CHECK = r"""() => {
  const root = document.querySelector('.container-problem');
  if (!root) return {ready: false};
  const out = {ready: true, sources: []};
  const seen = new Set();
  const walk = (node, depth, path) => {
    if (!node || depth > 3 || seen.has(node)) return;
    seen.add(node);
    if (typeof node !== 'object') return;
    for (const key of Object.keys(node).slice(0, 40)) {
      let value;
      try { value = node[key]; } catch (error) { continue; }
      if (value && typeof value === 'object') {
        const keys = Array.isArray(value) ? null : Object.keys(value).slice(0, 25);
        if (keys && keys.some(item => /answer|correct|score|analysis/i.test(item))) {
          out.sources.push({path: path + '.' + key, keys,
            answerShape: (() => {
              const answer = value.Answer ?? value.answer ?? value.correct_answer;
              if (answer === undefined) return null;
              if (Array.isArray(answer)) return {array: answer.length, sampleTypes: answer.map(v => typeof v)};
              return {type: typeof answer, length: String(answer).length};
            })()});
        }
        walk(value, depth + 1, path + '.' + key);
      }
    }
  };
  const vue = root.__vueParentComponent || root.__vue__;
  if (vue) {
    out.vue = true;
    walk(vue.setupState || vue.ctx || vue.proxy || {}, 0, 'vue');
    const problem = (vue.setupState || vue.ctx || {}).problem;
    if (problem) walk(problem, 0, 'problem');
  } else {
    out.vue = false;
  }
  out.rootDatasetKeys = Object.keys(root.dataset);
  return out;
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
    url = sys.argv[1]
    lock = acquire_lock()
    if lock is None:
        print("LOCKED")
        return 2
    config = Config.load()
    try:
        with sync_playwright() as playwright:
            options = {"user_data_dir": str(RUNTIME / "browser-profile"), "headless": False,
                       "no_viewport": True,
                       "args": ["--disable-blink-features=AutomationControlled"]}
            if config.browser != "chromium":
                options["channel"] = config.browser
            context = playwright.chromium.launch_persistent_context(**options)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(40):
                page.wait_for_timeout(1000)
                if page.frames and any(f.locator('.container-problem').count() for f in page.frames):
                    break
            for frame in page.frames:
                try:
                    if not frame.locator('.container-problem').count():
                        continue
                    result = frame.evaluate(CHECK)
                    print(frame.url.split('?')[0][-50:], json.dumps(result, ensure_ascii=False)[:1500])
                except Exception as exc:
                    print("ERR", type(exc).__name__, str(exc)[:120])
            context.close()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
