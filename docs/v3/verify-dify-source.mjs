// Static contract presence checks, not runtime integration or security tests.
// node docs/v3/verify-dify-source.mjs --source-dir <source> --output <report.json>
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
const args = process.argv.slice(2);
if (!args.includes('--source-dir') || !args.includes('--output')) throw new Error('Required: --source-dir and --output');
const option = key => args[args.indexOf(key) + 1];
const root = path.resolve(option('--source-dir'));
const commit = '50abea97062110044f15d0e03ccecadd9ccc87ca';
const rules = [
  ['retrieve_route', 'api/controllers/service_api/dataset/hit_testing.py', /@service_api_ns.route\("\/datasets\/<uuid:dataset_id>\/retrieve"\)/],
  ['upload_route', 'api/controllers/service_api/dataset/document.py', /\/document\/create-by-file/],
  ['batch_status_route', 'api/controllers/service_api/dataset/document.py', /\/documents\/<string:batch>\/indexing-status/],
  ['delete_status', 'api/controllers/service_api/dataset/document.py', /return "", 204/],
  ['metadata_write_route', 'api/controllers/service_api/dataset/metadata.py', /\/datasets\/<uuid:dataset_id>\/documents\/metadata/],
  ['retrieval_schema_accepts_filter', 'api/services/knowledge/entities/knowledge_entities.py', /metadata_filtering_conditions: MetadataFilteringCondition/],
  ['metadata_in_condition', 'api/core/rag/retrieval/dataset_retrieval.py', /case "in" \| "not in":/],
  ['metadata_sql_filter', 'api/core/rag/retrieval/dataset_retrieval.py', /json_field\.in_/],
  ['metadata_ids_before_retrieval', 'api/services/hit_testing_service.py', /document_ids_filter=document_ids_filter/],
  ['no_matches_return_empty', 'api/services/hit_testing_service.py', /if metadata_condition and not document_ids_filter:/],
  ['vector_scope_forwarded', 'api/core/rag/datasource/retrieval_service.py', /vector\.search_by_vector\([\s\S]*?document_ids_filter=document_ids_filter/],
  ['fulltext_scope_forwarded', 'api/core/rag/datasource/retrieval_service.py', /vector_processor\.search_by_full_text\([\s\S]*?document_ids_filter=document_ids_filter/],
  ['dataset_scoped_key_check', 'api/controllers/service_api/wraps.py', /if not scope\.allows_dataset/],
  ['query_limit_250', 'api/controllers/console/datasets/hit_testing_base.py', /max_length=250/],
  ['response_document_id', 'api/fields/hit_testing_fields.py', /document_id: str/],
  ['response_segment_position', 'api/fields/hit_testing_fields.py', /position: int/],
];
const files = new Map();
const checks = [];
for (const [id, file, pattern] of rules) {
  if (!files.has(file)) files.set(file, await readFile(path.join(root, file), 'utf8'));
  const body = files.get(file);
  const match = pattern.exec(body);
  const line = match ? body.slice(0, match.index).split('\n').length : null;
  checks.push({id, file, line, status: match ? 'source_pattern_found' : 'missing',
    source: `https://github.com/langgenius/dify/blob/${commit}/${file}${line ? `#L${line}` : ''}`});
}
const manifest = [];
for (const file of files.keys()) manifest.push({file, sha256:createHash('sha256').update(await readFile(path.join(root,file))).digest('hex')});
const report = {schema_version:1, audited_at:new Date().toISOString(), upstream:'https://github.com/langgenius/dify',
  tag:'1.18.0', commit, audit_level:'static_source_contract', runtime_integration:'not_run', upstream_tests:'not_run',
  summary:{checked:checks.length,found:checks.filter(x=>x.status==='source_pattern_found').length}, checks, manifest,
  limits:['Patterns prove source constructs, not runtime authorization or retrieval quality.',
    'Vector/fulltext filters are confirmed at dispatch; selected storage implementation still needs G0 validation.',
    'Precise original-page navigation and OCR parity with WeKnora are not established.',
    'The caller must authenticate the downloaded source revision; this script does not.']};
await writeFile(path.resolve(option('--output')), `${JSON.stringify(report,null,2)}\n`);
console.log(JSON.stringify(report.summary));
if (checks.some(x=>x.status==='missing')) process.exitCode=1;
