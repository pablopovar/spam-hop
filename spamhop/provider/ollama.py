import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from spamhop.provider.base import ProviderResult


RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
    },
    "required": ["score"],
    "additionalProperties": False,
}


class Provider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.engine = f"ollama:{model}"

    def _chat_url(self) -> str:
        if self.base_url.endswith("/api"):
            return f"{self.base_url}/chat"
        return f"{self.base_url}/api/chat"

    def score(self, system_prompt: str, message: str) -> ProviderResult:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                "format": RESULT_SCHEMA,
                "stream": False,
            }
        ).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = Request(
            self._chat_url(),
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response_payload = json.load(response)
        except HTTPError as exc:
            body = exc.read(4096).decode("utf-8", "replace")
            raise RuntimeError(f"Ollama HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Ollama connection failed: {exc.reason}") from exc

        try:
            content = response_payload["message"]["content"]
            result = json.loads(content)
            score = float(result["score"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("invalid structured response from Ollama") from exc

        if not 0 <= score <= 1:
            raise RuntimeError("Ollama score must be between 0 and 1")

        return ProviderResult(score=score)
