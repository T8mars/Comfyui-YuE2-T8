"""Locate the Windows shell without depending on user PATH configuration."""
import ctypes
from pathlib import Path


def powershell_executable() -> str:
    # The updater has a standalone equivalent because app files may not yet be installed.
    windows = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetWindowsDirectoryW(windows, len(windows))
    if not length or length >= len(windows):
        raise OSError('无法确定 Windows 系统目录，无法启动 Windows PowerShell')
    for system in ('Sysnative', 'System32'):
        executable = Path(windows.value)/system/'WindowsPowerShell/v1.0/powershell.exe'
        if executable.is_file():
            return str(executable)
    raise FileNotFoundError('找不到 Windows PowerShell 系统组件，请修复 Windows 后重试；无需修改 PATH')
