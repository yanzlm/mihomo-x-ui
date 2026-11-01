"""Fresh Debian 12/13 deployment of 3X-UI, Mihomo and their HTTPS dashboards.

Without --apply, only prints a deployment plan. Never run on an existing node.
"""
import argparse
from contextlib import closing
import errno
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import tarfile
import time
import urllib.request
import urllib.error
import zipfile

SOURCE = Path(__file__).resolve().parent
APP = Path('/opt/mihomo-subscription-manager')
PRIVATE = Path('/etc/mihomo-x-ui')
XUI = Path('/usr/local/x-ui')
MIHOMO = Path('/usr/local/bin/mihomo')
HOME = Path('/var/lib/mihomo')
CONFIG = Path('/etc/mihomo/config.yaml')
DATA = Path('/var/lib/mihomo-subscriptions')
PORTS = {'https': 443, 'http': 80, 'xui': 2053, 'subscription': 2096,
         'controller': 19091, 'manager': 19092, 'mixed': 7897,
         'relay': 17891, 'xray-api': 62789, 'xray-metrics': 11111}


def domain(value):
    value = value.lower().rstrip('.')
    if len(value) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]*[a-z0-9])?', value):
        raise ValueError('Use an ASCII DNS hostname, such as panel.example.com')
    return value


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--panel-domain', required=True, type=domain)
    p.add_argument('--xui-domain', required=True, type=domain)
    p.add_argument('--server-ip', action='append', required=True, type=ipaddress.ip_address,
                   help='Repeat for all public IPv4/IPv6 addresses of this VPS')
    p.add_argument('--node-port', type=int, default=24443)
    p.add_argument('--ssh-port', type=int, default=22, help='For firewall instructions only')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args(argv)
    if a.panel_domain == a.xui_domain:
        p.error('Use two different DNS hostnames')
    if not 1024 <= a.node_port <= 65535 or a.node_port in PORTS.values():
        p.error('node-port must be an unused high port, distinct from panel ports')
    if not 1 <= a.ssh_port <= 65535 or a.ssh_port in PORTS.values() or a.ssh_port == a.node_port:
        p.error('ssh-port conflicts with a deployment port')
    if any(not ip.is_global for ip in a.server_ip):
        p.error('server-ip must be a real public address (not a documentation address)')
    return a


def render_mihomo(secret):
    providers = {}
    for name, behavior, path in [('cn-domains', 'domain', 'geosite/cn.mrs'), ('cn-ips', 'ipcidr', 'geoip/cn.mrs')]:
        providers[name] = {'type': 'http', 'behavior': behavior, 'format': 'mrs',
                           'path': './rules/'+name+'.mrs', 'interval': 86400, 'proxy': 'DIRECT',
                           'url': 'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/'+path}
    return {'mixed-port': PORTS['mixed'], 'bind-address': '127.0.0.1', 'allow-lan': False,
            'mode': 'rule', 'log-level': 'info', 'ipv6': False,
            'unified-delay': True, 'tcp-concurrent': True,
            'external-controller': '127.0.0.1:'+str(PORTS['controller']), 'secret': secret,
            'external-ui': str(HOME/'ui'), 'profile': {'store-selected': True},
            'sniffer': {'enable': True, 'parse-pure-ip': True,
                        'sniff': {'TLS': {'ports':[443,8443]}, 'HTTP': {'ports':[80,8080]}, 'QUIC': {'ports':[443]}}},
            'listeners': [{'name': 'xray-relay', 'type': 'socks', 'listen': '127.0.0.1', 'port': PORTS['relay'], 'udp': True}],
            'dns': {'enable': True, 'listen': '127.0.0.1:5335', 'ipv6': False,
                    'respect-rules': True, 'enhanced-mode': 'fake-ip', 'fake-ip-range': '198.18.0.1/16',
                    'fake-ip-filter': ['+.lan', '+.local', '+.home.arpa'],
                    'default-nameserver': ['223.5.5.5', '119.29.29.29'],
                    'proxy-server-nameserver': ['https://223.5.5.5/dns-query'],
                    'direct-nameserver': ['https://223.5.5.5/dns-query'],
                    'nameserver': ['https://1.1.1.1/dns-query#PROXY'],
                    'nameserver-policy': {'rule-set:cn-domains': ['https://223.5.5.5/dns-query']}},
            'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'proxies': ['REJECT']}],
            'rule-providers': providers,
            'rules': ['DOMAIN-SUFFIX,lan,DIRECT', 'DOMAIN-SUFFIX,local,DIRECT', 'DOMAIN-SUFFIX,home.arpa,DIRECT',
                      *['IP-CIDR,'+cidr+',DIRECT,no-resolve' for cidr in ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '100.64.0.0/10']],
                      *['IP-CIDR6,'+cidr+',DIRECT,no-resolve' for cidr in ['::1/128', 'fc00::/7', 'fe80::/10']],
                      'RULE-SET,cn-domains,DIRECT', 'RULE-SET,cn-ips,DIRECT,no-resolve', 'MATCH,PROXY']}


def bridge(node_port):
    return {'outbound': {'tag': 'mihomo-relay', 'protocol': 'socks',
                         'settings': {'servers': [{'address': '127.0.0.1', 'port': PORTS['relay']}]}},
            'rule': {'type': 'field', 'inboundTag': ['inbound-'+str(node_port)], 'outboundTag': 'mihomo-relay'}}


def render_caddy(args):
    template = (SOURCE/'examples/Caddyfile.example').read_text(encoding='utf8')
    panel = template[template.index('proxy.example.com {'):].replace('proxy.example.com', args.panel_domain)
    return panel + '\n' + args.xui_domain + ''' {
    header Referrer-Policy no-referrer
    handle /sub/* {
        reverse_proxy 127.0.0.1:2096
    }
    handle {
        reverse_proxy 127.0.0.1:2053
    }
}
'''


def write(path, body, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    path.chmod(mode)


def download(asset, destination):
    if not re.fullmatch(r'[a-f0-9]{64}', asset['sha256']):
        raise ValueError('Missing pinned SHA-256')
    q = urllib.request.Request(asset['url'], headers={'User-Agent': 'mihomo-x-ui-installer/0.2.0'})
    digest = hashlib.sha256()
    with urllib.request.urlopen(q, timeout=60) as r, destination.open('wb') as f:
        size = 0
        while chunk := r.read(1024*1024):
            size += len(chunk)
            if size > 200*1024*1024:
                raise ValueError('Download exceeds size limit')
            digest.update(chunk); f.write(chunk)
    if digest.hexdigest() != asset['sha256']:
        raise ValueError('SHA-256 mismatch; refusing to execute '+destination.name)


def safe_path(name):
    p = Path(name)
    return not p.is_absolute() and '..' not in p.parts and '\\' not in name


def extract_tar(archive, destination):
    with tarfile.open(archive) as t:
        for member in t.getmembers():
            if not safe_path(member.name) or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe archive member')
            member.mode &= 0o777
        # Links and special files were rejected above; keep extraction portable to 3.11.
        if hasattr(tarfile, 'data_filter'):
            t.extractall(destination, filter='data')
        else:
            t.extractall(destination)


def extract_dashboard(archive, destination):
    with zipfile.ZipFile(archive) as z:
        size = 0
        for info in z.infolist():
            if not safe_path(info.filename) or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Unsafe dashboard archive')
            size += info.file_size
            if size > 100*1024*1024:
                raise ValueError('Dashboard expands beyond limit')
        z.extractall(destination)


def seed_xui(dbpath, args):
    """Only called on a freshly initialized, stopped 3X-UI SQLite instance."""
    with closing(sqlite3.connect(dbpath)) as db, db:
        if db.execute('SELECT count(*) FROM inbounds').fetchone()[0]:
            raise ValueError('Refusing to modify an existing node database')
        columns = {r[1] for r in db.execute('PRAGMA table_info(settings)')}
        if not {'key', 'value'} <= columns:
            raise ValueError('Unsupported 3X-UI settings schema')
        values = {'webListen': '127.0.0.1', 'subListen': '127.0.0.1', 'subPort': str(PORTS['subscription']),
                  'subPath': '/sub/', 'subURI': 'https://'+args.xui_domain+'/sub/',
                  'subCertFile': '', 'subKeyFile': '', 'webCertFile': '', 'webKeyFile': ''}
        for key, value in values.items():
            db.execute('DELETE FROM settings WHERE key=?', (key,))
            db.execute('INSERT INTO settings (key,value) VALUES (?,?)', (key,value))


def preflight(args):
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise ValueError('--apply requires root on fresh Debian 12/13')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'debian' or release.get('VERSION_ID', '').strip('"') not in ('12','13'):
        raise ValueError('Fresh-install support is limited to Debian 12/13')
    if not Path('/run/systemd/system').is_dir():
        raise ValueError('A running systemd is required')
    protected = [APP, PRIVATE, XUI, CONFIG.parent, HOME, DATA, Path('/etc/x-ui'),
                 MIHOMO, Path('/etc/caddy/Caddyfile'), Path('/usr/bin/x-ui'),
                 Path('/var/log/x-ui'), Path('/etc/default/x-ui'),
                 *[Path('/etc/systemd/system')/name for name in ['mihomo.service','mihomo-subscriptions.service','x-ui.service']]]
    found = [str(p) for p in protected if p.exists()]
    if found:
        raise ValueError('Existing installation detected; refusing to overwrite: '+', '.join(found))
    for port in list(PORTS.values()) + [args.node_port, 5335]:
        with socket.socket() as sock:
            sock.bind(('0.0.0.0',port))
        if socket.has_ipv6:
            try:
                with socket.socket(socket.AF_INET6) as sock:
                    sock.bind(('::',port))
            except OSError as error:
                if error.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL): raise
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('0.0.0.0',5335))


def run(args, log, **kwargs):
    try:
        subprocess.run(args, check=True, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    except subprocess.CalledProcessError:
        raise RuntimeError('Command failed; see private install.log: '+Path(args[0]).name) from None


def wait_http(url, headers=None):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with op.open(urllib.request.Request(url, headers=headers or {}), timeout=2) as r:
                if r.status == 200: return
        except (OSError, urllib.error.HTTPError):
            pass
        time.sleep(0.5)
    raise RuntimeError('Service did not become ready: '+url)


def apply(args, lock):
    preflight(args)
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if not arch:
        raise ValueError('Only amd64 and arm64 are supported')
    os.umask(0o077)
    PRIVATE.mkdir(mode=0o700)
    staging = PRIVATE/'downloads'; staging.mkdir()
    write(PRIVATE/'deployment.json', {'status': 'started', 'panel_domain': args.panel_domain,
                                     'xui_domain': args.xui_domain, 'node_port': args.node_port, 'versions': lock})
    with (PRIVATE/'install.log').open('w') as log:
        for key in ['3x-ui','mihomo']:
            print('Downloading and verifying '+key, flush=True)
            download(lock['components'][key]['assets'][arch], staging/key)
        download(lock['components']['metacubexd'], staging/'dashboard.zip')
        download(lock['components']['3x-ui']['menu'], staging/'x-ui-menu')
        print('Installing Debian packages and services', flush=True)
        run(['apt-get','update'],log)
        run(['apt-get','install','-y','curl','ca-certificates','python3-venv','caddy','tar','openssl','tzdata','socat','cron','iproute2'], log,
            env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
        run(['systemctl','stop','caddy'], log)
        run(['useradd','--system','--user-group','--home-dir',str(HOME),'--shell','/usr/sbin/nologin','mihomo'],log)
        HOME.mkdir(mode=0o750); shutil.chown(HOME,user='mihomo',group='mihomo')
        (HOME/'managed-subscriptions').mkdir(mode=0o750); shutil.chown(HOME/'managed-subscriptions',user='mihomo',group='mihomo')
        CONFIG.parent.mkdir(mode=0o750); shutil.chown(CONFIG.parent,user='root',group='mihomo')
        DATA.mkdir(mode=0o700)
        with gzip.open(staging/'mihomo','rb') as r, MIHOMO.open('wb') as f: shutil.copyfileobj(r,f)
        MIHOMO.chmod(0o755)
        extracted = staging/'xui'; extracted.mkdir(); extract_tar(staging/'3x-ui',extracted)
        package = extracted/'x-ui'
        if not (package/'x-ui').is_file():
            raise ValueError('Unknown 3X-UI package layout')
        shutil.copytree(package,XUI)
        (XUI/'x-ui').chmod(0o755)
        for binary in (XUI/'bin').glob('*'):
            if binary.is_file() and not binary.name.endswith(('.json','.dat','.mmdb')): binary.chmod(0o755)
        shutil.copy2(staging/'x-ui-menu','/usr/bin/x-ui'); Path('/usr/bin/x-ui').chmod(0o755)
        Path('/etc/x-ui').mkdir(mode=0o700); Path('/var/log/x-ui').mkdir(mode=0o700)
        shutil.copytree(SOURCE,APP,ignore=shutil.ignore_patterns('.git','.venv','__pycache__','artifacts','*.zip','*.tar.gz'))
        APP.chmod(0o755)
        for f in [APP/'web',*(APP/'web').iterdir()]:f.chmod(0o755 if f.is_dir() else 0o644)
        run(['python3','-m','venv',str(APP/'.venv')],log)
        run([str(APP/'.venv/bin/python'),'-m','pip','install','-r',str(APP/'requirements.txt')],log)
        secret = secrets.token_hex(32); username = 'admin-'+secrets.token_hex(4); password = secrets.token_urlsafe(28)
        base_path = '/'+secrets.token_hex(16)+'/'
        write(PRIVATE/'credentials.json', {'mihomo_url':'https://'+args.panel_domain, 'mihomo_secret': secret,
                                          'xui_url':'https://'+args.xui_domain+base_path,
                                          'xui_username':username,'xui_password':password})
        write(CONFIG,render_mihomo(secret),0o640); shutil.chown(CONFIG,user='root',group='mihomo')
        env = (APP/'examples/manager.env.example').read_text().replace('https://proxy.example.com','https://'+args.panel_domain)
        blocked = ','.join([str(ip) for ip in args.server_ip]+[args.panel_domain,args.xui_domain])
        env = re.sub(r'(?m)^M_BLOCKED_HOSTS=.*$', 'M_BLOCKED_HOSTS='+blocked,env)
        write(Path('/etc/mihomo-subscriptions.env'),env)
        dash = staging/'dashboard'; dash.mkdir(); extract_dashboard(staging/'dashboard.zip',dash)
        roots = [p for p in dash.iterdir() if p.is_dir()]
        if len(roots)!=1 or not (roots[0]/'index.html').is_file():raise ValueError('Unknown dashboard layout')
        shutil.copytree(roots[0],HOME/'ui')
        for f in [HOME/'ui',*(HOME/'ui').rglob('*')]:
            shutil.chown(f,user='mihomo',group='mihomo');f.chmod(0o755 if f.is_dir() else 0o644)
        run([str(XUI/'x-ui'),'setting','-username',username,'-password',password,
             '-port',str(PORTS['xui']),'-listenIP','127.0.0.1','-webBasePath',base_path],log,cwd=XUI)
        seed_xui(Path('/etc/x-ui/x-ui.db'),args)
        write(PRIVATE/'xray-bridge.json',bridge(args.node_port))
        write(Path('/etc/systemd/system/mihomo.service'),'''[Unit]
Description=Mihomo core
After=network-online.target
Wants=network-online.target
[Service]
User=mihomo
Group=mihomo
WorkingDirectory=/var/lib/mihomo
ExecStart=/usr/local/bin/mihomo -d /var/lib/mihomo -f /etc/mihomo/config.yaml
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/mihomo
UMask=0027
[Install]
WantedBy=multi-user.target
''',0o644)
        write(Path('/etc/systemd/system/x-ui.service'),'''[Unit]
Description=3X-UI managed by mihomo-x-ui fresh installer
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=/usr/local/x-ui
ExecStart=/usr/local/x-ui/x-ui
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=/etc/x-ui /usr/local/x-ui /var/log/x-ui
UMask=0077
[Install]
WantedBy=multi-user.target
''',0o644)
        for name in ['mihomo-subscriptions.service','mihomo-subscriptions-refresh.service','mihomo-subscriptions-refresh.timer']:
            write(Path('/etc/systemd/system')/name,(APP/'examples'/name).read_text(),0o644)
        write(Path('/etc/caddy/Caddyfile'),render_caddy(args),0o644)
        run(['caddy','validate','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'],log)
        run(['runuser','-u','mihomo','--',str(MIHOMO),'-t','-d',str(HOME),'-f',str(CONFIG)],log)
        run(['systemctl','daemon-reload'],log)
        run(['systemctl','enable','--now','mihomo','x-ui'],log)
        wait_http('http://127.0.0.1:19091/version', {'Authorization':'Bearer '+secret})
        wait_http('http://127.0.0.1:2053'+base_path+'login')
        manager_env = {k:v for k,v in os.environ.items() if not k.startswith('M_')}
        manager_env.update(M_ORIGIN='https://'+args.panel_domain, M_CORE=str(MIHOMO), M_BLOCKED_HOSTS=blocked)
        run([str(APP/'.venv/bin/python'),str(APP/'server.py'),'--bootstrap'],log,env=manager_env)
        run([str(APP/'.venv/bin/python'),str(APP/'integrate_dashboard.py'),'--ui-dir',str(HOME/'ui')],log)
        run(['systemctl','enable','--now','mihomo-subscriptions','mihomo-subscriptions-refresh.timer','caddy'],log)
        wait_http('http://127.0.0.1:19092/subscription-api/sources', {'Authorization':'Bearer '+secret})
        for origin, path in [('https://'+args.panel_domain,'/subscriptions/'),('https://'+args.xui_domain,base_path+'login')]:
            wait_http(origin+path)
        write(PRIVATE/'deployment.json', {'status':'services-ready','versions':lock,'node_port':args.node_port,
                                         'next':'Create REALITY inbound; configure Xray bridge; import subscription; choose a node; verify traffic'})
    print('Services and HTTPS verified. Login details: '+str(PRIVATE/'credentials.json'))
    print('Finish node/bridge/subscription setup: docs/full-stack.md')


def main(argv=None):
    args=arguments(argv)
    lock=json.loads((SOURCE/'deploy/versions.json').read_text())
    if not args.apply:
        print(json.dumps({'action':'plan-only; no changes', 'versions':{k:v['version'] for k,v in lock['components'].items()},
                          'panel':'https://'+args.panel_domain, 'xui':'https://'+args.xui_domain,
                          'public_ports':[80,443,args.node_port], 'keep_ssh_port':args.ssh_port,
                          'manual_finish':['Create REALITY inbound','Add Xray -> Mihomo SOCKS routing','Import subscription and choose node'],
                          'firewall':'No automatic firewall, timezone, BBR or full system upgrade'},indent=2))
        return
    try:apply(args,lock)
    except Exception as error:
        raise SystemExit('Installation stopped: '+str(error)+'\nPartial files are retained. Check /etc/mihomo-x-ui/install.log; do not rerun over existing data.')


if __name__=='__main__':main()
