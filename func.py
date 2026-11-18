import os
import time
import hmac
import hashlib
import base64
import ast
import requests
import datetime
import pandas as pd
from dotenv import load_dotenv
from collections.abc import Mapping, Sequence
import warnings
warnings.filterwarnings("ignore")

load_dotenv()


class CMC:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://pro-api.coinmarketcap.com"
        self.map_url = f"{self.base_url}/v1/cryptocurrency/map"
        self.pairs_url = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/market-pairs/latest"
        self.map_headers = {
            "Accepts": "application/json",
            "X-CMC_PRO_API_KEY": self.api_key
        }
        self.pairs_headers = {
            "Accept": "application/json",
            "User-Agent": "MyCMCClient/1.0"
        }

    def get_slug(self) -> pd.DataFrame:
        """Get slug for a coin symbol (lowest ranked if duplicates)."""
        all_coins, start, limit = [], 1, 5000

        while True:
            resp = requests.get(self.map_url, headers=self.map_headers,
                                params={"start": start, "limit": limit, "listing_status": "active"}, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                break
            all_coins.extend(data)
            start += limit

        # df = pd.DataFrame(all_coins).sort_values(by="rank", ascending=True)
        # matches = df[df["symbol"].str.lower() == symbol.lower()]

        # if matches.empty:
        #     raise ValueError(f"Symbol '{symbol}' not found in CoinMarketCap map.")

        # selected = matches.iloc[0]
        # if len(matches) > 1:
        #     print(f"Multiple matches for '{symbol}':\n{matches[['id', 'name', 'slug', 'rank']]}")
        #     print(f"Chosen slug: {selected['slug']} (rank {selected['rank']})")
        # return selected["slug"]
        return pd.DataFrame(all_coins, columns=["symbol", "slug"])

    def fetch_perpetual_pairs(self, slug: str) -> pd.DataFrame:
        """Fetch perpetual market pairs for a given slug."""
        all_pairs, start, limit = [], 1, 10

        while True:
            resp = requests.get(self.pairs_url, headers=self.pairs_headers, params={
                "slug": slug, "start": start, "limit": limit,
                "category": "perpetual", "centerType": "cex",
                "sort": "cmc_rank_advanced", "direction": "desc",
                "spotUntracked": "true"
            }, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json().get("data", {}).get("marketPairs", [])
            if not data:
                break
            all_pairs.extend(data)
            start += limit

        res = pd.json_normalize(all_pairs)
        if res.empty:
            print("Empty DataFrame")
            return res

        res.insert(3, 'asset', res.pop('baseSymbol'))
        res.insert(4, 'category', res.pop('category'))
        res.insert(5, 'funding', res.pop('fundingRate'))
        res.insert(6, 'funding_APR_4hbase', res['funding'] * 6 * 365)

        return res



class KCN:
    def __init__(self):
        self.key = os.getenv("KUCOIN_API_KEY")
        self.secret = os.getenv("KUCOIN_API_SECRET")
        self.passphrase = os.getenv("KUCOIN_PASSPHRASE")
        self.base_url = "https://api.kucoin.com"

    def _auth_headers(self, endpoint):
        if not all((self.key, self.secret, self.passphrase)):
            raise ValueError("Set the KuCoin API credentials in .env to enable this source.")
        ts = str(int(time.time() * 1000))
        prehash = f"{ts}GET{endpoint}"
        sign = base64.b64encode(hmac.new(self.secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
        pass_enc = base64.b64encode(hmac.new(self.secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()).decode()

        return {
            "KC-API-KEY": self.key,
            "KC-API-SIGN": sign,
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": pass_enc,
            "KC-API-KEY-VERSION": "2"
        }

    def fixed_earn(self) -> pd.DataFrame:
        """Retrieve KuCoin fixed earn (promotion) products."""
        endpoint = "/api/v1/earn/promotion/products"
        headers = self._auth_headers(endpoint)
        r = requests.get(self.base_url + endpoint, headers=headers, timeout=10)
        if r.status_code == 401:
            raise ValueError("KuCoin rejected the supplied API credentials.")
        
        res = pd.json_normalize(r.json().get("data", []))

        res.insert(1, 'asset', res.pop('incomeCurrency'))
        res.insert(2, 'APR', res.pop('returnRate'))
        res['APR'] = res['APR'].astype(float)
        res.insert(3, 'start_time', pd.to_datetime(res['lockStartTime']/1000, unit='s'))
        res.insert(4, 'end_time', pd.to_datetime(res['lockEndTime']/1000, unit='s'))
        res.insert(3, 'lock_up_period', res.pop('duration'))
        res.insert(6, 'status', res.pop('status'))
        res.insert(7, 'productRemainAmount', res.pop('productRemainAmount'))
        res.insert(3, 'fact_return, %', round(res['APR']/365*res['lock_up_period']*100, 4))

        return res

    def launchpool(self) -> pd.DataFrame:
        """Retrieve KuCoin launchpool (gem pool) products."""
        url = "https://www.kucoin.com/_api/currency-front/gem/customer/ongoingGem"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        res = pd.DataFrame(resp.json()['data']['gemNewPool'])
        details_df = pd.json_normalize(res["details"])

        flat_data = []
        for short_name, pool_info_list in zip(details_df["shortName"], details_df["poolInfoList"]):
            for item in pool_info_list:
                item = {"shortName": short_name, **item}
                flat_data.append(item)

        res = pd.DataFrame(flat_data)
        res['annualPercentageRate'] = res['annualPercentageRate'].astype(float)
        res.insert(0, 'reward_coin', res.pop('shortName'))
        res.insert(1, 'staking_coin', res.pop('stakingToken'))

        res.insert(2, 'APR_approx', res.pop('annualPercentageRate'))
        res.insert(3, 'start_time', pd.to_datetime(res.pop('stakingStartTime'), unit='ms', errors='coerce'))
        res.insert(4, 'end_time', pd.to_datetime(res.pop('stakingEndTime'), unit='ms', errors='coerce'))

        now = pd.Timestamp(datetime.datetime.utcnow())
        res.insert(5, 'status', res.apply(lambda row: 'ongoing' if row['start_time'] <= now <= row['end_time'] else ('finished' if now > row['end_time'] else 'upcoming'), axis=1))
        res.insert(6, 'duration_days', (res['end_time'] - res['start_time']).dt.days)

        res.pop('stakingTokenLogo')

        return pd.DataFrame(res)
    
    def account_balance(self) -> pd.DataFrame:
        """Retrieve KuCoin account balances and add USDT conversion (all accounts)."""
        # === 1. Get All Balances ===
        endpoint = "/api/v1/accounts"
        headers = self._auth_headers(endpoint)

        response = requests.get(self.base_url + endpoint, headers=headers, timeout=10)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch account balances: {response.text}")

        data = response.json().get("data", [])
        df = pd.DataFrame(data)

        if df.empty:
            return df

        # === 2. Get USDT Prices ===
        tickers_url = f"{self.base_url}/api/v1/market/allTickers"
        tickers_resp = requests.get(tickers_url, timeout=10)
        tickers_resp.raise_for_status()

        tickers = pd.DataFrame(tickers_resp.json()["data"]["ticker"])
        tickers = tickers[tickers["symbol"].str.endswith("USDT")].copy()
        tickers["asset"] = tickers["symbol"].str.replace("-USDT", "", regex=False)
        tickers["price"] = tickers["last"].astype(float)
        price_map = tickers.set_index("asset")["price"].to_dict()

        # === 3. Merge Prices Into Full Balance Table ===
        df["balance"] = df["balance"].astype(float)
        df["price_usdt"] = df["currency"].map(price_map).fillna(0)
        df.loc[df["currency"] == "USDT", "price_usdt"] = 1.0
        df["value_usdt"] = (df["balance"] * df["price_usdt"]).round(2)

        return df
    
    def earn_investments(self) -> pd.DataFrame:
        """Retrieve KuCoin Earn holdings (Flexible + Promotion)."""
        endpoint = "/api/v1/earn/hold-assets"
        headers = self._auth_headers(endpoint)

        response = requests.get(self.base_url + endpoint, headers=headers, timeout=10)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch earn holdings: {response.text}")

        data = response.json()['data']['items']
        df = pd.DataFrame(data)

        df['lockStartTime']=pd.to_datetime(df['lockStartTime']/1000, unit='s', errors='coerce')
        df['lockEndTime']=pd.to_datetime(df['lockEndTime']/1000, unit='s', errors='coerce')
        df['purchaseTime']=pd.to_datetime(df['purchaseTime']/1000, unit='s', errors='coerce')

        df = df.drop(columns=df.columns[[0,1,2,3,5,8,9,13,15]])
        if df.empty:
            return df
        return df



class Gate:
    def __init__(self):
        self.base_url = "https://www.gate.com/apiw/v2"

    def fixed_earn(self) -> pd.DataFrame:
        """Fetch Gate Fixed Earn products."""
        url = f"{self.base_url}/uni-loan/earn/market/list?limit=1000"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()['data']['list']
        flat = [item for sublist in pd.DataFrame(data)["fixed_list"] for item in sublist if sublist]
        res = pd.DataFrame(pd.json_normalize(flat))
        res.insert(2, 'APR', res.pop('year_rate'))
        res.insert(5, 'user_max_lend_volume', res.pop('user_max_lend_volume'))
        res['APR'] = res['APR'].astype(float)
        res.insert(3, 'fact_return, %', round(res['APR']/365*res['lock_up_period']*100, 4))
        
        return res



    def launchpool(self) -> pd.DataFrame:
        url = f"{self.base_url}/earn/launch-pool/project-list"
        resp = requests.get(url, params={"page": 1, "pageSize": 1000}, timeout=10)
        resp.raise_for_status()

        projects = pd.DataFrame(resp.json()['data']['list'])
        projects = projects.drop(columns=["coin_icon", "desc", "icon_show", "icon"])
        projects.rename(columns={"coin": "reward_coin"}, inplace=True)

        # Expand rewards_pools into rows
        reward_rows = []
        for _, row in projects.iterrows():
            for reward in row["reward_pools"]:
                combined = row.drop("reward_pools").to_dict()
                combined.update(reward)
                reward_rows.append(combined)

        now = pd.Timestamp(datetime.datetime.utcnow())

        res = pd.DataFrame(reward_rows)
        res.pop('name')
        res.insert(2, 'staking_coin', res.pop('coin'))
        res.insert(3, 'APR_approx', res.pop('rate_year').astype(float) / 100)
        res.insert(4, 'start_time', pd.to_datetime(res.pop('start_timest'), unit='s', errors='coerce'))
        res.insert(5, 'end_time', pd.to_datetime(res.pop('end_timest'), unit='s', errors='coerce'))
        res.insert(6, 'status', res.apply(lambda row: 'ongoing' if row['start_time'] <= now <= row['end_time'] else ('finished' if now > row['end_time'] else 'upcoming'), axis=1))
        res.insert(7, 'duration_days', res.pop('days'))

        return res



class Bybit:
    def __init__(self):
        self.headers = {
            'Lang': 'en',
            'Origin': 'https://www.bybit.com',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15'
        }

    def _earn_coin_by_code(self) -> pd.DataFrame:
        resp = requests.get("https://www.bybit.com/x-api/s1/byfi/get-coins", headers=self.headers)
        resp.raise_for_status()
        coins = resp.json()['result']['coins']
        return pd.DataFrame(
            [c['coin'][:4] for c in coins],
            columns=['coin_id', 'symbol', 'base_precision', 'quote_precision']
        ).astype({'coin_id': int, 'base_precision': int, 'quote_precision': int})

    def fixed_earn(self) -> pd.DataFrame:
        """Fetch Bybit Fixed Earn products."""
        df, i, coins = pd.DataFrame(), 1, self._earn_coin_by_code()
        while True:
            resp = requests.post(
                url="https://www.bybit.com/x-api/s1/byfi/get-saving-homepage-product-cards",
                headers=self.headers,
                json={
                    "product_area": [0], "page": i, "limit": 1, "product_type": 6,
                    "coin_name": "", "sort_apr": 0, "match_user_asset": 'false',
                    "show_available": 'false', "fixed_saving_version": 1
                }
            )
            data = resp.json()['result']['coin_products']
            if not data:
                break

            rows = pd.DataFrame(data)['saving_products'].explode().dropna().apply(pd.Series)
            df = pd.concat([df, rows], ignore_index=True)
            i += 1

        df = df.merge(coins[['coin_id', 'symbol']], left_on='coin', right_on='coin_id', how='left').drop(columns='coin_id')
        df['apy'] = df['apy'].str.rstrip('%').astype(float) / 100
        res = df.sort_values(by='apy', ascending=False).apply(pd.to_numeric, errors='ignore')
        res.insert(1, 'asset', res.pop('symbol'))
        res.insert(2, 'APR', res.pop('apy'))
        res.insert(3, 'lock_up_period', res.pop('staking_term'))
        res.insert(4, 'start_time', pd.to_datetime(res['subscribe_start_at'], unit='s'))
        res.insert(5, 'end_time', pd.to_datetime(res['subscribe_end_at'], unit='s'))

        now = pd.Timestamp(datetime.datetime.utcnow())
        res.insert(6, 'status', res.apply(lambda row: 'ongoing' if row['start_time'] <= now <= row['end_time'] else ('finished' if now > row['end_time'] else 'upcoming'), axis=1))
        res.insert(3, 'fact_return, %', round(res['APR']/365*res['lock_up_period']*100, 4))

        return res

    def launchpool(self) -> pd.DataFrame:
        """Fetch Bybit Launchpool projects."""
        df, i = pd.DataFrame(), 1
        while True:
            resp = requests.post(
                url="https://www.bybit.com/x-api/spot/api/launchpool/v1/history",
                headers=self.headers,
                json={'current': str(i), 'pageSize': '3'}
            )
            data = resp.json()['result']['list']
            if not data:
                break

            rows = [{**p, **pool} for p in data for pool in p['stakePoolList']]
            df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
            i += 1

        df['apr'] = df['apr'].astype(float) / 100
        res = df.apply(pd.to_numeric, errors='ignore')
        res = res.drop(columns=["returnCoinIcon", "desc", "website", "whitepaper",'rules'])

        res.insert(1, 'reward_coin', res.pop('returnCoin'))
        res.insert(2, 'staking_coin', res.pop('stakeCoin'))
        res.insert(3, 'APR_approx', res.pop('apr'))
        res.insert(4, 'start_time', pd.to_datetime(res['stakeBeginTime'], unit='ms'))
        res.insert(5, 'end_time', pd.to_datetime(res['stakeEndTime'], unit='ms'))
        res.insert(6, 'trade_start', pd.to_datetime(res['tradeBeginTime'], unit='ms'))
        res.insert(7, 'duration_days', (res['end_time'] - res['start_time']).dt.days)

        now = pd.Timestamp(datetime.datetime.utcnow())
        res.insert(7, 'status', res.apply(lambda row: 'ongoing' if row['start_time'] <= now <= row['end_time'] else ('finished' if now > row['end_time'] else 'upcoming'), axis=1))

        return res



if __name__ == '__main__':
    gt = KCN()
    print(gt.launchpool())