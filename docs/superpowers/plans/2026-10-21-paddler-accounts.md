# Paddler Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the sport data analyzer real per-person accounts, where each session carries a list of paddlers and a signed-in paddler sees only the sessions they are linked to.

**Architecture:** Two tables are added to the existing SQLite store — `users` and the `activity_paddlers` join table. Visibility is enforced inside `Store` (`list_for`, `get_for`) rather than in templates, so a session a paddler is not linked to is a 404 rather than a redirect. A small `app/auth.py` puts a paddler id plus a `session_epoch` in Flask's signed cookie; bumping the epoch on a password change invalidates every cookie issued before it. Templates stay thin and extend the existing `base.html`.

**Tech Stack:** Python 3.11, Flask 3, SQLite (stdlib `sqlite3`), `werkzeug.security` for hashing (ships with Flask — no new runtime dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-10-21-paddler-accounts-design.md`

## Global Constraints

- Python `>=3.11`; Flask `>=3.0,<4.0`; `fitdecode>=0.10,<1.0`; `gunicorn>=21.2,<24.0`. **No new runtime dependency** — password hashing uses `werkzeug.security`.
- SQLite only. Schema changes are `CREATE TABLE IF NOT EXISTS`, applied on `Store.__init__`, so a fresh install and the live instance share one code path.
- `PRAGMA foreign_keys=ON` is already set per connection in `Store._connect`; `activity_paddlers` relies on it for `ON DELETE CASCADE`.
- The app must work both at the root (public, via NPM) and under the `/sport` path mount (LAN, via Caddy `handle_path`). `url_for` is used for every link — never a hardcoded absolute path.
- Sessions last 12 hours and slide on each request.
- Unlinked session → **404**, not 403. Anonymous → redirect to `/login?next=<path>`.
- The password for the initial paddler is **never committed**. It arrives via `SDA_INITIAL_PADDLER_PASSWORD` or the CLI.
- Commit after every task. Run `pytest -q` before each commit.

## Review Focus

Failure modes the spec implies but no single task's tests would otherwise catch. Each gets a test in the task that owns the code.

1. **A paddler is deactivated (or their password changes) while already signed in.** Their existing cookie must stop working on the next request, not at some later point.
2. **Email case and stray whitespace at login.** `rpf@funston.com` signed up as `rpf@funston.com` must be reachable by typing ` RPF@Funston.com ` — people type what their keyboard gives them.
3. **The last remaining admin deactivates or demotes themselves.** Nothing in the design prevents a lockout with no way back in via the UI.
4. **An activity is deleted while linked to several paddlers.** The join rows must cascade; a stale row must not make the activity resurface or break the index query.
5. **A second paddler re-uploads a file that already exists.** The dedupe path returns the existing activity instead of inserting, and must *also* link the new uploader — otherwise the feature silently fails for exactly the case it was built for.

---

### Task 1: Users and linkage in the store

**Files:**
- Modify: `app/storage.py`
- Test: `tests/test_users.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `DuplicateEmail(Exception)`
  - `Store.create_paddler(email: str, name: str, password: str, *, is_admin: bool = False, is_active: bool = True) -> dict[str, Any]`
  - `Store.get_paddler(user_id: str) -> dict[str, Any] | None`
  - `Store.get_paddler_by_email(email: str) -> dict[str, Any] | None`
  - `Store.verify_paddler(email: str, password: str) -> dict[str, Any] | None`
  - `Store.list_paddlers() -> list[dict[str, Any]]`
  - `Store.update_paddler(user_id: str, *, name: str | None = None, is_admin: bool | None = None, is_active: bool | None = None) -> dict[str, Any] | None`
  - `Store.set_password(user_id: str, password: str) -> bool`
  - `Store.session_epoch_for(user_id: str) -> int | None`
  - `Store.link_paddler(activity_id: str, user_id: str) -> bool`
  - `Store.unlink_paddler(activity_id: str, user_id: str) -> bool`
  - `Store.paddlers_for(activity_id: str) -> list[dict[str, Any]]`
  - `Store.list_for(user_id: str, limit: int = 100) -> list[dict[str, Any]]`
  - `Store.get_for(activity_id: str, user_id: str) -> dict[str, Any] | None`
  - `Store.count_for(user_id: str) -> int`
  - `Store.count_admins(*, active_only: bool = False) -> int`
  - `Store.first_admin() -> dict[str, Any] | None`
  - `Store.backfill_paddlers(user_id: str) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_users.py
import pytest
from app.storage import DuplicateEmail, Store


def test_create_and_fetch_paddler(store):
    user = store.create_paddler("rpf@funston.com", "Richard Funston", "8983498131", is_admin=True)
    assert user["email"] == "rpf@funston.com"
    assert user["name"] == "Richard Funston"
    assert user["is_admin"] == 1
    assert "password_hash" not in user or user["password_hash"] != "8983498131"
    assert store.get_paddler_by_email("rpf@funston.com")["id"] == user["id"]
    assert store.get_paddler(user["id"])["name"] == "Richard Funston"


def test_email_is_lowercased_and_trimmed(store):
    user = store.create_paddler("  RPF@Funston.COM  ", "Richard", "pw")
    assert user["email"] == "rpf@funston.com"


def test_duplicate_email_rejected(store):
    store.create_paddler("rpf@funston.com", "Richard", "pw")
    with pytest.raises(DuplicateEmail):
        store.create_paddler("RPF@funston.com", "Someone Else", "pw")


def test_verify_paddler(store):
    store.create_paddler("rpf@funston.com", "Richard", "8983498131")
    assert store.verify_paddler("rpf@funston.com", "8983498131") is not None
    assert store.verify_paddler("RPF@funston.com ", "8983498131") is not None
    assert store.verify_paddler("rpf@funston.com", "wrong") is None
    assert store.verify_paddler("nobody@funston.com", "8983498131") is None


def test_set_password_bumps_session_epoch(store):
    user = store.create_paddler("rpf@funston.com", "Richard", "old")
    before = store.session_epoch_for(user["id"])
    assert store.set_password(user["id"], "new") is True
    assert store.session_epoch_for(user["id"]) == before + 1
    assert store.verify_paddler("rpf@funston.com", "new") is not None
    assert store.verify_paddler("rpf@funston.com", "old") is None


def test_deactivating_does_not_delete_the_row(store):
    user = store.create_paddler("rpf@funston.com", "Richard", "pw")
    updated = store.update_paddler(user["id"], is_active=False)
    assert updated["is_active"] == 0
    assert store.get_paddler(user["id"]) is not None


def test_link_and_visibility(store, saved_activity):
    a = store.create_paddler("a@funston.com", "A", "pw")
    b = store.create_paddler("b@funston.com", "B", "pw")
    assert store.link_paddler(saved_activity, a["id"]) is True
    assert store.link_paddler(saved_activity, a["id"]) is False  # already linked
    assert [p["id"] for p in store.paddlers_for(saved_activity)] == [a["id"]]
    assert store.get_for(saved_activity, a["id"]) is not None
    assert store.get_for(saved_activity, b["id"]) is None
    assert [r["id"] for r in store.list_for(a["id"])] == [saved_activity]
    assert store.list_for(b["id"]) == []
    assert store.count_for(a["id"]) == 1
    assert store.unlink_paddler(saved_activity, a["id"]) is True
    assert store.get_for(saved_activity, a["id"]) is None


def test_deleting_an_activity_cascades_its_links(store, saved_activity):
    a = store.create_paddler("a@funston.com", "A", "pw")
    store.link_paddler(saved_activity, a["id"])
    store.delete(saved_activity)
    assert store.paddlers_for(saved_activity) == []
    assert store.list_for(a["id"]) == []


def test_backfill_links_only_unlinked_activities(store, saved_activity):
    richard = store.create_paddler("rpf@funston.com", "Richard", "pw")
    other = store.create_paddler("o@funston.com", "O", "pw")
    store.link_paddler(saved_activity, other["id"])
    assert store.backfill_paddlers(richard["id"]) == 0
    assert store.backfill_paddlers(other["id"]) == 0
```

Add to `tests/conftest.py` a `saved_activity` fixture that needs no .fit file:
save a minimal report payload through the existing `Store.save` and return its id.

```python
@pytest.fixture
def saved_activity(store):
    report = {
        "version": REPORT_VERSION,
        "meta": {"start_time": "2024-06-15T08:00:00+00:00", "sport": "canoeing"},
        "summary": {"elapsed_s": 100.0, "distance_m": 500.0},
        "series": [],
    }
    record, _ = store.save("canoe.fit", b"\x00fit", report)
    return record["id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_users.py -q`
Expected: FAIL — `ImportError: cannot import name 'DuplicateEmail'`.

- [ ] **Step 3: Add the schema**

Append to `SCHEMA` in `app/storage.py`, after the `activities` index:

```sql
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    session_epoch INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    modified_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_paddlers (
    activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    PRIMARY KEY (activity_id, user_id)
);
CREATE INDEX IF NOT EXISTS activity_paddlers_user ON activity_paddlers (user_id);
```

- [ ] **Step 4: Implement the paddler methods**

`DuplicateEmail` is a plain `Exception` subclass. Normalisation happens in one
private helper so it cannot be forgotten on one path:

```python
@staticmethod
def _normalise_email(email: str) -> str:
    return email.strip().lower()
```

`create_paddler` catches `sqlite3.IntegrityError` and re-raises `DuplicateEmail`.
`set_password` writes a fresh hash **and** `session_epoch = session_epoch + 1`,
sharing one `datetime.now(timezone.utc).isoformat()` timestamp update.
`session_epoch_for` returns `None` for an unknown id. `link_paddler` uses
`INSERT OR IGNORE` and reports `cursor.rowcount > 0`.

- [ ] **Step 5: Add the scoped reads**

`list_for`, `get_for` and `count_for` join through `activity_paddlers`. Their
`ORDER BY` matches the existing `list()` so ordering does not change:

```sql
SELECT a.* FROM activities a
  JOIN activity_paddlers ap ON ap.activity_id = a.id
 WHERE ap.user_id = ?
 ORDER BY COALESCE(a.start_time, a.uploaded_at) DESC
 LIMIT ?
```

`count_admins` counts rows where `is_admin = 1`, adding `AND is_active = 1` when
`active_only` is set. Task 6 uses it to refuse the change that would leave zero
admins.

`backfill_paddlers` inserts link rows for every activity with no link row at all,
and returns the number inserted:

```sql
INSERT INTO activity_paddlers (activity_id, user_id)
SELECT id, ? FROM activities
 WHERE id NOT IN (SELECT activity_id FROM activity_paddlers)
```

**The invocation is what the rollout depends on.** `create_app` calls
`store.backfill_paddlers(admin["id"])` on every startup whenever
`store.count_admins(active_only=True)` is non-zero, using `store.first_admin()` to
pick the target. It must **not** be gated on the empty-users-table bootstrap: the
rollout creates `richard` through the CLI, which takes the other branch, and every
pre-existing session would then be linked to nobody — visible to no account, and
indistinguishable from having lost the data.

Add a test driving this through `create_app`, not just through the store method.
The store method passing proves nothing about whether anything calls it.

```python
def test_startup_links_orphaned_activities(tmp_path, monkeypatch):
    """Backfill is wired into the factory, not just available in the store."""
    store = Store(tmp_path)
    store.save("old.fit", b"\x00old", _report())
    store.create_paddler("rpf@funston.com", "Richard", "pw", is_admin=True)

    monkeypatch.setenv("SDA_DATA_DIR", str(tmp_path))
    app = create_app()
    user = Store(tmp_path).get_paddler_by_email("rpf@funston.com")

    assert Store(tmp_path).count_for(user["id"]) == 1


def test_startup_backfill_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SDA_DATA_DIR", str(tmp_path))
    create_app()
    create_app()
    store = Store(tmp_path)
    user = store.get_paddler_by_email("rpf@funston.com")
    assert store.count_for(user["id"]) == 1
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_users.py tests/test_web.py -q`
Expected: PASS. `test_web.py` is included because `Store.list`/`get`/`count`
must keep their current behaviour for existing callers.

- [ ] **Step 7: Commit**

```bash
git add app/storage.py tests/test_users.py tests/conftest.py
git commit -m "feat: users and per-session paddler links in the store"
```

---

### Task 2: Session auth module

**Files:**
- Create: `app/auth.py`
- Modify: `app/config.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Store` and `DuplicateEmail` from Task 1.
- Produces:
  - `load_secret_key(data_dir: Path) -> str`
  - `hash_password(password: str) -> str`
  - `check_password(password: str, password_hash: str) -> bool`
  - `current_paddler() -> dict[str, Any] | None`
  - `login_paddler(user: dict[str, Any]) -> None`
  - `logout_paddler() -> None`
  - `login_required(view)` — decorator
  - `admin_required(view)` — decorator

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
from app.auth import check_password, hash_password, load_secret_key


def test_password_roundtrip():
    digest = hash_password("8983498131")
    assert digest != "8983498131"
    assert check_password("8983498131", digest) is True
    assert check_password("8983498132", digest) is False


def test_same_password_hashes_differently():
    assert hash_password("pw") != hash_password("pw")


def test_secret_key_is_stable_and_not_the_old_default(tmp_path):
    first = load_secret_key(tmp_path)
    assert first == load_secret_key(tmp_path)
    assert first != "sport-data-analyzer"
    assert len(first) >= 32


def test_secret_key_file_is_private(tmp_path):
    load_secret_key(tmp_path)
    mode = (tmp_path / "secret_key").stat().st_mode & 0o777
    assert mode == 0o600


def test_login_session_roundtrip(client, store):
    user = store.create_paddler("a@funston.com", "A", "pw")
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["epoch"] = user["session_epoch"]
    response = client.get("/")
    assert response.status_code == 200


def test_deactivated_paddler_is_treated_as_signed_out(client, store):
    user = store.create_paddler("a@funston.com", "A", "pw")
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["epoch"] = user["session_epoch"]
    store.update_paddler(user["id"], is_active=False)
    assert client.get("/").status_code == 302


def test_stale_epoch_is_treated_as_signed_out(client, store):
    user = store.create_paddler("a@funston.com", "A", "pw")
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["epoch"] = user["session_epoch"]
    store.set_password(user["id"], "new")
    assert client.get("/").status_code == 302


def test_admin_required_rejects_non_admin(client, store):
    user = store.create_paddler("a@funston.com", "A", "pw", is_admin=False)
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["epoch"] = user["session_epoch"]
    assert client.get("/admin/paddlers").status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 3: Implement `app/auth.py`**

`load_secret_key` returns `SDA_SECRET_KEY` when set; otherwise it reads
`<data_dir>/secret_key`, creating it with `secrets.token_urlsafe(48)` written
through `os.open(..., O_CREAT | O_EXCL | O_WRONLY, 0o600)` so the file is never
briefly world-readable.

**On `FileExistsError`, re-read the file and return its contents** rather than
generating a new key. `wsgi.py` calls `create_app()` at import and the unit runs
`gunicorn --workers 2` with no `--preload`, so two processes reach this code
independently on a cold start. Without the re-read, both write a key, the loser's
in-memory key no longer matches the file, and about half of all sign-ins are
issued a cookie the other worker rejects — a bug that cannot reproduce in tests,
which build a single in-process app.

`current_paddler()` reads `session["user_id"]` and `session["epoch"]`, fetches the
row, and returns `None` unless the row exists, `is_active` is truthy, and the
stored `session_epoch` matches the cookie's `epoch`. This single function is what
implements Review Focus items 1 and 2.

`login_paddler` writes both keys and calls `session.permanent = True`.
`login_required` redirects with `url_for("sda.login", next=request.full_path)`.

`admin_required` resolves the signed-in paddler **first**: if there is none it
behaves exactly like `login_required` (302 to `/login`), and only a signed-in
paddler who is not an admin gets `abort(403)`. A naive `403 unless is_admin`
answers anonymous requests with 403 and tells a stranger the route exists.

- [ ] **Step 4: Wire the secret key into config and the factory**

`SDA_SECRET_KEY = os.environ.get("SDA_SECRET_KEY")` in `Config` with **no
insecure default**. In `create_app`, after the store is built, set
`app.config["SECRET_KEY"] = load_secret_key(config_object.DATA_DIR)` when the
env var is unset, and `app.permanent_session_lifetime = timedelta(hours=12)`.

Also set `app.config["SESSION_COOKIE_SAMESITE"] = "Lax"` and
`app.config["SESSION_COOKIE_HTTPONLY"] = True` explicitly. Flask's `HttpOnly`
default is already true, but its `SameSite` default is to emit no attribute at
all, leaving the behaviour to whichever browser is asking. Setting `Lax` is what
actually stops a cross-site form post from carrying the session cookie, and it
costs one line rather than a token library.

Flask re-issues the cookie on every request for a permanent session whenever
`SESSION_REFRESH_EACH_REQUEST` is true — that is what makes the 12 hours slide
rather than expire 12 hours after sign-in. Assert the setting rather than
assuming it, since it is a config value someone can flip:

```python
def test_disabling_session_refresh_would_break_sliding(app):
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_auth.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/auth.py app/config.py app/__init__.py tests/test_auth.py
git commit -m "feat: signed-cookie sessions with epoch invalidation"
```

---

### Task 3: Sign in and sign out

**Files:**
- Create: `app/templates/login.html`
- Modify: `app/routes.py`, `app/templates/base.html`, `app/static/css/style.css`
- Test: `tests/test_auth.py` (append)

**Interfaces:**
- Consumes: `current_paddler`, `login_paddler`, `logout_paddler` from Task 2.
- Produces: routes `sda.login` (`/login`) and `sda.logout` (`/logout`).

- [ ] **Step 1: Write the failing tests**

```python
def test_login_page_renders(client):
    assert client.get("/login").status_code == 200


def test_login_with_correct_credentials_redirects_to_root(client, store):
    store.create_paddler("rpf@funston.com", "Richard", "8983498131")
    response = client.post(
        "/login", data={"email": "rpf@funston.com", "password": "8983498131"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert client.get("/").status_code == 200


def test_login_honours_the_next_parameter(client, store):
    store.create_paddler("rpf@funston.com", "Richard", "pw")
    response = client.post(
        "/login", data={"email": "rpf@funston.com", "password": "pw", "next": "/a/abc123"}
    )
    assert response.headers["Location"].endswith("/a/abc123")


def test_login_rejects_a_foreign_next_target(client, store):
    store.create_paddler("rpf@funston.com", "Richard", "pw")
    response = client.post(
        "/login",
        data={"email": "rpf@funston.com", "password": "pw", "next": "https://evil.example/"},
    )
    assert "evil.example" not in response.headers["Location"]


def test_login_with_wrong_password_shows_an_error(client, store):
    store.create_paddler("rpf@funston.com", "Richard", "pw")
    response = client.post("/login", data={"email": "rpf@funston.com", "password": "nope"})
    assert response.status_code == 200
    assert b"incorrect" in response.data.lower()


def test_login_rejects_a_deactivated_paddler(client, store):
    user = store.create_paddler("rpf@funston.com", "Richard", "pw")
    store.update_paddler(user["id"], is_active=False)
    response = client.post("/login", data={"email": "rpf@funston.com", "password": "pw"})
    assert response.status_code == 200
    assert b"incorrect" in response.data.lower()


def test_logout_clears_the_session(client, store):
    store.create_paddler("rpf@funston.com", "Richard", "pw")
    client.post("/login", data={"email": "rpf@funston.com", "password": "pw"})
    client.post("/logout")
    assert client.get("/").status_code == 302


def test_anonymous_root_redirects_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_auth.py -q`
Expected: FAIL — 404 from `/login`.

- [ ] **Step 3: Implement the routes**

`GET /login` renders `login.html` with `next` from `request.args`. `POST /login`
calls `store.verify_paddler(email, password)`; on success calls `login_paddler`
and redirects to `next` **only when it is a safe local path**, on failure
re-renders with a single generic message — never "no such user" versus "wrong
password", which would let anyone enumerate accounts. `POST /logout` calls
`logout_paddler()` and redirects to `login`.

`base.html` gains a header block showing `current_paddler()["name"]` with a
sign-out form (`POST`) and an *Paddlers* link when `is_admin`. Add the header
markup to `style.css` using the existing CSS custom properties in that file.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_auth.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes.py app/templates/login.html app/templates/base.html app/static/css/style.css tests/test_auth.py
git commit -m "feat: sign in and sign out"
```

---

### Task 4: Scope every existing route to the signed-in paddler

**Files:**
- Modify: `app/routes.py`
- Test: `tests/test_visibility.py`

**Interfaces:**
- Consumes: `Store.list_for`, `get_for`, `count_for`, `link_paddler` (Task 1); `login_required` (Task 2).
- Produces: `Store.save` gains a keyword-only `user_id: str | None = None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_visibility.py
def test_index_lists_only_linked_sessions(signed_in, store, saved_activity, another_activity):
    ...

def test_report_is_404_for_an_unlinked_paddler(...): ...
def test_download_is_404_for_an_unlinked_paddler(...): ...
def test_delete_is_404_for_an_unlinked_paddler(...): ...
def test_delete_removes_it_for_a_linked_paddler(...): ...
def test_upload_links_the_uploader(client, store, sample_fit_bytes): ...
def test_reupload_of_an_identical_file_links_a_second_paddler(...): ...
def test_healthz_stays_public(...): ...
```

Shared fixtures, added to `tests/conftest.py`:

```python
@pytest.fixture
def sample_fit_bytes():
    return (Path(__file__).parent / "fixtures" / "canoe-session.fit").read_bytes()


@pytest.fixture
def signed_in(client, store):
    """A client already carrying a session for a fresh, non-admin paddler."""
    user = store.create_paddler("a@funston.com", "A", "pw")
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["epoch"] = user["session_epoch"]
    client.paddler = user
    return client


@pytest.fixture
def admin_client(client, store):
    """The same, for an admin."""
    user = store.create_paddler("admin@funston.com", "Admin", "pw", is_admin=True)
    with client.session_transaction() as sess:
        sess["user_id"] = user["id"]
        sess["epoch"] = user["session_epoch"]
    client.paddler = user
    return client


@pytest.fixture
def another_activity(store):
    """A second saved activity, linked to nobody."""
    report = {
        "version": REPORT_VERSION,
        "meta": {"start_time": "2024-07-01T08:00:00+00:00", "sport": "canoeing"},
        "summary": {"elapsed_s": 200.0, "distance_m": 900.0},
        "series": [],
    }
    record, _ = store.save("other.fit", b"\x00fit-other", report)
    return record["id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_visibility.py -q`
Expected: FAIL — the index shows another paddler's session.

- [ ] **Step 3: Apply `@login_required` and scope the reads**

`index` → `store.list_for(user["id"])` and `store.count_for(user["id"])`.

`activity`, `download`, `delete` → `store.get_for(activity_id, user["id"])`,
`abort(404)` when `None`.

`healthz` stays public but drops `activities` from its payload — a global count
readable by any anonymous caller is exactly what the visibility rule exists to
prevent. Add to `tests/test_visibility.py`:

```python
def test_healthz_does_not_leak_the_global_count(client, store, saved_activity):
    body = client.get("/healthz").get_json()
    assert body == {"ok": True}


def test_index_count_is_scoped_to_the_paddler(signed_in, store, saved_activity, another_activity):
    """another_activity belongs to nobody, so the header must say 1, not 2."""
    store.link_paddler(saved_activity, signed_in.paddler["id"])
    assert b"1" in signed_in.get("/").data
```

- [ ] **Step 4: Make upload link the uploader**

`Store.save(filename, raw, report, *, user_id=None)` links `user_id` to the
activity **on both paths** — the fresh insert and the existing-duplicate return.
This is Review Focus item 5: without it, a second paddler re-uploading the shared
file is not linked and the session never appears for them.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_visibility.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `pytest -q`
Expected: PASS — the existing `test_web.py` needs its requests to carry a session
now; update those tests to use the `signed_in` fixture rather than weakening them.

- [ ] **Step 7: Commit**

```bash
git add app/routes.py app/storage.py tests/test_visibility.py tests/test_web.py
git commit -m "feat: scope every session route to the linked paddler"
```

---

### Task 5: Manage the paddlers on a session

**Files:**
- Modify: `app/routes.py`, `app/templates/activity.html`, `app/static/js/report.js`
- Test: `tests/test_visibility.py` (append)

**Interfaces:**
- Consumes: `link_paddler`, `unlink_paddler`, `paddlers_for`, `list_paddlers`.
- Produces: route `sda.session_paddlers` — `POST /api/a/<activity_id>/paddlers`.

- [ ] **Step 1: Write the failing tests**

```python
def test_linked_paddler_can_add_another(signed_in, store, saved_activity): ...
def test_linked_paddler_can_remove_another(signed_in, store, saved_activity): ...
def test_unlinked_paddler_cannot_add_on_someone_elses_session(...):  # 404
def test_cannot_remove_the_last_paddler(...):  # 400 - would orphan the session
def test_cannot_link_the_same_paddler_twice(...):  # idempotent, still 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_visibility.py -q`
Expected: FAIL — 404 from the endpoint.

- [ ] **Step 3: Implement the endpoint**

The route requires `login_required`, resolves the activity with `get_for` (404 if
unlinked), and accepts `{"add": "<user_id>"}` or `{"remove": "<user_id>"}`.
Removing the last paddler returns 400 — an unowned session would be invisible to
everyone, which is indistinguishable from losing it.

- [ ] **Step 4: Add the UI**

`activity.html` gets a paddler list from `paddlers_for`, plus a `<select>` of
`list_paddlers()` and add/remove buttons carrying `data-session-paddlers-url` built
with `url_for`. `report.js` posts JSON and reloads on success, following the
existing `data-delete-url` pattern rather than introducing a new one.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_visibility.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes.py app/templates/activity.html app/static/js/report.js tests/test_visibility.py
git commit -m "feat: share a session with other paddlers"
```

---

### Task 6: The admin paddler screen

**Files:**
- Create: `app/templates/admin_paddlers.html`
- Modify: `app/routes.py`
- Test: `tests/test_admin.py`

**Interfaces:**
- Consumes: `Store.create_paddler`, `list_paddlers`, `update_paddler`, `set_password`, `DuplicateEmail`.
- Produces: routes `sda.admin_paddlers` (`GET /admin/paddlers`),
  `sda.admin_paddler_new` (`POST /admin/paddlers/new`),
  `sda.admin_paddler_update` (`POST /admin/paddlers/<user_id>/update`),
  `sda.admin_paddler_password` (`POST /admin/paddlers/<user_id>/password`).

- [ ] **Step 1: Write the failing tests**

```python
def test_admin_lists_paddlers(admin_client, store): ...
def test_non_admin_gets_403(...): ...
def test_admin_creates_a_paddler(admin_client, store): ...
def test_admin_cannot_create_a_duplicate_email(...):  # re-renders with an error, 200
def test_admin_changes_a_password(admin_client, store): ...
def test_password_change_evicts_that_paddlers_session(client, admin_client, store): ...
def test_admin_cannot_demote_the_last_admin(admin_client, store):  # 400
def test_admin_cannot_deactivate_the_last_admin(admin_client, store):  # 400
def test_admin_can_deactivate_a_second_paddler(admin_client, store): ...
```

The last two admin-protection tests are Review Focus item 3: with one admin, a
self-demotion or self-deactivation locks everyone out of the UI with no recovery.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_admin.py -q`
Expected: FAIL — 404 from `/admin/paddlers`.

- [ ] **Step 3: Implement the routes**

All four carry `@admin_required`. Password changes go through `set_password`, so
the epoch bump is inherited rather than reimplemented. The user id is validated
with `get_paddler` before use. Guard both lockout paths by counting remaining
active admins (`Store.count_admins(active_only=True)`) and refusing the change
that would reach zero.

- [ ] **Step 4: Build the screen**

`admin_paddlers.html` extends `base.html` and renders a row per paddler: name,
email, admin/active badges, an inline change-password form, and toggle buttons.
Table markup and the existing panel classes from `index.html` are reused. No new
JavaScript — these are plain POST forms so they work without the report page's
JS being loaded.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_admin.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes.py app/templates/admin_paddlers.html app/storage.py tests/test_admin.py
git commit -m "feat: admin screen to create paddlers and change passwords"
```

---

### Task 7: CLI for creating a paddler on a live host

**Files:**
- Create: `app/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Store.create_paddler`, `DuplicateEmail`.
- Produces: `python -m app.cli create-paddler --email … --name … --password … [--admin]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_creates_a_paddler(store, capsys): ...
def test_cli_refuses_a_duplicate_email(store): ...  # non-zero exit
def test_cli_requires_a_password(store): ...  # non-zero exit, nothing created
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.cli'`.

- [ ] **Step 3: Implement it**

`argparse` with a `create-paddler` subcommand building a `Store` from
`SDA_DATA_DIR`, exiting non-zero on `DuplicateEmail` and printing a plain
confirmation on success. The password is read from `--password`; it is never
echoed back.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/cli.py tests/test_cli.py
git commit -m "feat: create-paddler CLI"
```

---

### Task 8: Deploy, create Richard, backfill, and verify

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: no new code — this task proves the feature on the real install.

- [ ] **Step 1: Back up the live database**

```bash
ssh -i ~/.ssh/id_ed25519_pve02 root@pve02.funston.com \
  "pct exec 101 -- cp -a /var/lib/sport-data-analyzer/sport.db \
     /var/lib/sport-data-analyzer/sport.db.pre-auth"
```

- [ ] **Step 2: Deploy and check the LAN path first**

Push with `tools/w01.sh`, restart `sport-data-analyzer`, then confirm
`http://192.168.10.45/sport/` redirects to `/sport/login` and that the existing
activity count is unchanged.

Run: `curl -sS -o /dev/null -w '%{http_code}' http://192.168.10.45/sport/healthz`
Expected: `200`.

- [ ] **Step 3: Create Richard and backfill**

```bash
ssh -i ~/.ssh/id_ed25519_pve02 root@pve02.funston.com \
  "pct exec 101 -- /opt/sport-data-analyzer/.venv/bin/python -m app.cli create-paddler \
     --email rpf@funston.com --name 'Richard' --password '…' --admin"
```

Sign in on the LAN, confirm **every pre-existing session is listed**, and open
one to check its report still renders.

- [ ] **Step 4: Verify the public hostname**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' --resolve paddle.funston.com:443:115.64.25.93 \
  -u "$NPM_ACCESS_USER:$NPM_ACCESS_PASS" https://paddle.funston.com/
```

Expected: `302` to `/login` — not the session list. Then sign in through the
public URL and upload a real `.fit` from the fixture set.

- [ ] **Step 5: Verify the anti-goals by hand**

- Signed in as a second paddler, request a session id belonging to Richard →
  **404**.
- Change Richard's password from the admin screen while signed in on a second
  browser → the first browser is signed out on its next click.

- [ ] **Step 6: Update the README and commit**

Document the login flow, the admin screen, the CLI, and the `SDA_SECRET_KEY` /
`SDA_INITIAL_PADDLER_PASSWORD` environment variables.

```bash
git add README.md
git commit -m "docs: paddler accounts"
```
