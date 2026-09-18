import pandas as pd


class DataParser:
    def __init__(self, file_path: str):
        self.file_path = file_path
        # Читаем Excel как единую сетку без заголовков (header=None), 
        # чтобы избежать сдвигов колонок при парсинге сложных листов
        self.raw_df = pd.read_excel(file_path, header=None, engine='openpyxl')

    def _extract_section(self, header_marker: str) -> pd.DataFrame:
        start_idx = -1

        # 1. Ищем номер строки, в которой находится маркер шапки
        for idx, row in self.raw_df.iterrows():
            row_str = row.dropna().astype(str)
            if any(header_marker in val for val in row_str):
                start_idx = idx
                break

        if start_idx == -1:
            raise ValueError(f"Секция с заголовком '{header_marker}' не найдена в Excel!")

        # 2. Ищем конец таблицы (первая полностью пустая строка после старта)
        end_idx = len(self.raw_df)
        for idx in range(start_idx + 1, len(self.raw_df)):
            if self.raw_df.iloc[idx].dropna().empty:
                end_idx = idx
                break

        # 3. Вырезаем кусок сетки (только данные)
        df_section = self.raw_df.iloc[start_idx + 1: end_idx].copy()

        # 4. Назначаем заголовки из строки start_idx
        headers = self.raw_df.iloc[start_idx].fillna(f'Unnamed_{start_idx}').astype(str)
        df_section.columns = headers

        # 5. Очищаем от полностью пустых строк и столбцов
        df_section = df_section.dropna(how='all', axis=0).dropna(how='all', axis=1)

        return df_section

    def get_tasks(self) -> pd.DataFrame:
        # Теперь парсер автоматически забирает planned_start/end, actual_start/end и estimated_hh
        return self._extract_section('Номер инициативы')

    def get_estimates(self) -> pd.DataFrame:
        return self._extract_section('Роль')

    def get_dependencies(self) -> pd.DataFrame:
        # ТОТ САМЫЙ ФИКС ИЗ PDF (стр. 10): 
        # Игнорируем реальные имена шапки, режем строго по позиции столбцов
        df = self._extract_section('blocking_task_id')

        clean_df = pd.DataFrame()
        # Берем первые 2 столбца как блокер и блокируемого
        clean_df['blocking_task_id'] = df.iloc[:, 0].astype(str).str.strip()
        clean_df['blocked_task_id'] = df.iloc[:, 1].astype(str).str.strip()

        # Если есть 3-й столбец — это тип связи, иначе ставим по умолчанию
        if df.shape[1] >= 3:
            clean_df['dependency_type'] = df.iloc[:, 2].astype(str).str.strip()
        else:
            clean_df['dependency_type'] = 'depends on'

        # Очищаем мусор и пустые связи
        clean_df = clean_df.dropna(subset=['blocking_task_id', 'blocked_task_id'])
        clean_df = clean_df[~clean_df['blocking_task_id'].isin(['nan', 'None', ''])]
        clean_df = clean_df[~clean_df['blocked_task_id'].isin(['nan', 'None', ''])]

        return clean_df

    def get_engineers(self) -> pd.DataFrame:
        return self._extract_section('engineer_id')

    def get_team_history(self) -> pd.DataFrame:
        # Автоматически забирает planned_sp
        return self._extract_section('snapshot_date')


if __name__ == "__main__":
    # Тест парсера
    # Важно: укажи правильное расширение .xlsx!
    parser = DataParser("../data/dataset.xlsx")

    deps = parser.get_dependencies()
    print(f"✅ Зависимостей найдено: {len(deps)}")
    print(deps.head())

    tasks = parser.get_tasks()
    print(f"✅ Задач найдено: {len(tasks)}")
    if 'planned_start' in tasks.columns:
        print("✅ Поля для календаря (planned_start/end) успешно загружены!")