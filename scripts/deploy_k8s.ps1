[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet("validate", "render", "dry-run", "apply", "status", "rollback")]
    [string]$Action = "validate",

    [ValidateSet("base", "dev")]
    [string]$Overlay = "dev",

    [ValidateSet("all", "backend", "ui", "mcp-server", "phoenix")]
    [string]$Component = "all",

    [switch]$SkipSecretUpdate,

    [ValidateRange(30, 3600)]
    [int]$TimeoutSeconds = 600
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Namespace = "british-museum-agent"
$SecretName = "british-museum-secrets"
$KustomizePath = if ($Overlay -eq "base") {
    Join-Path $ProjectRoot "deploy\base"
} else {
    Join-Path $ProjectRoot "deploy\overlays\dev"
}
$NamespaceManifest = Join-Path $ProjectRoot "deploy\base\namespace.yaml"

function Get-ExecutablePath {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required executable '$Name' was not found on PATH."
    }
    return $command.Source
}

function Get-PythonPath {
    $localPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $localPython) {
        return $localPython
    }
    return Get-ExecutablePath -Name "python"
}

function Invoke-Kubectl {
    param([Parameter(Mandatory = $true)][string[]]$KubectlArgs)

    & $script:Kubectl @KubectlArgs
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl failed with exit code $LASTEXITCODE: $($KubectlArgs -join ' ')"
    }
}

function Invoke-LocalValidation {
    $python = Get-PythonPath
    $validator = Join-Path $ProjectRoot "scripts\validate_k8s.py"
    & $python $validator --root $ProjectRoot --kubectl-dry-run auto
    if ($LASTEXITCODE -ne 0) {
        throw "Kubernetes deployment validation failed."
    }
}

function Get-RequiredSecretValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$MinimumLength
    )

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Set the process environment variable $Name before applying, or use -SkipSecretUpdate with a pre-created Secret."
    }
    if ($value.StartsWith("REPLACE_ME", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name still contains a template placeholder."
    }
    if ($value.Length -lt $MinimumLength) {
        throw "$Name must contain at least $MinimumLength characters."
    }
    return $value
}

function Set-KubernetesSecret {
    $stringData = [ordered]@{
        STAFF_DEMO_PASSWORD = Get-RequiredSecretValue -Name "STAFF_DEMO_PASSWORD" -MinimumLength 12
        JWT_SECRET = Get-RequiredSecretValue -Name "JWT_SECRET" -MinimumLength 32
        MCP_INTERNAL_TOKEN = Get-RequiredSecretValue -Name "MCP_INTERNAL_TOKEN" -MinimumLength 32
    }

    foreach ($optionalName in @("GOOGLE_API_KEY", "GEMINI_API_KEY", "LANGSMITH_API_KEY")) {
        $optionalValue = [Environment]::GetEnvironmentVariable($optionalName, "Process")
        if (-not [string]::IsNullOrWhiteSpace($optionalValue)) {
            if ($optionalValue.StartsWith("REPLACE_ME", [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "$optionalName still contains a template placeholder."
            }
            $stringData[$optionalName] = $optionalValue
        }
    }

    $secret = [ordered]@{
        apiVersion = "v1"
        kind = "Secret"
        metadata = [ordered]@{
            name = $SecretName
            namespace = $Namespace
            labels = [ordered]@{
                "app.kubernetes.io/part-of" = "british-museum-agent"
            }
        }
        type = "Opaque"
        stringData = $stringData
    }

    $secretJson = $secret | ConvertTo-Json -Depth 8 -Compress
    $secretJson | & $script:Kubectl apply -f -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create or update Secret '$SecretName'."
    }
}

function Assert-KubernetesSecretExists {
    & $script:Kubectl get secret $SecretName --namespace $Namespace --output name | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Secret '$SecretName' does not exist in namespace '$Namespace'."
    }
}

function Show-Status {
    Invoke-Kubectl -KubectlArgs @(
        "get",
        "pods,deployments,services,persistentvolumeclaims,horizontalpodautoscalers",
        "--namespace", $Namespace,
        "--output", "wide"
    )
}

function Wait-ForRollouts {
    foreach ($deployment in @("mcp-server", "phoenix", "backend", "ui")) {
        Invoke-Kubectl -KubectlArgs @(
            "rollout", "status", "deployment/$deployment",
            "--namespace", $Namespace,
            "--timeout", "${TimeoutSeconds}s"
        )
    }
}

function Invoke-Rollback {
    $deployments = if ($Component -eq "all") {
        @("ui", "backend", "mcp-server", "phoenix")
    } else {
        @($Component)
    }

    foreach ($deployment in $deployments) {
        Invoke-Kubectl -KubectlArgs @("rollout", "history", "deployment/$deployment", "--namespace", $Namespace)
        if ($PSCmdlet.ShouldProcess("deployment/$deployment in namespace $Namespace", "Roll back to the previous revision")) {
            Invoke-Kubectl -KubectlArgs @("rollout", "undo", "deployment/$deployment", "--namespace", $Namespace)
            Invoke-Kubectl -KubectlArgs @(
                "rollout", "status", "deployment/$deployment",
                "--namespace", $Namespace,
                "--timeout", "${TimeoutSeconds}s"
            )
        }
    }
}

if (-not (Test-Path -LiteralPath $KustomizePath -PathType Container)) {
    throw "Kustomize path does not exist: $KustomizePath"
}

if ($Action -in @("validate", "render", "dry-run", "apply")) {
    Invoke-LocalValidation
}

if ($Action -eq "validate") {
    return
}

$script:Kubectl = Get-ExecutablePath -Name "kubectl"

switch ($Action) {
    "render" {
        Invoke-Kubectl -KubectlArgs @("kustomize", $KustomizePath)
    }
    "dry-run" {
        Invoke-Kubectl -KubectlArgs @("apply", "--dry-run=client", "--validate=false", "-k", $KustomizePath)
    }
    "apply" {
        if ($PSCmdlet.ShouldProcess("namespace $Namespace", "Apply Kubernetes deployment from $KustomizePath")) {
            Invoke-Kubectl -KubectlArgs @("apply", "-f", $NamespaceManifest)
            if ($SkipSecretUpdate) {
                Assert-KubernetesSecretExists
            } else {
                Set-KubernetesSecret
            }
            Invoke-Kubectl -KubectlArgs @("apply", "-k", $KustomizePath)
            Wait-ForRollouts
            Show-Status
        }
    }
    "status" {
        Show-Status
    }
    "rollback" {
        Invoke-Rollback
    }
}
