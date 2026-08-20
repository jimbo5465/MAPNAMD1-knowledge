# WelderBot

WelderBot is a Telegram bot for managing **Welder Qualification Tests (WQT)**
based on **ASME Section IX**.

The project is designed with a modular architecture so that each feature can
be developed, tested, and maintained independently.

---

## Main Features

- Welder Management
- WQT Registration (ASME Section IX)
- Excel Certificate Export (official WPQ form)
- Hierarchical Access Control (3 levels)
- Project Management
- Contractor Management (project ⇆ contractor relationship lifecycle)
- Qualification History
- Authentication & Authorization
- SQLite Database

---

## Access Control

Three access levels, each scoped to a defined boundary. Higher levels
inherit the permissions of lower levels; the global admin
(`config.ADMIN_IDS`) always has level 1 globally.

| Level | Role | Scope |
|---|---|---|
| 1 | Global project manager | Global — create/edit/delete any project |
| 2 | Contractor manager | Scoped to one project — add/edit/delete contractors within it |
| 3 | Operator | Scoped to one contractor — register tests only |

Access is granted from the main menu → "👥 مدیریت کاربران". A user must
have sent `/start` at least once to appear in the selection list (table
`pending_users`).

Project–contractor relationship is many-to-many (a project can have
multiple contractors), tracked in `project_contractors`.

Project termination is soft and reversible: welder and qualification
records stay intact, only new activity registration is blocked.

Contractor management supports add / re-link / label / terminate per
project. Termination requested by level 2 requires level-1 approval
(via immediate Telegram notification) before taking effect.

Access-level enforcement applies throughout the WQT registration flow:
level 2 sees only their own projects, level 3 sees only their assigned
contractor and skips the corresponding selection steps automatically.

---

## Project Structure
welderbot/
├── db/
├── engine/
├── forms/
├── handlers/
├── utils/
├── media/
├── logs/
├── config.py
├── main.py
├── requirements.txt
└── welderbot.service

---

## Technology Stack

- Python 3
- python-telegram-bot
- SQLite
- OpenPyXL
- Pillow
- jdatetime

---

## Installation

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it:

Linux
```bash
source .venv/bin/activate
```

Windows
```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Environment Variables

The project reads all sensitive values from environment variables.

Required:
```text
BOT_TOKEN
ADMIN_IDS
```

Optional:
```text
WELDERBOT_DEBUG
```

---

## Run

```bash
python main.py
```

---

## Dependencies

- python-telegram-bot
- openpyxl
- Pillow
- jdatetime

See `requirements.txt` for exact versions.

---

## Architecture

Documented in [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Internal API

Stable internal APIs are documented in [`CONTRACTS.md`](./CONTRACTS.md).

---

## Deployment

The project includes a ready-to-use systemd service: `welderbot.service`.
See `RUN.md` for deployment instructions.

---

## Known Open Item

Excel WPQ export: core logic and bot wiring are complete and tested.
Two items remain — see `ARCHITECTURE.md` and `CONTRACTS.md` for details:
- A few secondary fields (progression, shielding gas, filler spec, etc.)
  are not yet written to `extra_data` when a qualification is saved.
- Plate sample thickness is not yet stored in the database.

---

## License

Private Project

Copyright © [mohsen]

All Rights Reserved.
