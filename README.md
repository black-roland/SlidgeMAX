# MAX Transport

XMPP external component (XEP-0114) that puppeteers a [MAX](https://max.ru) account via [PyMax](https://docs.pymax.org/getting-started.html) and presents it on Prosody (or any component-capable server).

**In scope:** contacts, 1:1 messages, message editing, text notices for incoming calls, ad-hoc registration.

**Out of scope:** groups, `WebClient` / QR login, reactions, stickers, polls, files, voice messages, calls themselves, rich formatting, profile photos.

## How it maps

| MAX | XMPP |
| --- | --- |
| Your account | Gateway registration on the component JID |
| Contact with id `123` | `123@max.example.org` |
| Direct message | `<message type="chat">` |
| Edit | [XEP-0308](https://xmpp.org/extensions/xep-0308.html) last-message correction |
| Incoming call | Plain-text message (`Incoming voice call` / `Incoming video call`) |
| Dialog chat id | `me_id XOR peer_id` (MAX's own formula) |

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- An XMPP server that accepts external components (Prosody is the target)

## Install

```bash
git clone <this-repo>
cd max-transport
uv python pin 3.11
uv sync
cp config.example.toml config.toml
```

Edit `config.toml`. At minimum set:

- `component.jid` — must match the Prosody component name
- `component.secret` — shared secret
- `component.server` / `component.port` — usually `127.0.0.1:5347`

Run:

```bash
uv run max-transport --config config.toml
```

Validate without connecting:

```bash
uv run max-transport --config config.toml --check-config
```

## Prosody

```lua
-- prosody.cfg.lua, or a virtual host file
Component "max.example.org"
    component_secret = "the-same-secret-as-config.toml"
```

Restart Prosody after adding the component. The transport then connects *out* to Prosody's component port (`component_ports` / `component_interface`, default `5347` on localhost).

DNS: `max.example.org` does not need an A record if only local users use it. For federation, give the component a proper hostname on the same domain as the virtual host.

## Registration (ad-hoc)

Registration is [XEP-0050](https://xmpp.org/extensions/xep-0050.html), not in-band registration. SMS and optional 2FA do not fit a single IBR form.

1. Discover the gateway (`max.example.org`).
2. Run **Register MAX account**.
3. Phone number → SMS code → 2FA password if MAX asks.
4. Contacts arrive as subscription requests from `id@max.example.org`.

**Gajim:** Accounts → Discover services → the gateway → Execute command.

**Conversations / Dino:** Service discovery on the gateway → Commands.

A short chat with the component JID (`help`, `status`) is available for clients that hide commands.

In-band register (`jabber:iq:register`) is advertised because XEP-0100 expects it, but the form tells you to use the ad-hoc command.

### Adding a MAX user who is not in your contacts

Use gateway translation (`jabber:iq:gateway`) if the client supports "Add contact via gateway": enter a MAX user id or a phone number. The transport returns `id@max.example.org`.

## Configuration

TOML. String values expand environment variables (`secret = "${MAX_COMPONENT_SECRET}"`).

| Table | Options |
| --- | --- |
| `[component]` | `jid`, `secret`, `server`, `port`, `name`, `use_jabber_client_ns` |
| `[storage]` | `data_dir` |
| `[max]` | `device_type` (`DESKTOP` / `ANDROID` / `IOS`), `reconnect`, `reconnect_delay_seconds` |
| `[bridge]` | `always_online`, `sync_contacts_on_login`, `auto_subscribe`, `ignore_groups`, `call_notifications`, call/unsupported text, `registration_timeout_seconds` |
| `[registration]` | `allowed_domains`, `allowed_jids` |
| `[logging]` | `level`, `file` |

See [`config.example.toml`](config.example.toml).

`always_online = true` (default) keeps the MAX TCP session up even when the XMPP user is offline, so incoming MAX messages become XMPP offline messages.

## Storage

Files under `data_dir`:

```
data/
  users/<jid>.json      # registration records
  sessions/<jid>/       # PyMax SQLite session (token, device, sync)
  msgids.sqlite         # MAX message id ↔ XMPP origin-id (edits)
```

User records are JSON. Message-id maps use SQLite because they grow with every bridged message and must be queried both ways for XEP-0308.

Deleting `sessions/<jid>/` forces a fresh SMS login on the next start.

## XEPs

- XEP-0114 component connection
- XEP-0030 service discovery (`gateway` / `max`)
- XEP-0050 ad-hoc commands (register / unregister / status / reconnect)
- XEP-0077 advertised; actual signup is ad-hoc
- XEP-0100 gateway interaction (login/logout, contact add/remove)
- jabber:iq:gateway (id/phone → JID)
- XEP-0308 message correction
- XEP-0359 origin-id
- XEP-0199 keepalives

## Tests

```bash
uv sync --all-extras
uv run pytest
```

Live MAX or Prosody is not required for the unit tests.

## Limitations

- One MAX account per XMPP account.
- Groups and channels are dropped (`ignore_groups`).
- Non-text MAX payloads become a configurable placeholder.
- Incoming calls are notified as text; you cannot answer them here.
- PyMax talks to MAX's unofficial internal API. It can change without notice.

## License

MIT
