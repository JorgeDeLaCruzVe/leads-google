import json, unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from threading import Thread
import cloud_app

class CloudSecurityTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(("127.0.0.1",0),cloud_app.Handler)
        self.thread=Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,body=None,headers=None):
        req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or {})
        try:
            with urlopen(req,timeout=5) as r:return r.status,json.load(r)
        except HTTPError as e:return e.code,json.load(e)
    def test_anonymous_cannot_read_leads_or_jobs(self):
        for path in ("/api/leads","/api/jobs"):
            self.assertEqual(self.request(path)[0],401)
    def test_anonymous_cannot_write(self):
        for path in ("/api/import","/api/update","/api/jobs"):
            self.assertEqual(self.request(path,{"id":"test"})[0],401)
    def test_foreign_origin_rejected(self):
        status,_=self.request("/api/import",{"format":"csv","text":"title\\nTest"},headers={"Origin":"https://evil.example"})
        self.assertEqual(status,403)
    def test_invalid_token_rejected(self):
        with patch.object(cloud_app,"call",return_value=(401,{})):
            status,_=self.request("/api/leads",headers={"Authorization":"Bearer invalid"})
        self.assertEqual(status,401)
    def test_scraper_disabled_for_valid_session(self):
        def mock_call(path,token,method="GET",payload=None,extra=None):
            return (200,{"id":"user-id"}) if path=="/auth/v1/user" else (200,[])
        with patch.object(cloud_app,"call",side_effect=mock_call):
            status,_=self.request("/api/jobs",{"query":"Lisboa"},headers={"Authorization":"Bearer token"})
        self.assertEqual(status,503)
if __name__=="__main__":unittest.main()
