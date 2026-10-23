"""The requirements document must match the test suite.

`docs/requirements.md` states what the app does and cites the test that verifies
each acceptance criterion. That traceability is the document's entire value, so it
is enforced here rather than trusted:

- a criterion citing a test that does not exist makes the document a false claim,
  which is worse than not having the document at all;
- a test no criterion cites means the document has stopped accounting for the
  suite, and a reader will take its silence for "not required".

When one of these fails you have changed the suite without changing the
requirements, or the reverse. Fix whichever is wrong — do not weaken the test.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "requirements.md"

#: A criterion line, with or without the markdown backtick.
_TC_PREFIXES = ("TC:", "`TC:")

#: This file. Its tests check the document, not the application, so they are not
#: behaviour the document should describe - counting them would make the checks
#: self-referential and unsatisfiable.
_META = "test_requirements.py"


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _real_tests() -> set[str]:
    names: set[str] = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name == _META:
            continue
        names.update(
            re.findall(r"^\s*def (test_[a-z0-9_]+)", path.read_text(encoding="utf-8"), re.M)
        )
    return names


def _cited(text: str) -> list[str]:
    return [
        name
        for line in text.splitlines()
        if line.strip().startswith(_TC_PREFIXES)
        for name in re.findall(r"(test_[a-z0-9_]+)", line)
    ]


def _cited_by_requirement(text: str) -> dict[str, set[str]]:
    """FR id -> the tests its criteria name."""
    out: dict[str, set[str]] = {}
    sections = re.split(r"\n## (FR-\d+) — ", text)
    for i in range(1, len(sections), 2):
        body = sections[i + 1].split("\n## ")[0]
        names: set[str] = set()
        for line in body.splitlines():
            if line.strip().startswith(_TC_PREFIXES):
                names.update(re.findall(r"(test_[a-z0-9_]+)", line))
        out[sections[i]] = names
    return out


def test_the_requirements_document_exists():
    assert DOC.is_file(), f"missing {DOC}"


def test_every_cited_test_exists():
    """A criterion naming a test that is not real is a false claim."""
    missing = sorted(set(_cited(_text())) - _real_tests())

    assert not missing, (
        "docs/requirements.md cites tests that do not exist: "
        + ", ".join(missing)
        + " — fix the name or remove the criterion"
    )


def test_every_test_is_cited_by_a_requirement():
    """The document must account for the whole suite."""
    uncited = sorted(_real_tests() - set(_cited(_text())))

    assert not uncited, (
        "these tests are not cited by any requirement: "
        + ", ".join(uncited)
        + " — add the acceptance criterion they prove to docs/requirements.md"
    )


def test_every_requirement_cites_at_least_one_test():
    bare = sorted(fr for fr, names in _cited_by_requirement(_text()).items() if not names)

    assert not bare, (
        "these requirements cite no test, so nothing shows they hold: " + ", ".join(bare)
    )


def test_the_index_counts_match_the_body():
    """The summary table must not disagree with what it summarises."""
    text = _text()
    by_fr = _cited_by_requirement(text)

    wrong = []
    for fr, claimed in re.findall(r"^\| (FR-\d+) \| [^|]+ \| (\d+) \|", text, re.M):
        actual = len(by_fr.get(fr, ()))
        if int(claimed) != actual:
            wrong.append(f"{fr}: table says {claimed}, body cites {actual}")

    assert not wrong, "the index disagrees with the body: " + "; ".join(wrong)
