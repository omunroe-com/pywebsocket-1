#!/usr/bin/env python
#
# Copyright 2009, Google Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#     * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


"""Web Socket Echo client.

This is an example Web Socket client that talks with echo_wsh.py.
This may be useful for checking mod_pywebsocket installation.

Note:
This code is far from robust, e.g., we cut corners in handshake.

Example Usage:

# server setup
 % cd $pywebsocket
 % PYTHONPATH=$cwd/src python ./mod_pywebsocket/standalone.py -p 8880 \
    -d $cwd/src/example

# run echo client
 % python ./src/example/echo_client.py -p 8880 -s localhost \
     -o http://localhost -r /echo -m test

or

# run echo client to test IETF HyBi 00 protocol
 run with --protocol-version=hybi00

or

# server setup to test Hixie 75 protocol
 run with --allow-draft75

# run echo client to test Hixie 75 protocol
 run with --protocol-version=hixie75
"""


import codecs
import logging

# Use md5 module in Python 2.4
try:
    import hashlib
    md5_hash = hashlib.md5
except ImportError:
    import md5
    md5_hash = md5.md5

from optparse import OptionParser
import random
import re
import socket
import struct
import sys


_LOG_LEVELS = {
    'debug'    : logging.DEBUG,
    'info'     : logging.INFO,
    'warn'     : logging.WARN,
    'error'    : logging.ERROR,
    'critical' : logging.CRITICAL
}

_TIMEOUT_SEC = 10
_DEFAULT_PORT = 80
_DEFAULT_SECURE_PORT = 443
_UNDEFINED_PORT = -1

# Special message that tells the echo server to start closing handshake
_GOODBYE_MESSAGE = 'Goodbye'

# Opcodes introduced in IETF HyBi 01 for the new framing format
_OPCODE_CLOSE = 1
_OPCODE_TEXT = 4


def _method_line(resource):
    return 'GET %s HTTP/1.1\r\n' % resource


def _origin_header(origin):
    # 4.1 13. concatenation of the string "Origin:", a U+0020 SPACE character,
    # and the /origin/ value, converted to ASCII lowercase, to /fields/.
    return 'Origin: %s\r\n' % origin.lower()


def _hexify(s):
    return re.sub(".", lambda x: "%02x " % ord(x.group(0)), s)


def _receive_bytes(socket, length):
    bytes = []
    while length > 0:
        new_bytes = socket.recv(length)
        if not new_bytes:
            raise Exception('connection closed unexpectedly')
        bytes.append(new_bytes)
        length -= len(new_bytes)
    return ''.join(bytes)


class _TLSSocket(object):
    """Wrapper for a TLS connection."""

    def __init__(self, raw_socket):
        self._ssl = socket.ssl(raw_socket)

    def send(self, bytes):
        return self._ssl.write(bytes)

    def recv(self, size=-1):
        return self._ssl.read(size)

    def close(self):
        # Nothing to do.
        pass


class WebSocketHandshake(object):
    """Web Socket handshake for IETF HyBi 00 or later."""

    _UPGRADE_HEADER = 'Upgrade: WebSocket\r\n'
    _CONNECTION_HEADER = 'Connection: Upgrade\r\n'

    def __init__(self, socket, options):
        self._socket = socket
        self._options = options

    def handshake(self):
        """Handshake Web Socket.

        Raises:
            Exception: handshake failed.
        """
        # 4.1 5. send request line.
        self._socket.send(_method_line(self._options.resource))
        # 4.1 6. Let /fields/ be an empty list of strings.
        fields = []
        # 4.1 7. Add the string "Upgrade: WebSocket" to /fields/.
        fields.append(WebSocketHandshake._UPGRADE_HEADER)
        # 4.1 8. Add the string "Connection: Upgrade" to /fields/.
        fields.append(WebSocketHandshake._CONNECTION_HEADER)
        # 4.1 9-12. Add Host: field to /fields/.
        fields.append(self._format_host_header())
        # 4.1 13. Add Origin: field to /fields/.
        fields.append(_origin_header(self._options.origin))
        # TODO: 4.1 14 Add Sec-WebSocket-Protocol: field to /fields/.
        # TODO: 4.1 15 Add cookie headers to /fields/.

        # 4.1 16-23. Add Sec-WebSocket-Key<n> to /fields/.
        self._number1, key1 = self._generate_sec_websocket_key()
        fields.append('Sec-WebSocket-Key1: ' + key1 + '\r\n')
        self._number2, key2 = self._generate_sec_websocket_key()
        fields.append('Sec-WebSocket-Key2: ' + key2 + '\r\n')

        if self._options.protocol_version == 'hybi01':
            fields.append('Sec-WebSocket-Draft: 1\r\n')
        elif self._options.protocol_version == 'hybi00':
            fields.append('Sec-WebSocket-Draft: 0\r\n')
        else:
            raise Exception('illegal --protocol-version flag: %s' %
                            self._options.protocol_version)

        # 4.1 24. For each string in /fields/, in a random order: send the
        # string, encoded as UTF-8, followed by a UTF-8 encoded U+000D CARRIAGE
        # RETURN U+000A LINE FEED character pair (CRLF).
        random.shuffle(fields)
        for field in fields:
            self._socket.send(field)
        # 4.1 25. send a UTF-8-encoded U+000D CARRIAGE RETURN U+000A LINE FEED
        # character pair (CRLF).
        self._socket.send('\r\n')
        # 4.1 26. let /key3/ be a string consisting of eight random bytes (or
        # equivalently, a random 64 bit integer encoded in a big-endian order).
        self._key3 = self._generate_key3()
        # 4.1 27. send /key3/ to the server.
        self._socket.send(self._key3)
        logging.debug('%s' % _hexify(self._key3))

        logging.info('Sent handshake')

        # 4.1 28. Read bytes from the server until either the connection closes,
        # or a 0x0A byte is read. let /field/ be these bytes, including the 0x0A
        # bytes.
        field = ""
        while True:
            ch = _receive_bytes(self._socket, 1)
            field += ch
            if ch == '\n':
                break
        # if /field/ is not at least seven bytes long, or if the last
        # two bytes aren't 0x0D and 0x0A respectively, or if it does not
        # contain at least two 0x20 bytes, then fail the WebSocket connection
        # and abort these steps.
        if len(field) < 7 or not field.endswith('\r\n'):
            raise Exception('wrong status line: %s' % field)
        m = re.match("[^ ]* ([^ ]*) .*", field)
        if m is None:
            raise Exception('no code found in: %s' % field)
        # 4.1 29. let /code/ be the substring of /field/ that starts from the
        # byte after the first 0x20 byte, and ends with the byte before the
        # second 0x20 byte.
        code = m.group(1)
        # 4.1 30. if /code/ is not three bytes long, or if any of the bytes in
        # /code/ are not in the range 0x30 to 0x90, then fail the WebSocket
        # connection and abort these steps.
        if not re.match("[0-9][0-9][0-9]", code):
            raise Exception('wrong code %s in: %s' % (code, field))
        # 4.1 31. if /code/, interpreted as UTF-8, is "101", then move to the
        # next step.
        if code != "101":
            raise Exception('unexpected code in: %s' % field)
        # 4.1 32-39. read fields into /fields/
        fields = self._read_fields()
        # 4.1 40. _Fields processing_
        # read a byte from server
        ch = _receive_bytes(self._socket, 1)
        if ch != '\n':  # 0x0A
            raise Exception('expected LF after line: %s: %s' % (name, value))
        # 4.1 41. check /fields/
        if len(fields['upgrade']) != 1:
            raise Exception('not one ugprade: %s' % fields['upgrade'])
        if len(fields['connection']) != 1:
            raise Exception('not one connection: %s' % fields['connection'])
        if len(fields['sec-websocket-origin']) != 1:
            raise Exception('not one sec-websocket-origin: %s' %
                            fields['sec-sebsocket-origin'])
        if len(fields['sec-websocket-location']) != 1:
            raise Exception('not one sec-websocket-location: %s' %
                            fields['sec-sebsocket-location'])
        # TODO(ukai): protocol
        # if the entry's name is "upgrade"
        #  if the value is not exactly equal to the string "WebSocket",
        #  then fail the WebSocket connection and abort these steps.
        if fields['upgrade'][0] != 'WebSocket':
            raise Exception('unexpected upgrade: %s' % fields['upgrade'][0])
        # if the entry's name is "connection"
        #  if the value, converted to ASCII lowercase, is not exactly equal
        #  to the string "upgrade", then fail the WebSocket connection and
        #  abort these steps.
        if fields['connection'][0].lower() != 'upgrade':
            raise Exception('unexpected connection: %s' %
                            fields['connection'][0])
        # TODO(ukai): check origin, location, cookie, ..

        # 4.1 42. let /challenge/ be the concatenation of /number_1/,
        # expressed as a big endian 32 bit integer, /number_2/, expressed
        # as big endian 32 bit integer, and the eight bytes of /key_3/ in the
        # order they were sent on the wire.
        challenge = struct.pack("!I", self._number1)
        challenge += struct.pack("!I", self._number2)
        challenge += self._key3

        logging.debug('num %d, %d, %s' % (
            self._number1, self._number2,
            _hexify(self._key3)))
        logging.debug('challenge: %s' % _hexify(challenge))

        # 4.1 43. let /expected/ be the MD5 fingerprint of /challenge/ as a
        # big-endian 128 bit string.
        expected = md5_hash(challenge).digest()
        logging.debug('expected : %s' % _hexify(expected))

        # 4.1 44. read sixteen bytes from the server.
        # let /reply/ be those bytes.
        reply = _receive_bytes(self._socket, 16)
        logging.debug('reply    : %s' % _hexify(reply))

        # 4.1 45. if /reply/ does not exactly equal /expected/, then fail
        # the WebSocket connection and abort these steps.
        if expected != reply:
            raise Exception('challenge/response failed: %s != %s' % (
                expected, reply))
        # 4.1 46. The *WebSocket connection is established*.

    def _generate_sec_websocket_key(self):
        # 4.1 16. let /spaces_n/ be a random integer from 1 to 12 inclusive.
        spaces = random.randint(1, 12)
        # 4.1 17. let /max_n/ be the largest integer not greater than
        #  4,294,967,295 divided by /spaces_n/.
        maxnum = 4294967295 / spaces
        # 4.1 18. let /number_n/ be a random integer from 0 to /max_n/
        # inclusive.
        number = random.randint(0, maxnum)
        # 4.1 19. let /product_n/ be the result of multiplying /number_n/ and
        # /spaces_n/ together.
        product = number * spaces
        # 4.1 20. let /key_n/ be a string consisting of /product_n/, expressed
        # in base ten using the numerals in the range U+0030 DIGIT ZERO (0) to
        # U+0039 DIGIT NINE (9).
        key = str(product)
        # 4.1 21. insert between one and twelve random characters from the
        # range U+0021 to U+002F and U+003A to U+007E into /key_n/ at random
        # positions.
        available_chars = range(0x21, 0x2f + 1) + range(0x3a, 0x7e + 1)
        n = random.randint(1, 12)
        for _ in xrange(n):
            ch = random.choice(available_chars)
            pos = random.randint(0, len(key))
            key = key[0:pos] + chr(ch) + key[pos:]
        # 4.1 22. insert /spaces_n/ U+0020 SPACE characters into /key_n/ at
        # random positions other than start or end of the string.
        for _ in xrange(spaces):
            pos = random.randint(1, len(key) - 1)
            key = key[0:pos] + ' ' + key[pos:]
        return number, key

    def _generate_key3(self):
        # 4.1 26. let /key3/ be a string consisting of eight random bytes (or
        # equivalently, a random 64 bit integer encoded in a big-endian order).
        return ''.join([chr(random.randint(0, 255)) for _ in xrange(8)])

    def _read_fields(self):
        # 4.1 32. let /fields/ be a list of name-value pairs, initially empty.
        fields = {}
        while True:  # "Field"
            # 4.1 33. let /name/ and /value/ be empty byte arrays
            name = ''
            value = ''
            # 4.1 34. read /name/
            name = self._read_name()
            if name is None:
                break
            # 4.1 35. read spaces
            # TODO(tyoshino): Skip only one space as described in the spec.
            ch = self._skip_spaces()
            # 4.1 36. read /value/
            value = self._read_value(ch)
            # 4.1 37. read a byte from the server
            ch = _receive_bytes(self._socket, 1)
            if ch != '\n':  # 0x0A
                raise Exception('expected LF after line: %s: %s' % (
                    name, value))
            # 4.1 38. append an entry to the /fields/ list that has the name
            # given by the string obtained by interpreting the /name/ byte
            # array as a UTF-8 stream and the value given by the string
            # obtained by interpreting the /value/ byte array as a UTF-8 byte
            # stream.
            fields.setdefault(name, []).append(value)
            # 4.1 39. return to the "Field" step above
        return fields

    def _read_name(self):
        # 4.1 33. let /name/ be empty byte arrays
        name = ""
        while True:
            # 4.1 34. read a byte from the server
            ch = _receive_bytes(self._socket, 1)
            if ch == '\r':  # 0x0D
                return None
            elif ch == '\n':  # 0x0A
                raise Exception(
                    'unexpected LF when reading header name (%r)' % name)
            elif ch == ':':  # 0x3A
                return name
            elif ch >= 'A' and ch <= 'Z':  # range 0x31 to 0x5A
                ch = chr(ord(ch) + 0x20)
                name += ch
            else:
                name += ch

    def _skip_spaces(self):
        # 4.1 35. read a byte from the server
        while True:
            ch = _receive_bytes(self._socket, 1)
            if ch == ' ':  # 0x20
                continue
            return ch

    def _read_value(self, ch):
        # 4.1 33. let /value/ be empty byte arrays
        value = ''
        # 4.1 36. read a byte from server.
        while True:
            if ch == '\r':  # 0x0D
                return value
            elif ch == '\n':  # 0x0A
                raise Exception(
                    'unexpected LF when reading header value (%r)' % value)
            else:
                value += ch
            ch = _receive_bytes(self._socket, 1)

    def _skip_headers(self):
        terminator = '\r\n\r\n'
        pos = 0
        while pos < len(terminator):
            received = _receive_bytes(self._socket, 1)
            if received == terminator[pos]:
                pos += 1
            elif received == terminator[0]:
                pos = 1
            else:
                pos = 0

    def _format_host_header(self):
        # 4.1 9. Let /hostport/ be an empty string.
        hostport = ''
        # 4.1 10. Append the /host/ value, converted to ASCII lowercase, to
        # /hostport/
        hostport = self._options.server_host.lower()
        # 4.1 11. If /secure/ is false, and /port/ is not 80, or if /secure/
        # is true, and /port/ is not 443, then append a U+003A COLON character
        # (:) followed by the value of /port/, expressed as a base-ten integer,
        # to /hostport/
        if ((not self._options.use_tls and
             self._options.server_port != _DEFAULT_PORT) or
            (self._options.use_tls and
             self._options.server_port != _DEFAULT_SECURE_PORT)):
            hostport += ':' + str(self._options.server_port)
        # 4.1 12. concatenation of the string "Host:", a U+0020 SPACE
        # character, and /hostport/, to /fields/.
        host = 'Host: ' + hostport + '\r\n'
        return host


class WebSocketHixie75Handshake(WebSocketHandshake):
    """Web Socket Hixie 75 handshake."""

    _EXPECTED_RESPONSE = (
        'HTTP/1.1 101 Web Socket Protocol Handshake\r\n' +
        WebSocketHandshake._UPGRADE_HEADER +
        WebSocketHandshake._CONNECTION_HEADER)

    def __init__(self, socket, options):
        WebSocketHandshake.__init__(self, socket, options)

    def handshake(self):
        self._socket.send(_method_line(self._options.resource))
        self._socket.send(WebSocketHandshake._UPGRADE_HEADER)
        self._socket.send(WebSocketHandshake._CONNECTION_HEADER)
        self._socket.send(self._format_host_header())
        self._socket.send(_origin_header(self._options.origin))
        self._socket.send('\r\n')

        logging.info('Sent handshake')

        for expected_char in WebSocketHixie75Handshake._EXPECTED_RESPONSE:
            received = _receive_bytes(self._socket, 1)
            if expected_char != received:
                raise Exception('Handshake failure')
        # We cut corners and skip other headers.
        self._skip_headers()


class EchoClient(object):
    """Web Socket echo client."""

    def __init__(self, options):
        self._options = options
        self._socket = None

    def run(self):
        """Run the client.

        Shake hands and then repeat sending message and receiving its echo.
        """

        self._socket = socket.socket()
        self._socket.settimeout(self._options.socket_timeout)
        try:
            self._socket.connect((self._options.server_host,
                                  self._options.server_port))
            if self._options.use_tls:
                self._socket = _TLSSocket(self._socket)

            self._handshake = self._create_handshake()

            self._handshake.handshake()

            logging.info('Connection established')

            for line in self._options.message.split(','):
                frame = self._create_text_frame(line)

                self._socket.send(frame)
                if self._options.verbose:
                    print 'Send: %s' % line
                received = _receive_bytes(self._socket, len(frame))

                try:
                    payload = self._parse_frame_briefly(received)

                    if self._options.verbose:
                        print 'Recv: %s' % payload

                    if received != frame:
                        raise Exception('Incorrect echo: %r' % received)
                except Exception, e:
                    if self._options.verbose:
                        print 'Error: %s' % e
                    raise

            self._do_closing_handshake()
        finally:
            self._socket.close()

    def _do_closing_handshake_generic(self, closing_frame):
        """Perform closing handshake using the specified closing frame."""

        closing = ''

        try:
            try:
                if self._options.message.split(',')[-1] == _GOODBYE_MESSAGE:
                    # requested server initiated closing handshake, so
                    # expecting closing handshake message from server.
                    logging.info('Wait for server-initiated closing handshake')
                    closing = _receive_bytes(self._socket, len(closing_frame))
                    if closing == closing_frame:
                        # 4.2 3 8 If the /frame type/ is 0xFF and the
                        # /length/ was 0, then run the following substeps.
                        # TODO(ukai): handle \xff\x80..\x00 case.
                        # 1. If the WebSocket closing handshake has not
                        # yet started, then start the WebSocket closing
                        # handshake.
                        print 'Recv close'
                        self._socket.send(closing_frame)
                        print 'Send ack'
                        logging.info(
                            'Received closing handshake and sent ack')
                        # 2. Wait until either the WebSocket closing
                        # handshake has started or the WebSocket connection
                        # is closed.
                        # 3. If the WebSocket connection is not already
                        # closed, then close the WebSocket connection.
                        # close() in finally.
            except Exception, ex:
                print 'Exception: %s' % ex
        finally:
            # if server didn't initiate closing handshake, start
            # closing handshake from client.
            if closing != closing_frame:
                # 2, 3 Send a 0xFF byte and 0x00 byte to the server.
                self._socket.send(closing_frame)
                print 'Send close'
                logging.info('Sent closing handshake')
                # 4 The WebSocket closing handshake has started.
                # 5 Wait a user-agent-determined length of time, or
                # until the WebSocket connection is closed.
                # NOTE: the closing handshake finishes once the server
                # returns the 0xFF package, as described above.
                closing = _receive_bytes(self._socket, len(closing_frame))
                if closing != closing_frame:
                    logging.info('Received no valid ack')
                else:
                    print 'Recv ack'
                    logging.info('Received ack')

    def _create_handshake(self):
        return WebSocketHandshake(self._socket, self._options)

    def _create_text_frame(self, payload):
        encoded_payload = payload.encode('utf-8')
        header = chr(_OPCODE_TEXT)
        payload_length = len(encoded_payload)
        if payload_length <= 125:
            header += chr(payload_length)
        elif payload_length < 1 << 16:
            header += chr(126) + struct.pack('!H', payload_length)
        elif payload_length < 1 << 63:
            header += chr(127) + struct.pack('!Q', payload_length)
        else:
            raise Exception('Too long payload (%d byte)' % payload_length)
        return header + encoded_payload

    def _parse_frame_briefly(self, frame):
        if len(frame) <= 1:
            raise Exception('Incomplete %d octet frame' % len(frame))

        first_byte = ord(frame[0])
        if first_byte & 0xf != _OPCODE_TEXT:
            raise Exception('Bad opcode %d' % (first_byte & 0xf))
        elif first_byte & 0xf0:
            raise Exception(
                'Any of unsupported flag more/rsv1/rsv2/rsv3 is set')

        second_byte = ord(frame[1])
        if second_byte & 0x80:
            raise Exception('Unsupported flag rsv4 is set')

        payload_length = second_byte & 0x7f
        if payload_length == 127:
            if len(frame) < 2 + 8:
                raise Exception('Incomplete length header')
            payload_length = struct.unpack('!Q', frame[2:9])[0]
            payload_pos = 10
        elif payload_length == 126:
            if len(frame) < 2 + 2:
                raise Exception('Incomplete length header')
            payload_length = struct.unpack('!H', frame[2:3])[0]
            payload_pos = 4
        else:
            payload_pos = 2

        if len(frame) < payload_pos + payload_length:
            raise Exception('Incomplete payload')

        payload = frame[payload_pos:payload_pos + payload_length]
        return payload.decode('utf-8', 'replace')

    def _do_closing_handshake(self):
        self._do_closing_handshake_generic(chr(_OPCODE_CLOSE) + '\x00')

    def _parse_frame_briefly_hixie75(self, frame):
        if len(frame) <= 0:
            raise Exception('Incomplete 0 octet frame')
        elif frame[0] != '\x00':
            raise Exception('Bad frame type %d' % ord(frame[0]))
        else:
            return frame[1:-1].decode('utf-8', 'replace')


class EchoClientHybi00(EchoClient):
    """Web Socket echo client using IETF HyBi 00 protocol."""

    def _create_handshake(self):
        return WebSocketHandshake(self._socket, self._options)

    def _create_text_frame(self, payload):
        encoded_payload = payload.encode('utf-8')
        return ''.join(['\x00', encoded_payload, '\xff'])

    def _parse_frame_briefly(self, frame):
        return self._parse_frame_briefly_hixie75(frame)

    def _do_closing_handshake(self):
        self._do_closing_handshake_generic('\xff\x00')


class EchoClientHixie75(EchoClient):
    """Web Socket echo client using Hixie 75 protocol."""

    def _create_handshake(self):
        return  WebSocketHixie75Handshake(self._socket, self._options)

    def _create_text_frame(self, payload):
        encoded_payload = payload.encode('utf-8')
        return ''.join(['\x00', encoded_payload, '\xff'])

    def _parse_frame_briefly(self, frame):
        return self._parse_frame_briefly_hixie75(frame)

    def _do_closing_handshake(self):
        pass


def main():
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout)

    parser = OptionParser()
    parser.add_option('-s', '--server-host', '--server_host',
                      dest='server_host', type='string',
                      default='localhost', help='server host')
    parser.add_option('-p', '--server-port', '--server_port',
                      dest='server_port', type='int',
                      default=_UNDEFINED_PORT, help='server port')
    parser.add_option('-o', '--origin', dest='origin', type='string',
                      default='http://localhost/', help='origin')
    parser.add_option('-r', '--resource', dest='resource', type='string',
                      default='/echo', help='resource path')
    parser.add_option('-m', '--message', dest='message', type='string',
                      help=('comma-separated messages to send. '
                           '%s will force close the connection from server.' %
                            _GOODBYE_MESSAGE))
    parser.add_option('-q', '--quiet', dest='verbose', action='store_false',
                      default=True, help='suppress messages')
    parser.add_option('-t', '--tls', dest='use_tls', action='store_true',
                      default=False, help='use TLS (wss://)')
    parser.add_option('-k', '--socket-timeout', '--socket_timeout',
                      dest='socket_timeout', type='int', default=_TIMEOUT_SEC,
                      help='Timeout(sec) for sockets')
    parser.add_option('--draft75', dest='draft75',
                       action='store_true', default=False,
                      help='use the Hixie 75 protocol. This overrides '
                      'protocol-version flag')
    parser.add_option('--protocol-version', '--protocol_version',
                      dest='protocol_version',
                      type='string', default='hybi01',
                      help='WebSocket protocol version to use. One of '
                      + '\'hybi01\', \'hybi00\', \'hixie75\'')
    parser.add_option('--log-level', '--log_level', type='choice',
                      dest='log_level', default='warn',
                      choices=['debug', 'info', 'warn', 'error', 'critical'],
                      help='Log level.')

    (options, unused_args) = parser.parse_args()

    logger = logging.getLogger()
    logger.setLevel(_LOG_LEVELS[options.log_level])

    if options.draft75:
        options.protocol_version = 'hixie75'

    # Default port number depends on whether TLS is used.
    if options.server_port == _UNDEFINED_PORT:
        if options.use_tls:
            options.server_port = _DEFAULT_SECURE_PORT
        else:
            options.server_port = _DEFAULT_PORT

    # optparse doesn't seem to handle non-ascii default values.
    # Set default message here.
    if not options.message:
        options.message = u'Hello,\u65e5\u672c'   # "Japan" in Japanese

    if options.protocol_version == 'hybi01':
        EchoClient(options).run()
    elif options.protocol_version == 'hybi00':
        EchoClientHybi00(options).run()
    elif options.protocol_version == 'hixie75':
        EchoClientHixie75(options).run()
    else:
        raise Exception(
            'Invalid protocol version flag: %s' % options.protocol_version)


if __name__ == '__main__':
    main()


# vi:sts=4 sw=4 et
