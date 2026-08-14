# ADR 0003: Vendor-free report composition and future knowledge protocol

- Status: accepted
- Date: 2026-08-14

## Scope

This ADR defines boundaries only. It adds no MCP server, embedding model, graph
database, queue, SDK, migration, or worker.

## Stable query vocabulary

Future application services may expose these inputs after an actual feature
needs them:

| Query/use case | Required input | Required output rule |
|---|---|---|
| `ProjectInfoQuery` | `principal`, `project_id` | current project source revision after project-scope authorization |
| `ProductInfoQuery` | `principal`, `project_id` | product entries and source metadata, not a frontend DTO clone |
| `MaterialPropertyQuery` | `principal`, `project_id`, material selector | only after canonical material IDs exist |
| `PartStructureQuery` | `principal`, `project_id`, part selector | only after canonical part/BOM cardinality exists |
| `AnalysisEvidenceQuery` | `principal`, `project_id`, source selector | selected current source evidence and verification state |
| `ComposeReportContext` | `principal`, `project_id`, source references, report options | immutable provenance-bearing read model |
| `GenerateReport` | `ComposeReportContext`, renderer options | generated artifact plus provenance, never direct arbitrary file access |

Every source reference contains `type`, `id`, `version`, `checksum`,
`project_id`, and access scope. A report context records the selected source
revisions, creation timestamp, actor, authorization scope, and output checksum.
PostgreSQL remains the canonical permission and source check before returning
any derived result.

## Future integration ports

`EmbeddingPort`, `GraphProjectionPort`, and `KnowledgeSearchPort` are names,
not interfaces, until their first concrete product capability. Any future
projection must be idempotent by source reference and revalidate PostgreSQL
authorization and source existence before a result is returned.

## Future MCP adapter policy

An MCP tool, if approved later, is an adapter to the same application use case,
not an SQL or filesystem escape hatch. Each tool requires an allowlisted name,
explicit input schema, project scope, bounded timeout and response size, audit
event, and provenance-bearing response. It must not use the OpenAPI client as
its domain API.

## Deferred data models

Materials and parts remain deferred: current storage/API contracts do not
define canonical material IDs or part/BOM relationships. Empty aggregate
directories and fake ports would hide this unresolved product decision.
