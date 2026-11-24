"""Fuel rises are candidates for human confirmation, never measured litres."""
from datetime import datetime

from .charge_recorder import identifier, seconds, moved

MIN_FUEL_RISE = 10  # percentage points; conservative candidate threshold only


def observe(vehicle, point, state, zone, max_gap):
    previous = state.get('previous')
    candidate = state.pop('fuel_candidate', None)
    if not previous or seconds(previous['time'], point['time']) > min(max_gap, 7200):
        return []
    before, fuel = previous.get('fuel'), point.get('fuel')
    if fuel is None or moved(point):
        return []
    if candidate and fuel >= candidate['fuel_after'] - 2:
        record = dict(candidate, status='candidate', confirmed_observation_at=point['time'])
        return [record]
    if before is not None and fuel - before >= MIN_FUEL_RISE:
        state['fuel_candidate'] = {
            'id': identifier(vehicle, 'refuel', point['time']), 'kind': 'refuel',
            'date': datetime.fromisoformat(point['time']).astimezone(zone).date().isoformat(),
            'source': 'ha_telemetry', 'algorithm_version': 1,
            'first_observed_at': previous['time'], 'last_observed_at': point['time'],
            'fuel_before': before, 'fuel_after': fuel,
            'coordinates': point.get('coordinates'), 'coordinate_time_verified': False,
            'individual_field_times_verified': False, 'litres': None, 'amount': None}
    return []
