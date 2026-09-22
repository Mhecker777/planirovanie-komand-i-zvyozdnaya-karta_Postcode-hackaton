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


def _is_blank_id(value) -> bool:
    """True, если значение пустое: NaN, None, pd.NA, '', 'nan', '<NA>' и т.п.

    Не полагаемся на .astype(str), потому что поведение pandas при
    преобразовании NaN/None/pd.NA в строку различается между версиями
    и dtype колонки. Сначала проверяем pd.isna() — это покрывает все
    числовые/объектные/special NaN-like значения. Потом — строковую
    нормализацию на случай, если значение уже пришло как текст
    'nan' / '<NA>' / 'None' (например, из Excel).
    """
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        # pd.isna() может бросить, если пришёл list / dict — считаем
        # это невалидным ID и отсеиваем.
        return True
    s = str(value).strip().lower()
    return s in {'', 'nan', 'none', 'null', 'n/a', 'na', '<na>'}


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

    # Part-time инженеры (ENG-405, ENG-406, ENG-419, ENG-426) представлены
    # в датасете двумя строками — по одной на каждую команду. При выборе
    # «ENG-405» в мультиселекте показывались два одинаковых пункта.
    # Собираем уникальные ID, но в подписи показываем все команды инженера,
    # чтобы пользователь понимал, кого именно он выбирает.
    _eng_by_id = (
        eng_df.groupby('engineer_id')['team_id']
        .apply(lambda s: ', '.join(sorted(set(s.dropna().astype(str)))))
        .to_dict()
    )
    _unique_eng_ids = sorted(_eng_by_id.keys())
    _eng_options = [f'{eid} ({_eng_by_id.get(eid, "")})' for eid in _unique_eng_ids]

    _picked_labels = st.multiselect(
        'Выбрать инженеров:', options=_eng_options, key='eng_bulk_select'
    )
    # Превращаем подписи обратно в engineer_id
    selected_ids = [lbl.split(' (', 1)[0] for lbl in _picked_labels]

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
    st.markdown('---')
    st.markdown('##### 🗑️ Удалить навсегда (без сохранения истории)')
    st.caption(
        'Полное удаление инженера из системы. Используйте осторожно: '
        'запись исчезнет из `engineers_df`, история в журнале событий '
        'останется, но Bus Factor и планировщик про него больше не узнают. '
        'Для обычного увольнения используйте «❌ Уволить» выше — тогда '
        'инженер останется в системе со статусом «Уволен».'
    )

    # Ограничим список уволенными — жёсткое удаление имеет смысл только для них.
    # Активных сначала нужно явно уволить, чтобы случайно не потерять запись.
    fired_now = eng_df[eng_df['status'] == 'Уволен']
    if fired_now.empty:
        st.info('Сначала переведите инженера в статус «Уволен» на вкладке массовых действий — после этого станет доступна кнопка удаления.')
    else:
        hard_del_id = st.selectbox(
            'Кого удалить навсегда:',
            options=['—'] + fired_now['engineer_id'].tolist(),
            key='hard_delete_eng_pick',
        )
        confirm = st.checkbox(
            'Подтверждаю: удалить без возможности восстановления',
            key='hard_delete_eng_confirm',
        )
        if hard_del_id != '—' and confirm:
            if st.button(f'🗑️ Удалить {hard_del_id} навсегда', type='primary'):
                # Убираем из engineers_df
                new_eng = eng_df[eng_df['engineer_id'] != hard_del_id].reset_index(drop=True)
                dfs['engineers_df'] = new_eng

                # Убираем из сметы (estimates_df не хранит инженеров, там роли — но
                # если вдруг что-то было связано, здесь мы это не трогаем)
                # Убираем из истории (Team_history связана с командой, не с инженером)

                _stage(
                    'hard_delete', 'engineer', entity_id=hard_del_id,
                    old_value={'engineer_id': hard_del_id, 'status': 'Уволен'},
                    new_value=None,
                    comment='Полное удаление инженера из системы',
                )
                st.success(f'Инженер {hard_del_id} удалён навсегда. Обновите страницу для пересчёта.')
                st.rerun()
def render_teams() -> None:
    dfs = _dfs()
    eng_df = dfs['engineers_df']
    tasks_df = dfs['tasks_df']
    hist_df = dfs.get('history_df')

    # ------- Справочная сводка по командам -------
    summary = eng_df.groupby('team_id').agg(
        Инженеров=('engineer_id', 'count'),
        Ролей=('role', 'nunique'),
    ).reset_index().rename(columns={'team_id': 'Команда'})
    task_counts = tasks_df.groupby('team_id')['task_id'].count().rename('Открытых задач')
    summary = summary.merge(task_counts, left_on='Команда', right_index=True, how='left').fillna({'Открытых задач': 0})

    # Добавим расчётную capacity из истории для справки
    if hist_df is not None and not hist_df.empty and 'velocity_achieved' in hist_df.columns:
        vh = hist_df.copy()
        vh['velocity_achieved'] = pd.to_numeric(vh['velocity_achieved'], errors='coerce')
        avg_vel = vh.groupby('team_id')['velocity_achieved'].mean()
        summary['SP/спринт (из истории)'] = summary['Команда'].map(
            lambda t: int(avg_vel.get(t, 0) * 0.8) if pd.notnull(avg_vel.get(t)) else 'н/д'
        )
    else:
        summary['SP/спринт (из истории)'] = 'н/д'

    st.dataframe(summary, use_container_width=True, hide_index=True)

    # ------- Ручной override SP capacity -------
    st.markdown('---')
    st.markdown('##### 🎚️ Ручной SP capacity (опционально)')
    st.caption(
        'Если состав команды сильно изменился и историческая velocity уже не '
        'отражает реальность — задайте SP на спринт вручную. Значение '
        'используется вместо расчёта из Team_history. Пустое поле — команда '
        'использует значение из истории.'
    )

    overrides_df = dfs.get('team_overrides_df')
    if overrides_df is None or not isinstance(overrides_df, pd.DataFrame):
        overrides_df = pd.DataFrame(columns=['team_id', 'sp_capacity_per_sprint', 'comment'])
        dfs['team_overrides_df'] = overrides_df

    all_teams = sorted(set(eng_df['team_id'].astype(str)) | set(tasks_df['team_id'].astype(str)))
    all_teams = [t for t in all_teams if t and t.lower() != 'nan']

    # Собираем текущее состояние: одна строка на каждую команду
    existing = {}
    if not overrides_df.empty:
        for _, row in overrides_df.iterrows():
            tid = str(row.get('team_id', '')).strip()
            if tid:
                existing[tid] = {
                    'sp_capacity_per_sprint': row.get('sp_capacity_per_sprint'),
                    'comment': row.get('comment', ''),
                }

    ui_rows = []
    for team in all_teams:
        ov = existing.get(team, {})
        ui_rows.append({
            'Команда': team,
            'SP на спринт (override)': ov.get('sp_capacity_per_sprint', None),
            'Комментарий': ov.get('comment', '') or '',
        })
    ui_df = pd.DataFrame(ui_rows)

    edited_ov = st.data_editor(
        ui_df,
        hide_index=True,
        use_container_width=True,
        key='team_overrides_editor',
        column_config={
            'Команда': st.column_config.TextColumn('Команда', disabled=True),
            'SP на спринт (override)': st.column_config.NumberColumn(
                'SP на спринт (override)',
                min_value=0, max_value=200, step=1,
                help='Оставьте пустым, чтобы использовать значение из Team_history.',
            ),
            'Комментарий': st.column_config.TextColumn(
                'Комментарий', help='Например: «наняли двух senior-разработчиков в октябре»',
            ),
        },
    )

    if st.button('✅ Применить SP capacity'):
        new_rows = []
        for _, r in edited_ov.iterrows():
            team = str(r['Команда']).strip()
            if not team:
                continue
            raw_cap = r.get('SP на спринт (override)')
            cap_val = pd.to_numeric(raw_cap, errors='coerce')
            if pd.isna(cap_val):
                # Пустое поле — override снимается, команда вернётся к истории
                continue
            new_rows.append({
                'team_id': team,
                'sp_capacity_per_sprint': int(cap_val),
                'comment': str(r.get('Комментарий', '') or ''),
            })

        old_overrides = overrides_df.copy() if overrides_df is not None else pd.DataFrame()
        dfs['team_overrides_df'] = (
            pd.DataFrame(new_rows) if new_rows
            else pd.DataFrame(columns=['team_id', 'sp_capacity_per_sprint', 'comment'])
        )

        _stage(
            'update', 'team_overrides',
            comment=f'Обновлены SP capacity overrides: {len(new_rows)} команд',
            old_value=old_overrides.to_dict('records') if not old_overrides.empty else None,
            new_value=new_rows,
        )
        st.success(f'Применено. Команд с ручным override: {len(new_rows)}.')
        st.rerun()

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
        manual_id = st.text_input(
            'task_id (можно изменить)',
            value=auto_id,
            key='add_task_id_input',
            help='Автогенерируется как NEW-NNN. Можно заменить на свой — например, если задача уже существует во внешней системе.',
        )

        col1, col2, col3 = st.columns(3)
        new_team = col1.selectbox('Команда', _known_teams(dfs), key='add_task_team')
        new_init = col2.text_input('Номер инициативы', key='add_task_init')
        new_status = col3.selectbox('Статус', ['ToDo', 'InProgress', 'Done', 'Canceled', 'NotTaken'],
                                    key='add_task_status')

        col4, col5, col6 = st.columns(3)
        new_sp = col4.number_input('Story Points (estimation_sp)', min_value=1, value=5, step=1,
                                   key='add_task_sp')
        new_rung = col5.number_input('Приоритет rung (больше = срочнее)', min_value=0, value=50, step=10,
                                     key='add_task_rung')

        # Стартовый спринт. По умолчанию — следующий после текущего,
        # чтобы новая задача не могла попасть в уже завершённый спринт.
        _cur_sprint = int(st.session_state.get('_stored_current_sprint') or 0)
        # Ограничиваем 1..6: если текущий спринт = 6, следующего уже нет,
        # ставим 6 как максимум (задача попадёт в последний спринт квартала).
        _default_start = min(6, max(1, _cur_sprint + 1))
        new_start_sprint = col6.selectbox(
            'Стартовый спринт',
            options=[1, 2, 3, 4, 5, 6],
            index=_default_start - 1,
            key='add_task_start_sprint',
            help=(
                f'Текущий спринт: {_cur_sprint} '
                f'({"старт квартала" if _cur_sprint == 0 else "завершён"}). '
                f'Задача не будет запланирована раньше выбранного спринта.'
            ),
        )

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
            new_id_clean = (manual_id or '').strip()
            if not new_id_clean:
                st.error('task_id не может быть пустым.')
            elif not new_team.strip():
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
                    'start_sprint': new_start_sprint,
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

    if 'start_sprint' not in tasks_df.columns:
        tasks_df['start_sprint'] = 1

    display_cols = [
        c for c in [
            'task_id', 'team_id', 'Номер инициативы', 'status',
            'rung', 'estimation_sp', 'estimated_hh', 'start_sprint', 'summary',
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
            'start_sprint': st.column_config.NumberColumn(
                'start_sprint', min_value=1, max_value=6, step=1,
                help='Минимальный спринт, в котором задача может быть запланирована.'),
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

    # В deps_df могут быть связи, ссылающиеся на задачи, которых нет в Tasks
    # (например, SRV-4013 → SRV-4014, когда ни той, ни другой нет в Task-секции).
    # Планировщик их игнорирует и пишет warning, но из deps_df они не удаляются.
    #
    # Streamlit SelectboxColumn не может показать значение, которого нет
    # в options — вместо него рисуется пустое поле. Чтобы «висячие» ID были
    # видны и редактируемы, добавляем их в options вместе с валидными.
    _deps_ids = set()
    for _col in ('blocking_task_id', 'blocked_task_id'):
        if _col in deps_df.columns:
            _deps_ids.update(
                deps_df[_col].dropna().astype(str).str.strip().tolist()
            )
    # Убираем пустышки и мусор
    _deps_ids = {x for x in _deps_ids if x and x.lower() not in {'nan', 'none', 'null', '<na>'}}
    task_ids = sorted(set(task_ids) | _deps_ids)

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
    # Фильтруем пустые строки через pd.isna() + строковую нормализацию.
    # Применяем маску ДО приведения к строке, чтобы NaN / None / pd.NA
    # отсеивались на уровне pandas, а не через astype(str), поведение
    # которого нестабильно между версиями.
    mask_blank = (
        deps_clean['blocking_task_id'].apply(_is_blank_id)
        | deps_clean['blocked_task_id'].apply(_is_blank_id)
    )
    deps_clean = deps_clean[~mask_blank].reset_index(drop=True)
    # Приводим рабочие значения к строке — теперь, когда мусор удалён,
    # astype(str) не испортит реальные ID.
    for col in ('blocking_task_id', 'blocked_task_id'):
        if col in deps_clean.columns:
            deps_clean[col] = deps_clean[col].astype(str).str.strip()

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
        # Тот же фильтр, что и при чтении. Используем _is_blank_id —
        # он ловит и NaN/None/pd.NA, и строковые заглушки 'nan'/'<NA>'.
        edited = edited.copy()
        mask_blank = (
            edited['blocking_task_id'].apply(_is_blank_id)
            | edited['blocked_task_id'].apply(_is_blank_id)
        )
        edited = edited[~mask_blank].reset_index(drop=True)
        for col in ('blocking_task_id', 'blocked_task_id'):
            if col in edited.columns:
                edited[col] = edited[col].astype(str).str.strip()
        cycle = _has_cycle(edited)
        if cycle:
            st.error(f'После этих правок появляется цикл зависимостей: {" → ".join(c[0] for c in cycle)}. Правки не применены.')
        else:
            _stage('update', 'dependency', comment=f'Таблица связей отредактирована целиком ({len(edited)} строк)')
            dfs['deps_df'] = edited
            st.success('Связи обновлены.')
            st.rerun()

# ==================================================================
# ПЕРЕВОДЫ (управление реорганизацией)
# ==================================================================

def render_transfers() -> None:
    """Управление переводами между командами.

    Источник истины — `st.session_state['_forbidden_donors_value']` (set
    кортежей (donor_team, role)). Он читается в app.py, передаётся в
    `SmartScheduler.run_smart_planning(forbidden_donors=...)` и сохраняется
    в runtime_state при каждом изменении. Кнопки ниже меняют этот set и
    вызывают st.rerun() — на следующем прогоне план пересчитывается, а
    runtime_state обновляется автоматически (по хешу).
    """
    ctx = st.session_state.get('_transfers_context', {})
    active_summary = ctx.get('active_summary', pd.DataFrame())
    active_detail = ctx.get('active_detail', pd.DataFrame())
    baseline_detail = ctx.get('baseline_detail', pd.DataFrame())

    forbidden = set(st.session_state.get('_forbidden_donors_value', set()))

    def _save_forbidden(new_set):
        """Обновляем рабочий set и «рабочее» значение в session_state.
        Следующий rerun увидит новое значение, пересчитает план и сохранит
        его в runtime_state (хеш изменится)."""
        st.session_state['_forbidden_donors_value'] = set(new_set)
        st.session_state['_stored_forbidden_donors'] = [list(x) for x in new_set]

    st.markdown('##### 🔄 Управление переводами')
    st.caption(
        'Планировщик при нехватке часов берёт доноров из других команд. '
        'Запреты сохраняются в state.db и переживают перезапуск.'
    )
    st.info(
        '**Важно:** запрет перевода не удаляет задачу из плана. '
        'Планировщик сначала ищет **другого** донора на эту роль. '
        'Если альтернативы нет — задача переходит в **сплит** '
        '(делает то, что может, своими силами). '
        'Задача исчезнет из плана только если её нельзя выполнить вообще: '
        'тогда она попадёт в «Не поместились в квартал» с причиной '
        '«Дефицит специалистов» или «Нет специалистов в штате».'
    )

    # ---------- Активные переводы ----------
    st.markdown('##### ✅ Активные переводы')
    if active_detail.empty:
        st.info('В текущем плане переводов между командами нет.')
    else:
        hdr = st.columns([1, 2, 2, 2, 2, 2, 1, 1])
        for col, label in zip(hdr, ['Спринт', 'Задача', 'Донор', 'Роль',
                                     'Получатель', 'Инженер', 'ЧЧ', '']):
            col.markdown(f'**{label}**')

        for idx, row in active_detail.iterrows():
            donor = str(row.get('donor_team') or '').strip()
            role = str(row.get('role') or '').strip()
            c = st.columns([1, 2, 2, 2, 2, 2, 1, 1])
            c[0].write(str(row.get('sprint') or ''))
            c[1].write(str(row.get('task_id') or ''))
            c[2].write(donor)
            c[3].write(role)
            c[4].write(str(row.get('recipient_team') or ''))
            c[5].write(str(row.get('donor_engineer') or '—'))
            c[6].write(f"{float(row.get('hours') or 0):.0f}")
            if c[7].button('🚫', key=f'forbid_{idx}_{donor}_{role}',
                           help='Запретить этот перевод'):
                new_set = set(forbidden)
                new_set.add((donor, role))
                _save_forbidden(new_set)
                st.rerun()

    st.markdown('---')

    # ---------- Запрещённые переводы ----------
    st.markdown('##### 🚫 Запрещённые переводы')
    st.caption(
        'Нажмите «↩️», чтобы разрешить перевод обратно — план пересчитается, '
        'и планировщик снова сможет использовать эту связку.'
    )
    if not forbidden:
        st.caption('Список запретов пуст.')
    else:
        # Контекст запрета — из baseline, чтобы показать задачу, инженера и ЧЧ.
        baseline_index = {}
        if not baseline_detail.empty:
            for _, r in baseline_detail.iterrows():
                key = (str(r.get('donor_team') or '').strip(),
                       str(r.get('role') or '').strip())
                if key not in baseline_index:
                    baseline_index[key] = r

        # Для каждого запрета показываем, что произошло с задачей после
        # запрета: сплитуется, вылетела из плана, или всё ещё завершается
        # (если задачу закрывает кто-то другой).
        _active_by_task = {}
        if not active_detail.empty:
            for _, r in active_detail.iterrows():
                _active_by_task.setdefault(str(r.get('task_id') or ''), []).append(r)

        hdr2 = st.columns([2, 2, 2, 2, 2, 1, 2, 1])
        for col, label in zip(hdr2, ['Донор', 'Роль', 'Куда передавали',
                                      'Задача', 'Инженер', 'ЧЧ',
                                      'Что стало с задачей', '']):
            col.markdown(f'**{label}**')

        for idx, (donor, role) in enumerate(sorted(forbidden)):
            row = baseline_index.get((donor, role))
            recipient = str(row.get('recipient_team') or '—') if row is not None else '—'
            task_id = str(row.get('task_id') or '—') if row is not None else '—'
            donor_eng = str(row.get('donor_engineer') or '—') if row is not None else '—'
            hours = float(row.get('hours') or 0) if row is not None else 0.0

            # Ищем статус задачи в текущем плане
            task_impact = '—'
            if task_id != '—':
                task_rows_in_plan = _active_by_task.get(task_id, [])
                if task_rows_in_plan:
                    # Задача в плане, но уже без этого перевода
                    task_impact = '🔁 донор заменён'
                else:
                    # Проверяем, есть ли задача в плане вообще
                    _task_in_plan = any(
                        task_id in str(item.get('task_id', ''))
                        for items in st.session_state.get('_transfers_context', {}) \
                                .get('active_detail', pd.DataFrame()).to_dict('records')
                        for item in [items]
                    )
                    # Более надёжно — свериться с c_statuses через app
                    # Здесь просто пометим как «проверьте статус задачи»
                    task_impact = '⚠️ проверьте статус'

            c = st.columns([2, 2, 2, 2, 2, 1, 2, 1])
            c[0].write(donor)
            c[1].write(role)
            c[2].write(recipient)
            c[3].write(task_id)
            c[4].write(donor_eng)
            c[5].write(f'{hours:.0f}')
            c[6].write(task_impact)
            if c[7].button('↩️', key=f'unforbid_{idx}_{donor}_{role}',
                           help='Разрешить перевод обратно — планировщик снова '
                                'сможет использовать эту связку'):
                new_set = set(forbidden)
                new_set.discard((donor, role))
                _save_forbidden(new_set)
                st.rerun()

    st.markdown('---')
    st.caption(
        f'Всего переводов в baseline: **{len(baseline_detail)}**. '
        f'Активных сейчас: **{len(active_detail)}**. '
        f'Запрещено: **{len(forbidden)}**.'
    )

    if not active_summary.empty:
        csv_active = active_summary.to_csv(index=False, sep=';').encode('utf-8-sig')
        st.download_button(
            '⬇️ Скачать активные переводы (CSV)',
            data=csv_active,
            file_name='pochtatech_transfers_active.csv',
            mime='text/csv',
            key='download_transfers_active',
        )

# ==================================================================
# ЗАДАЧИ ВНЕ ПЛАНА (принудительная постановка)
# ==================================================================

def render_forced_tasks() -> None:
    """Принудительная постановка задач вне плана.

    Пользователь может взять любую задачу и поставить её на один или
    несколько спринтов, не считаясь с SP, ЧЧ и зависимостями. SP и ЧЧ
    из пулов команды НЕ вычитаются.
    """
    ctx = st.session_state.get('_forced_context', {})
    all_task_ids = ctx.get('all_task_ids', [])
    tasks_in_plan = set(ctx.get('tasks_in_plan', []))
    task_meta = ctx.get('task_meta', {})

    forced = dict(st.session_state.get('_forced_schedule_value', {}) or {})

    def _save_forced(new_forced):
        st.session_state['_forced_schedule_value'] = dict(new_forced)

    st.markdown('##### 📌 Задачи вне плана')
    st.caption(
        'Задача появится в плане на выбранных спринтах с пометкой '
        '«Принудительно (вне плана)». SP и ЧЧ из пулов команды **не '
        'списываются** — это ручное решение поверх алгоритма. '
        'Зависимости не проверяются. Сохраняется в state.db и переживает '
        'перезапуск приложения.'
    )

    st.markdown('**➕ Поставить задачу на спринт**')
    with st.form('add_forced_form', clear_on_submit=True):
        col1, col2 = st.columns([3, 2])
        sorted_ids = sorted(all_task_ids, key=lambda x: (x in tasks_in_plan, x))
        selected = col1.selectbox(
            'Задача:',
            options=['—'] + sorted_ids,
            key='forced_add_task',
            help='Можно выбрать любую задачу. Те, что не в плане, показаны первыми.',
        )
        sprints = col2.multiselect(
            'Спринты:',
            options=[1, 2, 3, 4, 5, 6],
            default=[1],
            key='forced_add_sprints',
            help='Можно выбрать несколько — задача будет растянута на них.',
        )
        if selected != '—' and selected in task_meta:
            m = task_meta[selected]
            st.caption(
                f"**{selected}** — {m.get('summary', '') or 'без описания'}  \n"
                f"Команда: `{m.get('team', '')}` · "
                f"rung: `{m.get('rung', 0)}` · SP: `{m.get('sp', 0)}` · "
                f"статус: `{m.get('status', '')}` · "
                f"{'✅ в плане' if selected in tasks_in_plan else '⚠️ не в плане'}"
            )
        comment = st.text_input('Комментарий (необязательно):', key='forced_add_comment')
        submitted = st.form_submit_button('📌 Поставить')
        if submitted:
            if selected == '—':
                st.error('Выберите задачу.')
            elif not sprints:
                st.error('Выберите хотя бы один спринт.')
            else:
                new_forced = dict(forced)
                new_forced[selected] = {
                    'sprints': sorted(int(s) for s in sprints),
                    'comment': (comment or '').strip(),
                }
                _save_forced(new_forced)
                st.success(
                    f'Задача {selected} поставлена на спринты: '
                    f'{", ".join(map(str, sorted(sprints)))}.'
                )
                st.rerun()

    st.markdown('---')
    st.markdown('##### 🚀 Уже принудительно поставлены')
    if not forced:
        st.caption('Список пуст.')
    else:
        hdr = st.columns([2, 1, 2, 2, 3, 1])
        for col, label in zip(hdr, ['Задача', 'Спринты', 'Команда', 'SP задачи',
                                     'Комментарий', '']):
            col.markdown(f'**{label}**')

        for t_id in sorted(forced.keys()):
            entry = forced[t_id]
            sprints = entry.get('sprints', []) if isinstance(entry, dict) else []
            comment = entry.get('comment', '') if isinstance(entry, dict) else ''
            m = task_meta.get(t_id, {})
            team = m.get('team', '') or '—'
            sp = m.get('sp', 0)
            summary = m.get('summary', '') or ''

            c = st.columns([2, 1, 2, 2, 3, 1])
            c[0].markdown(f"**{t_id}**  \n<small>{summary[:60]}</small>",
                          unsafe_allow_html=True)
            c[1].write(', '.join(map(str, sprints)))
            c[2].write(team)
            c[3].write(str(sp))
            c[4].write(comment or '—')
            if c[5].button('🗑️', key=f'unforce_{t_id}',
                           help='Убрать принудительную постановку'):
                new_forced = dict(forced)
                del new_forced[t_id]
                _save_forced(new_forced)
                st.rerun()

        st.caption(
            f'Всего принудительно поставлено: **{len(forced)}**. '
            f'Их SP и ЧЧ не учитываются в утилизации команд.'
        )

    if forced:
        st.markdown('---')
        if st.button('🗑️ Снять все принудительные задачи', type='secondary'):
            _save_forced({})
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
    sub_tabs = st.tabs([
        '👤 Инженеры',
        '🏢 Команды',
        '📋 Задачи',
        '🔗 Зависимости',
        '🔄 Переводы',
        '📌 Вне плана',
        '💰 Сметы',
        '💾 Сохранение / История',
    ])
    with sub_tabs[0]:
        render_engineers()
    with sub_tabs[1]:
        render_teams()
    with sub_tabs[2]:
        render_tasks()
    with sub_tabs[3]:
        render_dependencies()
    with sub_tabs[4]:
        render_transfers()
    with sub_tabs[5]:
        render_forced_tasks()
    with sub_tabs[6]:
        render_estimates()
    with sub_tabs[7]:
        render_save_panel(storage)
