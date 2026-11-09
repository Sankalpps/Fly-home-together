# Fly Home Together – Quick Start Guide

## Overview
This repository implements a full‑stack flight‑booking platform with a **single login page** that auto‑detects the user's role (traveler, desk staff, or admin) and shows the appropriate UI. It includes:
- Real‑time simulated domestic flight data for India.
- Desk portal for flight‑operations staff.
- Hidden admin console (accessible only to admins).
- Notification system (console‑based simulation).
- All core features are unit‑tested (82 pytest tests) and a live‑verification script confirms the running server.

## Prerequisites
- **OS:** Windows (tested on Windows 10/11).
- **Python:** 3.11+ (the virtual environment in the repo already contains the needed packages).
- **Dependencies:** Install with `pip install -r requirements.txt`. No external paid services are required; flight data is simulated unless you set an `AERODATABOX_API_KEY`.

## Running the Server
```powershell
# Activate the virtual environment if you have one (optional)
# .\venv\Scripts\activate

# Start the FastAPI app (auto‑reload for development)
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
The server will output a start‑up banner, e.g.:
```
[FlightGateway] SimulatedFlightGateway (set AERODATABOX_API_KEY for real data)
[FHT] Fly Home Together v2 -- platform ready
   DB: fht.db
   Flight data: Simulated
   Email: Simulated (console)
   Telegram: Simulated (console)
```
Open a browser at **http://127.0.0.1:8000**.

## Demo Credentials
| Email | Password | Role | Department |
|-------|----------|------|------------|
| `user1@gmail.com` | `password123` | **TRAVELER** | – |
| `ragu@atc.gov` | `password123` | **DESK_STAFF** | `FLIGHT_OPERATIONS` |
| `guru@atc.gov` | `password123` | **PLATFORM_ADMIN** | `ATC_HEADQUARTERS` |

Logging in with any of the above will automatically direct you to the appropriate UI (traveler UI, desk portal, or hidden admin console).

## Live Verification
A script `tests/verify_live.py` starts the server on port 8001, probes the UI, APIs, role detection, flight data, desk portal, and admin console, then prints a success message. Running it should end with:
```
>>> ALL SYSTEM & USER CHECKS PASSED SUCCESSFULLY! <<<
```
You can run it (outside the sandbox) with:
```powershell
python tests\verify_live.py
```
It will launch a temporary server, perform the checks, and shut down.

## Running Tests
```powershell
pytest -q
```
All 82 tests should pass.

## Deploying on Render

This repository includes a [`render.yaml`](./render.yaml) Blueprint configuration.

1. Push the repository to GitHub.
2. In Render, choose **New → Blueprint** and select this repository.
3. Set `SECRET_KEY` to a long random value in the Render environment settings.
4. Set `AERODATABOX_API_KEY` to your RapidAPI key to enable live flight data.
5. Deploy. Render uses the injected `PORT` and checks `/health` automatically.

The default SQLite database uses Render's ephemeral filesystem. Use a Render
persistent disk and set `DB_PATH` to its mounted path if data must survive
redeploys.

## Extending / Real Data
To use real flight data, obtain an API key from **AeroDataBox** and set the environment variable:
```powershell
$env:AERODATABOX_API_KEY = "your_key_here"
```
Then restart the server; the simulated gateway will switch to the live gateway automatically.

---
Feel free to explore the codebase, add new features, or replace the simulated gateway with a real GDS integration when ready.
