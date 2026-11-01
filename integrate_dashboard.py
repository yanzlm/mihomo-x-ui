"""Optionally add a link to a locally installed MetaCubeXD, with backups."""
import argparse
import datetime
import hashlib
from pathlib import Path
import re
import shutil
import uuid

TAG = '<script defer src="/subscriptions/integration.js"></script>'
LIFECYCLE = "/* subscription-manager */self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"


def integrate(ui, backups):
    ui = ui.resolve()
    index, worker = ui / 'index.html', ui / 'sw.js'
    html = index.read_text(encoding='utf8')
    if TAG in html:
        return None
    if '</head>' not in html:
        raise ValueError('No closing head element; refusing to patch unknown dashboard')
    new_html = html.replace('</head>', TAG + '</head>', 1)
    new_worker = None
    if worker.exists():
        body = worker.read_text(encoding='utf8')
        revision = hashlib.md5(new_html.encode()).hexdigest()
        pattern = r'''(url\s*:\s*["']\./["']\s*,\s*revision\s*:\s*["'])[^"']+(["'])'''
        body, count = re.subn(pattern, lambda m: m[1] + revision + m[2], body)
        if count != 1:
            raise ValueError('Unknown service worker precache layout; use the standalone page instead')
        new_worker = LIFECYCLE + body
    folder = backups / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, mode=0o700)
    shutil.copy2(index, folder / 'index.html')
    if worker.exists():
        shutil.copy2(worker, folder / 'sw.js')
    try:
        index.write_text(new_html, encoding='utf8', newline='\n')
        if new_worker is not None:
            worker.write_text(new_worker, encoding='utf8', newline='\n')
    except Exception:
        shutil.copy2(folder / 'index.html', index)
        if (folder / 'sw.js').exists():
            shutil.copy2(folder / 'sw.js', worker)
        raise
    return folder


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-dir', type=Path, required=True)
    parser.add_argument('--backup-dir', type=Path, default=Path('/var/lib/mihomo-subscriptions/dashboard-backups'))
    args = parser.parse_args()
    result = integrate(args.ui_dir, args.backup_dir)
    print('Dashboard already integrated' if result is None else f'Integrated. Restore files from: {result}')
