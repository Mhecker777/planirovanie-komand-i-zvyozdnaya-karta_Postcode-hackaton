import pandas as pd
import numpy as np
import networkx as nx
from parser import DataParser


def normalize_role_name(role: str) -> str:
    """Приводит названия ролей к единому стандарту (исправление опечаток в датасете)."""
    r = str(role).strip()
    r_lower = r.lower()
    if 'итого' in r_lower: return 'ИТОГО'
    if 'девопс' in r_lower: return 'Девопс'
    if 'ios' in r_lower: return 'Разработчик iOS'
    if 'bigdata' in r_lower or 'big data' in r_lower:
        if 'руководитель' in r_lower: return 'Руководитель BigData'
        if 'аналитик' in r_lower: return 'Аналитик Big Data'
        if 'разработчик' in r_lower: return 'Разработчик Big Data'
    return r


class SmartScheduler:
    def __init__(self, data_path: str):
        self.parser = DataParser(data_path)
        self.raw_tasks = self.parser.get_tasks()
        self.estimates_df = self.parser.get_estimates()
        self.deps_df = self.parser.get_dependencies()
        self.engineers_df = self.parser.get_engineers()
        self.history_df = self.parser.get_team_history()
        self._prepare_data()

    def _prepare_data(self):
        """Очистка и нормализация всех данных перед планированием."""
        # 1. ЗАДАЧИ
        self.tasks = self.raw_tasks.dropna(subset=['task_id']).copy()
        self.tasks['task_id'] = self.tasks['task_id'].astype(str).str.strip()
        self.tasks = self.tasks[~self.tasks['task_id'].isin(['nan', ''])]
        self.tasks['team_id'] = self.tasks['team_id'].astype(str).str.strip()
        self.tasks['status'] = self.tasks['status'].astype(str).str.strip()
        self.tasks['rung'] = pd.to_numeric(self.tasks['rung'], errors='coerce').fillna(0)
        self.tasks['estimation_sp'] = pd.to_numeric(self.tasks['estimation_sp'], errors='coerce').fillna(0).astype(int)

        # 2. ГРАФ ЗАВИСИМОСТЕЙ
        self.G = nx.DiGraph()
        valid_ids = set(self.tasks['task_id'].unique())
        for _, row in self.tasks.iterrows():
            self.G.add_node(row['task_id'], status=row['status'])

        for _, row in self.deps_df.iterrows():
            b_from = str(row['blocking_task_id']).strip()
            b_to = str(row['blocked_task_id']).strip()
            if b_from in valid_ids and b_to in valid_ids:
                self.G.add_edge(b_from, b_to)

        # 3. ЛИМИТЫ SP (Усечение вниз по правилам)
        self.history_df['velocity_achieved'] = pd.to_numeric(self.history_df['velocity_achieved'], errors='coerce')
        avg_vel = self.history_df.groupby('team_id')['velocity_achieved'].mean()
        self.team_sp_capacity = (avg_vel * 0.8).astype(int).to_dict()

        # 4. ФОНД ЧАСОВ ИНЖЕНЕРОВ (строго по командам)
        self.engineers_df['role_norm'] = self.engineers_df['role'].apply(normalize_role_name)
        self.engineers_df['capacity_rate'] = pd.to_numeric(self.engineers_df['capacity_rate'], errors='coerce')
        self.engineers_df['hours_per_sprint'] = self.engineers_df['capacity_rate'] * 80.0

        self.known_roles = set(self.engineers_df['role_norm'].unique())
        self.team_role_hours = {}
        for _, row in self.engineers_df.iterrows():
            team = str(row['team_id']).strip()
            role = row['role_norm']
            hrs = float(row['hours_per_sprint'])
            self.team_role_hours.setdefault(team, {})
            self.team_role_hours[team][role] = self.team_role_hours[team].get(role, 0.0) + hrs

        # 5. СМЕТА ТРУДОЗАТРАТ
        est = self.estimates_df.set_index(self.estimates_df.columns[0])
        self.task_estimates = {}
        for t_id, col_data in est.items():
            clean_id = str(t_id).strip()
            self.task_estimates[clean_id] = {}
            for role_name, hrs in col_data.items():
                norm_r = normalize_role_name(role_name)
                # Игнорируем "Итого" и нетехнический оверхед
                if norm_r == 'ИТОГО' or norm_r not in self.known_roles: continue
                val = pd.to_numeric(hrs, errors='coerce')
                if pd.notnull(val) and val > 0:
                    self.task_estimates[clean_id][norm_r] = float(val)

        # 6. ПРОВЕРКА ЦЕЛОСТНОСТИ СМЕТЫ (data quality)
        # estimated_hh — независимая итоговая оценка по задаче (колонка в Tasks),
        # сумма по ролям — то, что реально разложено в Калькуляторе сметы.
        # Раньше estimated_hh вообще не читался парсером и такой проверки не
        # существовало. Расхождение не блокирует планирование, но должно быть
        # видно пользователю — оно означает, что чья-то оценка (общая или
        # постатейная) неточна.
        self.data_quality_warnings = []
        if 'estimated_hh' in self.tasks.columns:
            for _, row in self.tasks.iterrows():
                t_id = row['task_id']
                declared_total = pd.to_numeric(row.get('estimated_hh'), errors='coerce')
                role_sum = sum(self.task_estimates.get(t_id, {}).values())
                if pd.notnull(declared_total) and abs(declared_total - role_sum) > 1:
                    self.data_quality_warnings.append(
                        f"{t_id}: итоговая оценка в Tasks (estimated_hh = {declared_total:.0f} ЧЧ) "
                        f"не совпадает с суммой по ролям в смете ({role_sum:.0f} ЧЧ), "
                        f"расхождение {abs(declared_total - role_sum):.0f} ЧЧ"
                    )

    def get_data_quality_warnings(self) -> list:
        """Список расхождений в исходных данных, обнаруженных при подготовке (не блокируют план)."""
        return self.data_quality_warnings

    def get_team_reliability(self) -> pd.DataFrame:
        """
        Историческая стабильность команды — раньше использовалась только
        velocity_achieved (средняя скорость), а planned_sp из Team_history
        не читался вообще. Два одинаковых средних Velocity — не одно и то
        же, если одна команда исполняет СВОЙ ЖЕ план стабильно, а другая —
        то в 2 раза больше, то в 2 раза меньше. Это напрямую влияет на то,
        насколько можно доверять прогнозу по такой команде.
        """
        hist = self.history_df.copy()
        hist['velocity_achieved'] = pd.to_numeric(hist['velocity_achieved'], errors='coerce')
        if 'planned_sp' not in hist.columns:
            return pd.DataFrame(columns=['Команда', 'Примечание']).assign(
                Примечание=['Поле planned_sp не найдено в Team_history'])
        hist['planned_sp'] = pd.to_numeric(hist['planned_sp'], errors='coerce')
        hist['own_say_do'] = hist.apply(
            lambda r: (r['velocity_achieved'] / r['planned_sp'] * 100)
            if pd.notnull(r['planned_sp']) and r['planned_sp'] > 0 else np.nan,
            axis=1
        )

        records = []
        for team, g in hist.groupby('team_id'):
            avg_v, std_v = g['velocity_achieved'].mean(), g['velocity_achieved'].std()
            avg_sd, std_sd = g['own_say_do'].mean(), g['own_say_do'].std()
            # Порог 15 п.п. разброса — условная граница «стабильно / нестабильно»,
            # подобрана эмпирически, стоит откалибровать вместе с бизнесом.
            stable = pd.notnull(std_sd) and std_sd < 15
            records.append({
                'Команда': team,
                'Ср. Velocity': round(avg_v, 1) if pd.notnull(avg_v) else None,
                'Разброс Velocity (σ)': round(std_v, 1) if pd.notnull(std_v) else None,
                'Ср. исполнение своего плана': f"{avg_sd:.0f}%" if pd.notnull(avg_sd) else 'н/д',
                'Разброс исполнения (σ)': f"{std_sd:.0f}%" if pd.notnull(std_sd) else 'н/д',
                'Оценка стабильности': ('✅ Стабильна' if stable else '⚠️ Нестабильна') if pd.notnull(std_sd) else 'н/д',
                'Спринтов в истории': len(g),
            })
        return pd.DataFrame(records).sort_values('Команда')

    def get_transfer_summary(self, logs_df: pd.DataFrame) -> pd.DataFrame:
        """
        Агрегат автоматических реорганизаций за весь прогон: какая команда
        сколько часов какой роли отдала и кому. Нужен, чтобы пользователь
        видел МАСШТАБ автоматических решений одним взглядом, а не построчно
        листал лог — и мог осознанно запретить конкретный перевод (см. UI).
        """
        transfers = logs_df[logs_df['action'] == 'Реорганизация (Перевод)']
        if transfers.empty:
            return pd.DataFrame(columns=['Команда-донор', 'Роль', 'Команда-получатель', 'Часов передано'])
        summary = transfers.groupby(['donor_team', 'role', 'recipient_team'])['hours'].sum().reset_index()
        summary.columns = ['Команда-донор', 'Роль', 'Команда-получатель', 'Часов передано']
        return summary.sort_values('Часов передано', ascending=False)

    def run_smart_planning(self, fact_sprint_done=None, current_time_sprint=0, baseline_schedule=None,
                            forbidden_donors=None):
        """
        Ядро симуляции с Машиной времени и трекером утилизации.

        baseline_schedule: результат ПЕРВОГО прогона (current_time_sprint=0), нужен
        только для честного расчёта Say/Do Ratio — знаменатель обязан оставаться
        исходным планом, а не пересчитанным (см. README, раздел про Say/Do).

        forbidden_donors: множество пар (team_id, роль), которые НЕЛЬЗЯ использовать
        как донора при автоматической реорганизации — ручной рычаг управления
        поверх автоматики (см. README/ROADMAP, «управляемая реорганизация»).
        """
        if fact_sprint_done is None: fact_sprint_done = {}
        if forbidden_donors is None: forbidden_donors = set()

        task_status = {row['task_id']: row['status'] for _, row in self.tasks.iterrows()}
        remaining_hours = {t: self.task_estimates.get(t, {}).copy() for t in task_status}

        sprint_schedule = {s: [] for s in range(1, 7)}
        explain_logs = []
        alerts = []
        sprint_when_done = {}
        total_burned_hours = {}  # Для трекера сотрудников
        # Реальный дефицит по ролям, по спринтам — фиксируем в момент обнаружения
        # в цикле (а не приближённо постфактум), чтобы 🟠-алерт был честной
        # реконструкцией того, что реально произошло в симуляции.
        sprint_deficits = {s: {} for s in range(1, 7)}

        init_sizes = self.tasks.groupby('Номер инициативы')['estimation_sp'].sum().to_dict()
        self.tasks['init_size'] = self.tasks['Номер инициативы'].map(init_sizes)

        def topo_sort_key(node):
            if node not in self.tasks['task_id'].values: return (999, 999, 999)
            row = self.tasks[self.tasks['task_id'] == node].iloc[0]
            is_active = 0 if row['status'] == 'InProgress' else 1
            return (is_active, row['init_size'], -float(row['rung']))

        try:
            topo_order = list(nx.lexicographical_topological_sort(self.G, key=topo_sort_key))
        except nx.NetworkXUnfeasible:
            topo_order = list(self.G.nodes)

        ordered_tasks = [self.tasks[self.tasks['task_id'] == n].iloc[0] for n in topo_order if
                         not self.tasks[self.tasks['task_id'] == n].empty]
        scored_tasks = pd.DataFrame(ordered_tasks)

        # ===== ЦИКЛ 6 СПРИНТОВ =====
        for sprint in range(1, 7):
            sp_pool = self.team_sp_capacity.copy()
            team_hours_pool = {t: roles.copy() for t, roles in self.team_role_hours.items()}

            # 1. ПРОШЛОЕ (Фиксируем факт из UI)
            if sprint <= current_time_sprint:
                for _, row in scored_tasks.iterrows():
                    t_id = row['task_id']
                    if fact_sprint_done.get(t_id) == sprint:
                        team = row['team_id']
                        sp_pool[team] -= row['estimation_sp']
                        task_status[t_id] = 'Done'
                        sprint_when_done[t_id] = sprint
                        sprint_schedule[sprint].append({
                            'task_id': t_id, 'initiative': row['Номер инициативы'], 'team': team,
                            'sp': row['estimation_sp'], 'status': '✅ Завершена (Факт)', 'summary': row['summary']
                        })

                        # Записываем часы в трекер сотрудников
                        orig_hours = self.task_estimates.get(t_id, {})
                        for r, hrs in orig_hours.items():
                            total_burned_hours.setdefault(team, {}).setdefault(r, 0.0)
                            total_burned_hours[team][r] += hrs
                            if t_id in remaining_hours and r in remaining_hours[t_id]:
                                remaining_hours[t_id][r] = 0
                continue

            # 2. БУДУЩЕЕ (Алгоритм планирования)
            for _, row in scored_tasks.iterrows():
                t_id = row['task_id']
                team = row['team_id']
                sp_needed = row['estimation_sp']
                initiative = row['Номер инициативы']

                if task_status[t_id] == 'Done': continue

                preds = list(self.G.predecessors(t_id))
                blocking = [p for p in preds if task_status.get(p) != 'Done' or sprint_when_done.get(p, 0) >= sprint]
                if blocking:
                    explain_logs.append(
                        {'sprint': sprint, 'task_id': t_id, 'team': team, 'initiative': initiative, 'action': 'Перенос',
                         'reason': f"Блокируется: {', '.join(blocking)}"})
                    continue

                if task_status[t_id] != 'InProgress' and sp_pool.get(team, 0) < sp_needed:
                    explain_logs.append(
                        {'sprint': sprint, 'task_id': t_id, 'team': team, 'initiative': initiative, 'action': 'Перенос',
                         'reason': f"Превышен лимит SP ({sp_pool.get(team, 0)} < {sp_needed})"})
                    continue

                req_hours = remaining_hours.get(t_id, {})
                can_allocate = True
                deficit_roles = []
                temp_hours_pool = {t: roles.copy() for t, roles in team_hours_pool.items()}
                transfers_for_task = []

                for r, needed in req_hours.items():
                    if needed <= 0: continue
                    avail = temp_hours_pool.get(team, {}).get(r, 0.0)
                    if avail < needed:
                        deficit = needed - avail
                        # Сортируем доноров по убыванию остатка этой роли — сначала
                        # берём у той команды, где запас больше, чтобы с большей
                        # вероятностью не выесть подчистую маленький резерв команды,
                        # которой этот же ресурс может понадобиться чуть позже в
                        # этом же спринте для её собственной задачи. Это не полная
                        # защита от "голодания" донора (для этого нужен полный
                        # предварительный расчёт потребностей всех команд наперёд —
                        # см. план развития, п.1.4), но заметно снижает риск.
                        donors_sorted = sorted(
                            ((dt, dr) for dt, dr in temp_hours_pool.items()
                             if dt != team and (dt, r) not in forbidden_donors),
                            key=lambda x: x[1].get(r, 0.0),
                            reverse=True
                        )
                        for donor_team, d_roles in donors_sorted:
                            donor_avail = d_roles.get(r, 0.0)
                            if donor_avail > 0:
                                transfer_amount = min(deficit, donor_avail)
                                temp_hours_pool[donor_team][r] -= transfer_amount
                                temp_hours_pool.setdefault(team, {})[r] = temp_hours_pool[team].get(r,
                                                                                                    0.0) + transfer_amount
                                deficit -= transfer_amount
                                transfers_for_task.append(
                                    {'sprint': sprint, 'task_id': t_id, 'team': team, 'initiative': initiative,
                                     'action': 'Реорганизация (Перевод)',
                                     'reason': f"Перевод '{r}' из {donor_team} ({transfer_amount} ЧЧ)",
                                     'donor_team': donor_team, 'recipient_team': team,
                                     'role': r, 'hours': transfer_amount})
                                if deficit <= 0: break
                        if deficit > 0:
                            can_allocate = False
                            deficit_roles.append(r)
                            sprint_deficits[sprint][r] = sprint_deficits[sprint].get(r, 0.0) + deficit

                if not can_allocate:
                    explain_logs.append(
                        {'sprint': sprint, 'task_id': t_id, 'team': team, 'initiative': initiative, 'action': 'Перенос',
                         'reason': f"Глобальный дефицит специалистов: {', '.join(deficit_roles)}"})
                    continue

                team_hours_pool = temp_hours_pool
                explain_logs.extend(transfers_for_task)

                if task_status[t_id] != 'InProgress': sp_pool[team] -= sp_needed
                task_fully_finished = True
                burned_summary = 0

                for r, needed in list(req_hours.items()):
                    if needed <= 0: continue
                    avail = team_hours_pool[team].get(r, 0.0)
                    burn = min(needed, avail)
                    team_hours_pool[team][r] -= burn
                    req_hours[r] -= burn
                    burned_summary += burn

                    # Записываем в трекер
                    total_burned_hours.setdefault(team, {}).setdefault(r, 0.0)
                    total_burned_hours[team][r] += burn

                    if req_hours[r] > 0: task_fully_finished = False

                if task_fully_finished:
                    task_status[t_id] = 'Done'
                    sprint_when_done[t_id] = sprint
                    status_text = '✅ Завершена'
                    reason_text = f"Выполнена (списано {int(burned_summary)} ЧЧ)"
                else:
                    task_status[t_id] = 'InProgress'
                    status_text = '⏳ Растянута (Сплиттинг)'
                    reason_text = f"Частично выполнена ({int(burned_summary)} ЧЧ), остаток перенесен"

                sprint_schedule[sprint].append(
                    {'task_id': t_id, 'initiative': initiative, 'team': team, 'sp': sp_needed, 'status': status_text,
                     'summary': row['summary']})
                explain_logs.append({'sprint': sprint, 'task_id': t_id, 'team': team, 'initiative': initiative,
                                     'action': 'Включена в план', 'reason': reason_text})

        # АЛЕРТЫ
        for t_id, status in task_status.items():
            if status != 'Done':
                alerts.append(
                    {'type': '🔴 КРИТИЧЕСКИЙ (Срыв инициативы)', 'task_id': t_id,
                     'initiative': self.tasks.loc[self.tasks['task_id'] == t_id, 'Номер инициативы'].iloc[0],
                     'description': f"Задача {t_id} вылетает за 12 недель."})

        # 🟡 РИСК КАСКАДНОГО СДВИГА — раньше проверялся гипотетический граф
        # (любая InProgress-задача с потомками в принципе), из-за чего почти
        # совпадал по составу с 🔴-алертом и не добавлял новой информации.
        # Теперь смотрим ФАКТИЧЕСКИ зафиксированные в логах блокировки: если
        # задача реально помешала стартовать хотя бы одной другой задаче
        # (была в чьей-то причине переноса «Блокируется: ...») — вот это и
        # есть настоящий, наблюдаемый каскадный сдвиг, а не гипотеза.
        blocked_by_map = {}
        for log in explain_logs:
            if log['action'] == 'Перенос' and log['reason'].startswith('Блокируется:'):
                blockers = [b.strip() for b in log['reason'].replace('Блокируется:', '').split(',')]
                for b in blockers:
                    blocked_by_map.setdefault(b, set()).add(log['task_id'])

        for blocker_id, blocked_set in blocked_by_map.items():
            init_series = self.tasks.loc[self.tasks['task_id'] == blocker_id, 'Номер инициативы']
            init_val = init_series.iloc[0] if not init_series.empty else '?'
            alerts.append({
                'type': '🟡 РИСК КАСКАДНОГО СДВИГА', 'task_id': blocker_id, 'initiative': init_val,
                'description': f"Задержка задачи {blocker_id} реально сдвинула старт "
                               f"{len(blocked_set)} зависимых задач: {', '.join(sorted(blocked_set))}."
            })

        # 🟠 РЕСУРСНЫЙ ДЕФИЦИТ — раньше был один общий алерт на весь остаток
        # бэклога квартала (сравнение с фондом ОДНОГО спринта — некорректная
        # база). Теперь — по каждому спринту отдельно, на основе того, что
        # реально не удалось закрыть в момент планирования (sprint_deficits),
        # ближе к формулировке ТЗ «дефицит на следующий спринт».
        for s in range(1, 7):
            for role, amount in sprint_deficits[s].items():
                alerts.append({
                    'type': '🟠 РЕСУРСНЫЙ ДЕФИЦИТ', 'task_id': f'Спринт {s}', 'initiative': 'Все',
                    'description': f"В спринте {s} не хватило {int(amount)} ЧЧ роли «{role}» "
                                   f"даже после попытки перевода из других команд."
                })

        all_inits = self.tasks.groupby('Номер инициативы')['task_id'].apply(list).to_dict()
        completed_inits = sum(
            1 for init, t_ids in all_inits.items() if all(task_status[tid] == 'Done' for tid in t_ids))
        pi_pred = (completed_inits / len(all_inits)) * 100 if all_inits else 0

        # ===== SAY/DO RATIO =====
        # ВАЖНО: числитель и знаменатель НЕ должны браться из одного и того же
        # пересчитанного плана — иначе метрика тавтологична и почти всегда
        # показывает ~100% (проверено: даже при провале 6 из 8 задач спринта
        # старая версия показывала 100%, т.к. "запланировано" пересчитывалось
        # из того же набора, что и "сделано").
        # Знаменатель — SP из ПЕРВОНАЧАЛЬНОГО плана (baseline_schedule),
        # числитель — SP задач, которые пользователь явно подтвердил как
        # реально сделанные в этом спринте (fact_sprint_done).
        say_do_detail = {}
        if baseline_schedule is not None and current_time_sprint > 0:
            for s in range(1, current_time_sprint + 1):
                planned_sp = sum(t['sp'] for t in baseline_schedule.get(s, []))
                done_sp = sum(
                    row['estimation_sp']
                    for _, row in self.tasks.iterrows()
                    if fact_sprint_done.get(row['task_id']) == s
                )
                ratio = (done_sp / planned_sp * 100) if planned_sp > 0 else 0.0
                say_do_detail[s] = {'planned_sp': planned_sp, 'done_sp': done_sp, 'ratio': ratio}

            if say_do_detail:
                avg_ratio = sum(v['ratio'] for v in say_do_detail.values()) / len(say_do_detail)
                say_do_label = f"{avg_ratio:.1f}% (в среднем по {len(say_do_detail)} пройденным спринтам)"
            else:
                say_do_label = "н/д"
        else:
            # Факта ещё нет (current_time_sprint == 0) — это первый прогон,
            # он и есть baseline. Say/Do здесь считать не из чего: план ещё
            # не сверялся с реальностью. Показываем явную пометку, а не число,
            # чтобы не создавать иллюзию готового показателя.
            say_do_label = "н/д (факт ещё не внесён)"

        kpis = {
            'PI Predictability Measure': f"{pi_pred:.1f}%",
            'Total Initiatives': len(all_inits),
            'Fully Completed Initiatives': completed_inits,
            'Sprint Say/Do Ratio': say_do_label,
            'Say/Do Detail': say_do_detail,  # для таблицы план/факт по спринтам в UI
        }

        # ВОЗВРАЩАЕМ total_burned_hours 6-м аргументом!
        return sprint_schedule, task_status, pd.DataFrame(explain_logs), alerts, kpis, total_burned_hours


if __name__ == "__main__":
    scheduler = SmartScheduler("../data/dataset.xlsx")
    schedule, final_statuses, logs_df, alerts, kpis = scheduler.run_smart_planning()
    print("Всё работает! KPI:", kpis)