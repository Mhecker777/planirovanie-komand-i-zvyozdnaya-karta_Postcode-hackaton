import pandas as pd
import numpy as np
import networkx as nx
from parser import DataParser


def normalize_role_name(role: str) -> str:
    """Приводит названия ролей к единому стандарту."""
    r = str(role).strip()
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
        """Очистка и нормализация всех данных."""
        # 1. Задачи: ровно 45 валидных задач
        self.tasks = self.raw_tasks.dropna(subset=['task_id']).copy()
        self.tasks['task_id'] = self.tasks['task_id'].astype(str).str.strip()
        self.tasks = self.tasks[~self.tasks['task_id'].isin(['nan', ''])]
        self.tasks['team_id'] = self.tasks['team_id'].astype(str).str.strip()
        self.tasks['status'] = self.tasks['status'].astype(str).str.strip()
        self.tasks['rung'] = pd.to_numeric(self.tasks['rung'], errors='coerce').fillna(0)
        self.tasks['estimation_sp'] = pd.to_numeric(self.tasks['estimation_sp'], errors='coerce').fillna(0).astype(int)

        # 2. Граф зависимостей
        self.G = nx.DiGraph()
        valid_ids = set(self.tasks['task_id'].unique())
        for _, row in self.tasks.iterrows():
            self.G.add_node(row['task_id'], status=row['status'])

        for _, row in self.deps_df.iterrows():
            b_from = str(row['blocking_task_id']).strip()
            b_to = str(row['blocked_task_id']).strip()
            dep_type = str(row.get('dependency_type', 'depends on')).strip()
            if b_from in valid_ids and b_to in valid_ids:
                self.G.add_edge(b_from, b_to, type=dep_type)

        # 3. Лимиты SP команд на спринт (Velocity * 0.8)
        self.history_df['velocity_achieved'] = pd.to_numeric(self.history_df['velocity_achieved'], errors='coerce')
        avg_vel = self.history_df.groupby('team_id')['velocity_achieved'].mean()
        self.team_sp_capacity = (avg_vel * 0.8).astype(int).to_dict()

        # 4. Фонд часов инженеров (нормализованный)
        self.engineers_df['role_norm'] = self.engineers_df['role'].apply(normalize_role_name)
        self.engineers_df['capacity_rate'] = pd.to_numeric(self.engineers_df['capacity_rate'], errors='coerce')
        self.engineers_df['hours_per_sprint'] = self.engineers_df['capacity_rate'] * 80.0

        # Запоминаем роли, которые реально есть в штате
        self.known_roles = set(self.engineers_df['role_norm'].unique())

        # Часы по командам и общекорпоративный фонд
        self.team_role_hours = {}
        self.global_role_hours = {}
        for _, row in self.engineers_df.iterrows():
            team = str(row['team_id']).strip()
            role = row['role_norm']
            hrs = float(row['hours_per_sprint'])

            self.team_role_hours.setdefault(team, {})
            self.team_role_hours[team][role] = self.team_role_hours[team].get(role, 0.0) + hrs
            self.global_role_hours[role] = self.global_role_hours.get(role, 0.0) + hrs

        # 5. Смета трудозатрат: фильтруем 'ИТОГО' и нетехнический оверхед
        est = self.estimates_df.copy()
        est = est.set_index(est.columns[0])
        self.task_estimates = {}

        for t_id, col_data in est.items():
            clean_id = str(t_id).strip()
            self.task_estimates[clean_id] = {}
            for role_name, hrs in col_data.items():
                norm_r = normalize_role_name(role_name)
                # Игнорируем строку "Итого по ролям" и роли, которых нет в штатке (РП, поддержка)
                if norm_r == 'ИТОГО' or norm_r not in self.known_roles:
                    continue
                val = pd.to_numeric(hrs, errors='coerce')
                if pd.notnull(val) and val > 0:
                    self.task_estimates[clean_id][norm_r] = float(val)

    def run_smart_planning(self):
        """Умное планирование на 6 спринтов (12 недель)."""
        task_status = {row['task_id']: row['status'] for _, row in self.tasks.iterrows()}
        remaining_hours = {t: self.task_estimates.get(t, {}).copy() for t in task_status}

        sprint_schedule = {s: [] for s in range(1, 7)}
        explain_logs = []
        alerts = []

        # Приоритизация: сначала активные InProgress, затем по бизнес-рангу (rung)
        scored_tasks = self.tasks.copy()
        scored_tasks['is_active'] = scored_tasks['status'].isin(['InProgress'])
        scored_tasks = scored_tasks.sort_values(by=['is_active', 'rung'], ascending=[False, False])

        # ===== ЦИКЛ ПО 6 СПРИНТАМ =====
        for sprint in range(1, 7):
            sp_pool = self.team_sp_capacity.copy()
            # Пул часов на спринт
            team_hours_pool = {t: roles.copy() for t, roles in self.team_role_hours.items()}
            global_hours_pool = self.global_role_hours.copy()

            for _, row in scored_tasks.iterrows():
                t_id = row['task_id']
                team = row['team_id']
                sp_needed = row['estimation_sp']
                initiative = row['Номер инициативы']

                if task_status[t_id] == 'Done':
                    continue

                # 1. ЗАВИСИМОСТИ
                preds = list(self.G.predecessors(t_id))
                uncompleted_preds = [p for p in preds if task_status.get(p) != 'Done']
                if uncompleted_preds:
                    explain_logs.append({
                        'sprint': sprint,
                        'task_id': t_id,
                        'team': team,
                        'initiative': initiative,
                        'action': 'Перенос',
                        'reason': f"Блокируется незавершенными: {', '.join(uncompleted_preds)}"
                    })
                    continue

                # 2. ЛИМИТ STORY POINTS КОМАНДЫ
                if task_status[t_id] != 'InProgress' and sp_pool.get(team, 0) < sp_needed:
                    explain_logs.append({
                        'sprint': sprint,
                        'task_id': t_id,
                        'team': team,
                        'initiative': initiative,
                        'action': 'Перенос',
                        'reason': f"Превышен лимит SP команды {team} (свободно: {sp_pool.get(team, 0)} SP, нужно: {sp_needed} SP)"
                    })
                    continue

                # 3. ПРОВЕРКА ДОСТУПНОСТИ ЧАСОВ (Сквозная смета)
                req_hours = remaining_hours.get(t_id, {})
                can_allocate = True
                deficit_roles = []

                # Проверяем, есть ли часы в компании по требуемым специальностям
                for r, needed in req_hours.items():
                    if needed > 0 and global_hours_pool.get(r, 0.0) <= 0.0:
                        can_allocate = False
                        deficit_roles.append(r)

                if not can_allocate:
                    explain_logs.append({
                        'sprint': sprint,
                        'task_id': t_id,
                        'team': team,
                        'initiative': initiative,
                        'action': 'Перенос',
                        'reason': f"Дефицит специалистов роли: {', '.join(deficit_roles)}"
                    })
                    continue

                # 4. СПИСЫВАЕМ РЕСУРСЫ (Берем задачу в спринт)
                if task_status[t_id] != 'InProgress':
                    sp_pool[team] -= sp_needed

                task_fully_finished = True
                burned_summary = 0

                for r, needed in list(req_hours.items()):
                    if needed <= 0:
                        continue
                    # Списываем сначала из команды, если есть, иначе из общего фонда компании
                    avail = global_hours_pool.get(r, 0.0)
                    burn = min(needed, avail)

                    global_hours_pool[r] -= burn
                    if r in team_hours_pool.get(team, {}):
                        team_hours_pool[team][r] = max(0.0, team_hours_pool[team][r] - burn)

                    req_hours[r] -= burn
                    burned_summary += burn

                    if req_hours[r] > 0:
                        task_fully_finished = False

                if task_fully_finished:
                    task_status[t_id] = 'Done'
                    status_text = '✅ Завершена'
                    reason_text = f"Успешно завершена (списано {int(burned_summary)} ЧЧ)"
                else:
                    task_status[t_id] = 'InProgress'
                    status_text = '⏳ Растянута (Сплиттинг)'
                    reason_text = f"Частично выполнена (списано {int(burned_summary)} ЧЧ), остаток перенесен"

                sprint_schedule[sprint].append({
                    'task_id': t_id,
                    'initiative': initiative,
                    'team': team,
                    'sp': sp_needed,
                    'status': status_text,
                    'summary': row['summary']
                })

                explain_logs.append({
                    'sprint': sprint,
                    'task_id': t_id,
                    'team': team,
                    'initiative': initiative,
                    'action': 'Включена в план',
                    'reason': reason_text
                })

        # ===== АЛЕРТЫ РИСКОВ =====
        for _, row in self.tasks.iterrows():
            t_id = row['task_id']
            st = task_status[t_id]
            init = row['Номер инициативы']

            if st != 'Done':
                alerts.append({
                    'type': '🔴 КРИТИЧЕСКИЙ (Срыв инициативы)',
                    'task_id': t_id,
                    'initiative': init,
                    'description': f"Задача {t_id} инициативы {init} не успевает в 12 недель квартала."
                })

        # ===== РАСЧЕТ KPI =====
        all_inits = self.tasks.groupby('Номер инициативы')['task_id'].apply(list).to_dict()
        completed_inits = sum(
            1 for init, t_ids in all_inits.items() if all(task_status[tid] == 'Done' for tid in t_ids))
        pi_predictability = (completed_inits / len(all_inits)) * 100

        # Say/Do для Спринта 1
        planned_sp_s1 = sum(t['sp'] for t in sprint_schedule[1])
        done_sp_s1 = sum(t['sp'] for t in sprint_schedule[1] if 'Завершена' in t['status'])
        say_do_ratio = (done_sp_s1 / planned_sp_s1 * 100) if planned_sp_s1 > 0 else 0

        kpis = {
            'PI Predictability Measure': f"{pi_predictability:.1f}%",
            'Total Initiatives': len(all_inits),
            'Fully Completed Initiatives': completed_inits,
            'Sprint 1 Say/Do Ratio': f"{say_do_ratio:.1f}%"
        }

        return sprint_schedule, task_status, pd.DataFrame(explain_logs), alerts, kpis


# ===== ЗАПУСК =====
if __name__ == "__main__":
    scheduler = SmartScheduler("../data/dataset.csv")
    schedule, final_statuses, logs_df, alerts, kpis = scheduler.run_smart_planning()

    print("\n" + "=" * 50)
    print("📊 КЛЮЧЕВЫЕ KPI КВАРТАЛА")
    print("=" * 50)
    for k, v in kpis.items():
        print(f"• {k}: {v}")

    print("\n" + "=" * 50)
    print("📅 РАСПРЕДЕЛЕНИЕ ЗАДАЧ ПО СПРИНТАМ (1-6)")
    print("=" * 50)
    for sprint, tasks in schedule.items():
        print(f"\n--- СПРИНТ {sprint} (Взято задач: {len(tasks)}) ---")
        for t in tasks:
            print(f"  [{t['team']}] {t['task_id']} ({t['initiative']}) | {t['sp']} SP | {t['status']}")

    print("\n" + "=" * 50)
    print(f"⚠️ АЛЕРТЫ РИСКОВ (Всего срывов: {len(alerts)})")
    print("=" * 50)
    for a in alerts[:5]:
        print(f"• {a['type']} -> {a['description']}")

    print("\n" + "=" * 50)
    print("💡 ПРИМЕРЫ ОБЪЯСНЕНИЙ РЕШЕНИЙ (Explainability)")
    print("=" * 50)
    print(logs_df[logs_df['action'] == 'Перенос'].head(5)[['sprint', 'task_id', 'reason']].to_string(index=False))