# Vercel frontend and Render backend

The repository-root `vercel.json` builds `frontend/` and forwards `/api/*` to
`https://sumo-api-532e.onrender.com`. Deploy from the repository root. The browser
uses the same origin, so no additional CORS setting is needed for this setup.
If replacing the Render service, update the rewrite destination.

`render.yaml` describes the free demo backend. The existing Render service is
`sumo-api` (`srv-dbdt7jsipgvc73a26ki0`), tracking `dev/implementation-v1` with:

- Root directory: repository root (`.`); the API needs the sibling `data/` directory
- Python: `3.12.8`
- Build: `pip install -r backend/requirements.txt`
- Start: `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Health check: `/api/health`
- `PROJECT_STORE=/tmp/sumo/project.sqlite3`

The free backend sleeps when inactive, so the first request can be slow. Its
SQLite data is temporary and can disappear on restart or deployment. For durable
cases, use a paid Render instance and persistent disk, and point `PROJECT_STORE`
inside the disk mount. Keep one worker because workflow coordination uses local
process locks.

The active NorthStar backend defaults to live Snowflake. Configure the existing
Snowflake account credentials privately in Render before deploying this version;
credentials are not included in the repository or uploaded by this change.
NorthStar case state and findings persist in Snowflake. SQLite remains relevant
only to the historical workflow and explicit local test provider.

An optional frontend `VITE_API_URL` can point directly at another backend origin
(without `/api`). That bypasses the rewrite and requires `CORS_ORIGINS` on the
backend to include the frontend's exact origin. Leave it unset for this deployment.
