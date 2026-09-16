import pandas as pd
import io


class DataParser:
    def __init__(self, file_path: str):
        self.file_path = file_path
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            raw = f.read()

        # Лечим опечатку в датасете: заменяем двойную точку с запятой в заголовке на одинарную
        self.raw_data = raw.replace('blocking_task_id;;blocked_task_id', 'blocking_task_id;blocked_task_id')

    def _extract_section(self, header_marker: str) -> pd.DataFrame:
        lines = self.raw_data.split('\n')
        start_idx = -1
        end_idx = len(lines)

        # 1. Ищем строку с нужными заголовками
        for i, line in enumerate(lines):
            if line.startswith(header_marker):
                start_idx = i
                break

        if start_idx == -1:
            raise ValueError(f"Секция с заголовком '{header_marker}' не найдена!")

        # 2. Ищем конец секции
        for i in range(start_idx + 1, len(lines)):
            if lines[i].strip() == '' or lines[i].strip().startswith(';;;'):
                end_idx = i
                break

        # 3. Собираем и читаем через pandas
        csv_str = '\n'.join(lines[start_idx:end_idx])
        df = pd.read_csv(io.StringIO(csv_str), sep=';')

        # 4. Удаляем мусорные пустые колонки справа
        df = df.dropna(axis=1, how='all')
        return df

    def get_tasks(self) -> pd.DataFrame:
        return self._extract_section('Номер инициативы;')

    def get_estimates(self) -> pd.DataFrame:
        return self._extract_section('Роль;')

    def get_dependencies(self) -> pd.DataFrame:
        df = self._extract_section('blocking_task_id')
        clean_df = pd.DataFrame()

        # Проверяем, сколько колонок реально прочиталось
        num_cols = df.shape[1]

        if num_cols >= 2:
            clean_df['blocking_task_id'] = df.iloc[:, 0].astype(str).str.strip()
            clean_df['blocked_task_id'] = df.iloc[:, 1].astype(str).str.strip()
        else:
            return pd.DataFrame(columns=['blocking_task_id', 'blocked_task_id', 'dependency_type'])

        # Если есть 3-я колонка с типом связи — берем её, иначе ставим 'depends on'
        if num_cols >= 3:
            clean_df['dependency_type'] = df.iloc[:, 2].astype(str).str.strip()
        else:
            clean_df['dependency_type'] = 'depends on'

        # Очищаем строки от мусора и пустых значений
        clean_df = clean_df.dropna(subset=['blocking_task_id', 'blocked_task_id'])
        clean_df = clean_df[~clean_df['blocking_task_id'].isin(['nan', '', 'None'])]
        clean_df = clean_df[~clean_df['blocked_task_id'].isin(['nan', '', 'None'])]

        return clean_df

    def get_engineers(self) -> pd.DataFrame:
        return self._extract_section('engineer_id;')

    def get_team_history(self) -> pd.DataFrame:
        return self._extract_section('snapshot_date;')


if __name__ == "__main__":
    parser = DataParser("../data/dataset.csv")
    print("Зависимости исправлены:", parser.get_dependencies().columns.tolist())