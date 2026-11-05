"""Windows process tests: never depend on the host's PowerShell PATH entry."""
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.apply_update import apply_files, powershell_executable
from app.yue2_app.windows_runtime import powershell_executable as service_powershell

ROOT = Path(__file__).resolve().parents[1]


class PowerShellResolverTests(unittest.TestCase):
    def test_absolute_system_paths_ignore_path_and_report_missing_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            windows = Path(temporary)/'Windows 系统'
            system = windows/'System32/WindowsPowerShell/v1.0/powershell.exe'
            native = windows/'Sysnative/WindowsPowerShell/v1.0/powershell.exe'
            def get_windows(buffer, size):
                buffer.value = str(windows)
                return len(buffer.value)
            kernel = SimpleNamespace(GetWindowsDirectoryW=get_windows)
            with mock.patch.object(ctypes,'windll',SimpleNamespace(kernel32=kernel),create=True), \
                    mock.patch.dict(os.environ,{'PATH':''}):
                for resolver in (powershell_executable,service_powershell):
                    with self.assertRaisesRegex(FileNotFoundError,'Windows PowerShell'):
                        resolver()
                system.parent.mkdir(parents=True); system.write_bytes(b'system')
                self.assertTrue(all(resolver()==str(system) for resolver in (powershell_executable,service_powershell)))
                native.parent.mkdir(parents=True); native.write_bytes(b'native')
                self.assertTrue(all(resolver()==str(native) for resolver in (powershell_executable,service_powershell)))
                self.assertEqual(os.environ['PATH'],'')


@unittest.skipUnless(os.name == 'nt', 'Windows launcher process test')
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='yue2-launcher-')
        self.kit = Path(self.temporary.name)/'音乐 工作台'
        runtime = self.kit/'runtime'
        runtime.mkdir(parents=True)
        shutil.copy2(sys.executable,runtime/'python.exe')
        # Embedded Python keeps its stdlib ZIP and extension modules beside the EXE;
        # regular CI Python uses PYTHONHOME's Lib/DLLs instead.
        for pattern in ('*.dll','python*.zip','*.pyd'):
            for path in Path(sys.executable).parent.glob(pattern):
                shutil.copy2(path,runtime/path.name)
        package = self.kit/'app/yue2_app'
        package.mkdir(parents=True)
        (package.parent/'__init__.py').write_text('',encoding='utf-8')
        (package/'__init__.py').write_text('__version__ = "path-fixture"\n',encoding='utf-8')
        self.token = uuid.uuid4().hex
        fixture = '''import json,os,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from . import __version__
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/test-stop/TOKEN':
            self.send_response(200); self.end_headers(); self.wfile.write(b'bye')
            threading.Thread(target=self.server.shutdown,daemon=True).start()
        elif self.path == '/api/health':
            body=json.dumps({'ok':True,'version':__version__,'root':os.environ['YUE2_HOME'],
                             'queued':0,'current_job':None}).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(body)))
            self.end_headers(); self.wfile.write(body)
        else:
            self.send_error(404)
    def log_message(self,*args): pass
if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--host'); parser.add_argument('--port',type=int)
    args=parser.parse_args()
    with ThreadingHTTPServer((args.host,args.port),Handler) as server: server.serve_forever()
'''.replace('TOKEN',self.token)
        (package/'service.py').write_text(fixture,encoding='utf-8')
        (self.kit/'scripts').mkdir()
        for name in ('start_local.ps1','start_webui.ps1','windows_powershell.cmd'):
            shutil.copy2(ROOT/'scripts'/name,self.kit/'scripts'/name)
        for name in ('启动本地整合包.bat','整合包启动.bat','start_webui.bat'):
            shutil.copy2(ROOT/name,self.kit/name)
        shutil.copy2(ROOT/'YuE2-T8.exe',self.kit/'YuE2-T8.exe')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); self.port=sock.getsockname()[1]
        self.environment = {**os.environ,'PATH':'','PYTHONHOME':sys.base_prefix,
                            'PYTHONNOUSERSITE':'1','YUE2_NO_PAUSE':'1'}
        self.environment.pop('PYTHONPATH',None)
        self.addCleanup(self.cleanup_fixture)

    def health(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/health',timeout=2) as response:
            return json.load(response)

    def run_logged(self, command, timeout=50):
        # Detached Windows children may inherit pipe handles even when their
        # own output is redirected. Capture to a file so EOF cannot hang a test.
        log = self.kit/('command-'+uuid.uuid4().hex+'.log')
        with log.open('wb') as stream:
            result = subprocess.run(command,cwd=self.kit,env=self.environment,
                                    stdout=stream,stderr=stream,timeout=timeout)
        result.stdout = log.read_text(encoding='utf-8',errors='replace')
        result.stderr = ''
        return result

    def cleanup_fixture(self):
        try:
            if Path(self.health()['root']).resolve() == self.kit.resolve():
                urllib.request.urlopen(f'http://127.0.0.1:{self.port}/test-stop/{self.token}',timeout=2).close()
        except OSError:
            pass
        # Wait only for the owned fixture's relay/HTTP process to release files.
        for attempt in range(30):
            try:
                self.temporary.cleanup()
                return
            except PermissionError:
                time.sleep(.1)
        self.temporary.cleanup()

    def launch(self, executable=None):
        result = self.run_logged([str(executable or self.kit/'YuE2-T8.exe'),
            '--no-browser','--no-pause','--no-switch','--port',str(self.port)])
        diagnostics = result.stdout+'\n'+result.stderr
        if result.returncode:
            direct = self.run_logged([powershell_executable(),'-NoProfile','-ExecutionPolicy','Bypass',
                '-File',str(self.kit/'scripts/start_local.ps1'),'-NoBrowser','-NoSwitch','-Port',str(self.port)],timeout=45)
            diagnostics += '\nDirect PowerShell diagnostic:\n'+direct.stdout+'\n'+direct.stderr
        self.assertEqual(result.returncode,0,diagnostics)
        self.assertEqual(Path(self.health()['root']).resolve(),self.kit.resolve())
        self.assertTrue(self.health()['ok'])

    def test_committed_exe_starts_repeatedly_after_update_with_empty_path(self):
        self.launch()
        self.launch()
        stage = self.kit/'cache/updates/test'
        stage.mkdir(parents=True)
        shutil.copy2(ROOT/'YuE2-T8.exe',stage/'YuE2-T8.exe')
        (stage/'scripts/launcher').mkdir(parents=True)
        shutil.copy2(ROOT/'scripts/launcher/YuE2Launcher.cs',stage/'scripts/launcher/YuE2Launcher.cs')
        (self.kit/'YuE2-T8.exe').write_bytes(b'pre-update launcher')
        backup, records = apply_files(stage,self.kit,'path-regression')
        self.assertEqual((backup/'YuE2-T8.exe').read_bytes(),b'pre-update launcher')
        self.assertTrue(any(row['file']=='YuE2-T8.exe' for row in records))
        self.launch()

    def test_bat_entry_points_and_x86_launcher_do_not_search_path(self):
        cmd = str(Path(os.environ['SystemRoot'])/'System32/cmd.exe')
        for name in ('启动本地整合包.bat','整合包启动.bat','start_webui.bat'):
            command = f'"{cmd}" /d /s /c ""{self.kit/name}" -NoBrowser -NoSwitch -Port {self.port}"'
            result = self.run_logged(command)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            self.assertEqual(Path(self.health()['root']).resolve(),self.kit.resolve())
        compiler = Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework/v4.0.30319/csc.exe'
        x86 = self.kit/'x86.exe'
        subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x86',f'/out:{x86}',
                        str(ROOT/'scripts/launcher/YuE2Launcher.cs')],check=True,capture_output=True)
        self.launch(x86)

    def test_missing_component_has_no_path_fallback(self):
        assembly = str(self.kit/'YuE2-T8.exe').replace("'","''")
        missing = str(self.kit/'not-a-windows-directory').replace("'","''")
        script = (f"$a=[Reflection.Assembly]::LoadFile('{assembly}');"
                  "$m=$a.GetType('YuE2Launcher').GetMethod('ResolvePowerShell',[Reflection.BindingFlags]'NonPublic,Static');"
                  f"if($null -ne $m.Invoke($null,@('{missing}'))){{exit 1}};exit 0")
        result = subprocess.run([powershell_executable(),'-NoProfile','-Command',script],
                                env=self.environment,capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))


if __name__ == '__main__':
    unittest.main()
