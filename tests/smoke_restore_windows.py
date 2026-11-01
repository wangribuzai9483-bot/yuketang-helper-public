"""Repeated notice-button restore, without pumping the browser worker, in isolated Edge windows."""
import queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

from playwright.sync_api import sync_playwright

from yuketang_helper.config import Config
from yuketang_helper.engine import Engine
from yuketang_helper.gui import App
from yuketang_helper.windows import WindowTarget


def buttons(widget):
    for child in widget.winfo_children():
        if isinstance(child,ttk.Button):yield child
        yield from buttons(child)


def main():
    with sync_playwright() as playwright, TemporaryDirectory() as profile:
        context=playwright.chromium.launch_persistent_context(profile,channel='msedge',headless=False)
        bystander=playwright.chromium.launch(channel='msedge',headless=False)
        other_context=bystander.new_context()
        root=tk.Tk();root.withdraw();app=App(root)
        try:
            page=context.pages[0];page.set_content('<p>Temporary restore test</p>')
            other=other_context.new_page();other.set_content('<p>Separate browser process</p>')
            engine=Engine(Config(),app.events)
            engine.context=context;engine.owned_page=page;engine.active_page=page
            engine.thread=SimpleNamespace(is_alive=lambda:True)
            engine.remember_window(page)
            assert engine.window_target is not None, 'Persistent browser native handle not found'
            app.engine=engine
            other_cdp=other_context.new_cdp_session(other)
            other_window=other_cdp.send('Browser.getWindowForTarget')
            other_cdp.send('Browser.setWindowBounds',{'windowId':other_window['windowId'],'bounds':{'windowState':'minimized'}})
            cdp=context.new_cdp_session(page)
            for episode in range(5):
                engine.window_state(page,'minimized')
                app.show_verification_notice('Local window restore test; no real verification challenge.')
                root.update_idletasks();app.last_restore_click=-float('inf')
                if episode==0:
                    engine.window_target=WindowTarget(0,engine.browser_pid)
                button=next(button for button in buttons(app.verification_notice) if button.cget('text')=='恢复浏览器')
                button.invoke()
                # Do not run _handle_commands yet: native UI restore must work
                # even when the browser worker is busy or waiting.
                assert engine.pending_window_action=='restore'
                assert cdp.send('Browser.getWindowForTarget')['bounds']['windowState']=='normal'
                assert other_cdp.send('Browser.getWindowForTarget')['bounds']['windowState']=='minimized'
                engine._handle_commands();app.poll()
                assert not app.restore_pending
                app.close_verification_notice()
            cdp.detach();other_cdp.detach()
            print('Persistent Edge restore OK across 5 notice episodes before worker processing; separate browser remained minimized.')
        finally:
            app.close_verification_notice();app.homework_panel.close();root.destroy()
            other_context.close();bystander.close();context.close()


if __name__=='__main__':main()
