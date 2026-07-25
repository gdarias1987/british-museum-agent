from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


EXPECTED_BASE_RESOURCES = {
    "namespace.yaml",
    "configmap.yaml",
    "pvc.yaml",
    "backend.yaml",
    "ui.yaml",
    "mcp-server.yaml",
    "phoenix.yaml",
    "hpa.yaml",
    "network-policies.yaml",
}
REQUIRED_SECRET_KEYS = {
    "STAFF_DEMO_PASSWORD",
    "JWT_SECRET",
    "MCP_INTERNAL_TOKEN",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "LANGSMITH_API_KEY",
}
REQUIRED_PVCS = {"chroma-data", "sqlite-data", "huggingface-cache", "phoenix-data"}
COMPONENTS = {"backend", "ui", "mcp-server", "phoenix"}
PLACEHOLDER_PATTERN = re.compile(r"(?i)(replace_me|change[_-]?me|<[^>]+>)")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: int = 0

    def check(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def read_text(path: Path, report: Report) -> str:
    if not path.is_file():
        report.errors.append(f"Missing required file: {path}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        report.errors.append(f"File is not valid UTF-8: {path}")
        return ""


def yaml_documents(text: str) -> list[str]:
    return [document.strip() for document in re.split(r"(?m)^---\s*$", text) if document.strip()]


def document_identity(document: str) -> tuple[str, str] | None:
    kind_match = re.search(r"(?m)^kind:\s*([^\s#]+)", document)
    metadata_match = re.search(r"(?ms)^metadata:\s*\n(?P<body>(?:^[ \t]+.*\n?)*)", document)
    if not kind_match or not metadata_match:
        return None
    name_match = re.search(r"(?m)^\s{2}name:\s*([^\s#]+)", metadata_match.group("body"))
    if not name_match:
        return None
    return kind_match.group(1), name_match.group(1)


def manifest_index(base: Path, report: Report) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for path in sorted(base.glob("*.yaml")):
        if path.name in {"kustomization.yaml", "secret.example.yaml"}:
            continue
        for document in yaml_documents(read_text(path, report)):
            identity = document_identity(document)
            if identity is None:
                report.errors.append(f"Cannot determine kind/name in {path.name}")
                continue
            if identity in index:
                report.errors.append(f"Duplicate Kubernetes object {identity[0]}/{identity[1]}")
            index[identity] = document
    return index


def extract_resource_list(kustomization: str) -> set[str]:
    resources: set[str] = set()
    in_resources = False
    for line in kustomization.splitlines():
        if line.strip() == "resources:":
            in_resources = True
            continue
        if in_resources and re.match(r"^[A-Za-z]", line):
            break
        match = re.match(r"^\s+-\s+([^#\s]+)", line) if in_resources else None
        if match:
            resources.add(match.group(1))
    return resources


def require_tokens(report: Report, label: str, text: str, tokens: list[str]) -> None:
    for token in tokens:
        report.check(token in text, f"{label} must contain {token!r}")


def validate_static(root: Path, report: Report) -> None:
    base = root / "deploy" / "base"
    overlay = root / "deploy" / "overlays" / "dev"
    required_files = [
        base / "kustomization.yaml",
        base / "namespace.yaml",
        base / "configmap.yaml",
        base / "secret.example.yaml",
        base / "pvc.yaml",
        base / "backend.yaml",
        base / "ui.yaml",
        base / "mcp-server.yaml",
        base / "phoenix.yaml",
        base / "hpa.yaml",
        base / "network-policies.yaml",
        overlay / "kustomization.yaml",
        root / "scripts" / "deploy_k8s.ps1",
        root / "scripts" / "validate_k8s.py",
        root / "docs" / "deployment.md",
        root / "Makefile",
    ]
    for path in required_files:
        report.check(path.is_file(), f"Missing required file: {path.relative_to(root)}")

    kustomization = read_text(base / "kustomization.yaml", report)
    resources = extract_resource_list(kustomization)
    report.check(resources == EXPECTED_BASE_RESOURCES, "Base kustomization resources do not match the expected deployment set")
    report.check("secret.example.yaml" not in resources, "Secret example must never be included in the base kustomization")
    require_tokens(
        report,
        "base kustomization",
        kustomization,
        ["kind: Kustomization", "namespace: british-museum-agent"],
    )

    overlay_kustomization = read_text(overlay / "kustomization.yaml", report)
    require_tokens(
        report,
        "dev overlay",
        overlay_kustomization,
        ["kind: Kustomization", "../../base", "configmap-patch.yaml", "ui-patch.yaml", "ui-hpa-patch.yaml"],
    )

    secret_example = read_text(base / "secret.example.yaml", report)
    # `metadata:` contains the substring `data:`; anchor the check to a YAML
    # top-level key instead of using a raw substring search.
    has_string_data = bool(re.search(r"(?m)^stringData:\s*$", secret_example))
    has_base64_data = bool(re.search(r"(?m)^data:\s*$", secret_example))
    report.check(has_string_data and not has_base64_data, "Secret example must use stringData and must not contain base64 data")
    for key in REQUIRED_SECRET_KEYS:
        match = re.search(rf"(?m)^\s{{2}}{re.escape(key)}:\s*(\S+)\s*$", secret_example)
        report.check(bool(match), f"Secret example is missing key {key}")
        if match:
            report.check(match.group(1).startswith("REPLACE_ME"), f"Secret example key {key} must remain an explicit placeholder")

    for path in sorted((root / "deploy").rglob("*.yaml")):
        if path == base / "secret.example.yaml":
            continue
        text = read_text(path, report)
        report.check(not PLACEHOLDER_PATTERN.search(text), f"Unresolved placeholder outside the Secret example: {path.relative_to(root)}")

    index = manifest_index(base, report)
    for component in COMPONENTS:
        report.check(("Deployment", component) in index, f"Missing Deployment/{component}")
        report.check(("Service", component) in index, f"Missing Service/{component}")

    for pvc in REQUIRED_PVCS:
        document = index.get(("PersistentVolumeClaim", pvc), "")
        report.check(bool(document), f"Missing PersistentVolumeClaim/{pvc}")
        require_tokens(report, f"PVC/{pvc}", document, ["ReadWriteOnce", "requests:", "storage:"])

    probe_contracts = {
        "backend": ["startupProbe:", "readinessProbe:", "path: /api/v1/health", "livenessProbe:", "tcpSocket:", "containerPort: 8000"],
        "ui": ["startupProbe:", "readinessProbe:", "path: /_stcore/health", "livenessProbe:", "containerPort: 8501"],
        "mcp-server": ["startupProbe:", "readinessProbe:", "path: /health", "livenessProbe:", "tcpSocket:", "containerPort: 8001"],
        "phoenix": ["startupProbe:", "readinessProbe:", "livenessProbe:", "tcpSocket:", "containerPort: 6006"],
    }
    for component, tokens in probe_contracts.items():
        deployment = index.get(("Deployment", component), "")
        require_tokens(report, f"Deployment/{component}", deployment, tokens)
        require_tokens(
            report,
            f"Deployment/{component} security/resources",
            deployment,
            [
                "requests:",
                "limits:",
                "runAsNonRoot: true",
                "automountServiceAccountToken: false",
                "allowPrivilegeEscalation: false",
                "readOnlyRootFilesystem: true",
                "type: RuntimeDefault",
                "drop:",
                "- ALL",
            ],
        )

    for component in ("backend", "mcp-server", "phoenix"):
        deployment = index.get(("Deployment", component), "")
        require_tokens(report, f"Deployment/{component} stateful guard", deployment, ["replicas: 1", "type: Recreate"])

    backend_hpa = index.get(("HorizontalPodAutoscaler", "backend"), "")
    ui_hpa = index.get(("HorizontalPodAutoscaler", "ui"), "")
    require_tokens(report, "backend HPA", backend_hpa, ["apiVersion: autoscaling/v2", "minReplicas: 1", "maxReplicas: 1", "averageUtilization: 70"])
    require_tokens(report, "UI HPA", ui_hpa, ["apiVersion: autoscaling/v2", "minReplicas: 2", "maxReplicas: 5", "averageUtilization: 70"])

    network_policies = read_text(base / "network-policies.yaml", report)
    require_tokens(
        report,
        "NetworkPolicies",
        network_policies,
        [
            "name: default-deny",
            "name: ui-traffic",
            "name: backend-traffic",
            "name: mcp-server-traffic",
            "name: phoenix-traffic",
            "port: 8501",
            "port: 8000",
            "port: 8001",
            "port: 6006",
            "port: 443",
        ],
    )

    deploy_script = read_text(root / "scripts" / "deploy_k8s.ps1", report)
    require_tokens(
        report,
        "PowerShell deploy script",
        deploy_script,
        [
            '"validate", "render", "dry-run", "apply", "status", "rollback"',
            "--dry-run=client",
            "ConvertTo-Json",
            "rollout\", \"undo",
            "rollout\", \"status",
            "SkipSecretUpdate",
        ],
    )

    makefile = read_text(root / "Makefile", report)
    for target in ("k8s-validate", "k8s-render", "k8s-deploy", "k8s-status"):
        report.check(bool(re.search(rf"(?m)^{re.escape(target)}:\s*$", makefile)), f"Makefile is missing target {target}")

    docs = read_text(root / "docs" / "deployment.md", report).lower()
    for topic in (
        "docker compose",
        "kubernetes",
        "metrics server",
        "readwriteonce",
        "secret",
        "rollback",
        "networkpolicy",
        "sqlite",
        "phoenix",
    ):
        report.check(topic in docs, f"Deployment documentation must explain {topic}")

    phoenix = index.get(("Deployment", "phoenix"), "")
    if "arizephoenix/phoenix:latest" in phoenix:
        report.warn("Phoenix uses a mutable :latest tag; pin a tested version or digest before production rollout")


def run_kubectl_validation(root: Path, mode: str, report: Report) -> None:
    if mode == "skip":
        return
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        message = "kubectl is not installed; skipped Kustomize render and client dry-run"
        if mode == "required":
            report.errors.append(message)
        else:
            report.warn(message)
        return

    for relative in (Path("deploy/base"), Path("deploy/overlays/dev")):
        target = root / relative
        render = subprocess.run(
            [kubectl, "kustomize", str(target)],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if render.returncode != 0:
            report.errors.append(f"kubectl kustomize failed for {relative}: {render.stderr.strip()}")
            continue
        report.check(bool(render.stdout.strip()), f"kubectl kustomize produced no output for {relative}")

        dry_run = subprocess.run(
            [kubectl, "apply", "--dry-run=client", "--validate=false", "-f", "-"],
            cwd=root,
            text=True,
            input=render.stdout,
            capture_output=True,
            check=False,
        )
        if dry_run.returncode != 0:
            message = f"kubectl client dry-run failed for {relative}: {dry_run.stderr.strip()}"
            if mode == "required":
                report.errors.append(message)
            else:
                report.warn(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the Kubernetes deployment without third-party Python packages.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Project root")
    parser.add_argument(
        "--kubectl-dry-run",
        choices=("auto", "required", "skip"),
        default="auto",
        help="Run kubectl kustomize and client dry-run when available",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    report = Report()
    validate_static(root, report)
    run_kubectl_validation(root, args.kubectl_dry_run, report)

    for warning in report.warnings:
        print(f"[WARN] {warning}")
    for error in report.errors:
        print(f"[ERROR] {error}")

    if report.errors:
        print(f"Validation failed: {len(report.errors)} error(s), {len(report.warnings)} warning(s), {report.checks} checks.")
        return 1

    print(f"Validation passed: {report.checks} checks, {len(report.warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
