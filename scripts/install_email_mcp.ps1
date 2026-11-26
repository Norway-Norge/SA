$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$targetDir = Join-Path $repoRoot '.secureagent/mcp/email-send'
$revision = '2b8e8ca7ff591c5605810df9c912d46214c9bac8'
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
foreach ($filename in @('index.js', 'package.json', 'package-lock.json')) {
    $url = "https://raw.githubusercontent.com/TT-Wang/dsh-assembler/$revision/generated/email-send/$filename"
    Invoke-WebRequest -Uri $url -OutFile (Join-Path $targetDir $filename)
}
# 使用系统解析后的地址连接，同时保留原主机名供 TLS 证书验证。
$serverPath = Join-Path $targetDir 'index.js'
$source = [System.IO.File]::ReadAllText($serverPath)
$oldTls = 'options.tls = { rejectUnauthorized: args.tlsRejectUnauthorized };'
$newTls = 'options.tls = { rejectUnauthorized: args.tlsRejectUnauthorized, servername: args.tlsServername };'
$oldField = "    host: z.string().optional().describe('SMTP 服务器主机名，transport=smtp 时必填，如 smtp.gmail.com'),"
$newField = $oldField + "`n    tlsServername: z.string().optional().describe('TLS 证书验证使用的服务器主机名'),"
$oldVerify = "    host: z.string().describe('SMTP 服务器主机名，如 smtp.gmail.com'),"
$newVerify = $oldVerify + "`n    tlsServername: z.string().optional().describe('TLS 证书验证使用的服务器主机名'),"
if (-not $source.Contains($oldTls) -or -not $source.Contains($oldField) -or -not $source.Contains($oldVerify)) { throw '邮件 MCP Server 版本不匹配。' }
$source = $source.Replace($oldTls, $newTls).Replace($oldField, $newField).Replace($oldVerify, $newVerify)
[System.IO.File]::WriteAllText($serverPath, $source, [System.Text.UTF8Encoding]::new($false))
& npm ci --ignore-scripts --no-audit --no-fund --prefix $targetDir
if ($LASTEXITCODE -ne 0) { throw '邮件 MCP Server 依赖安装失败。' }
Write-Host '邮件 MCP Server 已安装。'
