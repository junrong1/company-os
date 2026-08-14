"""The report service: a read-side fold over the log.

It tails the log and never writes to it. In Phase 1 every metric number in the
report is authored tuning, so the provenance trail — which decision, resolved
which way, by which path — is the report's most defensible content.
"""
