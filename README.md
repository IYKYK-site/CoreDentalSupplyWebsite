# Core AI Receptionist — Sprint 0 POC

The narrow proof of concept:

1. Patient calls one Twilio number.
2. Twilio opens a bidirectional Media Stream to this service.
3. The service bridges the call to OpenAI Realtime.
4. The AI answers office questions using `office.yaml`.
5. The AI can:
   - check availability,
   - create an appointment,
   - find an appointment,
   - reschedule an appointment,
   - cancel an appointment.
6. Scheduling uses Google Calendar first.
7. Dentrix replaces the scheduler later without changing the AI tool surface.
8. When the AI cannot help, it gives the configured fallback number.

## Deliberately NOT included

No admin UI, no multi-tenancy, no CRM, no billing, no patient portal, no analytics dashboard.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Configure these required values in `.env` before starting the service:

- `OPENAI_API_KEY`
- `PUBLIC_URL`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`

SMS is enabled in `office.yaml`, so also configure either
`TWILIO_MESSAGING_SERVICE_SID` or `TWILIO_PHONE_NUMBER`. The service validates
this environment at startup and reports missing variable names without
printing their values.

Put your Google OAuth Desktop App credentials in `client_secret.json`, then run:

```bash
python scripts/google_auth.py
```

This creates `token.json`.

Run the app:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Expose it during development:

```bash
ngrok http 8080
```

Set `PUBLIC_URL` in `.env` to the HTTPS ngrok URL and restart.

In Twilio, set the phone number's **A call comes in** webhook to:

```text
POST https://YOUR_HOST/incoming-call
```

## Smoke tests without Twilio

Health:

```bash
curl http://localhost:8080/health
```

Configuration:

```bash
curl http://localhost:8080/debug/config
```

Google Calendar availability:

```bash
curl "http://localhost:8080/debug/availability?date=2026-08-18&provider_id=dr_novoa"
```

## Important

This is a POC, not a production HIPAA deployment. Do not put real PHI into logs or test calls until the production security/compliance work is done.
