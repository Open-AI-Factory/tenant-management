"""Current UTC stand-in for an external profiler; no virtual clock or reset."""
import argparse
import base64
import getpass
import json
import ssl
import uuid
import urllib.request
from datetime import datetime, timedelta, timezone


def make_event(tenant_uid: str, sequence: int, devices: float, start_delay: int = 30, duration: int = 120) -> dict:
    now = datetime.now(timezone.utc)
    def at(value): return value.isoformat().replace('+00:00', 'Z')
    return {'eventId': str(uuid.uuid4()), 'source': 'dummy-profiler', 'tenantUID': tenant_uid,
            'poolKey': 'example-gpu-pool', 'unit': 'device', 'sequence': sequence,
            'issuedAt': at(now), 'windowStart': at(now + timedelta(seconds=start_delay)),
            'windowEnd': at(now + timedelta(seconds=start_delay + duration)),
            'expectedDevices': devices, 'confidence': 0.95}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant-uid', required=True)
    parser.add_argument('--sequence', type=int, required=True)
    parser.add_argument('--devices', type=float, required=True)
    parser.add_argument('--start-delay', type=int, default=30)
    parser.add_argument('--duration', type=int, default=120)
    parser.add_argument('--url', help='Omit to print a current UTC JSON forecast')
    parser.add_argument('--ca-file', help='Trusted management website CA/certificate')
    parser.add_argument('--user', help='Grafana administrator login; password is prompted')
    args = parser.parse_args()
    event = make_event(args.tenant_uid, args.sequence, args.devices, args.start_delay, args.duration)
    if not args.url:
        print(json.dumps(event, ensure_ascii=False, allow_nan=False, indent=2)); return
    if not args.url.startswith('https://'):
        parser.error('Live submission requires HTTPS')
    user = args.user or input('Grafana administrator: ')
    password = getpass.getpass('Grafana password: ')
    auth = 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()
    context = ssl.create_default_context(cafile=args.ca_file)
    def request(path, body=None, csrf=None):
        headers = {'Authorization': auth}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-CSRF-Token': csrf})
        req = urllib.request.Request(args.url.rstrip('/') + path, headers=headers,
                                     data=json.dumps(body, allow_nan=False).encode() if body is not None else None)
        with urllib.request.urlopen(req, context=context, timeout=20) as response:
            return json.load(response)
    session = request('/api/session')
    print(json.dumps(request('/api/kueue-quota/forecasts', event, session['csrf']), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
