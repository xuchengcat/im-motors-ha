"""Charging sessions from retained fresh samples, never from a polling timer."""
from datetime import datetime, timezone
import hashlib

from ..location import location_status
from ..telemetry import ACTIVE_CHARGING, SENSORS, normalized, raw, timestamp

ALGORITHM_VERSION = 1
KNOWN_STATES = frozenset(range(17))


def seconds(first, last):
    return (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()


def sample(snapshot, received_at, interval_minutes, *, hybrid=False):
    source = timestamp(raw(snapshot, 'root', 'updateTime'))
    if source is None:
        return None, 'missing_source_time'
    age = (received_at - source).total_seconds()
    if age < -300:
        return None, 'future_source_time'
    if age > max(900, interval_minutes * 180):
        return None, 'stale_cloud_snapshot'
    position = ({'latitude': snapshot.location_fields['latitude'].value,
                 'longitude': snapshot.location_fields['longitude'].value, 'source': 'app_amap'}
                if location_status(snapshot) == 'available' else None)
    point = {'time': source.isoformat(), 'received_at': received_at.isoformat(),
             'soc': normalized(snapshot, SENSORS['soc']),
             'charge_state': raw(snapshot, 'hvBattery', 'bmsChargeStatus'),
             'fast_plug': raw(snapshot, 'hvBattery', 'bmsFastPlugOn'),
             'slow_plug': raw(snapshot, 'hvBattery', 'bmsSlowPlugOn'),
             'speed': raw(snapshot, 'period', 'vehSpeed'),
             'driving_state': normalized(snapshot, SENSORS['driving_state']),
             'coordinates': position, 'coordinate_time_verified': False,
             'individual_field_times_verified': False}
    if hybrid:
        point['fuel'] = normalized(snapshot, SENSORS['fuel_level'])
    # Do not persist unverified energy counters or infer kWh from SOC.
    return point, 'fresh'


def identifier(vehicle, kind, stamp):
    return hashlib.sha256((vehicle + ':' + kind + ':' + stamp).encode()).hexdigest()[:32]


def moved(point):
    return (point.get('speed') is not None and point['speed'] > 0) or point.get('driving_state') == 'driving'


def unplugged(point):
    return point.get('fast_plug') == 0 and point.get('slow_plug') == 0


class ChargeRecorder:
    @staticmethod
    def start(vehicle, point, previous, zone):
        stamp = point['time']
        return {'id': identifier(vehicle, 'charge', stamp), 'kind': 'charge',
                'date': datetime.fromisoformat(stamp).astimezone(zone).date().isoformat(),
                'source': 'ha_telemetry', 'status': 'charging', 'algorithm_version': ALGORITHM_VERSION,
                'first_observed_at': stamp, 'last_observed_at': stamp,
                'start_bounds': {'after': previous['time'] if previous else None, 'by': stamp},
                'soc_start': point['soc'], 'soc_end': point['soc'],
                'coordinates': point.get('coordinates'),
                'soc_start_source': 'first_charging_sample', 'soc_end_source': 'last_charging_sample',
                'partial_start': previous is None, 'partial_end': False,
                'energy_kwh': None, 'energy_source': 'unavailable',
                'sample_count': 1, 'charging_sample_count': 1, 'pause_count': 0,
                'individual_field_times_verified': False, 'coordinate_time_verified': False,
                'charge_state_first': point['charge_state'],
                **({'fuel_start': point.get('fuel'), 'fuel_end': point.get('fuel')} if 'fuel' in point else {})}

    @staticmethod
    def finish(active, last_active, stop=None, *, incomplete=False):
        active['status'] = 'incomplete' if incomplete else 'complete'
        active['partial_end'] = incomplete
        active['last_observed_at'] = last_active['time']
        active['end_bounds'] = {'after': last_active['time'], 'by': stop['time'] if stop else None}
        if stop:
            active['stop_state'] = stop['charge_state']
            # A subsequent driving sample can include consumption after charging.
            if (not moved(stop) and stop['soc'] is not None and
                    (active['soc_end'] is None or stop['soc'] >= active['soc_end'])):
                active['soc_end'] = stop['soc']
                active['soc_end_source'] = 'first_noncharging_sample'
            if 'fuel' in stop:
                active['fuel_end'] = stop['fuel']
        active['observed_span_minutes'] = seconds(active['first_observed_at'], last_active['time']) / 60
        a, b = active['soc_start'], active['soc_end']
        active['soc_gain_points'] = b - a if a is not None and b is not None and b >= a else None
        return active

    def observe(self, vehicle, point, state, zone, max_gap):
        """Return changed records; caller commits records, sample and state together."""
        changed = []
        previous = state.get('previous')
        active = state.get('active')
        gap = (previous is not None and seconds(previous['time'], point['time']) > max_gap
               or active is not None and seconds(state['last_charging']['time'], point['time']) > max_gap)
        if active and gap:
            changed.append(self.finish(active, state['last_charging'], incomplete=True))
            state.pop('active', None)
            state.pop('pending_stop', None)
            active = None
        code = point['charge_state']
        if code in ACTIVE_CHARGING:
            if not active:
                baseline = (previous if previous and not gap and previous['charge_state'] in KNOWN_STATES
                            and previous['charge_state'] not in ACTIVE_CHARGING else None)
                active = self.start(vehicle, point, baseline, zone)
            else:
                if state.pop('pending_stop', None):
                    active['pause_count'] += 1
                active['sample_count'] += 1
                active['charging_sample_count'] += 1
                active['last_observed_at'] = point['time']
                active['soc_end'] = point['soc']
                active['soc_end_source'] = 'last_charging_sample'
                if active['soc_start'] is not None and point['soc'] is not None and point['soc'] < active['soc_start'] - 2:
                    active['soc_inconsistent'] = True
                if 'fuel' in point:
                    active['fuel_end'] = point['fuel']
            state['active'] = active
            state['last_charging'] = point
            active['status'] = 'charging'
            changed.append(dict(active))
            state['observation'] = 'charging'
        elif active:
            active['sample_count'] += 1
            if code not in KNOWN_STATES:
                # Unknown is not evidence of an end or a second confirmation.
                state['observation'] = 'unknown_charge_state'
            else:
                pending = state.get('pending_stop')
                explicit = moved(point) or unplugged(point)
                if explicit or pending is not None:
                    changed.append(self.finish(active, state['last_charging'], pending or point))
                    state.pop('active', None)
                    state.pop('pending_stop', None)
                    state['observation'] = 'complete'
                else:
                    state['pending_stop'] = point
                    active['status'] = 'paused'
                    changed.append(dict(active))
                    state['observation'] = 'confirming_end'
        else:
            state['observation'] = 'idle' if code in KNOWN_STATES else 'unknown_charge_state'
        return changed
