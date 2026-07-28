---
name: voice
description: Generate spoken audio with Gemini TTS (first-class Kageha tool). Use for voiceovers, narrated ads, and audio deliverables.
triggers:
  - voice
  - TTS
  - text to speech
  - text-to-speech
  - voiceover
  - speak this
  - narrate
  - gemini tts
  - audio narration
allowed-tools: gemini_tts
---

# voice

## When to use

Spoken deliverables: product voiceovers, Instagram/TikTok narration, ad reads,
accessibility audio, or any script that should become a WAV file.

## Do this (not that)

1. Call `gemini_tts(text=…, filename="artifacts/voiceover.wav")` immediately.
2. Keep scripts concise (under ~4000 chars); shorten marketing copy before TTS.
3. Optional `voice` (Gemini prebuilt names such as `Kore`, `Puck`, `Charon`).
4. **Do not** invent curl/ffmpeg/`say` pipelines for Gemini TTS.
5. Chat **voice channel** (mic in / spoken replies) is separate:
   - CLI: `kageha chat --voice` and optional `KAGEHA_VOICE_REPLY=1`
   - WebUI: mic button + Speak toggle in the composer

## Config

- `models.yaml` → `voice:` (`provider`, `model`, `voice`)
- Env overrides: `KAGEHA_VOICE_MODEL`, `KAGEHA_VOICE_NAME`
- Requires `GEMINI_API_KEY`
