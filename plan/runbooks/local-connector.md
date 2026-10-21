# A model on the author's own computer (SCN-002A)

A person runs the connector next to their model server (Ollama, LM Studio, a llama.cpp server),
confirms its code under **AI assistant → Use a model on your own computer**, and chooses one of its
models. The assistant's requests then go to that computer through a queue the connector polls over
HTTPS. Nothing on the person's computer is opened to the internet. ADR-025 has the design.

## Connecting a computer (for the person)

1. Start the model server and load a model. Usual addresses: Ollama `http://127.0.0.1:11434/v1`,
   LM Studio `http://127.0.0.1:1234/v1`, llama.cpp `http://127.0.0.1:8080/v1`.
2. From the Novel Engine source (the `connector` folder; Python 3.11 or later, nothing else):

   ```text
   python -m connector --site https://<your site>
   # another server:  --model-server http://127.0.0.1:1234/v1
   # another name:    --name "Study desktop"
   ```

   In a checkout, `uv run python -m connector --site …` works too.
3. It prints a code such as `WDJB-MJHT`. Type it under **AI assistant → Use a model on your own
   computer**, check the name and version shown, and **Connect this computer**. Only workspace
   owners can. The code works for ten minutes, once. A computer stops if the person who connected
   it leaves the workspace or is no longer an owner.
4. When the computer lists its models, pick one and **Use for the assistant**. Keep the connector
   running while you use the assistant.

The credential is kept in `~/.config/novel-engine-connector/<site>.json` (Windows:
`%APPDATA%\novel-engine-connector\`), readable only by that user. `python -m connector --site … --forget`
removes it.

## What the connector's exit codes mean

| Code | Meaning | What to do |
|---|---|---|
| 0 | Stopped normally (Ctrl-C) | — |
| 2 | Too old for the site | Get the newer connector; nothing is ever downloaded for you |
| 3 | Disconnected from the site (in the app, by expiry, a copied credential, or the member who connected it left or is no longer an owner) | Start it again for a new code |
| 4 | The code was turned down, ran out or was used | Start it again |
| 5 | Bad setup: no `--site`, an `http://` site that is not this computer, or the site unreachable | Fix the address |

## "Your computer is not connected right now" / "did not pick up the request"

The site has not heard from the connector for 75 seconds, or the connector did not take the request
within 20 seconds.

1. Is the connector still running on that computer, and is the computer awake?
2. Under **Your computers**, "Not running" means no poll recently; "Connected" means it is polling.
3. If the connector prints `The model server could not be reached`, start the model server.
4. Behind a strict proxy, the connector honours `HTTPS_PROXY`; long polls of 25 seconds must be
   allowed through.

## "took too long to answer"

Local models can be slow. The site waits `CONNECTOR_ANSWER_SECONDS` (600 unless set) after the
computer takes a request. Use a smaller model, or raise the setting on the server and restart it.
The connector keeps saying it is there while it works, so a long request does not make it look
offline.

## "The computer chosen for the assistant is no longer connected"

The chosen computer was disconnected. The assistant does not move to a key on its own (the author
chose to keep their words on their computer). Connect it again and choose its model, or **Stop using
a computer** to go back to a key (hosted) or the server's model (self-hosted).

## A credential may have leaked (a copied config folder, a lost laptop)

1. **Disconnect** the computer under **Your computers**. It stops at once; anything waiting for it
   is not sent.
2. Credentials rotate daily on their own; one not used for 30 days stops working anyway.
3. `make secret-scan` matches the `nvldev_` prefix if one was committed somewhere.

"Disconnected: an old credential for it was used again" means a credential the connector had
already replaced was used to ask for a new one after the new one was in use: two copies existed, so
the site stopped both (RFC 9700 reuse detection). Treat it as a leak: check where the connector's
config folder has been copied, then start the connector again for a new code. A rotation whose
answer was lost on the way (the new credential never used) is retried safely and does not do this.

## "Too many …" (429)

- **Too many wrong codes**: after 5 wrong codes in 10 minutes, no code is checked for that person,
  right or wrong, until the 10 minutes have passed.
- **Too many failed tries** (the connector): after 30 failed sign-ins in a minute from one address,
  nothing from that address is checked, even a good credential, until the minute has passed. The
  connector waits and tries again on its own.
- **Asking for work too often / polls open**: one computer may hold 2 polls open at once (a long
  poll and a heartbeat) and make 60 a minute. A second connector started with the same credential
  (a copied folder) shows up as this.

## "The answer from your computer was too large to use"

The site keeps an answer of up to 4 MiB, measured as received. A larger one is refused (413) and the
request ends at once; the connector says `too_large`. Ask for a shorter answer, or a smaller model
context.

## Raising the minimum connector version (operators)

`MINIMUM_CONNECTOR_VERSION` and `LATEST_CONNECTOR_VERSION` are in `backend/domain/connector.py`.
Raising the minimum makes older connectors stop with exit code 2 and the site refuse them (426);
tell people where the new one is first. Nothing is pushed to anyone's computer.

## Checking the queue (operators)

Counts only; the words are cleared when a request finishes:

```sql
SELECT status, count(*) FROM connector_requests
WHERE created_at > now() - interval '1 day' GROUP BY status;
SELECT workspace_id, name, status, last_seen_at, connector_version FROM connector_devices
ORDER BY last_seen_at DESC NULLS LAST LIMIT 20;
```

If a server is stopped mid-request, the waiting call cannot clear the words. Nothing is lost or
sent late: a request older than the 20-second pickup window is never handed to a computer, and a
sweep (run at most once a minute by each server, when a computer polls or a request is sent)
expires any request still `waiting` or `taken` after the pickup and answer windows, with failure
`abandoned`, and clears the answer of any finished request not read within 5 minutes. To see
whether anything is waiting to be swept:

```sql
SELECT status, count(*) FROM connector_requests
WHERE payload IS NOT NULL OR result IS NOT NULL GROUP BY status;
```

Deleting a story removes the requests made for it; deleting a workspace removes its computers, codes
and requests.
