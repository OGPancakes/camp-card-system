# Camp Card System

This is a small local web app for a camp store or snack bar.

Each camper can have:

- a name
- an age
- an RFID card number
- a weekly balance
- transaction history for charges and added funds
- stock market trading tied to each camper's wallet balance

## What it does

- requires staff login before using the system
- includes a main admin account: `johhny` / `admin`
- lets only the admin create leader and under leader logins
- records which staff account performed each action
- create campers and assign an RFID card number
- remove campers from active use without losing old records
- charge a camper for snacks or other camp items
- add money back to a camper account
- transfer money from one camper card to another
- replace a lost card by camper name and a new RFID card number
- look up a camper by RFID card number
- reset every camper balance for the start of a new week to `147000`
- run a camp stock market with `PIA`, `OIL`, `GOLD`, and `TECH`
- let staff save simple hype-of-the-day answers so the market can react
- let campers buy and sell shares by tapping or entering their RFID card number
- refresh market prices with big swings every 12 hours
- show a live market with smaller in-between moves and mini history graphs
- show action-specific confirmation animations for common actions

## Run it

Start the server:

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:8123
```

The app stores data in:

```text
camp_cards.db
```

## Use it on phones today

If everyone is on the same Wi-Fi, run:

```bash
HOST=0.0.0.0 python3 app.py
```

Then open this from phones on the same network:

```text
http://192.168.1.180:8123
```

If that address stops working, get the current Mac Wi-Fi address with:

```bash
ipconfig getifaddr en0
```

## Deploy it to a public link

The app is ready for a Python web service host such as Render, Railway, Fly.io, or a small VPS.

For Render:

- Push this folder to a GitHub repo.
- Create a new Render Web Service from that repo.
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:application --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- Health check path: `/health`
- Environment variables:
  - `ADMIN_USERNAME`: your real admin username
  - `ADMIN_PASSWORD`: your real admin password
  - `DATA_DIR`: persistent storage folder, for example `/var/data`
- Add a persistent disk mounted at the same path as `DATA_DIR` if you want balances, uploaded photos, sessions, and promos to survive restarts.

Important: `camp_cards.db` and `uploads/` are ignored by git on purpose so real camper data and photos do not get pushed publicly. If you want to move the current local data to the server, use a host with persistent storage and upload `camp_cards.db` plus `uploads/` into that storage folder.

## Production files

- `requirements.txt` installs Gunicorn.
- `Procfile` gives hosts a default web command.
- `runtime.txt` pins Python.
- `/health` returns a small JSON status response for host health checks.

## Good next upgrades

- connect a real RFID scanner so card numbers fill in automatically
- add scheduled background refresh so the market updates itself every 12 hours without a button click
- export camper balances and transactions to CSV
- add parent deposits or spending limits by item type
- add a dedicated camper portfolio page with profit/loss history
- lock down default credentials before any real deployment
