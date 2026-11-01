"""Check reactive multiselect submission in a temporary, minimized Edge."""
import queue

from playwright.sync_api import sync_playwright

from tests.test_homework import HomeworkTests
from tests.test_llm import MockServer
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine
from yuketang_helper.homework import HomeworkSession
from yuketang_helper.llm import ModelSettings


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=False, args=[
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
        ])
        try:
            context = browser.new_context()
            fixture = HomeworkTests()
            fixture.context = context
            fixture.page = context.new_page()
            fixture.reactive_multiple_fixture()
            posts = []

            def accept(route):
                posts.append(route.request.post_data_json)
                route.fulfill(body='{"success":true,"data":{"my_count":1}}',
                              content_type="application/json")

            context.route("**/mooc-api/v1/lms/exercise/problem_apply/", accept)
            engine = Engine(Config(verification_bypass="off", stealth=False), queue.Queue())
            engine.context = context
            engine.owned_page = fixture.page
            session = HomeworkSession(engine)
            try:
                engine.window_state(fixture.page, "minimized")
                with MockServer() as server:
                    server.content = '{"option_ids":["A","C","D"]}'
                    session.start(ModelSettings(base_url=server.url, model="mock"), auto_submit=True)
                    fixture.run_until_idle(session)
                    assert len(posts) == 1, posts
                    assert sorted(posts[0]["answer"]) == ["A", "C", "D"], posts
                    assert session.submitted_count == 1
                    assert len(server.requests) == 1
                cdp = context.new_cdp_session(fixture.page)
                try:
                    assert cdp.send("Browser.getWindowForTarget")["bounds"]["windowState"] == "minimized"
                finally:
                    cdp.detach()
                print("Minimized reactive multiselect: A/C/D submitted together exactly once; window stayed minimized.")
            finally:
                session.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
