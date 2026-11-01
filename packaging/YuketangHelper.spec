# Only application resources are collected; never collect the project root.
from pathlib import Path

root = Path(SPECPATH).parent
package = root / 'yuketang_helper'
datas = [(str(path), 'yuketang_helper') for suffix in ('*.js', '*.ps1')
         for path in package.glob(suffix)]
datas.append((str(package / 'assets'), 'yuketang_helper/assets'))

a = Analysis([str(root / 'app.py')], pathex=[str(root)], binaries=[], datas=datas,
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'unittest', 'pip', 'setuptools'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='雨课堂学习助手',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(package / 'assets' / 'ui-v2' / 'document.png'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='YuketangHelper')
