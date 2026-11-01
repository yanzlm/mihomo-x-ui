import base64, copy, shutil, importlib.util, json, os, subprocess, tempfile, threading, unittest
import urllib.error, urllib.request
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('subadmin',Path(__file__).with_name('server.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
ID='1234567890abcdef'

class Parsing(unittest.TestCase):
    source={'name':'测试来源','prefix':False}
    def test_base64_filters_self_and_stabilizes_names(self):
        raw='vless://11111111-1111-4111-8111-111111111111@203.0.113.10:443#示例节点|📊500GB\nvless://11111111-1111-4111-8111-111111111111@203.0.113.20:25720#本机入口'
        with patch.object(m,'BLOCKED',{'203.0.113.20'}):
            content,names,excluded=m.prepare(base64.b64encode(raw.encode()),self.source)
        self.assertEqual(names,['示例节点']);self.assertEqual(excluded,1)
        self.assertNotIn(b'203.0.113.20',content)
    def test_full_yaml_drops_all_non_node_settings(self):
        raw=b'mixed-port: 9999\nsecret: untrusted\nrules: [MATCH,DIRECT]\nproxies:\n - {name: US, type: vless, server: 203.0.113.10, port: 443, uuid: 11111111-1111-4111-8111-111111111111}\n'
        content,names,excluded=m.prepare(raw,{'name':'demo','prefix':True})
        parsed=json.loads(content)
        self.assertEqual(set(parsed),{'proxies'});self.assertEqual(names,['demo | US'])
    def test_invalid_empty_and_self_only_rejected(self):
        for raw in [b'not a subscription',b'<html>error</html>',b'{"proxies":[]}',b'vless://id@127.0.0.1:443#local']:
            with self.subTest(raw=raw),self.assertRaises(m.Problem):m.prepare(raw,self.source)
    def test_no_private_url_or_embedded_credentials(self):
        for url in ['file:///etc/passwd','http://127.0.0.1:19091','http://169.254.169.254/latest/meta-data','http://u:p@example.com']:
            with self.subTest(url=url),self.assertRaises(m.Problem):m.public_target(url)
    def test_redirect_to_private_address_is_rejected(self):
        def dns(host,*args,**kwargs):return [(2,1,6,'',('127.0.0.1' if host=='localhost' else '8.8.8.8',443))]
        def run(cmd,**kwargs):
            Path(cmd[cmd.index('--dump-header')+1]).write_text('HTTP/1.1 302 Found\nLocation: https://localhost/internal\n')
            return subprocess.CompletedProcess(cmd,0,'302','')
        with patch.object(m.socket,'getaddrinfo',dns),patch.object(m.subprocess,'run',run):
            with self.assertRaises(m.Problem):m.download('https://example.com/sub')

class Persistence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(m.shutil,'chown'),patch.object(m,'ETC',self.root/'config.json'),patch.object(m,'HOME',self.root/'home'),patch.object(m,'DATA',self.root/'admin')]
        for p in self.patches:p.start()
        m.HOME.mkdir();m.DATA.mkdir()
        self.s={'id':ID,'name':'原订阅','url':'https://example.com/sub','interval':3600,'enabled':True,'prefix':False}
        self.old={'sources':[self.s],'last_backup':None}
        self.cfg={'secret':'test-secret','dns':{'nameserver':['https://1.1.1.1/dns-query#main']},'listeners':[{'port':17891}], 'rules':['MATCH,main'], 'proxy-providers':{'原订阅':{'type':'file','path':str(m.source_path(self.s))}},'proxy-groups':[{'name':'main','type':'select','use':['原订阅']}]}
        m.atomic(m.ETC,m.jsonbytes(self.cfg));m.atomic(m.DATA/'state.json',m.jsonbytes(self.old));m.atomic(m.source_path(self.s),b'old nodes')
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def test_reconcile_rename_preserves_dns_and_rules(self):
        new=copy.deepcopy(self.old);new['sources'][0]['name']='新订阅'
        cfg=m.reconcile(self.cfg,self.old,new)
        for k in ('dns','rules','listeners','secret'):self.assertEqual(cfg[k],self.cfg[k])
        self.assertEqual(cfg['proxy-groups'][0]['use'],['新订阅'])
    def test_cannot_remove_last_active_source(self):
        with self.assertRaises(m.Problem):m.reconcile(self.cfg,self.old,{'sources':[]})
    def test_duplicate_name_rejected_before_download(self):
        with patch.object(m,'download') as fetch:
            with self.assertRaises(m.Problem):m.mutate('add',self.s)
            fetch.assert_not_called()
    def test_failed_reload_restores_config_cache_and_state(self):
        new=copy.deepcopy(self.old);new['sources'][0]['interval']=7200
        calls=[]
        def api(path,method='GET',body=None,config=None):
            calls.append((path,method))
            if path=='/proxies':return {'proxies':{'main':{'type':'Selector','now':'A','all':['A']}}}
            if path.startswith('/configs') and len([x for x in calls if x[0].startswith('/configs')])==1:raise OSError('simulated core reload failure')
        with patch.object(m,'core_api',api),patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b'')):
            with self.assertRaises(OSError):m.apply(self.old,new,{ID:b'new nodes'})
        self.assertEqual(m.load(m.ETC),self.cfg);self.assertEqual(m.source_path(self.s).read_bytes(),b'old nodes');self.assertEqual(m.state(),self.old)
    def test_disable_offline_source_does_not_fetch(self):
        second=copy.deepcopy(self.s);second.update({'id':'abcdef1234567890','name':'backup'})
        state=copy.deepcopy(self.old);state['sources'].append(second);m.atomic(m.DATA/'state.json',m.jsonbytes(state))
        self.cfg['proxy-providers']['backup']={'type':'file','path':str(m.source_path(second))};self.cfg['proxy-groups'][0]['use'].append('backup');m.atomic(m.ETC,m.jsonbytes(self.cfg))
        with patch.object(m,'fetched') as fetch,patch.object(m,'apply',return_value={}) as apply:
            m.mutate('edit',{'id':ID,'enabled':False});fetch.assert_not_called();self.assertFalse(apply.call_args.args[1]['sources'][0]['enabled'])
    def test_http_auth_and_origin(self):
        srv=m.http.server.ThreadingHTTPServer(('127.0.0.1',0),m.Handler)
        thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
        try:
            url=f'http://127.0.0.1:{srv.server_port}/subscription-api/sources'
            for headers,status in [({},401),({'Authorization':'Bearer test-secret','Origin':'https://evil.example'},403)]:
                with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(urllib.request.Request(url,headers=headers))
                self.assertEqual(caught.exception.code,status)
            with patch.object(m,'snapshot',return_value=self.old):
                with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer test-secret'})) as r:self.assertEqual(r.status,200)
        finally:srv.shutdown();srv.server_close()

TEST_CORE=os.environ.get('M_TEST_CORE') or shutil.which('mihomo')

class RealCore(unittest.TestCase):
    @unittest.skipUnless(TEST_CORE,'set M_TEST_CORE to run native core integration')
    def test_native_core_accepts_uri_and_rejects_bad_protocol(self):
        with patch.object(m,'CORE',TEST_CORE):
            self.assertEqual(m.validate_nodes(b'vless://11111111-1111-4111-8111-111111111111@203.0.113.1:443?encryption=none&security=none#test\n'),1)
            with self.assertRaises(m.Problem):m.validate_nodes(b'not-a-protocol://test@203.0.113.1:443#bad\n')

if __name__=='__main__':unittest.main(verbosity=2)
