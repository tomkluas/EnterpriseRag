"""Offline human scoring and explicitly opted-in V3 SSE capacity measurements."""
import argparse
import asyncio
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CONTEXT_FIELDS = ('host', 'model', 'network', 'original_size', 'document_count', 'active_chunks', 'load_phase')
FORMATS = ('pdf', 'scanned_pdf', 'docx', 'xlsx', 'txt', 'markdown')
CATEGORIES = ('answerable', 'unanswerable', 'conflict', 'historical', 'table', 'scan', 'multi_turn', 'permission')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values)*fraction)-1)] if values else None


async def measure_sse(lines, clock=time.monotonic, expect_substrings=()):
    start = clock()
    first = None
    done = False
    failure = None
    data = []
    event_name = None
    answer = ''
    has_sources = False

    def consume():
        nonlocal first, done, failure, answer, has_sources
        if not data:
            return
        try:
            event = json.loads('\n'.join(data))
            if not isinstance(event, dict):
                raise ValueError()
            kind = event.get('event', event_name)
            if kind == 'error':
                failure = 'sse_error'
            elif kind == 'token' and isinstance(event.get('data'), str):
                if first is None and event['data'].strip():
                    first = clock() - start
                if expect_substrings:
                    answer += event['data']
            elif kind == 'sources' and isinstance(event.get('data'), list) and event['data']:
                has_sources = True
            elif kind == 'done':
                done = True
        except (ValueError, TypeError):
            failure = 'malformed_sse'

    async for line in lines:
        if line == '':
            consume()
            data = []
            event_name = None
            if done:
                break
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
        elif line.startswith('event:'):
            event_name = line[6:].strip()
    # Unterminated SSE frames are not complete events.
    elapsed = clock() - start
    if not done:
        failure = failure or 'missing_done'
    if first is None:
        failure = failure or 'missing_token'
    matches = bool(expect_substrings) and all(value.strip() and value in answer for value in expect_substrings)
    if expect_substrings and not (matches and has_sources):
        failure = failure or 'answer_validation'
    return dict(success=failure is None, first_token_s=first, complete_s=elapsed, failure=failure,
                answer_validated=failure is None and matches and has_sources)


async def request_once(client, space_id, question, timeout, expect_substrings=()):
    start = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            response = await client.post('/api/v3/conversations')
            response.raise_for_status()
            conversation = response.json()['id']
            async with client.stream('POST', f'/api/v3/conversations/{conversation}/messages/stream',
                                     json={'space_id':space_id, 'query':question}) as stream:
                stream.raise_for_status()
                if 'text/event-stream' not in stream.headers.get('content-type', ''):
                    raise ValueError('content_type')
                row = await measure_sse(stream.aiter_lines(), expect_substrings=expect_substrings)
                # Include session creation and HTTP dispatch in latency measurements.
                offset = time.monotonic() - start - row['complete_s']
                if row['first_token_s'] is not None:
                    row['first_token_s'] += offset
                row['complete_s'] += offset
                return row
    except (TimeoutError, httpx.TimeoutException):
        reason = 'timeout'
    except httpx.HTTPStatusError:
        reason = 'http_status'
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        reason = 'transport_or_protocol'
    return dict(success=False, first_token_s=None, complete_s=time.monotonic()-start, failure=reason,
                answer_validated=False)


def capacity_report(rows, *, elapsed_s, load_s, concurrency, context):
    successful = [row for row in rows if row['success']]
    first = [row['first_token_s'] for row in successful]
    complete = [row['complete_s'] for row in successful]
    rate = len(successful)/len(rows) if rows else 0
    missing = [key for key in CONTEXT_FIELDS if key not in context or context[key] in ('', None)]
    first95 = percentile(first, .95)
    complete95 = percentile(complete, .95)
    gates = dict(duration=load_s >= 1800 and elapsed_s >= load_s, concurrency=concurrency >= 10,
                 context=not missing, first_token=first95 is not None and first95 <= 10,
                 complete=complete95 is not None and complete95 <= 45, success_rate=rate >= .99,
                 answer_validation=bool(successful) and all(row.get('answer_validated') is True for row in successful))
    return dict(kind='capacity', passed=all(gates.values()), gates=gates, request_count=len(rows),
                success_count=len(successful), success_rate=rate, elapsed_s=elapsed_s, load_s=load_s,
                concurrency=concurrency, first_token_p50_s=percentile(first,.5), first_token_p95_s=first95,
                complete_p50_s=percentile(complete,.5), complete_p95_s=complete95,
                failures={key:sum(row.get('failure') == key for row in rows)
                          for key in sorted({row.get('failure') for row in rows if row.get('failure')})},
                context=context, context_fingerprint=fingerprint(context), missing_context=missing,
                latency_population='successful requests; failures separately included in success_rate',
                limitations=['Does not certify production acceptance, stage timings, or queue backlog.'])


async def run_capacity(args, context):
    # Called only after CLI write opt-in validation. No credential is written to reports.
    token = os.environ[args.token_env]
    headers = {'Authorization':'Bearer '+token}
    rows = []
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    deadline = started + args.duration
    async with httpx.AsyncClient(base_url=args.target, headers=headers, timeout=args.timeout,
                                 limits=httpx.Limits(max_connections=args.concurrency)) as client:
        async def worker():
            while time.monotonic() < deadline:
                rows.append(await request_once(client, args.space_id, args.question, args.timeout, args.expect_substring))
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
    elapsed = time.monotonic()-started
    report = capacity_report(rows, elapsed_s=elapsed, load_s=min(args.duration, elapsed),
                             concurrency=args.concurrency, context=context)
    report['config_fingerprint'] = fingerprint(dict(target=args.target, space=args.space_id,
        question_hash=fingerprint(args.question), duration=args.duration, concurrency=args.concurrency,
        timeout=args.timeout, context=context, expectation_hashes=[fingerprint(v) for v in args.expect_substring]))
    report['expectation_hashes'] = [fingerprint(v) for v in args.expect_substring]
    report['started_at_utc'] = started_at
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    return report


def quality_example():
    """Schema-only example, intentionally incapable of passing acceptance."""
    return dict(dataset_id='', frozen=False, frozen_at='', representative_confirmed_by='',
                documents=[], cases=[])


def quality_report(data):
    if not isinstance(data, dict):
        raise ValueError('quality input must be an object')
    documents = data.get('documents', [])
    cases = data.get('cases', [])
    if not isinstance(documents, list) or not isinstance(cases, list):
        raise ValueError('documents and cases must be arrays')
    if any(not isinstance(d, dict) or not isinstance(d.get('id'), str) for d in documents):
        raise ValueError('documents require string IDs')
    if any(not isinstance(c, dict) or not isinstance(c.get('id'), str)
           or not isinstance(c.get('document_ids', []), list)
           or any(not isinstance(d, str) for d in c.get('document_ids', [])) for c in cases):
        raise ValueError('cases require string IDs and document ID arrays')
    errors = []
    docmap = {d.get('id'): d for d in documents}
    if len(docmap) != len(documents) or any(not d.get('id') for d in documents):
        errors.append('duplicate_or_missing_document_id')
    if len({c.get('id') for c in cases}) != len(cases) or any(not c.get('id') for c in cases):
        errors.append('duplicate_or_missing_case_id')
    covered_docs = {d for c in cases for d in c.get('document_ids', [])}
    if len(covered_docs) < 20 or not covered_docs.issubset(docmap):
        errors.append('at_least_20_representative_documents_required')
    if len(cases) < 200:
        errors.append('at_least_200_frozen_business_cases_required')
    if not (data.get('frozen') is True and data.get('frozen_at') and data.get('dataset_id') and data.get('representative_confirmed_by')):
        errors.append('freeze_and_business_owner_confirmation_required')
    if any(not (c.get('reviewed') is True and c.get('reviewer') and c.get('expected_answer_ref') and c.get('evidence_ref')) for c in cases):
        errors.append('human_review_expected_answers_and_evidence_required')
    if any(c.get('category') not in CATEGORIES for c in cases):
        errors.append('unknown_category')
    if set(CATEGORIES) - {c.get('category') for c in cases}:
        errors.append('missing_categories')
    if any(d.get('supported') is not True for d in documents):
        errors.append('unsupported_documents')
    if any(d.get('format') not in FORMATS for d in documents):
        errors.append('unknown_format')

    def score(items, field):
        return sum(c.get('outcome') == 'completed' and c.get(field) is True for c in items)/len(items) if items else None

    answers = [c for c in cases if c.get('category') not in ('unanswerable', 'permission')]
    refusals = [c for c in cases if c.get('category') == 'unanswerable']
    permissions = [c for c in cases if c.get('category') == 'permission']
    correct = score(answers, 'correct')
    citations = score(answers, 'citation_supported')
    refusal = score(refusals, 'refusal_correct')
    location = {}
    for fmt in FORMATS:
        subset = [c for c in answers if any(docmap.get(d, {}).get('format') == fmt for d in c.get('document_ids', []))]
        location[fmt] = dict(count=len(subset), accuracy=score(subset, 'location_correct'))
    permissions_ok = bool(permissions) and all(c.get('permission_leak') is False and c.get('outcome') == 'completed' for c in permissions)
    gates = dict(coverage_and_review=not errors, answer_accuracy=correct is not None and correct >= .9,
                 citation_support=citations is not None and citations >= .95,
                 refusal_accuracy=refusal is not None and refusal >= .95,
                 format_location=all(v['accuracy'] is not None and v['accuracy'] >= .95 for v in location.values()),
                 zero_permission_leaks=permissions_ok)
    return dict(kind='human_quality_summary', passed=all(gates.values()), gates=gates, errors=errors,
                case_count=len(cases), covered_document_count=len(covered_docs), answer_count=len(answers),
                refusal_count=len(refusals), permission_count=len(permissions), answer_accuracy=correct,
                citation_support=citations, refusal_accuracy=refusal, format_location=location,
                dataset_fingerprint=fingerprint(data),
                limitations=['Human attestations are inputs, not independently verified facts; no model judging is performed.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    quality = sub.add_parser('quality')
    quality.add_argument('--input', type=Path, required=True)
    quality.add_argument('--output', type=Path)
    example = sub.add_parser('schema')
    capacity = sub.add_parser('capacity')
    capacity.add_argument('--target', help='Explicit origin URL; creates conversations and calls configured models')
    capacity.add_argument('--allow-write', action='store_true')
    capacity.add_argument('--space-id')
    capacity.add_argument('--question', default='请根据授权资料回答测试问题。')
    capacity.add_argument('--expect-substring', action='append', default=[],
                          help='Repeat for each expected answer fragment; matching is not a business correctness score')
    capacity.add_argument('--token-env', default='V3_ACCEPTANCE_TOKEN')
    capacity.add_argument('--duration', type=float, default=1800)
    capacity.add_argument('--concurrency', type=int, default=10)
    capacity.add_argument('--timeout', type=float, default=180)
    capacity.add_argument('--context', type=Path, help='JSON with nonsecret fixed environment descriptions')
    capacity.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'schema':
        report = quality_example()
    elif args.command == 'quality':
        try:
            report = quality_report(json.loads(args.input.read_text(encoding='utf-8-sig')))
        except (ValueError, OSError) as error:
            parser.error('invalid quality input: '+str(error))
    else:
        if any(not value.strip() for value in args.expect_substring):
            parser.error('--expect-substring must be nonblank')
        if not all(math.isfinite(v) and v > 0 for v in (args.duration, args.timeout)) or args.concurrency < 1:
            parser.error('duration, timeout and concurrency must be positive and finite')
        if not args.allow_write:
            report = dict(kind='capacity', dry_run=True, passed=False,
                          note='No requests sent. Running requires explicit --target, --space-id, --allow-write and environment token.')
        else:
            target = urlsplit(args.target or '')
            if target.scheme not in ('http', 'https') or not target.netloc or target.username or target.password or target.query or target.fragment or target.path not in ('', '/'):
                parser.error('--target must be an explicit HTTP(S) origin without credentials or query')
            if not args.space_id or not os.environ.get(args.token_env):
                parser.error('--space-id and nonempty credential environment variable required')
            context = json.loads(args.context.read_text(encoding='utf-8-sig')) if args.context else {}
            # Only allow documented metadata fields; never copy arbitrary credential config.
            context = {key:context[key] for key in CONTEXT_FIELDS if key in context}
            report = asyncio.run(run_capacity(args, context))
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if getattr(args, 'output', None):
        args.output.write_text(output+'\n', encoding='utf-8')
    else:
        print(output)
    return 0 if report.get('passed') else 2


if __name__ == '__main__':
    raise SystemExit(main())
