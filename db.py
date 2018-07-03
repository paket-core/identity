"""PaKeT database interface."""
import logging
import os
import time

import pywallet.wallet

import kyc
import util.db

LOGGER = logging.getLogger('pkt.funder.db')
SEED = ('client ancient calm uncover opinion coil priority misery empty favorite moment myth')
XPUB = 'tpubD6NzVbkrYhZ4XMSG7EWChwJXwfByid9TdZRVaej1rpDTHV3WamyuApceF5DDZXetx8kbH82NouoazYqPeCEZWWeXHZ1do5LBCe5xMcZYeGe'
DB_HOST = os.environ.get('PAKET_DB_HOST', '127.0.0.1')
DB_PORT = int(os.environ.get('PAKET_DB_PORT', 3306))
DB_USER = os.environ.get('PAKET_DB_USER', 'root')
DB_PASSWORD = os.environ.get('PAKET_DB_PASSWORD')
DB_NAME = os.environ.get('PAKET_DB_NAME', 'paket')
SQL_CONNECTION = util.db.custom_sql_connection(DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME)
MINIMUM_MONTHLY_ALLOWANCE = 5000
BASIC_MONTHLY_ALLOWANCE = 10000


class UserNotFound(Exception):
    """Requested user does not exist."""


def init_db():
    """Initialize the database."""
    with SQL_CONNECTION() as sql:
        sql.execute('''
            CREATE TABLE users(
                pubkey VARCHAR(56) PRIMARY KEY,
                call_sign VARCHAR(32) UNIQUE NOT NULL)''')
        LOGGER.debug('users table created')
        sql.execute('''
            CREATE TABLE internal_user_infos(
                timestamp TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                pubkey VARCHAR(56),
                full_name VARCHAR(256),
                phone_number VARCHAR(32),
                address VARCHAR(1024),
                PRIMARY KEY (timestamp, pubkey),
                FOREIGN KEY(pubkey) REFERENCES users(pubkey))''')
        LOGGER.debug('internal_user_infos table populated')
        sql.execute('''
            CREATE TABLE test_results(
                timestamp TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                pubkey VARCHAR(56) NOT NULL,
                name VARCHAR(64) NOT NULL,
                result INTEGER,
                PRIMARY KEY (timestamp, pubkey),
                FOREIGN KEY(pubkey) REFERENCES users(pubkey))''')
        LOGGER.debug('test_results table created')
        sql.execute('''
            CREATE TABLE purchases(
                timestamp TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                user_pubkey VARCHAR(56) NOT NULL,
                payment_pubkey VARCHAR(56) NOT NULL,
                payment_currency VARCHAR(3) NOT NULL,
                requested_currency VARCHAR(3) NOT NULL DEFAULT 'BUL',
                euro_cents INTEGER NOT NULL,
                paid INTEGER DEFAULT 0,
                FOREIGN KEY(user_pubkey) REFERENCES users(pubkey))''')
        LOGGER.debug('purchases table created')


def create_user(pubkey, call_sign):
    """Create a new user."""
    with SQL_CONNECTION() as sql:
        try:
            sql.execute("INSERT INTO users (pubkey, call_sign) VALUES (%s, %s)", (pubkey, call_sign))
        except util.db.mysql.connector.IntegrityError as exception:
            bad_column_name = str(exception).split('.')[-1]
            bad_value = locals().get(bad_column_name)
            raise AssertionError("{} {} is non unique".format(bad_column_name, bad_value))


def get_user(pubkey=None, call_sign=None):
    """Get user pubkey, call_sign, and purchase allowance from either pubkey or call_sign."""
    assert bool(pubkey or call_sign) != bool(pubkey and call_sign), 'specify either pubkey or call_sign'
    condition = ('pubkey', pubkey) if pubkey else ('call_sign', call_sign)
    with SQL_CONNECTION() as sql:
        sql.execute("SELECT * FROM users WHERE {} = %s".format(condition[0]), (condition[1], ))
        try:
            users = sql.fetchall()
            assert len(users) == 1
            return users[0]
        except AssertionError:
            raise UserNotFound("user with {} {} does not exists".format(*condition))


def update_test(pubkey, test_name, result=None):
    """Update a test for a user."""
    with SQL_CONNECTION() as sql:
        try:
            sql.execute("INSERT INTO test_results (pubkey, name, result) VALUES (%s, %s, %s)", (
                pubkey, test_name, result))
        except util.db.mysql.connector.IntegrityError:
            raise UserNotFound("no user with pubkey {}".format(pubkey))


def get_test_result(pubkey, test_name):
    """Get the latest result of a test."""
    with SQL_CONNECTION() as sql:
        sql.execute("SELECT result FROM test_results WHERE pubkey = %s AND name = %s", (
            pubkey, test_name))
        try:
            return sql.fetchall()[0]['result']
        except IndexError:
            return 0


def get_user_infos(pubkey):
    """Get all user infos."""
    with SQL_CONNECTION() as sql:
        sql.execute(
            "SELECT * FROM internal_user_infos WHERE pubkey = %s", (pubkey,))
        try:
            return {
                key.decode('utf8') if isinstance(key, bytes) else key: val
                for key, val in sql.fetchall()[0].items()}
        except IndexError:
            raise UserNotFound("user with pubkey {} does not exists".format(pubkey))


def set_internal_user_info(pubkey, **kwargs):
    """Add optional details in local user info."""
    kwargs['pubkey'] = pubkey
    with SQL_CONNECTION() as sql:
        sql.execute("INSERT INTO internal_user_infos ({}) VALUES ({})".format(
            ', '.join(kwargs.keys()), ', '.join(['%s' for key in kwargs])), (list(kwargs.values())))
    if kwargs.get('full_name') and kwargs.get('phone_number') and kwargs.get('address'):
        basic_kyc_result = kyc.basic_kyc(kwargs.get('full_name'), kwargs.get('address'), kwargs.get('phone_number'))
        update_test(pubkey, 'basic', basic_kyc_result)
    return get_user_infos(pubkey)


def get_monthly_allowance(pubkey):
    """Get a user's monthly allowance."""
    return BASIC_MONTHLY_ALLOWANCE if get_test_result(pubkey, 'basic') > 0 else 0


def get_monthly_expanses(pubkey):
    """Get a user's expanses in the last month."""
    with SQL_CONNECTION() as sql:
        sql.execute("""
            SELECT SUM(euro_cents) FROM purchases
            WHERE user_pubkey = %s AND timestamp > %s AND paid > 0""", (
                pubkey, time.time() - (30 * 24 * 60 * 60)))
        try:
            return sql.fetchall()[0].items()[0] or 0
        except TypeError:
            return 0


def get_users():
    """Get list of users and their details - for debug only."""
    with SQL_CONNECTION() as sql:
        sql.execute('SELECT * FROM users')
        return {user['call_sign']: dict(
            get_user_infos(user['pubkey']),
            monthly_allowance=get_monthly_allowance(user['pubkey']),
            monthly_expanses=get_monthly_expanses(user['pubkey'])
        ) for user in sql.fetchall()}


def get_payment_address(user_pubkey, euro_cents, payment_currency, requested_currency):
    """Get an address to pay for a purchase."""
    assert payment_currency in ['BTC', 'ETH'], 'payment_currency must be BTC or ETH'
    assert requested_currency in ['BUL', 'XLM'], 'requested_currency must be BUL or XLM'
    remaining_monthly_allowance = get_monthly_allowance(user_pubkey) - get_monthly_expanses(user_pubkey)
    assert remaining_monthly_allowance >= int(euro_cents), \
        "{} is allowed to purchase up to {} euro-cents when {} are required".format(
            user_pubkey, remaining_monthly_allowance, euro_cents)

    payment_pubkey = pywallet.wallet.create_address(network=payment_currency+'test', xpub=XPUB)['address']
    with SQL_CONNECTION() as sql:
        sql.execute(
            """INSERT INTO purchases (user_pubkey, payment_pubkey, payment_currency, euro_cents, requested_currency)
            VALUES (%s, %s, %s, %s, %s)""",
            (user_pubkey, payment_pubkey, payment_currency, euro_cents, requested_currency))
    return payment_pubkey


def get_unpaid():
    """Get all unpaid addresses."""
    with SQL_CONNECTION() as sql:
        sql.execute('SELECT * FROM purchases WHERE paid = 0')
        return sql.fetchall()


def get_paid():
    """Get all paid addresses."""
    with SQL_CONNECTION() as sql:
        sql.execute('SELECT * FROM purchases WHERE paid = 1')
        return sql.fetchall()


def create_purchase(user_pubkey, payment_pubkey, payment_currency, requested_currency, euro_cents):
    """Create new purchase for user"""
    with SQL_CONNECTION() as sql:
        sql.execute("""
            INSERT INTO purchases (user_pubkey, payment_pubkey, payment_currency, requested_currency, euro_cents)
            VALUES (%s, %s, %s, %s, %s)""",
                    (user_pubkey, payment_pubkey, payment_currency, requested_currency, euro_cents))


def update_purchase(payment_pubkey, paid_status):
    """Update purchase status"""
    with SQL_CONNECTION() as sql:
        sql.execute("UPDATE purchases SET paid = %s WHERE payment_pubkey = %s", (paid_status, payment_pubkey))
