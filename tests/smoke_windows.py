"""Explicit Windows/Edge smoke check with a temporary, unsigned-in browser."""
import queue

from playwright.sync_api import sync_playwright

from tests.test_player import FIXTURE, MEDIA
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine, PLAYER_SCRIPT


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=False, args=[
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ])
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_content(FIXTURE)
            page.evaluate(PLAYER_SCRIPT)
            page.locator("video").evaluate("(v, src) => v.src = src", MEDIA)
            page.wait_for_function("document.querySelector('video').readyState >= 3")
            options = Config().player_options()
            page.evaluate("o => window.__yktHelper.read(o)", options)
            page.wait_for_function("!document.querySelector('video').paused")
            page.wait_for_function("document.querySelector('video').currentTime >= 0.2")
            engine = Engine(Config(), queue.Queue())
            engine.context = context
            engine.window_state(page, "minimized")
            session = context.new_cdp_session(page)
            assert session.send("Browser.getWindowForTarget")["bounds"]["windowState"] == "minimized"
            start = page.locator("video").evaluate("v => v.currentTime")
            print('Before minimize sample:', page.locator('video').evaluate('v => ({time:v.currentTime, rate:v.playbackRate, paused:v.paused, ready:v.readyState})'))
            page.wait_for_timeout(600)
            end = page.locator("video").evaluate("v => v.currentTime")
            print('After minimize sample:', page.locator('video').evaluate('v => ({time:v.currentTime, rate:v.playbackRate, paused:v.paused, ready:v.readyState})'))
            assert end - start > 0.7, f"Playback did not advance at 2x while minimized: {end - start}"
            engine.window_state(page, "normal")
            assert session.send("Browser.getWindowForTarget")["bounds"]["windowState"] == "normal"
            session.detach()
            print(f"Edge minimize/restore OK; media advanced {end - start:.2f}s in 0.60s at 2x.")
            context.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
