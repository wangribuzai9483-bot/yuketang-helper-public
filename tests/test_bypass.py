import base64
import math
import random
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from yuketang_helper import captcha
from yuketang_helper.bypass import BYPASS_SCRIPT, VerificationBypass
from yuketang_helper.config import Config

GAP = 210
PIECE = 60
BAND = (26, 86)


def slider_background():
    width, height = 340, 160
    gray = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            value = 132 + int(18 * math.sin(y / 17.0)) + int(10 * math.sin(x / 41.0))
            gray[y * width + x] = max(0, min(255, value))
    for y in range(BAND[0], BAND[1]):
        for x in (GAP - 1, GAP, GAP + 1, GAP + PIECE - 1, GAP + PIECE, GAP + PIECE + 1):
            if 0 <= x < width:
                gray[y * width + x] = 42
        for x in range(GAP + 2, GAP + PIECE - 1):
            gray[y * width + x] = max(0, gray[y * width + x] - 26)
    return captcha.GrayImage(width, height, gray)


def slider_fixture(markup, gap=GAP):
    png = base64.b64encode(captcha.encode_png(slider_background())).decode()
    return f'''<!doctype html><meta charset="utf-8">
<style>body {{ margin: 0; font: 14px/1.4 sans-serif }}</style>
{markup.replace("__SRC__", "data:image/png;base64," + png).replace("__GAP__", str(gap))}
'''


SLIDER = slider_fixture('''
<div id="tcaptcha_transform_dy" style="position:relative;width:360px;height:300px;padding:8px">
  <div>请完成安全核验</div>
  <div style="position:relative;width:340px;height:160px">
    <img id="slideBg" width="340" height="160" src="__SRC__" style="display:block">
    <div id="slideBlock" style="position:absolute;left:8px;top:20px;width:56px;height:56px;
         border:1px dashed #999"></div>
  </div>
  <div id="tcaptcha_drag" style="position:relative;width:340px;height:34px;background:#eee;margin-top:8px">
    <div id="tcaptcha_drag_thumb" style="position:absolute;left:0;top:0;width:40px;height:34px;
         background:#888"></div>
  </div>
</div>
<script>
  window.__gap = __GAP__;
  window.__travel = 0;
  window.__solved = false;
  window.__moves = 0;
  const thumb = document.getElementById('tcaptcha_drag_thumb');
  const piece = document.getElementById('slideBlock');
  const pieceLeft = parseFloat(piece.style.left) || 0;
  window.__target = window.__gap - pieceLeft;
  let startX = 0, dragging = false;
  thumb.addEventListener('mousedown', event => {
    dragging = true; startX = event.clientX; event.preventDefault();
  });
  document.addEventListener('mousemove', event => {
    if (!dragging) return;
    window.__moves += 1;
    window.__travel = event.clientX - startX;
    thumb.style.left = Math.max(0, Math.min(300, window.__travel)) + 'px';
    piece.style.left = (pieceLeft + window.__travel) + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (Math.abs(window.__travel - window.__target) <= 4) {
      window.__solved = true;
      document.getElementById('tcaptcha_transform_dy').style.display = 'none';
    } else {
      window.__travel = 0;
      thumb.style.left = '0px';
      piece.style.left = pieceLeft + 'px';
    }
  });
</script>
''')

PATH_CHALLENGE = '''<!doctype html><meta charset="utf-8">
<style>
  body { margin: 0; font: 14px/1.4 sans-serif }
  .dot { position: absolute; width: 34px; height: 34px; border-radius: 50%; background: #7b2ff7;
         color: #fff; display: flex; align-items: center; justify-content: center; font-size: 14px }
  .subject-item { display: inline-block; width: 28px; height: 28px; }
</style>
<div class="problems-aside"><div class="subject-item">1</div><div class="subject-item">2</div>
  <div class="subject-item">3</div><div class="subject-item">4</div></div>
<div role="dialog" class="safety-dialog" style="position:relative;width:420px;height:330px;border:1px solid #ddd">
  <div>请完成安全核验</div>
  <div>按住“起”点，依次经过 1、2、3，最后到“终”点松开</div>
  <div id="grid" style="position:relative;width:400px;height:230px;background:#fafafa">
    <div class="dot" style="left:20px;top:150px">起</div>
    <div class="dot" style="left:90px;top:120px">1</div>
    <div class="dot" style="left:160px;top:130px">2</div>
    <div class="dot" style="left:250px;top:70px">3</div>
    <div class="dot" style="left:330px;top:110px">终</div>
  </div>
  <div>请从“起”点开始。</div>
</div>
<script>
  const order = ['起', '1', '2', '3', '终'];
  const dots = [...document.querySelectorAll('.dot')];
  window.__visited = [];
  window.__wrong = 0;
  window.__solved = false;
  let dragging = false, index = 0;
  const centre = el => { const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; };
  const nearest = (x, y) => dots
    .map(el => ({el, c: centre(el)}))
    .map(item => ({...item, d: Math.hypot(item.c.x - x, item.c.y - y)}))
    .sort((left, right) => left.d - right.d)[0];
  document.addEventListener('mousedown', event => {
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 26 || hit.el.textContent.trim() !== '起') { window.__wrong += 1; return; }
    dragging = true; index = 0; window.__visited = ['起'];
  });
  document.addEventListener('mousemove', event => {
    if (!dragging) return;
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 24) return;
    const label = hit.el.textContent.trim();
    if (label === order[index + 1]) { index += 1; window.__visited.push(label); }
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (index === order.length - 1) {
      window.__solved = true;
      document.querySelector('.safety-dialog').style.display = 'none';
    }
  });
</script>
'''

CANVAS_PATH = '''<!doctype html><meta charset="utf-8">
<style>
  body { margin: 0; font: 14px/1.4 sans-serif }
  .dot { position: absolute; width: 34px; height: 34px; border-radius: 50%; background: #7b2ff7; }
</style>
<div role="dialog" style="width:430px;height:330px;padding:10px">
  <div>请完成安全核验</div>
  <div>按住“起”点，依次经过 1、2、3，最后到“终”点松开</div>
  <div id="board" style="position:relative;width:400px;height:230px;background:#fafafa">
    <div class="dot" data-label="起" style="left:20px;top:150px"></div>
    <div class="dot" data-label="1" style="left:90px;top:120px"></div>
    <div class="dot" data-label="2" style="left:160px;top:130px"></div>
    <div class="dot" data-label="3" style="left:250px;top:70px"></div>
    <div class="dot" data-label="终" style="left:330px;top:110px"></div>
  </div>
</div>
<script>
  const order = ['起', '1', '2', '3', '终'];
  const dots = [...document.querySelectorAll('.dot')];
  window.__visited = [];
  window.__solved = false;
  let dragging = false, index = 0;
  const centre = el => { const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; };
  const nearest = (x, y) => dots
    .map(el => ({el, c: centre(el)}))
    .map(item => ({...item, d: Math.hypot(item.c.x - x, item.c.y - y)}))
    .sort((left, right) => left.d - right.d)[0];
  document.addEventListener('mousedown', event => {
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 26 || hit.el.dataset.label !== '起') return;
    dragging = true; index = 0; window.__visited = ['起'];
  });
  document.addEventListener('mousemove', event => {
    if (!dragging) return;
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 24) return;
    if (hit.el.dataset.label === order[index + 1]) { index += 1; window.__visited.push(hit.el.dataset.label); }
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (index === order.length - 1) {
      window.__solved = true;
      document.querySelector('[role="dialog"]').style.display = 'none';
    }
  });
</script>
'''

CLOSED_SHADOW = '''<!doctype html><meta charset="utf-8">
<style>body { margin: 0; font: 14px/1.4 sans-serif }</style>
<div id="check"></div>
<script>
  const host = document.getElementById('check');
  const root = host.attachShadow({mode: 'closed'});
  root.innerHTML = `
    <div style="width:430px;height:330px;padding:10px">
      <div>请完成安全核验</div>
      <div>按住“起”点，依次经过 1、2、3，最后到“终”点松开</div>
      <div style="position:relative;width:400px;height:230px;background:#fafafa">
        <div class="dot" data-label="起" style="position:absolute;left:20px;top:150px;width:34px;height:34px;border-radius:50%;background:#7b2ff7"></div>
        <div class="dot" data-label="1" style="position:absolute;left:90px;top:120px;width:34px;height:34px;border-radius:50%;background:#7b2ff7"></div>
        <div class="dot" data-label="2" style="position:absolute;left:160px;top:130px;width:34px;height:34px;border-radius:50%;background:#7b2ff7"></div>
        <div class="dot" data-label="3" style="position:absolute;left:250px;top:70px;width:34px;height:34px;border-radius:50%;background:#7b2ff7"></div>
        <div class="dot" data-label="终" style="position:absolute;left:330px;top:110px;width:34px;height:34px;border-radius:50%;background:#7b2ff7"></div>
      </div>
    </div>`;
  const order = ['起', '1', '2', '3', '终'];
  const dots = [...root.querySelectorAll('.dot')];
  window.__visited = [];
  window.__solved = false;
  let dragging = false, index = 0;
  const centre = el => { const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; };
  const nearest = (x, y) => dots
    .map(el => ({el, c: centre(el)}))
    .map(item => ({...item, d: Math.hypot(item.c.x - x, item.c.y - y)}))
    .sort((left, right) => left.d - right.d)[0];
  document.addEventListener('mousedown', event => {
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 26 || hit.el.dataset.label !== '起') return;
    dragging = true; index = 0; window.__visited = ['起'];
  });
  document.addEventListener('mousemove', event => {
    if (!dragging) return;
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 24) return;
    if (hit.el.dataset.label === order[index + 1]) { index += 1; window.__visited.push(hit.el.dataset.label); }
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (index === order.length - 1) { window.__solved = true; host.style.display = 'none'; }
  });
</script>
'''

OPEN_SHADOW = '''<!doctype html><meta charset="utf-8">
<style>body { margin: 0; font: 14px/1.4 sans-serif }</style>
<div id="check"></div>
<script>
  const host = document.getElementById('check');
  const root = host.attachShadow({mode: 'open'});
  const dot = (label, left, top, colour) =>
    `<div class="dot" data-label="${label}" style="position:absolute;left:${left}px;top:${top}px;
      width:34px;height:34px;border-radius:50%;background:${colour};color:#fff;display:flex;
      align-items:center;justify-content:center;font-size:14px">${label}</div>`;
  root.innerHTML = `
    <div style="width:430px;height:330px;padding:10px">
      <div>请完成安全核验</div>
      <div>按住“起”点，依次经过 1、2、3，最后到“终”点松开</div>
      <div style="position:relative;width:400px;height:230px;background:#fafafa">
        ${dot('起', 20, 150, '#3b82f6')}${dot('1', 90, 120, '#7b2ff7')}
        ${dot('2', 160, 130, '#7b2ff7')}${dot('3', 250, 70, '#7b2ff7')}
        ${dot('终', 330, 110, '#22c55e')}
      </div>
    </div>`;
  const order = ['起', '1', '2', '3', '终'];
  const dots = [...root.querySelectorAll('.dot')];
  window.__visited = [];
  window.__solved = false;
  let dragging = false, index = 0;
  const centre = el => { const r = el.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; };
  const nearest = (x, y) => dots
    .map(el => ({el, c: centre(el)}))
    .map(item => ({...item, d: Math.hypot(item.c.x - x, item.c.y - y)}))
    .sort((left, right) => left.d - right.d)[0];
  document.addEventListener('mousedown', event => {
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 26 || hit.el.dataset.label !== '起') return;
    dragging = true; index = 0; window.__visited = ['起'];
  });
  document.addEventListener('mousemove', event => {
    if (!dragging) return;
    const hit = nearest(event.clientX, event.clientY);
    if (hit.d > 24) return;
    if (hit.el.dataset.label === order[index + 1]) { index += 1; window.__visited.push(hit.el.dataset.label); }
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (index === order.length - 1) { window.__solved = true; host.style.display = 'none'; }
  });
</script>
'''

CANVAS_SHADOW = '''<!doctype html><meta charset="utf-8">
<style>body { margin: 0; font: 14px/1.4 sans-serif }</style>
<div id="check"></div>
<script>
  const host = document.getElementById('check');
  const root = host.attachShadow({mode: 'open'});
  root.innerHTML = `
    <div class="card" style="width:430px;height:330px;padding:10px">
      <p class="title">请完成安全核验</p>
      <p class="hint">按住“起”点，依次经过 1、2、3，最后到“终”点松开</p>
      <canvas width="340" height="220" style="width:340px;height:220px"></canvas>
      <div class="status">请从“起”点开始。</div>
    </div>`;
  const canvas = root.querySelector('canvas');
  const ctx = canvas.getContext('2d');
  const dots = [
    {label: '起', x: 60, y: 150, colour: '#3b82f6'},
    {label: '1', x: 140, y: 80, colour: '#7b2ff7'},
    {label: '2', x: 220, y: 150, colour: '#7b2ff7'},
    {label: '3', x: 290, y: 90, colour: '#7b2ff7'},
    {label: '终', x: 300, y: 180, colour: '#22c55e'},
  ];
  function draw() {
    ctx.clearRect(0, 0, 340, 220);
    for (const dot of dots) {
      ctx.beginPath(); ctx.arc(dot.x, dot.y, 17, 0, Math.PI * 2);
      ctx.fillStyle = dot.colour; ctx.fill();
      ctx.fillStyle = '#fff'; ctx.font = 'bold 15px Arial';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(dot.label, dot.x, dot.y);
    }
  }
  draw();
  const order = ['起', '1', '2', '3', '终'];
  window.__visited = [];
  window.__solved = false;
  let dragging = false, index = 0;
  const local = event => {
    const box = canvas.getBoundingClientRect();
    return {x: event.clientX - box.x, y: event.clientY - box.y};
  };
  const nearest = point => dots
    .map(dot => ({dot, d: Math.hypot(dot.x - point.x, dot.y - point.y)}))
    .sort((a, b) => a.d - b.d)[0];
  document.addEventListener('mousedown', event => {
    const hit = nearest(local(event));
    if (hit.d > 26 || hit.dot.label !== '起') return;
    dragging = true; index = 0; window.__visited = ['起'];
  });
  document.addEventListener('mousemove', event => {
    if (!dragging) return;
    const hit = nearest(local(event));
    if (hit.d > 24) return;
    if (hit.dot.label === order[index + 1]) { index += 1; window.__visited.push(hit.dot.label); }
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (index === order.length - 1) { window.__solved = true; host.style.display = 'none'; }
  });
</script>
'''

CONFIRM = '''<!doctype html><meta charset="utf-8">
<div role="dialog" class="verify-dialog" style="width:320px;height:160px">
  <h3>请完成安全核验</h3>
  <button id="start" type="button" onclick="window.__clicked=(window.__clicked||0)+1;
    document.querySelector('.verify-dialog').style.display='none'">开始核验</button>
</div>
'''


class FakeOwner:
    def __init__(self, **options):
        self.config = Config(**options)
        self.messages = []

    def emit(self, kind, data):
        self.messages.append((kind, data))


class CaptchaImageTests(unittest.TestCase):
    def test_png_roundtrip(self):
        image = slider_background()
        decoded = captcha.decode_png(captcha.encode_png(image))
        self.assertEqual((decoded.width, decoded.height), (image.width, image.height))
        self.assertEqual(decoded.gray[:50], image.gray[:50])

    def test_png_rejects_foreign_data(self):
        with self.assertRaises(captcha.ImageError):
            captcha.decode_png(b"GIF89a-not-a-png")

    def test_gap_candidates_find_cutout(self):
        image = slider_background()
        candidates = captcha.gap_candidates(image, BAND, PIECE)
        self.assertTrue(candidates)
        self.assertLessEqual(abs(candidates[0] - GAP), 6, candidates)

    def test_blobs_find_coloured_dots(self):
        width = height = 80
        rgb = bytearray(b"\xff" * (width * height * 3))
        for y in range(30, 50):
            for x in range(30, 50):
                if (x - 40) ** 2 + (y - 40) ** 2 <= 100:
                    slot = (y * width + x) * 3
                    rgb[slot:slot + 3] = bytes((123, 47, 247))
        image = captcha.ColorImage(width, height, rgb)
        spots = captcha.blobs(image)
        self.assertEqual(len(spots), 1, spots)
        self.assertLessEqual(abs(spots[0]["cx"] - 40), 3)
        self.assertLessEqual(abs(spots[0]["cy"] - 40), 3)

    def test_trajectory_reaches_target_with_human_shape(self):
        rng = random.Random(7)
        steps = captcha.trajectory(210, rng)
        total = sum(delta for delta, _, _ in steps)
        self.assertLessEqual(abs(total - 210), 1.5)
        self.assertGreater(len(steps), 30)
        self.assertTrue(all(9 <= delay <= 260 for _, _, delay in steps))
        self.assertTrue(any(delta < 0 for delta, _, _ in steps), "expected an overshoot correction")
        self.assertTrue(all(abs(dy) <= 3.1 for _, dy, _ in steps))

    def test_trajectory_rejects_zero_distance(self):
        with self.assertRaises(captcha.ImageError):
            captcha.trajectory(0)


class ConfigBypassTests(unittest.TestCase):
    def test_defaults(self):
        config = Config().validate()
        self.assertEqual(config.verification_bypass, "solve")
        self.assertEqual(config.bypass_attempts, 3)
        self.assertTrue(config.stealth)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            Config(verification_bypass="magic").validate()
        with self.assertRaises(ValueError):
            Config(bypass_attempts=0).validate()
        with self.assertRaises(ValueError):
            Config(bypass_slider_gain=9).validate()
        with self.assertRaises(ValueError):
            Config(stealth="yes").validate()


class BrowserBypassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def page(self, html):
        page = self.browser.new_page(viewport={"width": 800, "height": 600})
        page.set_content(html)
        page.wait_for_timeout(120)
        self.addCleanup(page.close)
        return page

    def test_bypass_script_requires_heading_first(self):
        page = self.page("<div>普通页面</div>")
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertFalse(info["present"])

    def test_detect_confirm_dialog(self):
        page = self.page(CONFIRM)
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertTrue(info["present"])
        self.assertEqual(info["kind"], "confirm")
        self.assertTrue(info["buttons"])
        self.assertEqual(info["buttons"][0]["label"], "开始核验")

    def test_slider_distance_matches_synthetic_gap(self):
        page = self.page(SLIDER)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        frame, info = bypass._detection(page)
        self.assertIsNotNone(info, "slider challenge was not detected")
        self.assertEqual(info["kind"], "slider")
        distance = bypass._slider_distance(frame)
        self.assertIsNotNone(distance)
        self.assertLessEqual(abs(distance - (GAP - 8)), 8, distance)

    def test_slider_is_solved_automatically(self):
        page = self.page(SLIDER)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(page.evaluate("() => window.__solved"))
        self.assertGreater(page.evaluate("() => window.__moves"), 20)
        self.assertEqual(bypass.total_cleared, 1)

    def test_confirm_is_clicked_automatically(self):
        page = self.page(CONFIRM)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertEqual(page.evaluate("() => window.__clicked"), 1)

    def test_canvas_dots_are_solved_through_ocr_fallback(self):
        page = self.page(CANVAS_PATH)
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertEqual(info["kind"], "unknown", info)
        centres = page.evaluate('''() => {
          const panel = document.querySelector('[data-ykt-bypass="panel"]').getBoundingClientRect();
          return [...document.querySelectorAll('.dot')].map(el => {
            const box = el.getBoundingClientRect();
            return {label: el.dataset.label, x: box.x + box.width / 2 - panel.x,
                    y: box.y + box.height / 2 - panel.y};
          });
        }''')
        words = [{"text": item["label"], "x": item["x"] - 6, "y": item["y"] - 8,
                  "width": 12, "height": 16} for item in centres]
        # The panel also renders the instruction sentence, whose 起/终 must not win.
        words += [{"text": "起", "x": 40, "y": 18, "width": 12, "height": 16},
                  {"text": "终", "x": 300, "y": 18, "width": 12, "height": 16}]
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        with patch("yuketang_helper.bypass.ocr.recognize_boxes",
                   return_value={"words": words, "width": 0, "height": 0}):
            outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(page.evaluate("() => window.__solved"))
        self.assertEqual(page.evaluate("() => window.__visited"), ["起", "1", "2", "3", "终"])

    def test_canvas_dots_fall_back_to_layout_order(self):
        page = self.page(CANVAS_PATH)
        page.evaluate(BYPASS_SCRIPT)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        with patch("yuketang_helper.bypass.ocr.recognize_boxes",
                   return_value={"words": [], "width": 0, "height": 0}):
            outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(page.evaluate("() => window.__solved"))
        self.assertEqual(page.evaluate("() => window.__visited.length"), 5)

    def test_canvas_shadow_panel_is_read_from_pixels_and_solved(self):
        page = self.page(CANVAS_SHADOW)
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertIsNotNone(info.get("board"), info)
        data = page.evaluate("() => window.__yktBypass.readCanvas()")
        labels = sorted(spot["label"] for spot in data["spots"])
        self.assertEqual(labels, sorted(["起", "1", "2", "3", "终"]), data)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(page.evaluate("() => window.__solved"))
        self.assertEqual(page.evaluate("() => window.__visited"), ["起", "1", "2", "3", "终"])

    def test_open_shadow_panel_is_read_and_solved_from_the_dom(self):
        page = self.page(OPEN_SHADOW)
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertEqual(info["kind"], "path", info)
        self.assertEqual(info["path"]["labels"], ["起", "1", "2", "3", "终"])
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(page.evaluate("() => window.__solved"))
        self.assertEqual(page.evaluate("() => window.__visited"), ["起", "1", "2", "3", "终"])

    def test_closed_shadow_panel_is_solved_from_a_viewport_screenshot(self):
        page = self.page(CLOSED_SHADOW)
        page.evaluate(BYPASS_SCRIPT)
        self.assertFalse(page.evaluate("() => window.__yktBypass.detect().present"),
                         "closed shadow content must be invisible to the DOM probe")
        shot = page.screenshot()
        image = captcha.decode_png(shot, color=True)
        spots = sorted(captcha.blobs(image), key=lambda item: item["cx"])
        self.assertEqual(len(spots), 5, spots)
        labels = ["起", "1", "2", "3", "终"]
        words = [{"text": label, "x": spot["cx"] - 6, "y": spot["cy"] - 8,
                  "width": 12, "height": 16} for label, spot in zip(labels, spots)]
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        with patch("yuketang_helper.bypass.ocr.recognize_boxes",
                   return_value={"words": words, "width": 0, "height": 0}):
            outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(page.evaluate("() => window.__solved"))
        self.assertEqual(page.evaluate("() => window.__visited"), labels)

    def test_instruction_text_is_never_used_as_dots(self):
        # The sentence spells 起/1/2/3/终 as plain inline spans; without dots no
        # drag may start, otherwise the mouse would select that paragraph.
        html = '''<!doctype html><meta charset="utf-8">
        <div role="dialog" style="width:430px;height:300px;padding:10px">
          <div>请完成安全核验</div>
          <div>按住“<span>起</span>”点，依次经过 <span>1</span>、<span>2</span>、<span>3</span>，
               最后到“<span>终</span>”点松开</div>
        </div>'''
        page = self.page(html)
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertNotEqual(info["kind"], "path", info)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        page.evaluate("() => { window.__selected = false; document.addEventListener('selectstart', () => window.__selected = true); }")
        outcome = bypass.handle(page)
        self.assertIn(outcome["state"], {"unsupported", "retry"}, outcome)
        self.assertFalse(page.evaluate("() => window.__selected"), "text selection was triggered")

    def test_path_labels_in_nested_spans_inside_plain_panel(self):
        html = '''<!doctype html><meta charset="utf-8">
        <style>
          .node { position: absolute; width: 34px; height: 34px; border-radius: 50%;
                  background: #7b2ff7; color: #fff; display: flex; align-items: center;
                  justify-content: center }
        </style>
        <div class="mask"><div class="panel" style="width:430px;height:320px;padding:10px">
          <div class="title"><span>请完成安全核验</span></div>
          <div class="tip">按住“起”点，依次经过 1、2、3，最后到“终”点松开</div>
          <div class="grid" style="position:relative;height:200px">
            <div class="node" style="left:10px;top:120px"><span>起</span></div>
            <div class="node" style="left:80px;top:60px"><span>1</span></div>
            <div class="node" style="left:150px;top:100px"><span>2</span></div>
            <div class="node" style="left:230px;top:40px"><span>3</span></div>
            <div class="node" style="left:300px;top:90px"><span>终</span></div>
          </div>
          <div class="foot">请从“起”点开始。</div>
        </div></div>'''
        page = self.page(html)
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertEqual(info["kind"], "path", info)
        self.assertEqual(info["path"]["labels"], ["起", "1", "2", "3", "终"])

    def test_path_challenge_is_drawn_in_order(self):
        page = self.page(PATH_CHALLENGE)
        page.evaluate(BYPASS_SCRIPT)
        info = page.evaluate("() => window.__yktBypass.detect()")
        self.assertEqual(info["kind"], "path", info)
        self.assertEqual(info["path"]["labels"], ["起", "1", "2", "3", "终"])
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(page.evaluate("() => window.__solved"))
        self.assertEqual(page.evaluate("() => window.__visited"), ["起", "1", "2", "3", "终"])
        self.assertEqual(page.evaluate("() => window.__wrong"), 0)

    def test_path_challenge_reports_point_count(self):
        page = self.page(PATH_CHALLENGE)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        frame, info = bypass._detection(page)
        self.assertEqual(info["kind"], "path")
        points = bypass._path_points(frame, info)
        self.assertEqual(len(points), 5)
        self.assertLess(points[0][0], points[-1][0])

    def test_cross_origin_vendor_frame_is_solved(self):
        page = self.browser.new_page(viewport={"width": 900, "height": 700})
        self.addCleanup(page.close)
        vendor = SLIDER.replace("<div>请完成安全核验</div>", "").replace(
            "window.__solved = true;",
            "window.__solved = true; try { parent.postMessage('ykt-ok', '*'); } catch (error) {}")
        shell = '''<!doctype html><meta charset="utf-8">
        <div id="ykt-check"><h3>请完成安全核验</h3>
          <iframe id="tcaptcha_iframe_dy" width="380" height="320" style="border:0"
            src="https://turing.captcha.gtimg.com/cap_union_new_show?aid=1"></iframe></div>
        <script>
          addEventListener('message', event => {
            if (event.data === 'ykt-ok') document.getElementById('ykt-check').style.display = 'none';
          });
        </script>'''

        def serve(route):
            if "turing.captcha.gtimg.com" in route.request.url:
                route.fulfill(body=vendor, content_type="text/html; charset=utf-8")
            else:
                route.fulfill(body=shell, content_type="text/html; charset=utf-8")

        page.context.route("**/*", serve)
        page.goto("https://school.yuketang.cn/ai-workspace/lms-graph/100001/exercise/1")
        page.wait_for_timeout(300)
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        frame, info = bypass._detection(page)
        self.assertIsNotNone(info, "cross-origin slider was not detected")
        self.assertEqual(info["kind"], "slider")
        self.assertIn("turing.captcha.gtimg.com", frame.url)
        outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "cleared", outcome)
        self.assertTrue(any(f.evaluate("() => !!window.__solved") for f in page.frames
                            if "turing.captcha" in f.url))

    def test_off_mode_does_nothing(self):
        page = self.page(CONFIRM)
        owner = FakeOwner(verification_bypass="off")
        bypass = VerificationBypass(owner)
        self.assertEqual(bypass.handle(page)["state"], "off")
        self.assertEqual(page.evaluate("() => window.__clicked || 0"), 0)

    def test_unknown_structure_falls_back(self):
        page = self.page("<div role='dialog' style='width:300px;height:200px'><h3>请完成安全核验</h3></div>")
        owner = FakeOwner()
        bypass = VerificationBypass(owner)
        first = bypass.handle(page)
        self.assertEqual(first["state"], "retry", first)
        self.assertTrue(bypass.holds_foreground(page))
        bypass.page_state[id(page)]["cooldown"] = 0
        outcome = bypass.handle(page)
        self.assertEqual(outcome["state"], "unsupported", outcome)
        self.assertFalse(bypass.holds_foreground(page))

    def test_attempts_are_capped(self):
        page = self.page(SLIDER.replace("<= 4", "<= -1"))
        owner = FakeOwner(bypass_attempts=1)
        bypass = VerificationBypass(owner)
        self.assertIn(bypass.handle(page)["state"], {"retry", "exhausted"})
        bypass.page_state[id(page)]["cooldown"] = 0
        self.assertEqual(bypass.handle(page)["state"], "exhausted")


class GateIntegrationTests(unittest.TestCase):
    """The homework state machine must resume by itself after an auto-clear."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_homework_gate_resumes_after_slider_is_cleared(self):
        from tests.test_homework import HEADER, SINGLE
        from yuketang_helper.homework import HomeworkSession
        context = self.browser.new_context()
        page = context.new_page()
        self.addCleanup(context.close)
        hidden = SLIDER.replace('id="tcaptcha_transform_dy"',
                                'id="tcaptcha_transform_dy" style="display:none"')
        body = HEADER + SINGLE + hidden
        context.route("**/*", lambda route: route.fulfill(body=body, content_type="text/html; charset=utf-8"))
        url = "https://school.yuketang.cn/ai-workspace/lms-graph/100001/exercise/1?node_id=1"
        page.goto(url)
        events = []
        owner = SimpleNamespace(
            context=context,
            config=Config(course_url="https://school.yuketang.cn/ai-workspace/lms-graph/100001/video/1"),
            mode="homework", owned_page=page)
        owner.emit = lambda kind, data: events.append((kind, data))
        owner.set_verification_page = lambda target: None
        owner.clear_verification_page = lambda: None
        owner.window_state = lambda target, state: None
        owner.bypass = VerificationBypass(owner)
        session = HomeworkSession(owner)
        self.addCleanup(session.close)
        session.page = page
        page.evaluate('''() => {
          const box = document.getElementById('tcaptcha_transform_dy');
          box.style.display = 'block';
        }''')
        self.assertIsNotNone(session._verification_state(), "fixture challenge not detected")
        blocked = session._gate_verification()
        self.assertTrue(blocked)
        self.assertEqual(owner.bypass.total_cleared, 1)
        self.assertTrue(page.evaluate("() => window.__solved"))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and session.verification_wait:
            session._gate_verification()
            page.wait_for_timeout(120)
        self.assertIsNone(session.verification_wait)
        self.assertIn("homework", [kind for kind, _ in events])
        self.assertIn("resumed", [data.get("state") for kind, data in events if kind == "homework"])


if __name__ == "__main__":
    unittest.main()
