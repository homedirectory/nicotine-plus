# COPYRIGHT (C) 2020-2022 Nicotine+ Contributors
# COPYRIGHT (C) 2016-2017 Michael Labouebe <gfarmerfr@free.fr>
# COPYRIGHT (C) 2013 SeeSchloss <see@seos.fr>
# COPYRIGHT (C) 2009-2010 quinox <quinox@users.sf.net>
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

import os

from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

from pynicotine.config import config
from pynicotine.core import core
from pynicotine.events import events
from pynicotine.gtkgui.dialogs.fileproperties import FileProperties
from pynicotine.gtkgui.utils import copy_text
from pynicotine.gtkgui.widgets.accelerator import Accelerator
from pynicotine.gtkgui.widgets.filechooser import FolderChooser
from pynicotine.gtkgui.widgets.iconnotebook import IconNotebook
from pynicotine.gtkgui.widgets.infobar import InfoBar
from pynicotine.gtkgui.widgets.dialogs import EntryDialog
from pynicotine.gtkgui.widgets.popupmenu import PopupMenu
from pynicotine.gtkgui.widgets.popupmenu import FilePopupMenu
from pynicotine.gtkgui.widgets.popupmenu import UserPopupMenu
from pynicotine.gtkgui.widgets.theme import get_file_type_icon_name
from pynicotine.gtkgui.widgets.treeview import initialise_columns
from pynicotine.gtkgui.widgets.treeview import save_columns
from pynicotine.gtkgui.widgets.treeview import show_file_path_tooltip
from pynicotine.gtkgui.widgets.treeview import show_file_type_tooltip
from pynicotine.gtkgui.widgets.ui import UserInterface
from pynicotine.slskmessages import FileListMessage
from pynicotine.slskmessages import UserStatus
from pynicotine.utils import human_size
from pynicotine.utils import humanize
from pynicotine.utils import open_file_path


class UserBrowses(IconNotebook):

    def __init__(self, window):

        super().__init__(
            window,
            parent=window.userbrowse_content,
            parent_page=window.userbrowse_page
        )
        self.file_properties = None

        # Events
        for event_name, callback in (
            ("peer-connection-closed", self.peer_connection_error),
            ("peer-connection-error", self.peer_connection_error),
            ("server-disconnect", self.server_disconnect),
            ("shared-file-list-progress", self.shared_file_list_progress),
            ("shared-file-list-response", self.shared_file_list),
            ("user-browse-remove-user", self.remove_user),
            ("user-browse-show-user", self.show_user),
            ("user-status", self.user_status)
        ):
            events.connect(event_name, callback)

    def on_get_shares(self, *_args):

        entry_text = self.window.userbrowse_entry.get_text().strip()

        if not entry_text:
            return

        self.window.userbrowse_entry.set_text("")

        if entry_text.startswith("slsk://"):
            core.userbrowse.open_soulseek_url(entry_text)
        else:
            core.userbrowse.browse_user(entry_text)

    def show_user(self, user, path=None, local_shares_type=None, switch_page=True):

        if user not in self.pages:
            self.pages[user] = page = UserBrowse(self, user)

            self.append_page(page.container, user, focus_callback=page.on_focus,
                             close_callback=page.on_close, user=user)
            page.set_label(self.get_tab_label_inner(page.container))

        page = self.pages[user]
        page.local_shares_type = local_shares_type
        page.queued_path = path

        page.browse_queued_path()

        if switch_page:
            self.set_current_page(page.container)
            self.window.change_main_page(self.window.userbrowse_page)

    def remove_user(self, user):

        page = self.pages.get(user)

        if page is None:
            return

        page.clear()
        self.remove_page(page.container)
        del self.pages[user]

    def peer_connection_error(self, user, *_args, **_kwargs):

        page = self.pages.get(user)

        if page is not None:
            page.peer_connection_error()

    def user_status(self, msg):

        page = self.pages.get(msg.user)

        if page is not None:
            self.set_user_status(page.container, msg.user, msg.status)

    def shared_file_list_progress(self, user, position, total):

        page = self.pages.get(user)

        if page is not None:
            page.shared_file_list_progress(position, total)

    def shared_file_list(self, msg):

        page = self.pages.get(msg.init.target_user)

        if page is not None:
            page.shared_file_list(msg)

    def server_disconnect(self, *_args):
        for user, page in self.pages.items():
            self.set_user_status(page.container, user, UserStatus.OFFLINE)


class UserBrowse:

    def __init__(self, userbrowses, user):

        ui_template = UserInterface(scope=self, path="userbrowse.ui")
        (
            self.container,
            self.expand_button,
            self.expand_icon,
            self.file_list_view,
            self.folder_tree_view,
            self.info_bar,
            self.num_folders_label,
            self.progress_bar,
            self.refresh_button,
            self.retry_button,
            self.search_entry,
            self.share_size_label
        ) = ui_template.widgets

        self.userbrowses = userbrowses
        self.window = userbrowses.window
        self.user = user
        self.indeterminate_progress = True
        self.local_shares_type = None
        self.queued_path = None
        self.num_folders = 0
        self.share_size = 0

        self.dir_iters = {}
        self.dir_user_data = {}
        self.file_iters = {}

        self.selected_folder = None
        self.selected_folder_size = 0
        self.selected_files = {}

        self.search_list = []
        self.query = None
        self.search_position = 0

        self.info_bar = InfoBar(self.info_bar, button=self.retry_button)

        # Setup folder_tree_view
        self.dir_store = Gtk.TreeStore(str)
        self.dir_column_numbers = list(range(self.dir_store.get_n_columns()))
        cols = initialise_columns(
            self.window, None, self.folder_tree_view,
            ["folder", _("Folder"), -1, "text", None]
        )
        cols["folder"].set_sort_column_id(0)

        self.folder_tree_view.get_selection().connect("changed", self.on_select_dir)
        self.folder_tree_view.set_model(self.dir_store)

        # Popup Menu (folder_tree_view)
        self.user_popup_menu = UserPopupMenu(self.window.application, None, self.on_tab_popup)
        self.user_popup_menu.setup_user_menu(user, page="userbrowse")
        self.user_popup_menu.add_items(
            ("", None),
            ("#" + _("_Save Shares List to Disk"), self.on_save),
            ("#" + _("Close All Tabs…"), self.on_close_all_tabs),
            ("#" + _("_Close Tab"), self.on_close)
        )

        self.folder_popup_menu = PopupMenu(self.window.application, self.folder_tree_view, self.on_folder_popup_menu)

        if user == config.sections["server"]["login"]:
            self.folder_popup_menu.add_items(
                ("#" + _("Upload Folder…"), self.on_upload_directory_to),
                ("#" + _("Upload Folder & Subfolders…"), self.on_upload_directory_recursive_to),
                ("", None),
                ("#" + _("Open in File _Manager"), self.on_file_manager),
                ("#" + _("F_ile Properties"), self.on_file_properties, True),
                ("", None),
                ("#" + _("Copy _Folder Path"), self.on_copy_folder_path),
                ("#" + _("Copy _URL"), self.on_copy_dir_url),
                ("", None),
                (">" + _("User"), self.user_popup_menu)
            )
        else:
            self.folder_popup_menu.add_items(
                ("#" + _("_Download Folder"), self.on_download_directory),
                ("#" + _("Download Folder _To…"), self.on_download_directory_to),
                ("#" + _("Download Folder & Subfolders"), self.on_download_directory_recursive),
                ("#" + _("Download Folder & Subfolders To…"), self.on_download_directory_recursive_to),
                ("", None),
                ("#" + _("F_ile Properties"), self.on_file_properties, True),
                ("", None),
                ("#" + _("Copy _Folder Path"), self.on_copy_folder_path),
                ("#" + _("Copy _URL"), self.on_copy_dir_url),
                ("", None),
                (">" + _("User"), self.user_popup_menu)
            )

        # Setup file_list_view
        self.treeview_name = "user_browse"
        self.file_store = Gtk.ListStore(
            str,                  # (0) file type icon
            str,                  # (1) file name
            str,                  # (2) hsize
            str,                  # (3) hbitrate
            str,                  # (4) hlength
            GObject.TYPE_UINT64,  # (5) size
            GObject.TYPE_UINT,    # (6) bitrate
            GObject.TYPE_UINT     # (7) length
        )

        self.file_column_offsets = {}
        self.file_column_numbers = list(range(self.file_store.get_n_columns()))
        cols = initialise_columns(
            self.window, "user_browse", self.file_list_view,
            ["file_type", _("File Type"), 30, "icon", None],
            ["filename", _("Filename"), 600, "text", None],
            ["size", _("Size"), 100, "number", None],
            ["bitrate", _("Bitrate"), 100, "number", None],
            ["length", _("Duration"), 100, "number", None]
        )
        cols["file_type"].set_sort_column_id(0)
        cols["filename"].set_sort_column_id(1)
        cols["size"].set_sort_column_id(5)
        cols["bitrate"].set_sort_column_id(6)
        cols["length"].set_sort_column_id(7)

        cols["file_type"].get_widget().set_visible(False)

        self.file_list_view.set_model(self.file_store)

        for column in self.file_list_view.get_columns():
            self.file_column_offsets[column.get_title()] = 0
            column.connect("notify::x-offset", self.on_column_position_changed)

        # Popup Menu (file_list_view)
        self.file_popup_menu = FilePopupMenu(self.window.application, self.file_list_view, self.on_file_popup_menu)

        if user == config.sections["server"]["login"]:
            self.file_popup_menu.add_items(
                ("#" + "selected_files", None),
                ("", None),
                ("#" + _("Up_load File(s)…"), self.on_upload_files),
                ("#" + _("Upload Folder…"), self.on_upload_directory_to),
                ("", None),
                ("#" + _("Send to _Player"), self.on_play_files),
                ("#" + _("Open in File _Manager"), self.on_file_manager),
                ("#" + _("F_ile Properties"), self.on_file_properties),
                ("", None),
                ("#" + _("Copy _File Path"), self.on_copy_file_path),
                ("#" + _("Copy _URL"), self.on_copy_url),
                ("", None),
                (">" + _("User"), self.user_popup_menu)
            )
        else:
            self.file_popup_menu.add_items(
                ("#" + "selected_files", None),
                ("", None),
                ("#" + _("_Download File(s)"), self.on_download_files),
                ("#" + _("Download File(s) _To…"), self.on_download_files_to),
                ("", None),
                ("#" + _("_Download Folder"), self.on_download_directory),
                ("#" + _("Download Folder _To…"), self.on_download_directory_to),
                ("", None),
                ("#" + _("F_ile Properties"), self.on_file_properties),
                ("", None),
                ("#" + _("Copy _File Path"), self.on_copy_file_path),
                ("#" + _("Copy _URL"), self.on_copy_url),
                ("", None),
                (">" + _("User"), self.user_popup_menu)
            )

        # Key Bindings (folder_tree_view)
        Accelerator("Left", self.folder_tree_view, self.on_folder_collapse_accelerator)
        Accelerator("minus", self.folder_tree_view, self.on_folder_collapse_accelerator)  # "-"
        Accelerator("backslash", self.folder_tree_view, self.on_folder_collapse_sub_accelerator)  # "\"
        Accelerator("equal", self.folder_tree_view, self.on_folder_expand_sub_accelerator)  # "=" (for US/UK laptop)
        Accelerator("Right", self.folder_tree_view, self.on_folder_expand_accelerator)

        Accelerator("<Shift>Return", self.folder_tree_view, self.on_folder_focus_filetree_accelerator)  # brwse into
        Accelerator("<Primary>Return", self.folder_tree_view, self.on_folder_transfer_to_accelerator)  # w/to prompt
        Accelerator("<Shift><Primary>Return", self.folder_tree_view, self.on_folder_transfer_accelerator)  # no prmt
        Accelerator("<Primary><Alt>Return", self.folder_tree_view, self.on_folder_open_manager_accelerator)
        Accelerator("<Alt>Return", self.folder_tree_view, self.on_file_properties_accelerator, True)

        # Key Bindings (file_list_view)
        for accelerator in ("<Shift>Tab", "BackSpace", "backslash"):  # Avoid header, navigate up, "\"
            Accelerator(accelerator, self.file_list_view, self.on_focus_folder_accelerator)

        Accelerator("Left", self.file_list_view, self.on_focus_folder_left_accelerator)

        Accelerator("<Shift>Return", self.file_list_view, self.on_file_transfer_multi_accelerator)  # multi activate
        Accelerator("<Primary>Return", self.file_list_view, self.on_file_transfer_to_accelerator)  # with to prompt
        Accelerator("<Shift><Primary>Return", self.file_list_view, self.on_file_transfer_accelerator)  # no prompt
        Accelerator("<Primary><Alt>Return", self.file_list_view, self.on_file_open_manager_accelerator)
        Accelerator("<Alt>Return", self.file_list_view, self.on_file_properties_accelerator)

        # Key Bindings (General)
        for widget in (self.container, self.folder_tree_view, self.file_list_view):
            Accelerator("<Primary>f", widget, self.on_search_accelerator)  # Find focus

        Accelerator("F3", self.container, self.on_search_next_accelerator)
        Accelerator("<Shift>F3", self.container, self.on_search_previous_accelerator)
        Accelerator("<Primary>g", self.container, self.on_search_next_accelerator)  # Next search match
        Accelerator("<Shift><Primary>g", self.container, self.on_search_previous_accelerator)

        Accelerator("<Primary>backslash", self.container, self.on_expand_accelerator)  # expand / collapse all (button)
        Accelerator("F5", self.container, self.on_refresh_accelerator)
        Accelerator("<Primary>r", self.container, self.on_refresh_accelerator)  # Refresh
        Accelerator("<Primary>s", self.container, self.on_save_accelerator)  # Save Shares List

        self.expand_button.set_active(config.sections["userbrowse"]["expand_folders"])
        self.set_in_progress()

    def clear(self):

        self.clear_model()

        for menu in (self.user_popup_menu, self.folder_popup_menu, self.file_popup_menu,
                     self.folder_tree_view.column_menu, self.file_list_view.column_menu):
            menu.clear()

    def set_label(self, label):
        self.user_popup_menu.set_parent(label)

    """ Folder/File Views """

    def clear_model(self):

        self.query = None
        self.search_list.clear()

        self.selected_folder = None
        self.selected_files.clear()

        self.folder_tree_view.set_model(None)
        self.file_list_view.set_model(None)

        self.dir_iters.clear()
        self.dir_user_data.clear()
        self.dir_store.clear()

        self.file_iters.clear()
        self.file_store.clear()

        self.folder_tree_view.set_model(self.dir_store)
        self.file_list_view.set_model(self.file_store)

    def make_new_model(self, shares, private_shares=None):

        self.clear_model()
        private_size = num_private_folders = 0

        # Generate the directory tree and select first directory
        size, num_folders = self.create_folder_tree(shares)

        if private_shares:
            private_size, num_private_folders = self.create_folder_tree(private_shares, private=True)

        self.share_size = size + private_size
        self.num_folders = num_folders + num_private_folders

        self.share_size_label.set_text(human_size(self.share_size))
        self.num_folders_label.set_text(humanize(self.num_folders))

        if self.expand_button.get_active():
            self.folder_tree_view.expand_all()
        else:
            self.folder_tree_view.collapse_all()

        iterator = self.dir_store.get_iter_first()

        if iterator:
            path = self.dir_store.get_path(iterator)
            self.folder_tree_view.set_cursor(path)

        self.set_finished()

    def create_folder_tree(self, shares, private=False):

        total_size = 0

        if not shares:
            num_folders = 0
            return total_size, num_folders

        for folder, files in shares:
            current_path = None
            root_processed = False

            for subfolder in folder.split("\\"):
                parent = self.dir_iters.get(current_path)

                if not root_processed:
                    current_path = subfolder
                    root_processed = True
                else:
                    current_path = "\\".join([current_path, subfolder])

                if current_path in self.dir_iters:
                    # Folder was already added to tree
                    continue

                if not subfolder:
                    # Most likely a root folder
                    subfolder = "\\"

                if private:
                    subfolder = _("[PRIVATE]  %s") % subfolder

                self.dir_iters[current_path] = iterator = self.dir_store.insert_with_values(
                    parent, -1, self.dir_column_numbers, [subfolder]
                )
                self.dir_user_data[iterator.user_data] = current_path

            for filedata in files:
                total_size += filedata[2]

        return total_size, len(shares)

    def browse_queued_path(self):

        if self.queued_path is None:
            return

        folder, filename = self.queued_path.rsplit("\\", 1)
        iterator = self.dir_iters.get(folder)

        if not iterator:
            return

        self.queued_path = None

        # Scroll to the requested folder
        path = self.dir_store.get_path(iterator)
        self.folder_tree_view.expand_to_path(path)
        self.folder_tree_view.set_cursor(path)
        self.folder_tree_view.scroll_to_cell(path, None, True, 0.5, 0.5)

        iterator = self.file_iters.get(filename)

        if not iterator:
            return

        # Scroll to the requested file
        path = self.file_store.get_path(iterator)
        self.file_list_view.set_cursor(path)
        self.file_list_view.scroll_to_cell(path, None, True, 0.5, 0.5)

    def shared_file_list(self, msg):

        self.make_new_model(msg.list, msg.privatelist)
        self.info_bar.set_visible(False)

        if msg.list or msg.privatelist:
            self.browse_queued_path()

        else:
            self.retry_button.set_visible(False)
            self.info_bar.show_message(
                _("User's list of shared files is empty. Either the user is not sharing anything, "
                  "or they are sharing files privately.")
            )

        self.set_finished()

    def peer_connection_error(self):

        if self.refresh_button.get_sensitive():
            return

        self.retry_button.set_visible(True)
        self.info_bar.show_message(
            _("Unable to request shared files from user. Either the user is offline, you both have "
              "a closed listening port, or there's a temporary connectivity issue."),
            message_type=Gtk.MessageType.ERROR
        )

        self.set_finished()

    def pulse_progress(self, repeat=True):

        if not self.indeterminate_progress:
            return False

        self.progress_bar.pulse()
        return repeat

    def set_in_progress(self):

        self.indeterminate_progress = True

        self.progress_bar.pulse()
        GLib.timeout_add(320, self.pulse_progress, False)
        GLib.timeout_add(1000, self.pulse_progress)

        self.refresh_button.set_sensitive(False)

    def shared_file_list_progress(self, position, total):

        self.indeterminate_progress = False

        if total == 0 or position == 0:
            fraction = 0.0
        elif position >= total:
            fraction = 1.0
        else:
            fraction = float(position) / total

        self.progress_bar.set_fraction(fraction)

    def set_finished(self):

        self.indeterminate_progress = False

        self.userbrowses.request_tab_changed(self.container)
        self.progress_bar.set_fraction(1.0)
        self.refresh_button.set_sensitive(True)

    def set_directory(self, iter_user_data):

        directory = self.dir_user_data.get(iter_user_data)

        if directory is None or self.selected_folder == directory:
            return

        self.file_list_view.set_model(None)

        self.file_store.clear()
        self.file_iters.clear()

        self.file_list_view.set_model(self.file_store)

        self.selected_folder = directory
        files = core.userbrowse.user_shares[self.user].get(directory)

        if not files:
            return

        # Temporarily disable sorting for increased performance
        sort_column, sort_type = self.file_store.get_sort_column_id()
        self.file_store.set_default_sort_func(lambda *_args: 0)
        self.file_store.set_sort_column_id(-1, Gtk.SortType.ASCENDING)

        selected_folder_size = 0

        for _code, filename, size, _ext, attrs, *_unused in files:
            selected_folder_size += size
            h_size = humanize(size) if config.sections["ui"]["exact_file_sizes"] else human_size(size)
            h_bitrate, bitrate, h_length, length = FileListMessage.parse_result_bitrate_length(size, attrs)

            file_row = [get_file_type_icon_name(filename),
                        filename, h_size, h_bitrate, h_length,
                        GObject.Value(GObject.TYPE_UINT64, size),
                        GObject.Value(GObject.TYPE_UINT, bitrate),
                        GObject.Value(GObject.TYPE_UINT, length)]

            self.file_iters[filename] = self.file_store.insert_with_valuesv(-1, self.file_column_numbers, file_row)

        self.selected_folder_size = selected_folder_size

        if sort_column is not None and sort_type is not None:
            self.file_store.set_sort_column_id(sort_column, sort_type)

    def select_files(self):

        self.selected_files.clear()
        model, paths = self.file_list_view.get_selection().get_selected_rows()

        for path in paths:
            iterator = model.get_iter(path)
            rawfilename = model.get_value(iterator, 1)
            filesize = model.get_value(iterator, 5)

            self.selected_files[rawfilename] = filesize

    """ Search """

    def rebuild_search_matches(self):

        self.search_list.clear()
        temp_list = set()

        for directory, files in core.userbrowse.user_shares[self.user].items():

            if self.query in directory.lower():
                temp_list.add(directory)
                continue

            for file_data in files:
                if self.query in file_data[1].lower():
                    temp_list.add(directory)

        self.search_list = sorted(temp_list)

    def select_search_match_folder(self):

        directory = self.search_list[self.search_position]
        path = self.dir_store.get_path(self.dir_iters[directory])

        self.folder_tree_view.expand_to_path(path)
        self.folder_tree_view.set_cursor(path)

    def select_search_match_files(self):

        result_files = []
        found_first_match = False

        for filepath, iterator in self.file_iters.items():
            if self.query in filepath.lower():
                result_files.append(iterator)

        selection = self.file_list_view.get_selection()
        selection.unselect_all()

        for iterator in result_files:
            # Select each matching file in folder
            selection.select_iter(iterator)

            if found_first_match:
                continue

            # Position cursor at first match
            path = self.file_store.get_path(iterator)
            self.file_list_view.scroll_to_cell(path, None, True, 0.5, 0.5)
            found_first_match = True

    def find_search_matches(self, reverse=False):

        query = self.search_entry.get_text().lower()

        if not query:
            return False

        if self.query != query:
            # New search query, rebuild result list
            self.search_position = 0
            self.query = query

            self.rebuild_search_matches()
        else:
            # Increment/decrement search position
            self.search_position += -1 if reverse else 1

        if not self.search_list:
            return False

        if self.search_position < 0:
            self.search_position = len(self.search_list) - 1

        elif self.search_position >= len(self.search_list):
            self.search_position = 0

        # Set active folder
        self.select_search_match_folder()

        # Get matching files in the current folder
        self.select_search_match_files()
        return True

    """ Callbacks (folder_tree_view) """

    @staticmethod
    def on_folder_tooltip(widget, pos_x, pos_y, _keyboard_mode, tooltip):

        file_path_tooltip = show_file_path_tooltip(widget, pos_x, pos_y, tooltip, 0)

        if file_path_tooltip:
            return file_path_tooltip

        return False

    def on_select_dir(self, selection):

        _model, iterator = selection.get_selected()

        if iterator is None:
            return

        self.set_directory(iterator.user_data)

    def on_folder_popup_menu(self, *_args):
        self.user_popup_menu.toggle_user_items()

    def on_download_directory(self, *_args):

        if self.selected_folder is not None:
            core.userbrowse.download_folder(self.user, self.selected_folder)

    def on_download_directory_recursive(self, *_args):
        core.userbrowse.download_folder(self.user, self.selected_folder, prefix="", recurse=True)

    def on_download_directory_to_selected(self, selected, recurse):
        core.userbrowse.download_folder(self.user, self.selected_folder,
                                        prefix=os.path.join(selected, ""), recurse=recurse)

    def on_download_directory_to(self, *_args, recurse=False):

        if recurse:
            str_title = _("Select Destination for Downloading Multiple Folders")
        else:
            str_title = _("Select Destination Folder")

        FolderChooser(
            parent=self.window,
            title=str_title,
            callback=self.on_download_directory_to_selected,
            callback_data=recurse,
            initial_folder=config.sections["transfers"]["downloaddir"]
        ).show()

    def on_download_directory_recursive_to(self, *_args):
        self.on_download_directory_to(recurse=True)

    def on_upload_directory_to_response(self, dialog, _response_id, recurse):

        user = dialog.get_entry_value()
        folder = self.selected_folder

        if not user or folder is None:
            return

        core.userbrowse.send_upload_attempt_notification(user)
        core.userbrowse.upload_folder(user, folder, recurse=recurse)

    def on_upload_directory_to(self, *_args, recurse=False):

        folder = self.selected_folder

        if folder is None:
            return

        if recurse:
            str_title = _("Upload Folder (with Subfolders) To User")
        else:
            str_title = _("Upload Folder To User")

        EntryDialog(
            parent=self.window,
            title=str_title,
            message=_("Enter the name of the user you want to upload to:"),
            callback=self.on_upload_directory_to_response,
            callback_data=recurse,
            droplist=sorted(core.userlist.buddies)
        ).show()

    def on_upload_directory_recursive_to(self, *_args):
        self.on_upload_directory_to(recurse=True)

    def on_copy_folder_path(self, *_args):

        if self.selected_folder is None:
            return

        copy_text(self.selected_folder)

    def on_copy_dir_url(self, *_args):

        if self.selected_folder is None:
            return

        path = self.selected_folder + "\\"
        url = core.userbrowse.get_soulseek_url(self.user, path)
        copy_text(url)

    """ Key Bindings (folder_tree_view) """

    def on_folder_row_activated(self, _treeview, path, _column):

        if path is None:
            return

        # Keyboard accessibility support for <Return> key behaviour
        if self.folder_tree_view.row_expanded(path):
            expandable = self.folder_tree_view.collapse_row(path)
        else:
            expandable = self.folder_tree_view.expand_row(path, False)

        if not expandable and len(self.file_store) > 0:
            # This is the deepest level, so move focus over to Files if there are any
            self.file_list_view.grab_focus()

        # Note: Other Folder actions are handled by Accelerator functions [Shift/Ctrl/Alt+Return]
        # TODO: Mouse double-click actions will need *_args for keycode state & mods [Ctrl/Alt+DblClick]

    def on_folder_collapse_accelerator(self, *_args):
        """ Left: collapse row
            Shift+Left (Gtk) | "-" | "/" (Gtk) | """

        path, _focus_column = self.folder_tree_view.get_cursor()

        if path is None:
            return False

        self.folder_tree_view.collapse_row(path)
        return True

    def on_folder_expand_accelerator(self, *_args):
        """ Right: expand row
            Shift+Right (Gtk) | "+" (Gtk) |    """

        path, _focus_column = self.folder_tree_view.get_cursor()

        if path is None:
            return False

        expandable = self.folder_tree_view.expand_row(path, False)

        if not expandable and len(self.file_store) > 0:
            self.file_list_view.grab_focus()

        return True

    def on_folder_collapse_sub_accelerator(self, *_args):
        """ \backslash: collapse or expand to show subs """

        path, _focus_column = self.folder_tree_view.get_cursor()

        if path is None:
            return False

        self.folder_tree_view.collapse_row(path)  # show 2nd level
        self.folder_tree_view.expand_row(path, False)
        return True

    def on_folder_expand_sub_accelerator(self, *_args):
        """ =equal: expand only (dont move focus)   """

        path, _focus_column = self.folder_tree_view.get_cursor()

        if path is None:
            return False

        self.folder_tree_view.expand_row(path, False)
        return True

    def on_folder_focus_filetree_accelerator(self, *_args):
        """ Shift+Enter: focus selection over FileTree  """

        if len(self.file_store) >= 1:
            self.file_list_view.grab_focus()
            return True

        self.on_folder_expand_sub_accelerator()
        return True

    def on_folder_transfer_to_accelerator(self, *_args):
        """ Ctrl+Enter: Upload Folder To...
                        Download Folder Into...         """

        if self.user == config.sections["server"]["login"]:
            if len(self.file_store) >= 1:
                self.on_upload_directory_to()
            else:
                self.on_upload_directory_recursive_to()

        elif len(self.file_store) >= 1:
            self.on_download_directory_to()

        return True

    def on_folder_transfer_accelerator(self, *_args):
        """ Shift+Ctrl+Enter: Upload Folder Recursive To...
            (without prompt)  Download Folder           """

        if self.user == config.sections["server"]["login"]:
            self.on_folder_expand_sub_accelerator()
            self.on_upload_directory_recursive_to()
            return True

        if len(self.file_store) <= 0:
            # don't risk accidental recursive download
            self.on_folder_expand_sub_accelerator()
            return True

        self.on_download_directory()  # without prompt
        return True

    def on_folder_open_manager_accelerator(self, *_args):
        """ Ctrl+Alt+Enter: Open folder in File Manager... """

        if self.user != config.sections["server"]["login"]:
            return False

        self.on_file_manager()
        return True

    """ Callbacks (file_list_view) """

    @staticmethod
    def on_file_tooltip(widget, pos_x, pos_y, _keyboard_mode, tooltip):

        file_path_tooltip = show_file_path_tooltip(widget, pos_x, pos_y, tooltip, 1)

        if file_path_tooltip:
            return file_path_tooltip

        return show_file_type_tooltip(widget, pos_x, pos_y, tooltip, 0)

    def on_column_position_changed(self, column, _param):
        """ Save column position and width to config """

        col_title = column.get_title()
        offset = column.get_x_offset()

        if self.file_column_offsets[col_title] == offset:
            return

        self.file_column_offsets[col_title] = offset
        save_columns(self.treeview_name, self.file_list_view.get_columns())

    def on_file_popup_menu(self, menu, _widget):

        self.select_files()
        menu.set_num_selected_files(len(self.selected_files))

        self.user_popup_menu.toggle_user_items()

    def on_download_files(self, *_args, prefix=""):

        folder = self.selected_folder
        files = core.userbrowse.user_shares[self.user].get(folder)

        if not files:
            return

        for file_data in files:
            # Find the wanted file
            if file_data[1] not in self.selected_files:
                continue

            core.userbrowse.download_file(self.user, folder, file_data, prefix=prefix)

    def on_download_files_to_selected(self, selected, _data):
        self.on_download_files(prefix=selected)

    def on_download_files_to(self, *_args):

        try:
            _path_start, folder = self.selected_folder.rsplit("\\", 1)
        except ValueError:
            folder = self.selected_folder

        download_folder = config.sections["transfers"]["downloaddir"]
        path = os.path.join(download_folder, folder)

        if not os.path.isdir(path.encode("utf-8")):
            path = download_folder

        FolderChooser(
            parent=self.window,
            title=_("Select Destination Folder for Files"),
            callback=self.on_download_files_to_selected,
            initial_folder=path
        ).show()

    def on_upload_files_response(self, dialog, _response_id, _data):

        user = dialog.get_entry_value()
        folder = self.selected_folder

        if not user or folder is None:
            return

        core.userbrowse.send_upload_attempt_notification(user)

        for basename, size in self.selected_files.items():
            core.userbrowse.upload_file(user, folder, (None, basename, size))

    def on_upload_files(self, *_args):

        EntryDialog(
            parent=self.window,
            title=_("Upload File(s) To User"),
            message=_("Enter the name of the user you want to upload to:"),
            callback=self.on_upload_files_response,
            droplist=sorted(core.userlist.buddies)
        ).show()

    def on_play_files(self, *_args):

        path = core.shares.virtual2real(self.selected_folder)

        for base_name in self.selected_files:
            open_file_path(file_path=os.path.join(path, base_name),
                           command=config.sections["players"]["default"])

    def on_file_manager(self, *_args):

        if self.selected_folder is None:
            return

        open_file_path(file_path=core.shares.virtual2real(self.selected_folder),
                       command=config.sections["ui"]["filemanager"])

    def on_file_properties(self, _action, _state, all_files=False):

        data = []
        folder = self.selected_folder
        selected_size = 0
        selected_length = 0

        if all_files:
            files = core.userbrowse.user_shares[self.user].get(folder)

            if not files:
                return

            for file_data in files:
                filename = file_data[1]
                file_size = file_data[2]
                virtual_path = "\\".join([folder, filename])
                h_bitrate, _bitrate, h_length, length = FileListMessage.parse_result_bitrate_length(
                    file_size, file_data[4])
                selected_size += file_size
                selected_length += length

                data.append({"user": self.user, "fn": virtual_path, "filename": filename,
                             "directory": folder, "size": file_size, "bitrate": h_bitrate, "length": h_length})

        else:
            model, paths = self.file_list_view.get_selection().get_selected_rows()

            for path in paths:
                iterator = model.get_iter(path)
                filename = model.get_value(iterator, 1)
                file_size = model.get_value(iterator, 5)
                virtual_path = "\\".join([folder, filename])
                selected_size += file_size
                selected_length += model.get_value(iterator, 7)

                data.append({"user": self.user, "fn": virtual_path, "filename": filename,
                             "directory": folder, "size": file_size, "bitrate": model.get_value(iterator, 3),
                             "length": model.get_value(iterator, 4)})

        if data:
            if self.userbrowses.file_properties is None:
                self.userbrowses.file_properties = FileProperties(self.window.application, core)

            self.userbrowses.file_properties.update_properties(data, selected_size, selected_length)
            self.userbrowses.file_properties.show()

    def on_copy_file_path(self, *_args):

        if self.selected_folder is None or not self.selected_files:
            return

        text = "\\".join([self.selected_folder, next(iter(self.selected_files))])
        copy_text(text)

    def on_copy_url(self, *_args):

        if not self.selected_files:
            return

        path = "\\".join([self.selected_folder, next(iter(self.selected_files))])
        url = core.userbrowse.get_soulseek_url(self.user, path)
        copy_text(url)

    """ Key Bindings (file_list_view) """

    def on_file_row_activated(self, _treeview, _path, _column):

        self.select_files()

        if self.user == config.sections["server"]["login"]:
            self.on_play_files()
        else:
            self.on_download_files()

    def on_focus_folder_left_accelerator(self, *_args):
        """ Left: focus back parent folder (left arrow) """

        _path, column = self.file_list_view.get_cursor()

        if self.file_list_view.get_column(0) != column:
            return False  # allow horizontal scrolling

        self.folder_tree_view.grab_focus()
        return True

    def on_focus_folder_accelerator(self, *_args):
        """ Shift+Tab: focus selection back parent folder
            BackSpace | \backslash |                  """

        self.folder_tree_view.grab_focus()
        return True

    def on_file_transfer_to_accelerator(self, *_args):
        """ Ctrl+Enter: Upload File(s) To...
                        Download File(s) Into...  """

        if len(self.file_store) <= 0:  # avoid navigation trap
            self.folder_tree_view.grab_focus()
            return True

        if self.file_list_view.get_selection().count_selected_rows() <= 0:  # do folder instead
            self.on_folder_transfer_to_accelerator()
            return True

        self.select_files()

        if self.user == config.sections["server"]["login"]:
            self.on_upload_files()
            return True

        self.on_download_files_to()  # (with prompt, Single or Multi-selection)
        return True

    def on_file_transfer_accelerator(self, *_args):
        """ Shift+Ctrl+Enter: Upload File(s) To...
            (without prompt)  Download File(s) """

        if len(self.file_store) <= 0:
            self.folder_tree_view.grab_focus()  # avoid nav trap
            return True

        self.select_files()

        if self.user == config.sections["server"]["login"]:
            if self.file_list_view.get_selection().count_selected_rows() >= 1:
                self.on_upload_files()
            else:
                self.on_upload_directory_to()

        else:  # [user is not self]
            if self.file_list_view.get_selection().count_selected_rows() >= 1:
                self.on_download_files()  # (no prompt, Single or Multi-selection)
            else:
                self.on_download_directory()  # (without prompt, No-selection=All)

        return True

    def on_file_transfer_multi_accelerator(self, *_args):
        """ Shift+Enter: Send to Player (multiple files)
                         Download Files (multiple)   """

        if len(self.file_store) <= 0:
            self.folder_tree_view.grab_focus()  # avoid nav trap
            return True

        self.select_files()  # support multi-select with Up/Dn keys

        if self.user == config.sections["server"]["login"]:
            self.on_play_files()
        else:
            self.on_download_files()

        return True

    def on_file_open_manager_accelerator(self, *_args):
        """ Ctrl+Alt+Enter: Open in File Manager """

        if self.user == config.sections["server"]["login"]:
            self.on_file_manager()

        else:  # [user is not self]
            self.on_file_properties_accelerator()  # same as Alt+Enter

        return True

    def on_file_properties_accelerator(self, *_args):
        """ Alt+Enter: show file properties dialog """

        if len(self.file_store) <= 0:
            self.folder_tree_view.grab_focus()  # avoid nav trap

        self.on_file_properties(*_args)
        return True

    """ Callbacks (General) """

    def on_expand(self, *_args):

        active = self.expand_button.get_active()

        if active:
            self.folder_tree_view.expand_all()
            self.expand_icon.set_property("icon-name", "go-up-symbolic")
        else:
            self.folder_tree_view.collapse_all()
            self.expand_icon.set_property("icon-name", "go-down-symbolic")

        config.sections["userbrowse"]["expand_folders"] = active

    def on_tab_popup(self, *_args):
        self.user_popup_menu.toggle_user_items()

    def on_search(self, *_args):
        self.find_search_matches()

    def on_save(self, *_args):
        core.userbrowse.save_shares_list_to_disk(self.user)

    def on_refresh(self, *_args):

        if not self.refresh_button.get_sensitive():
            # Refresh is already in progress
            return

        self.clear_model()
        self.folder_tree_view.grab_focus()
        self.info_bar.set_visible(False)

        self.set_in_progress()
        core.userbrowse.browse_user(self.user, local_shares_type=self.local_shares_type, new_request=True)

    def on_focus(self):

        if self.file_list_view.get_selection().count_selected_rows() >= 1:
            self.file_list_view.grab_focus()
            return

        self.folder_tree_view.grab_focus()

    def on_close(self, *_args):
        core.userbrowse.remove_user(self.user)

    def on_close_all_tabs(self, *_args):
        self.userbrowses.remove_all_pages()

    """ Key Bindings (General) """

    def on_expand_accelerator(self, *_args):
        """ Ctrl+\backslash: Expand / Collapse All """

        self.expand_button.set_active(not self.expand_button.get_active())
        return True

    def on_save_accelerator(self, *_args):
        """ Ctrl+S: Save Shares List """

        self.on_save()
        return True

    def on_refresh_accelerator(self, *_args):
        """ Ctrl+R or F5: Refresh """

        self.on_refresh()
        return True

    def on_search_accelerator(self, *_args):
        """ Ctrl+F: Find """

        self.search_entry.grab_focus()
        return True

    def on_search_next_accelerator(self, *_args):
        """ Ctrl+G or F3: Find Next """

        if not self.find_search_matches():
            self.search_entry.grab_focus()

        return True

    def on_search_previous_accelerator(self, *_args):
        """ Shift+Ctrl+G or Shift+F3: Find Previous """

        if not self.find_search_matches(reverse=True):
            self.search_entry.grab_focus()

        return True

    def on_search_escape_accelerator(self, *_args):
        """ Escape: navigate out of search_entry """

        if self.file_list_view.get_selection().count_selected_rows() >= 1:
            self.file_list_view.grab_focus()
        else:
            self.folder_tree_view.grab_focus()

        return True
