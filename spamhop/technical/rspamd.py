from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import ipaddress
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_BRACKETED_IP = re.compile(r"\[(?:IPv6:)?([0-9A-Fa-f:.]+)\]")


@dataclass(frozen=True, slots=True)
class RspamdResult:
    score: float
    required_score: float | None
    action: str
    symbols: tuple[str, ...]


class RspamdClient:
    def __init__(self, *, base_url: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _check_url(self) -> str:
        return f"{self.base_url}/checkv2"

    @staticmethod
    def _metadata(message: bytes) -> dict[str, str]:
        parsed = BytesParser(policy=policy.default).parsebytes(
            message,
            headersonly=True,
        )
        metadata: dict[str, str] = {}

        envelope_from = parseaddr(str(parsed.get("Return-Path", "")))[1]
        if envelope_from:
            metadata["From"] = envelope_from

        recipient = (
            str(parsed.get("X-Original-To", "")).strip()
            or str(parsed.get("Delivered-To", "")).strip()
        )
        recipient_address = parseaddr(recipient)[1]
        if recipient_address:
            metadata["Deliver-To"] = recipient_address

        received = parsed.get_all("Received", [])
        if received:
            for candidate in _BRACKETED_IP.findall(str(received[0])):
                try:
                    address = ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                if not address.is_unspecified:
                    metadata["IP"] = str(address)
                    break

        return metadata

    def score(self, message: bytes) -> RspamdResult:
        headers = {
            "Content-Type": "message/rfc822",
            "Flags": "groups",
        }
        headers.update(self._metadata(message))

        request = Request(
            self._check_url(),
            data=message,
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as exc:
            body = exc.read(4096).decode("utf-8", "replace")
            raise RuntimeError(f"Rspamd HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Rspamd connection failed: {exc.reason}") from exc

        try:
            score = float(payload["score"])
            action = str(payload.get("action", ""))
            required_raw = payload.get("required_score")
            required_score = (
                None if required_raw is None else float(required_raw)
            )
            symbols_raw = payload.get("symbols", {})
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("invalid response from Rspamd") from exc

        if not isinstance(symbols_raw, dict):
            symbols_raw = {}

        return RspamdResult(
            score=score,
            required_score=required_score,
            action=action,
            symbols=tuple(sorted(str(name) for name in symbols_raw)),
        )
