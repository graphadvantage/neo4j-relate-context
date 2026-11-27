"""Pure, conservative identity and ranking helpers for shared intent recall."""
import hashlib
import json
import re

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536


def query_integrity(cypher):
    """Reject descriptions and obvious abbreviation, without claiming syntax validity."""
    if not isinstance(cypher, str) or not cypher.strip():
        return 'missing'
    leading=re.sub(r'\A(?:\s+|//[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)*','',cypher)
    if not re.match(r'(?i)^(CYPHER|MATCH|OPTIONAL\s+MATCH|RETURN|WITH|UNWIND|CALL|CREATE|MERGE|SHOW|EXPLAIN|PROFILE|USE|DELETE|DETACH\s+DELETE|SET|REMOVE|FOREACH|LOAD\s+CSV|INSERT|DROP|ALTER|GRANT|DENY|REVOKE)\b',leading):
        return 'not_cypher'
    code = re.sub(r"'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\"|`(?:``|[^`])*`|//[^\n]*|/\*[\s\S]*?\*/", ' ', cypher)
    return 'incomplete' if '...' in code or '…' in code else 'not_validated'


def scope(project, mcp_source_id):
    if not isinstance(project, str) or not project.strip() or len(project) > 160:
        raise ValueError('A non-empty project is required')
    if not isinstance(mcp_source_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', mcp_source_id):
        raise ValueError('mcp_source_id must be the stable MCP server name, not a transport connection UUID')
    return {'project': project.strip(), 'mcp_source_id': mcp_source_id,
            'scope_key': hashlib.sha256(json.dumps([project.strip(), mcp_source_id]).encode()).hexdigest()}


def source_from_tool(name, explicit=None):
    if explicit:
        return scope('validation', explicit)['mcp_source_id']
    if not isinstance(name, str):
        return None
    name = name.removeprefix('mcp__')
    for separator in ('__', '/', '.'):
        if separator in name:
            prefix = name.split(separator, 1)[0]
            try:
                return scope('validation', prefix)['mcp_source_id']
            except ValueError:
                return None
    return None


def candidate(row):
    """Only correlate queries explicitly recorded in the same turn as the prompt."""
    try:
        source = source_from_tool(row.get('tool'), row.get('mcp_source_id'))
    except ValueError:
        return None
    question = row.get('question')
    if not source or not isinstance(question, str) or len(question.strip()) < 12:
        return None
    if not row.get('query_id') or not row.get('cypher') or not row.get('prompt_id'):
        return None
    if query_integrity(row['cypher']) != 'not_validated':
        return None
    try:
        scoped = scope(row.get('project'), source)
    except ValueError:
        return None
    normalized = ' '.join(question.split())
    fingerprint = hashlib.sha256(json.dumps([scoped['scope_key'], normalized]).encode()).hexdigest()
    return {**row, **scoped, 'id': fingerprint, 'question': question.strip(),
            'search_text': normalized.encode('utf-8')[:6000].decode('utf-8','ignore'), 'embedding_model': MODEL,
            'association': 'same_recorded_turn', 'validation': 'client_reported_unverified'}


def lucene_query(question, scope_key):
    # Scope is a generated hex key; user words are quoted, never Lucene operators.
    words = re.findall(r'\w+', question, re.UNICODE)[:32]
    if not words:
        return None
    return 'scope_key:' + scope_key + ' AND search_text:(' + ' OR '.join('"' + w + '"' for w in words) + ')'


def fuse(vector_rows, text_rows, limit):
    """Reciprocal rank fusion; never compare cosine and Lucene score scales."""
    found = {}
    for lane, rows in (('vector', vector_rows), ('fulltext', text_rows)):
        for rank, row in enumerate(rows, 1):
            item = found.setdefault(row['id'], {**row, 'rank_score': 0.0, 'matched_by': []})
            item['rank_score'] += 1 / (60 + rank)
            item['matched_by'].append(lane)
    return sorted(found.values(), key=lambda row: (-row['rank_score'], row['id']))[:limit]


def bounded(result, max_chars):
    if type(max_chars) is not int or not 2000 <= max_chars <= 64000:
        raise ValueError('max_chars must be between 2000 and 64000')
    result = {**result, 'truncated': False}
    while len(json.dumps(result, ensure_ascii=False)) > max_chars:
        # Keep the matching intent and query ID when full statements exceed
        # the budget. Never substitute a preview that looks executable.
        oversized=next((query for match in reversed(result.get('matches',[]))
                        for query in reversed(match.get('queries',[])) if query.get('cypher')),None)
        if oversized is not None:
            oversized.pop('cypher',None)
            oversized.pop('parameters',None)
            oversized.update(retrieval_required=True,omission_reason='response_budget')
            result['truncated']=True
            continue
        key = next((key for key in ('matches', 'evidence', 'pending_local') if result.get(key)), None)
        if key:
            result[key].pop()
            result['truncated'] = True
        elif result.get('query'):
            # A truncated Cypher statement must never look like an executable query.
            result['required_chars'] = len(json.dumps(result, ensure_ascii=False))
            result['query'] = None
            result['truncated'] = True
        else:
            raise ValueError('Response budget is too small')
    return result
