# Senda product vision

**Make every interaction count.**

Senda is an intelligent operations platform for teams responsible for outbound
engagement. It is intended to connect operational data, outcome measurement,
controlled evaluation, governed recommendations, and feedback into one
defensible operating loop. The first product area is outbound voice workflows.

The operating problem is not simply placing calls or displaying dashboards.
Teams need to understand where a workflow loses people, distinguish a change
in pickup from a change in engagement, determine whether a proposed change is
supported by evidence, and retain a clear record of who approved it and what
happened afterward.

Senda's product direction is to help operations teams move from periodic,
manual configuration reviews toward evidence-led decisions with explicit
human control over sensitive changes. The direction is not a claim that every
part of this loop is automated or production-ready today.

## Product thesis

Operational improvement is safer when observation, measurement, evaluation,
authorization, and outcome review share the same provenance and history. A
recommendation should be explainable from observed records; an approved change
should be tied to the exact configuration that was reviewed; and an uncertain
remote result should be reconciled or escalated instead of guessed.

## Current evidence boundary

The repository contains a working controller prototype that ingests events
from an HTTP contract, stores them in PostgreSQL, derives KPI facts, evaluates
population evidence, and routes sensitive prompt changes through approval.
It includes a local simulator for development and contract testing. It is not
a hosted SaaS product, a voice agent, or a customer deployment. See the
[feature catalog](FEATURE_CATALOG.md) and [product boundaries](PRODUCT_BOUNDARIES.md)
for implemented, partial, and future capabilities.
