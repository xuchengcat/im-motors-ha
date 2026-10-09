"""Location validation, coordinate conversion and HA lifecycle without live I/O."""
import json
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er

from conftest import VIN, response
from custom_components.im_motors.location import coordinates, gcj02_to_wgs84, location_status
from custom_components.im_motors.pyim_china.offline_read_response import parse_read_response, Presence
from custom_components.im_motors.diagnostics import async_get_config_entry_diagnostics
from test_ha import create_entry


def snapshot(period):
    return parse_read_response('vehicle_tab', json.dumps({'resultCode':'200','data':{
        'category':{'vin':VIN,'period':period}}}))


@pytest.mark.parametrize('lat,lon', [(39.908823,116.39747),('39.908823','116.397470')])
def test_coordinate_pair_and_order(lat,lon):
    s=snapshot({'latitude':lat,'longitude':lon,'coOdntSysFmt':1})
    result=coordinates(s)
    assert location_status(s)=='available'
    # Synthetic reference point; inverse GCJ formula gives this WGS pair.
    assert result==pytest.approx((39.907422,116.391229),abs=0.000003)
    assert '39.908823' not in repr(s)
    assert '116.39747' not in repr(s.location_fields)


@pytest.mark.parametrize('period,status', [
 ({},'missing'),({'latitude':None,'longitude':120,'coOdntSysFmt':1},'missing'),
 ({'latitude':31,'longitude':None,'coOdntSysFmt':1},'missing'),
 ({'latitude':31,'longitude':120},'missing'),
 *[({'latitude':value,'longitude':120,'coOdntSysFmt':1},'invalid')
   for value in [0,'0',91,-91,True,False,'NaN','inf',' 31.0','31a',[],{},10**400]],
 *[({'latitude':31,'longitude':value,'coOdntSysFmt':1},'invalid')
   for value in [0,181,-181,True,'bad']],
 *[({'latitude':31,'longitude':120,'coOdntSysFmt':value},'unsupported_coordinate_system')
   for value in [0,2,99,True,'1']],
])
def test_missing_invalid_and_unknown_formats_do_not_break_telemetry(period,status):
    period=dict(period,originalBmsPackSOCDsp=50)
    s=snapshot(period)
    assert s.period['originalBmsPackSOCDsp'].value==50
    assert location_status(s)==status
    assert coordinates(s) is None


def test_presence_and_outside_china():
    s=snapshot({'latitude':None})
    assert s.location_fields['latitude'].presence is Presence.NULL
    assert s.location_fields['longitude'].presence is Presence.MISSING
    assert gcj02_to_wgs84(51.5,-0.1)==(51.5,-0.1)


async def test_tracker_uses_existing_query_cache_and_never_leaks_to_diagnostics(hass,account):
    account['transport'].send.side_effect=[response([{'vin':VIN}]),response({'category':{
        'vin':VIN,'updateTime':account['now'],'period':{
        'latitude':'39.908823','longitude':'116.39747','coOdntSysFmt':1}}})]
    entry=await create_entry(hass,account)
    with patch('custom_components.im_motors.pyim_china.AccountClient',return_value=account['client']):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    registry=er.async_get(hass)
    tracker=next(e.entity_id for e in registry.entities.values() if e.domain=='device_tracker')
    state=hass.states.get(tracker)
    assert state.state not in ('unknown','unavailable')
    assert state.attributes['source_type']=='gps'
    assert state.attributes['latitude']==pytest.approx(39.907422,abs=0.000003)
    assert state.attributes['longitude']==pytest.approx(116.391229,abs=0.000003)
    assert state.attributes['individual_sample_time_verified'] is False
    assert VIN not in str(state.as_dict())
    diagnostics=await async_get_config_entry_diagnostics(hass,entry)
    assert diagnostics['vehicles_with_location']==1
    assert '39.90' not in str(diagnostics) and '116.39' not in str(diagnostics)
    await entry.runtime_data.async_refresh()
    assert account['transport'].send.call_count==2
    account['transport'].send.side_effect=[response({'category':{'vin':VIN,'period':{}}})]
    await entry.runtime_data.async_query_vehicle()
    await hass.async_block_till_done()
    assert account['transport'].send.call_count==3
    state=hass.states.get(tracker)
    assert state.state=='unavailable'
    assert 'latitude' not in state.attributes and 'longitude' not in state.attributes
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
