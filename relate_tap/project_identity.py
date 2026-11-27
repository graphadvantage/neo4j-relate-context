"""Credential-free Git identities and explicit, additive legacy session binding."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

PROJECT_LIMIT = 512


def local_key(path):
    # Do not publish private filesystem paths as enterprise project names.
    return 'git-local:' + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()


def origin_key(origin, directory=None):
    """Normalize transport variants without collapsing owners or Git hosts.

    GitHub paths are case insensitive. Other hosts retain path case; resolving
    SSH aliases or renamed repositories requires explicit configuration.
    """
    origin = origin.strip()
    if not origin or any(ord(c) < 32 for c in origin):
        raise ValueError('Invalid Git origin')
    base = Path(directory or Path.cwd())
    if origin.startswith('file://'):
        parsed = urlsplit(origin)
        if parsed.netloc not in ('', 'localhost'):
            raise ValueError('Remote file origins require an explicit project key')
        return local_key(unquote(parsed.path))
    if '://' in origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('https', 'http', 'ssh', 'git') or not parsed.hostname:
            raise ValueError('Unsupported Git origin; configure an explicit project key')
        host = parsed.hostname.lower()
        port = parsed.port
        if port and port != {'https':443, 'http':80, 'ssh':22, 'git':9418}[parsed.scheme]:
            host += ':' + str(port)
        path = parsed.path
    else:
        scp = re.fullmatch(r'(?:[^/@:]+@)?([^/:]+):(.+)', origin)
        if scp and not re.match(r'^[A-Za-z]:[\\/]', origin):
            host, path = scp.groups()
            host = host.lower()
        else:
            return local_key(base / origin)
    parts = unquote(path).strip('/').split('/')
    if any(not part or part in ('.', '..') or re.search(r'[\s?#\\]', part) for part in parts):
        raise ValueError('Invalid Git origin path')
    parts[-1] = parts[-1].removesuffix('.git')
    if not parts[-1]:
        raise ValueError('Invalid Git origin path')
    path = '/'.join(parts)
    if host == 'github.com':
        path = path.lower()
    key = 'git:' + host + '/' + path
    if len(key) > PROJECT_LIMIT:
        raise ValueError('Git identity exceeds project key limit')
    return key


def discover_repository(directory=None):
    """Return a canonical key only when Git establishes repository provenance."""
    directory = Path(directory or Path.cwd()).resolve()
    try:
        common = subprocess.run(['git', '-C', str(directory), 'rev-parse',
                                 '--path-format=absolute', '--git-common-dir'],
                                capture_output=True, text=True, timeout=5)
        if common.returncode:
            return None
        remote = subprocess.run(['git', '-C', str(directory), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=5)
        if remote.returncode == 0 and remote.stdout.strip():
            try:
                return origin_key(remote.stdout, directory)
            except ValueError:
                # Preserve isolated local capture for unsupported remote syntax.
                pass
        return local_key(common.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return None


BIND_SESSIONS = '''
MATCH (legacy:ContextProject {name:$input.legacy})-[:HAS_SESSION]->(s:ContextSession:Code)
WHERE s.id IN $input.sessions AND NOT s:Chat
WITH collect(DISTINCT s) AS sessions
WHERE size(sessions)=size($input.sessions)
MERGE (target:ContextProject {name:$input.project})
SET target:Code
FOREACH (s IN sessions | MERGE (target)-[:HAS_SESSION]->(s))
RETURN size(sessions) AS bound
'''


def bind_sessions(run, legacy, project, sessions, apply=False):
    """Retain original nodes/links/IDs; never automatically bind by basename."""
    sessions = list(dict.fromkeys(sessions))
    if not sessions or any(not isinstance(s, str) or not s for s in sessions):
        raise ValueError('Select verified ContextSession IDs explicitly')
    if not project.startswith(('git:', 'git-local:')) or project == legacy:
        raise ValueError('Select a distinct canonical repository key')
    params = {'legacy':legacy, 'project':project, 'sessions':sessions}
    if not apply:
        return {'apply':False, **params}
    rows = run(BIND_SESSIONS, params, read_only=False)
    if not rows:
        raise ValueError('No changes made: every selected session must belong to the legacy project and be Code-only')
    return {'apply':True, **params, 'bound':rows[0]['bound']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--legacy-project', required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--session-id', action='append', required=True,
                        help='Verified ContextSession.id; repeat for each session')
    parser.add_argument('--apply', action='store_true', help='Default is a dry run')
    args = parser.parse_args()
    from database_lock import DatabaseLock
    from memory_graph import direct_query
    if not args.database.is_file():
        parser.error('Database must already exist')
    with DatabaseLock(args.database):
        def run(q, p, **kw):
            return direct_query(args.database, q, p, **kw)
        print(json.dumps(bind_sessions(run, args.legacy_project, origin_key(args.origin),
                                       args.session_id, args.apply)))


if __name__ == '__main__':
    main()
