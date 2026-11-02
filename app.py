import csv, io, json, sqlite3, threading, subprocess, uuid, os
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('LEADS_DATA_DIR', str(ROOT / 'data'))).resolve()
PORT = int(os.environ.get('LEADS_PORT', '8765'))
DATA.mkdir(parents=True, exist_ok=True)
@contextmanager
def db():
    c = sqlite3.connect(DATA / 'leads.db')
    c.execute('CREATE TABLE IF NOT EXISTS leads (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    try:
        with c: yield c
    finally:
        c.close()
def records(table):
    with db() as c: return [json.loads(x[0]) for x in c.execute(f'SELECT payload FROM {table}')]
def save(table, item):
    with db() as c: c.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)', (item['id'], json.dumps(item, ensure_ascii=False)))
def import_rows(rows):
    added = 0
    with db() as c:
        for row in rows:
            row = {str(k).strip(): v for k,v in row.items() if k is not None}
            if not row.get('title'): continue
            identity = row.get('place_id') or row.get('cid') or row.get('link') or (str(row['title']).casefold().strip()+'|'+str(row.get('address','')).casefold().strip())
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, str(identity)))
            previous = c.execute('SELECT payload FROM leads WHERE id=?', (key,)).fetchone()
            old = json.loads(previous[0]) if previous else {}
            merged = {**old, **{k:v for k,v in row.items() if v not in ('',None)}, 'id':key, 'estado':old.get('estado',row.get('estado') or 'Nuevo'), 'notas':old.get('notas',row.get('notas') or '')}
            c.execute('INSERT OR REPLACE INTO leads VALUES (?,?)', (key,json.dumps(merged,ensure_ascii=False)))
            added += not bool(previous)
    return added
def parse_csv(value):
    value = value.lstrip('\ufeff')
    try: dialect = csv.Sniffer().sniff(value[:8192], delimiters=',;\t')
    except csv.Error: dialect = csv.excel
    return list(csv.DictReader(io.StringIO(value),dialect=dialect))
def scrape(job):
    folder = DATA / job['id']; folder.mkdir()
    (folder/'queries.txt').write_text(job['query']+'\n',encoding='utf-8')
    args = ['docker','run','--rm','--name','leads-'+job['id'],'-v','gmaps-playwright-cache:/opt','-v',f'{folder}:/out','gosom/google-maps-scraper','-input','/out/queries.txt','-results','/out/results.csv','-depth',str(job['depth']),'-c','2','-exit-on-inactivity','3m']
    if job['email']: args.append('-email')
    try:
        with (folder/'run.log').open('w',encoding='utf-8') as log:
            result = subprocess.run(args,stdout=log,stderr=subprocess.STDOUT)
        path = folder/'results.csv'
        job['added'] = import_rows(parse_csv(path.read_text(encoding='utf-8-sig'))) if path.exists() else 0
        job['status'] = 'Completada' if result.returncode == 0 else 'Error'
        job['detail'] = '' if result.returncode == 0 else (folder/'run.log').read_text(encoding='utf-8')[-2000:]
    except Exception as e: job.update(status='Error',detail=str(e))
    save('jobs',job)
class Handler(BaseHTTPRequestHandler):
    def respond(self, value, status=200, mime='application/json; charset=utf-8'):
        raw = json.dumps(value,ensure_ascii=False).encode() if mime.startswith('application/json') else value.encode()
        self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/': return self.respond((ROOT/'index.html').read_text(encoding='utf-8'),mime='text/html; charset=utf-8')
        if path == '/api/leads': return self.respond(records('leads'))
        if path == '/api/jobs': return self.respond(records('jobs'))
        if path == '/api/health':
            try: ok = subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,timeout=8).returncode == 0
            except Exception: ok = False
            return self.respond({'docker':ok})
        self.respond({'error':'No encontrado'},404)
    def do_POST(self):
        if self.headers.get('Origin') not in (None,f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}'): return self.respond({'error':'Origen no permitido'},403)
        try:
            size = int(self.headers.get('Content-Length','0'))
            if size <= 0 or size > 25_000_000: raise ValueError('El cuerpo debe tener entre 1 byte y 25 MB.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body,dict): raise ValueError('Se esperaba un objeto JSON.')
            if self.path == '/api/import':
                rows = parse_csv(body['text']) if body['format']=='csv' else json.loads(body['text'])
                if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows): raise ValueError('El JSON debe ser una lista de empresas.')
                if not rows or not any(r.get('title') for r in rows): raise ValueError('No se encontraron empresas. El archivo debe incluir la columna title.')
                return self.respond({'added':import_rows(rows),'total':len(rows)})
            if self.path == '/api/update':
                row = next((r for r in records('leads') if r['id']==body['id']),None)
                if row is None: raise ValueError('Contacto no encontrado')
                row.update(estado=str(body['estado']),notas=str(body['notas'])); save('leads',row); return self.respond({'ok':True})
            if self.path == '/api/jobs':
                query = str(body['query']).strip()
                if not query or len(query)>500: raise ValueError('Indica actividad y ciudad (máximo 500 caracteres).')
                depth = int(body.get('depth',1))
                if depth not in (1,3,5): raise ValueError('Profundidad inválida')
                try: ready = subprocess.run(['docker','info'],capture_output=True,timeout=8).returncode == 0
                except Exception: ready = False
                if not ready: raise ValueError('Abre Docker Desktop y espera a que el motor esté activo antes de buscar.')
                job = {'id':str(uuid.uuid4()),'query':query,'depth':depth,'email':bool(body.get('email')),'status':'En curso','added':0}
                save('jobs',job); threading.Thread(target=scrape,args=(job,),daemon=True).start(); return self.respond(job)
            self.respond({'error':'No encontrado'},404)
        except (ValueError,KeyError,TypeError) as e: self.respond({'error':str(e)},400)
        except Exception: self.respond({'error':'No se pudo completar la operación.'},500)
if __name__ == '__main__':
    for job in records('jobs'):
        if job['status']=='En curso':
            job.update(status='Interrumpida',detail='El panel se reinició. Revisa data antes de repetir la búsqueda.'); save('jobs',job)
    server = ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    print(f'Leads Local: http://127.0.0.1:{PORT}',flush=True)
    if '--open' in __import__('sys').argv:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(f'http://127.0.0.1:{PORT}')).start()
    server.serve_forever()
