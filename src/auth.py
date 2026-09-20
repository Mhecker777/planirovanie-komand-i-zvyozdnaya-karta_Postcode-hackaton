"""
Слой аккаунтов (Auth_redact.md, раздел 3).

ВАЖНО (см. раздел 18 «Что НЕ входит в scope»): это НЕ настоящая
аутентификация — нет сессий с тайм-аутом, нет хэширования паролей, пароли
захардкожены в открытом виде. Это сознательно упрощённый демо-слой для
показа разграничения доступа на защите; в проде заменяется на SSO/OAuth.
"""
from dataclasses import dataclass
from typing import Optional

import streamlit as st


@dataclass(frozen=True)
class User:
    id: str
    name: str
    role: str


ROLES = {
    'admin': {
        'view_plan': True, 'edit_data': True,
        'manage_users': True, 'view_audit': True,
    },
    'planner': {
        'view_plan': True, 'edit_data': True,
        'manage_users': False, 'view_audit': True,
    },
    'viewer': {
        'view_plan': True, 'edit_data': False,
        'manage_users': False, 'view_audit': False,
    },
}

# Пароли захардкожены по решению команды (раздел 17, вопросы 2 и 12: admin/admin,
# viewer/Viewr — сохранено буквально, как согласовано, включая регистр).
# Пароль для planner НЕ был согласован явно — задан по аналогии с двумя
# другими ('planner'), это предположение и его стоит подтвердить с командой.
ACCOUNTS = {
    'admin': {'password': 'admin', 'name': 'Администратор', 'role': 'admin'},
    'planner': {'password': 'planner', 'name': 'Планировщик', 'role': 'planner'},
    'viewer': {'password': 'Viewr', 'name': 'Наблюдатель', 'role': 'viewer'},
}

DEFAULT_USER = User(id='admin', name='Администратор', role='admin')


def has_permission(user: Optional[User], permission: str) -> bool:
    if user is None:
        return False
    return bool(ROLES.get(user.role, {}).get(permission, False))


def get_current_user() -> User:
    """Текущий вошедший пользователь из session_state (по умолчанию — admin)."""
    if 'auth_user' not in st.session_state:
        st.session_state['auth_user'] = DEFAULT_USER
    return st.session_state['auth_user']


def set_current_user(user: User) -> None:
    st.session_state['auth_user'] = user


def try_login(account_id: str, password: str) -> Optional[User]:
    """Проверяет пароль; при успехе возвращает User, иначе None."""
    account = ACCOUNTS.get(account_id)
    if account is None or account['password'] != password:
        return None
    return User(id=account_id, name=account['name'], role=account['role'])


def require_permission(permission: str, section: str, stop_on_denied: bool = False) -> bool:
    """
    Проверяет право и логирует попытку доступа (разрешённую или отказанную)
    через storage.log_access, если storage доступен в session_state.

    ОТКЛОНЕНИЕ ОТ ДОКУМЕНТА: раздел 6.2 предполагает безусловный st.stop()
    при отказе. Технически st.stop() останавливает выполнение ВСЕГО скрипта
    Streamlit, а не только текущего `with tab:` блока — значит, если
    «Управление данными» стоит раньше «Журнала событий» по порядку вкладок,
    отказ в первой вкладке помешает отрисоваться второй в этом же прогоне,
    даже если у роли есть право на неё. Поэтому по умолчанию функция просто
    возвращает False и показывает сообщение, а вызывающий код сам решает не
    рендерить содержимое (`if require_permission(...): ...`) — это не мешает
    соседним вкладкам. Параметр stop_on_denied оставлен для мест, где
    поведение "жёсткий стоп" всё же нужно (например, отдельная страница).
    """
    user = get_current_user()
    granted = has_permission(user, permission)
    storage = st.session_state.get('storage')
    if storage is not None:
        dedup_cache = st.session_state.setdefault('_access_log_cache', {})
        storage.log_access(user, section, granted, dedup_cache=dedup_cache)
    if not granted:
        st.error(
            f'⛔ Недостаточно прав для раздела «{section}». '
            f'Требуется право «{permission}», у роли «{user.role}» его нет.'
        )
        if stop_on_denied:
            st.stop()
    return granted
