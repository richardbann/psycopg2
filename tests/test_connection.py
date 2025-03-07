#!/usr/bin/env python

# test_connection.py - unit test for connection attributes
#
# Copyright (C) 2008-2019 James Henstridge  <james@jamesh.id.au>
#
# psycopg2 is free software: you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# In addition, as a special exception, the copyright holders give
# permission to link this program with the OpenSSL library (or with
# modified versions of OpenSSL that use the same license as OpenSSL),
# and distribute linked combinations including the two.
#
# You must obey the GNU Lesser General Public License in all respects for
# all of the code used other than OpenSSL.
#
# psycopg2 is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public
# License for more details.

import gc
import os
import re
import sys
import time
import ctypes
import shutil
import tempfile
import threading
import subprocess as sp
from collections import deque
from operator import attrgetter
from weakref import ref
import signal
import platform

import psycopg2
import psycopg2.extras
from psycopg2 import extensions as ext

from .testutils import (
    PY2, unittest, skip_if_no_superuser, skip_before_postgres,
    skip_after_postgres, skip_before_libpq, skip_after_libpq,
    ConnectingTestCase, skip_if_tpc_disabled, skip_if_windows, slow)

from .testconfig import dbhost, dsn, dbname


class ConnectionTests(ConnectingTestCase):
    def test_closed_attribute(self):
        conn = self.conn
        self.assertEqual(conn.closed, False)
        conn.close()
        self.assertEqual(conn.closed, True)

    def test_close_idempotent(self):
        conn = self.conn
        conn.close()
        conn.close()
        self.assert_(conn.closed)

    def test_cursor_closed_attribute(self):
        conn = self.conn
        curs = conn.cursor()
        self.assertEqual(curs.closed, False)
        curs.close()
        self.assertEqual(curs.closed, True)

        # Closing the connection closes the cursor:
        curs = conn.cursor()
        conn.close()
        self.assertEqual(curs.closed, True)

    @skip_before_postgres(8, 4)
    @skip_if_no_superuser
    @skip_if_windows
    def test_cleanup_on_badconn_close(self):
        # ticket #148
        conn = self.conn
        cur = conn.cursor()
        self.assertRaises(psycopg2.OperationalError,
            cur.execute, "select pg_terminate_backend(pg_backend_pid())")

        self.assertEqual(conn.closed, 2)
        conn.close()
        self.assertEqual(conn.closed, 1)

    def test_reset(self):
        conn = self.conn
        # switch session characteristics
        conn.autocommit = True
        conn.isolation_level = 'serializable'
        conn.readonly = True
        if self.conn.info.server_version >= 90100:
            conn.deferrable = False

        self.assert_(conn.autocommit)
        self.assertEqual(conn.isolation_level, ext.ISOLATION_LEVEL_SERIALIZABLE)
        self.assert_(conn.readonly is True)
        if self.conn.info.server_version >= 90100:
            self.assert_(conn.deferrable is False)

        conn.reset()
        # now the session characteristics should be reverted
        self.assert_(not conn.autocommit)
        self.assertEqual(conn.isolation_level, ext.ISOLATION_LEVEL_DEFAULT)
        self.assert_(conn.readonly is None)
        if self.conn.info.server_version >= 90100:
            self.assert_(conn.deferrable is None)

    def test_notices(self):
        conn = self.conn
        cur = conn.cursor()
        if self.conn.info.server_version >= 90300:
            cur.execute("set client_min_messages=debug1")
        cur.execute("create temp table chatty (id serial primary key);")
        self.assertEqual("CREATE TABLE", cur.statusmessage)
        self.assert_(conn.notices)

    def test_notices_consistent_order(self):
        conn = self.conn
        cur = conn.cursor()
        if self.conn.info.server_version >= 90300:
            cur.execute("set client_min_messages=debug1")
        cur.execute("""
            create temp table table1 (id serial);
            create temp table table2 (id serial);
            """)
        cur.execute("""
            create temp table table3 (id serial);
            create temp table table4 (id serial);
            """)
        self.assertEqual(4, len(conn.notices))
        self.assert_('table1' in conn.notices[0])
        self.assert_('table2' in conn.notices[1])
        self.assert_('table3' in conn.notices[2])
        self.assert_('table4' in conn.notices[3])

    @slow
    def test_notices_limited(self):
        conn = self.conn
        cur = conn.cursor()
        if self.conn.info.server_version >= 90300:
            cur.execute("set client_min_messages=debug1")
        for i in range(0, 100, 10):
            sql = " ".join(["create temp table table%d (id serial);" % j
                            for j in range(i, i + 10)])
            cur.execute(sql)

        self.assertEqual(50, len(conn.notices))
        self.assert_('table99' in conn.notices[-1], conn.notices[-1])

    @slow
    def test_notices_deque(self):
        conn = self.conn
        self.conn.notices = deque()
        cur = conn.cursor()
        if self.conn.info.server_version >= 90300:
            cur.execute("set client_min_messages=debug1")

        cur.execute("""
            create temp table table1 (id serial);
            create temp table table2 (id serial);
            """)
        cur.execute("""
            create temp table table3 (id serial);
            create temp table table4 (id serial);""")
        self.assertEqual(len(conn.notices), 4)
        self.assert_('table1' in conn.notices.popleft())
        self.assert_('table2' in conn.notices.popleft())
        self.assert_('table3' in conn.notices.popleft())
        self.assert_('table4' in conn.notices.popleft())
        self.assertEqual(len(conn.notices), 0)

        # not limited, but no error
        for i in range(0, 100, 10):
            sql = " ".join(["create temp table table2_%d (id serial);" % j
                            for j in range(i, i + 10)])
            cur.execute(sql)

        self.assertEqual(len([n for n in conn.notices if 'CREATE TABLE' in n]),
            100)

    def test_notices_noappend(self):
        conn = self.conn
        self.conn.notices = None    # will make an error swallowes ok
        cur = conn.cursor()
        if self.conn.info.server_version >= 90300:
            cur.execute("set client_min_messages=debug1")

        cur.execute("create temp table table1 (id serial);")

        self.assertEqual(self.conn.notices, None)

    def test_server_version(self):
        self.assert_(self.conn.server_version)

    def test_protocol_version(self):
        self.assert_(self.conn.protocol_version in (2, 3),
            self.conn.protocol_version)

    def test_tpc_unsupported(self):
        cnn = self.conn
        if cnn.info.server_version >= 80100:
            return self.skipTest("tpc is supported")

        self.assertRaises(psycopg2.NotSupportedError,
            cnn.xid, 42, "foo", "bar")

    @slow
    @skip_before_postgres(8, 2)
    def test_concurrent_execution(self):
        def slave():
            cnn = self.connect()
            cur = cnn.cursor()
            cur.execute("select pg_sleep(4)")
            cur.close()
            cnn.close()

        t1 = threading.Thread(target=slave)
        t2 = threading.Thread(target=slave)
        t0 = time.time()
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assert_(time.time() - t0 < 7,
            "something broken in concurrency")

    def test_encoding_name(self):
        self.conn.set_client_encoding("EUC_JP")
        # conn.encoding is 'EUCJP' now.
        cur = self.conn.cursor()
        ext.register_type(ext.UNICODE, cur)
        cur.execute("select 'foo'::text;")
        self.assertEqual(cur.fetchone()[0], u'foo')

    def test_connect_nonnormal_envvar(self):
        # We must perform encoding normalization at connection time
        self.conn.close()
        oldenc = os.environ.get('PGCLIENTENCODING')
        os.environ['PGCLIENTENCODING'] = 'utf-8'    # malformed spelling
        try:
            self.conn = self.connect()
        finally:
            if oldenc is not None:
                os.environ['PGCLIENTENCODING'] = oldenc
            else:
                del os.environ['PGCLIENTENCODING']

    def test_connect_no_string(self):
        class MyString(str):
            pass

        conn = psycopg2.connect(MyString(dsn))
        conn.close()

    def test_weakref(self):
        conn = psycopg2.connect(dsn)
        w = ref(conn)
        conn.close()
        del conn
        gc.collect()
        self.assert_(w() is None)

    @slow
    def test_commit_concurrency(self):
        # The problem is the one reported in ticket #103. Because of bad
        # status check, we commit even when a commit is already on its way.
        # We can detect this condition by the warnings.
        conn = self.conn
        notices = []
        stop = []

        def committer():
            while not stop:
                conn.commit()
                while conn.notices:
                    notices.append((2, conn.notices.pop()))

        cur = conn.cursor()
        t1 = threading.Thread(target=committer)
        t1.start()
        for i in range(1000):
            cur.execute("select %s;", (i,))
            conn.commit()
            while conn.notices:
                notices.append((1, conn.notices.pop()))

        # Stop the committer thread
        stop.append(True)

        self.assert_(not notices, "%d notices raised" % len(notices))

    def test_connect_cursor_factory(self):
        conn = self.connect(cursor_factory=psycopg2.extras.DictCursor)
        cur = conn.cursor()
        cur.execute("select 1 as a")
        self.assertEqual(cur.fetchone()['a'], 1)

    def test_cursor_factory(self):
        self.assertEqual(self.conn.cursor_factory, None)
        cur = self.conn.cursor()
        cur.execute("select 1 as a")
        self.assertRaises(TypeError, (lambda r: r['a']), cur.fetchone())

        self.conn.cursor_factory = psycopg2.extras.DictCursor
        self.assertEqual(self.conn.cursor_factory, psycopg2.extras.DictCursor)
        cur = self.conn.cursor()
        cur.execute("select 1 as a")
        self.assertEqual(cur.fetchone()['a'], 1)

        self.conn.cursor_factory = None
        self.assertEqual(self.conn.cursor_factory, None)
        cur = self.conn.cursor()
        cur.execute("select 1 as a")
        self.assertRaises(TypeError, (lambda r: r['a']), cur.fetchone())

    def test_cursor_factory_none(self):
        # issue #210
        conn = self.connect()
        cur = conn.cursor(cursor_factory=None)
        self.assertEqual(type(cur), ext.cursor)

        conn = self.connect(cursor_factory=psycopg2.extras.DictCursor)
        cur = conn.cursor(cursor_factory=None)
        self.assertEqual(type(cur), psycopg2.extras.DictCursor)

    def test_failed_init_status(self):
        class SubConnection(ext.connection):
            def __init__(self, dsn):
                try:
                    super(SubConnection, self).__init__(dsn)
                except Exception:
                    pass

        c = SubConnection("dbname=thereisnosuchdatabasemate password=foobar")
        self.assert_(c.closed, "connection failed so it must be closed")
        self.assert_('foobar' not in c.dsn, "password was not obscured")

    def test_get_native_connection(self):
        conn = self.connect()
        capsule = conn.get_native_connection()
        # we can't do anything else in Python
        self.assertIsNotNone(capsule)

    def test_pgconn_ptr(self):
        conn = self.connect()
        self.assert_(conn.pgconn_ptr is not None)

        try:
            f = self.libpq.PQserverVersion
        except AttributeError:
            pass
        else:
            f.argtypes = [ctypes.c_void_p]
            f.restype = ctypes.c_int
            ver = f(conn.pgconn_ptr)
            if ver == 0 and sys.platform == 'darwin':
                return self.skipTest(
                    "I don't know why this func returns 0 on OSX")

            self.assertEqual(ver, conn.server_version)

        conn.close()
        self.assert_(conn.pgconn_ptr is None)

    @slow
    def test_multiprocess_close(self):
        dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(dir, "mptest.py"), 'w') as f:
                f.write("""\
import time
import psycopg2

def thread():
    conn = psycopg2.connect(%(dsn)r)
    curs = conn.cursor()
    for i in range(10):
        curs.execute("select 1")
        time.sleep(0.1)

def process():
    time.sleep(0.2)
""" % {'dsn': dsn})

            script = ("""\
import sys
sys.path.insert(0, %(dir)r)
import time
import threading
import multiprocessing
import mptest

t = threading.Thread(target=mptest.thread, name='mythread')
t.start()
time.sleep(0.2)
multiprocessing.Process(target=mptest.process, name='myprocess').start()
t.join()
""" % {'dir': dir})

            out = sp.check_output(
                [sys.executable, '-c', script], stderr=sp.STDOUT)
            self.assertEqual(out, b'', out)
        finally:
            shutil.rmtree(dir, ignore_errors=True)

    @slow
    def test_handles_keyboardinterrupt(self):
        script = """\
import psycopg2
host = "10.255.255.1"  # will timeout
try:
    psycopg2.connect(host=host, password="x", connect_timeout=1)
except KeyboardInterrupt:
    print("KeyboardInterrupt")
except Exception as e:
    print(str(e))
"""
        if platform.system() == 'Windows':
            proc = sp.Popen([sys.executable, '-c', script], stdout=sp.PIPE,
                            universal_newlines=True,
                            creationflags=sp.CREATE_NEW_PROCESS_GROUP)
            time.sleep(0.5)
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc = sp.Popen([sys.executable, '-c', script], stdout=sp.PIPE,
                            universal_newlines=True)
            time.sleep(0.5)
            proc.send_signal(signal.SIGINT)
        proc.wait()
        out = proc.stdout.read().strip()
        proc.stdout.close()
        self.assertEqual(out, "KeyboardInterrupt")


class ParseDsnTestCase(ConnectingTestCase):
    def test_parse_dsn(self):
        self.assertEqual(
            ext.parse_dsn('dbname=test user=tester password=secret'),
            dict(user='tester', password='secret', dbname='test'),
            "simple DSN parsed")

        self.assertRaises(psycopg2.ProgrammingError, ext.parse_dsn,
                          "dbname=test 2 user=tester password=secret")

        self.assertEqual(
            ext.parse_dsn("dbname='test 2' user=tester password=secret"),
            dict(user='tester', password='secret', dbname='test 2'),
            "DSN with quoting parsed")

        # Can't really use assertRaisesRegexp() here since we need to
        # make sure that secret is *not* exposed in the error message.
        raised = False
        try:
            # unterminated quote after dbname:
            ext.parse_dsn("dbname='test 2 user=tester password=secret")
        except psycopg2.ProgrammingError as e:
            raised = True
            self.assertTrue(str(e).find('secret') < 0,
                            "DSN was not exposed in error message")
        self.assertTrue(raised, "ProgrammingError raised due to invalid DSN")

    @skip_before_libpq(9, 2)
    def test_parse_dsn_uri(self):
        self.assertEqual(ext.parse_dsn('postgresql://tester:secret@/test'),
                         dict(user='tester', password='secret', dbname='test'),
                         "valid URI dsn parsed")

        raised = False
        try:
            # extra '=' after port value
            ext.parse_dsn(dsn='postgresql://tester:secret@/test?port=1111=x')
        except psycopg2.ProgrammingError as e:
            raised = True
            self.assertTrue(str(e).find('secret') < 0,
                            "URI was not exposed in error message")
        self.assertTrue(raised, "ProgrammingError raised due to invalid URI")

    def test_unicode_value(self):
        snowman = u"\u2603"
        d = ext.parse_dsn('dbname=' + snowman)
        if PY2:
            self.assertEqual(d['dbname'], snowman.encode('utf8'))
        else:
            self.assertEqual(d['dbname'], snowman)

    def test_unicode_key(self):
        snowman = u"\u2603"
        self.assertRaises(psycopg2.ProgrammingError, ext.parse_dsn,
            snowman + '=' + snowman)

    def test_bad_param(self):
        self.assertRaises(TypeError, ext.parse_dsn, None)
        self.assertRaises(TypeError, ext.parse_dsn, 42)

    def test_str_subclass(self):
        class MyString(str):
            pass

        res = ext.parse_dsn(MyString("dbname=test"))
        self.assertEqual(res, {'dbname': 'test'})


class MakeDsnTestCase(ConnectingTestCase):
    def test_empty_arguments(self):
        self.assertEqual(ext.make_dsn(), '')

    def test_empty_string(self):
        dsn = ext.make_dsn('')
        self.assertEqual(dsn, '')

    def test_params_validation(self):
        self.assertRaises(psycopg2.ProgrammingError,
            ext.make_dsn, 'dbnamo=a')
        self.assertRaises(psycopg2.ProgrammingError,
            ext.make_dsn, dbnamo='a')
        self.assertRaises(psycopg2.ProgrammingError,
            ext.make_dsn, 'dbname=a', nosuchparam='b')

    def test_empty_param(self):
        dsn = ext.make_dsn(dbname='sony', password='')
        self.assertDsnEqual(dsn, "dbname=sony password=''")

    def test_escape(self):
        dsn = ext.make_dsn(dbname='hello world')
        self.assertEqual(dsn, "dbname='hello world'")

        dsn = ext.make_dsn(dbname=r'back\slash')
        self.assertEqual(dsn, r"dbname=back\\slash")

        dsn = ext.make_dsn(dbname="quo'te")
        self.assertEqual(dsn, r"dbname=quo\'te")

        dsn = ext.make_dsn(dbname="with\ttab")
        self.assertEqual(dsn, "dbname='with\ttab'")

        dsn = ext.make_dsn(dbname=r"\every thing'")
        self.assertEqual(dsn, r"dbname='\\every thing\''")

    def test_database_is_a_keyword(self):
        self.assertEqual(ext.make_dsn(database='sigh'), "dbname=sigh")

    def test_params_merging(self):
        dsn = ext.make_dsn('dbname=foo host=bar', host='baz')
        self.assertDsnEqual(dsn, 'dbname=foo host=baz')

        dsn = ext.make_dsn('dbname=foo', user='postgres')
        self.assertDsnEqual(dsn, 'dbname=foo user=postgres')

    def test_no_dsn_munging(self):
        dsnin = 'dbname=a host=b user=c password=d'
        dsn = ext.make_dsn(dsnin)
        self.assertEqual(dsn, dsnin)

    def test_null_args(self):
        dsn = ext.make_dsn("dbname=foo", user="bar", password=None)
        self.assertDsnEqual(dsn, "dbname=foo user=bar")

    @skip_before_libpq(9, 2)
    def test_url_is_cool(self):
        url = 'postgresql://tester:secret@/test?application_name=wat'
        dsn = ext.make_dsn(url)
        self.assertEqual(dsn, url)

        dsn = ext.make_dsn(url, application_name='woot')
        self.assertDsnEqual(dsn,
            'dbname=test user=tester password=secret application_name=woot')

        self.assertRaises(psycopg2.ProgrammingError,
            ext.make_dsn, 'postgresql://tester:secret@/test?nosuch=param')
        self.assertRaises(psycopg2.ProgrammingError,
            ext.make_dsn, url, nosuch="param")

    @skip_before_libpq(9, 3)
    def test_get_dsn_parameters(self):
        conn = self.connect()
        d = conn.get_dsn_parameters()
        self.assertEqual(d['dbname'], dbname)  # the only param we can check reliably
        self.assert_('password' not in d, d)


class IsolationLevelsTestCase(ConnectingTestCase):

    def setUp(self):
        ConnectingTestCase.setUp(self)

        conn = self.connect()
        cur = conn.cursor()
        try:
            cur.execute("drop table isolevel;")
        except psycopg2.ProgrammingError:
            conn.rollback()
        cur.execute("create table isolevel (id integer);")
        conn.commit()
        conn.close()

    def test_isolation_level(self):
        conn = self.connect()
        self.assertEqual(
            conn.isolation_level,
            ext.ISOLATION_LEVEL_DEFAULT)

    def test_encoding(self):
        conn = self.connect()
        self.assert_(conn.encoding in ext.encodings)

    def test_set_isolation_level(self):
        conn = self.connect()
        curs = conn.cursor()

        levels = [
            ('read uncommitted',
                ext.ISOLATION_LEVEL_READ_UNCOMMITTED),
            ('read committed', ext.ISOLATION_LEVEL_READ_COMMITTED),
            ('repeatable read', ext.ISOLATION_LEVEL_REPEATABLE_READ),
            ('serializable', ext.ISOLATION_LEVEL_SERIALIZABLE),
        ]
        for name, level in levels:
            conn.set_isolation_level(level)

            # the only values available on prehistoric PG versions
            if conn.info.server_version < 80000:
                if level in (
                        ext.ISOLATION_LEVEL_READ_UNCOMMITTED,
                        ext.ISOLATION_LEVEL_REPEATABLE_READ):
                    name, level = levels[levels.index((name, level)) + 1]

            self.assertEqual(conn.isolation_level, level)

            curs.execute('show transaction_isolation;')
            got_name = curs.fetchone()[0]

            self.assertEqual(name, got_name)
            conn.commit()

        self.assertRaises(ValueError, conn.set_isolation_level, -1)
        self.assertRaises(ValueError, conn.set_isolation_level, 5)

    def test_set_isolation_level_autocommit(self):
        conn = self.connect()
        curs = conn.cursor()

        conn.set_isolation_level(ext.ISOLATION_LEVEL_AUTOCOMMIT)
        self.assertEqual(conn.isolation_level, ext.ISOLATION_LEVEL_DEFAULT)
        self.assert_(conn.autocommit)

        conn.isolation_level = 'serializable'
        self.assertEqual(conn.isolation_level, ext.ISOLATION_LEVEL_SERIALIZABLE)
        self.assert_(conn.autocommit)

        curs.execute('show transaction_isolation;')
        self.assertEqual(curs.fetchone()[0], 'serializable')

    def test_set_isolation_level_default(self):
        conn = self.connect()
        curs = conn.cursor()

        conn.autocommit = True
        curs.execute("set default_transaction_isolation to 'read committed'")

        conn.autocommit = False
        conn.set_isolation_level(ext.ISOLATION_LEVEL_SERIALIZABLE)
        self.assertEqual(conn.isolation_level,
            ext.ISOLATION_LEVEL_SERIALIZABLE)
        curs.execute("show transaction_isolation")
        self.assertEqual(curs.fetchone()[0], "serializable")

        conn.rollback()
        conn.set_isolation_level(ext.ISOLATION_LEVEL_DEFAULT)
        curs.execute("show transaction_isolation")
        self.assertEqual(curs.fetchone()[0], "read committed")

    def test_set_isolation_level_abort(self):
        conn = self.connect()
        cur = conn.cursor()

        self.assertEqual(ext.TRANSACTION_STATUS_IDLE,
            conn.info.transaction_status)
        cur.execute("insert into isolevel values (10);")
        self.assertEqual(ext.TRANSACTION_STATUS_INTRANS,
            conn.info.transaction_status)

        conn.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_SERIALIZABLE)
        self.assertEqual(psycopg2.extensions.TRANSACTION_STATUS_IDLE,
            conn.info.transaction_status)
        cur.execute("select count(*) from isolevel;")
        self.assertEqual(0, cur.fetchone()[0])

        cur.execute("insert into isolevel values (10);")
        self.assertEqual(psycopg2.extensions.TRANSACTION_STATUS_INTRANS,
            conn.info.transaction_status)
        conn.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        self.assertEqual(psycopg2.extensions.TRANSACTION_STATUS_IDLE,
            conn.info.transaction_status)
        cur.execute("select count(*) from isolevel;")
        self.assertEqual(0, cur.fetchone()[0])

        cur.execute("insert into isolevel values (10);")
        self.assertEqual(psycopg2.extensions.TRANSACTION_STATUS_IDLE,
            conn.info.transaction_status)
        conn.set_isolation_level(
            psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)
        self.assertEqual(psycopg2.extensions.TRANSACTION_STATUS_IDLE,
            conn.info.transaction_status)
        cur.execute("select count(*) from isolevel;")
        self.assertEqual(1, cur.fetchone()[0])
        self.assertEqual(conn.isolation_level,
            psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)

    def test_isolation_level_autocommit(self):
        cnn1 = self.connect()
        cnn2 = self.connect()
        cnn2.set_isolation_level(ext.ISOLATION_LEVEL_AUTOCOMMIT)

        cur1 = cnn1.cursor()
        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(0, cur1.fetchone()[0])
        cnn1.commit()

        cur2 = cnn2.cursor()
        cur2.execute("insert into isolevel values (10);")

        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(1, cur1.fetchone()[0])

    def test_isolation_level_read_committed(self):
        cnn1 = self.connect()
        cnn2 = self.connect()
        cnn2.set_isolation_level(ext.ISOLATION_LEVEL_READ_COMMITTED)

        cur1 = cnn1.cursor()
        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(0, cur1.fetchone()[0])
        cnn1.commit()

        cur2 = cnn2.cursor()
        cur2.execute("insert into isolevel values (10);")
        cur1.execute("insert into isolevel values (20);")

        cur2.execute("select count(*) from isolevel;")
        self.assertEqual(1, cur2.fetchone()[0])
        cnn1.commit()
        cur2.execute("select count(*) from isolevel;")
        self.assertEqual(2, cur2.fetchone()[0])

        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(1, cur1.fetchone()[0])
        cnn2.commit()
        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(2, cur1.fetchone()[0])

    def test_isolation_level_serializable(self):
        cnn1 = self.connect()
        cnn2 = self.connect()
        cnn2.set_isolation_level(ext.ISOLATION_LEVEL_SERIALIZABLE)

        cur1 = cnn1.cursor()
        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(0, cur1.fetchone()[0])
        cnn1.commit()

        cur2 = cnn2.cursor()
        cur2.execute("insert into isolevel values (10);")
        cur1.execute("insert into isolevel values (20);")

        cur2.execute("select count(*) from isolevel;")
        self.assertEqual(1, cur2.fetchone()[0])
        cnn1.commit()
        cur2.execute("select count(*) from isolevel;")
        self.assertEqual(1, cur2.fetchone()[0])

        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(1, cur1.fetchone()[0])
        cnn2.commit()
        cur1.execute("select count(*) from isolevel;")
        self.assertEqual(2, cur1.fetchone()[0])

        cur2.execute("select count(*) from isolevel;")
        self.assertEqual(2, cur2.fetchone()[0])

    def test_isolation_level_closed(self):
        cnn = self.connect()
        cnn.close()
        self.assertRaises(psycopg2.InterfaceError,
            cnn.set_isolation_level, 0)
        self.assertRaises(psycopg2.InterfaceError,
            cnn.set_isolation_level, 1)

    def test_setattr_isolation_level_int(self):
        cur = self.conn.cursor()
        self.conn.isolation_level = ext.ISOLATION_LEVEL_SERIALIZABLE
        self.assertEqual(self.conn.isolation_level, ext.ISOLATION_LEVEL_SERIALIZABLE)

        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.isolation_level = ext.ISOLATION_LEVEL_REPEATABLE_READ
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_REPEATABLE_READ)
            self.assertEqual(cur.fetchone()[0], 'repeatable read')
        else:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_SERIALIZABLE)
            self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.isolation_level = ext.ISOLATION_LEVEL_READ_COMMITTED
        self.assertEqual(self.conn.isolation_level,
            ext.ISOLATION_LEVEL_READ_COMMITTED)
        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

        self.conn.isolation_level = ext.ISOLATION_LEVEL_READ_UNCOMMITTED
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_READ_UNCOMMITTED)
            self.assertEqual(cur.fetchone()[0], 'read uncommitted')
        else:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_READ_COMMITTED)
            self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

        self.assertEqual(ext.ISOLATION_LEVEL_DEFAULT, None)
        self.conn.isolation_level = ext.ISOLATION_LEVEL_DEFAULT
        self.assertEqual(self.conn.isolation_level, None)
        cur.execute("SHOW transaction_isolation;")
        isol = cur.fetchone()[0]
        cur.execute("SHOW default_transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], isol)

    def test_setattr_isolation_level_str(self):
        cur = self.conn.cursor()
        self.conn.isolation_level = "serializable"
        self.assertEqual(self.conn.isolation_level, ext.ISOLATION_LEVEL_SERIALIZABLE)

        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.isolation_level = "repeatable read"
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_REPEATABLE_READ)
            self.assertEqual(cur.fetchone()[0], 'repeatable read')
        else:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_SERIALIZABLE)
            self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.isolation_level = "read committed"
        self.assertEqual(self.conn.isolation_level,
            ext.ISOLATION_LEVEL_READ_COMMITTED)
        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

        self.conn.isolation_level = "read uncommitted"
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_READ_UNCOMMITTED)
            self.assertEqual(cur.fetchone()[0], 'read uncommitted')
        else:
            self.assertEqual(self.conn.isolation_level,
                ext.ISOLATION_LEVEL_READ_COMMITTED)
            self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

        self.conn.isolation_level = "default"
        self.assertEqual(self.conn.isolation_level, None)
        cur.execute("SHOW transaction_isolation;")
        isol = cur.fetchone()[0]
        cur.execute("SHOW default_transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], isol)

    def test_setattr_isolation_level_invalid(self):
        self.assertRaises(ValueError, setattr, self.conn, 'isolation_level', 0)
        self.assertRaises(ValueError, setattr, self.conn, 'isolation_level', -1)
        self.assertRaises(ValueError, setattr, self.conn, 'isolation_level', 5)
        self.assertRaises(ValueError, setattr, self.conn, 'isolation_level', 'bah')

    def test_attribs_segfault(self):
        # bug #790
        for i in range(10000):
            self.conn.autocommit
            self.conn.readonly
            self.conn.deferrable
            self.conn.isolation_level


@skip_if_tpc_disabled
class ConnectionTwoPhaseTests(ConnectingTestCase):
    def setUp(self):
        ConnectingTestCase.setUp(self)

        self.make_test_table()
        self.clear_test_xacts()

    def tearDown(self):
        self.clear_test_xacts()
        ConnectingTestCase.tearDown(self)

    def clear_test_xacts(self):
        """Rollback all the prepared transaction in the testing db."""
        cnn = self.connect()
        cnn.set_isolation_level(0)
        cur = cnn.cursor()
        try:
            cur.execute(
                "select gid from pg_prepared_xacts where database = %s",
                (dbname,))
        except psycopg2.ProgrammingError:
            cnn.rollback()
            cnn.close()
            return

        gids = [r[0] for r in cur]
        for gid in gids:
            cur.execute("rollback prepared %s;", (gid,))
        cnn.close()

    def make_test_table(self):
        cnn = self.connect()
        cur = cnn.cursor()
        try:
            cur.execute("DROP TABLE test_tpc;")
        except psycopg2.ProgrammingError:
            cnn.rollback()
        cur.execute("CREATE TABLE test_tpc (data text);")
        cnn.commit()
        cnn.close()

    def count_xacts(self):
        """Return the number of prepared xacts currently in the test db."""
        cnn = self.connect()
        cur = cnn.cursor()
        cur.execute("""
            select count(*) from pg_prepared_xacts
            where database = %s;""",
            (dbname,))
        rv = cur.fetchone()[0]
        cnn.close()
        return rv

    def count_test_records(self):
        """Return the number of records in the test table."""
        cnn = self.connect()
        cur = cnn.cursor()
        cur.execute("select count(*) from test_tpc;")
        rv = cur.fetchone()[0]
        cnn.close()
        return rv

    def test_tpc_commit(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, ext.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, ext.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        self.assertEqual(cnn.status, ext.STATUS_PREPARED)
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_commit()
        self.assertEqual(cnn.status, ext.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(1, self.count_test_records())

    def test_tpc_commit_one_phase(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, ext.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, ext.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit_1p');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_commit()
        self.assertEqual(cnn.status, ext.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(1, self.count_test_records())

    def test_tpc_commit_recovered(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, ext.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, ext.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit_rec');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        cnn.close()
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        cnn.tpc_commit(xid)

        self.assertEqual(cnn.status, ext.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(1, self.count_test_records())

    def test_tpc_rollback(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, ext.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, ext.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_rollback');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        self.assertEqual(cnn.status, ext.STATUS_PREPARED)
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_rollback()
        self.assertEqual(cnn.status, ext.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

    def test_tpc_rollback_one_phase(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, ext.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, ext.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_rollback_1p');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_rollback()
        self.assertEqual(cnn.status, ext.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

    def test_tpc_rollback_recovered(self):
        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        self.assertEqual(cnn.status, ext.STATUS_READY)

        cnn.tpc_begin(xid)
        self.assertEqual(cnn.status, ext.STATUS_BEGIN)

        cur = cnn.cursor()
        cur.execute("insert into test_tpc values ('test_tpc_commit_rec');")
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn.tpc_prepare()
        cnn.close()
        self.assertEqual(1, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

        cnn = self.connect()
        xid = cnn.xid(1, "gtrid", "bqual")
        cnn.tpc_rollback(xid)

        self.assertEqual(cnn.status, ext.STATUS_READY)
        self.assertEqual(0, self.count_xacts())
        self.assertEqual(0, self.count_test_records())

    def test_status_after_recover(self):
        cnn = self.connect()
        self.assertEqual(ext.STATUS_READY, cnn.status)
        cnn.tpc_recover()
        self.assertEqual(ext.STATUS_READY, cnn.status)

        cur = cnn.cursor()
        cur.execute("select 1")
        self.assertEqual(ext.STATUS_BEGIN, cnn.status)
        cnn.tpc_recover()
        self.assertEqual(ext.STATUS_BEGIN, cnn.status)

    def test_recovered_xids(self):
        # insert a few test xns
        cnn = self.connect()
        cnn.set_isolation_level(0)
        cur = cnn.cursor()
        cur.execute("begin; prepare transaction '1-foo';")
        cur.execute("begin; prepare transaction '2-bar';")

        # read the values to return
        cur.execute("""
            select gid, prepared, owner, database
            from pg_prepared_xacts
            where database = %s;""",
            (dbname,))
        okvals = cur.fetchall()
        okvals.sort()

        cnn = self.connect()
        xids = cnn.tpc_recover()
        xids = [xid for xid in xids if xid.database == dbname]
        xids.sort(key=attrgetter('gtrid'))

        # check the values returned
        self.assertEqual(len(okvals), len(xids))
        for (xid, (gid, prepared, owner, database)) in zip(xids, okvals):
            self.assertEqual(xid.gtrid, gid)
            self.assertEqual(xid.prepared, prepared)
            self.assertEqual(xid.owner, owner)
            self.assertEqual(xid.database, database)

    def test_xid_encoding(self):
        cnn = self.connect()
        xid = cnn.xid(42, "gtrid", "bqual")
        cnn.tpc_begin(xid)
        cnn.tpc_prepare()

        cnn = self.connect()
        cur = cnn.cursor()
        cur.execute("select gid from pg_prepared_xacts where database = %s;",
            (dbname,))
        self.assertEqual('42_Z3RyaWQ=_YnF1YWw=', cur.fetchone()[0])

    @slow
    def test_xid_roundtrip(self):
        for fid, gtrid, bqual in [
            (0, "", ""),
            (42, "gtrid", "bqual"),
            (0x7fffffff, "x" * 64, "y" * 64),
        ]:
            cnn = self.connect()
            xid = cnn.xid(fid, gtrid, bqual)
            cnn.tpc_begin(xid)
            cnn.tpc_prepare()
            cnn.close()

            cnn = self.connect()
            xids = [x for x in cnn.tpc_recover() if x.database == dbname]
            self.assertEqual(1, len(xids))
            xid = xids[0]
            self.assertEqual(xid.format_id, fid)
            self.assertEqual(xid.gtrid, gtrid)
            self.assertEqual(xid.bqual, bqual)

            cnn.tpc_rollback(xid)

    @slow
    def test_unparsed_roundtrip(self):
        for tid in [
            '',
            'hello, world!',
            'x' * 199,  # PostgreSQL's limit in transaction id length
        ]:
            cnn = self.connect()
            cnn.tpc_begin(tid)
            cnn.tpc_prepare()
            cnn.close()

            cnn = self.connect()
            xids = [x for x in cnn.tpc_recover() if x.database == dbname]
            self.assertEqual(1, len(xids))
            xid = xids[0]
            self.assertEqual(xid.format_id, None)
            self.assertEqual(xid.gtrid, tid)
            self.assertEqual(xid.bqual, None)

            cnn.tpc_rollback(xid)

    def test_xid_construction(self):
        x1 = ext.Xid(74, 'foo', 'bar')
        self.assertEqual(74, x1.format_id)
        self.assertEqual('foo', x1.gtrid)
        self.assertEqual('bar', x1.bqual)

    def test_xid_from_string(self):
        x2 = ext.Xid.from_string('42_Z3RyaWQ=_YnF1YWw=')
        self.assertEqual(42, x2.format_id)
        self.assertEqual('gtrid', x2.gtrid)
        self.assertEqual('bqual', x2.bqual)

        x3 = ext.Xid.from_string('99_xxx_yyy')
        self.assertEqual(None, x3.format_id)
        self.assertEqual('99_xxx_yyy', x3.gtrid)
        self.assertEqual(None, x3.bqual)

    def test_xid_to_string(self):
        x1 = ext.Xid.from_string('42_Z3RyaWQ=_YnF1YWw=')
        self.assertEqual(str(x1), '42_Z3RyaWQ=_YnF1YWw=')

        x2 = ext.Xid.from_string('99_xxx_yyy')
        self.assertEqual(str(x2), '99_xxx_yyy')

    def test_xid_unicode(self):
        cnn = self.connect()
        x1 = cnn.xid(10, u'uni', u'code')
        cnn.tpc_begin(x1)
        cnn.tpc_prepare()
        cnn.reset()
        xid = [x for x in cnn.tpc_recover() if x.database == dbname][0]
        self.assertEqual(10, xid.format_id)
        self.assertEqual('uni', xid.gtrid)
        self.assertEqual('code', xid.bqual)

    def test_xid_unicode_unparsed(self):
        # We don't expect people shooting snowmen as transaction ids,
        # so if something explodes in an encode error I don't mind.
        # Let's just check uniconde is accepted as type.
        cnn = self.connect()
        cnn.set_client_encoding('utf8')
        cnn.tpc_begin(u"transaction-id")
        cnn.tpc_prepare()
        cnn.reset()

        xid = [x for x in cnn.tpc_recover() if x.database == dbname][0]
        self.assertEqual(None, xid.format_id)
        self.assertEqual('transaction-id', xid.gtrid)
        self.assertEqual(None, xid.bqual)

    def test_cancel_fails_prepared(self):
        cnn = self.connect()
        cnn.tpc_begin('cancel')
        cnn.tpc_prepare()
        self.assertRaises(psycopg2.ProgrammingError, cnn.cancel)

    def test_tpc_recover_non_dbapi_connection(self):
        cnn = self.connect(connection_factory=psycopg2.extras.RealDictConnection)
        cnn.tpc_begin('dict-connection')
        cnn.tpc_prepare()
        cnn.reset()

        xids = cnn.tpc_recover()
        xid = [x for x in xids if x.database == dbname][0]
        self.assertEqual(None, xid.format_id)
        self.assertEqual('dict-connection', xid.gtrid)
        self.assertEqual(None, xid.bqual)


class TransactionControlTests(ConnectingTestCase):
    def test_closed(self):
        self.conn.close()
        self.assertRaises(psycopg2.InterfaceError,
            self.conn.set_session,
            ext.ISOLATION_LEVEL_SERIALIZABLE)

    def test_not_in_transaction(self):
        cur = self.conn.cursor()
        cur.execute("select 1")
        self.assertRaises(psycopg2.ProgrammingError,
            self.conn.set_session,
            ext.ISOLATION_LEVEL_SERIALIZABLE)

    def test_set_isolation_level(self):
        cur = self.conn.cursor()
        self.conn.set_session(
            ext.ISOLATION_LEVEL_SERIALIZABLE)
        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.set_session(
            ext.ISOLATION_LEVEL_REPEATABLE_READ)
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(cur.fetchone()[0], 'repeatable read')
        else:
            self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.set_session(
            isolation_level=ext.ISOLATION_LEVEL_READ_COMMITTED)
        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

        self.conn.set_session(
            isolation_level=ext.ISOLATION_LEVEL_READ_UNCOMMITTED)
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(cur.fetchone()[0], 'read uncommitted')
        else:
            self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

    def test_set_isolation_level_str(self):
        cur = self.conn.cursor()
        self.conn.set_session("serializable")
        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.set_session("repeatable read")
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(cur.fetchone()[0], 'repeatable read')
        else:
            self.assertEqual(cur.fetchone()[0], 'serializable')
        self.conn.rollback()

        self.conn.set_session("read committed")
        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

        self.conn.set_session("read uncommitted")
        cur.execute("SHOW transaction_isolation;")
        if self.conn.info.server_version > 80000:
            self.assertEqual(cur.fetchone()[0], 'read uncommitted')
        else:
            self.assertEqual(cur.fetchone()[0], 'read committed')
        self.conn.rollback()

    def test_bad_isolation_level(self):
        self.assertRaises(ValueError, self.conn.set_session, 0)
        self.assertRaises(ValueError, self.conn.set_session, 5)
        self.assertRaises(ValueError, self.conn.set_session, 'whatever')

    def test_set_read_only(self):
        self.assert_(self.conn.readonly is None)

        cur = self.conn.cursor()
        self.conn.set_session(readonly=True)
        self.assert_(self.conn.readonly is True)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.conn.rollback()
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.conn.rollback()

        self.conn.set_session(readonly=False)
        self.assert_(self.conn.readonly is False)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'off')
        self.conn.rollback()

    def test_setattr_read_only(self):
        cur = self.conn.cursor()
        self.conn.readonly = True
        self.assert_(self.conn.readonly is True)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.assertRaises(self.conn.ProgrammingError,
            setattr, self.conn, 'readonly', False)
        self.assert_(self.conn.readonly is True)
        self.conn.rollback()
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.conn.rollback()

        cur = self.conn.cursor()
        self.conn.readonly = None
        self.assert_(self.conn.readonly is None)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'off')  # assume defined by server
        self.conn.rollback()

        self.conn.readonly = False
        self.assert_(self.conn.readonly is False)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'off')
        self.conn.rollback()

    def test_set_default(self):
        cur = self.conn.cursor()
        cur.execute("SHOW transaction_isolation;")
        isolevel = cur.fetchone()[0]
        cur.execute("SHOW transaction_read_only;")
        readonly = cur.fetchone()[0]
        self.conn.rollback()

        self.conn.set_session(isolation_level='serializable', readonly=True)
        self.conn.set_session(isolation_level='default', readonly='default')

        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], isolevel)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], readonly)

    @skip_before_postgres(9, 1)
    def test_set_deferrable(self):
        self.assert_(self.conn.deferrable is None)
        cur = self.conn.cursor()
        self.conn.set_session(readonly=True, deferrable=True)
        self.assert_(self.conn.deferrable is True)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')
        cur.execute("SHOW transaction_deferrable;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.conn.rollback()
        cur.execute("SHOW transaction_deferrable;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.conn.rollback()

        self.conn.set_session(deferrable=False)
        self.assert_(self.conn.deferrable is False)
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')
        cur.execute("SHOW transaction_deferrable;")
        self.assertEqual(cur.fetchone()[0], 'off')
        self.conn.rollback()

    @skip_after_postgres(9, 1)
    def test_set_deferrable_error(self):
        self.assertRaises(psycopg2.ProgrammingError,
            self.conn.set_session, readonly=True, deferrable=True)
        self.assertRaises(psycopg2.ProgrammingError,
            setattr, self.conn, 'deferrable', True)

    @skip_before_postgres(9, 1)
    def test_setattr_deferrable(self):
        cur = self.conn.cursor()
        self.conn.deferrable = True
        self.assert_(self.conn.deferrable is True)
        cur.execute("SHOW transaction_deferrable;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.assertRaises(self.conn.ProgrammingError,
            setattr, self.conn, 'deferrable', False)
        self.assert_(self.conn.deferrable is True)
        self.conn.rollback()
        cur.execute("SHOW transaction_deferrable;")
        self.assertEqual(cur.fetchone()[0], 'on')
        self.conn.rollback()

        cur = self.conn.cursor()
        self.conn.deferrable = None
        self.assert_(self.conn.deferrable is None)
        cur.execute("SHOW transaction_deferrable;")
        self.assertEqual(cur.fetchone()[0], 'off')  # assume defined by server
        self.conn.rollback()

        self.conn.deferrable = False
        self.assert_(self.conn.deferrable is False)
        cur.execute("SHOW transaction_deferrable;")
        self.assertEqual(cur.fetchone()[0], 'off')
        self.conn.rollback()

    def test_mixing_session_attribs(self):
        cur = self.conn.cursor()
        self.conn.autocommit = True
        self.conn.readonly = True

        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')

        cur.execute("SHOW default_transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')

        self.conn.autocommit = False
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')

        cur.execute("SHOW default_transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'off')

    def test_idempotence_check(self):
        self.conn.autocommit = False
        self.conn.readonly = True
        self.conn.autocommit = True
        self.conn.readonly = True

        cur = self.conn.cursor()
        cur.execute("SHOW transaction_read_only")
        self.assertEqual(cur.fetchone()[0], 'on')


class TestEncryptPassword(ConnectingTestCase):
    @skip_before_postgres(10)
    def test_encrypt_password_post_9_6(self):
        # MD5 algorithm
        self.assertEqual(
            ext.encrypt_password('psycopg2', 'ashesh', self.conn, 'md5'),
            'md594839d658c28a357126f105b9cb14cfc')

        # keywords
        self.assertEqual(
            ext.encrypt_password(
                password='psycopg2', user='ashesh',
                scope=self.conn, algorithm='md5'),
            'md594839d658c28a357126f105b9cb14cfc')

    @skip_before_libpq(10)
    @skip_before_postgres(10)
    def test_encrypt_server(self):
        cur = self.conn.cursor()
        cur.execute("SHOW password_encryption;")
        server_encryption_algorithm = cur.fetchone()[0]

        enc_password = ext.encrypt_password(
            'psycopg2', 'ashesh', self.conn)

        if server_encryption_algorithm == 'md5':
            self.assertEqual(
                enc_password, 'md594839d658c28a357126f105b9cb14cfc')
        elif server_encryption_algorithm == 'scram-sha-256':
            self.assertEqual(enc_password[:14], 'SCRAM-SHA-256$')

        self.assertEqual(
            ext.encrypt_password(
                'psycopg2', 'ashesh', self.conn, 'scram-sha-256'
            )[:14], 'SCRAM-SHA-256$')

        self.assertRaises(psycopg2.ProgrammingError,
            ext.encrypt_password, 'psycopg2', 'ashesh', self.conn, 'abc')

    def test_encrypt_md5(self):
        self.assertEqual(
            ext.encrypt_password('psycopg2', 'ashesh', algorithm='md5'),
            'md594839d658c28a357126f105b9cb14cfc')

    @skip_before_libpq(10)
    def test_encrypt_bad_libpq_10(self):
        self.assertRaises(psycopg2.ProgrammingError,
            ext.encrypt_password, 'psycopg2', 'ashesh', self.conn, 'abc')

    @skip_after_libpq(10)
    def test_encrypt_bad_before_libpq_10(self):
        self.assertRaises(psycopg2.NotSupportedError,
            ext.encrypt_password, 'psycopg2', 'ashesh', self.conn, 'abc')

    @skip_before_libpq(10)
    def test_encrypt_scram(self):
        self.assert_(
            ext.encrypt_password(
                'psycopg2', 'ashesh', self.conn, 'scram-sha-256')
            .startswith('SCRAM-SHA-256$'))

    @skip_after_libpq(10)
    def test_encrypt_scram_pre_10(self):
        self.assertRaises(psycopg2.NotSupportedError,
            ext.encrypt_password,
            password='psycopg2', user='ashesh',
            scope=self.conn, algorithm='scram-sha-256')

    def test_bad_types(self):
        self.assertRaises(TypeError, ext.encrypt_password)
        self.assertRaises(TypeError, ext.encrypt_password,
            'password', 42, self.conn, 'md5')
        self.assertRaises(TypeError, ext.encrypt_password,
            42, 'user', self.conn, 'md5')
        self.assertRaises(TypeError, ext.encrypt_password,
            42, 'user', 'wat', 'abc')
        self.assertRaises(TypeError, ext.encrypt_password,
            'password', 'user', 'wat', 42)


class AutocommitTests(ConnectingTestCase):
    def test_closed(self):
        self.conn.close()
        self.assertRaises(psycopg2.InterfaceError,
            setattr, self.conn, 'autocommit', True)

        # The getter doesn't have a guard. We may change this in future
        # to make it consistent with other methods; meanwhile let's just check
        # it doesn't explode.
        try:
            self.assert_(self.conn.autocommit in (True, False))
        except psycopg2.InterfaceError:
            pass

    def test_default_no_autocommit(self):
        self.assert_(not self.conn.autocommit)
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

        cur = self.conn.cursor()
        cur.execute('select 1;')
        self.assertEqual(self.conn.status, ext.STATUS_BEGIN)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_INTRANS)

        self.conn.rollback()
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

    def test_set_autocommit(self):
        self.conn.autocommit = True
        self.assert_(self.conn.autocommit)
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

        cur = self.conn.cursor()
        cur.execute('select 1;')
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

        self.conn.autocommit = False
        self.assert_(not self.conn.autocommit)
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

        cur.execute('select 1;')
        self.assertEqual(self.conn.status, ext.STATUS_BEGIN)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_INTRANS)

    def test_set_intrans_error(self):
        cur = self.conn.cursor()
        cur.execute('select 1;')
        self.assertRaises(psycopg2.ProgrammingError,
            setattr, self.conn, 'autocommit', True)

    def test_set_session_autocommit(self):
        self.conn.set_session(autocommit=True)
        self.assert_(self.conn.autocommit)
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

        cur = self.conn.cursor()
        cur.execute('select 1;')
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

        self.conn.set_session(autocommit=False)
        self.assert_(not self.conn.autocommit)
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)

        cur.execute('select 1;')
        self.assertEqual(self.conn.status, ext.STATUS_BEGIN)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_INTRANS)
        self.conn.rollback()

        self.conn.set_session('serializable', readonly=True, autocommit=True)
        self.assert_(self.conn.autocommit)
        cur.execute('select 1;')
        self.assertEqual(self.conn.status, ext.STATUS_READY)
        self.assertEqual(self.conn.info.transaction_status,
            ext.TRANSACTION_STATUS_IDLE)
        cur.execute("SHOW transaction_isolation;")
        self.assertEqual(cur.fetchone()[0], 'serializable')
        cur.execute("SHOW transaction_read_only;")
        self.assertEqual(cur.fetchone()[0], 'on')


class PasswordLeakTestCase(ConnectingTestCase):
    def setUp(self):
        super(PasswordLeakTestCase, self).setUp()
        PasswordLeakTestCase.dsn = None

    class GrassingConnection(ext.connection):
        """A connection snitching the dsn away.

        This connection passes the dsn to the test case class even if init
        fails (e.g. connection error). Test that we mangle the dsn ok anyway.
        """

        def __init__(self, *args, **kwargs):
            try:
                super(PasswordLeakTestCase.GrassingConnection, self).__init__(
                    *args, **kwargs)
            finally:
                # The connection is not initialized entirely, however the C
                # code should have set the dsn, and it should have scrubbed
                # the password away
                PasswordLeakTestCase.dsn = self.dsn

    def test_leak(self):
        self.assertRaises(psycopg2.DatabaseError,
            self.GrassingConnection, "dbname=nosuch password=whateva")
        self.assertDsnEqual(self.dsn, "dbname=nosuch password=xxx")

    @skip_before_libpq(9, 2)
    def test_url_leak(self):
        self.assertRaises(psycopg2.DatabaseError,
            self.GrassingConnection,
            "postgres://someone:whateva@localhost/nosuch")

        self.assertDsnEqual(self.dsn,
            "user=someone password=xxx host=localhost dbname=nosuch")


class SignalTestCase(ConnectingTestCase):
    @slow
    @skip_before_postgres(8, 2)
    def test_bug_551_returning(self):
        # Raise an exception trying to decode 'id'
        self._test_bug_551(query="""
            INSERT INTO test551 (num) VALUES (%s) RETURNING id
            """)

    @slow
    def test_bug_551_no_returning(self):
        # Raise an exception trying to decode 'INSERT 0 1'
        self._test_bug_551(query="""
            INSERT INTO test551 (num) VALUES (%s)
            """)

    def _test_bug_551(self, query):
        script = ("""\
import os
import sys
import time
import signal
import warnings
import threading

# ignore wheel deprecation warning
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    import psycopg2

def handle_sigabort(sig, frame):
    sys.exit(1)

def killer():
    time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGABRT)

signal.signal(signal.SIGABRT, handle_sigabort)

conn = psycopg2.connect(%(dsn)r)

cur = conn.cursor()

cur.execute("create table test551 (id serial, num varchar(50))")

t = threading.Thread(target=killer)
t.daemon = True
t.start()

while True:
    cur.execute(%(query)r, ("Hello, world!",))
""" % {'dsn': dsn, 'query': query})

        proc = sp.Popen([sys.executable, '-c', script],
            stdout=sp.PIPE, stderr=sp.PIPE)
        (out, err) = proc.communicate()
        self.assertNotEqual(proc.returncode, 0)
        # Strip [NNN refs] from output
        err = re.sub(br'\[[^\]]+\]', b'', err).strip()
        self.assert_(not err, err)


class TestConnectionInfo(ConnectingTestCase):
    def setUp(self):
        ConnectingTestCase.setUp(self)

        class BrokenConn(psycopg2.extensions.connection):
            def __init__(self, *args, **kwargs):
                # don't call superclass
                pass

        # A "broken" connection
        self.bconn = self.connect(connection_factory=BrokenConn)

    def test_dbname(self):
        self.assert_(isinstance(self.conn.info.dbname, str))
        self.assert_(self.bconn.info.dbname is None)

    def test_user(self):
        cur = self.conn.cursor()
        cur.execute("select user")
        self.assertEqual(self.conn.info.user, cur.fetchone()[0])
        self.assert_(self.bconn.info.user is None)

    def test_password(self):
        self.assert_(isinstance(self.conn.info.password, str))
        self.assert_(self.bconn.info.password is None)

    def test_host(self):
        expected = dbhost if dbhost else "/"
        self.assertIn(expected, self.conn.info.host)
        self.assert_(self.bconn.info.host is None)

    def test_host_readonly(self):
        with self.assertRaises(AttributeError):
            self.conn.info.host = 'override'

    def test_port(self):
        self.assert_(isinstance(self.conn.info.port, int))
        self.assert_(self.bconn.info.port is None)

    def test_options(self):
        self.assert_(isinstance(self.conn.info.options, str))
        self.assert_(self.bconn.info.options is None)

    @skip_before_libpq(9, 3)
    def test_dsn_parameters(self):
        d = self.conn.info.dsn_parameters
        self.assert_(isinstance(d, dict))
        self.assertEqual(d['dbname'], dbname)  # the only param we can check reliably
        self.assert_('password' not in d, d)

    def test_status(self):
        self.assertEqual(self.conn.info.status, 0)
        self.assertEqual(self.bconn.info.status, 1)

    def test_transaction_status(self):
        self.assertEqual(self.conn.info.transaction_status, 0)
        cur = self.conn.cursor()
        cur.execute("select 1")
        self.assertEqual(self.conn.info.transaction_status, 2)
        self.assertEqual(self.bconn.info.transaction_status, 4)

    def test_parameter_status(self):
        cur = self.conn.cursor()
        try:
            cur.execute("show server_version")
        except psycopg2.DatabaseError:
            self.assertIsInstance(
                self.conn.info.parameter_status('server_version'), str)
        else:
            self.assertEqual(
                self.conn.info.parameter_status('server_version'),
                cur.fetchone()[0])

        self.assertIsNone(self.conn.info.parameter_status('wat'))
        self.assertIsNone(self.bconn.info.parameter_status('server_version'))

    def test_protocol_version(self):
        self.assertEqual(self.conn.info.protocol_version, 3)
        self.assertEqual(self.bconn.info.protocol_version, 0)

    def test_server_version(self):
        cur = self.conn.cursor()
        try:
            cur.execute("show server_version_num")
        except psycopg2.DatabaseError:
            self.assert_(isinstance(self.conn.info.server_version, int))
        else:
            self.assertEqual(
                self.conn.info.server_version, int(cur.fetchone()[0]))

        self.assertEqual(self.bconn.info.server_version, 0)

    def test_error_message(self):
        self.assertIsNone(self.conn.info.error_message)
        self.assertIsNotNone(self.bconn.info.error_message)

        cur = self.conn.cursor()
        try:
            cur.execute("select 1 from nosuchtable")
        except psycopg2.DatabaseError:
            pass

        self.assert_('nosuchtable' in self.conn.info.error_message)

    def test_socket(self):
        self.assert_(self.conn.info.socket >= 0)
        self.assert_(self.bconn.info.socket < 0)

    def test_backend_pid(self):
        cur = self.conn.cursor()
        try:
            cur.execute("select pg_backend_pid()")
        except psycopg2.DatabaseError:
            self.assert_(self.conn.info.backend_pid > 0)
        else:
            self.assertEqual(
                self.conn.info.backend_pid, int(cur.fetchone()[0]))

        self.assert_(self.bconn.info.backend_pid == 0)

    def test_needs_password(self):
        self.assertIs(self.conn.info.needs_password, False)
        self.assertIs(self.bconn.info.needs_password, False)

    def test_used_password(self):
        self.assertIsInstance(self.conn.info.used_password, bool)
        self.assertIs(self.bconn.info.used_password, False)

    @skip_before_libpq(9, 5)
    def test_ssl_in_use(self):
        self.assertIsInstance(self.conn.info.ssl_in_use, bool)
        self.assertIs(self.bconn.info.ssl_in_use, False)

    @skip_after_libpq(9, 5)
    def test_ssl_not_supported(self):
        with self.assertRaises(psycopg2.NotSupportedError):
            self.conn.info.ssl_in_use
        with self.assertRaises(psycopg2.NotSupportedError):
            self.conn.info.ssl_attribute_names
        with self.assertRaises(psycopg2.NotSupportedError):
            self.conn.info.ssl_attribute('wat')

    @skip_before_libpq(9, 5)
    def test_ssl_attribute(self):
        attribs = self.conn.info.ssl_attribute_names
        self.assert_(attribs)
        if self.conn.info.ssl_in_use:
            for attrib in attribs:
                self.assertIsInstance(self.conn.info.ssl_attribute(attrib), str)
        else:
            for attrib in attribs:
                self.assertIsNone(self.conn.info.ssl_attribute(attrib))

        self.assertIsNone(self.conn.info.ssl_attribute('wat'))

        for attrib in attribs:
            self.assertIsNone(self.bconn.info.ssl_attribute(attrib))


def test_suite():
    return unittest.TestLoader().loadTestsFromName(__name__)


if __name__ == "__main__":
    unittest.main()
