"""Discover an MCP client and bootstrap its host-local capture destination."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

CLIENTS = {
    'claude-ai': 'claude-desktop', 'claude-desktop': 'claude-desktop',
    'claude-code': 'claude-code', 'codex-mcp-client': 'codex', 'codex': 'codex',
}


def canonical_agent(value):
    if not value:
        return None
    name = value.strip().lower()
    name = CLIENTS.get(name, name)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name):
        raise ValueError('Agent names must be simple lowercase identifiers')
    return name


def client_identity(message):
    params = message.get('params', {})
    if not isinstance(params, dict):
        return None
    info = params.get('clientInfo') if message.get('method') == 'initialize' else None
    if info is None:
        meta = params.get('_meta', {})
        info = meta.get('io.modelcontextprotocol/clientInfo') if isinstance(meta, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get('name'), str):
        return None
    return {k: info[k][:200] for k in ('name', 'version', 'title') if isinstance(info.get(k), str)}


def discover_agent(info):
    if not info:
        return None
    name=info.get('name','').strip().lower()
    # Claude Desktop creates a per-server local-agent MCP client in addition
    # to its claude-ai client. This is a Desktop transport identity, not an agent.
    if name.startswith('local-agent-mode-') and len(name)>len('local-agent-mode-'):
        return 'claude-desktop'
    return CLIENTS.get(name)


def resolved_route(route):
    if route.get('agent'):
        return route
    agent=discover_agent(route.get('client_info'))
    return {**route, 'agent': agent, 'database': agent+'.rl', 'identity_source':'mcp_client'} if agent else route


def tap_home():
    return Path(os.environ.get('RELATE_TAP_HOME', str(Path.home()/'.relate-tap')))


def settings():
    path = tap_home()/'config.json'
    return json.loads(path.read_text()) if path.exists() else {}


def database_directory():
    return Path(os.environ.get('RELATE_TAP_DATABASE_DIR') or settings().get('database_dir') or tap_home()/'databases')


def database_for(agent):
    return database_directory()/(canonical_agent(agent)+'.rl')


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name+'-')
    try:
        with os.fdopen(fd, 'w') as target:
            json.dump(data, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_skill(agent):
    if agent == 'claude-desktop':
        return {'state': 'mcp', 'detail': 'Desktop uses the memory MCP connector'}
    if agent == 'claude-code':
        root = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home()/'.claude')))
    elif agent == 'codex':
        root = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
    else:
        return {'state': 'unsupported'}
    destination = root/'skills/relate-tap-memory'
    destination.mkdir(parents=True, exist_ok=True)
    target = destination/'SKILL.md'
    # Publish a complete file atomically, without overwriting a user's existing skill.
    fd, temporary = tempfile.mkstemp(dir=destination, prefix='.skill-')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write((Path(__file__).parent/'skills/relate-tap-memory/SKILL.md').read_text().replace('__AGENT__', agent))
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target)
    except FileExistsError:
        return {'state': 'existing', 'path': str(target)}
    finally:
        os.unlink(temporary)
    return {'state': 'installed', 'path': str(target)}


def start_worker(agent, spool):
    home = tap_home()
    logs = home/'logs'
    logs.mkdir(parents=True, exist_ok=True)
    fd = os.open(logs/(agent+'.log'), os.O_WRONLY|os.O_CREAT|os.O_APPEND, 0o600)
    with os.fdopen(fd, 'ab') as log:
        options = {'start_new_session': True} if os.name == 'posix' else {
            'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        subprocess.Popen([sys.executable, '-m', 'relate_tap.worker', '--agent', agent,
                          '--spool', str(spool.resolve())], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, close_fds=True, **options)


def context_kind(project, agent=None, explicit=None, directory=None):
    """Classify work from explicit provenance or a matching Git checkout."""
    if explicit is not None:
        if explicit not in ('Chat', 'Code'):
            raise ValueError('context_kind must be Chat or Code')
        return explicit
    # Desktop projects are chat contexts even when launched inside a checkout.
    if canonical_agent(agent) == 'claude-desktop':
        return 'Chat'
    directory = Path(directory or Path.cwd()).resolve()
    try:
        result = subprocess.run(['git', '-C', str(directory), 'rev-parse', '--git-dir'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and project == discover_project(directory):
            return 'Code'
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def discover_project(directory=None):
    """Canonical Git identity, or a directory name for non-repository contexts."""
    from .project_identity import discover_repository
    directory = Path(directory or Path.cwd()).resolve()
    return discover_repository(directory) or directory.name
