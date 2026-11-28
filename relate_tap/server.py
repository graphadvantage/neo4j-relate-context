"""Docker-hosted console and single Relate spool collector."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from .sources import Sources
from .store import CaptureStore
from .registry import registry
from .collector import ContainerRoutes
from .diagnostics import Diagnostics
from .intent_recall import IntentRecall
from .cowork_metrics import CoworkMetrics
from memory_graph import RELATE, direct_query


def create_app(data=None, spool=None, config=None, frontend=None):
    data = Path(data or os.environ.get("RELATE_CONSOLE_DATA", "/data"))
    spool = Path(spool or os.environ.get("RELATE_TAP_SPOOL", "/spool"))
    frontend = Path(frontend or os.environ.get("RELATE_CONSOLE_FRONTEND", str(Path(__file__).resolve().parent.parent / "frontend/dist")))
    config = config or os.environ.get("RELATE_CONSOLE_SOURCES")
    store = CaptureStore(data / "capture.rl", spool, data / "offsets.json")
    container_routes = ContainerRoutes(data, spool)
    sources = Sources(store.database, config, store.graph_lock, os.environ.get("RELATE_CONSOLE_SOURCE_DIR"), container_routes.directory)
    sources.database_locks = container_routes.locks
    sync = None
    recall = IntentRecall(sources)
    cowork_metrics = CoworkMetrics(store.database,store.graph_lock)
    diagnostics = Diagnostics(data)
    stop = asyncio.Event()
    recall_activity = deque(maxlen=100)
    recall_activity_lock = threading.Lock()

    def recall_receipts():
        with recall_activity_lock:
            return [dict(row) for row in recall_activity]

    def capture_errors(agents, snapshot=None):
        snapshot = snapshot or store.snapshot()
        errors = [{**e, 'source': 'console collector'} for e in snapshot['errors']]
        errors.extend({**e, 'source': 'container capture'} for e in container_routes.errors)
        for agent in agents:
            errors.extend({**e, 'source': agent['agent']} for e in agent.get('errors', []))
            push = agent.get('replication', {})
            if push.get('error'):
                errors.append({'source': agent['agent']+' Aura push', 'error': push['error'], 'file': agent.get('database', '')})
        return errors

    async def collect():
        while not stop.is_set():
            await asyncio.to_thread(store.poll)
            await asyncio.to_thread(container_routes.poll)
            errors = capture_errors(registry(spool)['agents'] + container_routes.statuses())
            await asyncio.to_thread(diagnostics.update, errors)
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def promote_intents():
        while not stop.is_set():
            if all(os.environ.get('AURA_'+key) for key in ('URI','USERNAME','PASSWORD')):
                await asyncio.to_thread(recall.coding_search.prepare)
            try:
                if recall.enabled():
                    await asyncio.to_thread(recall.promote)
            except Exception as exc:
                recall.status={'state':'retrying','error':type(exc).__name__}
                diagnostics.report('Intent promotion','Promotion failed ('+type(exc).__name__+'); retrying.')
            # Count saved intents in the background, even when promotion fails.
            # The topology event stream never waits for a remote Aura query.
            await asyncio.to_thread(recall.refresh_intent_statistics)
            try:
                await asyncio.wait_for(stop.wait(),timeout=30)
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        nonlocal sync
        # One app process per data volume, including when launched manually.
        import fcntl
        owner = (data / "collector.lock").open("a")
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            owner.close()
            raise RuntimeError("A collector already owns this data volume") from None
        # Fail startup when the binary cannot execute (e.g. incompatible libc).
        try:
            subprocess.run([RELATE, "--version", "--json"], check=True, capture_output=True, timeout=10)
            with store.graph_lock:
                if not store.database.exists():
                    direct_query(store.database, 'RETURN 1 AS initialized', {}, read_only=False)
        except (OSError, subprocess.SubprocessError):
            owner.close()
            raise RuntimeError("Relate CLI cannot run in this environment") from None
        credentials = os.environ.get("RELATE_TAP_AURA_CREDENTIALS")
        if credentials:
            from aura_sync import AuraSync
            sync = AuraSync(store.database, Path(credentials), store.graph_lock)
        task = asyncio.create_task(collect())
        promotion = asyncio.create_task(promote_intents())
        try:
            yield
        finally:
            stop.set()
            await task
            await promotion
            recall.close()
            sources.close()
            owner.close()

    app = FastAPI(title="Relate capture console", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.environ.get(
        "RELATE_CONSOLE_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(","))
    app.state.store = store

    @app.middleware("http")
    async def access(request, call_next):
        token = os.environ.get("RELATE_CONSOLE_TOKEN")
        if token:
            import base64
            expected = "Basic " + base64.b64encode(("relate:" + token).encode()).decode()
            if not secrets.compare_digest(request.headers.get("authorization", ""), expected):
                return JSONResponse({"detail": "Authentication required"}, 401, {"WWW-Authenticate": 'Basic realm="Relate console"'})
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin requests are disabled"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get('/api/memory/status')
    def memory_status():
        return recall.status if recall.enabled() else {'state':'disabled'}

    @app.post('/api/memory/{operation}')
    async def memory_request(operation: str, body: dict):
        client=body.pop('_client',None)
        activity=None
        tools={'recall':'recall_context','query':'get_query','evidence':'get_evidence','session':'get_session'}
        if operation in tools and isinstance(client,dict) and all(
            isinstance(client.get(k),str) and 0<len(client[k])<=200 for k in ('agent','host')):
            activity={'id':str(uuid.uuid4()),'agent':client['agent'],'host':client['host'],
                'project':body.get('project'),'tool':tools[operation],
                'started_at':datetime.now(timezone.utc).isoformat(),'state':'running'}
            with recall_activity_lock:
                recall_activity.append(activity)
        state='error'
        try:
            result=await asyncio.to_thread(recall.request,operation,body)
            state=result.get('state','ready')
            try:
                await asyncio.to_thread(cowork_metrics.record,operation,body,result,client)
            except Exception as exc:
                diagnostics.report('Cowork recall hits','Could not persist hit ('+type(exc).__name__+').')
            return result
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
        except Exception as exc:
            diagnostics.report('Intent recall','Recall failed ('+type(exc).__name__+').')
            raise HTTPException(503,'Shared recall unavailable ('+type(exc).__name__+')') from None
        finally:
            if activity:
                with recall_activity_lock:
                    activity.update(state=state,finished_at=datetime.now(timezone.utc).isoformat())

    @app.get("/api/sources")
    def list_sources():
        return sources.public()

    @app.get("/api/health")
    def health():
        snapshot = store.snapshot()
        errors = snapshot['errors'] + container_routes.errors
        return JSONResponse({"state": "backlog" if errors else "ready"}, status_code=503 if errors else 200)

    @app.get('/api/errors')
    def error_history():
        return diagnostics.snapshot()

    @app.post('/api/errors/clear')
    def clear_errors():
        return diagnostics.clear()

    @app.get('/api/errors/log')
    def error_log():
        if not diagnostics.log.exists():
            return JSONResponse([])
        return FileResponse(diagnostics.log, media_type='application/x-ndjson', filename='relate-console-errors.jsonl')

    @app.post('/api/errors')
    def report_error(body: ErrorBody):
        return diagnostics.report(body.source, body.error)

    @app.get("/api/topology")
    def topology():
        inventory = registry(spool)
        container_status = container_routes.statuses()
        inventory['agents'].extend(container_status)
        for route in inventory['routes']:
            if route.get('writer') == 'console':
                status = next((a for a in container_status if a['agent'] == route.get('agent')), {})
                route['database_state'] = status.get('state', 'pending')
                route['persisted_offset'] = status.get('offsets', {}).get(route['connection']+'.jsonl', 0)
                route['commits'] = [c for c in status.get('commits', []) if c.get('connection') == route['connection']]
                route['replication'] = status.get('replication', {'state': 'disabled'})
        enterprise = next((s for s in sources.public() if s['kind'] == 'aura' and s['id'] == 'enterprise-relate-memory'), None)
        statuses = [a['replication']['state'] for a in inventory['agents'] if a.get('replication')]
        state = ('retrying' if 'retrying' in statuses else 'pushing' if 'pushing' in statuses else 'pending' if 'pending' in statuses
                 else 'synced' if statuses and all(s == 'synced' for s in statuses) else 'waiting' if enterprise and not statuses else 'disabled')
        snapshot = store.snapshot()
        errors = capture_errors(inventory['agents'], snapshot)
        diagnostics.update(errors)
        try:
            stats=store.statistics()
        except Exception as exc:
            diagnostics.report('Capture totals','Stored totals unavailable ('+type(exc).__name__+').')
            stats=None
        try:
            cowork_stats=cowork_metrics.statistics()
        except Exception as exc:
            diagnostics.report('Cowork recall hits','Stored totals unavailable ('+type(exc).__name__+').')
            cowork_stats={'state':'unavailable','groups':[]}
        return {**snapshot, 'errors': errors, 'error_history': diagnostics.snapshot(), **inventory, 'enterprise': enterprise,
                'recalls':recall_receipts(),'stats':stats,'intent_stats':recall.intent_stats,'cowork_stats':cowork_stats,
                "replication": dict(sync.status) if sync else {"state": state}}

    @app.get("/api/events")
    async def events(request: Request):
        async def stream():
            previous = None
            while not await request.is_disconnected():
                snapshot = topology()
                # Full bounded snapshots make reconnects safe without per-browser cursors.
                current = json.dumps([snapshot["revision"], snapshot["errors"], snapshot["replication"],
                                      snapshot['routes'], snapshot['agents'], snapshot['enterprise'], snapshot['error_history'], snapshot['recalls'],snapshot['stats'],snapshot['intent_stats'],snapshot['cowork_stats']])
                if current != previous:
                    yield "data: " + json.dumps(snapshot) + "\n\n"
                    previous = current
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/query")
    async def query(body: QueryBody):
        try:
            return await asyncio.to_thread(sources.query, body.source, body.query, body.parameters)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception as exc:
            raise HTTPException(503, f"Source unavailable ({type(exc).__name__})") from None

    if (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (frontend / "index.html").is_file():
            raise HTTPException(503, "Build the frontend first: cd frontend && npm ci && npm run build")
        return FileResponse(frontend / "index.html")

    return app


class ErrorBody(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    error: str = Field(min_length=1, max_length=4000)


class QueryBody(BaseModel):
    source: str
    query: str = Field(min_length=1, max_length=200000)
    parameters: dict = Field(default_factory=dict)


def main():
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("RELATE_CONSOLE_HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8080")), workers=1)
