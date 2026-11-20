"""Authorized-account local trip recording from existing vehicle telemetry."""
import asyncio
from datetime import datetime, timezone
from ..trips import BaseTripManager, settings
from ..const import account_type, ACCOUNT_AUTHORIZED
from ..trip_api import TripError
from .trip_recorder import TripRecorder, sample


class TripManager(BaseTripManager):
    source = "ha_telemetry"

    def __init__(self, hass, entry, coordinator):
        super().__init__(hass, entry, coordinator)
        self.recorder = TripRecorder(self.store, time_zone=hass.config.time_zone)

    @property
    def enabled(self):
        return (account_type(self.entry) == ACCOUNT_AUTHORIZED
                and self.entry.options.get("trip_history_enabled", False))

    async def setup(self):
        await self.hass.async_add_executor_job(self.store.initialize)
        saved = await self.hass.async_add_executor_job(settings, self.coordinator.client)
        self.key = saved.get("amap_api_key", "")
        self.status = await self.hass.async_add_executor_job(self.store.state, "sync") or {"state": "idle"}
        if self.status.get("state") == "running":
            await self.set_status(state="error", error="interrupted")
        for vehicle in self.coordinator.data.vehicles:
            await self.hass.async_add_executor_job(self.recorder.rebuild_if_needed,
                vehicle.identifier, self.coordinator.telemetry_interval_minutes)
            self.recording[vehicle.identifier] = await self.hass.async_add_executor_job(self.store.state, "recorder:" + vehicle.identifier)
        if self.enabled:
            await self.observe(self.coordinator.data)
            if self.key and not self.task:
                self.start(geocode_only=True)

    async def observe(self, snapshot):
        if not self.enabled:
            return
        for vehicle, telemetry in snapshot.telemetry.items():
            try:
                point, reason = sample(telemetry, datetime.now(timezone.utc), self.coordinator.telemetry_interval_minutes)
                if point is None:
                    self.recording[vehicle] = dict(self.recording.get(vehicle, {}), observation=reason)
                    continue
                odo = getattr(snapshot, "odometer", {}).get(vehicle, {})
                point["odometer"] = odo.get("value")
                point["odometer_source"] = odo.get("source_field")
                previous_id = self.recording.get(vehicle, {}).get("last_trip_id")
                previous_endpoint = self.recording.get(vehicle, {}).get("endpoint_revision", 0)
                result = await self.hass.async_add_executor_job(self.recorder.observe, vehicle, point, self.coordinator.telemetry_interval_minutes)
                self.recording[vehicle] = result
                if (result.get("last_trip_id") != previous_id or result.get("endpoint_revision", 0) != previous_endpoint) and self.key and not self.task:
                    self.start(geocode_only=True)
            except Exception:
                self.recording[vehicle] = dict(self.recording.get(vehicle, {}), observation="recording_failed")

    def start(self, *, geocode_only=False):
        if not self.enabled:
            raise TripError("history_disabled")
        if self.task is not None:
            return False
        self.task = self.hass.async_create_task(self._run(geocode_only=geocode_only), "im_motors trip addresses")
        return True

    async def _run(self, *, geocode_only=False):
        try:
            await self.set_status(state="running", error=None, address_error=None)
            if not geocode_only:
                await self.coordinator.async_query_vehicle()
            for vehicle in self.coordinator.data.vehicles:
                await self.geocode(vehicle.identifier)
            await self.set_status(state="complete", error=None, finished_at=datetime.now(timezone.utc).isoformat())
        except asyncio.CancelledError:
            await self.set_status(state="error", error="interrupted")
            raise
        except Exception as error:
            await self.set_status(state="error", error=error.code if isinstance(error, TripError) else "recording_failed")
        finally:
            self.task = None
