import math
import os
from collections import defaultdict

import networkx as nx
import numpy as np
import pandas as pd

from parser import DataParser


LOG_COLUMNS = [
    'sprint', 'task_id', 'team', 'initiative', 'action', 'reason', 'reason_code',
    'donor_team', 'recipient_team', 'role', 'hours'
]


def normalize_role_name(role) -> str:
    """Нормализует роль; пропуски возвращаются как пустая строка."""
    if role is None or pd.isna(role):
        return ''
    r = str(role).strip()
    if not r or r.lower() in {'nan', 'none', 'null'}:
        return ''
    r_lower = r.lower()
    if 'итого' in r_lower:
        return 'ИТОГО'
    if 'девопс' in r_lower:
        return 'Девопс'
    if 'ios' in r_lower:
        return 'Разработчик iOS'
    if 'bigdata' in r_lower or 'big data' in r_lower:
        if 'руководитель' in r_lower:
            return 'Руководитель BigData'
        if 'аналитик' in r_lower:
            return 'Аналитик Big Data'
        if 'разработчик' in r_lower:
            return 'Разработчик Big Data'
    return r


def _clean_id(value):
    if value is None or pd.isna(value):
        return ''
    value = str(value).strip()
    return '' if value.lower() in {'nan', 'none', 'null'} else value


class SmartScheduler:
    SPRINTS = range(1, 7)

    def __init__(self, data_path: str):
        self.data_path = data_path
        self.parser = DataParser(data_path)
        self.raw_tasks = self.parser.get_tasks()
        self.estimates_df = self.parser.get_estimates()
        self.deps_df = self.parser.get_dependencies()
        self.engineers_df = self.parser.get_engineers()
        self.history_df = self.parser.get_team_history()
        self._prepare_data()

    def _warn(self, message: str):
        if message not in self.data_quality_warnings:
            self.data_quality_warnings.append(message)
    def _engineer_has_skill(self, eng_id: str, role: str) -> bool:
        """Проверяет, что у инженера есть хотя бы один ключевой навык для роли."""
        required = self.role_required_skills.get(role)
        if not required:
            return True  # если роль не описана — не блокируем
        eng_skills = self.engineer_skills.get(eng_id, set())
        return bool(required & eng_skills)

    def _prepare_data(self):
        """Очистка, валидация и нормализация входных данных."""
        self.data_quality_warnings = []
        self.invalid_task_ids = set()
        self.missing_task_team_ids = set()
        self.bad_estimate_task_ids = set()

        required = {'task_id', 'team_id', 'status', 'rung', 'estimation_sp', 'Номер инициативы'}
        missing = sorted(required - set(self.raw_tasks.columns))
        if missing:
            raise ValueError(f"В Tasks отсутствуют обязательные колонки: {', '.join(missing)}")

        # 1. ЗАДАЧИ
        self.tasks = self.raw_tasks.copy()
        self.tasks['task_id'] = self.tasks['task_id'].map(_clean_id)
        self.tasks = self.tasks[self.tasks['task_id'] != ''].copy()

        duplicate_mask = self.tasks['task_id'].duplicated(keep=False)
        for task_id in sorted(self.tasks.loc[duplicate_mask, 'task_id'].unique()):
            self.invalid_task_ids.add(task_id)
            self._warn(f"{task_id}: task_id встречается более одного раза; задача исключена из автоматического планирования до исправления исходных данных.")

        self.tasks['team_id'] = self.tasks['team_id'].map(_clean_id)
        for task_id in self.tasks.loc[self.tasks['team_id'] == '', 'task_id']:
            self.missing_task_team_ids.add(task_id)
            self._warn(f"{task_id}: не указана команда team_id; задача не может быть автоматически запланирована.")

        self.tasks['status'] = self.tasks['status'].map(lambda x: '' if x is None or pd.isna(x) else str(x).strip())
        self.tasks['rung'] = pd.to_numeric(self.tasks['rung'], errors='coerce').fillna(0)

        sp_raw = pd.to_numeric(self.tasks['estimation_sp'], errors='coerce')
        bad_sp = sp_raw.isna() | (sp_raw <= 0)
        for task_id in self.tasks.loc[bad_sp, 'task_id']:
            self._warn(f"{task_id}: некорректный estimation_sp; для планирования использовано 1 SP.")
        self.tasks['estimation_sp'] = sp_raw.fillna(1).astype(int)
        self.tasks.loc[self.tasks['estimation_sp'] <= 0, 'estimation_sp'] = 1

        if 'Номер инициативы' in self.tasks.columns:
            self.tasks['Номер инициативы'] = self.tasks['Номер инициативы'].map(
                lambda x: 'Без инициативы' if x is None or pd.isna(x) or not str(x).strip() else str(x).strip()
            )

        # 2. ГРАФ ЗАВИСИМОСТЕЙ
        self.G = nx.DiGraph()
        valid_ids = set(self.tasks['task_id']) - self.invalid_task_ids
        self.G.add_nodes_from(valid_ids)
        seen_dependencies = set()
        for _, row in self.deps_df.iterrows():
            b_from = _clean_id(row.get('blocking_task_id'))
            b_to = _clean_id(row.get('blocked_task_id'))
            if not b_from or not b_to:
                continue
            if b_from not in valid_ids or b_to not in valid_ids:
                self._warn(f"Зависимость {b_from} → {b_to}: одна или обе задачи отсутствуют в Tasks.")
                continue
            edge = (b_from, b_to)
            if edge in seen_dependencies:
                self._warn(f"Дублирующаяся зависимость {b_from} → {b_to}.")
                continue
            seen_dependencies.add(edge)
            self.G.add_edge(b_from, b_to)

        cycles = list(nx.simple_cycles(self.G))
        if cycles:
            for cycle in cycles[:10]:
                self._warn(f"Цикл зависимостей: {' → '.join(cycle)}. Задачи цикла не будут автоматически планироваться.")
            self.invalid_cycle_task_ids = set(x for cycle in cycles for x in cycle)
        else:
            self.invalid_cycle_task_ids = set()

        # 3. ИСТОРИЯ / SP CAPACITY
        self.history_df = self.history_df.copy()
        self.history_df['team_id'] = self.history_df['team_id'].map(_clean_id)
        self.history_df['velocity_achieved'] = pd.to_numeric(self.history_df['velocity_achieved'], errors='coerce')
        self.history_df['planned_sp'] = pd.to_numeric(self.history_df.get('planned_sp'), errors='coerce') if 'planned_sp' in self.history_df.columns else np.nan

        invalid_velocity = self.history_df['velocity_achieved'].isna()
        for _, row in self.history_df.loc[invalid_velocity].iterrows():
            self._warn(f"История {row.get('team_id') or 'без команды'}: velocity_achieved отсутствует или нечисловой; строка не участвует в расчёте SP capacity.")
        negative_velocity = self.history_df['velocity_achieved'] < 0
        for _, row in self.history_df.loc[negative_velocity.fillna(False)].iterrows():
            self._warn(f"История {row.get('team_id') or 'без команды'}: отрицательный velocity_achieved; строка не участвует в расчёте SP capacity.")

        valid_hist = self.history_df[
            (self.history_df['team_id'] != '')
            & self.history_df['velocity_achieved'].notna()
            & (self.history_df['velocity_achieved'] >= 0)
        ].copy()
        avg_vel = valid_hist.groupby('team_id')['velocity_achieved'].mean()
        self.team_sp_capacity = {team: max(0, int(math.floor(v * 0.8))) for team, v in avg_vel.items()}

        # 4. ИНЖЕНЕРЫ
        self.engineers_df = self.engineers_df.copy()
        self.engineers_df['engineer_id'] = self.engineers_df['engineer_id'].map(_clean_id)
        self.engineers_df['team_id'] = self.engineers_df['team_id'].map(_clean_id)
        self.engineers_df['role_norm'] = self.engineers_df['role'].apply(normalize_role_name)
        self.engineers_df['capacity_rate'] = pd.to_numeric(self.engineers_df['capacity_rate'], errors='coerce')

        bad_cap = self.engineers_df['capacity_rate'].isna() | (self.engineers_df['capacity_rate'] < 0)
        for _, row in self.engineers_df.loc[bad_cap].iterrows():
            self._warn(f"Инженер {row.get('engineer_id') or '?'}: некорректный capacity_rate; использовано 0.")
        self.engineers_df.loc[bad_cap, 'capacity_rate'] = 0.0

        for _, row in self.engineers_df.loc[self.engineers_df['capacity_rate'] > 1].iterrows():
            self._warn(f"Инженер {row.get('engineer_id') or '?'}: capacity_rate={row['capacity_rate']:.2f} больше 1; значение сохранено как задано.")

        bad_eng = (self.engineers_df['team_id'] == '') | (self.engineers_df['role_norm'] == '')
        for _, row in self.engineers_df.loc[bad_eng].iterrows():
            self._warn(f"Инженер {row.get('engineer_id') or '?'}: отсутствует команда или роль; строка не участвует в фонде часов.")

        self.engineers_df['hours_per_sprint'] = self.engineers_df['capacity_rate'] * 80.0
        valid_eng = self.engineers_df[(self.engineers_df['team_id'] != '') & (self.engineers_df['role_norm'] != '')].copy()
        self.known_roles = set(valid_eng['role_norm']) - {''}
        self.team_role_hours = {}
        for _, row in valid_eng.iterrows():
            team = row['team_id']
            role = row['role_norm']
            hrs = float(row['hours_per_sprint'])
            if hrs <= 0:
                continue
            self.team_role_hours.setdefault(team, {})[role] = self.team_role_hours.setdefault(team, {}).get(role, 0.0) + hrs
        # 5.1. МАППИНГ РОЛЬ -> КЛЮЧЕВЫЕ НАВЫКИ
        # Используется для проверки технологического соответствия.
        # Ключ — нормализованное имя роли, значение — множество ключевых слов
        # (в нижнем регистре), которые должны встречаться в skills_declared.
        self.role_required_skills = {
            'Разработчик Java': {'java'},
            'Разработчик Java Script': {'javascript', 'react', 'vue', 'angular', 'typescript'},
            'Разработчик Android': {'android', 'kotlin'},
            'Разработчик iOS': {'ios', 'swift'},
            'Разработчик .net': {'.net', 'c#'},
            'Разработчик Big Data': {'big data', 'python', 'pyspark', 'spark', 'hadoop', 'scala'},
            'Разработчик BigData': {'big data', 'python', 'pyspark', 'spark', 'hadoop', 'scala'},
            'Аналитик Big Data': {'big data', 'python', 'sql', 'pandas', 'tableau'},
            'Аналитик': {'sql', 'rest api', 'bpmn', 'uml', 'json', 'xml'},
            'Аналитик 1С': {'1с', 'sql'},
            'Тестировщик': {'тестирование', 'rest api', 'sql', 'postman', 'devtools'},
            'Тестировщик (Автотестирование)': {'автотест', 'selenium', 'playwright', 'pytest', 'java', 'junit'},
            'Архитектор решений': {'архитектура', 'togaf', 'микросервисы', 'rest', 'grpc'},
            'Архитектор 1С': {'1с'},
            'ДевОпс': {'docker', 'kubernetes', 'ci/cd', 'linux', 'ansible', 'terraform'},
            'Дизайнер': {'figma', 'ux', 'ui', 'adobe'},
            'Руководитель проекта': {'agile', 'scrum', 'управление', 'bpmn'},
            'Руководитель BigData': {'big data', 'управление', 'agile'},
            'Специалист поддержки': {'поддержка', 'sql', 'rest api'},
            'Специалист поддержки 1С': {'1с', 'поддержка'},
            'Специалист поддержки BigData': {'big data', 'поддержка'},
            'Разработчик 1С': {'1с'},
        }

        # 5.2. НАВЫКИ ИНЖЕНЕРОВ (нижний регистр)
        self.engineer_skills = {}
        for _, row in self.engineers_df.iterrows():
            eng_id = row['engineer_id']
            if not eng_id:
                continue
            skills_raw = str(row.get('skills_declared', ''))
            skills = {s.strip().lower() for s in skills_raw.split(',') if s.strip()}
            self.engineer_skills[eng_id] = skills

        # 5.3. ИНЖЕНЕРЫ ПО КОМАНДАМ И РОЛЯМ
        self.team_role_engineers = {}
        for _, row in valid_eng.iterrows():
            team = row['team_id']
            role = row['role_norm']
            eng_id = row['engineer_id']
            if not team or not role or not eng_id:
                continue
            self.team_role_engineers.setdefault(team, {}).setdefault(role, set()).add(eng_id)
        # 5. СМЕТА
        self.task_estimates = defaultdict(dict)
        if self.estimates_df.empty:
            self._warn('Секция сметы пуста: ни одна задача не имеет постатейной оценки.')
        else:
            # В Excel строки = роли, столбцы = task_id. Первая колонка содержит роль.
            first_col = self.estimates_df.columns[0]
            est = self.estimates_df.copy()
            est[first_col] = est[first_col].map(_clean_id)
            valid_ids = set(self.tasks['task_id']) - self.invalid_task_ids
            for task_id in est.columns[1:]:
                clean_id = _clean_id(task_id)
                if not clean_id or clean_id not in valid_ids:
                    if clean_id:
                        self._warn(f"Смета содержит задачу {clean_id}, которой нет среди валидных task_id.")
                    continue
                for _, source_row in est.iterrows():
                    role_name = source_row[first_col]
                    norm_r = normalize_role_name(role_name)
                    if norm_r in {'', 'ИТОГО'}:
                        continue
                    val = pd.to_numeric(source_row[task_id], errors='coerce')
                    if pd.isna(val) or val == 0:
                        continue
                    if val < 0:
                        self._warn(f"{clean_id}: отрицательная оценка {val:g} ЧЧ для роли «{norm_r or role_name}» проигнорирована.")
                        continue
                    if norm_r not in self.known_roles:
                        self._warn(f"{clean_id}: роль «{role_name}» отсутствует среди сотрудников; её часы не участвуют в автоматическом планировании.")
                        continue
                    self.task_estimates[clean_id][norm_r] = self.task_estimates[clean_id].get(norm_r, 0.0) + float(val)

        # 6. ЦЕЛОСТНОСТЬ ОЦЕНОК
        for _, row in self.tasks.iterrows():
            t_id = row['task_id']
            role_sum = sum(self.task_estimates.get(t_id, {}).values())
            declared_total = pd.to_numeric(row.get('estimated_hh'), errors='coerce') if 'estimated_hh' in self.tasks.columns else np.nan
            if pd.isna(declared_total):
                self._warn(f"{t_id}: estimated_hh отсутствует или нечисловой.")
            elif declared_total < 0:
                self._warn(f"{t_id}: estimated_hh отрицательный ({declared_total:g} ЧЧ).")
            elif abs(float(declared_total) - role_sum) > 1:
                self._warn(
                    f"{t_id}: итоговая оценка в Tasks (estimated_hh = {declared_total:.0f} ЧЧ) "
                    f"не совпадает с суммой по ролям в смете ({role_sum:.0f} ЧЧ), "
                    f"расхождение {abs(float(declared_total) - role_sum):.0f} ЧЧ"
                )
            if not self.task_estimates.get(t_id):
                self.bad_estimate_task_ids.add(t_id)
                self._warn(f"{t_id}: нет ни одной положительной оценки по роли, доступной в штате.")

    def get_data_quality_warnings(self) -> list:
        return list(self.data_quality_warnings)

    def get_team_reliability(self) -> pd.DataFrame:
        hist = self.history_df.copy()
        if 'planned_sp' not in hist.columns:
            return pd.DataFrame(columns=['Команда', 'Примечание']).assign(Примечание=['Поле planned_sp не найдено в Team_history'])
        hist['own_say_do'] = np.where(
            hist['planned_sp'].notna() & (hist['planned_sp'] > 0) & hist['velocity_achieved'].notna(),
            hist['velocity_achieved'] / hist['planned_sp'] * 100,
            np.nan,
        )
        records = []
        for team, g in hist[hist['team_id'] != ''].groupby('team_id'):
            avg_v, std_v = g['velocity_achieved'].mean(), g['velocity_achieved'].std()
            avg_sd, std_sd = g['own_say_do'].mean(), g['own_say_do'].std()
            stable = pd.notnull(std_sd) and std_sd < 15
            records.append({
                'Команда': team,
                'Ср. Velocity': round(avg_v, 1) if pd.notnull(avg_v) else None,
                'Разброс Velocity (σ)': round(std_v, 1) if pd.notnull(std_v) else None,
                'Ср. исполнение своего плана': f"{avg_sd:.0f}%" if pd.notnull(avg_sd) else 'н/д',
                'Разброс исполнения (σ)': f"{std_sd:.0f}%" if pd.notnull(std_sd) else 'н/д',
                'Оценка стабильности': ('Стабильна' if stable else 'Нестабильна') if pd.notnull(std_sd) else 'н/д',
                'Спринтов в истории': len(g),
            })
        return pd.DataFrame(records).sort_values('Команда') if records else pd.DataFrame()

    def get_transfer_summary(self, logs_df: pd.DataFrame) -> pd.DataFrame:
        columns = ['Команда-донор', 'Роль', 'Команда-получатель', 'Часов передано']
        if logs_df is None or logs_df.empty or 'action' not in logs_df.columns:
            return pd.DataFrame(columns=columns)
        transfers = logs_df[logs_df['action'] == 'Реорганизация (Перевод)']
        if transfers.empty:
            return pd.DataFrame(columns=columns)
        summary = transfers.groupby(['donor_team', 'role', 'recipient_team'], dropna=False)['hours'].sum().reset_index()
        summary.columns = columns
        return summary.sort_values('Часов передано', ascending=False)

    def run_smart_planning(self, fact_sprint_done=None, current_time_sprint=0, baseline_schedule=None, forbidden_donors=None):
        fact_sprint_done = fact_sprint_done or {}
        forbidden_donors = forbidden_donors or set()
        current_time_sprint = max(0, min(6, int(current_time_sprint)))

        task_status = {}
        unknown_status_ids = set()
        done_words = {'done', 'completed', 'завершена', 'завершено', 'готово', 'выполнена', 'выполнено'}
        progress_words = {'inprogress', 'in progress', 'in_progress', 'в работе', 'выполняется', 'начата', 'начато'}
        planned_words = {'planned', 'план', 'запланирована', 'запланировано', 'новая', ''}
        canceled_words = {
            'отменено заказ', 'отменена заказчиком', 'отменено заказчиком',
            'отменено', 'cancelled', 'canceled',
        }
        not_taken_words = {
            'не будет взято в квартал', 'не берем в квартал',
            'не берём в квартал', 'не будет взято',
        }
        for _, row in self.tasks.iterrows():
            raw = row['status'].lower().strip()
            if raw in done_words:
                status = 'Done'
            elif raw in progress_words:
                status = 'InProgress'
            elif raw in canceled_words:
                status = 'Canceled'
            elif raw in not_taken_words:
                status = 'NotTaken'
            elif raw in planned_words:
                status = 'Planned'
            else:
                status = 'Planned'
                unknown_status_ids.add(row['task_id'])
                self._warn(
                    f"{row['task_id']}: неизвестный статус «{row['status']}»; для планирования трактуется как Planned.")
            task_status[row['task_id']] = status

        for t_id in self.invalid_task_ids | self.invalid_cycle_task_ids:
            task_status[t_id] = 'Invalid'

        remaining_hours = {t: dict(self.task_estimates.get(t, {})) for t in task_status}
        sprint_schedule = {s: [] for s in self.SPRINTS}
        explain_logs = []
        alerts = []
        sprint_when_done = {}
        reserved_sp = set()
        total_burned_hours = defaultdict(dict)
        sprint_deficits = {s: defaultdict(float) for s in self.SPRINTS}
        execution_sources = defaultdict(lambda: defaultdict(list))

        init_sizes = self.tasks.groupby('Номер инициативы')['estimation_sp'].sum().to_dict()
        self.tasks['init_size'] = self.tasks['Номер инициативы'].map(init_sizes).fillna(0)

        task_rows = {row['task_id']: row for _, row in self.tasks.iterrows() if row['task_id'] not in self.invalid_task_ids}
        def topo_key(node):
            row = task_rows.get(node)
            if row is None:
                return (9, 0, 0, str(node))
            active = 0 if task_status.get(node) == 'InProgress' else 1
            return (active, float(row['init_size']), -float(row['rung']), str(node))

        try:
            topo_order = list(nx.lexicographical_topological_sort(self.G, key=topo_key))
        except nx.NetworkXUnfeasible:
            topo_order = sorted(self.G.nodes, key=topo_key)

        scored_tasks = [task_rows[n] for n in topo_order if n in task_rows]

        def add_log(sprint, row, action, reason, reason_code, **extra):
            record = {
                'sprint': sprint, 'task_id': row['task_id'], 'team': row.get('team_id', ''),
                'initiative': row.get('Номер инициативы', 'Без инициативы'), 'action': action,
                'reason': reason, 'reason_code': reason_code,
                'donor_team': None, 'recipient_team': None, 'role': None, 'hours': 0.0,
            }
            record.update(extra)
            explain_logs.append(record)
        # Единоразово логируем отменённые и не взятые в квартал задачи.
        for _, row in self.tasks.iterrows():
            t_id = row['task_id']
            if task_status.get(t_id) == 'Canceled':
                add_log(1, row, 'Отменена', 'Задача отменена заказчиком.', 'canceled')
            elif task_status.get(t_id) == 'NotTaken':
                add_log(1, row, 'Не взята в квартал', 'Задача не будет взята в текущий квартал.', 'not_taken')

        for sprint in self.SPRINTS:
            sp_pool = {k: max(0, float(v)) for k, v in self.team_sp_capacity.items()}
            team_hours_pool = {t: dict(roles) for t, roles in self.team_role_hours.items()}

            # ФАКТ прошлых спринтов: принимаем как истину, но фиксируем перерасход.
            if sprint <= current_time_sprint:
                # 1. Фактически завершённые задачи — принимаем как истину.
                for row in scored_tasks:
                    t_id = row['task_id']
                    if fact_sprint_done.get(t_id) != sprint:
                        continue
                    team = row['team_id']
                    fact_sp = float(row['estimation_sp'])
                    if t_id not in reserved_sp:
                        if sp_pool.get(team, 0) < fact_sp:
                            alerts.append({'type': '🔴 ОШИБКА ФАКТА', 'task_id': t_id, 'initiative': row['Номер инициативы'],
                                           'description': f"Факт по {t_id}: {fact_sp:g} SP при доступном остатке {sp_pool.get(team, 0):g} SP."})
                        sp_pool[team] = max(0.0, sp_pool.get(team, 0.0) - fact_sp)
                        reserved_sp.add(t_id)
                    orig_hours = self.task_estimates.get(t_id, {})
                    burned = 0.0
                    for role, hrs in orig_hours.items():
                        hrs = float(hrs)
                        available = team_hours_pool.get(team, {}).get(role, 0.0)
                        if available < hrs:
                            alerts.append({'type': '🔴 ОШИБКА ФАКТА', 'task_id': t_id, 'initiative': row['Номер инициативы'],
                                           'description': f"Факт по {t_id}: роли «{role}» нужно {hrs:g} ЧЧ, доступно {max(0, available):g} ЧЧ."})
                        team_hours_pool.setdefault(team, {})[role] = max(0.0, available - hrs)
                        total_burned_hours[team][role] = total_burned_hours[team].get(role, 0.0) + hrs
                        remaining_hours.setdefault(t_id, {})[role] = 0.0
                        burned += hrs
                    task_status[t_id] = 'Done'
                    sprint_when_done[t_id] = sprint
                    sprint_schedule[sprint].append({'task_id': t_id, 'initiative': row['Номер инициативы'], 'team': team,
                                                    'sp': fact_sp, 'task_sp': fact_sp, 'status': 'Завершена (Факт)',
                                                    'burned_hh': burned, 'summary': row.get('summary', ''), 'rung': row.get('rung', 0)})

                # 2. Задачи из baseline на этот спринт, которые НЕ закрыты в факте.
                if baseline_schedule is not None:
                    fact_done_ids = {t for t, s in fact_sprint_done.items() if s == sprint}
                    seen_in_schedule = set()
                    for item in baseline_schedule.get(sprint, []):
                        tid = item['task_id']
                        if tid in fact_done_ids or tid in seen_in_schedule:
                            continue
                        if tid not in task_rows:
                            continue
                        if task_status.get(tid) == 'Done':
                            continue
                        row = task_rows[tid]
                        seen_in_schedule.add(tid)
                        sprint_schedule[sprint].append({
                            'task_id': tid,
                            'initiative': row['Номер инициативы'],
                            'team': row['team_id'],
                            'sp': 0.0,
                            'task_sp': float(row['estimation_sp']),
                            'status': 'Не выполнена (Факт)',
                            'burned_hh': 0.0,
                            'summary': row.get('summary', ''),
                            'rung': row.get('rung', 0),
                        })
                continue

            for row in scored_tasks:
                t_id = row['task_id']
                team = row['team_id']
                initiative = row['Номер инициативы']
                sp_needed = float(row['estimation_sp'])

                if task_status.get(t_id) in {'Done', 'Canceled', 'NotTaken'}:
                    continue
                if t_id in self.invalid_task_ids:
                    continue
                if t_id in self.invalid_cycle_task_ids:
                    add_log(sprint, row, 'Перенос', 'Циклическая зависимость; задача исключена из автоматического планирования.', 'dependency_cycle')
                    continue
                if not team:
                    add_log(sprint, row, 'Перенос', 'Не указана команда задачи.', 'bad_team')
                    continue

                preds = list(self.G.predecessors(t_id))
                blocking = [p for p in preds if task_status.get(p) != 'Done' or sprint_when_done.get(p, 0) >= sprint]
                if blocking:
                    add_log(sprint, row, 'Перенос', f"Блокируется: {', '.join(blocking)}", 'dependency')
                    continue

                if t_id in self.bad_estimate_task_ids or not remaining_hours.get(t_id):
                    add_log(sprint, row, 'Перенос', 'Нет полной постатейной оценки по доступным ролям; задача не планируется.', 'bad_estimate')
                    continue

                sp_to_commit = 0.0 if t_id in reserved_sp else sp_needed
                if sp_pool.get(team, 0) < sp_to_commit:
                    add_log(sprint, row, 'Перенос', f"Превышен лимит SP ({sp_pool.get(team, 0):g} < {sp_to_commit:g})", 'sp_capacity')
                    continue

                req_hours = remaining_hours[t_id]
                temp = {t: dict(roles) for t, roles in team_hours_pool.items()}
                transfers = []
                deficit_roles = []

                for role, needed in list(req_hours.items()):
                    needed = max(0.0, float(needed))
                    if needed <= 0:
                        continue
                    own = temp.get(team, {}).get(role, 0.0)
                    deficit = max(0.0, needed - own)
                    if deficit <= 0:
                        continue
                    donors = []
                    donors = []
                    for donor_team, donor_roles in temp.items():
                        if donor_team == team or (donor_team, role) in forbidden_donors:
                            continue
                        donor_avail = max(0.0, float(donor_roles.get(role, 0.0)))
                        if donor_avail > 0:
                            # Технологическое соответствие: в команде-доноре
                            # должен быть хотя бы один инженер с нужным навыком.
                            donor_engineers = self.team_role_engineers.get(donor_team, {}).get(role, set())
                            has_skill = any(self._engineer_has_skill(e, role) for e in donor_engineers)
                            if not has_skill:
                                continue
                            donors.append((donor_avail, donor_team))
                    for donor_avail, donor_team in sorted(donors, key=lambda x: (-x[0], x[1])):
                        if deficit <= 0:
                            break
                        amount = min(deficit, donor_avail)
                        temp[donor_team][role] -= amount
                        temp.setdefault(team, {})[role] = temp.get(team, {}).get(role, 0.0) + amount
                        deficit -= amount
                        transfers.append({'sprint': sprint, 'task_id': t_id, 'team': team, 'initiative': initiative,
                                          'action': 'Реорганизация (Перевод)',
                                          'reason': f"Перевод «{role}» из {donor_team} ({amount:g} ЧЧ)",
                                          'reason_code': 'transfer', 'donor_team': donor_team,
                                          'recipient_team': team, 'role': role, 'hours': amount})
                    if deficit > 1e-9:
                        deficit_roles.append(role)
                        sprint_deficits[sprint][role] += deficit

                # Частичный Split: если хоть какие-то часы доступны после переводов,
                # выполняем доступный объём и переносим остаток в следующий спринт.
                if deficit_roles:
                    # При полном отсутствии ресурса по всем ролям задача не стартует.
                    total_available = 0.0
                    for role, needed in req_hours.items():
                        available = temp.get(team, {}).get(role, 0.0)
                        total_available += min(max(0.0, float(needed)), max(0.0, available))
                    if total_available <= 1e-9:
                        add_log(sprint, row, 'Перенос', f"Глобальный дефицит специалистов: {', '.join(deficit_roles)}", 'role_capacity')
                        continue

                team_hours_pool = temp
                explain_logs.extend(transfers)
                for tr in transfers:
                    execution_sources[t_id][tr['role']].append({'team': tr['donor_team'], 'hours': tr['hours']})

                if t_id not in reserved_sp:
                    sp_pool[team] = max(0.0, sp_pool.get(team, 0.0) - sp_needed)
                    reserved_sp.add(t_id)
                    committed_sp = sp_needed
                else:
                    committed_sp = 0.0

                burned_summary = 0.0
                fully_finished = True
                for role, needed in list(req_hours.items()):
                    needed = max(0.0, float(needed))
                    if needed <= 0:
                        continue
                    available = max(0.0, temp.get(team, {}).get(role, 0.0))
                    burn = min(needed, available)
                    temp[team][role] = available - burn
                    req_hours[role] = max(0.0, needed - burn)
                    burned_summary += burn

                    # Сначала атрибутируем burn донору, затем остаток — своей команде.
                    left = burn
                    for source in execution_sources[t_id].get(role, []):
                        source_hours = min(float(source['hours']), left)
                        if source_hours <= 0:
                            continue
                        donor = source['team']
                        total_burned_hours[donor][role] = total_burned_hours[donor].get(role, 0.0) + source_hours
                        source['hours'] -= source_hours
                        left -= source_hours
                        if left <= 1e-9:
                            break
                    if left > 0:
                        total_burned_hours[team][role] = total_burned_hours[team].get(role, 0.0) + left
                    if req_hours[role] > 1e-9:
                        fully_finished = False

                # Неиспользованный перенос возвращаем донору.
                for role, entries in execution_sources.pop(t_id, {}).items():
                    for source in entries:
                        unused = max(0.0, float(source.get('hours', 0.0)))
                        if unused > 0:
                            donor = source['team']
                            team_hours_pool.setdefault(donor, {})[role] = team_hours_pool.get(donor, {}).get(role, 0.0) + unused

                if fully_finished:
                    task_status[t_id] = 'Done'
                    sprint_when_done[t_id] = sprint
                    status_text = 'Завершена'
                    reason_text = f"Выполнена (списано {burned_summary:.1f} ЧЧ)"
                    reason_code = 'completed'
                else:
                    task_status[t_id] = 'InProgress'
                    status_text = 'Растянута (Сплиттинг)'
                    reason_text = f"Частично выполнена ({burned_summary:.1f} ЧЧ), остаток перенесён"
                    reason_code = 'split'

                sprint_schedule[sprint].append({'task_id': t_id, 'initiative': initiative, 'team': team,
                                                'sp': committed_sp, 'task_sp': sp_needed, 'status': status_text,
                                                'burned_hh': burned_summary, 'summary': row.get('summary', ''),
                                                'rung': row.get('rung', 0)})
                add_log(sprint, row, 'Включена в план', reason_text, reason_code)
        # Аудит технологического соответствия: если для роли нет ни одного
        # инженера с ключевым навыком во всей компании — задача невыполнима
        # без найма или обучения.
        skill_deficit_alerts = set()
        for _, row in self.tasks.iterrows():
            t_id = row['task_id']
            if task_status.get(t_id) == 'Done':
                continue
            initiative = row['Номер инициативы']
            req_hours = self.task_estimates.get(t_id, {})
            for role, needed in req_hours.items():
                if needed <= 0:
                    continue
                all_engineers_with_role = set()
                for team_roles in self.team_role_engineers.values():
                    all_engineers_with_role.update(team_roles.get(role, set()))
                has_any = any(self._engineer_has_skill(e, role) for e in all_engineers_with_role)
                if not has_any:
                    key = (t_id, role)
                    if key not in skill_deficit_alerts:
                        skill_deficit_alerts.add(key)
                        alerts.append({
                            'type': '🟤 СТРУКТУРНЫЙ ДЕФИЦИТ (НАВЫКИ)',
                            'task_id': t_id,
                            'initiative': initiative,
                            'description': (
                                f"Для роли «{role}» ни у одного инженера в компании "
                                f"нет заявленных ключевых навыков. Задача не может быть "
                                f"выполнена без найма или обучения."
                            )
                        })
        # Финальный аудит SP.
        capacity_violations = []
        for sprint in self.SPRINTS:
            used = defaultdict(float)
            for item in sprint_schedule[sprint]:
                used[item['team']] += float(item.get('sp', 0) or 0)
            for team, used_sp in used.items():
                cap = float(self.team_sp_capacity.get(team, 0))
                if used_sp > cap + 1e-9:
                    capacity_violations.append((sprint, team, used_sp, cap))
        for sprint, team, used_sp, cap in capacity_violations:
            alerts.append({'type': '🔴 ОШИБКА ПЛАНИРОВАНИЯ', 'task_id': f'Спринт {sprint}', 'initiative': 'Все',
                           'description': f"Команда {team}: {used_sp:.0f} SP при лимите {cap:.0f} SP."})

        for t_id, status in task_status.items():
            if status in {'Done', 'Canceled', 'NotTaken'}:
                continue
            row = task_rows.get(t_id)
            if row is None:
                continue
            alerts.append({'type': '🔴 КРИТИЧЕСКИЙ (Срыв инициативы)', 'task_id': t_id,
                           'initiative': row['Номер инициативы'], 'description': f"Задача {t_id} не завершена за 6 спринтов."})

        blocked_by_map = defaultdict(set)
        for log in explain_logs:
            if log['action'] == 'Перенос' and log.get('reason_code') == 'dependency':
                for blocker in [x.strip() for x in str(log['reason']).replace('Блокируется:', '').split(',') if x.strip()]:
                    blocked_by_map[blocker].add(log['task_id'])
        for blocker_id, blocked in blocked_by_map.items():
            row = task_rows.get(blocker_id)
            if row is not None:
                alerts.append({'type': '🟡 РИСК КАСКАДНОГО СДВИГА', 'task_id': blocker_id,
                               'initiative': row['Номер инициативы'],
                               'description': f"Задержка {blocker_id} реально блокировала: {', '.join(sorted(blocked))}."})

        for sprint in self.SPRINTS:
            for role, amount in sprint_deficits[sprint].items():
                if amount > 1e-9:
                    alerts.append({'type': '🟠 РЕСУРСНЫЙ ДЕФИЦИТ', 'task_id': f'Спринт {sprint}', 'initiative': 'Все',
                                   'description': f"В спринте {sprint} не хватило {amount:.1f} ЧЧ роли «{role}» даже после попытки перевода."})
        # 🟣 ВНИМАНИЕ (Парттайм)
        part_timers = self.engineers_df[self.engineers_df['capacity_rate'] < 1.0]['engineer_id'].unique()
        if len(part_timers) > 0:
            alerts.append({
                'type': '🟣 ВНИМАНИЕ (Парттайм)', 'task_id': 'Штатное расписание', 'initiative': 'Орг. структура',
                'description': f"Инженеры разделены между командами (менее 1.0 ставки): {', '.join(part_timers)}. Суммарное время жестко ограничено."
            })

        # 🟤 СТРУКТУРНЫЙ ДЕФИЦИТ (роль отсутствует в команде)
        for row in scored_tasks:
            t_id = row['task_id']
            team = row['team_id']
            if task_status.get(t_id) in {'Canceled', 'NotTaken', 'Done'}:
                continue
            req_hours = self.task_estimates.get(t_id, {})
            for r, needed in req_hours.items():
                if needed > 0 and r not in self.team_role_hours.get(team, {}):
                    alerts.append({
                        'type': '🟤 СТРУКТУРНЫЙ ДЕФИЦИТ', 'task_id': t_id, 'initiative': row.get('Номер инициативы', 'Без инициативы'),
                        'description': f"Роль «{r}» полностью отсутствует в штате команды {team}. Выполнение задачи зависит исключительно от переводов из других команд."
                    })

        # 🟤 СТРУКТУРНЫЙ ДЕФИЦИТ (роль отсутствует в компании) — проверяем raw-смету,
        # чтобы поймать роли, отфильтрованные из task_estimates (нет в known_roles).
        if not self.estimates_df.empty:
            first_col = self.estimates_df.columns[0]
            reported_role_deficit = set()
            for row in scored_tasks:
                t_id = row['task_id']
                initiative = row['Номер инициативы']
                if task_status.get(t_id) in {'Canceled', 'NotTaken', 'Done'}:
                    continue
                if t_id not in self.estimates_df.columns:
                    continue
                for _, source_row in self.estimates_df.iterrows():
                    role_name = source_row[first_col]
                    norm_r = normalize_role_name(role_name)
                    if norm_r in {'', 'ИТОГО'}:
                        continue
                    if norm_r in self.known_roles:
                        continue
                    val = pd.to_numeric(source_row[t_id], errors='coerce')
                    if pd.isna(val) or val <= 0:
                        continue
                    key = (t_id, norm_r)
                    if key in reported_role_deficit:
                        continue
                    reported_role_deficit.add(key)
                    alerts.append({
                        'type': '🟤 СТРУКТУРНЫЙ ДЕФИЦИТ (РОЛЬ)',
                        'task_id': t_id,
                        'initiative': initiative,
                        'description': (
                            f"Роль «{norm_r}» отсутствует в штате компании. "
                            f"Задача не может быть выполнена без найма или перевода "
                            f"специалиста нужного профиля."
                        )
                    })

        all_inits = self.tasks.groupby('Номер инициативы')['task_id'].apply(list).to_dict()
        valid_init_groups = {init: ids for init, ids in all_inits.items() if all(t not in self.invalid_task_ids for t in ids)}

        canceled_inits = set()
        for init, ids in valid_init_groups.items():
            if any(task_status.get(t) in {'Canceled', 'NotTaken'} for t in ids):
                canceled_inits.add(init)

        considered_inits = {i: ids for i, ids in valid_init_groups.items() if i not in canceled_inits}
        completed_inits = sum(1 for ids in considered_inits.values() if ids and all(task_status.get(t) == 'Done' for t in ids))
        pi_pred = completed_inits / len(considered_inits) * 100 if considered_inits else 0.0

        say_do_detail = {}
        if baseline_schedule is not None and current_time_sprint > 0:
            baseline_tasks_by_sprint = {}
            for s in range(1, current_time_sprint + 1):
                baseline_tasks_by_sprint[s] = {}
                for item in baseline_schedule.get(s, []):
                    sp = float(item.get('sp', 0) or 0)
                    if sp > 0 and item['task_id'] not in baseline_tasks_by_sprint[s]:
                        baseline_tasks_by_sprint[s][item['task_id']] = sp

            for s in range(1, current_time_sprint + 1):
                planned_tasks = baseline_tasks_by_sprint[s]
                planned_sp = sum(planned_tasks.values())

                done_sp = 0.0
                late_done_sp = 0.0
                for tid, sp in planned_tasks.items():
                    fact_sprint = fact_sprint_done.get(tid)
                    if fact_sprint is None:
                        continue
                    if fact_sprint == s:
                        done_sp += sp
                    elif fact_sprint > s:
                        late_done_sp += sp

                has_commitment = planned_sp > 0
                ratio = (done_sp / planned_sp * 100) if has_commitment else 0.0
                say_do_detail[s] = {
                    'planned_sp': planned_sp,
                    'done_sp': done_sp,
                    'late_done_sp': late_done_sp,
                    'ratio': ratio,
                    'has_commitment': has_commitment,
                }
            if say_do_detail:
                avg = sum(v['ratio'] for v in say_do_detail.values()) / len(say_do_detail)
                say_do_label = f"{avg:.1f}% (в среднем по {len(say_do_detail)} пройденным спринтам)"
            else:
                say_do_label = 'н/д'
        else:
            say_do_label = 'н/д (факт ещё не внесён)'

        kpis = {
            'PI Predictability Measure': f"{pi_pred:.1f}%",
            'Total Initiatives': len(considered_inits),
            'Fully Completed Initiatives': completed_inits,
            'Canceled Initiatives': len(canceled_inits),
            'Sprint Say/Do Ratio': say_do_label,
            'Say/Do Detail': say_do_detail,
        }

        logs_df = pd.DataFrame(explain_logs, columns=LOG_COLUMNS)
        if not logs_df.empty:
            logs_df['sprint'] = pd.to_numeric(logs_df['sprint'], errors='coerce').astype('Int64')
            logs_df['hours'] = pd.to_numeric(logs_df['hours'], errors='coerce').fillna(0.0)

        return sprint_schedule, task_status, logs_df, alerts, kpis, {k: dict(v) for k, v in total_burned_hours.items()}


if __name__ == '__main__':
    path = os.path.join(os.path.dirname(__file__), 'dataset.xlsx')
    if not os.path.exists(path):
        path = os.path.join(os.path.dirname(__file__), '..', 'data', 'dataset.xlsx')
    scheduler = SmartScheduler(path)
    result = scheduler.run_smart_planning()
    print('Готово:', result[4])
