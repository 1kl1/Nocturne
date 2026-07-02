# Nocturne

Nocturne MVP is a single-container FastAPI application that audits selected Notion pages, stores suggested edits in a Notion database named `Nocturne`, and applies only suggestions that the user explicitly marks as `승인`.

## What Is Implemented

- FastAPI web application with onboarding, home dashboard, run logs, and settings for page targets, email, and account connections.
- SQLite persistence for users, connections, scan targets, runs, proposal cache, Nocturne edit history, email verification, and audit logs.
- Encrypted storage for Notion tokens and email verification state.
- Notion OAuth, page/database expansion, child-page traversal, block text extraction, proposal inbox creation, proposal page writes, and approved-item application.
- OpenRouter adapter using the server-provided `OPENROUTER_API_KEY` and service default model from `OPENROUTER_DEFAULT_MODEL`.
- Web search adapter with `tavily`, `brave`, `serper`, or `none`.
- Email notifications through SMTP. `console` remains useful for local development.
- Agent tool/action logging via `agent_tool_call` audit events.
- Internal scheduler loop for nightly runs in the user's timezone.
- Dockerfile and Coolify-friendly volume/env setup.

## Local Run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open `http://localhost:8000`.

For local form testing without live OpenRouter validation, set:

```bash
NOCTURNE_SKIP_EXTERNAL_VALIDATION=true
EMAIL_PROVIDER=console
WEB_SEARCH_PROVIDER=none
```

## Required Production Environment

```bash
APP_URL=
DATABASE_URL=sqlite:////app/data/nocturne.sqlite3
NOCTURNE_ENCRYPTION_KEY=
NOTION_CLIENT_ID=
NOTION_CLIENT_SECRET=
NOTION_REDIRECT_URI=
OPENROUTER_DEFAULT_MODEL=
OPENROUTER_API_KEY=
EMAIL_PROVIDER=
EMAIL_FROM=
SMTP_HOST=
SMTP_PORT=
SMTP_USERNAME=
SMTP_PASSWORD=
WEB_SEARCH_PROVIDER=
WEB_SEARCH_API_KEY=
```

OpenRouter is no longer configured by each user. Set `OPENROUTER_API_KEY` as a server environment variable, and keep it out of logs and screenshots.

SMTP is handled by the app through Python's `smtplib`, but the project cannot send real email by itself without a reachable SMTP service. If SMTP fails in deployment, check these first:

- The host allows outbound SMTP to your provider and port, usually `587` with STARTTLS.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `EMAIL_FROM` match your provider's settings.
- The provider permits the `EMAIL_FROM` sender. Some providers require domain verification, app passwords, or SPF/DKIM records.
- For local development, use `EMAIL_PROVIDER=console` to log verification codes instead of sending email.

## Docker

```bash
docker build -t nocturne .
docker run --rm -p 8000:8000 --env-file .env -v nocturne-data:/app/data nocturne
```

## Coolify

1. Deploy this repository as a Docker application.
2. Attach a persistent volume at `/app/data`.
3. Add the production environment variables above as Coolify secrets.
4. Set the public URL as `APP_URL` and `NOTION_REDIRECT_URI=https://your-domain/auth/notion/callback`.
5. Keep a single container; web app, scheduler, and worker run in the same process.

## Approval Boundary

The agent can collect pages, analyze blocks, write suggestions, send notifications, and read approved suggestions. It only writes back to original Notion content when a proposal in `Nocturne` has status `승인`; `대기`, `거절`, and `보류` are never applied.
