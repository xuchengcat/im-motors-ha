"""Power cycles must split short trips and retain timestamped route samples."""
from datetime import datetime, timedelta, timezone
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from custom_components.im_motors.trip_recorder import TripRecorder
from custom_components.im_motors.trip_store import TripStore
from custom_components.im_motors.trip_api import TripError


def observation(minute, powered, *, odo=100, gps=True, state=None):
    time = (datetime(2026, 10, 23, tzinfo=timezone.utc)+timedelta(minutes=minute)).isoformat()
    return {"time": time, "received_at": time, "powered": powered, "state": state,
            "odometer": odo, "coordinates": {"latitude": 31+minute/10000, "longitude": 121., "source": "app_amap"} if gps else None}


@pytest.fixture
def recorder(tmp_path):
    store=TripStore(tmp_path/'trips.sqlite3');store.initialize()
    return TripRecorder(store, 'Asia/Shanghai')


def recorded(recorder):
    return recorder.store.trips('v','2026-10-01','2026-10-31')


def test_two_cycles_same_day_split_without_parking_wait(recorder):
    for minute,power,odo in [(0,False,100),(1,True,100),(2,True,103),(3,False,105),(4,True,105),(5,False,106)]:
        recorder.observe('v',observation(minute,power,odo=odo),1)
    trips=recorded(recorder)
    assert len(trips)==2
    assert sorted(t['distance_km'] for t in trips)==[1,5]
    assert all(t['detection']=='power_cycle' for t in trips)
    assert recorder.store.days('v','2026-10-23','2026-10-23')[0]['total_count']==2


def test_ready_p_gear_and_long_stationary_do_not_end_power_cycle(recorder):
    for minute in [0,1,2,15,25]:
        state=recorder.observe('v',observation(minute,minute>0,state='ready'),1)
    assert state['active'] and not recorded(recorder)
    recorder.observe('v',observation(26,False),1)
    assert len(recorded(recorder))==1


def test_unknown_power_and_missing_gps_do_not_end_trip(recorder):
    for minute,power in [(0,False),(1,True),(2,None),(3,None)]:
        recorder.observe('v',observation(minute,power,gps=False),1)
    assert not recorded(recorder)
    recorder.observe('v',observation(4,False,gps=False),1)
    trip=recorded(recorder)[0]
    assert trip['sampling_gap'] and trip['end_coordinates'] is None


def test_restart_duplicate_and_odometer_rollback(recorder):
    recorder.observe('v',observation(1,True,odo=120),1)
    recorder=TripRecorder(TripStore(recorder.store.path),'Asia/Shanghai')
    recorder.observe('v',observation(1,False,odo=110),1)
    assert not recorded(recorder)
    recorder.observe('v',observation(2,False,odo=110),1)
    trip=recorded(recorder)[0]
    assert trip['distance_km'] is None and trip['odometer_reset'] and trip['partial_start']


def test_detail_returns_all_samples_in_order_with_gps_gaps_and_vehicle_isolation(recorder):
    for minute,power in [(0,False),(1,True),(2,True),(3,True),(4,False)]:
        recorder.observe('v',observation(minute,power,gps=minute!=2),1)
    trip=recorded(recorder)[0]
    detail=recorder.store.detail('v',trip['trip_id'])
    assert len(detail['points'])==4
    assert detail['points'][0]['time']==observation(1,True)['time']
    assert detail['points'][1]['map_coordinates'] is None
    assert detail['points'][2]['map_coordinates']!=detail['points'][2]['coordinates']
    with pytest.raises(TripError):recorder.store.detail('other',trip['trip_id'])


def test_rebuild_splits_legacy_merged_day_and_archives_previous_rows(recorder):
    samples=[observation(m,p,odo=100+m) for m,p in [(0,False),(1,True),(2,False),(3,True),(4,False)]]
    for p in samples:
        p['state']='driving' if p.pop('powered') else 'parking'
    with recorder.store.connection() as db:
        for p in samples:db.execute('INSERT INTO samples VALUES (?,?,?)',('v',p['time'],json.dumps(p)))
    recorder.rebuild_if_needed('v',1)
    assert len(recorded(recorder))==2
    assert recorder.rebuild_if_needed('v',1) is False


def test_midnight_trip_is_attributed_to_departure_day(recorder):
    for minute,power in [(958,False),(959,True),(961,False)]:
        recorder.observe('v',observation(minute,power),1)
    trip=recorded(recorder)[0]
    assert trip['crosses_date'] and trip['date']=='2026-10-23'


async def test_trip_recording_never_forces_cloud_refresh():
    from custom_components.im_motors.coordinator import ImMotorsCoordinator
    snapshot=SimpleNamespace(telemetry_time_ms=1_000_000)
    client=Mock();client.update.return_value=snapshot
    trips=SimpleNamespace(enabled=True,observe=AsyncMock())
    async def execute(call):return call()
    coordinator=SimpleNamespace(client=client,trips=trips,data=snapshot,
        config_entry=SimpleNamespace(options={'trip_sample_seconds':60}),
        hass=SimpleNamespace(async_add_executor_job=execute))
    for _ in range(2):
        await ImMotorsCoordinator._async_fetch_data(coordinator)
        assert client.update.call_args.kwargs['force_telemetry'] is False
    await ImMotorsCoordinator._async_fetch_data(coordinator,force_telemetry=True)
    assert client.update.call_args.kwargs['force_telemetry'] is True
    assert trips.observe.await_count==3


async def test_disabled_trip_recording_keeps_normal_cloud_cache():
    from custom_components.im_motors.coordinator import ImMotorsCoordinator
    client=Mock()
    async def execute(call):return call()
    coordinator=SimpleNamespace(client=client,trips=SimpleNamespace(enabled=False,observe=AsyncMock()),
        data=None,config_entry=SimpleNamespace(options={}),
        hass=SimpleNamespace(async_add_executor_job=execute))
    await ImMotorsCoordinator._async_fetch_data(coordinator)
    assert client.update.call_args.kwargs['force_telemetry'] is False
