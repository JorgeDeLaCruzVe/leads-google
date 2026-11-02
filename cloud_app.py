"""Private cloud API for Leads Google. Uses user-scoped Supabase REST; no service-role key."""
import json, os, uuid, csv, io, threading, subprocess, tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse, quote
from pathlib import Path

ROOT=Path(__file__).resolve().parent
URL=os.environ.get("SUPABASE_URL","").rstrip("/")
KEY=os.environ.get("SUPABASE_PUBLISHABLE_KEY","")
PORT=int(os.environ.get("PORT","10000"))
HOST=os.environ.get("HOST","127.0.0.1")
MAX_BODY=25_000_000
SCRAPER_ENABLED=os.environ.get('SCRAPER_ENABLED','0')=='1' and os.path.isfile('/usr/bin/google-maps-scraper')
JOB_LOCK=threading.Lock()
ACTIVE_JOBS=set()

def call(path, token, method="GET", payload=None, extra=None):
    headers={"apikey":KEY,"Authorization":"Bearer "+token,"Content-Type":"application/json"}
    if extra: headers.update(extra)
    body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    req=Request(URL+path,data=body,headers=headers,method=method)
    try:
        with urlopen(req,timeout=25) as response:
            data=response.read()
            return response.status,json.loads(data) if data else None
    except HTTPError as e:
        try: details=json.loads(e.read())
        except Exception: details={}
        return e.code,details

def csv_rows(value):
    value=value.lstrip("\ufeff")
    try: dialect=csv.Sniffer().sniff(value[:8192],delimiters=",;\t")
    except csv.Error: dialect=csv.excel
    return list(csv.DictReader(io.StringIO(value),dialect=dialect))

def identity(row):
    value=row.get("place_id") or row.get("cid") or row.get("link") or (str(row["title"]).casefold().strip()+"|"+str(row.get("address","")).casefold().strip())
    return str(uuid.uuid5(uuid.NAMESPACE_URL,str(value)))

def run_scrape(job,token,owner):
    try:
        with tempfile.TemporaryDirectory(prefix='leads-scrape-') as folder:
            query_file=Path(folder)/'queries.txt'
            result_file=Path(folder)/'results.csv'
            query_file.write_text(job['query']+'\\n',encoding='utf-8')
            args=['/usr/bin/google-maps-scraper','-input',str(query_file),'-results',str(result_file),'-depth',str(job['depth']),'-c','1','-exit-on-inactivity','3m']
            if job['email']: args.append('-email')
            result=subprocess.run(args,capture_output=True,text=True,timeout=540,check=False)
            if result.returncode!=0: raise RuntimeError('El motor terminó con error: '+(result.stderr or result.stdout)[-350:])
            if not result_file.exists(): raise RuntimeError('El motor no generó resultados CSV')
            rows=csv_rows(result_file.read_text(encoding='utf-8-sig'))
            added=0
            for row in rows[:500]:
                if not row.get('title'): continue
                key=str(uuid.uuid5(uuid.NAMESPACE_URL,owner+'|'+identity(row)))
                code,old=call('/rest/v1/leads?id=eq.'+quote(key)+'&select=payload',token)
                if code!=200: raise RuntimeError('No se pudieron consultar los contactos')
                previous=old[0]['payload'] if old else {}
                merged={**previous,**row,'id':key,'estado':previous.get('estado','Nuevo'),'notas':previous.get('notas','')}
                code,_=call('/rest/v1/leads',token,'POST',{'id':key,'owner_id':owner,'payload':merged},{'Prefer':'resolution=merge-duplicates,return=minimal'})
                if code not in (200,201,204): raise RuntimeError('No se pudieron guardar los contactos')
                added+=not bool(old)
            job.update(status='Completada',added=added,detail=f'{len(rows)} resultados, {added} contactos nuevos')
    except Exception as exc:
        job.update(status='Error',detail=str(exc)[:450])
    finally:
        call('/rest/v1/jobs?id=eq.'+quote(job['id']),token,'PATCH',{'payload':job})
        with JOB_LOCK: ACTIVE_JOBS.discard(job['id'])

class Handler(BaseHTTPRequestHandler):
    def send(self, data, status=200, html=False):
        raw=data.encode() if html else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def user(self):
        auth=self.headers.get("Authorization","")
        if not auth.startswith("Bearer "): return None
        token=auth[7:]
        status, result=call("/auth/v1/user",token)
        return token if status==200 and isinstance(result,dict) and result.get("id") else None
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/": return self.send((ROOT/"cloud_index.html").read_text(encoding="utf-8"),html=True)
        if path=="/api/config": return self.send({"url":URL,"key":KEY})
        if path=="/api/health": return self.send({"docker":False,"cloud":True,"scraper":SCRAPER_ENABLED})
        token=self.user()
        if not token: return self.send({"error":"Sesión no válida"},401)
        if path in ("/api/leads","/api/jobs"):
            table=path.rsplit("/",1)[-1]
            status,rows=call("/rest/v1/"+table+"?select=payload&order=created_at.desc",token)
            if status!=200:return self.send({"error":"Error al consultar datos"},502)
            return self.send([r["payload"] for r in rows])
        return self.send({"error":"No encontrado"},404)
    def do_POST(self):
        origin=self.headers.get("Origin")
        host=self.headers.get("Host","")
        if origin and origin not in ("https://"+host,"http://"+host): return self.send({"error":"Origen no permitido"},403)
        token=self.user()
        if not token:return self.send({"error":"Sesión no válida"},401)
        try:
            length=int(self.headers.get("Content-Length","0"))
            if length<1 or length>MAX_BODY:raise ValueError("Tamaño de solicitud no permitido")
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):raise ValueError("JSON inválido")
            path=urlparse(self.path).path
            status,me=call("/auth/v1/user",token)
            if status!=200:return self.send({"error":"Sesión no válida"},401)
            owner=me["id"]
            if path=="/api/jobs":
                if not SCRAPER_ENABLED:return self.send({"error":"El motor de extracción no está disponible"},503)
                query=str(body.get('query','')).strip()
                if not 3<=len(query)<=200:return self.send({"error":"Búsqueda inválida (3–200 caracteres)"},400)
                depth=int(body.get('depth',1))
                if depth not in (1,3):return self.send({"error":"Profundidad máxima de prueba: 3"},400)
                with JOB_LOCK:
                    if ACTIVE_JOBS:return self.send({"error":"Ya existe una búsqueda en curso. Intenta más tarde."},429)
                    job={'id':str(uuid.uuid4()),'query':query,'depth':depth,'email':bool(body.get('email',False)),'status':'En curso','added':0,'detail':'Extrayendo datos'}
                    ACTIVE_JOBS.add(job['id'])
                status,_=call('/rest/v1/jobs',token,'POST',{'id':job['id'],'owner_id':owner,'payload':job})
                if status not in (200,201,204):
                    with JOB_LOCK:ACTIVE_JOBS.discard(job['id'])
                    return self.send({"error":"No se pudo guardar el trabajo"},502)
                threading.Thread(target=run_scrape,args=(job,token,owner),daemon=True).start()
                return self.send({'ok':True,'job':job['id']},202)
            if path not in ("/api/import","/api/update"):return self.send({"error":"No encontrado"},404)
            status,me=call("/auth/v1/user",token)
            if status!=200:return self.send({"error":"Sesión no válida"},401)
            owner=me["id"]
            if path=="/api/import":
                rows=csv_rows(body["text"]) if body["format"]=="csv" else json.loads(body["text"])
                if not isinstance(rows,list) or not rows or not all(isinstance(r,dict) for r in rows) or not any(r.get("title") for r in rows):raise ValueError("Se requiere una lista de contactos con title")
                added=0
                for row in rows:
                    if not row.get("title"):continue
                    row={str(k).strip():v for k,v in row.items() if k is not None}
                    key=str(uuid.uuid5(uuid.NAMESPACE_URL,owner+'|'+identity(row)))
                    code,old=call("/rest/v1/leads?id=eq."+quote(key)+"&select=payload",token)
                    if code!=200: return self.send({"error":"Error al consultar contacto"},502)
                    previous=old[0]["payload"] if old else {}
                    merged={**previous,**{k:v for k,v in row.items() if v not in ("",None)},"id":key,"estado":previous.get("estado",row.get("estado") or "Nuevo"),"notas":previous.get("notas",row.get("notas") or "")}
                    code,_=call("/rest/v1/leads",token,"POST",{"id":key,"owner_id":owner,"payload":merged},{"Prefer":"resolution=merge-duplicates,return=minimal"})
                    if code not in (200,201,204):return self.send({"error":"Error al guardar contacto"},502)
                    added+=not bool(old)
                return self.send({"added":added,"total":len(rows)})
            key=str(body["id"])
            code,rows=call("/rest/v1/leads?id=eq."+quote(key)+"&select=payload",token)
            if code!=200:return self.send({"error":"Error al consultar contacto"},502)
            if not rows:raise ValueError("Contacto no encontrado")
            row=rows[0]["payload"]
            row.update(estado=str(body["estado"]),notas=str(body["notas"]))
            code,_=call("/rest/v1/leads?id=eq."+quote(key),token,"PATCH",{"payload":row})
            if code not in (200,204):return self.send({"error":"Error al guardar cambios"},502)
            return self.send({"ok":True})
        except (ValueError,KeyError,TypeError,UnicodeDecodeError) as e:return self.send({"error":str(e)},400)
        except Exception:return self.send({"error":"Error interno"},500)

if __name__=="__main__":
    if not URL or not KEY:raise SystemExit("Faltan SUPABASE_URL y SUPABASE_PUBLISHABLE_KEY")
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
