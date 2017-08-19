# -*- coding: utf-8 -*-

"""
Copyright (C) 2017 Dariusz Suchojad <dsuch at zato.io>

Licensed under LGPLv3, see LICENSE.txt for terms and conditions.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

# stdlib
import os
from copy import deepcopy

from zato.cli import common_logging_conf_contents, common_odb_opts, ZatoCommand
from zato.common.markov_passwords import generate_password
from zato.common.util import encrypt

config_template = """[bind]
host=0.0.0.0
port=47120

[cluster]
id={cluster_id}
stats_enabled=True

[odb]
engine={odb_engine}
db_name={odb_db_name}
host={odb_host}
port={odb_port}
username={odb_username}
password={odb_password}
pool_size=1
extra=
use_async_driver=True
is_active=True

[broker]
host={broker_host}
port={broker_port}
password={broker_password}
db=0

[crypto]
use_tls=True
tls_protocol=TLSv1
tls_ciphers=EECDH+AES:EDH+AES:-SHA1:EECDH+RC4:EDH+RC4:RC4-SHA:EECDH+AES256:EDH+AES256:AES256-SHA:!aNULL:!eNULL:!EXP:!LOW:!MD5
tls_client_certs=optional
priv_key_location=zato-connector-wmq-priv-key.pem
pub_key_location=zato-connector-wmq-pub-key.pem
cert_location=zato-connector-wmq-cert.pem
ca_certs_location=zato-connector-wmq-ca-certs.pem

[api_users]
user1={user1_password}
"""

class Create(ZatoCommand):
    """ Creates a new WebSpshere MQ connector instance.
    """
    needs_empty_dir = True
    allow_empty_secrets = True

    opts = deepcopy(common_odb_opts)

    opts.append({'name':'pub_key_path', 'help':"Path to connector's public key in PEM"})
    opts.append({'name':'priv_key_path', 'help':"Path to connector's private key in PEM"})
    opts.append({'name':'cert_path', 'help':"Path to connector's certificate in PEM"})
    opts.append({'name':'ca_certs_path', 'help':"Path to a bundle of CA certificates to be trusted"})
    opts.append({'name':'cluster_id', 'help':"ID of the cluster this connector will belong to"})

    def __init__(self, args):
        self.target_dir = os.path.abspath(args.path)
        super(Create, self).__init__(args)

    def execute(self, args, show_output=True, password=None, needs_created_flag=False):
        os.chdir(self.target_dir)

        repo_dir = os.path.join(self.target_dir, 'config', 'repo')
        conf_path = os.path.join(repo_dir, 'connector-wmq.conf')

        os.mkdir(os.path.join(self.target_dir, 'logs'))
        os.mkdir(os.path.join(self.target_dir, 'config'))
        os.mkdir(repo_dir)

        self.copy_connector_wmq_crypto(repo_dir, args)
        priv_key = open(os.path.join(repo_dir, 'zato-connector-wmq-priv-key.pem')).read()

        config = {
            'odb_db_name': args.odb_db_name or args.sqlite_path,
            'odb_engine': args.odb_type,
            'odb_host': args.odb_host or '',
            'odb_port': args.odb_port or '',
            'odb_password': encrypt(args.odb_password, priv_key) if args.odb_password else '',
            'odb_username': args.odb_user or '',
            'broker_host': args.kvdb_host,
            'broker_port': args.kvdb_port,
            'broker_password': encrypt(args.kvdb_password, priv_key) if args.kvdb_password else '',
            'user1_password': generate_password(),
            'cluster_id': args.cluster_id,
        }

        open(os.path.join(repo_dir, 'logging.conf'), 'w').write(
            common_logging_conf_contents.format(log_path='./logs/connector-wmq.log'))
        open(conf_path, 'w').write(config_template.format(**config))

        # Initial info
        self.store_initial_info(self.target_dir, self.COMPONENTS.CONNECTOR_WMQ.code)

        if show_output:
            if self.verbose:
                msg = """Successfully created a WebSpshere MQ instance.
    You can start it with the 'zato start {path}' command.""".format(
                path=os.path.abspath(os.path.join(os.getcwd(), self.target_dir)))
                self.logger.debug(msg)
            else:
                self.logger.info('OK')

        # We return it only when told to explicitly so when the command runs from CLI
        # it doesn't return a non-zero exit code.
        if needs_created_flag:
            return True
