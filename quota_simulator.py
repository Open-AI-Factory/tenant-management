"""Loopback-only forecast simulator. No Kubernetes client or live apply path."""
import argparse
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from forecast_quota import QuotaModel, ForecastError
from dummy_profiler import START, scenario

ROOT=Path(__file__).resolve().parent


class Simulator:
    def __init__(self,config):
        self.config=copy.deepcopy(config);self.lock=threading.RLock();self.reset()
    def reset(self):
        self.model=QuotaModel(self.config);self.model.now=START;self.index=0;self.receipt=None
        self.observations={};self.label='Ready';self.events=[]
    def state(self):
        return dict(self.model.snapshot(),observations=copy.deepcopy(self.observations),
                    latest={u:{k:v for k,v in e.items() if not k.startswith('_')} for u,e in self.model.latest.items()},
                    scenarioIndex=self.index,scenarioLength=len(scenario()),label=self.label,lastReceipt=self.receipt,events=copy.deepcopy(self.events))
    def advance(self,body):
        if not isinstance(body,dict) or set(body)!={'now','observations'}:raise ForecastError('now and observations required')
        # Validate the entire observation set without mutating model clock/ledger.
        trial=copy.deepcopy(self.model);trial.step(body['now'],body['observations'])
        self.model.now=body['now'];self.observations=copy.deepcopy(body['observations'])
    def receive(self,event):
        result=self.model.receive(event,self.model.now);self.receipt=result
        self.events.append(dict(result,at=self.model.now));self.events=self.events[-100:]
        return result
    def post(self,path,body):
        with self.lock:
            if path=='/api/forecasts':return self.receive(body)
            if path=='/api/advance':self.advance(body)
            elif path in ('/api/evaluate','/api/reset','/api/scenario/next'):
                if body!={}:raise ForecastError('Empty object required')
                if path=='/api/reset':self.reset()
                elif path=='/api/evaluate':self.model.step(self.model.now,self.observations)
                else:
                    if self.config!=json.loads((ROOT/'quota_demo_config.json').read_text(encoding='utf-8')):
                        raise ForecastError('Built-in scenario requires the default demo config',409)
                    if self.index>=len(scenario()):raise ForecastError('Scenario complete',409)
                    step=scenario()[self.index]
                    # Mixing manual time and scripted replay requires an explicit reset.
                    self.model.clock(step['at'])
                    previous=copy.deepcopy((self.model,self.observations,self.receipt,self.events))
                    try:
                        self.advance(dict(now=step['at'],observations=step['observations']))
                        for event in step['forecasts']:self.receive(event)
                        self.model.step(self.model.now,self.observations)
                    except ForecastError:
                        self.model,self.observations,self.receipt,self.events=previous;raise
                    self.label=step['label'];self.index+=1
            else:raise ForecastError('Not found',404)
            return self.state()


def make_server(port=8894,config=None):
    sim=Simulator(json.loads((ROOT/'quota_demo_config.json').read_text(encoding='utf-8')) if config is None else config)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,status,body,ctype='application/json; charset=utf-8'):
            payload=body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(payload)
        def check_origin(self):
            hosts={'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)}
            if self.headers.get('Host') not in hosts or self.headers.get('Origin') not in (None,'http://'+self.headers.get('Host','')):
                raise ForecastError('Loopback same-origin access only',403)
        def do_GET(self):
            try:
                self.check_origin()
                if self.path=='/api/state':
                    with sim.lock:self.respond(200,sim.state())
                elif self.path in ('/','/quota_simulator.js','/quota_simulator.css'):
                    name={'/':'quota_simulator.html'}.get(self.path,self.path[1:])
                    ctype={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.rsplit('.',1)[1]]
                    self.respond(200,(ROOT/name).read_bytes(),ctype)
                else:raise ForecastError('Not found',404)
            except ForecastError as e:self.respond(e.status,dict(error=str(e)))
        def do_POST(self):
            self.connection.settimeout(5)
            try:
                self.check_origin()
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ForecastError('JSON required',415)
                if self.headers.get('Transfer-Encoding'):raise ForecastError('Chunked body unsupported')
                try:length=int(self.headers.get('Content-Length','0'))
                except ValueError:raise ForecastError('Invalid body length')
                if not 1<=length<=32768:raise ForecastError('Body size limit',413)
                try:
                    def invalid_constant(value):raise ValueError(value)
                    def unique_fields(pairs):
                        result={}
                        for key,value in pairs:
                            if key in result:raise ValueError('Duplicate JSON field')
                            result[key]=value
                        return result
                    body=json.loads(self.rfile.read(length),parse_constant=invalid_constant,object_pairs_hook=unique_fields)
                except (ValueError,UnicodeError,RecursionError):raise ForecastError('Invalid JSON')
                self.respond(200,sim.post(self.path,body))
            except ForecastError as e:self.respond(e.status,dict(error=str(e)))
            except TimeoutError:self.respond(408,dict(error='Request timed out'))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.simulator=sim
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8894)
    parser.add_argument('--config',type=Path,default=ROOT/'quota_demo_config.json');args=parser.parse_args()
    server=make_server(args.port,json.loads(args.config.read_text(encoding='utf-8')))
    print('Simulation only: http://127.0.0.1:'+str(server.server_port),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
