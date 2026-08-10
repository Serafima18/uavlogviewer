from pymavlink import mavutil
from collections import defaultdict
import csv
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


class LogProcessor:
    def __init__(self, logfile_path):
        self.logfile_path: str = logfile_path
        self.message_data: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.message_descriptions: Dict[str, str] = self._load_message_descriptions()
        self.time_offset: float = 0.0

    def parse_log(self):
        """Парсинг MAVLink-лога и сохранение сообщений во внутреннюю структуру."""
        mav = mavutil.mavlink_connection(self.logfile_path, input='bin')

        # Смещения TimeUS -> GPS собираются из сообщений, где есть оба типа времени.
        offsets = []

        while True:
            msg = mav.recv_match()
            if msg is None:
                break

            msg_type = msg.get_type()
            timestamps = self._get_timestamps(msg)

            # Если сообщение содержит и TimeUS, и GPS-поля, используем его для расчёта смещения.
            if hasattr(msg, 'TimeUS') and hasattr(msg, 'GMS') and hasattr(msg, 'GWk'):
                gps_time = self._gps_to_seconds(msg.GWk, msg.GMS)
                if gps_time > 0:
                    timeus_seconds = msg.TimeUS / 1e6
                    offsets.append(gps_time - timeus_seconds)

            # Храним временные метки отдельно от полезных полей сообщения.
            message_record = {
                'timestamp': timestamps,
                'data': self._message_to_dict(msg),
            }
            self.message_data[msg_type].append(message_record)

        # После парсинга добавляем GPS-время туда, где есть только TimeUS.
        if offsets:
            self.time_offset = float(np.mean(offsets))
            self._timeus_to_gps()

    def _get_timestamps(self, msg):
        """Извлекает доступные временные метки из MAVLink-сообщения."""
        timestamps = {}

        # TimeUS храним в секундах для отображения и отдельно в raw-микросекундах для CSV.
        if hasattr(msg, 'TimeUS'):
            timestamps['TimeUS'] = msg.TimeUS / 1e6
            timestamps['TimeUS_raw'] = int(msg.TimeUS)

        # GPS-время в логе задано через GPS week + milliseconds.
        if hasattr(msg, 'GMS') and hasattr(msg, 'GWk'):
            gps_time = self._gps_to_seconds(msg.GWk, msg.GMS)
            timestamps['GPS'] = gps_time

        return timestamps

    def _gps_to_seconds(self, gps_week, gps_ms):
        """Конвертирует GPS-неделю и миллисекунды в секунды от Unix-эпохи."""
        gps_epoch = 315964800
        seconds_per_week = 604800
        return gps_epoch + (gps_week * seconds_per_week) + (gps_ms / 1000.0)

    def _timeus_to_gps(self):
        """Добавляет GPS-время сообщениям, у которых есть TimeUS, но нет GPS."""
        for messages in self.message_data.values():
            for message in messages:
                ts = message['timestamp']
                if 'TimeUS' in ts and 'GPS' not in ts:
                    ts['GPS'] = ts['TimeUS'] + self.time_offset

    def _message_to_dict(self, msg):
        """Преобразует MAVLink-сообщение в словарь без временных полей."""
        result = {}
        for field in msg.get_fieldnames():
            if field not in ['TimeUS', 'GMS', 'GWk']:
                result[field] = getattr(msg, field, None)
        return result

    def _load_message_descriptions(self):
        """Загружает описания MAVLink-сообщений и полей из utils/mavlink_params.json."""
        descriptions = {}
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base_dir, 'utils', 'mavlink_params.json')
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    descriptions = json.load(f)
            else:
                print(f'Файл описаний не найден: {path}')
        except Exception as e:
            print(f'Не удалось загрузить описания MAVLink: {e}')
        return descriptions

    def get_parameter_data(self, parameter_name, time_type='TimeUS'):
        """
        Возвращает массивы X/Y для параметра вида 'MSG_TYPE.FIELD_NAME'.
        X берётся из выбранного типа времени, иначе первый доступный вариант из TimeUS/GPS.
        """
        try:
            msg_type, field_name = parameter_name.split('.', 1)
            if msg_type not in self.message_data:
                return None, None

            x_data = []
            y_data = []

            for message_record in self.message_data[msg_type]:
                # Значение параметра
                field_value = message_record['data'].get(field_name)
                if field_value is None:
                    continue

                # Временная метка для оси X
                timestamp = message_record['timestamp'].get(time_type)
                if timestamp is None:
                    for alt_time_type in ['TimeUS', 'GPS']:
                        timestamp = message_record['timestamp'].get(alt_time_type)
                        if timestamp is not None:
                            break

                if timestamp is not None:
                    x_data.append(timestamp)
                    y_data.append(field_value)

            if not x_data:
                return None, None

            return np.array(x_data), np.array(y_data)

        except Exception as e:
            print(f'Ошибка получения данных для {parameter_name}: {e}')
            return None, None

    def get_available_parameters(self):
        """Формирует дерево доступных параметров для ParameterTreeWidget."""
        tree = {}
        for msg_type, messages in self.message_data.items():
            if not messages:
                continue

            if msg_type not in tree:
                tree[msg_type] = {
                    'description': self.message_descriptions.get(msg_type, 'Описание недоступно'),
                    'available_timestamps': set(),
                    'fields': {},
                }

            # В дереве показываем только пользовательские типы времени, без технического TimeUS_raw.
            for message in messages:
                visible_keys = {k for k in message['timestamp'].keys() if k != 'TimeUS_raw'}
                tree[msg_type]['available_timestamps'].update(visible_keys)

            # Берём набор полей из первого сообщения данного типа.
            first_message_data = messages[0]['data']
            for field_name in first_message_data.keys():
                has_data = sum(1 for msg in messages if msg['data'].get(field_name) is not None) > 0
                full_name = f'{msg_type}.{field_name}'
                tree[msg_type]['fields'][field_name] = {
                    'full_name': full_name,
                    'has_data': has_data,
                    'description': self.message_descriptions.get(full_name, ''),
                }

        # QTreeWidget удобнее получать список, а не set.
        for msg_type in tree:
            tree[msg_type]['available_timestamps'] = list(tree[msg_type]['available_timestamps'])

        return tree

    def get_time_types_available(self):
        """Возвращает все типы времени, доступные в текущем логе."""
        all_time_types = set()
        for messages in self.message_data.values():
            for message in messages:
                all_time_types.update(k for k in message['timestamp'].keys() if k != 'TimeUS_raw')
        return list(all_time_types)

    def get_message_statistics(self):
        """Возвращает краткую статистику по типам сообщений."""
        stats = {}
        for msg_type, messages in self.message_data.items():
            stats[msg_type] = {
                'available_timestamps': list(
                    set().union(*({k for k in msg['timestamp'].keys() if k != 'TimeUS_raw'} for msg in messages))
                )
            }
        return stats

    def _normalize_time_bounds(
        self,
        start_time: Optional[float],
        end_time: Optional[float],
    ) -> Tuple[Optional[float], Optional[float]]:
        """Нормализует границы диапазона: пустые оставляет пустыми, перепутанные меняет местами."""
        if start_time is None and end_time is None:
            return None, None
        if start_time is None:
            return None, float(end_time)
        if end_time is None:
            return float(start_time), None

        start_value = float(start_time)
        end_value = float(end_time)
        return (start_value, end_value) if start_value <= end_value else (end_value, start_value)

    def _iter_parameter_rows(
        self,
        parameter_name: str,
        start_time: Optional[float],
        end_time: Optional[float],
    ):
        """Собирает строки экспорта для одного параметра в выбранном диапазоне TimeUS."""
        msg_type, field_name = parameter_name.split('.', 1)
        if msg_type not in self.message_data:
            return []

        start_time, end_time = self._normalize_time_bounds(start_time, end_time)
        rows = []

        for message_record in self.message_data[msg_type]:
            ts = message_record['timestamp']
            value = message_record['data'].get(field_name)
            timeus_seconds = ts.get('TimeUS')
            if value is None or timeus_seconds is None:
                continue

            # Фильтрация по выбранному диапазону экспорта.
            if start_time is not None and timeus_seconds < start_time:
                continue
            if end_time is not None and timeus_seconds > end_time:
                continue

            # В CSV TimeUS нужен в исходных микросекундах.
            timeus_export = ts.get('TimeUS_raw')
            if timeus_export is None:
                timeus_export = int(round(float(timeus_seconds) * 1_000_000))

            gps_value = ts.get('GPS', -1)
            if gps_value is None:
                gps_value = -1

            rows.append({
                'timeus_seconds': float(timeus_seconds),
                'TimeUS': int(timeus_export),
                'TimeGPS': gps_value,
                'group': msg_type,
                'name': field_name,
                'value': value,
            })

        rows.sort(key=lambda item: item['TimeUS'])
        return rows

    def get_export_rows(self, parameter_name: str, start_time: Optional[float], end_time: Optional[float]):
        """Возвращает строки экспорта для одного параметра."""
        return self._iter_parameter_rows(parameter_name, start_time, end_time)

    def _sanitize_filename(self, name: str) -> str:
        """Удаляет из имени файла символы, недопустимые в большинстве файловых систем."""
        invalid = '<>:"/\\|?*'
        sanitized = ''.join('_' if ch in invalid else ch for ch in name)
        sanitized = sanitized.strip().strip('.')
        return sanitized or 'export'

    def export_parameters_combined(
        self,
        parameter_names: List[str],
        start_time: Optional[float],
        end_time: Optional[float],
        output_path: str,
        delimiter: str = ';',
    ):
        """Экспортирует все выбранные параметры в один общий CSV-файл."""
        rows = []
        for parameter_name in parameter_names:
            rows.extend(self._iter_parameter_rows(parameter_name, start_time, end_time))

        rows.sort(key=lambda item: item['TimeUS'])
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with output_file.open('w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.writer(csvfile, delimiter=delimiter)
            writer.writerow(['TimeUS', 'TimeGPS', 'ГруппаПараметра', 'ИмяПараметра', 'Значение'])
            for row in rows:
                writer.writerow([
                    row['TimeUS'],
                    row['TimeGPS'],
                    row['group'],
                    row['name'],
                    row['value'],
                ])

        return {
            'path': str(output_file),
            'row_count': len(rows),
            'parameter_count': len(parameter_names),
        }

    def export_parameters_separate(
        self,
        parameter_names: List[str],
        start_time: Optional[float],
        end_time: Optional[float],
        output_directory: str,
        delimiter: str = ';',
    ):
        """Экспортирует каждый выбранный параметр в отдельный CSV-файл."""
        output_dir = Path(output_directory)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        total_rows = 0

        for parameter_name in parameter_names:
            rows = self._iter_parameter_rows(parameter_name, start_time, end_time)
            total_rows += len(rows)

            msg_type, field_name = parameter_name.split('.', 1)
            file_name = self._sanitize_filename(f'{msg_type}_{field_name}') + '.csv'
            file_path = output_dir / file_name

            with file_path.open('w', newline='', encoding='utf-8-sig') as csvfile:
                writer = csv.writer(csvfile, delimiter=delimiter)
                writer.writerow(['TimeUS', 'TimeGPS', 'Значение'])
                for row in rows:
                    writer.writerow([row['TimeUS'], row['TimeGPS'], row['value']])

            results.append({
                'parameter_name': parameter_name,
                'path': str(file_path),
                'row_count': len(rows),
            })

        return {
            'directory': str(output_dir),
            'file_count': len(results),
            'row_count': total_rows,
            'files': results,
        }
