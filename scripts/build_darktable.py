#!/usr/bin/env python3
"""Build the lightweight darktable plugin distribution, without desktop dependencies."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0'


def main():
    name = f'RAW2LEICA-darktable-{VERSION}-macos'
    folder = ROOT / 'dist' / 'darktable'
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / f'{name}.zip'
    files = [ROOT / p for p in (
        'darktable/raw2leica.lua', 'scripts/install_darktable.py',
        'raw2leica/__init__.py', 'raw2leica/metadata.py',
        'raw2leica/exif_export.py', 'raw2leica/heif_data.py',
        'docs/darktable-plugin.md', 'docs/darktable-validation.md')]
    files += sorted((ROOT / 'raw2leica/profiles').glob('*.json'))
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
        raise RuntimeError('请先提交改动，再构建可发布插件包')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, f'{name}/{path.relative_to(ROOT)}')
        bundle.write(ROOT / 'docs/release-darktable-v0.1.0.md', f'{name}/README.md')
        bundle.writestr(f'{name}/BUILD-INFO.json', json.dumps(
            {'plugin_version': VERSION, 'commit': commit}, indent=2) + '\n')
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (folder / 'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n', encoding='utf-8')
    print(archive)
    print(f'SHA256: {checksum}')


if __name__ == '__main__':
    main()
