# COPYRIGHT (C) 2020-2022 Nicotine+ Contributors
# COPYRIGHT (C) 2016-2017 Michael Labouebe <gfarmerfr@free.fr>
# COPYRIGHT (C) 2009-2011 quinox <quinox@users.sf.net>
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

from pynicotine.config import config
from pynicotine.core import core
from pynicotine.gtkgui.widgets.filechooser import FileChooserButton
from pynicotine.gtkgui.widgets.filechooser import FolderChooser
from pynicotine.gtkgui.widgets.dialogs import Dialog
from pynicotine.gtkgui.widgets.dialogs import EntryDialog
from pynicotine.gtkgui.widgets.treeview import TreeView
from pynicotine.gtkgui.widgets.ui import UserInterface


class FastConfigure(Dialog):

    def __init__(self, application):

        self.rescan_required = False
        self.finished = False

        ui_template = UserInterface(scope=self, path="dialogs/fastconfigure.ui")
        (
            self.account_page,
            self.download_folder_button,
            self.first_port_entry,
            self.last_port_entry,
            self.main_icon,
            self.next_button,
            self.password_entry,
            self.port_page,
            self.previous_button,
            self.set_up_button,
            self.share_page,
            self.shares_list_container,
            self.stack,
            self.summary_page,
            self.username_entry,
            self.welcome_page
        ) = ui_template.widgets

        self.pages = [self.welcome_page, self.account_page, self.port_page, self.share_page, self.summary_page]

        super().__init__(
            parent=application.window,
            content_box=self.stack,
            buttons_start=(self.previous_button,),
            buttons_end=(self.next_button,),
            default_button=self.next_button,
            show_callback=self.on_show,
            close_callback=self.on_close,
            title=_("Setup Assistant"),
            width=720,
            height=450,
            resizable=False,
            show_title=False,
            close_destroy=False
        )

        self.main_icon.set_property("icon-name", config.application_id)

        # Page specific, share_page
        self.download_folder_button = FileChooserButton(
            self.download_folder_button, self, "folder", selected_function=self.on_download_folder_selected)

        self.shares_list_view = TreeView(
            application.window, parent=self.shares_list_container, multi_select=True,
            activate_row_callback=self.on_edit_shared_folder,
            columns={
                "virtual_folder": {
                    "column_type": "text",
                    "title": _("Virtual Folder"),
                    "width": 1,
                    "expand_column": True
                },
                "folder": {
                    "column_type": "text",
                    "title": _("Folder"),
                    "width": 125,
                    "expand_column": True
                }
            }
        )

        self.reset_completeness()

    def reset_completeness(self):
        """ Turns on the complete flag if everything required is filled in. """

        page = self.stack.get_visible_child()
        page_complete = (
            (page in (self.welcome_page, self.port_page, self.summary_page))
            or (page == self.account_page and self.username_entry.get_text() and self.password_entry.get_text())
            or (page == self.share_page and self.download_folder_button.get_path())
        )
        self.finished = (page == self.summary_page)
        next_label = _("_Finish") if page == self.summary_page else _("_Next")

        if self.next_button.get_label() != next_label:
            self.next_button.set_label(next_label)

        self.next_button.set_sensitive(page_complete)

        for button in (self.previous_button, self.next_button):
            button.set_visible(page != self.welcome_page)

    def on_entry_changed(self, *_args):
        self.reset_completeness()

    def on_download_folder_selected(self):
        config.sections["transfers"]["downloaddir"] = self.download_folder_button.get_path()

    def on_add_shared_folder_selected(self, selected, _data):

        shared_folders = config.sections["transfers"]["shared"]
        buddy_shared_folders = config.sections["transfers"]["buddyshared"]

        for folder_path in selected:
            if folder_path is None:
                continue

            if folder_path in (x[1] for x in shared_folders + buddy_shared_folders):
                continue

            self.rescan_required = True

            virtual_name = core.shares.get_normalized_virtual_name(
                os.path.basename(os.path.normpath(folder_path)),
                shared_folders=(shared_folders + buddy_shared_folders)
            )
            mapping = (virtual_name, folder_path)

            self.shares_list_view.add_row(mapping)
            config.sections["transfers"]["shared"].append(mapping)

    def on_add_shared_folder(self, *_args):

        FolderChooser(
            parent=self,
            title=_("Add a Shared Folder"),
            callback=self.on_add_shared_folder_selected,
            select_multiple=True
        ).show()

    def on_edit_shared_folder_response(self, dialog, _response_id, iterator):

        virtual_name = dialog.get_entry_value()

        if not virtual_name:
            return

        self.rescan_required = True

        shared_folders = config.sections["transfers"]["shared"]
        buddy_shared_folders = config.sections["transfers"]["buddyshared"]

        virtual_name = core.shares.get_normalized_virtual_name(
            virtual_name, shared_folders=(shared_folders + buddy_shared_folders)
        )
        old_virtual_name = self.shares_list_view.get_row_value(iterator, "virtual_folder")
        folder_path = self.shares_list_view.get_row_value(iterator, "folder")

        old_mapping = (old_virtual_name, folder_path)
        new_mapping = (virtual_name, folder_path)

        shared_folders.remove(old_mapping)
        shared_folders.append(new_mapping)

        self.shares_list_view.set_row_value(iterator, "virtual_folder", virtual_name)

    def on_edit_shared_folder(self, *_args):

        for iterator in self.shares_list_view.get_selected_rows():
            virtual_name = self.shares_list_view.get_row_value(iterator, "virtual_folder")
            folder_path = self.shares_list_view.get_row_value(iterator, "folder")

            EntryDialog(
                parent=self,
                title=_("Edit Shared Folder"),
                message=_("Enter new virtual name for '%(dir)s':") % {"dir": folder_path},
                default=virtual_name,
                callback=self.on_edit_shared_folder_response,
                callback_data=iterator
            ).show()
            return

    def on_remove_shared_folder(self, *_args):

        for iterator in reversed(self.shares_list_view.get_selected_rows()):
            virtual_name = self.shares_list_view.get_row_value(iterator, "virtual_folder")
            folder_path = self.shares_list_view.get_row_value(iterator, "folder")
            mapping = (virtual_name, folder_path)

            config.sections["transfers"]["shared"].remove(mapping)
            self.shares_list_view.remove_row(iterator)

            self.rescan_required = True

    def on_page_change(self, *_args):

        page = self.stack.get_visible_child()

        if page == self.welcome_page:
            self.set_up_button.grab_focus()

        elif page == self.account_page:
            self.username_entry.grab_focus()

        self.reset_completeness()

    def on_next(self, *_args):

        if self.finished:
            self.close()
            return

        start_page_index = self.pages.index(self.stack.get_visible_child()) + 1

        for page in self.pages[start_page_index:]:
            if page.get_visible():
                self.next_button.grab_focus()
                self.stack.set_visible_child(page)
                return

    def on_previous(self, *_args):

        start_page_index = self.pages.index(self.stack.get_visible_child())

        for page in reversed(self.pages[:start_page_index]):
            if page.get_visible():
                self.previous_button.grab_focus()
                self.stack.set_visible_child(page)
                return

    def on_close(self, *_args):

        if self.rescan_required:
            core.shares.rescan_shares()

        if not self.finished:
            return True

        # port_page
        first_port = min(self.first_port_entry.get_value_as_int(), self.last_port_entry.get_value_as_int())
        last_port = max(self.first_port_entry.get_value_as_int(), self.last_port_entry.get_value_as_int())
        config.sections["server"]["portrange"] = (first_port, last_port)

        # account_page
        if config.need_config():
            config.sections["server"]["login"] = self.username_entry.get_text()
            config.sections["server"]["passw"] = self.password_entry.get_text()

        core.connect()
        return True

    def on_show(self, *_args):

        self.rescan_required = False
        self.stack.set_visible_child(self.welcome_page)

        # welcome_page
        self.set_up_button.grab_focus()

        # account_page
        self.account_page.set_visible(config.need_config())

        self.username_entry.set_text(config.sections["server"]["login"])
        self.password_entry.set_text(config.sections["server"]["passw"])

        # port_page
        first_port, last_port = config.sections["server"]["portrange"]
        self.first_port_entry.set_value(first_port)
        self.last_port_entry.set_value(last_port)

        # share_page
        if config.sections["transfers"]["downloaddir"]:
            self.download_folder_button.set_path(
                config.sections["transfers"]["downloaddir"]
            )

        self.shares_list_view.clear()

        for entry in config.sections["transfers"]["shared"]:
            virtual_name, path = entry
            self.shares_list_view.add_row([str(virtual_name), str(path)], select_row=False)
