"""Private cloud API for Leads Google. Uses user-scoped Supabase REST; no service-role key."""
import json, os, uuid, csv, io
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
        if path=="/": return self.send((ROOT/"index.html").read_text(encoding="utf-8"),html=True)
        if path=="/api/config": return self.send({"url":URL,"key":KEY})
        if path=="/api/health": return self.send({"docker":False,"cloud":True,"scraper":False})
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
            if path=="/api/jobs":return self.send({"error":"La extracción en la nube está desactivada hasta validar el trabajador."},503)
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
                    key=identity(row)
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
