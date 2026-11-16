"""Read back CVE control evidence and require a genuine 1.0/pass combined smoke."""

import argparse
import hashlib
import json
import os
from urllib.parse import urlparse

from minio import Minio
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from abevalflow.db.engine import get_engine
from abevalflow.db.models import MCPCheckerRun, MCPCheckerTask


def verify(positive, negative):
    endpoint = urlparse(os.environ["MINIO_ENDPOINT"])
    storage = Minio(
        endpoint.netloc or endpoint.path,
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=endpoint.scheme == "https",
    )

    def read(key):
        response = storage.get_object("ab-eval-reports", key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    from abevalflow.mcp_ci import LABEL, Cluster

    cluster = Cluster(os.environ.get("POD_NAMESPACE", "ab-eval-flow"))
    results = []
    with Session(get_engine(os.environ["DATABASE_URL"])) as session:
        for uid, expected in [(positive, 1.0), (negative, 0.0)]:
            prefix = f"mcp-runs/{uid}"
            manifest = json.loads(read(prefix + "/archive-complete.json"))
            assert manifest["uid"] == uid and manifest["complete"] is True
            downloaded = {}
            for obj in manifest["objects"]:
                content = read(obj["key"])
                assert len(content) == obj["bytes"]
                assert hashlib.sha256(content).hexdigest() == obj["sha256"]
                downloaded[obj["key"]] = content

            def evidence(suffix):
                matches = [value for key, value in downloaded.items() if key.endswith(suffix)]
                assert len(matches) == 1, f"Required evidence missing/ambiguous: {suffix}"
                return json.loads(matches[0])

            process = evidence("/process-status.json")
            assert process["uid"] == uid and process["timed_out"] is False
            assert process["exit_code"] in {0, 1}
            assert process["agent_model"] == process["judge_model"] == "openai:claude-sonnet"
            readiness = evidence("/readiness.json")
            assert readiness["ready"] is True and "get_cve_by_id" in readiness["tools"]
            target = evidence("/target.json")
            assert target["uid"] == uid and "@sha256:" in target["descriptor"]["image"]
            assert any(key.endswith("/" + target["name"] + ".log") for key in downloaded)
            status = evidence("/pod-status/" + target["name"] + ".json")
            actual_image = status["status"]["container_statuses"][0]["image_id"]
            assert actual_image.split("@")[-1] == target["descriptor"]["image"].split("@")[-1]
            evidence("/context.json")
            assert any(key.endswith("/mcpchecker-process.log") for key in downloaded)
            report_keys = [key for key in downloaded if key.endswith("/mcpchecker-report.json")]
            native_keys = [key for key in downloaded if key.endswith("/mcpchecker-out.json")]
            assert len(report_keys) == len(native_keys) == 1
            report = json.loads(downloaded[report_keys[0]])
            native = json.loads(downloaded[native_keys[0]])["results"]
            assert report["overall_score"] == expected
            assert report["recommendation"] == ("pass" if expected else "fail")
            assert report["error_tasks"] == report["skipped_tasks"] == 0
            assert len(native) == 1 and native[0]["taskPassed"] is True
            assert not any(
                native[0].get(key) for key in ["timedOut", "taskError", "agentExecutionError", "taskJudgeError"]
            )
            task = report["tasks"][0]
            calls = task["tool_call_records"]
            assert calls and all(call["success"] for call in calls)
            assert any(
                call["tool_name"] == "get_cve_by_id" and call["arguments"] == {"cve_id": "CVE-2016-10739"}
                for call in calls
            )
            assert task["llm_judge_results"] and all(j["passed"] for j in task["llm_judge_results"])
            assert task["assertion_results"]["minToolCalls"]["passed"] is bool(expected)
            logs = b"\n".join(value for key, value in downloaded.items() if "/pod-logs/mcp-" in key)
            assert b'"event": "tool_call"' in logs and b'"success": true' in logs
            runs = session.scalars(select(MCPCheckerRun).where(MCPCheckerRun.pipeline_run_id == uid)).all()
            assert len(runs) == 1 and runs[0].overall_score == expected
            count = session.scalar(
                select(func.count()).select_from(MCPCheckerTask).where(MCPCheckerTask.run_id == runs[0].id)
            )
            assert count == 1
            assert json.loads(read(prefix + "/cleanup.json"))["deleted"] is True
            for kind in ["Pod", "Service", "NetworkPolicy"]:
                assert not cluster.list(kind, f"{LABEL}={uid}"), f"Remaining managed {kind}"
            assert not cluster.core.list_namespaced_persistent_volume_claim(
                cluster.namespace, label_selector=f"{LABEL}={uid}"
            ).items, "Evidence PVC not deleted"
            results.append({"uid": uid, "score": expected, "artifacts_checked": len(downloaded)})
    return {"overall_score": 1.0, "recommendation": "pass", "controls": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positive", required=True)
    parser.add_argument("--negative", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.positive, args.negative), indent=2))


if __name__ == "__main__":
    main()
