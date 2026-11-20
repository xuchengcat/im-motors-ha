"""Cumulative mileage mapping, invalid samples and normal query lifecycle."""
from unittest.mock import patch, Mock

import pytest
from homeassistant.helpers import entity_registry as er

from conftest import VIN, response
from custom_components.im_motors.telemetry import normalized, SENSORS
from test_telemetry import snapshot
from test_ha import create_entry


@pytest.mark.parametrize('value,expected', [(0,0),(12345,12345),(12346,12346),
    (None,None),(-1,None),(0x7FFFFFFF,None),(0xFFFFFFFF,None)])
def test_odometer_integer_km_and_invalid_samples(value,expected):
    data=snapshot({'period':{'vehOdo':value,'originalBmsPackSOCDsp':50}})
    assert normalized(data,SENSORS['odometer'])==expected
    assert normalized(data,SENSORS['soc'])==50
    assert normalized(snapshot(),SENSORS['odometer']) is None


async def test_odometer_entity_uses_shared_poll_and_manual_query(hass,account):
    def reading(value):
        return response({'category':{'vin':VIN,'updateTime':account['now'],
            'period':{'vehOdo':value,'originalBmsPackSOCDsp':50}}})
    account['transport'].send.side_effect=[response([{'vin':VIN}]),reading(12345)]
    entry=await create_entry(hass,account,options={"account_type":"primary"})
    h5 = Mock(return_value={'vin':VIN,'totalMileage':12345})
    with patch('custom_components.im_motors.pyim_china.AccountClient',return_value=account['client']), \
            patch('custom_components.im_motors.primary.trip_api.TripReader.read',h5):
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
    entry.runtime_data.odometer_reader.reader.read = h5
    registry=er.async_get(hass)
    entity=next(e.entity_id for e in registry.entities.values() if e.unique_id.endswith('_odometer'))
    soc_entity=next(e.entity_id for e in registry.entities.values() if e.unique_id.endswith('_soc'))
    state=hass.states.get(entity)
    assert state.state=='12345'
    assert state.attributes['unit_of_measurement']=='km'
    assert state.attributes['device_class']=='distance'
    assert state.attributes['state_class']=='total'
    assert state.attributes.get('last_reset') is None
    assert state.attributes['source_field']=='drive/daily/new.totalMileage'
    assert VIN not in str(state.as_dict())
    await entry.runtime_data.async_refresh()
    assert account['transport'].send.call_count==2
    assert h5.call_count==1
    for value,expected in [(12346,'12346'),(None,'unknown'),(-1,'unknown'),(12346,'12346')]:
        account['transport'].send.side_effect=[reading(value)]
        h5.return_value={'vin':VIN,'totalMileage':value}
        await entry.runtime_data.async_query_vehicle()
        await hass.async_block_till_done()
        assert hass.states.get(entity).state==expected
        assert hass.states.get(soc_entity).state=="50"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
