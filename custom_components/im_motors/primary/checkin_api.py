"""App-confirmed H5 check-in calls, sharing the protected HA account session."""
import base64
import http.client
import json
from pathlib import Path
import secrets
import ssl
from urllib.parse import urlencode

from ..h5 import decode_response, signing
from ..pyim_china.account_auth import RefreshDecision
from ..pyim_china.standalone_refresh_probe import refresh_once, load_session
from ..trip_api import TripError

BASE = '/app/planet/api/v1/checkin/'


def send(method, path, headers, body):
    if (method, path.split('?')[0]) not in {
            ('GET', BASE+'info'), ('GET', BASE+'record'), ('POST', BASE+'check')}:
        raise TripError('invalid_request')
    connection = http.client.HTTPSConnection('m.immotors.com', timeout=25, context=ssl.create_default_context())
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(2 * 1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 2 * 1024 * 1024:
            raise TripError('http_error')
        return raw.decode(), {k.lower(): v for k,v in response.getheaders()}
    finally:
        connection.close()


class CheckinApi:
    """Caller holds key_context and AccountFileLock across the daily transaction."""
    def __init__(self, client, *, transport=send):
        self.client = client
        self.transport = transport

    def call(self, operation, day=None):
        if operation not in {'info', 'record', 'check'}:
            raise TripError('invalid_request')
        c = self.client
        if (c.data_dir/'ha-fault.imvault').exists():
            raise TripError('account_paused')
        _, creds, native = c._validate()
        if creds.refresh_decision(c.clock()) == RefreshDecision.DUE:
            refresh_once(c.data_dir/'session.imvault',
                c.data_dir/'attempts'/('refresh-'+secrets.token_hex(16)+'.imvault'),
                load_config=lambda: native, transport_factory=c.transport_factory)
            _, creds = load_session(c.data_dir/'session.imvault')
        cfg = json.loads(Path(__file__).parent.parent.joinpath('pyim_china', 'h5_protocol.json').read_text())
        method = 'POST' if operation == 'check' else 'GET'
        path = BASE+operation
        query = ''
        if operation == 'record':
            from datetime import date
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                raise TripError('invalid_request')
            query = urlencode({'startTime': day, 'endTime': day})
        body = '{}' if operation == 'check' else None
        now = str(c.clock())
        url = 'https://m.immotors.com'+path
        signed = {'x-app-key': 'android-h5', 'x-nonce': secrets.token_hex(5),
                  'x-timestamp': now, 'x-encrypt': 'false'}
        # Use H5 headers, not the native API's version/device headers. The latter
        # caused 22064 on info; this browser-header profile was verified live.
        headers = {**signed, 'Authorization': 'Bearer '+creds.access_token,
            'x-signature': signing(method, url, signed, base64.b64decode(cfg['app_secret_b64']), query, body),
            'x-original-path': url, 'x-h5client-version': 'af508e20',
            'x-transaction-id': now+'@'+secrets.token_hex(4),
            'Referer': 'https://m.immotors.com/app/community/checkin',
            'Origin': 'https://m.immotors.com', 'X-Requested-With': 'com.immotors.lapp',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36 ImmotorsApp/3.2.7',
            'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json;charset=UTF-8'
        raw, returned = self.transport(method, path+('?' + query if query else ''), headers, body)
        return decode_response(raw, returned, cfg['encrypt_key'], h5=True)


def checked_today(record, day):
    """Same rule as the H5 calendar: existence of today's signTime in blocks."""
    if not isinstance(record, dict) or not isinstance(record.get('blocks'), list):
        raise TripError('invalid_response')
    for row in record['blocks']:
        if not isinstance(row, dict) or not isinstance(row.get('signTime'), str) or 'signType' not in row:
            raise TripError('invalid_response')
    return any(row['signTime'] == day for row in record['blocks'])
