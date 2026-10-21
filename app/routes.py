"""HTTP surface: upload, activity index, and the per-activity report page."""

from __future__ import annotations

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
from .auth import current_paddler, login_paddler, login_required, logout_paddler
from .basemaps import BASEMAPS
from .fitio import FitError, parse_fit

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
        "activity.html", record=record, report=report, basemaps=BASEMAPS
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
