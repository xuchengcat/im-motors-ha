"""Shared durable mileage cache; source strategies live in account folders."""
from datetime import datetime
import math
from types import MappingProxyType
from zoneinfo import ZoneInfo

from .pyim_china.credential_store import key_context, vault_for_path
from .pyim_china.runtime_lock import AccountFileLock
from .trip_api import TripError

from .const import ACCOUNT_PRIMARY, ACCOUNT_AUTHORIZED


def numeric_mileage(value):
    if (type(value) not in (int, float) or not 0 <= value < 0x7FFFFFFF
            or not math.isfinite(value)):
        raise TripError('invalid_odometer')
    return value


def mileage(data, vin):
    """Primary source parser retained for offline callers."""
    if not isinstance(data, dict) or data.get('vin') != vin:
        raise TripError('vehicle_mismatch')
    return numeric_mileage(data.get('totalMileage'))


class OdometerReader:
    def __init__(self, client, *, account_kind=ACCOUNT_PRIMARY, reader=None):
        self.client = client
        self.account_kind = account_kind
        if account_kind == ACCOUNT_PRIMARY:
            from .primary.odometer import PrimaryOdometerSource
            self.source = PrimaryOdometerSource(client, reader)
        elif account_kind == ACCOUNT_AUTHORIZED:
            from .authorized.odometer import AuthorizedOdometerSource
            self.source = AuthorizedOdometerSource(client, reader)
        else:
            raise ValueError('Unsupported account type')
        self.reader = self.source.reader
        self.source_field = self.source.field

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
                'error': 'storage_unavailable', 'trip_date': day, 'source_field': self.source_field}
                for v in snapshot.vehicles})
        result = {}
        changed = False
        for vehicle in snapshot.vehicles:
            identifier = vehicle.identifier
            cache_key = self.account_kind+':'+identifier
            previous = saved['records'].get(cache_key, {})
            legacy = saved['records'].get(identifier, {})
            if not previous and legacy.get('source_field') == self.source_field:
                previous = legacy
                previous.setdefault('source', self.source.name)
                previous.setdefault('cache_date', previous.get('trip_date'))
                saved['records'][cache_key] = saved['records'].pop(identifier)
                changed = True
            age = now-previous.get('attempted_at', 0)
            due = (force or not previous or previous.get('cache_date', previous.get('trip_date')) != day
                   or age < 0 or age >= c.telemetry_interval_ms)
            if not due:
                result[identifier] = {k:v for k,v in previous.items() if k != 'last_confirmed'}
                continue
            record = {'value': None, 'status': 'error', 'source_field': self.source_field,
                      'trip_date': day if self.account_kind == ACCOUNT_PRIMARY else None,
                      'cache_date': day, 'source': self.source.name, 'attempted_at': now, 'retrieved_at': None}
            last = previous.get('last_confirmed')
            if type(last) in (int, float):
                record['last_confirmed'] = last
            try:
                if (self.source.single_vehicle and len(vins) != 1) or identifier not in vins:
                    raise TripError('multiple_vehicles')
                data = self.source.read(day, identifier)
                if datetime.fromtimestamp(c.clock()/1000, ZoneInfo('Asia/Shanghai')).date().isoformat() != day:
                    raise TripError('calendar_day_changed')
                value = self.source.parse(data, vins[identifier])
                if self.account_kind == ACCOUNT_AUTHORIZED:
                    record["source_updated"] = data.get("source_updated")
                if last is not None and value < last:
                    raise TripError('odometer_decreased')
                record.update(value=value, status='ok', error=None,
                    retrieved_at=c.clock(), last_confirmed=value)
            except TripError as exc:
                record['error'] = exc.code
            except Exception:
                record['error'] = 'request_failed'
            saved['records'][cache_key] = record
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
