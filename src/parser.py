import pandas as pd
import io


class DataParser:
    def __init__(self, file_path: str):
        self.file_path = file_path
        with open(file_path, 'r', encoding='utf-8') as f:
            self.raw_data = f.read()

    def _extract_section(self, start_marker: str, end_marker: str = None) -> pd.DataFrame:
        """
        Вытаскивает нужную таблицу из склеенного CSV файла.
        """
        lines = self.raw_data.split('\n')
        start_idx = -1
        end_idx = len(lines)

        # Ищем начало секции
        for i, line in enumerate(lines):
            if start_marker in line:
                start_idx = i + 1  # Таблица начинается со следующей строки (заголовки)
                break

        if start_idx == -1:
            raise ValueError(f"Секция {start_marker} не найдена!")

        # Ищем конец секции (если есть маркер окончания или пустая строка/разделитель)
        for i in range(start_idx, len(lines)):
            # В датасете пустые секции разделены кучей точек с запятой ;;;;;
            if end_marker and end_marker in lines[i]:
                end_idx = i
                break
            elif ';;;;;;;;;;;;;;;;;;' in lines[i]:
                end_idx = i
                break

        # Собираем строки таблицы и читаем через pandas
        csv_str = '\n'.join(lines[start_idx:end_idx])
        df = pd.read_csv(io.StringIO(csv_str), sep=';')

        # Удаляем полностью пустые столбцы (возникают из-за лишних ; в конце строк)
        df = df.dropna(axis=1, how='all')
        return df

    def get_tasks(self) -> pd.DataFrame:
        # Убираем первую строку "Номер инициативы;task_id...", парсим со второй,
        # где реальные заголовки. Либо адаптируем маркер.
        return self._extract_section('Номер инициативы;task_id;')

    def get_estimates(self) -> pd.DataFrame:
        return self._extract_section('Estimates. Калькулятор сметы')

    def get_dependencies(self) -> pd.DataFrame:
        return self._extract_section('Task_dependencies (Связи зависимостей)')

    def get_engineers(self) -> pd.DataFrame:
        return self._extract_section('Engineers_profiles')

    def get_team_history(self) -> pd.DataFrame:
        return self._extract_section('Team_history (История емкости спринтов)')


# Тестируем парсер (запусти этот файл локально)
if __name__ == "__main__":
    # Укажи правильный путь до скачанного файла
    parser = DataParser("../data/dataset.csv")

    tasks_df = parser.get_tasks()
    engineers_df = parser.get_engineers()
    deps_df = parser.get_dependencies()

    print("Задачи загружены:", len(tasks_df))
    print("Инженеры загружены:", len(engineers_df))
    print("Зависимости загружены:", len(deps_df))
    print(engineers_df.head(2))