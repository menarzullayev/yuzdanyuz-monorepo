# PostgreSQL — Production-Ready System Setup

## The Problem We Solved

Local PostgreSQL installation was crashing due to:
1. **Daemonization issues**: Simple backgrounding (`&`) caused immediate exit
2. **Socket connection refused**: pg_isready reported success but psql couldn't connect
3. **Test database creation**: Django test runner killed daemon
4. **WAL corruption**: Improper shutdown left invalid record length errors

## The Solution: Persistent Daemon

We created a **self-healing daemon** that:
- Monitors PostgreSQL process
- Auto-restarts on crash
- Cleans stale lock files
- Runs indefinitely

### Installation

#### Step 1: Create Daemon Script

```bash
cat > ~/.local/pgsql/daemon.sh << 'DAEMON'
#!/bin/bash
PG="~/.local/pgsql/bin/postgres"
DATA="~/.local/pgsql/data"
RUN="~/.local/pgsql/run"

exec > ~/.local/pgsql/pg.log 2>&1

trap "echo Daemon exiting; exit 0" TERM INT

while true; do
  echo "=== Starting PostgreSQL at $(date) ==="
  rm -f "$DATA/postmaster.pid" "$RUN/.s.PGSQL"*
  "$PG" -D "$DATA" &
  PG_PID=$!
  echo "PostgreSQL PID: $PG_PID"

  wait $PG_PID
  STATUS=$?
  echo "PostgreSQL exited with status $STATUS at $(date)"
  sleep 3
done
DAEMON

chmod +x ~/.local/pgsql/daemon.sh
```

**Key points:**
- `exec > ... 2>&1` redirects output **before** backgrounding
- `trap` for clean shutdown
- `wait $PG_PID` ensures proper child process handling
- `rm -f postmaster.pid` clears stale locks on each restart

#### Step 2: Start Daemon

```bash
~/.local/pgsql/daemon.sh &
sleep 8
```

#### Step 3: Verify

```bash
~/.local/pgsql/bin/psql -h ~/.local/pgsql/run -p 5992 -U hsm -d postgres -c "SELECT 1;"
```

### Managing the Daemon

#### Check Status
```bash
ps aux | grep "daemon.sh\|postgres" | grep -v grep
```

#### View Logs
```bash
tail -50 ~/.local/pgsql/pg.log
```

#### Restart
```bash
pkill -f "daemon.sh"
pkill -9 postgres 2>/dev/null || true
sleep 2
~/.local/pgsql/daemon.sh &
```

#### Clean Shutdown
```bash
pkill -f "daemon.sh"
pkill -TERM postgres
sleep 3
pkill -9 postgres  # If still hanging
```

## Why This Works

### 1. **Output Redirection Before Backgrounding**
```bash
# ❌ Wrong: shell redirects after backgrounding
postgres -D /path &

# ✅ Right: daemon script redirects first
exec > log 2>&1
... (all output goes to file)
postgres -D /path &
```

### 2. **Process Supervision Loop**
```bash
while true; do
  postgres -D $DATA &
  wait $!  # ← Wait for child properly
  # If exits, loop restarts
  sleep 3  # ← Prevent rapid restart loop
done
```

### 3. **Signal Handling**
```bash
trap "exit 0" TERM INT
# Allows: pkill -f daemon.sh (clean)
```

### 4. **Lock File Cleanup**
```bash
rm -f "$DATA/postmaster.pid"
# ← Clear on every restart, prevents "already exists" error
```

## Running Tests

```bash
# Make sure daemon is running
~/.local/pgsql/daemon.sh &
sleep 8

# Run tests
python manage.py test apps.catalog --verbosity=2

# Should see:
# Ran 31 tests in XX.XXXs
# OK
```

## Troubleshooting

### Connection Refused on Socket

**Check:**
1. Daemon running: `ps aux | grep postgres`
2. Socket file exists: `ls -la ~/.local/pgsql/run/.s.PGSQL*`
3. Actual connection: `psql -h ~/.local/pgsql/run -p 5992 -U hsm ...`
4. Logs: `tail -20 ~/.local/pgsql/pg.log`

**Fix:**
```bash
pkill -9 postgres
rm -f ~/.local/pgsql/data/postmaster.pid
rm -f ~/.local/pgsql/run/.s.PGSQL*
~/.local/pgsql/daemon.sh &
sleep 8
```

### "postmaster.pid already exists"

This shouldn't happen with daemon.sh, but if it does:
```bash
rm -f ~/.local/pgsql/data/postmaster.pid
pkill -9 postgres
sleep 2
~/.local/pgsql/daemon.sh &
```

### Test Database Won't Create

Ensure:
1. Daemon is running
2. User `hsm` exists: `psql -U hsm -d postgres -c "SELECT 1"`
3. User can create databases: `psql -U postgres -d postgres -c "ALTER USER hsm CREATEDB;"`

### "redo is not required" / WAL Recovery

This is normal on unclean shutdown. The daemon handles it:
```
LOG: database system was interrupted
LOG: database system was not properly shut down
LOG: redo is not required
LOG: database system is ready to accept connections
```

## Security Considerations

### For Development (Current Setup)
✅ Local socket, `trust` auth
✅ No password required
✅ Acceptable for local testing

### For Production
⚠️ **NEVER** use this setup for production

Instead:
- Use managed cloud database (RDS, CloudSQL, etc.)
- Use containerized PostgreSQL (Docker + systemd)
- Use system-managed PostgreSQL (apt/brew + systemd)
- Enable SSL for network connections
- Use strong passwords
- Enable pg_hba.conf authentication
- Set up replication/backups

## Permanent System Integration

To make daemon start on boot, add to crontab:

```bash
crontab -e
# Add:
@reboot ~/.local/pgsql/daemon.sh &
```

Or create a systemd user service:

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/postgres.service << 'SERVICE'
[Unit]
Description=PostgreSQL Local Daemon
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/pgsql/daemon.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable postgres.service
systemctl --user start postgres.service
```

## Reference

| Command | Purpose |
|---------|---------|
| `~/.local/pgsql/daemon.sh &` | Start daemon |
| `pkill -f daemon.sh` | Stop daemon cleanly |
| `tail ~/.local/pgsql/pg.log` | View logs |
| `psql -h ~/.local/pgsql/run -p 5992 ...` | Connect |
| `python manage.py test` | Run tests (daemon auto-manages) |
