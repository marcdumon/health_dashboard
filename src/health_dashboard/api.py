"""The HTTP API of the health dashboard and its single-page GUI. A page read never downloads or changes anything: only a
button press does (Fetch now, Import, Download Fitbit, ...)."""

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google_api.auth import AuthError
from pydantic import BaseModel, Field

from health_dashboard import analysis as health_analysis
from health_dashboard import fitbit as health_fitbit
from health_dashboard import food as health_food
from health_dashboard import history as health_history
from health_dashboard import measurements as health_measurements
from health_dashboard import (
    overview,
    polar,
    polar_detail,
    polar_energy,
    polar_hrv,
    polar_load,
    polar_record,
    polar_recovery,
    polar_stairs,
    polar_store,
    sleep_summary,
    withings,
)
from health_dashboard import sleep as health_sleep
from health_dashboard import store as health_store
from health_dashboard import supplements as health_supplements
from health_dashboard.config import config

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / 'static'

app = FastAPI(title='health_dashboard')


@app.exception_handler(AuthError)
def auth_error(_: Request, error: AuthError) -> JSONResponse:
    """The Google Health login missing or expired: the Fitbit card shows a login button."""
    return JSONResponse({'detail': str(error), 'auth': True}, status_code=401)


def code_stamp() -> float:
    """When the app's Python code last changed on disk (GUI files are served fresh, so they need no restart)."""
    return max((f.stat().st_mtime for f in Path(__file__).parent.rglob('*.py')), default=0.0)


# the running server's own code; newer code on disk only takes effect after a restart
STARTED_STAMP = code_stamp()


@app.get('/api/version')
def version() -> dict:
    """Which GUI build the server serves, so an open page can tell it is outdated, and whether the server itself is."""
    index = STATIC / 'index.html'
    return {'build': str(int(index.stat().st_mtime)) if index.exists() else '', 'restart_needed': code_stamp() > STARTED_STAMP}


def _in_background(work: Callable[..., object], *args: object) -> None:
    """Run long work in a thread; the page polls for its progress."""
    threading.Thread(target=work, args=args, daemon=True).start()


def user_timezone() -> ZoneInfo:
    """The time zone of the days, from health_dashboard.toml."""
    return config().tz


def _health_now() -> datetime:
    """The time now in the user's time zone."""
    return datetime.now(user_timezone())


def _health_day(day: str | None) -> date:
    """A day of the Health page, checked: today or an earlier day."""
    today = health_store.health_day(_health_now())
    try:
        chosen = date.fromisoformat(day) if day else today
    except ValueError as error:
        raise HTTPException(400, f'"{day}" is not a day. Write it as YYYY-MM-DD.') from error
    if chosen > today:
        raise HTTPException(400, 'That day has not come yet. Choose today or an earlier day.')
    return chosen


def _regime_day(day: str | None) -> date:
    """A day of the supplement regime, checked: any day, also one that has not come yet (a change planned for it)."""
    try:
        return date.fromisoformat(day) if day else health_store.health_day(_health_now())
    except ValueError as error:
        raise HTTPException(400, f'"{day}" is not a day. Write it as YYYY-MM-DD.') from error


def _health_view(day: date) -> dict:
    """The Health page for a day: the supplements of the day with their entries and moments (the regime has its own
    route), the angina score, the Ketostix readings, the tape measurements with the last ones before the day, the latest
    Withings values up to the end of the day, the link, the Polar H10 import with the last sessions up to the day, and the
    sleep of the day with a look at the Sleep as Android backup, and the Fitbit values of the day with a look at the token,
    and the food of the day and of the day before (both are noted in the morning)."""
    now = _health_now()
    return {'day': day.isoformat(), 'today': health_store.health_day(now).isoformat(), 'now': now.strftime('%H:%M'),
            'supplements': health_supplements.day_view(day),
            'angina': health_store.angina(day), 'ketones': health_store.ketone_readings(day),
            'ketostix': health_store.KETOSTIX, 'tape': {'values': health_store.tape(day), 'last': health_store.last_tape(day)},
            'latest': withings.latest(before=f'{day + timedelta(days=1)}T{health_store.DAY_START_HOUR:02d}:00'),
            'withings': withings.status(),
            'polar': polar.status() | _polar_latest(day.isoformat()) | {
                'last_hrv': polar_hrv.last_test(day.isoformat()), 'last_recovery': polar_recovery.last_test(day.isoformat()),
                'last_stairs': polar_stairs.last_test(day.isoformat())},
            'sleep': health_sleep.status(now) | {'day': health_sleep.day_card(day.isoformat())},
            'fitbit': health_fitbit.status() | {'day': health_fitbit.day_card(day.isoformat())},
            'food': {'day': health_food.day(day), 'day_before': health_food.day(day - timedelta(days=1))}}


def _polar_latest(day: str) -> dict:
    """The newest session up to the day that is not an HRV test, with its net energy expenditure (None when a value it
    needs is missing)."""
    found = polar_store.latest(day)
    last = found['last_exercise']
    if last is not None:
        last['energy'] = polar_energy.energy([last])[last['id']]
    return found


class HealthSupplement(BaseModel):
    """A supplement of the list: its name, its dose unit (none: a yes or no of the day) and the amount of one dose; the day
    of the page."""

    day: str
    name: str
    dose_unit: str = ''
    step: float | None = None
    comment: str = ''


class HealthSupplementChange(HealthSupplement):
    """A supplement of the list with its plan (the amount at each moment) from the day on; without a comment, its comment
    stays."""

    plan: dict[str, float] = {}
    comment: str | None = None


class HealthIntake(BaseModel):
    """An amount of a supplement taken on a day at a time; none: one dose."""

    day: str
    supplement_id: int
    taken_at: str
    amount: float | None = None


class RegimeCopy(BaseModel):
    """The regime of one day copied to another: from that day on, the plan is the plan of the first day."""

    from_day: str
    to_day: str


class HealthAmount(BaseModel):
    """A new amount of an entry."""

    amount: float


class HealthMoment(BaseModel):
    """A moment of the day taken at a time."""

    day: str
    moment: str
    taken_at: str


class HealthKetones(BaseModel):
    """A Ketostix reading of a day: its time and its colour in mmol/L."""

    day: str
    taken_at: str
    level: float


class HealthTime(BaseModel):
    """A new clock time."""

    taken_at: str


class HealthTape(BaseModel):
    """The circumference of one site on a day in cm; none removes it."""

    day: str
    site: str
    cm: float | None = None


class HealthAngina(BaseModel):
    """The day's angina score (0 to 5, or none) and note, or angina not measured (no exertion that day)."""

    day: str
    score: int | None = None
    note: str = ''
    not_measured: bool = False


class HealthFood(BaseModel):
    """Some fields of the food of a day: the fields not sent stay. The day of the page (default: the food's day) gives the
    view of the answer, so a note for the day before keeps the page on its day."""

    day: str
    page_day: str | None = None
    first_food: str | None = None
    last_food: str | None = None
    amount: int | None = None
    protein: int | None = None
    carbs: int | None = None
    fat: int | None = None
    alcohol: bool | None = None
    salt: bool | None = None
    fast: bool | None = None
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None


@app.get('/api/health')
def health_view(day: str | None = None) -> dict:
    """A day of the Health page. A read only: Withings data is downloaded only by Fetch now."""
    return _health_view(_health_day(day))


@app.get('/api/health/history')
def health_history_view(days: int = Query(30, ge=7, le=365), activity: int | None = None) -> dict:
    """One row per day: supplements taken, angina, blood pressure, scale values, the Polar H10 values (with `activity`, of
    that activity only), the sleep; each recording at its time (points); each sleep with its times (sleeps); and the
    measurements removed as wrong."""
    today = health_store.health_day(_health_now())
    first = today - timedelta(days=days - 1)
    return {'days': health_history.days(days, today), 'points': health_history.chart_points(first, today, activity),
            'sleeps': health_sleep.sleeps(first.isoformat(), today.isoformat()), 'removed': health_measurements.removed(),
            'supplements': health_supplements.value_keys()}


@app.get('/api/health/supplement-relations')
def health_supplement_relations(key: str, days: int | None = Query(None, ge=7, le=20000),
                                lag: int = Query(1, ge=0, le=health_analysis.MAX_LAG)) -> dict:
    """Each supplement taken in the period against a value of the history, by day, the amount `lag` days earlier."""
    if not health_analysis.known(key):
        raise HTTPException(404, f'The Health history has no value named {key}.')
    try:
        return {'relations': health_analysis.supplement_relations(key, days, health_store.health_day(_health_now()), lag)}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get('/api/health/analysis')
def health_analysis_view(key: str, days: int | None = Query(None, ge=7, le=20000), window: int = Query(30, ge=3, le=365),
                         activity: int | None = None) -> dict:
    """The analysis of one value of the history, for the large view of a chart; without a number of days, of all data."""
    if not health_analysis.known(key):
        raise HTTPException(404, f'The Health history has no value named {key}.')
    return health_analysis.analyse(key, days, window, health_store.health_day(_health_now()), activity)


@app.get('/api/health/relation')
def health_relation_view(key: str, other: str, days: int | None = Query(None, ge=7, le=20000),
                         lag: int = Query(0, ge=0, le=health_analysis.MAX_LAG)) -> dict:
    """Two values of the history by day, the second one `lag` days earlier, with their correlation and that of every
    delay; without a number of days, of all data. A value of the Polar H10 sessions has no value of a day: 400."""
    for name in (key, other):
        if not health_analysis.known(name):
            raise HTTPException(404, f'The Health history has no value named {name}.')
    try:
        return health_analysis.relation(key, other, days, health_store.health_day(_health_now()), lag)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


class HealthGroups(BaseModel):
    """The Withings groups of one measurement (a weigh-in, or a blood pressure reading)."""

    grpids: list[int]


@app.get('/api/health/days/{day}/measurements')
def health_day_measurements(day: str) -> dict:
    """Each weigh-in and each blood pressure reading of a day, to remove a wrong one."""
    return {'measurements': health_measurements.day(_health_day(day))}


@app.post('/api/health/measurements/hide')
def health_hide(given: HealthGroups) -> dict:
    """Remove a wrong measurement from the history and the latest values; nothing is deleted at Withings."""
    health_measurements.hide(given.grpids)
    return {'removed': health_measurements.removed()}


@app.post('/api/health/measurements/show')
def health_show(given: HealthGroups) -> dict:
    """Put a removed measurement back."""
    health_measurements.show(given.grpids)
    return {'removed': health_measurements.removed()}


NO_SUPPLEMENT = 'This supplement is not in the list any more. Reload the page.'
NO_ENTRY = 'This entry is not there any more. Reload the page.'


@app.post('/api/health/intakes')
def health_intake_add(given: HealthIntake) -> dict:
    """Log an amount of a supplement taken at a time (none: one dose)."""
    day = _health_day(given.day)
    try:
        health_supplements.add_intake(day, given.supplement_id, given.taken_at, given.amount)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, NO_SUPPLEMENT) from error
    return _health_view(day)


@app.put('/api/health/intakes/{intake_id}')
def health_intake_amount(intake_id: int, given: HealthAmount) -> dict:
    """Change the amount of an entry."""
    try:
        return _health_view(health_supplements.set_amount(intake_id, given.amount))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, NO_ENTRY) from error


@app.delete('/api/health/intakes/{intake_id}')
def health_intake_remove(intake_id: int) -> dict:
    """Remove an entry at once."""
    try:
        return _health_view(health_supplements.remove_intake(intake_id))
    except KeyError as error:
        raise HTTPException(404, NO_ENTRY) from error


@app.post('/api/health/moments')
def health_moment_take(given: HealthMoment) -> dict:
    """Log the planned amounts of a moment of the day."""
    day = _health_day(given.day)
    try:
        health_supplements.take_moment(day, given.moment, given.taken_at)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _health_view(day)


@app.delete('/api/health/moments')
def health_moment_undo(day: str, moment: str) -> dict:
    """Remove the entries of a moment of a day at once."""
    chosen = _health_day(day)
    health_supplements.undo_moment(chosen, moment)
    return _health_view(chosen)


@app.get('/api/health/regime')
def health_regime(day: str | None = None) -> dict:
    """The supplement regime of a day (today without one), also of a day that has not come yet, with every change."""
    return {'regime': health_supplements.regime(_regime_day(day))}


@app.post('/api/health/regime/copy')
def health_regime_copy(given: RegimeCopy) -> dict:
    """Copy the regime of a day to another day, from which it counts; the regime of that day."""
    target = _regime_day(given.to_day)
    try:
        health_supplements.copy_regime(_regime_day(given.from_day), target)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {'regime': health_supplements.regime(target)}


@app.post('/api/health/supplements')
def health_supplement_add(given: HealthSupplement) -> dict:
    """Add a supplement to the list, from its day on; the regime of that day."""
    day = _regime_day(given.day)
    try:
        health_supplements.add_supplement(day, given.name, given.dose_unit, given.step, given.comment)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {'regime': health_supplements.regime(day)}


@app.put('/api/health/supplements/{supplement_id}')
def health_supplement_change(supplement_id: int, given: HealthSupplementChange) -> dict:
    """Change a supplement of the list, and its plan from its day on; the regime of that day."""
    day = _regime_day(given.day)
    try:
        health_supplements.change_supplement(supplement_id, day, given.name, given.dose_unit, given.step, given.plan,
                                             given.comment)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, NO_SUPPLEMENT) from error
    return {'regime': health_supplements.regime(day)}


@app.delete('/api/health/supplements/{supplement_id}')
def health_supplement_remove(supplement_id: int, day: str) -> dict:
    """Take a supplement out of the list from a day on, at once; its entries stay. The regime of that day."""
    chosen = _regime_day(day)
    try:
        health_supplements.remove_supplement(supplement_id, chosen)
    except KeyError as error:
        raise HTTPException(404, NO_SUPPLEMENT) from error
    return {'regime': health_supplements.regime(chosen)}


@app.put('/api/health/angina')
def health_angina(given: HealthAngina) -> dict:
    """The day's angina score and note."""
    day = _health_day(given.day)
    try:
        health_store.set_angina(day, given.score, given.note, given.not_measured)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _health_view(day)


@app.put('/api/health/food')
def health_food_set(given: HealthFood) -> dict:
    """Some fields of the food of a day; the view of the page's day."""
    day = _health_day(given.day)
    page = _health_day(given.page_day) if given.page_day else day
    try:
        health_food.set_food(day, given.model_dump(exclude_unset=True, exclude={'day', 'page_day'}))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _health_view(page)


@app.delete('/api/health/food')
def health_food_clear(day: str, page_day: str | None = None) -> dict:
    """Remove the food note of a day at once; the view of the page's day."""
    chosen = _health_day(day)
    health_food.clear(chosen)
    return _health_view(_health_day(page_day) if page_day else chosen)


@app.put('/api/health/tape')
def health_tape(given: HealthTape) -> dict:
    """One tape measurement of the day, or its removal."""
    day = _health_day(given.day)
    try:
        health_store.set_tape(day, given.site, given.cm)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _health_view(day)


@app.post('/api/health/ketones')
def health_ketones_add(given: HealthKetones) -> dict:
    """Note a Ketostix reading at a time."""
    day = _health_day(given.day)
    try:
        health_store.add_ketone_reading(day, given.taken_at, given.level)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return _health_view(day)


@app.put('/api/health/ketones/{reading_id}')
def health_ketones_time(reading_id: int, given: HealthTime) -> dict:
    """Change the time of a Ketostix reading."""
    try:
        return _health_view(health_store.set_ketone_time(reading_id, given.taken_at))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, 'This Ketostix reading is not there any more. Reload the page.') from error


@app.delete('/api/health/ketones/{reading_id}')
def health_ketones_remove(reading_id: int) -> dict:
    """Remove a Ketostix reading at once."""
    try:
        return _health_view(health_store.remove_ketone_reading(reading_id))
    except KeyError as error:
        raise HTTPException(404, 'This Ketostix reading is not there any more. Reload the page.') from error


def _health_page() -> RedirectResponse:
    """Back to the Health page, on the address the app is opened at."""
    return RedirectResponse(f'http://127.0.0.1:{config().port}/')


def _finish_sign_in(query: dict[str, str]) -> str:
    """The end of the Withings sign-in, on the app's route or the other port of the user's callback: the code becomes
    tokens. Nothing is downloaded: that is Fetch now. The page the browser goes to next."""
    try:
        if query.get('error') or not query.get('code'):
            raise withings.WithingsError('You did not allow the app to read your Withings data. Press Connect Withings, and '
                                         'press Allow at Withings.')
        with httpx.Client(timeout=30.0) as http:
            withings.connect(query['code'], query.get('state', ''), http)
    except withings.WithingsError as failure:
        health_store.set_setting('withings_error', str(failure))
    return f'http://127.0.0.1:{config().port}/'


@app.get('/api/health/withings/connect')
def withings_connect() -> RedirectResponse:
    """To the Withings sign-in, waiting on the callback's port when it is not the app's; without the app codes, back to the
    page, which says what to do."""
    try:
        url = withings.authorize_url()
        withings.listen(_finish_sign_in)
        return RedirectResponse(url)
    except withings.WithingsError as error:
        health_store.set_setting('withings_error', str(error))
        return _health_page()


@app.get('/api/health/withings/callback')
def withings_callback(code: str = '', state: str = '', error: str = '') -> RedirectResponse:
    """Withings sends the browser back here after the sign-in."""
    return RedirectResponse(_finish_sign_in({'code': code, 'state': state, 'error': error}))


@app.post('/api/health/withings/fetch')
def withings_fetch(day: str | None = None) -> dict:
    """Fetch new Withings measurements now (Withings allows one fetch in 10 minutes)."""
    if not withings.status()['connected']:
        raise HTTPException(409, 'Withings is not connected. Press Connect Withings first.')
    try:
        withings.sync(asked=True)
    except withings.TooSoon as error:
        raise HTTPException(429, str(error)) from error
    return _health_view(_health_day(day))


@app.post('/api/health/polar/import')
def polar_import(day: str | None = None) -> dict:
    """Import the new and changed Polar H10 sessions of the two folders, in the background; the page asks for the progress.
    A second press while an import runs does nothing."""
    if not polar.status()['configured']:
        raise HTTPException(409, polar.NOT_SET)
    # the timezone first: a failure to find it must not keep the import taken
    tz = _health_now().tzinfo
    if polar.begin():
        _in_background(polar.run_import, tz)
    return _health_view(_health_day(day))


@app.post('/api/health/sleep/import')
def sleep_import(day: str | None = None) -> dict:
    """Read the Sleep as Android backup and replace the stored sleeps with its sleeps. A backup that is not set, not there
    or cannot be read changes nothing: 409 with what to do."""
    try:
        health_sleep.import_backup()
    except health_sleep.SleepError as error:
        raise HTTPException(409, str(error)) from error
    return _health_view(_health_day(day))


@app.post('/api/health/fitbit/download')
def fitbit_download(day: str | None = None) -> dict:
    """Get the Fitbit data from Google Health up to today and compute the days that changed. A first day that is not set
    gives 409 with what to do; a missing or refused token gives 401 (the card offers the login)."""
    try:
        health_fitbit.download(health_store.health_day(_health_now()), user_timezone())
    except health_fitbit.FitbitError as error:
        raise HTTPException(409, str(error)) from error
    return _health_view(_health_day(day))


@app.post('/api/health/fitbit/login')
def fitbit_login(day: str | None = None) -> dict:
    """Open the Google Health consent page in the local browser and wait for it; the token goes to the secrets of the app folder."""
    health_fitbit.login()
    return _health_view(_health_day(day))


@app.get('/api/health/fitbit/day')
def fitbit_day(day: str | None = None) -> dict:
    """The Fitbit charts of a day: HR per minute, SpO2, HRV, steps, the sleep stages and the exercises."""
    return health_fitbit.day_detail(_health_day(day).isoformat())


@app.get('/api/health/overview')
def health_overview(day: str | None = None) -> dict:
    """The tiles of the overview row of a day (today by default): see overview."""
    return {'tiles': overview.overview(_health_day(day))}


@app.get('/api/health/sleep/summary')
def health_sleep_summary(days: int = Query(30, ge=7, le=3650)) -> dict | None:
    """The sleep pattern of the main sleeps of the period (sleep_summary); None without a sleep."""
    today = health_store.health_day(_health_now())
    return sleep_summary.summary((today - timedelta(days=days - 1)).isoformat(), today.isoformat())


@app.get('/api/health/sleep/times')
def sleep_times(days: int | None = Query(None, ge=7, le=20000)) -> dict:
    """Each sleep of the last `days` days with its times, for the large view of the sleep times; without a number of days,
    from the first sleep on. The first and the last day of the period."""
    today = health_store.health_day(_health_now())
    first = (today - timedelta(days=days - 1)).isoformat() if days else health_sleep.first_day() or today.isoformat()
    return {'first': first, 'last': today.isoformat(), 'sleeps': health_sleep.sleeps(first, today.isoformat())}


@app.get('/api/health/polar/waiting')
def polar_waiting() -> dict:
    """How many sessions in the two folders on the PC wait for the import; it only lists the folders and changes nothing."""
    return polar.waiting()


@app.get('/api/health/polar/sessions')
def polar_sessions(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=500)) -> dict:
    """The sessions newest first, how many there are, and the removed ones."""
    return polar_store.sessions(offset, limit) | {'removed': polar_store.removed_sessions()}


@app.get('/api/health/polar/days/{day}')
def polar_day(day: str) -> dict:
    """The sessions of one day, removed ones too: for a day of the history table or an outlier of the analysis."""
    return {'sessions': polar_store.day_sessions(_health_day(day).isoformat())}


@app.get('/api/health/polar/sessions/{session_id}')
def polar_session_detail(session_id: int) -> dict:
    """One session for its popup: each second, the zones, the parts and the comparison with the same activity."""
    found = polar_detail.detail(session_id)
    if found is None:
        raise HTTPException(404, 'There is no such Polar H10 session. Reload the page.')
    return found


@app.get('/api/health/polar/zones')
def polar_zones(days: int = Query(30, ge=7, le=365)) -> dict:
    """The minutes per week in each of the 5 HR zones of the sessions that are not HRV tests, with the zone limits of this
    year; both None without a birth date in person.toml."""
    today = health_store.health_day(_health_now())
    return polar_detail.zone_weeks(today - timedelta(days=days - 1), today) or {'limits': None, 'weeks': None}


@app.get('/api/health/polar/activity')
def polar_activity_weeks(days: int = Query(30, ge=7, le=365)) -> dict:
    """The minutes of moderate and of vigorous activity per week (Monday first) of the sessions that are not HRV tests, the
    WHO minimum a week (a vigorous minute counts double), and what person.toml must give when it is missing."""
    today = health_store.health_day(_health_now())
    return {'weeks': polar_load.weeks(today - timedelta(days=days - 1), today), 'target': polar_load.WHO_WEEK,
            'missing': polar_load.missing()}


@app.get('/api/health/polar/energy')
def polar_energy_weeks(days: int = Query(30, ge=7, le=365)) -> dict:
    """The net energy expenditure of the sessions that are not HRV tests per week (Monday first) and per activity, the
    activity names in list order (the colours of the chart follow it), and what person.toml must give when it is missing."""
    today = health_store.health_day(_health_now())
    return {'weeks': polar_energy.weeks(today - timedelta(days=days - 1), today),
            'activities': [a['name'] for a in polar_store.activities()], 'missing': polar_energy.missing()}


@app.get('/api/health/polar/weeks')
def polar_weeks(days: int = Query(30, ge=7, le=365), activity: int | None = None) -> dict:
    """The minutes of the sessions per week (Monday first) and per activity, and the activity names in list order (the
    colours of the chart follow it)."""
    today = health_store.health_day(_health_now())
    return {'weeks': polar_store.weeks(today - timedelta(days=days - 1), today, activity),
            'activities': [a['name'] for a in polar_store.activities()]}


class PolarSessions(BaseModel):
    """The sessions that the user selected in the list of all sessions."""

    ids: list[int] = Field(min_length=1, max_length=500)


def _polar_mark(ids: list[int], removed: bool) -> dict:
    """Take sessions out of the history and the analysis, or put them back."""
    if not polar_store.set_removed(ids, removed):
        raise HTTPException(404, 'There is no such Polar H10 session. Reload the page.')
    return {'ok': True}


@app.post('/api/health/polar/sessions/remove')
def polar_remove_selected(given: PolarSessions) -> dict:
    """Remove the selected sessions at once; Show again puts them back."""
    return _polar_mark(given.ids, True)


@app.post('/api/health/polar/sessions/show')
def polar_show_selected(given: PolarSessions) -> dict:
    """Put the selected removed sessions back in the history and the analysis."""
    return _polar_mark(given.ids, False)


@app.post('/api/health/polar/sessions/join')
def polar_join(given: PolarSessions) -> dict:
    """Join the selected sessions, which a glitch of the logger app cut apart, into the first of them; Split takes them
    apart again."""
    try:
        polar_store.join(given.ids)
    except KeyError as error:
        raise HTTPException(404, 'There is no such Polar H10 session. Reload the page.') from error
    except polar_store.JoinError as error:
        raise HTTPException(400, str(error)) from error
    return {'ok': True}


@app.post('/api/health/polar/sessions/{session_id}/split')
def polar_split(session_id: int) -> dict:
    """Take a joined session apart again, at once."""
    try:
        polar_store.split(session_id)
    except KeyError as error:
        raise HTTPException(404, 'There is no such Polar H10 session. Reload the page.') from error
    except polar_store.JoinError as error:
        raise HTTPException(400, str(error)) from error
    return {'ok': True}


@app.post('/api/health/polar/sessions/{session_id}/remove')
def polar_remove(session_id: int) -> dict:
    """Remove a wrong session at once; Show again puts it back."""
    return _polar_mark([session_id], True)


@app.post('/api/health/polar/sessions/{session_id}/show')
def polar_show(session_id: int) -> dict:
    """Put a removed session back in the history and the analysis."""
    return _polar_mark([session_id], False)


class ActivityName(BaseModel):
    """The name of an activity of the Polar H10 sessions."""

    name: str = Field(max_length=40)


def _activity_name(given: ActivityName) -> str:
    """The name without spaces around it; 422 when nothing is left."""
    name = given.name.strip()
    if not name:
        raise HTTPException(422, 'Type a name for the activity.')
    return name


@app.get('/api/health/polar/activities')
def polar_activities() -> dict:
    """The activities in list order, each with how many sessions have it, and the activity of the last recording started
    from the card (None when it is not in the list)."""
    found = polar_store.activities()
    last = health_store.setting('polar_last_activity')
    return {'activities': found, 'last_used': int(last) if last and any(a['id'] == int(last) for a in found) else None}


@app.post('/api/health/polar/activities')
def polar_activity_add(given: ActivityName) -> dict:
    """Add an activity at the end of the list."""
    try:
        return {'id': polar_store.add_activity(_activity_name(given))}
    except polar_store.ActivityError as error:
        raise HTTPException(409, str(error)) from error


@app.post('/api/health/polar/activities/{activity_id}')
def polar_activity_rename(activity_id: int, given: ActivityName) -> dict:
    """Give an activity another name."""
    try:
        found = polar_store.rename_activity(activity_id, _activity_name(given))
    except polar_store.ActivityError as error:
        raise HTTPException(409, str(error)) from error
    if not found:
        raise HTTPException(404, 'There is no such activity. Reload the page.')
    return {'ok': True}


@app.post('/api/health/polar/activities/{activity_id}/remove')
def polar_activity_remove(activity_id: int) -> dict:
    """Remove an activity that no session has."""
    try:
        found = polar_store.remove_activity(activity_id)
    except polar_store.ActivityError as error:
        raise HTTPException(409, str(error)) from error
    if not found:
        raise HTTPException(404, 'There is no such activity. Reload the page.')
    return {'ok': True}


class SessionLabel(BaseModel):
    """The activity (None: none) or the note of a session, or both; a field that is not sent stays as it is."""

    activity_id: int | None = None
    note: str | None = Field(None, max_length=2000)


@app.post('/api/health/polar/sessions/{session_id}/label')
def polar_label(session_id: int, given: SessionLabel) -> dict:
    """Set the activity or the note of a session; only the fields sent change."""
    sent = given.model_fields_set
    if 'activity_id' in sent and not polar_store.set_activity(session_id, given.activity_id):
        raise HTTPException(404, 'There is no such Polar H10 session or activity. Reload the page.')
    if 'note' in sent and not polar_store.set_note(session_id, given.note or ''):
        raise HTTPException(404, 'There is no such Polar H10 session. Reload the page.')
    return {'ok': True}


# the values of the newest session of each test, for the result in its popup
TEST_VALUES = {polar_store.HRV_TEST: polar_hrv.values_of, polar_store.RECOVERY_TEST: polar_recovery.values_of,
               polar_store.STAIRS_TEST: polar_stairs.values_of}


@app.get('/api/health/polar/tests/{test}/last')
def polar_last_test(test: str) -> dict | None:
    """The newest session of a test (hrv, recovery or stairs), any day, with its values or the reason it has none; None without
    one."""
    if test not in TEST_VALUES:
        raise HTTPException(404, f'There is no test {test}. Reload the page.')
    found = polar_store.last_session_of_test(test)
    return None if found is None else TEST_VALUES[test](found['id']) | {'session_id': found['id'], 'start_at': found['start_at']}


class PolarMarker(BaseModel):
    """A marker for the running recording: effort done (the HR recovery test)."""

    label: str


def _polar_live() -> dict:
    """The recording of the logger now, for the card."""
    return polar_record.live(_health_now().tzinfo)


@app.get('/api/health/polar/live')
def polar_live() -> dict:
    """The recording with the logger now: its state, HR, battery, the last marker and the last error."""
    return _polar_live()


class RecordStart(BaseModel):
    """The activity of a recording started from the card (None: none), and the test time of an HRV test: whole minutes, 5
    to 60."""

    activity_id: int | None = None
    measure_s: int | None = Field(None, ge=300, le=3600, multiple_of=60)


@app.post('/api/health/polar/live/start')
def polar_live_start(given: RecordStart) -> dict:
    """Start a recording with the logger; its session gets the activity and the test time chosen."""
    try:
        polar_record.start(_health_now().tzinfo, given.activity_id, given.measure_s)
    except polar_record.RecordError as error:
        raise HTTPException(409, str(error)) from error
    return _polar_live()


@app.post('/api/health/polar/live/stop')
def polar_live_stop() -> dict:
    """Stop the recording; its session is imported when the logger has ended."""
    try:
        polar_record.stop(_health_now().tzinfo)
    except polar_record.RecordError as error:
        raise HTTPException(409, str(error)) from error
    return _polar_live()


@app.post('/api/health/polar/live/markers')
def polar_live_marker(given: PolarMarker) -> dict:
    """A marker in the running recording."""
    try:
        polar_record.mark(given.label)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except polar_record.RecordError as error:
        raise HTTPException(409, str(error)) from error
    return _polar_live()


if STATIC.exists():
    app.mount('/assets', StaticFiles(directory=STATIC / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """The single-page GUI for every non-API path."""
        file = (STATIC / path).resolve()
        inside = file.is_relative_to(STATIC.resolve())
        return FileResponse(file if path and inside and file.is_file() else STATIC / 'index.html')


def main() -> None:
    """Serve the app on localhost only."""
    logging.basicConfig(level=logging.INFO)
    health_supplements.convert()
    uvicorn.run(app, host='127.0.0.1', port=config().port)
