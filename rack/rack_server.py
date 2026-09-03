"""Local HTTP server for the mechatronics rack page.

The browser never publishes MQTT. Every light command goes through here.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from rack.inventory_store import InventoryError
from rack.mqtt_transport import MqttTransport, RecordingTransport, TransportError, load_client_config
from rack.rack_config import RackConfigError, load_rack_config
from rack.rack_service import RackService

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
MAX_BODY_BYTES = 2_000_000


class OperatorAuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class RackHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, handler, service: RackService, static_root: Path):
        super().__init__(address, handler)
        self.service = service
        self.static_root = Path(static_root).resolve()


class RackHandler(BaseHTTPRequestHandler):
    server: RackHTTPServer

    def log_message(self, format, *args):  # noqa: A002 - matches BaseHTTPRequestHandler API
        return

    def _send_json(self, status, payload):
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _send_bytes(self, status, content_type, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_operator(self):
        expected = os.environ.get("RACK_OPERATOR_TOKEN")
        if not expected:
            raise OperatorAuthError(503, "operator_token_not_configured")
        supplied = self.headers.get("X-Rack-Operator", "")
        if not hmac.compare_digest(supplied, expected):
            raise OperatorAuthError(401, "operator_token_invalid")
        return "operator"

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body must be between 1 byte and 2 MB")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _static(self, path: str):
        relative = "rack.html" if path in ("", "/") else path.lstrip("/")
        candidate = (self.server.static_root / relative).resolve()
        try:
            candidate.relative_to(self.server.static_root)
        except ValueError:
            self._send_json(403, {"error": "path outside rack root"})
            return
        if not candidate.is_file() or candidate.suffix not in STATIC_TYPES:
            self._send_json(404, {"error": "not found"})
            return
        self._send_bytes(200, STATIC_TYPES[candidate.suffix], candidate.read_bytes())

    def do_GET(self):  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        service = self.server.service
        try:
            if path == "/api/rack":
                self._send_json(200, service.snapshot())
                return
            if path == "/api/search":
                results = service.search(
                    query=(query.get("q", [""])[0]),
                    category=(query.get("category", [None])[0]),
                    availability=(query.get("availability", [None])[0]),
                )
                self._send_json(200, {"results": results, "count": len(results)})
                return
            if path == "/api/health":
                self._send_json(
                    200, {"ok": True, "endpoint_availability": service.snapshot()["endpoint_availability"]}
                )
                return
            if path == "/api/audit":
                self._require_operator()
                limit = int(query.get("limit", ["20"])[0])
                self._send_json(200, {"entries": service.audit_tail(max(1, min(limit, 200)))})
                return
            if path == "/api/inventory/export.csv":
                self._require_operator()
                self._send_bytes(200, "text/csv; charset=utf-8", service.export_csv().encode("utf-8"))
                return
            self._static(path)
        except OperatorAuthError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except (InventoryError, RackConfigError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
        except TransportError as exc:
            self._send_json(502, {"error": str(exc)})
        except OSError as exc:
            self._send_json(500, {"error": f"rack page unavailable: {exc}"})

    def do_POST(self):  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        service = self.server.service
        try:
            if path == "/api/locate":
                payload = self._body()
                item_ids = payload.get("item_ids")
                if not isinstance(item_ids, list) or not all(isinstance(entry, str) for entry in item_ids):
                    raise ValueError("item_ids must be a list of strings")
                ttl = payload.get("ttl_seconds")
                if ttl is not None and (type(ttl) is not int or not 1 <= ttl <= 300):
                    raise ValueError("ttl_seconds must be between 1 and 300")
                self._send_json(200, service.locate(item_ids, ttl_seconds=ttl))
                return
            if path == "/api/locate/clear":
                self._send_json(200, service.clear_highlight())
                return
            if path == "/api/preview":
                self._require_operator()
                bin_id = self._body().get("bin_id")
                if not isinstance(bin_id, str):
                    raise ValueError("bin_id must be a string")
                self._send_json(200, service.preview_bin(bin_id))
                return
            if path == "/api/inventory/update":
                actor = self._require_operator()
                self._send_json(200, service.update_inventory(self._body(), actor=actor))
                return
            if path == "/api/inventory/import":
                actor = self._require_operator()
                payload = self._body()
                text = payload.get("csv")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("csv must be a non-empty string")
                self._send_json(
                    200, service.import_inventory(text, actor=actor, apply=bool(payload.get("apply")))
                )
                return
            self._send_json(404, {"error": "not found"})
        except OperatorAuthError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except (InventoryError, RackConfigError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
        except TransportError as exc:
            self._send_json(502, {"error": str(exc)})
        except OSError as exc:
            self._send_json(500, {"error": f"could not apply change: {exc}"})


def create_rack_server(host: str, port: int, service: RackService, static_root: Path | None = None) -> RackHTTPServer:
    root = Path(static_root or Path(__file__).resolve().parent / "static")
    return RackHTTPServer((host, port), RackHandler, service, root)


def _expiry_loop(service: RackService, stop: threading.Event) -> None:
    while not stop.wait(1.0):
        service.tick()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "rack")
    parser.add_argument("--config", type=Path, default=None, help="rack/cabinet config JSON; defaults to <data-dir>/rack-01.json")
    parser.add_argument("--client-config", type=Path, default=None, help="broker credential JSON; omit for dry run")
    args = parser.parse_args()

    config = load_rack_config(args.config or args.data_dir / "rack-01.json")
    if args.client_config:
        transport = MqttTransport(load_client_config(args.client_config), config)
    else:
        transport = RecordingTransport()
        print("no --client-config: running dry, no MQTT commands leave this process", flush=True)

    service = RackService(config, args.data_dir / "inventory.json", args.data_dir / "audit.jsonl", transport)
    server = create_rack_server(args.host, args.port, service)
    stop = threading.Event()
    sweeper = threading.Thread(target=_expiry_loop, args=(service, stop), daemon=True)
    sweeper.start()
    print(f"Mechatronics rack page: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
