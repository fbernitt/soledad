# -*- coding: utf-8 -*-
# test_sync.py
# Copyright (C) 2013, 2014 LEAP
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.


import mock
import os
import json
import tempfile
import threading
import time
from urlparse import urljoin

from leap.soledad.common import couch

from leap.soledad.common.tests import BaseSoledadTest
from leap.soledad.common.tests import test_sync_target
from leap.soledad.common.tests import u1db_tests as tests
from leap.soledad.common.tests.u1db_tests import (
    TestCaseWithServer,
    simple_doc,
    test_backends,
    test_sync
)
from leap.soledad.common.tests.test_couch import CouchDBTestCase
from leap.soledad.common.tests.test_target_soledad import (
    make_token_soledad_app,
    make_leap_document_for_test,
)
from leap.soledad.common.tests.test_sync_target import token_leap_sync_target
from leap.soledad.client import (
    Soledad,
    target,
)
from leap.soledad.common.tests.util import SoledadWithCouchServerMixin
from leap.soledad.client.sync import SoledadSynchronizer
from leap.soledad.server import SoledadApp



class InterruptableSyncTestCase(
        CouchDBTestCase, TestCaseWithServer):
    """
    Tests for encrypted sync using Soledad server backed by a couch database.
    """

    @staticmethod
    def make_app_with_state(state):
        return make_token_soledad_app(state)

    make_document_for_test = make_leap_document_for_test

    sync_target = token_leap_sync_target

    def _soledad_instance(self, user='user-uuid', passphrase=u'123',
                          prefix='',
                          secrets_path=Soledad.STORAGE_SECRETS_FILE_NAME,
                          local_db_path='soledad.u1db', server_url='',
                          cert_file=None, auth_token=None, secret_id=None):
        """
        Instantiate Soledad.
        """

        # this callback ensures we save a document which is sent to the shared
        # db.
        def _put_doc_side_effect(doc):
            self._doc_put = doc

        # we need a mocked shared db or else Soledad will try to access the
        # network to find if there are uploaded secrets.
        class MockSharedDB(object):

            get_doc = mock.Mock(return_value=None)
            put_doc = mock.Mock(side_effect=_put_doc_side_effect)
            lock = mock.Mock(return_value=('atoken', 300))
            unlock = mock.Mock()

            def __call__(self):
                return self

        Soledad._shared_db = MockSharedDB()
        return Soledad(
            user,
            passphrase,
            secrets_path=os.path.join(self.tempdir, prefix, secrets_path),
            local_db_path=os.path.join(
                self.tempdir, prefix, local_db_path),
            server_url=server_url,
            cert_file=cert_file,
            auth_token=auth_token,
            secret_id=secret_id)

    def make_app(self):
        self.request_state = couch.CouchServerState(
            self._couch_url, 'shared', 'tokens')
        return self.make_app_with_state(self.request_state)

    def setUp(self):
        TestCaseWithServer.setUp(self)
        CouchDBTestCase.setUp(self)
        self.tempdir = tempfile.mkdtemp(prefix="leap_tests-")
        self._couch_url = 'http://localhost:' + str(self.wrapper.port)

    def tearDown(self):
        CouchDBTestCase.tearDown(self)
        TestCaseWithServer.tearDown(self)

    def test_interruptable_sync(self):
        """
        Test if Soledad can sync many smallfiles.
        """

        class _SyncInterruptor(threading.Thread):
            """
            A thread meant to interrupt the sync process.
            """
            
            def __init__(self, soledad, couchdb):
                self._soledad = soledad
                self._couchdb = couchdb
                threading.Thread.__init__(self)

            def run(self):
                while db._get_generation() < 2:
                    time.sleep(1)
                self._soledad.stop_sync()
                time.sleep(1)

        number_of_docs = 10
        self.startServer()

        # instantiate soledad and create a document
        sol = self._soledad_instance(
            # token is verified in test_target.make_token_soledad_app
            auth_token='auth-token'
        )
        _, doclist = sol.get_all_docs()
        self.assertEqual([], doclist)

        # create many small files
        for i in range(0, number_of_docs):
            sol.create_doc(json.loads(simple_doc))

        # ensure remote db exists before syncing
        db = couch.CouchDatabase.open_database(
            urljoin(self._couch_url, 'user-user-uuid'),
            create=True,
            ensure_ddocs=True)

        # create interruptor thread
        t = _SyncInterruptor(sol, db)
        t.start()

        # sync with server
        sol._server_url = self.getURL()
        sol.sync()  # this will be interrupted when couch db gen >= 2
        t.join()

        # recover the sync process
        sol.sync()

        gen, doclist = db.get_all_docs()
        self.assertEqual(number_of_docs, len(doclist))

        # delete remote database
        db.delete_database()
        db.close()
        sol.close()


def make_soledad_app(state):
    return SoledadApp(state)


class TestSoledadDbSync(
        SoledadWithCouchServerMixin,
        test_sync.TestDbSync):
    """
    Test db.sync remote sync shortcut
    """

    scenarios = [
        ('py-http', {
            'make_app_with_state': make_soledad_app,
            'make_database_for_test': tests.make_memory_database_for_test,
        }),
        ('py-token-http', {
            'make_app_with_state': test_sync_target.make_token_soledad_app,
            'make_database_for_test': tests.make_memory_database_for_test,
            'token': True
        }),
    ]

    oauth = False
    token = False

    def setUp(self):
        """
        Need to explicitely invoke inicialization on all bases.
        """
        tests.TestCaseWithServer.setUp(self)
        self.main_test_class = test_sync.TestDbSync
        SoledadWithCouchServerMixin.setUp(self)
        self.startServer()
        self.db2 = couch.CouchDatabase.open_database(
            urljoin(
                'http://localhost:' + str(self.wrapper.port), 'test'),
                create=True,
                ensure_ddocs=True)

    def tearDown(self):
        """
        Need to explicitely invoke destruction on all bases.
        """
        self.db2.delete_database()
        SoledadWithCouchServerMixin.tearDown(self)
        tests.TestCaseWithServer.tearDown(self)

    def do_sync(self, target_name):
        """
        Perform sync using SoledadSynchronizer, SoledadSyncTarget
        and Token auth.
        """
        extra = {}
        extra = dict(creds={'token': {
            'uuid': 'user-uuid',
            'token': 'auth-token',
        }})
        target_url = self.getURL(target_name)
        return SoledadSynchronizer(
            self.db,
            target.SoledadSyncTarget(
                target_url,
                crypto=self._soledad._crypto,
                **extra)).sync(autocreate=True,
                               defer_decryption=False)

    def test_db_sync(self):
        """
        Test sync.

        Adapted to check for encrypted content.
        """
        doc1 = self.db.create_doc_from_json(tests.simple_doc)
        doc2 = self.db2.create_doc_from_json(tests.nested_doc)
        local_gen_before_sync = self.do_sync('test')
        gen, _, changes = self.db.whats_changed(local_gen_before_sync)
        self.assertEqual(1, len(changes))
        self.assertEqual(doc2.doc_id, changes[0][0])
        self.assertEqual(1, gen - local_gen_before_sync)
        self.assertGetEncryptedDoc(
            self.db2, doc1.doc_id, doc1.rev, tests.simple_doc, False)
        self.assertGetEncryptedDoc(
            self.db, doc2.doc_id, doc2.rev, tests.nested_doc, False)

    def test_db_sync_autocreate(self):
        """
        Test sync.

        Adapted to check for encrypted content.
        """
        doc1 = self.db.create_doc_from_json(tests.simple_doc)
        local_gen_before_sync = self.do_sync('test')
        gen, _, changes = self.db.whats_changed(local_gen_before_sync)
        self.assertEqual(0, gen - local_gen_before_sync)
        db3 = self.request_state.open_database('test')
        gen, _, changes = db3.whats_changed()
        self.assertEqual(1, len(changes))
        self.assertEqual(doc1.doc_id, changes[0][0])
        self.assertGetEncryptedDoc(
            db3, doc1.doc_id, doc1.rev, tests.simple_doc, False)
        t_gen, _ = self.db._get_replica_gen_and_trans_id(
            db3.replica_uid)
        s_gen, _ = db3._get_replica_gen_and_trans_id('test1')
        self.assertEqual(1, t_gen)
        self.assertEqual(1, s_gen)


load_tests = tests.load_with_scenarios
