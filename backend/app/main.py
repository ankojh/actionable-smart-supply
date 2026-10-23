from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import AppSettings
from .snowflake_client import query

app = FastAPI(title="Actionable Smart Supply API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in AppSettings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    """Liveness of the API itself; does not touch Snowflake."""
    return {"status": "ok"}


@app.get("/api/snowflake/health")
def snowflake_health():
    """Runs a trivial query to prove the Snowflake wiring works."""
    try:
        row = query(
            """
            SELECT CURRENT_VERSION() AS version,
                   CURRENT_ACCOUNT() AS account,
                   CURRENT_USER() AS "user",
                   CURRENT_ROLE() AS role,
                   CURRENT_WAREHOUSE() AS warehouse,
                   CURRENT_DATABASE() AS database,
                   CURRENT_SCHEMA() AS schema
            """
        )[0]
        return {"status": "ok", **{k.lower(): v for k, v in row.items()}}
    except Exception as e:  # surface the reason so wiring problems are easy to debug
        return JSONResponse(status_code=503, content={"status": "error", "error": str(e)})
