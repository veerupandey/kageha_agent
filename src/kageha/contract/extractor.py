"""Deterministic_Extractor (REL-011) — rule-based extraction of explicit requirements.

Extends the regex families already in runtime/validators.compile_requirements()
(slide/page counts, citations, browser outcome) with filename, dimensions,
test-command, and explicit-prohibition patterns. compile_requirements() itself
becomes a thin compatibility adapter over this extractor (REL-011.2).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from kageha.contract.models import ConstraintSource, ContractStatus, Requirement, RequirementKind


def _new_id(prefix: str = "req") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


_SLIDE_RE = re.compile(r"\b(?:create|make|build|produce)?\s*(\d+)\s+slides?\b", re.I)
_PDF_PAGES_RE = re.compile(r"\b(\d+)\s+(?:page|pages)\s+(?:pdf|document)\b", re.I)
_CITATION_RE = re.compile(r"\b(citations?|cited|sources?|sourced|research)\b", re.I)
_BROWSER_OUTCOME_RE = re.compile(
    r"\b(capture|take|save|create)\b.{0,30}\bscreenshot\b|"
    r"\bnavigate\b.{0,30}\b(browser|site|page)\b",
    re.I,
)
# New patterns extending the deterministic families (REL-011.1).
_FILE_COUNT_RE = re.compile(
    r"\b(\d+)\s+(?:files?|documents?|images?|scripts?|modules?)\b", re.I
)
_FILENAME_RE = re.compile(
    r"\b(?:file|filename|named?|call(?:ed)?|save(?:d)?\s+as)\s+"
    r"[`\"']?([\w.\-/]+\.\w{1,6})[`\"']?",
    re.I,
)
_DIMENSIONS_RE = re.compile(r"\b(\d+)\s*[x×]\s*(\d+)\b(?:\s*(px|pixels?))?", re.I)
_TEST_COMMAND_RE = re.compile(
    r"(?:run|use|execute)\s+(?:the\s+)?(?:command\s+)?[`\"']?"
    r"((?:pytest|npm test|npm run test|cargo test|go test|python -m pytest)"
    r"[^`\"'\n,;]*)[`\"']?",
    re.I,
)
_PROHIBITION_RE = re.compile(
    r"\b(?:do not|don't|never|must not|should not|avoid)\s+([^.;\n]{3,120})",
    re.I,
)


@dataclass
class ExtractionResult:
    requirements: list[Requirement]


class DeterministicExtractor:
    """Rule-based extraction of typed Requirement entries from request text."""

    def extract(self, objective: str) -> ExtractionResult:
        text = objective or ""
        requirements: list[Requirement] = []

        for slide in _SLIDE_RE.finditer(text):
            requirements.append(
                Requirement(
                    id=_new_id("slide"),
                    kind=RequirementKind.SLIDE_COUNT,
                    value=int(slide.group(1)),
                    source=ConstraintSource.EXPLICIT,
                )
            )

        for pdf_pages in _PDF_PAGES_RE.finditer(text):
            requirements.append(
                Requirement(
                    id=_new_id("page"),
                    kind=RequirementKind.PAGE_COUNT,
                    value=int(pdf_pages.group(1)),
                    source=ConstraintSource.EXPLICIT,
                )
            )

        if _CITATION_RE.search(text):
            requirements.append(
                Requirement(
                    id=_new_id("cite"),
                    kind=RequirementKind.CITATION,
                    value=True,
                    source=ConstraintSource.DETERMINISTIC,
                )
            )

        if _BROWSER_OUTCOME_RE.search(text):
            requirements.append(
                Requirement(
                    id=_new_id("browse"),
                    kind=RequirementKind.BROWSER_OUTCOME,
                    value=True,
                    source=ConstraintSource.DETERMINISTIC,
                )
            )

        file_count = _FILE_COUNT_RE.search(text)
        if file_count:
            requirements.append(
                Requirement(
                    id=_new_id("filecount"),
                    kind=RequirementKind.FILE_COUNT,
                    value=int(file_count.group(1)),
                    source=ConstraintSource.EXPLICIT,
                )
            )

        for match in _FILENAME_RE.finditer(text):
            requirements.append(
                Requirement(
                    id=_new_id("filename"),
                    kind=RequirementKind.FILENAME,
                    value=match.group(1),
                    source=ConstraintSource.EXPLICIT,
                )
            )

        dims = _DIMENSIONS_RE.search(text)
        if dims:
            requirements.append(
                Requirement(
                    id=_new_id("dims"),
                    kind=RequirementKind.DIMENSIONS,
                    value={"width": int(dims.group(1)), "height": int(dims.group(2))},
                    source=ConstraintSource.EXPLICIT,
                )
            )

        test_cmd = _TEST_COMMAND_RE.search(text)
        if test_cmd:
            requirements.append(
                Requirement(
                    id=_new_id("testcmd"),
                    kind=RequirementKind.TEST_COMMAND,
                    value=test_cmd.group(1).strip(),
                    source=ConstraintSource.EXPLICIT,
                )
            )

        for match in _PROHIBITION_RE.finditer(text):
            requirements.append(
                Requirement(
                    id=_new_id("prohibit"),
                    kind=RequirementKind.PROHIBITION,
                    value=match.group(1).strip(),
                    source=ConstraintSource.EXPLICIT,
                )
            )

        requirements = _mark_contradictions(requirements)
        return ExtractionResult(requirements=requirements)


def _mark_contradictions(requirements: list[Requirement]) -> list[Requirement]:
    """Two explicit requirements sharing a kind but conflicting in value are
    both marked UNRESOLVED with mutual `contradicts` refs (REL-011.3) instead
    of one being silently dropped or preferred.
    """
    by_kind: dict[RequirementKind, list[int]] = {}
    for idx, req in enumerate(requirements):
        by_kind.setdefault(req.kind, []).append(idx)

    out = list(requirements)
    for _kind, indices in by_kind.items():
        if len(indices) < 2:
            continue
        values = {out[i].value for i in indices if isinstance(out[i].value, (int, float, str))}
        if len(values) <= 1:
            continue  # same value repeated — not a contradiction
        # Conflicting values found — mark all entries of this kind unresolved.
        ids = tuple(out[i].id for i in indices)
        for i in indices:
            req = out[i]
            others = tuple(j for j in ids if j != req.id)
            out[i] = Requirement(
                id=req.id,
                kind=req.kind,
                value=req.value,
                source=req.source,
                status=ContractStatus.UNRESOLVED,
                contradicts=others,
            )
    return out
