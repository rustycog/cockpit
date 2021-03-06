#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This file is part of Cockpit.
#
# Copyright (C) 2015 Red Hat, Inc.
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

# Shared GitHub code. When run as a script, we print out info about
# our GitHub interacition.

import errno
import httplib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urlparse

import cache

__all__ = (
    'GitHub',
    'Checklist',
    'whitelist',
    'TESTING',
    'NO_TESTING',
    'NOT_TESTED',
    'ISSUE_TITLE_IMAGE_REFRESH'
)

TESTING = "Testing in progress"
NOT_TESTED = "Not yet tested"
NO_TESTING = "Manual testing required"

OUR_CONTEXTS = [
    "verify/",
    "avocado/",
    "container/",
    "selenium/",
    "koji/",
]

ISSUE_TITLE_IMAGE_REFRESH = "Image refresh for {0}"

BOTS = os.path.join(os.path.dirname(__file__), "..")
TOKEN = "~/.config/github-token"

# the user name is accepted if it's found in either list
WHITELIST = os.path.join(BOTS, "github", "whitelist")
WHITELIST_LOCAL = "~/.config/github-whitelist"

def determine_github_base():
    # pick a base
    try:
        # see where we get master from, e.g. origin
        get_remote_command = ["git", "config", "--local", "--get", "branch.master.remote"]
        remote = subprocess.Popen(get_remote_command, stdout=subprocess.PIPE, cwd=BOTS).communicate()[0].strip()
        # see if we have a git checkout - it can be in https or ssh format
        formats = [
            re.compile("""https:\/\/github\.com\/(.*)\.git"""),
            re.compile("""git@github.com:(.*)\.git""")
            ]
        remote_output = subprocess.Popen(
                ["git", "ls-remote", "--get-url", remote],
                stdout=subprocess.PIPE, cwd=BOTS
            ).communicate()[0].strip()
        for f in formats:
            m = f.match(remote_output)
            if m:
                return list(m.groups())[0]
    except subprocess.CalledProcessError:
        sys.stderr.write("Unable to get git repo information, using defaults\n")

    # if we still don't have something, default to cockpit-project/cockpit
    return "cockpit-project/cockpit"

# github base to use
GITHUB_BASE = "https://api.github.com/repos/{0}/".format(os.environ.get("GITHUB_BASE", determine_github_base()))

def known_context(context):
    for prefix in OUR_CONTEXTS:
        if context.startswith(prefix):
            return True
    return False

def whitelist(filename=WHITELIST):
    # Try to load the whitelists
    whitelist = []
    try:
        with open(filename, "r") as wh:
            whitelist += [x.strip() for x in wh.read().split("\n") if x.strip()]
    except IOError as exc:
        if exc.errno != errno.ENOENT:
            raise

    # The local file may or may not exist
    try:
        path = os.path.expanduser(WHITELIST_LOCAL)
        wh = open(path, "r")
        whitelist += [x.strip() for x in wh.read().split("\n") if x.strip()]
    except IOError as exc:
        if exc.errno != errno.ENOENT:
            raise

    # Remove duplicate entries
    return set(whitelist)

class Logger(object):
    def __init__(self, directory):
        hostname = socket.gethostname().split(".")[0]
        month = time.strftime("%Y%m")
        self.path = os.path.join(directory, "{0}-{1}.log".format(hostname, month))

        if not os.path.exists(directory):
            os.makedirs(directory)

    # Yes, we open the file each time
    def write(self, value):
        with open(self.path, 'a') as f:
            f.write(value)

class GitHub(object):
    def __init__(self, base=GITHUB_BASE, cacher=None):
        self.url = urlparse.urlparse(base)
        self.conn = None
        self.token = None
        self.debug = False
        try:
            gt = open(os.path.expanduser(TOKEN), "r")
            self.token = gt.read().strip()
            gt.close()
        except IOError as exc:
            if exc.errno == errno.ENOENT:
                pass
            else:
                raise
        self.available = self.token and True or False

        # The cache directory is $TEST_DATA/github ~/.cache/github
        if not cacher:
            data = os.environ.get("TEST_DATA",  os.path.expanduser("~/.cache"))
            cacher = cache.Cache(os.path.join(data, "github"))
        self.cache = cacher

        # Create a log for debugging our GitHub access
        self.log = Logger(self.cache.directory)
        self.log.write("")

    def qualify(self, resource):
        return urlparse.urljoin(self.url.path, resource)

    def request(self, method, resource, data="", headers=None):
        if headers is None:
            headers = { }
        headers["User-Agent"] = "Cockpit Tests"
        if self.token:
            headers["Authorization"] = "token " + self.token
        connected = False
        while not connected:
            if not self.conn:
                if self.url.scheme == 'http':
                    self.conn = httplib.HTTPConnection(self.url.netloc)
                else:
                    self.conn = httplib.HTTPSConnection(self.url.netloc, strict=True)
                connected = True
            self.conn.set_debuglevel(self.debug and 1 or 0)
            try:
                self.conn.request(method, self.qualify(resource), data, headers)
                response = self.conn.getresponse()
                break
            # This happens when GitHub disconnects a keep-alive connection
            except httplib.BadStatusLine:
                if connected:
                    raise
                self.conn = None
        heads = { }
        for (header, value) in response.getheaders():
            heads[header.lower()] = value
        self.log.write('{0} - - [{1}] "{2} {3} HTTP/1.1" {4} -\n'.format(
            self.url.netloc,
            time.asctime(),
            method,
            resource,
            response.status
        ))
        return {
            "status": response.status,
            "reason": response.reason,
            "headers": heads,
            "data": response.read()
        }

    def get(self, resource):
        headers = { }
        qualified = self.qualify(resource)
        cached = self.cache.read(qualified)
        if cached:
            if self.cache.current(qualified):
                return json.loads(cached['data'] or "null")
            etag = cached['headers'].get("etag", None)
            modified = cached['headers'].get("last-modified", None)
            if etag:
                headers['If-None-Match'] = etag
            elif modified:
                headers['If-Modified-Since'] = modified
        response = self.request("GET", resource, "", headers)
        if response['status'] == 404:
            return None
        elif cached and response['status'] == 304: # Not modified
            self.cache.write(qualified, cached)
            return json.loads(cached['data'] or "null")
        elif response['status'] < 200 or response['status'] >= 300:
            sys.stderr.write("{0}\n{1}\n".format(resource, response['data']))
            raise Exception("GitHub API problem: {0}".format(response['reason'] or response['status']))
        else:
            self.cache.write(qualified, response)
            return json.loads(response['data'] or "null")

    def post(self, resource, data, accept=[]):
        response = self.request("POST", resource, json.dumps(data), { "Content-Type": "application/json" })
        status = response['status']
        if (status < 200 or status >= 300) and status not in accept:
            sys.stderr.write("{0}\n{1}\n".format(resource, response['data']))
            raise Exception("GitHub API problem: {0}".format(response['reason'] or status))
        self.cache.mark()
        return json.loads(response['data'])

    def patch(self, resource, data, accept=[]):
        response = self.request("PATCH", resource, json.dumps(data), { "Content-Type": "application/json" })
        status = response['status']
        if (status < 200 or status >= 300) and status not in accept:
            sys.stderr.write("{0}\n{1}\n".format(resource, response['data']))
            raise Exception("GitHub API problem: {0}".format(response['reason'] or status))
        self.cache.mark()
        return json.loads(response['data'])

    def statuses(self, revision):
        result = { }
        page = 1
        count = 100
        while count == 100:
            data = self.get("commits/{0}/status?page={1}&per_page={2}".format(revision, page, count))
            count = 0
            page += 1
            if "statuses" in data:
                for status in data["statuses"]:
                    if known_context(status["context"]) and status["context"] not in result:
                        result[status["context"]] = status
                count = len(data["statuses"])
        return result

    def pulls(self):
        result = [ ]
        page = 1
        count = 100
        while count == 100:
            pulls = self.get("pulls?page={0}&per_page={1}".format(page, count))
            count = 0
            page += 1
            if pulls:
                result += pulls
                count = len(pulls)
        return result

    def issues(self, labels=[ "bot" ], state="open"):
        result = [ ]
        page = 1
        count = 100
        opened = True
        label = ",".join(labels)
        while count == 100 and opened:
            req = "issues?labels={0}&state={1}&page={2}&per_page={3}".format(label, state, page, count)
            issues = self.get(req)
            count = 0
            page += 1
            opened = False
            for issue in issues:
                if issue["state"] == "open":
                    opened = True
                count += 1
                result.append(issue)
        return result

class Checklist(object):
    def __init__(self, body):
        self.process(body)

    def process(self, body, items={ }):
        self.items = { }
        lines = [ ]
        items = items.copy()
        for line in body.splitlines():
            item = None
            checked = False
            stripped = line.strip()
            if stripped.startswith("* [ ] "):
                item = stripped[6:].strip()
                checked = False
            elif stripped.startswith("* [x] "):
                item = stripped[6:].strip()
                checked = True
            if item:
                if item in items:
                    checked = items[item]
                    del items[item]
                line = " * [{0}] {1}".format(checked and "x" or " ", item)
                self.items[item] = checked
            lines.append(line)
        for item, checked in items.items():
            line = " * [{0}] {1}".format(checked and "x" or " ", item)
            lines.append(line)
        self.body = "\n".join(lines)

    def check(self, item, checked=True):
        self.process(self.body, { item: checked })

    def add(self, item):
        self.process(self.body, { item: False })
