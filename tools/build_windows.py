"""Build a fresh windowed Windows distribution without copying local user data."""
from datetime import datetime
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build this Windows executable on Windows.')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output = ROOT / 'dist' / stamp
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--distpath', str(output),
                    '--workpath', str(ROOT / 'build' / stamp),
                    str(ROOT / 'packaging' / 'YuketangHelper.spec')], cwd=ROOT, check=True)
    bundle = output / 'YuketangHelper'
    # Distribution excludes all source-tree configs, captures and browser profiles.
    for path in bundle.rglob('*'):
        if path.name in {'.runtime', 'config.json', 'model-settings.json', 'startup-error.log'}:
            raise RuntimeError(f'Private data unexpectedly included: {path.name}')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'DISCLAIMER.md'):
        source = ROOT / name
        if not source.is_file():
            raise RuntimeError(f'Required distribution notice is missing: {name}')
        shutil.copy2(source, bundle / name)
    licenses = bundle / 'third-party-licenses'
    for name in ('playwright', 'Pillow', 'greenlet', 'pyee', 'typing_extensions', 'PyInstaller'):
        distribution = metadata.distribution(name)
        for file in distribution.files or []:
            if '.dist-info/licenses/' in str(file).replace('\\', '/'):
                target = licenses / name / Path(file).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(file), target)
    for label, file in [('Python', Path(sys.base_prefix) / 'LICENSE.txt'),
                        ('Tcl', Path(sys.base_prefix) / 'tcl/tcl8.6/license.terms'),
                        ('Tk', Path(sys.base_prefix) / 'tcl/tk8.6/license.terms')]:
        if file.exists():
            licenses.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, licenses / (label + '-LICENSE.txt'))
    (bundle / '使用说明.txt').write_text(
        '雨课堂 · 学习助手\n\n解压整个文件夹后双击“雨课堂学习助手.exe”。\n'
        '无需安装 Python；请先安装 Microsoft Edge，默认使用本机 Edge。\n'
        '必须保留 _internal 文件夹，不能只复制 exe。\n'
        '首次使用请设置课程链接、课程名及模型服务，自行完成网页登录。\n'
        '配置、密钥和学习记录保存在 %LOCALAPPDATA%\\YuketangHelper。\n'
        '源码版数据不会自动导入；不要把个人数据文件放进分享包。\n'
        '窗口兼容问题可附加 --native-titlebar 启动。\n'
        '此测试包尚未签名，当前功能实测范围请参阅项目说明。\n', encoding='utf-8-sig')
    archive = Path(shutil.make_archive(str(output / 'YuketangHelper-Windows-x64'), 'zip', output, 'YuketangHelper'))
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    checksum = digest.hexdigest()
    archive.with_suffix('.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='ascii')
    record = {'bundle': str(bundle), 'exe': str(bundle / '雨课堂学习助手.exe'),
              'archive': str(archive), 'sha256': checksum, 'architecture': platform.machine()}
    (ROOT / 'dist' / 'latest-build.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
