import re

import pandas as pd
from parser import DataParser


def _normalize_skill(skill: str) -> str:
    """Единая нормализация навыка: trim + lower. Пустая строка, если мусор."""
    if skill is None:
        return ''
    s = str(skill).strip()
    if not s or s.lower() in {'nan', 'none', 'null'}:
        return ''
    return s.lower()


class StarMapAnalytics:
    def __init__(self, data_path: str = None, *, engineers_df=None):
        """
        Как и SmartScheduler (см. Auth_redact.md, раздел 14.1): либо data_path
        (читаем Excel через DataParser), либо готовый engineers_df — нужно,
        чтобы правки в «Управление данными» сразу отражались в Bus Factor,
        не дожидаясь пересохранения файла.
        """
        if engineers_df is not None:
            self.engineers = engineers_df.copy()
        elif data_path is not None:
            self.parser = DataParser(data_path)
            self.engineers = self.parser.get_engineers()
        else:
            raise ValueError('Нужен либо data_path, либо engineers_df.')

        self.engineers['engineer_id'] = self.engineers['engineer_id'].astype(str).str.strip()
        self.engineers['team_id'] = self.engineers['team_id'].astype(str).str.strip()
        self.engineers['role'] = self.engineers['role'].astype(str).str.strip()
        self.engineers['skills_declared'] = self.engineers['skills_declared'].astype(str).str.strip()

        # Словарь "оригинальное написание" для отображения навыков.
        # Ключ — lower-case, значение — первое встреченное оригинальное написание.
        self._skill_display = {}
        for _, row in self.engineers.iterrows():
            for raw in str(row['skills_declared']).split(','):
                norm = _normalize_skill(raw)
                if norm and norm not in self._skill_display:
                    self._skill_display[norm] = str(raw).strip()

    def _display(self, skill_key: str) -> str:
        return self._skill_display.get(skill_key, skill_key)

    def get_bus_factor_and_training(self) -> pd.DataFrame:
        """Считает Bus Factor (по компании) и подбирает кандидатов на дообучение."""
        skill_to_engineers = {}
        eng_to_skills = {}
        eng_to_teams = {}

        for _, row in self.engineers.iterrows():
            eng_id = row['engineer_id']
            team_id = row['team_id']
            orig_skills = {
                _normalize_skill(s)
                for s in str(row['skills_declared']).split(',')
                if _normalize_skill(s)
            }

            eng_to_skills.setdefault(eng_id, set()).update(orig_skills)
            eng_to_teams.setdefault(eng_id, set()).add(team_id)
            for skill in orig_skills:
                skill_to_engineers.setdefault(skill, set()).add(eng_id)

        critical_engineers = set()
        for eng_set in skill_to_engineers.values():
            if len(eng_set) == 1:
                critical_engineers.add(list(eng_set)[0])

        skill_weight = {skill: 1.0 / len(eng_set) for skill, eng_set in skill_to_engineers.items()}

        records = []
        for skill, eng_set in skill_to_engineers.items():
            bf = len(eng_set)
            recommendation = "—"
            matched_skills_note = ""

            if bf == 1:
                sole_owner = list(eng_set)[0]
                owner_skills = eng_to_skills[sole_owner]
                owner_teams = eng_to_teams.get(sole_owner, set())

                best_candidate = None
                best_score = 0.0
                best_matched = set()
                same_team_fallback = None

                for candidate, c_skills in eng_to_skills.items():
                    if candidate == sole_owner:
                        continue

                    matched = owner_skills.intersection(c_skills)
                    if not matched:
                        continue

                    score = sum(skill_weight.get(s, 0.0) for s in matched)
                    if candidate in critical_engineers:
                        score *= 0.5

                    if score > best_score:
                        best_score = score
                        best_candidate = candidate
                        best_matched = matched

                    if same_team_fallback is None:
                        candidate_teams = eng_to_teams.get(candidate, set())
                        if candidate_teams & owner_teams:
                            same_team_fallback = candidate

                if best_candidate:
                    teams_str = ', '.join(sorted(eng_to_teams.get(best_candidate, {''})))
                    top_matched = sorted(best_matched, key=lambda s: -skill_weight.get(s, 0.0))[:3]
                    matched_skills_note = ', '.join(self._display(s) for s in top_matched)
                    flag = '⚠️ ' if best_candidate in critical_engineers else '💡 '
                    recommendation = f"{flag}{best_candidate} ({teams_str}) — похожие навыки: {matched_skills_note}"
                elif same_team_fallback:
                    recommendation = f"🔄 {same_team_fallback} (из той же команды, но стек не пересекается)"
                else:
                    recommendation = "⚠️ Искать на внешнем рынке (нет подходящих кандидатов в штате)"

            records.append({
                'Технология / Компетенция': self._display(skill),
                'Bus Factor': bf,
                'Специалисты': ', '.join(sorted(list(eng_set))),
                'Кого дообучить (Рекомендация)': recommendation
            })

        df_bf = pd.DataFrame(records)
        return df_bf.sort_values(by=['Bus Factor', 'Технология / Компетенция'])

    def get_bus_factor_by_team(self) -> pd.DataFrame:
        """Bus Factor внутри КОНКРЕТНОЙ команды."""
        records = []
        for team_id, team_df in self.engineers.groupby('team_id'):
            skill_to_eng_in_team = {}
            for _, row in team_df.iterrows():
                eng_id = row['engineer_id']
                skills = {
                    _normalize_skill(s)
                    for s in str(row['skills_declared']).split(',')
                    if _normalize_skill(s)
                }
                for skill in skills:
                    skill_to_eng_in_team.setdefault(skill, set()).add(eng_id)

            for skill, eng_set in skill_to_eng_in_team.items():
                bf_team = len(eng_set)
                records.append({
                    'Команда': team_id,
                    'Технология / Компетенция': self._display(skill),
                    'Bus Factor команды': bf_team,
                    'Специалисты в команде': ', '.join(sorted(eng_set)),
                    'Риск': '🔥 Критический (только 1 в команде)' if bf_team == 1 else '✅ В норме'
                })

        df = pd.DataFrame(records)
        return df.sort_values(by=['Bus Factor команды', 'Команда', 'Технология / Компетенция'])

    def get_critical_roles_shortage(self) -> pd.DataFrame:
        role_counts = self.engineers.groupby(['team_id', 'role'])['engineer_id'].nunique().reset_index()
        role_counts.columns = ['Команда', 'Роль', 'Кол-во сотрудников']
        role_counts['Риск'] = role_counts['Кол-во сотрудников'].apply(
            lambda x: '⚠️ Высокий риск (1 сотрудник)' if x == 1 else 'Низкий'
        )
        return role_counts

    def get_critical_skill_tasks(self, tasks_df: pd.DataFrame) -> pd.DataFrame:
        """
        Связывает критичные навыки (Bus Factor = 1) с конкретными задачами.
        Прямое совпадение — если название навыка встречается в текстовых полях задачи.
        Косвенное — задачи команды единственного владельца навыка.
        """
        skill_to_engineers = {}
        eng_to_team = {}

        for _, row in self.engineers.iterrows():
            eng_id = str(row['engineer_id']).strip()
            team_id = str(row['team_id']).strip()

            skills = {
                _normalize_skill(s)
                for s in str(row['skills_declared']).split(',')
                if _normalize_skill(s)
            }

            eng_to_team[eng_id] = team_id

            for skill in skills:
                skill_to_engineers.setdefault(skill, set()).add(eng_id)

        critical_skills = {
            skill: list(eng_set)[0]
            for skill, eng_set in skill_to_engineers.items()
            if len(eng_set) == 1
        }

        records = []

        preferred_text_columns = [
            'summary', 'description', 'Описание',
            'Наименование', 'Название', 'Задача',
        ]
        text_columns = [c for c in preferred_text_columns if c in tasks_df.columns]
        if not text_columns:
            text_columns = [c for c in tasks_df.columns if tasks_df[c].dtype == 'object']

        for skill, owner_id in critical_skills.items():
            owner_team = eng_to_team.get(owner_id, '')
            skill_lower = skill.lower().strip()
            if not skill_lower:
                continue

            for _, task in tasks_df.iterrows():
                task_id = str(task.get('task_id', '')).strip()
                task_team = str(task.get('team_id', '')).strip()
                if not task_id:
                    continue

                task_text_parts = []
                for column in text_columns:
                    value = task.get(column, '')
                    if pd.notna(value):
                        task_text_parts.append(str(value))
                task_text = ' '.join(task_text_parts).lower()

                # ЗАЩИТА ОТ ЛОЖНЫХ СОВПАДЕНИЙ: короткие токены навыка (напр.
                # «ТЗ», «C#», «Go») как голая подстрока могут случайно
                # встретиться внутри произвольного текста и породить
                # недостоверное "прямое упоминание". Для токенов короче
                # 3 символов прямое сопоставление с текстом отключаем —
                # для них остаётся только связь "задача команды владельца".
                # Для остальных используем совпадение по границе слова,
                # а не голую подстроку (иначе, например, skill "react"
                # ложно сработает на слове "reaction").
                if len(skill_lower) < 3:
                    direct_match = False
                else:
                    pattern = r'(?<![a-zA-Zа-яА-Я0-9])' + re.escape(skill_lower) + r'(?![a-zA-Zа-яА-Я0-9])'
                    direct_match = re.search(pattern, task_text) is not None
                same_team = task_team == owner_team

                if direct_match:
                    connection = 'Прямое упоминание навыка'
                    priority = 1
                elif same_team:
                    connection = 'Задача команды владельца'
                    priority = 2
                else:
                    continue

                records.append({
                    'Навык': self._display(skill),
                    'Bus Factor': 1,
                    'Владелец навыка': owner_id,
                    'Команда владельца': owner_team,
                    'Задача': task_id,
                    'Команда задачи': task_team,
                    'Связь': connection,
                    '_priority': priority,
                })

        if not records:
            return pd.DataFrame(columns=[
                'Навык', 'Bus Factor', 'Владелец навыка',
                'Команда владельца', 'Задача', 'Команда задачи', 'Связь',
            ])

        result = pd.DataFrame(records)
        result = result.sort_values(by=['_priority', 'Навык', 'Задача'])
        return result.drop(columns=['_priority'])

    def get_star_map_data(self) -> pd.DataFrame:
        """Данные для визуальной звёздной карты."""
        skill_to_engineers = {}

        for _, row in self.engineers.iterrows():
            eng_id = str(row['engineer_id']).strip()
            skills = {
                _normalize_skill(s)
                for s in str(row['skills_declared']).split(',')
                if _normalize_skill(s)
            }
            for skill in skills:
                skill_to_engineers.setdefault(skill, set()).add(eng_id)

        records = []
        for skill, engineers in skill_to_engineers.items():
            bf = len(engineers)
            records.append({
                'Навык': self._display(skill),
                'Навык_key': skill,
                'Bus Factor': bf,
                'Специалисты': ', '.join(sorted(engineers)),
                'Критичный': bf == 1,
            })

        if not records:
            return pd.DataFrame(columns=['Навык', 'Навык_key', 'Bus Factor', 'Специалисты', 'Критичный'])

        return pd.DataFrame(records).sort_values(
            by=['Критичный', 'Bus Factor', 'Навык'],
            ascending=[False, True, True],
        )

    def get_team_skill_matrix(self) -> pd.DataFrame:
        """
        Матрица «команда × навык»: сколько инженеров в команде владеют навыком.
        Используется для тепловой карты.
        Ноль в клетке = навыка в команде нет.
        """
        rows = []
        for _, row in self.engineers.iterrows():
            team = str(row['team_id']).strip()
            eng_id = str(row['engineer_id']).strip()
            if not team or not eng_id:
                continue
            skills = {
                _normalize_skill(s)
                for s in str(row['skills_declared']).split(',')
                if _normalize_skill(s)
            }
            for skill in skills:
                rows.append({'team': team, 'skill': skill, 'engineer': eng_id})

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        matrix = df.groupby(['team', 'skill'])['engineer'].nunique().unstack(fill_value=0)
        matrix.columns = [self._display(c) for c in matrix.columns]
        return matrix