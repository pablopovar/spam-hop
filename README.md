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
- Rspamd/LLM hybrid scoring with SpamHop-owned thresholds;
- optional metadata-only IMAP review queue and supervised Rspamd learning;
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

## Optional metadata-only review queue

Enable the review UI in `.env`:

```dotenv
SPAMHOP_REVIEW_ENABLED=true
SPAMHOP_REVIEW_USER=review
SPAMHOP_REVIEW_PASSWORD=<your-private-review-password>
SPAMHOP_REVIEW_RSPAMD_PASSWORD=<a-different-private-learning-password>
SPAMHOP_REVIEW_MAILBOXES=INBOX
SPAMHOP_REVIEW_PORT=8088
```

After `docker compose up -d --build`, open `http://127.0.0.1:8088/`
on the **Docker host** and authenticate with the review user/password.
The UI is deliberately bound to host loopback; use a secure tunnel for
remote access, not a public HTTP listener.

The indexer polls configured IMAP mailboxes, reads only message headers,
and stores a small SQLite record for each encountered email. The list
shows sender, recipient, subject, date, mailbox, review label and
training status. The default window is the newest 500 messages per
mailbox; adjust `SPAMHOP_REVIEW_MAX_MESSAGES` if needed.

**Spam** and **Ham** buttons record feedback. SpamHop re-fetches the
original message from upstream IMAP in read-only mode, checks UIDVALIDITY
and header identity, submits it to the password-protected Rspamd
controller `/learnspam` or `/learnham` endpoint, and discards the
in-memory message bytes. Training does not modify the upstream mailbox.

Only headers, feedback, and an IMAP locator (mailbox, UID, UIDVALIDITY,
Message-ID) are persisted in the `review-data` volume. No message body
or attachment is stored. When an email is moved or deleted, the label
remains visible but learning may fail because the original cannot be
retrieved at its recorded location. Feedback failures are retryable.

No learning is triggered solely by moving a message into a folder.
SpamCop.net is not contacted. This trains **Rspamd's local Bayesian
classifier**, not the LLM. Rspamd's default Bayes classifier requires
a sufficient corpus of both spam and ham (typically 200 of each)
before the statistical symbols become active.

Review is optional and disabled by default, preserving existing
deployments until enabled and private credentials are configured.

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

This is an initial single-mailbox research implementation. Downstream TLS, multi-account operation, additional LLM providers, and full IMAP compatibility are not yet implemented. The review queue keeps metadata but not raw emails. Actual integration testing against HQ3/Rspamd is pending.

## License

GNU Affero General Public License v3.0.
