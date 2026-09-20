import os
import sys
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
</style>
''',
    unsafe_allow_html=True,
)

st.title('📦 ПочтаТех: Квартальное планирование (PI)')

with st.expander('📖 Как читать этот дашборд — короткая справка'):
    st.markdown(
        '''
- **Bus Factor** — сколько человек в компании или команде владеют технологией.
- **SP** — Story Points задачи. Commitment резервируется только один раз, при первом включении задачи.
- **HH** — трудозатраты в человеко-часах. Именно HH расходуются по ролям внутри спринта.
- **«Растянута (Сплиттинг)»** — задача не помещается целиком по HH в одном спринте и продолжает выполняться в следующем; SP повторно не списываются.
- **«Реорганизация (Перевод)»** — временное использование свободных часов нужной роли из другой команды.
- **Зазор между зависимостями** — задача-потомок стартует не раньше следующего спринта после завершения предшественника.
- **Say/Do Ratio** — фактически завершённые SP / SP из исходного baseline-плана конкретного спринта.
- **🔴🟡🟠 алерты** — срыв задачи, фактически наблюдаемый каскадный сдвиг и дефицит роли в конкретном спринте.
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
    st.session_state['_runtime_loaded'] = True
st.session_state['storage'] = storage  # чтобы auth.require_permission мог найти его

user = auth.get_current_user()

if not st.session_state.get('_session_logged'):
    storage.log_session(user, 'login')
    st.session_state['_session_logged'] = True

# ---- Загрузка/восстановление состояния ----
# working_dfs — рабочая копия, которую редактирует «Управление данными»;
# правки видны сразу во всех вкладках плана, но не переживают перезапуск,
# пока не нажата «Сохранить» (тогда попадают в committed_dfs и в SQLite).
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
        }
        version = storage.save_snapshot(dfs, user=auth.DEFAULT_USER, comment='Первичная загрузка из dataset.xlsx')
    st.session_state['working_dfs'] = dfs
    st.session_state['committed_dfs'] = {k: v.copy() for k, v in dfs.items()}
    st.session_state['committed_version'] = version
    st.session_state['pending_changes'] = []
    st.session_state['dirty'] = False

# ===== КАЛЕНДАРЬ: инициализация + виджет + вычисления =====
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
st.sidebar.header('📅 Календарь квартала')
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

# ===== ПЛАНИРОВЩИКИ =====
working_dfs = st.session_state['working_dfs']
committed_dfs = st.session_state['committed_dfs']

sched = SmartScheduler(**working_dfs, sprint_dates=sprint_dates_for_sched)
analytics = StarMapAnalytics(engineers_df=working_dfs['engineers_df'])
baseline_sched = SmartScheduler(**committed_dfs, sprint_dates=sprint_dates_for_sched)
b_schedule, b_statuses, b_logs, b_alerts, b_kpis, b_burned = baseline_sched.run_smart_planning()

bf_df = analytics.get_bus_factor_and_training()
roles_df = analytics.get_critical_roles_shortage()

# ===== УПРАВЛЯЕМАЯ РЕОРГАНИЗАЦИЯ =====
st.sidebar.markdown('---')
st.sidebar.header('🔧 Управление реорганизацией')
transfer_summary = sched.get_transfer_summary(b_logs)
forbidden_donors = set()
if not transfer_summary.empty:
    st.sidebar.caption(
        'Алгоритм показывает автоматические переводы из baseline. '
        'Вы можете запретить конкретную связку «донор + роль» и пересчитать план. '
        'Актуальный список переводов — ниже, после пересчёта.'
    )
    ts = transfer_summary.copy()
    ts['_key'] = ts['Команда-донор'].astype(str) + ' отдаёт «' + ts['Роль'].astype(str) + '»'
    options = ts.drop_duplicates('_key')['_key'].tolist()
    picked = st.sidebar.multiselect('Запретить перевод от команды-донора:', options=options)
    for value in picked:
        donor, role_part = value.split(' отдаёт «', 1)
        forbidden_donors.add((donor, role_part.rstrip('»')))
else:
    st.sidebar.caption('В baseline-плане переводов между командами не потребовалось.')

# ===== ВЫБОР ТЕКУЩЕГО СПРИНТА =====
today = date.today()
auto_sprint_idx = 0
for i in range(1, 7):
    if today > sprint_date_ranges[i][1]:
        auto_sprint_idx = i

# Восстанавливаем из runtime, если пользователь уже выбирал спринт.
saved_sprint = st.session_state.get('_stored_current_sprint')
if saved_sprint is not None:
    try:
        auto_sprint_idx = max(0, min(6, int(saved_sprint)))
    except (ValueError, TypeError):
        pass

st.sidebar.markdown('---')
st.sidebar.header('⏳ Симуляция времени (Факт)')
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

# Сохраняем текущий спринт — data_management использует его для дефолта
# «Стартовый спринт» при создании новой задачи, чтобы новая задача не могла
# попасть в уже завершённый спринт.
st.session_state['_stored_current_sprint'] = current_time_sprint
# ---- Баннер «Текущий спринт» ----
if current_time_sprint > 0:
    st.info(
        f'📅 **Текущий спринт:** {sprint_label(current_time_sprint)} — завершён. '
        f'Новые задачи будут запланированы не раньше Спринта {current_time_sprint + 1}.'
    )
else:
    st.info('📅 **Текущий спринт:** Старт квартала. Все 6 спринтов впереди.')
# ===== ФАКТ (в main, до валидации зависимостей) =====
fact_sprint_done = {}
if current_time_sprint > 0:
    # Streamlit запрещает вложенные expander-ы. Раньше был внешний expander
    # «Внесение факта», внутри которого для каждого спринта рисовался свой
    # expander — это падало с StreamlitAPIException в новых версиях.
    # Заменили внешний expander на обычный заголовок: он визуально отделяет
    # блок, но не создаёт вложенности.
    st.markdown('### ✅ Внесение факта по завершённым спринтам')
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
                    f'###### 🔵 Продолжающиеся задачи ({len(continuing_ids)}) — подтверждать не нужно'
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
_current_runtime = {
    'current_time_sprint': current_time_sprint,
    'fact_sprint_done': fact_sprint_done,
    'quarter_start_value': quarter_start.isoformat() if quarter_start else None,
}
_runtime_hash = hash((
    _current_runtime['current_time_sprint'],
    tuple(sorted(_current_runtime['fact_sprint_done'].items())),
    _current_runtime['quarter_start_value'],
))
if st.session_state.get('_last_runtime_hash') != _runtime_hash:
    storage.save_runtime(_current_runtime)
    st.session_state['_last_runtime_hash'] = _runtime_hash
    st.session_state['_stored_current_sprint'] = current_time_sprint
    st.session_state['_stored_fact'] = dict(fact_sprint_done)
    st.session_state['_stored_quarter_start_iso'] = _current_runtime['quarter_start_value']

# ===== ВАЛИДАЦИЯ ФАКТИЧЕСКИХ ЗАВИСИМОСТЕЙ =====
# Предшественник может отсутствовать в fact_sprint_done по трём причинам:
# 1) он уже Done в исходных данных (закрыт до квартала) — не нарушение;
# 2) он Canceled/NotTaken — не будет выполняться вообще, блокировать не может;
# 3) его забыли отметить — реальное нарушение.
# Раньше мы ругались на все три случая, поэтому у QA-9022 (зависит от
# ASUKD-5222, который в исходном Excel уже Done) всегда висело предупреждение.
_done_statuses = {
    'done', 'completed', 'завершена', 'завершено', 'готово',
    'выполнена', 'выполнено',
}
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
            continue  # предшественник не мешает — либо сделан раньше квартала, либо не будет делаться

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
    st.warning('⚠️ Обнаружены нарушения фактических зависимостей')
    with st.expander(f'Показать нарушения ({len(fact_dependency_warnings)})'):
        for warning in fact_dependency_warnings:
            st.caption(f'• {warning}')

# ===== ПЕРЕСЧЁТ =====
needs_recompute = current_time_sprint > 0 or bool(forbidden_donors)
if needs_recompute:
    c_schedule, c_statuses, c_logs, c_alerts, c_kpis, c_burned = sched.run_smart_planning(
        fact_sprint_done=fact_sprint_done,
        current_time_sprint=current_time_sprint,
        baseline_schedule=b_schedule,
        forbidden_donors=forbidden_donors,
    )
else:
    c_schedule, c_statuses, c_logs, c_alerts, c_kpis, c_burned = (
        b_schedule, b_statuses, b_logs, b_alerts, b_kpis, b_burned
    )
# ---- Актуальный список переводов после пересчёта ----
# Показываем в сайдбаре то, что реально осталось в плане с учётом запретов.
# Раньше здесь отображался baseline — из-за этого запрещённые переводы
# визуально никуда не исчезали.
current_transfer_summary = sched.get_transfer_summary(c_logs)
with st.sidebar.expander(
    f'📋 Переводы в текущем плане ({len(current_transfer_summary)})',
    expanded=False,
):
    if current_transfer_summary.empty:
        st.caption('В текущем плане переводов между командами нет.')
    else:
        st.dataframe(current_transfer_summary, use_container_width=True, hide_index=True)
        csv_transfers = current_transfer_summary.to_csv(index=False, sep=';').encode('utf-8-sig')
        st.download_button(
            '📥 Скачать список переводов (CSV)',
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
    with st.expander(
        '📊 План vs Факт по пройденным спринтам',
        expanded=True
    ):
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

        # Таблица остаётся
        st.dataframe(
            plan_fact_df,
            use_container_width=True,
            hide_index=True
        )

        # ==========================================================
        # ГРАФИК ПЛАН VS ФАКТ
        # ==========================================================

        chart_records = []

        for s, v in sorted(say_do_detail.items()):
            chart_records.append({
                'Спринт': sprint_label(s),
                'Тип': 'План',
                'SP': float(v['planned_sp']),
            })

            chart_records.append({
                'Спринт': sprint_label(s),
                'Тип': 'Факт',
                'SP': float(v['done_sp']),
            })

        chart_df = pd.DataFrame(chart_records)

        fig_plan_fact = px.bar(
            chart_df,
            x='Спринт',
            y='SP',
            color='Тип',
            barmode='group',
            text='SP',
            title='План и факт Story Points по спринтам',
        )

        fig_plan_fact.update_traces(
            texttemplate='%{text:.0f}',
            textposition='outside',
        )

        fig_plan_fact.update_layout(
            height=430,
            xaxis_title='Спринт',
            yaxis_title='Story Points',
            legend_title='',
            hovermode='x unified',
        )

        st.plotly_chart(
            fig_plan_fact,
            use_container_width=True,
        )

        # ==========================================================
        # SAY / DO ПО СПРИНТАМ
        # ==========================================================

        say_do_chart_df = pd.DataFrame([
            {
                'Спринт': sprint_label(s),
                'Say/Do (%)': float(v['ratio']),
            }
            for s, v in sorted(say_do_detail.items())
            if v.get('has_commitment', True)
        ])

        fig_say_do = px.line(
            say_do_chart_df,
            x='Спринт',
            y='Say/Do (%)',
            markers=True,
            text='Say/Do (%)',
            title='Say/Do по завершённым спринтам',
        )

        fig_say_do.update_traces(
            texttemplate='%{text:.1f}%',
            textposition='top center',
        )

        fig_say_do.update_layout(
            height=350,
            xaxis_title='Спринт',
            yaxis_title='Say/Do, %',
            yaxis=dict(range=[0, 110]),
        )

        st.plotly_chart(
            fig_say_do,
            use_container_width=True,
        )
# ===== DATA QUALITY =====
dq_warnings = sched.get_data_quality_warnings()
if dq_warnings:
    with st.expander(f'⚠️ Замечания к исходным данным ({len(dq_warnings)})'):
        for warning in dq_warnings:
            st.caption(f'• {warning}')

# ===== ВКЛАДКИ =====
tab1, tab2, tab3, tab4, tab5, tab_manage, tab_events = st.tabs(
    ['📅 План и Задачи', '👥 Трекер сотрудников', '💡 Лог решений', '⭐ Звездная карта', '⚠️ Алерты',
     '🛠 Управление данными', '📜 Журнал событий']
)

with tab1:
    # Собираем уникальные задачи и диапазон спринтов, в которых они встречаются.
    task_sprints = {}
    for sprint, tasks in c_schedule.items():
        for item in tasks:
            tid = item['task_id']
            if tid not in task_sprints:
                task_sprints[tid] = {
                    'task_id': tid,
                    'summary': item.get('summary', ''),
                    'team': item.get('team', ''),
                    'initiative': item.get('initiative', 'Без инициативы'),
                    'rung': item.get('rung', 0),
                    'statuses': [],
                    'sprints': [],
                    'sp': item.get('task_sp', item.get('sp', 0)),
                    'burned_hh': 0.0,
                }
            task_sprints[tid]['sprints'].append(sprint)
            task_sprints[tid]['statuses'].append(item.get('status', ''))
            task_sprints[tid]['burned_hh'] += float(item.get('burned_hh', 0) or 0)

    # Одна строка = одна задача в одном спринте. Задача с несколькими
    # спринтами даёт несколько отдельных отрезков на диаграмме.
    gantt_records = []
    for sprint, tasks in c_schedule.items():
        start, end = sprint_date_ranges[sprint]
        for item in tasks:
            gantt_records.append({
                'Task': item['task_id'],
                'Sprint': sprint_label(sprint),
                'Sprint_Num': sprint,
                'Start': start,
                'Finish': end + timedelta(days=1),
                'Summary': item.get('summary', ''),
                'Team': item.get('team', ''),
                'Initiative': item.get('initiative', 'Без инициативы'),
                'Rung': item.get('rung', 0),
                'Status': item.get('status', ''),
                'SP': item.get('sp', 0),
                'SP задачи': item.get('task_sp', item.get('sp', 0)),
                'Списано HH': round(float(item.get('burned_hh', 0) or 0), 1),
            })

    if gantt_records:
        gantt_df = pd.DataFrame(gantt_records)
        fig_height = max(500, min(1400, 30 * gantt_df['Task'].nunique() + 250))

        fig = px.timeline(
            gantt_df,
            x_start='Start',
            x_end='Finish',
            y='Task',
            color='Team',
            text='Status',
            hover_data=['Sprint', 'Initiative', 'Rung', 'SP', 'SP задачи', 'Списано HH', 'Summary'],
            title='График выполнения задач по кварталу',
        )

        tickvals = []
        ticktext = []
        for i in range(1, 7):
            s, e = sprint_date_ranges[i]
            tickvals.append(s + (e - s) / 2)
            ticktext.append(f'Спринт {i}')

        fig.update_layout(
            margin=dict(l=10, r=10, t=60, b=40),
            uirevision='constant',  # сохраняет позицию/зум при rerender
            height=max(500, min(1400, 30 * gantt_df['Task'].nunique() + 250)),
            xaxis=dict(
                tickmode='array',
                tickvals=tickvals,
                ticktext=ticktext,
                title='Спринты квартала',
            ),
            yaxis=dict(autorange='reversed', title='Задача'),
            legend_title='Команда',
        )

        st.plotly_chart(fig, use_container_width=True, key='gantt_main')

        export_df = gantt_df.drop(columns=['Start', 'Finish'])
        st.download_button(
            label='📥 Скачать пересчитанный план (CSV)',
            data=export_df.to_csv(index=False, sep=';').encode('utf-8-sig'),
            file_name='pochtatech_plan.csv',
            mime='text/csv',
        )
    else:
        st.info('В текущем сценарии ни одна задача не получила плановые часы.')

    st.markdown('---')
    st.subheader('❌ Задачи, не поместившиеся в квартал')

    reason_labels = {
        'dependency': '🟡 Ждёт зависимость',
        'sp_capacity': '🟠 Не хватает SP команды',
        'role_capacity': '🔴 Дефицит специалистов',
        'bad_estimate': '⚪ Некорректная смета',
        'bad_team': '⚪ Не указана команда',
        'dependency_cycle': '⚪ Цикл зависимостей',
        'split': '🔵 Растянута (Сплиттинг)',
        'canceled': '⚪ Отменена заказчиком',
        'not_taken': '⚪ Не взята в квартал',
        'completed': '🟢 Завершена',
        'transfer': '🔄 Перевод ресурса',
    }

    unscheduled = []
    for _, row in sched.tasks.iterrows():
        tid = row['task_id']
        status = c_statuses.get(tid)
        if status == 'Done':
            continue
        if status in {'Canceled', 'NotTaken'}:
            continue

        task_logs = c_logs[c_logs['task_id'] == tid] if not c_logs.empty else pd.DataFrame()
        if not task_logs.empty:
            task_logs = task_logs.sort_values('sprint', na_position='first')
            last = task_logs.iloc[-1]
            reason_code = str(last.get('reason_code', ''))
            category = reason_labels.get(reason_code, '⚪ Другое')
            reason_text = str(last.get('reason', ''))
            if status == 'InProgress' and reason_code != 'split':
                category = reason_labels['split']
                reason_text = f"Задача продолжается в следующем спринте. {reason_text}"
        else:
            category = '⚪ Другое'
            reason_text = 'Критический дефицит ресурсов или некорректные исходные данные.'
        unscheduled.append(
            {
                'Задача': tid,
                'Rung': row.get('rung', 0),
                'Инициатива': row.get('Номер инициативы', 'Без инициативы'),
                'Команда': row.get('team_id', ''),
                'Категория': category,
                'Причина невыполнения': reason_text,
            }
        )

    if unscheduled:
        uns_df = pd.DataFrame(unscheduled)
        st.dataframe(uns_df, use_container_width=True, hide_index=True)
    else:
        st.success('Все задачи успешно распределены в квартал!')
with tab2:
    st.subheader('👥 Загрузка и эффективность сотрудников')
    st.caption('Утилизация распределяется пропорционально capacity_rate внутри одной связки «команда + роль». '
               'Уволенные инженеры исключены из агрегатов.')

    emp_data = []
    for _, emp in sched.engineers_df.iterrows():
        engineer_id = emp.get('engineer_id', '')
        team = emp.get('team_id', '')
        role = emp.get('role_norm', '')
        status = str(emp.get('status', 'Активен'))
        if status == 'Уволен':
            continue  # уволенных не показываем в трекере
        cap_rate = float(emp.get('capacity_rate', 0) or 0)
        # средние часы за спринт по роли (мы уже посчитали team_role_hours как среднее)
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
            'Инженер': engineer_id,
            'Команда': team,
            'Роль': role,
            'Статус': status,
            'Ставка': cap_rate,
            'Доступно (ЧЧ/кв)': round(emp_quarter_cap, 1),
            'Загрузка (ЧЧ/кв)': round(emp_burned, 1),
            'Утилизация (%)': f'{min(100, max(0, util_pct)):.1f}%',
        })

    emp_df = pd.DataFrame(emp_data)
    if not emp_df.empty:
        emp_df['_util_num'] = pd.to_numeric(emp_df['Утилизация (%)'].str.rstrip('%'), errors='coerce').fillna(0)
        emp_df = emp_df.sort_values(by=['_util_num', 'Команда'], ascending=[False, True]).drop(columns='_util_num')
    st.dataframe(emp_df, use_container_width=True, height=600, hide_index=True)

    # Отдельно — уволенные (для аудита)
    fired = sched.engineers_df[sched.engineers_df['status'] == 'Уволен']
    if not fired.empty:
        with st.expander(f'❌ Уволенные ({len(fired)})', expanded=False):
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
        'Показать только финальное решение по каждой задаче',
        value=True,
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
        # Сортируем так, чтобы последняя запись по задаче была действительно последней.
        # Сначала по task_id, потом по sprint (NaN — в начало), потом по action.
        sort_cols = ['task_id']
        if 'sprint' in f_logs.columns:
            sort_cols.append('sprint')
        f_logs = f_logs.sort_values(sort_cols, na_position='first')
        f_logs = f_logs.groupby('task_id', as_index=False).tail(1)

    st.dataframe(f_logs, use_container_width=True, height=450, hide_index=True)

# ===== ВКЛАДКА 4: ЗВЁЗДНАЯ КАРТА =====
with tab4:
    st.subheader('⭐ Звёздная карта компетенций')
    st.caption(
        'Карта показывает распределение навыков по командам и критичные компетенции, '
        'которыми владеет только один инженер.'
    )

    # ---------- Блок 1: KPI по Bus Factor ----------
    star_df = analytics.get_star_map_data()
    total_skills = len(star_df)
    critical_skills = int(star_df['Критичный'].sum()) if not star_df.empty else 0
    critical_percent = (critical_skills / total_skills * 100) if total_skills else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric('Всего навыков', total_skills)
    c2.metric('Критичных (BF=1)', f'{critical_skills}')
    c3.metric('Доля критичных', f'{critical_percent:.1f}%')

    if critical_percent > 30:
        st.error(
            f'🔴 Больше трети навыков держатся на одном человеке. '
            f'Любой отпуск или увольнение критично скажется на квартале.'
        )
    elif critical_percent > 15:
        st.warning(
            f'🟡 {critical_percent:.0f}% навыков критичны. '
            f'Есть зоны риска — стоит запланировать дообучение.'
        )
    else:
        st.success('🟢 Компания устойчива: критичных навыков немного.')

    # ---------- Блок 2: Тепловая карта команды × навыки ----------
    st.markdown('---')
    st.markdown('##### 🌡️ Тепловая карта: навык × команда')
    st.caption(
        'Клетка = сколько инженеров в команде владеют навыком. '
        'Красные клетки — критичные (только 1 человек). '
        'Пустые клетки — навыка в команде нет вообще.'
    )

    matrix = analytics.get_team_skill_matrix()

    if matrix.empty:
        st.info('Нет данных для тепловой карты.')
    else:
        # Оставляем только навыки, которые есть хотя бы в одной команде
        matrix = matrix.loc[matrix.sum(axis=1) > 0]

        # Сортируем строки: сначала критичные (где минимум 1, но среднее маленькое)
        matrix = matrix.assign(
            _min=matrix.min(axis=1),
            _sum=matrix.sum(axis=1),
        ).sort_values(['_min', '_sum'], ascending=[True, True]).drop(columns=['_min', '_sum'])

        # Ограничиваем до 60 навыков, иначе heatmap нечитаем
        if matrix.shape[0] > 60:
            st.caption(f'Показаны первые 60 навыков из {matrix.shape[0]} по критичности.')
            matrix = matrix.head(60)

        fig_heat = px.imshow(
            matrix.values,
            x=matrix.columns.tolist(),
            y=matrix.index.tolist(),
            color_continuous_scale=[
                (0.0, '#3a0d0d'),   # 0 — тёмно-красный
                (0.25, '#c0392b'),  # 1 — красный
                (0.5, '#f39c12'),   # 2 — оранжевый
                (1.0, '#27ae60'),   # 3+ — зелёный
            ],
            aspect='auto',
            text_auto=True,
        )
        fig_heat.update_layout(
            height=max(400, min(1400, 22 * matrix.shape[0] + 150)),
            xaxis_title='Команда',
            yaxis_title='Навык',
            coloraxis_colorbar=dict(title='Инженеров'),
        )
        fig_heat.update_xaxes(side='top')
        st.plotly_chart(fig_heat, use_container_width=True)

    # ---------- Блок 4: Bus Factor по компании ----------
    with st.expander('🌍 Полная таблица Bus Factor по компании', expanded=False):
        st.dataframe(bf_df, use_container_width=True, hide_index=True)

    # ---------- Блок 5: Bus Factor по командам ----------
    with st.expander('🏢 Bus Factor по командам (локальный риск)', expanded=False):
        st.caption('Навык может быть не критичным для компании в целом, но критичным для КОНКРЕТНОЙ команды.')
        bf_team_local = analytics.get_bus_factor_by_team()
        team_filter = st.selectbox(
            'Команда:',
            ['Все'] + sorted(bf_team_local['Команда'].unique().tolist()),
            key='bf_team_filter',
        )
        view_df = bf_team_local if team_filter == 'Все' else bf_team_local[bf_team_local['Команда'] == team_filter]
        critical_view = view_df[view_df['Bus Factor команды'] == 1]
        if critical_view.empty:
            st.success('В выбранных командах критичных навыков нет.')
        else:
            st.dataframe(critical_view, use_container_width=True, hide_index=True)

    # ---------- Блок 6: Связка критичных навыков с задачами ----------
    with st.expander('🔗 Критичные навыки → задачи (влияние на квартал)', expanded=True):
        st.caption(
            'Связь «навык → задача» показывает, где отсутствие одного инженера '
            'остановит конкретную работу. '
            '«Прямое упоминание» — навык встречается в тексте задачи. '
            '«Задача команды владельца» — задача в команде, где живёт единственный носитель навыка.'
        )

        skill_tasks_df = analytics.get_critical_skill_tasks(sched.tasks)

        if skill_tasks_df.empty:
            st.success('🟢 Нет критичных навыков, привязанных к задачам квартала.')
        else:
            # Добавим фильтр по связи
            connection_types = sorted(skill_tasks_df['Связь'].dropna().unique().tolist())
            picked = st.multiselect(
                'Тип связи:',
                options=connection_types,
                default=connection_types,
                key='skill_tasks_filter',
            )
            filtered = skill_tasks_df[skill_tasks_df['Связь'].isin(picked)]

            st.dataframe(
                filtered[['Навык', 'Владелец навыка', 'Команда владельца', 'Задача', 'Команда задачи', 'Связь']],
                use_container_width=True,
                hide_index=True,
                height=min(500, 40 * len(filtered) + 60),
            )

            # KPI: сколько задач зависит от критичных навыков
            unique_tasks_at_risk = filtered['Задача'].nunique()
            st.caption(
                f'⚠️ Задач под риском из-за критичных навыков: **{unique_tasks_at_risk}** '
                f'из {sched.tasks.shape[0]} в квартале.'
            )

    # ---------- Блок 7: Роли с одним сотрудником ----------
    with st.expander('👤 Роли с одним сотрудником в команде', expanded=False):
        if roles_df.empty:
            st.info('Нет данных по ролям.')
        else:
            single_role = roles_df[roles_df['Кол-во сотрудников'] == 1]
            if single_role.empty:
                st.success('Все роли в командах покрыты минимум двумя инженерами.')
            else:
                st.dataframe(single_role, use_container_width=True, hide_index=True)

with tab5:
    reds = [a for a in c_alerts if '🔴' in a.get('type', '')]
    yellows = [a for a in c_alerts if '🟡' in a.get('type', '')]
    oranges = [a for a in c_alerts if '🟠' in a.get('type', '')]
    purples = [a for a in c_alerts if '🟣' in a.get('type', '')]
    browns = [a for a in c_alerts if '🟤' in a.get('type', '')]

    # 5 колонок вместо 3
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.error(f'🔴 Срывы: {len(reds)}')
    c2.warning(f'🟡 Сдвиги: {len(yellows)}')
    c3.info(f'🟠 Дефициты в спринте: {len(oranges)}')
    c4.success(f'🟣 Парттайм: {len(purples)}')
    c5.error(f'🟤 Стр. дефицит: {len(browns)}')

    for alert in c_alerts:
        text = f"**{alert.get('type', 'Алерт')}** | {alert.get('task_id', '')} ({alert.get('initiative', '')})\n\n{alert.get('description', '')}"
        if '🔴' in alert.get('type', '') or '🟤' in alert.get('type', ''):
            st.error(text)
        elif '🟡' in alert.get('type', ''):
            st.warning(text)
        else:
            st.info(text)

# ===== УПРАВЛЕНИЕ ДАННЫМИ (Auth_redact.md, разделы 7-12) =====
with tab_manage:
    if auth.require_permission('edit_data', 'management'):
        data_management.render(storage)

# ===== ЖУРНАЛ СОБЫТИЙ (Auth_redact.md, раздел 13) =====
with tab_events:
    if auth.require_permission('view_audit', 'event_log'):
        data_management.render_event_log(storage)
