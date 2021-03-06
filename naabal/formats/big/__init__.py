# -*- coding: utf-8 -*-

# The MIT License (MIT)
#
# Copyright (c) 2015 Alex Headley <aheadley@waysaboutstuff.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import struct
import os
import os.path
import logging

from naabal.formats import StructuredFile, StructuredFileSection, StructuredFileSequence
from naabal.util import StringIO, datetime_to_timestamp, timestamp_to_datetime
from naabal.util.file_io import FileInFile, chunked_copy
from naabal.util.gbx_crypt import GearboxCrypt
from naabal.errors import GearboxEncryptionException

logger = logging.getLogger('naabal.formats.big')

class BigInfo(object):
    _bigfile        = None
    _offset         = 0
    _name           = None
    _mtime          = None
    _real_size      = 0
    _stored_size    = 0

    def __init__(self, bigfile):
        self._bigfile = bigfile

    def __repr__(self):
        return '<{0}("{1}")>'.format(self.__class__.__name__, self.name)

    def open(self, mode='rb'):
        return FileInFile(self._bigfile, self._offset, self.stored_size, name=self.name)

    def load(self, data):
        raise NotImplemented()

    @property
    def name(self):
        return self._name

    @property
    def mtime(self):
        return self._mtime

    @property
    def is_compressed(self):
        return self.real_size > self.stored_size

    @property
    def real_size(self):
        return self._real_size

    @property
    def stored_size(self):
        return self._stored_size

class ExternalBigInfo(BigInfo):
    def open(self, mode='rb'):
        return open(self._real_filename, mode)

    def load(self, file, alt_filename=None):
        if hasattr(file, 'read'):
            real_filename = file.name
        else:
            real_filename = file

        if alt_filename is None:
            alt_filename = filename
        self._real_filename = real_filename
        logger.debug('Loading metadata for (%s) from: %s', alt_filename, real_filename)

        fstat = os.stat(real_filename)
        self._offset         = 0
        self._name           = alt_filename
        self._mtime          = timestamp_to_datetime(fstat.st_mtime)
        self._real_size      = fstat.st_size
        self._stored_size    = fstat.st_size

class BigFile(StructuredFile):
    _members        = []

    def __iter__(self):
        return iter(self.get_members())

    def __len__(self):
        return len(self._members)

    def load(self):
        super(BigFile, self).load()
        self._members = self._get_members()
        self._sort_members()

    def check_format(self):
        key, member_type = self.STRUCTURE[0]
        self.seek(0)
        try:
            member_type(self)
        except Exception:
            return False
        return True

    def open_member(self, member, mode='rb'):
        handle = member.open(mode)
        logger.debug('Opened member [%r] in mode "%s" as: %r', member, mode, handle)
        return handle

    def get_member(self, filename):
        for member in self.get_members():
            if member.name == filename:
                return member
        else:
            raise KeyError(filename)

    def get_members(self):
        return self._members

    def get_filenames(self):
        return [member.name for member in self.get_members()]

    def extract_file(self, member, fileobj, decompress=True):
        with self.open_member(member) as infile:
            if decompress and member.is_compressed:
                logger.debug('Extracting and decompressing member: %r', member)
                self.COMPRESSION_ALGORITHM.decompress_stream(infile, fileobj)
            else:
                chunked_copy(infile.read, fileobj.write)
            logger.info('Extracted %r to %r', infile, fileobj)

    def extract(self, member, path='', decompress=True):
        full_filename = os.path.join(path, member.name)
        dir_name = os.path.dirname(full_filename)
        mtime = datetime_to_timestamp(member.mtime)

        if not os.path.isdir(dir_name):
            os.makedirs(dir_name)

        with open(full_filename, 'wb') as outfile:
            self.extract_file(member, outfile, decompress)
        os.utime(full_filename, (mtime, mtime))

    def extract_all(self, members=None, path='', decompress=True):
        if members is None:
            members = self.get_members()
        for member in members:
            self.extract(member, path, decompress)

    def add_file(self, fileobj):
        self.add(self.get_biginfo(fileobj))

    def add(self, biginfo, sort_after=True):
        logger.info('Adding member to archive: %r', biginfo)
        self._members.append(biginfo)
        if sort_after:
            self._sort_members()

    def add_all(self, path='', exclude=None):
        if exclude is None:
            exclude = lambda fn: False

        logger.debug('Walking path: %s', path)
        for dirpath, dirnames, filenames in os.walk(path, topdown=False):
            logger.debug('Found %d files in dir: %s', len(filenames), dirpath)
            for filename in (os.path.join(dirpath, fn) for fn in filenames):
                if not exclude(filename):
                    partial_filename = filename.replace(path, '', 1)
                    logger.info('Adding file as: %s => %s', filename, partial_filename)
                    self.add(self.get_biginfo(filename, alt_filename=partial_filename), False)
                else:
                    logger.debug('Excluding file: %s', filename)
        self._sort_members()

    def get_biginfo(self, filename, alt_filename=None):
        big_info = ExternalBigInfo(self)
        big_info.load(filename, alt_filename)
        return big_info

    def _get_members(self):
        raise NotImplemented()

    def _sort_members(self):
        self._members.sort(key=lambda m: m.name)

class BigSection(StructuredFileSection): pass
class BigSequence(StructuredFileSequence): pass

class GearboxEncryptedBigFile(BigFile):
    MASTER_KEY                  = None
    ENCRYPTION_KEY_MARKER       = 0x00000000
    ENCRYPTION_KEY_MAX_SIZE     = 1024 # 0x0400

    _crypto                     = None

    @property
    def data_size(self):
        return self._crypto._data_size

    def load(self):
        self._crypto = self._load_encryption()
        self._real_handle = self._handle
        self._handle = FileInFile(self._real_handle, 0, self.data_size)
        super(GearboxEncryptedBigFile, self).load()

    def check_format(self):
        try:
            self._crypto = self._load_encryption()
        except Exception:
            return False
        return super(GearboxEncryptedBigFile, self).check_format()

    def read(self, size=None):
        if self.data_size is None:
            # we don't have the key yet, just pass through
            return self._handle.read(size)
        else:
            cur_pos = self._handle.tell()
            if cur_pos < self.data_size:
                # we're gonna read encrypted data
                if size is None:
                    # make sure we don't read past the encrypted data
                    size = self.data_size - cur_pos
                else:
                    if cur_pos + size > self.data_size:
                        size = self.data_size - cur_pos
                return self._read_encrypted(size)
            else:
                return self._handle.read(size)

    def _read_encrypted(self, size):
        offset = self.tell()
        return self._crypto.decrypt(self._handle.read(size), offset)

    def _load_encryption(self):
        self.seek(-4, os.SEEK_END)
        last_int_loc = self.tell()
        marker_offset = struct.unpack('<L', self._handle.read(4))[0]
        logger.debug('Read marker offset: %d [0x%08X]', marker_offset, marker_offset)
        if marker_offset < (last_int_loc - 6):
            self.seek(-marker_offset, os.SEEK_CUR)
            encrypted_data_size = self.tell()
            marker = struct.unpack('<L', self._handle.read(4))[0]
            logger.debug('Read marker as: 0x%08X', marker)
            if marker == self.ENCRYPTION_KEY_MARKER:
                encryption_key_bytes = struct.unpack('<H', self._handle.read(2))[0]
                logger.debug('Read local encryption key length as: %d [0x%08X]',
                    encryption_key_bytes, encryption_key_bytes)
                if encryption_key_bytes <= self.ENCRYPTION_KEY_MAX_SIZE:
                    local_encryption_key = bytearray(self._handle.read(encryption_key_bytes))
                    return GearboxCrypt(encrypted_data_size, local_encryption_key, self.MASTER_KEY)
                else:
                    raise GearboxEncryptionException('Invalid encryption key size: %d > %d' %
                        (encryption_key_bytes, self.ENCRYPTION_KEY_MAX_SIZE))
            else:
                raise GearboxEncryptionException('Unexpected marker value: 0x%08X should be 0x%08X' %
                    (marker, self.ENCRYPTION_KEY_MARKER))
        else:
            raise GearboxEncryptionException('Invalid marker offset: %d', marker_offset)
