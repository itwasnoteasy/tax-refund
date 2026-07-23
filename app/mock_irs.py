"""Mock IRS Integration Service.

Scaffold only, no implementation yet. Will support the 8 modes from
02_TECHNICAL_DESIGN.md §2: NORMAL, FAILING, FLAPPING, SLOW, TIMEOUT,
MALFORMED_RESPONSE, UNKNOWN_STATUS_CODE, RATE_LIMITED. Mode is read
from/written to Vercel KV (see kv_store.py), not held in memory, since
the demo control panel and a subsequent status check may run in
different serverless instances.
"""
