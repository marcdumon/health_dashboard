# health_dashboard

A personal health dashboard that runs on your own PC, in the browser. All data stays in the project folder.

| Part | What it shows | Where the data comes from |
|---|---|---|
| Today | supplements (a dated plan and each amount taken), angina score, ketones, tape measurements | you type them in |
| Withings | blood pressure, pulse, weight and body composition (also per arm, leg and trunk) | the Withings API, on Fetch now |
| Polar H10 | sessions, HR zones, HRV test, HR recovery test, stairs test, training load, net energy expenditure | the Polar H10 logger on the PC and the Polar Sensor Logger app on the phone, on Import |
| Sleep | sleep times, stages and cycles | the Sleep as Android backup in Dropbox, on Import sleep |
| Fitbit | HR, HRV, SpO2, breathing rate, skin temperature, sleep, activity and exercises | the Google Health API, on Download Fitbit |
| History | a chart of every value by day, the large view of each chart and the relation of two values | all of the above |

A page read never downloads or changes anything: only a button does.

## Run it

```bash
./run_health_dashboard.sh          # or add --no-browser
```

It installs missing packages, rebuilds the GUI when its source changed, starts the app, waits until it answers and
opens http://127.0.0.1:8766. If the app is already running, it only opens the browser. Ctrl+C stops it; the app's output
goes to `data/server.log`.

## Setup

The Google code is in the library [google_api](https://github.com/marcdumon/google_api), linked from the folder next to
this one:

```bash
git clone https://github.com/marcdumon/google_api ../google_api
uv sync
cp health_dashboard.example.toml health_dashboard.toml   # your folders (not in git)
cp .env.example .env                   # the Withings app codes and the phone FTP password
cp person.example.toml person.toml     # your birth date and sex (not in git)
cd frontend && npm install && npm run build && cd ..
uv run health-dashboard                # http://127.0.0.1:8766
```

- `health_dashboard.toml`: the port, the time zone, the Polar folders, the Sleep as Android backup and the first Fitbit day.
  `run_health_dashboard.sh` makes it from `health_dashboard.example.toml` when it is missing.
- Withings: make an app at developer.withings.com, put its codes in `.env`, then press Connect Withings.
- Fitbit: the Google Health API needs an OAuth client of type Desktop at `secrets/client_secret.json`; Log in to Google
  Health on the Fitbit card keeps the token in `secrets/google_health_token.json`. The steps are in
  [docs/google-health-setup.md](docs/google-health-setup.md).
- `data/`, `secrets/`, `.env`, `health_dashboard.toml` and `person.toml` are not in git.

## Tests

```bash
uv run pytest      # no network: fakes stand in for Withings, Google Health, the Polar logger and the phone
```
