[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",

    [ValidateSet("base", "dev")]
    [string]$Overlay = "dev",

    [switch]$SkipBuild,

    [ValidateRange(1024, 65535)]
    [int]$BackendPort = 18000,

    [ValidateRange(1024, 65535)]
    [int]$UiPort = 18501,

    [ValidateRange(1024, 65535)]
    [int]$PhoenixPort = 16006,

    [ValidateRange(60, 3600)]
    [int]$TimeoutSeconds = 900
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Namespace = "british-museum-agent"
$RuntimeDirectory = Join-Path $ProjectRoot "data\runtime"
$StatePath = Join-Path $RuntimeDirectory "k8s-manager.json"
$DeployScript = Join-Path $PSScriptRoot "deploy_k8s.ps1"
$EnvPath = Join-Path $ProjectRoot ".env"

function Get-ExecutablePath {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required executable '$Name' was not found on PATH."
    }
    return $command.Source
}

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable $($Arguments -join ' ')"
    }
}

function Import-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw ".env was not found at $Path. Copy .env.example and configure it first."
    }

    $loaded = 0
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            continue
        }

        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        [Environment]::SetEnvironmentVariable($name, $value, "Process")
        $loaded++
    }

    Write-Host "Loaded $loaded variables from .env (values hidden)."
}

function Assert-RequiredSecrets {
    foreach ($requirement in @(
        @{ Name = "STAFF_DEMO_PASSWORD"; MinimumLength = 12 },
        @{ Name = "JWT_SECRET"; MinimumLength = 32 },
        @{ Name = "MCP_INTERNAL_TOKEN"; MinimumLength = 32 }
    )) {
        $value = [Environment]::GetEnvironmentVariable($requirement.Name, "Process")
        if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -lt $requirement.MinimumLength) {
            throw "$($requirement.Name) must be present in .env and contain at least $($requirement.MinimumLength) characters."
        }
    }
}

function Get-KubernetesContext {
    $context = (& $script:Kubectl config current-context 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($context)) {
        throw "kubectl has no active context. Enable Kubernetes in Docker Desktop first."
    }
    return $context
}

function Test-MetricsServerAvailable {
    & $script:Kubectl get deployment metrics-server --namespace kube-system *> $null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    $available = (& $script:Kubectl get deployment metrics-server --namespace kube-system --output jsonpath='{.status.availableReplicas}' 2>$null | Out-String).Trim()
    return $LASTEXITCODE -eq 0 -and $available -eq "1"
}

function Ensure-MetricsServer {
    $context = Get-KubernetesContext
    Write-Host "Kubernetes context: $context"

    & $script:Kubectl get deployment metrics-server --namespace kube-system *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "metrics-server is not installed. Install it before start; the project HPA requires it."
    }

    if (-not (Test-MetricsServerAvailable) -and $context -eq "docker-desktop") {
        $deploymentJson = (& $script:Kubectl get deployment metrics-server --namespace kube-system --output json | Out-String)
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to inspect metrics-server."
        }
        $deployment = $deploymentJson | ConvertFrom-Json
        $arguments = @($deployment.spec.template.spec.containers[0].args)
        if ($arguments -notcontains "--kubelet-insecure-tls") {
            New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
            $patchPath = Join-Path $RuntimeDirectory "metrics-server-patch.json"
            $patch = @(
                @{
                    op = "add"
                    path = "/spec/template/spec/containers/0/args/-"
                    value = "--kubelet-insecure-tls"
                }
            ) | ConvertTo-Json -Compress
            [System.IO.File]::WriteAllText($patchPath, $patch, (New-Object System.Text.UTF8Encoding($false)))
            try {
                Invoke-NativeCommand $script:Kubectl @(
                    "patch", "deployment", "metrics-server",
                    "--namespace", "kube-system",
                    "--type=json", "--patch-file", $patchPath
                )
            }
            finally {
                Remove-Item -LiteralPath $patchPath -Force -ErrorAction SilentlyContinue
            }
        }
    }

    Invoke-NativeCommand $script:Kubectl @(
        "rollout", "status", "deployment/metrics-server",
        "--namespace", "kube-system", "--timeout=180s"
    )

    $deadline = (Get-Date).AddSeconds(90)
    do {
        & $script:Kubectl top nodes *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "metrics-server is ready."
            return
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    throw "metrics-server is Available but the Metrics API did not become ready."
}

function Build-Images {
    if ($SkipBuild) {
        Write-Host "Skipping image build."
        return
    }

    Push-Location $ProjectRoot
    try {
        Invoke-NativeCommand $script:Docker @(
            "build", "-f", "Dockerfile.backend",
            "-t", "british-museum-agent-backend:latest", "."
        )
        Invoke-NativeCommand $script:Docker @(
            "build", "-f", "Dockerfile.streamlit",
            "-t", "british-museum-agent-ui:latest", "."
        )
    }
    finally {
        Pop-Location
    }
}

function Stop-ManagedPortForwards {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
        return
    }

    try {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($forward in @($state.forwards)) {
            $process = Get-Process -Id ([int]$forward.pid) -ErrorAction SilentlyContinue
            if ($null -ne $process -and $process.ProcessName -like "kubectl*") {
                Stop-Process -Id $process.Id -Force
                Write-Host "Stopped port-forward '$($forward.name)' (PID $($process.Id))."
            }
        }
    }
    finally {
        Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    }
}

function Start-ManagedPortForward {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Service,
        [Parameter(Mandatory = $true)][int]$LocalPort,
        [Parameter(Mandatory = $true)][int]$RemotePort
    )

    $stdoutPath = Join-Path $RuntimeDirectory "$Name.stdout.log"
    $stderrPath = Join-Path $RuntimeDirectory "$Name.stderr.log"
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue

    $process = Start-Process `
        -FilePath $script:Kubectl `
        -ArgumentList @(
            "port-forward", "service/$Service",
            ("{0}:{1}" -f $LocalPort, $RemotePort),
            "--namespace", $Namespace
        ) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru

    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $detail = if (Test-Path -LiteralPath $stderrPath) {
            Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        }
        else {
            "No stderr was captured."
        }
        throw "Port-forward '$Name' failed: $detail"
    }

    return [ordered]@{
        name = $Name
        pid = $process.Id
        local_port = $LocalPort
        remote_port = $RemotePort
        url = "http://localhost:$LocalPort"
    }
}

function Wait-HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$WaitSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host "$Name is reachable at $Url"
                return
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    } while ((Get-Date) -lt $deadline)

    throw "$Name did not become reachable at $Url within $WaitSeconds seconds."
}

function Save-PortForwardState {
    param([Parameter(Mandatory = $true)][object[]]$Forwards)

    New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
    $state = [ordered]@{
        namespace = $Namespace
        created_at = (Get-Date).ToString("o")
        forwards = $Forwards
    }
    [System.IO.File]::WriteAllText(
        $StatePath,
        ($state | ConvertTo-Json -Depth 6),
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Start-Environment {
    Import-DotEnv $EnvPath
    Assert-RequiredSecrets
    Ensure-MetricsServer
    Build-Images

    & $DeployScript -Action apply -Overlay $Overlay -TimeoutSeconds $TimeoutSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "Kubernetes deployment failed with exit code $LASTEXITCODE."
    }

    Stop-ManagedPortForwards
    New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null

    $forwards = @()
    try {
        $forwards += Start-ManagedPortForward "backend" "backend" $BackendPort 8000
        $forwards += Start-ManagedPortForward "ui" "ui" $UiPort 8501
        $forwards += Start-ManagedPortForward "phoenix" "phoenix" $PhoenixPort 6006
        Save-PortForwardState $forwards

        Wait-HttpEndpoint "Backend" "http://localhost:$BackendPort/api/v1/health" 120
        Wait-HttpEndpoint "UI" "http://localhost:$UiPort" 60
        Wait-HttpEndpoint "Phoenix" "http://localhost:$PhoenixPort" 120
    }
    catch {
        Save-PortForwardState $forwards
        Stop-ManagedPortForwards
        throw
    }

    Write-Host ""
    Write-Host "Environment started successfully."
    Write-Host "UI:      http://localhost:$UiPort"
    Write-Host "Backend: http://localhost:$BackendPort/api/v1/health"
    Write-Host "Phoenix: http://localhost:$PhoenixPort"
}

function Stop-Environment {
    Stop-ManagedPortForwards

    $context = Get-KubernetesContext
    Write-Host "Kubernetes context: $context"

    & $script:Kubectl delete horizontalpodautoscaler --all --namespace $Namespace --ignore-not-found=true
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to remove HPAs before scaling down."
    }

    Invoke-NativeCommand $script:Kubectl @(
        "scale", "deployment", "--all",
        "--replicas=0", "--namespace", $Namespace
    )

    $deadline = (Get-Date).AddSeconds(180)
    do {
        $pods = (& $script:Kubectl get pods --namespace $Namespace --output name 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pods)) {
            break
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    Write-Host "Environment stopped. PVCs, Secrets and indexed data were preserved."
}

function Show-Status {
    $context = Get-KubernetesContext
    Write-Host "Kubernetes context: $context"
    Write-Host ""

    & $script:Kubectl get deployments,pods,horizontalpodautoscalers,persistentvolumeclaims --namespace $Namespace
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read resources from namespace '$Namespace'."
    }

    Write-Host ""
    if (Test-Path -LiteralPath $StatePath -PathType Leaf) {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($forward in @($state.forwards)) {
            $process = Get-Process -Id ([int]$forward.pid) -ErrorAction SilentlyContinue
            $status = if ($null -ne $process -and $process.ProcessName -like "kubectl*") { "running" } else { "stopped" }
            Write-Host ("Port-forward {0}: {1} ({2})" -f $forward.name, $forward.url, $status)
        }
    }
    else {
        Write-Host "No managed port-forwards are active."
    }
}

$script:Kubectl = Get-ExecutablePath "kubectl"

switch ($Action) {
    "start" {
        $script:Docker = Get-ExecutablePath "docker"
        Start-Environment
    }
    "stop" {
        Stop-Environment
    }
    "status" {
        Show-Status
    }
}
