from PyQt5.QtWidgets import (QTreeWidget, QTreeWidgetItem, QHeaderView,
                             QCheckBox, QHBoxLayout, QWidget,
                             QColorDialog, QPushButton)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont


class ParameterTreeWidget(QTreeWidget):
    parameter_toggled = pyqtSignal(str, bool, str)  # parameter_name, is_checked, color
    color_changed = pyqtSignal(str, str)            # parameter_name, color

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["Параметр", "Цвет"])
        self.setColumnCount(2)
        header = self.header()

        # Последняя колонка не растягивается: она нужна только под кнопку цвета.
        header.setStretchLastSection(False)

        # Левая колонка занимает всё доступное место.
        header.setSectionResizeMode(0, QHeaderView.Stretch)

        # Правая колонка фиксированная, чтобы кнопки цвета всегда были ровными.
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.setColumnWidth(1, 44)        # под кнопку 20x20 + отступы
        header.setMinimumSectionSize(40)  # чтобы нельзя было ужать колонку в ноль

        # Не даём пользователю случайно менять ширину колонки цвета.
        header.setSectionsClickable(True)
        header.setSectionResizeMode(1, QHeaderView.Fixed)

        self.parameter_checkboxes = {}      # parameter_name: QCheckBox
        self.parameter_color_buttons = {}   # parameter_name: QPushButton
        self.parameter_colors = {}          # parameter_name: color

    def update_tree(self, parameters_tree):
        """Обновляет дерево параметров после загрузки/парсинга лога."""
        self.clear()
        self.parameter_checkboxes.clear()
        self.parameter_color_buttons.clear()
        self.parameter_colors.clear()

        # Предопределённая палитра цветов для новых параметров.
        default_colors = [
            '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
            '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
        ]
        color_index = 0

        for msg_type, msg_info in parameters_tree.items():
            # Верхний уровень дерева — тип MAVLink-сообщения.
            msg_item = QTreeWidgetItem(self)
            msg_item.setText(0, msg_type)
            msg_item.setToolTip(0, f"{msg_info['description']}")

            # Заголовок группы выделяем жирным.
            font = QFont()
            font.setBold(True)
            msg_item.setFont(0, font)

            for field_name, field_info in msg_info['fields'].items():
                if not field_info['has_data']:
                    continue  # поля без данных не показываем

                # Второй уровень дерева — конкретное поле внутри сообщения.
                field_item = QTreeWidgetItem(msg_item)
                field_item.setText(0, field_name)

                parameter_name = field_info['full_name']
                default_color = default_colors[color_index % len(default_colors)]
                self.parameter_colors[parameter_name] = default_color
                color_index += 1

                # Чекбокс выбора параметра для отображения на графике.
                checkbox_widget = QWidget()
                checkbox_layout = QHBoxLayout(checkbox_widget)
                checkbox_layout.setContentsMargins(2, 2, 2, 2)

                checkbox = QCheckBox()
                checkbox.stateChanged.connect(
                    lambda state, p=parameter_name:
                    self._on_parameter_toggled(p, state == Qt.Checked)
                )

                checkbox_layout.addWidget(checkbox)
                checkbox_layout.setAlignment(checkbox, Qt.AlignCenter)

                # Tooltip с описанием поля, если описание есть в базе MAVLink-параметров.
                desc = field_info.get('description', '')
                if desc:
                    field_item.setToolTip(0, desc)
                    checkbox_widget.setToolTip(desc)

                # Кнопка выбора цвета для конкретного параметра.
                color_button = QPushButton()
                color_button.setFixedSize(20, 20)
                color_button.setStyleSheet(f"background-color: {default_color}; border: 1px solid #666;")
                color_button.clicked.connect(
                    lambda checked=False, p=parameter_name:
                    self._choose_color(p)
                )

                self.setItemWidget(field_item, 0, checkbox_widget)
                self.setItemWidget(field_item, 1, color_button)

                self.parameter_checkboxes[parameter_name] = checkbox
                self.parameter_color_buttons[parameter_name] = color_button

    def _on_parameter_toggled(self, parameter_name, is_checked):
        """Сообщает MainWindow, что пользователь включил/выключил параметр."""
        color = self.parameter_colors[parameter_name]
        self.parameter_toggled.emit(parameter_name, is_checked, color)

    def _choose_color(self, parameter_name):
        """Открывает диалог выбора цвета для параметра."""
        current_color = QColor(self.parameter_colors[parameter_name])
        color = QColorDialog.getColor(current_color)
        if color.isValid():
            self.set_parameter_color(parameter_name, color.name())
            self.color_changed.emit(parameter_name, color.name())

    def set_parameter_color(self, parameter_name, color):
        """Сохраняет цвет параметра и обновляет соответствующую кнопку."""
        if parameter_name in self.parameter_color_buttons:
            self.parameter_colors[parameter_name] = color
            button = self.parameter_color_buttons[parameter_name]
            button.setStyleSheet(f"background-color: {color}; border: 1px solid #666;")

    def get_parameter_color(self, parameter_name):
        """Возвращает сохранённый цвет параметра или цвет по умолчанию."""
        return self.parameter_colors.get(parameter_name, '#1f77b4')
