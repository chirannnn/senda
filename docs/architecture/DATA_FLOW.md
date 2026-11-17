# Data and decision flows

These diagrams represent the current controller implementation and local
integration topology. They do not depict a hosted deployment.

## Event ingestion

```mermaid
sequenceDiagram
    participant Runtime
    participant Client as HTTP client
    participant API as Workflow API
    participant Store as PostgreSQL store
    Runtime->>Client: health, workflows, state
    Runtime->>Store: read checkpoint
    Runtime->>Client: list history/live chunks after checkpoint
    Client->>API: GET chunk descriptors
    Runtime->>Client: stream descriptor NDJSON
    Client->>API: GET chunk body
    Runtime->>Runtime: validate IDs, ordering, count and bounds
    Runtime->>Store: begin transaction
    Store->>Store: COPY to temp stage; merge; check replay conflicts
    Store->>Store: write checkpoint in same transaction
    Store-->>Runtime: commit result
```

## Analytics

```mermaid
flowchart LR
    E[(Committed raw events)] --> PAGE[Ordered bounded event page]
    PAGE --> PROJECT[Validate and derive one fact per event]
    PROJECT --> TX[Single fact-page transaction]
    TX --> REF[Workflow reference upsert]
    TX --> FACT[(KPI facts)]
    TX --> CURSOR[(Analytics cursor)]
    FACT --> QUERY[SQL KPI aggregation / history query]
    QUERY --> OUT[Separate pickup, engagement, quality and provenance]
```

Pickup is connected/dials; engagement is concluded/connected. Invalid,
incomplete, and unsupported facts remain classified and are excluded from
valid evidence as appropriate.

## Experiment evaluation

```mermaid
flowchart TD
    PLAN[Caller supplies fixed plan and assumptions] --> SNAP[Read persisted valid facts to fixed cutoff]
    SNAP --> CLUSTER[Exclude cross-arm people; cluster by person]
    CLUSTER --> EST[Deterministic bootstrap and Holm correction]
    EST --> GUARD[Independent primary and configured guardrail checks]
    GUARD --> DEC{Evidence and config checks pass?}
    DEC -->|No| AUDIT[Persist decision and reason; no proposal]
    DEC -->|Yes| PROPOSAL[Create evidence-backed proposal]
    PROPOSAL --> GOV[Existing approval lifecycle]
```

The runtime does not schedule experiment plans or assign traffic. Statistical
evaluation is deterministic code, not an LLM decision.

## Incident and approval lifecycle

```mermaid
flowchart TD
    QA[Observed population QA facts] --> GATE[Quality and evidence gates]
    GATE -->|Insufficient| RECORD[Persist reason and timeline]
    GATE -->|Sufficient and harmful| CREDIT[Check available credits]
    CREDIT --> SUGGEST[Request untrusted candidate]
    SUGGEST --> VALIDATE[Validate exact candidate]
    VALIDATE -->|Rejected or ambiguous| RECORD
    VALIDATE -->|Passed| SUBMIT[Persist proposal and outbox, then POST approval]
    SUBMIT --> HUMAN[Human approval decision]
    HUMAN -->|Reject / expire / lapse| RECORD
    HUMAN -->|Approve| STALE[Recheck current version and approved diff]
    STALE --> APPLY[Persist intent; apply exact approved proposal]
    APPLY --> MONITOR[Measure actual post-apply outcomes]
    MONITOR --> REMEDY[New governed proposal only if evidence justifies]
```

## Safe apply

```mermaid
sequenceDiagram
    participant G as Governance service
    participant DB as PostgreSQL
    participant API as Workflow API
    G->>API: GET approval and current config/version
    G->>G: match approved payload and base version
    G->>DB: persist apply outbox intent
    G->>API: POST apply with approval identity
    alt response proves applied
        API-->>G: applied result
        G->>DB: persist outcome and audit
    else response ambiguous
        G->>API: GET approval/config history
        alt history proves exact change
            G->>DB: persist reconciled result and audit
        else not provable
            G->>DB: mark reconciliation required
        end
    end
```

## Recovery

```mermaid
flowchart TD
    START[Process restart] --> DBREAD[Read durable checkpoints and unresolved outbox]
    DBREAD --> INGEST[Replay published chunks after checkpoint]
    INGEST --> TX{Chunk transaction committed?}
    TX -->|No| ROLLBACK[No chunk rows/checkpoint; replay full descriptor]
    TX -->|Yes| RETAIN[Committed rows/checkpoint retained]
    ROLLBACK --> CURSOR
    RETAIN --> CURSOR[Resume analytics from stored cursor]
    CURSOR --> REMOTE[Reconcile pending remote operation from documented reads]
    REMOTE --> PROVEN{Remote effect proven?}
    PROVEN -->|Yes| DONE[Record outcome and continue]
    PROVEN -->|No| HALT[Mark review required and stop affected operation]
```

## Local development topology

```mermaid
flowchart LR
    DEV[Developer terminal] -->|starts| CTRL[Controller CLI]
    DEV -->|starts locally| SIM[Bundled simulator harness]
    CTRL <-->|HTTP on local interface| SIM
    CTRL -->|SQLAlchemy async| PG[(Local PostgreSQL)]
```

There is no controller container, broker, worker fleet, metrics server, or
production deployment topology defined in this repository.
