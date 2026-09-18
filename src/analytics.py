import pandas as pd
from parser import DataParser


class StarMapAnalytics:
    def __init__(self, data_path: str):
        self.parser = DataParser(data_path)
        self.engineers = self.parser.get_engineers()

        self.engineers['engineer_id'] = self.engineers['engineer_id'].astype(str).str.strip()
        self.engineers['team_id'] = self.engineers['team_id'].astype(str).str.strip()
        self.engineers['role'] = self.engineers['role'].astype(str).str.strip()
        self.engineers['skills_declared'] = self.engineers['skills_declared'].astype(str).str.strip()

    def get_bus_factor_and_training(self) -> pd.DataFrame:
        """Считает Bus Factor (по компании) и подбирает кандидатов на дообучение."""
        skill_to_engineers = {}
        eng_to_skills = {}
        eng_to_team = {}

        for _, row in self.engineers.iterrows():
            eng_id = row['engineer_id']
            team_id = row['team_id']
            orig_skills = [s.strip() for s in row['skills_declared'].split(',') if s.strip()]

            eng_to_skills[eng_id] = set(orig_skills)
            eng_to_team[eng_id] = team_id
            for skill in orig_skills:
                skill_to_engineers.setdefault(skill, set()).add(eng_id)

        critical_engineers = set()
        for eng_set in skill_to_engineers.values():
            if len(eng_set) == 1:
                critical_engineers.add(list(eng_set)[0])

        # ВЕС РЕДКОСТИ НАВЫКА (аналог IDF): чем меньше людей в компании владеют
        # навыком, тем больше вес совпадения по нему при подборе кандидата.
        # Раньше совпадение считалось "в лоб" (просто число общих навыков),
        # из-за чего общие инструментальные навыки (REST API, SQL, Git,
        # Postman — они почти у всех) перевешивали профильные и давали
        # нерелевантные рекомендации (пример из проверки: QA-инженера
        # рекомендовали в Android-разработчики из-за общего "Java").
        skill_weight = {skill: 1.0 / len(eng_set) for skill, eng_set in skill_to_engineers.items()}

        records = []
        for skill, eng_set in skill_to_engineers.items():
            bf = len(eng_set)
            recommendation = "—"
            matched_skills_note = ""

            if bf == 1:
                sole_owner = list(eng_set)[0]
                owner_skills = eng_to_skills[sole_owner]
                owner_team = eng_to_team[sole_owner]

                best_candidate = None
                best_score = 0.0
                best_matched = set()
                same_team_fallback = None

                for candidate, c_skills in eng_to_skills.items():
                    if candidate == sole_owner or candidate in critical_engineers:
                        continue

                    matched = owner_skills.intersection(c_skills)
                    score = sum(skill_weight.get(s, 0.0) for s in matched)

                    if score > best_score:
                        best_score = score
                        best_candidate = candidate
                        best_matched = matched

                    if eng_to_team[candidate] == owner_team:
                        same_team_fallback = candidate

                if best_candidate:
                    team = eng_to_team[best_candidate]
                    # Показываем САМИ совпавшие навыки — по ним сразу видно,
                    # релевантна рекомендация или это случайное совпадение
                    # по общеинструментальному навыку.
                    top_matched = sorted(best_matched, key=lambda s: -skill_weight.get(s, 0.0))[:3]
                    matched_skills_note = ', '.join(top_matched)
                    recommendation = f"💡 {best_candidate} ({team}) — похожие навыки: {matched_skills_note}"
                elif same_team_fallback:
                    recommendation = f"🔄 {same_team_fallback} (из той же команды, но стек не пересекается)"
                else:
                    recommendation = "⚠️ Искать на внешнем рынке (нет подходящих кандидатов в штате)"

            records.append({
                'Технология / Компетенция': skill,
                'Bus Factor': bf,
                'Специалисты': ', '.join(sorted(list(eng_set))),
                'Кого дообучить (Рекомендация)': recommendation
            })

        df_bf = pd.DataFrame(records)
        return df_bf.sort_values(by=['Bus Factor', 'Технология / Компетенция'])

    def get_bus_factor_by_team(self) -> pd.DataFrame:
        """
        Bus Factor В РАЗРЕЗЕ КОМАНД: какие навыки внутри КОНКРЕТНОЙ команды
        держатся на одном человеке — даже если в компании в целом носителей
        больше (они просто в другой команде и не подставят плечо в моменте).
        """
        records = []
        for team_id, team_df in self.engineers.groupby('team_id'):
            skill_to_eng_in_team = {}
            for _, row in team_df.iterrows():
                eng_id = row['engineer_id']
                skills = [s.strip() for s in row['skills_declared'].split(',') if s.strip()]
                for skill in skills:
                    skill_to_eng_in_team.setdefault(skill, set()).add(eng_id)

            for skill, eng_set in skill_to_eng_in_team.items():
                bf_team = len(eng_set)
                records.append({
                    'Команда': team_id,
                    'Технология / Компетенция': skill,
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