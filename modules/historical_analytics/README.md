# Historical analytics foundation (Phase 2A.2a)

This module is the **disabled-by-default** recreatable analytics
foundation. Phase 2A.2a owns only:

- the feature flag `enable_historical_analytics` (default `false`)
- a gated read-only lookup of the persistent historical facts bucket
- a disposable Athena query-results S3 bucket

It does **not** create Glue, Athena workgroups, crawlers, dashboards,
runtime query IAM roles, or named query APIs.

## Ownership boundary

Canonical historical facts are owned exclusively by
`envs/dev-historical-data` / `modules/historical_facts_data`.

- Persistent facts bucket: `force_destroy = false`
- Disposable Athena query-results bucket: `force_destroy = true`

| Class | Root | Persistence | `force_destroy` |
| --- | --- | --- | --- |
| Historical facts bucket | `envs/dev-historical-data` | persistent | `false` |
| Athena query-results bucket | `envs/dev` (this module) | disposable derived data | `true` |

The persistent bucket name is

`${name_prefix}-historical-facts-${account_id}-${aws_region}`

This module never declares `aws_s3_bucket` for that name. When
`enable_historical_analytics=true`, it looks the bucket up with gated
`data.aws_s3_bucket` using the same formula. When the flag is `false`,
that data source is not evaluated, so `terraform validate` does not
require the data-plane bucket to exist.

The results bucket name is

`${name_prefix}-historical-athena-results-${account_id}-${aws_region}`

Results are derived analytics artifacts (default 3-day current-object
expiry). They are not canonical historical truth, are not part of
coverage evaluation, and must never be written under the canonical
`dataset=` / `metadata/` / `errors/` prefixes.

Tags use `Component = historical-analytics` and
`DataType = historical-analytics-derived`. This bucket is **not** tagged
`DataType = historical-facts`.

## Lifecycle

`dev-up` recreates disposable `envs/dev` resources. With analytics still
disabled, this module creates nothing.

`dev-down -Force`:

1. DEACTIVATEs historical collection when collection is enabled
2. destroys `envs/dev`, including this module's results bucket if it
   exists (`force_destroy = true`)
3. leaves the persistent historical facts bucket intact

There is no `historical-data-down.ps1`. Ordinary `dev-down` / `dev-reset`
must not target `envs/dev-historical-data`.

## Enablement

`enable_historical_analytics` must remain `false` in `envs/dev` until a
reviewed analytics enable/apply. Glue catalog tables and the Athena
workgroup are later 2A.2 subphases, not this module yet.
