"""Authorized account: kilometres from vehicle health/diagnostic status."""
from .health_api import HealthReader
from ..trip_api import TripError

SOURCE = 'maintenance/status.mileage'


class AuthorizedOdometerSource:
    field = SOURCE
    name = 'vehicle_health_diagnostics'
    single_vehicle = False

    def __init__(self, client, reader=None):
        self.reader = reader or HealthReader(client)

    def read(self, day, vehicle):
        return self.reader.read(vehicle)

    def parse(self, data, vin):
        from ..odometer import numeric_mileage
        if not isinstance(data, dict) or data.get('requested_vin') != vin:
            raise TripError('vehicle_mismatch')
        return numeric_mileage(data.get('mileage'))
