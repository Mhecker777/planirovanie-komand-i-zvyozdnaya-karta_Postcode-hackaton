"""
Слой хранения (Auth_redact.md, раздел 4). SQLite: один файл на диске,
атомарные транзакции, без внешних зависимостей сверх стандартной библиотеки.

Формат снапшота: 5 DataFrame сериализуются через to_json(orient='split') —
компактнее repr/pickle, не исполняет произвольный код при чтении (в отличие
от pickle), человекочитаемо для отладки.
"""
import io
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

SCHEMA_VERSION = 1

# Раздел 17, вопрос 9 — лимит версий НЕ был согласован явно. Значение по
# умолчанию ниже — консервативная защита от бесконтрольного роста state.db;
# обсудить с командой и при необходимости поднять/убрать лимит.
MAX_VERSIONS = 100

# Раздел 17, вопрос 4 — окно дедупликации access_granted тоже не было
# согласовано явно ("хз" в исходном обсуждении). 5 минут — рабочее
# предположение: достаточно, чтобы не заводить запись на каждый rerender
# Streamlit при активной работе в одной вкладке, но не настолько большое,
# чтобы скрыть повторный заход после перерыва.
ACCESS_LOG_DEDUP_WINDOW = timedelta(minutes=5)

AUTO_BACKUP_EVERY = 10  # раздел 17, вопрос 8 — согласовано явно

DF_KEYS = ['tasks_df', 'estimates_df', 'deps_df', 'engineers_df', 'history_df']


def _stringify(value) -> Optional[str]:
    return None if value is None else str(value)


class StateStorage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS snapshots (
                    version    INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    user_id    TEXT NOT NULL,
                    user_name  TEXT NOT NULL,
                    comment    TEXT,
                    payload    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_log (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at     TEXT NOT NULL,
                    user_id        TEXT NOT NULL,
                    user_name      TEXT NOT NULL,
                    user_role      TEXT NOT NULL,
                    event_category TEXT NOT NULL,
                    event_type     TEXT NOT NULL,
                    section        TEXT,
                    granted        INTEGER,
                    entity_type    TEXT,
                    entity_id      TEXT,
                    field          TEXT,
                    old_value      TEXT,
                    new_value      TEXT,
                    comment        TEXT,
                    version        INTEGER,
                    FOREIGN KEY (version) REFERENCES snapshots(version)
                );
                
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_event_time     ON event_log(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_event_user     ON event_log(user_id);
                CREATE INDEX IF NOT EXISTS idx_event_category ON event_log(event_category);
                CREATE INDEX IF NOT EXISTS idx_event_entity   ON event_log(entity_type, entity_id);
            ''')
            conn.commit()

    # ---------------- СНАПШОТЫ ----------------

    def save_snapshot(self, dataframes: dict, user, comment: str = None) -> int:
        missing = [k for k in DF_KEYS if k not in dataframes]
        if missing:
            raise ValueError(f'В dataframes не хватает ключей: {missing}')

        payload = {
            'schema_version': SCHEMA_VERSION,
            'data': {k: dataframes[k].to_json(orient='split', date_format='iso') for k in DF_KEYS},
        }
        now = datetime.utcnow().isoformat()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                'INSERT INTO snapshots (created_at, user_id, user_name, comment, payload) VALUES (?, ?, ?, ?, ?)',
                (now, user.id, user.name, comment or '', json.dumps(payload)),
            )
            version = cur.lastrowid
            conn.commit()

        self._prune_old_versions()
        if version % AUTO_BACKUP_EVERY == 0:
            self._auto_backup(version)
        return version

    def _prune_old_versions(self) -> None:
        with closing(self._connect()) as conn:
            count = conn.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
            if count > MAX_VERSIONS:
                to_delete = count - MAX_VERSIONS
                conn.execute(
                    'DELETE FROM snapshots WHERE version IN '
                    '(SELECT version FROM snapshots ORDER BY version ASC LIMIT ?)',
                    (to_delete,),
                )
                conn.commit()

    def _auto_backup(self, version: int) -> None:
        backup_path = f'{self.db_path}.backup_v{version}'
        try:
            shutil.copy2(self.db_path, backup_path)
        except OSError:
            pass  # бэкап — best-effort, не должен ронять само сохранение

    @staticmethod
    def _payload_to_dataframes(payload_json: str) -> dict:
        payload = json.loads(payload_json)
        data = payload.get('data', payload)  # запас на случай будущей смены формата
        return {k: pd.read_json(io.StringIO(v), orient='split') for k, v in data.items()}

    def load_latest(self):
        """Возвращает (dataframes, version) последнего снапшота или None, если снапшотов ещё нет."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                'SELECT version, payload FROM snapshots ORDER BY version DESC LIMIT 1'
            ).fetchone()
        if row is None:
            return None
        return self._payload_to_dataframes(row['payload']), row['version']

    def load_version(self, version: int) -> Optional[dict]:
        with closing(self._connect()) as conn:
            row = conn.execute('SELECT payload FROM snapshots WHERE version = ?', (version,)).fetchone()
        if row is None:
            return None
        return self._payload_to_dataframes(row['payload'])

    def list_versions(self, limit: int = 50) -> pd.DataFrame:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                '''SELECT s.version, s.created_at, s.user_name, s.comment,
                          (SELECT COUNT(*) FROM event_log e
                           WHERE e.version = s.version AND e.event_category = 'change') AS changes
                   FROM snapshots s ORDER BY s.version DESC LIMIT ?''',
                (limit,),
            ).fetchall()
        return pd.DataFrame([dict(r) for r in rows])

    # ---------------- ЖУРНАЛ СОБЫТИЙ ----------------

    def log_session(self, user, event_type: str, comment: str = None) -> None:
        self._insert_event(user, 'session', event_type, comment=comment)

    def log_access(self, user, section: str, granted: bool, dedup_cache: dict = None) -> None:
        """
        dedup_cache — обычный dict, который вызывающий код хранит между
        рендерами (например, в st.session_state) — так storage.py не
        зависит от Streamlit напрямую и его легко тестировать отдельно.
        Дедуплицируем только УСПЕШНЫЕ проверки (см. константу выше);
        отказы пишем каждый раз — для аудита важна каждая попытка.
        """
        if granted and dedup_cache is not None:
            cache_key = (user.id, section)
            now = datetime.utcnow()
            last = dedup_cache.get(cache_key)
            if last is not None and now - last < ACCESS_LOG_DEDUP_WINDOW:
                return
            dedup_cache[cache_key] = now
        self._insert_event(
            user, 'access', 'access_granted' if granted else 'access_denied',
            section=section, granted=granted,
        )

    def log_change(self, version: Optional[int], user, action: str, entity_type: str,
                   entity_id: str = None, field: str = None, old_value=None, new_value=None,
                   comment: str = None) -> None:
        self._insert_event(
            user, 'change', action, entity_type=entity_type, entity_id=entity_id,
            field=field, old_value=_stringify(old_value), new_value=_stringify(new_value),
            comment=comment, version=version,
        )

    def log_batch(self, version: Optional[int], user, changes: list) -> None:
        """changes: список dict с ключами action, entity_type, entity_id, field, old_value, new_value, comment."""
        for ch in changes:
            self.log_change(
                version, user,
                action=ch.get('action', 'update'),
                entity_type=ch.get('entity_type', ''),
                entity_id=ch.get('entity_id'),
                field=ch.get('field'),
                old_value=ch.get('old_value'),
                new_value=ch.get('new_value'),
                comment=ch.get('comment'),
            )

    def _insert_event(self, user, category, event_type, section=None, granted=None,
                      entity_type=None, entity_id=None, field=None, old_value=None,
                      new_value=None, comment=None, version=None) -> None:
        now = datetime.utcnow().isoformat()
        with closing(self._connect()) as conn:
            conn.execute(
                '''INSERT INTO event_log
                   (created_at, user_id, user_name, user_role, event_category, event_type,
                    section, granted, entity_type, entity_id, field, old_value, new_value, comment, version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (now, user.id, user.name, user.role, category, event_type,
                 section, None if granted is None else int(granted), entity_type, entity_id,
                 field, old_value, new_value, comment, version),
            )
            conn.commit()

    def get_events(self, filters: dict = None, limit: int = 500) -> pd.DataFrame:
        filters = filters or {}
        clauses, params = [], []
        for col in ('user_id', 'user_role', 'event_category', 'event_type', 'section', 'entity_type'):
            values = filters.get(col)
            if values:
                placeholders = ','.join('?' * len(values))
                clauses.append(f'{col} IN ({placeholders})')
                params.extend(values)
        if filters.get('entity_id_search'):
            clauses.append('entity_id LIKE ?')
            params.append(f"%{filters['entity_id_search']}%")
        if filters.get('date_from'):
            clauses.append('created_at >= ?')
            params.append(filters['date_from'])
        if filters.get('date_to'):
            clauses.append('created_at <= ?')
            params.append(filters['date_to'])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f'SELECT * FROM event_log {where} ORDER BY created_at DESC LIMIT ?',
                (*params, limit),
            ).fetchall()
        return pd.DataFrame([dict(r) for r in rows])
    # ---------------- RUNTIME-СОСТОЯНИЕ (симуляция времени, факт, календарь) ----------------

    def save_runtime(self, state: dict) -> None:
        """Сохраняет произвольный JSON-совместимый dict под ключами.
        Используется для персистентности UI-состояния: какой спринт завершён,
        какие задачи отмечены как выполненные, дата старта квартала."""
        now = datetime.utcnow().isoformat()
        with closing(self._connect()) as conn:
            for k, v in state.items():
                conn.execute(
                    'INSERT INTO runtime_state (key, value, updated_at) VALUES (?, ?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
                    (str(k), json.dumps(v, default=str), now),
                )
            conn.commit()

    def load_runtime(self) -> dict:
        """Возвращает сохранённое runtime-состояние. Пустой dict, если ничего нет."""
        with closing(self._connect()) as conn:
            rows = conn.execute('SELECT key, value FROM runtime_state').fetchall()
        result = {}
        for row in rows:
            try:
                result[row['key']] = json.loads(row['value'])
            except (json.JSONDecodeError, TypeError):
                continue
        return result

    def clear_runtime(self) -> None:
        """Сбрасывает runtime-состояние (используется при откате/сбросе)."""
        with closing(self._connect()) as conn:
            conn.execute('DELETE FROM runtime_state')
            conn.commit()

    # ---------------- ЭКСПОРТ / ИМПОРТ БД ЦЕЛИКОМ ----------------

    def export_backup(self, path: str) -> None:
        shutil.copy2(self.db_path, path)

    def import_backup(self, path: str) -> None:
        shutil.copy2(path, self.db_path)
