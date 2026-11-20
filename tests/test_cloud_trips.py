"""Official calendar protocol, paging, durable archives and HA role separation."""
import asyncio
import base64
from datetime import datetime, timedelta
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest

from conftest import VIN, response
from test_ha import create_entry
from custom_components.im_motors.primary.trip_api import TripReader, decode_response, h5_signature
from custom_components.im_motors.primary.trips import CloudTripManager, calendar_dates, route_points
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.offline_encryption import encrypt_text
from custom_components.im_motors.trip_api import TripError
from custom_components.im_motors.trip_store import TripStore
from custom_components.im_motors.trip_web import ws_entries, ws_query

VEHICLE = sha256(VIN.encode()).hexdigest()
DAY = '2026-10-18'


def row(i):
    return {'tripId': f'synthetic-{i}', 'tripStartTime': DAY+' 10:00:00',
            'tripEndTime': DAY+' 10:10:00', 'tripMileage': 1.2, 'tripDuration': 10}


def detail():
    return {'tripStartLat': 31., 'tripStartLon': 121., 'tripEndLat': 31.1,
            'tripEndLon': 121.1, 'poiList': [
                {'latitude': 31., 'longitude': 121., 'instantaneousVelocity': 20},
                {'latitude': 0., 'longitude': 0.},
                {'latitude': 31.1, 'longitude': 121.1, 'instantaneousVelocity': 30}]}


@pytest.fixture
def manager(hass, account):
    coordinator = SimpleNamespace(client=account['client'], _request_lock=asyncio.Lock(),
        telemetry_interval_minutes=60, data=SimpleNamespace(vehicles=[SimpleNamespace(identifier=VEHICLE)]))
    entry = SimpleNamespace(data={'account_type': 'primary'}, options={'cloud_trip_history_enabled': True})
    return CloudTripManager(hass, entry, coordinator)


def test_h5_and_native_requests_are_scoped_and_signed(account):
    account['client'].update()
    get = Mock(return_value=(json.dumps({'resultCode': '200', 'data': {'tripStatus': 2}}), {}))
    reader = TripReader(account['client'], get=get)
    assert reader.read('summary', {'tripDate': DAY}, VEHICLE)['tripStatus'] == 2
    host, path, headers = get.call_args.args
    assert host == 'm.immotors.com' and path.endswith('tripDate='+DAY)
    assert headers['x-app-key'] == 'android-h5' and headers['vin'] == VIN
    assert headers['Origin'] == 'https://m.immotors.com'
    assert headers['X-Requested-With'] == 'com.immotors.lapp'
    assert 'wv' in headers['User-Agent']
    assert headers['x-original-path'].startswith('https://m.immotors.com/')
    assert len(headers['x-nonce']) == 10
    assert headers['x-signature']
    query = {'tripId': 'synthetic-1', 'tripStartTime': '1792288800000', 'tripEndTime': '1792289400000'}
    reader.read('detail', query, VEHICLE)
    host, path, headers = get.call_args.args
    assert host == 'mh.immotors.com' and path.startswith('/app/vus/v3/drive/daily/trip/detail?')
    assert headers['x-app-key'] == 'android'
    before = get.call_count
    with pytest.raises(TripError, match='vehicle_unavailable'):
        reader.read('summary', {'tripDate': DAY}, 'wrong-vehicle')
    with key_context(account['key']):
        vault_for_path(account['data']/'ha-fault.imvault')._save_payload({'reason': 'synthetic'})
    with pytest.raises(TripError, match='account_paused'):
        reader.read('summary', {'tripDate': DAY}, VEHICLE)
    assert get.call_count == before


@pytest.mark.parametrize('operation,query', [
    ('remove', {'tripId': 'synthetic'}), ('summary', {'tripDate': '2026-1-1'}),
    ('trips', {'tripDate': DAY, 'offset': 0, 'limit': 5}),
    ('trips', {'tripDate': DAY, 'offset': 1, 'limit': True}),
    ('detail', {'tripId': '', 'tripStartTime': '1', 'tripEndTime': '2'}),
    ('detail', {'tripId': 'synthetic', 'tripStartTime': '2', 'tripEndTime': '1'}),
])
def test_invalid_requests_never_reach_transport(account, operation, query):
    get = Mock()
    with pytest.raises(TripError, match='invalid_request'):
        TripReader(account['client'], get=get).read(operation, query, VEHICLE)
    get.assert_not_called()


def test_real_h5_encrypted_envelope_and_error_distinction():
    inner = {'resultCode': '200', 'data': {'tripStatus': 1, 'hasTripDates': ['20261018']}}
    outer = {'resultCode': 'success', 'data': {'cipherText': encrypt_text(json.dumps(inner), 'SYNTHETIC-KEY')}}
    assert decode_response(json.dumps(outer), {'x-encrypt': 'true'}, 'SYNTHETIC-KEY', h5=True) == inner['data']
    for code, expected in [('22064', 'protocol_rejected'), ('23373', 'not_vehicle_owner'), ('22061', 'authentication_failed')]:
        with pytest.raises(TripError, match=expected):
            decode_response(json.dumps({'resultCode': code, 'data': {}}), {}, 'SYNTHETIC-KEY', h5=True)


def test_calendar_and_route_points_preserve_missing_times():
    assert calendar_dates(['20261018', 20261019, '2026-10-18']) == [DAY, '2026-10-19']
    for value in [None, [True], ['20260230']]:
        with pytest.raises(TripError, match='invalid_response'):
            calendar_dates(value)
    points = route_points(detail())
    assert len(points) == 3 and points[1]['coordinates'] is None
    assert all(p['time'] is None for p in points)
    assert points[0]['speed_kmh'] == 20


async def test_complete_paging_details_and_atomic_retention(manager):
    await manager.setup()
    summary = {'tripStatus': 1, 'dayMileage': 9.1, 'vin': VIN}
    manager.read = AsyncMock(side_effect=[summary,
        {'totalCount': 6, 'blocks': [row(i) for i in range(5)]},
        {'totalCount': 6, 'blocks': [row(5)]}, *[detail() for _ in range(6)]])
    result = await manager.fetch_day(DAY, VEHICLE)
    assert result['list_complete'] and result['distance_km'] == 9.1
    assert result['source'] == 'official_cloud'
    assert manager.read.call_args_list[2].args[1]['offset'] == 2
    assert len(result['trips']) == 6 and len(result['trips'][0]['route_points']) == 3
    await manager.hass.async_add_executor_job(manager.store.save_day, VEHICLE, result)
    route = await manager.hass.async_add_executor_job(manager.store.detail, VEHICLE, 'synthetic-0')
    assert route['route_source'] == 'official_cloud' and len(route['endpoints']) == 2
    assert route['points'][0]['time'] is None and route['points'][1]['map_coordinates'] is None
    manager.read = AsyncMock(return_value={'tripStatus': 3})
    denied = await manager.fetch_day(DAY, VEHICLE)
    await manager.hass.async_add_executor_job(manager.store.save_day, VEHICLE, denied)
    retained = await manager.hass.async_add_executor_job(manager.store.trips, VEHICLE, DAY, DAY)
    assert len(retained) == 6 and all(t['source_present'] for t in retained)


@pytest.mark.parametrize('second', [
    {'totalCount': 6, 'blocks': [row(0)]},
    {'totalCount': 7, 'blocks': [row(5), row(6)]},
    {'totalCount': 6, 'blocks': []},
])
async def test_incomplete_or_repeated_pages_are_not_committed(manager, second):
    manager.read = AsyncMock(side_effect=[{'tripStatus': 1},
        {'totalCount': 6, 'blocks': [row(i) for i in range(5)]}, second])
    with pytest.raises(TripError, match='incomplete_day'):
        await manager.fetch_day(DAY, VEHICLE)


async def test_mismatched_vehicle_or_detail_stops_day(manager):
    manager.read = AsyncMock(return_value={'tripStatus': 1, 'vin': 'UNRELATED'})
    with pytest.raises(TripError, match='vehicle_mismatch'):
        await manager.fetch_day(DAY, VEHICLE)
    manager.read = AsyncMock(side_effect=[{'tripStatus': 1}, {'totalCount': 1, 'blocks': [row(1)]},
        dict(detail(), tripId='OTHER')])
    with pytest.raises(ValueError, match='tripId'):
        await manager.fetch_day(DAY, VEHICLE)


async def test_no_access_and_no_trips_are_different(manager):
    manager.read = AsyncMock(return_value={'tripStatus': 2})
    empty = await manager.fetch_day(DAY, VEHICLE)
    assert empty['status'] == 'no_trips' and empty['list_complete']
    manager.read = AsyncMock(return_value={'tripStatus': 3})
    denied = await manager.fetch_day(DAY, VEHICLE)
    assert denied['status'] == 'no_access' and not denied['list_complete']


async def test_role_and_date_limits_never_start_cloud_calls(manager):
    assert manager.enabled
    manager.entry.options['account_type'] = 'authorized'
    assert not manager.enabled
    with pytest.raises(TripError, match='cloud_history_disabled'):
        manager.start()
    manager.entry.options['account_type'] = 'primary'
    with pytest.raises(TripError, match='cloud_date_range'):
        manager.start(start_date='2026-01-01', end_date='2026-03-01')
    tomorrow = (datetime.now(ZoneInfo('Asia/Shanghai')).date()+timedelta(days=1)).isoformat()
    with pytest.raises(TripError, match='cloud_date_range'):
        manager.start(start_date=tomorrow, end_date=tomorrow)
    assert manager.task is None
    await manager.observe(object())
    assert manager.recorder is None and manager.store.path.name == 'cloud-trips.sqlite3'


async def test_real_ha_primary_setup_sync_and_web_calendar(hass, account):
    account['transport'].send.side_effect = [response([{'vin': VIN}]), response({'category': {'vin': VIN}})]
    entry = await create_entry(hass, account, options={'account_type': 'primary', 'cloud_trip_history_enabled': True})
    with patch('custom_components.im_motors.pyim_china.AccountClient', return_value=account['client']):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done(wait_background_tasks=True)
    manager = entry.runtime_data.trips
    assert isinstance(manager, CloudTripManager)
    manager.reader.read = Mock(side_effect=[{'tripStatus': 1, 'hasTripDates': ['20261018'], 'vin': VIN},
        {'totalCount': 1, 'blocks': [row(1)]}, detail()])
    manager.start(start_date=DAY, end_date=DAY)
    await manager.task
    assert manager.status['state'] == 'complete'
    assert manager.status['processed'] == 1
    manager.reader.read.reset_mock()
    manager.store.state('calendar_remote', {'available_dates': [DAY, '2026-10-17']})
    conn = Mock(user=SimpleNamespace(is_admin=True))
    ws_entries(hass, conn, {'id': 1, 'type': 'im_motors/trips/entries'})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert conn.send_result.call_args.args[1][0]['source'] == 'official_cloud'
    ws_query(hass, conn, {'id': 2, 'type': 'im_motors/trips/query', 'entry_id': entry.entry_id,
        'vehicle_id': VEHICLE, 'start_date': DAY, 'end_date': DAY, 'include_trips': True})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = conn.send_result.call_args.args[1]
    assert result['source'] == 'official_cloud'
    assert result['calendar']['available_dates'] == [DAY]
    assert result['calendar']['source'] == 'local_archive'
    manager.reader.read.assert_not_called()
    assert len(result['trips']) == 1
    with manager.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 0
