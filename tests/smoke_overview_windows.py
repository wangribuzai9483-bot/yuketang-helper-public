"""Use only synthetic pages and a temporary, unsigned-in Edge profile."""
from pathlib import Path
import json,queue,tempfile
from unittest.mock import patch
from tests.test_overview_entry import chapter,leaf,CHAPTER_SCRIPT,OVERVIEW,VIDEO
from tests.test_player import FIXTURE,media_url
from tests.smoke_video_recovery_windows import wait_event
from yuketang_helper.config import Config
from yuketang_helper.engine import Engine

output=Path('outputs/overview-entry-20261101');output.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='ykt-overview-') as folder,patch('yuketang_helper.engine.RUNTIME',Path(folder)):
    engine=Engine(Config(course_name='旧名称示例',course_url=OVERVIEW,verification_bypass='off',stealth=False),queue.Queue())
    def entry():
        def route(request):
            if '/video/' in request.request.url:
                html=FIXTURE.replace('我的课程','大纲入口演示课程')+'<script>document.querySelector("video").src='+json.dumps(media_url(24))+'</script>'
            else:
                html='<meta charset="utf-8"><h1>课程大纲示例（虚构数据）</h1>'+chapter()+CHAPTER_SCRIPT+'<script>window.leafMarkup='+json.dumps(leaf('考试','window.bad=true')+leaf(action="window.open('"+VIDEO+"')"))+'</script>'
            request.fulfill(body=html,content_type='text/html; charset=utf-8')
        engine.context.route('**/*',route);engine.owned_page.goto(OVERVIEW)
        engine.owned_page.screenshot(path=str(output/'overview-example.png'))
    engine._open_entry=entry
    original_poll=engine._poll
    saved=[]
    def poll():
        original_poll()
        if engine.active_page and '/video/' in engine.active_page.url and not saved:
            engine.active_page.screenshot(path=str(output/'player-example.png'));saved.append(True)
    engine._poll=poll;engine.start()
    try:
        result=wait_event(engine,lambda e:e.kind=='status' and e.data.get('state')=='playing' and e.data.get('time',0)>.5)
        before=result.data['time'];engine.command('minimize')
        wait_event(engine,lambda e:e.kind=='window' and e.data=='minimize')
        after=wait_event(engine,lambda e:e.kind=='status' and e.data.get('time',0)>before+1)
        assert after.data['rate']==2 and after.data['duration']>0
        engine.command('stop');engine.thread.join(12)
        assert engine.finished.is_set() and not engine.thread.is_alive()
        print('Overview chapter -> video popup -> actual 2x playback -> minimized time advances -> clean stop OK.')
    finally:
        engine.command('stop');engine.thread.join(12)
