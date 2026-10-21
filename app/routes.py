"""HTTP surface: upload, activity index, and the per-activity report page."""

from __future__ import annotations

import re
from typing import Any

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
    admin_required,
    current_paddler,
    login_paddler,
    login_required,
    logout_paddler,
)
from .basemaps import BASEMAPS
from .fitio import FitError, parse_fit
from .storage import DuplicateEmail

bp = Blueprint("sda", __name__)


def _store():
    return current_app.extensions["store"]


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
        user = _store().verify_paddler(email, request.form.get("password", ""))
        if user is None:
            # Deliberately one message for an unknown account and for a wrong
            # password: distinguishing them tells anyone which addresses exist.
            error = "That email and password combination is incorrect."
        else:
            login_paddler(user)
            return redirect(_safe_next(next_target))

    return render_template("login.html", error=error, email=email, next=next_target)


@bp.post("/logout")
def logout():
    """Sign out. Harmless when already signed out."""
    logout_paddler()
    return redirect(url_for("sda.login"))


@bp.get("/")
@login_required
def index():
    store = _store()
    paddler = current_paddler()
    return render_template(
        "index.html",
        activities=store.list_for(paddler["id"], 60),
        # Scoped, not global: a deployment-wide number above a filtered list
        # reads as a bug to the person looking at it.
        count=store.count_for(paddler["id"]),
    )


@bp.post("/api/upload")
@login_required
def upload():
    """Accept one or more .fit files and return a report link for each."""
    files = request.files.getlist("files") or list(request.files.values())
    if not files:
        return jsonify({"error": "no files were uploaded"}), 400

    uploader_id = current_paddler()["id"]
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
    # get_for, not get: an unlinked paddler gets a 404 and cannot confirm the id
    # exists. A 403 would tell them it does.
    record = store.get_for(activity_id, current_paddler()["id"])
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
        paddlers=store.paddlers_for(activity_id),
        shareable=_paddler_choices(store, activity_id),
        me=current_paddler(),
    )


@bp.get("/a/<activity_id>/download")
@login_required
def download(activity_id: str):
    store = _store()
    record = store.get_for(activity_id, current_paddler()["id"])
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
    if store.get_for(activity_id, current_paddler()["id"]) is None:
        abort(404)
    if not store.delete(activity_id):
        abort(404)
    return jsonify({"ok": True})


def _paddler_choices(store, activity_id: str) -> list[dict[str, Any]]:
    """Active paddlers not already on this session."""
    linked = {p["id"] for p in store.paddlers_for(activity_id)}
    return [
        p for p in store.list_paddlers() if p["is_active"] and p["id"] not in linked
    ]


@bp.post("/api/a/<activity_id>/paddlers")
@login_required
def session_paddlers(activity_id: str):
    """Share a session, or stop sharing it.

    Any paddler already linked may do this: a shared session behaves like a
    shared document, and the group is a household or club. Someone not linked
    gets a 404 - they cannot tell the session exists, let alone edit it.
    """
    store = _store()
    if store.get_for(activity_id, current_paddler()["id"]) is None:
        abort(404)

    payload = request.get_json(silent=True) or {}
    add = payload.get("add")
    remove = payload.get("remove")

    if add:
        if store.get_paddler(add) is None:
            return jsonify({"error": "no such paddler"}), 400
        store.link_paddler(activity_id, add)
    elif remove:
        if len(store.paddlers_for(activity_id)) <= 1:
            # Refused rather than allowed: a session with no paddler is
            # invisible to everyone, which is indistinguishable from lost.
            return jsonify({"error": "a session must keep at least one paddler"}), 400
        store.unlink_paddler(activity_id, remove)
    else:
        return jsonify({"error": "expected 'add' or 'remove'"}), 400

    return jsonify(
        {
            "ok": True,
            "paddlers": [
                {"id": p["id"], "name": p["name"], "email": p["email"]}
                for p in store.paddlers_for(activity_id)
            ],
        }
    )


# -- the admin screen --------------------------------------------------------


def _render_admin(store, *, error=None, notice=None, form=None, status=200):
    return (
        render_template(
            "admin_paddlers.html",
            paddlers=store.list_paddlers(),
            me=current_paddler(),
            error=error,
            notice=notice,
            form=form or {},
        ),
        status,
    )


def _usable_email(candidate: str) -> bool:
    """Enough of a check that a typo cannot quietly become an account."""
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", candidate.strip()))


@bp.get("/admin/paddlers")
@admin_required
def admin_paddlers():
    return _render_admin(
        _store(),
        error=request.args.get("error"),
        notice=request.args.get("notice"),
    )


@bp.post("/admin/paddlers/new")
@admin_required
def admin_paddler_new():
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
        store.create_paddler(
            email,
            name,
            password,
            is_admin=request.form.get("is_admin") == "1",
        )
    except DuplicateEmail:
        return _render_admin(
            store,
            error="There is already a paddler with that email address.",
            form=form,
        )

    return redirect(url_for("sda.admin_paddlers", notice=f"{name} can sign in now."))


@bp.post("/admin/paddlers/<user_id>/password")
@admin_required
def admin_paddler_password(user_id: str):
    store = _store()
    target = store.get_paddler(user_id)
    if target is None:
        abort(404)

    password = request.form.get("password") or ""
    if not password:
        return _render_admin(store, error="A password is required.")

    # set_password bumps session_epoch, so this evicts every session already
    # open for that paddler rather than leaving a stale cookie valid.
    store.set_password(user_id, password)
    return redirect(
        url_for("sda.admin_paddlers", notice=f"Password changed for {target['name']}.")
    )


@bp.post("/admin/paddlers/<user_id>/update")
@admin_required
def admin_paddler_update(user_id: str):
    store = _store()
    target = store.get_paddler(user_id)
    if target is None:
        abort(404)

    name = request.form.get("name")
    wants_admin = request.form.get("is_admin")
    wants_active = request.form.get("is_active")

    # Refuse the change that would leave nobody able to reach this screen.
    # There is no way back in through the UI once it happens.
    demoting = wants_admin == "0"
    deactivating = wants_active == "0"
    if (demoting or deactivating) and target["is_admin"] and target["is_active"]:
        if store.count_admins(active_only=True) <= 1:
            abort(
                400,
                description=(
                    "That would leave no active admin, and there would be no way "
                    "back into this screen."
                ),
            )

    fields: dict[str, Any] = {}
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            abort(400, description="A name is required.")
        fields["name"] = cleaned
    if wants_admin is not None:
        fields["is_admin"] = wants_admin == "1"
    if wants_active is not None:
        fields["is_active"] = wants_active == "1"

    if fields:
        store.update_paddler(user_id, **fields)

    return redirect(url_for("sda.admin_paddlers", notice=f"{target['name']} updated."))


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
