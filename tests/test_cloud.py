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

    def test_import_deduplication_and_notes(self):
        store={}
        def fake_call(path,token,method="GET",payload=None,extra=None):
            if path=="/auth/v1/user":return 200,{"id":"owner-1"}
            if path.startswith("/rest/v1/leads?id=eq.") and method=="GET":
                key=path.split("?id=eq.")[1].split("&")[0]
                return 200,[{"payload":store[key]}] if key in store else []
            if path=="/rest/v1/leads" and method=="POST":
                store[payload["id"]]=payload["payload"]
                return 201,None
            raise AssertionError((path,method))
        headers={"Authorization":"Bearer test"}
        csv_text="title,place_id,phone\\nClínica,abc,+351123\\n"
        with patch.object(cloud_app,"call",side_effect=fake_call):
            self.assertEqual(self.request("/api/import",{"format":"csv","text":csv_text},headers)[1]["added"],1)
            row=next(iter(store.values()))
            row["notas"]="Seguimiento"
            self.assertEqual(self.request("/api/import",{"format":"csv","text":csv_text},headers)[1]["added"],0)
        self.assertEqual(next(iter(store.values()))["notas"],"Seguimiento")
        self.assertEqual(len(store),1)

    def test_update_contact(self):
        store={"lead-1":{"id":"lead-1","title":"Empresa","estado":"Nuevo","notas":""}}
        def fake_call(path,token,method="GET",payload=None,extra=None):
            if path=="/auth/v1/user":return 200,{"id":"owner-1"}
            if method=="GET":return 200,[{"payload":store["lead-1"]}]
            if method=="PATCH":
                store["lead-1"]=payload["payload"]
                return 204,None
            raise AssertionError((path,method))
        with patch.object(cloud_app,"call",side_effect=fake_call):
            status,_=self.request("/api/update",{"id":"lead-1","estado":"Contactado","notas":"Llamar"}, {"Authorization":"Bearer test"})
        self.assertEqual(status,200)
        self.assertEqual(store["lead-1"]["notas"],"Llamar")
        self.assertEqual(store["lead-1"]["estado"],"Contactado")

if __name__=="__main__":unittest.main()
