import ipaddress
import re
import json
import urllib.request
import subprocess
from pathlib import Path

raw = subprocess.check_output(['/sbin/ifconfig', 'en1'], text=True)
addresses = re.findall(r'inet6 ([0-9a-f:]+) prefixlen \d+ autoconf secured', raw)
address = next((a for a in addresses if ipaddress.ip_address(a).is_global), None)
if not address:
    raise SystemExit('No stable global IPv6 on en1')
auth = json.loads(Path(__file__).with_name('dns-auth.json').read_text())
path = 'zones/' + auth['zone'] + '/dns_records'

def api(path, method='GET', config=None):
    data = None if config is None else json.dumps(config).encode()
    request = urllib.request.Request('https://api.cloudflare.com/client/v4/' + path,
        data=data, method=method, headers={
            'Authorization': 'Bearer ' + auth['token'],
            'Content-Type': 'application/json',
        })
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    if not result['success']:
        raise ValueError('Cloudflare DNS update failed')
    return result['result']

for name in ('dav6.mossdock.autos', 'photos6.mossdock.autos', 'books6.mossdock.autos'):
    records = api(path + '?name=' + name)
    config = dict(type='AAAA', name=name, content=address, proxied=False, ttl=120)
    if not records:
        api(path, 'POST', config)
    elif len(records) != 1 or records[0]['type'] != 'AAAA' or records[0]['proxied']:
        raise SystemExit(f'Unexpected DNS records for {name}')
    elif records[0]['content'] != address:
        api(path + '/' + records[0]['id'], 'PATCH', config)
