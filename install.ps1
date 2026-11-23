<#
Install, update or remove the skills in this pack plus the recommended third-party skills
(recommended-skills.txt). Windows counterpart of install.sh; same lock file, same options.

  .\install.ps1                                   # numbered menus (install / update / uninstall / list)
  .\install.ps1 -Action install -Agent claude,codex -Scope project   # or -Agent all
  .\install.ps1 -Action install -Dir C:\path\to\skills -Skills dipesh-task-planner-skill
  .\install.ps1 -Action update                    # every skill installed by this script, every scope
  .\install.ps1 -Action uninstall -Skills unslop
  .\install.ps1 -Action list
  -NoRecommended / -OnlyRecommended limit the set.

Remote one-liner:
  irm https://raw.githubusercontent.com/Dipeshpal/dipesh-skills/main/install.ps1 | iex
Needs git on PATH.
#>
param(
    [ValidateSet("", "install", "update", "uninstall", "list")] [string]$Action = "",
    [ValidateSet("claude", "codex", "agents", "all")] [string[]]$Agent = @("claude"),
    [ValidateSet("user", "project")] [string]$Scope = "user",
    [string]$Dir = "",
    [string[]]$Skills = @(),
    [switch]$NoRecommended,
    [switch]$OnlyRecommended
)
$ErrorActionPreference = "Stop"
$RepoUrl = if ($env:DIPESH_SKILLS_REPO) { $env:DIPESH_SKILLS_REPO } else { "https://github.com/Dipeshpal/dipesh-skills.git" }
$Lock = ".dipesh-skills.lock"   # name <TAB> source <TAB> commit <TAB> date
$env:GIT_TERMINAL_PROMPT = "0"
$Work = Join-Path ([IO.Path]::GetTempPath()) ("dipesh-skills-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Work | Out-Null
$Interactive = ($Action -eq "")

function Menu($title, $options) {
    Write-Host $title
    for ($i = 0; $i -lt $options.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $options[$i]) }
    $r = Read-Host "Choice [1]"
    if ($r -match '^\d+$' -and [int]$r -ge 1 -and [int]$r -le $options.Count) { return [int]$r - 1 }
    return 0
}

function SkillsDir($agent, $scope) {
    if ($scope -eq "user") { Join-Path $HOME ".$agent\skills" } else { Join-Path (Get-Location) ".$agent\skills" }
}

try {
    # ---- sources
    $Src = $PSScriptRoot
    if (-not $Src -or -not (Test-Path (Join-Path $Src "plugins")) -or $Action -eq "update") {
        git clone --depth 1 --quiet $RepoUrl (Join-Path $Work "self") 2>$null
        if ($LASTEXITCODE -eq 0) { $Src = Join-Path $Work "self" }
        elseif (-not $Src -or -not (Test-Path (Join-Path $Src "plugins"))) { throw "could not fetch $RepoUrl" }
        else { Write-Warning "could not fetch $RepoUrl, using local checkout $Src" }
    }
    $Own = @{}
    Get-ChildItem (Join-Path $Src "plugins\*\skills\*") -Directory | ForEach-Object { $Own[$_.Name] = $_.FullName }
    $Rec = [ordered]@{}
    $recFile = Join-Path $Src "recommended-skills.txt"
    if (Test-Path $recFile) {
        foreach ($line in Get-Content $recFile) {
            $p = $line.Trim() -split '\s+'
            if ($p[0] -and -not $p[0].StartsWith("#")) { $Rec[$p[0]] = @{ repo = $p[1]; path = $(if ($p.Count -gt 2) { $p[2] } else { "." }) } }
        }
    }
    $ownCommit = (git -C $Src rev-parse --short HEAD 2>$null); if (-not $ownCommit) { $ownCommit = "local" }

    function LockRead($d) {
        $f = Join-Path $d $Lock
        if (Test-Path $f) { Get-Content $f | Where-Object { $_ } | ForEach-Object { , ($_ -split "`t") } } else { @() }
    }
    function LockSet($d, $name, $source, $commit) {
        $rows = @(LockRead $d | Where-Object { $_[0] -ne $name } | ForEach-Object { $_ -join "`t" })
        $rows += "$name`t$source`t$commit`t$(Get-Date -Format yyyy-MM-dd)"
        Set-Content -Path (Join-Path $d $Lock) -Value $rows
    }
    function LockDel($d, $name) {
        $rows = @(LockRead $d | Where-Object { $_[0] -ne $name } | ForEach-Object { $_ -join "`t" })
        if ($rows.Count) { Set-Content -Path (Join-Path $d $Lock) -Value $rows } else { Remove-Item (Join-Path $d $Lock) }
    }
    function InstallOne($name, $d) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $dest = Join-Path $d $name
        if ($Own.ContainsKey($name)) {
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            Copy-Item -Recurse $Own[$name] $dest
            LockSet $d $name "dipesh-skills" $ownCommit; return $ownCommit
        }
        if ($Rec.Contains($name)) {
            $r = $Rec[$name]; $co = Join-Path $Work ($r.repo -replace '/', '__')
            if (-not (Test-Path $co)) {
                git clone --depth 1 --quiet "https://github.com/$($r.repo).git" $co 2>$null
                if ($LASTEXITCODE -ne 0) { Write-Warning "could not fetch $($r.repo), skipped $name"; return $null }
            }
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            Copy-Item -Recurse (Join-Path $co $r.path) $dest
            if (Test-Path (Join-Path $dest ".git")) { Remove-Item -Recurse -Force (Join-Path $dest ".git") }
            $c = git -C $co rev-parse --short HEAD
            LockSet $d $name $r.repo $c; return $c
        }
        Write-Warning "unknown skill $name"; return $null
    }
    function Scopes {
        $dirs = foreach ($a in "claude", "codex", "agents") { SkillsDir $a "user"; SkillsDir $a "project" }
        if ($Dir) { $dirs += $Dir }
        $dirs | Select-Object -Unique | Where-Object { Test-Path (Join-Path $_ $Lock) }
    }
    function Wanted($n) { ($Skills.Count -eq 0) -or ($Skills -contains $n) }
    # Recommended Claude Code plugins (whole plugins, not skill folders), installed through the
    # claude CLI when Claude Code is a target. plugin@marketplace -> marketplace github repo
    function ClaudePlugins($mode) {
        if ($NoRecommended -or $Dir -or ($Agent -notcontains "claude")) { return }
        foreach ($p in @(@("ecc@ecc", "affaan-m/ECC"))) {
            $id, $repo = $p; $name = $id.Split("@")[0]
            if (-not (Wanted $name)) { continue }
            if ($mode -eq "install" -and $Interactive -and (Menu "Also install the Claude Code plugin $name (${repo}: agents, skills, hooks, rules)?" @("Yes", "No"))) { continue }
            if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Write-Host "  -> Claude plugin ${name}: run /plugin marketplace add $repo then /plugin install $id"; continue }
            claude plugin marketplace add $repo *> $null
            if ($LASTEXITCODE -ne 0) { claude plugin marketplace update $id.Split("@")[1] *> $null }
            if ($mode -eq "update") { claude plugin update $id *> $null } else { claude plugin install $id --scope $Scope *> $null }
            if ($LASTEXITCODE -eq 0) { Write-Host "$($mode)ed plugin $id" } else { Write-Warning "could not $mode plugin $id; run: claude plugin $mode $id" }
        }
    }
    function PostInstall($d, $n, $mode) {
        $bc = Join-Path $d "$n\scripts\bc.py"
        if (-not (Test-Path $bc)) { return }
        $py = Get-Command python -ErrorAction SilentlyContinue   # Windows PowerShell 5.1: no `??`
        if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
        if (-not $py) { Write-Warning "$n needs Python 3 for its setup: install it, then run: python `"$bc`" setup"; return }
        if ($mode -eq "refresh") { & $py.Source $bc setup --refresh }
        elseif ((Menu "$n needs a one-time setup (browser, model, dependencies). Run it now?" @("Yes", "Later")) -eq 0) {
            & $py.Source $bc setup --scope $mode
        } else { Write-Host "  -> finish with: python `"$bc`" setup" }
    }

    if ($Interactive) {
        $Action = @("install", "update", "uninstall", "list")[(Menu "What do you want to do?" @("Install skills", "Update installed skills", "Uninstall skills", "Show installed skills"))]
        if ($Action -eq "install") {
            Write-Host "Which agents are the skills for?"
            $names = @("Claude Code (.claude\skills)", "Codex (.codex\skills)", "Any agent using the open Agent Skills layout (.agents\skills)", "Custom directory")
            for ($i = 0; $i -lt $names.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $names[$i]) }
            $r = Read-Host "Numbers, separated by spaces (e.g. 1 2 for Claude Code and Codex) [1]"
            $picked = @($r -split '\s+' | Where-Object { $_ -match '^[1-4]$' } | ForEach-Object { [int]$_ - 1 })
            if (-not $picked.Count) { $picked = @(0) }
            $Agent = @($picked | Where-Object { $_ -lt 3 } | ForEach-Object { @("claude", "codex", "agents")[$_] })
            if ($picked -contains 3) { $Dir = Read-Host "Custom directory" }
            if ($Agent.Count) {
                $Scope = @("user", "project")[(Menu "Where should they be installed?" @("Install for you (user scope)", "Install for all collaborators on this repository (project scope)"))]
            }
        }
    }
    if ($Agent -contains "all") { $Agent = @("claude", "codex", "agents") }
    $Targets = @(foreach ($a in $Agent) { SkillsDir $a $Scope })
    if ($Dir) { $Targets = @($(if ($Interactive) { $Targets }) + $Dir) }

    switch ($Action) {
        "list" {
            $any = $false
            foreach ($d in Scopes) { $any = $true; Write-Host $d; foreach ($r in LockRead $d) { Write-Host ("  {0,-28} {1,-36} {2}  ({3})" -f $r) } }
            if (-not $any) { Write-Host "nothing installed by this script yet." }
        }
        "install" {
            $list = @(); if (-not $OnlyRecommended) { $list += $Own.Keys | Sort-Object }; if (-not $NoRecommended) { $list += $Rec.Keys }
            if ($Interactive) {
                for ($i = 0; $i -lt $list.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $list[$i]) }
                $r = Read-Host "Skills to install, numbers separated by spaces, or Enter for all"
                if ($r) { $Skills = @($r -split '\s+' | Where-Object { $_ -match '^\d+$' -and [int]$_ -le $list.Count } | ForEach-Object { $list[[int]$_ - 1] }) }
            }
            $ok = 0; $setupOffered = @()
            foreach ($Target in $Targets) {
                foreach ($n in $list) {
                    if (-not (Wanted $n)) { continue }
                    $c = InstallOne $n $Target
                    if (-not $c) { continue }
                    Write-Host "installed $n ($c) -> $Target\$n"; $ok++
                    if ($setupOffered -notcontains $n) {   # setup state is per user, not per agent dir
                        $setupOffered += $n
                        PostInstall $Target $n $(if ($Target.StartsWith((Get-Location).Path)) { "project" } else { "user" })
                    }
                }
            }
            ClaudePlugins "install"
            if (-not $ok -and $Skills -notcontains "ecc") { throw "no matching skills found" }
            Write-Host "done. Restart your agent session to pick up new skills."
        }
        "update" {
            $any = $false
            foreach ($d in Scopes) {
                foreach ($r in LockRead $d) {
                    $n = $r[0]; if (-not (Wanted $n)) { continue }; $any = $true
                    $c = InstallOne $n $d
                    if (-not $c) { continue }
                    if ($c -eq $r[2]) { Write-Host "up to date  $n ($c)  $d" } else { Write-Host "updated     $n ($($r[2]) -> $c)  $d" }
                    PostInstall $d $n "refresh"
                }
            }
            ClaudePlugins "update"
            if (-not $any) { Write-Host "nothing to update - install first." }
        }
        "uninstall" {
            if ($Skills.Count -eq 0 -and -not $Interactive) { throw "name the skills to remove with -Skills" }
            $entries = @(foreach ($d in Scopes) { foreach ($r in LockRead $d) { if (Wanted $r[0]) { , @($d, $r[0]) } } })
            if (-not $entries.Count) { Write-Host "nothing to uninstall."; break }
            if ($Interactive) {
                for ($i = 0; $i -lt $entries.Count; $i++) { Write-Host ("  {0}. {1}  ({2})" -f ($i + 1), $entries[$i][1], $entries[$i][0]) }
                $r = Read-Host "Numbers to remove (nothing removed on Enter)"
                $entries = @($r -split '\s+' | Where-Object { $_ -match '^\d+$' -and [int]$_ -le $entries.Count } | ForEach-Object { , $entries[[int]$_ - 1] })
            }
            foreach ($e in $entries) {
                Remove-Item -Recurse -Force (Join-Path $e[0] $e[1]); LockDel $e[0] $e[1]
                Write-Host "removed $($e[1]) from $($e[0])"
            }
        }
    }
}
finally {
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
