#!/usr/bin/env python3
"""Cache the public LCEDA/EasyEDA symbols for the PCB's exact supplier parts.

Read-only network requests; only a new local cache is written. No account,
project upload, library publication, or existing design modification.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'PCB_DeskBuddy_SMD_v1.0_FIXED_2026-09-19.json'
DESTINATION = HERE / 'DeskBuddy_Schematic_Symbol_Sources.json'


def main():
    if DESTINATION.exists():
        raise SystemExit('Cache already exists; refusing to replace the reviewed symbol snapshot.')
    parts = set()
    for shape in json.loads(SOURCE.read_text())['shape']:
        if shape.startswith('LIB~'):
            a = shape.split('#@$')[0].split('~')[3].split('`')
            params = dict(zip(a[::2], a[1::2]))
            parts.add(params['Supplier Part'])

    def fetch(part):
        url = f'https://lceda.cn/api/products/{part}/components?version=6.5.51'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=40) as response:
            raw = response.read()
        result = json.loads(raw)
        if not result.get('success'):
            raise ValueError(f'Cannot load {part}: {result}')
        r = result['result']
        data = r['dataStr']
        if isinstance(data, str):
            data = json.loads(data)
        record = dict(url=url, response_sha256=hashlib.sha256(raw).hexdigest(),
                      title=r['title'], uuid=r['uuid'], updated_at=r.get('updated_at'),
                      data=data, footprint_uuid=r['packageDetail']['uuid'])
        return part, record

    with ThreadPoolExecutor(max_workers=4) as pool:
        symbols = dict(pool.map(fetch, sorted(parts)))
    output = dict(retrieved_utc=datetime.now(timezone.utc).isoformat(), symbols=symbols)
    DESTINATION.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
    for part, record in symbols.items():
        pins = []
        for shape in record['data']['shape']:
            if shape.startswith('P~'):
                groups = [g.split('~') for g in shape.split('^^')]
                pins.append((groups[0][3], groups[3][4], groups[0][4:7]))
        print(part, record['title'], 'pin count', len(pins), 'pins', pins)
        print('shape types', sorted({s.split('~')[0] for s in record['data']['shape']}))


if __name__ == '__main__':
    main()
