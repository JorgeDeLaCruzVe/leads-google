"""Private cloud API for Leads Google. Uses user-scoped Supabase REST; no service-role key."""
import json, html, os, uuid, csv, io, threading, subprocess, tempfile, socket, ipaddress, re, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlparse, quote, urljoin, parse_qsl, urlencode, urlunparse
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parent
URL=os.environ.get("SUPABASE_URL","").rstrip("/")
KEY=os.environ.get("SUPABASE_PUBLISHABLE_KEY","")
PORT=int(os.environ.get("PORT","10000"))
HOST=os.environ.get("HOST","127.0.0.1")
MAX_BODY=25_000_000
SCRAPER_ENABLED=os.environ.get('SCRAPER_ENABLED','0')=='1' and os.path.isfile('/usr/bin/google-maps-scraper')
SCRAPER_BACKEND_URL=os.environ.get('SCRAPER_BACKEND_URL','').rstrip('/')
def scraper_ready():
    if SCRAPER_ENABLED:return True
    if not SCRAPER_BACKEND_URL:return False
    try:
        with urlopen(SCRAPER_BACKEND_URL+'/api/health',timeout=25) as response:
            return bool(json.load(response).get('scraper'))
    except Exception:return False
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

class DirectoryParser(HTMLParser):
    """Extract public directory cards, including pai.pt search result cards."""
    CARD_WORDS=('business-card','listing-card','company-card','search-result','result-item','result-card','listing-item','company-item','business-item','result__item','result_entry','search_result','card--result')
    def __init__(self):
        super().__init__()
        self.rows=[];self.stack=[];self.card=None;self.card_depth=0
        self.capture=None;self.parts=[];self.link=''
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);classes=' '.join((a.get('class',''),a.get('id',''))).lower()
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(tag)
        if self.card is None and tag in ('article','li','div','section') and any(w in classes for w in self.CARD_WORDS):
            self.card={'title':'','phone':'','website':'','address':'','emails':'','link':'','source':'Páginas Amarelas' if 'pai.pt' in getattr(self,'base','') else 'URL personalizada'}
            self.card_depth=len(self.stack)
        if self.card is None:return
        href=a.get('href','')
        if tag=='a':
            if href.startswith('tel:'):self.card['phone']=href[4:].strip()
            elif href.startswith('mailto:'):self.card['emails']=href[7:].split('?')[0].strip()
            elif href.startswith('http') and urlparse(href).hostname and not urlparse(href).hostname.endswith('pai.pt'):self.card['website']=href
            elif href and not href.startswith(('#','javascript:')) and not self.card['link']:self.card['link']=urljoin(getattr(self,'base',''),href)
        if tag in ('h2','h3','h4') or (tag in ('a','span','div','p') and any(w in classes for w in ('business-name','company-name','listing-title','result-title','company_title','entity-name','card-title','card-link'))):
            self.capture='title';self.parts=[]
        elif tag in ('address',) or any(w in classes for w in ('address','morada','location-address')):
            self.capture='address';self.parts=[]
    def handle_data(self,data):
        if self.card is not None and self.capture:self.parts.append(data)
    def handle_endtag(self,tag):
        if self.card is not None:
            if self.capture and (tag in ('h2','h3','h4','address','a','span','p','div')):
                value=' '.join(' '.join(self.parts).split())
                if value and (self.capture!='title' or not self.card['title']):self.card[self.capture]=value
                self.capture=None;self.parts=[]
            if len(self.stack)==self.card_depth:
                if self.card['title'] and (self.card['link'] or self.card['phone'] or self.card['address'] or self.card['website'] or self.card['emails']):
                    self.rows.append(self.card)
                self.card=None;self.capture=None
        if tag in self.stack:
            while self.stack:
                opened=self.stack.pop()
                if opened==tag:break

def structured_directory_rows(html,base):
    """Parse schema.org JSON-LD business records when directory card markup differs."""
    rows=[]
    for match in re.finditer('<script[^>]*application/ld.json[^>]*>(.*?)</script>',html,re.I|re.S):
        try: root=json.loads(match.group(1))
        except (ValueError,TypeError):continue
        queue=[root]
        while queue:
            item=queue.pop()
            if isinstance(item,list):queue.extend(item);continue
            if not isinstance(item,dict):continue
            for k in ('@graph','itemListElement','item'):
                if k in item:queue.append(item[k])
            kind=item.get('@type',[])
            kinds=[kind] if isinstance(kind,str) else kind if isinstance(kind,list) else []
            if not any(t in ('LocalBusiness','Organization','Store','HairSalon','BeautySalon','ProfessionalService','Restaurant','AutoRepair','MedicalBusiness') or str(t).endswith('Business') for t in kinds):continue
            name=item.get('name')
            if not isinstance(name,str) or not name.strip():continue
            address=item.get('address','')
            if isinstance(address,dict):address=', '.join(str(address.get(k,'')) for k in ('streetAddress','postalCode','addressLocality') if address.get(k))
            if not isinstance(address,str):address=''
            phone=item.get('telephone','');website=item.get('url','')
            if not any((phone,website,address)):continue
            rows.append({'title':name.strip(),'phone':str(phone or ''),'website':str(website or ''),'address':address,'emails':str(item.get('email','') or ''),'link':str(item.get('@id',base) or base),'source':'Páginas Amarelas' if 'pai.pt' in base else 'URL personalizada'})
    return rows

def public_url(value):
    parsed=urlparse(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Solo se permiten direcciones HTTPS públicas')
    hostname=parsed.hostname.lower()
    if hostname=='localhost' or hostname.endswith(('.local','.internal','.localhost')):
        raise ValueError('Dirección privada no permitida')
    for item in socket.getaddrinfo(hostname,443,type=socket.SOCK_STREAM):
        ip=ipaddress.ip_address(item[4][0])
        if not ip.is_global:raise ValueError('La dirección no es pública')
    return value

class PaiLinks(HTMLParser):
    """Collect PAI business links and real pagination controls."""
    def __init__(self,base):
        super().__init__();self.base=base;self.links=[];self.pages=[];self.current=None;self.parts=[];self.page_number=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag not in ('a','link','button'):return
        href=a.get('href','') or a.get('data-href','') or a.get('data-url','')
        rel=a.get('rel','')
        if isinstance(rel,list):rel=' '.join(rel)
        label=' '.join(str(a.get(k,'') or '') for k in ('aria-label','title','class','data-page')).lower()
        if tag=='button' and a.get('data-page','').isdigit():
            self.page_number=max(self.page_number,int(a['data-page']))
        if not href:return
        absolute=urljoin(self.base,href);p=urlparse(absolute)
        if p.hostname not in ('www.pai.pt','pai.pt'):return
        if p.path.startswith('/searches'):
            if 'page=' in p.query or 'offset=' in p.query or 'next' in rel or any(x in label for x in ('next','seguinte','próxima','pagination','paginacao')):
                self.pages.append(absolute)
            return
        if tag!='a' or not p.path.startswith('/paginas/'):return
        self.current=absolute;self.parts=[]
    def handle_data(self,data):
        if self.current:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.current:
            name=' '.join(' '.join(self.parts).split())
            if len(name)>3 and not name.lower().startswith(('ver mais','website','visitar','saber mais','ver detalhes')):
                self.links.append((name,self.current))
            self.current=None


class PaiEmailParser(HTMLParser):
    """Find email addresses exposed by public 'Enviar email' links/buttons."""
    def __init__(self):
        super().__init__();self.emails=set()
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        for key in ('href','data-email','data-mail','data-href','onclick','aria-label'):
            value=html.unescape(a.get(key,'') or '')
            for match in re.findall(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}',value,re.I):
                self.emails.add(match.lower())
    def handle_data(self,data):
        if '@' in data:
            for match in re.findall(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}',data,re.I):
                self.emails.add(match.lower())

class PaiWebsiteParser(HTMLParser):
    """Capture the external company website from a PAI detail page."""
    def __init__(self,base):
        super().__init__();self.base=base;self.candidates=[];self.active=None;self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag!='a':return
        a=dict(attrs)
        href=html.unescape(a.get('href','') or a.get('data-href','') or '').strip()
        if not href:return
        absolute=urljoin(self.base,href)
        parsed=urlparse(absolute)
        if parsed.scheme not in ('http','https') or not parsed.hostname:return
        host=parsed.hostname.lower()
        if host=='pai.pt' or host.endswith('.pai.pt'):return
        label=' '.join(str(a.get(k,'') or '') for k in ('title','aria-label','class')).lower()
        self.active=absolute;self.parts=[label]
    def handle_data(self,data):
        if self.active:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.active:
            label=' '.join(self.parts).lower()
            score=2 if any(word in label for word in ('visitar website','visitar site','website','site oficial','página web')) else 0
            if score:self.candidates.append((score,self.active))
            self.active=None;self.parts=[]
    def website(self):
        return sorted(self.candidates,reverse=True)[0][1] if self.candidates else ''

def external_company_website(value):
    if not isinstance(value,str):return ''
    value=value.strip()
    parsed=urlparse(value)
    if parsed.scheme not in ('http','https') or not parsed.hostname:return ''
    host=parsed.hostname.lower()
    if host=='pai.pt' or host.endswith('.pai.pt'):return ''
    return value


def directory_rows(url,limit=20):
    url=public_url(url)
    def fetch(target):
        public_url(target)
        req=Request(target,headers={'User-Agent':'LeadsGoogle/1.0 (public directory research)','Accept':'text/html','Accept-Language':'pt-PT,pt;q=0.9'})
        with urlopen(req,timeout=25) as response:
            if urlparse(response.url).hostname!=urlparse(target).hostname:raise ValueError('Redirección a otro dominio no permitida')
            if 'text/html' not in response.headers.get('Content-Type',''):raise ValueError('El enlace no contiene una página HTML')
            data=response.read(2_000_001)
        if len(data)>2_000_000:raise ValueError('Página demasiado grande')
        return data.decode('utf-8','replace')
    combined=[]
    visited_pages=set()
    visited_details=set()
    seen_signatures=set()
    repeated_pages=0
    failed_pages=0
    parsed_url=urlparse(url)
    is_pai_search=parsed_url.hostname in ('pai.pt','www.pai.pt') and parsed_url.path.startswith('/searches')
    if is_pai_search:
        # PAI uses &page=N. Preserve every search filter, including repeated [] values.
        base_params=[(k,v) for k,v in parse_qsl(parsed_url.query,keep_blank_values=True) if k!='page']
        original_page=next((int(v) for k,v in parse_qsl(parsed_url.query) if k=='page' and v.isdigit()),1)
        # Scan all available numbered pages until the unique-business target is reached.
        max_pages=100
        pages=[urlunparse(parsed_url._replace(query=urlencode(base_params+([('page',str(n))] if n>1 else [])))) for n in range(original_page,original_page+max_pages)]
    else:
        pages=[url]
    for page in pages:
        if page in visited_pages:continue
        visited_pages.add(page)
        try:content=fetch(page)
        except (ValueError,OSError,HTTPError):
            if page==url:raise
            failed_pages+=1
            if failed_pages>=5:break
            continue
        failed_pages=0
        parser=DirectoryParser();parser.base=page;parser.feed(content)
        if not is_pai_search:
            combined.extend(parser.rows+structured_directory_rows(content,page))
            continue
        links=PaiLinks(page);links.feed(content)
        signature=tuple(sorted(set(link for _,link in links.links)))
        if signature and signature in seen_signatures:
            repeated_pages+=1
            # A single repeated page must not terminate a large extraction.
            if repeated_pages>=5:break
            continue
        repeated_pages=0
        if signature:seen_signatures.add(signature)
        if not links.links:
            repeated_pages+=1
            if repeated_pages>=5:break
            continue
        for name,link in dict.fromkeys(links.links):
            if link in visited_details:continue
            visited_details.add(link)
            # The listing itself provides the business identity; details enrich it.
            row={'title':name,'link':link,'source':'Páginas Amarelas'}
            try:
                detail=fetch(link)
                detail_rows=structured_directory_rows(detail,link)
                if not detail_rows:
                    p=DirectoryParser();p.base=link;p.feed(detail);detail_rows=p.rows
                if detail_rows:
                    best=next((item for item in detail_rows if item.get('title','').casefold()==name.casefold()),detail_rows[0])
                    row.update({k:v for k,v in best.items() if v})
                websites=PaiWebsiteParser(link);websites.feed(detail)
                row['website']=websites.website() or external_company_website(row.get('website',''))
                emails=PaiEmailParser();emails.feed(detail)
                if emails.emails:row['emails']='; '.join(sorted(emails.emails))
            except (ValueError,OSError,HTTPError):
                pass
            row['website']=external_company_website(row.get('website',''))
            combined.append(row)
            if len(visited_details)>=limit:break
        if len(visited_details)>=limit:break
    unique={}
    for row in combined:
        key=(row['title'].casefold(),row.get('address','').casefold())
        if key not in unique:unique[key]=row
        else:
            original=unique[key]
            for field in ('phone','website','address','emails','link'):
                if row.get(field) and not original.get(field):original[field]=row[field]
            if row.get('emails') and original.get('emails') and row['emails']!=original['emails']:
                original['emails']='; '.join(sorted(set(filter(None,re.split(r'[;,\s]+',original['emails']+';'+row['emails'])))))
    return list(unique.values())[:limit]

def run_scrape(job,token,owner):
    try:
        if job.get('source') in ('url','paginas_amarelas'):
            rows=directory_rows(job['url'],job.get('limit',20))
            timed_out=False
            if not rows:raise RuntimeError('La página se cargó, pero no se reconocieron fichas de empresas. Es posible que pai.pt utilice una estructura HTML diferente o que el acceso automático esté limitado.')
        else:
            # Repeat Maps searches with distinct geographical phrasings to seek the requested target.
            # This is best-effort: source coverage and free runtime cannot guarantee a count.
            requested=job.get('limit',20)
            base=job['query']
            variants=[base]+[base+' '+area for area in ('centro','arredores','zona norte','zona sul','zona este','zona oeste','proximidades','bairros','zona industrial','zona comercial','área metropolitana','distrito','concelho','região')]
            # Report progress while additional search passes are being attempted.
            rows=[];seen=set();timed_out=False
            with tempfile.TemporaryDirectory(prefix='leads-scrape-') as folder:
                for attempt,query in enumerate(variants[:min(len(variants),max(3,(requested+14)//15+1))]):
                    if len(rows)>=requested:break
                    job['detail']=f'{len(rows)} de {requested} encontrados · búsqueda {attempt+1} de {min(len(variants),max(3,(requested+14)//15+1))}'
                    call('/rest/v1/jobs?id=eq.'+quote(job['id']),token,'PATCH',{'payload':job})
                    query_file=Path(folder)/('queries_'+str(attempt)+'.txt')
                    result_file=Path(folder)/('results_'+str(attempt)+'.csv')
                    query_file.write_text(query+'\n',encoding='utf-8')
                    args=['/usr/bin/google-maps-scraper','-input',str(query_file),'-results',str(result_file),'-depth',str(job['depth']),'-c','1','-exit-on-inactivity','3m']
                    try:
                        result=subprocess.run(args,capture_output=True,text=True,timeout=150,check=False)
                        if result.returncode!=0 and not result_file.exists():
                            if attempt==0:raise RuntimeError('El motor terminó con error: '+(result.stderr or result.stdout)[-300:])
                            continue
                    except subprocess.TimeoutExpired:
                        timed_out=True
                        if not result_file.exists():continue
                    if result_file.exists():
                        for item in csv_rows(result_file.read_text(encoding='utf-8-sig')):
                            if not item.get('title'):continue
                            key=identity(item)
                            if key not in seen:
                                seen.add(key);rows.append(item)
                            if len(rows)>=requested:break
            if not rows:raise RuntimeError('El motor no devolvió negocios para la búsqueda')
        added=0
        for row in rows[:job.get('limit',20)]:
            if not row.get('title'): continue
            key=str(uuid.uuid5(uuid.NAMESPACE_URL,owner+'|'+identity(row)))
            code,old=call('/rest/v1/leads?id=eq.'+quote(key)+'&select=payload',token)
            if code!=200: raise RuntimeError('No se pudieron consultar los contactos')
            previous=old[0]['payload'] if old else {}
            merged={**previous,**row,'id':key,'estado':previous.get('estado','Nuevo'),'notas':previous.get('notas','')}
            code,_=call('/rest/v1/leads',token,'POST',{'id':key,'owner_id':owner,'payload':merged},{'Prefer':'resolution=merge-duplicates,return=minimal'})
            if code not in (200,201,204): raise RuntimeError('No se pudieron guardar los contactos')
            added+=not bool(old)
        job.update(status='Parcial' if timed_out or len(rows)<job.get('limit',20) else 'Completada',added=added,detail=f'{len(rows)} de {job.get("limit",20)} solicitados, {added} contactos nuevos'+(' (tiempo límite alcanzado)' if timed_out else '; la fuente no ofreció más resultados' if len(rows)<job.get('limit',20) else ''))
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
        if path=="/api/health": return self.send({"docker":SCRAPER_ENABLED,"cloud":True,"scraper":scraper_ready()})
        token=self.user()
        if not token: return self.send({"error":"Sesión no válida"},401)
        if path=="/api/engine/reconnect":
            if SCRAPER_ENABLED:return self.send({"connected":True,"message":"Motor local activo"})
            if not SCRAPER_BACKEND_URL:return self.send({"connected":False,"message":"No hay un servidor extractor configurado"})
            for attempt in range(3):
                try:
                    request=Request(SCRAPER_BACKEND_URL+'/api/health',headers={'Cache-Control':'no-cache','User-Agent':'LeadsGoogle-EngineReconnect/1.0'})
                    with urlopen(request,timeout=20) as response:
                        if json.load(response).get('scraper'):
                            return self.send({"connected":True,"message":"Motor conectado correctamente","attempts":attempt+1})
                except Exception:
                    pass
                if attempt<2:time.sleep(3)
            return self.send({"connected":False,"message":"El servidor extractor no respondió tras 3 intentos. Comprueba que el servicio extractor esté activo en Render.","attempts":3})
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
            if path=="/api/leads/clear":
                if body.get('confirm') is not True:return self.send({'error':'Confirmación requerida'},400)
                status,_=call('/rest/v1/leads?owner_id=eq.'+quote(owner),token,'DELETE')
                if status not in (200,204):return self.send({'error':'No se pudieron eliminar los resultados'},502)
                return self.send({'ok':True})
            if path=="/api/jobs/delete":
                job_id=str(body.get('id',''))
                try:uuid.UUID(job_id)
                except ValueError:return self.send({'error':'ID inválido'},400)
                status,records=call('/rest/v1/jobs?id=eq.'+quote(job_id)+'&select=payload',token)
                if status!=200:return self.send({'error':'No se pudo consultar la búsqueda'},502)
                if not records:return self.send({'error':'Búsqueda no encontrada'},404)
                if records[0]['payload'].get('status')=='En curso':return self.send({'error':'No se puede eliminar una búsqueda en curso'},409)
                status,_=call('/rest/v1/jobs?id=eq.'+quote(job_id),token,'DELETE')
                if status not in (200,204):return self.send({'error':'No se pudo eliminar'},502)
                return self.send({'ok':True})
            if path=="/api/jobs/clear":
                if body.get("confirm") is not True:return self.send({"error":"Confirmación requerida"},400)
                status,records=call('/rest/v1/jobs?select=payload',token)
                if status!=200:return self.send({"error":"No se pudo consultar el historial"},502)
                if any(row.get('payload',{}).get('status')=='En curso' for row in records):
                    return self.send({"error":"Espera a que finalicen las búsquedas en curso"},409)
                status,_=call('/rest/v1/jobs?owner_id=eq.'+quote(owner),token,'DELETE')
                if status not in (200,204):return self.send({"error":"No se pudo limpiar el historial"},502)
                return self.send({"ok":True})
            if path=="/api/jobs":
                if not SCRAPER_ENABLED:
                    if not scraper_ready():return self.send({"error":"El motor de extracción no está disponible"},503)
                    request=Request(SCRAPER_BACKEND_URL+'/api/jobs',data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},method="POST")
                    try:
                        with urlopen(request,timeout=20) as upstream:
                            return self.send(json.load(upstream),upstream.status)
                    except HTTPError as exc:
                        try: data=json.load(exc)
                        except Exception: data={"error":"Error en el motor de búsqueda"}
                        return self.send(data,exc.code)
                source=str(body.get('source','maps'))
                if source not in ('maps','url','paginas_amarelas'):return self.send({'error':'Fuente inválida'},400)
                source_url=str(body.get('url','')).strip()
                if source!='maps':
                    try: public_url(source_url)
                    except (ValueError,OSError) as exc:return self.send({'error':str(exc)},400)
                query=str(body.get('query','')).strip()
                if source=='maps' and not 3<=len(query)<=200:return self.send({"error":"Búsqueda inválida (3–200 caracteres)"},400)
                limit=int(body.get('limit',20))
                if limit not in (20,50,100,200,500):return self.send({'error':'Cantidad inválida'},400)
                depth=int(body.get('depth',1))
                if depth not in (1,3):return self.send({"error":"Profundidad máxima de prueba: 3"},400)
                with JOB_LOCK:
                    if ACTIVE_JOBS:return self.send({"error":"Ya existe una búsqueda en curso. Intenta más tarde."},429)
                    job={'id':str(uuid.uuid4()),'query':query if source=='maps' else source_url,'source':source,'url':source_url,'depth':depth,'limit':limit,'email':False,'status':'En curso','added':0,'detail':'Extrayendo datos'}
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
