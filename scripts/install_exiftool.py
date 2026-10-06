"""Install a pinned, integrity-checked Perl ExifTool distribution in this project."""
from pathlib import Path
import base64
import hashlib
import io
import shutil
import tarfile
import tempfile
import urllib.request

VERSION = '13.59.3'
URL = f'https://registry.npmjs.org/exiftool-vendored.pl/-/exiftool-vendored.pl-{VERSION}.tgz'
INTEGRITY = 'QmYq2JKCXRK9Uo3E3nHb0gQ6Xce2HK7wPtrHF5r+IYWyiM5WkOhiT3BesLI9lvJvPAbzjqFxQlrjXv+/cuSX/Q=='
if not shutil.which('perl'):
    raise SystemExit('This distribution needs Perl. On Windows, install ExifTool for Windows and add it to PATH.')
root = Path(__file__).resolve().parents[1]
with urllib.request.urlopen(URL, timeout=60) as response:
    data = response.read()
if base64.b64encode(hashlib.sha512(data).digest()).decode() != INTEGRITY:
    raise SystemExit('ExifTool download integrity mismatch')
with tempfile.TemporaryDirectory(dir=root) as staging:
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        archive.extractall(staging, filter='data')
    destination = root / '.tools' / 'exiftool'
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():
        raise SystemExit(f'{destination} already exists; leaving it unchanged.')
    shutil.move(str(Path(staging) / 'package'), destination)
print(f'Installed ExifTool in {destination}; upstream licenses are included.')
