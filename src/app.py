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


@st.cache_resource(show_spinner=False)
def load_models(data_path: str):
    sched = SmartScheduler(data_path)
    analytics = StarMapAnalytics(data_path)
    return sched, analytics


@st.cache_data(show_spinner=False)
def load_base_plan(data_path: str):
    sched = SmartScheduler(data_path)
    return sched.run_smart_planning()


sched, analytics = load_models(DATA_PATH)
b_schedule, b_statuses, b_logs, b_alerts, b_kpis, b_burned = load_base_plan(DATA_PATH)

bf_df = analytics.get_bus_factor_and_training()
bf_team_df = analytics.get_bus_factor_by_team()
roles_df = analytics.get_critical_roles_shortage()

# ===== КАЛЕНДАРЬ =====
st.sidebar.header('📅 Календарь квартала')
default_quarter_start = date.today() - timedelta(days=date.today().weekday())
quarter_start = st.sidebar.date_input(
    'Дата старта квартала (Спринт 1):',
    value=default_quarter_start,
)

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


today = date.today()
auto_sprint_idx = 0
for i in range(1, 7):
    if today > sprint_date_ranges[i][1]:
        auto_sprint_idx = i

st.sidebar.markdown('---')
st.sidebar.header('⏳ Симуляция времени (Факт)')
sprint_options = ['Старт квартала (Спринт 0)'] + [f'Завершен {sprint_label(i)}' for i in range(1, 7)]
selected_time = st.sidebar.selectbox(
    'Выберите текущую дату:',
    sprint_options,
    index=min(auto_sprint_idx, len(sprint_options) - 1),
    help='По умолчанию выбран последний полностью завершившийся спринт.',
)
current_time_sprint = sprint_options.index(selected_time)

# ===== ФАКТ =====
fact_sprint_done = {}
if current_time_sprint > 0:
    st.sidebar.markdown('### ✅ Внесение факта')
    st.sidebar.caption('Отметьте только реально завершённые задачи. По умолчанию все чекбоксы сняты.')

    all_task_ids = sched.tasks['task_id'].tolist()
    for sprint in range(1, current_time_sprint + 1):
        with st.sidebar.expander(f'Факт: {sprint_label(sprint)}', expanded=(sprint == current_time_sprint)):
            baseline_ids = []
            seen = set()
            for item in b_schedule.get(sprint, []):
                tid = item['task_id']
                if tid in all_task_ids and tid not in seen:
                    baseline_ids.append(tid)
                    seen.add(tid)

            prior_fact_ids = set(fact_sprint_done)
            baseline_ids = [tid for tid in baseline_ids if tid not in prior_fact_ids]

            if baseline_ids:
                rows = []
                for tid in baseline_ids:
                    matches = sched.tasks.loc[sched.tasks['task_id'] == tid]
                    if matches.empty:
                        continue
                    trow = matches.iloc[0]
                    rows.append(
                        {
                            'Задача': tid,
                            'Команда': trow['team_id'],
                            'SP': int(trow['estimation_sp']),
                            'Завершено': True,
                        }
                    )

                if rows:
                    edit_df = pd.DataFrame(rows)
                    edited = st.data_editor(
                        edit_df,
                        column_config={'Завершено': st.column_config.CheckboxColumn('Реально завершено?')},
                        disabled=['Задача', 'Команда', 'SP'],
                        hide_index=True,
                        use_container_width=True,
                        key=f'editor_{sprint}',
                    )
                    selected = edited.loc[edited['Завершено'], 'Задача'].tolist()
                else:
                    selected = []
            else:
                st.caption('На этот спринт по baseline-плану ничего не осталось для подтверждения.')
                selected = []

            extra_pool = [
                tid for tid in all_task_ids
                if tid not in baseline_ids and tid not in prior_fact_ids
            ]
            extra_selected = st.multiselect(
                'Дополнительно завершено (не было в baseline на этот спринт):',
                options=extra_pool,
                key=f'extra_{sprint}',
            )

            for tid in selected + extra_selected:
                if tid not in fact_sprint_done:
                    fact_sprint_done[tid] = sprint

# Валидация фактических зависимостей
fact_dependency_warnings = []
for task_id, done_sprint in fact_sprint_done.items():
    if task_id not in sched.G:
        continue
    for predecessor in sched.G.predecessors(task_id):
        predecessor_sprint = fact_sprint_done.get(predecessor)
        if predecessor_sprint is None:
            fact_dependency_warnings.append(
                f'{task_id} отмечена завершённой в Спринте {done_sprint}, но зависимость {predecessor} в факте не завершена.'
            )
        elif predecessor_sprint >= done_sprint:
            fact_dependency_warnings.append(
                f'{task_id} отмечена в Спринте {done_sprint}, но зависимость {predecessor} завершена в Спринте {predecessor_sprint}.'
            )

if fact_dependency_warnings:
    st.sidebar.warning('⚠️ Обнаружены нарушения фактических зависимостей')
    with st.sidebar.expander(f'Показать нарушения ({len(fact_dependency_warnings)})'):
        for warning in fact_dependency_warnings:
            st.caption(f'• {warning}')

# ===== УПРАВЛЯЕМАЯ РЕОРГАНИЗАЦИЯ =====
st.sidebar.markdown('---')
st.sidebar.header('🔧 Управление реорганизацией')
transfer_summary = sched.get_transfer_summary(b_logs)
forbidden_donors = set()
if not transfer_summary.empty:
    st.sidebar.caption(
        'Алгоритм показывает автоматические переводы из baseline. Вы можете запретить конкретную связку «донор + роль» и пересчитать план.'
    )
    ts = transfer_summary.copy()
    ts['_key'] = ts['Команда-донор'].astype(str) + ' отдаёт «' + ts['Роль'].astype(str) + '»'
    options = ts.drop_duplicates('_key')['_key'].tolist()
    picked = st.sidebar.multiselect('Запретить перевод от команды-донора:', options=options)
    for value in picked:
        donor, role_part = value.split(' отдаёт «', 1)
        forbidden_donors.add((donor, role_part.rstrip('»')))
    with st.sidebar.expander(f'Все автопереводы в baseline ({len(transfer_summary)})'):
        st.dataframe(transfer_summary, use_container_width=True, hide_index=True)
else:
    st.sidebar.caption('В baseline-плане переводов между командами не потребовалось.')

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
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ['📅 План и Задачи', '👥 Трекер сотрудников', '💡 Лог решений', '⭐ Звездная карта', '⚠️ Алерты']
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

    gantt_records = []
    for tid, info in task_sprints.items():
        sprints = sorted(info['sprints'])
        start_sprint = sprints[0]
        end_sprint = sprints[-1]
        start_date = sprint_date_ranges[start_sprint][0]
        end_date = sprint_date_ranges[end_sprint][1]
        gantt_records.append({
            'Task': tid,
            'Summary': info['summary'],
            'Team': info['team'],
            'Initiative': info['initiative'],
            'Rung': info['rung'],
            'Start': start_date,
            'Finish': end_date,
            'Sprints': ', '.join(f'Спринт {s}' for s in sprints),
            'Statuses': ', '.join(info['statuses']),
            'SP': info['sp'],
            'Списано HH': round(info['burned_hh'], 1),
        })

    if gantt_records:
        gantt_df = pd.DataFrame(gantt_records)

        fig = px.timeline(
            gantt_df,
            x_start='Start',
            x_end='Finish',
            y='Task',
            color='Team',
            hover_data=['Summary', 'Initiative', 'Rung', 'Sprints', 'Statuses', 'SP', 'Списано HH'],
            title='График выполнения задач по кварталу',
        )

        # Подписи спринтов по оси X
        tickvals = []
        ticktext = []
        for i in range(1, 7):
            start, end = sprint_date_ranges[i]
            tickvals.append(start + (end - start) / 2)
            ticktext.append(f'Спринт {i}')

        fig.update_layout(
            height=max(500, min(1200, 30 * gantt_df['Task'].nunique() + 250)),
            xaxis=dict(
                tickmode='array',
                tickvals=tickvals,
                ticktext=ticktext,
                title='Спринты квартала',
            ),
            yaxis=dict(
                autorange='reversed',
                title='Задача',
            ),
            legend_title='Команда',
        )

        st.plotly_chart(fig, use_container_width=True)

        # CSV для скачивания — оставляем, но на основе gantt_df
        export_df = gantt_df.copy()
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
    st.caption('Утилизация распределяется пропорционально capacity_rate внутри одной связки «команда + роль».')

    emp_data = []
    for _, emp in sched.engineers_df.iterrows():
        engineer_id = emp.get('engineer_id', '')
        team = emp.get('team_id', '')
        role = emp.get('role_norm', '')
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

        emp_data.append(
            {
                'Инженер': engineer_id,
                'Команда': team,
                'Роль': role,
                'Ставка': cap_rate,
                'Доступно (ЧЧ/кв)': round(emp_quarter_cap, 1),
                'Загрузка (ЧЧ/кв)': round(emp_burned, 1),
                'Утилизация (%)': f'{min(100, max(0, util_pct)):.1f}%',
            }
        )

    emp_df = pd.DataFrame(emp_data)
    if not emp_df.empty:
        emp_df['_util_num'] = pd.to_numeric(emp_df['Утилизация (%)'].str.rstrip('%'), errors='coerce').fillna(0)
        emp_df = emp_df.sort_values(by=['_util_num', 'Команда'], ascending=[False, True]).drop(columns='_util_num')
    st.dataframe(emp_df, use_container_width=True, height=600, hide_index=True)

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
    search = st.text_input('Поиск по task_id:')

    f_logs = c_logs.copy()
    if action_filter:
        f_logs = f_logs[f_logs['action'].isin(action_filter)]
    if team_filter:
        f_logs = f_logs[f_logs['team'].astype(str).isin(team_filter)]
    if init_filter:
        f_logs = f_logs[f_logs['initiative'].astype(str).isin(init_filter)]
    if search.strip():
        f_logs = f_logs[f_logs['task_id'].astype(str).str.contains(search.strip(), case=False, na=False, regex=False)]
    st.dataframe(f_logs, use_container_width=True, height=450, hide_index=True)

# ===== ВКЛАДКА 4: ЗВЁЗДНАЯ КАРТА =====
# Вкладка 4: Звездная карта
# Вкладка 4: Звездная карта
with tab4:
    with st.expander("🌍 Bus Factor по компании (Глобальный риск)", expanded=True):
        st.dataframe(bf_df, use_container_width=True)

    with st.expander("🏢 Bus Factor по командам (Локальный риск)"):
        st.caption("Навык может быть не критичным для компании в целом, но критичным для КОНКРЕТНОЙ команды.")
        bf_team_df = analytics.get_bus_factor_by_team()
        team_filter = st.selectbox("Команда:", ["Все"] + sorted(bf_team_df['Команда'].unique().tolist()))
        view_df = bf_team_df if team_filter == "Все" else bf_team_df[bf_team_df['Команда'] == team_filter]
        st.dataframe(view_df[view_df['Bus Factor команды'] == 1], use_container_width=True)
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
