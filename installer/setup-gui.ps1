param(
  [Parameter(Mandatory = $true)]
  [string]$SourceRoot
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Stop"
$source = (Resolve-Path $SourceRoot).Path.TrimEnd('\')
$defaultTarget = Join-Path $env:LOCALAPPDATA "Programs\Warsh ERP"

function Write-Log {
  param(
    [System.Windows.Forms.TextBox]$LogBox,
    [string]$Message
  )
  $LogBox.AppendText($Message + "`r`n")
  $LogBox.ScrollToCaret()
}

function New-Shortcut {
  param(
    [string]$ShortcutPath,
    [string]$TargetPath,
    [string]$WorkingDirectory,
    [string]$IconPath
  )
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($ShortcutPath)
  $shortcut.TargetPath = $TargetPath
  $shortcut.WorkingDirectory = $WorkingDirectory
  if ($IconPath -and (Test-Path $IconPath)) {
    $shortcut.IconLocation = $IconPath
  }
  $shortcut.Save()
}

function Try-NewShortcut {
  param(
    [string]$ShortcutPath,
    [string]$TargetPath,
    [string]$WorkingDirectory,
    [string]$IconPath,
    [System.Windows.Forms.TextBox]$LogBox
  )
  try {
    $shortcutDir = Split-Path $ShortcutPath -Parent
    if (!(Test-Path $shortcutDir)) {
      New-Item -ItemType Directory -Path $shortcutDir | Out-Null
    }
    New-Shortcut -ShortcutPath $ShortcutPath -TargetPath $TargetPath -WorkingDirectory $WorkingDirectory -IconPath $IconPath
    Write-Log $LogBox "Shortcut created: $ShortcutPath"
    return $true
  }
  catch {
    Write-Log $LogBox ("Shortcut skipped: " + $_.Exception.Message)
    return $false
  }
}

function Copy-App {
  param(
    [string]$From,
    [string]$To,
    [System.Windows.Forms.TextBox]$LogBox
  )

  if (!(Test-Path $To)) {
    New-Item -ItemType Directory -Path $To | Out-Null
  }

  $excludeDirs = @(
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "react-src\node_modules",
    "desktop-data",
    "postgres-data",
    "dist-electron",
    "__pycache__"
  )

  $excludeFiles = @("*.pyc", "*.pyo", "*.log")
  $args = @(
    "`"$From`"",
    "`"$To`"",
    "/E",
    "/NFL",
    "/NDL",
    "/NJH",
    "/NJS",
    "/NP"
  )

  foreach ($dir in $excludeDirs) {
    $args += "/XD"
    $args += "`"$From\$dir`""
  }
  foreach ($file in $excludeFiles) {
    $args += "/XF"
    $args += $file
  }

  Write-Log $LogBox "Copying files to $To ..."
  $process = Start-Process -FilePath "robocopy.exe" -ArgumentList $args -Wait -PassThru -WindowStyle Hidden
  if ($process.ExitCode -gt 7) {
    throw "File copy failed. Robocopy exit code: $($process.ExitCode)"
  }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Warsh ERP Setup"
$form.StartPosition = "CenterScreen"
$form.Size = New-Object System.Drawing.Size(760, 540)
$form.MinimumSize = New-Object System.Drawing.Size(720, 500)
$form.BackColor = [System.Drawing.Color]::FromArgb(246, 244, 239)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Install Warsh ERP"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 18, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(26, 24)
$form.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = "Choose the install folder. Setup will copy files and create shortcuts."
$subtitle.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(90, 98, 112)
$subtitle.AutoSize = $true
$subtitle.Location = New-Object System.Drawing.Point(30, 68)
$form.Controls.Add($subtitle)

$pathLabel = New-Object System.Windows.Forms.Label
$pathLabel.Text = "Install path"
$pathLabel.AutoSize = $true
$pathLabel.Location = New-Object System.Drawing.Point(30, 110)
$form.Controls.Add($pathLabel)

$pathBox = New-Object System.Windows.Forms.TextBox
$pathBox.Text = $defaultTarget
$pathBox.Location = New-Object System.Drawing.Point(30, 134)
$pathBox.Size = New-Object System.Drawing.Size(580, 28)
$form.Controls.Add($pathBox)

$browseBtn = New-Object System.Windows.Forms.Button
$browseBtn.Text = "Browse"
$browseBtn.Location = New-Object System.Drawing.Point(620, 132)
$browseBtn.Size = New-Object System.Drawing.Size(90, 31)
$form.Controls.Add($browseBtn)

$desktopCheck = New-Object System.Windows.Forms.CheckBox
$desktopCheck.Text = "Create Desktop shortcut"
$desktopCheck.Checked = $true
$desktopCheck.AutoSize = $true
$desktopCheck.Location = New-Object System.Drawing.Point(30, 176)
$form.Controls.Add($desktopCheck)

$startCheck = New-Object System.Windows.Forms.CheckBox
$startCheck.Text = "Create Start Menu shortcut"
$startCheck.Checked = $true
$startCheck.AutoSize = $true
$startCheck.Location = New-Object System.Drawing.Point(260, 176)
$form.Controls.Add($startCheck)

$openCheck = New-Object System.Windows.Forms.CheckBox
$openCheck.Text = "Open app after install"
$openCheck.Checked = $true
$openCheck.AutoSize = $true
$openCheck.Location = New-Object System.Drawing.Point(500, 176)
$form.Controls.Add($openCheck)

$installBtn = New-Object System.Windows.Forms.Button
$installBtn.Text = "Install"
$installBtn.BackColor = [System.Drawing.Color]::FromArgb(15, 110, 86)
$installBtn.ForeColor = [System.Drawing.Color]::White
$installBtn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$installBtn.Location = New-Object System.Drawing.Point(30, 215)
$installBtn.Size = New-Object System.Drawing.Size(120, 38)
$form.Controls.Add($installBtn)

$closeBtn = New-Object System.Windows.Forms.Button
$closeBtn.Text = "Close"
$closeBtn.Location = New-Object System.Drawing.Point(160, 215)
$closeBtn.Size = New-Object System.Drawing.Size(100, 38)
$form.Controls.Add($closeBtn)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ScrollBars = "Vertical"
$logBox.ReadOnly = $true
$logBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$logBox.Location = New-Object System.Drawing.Point(30, 275)
$logBox.Size = New-Object System.Drawing.Size(680, 190)
$logBox.RightToLeft = [System.Windows.Forms.RightToLeft]::No
$form.Controls.Add($logBox)

$browseBtn.Add_Click({
  $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
  $dialog.Description = "Choose Warsh ERP install folder"
  $dialog.SelectedPath = Split-Path $pathBox.Text -Parent
  if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    $pathBox.Text = Join-Path $dialog.SelectedPath "Warsh ERP"
  }
})

$closeBtn.Add_Click({ $form.Close() })

$installBtn.Add_Click({
  try {
    $installBtn.Enabled = $false
    $target = $pathBox.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($target)) {
      throw "Please choose an install path."
    }

    Copy-App -From $source -To $target -LogBox $logBox

    $launcher = Join-Path $target "WarshERP.cmd"
    if (!(Test-Path $launcher)) {
      throw "Launcher file was not copied: $launcher"
    }

    $electronIcon = Join-Path $target "node_modules\electron\dist\electron.exe"

    if ($desktopCheck.Checked) {
      $desktopPath = [Environment]::GetFolderPath("DesktopDirectory")
      if ([string]::IsNullOrWhiteSpace($desktopPath)) {
        $desktopPath = [Environment]::GetFolderPath("Desktop")
      }
      $desktopShortcut = Join-Path $desktopPath "Warsh ERP.lnk"
      $null = Try-NewShortcut -ShortcutPath $desktopShortcut -TargetPath $launcher -WorkingDirectory $target -IconPath $electronIcon -LogBox $logBox
    }

    if ($startCheck.Checked) {
      $startDir = Join-Path ([Environment]::GetFolderPath("Programs")) "Warsh ERP"
      if (!(Test-Path $startDir)) { New-Item -ItemType Directory -Path $startDir | Out-Null }
      $startShortcut = Join-Path $startDir "Warsh ERP.lnk"
      $null = Try-NewShortcut -ShortcutPath $startShortcut -TargetPath $launcher -WorkingDirectory $target -IconPath $electronIcon -LogBox $logBox
    }

    Write-Log $logBox "Install complete."
    [System.Windows.Forms.MessageBox]::Show("Warsh ERP installed successfully.", "Warsh ERP Setup", "OK", "Information") | Out-Null

    if ($openCheck.Checked) {
      Start-Process -FilePath $launcher -WorkingDirectory $target
    }
  }
  catch {
    Write-Log $logBox ("ERROR: " + $_.Exception.Message)
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, "Setup Error", "OK", "Error") | Out-Null
  }
  finally {
    $installBtn.Enabled = $true
  }
})

[void]$form.ShowDialog()
