# COPYRIGHT (C) 2020-2022 Nicotine+ Contributors
# COPYRIGHT (C) 2018 Mutnick <mutnick@techie.com>
# COPYRIGHT (C) 2016-2017 Michael Labouebe <gfarmerfr@free.fr>
# COPYRIGHT (C) 2008-2011 quinox <quinox@users.sf.net>
# COPYRIGHT (C) 2009 hedonist <ak@sensi.org>
# COPYRIGHT (C) 2006-2009 daelstorm <daelstorm@gmail.com>
# COPYRIGHT (C) 2003-2004 Hyriand <hyriand@thegraveyard.org>
#
# GNU GENERAL PUBLIC LICENSE
#    Version 3, 29 June 2007
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import time

from gi.repository import GObject
from gi.repository import Gtk

from pynicotine.config import config
from pynicotine.gtkgui.application import GTK_API_VERSION
from pynicotine.gtkgui.dialogs.fileproperties import FileProperties
from pynicotine.gtkgui.utils import copy_text
from pynicotine.gtkgui.widgets.accelerator import Accelerator
from pynicotine.gtkgui.widgets.popupmenu import PopupMenu
from pynicotine.gtkgui.widgets.popupmenu import FilePopupMenu
from pynicotine.gtkgui.widgets.popupmenu import UserPopupMenu
from pynicotine.gtkgui.widgets.theme import add_css_class
from pynicotine.gtkgui.widgets.theme import get_file_type_icon_name
from pynicotine.gtkgui.widgets.theme import remove_css_class
from pynicotine.gtkgui.widgets.treeview import collapse_treeview
from pynicotine.gtkgui.widgets.treeview import create_grouping_menu
from pynicotine.gtkgui.widgets.treeview import initialise_columns
from pynicotine.gtkgui.widgets.treeview import save_columns
from pynicotine.gtkgui.widgets.treeview import select_user_row_iter
from pynicotine.gtkgui.widgets.treeview import show_file_path_tooltip
from pynicotine.gtkgui.widgets.treeview import show_file_type_tooltip
from pynicotine.gtkgui.widgets.ui import UserInterface
from pynicotine.slskmessages import UINT64_LIMIT
from pynicotine.transfers import Transfer
from pynicotine.utils import human_length
from pynicotine.utils import human_size
from pynicotine.utils import human_speed
from pynicotine.utils import humanize


class TransferList:

    path_separator = path_label = retry_label = abort_label = None
    deprioritized_statuses = ()
    transfer_page = user_counter = file_counter = expand_button = expand_icon = grouping_button = None

    def __init__(self, window, transfer_type):

        ui_template = UserInterface(scope=self, path=f"{transfer_type}s.ui")
        (
            self.clear_all_button,
            self.container,
            self.tree_view
        ) = ui_template.widgets

        self.window = window
        self.type = transfer_type

        if GTK_API_VERSION >= 4:
            self.clear_all_button.set_has_frame(False)

        Accelerator("t", self.tree_view, self.on_abort_transfers_accelerator)
        Accelerator("r", self.tree_view, self.on_retry_transfers_accelerator)
        Accelerator("Delete", self.tree_view, self.on_clear_transfers_accelerator)
        Accelerator("<Alt>Return", self.tree_view, self.on_file_properties_accelerator)

        self.transfer_list = []
        self.users = {}
        self.paths = {}
        self.tree_users = None
        self.last_redraw_time = 0
        self.file_properties = None

        # Use dict instead of list for faster membership checks
        self.selected_users = {}
        self.selected_transfers = {}

        # Status list
        self.statuses = {
            "Queued": _("Queued"),
            "Queued (prioritized)": _("Queued (prioritized)"),
            "Queued (privileged)": _("Queued (privileged)"),
            "Getting status": _("Getting status"),
            "Transferring": _("Transferring"),
            "Connection timeout": _("Connection timeout"),
            "Pending shutdown.": _("Pending shutdown"),
            "User logged off": _("User logged off"),
            "Disallowed extension": _("Disallowed extension"),
            "Cancelled": _("Cancelled"),
            "Paused": _("Paused"),
            "Finished": _("Finished"),
            "Filtered": _("Filtered"),
            "Banned": _("Banned"),
            "Too many files": _("Too many files"),
            "Too many megabytes": _("Too many megabytes"),
            "File not shared": _("File not shared"),
            "File not shared.": _("File not shared"),
            "Download folder error": _("Download folder error"),
            "Local file error": _("Local file error"),
            "Remote file error": _("Remote file error")
        }

        self.create_model()

        self.h_adjustment = self.tree_view.get_parent().get_hadjustment()
        self.column_numbers = list(range(self.transfersmodel.get_n_columns()))
        self.cols = cols = initialise_columns(
            window, transfer_type, self.tree_view,
            ["user", _("User"), 200, "text", None],
            ["path", self.path_label, 400, "text", None],
            ["file_type", _("File Type"), 40, "icon", None],
            ["filename", _("Filename"), 400, "text", None],
            ["status", _("Status"), 140, "text", None],
            ["queue_position", _("Queue"), 90, "number", None],
            ["percent", _("Percent"), 90, "progress", None],
            ["size", _("Size"), 180, "number", None],
            ["speed", _("Speed"), 100, "number", None],
            ["time_elapsed", _("Time Elapsed"), 140, "number", None],
            ["time_left", _("Time Left"), 140, "number", None],
        )

        cols["user"].set_sort_column_id(0)
        cols["path"].set_sort_column_id(1)
        cols["file_type"].set_sort_column_id(2)
        cols["filename"].set_sort_column_id(3)
        cols["status"].set_sort_column_id(4)
        cols["queue_position"].set_sort_column_id(14)
        cols["percent"].set_sort_column_id(6)
        cols["size"].set_sort_column_id(11)
        cols["speed"].set_sort_column_id(13)
        cols["time_elapsed"].set_sort_column_id(15)
        cols["time_left"].set_sort_column_id(16)

        cols["file_type"].get_widget().set_visible(False)

        menu = create_grouping_menu(
            window, config.sections["transfers"][f"group{transfer_type}s"], self.on_toggle_tree)
        self.grouping_button.set_menu_model(menu)

        if GTK_API_VERSION >= 4:
            add_css_class(widget=self.grouping_button.get_first_child(), css_class="image-button")

        self.expand_button.connect("toggled", self.on_expand_tree)
        self.expand_button.set_active(config.sections["transfers"][f"{transfer_type}sexpanded"])

        self.popup_menu_users = UserPopupMenu(window.application)
        self.popup_menu_clear = PopupMenu(window.application)
        self.clear_all_button.set_menu_model(self.popup_menu_clear.model)

        self.popup_menu_copy = PopupMenu(window.application)
        self.popup_menu_copy.add_items(
            ("#" + _("Copy _File Path"), self.on_copy_file_path),
            ("#" + _("Copy _URL"), self.on_copy_url),
            ("#" + _("Copy Folder U_RL"), self.on_copy_dir_url)
        )

        self.popup_menu = FilePopupMenu(window.application, self.tree_view, self.on_popup_menu)
        self.popup_menu.add_items(
            ("#" + "selected_files", None),
            ("", None),
            ("#" + _("Send to _Player"), self.on_play_files),
            ("#" + _("_Open in File Manager"), self.on_open_file_manager),
            ("#" + _("F_ile Properties"), self.on_file_properties),
            ("", None),
            ("#" + self.retry_label, self.on_retry_transfer),
            ("#" + self.abort_label, self.on_abort_transfer),
            ("#" + _("_Clear"), self.on_clear_transfer),
            ("", None),
            ("#" + _("_Browse Folder(s)"), self.on_browse_folder),
            ("#" + _("_Search"), self.on_file_search),
            ("", None),
            (">" + _("Copy"), self.popup_menu_copy),
            (">" + _("Clear All"), self.popup_menu_clear),
            (">" + _("User Actions"), self.popup_menu_users)
        )

    def create_model(self):
        """ Create a tree model based on the grouping mode. Scrolling performance of Gtk.TreeStore
        is bad with large plain lists, so use Gtk.ListStore in ungrouped mode where no tree structure
        is necessary. """

        tree_model_class = Gtk.ListStore if self.tree_users == "ungrouped" else Gtk.TreeStore
        self.transfersmodel = tree_model_class(
            str,                   # (0)  user
            str,                   # (1)  path
            str,                   # (2)  file type icon
            str,                   # (3)  file name
            str,                   # (4)  translated status
            str,                   # (5)  hqueue position
            int,                   # (6)  percent
            str,                   # (7)  hsize
            str,                   # (8)  hspeed
            str,                   # (9)  htime elapsed
            str,                   # (10) htime left
            GObject.TYPE_UINT64,   # (11) size
            GObject.TYPE_UINT64,   # (12) current bytes
            GObject.TYPE_UINT64,   # (13) speed
            GObject.TYPE_UINT,     # (14) queue position
            int,                   # (15) time elapsed
            GObject.TYPE_UINT64,   # (16) time left
            GObject.TYPE_PYOBJECT  # (17) transfer object
        )

        if self.tree_users is not None:
            self.tree_view.set_model(self.transfersmodel)

    def init_transfers(self, transfer_list):
        self.transfer_list = transfer_list
        self.update_model()

    def save_columns(self):
        save_columns(self.type, self.tree_view.get_columns())

    def select_transfers(self):

        self.selected_transfers.clear()
        self.selected_users.clear()

        model, paths = self.tree_view.get_selection().get_selected_rows()

        for path in paths:
            iterator = model.get_iter(path)
            self.select_transfer(model, iterator, select_user=True)

            # If we're in grouping mode, select any transfers under the selected
            # user or folder
            self.select_child_transfers(model, model.iter_children(iterator))

    def select_child_transfers(self, model, iterator):

        while iterator is not None:
            self.select_transfer(model, iterator)
            self.select_child_transfers(model, model.iter_children(iterator))
            iterator = model.iter_next(iterator)

    def select_transfer(self, model, iterator, select_user=False):

        transfer = model.get_value(iterator, 17)

        if transfer.filename is not None and transfer not in self.selected_transfers:
            self.selected_transfers[transfer] = None

        if select_user and transfer.user not in self.selected_users:
            self.selected_users[transfer.user] = None

    def new_transfer_notification(self, finished=False):
        if self.window.current_page_id != self.transfer_page.id:
            self.window.notebook.request_tab_changed(self.transfer_page, is_important=finished)

    def on_file_search(self, *_args):

        transfer = next(iter(self.selected_transfers), None)

        if not transfer:
            return

        self.window.search_entry.set_text(transfer.filename.rsplit("\\", 1)[1])
        self.window.change_main_page(self.window.search_page)

    def translate_status(self, status):

        translated_status = self.statuses.get(status)

        if translated_status:
            return translated_status

        return status

    def update_num_users_files(self):
        self.user_counter.set_text(humanize(len(self.users)))
        self.file_counter.set_text(humanize(len(self.transfer_list)))

    def redraw_treeview(self):
        """ Workaround for GTK 3 issue where GtkTreeView doesn't refresh changed values
        if horizontal scrolling is present while fixed-height mode is enabled """

        if GTK_API_VERSION != 3 or self.h_adjustment.get_value() <= 0:
            return

        current_time = time.time()

        if (current_time - self.last_redraw_time) < 1:
            return

        self.last_redraw_time = current_time
        self.tree_view.queue_draw()

    def update_model(self, transfer=None, update_parent=True, forceupdate=False):

        if not forceupdate and self.window.current_page_id != self.transfer_page.id:
            # No need to do unnecessary work if transfers are not visible
            return

        update_counters = False

        if transfer is not None:
            update_counters = self.update_specific(transfer)

        elif self.transfer_list:
            for transfer_i in reversed(self.transfer_list):
                row_added = self.update_specific(transfer_i)

                if row_added and not update_counters:
                    update_counters = True

        if update_parent:
            self.update_parent_rows(transfer)

        if update_counters:
            self.update_num_users_files()

        self.redraw_treeview()

    def update_parent_rows(self, transfer=None):

        if self.tree_users != "ungrouped":
            if transfer is not None:
                username = transfer.user
                path = transfer.path if self.type == "download" else transfer.filename.rsplit("\\", 1)[0]
                user_path = username + path

                user_path_iter = self.paths.get(user_path)
                user_iter = self.users.get(username)

                if user_path_iter:
                    self.update_parent_row(user_path_iter, user_path, folder=True)

                if user_iter:
                    self.update_parent_row(user_iter, username)

            else:
                for user_path, user_path_iter in self.paths.copy().items():
                    self.update_parent_row(user_path_iter, user_path, folder=True)

                for username, user_iter in self.users.copy().items():
                    self.update_parent_row(user_iter, username)

        # Show tab description if necessary
        self.container.get_parent().set_visible(self.transfer_list)

    @staticmethod
    def get_hqueue_position(queue_position):
        return str(queue_position) if queue_position > 0 else ""

    @staticmethod
    def get_hsize(current_byte_offset, size):
        return f"{human_size(current_byte_offset)} / {human_size(size)}"

    @staticmethod
    def get_hspeed(speed):
        return human_speed(speed) if speed > 0 else ""

    @staticmethod
    def get_helapsed(elapsed):
        return human_length(elapsed) if elapsed > 0 else ""

    @staticmethod
    def get_hleft(left):
        return human_length(left) if left >= 1 else ""

    @staticmethod
    def get_percent(current_byte_offset, size):
        return min(((100 * int(current_byte_offset)) / int(size)), 100) if size > 0 else 100

    def update_parent_row(self, initer, key, folder=False):

        speed = 0.0
        total_size = current_byte_offset = 0
        elapsed = 0
        parent_status = "Finished"

        iterator = self.transfersmodel.iter_children(initer)

        if iterator is None:
            # Remove parent row if no children are present anymore
            dictionary = self.paths if folder else self.users
            self.transfersmodel.remove(initer)
            del dictionary[key]
            return

        while iterator is not None:
            transfer = self.transfersmodel.get_value(iterator, 17)
            status = transfer.status

            if status == "Transferring":
                # "Transferring" status always has the highest priority
                parent_status = status
                speed += transfer.speed or 0

            elif parent_status in self.deprioritized_statuses and status != "Finished":
                # "Finished" status always has the lowest priority
                parent_status = status

            if status == "Filtered" and transfer.filename:
                # We don't want to count filtered files when calculating the progress
                iterator = self.transfersmodel.iter_next(iterator)
                continue

            elapsed += transfer.time_elapsed or 0
            total_size += transfer.size or 0
            current_byte_offset += transfer.current_byte_offset or 0

            iterator = self.transfersmodel.iter_next(iterator)

        transfer = self.transfersmodel.get_value(initer, 17)
        total_size = min(total_size, UINT64_LIMIT)
        current_byte_offset = min(current_byte_offset, UINT64_LIMIT)

        if transfer.status != parent_status:
            self.transfersmodel.set_value(initer, 4, self.translate_status(parent_status))
            transfer.status = parent_status

        if transfer.speed != speed:
            self.transfersmodel.set_value(initer, 8, self.get_hspeed(speed))
            self.transfersmodel.set_value(initer, 13, GObject.Value(GObject.TYPE_UINT64, speed))
            transfer.speed = speed

        if transfer.time_elapsed != elapsed:
            left = (total_size - current_byte_offset) / speed if speed and total_size > current_byte_offset else 0
            self.transfersmodel.set_value(initer, 9, self.get_helapsed(elapsed))
            self.transfersmodel.set_value(initer, 10, self.get_hleft(left))
            self.transfersmodel.set_value(initer, 15, elapsed)
            self.transfersmodel.set_value(initer, 16, GObject.Value(GObject.TYPE_UINT64, left))
            transfer.time_elapsed = elapsed

        if transfer.current_byte_offset != current_byte_offset:
            self.transfersmodel.set_value(initer, 6, self.get_percent(current_byte_offset, total_size))
            self.transfersmodel.set_value(initer, 7, self.get_hsize(current_byte_offset, total_size))
            self.transfersmodel.set_value(initer, 12, GObject.Value(GObject.TYPE_UINT64, current_byte_offset))
            transfer.current_byte_offset = current_byte_offset

        if transfer.size != total_size:
            self.transfersmodel.set_value(initer, 6, self.get_percent(current_byte_offset, total_size))
            self.transfersmodel.set_value(initer, 7, self.get_hsize(current_byte_offset, total_size))
            self.transfersmodel.set_value(initer, 11, GObject.Value(GObject.TYPE_UINT64, total_size))
            transfer.size = total_size

    def update_specific(self, transfer=None):

        current_byte_offset = transfer.current_byte_offset or 0
        queue_position = transfer.queue_position or 0
        modifier = transfer.modifier
        status = transfer.status or ""

        if modifier and status == "Queued":
            # Priority status
            status = status + f" ({modifier})"

        size = transfer.size or 0
        speed = transfer.speed or 0
        hspeed = self.get_hspeed(speed)
        elapsed = transfer.time_elapsed or 0
        helapsed = self.get_helapsed(elapsed)
        left = transfer.time_left or 0
        initer = transfer.iterator

        # Modify old transfer
        if initer is not None:
            translated_status = self.translate_status(status)

            if self.transfersmodel.get_value(initer, 4) != translated_status:
                self.transfersmodel.set_value(initer, 4, translated_status)

            if self.transfersmodel.get_value(initer, 8) != hspeed:
                self.transfersmodel.set_value(initer, 8, hspeed)
                self.transfersmodel.set_value(initer, 13, GObject.Value(GObject.TYPE_UINT64, speed))

            if self.transfersmodel.get_value(initer, 9) != helapsed:
                self.transfersmodel.set_value(initer, 9, helapsed)
                self.transfersmodel.set_value(initer, 10, self.get_hleft(left))
                self.transfersmodel.set_value(initer, 15, elapsed)
                self.transfersmodel.set_value(initer, 16, GObject.Value(GObject.TYPE_UINT64, left))

            if self.transfersmodel.get_value(initer, 12) != current_byte_offset:
                self.transfersmodel.set_value(initer, 6, self.get_percent(current_byte_offset, size))
                self.transfersmodel.set_value(initer, 7, self.get_hsize(current_byte_offset, size))
                self.transfersmodel.set_value(initer, 12, GObject.Value(GObject.TYPE_UINT64, current_byte_offset))

            elif self.transfersmodel.get_value(initer, 11) != size:
                self.transfersmodel.set_value(initer, 6, self.get_percent(current_byte_offset, size))
                self.transfersmodel.set_value(initer, 7, self.get_hsize(current_byte_offset, size))
                self.transfersmodel.set_value(initer, 11, GObject.Value(GObject.TYPE_UINT64, size))

            if self.transfersmodel.get_value(initer, 14) != queue_position:
                self.transfersmodel.set_value(initer, 5, self.get_hqueue_position(queue_position))
                self.transfersmodel.set_value(initer, 14, GObject.Value(GObject.TYPE_UINT, queue_position))

            return False

        expand_user = False
        expand_folder = False

        filename = transfer.filename
        user = transfer.user
        shortfn = filename.split("\\")[-1]

        if self.tree_users != "ungrouped":
            # Group by folder or user

            empty_int = 0
            empty_str = ""

            if user not in self.users:
                # Create Parent if it doesn't exist
                # ProgressRender not visible (last column sets 4th column)
                self.users[user] = self.transfersmodel.insert_with_values(
                    None, -1, self.column_numbers,
                    [
                        user,
                        empty_str,
                        empty_str,
                        empty_str,
                        empty_str,
                        empty_str,
                        empty_int,
                        empty_str,
                        empty_str,
                        empty_str,
                        empty_str,
                        empty_int,
                        empty_int,
                        empty_int,
                        empty_int,
                        empty_int,
                        empty_int,
                        Transfer(user=user)
                    ]
                )

                if self.tree_users == "folder_grouping":
                    expand_user = True
                else:
                    expand_user = self.expand_button.get_active()

            parent = self.users[user]

            if self.tree_users == "folder_grouping":
                # Group by folder

                """ Paths can be empty if files are downloaded individually, make sure we
                don't add files to the wrong user in the TreeView """
                full_path = path = transfer.path if self.type == "download" else transfer.filename.rsplit("\\", 1)[0]
                user_path = user + path

                if config.sections["ui"]["reverse_file_paths"]:
                    path = self.path_separator.join(reversed(path.split(self.path_separator)))

                if user_path not in self.paths:
                    self.paths[user_path] = self.transfersmodel.insert_with_values(
                        self.users[user], -1, self.column_numbers,
                        [
                            user,
                            path,
                            empty_str,
                            empty_str,
                            empty_str,
                            empty_str,
                            empty_int,
                            empty_str,
                            empty_str,
                            empty_str,
                            empty_str,
                            empty_int,
                            empty_int,
                            empty_int,
                            empty_int,
                            empty_int,
                            empty_int,
                            Transfer(user=user, path=full_path)
                        ]
                    )
                    expand_folder = self.expand_button.get_active()

                parent = self.paths[user_path]
        else:
            # No grouping
            # We use this list to get the total number of users
            self.users.setdefault(user, set()).add(transfer)
            parent = None

        # Add a new transfer
        if self.tree_users == "folder_grouping":
            # Group by folder, path not visible
            path = ""
        else:
            path = transfer.path if self.type == "download" else transfer.filename.rsplit("\\", 1)[0]

            if config.sections["ui"]["reverse_file_paths"]:
                path = self.path_separator.join(reversed(path.split(self.path_separator)))

        row = (
            user,
            path,
            get_file_type_icon_name(shortfn),
            shortfn,
            self.translate_status(status),
            self.get_hqueue_position(queue_position),
            self.get_percent(current_byte_offset, size),
            self.get_hsize(current_byte_offset, size),
            hspeed,
            helapsed,
            self.get_hleft(left),
            GObject.Value(GObject.TYPE_UINT64, size),
            GObject.Value(GObject.TYPE_UINT64, current_byte_offset),
            GObject.Value(GObject.TYPE_UINT64, speed),
            GObject.Value(GObject.TYPE_UINT, queue_position),
            elapsed,
            GObject.Value(GObject.TYPE_UINT64, left),
            transfer
        )

        if parent is None:
            transfer.iterator = self.transfersmodel.insert_with_valuesv(-1, self.column_numbers, row)
        else:
            transfer.iterator = self.transfersmodel.insert_with_values(parent, -1, self.column_numbers, row)

        if expand_user:
            self.tree_view.expand_row(self.transfersmodel.get_path(self.users[user]), False)

        if expand_folder:
            self.tree_view.expand_row(self.transfersmodel.get_path(self.paths[user_path]), False)

        return True

    def clear_model(self):

        self.tree_view.set_model(None)

        self.users.clear()
        self.paths.clear()
        self.selected_transfers.clear()
        self.selected_users.clear()
        self.transfersmodel.clear()

        self.tree_view.set_model(self.transfersmodel)

        for transfer in self.transfer_list:
            transfer.iterator = None

    def retry_selected_transfers(self):
        # Implemented in subclasses
        pass

    def abort_selected_transfers(self):
        # Implemented in subclasses
        pass

    def clear_selected_transfers(self):
        # Implemented in subclasses
        pass

    def abort_transfer(self, transfer, status_message=None, update_parent=True):
        if status_message is not None:
            self.update_model(transfer, update_parent=update_parent)

    def abort_transfers(self, _transfers, _status_message=None):
        self.update_parent_rows()

    def clear_transfer(self, transfer, update_parent=True):

        user = transfer.user

        if self.tree_users == "ungrouped" and user in self.users:
            # No grouping
            self.users[user].discard(transfer)

            if not self.users[user]:
                del self.users[user]

        if transfer.iterator is not None:
            self.transfersmodel.remove(transfer.iterator)

        if update_parent:
            self.update_parent_rows(transfer)
            self.update_num_users_files()

    def clear_transfers(self, *_args):
        self.update_parent_rows()
        self.update_num_users_files()

    def add_popup_menu_user(self, popup, user):

        popup.setup_user_menu(user)
        popup.add_items(
            ("", None),
            ("#" + _("Select User's Transfers"), self.on_select_user_transfers, user)
        )
        popup.update_model()
        popup.toggle_user_items()

    def populate_popup_menu_users(self):

        self.popup_menu_users.clear()

        if not self.selected_users:
            return

        # Multiple users, create submenus for each user
        if len(self.selected_users) > 1:
            for user in self.selected_users:
                popup = UserPopupMenu(self.window.application)
                self.add_popup_menu_user(popup, user)
                self.popup_menu_users.add_items((">" + user, popup))
                self.popup_menu_users.update_model()
            return

        # Single user, add items directly to "User Actions" submenu
        user = next(iter(self.selected_users), None)
        self.add_popup_menu_user(self.popup_menu_users, user)

    def on_expand_tree(self, *_args):

        expanded = self.expand_button.get_active()

        if expanded:
            icon_name = "go-up-symbolic"
            self.tree_view.expand_all()

        else:
            icon_name = "go-down-symbolic"
            collapse_treeview(self.tree_view, self.tree_users)

        self.expand_icon.set_property("icon-name", icon_name)

        config.sections["transfers"][f"{self.type}sexpanded"] = expanded
        config.write_configuration()

    def on_toggle_tree(self, action, state):

        mode = state.get_string()
        active = mode != "ungrouped"
        popover = self.grouping_button.get_popover()

        if popover is not None:
            popover.set_visible(False)

        if GTK_API_VERSION >= 4:
            # Ensure buttons are flat in libadwaita
            css_class_function = add_css_class if active else remove_css_class
            css_class_function(widget=self.grouping_button.get_parent(), css_class="linked")

        config.sections["transfers"][f"group{self.type}s"] = mode
        self.tree_view.set_show_expanders(active)
        self.expand_button.set_visible(active)

        self.tree_users = mode

        self.clear_model()
        self.create_model()

        if self.transfer_list:
            self.update_model()

        action.set_state(state)

    @staticmethod
    def on_tooltip(widget, pos_x, pos_y, _keyboard_mode, tooltip):

        file_path_tooltip = show_file_path_tooltip(widget, pos_x, pos_y, tooltip, 17, transfer=True)

        if file_path_tooltip:
            return file_path_tooltip

        return show_file_type_tooltip(widget, pos_x, pos_y, tooltip, 2)

    def on_popup_menu(self, menu, _widget):

        self.select_transfers()
        menu.set_num_selected_files(len(self.selected_transfers))

        self.populate_popup_menu_users()

    def on_row_activated(self, _treeview, path, _column):

        if self.tree_view.collapse_row(path):
            return

        if self.tree_view.expand_row(path, open_all=False):
            return

        self.select_transfers()
        action = config.sections["transfers"][f"{self.type}_doubleclick"]

        if action == 1:    # Send to Player
            self.on_play_files()

        elif action == 2:  # Open in File Manager
            self.on_open_file_manager()

        elif action == 3:  # Search
            self.on_file_search()

        elif action == 4:  # Pause / Abort
            self.abort_selected_transfers()

        elif action == 5:  # Clear
            self.clear_selected_transfers()

        elif action == 6:  # Resume / Retry
            self.retry_selected_transfers()

        elif action == 7:  # Browse Folder
            self.on_browse_folder()

    def on_select_user_transfers(self, *args):

        if not self.selected_users:
            return

        selected_user = args[-1]

        sel = self.tree_view.get_selection()
        fmodel = self.tree_view.get_model()
        sel.unselect_all()

        iterator = fmodel.get_iter_first()

        select_user_row_iter(fmodel, sel, 0, selected_user, iterator)

        self.select_transfers()

    def on_abort_transfers_accelerator(self, *_args):
        """ T: abort transfer """

        self.select_transfers()
        self.abort_selected_transfers()
        return True

    def on_retry_transfers_accelerator(self, *_args):
        """ R: retry transfers """

        self.select_transfers()
        self.retry_selected_transfers()
        return True

    def on_clear_transfers_accelerator(self, *_args):
        """ Delete: clear transfers """

        self.select_transfers()
        self.clear_selected_transfers()
        return True

    def on_file_properties_accelerator(self, *_args):
        """ Alt+Return: show file properties dialog """

        self.select_transfers()
        self.on_file_properties()
        return True

    def on_file_properties(self, *_args):

        data = []
        selected_size = 0

        for transfer in self.selected_transfers:
            fullname = transfer.filename
            filename = fullname.split("\\")[-1]
            directory = fullname.rsplit("\\", 1)[0]
            file_size = transfer.size
            selected_size += file_size

            data.append({
                "user": transfer.user,
                "fn": fullname,
                "filename": filename,
                "directory": directory,
                "path": transfer.path,
                "queue_position": transfer.queue_position,
                "speed": transfer.speed,
                "size": file_size,
                "bitrate": transfer.bitrate,
                "length": transfer.length
            })

        if data:
            if self.file_properties is None:
                self.file_properties = FileProperties(self.window.application, download_button=False)

            self.file_properties.update_properties(data, total_size=selected_size)
            self.file_properties.show()

    def on_copy_url(self, *_args):
        # Implemented in subclasses
        pass

    def on_copy_dir_url(self, *_args):
        # Implemented in subclasses
        pass

    def on_copy_file_path(self, *_args):

        transfer = next(iter(self.selected_transfers), None)

        if transfer:
            copy_text(transfer.filename)

    def on_play_files(self, *_args):
        # Implemented in subclasses
        pass

    def on_open_file_manager(self, *_args):
        # Implemented in subclasses
        pass

    def on_browse_folder(self, *_args):
        # Implemented in subclasses
        pass

    def on_retry_transfer(self, *_args):
        self.select_transfers()
        self.retry_selected_transfers()

    def on_abort_transfer(self, *_args):
        self.select_transfers()
        self.abort_selected_transfers()

    def on_clear_transfer(self, *_args):
        self.select_transfers()
        self.clear_selected_transfers()
