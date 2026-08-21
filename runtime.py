"""Single-writer controller and HTTPS application runtime."""
import os
import secrets
import ssl
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from server import Kube, sync_one, LOCK
from tenant_api import TenantAPI
from http_app import grafana_authenticator, make_handler
from kubeutil import listing, BASE, MANAGED
from errors import Error
import executions as runs
from kueue_quota_control import KueueQuotaControl


def observe_records(kube):
    for cm in runs.records(kube):
        try:
            r=runs.decode(cm)
            # A worker only adopts an existing fixed-profile Pod, never submits as an old actor.
            if not r['pod'].get('uid'): r=runs.recover_existing(kube,r)
            if r['pod'].get('uid'): runs.observe_execution(kube,r)
        except Error as exc: print('Execution observation: '+str(exc)[:200],flush=True)


def main():
    kube=Kube(); tls=Path('/tls'); secret=secrets.token_bytes(32)
    grafana=os.environ['GRAFANA_URL']; origin=os.environ['PUBLIC_ORIGIN']; instance=os.environ['GRAFANA_INSTANCE_ID']
    auth=grafana_authenticator(grafana,instance,ssl.create_default_context(cafile=str(tls/'tls.crt')),secret)
    budget=KueueQuotaControl(kube,enabled=os.environ.get('KUEUE_QUOTA_ENABLED')=='true')
    handler=make_handler(TenantAPI(kube,secret,os.environ.get('TENANT_MUTATIONS_ENABLED')=='true'),auth,origin,grafana,Path('/app'),LOCK,budget)
    def loop():
        while True:
            try:
                with LOCK:
                    if budget.enabled: budget.tick()
                    if os.environ.get('TENANT_RECONCILE_ENABLED')=='true':
                        for p in listing(kube,BASE):
                            if p['metadata'].get('labels',{}).get(MANAGED)=='true': sync_one(kube,p)
                    observe_records(kube)
            except Exception as exc: print('Reconcile error: '+type(exc).__name__,flush=True)
            time.sleep(5)
    threading.Thread(target=loop,daemon=True).start()
    srv=ThreadingHTTPServer(('0.0.0.0',8443),handler)
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(str(tls/'tls.crt'),str(tls/'tls.key'))
    srv.socket=ctx.wrap_socket(srv.socket,server_side=True); srv.serve_forever()


if __name__=='__main__': main()
