function Remove-OwnedRuntimeTree([string]$Path, [string]$Parent) {
    $TreeRoot = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $OwnerRoot = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not $TreeRoot.StartsWith($OwnerRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "目录超出允许清理的位置：$TreeRoot" }
    if (-not (Test-Path -LiteralPath $TreeRoot)) { return }
    if ((Get-Item -LiteralPath $TreeRoot -Force).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw '拒绝递归清理运行时根目录链接' }
    function Remove-Entry([IO.FileSystemInfo]$Entry) {
        $EntryPath = [IO.Path]::GetFullPath($Entry.FullName)
        if ($EntryPath -ine $TreeRoot -and -not $EntryPath.StartsWith($TreeRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '清理目标超出当前运行时目录' }
        $Directory = $Entry.Attributes.HasFlag([IO.FileAttributes]::Directory)
        if ($Entry.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
            # Unlink a junction/symlink itself; never enumerate its external target.
            if ($Directory) { [IO.Directory]::Delete($EntryPath) } else { [IO.File]::Delete($EntryPath) }
        } elseif ($Directory) {
            foreach ($Child in ([IO.DirectoryInfo]$Entry).GetFileSystemInfos()) { Remove-Entry $Child }
            [IO.Directory]::Delete($EntryPath)
        } else {
            if ($Entry.IsReadOnly) { $Entry.IsReadOnly = $false }
            [IO.File]::Delete($EntryPath)
        }
    }
    Remove-Entry ([IO.DirectoryInfo]$TreeRoot)
}
