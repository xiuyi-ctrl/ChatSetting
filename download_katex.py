"""Copy KaTeX browser assets from the official npm package."""
import io
import tarfile
from pathlib import Path

import requests


version = '0.19.1'
response = requests.get(f'https://registry.npmjs.org/katex/-/katex-{version}.tgz', timeout=25)
response.raise_for_status()
root = Path(__file__).resolve().parent / 'assets' / 'katex'
root.mkdir(parents=True, exist_ok=True)
count = 0
with tarfile.open(fileobj=io.BytesIO(response.content), mode='r:gz') as archive:
    for member in archive.getmembers():
        prefix = 'package/dist/'
        if not member.isfile() or not member.name.startswith(prefix):
            continue
        relative = member.name[len(prefix):]
        if relative not in ('katex.min.js', 'katex.min.css') and not relative.startswith('fonts/'):
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.extractfile(member).read())
        count += 1
print(f'Copied {count} KaTeX files')
