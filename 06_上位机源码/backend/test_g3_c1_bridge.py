import unittest
from host.g3_c1_bridge import G3Bridge

class BridgeTests(unittest.TestCase):
    def test_identity_and_offline_rejection(self):
        b=G3Bridge()
        s=b.status()
        self.assertEqual((s['profile'],s['variant'],s['user_code'],s['schema'],s['wire_version']),('G3_C1',5,'56354733','46443405',5))
        self.assertFalse(s['connected'])
        with self.assertRaises(ValueError): b.command({'action':'play-c1','request_id':'a'})
        with self.assertRaises(ValueError): b.command({'action':'trajectory','request_id':'b','value':7})
    def test_mapping_pagination_and_session_mismatch(self):
        b=G3Bridge(); b.add_mapping({'source_index':17,'break_before':True})
        p=b.gowin(0,None,10)
        self.assertEqual(p['samples'][0]['point']['source_index'],17)
        self.assertTrue(b.gowin(0,'old',10)['overrun'])
        self.assertEqual(b.gowin(p['cursor'],p['stream'],10)['samples'],[])


class CommandTests(unittest.TestCase):
    def test_duplicate_request_never_replays_and_rejects_conflict(self):
        class Owner:
            def __init__(self): self.sent=[]
            def command(self,opcode,source): self.sent.append((opcode,source))
        class Controller:
            def __init__(self):
                import threading
                self.cancel=threading.Event()
        b=G3Bridge(); owner=Owner(); b.owner=owner; b.controller=Controller()
        req={'request_id':'one','action':'stop'}
        b.command(req); b.command(req)
        self.assertEqual(len(owner.sent),1)
        self.assertTrue(b.controller.cancel.is_set())
        with self.assertRaises(ValueError): b.command({'request_id':'one','action':'zero'})
    def test_unknown_ack_is_not_retried(self):
        class Owner:
            def command(self,opcode,source): pass
        class Controller:
            def __init__(self):
                import threading
                self.cancel=threading.Event()
        b=G3Bridge(); b.owner=Owner(); b.controller=Controller()
        b.command({'request_id':'x','action':'zero'})
        b.trace('write_requested',{'source':'WEB/x','request_id':23,'opcode':17})
        b.trace('command_unknown',{'request_id':23})
        self.assertEqual(b.requests['x']['state'],'indeterminate')

class IsolationTests(unittest.TestCase):
    def test_lazy_original_import_resolves_g3_protocol(self):
        import types, sys
        from host.g3_c1_bridge import load_upstream
        modules=load_upstream(); fake=types.ModuleType('protocol')
        old=sys.modules.get('protocol'); sys.modules['protocol']=fake
        try:
            lookup=modules['draw_trajectory'].__dict__['__builtins__']['__import__']
            self.assertIs(lookup('protocol'),modules['protocol'])
        finally:
            if old is None: sys.modules.pop('protocol',None)
            else: sys.modules['protocol']=old

class HttpTests(unittest.TestCase):
    def test_loopback_headers_and_gowin_pagination(self):
        import http.client, json
        b=G3Bridge(); b.add_mapping({'source_index':2,'break_before':True})
        server=b.serve(0); port=server.server_port
        try:
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
            c.request('GET','/status'); r=c.getresponse(); self.assertEqual(r.status,403); r.read()
            c.request('GET','/gowin?after=0&limit=1',headers={'X-J280-Local':'1'})
            r=c.getresponse(); self.assertEqual(r.status,200); data=json.loads(r.read())
            self.assertEqual(data['samples'][0]['point']['source_index'],2)
            c.request('GET','/status',headers={'X-J280-Local':'1','Origin':'http://localhost'})
            r=c.getresponse(); self.assertEqual(r.status,403); r.read(); c.close()
        finally:
            server.shutdown(); server.server_close()

if __name__=='__main__': unittest.main()
