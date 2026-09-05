# LLM Router

A small local web app that sends each message to whichever model — Claude,
ChatGPT, Gemini, Grok, Perplexity, or DeepSeek — best fits the task, using
your own API keys. Runs entirely on your machine; no data goes through any
third-party server except the AI providers themselves.

## How routing works

`app.py` has a `pick_model()` function with plain keyword lists, checked in
this order (first match wins):

1. Very long message or long-running conversation → **Gemini** (largest context window)
2. Code/technical language → **Claude** (strong coding + reasoning)
3. Mentions of X/Twitter or social sentiment → **Grok** (native X search)
4. Current-events questions that need sources → **Perplexity** (native web search + citations)
5. Creative writing requests → **ChatGPT**
6. Short, routine-looking questions with no other signal → **DeepSeek** (cheap workhorse, saves cost on simple lookups)
7. Everything else → **Claude** (general-purpose default)

Single-word signals (like "api" or "css") are matched as whole words, not
raw substrings — so "capital" won't accidentally trigger the code route just
because it contains "api". Multi-word phrases are matched as substrings
since spaces already prevent that kind of false positive.

## Other features

- **Streaming.** Replies stream in token-by-token instead of waiting for the
  full response.
- **Ask All.** The **Ask All** button next to Send sends your message to
  every provider that has an API key set, all at once — each reply streams
  into its own bubble concurrently, rather than the normal one-model-per-message
  routing.
- **Auto thinking level.** Alongside picking which model answers, `app.py`
  also picks how hard it should think — `pick_thinking_level()` reads the
  message for complexity signals (length, phrases like "step by step" or
  "quick") and lands on one of four generic levels (`none`/`low`/`medium`/`high`),
  which gets translated into each provider's own reasoning-effort dial
  (Claude's adaptive-thinking `effort`, OpenAI/Grok/DeepSeek's
  `reasoning_effort`, Gemini's `thinking_level`, Perplexity's preset). Shown
  in the reason line under each reply, e.g. "Thinking: high (complex/analytical)".
- **Persistent history.** Conversations are saved to `history.json` after
  every exchange and reloaded automatically when you reopen the app —
  restarting the server doesn't lose anything. "New chat" clears it.
- **Export.** The Export button downloads the current conversation as
  Markdown. Add `?format=json` to the `/export` URL for a raw JSON export
  instead.
- **Markdown rendering.** Replies render as formatted markdown (bold, code
  blocks, lists) instead of raw text, using `marked` + `DOMPurify` from a
  CDN. If you're offline when the page loads, it falls back to plain text
  rather than showing nothing.
- **Optional password.** Set an app password in Settings to gate the whole
  app behind a login page — see the security note below for why this
  matters once you're binding to `0.0.0.0`.

## Using it from your phone and your computer

The app runs on whichever machine you start `python app.py` on — usually
your computer. There's one shared conversation, not one per device: your
phone and your computer both talk to the same running server, so it's
genuinely the same chat wherever you open it.

- **On that same computer:** open `http://localhost:5050`.
- **On your phone (or any other device on the same Wi-Fi):** find your
  computer's LAN IP (Mac: System Settings > Wi-Fi > Details; Windows:
  `ipconfig` in Command Prompt) and open `http://<that-ip>:5050`.

The server has to stay running on your computer to be reachable from your
phone — closing the terminal takes the app down everywhere. Want it
reachable from anywhere without keeping your computer on? Two options:

- [DEPLOY.md](DEPLOY.md) — Render, ~$7/month flat, simplest setup.
- [DEPLOY_GCP.md](DEPLOY_GCP.md) — Google Cloud Run, reuses existing GCP
  billing, cost varies (~$3–10/month) depending on config.

Both need zero code changes — storage already reads its path from an
environment variable (`DATA_DIR`), so it works the same whether that's a
local folder, a Render disk, or a mounted Cloud Storage bucket.

## Security note

Binding to `0.0.0.0` (so your phone can reach it) also means **anyone else
on your Wi-Fi can open the app**, read your conversation history, and send
messages that spend money on your configured API keys — there's no login by
default. If you're on a home network you trust, that may be fine. If not,
set an app password in Settings before relying on this day to day. It's a
single shared password (hashed, not stored in plaintext) — fine for keeping
casual/accidental access out, not a substitute for real multi-user auth.

Every reply shows which model answered and why, and you can always override
the pick from the dropdown next to the message box. Edit the keyword lists
directly in `app.py` (`CODE_SIGNALS`, `RESEARCH_SIGNALS`, `CREATIVE_SIGNALS`)
to tune it to how you actually use it.

## Setup

1. **Install Python 3.9+** if you don't have it already.
2. Open a terminal in this folder and install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run the app:
   ```
   python app.py
   ```
4. Open **http://localhost:5050** in your browser.
5. Click **Settings** and paste in your API keys:
   - Anthropic: https://console.anthropic.com/settings/keys
   - OpenAI: https://platform.openai.com/api-keys
   - Google (Gemini): https://aistudio.google.com/apikey
   - xAI (Grok): https://console.x.ai
   - Perplexity: https://perplexity.ai/account/api
   - DeepSeek: https://platform.deepseek.com/api_keys

Keys are saved to `config.json` in this folder (never committed to git —
see `.gitignore`) so you don't have to re-enter them every time.

## Notes

- **Model names drift.** The defaults in `app.py` (`claude-sonnet-5`,
  `gpt-5.1`, `gemini-3.8-flash`, `grok-4.6`, `deepseek-v4-flash`) were current
  as of September 2026. If a call fails with a "model not found" error, check
  that provider's docs for the current model ID and update it in Settings
  (no code change needed). DeepSeek in particular renames its models
  periodically — `deepseek-chat`/`deepseek-reasoner` retired July 24, 2026 in
  favor of `deepseek-v4-flash`/`deepseek-v4-pro`.
- **Perplexity works differently.** Perplexity retired its old Sonar
  model-based API on September 27, 2026 in favor of an "Agent API" that
  takes a `preset` (e.g. `pro-search`, `deep-research`) instead of a model
  name — this app already uses the new API. See
  https://docs.perplexity.ai/docs/agent-api/presets for other presets if
  `pro-search` isn't the right fit (e.g. `deep-research` for long multi-step
  research instead of quick grounded Q&A).
- **This is a single-user app.** All devices that connect share the same
  conversation and the same configured API keys/settings — there's no
  concept of separate accounts. `history.json` and `config.json` live next
  to `app.py` and are never committed to git.
- **Costs are real.** Every message is a paid API call to whichever provider
  gets picked — there's no free tier bundled in.
