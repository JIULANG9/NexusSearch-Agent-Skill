# Ticketd (demo project)

A deliberately small FastAPI service used as input for the NexusSearch demo run.
It manages tickets with a Postgres backend and a hand-rolled LLM summariser.

## Layout
- `src/app.py` — HTTP layer, `/tickets` CRUD plus `/tickets/{id}/summary`
- `src/summariser.py` — direct OpenAI-shaped HTTP call, no retry, no caching
- `src/db.py` — sync `psycopg` connection per request
- `docs/DECISIONS.md` — why the current design was chosen

## Run
```bash
uvicorn src.app:app --reload
```

## Known pain points
- the summariser blocks a request thread for up to 30 s
- no streaming, so long tickets time out behind the gateway
- no tests for the LLM path
