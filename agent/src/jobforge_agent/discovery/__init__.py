"""Continuous incremental discovery (ARCHITECTURE 3.3).

Adapters and the RFC 9309 robots handling are ported from ../job-scanner/ats_scanner at
commit edd1528 (SPEC Q3). Differences from the scanner are deliberate and documented where
they occur: conditional requests, no in-request retries (failures reschedule with backoff),
per-host limits and backoff that persist in the database, and change detection instead of a
nightly upsert.
"""
