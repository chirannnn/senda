# Customer problems (hypotheses)

The repository contains no customer interviews, account data, or market
research. These are problem hypotheses suggested by the product direction and
must be validated with outbound operations teams.

- Performance data may be split across execution, call-quality, and
  configuration systems, making it hard to explain the cause of a change.
- An overall success rate can conceal distinct pickup and engagement failures.
- Teams may change workflow prompts or routing settings without a fixed
  evidence window, documented guardrail, or repeatable approval trail.
- Interrupted ingestion and unclear remote-write outcomes can weaken trust in
  operational metrics and configuration history.
- Human reviewers may lack concise evidence that ties a proposed change to
  observed outcomes and the current configuration version.

Validation should establish how frequently these problems occur, current
workarounds, risk and cost of errors, existing budget ownership, and whether a
shared operations system improves decision quality.
