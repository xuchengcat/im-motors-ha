"""Keep power-cycle boundaries and require spatial evidence for endpoints."""
import json
from custom_components.im_motors.trip_recorder import TripRecorder
from custom_components.im_motors.trip_store import TripStore
from test_power_trips import recorder, recorded, observation


def p(minute, powered, position, odo=100):
    point=observation(minute,powered,odo=odo)
    point['coordinates']['latitude']=31+position/111195
    return point


def origin(recorder):
    for minute in [0,10]:recorder.observe('v',p(minute,False,0),10)


def test_first_moving_sample_is_not_origin_and_late_stable_fix_is_destination(recorder):
    origin(recorder)
    recorder.observe('v',p(11,True,1000,101),10)
    recorder.observe('v',p(20,False,2000,110),10)
    trip=recorded(recorder)[0]
    assert trip['start_coordinates']==p(0,False,0)['coordinates']
    assert trip['start_parking_confirmed'] and trip['end_coordinates'] is None
    assert trip['end_location_status']=='awaiting_stable_parking'
    for minute in [30,40]:recorder.observe('v',p(minute,None,5000,110),10)
    trip=recorded(recorder)[0]
    assert trip['end_coordinates']==p(30,None,5000)['coordinates']
    assert trip['end_parking_confirmed'] and trip['parking_confirmation_samples']==2
    assert '08:20:00' in trip['end_time']
    assert trip['end_location_sample_time']==p(30,None,5000)['time']
    assert trip['distance_km']==10 and len(recorded(recorder))==1
    detail=recorder.store.detail('v',trip['trip_id'])
    assert [e['role'] for e in detail['endpoints']]==['start','end']
    assert len(detail['points'])==2  # Parking proof is separate, not fabricated trip telemetry.


def test_brief_off_stop_cannot_borrow_next_cycles_destination(recorder):
    origin(recorder)
    for point in [p(11,True,1000,101),p(20,False,5000,105),p(21,True,6000,106),
                  p(30,False,10000,110),p(40,False,10000,110)]:
        recorder.observe('v',point,10)
    trips=sorted(recorded(recorder),key=lambda t:t['start_time'])
    assert len(trips)==2
    assert trips[0]['end_coordinates'] is None and trips[0]['end_location_status']=='unconfirmed'
    assert trips[1]['start_coordinates'] is None
    assert trips[1]['end_coordinates']==p(30,False,10000)['coordinates']


def test_duplicate_cloud_snapshot_and_gps_outlier_do_not_confirm_endpoint(recorder):
    origin(recorder)
    recorder.observe('v',p(11,True,1000,101),10)
    recorder.observe('v',p(20,False,5000,105),10)
    recorder.observe('v',p(20,False,5000,105),10)
    assert not recorded(recorder)[0]['end_parking_confirmed']
    recorder.observe('v',p(30,False,9000,105),10)
    recorder.observe('v',p(40,False,5000,105),10)
    assert not recorded(recorder)[0]['end_parking_confirmed']
    recorder.observe('v',p(50,False,5000,105),10)
    assert recorded(recorder)[0]['end_parking_confirmed']


def test_stationary_ready_does_not_confirm_an_endpoint(recorder):
    origin(recorder)
    for minute in [11,21,31]:recorder.observe('v',p(minute,True,5000,105),10)
    assert not recorded(recorder)
    recorder.observe('v',p(32,False,5000,105),10)
    assert not recorded(recorder)[0]['end_parking_confirmed']
    recorder.observe('v',p(42,False,5000,105),10)
    assert recorded(recorder)[0]['end_parking_confirmed']


def test_frozen_gps_after_mileage_increase_does_not_validate_old_origin(recorder):
    origin(recorder)
    for minute,power,odo in [(11,True,101),(20,False,110),(30,False,110)]:
        recorder.observe('v',p(minute,power,0,odo),10)
    assert recorded(recorder)[0]['end_coordinates'] is None
    assert recorder.store.state('recorder:v')['parking_anchor'] is None


def test_unobserved_mileage_change_invalidates_old_origin_and_pending_endpoint(recorder):
    origin(recorder)
    recorder.observe('v',p(11,None,5000,105),10)
    recorder.observe('v',p(12,True,6000,106),10)
    recorder.observe('v',p(20,False,9000,110),10)
    recorder.observe('v',p(30,None,10000,120),10)
    recorder.observe('v',p(40,None,10000,120),10)
    trip=recorded(recorder)[0]
    assert trip['start_coordinates'] is None and trip['end_coordinates'] is None


def test_restart_keeps_pending_endpoint_and_stale_geocode_cannot_overwrite_it(recorder):
    origin(recorder)
    recorder.observe('v',p(11,True,1000,101),10)
    recorder.observe('v',p(20,False,5000,105),10)
    old=recorded(recorder)[0]
    recorder=TripRecorder(TripStore(recorder.store.path),'Asia/Shanghai')
    state=recorder.observe('v',p(30,False,5000,105),10)
    assert state['endpoint_revision']==1
    old['end_address']='stale geocoding result'
    recorder.store.update_trip('v',old)
    trip=recorded(recorder)[0]
    assert trip['end_parking_confirmed'] and trip['end_address'] is None


def test_rebuild_replays_endpoint_corrections_and_preserves_cycle_identity(recorder):
    origin(recorder)
    for point in [p(11,True,1000,101),p(20,False,5000,105),p(30,False,5000,105)]:
        recorder.observe('v',point,10)
    original=recorded(recorder)[0]
    state=recorder.store.state('recorder:v');state['algorithm_version']=3
    recorder.store.state('recorder:v',state)
    recorder.rebuild_if_needed('v',10)
    restored=recorded(recorder)[0]
    assert restored['trip_id']==original['trip_id']
    assert restored['start_coordinates']==original['start_coordinates']
    assert restored['end_coordinates']==original['end_coordinates']
    assert restored['start_parking_confirmed'] and restored['end_parking_confirmed']
    with recorder.store.connection() as db:
        assert db.execute("SELECT count(*) FROM trip_revisions WHERE kind='trip'").fetchone()[0]==1
