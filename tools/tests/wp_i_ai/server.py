"""Synthetic-only FastAPI + loopback OpenAI-compatible stub, no real provider."""
import runpy,sys,threading,json
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import uvicorn
root=Path(__file__).resolve().parents[3];sys.path.insert(0,str(root));original=uvicorn.run
state={'mode':'disabled','hold':False,'entered':0,'calls':0,'models':0,'wireSafe':True};release=threading.Event();release.set()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,payload):
        raw=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        state['models']+=1
        if state['mode']=='unavailable':return self.reply(503,{'error':'synthetic unavailable'})
        return self.reply(200,{'data':[{'id':'synthetic-i6c'}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));wire=body.get('messages',[]);serialized=json.dumps(wire,ensure_ascii=False)
        state['wireSafe']=state['wireSafe'] and not any(x in serialized for x in ['Синтетический','C34.1','policyNumber','treatingDoctor','historyEntryId'])
        state['calls']+=1;state['entered']+=1
        if state['hold']:release.wait(20)
        if state['mode']=='bad502':return self.reply(200,b'{invalid synthetic json')
        content={'malicious':'<img src=x onerror="window.i6cExecuted=true">\n<script>window.i6cExecuted=true</script>\n**Текст, не HTML**','internal':'api_key: SYNTHETIC_NOT_A_KEY; system_prompt: internal synthetic instruction'}.get(state['mode'],'Синтетический ответ локального HTTP-провайдера. Только проверка интерфейса.')
        return self.reply(200,{'choices':[{'message':{'role':'assistant','content':content}}]})

def run(measured,*args,**kwargs):
    from backend.app import config
    from backend.app.ai import provider
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;t=threading.Thread(target=server.serve_forever,daemon=True);t.start();base=f'http://127.0.0.1:{server.server_port}/v1'
    config.AI_DATA_MODE='synthetic';config.AI_MODE='disabled';config.AI_ENABLED_RAW=None;config.AI_ENABLED=False;config.AI_BASE_URL_EXPLICIT=False;config.AI_MODEL_EXPLICIT=False;config.AI_MODEL='';config.AI_LOCAL_BASE_URL=base;config.AI_LOCAL_MODEL='synthetic-i6c';config.AI_API_KEY='';config.AI_SYNTHETIC_ALLOWLIST=(base,);provider.clear_health_cache()
    @measured.inner.post('/synthetic/i6c/control')
    def control(body:dict):
        if 'mode' in body:
            assert body['mode'] in {'disabled','unavailable','available','malicious','internal','bad502'};state['mode']=body['mode'];config.AI_MODE='disabled' if body['mode']=='disabled' else 'auto';provider.clear_health_cache()
        if 'hold' in body:
            state['hold']=bool(body['hold'])
            if state['hold']:release.clear()
            else:release.set()
        return {'syntheticOnly':True,**state}
    @measured.inner.get('/synthetic/i6c/stats')
    def stats():return {'syntheticOnly':True,**state}
    try:return original(measured,*args,**kwargs)
    finally:release.set();server.shutdown();server.server_close();t.join(timeout=5)
uvicorn.run=run
runpy.run_path(str(root/'tools/tests/wp_h/server.py'),run_name='__main__')
