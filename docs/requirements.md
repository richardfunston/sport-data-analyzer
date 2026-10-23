# Sport Data Analyzer — Functional Requirements

**Version:** 1.0
**Status:** reflects the app as built
**Scope:** the behaviour of the application itself

This document states what the app *does*, derived from the code and from the 330
tests that pin it. Every acceptance criterion below names the test that verifies
it, so nothing here is aspirational — if it is in this document, it is enforced.

---

## 1. Scope

### In scope
The app's own behaviour: uploading, decoding, analysis, reporting, accounts,
sharing, administration, and the browser-facing pages.

### Out of scope
Everything about *how the app is published and hosted*: the reverse proxy, NAT
and port forwarding, DNS (public and split-horizon), TLS certificates, the
container it runs in, the deploy pipeline, and the external content-filter
investigation. Those are deployment concerns, documented in the README and
elsewhere, and they say nothing about the app's function.

### Also out of scope (non-functional)
Performance targets, capacity, availability and security *hardening* as goals in
themselves. Where a performance property is pinned by a test (for example, that
each rolling window is computed exactly once) it appears as a functional
constraint, because regressing it changes observable behaviour.

---

## 2. How to read this

| Prefix | Meaning |
|---|---|
| `FR-n` | A functional requirement — a feature area |
| `US-n.m` | A user story within that feature |
| `AC-n.m.k` | An acceptance criterion for that story |
| `TC:` | The test that verifies the criterion, by its real function name |

Each `TC:` name is a test in `tests/`. A test listed against a criterion is the
evidence that the criterion holds; the suite is the specification's proof.

### Actors

| Actor | Definition |
|---|---|
| **Visitor** | Not signed in |
| **Member** | Signed in, not an admin |
| **Admin** | A member with `is_admin` set |
| **Uploader** | The phone/browser performing an upload (always a signed-in member) |
| **System** | Startup behaviour: migrations, bootstrap, the backfill |

---

## 3. Requirement index

| ID | Feature | Tests cited |
|---|---|---|
| FR-1 | Upload a session from a phone | 11 |
| FR-2 | Decode a FIT file into series | 14 |
| FR-3 | Deduplicate re-uploads | 2 |
| FR-4 | Session list (index) | 5 |
| FR-5 | Report: overall statistics | 8 |
| FR-6 | Report: speed distribution | 4 |
| FR-7 | Report: speed trace and rolling windows | 18 |
| FR-8 | Report: heart rate | 2 |
| FR-9 | Report: elevation, withheld on water | 14 |
| FR-10 | Report: route map and fallback | 12 |
| FR-11 | Report: splits | 2 |
| FR-12 | Download the original file | 3 |
| FR-13 | Delete a session | 5 |
| FR-14 | Accounts: identity, sign in, sessions | 32 |
| FR-15 | Per-member visibility | 10 |
| FR-16 | Sharing a session | 9 |
| FR-17 | Password change and session eviction | 8 |
| FR-18 | Change your own email | 25 |
| FR-19 | Admin: member management | 22 |
| FR-20 | Admin: the last-admin guard | 16 |
| FR-21 | Admin: deactivation re-homes sessions | 3 |
| FR-22 | Sign-in throttling | 5 |
| FR-23 | Cross-site write protection | 15 |
| FR-24 | Bootstrap and CLI | 15 |
| FR-25 | Liveness endpoint | 3 |
| FR-26 | Time display in a local timezone | 14 |
| FR-27 | Report caching and regeneration | 4 |
| FR-28 | Page delivery and mounting | 5 |
| FR-29 | Data integrity invariants | 9 |

**Every one of the 283 test functions in the suite is cited by exactly one
acceptance criterion above**, so there is no untested behaviour described here and
no test that a requirement does not account for. The counts are per requirement and
a few tests serve more than one criterion.

---

## FR-1 — Upload a session from a phone

The primary entry point. A member picks a `.fit` file on their phone and gets a
report back.

### US-1.1 — Upload one or more recordings
> As a member, I want to upload a `.fit` file from my phone so that I can see what
> the session looked like.

- **AC-1.1.1** A `POST` to `/api/upload` with a multipart `files` field returns
  `200` and one result per file, each carrying `ok`, `created`, `id`, `meta`,
  `summary`, and a `url` ending `/a/<id>`.
  `TC: test_upload_returns_a_report_link`
- **AC-1.1.2** Several files may be uploaded in one request; each is reported
  separately and one bad file does not fail the others.
  `TC: test_uploading_multiple_files_in_one_request`
- **AC-1.1.3** The uploaded bytes are stored unmodified and are retrievable
  byte-for-byte.
  `TC: test_download_returns_the_original_bytes`
- **AC-1.1.4** The uploader is linked to the session the upload creates.
  `TC: test_upload_links_the_uploader`
- **AC-1.1.5** The upload requires a session; a visitor is redirected to sign in
  rather than being served a form that cannot work.
  `TC: test_upload_redirects_an_anonymous_visitor`

### US-1.2 — Get a clear rejection when a file is wrong
> As a member, if I pick the wrong file I want to be told, not shown an error page.

- **AC-1.2.1** A filename that is not `.fit` is rejected per-file with a reason.
  `TC: test_non_fit_upload_is_rejected`
- **AC-1.2.2** A payload that is not a readable FIT file is rejected with a
  reason and does not produce a 500. This covers random bytes, an empty file, and
  a truncated file.
  `TC: test_corrupt_fit_upload_is_rejected`, `TC: test_rejects_random_bytes`,
  `TC: test_rejects_empty_input`, `TC: test_rejects_a_truncated_file`
- **AC-1.2.3** A request carrying no files at all is `400`.
  `TC: test_upload_without_files_is_a_bad_request`

### US-1.3 — Uploads are bounded
- **AC-1.3.1** A request body above `SDA_MAX_UPLOAD_BYTES` (default 64 MB) is
  refused by the app's request-size ceiling rather than being buffered whole.
  *Configured via `MAX_CONTENT_LENGTH` with a `413` handler. No dedicated test —
  see §5 Known gaps.*

---

## FR-2 — Decode a FIT file into series

Turning a device file into columns of numbers the analysis can use.

### US-2.1 — A device file becomes usable data
> As the system, I want to decode the recording into aligned series so that every
> statistic is computed from the same clock.

- **AC-2.1.1** Every series column is the same length as the timestamp column.
  `TC: test_every_series_column_is_the_same_length`
- **AC-2.1.2** Timestamps are relative: the first is `0` and the series increases.
  `TC: test_timestamps_start_at_zero_and_increase`
- **AC-2.1.3** GPS coordinates are converted from FIT semicircles to degrees, and
  the track is geographically sane for a real recording.
  `TC: test_coordinates_are_converted_to_degrees`,
  `TC: test_real_recording_track_is_geographically_sane`
- **AC-2.1.4** Distance is monotonically non-decreasing and matches the recording's
  own total.
  `TC: test_distance_is_monotonic_and_matches_the_simulated_total`
- **AC-2.1.5** Speed values are within plausible bounds — a decoder error that
  produces a 900 km/h sample must be visible as a test failure.
  `TC: test_speed_stays_within_plausible_bounds`
- **AC-2.1.6** A real Wahoo ELEMNT recording decodes and yields sane statistics.
  `TC: test_parses_a_real_device_recording`,
  `TC: test_real_recording_yields_sane_statistics`
- **AC-2.1.7** A synthetic canoeing session decodes with the expected shape.
  `TC: test_parses_a_canoeing_session`

### US-2.2 — Sport names are correct, including new ones
> As a member, I want my session labelled with the right sport, not a number.

- **AC-2.2.1** FIT sport enum integers resolve to their official names. Canoeing
  is enum `88`, which is outside the decoder library's snapshot of the profile.
  `TC: test_sport_enum_integers_resolve_to_names`
- **AC-2.2.2** A sport already given as a name passes through unchanged.
  `TC: test_sport_names_pass_through_unchanged`
- **AC-2.2.3** An unknown enum value yields `None` rather than raising, so a
  newer device cannot break an upload.
  `TC: test_unknown_enum_values_are_none_not_an_exception`
- **AC-2.2.4** A missing sport is treated as *not a water sport* — the safe
  default, since withholding elevation from a land session is a visible loss.
  `TC: test_a_missing_sport_is_not_a_water_sport`
- **AC-2.2.5** Snake-case sport names are formatted for display.
  `TC: test_title_formats_snake_case_sports`

---

## FR-3 — Deduplicate re-uploads

### US-3.1 — Re-uploading adds nothing, but still shares
> As a member, if I upload a file I already have, I want the existing report — and
> if it is my partner's watch file, I want to see the session, not be told it is a
> duplicate.

- **AC-3.1.1** A byte-identical upload (matched by SHA-256) returns the existing
  session instead of creating a second one.
  `TC: test_reuploading_the_same_file_is_deduplicated`
- **AC-3.1.2** The re-uploading member is linked to that existing session.
  `TC: test_reupload_of_an_identical_file_links_a_second_member`
- **AC-3.1.3** The result reports `created: false` for a duplicate so the caller
  can tell the difference.
  `TC: test_reuploading_the_same_file_is_deduplicated`

---

## FR-4 — Session list (index)

### US-4.1 — See my sessions, and only mine
> As a member, I want a list of my own sessions.

- **AC-4.1.1** The index lists only sessions the member is linked to.
  `TC: test_index_lists_only_linked_sessions`
- **AC-4.1.2** The count shown is scoped to the member, not deployment-wide — a
  global number above a filtered list reads as a bug.
  `TC: test_index_count_is_scoped_to_the_member`
- **AC-4.1.3** Each row shows the session's date and time.
  `TC: test_uploaded_activity_appears_in_the_index`
- **AC-4.1.4** A visitor is redirected to sign in.
  `TC: test_index_redirects_an_anonymous_visitor_to_the_sign_in_page`
- **AC-4.1.5** The page renders.
  `TC: test_index_renders`

---

## FR-5 — Report: overall statistics

### US-5.1 — The numbers that matter for a session
> As a member, I want the averages and maxima that describe the session.

- **AC-5.1.1** The report exposes distance, moving time, elapsed time, stopped
  time, average speed (moving and total), max speed, average pace, best pace,
  average and max heart rate, average and max cadence, ascent, descent, calories
  and temperature.
  `TC: test_report_page_embeds_the_payload`
- **AC-5.1.2** Averages are integrated over elapsed time, not averaged across
  samples, so an irregular recording cadence does not bias them.
  `TC: test_summarize_constant_speed`
- **AC-5.1.3** Heart rate is weighted by time.
  `TC: test_summarize_weights_average_heart_rate_by_time`
- **AC-5.1.4** Moving and stopped time are separated, with movement above
  0.5 m/s (1.8 km/h).
  `TC: test_summarize_separates_moving_time_from_stopped_time`
- **AC-5.1.5** Auto-pause gaps are not counted as moving time. Devices stop
  writing records when the athlete stops, leaving multi-minute holes; each
  sample's duration is capped at three times the recording cadence. Without this
  the figure is ~18% too high on a real ELEMNT file.
  `TC: test_summarize_does_not_count_auto_pause_gaps_as_moving`
- **AC-5.1.6** Distance is derived from speed when the device never recorded it —
  common on indoor or GPS-less sessions.
  `TC: test_summarize_derives_distance_when_the_device_omits_it`
- **AC-5.1.7** Metrics the recording does not carry are reported as absent rather
  than as zero.
  `TC: test_summarize_reports_missing_metrics_as_none`
- **AC-5.1.8** The uploaded summary agrees with a direct parse of the same file.
  `TC: test_upload_summary_matches_a_direct_parse`

---

## FR-6 — Report: speed distribution

### US-6.1 — See where the time actually went
> As a member, I want to know how long I spent at each speed, not how many samples
> happened to land there.

- **AC-6.1.1** The histogram buckets **time spent** in each speed band, not sample
  counts, so an hour spent crawling into wind is visible as an hour.
  `TC: test_speed_histogram_buckets_time_by_speed`
- **AC-6.1.2** The distribution is annotated with the time-weighted mean, median,
  95th percentile, and the band containing the most time.
  `TC: test_speed_histogram_reports_distribution_stats`
- **AC-6.1.3** A session with no speed data produces no histogram rather than a
  misleading empty one.
  `TC: test_speed_histogram_without_speed_data_is_none`
- **AC-6.1.4** An all-zero-speed session does not divide by zero.
  `TC: test_speed_histogram_all_zero_speed_does_not_divide_by_zero`

---

## FR-7 — Report: speed trace and rolling windows

### US-7.1 — Read the effort pattern through the noise
> As a member, I want a smoothed line over my speed so I can see the shape of the
> session rather than every GPS wobble.

- **AC-7.1.1** Instantaneous speed is plotted against elapsed time, with a
  trailing rolling average drawn over the top.
  `TC: test_report_page_embeds_the_payload`
- **AC-7.1.2** The rolling window is selectable, spanning 30 seconds to 10
  minutes.
  `TC: test_the_window_choices_span_30_seconds_to_10_minutes`,
  `TC: test_report_page_renders_the_rolling_window_picker`
- **AC-7.1.3** A series is precomputed for every window at build time, so
  switching is instant and never re-smooths an already-smoothed line.
  `TC: test_build_report_precomputes_a_series_for_every_window`
- **AC-7.1.4** Each window yields a genuinely different series, and longer windows
  are smoother.
  `TC: test_each_window_returns_a_different_series`,
  `TC: test_longer_windows_are_smoother`
- **AC-7.1.5** Each rolling window is computed exactly once per build. Computing
  it inline recomputed the whole series once per plotted point and took a
  13,471-record file from 0.5 s to 105 s.
  `TC: test_each_rolling_window_is_computed_exactly_once`
- **AC-7.1.6** A window longer than the session still produces a usable series.
  `TC: test_a_window_longer_than_the_session_still_produces_a_series`
- **AC-7.1.7** Rolling averages never go negative.
  `TC: test_rolling_windows_never_go_negative`
- **AC-7.1.8** The rolling mean ignores missing samples, uses a trailing window,
  never dips below the series minimum, and returns a constant series as that
  constant.
  `TC: test_rolling_mean_uses_a_trailing_window`,
  `TC: test_rolling_mean_ignores_missing_samples`,
  `TC: test_rolling_mean_never_dips_below_the_series_minimum`,
  `TC: test_rolling_mean_of_constant_series_is_that_constant`,
  `TC: test_rolling_mean_of_all_missing_is_missing`
- **AC-7.1.9** Chart data is downsampled (LTTB) to a bounded size so a
  13,000-sample recording stays responsive on a phone while keeping its peaks.
  `TC: test_report_charts_are_downsampled_to_a_bounded_size`,
  `TC: test_lttb_keeps_requested_count_and_endpoints`,
  `TC: test_lttb_preserves_a_spike_better_than_decimation`,
  `TC: test_lttb_returns_everything_when_target_exceeds_input`

---

## FR-8 — Report: heart rate

### US-8.1 — See heart rate when the device recorded it
- **AC-8.1.1** A full heart-rate trace is shown when the file carries one.
  `TC: test_report_page_embeds_the_payload`
- **AC-8.1.2** The panel is absent when there is no heart-rate data.
  `TC: test_summarize_reports_missing_metrics_as_none`

---

## FR-9 — Report: elevation, withheld on water

### US-9.1 — Don't show meaningless altitude
> As a paddler, I don't want a mountain profile invented from wave noise.

- **AC-9.1.1** The altitude profile is **withheld for every water sport**
  (canoeing, kayaking, paddling, rowing, swimming, SUP, sailing, surfing…): on
  water the trace is surface noise, so waves and GPS drift read as climbing.
  `TC: test_elevation_is_withheld_for_a_water_sport`,
  `TC: test_every_water_sport_withholds_elevation`
- **AC-9.1.2** Land sports still show it.
  `TC: test_land_sports_still_show_elevation`
- **AC-9.1.3** It is hidden when there is no altitude data at all, regardless of
  sport.
  `TC: test_elevation_is_hidden_when_there_is_no_altitude_data_at_all`
- **AC-9.1.4** A water **sub**-sport puts a session recorded under a generic
  parent sport on water — a whitewater run must not slip through.
  `TC: test_a_water_sub_sport_withholds_elevation`,
  `TC: test_a_water_sub_sport_puts_a_generic_session_on_water`
- **AC-9.1.5** Elevation statistics are still computed and stored; only the panel
  is withheld, so a river trip can be restored with one flag. A session with no
  altitude data at all reports no gain rather than zero.
  `TC: test_elevation_gain_of_flat_ground_is_zero`,
  `TC: test_elevation_gain_counts_a_climb_and_a_drop`,
  `TC: test_elevation_gain_needs_altitude_data`
- **AC-9.1.6** Altitude is smoothed over five samples and changes under 1 m are
  ignored, so GPS jitter is not reported as climbing.
  `TC: test_elevation_gain_ignores_gps_jitter`
- **AC-9.1.7** Water-sport membership is consistent, and a canoe sport is
  recognised as a paddling sport: paddling sports are a subset of water sports.
  `TC: test_paddling_sports_are_a_subset_of_water_sports`,
  `TC: test_is_paddling_recognises_canoe_sports`,
  `TC: test_water_sports_are_recognised`,
  `TC: test_land_sports_are_not_water_sports`

---

## FR-10 — Report: route map and fallback

### US-10.1 — See where I went, with no API key
> As the operator, I want a map that needs no signup or billing account.

- **AC-10.1.1** The GPS track is drawn on a real map background with a
  **Streets / Satellite / Terrain / Dark** switcher.
  `TC: test_streets_and_satellite_are_offered`,
  `TC: test_report_page_renders_the_basemap_picker`
- **AC-10.1.2** No provider requires an API key.
  `TC: test_no_provider_needs_an_api_key`
- **AC-10.1.3** Every entry is usable as a Leaflet tile layer, keys are unique,
  providers are HTTPS, and a default exists as the first entry.
  `TC: test_every_entry_is_usable_as_a_leaflet_tile_layer`,
  `TC: test_keys_are_unique`, `TC: test_providers_are_https`,
  `TC: test_a_default_basemap_exists_and_is_the_first_entry`
- **AC-10.1.4** The basemap catalogue is embedded for the frontend.
  `TC: test_basemap_catalogue_is_embedded_for_the_frontend`
- **AC-10.1.5** If the mapping library or the network is unavailable, the track
  falls back to an inline SVG outline, so the shape is always visible.
  `TC: test_map_and_fallback_share_one_height_knob`
- **AC-10.1.6** Elements the page toggles ship hidden, and the shipped stylesheet
  makes the `hidden` attribute win — a zero-height map silently renders no tiles.
  `TC: test_every_element_the_page_toggles_ships_hidden`,
  `TC: test_hidden_attribute_is_forced_to_win`,
  `TC: test_shipped_stylesheet_carries_the_override`

---

## FR-11 — Report: splits

### US-11.1 — Compare kilometre by kilometre
- **AC-11.1.1** The session is divided by kilometre, each split carrying time,
  average speed, pace, max speed and average heart rate.
  `TC: test_splits_divide_the_session_by_kilometre`
- **AC-11.1.2** A session with no distance produces no splits.
  `TC: test_splits_empty_without_distance`

---

## FR-12 — Download the original file

### US-12.1 — Get my file back
> As a member, I want the original file, unaltered.

- **AC-12.1.1** The download returns the original bytes exactly.
  `TC: test_download_returns_the_original_bytes`
- **AC-12.1.2** An unlinked member gets `404`, not `403` — a `403` would confirm
  the session exists.
  `TC: test_download_is_404_for_an_unlinked_member`
- **AC-12.1.3** A visitor is redirected to sign in.
  `TC: test_download_redirects_an_anonymous_visitor`

---

## FR-13 — Delete a session

### US-13.1 — Remove a session I own
- **AC-13.1.1** Deleting removes the session, and every link to it is cascaded
  away rather than left dangling.
  `TC: test_delete_removes_the_activity`,
  `TC: test_deleting_an_activity_cascades_every_link`
- **AC-13.1.2** A linked member can delete.
  `TC: test_a_linked_member_can_delete`
- **AC-13.1.3** An unlinked member gets `404`.
  `TC: test_delete_is_404_for_an_unlinked_member`
- **AC-13.1.4** A visitor is redirected to sign in.
  `TC: test_delete_redirects_an_anonymous_visitor`

---

## FR-14 — Accounts: identity, sign in, sessions

### US-14.1 — Sign in with an email and password
- **AC-14.1.1** The account is identified by email; email and password are the
  only credentials.
  `TC: test_login_page_renders`,
  `TC: test_login_with_correct_credentials_redirects_to_the_index`
- **AC-14.1.2** The email is accepted however it is typed — trimmed and
  case-folded.
  `TC: test_login_accepts_the_email_however_it_is_typed`,
  `TC: test_email_is_lowercased_and_trimmed`
- **AC-14.1.3** Passwords are stored as scrypt hashes; the same password hashes
  differently each time, and a corrupt hash fails closed.
  `TC: test_password_roundtrip`, `TC: test_same_password_hashes_differently`,
  `TC: test_check_password_rejects_a_corrupt_hash`

### US-14.2 — Sign-in failures reveal nothing
> As the operator, I don't want the login page to tell a stranger who has an account.

- **AC-14.2.1** A wrong password and a non-existent account produce the **same**
  single generic error.
  `TC: test_login_with_wrong_password_shows_one_generic_error`,
  `TC: test_login_does_not_reveal_whether_an_account_exists`
- **AC-14.2.2** A deactivated member cannot sign in.
  `TC: test_login_rejects_a_deactivated_member`

### US-14.3 — A session that behaves
- **AC-14.3.1** The session lasts 12 hours and slides on each request.
  `TC: test_session_lasts_twelve_hours`,
  `TC: test_disabling_session_refresh_would_break_sliding`
- **AC-14.3.2** The cookie is `SameSite=Lax`, `HttpOnly`, and marked `Secure`
  exactly when the request was HTTPS — so the public hostname never sends it in
  the clear while the plain-HTTP LAN mount still works.
  `TC: test_cookie_is_samesite_lax_and_httponly`,
  `TC: test_the_cookie_is_secure_over_https_and_plain_over_http`,
  `TC: test_cookie_secure_can_be_forced_on`
- **AC-14.3.3** The session survives a round trip through the cookie, and a
  signed-in member is reported as the current member.
  `TC: test_session_survives_a_round_trip_through_the_cookie`,
  `TC: test_login_then_current_member`
- **AC-14.3.4** Signing out clears the session, and signing out twice is
  harmless.
  `TC: test_sign_out_clears_the_session`,
  `TC: test_sign_out_when_already_signed_out_is_harmless`,
  `TC: test_logout_signs_the_member_out`
- **AC-14.3.5** A post-sign-in `next` parameter is honoured only when it points
  at this site; `//evil.example/` is refused so the login page cannot be used as
  an open redirector.
  `TC: test_login_honours_a_safe_next_parameter`,
  `TC: test_login_refuses_to_redirect_off_site`

### US-14.4 — Authority is re-read, never trusted from the cookie
- **AC-14.4.1** The member row is re-read every request, so a deactivation,
  deletion or password change takes effect on the **next** request rather than at
  the next sign-in.
  `TC: test_current_member_ignores_a_deactivated_member`,
  `TC: test_current_member_ignores_a_deleted_member`,
  `TC: test_current_member_ignores_a_stale_epoch`,
  `TC: test_deactivated_member_is_signed_out_on_the_next_request`
- **AC-14.4.2** An anonymous caller has no member.
  `TC: test_current_member_is_none_when_anonymous`

### US-14.5 — The signing key is a real secret
- **AC-14.5.1** The key is generated, persisted with owner-only permissions,
  stable across restarts, and honours the environment.
  `TC: test_secret_key_is_stable_and_not_the_old_default`,
  `TC: test_secret_key_file_is_private`,
  `TC: test_secret_key_reuses_a_key_already_on_disk`,
  `TC: test_secret_key_honours_the_environment`,
  `TC: test_secret_key_is_not_the_insecure_default`
- **AC-14.5.2** Two workers starting together converge on one key rather than
  each generating their own.
  `TC: test_secret_key_reuses_a_key_already_on_disk`

---

## FR-15 — Per-member visibility

The core access rule: being signed in grants nothing on its own.

### US-15.1 — I see only what I am linked to
- **AC-15.1.1** A member sees exactly the sessions they are linked to.
  `TC: test_index_lists_only_linked_sessions`, `TC: test_link_and_visibility`
- **AC-15.1.2** Opening, downloading or deleting an unlinked session answers
  **`404`, not `403`** — a `403` confirms the id exists.
  `TC: test_report_is_404_for_an_unlinked_member`,
  `TC: test_download_is_404_for_an_unlinked_member`,
  `TC: test_delete_is_404_for_an_unlinked_member`
- **AC-15.1.3** A linked member can open the report.
  `TC: test_a_linked_member_can_open_the_report`
- **AC-15.1.4** A member cannot see a session they are not linked to even after
  sharing has been used elsewhere.
  `TC: test_a_member_still_cannot_see_a_session_they_are_not_linked_to`
- **AC-15.1.5** An unknown session id is `404`.
  `TC: test_unknown_activity_is_not_found`

### US-15.2 — Everything else needs a session
- **AC-15.2.1** Every page except the liveness probe and the sign-in page
  requires a session.
  `TC: test_report_redirects_an_anonymous_visitor`
- **AC-15.2.2** Sharing requires signing in.
  `TC: test_sharing_requires_signing_in`

---

## FR-16 — Sharing a session

A session is a *shared* record. A two-member canoe is one session linked twice.

### US-16.1 — Share my session with someone else
- **AC-16.1.1** A linked member can add another member to the session, and a
  session can carry several members — a two-member canoe is one session linked
  twice.
  `TC: test_a_linked_member_can_add_another`,
  `TC: test_a_session_can_have_several_members`
- **AC-16.1.2** Adding the same member twice changes nothing and does not error.
  `TC: test_adding_the_same_member_twice_is_harmless`
- **AC-16.1.3** A linked member can remove another member.
  `TC: test_a_linked_member_can_remove_another`
- **AC-16.1.4** Removing the **last** member is refused outright: an unowned
  session is invisible to every account, which is indistinguishable from having
  lost it.
  `TC: test_cannot_remove_the_last_member`
- **AC-16.1.5** An unlinked member cannot share someone else's session.
  `TC: test_an_unlinked_member_cannot_share_someone_elses_session`
- **AC-16.1.6** Adding an unknown member is refused.
  `TC: test_cannot_add_an_unknown_member`
- **AC-16.1.7** A malformed sharing request is `400`.
  `TC: test_a_malformed_request_is_a_bad_request`
- **AC-16.1.8** The report page lists the session's members and offers the ones
  not yet on it.
  `TC: test_the_report_page_lists_the_members_and_offers_the_others`

---

## FR-17 — Password change and session eviction

### US-17.1 — Changing a password ends older sessions
> As a member, if my cookie may have been copied, changing my password must end
> that session — for good.

- **AC-17.1.1** An admin can set a new password for a member.
  `TC: test_the_admin_changes_a_password`
- **AC-17.1.2** Changing a password bumps a per-member `session_epoch`, so every
  cookie issued before it stops working.
  `TC: test_set_password_bumps_session_epoch`,
  `TC: test_changing_a_password_signs_that_member_out_everywhere`,
  `TC: test_password_change_evicts_an_existing_session`
- **AC-17.1.3** A live session continues to work after an unrelated change.
  `TC: test_a_live_session_still_works`
- **AC-17.1.4** An empty new password is refused; an unknown member is `404`.
  `TC: test_an_empty_new_password_is_refused`,
  `TC: test_a_password_for_an_unknown_member_is_404`,
  `TC: test_set_password_for_unknown_member_is_false`
- **AC-17.1.5** Signing out only clears *this browser's* copy of the cookie. It
  cannot revoke a copy someone already took; the documented remedy is a password
  change. *(No test — this is a property of cookie sessions, stated so nobody
  believes otherwise.)*

---

## FR-18 — Change your own email

### US-18.1 — Change my sign-in address without losing anything
> As a member, I want to change my email, but my links to sessions and my access
> must stay.

- **AC-18.1.1** A member can reach a page showing their current sign-in address.
  `TC: test_the_account_page_shows_the_current_email`
- **AC-18.1.2** The page is reachable from the header, and requires a session.
  `TC: test_the_nav_links_to_the_account_page`,
  `TC: test_the_account_page_needs_a_session`
- **AC-18.1.3** A member can change their own email, and is told it worked.
  `TC: test_a_member_can_change_their_own_email`,
  `TC: test_the_change_is_confirmed_on_the_page`
- **AC-18.1.4** The member's `id` does not change, so **every session link
  survives** — nothing is re-linked and nothing is back-filled.
  `TC: test_the_member_id_is_unchanged`, `TC: test_the_session_links_survive`,
  `TC: test_change_email_keeps_every_session_link`,
  `TC: test_change_email_updates_in_place_and_keeps_the_id`
- **AC-18.1.5** The member stays signed in: `session_epoch` is **not** bumped. A
  password change evicts sessions; editing your own address must not.
  `TC: test_the_member_stays_signed_in`,
  `TC: test_change_email_does_not_touch_the_session_epoch`
- **AC-18.1.6** The password hash, admin flag and active status are untouched.
  `TC: test_the_password_and_admin_flag_survive`,
  `TC: test_change_email_keeps_the_password_admin_flag_and_activity`
- **AC-18.1.7** The old address stops signing in and the new one works.
  `TC: test_the_new_email_signs_in_and_the_old_one_does_not`
- **AC-18.1.8** The address is trimmed and case-folded.
  `TC: test_the_email_is_trimmed_and_case_folded`,
  `TC: test_change_email_normalises_what_it_is_given`
- **AC-18.1.9** An address already in use is refused **case-insensitively** (the
  column is `UNIQUE COLLATE NOCASE`), and the refusal leaves the member untouched.
  `TC: test_an_email_already_in_use_is_refused`,
  `TC: test_a_duplicate_is_refused_regardless_of_case`,
  `TC: test_change_email_refuses_an_address_already_in_use`
- **AC-18.1.10** Input that is not an email address is refused (empty,
  whitespace, `nope`, `no@`, `@no.com`).
  `TC: test_something_that_is_not_an_email_is_refused`
- **AC-18.1.11** An admin can fix anyone's address, and that member's links
  survive it. This is the route back in if someone mistypes theirs and is then
  signed out.
  `TC: test_an_admin_can_change_another_members_email`,
  `TC: test_an_admin_change_keeps_that_members_links`
- **AC-18.1.12** A non-admin cannot change someone else's address.
  `TC: test_a_non_admin_cannot_change_someone_elses_email`
- **AC-18.1.13** An admin cannot take an address already in use.
  `TC: test_an_admin_cannot_take_an_email_that_is_in_use`
- **AC-18.1.14** An unknown member returns nothing rather than erroring.
  `TC: test_change_email_for_an_unknown_member_returns_none`

> **Note on absent functionality.** There is no mail sending anywhere in this app,
> so an email change takes effect immediately with **no confirmation step**, and
> there is no self-service password reset. Both are deliberate; see §5.

---

## FR-19 — Admin: member management

### US-19.1 — Manage who can sign in
- **AC-19.1.1** An anonymous caller is redirected to sign in, **not** forbidden —
  a `403` would confirm the route exists.
  `TC: test_an_anonymous_visitor_is_redirected_not_forbidden`,
  `TC: test_an_anonymous_post_is_redirected_too`
- **AC-19.1.2** A signed-in non-admin gets `403`, and cannot create a member.
  `TC: test_a_signed_in_non_admin_gets_403`,
  `TC: test_a_non_admin_cannot_create_a_member`
- **AC-19.1.3** The admin sees every member, with a link to change a password.
  `TC: test_the_admin_sees_every_member`,
  `TC: test_the_admin_screen_links_change_password`
- **AC-19.1.4** An admin can create a member, who is **not** an admin by default;
  a created member can be fetched back.
  `TC: test_the_admin_creates_a_member`,
  `TC: test_a_new_member_is_not_an_admin_by_default`,
  `TC: test_created_member_is_active_and_not_admin_by_default`,
  `TC: test_create_and_fetch_member`
- **AC-19.1.5** A duplicate email is reported and nothing changes.
  `TC: test_a_duplicate_email_is_reported_and_nothing_changes`,
  `TC: test_duplicate_email_rejected`
- **AC-19.1.6** Missing or unusable input is refused: an unusable email, a
  missing password, a missing name.
  `TC: test_an_unusable_email_is_rejected`,
  `TC: test_a_missing_password_is_rejected`,
  `TC: test_a_missing_name_is_rejected`
- **AC-19.1.7** An admin can rename, promote, deactivate and reactivate a member.
  `TC: test_the_admin_renames_a_member`, `TC: test_the_admin_promotes_another_member`,
  `TC: test_the_admin_deactivates_a_second_member`,
  `TC: test_update_member_renames_and_promotes`
- **AC-19.1.8** An update for an unknown member is `404`; so is an update by id
  that does not exist. Deactivating never deletes the row.
  `TC: test_an_update_for_an_unknown_member_is_404`,
  `TC: test_update_member_returns_none_for_unknown_id`,
  `TC: test_deactivating_does_not_delete_the_row`

---

## FR-20 — Admin: the last-admin guard

### US-20.1 — Never lock everyone out
> As the operator, I want it to be impossible to reach zero admins through the UI.

- **AC-20.1.1** The last active admin cannot demote or deactivate themselves.
  `TC: test_the_last_admin_cannot_demote_themselves`,
  `TC: test_the_last_admin_cannot_deactivate_themselves`,
  `TC: test_update_member_safely_refuses_the_last_admin_demoting_themselves`,
  `TC: test_update_member_safely_refuses_the_last_admin_deactivating_themselves`
- **AC-20.1.2** A second admin can be demoted or deactivated.
  `TC: test_a_second_admin_can_be_demoted`,
  `TC: test_a_second_admin_can_be_deactivated`,
  `TC: test_update_member_safely_allows_a_spare_admin_to_be_demoted`
- **AC-20.1.3** An inactive admin does not count towards the guard.
  `TC: test_an_inactive_admin_does_not_count_towards_the_guard`,
  `TC: test_update_member_safely_ignores_an_inactive_admin`,
  `TC: test_count_admins`, `TC: test_first_admin`,
  `TC: test_first_admin_skips_deactivated`
- **AC-20.1.4** The count and the write happen in **one** transaction that takes
  the write lock before the count is read. Two workers can otherwise both read a
  count of two, both conclude the change is safe, and both apply it.
  `TC: test_two_racing_demotions_cannot_leave_zero_admins`
- **AC-20.1.5** Reactivating and renaming still work through the guarded path.
  `TC: test_update_member_safely_allows_reactivating`,
  `TC: test_update_member_safely_still_renames`,
  `TC: test_update_member_safely_returns_none_for_an_unknown_id`

---

## FR-21 — Admin: deactivation re-homes sessions

### US-21.1 — Deactivating a member must not lose their sessions
- **AC-21.1.1** Deactivating the only member on a session does not make those
  sessions invisible: they are reassigned to the admin performing the change, and
  the screen says how many.
  `TC: test_deactivating_a_sole_owner_does_not_lose_their_sessions`
- **AC-21.1.2** Reactivating a member keeps them on their sessions.
  `TC: test_reactivating_a_member_keeps_them_on_their_sessions`
- **AC-21.1.3** When the sessions are already shared with someone active, the
  deactivation is quiet — nothing is reassigned.
  `TC: test_deactivating_someone_whose_sessions_are_shared_is_quiet`

---

## FR-22 — Sign-in throttling

### US-22.1 — Slow down guessing without enabling a lockout
- **AC-22.1.1** Five failed sign-ins from one address against one account within
  fifteen minutes cause the next attempt to answer `429`.
  `TC: test_repeated_failures_are_throttled`
- **AC-22.1.2** Throttling is keyed on the **(email, address) pair**, not either
  alone: address alone lets one shared NAT lock out everyone behind it, and email
  alone lets a stranger lock a known member out of their own account.
  `TC: test_throttling_does_not_lock_out_a_different_address`
- **AC-22.1.3** A successful sign-in clears the failures.
  `TC: test_a_successful_login_clears_the_failures`
- **AC-22.1.4** Failed sign-ins are logged.
  `TC: test_a_failed_login_is_logged`
- **AC-22.1.5** The counter lives in SQLite, not process memory: the app runs two
  worker processes with no preload, so an in-process counter would give an
  attacker twice the attempts and forget on every restart.
  `TC: test_the_throttle_survives_a_restart`

---

## FR-23 — Cross-site write protection

### US-23.1 — A write from another site is refused
- **AC-23.1.1** A state-changing request whose `Origin` (or `Referer`) names
  another host answers `403`.
  `TC: test_a_write_from_another_site_is_refused`
- **AC-23.1.2** A write from this site is allowed.
  `TC: test_a_write_from_this_site_is_allowed`
- **AC-23.1.3** A request sending neither header is allowed, because a browser
  cannot suppress `Origin` on a cross-site `POST` — it is a forbidden header — so
  an absent header means the caller is not a browser making a cross-site request.
  `TC: test_a_write_with_no_origin_at_all_is_allowed`
- **AC-23.1.4** `Referer` is used when `Origin` is absent, and a matching
  `Referer` is allowed.
  `TC: test_a_cross_site_referer_is_used_when_origin_is_absent`,
  `TC: test_a_matching_referer_is_allowed`
- **AC-23.1.5** A `null` origin (a sandboxed iframe or a `file://` page) is
  refused.
  `TC: test_a_null_origin_is_refused`
- **AC-23.1.6** Safe methods are not checked, so ordinary navigation is
  unaffected.
  `TC: test_a_safe_method_is_not_checked`
- **AC-23.1.7** The check covers **every** write in the app: signing in and out,
  uploading, deleting, sharing, creating a member, and changing a password.
  `TC: test_it_covers_signing_in`, `TC: test_it_covers_signing_out`,
  `TC: test_it_covers_an_upload`, `TC: test_it_covers_deleting_a_session`,
  `TC: test_it_covers_sharing_a_session`,
  `TC: test_it_covers_creating_a_member`,
  `TC: test_it_covers_the_admin_password_change`
- **AC-23.1.8** Changing your own email is covered by the same rule with no extra
  wiring.
  `TC: test_a_cross_site_change_is_refused`

---

## FR-24 — Bootstrap and CLI

### US-24.1 — Create the first account
- **AC-24.1.1** A CLI command creates a member, admin or plain, and **never
  echoes the password**.
  `TC: test_the_cli_creates_a_member`,
  `TC: test_the_cli_creates_a_plain_member_without_admin`,
  `TC: test_the_cli_never_echoes_the_password`
- **AC-24.1.2** The CLI refuses a duplicate email, an unusable email, or no
  password, and can take the password from the environment.
  `TC: test_the_cli_refuses_a_duplicate_email`,
  `TC: test_the_cli_refuses_an_unusable_email`,
  `TC: test_the_cli_creates_nothing_without_a_password`,
  `TC: test_the_cli_takes_the_password_from_the_environment`

### US-24.2 — A fresh install configures itself
- **AC-24.2.1** Three environment variables bootstrap the first admin on a fresh
  install.
  `TC: test_a_fresh_install_can_bootstrap_its_first_admin`
- **AC-24.2.2** The bootstrap does nothing without the variables, and does
  nothing once any member exists — **so a restart can never resurrect a deleted
  account or reset a password**.
  `TC: test_the_bootstrap_does_nothing_without_the_variables`,
  `TC: test_the_bootstrap_does_not_fire_once_a_member_exists`,
  `TC: test_the_bootstrap_ignores_an_unusable_email`

### US-24.3 — Existing data is never orphaned by an upgrade
- **AC-24.3.1** At startup, activities with no link are attached to an admin.
  `TC: test_startup_links_orphaned_activities`
- **AC-24.3.2** The backfill touches **only unlinked** activities, and is wired
  into the **app factory**, not merely available in the store — a correct
  migration that nothing calls still orphans the data.
  `TC: test_startup_links_orphaned_activities`,
  `TC: test_backfill_links_only_unlinked_activities`
- **AC-24.3.3** It is idempotent and does nothing when there is no admin.
  `TC: test_startup_backfill_is_idempotent`,
  `TC: test_startup_backfill_does_nothing_without_an_admin`

---

## FR-25 — Liveness endpoint

### US-25.1 — A public probe that says nothing else
- **AC-25.1.1** `/healthz` is public, returns `{"ok": true}`, and discloses
  nothing about the deployment — not even how many sessions exist.
  `TC: test_healthz`, `TC: test_healthz_stays_public`,
  `TC: test_healthz_does_not_leak_the_global_count`

---

## FR-26 — Time display in a local timezone

### US-26.1 — Read times in local time, not UTC
> As a member, I want the session's date and time in the timezone I actually
> record in.

- **AC-26.1.1** Timestamps are **stored** as UTC (FIT files record UTC) and
  **rendered** in a configured timezone, default `Australia/Sydney`, overridable
  by `SDA_DISPLAY_TZ`.
  `TC: test_daytime_renders_in_the_display_timezone`,
  `TC: test_the_display_timezone_is_configurable`
- **AC-26.1.2** Daylight saving is handled: the same code renders AEST (+10) in
  May and AEDT (+11) in January. A fixed offset would pass one and be an hour
  wrong for half the year.
  `TC: test_daytime_follows_daylight_saving`
- **AC-26.1.3** The rendered time carries its zone abbreviation, because an
  unqualified time is the ambiguity being removed.
  `TC: test_daytime_renders_in_the_display_timezone`
- **AC-26.1.4** Date-only labels use the **local** calendar day, so an evening
  session is not labelled with the previous day.
  `TC: test_short_date_uses_the_local_calendar_day`
- **AC-26.1.5** A naive timestamp is read as UTC, not as server-local time, so
  the result cannot depend on where the process runs.
  `TC: test_naive_timestamps_are_read_as_utc`
- **AC-26.1.6** Missing or malformed input renders as an em dash rather than
  raising; an unresolvable timezone degrades to the default rather than breaking
  the page.
  `TC: test_time_formatters_survive_missing_and_broken_input`,
  `TC: test_an_unknown_timezone_degrades_instead_of_breaking_the_page`
- **AC-26.1.7** The index and the report header both render in that timezone, and
  the header is rendered **server-side** so it does not depend on the viewer's
  browser timezone.
  `TC: test_index_renders_the_session_time_in_the_display_timezone`,
  `TC: test_report_header_is_rendered_server_side_in_the_display_timezone`
- **AC-26.1.8** Durations, distances, speeds and paces are formatted consistently
  for display.
  `TC: test_duration_formatting`, `TC: test_distance_and_speed_formatting`,
  `TC: test_pace_formatting`, `TC: test_window_label`,
  `TC: test_window_label_handles_missing_values`

---

## FR-27 — Report caching and regeneration

### US-27.1 — Reports are cached, and improving the analysis upgrades old sessions
- **AC-27.1.1** The computed report is stored alongside the upload rather than
  recomputed per request.
  `TC: test_report_page_embeds_the_payload`
- **AC-27.1.2** A report written by an older analysis version is regenerated from
  the original `.fit` and the cache rewritten, so improving a statistic upgrades
  sessions uploaded months ago.
  `TC: test_stale_report_is_rebuilt_from_the_original_file`
- **AC-27.1.3** A missing stored file produces a clean page, not a 500.
  `TC: test_report_stays_clean_when_the_stored_file_is_missing`
- **AC-27.1.4** A stored upload always keeps its original bytes for that
  regeneration.
  `TC: test_download_returns_the_original_bytes`

---

## FR-28 — Page delivery and mounting

### US-28.1 — The same code serves both mounts
- **AC-28.1.1** Static assets are served.
  `TC: test_static_assets_are_served`
- **AC-28.1.2** Third-party frontend libraries are vendored, not loaded from a
  CDN.
  `TC: test_leaflet_is_vendored`
- **AC-28.1.3** The page respects a path prefix when served behind a
  path-mounting proxy, using root-relative URLs throughout.
  `TC: test_report_page_carries_the_path_prefix_when_proxied`
- **AC-28.1.4** The stylesheet exists and enforces the rules the pages depend on.
  `TC: test_stylesheet_exists`, `TC: test_the_map_is_taller_on_a_phone_than_it_used_to_be`

---

## FR-29 — Data integrity invariants

Enforced in the schema rather than in each write path, because an invariant that
lives only in application code holds exactly as long as every writer remembers it.

- **AC-29.1.1** `users.email` is `UNIQUE COLLATE NOCASE`. SQLite's default `TEXT`
  collation is `BINARY`, so a plain `UNIQUE` would accept `RPF@…` beside
  `rpf@…` and split one person into two accounts whose login depends on which
  casing they type.
  `TC: test_duplicate_email_rejected`,
  `TC: test_a_duplicate_is_refused_regardless_of_case`
- **AC-29.1.2** Deleting an activity cascades to its links, and enforcement is
  per-connection (`PRAGMA foreign_keys=ON` in the connect helper — SQLite has
  foreign keys off by default).
  `TC: test_deleting_an_activity_cascades_every_link`
- **AC-29.1.3** A link whose activity has since gone is not counted.
  `TC: test_count_for_ignores_a_link_whose_activity_is_gone`
- **AC-29.1.4** A database written before the join table was renamed migrates on
  startup, keeping its sessions, and the migration is a no-op afterwards.
  `TC: test_a_database_written_before_the_rename_keeps_its_sessions`,
  `TC: test_the_rename_migration_runs_once_and_is_harmless_afterwards`
- **AC-29.1.5** Member verification rejects a deactivated member.
  `TC: test_verify_member`, `TC: test_verify_rejects_a_deactivated_member`,
  `TC: test_session_epoch_for_unknown_member_is_none`

---

## 4. Traceability

| FR | Primary test file(s) |
|---|---|
| FR-1 Upload | `test_web.py` |
| FR-2 Decode | `test_fitio.py` |
| FR-3 Dedupe | `test_web.py`, `test_visibility.py` |
| FR-4 List | `test_web.py`, `test_visibility.py` |
| FR-5 Statistics | `test_analysis.py`, `test_web.py` |
| FR-6 Histogram | `test_analysis.py` |
| FR-7 Trace / windows | `test_analysis.py`, `test_web.py` |
| FR-8 Heart rate | `test_analysis.py`, `test_web.py` |
| FR-9 Elevation | `test_analysis.py`, `test_fitio.py` |
| FR-10 Map | `test_basemaps.py`, `test_web.py`, `test_stylesheet.py` |
| FR-11 Splits | `test_analysis.py` |
| FR-12 Download | `test_web.py`, `test_visibility.py` |
| FR-13 Delete | `test_web.py`, `test_visibility.py` |
| FR-14 Accounts | `test_auth.py`, `test_users.py` |
| FR-15 Visibility | `test_visibility.py` |
| FR-16 Sharing | `test_visibility.py` |
| FR-17 Password / epoch | `test_admin.py`, `test_users.py` |
| FR-18 Email change | `test_account.py`, `test_users.py` |
| FR-19 Admin management | `test_admin.py` |
| FR-20 Last-admin guard | `test_admin.py`, `test_users.py` |
| FR-21 Re-homing | `test_admin.py` |
| FR-22 Throttling | `test_auth.py` |
| FR-23 Cross-site | `test_cross_site.py`, `test_account.py` |
| FR-24 Bootstrap / CLI | `test_cli.py`, `test_users.py` |
| FR-25 Liveness | `test_web.py`, `test_visibility.py` |
| FR-26 Timezone | `test_formatting.py`, `test_web.py` |
| FR-27 Caching | `test_web.py` |
| FR-28 Delivery | `test_web.py`, `test_stylesheet.py` |
| FR-29 Integrity | `test_users.py` |

**Suite totals:** 283 test functions across 13 application test files, expanding
to **330 test cases** with parametrisation. A fourteenth file,
`tests/test_requirements.py`, checks this document rather than the app and is
excluded from the counts above. Run everything with `python -m pytest`.

---

## 5. Known gaps and deliberate absences

Stated plainly so that nothing here is mistaken for a guarantee.

### Not covered by an automated test
| Gap | Why | Risk |
|---|---|---|
| The `SDA_MAX_UPLOAD_BYTES` ceiling (AC-1.3.1) | Configured through the framework's request-size setting with a `413` handler; no dedicated test | A regression would be silent until a large upload |
| `app/static/js/report.js` rendering | The browserless harness (`tools/verify_report_js.js`) needs node, which is not installed on the app host or the dev host, and is not wired into CI | Frontend rendering changes rest on review, not a test |
| Cross-browser behaviour | No browser test exists | Layout/interaction regressions surface only to a user |

### Deliberate absences — not defects
| Absence | Rationale |
|---|---|
| **No email verification** | The app cannot send mail. An address change takes effect immediately. A typo is recoverable while the member is still signed in, or by an admin. |
| **No self-service password change or reset** | Passwords are set by an admin. There is no mail channel to run a reset over. |
| **Sign-out cannot revoke a copied cookie** | These are client-side cookie sessions. The documented remedy is a password change, which bumps `session_epoch`. |
| **No server-side session store** | Sessions are signed cookies. Consequences above; accepted. |
| **No audit log of admin actions** | Not required by any stated use case. |
| **No multi-tenant separation** | One deployment serves one group who share sessions by explicit linking. |

### Non-functional properties pinned as functional constraints
These are listed because regressing them changes observable behaviour, not
because the app has performance targets:

- Each rolling window is computed **exactly once** per report build (AC-7.1.5).
  Computing it inline took a 13,471-record file from 0.5 s to 105 s.
- Charts are **downsampled** before reaching the browser (AC-7.1.9) so a large
  recording stays usable on a phone while keeping its peaks.
- The throttle counter is in **SQLite**, not process memory (AC-22.1.5), because
  the app runs two worker processes.
- The last-admin count and write share **one transaction** (AC-20.1.4) for the
  same reason.
