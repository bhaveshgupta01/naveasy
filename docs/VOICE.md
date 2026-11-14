# Voice: Gemini Live (free tier)

Checked on Google's official docs on Nov 14, 2026.

## Why Gemini Live

| Need | Gemini Live |
| --- | --- |
| Speech in, speech out, low latency | Native audio Live models |
| User interrupts the model mid-answer | Yes: "Users can interrupt the model at any time"; on interruption "the ongoing generation is canceled and discarded" (automatic voice activity detection, on by default) |
| Call our API during the conversation | Yes: function calling. `gemini-3.8-live` supports synchronous and asynchronous calls. We must send tool results back ourselves (no automatic tool handling) |
| Browser connects directly, key stays secret | Yes: ephemeral tokens minted by our backend (`POST /voice/token`); the feature is marked "in preview" |
| Cost | `gemini-3.8-live` and `gemini-3.1-flash-live-preview` are "Free of charge" on the free tier. Paid: about $0.005/min audio in, $0.018/min audio out |

**Model:** `gemini-3.8-live` (fallback `gemini-3.1-flash-live-preview`, synchronous tools only).

**Caveat:** on the free tier Google lists "Used to improve our products: Yes" (paid tier: No).
Demo with test data only; real users' disability information needs the paid tier.

## Limits to design around

- Audio-only sessions: 15 minutes without context compression (compression extends them).
- A connection lasts about 10 minutes; enable `sessionResumption` and reconnect on the `GoAway` message.
  Resumption handles stay valid for 2 hours.
- Ephemeral tokens: default expiry 30 minutes; a new session must start within 1 minute of minting.
- Free-tier rate limits are only shown inside AI Studio; check them before the demo.
- Use a headset or push-to-talk on stage: a loud room triggers false interruptions.

## Architecture

1. Browser asks our backend `POST /voice/token` → backend calls Google's ephemeral-token endpoint
   (`POST https://generativelanguage.googleapis.com/v1beta/auth_tokens`) with `GEMINI_API_KEY` and returns the token.
2. Browser opens a Live session with the token, streams mic audio, plays the model's audio.
3. The model calls our tools; the browser (or backend) runs them against the NavEasy API and sends results back.
4. Fast tools only (precomputed scores, routes, places), so answers come in about a second. For slow work
   (relocation, furnishing) the model says "Let me check that" first.

## Tool declarations (map 1:1 to the API)

| Function | Arguments | Calls |
| --- | --- | --- |
| `find_homes` | `profiles[]`, `area?`, `max_rent?` | `GET /map/buildings` |
| `nearest_place` | `building_id`, `category`, `profile` | `GET /routes` |
| `describe_home` | `building_id` | `GET /buildings/{id}` |
| `check_apartment` | `model_id`, `profile` | `GET /units/{model_id}/heatmap` (read `problems`) |
| `compare_cities` | `origin_address`, `profiles[]` | `POST /relocate` |

## Status

- [x] Backend endpoint stub (`POST /voice/token` returns 503 until the key is set, 501 until implemented)
- [ ] Mint ephemeral tokens
- [ ] Browser Live session (mic in, audio out, interruption handling)
- [ ] Tool calls wired to the API
- [ ] Photon phone line (stretch, for Photon's Best Voice Agent prize)

Docs: [Live API](https://aistudio.google.com/docs/live-api) ·
[tools](https://aistudio.google.com/docs/live-api/tools) ·
[ephemeral tokens](https://aistudio.google.com/docs/live-api/ephemeral-tokens) ·
[session management](https://aistudio.google.com/docs/live-api/session-management) ·
[pricing](https://aistudio.google.com/docs/pricing)
