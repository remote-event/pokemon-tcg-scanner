"""Resident scan sessions, serialized inference and durable recognition commits."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from time import monotonic

from fastapi import WebSocketDisconnect
from .live import LiveState


@dataclass
class Client:
    state: LiveState = field(default_factory=LiveState)
    socket: object = None
    touched: float = field(default_factory=monotonic)
    result: dict | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Runtime:
    def __init__(self, factory):
        self.factory = factory
        self.visual = ThreadPoolExecutor(1, thread_name_prefix="tcg-visual")
        self.clients = OrderedDict()
        self.processor = None
        self.history = None
        self.loop = None
        self.watchers = set()

    def history_changed(self):
        def notify():
            for queue in self.watchers:
                if not queue.full():
                    queue.put_nowait(True)
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(notify)

    async def call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.visual, fn, *args)

    async def send(self, client, message):
        async with client.send_lock:
            ws = client.socket
            if ws is not None:
                try:
                    await asyncio.wait_for(ws.send_json(message), 3)
                except (RuntimeError, WebSocketDisconnect, OSError, asyncio.TimeoutError):
                    with suppress(Exception):
                        await ws.close()

    def process(self, client, body, threshold, received):
        # Check age on the owning inference thread, after waiting for other phones.
        if monotonic() - received > 1.5:
            return {"event": "dropped", "interval_ms": 250}
        # Selection and recognition commit share a lock: deletion/switching cannot
        # orphan an in-flight scan or assign it to a collection after deletion.
        with self.history.lock if self.history else nullcontext():
            if self.history and not self.history.active():
                return {"event": "waiting", "hint": "Choose a collection to start scanning", "interval_ms": 500}
            response = self.processor.process(client.state, body, threshold)
            card_crop = response.pop("_card_crop", None)  # Internal pixels never enter WebSocket JSON.
            # Commit before returning across the async boundary: disconnect/cancellation
            # cannot lose a recognition that has already latched the presentation.
            if response["event"] == "recognized":
                client.result = dict(response)
                if self.history:
                    self.history.save(response, card_crop)
            elif response['event'] == 'confirmation':
                client.state.pending = dict(response=dict(response), crop=card_crop,
                    collection_id=self.history.active() if self.history else None, created=monotonic())
                self.history_changed()
            elif response["event"] == "removed":
                client.result = None
                client.state.pending = None
                self.history_changed()
            return response

    def confirmation_views(self):
        rows = []
        for client in tuple(self.clients.values()):
            pending = client.state.pending
            if pending and monotonic()-pending['created'] < 180:
                if self.history and pending['collection_id'] == self.history.active():
                    rows.append({**pending['response'], 'collection_id':pending['collection_id']})
        return rows

    def confirm(self, request_id, card_id):
        with self.history.lock:
            existing = self.history.get(request_id)
            if existing:
                if existing['card']['id'] != card_id:
                    raise ValueError('This scan was already confirmed as another card')
                return None, dict(event='recognized', recognition_id=request_id, matches=existing['matches'])
            for client in tuple(self.clients.values()):
                pending = client.state.pending
                if not pending or pending['response']['request_id'] != request_id:
                    continue
                if monotonic()-pending['created'] >= 180 or self.history.active() != pending['collection_id']:
                    client.state.pending = None
                    raise ValueError('Scan expired or collection changed. Present the card again.')
                result = dict(pending['response'])
                if card_id is None:
                    if client.state.uncertain_signature is not None:
                        client.state.gate.accept(client.state.uncertain_signature)
                    client.state.reset_uncertainty()
                    client.state.pending = None
                    self.history_changed()
                    return client, dict(event='dismissed', request_id=request_id)
                matches = result['matches']
                if not any(m['card']['id'] == card_id for m in matches):
                    raise ValueError('Choose one of the proposed cards')
                result.update(event='recognized', recognition_id=request_id, checking=False,
                    verified=False, verification='user_confirmed', hint='',
                    matches=sorted(matches, key=lambda m:m['card']['id'] != card_id))
                self.history.save(result, pending['crop'])
                if client.state.uncertain_signature is not None:
                    client.state.gate.accept(client.state.uncertain_signature)
                client.state.reset_uncertainty()
                client.state.recognition_id = request_id
                client.state.aliases = {card_id}
                client.state.pending = None
                client.result = result
                self.history_changed()
                return client, result
            raise ValueError('This scan is no longer awaiting confirmation')
