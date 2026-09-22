import os
import sys
from html import escape
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, '..')) if os.path.basename(CURRENT_DIR) == 'src' else CURRENT_DIR

# Надёжный импорт локальных модулей независимо от того, запускается ли app.py
# из корня проекта или из src/.
src_dir = os.path.join(PROJECT_ROOT, 'src')
for import_dir in (CURRENT_DIR, src_dir, PROJECT_ROOT):
    if import_dir not in sys.path:
        sys.path.insert(0, import_dir)

import auth
import data_management
from storage import StateStorage
from parser import DataParser
from scheduler import SmartScheduler
from analytics import StarMapAnalytics


st.set_page_config(page_title='ПочтаТех — Планировщик', page_icon='📦', layout='wide')

# Font Awesome 6 Free — иконки вместо emoji в интерфейсе.
FONT_AWESOME_CSS = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css"

st.markdown(
    f"""
    <link rel="stylesheet" href="{FONT_AWESOME_CSS}">
    <style>
        .fa-ui-icon {{
            display: inline-block;
            width: 1.25em;
            text-align: center;
            margin-right: 0.35em;
        }}
        /* Иконки для вкладок: st.tabs не принимает HTML внутри label. */
        .stTabs [data-baseweb="tab"]:nth-child(1)::before,
        .stTabs [data-baseweb="tab"]:nth-child(2)::before,
        .stTabs [data-baseweb="tab"]:nth-child(3)::before,
        .stTabs [data-baseweb="tab"]:nth-child(4)::before,
        .stTabs [data-baseweb="tab"]:nth-child(5)::before,
        .stTabs [data-baseweb="tab"]:nth-child(6)::before,
        .stTabs [data-baseweb="tab"]:nth-child(7)::before {{
            font-family: "Font Awesome 6 Free";
            font-weight: 900;
            margin-right: 0.45em;
        }}
        .stTabs [data-baseweb="tab"]:nth-child(1)::before {{ content: "\\f0ae"; }}
        .stTabs [data-baseweb="tab"]:nth-child(2)::before {{ content: "\\f0c0"; }}
        .stTabs [data-baseweb="tab"]:nth-child(3)::before {{ content: "\\f080"; }}
        .stTabs [data-baseweb="tab"]:nth-child(4)::before {{ content: "\\f005"; }}
        .stTabs [data-baseweb="tab"]:nth-child(5)::before {{ content: "\\f071"; }}
        .stTabs [data-baseweb="tab"]:nth-child(6)::before {{ content: "\\f085"; }}
        .stTabs [data-baseweb="tab"]:nth-child(7)::before {{ content: "\\f1da"; }}

        /* Отключаем Font Awesome иконки для ВЛОЖЕННЫХ st.tabs (подвкладок).
           Иначе CSS nth-child применяется и к подвкладкам, и они получают
           иконки главных вкладок — визуально не совпадает с содержимым.
           Подвкладки рендерятся как .stTabs внутри другого .stTabs. */
        .stTabs .stTabs [data-baseweb="tab"]::before {{
            content: none !important;
            display: none !important;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)


def fa_icon(name: str, extra_class: str = '') -> str:
    """Возвращает HTML Font Awesome иконки."""
    classes = f"fa-solid {name} fa-ui-icon {extra_class}".strip()
    return f'<i class="{classes}" aria-hidden="true"></i>'


def fa_text(icon: str, text: str) -> str:
    """Удобная комбинация иконки и текста для markdown/HTML."""
    return f"{fa_icon(icon)}{text}"


def render_unscheduled_table(rows: list[dict]) -> str:
    """Рендерит таблицу с Font Awesome иконками в колонке «Категория»."""
    headers = ['Задача', 'Rung', 'Инициатива', 'Команда', 'Категория', 'Причина']
    parts = ['<div class="fa-table-wrap"><table class="fa-table"><thead><tr>']
    parts.extend(f'<th>{escape(h)}</th>' for h in headers)
    parts.append('</tr></thead><tbody>')

    icon_map = {
        'Ждёт зависимость': 'fa-link',
        'Не хватает SP команды': 'fa-chart-column',
        'Дефицит специалистов': 'fa-user-xmark',
        'Нет специалистов в штате': 'fa-user-slash',
        'Неполное покрытие ролей': 'fa-circle-half-stroke',
        'Некорректная смета': 'fa-circle-exclamation',
        'Не указана команда': 'fa-people-group',
        'Цикл зависимостей': 'fa-arrows-rotate',
        'Растянута (Сплиттинг)': 'fa-arrows-left-right',
        'Отменена заказчиком': 'fa-ban',
        'Не взята в квартал': 'fa-circle-minus',
        'Завершена': 'fa-circle-check',
        'Перевод ресурса': 'fa-right-left',
        'Другое': 'fa-circle-question',
    }
    for row in rows:
        category = str(row.get('Категория', 'Другое'))
        icon = fa_icon(icon_map.get(category, 'fa-circle-question'))
        parts.append('<tr>')
        for h in headers:
            if h == 'Категория':
                parts.append(f'<td>{icon}{escape(category)}</td>')
            else:
                parts.append(f'<td>{escape(str(row.get(h, "")))}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


st.markdown(
    '''
<style>
html, body, [class*="css"] { font-size: 17px; }
div[data-testid="stMetric"] {
    background-color: rgba(120, 120, 120, 0.06);
    border: 1px solid rgba(120, 120, 120, 0.18);
    border-radius: 10px;
    padding: 14px 16px 10px 16px;
}
div[data-testid="stMetricValue"] { font-size: 2.05rem; }
div[data-testid="stMetricLabel"] { font-size: 0.95rem; opacity: 0.85; }
.stTabs [data-baseweb="tab"] { font-size: 1.05rem; padding: 10px 20px; }
h1 { font-size: 2.1rem !important; }
h3, h4, h5 { margin-top: 0.6rem; }
.fa-table-wrap { overflow-x: auto; }
.fa-table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
.fa-table th, .fa-table td { padding: 8px 10px; border-bottom: 1px solid rgba(128,128,128,.2); text-align: left; vertical-align: top; }
.fa-table th { font-weight: 600; }
.fa-table .fa-ui-icon { margin-right: 0.35em; }

/* ─── Скролл вкладок ─────────────────────────────────────────── */
/* 7 длинных вкладок («Управление данными», «Журнал событий» и др.)
   не влезают в узкое окно. Разрешаем горизонтальный скролл вместо
   обрезания и запрещаем flex-перенос на вторую строку. */
.stTabs [data-baseweb="tab-list"] {
    overflow-x: auto !important;
    overflow-y: hidden !important;
    flex-wrap: nowrap !important;
    scrollbar-width: thin;
    scrollbar-color: rgba(128,128,128,.45) transparent;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
    height: 6px;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-track {
    background: transparent;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-thumb {
    background: rgba(128,128,128,.45);
    border-radius: 3px;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-thumb:hover {
    background: rgba(128,128,128,.7);
}
.stTabs [data-baseweb="tab"] {
    flex-shrink: 0 !important;
    white-space: nowrap;
}
/* ────────────────────────────────────────────────────────────── */
</style>
''',
    unsafe_allow_html=True,
)

st.markdown(f"# {fa_text('fa-box', 'ПочтаТех: Квартальное планирование (PI)')}", unsafe_allow_html=True)

with st.expander('Как читать этот дашборд — короткая справка'):
    st.markdown(
        '''
- **Bus Factor** — сколько человек в компании или команде владеют технологией.
- **SP** — Story Points задачи. Commitment резервируется только один раз, при первом включении задачи.
- **HH** — трудозатраты в человеко-часах. Именно HH расходуются по ролям внутри спринта.
- **«Растянута (Сплиттинг)»** — задача не помещается целиком по HH в одном спринте и продолжает выполняться в следующем; SP повторно не списываются.
- **«Реорганизация (Перевод)»** — временное использование свободных часов нужной роли из другой команды.
- **Зазор между зависимостями** — задача-потомок стартует не раньше следующего спринта после завершения предшественника.
- **Say/Do Ratio** — фактически завершённые SP / SP из исходного baseline-плана конкретного спринта.
- **Алерты** — срыв задачи, фактически наблюдаемый каскадный сдвиг и дефицит роли в конкретном спринте.
'''
    )


def resolve_data_path() -> str:
    candidates = [
        os.path.join(PROJECT_ROOT, 'data', 'dataset.xlsx'),
        os.path.join(PROJECT_ROOT, 'dataset.xlsx'),
        os.path.join(CURRENT_DIR, 'data', 'dataset.xlsx'),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(
        'Не найден dataset.xlsx. Ожидался один из путей: ' + ', '.join(candidates)
    )


DATA_PATH = resolve_data_path()
DB_PATH = os.path.join(os.path.dirname(DATA_PATH), 'state.db')


@st.cache_resource(show_spinner=False)
def get_storage(db_path: str) -> StateStorage:
    return StateStorage(db_path)


storage = get_storage(DB_PATH)
# ---- Восстанавливаем runtime-состояние из предыдущей сессии ----
if not st.session_state.get('_runtime_loaded'):
    _stored_runtime = storage.load_runtime()
    st.session_state['_stored_current_sprint'] = _stored_runtime.get('current_time_sprint')
    st.session_state['_stored_fact'] = _stored_runtime.get('fact_sprint_done', {}) or {}
    st.session_state['_stored_quarter_start_iso'] = _stored_runtime.get('quarter_start_value')
    # Флаг «игнорировать 1С-роли» тоже персистим между сессиями. По умолчанию True
    # (старое поведение — 1С-роли игнорируются, задачи планируются по остальным).
    st.session_state['_stored_ignore_1c_roles'] = bool(
        _stored_runtime.get('ignore_1c_roles', True)
    )
    st.session_state['_ignore_1c_roles_value'] = st.session_state['_stored_ignore_1c_roles']
    # Запрещённые переводы: список пар [donor_team, role]. Хранится в
    # runtime_state — переживает F5 и перезапуск. Раньше был эфемерным
    # session_state и обнулялся при каждой перезагрузке.
    _stored_forbidden_raw = _stored_runtime.get('forbidden_donors', []) or []
    st.session_state['_stored_forbidden_donors'] = _stored_forbidden_raw
    st.session_state['_forbidden_donors_value'] = {
        (str(pair[0]), str(pair[1]))
        for pair in _stored_forbidden_raw
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
    }
    # Принудительные задачи вне плана: {task_id: {'sprints': [...], 'comment': '...'}}.
    st.session_state['_forced_schedule_value'] = dict(
        _stored_runtime.get('forced_schedule', {}) or {}
    )
    st.session_state['_runtime_loaded'] = True
st.session_state['storage'] = storage

user = auth.get_current_user()

if not st.session_state.get('_session_logged'):
    storage.log_session(user, 'login')
    st.session_state['_session_logged'] = True

# ---- Панель входа (сайдбар) ----
st.sidebar.markdown('---')
st.sidebar.markdown(f"### {fa_text('fa-user-lock', 'Аккаунт')}", unsafe_allow_html=True)
st.sidebar.caption(f'Вы вошли как **{user.name}** (роль: {user.role})')

with st.sidebar.expander('Сменить роль'):
    account_ids = list(auth.ACCOUNTS.keys())
    default_idx = account_ids.index(user.id) if user.id in account_ids else 0
    picked_id = st.selectbox(
        'Пользователь:',
        account_ids,
        index=default_idx,
        key='login_role_pick',
    )
    pwd = st.text_input('Пароль:', type='password', key='login_pwd')
    if st.button('Войти', key='login_btn'):
        new_user = auth.try_login(picked_id, pwd)
        if new_user is not None:
            auth.set_current_user(new_user)
            storage.log_session(new_user, 'role_switch', comment=f'Переключение на {picked_id}')
            st.rerun()
        else:
            st.error('Неверный пароль.')

# ---- Загрузка/восстановление состояния ----
if 'working_dfs' not in st.session_state:
    restored = storage.load_latest()
    if restored is not None:
        dfs, version = restored
    else:
        parser = DataParser(DATA_PATH)
        dfs = {
            'tasks_df': parser.get_tasks(), 'estimates_df': parser.get_estimates(),
            'deps_df': parser.get_dependencies(), 'engineers_df': parser.get_engineers(),
            'history_df': parser.get_team_history(),
            'team_overrides_df': pd.DataFrame(
                columns=['team_id', 'sp_capacity_per_sprint', 'comment']
            ),
        }
        version = storage.save_snapshot(dfs, user=auth.DEFAULT_USER, comment='Первичная загрузка из dataset.xlsx')
    st.session_state['working_dfs'] = dfs
    st.session_state['committed_dfs'] = {k: v.copy() for k, v in dfs.items()}
    st.session_state['committed_version'] = version
    st.session_state['pending_changes'] = []
    st.session_state['dirty'] = False

# ===== КАЛЕНДАРЬ =====
if 'quarter_start_value' not in st.session_state:
    restored_iso = st.session_state.get('_stored_quarter_start_iso')
    if restored_iso:
        try:
            st.session_state['quarter_start_value'] = pd.to_datetime(restored_iso).date()
        except (ValueError, TypeError):
            st.session_state['quarter_start_value'] = (
                date.today() - timedelta(days=date.today().weekday())
            )
    else:
        st.session_state['quarter_start_value'] = (
            date.today() - timedelta(days=date.today().weekday())
        )
st.sidebar.markdown('---')
st.sidebar.markdown(f"### {fa_text('fa-calendar-days', 'Календарь квартала')}", unsafe_allow_html=True)
_new_quarter_start = st.sidebar.date_input(
    'Дата старта квартала (Спринт 1):',
    value=st.session_state['quarter_start_value'],
    key='quarter_start_input',
)

if _new_quarter_start != st.session_state['quarter_start_value']:
    st.session_state['quarter_start_value'] = _new_quarter_start
    st.rerun()

quarter_start = st.session_state['quarter_start_value']

sprint_date_ranges = {
    i: (
        quarter_start + timedelta(days=14 * (i - 1)),
        quarter_start + timedelta(days=14 * (i - 1) + 13),
    )
    for i in range(1, 7)
}


def sprint_label(i: int) -> str:
    start, end = sprint_date_ranges[i]
    return f'Спринт {i} ({start:%d.%m}–{end:%d.%m})'


sprint_dates_for_sched = {i: sprint_date_ranges[i] for i in range(1, 7)}

# ===== НАСТРОЙКИ ПЛАНИРОВАНИЯ =====
st.sidebar.markdown('---')
st.sidebar.markdown(f"### {fa_text('fa-user-shield', 'Настройки планирования')}", unsafe_allow_html=True)

# Подтверждённое (persisted) значение — источник истины для планировщика.
_persisted_1c = bool(st.session_state.get('_ignore_1c_roles_value', True))

# Колбэки смены режима. Выполняются ДО инстанцирования виджета на
# следующем rerun, поэтому присвоение session_state с ключом виджета
# безопасно (иначе Streamlit бросил бы "cannot be modified after widget
# is instantiated").
def _confirm_1c_toggle():
    new_val = bool(st.session_state.get('ignore_1c_roles_cb', True))
    st.session_state['_ignore_1c_roles_value'] = new_val
    st.session_state['_stored_ignore_1c_roles'] = new_val

def _cancel_1c_toggle():
    # Возвращаем чекбокс к подтверждённому значению.
    st.session_state['ignore_1c_roles_cb'] = st.session_state['_ignore_1c_roles_value']

# Гарантируем, что ключ виджета есть в session_state до его создания.
# Это единственный способ подсунуть виджету persisted-значение без
# конфликта с параметром value= (Streamlit запрещает передавать оба
# одновременно и ругается предупреждением).
if 'ignore_1c_roles_cb' not in st.session_state:
    st.session_state['ignore_1c_roles_cb'] = _persisted_1c

_cb_1c_value = st.sidebar.checkbox(
    'Игнорировать 1С-роли при планировании',
    key='ignore_1c_roles_cb',
    # value= НЕ передаём: значение уже сидит в session_state.
    help=(
        'В датасете нет ни одного 1С-инженера, поэтому задачи с 1С-ролями '
        'нельзя выполнить полностью.\n\n'
        '• Галочка ВКЛючена: 1С-роли игнорируются, задача планируется по '
        'остальным ролям.\n'
        '• Галочка СНЯТА: задача с любой 1С-ролью вообще не берётся в план. '
        'Она попадёт в раздел «Не поместились в квартал» с категорией '
        '«Нет специалистов в штате» и причиной вида «Нет специалистов '
        'для ролей: Аналитик 1С».'
    ),
)

# Если текущее состояние чекбокса расходится с подтверждённым — показываем
# блок подтверждения. Планировщик до подтверждения использует прежнее
# значение, чтобы случайный клик не перекроил план.
if _cb_1c_value != _persisted_1c:
    _mode_now = 'игнорировать 1С-роли' if _persisted_1c else 'снимать 1С-задачи с плана'
    _mode_new = 'игнорировать 1С-роли' if _cb_1c_value else 'снимать 1С-задачи с плана'
    st.sidebar.warning(
        f'⚠️ **Подтверждение смены режима**\n\n'
        f'Сейчас: {_mode_now}.\n\n'
        f'Станет: **{_mode_new}**.\n\n'
        f'План будет пересчитан только после подтверждения.'
    )
    _col_ok, _col_cancel = st.sidebar.columns(2)
    _col_ok.button(
        '✅ Подтвердить',
        key='confirm_1c_toggle',
        on_click=_confirm_1c_toggle,
        use_container_width=True,
        type='primary',
    )
    _col_cancel.button(
        '↩️ Отмена',
        key='cancel_1c_toggle',
        on_click=_cancel_1c_toggle,
        use_container_width=True,
    )
    # До подтверждения — прежний режим.
    ignore_1c_roles = _persisted_1c
else:
    ignore_1c_roles = _persisted_1c

if ignore_1c_roles:
    st.sidebar.caption('Режим: 1С-роли **игнорируются**, задачи планируются.')
else:
    st.sidebar.caption('Режим: задачи с 1С-ролями **снимаются с плана**.')

# ===== ПЛАНИРОВЩИКИ =====
working_dfs = st.session_state['working_dfs']
committed_dfs = st.session_state['committed_dfs']

sched = SmartScheduler(
    **working_dfs,
    sprint_dates=sprint_dates_for_sched,
    ignore_1c_roles=ignore_1c_roles,
)
analytics = StarMapAnalytics(engineers_df=working_dfs['engineers_df'])
baseline_sched = SmartScheduler(
    **committed_dfs,
    sprint_dates=sprint_dates_for_sched,
    ignore_1c_roles=ignore_1c_roles,
)
b_schedule, b_statuses, b_logs, b_alerts, b_kpis, b_burned = baseline_sched.run_smart_planning()

bf_df = analytics.get_bus_factor_and_training()
roles_df = analytics.get_critical_roles_shortage()

# ===== УПРАВЛЯЕМАЯ РЕОРГАНИЗАЦИЯ (ссылка на вкладку) =====
st.sidebar.markdown('---')
st.sidebar.markdown(f"### {fa_text('fa-screwdriver-wrench', 'Управление реорганизацией')}", unsafe_allow_html=True)

# Запрещённые переводы — источник истины из runtime_state. Пользователь
# управляет ими через «Управление данными → 🔄 Переводы», здесь только
# статус и подсказка. Раньше чекбоксы были прямо в сайдбаре, но они не
# персистили: после F5 список обнулялся и план пересчитывался без запретов.
forbidden_donors = set(st.session_state.get('_forbidden_donors_value', set()))

st.sidebar.caption(
    'Полный список переводов, запрет и восстановление — '
    'во вкладке **«Управление данными → 🔄 Переводы»** '
    '(вкладки прокручиваются по горизонтали колёсиком).'
)
if forbidden_donors:
    st.sidebar.warning(
        f'🚫 Запрещено: **{len(forbidden_donors)}** перевод(ов). '
        f'План пересчитан с учётом запретов.'
    )
else:
    st.sidebar.caption('Активных запретов нет.')

# ===== ВЫБОР ТЕКУЩЕГО СПРИНТА =====
today = date.today()
auto_sprint_idx = 0
for i in range(1, 7):
    if today > sprint_date_ranges[i][1]:
        auto_sprint_idx = i

saved_sprint = st.session_state.get('_stored_current_sprint')
if saved_sprint is not None:
    try:
        auto_sprint_idx = max(0, min(6, int(saved_sprint)))
    except (ValueError, TypeError):
        pass

st.sidebar.markdown('---')
st.sidebar.markdown(f"### {fa_text('fa-hourglass-half', 'Симуляция времени (Факт)')}", unsafe_allow_html=True)
sprint_options = ['Старт квартала (Спринт 0)'] + [
    f'Завершен {sprint_label(i)}' for i in range(1, 7)
]
selected_time = st.sidebar.selectbox(
    'Выберите текущую дату:',
    sprint_options,
    index=min(auto_sprint_idx, len(sprint_options) - 1),
    key='current_sprint_selector',
    help='По умолчанию выбран последний полностью завершившийся спринт.',
)
current_time_sprint = sprint_options.index(selected_time)

st.session_state['_stored_current_sprint'] = current_time_sprint
# ---- Баннер «Текущий спринт» ----
if current_time_sprint > 0:
    st.info(
        f'Текущий спринт: {sprint_label(current_time_sprint)} — завершён. '
        f'Новые задачи будут запланированы не раньше Спринта {current_time_sprint + 1}.'
    )
else:
    st.info('Текущий спринт: Старт квартала. Все 6 спринтов впереди.')

# ===== ФАКТ =====
fact_sprint_done = {}
if current_time_sprint > 0:
    st.markdown(f"### {fa_text('fa-circle-check', 'Внесение факта по завершённым спринтам')}", unsafe_allow_html=True)
    st.caption(
        'По умолчанию все задачи, запланированные к завершению в спринте, '
        'отмечены как завершённые. Снимите галочку с тех, что фактически не закрылись. '
        'Продолжающиеся задачи (сплиттинг) здесь не показываются — их можно подтвердить '
        'в спринте фактического завершения или вручную добавить через «Досрочно завершено».'
    )
    all_task_ids = sched.tasks['task_id'].tolist()
    task_by_id = {row['task_id']: row for _, row in sched.tasks.iterrows()}
    final_statuses = {
        'done', 'completed', 'завершена', 'завершено', 'готово',
        'выполнена', 'выполнено',
        'отменено заказ', 'отменена заказчиком', 'отменено заказчиком',
        'отменено', 'cancelled', 'canceled',
        'не будет взято в квартал', 'не берем в квартал',
        'не берём в квартал', 'не будет взято',
    }

    def _is_actionable(tid):
        row = task_by_id.get(tid)
        if row is None:
            return False
        return str(row.get('status', '')).lower().strip() not in final_statuses

    first_appearance = {}
    for s in sorted(b_schedule.keys()):
        for item in b_schedule.get(s, []):
            first_appearance.setdefault(item['task_id'], s)

    for sprint in range(1, current_time_sprint + 1):
        with st.expander(
                f'Факт: {sprint_label(sprint)}',
                expanded=(sprint == current_time_sprint),
        ):
            planned_ids, seen = [], set()
            for item in b_schedule.get(sprint, []):
                tid = item['task_id']
                if tid in seen or tid not in task_by_id:
                    continue
                if item.get('status', '') != 'Завершена':
                    continue
                if not _is_actionable(tid):
                    continue
                seen.add(tid)
                planned_ids.append(tid)

            prior_fact_ids = set(fact_sprint_done)
            planned_ids = [tid for tid in planned_ids if tid not in prior_fact_ids]

            saved_fact = st.session_state.get('_stored_fact', {}) or {}

            if planned_ids:
                rows = []
                for tid in planned_ids:
                    if tid not in saved_fact:
                        checked = True
                    else:
                        checked = saved_fact.get(tid) == sprint
                    rows.append({
                        'Задача': tid,
                        'Команда': task_by_id[tid]['team_id'],
                        'SP': int(task_by_id[tid]['estimation_sp']),
                        'Завершено': checked,
                    })
                edit_df = pd.DataFrame(rows)
                edited = st.data_editor(
                    edit_df,
                    column_config={'Завершено': st.column_config.CheckboxColumn('Реально завершено?')},
                    disabled=['Задача', 'Команда', 'SP'],
                    hide_index=True, use_container_width=True,
                    key=f'fact_editor_{sprint}',
                )
                selected = edited.loc[edited['Завершено'], 'Задача'].tolist()
            else:
                st.caption('Нет новых завершаемых задач по плану на этот спринт.')
                selected = []

            continuing_ids, seen_c = [], set()
            for item in b_schedule.get(sprint, []):
                tid = item['task_id']
                if tid in seen_c or tid not in task_by_id:
                    continue
                if item.get('status', '') != 'Растянута (Сплиттинг)':
                    continue
                if not _is_actionable(tid):
                    continue
                seen_c.add(tid)
                continuing_ids.append((tid, float(item.get('burned_hh', 0) or 0)))

            if continuing_ids:
                st.markdown(
                    f'###### Продолжающиеся задачи ({len(continuing_ids)}) — подтверждать не нужно'
                )
                st.caption(
                    'Эти задачи не завершаются в этом спринте, они идут дальше. '
                    'Прогресс уже учтён — в следующем спринте они возьмут остаток часов.'
                )
                cont_df = pd.DataFrame([
                    {
                        'Задача': tid,
                        'Команда': task_by_id[tid]['team_id'],
                        'SP задачи': int(task_by_id[tid]['estimation_sp']),
                        'Списано ЧЧ (план)': burned,
                    }
                    for tid, burned in continuing_ids
                ])
                st.dataframe(cont_df, use_container_width=True, hide_index=True)

            extra_pool = sorted(
                (tid for tid in all_task_ids
                 if tid not in planned_ids and tid not in prior_fact_ids and _is_actionable(tid)),
                key=lambda t: (first_appearance.get(t, 999), t),
            )
            saved_extra_default = [
                tid for tid, s in saved_fact.items()
                if s == sprint and tid in extra_pool
            ]
            extra_selected = st.multiselect(
                'Досрочно завершено (не из этого спринта):',
                options=extra_pool,
                default=saved_extra_default,
                key=f'fact_extra_{sprint}',
                help='Задачи, которые по плану должны были делаться в других спринтах, '
                     'но фактически закрыты уже сейчас.',
            )
            for tid in selected + extra_selected:
                if tid not in fact_sprint_done:
                    fact_sprint_done[tid] = sprint

# ---- Сохраняем runtime-состояние на диск ----
_forbidden_snapshot = sorted(
    st.session_state.get('_forbidden_donors_value', set())
)
_forced_schedule_now = dict(st.session_state.get('_forced_schedule_value', {}) or {})
_forced_snapshot = tuple(sorted(
    (t_id, tuple(entry.get('sprints', []) if isinstance(entry, dict) else []))
    for t_id, entry in _forced_schedule_now.items()
))

_current_runtime = {
    'current_time_sprint': current_time_sprint,
    'fact_sprint_done': fact_sprint_done,
    'quarter_start_value': quarter_start.isoformat() if quarter_start else None,
    'ignore_1c_roles': bool(st.session_state.get('_ignore_1c_roles_value', True)),
    'forbidden_donors': [list(pair) for pair in _forbidden_snapshot],
    # Принудительные задачи вне плана: {task_id: {'sprints': [...], 'comment': '...'}}
    'forced_schedule': _forced_schedule_now,
}
_runtime_hash = hash((
    _current_runtime['current_time_sprint'],
    tuple(sorted(_current_runtime['fact_sprint_done'].items())),
    _current_runtime['quarter_start_value'],
    _current_runtime['ignore_1c_roles'],
    tuple(_forbidden_snapshot),
    _forced_snapshot,
))
if st.session_state.get('_last_runtime_hash') != _runtime_hash:
    storage.save_runtime(_current_runtime)
    st.session_state['_last_runtime_hash'] = _runtime_hash
    st.session_state['_stored_current_sprint'] = current_time_sprint
    st.session_state['_stored_fact'] = dict(fact_sprint_done)
    st.session_state['_stored_quarter_start_iso'] = _current_runtime['quarter_start_value']
    st.session_state['_stored_ignore_1c_roles'] = _current_runtime['ignore_1c_roles']
    st.session_state['_stored_forbidden_donors'] = _current_runtime['forbidden_donors']

# ===== ВАЛИДАЦИЯ ФАКТИЧЕСКИХ ЗАВИСИМОСТЕЙ =====
_done_statuses = {'done', 'completed', 'завершена', 'завершено', 'готово', 'выполнена', 'выполнено'}
_skipped_statuses = {
    'отменено заказ', 'отменена заказчиком', 'отменено заказчиком',
    'отменено', 'cancelled', 'canceled',
    'не будет взято в квартал', 'не берем в квартал',
    'не берём в квартал', 'не будет взято',
}


def _task_status_lower(task_id: str) -> str:
    rows = sched.tasks.loc[sched.tasks['task_id'] == task_id]
    if rows.empty:
        return ''
    return str(rows.iloc[0].get('status', '')).strip().lower()


def _already_done(task_id: str) -> bool:
    return _task_status_lower(task_id) in _done_statuses


def _skipped(task_id: str) -> bool:
    return _task_status_lower(task_id) in _skipped_statuses


fact_dependency_warnings = []
for task_id, done_sprint in fact_sprint_done.items():
    if task_id not in sched.G:
        continue
    for predecessor in sched.G.predecessors(task_id):
        if _already_done(predecessor) or _skipped(predecessor):
            continue
        predecessor_sprint = fact_sprint_done.get(predecessor)
        if predecessor_sprint is None:
            fact_dependency_warnings.append(
                f'{task_id} отмечена завершённой в Спринте {done_sprint}, '
                f'но зависимость {predecessor} в факте не завершена.'
            )
        elif predecessor_sprint >= done_sprint:
            fact_dependency_warnings.append(
                f'{task_id} отмечена в Спринте {done_sprint}, '
                f'но зависимость {predecessor} завершена в Спринте {predecessor_sprint}.'
            )

if fact_dependency_warnings:
    st.warning('Обнаружены нарушения фактических зависимостей')
    with st.expander(f'Показать нарушения ({len(fact_dependency_warnings)})'):
        for warning in fact_dependency_warnings:
            st.caption(f'• {warning}')

# ===== ПЕРЕСЧЁТ =====
_forced_schedule = dict(st.session_state.get('_forced_schedule_value', {}) or {})

needs_recompute = (
    current_time_sprint > 0
    or bool(forbidden_donors)
    or bool(_forced_schedule)
)
if needs_recompute:
    c_schedule, c_statuses, c_logs, c_alerts, c_kpis, c_burned = sched.run_smart_planning(
        fact_sprint_done=fact_sprint_done,
        current_time_sprint=current_time_sprint,
        baseline_schedule=b_schedule,
        forbidden_donors=forbidden_donors,
        forced_schedule=_forced_schedule,
    )
else:
    c_schedule, c_statuses, c_logs, c_alerts, c_kpis, c_burned = (
        b_schedule, b_statuses, b_logs, b_alerts, b_kpis, b_burned
    )

# ---- Актуальный список переводов после пересчёта ----
current_transfer_summary = sched.get_transfer_summary(c_logs)


def _transfers_detail_from_logs(logs_df):
    """Возвращает DF с постатейной детализацией переводов. Из логов берём
    всё, что нужно для вкладки «Переводы»: спринт, задачу, донора, роль,
    получателя, часы и конкретного инженера-донора."""
    cols = ['sprint', 'task_id', 'initiative', 'donor_team', 'role',
            'recipient_team', 'hours', 'donor_engineer']
    if logs_df is None or logs_df.empty or 'action' not in logs_df.columns:
        return pd.DataFrame(columns=cols)
    tr = logs_df[logs_df['action'] == 'Реорганизация (Перевод)'].copy()
    if tr.empty:
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in tr.columns:
            tr[c] = None
    return tr[cols].reset_index(drop=True)


# Контекст для вкладки «Управление данными → 🔄 Переводы».
# Активные — из c_logs (после применения запретов), baseline — из b_logs
# (для контекста: что было в исходном плане).
st.session_state['_transfers_context'] = {
    'active_summary': current_transfer_summary,
    'active_detail': _transfers_detail_from_logs(c_logs),
    'baseline_detail': _transfers_detail_from_logs(b_logs),
}

with st.sidebar.expander(
    f'Переводы в текущем плане ({len(current_transfer_summary)})',
    expanded=False,
):
    if current_transfer_summary.empty:
        st.caption('В текущем плане переводов между командами нет.')
    else:
        st.dataframe(current_transfer_summary, use_container_width=True, hide_index=True)
        csv_transfers = current_transfer_summary.to_csv(index=False, sep=';').encode('utf-8-sig')
        st.download_button(
            'Скачать список переводов (CSV)',
            data=csv_transfers,
            file_name='pochtatech_transfers.csv',
            mime='text/csv',
            key='download_transfers_csv',
        )

# ===== KPI =====
col1, col2, col3, col4 = st.columns(4)
col1.metric('PI Predictability Measure', c_kpis['PI Predictability Measure'])
col2.metric(
    'Инициатив завершено',
    f"{c_kpis['Fully Completed Initiatives']} из {c_kpis['Total Initiatives']}",
)
col3.metric('Say/Do Ratio (по факту)', c_kpis['Sprint Say/Do Ratio'])
critical_bf = int((bf_df['Bus Factor'] == 1).sum()) if 'Bus Factor' in bf_df.columns else 0
col4.metric('Уязвимых технологий (BF=1)', f'{critical_bf} из {len(bf_df)}')

canceled_count = int(c_kpis.get('Canceled Initiatives', 0))
if canceled_count:
    st.caption(f"Инициатив исключено из расчёта PI (отменено/не взято): {canceled_count}")
st.divider()

say_do_detail = c_kpis.get('Say/Do Detail') or {}

if say_do_detail:
    with st.expander('План vs Факт по пройденным спринтам', expanded=True):
        detail_rows = []
        for s, v in sorted(say_do_detail.items()):
            say_do_str = f"{v['ratio']:.1f}%" if v.get('has_commitment', True) else 'н/д (нет commit)'
            detail_rows.append({
                'Спринт': sprint_label(s),
                'Запланировано SP (baseline)': v['planned_sp'],
                'Сделано по факту SP': v['done_sp'],
                'Сделано позже': v.get('late_done_sp', 0),
                'Say/Do': say_do_str,
            })

        plan_fact_df = pd.DataFrame(detail_rows)
        st.dataframe(plan_fact_df, use_container_width=True, hide_index=True)

        chart_records = []
        for s, v in sorted(say_do_detail.items()):
            chart_records.append({'Спринт': sprint_label(s), 'Тип': 'План', 'SP': float(v['planned_sp'])})
            chart_records.append({'Спринт': sprint_label(s), 'Тип': 'Факт', 'SP': float(v['done_sp'])})

        chart_df = pd.DataFrame(chart_records)
        fig_plan_fact = px.bar(
            chart_df, x='Спринт', y='SP', color='Тип', barmode='group', text='SP',
            title='План и факт Story Points по спринтам',
        )
        fig_plan_fact.update_traces(texttemplate='%{text:.0f}', textposition='outside')
        fig_plan_fact.update_layout(
            height=430, xaxis_title='Спринт', yaxis_title='Story Points',
            legend_title='', hovermode='x unified',
        )
        st.plotly_chart(fig_plan_fact, use_container_width=True)

        say_do_chart_df = pd.DataFrame([
            {'Спринт': sprint_label(s), 'Say/Do (%)': float(v['ratio'])}
            for s, v in sorted(say_do_detail.items())
            if v.get('has_commitment', True)
        ])
        fig_say_do = px.line(
            say_do_chart_df, x='Спринт', y='Say/Do (%)', markers=True, text='Say/Do (%)',
            title='Say/Do по завершённым спринтам',
        )
        fig_say_do.update_traces(texttemplate='%{text:.1f}%', textposition='top center')
        fig_say_do.update_layout(
            height=350, xaxis_title='Спринт', yaxis_title='Say/Do, %', yaxis=dict(range=[0, 110]),
        )
        st.plotly_chart(fig_say_do, use_container_width=True)

# ===== DATA QUALITY =====
dq_warnings = sched.get_data_quality_warnings()
if dq_warnings:
    with st.expander(f'Замечания к исходным данным ({len(dq_warnings)})'):
        for warning in dq_warnings:
            st.caption(f'• {warning}')

# ===== ВКЛАДКИ =====
tab1, tab2, tab3, tab4, tab5, tab_manage, tab_events = st.tabs(
    ['План и Задачи', 'Трекер сотрудников', 'Лог решений', 'Звездная карта', 'Алерты',
     'Управление данными', 'Журнал событий']
)

with tab1:
    task_sprints = {}
    for sprint, tasks in c_schedule.items():
        for item in tasks:
            tid = item['task_id']
            if tid not in task_sprints:
                task_sprints[tid] = {
                    'task_id': tid, 'summary': item.get('summary', ''),
                    'team': item.get('team', ''), 'initiative': item.get('initiative', 'Без инициативы'),
                    'rung': item.get('rung', 0), 'statuses': [], 'sprints': [],
                    'sp': item.get('task_sp', item.get('sp', 0)), 'burned_hh': 0.0,
                }
            task_sprints[tid]['sprints'].append(sprint)
            task_sprints[tid]['statuses'].append(item.get('status', ''))
            task_sprints[tid]['burned_hh'] += float(item.get('burned_hh', 0) or 0)

    gantt_records = []
    for sprint, tasks in c_schedule.items():
        start, end = sprint_date_ranges[sprint]
        for item in tasks:
            _status = item.get('status', '')
            _is_forced = _status == 'Принудительно (вне плана)'
            gantt_records.append({
                'Task': item['task_id'], 'Sprint': sprint_label(sprint), 'Sprint_Num': sprint,
                'Start': start, 'Finish': end + timedelta(days=1),
                'Summary': item.get('summary', ''), 'Team': item.get('team', ''),
                'Initiative': item.get('initiative', 'Без инициативы'), 'Rung': item.get('rung', 0),
                'Status': ('📌 ' + _status) if _is_forced else _status,
                'SP': item.get('sp', 0),
                'SP задачи': item.get('task_sp', item.get('sp', 0)),
                'Списано HH': round(float(item.get('burned_hh', 0) or 0), 1),
            })

    if gantt_records:
        gantt_df = pd.DataFrame(gantt_records)
        fig = px.timeline(
            gantt_df, x_start='Start', x_end='Finish', y='Task', color='Team', text='Status',
            hover_data=['Sprint', 'Initiative', 'Rung', 'SP', 'SP задачи', 'Списано HH', 'Summary'],
            title='График выполнения задач по кварталу',
        )
        tickvals, ticktext = [], []
        for i in range(1, 7):
            s, e = sprint_date_ranges[i]
            tickvals.append(s + (e - s) / 2)
            ticktext.append(f'Спринт {i}')
        # constraintext='none' — критично: без него Plotly молча скрывает
        # подписи вида «Растянута (Сплиттинг)», если полоса на графике
        # оказалась уже текста (например, при уменьшенном окне браузера).
        # cliponaxis=False разрешает тексту выходить за пределы полосы.
        fig.update_traces(
            textposition='inside',
            insidetextanchor='middle',
            constraintext='none',
            cliponaxis=False,
            textfont=dict(size=10, color='#ffffff'),
        )
        fig.update_layout(
            margin=dict(l=10, r=10, t=60, b=40),
            uirevision='constant',
            height=max(500, min(1400, 30 * gantt_df['Task'].nunique() + 250)),
            xaxis=dict(tickmode='array', tickvals=tickvals, ticktext=ticktext, title='Спринты квартала'),
            yaxis=dict(autorange='reversed', title='Задача'),
            legend_title='Команда',
        )
        st.plotly_chart(fig, use_container_width=True, key='gantt_main')

        export_df = gantt_df.drop(columns=['Start', 'Finish'])
        st.download_button(
            label='Скачать пересчитанный план (CSV)',
            data=export_df.to_csv(index=False, sep=';').encode('utf-8-sig'),
            file_name='pochtatech_plan.csv', mime='text/csv',
        )
    else:
        st.info('В текущем сценарии ни одна задача не получила плановые часы.')

    # ---------- Специалисты по задаче ----------
    st.markdown('---')
    st.markdown(f"### {fa_text('fa-user-gear', 'Специалисты по задаче')}", unsafe_allow_html=True)
    st.caption(
        'Выберите задачу — увидите, какие роли нужны, сколько часов, '
        'кто в команде может её выполнить и откуда будут привлечены доноры.'
    )

    _all_task_ids_for_roles = sorted({
        item['task_id'] for _items in c_schedule.values() for item in _items
    })
    _raw_estimates = getattr(sched, 'task_estimates_raw', {})
    _all_task_ids_for_roles = sorted(
        set(_all_task_ids_for_roles)
        | set(sched.task_estimates.keys())
        | set(_raw_estimates.keys())
    )

    if not _all_task_ids_for_roles:
        st.info('Нет задач со сметой — нечего показывать.')
    else:
        col_pick1, col_pick2 = st.columns([3, 1])
        selected_task_for_roles = col_pick1.selectbox(
            'Задача:', options=_all_task_ids_for_roles, key='role_view_task_pick',
        )
        show_all_tasks_roles = col_pick2.checkbox(
            'Все задачи', value=False, key='role_view_all_tasks',
            help='Показать потребности по всем задачам сразу (может быть длинная таблица).',
        )

        def _collect_task_roles(tid: str) -> pd.DataFrame:
            req = sched.task_estimates.get(tid, {})
            raw = getattr(sched, 'task_estimates_raw', {}).get(tid, {})
            all_role_names = set(req.keys()) | set(raw.keys())
            if not all_role_names:
                return pd.DataFrame()
            task_row = sched.tasks.loc[sched.tasks['task_id'] == tid]
            task_team = str(task_row.iloc[0]['team_id']).strip() if not task_row.empty else ''

            task_transfers = pd.DataFrame()
            if not c_logs.empty:
                task_transfers = c_logs[
                    (c_logs['task_id'] == tid) & (c_logs['action'] == 'Реорганизация (Перевод)')
                ]

            rows = []
            for role in sorted(all_role_names):
                hours = float(req.get(role, raw.get(role, 0)))
                is_available = role in req
                own_engineers = sched.team_role_engineers.get(task_team, {}).get(role, set())

                donor_info = ''
                if not task_transfers.empty:
                    tr = task_transfers[task_transfers['role'] == role]
                    if not tr.empty:
                        pairs = []
                        for _, r in tr.iterrows():
                            d_team = str(r.get('donor_team', '') or '').strip()
                            d_hours = float(r.get('hours', 0) or 0)
                            d_eng = str(r.get('donor_engineer', '') or '').strip()
                            if d_team and d_hours > 0:
                                if d_eng and d_eng.lower() != 'nan':
                                    pairs.append(f'{d_team} → {d_eng} ({d_hours:.0f} ЧЧ)')
                                else:
                                    pairs.append(f'{d_team} ({d_hours:.0f} ЧЧ)')
                        donor_info = ', '.join(pairs) if pairs else ''

                # Показываем, что по этой роли есть запрещённый донор —
                # иначе пользователь не понимает, почему задача стала сплитом.
                if not donor_info and role not in req:
                    _forbidden = st.session_state.get('_forbidden_donors_value', set())
                    _blocked_teams = [
                        team for team, blocked_role in _forbidden
                        if blocked_role == role
                    ]
                    if _blocked_teams:
                        donor_info = f'🚫 запрещено: {", ".join(sorted(set(_blocked_teams)))}'

                own_details = []
                for eid in sorted(own_engineers):
                    emp = sched.engineers_df[sched.engineers_df['engineer_id'] == eid]
                    if emp.empty:
                        continue
                    er = emp.iloc[0]
                    status = str(er.get('status', 'Активен'))
                    cap = float(er.get('capacity_rate', 0) or 0)
                    own_details.append(f'{eid} ({cap:g} ст., {status})')
                if not is_available:
                    own_str = 'роли нет в штате компании'
                else:
                    own_str = '; '.join(own_details) if own_details else '— нет в команде'

                rows.append({
                    'Роль': role, 'Нужно ЧЧ': int(hours),
                    'В команде задачи': own_str,
                    'Перевод (донор)': donor_info if donor_info else '—',
                })
            return pd.DataFrame(rows)

        if show_all_tasks_roles:
            all_rows = []
            for tid in _all_task_ids_for_roles:
                df_one = _collect_task_roles(tid)
                if df_one.empty:
                    continue
                task_row = sched.tasks.loc[sched.tasks['task_id'] == tid]
                task_team = str(task_row.iloc[0]['team_id']).strip() if not task_row.empty else ''
                df_one.insert(0, 'Команда', task_team)
                df_one.insert(0, 'Задача', tid)
                all_rows.append(df_one)
            if all_rows:
                full_df = pd.concat(all_rows, ignore_index=True)
                st.dataframe(full_df, use_container_width=True, hide_index=True, height=600)
                st.download_button(
                    label='Скачать потребности по ролям (CSV)',
                    data=full_df.to_csv(index=False, sep=';').encode('utf-8-sig'),
                    file_name='pochtatech_task_roles.csv', mime='text/csv',
                    key='download_task_roles_all',
                )
            else:
                st.info('Нет данных по ролям для отображения.')
        else:
            df_roles = _collect_task_roles(selected_task_for_roles)
            if df_roles.empty:
                st.info(f'У задачи {selected_task_for_roles} нет постатейной сметы.')
            else:
                task_row = sched.tasks.loc[sched.tasks['task_id'] == selected_task_for_roles]
                if not task_row.empty:
                    t = task_row.iloc[0]
                    st.markdown(
                        f"**Задача {selected_task_for_roles}** — *{str(t.get('summary', '') or '')}*  \n"
                        f"Команда: `{str(t.get('team_id', '') or '')}` · "
                        f"Приоритет (rung): `{t.get('rung', 0)}` · "
                        f"Story Points: `{int(t.get('estimation_sp', 0) or 0)}`"
                    )
                st.dataframe(
                    df_roles, use_container_width=True, hide_index=True,
                    column_config={
                        'Нужно ЧЧ': st.column_config.NumberColumn('Нужно ЧЧ', format='%d'),
                        'В команде задачи': st.column_config.TextColumn('В команде задачи', width='large'),
                        'Перевод (донор)': st.column_config.TextColumn('Перевод (донор)', width='large'),
                    },
                )
                n_roles = len(df_roles)
                n_own = sum(1 for _, r in df_roles.iterrows() if r['В команде задачи'] != '— нет в команде')
                n_transfer = sum(1 for _, r in df_roles.iterrows() if r['Перевод (донор)'] != '—')
                c_a, c_b, c_c = st.columns(3)
                c_a.metric('Ролей нужно', n_roles)
                c_b.metric('Покрыто своими', n_own)
                c_c.metric('С переводом', n_transfer)

    # ---------- Статус незавершённых задач ----------
    st.markdown('---')
    st.markdown(f"### {fa_text('fa-list-check', 'Статус незавершённых задач')}", unsafe_allow_html=True)

    reason_labels = {
        'dependency': 'Ждёт зависимость',
        'sp_capacity': 'Не хватает SP команды',
        'role_capacity': 'Дефицит специалистов',
        'no_specialist': 'Нет специалистов в штате',
        'incomplete_coverage': 'Неполное покрытие ролей',
        'bad_estimate': 'Некорректная смета',
        'bad_team': 'Не указана команда',
        'dependency_cycle': 'Цикл зависимостей',
        'split': 'Растянута (Сплиттинг)',
        'canceled': 'Отменена заказчиком',
        'not_taken': 'Не взята в квартал',
        'completed': 'Завершена',
        'transfer': 'Перевод ресурса',
    }

    tasks_in_plan = set()
    for _items in c_schedule.values():
        for _item in _items:
            tasks_in_plan.add(_item['task_id'])

    continuing_rows = []
    not_fit_rows = []

    for _, row in sched.tasks.iterrows():
        tid = row['task_id']
        status = c_statuses.get(tid)
        if status in {'Done', 'Canceled', 'NotTaken'}:
            continue

        task_logs = c_logs[c_logs['task_id'] == tid] if not c_logs.empty else pd.DataFrame()
        if not task_logs.empty:
            task_logs = task_logs.sort_values('sprint', na_position='first')
            last = task_logs.iloc[-1]
            reason_code = str(last.get('reason_code', ''))
            category = reason_labels.get(reason_code, 'Другое')
            reason_text = str(last.get('reason', ''))
        else:
            category = 'Другое'
            reason_text = 'Критический дефицит ресурсов или некорректные исходные данные.'

        record = {
            'Задача': tid, 'Rung': row.get('rung', 0),
            'Инициатива': row.get('Номер инициативы', 'Без инициативы'),
            'Команда': row.get('team_id', ''),
            'Категория': category, 'Причина': reason_text,
        }
        if tid in tasks_in_plan:
            continuing_rows.append(record)
        else:
            not_fit_rows.append(record)

    if continuing_rows:
        with st.expander(
            f'Продолжаются в следующих спринтах ({len(continuing_rows)})',
            expanded=False,
        ):
            st.caption(
                'Эти задачи есть в плане хотя бы одного спринта. Они ещё '
                'не завершены: часть часов уже списана, остаток перенесён '
                'в следующие спринты.'
            )
            st.markdown(render_unscheduled_table(continuing_rows), unsafe_allow_html=True)

    if not_fit_rows:
        st.markdown(f'##### {fa_text("fa-circle-xmark", f"Не поместились в квартал ({len(not_fit_rows)})")}', unsafe_allow_html=True)
        st.caption(
            'Эти задачи не попали ни в один спринт. Причина — в колонке '
            '«Причина»: блокирующая зависимость, дефицит SP, дефицит ролей '
            'или некорректные исходные данные.'
        )
        st.markdown(render_unscheduled_table(not_fit_rows), unsafe_allow_html=True)

    if not continuing_rows and not not_fit_rows:
        st.success('Все задачи успешно распределены и завершены в квартале!')

with tab2:
    st.markdown(f"### {fa_text('fa-users', 'Загрузка и эффективность сотрудников')}", unsafe_allow_html=True)
    st.caption('Утилизация распределяется пропорционально capacity_rate внутри одной связки «команда + роль». '
               'Уволенные инженеры исключены из агрегатов.')

    emp_data = []
    for _, emp in sched.engineers_df.iterrows():
        engineer_id = emp.get('engineer_id', '')
        team = emp.get('team_id', '')
        role = emp.get('role_norm', '')
        status = str(emp.get('status', 'Активен'))
        if status == 'Уволен':
            continue
        cap_rate = float(emp.get('capacity_rate', 0) or 0)
        team_role_cap_sprint = float(sched.team_role_hours.get(team, {}).get(role, 0) or 0)
        burned_for_role = float(c_burned.get(team, {}).get(role, 0) or 0)
        emp_sprint_cap = max(0.0, cap_rate * 80.0)
        emp_quarter_cap = emp_sprint_cap * 6
        if team_role_cap_sprint > 0:
            emp_burned = burned_for_role * emp_sprint_cap / team_role_cap_sprint
        else:
            emp_burned = 0.0
        util_pct = emp_burned / emp_quarter_cap * 100 if emp_quarter_cap > 0 else 0.0
        emp_data.append({
            'Инженер': engineer_id, 'Команда': team, 'Роль': role, 'Статус': status,
            'Ставка': cap_rate, 'Доступно (ЧЧ/кв)': round(emp_quarter_cap, 1),
            'Загрузка (ЧЧ/кв)': round(emp_burned, 1),
            'Утилизация (%)': f'{min(100, max(0, util_pct)):.1f}%',
        })

    emp_df = pd.DataFrame(emp_data)
    if not emp_df.empty:
        emp_df['_util_num'] = pd.to_numeric(emp_df['Утилизация (%)'].str.rstrip('%'), errors='coerce').fillna(0)
        emp_df = emp_df.sort_values(by=['_util_num', 'Команда'], ascending=[False, True]).drop(columns='_util_num')
    st.dataframe(emp_df, use_container_width=True, height=600, hide_index=True)

    fired = sched.engineers_df[sched.engineers_df['status'] == 'Уволен']
    if not fired.empty:
        with st.expander(f'Уволенные ({len(fired)})', expanded=False):
            st.dataframe(
                fired[['engineer_id', 'team_id', 'role', 'status_start_date']].rename(columns={
                    'engineer_id': 'Инженер', 'team_id': 'Команда', 'role': 'Роль',
                    'status_start_date': 'Дата увольнения',
                }),
                use_container_width=True, hide_index=True,
            )

    st.markdown('---')
    st.markdown('##### Историческая стабильность команд')
    st.dataframe(sched.get_team_reliability(), use_container_width=True, hide_index=True)

    if not roles_df.empty:
        with st.expander('Роли с одним сотрудником в команде'):
            st.dataframe(roles_df[roles_df['Кол-во сотрудников'] == 1], use_container_width=True, hide_index=True)

with tab3:
    col_f1, col_f2, col_f3 = st.columns(3)
    safe_actions = sorted(c_logs['action'].dropna().unique().tolist()) if not c_logs.empty else []
    default_actions = [a for a in ['Реорганизация (Перевод)', 'Перенос'] if a in safe_actions]
    action_filter = col_f1.multiselect('Действие:', options=safe_actions, default=default_actions)
    team_options = sorted(c_logs['team'].dropna().astype(str).unique().tolist()) if not c_logs.empty else []
    team_filter = col_f2.multiselect('Команда:', options=team_options)
    init_options = sorted(c_logs['initiative'].dropna().astype(str).unique().tolist()) if not c_logs.empty else []
    init_filter = col_f3.multiselect('Инициатива:', options=init_options)

    col_f4, col_f5 = st.columns(2)
    search = col_f4.text_input('Поиск по task_id:')
    only_final = col_f5.checkbox(
        'Показать только финальное решение по каждой задаче', value=True,
        help=(
            'Если включено — по каждой задаче остаётся одна последняя запись. '
            'Промежуточные «Переносы» между спринтами внутри квартала скрываются, '
            'остаётся только итог: где задача встала в план или почему не встала вовсе.'
        ),
    )

    f_logs = c_logs.copy()
    if action_filter:
        f_logs = f_logs[f_logs['action'].isin(action_filter)]
    if team_filter:
        f_logs = f_logs[f_logs['team'].astype(str).isin(team_filter)]
    if init_filter:
        f_logs = f_logs[f_logs['initiative'].astype(str).isin(init_filter)]
    if search.strip():
        f_logs = f_logs[f_logs['task_id'].astype(str).str.contains(search.strip(), case=False, na=False, regex=False)]

    if only_final and not f_logs.empty:
        sort_cols = ['task_id']
        if 'sprint' in f_logs.columns:
            sort_cols.append('sprint')
        f_logs = f_logs.sort_values(sort_cols, na_position='first')
        f_logs = f_logs.groupby('task_id', as_index=False).tail(1)

    st.dataframe(f_logs, use_container_width=True, height=450, hide_index=True)


def _alert_kind(alert_type: str) -> str:
    """Нормализует тип алерта, независимо от старого emoji-префикса."""
    value = str(alert_type or '').lower()
    # Неполное покрытие ролей относим к структурному дефициту: причина
    # та же — на задачу не хватает людей нужной роли. Но это более
    # специфичная проверка, поэтому идёт раньше общей.
    if 'неполное покрытие' in value or 'покрытие ролей' in value:
        return 'brown'
    if 'срыв' in value or 'ошибка' in value:
        return 'red'
    if 'сдвиг' in value:
        return 'yellow'
    if 'структурный дефицит' in value or 'стр. дефицит' in value:
        return 'brown'
    if 'нет доступного' in value:
        return 'brown'
    if 'перерасход' in value or ('дефицит' in value and 'парт' not in value):
        return 'orange'
    if 'парттайм' in value:
        return 'purple'
    return 'other'


def _clean_alert_type(alert_type: str) -> str:
    return str(alert_type or '').strip()


with tab4:
    st.markdown(f"### {fa_text('fa-star', 'Звёздная карта компетенций')}", unsafe_allow_html=True)
    st.caption(
        'Карта показывает распределение навыков по командам и критичные компетенции, '
        'которыми владеет только один инженер.'
    )

    star_df = analytics.get_star_map_data()
    total_skills = len(star_df)
    critical_skills = int(star_df['Критичный'].sum()) if not star_df.empty else 0
    critical_percent = (critical_skills / total_skills * 100) if total_skills else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric('Всего навыков', total_skills)
    c2.metric('Критичных (BF=1)', f'{critical_skills}')
    c3.metric('Доля критичных', f'{critical_percent:.1f}%')

    if critical_percent > 30:
        st.error('Больше трети навыков держатся на одном человеке. Любой отпуск или увольнение критично скажется на квартале.')
    elif critical_percent > 15:
        st.warning(f'{critical_percent:.0f}% навыков критичны. Есть зоны риска — стоит запланировать дообучение.')
    else:
        st.success('Компания устойчива: критичных навыков немного.')

    st.markdown('---')
    st.markdown(f"##### {fa_text('fa-temperature-half', 'Тепловая карта: навык × команда')}", unsafe_allow_html=True)
    st.caption(
        'Клетка = сколько инженеров в команде владеют навыком. '
        'Красные клетки — критичные (только 1 человек). '
        'Пустые клетки — навыка в команде нет вообще.'
    )

    matrix = analytics.get_team_skill_matrix()

    if matrix.empty:
        st.info('Нет данных для тепловой карты.')
    else:
        matrix = matrix.loc[matrix.sum(axis=1) > 0]
        matrix = matrix.assign(_min=matrix.min(axis=1), _sum=matrix.sum(axis=1)) \
                       .sort_values(['_min', '_sum'], ascending=[True, True]) \
                       .drop(columns=['_min', '_sum'])
        if matrix.shape[0] > 60:
            st.caption(f'Показаны первые 60 навыков из {matrix.shape[0]} по критичности.')
            matrix = matrix.head(60)

        fig_heat = px.imshow(
            matrix.values, x=matrix.columns.tolist(), y=matrix.index.tolist(),
            color_continuous_scale=[
                (0.0, '#3a0d0d'), (0.25, '#c0392b'), (0.5, '#f39c12'), (1.0, '#27ae60'),
            ],
            aspect='auto', text_auto=True,
        )
        fig_heat.update_layout(
            height=max(400, min(1400, 22 * matrix.shape[0] + 150)),
            xaxis_title='Команда', yaxis_title='Навык',
            coloraxis_colorbar=dict(title='Инженеров'),
        )
        fig_heat.update_xaxes(side='top')
        st.plotly_chart(fig_heat, use_container_width=True)

    with st.expander('Полная таблица Bus Factor по компании', expanded=False):
        st.dataframe(bf_df, use_container_width=True, hide_index=True)

    with st.expander('Bus Factor по командам (локальный риск)', expanded=False):
        st.caption('Навык может быть не критичным для компании в целом, но критичным для КОНКРЕТНОЙ команды.')
        bf_team_local = analytics.get_bus_factor_by_team()
        team_filter = st.selectbox(
            'Команда:', ['Все'] + sorted(bf_team_local['Команда'].unique().tolist()),
            key='bf_team_filter',
        )
        view_df = bf_team_local if team_filter == 'Все' else bf_team_local[bf_team_local['Команда'] == team_filter]
        critical_view = view_df[view_df['Bus Factor команды'] == 1]
        if critical_view.empty:
            st.success('В выбранных командах критичных навыков нет.')
        else:
            st.dataframe(critical_view, use_container_width=True, hide_index=True)

    with st.expander('Критичные навыки → задачи (влияние на квартал)', expanded=True):
        st.caption(
            'Связь «навык → задача» показывает, где отсутствие одного инженера '
            'остановит конкретную работу. '
            '«Прямое упоминание» — навык встречается в тексте задачи. '
            '«Задача команды владельца» — задача в команде, где живёт единственный носитель навыка.'
        )
        skill_tasks_df = analytics.get_critical_skill_tasks(sched.tasks)

        if skill_tasks_df.empty:
            st.success('Нет критичных навыков, привязанных к задачам квартала.')
        else:
            connection_types = sorted(skill_tasks_df['Связь'].dropna().unique().tolist())
            picked = st.multiselect(
                'Тип связи:', options=connection_types, default=connection_types,
                key='skill_tasks_filter',
            )
            filtered = skill_tasks_df[skill_tasks_df['Связь'].isin(picked)]
            st.dataframe(
                filtered[['Навык', 'Владелец навыка', 'Команда владельца', 'Задача', 'Команда задачи', 'Связь']],
                use_container_width=True, hide_index=True,
                height=min(500, 40 * len(filtered) + 60),
            )
            unique_tasks_at_risk = filtered['Задача'].nunique()
            st.caption(f'Задач под риском из-за критичных навыков: **{unique_tasks_at_risk}** из {sched.tasks.shape[0]} в квартале.')

    with st.expander('Роли с одним сотрудником в команде', expanded=False):
        if roles_df.empty:
            st.info('Нет данных по ролям.')
        else:
            single_role = roles_df[roles_df['Кол-во сотрудников'] == 1]
            if single_role.empty:
                st.success('Все роли в командах покрыты минимум двумя инженерами.')
            else:
                st.dataframe(single_role, use_container_width=True, hide_index=True)


with tab5:
    # Группируем алерты по категориям — каждая в своём expander.
    alert_categories = [
        ('red',    'СРЫВ И ОШИБКИ ПЛАНИРОВАНИЯ',          'fa-circle-xmark',     st.error,   True),
        ('brown',  'СТРУКТУРНЫЙ ДЕФИЦИТ',                  'fa-user-slash',       st.error,   True),
        ('orange', 'РЕСУРСНЫЙ ДЕФИЦИТ / ПЕРЕРАСХОД',       'fa-circle-exclamation', st.warning, True),
        ('yellow', 'РИСК КАСКАДНОГО СДВИГА',               'fa-triangle-exclamation', st.warning, False),
        ('purple', 'ПАРТТАЙМ',                             'fa-user-clock',       st.info,    True),
    ]

    counts = {kind: sum(1 for a in c_alerts if _alert_kind(a.get('type', '')) == kind)
              for kind, _, _, _, _ in alert_categories}

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.error(f'Срывы: {counts["red"]}')
    c2.error(f'Стр. дефицит: {counts["brown"]}')
    c3.warning(f'Дефициты: {counts["orange"]}')
    c4.warning(f'Сдвиги: {counts["yellow"]}')
    c5.info(f'Парттайм: {counts["purple"]}')

    st.divider()

    for kind, label, icon, renderer, expanded in alert_categories:
        group = [a for a in c_alerts if _alert_kind(a.get('type', '')) == kind]
        if not group:
            continue
        with st.expander(f'{label} ({len(group)})', expanded=expanded):
            for alert in group:
                text = (
                    f"**{_clean_alert_type(alert.get('type', 'Алерт'))}** | "
                    f"{alert.get('task_id', '')} "
                    f"({alert.get('initiative', '')})\n\n"
                    f"{alert.get('description', '')}"
                )
                renderer(text)

# ===== УПРАВЛЕНИЕ ДАННЫМИ =====
with tab_manage:
    if auth.require_permission('edit_data', 'management'):
        # Контекст для вкладки «📌 Вне плана».
        _tasks_in_plan = set()
        for _items in c_schedule.values():
            for _it in _items:
                _tasks_in_plan.add(_it['task_id'])
        st.session_state['_forced_context'] = {
            'all_task_ids': sorted(sched.tasks['task_id'].astype(str).tolist()),
            'tasks_in_plan': sorted(_tasks_in_plan),
            'task_meta': {
                str(row['task_id']): {
                    'team': str(row.get('team_id', '') or ''),
                    'rung': int(row.get('rung', 0) or 0),
                    'sp': int(row.get('estimation_sp', 0) or 0),
                    'summary': str(row.get('summary', '') or ''),
                    'status': str(row.get('status', '') or ''),
                }
                for _, row in sched.tasks.iterrows()
            },
        }
        data_management.render(storage)

# ===== ЖУРНАЛ СОБЫТИЙ =====
with tab_events:
    if auth.require_permission('view_audit', 'event_log'):
        data_management.render_event_log(storage)