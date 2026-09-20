"""
UI управления данными (Auth_redact.md, разделы 7-13).

Модель правок: все изменения сначала попадают в st.session_state['working_dfs']
(рабочая копия 5 DataFrame) и st.session_state['pending_changes'] (список для
батч-лога). Ничего не пишется в SQLite, пока пользователь явно не нажмёт
«Сохранить» на вкладке «Сохранение/Импорт/История» — так можно свободно
экспериментировать и откатить правки кнопкой «Отменить», не трогая БД.
"""
import io
from datetime import datetime

import networkx as nx
import pandas as pd
import streamlit as st

import auth
import re
from datetime import date, timedelta


def _next_engineer_id(eng_df) -> str:
    """ENG-NNN — следующая свободная нумерация."""
    max_n = 0
    for eid in eng_df['engineer_id'].astype(str):
        m = re.match(r'^ENG-(\d+)$', eid.strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f'ENG-{max_n + 1:03d}'


def _next_task_id(tasks_df) -> str:
    max_n = 0
    for tid in tasks_df['task_id'].astype(str):
        m = re.match(r'^NEW-(\d+)$', tid.strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f'NEW-{max_n + 1:03d}'


def _known_roles(dfs) -> list:
    roles = set()
    eng = dfs.get('engineers_df')
    if eng is not None and 'role' in eng.columns:
        roles.update(eng['role'].dropna().astype(str).str.strip())
    est = dfs.get('estimates_df')
    if est is not None and len(est.columns) > 0:
        roles.update(est.iloc[:, 0].dropna().astype(str).str.strip())
    return sorted(r for r in roles if r and r.lower() != 'nan' and r != 'ИТОГО')


def _known_skills(dfs) -> list:
    skills = set()
    eng = dfs.get('engineers_df')
    if eng is not None and 'skills_declared' in eng.columns:
        for val in eng['skills_declared'].dropna().astype(str):
            for s in val.split(','):
                s = s.strip()
                if s and s.lower() != 'nan':
                    skills.add(s)
    return sorted(skills, key=str.lower)


def _known_teams(dfs) -> list:
    teams = set()
    for key in ('engineers_df', 'tasks_df'):
        df = dfs.get(key)
        if df is not None and 'team_id' in df.columns:
            teams.update(df['team_id'].dropna().astype(str).str.strip())
    return sorted(t for t in teams if t and t.lower() != 'nan')

def _dfs() -> dict:
    return st.session_state['working_dfs']


def _stage(action: str, entity_type: str, entity_id=None, field=None,
          old_value=None, new_value=None, comment: str = None) -> None:
    st.session_state.setdefault('pending_changes', []).append({
        'action': action, 'entity_type': entity_type, 'entity_id': entity_id,
        'field': field, 'old_value': old_value, 'new_value': new_value, 'comment': comment,
    })
    st.session_state['dirty'] = True


def _pending_count() -> int:
    return len(st.session_state.get('pending_changes', []))


# ==================================================================
# ИНЖЕНЕРЫ (раздел 7)
# ==================================================================

def render_engineers() -> None:
    dfs = _dfs()
    eng_df = dfs['engineers_df']
    if 'status' not in eng_df.columns:
        eng_df['status'] = 'Активен'
    if 'status_start_date' not in eng_df.columns:
        eng_df['status_start_date'] = ''
    if 'status_end_date' not in eng_df.columns:
        eng_df['status_end_date'] = ''
    eng_df['status'] = eng_df['status'].fillna('Активен').replace('', 'Активен')

    total = len(eng_df)
    active = int((eng_df['status'] == 'Активен').sum())
    sick_vac = int(eng_df['status'].isin(['Больничный', 'Отпуск']).sum())
    fired = int((eng_df['status'] == 'Уволен').sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Всего в штате', total)
    c2.metric('Активных', active)
    c3.metric('Больничный/Отпуск', sick_vac)
    c4.metric('Уволено', fired)

    st.markdown('---')
    st.markdown('##### ➕ Добавить инженера')
    with st.form('add_engineer_form', clear_on_submit=True):
        auto_id = _next_engineer_id(eng_df)
        st.text_input('ID (генерируется автоматически)', value=auto_id, disabled=True)

        col1, col2 = st.columns(2)
        teams = _known_teams(dfs)
        team_options = ['— Новая команда —'] + teams
        new_team = col1.selectbox('Команда', team_options, key='add_eng_team')
        if new_team == '— Новая команда —':
            new_team = col1.text_input('Новый team_id', key='add_eng_team_manual')

        roles = _known_roles(dfs)
        role_options = ['— Своя роль —'] + roles
        new_role = col2.selectbox('Роль', role_options, key='add_eng_role')
        if new_role == '— Своя роль —':
            new_role = col2.text_input('Роль (вручную)', key='add_eng_role_manual')

        existing_skills = _known_skills(dfs)
        picked_skills = st.multiselect('Существующие навыки', existing_skills, key='add_eng_skills_pick')
        extra_skills_raw = st.text_input('Дополнительные навыки (через запятую)', key='add_eng_skills_extra')
        extra_skills = [s.strip() for s in extra_skills_raw.split(',') if s.strip()]
        new_skills = ', '.join(picked_skills + extra_skills)

        col_cap, col_status = st.columns(2)
        new_cap = col_cap.slider('Ставка', 0.0, 1.0, 1.0, 0.5, key='add_eng_cap')
        new_status = col_status.selectbox('Статус', ['Активен', 'Больничный', 'Отпуск', 'Уволен'], key='add_eng_status')

        status_start_iso = ''
        status_end_iso = ''
        if new_status in ('Больничный', 'Отпуск'):
            cd1, cd2 = st.columns(2)
            start_d = cd1.date_input('Начало периода', value=date.today(), key='add_eng_sdate')
            end_d = cd2.date_input('Окончание периода', value=date.today() + timedelta(days=13), key='add_eng_edate')
            status_start_iso = start_d.isoformat()
            status_end_iso = end_d.isoformat()
        elif new_status == 'Уволен':
            start_d = st.date_input('Дата увольнения', value=date.today(), key='add_eng_fired_date')
            status_start_iso = start_d.isoformat()

        submitted = st.form_submit_button('Добавить')
        if submitted:
            team_clean = (new_team or '').strip()
            role_clean = (new_role or '').strip()
            if not team_clean or not role_clean:
                st.error('Команда и роль обязательны.')
            else:
                new_row = {c: '' for c in eng_df.columns}
                new_row.update({
                    'engineer_id': auto_id, 'team_id': team_clean, 'role': role_clean,
                    'capacity_rate': new_cap, 'skills_declared': new_skills,
                    'status': new_status,
                    'status_start_date': status_start_iso,
                    'status_end_date': status_end_iso,
                })
                dfs['engineers_df'] = pd.concat([eng_df, pd.DataFrame([new_row])], ignore_index=True)
                dfs['engineers_df']['capacity_rate'] = pd.to_numeric(
                    dfs['engineers_df']['capacity_rate'], errors='coerce'
                )
                _stage('add', 'engineer', entity_id=auto_id, new_value=new_row, comment='Добавлен через форму')
                st.success(f'Инженер {auto_id} добавлен (в несохранённых правках).')
                st.rerun()

    st.markdown('---')
    st.markdown('##### ✏️ Редактирование')
    st.caption('Уволенные строки будут полностью исключены из расчёта. Даты статуса видны в отдельных колонках.')
    display_cols = [c for c in
                    ['engineer_id', 'team_id', 'role', 'capacity_rate', 'status',
                     'status_start_date', 'status_end_date', 'skills_declared']
                    if c in eng_df.columns]

    def _row_color(row):
        colors = {'Активен': '#e8f5e9', 'Больничный': '#fff8e1', 'Отпуск': '#fff8e1', 'Уволен': '#ffebee'}
        c = colors.get(row.get('status'), '')
        return [f'background-color: {c}; color: #1a1a1a'] * len(row)

    st.dataframe(eng_df[display_cols].style.apply(_row_color, axis=1), use_container_width=True, height=300)

    edited = st.data_editor(
        eng_df[display_cols], disabled=['engineer_id'], hide_index=True,
        use_container_width=True, num_rows='fixed', key='eng_editor',
        column_config={
            'status': st.column_config.SelectboxColumn('status',
                options=['Активен', 'Больничный', 'Отпуск', 'Уволен']),
            'capacity_rate': st.column_config.NumberColumn('capacity_rate',
                min_value=0.0, max_value=1.0, step=0.1),
        },
    )
    if st.button('Применить правки в таблице выше'):
        changed = 0
        for _, new_row in edited.iterrows():
            eid = new_row['engineer_id']
            old_match = eng_df.loc[eng_df['engineer_id'] == eid]
            if old_match.empty:
                continue
            old_row = old_match.iloc[0]
            for col in display_cols:
                if col == 'engineer_id' or str(old_row[col]) == str(new_row[col]):
                    continue
                _stage('update', 'engineer', entity_id=eid, field=col,
                      old_value=old_row[col], new_value=new_row[col])
                eng_df.loc[eng_df['engineer_id'] == eid, col] = new_row[col]
                changed += 1
        dfs['engineers_df'] = eng_df
        if changed:
            st.success(f'Применено изменений: {changed} (пока не сохранены).')
            st.rerun()
        else:
            st.info('Изменений не найдено.')

    st.markdown('---')
    st.markdown('##### ⚡ Массовые действия')
    selected_ids = st.multiselect('Выбрать инженеров:', eng_df['engineer_id'].tolist(), key='eng_bulk_select')

    with st.expander('Даты для массовых действий', expanded=False):
        bulk_start = st.date_input('Начало (для больничного/отпуска)', value=date.today(), key='bulk_start')
        bulk_end = st.date_input('Окончание (для больничного/отпуска)',
                                 value=date.today() + timedelta(days=13), key='bulk_end')
        bulk_fired = st.date_input('Дата увольнения', value=date.today(), key='bulk_fired')

    def _bulk_status(new_status: str, label: str, start_iso: str = '', end_iso: str = '') -> None:
        if not selected_ids:
            st.warning('Никто не выбран.')
            return
        for eid in selected_ids:
            old = eng_df.loc[eng_df['engineer_id'] == eid, 'status'].values[0]
            eng_df.loc[eng_df['engineer_id'] == eid, 'status'] = new_status
            eng_df.loc[eng_df['engineer_id'] == eid, 'status_start_date'] = start_iso
            eng_df.loc[eng_df['engineer_id'] == eid, 'status_end_date'] = end_iso
            _stage('bulk_status', 'engineer', entity_id=eid, field='status',
                  old_value=old, new_value=new_status,
                  comment=f'{start_iso} — {end_iso}' if start_iso or end_iso else None)
        dfs['engineers_df'] = eng_df
        st.success(f'{label}: {", ".join(selected_ids)}')
        st.rerun()

    b1, b2, b3, b4 = st.columns(4)
    if b1.button('🩺 На больничный'):
        _bulk_status('Больничный', 'Отправлены на больничный',
                    bulk_start.isoformat(), bulk_end.isoformat())
    if b2.button('🏖️ В отпуск'):
        _bulk_status('Отпуск', 'Отправлены в отпуск',
                    bulk_start.isoformat(), bulk_end.isoformat())
    if b3.button('❌ Уволить'):
        _bulk_status('Уволен', 'Уволены', bulk_fired.isoformat(), '')
    if b4.button('↩️ Вернуть в работу'):
        _bulk_status('Активен', 'Возвращены в работу')

# ==================================================================
# КОМАНДЫ (раздел 8)
# ==================================================================

def render_teams() -> None:
    dfs = _dfs()
    eng_df = dfs['engineers_df']
    tasks_df = dfs['tasks_df']

    summary = eng_df.groupby('team_id').agg(
        Инженеров=('engineer_id', 'count'),
        Ролей=('role', 'nunique'),
    ).reset_index().rename(columns={'team_id': 'Команда'})
    task_counts = tasks_df.groupby('team_id')['task_id'].count().rename('Открытых задач')
    summary = summary.merge(task_counts, left_on='Команда', right_index=True, how='left').fillna({'Открытых задач': 0})
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.markdown('---')
    st.markdown('##### ➕ Создать команду')
    st.caption('Команда появится в списках, как только в неё будет добавлен хотя бы один инженер или задача.')
    with st.form('add_team_form', clear_on_submit=True):
        new_team_id = st.text_input('ID новой команды (team_id)')
        if st.form_submit_button('Создать'):
            existing = set(eng_df['team_id'].astype(str)) | set(tasks_df['team_id'].astype(str))
            if not new_team_id.strip():
                st.error('Укажите ID команды.')
            elif new_team_id.strip() in existing:
                st.error(f'Команда «{new_team_id}» уже существует.')
            else:
                _stage('add', 'team', entity_id=new_team_id.strip(),
                      comment='Пустая команда создана — станет видимой после добавления людей/задач')
                st.success(
                    f'Команда «{new_team_id}» зарегистрирована. Добавьте в неё инженера на вкладке «Инженеры», '
                    f'чтобы она появилась в сводке.'
                )

    st.markdown('---')
    st.markdown('##### ✏️ Переименовать команду')
    with st.form('rename_team_form'):
        teams = sorted(set(eng_df['team_id'].astype(str)) | set(tasks_df['team_id'].astype(str)))
        old_name = st.selectbox('Команда:', teams) if teams else None
        new_name = st.text_input('Новое имя')
        if st.form_submit_button('Переименовать') and old_name:
            if not new_name.strip():
                st.error('Укажите новое имя.')
            elif new_name.strip() in teams:
                st.error('Команда с таким именем уже существует.')
            else:
                eng_df.loc[eng_df['team_id'] == old_name, 'team_id'] = new_name.strip()
                tasks_df.loc[tasks_df['team_id'] == old_name, 'team_id'] = new_name.strip()
                dfs['engineers_df'], dfs['tasks_df'] = eng_df, tasks_df
                _stage('rename', 'team', entity_id=old_name, field='team_id',
                      old_value=old_name, new_value=new_name.strip())
                st.success(f'«{old_name}» → «{new_name}»')
                st.rerun()

    st.markdown('---')
    st.markdown('##### 🗑️ Удалить команду')
    st.caption('Удаление возможно только если в команде не осталось ни инженеров, ни задач — иначе сначала перенесите их в другую команду.')
    teams_now = sorted(set(eng_df['team_id'].astype(str)) | set(tasks_df['team_id'].astype(str)))
    del_team = st.selectbox('Команда для удаления:', ['—'] + teams_now, key='del_team_pick')
    if del_team != '—' and st.button('Удалить команду', type='primary'):
        has_eng = (eng_df['team_id'] == del_team).any()
        has_tasks = (tasks_df['team_id'] == del_team).any()
        if has_eng or has_tasks:
            st.error(
                f'Нельзя удалить «{del_team}»: в ней ещё {int((eng_df["team_id"] == del_team).sum())} '
                f'инженеров и {int((tasks_df["team_id"] == del_team).sum())} задач.'
            )
        else:
            _stage('delete', 'team', entity_id=del_team)
            st.success(f'Команда «{del_team}» удалена.')


# ==================================================================
# ЗАДАЧИ (раздел 9)
# ==================================================================

def render_tasks() -> None:
    dfs = _dfs()
    tasks_df = dfs['tasks_df']
    deps_df = dfs['deps_df']
    estimates_df = dfs['estimates_df']
    role_col = estimates_df.columns[0]
    all_roles = estimates_df[role_col].astype(str).tolist()

    st.markdown('##### ➕ Добавить задачу')
    st.markdown('##### ➕ Добавить задачу')
    with st.form('add_task_form', clear_on_submit=True):
        auto_id = _next_task_id(tasks_df)
        st.text_input('task_id (генерируется автоматически)', value=auto_id, disabled=True)

        col1, col2, col3 = st.columns(3)
        new_team = col1.selectbox('Команда', _known_teams(dfs), key='add_task_team')
        new_init = col2.text_input('Номер инициативы', key='add_task_init')
        new_status = col3.selectbox('Статус', ['ToDo', 'InProgress', 'Done', 'Canceled', 'NotTaken'],
                                    key='add_task_status')

        col4, col5 = st.columns(2)
        new_sp = col4.number_input('Story Points (estimation_sp)', min_value=1, value=5, step=1,
                                   key='add_task_sp')
        new_rung = col5.number_input('Приоритет rung (больше = срочнее)', min_value=0, value=50, step=10,
                                     key='add_task_rung')

        new_summary = st.text_area(
            'Краткое описание задачи (summary)',
            key='add_task_summary',
            help='Пара предложений о том, что нужно сделать. Показывается в диаграмме Ганта и в отчётах.',
        )

        st.markdown('**Часы по ролям (ЧЧ).** Заполните только те роли, которые реально нужны — '
                    'остальные оставьте 0.')
        role_hours = {}
        cols = st.columns(3)
        for i, role in enumerate(all_roles):
            role_hours[role] = cols[i % 3].number_input(
                role, min_value=0, value=0, step=5, key=f'newtask_role_{role}'
            )

        estimated_hh = sum(role_hours.values())
        st.info(f'**Итог по ролям (авторасчёт): {estimated_hh} ЧЧ.** '
                f'Это значение уйдёт в `estimated_hh` задачи — используется в сводках и при проверке '
                f'расхождений.')

        submitted = st.form_submit_button('Добавить задачу')
        if submitted:
            new_id_clean = auto_id
            if not new_team.strip():
                st.error('Команда обязательна.')
            elif new_id_clean in tasks_df['task_id'].astype(str).values:
                st.error(f'Задача {new_id_clean} уже существует.')
            else:
                new_row = {c: '' for c in tasks_df.columns}
                new_row.update({
                    'task_id': new_id_clean, 'team_id': new_team.strip(),
                    'Номер инициативы': new_init.strip(), 'estimation_sp': new_sp,
                    'rung': new_rung, 'status': new_status,
                    'summary': new_summary.strip(),
                    'estimated_hh': estimated_hh,
                })
                dfs['tasks_df'] = pd.concat([tasks_df, pd.DataFrame([new_row])], ignore_index=True)
                for numeric_col in ('estimation_sp', 'rung', 'estimated_hh'):
                    if numeric_col in dfs['tasks_df'].columns:
                        dfs['tasks_df'][numeric_col] = pd.to_numeric(
                            dfs['tasks_df'][numeric_col], errors='coerce'
                        )
                if new_id_clean not in estimates_df.columns:
                    estimates_df[new_id_clean] = 0
                for role, hrs in role_hours.items():
                    if hrs > 0:
                        estimates_df.loc[estimates_df[role_col] == role, new_id_clean] = hrs
                dfs['estimates_df'] = estimates_df
                _stage('add', 'task', entity_id=new_id_clean, new_value=new_row,
                       comment='Добавлена через форму')
                st.success(f'Задача {new_id_clean} добавлена (в несохранённых правках).')
                st.rerun()

    st.markdown('---')
    st.markdown('##### ✏️ Редактирование общих полей задачи')
    st.caption('Story Points, rung, статус, summary и др. Часы по ролям редактируются ниже.')

    # Добавим estimated_hh, если его нет (для задач из Excel колонка может отсутствовать)
    if 'estimated_hh' not in tasks_df.columns:
        tasks_df['estimated_hh'] = 0
    if 'summary' not in tasks_df.columns:
        tasks_df['summary'] = ''

    display_cols = [
        c for c in [
            'task_id', 'team_id', 'Номер инициативы', 'status',
            'rung', 'estimation_sp', 'estimated_hh', 'summary',
        ] if c in tasks_df.columns
    ]
    edited = st.data_editor(
        tasks_df[display_cols], disabled=['task_id'], hide_index=True,
        use_container_width=True, num_rows='fixed', key='task_editor',
        column_config={
            'status': st.column_config.SelectboxColumn(
                'status', options=['ToDo', 'InProgress', 'Done', 'Canceled', 'NotTaken']),
            'estimation_sp': st.column_config.NumberColumn(
                'estimation_sp', min_value=1, step=1,
                help='Story Points — коммитмент команды. Влияет на SP-ёмкость.'),
            'estimated_hh': st.column_config.NumberColumn(
                'estimated_hh', min_value=0, step=5,
                help='Итоговые часы по задаче. Должно совпадать с суммой по ролям.'),
            'rung': st.column_config.NumberColumn(
                'rung', min_value=0, step=5,
                help='Приоритет: чем больше, тем раньше задача берётся в работу.'),
        },
    )
    if st.button('Применить правки в таблице задач'):
        changed = 0
        for _, new_row in edited.iterrows():
            tid = new_row['task_id']
            old_match = tasks_df.loc[tasks_df['task_id'] == tid]
            if old_match.empty:
                continue
            old_row = old_match.iloc[0]
            for col in display_cols:
                if col == 'task_id' or str(old_row[col]) == str(new_row[col]):
                    continue
                _stage('update', 'task', entity_id=tid, field=col,
                      old_value=old_row[col], new_value=new_row[col])
                tasks_df.loc[tasks_df['task_id'] == tid, col] = new_row[col]
                changed += 1
        dfs['tasks_df'] = tasks_df
        if changed:
            st.success(f'Применено изменений: {changed} (пока не сохранены).')
            st.rerun()
        else:
            st.info('Изменений не найдено.')

    st.markdown('---')
    st.markdown('##### 🧮 Часы по ролям для выбранной задачи')
    st.caption(
        'Здесь редактируются человеко-часы по каждой роли. '
        'Сумма автоматически сверяется с `estimated_hh` задачи и с SP.'
    )
    role_col = estimates_df.columns[0]
    all_roles = estimates_df[role_col].astype(str).tolist()
    task_ids_for_hours = tasks_df['task_id'].astype(str).tolist()
    selected_task_id = st.selectbox(
        'Задача:', ['—'] + task_ids_for_hours, key='task_hours_pick',
    )

    if selected_task_id != '—':
        if selected_task_id not in estimates_df.columns:
            estimates_df[selected_task_id] = 0

        # Соберём таблицу текущих часов по ролям
        hours_table = pd.DataFrame({
            'Роль': all_roles,
            'Часы (ЧЧ)': [
                pd.to_numeric(
                    estimates_df.loc[estimates_df[role_col].astype(str) == role, selected_task_id],
                    errors='coerce'
                ).fillna(0).iloc[0] if not estimates_df.loc[estimates_df[role_col].astype(str) == role].empty else 0
                for role in all_roles
            ],
        })

        edited_hours = st.data_editor(
            hours_table, hide_index=True, use_container_width=True,
            key=f'hours_editor_{selected_task_id}',
            column_config={
                'Роль': st.column_config.TextColumn('Роль', disabled=True),
                'Часы (ЧЧ)': st.column_config.NumberColumn(
                    'Часы (ЧЧ)', min_value=0, step=5,
                ),
            },
        )

        total_hours = float(edited_hours['Часы (ЧЧ)'].sum())
        task_row = tasks_df.loc[tasks_df['task_id'] == selected_task_id]
        declared_hh = float(task_row['estimated_hh'].iloc[0]) if not task_row.empty and 'estimated_hh' in task_row.columns else 0.0

        col_a, col_b = st.columns(2)
        col_a.metric('Сумма по ролям', f'{total_hours:.0f} ЧЧ')
        col_b.metric('estimated_hh задачи', f'{declared_hh:.0f} ЧЧ')

        if abs(total_hours - declared_hh) > 1:
            st.warning(
                f'⚠️ Сумма по ролям ({total_hours:.0f} ЧЧ) не совпадает с estimated_hh '
                f'({declared_hh:.0f} ЧЧ). Сохраните правки и при необходимости '
                f'поправьте estimated_hh в таблице задач выше — либо одно, либо другое.'
            )

        if st.button(f'💾 Сохранить часы по задаче {selected_task_id}'):
            changed = 0
            for _, row in edited_hours.iterrows():
                role = row['Роль']
                new_hrs = float(row['Часы (ЧЧ)'])
                mask = estimates_df[role_col].astype(str) == role
                if mask.empty:
                    continue
                old_hrs = pd.to_numeric(estimates_df.loc[mask, selected_task_id], errors='coerce').fillna(0).iloc[0]
                if abs(float(old_hrs) - new_hrs) < 1e-6:
                    continue
                estimates_df.loc[mask, selected_task_id] = new_hrs
                _stage('update', 'estimate', entity_id=selected_task_id,
                      field=str(role), old_value=float(old_hrs), new_value=new_hrs)
                changed += 1

            if changed:
                # Обновим estimated_hh задачи, чтобы не было расхождения
                tasks_df.loc[tasks_df['task_id'] == selected_task_id, 'estimated_hh'] = total_hours
                dfs['estimates_df'] = estimates_df
                dfs['tasks_df'] = tasks_df
                _stage('update', 'task', entity_id=selected_task_id, field='estimated_hh',
                      old_value=declared_hh, new_value=total_hours)
                st.success(f'Обновлено {changed} ролей. estimated_hh пересчитан на {total_hours:.0f} ЧЧ.')
                st.rerun()
            else:
                st.info('Изменений не найдено.')

    st.markdown('---')
    st.markdown('##### 🗑️ Удалить задачу')
    del_task = st.selectbox('Задача:', ['—'] + tasks_df['task_id'].astype(str).tolist(), key='del_task_pick')
    if del_task != '—':
        blocked = deps_df[(deps_df['blocking_task_id'] == del_task) | (deps_df['blocked_task_id'] == del_task)]
        if not blocked.empty:
            st.warning(
                f'У задачи {del_task} есть {len(blocked)} связей зависимостей — они будут удалены вместе с задачей '
                f'(каскадно): {", ".join(blocked["blocking_task_id"].astype(str) + "→" + blocked["blocked_task_id"].astype(str))}'
            )
        if st.button('Удалить задачу' + (' и её зависимости' if not blocked.empty else ''), type='primary'):
            dfs['tasks_df'] = tasks_df[tasks_df['task_id'] != del_task].reset_index(drop=True)
            dfs['deps_df'] = deps_df[
                (deps_df['blocking_task_id'] != del_task) & (deps_df['blocked_task_id'] != del_task)
            ].reset_index(drop=True)
            if del_task in estimates_df.columns:
                dfs['estimates_df'] = estimates_df.drop(columns=[del_task])
            _stage('delete', 'task', entity_id=del_task,
                  comment=f'Каскадно удалено {len(blocked)} связей зависимостей' if not blocked.empty else None)
            st.success(f'Задача {del_task} удалена.')
            st.rerun()


# ==================================================================
# ЗАВИСИМОСТИ (раздел 10)
# ==================================================================

def _has_cycle(deps_df: pd.DataFrame) -> list:
    """Возвращает список циклов (каждый — список task_id), если они есть."""
    g = nx.DiGraph()
    for _, row in deps_df.iterrows():
        g.add_edge(str(row['blocking_task_id']), str(row['blocked_task_id']))
    try:
        return list(nx.find_cycle(g))
    except nx.NetworkXNoCycle:
        return []


def render_dependencies() -> None:
    dfs = _dfs()
    deps_df = dfs['deps_df']
    tasks_df = dfs['tasks_df']
    task_ids = tasks_df['task_id'].astype(str).tolist()

    st.markdown('##### ➕ Добавить связь')
    with st.form('add_dep_form', clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        blocker = col1.selectbox('Блокирующая задача', task_ids, key='add_dep_blocker')
        blocked = col2.selectbox('Блокируемая задача', task_ids, key='add_dep_blocked')
        dep_type = col3.selectbox('Тип', ['depends on', 'is required for', 'has to be done before'], key='add_dep_type')
        if st.form_submit_button('Добавить связь'):
            if blocker == blocked:
                st.error('Задача не может блокировать саму себя.')
            else:
                candidate = pd.concat([deps_df, pd.DataFrame([{
                    'blocking_task_id': blocker, 'blocked_task_id': blocked, 'dependency_type': dep_type,
                }])], ignore_index=True)
                cycle = _has_cycle(candidate)
                if cycle:
                    st.error(f'Эта связь создаёт цикл зависимостей: {" → ".join(c[0] for c in cycle)} → снова к началу. Связь не добавлена.')
                else:
                    dfs['deps_df'] = candidate
                    _stage('add', 'dependency', entity_id=f'{blocker}→{blocked}',
                          new_value=f'{blocker} {dep_type} {blocked}')
                    st.success(f'Связь {blocker} → {blocked} добавлена.')
                    st.rerun()

    st.markdown('---')
    st.markdown('##### ✏️ Существующие связи')

    # Чистим пустые строки — могли попасть из Excel/парсера или быть
    # случайно добавлены пользователем в data_editor.
    deps_clean = deps_df.copy()
    for col in ('blocking_task_id', 'blocked_task_id'):
        if col in deps_clean.columns:
            deps_clean[col] = deps_clean[col].astype(str).str.strip()
    deps_clean = deps_clean[
        (~deps_clean['blocking_task_id'].isin(['', 'nan', 'None', 'null']))
        & (~deps_clean['blocked_task_id'].isin(['', 'nan', 'None', 'null']))
    ].reset_index(drop=True)

    # Если в объекте DataFrame появились лишние пустые строки — обновим рабочее состояние
    if len(deps_clean) != len(deps_df):
        dfs['deps_df'] = deps_clean
        _stage('clean', 'dependency', comment=f'Удалено пустых строк: {len(deps_df) - len(deps_clean)}')

    edited = st.data_editor(
        deps_clean, hide_index=True, use_container_width=True,
        num_rows='dynamic', key='deps_editor',
        column_config={
            'blocking_task_id': st.column_config.SelectboxColumn(
                'blocking_task_id', options=task_ids, required=True),
            'blocked_task_id': st.column_config.SelectboxColumn(
                'blocked_task_id', options=task_ids, required=True),
        },
    )
    if st.button('Применить правки в таблице связей'):
        # Отфильтруем пустые строки, которые пользователь мог добавить, но не заполнить.
        edited = edited.copy()
        for col in ('blocking_task_id', 'blocked_task_id'):
            if col in edited.columns:
                edited[col] = edited[col].astype(str).str.strip()
        edited = edited[
            (~edited['blocking_task_id'].isin(['', 'nan', 'None', 'null']))
            & (~edited['blocked_task_id'].isin(['', 'nan', 'None', 'null']))
        ].reset_index(drop=True)
        cycle = _has_cycle(edited)
        if cycle:
            st.error(f'После этих правок появляется цикл зависимостей: {" → ".join(c[0] for c in cycle)}. Правки не применены.')
        else:
            _stage('update', 'dependency', comment=f'Таблица связей отредактирована целиком ({len(edited)} строк)')
            dfs['deps_df'] = edited
            st.success('Связи обновлены.')
            st.rerun()


# ==================================================================
# СМЕТЫ (раздел 11)
# ==================================================================

def render_estimates() -> None:
    dfs = _dfs()
    estimates_df = dfs['estimates_df']
    tasks_df = dfs['tasks_df']
    role_col = estimates_df.columns[0]

    task_ids = [c for c in estimates_df.columns if c != role_col]
    selected_task = st.selectbox('Задача:', task_ids, key='estimate_task_pick')

    if selected_task:
        sub = estimates_df[[role_col, selected_task]].copy()
        sub.columns = ['Роль', 'Часы']
        edited = st.data_editor(
            sub, hide_index=True, use_container_width=True, disabled=['Роль'], key='estimate_editor',
            column_config={'Часы': st.column_config.NumberColumn('Часы', min_value=0, step=5)},
        )
        if st.button('Сохранить смету по задаче'):
            changed = 0
            for _, row in edited.iterrows():
                role, new_hrs = row['Роль'], row['Часы']
                old_hrs = estimates_df.loc[estimates_df[role_col] == role, selected_task].values[0]
                if str(old_hrs) != str(new_hrs):
                    estimates_df.loc[estimates_df[role_col] == role, selected_task] = new_hrs
                    _stage('update', 'estimate', entity_id=selected_task, field=role,
                          old_value=old_hrs, new_value=new_hrs)
                    changed += 1
            dfs['estimates_df'] = estimates_df
            if changed:
                st.success(f'Смета обновлена ({changed} строк).')
            else:
                st.info('Изменений не найдено.')

            total_hours = pd.to_numeric(edited['Часы'], errors='coerce').sum()
            task_row = tasks_df.loc[tasks_df['task_id'] == selected_task]
            if not task_row.empty and 'estimated_hh' in task_row.columns:
                declared = pd.to_numeric(task_row.iloc[0].get('estimated_hh'), errors='coerce')
                if pd.notnull(declared) and abs(declared - total_hours) > 1:
                    st.warning(
                        f'⚠️ Сумма по ролям ({total_hours:.0f} ЧЧ) не совпадает с estimated_hh '
                        f'задачи ({declared:.0f} ЧЧ) — разойдутся до тех пор, пока не поправите одно из двух.'
                    )
            if changed:
                st.rerun()


# ==================================================================
# СОХРАНЕНИЕ / ИМПОРТ / ИСТОРИЯ (раздел 12)
# ==================================================================

def render_save_panel(storage) -> None:
    user = auth.get_current_user()
    dirty = st.session_state.get('dirty', False)
    pending = _pending_count()
    committed_version = st.session_state.get('committed_version')

    if dirty:
        st.warning(f'⚠️ Есть несохранённые правки: {pending}. Изменения видны только вам, пока не нажмёте «Сохранить».')
    else:
        st.success(f'✅ Все правки сохранены. Текущая версия: {committed_version}.')

    col1, col2 = st.columns(2)
    with col1:
        comment = st.text_input('Комментарий к сохранению (необязательно):', key='save_comment')
        if st.button('💾 Сохранить', type='primary', disabled=not dirty):
            new_version = storage.save_snapshot(_dfs(), user, comment=comment or None)
            storage.log_batch(new_version, user, st.session_state.get('pending_changes', []))
            st.session_state['committed_version'] = new_version
            st.session_state['committed_dfs'] = {k: v.copy() for k, v in _dfs().items()}
            st.session_state['pending_changes'] = []
            st.session_state['dirty'] = False
            st.success(f'Сохранено как версия {new_version}.')
            st.rerun()
    with col2:
        if st.button('↩️ Отменить все правки', disabled=not dirty):
            saved = storage.load_latest()
            if saved is not None:
                dfs, version = saved
                st.session_state['working_dfs'] = dfs
                st.session_state['committed_dfs'] = {k: v.copy() for k, v in dfs.items()}
                st.session_state['committed_version'] = version
            st.session_state['pending_changes'] = []
            st.session_state['dirty'] = False
            st.success('Правки отменены, восстановлена последняя сохранённая версия.')
            st.rerun()

    st.markdown('---')
    st.markdown('##### 🕒 История версий')
    versions = storage.list_versions()
    st.dataframe(versions, use_container_width=True, hide_index=True)
    if not versions.empty:
        rollback_to = st.selectbox('Откатиться к версии:', versions['version'].tolist(), key='rollback_pick')
        if st.button('Откатить к выбранной версии', type='secondary'):
            dfs = storage.load_version(rollback_to)
            if dfs is not None:
                st.session_state['working_dfs'] = dfs
                _stage('rollback', 'snapshot', entity_id=str(rollback_to), comment=f'Откат к версии {rollback_to}')
                st.warning(f'Загружена версия {rollback_to}. Нажмите «Сохранить», чтобы закрепить откат новой версией.')
                st.rerun()

    st.markdown('---')
    st.markdown('##### 📦 Экспорт / импорт базы целиком')
    col3, col4 = st.columns(2)
    with col3:
        try:
            with open(storage.db_path, 'rb') as f:
                st.download_button('Скачать state.db', f.read(), file_name='state_backup.db')
        except FileNotFoundError:
            st.caption('База ещё не создана.')
    with col4:
        uploaded = st.file_uploader('Восстановить из файла .db', type=['db'])
        if uploaded is not None and st.button('Восстановить базу из файла'):
            with open(storage.db_path, 'wb') as f:
                f.write(uploaded.read())
            st.success('База восстановлена из файла. Перезагрузите страницу.')


# ==================================================================
# ЖУРНАЛ СОБЫТИЙ (раздел 13)
# ==================================================================

def render_event_log(storage) -> None:
    st.caption('Полный аудит: кто и когда заходил, каким разделам было отказано в доступе, что именно изменено.')

    col1, col2, col3 = st.columns(3)
    category_filter = col1.multiselect('Категория:', ['session', 'access', 'change'])
    role_filter = col2.multiselect('Роль:', list(auth.ROLES.keys()))
    entity_search = col3.text_input('Поиск по entity_id:')

    filters = {}
    if category_filter:
        filters['event_category'] = category_filter
    if role_filter:
        filters['user_role'] = role_filter
    if entity_search:
        filters['entity_id_search'] = entity_search

    events = storage.get_events(filters=filters, limit=1000)

    denied_count = int((events['event_type'] == 'access_denied').sum()) if not events.empty else 0
    changes_count = int((events['event_category'] == 'change').sum()) if not events.empty else 0
    k1, k2, k3 = st.columns(3)
    k1.metric('Всего событий', len(events))
    k2.metric('Отказов в доступе', denied_count)
    k3.metric('Изменений данных', changes_count)

    st.dataframe(events, use_container_width=True, height=450)

    if not events.empty:
        csv = events.to_csv(index=False).encode('utf-8-sig')
        st.download_button('⬇️ Скачать CSV', csv, file_name=f'event_log_{datetime.now():%Y%m%d_%H%M}.csv')


# ==================================================================
# ТОЧКА ВХОДА
# ==================================================================

def render(storage) -> None:
    """Рендерит все сабтабы управления данными внутри уже открытой вкладки."""
    sub_tabs = st.tabs(['👤 Инженеры', '🏢 Команды', '📋 Задачи', '🔗 Зависимости', '💰 Сметы', '💾 Сохранение / История'])
    with sub_tabs[0]:
        render_engineers()
    with sub_tabs[1]:
        render_teams()
    with sub_tabs[2]:
        render_tasks()
    with sub_tabs[3]:
        render_dependencies()
    with sub_tabs[4]:
        render_estimates()
    with sub_tabs[5]:
        render_save_panel(storage)
