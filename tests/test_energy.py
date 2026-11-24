"""Synthetic charging transitions, persistence, field parsing and local edits."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import json

import pytest

from custom_components.im_motors.energy.charge_recorder import sample
from custom_components.im_motors.energy.manager import EnergyManager
from custom_components.im_motors.energy.store import EnergyStore
from custom_components.im_motors.energy.web import validate_manual
from custom_components.im_motors.pyim_china.offline_read_response import parse_read_response
from custom_components.im_motors.telemetry import normalized, SENSORS
from custom_components.im_motors.trip_api import TripError

BASE = datetime(2026, 11, 24, 15, 50, tzinfo=timezone.utc)


def point(minutes, code=1, soc=30, **extra):
    stamp = (BASE + timedelta(minutes=minutes)).isoformat()
    return dict(time=stamp, received_at=stamp, charge_state=code, soc=soc,
                fast_plug=None, slow_plug=None, speed=0, coordinates=None, **extra)


@pytest.fixture
def manager(tmp_path):
    async def execute(fn, *args):
        return fn(*args)
    c = SimpleNamespace(client=SimpleNamespace(data_dir=tmp_path), telemetry_interval_minutes=10, hybrid_capabilities={})
    entry = SimpleNamespace(options={'charge_history_enabled': True})
    hass = SimpleNamespace(config=SimpleNamespace(time_zone='Asia/Shanghai'), async_add_executor_job=execute)
    m = EnergyManager(hass, entry, c)
    m.store.initialize()
    return m


def push(m, p, fuel=False):
    m._observe('v', p, 'fresh', fuel)


def rows(m):
    return m.store.query('v', '2026-11-01', '2026-12-01')


def test_complete_session_bounds_cross_midnight_and_energy_unknown(manager):
    push(manager, point(0, 0))
    push(manager, point(10, 1, 35))
    push(manager, point(20, 1, 45))
    push(manager, point(30, 2, 50))
    assert rows(manager)[0]['status'] == 'paused'
    push(manager, point(40, 2, 50))
    r = rows(manager)[0]
    assert len(rows(manager)) == 1
    assert r['date'] == '2026-11-25'
    assert r['status'] == 'complete'
    assert r['start_bounds'] == {'after': point(0)['time'], 'by': point(10)['time']}
    assert r['end_bounds'] == {'after': point(20)['time'], 'by': point(30)['time']}
    assert (r['soc_start'], r['soc_end'], r['soc_gain_points']) == (35, 50, 15)
    assert r['energy_kwh'] is None
    assert not r['partial_start'] and not r['partial_end']


def test_pause_resume_keeps_one_record_and_fresh_confirmation(manager):
    push(manager, point(0))
    push(manager, point(10, 14, 40))
    push(manager, point(10, 14, 40))  # repeated cache is not confirmation
    assert rows(manager)[0]['status'] == 'paused'
    push(manager, point(20, 1, 45))
    assert len(rows(manager)) == 1
    assert rows(manager)[0]['pause_count'] == 1
    push(manager, point(30, 2, 50))
    push(manager, point(40, 2, 50))
    assert rows(manager)[0]['status'] == 'complete'


@pytest.mark.parametrize('code', [0, 5, 6, 7, 9, 11, 13, 15])
def test_waiting_reserved_and_discharging_never_start_charge(manager, code):
    push(manager, point(0, code))
    push(manager, point(10, code, 40))
    assert rows(manager) == []


def test_restart_recovers_active_and_preserves_manual_fields(manager):
    push(manager, point(0))
    r = rows(manager)[0]
    manager.store.save_manual('v', 'charge', {'date': r['date'], 'energy_kwh': 42.8, 'note': '账单'}, identifier=r['id'], revision=r['revision'])
    restarted = EnergyManager(manager.hass, manager.entry, manager.coordinator)
    push(restarted, point(10, 1, 50))
    push(restarted, point(20, 2, 60, driving_state='driving'))
    r = rows(restarted)[0]
    assert r['status'] == 'complete' and r['manual']['energy_kwh'] == 42.8
    assert r['soc_end'] == 50  # driving-time SOC is not the charging endpoint


def test_gap_splits_and_marks_both_missing_boundaries(manager):
    push(manager, point(0))
    push(manager, point(90, 1, 70))
    values = rows(manager)
    assert len(values) == 2
    old = next(r for r in values if r['status'] == 'incomplete')
    current = next(r for r in values if r['status'] == 'charging')
    assert old['partial_end'] and old['end_bounds']['by'] is None
    assert current['partial_start']


def test_unknown_states_and_missing_snapshots_do_not_confirm_end(manager):
    push(manager, point(0))
    manager._observe('v', None, 'stale_cloud_snapshot', False)
    push(manager, point(10, None))
    push(manager, point(20, None))
    assert rows(manager)[0]['status'] == 'charging'
    push(manager, point(40, None))
    assert rows(manager)[0]['status'] == 'incomplete'


def test_duplicate_and_out_of_order_are_idempotent(manager):
    push(manager, point(0))
    push(manager, point(10))
    push(manager, point(10))
    push(manager, point(0))
    assert rows(manager)[0]['charging_sample_count'] == 2
    with manager.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 2


def test_disable_retains_incomplete_and_never_merges_on_reenable(manager):
    push(manager, point(0))
    manager.entry.options['charge_history_enabled'] = False
    push(manager, point(5))
    assert rows(manager)[0]['status'] == 'incomplete'
    manager.entry.options['charge_history_enabled'] = True
    push(manager, point(10))
    assert len(rows(manager)) == 2


def test_refuel_needs_second_fresh_confirmation_and_remains_candidate(manager):
    push(manager, point(0, 0, fuel=20), True)
    push(manager, point(10, 0, fuel=75), True)
    push(manager, point(10, 0, fuel=75), True)
    assert rows(manager) == []
    push(manager, point(20, 0, fuel=75), True)
    r = rows(manager)[0]
    assert r['kind'] == 'refuel' and r['status'] == 'candidate' and r['litres'] is None
    push(manager, point(30, 0, fuel=75), True)
    assert len(rows(manager)) == 1


def test_fuel_disabled_gap_and_driving_do_not_create_candidates(manager):
    push(manager, point(0, 0, fuel=20))
    push(manager, point(10, 0, fuel=80))
    push(manager, point(20, 0, fuel=80))
    assert rows(manager) == []
    push(manager, point(100, 0, fuel=20), True)
    push(manager, point(110, 0, fuel=80, driving_state='driving'), True)
    push(manager, point(120, 0, fuel=80), True)
    assert rows(manager) == []


def test_archive_transaction_rolls_back_partial_observation(manager, monkeypatch):
    push(manager, point(0))
    old = rows(manager)[0]
    monkeypatch.setattr(manager.store, 'put', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        push(manager, point(10))
    assert rows(manager)[0] == old
    assert manager.store.status('v')['previous']['time'] == point(0)['time']
    with manager.store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM samples').fetchone()[0] == 1


def test_optimistic_edit_and_soft_hide_preserve_observation(manager):
    push(manager, point(0))
    r = rows(manager)[0]
    saved = manager.store.save_manual('v', 'charge', {'date': r['date'], 'energy_kwh': 0}, identifier=r['id'], revision=r['revision'])
    assert saved['soc_start'] == r['soc_start'] and saved['manual']['energy_kwh'] == 0
    with pytest.raises(TripError, match='record_changed'):
        manager.store.save_manual('v', 'charge', {'date': r['date']}, identifier=r['id'], revision=r['revision'])
    manager.store.set_hidden('v', r['id'], saved['revision'], True)
    push(manager, point(10))
    assert rows(manager)[0]['hidden'] and rows(manager)[0]['manual']['energy_kwh'] == 0
    assert manager.store.query('other', '2026-11-01', '2026-12-01') == []
    assert manager.store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('values', [
    {'date': 'bad'}, {'date': '2026-11-24', 'energy_kwh': float('nan')},
    {'date': '2026-11-24', 'soc_start': 101}, {'date': '2026-11-24', 'energy_kwh': True},
    {'date': '2026-11-24', 'address': 'a'*257}, {'date': '2026-11-24', 'litres': 10},
    {'date': '2026-11-24', 'start_time': '2026-11-24T10:00:00'},
    {'date': '2026-11-24', 'start_time': '2026-11-24T10:00:00+08:00', 'end_time': '2026-11-24T09:00:00+08:00'}])
def test_invalid_manual_fields_rejected(values):
    with pytest.raises(TripError):
        validate_manual('charge', values)


def test_parse_fuel_level_end_to_end_and_snapshot_freshness():
    stamp = int(BASE.timestamp()*1000)
    snapshot = parse_read_response('vehicle_tab', json.dumps({'resultCode':'200','data':{
        'category':{'updateTime':stamp,'period':{'fuelLvlPcnt':0,'originalBmsPackSOCDsp':55},'hvBattery':{'bmsChargeStatus':1}},
        'vehicleFunction':{'features':[{'featureKey':'HYBRID_ELECTRIC'}]}}}))
    assert normalized(snapshot, SENSORS['fuel_level']) == 0
    assert sample(snapshot, BASE, 10, hybrid=True)[0]['fuel'] == 0
    assert 'fuel' not in sample(snapshot, BASE, 10)[0]
    assert sample(snapshot, BASE+timedelta(hours=1), 10)[1] == 'stale_cloud_snapshot'
    assert sample(snapshot, BASE-timedelta(minutes=6), 10)[1] == 'future_source_time'


def test_enabling_charge_during_existing_fuel_only_sampling_marks_partial_start(manager):
    manager.entry.options['charge_history_enabled']=False
    push(manager,point(0,1,fuel=50),True)
    manager.entry.options['charge_history_enabled']=True
    push(manager,point(10,1,fuel=50),True)
    assert rows(manager)[0]['partial_start']


def test_future_archive_schema_not_downgraded(manager):
    with manager.store.connection() as db:
        db.execute('PRAGMA user_version=2')
    with pytest.raises(TripError,match='unsupported_archive_version'):
        manager.store.initialize()
    with manager.store.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2
