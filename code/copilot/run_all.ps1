# Starts the co-pilot parts, each in its own window. Run from code/copilot:
#   .\run_all.ps1                  # real transcript + real engine
#   .\run_all.ps1 -MockTranscript  # scripted transcript instead of part 1
#   .\run_all.ps1 -MockEngine      # scripted suggestions instead of part 2 (GUI dev)
#   .\run_all.ps1 -MockTranscript -Speed 3
param([switch]$MockTranscript, [switch]$MockEngine, [double]$Speed = 1)

$here = $PSScriptRoot
function Start-Part($title, $module, $extra = @()) {
    Start-Process powershell -WorkingDirectory $here -ArgumentList @(
        "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='$title'; python -m $module $($extra -join ' ')")
}

if ($MockEngine) {
    Start-Part "2 mock engine" "mocks.mock_engine" @("--loop", "--speed", $Speed)
} else {
    if ($MockTranscript) { Start-Part "1 mock transcript" "mocks.mock_transcript" @("--loop", "--speed", $Speed) }
    else                 { Start-Part "1 transcript" "transcript.main" }
    Start-Part "2 engine" "engine.main"
}

# TODO(Mahreen): start the GUI here once it exists.
Write-Host "Started. Watch a stream with: python -m mocks.tap transcript | suggestions"
Write-Host "Dev preview of the GUI: open mocks\preview.html in a browser."
