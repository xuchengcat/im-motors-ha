"""Persistent daily 02:00–06:00 randomized community check-in for primary accounts."""
import asyncio
from datetime import datetime, timedelta
import secrets
from zoneinfo import ZoneInfo

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_time

from .checkin_api import CheckinApi, checked_today
from .const import ACCOUNT_PRIMARY, account_type
from .pyim_china.credential_store import key_context, vault_for_path
from .pyim_china.runtime_lock import AccountFileLock

OPTION = 'daily_checkin_enabled'
TERMINAL = {'already_checked', 'checked', 'error', 'unknown', 'missed'}


def plan(state, now, randbelow=secrets.randbelow):
    """Persist a chosen time; restarting never redraws an existing daily plan."""
    today = now.date()
    attempts = state.setdefault('attempts', {})
    day = today if now.hour < 6 and not (now.hour == 5 and now.minute == 59 and now.second >= 58) and attempts.get(today.isoformat(), {}).get('status') not in TERMINAL else today+timedelta(days=1)
    stamp = day.isoformat()
    old = state.get('next_run')
    if old and datetime.fromisoformat(old).date() == day:
        return datetime.fromisoformat(old)
    start = datetime.combine(day, datetime.min.time(), now.tzinfo)+timedelta(hours=2)
    end = start+timedelta(hours=4)
    start = max(start, now.replace(microsecond=0)+timedelta(seconds=1))
    seconds = max(1, int((end-start).total_seconds()))
    target = start+timedelta(seconds=randbelow(seconds))
    state['next_run'] = target.isoformat()
    state['attempts'] = {d:v for d,v in attempts.items() if d >= (today-timedelta(days=10)).isoformat()}
    return target


def transaction(client, state, now, save, api):
    """Called under the account lock. Durable submitting marker prevents replay."""
    day = now.date().isoformat()
    if not 2 <= now.hour < 6:
        return
    attempts = state.setdefault('attempts', {})
    previous = attempts.get(day, {}).get('status')
    if previous in TERMINAL:
        return
    attempt = {'date': day, 'started_at': now.isoformat(), 'status': 'checking'}
    # A crash or timeout during POST must not result in a second POST.
    uncertain = previous == 'submitting'
    attempts[day] = attempt
    if uncertain:
        attempt['status'] = 'submitting'
    save(state)
    try:
        api.call('info')
        record = api.call('record', day)
        server_time = record.get('nowTime')
        if not isinstance(server_time, (str, int)) or isinstance(server_time, bool):
            raise ValueError('Missing server time')
        server_now = datetime.fromtimestamp(int(server_time)/1000, now.tzinfo)
        if server_now.date() != now.date() or abs((server_now-now).total_seconds()) > 300:
            raise ValueError('Server date or time differs')
        if checked_today(record, day):
            attempt['status'] = 'already_checked'
        elif uncertain:
            attempt['status'] = 'unknown'
        else:
            current = datetime.fromtimestamp(client.clock()/1000, now.tzinfo) if client else now
            if current.date() != now.date() or not 2 <= current.hour < 6:
                raise ValueError('Check-in window closed before submission')
            attempt['status'] = 'submitting'
            save(state)  # Atomic fsync before the only side effect.
            api.call('check')
            # Trust the same record used by the App, not the popup response.
            attempt['status'] = 'checked' if checked_today(api.call('record', day), day) else 'unknown'
        count = record.get('signSum')
        if type(count) is int:
            attempt['sign_count_before'] = count
    except Exception:
        attempt['status'] = 'unknown' if attempt['status'] == 'submitting' else 'error'
        attempt['error'] = 'checkin_request_failed'
    attempt['finished_at'] = now.isoformat()
    state['last_result'] = dict(attempt)
    save(state)


class CheckinManager:
    def __init__(self, hass, entry, coordinator):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.state = {}
        self._remove_timer = self._remove_started = None
        self._task = None
        self._stopping = False
        self.api = CheckinApi(coordinator.client)

    @property
    def enabled(self):
        return account_type(self.entry) == ACCOUNT_PRIMARY and self.entry.options.get(OPTION, False)

    def now(self):
        return datetime.now(ZoneInfo(self.hass.config.time_zone))

    def _work(self, run):
        c = self.coordinator.client
        with key_context(c.key_file), AccountFileLock(c.data_dir):
            vault = vault_for_path(c.data_dir/'checkin-state.imvault')
            state = vault._load_payload() if vault.path.exists() else {'schema_version': 1, 'kind': 'daily-checkin'}
            if run:
                transaction(c, state, self.now(), vault._save_payload, self.api)
            plan(state, self.now())
            vault._save_payload(state)
            return state

    async def setup(self):
        if not self.enabled:
            return
        if self.hass.is_running:
            self._launch(False)
        else:
            @callback
            def started(event):
                self._remove_started = None
                self._launch(False)
            self._remove_started = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, started)

    @callback
    def _launch(self, run):
        if not self._stopping and not (self._task and not self._task.done()):
            self._task = self.hass.async_create_background_task(self._cycle(run), 'im_motors_checkin', eager_start=False)

    async def _cycle(self, run):
        try:
            async with self.coordinator._request_lock:
                job = self.hass.async_add_executor_job(self._work, run)
                try:
                    self.state = await asyncio.shield(job)
                except asyncio.CancelledError:
                    await job
                    raise
            target = datetime.fromisoformat(self.state['next_run'])
            target = max(target, self.now()+timedelta(seconds=1))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Lock contention/storage faults do not send SMS or a blind POST.
            target = self.now()+timedelta(minutes=5)
        if not self._stopping:
            self.coordinator.async_update_listeners()
            @callback
            def trigger(_):
                self._remove_timer = None
                self._launch(True)
            self._remove_timer = async_track_point_in_time(self.hass, trigger, target)

    async def stop(self):
        self._stopping = True
        for remove in (self._remove_timer, self._remove_started):
            if remove:
                remove()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
