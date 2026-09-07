"""Generate a staged host service file. Does not change or run the live host."""
from pathlib import Path
import ast
import hashlib
src=(Path(__file__).parent/'reference/server.py').read_text()
needle='    for name, tgt in CAST_MAP.items():\n        NodeAnimator(BY_NAME[name], tgt["node"], tgt["pixels"]).start()'
replacement='''    # Print Tide owns all seven printer ropes; keep exactly one renderer.
    import sys
    sys.path.insert(0, "/home/edi/printer-light-studio")
    try:
        from light_studio.core import Studio
        from light_studio.web import serve
        def studio_publish(node, payload):
            c = ledwall_client()
            if c is None:
                return False
            return c.publish(f"ledwall/{node}/set", json.dumps(payload, separators=(",", ":"))).rc == 0
        candidate = Studio("/home/edi/printer-light-studio/data", CAST_MAP,
                           lambda: [p.snapshot() for p in PRINTERS], studio_publish,
                           lambda: CAST["enabled"])
        studio_http = serve(candidate, resolve_bind_host("auto"), 8772)
        LIGHT_STUDIO = candidate
        # Rope health: feed controller acks and retained online/offline status
        # into the studio. ledwall_client() may hand back a fresh client after a
        # broker drop, so re-attach whenever the client object changes.
        def _studio_on_message(_c, _u, msg):
            try:
                parts = msg.topic.split("/")
                if len(parts) != 3 or parts[0] != "ledwall":
                    return
                if parts[2] == "status":
                    candidate.observe_node(parts[1], "status", msg.payload.decode("utf-8", "replace"))
                elif parts[2] == "ack":
                    candidate.observe_node(parts[1], "ack", json.loads(msg.payload.decode("utf-8", "replace")))
            except Exception:
                pass
        def _studio_health_thread():
            attached = None
            while True:
                try:
                    c = ledwall_client()
                    if c is not None and c is not attached:
                        c.on_message = _studio_on_message
                        c.subscribe([("ledwall/+/status", 0), ("ledwall/+/ack", 0)])
                        attached = c
                        print("[print-tide] rope health subscribed", flush=True)
                except Exception as e:
                    print(f"[print-tide] rope health subscribe failed: {e}", flush=True)
                time.sleep(2.0)
        threading.Thread(target=_studio_health_thread, daemon=True, name="print-tide-health").start()
        print("Print Tide lighting UI active on port 8772", flush=True)
    except Exception:
        print("Print Tide could not initialize; keeping the original animator", flush=True)
        for name, tgt in CAST_MAP.items():
            NodeAnimator(BY_NAME[name], tgt["node"], tgt["pixels"]).start()'''
assert src.count(needle)==1
src=src.replace(needle,replacement)
assert src.count('CAST_MAP = load_cast_map()')==1
src=src.replace('CAST_MAP = load_cast_map()', 'CAST_MAP = load_cast_map()\nLIGHT_STUDIO = None\n\ndef current_cast_map():\n    return LIGHT_STUDIO.mapping() if LIGHT_STUDIO is not None else CAST_MAP')
src=src.replace('s["cast_target"] = CAST_MAP.get(p.name)','s["cast_target"] = current_cast_map().get(p.name)')
src=src.replace('s["cast_target"] = CAST_MAP.get(pr.name)','s["cast_target"] = current_cast_map().get(pr.name)')
ast.parse(src)
(Path(__file__).parent/'server.staged.py').write_text(src)
print('Staged integration generated; reference SHA256:',hashlib.sha256((Path(__file__).parent/'reference/server.py').read_bytes()).hexdigest())
