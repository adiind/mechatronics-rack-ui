"""Gemini-backed chat over the verified rack inventory.

The browser talks only to this server; the Gemini key is read from an
ignored env file on the Pi and never leaves the process or appears in an
error string. The model is asked for a structured answer whose item ids are
then re-validated against the inventory record, so a hallucinated part can
never be shown as "in the rack" or light a bin.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "gemini-3.6-flash"
# Tried in order when the primary is overloaded (503/429) or retired (404).
DEFAULT_FALLBACK_MODELS = ("gemini-3.5-flash", "gemini-flash-latest")
# One quick retry per model; the fallback chain, not long backoff, is the remedy for a 503 spike.
RETRY_DELAYS_SECONDS = (1.0,)
DEFAULT_ENV_PATH = Path("~/.config/mechatronics-rack-ui/gemini.env")
MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_TURNS = 10
MAX_NOTE_CHARS = 160
REQUEST_TIMEOUT_SECONDS = 40

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "Plain-language answer for the visitor. Markdown is not rendered."},
        "item_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "item_id values from the inventory list that answer the request. Empty if none.",
        },
        "light": {"type": "boolean", "description": "true when the visitor would benefit from the matching bins lighting up."},
    },
    "required": ["reply", "item_ids", "light"],
}

SYSTEM_PROMPT = """You are the assistant for a mechatronics parts rack in a shared workshop.
You will be given the complete verified inventory as a JSON list. Each entry has an item_id,
a display_name, a category, a quantity, an availability, and either a bin location like
"rack-01/bin-07" or the phrase "not in a mapped bin".

Rules:
- Only ever mention parts that appear in the inventory list, and refer to them by display_name.
  Never invent a part, a quantity, or a location. If nothing fits, say so plainly.
- When a visitor asks where something is, answer with the bin location(s). If an item has no
  mapped bin, say it is stocked but not yet placed in a numbered bin.
- When a visitor describes a robot or project, propose a build using only parts from the list:
  name each part, its bin, and its role in the build, then give brief wiring/assembly guidance
  (a few short steps). Clearly list anything the build needs that this rack does not stock.
- Put the item_id of every inventory part you recommend or locate into item_ids, in order of
  importance. Set light to true when you named at least one part with a mapped bin, so the rack
  can light those bins for the visitor. Set light to false for small talk or when nothing matched.
- Keep replies compact: a short paragraph or a few short lines. No markdown headings or tables.
"""


class ChatError(RuntimeError):
    """A stable, user-facing chat error. Never carries the API key."""


def load_gemini_key(path: Path | None = None) -> str | None:
    """Return the key from GEMINI_API_KEY in the environment or the env file, else None."""
    direct = os.environ.get("GEMINI_API_KEY", "").strip()
    if direct:
        return direct
    env_path = Path(path or DEFAULT_ENV_PATH).expanduser()
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if key == "GEMINI_API_KEY":
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


class GeminiClient:
    """Minimal generateContent client using only the standard library.

    A 503/429 from the primary model is retried with a short backoff, then the
    fallback models are tried in order; a 404 (model retired) skips straight to
    the next model. `last_model` records which one actually answered.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        fallback_models: tuple[str, ...] | list[str] = DEFAULT_FALLBACK_MODELS,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        retry_delays: tuple[float, ...] = RETRY_DELAYS_SECONDS,
        sleep=time.sleep,
    ):
        if not api_key:
            raise ChatError("chat_not_configured")
        self._api_key = api_key
        self.model = model
        self.fallback_models = [name for name in fallback_models if name and name != model]
        self.last_model: str | None = None
        self._timeout = timeout
        self._retry_delays = tuple(retry_delays)
        self._sleep = sleep

    def _request_once(self, model: str, body: dict) -> dict:
        """One HTTP call. Raises ChatError with a stable code; logs the upstream reason."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Operators need the upstream reason (quota, bad model, schema) in the
            # server log. The key travels in a header, so the body never holds it.
            detail = " ".join(exc.read(2000).decode("utf-8", errors="replace").split())[:400]
            print(f"chat upstream HTTP {exc.code} from {model}: {detail}", file=sys.stderr, flush=True)
            if exc.code == 404:
                raise ChatError("chat_model_unavailable") from None
            if exc.code == 429 or exc.code >= 500:
                raise ChatError("chat_upstream_busy") from None
            raise ChatError("chat_upstream_rejected") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ChatError("chat_upstream_unreachable") from None

    def generate(self, system: str, contents: list[dict], schema: dict) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        last_error = ChatError("chat_upstream_busy")
        for model in [self.model, *self.fallback_models]:
            for attempt in range(len(self._retry_delays) + 1):
                try:
                    payload = self._request_once(model, body)
                except ChatError as exc:
                    last_error = exc
                    code = str(exc)
                    if code == "chat_model_unavailable":
                        break  # retired model: move on to the next one immediately
                    if code == "chat_upstream_rejected":
                        raise
                    if attempt < len(self._retry_delays):
                        self._sleep(self._retry_delays[attempt])
                    continue
                self.last_model = model
                return self._parse(payload)
        raise last_error

    @staticmethod
    def _parse(payload: dict) -> dict:
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            answer = json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ChatError("chat_upstream_unparseable") from None
        if not isinstance(answer, dict):
            raise ChatError("chat_upstream_unparseable")
        return answer


def inventory_context(inventory: dict) -> list[dict]:
    """The grounding list handed to the model: every item, compact, nothing hidden."""
    entries = []
    for item in inventory["items"]:
        note = " ".join(item.get("notes", "").split())
        if len(note) > MAX_NOTE_CHARS:
            note = note[: MAX_NOTE_CHARS - 1].rstrip() + "…"
        entries.append(
            {
                "item_id": item["item_id"],
                "display_name": item["display_name"],
                "category": item["category"],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "availability": item["availability"],
                "location": ", ".join(sorted(item["locations"])) or "not in a mapped bin",
                "notes": note,
            }
        )
    return entries


def validate_history(history: object) -> list[dict]:
    if history is None:
        return []
    if not isinstance(history, list):
        raise ValueError("history must be a list")
    cleaned = []
    for turn in history[-MAX_HISTORY_TURNS * 2 :]:
        if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
            raise ValueError("history turns need a role of user or assistant")
        text = turn.get("text")
        if not isinstance(text, str):
            raise ValueError("history turns need text")
        cleaned.append({"role": turn["role"], "text": text[:MAX_MESSAGE_CHARS]})
    return cleaned


def validate_message(message: object) -> str:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message must be a non-empty string")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ValueError(f"message must be at most {MAX_MESSAGE_CHARS} characters")
    return message.strip()


class ChatService:
    """Grounds a visitor's request in the inventory, asks Gemini, re-validates, lights bins."""

    def __init__(self, rack_service, client: GeminiClient | None):
        self._rack = rack_service
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def model(self) -> str | None:
        return self._client.model if self._client else None

    def _contents(self, inventory: dict, history: list[dict], message: str) -> list[dict]:
        grounding = json.dumps(inventory_context(inventory), ensure_ascii=False, separators=(",", ":"))
        contents = [
            {"role": "user", "parts": [{"text": f"Verified inventory (JSON):\n{grounding}"}]},
            {"role": "model", "parts": [{"text": json.dumps({"reply": "Understood. I will only use these parts.", "item_ids": [], "light": False})}]},
        ]
        for turn in history:
            role = "user" if turn["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": turn["text"]}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        return contents

    def answer(self, message: str, *, history: list[dict] | None = None, light: bool = True) -> dict:
        if self._client is None:
            raise ChatError("chat_not_configured")
        message = validate_message(message)
        history = validate_history(history)
        inventory = self._rack.inventory_snapshot()
        known = {item["item_id"]: item for item in inventory["items"]}

        raw = self._client.generate(SYSTEM_PROMPT, self._contents(inventory, history, message), RESPONSE_SCHEMA)
        reply = raw.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            raise ChatError("chat_upstream_unparseable")
        proposed = raw.get("item_ids") if isinstance(raw.get("item_ids"), list) else []

        matched_ids: list[str] = []
        dropped: list[str] = []
        for candidate in proposed:
            if not isinstance(candidate, str):
                continue
            if candidate not in known:
                dropped.append(candidate)
            elif candidate not in matched_ids:
                matched_ids.append(candidate)

        matches = [
            {
                "item_id": item_id,
                "display_name": known[item_id]["display_name"],
                "category": known[item_id]["category"],
                "quantity": known[item_id]["quantity"],
                "unit": known[item_id]["unit"],
                "availability": known[item_id]["availability"],
                "locations": sorted(known[item_id]["locations"]),
            }
            for item_id in matched_ids
        ]
        mapped_ids = [entry["item_id"] for entry in matches if entry["locations"]]
        wants_light = bool(raw.get("light")) and light and bool(mapped_ids)
        highlight = self._rack.locate(mapped_ids) if wants_light else None

        return {
            "reply": reply.strip(),
            "matches": matches,
            "lit": highlight["lit"] if highlight else [],
            "expires_in": highlight["expires_in"] if highlight else 0,
            "unmapped": [entry["item_id"] for entry in matches if not entry["locations"]],
            "dropped": dropped,
            "model": getattr(self._client, "last_model", None) or self._client.model,
        }
