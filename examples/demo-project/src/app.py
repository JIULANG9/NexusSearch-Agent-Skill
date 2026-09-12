"""Minimal ticket HTTP layer for the NexusSearch example workspace."""

from fastapi import FastAPI

from .db import fetch_ticket, list_tickets
from .summariser import summarise

app = FastAPI(title="Ticketd")


@app.get("/tickets")
def get_tickets() -> list[dict]:
    return list_tickets()


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int) -> dict:
    ticket = fetch_ticket(ticket_id)
    if ticket is None:
        return {"error": "not found"}
    return ticket


@app.get("/tickets/{ticket_id}/summary")
def get_summary(ticket_id: int) -> dict:
    ticket = fetch_ticket(ticket_id)
    if ticket is None:
        return {"error": "not found"}
    return {"id": ticket_id, "summary": summarise(ticket["body"])}
