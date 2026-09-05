import json
import os
import re
import threading
from datetime import timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, render_template_string, request, session, stream_with_context, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
# Keep the login session alive across browser restarts (only relevant if an
# app password is set — see the password gate below).
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

if os.environ.get("FLASK_SECRET_KEY") is None and (os.environ.get("RENDER") or os.environ.get("K_SERVICE")):
    # A missing secret key is a local-dev convenience, not a production one —
    # fail loudly on Render or Cloud Run (K_SERVICE is Cloud Run's
    # auto-injected marker) rather than silently signing sessions with a
    # value that ships in this file.
    raise RuntimeError("FLASK_SECRET_KEY must be set in production (see DEPLOY.md / DEPLOY_GCP.md).")

# In production, point this at a mounted persistent disk (e.g. DATA_DIR=/data
# on Render) so config/history survive restarts and redeploys. Defaults to
# this folder, which is exactly right for local/home use.
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "history.json"

DEFAULT_CONFIG = {
    "anthropic_api_key": "",
    "openai_api_key": "",
    "google_api_key": "",
    "xai_api_key": "",
    "perplexity_api_key": "",
    "deepseek_api_key": "",
    # Defaults current as of Sept 2026 — check each provider's docs
    # periodically and update here if a model gets deprecated.
    "anthropic_model": "claude-sonnet-5",
    "openai_model": "gpt-5.1",
    "google_model": "gemini-3.8-flash",
    "xai_model": "grok-4.6",
    # Perplexity's old Sonar endpoints retire Sept 27, 2026. This app already
    # uses the new Agent API, which is preset-based instead of model-based.
    # See https://docs.perplexity.ai/docs/agent-api/presets for other options
    # (e.g. "deep-research" for long multi-step research instead of quick Q&A).
    "perplexity_preset": "pro-search",
    # Cheap, fast workhorse for routine/simple messages. "deepseek-chat" and
    # "deepseek-reasoner" retired July 24, 2026 — this is the current name.
    "deepseek_model": "deepseek-v4-flash",
    # Empty = no login required. Set via Settings, never stored as plaintext.
    "app_password_hash": "",
}

# A single shared conversation, not per-browser-session — the whole point is
# one chat you can pick up from your phone or your computer. Persisted to
# history.json after every exchange so a server restart doesn't lose it.
# Guarded by a lock since two devices could message at the same instant.
CONVERSATION_LOCK = threading.Lock()


def load_conversation():
    if HISTORY_PATH.exists():
        try:
            data = json.loads(HISTORY_PATH.read_text())
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Migrates the old per-session format (one list per browser) into
            # a single timeline, in whatever order the file happens to have.
            merged = []
            for thread in data.values():
                merged.extend(thread)
            return merged
    return []


def save_conversation():
    HISTORY_PATH.write_text(json.dumps(CONVERSATION, indent=2))


CONVERSATION = load_conversation()


def load_config():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            data = {}
        merged = DEFAULT_CONFIG.copy()
        merged.update(data)
        return merged
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get_conversation():
    return CONVERSATION


# ---------------------------------------------------------------------------
# Routing logic — this is a simple, transparent, editable heuristic. Add or
# change keywords below to change how tasks get routed. Every response tells
# the user which model answered and why, so the logic is never a black box.
#
# Priority order (checked top to bottom, first match wins):
#   1. Long input / long conversation  -> Gemini   (largest context window)
#   2. Code / technical                -> Claude   (strong coding + reasoning)
#   3. X/Twitter or social sentiment   -> Grok     (native X search)
#   4. Current events / needs sources  -> Perplexity (native web search + citations)
#   5. Creative writing                -> ChatGPT
#   6. Short/simple, no other signal   -> DeepSeek (cheap workhorse for routine stuff)
#   7. Everything else                 -> Claude   (general-purpose default)
# ---------------------------------------------------------------------------
CODE_SIGNALS = [
    "code", "function", "bug", "debug", "python", "javascript", "typescript",
    "error", "compile", "algorithm", "sql", "regex", "api", "script",
    "stack trace", "refactor", "class", "def", "html", "css", "json",
]
SOCIAL_SIGNALS = [
    "twitter", "tweet", "x post", "trending on x", "elon musk",
]
RESEARCH_SIGNALS = [
    "latest", "current", "news", "today", "recent", "this week",
    "stock price", "weather", "sources", "cite your sources", "what's happening",
]
CREATIVE_SIGNALS = [
    "poem", "story", "creative", "brainstorm", "joke", "slogan",
    "names for", "write a song", "fiction", "tagline", "metaphor",
]
# Below this length, with no other signal matched, a message is treated as a
# quick/routine lookup and sent to the cheap model rather than Claude.
SIMPLE_LENGTH_THRESHOLD = 150


def matches_any(text: str, signals) -> bool:
    """Signal match, but word-bounded for single words so short tokens like
    "api" or "css" don't fire on substrings inside unrelated words (e.g.
    "capital", "access"). Multi-word phrases are matched as plain substrings
    since spaces already prevent that kind of false positive."""
    for sig in signals:
        sig = sig.strip()
        if " " in sig:
            if sig in text:
                return True
        elif re.search(r"\b" + re.escape(sig) + r"\b", text):
            return True
    return False


def pick_model(prompt: str, history_len: int):
    text = prompt.lower()

    if len(prompt) > 6000 or history_len > 20:
        return "gemini", "Long input or long-running conversation — routed to Gemini for its large context window."
    if matches_any(text, CODE_SIGNALS):
        return "claude", "Detected a coding/technical task — routed to Claude, which tends to be strong on code and technical reasoning."
    if matches_any(text, SOCIAL_SIGNALS):
        return "grok", "Detected a question about X/Twitter or social sentiment — routed to Grok for its native X search."
    if matches_any(text, RESEARCH_SIGNALS):
        return "perplexity", "Detected a request for current info that needs sources — routed to Perplexity for web-grounded, cited answers."
    if matches_any(text, CREATIVE_SIGNALS):
        return "chatgpt", "Detected a creative writing task — routed to ChatGPT."
    if len(prompt) < SIMPLE_LENGTH_THRESHOLD and not matches_any(text, HIGH_EFFORT_SIGNALS):
        return "deepseek", "Short, routine-looking question — routed to DeepSeek, a cheap workhorse model, to save cost on simple lookups."
    return "claude", "No strong signal detected — defaulting to Claude as the general-purpose choice."


# ---------------------------------------------------------------------------
# Thinking-level routing — a second, independent decision from which model
# gets picked: how hard should that model reason before answering. Every
# provider here exposes this as a "reasoning effort" style dial, just under
# different names and value sets, so we pick one of four generic levels
# ("none", "low", "medium", "high") and translate it per-provider below.
# ---------------------------------------------------------------------------
HIGH_EFFORT_SIGNALS = [
    "prove", "derive", "step by step", "step-by-step", "analyze", "analyse",
    "trade-off", "tradeoff", "architecture", "debug", "optimi", "in depth",
    "thorough", "edge case", "root cause", "compare and contrast",
    "walk me through", "why exactly",
]
LOW_EFFORT_SIGNALS = [
    "quick", "briefly", "in one sentence", "tl;dr", "just tell me", "yes or no",
]


def pick_thinking_level(prompt: str):
    text = prompt.lower()
    if matches_any(text, HIGH_EFFORT_SIGNALS) or len(prompt) > 500:
        return "high", "complex/analytical — reasoning harder"
    if matches_any(text, LOW_EFFORT_SIGNALS):
        return "none", "asked for something quick — skipping deep reasoning"
    if len(prompt) < 60:
        return "low", "short/simple — light reasoning"
    return "medium", "standard question — moderate reasoning"


# Generic level -> each provider's actual parameter value. Where a provider
# doesn't have a matching tier, we round to the nearest one it supports
# (e.g. Gemini 3 Flash has no "medium", so it gets "low").
THINKING_MAP = {
    "claude": {"none": "minimal", "low": "low", "medium": "medium", "high": "high"},
    "chatgpt": {"none": "none", "low": "low", "medium": "medium", "high": "high"},
    "gemini": {"none": "minimal", "low": "low", "medium": "low", "high": "high"},
    "grok": {"none": "low", "low": "low", "medium": "medium", "high": "high"},
    "deepseek": {"none": "none", "low": "low", "medium": "medium", "high": "high"},
    # Perplexity has no reasoning-effort dial — depth comes from which preset
    # runs. "medium" uses whatever preset is configured in Settings, so that
    # field still does something.
    "perplexity": {"none": "fast-search", "low": "fast-search", "high": "deep-research"},
}


# ---------------------------------------------------------------------------
# Provider calls — each is a generator that takes the shared config, the
# normalized history ([{"role": "user"|"assistant", "content": str}, ...]),
# and a generic thinking level, and yields plain-text chunks as they arrive.
# ---------------------------------------------------------------------------

def call_claude(cfg, history, thinking_level):
    import anthropic
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    effort = THINKING_MAP["claude"][thinking_level]
    with client.messages.stream(
        model=cfg["anthropic_model"], max_tokens=2000, messages=messages,
        thinking={"type": "adaptive"}, output_config={"effort": effort},
    ) as stream:
        for text in stream.text_stream:
            yield text


def call_chatgpt(cfg, history, thinking_level):
    from openai import OpenAI
    client = OpenAI(api_key=cfg["openai_api_key"])
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    effort = THINKING_MAP["chatgpt"][thinking_level]
    stream = client.chat.completions.create(
        model=cfg["openai_model"], messages=messages, stream=True,
        reasoning_effort=effort,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def call_gemini(cfg, history, thinking_level):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=cfg["google_api_key"])
    contents = []
    for m in history:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    level = THINKING_MAP["gemini"][thinking_level]
    config = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level=level))
    for chunk in client.models.generate_content_stream(
        model=cfg["google_model"], contents=contents, config=config
    ):
        if chunk.text:
            yield chunk.text


def call_grok(cfg, history, thinking_level):
    # xAI's API is OpenAI-compatible — same SDK, different base_url.
    from openai import OpenAI
    client = OpenAI(api_key=cfg["xai_api_key"], base_url="https://api.x.ai/v1")
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    effort = THINKING_MAP["grok"][thinking_level]
    stream = client.chat.completions.create(
        model=cfg["xai_model"], messages=messages, stream=True,
        reasoning_effort=effort,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def call_perplexity(cfg, history, thinking_level):
    # Perplexity's Agent API (Sonar endpoints retire Sept 27, 2026).
    from perplexity import Perplexity
    client = Perplexity(api_key=cfg["perplexity_api_key"])
    input_items = [{"role": m["role"], "content": m["content"]} for m in history]
    # "medium" isn't in THINKING_MAP for Perplexity on purpose — it falls
    # through to the preset configured in Settings.
    preset = THINKING_MAP["perplexity"].get(thinking_level, cfg["perplexity_preset"])
    stream = client.responses.create(preset=preset, input=input_items, stream=True)
    for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta


def call_deepseek(cfg, history, thinking_level):
    # DeepSeek's API is OpenAI-compatible — same SDK, different base_url.
    from openai import OpenAI
    client = OpenAI(api_key=cfg["deepseek_api_key"], base_url="https://api.deepseek.com")
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    effort = THINKING_MAP["deepseek"][thinking_level]
    kwargs = {}
    if effort in ("medium", "high"):
        # Flash's default is non-thinking; this actually turns reasoning on.
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    stream = client.chat.completions.create(
        model=cfg["deepseek_model"], messages=messages, stream=True,
        reasoning_effort=effort, **kwargs,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


CALLERS = {
    "claude": call_claude,
    "chatgpt": call_chatgpt,
    "gemini": call_gemini,
    "grok": call_grok,
    "perplexity": call_perplexity,
    "deepseek": call_deepseek,
}
LABELS = {
    "claude": "Claude (Anthropic)",
    "chatgpt": "ChatGPT (OpenAI)",
    "gemini": "Gemini (Google)",
    "grok": "Grok (xAI)",
    "perplexity": "Perplexity",
    "deepseek": "DeepSeek",
}
KEY_FIELD = {
    "claude": "anthropic_api_key",
    "chatgpt": "openai_api_key",
    "gemini": "google_api_key",
    "grok": "xai_api_key",
    "perplexity": "perplexity_api_key",
    "deepseek": "deepseek_api_key",
}


# ---------------------------------------------------------------------------
# Optional password gate. Off by default (no app_password_hash set). Worth
# turning on once the server binds to 0.0.0.0 — otherwise anyone on the same
# Wi-Fi can open the app, read your conversation history, and rack up API
# charges on your configured keys with no login at all.
# ---------------------------------------------------------------------------
LOGIN_PAGE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM Router - Login</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f1115;color:#e8e9ec;height:100vh;display:flex;align-items:center;justify-content:center;}
form{background:#171a21;border:1px solid #2a2e38;border-radius:10px;padding:24px;width:280px;}
h1{font-size:16px;margin:0 0 16px;}
input{width:100%;background:#0f1115;border:1px solid #2a2e38;color:#e8e9ec;padding:8px 10px;border-radius:6px;font-size:14px;box-sizing:border-box;margin-bottom:12px;}
button{width:100%;background:#6c7cff;border:none;color:white;padding:8px;border-radius:8px;font-weight:600;cursor:pointer;}
.error{color:#f4a3a3;font-size:12px;margin-bottom:12px;}
</style></head><body>
<form method="POST">
<h1>LLM Router</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<input type="password" name="password" placeholder="Password" autofocus>
<button type="submit">Unlock</button>
</form>
</body></html>"""

# Routes that talk to the frontend's fetch() calls should return a JSON 401
# instead of an HTML redirect on an expired/missing session — a redirect
# would hand the JS a login page where it expected JSON or an SSE stream.
API_ENDPOINTS = {"chat_stream", "settings", "history", "export", "reset"}


@app.route("/login", methods=["GET", "POST"])
def login():
    cfg = load_config()
    if not cfg.get("app_password_hash"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        if check_password_hash(cfg["app_password_hash"], request.form.get("password", "")):
            session["authed"] = True
            session.permanent = True
            return redirect(url_for("index"))
        error = "Wrong password."
    return render_template_string(LOGIN_PAGE, error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("authed", None)
    return jsonify({"ok": True})


@app.before_request
def require_login():
    if request.endpoint in ("login", "logout", "static", None):
        return
    cfg = load_config()
    if not cfg.get("app_password_hash") or session.get("authed"):
        return
    if request.endpoint in API_ENDPOINTS:
        return jsonify({"error": "Session expired. Refresh the page and log in again."}), 401
    return redirect(url_for("login"))


@app.route("/")
def index():
    cfg = load_config()
    key_status = {
        "anthropic": bool(cfg["anthropic_api_key"]),
        "openai": bool(cfg["openai_api_key"]),
        "google": bool(cfg["google_api_key"]),
        "xai": bool(cfg["xai_api_key"]),
        "perplexity": bool(cfg["perplexity_api_key"]),
        "deepseek": bool(cfg["deepseek_api_key"]),
        "password": bool(cfg["app_password_hash"]),
    }
    return render_template(
        "index.html", key_status=key_status, cfg=cfg,
        password_protected=bool(cfg["app_password_hash"]),
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if request.method == "POST":
        data = request.get_json(force=True)
        for field in (
            "anthropic_api_key", "openai_api_key", "google_api_key",
            "xai_api_key", "perplexity_api_key", "deepseek_api_key",
            "anthropic_model", "openai_model", "google_model",
            "xai_model", "perplexity_preset", "deepseek_model",
        ):
            if data.get(field):
                cfg[field] = data[field]
        if data.get("app_password"):
            cfg["app_password_hash"] = generate_password_hash(data["app_password"])
            session["authed"] = True  # don't lock out the person who just set it
            session.permanent = True
        if data.get("clear_password"):
            cfg["app_password_hash"] = ""
        save_config(cfg)
        return jsonify({"ok": True})
    return jsonify({
        "anthropic_model": cfg["anthropic_model"],
        "openai_model": cfg["openai_model"],
        "google_model": cfg["google_model"],
        "xai_model": cfg["xai_model"],
        "perplexity_preset": cfg["perplexity_preset"],
        "deepseek_model": cfg["deepseek_model"],
        "key_status": {
            "anthropic": bool(cfg["anthropic_api_key"]),
            "openai": bool(cfg["openai_api_key"]),
            "google": bool(cfg["google_api_key"]),
            "xai": bool(cfg["xai_api_key"]),
            "perplexity": bool(cfg["perplexity_api_key"]),
            "deepseek": bool(cfg["deepseek_api_key"]),
            "password": bool(cfg["app_password_hash"]),
        },
    })


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    cfg = load_config()
    data = request.get_json(force=True)
    message = (data.get("message") or "").strip()
    override = data.get("override", "auto")
    if not message:
        return jsonify({"error": "Empty message"}), 400

    history = get_conversation()
    with CONVERSATION_LOCK:
        history.append({"role": "user", "content": message})
        save_conversation()

    if override in CALLERS:
        model, reason = override, f"Manually selected: {LABELS[override]}."
    else:
        model, reason = pick_model(message, len(history))

    thinking_level, thinking_why = pick_thinking_level(message)
    reason = f"{reason} Thinking: {thinking_level} ({thinking_why})."

    key_field = KEY_FIELD[model]
    if not cfg.get(key_field):
        with CONVERSATION_LOCK:
            history.pop()
            save_conversation()
        return jsonify({"error": f"No API key set for {LABELS[model]}. Add it in Settings."}), 400

    def sse(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    def generate():
        yield sse("meta", {
            "model": model, "model_label": LABELS[model], "reason": reason,
            "thinking_level": thinking_level,
        })
        chunks = []
        try:
            for piece in CALLERS[model](cfg, history, thinking_level):
                chunks.append(piece)
                yield sse("delta", {"text": piece})
        except Exception as exc:
            with CONVERSATION_LOCK:
                history.pop()
                save_conversation()
            yield sse("error", {"error": f"{LABELS[model]} call failed: {exc}"})
            return
        with CONVERSATION_LOCK:
            history.append({"role": "assistant", "content": "".join(chunks), "model": model})
            save_conversation()
        yield sse("done", {})

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/history")
def history():
    return jsonify({"messages": get_conversation()})


@app.route("/export")
def export():
    fmt = request.args.get("format", "md")
    history = get_conversation()
    if fmt == "json":
        payload = json.dumps(history, indent=2)
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=conversation.json"},
        )
    parts = []
    for m in history:
        if m["role"] == "user":
            parts.append(f"**You:**\n\n{m['content']}")
        else:
            label = LABELS.get(m.get("model"), "Assistant")
            parts.append(f"**{label}:**\n\n{m['content']}")
    md = "\n\n---\n\n".join(parts) if parts else "_No messages yet._"
    return Response(
        md,
        mimetype="text/markdown",
        headers={"Content-Disposition": "attachment; filename=conversation.md"},
    )


@app.route("/reset", methods=["POST"])
def reset():
    with CONVERSATION_LOCK:
        CONVERSATION.clear()
        save_conversation()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # host="0.0.0.0" so you can also open this from your phone's browser —
    # visit http://<your-computer's-LAN-IP>:5050 while on the same Wi-Fi.
    # threaded=True so a slow/streaming request doesn't block other requests.
    app.run(debug=True, host="0.0.0.0", port=5050, threaded=True)
