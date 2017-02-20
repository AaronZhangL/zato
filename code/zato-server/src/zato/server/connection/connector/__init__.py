# -*- coding: utf-8 -*-

"""
Copyright (C) 2016 Dariusz Suchojad <dsuch at zato.io>

Licensed under LGPLv3, see LICENSE.txt for terms and conditions.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

# stdlib
from datetime import datetime
from logging import getLogger
from traceback import format_exc

# gevent
from gevent import sleep, spawn
from gevent.lock import RLock

# Zato
from zato.common.util import spawn_greenlet

# ################################################################################################################################

logger = getLogger(__name__)

# ################################################################################################################################

class connector_type:
    """ All types of ZeroMQ connections that we support.
    """
    class channel:
        zmq = 'ZeroMQ channel'

    class out:
        vault_conn = 'Vault connection'
        zmq = 'ZeroMQ outgoing'

    class duplex:
        amqp = 'AMQP'
        web_socket = 'WebSocket'
        zmq_v01 = 'ZeroMQ MDP v0.1'

class Inactive(Exception):
    pass

# ################################################################################################################################

class EventLogger(object):
    def __init__(self, enter_verb, exit_verb, enter_func, exit_func):

        self.enter_verb = enter_verb
        self.exit_verb = exit_verb

        self.enter_func = enter_func
        self.exit_func = exit_func

    def __enter__(self):
        self.enter_func(self.enter_verb)

    def __exit__(self, *args, **kwargs):
        self.exit_func(self.exit_verb)

# ################################################################################################################################

class Connector(object):

    # Whether that connector's start method should be called in its own greenlet
    start_in_greenlet = False

    def __init__(self, name, type, config, on_message_callback=None, auth_func=None):
        self.name = name
        self.type = type
        self.config = config
        self.on_message_callback = on_message_callback # Invoked by channels for each message received
        self.auth_func = auth_func # Invoked by channels that need to authenticate users
        self.service = config.get('service_name') # Service to invoke by channels for each message received

        self.id = self.config.id
        self.is_active = self.config.is_active
        self.is_inactive = not self.is_active
        self.is_connected = False

        self.keep_connecting = True
        self.keep_running = False
        self.lock = RLock()

        # May be provided by subclasses
        self.conn = None

# ################################################################################################################################

    def get_log_details(self):
        """ Can be overridden in subclasses.
        """
        return ''

    get_prev_log_details = get_log_details

# ################################################################################################################################

    def _start_loop(self):
        """ Establishes a connection to the external resource in a loop that keeps running as long as self.is_connected is False.
        The flag must be set to True in a subclass's self._start method.
        """
        attempts = 0
        log_each = 10
        start = datetime.utcnow()

        try:
            while self.keep_connecting:
                while not self.is_connected:
                    try:
                        self._start()
                    except Exception, e:
                        logger.warn(format_exc(e))
                        sleep(2)

                    # We go here if ._start did not set self.is_conneted to True
                    attempts += 1
                    if attempts % log_each == 0:
                        logger.warn('Could not connect to %s (%s) after %s attempts, time spent so far: %s',
                            self.get_log_details(), self.name, attempts, datetime.utcnow() - start)

                # Ok, break from the outermost loop
                self.keep_connecting = False

        except KeyboardInterrupt:
            self.keep_connecting = False

# ################################################################################################################################

    def _start(self):
        raise NotImplementedError('Must be implemented in subclasses')

# ################################################################################################################################

    def _send(self):
        raise NotImplementedError('Must be implemented in subclasses')

# ################################################################################################################################

    def send(self, msg, *args, **kwargs):
        with self.lock:
            if self.is_inactive:
                raise Inactive('Connection `{}` is inactive ({})'.format(self.name, self.type))
            self._send(msg, *args, **kwargs)

# ################################################################################################################################

    def _start_stop_logger(self, enter_verb, exit_verb):
        return EventLogger(enter_verb, exit_verb, self._debug_start_stop, self._info_start_stop)

    def _debug_start_stop(self, verb):
        logger.debug('%s %s connector `%s`', verb, self.type, self.name)

    def _info_start_stop(self, verb):
        log_details = self.get_prev_log_details() if 'Stop' in verb else self.get_log_details()
        logger.info('%s %s connector `%s`%s', verb, self.type, self.name, ' ({})'.format(log_details) if log_details else '')

# ################################################################################################################################

    def _spawn_start(self):
        spawn(self._start_loop).get()

# ################################################################################################################################

    def start(self, needs_log=True):
        with self._start_stop_logger('Starting',' Started'):
            self.keep_running = True

            try:
                if self.start_in_greenlet:
                    spawn_greenlet(self._spawn_start)
                else:
                    self._start_loop()
            except Exception, e:
                logger.warn(format_exc(e))

# ################################################################################################################################

    def stop(self):
        with self._start_stop_logger('Stopping',' Stopped'):
            self.keep_connecting = False # Set to False in case .stop is called before the connection was established
            self.keep_running = False
            self._stop()

# ################################################################################################################################

    def restart(self):
        """ Stops and starts the connector, must be called with self.lock held.
        """
        self.stop()
        self.start()

# ################################################################################################################################

    def edit(self, old_name, config):
        with self.lock:
            config.prev_address = self.config.address
            self._edit(old_name, config)
            self.restart()

# ################################################################################################################################

    def _edit(self, old_name, config):
        self.name = config.name
        self.config = config

# ################################################################################################################################

    def _stop(self):
        """ Can be, but does not have to, overwritten by subclasses to customize the behaviour.
        """

# ################################################################################################################################

class ConnectorStore(object):
    """ Base container for all connectors.
    """
    def __init__(self, type, connector_class):
        self.type = type
        self.connector_class = connector_class
        self.connectors = {}
        self.lock = RLock()

    def create(self, name, config, on_message_callback=None, auth_func=None):
        with self.lock:
            self.connectors[name] = self.connector_class(name, self.type, config, on_message_callback, auth_func)

    def edit(self, old_name, config, *ignored_args):
        with self.lock:
            self.connectors[old_name].edit(old_name, config)
            self.connectors[config.name] =  self.connectors.pop(old_name)

    def delete(self, name):
        with self.lock:
            self.connectors[name].stop()
            del self.connectors[name]

    def start(self, name=None):
        with self.lock:
            for c in self.connectors.values():

                # Perhaps we want to start a single connector so we need to filter out the other ones
                if name and name != c.name:
                    continue

                c.start()

    def invoke(self, name, *args, **kwargs):
        return self.connectors[name].invoke(*args, **kwargs)

# ################################################################################################################################
