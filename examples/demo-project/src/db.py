"""Per-request sync connections - deliberately naive for the example."""

import os

import psycopg

DSN = os.environ.get("TICKETD_PG_URL", "postgresql://localhost/ticketd")


def _connect():
    return psycopg.connect(DSN)


def list_tickets() -> list[dict]:
    with _connect() as connection:
        rows = connection.execute("SELECT id, title, body FROM tickets ORDER BY id").fetchall()
    return [{"id": row[0], "title": row[1], "body": row[2]} for row in rows]


def fetch_ticket(ticket_id: int) -> dict | None:
    with _connect() as connection:
        row = connection.execute("SELECT id, title, body FROM tickets WHERE id = %s", (ticket_id,)).fetchone()
    return {"id": row[0], "title": row[1], "body": row[2]} if row else None
