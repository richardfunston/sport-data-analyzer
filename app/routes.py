"""HTTP surface: upload, activity index, and the per-activity report page."""

from __future__ import annotations

from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)

from .analysis import REPORT_VERSION, build_report
from .basemaps import BASEMAPS
from .fitio import FitError, parse_fit

bp = Blueprint("sda", __name__)


def _store():
    return current_app.extensions["store"]


@bp.get("/")
def index():
    store = _store()
    return render_template(
        "index.html",
        activities=store.list(60),
        count=store.count(),
    )


@bp.post("/api/upload")
def upload():
    """Accept one or more .fit files and return a report link for each."""
    files = request.files.getlist("files") or list(request.files.values())
    if not files:
        return jsonify({"error": "no files were uploaded"}), 400

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

        record, created = _store().save(name, raw, report)
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
def activity(activity_id: str):
    store = _store()
    record = store.get(activity_id)
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
def download(activity_id: str):
    store = _store()
    record = store.get(activity_id)
    if record is None:
        abort(404)
    return send_file(
        store.raw_path(activity_id),
        as_attachment=True,
        download_name=record["filename"],
        mimetype="application/octet-stream",
    )


@bp.post("/api/a/<activity_id>/delete")
def delete(activity_id: str):
    if not _store().delete(activity_id):
        abort(404)
    return jsonify({"ok": True})


@bp.get("/healthz")
def healthz():
    return jsonify({"ok": True, "activities": _store().count()})


@bp.app_errorhandler(413)
def too_large(_error):
    limit_mb = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return jsonify({"error": f"upload exceeds the {limit_mb} MB limit"}), 413
