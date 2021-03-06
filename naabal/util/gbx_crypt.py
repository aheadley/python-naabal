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

try:
    # py2
    from itertools import izip
except ImportError:
    # py3k
    izip = zip
import logging

from naabal.util import split_by
from naabal.util.c_macros import COMBINE_BYTES, SPLIT_TO_BYTES, ROTL, CAST_TO_CHAR

logger = logging.getLogger('naabal.util.gbx_crypt')

class GearboxCrypt(object):
    def __init__(self, data_size, local_key, global_key, chunk_size=4 * 1024):
        logger.debug('Setting up crypto for data size: %d', data_size)
        self._chunk_size = chunk_size
        self._data_size = data_size
        self._key_size = len(local_key)
        self._encryption_key = self._combine_keys(local_key, global_key)

    @property
    def encryption_key(self):
        return self._encryption_key

    def decrypt_stream(self, input_buffer, output_buffer, offset=0):
        start_pos = input_buffer.tell()
        offset += start_pos
        chunk = input_buffer.read(self._chunk_size)
        while chunk:
            output_buffer.write(self.decrypt(chunk, offset))
            offset += len(chunk)
            chunk = input_buffer.read(self._chunk_size)
        logger.debug('Decrypted %d bytes', input_buffer.tell() - start_pos)
        return input_buffer.tell() - start_pos

    def decrypt(self, data, offset=0):
        data = bytearray(data)
        key_data = self._key_stream(len(data), offset)
        return str(bytearray(0xFF & (c + k) for c, k in izip(data, key_data)))

    def encrypt_stream(self, input_buffer, output_buffer, offset=0):
        start_pos = input_buffer.tell()
        offset += start_pos
        chunk = input_buffer.read(self._chunk_size)
        while chunk:
            output_buffer.write(self.encrypt(chunk, offset))
            offset += len(chunk)
            chunk = input_buffer.read(self._chunk_size)
        logger.debug('Encrypted %d bytes', input_buffer.tell() - start_pos)
        return input_buffer.tell() - start_pos

    def encrypt(self, data, offset=0):
        data = bytearray(data)
        key_data = self._key_stream(len(data), offset)
        return str(bytearray(0xFF & (c - k) for c, k in izip(data, key_data)))

    def _key_stream(self, length, start_pos=0):
        key = self._encryption_key
        ks = self._key_size
        for i in xrange(start_pos, start_pos + length):
            yield key[i % ks]

    def _combine_keys(self, local_key, global_key):
        logger.debug('Creating combined key from local key of %d bytes', len(local_key))
        local_key = [COMBINE_BYTES(bytes) for bytes in split_by(local_key, 4)]
        global_key = [COMBINE_BYTES(bytes) for bytes in split_by(global_key, 4)]
        combined_key = bytearray(self._key_size)

        for i in xrange(0, self._key_size, 4):
            c = local_key[i / 4]
            for b in range(4):
                bytes = SPLIT_TO_BYTES(ROTL(c + self._data_size, 8))
                for j in range(4):
                    c = global_key[CAST_TO_CHAR(c ^ bytes[j])] ^ (c >> 8)
                combined_key[i + b] = CAST_TO_CHAR(c)
        logger.debug('Created combined key: %s', str(combined_key).encode('hex'))
        return combined_key
