import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np
import pandas_ta as ta
import warnings
warnings.filterwarnings('ignore')

# Load data
df_raw = pd.read_csv('xauusd-ohcl.csv', sep='\t')
df_raw.columns = [c.strip('<>').lower() for c in df_raw.columns]
df_raw['datetime'] = pd.to_datetime(df_raw['date'] + ' ' + df_raw['time'])
df_raw = df_raw.rename(columns={'tickvol': 'volume'})
df_raw = df_raw[['datetime','open','high','low','close','volume']].set_index('datetime')
df_raw = df_raw.sort_index()

df = df_raw.copy()
df['ema50']  = ta.ema(df['close'], length=50)
df['ema200'] = ta.ema(df['close'], length=200)
df['rsi_m5'] = ta.rsi(df['close'], length=14)
df['atr']    = ta.atr(df['high'], df['low'], df['close'], length=14)
bb = ta.bbands(df['close'], length=20, std=2)
bbu = [c for c in bb.columns if c.startswith('BBU')][0]
bbl = [c for c in bb.columns if c.startswith('BBL')][0]
bbm = [c for c in bb.columns if c.startswith('BBM')][0]
df['bbw'] = (bb[bbu] - bb[bbl]) / bb[bbm] * 100

# H1 RSI & trend
h1 = df_raw.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
h1['ema50_h1']  = ta.ema(h1['close'], length=50)
h1['ema200_h1'] = ta.ema(h1['close'], length=200)
h1['rsi_h1']    = ta.rsi(h1['close'], length=14)
h1['trend_h1']  = (h1['ema50_h1'] > h1['ema200_h1']).map({True:'up', False:'down'})
h1['adx_h1']    = ta.adx(h1['high'], h1['low'], h1['close'])['ADX_14']

# H4 RSI & trend
h4 = df_raw.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
h4['ema50_h4']  = ta.ema(h4['close'], length=50)
h4['ema200_h4'] = ta.ema(h4['close'], length=200)
h4['rsi_h4']    = ta.rsi(h4['close'], length=14)
h4['trend_h4']  = (h4['ema50_h4'] > h4['ema200_h4']).map({True:'up', False:'down'})

# Merge ke M5
h1_m = h1[['trend_h1','rsi_h1','adx_h1']].copy()
h4_m = h4[['trend_h4','rsi_h4']].copy()

df = df.reset_index()
df['hour_key'] = df['datetime'].dt.floor('h')
df['h4_key']   = df['datetime'].dt.floor('4h')
h1_m = h1_m.rename_axis('hour_key').reset_index()
h4_m = h4_m.rename_axis('h4_key').reset_index()

df = df.merge(h1_m, on='hour_key', how='left')
df = df.merge(h4_m, on='h4_key', how='left')
df = df.set_index('datetime')

# Jam UTC filter (skip 10,11,16,17)
df['hour_utc'] = df.index.hour
jam_bad = [10, 11, 16, 17]

# Simulate trades
df = df.dropna(subset=['ema50','ema200','rsi_m5','atr','trend_h1','rsi_h1']).copy()

def simulate(df_in, label=''):
    trades = []
    idx_arr = df_in.index.tolist()
    pos_map = {v: k for k, v in enumerate(idx_arr)}

    for pos, ts in enumerate(idx_arr):
        row = df_in.loc[ts]
        # Signal logic
        if row['trend_h1'] == 'down' and row['close'] < row['ema50'] and row['rsi_m5'] < 50:
            direction = 'sell'
        elif row['trend_h1'] == 'up' and row['close'] > row['ema50'] and row['rsi_m5'] > 50:
            direction = 'buy'
        else:
            continue
        if row['atr'] < 3.0: continue

        atr = row['atr']
        ep  = row['close']
        sl  = ep + atr * 1.5 if direction == 'sell' else ep - atr * 1.5
        tp  = ep - atr * 3.0 if direction == 'sell' else ep + atr * 3.0

        rsi_h1 = row['rsi_h1']
        rsi_h1_norm = (50 - rsi_h1) if direction == 'sell' else (rsi_h1 - 50)
        bbw    = row['bbw']
        adx_h1 = row['adx_h1'] if not pd.isna(row.get('adx_h1', np.nan)) else 0
        hour   = row['hour_utc']
        trend_h4 = row.get('trend_h4', None)
        rsi_h4   = row.get('rsi_h4', 50)
        rsi_h4_norm = (50 - rsi_h4) if direction == 'sell' else (rsi_h4 - 50)

        outcome = None
        for j in range(pos+1, min(pos+100, len(idx_arr))):
            future = df_in.loc[idx_arr[j]]
            if direction == 'sell':
                if future['low']  <= tp: outcome = 'profit'; break
                if future['high'] >= sl: outcome = 'loss';   break
            else:
                if future['high'] >= tp: outcome = 'profit'; break
                if future['low']  <= sl: outcome = 'loss';   break

        if outcome:
            trades.append({
                'outcome': outcome,
                'rsi_h1_norm': rsi_h1_norm,
                'rsi_h4_norm': rsi_h4_norm,
                'bbw': bbw,
                'adx_h1': adx_h1,
                'hour': hour,
                'trend_h4': trend_h4,
                'direction': direction,
            })

    t = pd.DataFrame(trades)
    if len(t) == 0:
        print(f'{label}: No trades')
        return t
    wr = (t.outcome=='profit').mean()*100
    print(f'{label}: {len(t)} trades | WR: {wr:.1f}%')
    return t

print('=' * 60)
print('BASELINE')
print('=' * 60)
t_base = simulate(df, 'Baseline (no filter)')

print()
print('=' * 60)
print('FILTER RSI H1 (searah trend)')
print('=' * 60)
for thr in [5, 10, 15, 20, 25, 30]:
    filt = t_base[t_base['rsi_h1_norm'] > thr]
    if len(filt) > 50:
        wr = (filt.outcome=='profit').mean()*100
        print(f'RSI H1 >{thr:2d} (norm): {len(filt):5d} trades | WR: {wr:.1f}%')

print()
print('=' * 60)
print('FILTER JAM UTC (skip 10,11,16,17) + RSI H1')
print('=' * 60)
t_jam = t_base[~t_base['hour'].isin(jam_bad)]
jam_wr = (t_jam.outcome=='profit').mean()*100
print(f'Jam filter only: {len(t_jam)} trades | WR: {jam_wr:.1f}%')
for thr in [5, 10, 15, 20, 25]:
    filt = t_jam[t_jam['rsi_h1_norm'] > thr]
    if len(filt) > 50:
        wr = (filt.outcome=='profit').mean()*100
        print(f'  + RSI H1 >{thr:2d}: {len(filt):5d} trades | WR: {wr:.1f}%')

print()
print('=' * 60)
print('FILTER JAM + BBW + RSI H1')
print('=' * 60)
t_comb = t_jam[(t_jam['bbw'] >= 1.0) & (t_jam['bbw'] <= 3.0)]
comb_wr = (t_comb.outcome=='profit').mean()*100
print(f'Jam+BBW only:    {len(t_comb)} trades | WR: {comb_wr:.1f}%')
for thr in [5, 10, 15, 20]:
    filt = t_comb[t_comb['rsi_h1_norm'] > thr]
    if len(filt) > 30:
        wr = (filt.outcome=='profit').mean()*100
        print(f'  + RSI H1 >{thr:2d}: {len(filt):5d} trades | WR: {wr:.1f}%')

print()
print('=' * 60)
print('FILTER ADX H1 >= threshold')
print('=' * 60)
for adx_thr in [20, 25, 30, 35, 40]:
    filt = t_base[t_base['adx_h1'] >= adx_thr]
    if len(filt) > 50:
        wr = (filt.outcome=='profit').mean()*100
        print(f'ADX H1 >={adx_thr}: {len(filt):5d} trades | WR: {wr:.1f}%')

print()
print('=' * 60)
print('KOMBINASI TERBAIK: JAM + BBW + RSI H1 + ADX H1')
print('=' * 60)
for rsi_thr in [10, 15, 20]:
    for adx_thr in [25, 30, 35]:
        filt = t_jam[
            (t_jam['bbw'] >= 1.0) & (t_jam['bbw'] <= 3.0) &
            (t_jam['rsi_h1_norm'] > rsi_thr) &
            (t_jam['adx_h1'] >= adx_thr)
        ]
        if len(filt) >= 20:
            wr = (filt.outcome=='profit').mean()*100
            pf = (filt.outcome=='profit').sum() / max((filt.outcome=='loss').sum(), 1) * 2.0
            print(f'RSI H1>{rsi_thr} ADX H1>={adx_thr}: {len(filt):4d} trades | WR: {wr:.1f}% | PF: {pf:.2f}')

print()
print('=' * 60)
print('WR PER BUCKET RSI H1 NORM (detail)')
print('=' * 60)
bins = [-100, -20, -10, 0, 5, 10, 15, 20, 30, 50, 100]
t_base['rsi_h1_bucket'] = pd.cut(t_base['rsi_h1_norm'], bins=bins)
bucket_stats = t_base.groupby('rsi_h1_bucket', observed=False).agg(
    count=('outcome','count'),
    wr=('outcome', lambda x: (x=='profit').mean()*100)
).reset_index()
for _, row in bucket_stats.iterrows():
    if row['count'] > 10:
        bar = '|' * int(row['wr'] / 2)
        print(f'  {str(row["rsi_h1_bucket"]):15s}  {bar:25s} {row["wr"]:.0f}%  ({int(row["count"])})')
