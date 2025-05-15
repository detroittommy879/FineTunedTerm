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
import functools # Added for functools.partial

from PyQt6.QtCore import Qt, QProcess, QSize, pyqtSignal, QProcessEnvironment, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QGroupBox, QLabel, QSplitter, QTextEdit, QScrollArea,
    QTabWidget, QSizePolicy, QFileDialog, QRadioButton, QStackedWidget,
    QStatusBar # Added for status messages
)
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat, QMouseEvent, QFocusEvent # Added QFocusEvent

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
    """A minimal ANSI-aware terminal, with input-guarded backspace and cursor locked to bottom for input."""
    commandEntered = pyqtSignal(str)
    titleChanged = pyqtSignal(str)
    # Signal to notify MainWindow when this terminal gains focus.
    # Using TerminalWidget directly as type hint, assuming it's resolvable in this single-file script.
    terminalFocusGained = pyqtSignal('PyQt_PyObject') # Use 'PyQt_PyObject' or object for robustness if TerminalWidget type hint causes issues early

    ansi_escape_regex = re.compile(
        r'\x1b(?:'
         r'\[([\d;?]*)m'  # SGR
        r'|\]([012]);([^\x07\x1b]*)(?:\x07|\x1b\\)'  # OSC
        r'|\[([\d;]*)([HJK])'  # CSI cursor/erase
        r'|([()][012AB])'  # Character set
        r'|([ENOM])'      # Other modes
        r'|=|>)'          # Keypad modes
    )

    def __init__(self, parent=None, terminal_id="Unknown"):
        super().__init__(parent)
        self.terminal_id = terminal_id
        self.setReadOnly(False)
        self.setAcceptRichText(False)
        self.setFont(QFont("Courier New", 10))
        self.setStyleSheet(f"background-color:{DEFAULT_BG_COLOR.name()};"
                           f"color:{DEFAULT_FG_COLOR.name()};")
        self.current_format = QTextCharFormat()
        self._reset_char_format_to_default()
        self.setCurrentCharFormat(self.current_format)
        
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(50) # Ensure it has some minimum height in splitter

        self.input_start_pos = 0
        self.process = QProcess(self)
        self.process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.handle_process_error)
        self.process_running = False
        self.restart_count = 0
        self.last_started = 0
        QTimer.singleShot(0, self.start_process) # Start process after event loop starts

    def _reset_char_format_to_default(self):
        f = self.current_format
        f.setForeground(DEFAULT_FG_COLOR)
        f.setBackground(DEFAULT_BG_COLOR)
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
                                else: 
                                    pass
                            except ValueError: # Malformed number for color_mode
                                pass # Skip malformed part
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
            logger.warning(f"[{self.terminal_id}] Process already running, not starting again.")
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

        shell_executable = shutil.which("bash") or shutil.which("powershell.exe") or shutil.which("cmd.exe")
        if not shell_executable:
            logger.error(f"[{self.terminal_id}] No suitable shell found.")
            self.append_ansi_text(f"\x1b[31m⚠️ [{self.terminal_id}] No shell found (bash, powershell, cmd).\x1b[0m\n")
            return

        args = []
        if 'bash' in shell_executable.lower():
            args = ["--login", "-i"]

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
        self.terminalFocusGained.emit(self) # Emit self


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Terminal Emulator")
        self.resize(900, 700)
        self.setStatusBar(QStatusBar()) # Add a status bar

        self.terminals = [] 
        self.terminal_counter = 0
        self.last_focused_terminal: TerminalWidget | None = None # Track last focused terminal

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
        
        # Preset Commands Panel
        self.commands_group = QGroupBox("Preset Commands")
        commands_layout = QHBoxLayout() 
        self.commands_group.setLayout(commands_layout)

        preset_commands = {
            "List Files": "ls -lah",
            "Git Status": "git status",
            "Disk Usage": "df -h",
            "IP Config (Win)": "ipconfig",
            "IP Addr (Linux)": "ip addr",
            "Python Ver": "python --version"
        }

        for btn_text, cmd_str in preset_commands.items():
            button = QPushButton(btn_text)
            button.clicked.connect(functools.partial(self._send_preset_command, cmd_str))
            commands_layout.addWidget(button)
        
        mlay.addWidget(self.commands_group) # Add command panel to main layout
        self.setCentralWidget(main_widget)

        # Initial setup
        self.rb_stack.setChecked(True) 
        self._add_new_terminal_instance() 
        self._add_new_terminal_instance() 
        self._refresh_active_view_layout()
        
        # Set initial focus and last_focused_terminal
        if self.terminals:
            # The focus set in _on_add_terminal_clicked (via _refresh) should handle this.
            # For robustness, ensure one is focused if view is already set.
            if self.views.currentIndex() == 0 and self.stacker.count() > 0: # Stacked
                 self.stacker.widget(self.stacker.count()-1).setFocus()
            elif self.views.currentIndex() == 1 and self.tabber.count() > 0: # Tabbed
                 self.tabber.currentWidget().setFocus()


    def _on_terminal_focus_gained(self, terminal_widget: TerminalWidget):
        if isinstance(terminal_widget, TerminalWidget): # Ensure it's the correct type
            self.last_focused_terminal = terminal_widget
            logger.debug(f"MainWindow: Last focused terminal updated to {terminal_widget.terminal_id}")


    def _on_view_mode_changed(self):
        sender = self.sender()
        if not sender.isChecked():
            return

        new_view_index = 0 if sender == self.rb_stack else 1
        if self.views.currentIndex() != new_view_index:
            self.views.setCurrentIndex(new_view_index)
            self._refresh_active_view_layout() # Refresh first
            
            # Then set focus
            if new_view_index == 1 and self.tabber.count() > 0: 
                current_tab_widget = self.tabber.currentWidget()
                if current_tab_widget:
                    current_tab_widget.setFocus()
            elif new_view_index == 0 and len(self.terminals) > 0: 
                # Try focusing last_focused_terminal if it's in the stacker, else last in stacker
                focused = False
                if self.last_focused_terminal and self.last_focused_terminal in self.terminals:
                    is_in_stacker = any(self.stacker.widget(i) == self.last_focused_terminal for i in range(self.stacker.count()))
                    if is_in_stacker:
                        self.last_focused_terminal.setFocus()
                        focused = True
                if not focused and self.stacker.count() > 0:
                    self.stacker.widget(self.stacker.count() -1).setFocus()


    def _add_new_terminal_instance(self) -> TerminalWidget:
        self.terminal_counter += 1
        tid = f"Term{self.terminal_counter}"
        term = TerminalWidget(terminal_id=tid)
        term.titleChanged.connect(lambda title, widget=term: self._update_terminal_title(widget, title))
        term.terminalFocusGained.connect(self._on_terminal_focus_gained) # Connect focus signal
        term.setProperty("current_title", tid) 
        self.terminals.append(term)
        return term

    def _on_add_terminal_clicked(self):
        new_term = self._add_new_terminal_instance()
        self._refresh_active_view_layout() # Refresh first
        
        # Then set focus on the new terminal
        if self.views.currentIndex() == 0: 
            new_term.setFocus()
        else: 
            idx = self.tabber.indexOf(new_term)
            if idx != -1:
                self.tabber.setCurrentIndex(idx)
            new_term.setFocus()


    def _on_tab_close_requested(self, index: int):
        if self.tabber.count() <= 1 and self.stacker.count() == 0 : # Don't close the very last terminal
             # Check more broadly: if len(self.terminals) <= 1
            if len(self.terminals) <=1:
                return
        
        widget_to_close = self.tabber.widget(index)
        if widget_to_close and isinstance(widget_to_close, TerminalWidget):
            self._close_terminal_widget(widget_to_close)
        
        # After closing, if tabber still has tabs, ensure current one has focus
        if self.views.currentWidget() == self.tabber and self.tabber.count() > 0:
            current_tab_w = self.tabber.currentWidget()
            if current_tab_w:
                current_tab_w.setFocus()


    def _close_terminal_widget(self, term_widget: TerminalWidget):
        if term_widget == self.last_focused_terminal:
            self.last_focused_terminal = None # Clear if closed terminal was last focused
            logger.debug(f"Closed terminal {term_widget.terminal_id} was last focused. Resetting last_focused_terminal.")

        if term_widget in self.terminals:
            logger.info(f"Closing terminal: {term_widget.terminal_id}")
            term_widget.terminalFocusGained.disconnect(self._on_terminal_focus_gained) # Disconnect signal
            term_widget.close() 
            self.terminals.remove(term_widget)
            term_widget.deleteLater() 
            self._refresh_active_view_layout()
        else:
            logger.warning(f"Attempted to close a widget not in self.terminals: {term_widget}")


    def _update_terminal_title(self, widget: TerminalWidget, title: str):
        name = title.strip() or widget.terminal_id
        widget.setProperty("current_title", name) 

        if self.views.currentWidget() == self.tabber:
            idx = self.tabber.indexOf(widget)
            if idx != -1:
                self.tabber.setTabText(idx, name[:30]) 

    def _refresh_active_view_layout(self):
        try:
            self.tabber.tabCloseRequested.disconnect(self._on_tab_close_requested)
        except TypeError: 
            pass

        active_terminals_in_view_before_refresh = []
        current_view_widget = self.views.currentWidget()
        if current_view_widget == self.tabber:
            for i in range(self.tabber.count()):
                active_terminals_in_view_before_refresh.append(self.tabber.widget(i))
        elif current_view_widget == self.stacker:
            for i in range(self.stacker.count()):
                active_terminals_in_view_before_refresh.append(self.stacker.widget(i))


        for term_widget in list(self.terminals): # Iterate a copy if modifying list
            # Detach only if currently parented to stacker or is a tab page
            is_in_stacker = (self.stacker.indexOf(term_widget) != -1)
            is_in_tabber = (self.tabber.indexOf(term_widget) != -1)

            if term_widget.parent() == self.stacker or is_in_stacker:
                 if is_in_stacker: # Ensure it's actually a direct child widget of stacker
                    term_widget.setParent(None)
            elif is_in_tabber : # If it's a tab page, removeTab will handle reparenting
                 pass # Will be handled by clear tabber below

        while self.tabber.count() > 0:
            self.tabber.removeTab(0)

        # Clear Stacker (setting parent to None removes widget from splitter)
        # Collect widgets to remove to avoid issues with changing count during iteration
        stacker_widgets_to_remove = [self.stacker.widget(i) for i in range(self.stacker.count())]
        for widget_in_stacker in stacker_widgets_to_remove:
            widget_in_stacker.setParent(None) # This should remove it from splitter

        active_view_idx = self.views.currentIndex()

        if active_view_idx == 0: 
            for term_widget in self.terminals:
                self.stacker.addWidget(term_widget)
                term_widget.show() 
            if self.stacker.count() > 0:
                base_size = self.stacker.height() if self.stacker.orientation() == Qt.Orientation.Vertical else self.stacker.width()
                if base_size > 0 and self.stacker.count() > 0: # Avoid division by zero
                    size_per_widget = base_size // self.stacker.count()
                    sizes = [size_per_widget] * self.stacker.count()
                    self.stacker.setSizes(sizes)

        else: 
            for term_widget in self.terminals:
                title = term_widget.property("current_title") or term_widget.terminal_id
                self.tabber.addTab(term_widget, title[:30])
                term_widget.show() 

        self.tabber.tabCloseRequested.connect(self._on_tab_close_requested)
        
        if not any(t.hasFocus() for t in self.terminals if t in active_terminals_in_view_before_refresh) and len(self.terminals) == 0:
             self.setFocus()

    def _get_active_terminal(self) -> TerminalWidget | None:
        current_view_widget = self.views.currentWidget()

        if current_view_widget == self.tabber:
            widget_in_current_tab = self.tabber.currentWidget()
            if isinstance(widget_in_current_tab, TerminalWidget) and widget_in_current_tab in self.terminals:
                return widget_in_current_tab
            return None 

        elif current_view_widget == self.stacker:
            focused_widget = QApplication.instance().focusWidget()
            if isinstance(focused_widget, TerminalWidget) and focused_widget in self.terminals:
                if any(self.stacker.widget(i) == focused_widget for i in range(self.stacker.count())):
                    return focused_widget

            if self.last_focused_terminal and self.last_focused_terminal in self.terminals:
                if any(self.stacker.widget(i) == self.last_focused_terminal for i in range(self.stacker.count())):
                    return self.last_focused_terminal
            
            if self.stacker.count() > 0:
                for term_widget in reversed(self.terminals):
                    if any(self.stacker.widget(i) == term_widget for i in range(self.stacker.count())):
                        return term_widget
            return None 
        return None

    def _send_preset_command(self, command_text: str):
        active_term = self._get_active_terminal()
        if active_term:
            logger.info(f"Sending preset command '{command_text}' to {active_term.terminal_id}")
            active_term.send_command(command_text)
            active_term.setFocus() # Return focus to the terminal after sending command
            self.statusBar().showMessage(f"Sent '{command_text}' to {active_term.terminal_id}", 2000)
        else:
            logger.warning("No active terminal found to send preset command.")
            self.statusBar().showMessage("No active terminal to send command.", 3000)


    def closeEvent(self, event):
        logger.info("MainWindow close event. Closing all terminals.")
        for term_widget in list(self.terminals): # Iterate over a copy
            self._close_terminal_widget(term_widget)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec())