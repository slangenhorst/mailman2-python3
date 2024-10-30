# Copyright (C) 1998-2018 by the Free Software Foundation, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301,
# USA.

"""Extend mailbox.UnixMailbox.
"""

import os
import sys
import mailbox

import email
from email.parser import Parser
from email.errors import MessageParseError

from Mailman import mm_cfg
from Mailman.Message import Generator, BytesGenerator
from Mailman.Message import Message
from Mailman.Logging.Syslog import syslog


def _safeparser(fp):
    try:
        return email.message_from_binary_file(fp, Message)
    except MessageParseError:
        # Don't return None since that will stop a mailbox iterator
        return ''



class Mailbox(mailbox.mbox):
    def __init__(self, fp, factory = _safeparser):
        #mailbox.mbox.__init__(self, fp, _safeparser)
        #class mbox (_mboxMMDF)
        self._message_factory = mailbox.mboxMessage
        #class _mboxMMDF (_singlefileMailbox)
        self._f = fp
        self._toc = None
        self._next_key = 0
        self._pending = False       # No changes require rewriting the file.
        self._pending_sync = False  # No need to sync the file
        self._locked = False
        self._file_length = None    # Used to record mailbox size
        #class _singlefileMailbox (Mailbox)
        self._file = fp
        #class Mailbox
        self._factory = factory
        self._path = None

    # msg should be an rfc822 message or a subclass.
    def AppendMessage(self, msg):
        # Check the last character of the file and write a newline if it isn't
        # a newline (but not at the beginning of an empty file).
        try:
            self._f.seek(-1, 2)
        except IOError as e:
            # Assume the file is empty.  We can't portably test the error code
            # returned, since it differs per platform.
            pass
        else:
            if self._f.read(1) != b'\n':
                self._f.write(b'\n')
        # Seek to the last char of the mailbox
        self._f.seek(0, 2)
        # Create a Generator instance to write the message to the file
        g = BytesGenerator(self._f)
        g.flatten(msg, unixfrom=True)
        # Add one more trailing newline for separation with the next message
        # to be appended to the mbox.
        self._f.write(b'\n')



# This stuff is used by pipermail.py:processUnixMailbox().  It provides an
# opportunity for the built-in archiver to scrub archived messages of nasty
# things like attachments and such...
def _archfactory(mailbox):
    # The factory gets a file object, but it also needs to have a MailList
    # object, so the clearest <wink> way to do this is to build a factory
    # function that has a reference to the mailbox object, which in turn holds
    # a reference to the mailing list.  Nested scopes would help here, BTW,
    # but we can't rely on them being around (e.g. Python 2.0).
    def scrubber(fp, mailbox=mailbox):
        msg = _safeparser(fp)
        if msg == '':
            return msg
        return mailbox.scrub(msg)
    return scrubber


class ArchiverMailbox(Mailbox):
    # This is a derived class which is instantiated with a reference to the
    # MailList object.  It is build such that the factory calls back into its
    # scrub() method, giving the scrubber module a chance to do its thing
    # before the message is archived.
    def __init__(self, fp, mlist):
        if mm_cfg.ARCHIVE_SCRUBBER:
            __import__(mm_cfg.ARCHIVE_SCRUBBER)
            self._scrubber = sys.modules[mm_cfg.ARCHIVE_SCRUBBER].process
        else:
            self._scrubber = None
        self._mlist = mlist
        Mailbox.__init__(self, fp, _archfactory(self))

    def scrub(self, msg):
        if self._scrubber:
            return self._scrubber(self._mlist, msg)
        else:
            return msg

    def skipping(self, flag):
        """ This method allows the archiver to skip over messages without
        scrubbing attachments into the attachments directory."""
        if flag:
            self.factory = _safeparser
        else:
            self.factory = _archfactory(self)


    def _generate_toc(self):
        linesep = os.linesep.encode('ascii')
        """Generate key-to-(start, stop) table of contents."""
        starts, stops = [0], []
        last_was_empty = False
        self._file.seek(0)
        while True:
            line_pos = self._file.tell()
            line = self._file.readline()
            if line.startswith(b'From '):
                if len(stops) < len(starts):
                    if last_was_empty:
                        stops.append(line_pos - len(linesep))
                    else:
                        # The last line before the "From " line wasn't
                        # blank, but we consider it a start of a
                        # message anyway.
                        stops.append(line_pos)
                starts.append(line_pos)
                last_was_empty = False
            elif not line:
                if last_was_empty:
                    stops.append(line_pos - len(linesep))
                else:
                    stops.append(line_pos)
                break
            elif line == linesep:
                last_was_empty = True
            else:
                last_was_empty = False
        self._toc = dict(enumerate(zip(starts, stops)))
        print(self._toc)
        self._next_key = len(self._toc)
        self._file_length = self._file.tell()
