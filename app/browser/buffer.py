#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (C) 2018 Andy Stewart
#
# Author:     Andy Stewart <lazycat.manatee@gmail.com>
# Maintainer: Andy Stewart <lazycat.manatee@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from PyQt5.QtCore import QUrl
from core.browser import BrowserBuffer
from urllib.parse import urlparse
from core.utils import touch, interactive, is_port_in_use
import os
import subprocess
import sqlite3
import re

class AppBuffer(BrowserBuffer):
    def __init__(self, buffer_id, url, config_dir, arguments, emacs_var_dict, module_path):
        BrowserBuffer.__init__(self, buffer_id, url, config_dir, arguments, emacs_var_dict, module_path, False)

        self.config_dir = config_dir

        # When arguments is "temp_html_file", browser will load content of html file, then delete temp file.
        # Usually use for render html mail.
        if arguments == "temp_html_file":
            with open(url, "r") as html_file:
                self.buffer_widget.setHtml(html_file.read())
                if os.path.exists(url):
                    os.remove(url)
        else:
            self.buffer_widget.setUrl(QUrl(url))

        self.history_list = []
        if self.emacs_var_dict["eaf-browser-remember-history"] == "true":
            self.history_log_file_path = os.path.join(self.config_dir, "browser", "history", "log.txt")

            self.history_pattern = re.compile("^(.+)ᛝ(.+)ᛡ(.+)$")
            self.noprefix_url_pattern = re.compile("^(https?|file)://(.+)")
            self.nopostfix_url_pattern = re.compile("^[^#\?]*")
            self.history_close_file_path = os.path.join(self.config_dir, "browser", "history", "close.txt")
            touch(self.history_log_file_path)
            with open(self.history_log_file_path, "r", encoding="utf-8") as f:
                raw_list = f.readlines()
                for raw_his in raw_list:
                    his_line = re.match(self.history_pattern, raw_his)
                    if his_line is None: # Obsolete Old history format
                        old_his = re.match("(.*)\s((https?|file):[^\s]+)$", raw_his)
                        if old_his is not None:
                            self.history_list.append(HistoryPage(old_his.group(1), old_his.group(2), 1))
                    else:
                        self.history_list.append(HistoryPage(his_line.group(1), his_line.group(2), his_line.group(3)))

        self.autofill = PasswordDb(os.path.join(os.path.dirname(config_dir), "browser", "password.db"))
        self.pw_autofill_id = 0
        self.pw_autofill_raw = self.buffer_widget.read_js_content("pw_autofill.js")

        self.close_page.connect(self.record_close_page)

        self.buffer_widget.titleChanged.connect(self.record_history)
        self.buffer_widget.titleChanged.connect(self.change_title)

        self.buffer_widget.translate_selected_text.connect(self.translate_text)

        self.buffer_widget.open_url_in_new_tab.connect(self.open_url_in_new_tab)
        self.buffer_widget.duplicate_page_in_new_tab.connect(self.duplicate_page_in_new_tab)
        self.buffer_widget.open_url_in_background_tab.connect(self.open_url_in_background_tab)

        self.buffer_widget.urlChanged.connect(self.set_adblocker)
        self.buffer_widget.urlChanged.connect(self.caret_exit)

        # Record url when url changed.
        self.buffer_widget.urlChanged.connect(self.update_url)

    def after_page_load_hook(self):
        ''' Hook to run after update_progress hits 100. '''
        self.init_pw_autofill()
        if self.emacs_var_dict["eaf-browser-enable-adblocker"] == "true":
            self.load_adblocker()

    def handle_input_response(self, callback_tag, result_content):
        ''' Handle input message.'''
        if not BrowserBuffer.handle_input_response(self, callback_tag, result_content):
            if callback_tag == "clear_history":
                self._clear_history()
            elif callback_tag == "import_chrome_history":
                self._import_chrome_history()
            elif callback_tag == "clear_cookies":
                self._clear_cookies()

    def try_start_aria2_daemon(self):
        ''' Try to start aria2 daemon.'''
        if not is_port_in_use(6800):
            with open(os.devnull, "w") as null_file:
                aria2_args = ["aria2c"]

                aria2_args.append("-d") # daemon
                aria2_args.append("-c") # continue download
                aria2_args.append("--auto-file-renaming=false") # not auto rename file
                aria2_args.append("-d {}".format(os.path.expanduser(str(self.emacs_var_dict["eaf-browser-download-path"]))))

                aria2_proxy_host = str(self.emacs_var_dict["eaf-browser-aria2-proxy-host"])
                aria2_proxy_port = str(self.emacs_var_dict["eaf-browser-aria2-proxy-port"])

                if aria2_proxy_host != "" and aria2_proxy_port != "":
                    aria2_args.append("--all-proxy")
                    aria2_args.append("http://{0}:{1}".format(aria2_proxy_host, aria2_proxy_port))

                aria2_args.append("--enable-rpc")
                aria2_args.append("--rpc-listen-all")

                subprocess.Popen(aria2_args, stdout=null_file)

    @interactive(insert_or_do=True)
    def open_downloads_setting(self):
        ''' Open aria2 download manage page. '''
        self.try_start_aria2_daemon()
        index_file = os.path.join(os.path.dirname(__file__), "aria2-ng", "index.html")
        self.buffer_widget.open_url_new_buffer(QUrl.fromLocalFile(index_file).toString())

    def record_close_page(self, url):
        ''' Record closing pages.'''
        self.page_closed = True
        if self.emacs_var_dict["eaf-browser-remember-history"] == "true" and self.arguments != "temp_html_file" and url != "about:blank":
            touch(self.history_close_file_path)
            with open(self.history_close_file_path, "r") as f:
                close_urls = f.readlines()
                close_urls.append("{0}\n".format(url))
                if len(close_urls) > 30:
                    del close_urls[:len(close_urls)-30]
                open(self.history_close_file_path, "w").writelines(close_urls)

    def load_adblocker(self):
        self.buffer_widget.load_css(os.path.join(os.path.dirname(__file__), "adblocker.css"),'adblocker')

    @interactive
    def toggle_adblocker(self):
        ''' Change adblocker status.'''
        if self.emacs_var_dict["eaf-browser-enable-adblocker"] == "true":
            self.set_emacs_var.emit("eaf-browser-enable-adblocker", "false", "true")
            self.buffer_widget.remove_css('adblocker',True)
            self.message_to_emacs.emit("Successfully disabled adblocker!")
        elif self.emacs_var_dict["eaf-browser-enable-adblocker"] == "false":
            self.set_emacs_var.emit("eaf-browser-enable-adblocker", "true", "true")
            self.load_adblocker()
            self.message_to_emacs.emit("Successfully enabled adblocker!")

    def update_url(self, url):
        self.url = self.buffer_widget.url().toString()

    def set_adblocker(self, url):
        if self.emacs_var_dict["eaf-browser-enable-adblocker"] == "true" and not self.page_closed:
            self.load_adblocker()

    def add_password_entry(self):
        self.buffer_widget.eval_js(self.pw_autofill_raw.replace("%1", "''"))
        password, form_data = self.buffer_widget.execute_js("retrievePasswordFromPage();")
        if password != "":
            self.autofill.add_entry(urlparse(self.current_url).hostname, password, form_data)
            self.message_to_emacs.emit("Successfully recorded this page's password!")
            return True
        else:
            self.message_to_emacs.emit("There is no password present in this page!")
            return False

    def pw_autofill_gen_id(self, id):
        result = self.autofill.get_entries(urlparse(self.url).hostname, id)
        new_id = 0
        for row in result:
            new_id = row[0]
            password = row[2]
            form_data = row[3]
            self.buffer_widget.eval_js(self.pw_autofill_raw.replace("%1", form_data))
            self.buffer_widget.eval_js('autofillPassword("%s");' % password)
            break
        return new_id

    def init_pw_autofill(self):
        if self.emacs_var_dict["eaf-browser-enable-autofill"] == "true":
            self.pw_autofill_id = self.pw_autofill_gen_id(0)

    @interactive
    def save_page_password(self):
        ''' Record form data.'''
        if self.emacs_var_dict["eaf-browser-enable-autofill"] == "true":
            self.add_password_entry()
        else:
            self.message_to_emacs.emit("Password autofill is not enabled! Enable with `C-t` (default binding)")

    @interactive
    def toggle_password_autofill(self):
        ''' Toggle Autofill status for password data'''
        if self.emacs_var_dict["eaf-browser-enable-autofill"] == "false":
            self.set_emacs_var.emit("eaf-browser-enable-autofill", "true", "true")
            self.pw_autofill_id = self.pw_autofill_gen_id(0)
            self.message_to_emacs.emit("Successfully enabled autofill!")
        else:
            self.pw_autofill_id = self.pw_autofill_gen_id(self.pw_autofill_id)
            if self.pw_autofill_id == 0:
                self.set_emacs_var.emit("eaf-browser-enable-autofill", "false", "true")
                self.message_to_emacs.emit("Successfully disabled password autofill!")
            else:
                self.message_to_emacs.emit("Successfully changed password autofill id!")

    def _record_history(self, new_title, new_url):
        # Throw traceback info if algorithm has bug and protection of historical record is not erased.
        try:
            noprefix_new_url_match = re.match(self.noprefix_url_pattern, new_url)
            if noprefix_new_url_match is not None:
                found = False
                for history in self.history_list:
                    noprefix_url_match = re.match(self.noprefix_url_pattern, history.url)
                    if noprefix_url_match is not None:
                        noprefix_url = noprefix_url_match.group(2)
                        noprefix_new_url = noprefix_new_url_match.group(2)
                        nopostfix_new_url_match = re.match(self.nopostfix_url_pattern, noprefix_new_url)

                        if noprefix_url == noprefix_new_url: # found unique url
                            history.title = new_title
                            history.url = new_url
                            history.hit += 0.5
                            found = True
                        elif nopostfix_new_url_match is not None and noprefix_url == nopostfix_new_url_match.group():
                            # also increment parent
                            history.hit += 0.25

                if not found:
                    self.history_list.append(HistoryPage(new_title, new_url, 1))

            self.history_list.sort(key = lambda x: x.hit, reverse = True)

            with open(self.history_log_file_path, "w", encoding="utf-8") as f:
                f.writelines(map(lambda history: history.title + "ᛝ" + history.url + "ᛡ" + str(history.hit) + "\n", self.history_list))
        except Exception:
            import traceback
            self.message_to_emacs.emit("Error in record_history: " + str(traceback.print_exc()))

    def record_history(self, new_title):
        ''' Record browser history.'''
        new_url = self.buffer_widget.filter_url(self.buffer_widget.get_url())
        if self.emacs_var_dict["eaf-browser-remember-history"] == "true" and self.buffer_widget.filter_title(new_title) != "" and \
           self.arguments != "temp_html_file" and new_title != "about:blank" and new_url != "about:blank":
            self._record_history(new_title, new_url)

    @interactive(insert_or_do=True)
    def new_blank_page(self):
        ''' Open new blank page.'''
        self.eval_in_emacs.emit('eaf-open', [self.emacs_var_dict["eaf-browser-blank-page-url"], "browser", "", 't'])

    def _clear_history(self):
        if os.path.exists(self.history_log_file_path):
            os.remove(self.history_log_file_path)
            self.message_to_emacs.emit("Cleared browsing history.")
        else:
            self.message_to_emacs.emit("There is no browsing history.")

    @interactive
    def clear_history(self):
        ''' Clear browsing history.'''
        self.send_input_message("Are you sure you want to clear all browsing history?", "clear_history", "yes-or-no")

    def _import_chrome_history(self):
        dbpath = os.path.expanduser(self.emacs_var_dict["eaf-browser-chrome-history-file"])
        if not os.path.exists(dbpath):
            self.message_to_emacs.emit("The chrome history file: '{}' not exist, please check your setting.".format(dbpath))
            return

        self.message_to_emacs.emit("Importing from {}...".format(dbpath))

        conn = sqlite3.connect(dbpath)
        # Keep lastest entry in dict by last_visit_time asc order.
        sql = 'select title, url from urls order by last_visit_time asc'
        # May fetch many by many not fetch all,
        # but this should called only once, so not important now.
        try:
            chrome_histories = conn.execute(sql).fetchall()
        except sqlite3.OperationalError as e:
            if e.args[0] == 'database is locked':
                self.message_to_emacs.emit("The chrome history file is locked, please close your chrome app first.")
            else:
                self.message_to_emacs.emit("Failed to read chrome history entries: {}.".format(e))
            return

        histories = dict(chrome_histories)  # Drop duplications with same title.
        total = len(histories)
        for i, (title, url) in enumerate(histories.items(), 1):
            self._record_history(title, url)
            self.message_to_emacs.emit("Importing {} / {} ...".format(i, total))
        self.message_to_emacs.emit("{} chrome history entries imported.".format(total))

    @interactive
    def import_chrome_history(self):
        ''' Import history entries from chrome history db.'''
        self.send_input_message("Are you sure you want to import all history from chrome?", "import_chrome_history", "yes-or-no")

    def _clear_cookies(self):
        ''' Clear cookies.'''
        self.buffer_widget.cookie_storage.clear_cookies(self.buffer_widget.cookie_store)
        self.message_to_emacs.emit("Cleared all cookies.")

    @interactive
    def clear_cookies(self):
        ''' Clear cookies.'''
        self.send_input_message("Are you sure you want to clear all browsing cookies?", "clear_cookies", "yes-or-no")

class HistoryPage():
    def __init__(self, title, url, hit):
        self.title = title
        self.url = url
        self.hit = float(hit)


class PasswordDb(object):
    def __init__(self, dbpath):
        self._conn = sqlite3.connect(dbpath)
        self._conn.execute("""
        CREATE TABLE IF NOT EXISTS autofill
        (id INTEGER PRIMARY KEY AUTOINCREMENT, host TEXT,
         password TEXT, form_data TEXT)
        """)

    def add_entry(self, host, password, form_data):
        result = self._conn.execute("""
        SELECT id, host, password, form_data FROM autofill
        WHERE host=? AND form_data=? ORDER BY id
        """, (host, str(form_data)))
        if len(list(result))>0:
            self._conn.execute("""
            UPDATE autofill SET password=?
            WHERE host=? and form_data=?
            """, (password, host, str(form_data)))
        else:
            self._conn.execute("""
            INSERT INTO autofill (host, password, form_data)
            VALUES (?, ?, ?)
            """, (host, password, str(form_data)))
        self._conn.commit()

    def get_entries(self, host, id):
        return self._conn.execute("""
        SELECT id, host, password, form_data FROM autofill
        WHERE host=? and id>? ORDER BY id
        """, (host, id))
