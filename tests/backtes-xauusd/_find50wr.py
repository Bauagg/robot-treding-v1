import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np
import pandas_ta as ta
import warnings
warnings.filterwarnings('ignore')

df_raw = pd.read_csv('xauusd-ohcl.csv', sep='\t')
df_raw.columns = [c.strip('<>').lower() for c in df_raw.columns]
df_raw['datetime'] = pd.to_datetime(df_raw['date'] + ' ' + df_raw['time'])
df_raw = df_raw.rename(columns={'tickvol': 'volume'})
df_raw = df_raw[['datetime','open','high','low','close','volume']].set_index('datetime')
df_raw = df_raw.sort_index()

df = df_raw.copy()
df['ema50_m5']  = ta.ema(df['close'], length=50)
df['ema200_m5'] = ta.ema(df['close'], length=200)
df['rsi_m5']    = ta.rsi(df['close'], length=14)
df['atr_m5']    = ta.atr(df['high'], df['low'], df['close'], length=14)
bb = ta.bbands(df['close'], length=20, std=2)
bbu = [c for c in bb.columns if c.startswith('BBU')][0]
bbl = [c for c in bb.columns if c.startswith('BBL')][0]
bbm = [c for c in bb.columns if c.startswith('BBM')][0]
df['bbw'] = (bb[bbu] - bb[bbl]) / bb[bbm] * 100

h1 = df_raw.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
h1['ema50_h1']  = ta.ema(h1['close'], length=50)
h1['ema200_h1'] = ta.ema(h1['close'], length=200)
h1['rsi_h1']    = ta.rsi(h1['close'], length=14)
h1['adx_h1']    = ta.adx(h1['high'], h1['low'], h1['close'])['ADX_14']
h1['slope_h1']  = h1['ema50_h1'] - h1['ema50_h1'].shift(4)

def get_trend_h1(row):
    if row['slope_h1'] > 0.25 and (row['close'] > row['ema200_h1'] or row['ema50_h1'] > row['ema200_h1']): return 'up'
    elif row['slope_h1'] < -0.25 and (row['close'] < row['ema200_h1'] or row['ema50_h1'] < row['ema200_h1']): return 'down'
    return 'sideways'

h1['trend_h1'] = h1.apply(get_trend_h1, axis=1)
h1_m5 = h1[['ema50_h1','ema200_h1','rsi_h1','adx_h1','trend_h1']].resample('5min').ffill()
df = df.join(h1_m5, how='left')
df[['ema50_h1','ema200_h1','rsi_h1','adx_h1','trend_h1']] = df[['ema50_h1','ema200_h1','rsi_h1','adx_h1','trend_h1']].ffill()

def detect_pattern(df, i):
    if i < 1: return 'none'
    o,h,l,c = df['open'].iloc[i],df['high'].iloc[i],df['low'].iloc[i],df['close'].iloc[i]
    po,pc   = df['open'].iloc[i-1],df['close'].iloc[i-1]
    body=abs(c-o); up_shad=h-max(o,c); lo_shad=min(o,c)-l; cr=h-l; mid=(h+l)/2
    if cr==0: return 'none'
    if body < cr*0.05: return 'doji'
    if lo_shad>2*body and lo_shad>up_shad and c>mid: return 'pin_bar_bull'
    if up_shad>2*body and up_shad>lo_shad and c<mid: return 'pin_bar_bear'
    if c>o and pc<po and c>po and o<pc: return 'engulfing_bull'
    if c<o and pc>po and c<po and o>pc: return 'engulfing_bear'
    if c>o and body>=cr*0.8: return 'marubozu_bull'
    if c<o and body>=cr*0.8: return 'marubozu_bear'
    return 'none'

df['pattern'] = [detect_pattern(df,i) for i in range(len(df))]

def calc_score(row, action):
    if action == 'buy':
        if row['trend_h1'] != 'up': return 0
        s = 1
        if row['ema50_m5'] > row['ema200_m5']: s += 1
        if row['rsi_m5'] > 50: s += 1
        if row['pattern'] in ('pin_bar_bull','engulfing_bull','marubozu_bull'): s += 1
        if row['close'] < row['ema50_h1']: s += 1
        if row['close'] > row['ema200_h1']: s += 1
        return s
    elif action == 'sell':
        if row['trend_h1'] != 'down': return 0
        s = 1
        if row['ema50_m5'] < row['ema200_m5']: s += 1
        if row['rsi_m5'] < 50: s += 1
        if row['pattern'] in ('pin_bar_bear','engulfing_bear','marubozu_bear'): s += 1
        if row['close'] > row['ema50_h1']: s += 1
        if row['close'] < row['ema200_h1']: s += 1
        return s
    return 0

df['score_buy']  = df.apply(lambda r: calc_score(r,'buy'), axis=1)
df['score_sell'] = df.apply(lambda r: calc_score(r,'sell'), axis=1)

df_valid = df.dropna(subset=['ema50_m5','ema200_m5','rsi_m5','atr_m5','rsi_h1','adx_h1'])
BAD_HOURS = [10, 11, 16, 17]

trades = []
for i in range(1, len(df_valid)):
    row = df_valid.iloc[i]
    if row['atr_m5'] < 3.0: continue
    if row['pattern'] == 'doji': continue
    if row.name.hour in BAD_HOURS: continue
    if row['bbw'] < 1.0 or row['bbw'] >= 3.0: continue
    action = None
    if row['score_buy'] >= 5:    action = 'buy'
    elif row['score_sell'] >= 5: action = 'sell'
    if action is None: continue
    rsi_h1_norm = (50 - row['rsi_h1']) if action == 'sell' else (row['rsi_h1'] - 50)
    ep = row['close']; atr = row['atr_m5']
    sl = ep + atr*1.5 if action=='sell' else ep - atr*1.5
    tp = ep - atr*3.0 if action=='sell' else ep + atr*3.0
    outcome = None
    profit = 0
    for j in range(i+1, min(i+200, len(df_valid))):
        f = df_valid.iloc[j]
        if action == 'sell':
            if f['low']  <= tp: outcome='profit'; profit=round((ep-tp)*0.01*100,2); break
            if f['high'] >= sl: outcome='loss';   profit=round((ep-sl)*0.01*100,2); break
        else:
            if f['high'] >= tp: outcome='profit'; profit=round((tp-ep)*0.01*100,2); break
            if f['low']  <= sl: outcome='loss';   profit=round((sl-ep)*0.01*100,2); break
    if not outcome: continue
    trades.append({'outcome':outcome,'rsi_h1_norm':rsi_h1_norm,'adx_h1':row['adx_h1'],'profit':profit,'action':action})

t = pd.DataFrame(trades)

candidates = [
    ('A: RSI H1>22 + ADX H1>=35', 22, 35),
    ('B: RSI H1>25 + ADX H1>=32', 25, 32),
    ('C: RSI H1>20 + ADX H1>=32', 20, 32),
    ('D: RSI H1>22 + ADX H1>=32', 22, 32),
    ('E: RSI H1>25 + ADX H1>=30', 25, 30),
]

print('=' * 65)
print('KANDIDAT TERPILIH (balance WR vs jumlah trade):')
print('=' * 65)

for label, rsi_thr, adx_thr in candidates:
    filt = t[(t['rsi_h1_norm'] > rsi_thr) & (t['adx_h1'] >= adx_thr)].reset_index(drop=True)
    n = len(filt)
    if n == 0: continue
    wr = (filt['outcome']=='profit').mean()*100
    w = (filt['outcome']=='profit').sum()
    l = (filt['outcome']=='loss').sum()
    pf = (w*2.0)/l if l > 0 else 99
    net = filt['profit'].sum()
    eq = 100 + filt['profit'].cumsum()
    dd = (eq - eq.cummax()).min()
    final = eq.iloc[-1]
    streak = max_s = 0
    for o in filt['outcome']:
        streak = streak+1 if o=='loss' else 0
        max_s = max(max_s, streak)
    min_eq = eq.min()
    status = 'JEBOL' if min_eq <= 0 else 'AMAN'
    print(f'\n  {label}')
    print(f'  Total Trade    : {n}')
    print(f'  Win Rate       : {wr:.1f}%')
    print(f'  Profit Factor  : {pf:.2f}')
    print(f'  Net P&L        : ${net:.2f}')
    print(f'  Max Drawdown   : ${dd:.2f}')
    print(f'  Equity Akhir   : ${final:.2f}')
    print(f'  Max Streak Loss: {max_s}x')
    print(f'  Min Equity     : ${min_eq:.2f} -> {status}')
