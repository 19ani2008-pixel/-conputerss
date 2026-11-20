import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from analyzer import analyze_source, detect_language, validate_patch
from analyzer.ai_repair import analyze_with_ai


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def create_job(files: list[dict[str, str]]) -> str:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0,
            "steps": [
                {"name": "Receive project", "status": "pending"},
                {"name": "Scan source files", "status": "pending"},
                {"name": "Generate AI repairs", "status": "pending"},
                {"name": "Test in Docker sandbox", "status": "pending"},
                {"name": "Prepare review", "status": "pending"}
            ],
            "files": [],
            "error": None
        }

    thread = threading.Thread(
        target=_run_project_job,
        args=(job_id, files),
        daemon=True
    )
    thread.start()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _set_step(job_id: str, index: int, status: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job["steps"][index]["status"] = status
        job["progress"] = (index + (1 if status == "complete" else 0)) * 20
        if status == "running":
            job["status"] = "running"


def _set_error(job_id: str, message: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "failed"
        job["error"] = message


def _safe_project_path(root: Path, relative_name: str) -> Path:
    relative = Path(relative_name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe project path: {relative_name}")
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _sandbox_skipped() -> dict[str, Any]:
    return {
        "available": False,
        "skipped": True,
        "passed": None,
        "message": (
            "Not run: sandbox execution supports Python projects only. "
            "Other languages are checked with parser-based syntax validation."
        ),
        "output": ""
    }


def _run_sandbox(project_root: Path, command: list[str]) -> dict[str, Any]:
    if not _docker_available():
        return {
            "available": False,
            "passed": False,
            "message": "Docker is unavailable; no untrusted code was executed.",
            "output": ""
        }

    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", "256m",
                "--cpus", "1",
                "--pids-limit", "64",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
                "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache",
                "-v", f"{project_root}:/work:ro",
                "-w", "/work",
                "python:3.11-slim",
                *command
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False
        )
        return {
            "available": True,
            "passed": result.returncode == 0,
            "message": "Sandbox check passed." if result.returncode == 0 else "Sandbox check failed.",
            "output": (result.stdout + result.stderr).strip()[-12000:]
        }
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "passed": False,
            "message": "Sandbox test timed out.",
            "output": ""
        }


def _run_project_job(job_id: str, files: list[dict[str, str]]) -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bugradar-project-"))

    try:
        with _jobs_lock:
            _jobs[job_id]["status"] = "running"

        _set_step(job_id, 0, "running")
        project_root = temp_root / "project"
        project_root.mkdir()

        source_files: list[tuple[str, str, str]] = []
        has_python = False
        for uploaded in files:
            name = uploaded["name"].replace("\\", "/").lstrip("/")
            content = uploaded["content"]
            target = _safe_project_path(project_root, name)
            target.write_text(content, encoding="utf-8")
            language = detect_language(name, content)
            if language:
                source_files.append((name, content, language.name))
                has_python = has_python or language.name == "Python"

        _set_step(job_id, 0, "complete")
        _set_step(job_id, 1, "running")

        file_results: list[dict[str, Any]] = []
        for name, source, language in source_files:
            findings, engine = analyze_source(source, language)
            file_results.append({
                "name": name,
                "language": language,
                "engine": engine,
                "findings": findings,
                "repairs": []
            })

        _set_step(job_id, 1, "complete")
        _set_step(job_id, 2, "running")

        # Repair each finding group through one contextual AI request per file.
        for file_result in file_results:
            source = (project_root / file_result["name"]).read_text(encoding="utf-8")
            if not file_result["findings"]:
                file_result["repairs"] = []
                continue

            try:
                result = analyze_with_ai(
                    source,
                    file_result["findings"],
                    file_result["language"],
                    has_static_analysis=file_result["engine"] != "none",
                )
                validation, patch = validate_patch(
                    result.get("suggested_patch", ""),
                    file_result["language"]
                )
                # Only a repair that passed its syntax check, or one that
                # cannot be checked for this language, is offered.
                patch_valid = bool(patch) and validation["valid"] is not False
                validation_message = validation["message"]
                if validation["valid"] is False:
                    patch = ""

                file_result["repairs"].append({
                    "summary": result.get("summary", ""),
                    "root_cause": result.get("root_cause", ""),
                    "impact": result.get("impact", ""),
                    "explanation": result.get("explanation", ""),
                    "confidence": result.get("confidence", "Low"),
                    "suggested_patch": patch,
                    "patch_valid": patch_valid,
                    "patch_checked": validation["valid"] is True,
                    "patch_validation": validation_message
                })
            except Exception as error:
                file_result["repairs"].append({
                    "summary": "AI repair unavailable.",
                    "root_cause": "",
                    "impact": "",
                    "explanation": str(error),
                    "confidence": "Low",
                    "suggested_patch": "",
                    "patch_valid": False,
                    "patch_checked": False,
                    "patch_validation": "The AI repair could not be generated."
                })

        _set_step(job_id, 2, "complete")
        _set_step(job_id, 3, "running")

        compile_cmd = ["python", "-m", "compileall", "-q", "."]
        unittest_cmd = ["python", "-m", "unittest", "discover", "-s", ".", "-p", "*test*.py"]

        def run_checks() -> tuple[dict[str, Any], dict[str, Any]]:
            if not has_python:
                return _sandbox_skipped(), _sandbox_skipped()
            return (
                _run_sandbox(project_root, compile_cmd),
                _run_sandbox(project_root, unittest_cmd)
            )

        compile_before, unittest_before = run_checks()

        for file_result in file_results:
            if not file_result["repairs"]:
                continue
            repair = file_result["repairs"][0]
            if repair["patch_valid"]:
                target = _safe_project_path(project_root, file_result["name"])
                target.write_text(repair["suggested_patch"], encoding="utf-8")

        compile_after, unittest_after = run_checks()

        _set_step(job_id, 3, "complete")
        _set_step(job_id, 4, "running")

        with _jobs_lock:
            job = _jobs[job_id]
            job["status"] = "complete"
            job["progress"] = 100
            job["files"] = file_results
            job["tests"] = {
                "compile_before": compile_before,
                "unittest_before": unittest_before,
                "compile_after": compile_after,
                "unittest_after": unittest_after
            }
            job["steps"][4]["status"] = "complete"

    except Exception as error:
        _set_error(job_id, str(error))
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
