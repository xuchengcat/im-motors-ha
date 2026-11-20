"""Current cumulative kilometres from the App's daily driving H5 response."""
from datetime import datetime
import math
from types import MappingProxyType
from zoneinfo import ZoneInfo

from .cloud_trip_api import TripReader
from .pyim_china.credential_store import key_context, vault_for_path
from .pyim_china.runtime_lock import AccountFileLock
from .trip_api import TripError

SOURCE = 'drive/daily/new.totalMileage'


def mileage(data, vin):
    if not isinstance(data, dict) or data.get('vin') != vin:
        raise TripError('vehicle_mismatch')
    value = data.get('totalMileage')
    if (type(value) not in (int, float) or not 0 <= value < 0x7FFFFFFF
            or not math.isfinite(value)):
        raise TripError('invalid_odometer')
    return value


class OdometerReader:
    def __init__(self, client, *, reader=None):
        self.client = client
        self.reader = reader or TripReader(client)

    def _load(self):
        c = self.client
        with key_context(c.key_file), AccountFileLock(c.data_dir):
            vault = vault_for_path(c.data_dir/'odometer.imvault')
            saved = vault._load_payload() if vault.path.exists() else {'kind': 'h5-odometer', 'records': {}}
            if saved.get('kind') != 'h5-odometer' or not isinstance(saved.get('records'), dict):
                raise TripError('invalid_storage')
            metadata = vault_for_path(c.data_dir/'ha-metadata.imvault')._load_payload()
            return saved, metadata.get('vins', {})

    def update(self, snapshot, *, force=False):
        c = self.client
        now = c.clock()
        # The official calendar uses Shanghai dates, independent of HA's zone.
        day = datetime.fromtimestamp(now/1000, ZoneInfo('Asia/Shanghai')).date().isoformat()
        try:
            saved, vins = self._load()
        except Exception:
            return MappingProxyType({v.identifier: {'value': None, 'status': 'error',
                'error': 'storage_unavailable', 'trip_date': day, 'source_field': SOURCE}
                for v in snapshot.vehicles})
        result = {}
        changed = False
        for vehicle in snapshot.vehicles:
            identifier = vehicle.identifier
            previous = saved['records'].get(identifier, {})
            age = now-previous.get('attempted_at', 0)
            due = (force or not previous or previous.get('trip_date') != day
                   or age < 0 or age >= c.telemetry_interval_ms)
            if not due:
                result[identifier] = {k:v for k,v in previous.items() if k != 'last_confirmed'}
                continue
            record = {'value': None, 'status': 'error', 'source_field': SOURCE,
                      'trip_date': day, 'attempted_at': now, 'retrieved_at': None}
            last = previous.get('last_confirmed')
            if type(last) in (int, float):
                record['last_confirmed'] = last
            try:
                if len(vins) != 1 or identifier not in vins:
                    raise TripError('multiple_vehicles')
                data = self.reader.read('summary', {'tripDate': day}, identifier)
                if datetime.fromtimestamp(c.clock()/1000, ZoneInfo('Asia/Shanghai')).date().isoformat() != day:
                    raise TripError('calendar_day_changed')
                value = mileage(data, vins[identifier])
                if last is not None and value < last:
                    raise TripError('odometer_decreased')
                record.update(value=value, status='ok', error=None,
                    retrieved_at=c.clock(), last_confirmed=value)
            except TripError as exc:
                record['error'] = exc.code
            except Exception:
                record['error'] = 'request_failed'
            saved['records'][identifier] = record
            result[identifier] = {k:v for k,v in record.items() if k != 'last_confirmed'}
            changed = True
        if changed:
            try:
                with key_context(c.key_file), AccountFileLock(c.data_dir):
                    vault_for_path(c.data_dir/'odometer.imvault')._save_payload(saved)
            except Exception:
                # Do not publish a value without its durable polling checkpoint.
                return MappingProxyType({k: {**v, 'value': None, 'status': 'error',
                    'error': 'storage_unavailable'} for k,v in result.items()})
        return MappingProxyType(result)
