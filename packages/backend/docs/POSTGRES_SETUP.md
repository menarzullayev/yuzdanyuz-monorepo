# PostgreSQL O'rnatish Va Xizmatini Ishga Tushirish

## Muammoli O'rnatish (Hozirgi)

Lokalnomoda `~/.local/pgsql` ga o'rnatilgan PostgreSQL 16.1 bilan ishlashda quyidagi muammolar paydo bo'ladi:

### 1. **Daemon Crash on Startup**
```
FATAL: lock file "postmaster.pid" already exists
Is another postmaster (PID 123) running?
```

**Sababi:** Noaniq kurashtirish yoki xavfsiz to'xtash bo'lmaganda stale lock fayllar qoladi.

**Yechimi:**
```bash
rm -f ~/.local/pgsql/data/postmaster.pid
rm -f ~/.local/pgsql/run/.s.PGSQL*
```

### 2. **Socket Connection Refused**
```
psql: error: connection to server on socket failed: Connection refused
```

Pg_isready accepted qilsa ham, actual biriktirishlar rad etiladi.

**Sababi:** Socket permissions, authentication, yoki daemon shaxsi shutdown.

**Yechimi:** Socket sockdir permissionslarini tekshiring:
```bash
ls -la ~/.local/pgsql/run/
# Should show: srwxrwxrwx for .s.PGSQL.5992
chmod 777 ~/.local/pgsql/run
```

## Tavsiya Qilingan O'rnatish

### **Variyant 1: Docker (Ideal - Prod-Ready)**

```bash
docker run --name yuzdanyuz-db \
  -e POSTGRES_USER=hsm \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=yuzdanyuz_db \
  -p 5432:5432 \
  -v postgres_data:/var/lib/postgresql/data \
  -d postgres:16

# .env ga:
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=hsm
DB_PASSWORD=password
```

### **Variyant 2: Homebrew (macOS)**

```bash
brew install postgresql@16
brew services start postgresql@16

createdb yuzdanyuz_db
createuser -P hsm  # Password: (set password)
```

### **Variyant 3: System Package (Linux)**

```bash
sudo apt install postgresql postgresql-contrib
sudo -u postgres createdb yuzdanyuz_db
sudo -u postgres createuser -P hsm
```

### **Variyant 4: Manual Compiled (Faqat zarar - Hozirgi Setup)**

Agar `~/.local/pgsql` dan foydalanishning kerak bo'lsa:

```bash
# 1. Xavfsiz tugatish
~/.local/pgsql/bin/pg_ctl stop -D ~/.local/pgsql/data

# 2. Recovery mode bilan qayta ishga tushirish
~/.local/pgsql/bin/postgres -D ~/.local/pgsql/data -c synchronous_commit=off

# 3. Tugatish va qayta ishga tushirish (clean startup)
~/.local/pgsql/bin/pg_ctl stop -m fast -D ~/.local/pgsql/data
rm -f ~/.local/pgsql/data/postmaster.pid
~/.local/pgsql/bin/postgres -D ~/.local/pgsql/data &
sleep 5
~/.local/pgsql/bin/pg_isready -h ~/.local/pgsql/run -p 5992
```

## Django Sozlamalar

### `.env` (Development)

```bash
# Simple TCP connection (recommended)
DB_HOST=127.0.0.1
DB_PORT=5432
DB_USER=hsm
DB_PASSWORD=your_password
DB_NAME=yuzdanyuz_db

# Socket connection (risky)
# DB_HOST=/var/run/postgresql
# DB_PORT=5432
```

### `settings/base.py`

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'yuzdanyuz_db'),
        'USER': os.getenv('DB_USER', 'hsm'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}
```

## Test Ishga Tushirish

### Variant 1: Real PostgreSQL (Tavsiyalangan)

```bash
# 1. Ensure DB is running
pg_isready -h localhost -p 5432

# 2. Run tests
python manage.py test apps.catalog --verbosity=2

# 3. Keep logs
python manage.py test apps.catalog --verbosity=2 2>&1 | tee test.log
```

### Variant 2: In-Memory SQLite (Tezlik)

```bash
DJANGO_DB_ENGINE=sqlite3 python manage.py test apps.catalog
```

## Tushunchalar

| Muammo | Belgilari | Yechim |
|--------|----------|--------|
| **Daemon Crush** | "postmaster.pid exists" | Stale PID faylni o'chiring |
| **Socket Failed** | "Connection refused on socket" | Socket permissions, recovery mode |
| **Auth Failed** | "role hsm does not exist" | `createuser hsm` bilan foydalanuvchi yarat |
| **Test DB Error** | "permission denied to create database" | `ALTER USER hsm CREATEDB;` |
| **Slow Startup** | Recovery takes 30s+ | `synchronous_commit=off` qayta qurish rejimida |

## Ishlab Chiqarish (Production)

**NEVER** ishlamachanmada local PG binary dan foydalanmang. Faqat:
- RDS/Cloud Database
- Docker container
- System-managed PostgreSQL (systemd)

Qaragani ko'ring: [DEPLOYMENT.md](DEPLOYMENT.md)
