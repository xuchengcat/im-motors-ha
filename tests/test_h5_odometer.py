"""Today's H5 cumulative mileage, durable poll limits and isolated failures."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import pytest

from conftest import VIN
from custom_components.im_motors.odometer import OdometerReader, mileage
from custom_components.im_motors.pyim_china.credential_store import key_context,vault_for_path
from custom_components.im_motors.trip_api import TripError


@pytest.mark.parametrize('value',[0,38492,38492.0,38492.7])
def test_mileage_accepts_numeric_km_without_scaling(value):
    assert mileage({'vin':VIN,'totalMileage':value},VIN)==value


@pytest.mark.parametrize('value',[None,True,'38492',-1,float('inf'),float('nan'),0x7FFFFFFF])
def test_invalid_values_are_unknown(value):
    with pytest.raises(TripError):mileage({'vin':VIN,'totalMileage':value},VIN)


def test_missing_or_foreign_vehicle_is_rejected():
    for data in ({'totalMileage':4},{'vin':'other','totalMileage':4},{'vin':VIN}):
        with pytest.raises(TripError):mileage(data,VIN)


@pytest.fixture
def meter(account):
    c=account['client'];identifier='synthetic-vehicle'
    with key_context(c.key_file):
        vault_for_path(c.data_dir/'ha-metadata.imvault')._save_payload({'vins':{identifier:VIN}})
    snapshot=SimpleNamespace(vehicles=[SimpleNamespace(identifier=identifier)])
    api=Mock();api.read.return_value={'vin':VIN,'totalMileage':100.5}
    return OdometerReader(c,reader=api),api,snapshot,identifier


def test_cached_across_polls_restarts_and_force(meter,account):
    reader,api,snapshot,v=meter
    assert reader.update(snapshot)[v]['value']==100.5
    assert reader.update(snapshot)[v]['value']==100.5
    assert api.read.call_count==1
    replacement=OdometerReader(account['client'],reader=api)
    assert replacement.update(snapshot)[v]['value']==100.5
    assert api.read.call_count==1
    api.read.return_value={'vin':VIN,'totalMileage':101.7}
    assert replacement.update(snapshot,force=True)[v]['value']==101.7
    assert api.read.call_count==2
    account['clock'].return_value+=account['client'].telemetry_interval_ms
    replacement.update(snapshot)
    assert api.read.call_count==3


def test_shanghai_day_rollover_queries_today_only(meter,account):
    reader,api,snapshot,v=meter
    now=datetime(2026,11,20,23,59,tzinfo=ZoneInfo('Asia/Shanghai'))
    account['clock'].return_value=int(now.timestamp()*1000)
    reader.update(snapshot)
    assert api.read.call_args.args==('summary',{'tripDate':'2026-11-20'},v)
    account['clock'].return_value=int((now+timedelta(minutes=2)).timestamp()*1000)
    reader.update(snapshot)
    assert api.read.call_args.args==('summary',{'tripDate':'2026-11-21'},v)
    assert api.read.call_count==2


@pytest.mark.parametrize('code',['not_vehicle_owner','authentication_failed','protocol_rejected','network_error'])
def test_error_does_not_republish_last_value_or_repeat_each_minute(meter,code):
    reader,api,snapshot,v=meter
    reader.update(snapshot)
    api.read.side_effect=TripError(code)
    result=reader.update(snapshot,force=True)[v]
    assert result['value'] is None and result['error']==code
    assert reader.update(snapshot)[v]['value'] is None
    assert api.read.call_count==2


def test_cumulative_decrease_does_not_create_false_reset(meter):
    reader,api,snapshot,v=meter
    reader.update(snapshot)
    api.read.return_value={'vin':VIN,'totalMileage':99}
    result=reader.update(snapshot,force=True)[v]
    assert result['value'] is None and result['error']=='odometer_decreased'
    api.read.return_value={'vin':VIN,'totalMileage':101}
    assert reader.update(snapshot,force=True)[v]['value']==101


def test_multi_vehicle_no_ambiguous_request(meter,account):
    reader,api,snapshot,v=meter
    with key_context(account['key']):
        vault_for_path(account['data']/'ha-metadata.imvault')._save_payload({'vins':{v:VIN,'second':'other'}})
    assert reader.update(snapshot)[v]['error']=='multiple_vehicles'
    api.read.assert_not_called()
