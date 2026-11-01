"""Subscription management for one local Mihomo core. Python stdlib + PyYAML."""
from __future__ import annotations
import base64, contextlib, copy, datetime, hashlib, hmac, http.server, ipaddress
import json, os, re, secrets, shutil, socket, subprocess, sys, tempfile, threading
import time, urllib.parse, urllib.request, uuid
from pathlib import Path
import yaml

ETC = Path(os.environ.get('M_CONFIG', '/etc/mihomo/config.yaml'))
HOME = Path(os.environ.get('M_HOME', '/var/lib/mihomo'))
DATA = Path(os.environ.get('M_ADMIN_DATA', '/var/lib/mihomo-subscriptions'))
CORE = os.environ.get('M_CORE', '/usr/local/lib/mihomo/mihomo')
API = os.environ.get('M_API', 'http://127.0.0.1:19091')
ORIGIN = os.environ.get('M_ORIGIN', '')
TARGET_GROUPS = {n.strip() for n in os.environ.get('M_TARGET_GROUPS', 'PROXY').split(',') if n.strip()}
FILE_USER = os.environ.get('M_FILE_USER', 'mihomo')
FILE_GROUP = os.environ.get('M_FILE_GROUP', 'mihomo')
PORT = int(os.environ.get('M_ADMIN_PORT', '19092'))
STATIC = Path(__file__).parent / 'web'
MAX_BYTES = 8 * 1024 * 1024
BLOCKED = {'localhost'} | {h.strip().lower().strip('[]') for h in os.environ.get('M_BLOCKED_HOSTS', '').split(',') if h.strip()}
LOCK = threading.Lock()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

class Problem(Exception):
    pass

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')

def load(path, default=None):
    if not path.exists(): return copy.deepcopy(default)
    text = path.read_text(encoding='utf-8-sig')
    return yaml.safe_load(text) if path == ETC else json.loads(text)

def atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content if isinstance(content, bytes) else content.encode('utf8'))
            f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if path.is_relative_to(HOME) or path == ETC:
            if os.name != 'nt' and FILE_GROUP: shutil.chown(tmp, user='root' if path == ETC else (FILE_USER or None), group=FILE_GROUP)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def jsonbytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf8')

@contextlib.contextmanager
def locked():
    with LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        with (DATA / 'lock').open('a') as f:
            try:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX)
            except ImportError: pass
            yield

def core_api(path, method='GET', body=None, config=None):
    cfg = config or load(ETC)
    q = urllib.request.Request(API + path, method=method,
        headers={'Authorization': 'Bearer ' + cfg['secret'], 'Content-Type': 'application/json'},
        data=None if body is None else jsonbytes(body))
    with OPENER.open(q, timeout=20) as r:
        return json.load(r) if r.status != 204 else None

def public_target(url):
    try:
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError()
        port = u.port or (443 if u.scheme == 'https' else 80)
        addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)))
        if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses): raise ValueError()
        # Prefer IPv4; use IPv6 when it is the only public answer.
        addresses.sort(key=lambda x: ':' in x)
        return u, port, addresses[0]
    except (ValueError, OSError):
        raise Problem('订阅地址必须是可访问的公网 HTTP/HTTPS 链接，不能指向内网或本机管理地址。') from None

def download(url):
    with tempfile.TemporaryDirectory(prefix='sub-download-') as tmp:
        body, headers = Path(tmp)/'body', Path(tmp)/'headers'
        for _ in range(5):
            u, port, addr = public_target(url)
            address = '[' + addr + ']' if ':' in addr else addr
            cmd = [shutil.which('curl.exe' if os.name == 'nt' else 'curl') or 'curl',
                '--silent', '--show-error', '--http1.1', '--noproxy', '*', '--proto', '=http,https',
                '--connect-timeout', '8', '--max-time', '25', '--max-filesize', str(MAX_BYTES),
                '--resolve', f'{u.hostname}:{port}:{address}', '--user-agent', os.environ.get('M_USER_AGENT', 'mihomo-subscription-manager/0.1.0'),
                '--dump-header', str(headers), '--output', str(body), '--write-out', '%{http_code}', url]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if p.returncode: raise Problem('订阅下载失败：请检查链接、证书和服务器连通性。')
            status = int(p.stdout[-3:])
            h = {}
            for line in headers.read_text(encoding='latin1').splitlines():
                if ':' in line:
                    k, v = line.split(':', 1); h[k.strip().lower()] = v.strip()
            if status in (301, 302, 303, 307, 308):
                if 'location' not in h: raise Problem('订阅跳转地址无效。')
                target = urllib.parse.urljoin(url, h['location'])
                if u.scheme == 'https' and urllib.parse.urlsplit(target).scheme != 'https':
                    raise Problem('订阅跳转降低了 HTTPS 安全级别。')
                url = target; continue
            if status != 200: raise Problem(f'订阅服务器返回 HTTP {status}。')
            raw = body.read_bytes()
            if not raw or len(raw) > MAX_BYTES: raise Problem('订阅为空或超过 8 MB。')
            return raw, h.get('subscription-userinfo', '')
        raise Problem('订阅跳转次数过多。')

def b64decode(text):
    text = re.sub(r'\s+', '', text)
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4)).decode('utf-8-sig')

def own_host(host):
    host = str(host or '').strip('[]').lower()
    if host in BLOCKED: return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_unspecified or ip.is_link_local
    except ValueError: return False

def clean_name(name, source):
    name = re.sub(r'\|📊.*$', '', str(name)).strip()
    return (source['name'] + ' | ' if source.get('prefix') else '') + name

def prepare(raw, source):
    """Keep only nodes; never accept a subscription's routes, DNS or controller settings."""
    try:
        text = raw.decode('utf-8-sig').strip()
        if '://' not in text and not text.startswith(('{', '[')) and not re.search(r'(?m)^proxies\s*:', text):
            text = b64decode(text)
        if text.startswith('{') or re.search(r'(?m)^proxies\s*:', text):
            parsed = yaml.safe_load(text)
            nodes = parsed.get('proxies') if isinstance(parsed, dict) else None
            if not isinstance(nodes, list) or len(nodes) > 5000: raise ValueError()
            kept = []
            for node in nodes:
                if not isinstance(node, dict) or not node.get('name') or not node.get('type'): raise ValueError()
                if own_host(node.get('server')): continue
                node['name'] = clean_name(node['name'], source); kept.append(node)
            excluded = len(nodes) - len(kept)
            result = jsonbytes({'proxies': kept})
            names = [n['name'] for n in kept]
        else:
            lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
            if not lines or len(lines) > 5000: raise ValueError()
            kept, names, excluded = [], [], 0
            for line in lines:
                u = urllib.parse.urlsplit(line)
                if not u.scheme or '://' not in line: raise ValueError()
                if u.scheme == 'vmess':
                    node = json.loads(b64decode(line.split('://', 1)[1]))
                    if own_host(node.get('add')): excluded += 1; continue
                    name = clean_name(node.get('ps') or node.get('add'), source); node['ps'] = name
                    line = 'vmess://' + base64.b64encode(jsonbytes(node)).decode()
                elif u.scheme == 'ssr':
                    node = b64decode(line.split('://', 1)[1]); host = node.split(':', 1)[0]
                    if own_host(host): excluded += 1; continue
                    main, sep, query = node.partition('/?'); params = urllib.parse.parse_qs(query)
                    name = clean_name(b64decode(params['remarks'][0]) if params.get('remarks') else host, source)
                    params['remarks'] = [base64.urlsafe_b64encode(name.encode()).decode().rstrip('=')]
                    line = 'ssr://' + base64.urlsafe_b64encode((main + '/?' + urllib.parse.urlencode(params, doseq=True)).encode()).decode().rstrip('=')
                else:
                    host = u.hostname
                    if u.scheme == 'ss' and '@' not in u.netloc:
                        decoded = b64decode(u.netloc or u.path)
                        host = urllib.parse.urlsplit('ss://' + decoded).hostname
                    if own_host(host): excluded += 1; continue
                    name = clean_name(urllib.parse.unquote(u.fragment) or host or u.scheme, source)
                    line = urllib.parse.urlunsplit(u._replace(fragment=urllib.parse.quote(name, safe='')))
                kept.append(line); names.append(name)
            result = ('\n'.join(kept) + '\n').encode()
        if not names: raise Problem('订阅没有可用出站节点；指向当前服务器自身的节点已排除，避免回环。')
        if len(set(names)) != len(names): raise Problem('订阅中有重复节点名称，请开启“节点加订阅前缀”或调整来源。')
        return result, names, excluded
    except Problem: raise
    except Exception: raise Problem('订阅格式无效，请使用 Clash/Mihomo YAML、原始节点链接或 Base64 订阅。') from None

def validate_nodes(raw):
    with tempfile.TemporaryDirectory(prefix='sub-core-') as tmp:
        d = Path(tmp); (d/'source.txt').write_bytes(raw)
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
        token = secrets.token_hex(16)
        cfg = {'external-controller': f'127.0.0.1:{port}', 'secret': token, 'log-level':'silent',
            'proxy-providers': {'check': {'type': 'file', 'path': './source.txt'}},
            'proxy-groups': [{'name':'check-group','type':'select','use':['check']}], 'rules':['MATCH,check-group']}
        (d/'config.json').write_bytes(jsonbytes(cfg))
        with (d/'log').open('wb') as log:
            p = subprocess.Popen([CORE, '-d', str(d), '-f', str(d/'config.json')], stdout=log, stderr=log)
            try:
                for _ in range(50):
                    if p.poll() is not None: break
                    try:
                        q = urllib.request.Request(f'http://127.0.0.1:{port}/providers/proxies', headers={'Authorization':'Bearer '+token})
                        with OPENER.open(q, timeout=0.5) as r: nodes = json.load(r)['providers']['check']['proxies']
                        if nodes: return len(nodes)
                    except Exception: pass
                    time.sleep(0.1)
                raise Problem('Mihomo 无法加载订阅节点，原配置未修改。')
            finally:
                p.terminate()
                try: p.wait(timeout=4)
                except subprocess.TimeoutExpired: p.kill(); p.wait()

def source_path(source):
    if not re.fullmatch(r'[a-f0-9]{16}', source['id']): raise Problem('订阅标识无效。')
    return HOME/'managed-subscriptions'/(source['id']+'.txt')

def state():
    return load(DATA/'state.json', {'sources':[], 'last_backup':None})

def source_spec(body, old=None):
    result = dict(old or {'id':uuid.uuid4().hex[:16], 'enabled':True, 'prefix':True})
    result.update({k:body[k] for k in ('name','url','interval','enabled','prefix') if k in body})
    if not isinstance(result.get('name'), str) or not result['name'].strip() or len(result['name']) > 60 or re.search(r'[\x00-\x1f/\\#?]',result['name']):
        raise Problem('请填写 1—60 字的订阅名称，不能含斜线或控制字符。')
    result['name'] = result['name'].strip()
    if not isinstance(result.get('url'), str) or len(result['url']) > 4096: raise Problem('请填写订阅链接。')
    result['url'] = result['url'].strip()
    result['interval'] = int(result.get('interval', 3600))
    if not 300 <= result['interval'] <= 604800: raise Problem('自动更新间隔应在 5 分钟到 7 天之间。')
    if type(result['enabled']) is not bool or type(result['prefix']) is not bool: raise Problem('订阅开关格式无效。')
    return result

def backup(oldcfg, oldstate):
    folder = DATA/'backups'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    atomic(folder/'config.json',jsonbytes(oldcfg)); atomic(folder/'state.json',jsonbytes(oldstate))
    files = folder/'files'; files.mkdir()
    for s in oldstate['sources']:
        p = source_path(s)
        if p.exists(): shutil.copy2(p, files/p.name)
    return folder

def reconcile(cfg, previous, updated):
    result = copy.deepcopy(cfg)
    managed = {s['name'] for s in previous['sources']}
    active = [s for s in updated['sources'] if s['enabled']]
    if not active: raise Problem('至少保留一个启用的订阅，避免代理组没有节点。')
    others = {k:v for k,v in result.get('proxy-providers',{}).items() if k not in managed}
    names = [s['name'] for s in updated['sources']]
    if len(set(names)) != len(names) or any(n in others for n in names): raise Problem('订阅名称已存在。')
    if any(n==g['name'] for n in names for g in result['proxy-groups']): raise Problem('订阅名称不能与策略组同名。')
    for s in active: others[s['name']] = {'type':'file','path':str(source_path(s)), 'health-check':{'enable':True,'url':'https://www.gstatic.com/generate_204','interval':300,'timeout':5000,'lazy':True}}
    result['proxy-providers'] = others
    for g in result['proxy-groups']:
        if set(g.get('use',[])) & managed or (not managed and g['name'] in TARGET_GROUPS):
            g['use'] = [n for n in g.get('use',[]) if n not in managed] + [s['name'] for s in active]
    return result

def restore_choices(choices):
    proxies = core_api('/proxies')['proxies']
    for name, choice in choices.items():
        options = proxies.get(name,{}).get('all',[])
        if choice not in options:
            prefix = re.sub(r'\|📊.*$', '', choice)
            choice = next((n for n in options if re.sub(r'\|📊.*$', '', n) == prefix), None)
        if choice in options:
            core_api('/proxies/'+urllib.parse.quote(name,safe=''),'PUT',{'name':choice})

def apply(previous, updated, bodies):
    oldcfg = load(ETC); newcfg = reconcile(oldcfg, previous, updated)
    choices = {n:v['now'] for n,v in core_api('/proxies')['proxies'].items() if v.get('type')=='Selector'}
    folder = backup(oldcfg, previous)
    try:
        (HOME/'managed-subscriptions').mkdir(exist_ok=True)
        if os.name!='nt' and FILE_GROUP: shutil.chown(HOME/'managed-subscriptions',user=FILE_USER or None,group=FILE_GROUP)
        for ident, content in bodies.items():
            s = next(s for s in updated['sources'] if s['id']==ident)
            atomic(source_path(s), content, 0o640)
        candidate = DATA/'candidate.json'; atomic(candidate,jsonbytes(newcfg))
        p = subprocess.run([CORE,'-t','-d',str(HOME),'-f',str(candidate)],capture_output=True,timeout=25)
        if p.returncode: raise Problem('配置校验未通过，已保留原配置。')
        atomic(ETC,jsonbytes(newcfg),0o640)
        core_api('/configs?force=true','PUT',{'payload':json.dumps(newcfg,ensure_ascii=False)},newcfg)
        for _ in range(30):
            loaded = core_api('/providers/proxies')['providers']
            if all(s['name'] in loaded and len(loaded[s['name']].get('proxies',[]))>0 for s in updated['sources'] if s['enabled']):break
            time.sleep(0.2)
        else: raise Problem('订阅未能加载，正在恢复原配置。')
        restore_choices(choices)
        updated['last_backup'] = folder.name; atomic(DATA/'state.json',jsonbytes(updated))
        return updated
    except Exception:
        for s in previous['sources']:
            p = folder/'files'/source_path(s).name
            if p.exists(): atomic(source_path(s),p.read_bytes(),0o640)
        atomic(ETC,jsonbytes(oldcfg),0o640)
        core_api('/configs?force=true','PUT',{'payload':json.dumps(oldcfg,ensure_ascii=False)},oldcfg)
        restore_choices(choices)
        atomic(DATA/'state.json',jsonbytes(previous))
        raise

def fetched(source):
    raw, usage = download(source['url']); content, names, excluded = prepare(raw,source)
    count = validate_nodes(content)
    if count != len(names): raise Problem('部分节点无法被 Mihomo 识别，原订阅未修改。')
    source.update({'node_count':count,'excluded_count':excluded,'updated_at':now(),'last_error':'','usage':usage})
    return content

def mutate(action, body):
    with locked():
        old = state(); new = copy.deepcopy(old); bodies = {}
        ident = body.get('id')
        found = next((s for s in new['sources'] if s['id']==ident),None) if action!='add' else None
        if action in ('edit','delete','refresh') and not found: raise Problem('订阅不存在，请刷新列表。')
        if action in ('add','edit'):
            s = source_spec(body,found)
            if found: new['sources'][new['sources'].index(found)] = s
            else:
                if len(new['sources'])>=32: raise Problem('最多支持 32 个订阅。')
                new['sources'].append(s)
            # Validate names and last-source protection before sending network requests.
            reconcile(load(ETC),old,new)
            if s['enabled']: bodies[s['id']] = fetched(s)
        elif action=='delete': new['sources'].remove(found)
        elif action in ('refresh','refresh-all'):
            for s in new['sources']:
                if s['enabled'] and (action=='refresh-all' or s['id']==ident): bodies[s['id']] = fetched(s)
        else: raise Problem('不支持的操作。')
        return apply(old,new,bodies)

def bootstrap():
    with locked():
        if (DATA/'state.json').exists(): return
        cfg = load(ETC); old = {'sources':[],'last_backup':None}; new = copy.deepcopy(old); bodies={}
        for name, p in cfg.get('proxy-providers',{}).items():
            if p.get('type') != 'http': continue
            s = source_spec({'name':name,'url':p['url'],'interval':p.get('interval',3600),'prefix':False})
            old['sources'].append(copy.deepcopy(s)); new['sources'].append(s); bodies[s['id']] = fetched(s)
        if not new['sources']:
            if not any(g['name'] in TARGET_GROUPS for g in cfg.get('proxy-groups', [])):
                raise Problem('没有 HTTP 订阅；请用 M_TARGET_GROUPS 指定接收新订阅的现有策略组。')
            atomic(DATA/'state.json',jsonbytes(old));return
        installed=apply(old,new,bodies)
        installed['last_backup']=None;atomic(DATA/'state.json',jsonbytes(installed))

def refresh_due():
    for s in state()['sources']:
        if not s['enabled']: continue
        try: last = datetime.datetime.fromisoformat(s['updated_at']).timestamp()
        except (KeyError,ValueError): last = 0
        if time.time()-last < s['interval']: continue
        try: mutate('refresh',{'id':s['id']})
        except Exception:
            with locked():
                st=state(); item=next((x for x in st['sources'] if x['id']==s['id']),None)
                if item:
                    item['last_error']='自动更新失败，继续使用上次成功的节点。';item['last_attempt']=now()
                    atomic(DATA/'state.json',jsonbytes(st))

def snapshot():
    st = state(); runtime=core_api('/providers/proxies')['providers']
    for s in st['sources']:
        s['nodes'] = [{'name':n['name'],'type':n.get('type'),'alive':n.get('alive')} for n in runtime.get(s['name'],{}).get('proxies',[])]
    return st

class Handler(http.server.BaseHTTPRequestHandler):
    server_version='MihomoSubscriptions/1.0'
    def log_message(self,*args): pass
    def reply(self,status,value):
        b=jsonbytes(value);self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def authorized(self):
        token=self.headers.get('Authorization','').removeprefix('Bearer ')
        if not token or not hmac.compare_digest(token.encode('utf8'),load(ETC)['secret'].encode('utf8')):
            self.reply(401,{'error':'请输入正确的 Mihomo 面板密钥。'});return False
        if self.headers.get('Origin') not in (None,ORIGIN):
            self.reply(403,{'error':'请求来源不允许。'});return False
        return True
    def do_GET(self):
        path=urllib.parse.urlsplit(self.path).path
        if path=='/subscription-api/sources':
            if not self.authorized():return
            try:self.reply(200,snapshot())
            except Exception:self.reply(503,{'error':'无法读取核心状态，请稍后重试。'})
            return
        self.reply(404,{'error':'接口不存在。'})
    def do_POST(self):
        if not self.authorized():return
        try:
            if not self.headers.get('Content-Type','').startswith('application/json'):raise Problem('请求格式应为 JSON。')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=32768:raise Problem('请求大小无效。')
            body=json.loads(self.rfile.read(length));path=urllib.parse.urlsplit(self.path).path
            if path=='/subscription-api/rollback':
                with locked():
                    st=state();bid=st.get('last_backup')
                    if not bid or not re.fullmatch(r'\d{8}T\d{6}-[a-f0-9]{6}',bid):raise Problem('没有可回退的修改。')
                    folder=DATA/'backups'/bid;target=load(folder/'state.json')
                    bodies={s['id']:(folder/'files'/source_path(s).name).read_bytes() for s in target['sources'] if (folder/'files'/source_path(s).name).exists()}
                    if not bodies:raise Problem('首次迁移请使用服务器完整回退备份。')
                    apply(st,target,bodies)
            else:
                action=path.removeprefix('/subscription-api/')
                if not path.startswith('/subscription-api/'):raise Problem('接口不存在。')
                mutate(action,body)
            self.reply(200,{'ok':True,'message':'已保存并应用，节点列表已更新。','state':snapshot()})
        except (Problem,ValueError,TypeError,KeyError) as e:
            self.reply(400,{'error':str(e) if isinstance(e,Problem) else '输入内容无效，请检查后重试。'})
        except Exception:self.reply(500,{'error':'操作失败，原配置已保留；请检查订阅或服务器日志。'})

if __name__=='__main__':
    os.umask(0o077)
    origin=urllib.parse.urlsplit(ORIGIN)
    if not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        raise SystemExit('Set M_ORIGIN to your exact panel origin, without a trailing slash.')
    if origin.scheme!='https' and not (origin.scheme=='http' and origin.hostname in ('localhost','127.0.0.1','::1')):
        raise SystemExit('M_ORIGIN requires HTTPS (HTTP is allowed only on localhost).')
    if not isinstance(load(ETC).get('secret'),str) or not load(ETC)['secret']:
        raise SystemExit('Mihomo must have a non-empty string secret.')
    api_host=urllib.parse.urlsplit(API).hostname
    if api_host not in ('localhost','127.0.0.1','::1'):
        raise SystemExit('M_API must use a loopback controller.')
    if '--bootstrap' in sys.argv:bootstrap()
    elif '--refresh-due' in sys.argv:refresh_due()
    else:http.server.ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
