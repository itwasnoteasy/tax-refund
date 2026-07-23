"""FastAPI application entry point — Vercel's expected serverless location.

Scaffold only. Routers for the refund-status core (refund_status.py),
notifications (notifications.py), the demo-control endpoints
(/demo/set-irs-mode), and the Tax Help Assistant (help_assistant/) are
wired in during their respective build phases, not here — see
docs/spec/03_API_CONTRACT.yaml for the full route surface this app will
eventually expose.
"""
from fastapi import FastAPI

app = FastAPI(
    title="TurboTax Refund Status PoC API",
    version="0.1.0-poc",
)
