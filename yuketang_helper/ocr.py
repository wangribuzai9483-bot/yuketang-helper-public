"""Read the rendered text, rather than encoded font character codes, locally."""
import copy
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from .config import RUNTIME


class OcrError(ValueError):
    pass


def _raw_result(path: Path):
    script = Path(__file__).with_name("windows_ocr.ps1")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-ImagePath", str(path.resolve()),
        ], capture_output=True, timeout=30, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise OcrError("本机文字识别超时，请恢复浏览器后重新识别当前题。") from None
    except OSError:
        raise OcrError("无法启动 Windows 本机文字识别。") from None
    if result.returncode:
        raise OcrError("本机中文文字识别失败，请确认 Windows 已安装简体中文 OCR 语言组件。")
    try:
        data = json.loads(result.stdout.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise OcrError("未识别到文字，请恢复浏览器，确保题干已加载。") from None
    return data if isinstance(data, dict) else {}


def recognize_boxes(path: Path):
    """OCR words with pixel rectangles, used to locate captcha dots drawn on a canvas."""
    data = _raw_result(path)
    words = data.get("words") or []
    if isinstance(words, dict):
        words = [words]
    scale = float(data.get("scale") or 1) or 1.0
    origin_width = int(data.get("origin_width") or 0)
    origin_height = int(data.get("origin_height") or 0)
    boxes = []
    for word in words:
        if not isinstance(word, dict):
            continue
        try:
            boxes.append({
                "text": str(word.get("text") or ""),
                "x": float(word.get("x") or 0) / scale,
                "y": float(word.get("y") or 0) / scale,
                "width": float(word.get("w") or 0) / scale,
                "height": float(word.get("h") or 0) / scale,
            })
        except (TypeError, ValueError):
            continue
    return {"words": boxes, "width": origin_width, "height": origin_height}


def recognize_image(path: Path):
    data = _raw_result(path)
    try:
        value = data["text"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise OcrError("未识别到文字，请恢复浏览器，确保题干已加载。") from None
    value = re.sub(r"\s+", " ", value).strip()
    # Windows OCR inserts word separators between individual Chinese characters.
    value = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)
    value = re.sub(r"\s*([，。；：、？！《》（）])\s*", r"\1", value)
    return value


def capture_rendered(frame, selector, path, tight=True):
    """Rasterize glyphs at higher density; resizing a 16px screenshot loses strokes."""
    locator = frame.locator(selector)
    locator.scroll_into_view_if_needed(timeout=15000)
    box = locator.bounding_box()
    if not box or min(box["width"], box["height"]) <= 0:
        raise OcrError("题干区域不可见，请恢复浏览器后重试。")
    if tight:
        # A short checkbox option may live in a full-width flex row. Crop actual
        # text bounds so OCR does not downscale four characters in a huge image.
        bounds = locator.evaluate('''el => {
          const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),rects=[];
          let node;
          while(node=walker.nextNode()) {
            if(!node.textContent.trim()) continue;
            const parent=node.parentElement;
            if(!parent?.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) continue;
            const range=document.createRange();range.selectNodeContents(node);
            for(const r of range.getClientRects()) if(r.width && r.height) rects.push(r);
          }
          if(!rects.length) return null;
          const own=el.getBoundingClientRect();
          const x=Math.min(...rects.map(r=>r.left)),y=Math.min(...rects.map(r=>r.top));
          return {x:x-own.x,y:y-own.y,own_width:own.width,own_height:own.height,width:Math.max(...rects.map(r=>r.right))-x,
            height:Math.max(...rects.map(r=>r.bottom))-y};
        }''')
        if bounds:
            sx = box['width'] / bounds['own_width'] if bounds['own_width'] else 1
            sy = box['height'] / bounds['own_height'] if bounds['own_height'] else 1
            box = {'x': box['x']+bounds['x']*sx, 'y': box['y']+bounds['y']*sy,
                   'width': bounds['width']*sx, 'height': bounds['height']*sy}
    page = frame.page
    scroll = page.evaluate("({x:window.scrollX,y:window.scrollY})")
    clip = {
        "x": max(0, box["x"] + scroll["x"] - 10),
        "y": max(0, box["y"] + scroll["y"] - 10),
        "width": box["width"] + 20, "height": box["height"] + 20,
        "scale": min(3, 3800 / max(box["width"] + 20, box["height"] + 20)),
    }
    session = page.context.new_cdp_session(page)
    try:
        result = session.send("Page.captureScreenshot", {
            "format": "png", "fromSurface": True, "captureBeyondViewport": True, "clip": clip,
        })
        path.write_bytes(base64.b64decode(result["data"]))
    finally:
        session.detach()


class RenderedTextReader:
    def __init__(self, recognize=recognize_image, notify=None, blocked=None):
        self.recognize = recognize
        self.notify = notify or (lambda: None)
        self.cache = {}
        self.blocked = blocked or (lambda: False)

    def resolve(self, frame, question):
        if not question.get("needs_ocr"):
            return question
        cache_key = (frame, question.get("document_id"), question["fingerprint"])
        if cache_key not in self.cache:
            self.notify()
            frame.evaluate("async () => { if (document.fonts) await Promise.race([document.fonts.ready,new Promise(resolve=>setTimeout(resolve,3000))]); }")
            targets = frame.evaluate("fingerprint => window.__yktHomework.ocrTargets(fingerprint)", question["fingerprint"])
            if not targets:
                raise OcrError("题面已切换或没有找到可见文字区域，请重新识别。")
            RUNTIME.mkdir(exist_ok=True)
            values = {}
            with tempfile.TemporaryDirectory(prefix="ocr-", dir=RUNTIME) as folder:
                for i, target in enumerate(targets):
                    path = Path(folder) / f"text-{i}.png"
                    if self.blocked() or frame.evaluate('window.__yktHomework.securityState().required'):
                        raise OcrError('安全核验正在显示，文字识别已暂停。')
                    # Allow the compositor to paint newly mounted option rows.
                    frame.evaluate('''() => new Promise(resolve=>{
                      const timer=setTimeout(resolve,180);
                      requestAnimationFrame(()=>requestAnimationFrame(()=>{clearTimeout(timer);resolve();}));
                    })''')
                    for attempt in range(2):
                        selector = target['selector'] if not attempt else target.get('fallback_selector', target['selector'])
                        try:
                            if self.blocked():
                                raise OcrError('安全核验正在显示，文字识别已暂停。')
                            capture_rendered(frame, selector, path, tight=not attempt)
                            if self.blocked():
                                raise OcrError('安全核验正在显示，文字识别已暂停。')
                            values[target["key"]] = self.recognize(path)
                            break
                        except OcrError:
                            if self.blocked():
                                raise OcrError('安全核验正在显示，文字识别已暂停。') from None
                            if not attempt:
                                frame.page.wait_for_timeout(180)
                                continue
                            failure_folder = RUNTIME / 'ocr-failures'
                            failure_folder.mkdir(exist_ok=True)
                            field = re.sub(r'[^a-zA-Z0-9_-]', '-', target['key'])
                            failure = failure_folder / f'{time.time_ns()}-{field}.png'
                            if path.exists():
                                failure.write_bytes(path.read_bytes())
                            label = '题干' if field == 'stem' else '选项 '+target['key'].split(':',1)[-1]
                            detail = f'已保留本机截图：{failure}' if failure.exists() else '对应区域尚未完成渲染，请重新识别或导出题面样本。'
                            raise OcrError(f'{label}文字识别失败，{detail}') from None
            latest = frame.evaluate("() => window.__yktHomework.read()")
            if not latest or latest["fingerprint"] != question["fingerprint"] or latest.get("document_id") != question.get("document_id"):
                raise OcrError("文字识别期间题目已切换，未使用旧题面。")
            if len(self.cache) >= 128:
                self.cache.pop(next(iter(self.cache)))
            self.cache[cache_key] = values
        # Cache only text: keep current checked/disabled/draft state from the page.
        result = copy.deepcopy(question)
        values = self.cache[cache_key]
        fields = question.get("ocr_fields", {})
        if fields.get("stem"):
            if not values.get("stem"):
                raise OcrError("题干文字识别未完成，已停止调用模型。")
            result["stem"] = values["stem"]
        for option in result["options"]:
            if option["id"] in fields.get("options", []):
                key = "option:" + option["id"]
                if not values.get(key):
                    raise OcrError("选项文字识别未完成，已停止调用模型。")
                option["text"] = values[key]
        result["needs_ocr"] = False
        result["text_source"] = "windows-ocr"
        return result
