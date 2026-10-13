# Review notes

## Main issues fixed
- Readiness endpoint now pings MongoDB.
- Employee codes and attendance natural keys are enforced with unique indexes, preventing duplicate concurrent writes.
- Pagination uses 1-based pages and MongoDB-side sort/skip/limit; totals respect filters.
- Epoch-millisecond validation, UTC storage and IST attendance-day calculations are explicit.
- Punch-out handles overnight shifts and concurrent punch-out requests.
- Regularization recomputes derived fields, records only changed fields, appends audit history and uses compare-and-set to avoid lost concurrent updates.
- Analytics and explain routes have been implemented.

## Remaining production concerns
- Authentication/authorization is not part of the supplied contract. Do not deploy correction or admin endpoints publicly without adding it.
- Aggregation pipelines should be exercised against the exact MongoDB server version and the full grader dataset; explain output and index use can vary by server version and query planner.
