"""Генерация таблицы стилей Qt из палитры скина."""
from __future__ import annotations

from PySide6.QtGui import QColor


def mix(c1: str, c2: str, t: float) -> str:
    a, b = QColor(c1), QColor(c2)
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t)).name()


def alpha(c: str, a: float) -> str:
    q = QColor(c)
    return f"rgba({q.red()},{q.green()},{q.blue()},{a:.2f})"


def build_qss(p: dict[str, str], scale: float = 1.0) -> str:
    fs = round(13 * scale)
    small = round(11 * scale)
    title = round(20 * scale)
    return f"""
* {{ font-family: "Segoe UI", "Bahnschrift", "Arial"; font-size: {fs}px; color: {p['text']}; }}
QMainWindow, #Root {{ background: {p['bg']}; }}
#Root {{ border: 1px solid {p['border']}; }}
QToolTip {{ background: {p['panel2']}; color: {p['text']}; border: 1px solid {p['accent']}; padding: 4px; }}

/* ---------- заголовок окна */
#TitleBar {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {mix(p['panel2'], p['bg'], 0.1)}, stop:1 {p['bg']});
            border-bottom: 1px solid {p['border']}; }}
#Logo {{ font-family: "Bahnschrift", "Segoe UI"; font-size: {round(18 * scale)}px; font-weight: 700; letter-spacing: 4px; color: {p['text']}; }}
#LogoAccent {{ font-family: "Bahnschrift", "Segoe UI"; font-size: {round(18 * scale)}px; font-weight: 700; letter-spacing: 4px; color: {p['accent']}; }}
#TitleBtn {{ background: transparent; border: none; min-width: 40px; min-height: 30px; font-size: {fs + 2}px; color: {p['muted']}; }}
#TitleBtn:hover {{ background: {p['panel2']}; color: {p['text']}; }}
#CloseBtn {{ background: transparent; border: none; min-width: 44px; min-height: 30px; font-size: {fs + 2}px; color: {p['muted']}; }}
#CloseBtn:hover {{ background: {p['accent']}; color: white; }}
#Badge {{ border: 1px solid {p['border']}; border-radius: 9px; padding: 1px 9px; font-size: {small}px; color: {p['muted']}; }}
#BadgeWarn {{ border: 1px solid {p['accent']}; border-radius: 9px; padding: 1px 9px; font-size: {small}px; color: {p['accent2']}; background: {alpha(p['accent'], 0.10)}; }}
#BadgeWarn:hover {{ background: {alpha(p['accent'], 0.25)}; }}

/* ---------- боковое меню */
#Sidebar {{ background: {p['panel']}; border-right: 1px solid {p['border']}; }}
#NavBtn {{ text-align: left; padding: 9px 14px 9px 12px; border: none; border-left: 3px solid transparent;
          background: transparent; color: {p['muted']}; font-size: {fs}px; }}
#NavBtn:hover {{ background: {p['panel2']}; color: {p['text']}; }}
#NavBtn:checked {{ border-left: 3px solid {p['accent']}; color: {p['text']};
                  background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {alpha(p['accent'], 0.22)}, stop:1 transparent); }}
#Version {{ color: {p['muted']}; font-size: {small}px; }}

/* ---------- страницы */
#PageTitle {{ font-family: "Bahnschrift", "Segoe UI"; font-size: {title}px; font-weight: 600; letter-spacing: 2px; }}
#PageSubtitle {{ color: {p['muted']}; font-size: {small + 1}px; }}
#StatusLine {{ color: {p['muted']}; font-size: {small}px; padding-top: 2px; }}
#Card {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 6px; }}
#CardTitle {{ color: {p['muted']}; font-size: {small}px; letter-spacing: 2px; font-weight: 600; }}
#Big {{ font-family: "Bahnschrift", "Segoe UI"; font-size: {round(28 * scale)}px; font-weight: 600; }}
#Muted {{ color: {p['muted']}; }}
#Accent {{ color: {p['accent']}; font-weight: 600; }}
#HLine {{ background: {p['border']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- кнопки */
QPushButton {{ background: {p['panel2']}; border: 1px solid {p['border']}; border-radius: 4px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {p['accent']}; background: {mix(p['panel2'], p['accent'], 0.08)}; }}
QPushButton:pressed {{ background: {mix(p['panel2'], p['accent'], 0.25)}; }}
QPushButton:disabled {{ color: {p['muted']}; border-color: {p['panel2']}; }}
QPushButton#Primary {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {p['accent']}, stop:1 {mix(p['accent'], '#000000', 0.25)});
                      border: 1px solid {p['accent2']}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {p['accent2']}; }}
QPushButton#Primary:disabled {{ background: {p['panel2']}; color: {p['muted']}; border-color: {p['border']}; }}

/* ---------- поля ввода */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {p['graph_bg']}; border: 1px solid {p['border']};
       border-radius: 4px; padding: 5px 8px; selection-background-color: {p['accent']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {p['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ width: 0; height: 0; border-left: 5px solid transparent; border-right: 5px solid transparent;
       border-top: 6px solid {p['muted']}; margin-right: 8px; }}
QComboBox::down-arrow:hover {{ border-top-color: {p['accent']}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
       width: 18px; border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ width: 0; height: 0; border-left: 4px solid transparent;
       border-right: 4px solid transparent; border-bottom: 5px solid {p['muted']}; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ width: 0; height: 0; border-left: 4px solid transparent;
       border-right: 4px solid transparent; border-top: 5px solid {p['muted']}; }}
QSpinBox::up-arrow:hover, QSpinBox::down-arrow:hover {{ border-bottom-color: {p['accent']}; border-top-color: {p['accent']}; }}
QComboBox QAbstractItemView {{ background: {p['panel2']}; border: 1px solid {p['border']}; selection-background-color: {p['accent']}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {p['border']}; border-radius: 3px; background: {p['graph_bg']}; }}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent2']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {p['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; background: {p['text']}; border: 2px solid {p['accent']}; }}

/* ---------- таблицы */
QTableView, QTreeWidget, QListWidget, QTextEdit, QPlainTextEdit {{ background: {p['graph_bg']}; alternate-background-color: {mix(p['graph_bg'], p['panel'], 0.55)};
       border: 1px solid {p['border']}; border-radius: 4px; gridline-color: {p['border']};
       selection-background-color: {alpha(p['accent'], 0.35)}; selection-color: {p['text']}; outline: 0; }}
QHeaderView::section {{ background: {p['panel2']}; color: {p['muted']}; border: none; border-right: 1px solid {p['border']};
       border-bottom: 1px solid {p['accent']}; padding: 5px 8px; font-size: {small}px; font-weight: 600; letter-spacing: 1px; }}
QTreeWidget::item, QListWidget::item {{ padding: 3px; }}
QTreeView::indicator, QListView::indicator, QTableView::indicator {{ width: 14px; height: 14px; border: 1px solid {p['muted']};
       border-radius: 3px; background: {p['graph_bg']}; }}
QTreeView::indicator:checked, QListView::indicator:checked, QTableView::indicator:checked {{ background: {p['accent']};
       border-color: {p['accent2']}; }}
QTreeView::indicator:indeterminate {{ background: {alpha(p['accent'], 0.45)}; border-color: {p['accent']}; }}
QTableView::item {{ padding: 0 6px; }}

/* ---------- прокрутка */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {p['border']}; border-radius: 4px; min-width: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- прочее */
QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 4px; top: -1px; background: {p['panel']}; }}
QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: 7px 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['accent']}; }}
QTabBar::tab:hover {{ color: {p['text']}; }}
QProgressBar {{ background: {p['graph_bg']}; border: 1px solid {p['border']}; border-radius: 3px; text-align: center; height: 14px; font-size: {small}px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {mix(p['accent'], '#000000', 0.3)}, stop:1 {p['accent']}); border-radius: 2px; }}
QMenu {{ background: {p['panel2']}; border: 1px solid {p['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {alpha(p['accent'], 0.35)}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
QMessageBox, QDialog, QInputDialog {{ background: {p['panel']}; }}
QGroupBox {{ border: 1px solid {p['border']}; border-radius: 6px; margin-top: 14px; padding-top: 8px; background: {p['panel']}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p['muted']}; font-weight: 600; letter-spacing: 1px; }}
"""
