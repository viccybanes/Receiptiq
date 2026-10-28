"""Optional Gemini REST transport; no key or historical examples enter receipt records."""

import asyncio
import json
import re

import httpx

from .google_key import load_google_key
from .vision import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    VisionEngine,
    VisionError,
    _cancelled,
    _MalformedReply,
    _parse_receipt,
)
from .vision_settings import require_google_enabled

GOOGLE_HOST = "https://generativelanguage.googleapis.com"


class GoogleVisionEngine(VisionEngine):
    provider = "google"
    source = "google_vision"

    def __init__(self, root):
        self.root = root

    async def _request(self, model, body, stop):
        async def send():
            # Disable environment proxies and redirects so the key only goes to Google's fixed HTTPS host.
            async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5, write=10),
                                         follow_redirects=False, trust_env=False) as client:
                key = load_google_key(self.root)
                async with client.stream("POST", f"{GOOGLE_HOST}/v1beta/models/{model}:generateContent",
                                         content=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}) as response:
                    if response.status_code != 200:
                        messages = {
                            400: "Google rejected this request. Check the Gemini model and API key in Settings.",
                            401: "Google API key was rejected. Replace it in Settings.",
                            403: "Google denied access. Check the API key's permissions, project, and regional availability.",
                            404: "This Gemini model is unavailable. Choose an available image-capable Gemini model in Settings.",
                            429: "Google quota or rate limit reached. Check AI Studio quota/billing and retry later.",
                        }
                        raise VisionError(messages.get(response.status_code,
                            "Google vision is unavailable. Try again later or select local Qwen in Settings."))
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise VisionError("Google returned an oversized response. Try one page or a smaller region.")
                        chunks.append(chunk)
                    return b"".join(chunks)

        task = asyncio.create_task(send())
        try:
            async with asyncio.timeout(180):
                while True:
                    _cancelled(stop)
                    require_google_enabled(self.root)
                    done, _ = await asyncio.wait({task}, timeout=0.2)
                    if done:
                        return task.result()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _read_receipt(self, payload, stop):
        model = payload["model"]
        if not re.fullmatch(r"gemini-[a-zA-Z0-9._-]{1,93}", model):
            raise VisionError("Choose a valid Gemini model ID in Settings.")
        _cancelled(stop)
        require_google_enabled(self.root)
        system, user = payload["messages"]
        request = {
            "systemInstruction": {"parts": [{"text": system["content"]}]},
            "contents": [{"role": "user", "parts": [{"text": user["content"]}] + [
                {"inlineData": {"mimeType": "image/jpeg", "data": data}} for data in user["images"]]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": payload["format"],
                                 "maxOutputTokens": 16384},
        }
        body = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise VisionError("Selected pages exceed the Google request limit. Choose fewer pages or a smaller region.")
        try:
            raw = asyncio.run(self._request(model, body, stop))
        except (TimeoutError, httpx.TimeoutException):
            raise VisionError("Google vision timed out. Check your connection and retry, or select local Qwen.") from None
        except httpx.HTTPError:
            # Never propagate provider bodies or request objects containing the key to logs/API errors.
            raise VisionError("Cannot reach Google vision. Check your internet connection or select local Qwen.") from None
        _cancelled(stop)
        require_google_enabled(self.root)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise VisionError("Google returned an oversized response. Try a smaller region.")
        try:
            reply = json.loads(raw)
            if reply.get("promptFeedback", {}).get("blockReason"):
                raise VisionError("Google declined this image. Try another image or enter the receipt manually.")
            candidate = reply["candidates"][0]
            finish = candidate.get("finishReason")
            if finish not in ("STOP", "MAX_TOKENS"):
                raise VisionError("Google could not complete this reading. Try another image or enter values manually.")
            content = "".join(part["text"] for part in candidate["content"]["parts"]
                              if "text" in part and not part.get("thought"))
            return _parse_receipt(content, incomplete=finish != "STOP")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise _MalformedReply("Google returned malformed receipt data with no usable fields. "
                                  "Try a smaller region or enter values manually.") from None
