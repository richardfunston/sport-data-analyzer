"""Tests for the HTTP surface: upload, listing, and the report page."""

from __future__ import annotations

import io
import json
import re

from app.analysis import ROLLING_WINDOWS
from app.fitio import parse_fit


def upload(client, payload: bytes, filename: str = "canoe-session.fit"):
    return client.post(
        "/api/upload",
        data={"files": (io.BytesIO(payload), filename)},
        content_type="multipart/form-data",
    )


def test_index_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Upload a session" in response.data


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "activities": 0}


def test_upload_returns_a_report_link(client, canoe_fit):
    response = upload(client, canoe_fit)
    assert response.status_code == 200

    body = response.get_json()
    assert len(body["results"]) == 1

    result = body["results"][0]
    assert result["ok"] is True, result
    assert result["created"] is True
    assert result["meta"]["sport"] == "canoeing"
    assert result["summary"]["distance_m"] > 4000
    assert result["url"].endswith("/a/" + result["id"])


def test_uploaded_activity_appears_in_the_index(client, canoe_fit):
    upload(client, canoe_fit)

    response = client.get("/")
    assert response.status_code == 200
    assert b"canoe-session.fit" not in response.data  # only the date is listed
    assert b"Canoeing" in response.data

    assert client.get("/healthz").get_json()["activities"] == 1


def test_report_page_embeds_the_payload(client, canoe_fit):
    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]

    response = client.get(f"/a/{activity_id}")
    assert response.status_code == 200
    html = response.data.decode()

    match = re.search(
        r'<script id="report-data" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert match, "report payload is not embedded"

    payload = json.loads(match.group(1))
    assert payload["meta"]["sport"] == "canoeing"
    assert payload["is_paddling"] is True
    assert payload["summary"]["distance_m"] > 4000
    assert len(payload["histogram"]["bins"]) == 24
    assert payload["trace"]["t"]
    assert len(payload["trace"]["t"]) == len(payload["trace"]["speed"])
    assert payload["trace"]["rolling_options"] == list(ROLLING_WINDOWS)
    assert set(payload["trace"]["rolling_windows"]) == {str(w) for w in ROLLING_WINDOWS}
    assert payload["splits"]
    assert len(payload["track"]) > 10


def test_report_page_renders_the_rolling_window_picker(client, canoe_fit):
    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]
    html = client.get(f"/a/{activity_id}").data.decode()

    for window in ROLLING_WINDOWS:
        assert f'id="window-{window}"' in html, window
        assert f'data-window="{window}"' in html, window

    assert ">10 min</button>" in html
    assert ">30 s</button>" in html
    # Exactly one option starts selected.
    assert html.count('class="is-active"') == 1
    assert html.count('aria-pressed="true"') == 1


def test_report_charts_are_downsampled_to_a_bounded_size(client, canoe_fit):
    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]

    html = client.get(f"/a/{activity_id}").data.decode()
    match = re.search(
        r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S
    )
    report = json.loads(match.group(1))

    assert report["points_total"] == 1560
    assert report["points_charted"] <= 1200
    assert report["points_charted"] == len(report["trace"]["t"])


def test_reuploading_the_same_file_is_deduplicated(client, canoe_fit):
    first = upload(client, canoe_fit).get_json()["results"][0]
    second = upload(client, canoe_fit).get_json()["results"][0]

    assert second["id"] == first["id"]
    assert second["created"] is False
    assert client.get("/healthz").get_json()["activities"] == 1


def test_uploading_multiple_files_in_one_request(client, canoe_fit):
    response = client.post(
        "/api/upload",
        data={
            "files": [
                (io.BytesIO(canoe_fit), "one.fit"),
                (io.BytesIO(canoe_fit), "two.fit"),
            ]
        },
        content_type="multipart/form-data",
    )
    results = response.get_json()["results"]
    assert len(results) == 2
    assert all(r["ok"] for r in results)
    # Same bytes twice -> one stored activity.
    assert results[0]["id"] == results[1]["id"]
    assert client.get("/healthz").get_json()["activities"] == 1


def test_non_fit_upload_is_rejected(client):
    response = client.post(
        "/api/upload",
        data={"files": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    result = response.get_json()["results"][0]
    assert result["ok"] is False
    assert "not a .fit" in result["error"]


def test_corrupt_fit_upload_is_rejected(client):
    response = upload(client, b"\x0e\x10\x00\x00garbage" * 20, "broken.fit")
    assert response.status_code == 400
    assert response.get_json()["results"][0]["ok"] is False


def test_upload_without_files_is_a_bad_request(client):
    response = client.post("/api/upload", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_download_returns_the_original_bytes(client, canoe_fit):
    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]

    response = client.get(f"/a/{activity_id}/download")
    assert response.status_code == 200
    assert response.data == canoe_fit


def test_delete_removes_the_activity(client, canoe_fit):
    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]

    assert client.post(f"/api/a/{activity_id}/delete").status_code == 200
    assert client.get(f"/a/{activity_id}").status_code == 404
    assert client.get("/healthz").get_json()["activities"] == 0


def test_stale_report_is_rebuilt_from_the_original_file(app, client, canoe_fit):
    """Improving the analysis must upgrade reports already on disk."""
    from app.analysis import REPORT_VERSION

    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]
    store = app.extensions["store"]

    stale = store.report(activity_id)
    stale["version"] = REPORT_VERSION - 1
    stale["summary"]["distance_m"] = 1.0  # an obviously wrong value
    store.save_report(activity_id, stale)
    assert store.report(activity_id)["summary"]["distance_m"] == 1.0

    html = client.get(f"/a/{activity_id}").data.decode()
    match = re.search(
        r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S
    )
    payload = json.loads(match.group(1))

    assert payload["version"] == REPORT_VERSION
    assert payload["summary"]["distance_m"] > 4000
    # The rebuild is persisted, not recomputed on every request.
    assert store.report(activity_id)["version"] == REPORT_VERSION
    assert store.get(activity_id)["distance_m"] > 4000


def test_report_stays_clean_when_the_stored_file_is_missing(app, client, canoe_fit):
    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]
    app.extensions["store"].raw_path(activity_id).unlink()
    app.extensions["store"].report_path(activity_id).unlink()

    assert client.get(f"/a/{activity_id}").status_code == 404


def test_unknown_activity_is_not_found(client):
    assert client.get("/a/deadbeef").status_code == 404
    assert client.post("/api/a/deadbeef/delete").status_code == 404


def test_report_page_carries_the_path_prefix_when_proxied(client, canoe_fit):
    """Caddy strips /sport and forwards it in X-Forwarded-Prefix."""
    activity_id = upload(client, canoe_fit).get_json()["results"][0]["id"]

    response = client.get(
        f"/a/{activity_id}", headers={"X-Forwarded-Prefix": "/sport"}
    )
    html = response.data.decode()

    assert 'href="/sport/a/' in html
    assert 'data-delete-url="/sport/api/a/' in html


def test_static_assets_are_served(client):
    assert client.get("/static/css/style.css").status_code == 200
    assert client.get("/static/js/report.js").status_code == 200
    assert client.get("/static/js/upload.js").status_code == 200
    assert client.get("/static/vendor/chart.umd.min.js").status_code == 200


def test_upload_summary_matches_a_direct_parse(client, canoe_fit):
    """The upload response and a fresh parse must agree."""
    result = upload(client, canoe_fit).get_json()["results"][0]
    direct = parse_fit(canoe_fit)

    assert result["meta"]["sample_count"] == direct["meta"]["sample_count"]
    assert result["meta"]["sport"] == direct["meta"]["sport"]
    assert result["summary"]["distance_m"] > 0
    assert result["summary"]["max_speed_ms"] == max(
        v for v in direct["series"]["speed"] if v is not None
    )
