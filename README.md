# SpamHop

Minimal IMAP processing hop.

```text
IMAP client
    ↓
Go IMAP transport
    ↓
Python processor
    ↓
Go IMAP transport
    ↓
upstream IMAP server
````

Python owns the application and processing layer.

Go owns only IMAP transport.

The processor is replaceable through:

```text
SPAMHOP_PROCESSOR=module.path:Processor
```

The initial processor is a passthrough processor.

SpamHop does not create or replace upstream mailbox messages.
