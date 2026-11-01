"""Export an explicit source allowlist, without initializing Git or uploading."""
from datetime import datetime
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {'.gitignore', '.gitattributes', 'AGENTS.md', 'README.md', 'ROADMAP.md', 'app.py',
       'start.ps1', '启动雨课堂助手.cmd', 'requirements.txt', 'requirements-build.txt',
       'config.example.json', 'model-settings.example.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md'}
DIRS = {'yuketang_helper', 'tests', 'tools', 'packaging', 'docs'}
PATTERNS = [r'\bsk-[A-Za-z0-9_-]{20,}', r'\bgh[pousr]_[A-Za-z0-9]{25,}',
            r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']


def candidates():
    paths = [ROOT / name for name in TOP if (ROOT / name).is_file()]
    for name in sorted(DIRS):
        paths.extend((ROOT / name).rglob('*'))
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        if path.is_symlink() or not path.is_file() or '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        if len(relative.parts) == 1 and path.name in TOP:
            yield path
        elif relative.parts[0] in DIRS and path.suffix.lower() in {'.py', '.js', '.ps1', '.spec', '.md', '.png', '.ico'}:
            yield path


def main():
    files = list(candidates())
    findings = []
    for path in files:
        if path.suffix.lower() in {'.png', '.ico'}:
            continue
        text = path.read_text(encoding='utf-8-sig')
        if any(re.search(pattern, text) for pattern in PATTERNS):
            findings.append(str(path.relative_to(ROOT)))
    if findings:
        # Report names only; never echo a matched credential into terminal output.
        raise SystemExit('Review potential credentials in: ' + ', '.join(findings))
    out = ROOT / 'dist' / ('source-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(out / 'YuketangHelper-source.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    (out / 'manifest.json').write_text(json.dumps({
        'files': [path.relative_to(ROOT).as_posix() for path in files],
        'credential_pattern_matches': findings, 'manual_review_required': True,
        'license_selected': (ROOT / 'LICENSE').exists(),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Exported {len(files)} files to {out}')


if __name__ == '__main__':
    main()
