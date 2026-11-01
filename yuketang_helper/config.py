from dataclasses import asdict, dataclass, fields
import json
import math
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

def data_root():
    """Frozen apps keep private state outside the replaceable distribution folder."""
    if getattr(sys, 'frozen', False):
        local = os.environ.get('LOCALAPPDATA')
        return (Path(local) if local else Path.home() / 'AppData' / 'Local') / 'YuketangHelper'
    return Path(__file__).resolve().parent.parent


ROOT = data_root()
RUNTIME = ROOT / ".runtime"


def allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "yuketang.cn" or host.endswith(".yuketang.cn")
    ) and parsed.username is None and parsed.password is None


@dataclass
class Config:
    login_url: str = "https://www.yuketang.cn/web"
    course_name: str = "我的课程"
    course_url: str = ""
    browser: str = "msedge"
    speed: float = 2.0
    muted: bool = True
    auto_next: bool = True
    auto_minimize: bool = False
    resume_paused: bool = True
    stall_seconds: int = 25
    next_delay_seconds: int = 5
    verification_bypass: str = "solve"
    bypass_attempts: int = 3
    bypass_slider_gain: float = 0.0
    stealth: bool = True
    answer_source: str = "auto"

    def validate(self):
        if not allowed_url(self.login_url):
            raise ValueError("入口必须是 https:// 开头的雨课堂网址（yuketang.cn）。")
        if self.course_url and not allowed_url(self.course_url):
            raise ValueError("课程直达地址必须是 https:// 开头的雨课堂网址。")
        if self.browser not in {"msedge", "chrome", "chromium"}:
            raise ValueError("浏览器请选择 msedge、chrome 或 chromium。")
        if not math.isfinite(self.speed) or not 0.5 <= self.speed <= 2:
            raise ValueError("播放速度须在 0.5 到 2.0 之间。")
        if not self.course_name.strip():
            raise ValueError("请填写课程名称。")
        if not 10 <= self.stall_seconds <= 300:
            raise ValueError("卡顿检测时间须在 10 到 300 秒之间。")
        if not 1 <= self.next_delay_seconds <= 30:
            raise ValueError("续播等待须在 1 到 30 秒之间。")
        if str(self.verification_bypass).lower() in {"shim", "both"}:
            # Older configs used the removed response-replacement mode.
            self.verification_bypass = "solve"
        if str(self.verification_bypass).lower() not in {"off", "solve"}:
            raise ValueError("安全核验处理方式请选择 off 或 solve。")
        if not 1 <= int(self.bypass_attempts) <= 6:
            raise ValueError("安全核验自动尝试次数须在 1 到 6 之间。")
        if not math.isfinite(float(self.bypass_slider_gain)) or not 0 <= float(self.bypass_slider_gain) <= 3:
            raise ValueError("滑块拖动比例须在 0 到 3 之间，0 表示自动计算。")
        if str(self.answer_source).lower() not in {"auto", "embedded", "model"}:
            raise ValueError("答案来源请选择 auto（优先题目自带）、embedded（只用自带答案）或 model（只用模型）。")
        for name in ("muted", "auto_next", "auto_minimize", "resume_paused", "stealth"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须是 true 或 false。")
        return self

    def entry_url(self):
        """The official login accepts a relative `next` path on the same campus host."""
        if not self.course_url:
            return self.login_url
        course = urlparse(self.course_url)
        login = urlparse(self.login_url)
        if login.netloc != course.netloc:
            login = course._replace(path="/web/", params="", query="", fragment="")
        next_path = urlunparse(("", "", course.path, course.params, course.query, course.fragment))
        query = dict(parse_qsl(login.query, keep_blank_values=True))
        query["next"] = next_path
        # The campus login checks both `type` and `next`; next alone opens its
        # legacy course selector instead of returning to the requested video.
        query.setdefault("type", "1")
        return urlunparse(login._replace(query=urlencode(query)))

    @classmethod
    def load(cls, path: Path = ROOT / "config.json"):
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("config.json 必须是 JSON 对象。")
        known = {f.name for f in fields(cls)}
        try:
            return cls(**{k: v for k, v in data.items() if k in known}).validate()
        except (TypeError, AttributeError) as exc:
            raise ValueError("config.json 中的配置类型不正确。") from exc

    def save(self, path: Path = ROOT / "config.json"):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def player_options(self):
        return {
            "speed": self.speed,
            "muted": self.muted,
            "resumePaused": self.resume_paused,
            "stallSeconds": self.stall_seconds,
        }
