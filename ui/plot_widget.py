from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QPushButton, QLabel, QComboBox, QLineEdit, QMessageBox,
                             QMainWindow, QDockWidget, QMenu, QAction, QInputDialog,
                             QSplitter, QFrame, QDialog, QApplication, QListWidget, QListWidgetItem, QDialogButtonBox, QAbstractItemView)
from PyQt5.QtCore import QPointF, Qt, QTimer, pyqtSignal, QMimeData
from datetime import datetime, timezone
import pyqtgraph as pg
import numpy as np
from PyQt5.QtGui import QDrag


# Вспомогательные оси и ViewBox


def _format_number(value: float) -> str:
    """Форматирует числа для подписей осей."""
    if value is None or not np.isfinite(value):
        return ''
    abs_value = abs(value)
    if abs_value >= 1e6 or (0 < abs_value < 1e-3):
        return f'{value:.3e}'
    if abs_value >= 1000:
        return f'{value:.1f}'
    if abs_value >= 1:
        return f'{value:.3f}'.rstrip('0').rstrip('.')
    return f'{value:.6f}'.rstrip('0').rstrip('.')


class TimeAxis(pg.AxisItem):
    """Ось времени: TimeUS отображается как число, GPS — как HH:MM:SS."""

    def __init__(self, get_time_type, get_time_offset, has_custom_x_transform, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._get_time_type = get_time_type
        self._get_time_offset = get_time_offset
        self._has_custom_x_transform = has_custom_x_transform

    def tickStrings(self, values, scale, spacing):
        """Возвращает подписи делений оси."""
        if self._get_time_type() == 'GPS' and not self._has_custom_x_transform():
            labels = []
            for value in values:
                try:
                    dt = datetime.fromtimestamp(value, tz=timezone.utc)
                    labels.append(dt.strftime('%H:%M:%S'))
                except Exception:
                    labels.append('')
            return labels
        return super().tickStrings(values, scale, spacing)


class ParameterAxis(pg.AxisItem):
    """Левая ось ячейки, показывающая raw-Y после обратного пересчёта из display-Y."""

    def __init__(self, parameter_name, get_y_affine, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parameter_name = parameter_name
        self._get_y_affine = get_y_affine

    def tickStrings(self, values, scale, spacing):
        """Показывает исходные значения параметра, а не изменённые display-значения."""
        a, b = self._get_y_affine(self.parameter_name)
        if abs(a) < 1e-12:
            return ['' for _ in values]
        return [_format_number((value - b) / a) for value in values]


class CellViewBox(pg.ViewBox):
    """
    ViewBox одной ячейки:
      - колесо мыши    -> зум по X
      - Shift + колесо -> зум по Y
      - drag           -> панорамирование по X
      - Shift + drag   -> панорамирование по Y
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def wheelEvent(self, ev, axis=None):
        """Разделяет масштабирование колесом мыши по X/Y."""
        axis = 1 if ev.modifiers() & Qt.ShiftModifier else 0
        super().wheelEvent(ev, axis=axis)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != Qt.LeftButton:
            ev.ignore()
            return
        ev.accept()
        
        # Выбираем ось
        if ev.modifiers() & Qt.ShiftModifier:
            axis = 1
        else:
            axis = 0

        # Родительский метод делает всю работу
        super().mouseDragEvent(ev, axis=axis)

# Основной виджет графиков


class GraphHeader(QLabel):
    """Перетаскивание заголовка меняет местами графики в сетке."""
    def __init__(self, name, owner):
        super().__init__(name)
        self.name = name
        self.owner = owner
        self._start = None
        self.setStyleSheet('font-weight: bold; padding: 5px; background: #e8edf3;')
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._start = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start is not None and (event.pos() - self._start).manhattanLength() > QApplication.startDragDistance():
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData('application/x-uav-graph', self.name.encode('utf-8'))
            drag.setMimeData(mime)
            drag.exec_(Qt.MoveAction)
            self._start = None

    def mouseReleaseEvent(self, event):
        self._start = None
        super().mouseReleaseEvent(event)


class GraphPanel(QFrame):
    """Панель внутри главного окна, принимающая перетаскивание графика."""
    def __init__(self, name, owner, plot):
        super().__init__()
        self.name = name
        self.owner = owner
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumSize(120, 90)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        bar = QHBoxLayout()
        bar.addWidget(GraphHeader(name, owner), 1)
        detach = QPushButton('↗')
        detach.setToolTip('Вынести график в отдельное окно')
        detach.setFixedWidth(28)
        detach.clicked.connect(lambda: owner._detach_graph(name))
        bar.addWidget(detach)
        layout.addLayout(bar)
        layout.addWidget(plot, 1)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/x-uav-graph'):
            event.acceptProposedAction()

    def dropEvent(self, event):
        source = bytes(event.mimeData().data('application/x-uav-graph')).decode('utf-8')
        self.owner._swap_graphs(source, self.name)
        event.acceptProposedAction()


class GraphWindow(QDialog):
    """Отдельное окно только после явного открепления пользователем."""
    def __init__(self, name, owner, plot):
        super().__init__(owner.window())
        self.name = name
        self.owner = owner
        self.setWindowTitle(name)
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(plot)

    def closeEvent(self, event):
        self.owner._attach_graph(self.name)
        super().closeEvent(event)


class PlotWidget(QWidget):
    time_type_changed = pyqtSignal(str)
    cursor_position_changed = pyqtSignal(float, dict)
    active_plot_changed = pyqtSignal(str)
    export_range_changed = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()

        # Данные, графики и цвета.
        self.curves = {}              # parameter_name: PlotDataItem
        self.plots = self.curves      # совместимость со старым названием
        self.plot_data = {}           # parameter_name: raw/display/ops
        self.plot_colors = {}         # parameter_name: color
        self.active_plot = None

        # Отдельные ячейки.
        self.plot_widgets = {}        # parameter_name: pg.PlotWidget
        self.plot_items = {}          # parameter_name: pg.PlotItem
        self.view_boxes = {}          # parameter_name: CellViewBox
        self.time_axes = {}           # parameter_name: TimeAxis
        self.left_axes = {}           # parameter_name: ParameterAxis
        self.master_view_box = None

        # Режим отображения: одна общая область или отдельные ячейки.
        self.display_mode = 'dock'
        self.common_plot_widget = None
        self.common_plot_item = None
        self.common_view_box = None
        self.common_time_axis = None
        self.common_cursor_line = None
        self.common_export_preview_line = None
        self.common_export_start_line = None
        self.common_export_end_line = None
        self.common_legend = None
        self.common_right_axes = {}   # parameter_name: ParameterAxis для режима одной области

        # Курсор и маркеры.
        self.cursor_lines = {}        # текущие линии курсора в активном режиме
        self.cell_cursor_lines = {}   # parameter_name: InfiniteLine для режима ячеек
        self.cursor_points = {}       # parameter_name: ScatterPlotItem
        self.cursor_line = None       # совместимость со старым кодом

        # Смещение TimeUS -> GPS.
        self.time_offset = 0.0

        # Состояние CSV-экспорта.
        self.export_selection_enabled = False
        self.export_selection_target = None
        self.export_start_time = None
        self.export_end_time = None
        self.export_preview_lines = {}
        self.export_start_lines = {}
        self.export_end_lines = {}
        self.cell_export_preview_lines = {}
        self.cell_export_start_lines = {}
        self.cell_export_end_lines = {}
        self.export_preview_line = None
        self.export_start_line = None
        self.export_end_line = None

        # Dock-конструктор: одна панель может содержать несколько параметров.
        self.dock_panels = {}  # имя -> GraphPanel
        self.dock_host = None
        self.graph_order = []
        self.floating_graphs = {}
        self._split_rows = []
        self.graph_groups = {}       # имя параметра -> идентификатор панели
        self.group_widgets = {}      # идентификатор -> общий PlotWidget
        self.group_curves = {}       # идентификатор -> копии кривых
        self._panel_plots = {}       # идентификатор -> виджет панели
        self.init_ui()

    # Создание интерфейса

    def init_ui(self):
        """Инициализация интерфейса графиков."""
        layout = QVBoxLayout(self)

        # Верхняя панель управления.
        control_layout = QHBoxLayout()

        self.time_type_combo = QComboBox()
        self.time_type_combo.addItems(['TimeUS', 'GPS'])
        self.time_type_combo.currentTextChanged.connect(self._on_time_type_combo_changed)

        self.active_plot_combo = QComboBox()
        self.active_plot_combo.currentTextChanged.connect(self.on_active_plot_combo_changed)

        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItems(['По ячейкам', 'В одной области'])
        self.display_mode_combo.setCurrentText('По ячейкам')
        self.display_mode_combo.currentTextChanged.connect(self.on_display_mode_changed)

        self.color_btn = QPushButton('Изменить цвет')
        self.color_btn.clicked.connect(self.change_active_plot_color)
        self.color_btn.setEnabled(False)

        self.reset_all_btn = QPushButton('Фокус на всех')
        self.reset_all_btn.clicked.connect(self.reset_all_plots)

        self.reset_active_btn = QPushButton('Фокус на активный')
        self.reset_active_btn.clicked.connect(self.reset_active_plot)
        self.reset_active_btn.setEnabled(False)

        self.gps_date_label = QLabel('')
        self.gps_date_label.setStyleSheet('color: gray; font-size: 10px;')

        control_layout.addWidget(QLabel('Ось времени:'))
        control_layout.addWidget(self.time_type_combo)
        control_layout.addWidget(QLabel('Активный график:'))
        control_layout.addWidget(self.active_plot_combo)
        control_layout.addWidget(QLabel('Отображение:'))
        control_layout.addWidget(self.display_mode_combo)
        self.group_btn = QPushButton('Объединить графики')
        self.group_btn.clicked.connect(self.merge_graphs_dialog)
        control_layout.addWidget(self.group_btn)
        self.ungroup_btn = QPushButton('Разделить графики')
        self.ungroup_btn.clicked.connect(self.split_graphs_dialog)
        control_layout.addWidget(self.ungroup_btn)
        control_layout.addWidget(self.color_btn)
        control_layout.addWidget(self.reset_all_btn)
        control_layout.addWidget(self.reset_active_btn)
        control_layout.addWidget(self.gps_date_label)
        control_layout.addStretch()
        layout.addLayout(control_layout)

        # Сетка ячеек графиков.
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(6)
        layout.addWidget(self.grid_container, 1)
        self.grid_container.hide()

        # QSplitter — настоящая внутренняя область, не создающая отдельных окон.
        self.dock_host = QSplitter(Qt.Vertical, self)
        self.dock_host.setChildrenCollapsible(False)
        layout.addWidget(self.dock_host, 1)

        # Панель масштабирования отображения.
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel('Масштаб отображения:'))

        self.scale_axis_combo = QComboBox()
        self.scale_axis_combo.addItems(['Ось X', 'Ось Y'])
        scale_layout.addWidget(self.scale_axis_combo)

        self.scale_target_combo = QComboBox()
        self.scale_target_combo.addItems(['Активный график', 'Все графики'])
        scale_layout.addWidget(self.scale_target_combo)

        self.scale_factor_edit = QLineEdit('1.0')
        self.scale_factor_edit.setPlaceholderText('Коэффициент')
        self.scale_factor_edit.setMaximumWidth(80)
        scale_layout.addWidget(self.scale_factor_edit)

        self.scale_apply_btn = QPushButton('Применить масштаб')
        self.scale_apply_btn.clicked.connect(self.apply_data_scaling)
        scale_layout.addWidget(self.scale_apply_btn)

        self.scale_reset_btn = QPushButton('Сбросить масштаб')
        self.scale_reset_btn.clicked.connect(self.reset_data_scaling)
        scale_layout.addWidget(self.scale_reset_btn)

        scale_layout.addStretch()
        layout.addLayout(scale_layout)

        # Панель сдвига отображения.
        shift_layout = QHBoxLayout()
        shift_layout.addWidget(QLabel('Сдвиг отображения:'))

        self.shift_axis_combo = QComboBox()
        self.shift_axis_combo.addItems(['Ось X', 'Ось Y'])
        shift_layout.addWidget(self.shift_axis_combo)

        self.shift_target_combo = QComboBox()
        self.shift_target_combo.addItems(['Активный график', 'Все графики'])
        shift_layout.addWidget(self.shift_target_combo)

        self.shift_value_edit = QLineEdit('0.0')
        self.shift_value_edit.setPlaceholderText('Смещение')
        self.shift_value_edit.setMaximumWidth(80)
        shift_layout.addWidget(self.shift_value_edit)

        self.shift_apply_btn = QPushButton('Применить сдвиг')
        self.shift_apply_btn.clicked.connect(self.apply_data_shift)
        shift_layout.addWidget(self.shift_apply_btn)

        self.shift_reset_btn = QPushButton('Сбросить сдвиг')
        self.shift_reset_btn.clicked.connect(self.reset_data_shift)
        shift_layout.addWidget(self.shift_reset_btn)

        shift_layout.addStretch()
        layout.addLayout(shift_layout)

    # Сетка ячеек

    def _is_cell_mode(self):
        """Проверяет, включён ли режим отдельных ячеек."""
        return self.display_mode in ('cells', 'dock')

    def on_display_mode_changed(self, text):
        """Ячейки сразу редактируются мышью в главном окне."""
        self.display_mode = 'single' if text == 'В одной области' else 'dock'
        self.grid_container.setVisible(self.display_mode == 'single')
        self.dock_host.setVisible(self.display_mode == 'dock')
        self._rebuild_plot_layout()
        self.reset_all_plots()

    def _create_common_plot(self):
        """Создаёт общий график для режима отображения всех линий в одной области."""
        if self.common_plot_widget is not None:
            return

        self.common_time_axis = TimeAxis(
            get_time_type=lambda: self.time_type_combo.currentText(),
            get_time_offset=lambda: self.time_offset,
            has_custom_x_transform=self._has_custom_x_transform,
            orientation='bottom',
        )

        self.common_view_box = CellViewBox()
        self.common_plot_widget = pg.PlotWidget(
            viewBox=self.common_view_box,
            axisItems={'bottom': self.common_time_axis},
        )

        self.common_plot_widget.getPlotItem().buttonsHidden = True # убрать а
        self.common_plot_widget.setBackground('w')
        self.common_plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.common_view_box.disableAutoRange(axis=pg.ViewBox.XAxis)
        self.common_view_box.disableAutoRange(axis=pg.ViewBox.YAxis)
        self.common_plot_widget.setLabel('left', 'Значение')
        self.common_plot_widget.setLabel('bottom', 'Время')

        self.common_plot_item = self.common_plot_widget.getPlotItem()
        self.common_legend = self.common_plot_widget.addLegend()

        self.common_plot_widget.scene().sigMouseMoved.connect(self.on_mouse_moved)
        self.common_plot_widget.scene().sigMouseClicked.connect(self.on_mouse_clicked)

        self.common_cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#000000', width=1))
        self.common_cursor_line.setVisible(False)
        self.common_plot_widget.addItem(self.common_cursor_line, ignoreBounds=True)

        self.common_export_preview_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen((120, 120, 120), width=1, style=Qt.DashLine),
        )
        self.common_export_start_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen((0, 140, 0), width=2),
        )
        self.common_export_end_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen((180, 0, 0), width=2),
        )

        for line in (self.common_export_preview_line, self.common_export_start_line, self.common_export_end_line):
            line.setVisible(False)
            self.common_plot_widget.addItem(line, ignoreBounds=True)

    def _clear_common_right_axes(self):
        """Удаляет индивидуальные правые оси из общего графика."""
        if self.common_plot_item is None:
            self.common_right_axes.clear()
            return

        for axis in list(self.common_right_axes.values()):
            try:
                self.common_plot_item.layout.removeItem(axis)
            except Exception:
                pass

        self.common_right_axes.clear()

    def _ensure_common_right_axes(self):
        """Создаёт правые raw-оси для всех графиков в режиме одной области."""
        if self.common_plot_item is None:
            return

        # Пересобираем оси полностью, чтобы порядок соответствовал порядку графиков.
        self._clear_common_right_axes()

        for name in self.plot_data:
            color = self.plot_colors.get(name, '#1f77b4')
            width = 2 if name == self.active_plot else 1

            axis = ParameterAxis(name, self._get_y_affine, orientation='right')
            axis.setLabel(text=name)
            axis.setStyle(tickTextOffset=6)
            axis.setPen(pg.mkPen(color, width=width))
            axis.setTextPen(pg.mkPen(color, width=width))
            axis.linkToView(self.common_plot_item.vb)

            col = self.common_plot_item.layout.columnCount()
            self.common_plot_item.layout.addItem(axis, 2, col)
            self.common_right_axes[name] = axis

    def _detach_plot_items(self):
        """Убирает линии и маркеры из текущих областей перед переносом в другой режим."""
        widgets = list(self.plot_widgets.values())
        if self.common_plot_widget is not None:
            widgets.append(self.common_plot_widget)

        for widget in widgets:
            for item in list(self.curves.values()) + list(self.cursor_points.values()):
                try:
                    widget.removeItem(item)
                except Exception:
                    pass

        if self.common_legend is not None:
            try:
                self.common_legend.clear()
            except Exception:
                pass

    def _rebuild_plot_layout(self):
        """Перестраивает область графиков под выбранный режим отображения."""
        self._detach_plot_items()
        self._clear_grid_layout()
        if self.display_mode == 'single':
            for window in list(self.floating_graphs.values()):
                window.close()
            self._release_dock_widgets()

        if self._is_cell_mode():
            self._clear_common_right_axes()
            self.cursor_lines = {
                name: self.cell_cursor_lines[name]
                for name in self.plot_data
                if name in self.cell_cursor_lines
            }
            self.export_preview_lines = {
                name: self.cell_export_preview_lines[name]
                for name in self.plot_data
                if name in self.cell_export_preview_lines
            }
            self.export_start_lines = {
                name: self.cell_export_start_lines[name]
                for name in self.plot_data
                if name in self.cell_export_start_lines
            }
            self.export_end_lines = {
                name: self.cell_export_end_lines[name]
                for name in self.plot_data
                if name in self.cell_export_end_lines
            }

            for name in self.plot_data:
                if name in self.curves:
                    self.plot_widgets[name].addItem(self.curves[name])
                if name in self.cursor_points:
                    self.plot_widgets[name].addItem(self.cursor_points[name])

            self.cursor_line = next(iter(self.cursor_lines.values()), None)
            self.export_preview_line = next(iter(self.export_preview_lines.values()), None)
            self.export_start_line = next(iter(self.export_start_lines.values()), None)
            self.export_end_line = next(iter(self.export_end_lines.values()), None)
            if self.display_mode == 'dock':
                self._sync_dock_panels()
                self._sync_x_links()
                self._update_axis_labels()
            else:
                self._rebuild_grid()
        else:
            self._create_common_plot()
            self.grid_layout.addWidget(self.common_plot_widget, 0, 0)
            self.grid_layout.setRowStretch(0, 1)
            self.grid_layout.setColumnStretch(0, 1)
            self.master_view_box = self.common_view_box

            self.cursor_lines = {'__common__': self.common_cursor_line}
            self.export_preview_lines = {'__common__': self.common_export_preview_line}
            self.export_start_lines = {'__common__': self.common_export_start_line}
            self.export_end_lines = {'__common__': self.common_export_end_line}
            self.cursor_line = self.common_cursor_line
            self.export_preview_line = self.common_export_preview_line
            self.export_start_line = self.common_export_start_line
            self.export_end_line = self.common_export_end_line

            for name in self.plot_data:
                if name in self.curves:
                    self.common_plot_widget.addItem(self.curves[name])
                    if self.common_legend is not None:
                        try:
                            self.common_legend.addItem(self.curves[name], name)
                        except Exception:
                            pass
                if name in self.cursor_points:
                    self.common_plot_widget.addItem(self.cursor_points[name])

            self._ensure_common_right_axes()
            self._update_axis_labels()

        if self.active_plot in self.curves:
            self.set_active_plot(self.active_plot)
        self._update_export_lines()

    def _release_dock_widgets(self):
        """Освобождает PlotWidget перед сменой режима, не удаляя графики."""
        self._clear_splitters()

    def _group_of(self, name):
        return self.graph_groups.get(name, name)

    def _members(self, group):
        return [name for name in self.graph_order
                if name in self.plot_data and self._group_of(name) == group]

    def _clear_splitters(self):
        # Извлекаем PlotWidget до удаления GraphPanel: иначе Qt удалит графики.
        for key, panel in list(self.dock_panels.items()):
            plot = self._panel_plots.get(key)
            if plot is not None and key not in self.floating_graphs:
                plot.setParent(None)
            panel.setParent(None)
            panel.deleteLater()
        self.dock_panels.clear()
        self._panel_plots.clear()
        while self.dock_host.count():
            child = self.dock_host.widget(0)
            child.setParent(None)
            child.deleteLater()
        self._split_rows.clear()

    def _get_group_plot(self, group, members):
        if len(members) == 1:
            return self.plot_widgets[members[0]]
        widget = self.group_widgets.get(group)
        if widget is None:
            widget = pg.PlotWidget(viewBox=CellViewBox())
            widget.setBackground('w')
            widget.showGrid(x=True, y=True, alpha=0.3)
            widget.setLabel('bottom', 'Время')
            widget.addLegend()
            self.group_widgets[group] = widget
            self.group_curves[group] = {}
        for name, item in list(self.group_curves[group].items()):
            if name not in members:
                widget.removeItem(item)
                del self.group_curves[group][name]
        for name in members:
            data = self.plot_data[name]
            if name not in self.group_curves[group]:
                self.group_curves[group][name] = widget.plot(
                    data['x_display'], data['y_display'],
                    pen=pg.mkPen(self.plot_colors[name], width=2), name=name)
            else:
                self.group_curves[group][name].setData(
                    data['x_display'], data['y_display'])
                self.group_curves[group][name].setPen(
                    pg.mkPen(self.plot_colors[name], width=2))
        widget.enableAutoRange()
        return widget

    def _sync_dock_panels(self):
        """Создаёт по одной панели на группу, сохраняя обычные размеры при отсутствии изменений."""
        names = list(self.plot_data)
        self.graph_order = [n for n in self.graph_order if n in names]
        self.graph_order.extend(n for n in names if n not in self.graph_order)
        groups = []
        for name in self.graph_order:
            group = self._group_of(name)
            if group not in groups and group not in self.floating_graphs:
                groups.append(group)
        if list(self.dock_panels) == groups:
            return
        self._clear_splitters()
        if not groups:
            return
        import math
        cols = max(1, math.ceil(math.sqrt(len(groups))))
        for start in range(0, len(groups), cols):
            row = QSplitter(Qt.Horizontal, self.dock_host)
            row.setChildrenCollapsible(False)
            self.dock_host.addWidget(row)
            self._split_rows.append(row)
            for group in groups[start:start + cols]:
                members = self._members(group)
                plot = self._get_group_plot(group, members)
                panel = GraphPanel(group, self, plot)
                panel.findChild(GraphHeader).setText(' + '.join(members))
                row.addWidget(panel)
                self.dock_panels[group] = panel
                self._panel_plots[group] = plot
        self.dock_host.setSizes([1] * self.dock_host.count())

    def _swap_graphs(self, source, target):
        if source == target:
            return
        a = self._members(source)
        b = self._members(target)
        if not a or not b:
            return
        indices = [self.graph_order.index(n) for n in a + b]
        positions = sorted(indices)
        sequence = b + a if min(self.graph_order.index(n) for n in a) < min(self.graph_order.index(n) for n in b) else a + b
        for pos, name in zip(positions, sequence):
            self.graph_order[pos] = name
        self._clear_splitters()
        self._sync_dock_panels()

    def _detach_graph(self, group):
        if group in self.floating_graphs or group not in self.dock_panels:
            return
        plot = self._panel_plots[group]
        plot.setParent(None)
        window = GraphWindow(' + '.join(self._members(group)), self, plot)
        window.name = group
        self.floating_graphs[group] = window
        self._clear_splitters()
        self._sync_dock_panels()
        window.show()

    def _attach_graph(self, group):
        window = self.floating_graphs.pop(group, None)
        if window is None:
            return
        plot = self.group_widgets.get(group)
        if plot is None and group in self.plot_widgets:
            plot = self.plot_widgets[group]
        if plot is not None:
            plot.setParent(None)
        self._clear_splitters()
        self._sync_dock_panels()

    def show_all_graphs(self):
        for window in self.floating_graphs.values():
            window.show()
            window.raise_()

    def reset_graph_layout(self):
        for window in list(self.floating_graphs.values()):
            window.close()
        self.graph_order = list(self.plot_data)
        self._clear_splitters()
        if self.display_mode == 'dock':
            self._sync_dock_panels()

    def merge_graphs_dialog(self):
        """Выбрать произвольные 2–N кривых и поместить их в одну панель."""
        if len(self.plot_data) < 2:
            QMessageBox.information(self, 'Объединение', 'Добавьте хотя бы два графика.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Объединить графики')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Выберите 2 или больше параметров (Ctrl или Shift):'))
        listing = QListWidget(dialog)
        listing.setSelectionMode(QAbstractItemView.MultiSelection)
        for name in self.graph_order:
            if name in self.plot_data:
                listing.addItem(name)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected = [item.text() for item in listing.selectedItems()]
        if len(selected) < 2:
            QMessageBox.warning(self, 'Объединение', 'Выберите не менее двух параметров.')
            return
        group = self._group_of(selected[0])
        # Если один из выбранных параметров уже в группе, переносим всю его группу.
        affected = {self._group_of(n) for n in selected}
        for name in list(self.plot_data):
            if self._group_of(name) in affected:
                self.graph_groups[name] = group
        self._refresh_group_layout()

    def split_graphs_dialog(self):
        groups = {}
        for name in self.plot_data:
            groups.setdefault(self._group_of(name), []).append(name)
        choices = [name for names in groups.values() if len(names) > 1 for name in names]
        if not choices:
            QMessageBox.information(self, 'Разделение', 'Нет объединённых графиков.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Разделить графики')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Выберите параметры, которые нужно отделить:'))
        listing = QListWidget(dialog)
        listing.setSelectionMode(QAbstractItemView.MultiSelection)
        listing.addItems(choices)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected = [item.text() for item in listing.selectedItems()]
        # Выделяем оставшимся членам группы стабильный идентификатор.
        for name in selected:
            self.graph_groups[name] = name
        self._normalize_groups()
        self._refresh_group_layout()

    def _normalize_groups(self):
        groups = {}
        for name in self.plot_data:
            groups.setdefault(self._group_of(name), []).append(name)
        for key, members in groups.items():
            if key not in members:
                for name in members:
                    self.graph_groups[name] = members[0]

    def _refresh_group_layout(self):
        for window in list(self.floating_graphs.values()):
            window.close()
        self._clear_splitters()
        if self.display_mode == 'dock':
            self._sync_dock_panels()

    def _get_grid_shape(self, count):
        """Возвращает размеры сетки по количеству графиков."""
        if count <= 1:
            return 1, 1
        if count == 2:
            return 2, 1
        if count == 3:
            return 3, 1
        if count == 4:
            return 2, 2
        if count <= 6:
            return 3, 2
        return 3, 3

    def _clear_grid_layout(self):
        """Очищает layout сетки, не удаляя сами виджеты графиков."""
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        # QGridLayout запоминает stretch строк/колонок даже после удаления виджетов.
        # Поэтому при переходе из сетки 2x2/3x3 в режим одной области пустые
        # строки/колонки могли продолжать занимать место, а общий график оставался
        # размером с одну бывшую ячейку. Перед новой раскладкой всё обнуляем.
        for row in range(9):
            self.grid_layout.setRowStretch(row, 0)
            self.grid_layout.setRowMinimumHeight(row, 0)

        for col in range(9):
            self.grid_layout.setColumnStretch(col, 0)
            self.grid_layout.setColumnMinimumWidth(col, 0)

    def _rebuild_grid(self):
        """Перестраивает расположение ячеек при изменении числа графиков."""
        if not self._is_cell_mode():
            return

        self._clear_grid_layout()

        names = list(self.plot_data.keys())
        rows, cols = self._get_grid_shape(len(names))

        for row in range(3):
            self.grid_layout.setRowStretch(row, 0)
        for col in range(3):
            self.grid_layout.setColumnStretch(col, 0)

        for index, name in enumerate(names):
            row = index // cols
            col = index % cols
            self.grid_layout.addWidget(self.plot_widgets[name], row, col)
            self.grid_layout.setRowStretch(row, 1)
            self.grid_layout.setColumnStretch(col, 1)

        self._sync_x_links()
        self._update_axis_labels()

    def _sync_x_links(self):
        """Связывает все ячейки по оси X."""
        names = list(self.view_boxes.keys())
        self.master_view_box = self.view_boxes[names[0]] if names else None

        for view_box in self.view_boxes.values():
            # После добавления нового PlotDataItem pyqtgraph может сам включить
            # авто-диапазон. Если это случается после нашего reset_all_plots(),
            # отдельная ячейка иногда получает старый или слишком широкий X-range.
            # Поэтому перед линковкой и фокусировкой явно выключаем auto-range.
            view_box.disableAutoRange(axis=pg.ViewBox.XAxis)
            view_box.disableAutoRange(axis=pg.ViewBox.YAxis)

            if view_box is self.master_view_box:
                view_box.setXLink(None)
            elif self.master_view_box is not None:
                view_box.setXLink(self.master_view_box)

    def _update_axis_labels(self):
        """Обновляет подписи осей в текущем режиме отображения."""
        bottom_label = 'Display X' if self._has_custom_x_transform() else 'Время'

        if self._is_cell_mode():
            for name, plot_widget in self.plot_widgets.items():
                plot_widget.setLabel('left', name)
                plot_widget.setLabel('bottom', bottom_label)
        elif self.common_plot_widget is not None:
            self.common_plot_widget.setLabel('left', 'Общая шкала')
            self.common_plot_widget.setLabel('bottom', bottom_label)

    def _create_plot_cell(self, parameter_name, color):
        """Создаёт отдельную ячейку для параметра."""
        left_axis = ParameterAxis(parameter_name, self._get_y_affine, orientation='left')
        left_axis.setLabel(text=parameter_name)
        left_axis.setPen(pg.mkPen(color, width=1))
        left_axis.setTextPen(pg.mkPen(color, width=1))

        time_axis = TimeAxis(
            get_time_type=lambda: self.time_type_combo.currentText(),
            get_time_offset=lambda: self.time_offset,
            has_custom_x_transform=self._has_custom_x_transform,
            orientation='bottom',
        )

        view_box = CellViewBox()
        plot_widget = pg.PlotWidget(
            viewBox=view_box,
            axisItems={'left': left_axis, 'bottom': time_axis},
        )

        plot_widget.getPlotItem().buttonsHidden = True # убрать а
        plot_widget.setBackground('w')
        plot_widget.showGrid(x=True, y=True, alpha=0.3)
        view_box.disableAutoRange(axis=pg.ViewBox.XAxis)
        view_box.disableAutoRange(axis=pg.ViewBox.YAxis)
        plot_widget.setLabel('left', parameter_name)
        plot_widget.setLabel('bottom', 'Время')
        plot_widget.setTitle(parameter_name, color=color, size='9pt')
        plot_widget.scene().sigMouseMoved.connect(self.on_mouse_moved)
        plot_widget.scene().sigMouseClicked.connect(self.on_mouse_clicked)

        self.plot_widgets[parameter_name] = plot_widget
        self.plot_items[parameter_name] = plot_widget.getPlotItem()
        self.view_boxes[parameter_name] = view_box
        self.time_axes[parameter_name] = time_axis
        self.left_axes[parameter_name] = left_axis

        # Общий курсор представлен отдельной вертикальной линией в каждой ячейке.
        cursor_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#000000', width=1))
        cursor_line.setVisible(False)
        plot_widget.addItem(cursor_line, ignoreBounds=True)
        self.cell_cursor_lines[parameter_name] = cursor_line
        if self._is_cell_mode():
            self.cursor_lines[parameter_name] = cursor_line
            self.cursor_line = self.cursor_line or cursor_line

        # Линии экспортного диапазона тоже дублируются по всем ячейкам.
        preview_line = pg.InfiniteLine(angle=90, movable=False,
                                       pen=pg.mkPen((120, 120, 120), width=1, style=Qt.DashLine))
        start_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((0, 140, 0), width=2))
        end_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((180, 0, 0), width=2))

        for line in (preview_line, start_line, end_line):
            line.setVisible(False)
            plot_widget.addItem(line, ignoreBounds=True)

        self.cell_export_preview_lines[parameter_name] = preview_line
        self.cell_export_start_lines[parameter_name] = start_line
        self.cell_export_end_lines[parameter_name] = end_line

        if self._is_cell_mode():
            self.export_preview_lines[parameter_name] = preview_line
            self.export_start_lines[parameter_name] = start_line
            self.export_end_lines[parameter_name] = end_line
            self.export_preview_line = self.export_preview_line or preview_line
            self.export_start_line = self.export_start_line or start_line
            self.export_end_line = self.export_end_line or end_line

    def _delete_plot_cell(self, parameter_name):
        """Удаляет ячейку параметра."""
        plot_widget = self.plot_widgets.pop(parameter_name, None)
        if plot_widget is not None:
            plot_widget.setParent(None)
            plot_widget.deleteLater()

        self.plot_items.pop(parameter_name, None)
        self.view_boxes.pop(parameter_name, None)
        self.time_axes.pop(parameter_name, None)
        self.left_axes.pop(parameter_name, None)
        self.cursor_lines.pop(parameter_name, None)
        self.cell_cursor_lines.pop(parameter_name, None)
        self.export_preview_lines.pop(parameter_name, None)
        self.export_start_lines.pop(parameter_name, None)
        self.export_end_lines.pop(parameter_name, None)
        self.cell_export_preview_lines.pop(parameter_name, None)
        self.cell_export_start_lines.pop(parameter_name, None)
        self.cell_export_end_lines.pop(parameter_name, None)

        self.cursor_line = next(iter(self.cursor_lines.values()), None)
        self.export_preview_line = next(iter(self.export_preview_lines.values()), None)
        self.export_start_line = next(iter(self.export_start_lines.values()), None)
        self.export_end_line = next(iter(self.export_end_lines.values()), None)

    # Raw/display данные

    def _has_custom_x_transform(self):
        """Проверяет, были ли применены преобразования по X."""
        return any(data['ops_x'] for data in self.plot_data.values())

    def _update_bottom_axis_label(self):
        """Совместимость со старой логикой обновления нижней оси."""
        self._update_axis_labels()

    def _get_parameters_to_modify(self, target: str):
        """Возвращает список параметров для преобразования."""
        if target == 'Активный график' and self.active_plot:
            return [self.active_plot]
        if target == 'Все графики':
            return list(self.plot_data.keys())
        return []

    def _apply_ops(self, values: np.ndarray, ops):
        """Применяет список display-операций к raw-массиву."""
        result = np.asarray(values, dtype=float).copy()
        for kind, value in ops:
            if kind == 'scale':
                result *= value
            elif kind == 'shift':
                result += value
        return result

    def _get_affine_from_ops(self, ops):
        """Возвращает коэффициенты display = raw * a + b."""
        a = 1.0
        b = 0.0
        for kind, value in ops:
            if kind == 'scale':
                a *= value
                b *= value
            elif kind == 'shift':
                b += value
        return a, b

    def _get_y_affine(self, parameter_name):
        """Возвращает итоговое преобразование Y для параметра."""
        if parameter_name not in self.plot_data:
            return 1.0, 0.0
        return self._get_affine_from_ops(self.plot_data[parameter_name]['ops_y'])

    def _refresh_axis(self, parameter_name):
        """Перерисовывает оси конкретной ячейки или общей области."""
        axes = [self.left_axes.get(parameter_name), self.time_axes.get(parameter_name)]
        if not self._is_cell_mode():
            axes.append(self.common_time_axis)
            axes.append(self.common_right_axes.get(parameter_name))

        for axis in axes:
            if axis is not None:
                axis.picture = None
                axis.update()

    def _refresh_all_axes(self):
        """Перерисовывает оси всех ячеек."""
        for name in self.plot_data:
            self._refresh_axis(name)

    def _refresh_right_axis(self, parameter_name):
        """Совместимость: раньше была правая ось, теперь обновляется левая ось ячейки."""
        self._refresh_axis(parameter_name)

    def _refresh_all_right_axes(self):
        """Совместимость: обновляет оси всех ячеек."""
        self._refresh_all_axes()

    def _rebuild_display_data(self, parameter_name):
        """Пересчитывает display-данные из raw-данных и операций."""
        data = self.plot_data[parameter_name]
        data['x_display'] = self._apply_ops(data['x_raw'], data['ops_x'])
        data['y_display'] = self._apply_ops(data['y_raw'], data['ops_y'])

        curve = self.curves.get(parameter_name)
        if curve is not None:
            curve.setData(data['x_display'], data['y_display'])

        for group, curves in self.group_curves.items():
            if parameter_name in curves:
                curves[parameter_name].setData(data['x_display'], data['y_display'])
        self._refresh_axis(parameter_name)
        self._update_bottom_axis_label()
        self.update_gps_date_label()
        self._update_export_lines()

    def _remove_ops_of_kind(self, parameter_name, axis_key, kind):
        """Удаляет все операции одного типа: scale или shift."""
        data = self.plot_data.get(parameter_name)
        if data is None:
            return
        key = 'ops_x' if axis_key == 'x' else 'ops_y'
        data[key] = [op for op in data[key] if op[0] != kind]
        self._rebuild_display_data(parameter_name)

    def _get_display_bounds(self, parameter_names):
        """Возвращает общие display-границы выбранных параметров."""
        x_min = x_max = y_min = y_max = None
        for name in parameter_names:
            data = self.plot_data.get(name)
            if data is None or len(data['x_display']) == 0 or len(data['y_display']) == 0:
                continue

            lx_min = float(np.nanmin(data['x_display']))
            lx_max = float(np.nanmax(data['x_display']))
            ly_min = float(np.nanmin(data['y_display']))
            ly_max = float(np.nanmax(data['y_display']))

            x_min = lx_min if x_min is None else min(x_min, lx_min)
            x_max = lx_max if x_max is None else max(x_max, lx_max)
            y_min = ly_min if y_min is None else min(y_min, ly_min)
            y_max = ly_max if y_max is None else max(y_max, ly_max)

        return x_min, x_max, y_min, y_max

    def _set_x_focus_for_names(self, parameter_names):
        """Устанавливает общий X-диапазон для всех связанных ячеек."""
        x_min, x_max, _, _ = self._get_display_bounds(parameter_names)
        if x_min is None or x_max is None:
            return

        x_range = x_max - x_min
        x_pad = x_range * 0.1 if x_range > 0 else (abs(x_min) * 0.1 if x_min != 0 else 1.0)
        x_range_with_pad = (x_min - x_pad, x_max + x_pad)

        # В режиме ячеек одного setXRange на master ViewBox иногда недостаточно:
        # новая ячейка может применить свой auto-range уже после линковки. Поэтому
        # задаём одинаковый диапазон всем ViewBox явно.
        if self._is_cell_mode():
            for view_box in self.view_boxes.values():
                view_box.disableAutoRange(axis=pg.ViewBox.XAxis)
                view_box.setXRange(*x_range_with_pad, padding=0)
            return

        if self.common_view_box is not None:
            self.common_view_box.disableAutoRange(axis=pg.ViewBox.XAxis)
            self.common_view_box.setXRange(*x_range_with_pad, padding=0)

    def _set_y_focus_for_names(self, parameter_names):
        """Устанавливает Y-диапазон: общий в одной области или индивидуальный в ячейках."""
        if not self._is_cell_mode():
            _, _, y_min, y_max = self._get_display_bounds(parameter_names)
            if y_min is None or y_max is None or self.common_view_box is None:
                return

            y_range = y_max - y_min
            y_pad = y_range * 0.1 if y_range > 0 else (abs(y_min) * 0.1 if y_min != 0 else 1.0)
            self.common_view_box.disableAutoRange(axis=pg.ViewBox.YAxis)
            self.common_view_box.setYRange(y_min - y_pad, y_max + y_pad, padding=0)
            return

        for name in parameter_names:
            data = self.plot_data.get(name)
            view_box = self.view_boxes.get(name)
            if data is None or view_box is None or len(data['y_display']) == 0:
                continue

            y_min = float(np.nanmin(data['y_display']))
            y_max = float(np.nanmax(data['y_display']))
            y_range = y_max - y_min
            y_pad = y_range * 0.1 if y_range > 0 else (abs(y_min) * 0.1 if y_min != 0 else 1.0)
            view_box.disableAutoRange(axis=pg.ViewBox.YAxis)
            view_box.setYRange(y_min - y_pad, y_max + y_pad, padding=0)

    def _set_focus_for_names(self, parameter_names):
        """Фокусирует X и Y на выбранных параметрах."""
        self._set_x_focus_for_names(parameter_names)
        self._set_y_focus_for_names(parameter_names)
        self.update_gps_date_label()
        self._refresh_all_axes()

    # Выбор экспортного диапазона

    def _get_selection_reference_name(self):
        """Выбирает график, по которому raw-время связывается с display-X."""
        if self.active_plot in self.plot_data:
            return self.active_plot
        if self.plot_data:
            return next(iter(self.plot_data))
        return None

    def _raw_time_to_display_x(self, raw_time, reference_name=None):
        """Переводит raw TimeUS в ближайшую display-позицию."""
        if raw_time is None:
            return None

        reference_name = reference_name or self._get_selection_reference_name()
        if reference_name is None or reference_name not in self.plot_data:
            return None

        data = self.plot_data[reference_name]
        if len(data['x_raw']) == 0:
            return None

        idx = int(np.argmin(np.abs(data['x_raw'] - float(raw_time))))
        return float(data['x_display'][idx])

    def _cell_name_from_scene_pos(self, scene_pos):
        """Определяет область графика под курсором."""
        if not self._is_cell_mode():
            if self.common_plot_item is not None and self.common_plot_item.vb.sceneBoundingRect().contains(scene_pos):
                return '__common__'
            return None

        for name, plot_item in self.plot_items.items():
            if plot_item.vb.sceneBoundingRect().contains(scene_pos):
                return name
        return None

    def _nearest_raw_time_from_scene(self, scene_pos):
        """Находит ближайшее raw-время к клику."""
        source_name = self._cell_name_from_scene_pos(scene_pos)
        reference_name = source_name if source_name not in (None, '__common__') else self._get_selection_reference_name()
        if reference_name is None:
            return None

        plot_item = self.common_plot_item if source_name == '__common__' else self.plot_items[reference_name]
        mouse_point = plot_item.vb.mapSceneToView(scene_pos)
        x_display_cursor = float(mouse_point.x())

        data = self.plot_data[reference_name]
        if len(data['x_display']) == 0:
            return None

        idx = int(np.argmin(np.abs(data['x_display'] - x_display_cursor)))
        return float(data['x_raw'][idx])

    def _emit_export_range_changed(self):
        """Сообщает MainWindow о новом диапазоне экспорта."""
        self.export_range_changed.emit(self.export_start_time, self.export_end_time)

    def _is_export_click_mode_active(self):
        """Проверяет, активен ли выбор границ кликами."""
        return self.export_selection_enabled or self.export_selection_target in ('start', 'end')

    def _set_line_group_visible(self, lines, visible):
        """Показывает/скрывает группу вертикальных линий."""
        for line in lines.values():
            line.setVisible(visible)

    def _set_line_group_pos(self, lines, x_pos, visible=True):
        """Ставит группу вертикальных линий в одну X-позицию."""
        for line in lines.values():
            line.setPos(x_pos)
            line.setVisible(visible)

    def _update_export_lines(self):
        """Обновляет линии экспортного диапазона во всех ячейках."""
        start_display = self._raw_time_to_display_x(self.export_start_time)
        end_display = self._raw_time_to_display_x(self.export_end_time)

        if start_display is None:
            self._set_line_group_visible(self.export_start_lines, False)
        else:
            self._set_line_group_pos(self.export_start_lines, start_display, True)

        if end_display is None:
            self._set_line_group_visible(self.export_end_lines, False)
        else:
            self._set_line_group_pos(self.export_end_lines, end_display, True)

        if not self._is_export_click_mode_active():
            self._set_line_group_visible(self.export_preview_lines, False)

    def set_export_selection_enabled(self, enabled: bool):
        """Включает/выключает режим выбора диапазона двумя кликами."""
        self.export_selection_enabled = bool(enabled)
        if enabled:
            if self.export_start_time is None:
                self.export_selection_target = 'start'
            elif self.export_end_time is None:
                self.export_selection_target = 'end'
            else:
                self.export_selection_target = None
        else:
            self.export_selection_target = None
            self._set_line_group_visible(self.export_preview_lines, False)
        self._update_export_lines()

    def set_next_export_boundary(self, boundary_name: str):
        """Назначает следующий клик началом или концом диапазона."""
        self.export_selection_target = boundary_name if boundary_name in ('start', 'end') else None
        self._update_export_lines()

    def clear_export_selection(self, emit_signal=True):
        """Очищает экспортный диапазон."""
        self.export_start_time = None
        self.export_end_time = None
        self.export_selection_target = 'start' if self.export_selection_enabled else None
        self._set_line_group_visible(self.export_start_lines, False)
        self._set_line_group_visible(self.export_end_lines, False)
        if emit_signal:
            self._emit_export_range_changed()

    def set_export_boundary(self, boundary_name: str, time_value, emit_signal=True):
        """Устанавливает одну границу экспортного диапазона."""
        if time_value is None:
            return

        time_value = float(time_value)
        if boundary_name == 'start':
            self.export_start_time = time_value
        elif boundary_name == 'end':
            self.export_end_time = time_value
        else:
            return

        if self.export_start_time is not None and self.export_end_time is not None:
            if self.export_start_time > self.export_end_time:
                self.export_start_time, self.export_end_time = self.export_end_time, self.export_start_time

        if self.export_selection_enabled:
            if self.export_start_time is None:
                self.export_selection_target = 'start'
            elif self.export_end_time is None:
                self.export_selection_target = 'end'
            else:
                self.export_selection_target = None
        else:
            self.export_selection_target = None

        self._update_export_lines()
        if emit_signal:
            self._emit_export_range_changed()

    def set_export_range(self, start_time, end_time, emit_signal=True):
        """Устанавливает обе границы экспортного диапазона из внешних полей."""
        self.export_start_time = float(start_time) if start_time is not None else None
        self.export_end_time = float(end_time) if end_time is not None else None

        if self.export_start_time is not None and self.export_end_time is not None:
            if self.export_start_time > self.export_end_time:
                self.export_start_time, self.export_end_time = self.export_end_time, self.export_start_time

        if self.export_selection_enabled:
            if self.export_start_time is None:
                self.export_selection_target = 'start'
            elif self.export_end_time is None:
                self.export_selection_target = 'end'
            else:
                self.export_selection_target = None
        else:
            self.export_selection_target = None

        self._update_export_lines()
        if emit_signal:
            self._emit_export_range_changed()

    def get_export_range(self):
        """Возвращает текущий экспортный диапазон."""
        return self.export_start_time, self.export_end_time

    # Управление графиками

    def add_plot(self, parameter_name, x_data, y_data, color='#1f77b4'):
        """Добавляет новый график в отдельную ячейку."""
        if parameter_name in self.curves:
            self.remove_plot(parameter_name)


        x_arr = np.asarray(x_data, dtype=float)
        y_arr = np.asarray(y_data, dtype=float)

        self.plot_data[parameter_name] = {
            'x_raw': x_arr,
            'y_raw': y_arr,
            'x_display': x_arr.copy(),
            'y_display': y_arr.copy(),
            'ops_x': [],
            'ops_y': [],
        }
        self.plot_colors[parameter_name] = color

        self._create_plot_cell(parameter_name, color)

        curve = pg.PlotDataItem(x_arr, y_arr, pen=pg.mkPen(color, width=2))
        self.curves[parameter_name] = curve

        cursor_point = pg.ScatterPlotItem([0], [0], pen=pg.mkPen(color, width=2),
                                          brush=pg.mkBrush(color), size=9, symbol='o')
        cursor_point.setVisible(False)
        self.cursor_points[parameter_name] = cursor_point

        self.active_plot_combo.addItem(parameter_name)

        if len(x_arr) > 0:
            self._set_line_group_pos(self.cursor_lines, float(x_arr[0]), True)

        if self.active_plot is None:
            self.set_active_plot(parameter_name)

        self._rebuild_display_data(parameter_name)
        self._rebuild_plot_layout()
        self.reset_all_plots()

        # Дополнительная отложенная фокусировка нужна для случая, когда pyqtgraph
        # применяет внутренний auto-range уже после добавления элемента в сцену.
        QTimer.singleShot(0, self.reset_all_plots)

        return True

    def remove_plot(self, parameter_name):
        """Удаляет график и связанную с ним ячейку."""
        if parameter_name not in self.curves:
            return

        curve = self.curves.pop(parameter_name, None)
        point = self.cursor_points.pop(parameter_name, None)

        # В режиме общей области элементы нужно явно убрать из общего PlotWidget.
        for widget in list(self.plot_widgets.values()) + ([self.common_plot_widget] if self.common_plot_widget is not None else []):
            for item in (curve, point):
                if item is not None:
                    try:
                        widget.removeItem(item)
                    except Exception:
                        pass

        self.graph_groups.pop(parameter_name, None)
        for group, curves in self.group_curves.items():
            if parameter_name in curves:
                widget = self.group_widgets.get(group)
                if widget is not None:
                    widget.removeItem(curves[parameter_name])
                del curves[parameter_name]
        self.plot_data.pop(parameter_name, None)
        self.plot_colors.pop(parameter_name, None)
        common_axis = self.common_right_axes.pop(parameter_name, None)
        if common_axis is not None and self.common_plot_item is not None:
            try:
                self.common_plot_item.layout.removeItem(common_axis)
            except Exception:
                pass

        if parameter_name in self.floating_graphs:
            self.floating_graphs[parameter_name].close()
        if parameter_name in self.dock_panels:
            self._clear_splitters()
        if parameter_name in self.graph_order:
            self.graph_order.remove(parameter_name)
        self._delete_plot_cell(parameter_name)

        index = self.active_plot_combo.findText(parameter_name)
        if index >= 0:
            self.active_plot_combo.removeItem(index)

        if self.active_plot == parameter_name:
            if self.curves:
                self.set_active_plot(next(iter(self.curves)))
            else:
                self.active_plot = None
                self.color_btn.setEnabled(False)
                self.reset_active_btn.setEnabled(False)

        self._normalize_groups()
        self._rebuild_plot_layout()
        self._update_bottom_axis_label()
        self.update_gps_date_label()
        self._refresh_all_axes()
        self._update_export_lines()

    def clear_all_plots(self):
        """Полностью очищает все графики."""
        for name in list(self.curves.keys()):
            self.remove_plot(name)

        self._clear_common_right_axes()
        self.graph_groups.clear()
        self.group_curves.clear()
        self.group_widgets.clear()
        self.plot_data.clear()
        self.plot_colors.clear()
        self.active_plot_combo.clear()
        self.active_plot = None
        self.color_btn.setEnabled(False)
        self.reset_active_btn.setEnabled(False)
        self.clear_export_selection(emit_signal=True)
        self._set_line_group_visible(self.export_preview_lines, False)

    def set_active_plot(self, parameter_name):
        """Устанавливает активный график."""
        if parameter_name not in self.curves:
            return

        self.active_plot = parameter_name

        idx = self.active_plot_combo.findText(parameter_name)
        if idx >= 0 and self.active_plot_combo.currentIndex() != idx:
            self.active_plot_combo.blockSignals(True)
            self.active_plot_combo.setCurrentIndex(idx)
            self.active_plot_combo.blockSignals(False)

        self.color_btn.setEnabled(True)
        self.reset_active_btn.setEnabled(True)

        for name, curve in self.curves.items():
            color = self.plot_colors[name]
            curve.setPen(pg.mkPen(color, width=2 if name == parameter_name else 2))

        for name, axis in self.left_axes.items():
            color = self.plot_colors[name]
            width = 2 if name == parameter_name else 1
            axis.setPen(pg.mkPen(color, width=width))
            axis.setTextPen(pg.mkPen(color, width=width))

        for name, axis in self.common_right_axes.items():
            color = self.plot_colors[name]
            width = 2 if name == parameter_name else 1
            axis.setPen(pg.mkPen(color, width=width))
            axis.setTextPen(pg.mkPen(color, width=width))

        self._update_export_lines()
        self.active_plot_changed.emit(parameter_name)

    def on_active_plot_combo_changed(self, plot_name):
        """Обработка изменения активного графика через комбобокс."""
        if plot_name and plot_name in self.curves:
            self.set_active_plot(plot_name)

    def change_active_plot_color(self):
        """Изменение цвета активного графика."""
        if not self.active_plot:
            return

        from PyQt5.QtWidgets import QColorDialog
        color = QColorDialog.getColor()
        if color.isValid():
            self.update_plot_color(self.active_plot, color.name())

    def update_plot_color(self, parameter_name, color):
        """Обновляет цвет графика, точки курсора и оси."""
        if parameter_name not in self.curves:
            return

        self.plot_colors[parameter_name] = color
        self.curves[parameter_name].setPen(pg.mkPen(color, width=3 if parameter_name == self.active_plot else 2))

        point = self.cursor_points.get(parameter_name)
        if point is not None:
            point.setPen(pg.mkPen(color, width=2))
            point.setBrush(pg.mkBrush(color))

        axis = self.left_axes.get(parameter_name)
        if axis is not None:
            width = 2 if parameter_name == self.active_plot else 1
            axis.setPen(pg.mkPen(color, width=width))
            axis.setTextPen(pg.mkPen(color, width=width))

        common_axis = self.common_right_axes.get(parameter_name)
        if common_axis is not None:
            width = 2 if parameter_name == self.active_plot else 1
            common_axis.setPen(pg.mkPen(color, width=width))
            common_axis.setTextPen(pg.mkPen(color, width=width))

        for curves in self.group_curves.values():
            if parameter_name in curves:
                curves[parameter_name].setPen(pg.mkPen(color, width=2))
        plot_widget = self.plot_widgets.get(parameter_name)
        if plot_widget is not None:
            plot_widget.setTitle(parameter_name, color=color, size='9pt')

    def update_plot_data(self, parameter_name, x_data, y_data):
        """Обновляет raw-данные графика и пересчитывает display-данные."""
        if parameter_name not in self.plot_data:
            return
        self.plot_data[parameter_name]['x_raw'] = np.asarray(x_data, dtype=float)
        self.plot_data[parameter_name]['y_raw'] = np.asarray(y_data, dtype=float)
        self._rebuild_display_data(parameter_name)

    # Фокусировка

    def auto_scale_y(self, parameter_name):
        """Фокусируется по Y на одном параметре."""
        self._set_y_focus_for_names([parameter_name])

    def reset_all_plots(self):
        """Фокусирует X на всех графиках, а Y — отдельно в каждой ячейке."""
        names = list(self.plot_data.keys())
        self._set_x_focus_for_names(names)
        self._set_y_focus_for_names(names)
        self.update_gps_date_label()
        self._refresh_all_axes()

    def reset_active_plot(self):
        """Фокусирует X/Y на активном графике."""
        if self.active_plot:
            self._set_focus_for_names([self.active_plot])

    # Мышь и синхронный курсор

    def on_mouse_moved(self, pos):
        """Обработка движения мыши над любой ячейкой."""
        source_name = self._cell_name_from_scene_pos(pos)
        if source_name is None:
            self._set_line_group_visible(self.cursor_lines, False)
            if self._is_export_click_mode_active():
                self._set_line_group_visible(self.export_preview_lines, False)
            for point in self.cursor_points.values():
                point.setVisible(False)
            return

        source_plot_item = self.common_plot_item if source_name == '__common__' else self.plot_items[source_name]
        mouse_point = source_plot_item.vb.mapSceneToView(pos)
        x_display_cursor = float(mouse_point.x())

        # Одна и та же вертикальная позиция X показывается во всех ячейках.
        self._set_line_group_pos(self.cursor_lines, x_display_cursor, True)

        if self._is_export_click_mode_active():
            self._set_line_group_pos(self.export_preview_lines, x_display_cursor, True)

        parameter_values = {}
        for name, data in self.plot_data.items():
            x_display = data['x_display']
            y_display = data['y_display']
            x_raw = data['x_raw']
            y_raw = data['y_raw']

            if len(x_display) == 0:
                continue

            idx = int(np.argmin(np.abs(x_display - x_display_cursor)))
            self.cursor_points[name].setData([x_display[idx]], [y_display[idx]])
            self.cursor_points[name].setVisible(True)
            self.cursor_points[name].setSize(12 if name == self.active_plot else 8)

            parameter_values[name] = {
                'x': float(x_raw[idx]),
                'y': float(y_raw[idx]),
                'display_x': float(x_display[idx]),
                'display_y': float(y_display[idx]),
                'distance': float(abs(x_display[idx] - x_display_cursor)),
            }

        emit_x = x_display_cursor
        if self.active_plot and self.active_plot in parameter_values:
            emit_x = parameter_values[self.active_plot]['x']
        elif parameter_values:
            emit_x = parameter_values[next(iter(parameter_values))]['x']

        self.cursor_position_changed.emit(float(emit_x), parameter_values)

    def _nearest_plot_name_in_common(self, scene_pos):
        """Находит ближайший график к клику в режиме общей области."""
        if self.common_plot_item is None:
            return None

        mouse_point = self.common_plot_item.vb.mapSceneToView(scene_pos)
        best_name = None
        best_dist2 = None

        for name, data in self.plot_data.items():
            x_display = data['x_display']
            y_display = data['y_display']
            if len(x_display) == 0:
                continue

            idx = int(np.argmin(np.abs(x_display - float(mouse_point.x()))))
            point_scene = self.common_plot_item.vb.mapViewToScene(
                QPointF(float(x_display[idx]), float(y_display[idx]))
            )

            dx = float(point_scene.x() - scene_pos.x())
            dy = float(point_scene.y() - scene_pos.y())
            dist2 = dx * dx + dy * dy

            if best_dist2 is None or dist2 < best_dist2:
                best_dist2 = dist2
                best_name = name

        if best_name is not None and best_dist2 is not None and best_dist2 < 25 ** 2:
            return best_name
        return None

    def on_mouse_clicked(self, event):
        """Обработка кликов: экспортный диапазон, активный график, фокус."""
        if event.double():
            self.reset_active_plot()
            return

        scene_pos = event.scenePos()
        source_name = self._cell_name_from_scene_pos(scene_pos)
        if source_name is None:
            return

        if self._is_export_click_mode_active() and event.button() == Qt.LeftButton:
            raw_time = self._nearest_raw_time_from_scene(scene_pos)
            if raw_time is not None:
                if self.export_selection_target in ('start', 'end'):
                    boundary_name = self.export_selection_target
                elif self.export_selection_enabled and self.export_start_time is None:
                    boundary_name = 'start'
                elif self.export_selection_enabled and self.export_end_time is None:
                    boundary_name = 'end'
                else:
                    start_distance = abs(raw_time - self.export_start_time)
                    end_distance = abs(raw_time - self.export_end_time)
                    boundary_name = 'start' if start_distance <= end_distance else 'end'
                self.set_export_boundary(boundary_name, raw_time)
            return

        if event.button() == Qt.LeftButton:
            if source_name == '__common__':
                closest_name = self._nearest_plot_name_in_common(scene_pos)
                if closest_name is not None:
                    self.set_active_plot(closest_name)
            else:
                self.set_active_plot(source_name)

    # Масштабирование и сдвиг display-данных

    def apply_data_scaling(self):
        """Применяет масштабирование только к отображению, не меняя raw-данные."""
        try:
            axis = self.scale_axis_combo.currentText()
            target = self.scale_target_combo.currentText()
            scale_factor = float(self.scale_factor_edit.text())
        except ValueError:
            QMessageBox.warning(self, 'Ошибка', 'Введите корректный числовой коэффициент')
            return

        if scale_factor == 0:
            QMessageBox.warning(self, 'Ошибка', 'Коэффициент масштабирования не может быть равен 0')
            return

        parameter_names = self._get_parameters_to_modify(target)
        if not parameter_names:
            QMessageBox.information(self, 'Информация', 'Нет графиков для масштабирования')
            return

        axis_key = 'x' if axis == 'Ось X' else 'y'
        ops_key = 'ops_x' if axis_key == 'x' else 'ops_y'

        for name in parameter_names:
            self.plot_data[name][ops_key].append(('scale', scale_factor))
            self._rebuild_display_data(name)

        self._refresh_all_axes()

    def reset_data_scaling(self):
        """Сбрасывает все операции масштабирования по выбранной оси."""
        axis = self.scale_axis_combo.currentText()
        target = self.scale_target_combo.currentText()
        parameter_names = self._get_parameters_to_modify(target)

        if not parameter_names:
            QMessageBox.information(self, 'Информация', 'Нет графиков для сброса масштаба')
            return

        axis_key = 'x' if axis == 'Ось X' else 'y'
        changed = False

        for name in parameter_names:
            ops_key = 'ops_x' if axis_key == 'x' else 'ops_y'
            old_len = len(self.plot_data[name][ops_key])
            self._remove_ops_of_kind(name, axis_key, 'scale')
            changed = changed or len(self.plot_data[name][ops_key]) != old_len

        if not changed:
            QMessageBox.information(self, 'Информация', 'Масштабирование по выбранной оси не применялось')
            return

        self._refresh_all_axes()

    def apply_data_shift(self):
        """Применяет сдвиг только к отображению, не меняя raw-данные."""
        try:
            axis = self.shift_axis_combo.currentText()
            target = self.shift_target_combo.currentText()
            shift_value = float(self.shift_value_edit.text())
        except ValueError:
            QMessageBox.warning(self, 'Ошибка', 'Введите корректное числовое значение смещения')
            return

        parameter_names = self._get_parameters_to_modify(target)
        if not parameter_names:
            QMessageBox.information(self, 'Информация', 'Нет графиков для сдвига')
            return

        axis_key = 'x' if axis == 'Ось X' else 'y'
        ops_key = 'ops_x' if axis_key == 'x' else 'ops_y'

        for name in parameter_names:
            self.plot_data[name][ops_key].append(('shift', shift_value))
            self._rebuild_display_data(name)

        self._refresh_all_axes()

    def reset_data_shift(self):
        """Сбрасывает все операции сдвига по выбранной оси."""
        axis = self.shift_axis_combo.currentText()
        target = self.shift_target_combo.currentText()
        parameter_names = self._get_parameters_to_modify(target)

        if not parameter_names:
            QMessageBox.information(self, 'Информация', 'Нет графиков для сброса сдвига')
            return

        axis_key = 'x' if axis == 'Ось X' else 'y'
        changed = False

        for name in parameter_names:
            ops_key = 'ops_x' if axis_key == 'x' else 'ops_y'
            old_len = len(self.plot_data[name][ops_key])
            self._remove_ops_of_kind(name, axis_key, 'shift')
            changed = changed or len(self.plot_data[name][ops_key]) != old_len

        if not changed:
            QMessageBox.information(self, 'Информация', 'Сдвиг по выбранной оси не применялся')
            return

        self._refresh_all_axes()

    # Время и GPS-подписи

    def _on_time_type_combo_changed(self, text: str):
        """Локальная обработка выбора типа времени."""
        self.time_type_changed.emit(text)
        self.update_gps_date_label()
        axes = list(self.time_axes.values())
        if self.common_time_axis is not None:
            axes.append(self.common_time_axis)

        for axis in axes:
            axis.picture = None
            axis.update()

    def update_gps_date_label(self):
        """Обновляет подпись даты GPS в верхней панели."""
        if self.time_type_combo.currentText() != 'GPS':
            self.gps_date_label.setText('')
            return

        if self._has_custom_x_transform():
            self.gps_date_label.setText('Дата GPS: display X')
            return

        if self.master_view_box is None:
            self.gps_date_label.setText('')
            return

        x_range = self.master_view_box.viewRange()[0]
        x_center = (x_range[0] + x_range[1]) / 2.0

        try:
            dt = datetime.fromtimestamp(x_center, tz=timezone.utc)
            self.gps_date_label.setText(f'Дата GPS: {dt.strftime("%Y-%m-%d")}')
        except Exception:
            self.gps_date_label.setText('')

    def set_time_offset(self, offset: float):
        """Сохраняет смещение TimeUS -> GPS и обновляет GPS-подписи."""
        self.time_offset = offset
        self.update_gps_date_label()
        axes = list(self.time_axes.values())
        if self.common_time_axis is not None:
            axes.append(self.common_time_axis)

        for axis in axes:
            axis.picture = None
            axis.update()
