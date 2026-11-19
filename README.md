# SpamHop

SpamHop is a **server- and client-agnostic spam scoring system that adds an independent processing Hop to the message transport pipeline.**

**Current stage: Research & Architecture — runnable end-to-end prototype**

```text
Sender
  ↓
Destination mail server
  ↓
SpamHop
  ↓
User mail client
```

SpamHop leaves the authoritative upstream mailbox untouched. It scores the message representation in transit and adds SpamHop scoring metadata to the representation delivered downstream.

## Current architecture

```text
IMAP client
    ↕
Go IMAP transport ↔ upstream IMAP server
    ↕
Python scoring engine
```

Python owns application lifecycle and scoring. Go owns IMAP transport and the downstream message representation. The scoring engine and LLM provider are replaceable components.

## Current implementation

The initial runnable version provides:

- independent downstream IMAP credentials;
- configurable upstream IMAP host, TLS mode, username and password;
- configurable scoring processor;
- configurable LLM provider, endpoint, API key and model;
- configurable prompt file;
- Ollama as the first provider implementation;
- in-memory score caching by message content;
- downstream `X-SpamHop-Score`, `X-SpamHop-Verdict`, and `X-SpamHop-Engine` metadata;
- no mutation, movement, deletion, or re-upload of upstream messages.

An email whose subject is exactly `SpamHop Positive Test` is deterministically scored as spam with score `1.0`. This test does not call the LLM.

## Configuration

Copy the example configuration:

```bash
cp .env.example .env
```

Edit `.env`. No IMAP host, IMAP credential, prompt, LLM endpoint, API key, or model is embedded in application code.

The default prompt is stored at:

```text
config/spam-prompt.txt
```

It can be edited or replaced by changing `SPAMHOP_PROMPT_FILE`.

### Ollama on the Docker host

For the laptop deployment, `.env.example` uses:

```text
SPAMHOP_LLM_BASE_URL=http://host.docker.internal:11434
```

Compose maps `host.docker.internal` to the Linux Docker host. Ollama's local API does not require authentication; leave `SPAMHOP_LLM_API_KEY` empty for that setup.

## Run

```bash
docker compose build
docker compose up -d
docker compose logs --tail=50
```

Configure the mail client to connect to the SpamHop host on the configured `SPAMHOP_PORT` using `SPAMHOP_CLIENT_USERNAME` and `SPAMHOP_CLIENT_PASSWORD`.

## Principles

- Server agnostic.
- Client agnostic.
- Upstream messages remain authoritative.
- Spam scoring is specialized and replaceable.
- Transport, representation, and scoring remain separate concerns.
- Provider and model choices are configuration, not architecture.

## Current limitations

This is an initial single-mailbox research implementation. Downstream TLS, persistent score storage/spooling, multi-account operation, additional scoring providers, and broader anti-spam signals are not yet implemented.

## License

GNU Affero General Public License v3.0.
