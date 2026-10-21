# Paddler accounts and per-person session visibility

Date: 2026-10-21
Status: proposed — awaiting approval

## Intent

The app now listens on the public internet at `paddle.funston.com`. Anyone who
reaches it can read every session, upload anything, and delete any session. There
is no notion of a person in the data model: an activity is an anonymous row.

Richard wants real per-person accounts. A session is a shared record — a canoe
has two paddlers, a solo run has one — so the relationship is many-to-many. A
signed-in paddler sees only the sessions they are linked to, and nothing else.

## Goals

- A paddler is an account with an email, a normal name, and a password.
- A session carries a list of paddlers. Several paddlers may share one session.
- A signed-in paddler sees only sessions they are linked to.
- An admin screen lists paddlers, creates them, and changes their passwords.
- One initial paddler, `richard`, is created as an admin.
- Every session that exists today is linked to `richard`.

## Non-goals

- Public self-registration. Accounts are created by an admin.
- Password reset by email; there is no mail path from this host.
- OAuth, SSO, or any external identity provider.
- Per-session roles. Linkage is binary: you are on the session or you are not.
- Per-paddler statistics or split times within a shared session.

## Anti-goals

These would each mean the feature technically works but has failed.

- Any signed-in paddler reaching a session they are not linked to.
- A password, or its hash, readable in the repository.
- A session cookie that keeps working after the password behind it changes.
- Losing or orphaning an existing session during the migration.

## Constraints

- Flask 3 + SQLite. No new external service, and no new runtime dependency:
  password hashing uses `werkzeug.security`, which ships with Flask.
- Must work both at the root (as seen through NPM at `paddle.funston.com`) and
  under the `/sport` path mount that Caddy applies on the LAN.
- The existing suite (~120 tests) must keep passing.
- Live data lives at `/var/lib/sport-data-analyzer` and must migrate in place.

## Design

### Schema

Two tables are added. They are created on startup if absent, so the migration is
the same code path as a fresh install.

**Backfill runs on every startup and is idempotent.** It links every activity
that has zero rows in `activity_paddlers` to an admin account, and does nothing
when no admin exists yet. It is deliberately *not* tied to the empty-users-table
bootstrap: the rollout creates `richard` with the CLI, which is the other branch
of that condition, and gating the backfill there would leave every pre-existing
session linked to nobody — invisible to every account and indistinguishable from
having lost them. Running it unconditionally on startup is safe because the app
refuses to remove the last paddler from a session, so a zero-link activity cannot
arise afterwards. A second run inserts nothing.

```sql
CREATE TABLE users (
    id            TEXT PRIMARY KEY,          -- 8 hex chars
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,   -- stored lowercased
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    session_epoch INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    modified_at   TEXT NOT NULL
);

CREATE TABLE activity_paddlers (
    activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id)     ON DELETE CASCADE,
    PRIMARY KEY (activity_id, user_id)
);
CREATE INDEX activity_paddlers_user ON activity_paddlers (user_id);
```

`email` is stored lowercased and compared lowercased, so `Rpf@` and `rpf@` are
one account.

The column is declared `COLLATE NOCASE` because SQLite's default collation for
`TEXT` is `BINARY`, so a plain `UNIQUE` compares bytes and accepts
`RPF@funston.com` sitting next to `rpf@funston.com` — confirmed against the local
SQLite, which took the duplicate under a plain `UNIQUE` and rejected it under
`NOCASE`. Without the declaration the single-account rule is only as strong as
every write path remembering to lowercase, and the admin form, the CLI and the
environment bootstrap are three separate chances to forget. Python lowercasing
stays; the collation is what makes the database enforce it too.

### Passwords

`werkzeug.security.generate_password_hash` / `check_password_hash`, which use
scrypt with a per-password salt. Hashes are written to SQLite and nowhere else.

Richard's password is **not** committed. The bootstrap reads it from the
environment (`SDA_INITIAL_PADDLER_PASSWORD`) and runs only when the users table
is empty, so a fresh clone never carries a credential. A small CLI does the same
job on a live host:

    python -m app.cli create-paddler --email rpf@funston.com --name Richard \
        --password '…' --admin

### Signing in

A signed Flask session cookie carries the paddler's id and their
`session_epoch`. `SECRET_KEY` comes from `SDA_SECRET_KEY`; when that is unset the
app generates a random key once and persists it to `<DATA_DIR>/secret_key`
(mode 0600). The current default of the literal string `sport-data-analyzer` is
removed — it is public in the repository, so any reader could forge a cookie.

The write must be **create-exclusive and then re-read**. `wsgi.py` calls
`create_app()` at import time and the unit runs `gunicorn --workers 2` with no
`--preload`, so two processes build the app independently at start. If the key
file is absent, both generate one; whichever writes last wins, and the other
worker keeps serving cookies signed with a key the file no longer holds. Roughly
half of all sign-ins would then be accepted by one worker and rejected by the
other — "signed in, then signed out on the next click" in production, and never
in tests, which run a single in-process app. Creating with
`O_CREAT | O_EXCL` and re-reading on `FileExistsError` makes every worker converge
on one value regardless of start order.

Changing a password increments `session_epoch`, which makes every cookie issued
before the change stop validating. The cost is one integer; the alternative is
that a password change does not actually evict anyone.

### Access control

Three helpers in `app/auth.py`:

| Helper | Effect |
|---|---|
| `current_paddler()` | the signed-in paddler, or `None` |
| `@login_required` | redirects to `/login?next=…` when not signed in |
| `@admin_required` | redirect when anonymous, `403` when signed in but not an admin |

A deactivated paddler (`is_active = 0`) is treated as signed out.
`@admin_required` resolves the signed-in paddler first: anonymous gets the same
redirect as `@login_required`, and only a signed-in non-admin gets a `403`. A
naive `403 unless is_admin` would answer an anonymous request with 403, telling
someone who is not signed in that the route exists.

Visibility is enforced in the store, not the template: `Store.list_for(user_id)`
and `Store.get_for(activity_id, user_id)` join through `activity_paddlers`. A
session the paddler is not linked to is a **404**, not a 403 — an outsider should
not learn that a given id exists.

### Routes

| Route | Who | Purpose |
|---|---|---|
| `GET/POST /login` | anyone | sign in |
| `POST /logout` | signed in | sign out |
| `GET /admin/paddlers` | admin | list, create, change password |
| `POST /admin/paddlers/new` | admin | create a paddler |
| `POST /admin/paddlers/<id>/password` | admin | set a new password |
| `POST /admin/paddlers/<id>/update` | admin | rename, toggle admin, toggle active |
| `POST /api/a/<id>/paddlers` | linked | add or remove a paddler on a session |

Existing routes gain `@login_required`, and index / report / download / delete
are scoped to the signed-in paddler:

- **index** — only linked sessions, and the header count is scoped the same way.
  The view currently passes `count=store.count()`, which is global; left alone,
  the header would report every session on the deployment above a list showing
  only the paddler's own.
- **healthz** — stays public but stops returning `activities`. It currently
  discloses the global session count to any anonymous caller, which is what the
  visibility rule exists to prevent.
- **upload** — links the uploading paddler to the new session automatically.
  Re-uploading an identical file that already exists links the uploader to it
  rather than failing, so a second paddler can add the same file.
- **report / download** — 404 unless linked.
- **delete** — linked paddlers may delete, since they can already see it.

### Naming

Templates gain `login.html` and `admin_paddlers.html`, both extending the
existing `base.html`. The base template gets the signed-in paddler's name and a
sign-out control in the header.

## Calls I am making

These are mine, not Richard's. They are listed so they can be overruled.

- **Any paddler linked to a session may add or remove paddlers on it.** A shared
  session behaves like a shared document, and the group is a household or club.
  The alternative — admin-only sharing — means Richard gets asked to add his own
  crew every time, which is the friction this feature exists to remove.
- **Deletion stays with linked paddlers**, for the same reason.
- **Sessions last 12 hours and slide** on each request. Long enough to upload
  from a phone at the river, short enough that a lost phone is not a standing key.
- **Richard's password is 10 digits, all numeric.** Implemented exactly as asked
  and changeable from the admin screen. Noted, not argued.

## Testing

The suite is pytest against a temp data directory, so no network or live data is
needed. New coverage:

- creating, fetching, and re-emailing a paddler; duplicate email rejected
- password verification: correct, wrong, and case-sensitivity of the email
- login → logout, wrong password, deactivated account
- a password change invalidating an earlier cookie (the `session_epoch` path)
- visibility: linked paddler sees the session; unlinked paddler gets 404 on
  report, download, and delete; index lists only their own
- admin routes rejecting a non-admin with 403 and an anonymous user with a redirect
- upload linking the uploader, and a re-upload linking a second paddler
- the migration: an existing session with no links becomes Richard's, and a
  second run changes nothing

## Rollout

1. Build and test on the LAN `/sport` mount first, where the current app is
   already reachable, so the public site is not the test bed.
2. Back up `/var/lib/sport-data-analyzer/sport.db` before deploying.
3. Deploy, run the CLI to create `richard`, then confirm the existing sessions
   appear under that account — the startup backfill is what links them, so check
   the count on the index against the pre-deploy total before moving on. Then
   check the public hostname.
4. NPM's basic-auth layer stays for now as a second gate; remove it once the
   app's own login has been exercised for real.
