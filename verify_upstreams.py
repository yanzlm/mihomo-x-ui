"""CI-only compatibility smoke test. Downloads pinned Linux artifacts to a temp dir.

Does not install services, change firewall rules or issue certificates.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

import stack
from integrate_dashboard import integrate


def verify():
    if platform.system()!='Linux' or platform.machine()!='x86_64':
        raise SystemExit('Run this smoke test on Linux amd64')
    lock=json.loads((stack.SOURCE/'deploy/versions.json').read_text())
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        for key in ['3x-ui','mihomo']:
            stack.download(lock['components'][key]['assets']['amd64'],root/key)
        stack.download(lock['components']['metacubexd'],root/'dashboard.zip')
        stack.download(lock['components']['3x-ui']['menu'],root/'menu.sh')
        subprocess.run(['bash','-n',str(root/'menu.sh')],check=True)
        stack.extract_tar(root/'3x-ui',root/'xui')
        package=root/'xui/x-ui'
        xui=package/'x-ui';xui.chmod(0o755)
        env=dict(os.environ,XUI_DB_FOLDER=str(root/'db'),XUI_LOG_FOLDER=str(root/'log'),XUI_BIN_FOLDER=str(package/'bin'))
        (root/'db').mkdir();(root/'log').mkdir()
        subprocess.run([str(xui),'setting','-username','smoke-only','-password','temporary-test-only',
                        '-port','2053','-listenIP','127.0.0.1','-webBasePath','/smoke-test/'],
                       env=env,cwd=package,check=True)
        args=argparse.Namespace(panel_domain='panel.example.com',xui_domain='xui.example.com',node_port=24443)
        stack.seed_xui(root/'db/x-ui.db',args)
        with closing(sqlite3.connect(root/'db/x-ui.db')) as db, db:
            assert db.execute('SELECT count(*) FROM users').fetchone()[0]==1
            assert dict(db.execute('SELECT key,value FROM settings'))['subListen']=='127.0.0.1'
        binary=root/'mihomo-core'
        with stack.gzip.open(root/'mihomo','rb') as r:binary.write_bytes(r.read())
        binary.chmod(0o755)
        home=root/'home';home.mkdir()
        dashboard=home/'dashboard';dashboard.mkdir()
        stack.extract_dashboard(root/'dashboard.zip',dashboard)
        assert len(list(dashboard.glob('*/index.html')))==1
        cfg=stack.render_mihomo('ci-smoke-secret')
        cfg['external-ui']=str(next(dashboard.iterdir()))
        assert integrate(Path(cfg['external-ui']),root/'dashboard-backup') is not None
        config=root/'mihomo-config.json';config.write_text(json.dumps(cfg))
        subprocess.run([str(binary),'-t','-d',str(home),'-f',str(config)],check=True)
        # Exercise real rule-provider downloads and the loopback API as well.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        runtime=dict(cfg, **{'external-controller':f'127.0.0.1:{port}','mixed-port':0,'listeners':[],
                            'dns':{'enable':False}})
        config.write_text(json.dumps(runtime))
        with (root/'mihomo.log').open('w') as log:
            proc=subprocess.Popen([str(binary),'-d',str(home),'-f',str(config)],stdout=log,stderr=subprocess.STDOUT)
            try:
                op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(90):
                    try:
                        q=urllib.request.Request(f'http://127.0.0.1:{port}/providers/rules',headers={'Authorization':'Bearer ci-smoke-secret'})
                        with op.open(q,timeout=1) as r:providers=json.load(r)['providers']
                        if all(providers.get(n,{}).get('ruleCount',0)>0 for n in ['cn-domains','cn-ips']):break
                    except OSError:pass
                    if proc.poll() is not None:raise RuntimeError('Mihomo exited during rule-provider check')
                    time.sleep(0.5)
                else:raise RuntimeError('China domain/IP rule providers did not load')
            finally:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        bridge=stack.bridge(24443)
        xray=next((package/'bin').glob('xray-linux-*'));xray.chmod(0o755)
        xcfg={'log':{'loglevel':'warning'},'inbounds':[], 'outbounds':[bridge['outbound']],
              'routing':{'rules':[bridge['rule']]}}
        xfile=root/'xray-test.json';xfile.write_text(json.dumps(xcfg))
        subprocess.run([str(xray),'run','-test','-config',str(xfile)],check=True,cwd=xray.parent)
        print('PASS pinned 3X-UI SQLite initialization, loopback settings, Mihomo config and CN rules, dashboard integration, Xray SOCKS bridge')


if __name__=='__main__':verify()
