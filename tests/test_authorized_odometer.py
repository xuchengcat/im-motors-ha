"""Authorized diagnostic mileage and role-specific code/cache isolation."""
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from homeassistant.helpers import entity_registry as er

from conftest import VIN, response
from test_ha import create_entry
from custom_components.im_motors.odometer import OdometerReader
from custom_components.im_motors.authorized.health_api import HealthReader, PATH, status_post
from custom_components.im_motors.pyim_china.credential_store import key_context,vault_for_path
from custom_components.im_motors.trip_api import TripError

VEHICLE=sha256(VIN.encode()).hexdigest()


@pytest.fixture
def metadata(account):
    with key_context(account['key']):
        vault_for_path(account['data']/'ha-metadata.imvault')._save_payload({'vins':{VEHICLE:VIN}})
    return SimpleNamespace(vehicles=[SimpleNamespace(identifier=VEHICLE)])


def test_health_read_uses_only_scoped_status_post(account,metadata):
    post=Mock(return_value=(json.dumps({'resultCode':'200','data':{'mileage':40000,'updateTime':'2026-11-20 08:08'}}),{}))
    reader=HealthReader(account['client'],post=post)
    result=reader.read(VEHICLE)
    assert result=={'mileage':40000,'requested_vin':VIN,'source_updated':'2026-11-20T08:08:00+08:00'}
    path,headers,body=post.call_args.args
    assert path==PATH and json.loads(body)=={'vin':VIN}
    assert headers['x-original-path']=='https://m.immotors.com'+PATH
    assert headers['x-app-key']=='android-h5'
    assert 'deviceId' not in headers
    with pytest.raises(TripError):status_post('/app/planet/api/v1/maintenance/health/report/generate',{},'{}')


def test_unknown_vehicle_or_mismatching_response_is_rejected(account,metadata):
    post=Mock(return_value=(json.dumps({'resultCode':'200','data':{'mileage':40000,'vin':'OTHER'}}),{}))
    reader=HealthReader(account['client'],post=post)
    with pytest.raises(TripError):reader.read('unassociated')
    post.assert_not_called()
    with pytest.raises(TripError):reader.read(VEHICLE)


def test_authorized_never_calls_primary_or_requires_its_high_water_mark(account,metadata):
    primary=Mock();primary.read.return_value={'vin':VIN,'totalMileage':40050}
    p=OdometerReader(account['client'],account_kind='primary',reader=primary)
    assert p.update(metadata)[VEHICLE]['value']==40050
    health=Mock();health.read.return_value={'requested_vin':VIN,'mileage':40000,'source_updated':'2026-11-20T08:08:00+08:00'}
    a=OdometerReader(account['client'],account_kind='authorized',reader=health)
    with patch('custom_components.im_motors.primary.trip_api.TripReader.read',side_effect=AssertionError('Primary API called')):
        result=a.update(metadata)[VEHICLE]
        assert result['value']==40000 and result['source_field']=='maintenance/status.mileage'
        assert result['trip_date'] is None
        assert a.update(metadata)[VEHICLE]['value']==40000
    assert health.read.call_count==1 and health.read.call_args.args==(VEHICLE,)
    # Switching back reuses the primary cache, not the older health reading.
    assert p.update(metadata)[VEHICLE]['value']==40050
    assert primary.read.call_count==1


def test_migrate_legacy_primary_cache_without_query(account,metadata):
    with key_context(account['key']):
        vault_for_path(account['data']/'odometer.imvault')._save_payload({'kind':'h5-odometer','records':{VEHICLE:{
            'source_field':'drive/daily/new.totalMileage','value':100,'last_confirmed':100,
            'trip_date':__import__('datetime').datetime.fromtimestamp(account['now']/1000,__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date().isoformat(),
            'attempted_at':account['now'],'status':'ok'}}})
    api=Mock()
    result=OdometerReader(account['client'],account_kind='primary',reader=api).update(metadata)
    assert result[VEHICLE]['value']==100
    api.read.assert_not_called()


@pytest.mark.parametrize('value',[None,-1,True,'40000',float('nan')])
def test_health_invalid_values_do_not_use_primary_fallback(account,metadata,value):
    api=Mock();api.read.return_value={'requested_vin':VIN,'mileage':value}
    result=OdometerReader(account['client'],account_kind='authorized',reader=api).update(metadata)[VEHICLE]
    assert result['value'] is None and result['error']=='invalid_odometer'


async def test_authorized_entity_keeps_id_and_diagnostic_source(hass,account):
    def send(request):
        if '/v6/tab/vehicle?' in request.url:
            return response({'category':{'vin':VIN,'period':{'vehOdo':49999,'originalBmsPackSOCDsp':50}}})
        return response([{'vin':VIN}])
    account['transport'].send.side_effect=send
    api=Mock(return_value={'requested_vin':VIN,'mileage':40000,'source_updated':'2026-11-20T08:08:00+08:00'})
    entry=await create_entry(hass,account)
    with patch('custom_components.im_motors.pyim_china.AccountClient',return_value=account['client']), \
            patch('custom_components.im_motors.authorized.health_api.HealthReader.read',api), \
            patch('custom_components.im_motors.primary.trip_api.TripReader.read',side_effect=AssertionError('Primary route called')):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    sensor=next(e for e in er.async_get(hass).entities.values() if e.unique_id.endswith('_odometer'))
    state=hass.states.get(sensor.entity_id)
    assert state.state=='40000'
    assert state.attributes['source_field']=='maintenance/status.mileage'
    assert state.attributes['source_updated']=='2026-11-20T08:08:00+08:00'
    assert VIN not in str(state.as_dict())
    assert entry.runtime_data.checkin is None
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_authorized_multiple_vehicles_request_separate_vins(account,metadata):
    second_vin='LSY00000000000002'
    second=sha256(second_vin.encode()).hexdigest()
    with key_context(account['key']):
        vault_for_path(account['data']/'ha-metadata.imvault')._save_payload({'vins':{VEHICLE:VIN,second:second_vin}})
    requested=[]
    def post(path,headers,body):
        vin=json.loads(body)['vin'];requested.append(vin)
        return json.dumps({'resultCode':'200','data':{'mileage':100 if vin==VIN else 200}}),{}
    reader=OdometerReader(account['client'],account_kind='authorized')
    reader.reader.post=post
    snapshot=SimpleNamespace(vehicles=[SimpleNamespace(identifier=VEHICLE),SimpleNamespace(identifier=second)])
    result=reader.update(snapshot)
    assert result[VEHICLE]['value']==100 and result[second]['value']==200
    assert requested==[VIN,second_vin]


def test_role_folders_have_no_cross_role_imports():
    import ast
    import importlib.util
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'custom_components/im_motors'
    for role,other in (('primary','authorized'),('authorized','primary')):
        for path in (root/role).glob('*.py'):
            package='custom_components.im_motors.'+role
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.ImportFrom):
                    module=importlib.util.resolve_name('.'*node.level+(node.module or ''),package) if node.level else node.module
                    assert not module.startswith('custom_components.im_motors.'+other),path
