"""Read-only detection of verification UI, including accessible closed shadow roots."""
from pathlib import Path
import re
from urllib.parse import urldefrag

from playwright.sync_api import Error as BrowserError

VERIFICATION_SCRIPT = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
HEADING = re.compile(r'^(?:请(?:先)?(?:完成|进行|通过))?(?:安全(?:核验|校验|验证)|人机(?:验证|校验))[：:！!。.]?$')
VISIBLE_ELEMENT = """el => {
  const node=el.nodeType===Node.ELEMENT_NODE?el:el.parentElement;
  return !!node && node.getClientRects().length>0 &&
    node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
}"""


def visible_backend(session, backend_id):
    resolved = session.send('DOM.resolveNode', {'backendNodeId': backend_id})
    object_id = resolved.get('object', {}).get('objectId')
    if not object_id:
        return False
    try:
        result = session.send('Runtime.callFunctionOn', {
            'objectId': object_id,
            'functionDeclaration': 'function(){return ('+VISIBLE_ELEMENT+')(this);}',
            'returnByValue': True,
        })
        return result.get('result', {}).get('value') is True
    finally:
        session.send('Runtime.releaseObject', {'objectId': object_id})


def accessible_heading(context, target):
    shared_session = False
    try:
        session = context.new_cdp_session(target)
    except BrowserError:
        # Same-process child frames need their parent's session AND an explicit
        # frameId; otherwise getFullAXTree silently reads only the top document.
        session = context.new_cdp_session(target.page)
        shared_session = True
    try:
        root = session.send('Page.getFrameTree')['frameTree']
        frames = {}

        def collect(node):
            frames[node['frame']['id']] = node['frame']
            for child in node.get('childFrames', []):
                collect(child)

        collect(root)
        root_id = root['frame']['id']
        if shared_session:
            frame_ids = [key for key, value in frames.items() if key != root_id and
                         urldefrag(value.get('url', ''))[0] == urldefrag(target.url)[0]]
            named = [key for key in frame_ids if frames[key].get('name', '') == target.name]
            if named:
                frame_ids = named
        else:
            frame_ids = [root_id]
        for frame_id in frame_ids:
            # Duplicate URLs are common. Verify every iframe ancestor so a hidden
            # stale challenge cannot block a visible, already-cleared instance.
            ancestor = frame_id
            shown = True
            while ancestor != root_id:
                owner = session.send('DOM.getFrameOwner', {'frameId': ancestor})
                if not visible_backend(session, owner['backendNodeId']):
                    shown = False
                    break
                ancestor = frames[ancestor].get('parentId', root_id)
            if not shown:
                continue
            nodes = session.send('Accessibility.getFullAXTree', {'frameId': frame_id}).get('nodes', [])
            for node in nodes:
                if node.get('ignored'):
                    continue
                name = re.sub(r'\s+', '', str(node.get('name', {}).get('value', '')))
                if HEADING.fullmatch(name) and node.get('backendDOMNodeId') and visible_backend(session, node['backendDOMNodeId']):
                    return True
        return False
    finally:
        session.detach()


def verification_state(page):
    uncertain = False
    visible_frames = []
    for frame in page.frames:
        try:
            ancestor = frame
            shown = True
            while ancestor.parent_frame:
                if not ancestor.frame_element().evaluate(VISIBLE_ELEMENT):
                    shown = False
                    break
                ancestor = ancestor.parent_frame
            if not shown:
                continue
            visible_frames.append(frame)
            frame.evaluate(VERIFICATION_SCRIPT)
            if frame.evaluate('window.__yktVerification.read().required'):
                return {'required': True, 'source': 'visible-heading', 'uncertain': False}
        except BrowserError:
            uncertain = True
    # Accessibility exposes visible headings even when their component uses a
    # closed shadow root. Only the heading and its visibility are inspected.
    for target in [page, *[frame for frame in visible_frames if frame != page.main_frame]]:
        try:
            if accessible_heading(page.context, target):
                return {'required': True, 'source': 'accessible-heading', 'uncertain': False}
        except BrowserError:
            pass
    return {'required': False, 'source': '', 'uncertain': uncertain}
