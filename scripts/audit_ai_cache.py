"""Read-only aggregate audit of locally recorded AI usage; no application imports."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('ISO timestamps must include a timezone')
    return result


def object_value(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def counter(sources, fields):
    values = [source[field] for source in sources for field in fields
              if field in source and source[field] is not None]
    valid = all(type(value) is int and value >= 0 for value in values)
    if valid:
        valid = len(set(values)) <= 1
    return (values[0] if values and valid else None), valid


def normalize_usage(value, protocol):
    if protocol not in ('openai', 'responses'):
        return None, None, None, None, 'unsupported_protocol'
    usage = object_value(value)
    sources = [usage, object_value(usage.get('raw')), object_value(usage.get('usage'))]
    inp, input_valid = counter(sources, ('input_tokens', 'prompt_tokens'))
    out, _ = counter(sources, ('output_tokens', 'completion_tokens'))
    hit_sources = [{key: source[key]} for source in sources for key in ('prompt_cache_hit_tokens', 'cache_hit_tokens')
                   if key in source]
    hit_sources += [object_value(source.get(key)) for source in sources
                    for key in ('prompt_tokens_details', 'input_tokens_details')]
    hit, hit_valid = counter(hit_sources, ('prompt_cache_hit_tokens', 'cache_hit_tokens', 'cached_tokens'))
    miss, miss_valid = counter(sources, ('prompt_cache_miss_tokens', 'cache_miss_tokens'))
    reason = 'invalid_input' if not input_valid else 'missing_input' if inp is None else None
    if not reason and (not hit_valid or not miss_valid):
        reason = 'invalid_cache_counts'
    if not reason and hit is None and miss is None:
        reason = 'missing_cache_counts'
    if not reason:
        if hit is None:
            hit = inp - miss
        if miss is None:
            miss = inp - hit
        if min(hit, miss) < 0 or hit + miss != inp:
            reason = 'contradictory_cache_counts'
    return inp, out, None if reason else hit, None if reason else miss, reason


def empty_totals():
    return dict(runs=0, input_tokens=None, output_tokens=None, cache_hit_tokens=None,
                cache_miss_tokens=None, input_known_runs=0, output_known_runs=0,
                cache_known_runs=0, cache_unknown_runs=0, weighted_hit_rate=None,
                cache_unknown_reasons=Counter(), status_counts=Counter())


def accumulate(total, row, usage):
    inp, out, hit, miss, reason = usage
    total['runs'] += 1
    total['status_counts'][row['status']] += 1
    for key, value, known in [('input_tokens', inp, 'input_known_runs'),
                              ('output_tokens', out, 'output_known_runs')]:
        if value is not None:
            total[key] = (total[key] or 0) + value
            total[known] += 1
    if reason:
        total['cache_unknown_runs'] += 1
        total['cache_unknown_reasons'][reason] += 1
    else:
        total['cache_known_runs'] += 1
        for key, value in [('cache_hit_tokens', hit), ('cache_miss_tokens', miss)]:
            total[key] = (total[key] or 0) + value
        denominator = total['cache_hit_tokens'] + total['cache_miss_tokens']
        total['weighted_hit_rate'] = total['cache_hit_tokens'] / denominator if denominator else None


def destination(manifest, row):
    snapshot = object_value(manifest.get('provider_snapshot'))
    pooled = manifest.get('pool_used') == 'pool'
    # A pool's initial provider is not proof of the actual successful route.
    # Keep each missing snapshot field unknown instead of mixing providers.
    provider = snapshot if pooled else {**object_value(manifest.get('provider')), **snapshot}
    try:
        host = (urlsplit(provider.get('base_url', '')).hostname or 'unknown').lower().rstrip('.')
    except (ValueError, TypeError, AttributeError):
        host = 'unknown'
    model = provider.get('model') or (None if pooled else row['model']) or 'unknown'
    protocol = provider.get('protocol') or (None if pooled else row['protocol']) or 'unknown'
    return host, model, protocol, pooled and 'unknown' in (host, model, protocol)


def audit(database, since=None, until=None, clock='received'):
    sql = ('SELECT g.model,g.protocol,g.status,g.usage,g.manifest,g.created_at,t.kind AS task_kind '
           'FROM generation_runs g LEFT JOIN tasks t ON t.id=g.task_id')
    result = dict(schema_version='ai-cache-audit.v2', generated_at=datetime.now(timezone.utc).isoformat(),
                  database_access='mode=ro; query_only=ON', clock=clock, since=since.isoformat() if since else None,
                  until=until.isoformat() if until else None, interval='[since, until)',
                  totals=empty_totals(), groups=[], clock_fallback_runs=0,
                  invalid_clock_runs=0, unattributed_pool_runs=0)
    groups, hashes = {}, Counter()
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        result['audit_query_plan'] = [row['detail'] for row in connection.execute('EXPLAIN QUERY PLAN ' + sql)]
        result['query_plan'] = result['audit_query_plan']  # Compatibility with the first report.
        result['response_cache_lookup_query_plan'] = [row['detail'] for row in connection.execute(
            'EXPLAIN QUERY PLAN SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at DESC',
            ('cache-audit-placeholder',))]
        result['indexes'] = [row['name'] for row in connection.execute("PRAGMA index_list('generation_runs')")]
        for row in connection.execute(sql):
            manifest = object_value(row['manifest'])
            evidence = object_value(manifest.get('response_evidence'))
            received = evidence.get('received_at') if clock == 'received' else None
            fallback = False
            try:
                moment = timestamp(received) if received else None
            except (ValueError, TypeError):
                moment = None
            if moment is None:
                fallback = clock == 'received'
                try:
                    moment = timestamp(row['created_at'])
                except (ValueError, TypeError):
                    result['invalid_clock_runs'] += 1
                    continue
            if since and moment < since or until and moment >= until:
                continue
            result['clock_fallback_runs'] += int(fallback)
            host, model, protocol, unattributed = destination(manifest, row)
            result['unattributed_pool_runs'] += int(unattributed)
            stage = next((manifest[key] for key in ('writing_stage', 'stage', 'knowledge_operation', 'operation')
                          if isinstance(manifest.get(key), str) and manifest[key]), row['task_kind'] or 'unknown')
            key = host, model, protocol, stage
            group = groups.setdefault(key, dict(host=host, model=model, protocol=protocol, stage=stage, **empty_totals()))
            usage = normalize_usage(row['usage'], protocol)
            accumulate(group, row, usage)
            accumulate(result['totals'], row, usage)
            context_hash = manifest.get('outgoing_context_hash')
            if isinstance(context_hash, str) and context_hash:
                hashes[key + (context_hash,)] += 1
    result['groups'] = [groups[key] for key in sorted(groups)]
    result['duplicate_context_candidates'] = dict(
        groups=sum(count > 1 for count in hashes.values()),
        additional_runs=sum(count - 1 for count in hashes.values() if count > 1),
        interpretation='Repeated outgoing_context_hash is a candidate only; it does not prove safe response reuse.')
    result['limitations'] = [
        'Token sums include known values only; null means no known values. Check known/unknown run counts.',
        'Hit rate is weighted over runs with consistent known input and cache counts; missing cache is never zero.',
        'Host/model/protocol use saved provider snapshots; legacy pool failover without an actual snapshot is unknown.',
        'Received clock falls back to created_at when response receipt time is missing or invalid.',
        'This audits recorded run usage, not an independent provider invoice or every internal HTTP retry.']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'data/workbench.sqlite3')
    parser.add_argument('--since', type=timestamp)
    parser.add_argument('--until', type=timestamp)
    parser.add_argument('--clock', choices=('received', 'created'), default='received')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.since and args.until and args.since >= args.until:
        parser.error('--since must be earlier than --until')
    source = args.database.resolve()
    protected = [source, *(Path(str(source) + suffix) for suffix in ('-wal', '-shm', '-journal'))]
    if args.output and any(args.output.resolve() == path or
                           args.output.exists() and path.exists() and args.output.samefile(path)
                           for path in protected):
        parser.error('--output must not overwrite the source database or its sidecars')
    encoded = json.dumps(audit(args.database, args.since, args.until, args.clock), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', encoding='utf-8')
    else:
        print(encoded)


if __name__ == '__main__':
    main()
