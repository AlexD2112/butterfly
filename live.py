"""
In-process pub/sub for multiplayer's live-update layer (SSE) - see NOTES.md, 2026-08-21. Deliberately
tiny: a dict of subspace_id/server_id -> a set of queue.Queue objects, one queue per open SSE
connection. multiplayer.py's message/action/conclusion/rumor-broadcast functions call
publish_subspace_event/publish_server_event right after recording something; server.py's SSE route
subscribes one queue per connection under both its subspace and server, so a single generator loop
only ever needs to drain one queue, not juggle two.

This is in-process only - it works because this whole app is deliberately a single Python process
(no --workers flag anywhere, same assumption the per-account turn lock and the tick scheduler already
make). It would NOT work correctly across multiple worker processes (each process would have its own
disconnected copy of these dicts) - flagging as the same category of constraint already documented
elsewhere, not a new one.

Known, accepted limitation: a connection that disconnects ungracefully (network drop, not a clean
close) relies on the SSE generator's `finally` block eventually running to unsubscribe - if that
doesn't happen promptly, a queue can sit subscribed and unread for a while, growing unboundedly if
events keep arriving. No periodic pruning is implemented. Acceptable at this app's current scale;
worth revisiting if long-lived idle connections turn out to be common.
"""

import queue
import threading

_subspace_subscribers = {}
_server_subscribers = {}
_lock = threading.Lock()

# Bounds how long a client might wait between actual events before getting a heartbeat - keeps
# intermediaries (proxies, the browser itself) from treating a quiet connection as dead.
HEARTBEAT_TIMEOUT_SECONDS = 15


def subscribe(subspace_id, server_id):
    q = queue.Queue()
    with _lock:
        _subspace_subscribers.setdefault(subspace_id, set()).add(q)
        _server_subscribers.setdefault(server_id, set()).add(q)
    return q


def unsubscribe(subspace_id, server_id, q):
    with _lock:
        subs = _subspace_subscribers.get(subspace_id)
        if subs is not None:
            subs.discard(q)
        subs = _server_subscribers.get(server_id)
        if subs is not None:
            subs.discard(q)


def publish_subspace_event(subspace_id, event):
    with _lock:
        subs = list(_subspace_subscribers.get(subspace_id, ()))
    for q in subs:
        q.put(event)


def publish_server_event(server_id, event):
    with _lock:
        subs = list(_server_subscribers.get(server_id, ()))
    for q in subs:
        q.put(event)
