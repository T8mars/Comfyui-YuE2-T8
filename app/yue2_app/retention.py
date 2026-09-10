from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from .io import atomic_json, within

JOB_ID_PATTERN = re.compile(r"\d{8}-\d{6}-[0-9a-f]{8}")
TERMINAL = {"complete", "failed", "cancelled"}
DEFAULT_POLICY = {
    "enabled": True,
    "cleanup_interval_hours": 6,
    "jobs": {"max_age_days": 30, "max_count": 100, "max_bytes_gib": 100},
    "uploads": {"max_age_days": 7, "max_bytes_gib": 10},
    "logs": {"max_age_days": 30, "max_bytes_gib": 2},
}


def _merge_policy(value: dict) -> dict:
    result = json.loads(json.dumps(DEFAULT_POLICY))
    if not isinstance(value, dict):
        raise ValueError("retention.json 必须是 JSON 对象")
    for key in ("enabled", "cleanup_interval_hours"):
        if key in value:
            result[key] = value[key]
    for section in ("jobs", "uploads", "logs"):
        if section in value:
            if not isinstance(value[section], dict):
                raise ValueError(f"retention.json 的 {section} 必须是对象")
            result[section].update(value[section])
    if not isinstance(result["enabled"], bool):
        raise ValueError("retention.enabled 必须是布尔值")
    numeric = [result["cleanup_interval_hours"]]
    for section in ("jobs", "uploads", "logs"):
        numeric.extend(result[section].values())
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0 for item in numeric):
        raise ValueError("retention.json 的限制值必须是非负数字")
    if float(result["cleanup_interval_hours"]) <= 0:
        raise ValueError("retention.cleanup_interval_hours 必须大于 0")
    return result


def _tree_size(path: Path) -> int:
    if path.is_symlink():
        return path.lstat().st_size
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _select(items: list[dict], rules: dict, now: float, include_count: bool) -> dict[str, str]:
    selected: dict[str, str] = {}
    max_age = float(rules.get("max_age_days", 0)) * 86400
    if max_age > 0:
        for item in items:
            if now - item["timestamp"] > max_age:
                selected[item["key"]] = "age"
    remaining = [item for item in items if item["key"] not in selected]
    remaining.sort(key=lambda item: item["timestamp"])
    max_count = int(rules.get("max_count", 0)) if include_count else 0
    if max_count > 0 and len(remaining) > max_count:
        for item in remaining[:len(remaining) - max_count]:
            selected[item["key"]] = "count"
        remaining = [item for item in remaining if item["key"] not in selected]
    max_bytes = int(float(rules.get("max_bytes_gib", 0)) * 2**30)
    total = sum(item["bytes"] for item in remaining)
    if max_bytes > 0:
        for item in remaining:
            if total <= max_bytes:
                break
            selected[item["key"]] = "capacity"
            total -= item["bytes"]
    return selected


class RetentionManager:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config_path = self.root / "retention.json"
        if not self.config_path.exists():
            atomic_json(self.config_path, DEFAULT_POLICY)
        self.policy = self.load_policy()
        self.last_cleanup_monotonic = 0.0
        self.last_report: dict | None = None

    def load_policy(self) -> dict:
        return _merge_policy(json.loads(self.config_path.read_text(encoding="utf-8-sig")))

    def reload(self) -> dict:
        self.policy = self.load_policy()
        return self.policy

    def usage(self) -> dict:
        result = {}
        for name, path in {
                "jobs": self.root / "outputs" / "jobs",
                "uploads": self.root / "uploads",
                "logs": self.root / "logs",
                "exports": self.root / "exports",
        }.items():
            size = _tree_size(path)
            result[name] = {"bytes": size, "gib": round(size / 2**30, 3)}
        return result

    def status(self) -> dict:
        self.reload()
        return {"policy": self.policy, "usage": self.usage(), "last_cleanup": self.last_report}

    def cleanup(self, current_job: str | None = None, *, force: bool = False) -> dict:
        interval = float(self.policy["cleanup_interval_hours"]) * 3600
        elapsed = time.monotonic() - self.last_cleanup_monotonic
        if not force and self.last_cleanup_monotonic and elapsed < interval:
            return self.last_report or {"skipped": True, "reason": "interval"}
        self.last_cleanup_monotonic = time.monotonic()
        self.reload()
        before = self.usage()
        report = {"ran_at": time.time(), "deleted": {"jobs": [], "uploads": [], "logs": []},
                  "errors": [], "before": before}
        if not self.policy["enabled"]:
            report.update({"skipped": True, "reason": "disabled", "after": before})
            self.last_report = report
            return report

        now = time.time()
        jobs_root = self.root / "outputs" / "jobs"
        job_items = []
        for directory in (jobs_root.iterdir() if jobs_root.is_dir() else ()):
            if not directory.is_dir() or not JOB_ID_PATTERN.fullmatch(directory.name) or directory.name == current_job:
                continue
            try:
                status = json.loads((directory / "status.json").read_text(encoding="utf-8-sig"))
                if status.get("status") not in TERMINAL or status.get("id") != directory.name:
                    continue
                timestamp = float(status.get("finished_at") or status.get("updated_at") or directory.stat().st_mtime)
                job_items.append({"key": directory.name, "path": directory, "timestamp": timestamp,
                                  "bytes": _tree_size(directory)})
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        selected_jobs = _select(job_items, self.policy["jobs"], now, include_count=True)
        for item in job_items:
            if item["key"] not in selected_jobs:
                continue
            try:
                target = within(jobs_root, item["path"])
                if target.is_symlink():
                    target.unlink()
                else:
                    shutil.rmtree(target)
                report["deleted"]["jobs"].append({"id": item["key"], "reason": selected_jobs[item["key"]],
                                                    "bytes": item["bytes"]})
                job_log = self.root / "logs" / f"{item['key']}.log"
                if job_log.is_file() and not job_log.is_symlink():
                    try:
                        job_log.unlink()
                    except OSError:
                        pass
            except (OSError, ValueError) as exc:
                report["errors"].append({"path": str(item["path"]), "error": str(exc)})

        excluded_logs = {"server.log", "server.stdout.log", "server.stderr.log"}
        if current_job:
            excluded_logs.add(f"{current_job}.log")
        for category, directory, excluded in (
            ("uploads", self.root / "uploads", set()),
            ("logs", self.root / "logs", excluded_logs),
        ):
            items = []
            for path in (directory.iterdir() if directory.is_dir() else ()):
                if not path.is_file() or path.is_symlink() or path.name in excluded:
                    continue
                try:
                    stat = path.stat()
                    items.append({"key": path.name, "path": path, "timestamp": stat.st_mtime,
                                  "bytes": stat.st_size})
                except OSError:
                    continue
            selected = _select(items, self.policy[category], now, include_count=False)
            for item in items:
                if item["key"] not in selected:
                    continue
                try:
                    within(directory, item["path"]).unlink()
                    report["deleted"][category].append({"name": item["key"], "reason": selected[item["key"]],
                                                        "bytes": item["bytes"]})
                except (OSError, ValueError) as exc:
                    report["errors"].append({"path": str(item["path"]), "error": str(exc)})
        report["after"] = self.usage()
        self.last_report = report
        return report
