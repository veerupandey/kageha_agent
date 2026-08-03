"""Deterministic artifact validators and requirement compilation."""

from __future__ import annotations

import ast
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from kageha.runtime.types import VerificationResult


@dataclass(frozen=True)
class ValidationCheck:
    validator: str
    target: str
    passed: bool
    status: str
    evidence: str = ""
    defect: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_evidence(
        self,
        *,
        session_id: str,
        turn_id: str,
        criterion_id: str = "",
    ) -> Any:
        """Convert this check into an EvidenceRecord (REL-021.1).

        Captures a sha256 digest of the artifact (when available in
        metadata) plus the structural fields already computed by the
        validator (page/slide counts, dimensions, etc).
        """
        from kageha.verification.evidence import (
            EvidenceCertainty,
            EvidenceRecord,
            EvidenceSource,
        )

        digest = str(self.metadata.get("sha256") or "")
        if not digest and self.evidence:
            import hashlib

            digest = hashlib.sha256(self.evidence.encode("utf-8", errors="replace")).hexdigest()
        certainty = (
            EvidenceCertainty.VERIFIED
            if self.status == "pass"
            else (
                EvidenceCertainty.UNVERIFIABLE
                if self.status == "unresolved"
                else EvidenceCertainty.PROBABLE
            )
        )
        return EvidenceRecord.new(
            session_id=session_id,
            turn_id=turn_id,
            criterion_id=criterion_id,
            source=EvidenceSource.ARTIFACT_DIGEST,
            source_ref=self.target,
            digest=digest,
            certainty=certainty,
            producer=self.validator,
            artifact_path=self.target,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class ValidationContext:
    objective: str
    workspace: Path
    artifacts: tuple[str, ...]

    def resolve(self, relative: str) -> Path:
        root = self.workspace.resolve()
        candidate = (root / relative).resolve()
        if candidate != root and not str(candidate).startswith(str(root) + "/"):
            raise ValueError(f"artifact escapes workspace: {relative}")
        return candidate


class Validator(Protocol):
    name: str

    def supports(self, path: Path) -> bool: ...

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]: ...


class FileValidator:
    name = "file"

    def supports(self, path: Path) -> bool:
        return True

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]:
        if not path.is_file():
            return [
                ValidationCheck(
                    self.name,
                    str(path),
                    False,
                    "fail",
                    defect="artifact does not exist",
                )
            ]
        size = path.stat().st_size
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return [
            ValidationCheck(
                self.name,
                str(path),
                size > 0,
                "pass" if size > 0 else "fail",
                evidence=f"size={size} sha256={digest}",
                defect="" if size > 0 else "artifact is empty",
                metadata={
                    "size_bytes": size,
                    "sha256": digest,
                    "media_type": mimetypes.guess_type(path.name)[0] or "",
                },
            )
        ]


class StructuredDataValidator:
    name = "structured_data"
    _suffixes = {".json", ".yaml", ".yml"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self._suffixes

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]:
        try:
            text = path.read_text(encoding="utf-8")
            value = (
                json.loads(text)
                if path.suffix.lower() == ".json"
                else yaml.safe_load(text)
            )
            if value is None:
                raise ValueError("document is empty")
        except Exception as exc:  # noqa: BLE001
            return [
                ValidationCheck(
                    self.name,
                    str(path),
                    False,
                    "fail",
                    defect=f"invalid {path.suffix.lower()[1:]}: {exc}",
                )
            ]
        return [
            ValidationCheck(
                self.name,
                str(path),
                True,
                "pass",
                evidence=f"parsed root type={type(value).__name__}",
            )
        ]


class PythonValidator:
    name = "python"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".py"

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            return [
                ValidationCheck(
                    self.name,
                    str(path),
                    False,
                    "fail",
                    defect=f"Python compilation failed: {exc}",
                )
            ]
        return [
            ValidationCheck(
                self.name,
                str(path),
                True,
                "pass",
                evidence="Python AST compilation passed",
            )
        ]


class PDFValidator:
    name = "pdf"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = len(reader.pages)
            text_chars = sum(len((page.extract_text() or "").strip()) for page in reader.pages)
        except Exception as exc:  # noqa: BLE001
            return [
                ValidationCheck(
                    self.name,
                    str(path),
                    False,
                    "fail",
                    defect=f"PDF parse/text extraction failed: {exc}",
                )
            ]
        expected = requirements.get("pdf_pages")
        count_ok = expected is None or pages == expected
        checks = [
            ValidationCheck(
                self.name,
                str(path),
                pages > 0 and count_ok,
                "pass" if pages > 0 and count_ok else "fail",
                evidence=f"pages={pages} extracted_text_chars={text_chars}",
                defect=(
                    ""
                    if count_ok
                    else f"expected exactly {expected} PDF pages, found {pages}"
                ),
                metadata={"pages": pages, "text_chars": text_chars},
            )
        ]
        renderer = shutil.which("pdftoppm")
        if renderer:
            with tempfile.TemporaryDirectory(prefix="kageha-pdf-render-") as raw:
                target = Path(raw) / "page"
                proc = subprocess.run(
                    [renderer, "-f", "1", "-singlefile", "-png", str(path), str(target)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                rendered = target.with_suffix(".png")
                ok = proc.returncode == 0 and rendered.is_file() and rendered.stat().st_size > 0
                checks.append(
                    ValidationCheck(
                        "pdf_render",
                        str(path),
                        ok,
                        "pass" if ok else "fail",
                        evidence=f"renderer={renderer} exit={proc.returncode}",
                        defect="" if ok else "PDF first-page rendering failed",
                    )
                )
        else:
            checks.append(
                ValidationCheck(
                    "pdf_render",
                    str(path),
                    False,
                    "unresolved",
                    defect="pdftoppm is unavailable; render verification is unresolved",
                )
            )
        return checks


class PowerPointValidator:
    name = "powerpoint"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pptx"

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]:
        try:
            with zipfile.ZipFile(path) as archive:
                corrupt = archive.testzip()
                slides = [
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                ]
                has_presentation = "ppt/presentation.xml" in archive.namelist()
            if corrupt:
                raise ValueError(f"corrupt member: {corrupt}")
        except Exception as exc:  # noqa: BLE001
            return [
                ValidationCheck(
                    self.name,
                    str(path),
                    False,
                    "fail",
                    defect=f"invalid PPTX package: {exc}",
                )
            ]
        expected = requirements.get("slides")
        count_ok = expected is None or len(slides) == expected
        package_ok = has_presentation and bool(slides) and count_ok
        checks = [
            ValidationCheck(
                self.name,
                str(path),
                package_ok,
                "pass" if package_ok else "fail",
                evidence=f"slides={len(slides)} package_valid={has_presentation}",
                defect=(
                    ""
                    if package_ok
                    else (
                        f"expected exactly {expected} slides, found {len(slides)}"
                        if not count_ok
                        else "PPTX has no valid presentation/slides"
                    )
                ),
                metadata={"slides": len(slides)},
            )
        ]
        renderer = shutil.which("libreoffice") or shutil.which("soffice")
        if renderer:
            with tempfile.TemporaryDirectory(prefix="kageha-pptx-render-") as raw:
                proc = subprocess.run(
                    [
                        renderer,
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        raw,
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
                rendered = Path(raw) / f"{path.stem}.pdf"
                ok = proc.returncode == 0 and rendered.is_file() and rendered.stat().st_size > 0
                checks.append(
                    ValidationCheck(
                        "powerpoint_render",
                        str(path),
                        ok,
                        "pass" if ok else "fail",
                        evidence=f"renderer={renderer} exit={proc.returncode}",
                        defect="" if ok else "PowerPoint rendering failed",
                    )
                )
        else:
            checks.append(
                ValidationCheck(
                    "powerpoint_render",
                    str(path),
                    False,
                    "unresolved",
                    defect="LibreOffice is unavailable; slide rendering is unresolved",
                )
            )
        return checks


class ImageValidator:
    name = "image"
    _suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self._suffixes

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]:
        try:
            from PIL import Image, ImageStat

            with Image.open(path) as image:
                image.load()
                width, height = image.size
                stat = ImageStat.Stat(image.convert("RGB").resize((64, 64)))
                extrema = stat.extrema
                blank = all(low == high for low, high in extrema)
        except Exception as exc:  # noqa: BLE001
            return [
                ValidationCheck(
                    self.name,
                    str(path),
                    False,
                    "fail",
                    defect=f"image decode failed: {exc}",
                )
            ]
        min_width = int(requirements.get("image_min_width") or 64)
        min_height = int(requirements.get("image_min_height") or 64)
        ok = width >= min_width and height >= min_height and not blank
        return [
            ValidationCheck(
                self.name,
                str(path),
                ok,
                "pass" if ok else "fail",
                evidence=f"dimensions={width}x{height} blank={blank}",
                defect=(
                    ""
                    if ok
                    else "image is blank, undecodable, or too small for legible output"
                ),
                metadata={"width": width, "height": height, "blank": blank},
            )
        ]


class VideoValidator:
    name = "video"
    _suffixes = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self._suffixes

    def validate(
        self,
        path: Path,
        *,
        context: ValidationContext,
        requirements: dict[str, Any],
    ) -> list[ValidationCheck]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return [
                ValidationCheck(
                    self.name,
                    str(path),
                    False,
                    "unresolved",
                    defect="ffprobe is unavailable; video validation is unresolved",
                )
            ]
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,nb_frames",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            value = json.loads(proc.stdout)
            duration = float((value.get("format") or {}).get("duration") or 0.0)
            streams = list(value.get("streams") or [])
        except Exception:  # noqa: BLE001
            duration, streams = 0.0, []
        video = [item for item in streams if item.get("codec_type") == "video"]
        audio = [item for item in streams if item.get("codec_type") == "audio"]
        ok = proc.returncode == 0 and duration > 0 and bool(video)
        return [
            ValidationCheck(
                self.name,
                str(path),
                ok,
                "pass" if ok else "fail",
                evidence=(
                    f"duration={duration:.3f}s video_streams={len(video)} "
                    f"audio_streams={len(audio)}"
                ),
                defect="" if ok else "video has no playable duration/video stream",
                metadata={
                    "duration": duration,
                    "video_streams": len(video),
                    "audio_streams": len(audio),
                },
            )
        ]


class ValidatorRegistry:
    def __init__(self, validators: list[Validator] | None = None) -> None:
        self.validators = validators or [
            StructuredDataValidator(),
            PythonValidator(),
            PDFValidator(),
            PowerPointValidator(),
            ImageValidator(),
            VideoValidator(),
        ]
        self.file_validator = FileValidator()

    def validate(
        self,
        context: ValidationContext,
        *,
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        requirements = dict(requirements or compile_requirements(context.objective))
        checks: list[ValidationCheck] = []
        for relative in context.artifacts:
            try:
                path = context.resolve(relative)
            except ValueError as exc:
                checks.append(
                    ValidationCheck(
                        "workspace_boundary",
                        relative,
                        False,
                        "fail",
                        defect=str(exc),
                    )
                )
                continue
            checks.extend(
                self.file_validator.validate(
                    path,
                    context=context,
                    requirements=requirements,
                )
            )
            if not path.is_file():
                continue
            for validator in self.validators:
                if validator.supports(path):
                    checks.extend(
                        validator.validate(
                            path,
                            context=context,
                            requirements=requirements,
                        )
                    )
        expected_artifacts = requirements.get("minimum_artifacts")
        if expected_artifacts:
            actual = len(context.artifacts)
            checks.append(
                ValidationCheck(
                    "artifact_count",
                    "artifacts",
                    actual >= int(expected_artifacts),
                    "pass" if actual >= int(expected_artifacts) else "fail",
                    evidence=f"actual={actual} minimum={expected_artifacts}",
                    defect=(
                        ""
                        if actual >= int(expected_artifacts)
                        else f"expected at least {expected_artifacts} artifacts, found {actual}"
                    ),
                )
            )
        checks.extend(_validate_citations(context, requirements))
        checks.extend(_validate_browser_outcome(context, requirements))
        failed = [check for check in checks if check.status == "fail"]
        unresolved = [check for check in checks if check.status == "unresolved"]
        passed = not failed and not unresolved
        return VerificationResult(
            status="pass" if passed else ("unresolved" if unresolved and not failed else "fail"),
            deterministic_passed=passed,
            semantic_status="unresolved",
            checks=[asdict(check) for check in checks],
            defects=[
                {
                    "validator": check.validator,
                    "artifact": check.target,
                    "problem": check.defect or check.status,
                }
                for check in failed + unresolved
            ],
            evidence=[check.evidence for check in checks if check.evidence],
        )


def compile_requirements(objective: str) -> dict[str, Any]:
    """Compile unambiguous numeric/media requirements from the user objective.

    Thin compatibility adapter (REL-011.2): delegates to
    ``Deterministic_Extractor.extract()`` and projects the typed
    ``Requirement`` list back into the pre-migration flat dict shape so any
    caller still depending on that dict contract keeps working unchanged.
    """
    from kageha.contract.extractor import DeterministicExtractor
    from kageha.contract.models import RequirementKind

    text = objective.lower()
    result = DeterministicExtractor().extract(objective)
    requirements: dict[str, Any] = {}
    for req in result.requirements:
        # First match wins per key, matching the original single-match
        # re.search() semantics this adapter replaces.
        if req.kind == RequirementKind.SLIDE_COUNT and "slides" not in requirements:
            requirements["slides"] = req.value
        elif req.kind == RequirementKind.PAGE_COUNT and "pdf_pages" not in requirements:
            requirements["pdf_pages"] = req.value
        elif req.kind == RequirementKind.CITATION:
            requirements["citations"] = True
        elif req.kind == RequirementKind.BROWSER_OUTCOME:
            requirements["browser_outcome"] = True
    if re.search(
        r"\b(create|make|generate|build|write|produce|save|export|diagram|"
        r"slides?|pdf|image|video|code|file)\b",
        text,
    ):
        requirements["minimum_artifacts"] = 1
    return requirements


_URL_RE = re.compile(r"https?://[^\s<>()\]\"']+")


def _validate_citations(
    context: ValidationContext,
    requirements: dict[str, Any],
) -> list[ValidationCheck]:
    if not requirements.get("citations"):
        return []
    urls: list[str] = []
    for relative in context.artifacts:
        path = context.resolve(relative)
        if path.suffix.lower() not in {
            ".md",
            ".txt",
            ".html",
            ".htm",
            ".json",
            ".yaml",
            ".yml",
        }:
            continue
        try:
            urls.extend(_URL_RE.findall(path.read_text(errors="replace")))
        except OSError:
            continue
    urls = list(dict.fromkeys(urls))
    if not urls:
        return [
            ValidationCheck(
                "citations",
                "artifacts",
                False,
                "fail",
                defect="the task requested sources/citations but no source URL was found",
            )
        ]
    reachable = 0
    failures: list[str] = []
    try:
        import httpx

        with httpx.Client(
            follow_redirects=True,
            timeout=8.0,
            headers={"User-Agent": "KagehaValidator/0.3"},
        ) as client:
            for url in urls[:20]:
                try:
                    response = client.head(url)
                    if response.status_code in {403, 405}:
                        response = client.get(url)
                    if response.status_code < 400:
                        reachable += 1
                    else:
                        failures.append(f"{url} ({response.status_code})")
                except httpx.HTTPError as exc:
                    failures.append(f"{url} ({type(exc).__name__})")
    except ImportError:
        return [
            ValidationCheck(
                "citations",
                "artifacts",
                False,
                "unresolved",
                defect="httpx is unavailable; citation reachability is unresolved",
            )
        ]
    ok = reachable == len(urls[:20])
    return [
        ValidationCheck(
            "citations",
            "artifacts",
            ok,
            "pass" if ok else "fail",
            evidence=f"urls={len(urls)} reachable={reachable}",
            defect="" if ok else "unreachable sources: " + "; ".join(failures[:5]),
            metadata={"urls": urls[:20], "reachable": reachable},
        )
    ]


def _validate_browser_outcome(
    context: ValidationContext,
    requirements: dict[str, Any],
) -> list[ValidationCheck]:
    if not requirements.get("browser_outcome"):
        return []
    screenshots = [
        relative
        for relative in context.artifacts
        if Path(relative).suffix.lower() in ImageValidator._suffixes
        and any(
            token in Path(relative).name.lower()
            for token in ("browse", "browser", "screen", "page")
        )
    ]
    ok = bool(screenshots)
    return [
        ValidationCheck(
            "browser_outcome",
            "artifacts",
            ok,
            "pass" if ok else "fail",
            evidence=f"screenshots={screenshots}",
            defect=(
                ""
                if ok
                else "requested browser navigation/screenshot has no screenshot artifact"
            ),
        )
    ]


def validate_result(
    *,
    objective: str,
    workspace: Path,
    artifacts: list[str],
    registry: ValidatorRegistry | None = None,
) -> VerificationResult:
    return (registry or ValidatorRegistry()).validate(
        ValidationContext(
            objective=objective,
            workspace=workspace,
            artifacts=tuple(artifacts),
        )
    )
