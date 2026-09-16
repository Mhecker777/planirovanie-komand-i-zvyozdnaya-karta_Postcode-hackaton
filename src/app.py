import streamlit as st
import pandas as pd
import plotly.express as px
import sys
import os

# Добавляем папку src в путь поиска модулей
sys.path.append(os.path.abspath("src"))

from scheduler import SmartScheduler
from analytics import StarMapAnalytics

# Настройка страницы
st.set_page_config(
    page_title="ПочтаТех — Планировщик и Звездная Карта",
    page_icon="📦",
    layout="wide"
)

# Заголовок
st.title("📦 ПочтаТех: Квартальный цикл планирования (PI) и KPI")
st.caption("Автоматизированная система балансировки бэклога, анализа рисков и Звездной карты компетенций")

# Автоматическое определение пути к файлу с данными
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Если app.py лежит в папке src, поднимаемся на уровень выше
if os.path.basename(CURRENT_DIR) == "src":
    PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
else:
    PROJECT_ROOT = CURRENT_DIR

DATA_PATH = os.path.join(PROJECT_ROOT, "data", "dataset.csv")

# Дополнительная проверка на случай, если файл назван по-другому
if not os.path.exists(DATA_PATH):
    alt_path = os.path.join(PROJECT_ROOT, "data", "Датасет_с_правками_основной.csv")
    if os.path.exists(alt_path):
        DATA_PATH = alt_path


@st.cache_data
def load_and_calculate():
    sched = SmartScheduler(DATA_PATH)
    schedule, final_statuses, logs_df, alerts, kpis = sched.run_smart_planning()

    analytics = StarMapAnalytics(DATA_PATH)
    bf_df = analytics.get_bus_factor()
    roles_df = analytics.get_critical_roles_shortage()

    return sched, schedule, final_statuses, logs_df, alerts, kpis, bf_df, roles_df


sched, schedule, final_statuses, logs_df, alerts, kpis, bf_df, roles_df = load_and_calculate()

# ===== БЛОК KPI =====
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Предсказуемость квартала (KPI)", kpis['PI Predictability Measure'], "Норма 80-100%")
with col2:
    st.metric("Инициатив завершено", f"{kpis['Fully Completed Initiatives']} из {kpis['Total Initiatives']}")
with col3:
    st.metric("Точность спринта 1 (Say/Do)", kpis['Sprint 1 Say/Do Ratio'])
with col4:
    critical_bf_count = len(bf_df[bf_df['Bus Factor'] == 1])
    st.metric("Критических компетенций (BF=1)", critical_bf_count, delta="Риск для бэклога", delta_color="inverse")

st.divider()

# ===== ВКЛАДКИ ПРИЛОЖЕНИЯ =====
tab1, tab2, tab3, tab4 = st.tabs([
    "📅 Квартальный план спринтов",
    "💡 Лог решений (Explainability)",
    "⭐ Звездная карта и Bus Factor",
    "⚠️ Риски и Алерты"
])

# Вкладка 1: План спринтов
with tab1:
    st.subheader("Распределение ИТ-работ по спринтам")

    # Собираем датасет для визуализации диаграммы Ганта
    gantt_records = []
    for sp_num, task_list in schedule.items():
        for t in task_list:
            gantt_records.append({
                'Task': t['task_id'],
                'Summary': t['summary'],
                'Team': t['team'],
                'Sprint': f"Спринт {sp_num}",
                'Sprint_Num': sp_num,
                'Initiative': t['initiative'],
                'Status': t['status'],
                'SP': t['sp']
            })
    gantt_df = pd.DataFrame(gantt_records)

    if not gantt_df.empty:
        # Диаграмма распределения
        fig = px.bar(
            gantt_df,
            x="Sprint_Num",
            y="Task",
            color="Team",
            text="Status",
            hover_data=["Initiative", "SP", "Summary"],
            title="Задачи в спринтах (горизонт 12 недель)",
            labels={"Sprint_Num": "Номер спринта", "Task": "Задача"},
            orientation='h'
        )
        fig.update_layout(yaxis={'categoryorder': 'total ascending'}, height=600)
        st.plotly_chart(fig, use_container_width=True)

        # Таблица плана с фильтром по спринтам
        selected_sprint = st.selectbox("Выберите спринт для просмотра деталей:", [f"Спринт {i}" for i in range(1, 7)])
        sprint_num = int(selected_sprint.split()[-1])

        current_tasks = [t for t in schedule[sprint_num]]
        if current_tasks:
            st.dataframe(pd.DataFrame(current_tasks), use_container_width=True)
        else:
            st.info("В этот спринт не было запланировано новых задач (ресурсный дефицит или все выполнено).")

# Вкладка 2: Explainability
with tab2:
    st.subheader("Прозрачность решений алгоритма (Explainability)")
    st.markdown("Здесь система объясняет, почему каждая задача была включена, перенесена или отложена.")

    action_filter = st.multiselect("Фильтр по действию:", options=logs_df['action'].unique(),
                                   default=['Перенос', 'Включена в план'])
    filtered_logs = logs_df[logs_df['action'].isin(action_filter)]

    search_task = st.text_input("Поиск по task_id (например: DB-202, SRV-4011):")
    if search_task:
        filtered_logs = filtered_logs[filtered_logs['task_id'].str.contains(search_task.strip(), case=False)]

    st.dataframe(filtered_logs, use_container_width=True, height=400)

# Вкладка 3: Звездная карта и Bus Factor
with tab3:
    st.subheader("Анализ незаменимости (Bus Factor) и Звездная карта")
    st.markdown("🎯 **Целевой показатель:** Bus Factor > 1 для всех критических технологий.")

    col_bf1, col_bf2 = st.columns([2, 1])
    with col_bf1:
        st.markdown("##### Компетенции и риски Bus Factor")
        st.dataframe(bf_df, use_container_width=True, height=400)
    with col_bf2:
        st.markdown("##### Риски дефицита в командах (1 сотрудник на роль)")
        st.dataframe(roles_df[roles_df['Риск'].str.contains('Высокий')], use_container_width=True)

# Вкладка 4: Алерты
with tab4:
    st.subheader("Система раннего предупреждения рисков")
    st.error(f"🔴 Критических срывов инициатив: {len(alerts)}")

    for a in alerts:
        with st.expander(f"{a['type']} — {a['task_id']} ({a['initiative']})"):
            st.write(a['description'])
            st.write(
                "Рекомендация: перенести на следующий квартальный цикл (PI+1) согласно справочнику «Варианты выбора цели».")