#!/usr/bin/python
# -*- coding: utf-8 -*-
#
from __future__ import unicode_literals
import os
import sys
import time
import json
import requests
import argparse
import traceback
import simplejson as json
import multiprocessing

requests.adapters.DEFAULT_RETRIES = 3

def token_str2_float(token):
    return float(token.split(" ")[0])

def get_onchain_balance(account_name, node_host):
        body = {'scope':account_name, 'code':'eosio.token', 'table':'accounts', 'json':True}
        ret = requests.post("http://%s/v1/chain/get_table_rows" % node_host, data=json.dumps(body), timeout=2)
        if ret.status_code/100 != 2:
            print 'ERROR: failed to call get_table_rows accounts for:', account_name, ret.text
            return -1
        balance_info = json.loads(ret.text)
        balance = 0.0
        if balance_info['rows']:
            # {"rows":[{"balance":"804148103.4130 SYS"}],"more":false}
            balance = token_str2_float(balance_info['rows'][0]['balance'])
        return balance

def check_balance_signal_account(param):
    node_host, account_name, pub_key, snapshot_balance = param
    signal_onchain_amount = -1
    try:
        # call get account
        ret = requests.post("http://%s/v1/chain/get_account" % node_host, data=json.dumps({'account_name':account_name}), timeout=2)
        if ret.status_code/100 != 2:
            print 'ERROR: failed to call get_account for:', account_name, ret.text
            return signal_onchain_amount
        account_info = json.loads(ret.text)
        owner_pubkey = account_info['permissions'][0]['required_auth']['keys'][0]['key']

        # Validate the public key onchain whether same with the snapshot
        if pub_key != owner_pubkey:
            print 'ERROR: account %s snapshot pubkey(%s)!=onchain pubkey(%s)' % (account_name, pub_key, owner_pubkey)
            return signal_onchain_amount
        net_delegated, cpu_delegated = 0, 0
        if account_info['delegated_bandwidth']:
            net_delegated, cpu_delegated = token_str2_float(account_info['delegated_bandwidth']['net_weight']), token_str2_float(account_info['delegated_bandwidth']['cpu_weight'])

        # call get account balance
        balance = get_onchain_balance(account_name, node_host)
        if balance < 0:
            print 'ERROR: failed to call get_table_rows accounts for:', account_name, ret.text
            return signal_onchain_amount

        # Validate the balance onchain whether same with the snapshot amount
        onchain_balance = balance + net_delegated + cpu_delegated
        if abs(snapshot_balance - onchain_balance) > 0.0001:
            print 'ERROR: account %s snapshot_balance(%f) != onchain_balance(%f)' % (account_name, snapshot_balance, onchain_balance)
            return signal_onchain_amount
        #print '%s balance:%f net_delegated:%f cpu_delegated:%f onchain_balance:%f snapshot_balance:%f' % (account_name, balance, net_delegated, cpu_delegated, onchain_balance, snapshot_balance)
        signal_onchain_amount = onchain_balance
        return signal_onchain_amount
    except Exception as e:
        print 'check_balance_signal_account get exception:', e
        print traceback.print_exc()
        return signal_onchain_amount

def check_balance(conf_dict, process_pool, cpu_count):
    node_host, snapshot_csv = conf_dict['nodeosd_host'], conf_dict['snapshot_csv']
    EOS_TOTAL = 1000000000.0000
    account_onchain_balance_total, batch_size = 0.0, cpu_count*100
    try:
        with open(snapshot_csv, 'r') as fp:
            batch_lines, cur_len, line_nu = [None] * batch_size, 0, 0
            for line in fp.readlines():
                _, account_name, pub_key, snapshot_balance = line.replace('"','').split(',')
                batch_lines[cur_len] = (node_host, account_name, pub_key, float(snapshot_balance))
                cur_len += 1
                line_nu += 1
                if cur_len<batch_size:
                    continue
                results = process_pool.map(check_balance_signal_account, batch_lines, cpu_count)
                for signal_onchain_amount in results:
                    if signal_onchain_amount < 0:
                        return False
                    account_onchain_balance_total += signal_onchain_amount
                print 'check progress, line number:', line_nu
                batch_lines, cur_len = [None] * batch_size, 0

            if cur_len>0:
                results = process_pool.map(check_balance_signal_account, batch_lines[:cur_len], cpu_count)
                for signal_onchain_amount in results:
                    if signal_onchain_amount < 0:
                        return False
                    account_onchain_balance_total += signal_onchain_amount
            eosio_onchain_balance = get_onchain_balance('eosio', node_host)
            print 'eosio_onchain_balance:', eosio_onchain_balance, ' account_onchain_balance_total:', account_onchain_balance_total
            if abs(EOS_TOTAL - eosio_onchain_balance - account_onchain_balance_total) > 0.0001:
                print 'ERROR: There are some illegal transfer token action EOS_TOTAL(%f) != onchain_total(%f)' % (EOS_TOTAL, (eosio_onchain_balance + account_onchain_balance_total))
                return False
            return True
    except Exception as e:
        print 'EXCEPTION: there are exception:', e
        print traceback.print_exc()
        return False
        
def check_contract():
    pass

def main():
    parser = argparse.ArgumentParser(description='EOSIO onchain validator tool.')
    parser.add_argument('--action', type=str, required=True, help='contract_validate|chain_validate')
    parser.add_argument('--config', type=str, required=True, help='validator.json config file path')
    args = parser.parse_args()
    action, conf_file = args.action, os.path.abspath(os.path.expanduser(args.config))
    if action not in ('contract_validate', 'chain_validate'):
        print 'ERROR: action should be one of contract_validate|chain_validate'
        sys.exit(1)
    if not os.path.isfile(conf_file):
        print 'ERROR: validator config file not exist:',conf_file
        sys.exit(1)
    conf_dict = None
    with open(conf_file, 'r') as fp:
        conf_dict = json.loads(fp.read())
    if not conf_dict:
        print 'ERROR: validator config can not be empty:',conf_file
        sys.exit(1)

    cpu_count = multiprocessing.cpu_count()
    process_pool = multiprocessing.Pool(processes=cpu_count)
    try:
        if action == 'contract_validate':
            if not check_contract():
                print 'ERROR: !!! The Contract Check FAILED !!!'
                sys.exit(1)
            else:
                print 'SUCCESS: !!! The Contract Check SUCCESS !!!'
                sys.exit(1)
        if action == 'chain_validate':
            TEST_TIME, i = 100, 0
            time_start = time.time()
            while i < TEST_TIME:
                i += 1
                if not check_balance(conf_dict, process_pool, cpu_count):
                    print 'ERROR: !!! The Balance Onchain Check FAILED !!!'
                    sys.exit(1)
                else:
                    print 'SUCCESS: !!! The Balance Onchain Check SUCCESS !!!', i
            time_usage = time.time()-time_start
            print 'TESTING TIME USAGE:%fs, %f/s accounts' % (time_usage, TEST_TIME*5/time_usage)
    except Exception as e:
        print action, ' get exception:', e
        print traceback.print_exc()
    finally:
        process_pool.close()
        process_pool.join()

if __name__ == '__main__':
    main()