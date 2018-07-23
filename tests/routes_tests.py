"""Tests for routes module"""
import json
import unittest

import paket_stellar
import util.logger
import webserver

import db
import routes

LOGGER = util.logger.logging.getLogger('pkt.funder.test')
util.logger.setup()
APP = webserver.setup(routes.BLUEPRINT)
APP.testing = True


class BaseRoutesTests(unittest.TestCase):
    """Base class for all routes tests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = APP.test_client()
        self.host = 'http://localhost'
        LOGGER.info('init done')

    def setUp(self):
        """Clear table and refill them with new data"""
        try:
            LOGGER.info('creating tables...')
            db.init_db()
        except db.util.db.mysql.connector.ProgrammingError:
            LOGGER.info('tables already exists')
        db.util.db.clear_tables(db.SQL_CONNECTION, db.DB_NAME)

    def call(self, path, expected_code=None, fail_message=None, seed=None, **kwargs):
        """Post data to API server."""
        LOGGER.info("calling %s", path)
        if seed:
            fingerprint = webserver.validation.generate_fingerprint(
                "{}/v{}/{}".format(self.host, routes.VERSION, path), kwargs)
            signature = webserver.validation.sign_fingerprint(fingerprint, seed)
            headers = {
                'Pubkey': paket_stellar.get_keypair(seed=seed).address().decode(),
                'Fingerprint': fingerprint, 'Signature': signature}
        else:
            headers = None
        response = self.app.post("/v{}/{}".format(routes.VERSION, path), headers=headers, data=kwargs)
        response = dict(real_status_code=response.status_code, **json.loads(response.data.decode()))
        if expected_code:
            self.assertEqual(response['real_status_code'], expected_code, "{} ({})".format(
                fail_message, response.get('error')))
        return response

    def internal_test_create_user(self, keypair, call_sign, **kwargs):
        """Create user"""
        pubkey = keypair.address().decode()
        seed = keypair.seed()
        user = self.call(
            'create_user', 201, 'could not create user', seed,
            user_pubkey=pubkey, call_sign=call_sign, **kwargs)['user']
        self.assertEqual(user['pubkey'], pubkey,
                         "pubkey of created user: {} does not match given: {}".format(user['pubkey'], pubkey))
        self.assertEqual(user['call_sign'], call_sign,
                         "call sign of created user: {} does not match given: {}".format(user['call_sign'], call_sign))
        return user


class CreateUserTest(BaseRoutesTests):
    """Test for create_user endpoint."""

    def test_create_user(self):
        """Test create user."""
        keypair = paket_stellar.get_keypair()
        call_sign = 'test_user'
        self.internal_test_create_user(keypair, call_sign)
        users = db.get_users()
        self.assertEqual(len(users), 1, "number of existing users: {} should be 1".format(len(users)))

    def test_create_with_infos(self):
        """Test create user with provided user info."""
        keypair = paket_stellar.get_keypair()
        call_sign = 'test_user'
        full_name = 'Kapitoshka Vodyanovych'
        phone_number = '+380 67 13 666'
        address = 'Vulychna 14, Trypillya'
        user = self.internal_test_create_user(
            keypair, call_sign, full_name=full_name, phone_number=phone_number, address=address)
        user_infos = db.get_user_infos(user['pubkey'])
        self.assertEqual(
            user_infos['full_name'], full_name,
            "stored full name: {} does not match given: {}".format(user_infos['full_name'], full_name))
        self.assertEqual(
            user_infos['phone_number'], phone_number,
            "stored phone number: {} does not match given: {}".format(user_infos['phone_number'], phone_number))
        self.assertEqual(
            user_infos['address'], address,
            "stored address: {} does not match given: {}".format(user_infos['address'], address))


class GetUserTest(BaseRoutesTests):
    """Test for get_user endpoint."""

    def test_get_user_by_pubkey(self):
        """Test get user by pubkey."""
        keypair = paket_stellar.get_keypair()
        call_sign = 'test_user'
        user = self.internal_test_create_user(keypair, call_sign)
        stored_user = self.call('get_user', 200, 'could not get user', pubkey=user['pubkey'])['user']
        self.assertEqual(
            stored_user['pubkey'], user['pubkey'], "stored user: {} does not match created one: {}".format(
                stored_user['pubkey'], user['pubkey']))

    def test_get_user_by_call_sign(self):
        """Test get user by call sign."""
        keypair = paket_stellar.get_keypair()
        call_sign = 'test_user'
        user = self.internal_test_create_user(keypair, call_sign)
        stored_user = self.call('get_user', 200, 'could not get user', call_sign=call_sign)['user']
        self.assertEqual(
            stored_user['call_sign'], user['call_sign'], "stored user: {} does not match created one: {}".format(
                stored_user['call_sign'], user['call_sign']))
