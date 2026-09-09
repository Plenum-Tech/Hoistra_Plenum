# A Hoistra testing environment

One command. A database with a portfolio already in it, the six backend services, and the
frontend — all on `http://localhost:3000`.

```bash
git clone <this repository>
cd Hoistra
docker compose -f docker-compose.testenv.yml up -d --build
```

First run builds seven images and takes a few minutes. After that it is seconds.

Give it about a minute after `up` before judging it. Most services are serving within
seconds, but `connector-app` installs a shared library at start rather than at build, so
`/backend/connector/` answers 502 until it finishes. `docker compose -f
docker-compose.testenv.yml ps` shows it as `healthy` when it is genuinely ready.

Then open **http://localhost:3000**.

There is no `.env` to fill in, nothing to run by hand afterwards, and no credentials to be
given. If it starts, it works.

---

## What is in it

| | |
|---|---|
| **Buildings** | 9 across four countries — UK, UAE, US, Singapore — with 108 floors, 108 spaces, 54 assets, 54 equipment records and 18 meters |
| **Compliance** | 83 certificates, 72 against buildings and 11 against vendors, scored against the UK (55 types), UAE (45) and US (18) regulation packs |
| **Vendors** | 6 firms, 12 contracts, 108 work orders |
| **Auth** | 6 accounts, and registration that works |

Expiry dates are spread deliberately across every lifecycle state — current, due for
renewal, expiring soon, lapsed. A register where everything is current tells you nothing
about a screen whose job is to show what is not.

**Nothing in it is real.** No customer name, no issued certificate number, no document
that resolves to a file anywhere. It is all invented, and it must stay that way: see
*Do not put real data in it* below.

---

## Signing in

The six demo accounts **cannot sign in**. Their password hash is not a hash of anything —
a demo account with a known password is a back door, and this repository is readable by
anyone who has it. They exist so you can see the account list, the role gates, and the
distinction between a job title and a platform role:

| Name | Job title (`users.role`) | Platform role (`users.platform_role`) |
|---|---|---|
| Rowan Ellis | Facilities Director | `admin` |
| Sam Okafor | Facility Manager | `user` |
| Priya Raman | Maintenance Supervisor | `user` |
| Jo Whitfield | Maintenance Planner | `user` |
| Chris Nakamura | HVAC Specialist | `user` |
| Dana Ilyas | Quality Inspector | `user` |

Two different questions about the same person: a Facilities Director need not administer
anything, and a Maintenance Tech might.

**To get an account you can use, register one.** Email is switched off, so the one-time
code is never sent — and it is never logged or stored either, because a code sitting in a
table is not one-time in any useful sense. Read it from the service log:

```bash
curl -X POST http://localhost:3000/backend/ops-intelligence/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"a-long-enough-passphrase"}'

docker compose -f docker-compose.testenv.yml logs svc-operations-intelligence | tail -40
```

The first account to register becomes a `user`. To make yourself a superadmin, either set
`AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` to your address before first boot, or promote yourself
once the account exists:

```bash
docker compose -f docker-compose.testenv.yml exec postgres \
  psql -U cafm -d hoistra -c \
  "UPDATE plenum_cafm.users SET platform_role='superadmin' WHERE email='you@example.com'"
```

Your password must not contain your own name — the policy refuses that, which surprises
people who name themselves in their passphrase.

---

## Where things are

Everything is behind one port. The gateway serves the frontend and proxies each backend,
so nothing points at a host address and the environment works unchanged behind a tunnel or
on another machine.

| | |
|---|---|
| Frontend | `http://localhost:3000` |
| Compliance / buildings / energy | `http://localhost:3000/backend/ops-intelligence` |
| Auth | `http://localhost:3000/backend/ops-intelligence/api/auth` |
| Connector | `http://localhost:3000/backend/connector` |
| Work orders | `http://localhost:3000/backend/work-order` |
| Schema mapper | `http://localhost:3000/backend/schema-mapper` |
| Deep agents | `http://localhost:3000/backend/deep-agents` |

The database is not published to the host. To query it:

```bash
docker compose -f docker-compose.testenv.yml exec postgres psql -U cafm -d hoistra
```

---

## Starting over

```bash
docker compose -f docker-compose.testenv.yml down -v
docker compose -f docker-compose.testenv.yml up -d
```

**The `-v` matters.** The database loads `db/*.sql` only on its FIRST boot, into an empty
volume. Keep the volume and you keep whatever the last run left in it — including anything
you changed while testing.

---

## Do not put real data in it

`AUTH_JWT_SECRET` and `AUTH_OTP_PEPPER` are fixed values written in
`docker-compose.testenv.yml`, in the open, on purpose: an environment that generates new
ones on every restart invalidates every session and every outstanding code each time it
comes up, which makes testing sign-in tedious for no gain on a throwaway stack.

The consequence is that anyone who can read this repository can forge a token for any
environment started this way. That is fine for invented buildings and unacceptable for
anything else.

The same goes for what you load into it. If you import a real portfolio — real firms, real
certificate numbers, real document URLs — that database is no longer a test environment,
and this file's secrets are no longer a reasonable trade.

---

## If something does not come up

```bash
docker compose -f docker-compose.testenv.yml ps
docker compose -f docker-compose.testenv.yml logs <service> | tail -50
```

A few failures are worth recognising:

**`port is already allocated`** — something already has 3000. Change the published port in
`docker-compose.testenv.yml` (`"3000:8080"` → `"3001:8080"`), or stop the other thing.

**A service restarting, with `MigrationError` in the log** — a migration could not be
applied, and the service refuses to start rather than serve a half-applied schema. The
message names the file and the reason. This is the runner working; read what it says.

**`KeyShapeUnavailable`** — the service could not read what `plenum_cafm.users.id` is keyed
on. On this stack that means the database did not initialise: `down -v` and up again.

**502 Bad Gateway on every `/backend/...` path, but the service says `Up`** — nginx
resolves each upstream's address once, when it starts. Restart or recreate a backend
service on its own and its container gets a new address, which the gateway does not learn.
Restart the gateway after it:

```bash
docker compose -f docker-compose.testenv.yml restart gateway-app
```

A plain `up -d` from nothing never hits this: everything starts in dependency order and no
address changes afterwards.

**The database is empty** — the init scripts only run on an empty volume. If you started
the stack once before `db/04_demo_data.sql` existed, `down -v` and up again.
