import streamlit as st
import pandas as pd
import plotly.express as px
import sys
import os
from datetime import date, timedelta

sys.path.append(os.path.abspath("src"))
from scheduler import SmartScheduler
from analytics import StarMapAnalytics

st.set_page_config(page_title="ПочтаТех — Планировщик", page_icon="📦", layout="wide")

# ===== КРУПНЫЙ, РАЗБОРЧИВЫЙ ИНТЕРФЕЙС =====
# Базовый Streamlit-стиль довольно мелкий и плотный для дашборда, которым
# пользуются тимлиды/РМ/ИТ-директор на своём экране, а не разработчики в IDE.
# Увеличиваем шрифты ключевых элементов и добавляем видимые границы карточкам
# метрик, чтобы контраст между 4 KPI на первом экране считывался сразу.
st.markdown("""
<style>
html, body, [class*="css"]  { font-size: 17px; }
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
""", unsafe_allow_html=True)

st.title("📦 ПочтаТех: Квартальное планирование (PI)")

with st.expander("📖 Как читать этот дашборд — короткая справка"):
    st.markdown("""
- **Bus Factor** — сколько человек в компании (или в конкретной команде) владеют технологией.
  Bus Factor = 1 — риск: если этот человек в отпуске/уволился, работу по технологии выполнить некому.
- **«Растянута (Сплиттинг)»** — задаче не хватило часов специалистов в одном спринте, она продолжается в следующем.
- **«Реорганизация (Перевод)»** — алгоритм временно взял часы специалиста из другой команды, у которой их не хватало в своей.
- **Зазор между зависимостями** — если задача А блокирует задачу Б, Б не может начаться раньше спринта, следующего за тем, в котором А завершилась (нельзя стартовать в тот же спринт).
- **Say/Do Ratio** — сколько Story Points реально сделано по факту от того, что было изначально запланировано (не путать с самим пересчитанным планом).
- **🔴🟡🟠 алерты** — 🔴 задача точно не уложится в квартал; 🟡 её задержка реально сдвинула другие задачи; 🟠 в конкретном спринте не хватило часов роли даже после попытки перевода из других команд.
    """)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..")) if os.path.basename(
    CURRENT_DIR) == "src" else CURRENT_DIR
DATA_PATH = os.path.join(PROJECT_ROOT, "data", "dataset.xlsx")


@st.cache_data
def load_base_data():
    sched = SmartScheduler(DATA_PATH)
    analytics = StarMapAnalytics(DATA_PATH)
    return sched, analytics, sched.run_smart_planning()


sched, analytics, base_results = load_base_data()
b_schedule, b_statuses, b_logs, b_alerts, b_kpis, b_burned = base_results
bf_df = analytics.get_bus_factor_and_training()
roles_df = analytics.get_critical_roles_shortage()

# ===== СИНХРОНИЗАЦИЯ С РЕАЛЬНЫМ КАЛЕНДАРЁМ =====
# В датасете есть planned_start/planned_end по задачам, но они разбросаны
# почти на год (сентябрь 2025 — июль 2026) и не образуют один связный
# 12-недельный квартал — выводить дату старта квартала ИЗ них означало бы
# подсунуть недостоверную дату. Поэтому старт квартала — явный выбор
# пользователя, а не вычисление из зашумлённых данных.
st.sidebar.header("📅 Календарь квартала")
default_quarter_start = date.today() - timedelta(days=date.today().weekday())  # ближайший понедельник, для аккуратности
quarter_start = st.sidebar.date_input("Дата старта квартала (Спринт 1):", value=default_quarter_start)

sprint_date_ranges = {}
for i in range(1, 7):
    s_start = quarter_start + timedelta(days=14 * (i - 1))
    s_end = s_start + timedelta(days=13)
    sprint_date_ranges[i] = (s_start, s_end)


def sprint_label(i: int) -> str:
    s_start, s_end = sprint_date_ranges[i]
    return f"Спринт {i} ({s_start.strftime('%d.%m')}–{s_end.strftime('%d.%m')})"


# Автоподсказка текущего спринта по сегодняшней дате — пользователь всё
# равно может выбрать вручную любой другой пункт в селекторе ниже.
# Считаем спринт "завершённым" только если сегодняшняя дата ПОЗЖЕ его
# конца — если сегодня внутри диапазона спринта, он ещё идёт и не должен
# считаться пройденным по умолчанию.
today = date.today()
auto_sprint_idx = 0
for i in range(1, 7):
    _, s_end = sprint_date_ranges[i]
    if today > s_end:
        auto_sprint_idx = i

# ===== БОКОВАЯ ПАНЕЛЬ: УМНЫЙ ВВОД ФАКТА =====
st.sidebar.markdown("---")
st.sidebar.header("⏳ Симуляция времени (Факт)")
sprint_options = ["Старт квартала (Спринт 0)"] + [f"Завершен {sprint_label(i)}" for i in range(1, 6)]
selected_time = st.sidebar.selectbox(
    "Выберите текущую дату:", sprint_options,
    index=min(auto_sprint_idx, len(sprint_options) - 1),
    help="По умолчанию подставлен спринт, в который попадает сегодняшняя дата при выбранном старте квартала."
)
current_time_sprint = sprint_options.index(selected_time)

fact_sprint_done = {}
if current_time_sprint > 0:
    st.sidebar.markdown("### ✅ Внесение факта")
    st.sidebar.caption("Отметьте галочкой то, что реально завершено. Снятая галочка — задача переезжает в следующий спринт.")

    available_tasks = sched.tasks['task_id'].tolist()

    for s in range(1, current_time_sprint + 1):
        with st.sidebar.expander(f"Факт: {sprint_label(s)}", expanded=(s == current_time_sprint)):
            # Основной список — то, что ИЗНАЧАЛЬНО планировалось на этот спринт
            # (чек-лист с чекбоксом на каждую задачу, а не мультиселект, где
            # "не завершено" приходилось выражать УДАЛЕНИЕМ строки — легко
            # промахнуться и не всегда очевидно, что осталось).
            baseline_ids = [t['task_id'] for t in b_schedule[s] if
                            'Завершена' in t['status'] and t['task_id'] in available_tasks]

            if baseline_ids:
                rows = []
                for tid in baseline_ids:
                    trow = sched.tasks.loc[sched.tasks['task_id'] == tid].iloc[0]
                    rows.append({'Задача': tid, 'Команда': trow['team_id'], 'SP': int(trow['estimation_sp']),
                                'Завершено': True})
                edit_df = pd.DataFrame(rows)
                edited = st.data_editor(
                    edit_df,
                    column_config={'Завершено': st.column_config.CheckboxColumn('Реально завершено?')},
                    disabled=['Задача', 'Команда', 'SP'],
                    hide_index=True, use_container_width=True, key=f"editor_{s}"
                )
                selected = edited.loc[edited['Завершено'], 'Задача'].tolist()
            else:
                st.caption("На этот спринт по базовому плану ничего не было запланировано.")
                selected = []

            # Отдельно — если по факту сделали что-то СВЕРХ исходного плана
            extra_pool = [t for t in available_tasks if t not in baseline_ids]
            extra_selected = st.multiselect(
                f"Дополнительно завершено (не было в плане на спринт {s}):",
                options=extra_pool, key=f"extra_{s}"
            )

            for t in selected + extra_selected:
                fact_sprint_done[t] = s
                available_tasks.remove(t)

# ===== БОКОВАЯ ПАНЕЛЬ: УПРАВЛЯЕМАЯ РЕОРГАНИЗАЦИЯ =====
# Алгоритм сам переводит часы специалистов между командами при дефиците —
# это и есть автоматическая реализация "команды можно перестраивать". Но
# тимлид должен видеть масштаб этих решений и иметь возможность вмешаться
# вручную, а не узнавать о переводах только постфактум из лога.
st.sidebar.markdown("---")
st.sidebar.header("🔧 Управление реорганизацией")
transfer_summary = sched.get_transfer_summary(b_logs)
forbidden_donors = set()
if not transfer_summary.empty:
    st.sidebar.caption(
        "Алгоритм сам перевёл часы специалистов между командами там, где не хватало "
        "своих. Список ниже — из базового плана. Можно запретить конкретный перевод "
        "и пересчитать план с учётом этого ограничения."
    )
    ts = transfer_summary.copy()
    ts['_key'] = ts['Команда-донор'] + ' отдаёт «' + ts['Роль'] + '»'
    options = ts.drop_duplicates('_key')['_key'].tolist()
    picked = st.sidebar.multiselect(
        "Запретить перевод от команды-донора:",
        options=options,
        help="Например: «Team-L отдаёт «Разработчик Java»» — если запретить, их задачи в приоритете перед чужими."
    )
    for p in picked:
        donor, role_part = p.split(' отдаёт «')
        forbidden_donors.add((donor, role_part.rstrip('»')))

    with st.sidebar.expander(f"Все автопереводы в базовом плане ({len(transfer_summary)})"):
        st.dataframe(transfer_summary, use_container_width=True, height=250)
else:
    st.sidebar.caption("В базовом плане переводов между командами не потребовалось.")

# 2. ПЕРЕСЧЕТ ПЛАНА НА ЛЕТУ
needs_recompute = current_time_sprint > 0 or len(forbidden_donors) > 0
if needs_recompute:
    c_schedule, c_statuses, c_logs, c_alerts, c_kpis, c_burned = sched.run_smart_planning(
        fact_sprint_done=fact_sprint_done,
        current_time_sprint=current_time_sprint,
        baseline_schedule=b_schedule,  # нужен, чтобы Say/Do считался от исходного плана, а не от самого себя
        forbidden_donors=forbidden_donors
    )
else:
    c_schedule, c_statuses, c_logs, c_alerts, c_kpis, c_burned = b_schedule, b_statuses, b_logs, b_alerts, b_kpis, b_burned

# ===== БЛОК KPI =====
col1, col2, col3, col4 = st.columns(4)
col1.metric("Предсказуемость квартала", c_kpis['PI Predictability Measure'], "Норма 80-100%")
col2.metric("Инициатив завершено", f"{c_kpis['Fully Completed Initiatives']} из {c_kpis['Total Initiatives']}")
col3.metric("Say/Do Ratio (по факту)", c_kpis['Sprint Say/Do Ratio'])
critical_bf = len(bf_df[bf_df['Bus Factor'] == 1])
col4.metric("Уязвимых технологий (BF=1)", f"{critical_bf} из {len(bf_df)}", f"-{critical_bf} точек отказа",
            delta_color="inverse")
st.divider()

# ===== ЭКРАН СРАВНЕНИЯ ПЛАН vs ФАКТ =====
say_do_detail = c_kpis.get('Say/Do Detail') or {}
if say_do_detail:
    with st.expander("📊 План vs Факт по пройденным спринтам", expanded=True):
        detail_rows = [
            {'Спринт': sprint_label(s), 'Запланировано SP (baseline)': v['planned_sp'],
             'Сделано по факту SP': v['done_sp'], 'Say/Do': f"{v['ratio']:.1f}%"}
            for s, v in sorted(say_do_detail.items())
        ]
        st.dataframe(pd.DataFrame(detail_rows), use_container_width=True)

# ===== ЗАМЕЧАНИЯ К КАЧЕСТВУ ДАННЫХ =====
dq_warnings = sched.get_data_quality_warnings()
if dq_warnings:
    with st.expander(f"⚠️ Замечания к исходным данным ({len(dq_warnings)})"):
        for w in dq_warnings:
            st.caption(f"• {w}")

# ===== ВКЛАДКИ =====
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📅 План и Задачи", "👥 Трекер сотрудников", "💡 Лог решений", "⭐ Звездная карта", "⚠️ Алерты"])

# Вкладка 1: План
with tab1:
    gantt_records = [{'Task': t['task_id'], 'Summary': t['summary'], 'Team': t['team'],
                      'Sprint_Num': s, 'Sprint': sprint_label(s),
                      'Initiative': t['initiative'], 'Status': t['status'], 'SP': t['sp']} for s, tasks in
                     c_schedule.items() for t in tasks]
    if gantt_records:
        gantt_df = pd.DataFrame(gantt_records)
        fig = px.bar(gantt_df, x="Sprint_Num", y="Task", color="Team", text="Status",
                     hover_data=["Sprint", "Initiative", "SP", "Summary"],
                     title="Диаграмма Ганта (Горизонт 12 недель)",
                     orientation='h')
        fig.update_layout(
            yaxis={'categoryorder': 'total ascending'}, height=600,
            xaxis=dict(
                tickmode='array', tickvals=list(range(1, 7)),
                ticktext=[sprint_label(i) for i in range(1, 7)]
            )
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("❌ Задачи, не поместившиеся в квартал (Срыв 12 недель)")

    def classify_reason(reason: str) -> str:
        if 'Блокируется' in reason:
            return '🟡 Ждёт зависимость'
        if 'Превышен лимит SP' in reason:
            return '🟠 Не хватает SP команды'
        if 'дефицит специалистов' in reason.lower():
            return '🔴 Дефицит специалистов'
        return '⚪ Другое'

    unscheduled = []
    for _, row in sched.tasks.iterrows():
        t_id = row['task_id']
        if c_statuses.get(t_id) != 'Done':
            reasons = c_logs[(c_logs['task_id'] == t_id) & (c_logs['action'] == 'Перенос')]
            last_reason = reasons.iloc[-1]['reason'] if not reasons.empty else "Критический дефицит ресурсов"
            unscheduled.append({
                'Задача': t_id, 'Инициатива': row['Номер инициативы'], 'Команда': row['team_id'],
                'Категория': classify_reason(last_reason),
                'Причина невыполнения (Объяснение)': last_reason,
            })

    if unscheduled:
        uns_df = pd.DataFrame(unscheduled)
        color_map = {
            '🔴 Дефицит специалистов': '#ffdede',
            '🟠 Не хватает SP команды': '#ffe9c7',
            '🟡 Ждёт зависимость': '#fff6c9',
            '⚪ Другое': '#eeeeee',
        }

        def highlight_row(row):
            color = color_map.get(row['Категория'], '')
            # ВАЖНО: явно задаём тёмный цвет текста поверх светлого фона.
            # Без этого в тёмной теме Streamlit текст по умолчанию светлый,
            # и на пастельном фоне становится практически нечитаемым —
            # именно это и произошло в прошлой версии.
            return [f'background-color: {color}; color: #1a1a1a'] * len(row)

        st.dataframe(uns_df.style.apply(highlight_row, axis=1), use_container_width=True)
    else:
        st.success("Все задачи успешно распределены в квартал!")

# Вкладка 2: НОВЫЙ ТРЕКЕР СОТРУДНИКОВ
with tab2:
    st.subheader("Загрузка и эффективность сотрудников (за квартал)")
    st.write("Сводная таблица утилизации инженеров на основе списанных алгоритмом часов.")

    emp_data = []
    for _, emp in sched.engineers_df.iterrows():
        e_id, team, role, cap_rate = emp['engineer_id'], emp['team_id'], emp['role_norm'], emp['capacity_rate']

        team_role_cap_sprint = sched.team_role_hours.get(team, {}).get(role, 0)
        burned_for_role = c_burned.get(team, {}).get(role, 0)

        if team_role_cap_sprint > 0:
            emp_quarter_cap = (cap_rate * 80) * 6
            emp_burned = burned_for_role * ((cap_rate * 80) / team_role_cap_sprint)
            util_pct = (emp_burned / emp_quarter_cap) * 100 if emp_quarter_cap > 0 else 0
        else:
            emp_quarter_cap = emp_burned = util_pct = 0

        emp_data.append({
            'Инженер': e_id, 'Команда': team, 'Роль': role, 'Ставка': cap_rate,
            'Доступно (ЧЧ/кв)': int(emp_quarter_cap), 'Загрузка (ЧЧ/кв)': int(emp_burned),
            'Утилизация (%)': f"{min(100, util_pct):.1f}%"
        })

    emp_df = pd.DataFrame(emp_data).sort_values(by=['Утилизация (%)', 'Команда'], ascending=[False, True])
    st.dataframe(emp_df, use_container_width=True, height=600)

    st.markdown("---")
    st.markdown("##### Историческая стабильность команд")
    st.caption(
        "Не только средняя скорость (Velocity), но и насколько предсказуемо команда "
        "исполняла СВОЙ ЖЕ план в прошлом — две команды с одинаковым средним могут "
        "сильно отличаться по разбросу (одна стабильна, другая скачет от спринта к спринту)."
    )
    st.dataframe(sched.get_team_reliability(), use_container_width=True)

# Вкладка 3: Логи
with tab3:
    col_f1, col_f2, col_f3 = st.columns(3)
    action_filter = col_f1.multiselect("Действие:", options=c_logs['action'].unique(),
                                       default=['Реорганизация (Перевод)', 'Перенос'])
    team_options = sorted(c_logs['team'].dropna().unique().tolist()) if 'team' in c_logs.columns else []
    team_filter = col_f2.multiselect("Команда:", options=team_options)
    init_options = sorted(c_logs['initiative'].dropna().unique().tolist()) if 'initiative' in c_logs.columns else []
    init_filter = col_f3.multiselect("Инициатива:", options=init_options)

    search = st.text_input("Поиск по task_id:")
    f_logs = c_logs[c_logs['action'].isin(action_filter)]
    if team_filter: f_logs = f_logs[f_logs['team'].isin(team_filter)]
    if init_filter: f_logs = f_logs[f_logs['initiative'].isin(init_filter)]
    if search: f_logs = f_logs[f_logs['task_id'].str.contains(search.strip(), case=False)]
    st.dataframe(f_logs, use_container_width=True, height=400)

# Вкладка 4: Звездная карта
with tab4:
    st.markdown("##### Bus Factor по компании")
    col_bf1, col_bf2 = st.columns([2, 1])
    with col_bf1: st.dataframe(bf_df, use_container_width=True, height=400)
    with col_bf2: st.dataframe(roles_df[roles_df['Риск'].str.contains('Высокий')], use_container_width=True)

    st.markdown("---")
    st.markdown("##### Bus Factor по командам")
    st.caption(
        "Навык может быть не критичным для компании в целом (несколько носителей), "
        "но критичным для КОНКРЕТНОЙ команды, если все остальные носители — в других командах."
    )
    bf_team_df = analytics.get_bus_factor_by_team()
    team_filter = st.selectbox("Команда:", ["Все"] + sorted(bf_team_df['Команда'].unique().tolist()))
    view_df = bf_team_df if team_filter == "Все" else bf_team_df[bf_team_df['Команда'] == team_filter]
    st.dataframe(view_df[view_df['Bus Factor команды'] == 1], use_container_width=True, height=350)

# Вкладка 5: Алерты
with tab5:
    reds = [a for a in c_alerts if '🔴' in a['type']]
    yellows = [a for a in c_alerts if '🟡' in a['type']]
    oranges = [a for a in c_alerts if '🟠' in a['type']]
    c1, c2, c3 = st.columns(3)
    c1.error(f"🔴 Срывов: {len(reds)}");
    c2.warning(f"🟡 Сдвигов: {len(yellows)}");
    c3.info(f"🟠 Дефицитов: {len(oranges)}")
    for a in c_alerts:
        if '🔴' in a['type']:
            st.error(f"**{a['type']}** | {a['task_id']} ({a['initiative']})\n\n{a['description']}")
        elif '🟡' in a['type']:
            st.warning(f"**{a['type']}** | {a['task_id']} ({a['initiative']})\n\n{a['description']}")
        else:
            st.info(f"**{a['type']}** | {a['description']}")