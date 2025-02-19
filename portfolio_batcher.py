import requests
import rookiepy
import json
import sys
import time
import lxml.html
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

class PortfolioGrouper:
    def __init__(self):
        self.groups = {}
        self.recorder = {}
        self.skipped = {}

    def title(self, name, id):
        self.groups.update({name: [id]})

    def add(self, name, stock):
        stock = stock.upper()
        self.groups[name].append(stock)
        pos = (name, len(self.groups[name])-1)
        try:
            self.recorder[stock].append(pos)
        except KeyError:
            self.recorder.update({stock: [pos]})

    def check(self, stock):
        stock = stock.upper()
        if stock in self.recorder:
            return self.recorder[stock]
        elif 'SPECIAL_ALL' in self.recorder:
            self.add('all', stock)
            return self.recorder[stock]
        else:
            return None
        
    def assign(self, indexes, exchange, shares, cost):
        for name, index in indexes:
            stock = self.groups[name][index]
            stock = StockData(stock, exchange, shares, cost)
            self.groups[name][index] = stock

    def skipping(self, stock, reason):
        if stock not in self.skipped:
            self.skipped.update({stock: reason})

    def release(self):
        clean_groups = []
        for g, group in enumerate(self.groups.values()):
            clean_groups.append([group[0]])
            for stock in group[1:]:
                if isinstance(stock, StockData):
                    clean_groups[g].append(stock)
                else:
                    self.skipping(stock, 'stock is not in portfolio')
        return clean_groups
    
    def debug_print(self):
        for tag in ('groups', 'recorder', 'skipped'):
            print(tag+':')
            for d, v in eval('self.'+tag+'.items()'):
                print(d+' -- '+str(v))

@dataclass
class StockData:
    ticker: str
    exchange: str
    shares: str
    cost: str

    def __post_init__(self):
        if self.exchange == 'ARCA':
            self.exchange = 'NYSEARCA'

        if self.exchange == 'PINK':
            self.exchange = 'OTCMKTS'

        if self.exchange == 'AMEX':
            self.exchange = 'NYSEAMERICAN'
        
        if ' ' in self.ticker:
            self.ticker = self.ticker.replace(' ', '.')

def move_decimal(number):
    n = Decimal(str(number))
    d = n.as_tuple().exponent
    if d == 0:
        return [int(n), None]
    else:
        d = abs(d)
        return [int(n.shift(d)), d]
    
def callback_json(page_tree):
    search_string = ['AF_initDataCallback', "'ds:4'"]
    p = page_tree.xpath('//script[@nonce][contains(text(),"{0[0]}") and contains(text(),"key: {0[1]}")]/text()'.format(search_string))[0][20:-2]

    b = 0
    for s in ('key:', '+', 'hash:', '+', '-', 'data:', 'sideChannel:'):
        if s == '+':
            f = p.find('\'', b)
            b = p.find('\'', f+1)
            p = p[:f] + '\"' + p[f+1:b] + '\"' + p[b+1:]
        elif s == '-':
            b = -1
        else:
            n = '"'+s[:-1]+'":'
            p = p.replace(s,n)
            if b > 0:
                b = p.find(n, b) + len(n)
    print(p)
    return json.loads(p)
    
def get_chain_unix(page_tree):
    p = callback_json(page_tree)
    return [p['data'][0][0][5][0], p['data'][0][0][5][1]]

def get_delete_codes(page_tree):
    codes = []
    p = callback_json(page_tree)
    try:
        for stock in p['data'][0][0][6][0]:
            codes.append((stock[1][0][0][0][0], stock[0]))
    
    except IndexError:
        pass

    finally:
        return codes

path_to_portfolio = ''
rapt = ''

ibkr_url = 'https://localhost:5000/v1/api/portfolio'
google_url = 'https://www.google.com/finance'
portfolio_path = '/finance/portfolio/'
batch_path = google_url+'/_/GoogleFinanceUi/data/batchexecute'
retry_max = 2
retry = retry_max
wait = 1

finance_cookie = []
cookies = rookiepy.edge(['google.com'])
# cookies = rookiepy.chrome(['google.com'])
for cookie in cookies:
    if cookie['domain'] == '.google.com':
        finance_cookie.append(cookie['name']+'='+cookie['value'])
finance_cookie = '; '.join(finance_cookie)

while True:
    response = requests.get(ibkr_url+'/accounts', verify=False)
    time.sleep(wait)
    try:
        if response.status_code == requests.codes.ok:
            ibkr_id = response.json()
            ibkr_id = ibkr_id[0]['id']
        else:
            response.raise_for_status()

    except (KeyError, requests.exceptions.JSONDecodeError):
        retry -= 1
        if retry < 0:
            print('FAILED AT ACCOUNT START')
            sys.exit()
        else:
            print('LIKELY GETTING OLD REQUEST AT START')

    except requests.exceptions.HTTPError as e:
        print('HTTP ERROR AT ACCOUNT START')
        print(e)
        sys.exit()

    else:
        retry = retry_max
        break

portfolio_groups = PortfolioGrouper()
path = Path.cwd() / path_to_portfolio
with path.open() as f:
    mode = 'start'
    for line in f:
        if mode == 'read':
            if line[0] == '|':
                mode = 'title'
            else:
                line += ' random'
                line, _ = line.split(maxsplit=1)
                portfolio_groups.add(p_name, line.replace('.', ' '))
        elif mode == 'start':
            if line[0] == '|':
                mode = 'title'
        elif mode == 'title':
            if line[0] == '-':
                mode = 'end'
            else:
                line = line.strip('|\n\r')
                p_name, _, p_id = line.partition(' ')
                f.readline()
                f.readline()
                portfolio_groups.title(p_name, p_id)
                mode = 'read'
        else:
            break

positions_url = ibkr_url+'/'+ibkr_id+'/positions/'
page = 0
past_stock = None
while True:
    response = requests.get(positions_url+str(page), verify=False)
    time.sleep(wait)
    try:
        if response.status_code == requests.codes.ok:
            position_page = response.json()
            first_stock = position_page[0]['ticker']
            if past_stock is not None:
                assert first_stock != past_stock
            
            for position in position_page:
                if position['assetClass']=='STK' and position['listingExchange']!='VALUE':
                    if 'ticker' in position:
                        indexes = portfolio_groups.check(position['ticker'])
                        if indexes is not None:
                            portfolio_groups.assign(indexes,
                                                    position['listingExchange'],
                                                    position['position'],
                                                    position['avgCost']
                                                    )
                    else:
                        portfolio_groups.skipping(position['contractDesc'], 'stock data is incomplete')
            past_stock = first_stock
            page += 1
        else:
            response.raise_for_status()

    except (KeyError, requests.exceptions.JSONDecodeError, AssertionError) as e:
        retry -= 1
        if retry < 0:
            print('FAILED AT PORTFOLIO PAGINATION '+str(page))
            sys.exit()
        else:
            print('LIKELY GETTING OLD REQUEST AT PAGINATION')
            print(e)

    except requests.exceptions.HTTPError as e:
        print('HTTP ERROR AT PORTFOLIO PAGINATION '+str(page))
        print(e)
        sys.exit()

    except IndexError:
        break

orders = portfolio_groups.release()
portfolio_groups.debug_print()
# rapt = WIZ_global_data['Dbw5Ud']
# some process to get rapt?

headers = {'Cookie': finance_cookie}
first_portfolio = orders[0][0]
response = requests.get(google_url+'/portfolio/'+first_portfolio+'?rapt='+rapt, headers=headers)
page_tree = lxml.html.fromstring(response.content)
WIZ_global_data = page_tree.xpath('//script[@data-id="_gd"]/text()')[0].lstrip('window.')[:-1]
exec('false=False\ntrue=True\n'+WIZ_global_data)
# for k,v in WIZ_global_data.items():
#     print(k+' = '+str(v))

bl = WIZ_global_data['cfb2h']
at = WIZ_global_data['SNlM0e']
sid = WIZ_global_data['FdrFJe']
reqid = datetime.now()
reqid = 1 + (3600 * reqid.hour + 60 * reqid.minute + reqid.second)
chain_unix = get_chain_unix(page_tree)
payload = {'hl': 'en', 'rt': 'c', 'bl': bl, 'rapt': rapt, 'at': at}
# test1 = 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
# orders = [['xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', test1, ['aapl', 'nasdaq', '5.5', '10.5'], ['spy', 'NYSEARCA', '20.00', '20']]]

for order in orders:
    portfolio = order[0]
    if portfolio != first_portfolio:
        print('next port')
        response = requests.get(google_url+'/portfolio/'+portfolio+'?rapt='+rapt, headers=headers)
        page_tree = lxml.html.fromstring(response.content)
        chain_unix = get_chain_unix(page_tree)
    batch_oders = get_delete_codes(page_tree)
    batch_oders.extend(order[1:])
    response = None

    for batch in batch_oders:
        print(batch)
        try:
            response = response.text
            chain_unix, _, sid = response.rpartition('"af.httprm",')
            chain_unix, _, _ = chain_unix.rpartition(']]",')
            chain_unix = chain_unix[chain_unix.rfind('[')+1:]
            chain_unix = [int(s) for s in chain_unix.split(',')]
            _, sid, _ = sid.split('"', 2)
            reqid += 100000
        except AttributeError:
            print('lol')
            pass

        if isinstance(batch, StockData):
            rpc = 'HFvyHc'
            ticker = batch.ticker.upper()
            exchange = batch.exchange.upper()
            shares = move_decimal(batch.shares)
            cost = move_decimal(batch.cost)
            # batch_body = [portfolio, None, None, [[None, shares, cost, None, None, None, []]], [None, [ticker, exchange]], chain_unix]
            batch_body = [portfolio, None, None, [[None, shares, cost]], [None, [ticker, exchange]], chain_unix]
        else:
            rpc = 'FZQ3s'
            batch_body = [portfolio, batch[0], batch[1], chain_unix]
        
        req_body = [[[rpc, json.dumps(batch_body, separators=(',', ':')), None, 'generic']]]
        req_payload = {'rpcids': rpc,
                       '_reqid': reqid,
                       'source-path': portfolio_path+portfolio,
                       'f.req': json.dumps(req_body, separators=(',', ':')),
                       'f.sid': sid,
                       }
        payload.update(req_payload)

        response = requests.post(batch_path, headers=headers, params=payload)
        print('-\n----\n')
        print(payload)
        print('-\n----\n')
        print(response.text)

portfolio_groups.debug_print()
print(orders)
        