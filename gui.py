import sys
import json
from datetime import datetime
import threading
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QMessageBox, QDateTimeEdit,
    QDialog, QDialogButtonBox, QApplication
)
from PyQt6.QtCore import QTimer, QDateTime, QSettings, Qt, QTimeZone, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QAction

# Need to install this: pip install PyQt6-QSystemTrayIcon
# It's not part of the standard PyQt6 distribution
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu

# Use plyer for cross-platform notifications
try:
    from plyer import notification
    PLYER_AVAILABLE = True
except ImportError:
    print("Warning: Plyer library not found. Desktop notifications will not be available.")
    print("Install it using: pip install plyer")
    PLYER_AVAILABLE = False

# Constants
SETTINGS_FILE = "reminders.json" # Simple JSON file for persistence
CHECK_INTERVAL_MS = 15 * 1000 # Check every 15 seconds

# --- Edit Reminder Dialog ---
class EditReminderDialog(QDialog):
    def __init__(self, current_text, current_datetime, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Reminder")

        # Ensure current_datetime is timezone-aware (use local timezone)
        if current_datetime.tzinfo is None:
            current_datetime = current_datetime.astimezone()

        # Convert Python datetime to QDateTime
        q_datetime = QDateTime(current_datetime)

        layout = QVBoxLayout(self)

        # Reminder Text
        self.text_input = QLineEdit(current_text)
        layout.addWidget(QLabel("Reminder Text:"))
        layout.addWidget(self.text_input)

        # Date and Time
        self.datetime_input = QDateTimeEdit()
        self.datetime_input.setDateTime(q_datetime)
        self.datetime_input.setCalendarPopup(True)
        # Set minimum to now to prevent editing to a past time initially,
        # but allow keeping an already past time if needed.
        self.datetime_input.setMinimumDateTime(QDateTime.currentDateTime())
        layout.addWidget(QLabel("Date & Time:"))
        layout.addWidget(self.datetime_input)

        # OK and Cancel Buttons
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def get_data(self):
        # Convert QDateTime back to Python datetime
        # Ensure it's converted to the system's local timezone for comparison
        edited_qdatetime = self.datetime_input.dateTime()

        # Make the Python datetime object timezone-aware using the system's timezone
        python_datetime = edited_qdatetime.toPyDateTime().astimezone()

        return self.text_input.text().strip(), python_datetime


# --- Worker Thread for Checking Reminders ---
class ReminderCheckerThread(QThread):
    # Define a signal to emit when a reminder is due
    reminder_due_signal = pyqtSignal(str, str)  # title, message

    def __init__(self, reminders, parent=None):
        super().__init__(parent)
        self.reminders = reminders
        self.running = True  # Control the thread loop

    def stop(self):
        self.running = False
        self.quit() # Proper way to stop the thread
        self.wait()

    def run(self):
        while self.running:
            now_aware = datetime.now().astimezone()
            for reminder in list(self.reminders):  # Iterate over a copy
                if not isinstance(reminder.get('dateTime'), datetime):
                    continue

                # Ensure reminder time is timezone-aware for comparison
                reminder_time = reminder['dateTime']
                if reminder_time.tzinfo is None:
                    reminder_time = reminder_time.astimezone()

                if not reminder.get('notified', False) and reminder_time <= now_aware:
                    print(f"Reminder due (thread): {reminder['text']}")
                    # Emit the signal with the reminder details
                    self.reminder_due_signal.emit(reminder['text'], f"Reminder set for {reminder_time.strftime('%H:%M')}")
                    reminder['notified'] = True  # Mark as notified in the original list

            time.sleep(CHECK_INTERVAL_MS / 1000)  # Sleep in seconds



# --- Main Application Window ---
class ReminderApp(QWidget):
    def __init__(self):
        super().__init__()
        self.reminders = []
        # Use app name and org name for QSettings
        self.settings = QSettings("MyCompany", "ReminderApp")
        self.initUI()
        self.load_reminders()
        self.setup_tray_icon()
        self.start_reminder_thread()
        self.restore_window_state()
        self.is_hidden = False # Track if the window is hidden

    def initUI(self):
        self.setWindowTitle('Simple Reminder App')

        main_layout = QVBoxLayout(self)

        # --- Input Area ---
        input_group = QVBoxLayout()
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel("Reminder:"))
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("What to be reminded of?")
        text_layout.addWidget(self.text_input)
        input_group.addLayout(text_layout)

        datetime_layout = QHBoxLayout()
        datetime_layout.addWidget(QLabel("Date & Time:"))
        self.datetime_input = QDateTimeEdit(self)
        # Default to 1 minute from now, ensure it uses current timezone
        self.datetime_input.setDateTime(QDateTime.currentDateTime().addSecs(60))
        self.datetime_input.setCalendarPopup(True)
        self.datetime_input.setMinimumDateTime(QDateTime.currentDateTime())
        datetime_layout.addWidget(self.datetime_input)
        input_group.addLayout(datetime_layout)

        self.add_button = QPushButton("Add Reminder")
        self.add_button.clicked.connect(self.add_reminder)
        input_group.addWidget(self.add_button, alignment=Qt.AlignmentFlag.AlignRight)
        main_layout.addLayout(input_group)

        # --- List Area ---
        main_layout.addWidget(QLabel("Active Reminders (Double-click to edit):"))
        self.reminder_list = QListWidget()
        self.reminder_list.itemDoubleClicked.connect(self.edit_reminder_dialog) # Connect double-click
        main_layout.addWidget(self.reminder_list)

        # --- Delete Button ---
        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.clicked.connect(self.delete_reminder)
        main_layout.addWidget(self.delete_button)

        self.setLayout(main_layout)


    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        if hasattr(QIcon, 'fromTheme'): # Check for theme icons (Linux)
            icon = QIcon.fromTheme("alarm-clock") # Try a standard icon
            if icon.isNull():
                icon = QIcon.fromTheme("preferences-system-time")
            if icon.isNull():
                icon = QIcon() # Fallback to blank
        else:
            icon = QIcon() # Fallback if no theme support
        if icon.isNull():
            print("Warning: Could not load a theme icon, using a blank icon.")
        self.tray_icon.setIcon(icon)

        # Define the context menu
        tray_menu = QMenu()
        show_action = QAction("Show Reminders", self)
        show_action.triggered.connect(self.show_window)
        tray_menu.addAction(show_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.instance().quit) # Correctly quit the app
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        self.tray_icon.activated.connect(self.on_tray_icon_activated)


    def on_tray_icon_activated(self, reason):
        # Double click or left click usually
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick or \
           reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window() # Show on click

    def show_window(self):
        self.show()
        self.is_hidden = False
        self.activateWindow() # Bring to front


    def start_reminder_thread(self):
        self.reminder_thread = ReminderCheckerThread(self.reminders)
        # Connect the signal from the thread to the show_notification method
        self.reminder_thread.reminder_due_signal.connect(self.show_notification)
        self.reminder_thread.start()

    def stop_reminder_thread(self):
        if hasattr(self, 'reminder_thread') and self.reminder_thread:
            self.reminder_thread.stop()
            # self.reminder_thread.wait() # Wait for it to finish (optional)
            print("Reminder thread stopped.")

    def load_reminders(self):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                loaded_data = json.load(f)
                self.reminders = [] # Reset before loading
                for item in loaded_data:
                    try:
                        # Convert ISO string back to datetime object
                        dt_obj = datetime.fromisoformat(item['dateTime'])
                        # Make timezone-aware using system's local timezone if naive
                        if dt_obj.tzinfo is None:
                            dt_obj = dt_obj.astimezone() # Convert naive time to local aware time

                        item['dateTime'] = dt_obj
                        item.setdefault('notified', False) # Ensure 'notified' exists
                        self.reminders.append(item)
                    except (ValueError, TypeError, KeyError) as e:
                        print(f"Warning: Skipping invalid reminder data during load: {item}. Error: {e}")

                self.reminders.sort(key=lambda r: r['dateTime'])
        except FileNotFoundError:
            self.reminders = []
            print(f"'{SETTINGS_FILE}' not found. Starting fresh.")
        except json.JSONDecodeError:
            self.reminders = []
            print(f"Error reading '{SETTINGS_FILE}'. File might be corrupted. Starting fresh.")
        except Exception as e:
            self.reminders = []
            print(f"An unexpected error occurred loading reminders: {e}")
        self.update_list()  # Call update_list AFTER loading is complete

        # Restart the thread with loaded reminders
        self.stop_reminder_thread()
        self.start_reminder_thread()


    def save_reminders(self):
        try:
            reminders_to_save = []
            for r in self.reminders:
                reminder_copy = r.copy()
                if isinstance(reminder_copy.get('dateTime'), datetime):
                    # Save in ISO format, preserving timezone info if available
                    reminder_copy['dateTime'] = reminder_copy['dateTime'].isoformat()
                    reminders_to_save.append(reminder_copy)
                else:
                    print(f"Warning: Skipping reminder '{r.get('text', 'N/A')}' due to invalid dateTime during save.")

            with open(SETTINGS_FILE, 'w') as f:
                json.dump(reminders_to_save, f, indent=4)
        except Exception as e:
            print(f"Error saving reminders to '{SETTINGS_FILE}': {e}")
            QMessageBox.warning(self, "Save Error", f"Could not save reminders: {e}")

    def add_reminder(self):
        text = self.text_input.text().strip()
        q_datetime = self.datetime_input.dateTime()

        if not text:
            QMessageBox.warning(self, "Input Error", "Reminder text cannot be empty.")
            return

        # Convert QDateTime to Python datetime, making it timezone-aware
        reminder_datetime = q_datetime.toPyDateTime().astimezone()
        now_aware = datetime.now().astimezone() # Get timezone-aware current time

        if reminder_datetime <= now_aware:
            QMessageBox.warning(self, "Input Error", "Please select a future date and time.")
            return

        new_reminder = {
            "id": datetime.now().timestamp(), # Simple unique ID
            "text": text,
            "dateTime": reminder_datetime,
            "notified": False
        }
        self.reminders.append(new_reminder)
        self.reminders.sort(key=lambda r: r['dateTime'])
        self.update_list()
        self.save_reminders()

        # Restart the thread with updated reminders
        self.stop_reminder_thread()
        self.start_reminder_thread()

        self.text_input.clear()
        # Reset input time to 1 minute from now
        self.datetime_input.setDateTime(QDateTime.currentDateTime().addSecs(60))

    def delete_reminder(self):
        selected_item = self.reminder_list.currentItem()
        if not selected_item:
            QMessageBox.warning(self, "Selection Error", "Please select a reminder to delete.")
            return

        reminder_id = selected_item.data(Qt.ItemDataRole.UserRole)
        reminder_text = "this reminder"
        # Find the text for the confirmation message
        for r in self.reminders:
            if r['id'] == reminder_id:
                reminder_text = f"'{r['text']}'"
                break

        confirm = QMessageBox.question(self, "Confirm Delete",
                                       f"Are you sure you want to delete {reminder_text}?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if confirm == QMessageBox.StandardButton.Yes:
            self.reminders = [r for r in self.reminders if r['id'] != reminder_id]
            self.update_list() # Update list display first
            self.save_reminders() # Then save the changes

            # Restart the thread with updated reminders
            self.stop_reminder_thread()
            self.start_reminder_thread()


    def edit_reminder_dialog(self, item):
        reminder_id = item.data(Qt.ItemDataRole.UserRole)
        # Find the reminder in our list
        reminder_to_edit = None
        for r in self.reminders:
            if r['id'] == reminder_id:
                reminder_to_edit = r
                break

        if not reminder_to_edit:
            QMessageBox.critical(self, "Error", "Could not find reminder data to edit.")
            return

        # Ensure dateTime is a datetime object before passing
        if not isinstance(reminder_to_edit['dateTime'], datetime):
            QMessageBox.critical(self, "Error", "Invalid date data for the selected reminder.")
            return

        dialog = EditReminderDialog(reminder_to_edit['text'], reminder_to_edit['dateTime'], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text, new_datetime = dialog.get_data()
            now_aware = datetime.now().astimezone()

            if not new_text:
                QMessageBox.warning(self, "Input Error", "Reminder text cannot be empty.")
                return # Stay in dialog? No, just abort edit.
            if new_datetime <= now_aware:
                # Allow editing to a past time, but maybe warn?
                # For now, we prevent setting to past/now during add/edit.
                QMessageBox.warning(self, "Input Error", "Cannot set reminder time to the past or present.")
                return # Abort edit


            # Update the reminder in the list
            original_time = reminder_to_edit['dateTime']
            reminder_to_edit['text'] = new_text
            reminder_to_edit['dateTime'] = new_datetime

            # Reset 'notified' status if the time was changed to a future time
            # and it was previously notified or past due
            if new_datetime > now_aware and (reminder_to_edit.get('notified', False) or original_time <= now_aware):
                reminder_to_edit['notified'] = False
                print(f"Resetting notification status for edited reminder: {new_text}")

            self.reminders.sort(key=lambda r: r['dateTime'])
            self.update_list() # Update UI
            self.save_reminders() # Persist changes

            # Restart the thread with updated reminders
            self.stop_reminder_thread()
            self.start_reminder_thread()



    def update_list(self):
        self.reminder_list.clear()
        now_aware = datetime.now().astimezone() # Use timezone-aware comparison

        # Ensure reminders are sorted before display (redundant if always sorted on change)
        # self.reminders.sort(key=lambda r: r['dateTime'])

        for reminder in self.reminders:
            dt_obj = reminder['dateTime']
            if not isinstance(dt_obj, datetime):
                print(f"Skipping reminder with invalid dateTime during update_list: {reminder.get('text', 'N/A')}")
                continue

            # Ensure dt_obj is timezone-aware for consistent formatting
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.astimezone()

            time_str = dt_obj.strftime("%Y-%m-%d %H:%M") # Local time format

            display_text = f"{reminder['text']} @ {time_str}"
            list_item = QListWidgetItem(display_text)
            list_item.setData(Qt.ItemDataRole.UserRole, reminder['id']) # Store ID

            if reminder.get('notified', False):
                list_item.setForeground(Qt.GlobalColor.gray)
                list_item.setText(f"{display_text} (Notified)")
            elif dt_obj <= now_aware: # Use aware comparison
                list_item.setForeground(Qt.GlobalColor.red)
                list_item.setText(f"{display_text} (Past Due)")

            self.reminder_list.addItem(list_item)


    def show_notification(self, title, message):
        if not PLYER_AVAILABLE:
            print(f"Notification (plyer unavailable): {title} - {message}")
            QMessageBox.information(self, title, f"{message} (Error displaying native notification)")
            return
        try:
            # Use threading to prevent potential GUI block if plyer takes time
            # import threading
            # thread = threading.Thread(target=notification.notify, kwargs={
            #      'title': title,
            #      'message': message,
            #      'app_name': 'Simple Reminder App',
            #      'timeout': 10,
            # })
            # thread.start()
            # Simpler approach for now: direct call
             notification.notify(
                 title=title,
                 message=message,
                 app_name='Simple Reminder App',
                 timeout=10, # seconds
             )
        except Exception as e:
            print(f"Error showing notification using plyer: {e}")
            # Fallback if plyer fails
            QMessageBox.information(self, title, f"{message} (Error displaying native notification)")

    # --- Window State Persistence ---
    def save_window_state(self):
        self.settings.setValue("geometry", self.saveGeometry())
        # self.settings.setValue("windowState", self.saveState()) # State can be less reliable
        print("Window geometry saved.")

    def restore_window_state(self):
        geometry = self.settings.value("geometry")
        if geometry and isinstance(geometry, bytes): # QSettings often returns bytes
            try:
                self.restoreGeometry(geometry)
                print("Window geometry restored.")
            except Exception as e:
                print(f"Failed to restore geometry: {e}. Using default size.")
                self.resize(450, 550) # Fallback size
        else:
            self.resize(450, 550) # Adjusted default size
            print("No saved geometry, using default size.")

    def closeEvent(self, event):
        # Override close event to minimize to tray
        if self.tray_icon.isVisible():
            print("Minimizing to system tray.")  # Added print
            event.ignore() # Keep app running in background
            self.hide()
            self.is_hidden = True
            self.tray_icon.showMessage("Reminder App", "Running in the system tray.",
                                         QSystemTrayIcon.MessageIcon.Information, 2000) # 2 sec
        else:
            print("Actually quitting.")  # Added print
            # Actually close (e.g., if tray icon is not supported)
            self.save_reminders()
            self.save_window_state()
            event.accept()

