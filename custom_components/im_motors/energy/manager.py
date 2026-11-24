"""Consume the coordinator's samples without making any vehicle requests."""
from datetime import datetime, timezone
import json
import logging
from zoneinfo import ZoneInfo

from .charge_recorder import ChargeRecorder, sample
from .refuel_recorder import observe as observe_fuel
from .store import EnergyStore

LOGGER = logging.getLogger(__name__)


class EnergyManager:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.store = EnergyStore(coordinator.client.data_dir / 'energy.sqlite3')
        self.zone = ZoneInfo(hass.config.time_zone)
        self.recorder = ChargeRecorder()
        self.errors = {}

    @property
    def enabled(self):
        return self.entry.options.get('charge_history_enabled', False)

    async def setup(self):
        try:
            await self.hass.async_add_executor_job(self.store.initialize)
            self.coordinator.hybrid_capabilities = await self.hass.async_add_executor_job(self.store.capabilities)
        except Exception:
            self.errors = {v.identifier: 'storage_error' for v in self.coordinator.data.vehicles}
            LOGGER.error('Energy archive could not initialize; vehicle telemetry remains available')
            return
        await self.observe(self.coordinator.data)

    async def observe(self, snapshot):
        for vehicle, telemetry in snapshot.telemetry.items():
            try:
                capability = telemetry.supports_feature('HYBRID_ELECTRIC')
                if capability is not None:
                    if self.coordinator.hybrid_capabilities.get(vehicle) is not capability:
                        await self.hass.async_add_executor_job(self.store.remember_capability, vehicle, capability)
                    self.coordinator.hybrid_capabilities[vehicle] = capability
                is_hybrid = self.coordinator.hybrid_capabilities.get(vehicle) is True
                point, reason = sample(telemetry, datetime.now(timezone.utc),
                                       self.coordinator.telemetry_interval_minutes, hybrid=is_hybrid)
                fuel_enabled = is_hybrid and self.entry.options.get('refuel_detection_enabled', False)
                await self.hass.async_add_executor_job(self._observe, vehicle, point, reason, fuel_enabled)
                self.errors.pop(vehicle, None)
            except Exception:
                # Local disk problems must not stop vehicle telemetry or trip recording.
                if vehicle not in self.errors:
                    LOGGER.error('Energy archive could not store a sample; vehicle telemetry remains available')
                self.errors[vehicle] = 'storage_error'

    def _observe(self, vehicle, point, reason, fuel_enabled):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM state WHERE vehicle=?', (vehicle,)).fetchone()
            state = json.loads(row[0]) if row else {}
            if not self.enabled:
                if state.get('active'):
                    # An explicit disable interrupts observation, even if re-enabled shortly afterwards.
                    self.store.put(db, vehicle, self.recorder.finish(
                        state['active'], state['last_charging'], incomplete=True))
                for key in ('active', 'pending_stop', 'last_charging'):
                    state.pop(key, None)
            if not fuel_enabled:
                state.pop('fuel_candidate', None)
            if not self.enabled and not fuel_enabled:
                state.pop('previous', None)
                state['observation'] = 'disabled'
            elif point is None:
                state['observation'] = reason
            elif state.get('previous') and point['time'] <= state['previous']['time']:
                state['observation'] = 'cached_or_out_of_order'
            else:
                db.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?)',
                           (vehicle, point['time'], json.dumps(point)))
                max_gap = max(900, self.coordinator.telemetry_interval_minutes * 180)
                records = self.recorder.observe(vehicle, point, state, self.zone, max_gap) if self.enabled else []
                if fuel_enabled:
                    records += observe_fuel(vehicle, point, state, self.zone, max_gap)
                for record in records:
                    self.store.put(db, vehicle, record)
                state['previous'] = point
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (vehicle, json.dumps(state)))

    async def stop(self):
        """Writes are awaited inline; no background network or worker to cancel."""
