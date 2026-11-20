"""Primary account: current cumulative kilometres from today's driving H5."""
from .trip_api import TripReader
from ..trip_api import TripError

SOURCE = 'drive/daily/new.totalMileage'


class PrimaryOdometerSource:
    field = SOURCE
    name = 'official_driving_calendar'
    single_vehicle = True

    def __init__(self, client, reader=None):
        self.reader = reader or TripReader(client)

    def read(self, day, vehicle):
        return self.reader.read('summary', {'tripDate':day}, vehicle)

    def parse(self, data, vin):
        from ..odometer import numeric_mileage
        if not isinstance(data, dict) or data.get('vin') != vin:
            raise TripError('vehicle_mismatch')
        return numeric_mileage(data.get('totalMileage'))
