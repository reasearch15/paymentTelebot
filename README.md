# Payment Ledger

Local project foundation for `payments.youplatform.org`.

## Stack

- Frontend: Next.js, TypeScript, App Router
- Backend: FastAPI, Python 3.12, SQLAlchemy async, Alembic
- Database: PostgreSQL
- Authentication: one admin account, no signup, no staff roles

## Windows Local Development

### 1. Start PostgreSQL

From the project root:

```powershell
docker compose up -d
```

### 2. Configure Backend

```powershell
cd backend
Copy-Item .env.example .env
```

Edit `backend\.env` and set a long random `SECRET_KEY`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD`.

Create and activate a virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Run migrations and create the admin account:

```powershell
alembic upgrade head
python -m app.cli.create_admin
python -m app.cli.seed_providers
```

Start the backend:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the Gmail listener in another PowerShell window after the backend environment is configured:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.workers.gmail_listener
```

Health check:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

### 3. Configure Frontend

Open a second PowerShell window:

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

Visit `http://localhost:3000/login`.

## Backend Validation

```powershell
cd backend
python -m compileall app alembic
python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; script = ScriptDirectory.from_config(Config('alembic.ini')); print(script.get_current_head())"
```

## Frontend Validation

```powershell
cd frontend
npm run typecheck
npm run build
```

## Notes

This foundation intentionally does not include payment email parsers, Telegram bot integration, ledger calculations, settlement actions, or production deployment.

The Gmail listener captures raw emails only. It does not create transactions, parse payments, or notify Telegram.
