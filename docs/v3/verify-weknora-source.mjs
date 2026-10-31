// Static source-contract audit only. This does not execute WeKnora or prove runtime behavior.
// Run: node docs/v3/verify-weknora-source.mjs --source-dir <downloaded-source> --output <report.json>
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

const args = process.argv.slice(2);
const option = name => args[args.indexOf(name) + 1];
if (!args.includes('--source-dir') || !args.includes('--output')) {
  throw new Error('Required: --source-dir <path> --output <path>');
}
const root = path.resolve(option('--source-dir'));
const commit = 'f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7';
const rules = [
  ['retrieval_route', 'internal/router/routes_knowledge.go', /kb\.POST\("\/:id\/hybrid-search"/],
  ['upload_route', 'internal/router/routes_knowledge.go', /kb\.POST\("\/file"/],
  ['delete_route', 'internal/router/routes_knowledge.go', /k\.DELETE\("\/:id"/],
  ['reparse_route', 'internal/router/routes_knowledge.go', /k\.POST\("\/:id\/reparse"/],
  ['rebuild_route', 'internal/router/routes_knowledge.go', /kbRebuild\.POST\("\/rebuild-index"/],
  ['document_scope_input', 'internal/types/search.go', /KnowledgeIDs\s+\[\]string `json:"knowledge_ids"/],
  ['skip_enrichment_input', 'internal/types/search.go', /SkipContextEnrichment bool/],
  ['document_scope_propagated', 'internal/application/service/knowledgebase_search.go', /KnowledgeIDs:\s+params\.KnowledgeIDs/],
  ['multi_kb_authorization', 'internal/application/service/knowledgebase_search.go', /s\.authorizeKBAccess\(ctx, kbs\)/],
  ['embedding_consistency', 'internal/application/service/knowledgebase_search.go', /s\.validateSameEmbeddingModel\(ctx, kbs\)/],
  ['keyword_document_filter', 'internal/application/repository/retriever/postgres/repository.go', /Column: "knowledge_id"/],
  ['vector_document_filter', 'internal/application/repository/retriever/postgres/repository.go', /Filtering vector search by knowledge IDs/],
  ['source_locators_in_response', 'internal/types/search.go', /SourceLocators SourceLocators `json:"source_locators,omitempty"`/],
  ['locator_partial_flag', 'internal/types/source_locator.go', /Partial bool `json:"partial,omitempty"`/],
  ['api_key_retrieve_capability', 'internal/types/tenant_api_key.go', /APIKeyCapabilityRetrieve APIKeyCapability = "retrieve"/],
  ['api_key_kb_allowlist', 'internal/types/tenant_api_key.go', /KnowledgeBaseIDs StringArray/],
  ['explicit_rbac_override', 'internal/config/config.go', /os\.Getenv\("WEKNORA_TENANT_ENABLE_RBAC"\)/],
  ['no_rerank_by_default', 'internal/handler/knowledgebase.go', /if req\.Rerank != nil/],
  ['rerank_failure_fallback', 'internal/application/service/knowledgebase_search_rerank.go', /diag\.Outcome = types\.RerankOutcomeModelUnavailable/],
  ['document_retry_count', 'internal/application/service/knowledge_task_options.go', /asynq\.MaxRetry\(3\)/],
  ['durable_mode_detection', 'internal/container/reset_pending_tasks.go', /distributed := os\.Getenv\("REDIS_ADDR"\) != ""/],
  ['housekeeping_failure_detection', 'internal/application/service/knowledge_housekeeping.go', /marks them as failed/],
  ['redis_aof_in_compose', 'docker-compose.yml', /redis-server --appendonly yes/],
  ['postgres_search_is_paradedb', 'docker-compose.yml', /image: paradedb\/paradedb:v0\.25\.11-pg17/],
  ['upstream_go_requirement', 'go.mod', /^go 1\.26\.0$/m],
];
const files = new Map();
const checks = [];
for (const [id, relative, regex] of rules) {
  let body = files.get(relative);
  if (body === undefined) {
    body = await readFile(path.join(root, relative), 'utf8');
    files.set(relative, body);
  }
  const match = regex.exec(body);
  const line = match ? body.slice(0, match.index).split('\n').length : null;
  checks.push({ id, status: match ? 'source_pattern_found' : 'missing', file: relative, line,
    source: `https://github.com/Tencent/WeKnora/blob/${commit}/${relative}${line ? `#L${line}` : ''}` });
}
const manifest = [];
for (const [relative] of files) {
  const bytes = await readFile(path.join(root, relative));
  manifest.push({ file: relative, sha256: createHash('sha256').update(bytes).digest('hex') });
}
const report = {
  schema_version: 1, audited_at: new Date().toISOString(), upstream: 'https://github.com/Tencent/WeKnora',
  tag: 'v0.9.0', commit, audit_level: 'static_source_contract',
  scope: 'Presence of source constructs at the pinned revision; manual call-chain review is documented separately.',
  summary: { checked: checks.length, found: checks.filter(x => x.status === 'source_pattern_found').length },
  runtime_integration: 'not_run', upstream_go_tests: 'not_run',
  limits: ['Docker Linux engine was unavailable, including after an attempt to start installed Docker Desktop.',
    'Installed Go is 1.21.5; upstream go.mod requires 1.26.0.',
    'Pattern checks do not prove authorization, recovery, parser quality, or concurrency behavior.',
    'The caller must obtain source files from the pinned commit; this script does not authenticate the source directory.'],
  checks, manifest,
};
await writeFile(path.resolve(option('--output')), `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify(report.summary));
if (checks.some(x => x.status === 'missing')) process.exitCode = 1;
