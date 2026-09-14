#!/usr/bin/env python3
"""
Printer-LED MCP server.

Runs persistently on the Raspberry Pi. Holds live MQTT subscriptions to one or
more Bambu Lab printers (each printer is its own TLS MQTT broker on :8883) and
drives LED displays:

  * "cast" mode paints each printer's print progress onto an LED target --
    a WLED (ESP32) strip over HTTP and/or WS2811 ledwall nodes over MQTT.
  * MCP tools let any connected Claude Code read live printer status, turn on
    WLED effects/patterns, set solid colors, and toggle power.

Transport: Streamable-HTTP on 0.0.0.0:<port> so remote Claude Code instances
(other laptops on the LAN) can connect. All config -- including printer access
codes -- comes from environment variables set by the systemd unit's locked-down
EnvironmentFile. No secrets live in this source file.

Printer config (choose one):
  PRINTERS='[{"name":"x1c","host":"10.106.3.30","serial":"ABC...","code":"12345678"}]'
  -- or the legacy single-printer vars PRINTER_HOST / PRINTER_SERIAL / PRINTER_CODE
     (registered under the name "printer1").
"serial" may be omitted: it is auto-discovered from the printer's report topic.

Cast mapping (which printer paints which ledwall node):
  CAST_MAP='{"x1c":{"node":"node02","pixels":20}}'
  -- default: the first printer -> LEDWALL_NODE (LEDWALL_PIXELS wide).
The WLED strip mirrors WLED_PRINTER (default: the first printer).
"""
import hmac, ipaddress, json, os, ssl, subprocess, threading, time
from collections import deque
import requests
import uvicorn
import paho.mqtt.client as mqtt
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings


def resolve_bind_host(configured):
    """Auto-select the listen address.

    MCP_HOST=auto  -> bind the Tailscale tailnet IP (100.64.0.0/10) if the tunnel
                      is up; otherwise 127.0.0.1 (localhost only -- not exposed to
                      the shared campus LAN). So the server is locked to the tailnet
                      the moment Tailscale comes up, with a safe fallback until then.
    MCP_HOST=<ip>  -> bind exactly that (e.g. 0.0.0.0 to force LAN-wide).
    """
    v = (configured or "auto").strip()
    if v.lower() != "auto":
        return v
    try:
        out = subprocess.check_output(["ip", "-o", "-4", "addr", "show"], text=True, timeout=3)
        cgnat = ipaddress.ip_network("100.64.0.0/10")   # Tailscale's range
        for line in out.splitlines():
            toks = line.split()
            for i, t in enumerate(toks):
                if t == "inet" and i + 1 < len(toks):
                    addr = toks[i + 1].split("/")[0]
                    try:
                        if ipaddress.ip_address(addr) in cgnat:
                            return addr
                    except ValueError:
                        pass
    except Exception:
        pass
    return "127.0.0.1"

# ---- config (env only; no secret defaults in source) -----------------------
WLED_HOST      = os.getenv("WLED_HOST", "").strip()       # e.g. 10.106.x.y or wled-xxxx.local
WLED_PRINTER   = os.getenv("WLED_PRINTER", "").strip()    # printer name; empty = first
MCP_HOST       = resolve_bind_host(os.getenv("MCP_HOST", "auto"))
MCP_PORT       = int(os.getenv("MCP_PORT", "8765"))
MCP_TOKEN      = os.getenv("MCP_TOKEN", "").strip()       # shared passphrase; empty = no auth
CAST_DEFAULT   = os.getenv("CAST_ON_START", "1") == "1"
# ledwall casting defaults (used when CAST_MAP doesn't name a printer)
LEDWALL_CONF   = os.getenv("LEDWALL_CONF", "/home/edi/ledwall-mcp/ledwall.conf")
LEDWALL_NODE   = os.getenv("LEDWALL_NODE", "node02").strip()
LEDWALL_PIXELS = int(os.getenv("LEDWALL_PIXELS", "20"))

# colors by printer state (r,g,b)
COL_PRINT  = [0, 255, 60]     # green = printing (loading-bar style)
COL_DONE   = [0, 255, 60]     # green = finished
COL_ERR    = [255, 30, 0]     # red   = print failed (print_error only, not HMS warnings)
COL_IDLE   = [120, 120, 120]  # grey  = idle
COL_REST   = [255, 0, 0]      # red   = the not-yet-printed remainder of the bar
COL_DROP   = [0, 255, 60]     # raindrop, same green as the water level
COL_SPLASH = [0, 255, 60]     # splash flash when a drop lands (green only, per Edi)
DROP_INTERVAL = 1.8           # seconds between raindrops
DROP_STEP     = 0.06          # seconds per pixel of fall
REPAINT_S     = 10.0          # periodic full-base repaint (heals strip power-cycles,
                              # which leave WS2811 modules stuck on power-on white)
BAR_BRI    = 140              # brightness of the progress bar (0-255)

CAST = {"enabled": CAST_DEFAULT}
_led_count = {"n": None}   # cached WLED LED count


# ---- printers ---------------------------------------------------------------
#: Raw report capture for the telemetry log page: how many reports to keep per
#: printer, and which keys are stripped before a report leaves the Printer.
#: Generous on purpose: a key is dropped if any underscore-separated token is
#: in the set, or if it contains one of the fragments. Access codes never enter
#: a report, but serial numbers, addresses and camera URLs do.
REPORT_LOG = 400
_REDACT_TOKENS = {"sn", "serial", "access", "code", "passwd", "password", "passw",
                  "ssid", "ip", "mac", "url", "rtsp", "ipcam", "token", "key",
                  "secret", "uid", "uuid", "host", "addr"}
_REDACT_FRAGMENTS = ("access", "passw", "rtsp", "ipcam", "serial", "secret", "token")

def redact_report(value):
    """Recursively drop sensitive-looking keys from a Bambu report payload."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k)
            low = key.lower()
            if any(f in low for f in _REDACT_FRAGMENTS) or \
               any(tok in _REDACT_TOKENS for tok in low.split("_")):
                continue
            out[key] = redact_report(v)
        return out
    if isinstance(value, list):
        return [redact_report(v) for v in value]
    return value


class Printer:
    """One Bambu printer: config + live status + its own MQTT reconnect loop."""

    def __init__(self, name, host, code, serial=""):
        self.name, self.host, self.code = name, host, code
        self.serial = serial or ""          # may be discovered from the report topic
        self.lock = threading.Lock()
        self.status = {
            "percent": None, "state": None, "job": None,
            "layer": None, "total_layer": None, "remaining_min": None,
            "nozzle": None, "bed": None, "has_error": False,
            "updated": None, "mqtt_connected": False,
        }
        self.cast_last = None               # last (lit, color) painted on its node
        self.reports = deque(maxlen=REPORT_LOG)   # (time, redacted raw "print" dict)

    def raw_reports(self, since=None, limit=REPORT_LOG):
        """Recent redacted report payloads as ``[(epoch_seconds, dict), ...]``."""
        with self.lock:
            items = list(self.reports)
        if since is not None:
            items = [item for item in items if item[0] > since]
        return items[-int(limit):] if limit else items

    def snapshot(self):
        with self.lock:
            s = dict(self.status)
        s["name"], s["host"], s["serial"] = self.name, self.host, self.serial or None
        return s

    # -- mqtt callbacks --
    def _on_connect(self, c, u, flags, rc, props=None):
        ok = getattr(rc, "value", rc) == 0
        self.status["mqtt_connected"] = ok
        if not ok:
            return
        if self.serial:
            c.subscribe(f"device/{self.serial}/report", qos=0)
            c.publish(f"device/{self.serial}/request",
                      json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}))
        else:
            c.subscribe("device/+/report", qos=0)   # serial unknown: discover it

    def _on_disconnect(self, c, u, *a):
        self.status["mqtt_connected"] = False

    def _on_message(self, c, u, msg):
        if not self.serial and msg.topic.startswith("device/"):
            parts = msg.topic.split("/")
            if len(parts) >= 3:
                self.serial = parts[1]
                c.publish(f"device/{self.serial}/request",
                          json.dumps({"pushing": {"sequence_id": "1", "command": "pushall"}}))
        try:
            p = json.loads(msg.payload).get("print", {})
        except Exception:
            return
        if not isinstance(p, dict):
            return
        st = self.status
        with self.lock:
            self.reports.append((time.time(), redact_report(p)))
            if p.get("mc_percent") is not None:        st["percent"] = p["mc_percent"]
            if p.get("gcode_state") is not None:        st["state"] = p["gcode_state"]
            if p.get("subtask_name"):                   st["job"] = p["subtask_name"]
            if p.get("layer_num") is not None:          st["layer"] = p["layer_num"]
            if p.get("total_layer_num") is not None:    st["total_layer"] = p["total_layer_num"]
            if p.get("mc_remaining_time") is not None:  st["remaining_min"] = p["mc_remaining_time"]
            if p.get("nozzle_temper") is not None:      st["nozzle"] = p["nozzle_temper"]
            if p.get("bed_temper") is not None:         st["bed"] = p["bed_temper"]
            # HMS entries are often mere warnings (AMS, filament) -- only a real
            # print_error should turn the bar red.
            if p.get("print_error") is not None:        st["has_error"] = p["print_error"] != 0
            # Speed profile: spd_lvl 1 Silent, 2 Standard, 3 Sport, 4 Ludicrous;
            # spd_mag is the matching feed-rate percentage (50/100/124/166).
            if p.get("spd_lvl") is not None:            st["speed_level"] = p["spd_lvl"]
            if p.get("spd_mag") is not None:            st["speed_percent"] = p["spd_mag"]
            # home_flag bit 23 is the enclosure door on X1/H2D-class machines
            # (printers without a door sensor simply never set it).
            if isinstance(p.get("home_flag"), int):     st["door_open"] = bool(p["home_flag"] & (1 << 23))
            st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def mqtt_thread(self):
        while True:
            try:
                cl = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=f"pi-led-mcp-{self.name}", protocol=mqtt.MQTTv311)
                cl.username_pw_set("bblp", self.code)
                cl.tls_set(cert_reqs=ssl.CERT_NONE, tls_version=ssl.PROTOCOL_TLS_CLIENT)
                cl.tls_insecure_set(True)
                cl.on_connect, cl.on_message, cl.on_disconnect = \
                    self._on_connect, self._on_message, self._on_disconnect
                cl.connect(self.host, 8883, keepalive=60)
                cl.loop_forever()
            except Exception:
                self.status["mqtt_connected"] = False
                time.sleep(5)   # reconnect backoff


def load_printers():
    js = os.getenv("PRINTERS", "").strip()
    out, seen = [], set()
    if js:
        for cfg in json.loads(js):
            name = str(cfg["name"]).strip()
            if not name or name in seen:
                raise ValueError(f"duplicate/empty printer name: {name!r}")
            seen.add(name)
            out.append(Printer(name, cfg["host"], cfg["code"], cfg.get("serial", "")))
    elif os.getenv("PRINTER_HOST", ""):
        out.append(Printer("printer1", os.getenv("PRINTER_HOST"),
                           os.getenv("PRINTER_CODE", ""), os.getenv("PRINTER_SERIAL", "")))
    return out

PRINTERS = load_printers()
BY_NAME = {p.name: p for p in PRINTERS}

def load_cast_map():
    """printer name -> {node, pixels}. Defaults the first printer to LEDWALL_NODE."""
    js = os.getenv("CAST_MAP", "").strip()
    if js:
        m = json.loads(js)
        return {n: {"node": v["node"], "pixels": int(v.get("pixels", LEDWALL_PIXELS))}
                for n, v in m.items() if n in BY_NAME}
    if PRINTERS and LEDWALL_NODE:
        return {PRINTERS[0].name: {"node": LEDWALL_NODE, "pixels": LEDWALL_PIXELS}}
    return {}

CAST_MAP = load_cast_map()

def wled_printer():
    if WLED_PRINTER and WLED_PRINTER in BY_NAME:
        return BY_NAME[WLED_PRINTER]
    return PRINTERS[0] if PRINTERS else None


# ---- WLED helpers ----------------------------------------------------------
def wled_url(path):
    return f"http://{WLED_HOST}{path}"

def wled_post_state(payload):
    if not WLED_HOST:
        return {"ok": False, "error": "WLED_HOST not configured"}
    try:
        r = requests.post(wled_url("/json/state"), json=payload, timeout=4)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def wled_get(path):
    if not WLED_HOST:
        return None
    try:
        return requests.get(wled_url(path), timeout=4).json()
    except Exception:
        return None

def led_count():
    if _led_count["n"] is None:
        info = wled_get("/json/info")
        try:
            _led_count["n"] = int(info["leds"]["count"])
        except Exception:
            return 30  # fallback guess until WLED reachable (not cached)
    return _led_count["n"]

def bar_shape(pr, n):
    """(lit, color) for printer pr on an n-pixel bar."""
    with pr.lock:
        pct, state, err = pr.status["percent"], pr.status["state"], pr.status["has_error"]
        conn = pr.status["mqtt_connected"]
    if not conn:
        return n, COL_IDLE   # printer unreachable: full gray bar, never a stale percent
    pct = 0 if pct is None else pct
    lit = max(0, min(n, round(n * pct / 100.0)))
    if err:                        color = COL_ERR
    elif state == "RUNNING":       color = COL_PRINT
    elif state == "FINISH":        color = COL_DONE
    else:                          color = COL_IDLE
    return lit, color

def paint_progress():
    """WLED strip: light count*percent LEDs in the state color, rest off."""
    pr = wled_printer()
    if pr is None:
        return {"ok": False, "error": "no printers configured"}
    n = led_count()
    lit, color = bar_shape(pr, n)
    seg_i = [0, lit, color, lit, n, COL_REST] if lit < n else [0, n, color]
    return wled_post_state({"on": True, "bri": BAR_BRI, "seg": [{"fx": 0, "i": seg_i}]})


# ---- LED-wall node casting (WS2811 nodes via the ledwall broker) -------------
_lw = {"client": None, "lock": threading.Lock()}   # persistent broker client, shared by all node targets

def _ledwall_creds():
    """Parse the mosquitto-style options file (same one the ledwall MCP uses).
    Returns (host, port, user, pw) or None. Credentials are never logged."""
    try:
        st = os.stat(LEDWALL_CONF)
    except OSError:
        return None
    if st.st_mode & 0o077:
        return None   # refuse group/world-readable credentials
    opts = {}
    with open(LEDWALL_CONF) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            opts[parts[0].lstrip("-")] = parts[1].strip() if len(parts) > 1 else ""
    host, user = opts.get("host"), opts.get("username")
    pw = opts.get("pw", opts.get("password"))
    if not host or not user or not pw:
        return None
    return host, int(opts.get("port", "1883")), user, pw

def ledwall_client():
    with _lw["lock"]:
        c = _lw["client"]
        if c is not None and c.is_connected():
            return c
        creds = _ledwall_creds()
        if not creds:
            return None
        if c is not None:
            # tear down the dead client fully or its loop thread + socketpair leak
            try:
                c.loop_stop()
                c.disconnect()
            except Exception:
                pass
            _lw["client"] = None
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                            client_id=f"pi-led-mcp-lw-{os.getpid()}")
            c.username_pw_set(creds[2], creds[3])
            c.connect(creds[0], creds[1], keepalive=30)
            c.loop_start()
            # wait for CONNACK so a second animator thread does not judge this
            # client dead and trigger a create/kick storm
            for _ in range(50):
                if c.is_connected():
                    break
                time.sleep(0.02)
            _lw["client"] = c
            print("[ledwall] broker client connected", flush=True)
            return c
        except Exception as e:
            print(f"[ledwall] broker connect failed: {e}", flush=True)
            return None

class NodeAnimator(threading.Thread):
    """Drives one WS2811 node as a 'bucket filling with rain': the green water
    level is the print %, the remainder is red, and while the print RUNs a blue
    drop falls from the top into the water with a small splash. All publishes
    are fire-and-forget (QoS 0); the base bar repaints whenever % or state
    changes, which also wipes any in-flight drop cleanly."""

    def __init__(self, pr, node, pixels):
        super().__init__(daemon=True, name=f"anim-{node}")
        self.pr, self.node, self.pixels = pr, node, pixels
        self.topic = f"ledwall/{node}/set"

    def _pub(self, c, payload):
        c.publish(self.topic, json.dumps(payload, separators=(",", ":")))

    def _px(self, c, i, rgb):
        self._pub(c, {"op": "pixel", "index": i, "rgb": list(rgb)})

    def _paint_base(self, c, lit, color):
        if lit > 0:
            self._pub(c, {"op": "range", "start": 0, "count": lit, "rgb": color})
        if lit < self.pixels:
            self._pub(c, {"op": "range", "start": lit,
                          "count": self.pixels - lit, "rgb": COL_REST})

    def run(self):
        last_base = None
        drop = None                      # current drop position, or None
        next_drop = time.monotonic() + 1.0
        next_repaint = 0.0               # periodic repaint deadline
        while True:
            if not CAST["enabled"]:
                last_base, drop = None, None   # full repaint when re-enabled
                time.sleep(0.5)
                continue
            c = ledwall_client()
            if c is None:
                time.sleep(3)
                continue
            lit, color = bar_shape(self.pr, self.pixels)
            with self.pr.lock:
                state = self.pr.status["state"]
            base = (lit, tuple(color))
            now = time.monotonic()
            if base != last_base or now >= next_repaint:
                self._paint_base(c, lit, color)
                if base != last_base:
                    drop = None
                elif drop is not None:
                    self._px(c, drop, COL_DROP)   # keep in-flight drop visible
                last_base, next_repaint = base, now + REPAINT_S
            raining = state == "RUNNING" and lit < self.pixels
            if drop is None:
                if raining and now >= next_drop:
                    drop = self.pixels - 1
                    self._px(c, drop, COL_DROP)
            else:
                self._px(c, drop, color if drop < lit else COL_REST)  # restore behind
                drop -= 1
                if drop < lit:            # reached the water line: splash + absorb
                    if lit > 0:
                        self._px(c, lit - 1, COL_SPLASH)
                        time.sleep(0.12)
                        self._px(c, lit - 1, color)
                    drop = None
                    next_drop = time.monotonic() + DROP_INTERVAL
                else:
                    self._px(c, drop, COL_DROP)
            time.sleep(DROP_STEP)


def cast_tick():
    """WLED mirror only; the ledwall nodes are driven by their animators."""
    return {"wled": paint_progress()} if WLED_HOST else {}

def cast_thread():
    while True:
        if CAST["enabled"]:
            cast_tick()
        time.sleep(3)


# ---- MCP server + tools ----------------------------------------------------
tss = TransportSecuritySettings(enable_dns_rebinding_protection=False)
server = MCPServer(
    name="printer-led",
    title="Printer LED (Bambu -> LEDs)",
    instructions="Cast Bambu print progress to LED strips (WLED + WS2811 nodes) "
                 "and drive LED patterns. Multiple printers supported by name.",
)

def _resolve_printer(name):
    if name:
        return BY_NAME.get(name.strip())
    return PRINTERS[0] if len(PRINTERS) == 1 else None

@server.tool(description="List configured printers: name, host, serial, connection state, cast target.")
def list_printers() -> list:
    out = []
    for p in PRINTERS:
        s = p.snapshot()
        s["cast_target"] = CAST_MAP.get(p.name)
        out.append(s)
    return out

@server.tool(description="Live status of a Bambu printer (percent, state, job, temps). "
                         "Omit 'printer' when only one is configured; otherwise pass its name.")
def get_print_status(printer: str | None = None) -> dict:
    pr = _resolve_printer(printer)
    if pr is None:
        return {"error": "unknown or ambiguous printer; use list_printers",
                "printers": sorted(BY_NAME)}
    s = pr.snapshot()
    s["cast_enabled"] = CAST["enabled"]
    s["cast_target"] = CAST_MAP.get(pr.name)
    s["wled_host"] = WLED_HOST or None
    return s

@server.tool(description="Turn progress-casting on/off. When on, each mapped LED target "
                         "fills to its printer's live print %.")
def cast_progress(enabled: bool) -> dict:
    CAST["enabled"] = enabled
    if enabled:
        for p in PRINTERS:
            p.cast_last = None   # force a repaint even if the bar hasn't moved
        return {"cast_enabled": True, "painted": cast_tick()}
    return {"cast_enabled": False}

@server.tool(description="Set a WLED effect pattern by name or numeric id. Stops progress-casting. "
                         "Optional palette (name/id) and brightness 0-255.")
def set_pattern(effect: str, palette: str | None = None, brightness: int | None = None) -> dict:
    CAST["enabled"] = False
    effects = wled_get("/json/effects") or []
    palettes = wled_get("/json/palettes") or []
    def resolve(val, names):
        if val is None:
            return None
        if isinstance(val, str) and val.isdigit():
            return int(val)
        for i, nm in enumerate(names):
            if isinstance(val, str) and nm.lower() == val.lower():
                return i
        try:
            return int(val)
        except Exception:
            return None
    fx = resolve(effect, effects)
    if fx is None:
        return {"ok": False, "error": f"unknown effect '{effect}'", "available_sample": effects[:20]}
    seg = {"fx": fx}
    pal = resolve(palette, palettes) if palette else None
    if pal is not None:
        seg["pal"] = pal
    body = {"on": True, "seg": [seg]}
    if brightness is not None:
        body["bri"] = max(0, min(255, brightness))
    r = wled_post_state(body)
    return {"ok": r.get("ok"),
            "effect": effects[fx] if fx < len(effects) else fx,
            "palette": (palettes[pal] if pal is not None and pal < len(palettes) else pal),
            "raw": r}

@server.tool(description="Set the whole WLED strip to a solid RGB color. Stops progress-casting.")
def set_color(r: int, g: int, b: int, brightness: int | None = None) -> dict:
    CAST["enabled"] = False
    body = {"on": True, "seg": [{"fx": 0, "col": [[max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))]]}]}
    if brightness is not None:
        body["bri"] = max(0, min(255, brightness))
    return wled_post_state(body)

@server.tool(description="Turn the WLED strip power on or off.")
def set_power(on: bool) -> dict:
    return wled_post_state({"on": bool(on)})

@server.tool(description="List available WLED effect names (index = id).")
def list_effects() -> list:
    return wled_get("/json/effects") or ["<WLED unreachable or not configured>"]

@server.tool(description="List available WLED palette names (index = id).")
def list_palettes() -> list:
    return wled_get("/json/palettes") or ["<WLED unreachable or not configured>"]

@server.tool(description="WLED device info (LED count, version, name, signal).")
def wled_info() -> dict:
    return wled_get("/json/info") or {"error": "WLED unreachable or WLED_HOST not set"}


class TokenAuth:
    """ASGI middleware: require the shared passphrase on the MCP endpoint.

    Accepts either  Authorization: Bearer <token>  or  X-Auth-Token: <token>.
    Constant-time compared. If MCP_TOKEN is unset, auth is disabled (open).
    """
    def __init__(self, app, token, prefix="/mcp"):
        self.app, self.token, self.prefix = app, token, prefix

    async def __call__(self, scope, receive, send):
        if self.token and scope.get("type") == "http" and scope.get("path", "").startswith(self.prefix):
            hdr = {k.lower(): v for k, v in (scope.get("headers") or [])}
            bearer = hdr.get(b"authorization", b"").decode()
            xtok = hdr.get(b"x-auth-token", b"").decode()
            presented = bearer[7:] if bearer.startswith("Bearer ") else xtok
            if not (presented and hmac.compare_digest(presented, self.token)):
                body = b'{"error":"unauthorized: missing or wrong passphrase"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    for p in PRINTERS:
        threading.Thread(target=p.mqtt_thread, daemon=True).start()
    threading.Thread(target=cast_thread, daemon=True).start()
    for name, tgt in CAST_MAP.items():
        NodeAnimator(BY_NAME[name], tgt["node"], tgt["pixels"]).start()
    reach = ("campus-LAN" if MCP_HOST == "0.0.0.0" else
             "tailnet-only" if MCP_HOST.startswith("100.") else
             "localhost-only" if MCP_HOST in ("127.0.0.1", "::1") else "as-configured")
    casts = ", ".join(f"{n}->{t['node']}" for n, t in CAST_MAP.items()) or "none"
    print(f"printer-led MCP  ->  http://{MCP_HOST}:{MCP_PORT}/mcp   "
          f"[{reach} · auth={'ON' if MCP_TOKEN else 'OFF'}]   "
          f"printers={[p.name for p in PRINTERS] or 'NONE'} · casts: {casts} · "
          f"WLED={WLED_HOST or 'UNSET'}", flush=True)
    app = TokenAuth(server.streamable_http_app(host=MCP_HOST, transport_security=tss), MCP_TOKEN)
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")
