#!/usr/bin/env python3
"""Install/uninstall only the managed RAW2LEICA darktable files."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '-- BEGIN RAW2LEICA managed plugin'
END = '-- END RAW2LEICA managed plugin'


def lua_string(value):
    # JSON string escapes are Lua compatible for ordinary paths; encode controls
    # with Lua decimal escapes, including tabs/newlines in unusual filenames.
    return '"' + ''.join('\\' + c if c in '\\"' else f'\\{ord(c):03d}' if ord(c) < 32 else c for c in str(value)) + '"'


def remove_block(text):
    if BEGIN not in text:
        return text
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(END) < text.index(BEGIN):
        raise RuntimeError('luarc 的 RAW2LEICA 管理区块异常；请检查文件后再安装')
    start = text.index(BEGIN)
    finish = text.index(END) + len(END)
    if text[finish:finish+1] == '\n':
        finish += 1
    return text[:start] + text[finish:]


def migrate_storage(config: Path):
    """Retire our old storage without changing native paths or other settings."""
    rc = config / 'darktablerc'
    if not rc.exists():
        return
    original = rc.read_text(encoding='utf-8')
    updated = re.sub(r'^plugins/lighttable/export/storage_name=raw2leica$',
                     'plugins/lighttable/export/storage_name=disk', original, flags=re.M)
    if updated != original:
        backup = config / 'darktablerc.raw2leica-backup'
        if not backup.exists():
            shutil.copy2(rc, backup)
        rc.write_text(updated, encoding='utf-8')


def install(config: Path, exiftool: Path | None = None, uninstall=False):
    config = config.expanduser().resolve()
    runtime = config / 'raw2leica-runtime'
    lua_dir = config / 'lua'
    luarc = config / 'luarc'
    original = luarc.read_text(encoding='utf-8') if luarc.exists() else ''
    clean = remove_block(original)
    if uninstall:
        if luarc.exists():
            luarc.write_text(clean, encoding='utf-8')
        for file in ('raw2leica.lua', 'raw2leica_config.lua'):
            (lua_dir / file).unlink(missing_ok=True)
        if runtime.exists():
            shutil.rmtree(runtime)
        return
    if sys.version_info < (3, 11):
        raise RuntimeError('需要 Python 3.11+')
    if not exiftool:
        candidates = [shutil.which('exiftool'), '/opt/homebrew/bin/exiftool',
                      ROOT / '.tools/exiftool/bin/exiftool']
        exiftool = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
    if not exiftool or not exiftool.is_file():
        raise RuntimeError('请先安装 ExifTool：brew install exiftool，或使用 --exiftool 指定路径')
    exiftool = exiftool.resolve()
    subprocess.run([str(exiftool), '-ver'], check=True, capture_output=True)
    runtime.mkdir(parents=True, exist_ok=True)
    environment = runtime / 'venv'
    if not (environment / 'bin/python').exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', 'Pillow>=12,<13', 'pillow-heif>=1,<2'], check=True)
    package = runtime / 'raw2leica'
    package.mkdir(exist_ok=True)
    for filename in ('__init__.py', 'metadata.py', 'exif_export.py', 'heif_data.py'):
        shutil.copyfile(ROOT / 'raw2leica' / filename, package / filename)
    shutil.copytree(ROOT / 'raw2leica/profiles', package / 'profiles', dirs_exist_ok=True)
    # Copy ExifTool with its libraries/licenses when using the project-local vendor.
    vendored = ROOT / '.tools/exiftool'
    if vendored.resolve() in exiftool.parents:
        shutil.copytree(vendored, runtime / 'exiftool', dirs_exist_ok=True)
        exiftool = runtime / 'exiftool/bin/exiftool'
    launcher = runtime / 'raw2leica-exif'
    launcher.write_text('#!' + str(python) + '\n' +
        'import os, sys\n' + f'os.environ["EXIFTOOL_PATH"] = {str(exiftool)!r}\n' +
        f'sys.path.insert(0, {str(runtime)!r})\n' +
        'from raw2leica.exif_export import main\nraise SystemExit(main())\n', encoding='utf-8')
    launcher.chmod(0o755)
    # Python shebangs cannot contain spaces. Use a shell wrapper and an argv-safe
    # Python bootstrap for the interpreter path (including custom config paths).
    bootstrap = runtime / 'entry.py'
    bootstrap.write_text(launcher.read_text().split('\n', 1)[1], encoding='utf-8')
    import shlex
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' ' +
                        shlex.quote(str(bootstrap)) + ' "$@"\n', encoding='utf-8')
    subprocess.run([str(launcher), '--check'], check=True)
    result = subprocess.run([str(launcher), '--profiles'], check=True, capture_output=True, text=True)
    profiles = json.loads(result.stdout)['profiles']
    lua_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'darktable/raw2leica.lua', lua_dir / 'raw2leica.lua')
    generated = 'return {backend=' + lua_string(launcher) + ', profiles={\n'
    generated += ''.join('{id=' + lua_string(p['id']) + ', label=' + lua_string(p['display_name']) + '},\n' for p in profiles)
    (lua_dir / 'raw2leica_config.lua').write_text(generated + '}}\n', encoding='utf-8')
    backup = config / "luarc.raw2leica-backup"
    if luarc.exists() and not backup.exists():
        shutil.copy2(luarc, backup)
    block = BEGIN + '\npackage.path = package.path .. ";" .. ' + lua_string(lua_dir / '?.lua') + '\nrequire "raw2leica"\n' + END + '\n'
    luarc.write_text(clean + ('' if not clean or clean.endswith('\n') else '\n') + block, encoding='utf-8')
    migrate_storage(config)


def main():
    parser = argparse.ArgumentParser(description='安装 macOS darktable Leica EXIF 导出插件')
    parser.add_argument('--config-dir', type=Path, default=Path.home() / '.config/darktable')
    parser.add_argument('--exiftool', type=Path)
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('首版安装器仅支持 macOS')
    install(args.config_dir, args.exiftool, args.uninstall)
    print('已卸载插件。' if args.uninstall else '插件已安装；请重启 darktable，在 Leica EXIF 面板启用后处理，使用原生文件存储导出。')


if __name__ == '__main__':
    main()
