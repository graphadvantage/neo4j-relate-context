"""Use the existing console as the retrieval backend; keys stay inside Docker."""
import base64
import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .bootstrap import settings
from .intent_model import scope


def request(operation, arguments, project=None, agent=None):
    scoped=scope(arguments.get('project') or project,arguments.get('mcp_source_id'))
    body={**arguments,**scoped}
    body.pop('scope_key',None)
    if agent:
        body['_client']={'agent':agent,'host':socket.gethostname()}
    base=os.environ.get('RELATE_MEMORY_CONSOLE_URL') or settings().get('console_url') or 'http://127.0.0.1:8080'
    parsed=urlparse(base)
    if parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Invalid configured memory console URL')
    headers={'Content-Type':'application/json'}
    token=os.environ.get('RELATE_CONSOLE_TOKEN') or settings().get('console_token')
    if token:
        headers['Authorization']='Basic '+base64.b64encode(('relate:'+token).encode()).decode()
    message=Request(base.rstrip('/')+'/api/memory/'+operation,
        data=json.dumps(body).encode(),headers=headers)
    try:
        with urlopen(message,timeout=120) as response:
            return json.load(response)
    except HTTPError as exc:
        # Do not echo server/provider errors which could contain configuration.
        raise ValueError('Shared recall request rejected (HTTP '+str(exc.code)+')') from None
    except (URLError,TimeoutError,OSError):
        raise ValueError('Shared recall console is unavailable; no unscoped fallback was attempted') from None
