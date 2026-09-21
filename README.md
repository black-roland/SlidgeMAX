# SlidgeMAX

XMPP gateway (legacy module) for the [MAX](https://max.ru) messenger, built on [Slidge](https://slidge.im/).

Bridges 1:1 chats, message editing, and text notifications for calls. No groups.

## Requirements

- Python >= 3.13
- An XMPP server with external component support (Prosody recommended)
- `uv` or pip

## Install for systemd (`/opt/slidgemax`)

Slidge is not a separate daemon. It is a Python library pulled in when this package is installed. The systemd unit runs `/opt/slidgemax/bin/slidgemax`, which calls Slidge with `--legacy-module slidgemax`.

Requires Python 3.13+. On RHEL, AlmaLinux, or Rocky Linux, install `uv` from EPEL and let it use a matching interpreter.

```bash
# as root
dnf install epel-release
dnf install uv python3.14

git clone <repo> /opt/slidgemax-src
cd /opt/slidgemax-src
uv venv /opt/slidgemax --python 3.14
uv pip install --python /opt/slidgemax/bin/python .

# Slidge lands in the same venv:
/opt/slidgemax/bin/python -c 'import slidge, slidgemax; print(slidge.__version__, slidgemax.__version__)'
/opt/slidgemax/bin/slidgemax --help
```

Leave `/opt/slidgemax` owned by root and world-executable. The service user only needs to run that interpreter; it must not write there. State (Slidge database, PyMax session files) goes under `/var/lib/slidgemax/<jid>/`.

To upgrade, pull the source and reinstall into the same venv:

```bash
cd /opt/slidgemax-src
git pull
uv pip install --python /opt/slidgemax/bin/python .
systemctl restart slidgemax@max.example.org.service
```

## Run (development)

From a checkout, without the `/opt` install:

```bash
uv sync
uv run slidge \
  --legacy-module slidgemax \
  --jid max.example.org \
  --secret "shared-secret" \
  --home-dir ./data \
  --server 127.0.0.1 --port 5347
```

## Registration

1. In your XMPP client, discover the component.
2. Run the **Register** ad-hoc command (or send "register").
3. Provide phone number, then the SMS code in the next form.
4. If MAX has account 2FA, a third form asks for that password. Do not send it as a chat message.

Contacts appear as `123456@max.example.org`.

## Prosody example

```
Component "max.example.org"
    component_secret = "shared-secret"
```

## Running as a systemd service

Install into `/opt/slidgemax` first (see above). The unit is `contrib/systemd/slidgemax@.service`. The instance name is the component JID. Settings use `/etc/sysconfig`: `KEY=value`, no `export`.

```bash
# as root, after /opt/slidgemax is installed
useradd --system --home-dir /var/lib/slidgemax --shell /usr/sbin/nologin slidgemax
install -d -o slidgemax -g slidgemax -m 0750 /var/lib/slidgemax

cp contrib/systemd/slidgemax@.service /etc/systemd/system/

# Required per-instance file. Optional shared defaults: /etc/sysconfig/slidgemax
cat > /etc/sysconfig/slidgemax-max.example.org <<EOF
MAX_COMPONENT_SECRET=your-component-secret
EOF
chmod 640 /etc/sysconfig/slidgemax-max.example.org
chown root:slidgemax /etc/sysconfig/slidgemax-max.example.org

systemctl daemon-reload
systemctl enable --now slidgemax@max.example.org.service
journalctl -u slidgemax@max.example.org -f
```

`ExecStart` is `/opt/slidgemax/bin/slidgemax`. That script starts Slidge; do not install Slidge on its own for this unit. Prosody must use the same JID and the same `MAX_COMPONENT_SECRET`.

## Configuration

Slidge options apply (`--home-dir`, logging, etc.).

Additional runtime flags are not exposed yet; defaults are sensible (reconnect on, ignore groups, call notifications on, placeholder for unsupported media).

PyMax sessions are stored under `$HOME_DIR/max-sessions/`.

## Limitations (as designed)

- 1:1 only (groups out of scope)
- Text, edits, and call notifications only
- No files, voice, stickers, reactions, rich cards, avatars
- PyMax is unofficial

## Development

```bash
uv sync --dev
uv run pytest
```

## License

Apache-2.0
