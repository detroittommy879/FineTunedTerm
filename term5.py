#!/usr/bin/env python
# filepath: t:\z-test1\termbuttons\term_enhanced.py

import os
import sys
import subprocess
import shutil
import logging
import time
import datetime
import re
import functools
import json # For settings

from PyQt6.QtCore import Qt, QProcess, QSize, pyqtSignal, QProcessEnvironment, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QGroupBox, QLabel, QSplitter, QTextEdit, QScrollArea,
    QTabWidget, QSizePolicy, QFileDialog, QRadioButton, QStackedWidget,
    QStatusBar, QDialog, QLineEdit, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QMessageBox, QFontDialog # Added for font and presets dialog
)
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat, QMouseEvent, QFocusEvent, QAction # Added QAction

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler(f"termlog_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- Default Settings ---
DEFAULT_FONT = QFont("Courier New", 10)
DEFAULT_PRESETS = [
    {"label": "List Files", "command": "ls -lah"},
    {"label": "Git Status", "command": "git status"},
    {"label": "Disk Usage", "command": "df -h"},
]
SETTINGS_FILE_NAME = "term_enhanced_settings.json"

# ANSI palette...
DEFAULT_FG_COLOR = QColor("white")
DEFAULT_BG_COLOR = QColor("black")
ANSI_SGR_CODES_TO_COLORS = {
    30: QColor("black"), 31: QColor("#CD0000"), 32: QColor("#00CD00"),
    33: QColor("#CDCD00"), 34: QColor("#0000EE"), 35: QColor("#CD00CD"),
    36: QColor("#00CDCD"), 37: QColor("#E5E5E5"), 39: DEFAULT_FG_COLOR,
    40: QColor("black"), 41: QColor("#CD0000"), 42: QColor("#00CD00"),
    43: QColor("#CDCD00"), 44: QColor("#0000EE"), 45: QColor("#CD00CD"),
    46: QColor("#00CDCD"), 47: QColor("#E5E5E5"), 49: DEFAULT_BG_COLOR,
    90: QColor("#7F7F7F"), 91: QColor("red"), 92: QColor("green"),
    93: QColor("yellow"), 94: QColor("blue"), 95: QColor("magenta"),
    96: QColor("cyan"), 97: QColor("white"),
    100: QColor("#7F7F7F"), 101: QColor("red"), 102: QColor("green"),
    103: QColor("yellow"), 104: QColor("blue"), 105: QColor("magenta"),
    106: QColor("cyan"), 107: QColor("white"),
}

class TerminalWidget(QTextEdit):
    commandEntered = pyqtSignal(str)
    titleChanged = pyqtSignal(str)
    terminalFocusGained = pyqtSignal(QWidget) # Changed to QWidget for broader compatibility

    ansi_escape_regex = re.compile(
        r'\x1b(?:'
         r'\[([\d;?]*)m'  # SGR
        r'|\]([012]);([^\x07\x1b]*)(?:\x07|\x1b\\)'  # OSC
        r'|\[([\d;]*)([HJK])'  # CSI cursor/erase
        r'|([()][012AB])'  # Character set
        r'|([ENOM])'      # Other modes
        r'|=|>)'          # Keypad modes
    )

    def __init__(self, parent=None, terminal_id="Unknown", initial_font=None):
        super().__init__(parent)
        self.terminal_id = terminal_id
        self.setReadOnly(False)
        self.setAcceptRichText(False)
        
        self.current_font = initial_font if initial_font else QFont(DEFAULT_FONT) # Use provided font
        self.setFont(self.current_font)

        self.setStyleSheet(f"background-color:{DEFAULT_BG_COLOR.name()};"
                           f"color:{DEFAULT_FG_COLOR.name()};")
        self.current_format = QTextCharFormat()
        self._reset_char_format_to_default() # This will use current font's properties potentially
        self.setCurrentCharFormat(self.current_format)
        
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(50) 

        self.input_start_pos = 0
        self.process = QProcess(self)
        
        # Set TERM environment variable for better compatibility with shell apps
        proc_env = QProcessEnvironment.systemEnvironment()
        proc_env.insert("TERM", "xterm-256color") 
        self.process.setProcessEnvironment(proc_env)

        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.handle_process_error)
        self.process_running = False
        self.restart_count = 0
        self.last_started = 0
        QTimer.singleShot(0, self.start_process)

    def apply_font(self, font: QFont):
        self.current_font = font
        self.setFont(self.current_font)
        # Re-apply default format to ensure font color/style consistency
        # (though SGR codes will override parts of this)
        self._reset_char_format_to_default()
        self.setCurrentCharFormat(self.current_format)
        # If you want to reformat existing text, that's more complex.
        # This primarily affects new text and the default appearance.

    def _reset_char_format_to_default(self):
        f = self.current_format
        f.setForeground(DEFAULT_FG_COLOR)
        f.setBackground(DEFAULT_BG_COLOR)
        # Use current font's weight, italic settings as base, unless SGR overrides
        f.setFontWeight(self.current_font.weight())
        f.setFontItalic(self.current_font.italic())
        f.setFontUnderline(self.current_font.underline())
        # Explicitly reset SGR sensitive properties
        f.setFontWeight(QFont.Weight.Normal)
        f.setFontItalic(False)
        f.setFontUnderline(False)


    def _apply_sgr_code(self, code):
        if code == 0:
            self._reset_char_format_to_default()
        elif code == 1:
            self.current_format.setFontWeight(QFont.Weight.Bold)
        elif code == 3:
            self.current_format.setFontItalic(True)
        elif code == 4:
            self.current_format.setFontUnderline(True)
        elif code in ANSI_SGR_CODES_TO_COLORS:
            color = ANSI_SGR_CODES_TO_COLORS[code]
            if (30 <= code <= 37) or (90 <= code <= 97) or code == 39:
                self.current_format.setForeground(color)
            elif (40 <= code <= 47) or (100 <= code <= 107) or code == 49:
                self.current_format.setBackground(color)
        elif code == 38: 
            pass 
        elif code == 48:
            pass

    def append_ansi_text(self, text: str):
        self.moveCursor(QTextCursor.MoveOperation.End)
        last = 0
        for m in self.ansi_escape_regex.finditer(text):
            s, e = m.span()
            if s > last:
                seg = text[last:s]
                self.setCurrentCharFormat(self.current_format)
                self.insertPlainText(seg)
            
            sgr, osc_t, osc_c, csi_p, csi_l, cs, sc = m.groups()

            if sgr is not None: 
                parts = sgr.split(';') if sgr else ['0'] 
                idx = 0
                while idx < len(parts):
                    part = parts[idx]
                    if not part: 
                        code = 0 
                    else:
                        try:
                            code = int(part)
                        except ValueError:
                            idx +=1
                            continue 
                    
                    if code == 38 or code == 48: 
                        if idx + 1 < len(parts):
                            try:
                                color_mode = int(parts[idx+1])
                                if color_mode == 5 and idx + 2 < len(parts): 
                                    idx += 2 
                                elif color_mode == 2 and idx + 4 < len(parts): 
                                    idx += 4
                            except ValueError:
                                pass 
                        else: 
                            pass
                    else: 
                        self._apply_sgr_code(code)
                    idx += 1
            elif osc_t and osc_c: 
                if osc_t in ('0', '1', '2'): 
                    self.titleChanged.emit(osc_c)
            last = e
            
        if last < len(text):
            tail = text[last:]
            self.setCurrentCharFormat(self.current_format)
            self.insertPlainText(tail)

        self.moveCursor(QTextCursor.MoveOperation.End)
        self.ensureCursorVisible()
        self.input_start_pos = self.textCursor().position()

    def handle_process_error(self, err: QProcess.ProcessError):
        msg = f"⚠️ ProcessError ({err}): {self.process.errorString()}\n"
        logger.error(f"[{self.terminal_id}] Process error: {self.process.errorString()} (code: {err})")
        self.append_ansi_text(msg)

    def start_process(self):
        if self.process_running:
            logger.warning(f"[{self.terminal_id}] Process already running.")
            return

        now = time.time()
        if 0 < (now - self.last_started) < 2: 
            self.restart_count += 1
            if self.restart_count > 3:
                logger.error(f"[{self.terminal_id}] Too many restarts. Stopping.")
                self.append_ansi_text(f"\x1b[31m⚠️ [{self.terminal_id}] Too many restarts. Shell disabled.\x1b[0m\n")
                return
        else:
            self.restart_count = 0
        self.last_started = now

        shell_executable = None
        args = []

        bash_exe = shutil.which("bash") 
        wsl_exe = shutil.which("wsl.exe")

        if bash_exe:
            shell_executable = bash_exe
            # For Git Bash/MSYS, --login without -i might reduce "ioctl" messages.
            # If it's WSL's own bash.exe (older method), this might be less ideal than wsl.exe.
            args = ["--login"] 
            logger.info(f"[{self.terminal_id}] Prioritizing bash: {bash_exe} with args: {args}")
        elif wsl_exe:
            shell_executable = wsl_exe
            args = ["-e", "bash", "--login", "-i"] 
            logger.info(f"[{self.terminal_id}] Using WSL with bash as fallback.")
        else:
            shell_executable = shutil.which("powershell.exe") or shutil.which("cmd.exe")
            if shell_executable:
                logger.info(f"[{self.terminal_id}] Using fallback shell: {shell_executable}.")
        
        if not shell_executable:
            logger.error(f"[{self.terminal_id}] No suitable shell found (WSL, bash, powershell, cmd).")
            self.append_ansi_text(f"\x1b[31m⚠️ [{self.terminal_id}] No shell found.\x1b[0m\n")
            return

        logger.info(f"[{self.terminal_id}] Starting shell: {shell_executable} {' '.join(args)}")
        self.append_ansi_text(f"[{self.terminal_id}] Starting {os.path.basename(shell_executable)} {' '.join(args)}...\n")
        
        self.process.start(shell_executable, args)
        if not self.process.waitForStarted(3000):
            logger.error(f"[{self.terminal_id}] Failed to start shell: {shell_executable}")
            self.append_ansi_text(f"\x1b[31m⚠️ Failed to start shell: {os.path.basename(shell_executable)}.\x1b[0m\n")
            self.process_running = False
        else:
            logger.info(f"[{self.terminal_id}] Shell started successfully.")
            self.process_running = True

    def read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode(sys.stdout.encoding or 'utf-8', errors='replace')
        self.append_ansi_text(data)

    def read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode(sys.stderr.encoding or 'utf-8', errors='replace')
        self.append_ansi_text(data)

    def process_finished(self, exitCode, exitStatus: QProcess.ExitStatus):
        status_str = "crashed" if exitStatus == QProcess.ExitStatus.CrashExit else "finished"
        message = f"\n\x1b[33m[{self.terminal_id}] Shell process {status_str} with code {exitCode}.\x1b[0m\n"
        logger.info(f"[{self.terminal_id}] Process finished. ExitCode: {exitCode}, Status: {exitStatus}")
        self.append_ansi_text(message)
        self.process_running = False

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_C and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.process_running:
                self.process.write(b'\x03') 
            return

        self.moveCursor(QTextCursor.MoveOperation.End)
        current_cursor_pos = self.textCursor().position()

        if ev.key() == Qt.Key.Key_Backspace:
            if current_cursor_pos > self.input_start_pos:
                super().keyPressEvent(ev) 
            return

        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            doc = self.document()
            txt_to_send = doc.toPlainText()[self.input_start_pos:]
            cmd = txt_to_send.rstrip('\n')

            if self.process_running:
                self.process.write((cmd + "\n").encode(sys.stdout.encoding or 'utf-8'))
            else:
                self.append_ansi_text("\x1b[31mShell not running.\x1b[0m\n")
            
            super().keyPressEvent(ev) 
            self.input_start_pos = self.textCursor().position() 
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, event: QMouseEvent):
        super().mousePressEvent(event)

    def send_command(self, cmd, append_enter=True):
        if self.process_running:
            self.moveCursor(QTextCursor.MoveOperation.End) 
            data = cmd + ('\n' if append_enter else '')
            self.process.write(data.encode(sys.stdout.encoding or 'utf-8'))
            logger.debug(f"[{self.terminal_id}] Sent command: {cmd.strip()}")
        else:
            self.append_ansi_text(f"\x1b[31m[{self.terminal_id}] ❌ Not running. Cannot send command.\x1b[0m\n")

    def closeEvent(self, event):
        logger.info(f"[{self.terminal_id}] Close event received. Killing process.")
        if self.process_running and self.process.state() == QProcess.ProcessState.Running:
            self.process.kill()
            self.process.waitForFinished(1000) 
        super().closeEvent(event)

    def focusInEvent(self, event: QFocusEvent):
        super().focusInEvent(event)
        logger.debug(f"[{self.terminal_id}] Focus In Event")
        self.terminalFocusGained.emit(self) 


class EditPresetsDialog(QDialog):
    def __init__(self, presets, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Preset Commands")
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Button Label", "Command"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        button_layout = QHBoxLayout()
        btn_add = QPushButton("Add Row")
        btn_add.clicked.connect(self.add_row)
        btn_remove = QPushButton("Remove Selected Row")
        btn_remove.clicked.connect(self.remove_row)
        button_layout.addWidget(btn_add)
        button_layout.addWidget(btn_remove)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        dialog_buttons = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        dialog_buttons.addStretch()
        dialog_buttons.addWidget(btn_ok)
        dialog_buttons.addWidget(btn_cancel)
        layout.addLayout(dialog_buttons)

        self.load_presets(presets)

    def load_presets(self, presets):
        self.table.setRowCount(0) # Clear table
        for preset in presets:
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)
            self.table.setItem(row_position, 0, QTableWidgetItem(preset.get("label", "")))
            self.table.setItem(row_position, 1, QTableWidgetItem(preset.get("command", "")))

    def add_row(self):
        row_position = self.table.rowCount()
        self.table.insertRow(row_position)
        self.table.setItem(row_position, 0, QTableWidgetItem("New Label"))
        self.table.setItem(row_position, 1, QTableWidgetItem("new_command"))

    def remove_row(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            self.table.removeRow(current_row)

    def get_presets(self):
        presets = []
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 0)
            command_item = self.table.item(row, 1)
            presets.append({
                "label": label_item.text() if label_item else "",
                "command": command_item.text() if command_item else ""
            })
        return presets


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Terminal Emulator")
        self.resize(900, 700)
        self.setStatusBar(QStatusBar())

        self.settings_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SETTINGS_FILE_NAME)
        self.current_font = QFont(DEFAULT_FONT)
        self.preset_commands = list(DEFAULT_PRESETS) # Use a copy
        self._load_settings()

        self._create_menus()

        self.terminals = [] 
        self.terminal_counter = 0
        self.last_focused_terminal: TerminalWidget | None = None

        topbar = QWidget()
        tlay = QHBoxLayout(topbar)
        tlay.setContentsMargins(0,0,0,0)
        tlay.addWidget(QLabel("View:"))
        self.rb_stack = QRadioButton("Stacked")
        self.rb_tabs = QRadioButton("Tabbed")
        tlay.addWidget(self.rb_stack)
        tlay.addWidget(self.rb_tabs)
        tlay.addStretch()
        self.btn_add = QPushButton("+")
        self.btn_add.setFixedSize(QSize(25, 25))
        tlay.addWidget(self.btn_add)

        self.stacker = QSplitter(Qt.Orientation.Vertical)
        self.tabber = QTabWidget()
        self.tabber.setTabsClosable(True)
        
        self.views = QStackedWidget()
        self.views.addWidget(self.stacker)  
        self.views.addWidget(self.tabber)   

        self.rb_stack.toggled.connect(self._on_view_mode_changed)
        self.rb_tabs.toggled.connect(self._on_view_mode_changed)
        self.btn_add.clicked.connect(self._on_add_terminal_clicked)
        self.tabber.tabCloseRequested.connect(self._on_tab_close_requested)

        main_widget = QWidget()
        mlay = QVBoxLayout(main_widget)
        mlay.addWidget(topbar)
        mlay.addWidget(self.views, 1)
        
        self.commands_group = QGroupBox("Preset Commands")
        self.commands_layout = QHBoxLayout() 
        self.commands_group.setLayout(self.commands_layout)
        self.commands_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed) # Fixed vertical, preferred horizontal
        self._populate_preset_buttons() # Populate based on loaded/default presets
        
        mlay.addWidget(self.commands_group)
        self.setCentralWidget(main_widget)

        self.rb_stack.setChecked(True) 
        self._add_new_terminal_instance() 
        self._add_new_terminal_instance() 
        self._refresh_active_view_layout()
        
        if self.terminals:
            if self.views.currentIndex() == 0 and self.stacker.count() > 0:
                 self.stacker.widget(self.stacker.count()-1).setFocus()
            elif self.views.currentIndex() == 1 and self.tabber.count() > 0:
                 self.tabber.currentWidget().setFocus()
        
        self._apply_font_to_all_terminals() # Apply loaded/default font

    def _create_menus(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        exit_action = QAction("&Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        settings_menu = menu_bar.addMenu("&Settings")
        font_action = QAction("Change &Font...", self)
        font_action.triggered.connect(self._show_font_dialog)
        settings_menu.addAction(font_action)

        presets_action = QAction("Edit &Presets...", self)
        presets_action.triggered.connect(self._show_edit_presets_dialog)
        settings_menu.addAction(presets_action)

    def _load_settings(self):
        try:
            if os.path.exists(self.settings_file_path):
                with open(self.settings_file_path, 'r') as f:
                    settings = json.load(f)
                
                font_family = settings.get("font_family", DEFAULT_FONT.family())
                font_size = settings.get("font_size", DEFAULT_FONT.pointSize())
                font_weight_str = settings.get("font_weight", "Normal") # Store as string
                font_italic = settings.get("font_italic", DEFAULT_FONT.italic())

                # Map string weight to QFont.Weight enum
                weight_map = {
                    "Thin": QFont.Weight.Thin, "ExtraLight": QFont.Weight.ExtraLight,
                    "Light": QFont.Weight.Light, "Normal": QFont.Weight.Normal,
                    "Medium": QFont.Weight.Medium, "DemiBold": QFont.Weight.DemiBold,
                    "Bold": QFont.Weight.Bold, "ExtraBold": QFont.Weight.ExtraBold,
                    "Black": QFont.Weight.Black
                }
                font_weight = weight_map.get(font_weight_str, QFont.Weight.Normal)

                self.current_font = QFont(font_family, font_size)
                self.current_font.setWeight(font_weight)
                self.current_font.setItalic(font_italic)
                
                loaded_presets = settings.get("presets", DEFAULT_PRESETS)
                if isinstance(loaded_presets, list) and all(isinstance(p, dict) for p in loaded_presets):
                    self.preset_commands = loaded_presets
                else:
                    self.preset_commands = list(DEFAULT_PRESETS) # Fallback
                logger.info(f"Settings loaded from {self.settings_file_path}")
            else:
                logger.info("Settings file not found. Using defaults.")
                self.current_font = QFont(DEFAULT_FONT)
                self.preset_commands = list(DEFAULT_PRESETS)
        except Exception as e:
            logger.error(f"Error loading settings: {e}. Using defaults.")
            self.current_font = QFont(DEFAULT_FONT)
            self.preset_commands = list(DEFAULT_PRESETS)

    def _save_settings(self):
        # Create a reverse map for QFont.Weight to string
        weight_to_str_map = {v: k for k, v in {
            "Thin": QFont.Weight.Thin, "ExtraLight": QFont.Weight.ExtraLight,
            "Light": QFont.Weight.Light, "Normal": QFont.Weight.Normal,
            "Medium": QFont.Weight.Medium, "DemiBold": QFont.Weight.DemiBold,
            "Bold": QFont.Weight.Bold, "ExtraBold": QFont.Weight.ExtraBold,
            "Black": QFont.Weight.Black
        }.items()}
        font_weight_str = weight_to_str_map.get(self.current_font.weight(), "Normal")

        settings = {
            "font_family": self.current_font.family(),
            "font_size": self.current_font.pointSize(),
            "font_weight": font_weight_str,
            "font_italic": self.current_font.italic(),
            "presets": self.preset_commands
        }
        try:
            with open(self.settings_file_path, 'w') as f:
                json.dump(settings, f, indent=4)
            logger.info(f"Settings saved to {self.settings_file_path}")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    def _show_font_dialog(self):
        font, ok = QFontDialog.getFont(
            self.current_font, self, "Select Font",
            QFontDialog.FontDialogOption.MonospacedFonts
        )
        if ok:
            self.current_font = font
            self._apply_font_to_all_terminals()
            self._save_settings()
            logger.info(f"Font changed to: {font.family()} {font.pointSize()}pt")

    def _apply_font_to_all_terminals(self):
        for term in self.terminals:
            term.apply_font(self.current_font)

    def _show_edit_presets_dialog(self):
        dialog = EditPresetsDialog(list(self.preset_commands), self) # Pass a copy
        if dialog.exec():
            self.preset_commands = dialog.get_presets()
            self._populate_preset_buttons()
            self._save_settings()
            logger.info("Preset commands updated.")

    def _clear_layout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                else: # If it's a sub-layout
                    sub_layout = item.layout()
                    if sub_layout is not None:
                        self._clear_layout(sub_layout)


    def _populate_preset_buttons(self):
        self._clear_layout(self.commands_layout) # Clear existing buttons
        
        for preset in self.preset_commands:
            btn_text = preset.get("label", "Cmd")
            cmd_str = preset.get("command", "")
            button = QPushButton(btn_text)
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            button.clicked.connect(functools.partial(self._send_preset_command, cmd_str))
            self.commands_layout.addWidget(button)
        
        self.commands_layout.addStretch() # Push buttons to the left

    def _on_terminal_focus_gained(self, terminal_widget: TerminalWidget):
        if isinstance(terminal_widget, TerminalWidget):
            self.last_focused_terminal = terminal_widget
            logger.debug(f"MainWindow: Last focused terminal updated to {terminal_widget.terminal_id}")

    def _on_view_mode_changed(self):
        sender = self.sender()
        if not sender.isChecked(): return
        new_view_index = 0 if sender == self.rb_stack else 1
        if self.views.currentIndex() != new_view_index:
            self.views.setCurrentIndex(new_view_index)
            self._refresh_active_view_layout()
            if new_view_index == 1 and self.tabber.count() > 0: 
                current_tab_widget = self.tabber.currentWidget()
                if current_tab_widget: current_tab_widget.setFocus()
            elif new_view_index == 0 and len(self.terminals) > 0: 
                focused = False
                if self.last_focused_terminal and self.last_focused_terminal in self.terminals and \
                   any(self.stacker.widget(i) == self.last_focused_terminal for i in range(self.stacker.count())):
                    self.last_focused_terminal.setFocus()
                    focused = True
                if not focused and self.stacker.count() > 0:
                    self.stacker.widget(self.stacker.count() -1).setFocus()

    def _add_new_terminal_instance(self) -> TerminalWidget:
        self.terminal_counter += 1
        tid = f"Term{self.terminal_counter}"
        term = TerminalWidget(terminal_id=tid, initial_font=self.current_font) # Pass current font
        term.titleChanged.connect(lambda title, widget=term: self._update_terminal_title(widget, title))
        term.terminalFocusGained.connect(self._on_terminal_focus_gained)
        term.setProperty("current_title", tid) 
        self.terminals.append(term)
        return term

    def _on_add_terminal_clicked(self):
        new_term = self._add_new_terminal_instance()
        self._refresh_active_view_layout()
        if self.views.currentIndex() == 0: new_term.setFocus()
        else: 
            idx = self.tabber.indexOf(new_term)
            if idx != -1: self.tabber.setCurrentIndex(idx)
            new_term.setFocus()

    def _on_tab_close_requested(self, index: int):
        if len(self.terminals) <=1: return
        widget_to_close = self.tabber.widget(index)
        if widget_to_close and isinstance(widget_to_close, TerminalWidget):
            self._close_terminal_widget(widget_to_close)
        if self.views.currentWidget() == self.tabber and self.tabber.count() > 0:
            current_tab_w = self.tabber.currentWidget()
            if current_tab_w: current_tab_w.setFocus()

    def _close_terminal_widget(self, term_widget: TerminalWidget):
        if term_widget == self.last_focused_terminal: self.last_focused_terminal = None
        if term_widget in self.terminals:
            logger.info(f"Closing terminal: {term_widget.terminal_id}")
            try: term_widget.terminalFocusGained.disconnect(self._on_terminal_focus_gained)
            except TypeError: pass # Was not connected
            term_widget.close() 
            self.terminals.remove(term_widget)
            term_widget.deleteLater() 
            self._refresh_active_view_layout()
        else: logger.warning(f"Attempted to close a widget not in self.terminals: {term_widget}")

    def _update_terminal_title(self, widget: TerminalWidget, title: str):
        name = title.strip() or widget.terminal_id
        widget.setProperty("current_title", name) 
        if self.views.currentWidget() == self.tabber:
            idx = self.tabber.indexOf(widget)
            if idx != -1: self.tabber.setTabText(idx, name[:30]) 

    def _refresh_active_view_layout(self):
        try: self.tabber.tabCloseRequested.disconnect(self._on_tab_close_requested)
        except TypeError: pass

        # Detach widgets
        for term_widget in list(self.terminals): 
            if term_widget.parent() == self.stacker or self.tabber.indexOf(term_widget) != -1:
                 term_widget.setParent(None) # Will be removed from old layout

        # Clear views
        while self.tabber.count() > 0: self.tabber.removeTab(0)
        
        # Clearing stacker: QSplitter doesn't have simple clear. Iterate and remove by setting parent.
        temp_stacker_widgets = [self.stacker.widget(i) for i in range(self.stacker.count())]
        for w in temp_stacker_widgets:
            w.setParent(None) # Removes from splitter

        # Re-populate
        active_view_idx = self.views.currentIndex()
        if active_view_idx == 0: 
            for term_widget in self.terminals:
                self.stacker.addWidget(term_widget)
                term_widget.show() 
            if self.stacker.count() > 0:
                total_size = self.stacker.height() if self.stacker.orientation() == Qt.Orientation.Vertical else self.stacker.width()
                if total_size > 0 and self.stacker.count() > 0:
                    size_per_widget = total_size // self.stacker.count()
                    self.stacker.setSizes([size_per_widget] * self.stacker.count())
        else: 
            for term_widget in self.terminals:
                title = term_widget.property("current_title") or term_widget.terminal_id
                self.tabber.addTab(term_widget, title[:30])
                term_widget.show() 

        self.tabber.tabCloseRequested.connect(self._on_tab_close_requested)
        if not any(t.hasFocus() for t in self.terminals if t.isVisible()) and len(self.terminals) == 0:
             self.setFocus()

    def _get_active_terminal(self) -> TerminalWidget | None:
        current_view_widget = self.views.currentWidget()
        if current_view_widget == self.tabber:
            widget_in_current_tab = self.tabber.currentWidget()
            if isinstance(widget_in_current_tab, TerminalWidget) and widget_in_current_tab in self.terminals:
                return widget_in_current_tab
        elif current_view_widget == self.stacker:
            focused_widget = QApplication.instance().focusWidget()
            if isinstance(focused_widget, TerminalWidget) and focused_widget in self.terminals and \
               any(self.stacker.widget(i) == focused_widget for i in range(self.stacker.count())):
                return focused_widget
            if self.last_focused_terminal and self.last_focused_terminal in self.terminals and \
               any(self.stacker.widget(i) == self.last_focused_terminal for i in range(self.stacker.count())):
                return self.last_focused_terminal
            if self.stacker.count() > 0: # Fallback to bottom-most visible in stacker
                # Iterate terminals to find one that's in the stacker, prefer last one added (often bottom)
                for term_widget in reversed(self.terminals): # Check most recent ones first
                    if any(self.stacker.widget(i) == term_widget for i in range(self.stacker.count())):
                        return term_widget
        return None

    def _send_preset_command(self, command_text: str):
        active_term = self._get_active_terminal()
        if active_term:
            logger.info(f"Sending preset command '{command_text}' to {active_term.terminal_id}")
            active_term.send_command(command_text)
            active_term.setFocus() 
            self.statusBar().showMessage(f"Sent '{command_text}' to {active_term.terminal_id}", 2000)
        else:
            logger.warning("No active terminal found to send preset command.")
            self.statusBar().showMessage("No active terminal to send command.", 3000)

    def closeEvent(self, event):
        logger.info("MainWindow close event. Closing all terminals.")
        self._save_settings() # Save settings on close
        for term_widget in list(self.terminals):
            self._close_terminal_widget(term_widget)
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec())