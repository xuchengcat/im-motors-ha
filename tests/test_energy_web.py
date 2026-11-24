"""Real HA lifecycle and websocket permissions, capability gating, no extra HTTP."""
from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json

import pytest
from homeassistant.helpers import entity_registry as er
from conftest import VIN, response
from test_ha import create_entry
from custom_components.im_motors.energy.web import ws_entries, ws_query, ws_save, ws_hide, ws_geocode
from custom_components.im_motors.capabilities import hybrid, requires_hybrid
from custom_components.im_motors.pyim_china.offline_read_response import parse_read_response
from custom_components.im_motors.telemetry import SENSORS


async def loaded(hass, account, is_hybrid=False, options=None):
    features = [{'featureKey':'HYBRID_ELECTRIC'}] if is_hybrid else []
    account['transport'].send.side_effect = [response([{'vin':VIN}]), response({
        'category':{'vin':VIN,'updateTime':account['now'], 'period':{'fuelLvlPcnt':45,'originalBmsPackSOCDsp':30},
                    'hvBattery':{'bmsChargeStatus':1}}, 'vehicleFunction':{'features':features}})]
    entry = await create_entry(hass, account, options=options or {'charge_history_enabled':True})
    with patch('custom_components.im_motors.pyim_china.AccountClient',return_value=account['client']):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry, entry.runtime_data.data.vehicles[0].identifier


async def call(hass, fn, payload, admin=True):
    conn = Mock(user=SimpleNamespace(is_admin=admin))
    fn(hass, conn, {'id':1, **payload})
    await hass.async_block_till_done(wait_background_tasks=True)
    return conn


async def test_local_crud_and_queries_do_not_request_vehicle(hass, account):
    entry,vehicle = await loaded(hass,account)
    count=account['transport'].send.call_count
    args={'entry_id':entry.entry_id,'vehicle_id':vehicle}
    c=await call(hass,ws_save,{'type':'im_motors/energy/save',**args,'kind':'charge','values':{'date':'2026-11-24','energy_kwh':20,'note':'账单'}})
    r=c.send_result.call_args.args[1]
    assert r['manual']['energy_kwh']==20
    c=await call(hass,ws_query,{'type':'im_motors/energy/query',**args,'start_date':'2026-11-01','end_date':'2026-11-30'})
    result=c.send_result.call_args.args[1]
    assert result['stats']['entered_energy_kwh']==20
    assert not result['hybrid'] and 'refuels' not in result['stats']
    c=await call(hass,ws_save,{'type':'im_motors/energy/save',**args,'kind':'refuel','values':{'date':'2026-11-24','litres':20}})
    assert c.send_error.call_args.args[1]=='hybrid_required'
    assert account['transport'].send.call_count==count


@pytest.mark.parametrize('fn,payload', [
    (ws_entries,{'type':'im_motors/energy/entries'}),
    (ws_query,{'type':'im_motors/energy/query','entry_id':'e','vehicle_id':'v','start_date':'2026-11-24','end_date':'2026-11-24'}),
    (ws_save,{'type':'im_motors/energy/save','entry_id':'e','vehicle_id':'v','kind':'charge','values':{'date':'2026-11-24'}}),
    (ws_hide,{'type':'im_motors/energy/hide','entry_id':'e','vehicle_id':'v','record_id':'r','revision':1,'hidden':True}),
    (ws_geocode,{'type':'im_motors/energy/geocode','entry_id':'e','vehicle_id':'v','record_id':'r'}),
])
async def test_all_energy_commands_reject_non_admin(hass,fn,payload):
    c=await call(hass,fn,payload,False)
    c.send_result.assert_not_called()
    c.async_handle_exception.assert_called_once()


async def test_pure_electric_omits_all_fuel_entities_and_settings(hass,account):
    entry,vehicle=await loaded(hass,account)
    registry=er.async_get(hass)
    uids={e.unique_id for e in er.async_entries_for_config_entry(registry,entry.entry_id)}
    for key,spec in SENSORS.items():
        if requires_hybrid(key,spec):
            assert f'{entry.entry_id}_{vehicle}_{key}' not in uids
    flow=await hass.config_entries.options.async_init(entry.entry_id)
    assert 'charge_history_enabled' in flow['data_schema'].schema
    assert 'refuel_detection_enabled' not in flow['data_schema'].schema
    assert hybrid(entry.runtime_data,vehicle) is False


async def test_hybrid_creates_fuel_sensor_and_capability_survives_missing_list(hass,account):
    entry,vehicle=await loaded(hass,account,True)
    registry=er.async_get(hass)
    entity_id=registry.async_get_entity_id('sensor','im_motors',f'{entry.entry_id}_{vehicle}_fuel_level')
    assert hass.states.get(entity_id).state=='45'
    missing=parse_read_response('vehicle_tab',json.dumps({'resultCode':'200','data':{'category':{'vin':VIN}}}))
    snapshot=replace(entry.runtime_data.data,telemetry={vehicle:missing})
    await entry.runtime_data.energy.observe(snapshot)
    entry.runtime_data.async_set_updated_data(snapshot)
    assert hybrid(entry.runtime_data,vehicle) is True
    assert entry.runtime_data.energy.store.capabilities()[vehicle] is True
    flow=await hass.config_entries.options.async_init(entry.entry_id)
    assert 'refuel_detection_enabled' in flow['data_schema'].schema


async def test_primary_account_records_independently_of_trip_switch(hass,account):
    entry,vehicle=await loaded(hass,account,options={'account_type':'primary','charge_history_enabled':True,'cloud_trip_history_enabled':False})
    assert not entry.runtime_data.trips.enabled
    records=entry.runtime_data.energy.store.query(vehicle,'2020-01-01','2030-01-01')
    assert len(records)==1 and records[0]['status']=='charging'


async def test_foreign_vehicle_rejected(hass,account):
    entry,vehicle=await loaded(hass,account)
    c=await call(hass,ws_query,{'type':'im_motors/energy/query','entry_id':entry.entry_id,'vehicle_id':'foreign','start_date':'2026-11-24','end_date':'2026-11-24'})
    assert c.send_error.call_args.args[1]=='vehicle_unavailable'


async def test_legacy_entities_hidden_without_overriding_user_disable(hass,account):
    entry,vehicle=await loaded(hass,account)
    registry=er.async_get(hass)
    old=registry.async_get_or_create('sensor','im_motors',f'{entry.entry_id}_{vehicle}_fuel_level',config_entry=entry,suggested_object_id='old_fuel')
    user=registry.async_get_or_create('sensor','im_motors',f'{entry.entry_id}_{vehicle}_fuel_range',config_entry=entry,suggested_object_id='user_fuel',disabled_by=er.RegistryEntryDisabler.USER)
    with patch('custom_components.im_motors.pyim_china.AccountClient',return_value=account['client']):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get(old.entity_id).disabled_by==er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(old.entity_id).hidden_by==er.RegistryEntryHider.INTEGRATION
    assert registry.async_get(user.entity_id).disabled_by==er.RegistryEntryDisabler.USER
    assert registry.async_get(user.entity_id).hidden_by==er.RegistryEntryHider.INTEGRATION
    assert hass.states.get(old.entity_id) is None


async def test_geocoding_reuses_address_cache_and_preserves_other_manual_fields(hass,account):
    entry,vehicle=await loaded(hass,account)
    manager=entry.runtime_data.energy
    r=manager.store.query(vehicle,'2020-01-01','2030-01-01')[0]
    with manager.store.connection() as db:
        r['coordinates']={'latitude':31,'longitude':121,'source':'app_amap'}
        r=manager.store.put(db,vehicle,r)
    r=manager.store.save_manual(vehicle,'charge',{'date':r['date'],'energy_kwh':25,'note':'keep'},identifier=r['id'],revision=r['revision'])
    entry.runtime_data.trips.store.address(r['coordinates'],{'address':'测试地点','poi':'测试'})
    args={'type':'im_motors/energy/geocode','entry_id':entry.entry_id,'vehicle_id':vehicle,'record_id':r['id']}
    with patch('custom_components.im_motors.energy.web.settings',return_value={'amap_api_key':'synthetic'}),patch('custom_components.im_motors.energy.web.AmapGeocoder') as geocoder:
        c=await call(hass,ws_geocode,args)
        c.send_result.assert_called_once()
        geocoder.assert_not_called()
    saved=manager.store.detail(vehicle,r['id'])
    assert saved['manual']=={'date':r['date'],'energy_kwh':25,'note':'keep','address':'测试地点'}
