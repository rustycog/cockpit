# -*- coding: utf-8 -*-

# This file is part of Cockpit.
#
# Copyright (C) 2013 Red Hat, Inc.
#
# Cockpit is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# Cockpit is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Cockpit; If not, see <http://www.gnu.org/licenses/>.

"""
Tools for writing Cockpit test cases.
"""

from time import sleep

import argparse
import errno
import subprocess
import os
import select
import shutil
import socket
import sys
import traceback
import random
import re
import json
import tempfile
import time
import unittest

import tap
import testvm

TEST_DIR = os.path.normpath(os.path.dirname(os.path.realpath(os.path.join(__file__, ".."))))
BOTS_DIR = os.path.normpath(os.path.join(TEST_DIR, "..", "bots"))

os.environ["PATH"] = "{0}:{1}:{2}".format(os.environ.get("PATH"), BOTS_DIR, TEST_DIR)

__all__ = (
    # Test definitions
    'test_main',
    'arg_parser',
    'Browser',
    'MachineCase',
    'skipImage',
    'Error',

    'sit',
    'wait',
    'opts',
    'TEST_DIR',
)

# Command line options
opts = argparse.Namespace()
opts.sit = False
opts.trace = False
opts.attachments = None
opts.revision = None
opts.address = None
opts.jobs = 1
opts.network = True

def attach(filename):
    if not opts.attachments:
        return
    dest = os.path.join(opts.attachments, os.path.basename(filename))
    if os.path.exists(filename) and not os.path.exists(dest):
        shutil.move(filename, dest)

class Browser:
    def __init__(self, address, label, port=9090):
        self.default_user = "admin"
        self.address = address
        self.label = label
        self.phantom = Phantom("en_US.utf8")
        self.port = port
        self.password = "foobar"

    def title(self):
        return self.phantom.eval('document.title')

    def open(self, href, cookie=None):
        """
        Load a page into the browser.

        Arguments:
          page: The path of the Cockpit page to load, such as "/dashboard".
          url: The full URL to load.

        Either PAGE or URL needs to be given.

        Raises:
          Error: When a timeout occurs waiting for the page to load.
        """
        if href.startswith("/"):
            href = "http://%s:%s%s" % (self.address, self.port, href)

        def tryopen(hard=False):
            try:
                self.phantom.kill()
                if cookie is not None:
                    self.phantom.cookies(cookie)
                self.phantom.open(href)
                return True
            except:
                if hard:
                    raise
                return False

        tries = 0
        while not tryopen(tries >= 20):
            print "Restarting browser..."
            sleep(0.1)
            tries = tries + 1

    def reload(self):
        self.switch_to_top()
        self.wait_js_cond("ph_select('iframe.container-frame').every(function (e) { return e.getAttribute('data-loaded'); })")
        self.phantom.reload()

    def expect_load(self):
        self.phantom.expect_load()

    def switch_to_frame(self, name):
        self.phantom.switch_frame(name)

    def switch_to_top(self):
        self.phantom.switch_top()

    def upload_file(self, selector, file):
        self.phantom.upload_file(selector, file)

    def eval_js(self, code):
        return self.phantom.eval(code)

    def call_js_func(self, func, *args):
        return self.phantom.eval("%s(%s)" % (func, ','.join(map(jsquote, args))))

    def cookie(self, name):
        cookies = self.phantom.cookies()
        for c in cookies:
            if c['name'] == name:
                return c['value']
        return None

    def go(self, hash, host="localhost"):
        # if not hash.startswith("/@"):
        #    hash = "/@" + host + hash
        self.call_js_func('ph_go', hash)

    def click(self, selector, force=False):
        self.call_js_func('ph_click', selector, force)

    def val(self, selector):
        return self.call_js_func('ph_val', selector)

    def set_val(self, selector, val):
        self.call_js_func('ph_set_val', selector, val)

    def text(self, selector):
        return self.call_js_func('ph_text', selector)

    def attr(self, selector, attr):
        return self.call_js_func('ph_attr', selector, attr)

    def set_attr(self, selector, attr, val):
        self.call_js_func('ph_set_attr', selector, attr, val and 'true' or 'false')

    def set_checked(self, selector, val):
        self.call_js_func('ph_set_checked', selector, val)

    def focus(self, selector):
        self.call_js_func('ph_focus', selector)

    def key_press(self, keys):
        return self.phantom.keys('keypress', keys)

    def wait_timeout(self, timeout):
        browser = self
        class WaitParamsRestorer():
            def __init__(self, timeout):
                self.timeout = timeout
            def __enter__(self):
                pass
            def __exit__(self, type, value, traceback):
                browser.phantom.timeout = self.timeout
        r = WaitParamsRestorer(self.phantom.timeout)
        self.phantom.timeout = max(timeout, self.phantom.timeout)
        return r

    def wait(self, predicate):
        self.arm_timeout()
        while True:
            val = predicate()
            if val:
                self.disarm_timeout()
                return val
            self.wait_checkpoint()

    def inject_js(self, code):
        self.phantom.do(code);

    def wait_js_cond(self, cond):
        return self.phantom.wait(cond)

    def wait_js_func(self, func, *args):
        return self.phantom.wait("%s(%s)" % (func, ','.join(map(jsquote, args))))

    def is_present(self, selector):
        return self.call_js_func('ph_is_present', selector)

    def wait_present(self, selector):
        return self.wait_js_func('ph_is_present', selector)

    def wait_not_present(self, selector):
        return self.wait_js_func('!ph_is_present', selector)

    def is_visible(self, selector):
        return self.call_js_func('ph_is_visible', selector)

    def wait_visible(self, selector):
        return self.wait_js_func('ph_is_visible', selector)

    def wait_val(self, selector, val):
        return self.wait_js_func('ph_has_val', selector, val)

    def wait_not_val(self, selector, val):
        return self.wait_js_func('!ph_has_val', selector, val)

    def wait_attr(self, selector, attr, val):
        return self.wait_js_func('ph_has_attr', selector, attr, val)

    def wait_not_attr(self, selector, attr, val):
        return self.wait_js_func('!ph_has_attr', selector, attr, val)

    def wait_not_visible(self, selector):
        return self.wait_js_func('!ph_is_visible', selector)

    def wait_in_text(self, selector, text):
        return self.wait_js_func('ph_in_text', selector, text)

    def wait_not_in_text(self, selector, text):
        return self.wait_js_func('!ph_in_text', selector, text)

    def wait_text(self, selector, text):
        return self.wait_js_func('ph_text_is', selector, text)

    def wait_text_not(self, selector, text):
        return self.wait_js_func('!ph_text_is', selector, text)

    def wait_popup(self, id):
        """Wait for a popup to open.

        Arguments:
          id: The 'id' attribute of the popup.
        """
        self.wait_visible('#' + id);

    def wait_popdown(self, id):
        """Wait for a popup to close.

        Arguments:
            id: The 'id' attribute of the popup.
        """
        self.wait_not_visible('#' + id)

    def arm_timeout(self):
        return self.phantom.arm_timeout(self.phantom.timeout * 1000)

    def disarm_timeout(self):
        return self.phantom.disarm_timeout()

    def wait_checkpoint(self):
        return self.phantom.wait_checkpoint()

    def dialog_complete(self, sel, button=".btn-primary", result="hide"):
        self.click(sel + " " + button)
        self.wait_not_present(sel + " .dialog-wait-ct")

        dialog_visible = self.call_js_func('ph_is_visible', sel)
        if result == "hide":
            if dialog_visible:
                raise AssertionError(sel + " dialog did not complete and close")
        elif result == "fail":
            if not dialog_visible:
                raise AssertionError(sel + " dialog is closed no failures present")
            dialog_error = self.call_js_func('ph_is_present', sel + " .dialog-error")
            if not dialog_error:
                raise AssertionError(sel + " dialog has no errors")
        else:
            raise Error("invalid dialog result argument: " + result)

    def dialog_cancel(self, sel, button=".btn[data-dismiss='modal']"):
        self.click(sel + " " + button)
        self.wait_not_visible(sel)

    def enter_page(self, path, host=None, reconnect=True):
        """Wait for a page to become current.

        Arguments:

            id: The identifier the page.  This is a string starting with "/"
        """
        assert path.startswith("/")
        if host:
            frame = host + path
        else:
            frame = "localhost" + path
        frame = "cockpit1:" + frame

        self.switch_to_top()

        while True:
            try:
                self.wait_present("iframe.container-frame[name='%s'][data-loaded]" % frame)
                self.wait_not_visible(".curtains-ct")
                self.wait_visible("iframe.container-frame[name='%s']" % frame)
                break
            except Error, ex:
                if reconnect and ex.msg.startswith('timeout'):
                    reconnect = False
                    if self.is_present("#machine-reconnect"):
                        self.click("#machine-reconnect", True)
                        self.wait_not_visible(".curtains-ct")
                        continue
                exc_info = sys.exc_info()
                raise exc_info[0], exc_info[1], exc_info[2]

        self.switch_to_frame(frame)
        self.wait_present("body")
        self.wait_visible("body")

    def leave_page(self):
        self.switch_to_top()

    def wait_action_btn(self, sel, entry):
        self.wait_text(sel + ' button:first-child', entry);

    def click_action_btn(self, sel, entry=None):
        # We don't need to open the menu, it's enough to simulate a
        # click on the invisible button.
        if entry:
            self.click(sel + ' a:contains("%s")' % entry, True);
        else:
            self.click(sel + ' button:first-child');

    def login_and_go(self, path=None, user=None, host=None, authorized=True):
        if user is None:
            user = self.default_user
        href = path
        if not href:
            href = "/"
        if host:
            href = "/@" + host + href
        self.open(href)
        self.wait_visible("#login")
        self.set_val('#login-user-input', user)
        self.set_val('#login-password-input', self.password)
        self.set_checked('#authorized-input', authorized)

        self.click('#login-button')
        self.expect_load()
        self.wait_present('#content')
        self.wait_visible('#content')
        if path:
            self.enter_page(path.split("#")[0], host=host)

    def logout(self):
        self.switch_to_top()
        self.wait_present("#navbar-dropdown")
        self.wait_visible("#navbar-dropdown")
        self.click("#navbar-dropdown")
        self.click('#go-logout')
        self.expect_load()

    def relogin(self, path=None, user=None, authorized=None):
        if user is None:
            user = self.default_user
        self.logout()
        self.wait_visible("#login")
        self.set_val("#login-user-input", user)
        self.set_val("#login-password-input", self.password)
        if authorized is not None:
            self.set_checked('#authorized-input', authorized)
        self.click('#login-button')
        self.expect_load()
        self.wait_present('#content')
        self.wait_visible('#content')
        if path:
            if path.startswith("/@"):
                host = path[2:].split("/")[0]
            else:
                host = None
            self.enter_page(path.split("#")[0], host=host)

    def snapshot(self, title, label=None):
        """Take a snapshot of the current screen and save it as a PNG.

        Arguments:
            title: Used for the filename.
        """
        if self.phantom and self.phantom.valid:
            filename = "{0}-{1}.png".format(label or self.label, title)
            self.phantom.show(filename)
            attach(filename)

    def kill(self):
        self.phantom.kill()


class MachineCase(unittest.TestCase):
    runner = None
    machine = None
    machine_class = None
    browser = None
    machines = { }

    # additional_machines is a dictionary of dictionaries, one for each additional machine to be created, e.g.:
    # additional_machines = { 'openshift' : { machine: { 'image': 'openshift' }, 'start': { 'memory_mb': 1024 } } }
    # These will be instantiated during setUp
    additional_machines = { }

    def label(self):
        (unused, sep, label) = self.id().partition(".")
        return label.replace(".", "-")

    def new_machine(self, machine_key, image=testvm.DEFAULT_IMAGE):
        import testvm
        machine_class = self.machine_class
        if opts.address:
            if machine_class:
                raise unittest.SkipTest("Cannot run this test when specific machine address is specified")
            if len(self.machines) != 0:
                raise unittest.SkipTest("Cannot run multiple machines if a specific machine address is specified")
            machine = testvm.Machine(address=opts.address, image=image, verbose=opts.trace, label=self.label())
            self.addCleanup(lambda: machine.disconnect())
        else:
            if not machine_class:
                machine_class = testvm.VirtMachine
            machine = machine_class(verbose=opts.trace, image=image, label=self.label(), fetch=opts.network)
            self.addCleanup(lambda: machine.kill())

        self.machines[machine_key] = machine
        return machine

    def new_browser(self, address=None, port=9090):
        browser = Browser(address = address or self.machine.address, label=self.label(), port=port)
        self.addCleanup(lambda: browser.kill())
        return browser

    def run(self, result=None):
        orig_result = result

        # We need a result to intercept, so create one here
        if result is None:
            result = self.defaultTestResult()
            startTestRun = getattr(result, 'startTestRun', None)
            if startTestRun is not None:
                startTestRun()

        self.currentResult = result

        # Here's the loop to actually retry running the test. It's an awkward
        # place for this loop, since it only applies to MachineCase based
        # TestCases. However for the time being there is no better place for it.
        #
        # Policy actually dictates retries.  The number here is an upper bound to
        # prevent endless retries if Policy.check_retry is buggy.
        max_retry_hard_limit = 10
        for retry in range(0, max_retry_hard_limit):
            try:
                super(MachineCase, self).run(result)
            except RetryError, ex:
                assert retry < max_retry_hard_limit
                sys.stderr.write("{0}\n".format(ex))
                sleep(retry * 10)
            else:
                break

        self.currentResult = None

        # Standard book keeping that we have to do
        if orig_result is None:
            stopTestRun = getattr(result, 'stopTestRun', None)
            if stopTestRun is not None:
                stopTestRun()

    def setUp(self, macaddr=None, memory_mb=None, cpus=None):
        self.machines = { }
        self.machine = self.new_machine(machine_key='0')

        self.machine.start(macaddr=macaddr, memory_mb=memory_mb, cpus=cpus, wait_for_ip=False)

        # first create all additional machines, wait for them later
        for machine_name, machine_options in self.additional_machines.iteritems():
            if not 'machine' in machine_options:
                machine_options['machine'] = { }
            if not 'start' in machine_options:
                machine_options['start'] = { }
            machine = self.new_machine(machine_key=machine_name, **machine_options['machine'])
            options = machine_options['start']
            options['wait_for_ip'] = False
            machine.start(**options)

        # now wait for the other machines to be up
        for machine_name, machine in self.machines.iteritems():
            if machine_name != '0' or not opts.address:
                if opts.trace:
                    print "starting machine %s (%s)" % (machine.image, machine.address)
                machine.wait_boot()

        self.browser = self.new_browser()
        self.tmpdir = tempfile.mkdtemp()

        def sitter():
            if opts.sit and not self.currentResult.wasSuccessful():
                self.currentResult.printErrors()
                if self.machine:
                    print >> sys.stderr, "ADDRESS: %s" % self.machine.address
                sit()
        self.addCleanup(sitter)

        def intercept():
            if not self.currentResult.wasSuccessful():
                self.snapshot("FAIL")
                self.copy_journal("FAIL")
                self.copy_cores("FAIL")
        self.addCleanup(intercept)

    def tearDown(self):
        if self.currentResult.wasSuccessful() and len(self.currentResult.skipped) == 0 and self.machine.address:
            self.check_journal_messages()
        shutil.rmtree(self.tmpdir)

    def login_and_go(self, path=None, user=None, host=None, authorized=True):
        self.machine.start_cockpit(host)
        self.browser.login_and_go(path, user=user, host=host, authorized=authorized)

    allowed_messages = [
        # This is a failed login, which happens every time
        "Returning error-response 401 with reason `Sorry'",

        # Reauth stuff
        '.*Reauthorizing unix-user:.*',
        '.*user .* was reauthorized.*',

        # Happens when the user logs out during reauthorization
        "Error executing command as another user: Not authorized",
        "This incident has been reported.",

        # Reboots are ok
        "-- Reboot --",

        # Sometimes D-Bus goes away before us during shutdown
        "Lost the name com.redhat.Cockpit on the session message bus",
        "GLib-GIO:ERROR:gdbusobjectmanagerserver\\.c:.*:g_dbus_object_manager_server_emit_interfaces_.*: assertion failed \\(error == NULL\\): The connection is closed \\(g-io-error-quark, 18\\)",
        "Error sending message: The connection is closed",

        # Will go away with glib 2.43.2
        ".*: couldn't write web output: Error sending data: Connection reset by peer",

        # pam_lastlog outdated complaints
        ".*/var/log/lastlog: No such file or directory",

        # ssh messages may be dropped when closing
        '10.*: dropping message while waiting for child to exit',

        # SELinux messages to ignore
        "(audit: )?type=1403 audit.*",
        "(audit: )?type=1404 audit.*",

        # https://bugzilla.redhat.com/show_bug.cgi?id=1298157
        "(audit: )?type=1400 .*granted.*comm=\"tuned\".*",

        # https://bugzilla.redhat.com/show_bug.cgi?id=1298171
        "(audit: )?type=1400 .*denied.*comm=\"iptables\".*name=\"xtables.lock\".*",

        # https://bugzilla.redhat.com/show_bug.cgi?id=1386624
        ".*type=1400 .*denied  { name_bind } for.*dhclient.*",

        # https://bugzilla.redhat.com/show_bug.cgi?id=1419263
        ".*type=1400 .*denied  { write } for.*firewalld.*__pycache__.*",

        # https://bugzilla.redhat.com/show_bug.cgi?id=1242656
        "(audit: )?type=1400 .*denied.*comm=\"cockpit-ws\".*name=\"unix\".*dev=\"proc\".*",
        "(audit: )?type=1400 .*denied.*comm=\"ssh-transport-c\".*name=\"unix\".*dev=\"proc\".*",
        "(audit: )?type=1400 .*denied.*comm=\"cockpit-ssh\".*name=\"unix\".*dev=\"proc\".*",

        # https://bugzilla.redhat.com/show_bug.cgi?id=1374820
        "(audit: )?type=1400 .*denied.*comm=\"systemd\" path=\"/run/systemd/inaccessible/blk\".*",

        # SELinux fighting with systemd: https://bugzilla.redhat.com/show_bug.cgi?id=1253319
        "(audit: )?type=1400 audit.*systemd-journal.*path=2F6D656D66643A73642D73797374656D642D636F726564756D202864656C6574656429",

        # SELinux and plymouth: https://bugzilla.redhat.com/show_bug.cgi?id=1427884
        "(audit: )?type=1400 audit.*connectto.*plymouth.*unix_stream_socket.*",

        # SELinux and nfs-utils fighting: https://bugzilla.redhat.com/show_bug.cgi?id=1447854
        ".*type=1400 .*denied  { execute } for.*sm-notify.*init_t.*",

        # apparmor loading
        "(audit: )?type=1400.*apparmor=\"STATUS\".*",

        # apparmor noise
        "(audit: )?type=1400.*apparmor=\"ALLOWED\".*",

        # Messages from systemd libraries when they are in debug mode
        'Successfully loaded SELinux database in.*',
        'calling: info',
        'Sent message type=method_call sender=.*',
        'Got message type=method_return sender=.*',

        # HACK: https://github.com/systemd/systemd/pull/1758
        'Error was encountered while opening journal files:.*',
        'Failed to get data: Cannot assign requested address',

        # Various operating systems see this from time to time
        "Journal file.*truncated, ignoring file.",
    ]

    def allow_journal_messages(self, *patterns):
        """Don't fail if the journal containes a entry matching the given regexp"""
        for p in patterns:
            self.allowed_messages.append(p)

    def allow_hostkey_messages(self):
        self.allow_journal_messages('.*: .* host key for server is not known: .*',
                                    '.*: refusing to connect to unknown host: .*',
                                    '.*: failed to retrieve resource: hostkey-unknown')

    def allow_restart_journal_messages(self):
        self.allow_journal_messages(".*Connection reset by peer.*",
                                    ".*Broken pipe.*",
                                    "g_dbus_connection_real_closed: Remote peer vanished with error: Underlying GIOStream returned 0 bytes on an async read \\(g-io-error-quark, 0\\). Exiting.",
                                    "connection unexpectedly closed by peer",
                                    # HACK: https://bugzilla.redhat.com/show_bug.cgi?id=1141137
                                    "localhost: bridge program failed: Child process killed by signal 9",
                                    "request timed out, closing",
                                    "PolicyKit daemon disconnected from the bus.",
                                    ".*couldn't create polkit session subject: No session for pid.*",
                                    "We are no longer a registered authentication agent.",
                                    ".*: failed to retrieve resource: terminated",
                                    # HACK: https://bugzilla.redhat.com/show_bug.cgi?id=1253319
                                    'audit:.*denied.*2F6D656D66643A73642D73797374656D642D636F726564756D202864656C.*',
                                    'audit:.*denied.*comm="systemd-user-se".*nologin.*',

                                    'localhost: dropping message while waiting for child to exit',
                                    '.*: GDBus.Error:org.freedesktop.PolicyKit1.Error.Failed: .*',
                                    '.*g_dbus_connection_call_finish_internal.*G_IS_DBUS_CONNECTION.*',
                                    )

    def allow_authorize_journal_messages(self):
        self.allow_journal_messages("cannot reauthorize identity.*:.*unix-user:admin.*",
                                    ".*: pam_authenticate failed: Authentication failure",
                                    ".*is not in the sudoers file.  This incident will be reported.",
                                    ".*: a password is required",
                                    "user user was reauthorized",
                                    "sudo: unable to resolve host .*",
                                    ".*: sorry, you must have a tty to run sudo",
                                    ".*/pkexec: bridge exited",
                                    "We trust you have received the usual lecture from the local System",
                                    "Administrator. It usually boils down to these three things:",
                                    "#1\) Respect the privacy of others.",
                                    "#2\) Think before you type.",
                                    "#3\) With great power comes great responsibility.",
                                    ".*Sorry, try again.",
                                    ".*incorrect password attempt.*")

    def check_journal_messages(self, machine=None):
        """Check for unexpected journal entries."""
        machine = machine or self.machine
        syslog_ids = [ "cockpit-ws", "cockpit-bridge" ]
        messages = machine.journal_messages(syslog_ids, 5)
        messages += machine.audit_messages("14") # 14xx is selinux
        all_found = True
        first = None
        for m in messages:
            # remove leading/trailing whitespace
            m = m.strip()
            found = False
            for p in self.allowed_messages:
                match = re.match(p, m)
                if match and match.group(0) == m:
                    found = True
                    break
            if not found:
                print "Unexpected journal message '%s'" % m
                all_found = False
                if not first:
                    first = m
        if not all_found:
            self.copy_journal("FAIL")
            self.copy_cores("FAIL")
            raise Error(first)

    def snapshot(self, title, label=None):
        """Take a snapshot of the current screen and save it as a PNG.

        Arguments:
            title: Used for the filename.
        """
        if self.browser is not None:
            self.browser.snapshot(title, label)

    def copy_journal(self, title, label=None):
        for name, m in self.machines.iteritems():
            if m.address:
                log = "%s-%s-%s.log" % (label or self.label(), m.address, title)
                with open(log, "w") as fp:
                    m.execute("journalctl", stdout=fp)
                    print "Journal extracted to %s" % (log)
                    attach(log)

    def copy_cores(self, title, label=None):
        for name, m in self.machines.iteritems():
            if m.address:
                dest = "%s-%s-%s.core" % (label or self.label(), m.address, title)
                m.download_dir("/var/lib/systemd/coredump", dest)
                try:
                    os.rmdir(dest)
                except OSError, ex:
                    if ex.errno == errno.ENOTEMPTY:
                        print "Core dumps downloaded to %s" % (dest)
                        attach(dest)

some_failed = False

def jsquote(str):
    return json.dumps(str)

# See phantom-driver for the methods that are defined
class Phantom:
    def __init__(self, lang=None):
        self.lang = lang
        self.timeout = 60
        self.valid = False
        self._driver = None

    def __getattr__(self, name):
        if not name.startswith("_"):
            return lambda *args: self._invoke(name, *args)
        raise AttributeError

    def _invoke(self, name, *args):
        if not self._driver:
            self.start()
        if opts.trace:
            print "-> {0}({1})".format(name, repr(args)[1:-2])
        line = json.dumps({
            "cmd": name,
            "args": args,
            "timeout": self.timeout * 1000
        }).replace("\n", " ") + "\n"
        self._driver.stdin.write(line)
        line = self._driver.stdout.readline()
        if not line:
            self.kill()
            raise Error("PhantomJS or driver broken")
        try:
            res = json.loads(line)
        except:
            print line.strip()
            raise
        if 'error' in res:
            if opts.trace:
                print "<- raise", res['error']
            raise Error(res['error'])
        if 'result' in res:
            if opts.trace:
                print "<-", repr(res['result'])
            return res['result']
        raise Error("unexpected: " + line.strip())

    def start(self):
        environ = os.environ.copy()
        if self.lang:
            environ["LC_ALL"] = self.lang
        path = os.path.dirname(__file__)
        command = [
            "%s/phantom-command" % path,
            "%s/phantom-driver.js" % path,
            "%s/sizzle.js" % path,
            "%s/phantom-lib.js" % path
        ]
        self.valid = True
        self._driver = subprocess.Popen(command, env=environ,
                                        stdout=subprocess.PIPE,
                                        stdin=subprocess.PIPE, close_fds=True)

    def kill(self):
        self.valid = False
        if self._driver:
            self._driver.terminate()
            self._driver.wait()
            self._driver = None

def skipImage(reason, *args):
    if testvm.DEFAULT_IMAGE in args:
        return unittest.skip("{0}: {1}".format(testvm.DEFAULT_IMAGE, reason))
    return lambda func: func

class Policy(object):
    def __init__(self, retryable=True):
        self.retryable = retryable

    def normalize_traceback(self, trace):
        # All file paths converted to basename
        return re.sub(r'File "[^"]*/([^/"]+)"', 'File "\\1"', trace.strip())

    def check_issue(self, trace):
        cmd = [ "image-naughty", testvm.DEFAULT_IMAGE ]

        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            (output, error) = proc.communicate(str(trace))
        except OSError, ex:
            if getattr(ex, 'errno', 0) != errno.ENOENT:
                sys.stderr.write("Couldn't check known issue: {0}\n".format(str(ex)))
            output = ""

        return output

    def check_retry(self, trace, tries):
        # Never try more than five times
        if not self.retryable or tries >= 5:
            return False

        # We check for persistent but test harness or framework specific
        # failures that otherwise cause flakiness and false positives.
        #
        # The things we check here must:
        #  * have no impact on users of Cockpit in the real world
        #  * be things we tried to resolve in other ways. This is a last resort
        #
        trace = self.normalize_traceback(trace)

        # HACK: An issue in phantomjs and QtWebkit
        # http://stackoverflow.com/questions/35337304/qnetworkreply-network-access-is-disabled-in-qwebview
        # https://github.com/owncloud/client/issues/3600
        # https://github.com/ariya/phantomjs/issues/14789
        if "PhantomJS or driver broken" in trace:
            return True

        # HACK: Interacting with sshd during boot is not always predictable
        # We're using an implementation detail of the server as our "way in" for testing.
        # This often has to do with sshd being restarted for some reason
        if "SSH master process exited with code: 255" in trace:
            return True

        # HACK: Intermittently the new libvirt machine won't get an IP address
        # or SSH will completely fail to start. We've tried various approaches
        # to minimize this, but it happens every 100,000 tests or so
        if "Failure: Unable to reach machine " in trace:
            return True

        # HACK: For when the verify machine runs out of available processes
        # We should retry this test process
        if "self.pid = os.fork()\nOSError: [Errno 11] Resource temporarily unavailable" in trace:
            return True

        return False

class TestResult(tap.TapResult):
    def __init__(self, stream, descriptions, verbosity):
        self.policy = None
        super(TestResult, self).__init__(verbosity)

    def maybeIgnore(self, test, err):
        string = self._exc_info_to_string(err, test)
        if self.policy:
            issue = self.policy.check_issue(string)
            if issue:
                self.addSkip(test, "Known issue #{0}".format(issue))
                return True
            tries = getattr(test, "retryCount", 1)
            if self.policy.check_retry(string, tries):
                self.offset -= 1
                setattr(test, "retryCount", tries + 1)
                test.doCleanups()
                raise RetryError("Retrying due to failure of test harness or framework")
        return False

    def addError(self, test, err):
        if not self.maybeIgnore(test, err):
            super(TestResult, self).addError(test, err)

    def addFailure(self, test, err):
        if not self.maybeIgnore(test, err):
            super(TestResult, self).addError(test, err)

    def startTest(self, test):
        sys.stdout.write("# {0}\n# {1}\n#\n".format('-' * 70, str(test)))
        sys.stdout.flush()
        super(TestResult, self).startTest(test)

    def stopTest(self, test):
        sys.stdout.write("\n")
        sys.stdout.flush()
        super(TestResult, self).stopTest(test)

class OutputBuffer(object):
    def __init__(self):
        self.poll = select.poll()
        self.buffers = { }
        self.fds = { }

    def drain(self):
        while self.fds:
            for p in self.poll.poll(1000):
                data = os.read(p[0], 1024)
                if data == "":
                    self.poll.unregister(p[0])
                else:
                    self.buffers[p[0]] += data
            else:
                break

    def push(self, pid, fd):
        self.poll.register(fd, select.POLLIN)
        self.fds[pid] = fd
        self.buffers[fd] = ""

    def pop(self, pid):
        fd = self.fds.pop(pid)
        buffer = self.buffers.pop(fd)
        try:
            self.poll.unregister(fd)
        except KeyError:
            pass
        while True:
            data = os.read(fd, 1024)
            if data == "":
                break
            buffer += data
        os.close(fd)
        return buffer

class TapRunner(object):
    resultclass = TestResult

    def __init__(self, verbosity=1, jobs=1, thorough=False):
        self.stream = unittest.runner._WritelnDecorator(sys.stderr)
        self.verbosity = verbosity
        self.thorough = thorough
        self.jobs = jobs

    def runOne(self, test, offset):
        result = TestResult(self.stream, False, self.verbosity)
        result.offset = offset
        if not self.thorough:
            result.policy = Policy()
        try:
            test(result)
        except KeyboardInterrupt:
            return False
        except:
            sys.stderr.write("Unexpected exception while running {0}\n".format(test))
            traceback.print_exc(file=sys.stderr)
            return False
        else:
            result.printErrors()
            return result.wasSuccessful()

    def run(self, testable):
        tap.TapResult.plan(testable)
        count = testable.countTestCases()

        # For statistics
        start = time.time()

        pids = set()
        options = 0
        buffer = None
        if self.jobs > 1:
            buffer = OutputBuffer()
            options = os.WNOHANG
        offset = 0
        failures = { "count": 0 }

        def join_some(n):
            while len(pids) > n:
                if buffer:
                    buffer.drain()
                try:
                    (pid, code) = os.waitpid(-1, options)
                except KeyboardInterrupt:
                    sys.exit(255)
                if pid:
                    if buffer:
                        sys.stdout.write(buffer.pop(pid))
                    pids.remove(pid)
                if code & 0xff:
                    failed = 1
                else:
                    failed = (code >> 8) & 0xff
                failures["count"] += failed

        for test in testable:
            join_some(self.jobs - 1)

            # Fork off a child process for each test
            if buffer:
                (rfd, wfd) = os.pipe()

            sys.stdout.flush()
            sys.stderr.flush()
            pid = os.fork()
            if not pid:
                if buffer:
                    os.dup2(wfd, 1)
                    os.dup2(wfd, 2)
                random.seed()
                if self.runOne(test, offset):
                    sys.exit(0)
                else:
                    sys.exit(1)

            # The parent process
            pids.add(pid)
            if buffer:
                os.close(wfd)
                buffer.push(pid, rfd)
            offset += test.countTestCases()

        # Wait for the remaining subprocesses
        join_some(0)

        # Report on the results
        duration = int(time.time() - start)
        hostname = socket.gethostname().split(".")[0]
        details = "[{0}s on {1}]".format(duration, hostname)
        count = failures["count"]
        if count:
            sys.stdout.write("# {0} TESTS FAILED {1}\n".format(count, details))
        else:
            sys.stdout.write("# TESTS PASSED {0}\n".format(details))
        return count

def arg_parser():
    parser = argparse.ArgumentParser(description='Run Cockpit test(s)')
    parser.add_argument('-j', '--jobs', dest="jobs", type=int,
                        default=os.environ.get("TEST_JOBS", 1), help="Number of concurrent jobs")
    parser.add_argument('-v', '--verbose', dest="verbosity", action='store_const',
                        const=2, help='Verbose output')
    parser.add_argument('-t', "--trace", dest='trace', action='store_true',
                        help='Trace machine boot and commands')
    parser.add_argument('-q', '--quiet', dest='verbosity', action='store_const',
                        const=0, help='Quiet output')
    parser.add_argument('--thorough', dest='thorough', action='store_true',
                        help='Thorough mode, no skipping known issues')
    parser.add_argument('-s', "--sit", dest='sit', action='store_true',
                        help="Sit and wait after test failure")
    parser.add_argument('--nonet', dest="network", action="store_false",
                        help="Don't go online to download images or data")
    parser.add_argument('tests', nargs='*')

    parser.set_defaults(verbosity=1, network=True)
    return parser

def test_main(options=None, suite=None, attachments=None, **kwargs):
    """
    Run all test cases, as indicated by arguments.

    If no arguments are given on the command line, all test cases are
    executed.  Otherwise only the given test cases are run.
    """

    global opts

    # Turn off python stdout buffering
    sys.stdout.flush()
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

    standalone = options is None
    parser = arg_parser()
    parser.add_argument('--machine', dest="address", action="store",
                        default=None, help="Run this test against an already running machine")

    if standalone:
        options = parser.parse_args()

    # Have to copy into opts due to python globals across modules
    for (key, value) in vars(options).items():
        setattr(opts, key, value);

    if opts.sit and opts.jobs > 1:
        parser.error("the -s or --sit argument not avalible with multiple jobs")

    opts.address = getattr(opts, "address", None)
    opts.attachments = os.environ.get("TEST_ATTACHMENTS", attachments)
    if opts.attachments and not os.path.exists(opts.attachments):
        os.makedirs(opts.attachments)

    import __main__
    if len(opts.tests) > 0:
        if suite:
            parser.error("tests may not be specified when running a predefined test suite")
        suite = unittest.TestLoader().loadTestsFromNames(opts.tests, module=__main__)
    elif not suite:
        suite = unittest.TestLoader().loadTestsFromModule(__main__)

    runner = TapRunner(verbosity=opts.verbosity, jobs=opts.jobs, thorough=opts.thorough)
    ret = runner.run(suite)
    if not standalone:
        return ret
    sys.exit(ret)

class Error(Exception):
    def __init__(self, msg):
        self.msg = msg
    def __str__(self):
        return self.msg

class RetryError(Error):
    pass

def wait(func, msg=None, delay=1, tries=60):
    """
    Wait for FUNC to return something truthy, and return that.

    FUNC is called repeatedly until it returns a true value or until a
    timeout occurs.  In the latter case, a exception is raised that
    describes the situation.  The exception is either the last one
    thrown by FUNC, or includes MSG, or a default message.

    Arguments:
      func: The function to call.
      msg: A error message to use when the timeout occurs.  Defaults
        to a generic message.
      delay: How long to wait between calls to FUNC, in seconds.
        Defaults to 1.
      tries: How often to call FUNC.  Defaults to 60.

    Raises:
      Error: When a timeout occurs.
    """

    t = 0
    while t < tries:
        try:
            val = func()
            if val:
                return val
        except:
            if t == tries-1:
                raise
            else:
                pass
        t = t + 1
        sleep(delay)
    raise Error(msg or "Condition did not become true.")

def sit():
    """
    Wait until the user confirms to continue.

    The current test case is suspended so that the user can inspect
    the browser.
    """
    raw_input ("Press RET to continue... ")
