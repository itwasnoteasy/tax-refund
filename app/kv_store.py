"""Vercel KV wrapper (get/set/ttl) — not a plain Python dict.

Scaffold only, no implementation yet. This module is the one place the
cache, the circuit breaker's state, and the mock IRS mode toggle are
read/written, because those three specifically must survive across
what may be separate serverless invocations (see CLAUDE.md's Vercel
section and 02_TECHNICAL_DESIGN.md §1). Implementation is test-first
per CLAUDE.md's development-loop rule — tests land before the get/set/
ttl interface here.
"""
