# SpamHop

SpamHop is a **server- and client-agnostic spam scoring system that adds an independent processing Hop to the message transport pipeline.**

**Current stage: Research & Architecture**

SpamHop is being designed around one central idea: spam scoring should be independent from both the mail server and the mail client.

```text
Sender
  ↓
Destination mail server
  ↓
SpamHop
  ↓
User mail client
```

SpamHop introduces a processing layer between the authoritative mail server and the client that receives the message.

## Core principles

### Server agnostic

SpamHop should not depend on a particular mail provider, MTA, mailbox implementation, or server-side plugin system.

The upstream mail server remains authoritative.

### Client agnostic

SpamHop should work with ordinary mail clients without requiring a SpamHop-specific client, extension, or plugin.

### Independent Hop

SpamHop is not intended to modify the authoritative mailbox object.

It receives the message representation from upstream, processes it, and exposes a derived representation downstream.

### Spam scoring, not mailbox management

SpamHop's responsibility is classification and scoring.

It is not intended to become a general mail-management system.

SpamHop does not own mailbox organization, folder movement, deletion, quarantine, or other user-facing mail actions.

Those decisions remain with downstream systems or clients.

### Replaceable spam engine

The spam-scoring engine is a separate component.

The transport must not depend on a specific:

- language model;
- spam engine;
- API provider;
- reputation service;
- rule system;
- security product.

Different classifiers should be replaceable without redesigning the transport.

## Architectural direction

The current architecture separates the application and scoring system from the protocol transport.

```text
Python application
    ├── configuration
    ├── lifecycle
    └── replaceable spam-scoring engine

Go transport
    ├── downstream IMAP server
    ├── upstream IMAP client
    └── message transport / representation
```

Python owns the SpamHop application.

Go is deliberately restricted to the transport layer.

The boundary between them is intended to remain small and explicit.

## IMAP transport model

For the current proof of concept:

```text
IMAP client
    ↓
SpamHop
    ↓
upstream IMAP server
```

SpamHop behaves as:

- an IMAP server toward the user's mail client;
- an IMAP client toward the upstream mail server;
- an intermediate processing Hop between them.

The upstream message remains untouched.

The representation delivered downstream may contain SpamHop-derived classification information.

## Current research

The project is currently validating the architecture before implementing the full spam engine.

Current work includes:

- correct intermediary IMAP behavior;
- upstream/downstream representation semantics;
- transport and classifier boundaries;
- live processing versus short-lived transitional spooling;
- compatibility with ordinary IMAP servers and clients;
- failure behavior and transport reliability;
- modular spam-scoring interfaces.

## Current proof of concept

The repository contains an early transport implementation intended to prove the Hop architecture.

It is not yet a production spam-filtering system.

The current PoC intentionally excludes:

- sophisticated spam classification;
- model/provider integrations;
- SPF, DKIM and DMARC analysis;
- reputation and blacklist systems;
- malware or antivirus integration;
- management UI;
- production authentication hardening;
- provider-specific integrations.

Those belong after the transport architecture is proven.

## Direction

The long-term goal is a specialized spam-scoring layer that can combine multiple forms of evidence while remaining independent from the systems on either side of it.

SpamHop should be able to evolve its classification engine without forcing users to change mail servers or mail clients.

The Hop is the stable architectural boundary.

The spam engine is replaceable.

## License

GNU Affero General Public License v3.0.