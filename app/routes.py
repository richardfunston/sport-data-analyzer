"""HTTP surface: upload, activity index, and the per-activity report page."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .analysis import REPORT_VERSION, build_report
from .auth import (
    LOGIN_MAX_FAILURES,
    LOGIN_WINDOW_SECONDS,
    admin_required,
    current_member,
    login_member,
    login_required,
    logout_member,
    usable_email,
)
from .basemaps import BASEMAPS
from .fitio import FitError, parse_fit
from .storage import DuplicateEmail, LastAdminError

bp = Blueprint("sda", __name__)


def _store():
    return current_app.extensions["store"]


#: Methods that change something. A cross-site request using one of these is
#: precisely what CSRF is.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _same_site(source: str | None) -> bool:
    """True when ``source`` is a URL on this very host, or absent.

    An absent header is allowed on purpose. A browser cannot suppress ``Origin``
    on a cross-site POST - it is a forbidden header name - so no header means the
    caller is not a browser making a cross-site request: curl, a script, an
    uptime probe. Refusing those would break every non-browser client to defend
    against an attack that cannot take that shape.
    """
    if not source:
        return True
    if source == "null":
        # A sandboxed iframe or a file:// page. Not this site.
        return False
    origin = urlsplit(source)
    if not origin.netloc:
        return False
    return origin.netloc.lower() == request.host.lower()


@bp.before_request
def _refuse_cross_site_writes():
    """Refuse a write whose Origin/Referer names another site.

    ``SameSite=Lax`` already stops the ordinary cross-site form post, so this is
    the second gate: it also covers same-site hosts under a shared parent domain
    and clients that ignore SameSite. It costs a header comparison rather than a
    token library and a template change on every form.
    """
    if request.method in UNSAFE_METHODS:
        source = request.headers.get("Origin") or request.headers.get("Referer")
        if not _same_site(source):
            abort(403, description="Cross-site request refused.")
    return None


def _safe_next(candidate: str | None) -> str:
    """The post-sign-in destination, or the index when the value is not ours.

    ``next`` is attacker-controlled. Checking only that it starts with ``/`` is
    not enough: ``//evil.example/`` starts with a slash and a browser reads it as
    protocol-relative, so the login page becomes an open redirector on a host
    people are being trained to trust. Both ``//`` and ``/\\`` are refused.
    """
    if not candidate or not candidate.startswith("/"):
        return url_for("sda.index")
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return url_for("sda.index")
    return candidate


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_target = request.form.get("next") or request.args.get("next") or ""
    error = None
    email = ""

    if request.method == "POST":
        email = request.form.get("email", "")
        store = _store()
        address = request.remote_addr or "unknown"

        if (
            store.count_recent_login_failures(email, address, LOGIN_WINDOW_SECONDS)
            >= LOGIN_MAX_FAILURES
        ):
            return (
                render_template(
                    "login.html",
                    error="Too many failed attempts. Try again in a few minutes.",
                    email=email,
                    next=next_target,
                ),
                429,
            )

        user = store.verify_member(email, request.form.get("password", ""))
        if user is None:
            store.record_login_failure(email, address)
            # Logged so a run of failures is visible rather than silent. The
            # submitted password is never logged.
            current_app.logger.warning(
                "failed sign-in for %s from %s", email, address
            )
            # Deliberately one message for an unknown account and for a wrong
            # password: distinguishing them tells anyone which addresses exist.
            error = "That email and password combination is incorrect."
        else:
            store.clear_login_failures(email, address)
            login_member(user)
            return redirect(_safe_next(next_target))

    return render_template("login.html", error=error, email=email, next=next_target)


@bp.post("/logout")
def logout():
    """Sign out. Harmless when already signed out."""
    logout_member()
    return redirect(url_for("sda.login"))


@bp.get("/")
@login_required
def index():
    store = _store()
    member = current_member()
    return render_template(
        "index.html",
        activities=store.list_for(member["id"], 60),
        # Scoped, not global: a deployment-wide number above a filtered list
        # reads as a bug to the person looking at it.
        count=store.count_for(member["id"]),
    )


@bp.post("/api/upload")
@login_required
def upload():
    """Accept one or more .fit files and return a report link for each."""
    files = request.files.getlist("files") or list(request.files.values())
    if not files:
        return jsonify({"error": "no files were uploaded"}), 400

    uploader_id = current_member()["id"]
    results: list[dict[str, Any]] = []
    for upload_file in files:
        name = upload_file.filename or "upload.fit"
        if not name.lower().endswith(".fit"):
            results.append(
                {"filename": name, "ok": False, "error": "not a .fit file"}
            )
            continue

        raw = upload_file.read()
        if not raw:
            results.append({"filename": name, "ok": False, "error": "file was empty"})
            continue

        try:
            report = build_report(parse_fit(raw))
        except FitError as exc:
            results.append({"filename": name, "ok": False, "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - surface anything to the client
            current_app.logger.exception("failed to analyse %s", name)
            results.append(
                {"filename": name, "ok": False, "error": f"could not analyse: {exc}"}
            )
            continue

        record, created = _store().save(name, raw, report, user_id=uploader_id)
        results.append(
            {
                "filename": name,
                "ok": True,
                "created": created,
                "id": record["id"],
                "url": url_for("sda.activity", activity_id=record["id"]),
                "meta": report["meta"],
                "summary": report["summary"],
            }
        )

    return jsonify({"results": results}), 200 if any(r["ok"] for r in results) else 400


def _rebuild_report(store, activity_id: str) -> dict[str, Any] | None:
    """Recompute a stored report from the original upload."""
    path = store.raw_path(activity_id)
    if not path.exists():
        return None
    try:
        report = build_report(parse_fit(path.read_bytes()))
    except FitError:
        current_app.logger.exception("could not rebuild the report for %s", activity_id)
        return None
    store.save_report(activity_id, report)
    return report


@bp.get("/a/<activity_id>")
@login_required
def activity(activity_id: str):
    store = _store()
    # get_for, not get: an unlinked member gets a 404 and cannot confirm the id
    # exists. A 403 would tell them it does.
    record = store.get_for(activity_id, current_member()["id"])
    if record is None:
        abort(404, description="no such activity")

    report = store.report(activity_id)
    # A report written by an older version of the analysis is rebuilt from the
    # stored .fit file, so improving the statistics upgrades past uploads.
    if report is None or report.get("version") != REPORT_VERSION:
        report = _rebuild_report(store, activity_id)
    if report is None:
        abort(404, description="no such activity")

    return render_template(
        "activity.html",
        record=record,
        report=report,
        basemaps=BASEMAPS,
        members=store.members_for(activity_id),
        shareable=_member_choices(store, activity_id),
        me=current_member(),
    )


@bp.get("/a/<activity_id>/download")
@login_required
def download(activity_id: str):
    store = _store()
    record = store.get_for(activity_id, current_member()["id"])
    if record is None:
        abort(404)
    return send_file(
        store.raw_path(activity_id),
        as_attachment=True,
        download_name=record["filename"],
        mimetype="application/octet-stream",
    )


@bp.post("/api/a/<activity_id>/delete")
@login_required
def delete(activity_id: str):
    store = _store()
    if store.get_for(activity_id, current_member()["id"]) is None:
        abort(404)
    if not store.delete(activity_id):
        abort(404)
    return jsonify({"ok": True})


def _member_choices(store, activity_id: str) -> list[dict[str, Any]]:
    """Active members not already on this session."""
    linked = {p["id"] for p in store.members_for(activity_id)}
    return [
        p for p in store.list_members() if p["is_active"] and p["id"] not in linked
    ]


@bp.post("/api/a/<activity_id>/members")
@login_required
def session_members(activity_id: str):
    """Share a session, or stop sharing it.

    Any member already linked may do this: a shared session behaves like a
    shared document, and the group is a household or club. Someone not linked
    gets a 404 - they cannot tell the session exists, let alone edit it.
    """
    store = _store()
    if store.get_for(activity_id, current_member()["id"]) is None:
        abort(404)

    payload = request.get_json(silent=True) or {}
    add = payload.get("add")
    remove = payload.get("remove")

    if add:
        if store.get_member(add) is None:
            return jsonify({"error": "no such member"}), 400
        store.link_member(activity_id, add)
    elif remove:
        if len(store.members_for(activity_id)) <= 1:
            # Refused rather than allowed: a session with no member is
            # invisible to everyone, which is indistinguishable from lost.
            return jsonify({"error": "a session must keep at least one member"}), 400
        store.unlink_member(activity_id, remove)
    else:
        return jsonify({"error": "expected 'add' or 'remove'"}), 400

    return jsonify(
        {
            "ok": True,
            "members": [
                {"id": p["id"], "name": p["name"], "email": p["email"]}
                for p in store.members_for(activity_id)
            ],
        }
    )


# -- the admin screen --------------------------------------------------------


def _render_account(store, me, *, error=None, notice=None, status=200):
    return (
        render_template(
            "account.html",
            me=me,
            error=error,
            notice=notice,
            # Shown on the page so "your sessions stay linked" is visible rather
            # than merely claimed.
            session_count=store.count_for(me["id"]),
        ),
        status,
    )


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """The member's own account: change the address you sign in with.

    Deliberately the only self-service edit. Email is the sign-in identity, and
    changing it is an UPDATE on the existing row, so the member's id - which every
    session link is keyed on - never moves. Nothing gets re-linked and no session
    is evicted, which is what "the links and the system stay" means in practice.
    """
    store = _store()
    me = current_member()

    if request.method == "GET":
        return _render_account(store, me, notice=request.args.get("notice"))

    email = (request.form.get("email") or "").strip()
    if not _usable_email(email):
        return _render_account(
            store, me, error="That does not look like an email address.", status=400
        )

    try:
        updated = store.change_email(me["id"], email)
    except DuplicateEmail:
        return _render_account(
            store,
            me,
            error="There is already a member with that email address.",
            status=400,
        )

    if updated is None:
        # The row vanished between the session check and the write. Nothing was
        # changed, so this is a genuinely unknown member rather than an error.
        abort(404)

    if updated["email"] == me["email"]:
        notice = f"{updated['email']} is already your sign-in address."
    else:
        notice = f"Your sign-in address is now {updated['email']}."
    return redirect(url_for("sda.account", notice=notice))


def _render_admin(store, *, error=None, notice=None, form=None, status=200):
    return (
        render_template(
            "admin_members.html",
            members=store.list_members(),
            me=current_member(),
            error=error,
            notice=notice,
            form=form or {},
        ),
        status,
    )


def _usable_email(candidate: str) -> bool:
    """Kept as a thin alias so the route reads naturally; see auth.usable_email."""
    return usable_email(candidate)


@bp.get("/admin/members")
@admin_required
def admin_members():
    return _render_admin(
        _store(),
        error=request.args.get("error"),
        notice=request.args.get("notice"),
    )


@bp.post("/admin/members/new")
@admin_required
def admin_member_new():
    store = _store()
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    form = {"name": name, "email": email}

    if not name:
        return _render_admin(store, error="A name is required.", form=form)
    if not _usable_email(email):
        return _render_admin(
            store, error="That does not look like an email address.", form=form
        )
    if not password:
        return _render_admin(store, error="A password is required.", form=form)

    try:
        store.create_member(
            email,
            name,
            password,
            is_admin=request.form.get("is_admin") == "1",
        )
    except DuplicateEmail:
        return _render_admin(
            store,
            error="There is already a member with that email address.",
            form=form,
        )

    return redirect(url_for("sda.admin_members", notice=f"{name} can sign in now."))


@bp.post("/admin/members/<user_id>/password")
@admin_required
def admin_member_password(user_id: str):
    store = _store()
    target = store.get_member(user_id)
    if target is None:
        abort(404)

    password = request.form.get("password") or ""
    if not password:
        return _render_admin(store, error="A password is required.")

    # set_password bumps session_epoch, so this evicts every session already
    # open for that member rather than leaving a stale cookie valid.
    store.set_password(user_id, password)
    return redirect(
        url_for("sda.admin_members", notice=f"Password changed for {target['name']}.")
    )


@bp.post("/admin/members/<user_id>/update")
@admin_required
def admin_member_update(user_id: str):
    store = _store()
    target = store.get_member(user_id)
    if target is None:
        abort(404)

    name = request.form.get("name")
    email = request.form.get("email")
    wants_admin = request.form.get("is_admin")
    wants_active = request.form.get("is_active")

    # Refuse the change that would leave nobody able to reach this screen.
    # There is no way back in through the UI once it happens. The count and the
    # write happen in one transaction inside the store, because counting here and
    # updating afterwards is the check-then-act race two workers can lose.
    fields: dict[str, Any] = {}
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            abort(400, description="A name is required.")
        fields["name"] = cleaned
    if email is not None:
        # The admin's route into a mistyped address: without it, a member who
        # typos their own email and then gets signed out has no way back in.
        cleaned = email.strip()
        if not _usable_email(cleaned):
            return _render_admin(
                store, error="That does not look like an email address.", status=400
            )
        fields["email"] = cleaned
    if wants_admin is not None:
        fields["is_admin"] = wants_admin == "1"
    if wants_active is not None:
        fields["is_active"] = wants_active == "1"

    if fields:
        try:
            store.update_member_safely(user_id, **fields)
        except LastAdminError:
            abort(
                400,
                description=(
                    "That would leave no active admin, and there would be no way "
                    "back into this screen."
                ),
            )
        except DuplicateEmail:
            return _render_admin(
                store,
                error="There is already a member with that email address.",
                status=400,
            )

    notice = f"{target['name']} updated."

    if fields.get("is_active") is False:
        # Deactivating the only member on a session reaches the same dead end
        # that removing the last member is refused for: the sessions go
        # invisible to every account, and the link row survives so the startup
        # backfill would not rescue them either. Re-home them instead.
        moved = store.backfill_members(current_member()["id"])
        if moved:
            plural = "" if moved == 1 else "s"
            notice = (
                f"{target['name']} deactivated. {moved} session{plural} reassigned "
                "to you, because nobody active was left on them."
            )

    return redirect(url_for("sda.admin_members", notice=notice))


@bp.get("/healthz")
def healthz():
    """Public, so it must not disclose anything about the deployment.

    It used to return the total activity count, which told any anonymous caller
    how much is here.
    """
    return jsonify({"ok": True})


@bp.app_errorhandler(413)
def too_large(_error):
    limit_mb = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": f"upload exceeds the {limit_mb} MB limit"}), 413
