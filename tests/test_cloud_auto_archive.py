"""Daily checkpoints, incremental archive safety and database-only views."""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest

from conftest import VIN
from test_cloud_trips import DAY, VEHICLE, detail
from custom_components.im_motors.cloud_trips import CloudTripManager
from custom_components.im_motors.trip_api import TripError
from custom_components.im_motors.trip_store import TripStore
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED

NOW = datetime(2026, 11, 19, 3, tzinfo=ZoneInfo('Asia/Shanghai'))


def trip(day, i):
    return {'tripId': f'synthetic-{i}', 'tripStartTime': day+' 10:00:00',
            'tripEndTime': day+' 10:10:00', 'tripMileage': 1.2, 'tripDuration': 10}


@pytest.fixture
async def manager(hass, account):
    coordinator = SimpleNamespace(client=account['client'], _request_lock=asyncio.Lock(),
        telemetry_interval_minutes=60, data=SimpleNamespace(vehicles=[SimpleNamespace(identifier=VEHICLE)]))
    entry = SimpleNamespace(data={'account_type': 'primary'}, options={'cloud_trip_history_enabled': True})
    value = CloudTripManager(hass, entry, coordinator)
    value.now = lambda: NOW
    yield value
    await value.stop()


def provider(rows, *, fail_date=None, fail_code='business_error'):
    def read(operation, query, vehicle):
        assert vehicle == VEHICLE
        if operation == 'summary':
            day = query['tripDate']
            if day == fail_date:
                raise TripError(fail_code)
            return {'tripStatus': 1 if rows.get(day) else 2, 'dayMileage': 9.1,
                    'hasTripDates': sorted(rows), 'vin': VIN}
        if operation == 'trips':
            values = rows.get(query['tripDate'], [])
            start = (query['offset']-1)*5
            return {'totalCount': len(values), 'blocks': values[start:start+5]}
        if operation == 'detail':
            return detail()
        raise AssertionError(operation)
    return read


async def run_daily(manager):
    assert await manager.async_daily_sync()
    await manager.task


def test_daily_checkpoint_persists_and_interruption_can_resume(tmp_path):
    store = TripStore(tmp_path/'cloud.sqlite3');store.initialize()
    assert store.claim_daily_sync('2026-11-19', NOW.isoformat())
    store.finish_daily_sync('2026-11-19', 'complete', NOW.isoformat())
    reopened = TripStore(store.path)
    assert not reopened.claim_daily_sync('2026-11-19', NOW.isoformat())
    assert reopened.claim_daily_sync('2026-11-20', NOW.isoformat())
    reopened.finish_daily_sync('2026-11-20', 'error', NOW.isoformat(), 'authentication_failed')
    assert not reopened.claim_daily_sync('2026-11-20', NOW.isoformat())
    assert reopened.claim_daily_sync('2026-11-21', NOW.isoformat())
    reopened.finish_daily_sync('2026-11-21', 'interrupted', NOW.isoformat(), 'interrupted')
    assert reopened.claim_daily_sync('2026-11-21', NOW.isoformat())
    assert reopened.state('cloud_auto_sync')['resumes'] == 1


async def test_daily_archive_filters_window_and_only_downloads_missing_ids(manager):
    rows = {DAY: [trip(DAY, 1)], '2026-11-18': [trip('2026-11-18', 2)],
            '2026-01-01': [trip('2026-01-01', 99)]}
    await manager.setup()
    manager.reader.read = Mock(side_effect=provider(rows))
    await run_daily(manager)
    assert manager.status['state'] == 'complete'
    assert manager.status['downloaded_details'] == 2
    assert all(call.args[1].get('tripDate') != '2026-01-01' for call in manager.reader.read.call_args_list)
    calls = manager.reader.read.call_count
    assert not await manager.async_daily_sync()
    assert manager.reader.read.call_count == calls
    archived = manager.store.cloud_payloads(VEHICLE, DAY)['synthetic-1']
    archived.update(start_address='SYNTHETIC-ADDRESS', start_address_source='amap_web_service')
    manager.store.update_trip(VEHICLE, archived)
    manager.now = lambda: NOW+timedelta(days=1)
    rows['2026-11-18'].append(trip('2026-11-18', 3))
    manager.reader.read.reset_mock()
    await run_daily(manager)
    assert manager.status['state'] == 'complete' and manager.status['downloaded_details'] == 1
    details = [c.args[1]['tripId'] for c in manager.reader.read.call_args_list if c.args[0] == 'detail']
    assert details == ['synthetic-3']
    retained = manager.store.cloud_payloads(VEHICLE, DAY)['synthetic-1']
    assert retained['start_address'] == 'SYNTHETIC-ADDRESS'
    assert len(retained['route_points']) == 3
    assert manager.store.state('cloud_auto_sync')['last_success_date'] == '2026-11-20'


async def test_changed_remote_trip_refreshes_detail_without_duplicate(manager):
    rows = {DAY: [trip(DAY, 1)]}
    await manager.setup();manager.reader.read = Mock(side_effect=provider(rows))
    await run_daily(manager)
    rows[DAY][0]['tripMileage'] = 3.4
    manager.now = lambda: NOW+timedelta(days=1)
    await run_daily(manager)
    saved = manager.store.trips(VEHICLE, DAY, DAY)
    assert len(saved) == 1 and saved[0]['distance_km'] == 3.4
    assert manager.status['downloaded_details'] == 1


async def test_ha_started_event_schedules_once_and_unload_removes_timer(manager):
    remove = Mock()
    manager.reader.read = Mock(side_effect=provider({}))
    with patch('custom_components.im_motors.cloud_trips.async_track_time_change', return_value=remove) as track:
        await manager.setup()
        assert track.call_args.kwargs == {'hour': 3, 'minute': 0, 'second': 0}
        manager.hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await manager.hass.async_block_till_done(wait_background_tasks=True)
        assert manager.store.state('cloud_auto_sync')['last_success_date'] == '2026-11-19'
        count = manager.reader.read.call_count
        await manager._scheduled(NOW)
        await manager.hass.async_block_till_done(wait_background_tasks=True)
        assert manager.reader.read.call_count == count
        await manager.stop()
        remove.assert_called_once()


async def test_disabled_role_never_schedules_or_reads(manager):
    manager.entry.options['account_type'] = 'authorized'
    manager.reader.read = Mock()
    with patch('custom_components.im_motors.cloud_trips.async_track_time_change') as track:
        await manager.setup()
        track.assert_not_called()
    assert not await manager.async_daily_sync()
    manager.reader.read.assert_not_called()


async def test_failed_date_does_not_block_other_archives(manager):
    rows = {DAY: [trip(DAY, 1)], '2026-11-18': [trip('2026-11-18', 2)]}
    await manager.setup();manager.reader.read = Mock(side_effect=provider(rows, fail_date=DAY))
    await run_daily(manager)
    assert manager.status['state'] == 'error' and manager.status['error'] == 'partial_archive'
    assert manager.status['failed_dates'] == [DAY]
    assert manager.store.stats(VEHICLE)['trips'] == 1
    assert not await manager.async_daily_sync()


async def test_authentication_failure_stops_and_is_not_retried_same_day(manager):
    await manager.setup()
    manager.reader.read = Mock(side_effect=TripError('authentication_failed'))
    await run_daily(manager)
    assert manager.status['error'] == 'authentication_failed'
    assert manager.reader.read.call_count == 1
    assert not await manager.async_daily_sync()
    assert manager.reader.read.call_count == 1


async def test_interrupted_background_archive_resumes_after_restart(manager):
    await manager.setup()
    manager.reader.read = Mock(side_effect=provider({DAY: [trip(DAY, 1)]}))
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set();await asyncio.Event().wait()
    manager.fetch_day = blocked
    assert await manager.async_daily_sync()
    await entered.wait()
    await manager.stop()
    assert manager.store.state('cloud_auto_sync')['state'] == 'interrupted'
    restored = CloudTripManager(manager.hass, manager.entry, manager.coordinator)
    restored.now = lambda: NOW
    restored.reader.read = Mock(side_effect=provider({DAY: [trip(DAY, 1)]}))
    try:
        await restored.setup();await run_daily(restored)
        assert restored.store.state('cloud_auto_sync')['state'] == 'complete'
        assert restored.store.state('cloud_auto_sync')['resumes'] == 1
        assert restored.store.stats(VEHICLE)['trips'] == 1
    finally:
        await restored.stop()


async def test_expired_remote_day_preserves_totals_route_and_local_calendar(manager):
    await manager.setup()
    manager.reader.read = Mock(side_effect=provider({DAY: [trip(DAY, 1)]}))
    saved = await manager.fetch_day(DAY, VEHICLE)
    saved['trips'][0]['start_address'] = 'SYNTHETIC-ADDRESS'
    manager.store.save_day(VEHICLE, saved)
    manager.reader.read = Mock(side_effect=provider({}))
    expired = await manager.fetch_day(DAY, VEHICLE)
    manager.store.save_day(VEHICLE, expired)
    summary = manager.store.days(VEHICLE, DAY, DAY)[0]
    assert summary['distance_km'] == saved['distance_km']
    assert summary['remote_status'] == 'no_trips'
    retained = manager.store.cloud_payloads(VEHICLE, DAY)['synthetic-1']
    assert retained['start_address'] == 'SYNTHETIC-ADDRESS' and len(retained['route_points']) == 3
    assert manager.store.calendar(VEHICLE) == {'available_dates': [DAY], 'source': 'local_archive'}


async def test_missing_remote_route_does_not_overwrite_retained_route(manager):
    await manager.setup()
    manager.reader.read = Mock(side_effect=provider({DAY: [trip(DAY, 1)]}))
    saved = await manager.fetch_day(DAY, VEHICLE)
    saved['trips'][0]['start_address'] = 'SYNTHETIC-ADDRESS'
    manager.store.save_day(VEHICLE, saved)
    missing = dict(saved, trips=[dict(saved['trips'][0], route_points=[], start_coordinates=None, start_address=None)])
    manager.store.save_day(VEHICLE, missing)
    retained = manager.store.cloud_payloads(VEHICLE, DAY)['synthetic-1']
    assert retained['route_points'] == saved['trips'][0]['route_points']
    assert retained['start_address'] == 'SYNTHETIC-ADDRESS'


async def test_read_refreshes_due_auth_before_sending_get(manager):
    manager.reader.read = Mock(side_effect=[TripError('authentication_refresh_required'), {'tripStatus': 2}])
    with patch.object(manager.coordinator.client, 'update') as update:
        result = await manager.read('summary', {'tripDate': DAY}, VEHICLE)
        update.assert_called_once()
    assert result['tripStatus'] == 2 and manager.reader.read.call_count == 2
    manager.reader.read = Mock(side_effect=TripError('network_error'))
    with pytest.raises(TripError, match='network_error'):
        await manager.read('summary', {'tripDate': DAY}, VEHICLE)
    assert manager.reader.read.call_count == 1


async def test_daily_job_waits_until_manual_sync_has_finished(manager):
    await manager.setup()
    waiting = asyncio.Event();release = asyncio.Event()
    delegate = provider({DAY: [trip(DAY, 1)]})
    first = True
    async def read(operation, query, vehicle):
        nonlocal first
        if first:
            first = False;waiting.set();await release.wait()
        return delegate(operation, query, vehicle)
    manager.read = read
    manager.start(start_date=DAY, end_date=DAY)
    manual = manager.task
    await waiting.wait()
    assert not await manager.async_daily_sync()
    assert not manager.store.state('cloud_auto_sync')
    release.set();await manual
    await manager.hass.async_block_till_done(wait_background_tasks=True)
    assert manager.store.state('cloud_auto_sync')['last_success_date'] == '2026-11-19'
    assert manager.status['automatic'] and manager.status['downloaded_details'] == 0
