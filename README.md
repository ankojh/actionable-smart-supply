# Actionable Smart Supply

Minimal React (Vite) + FastAPI scaffold wired to Snowflake. Product scope is TBD; this just proves the connection works.

```
backend/   FastAPI app, Snowflake connector, config from env vars
frontend/  React page that calls the backend and shows the Snowflake result
sample_data/  Small synthetic planning scenarios, generator, validation, and test answers
```

## Development sample data

See [sample_data/README.md](sample_data/README.md) for five reproducible supply-chain
scenarios, data definitions, simulated supplier documents, and expected outcomes.
The generated JSON is ready for local development; no Snowflake data is loaded.

```bash
python3 -m sample_data.generate
python3 -m sample_data.validate
python3 -m unittest discover -s sample_data/tests -v
```

## Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set SNOWFLAKE_USER and SNOWFLAKE_TOKEN; .env is gitignored
uvicorn app.main:app --reload --port 8000
```

Endpoints:

- `GET /api/health` – API liveness, no Snowflake call
- `GET /api/snowflake/health` – runs `SELECT CURRENT_VERSION()` (plus current account/user/role/warehouse/db/schema); returns 503 with the error message if the connection fails

### Auth options (`SNOWFLAKE_AUTH`)

| value | needs | notes |
|---|---|---|
| `password` | `SNOWFLAKE_PASSWORD` | simplest; fails if the user has MFA enforced |
| `keypair` | `SNOWFLAKE_PRIVATE_KEY_PATH` (+ passphrase if encrypted) | recommended for services |
| `pat` | `SNOWFLAKE_TOKEN` | programmatic access token from Snowsight |
| `externalbrowser` | nothing extra | SSO in a browser; local dev only |

Account identifier is `<org>-<account>`, e.g. `sfsehol-anthropic_snowflake_hackathon_fevwgp` for `app.snowflake.com/sfsehol/anthropic_snowflake_hackathon_fevwgp`.

## Frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173, proxies /api to :8000
```
