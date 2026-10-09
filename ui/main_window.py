import os
import sys
from datetime import datetime, timezone

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (QAction, QApplication, QComboBox, QDockWidget,
                             QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QMainWindow, QMessageBox, QProgressBar,
                             QPushButton, QStatusBar, QTextEdit, QToolBar,
                             QVBoxLayout, QWidget, QColorDialog)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from log_processor import LogProcessor
from ui.parameter_tree import ParameterTreeWidget
from ui.plot_widget import PlotWidget


class ParseThread(QThread):
    finished = pyqtSignal(bool)
    progress = pyqtSignal(str)

    def __init__(self, processor, file_path):
        super().__init__()
        self.processor = processor
        self.file_path = file_path

    def run(self):
        """Запускает парсинг лога в отдельном потоке, чтобы интерфейс не зависал."""
        self.progress.emit('Парсинг MAVLink лога...')
        try:
            self.processor.parse_log()
            self.finished.emit(True)
        except Exception as e:
            print(f'Ошибка парсинга: {e}')
            self.finished.emit(False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.processor = None
        self.current_plots = {}  # parameter_name: (x_data, y_data, color)
        self.current_time_type = None
        self.last_cursor_time = None
        self._updating_export_fields = False
        self.init_ui()

    def init_ui(self):
        """Инициализация основного окна приложения."""
        self.setWindowTitle('UAVLogViewer')
        self.setGeometry(100, 100, 1600, 900)

        # Центральная область — график занимает основное пространство окна.
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # Виджет графиков.
        self.plot_widget = PlotWidget()
        self.plot_widget.time_type_changed.connect(self.on_time_type_changed)
        self.plot_widget.cursor_position_changed.connect(self.on_cursor_position_changed)
        self.plot_widget.active_plot_changed.connect(self.on_active_plot_changed)
        self.plot_widget.export_range_changed.connect(self.on_export_range_changed)
        self.current_time_type = self.plot_widget.time_type_combo.currentText()
        main_layout.addWidget(self.plot_widget)

        # Графики располагаются в основном окне. Их можно менять в размерах,
        # перемещать за заголовки и выносить отдельно двойным щелчком.
        self.setDockNestingEnabled(True)
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)

        # Боковые/нижние панели.
        self.create_parameter_dock()
        self.create_cursor_info_dock()
        self.create_export_dock()

        # Статусная строка и индикатор загрузки.
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumWidth(200)
        self.status_bar.addPermanentWidget(self.progress_bar)

        # Меню и панель инструментов.
        self.create_menu()
        self.create_toolbar()
        layout_menu = self.menuBar().addMenu('Расположение графиков')
        layout_menu.addAction('Показать все графики', self.plot_widget.show_all_graphs)
        layout_menu.addAction('Восстановить сетку', self.plot_widget.reset_graph_layout)
        layout_menu.addAction('Объединить графики', self.plot_widget.merge_graphs_dialog)
        layout_menu.addAction('Разделить графики', self.plot_widget.split_graphs_dialog)

        self.status_bar.showMessage('Готов к работе. Выберите файл лога для анализа.')

    def create_parameter_dock(self):
        """Создаёт dock-панель с деревом MAVLink-параметров."""
        dock_widget = QDockWidget('Параметры MAVLink', self)
        dock_widget.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        dock_widget.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        self.tree_widget = ParameterTreeWidget()
        self.tree_widget.parameter_toggled.connect(self.on_parameter_toggled)
        self.tree_widget.color_changed.connect(self.on_color_changed)

        dock_widget.setWidget(self.tree_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, dock_widget)

    def create_cursor_info_dock(self):
        """Создаёт нижнюю панель с информацией о точке под курсором."""
        info_dock = QDockWidget('Информация о точке', self)
        info_dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)

        self.cursor_info_text = QTextEdit()
        self.cursor_info_text.setReadOnly(True)
        self.cursor_info_text.setMaximumHeight(170)
        self.cursor_info_text.setStyleSheet('font-family: monospace;')

        info_dock.setWidget(self.cursor_info_text)
        self.addDockWidget(Qt.BottomDockWidgetArea, info_dock)

    def create_export_dock(self):
        """Создаёт панель экспорта CSV и элементы выбора диапазона."""
        self.export_dock = QDockWidget('Экспорт CSV', self)
        self.export_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.export_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        panel = QWidget()
        layout = QVBoxLayout(panel)

        # Короткая подсказка по порядку работы с экспортом.
        hint = QLabel(
            '1) Отметьте нужные графики.\n'
            '2) Либо включите режим выбора диапазона кликами, либо назначьте начало/конец следующим кликом.\n'
            '3) При необходимости отредактируйте границы вручную.\n'
            '4) Выберите каталог и выполните экспорт.'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Основные поля экспорта.
        form = QFormLayout()

        self.export_mode_combo = QComboBox()
        self.export_mode_combo.addItems([
            'Все графики одним файлом',
            'Каждый график в отдельный файл',
        ])
        self.export_mode_combo.currentTextChanged.connect(self.update_export_mode_ui)
        form.addRow('Режим:', self.export_mode_combo)

        self.export_delimiter_edit = QLineEdit(';')
        self.export_delimiter_edit.setMaxLength(1)
        form.addRow('Разделитель:', self.export_delimiter_edit)

        self.export_start_edit = QLineEdit()
        self.export_start_edit.setPlaceholderText('Начало диапазона, сек')
        self.export_start_edit.editingFinished.connect(self.apply_export_range_from_fields)
        form.addRow('Начало TimeUS:', self.export_start_edit)

        self.export_end_edit = QLineEdit()
        self.export_end_edit.setPlaceholderText('Конец диапазона, сек')
        self.export_end_edit.editingFinished.connect(self.apply_export_range_from_fields)
        form.addRow('Конец TimeUS:', self.export_end_edit)

        self.export_directory_edit = QLineEdit()
        form.addRow('Каталог:', self.export_directory_edit)

        self.export_filename_edit = QLineEdit('export')
        form.addRow('Имя файла:', self.export_filename_edit)

        layout.addLayout(form)

        # Выбор папки сохранения.
        browse_row = QHBoxLayout()
        self.export_browse_btn = QPushButton('Выбрать каталог...')
        self.export_browse_btn.clicked.connect(self.choose_export_directory)
        browse_row.addWidget(self.export_browse_btn)
        layout.addLayout(browse_row)

        # Выбор диапазона двумя кликами и очистка границ.
        range_row = QHBoxLayout()
        self.export_select_btn = QPushButton('Выбор диапазона кликами')
        self.export_select_btn.setCheckable(True)
        self.export_select_btn.toggled.connect(self.on_export_selection_toggled)
        range_row.addWidget(self.export_select_btn)

        self.export_clear_btn = QPushButton('Очистить границы')
        self.export_clear_btn.clicked.connect(self.clear_export_range)
        range_row.addWidget(self.export_clear_btn)
        layout.addLayout(range_row)

        # Одноразовый выбор только начала или только конца диапазона.
        click_row = QHBoxLayout()
        self.export_start_pick_btn = QPushButton('Начало: следующий клик')
        self.export_start_pick_btn.setCheckable(True)
        self.export_start_pick_btn.clicked.connect(self.arm_export_start_pick)
        click_row.addWidget(self.export_start_pick_btn)

        self.export_end_pick_btn = QPushButton('Конец: следующий клик')
        self.export_end_pick_btn.setCheckable(True)
        self.export_end_pick_btn.clicked.connect(self.arm_export_end_pick)
        click_row.addWidget(self.export_end_pick_btn)
        layout.addLayout(click_row)

        self.export_info_label = QLabel('Диапазон не выбран')
        self.export_info_label.setWordWrap(True)
        layout.addWidget(self.export_info_label)

        self.export_btn = QPushButton('Выгрузить CSV')
        self.export_btn.clicked.connect(self.export_csv)
        layout.addWidget(self.export_btn)

        layout.addStretch()

        self.export_dock.setWidget(panel)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.export_dock)
        self.export_dock.hide()

        self.update_export_mode_ui()

    def create_menu(self):
        """Создаёт главное меню приложения."""
        menubar = self.menuBar()

        # Меню «Файл».
        file_menu = menubar.addMenu('Файл')

        open_action = QAction('Открыть лог', self)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        export_action = QAction('Экспорт CSV', self)
        export_action.triggered.connect(self.toggle_export_panel)
        file_menu.addAction(export_action)

        clear_action = QAction('Очистить все графики', self)
        clear_action.triggered.connect(self.clear_all_plots)
        file_menu.addAction(clear_action)

        file_menu.addSeparator()

        exit_action = QAction('Выход', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Меню «Вид».
        view_menu = menubar.addMenu('Вид')

        reset_all_action = QAction('Сбросить масштаб всех графиков', self)
        reset_all_action.triggered.connect(self.plot_widget.reset_all_plots)
        view_menu.addAction(reset_all_action)

        reset_active_action = QAction('Сбросить масштаб активного графика', self)
        reset_active_action.triggered.connect(self.plot_widget.reset_active_plot)
        view_menu.addAction(reset_active_action)

        view_menu.addSeparator()

        export_panel_action = QAction('Показать панель экспорта', self)
        export_panel_action.triggered.connect(self.toggle_export_panel)
        view_menu.addAction(export_panel_action)

        change_color_action = QAction('Изменить цвет активного графика', self)
        change_color_action.triggered.connect(self.change_active_plot_color)
        view_menu.addAction(change_color_action)

        # Меню «Справка».
        help_menu = menubar.addMenu('Справка')
        about_action = QAction('О программе', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def create_toolbar(self):
        """Создаёт верхнюю панель инструментов."""
        toolbar = QToolBar('Основные инструменты')
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        open_btn = QPushButton('Открыть лог')
        open_btn.clicked.connect(self.open_file)
        toolbar.addWidget(open_btn)

        toolbar.addSeparator()

        export_btn = QPushButton('Экспорт CSV')
        export_btn.clicked.connect(self.toggle_export_panel)
        toolbar.addWidget(export_btn)

        toolbar.addSeparator()

        clear_btn = QPushButton('Очистить все')
        clear_btn.clicked.connect(self.clear_all_plots)
        toolbar.addWidget(clear_btn)

        toolbar.addSeparator()

        self.active_plot_label = QLabel('Активный график: нет')
        toolbar.addWidget(self.active_plot_label)

        toolbar.addSeparator()

        self.color_btn = QPushButton('Изменить цвет')
        self.color_btn.clicked.connect(self.change_active_plot_color)
        self.color_btn.setEnabled(False)
        toolbar.addWidget(self.color_btn)

    def show_export_panel(self):
        """Показывает панель экспорта и поднимает её поверх остальных dock-панелей."""
        self.export_dock.show()
        self.export_dock.raise_()

    def toggle_export_panel(self):
        """Переключает видимость панели экспорта CSV."""
        if self.export_dock.isVisible():
            self.export_dock.hide()
        else:
            self.show_export_panel()

    def update_export_mode_ui(self):
        """Обновляет доступность поля имени файла в зависимости от режима экспорта."""
        single_file_mode = self.export_mode_combo.currentIndex() == 0
        self.export_filename_edit.setEnabled(single_file_mode)
        if single_file_mode:
            self.export_filename_edit.setPlaceholderText('Имя файла без расширения')
        else:
            self.export_filename_edit.setPlaceholderText('Не используется в этом режиме')

    def choose_export_directory(self):
        """Открывает диалог выбора директории для сохранения CSV."""
        directory = QFileDialog.getExistingDirectory(
            self,
            'Выберите директорию для сохранения CSV',
            self.export_directory_edit.text().strip() or os.getcwd(),
        )
        if directory:
            self.export_directory_edit.setText(directory)

    def _parse_optional_float(self, text: str):
        """Преобразует поле диапазона в float; пустое поле считается незаданной границей."""
        cleaned = text.strip().replace(',', '.')
        if not cleaned:
            return None
        return float(cleaned)

    def apply_export_range_from_fields(self):
        """Применяет диапазон экспорта, введённый вручную в текстовые поля."""
        if self._updating_export_fields:
            return

        try:
            start_value = self._parse_optional_float(self.export_start_edit.text())
            end_value = self._parse_optional_float(self.export_end_edit.text())
        except ValueError:
            QMessageBox.warning(self, 'Ошибка', 'Границы диапазона должны быть числами')
            self.on_export_range_changed(*self.plot_widget.get_export_range())
            return

        self.plot_widget.set_export_range(start_value, end_value)

    def on_export_range_changed(self, start_time, end_time):
        """Синхронизирует текстовые поля и подпись при изменении диапазона на графике."""
        self._updating_export_fields = True
        try:
            self.export_start_edit.setText('' if start_time is None else f'{start_time:.6f}')
            self.export_end_edit.setText('' if end_time is None else f'{end_time:.6f}')
        finally:
            self._updating_export_fields = False

        # Если одноразовый выбор кликом завершился, кнопки нужно визуально отжать.
        if getattr(self, 'export_start_pick_btn', None) is not None or getattr(self, 'export_end_pick_btn', None) is not None:
            if self.plot_widget.export_selection_target is None:
                self._set_single_pick_buttons(False, False)

        if start_time is None and end_time is None:
            self.export_info_label.setText('Диапазон не выбран')
        elif start_time is None:
            self.export_info_label.setText(f'Выбран только конец: {end_time:.6f} сек')
        elif end_time is None:
            self.export_info_label.setText(f'Выбрано только начало: {start_time:.6f} сек')
        else:
            self.export_info_label.setText(
                f'Диапазон: {start_time:.6f} .. {end_time:.6f} сек '
                f'(длительность {end_time - start_time:.6f} сек)'
            )

    def _set_single_pick_buttons(self, start_checked: bool, end_checked: bool):
        """Без лишних сигналов выставляет состояние кнопок одноразового выбора границы."""
        if hasattr(self, 'export_start_pick_btn'):
            self.export_start_pick_btn.blockSignals(True)
            self.export_start_pick_btn.setChecked(bool(start_checked))
            self.export_start_pick_btn.blockSignals(False)
        if hasattr(self, 'export_end_pick_btn'):
            self.export_end_pick_btn.blockSignals(True)
            self.export_end_pick_btn.setChecked(bool(end_checked))
            self.export_end_pick_btn.blockSignals(False)

    def on_export_selection_toggled(self, enabled):
        """Включает/выключает режим выбора экспортного диапазона кликами по графику."""
        if enabled and not self.current_plots:
            QMessageBox.information(self, 'Информация', 'Сначала отобразите хотя бы один график')
            self.export_select_btn.blockSignals(True)
            self.export_select_btn.setChecked(False)
            self.export_select_btn.blockSignals(False)
            return

        if enabled:
            self._set_single_pick_buttons(False, False)

        self.plot_widget.set_export_selection_enabled(enabled)
        if enabled:
            self.status_bar.showMessage('Режим выбора диапазона включён: поставьте начало и конец кликами по графику')
        else:
            self.status_bar.showMessage('Режим выбора диапазона выключен')

    def clear_export_range(self):
        """Очищает выбранные границы экспортного диапазона."""
        self._set_single_pick_buttons(False, False)
        self.plot_widget.clear_export_selection()

    def arm_export_boundary_pick(self, boundary_name):
        """Готовит одноразовый выбор начала или конца диапазона следующим кликом."""
        if not self.current_plots:
            QMessageBox.information(self, 'Информация', 'Сначала отобразите хотя бы один график')
            self._set_single_pick_buttons(False, False)
            return

        # Одноразовый выбор границы не должен конфликтовать с режимом выбора диапазона.
        self.export_select_btn.blockSignals(True)
        self.export_select_btn.setChecked(False)
        self.export_select_btn.blockSignals(False)
        self.plot_widget.set_export_selection_enabled(False)
        self.plot_widget.set_next_export_boundary(boundary_name)

        self._set_single_pick_buttons(boundary_name == 'start', boundary_name == 'end')
        label = 'начало' if boundary_name == 'start' else 'конец'
        self.status_bar.showMessage(f'Выберите {label} диапазона одним следующим кликом по графику')

    def arm_export_start_pick(self, checked=False):
        """Обработчик кнопки одноразового выбора начала диапазона."""
        if checked:
            self.arm_export_boundary_pick('start')
        else:
            if self.plot_widget.export_selection_target == 'start':
                self.plot_widget.set_next_export_boundary(None)
            self._set_single_pick_buttons(False, self.export_end_pick_btn.isChecked())
            self.status_bar.showMessage('Выбор начала следующим кликом отменён')

    def arm_export_end_pick(self, checked=False):
        """Обработчик кнопки одноразового выбора конца диапазона."""
        if checked:
            self.arm_export_boundary_pick('end')
        else:
            if self.plot_widget.export_selection_target == 'end':
                self.plot_widget.set_next_export_boundary(None)
            self._set_single_pick_buttons(self.export_start_pick_btn.isChecked(), False)
            self.status_bar.showMessage('Выбор конца следующим кликом отменён')

    def open_file(self):
        """Открывает диалог выбора MAVLink-лога."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            'Выберите MAVLink лог',
            '',
            'MAVLink логи (*.tlog *.bin *.log);;Все файлы (*)',
        )
        if file_path:
            self.load_file(file_path)

    def load_file(self, file_path):
        """Создаёт LogProcessor и запускает парсинг выбранного файла."""
        self.status_bar.showMessage(f'Загрузка файла: {os.path.basename(file_path)}')
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        # Перед загрузкой нового лога убираем старые графики и выбранные параметры.
        self.clear_all_plots()
        self.processor = LogProcessor(file_path)

        # Парсинг выполняется в отдельном потоке.
        self.parse_thread = ParseThread(self.processor, file_path)
        self.parse_thread.finished.connect(self.on_parse_finished)
        self.parse_thread.progress.connect(self.status_bar.showMessage)
        self.parse_thread.start()

    def on_parse_finished(self, success):
        """Обрабатывает результат парсинга и обновляет интерфейс."""
        self.progress_bar.setVisible(False)

        if success:
            self.status_bar.showMessage('Парсинг завершен успешно')

            # Дерево параметров строится по реально найденным сообщениям и полям.
            parameters_tree = self.processor.get_available_parameters()
            self.tree_widget.update_tree(parameters_tree)

            # Обновляем список доступных временных шкал.
            time_types = self.processor.get_time_types_available()
            current_time_type = self.plot_widget.time_type_combo.currentText()
            self.plot_widget.time_type_combo.clear()
            self.plot_widget.time_type_combo.addItems(time_types)
            self.plot_widget.set_time_offset(getattr(self.processor, 'time_offset', 0.0))

            # По возможности сохраняем предыдущий выбранный тип времени.
            if current_time_type in time_types:
                self.plot_widget.time_type_combo.setCurrentText(current_time_type)
            elif time_types:
                self.plot_widget.time_type_combo.setCurrentText(time_types[0])

        else:
            QMessageBox.critical(
                self,
                'Ошибка',
                'Не удалось распарсить файл. Убедитесь, что это корректный MAVLink лог.',
            )
            self.status_bar.showMessage('Ошибка парсинга файла')

    def on_parameter_toggled(self, parameter_name, is_checked, color):
        """Добавляет или удаляет график при переключении чекбокса в дереве."""
        if is_checked:
            if not self.add_plot(parameter_name, color):
                # не удалось добавить – снимаем галочку
                checkbox = self.tree_widget.parameter_checkboxes.get(parameter_name)
                if checkbox:
                    checkbox.blockSignals(True)
                    checkbox.setChecked(False)
                    checkbox.blockSignals(False)
        else:
            self.remove_plot(parameter_name)

    def on_color_changed(self, parameter_name, color):
        """Синхронизирует цвет графика после изменения цвета в дереве параметров."""
        if parameter_name in self.current_plots:
            self.plot_widget.update_plot_color(parameter_name, color)
            x_data, y_data, _ = self.current_plots[parameter_name]
            self.current_plots[parameter_name] = (x_data, y_data, color)

    def add_plot(self, parameter_name, color):
        """Получает данные параметра из LogProcessor и добавляет их на график."""
        if self.processor is None:
            QMessageBox.warning(self, 'Предупреждение', 'Сначала откройте файл лога')
            return False

        x_data, y_data = self.processor.get_parameter_data(parameter_name, 'TimeUS')

        if x_data is not None and y_data is not None and len(x_data) > 0:
            success = self.plot_widget.add_plot(parameter_name, x_data, y_data, color)
            if success:
                self.current_plots[parameter_name] = (x_data, y_data, color)
                self.plot_widget.set_active_plot(parameter_name)
                self.status_bar.showMessage(f'Добавлен график: {parameter_name}')
            return success
        else:
            # Если данных нет, объясняем пользователю, какие временные шкалы доступны.
            msg_type = parameter_name.split('.')[0]
            stats = self.processor.get_message_statistics()
            if msg_type in stats:
                available_times = stats[msg_type]['available_timestamps']
                message = (
                    f"Для параметра {parameter_name} нет данных с временем 'TimeUS'. "
                    f"Доступные типы времени: {', '.join(available_times)}"
                )
            else:
                message = f"Для параметра {parameter_name} нет данных с временем 'TimeUS'"

            QMessageBox.warning(self, 'Данные недоступны', message)
            if parameter_name in self.tree_widget.parameter_checkboxes:
                self.tree_widget.parameter_checkboxes[parameter_name].setChecked(False)
            return False

    def remove_plot(self, parameter_name):
        """Удаляет график из PlotWidget и из списка текущих графиков MainWindow."""
        self.plot_widget.remove_plot(parameter_name)
        if parameter_name in self.current_plots:
            del self.current_plots[parameter_name]
            self.status_bar.showMessage(f'Удален график: {parameter_name}')

    def clear_all_plots(self):
        """Очищает все графики, выбранные чекбоксы и состояние выбора диапазона."""
        self.plot_widget.clear_all_plots()
        self.current_plots.clear()

        # Снимаем чекбоксы без лишних повторных сигналов.
        for checkbox in self.tree_widget.parameter_checkboxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)

        self.active_plot_label.setText('Активный график: нет')
        self.color_btn.setEnabled(False)
        self.last_cursor_time = None

        if hasattr(self, 'export_select_btn'):
            self.export_select_btn.blockSignals(True)
            self.export_select_btn.setChecked(False)
            self.export_select_btn.blockSignals(False)

        self._set_single_pick_buttons(False, False)

        self.status_bar.showMessage('Все графики очищены')

    def on_time_type_changed(self, time_type):
        """Обрабатывает смену типа времени на панели графиков."""
        if self.processor is None or not self.current_plots:
            return

        self.current_time_type = time_type
        self.plot_widget.update_gps_date_label()

    def on_cursor_position_changed(self, x_pos, parameter_values):
        """Обновляет нижнюю панель информации при движении курсора по графику."""
        self.last_cursor_time = x_pos

        if not parameter_values:
            self.cursor_info_text.setText('Нет данных для отображения')
            return

        offset = self.processor.time_offset if self.processor else 0.0

        def to_dt(ts):
            """Конвертирует TimeUS-секунды в GPS datetime с учётом найденного смещения."""
            try:
                return datetime.fromtimestamp(ts + offset, tz=timezone.utc)
            except Exception:
                return None

        dt_gps = to_dt(x_pos)
        info_lines = [f'TimeUS: {x_pos:.3f} сек']

        if dt_gps:
            info_lines.append(f'Дата GPS:  {dt_gps.strftime("%Y-%m-%d")}')
            info_lines.append(f'Время GPS: {dt_gps.strftime("%H:%M:%S")}')
        else:
            info_lines.append('GPS: недоступно')

        # Если выбран экспортный диапазон, показываем его в той же информационной панели.
        export_start, export_end = self.plot_widget.get_export_range()
        if export_start is not None or export_end is not None:
            info_lines.append('')
            info_lines.append('Экспортный диапазон:')
            info_lines.append(f'  Начало: {"не задано" if export_start is None else f"{export_start:.3f} сек"}')
            info_lines.append(f'  Конец:  {"не задано" if export_end is None else f"{export_end:.3f} сек"}')

        info_lines.append('')
        active_plot = self.plot_widget.active_plot

        # Сначала отдельно показываем активный график.
        if active_plot and active_plot in parameter_values:
            active_data = parameter_values[active_plot]
            dt_gps_point = to_dt(active_data['x'])

            info_lines.append(f'► Активный график ({active_plot}):')
            info_lines.append(f'   Значение: {active_data["y"]:.6f}')
            info_lines.append(f'   TimeUS:   {active_data["x"]:.3f} сек')
            if dt_gps_point:
                info_lines.append(f'   GPS:      {dt_gps_point.strftime("%H:%M:%S")}')
            info_lines.append('')

        # Затем список значений всех отображаемых графиков.
        info_lines.append('Все графики:')
        for param_name, data in parameter_values.items():
            marker = '► ' if param_name == active_plot else '  '
            info_lines.append(
                f'{marker}{param_name}: {data["y"]:.6f} '
                f'(TimeUS: {data["x"]:.3f} сек)'
            )

        self.cursor_info_text.setText('\n'.join(info_lines))

    def on_active_plot_changed(self, parameter_name):
        """Обновляет элементы интерфейса при смене активного графика."""
        self.active_plot_label.setText(f'Активный график: {parameter_name}')
        self.color_btn.setEnabled(True)

        # В статусной строке показываем доступные временные шкалы для типа сообщения.
        if self.processor:
            msg_type = parameter_name.split('.')[0]
            stats = self.processor.get_message_statistics()
            if msg_type in stats:
                available_times = stats[msg_type]['available_timestamps']
                self.status_bar.showMessage(
                    f'Активный: {parameter_name}. Доступное время: {", ".join(available_times)}'
                )

    def change_active_plot_color(self):
        """Открывает диалог выбора цвета для активного графика."""
        if not self.plot_widget.active_plot:
            return

        current_color = self.plot_widget.plot_colors.get(self.plot_widget.active_plot, '#1f77b4')
        color = QColorDialog.getColor(QColor(current_color))

        if color.isValid():
            new_color = color.name()

            # Обновляем цвет линии на графике.
            self.plot_widget.update_plot_color(self.plot_widget.active_plot, new_color)

            # Синхронизируем кнопку цвета в дереве параметров.
            if self.plot_widget.active_plot in self.tree_widget.parameter_colors:
                self.tree_widget.set_parameter_color(self.plot_widget.active_plot, new_color)

            # Синхронизируем цвет в списке текущих графиков.
            if self.plot_widget.active_plot in self.current_plots:
                x_data, y_data, _ = self.current_plots[self.plot_widget.active_plot]
                self.current_plots[self.plot_widget.active_plot] = (x_data, y_data, new_color)

            self.status_bar.showMessage(f"Цвет графика '{self.plot_widget.active_plot}' изменен")

    def _get_selected_parameter_names(self):
        """Возвращает имена всех параметров, которые сейчас отображаются на графике."""
        return list(self.current_plots.keys())

    def export_csv(self):
        """Выполняет экспорт выбранных графиков в CSV по выбранному диапазону TimeUS."""
        if self.processor is None:
            QMessageBox.information(self, 'Информация', 'Сначала откройте лог')
            return

        parameter_names = self._get_selected_parameter_names()
        if not parameter_names:
            QMessageBox.information(self, 'Информация', 'Нет отображаемых графиков для экспорта')
            return

        start_time, end_time = self.plot_widget.get_export_range()
        if start_time is None or end_time is None:
            QMessageBox.information(
                self,
                'Информация',
                'Нужно задать и начало, и конец диапазона TimeUS',
            )
            return

        delimiter = self.export_delimiter_edit.text() or ';'
        if len(delimiter) != 1:
            QMessageBox.warning(self, 'Ошибка', 'Разделитель должен состоять из одного символа')
            return

        output_directory = self.export_directory_edit.text().strip()
        if not output_directory:
            QMessageBox.information(self, 'Информация', 'Выберите директорию сохранения')
            return

        if not os.path.isdir(output_directory):
            QMessageBox.warning(self, 'Ошибка', 'Указанная директория не существует')
            return

        try:
            if self.export_mode_combo.currentIndex() == 0:
                # Режим: все выбранные параметры в одном CSV-файле.
                file_name = self.export_filename_edit.text().strip() or 'export'
                if not file_name.lower().endswith('.csv'):
                    file_name += '.csv'
                output_path = os.path.join(output_directory, file_name)
                result = self.processor.export_parameters_combined(
                    parameter_names,
                    start_time,
                    end_time,
                    output_path,
                    delimiter,
                )
                QMessageBox.information(
                    self,
                    'Экспорт завершен',
                    f'Файл сохранён:\n{result["path"]}\n\nСтрок: {result["row_count"]}',
                )
                self.status_bar.showMessage(f'Экспорт завершён: {result["path"]}')
            else:
                # Режим: каждый параметр в отдельный CSV-файл.
                result = self.processor.export_parameters_separate(
                    parameter_names,
                    start_time,
                    end_time,
                    output_directory,
                    delimiter,
                )
                QMessageBox.information(
                    self,
                    'Экспорт завершен',
                    f'Файлы сохранены в:\n{result["directory"]}\n\n'
                    f'Файлов: {result["file_count"]}\nСтрок суммарно: {result["row_count"]}',
                )
                self.status_bar.showMessage(f'Экспорт завершён в каталог: {result["directory"]}')
        except Exception as e:
            QMessageBox.critical(self, 'Ошибка экспорта', f'Не удалось выполнить экспорт:\n{e}')

    def show_about(self):
        """Показывает окно «О программе» с краткой справкой по управлению."""
        about_text = """
        <h2>MAVLink Log Analyzer</h2>
        <p>Приложение для анализа логов MAVLink</p>
        <p><b>Управление:</b></p>
        <ul>
            <li><b>Клик по графику</b> — выбор активного графика</li>
            <li><b>Двойной клик</b> — сброс масштаба активного графика</li>
            <li><b>Перетаскивание</b> — перемещение по оси X</li>
            <li><b>Shift + перетаскивание</b> — перемещение по оси Y</li>
            <li><b>Колесо мыши</b> — масштабирование по оси X</li>
            <li><b>Колесо мыши + Shift</b> — масштабирование по оси Y</li>
            <li><b>Панель «Экспорт CSV»</b> — выбор диапазона и выгрузка отображаемых графиков</li>
        </ul>
        """
        QMessageBox.about(self, 'О программе', about_text)

    def closeEvent(self, event):
        """Останавливает поток парсинга при закрытии окна."""
        if hasattr(self, 'parse_thread') and self.parse_thread.isRunning():
            self.parse_thread.terminate()
            self.parse_thread.wait()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.WindowText, QColor(0, 0, 0))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
