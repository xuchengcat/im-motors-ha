"""Admin-only local archive APIs. Only explicit address completion uses networking."""
from datetime import date, datetime
from functools import partial
import math

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from ..capabilities import hybrid
from ..const import DOMAIN
from ..trip_api import TripError, AmapGeocoder
from ..trip_store import validate_range
from ..trip_web import require_admin
from ..trips import settings

ERRORS = {'invalid_record': '记录格式不正确，请检查日期、时间和数值。',
          'record_changed': '记录已经变化，请刷新后再保存。',
          'record_unavailable': '记录不存在。', 'vehicle_unavailable': '车辆不可用。',
          'entry_unavailable': '账号不可用。', 'hybrid_required': '该车辆未确认支持燃油功能。',
          'key_required': '请先在集成选项中配置高德 Web服务 Key。'}


def manager_for(hass, entry_id, vehicle):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not getattr(entry, 'runtime_data', None):
        raise TripError('entry_unavailable')
    coordinator = entry.runtime_data
    if not coordinator.data or vehicle not in {v.identifier for v in coordinator.data.vehicles}:
        raise TripError('vehicle_unavailable')
    manager = getattr(coordinator, 'energy', None)
    if manager is None:
        raise TripError('entry_unavailable')
    return manager, hybrid(coordinator, vehicle) is True


def public_record(record, is_hybrid):
    record = dict(record)
    if not is_hybrid:
        for key in ('fuel_start', 'fuel_end', 'fuel_before', 'fuel_after'):
            record.pop(key, None)
        if 'manual' in record:
            record['manual'] = {k: v for k, v in record['manual'].items() if not k.startswith('fuel_')}
    return record


def validate_manual(kind, value):
    """Bounded, finite user inputs; missing is different from zero."""
    if not isinstance(value, dict):
        raise TripError('invalid_record')
    common = {'date', 'start_time', 'end_time', 'address', 'note', 'amount'}
    allowed = common | ({'energy_kwh', 'soc_start', 'soc_end'} if kind == 'charge'
                        else {'litres', 'fuel_before', 'fuel_after', 'confirmed'})
    if set(value) - allowed:
        raise TripError('invalid_record')
    try:
        if date.fromisoformat(value['date']).isoformat() != value['date']:
            raise ValueError()
        result = {'date': value['date']}
        for key, limit in (('address', 256), ('note', 1000)):
            if key in value:
                if not isinstance(value[key], str) or len(value[key]) > limit or any(ord(c) < 32 for c in value[key]):
                    raise ValueError()
                result[key] = value[key].strip()
        for key in ('start_time', 'end_time'):
            if value.get(key):
                if not isinstance(value[key], str) or len(value[key]) > 40:
                    raise ValueError()
                stamp = datetime.fromisoformat(value[key])
                if stamp.tzinfo is None:
                    raise ValueError()
                result[key] = stamp.isoformat()
        if result.get('start_time') and result.get('end_time'):
            if datetime.fromisoformat(result['end_time']) < datetime.fromisoformat(result['start_time']):
                raise ValueError()
        bounds = {'amount': 1000000, 'energy_kwh': 10000, 'litres': 10000,
                  'soc_start': 100, 'soc_end': 100, 'fuel_before': 100, 'fuel_after': 100}
        for key in allowed & bounds.keys():
            v = value.get(key)
            if v is None:
                continue
            if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= bounds[key]:
                raise ValueError()
            result[key] = v
        if 'confirmed' in value:
            if type(value['confirmed']) is not bool:
                raise ValueError()
            result['confirmed'] = value['confirmed']
        return result
    except (ValueError, TypeError, KeyError, OverflowError):
        raise TripError('invalid_record') from None


async def query(hass, msg):
    manager, is_hybrid = manager_for(hass, msg['entry_id'], msg['vehicle_id'])
    first, last = validate_range(msg['start_date'], msg['end_date'])
    if (last - first).days > 365:
        raise TripError('invalid_record')
    rows = await hass.async_add_executor_job(manager.store.query, msg['vehicle_id'], msg['start_date'], msg['end_date'])
    rows = [public_record(r, is_hybrid) for r in rows if is_hybrid or r['kind'] == 'charge']
    if not msg.get('include_hidden', False):
        rows = [r for r in rows if not r.get('hidden')]
    charges = [r for r in rows if r['kind'] == 'charge' and not r.get('hidden')]
    metered = [r['manual']['energy_kwh'] for r in charges if r.get('manual', {}).get('energy_kwh') is not None]
    state = await hass.async_add_executor_job(manager.store.status, msg['vehicle_id'])
    observation = manager.errors.get(msg['vehicle_id']) or state.get('observation', 'waiting')
    previous = state.get('previous', {})
    if previous.get('time') and manager.enabled:
        from datetime import timezone
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous['time'])).total_seconds()
        if age > max(900, manager.coordinator.telemetry_interval_minutes * 180):
            observation = 'sampling_interrupted'
    stats = {'charges': len(charges), 'entered_energy_kwh': sum(metered) if metered else None,
             'charges_with_energy': len(metered), 'charges_without_energy': len(charges) - len(metered)}
    if is_hybrid:
        stats['refuels'] = sum(r['kind'] == 'refuel' and not r.get('hidden') for r in rows)
    if msg.get('kind'):
        if msg['kind'] == 'refuel' and not is_hybrid:
            raise TripError('hybrid_required')
        rows = [r for r in rows if r['kind'] == msg['kind']]
    offset, limit = msg.get('offset', 0), msg.get('limit', 100)
    return {'records': rows[offset:offset + limit], 'total': len(rows), 'stats': stats,
            'hybrid': is_hybrid, 'recording_enabled': manager.enabled,
            'observation': observation, 'last_sample': previous.get('time'),
            'interval_minutes': manager.coordinator.telemetry_interval_minutes}


async def edit(hass, msg):
    manager, is_hybrid = manager_for(hass, msg['entry_id'], msg['vehicle_id'])
    kind = msg['kind']
    if kind == 'refuel' and not is_hybrid:
        raise TripError('hybrid_required')
    old = None
    if msg.get('record_id'):
        old = await hass.async_add_executor_job(manager.store.detail, msg['vehicle_id'], msg['record_id'])
        if old['kind'] != kind:
            raise TripError('invalid_record')
    values = validate_manual(kind, msg['values'])
    if old and old['source'] == 'ha_telemetry' and values['date'] != old['date']:
        raise TripError('invalid_record')
    result = await hass.async_add_executor_job(partial(manager.store.save_manual,
        msg['vehicle_id'], kind, values, identifier=msg.get('record_id'), revision=msg.get('revision')))
    return public_record(result, is_hybrid)


BASE = {vol.Required('entry_id'): str, vol.Required('vehicle_id'): str}
RANGE = {**BASE, vol.Required('start_date'): str, vol.Required('end_date'): str,
         vol.Optional('kind'): vol.In(('charge', 'refuel')),
         vol.Optional('include_hidden', default=False): bool,
         vol.Optional('offset', default=0): vol.All(int, vol.Range(min=0, max=100000)),
         vol.Optional('limit', default=100): vol.All(int, vol.Range(min=1, max=500))}
EDIT = {**BASE, vol.Required('kind'): vol.In(('charge', 'refuel')),
        vol.Required('values'): dict, vol.Optional('record_id'): vol.All(str, vol.Length(min=1, max=64)),
        vol.Optional('revision'): vol.All(int, vol.Range(min=1))}


@websocket_api.websocket_command({vol.Required('type'): 'im_motors/energy/entries'})
@websocket_api.async_response
async def ws_entries(hass, connection, msg):
    require_admin(connection)
    rows = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        c = getattr(entry, 'runtime_data', None)
        if c is None or c.data is None or getattr(c, 'energy', None) is None:
            continue
        rows.append({'entry_id': entry.entry_id, 'title': entry.title,
                     'vehicles': [{'id': v.identifier, 'name': v.name, 'hybrid': hybrid(c, v.identifier) is True}
                                  for v in c.data.vehicles]})
    connection.send_result(msg['id'], rows)


@websocket_api.websocket_command({vol.Required('type'): 'im_motors/energy/query', **RANGE})
@websocket_api.async_response
async def ws_query(hass, connection, msg):
    require_admin(connection)
    await respond(connection, msg, query(hass, msg))


@websocket_api.websocket_command({vol.Required('type'): 'im_motors/energy/save', **EDIT})
@websocket_api.async_response
async def ws_save(hass, connection, msg):
    require_admin(connection)
    await respond(connection, msg, edit(hass, msg))


async def respond(connection, msg, action):
    try:
        connection.send_result(msg['id'], await action)
    except TripError as error:
        connection.send_error(msg['id'], error.code, ERRORS.get(error.code, '操作失败，原记录已保留。'))
    except Exception:
        connection.send_error(msg['id'], 'archive_error', '本地档案不可用，请检查存储。')


@websocket_api.websocket_command({vol.Required('type'): 'im_motors/energy/hide', **BASE,
    vol.Required('record_id'): str, vol.Required('revision'): vol.All(int, vol.Range(min=1)),
    vol.Required('hidden'): bool})
@websocket_api.async_response
async def ws_hide(hass, connection, msg):
    require_admin(connection)
    async def action():
        manager, is_hybrid = manager_for(hass, msg['entry_id'], msg['vehicle_id'])
        old = await hass.async_add_executor_job(manager.store.detail, msg['vehicle_id'], msg['record_id'])
        if old['kind'] == 'refuel' and not is_hybrid:
            raise TripError('hybrid_required')
        await hass.async_add_executor_job(manager.store.set_hidden, msg['vehicle_id'], msg['record_id'], msg['revision'], msg['hidden'])
        return {'saved': True}
    await respond(connection, msg, action())


@websocket_api.websocket_command({vol.Required('type'): 'im_motors/energy/geocode', **BASE,
    vol.Required('record_id'): str})
@websocket_api.async_response
async def ws_geocode(hass, connection, msg):
    require_admin(connection)
    async def action():
        manager, is_hybrid = manager_for(hass, msg['entry_id'], msg['vehicle_id'])
        r = await hass.async_add_executor_job(manager.store.detail, msg['vehicle_id'], msg['record_id'])
        if r['kind'] == 'refuel' and not is_hybrid:
            raise TripError('hybrid_required')
        point = r.get('coordinates')
        if not point:
            raise TripError('invalid_record')
        saved = await hass.async_add_executor_job(settings, manager.coordinator.client)
        if not saved.get('amap_api_key'):
            raise TripError('key_required')
        # Reuse the existing account geocache. No request on local query or render.
        cache = manager.coordinator.trips.store
        address = await hass.async_add_executor_job(cache.address, point)
        if not address:
            address = await hass.async_add_executor_job(AmapGeocoder(saved['amap_api_key']).reverse,
                                                       point['latitude'], point['longitude'])
            await hass.async_add_executor_job(cache.address, point, address)
        values = dict(r.get('manual', {}), date=r['date'], address=address['address'])
        await hass.async_add_executor_job(partial(manager.store.save_manual,
            msg['vehicle_id'], r['kind'], values, identifier=r['id'], revision=r['revision']))
        return {'saved': True}
    await respond(connection, msg, action())


async def async_setup_web(hass):
    key = DOMAIN + '_energy_web'
    if hass.data.get(key):
        return
    for command in (ws_entries, ws_query, ws_save, ws_hide, ws_geocode):
        websocket_api.async_register_command(hass, command)

    async def handle(call):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        try:
            return await (query(hass, call.data) if call.service == 'query_energy_history' else edit(hass, call.data))
        except TripError as error:
            raise HomeAssistantError(ERRORS.get(error.code, '本地档案操作失败。')) from None

    hass.services.async_register(DOMAIN, 'query_energy_history', handle,
        schema=vol.Schema(RANGE), supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, 'save_energy_record', handle,
        schema=vol.Schema(EDIT), supports_response=SupportsResponse.ONLY)
    hass.data[key] = True
