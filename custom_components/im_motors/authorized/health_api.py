"""Read vehicle health/maintenance status for the explicitly associated VIN."""
import base64
from datetime import datetime
import http.client
import json
from pathlib import Path
import secrets
import ssl
from zoneinfo import ZoneInfo

from ..h5 import signing, decode_response
from ..pyim_china.account_auth import RefreshDecision
from ..pyim_china.credential_store import key_context
from ..pyim_china.runtime_lock import AccountFileLock
from ..trip_api import TripError

PATH = '/app/planet/api/v1/maintenance/status'


def status_post(path, headers, body):
    # This POST reads status. Report generation, booking and controls are absent.
    if path != PATH:
        raise TripError('invalid_request')
    connection = http.client.HTTPSConnection('m.immotors.com', timeout=25, context=ssl.create_default_context())
    try:
        connection.request('POST', path, body=body.encode('utf-8'), headers=headers)
        response = connection.getresponse()
        raw = response.read(2 * 1024 * 1024 + 1)
        if response.status in (401, 403):
            raise TripError('authentication_failed')
        if response.status != 200 or len(raw) > 2 * 1024 * 1024:
            raise TripError('http_error')
        return raw.decode('utf-8'), {k.lower():v for k,v in response.getheaders()}
    except TripError:
        raise
    except Exception:
        raise TripError('network_error') from None
    finally:
        connection.close()


def updated_time(value):
    if not isinstance(value, str):
        return None
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat()
        except ValueError:
            pass
    return None


class HealthReader:
    def __init__(self, client, *, post=status_post):
        self.client = client
        self.post = post

    def read(self, vehicle):
        c = self.client
        try:
            with key_context(c.key_file), AccountFileLock(c.data_dir):
                if (c.data_dir/'ha-fault.imvault').exists():
                    raise TripError('account_paused')
                _, creds, _ = c._validate()
                if creds.refresh_decision(c.clock()) != RefreshDecision.CURRENT:
                    raise TripError('authentication_refresh_required')
                metadata = c._vault('ha-metadata.imvault')._load_payload()
                vin = metadata.get('vins', {}).get(vehicle)
                from ..pyim_china.client import _VIN
                if not isinstance(vin, str) or not _VIN.fullmatch(vin):
                    raise TripError('vehicle_unavailable')
                import hashlib
                if hashlib.sha256(vin.encode('ascii')).hexdigest() != vehicle:
                    raise TripError('vehicle_mismatch')
                cfg = json.loads(Path(__file__).parent.parent.joinpath('pyim_china','h5_protocol.json').read_text())
                now = str(c.clock())
                url = 'https://m.immotors.com'+PATH
                body = json.dumps({'vin':vin}, separators=(',',':'))
                signed = {'x-app-key':'android-h5','x-nonce':secrets.token_hex(5),
                          'x-timestamp':now,'x-encrypt':'false'}
                headers = {**signed, 'Authorization':'Bearer '+creds.access_token,
                    'x-signature':signing('POST',url,signed,base64.b64decode(cfg['app_secret_b64']),body=body),
                    'x-original-path':url,'x-h5client-version':'c7f6207e',
                    'x-transaction-id':now+'@'+secrets.token_hex(4),
                    'Referer':'https://m.immotors.com/app/autoserv/maintenance-reminder',
                    'Origin':'https://m.immotors.com','X-Requested-With':'com.immotors.lapp',
                    'Content-Type':'application/json;charset=UTF-8','Accept':'application/json',
                    'User-Agent':'Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36 ImmotorsApp/3.2.7'}
                raw, returned = self.post(PATH, headers, body)
                data = decode_response(raw, returned, cfg['encrypt_key'], h5=True)
                # The verified endpoint omits VIN. Bind to the validated request,
                # and reject a conflicting VIN if a newer response supplies one.
                if 'vin' in data and data['vin'] != vin:
                    raise TripError('vehicle_mismatch')
                return {'mileage':data.get('mileage'), 'requested_vin':vin,
                        'source_updated':updated_time(data.get('updateTime'))}
        except TripError:
            raise
        except Exception:
            raise TripError('account_unavailable') from None
