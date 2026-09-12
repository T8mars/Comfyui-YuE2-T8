"""Copy, verify and atomically switch user RVC locations; originals remain backups."""
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .io import atomic_json, sha256, within
from .rvc_library import locations, storage_settings

KINDS = ('projects', 'datasets', 'voices')
OWNER = '.yue2-rvc-migration.json'


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def targets(root: Path, values: dict, *, source=None) -> dict:
    if not isinstance(values, dict) or set(values) - set(KINDS):
        raise ValueError('目录设置必须包含训练项目、素材、音色库路径')
    current = source or locations(root)
    result = {}
    for kind in KINDS:
        value = str(values.get(kind, current[kind])).strip()
        path = Path(value).expanduser() if value else root / 'userdata/rvc' / kind
        path = (path if path.is_absolute() else root / path).resolve()
        if path == Path(path.anchor) or path == (root/'userdata/rvc').resolve() or path in root.resolve().parents or path == root.resolve():
            raise ValueError('请选择独立的数据文件夹，不能使用磁盘根目录或整合包根目录')
        for protected in ('app','vendor','runtime','models','downloads','outputs','uploads','logs','cache'):
            if _overlap(path, (root/protected).resolve()):
                raise ValueError('音色数据目录不能与程序、基础模型或任务目录重叠')
        for other in KINDS:
            if _overlap(path, current[other]) and not (other == kind and path == current[kind]):
                raise ValueError('新目录不能与现有素材、训练或音色目录重叠')
        result[kind] = path
    if any(_overlap(result[a], result[b]) for i,a in enumerate(KINDS) for b in KINDS[i+1:]):
        raise ValueError('三个数据目录不能相同或互相嵌套')
    return result


def inventory(directory: Path) -> list[dict]:
    if directory.is_symlink() or directory.is_junction():
        raise ValueError('迁移源目录不能是链接')
    if not directory.exists():
        return []
    records = []
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(parent)/name
            if path.is_symlink() or path.is_junction():
                raise ValueError(f'迁移目录中含链接，请先整理为实际文件：{path}')
        for name in files:
            path = Path(parent)/name
            if path == directory/OWNER:
                marker = json.loads(path.read_text())
                if not isinstance(marker, dict) or not re.fullmatch('[a-f0-9]{32}', str(marker.get('id', ''))) or not isinstance(marker.get('target'), str):
                    raise ValueError('目录中存在名称冲突的迁移标记，请先改名后重试')
                continue
            info = path.stat()
            records.append({'file': path.relative_to(directory).as_posix(), 'size': info.st_size,
                            'mtime_ns': info.st_mtime_ns})
    return sorted(records, key=lambda value: value['file'])


def preview(root: Path, values: dict) -> dict:
    old = locations(root)
    new = targets(root, values)
    changes, needed = [], {}
    for kind in KINDS:
        if old[kind] == new[kind]:
            continue
        target = new[kind]
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError(f'目标目录需要为空，以免覆盖已有文件：{target}')
        files = inventory(old[kind])
        size = sum(value['size'] for value in files)
        parent = target.parent
        while not parent.exists():
            parent = parent.parent
        device = parent.stat().st_dev
        needed[device] = (parent, needed.get(device, (parent, 0))[1] + size)
        changes.append({'kind':kind, 'source':str(old[kind]), 'target':str(target),
                        'bytes':size, 'files':len(files)})
    for parent, size in needed.values():
        if shutil.disk_usage(parent).free < size + 100*1024**2:
            raise ValueError(f'目标磁盘空间不足，需要约 {size/1024**3:.2f} GB 和校验余量：{parent}')
    return {'changes':changes, 'bytes':sum(value['bytes'] for value in changes),
            'originals_retained':True, 'locations':{k:str(v) for k,v in new.items()}}


def migrate(root: Path, request: dict, ctx) -> dict:
    state_file = ctx.job_dir/'artifacts/storage-migration.json'
    previous = request.get('resume_from')
    if previous:
        source_job = within(root/'outputs/jobs', Path(previous))
        record = json.loads((source_job/'artifacts/storage-migration.json').read_text(encoding='utf-8'))
        if Path(record['root']).resolve() != root.resolve():
            raise ValueError('迁移记录不属于当前整合包')
        atomic_json(state_file, record)
    else:
        info = preview(root, request.get('directories', {}))
        if not info['changes']:
            return {'locations': info['locations'], 'backups': [], 'message': '目录未发生变化'}
        ident = uuid.uuid4().hex
        record = {'schema':1, 'id':ident, 'root':str(root.resolve()), 'before':storage_settings(root),
                  'locations':info['locations'], 'changes':info['changes'], 'phase':'copying'}
        for change in record['changes']:
            target = Path(change['target'])
            change['stage'] = str(target.parent/('.yue2-rvc-' + ident + '-' + change['kind']))
            change['inventory'] = inventory(Path(change['source']))
        atomic_json(state_file, record)
    before = record['before']
    if not re.fullmatch('[a-f0-9]{32}', str(record['id'])):
        raise ValueError('迁移记录 ID 无效')
    current = storage_settings(root)
    committed = current.get('migration_id') == record['id']
    if not committed and current != before:
        raise ValueError('目录设置已改变，不能继续旧迁移；原文件和迁移副本均已保留')
    # Revalidate recovery paths against the original configured locations.
    old = {}
    for kind in KINDS:
        path = Path(before.get(kind+'_directory') or root/'userdata/rvc'/kind).expanduser()
        old[kind] = (path if path.is_absolute() else root/path).resolve()
    expected = targets(root, record['locations'], source=old)
    if sorted(change['kind'] for change in record['changes']) != sorted(kind for kind in KINDS if expected[kind] != old[kind]):
        raise ValueError('迁移记录与目录变化不一致')
    if set(record['locations']) != set(KINDS):
        raise ValueError('迁移记录中的目录不完整')
    if committed and any(locations(root)[kind] != expected[kind] for kind in KINDS):
        raise ValueError('当前目录设置与已提交迁移不一致')
    copied = 0
    total = sum(item['bytes'] for item in record['changes'])
    for change in record['changes']:
        kind = change['kind']
        source, target, stage = map(Path, (change['source'],change['target'],change['stage']))
        if (source != old[kind] or target != expected[kind] or
                stage != target.parent/('.yue2-rvc-'+record['id']+'-'+kind)):
            raise ValueError('迁移记录包含无效路径')
        destination = target if target.is_dir() and (target/OWNER).is_file() else stage
        if committed:
            destination = target
        if destination.is_symlink() or destination.is_junction():
            raise ValueError('迁移暂存目录不能是链接')
        if destination.exists() and (destination/OWNER).is_file():
            owner = json.loads((destination/OWNER).read_text())
            if owner != {'id':record['id'],'target':str(target)}:
                raise ValueError('目标目录不属于当前迁移')
        elif not committed:
            if destination.exists():
                raise ValueError('迁移暂存目录已被占用')
            destination.mkdir(parents=True)
            atomic_json(destination/OWNER, {'id':record['id'],'target':str(target)})
        snapshot = [{k:v for k,v in entry.items() if k != 'sha256'} for entry in change['inventory']]
        if not committed and inventory(source) != snapshot:
            raise ValueError('迁移期间原目录发生改变；原文件已保留，请选择另一个空目标目录重新迁移')
        for entry in change['inventory']:
            if not committed:
                ctx.check_cancelled()
            original = within(source, source/entry['file'])
            output = within(destination, destination/entry['file'])
            if committed:
                if not output.is_file() or sha256(output) != entry.get('sha256'):
                    raise ValueError('已迁移文件缺失或损坏，请从原目录备份恢复')
                continue
            digest = sha256(original)
            if not output.is_file() or sha256(output) != digest:
                output.parent.mkdir(parents=True, exist_ok=True)
                temporary = output.with_name(output.name+'.migration-tmp')
                with original.open('rb') as inp, temporary.open('wb') as out:
                    while block := inp.read(8*1024**2):
                        ctx.check_cancelled()
                        out.write(block)
                shutil.copystat(original, temporary)
                if sha256(temporary) != digest:
                    raise ValueError('文件复制校验失败，原目录保持不变')
                os.replace(temporary, output)
            entry['sha256'] = digest
            copied += entry['size']
            ctx.update('rvc_storage_copy', completed=copied, total=total,
                       message=f'正在校验并复制 {kind}：{entry["file"]}')
        if not committed:
            # All prior copied hashes remain in the journal if interrupted during promotion.
            atomic_json(state_file, record)
    if not committed:
        for change in record['changes']:
            source = Path(change['source'])
            original_inventory = [{k:v for k,v in entry.items() if k != 'sha256'} for entry in change['inventory']]
            if inventory(source) != original_inventory:
                raise ValueError('原目录在校验期间发生改变，尚未切换设置')
        ctx.check_cancelled()
        ctx.update('rvc_storage_switch', message='所有文件已通过校验，正在切换目录；原文件保留为备份')
        for change in record['changes']:
            target, stage = Path(change['target']), Path(change['stage'])
            if (target/OWNER).is_file():
                continue
            if target.exists():
                target.rmdir()  # Only an empty user-selected destination can be replaced.
            os.replace(stage, target)
        new = {**before, **{k+'_directory':str(v) for k,v in expected.items()}, 'migration_id':record['id']}
        history = list(before.get('previous_dataset_directories', []))
        if old['datasets'] != expected['datasets'] and str(old['datasets']) not in history:
            history.append(str(old['datasets']))
        new['previous_dataset_directories'] = history
        atomic_json(root/'userdata/rvc/settings.json', new)
    record['phase'] = 'complete'
    atomic_json(state_file, record)
    return {'locations':record['locations'], 'backups':[value['source'] for value in record['changes']],
            'migration_committed':True, 'message':'目录已切换，原目录保留为备份；确认新位置可用后可自行清理原目录'}
