"""In-memory entity dataclasses for the PoC's seed data.

Scaffold only, no implementation yet. Read-heavy seed data (returns,
predictions, audit log) is the one component that doesn't need Vercel
KV — see 02_TECHNICAL_DESIGN.md §1 for why this is the exception, not
the default. NOTE: the concrete entity model this module should
implement is declared in 02_TECHNICAL_DESIGN.md §3 as "unchanged from
the finalized version — see turbotax_entity_model.mermaid", which is
not present in this repo. See the Open Question logged in
docs/spec/06_SCOPE.md before fields are defined here.
"""
