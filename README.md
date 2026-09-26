# ServeSense

[![Playwright Screenshots](https://github.com/iamrichmack111/servesense/actions/workflows/playwright-screenshots.yml/badge.svg)](https://github.com/iamrichmack111/servesense/actions/workflows/playwright-screenshots.yml)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)


ServeSense is a Dockerized Flask restaurant operations platform with staff/pay-rate management, sales tracking, availability/PTO, AI-assisted staffing recommendations, drag-and-drop scheduling, reports, multiple administrator accounts, CSV exports, and full database backups.

## Start

```bash
cp .env.example .env
docker compose up --build -d
```

Open `http://localhost:8003`.

Default demo credentials from `.env.example`:

- Username: `owner`
- Password: `ServeSenseDemo123!`

Change these before public deployment.

## Demo workflow

1. Sign in.
2. Click **Load demo data** on the dashboard.
3. Open **Predict** and generate a recommended lineup.
4. Save it into the schedule builder.
5. Drag employees between the roster and position lanes.
6. Publish the schedule.
7. Review Reports and export data from Settings.

## Included modules

- Dashboard KPIs and leaderboards
- Staff profiles, roles, pay rates, certifications, and status
- Sales, hours, covers, tips, lateness, and performance metrics
- Availability, preferences, PTO, and exclusions
- Explainable prediction scoring
- Drag-and-drop schedule builder with autosave
- Published/draft schedules
- Reservations and party-size tracking
- Payroll and seven-day labor estimates
- Labor percentage and employee performance reports
- Owner/admin/manager accounts
- Restaurant settings
- Staff, sales, availability, schedule, and admin CSV exports
- Complete ZIP backup with SQLite database

## Screenshots

### Staff and pay rates
![Staff and pay rates](docs/screenshots/staff.png)

### Sales and shift performance
![Sales and shift performance](docs/screenshots/sales.jpeg)

### Availability and PTO
![Availability and PTO](docs/screenshots/availability.png)

### AI-assisted staffing prediction
![AI-assisted staffing prediction](docs/screenshots/prediction.png)

### Schedules
![Schedules](docs/screenshots/schedules.png)

### Reservations
![Reservations](docs/screenshots/reservations.png)

### Payroll and labor
![Payroll and labor](docs/screenshots/payroll.png)

### Reports and performance
![Reports and performance](docs/screenshots/reports.png)

### Restaurant settings
![Restaurant settings](docs/screenshots/settings.png)

### Administrator users
![Administrator users](docs/screenshots/admin-users.png)

## GitHub Container Package

ServeSense is published as a Docker image through GitHub Container Registry.

Pull the latest release:

```bash
docker pull ghcr.io/iamrichmack111/servesense:latest
```

Run it:

```bash
docker run -d \
  --name servesense \
  --restart unless-stopped \
  --env-file .env \
  -p 8003:8000 \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/iamrichmack111/servesense:latest
```

Versioned releases can also be pulled directly:

```bash
docker pull ghcr.io/iamrichmack111/servesense:v0.2.0
```

The container image is automatically built for both AMD64 and ARM64 by GitHub Actions.


## Automated UI screenshots

Playwright captures the main authenticated ServeSense screens in Chromium. Run locally with:

```bash
pip install playwright
python -m playwright install chromium
docker compose up --build -d
python tests/playwright_screenshots.py
```

Screenshots are written to `docs/screenshots/playwright/` and uploaded as a GitHub Actions artifact on CI runs.
