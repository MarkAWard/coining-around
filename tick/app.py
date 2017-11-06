import atexit
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from functools import partial
import logging
import os
from pytz import utc
import pickle

from flask import Flask
from flask import jsonify, request

from apscheduler.schedulers.background import BackgroundScheduler
import gdax
from influxdb import InfluxDBClient

app = Flask(__name__)
logging.getLogger()
scheduler = BackgroundScheduler(daemon=True, timezone=utc)
scheduler.start()
atexit.register(lambda: scheduler.shutdown(wait=False))

INTERVAL = 1 # minute
GRANULARITY = 10 # seconds
LAST_TRADE_FILE = 'last_trade-{}.id'
FIELDS = ['time', 'low', 'high', 'open', 'close', 'volume']
MEASURE = {
    "measurement": "rates",
    "tags": {
        "prod_id": None
    },
    "time": None,
    "fields": None
}


######### REAL TIME TRADES ###################

def get_trades(prod_id):
    filename = LAST_TRADE_FILE.format(prod_id)
    if os.path.isfile(filename):
        with open(filename, 'r') as fp:
            LAST_TRADE_ID = int(fp.read())
    else:
        LAST_TRADE_ID = -1

    client = gdax.PublicClient()
    trades = client.get_product_trades(prod_id)
    trades = filter(lambda t: t['trade_id'] > LAST_TRADE_ID, trades)
    trades = map(to_float, trades)
    for trade in trades:
        trade.update({'prod_id': prod_id})

    trade_ids = [t['trade_id'] for t in trades]
    if trade_ids:
        with open(filename, 'w') as fp:
            LAST_TRADE_ID = max(trade_ids)
            fp.write(str(LAST_TRADE_ID))

    return trades

def to_float(trade):
    fields = {'price', 'size'}
    return dict([[k, float(v)] if k in fields else [k, v] for k, v in trade.items()])
 
@app.route('/feed/')
def trades():
    prod_id = request.values['prod_id']
    trades = get_trades(prod_id)
    return jsonify(trades)

######### END REAL TIME TRADES ###############

######### BUCKETED PRODUCT RATES #############

def format_rate(rate, prod_id=None):
    if prod_id is None:
        raise Exception()
    data = deepcopy(MEASURE)
    data['tags']['prod_id'] = prod_id
    data['time'] = datetime.fromtimestamp(rate.pop('time'))
    data['fields'] = {k: float(v) for k, v in rate.items()}
    return data

def get_rates(prod_id, start, end, granularity=GRANULARITY):
    public_client = gdax.PublicClient()
    rates = public_client.get_product_historic_rates(
        prod_id, start=start, end=end, granularity=GRANULARITY)
    rates = map(lambda r: dict(zip(FIELDS, r)), rates)
    data = map(partial(format_rate, prod_id=prod_id), rates)

    db = InfluxDBClient('influxdb', 8086, 'root', 'root', 'rates')
    if 'rates' not in set(d['name'] for d in db.get_list_database()):
        db.create_database('rates')
    db.write_points(data)

def historical_rates(prod_id):
    end = datetime.now(utc)
    start = end - timedelta(minutes=INTERVAL)
    get_rates(prod_id, start, end)

btc_rates = partial(historical_rates, prod_id='BTC-USD')
eth_rates = partial(historical_rates, prod_id='ETH-USD')
ltc_rates = partial(historical_rates, prod_id='LTC-USD')

scheduler.add_job(btc_rates, 'interval', minutes=INTERVAL, id='btc_rates')
scheduler.add_job(eth_rates, 'interval', minutes=INTERVAL, id='eth_rates')
scheduler.add_job(ltc_rates, 'interval', minutes=INTERVAL, id='ltc_rates')

######### END BUCKETED PRODUCT RATES #########

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9999, debug=True, threaded=True)
