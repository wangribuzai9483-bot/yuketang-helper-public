import argparse
import json
from pathlib import Path
import sys


def run():
    parser = argparse.ArgumentParser(description="雨课堂学习助手")
    parser.add_argument("--check", action="store_true", help="检查依赖及界面初始化，不打开浏览器")
    parser.add_argument("--check-output", type=Path, help="将检查结果写入指定 JSON 文件")
    parser.add_argument("--native-titlebar", action="store_true", help="回退到 Windows 原生标题栏")
    args = parser.parse_args()
    if args.native_titlebar:
        import os
        os.environ['YUKETANG_NATIVE_TITLEBAR'] = '1'
    from yuketang_helper.gui import App, main
    if args.check:
        import tkinter as tk
        from playwright.sync_api import sync_playwright
        root = tk.Tk()
        root.withdraw()
        app = App(root)
        try:
            root.update_idletasks()
            with sync_playwright() as playwright:
                assert playwright.chromium.name == 'chromium'
            if args.check_output:
                args.check_output.write_text(json.dumps({'ok': True, 'gui': True,
                    'playwright_driver': True, 'frozen': bool(getattr(sys, 'frozen', False))}), encoding='utf-8')
        finally:
            app.homework_panel.close()
            app.learning.close()
            root.destroy()
        if sys.stdout is not None:
            print("Dependencies, GUI and Playwright driver OK.")
    else:
        main()

if __name__ == "__main__":
    try:
        run()
    except Exception:
        if not getattr(sys, 'frozen', False):
            raise
        import traceback
        import os
        import ctypes
        folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'YuketangHelper'
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / 'startup-error.log'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None, f'程序启动失败。错误详情保存在：\n{log}', '雨课堂 · 学习助手', 0x10)
        sys.exit(1)
