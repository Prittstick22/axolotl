"""Read-only demo preflight. Standard library only; never starts or approves a run."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

MANUAL_CHECKS = [
    "Check recorded baseline Python 2.7 and candidate Python 3.14 image digests; "
    "the default runner health check does not check both chess images.",
    "Inspect the real model patch, final verification and downloadable artifacts.",
    "Check search results, quota, microphone, recording labels and upload access.",
    "Complete two human rehearsals and Dev 1/2 factual review.",
]


def inspect_demo(get, repository_id=None, run_id=None):
    """Use an injected GET client so offline tests never contact services."""
    checks = []

    def check(name, passed):
        checks.append({"check": name, "passed": bool(passed)})

    def fetch(path, expected_type=dict):
        try:
            value = get(path)
            if not isinstance(value, expected_type):
                raise ValueError("unexpected response shape")
            return value
        except (HTTPError, URLError, OSError, ValueError, TimeoutError):
            # Do not print response bodies, connection strings or server diagnostics.
            check("GET " + path + " returned usable JSON", False)
            return expected_type()

    status = fetch("/system/status")
    check("MongoDB reachable", status.get("mongodb") == "ok")
    indexes = status.get("search_indexes") or {}
    check("Both Atlas search indexes ready", isinstance(indexes, dict)
          and all(indexes.get(k) == "ok" for k in ("keyword", "vector")))
    runner = status.get("runner") or {}
    check("Default trusted Docker runner available", isinstance(runner, dict)
          and runner.get("mode") == "trusted_docker" and runner.get("available") is True)
    gemini = status.get("gemini") or {}
    check("Configured Gemini model reachable", isinstance(gemini, dict)
          and gemini.get("reachable") is True and bool(gemini.get("model")))
    targets = fetch("/migration-targets", list)
    check("Python 2-to-3 target enabled", any(
        isinstance(t, dict) and t.get("target_id") == "python2_to_3"
        and t.get("supported") is True and t.get("target_python") == "3.14"
        for t in targets
    ))

    check("Chess repository ID supplied", bool(repository_id))
    if repository_id:
        path = "/repositories/" + quote(repository_id, safe="")
        repo = fetch(path)
        check("Repository is trusted chess", repo.get("executable") is True
              and repo.get("demo_fixture_id") == "chess"
              and repo.get("repository_id") == repository_id)
        check("Static analysis complete", repo.get("analysis_status") == "ok")
        index = fetch(path + "/index")
        total, embedded = index.get("total"), index.get("embedded")
        check("Chess embeddings complete", index.get("state") == "ready"
              and type(total) is int and total > 0
              and type(embedded) is int and embedded == total)

    check("Real completed run ID supplied", bool(run_id))
    if run_id:
        path = "/runs/" + quote(run_id, safe="")
        run = fetch(path)
        check("Run completed for this repository", bool(repository_id)
              and run.get("repository_id") == repository_id and run.get("run_id") == run_id
              and run.get("state") == "completed" and run.get("runner_mode") == "trusted_docker")
        plan_id = run.get("plan_id")
        plan = fetch("/plans/" + quote(plan_id, safe="")) if isinstance(plan_id, str) else {}
        check("Run used an approved chess target plan", plan.get("target_id") == "python2_to_3"
              and plan.get("plan_id") == plan_id
              and plan.get("status") in {"approved", "superseded"}
              and bool(plan.get("approved_at"))
              and plan.get("repository_id") == repository_id)
        report = fetch(path + "/report")
        check("Completed report with final verification exists", report.get("run_id") == run_id
              and report.get("verdict") == "completed"
              and bool(report.get("final_verification_id")))
        artifacts = fetch(path + "/artefacts", list)
        patch_hash = report.get("final_artefact_hash")
        check("Recorded patch matches report hash", bool(patch_hash) and any(
            isinstance(a, dict) and a.get("kind") == "patch" and a.get("sha256") == patch_hash
            and a.get("artefact_id") in (report.get("artefact_ids") or [])
            for a in artifacts
        ))

    return {"checked_at": datetime.now(UTC).isoformat(),
            "automated_checks_passed": all(c["passed"] for c in checks),
            "checks": checks, "manual_checks_still_required": MANUAL_CHECKS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000/api/v1")
    parser.add_argument("--repository")
    parser.add_argument("--run")
    parser.add_argument("--output", type=Path, help="Optional JSON evidence file")
    args = parser.parse_args(argv)
    parsed = urlsplit(args.api)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        parser.error("--api must be an HTTP(S) API base URL without credentials, query or fragment")

    def get(path):
        with urlopen(args.api.rstrip("/") + path, timeout=10) as response:
            return json.load(response)

    result = inspect_demo(get, args.repository, args.run)
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["automated_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
