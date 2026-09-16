import pandas as pd
from parser import DataParser


class StarMapAnalytics:
    def __init__(self, data_path: str):
        self.parser = DataParser(data_path)
        self.engineers = self.parser.get_engineers()
        self.tasks = self.parser.get_tasks()

        # Очистка данных
        self.engineers['engineer_id'] = self.engineers['engineer_id'].astype(str).str.strip()
        self.engineers['team_id'] = self.engineers['team_id'].astype(str).str.strip()
        self.engineers['role'] = self.engineers['role'].astype(str).str.strip()
        self.engineers['skills_declared'] = self.engineers['skills_declared'].astype(str).str.strip()

    def get_bus_factor(self) -> pd.DataFrame:
        """
        Считает Bus Factor по технологиям и навыкам.
        Bus Factor = количество уникальных специалистов, владеющих технологией.
        Если Bus Factor == 1 — это критический риск для бэклога!
        """
        skill_to_engineers = {}

        for _, row in self.engineers.iterrows():
            eng_id = row['engineer_id']
            skills_str = row['skills_declared']

            # Разделяем навыки по запятой
            skills = [s.strip() for s in skills_str.split(',') if s.strip()]
            for skill in skills:
                skill_to_engineers.setdefault(skill, set()).add(eng_id)

        # Формируем итоговый датафрейм
        records = []
        for skill, eng_set in skill_to_engineers.items():
            bf = len(eng_set)
            records.append({
                'Технология / Компетенция': skill,
                'Bus Factor': bf,
                'Количество специалистов': bf,
                'Специалисты': ', '.join(sorted(list(eng_set))),
                'Уровень риска': '🔥 Критический (Bus Factor = 1)' if bf == 1 else '✅ В норме (> 1)'
            })

        df_bf = pd.DataFrame(records)
        return df_bf.sort_values(by=['Bus Factor', 'Технология / Компетенция'])

    def get_critical_roles_shortage(self) -> pd.DataFrame:
        """
        Определяет уникальные роли в командах, где потеря 1 человека полностью блокирует роль.
        """
        role_counts = self.engineers.groupby(['team_id', 'role'])['engineer_id'].nunique().reset_index()
        role_counts.columns = ['Команда', 'Роль', 'Кол-во сотрудников']
        role_counts['Риск'] = role_counts['Кол-во сотрудников'].apply(
            lambda x: '⚠️ Высокий риск (1 сотрудник)' if x == 1 else 'Низкий'
        )
        return role_counts


# Тестируем расчет Bus Factor
if __name__ == "__main__":
    analytics = StarMapAnalytics("../data/dataset.csv")
    bf_df = analytics.get_bus_factor()

    critical_skills = bf_df[bf_df['Bus Factor'] == 1]
    print(f"\n--- Найдено {len(critical_skills)} критических технологий с Bus Factor = 1 ---")
    print(critical_skills[['Технология / Компетенция', 'Специалисты']].head(10).to_string(index=False))