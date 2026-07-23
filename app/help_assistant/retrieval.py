"""Hybrid dense+sparse retrieval, RRF fusion, cross-encoder rerank.

Scaffold only, no implementation yet. Dense retrieval uses a hosted
embedding API, not a locally-loaded model, to avoid Vercel cold-start
risk (02_TECHNICAL_DESIGN.md §6). The cross-encoder-vs-LLM-rerank
choice is deferred to that phase, per the cold-start trade-off
documented there — do not add sentence-transformers or another heavy
local model dependency until that decision is made.
"""
