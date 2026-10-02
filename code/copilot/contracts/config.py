"""Ports and URLs. Override the ports with env vars if something else is using them."""
import os

HOST = "127.0.0.1"
TRANSCRIPT_PORT = int(os.environ.get("COPILOT_TRANSCRIPT_PORT", 8771))
ENGINE_PORT = int(os.environ.get("COPILOT_ENGINE_PORT", 8772))

TRANSCRIPT_WS = f"ws://{HOST}:{TRANSCRIPT_PORT}/transcript"
SUGGESTIONS_WS = f"ws://{HOST}:{ENGINE_PORT}/suggestions"
IMAGES_URL = f"http://{HOST}:{ENGINE_PORT}/images"
