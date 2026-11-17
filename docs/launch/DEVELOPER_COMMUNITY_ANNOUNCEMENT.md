# Developer community announcement draft

I’m working on Senda, a prototype for improving outbound workflows from
observed evidence rather than isolated aggregate rates.

The current code is a Python async controller with a PostgreSQL store. It
validates and ingests ordered event chunks, derives separate pickup and
engagement facts, and uses durable checkpoints and an outbox to recover around
process crashes and ambiguous HTTP writes. Statistical comparison is
deterministic and caller-planned; prompt changes pass through validation and
human approval.

The project is not a hosted service and does not include production auth,
tenant isolation, or a user interface. I’d value technical feedback on
experiment assignment assumptions, recovery boundaries, and how to measure
database write paths without overclaiming fixture results.
