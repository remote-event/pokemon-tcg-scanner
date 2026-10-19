"""Local-only resident scanner. Run python -m tcg_scanner.server --host LAN_IP."""
import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import ipaddress
import json
import logging
import os
from pathlib import Path
import socket
import struct
from time import monotonic
from uuid import UUID
from urllib.parse import urlsplit

# Inference never contacts model hubs or catalog services, including on cold start.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["TCG_OFFLINE"] = "1"
os.environ["YOLO_OFFLINE"] = "true"
os.environ["YOLO_AUTOINSTALL"] = "false"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from .config import Config, default_config_path
from .live import LiveProcessor
from .runtime import Client, Runtime
from .collections import CollectionStore, Conflict

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

log = logging.getLogger("tcg.server")
MAX_FRAME = 1024 * 1024
NETWORKS = tuple(ipaddress.ip_network(s) for s in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))


def local_address(value):
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address):
            address = address.ipv4_mapped or address
        return any(address.version == n.version and address in n for n in NETWORKS)
    except ValueError:
        return False


def default_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # A route lookup only; connect on UDP does not transmit a packet.
        sock.connect(("192.168.0.1", 9))
        return sock.getsockname()[0]



def create_app(config=None, *, host="127.0.0.1", port=8765, advertise=True, processor_factory=None, history_root=None):
    factory = processor_factory or (lambda: LiveProcessor(config or Config.load(default_config_path(), {})))
    runtime = Runtime(factory)

    @asynccontextmanager
    async def lifespan(app):
        zc = None
        try:
            runtime.loop = asyncio.get_running_loop()
            if history_root is not None or config is not None:
                runtime.history = CollectionStore(history_root or config.root / 'scan-history',
                                          config.root if config else None, runtime.history_changed, enable_prices=processor_factory is None)
            runtime.processor = await runtime.call(factory)
            if runtime.history and isinstance(runtime.processor, LiveProcessor):
                await runtime.call(runtime.history.recrop_frames, runtime.processor.session.cropper)
            if advertise and not host.startswith("127."):
                from zeroconf import ServiceInfo
                from zeroconf.asyncio import AsyncZeroconf
                try:
                    zc = AsyncZeroconf(interfaces=[host])
                    info = ServiceInfo("_tcgscanner._tcp.local.", "pokemon-tcg-scanner._tcgscanner._tcp.local.",
                                       addresses=[socket.inet_aton(host)], port=port,
                                       properties={"protocol": "1"}, server="tcg-scanner.local.")
                    await zc.async_register_service(info, allow_name_change=True)
                except Exception:
                    log.exception("Discovery unavailable; use manual IP %s:%s", host, port)
            log.info("Ready at %s:%s (local network only)", host, port)
            yield
        finally:
            if zc:
                await zc.async_close()
            if runtime.processor:
                await runtime.call(runtime.processor.close)
            runtime.visual.shutdown(wait=True, cancel_futures=True)
            if runtime.history:
                runtime.history.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime

    def same_origin(connection):
        authority = connection.headers.get('host', '')
        try:
            hostname = urlsplit('http://' + authority).hostname
            # testserver is the in-process test transport, never accepted in production.
            if not local_address(hostname or '') and not (processor_factory and hostname == 'testserver'):
                return False
        except ValueError:
            return False
        origin = connection.headers.get('origin')
        return (not origin or origin == 'http://' + authority) and connection.headers.get('sec-fetch-site') != 'cross-site'

    @app.middleware("http")
    async def local_only(request, call_next):
        if not request.client or not local_address(request.client.host) or not same_origin(request):
            return JSONResponse({"error": "Local same-origin clients only"}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return JSONResponse({'error': 'JSON required'}, status_code=415)
            if request.url.path == '/api/library/migrate' and request.headers.get('origin'):
                return JSONResponse({'error': 'Native migration only'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def history():
        if runtime.history is None:
            raise HTTPException(503, 'History unavailable')
        return runtime.history

    @app.get('/')
    async def gallery():
        return FileResponse(WEB_ROOT / 'index.html', headers={'Cache-Control': 'no-cache'})

    @app.get('/web/{name}')
    async def web_asset(name: str):
        if name not in ('gallery.js', 'gallery.css'):
            raise HTTPException(404)
        return FileResponse(WEB_ROOT / name, headers={'Cache-Control': 'no-cache'})

    @app.get('/api/prices/collection')
    async def collection_value():
        return await asyncio.to_thread(history().collection_value)

    @app.get('/api/library/pending')
    async def pending_scans():
        return {'rows': await runtime.call(runtime.confirmation_views)}

    @app.get('/api/library/pending/{request_id}/image')
    async def pending_image(request_id: str):
        def get():
            for client in tuple(runtime.clients.values()):
                pending = client.state.pending
                if pending and pending['response']['request_id'] == request_id and monotonic()-pending['created'] < 180:
                    return pending['crop']
        body = await runtime.call(get)
        if not body:
            raise HTTPException(404, 'Scan expired')
        return Response(body, media_type='image/jpeg' if body[:2] == b'\xff\xd8' else 'image/png', headers={'Cache-Control':'no-store'})

    @app.post('/api/library/confirm')
    async def confirm_scan(request: Request):
        body = await read_json(request, 4096)
        if not isinstance(body, dict) or not isinstance(body.get('request_id'), str) or not (body.get('card_id') is None or isinstance(body['card_id'], str)):
            raise HTTPException(400, 'Invalid confirmation')
        try:
            client, result = await runtime.call(runtime.confirm, body['request_id'], body.get('card_id'))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if client:
            await runtime.send(client, {**result, 'type':'confirmation'})
        return result

    @app.get('/api/prices/{card_id}')
    async def card_price(card_id: str):
        try:
            value = await asyncio.to_thread(history().price, card_id)
            if value is None:
                raise HTTPException(503, 'Prices unavailable')
            return value
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get('/api/library')
    async def library_view(collection_id: str = '', before: str = '', query: str = '', limit: int = 40):
        try:
            return await asyncio.to_thread(history().view, collection_id, before, query, limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get('/api/library/export')
    def export_collection(collection_id: str):
        try:
            archive = history().export_zip(collection_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        archive.seek(0, 2)
        size = archive.tell()
        archive.seek(0)

        def chunks():
            try:
                while chunk := archive.read(1024 * 1024):
                    yield chunk
            finally:
                archive.close()

        return StreamingResponse(chunks(), media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="collection-{collection_id}.zip"',
            'Content-Length': str(size), 'Cache-Control': 'no-store',
        }, background=BackgroundTask(archive.close))

    async def read_json(request, limit):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise HTTPException(413, 'Request too large')
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise HTTPException(400, 'Invalid JSON')

    @app.post('/api/library/action')
    async def library_action(request: Request):
        value = await read_json(request, 256*1024)
        try:
            return await asyncio.to_thread(history().action, value)
        except Conflict as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/library/migrate')
    async def migrate_library(request: Request):
        value = await read_json(request, 32*1024*1024)
        try:
            return await asyncio.to_thread(history().migrate_phone, value)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(400, str(exc))

    @app.post('/api/collection/sync')
    @app.post('/api/history/import')
    async def retired_sync():
        raise HTTPException(410, 'Update the Android app to use shared collections')

    @app.get('/api/history')
    async def list_history(before: str = '', limit: int = 40):
        try:
            return await asyncio.to_thread(history().listing, before, limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get('/api/history/changes')
    async def changed_history(after: int = 0):
        return await asyncio.to_thread(history().changes, max(0, after))

    @app.get('/api/history/{scan_id}')
    async def scan_detail(scan_id: str):
        result = await asyncio.to_thread(history().get, scan_id)
        if result is None:
            raise HTTPException(404, 'Scan not saved yet')
        return result

    def image_response(request, path):
        etag = '"' + path.name + '"'
        headers = {'Cache-Control': 'private, max-age=31536000, immutable', 'ETag': etag}
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type='image/jpeg', headers=headers)

    @app.get('/media/{name}')
    async def scan_image(name: str, request: Request):
        try:
            return image_response(request, history().media(name))
        except FileNotFoundError:
            raise HTTPException(404)

    @app.get('/reference/{name}')
    async def reference_image(name: str, request: Request):
        store = history()
        try:
            path = await asyncio.get_running_loop().run_in_executor(store.downloads, store.reference, name)
            return image_response(request, path)
        except FileNotFoundError:
            raise HTTPException(404)
        except Exception:
            return JSONResponse({'error': 'Reference unavailable. Try again shortly.'}, status_code=503,
                                headers={'Cache-Control': 'no-store', 'Retry-After': '60'})

    @app.websocket('/history/events')
    async def history_events(ws: WebSocket):
        if not ws.client or not local_address(ws.client.host) or not same_origin(ws) or len(runtime.watchers) >= 16:
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=1)
        runtime.watchers.add(queue)
        async def send_changes():
            while True:
                await ws.send_json({'type': 'history_changed', 'revision': history().revision()})
                try:
                    await asyncio.wait_for(queue.get(), 15)
                except asyncio.TimeoutError:
                    pass  # Heartbeat also recovers missed notifications after a reconnect.
        async def wait_disconnect():
            while True:
                value = await ws.receive()
                if value['type'] == 'websocket.disconnect':
                    return
        tasks = [asyncio.create_task(send_changes()), asyncio.create_task(wait_disconnect())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            runtime.watchers.discard(queue)
            for task in tasks:
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

    @app.get("/health")
    async def health():
        info = dict(runtime.processor.info)
        reader = getattr(runtime.processor, 'reader', None)
        if reader is not None and not reader.available:
            info['ocr'] = dict(status='degraded', error='Collector OCR unavailable; restart to retry',
                               startup=info.get('ocr'))
        return {"ready": True, **info, "stats": dict(runtime.processor.stats)}

    @app.websocket("/scan")
    async def scan(ws: WebSocket):
        if not ws.client or not local_address(ws.client.host) or ws.headers.get("origin"):
            await ws.close(code=1008)
            return
        await ws.accept()
        client = None
        tasks = []
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            key = str(UUID(hello["session"]))
            if hello.get("protocol") != 1:
                raise ValueError("Unsupported protocol")
            input_mode = hello.get('input_mode', 'frame')
            if input_mode not in ('frame', 'card_crop_v1'):
                raise ValueError('Unsupported image mode')
            threshold = float(hello.get("threshold", .5))
            if not .5 <= threshold <= .95:
                raise ValueError("Invalid threshold")
            aliases = hello.get("resume_aliases", [])
            if not isinstance(aliases, list) or len(aliases) > 10 or any(not isinstance(a, str) or len(a) > 128 for a in aliases):
                raise ValueError("Invalid aliases")
            client = runtime.clients.get(key)
            if client and client.socket is not None:
                await ws.close(code=1013, reason="Session already connected")
                client = None
                return
            if client is None:
                for old_key, old in list(runtime.clients.items()):
                    if old.socket is None and monotonic() - old.touched > 600:
                        runtime.clients.pop(old_key)
                if len(runtime.clients) >= 8:
                    oldest = next((k for k, c in runtime.clients.items() if c.socket is None), None)
                    if oldest is None:
                        await ws.close(code=1013, reason="Server busy")
                        return
                    runtime.clients.pop(oldest)
                client = Client()
                client.state.aliases.update(aliases)
                runtime.clients[key] = client
            client.socket = ws
            client.state.input_mode = input_mode
            client.touched = monotonic()
            await runtime.call(client.state.interrupt_view)
            if client.result and hello.get("resume_id") == client.state.recognition_id:
                await runtime.call(client.state.aliases.update, aliases)
            await runtime.send(client, {"type": "ready", **runtime.processor.info,
                                        'pending': client.state.pending['response'] if client.state.pending else None,
                                        "result": client.result, "interval_ms": 250,
                                        "collections_version": 1, "history_id": runtime.history.instance if runtime.history else None})
            log.info("Phone connected %s session=%s", ws.client.host, key[:8])
            queue = asyncio.Queue(maxsize=1)

            async def receive():
                last_sequence = -1
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    body = message.get("bytes")
                    if body is None or not 8 <= len(body) <= MAX_FRAME + 8:
                        raise ValueError("Expected sequence header and compressed JPEG")
                    sequence = struct.unpack("!Q", body[:8])[0]
                    if sequence <= last_sequence:
                        raise ValueError("Frame sequence must increase")
                    last_sequence = sequence
                    if queue.full():
                        queue.get_nowait()  # Never build an inference backlog.
                    queue.put_nowait((sequence, monotonic(), body[8:]))

            async def consume():
                while True:
                    sequence, received, body = await queue.get()
                    if monotonic() - received > 1.5:
                        await runtime.send(client, {"type": "result", "sequence": sequence, "event": "dropped", "interval_ms": 250})
                        continue
                    try:
                        response = await runtime.call(runtime.process, client, body, threshold, received)
                        response.update(type="result", sequence=sequence)
                        await runtime.send(client, response)
                    except (ValueError, OSError) as exc:
                        await runtime.send(client, {"type": "result", "sequence": sequence, "event": "error", "hint": str(exc), "interval_ms": 500})
                    client.touched = monotonic()

            tasks = [asyncio.create_task(receive()), asyncio.create_task(consume())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            log.warning("Invalid client message: %s", exc)
            with suppress(Exception):
                await ws.close(code=1008, reason="Invalid scanner message")
        except Exception:
            log.exception("Connection failed")
            with suppress(Exception):
                await ws.close(code=1011)
        finally:
            for task in tasks:
                task.cancel()
            if client and client.socket is ws:
                # Reconnect's gate interrupt is serialized after any in-flight work.
                client.socket = None
                client.touched = monotonic()
                await runtime.call(client.state.clear_view)
                log.info("Phone disconnected; presentation retained")
            if tasks:
                with suppress(asyncio.CancelledError):
                    await asyncio.gather(*tasks, return_exceptions=True)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None, help="Private desktop IPv4; defaults to the local route address")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=None, help="Local config, then shared defaults")
    parser.add_argument("--no-discovery", action="store_true")
    parser.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    parser.add_argument("--access-log", action="store_true", help="Log every HTTP request (off by default)")
    parser.add_argument("--history-dir", type=Path, help="Override the durable scan-history directory (also useful for isolated tests)")
    args = parser.parse_args()
    host = args.host or default_address()
    if not local_address(host) or not 1 <= args.port <= 65535:
        parser.error("Bind a specific private IPv4 or loopback address and valid port; wildcard/public binding is disabled")
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # CPU instruction-set probing is useful for debugging, not normal startup.
    logging.getLogger('faiss').setLevel(logging.DEBUG if args.log_level == 'debug' else logging.WARNING)
    import uvicorn
    uvicorn.run(create_app(Config.load(args.config or default_config_path(), {}), host=host, port=args.port, advertise=not args.no_discovery, history_root=args.history_dir),
                host=host, port=args.port, workers=1, proxy_headers=False, ws_max_size=MAX_FRAME + 8,
                ws_max_queue=1, ws_per_message_deflate=False, ws_ping_interval=10, ws_ping_timeout=10,
                log_level=args.log_level, access_log=args.access_log)


if __name__ == "__main__":
    main()
