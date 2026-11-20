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
- optional seven-day raw-sample training mode with an immutable score log;
- human Spam/Ham feedback through authenticated Rspamd learning;
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

## Optional seven-day training mode

To start capturing scored messages for human feedback, set:

```dotenv
SPAMHOP_TRAINING_ENABLED=true
SPAMHOP_REVIEW_USER=review
SPAMHOP_REVIEW_PASSWORD=<private-review-password>
SPAMHOP_REVIEW_RSPAMD_PASSWORD=<different-learning-password>
SPAMHOP_REVIEW_PORT=8088
SPAMHOP_TRAINING_MAX_MESSAGE_BYTES=26214400
```

With `SPAMHOP_TRAINING_ENABLED=true`, the review UI starts automatically.
From the Docker host, open `http://127.0.0.1:8088/` and log in.
The UI port remains reserved by the running Compose container even when
the interface is disabled; this is intentional.

Only messages actually **scored by SpamHop during IMAP FETCH** are captured.
Because this is an IMAP proxy, SpamHop does not see new mail automatically
unless a client fetches and triggers scoring. It does not independently
scan all mailboxes or copy all upstream mail. This is a current limitation.

The review table includes:

- Date, sender, subject and recipient (from RFC822 headers);
- **Original score**, original verdict and engine (never overwritten by feedback);
- **Re-classified to:** the user's Spam/Ham label, separate from the original score;
- Rspamd learning status (`learned`, `error`, `uncertain`, `expired`).

Clicking Spam or Ham sends the retained original RFC822 bytes to Rspamd's
authenticated `/learnspam` or `/learnham` endpoint. This trains Rspamd's
statistical model for **future** messages. It does **not** retroactively
change the original score, upstream mailbox, or downstream message.

Rspamd must return an explicit JSON `{"success":true}` before SpamHop
records training as confirmed. A definitive rejection is `error`; a
timeout/disconnect, malformed JSON, or missing confirmation is `uncertain`
because the server may already have learned the message. There are no
automatic retries on uncertain outcomes. The label is saved regardless.

Complete email samples are stored in a private named Docker volume for
**seven days after first capture**. The cleanup worker deletes expired
raw samples, including after service restarts, while keeping the SQLite
metadata/feedback log. Oversized samples are logged as `too-large` and
cannot be used for training; the default per-message limit is 25 MiB.

To stop capturing new messages but keep the review UI and expiration worker:

```dotenv
SPAMHOP_TRAINING_ENABLED=false
SPAMHOP_REVIEW_ENABLED=true
```

When both switches are false, the interface is disabled. Rspamd continues
to score normally. No SpamCop.net reporting or LLM fine-tuning is performed.

Bayesian symbols typically require a minimum corpus of confirmed spam and
ham examples to activate; see Rspamd's `min_learns` setting.

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

This is an initial single-mailbox research implementation. Downstream TLS, multi-account operation, additional LLM providers, and full IMAP compatibility are not yet implemented. Training mode temporarily keeps raw messages for seven days; only metadata remains afterward. Actual integration testing against HQ3/Rspamd is pending.

## License

GNU Affero General Public License v3.0.
