"""Automatic handling of the platform security check (雨课堂 安全核验 / 腾讯 TCaptcha).

Page helpers and manual handoff:

1. ``solve``  – real interaction: decode the slider screenshot, locate the cut-out
   edge, and drag the thumb with a humanised curve. Works with the vendor SDK
   that yuketang loads from ``turing.captcha.gtimg.com``.
2. manual     – whatever the helpers cannot clear keeps the old behaviour: pause,
   restore the browser and let the user finish the check.

The module only reads layout information and the challenge image; it never reads
answers, cookies or account data.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import random
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError

from . import captcha
from . import ocr
from .config import RUNTIME
from .verification import verification_state

STEALTH_SCRIPT = Path(__file__).with_name("stealth.js").read_text(encoding="utf-8")
BYPASS_SCRIPT = Path(__file__).with_name("bypass.js").read_text(encoding="utf-8")

CAPTCHA_HOSTS = (
    "turing.captcha.gtimg.com", "turing.captcha.qcloud.com", "ssl.captcha.qq.com",
    "captcha.gtimg.com", "cap.qq.com", "captcha.qcloud.com",
)
COOLDOWN = 2.5


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _captcha_frame(frame):
    return _host(frame.url) in CAPTCHA_HOSTS or any(name in frame.url for name in ("captcha", "verify", "challenge"))


class VerificationBypass:
    """Layered security-check handling bound to one browser context."""

    def __init__(self, owner):
        self.owner = owner
        self.installed = False
        self.rng = random.Random()
        self.page_state = {}
        self.total_cleared = 0
        self.last_message = ""
        self.last_attempt = 0.0
        self.probe_requested = False

    # ---------------------------------------------------------------- settings
    @property
    def config(self):
        return getattr(self.owner, "config", None)

    def mode(self):
        mode = str(getattr(self.config, "verification_bypass", "solve") or "solve").lower()
        return mode if mode in {"off", "solve"} else "solve"

    def max_attempts(self):
        try:
            return max(1, min(6, int(getattr(self.config, "bypass_attempts", 3))))
        except (TypeError, ValueError):
            return 3

    def gain(self):
        try:
            value = float(getattr(self.config, "bypass_slider_gain", 0) or 0)
        except (TypeError, ValueError):
            value = 0.0
        return value if 0.3 <= value <= 3 else 0.0

    # ----------------------------------------------------------------- install
    def install(self, context, stealth=True):
        """Inject the scripts once per browser context; safe to call twice."""
        if self.installed or context is None:
            return
        try:
            if stealth:
                context.add_init_script(STEALTH_SCRIPT)
            context.add_init_script(BYPASS_SCRIPT)
        except Exception:
            return
        # Keep ordinary navigation and verification responses untouched. Passing
        # a list to context.route installs an invalid catch-all handler: matching
        # raises TypeError and leaves even the login document request suspended.
        # Do not register a response-replacement route during startup.
        self.installed = True

    # ---------------------------------------------------------------- helpers
    def _notice(self, message):
        if message != self.last_message:
            self.last_message = message
            try:
                self.owner.emit("log", message)
            except Exception:
                pass

    def _detection(self, page):
        """Return (frame, descriptor) for the most promising challenge, or None."""
        best = None
        order = {"path": -1, "slider": 0, "confirm": 1, "unknown": 2}
        for frame in page.frames:
            try:
                frame.evaluate(BYPASS_SCRIPT)
                info = frame.evaluate("() => window.__yktBypass.detect()")
            except Exception:
                continue
            if not isinstance(info, dict) or not info.get("present"):
                continue
            rank = (order.get(info.get("kind"), 3), 0 if _captcha_frame(frame) else 1)
            if best is None or rank < best[0]:
                best = (rank, frame, info)
        return (best[1], best[2]) if best else (None, None)

    def _box(self, frame, role):
        try:
            locator = frame.locator(f'[data-ykt-bypass="{role}"]').first
            if not locator.count():
                return None
            return locator.bounding_box()
        except BrowserError:
            return None

    def _shot(self, frame, role):
        try:
            locator = frame.locator(f'[data-ykt-bypass="{role}"]').first
            if not locator.count():
                return None
            return locator.screenshot(timeout=4000)
        except Exception:
            return None

    def _human_move(self, page, x, y):
        page.mouse.move(x + self.rng.uniform(-24, 24), y + self.rng.uniform(-16, 16), steps=3)
        page.wait_for_timeout(int(self.rng.uniform(40, 90)))
        page.mouse.move(x + self.rng.uniform(-3, 3), y + self.rng.uniform(-2, 2), steps=2)
        page.wait_for_timeout(int(self.rng.uniform(30, 70)))

    def _reveal(self, frame):
        """Bring the challenge into view; a drag outside the viewport never fires."""
        for role in ("thumb", "path-0", "bg"):
            try:
                locator = frame.locator(f'[data-ykt-bypass="{role}"]').first
                if locator.count():
                    locator.scroll_into_view_if_needed(timeout=1500)
                    return
            except Exception:
                continue

    def _glide(self, page, start, end):
        """Move between two dots with a slight bow and human pacing."""
        (sx, sy), (ex, ey) = start, end
        distance = math.hypot(ex - sx, ey - sy)
        steps = max(6, min(40, int(distance / 5)))
        bow = self.rng.uniform(-3.5, 3.5)
        nx, ny = (-(ey - sy), ex - sx)
        norm = math.hypot(nx, ny) or 1.0
        nx, ny = nx / norm, ny / norm
        for step in range(1, steps + 1):
            ratio = step / steps
            bend = math.sin(math.pi * ratio) * bow
            page.mouse.move(sx + (ex - sx) * ratio + nx * bend,
                            sy + (ey - sy) * ratio + ny * bend, steps=1)
            page.wait_for_timeout(int(self.rng.uniform(6, 15)))

    def _path_points(self, frame, info):
        count = int((info.get("path") or {}).get("count") or 0)
        points = []
        for index in range(count):
            box = self._box(frame, f"path-{index}")
            if not box:
                return []
            points.append((box["x"] + box["width"] / 2, box["y"] + box["height"] / 2))
        return points

    def _points_are_dots(self, frame, points):
        """Refuse to drag text: every point must sit on a filled dot or SVG shape."""
        try:
            offset = (0.0, 0.0)
            if frame.parent_frame is not None:
                box = frame.frame_element().bounding_box()
                if not box:
                    return True
                offset = (box["x"], box["y"])
            for x, y in points:
                if not frame.evaluate(
                        "p => window.__yktBypass.dotAt(p[0], p[1])",
                        [x - offset[0], y - offset[1]]):
                    return False
            return True
        except Exception:
            return True

    def _frame_offset(self, frame):
        try:
            if frame.parent_frame is None:
                return (0.0, 0.0)
            box = frame.frame_element().bounding_box()
            return (box["x"], box["y"]) if box else (0.0, 0.0)
        except Exception:
            return (0.0, 0.0)

    def _backend_rect(self, session, backend):
        object_id = None
        try:
            object_id = session.send("DOM.resolveNode", {"backendNodeId": backend}) \
                .get("object", {}).get("objectId")
            if not object_id:
                return None
            result = session.send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": (
                    "function(){const el=this.nodeType===1?this:this.parentElement;"
                    "if(!el)return null;const r=el.getBoundingClientRect();"
                    "if(!r.width||!r.height)return null;"
                    "return {x:r.x,y:r.y,w:r.width,h:r.height};}"),
                "returnByValue": True,
            })
            value = result.get("result", {}).get("value")
            return value if isinstance(value, dict) else None
        except Exception:
            return None
        finally:
            if object_id:
                try:
                    session.send("Runtime.releaseObject", {"objectId": object_id})
                except Exception:
                    pass

    def _ax_points(self, page, frame):
        """Dot positions from the accessibility tree, which also sees shadow DOM."""
        session = None
        found = []
        try:
            session = page.context.new_cdp_session(frame)
            nodes = session.send("Accessibility.getFullAXTree").get("nodes", [])
            for node in nodes:
                if node.get("ignored"):
                    continue
                value = str((node.get("name") or {}).get("value") or "").strip()
                if not value or len(value) > 2:
                    continue
                if not (value in ("起", "始", "终", "止") or value.isdigit()):
                    continue
                backend = node.get("backendDOMNodeId")
                if not backend:
                    continue
                rect = self._backend_rect(session, backend)
                if not rect or not 8 <= rect["w"] <= 90 or not 8 <= rect["h"] <= 90:
                    continue
                found.append((value, rect))
        except Exception:
            found = []
        finally:
            if session:
                try:
                    session.detach()
                except Exception:
                    pass
        if not found:
            return [], []
        offset = self._frame_offset(frame)
        start = end = None
        steps = []
        seen = set()
        for value, rect in found:
            centre = (offset[0] + rect["x"] + rect["w"] / 2, offset[1] + rect["y"] + rect["h"] / 2)
            key = (round(centre[0] / 5), round(centre[1] / 5))
            if key in seen:
                continue
            seen.add(key)
            if value in ("起", "始") and start is None:
                start = (value, centre)
            elif value in ("终", "止") and end is None:
                end = (value, centre)
            elif value.isdigit():
                steps.append((int(value), value, centre))
        if not start or not end:
            return [], []
        steps.sort(key=lambda item: item[0])
        ordered = [start] + [(label, centre) for _, label, centre in steps] + [end]
        return [label for label, _ in ordered], [centre for _, centre in ordered]

    def _ax_detection(self, page):
        for frame in page.frames:
            labels, points = self._ax_points(page, frame)
            if len(points) >= 2:
                return frame, {"kind": "ax-path", "labels": labels, "points": points,
                               "buttons": [], "slider": None, "path": {"labels": labels}}
        return None, None

    def _press_path(self, page, frame, points, labels, verify=False):
        """Press the first dot, glide through the rest, release on the last."""
        if len(points) < 2:
            return False
        if verify and not self._points_are_dots(frame, points):
            self._log("skip-path", reason="points-not-dots", points=len(points))
            return False
        self._notice(f"识别到点选路径核验：{labels}，按顺序连线。")
        start = points[0]
        try:
            frame.evaluate("() => window.__yktBypass.noSelect(true)")
        except Exception:
            pass
        try:
            self._human_move(page, *start)
            page.mouse.move(*start, steps=2)
            page.mouse.down()
            page.wait_for_timeout(int(self.rng.uniform(90, 170)))
            for source, target in zip(points, points[1:]):
                self._glide(page, source, target)
                page.wait_for_timeout(int(self.rng.uniform(50, 110)))
            page.wait_for_timeout(captcha.release_hold(self.rng))
            page.mouse.up()
        finally:
            try:
                frame.evaluate("() => window.__yktBypass.noSelect(false)")
            except Exception:
                pass
        return True

    def _draw_path(self, page, frame, info):
        """Press the start dot, glide through 1..n, release on the end dot."""
        self._reveal(frame)
        points = self._path_points(frame, info)
        labels = " → ".join((info.get("path") or {}).get("labels") or [])
        if len(points) >= 2:
            return self._press_path(page, frame, points, labels, verify=True)
        return self._draw_ocr_path(page, frame, info)

    def _draw_ocr_path(self, page, frame, info):
        """Fallback for panels whose dots are painted on a canvas: OCR the labels."""
        labels, points = self._ocr_points(frame)
        if len(points) < 2:
            return False
        self._log("ocr-path", labels=labels, points=len(points))
        return self._press_path(page, frame, points, " → ".join(labels))

    def _ocr_points(self, frame):
        panel = self._box(frame, "panel")
        shot = self._shot(frame, "panel")
        if not panel or not shot or panel["width"] < 60 or panel["height"] < 60:
            return [], []
        try:
            image = captcha.decode_png(shot, color=True)
        except captcha.ImageError:
            return [], []
        RUNTIME.mkdir(exist_ok=True)
        pending = RUNTIME / "verification-ocr.png"
        try:
            pending.write_bytes(shot)
            data = ocr.recognize_boxes(pending)
        except Exception:
            return [], []
        scale = panel["width"] / image.width if image.width else 1.0
        words = [((word.get("text") or "").strip(),
                  panel["x"] + (word["x"] + word["width"] / 2) * scale,
                  panel["y"] + (word["y"] + word["height"] / 2) * scale)
                 for word in data.get("words", [])]
        spots = [{"cx": panel["x"] + spot["cx"] * scale,
                  "cy": panel["y"] + spot["cy"] * scale,
                  "area": spot["area"]}
                 for spot in self._blobs(image)]
        return self._ordered_from(words, spots)

    def _viewport_ocr_path(self, page):
        """Fallback when a closed shadow root or canvas hides the dots from the DOM."""
        try:
            size = page.evaluate("() => [innerWidth, innerHeight, devicePixelRatio || 1]")
            shot = page.screenshot(timeout=8000)
        except Exception:
            return False
        try:
            image = captcha.decode_png(shot, color=True)
        except captcha.ImageError:
            return False
        css_width = size[0] or image.width
        scale = css_width / image.width if image.width else 1.0
        RUNTIME.mkdir(exist_ok=True)
        pending = RUNTIME / "verification-viewport.png"
        try:
            pending.write_bytes(shot)
            data = ocr.recognize_boxes(pending)
        except Exception:
            return False
        words = [((word.get("text") or "").strip(),
                  (word["x"] + word["width"] / 2) * scale,
                  (word["y"] + word["height"] / 2) * scale)
                 for word in data.get("words", [])]
        spots = [{"cx": spot["cx"] * scale, "cy": spot["cy"] * scale, "area": spot["area"]}
                 for spot in self._blobs(image)]
        labels, points = self._ordered_from(words, spots)
        if len(points) < 2:
            return False
        self._log("viewport-path", labels=labels, points=len(points))
        return self._press_path(page, page.main_frame, points, " → ".join(labels))

    def _blobs(self, image):
        try:
            return captcha.blobs(image)
        except Exception:
            return []

    def _ordered_from(self, words, spots):
        """Match 起/终/数字 to coloured dots and return the drawing order."""
        if not spots:
            # Without a coloured dot there is nothing safe to press: raw text
            # coordinates would turn the drag into a text selection.
            return [], []
        start = end = None
        steps = []
        used = set()
        for value, word_x, word_y in words:
            if not value or len(value) > 2:
                continue
            centre = (word_x, word_y)
            if spots:
                distances = [math.hypot(spot["cx"] - centre[0], spot["cy"] - centre[1]) for spot in spots]
                nearest = min(range(len(spots)), key=lambda index: distances[index])
                # The instruction sentence also says 起/终; only text that sits on a
                # coloured dot may label a point.
                if distances[nearest] > 70 or nearest in used:
                    continue
                used.add(nearest)
                centre = (spots[nearest]["cx"], spots[nearest]["cy"])
            if value in ("起", "始", "开始") and start is None:
                start = (value, centre)
            elif value in ("终", "止", "结束") and end is None:
                end = (value, centre)
            elif value.isdigit() and len(value) <= 2:
                steps.append((int(value), value, centre))
        if not start or not end:
            geometric = self._geometric_points(spots)
            if geometric:
                return geometric
            return [], []
        steps.sort(key=lambda item: item[0])
        ordered = [start] + [(label, centre) for _, label, centre in steps] + [end]
        return [label for label, _ in ordered], [centre for _, centre in ordered]

    def _geometric_points(self, spots):
        """Last resort: OCR could not label the dots, so follow their layout order."""
        if not spots:
            return [], []
        areas = sorted(spot["area"] for spot in spots)
        median = areas[len(areas) // 2] or 1
        similar = [spot for spot in spots if 0.35 <= spot["area"] / median <= 2.8]
        if not 3 <= len(similar) <= 8:
            return [], []
        spread_x = max(spot["cx"] for spot in similar) - min(spot["cx"] for spot in similar)
        spread_y = max(spot["cy"] for spot in similar) - min(spot["cy"] for spot in similar)
        horizontal = spread_x >= spread_y
        ordered = sorted(similar, key=lambda spot: spot["cx"] if horizontal else spot["cy"])
        points = [(spot["cx"], spot["cy"]) for spot in ordered]
        self._log("ocr-path-geometric", points=len(points), horizontal=horizontal)
        return ["?"] * len(points), points


    def _drag(self, page, box, distance):
        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2
        self._human_move(page, start_x, start_y)
        page.mouse.move(start_x, start_y, steps=1)
        page.mouse.down()
        page.wait_for_timeout(int(self.rng.uniform(60, 130)))
        x = start_x
        y = start_y
        for delta, dy, delay in captcha.trajectory(distance, self.rng):
            x += delta
            y = start_y + dy
            page.mouse.move(x, y, steps=1)
            page.wait_for_timeout(delay)
        page.wait_for_timeout(captcha.release_hold(self.rng))
        page.mouse.up()

    # ---------------------------------------------------------------- solving
    def _slider_distance(self, frame):
        """Estimate the drag distance in CSS pixels, or None when unclear."""
        self._reveal(frame)
        bg_box = self._box(frame, "bg")
        piece_box = self._box(frame, "piece")
        thumb_box = self._box(frame, "thumb")
        track_box = self._box(frame, "track")
        if not thumb_box or not bg_box:
            return None
        if bg_box["width"] < 80 or bg_box["height"] < 30:
            return None
        shot = self._shot(frame, "bg")
        if not shot:
            return None
        try:
            image = captcha.decode_png(shot)
        except captcha.ImageError:
            return None
        scale = bg_box["width"] / image.width if image.width else 1.0
        piece_css = piece_box["width"] if piece_box else image.width * 0.16
        piece_natural = max(12, int(round(piece_css / scale)))
        offset_css = (piece_box["x"] - bg_box["x"]) if piece_box else 0.0
        band = (0, image.height)
        if piece_box and (piece_box["y"] >= bg_box["y"] - 4
                          and piece_box["y"] + piece_box["height"] <= bg_box["y"] + bg_box["height"] + 4):
            band = (max(0, int((piece_box["y"] - bg_box["y"]) / scale)),
                    min(image.height, int((piece_box["y"] - bg_box["y"] + piece_box["height"]) / scale)))
            if band[1] - band[0] < 8:
                band = (0, image.height)
        try:
            candidates = captcha.gap_candidates(image, band, piece_natural, limit=2)
        finally:
            del shot
        if not candidates:
            return None
        gap_css = candidates[0] * scale
        distance = gap_css - offset_css
        if track_box and bg_box["width"] > 0:
            gain = self.gain() or (track_box["width"] / bg_box["width"])
            if 0.5 <= gain <= 1.8:
                distance *= gain
        if distance <= 4:
            return None
        limit = (track_box["width"] - thumb_box["width"]) if track_box else distance * 1.4
        return max(6.0, min(distance, max(12.0, limit)))

    def _board_box(self, frame, info):
        """Canvas rectangle in page coordinates (locator box first, then the dump)."""
        box = self._box(frame, "board")
        if box:
            return box
        board = (info or {}).get("board") or {}
        rect = board.get("rect")
        if rect and len(rect) == 4:
            return {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]}
        return None

    def _canvas_path(self, page, frame, info):
        """Read the canvas pixels, label the dots by glyph matching and draw."""
        board = self._board_box(frame, info)
        try:
            data = frame.evaluate("() => window.__yktBypass.readCanvas()")
        except Exception:
            return False
        if not isinstance(data, dict) or not board:
            return False
        width = float(data.get("width") or 0)
        spots = data.get("spots") or []
        if width <= 0 or len(spots) < 2:
            return False
        scale = board["width"] / width
        offset = self._frame_offset(frame)
        entries = []
        for spot in spots:
            label = str(spot.get("label") or "").strip()
            try:
                score = float(spot.get("score") or 0)
                x = offset[0] + board["x"] + float(spot["x"]) * scale
                y = offset[1] + board["y"] + float(spot["y"]) * scale
            except (TypeError, ValueError):
                continue
            if not label or score < 0.55:
                continue
            entries.append((label, score, (x, y)))
        start = next((item for item in entries if item[0] in ("起", "始")), None)
        end = next((item for item in entries if item[0] in ("终", "止")), None)
        if not start or not end:
            return False
        steps = sorted((item for item in entries if item[0].isdigit()),
                       key=lambda item: (int(item[0]), item[2][0]))
        ordered = [start, *steps, end]
        labels = " → ".join(item[0] for item in ordered)
        points = [item[2] for item in ordered]
        self._log("canvas-path", labels=labels, points=len(points),
                  scores=[round(item[1], 2) for item in ordered])
        self._notice(f"识别到画布核验点位：{labels}，按顺序连线。")
        return self._press_path(page, frame, points, labels)

    def _solve_frame(self, page, frame, info):
        """Perform one interaction. Returns True when something was actually done."""
        kind = info.get("kind")
        if kind == "path":
            return self._draw_path(page, frame, info)
        # Canvas-drawn checks expose no DOM text; read the pixels instead. This is
        # the most precise route for the yuketang path challenge.
        if info.get("board") and self._canvas_path(page, frame, info):
            return True
        if kind == "ax-path":
            return self._press_path(page, frame, info.get("points") or [],
                                    " → ".join(info.get("labels") or []))
        if kind == "viewport":
            return self._viewport_ocr_path(page)
        if kind == "confirm":
            return self._confirm(frame, info)
        if kind == "unknown":
            # Fallback: locate the dots from a panel screenshot via OCR and blobs.
            return self._draw_ocr_path(page, frame, info)
        if kind != "slider":
            return False
        distance = self._slider_distance(frame)
        if not distance:
            return False
        thumb = self._box(frame, "thumb")
        if not thumb:
            return False
        self._notice(f"识别到滑块核验，按缺口位置拖动 {distance:.0f} 像素。")
        self._drag(page, thumb, distance)
        return True

    def _settle(self, page, timeout=1500):
        page.wait_for_timeout(timeout)
        try:
            return not verification_state(page)["required"]
        except BrowserError:
            return False

    def _confirm(self, frame, info):
        buttons = info.get("buttons") or []
        for index, button in enumerate(buttons):
            if button.get("disabled"):
                continue
            try:
                frame.locator(f'[data-ykt-bypass="confirm-{index}"]').first.click(timeout=1500)
                return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------ entry
    def handle(self, page, wait=None):
        """Try to clear a visible security check. Returns {state, message}."""
        if self.mode() == "off" or not page or page.is_closed():
            return {"state": "off", "message": ""}
        now = time.monotonic()
        record = self.page_state.setdefault(id(page), {"attempts": 0, "cooldown": 0.0, "total": 0})
        if now < record["cooldown"]:
            return {"state": "cooldown", "message": ""}
        frame, info = self._detection(page)
        if not info:
            try:
                required = verification_state(page)["required"]
            except Exception:
                required = False
            if not required:
                self._log("detect-none")
                return {"state": "none", "message": ""}
            # Accessibility names still expose the rendered 起/1/2/3/终 even when
            # the modal lives in a closed shadow root.
            frame, info = self._ax_detection(page)
            if not info:
                # Last resort: read the dots from a viewport screenshot.
                frame, info = page.main_frame, {"kind": "viewport", "container": {},
                                                "buttons": [], "slider": None, "path": None}
        limit = self.max_attempts()
        # A failed attempt usually re-renders a fresh puzzle with new dot positions;
        # that is a new challenge, so it deserves a fresh set of tries.
        signature = json.dumps([info.get("kind"), (info.get("path") or {}).get("labels"),
                                (info.get("container") or {}).get("rect")], ensure_ascii=False)
        if record.get("signature") != signature:
            record["attempts"] = 0
        record["signature"] = signature
        record["total"] = record.get("total", 0) + 1
        if record["total"] > limit * 4 or record["attempts"] >= limit:
            return {"state": "exhausted", "message": "自动核验尝试次数已用完，请在浏览器手动完成。"}
        record["attempts"] += 1
        record["cooldown"] = now + COOLDOWN + self.rng.uniform(0, 1.2)
        if info.get("kind") in {"path", "ax-path", "slider", "confirm"}:
            record.pop("unknown", None)
            record.pop("unsupported", None)
        if self.probe_requested:
            self.probe_requested = False
            try:
                self.probe(page)
            except Exception:
                pass
        elif info.get("kind") == "unknown" and "unknown" not in record:
            # Keep the first unsupported structure on disk: selectors are usually
            # adjusted from that dump instead of guessing.
            try:
                self.probe(page)
            except Exception:
                pass
        self._log("detect", kind=info.get("kind"), labels=(info.get("path") or {}).get("labels"),
                  container=(info.get("container") or {}).get("rect"),
                  attempt=record["attempts"])
        acted = False
        try:
            acted = self._solve_frame(page, frame, info)
        except BrowserError:
            acted = False
        except Exception:
            acted = False
        cleared = self._settle(page) if acted else False
        self._log("result", kind=info.get("kind"), acted=acted, cleared=cleared)
        if cleared:
            self.total_cleared += 1
            record.update({"attempts": 0, "cooldown": 0.0, "total": 0})
            message = "安全核验已自动通过，继续之前的任务。"
            self._notice(message)
            return {"state": "cleared", "message": message}
        remaining = limit - record["attempts"]
        if info.get("kind") == "unknown":
            # A challenge that is still animating in has no dots yet, so require a
            # second look before giving up and handing it to the user.
            record["unknown"] = record.get("unknown", 0) + 1
            if record["unknown"] >= 2:
                record["unsupported"] = True
                return {"state": "unsupported",
                        "message": "检测到安全核验，但当前结构无法自动处理；已恢复浏览器，请手动完成。"}
            return {"state": "retry", "message": "核验结构尚未识别，稍后重试。"}
        return {"state": "retry", "message": f"安全核验自动处理未通过，剩余 {remaining} 次尝试。"}

    def reset(self, page=None):
        if page is None:
            self.page_state.clear()
        else:
            self.page_state.pop(id(page), None)

    def _log(self, event, **fields):
        """Append one JSON line so a failed run can be inspected afterwards."""
        try:
            RUNTIME.mkdir(exist_ok=True)
            target = RUNTIME / "verification.log"
            if target.exists() and target.stat().st_size > 512 * 1024:
                target.unlink()
            record = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **fields}
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def holds_foreground(self, page):
        """True while automatic handling still has tries left, so the UI waits."""
        if self.mode() == "off" or not page:
            return False
        record = self.page_state.get(id(page))
        if not record:
            return True
        if record.get("unsupported"):
            return False
        limit = self.max_attempts()
        return record["attempts"] < limit and record.get("total", 0) <= limit * 4

    # ------------------------------------------------------------------ probe
    def probe(self, page):
        """Dump the live challenge structure so selectors can be tuned offline."""
        RUNTIME.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        payload = {"page": strip_query(page.url)[:200], "frames": []}
        for index, frame in enumerate(page.frames):
            entry = {"index": index, "url": strip_query(frame.url)[:200], "host": _host(frame.url)}
            try:
                frame.evaluate(BYPASS_SCRIPT)
                entry["structure"] = frame.evaluate("() => window.__yktBypass.probe()")
            except Exception as exc:
                entry["error"] = type(exc).__name__
            try:
                locator = frame.locator('[data-ykt-bypass="bg"], [data-ykt-bypass="thumb"]').first
                target = RUNTIME / f"verification-{stamp}-frame{index}.png"
                if locator.count():
                    locator.screenshot(path=str(target), timeout=4000)
                    entry["screenshot"] = target.name
            except Exception:
                pass
            payload["frames"].append(entry)
        target = RUNTIME / f"verification-probe-{stamp}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._notice(f"已导出核验页面结构：{target.name}")
        return target


def strip_query(url):
    """Helper kept for callers that log challenge URLs without query tokens."""
    return re.sub(r"\?.*$", "", url or "")
