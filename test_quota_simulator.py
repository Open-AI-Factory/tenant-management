import importlib.util
import json
import threading
import unittest
import urllib.request
import urllib.error
import http.client
from test_forecast_quota import T, forecast


class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('quota_simulator'),'HTTP simulator required')
        from quota_simulator import make_server
        self.server=make_server(0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        if hasattr(self,'server'):self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,body=None,headers=None):
        data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(self.url+path,data=data,headers=dict({'Content-Type':'application/json'},**(headers or {})))
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def test_external_http_receipt_then_time_trigger(self):
        self.assertEqual(self.request('/api/forecasts',forecast())[0],200)
        self.assertEqual(self.request('/api/evaluate',{})[1]['quotas']['tenant-a'],1)
        self.request('/api/advance',{'now':T+30,'observations':{'tenant-a':{'idle':True,'observedAt':T+30}}})
        self.assertEqual(self.request('/api/evaluate',{})[1]['quotas'],{'tenant-a':3,'tenant-b':1})
        self.assertEqual(self.request('/api/forecasts',forecast())[1]['status'],'Duplicate')
        self.assertEqual(self.request('/api/state')[1]['receivedEvents'],1)
    def test_cross_origin_and_wrong_host_cannot_write(self):
        for headers in ({'Origin':'https://untrusted.example'},{'Host':'untrusted.example'}):
            self.assertEqual(self.request('/api/forecasts',forecast(),headers)[0],403)
        self.assertEqual(self.request('/api/state')[1]['receivedEvents'],0)
    def test_invalid_advance_and_forecast_leave_state_unchanged(self):
        before=self.request('/api/state')[1]
        self.assertEqual(self.request('/api/advance',{'now':T+5,'observations':{'tenant-a':None}})[0],400)
        self.assertEqual(self.request('/api/advance',{'now':T-1,'observations':{}})[0],409)
        self.assertEqual(self.request('/api/forecasts',forecast(expectedDevices=float('nan')))[0],400)
        self.assertEqual(self.request('/api/state')[1],before)
    def test_separate_dummy_sender_replays_full_scenario_over_http(self):
        from dummy_profiler import replay
        states=replay(self.url)
        self.assertEqual(states[0]['quotas'],{'tenant-a':1,'tenant-b':1})
        self.assertEqual(states[1]['quotas'],{'tenant-a':3,'tenant-b':1})
        self.assertEqual(states[6]['quotas'],{'tenant-a':1,'tenant-b':3})
        self.assertEqual(states[10]['history'][-2]['reason'],'ActiveWorkloads')
        self.assertEqual(states[11]['quotas'],{'tenant-a':3,'tenant-b':1})
        self.assertEqual(states[-1]['history'][-2]['reason'],'StaleForecast')
        self.assertTrue(all(sum(s['quotas'].values())<=4 for s in states))
    def test_scenario_next_and_reset(self):
        self.assertEqual(self.request('/api/scenario/next',{})[1]['scenarioIndex'],1)
        self.assertEqual(self.request('/api/scenario/next',{})[1]['quotas']['tenant-a'],3)
        state=self.request('/api/reset',{})[1]
        self.assertEqual(state['scenarioIndex'],0);self.assertEqual(state['receivedEvents'],0)
        self.assertEqual(state['quotas'],{'tenant-a':1,'tenant-b':1})

    def raw_request(self,payload):
        req=urllib.request.Request(self.url+'/api/forecasts',data=payload,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=3) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as exc:return exc.code,json.load(exc)
        except http.client.RemoteDisconnected:return 0,{'error':'Disconnected without HTTP response'}

    def test_invalid_unicode_is_rejected_without_poisoning_state(self):
        before=self.request('/api/state')[1]
        code,_=self.raw_request(json.dumps(forecast(eventId='\ud800')).encode())
        self.assertEqual(code,400,'malformed Unicode must produce an HTTP error, not disconnect')
        self.assertEqual(self.request('/api/state')[1],before)

    def test_duplicate_json_fields_are_rejected_without_mutation(self):
        before=self.request('/api/state')[1]
        payload=json.dumps(forecast())[:-1]+',"expectedDevices":1}'
        self.assertEqual(self.raw_request(payload.encode())[0],400)
        self.assertEqual(self.request('/api/state')[1],before)

if __name__=='__main__':unittest.main()
