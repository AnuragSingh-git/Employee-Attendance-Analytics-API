# Implementation decisions

- `emp_code` is the business key; MongoDB `_id` is never exposed as an API identifier.
- Instants are stored as UTC BSON datetimes and serialized as epoch milliseconds. Calendar dates and shift times remain strings.
- Unique indexes are used instead of check-then-insert for race-sensitive natural keys.
- MongoDB performs filtering, sorting, pagination and the primary analytics aggregations. Department trend uses `$densify` and `$setWindowFields` for date gaps and its rolling average.
- Manual corrections append history and use a compare-and-set update to prevent silently overwriting another correction.
- No approval or authentication layer was invented because the supplied API contract does not define one; `regularized_by` is audit metadata, not proof of identity.
