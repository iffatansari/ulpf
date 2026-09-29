<#
.SYNOPSIS
    One command: bring up the ULPF stack, publish only DLQ-free simulated
    records, and prove from OpenSearch that every one of them reached Silver
    and that the unknown ones were handled by drain3-fallback-v1.

.DESCRIPTION
    The claim being tested is narrow and falsifiable:

        1. Nothing this sends ends up in the DLQ.
        2. Every record it sends ends up in Silver.
        3. Records the pipeline cannot identify by format are not rejected.
           They are rescued by the drain3-fallback-v1 tier.

    Before anything is measured, every counter the UI reads -- Bronze,
    Silver, DLQ, the replay ledger and the upload ledger -- is wiped, so
    the run (and the dashboard behind it) always starts from zero.

    A count of "drain3-fallback-v1 > 0" does NOT prove (3). It is satisfied by
    a single stray record while every real unknown record went to the DLQ. So
    this script proves (3) per document, across two phases:

      Phase A - UNKNOWN ONLY
        Publishes free-form prose and nothing else. Every resulting Silver
        document is then checked individually: it must carry
        parser_id=drain3-fallback-v1, parser_tier=drain3, confidence 0.5,
        severity low, a drain_template containing drain3's own <IP> and
        <USER> wildcards, a real src_endpoint, and a non-null user. If the
        primary chain had quietly learned the prose, or a hardcoded regex
        had picked it up, none of those hold.

        The <IP>/<USER> check is the load-bearing one. Those tokens are
        emitted by drain3's masking instructions in
        orchestrator/parsers/drain_miner.py, so their presence in the stored
        template is direct evidence the machine-learned tier mined the line,
        not that some other parser happened to accept it.

      Phase B - THE FULL DLQ-FREE MIX
        Publishes syslog, cef, json and unknown together and checks the
        whole set end to end: exact per-parser counts, Bronze == Silver for
        the run, the DLQ count unmoved, and not one DLQ document referencing
        any raw event this run produced.

    Phase attribution is exact rather than coincidental: each of the four
    formats has its own dedicated parser id, so the parser_id breakdown IS
    the per-format breakdown. A syslog record can never appear under
    drain3-fallback-v1, and an unknown record can never appear under
    json-parser-v1.

    Startup is staged, and the order is load-bearing. The orchestrator opens
    its OpenSearch client at import-of-main time with no retry, so booting it
    before OpenSearch accepts connections kills it with ConnectionError and
    leaves `restart: on-failure` to paper over the race. That is why this
    stack used to look randomly broken.

.PARAMETER Count
    Records for phase B (the mixed run). Default 40, i.e. 10 per format.

.PARAMETER UnknownCount
    Records for phase A (the unknown-only run). Default 12.

.PARAMETER Rate
    Records per second for the two proof phases. Default 20. These phases
    exist to be checked, not watched, so they run fast.

.PARAMETER StreamRate
    Records per second for the final live-stream phase. Default 0.5, i.e.
    exactly one record every two seconds, which is the pace the UI is
    meant to be watched at: fast enough that the board keeps moving, slow
    enough that an individual event is readable as it arrives.

.PARAMETER StreamSeconds
    How long the live-stream phase runs. Default 60. Zero skips it.

.PARAMETER SeedDlq
    How many records to leave in the DLQ at the end. Default 2.
    These are produced by a short `dlq_demo` phase at the end of the run --
    a generator that emits JSON with no recoverable identity fields, so the
    orchestrator quarantines it as `no_recoverable_identity` through the
    real pipeline. No external seeding step is visible.

.PARAMETER Formats
    Phase B generator names. Must not name a deliberate-failure generator
    unless -AllowDeliberateFailures is passed, because this run's whole
    claim is that the DLQ stays empty.

.PARAMETER SkipBuild
    Reuse the existing simulator image instead of rebuilding it.

.PARAMETER WithUI
    Also start the UI container.

.PARAMETER Teardown
    Stop the stack when finished instead of leaving it up for inspection.

.PARAMETER KeepStream
    Leave the live-stream producer running after this script returns, so
    the UI keeps ticking at one record every two seconds. Without it the
    stream is stopped at the end of its window.

.EXAMPLE
    ./demo/run_live_demo.ps1
    12 unknown + 40 mixed records, both phases proven, then a one-minute
    live stream at one record every two seconds.

.EXAMPLE
    ./demo/run_live_demo.ps1 -UnknownCount 4 -StreamSeconds 0
    Fastest run that still exercises the fallback tier, with no live stream.

.EXAMPLE
    ./demo/run_live_demo.ps1 -Count 400 -Rate 50
    Longer soak, to watch drain3 keep up with volume.

.EXAMPLE
    ./demo/run_live_demo.ps1 -KeepStream
    Prove the run, then leave a 1-record-per-2s stream running for the UI.

.NOTES
    The DLQ finishes with -SeedDlq records on it (default 2) so the DLQ page
    has something to show. They arrive as no_recoverable_identity -- every
    parser tried, and Drain3 declined because the line carries no
    src/dst/user/mac field to stand an event on. Nothing this script's
    generators produce can reach the DLQ, and the run proves that before it
    seeds anything: teaching the demo that an unrecoverable record is a
    normal outcome would be a lie.
#>
[CmdletBinding()]
param(
    [int]$Count = 40,
    [int]$UnknownCount = 12,
    [double]$Rate = 20,
    [double]$StreamRate = 0.5,
    [int]$StreamSeconds = 60,
    [int]$SeedDlq = 2,
    [string]$Formats = "syslog,cef,json,unknown",
    [switch]$SkipBuild,
    [switch]$WithUI,
    [switch]$Teardown,
    [switch]$KeepStream,
    [switch]$AllowDeliberateFailures
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo

# --------------------------------------------------------------------- config
$SimImage = "ulpf-main-live_sim"
$SimName = "ulpf-live-demo-sim"
$OsUrl = "http://localhost:9200"
$ApiUrl = "http://localhost:8000"
$OrchBanner = "Orchestrator started. Reading"

$BronzeIndex = "ulpf-bronze"
$SilverIndex = "ulpf-silver"
$DlqIndex = "ulpf-dlq"
$ReprocessIndex = "ulpf-reprocess-runs"
$UploadsIndex = "ulpf-uploads"
$SourcesIndex = "ulpf-sources"

# Parser ids, straight out of orchestrator/main.py::_DEDICATED_PARSERS and
# orchestrator/parsers/drain_fallback.py::PARSER_ID.
$Drain3ParserId = "drain3-fallback-v1"

# Every generator the producer exposes (kafka_live_producer.py::GENERATORS).
# Kept separate from the table below: "the producer can emit this" and "this
# generator's records provably land under a known parser" are different
# questions, and conflating them makes a guard unreachable.
$KnownGenerators = @("syslog", "cef", "json", "unknown", "malformed_json")

# Which parser each generator's records end up under.
#   syslog/cef/json  -> a dedicated parser, which wins outright.
#   unknown          -> no dedicated parser exists, so the fallback tier takes it.
#   malformed_json   -> declared as `json` (FORCED_HINTS), the json parser
#                       declines the truncated body, and the orchestrator falls
#                       through to the fallback tier. It lands in Silver under
#                       drain3 rather than in the DLQ, which is why opting into
#                       the deliberate-failure generator still attributes cleanly.
$ExpectedParserFor = @{
    "syslog"         = "syslog-parser-v1"
    "cef"            = "cef-parser-v1"
    "json"           = "json-parser-v1"
    "unknown"        = $Drain3ParserId
    "malformed_json" = $Drain3ParserId
}

# Generators that break on purpose. The orchestrator still rescues them via
# drain3, so they do not reach the DLQ -- but including one would weaken the
# "this run is clean by construction" claim for no benefit, so the guard
# below makes that a deliberate choice rather than a typo.
$DeliberateFailureFormats = @("malformed_json")

# ------------------------------------------------------------------ reporting
$script:failures = New-Object System.Collections.ArrayList

function Say($msg) { Write-Host $msg }
function Step($msg) { Write-Host ""; Write-Host "=== $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Bad($msg) { Write-Host "    [FAIL] $msg" -ForegroundColor Red }
function Note($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }

# Deliberately emits nothing on the success path. A `return $condition` here
# would push a bare True/False into the caller's output stream on every
# check, interleaved with the report.
function Assert-That($condition, $message) {
    if ($condition) { Ok $message }
    else { Bad $message; [void]$script:failures.Add($message) }
}

# --------------------------------------------------------------- docker plumbing
function Probe-Docker($argList) {
    # Never throws. Two reasons this has to exist:
    #   - docker writes progress chatter to stderr even on success ("Container
    #     ulpf-opensearch Running"). Under $ErrorActionPreference=Stop a
    #     native command's stderr surfaces as a terminating
    #     NativeCommandError, so a healthy command reads as a failure.
    #   - some probes are EXPECTED to fail ("is the engine up?"), and a
    #     helper that throws cannot answer that question.
    # The ErrorActionPreference override is function-local: assigning it here
    # shadows the script value rather than mutating it, and real cmdlet
    # exceptions further up still stop us.
    $ErrorActionPreference = "Continue"
    $out = & docker @argList 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    # -Width so the console width does not insert a hard newline in the
    # middle of a sentence. Later steps split this text on "`n" to show the
    # last couple of lines of compose output, and a wrap at column 80 turns
    # one line of a real error into two half-lines.
    return [pscustomobject]@{ Code = $code; Out = ($out | Out-String -Width 500) }
}

function Invoke-Docker {
    $r = Probe-Docker $args
    if ($r.Code -ne 0) {
        throw "docker $($args -join ' ') failed (exit $($r.Code)): $($r.Out.Trim())"
    }
    return $r.Out
}

function Invoke-Compose { Invoke-Docker (@("compose") + $args) }

function Get-DockerErrorText($text) {
    # PowerShell reports a native command's stderr as "<prog> : <message>",
    # and docker's messages routinely continue onto a second line. Take the
    # first line that says something and drop the "docker.exe : " lead-in,
    # so the text is quotable in an error message rather than decorated.
    $line = ($text -split "`r?`n" | Where-Object { $_ -match '\S' } |
        ForEach-Object { ($_ -replace '^\s*\S+\s*:\s*', '').Trim() } |
        Where-Object { $_ -match '\S' } | Select-Object -First 1)
    if (-not $line) { return "(no output)" }
    return $line
}

function Wait-For($label, $timeoutSeconds, $probe) {
    # A fixed sleep is not a readiness check: it is a guess that happens to
    # be right when the machine is idle. Poll a real condition instead, and
    # keep the last error so a timeout can say WHY.
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    $last = ""
    while ((Get-Date) -lt $deadline) {
        try {
            if (& $probe) { Ok $label; return $true }
        } catch { $last = $_.Exception.Message }
        Start-Sleep -Seconds 3
    }
    if ($last) { Note "last error: $last" }
    return $false
}

function Get-ContainerId($service) {
    $ids = @((Invoke-Compose ps -q $service) -split "\s+" | Where-Object { $_ })
    if (-not $ids) { return "" }
    return $ids[-1]
}

function Get-ComposeNetwork {
    # Do not hardcode "<project>_default": the network name is derived from
    # the directory name, and this checkout's is "ulpf-main (1)\ulpf-main".
    # Ask the running container which network it is actually attached to.
    $cid = Get-ContainerId "redpanda"
    if (-not $cid) { throw "redpanda container not found; is the stack up?" }
    # The template concatenates every network name, so take the first line:
    # if this container is ever attached to more than one, a joined name
    # would fail as a network that does not exist instead of using the one
    # Redpanda is actually reachable on.
    $net = ((Invoke-Docker inspect -f "{{range `$k, `$v := .NetworkSettings.Networks}}{{`$k}}{{end}}" $cid) -split "`r?`n" |
        Where-Object { $_ -match '\S' } | Select-Object -First 1)
    if (-not $net) { throw "could not determine the compose network for $cid" }
    return $net.Trim()
}

# ------------------------------------------------------------- opensearch client
function Invoke-Os($path, $body) {
    $json = if ($null -eq $body) { "{}" } else { ($body | ConvertTo-Json -Depth 24 -Compress) }
    Invoke-RestMethod -Method Post -Uri "$OsUrl/$path" -ContentType "application/json" -Body $json -TimeoutSec 20
}

function Get-Os($path) {
    Invoke-RestMethod -Method Get -Uri "$OsUrl/$path" -TimeoutSec 20
}

function Invoke-OsDelete($path) {
    # Separate from Invoke-Os because that one POSTs: POSTing to a _doc
    # endpoint upserts the document, so "delete the stale demo source" run
    # through it would have re-indexed every row it meant to remove.
    return Invoke-RestMethod -Method Delete -Uri "$OsUrl/$path" -TimeoutSec 20
}

$script:mappingCache = @{}

function Get-IndexMapping($index) {
    if (-not $script:mappingCache.ContainsKey($index)) {
        $script:mappingCache[$index] = (Get-Os "$index/_mapping").$index.mappings.properties
    }
    return $script:mappingCache[$index]
}

function Resolve-SearchField($index, $path) {
    # Silver is dynamically mapped, so a source id lands on
    # `extensions.source_id` as text-with-a-keyword-subfield, while Bronze
    # declares it as a real keyword. A hardcoded ".keyword" suffix is right
    # for one and wrong for the other, and a wrong field name produces an
    # empty result set -- which reads as "no records arrived" and sends you
    # hunting for a pipeline bug that does not exist. Resolve it from the
    # live mapping instead of guessing.
    $node = $null
    $current = Get-IndexMapping $index
    foreach ($part in $path.Split(".")) {
        if ($null -eq $current) { return $null }
        $prop = $current.PSObject.Properties[$part]
        if ($null -eq $prop) { return $null }
        $node = $prop.Value
        $current = $node.properties
    }
    if ($null -eq $node) { return $null }
    if ($node.type -eq "text" -and $null -ne $node.fields) {
        if ($null -ne $node.fields.PSObject.Properties["keyword"]) { return "$path.keyword" }
    }
    return $path
}

function Get-SourceTermQuery($index, $path, $sourceId) {
    $field = Resolve-SearchField $index $path
    if (-not $field) {
        throw "$index has no field '$path' in its mapping. Silver is expected to carry the source id under extensions.source_id and Bronze under source_id."
    }
    return @{ term = @{ $field = $sourceId } }
}

function Get-CountFor($index, $query) {
    return (Invoke-Os "$index/_count" @{ query = $query }).count
}

function Get-ParserBreakdown($index, $query) {
    $agg = Invoke-Os "$index/_search" @{
        size = 0
        query = $query
        aggs = @{ p = @{ terms = @{ field = "parser_id.keyword"; size = 50 } } }
    }
    $map = @{}
    foreach ($b in $agg.aggregations.p.buckets) { $map[$b.key] = $b.doc_count }
    return $map
}

function Get-AllDocs($index, $query, $fields) {
    # Page with from/size rather than one giant request, so the result stays
    # correct if a caller raises -Count into the thousands. The default
    # OpenSearch result window is 10k, which is also the honest ceiling for
    # this script: past it, from/size paging silently returns short pages
    # and a "clean" run could hide missing records.
    $docs = New-Object System.Collections.ArrayList
    $page = 500
    $window = 10000
    $from = 0
    $total = 0
    while ($true) {
        $body = @{
            size = $page
            from = $from
            query = $query
            track_total_hits = $true
            sort = @(@{ _doc = "asc" })
        }
        if ($fields) { $body._source = $fields }
        $res = Invoke-Os "$index/_search" $body
        $hits = $res.hits.hits
        $total = $res.hits.total.value
        foreach ($h in $hits) { [void]$docs.Add($h._source) }
        if ($hits.Count -eq 0 -or $docs.Count -ge $total) { break }
        $from += $page
        if ($from + $page -gt $window) {
            throw "$index matched $total documents for this run, past the $window result window. Re-run with a smaller -Count."
        }
    }
    return ,$docs
}

# ------------------------------------------------------------------- producing
function Invoke-Producer {
    param(
        [string]$SourceId,
        [string]$FormatList,
        [int]$N,
        [string]$Label
    )
    if ($N -le 0) { throw "$Label needs a positive record count" }

    Step "Producing $N record(s) as source_id=$SourceId ($FormatList)"
    $out = Invoke-Docker run --rm --network $script:Network `
        -e REDPANDA_BROKER=redpanda:29092 `
        -e RAW_TOPIC=logs.raw `
        -e SIM_SOURCE_ID=$SourceId `
        -e SIM_SOURCE_TYPE=application `
        -e SIM_COLLECTOR_ID=live-sim-1 `
        --name $SimName `
        $SimImage --count $N --rate $Rate --format $FormatList --preview ([Math]::Min(6, $N))

    foreach ($line in ($out -split "`r?`n")) {
        if ($line -match '^\s*\d+\s+\S+\s+hint=') { Say ("    " + $line.Trim()) }
    }

    if ($out -notmatch 'sent (\d+) records') {
        Bad "the producer never reported a total"
        throw "producer produced no summary; it did not complete"
    }
    $sent = [int]$Matches[1]
    if ($sent -ne $N) {
        Bad "producer reported $sent records, expected $N"
        throw "producer short-circuited"
    }
    Ok "producer confirmed $sent record(s) accepted by Redpanda (acks=all)"
    return $sent
}

function Wait-ForSilver($sourceId, $expected, $label) {
    # "Sent" is not "stored": indexing is async. Poll the real count.
    $script:silverSeen = 0
    $ok = Wait-For "$label" 120 {
        $script:silverSeen = Get-CountFor $SilverIndex (Get-SourceTermQuery $SilverIndex "extensions.source_id" $sourceId)
        return $script:silverSeen -ge $expected
    }
    return $ok
}

function Register-Source($sourceId, $name) {
    # Written straight to ulpf-sources because the registry mints its own
    # ids (make_source_id appends a random suffix), and the whole point of
    # this run is to tag its events with ids the script already chose so it
    # can query them afterwards.
    #
    # Registering matters for more than tidiness: the Sources page totals
    # per registered source, so events tagged with an unregistered
    # source_id have no card to land on at all.
    $now = (Get-Date).ToUniversalTime().ToString("o")
    $doc = @{
        source_id       = $sourceId
        name            = $name
        source_type     = "application"
        transport       = "kafka_sim"
        expected_format = "mixed"
        enabled         = $true
        created_at      = $now
        last_seen_at    = $null
        last_upload_at  = $null
        description     = "created by demo/run_live_demo.ps1"
    }
    # `$($sourceId)?refresh=true` and NOT `$sourceId?refresh=true`: `?` is a
    # legal character in a PowerShell variable name, so the bare form
    # interpolates to `=true` and every source in the run lands on the same
    # document id -- each one overwriting the last, silently leaving exactly
    # one registered source no matter how many were asked for.
    Invoke-Os "$SourcesIndex/_doc/$($sourceId)?refresh=true" $doc | Out-Null
}

function Get-UiStats {
    # The exact payload every section of the UI renders. Reading it here
    # means the run verifies the same numbers the operator is looking at,
    # rather than a private tally the dashboard never sees.
    return Invoke-RestMethod -Method Get -Uri "$ApiUrl/stats" -TimeoutSec 20
}

function Start-LiveStream {
    param(
        [string]$SourceId,
        [string]$FormatList,
        [double]$RecordsPerSecond,
        [int]$Seconds
    )

    # Detached, and with a different container name from the proof phases:
    # a stream left running and a proof run's producer must never race for
    # the same name, and `docker run --rm` here would fight the cleanup
    # below for it.
    $streamName = "$SimName-stream"
    $expected = [int][Math]::Floor($Seconds * $RecordsPerSecond)
    if ($expected -lt 1) {
        Note "a $Seconds s stream at $RecordsPerSecond/s would produce no records; skipping"
        return $null
    }

    Step "Live stream - one record every $([Math]::Round(1 / $RecordsPerSecond, 2))s for $Seconds s"
    # Probe before removing: `docker rm -f` on a container that is not there
    # exits non-zero, and Invoke-Docker turns a non-zero exit into a thrown
    # error. A missing container is the normal case on the first run.
    $existing = (Probe-Docker @("ps", "-a", "--filter", "name=^/$streamName$", "-q")).Out.Trim()
    if ($existing) { Invoke-Docker rm -f $streamName | Out-Null }

    Invoke-Docker run -d --rm --network $script:Network `
        -e REDPANDA_BROKER=redpanda:29092 `
        -e RAW_TOPIC=logs.raw `
        -e SIM_SOURCE_ID=$SourceId `
        -e SIM_SOURCE_TYPE=application `
        -e SIM_COLLECTOR_ID=live-sim-1 `
        --name $streamName `
        $SimImage --rate $RecordsPerSecond --format $FormatList --preview 0 | Out-Null
    Ok "producer running as '$streamName', target $expected record(s)"

    return @{
        Name     = $streamName
        SourceId = $SourceId
        Expected = $expected
        Pace     = $RecordsPerSecond
    }
}

function Stop-LiveStream($stream) {
    if (-not $stream) { return }
    $probe = Probe-Docker @("ps", "-a", "--filter", "name=^/$($stream.Name)$", "-q")
    if ($probe.Out.Trim()) {
        Invoke-Docker rm -f $stream.Name | Out-Null
        Ok "live-stream producer stopped"
    }
}

function Wait-ForLiveStream($stream, $seconds) {
    # Print the dashboard's own numbers on a fixed tick, so the operator can
    # watch the UI move at the same pace instead of taking the final total
    # on faith. A fixed sleep is still the right tool here: this is a
    # duration to fill, not a condition to detect.
    $deadline = (Get-Date).AddSeconds($seconds)
    $ticks = 0
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $ticks++
        try {
            $s = Get-UiStats
            $seen = Get-CountFor $SilverIndex (Get-SourceTermQuery $SilverIndex "extensions.source_id" $stream.SourceId)
            $perTick = [Math]::Round(2 * $stream.Pace, 2)
            Say ("    t+{0,3}s  silver={1,4}  bronze={2,4}  dlq={3,2}  rescued={4,3}  (this stream {5}, expect ~{6}/2s)" -f `
                    ($ticks * 2), $s.silver_events, $s.bronze_events, $s.dlq_events, $s.rescued, $seen, $perTick)
        }
        catch {
            Note "  tick read failed: $($_.Exception.Message)"
        }
    }
}

# ==============================================================================
#                                    MAIN
# ==============================================================================
try {
    # ---------------------------------------------------------------- validate
    Step "Validating arguments"
    $formatList = @($Formats.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($formatList.Count -eq 0) { throw "-Formats must name at least one generator" }
    foreach ($f in $formatList) {
        if ($KnownGenerators -notcontains $f) {
            throw "'$f' is not a generator the producer exposes. Known: $($KnownGenerators -join ', ')"
        }
    }
    $deliberate = @($formatList | Where-Object { $DeliberateFailureFormats -contains $_ })
    $cleanMix = ($deliberate.Count -eq 0)
    if (-not $AllowDeliberateFailures -and -not $cleanMix) {
        throw "'$($deliberate -join ', ')' is a deliberate-failure generator. Including it muddies the claim this run exists to prove. Re-run with -AllowDeliberateFailures if that is genuinely what you want."
    }
    if (-not $cleanMix) {
        Note "deliberate-failure generator(s) opted into: $($deliberate -join ', ')"
        Note "these still reach Silver (the fallback tier rescues them), but they carry no"
        Note "masked prose template, so the drain3 template-masking check is skipped."
    }
    if (-not ($formatList -contains "unknown")) {
        Note "'unknown' is not in -Formats, so the fallback tier will not be exercised. That is a valid run, but it does not prove what this script is for."
    }
    $runTag = (Get-Date -Format "HHmmss")
    $sourceUnknown = "ulpf-demo-$runTag-unknown"
    $sourceMixed = "ulpf-demo-$runTag-mixed"
    $sourceStream = "ulpf-demo-$runTag-stream"
    $sourceQuarantine = "ulpf-demo-$runTag-quarantine"
    Ok "run tag $runTag"

    if ($StreamSeconds -lt 0) { throw "-StreamSeconds cannot be negative" }
    if ($StreamRate -le 0) { throw "-StreamRate must be greater than 0" }
    if ($SeedDlq -lt 0) { throw "-SeedDlq cannot be negative" }

    # --------------------------------------------------------------- preflight
    Step "Preflight"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker is not on PATH. Install Docker Desktop, or start it and re-run."
    }
    if (-not (Test-Path (Join-Path $repo "docker-compose.yml"))) {
        throw "docker-compose.yml not found in $repo"
    }

    $composeProbe = Probe-Docker @("compose", "version")
    if ($composeProbe.Code -ne 0) {
        throw "'docker compose' is unavailable: $(Get-DockerErrorText $composeProbe.Out)"
    }

    # 'compose version' is answered by the client-side plugin and succeeds
    # with the engine stopped, so it proves nothing about Docker actually
    # running. Ask the engine, and say so plainly when it is not -- otherwise
    # the first failure the operator sees is a daemon-connection stack trace
    # from three steps down.
    $engine = Probe-Docker @("version", "--format", "{{.Server.Version}}")
    if ($engine.Code -ne 0) {
        throw "the Docker engine is not reachable. Start Docker Desktop and wait for it to report ready, then re-run. Docker said: $(Get-DockerErrorText $engine.Out)"
    }

    # A container left over from an aborted run holds the name and makes the
    # next one fail with a name conflict that looks nothing like the real
    # problem.
    $stale = (Probe-Docker @("ps", "-a", "--filter", "name=^/$SimName$", "-q")).Out.Trim()
    if ($stale) { Invoke-Docker rm -f $SimName | Out-Null; Ok "removed stale $SimName container" }

    $composeVersion = Get-DockerErrorText $composeProbe.Out
    Ok "docker engine $($engine.Out.Trim()), $composeVersion"

    # ------------------------------------------------------------ stage 1: infra
    Step "Stage 1/2 - OpenSearch and Redpanda"
    (Invoke-Compose up -d opensearch redpanda).Trim().Split("`n") | Select-Object -Last 2 | ForEach-Object { Say "    $($_.Trim())" }

    $infraOk = Wait-For "opensearch yellow-or-green and redpanda healthy" 180 {
        $h = Get-Os "_cluster/health"
        $rp = (Invoke-Docker inspect -f "{{.State.Health.Status}}" (Get-ContainerId "redpanda")).Trim()
        ($h.status -in @("yellow", "green")) -and ($rp -eq "healthy")
    }
    if (-not $infraOk) { throw "infrastructure did not become healthy" }

    # ------------------------------------------------------------- stage 2: app
    Step "Stage 2/2 - orchestrator and API"
    $appServices = @("orchestrator", "api")
    if ($WithUI) { $appServices += "ui" }
    # The DLQ demo records are produced by the Kafka producer directly,
    # so no collector service is needed.
    (Invoke-Compose up -d --build @appServices).Trim().Split("`n") | Select-Object -Last 2 | ForEach-Object { Say "    $($_.Trim())" }

    # The startup banner is the honest signal: it prints only once the
    # OpenSearch client is connected and the consumer is live. Two traps,
    # both of which produce a wrong answer rather than an error:
    #   - `docker logs` keeps output from previous runs, so an old line
    #     satisfies the check while the current process is crash-looping.
    #   - `compose up` does not restart an unchanged, already-running
    #     container, so demanding a *new* banner fails every run after the
    #     first.
    # Scope the search to the container's current lifetime by reading
    # StartedAt on each poll. Re-reading it means a container that crashes
    # and comes back is still recognised once it actually boots.
    $orchOk = Wait-For "orchestrator consuming $OrchBanner ..." 180 {
        $cid = Get-ContainerId "orchestrator"
        if (-not $cid) { return $false }
        $startedAt = (Invoke-Docker inspect -f "{{.State.StartedAt}}" $cid).Trim()
        if (-not $startedAt) { return $false }
        $recent = Invoke-Docker logs --since $startedAt $cid
        ($recent -like "*$OrchBanner*")
    }
    $apiOk = Wait-For "api serving on :8000" 120 {
        (Invoke-RestMethod "$ApiUrl/openapi.json" -TimeoutSec 5).paths -ne $null
    }

    if (-not ($orchOk -and $apiOk)) {
        # The orchestrator's first action is an OpenSearch connection with no
        # retry, so a failure here is almost always the boot race rather than
        # a real defect. Name it instead of leaving the operator to guess.
        $tail = (Invoke-Docker logs --tail 40 (Get-ContainerId "orchestrator")) -split "`r?`n" |
            Where-Object { $_ -match 'ConnectionError|Connection refused|Traceback' } | Select-Object -First 1
        if ($tail) {
            Note "looks like the OpenSearch boot race, not a real defect:"
            Note "  $($tail.Trim())"
        }
        throw "app services did not come up (orchestrator=$orchOk api=$apiOk)"
    }

    # ------------------------------------------------------------- sim image
    Step "Building the simulator image"
    if ($SkipBuild) {
        Ok "skipping build (image reused)"
    } else {
        (Invoke-Docker build -q -f demo/Dockerfile -t $SimImage .).Trim().Split("`n") | Select-Object -Last 1 | ForEach-Object { Say "    $($_.Trim())" }
    }

    # A single probe that does two jobs. Importing drain3 is the exact
    # regression the demo image is prone to: install only the SSE collector's
    # requirements and the one generator meant to exercise the fallback tier
    # cannot have done so, however long it runs. Reading GENERATORS out of the
    # real producer module means the validation above is checked against the
    # code that will actually run, not against a list copied into this script
    # that quietly drifts.
    $probe = @"
import json, drain3, demo.kafka_live_producer as p
print('PROBE=' + json.dumps({'generators': sorted(p.GENERATORS), 'default_formats': p.DEFAULT_FORMATS, 'forced_hints': sorted(p.FORCED_HINTS)}))
"@
    # Collapse to one line before handing it to docker: a multi-line argv
    # element survives most shells and then surprises you on the one you
    # did not test. The lines are separate statements, so they are joined
    # with ';' rather than a bare space. Single quotes only: Windows
    # PowerShell 5.1 does not escape embedded double quotes when building a
    # native command line, so a " would be swallowed before python sees it.
    $probe = ($probe -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join "; "
    $probeOut = Invoke-Docker run --rm --entrypoint python $SimImage -c $probe
    $probeLine = ($probeOut -split "`r?`n" | Where-Object { $_ -like "PROBE=*" } | Select-Object -First 1)
    if (-not $probeLine) {
        Bad "the simulator image could not import drain3 / kafka_live_producer"
        Note $probeOut.Trim()
        throw "simulator image is not usable"
    }
    Ok "simulator image imports drain3 and the producer"

    # ------------------------------------------------------------- reset
    # Every section of the UI counts straight out of these indices, so a fresh
    # run must start from an empty board: Bronze, Silver, DLQ, the replay
    # ledger and the upload ledger are all cleared before anything is
    # measured. Documents are deleted rather than the indices themselves so
    # every mapping survives and the field resolution below keeps working.
    #
    # Any DLQ record left over from an earlier run is deleted here too, not
    # carried as "before" state. A run that starts with a non-empty DLQ can
    # only ever assert "the DLQ did not grow", which is a much weaker claim
    # than "the DLQ is empty and stayed empty" -- and it is why a stale red
    # row from last week's manual test could sit on the page for ever.
    Step "Reset - all counters to zero"
    foreach ($idx in @($BronzeIndex, $SilverIndex, $DlqIndex, $ReprocessIndex, $UploadsIndex)) {
        $present = $true
        try { Get-Os $idx | Out-Null } catch { $present = $false }
        if (-not $present) { Note "$idx not created yet - nothing to clear"; continue }
        $deleted = (Invoke-Os "$idx/_delete_by_query?refresh=true&conflicts=proceed" @{ query = @{ match_all = @{} } }).deleted
        Ok "$idx cleared ($deleted document(s) removed)"
    }

    # Deleting documents is not enough for the board to read zero. The API
    # also holds an SSE replay buffer (which would replay the deleted events
    # at any reconnecting browser) and a resolved-field cache built from the
    # mappings that were just invalidated. This is the call that clears both
    # and tells every open tab to re-read its snapshot.
    try {
        $r = Invoke-RestMethod -Method Post -Uri "$ApiUrl/stats/reset" -ContentType "application/json" -Body "{}" -TimeoutSec 20
        if ($r.reset) {
            Ok "API live-stream state cleared (replay buffer + field cache)"
        }
        else {
            Note "the API reported no live-stream hub; counters are still zero in OpenSearch"
        }
    }
    catch {
        Note "could not reach $ApiUrl/stats/reset ($($_.Exception.Message))"
        Note "OpenSearch is empty, but an already-open browser tab may replay pre-run events."
    }

    # Previous demo sources are artifacts of an earlier run, not user
    # config, and leaving them behind means every run adds another empty
    # card to the Sources page. Matched on the `ulpf-demo-` id prefix,
    # which this script is the only thing that mints -- so a hand-registered
    # source can never be caught by it, whatever its description says.
    $staleSources = 0
    try {
        # No ?refresh= here: _search does not accept it and answers 400 with
        # "unrecognized parameter". The deletes below are individually
        # refreshed, so the search does not need to be.
        $found = Invoke-Os "$SourcesIndex/_search" @{
            size = 1000
            query = @{ prefix = @{ source_id = "ulpf-demo-" } }
            _source = @("source_id")
        }
        foreach ($h in $found.hits.hits) {
            Invoke-OsDelete "$SourcesIndex/_doc/$($h._source.source_id)?refresh=true"
            $staleSources++
        }
    }
    catch {
        Note "could not sweep previous demo sources: $($_.Exception.Message)"
    }
    if ($staleSources -gt 0) { Ok "removed $staleSources source(s) registered by an earlier demo run" }

    # Verify rather than assume. "We deleted them" and "they read zero" are
    # different claims, and the second is the one the UI is about to make.
    Step "Verify - every counter reads zero"
    foreach ($idx in @($BronzeIndex, $SilverIndex, $DlqIndex, $ReprocessIndex, $UploadsIndex)) {
        $present = $true
        try { Get-Os $idx | Out-Null } catch { $present = $false }
        if (-not $present) {
            Assert-That $true "$idx does not exist yet, so its count is 0"
            continue
        }
        $remaining = (Get-Os "$idx/_count").count
        Assert-That ($remaining -eq 0) "$idx reads 0 (found $remaining)"
    }
    try {
        $s = Invoke-RestMethod -Method Get -Uri "$ApiUrl/stats" -TimeoutSec 20
        Assert-That ($s.silver_events -eq 0) "the UI aggregate reports 0 normalized events (found $($s.silver_events))"
        Assert-That ($s.dlq_events -eq 0) "the UI aggregate reports 0 DLQ records (found $($s.dlq_events))"
        Assert-That ($s.rescued -eq 0) "the UI aggregate reports 0 rescued events (found $($s.rescued))"
    }
    catch {
        Bad "could not read $ApiUrl/stats, so the dashboard's own numbers are unverified"
        Note $_.Exception.Message
    }

    # ------------------------------------------------------------- baseline DLQ
    Step "Baseline"
    $dlqBefore = (Get-Os "$DlqIndex/_count").count
    $bronzeBefore = (Get-Os "$BronzeIndex/_count").count
    $silverBefore = (Get-Os "$SilverIndex/_count").count
    Say "    DLQ=$dlqBefore  Bronze=$bronzeBefore  Silver=$silverBefore"

    $script:Network = Get-ComposeNetwork
    Ok "compose network: $($script:Network)"

    # Registered after the reset, because the reset clears the run ledger
    # and the registry is where a run's source ids become visible as cards
    # on the Sources page.
    Step "Register this run's sources"
    Register-Source $sourceUnknown "Live demo $runTag - unknown only"
    Register-Source $sourceMixed "Live demo $runTag - mixed"
    if ($StreamSeconds -gt 0) { Register-Source $sourceStream "Live demo $runTag - live stream" }
    if ($SeedDlq -gt 0) { Register-Source $sourceQuarantine "Live demo $runTag - quarantined" }
    Ok "registered $($sourceUnknown), $sourceMixed$(if ($StreamSeconds -gt 0) { ", $sourceStream" })$(if ($SeedDlq -gt 0) { ", $sourceQuarantine" })"

    # ========================================================================
    # PHASE A -- unknown records only
    # ========================================================================
    Step "PHASE A - unknown format only ($UnknownCount record(s))"
    Note "the only parser that can accept these is the fallback tier, so any"
    Note "record that is not drain3-fallback-v1 is a defect, not a curiosity."

    $sentA = Invoke-Producer -SourceId $sourceUnknown -FormatList "unknown" -N $UnknownCount -Label "phase A"
    $settledA = Wait-ForSilver $sourceUnknown $sentA "phase A: all $sentA unknown record(s) in Silver"

    $queryA = Get-SourceTermQuery $SilverIndex "extensions.source_id" $sourceUnknown
    $docsA = Get-AllDocs $SilverIndex $queryA @("parser_id", "parser_tier", "confidence_score", "severity", "class_name", "user", "src_endpoint", "dst_endpoint", "time", "raw_event_id", "extensions")

    Assert-That $settledA "phase A: all $sentA unknown record(s) reached Silver"
    if (-not $settledA) {
        Bad "phase A: only $($script:silverSeen) of $sentA arrived; cannot prove per-record behaviour"
    }
    else {
        Assert-That ($docsA.Count -eq $sentA) "phase A: Silver holds exactly $sentA document(s) for this run ($($docsA.Count))"

        $offenders = @($docsA | Where-Object { $_.parser_id -ne $Drain3ParserId })
        Assert-That ($offenders.Count -eq 0) "phase A: every unknown record was normalized by $Drain3ParserId"
        if ($offenders.Count -gt 0) {
            $offenders | Select-Object -First 3 | ForEach-Object { Note "  handled by $($_.parser_id) instead" }
        }

        $badTier = @($docsA | Where-Object { $_.parser_tier -ne "drain3" })
        Assert-That ($badTier.Count -eq 0) "phase A: every record is tagged parser_tier=drain3 (the ML tier, not a generic parser)"

        $badConf = @($docsA | Where-Object { [double]$_.confidence_score -ne 0.5 })
        Assert-That ($badConf.Count -eq 0) "phase A: every record carries the tier's 0.5 confidence (never high)"

        $badSev = @($docsA | Where-Object { $_.severity -ne "low" })
        Assert-That ($badSev.Count -eq 0) "phase A: every record is severity=low (the tier never invents severity)"

        $noTemplate = @($docsA | Where-Object { -not $_.extensions.drain_template })
        Assert-That ($noTemplate.Count -eq 0) "phase A: every record has a mined drain_template"

        # The decisive check. <IP> and <USER> are drain3's own wildcards,
        # produced by the masking instructions in drain_miner.py. If the
        # template still shows a literal IP, no machine-learned template was
        # involved, whatever parser_id claims.
        $unmasked = @($docsA | Where-Object {
            $t = $_.extensions.drain_template
            $t -and ($t -notlike "*<IP>*" -or $t -notlike "*<USER>*")
        })
        Assert-That ($unmasked.Count -eq 0) "phase A: drain3 actually masked the line (every template has <IP> and <USER>)"
        if ($unmasked.Count -gt 0) { Note "  unmasked: $($unmasked[0].extensions.drain_template)" }

        $noSrc = @($docsA | Where-Object { $_.src_endpoint -notmatch '^\d{1,3}(\.\d{1,3}){3}$' })
        Assert-That ($noSrc.Count -eq 0) "phase A: every record has a real IPv4 src_endpoint recovered from the prose"

        $noUser = @($docsA | Where-Object { -not $_.user })
        Assert-That ($noUser.Count -eq 0) "phase A: every record has a user recovered from the prose"

        $noCluster = @($docsA | Where-Object { $null -eq $_.extensions.drain_cluster_id })
        Assert-That ($noCluster.Count -eq 0) "phase A: every record is tied to a drain3 cluster id"

        $dlqAfterA = (Get-Os "$DlqIndex/_count").count
        Assert-That ($dlqAfterA -eq $dlqBefore) "phase A: DLQ unmoved ($dlqBefore -> $dlqAfterA)"

        if ($docsA.Count -gt 0) {
            Say ""
            Note "one phase A document, as stored:"
            $sample = $docsA[0]
            Note "  parser_id       $($sample.parser_id)"
            Note "  parser_tier     $($sample.parser_tier)  confidence=$($sample.confidence_score)  severity=$($sample.severity)"
            Note "  class_name      $($sample.class_name)"
            Note "  src_endpoint    $($sample.src_endpoint)   user=$($sample.user)"
            Note "  drain_template  $($sample.extensions.drain_template)"
            Note "  cluster_id      $($sample.extensions.drain_cluster_id)"
            Say ""
        }
    }

    # ========================================================================
    # PHASE B -- the full DLQ-free mix
    # ========================================================================
    Step "PHASE B - the DLQ-free mix ($Count record(s): $Formats)"
    $sentB = Invoke-Producer -SourceId $sourceMixed -FormatList $Formats -N $Count -Label "phase B"
    $settledB = Wait-ForSilver $sourceMixed $sentB "phase B: all $sentB record(s) in Silver"

    $queryB = Get-SourceTermQuery $SilverIndex "extensions.source_id" $sourceMixed
    $docsB = Get-AllDocs $SilverIndex $queryB @("parser_id", "parser_tier", "raw_event_id", "user", "src_endpoint", "extensions")

    $breakdown = Get-ParserBreakdown $SilverIndex $queryB
    Say ""
    Say "    parser breakdown for this run:"
    foreach ($k in ($breakdown.Keys | Sort-Object)) { Say ("      {0,-22} {1}" -f $k, $breakdown[$k]) }

    # The producer rotates formats[sent % len(formats)], so the split is
    # fully determined by the count -- not estimated.
    $expectedPerFormat = @{}
    for ($i = 0; $i -lt $sentB; $i++) {
        $g = $formatList[$i % $formatList.Count]
        if (-not $expectedPerFormat.ContainsKey($g)) { $expectedPerFormat[$g] = 0 }
        $expectedPerFormat[$g]++
    }
    # Totals keyed by the parser that must claim them. Assert per format and
    # you get it wrong the moment two formats share a parser: `unknown` and
    # `malformed_json` both land on the fallback tier, so the drain3 count is
    # their sum, not either one alone.
    $expectedPerParser = @{}
    foreach ($g in $expectedPerFormat.Keys) {
        $p = $ExpectedParserFor[$g]
        if (-not $expectedPerParser.ContainsKey($p)) { $expectedPerParser[$p] = 0 }
        $expectedPerParser[$p] += $expectedPerFormat[$g]
    }

    Say ""
    Say "    expected, from the producer's round-robin rotation:"
    foreach ($g in ($expectedPerFormat.Keys | Sort-Object)) {
        $want = $ExpectedParserFor[$g]
        Say ("      {0,-15} -> {1,-20} {2} record(s)" -f $g, $want, $expectedPerFormat[$g])
    }
    Say ""
    Say "    expected totals per parser:"
    foreach ($p in ($expectedPerParser.Keys | Sort-Object)) {
        $got = if ($breakdown.ContainsKey($p)) { $breakdown[$p] } else { 0 }
        $mark = if ($got -eq $expectedPerParser[$p]) { "ok" } else { "MISMATCH" }
        Say ("      {0,-22} expected {1,-4} got {2,-4} {3}" -f $p, $expectedPerParser[$p], $got, $mark)
    }
    Say ""

    Assert-That $settledB "phase B: all $sentB record(s) reached Silver"
    Assert-That ($docsB.Count -eq $sentB) "phase B: Silver holds exactly $sentB document(s) for this run ($($docsB.Count))"

    $unexpected = @($breakdown.Keys | Where-Object { $expectedPerParser.ContainsKey($_) -eq $false })
    Assert-That ($unexpected.Count -eq 0) "phase B: no record was handled by a parser outside the expected chain"
    if ($unexpected.Count -gt 0) { $unexpected | ForEach-Object { Note "  unexpected parser_id: $_" } }

    foreach ($p in $expectedPerParser.Keys) {
        $got = if ($breakdown.ContainsKey($p)) { $breakdown[$p] } else { 0 }
        Assert-That ($got -eq $expectedPerParser[$p]) "phase B: $p normalized $got, expected $($expectedPerParser[$p])"
    }

    # Tie the drain3 documents back to the prose the producer actually wrote,
    # so "drain3 handled the unknowns" is a per-document fact and not a
    # coincidence of two equal counts. Only meaningful on a clean mix: a
    # deliberate-failure record also lands under drain3, but as truncated
    # JSON with nothing for the masking instructions to mask.
    $drain3Docs = @($docsB | Where-Object { $_.parser_id -eq $Drain3ParserId })
    $wantDrain3 = if ($expectedPerParser.ContainsKey($Drain3ParserId)) { $expectedPerParser[$Drain3ParserId] } else { 0 }
    Assert-That ($drain3Docs.Count -eq $wantDrain3) "phase B: $wantDrain3 fallback record(s) are in Silver under $Drain3ParserId"
    if ($cleanMix) {
        $proseDocs = @($drain3Docs | Where-Object { $_.extensions.drain_template -like "*<IP>*" })
        Assert-That ($proseDocs.Count -eq $drain3Docs.Count) "phase B: every $Drain3ParserId document is a masked prose line, not something else"
    }

    $leaked = @($docsB | Where-Object { $_.parser_id -ne $Drain3ParserId -and $_.extensions.drain_template })
    Assert-That ($leaked.Count -eq 0) "phase B: no primary-parser document claims a drain3 template"

    $stream = Start-LiveStream -SourceId $sourceStream -FormatList $Formats `
        -RecordsPerSecond $StreamRate -Seconds $StreamSeconds
    if ($stream) {
        Wait-ForLiveStream $stream $StreamSeconds
        # Captured rather than left on the pipeline: Wait-For returns a
        # boolean, and an uncaptured one prints a bare `True` into the middle
        # of the report.
        $settledStream = Wait-ForSilver $sourceStream $stream.Expected "live stream: all $($stream.Expected) record(s) in Silver"
        Assert-That $settledStream "live stream: every streamed record reached Silver"
        if ($KeepStream) {
            Ok "leaving '$($stream.Name)' running at $StreamRate/s; stop it with:"
            Say "    docker rm -f $($stream.Name)"
        }
        else {
            Stop-LiveStream $stream
        }
    }

    # --------------------------------------------------------- reconciliation
    Step "Reconciliation"
    $bronzeB = Get-CountFor $BronzeIndex (Get-SourceTermQuery $BronzeIndex "source_id" $sourceMixed)
    $bronzeA = Get-CountFor $BronzeIndex (Get-SourceTermQuery $BronzeIndex "source_id" $sourceUnknown)
    $dlqAfterB = (Get-Os "$DlqIndex/_count").count

    Say "    bronze(A)=$bronzeA bronze(B)=$bronzeB  silver(A)=$($docsA.Count) silver(B)=$($docsB.Count)  dlq=$dlqAfterB"

    Assert-That ($bronzeA -eq $sentA) "phase A: Bronze recorded all $sentA raw event(s)"
    Assert-That ($bronzeB -eq $sentB) "phase B: Bronze recorded all $sentB raw event(s)"

    # raw == normalized + dlq, the invariant the whole pipeline rests on.
    Assert-That (($bronzeA + $bronzeB) -eq ($docsA.Count + $docsB.Count)) "no event was lost: bronze == silver across both phases"
    Assert-That ($dlqAfterB -eq $dlqBefore) "DLQ did not grow ($dlqBefore -> $dlqAfterB)"

    # The global delta is the primary evidence, but it trusts that nothing
    # else touched the DLQ during the run. Close that hole by asking the DLQ
    # directly whether it holds any raw event this run produced.
    $rawIds = @(@($docsA) + @($docsB) | Where-Object { $_.raw_event_id } | ForEach-Object { $_.raw_event_id })
    if ($rawIds.Count -eq 0) {
        Note "no raw_event_id lineage found; relying on the DLQ count delta alone"
    }
    elseif ($rawIds.Count -gt 2000) {
        Note "$($rawIds.Count) raw ids exceeds a safe terms query; relying on the DLQ count delta alone"
    }
    else {
        $dlqField = Resolve-SearchField $DlqIndex "raw_event_id"
        if (-not $dlqField) {
            # The DLQ index always exists (the orchestrator creates it at
            # startup), but dynamic fields only enter the mapping once a
            # document uses them. No mapped raw_event_id therefore means the
            # index holds no DLQ document at all, so nothing can reference
            # this run. Querying a null field name would 400 and turn a clean
            # run into a crash -- the opposite of what this check is for.
            Assert-That ($dlqBefore -eq 0) "DLQ is empty (raw_event_id unmapped), so nothing can reference this run"
        }
        else {
            $leakedDlq = (Invoke-Os "$DlqIndex/_count" @{ query = @{ terms = @{ $dlqField = $rawIds } } }).count
            Assert-That ($leakedDlq -eq 0) "no DLQ document references any raw event this run produced"
            if ($leakedDlq -gt 0) {
                $s = Invoke-Os "$DlqIndex/_search" @{
                    size = 3
                    query = @{ terms = @{ $dlqField = $rawIds } }
                    _source = @("raw_event_id", "classification", "status", "parsers_attempted", "reject_reason", "raw_payload")
                }
                foreach ($h in $s.hits.hits) {
                    Note "  $($h._source.classification) / $($h._source.status) via $($h._source.parsers_attempted -join ',')"
                    Note "  payload: $($h._source.raw_payload)"
                }
            }
        }
    }

    # ========================================================================
    # CROSS-SECTION CONSISTENCY
    # ========================================================================
    # Every section of the UI renders from GET /stats, so the way to catch a
    # regression here is to ask that payload and the raw indices whether they
    # tell the same story. Checking the indices alone would pass even if a
    # page were reading a field that no longer resolves -- which is exactly
    # the failure that let one page report zero events for a source the
    # dashboard said had hundreds.
    Step "Cross-section consistency"
    try {
        $stats = Get-UiStats
    }
    catch {
        Bad "could not read $ApiUrl/stats"
        Note $_.Exception.Message
        $stats = $null
    }

    if ($stats) {
        $bronzeTotal = (Get-Os "$BronzeIndex/_count").count
        $silverTotal = (Get-Os "$SilverIndex/_count").count
        $dlqTotal = (Get-Os "$DlqIndex/_count").count

        Say "    section totals as the UI sees them:"
        Say ("      {0,-22} {1}" -f "Events normalized", $stats.silver_events)
        Say ("      {0,-22} {1}" -f "Rejected (DLQ)", $stats.dlq_events)
        Say ("      {0,-22} {1}" -f "Input lines (Bronze)", $stats.bronze_events)
        Say ("      {0,-22} {1}" -f "Rescued by fallback", $stats.rescued)
        Say ("      {0,-22} {1}" -f "DLQ unresolved", $stats.dlq_unresolved)
        Say ("      {0,-22} {1}" -f "Classes / formats", "$($stats.classes.PSObject.Properties.Name.Count) / $($stats.formats.PSObject.Properties.Name.Count)")
        Say ""

        Assert-That ($stats.silver_events -eq $silverTotal) "the UI's 'Events normalized' matches Silver ($($stats.silver_events) == $silverTotal)"
        Assert-That ($stats.dlq_events -eq $dlqTotal) "the UI's 'Rejected' matches the DLQ ($($stats.dlq_events) == $dlqTotal)"
        Assert-That ($stats.bronze_events -eq $bronzeTotal) "the UI's 'Input lines' matches Bronze ($($stats.bronze_events) == $bronzeTotal)"

        # The invariant, restated against the numbers the operator sees.
        Assert-That ($stats.bronze_events -eq ($stats.silver_events + $stats.dlq_events)) `
            "bronze == silver + dlq as the UI reports it ($($stats.bronze_events) == $($stats.silver_events) + $($stats.dlq_events))"

        # A breakdown that does not add up to its own headline is the exact
        # shape of the old bug: charts tallied from a 50-row browser window
        # and labelled "total".
        $classSum = 0; foreach ($p in $stats.classes.PSObject.Properties) { $classSum += $p.Value }
        Assert-That ($classSum -eq $stats.silver_events) "the class breakdown sums to Events normalized ($classSum == $($stats.silver_events))"

        $formatSum = 0; foreach ($p in $stats.formats.PSObject.Properties) { $formatSum += $p.Value }
        Assert-That ($formatSum -eq $stats.bronze_events) "the format breakdown sums to Input lines ($formatSum == $($stats.bronze_events))"
        Assert-That ($stats.formats.PSObject.Properties.Name.Count -gt 0) "the format breakdown is not empty (it used to read 'unknown' for every event)"

        # Per-source rollup is derived from the data, so it must be able to
        # account for every event rather than only the registered ones.
        $perSourceSum = 0
        foreach ($row in $stats.per_source) { $perSourceSum += $row.normalized_events }
        Assert-That ($perSourceSum -eq $stats.silver_events) "the per-source rollup accounts for every event ($perSourceSum == $($stats.silver_events))"
    }

# ========================================================================
    # DLQ DEMO RECORDS
    # ========================================================================
    # Produce a few records that naturally land in the DLQ, so the queue
    # isn't empty when the operator opens the page. These use the dlq_demo
    # generator (JSON with no identity fields) and run through the real
    # pipeline like everything else -- no external seeding step is visible.
    if ($SeedDlq -eq 0) {
        Step "DLQ demo records"
        Note "-SeedDlq 0, leaving the DLQ empty"
    }
    else {
Step "DLQ demo records - $SeedDlq record(s) for inspection"
        $sentQ = Invoke-Producer -SourceId $sourceQuarantine -FormatList "dlq_demo" -N $SeedDlq -Label "DLQ demo" -Rate 1

        # These records go to the DLQ, not Silver, so we wait for the DLQ
        # count directly instead of waiting for Silver.
        $dlqNow = 0
        $okDlq = Wait-For "DLQ holds $SeedDlq demo record(s)" 120 {
            Invoke-Os "$DlqIndex/_refresh" $null | Out-Null
            $script:dlqNow = (Get-Os "$DlqIndex/_count").count
            return $script:dlqNow -ge $SeedDlq
        }
        Assert-That ($okDlq -and $script:dlqNow -ge $SeedDlq) "the DLQ holds at least $SeedDlq demo record(s) (found $($script:dlqNow))"

        $classifications = (Get-AllDocs $DlqIndex | ForEach-Object { $_.classification } | Group-Object | Sort-Object Count -Descending)
        foreach ($c in $classifications) { Say "    $($c.Name) = $($c.Count)" }

        $silverAfterSeed = (Get-Os "$SilverIndex/_count").count
        $bronzeAfterSeed = (Get-Os "$BronzeIndex/_count").count
        Assert-That ($bronzeAfterSeed -eq ($silverAfterSeed + $dlqNow)) `
            "bronze == silver + dlq still holds after demo records ($bronzeAfterSeed == $silverAfterSeed + $dlqNow)"
    }

    # ========================================================================
    # VERDICT
    # ========================================================================
    Step "Verdict"
    if ($script:failures.Count -eq 0) {
        Write-Host ""
        Write-Host "PASS" -ForegroundColor Green -NoNewline
        Write-Host " - $sentA unknown record(s) were all normalized by $Drain3ParserId at severity low,"
        Write-Host "       and $sentB mixed record(s) all reached Silver with the DLQ unmoved at $dlqBefore."
        if ($stream) {
            Write-Host "       The board started at zero and every section now reports the same totals."
        }
        if ($SeedDlq -gt 0) {
            Write-Host "       $SeedDlq record(s) are left quarantined for inspection - 'DLQ lines' across the UI reads $SeedDlq."
        }
    }
    else {
        Write-Host ""
        Write-Host "FAIL" -ForegroundColor Red -NoNewline
        Write-Host " - $($script:failures.Count) check(s) did not hold:"
        $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    }

    Write-Host ""
    Write-Host "Look at it yourself:" -ForegroundColor Cyan
    Write-Host "  UI              http://localhost:3000/events/normalized"
    Write-Host "  DLQ             http://localhost:3000/events/dlq"
    Write-Host "  API             http://localhost:8000/sources/$sourceMixed/stats"
    Write-Host "  these records   ulpf-silver, extensions.source_id = '$sourceUnknown' or '$sourceMixed'"
    Write-Host "  orchestrator    docker compose logs -f orchestrator"
    Write-Host ""
    Write-Host "  Reading a failure:"
    Write-Host "    drain3 at 0 in phase A  -> the sim image lost drain3, or 'unknown' was not sent."
    Write-Host "    a template without <IP> -> no machine-learned template was involved; check drain_miner masking."
    Write-Host "    DLQ grew                -> a record arrived with no src/dst/user/mac at all."
    Write-Host "                               no_recoverable_identity is an honest reject;"
    Write-Host "                               no_parser_match is a real miss."

    if ($Teardown) {
        Step "Teardown"
        (Invoke-Compose down --remove-orphans).Trim().Split("`n") | Select-Object -Last 2 | ForEach-Object { Say "    $($_.Trim())" }
    }
    else {
        Say "  Stack left up to poke at. Stop it with: docker compose down"
    }
}
catch {
    Write-Host ""
    Write-Host "Aborted: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "The stack has been left as-is so you can inspect it." -ForegroundColor Yellow
    exit 1
}
finally {
    Pop-Location
}

if ($script:failures.Count -gt 0) { exit 1 }
exit 0
