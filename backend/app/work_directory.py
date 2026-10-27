"""Current-format workspace identity and fixed-location startup preflight."""

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
from contextlib import closing

from .core.config import Settings


MARKER = '.douyincut-workspace.json'
INITIALIZING = '.douyincut-initializing.json'
FOLDERS = ('database', 'recordings', 'analysis', 'previews', 'exports', 'logs')
REQUIRED_COLUMNS = {
    'objects': {'kind', 'id', 'revision', 'body', 'created'},
    'jobs': {'id', 'kind', 'payload', 'state', 'progress', 'attempts', 'owner', 'lease',
             'error', 'result', 'created', 'updated', 'error_detail'},
    'cache': {'key', 'body'},
    'attempts': {'job_id', 'attempt', 'owner', 'started', 'ended', 'state', 'error', 'error_detail'},
    'live_monitors': {'id', 'url', 'name', 'live_state', 'last_checked', 'error', 'retry_blocked',
                      'current_session_id', 'created', 'updated'},
    'live_sessions': {'id', 'monitor_id', 'status', 'started', 'ended', 'ffmpeg_pid', 'end_time_source'},
    'live_segments': {'id', 'session_id', 'path', 'segment_index', 'status', 'started', 'ended',
                      'size_bytes', 'duration_us', 'asset_id', 'import_job_id'},
    'live_monitor_controls': {'monitor_id', 'auto_record', 'notify_live', 'record_requested', 'record_held', 'updated'},
    'live_product_events': {'id', 'session_id', 'event_key', 'promotion_id', 'product_name',
                           'explain_type', 'event_time', 'received_at', 'source'},
    'live_product_periods': {'id', 'session_id', 'promotion_id', 'product_name', 'started', 'ended'},
    'live_segment_product_clips': {'id', 'session_id', 'segment_id', 'segment_index', 'period_id',
                                  'promotion_id', 'product_name', 'start', 'end', 'path', 'status',
                                  'size_bytes', 'duration_us', 'asset_id', 'import_job_id', 'error'},
    'live_monitor_liveness': {'monitor_id', 'is_live', 'checked'},
    'live_session_current_product': {'session_id', 'promotion_id', 'product_name', 'event_time', 'received_at'},
    'live_session_products': {'session_id', 'promotion_id', 'product_name', 'updated_at'},
    'live_session_product_catalog_state': {'session_id', 'error_detail', 'updated'},
    'live_product_listener_state': {'session_id', 'status', 'detail', 'updated'},
}


def _path(value):
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError('工作目录必须使用原盘符和绝对路径。')
    return Path(os.path.abspath(path))


def _key(path):
    return os.path.normcase(str(_path(path)))


def _read_record(path):
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(record, dict) or record['format'] != 1 or not isinstance(record['id'], str)
                or str(uuid.UUID(record['id'])) != record['id']):
            raise ValueError('invalid current workspace identity')
        _path(record['path'])
        return record
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(f'无法读取当前格式的工作目录身份：{path}。请恢复原位置的数据或定位记录。') from exc


def _write_json(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.douyincut-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _writable(root):
    try:
        with tempfile.TemporaryFile(prefix='.douyincut-write-', dir=root) as probe:
            probe.write(b'workspace write check')
            probe.flush()
            os.fsync(probe.fileno())
    except OSError as exc:
        raise RuntimeError(f'工作目录无法写入，请检查权限及可用空间：{root}（{exc}）') from exc


def _validate_database(database, record):
    if not database.is_file():
        raise RuntimeError(f'关键数据库缺失：{database}。请恢复原数据库后重试，不会创建空库。')
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
            connection.execute('PRAGMA query_only=ON')
            if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise ValueError('数据库完整性检查失败')
            for table, columns in REQUIRED_COLUMNS.items():
                present = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
                if not columns <= present:
                    raise ValueError(f'缺少当前格式表或字段：{table}')
            identity = connection.execute('SELECT id,path,format FROM desktop_workspace').fetchall()
            if identity != [(record['id'], record['path'], 1)]:
                raise ValueError('数据库身份与工作目录清单不一致')
    except (sqlite3.Error, ValueError) as exc:
        raise RuntimeError(f'关键数据库不是完整的当前工作数据：{database}（{exc}）') from exc


def _validate(root, expected=None):
    if not root.is_dir():
        raise RuntimeError(f'原工作目录不可用，请恢复原盘符和路径后重试：{root}')
    record = _read_record(root / MARKER)
    # resolve() also rejects junction/symlink aliases to another location.
    if _key(root) != _key(record['path']) or _key(root.resolve()) != _key(root):
        raise RuntimeError(f'工作目录已搬迁、改名或改盘符。本版仅支持原位置：{record["path"]}')
    if expected and (record['id'] != expected['id'] or _key(root) != _key(expected['path'])):
        raise RuntimeError('工作数据身份与当前用户定位记录不一致。请恢复原位置的原始数据。')
    for folder in FOLDERS:
        if not (root / folder).is_dir():
            raise RuntimeError(f'工作数据目录缺失：{root / folder}。不会自动创建替代数据。')
        if _key((root / folder).resolve()) != _key(root / folder):
            raise RuntimeError(f'工作数据目录不能重定向至其他位置：{root / folder}')
    database = Settings(root).database
    _validate_database(database, record)
    _writable(root)
    for folder in FOLDERS:
        _writable(root / folder)
    try:
        # mode=rw refuses to create a missing database, even across a race.
        with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=1)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
    except sqlite3.Error as exc:
        raise RuntimeError(f'数据库无法写入，请检查权限或占用：{database}（{exc}）') from exc
    return record


def _recover_initial_intent(root):
    contents = list(root.iterdir())
    if len(contents) != 1:
        return
    candidate = contents[0]
    if not candidate.name.startswith('.douyincut-') or not candidate.is_file():
        return
    if _key(candidate.resolve()) != _key(candidate):
        return
    try:
        record = _read_record(candidate)
    except RuntimeError:
        return  # Incomplete or unknown content stays untouched and is rejected.
    if set(record) != {'format', 'id', 'path'} or _key(record['path']) != _key(root):
        return
    candidate.rename(root / INITIALIZING)


def _build_staging(staging, record):
    from .core.storage import Store
    from .live_monitoring import LiveStore

    staging.mkdir(exist_ok=True)
    if _key(staging.resolve()) != _key(staging):
        raise RuntimeError('初始化暂存目录不能重定向，保留全部内容。')
    for folder in FOLDERS:
        path = staging / folder
        if path.exists() and (not path.is_dir() or _key(path.resolve()) != _key(path)):
            raise RuntimeError(f'初始化暂存目录冲突：{path}。保留全部内容。')
    settings = Settings(staging)
    settings.prepare()
    LiveStore(Store(settings.database))
    with closing(sqlite3.connect(settings.database)) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS desktop_workspace (id TEXT PRIMARY KEY,path TEXT NOT NULL,format INTEGER NOT NULL)')
        identity = connection.execute('SELECT id,path,format FROM desktop_workspace').fetchall()
        if not identity:
            connection.execute('INSERT INTO desktop_workspace VALUES (?,?,1)', (record['id'], record['path']))
        elif identity != [(record['id'], record['path'], 1)]:
            raise RuntimeError('初始化数据库身份不一致，保留全部内容。')
        connection.commit()
        connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    _validate_database(settings.database, record)
    _write_json(staging / MARKER, record)


def _publish_initialization(root):
    record = _read_record(root / INITIALIZING)
    if _key(root) != _key(record['path']):
        raise RuntimeError(f'初始化目录已搬迁，请恢复原位置：{record["path"]}')
    staging = root / ('.douyincut-init-' + record['id'])
    if not (staging / MARKER).exists():
        # The journal owns this exact staging location before its first write.
        # Only unpublished initialization can be continued; published folders
        # without their staging marker are an integrity error, never rebuilt.
        if any((root / folder).exists() for folder in FOLDERS):
            raise RuntimeError('初始化内容发生冲突，保留全部数据，请检查原位置。')
        _build_staging(staging, record)
    if not staging.is_dir() or _key(staging.resolve()) != _key(staging):
        raise RuntimeError('当前初始化数据缺失，请恢复原位置的初始化内容后重试。')
    if _read_record(staging / MARKER) != record:
        raise RuntimeError('当前初始化身份不一致，拒绝覆盖目录。')
    # Exactly one side must own each folder. Never merge or overwrite contents.
    for folder in FOLDERS:
        locations = [path for path in (staging / folder, root / folder) if path.exists()]
        if len(locations) != 1 or not locations[0].is_dir():
            raise RuntimeError(f'初始化目录发生冲突：{folder}。保留所有数据，请检查权限或目录内容。')
        if _key(locations[0].resolve()) != _key(locations[0]):
            raise RuntimeError(f'初始化目录不能重定向：{folder}')
    database = Settings(staging if (staging / 'database').exists() else root).database
    _validate_database(database, record)
    for folder in FOLDERS:
        source = staging / folder
        if source.exists():
            source.rename(root / folder)
    (staging / MARKER).rename(root / MARKER)
    # A terminated atomic marker write may leave a temporary file. Preserve
    # all unexpected content; only remove staging when it is actually empty.
    if not any(staging.iterdir()):
        staging.rmdir()
    (root / INITIALIZING).unlink()
    return record


class WorkDirectoryRegistry:
    """The user locator is independent of the work root; never choose a fallback."""

    def __init__(self, config_path, *, application_dir=None):
        self.config_path = Path(config_path)
        self.application_dir = Path(application_dir).resolve() if application_dir else None

    def _outside_application(self, root):
        if self.application_dir is not None and (root == self.application_dir or self.application_dir in root.parents):
            raise RuntimeError('工作数据必须与程序文件分开，请选择安装目录以外的位置。')

    def inspect(self):
        if not self.config_path.exists():
            return {'state': 'selection_required', 'path': None, 'message': '请选择新的空目录，或重新关联原位置的当前格式工作目录。'}
        try:
            record = _read_record(self.config_path)
            self.require(record['path'])
            return {'state': 'ready', 'path': record['path'], 'message': ''}
        except (RuntimeError, OSError) as exc:
            try:
                path = json.loads(self.config_path.read_text(encoding='utf-8')).get('path')
            except (OSError, ValueError, AttributeError):
                path = None
            return {'state': 'repair_required', 'path': path, 'message': str(exc)}

    def require(self, root):
        expected = _read_record(self.config_path)
        root = _path(root)
        self._outside_application(root)
        if _key(root) != _key(expected['path']):
            raise RuntimeError(f'不能切换工作目录，请恢复原位置：{expected["path"]}')
        return _validate(root, expected)

    def select(self, selected):
        root = _path(selected)
        self._outside_application(root)
        if not root.is_dir():
            raise RuntimeError(f'请选择已存在的工作目录：{root}')
        if _key(root.resolve()) != _key(root):
            raise RuntimeError('工作目录不能使用链接或重定向位置，请选择原盘符上的实际绝对路径。')
        expected = _read_record(self.config_path) if self.config_path.exists() else None
        if expected and _key(root) != _key(expected['path']):
            raise RuntimeError(f'本版不提供工作区切换，请恢复原位置：{expected["path"]}')
        if expected is None and not (root / MARKER).exists() and not (root / INITIALIZING).exists():
            _recover_initial_intent(root)
        if (root / MARKER).exists():
            record = _validate(root, expected)
        elif (root / INITIALIZING).is_file() and expected is None:
            record = _publish_initialization(root)
            _validate(root)
        else:
            if expected:
                raise RuntimeError('原工作目录身份文件缺失，不会初始化替代空库。请恢复原始数据。')
            if any(root.iterdir()):
                raise RuntimeError('该目录包含文件，但没有当前格式工作数据身份。请选择空目录或原工作目录；不会迁移或覆盖文件。')
            _writable(root)
            record = {'format': 1, 'id': str(uuid.uuid4()), 'path': str(root)}
            # Persist identity and the exact staging path before its first
            # directory/database write, including interrupted partial builds.
            _write_json(root / INITIALIZING, record)
            record = _publish_initialization(root)
            _validate(root)
        _write_json(self.config_path, record)
        return {'state': 'ready', 'path': str(root), 'message': ''}
