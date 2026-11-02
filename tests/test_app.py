import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(app, 'DATA', Path(self.temp.name))
        self.data_patch.start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.port_patch = patch.object(app, 'PORT', self.server.server_port)
        self.port_patch.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.port_patch.stop()
        self.data_patch.stop()
        self.temp.cleanup()

    def request(self, path, body=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = origin
        req = Request(self.base+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(req, timeout=5) as res: return res.status, json.load(res)
        except HTTPError as e: return e.code, json.load(e)

    def test_import_update_deduplicate_and_restore_json(self):
        csv = '\ufefftitle;place_id;phone;address\nClínica de prueba;abc;+351123;Lisboa\n'
        self.assertEqual(self.request('/api/import', {'format':'csv','text':csv})[1]['added'], 1)
        lead = self.request('/api/leads')[1][0]
        self.assertEqual(lead['phone'], '+351123')
        self.assertEqual(self.request('/api/update', {'id':lead['id'],'estado':'Revisado','notas':'Nota de prueba'})[0],200)
        self.assertEqual(self.request('/api/import', {'format':'csv','text':csv})[1]['added'],0)
        lead = self.request('/api/leads')[1][0]
        self.assertEqual(lead['notas'], 'Nota de prueba')
        self.assertEqual(lead['estado'], 'Revisado')
        with app.db() as c: c.execute('DELETE FROM leads')
        self.request('/api/import', {'format':'json','text':json.dumps([lead])})
        restored = self.request('/api/leads')[1][0]
        self.assertEqual(restored,lead)

    def test_invalid_import_and_cross_origin_are_rejected(self):
        self.assertEqual(self.request('/api/import', {'format':'json','text':'{}'})[0],400)
        self.assertEqual(self.request('/api/import', {'format':'csv','text':'name\nWrong column'})[0],400)
        self.assertEqual(self.request('/api/import', [], origin=self.base)[0],400)
        self.assertEqual(self.request('/api/import', {}, origin='https://untrusted.example')[0],403)

    def test_missing_docker_gives_actionable_error(self):
        with patch.object(app.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertFalse(self.request('/api/health')[1]['docker'])
            code, result = self.request('/api/jobs', {'query':'Prueba Lisboa','depth':1})
        self.assertEqual(code,400)
        self.assertIn('Docker Desktop',result['error'])
        self.assertEqual(app.records('jobs'),[])

    def test_scraper_result_is_imported(self):
        job={'id':'test-job','query':'Prueba Lisboa','depth':1,'email':True,'status':'En curso'}
        def fake_run(args, **kwargs):
            self.assertIn('-email',args)
            (app.DATA/'test-job'/'results.csv').write_text('title,place_id\nTest,abc\n',encoding='utf-8')
            return type('Result',(),{'returncode':0})()
        with patch.object(app.subprocess,'run',side_effect=fake_run): app.scrape(job)
        self.assertEqual(app.records('jobs')[0]['status'],'Completada')
        self.assertEqual(len(app.records('leads')),1)

if __name__ == '__main__': unittest.main()
