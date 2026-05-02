import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import numpy as np
import pandas_ta as ta
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

# ── Load & semua indikator ───────────────────────────────────────
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

# Tambahan indikator
df['ema21_m5']  = ta.ema(df['close'], length=21)
df['macd_m5']   = ta.macd(df['close'])['MACD_12_26_9']
df['macd_sig']  = ta.macd(df['close'])['MACDs_12_26_9']
df['macd_hist'] = ta.macd(df['close'])['MACDh_12_26_9']
df['stoch_k']   = ta.stoch(df['high'], df['low'], df['close'])['STOCHk_14_3_3']
df['stoch_d']   = ta.stoch(df['high'], df['low'], df['close'])['STOCHd_14_3_3']
df['adx_m5']    = ta.adx(df['high'], df['low'], df['close'])['ADX_14']
df['dmp_m5']    = ta.adx(df['high'], df['low'], df['close'])['DMP_14']
df['dmn_m5']    = ta.adx(df['high'], df['low'], df['close'])['DMN_14']
df['cci_m5']    = ta.cci(df['high'], df['low'], df['close'], length=14)
df['williams_r']= ta.willr(df['high'], df['low'], df['close'], length=14)
df['mfi_m5']    = ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=14)
df['obv_m5']    = ta.obv(df['close'], df['volume'])
df['vwap_m5']   = ta.vwap(df['high'], df['low'], df['close'], df['volume'])

bb = ta.bbands(df['close'], length=20, std=2)
bbu = [c for c in bb.columns if c.startswith('BBU')][0]
bbl = [c for c in bb.columns if c.startswith('BBL')][0]
bbm = [c for c in bb.columns if c.startswith('BBM')][0]
df['bbw']       = (bb[bbu] - bb[bbl]) / bb[bbm] * 100
df['bb_pos']    = (df['close'] - bb[bbl]) / (bb[bbu] - bb[bbl])  # posisi close di dalam BB 0-1

# H1
df_h1 = df_raw.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_h1['ema50_h1']  = ta.ema(df_h1['close'], length=50)
df_h1['ema200_h1'] = ta.ema(df_h1['close'], length=200)
df_h1['atr_h1']    = ta.atr(df_h1['high'], df_h1['low'], df_h1['close'], length=14)
df_h1['rsi_h1']    = ta.rsi(df_h1['close'], length=14)
df_h1['adx_h1']    = ta.adx(df_h1['high'], df_h1['low'], df_h1['close'])['ADX_14']
df_h1['slope_h1']  = df_h1['ema50_h1'] - df_h1['ema50_h1'].shift(4)
def get_trend_h1(row):
    if row['slope_h1'] > 0.25 and (row['close'] > row['ema200_h1'] or row['ema50_h1'] > row['ema200_h1']): return 'up'
    elif row['slope_h1'] < -0.25 and (row['close'] < row['ema200_h1'] or row['ema50_h1'] < row['ema200_h1']): return 'down'
    return 'sideways'
df_h1['trend_h1'] = df_h1.apply(get_trend_h1, axis=1)
df_h1_m5 = df_h1[['ema50_h1','ema200_h1','atr_h1','trend_h1','rsi_h1','adx_h1']].resample('5min').ffill()
df = df.join(df_h1_m5, how='left')
df[['ema50_h1','ema200_h1','atr_h1','trend_h1','rsi_h1','adx_h1']] = df[['ema50_h1','ema200_h1','atr_h1','trend_h1','rsi_h1','adx_h1']].ffill()

# H4
df_h4 = df_raw.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_h4['ema50_h4']  = ta.ema(df_h4['close'], length=50)
df_h4['ema200_h4'] = ta.ema(df_h4['close'], length=200)
df_h4['slope_h4']  = df_h4['ema50_h4'] - df_h4['ema50_h4'].shift(2)
def trend_h4(row):
    if row['slope_h4'] > 1.0 and row['ema50_h4'] > row['ema200_h4']: return 'up'
    elif row['slope_h4'] < -1.0 and row['ema50_h4'] < row['ema200_h4']: return 'down'
    return 'sideways'
df_h4['trend_h4'] = df_h4.apply(trend_h4, axis=1)
df_h4_m5 = df_h4[['trend_h4','ema50_h4']].resample('5min').ffill()
df = df.join(df_h4_m5, how='left')
df[['trend_h4','ema50_h4']] = df[['trend_h4','ema50_h4']].ffill()

def detect_pattern(df, i):
    if i < 1: return 'none'
    o,h,l,c=df['open'].iloc[i],df['high'].iloc[i],df['low'].iloc[i],df['close'].iloc[i]
    po,pc=df['open'].iloc[i-1],df['close'].iloc[i-1]
    body=abs(c-o); up=h-max(o,c); lo=min(o,c)-l; cr=h-l; mid=(h+l)/2
    if cr==0: return 'none'
    if body<cr*0.05: return 'doji'
    if lo>2*body and lo>up and c>mid: return 'pin_bar_bull'
    if up>2*body and up>lo and c<mid: return 'pin_bar_bear'
    if c>o and pc<po and c>po and o<pc: return 'engulfing_bull'
    if c<o and pc>po and c<po and o>pc: return 'engulfing_bear'
    if c>o and body>=cr*0.8: return 'marubozu_bull'
    if c<o and body>=cr*0.8: return 'marubozu_bear'
    return 'none'
df['pattern'] = [detect_pattern(df,i) for i in range(len(df))]

def calc_score(row, action):
    if action=='buy':
        if row['trend_h1']!='up': return 0
        s=1
        if row['ema50_m5']>row['ema200_m5']: s+=1
        if row['rsi_m5']>50: s+=1
        if row['pattern'] in ('pin_bar_bull','engulfing_bull','marubozu_bull'): s+=1
        if row['close']<row['ema50_h1']: s+=1
        if row['close']>row['ema200_h1']: s+=1
        return s
    elif action=='sell':
        if row['trend_h1']!='down': return 0
        s=1
        if row['ema50_m5']<row['ema200_m5']: s+=1
        if row['rsi_m5']<50: s+=1
        if row['pattern'] in ('pin_bar_bear','engulfing_bear','marubozu_bear'): s+=1
        if row['close']>row['ema50_h1']: s+=1
        if row['close']<row['ema200_h1']: s+=1
        return s
    return 0
df['score_buy']  = df.apply(lambda r: calc_score(r,'buy'),  axis=1)
df['score_sell'] = df.apply(lambda r: calc_score(r,'sell'), axis=1)

# ── Simulasi base trades ─────────────────────────────────────────
ATR_MIN=3.0; LOT=0.001
trades=[]
df_valid = df.dropna(subset=['ema50_m5','ema200_m5','rsi_m5','atr_m5','ema50_h1','ema200_h1',
                              'macd_m5','adx_m5','stoch_k','bbw','trend_h4'])
for i in range(1, len(df_valid)):
    row=df_valid.iloc[i]
    if row['atr_m5']<ATR_MIN: continue
    if row['pattern']=='doji': continue
    action=None; score=0
    if row['score_buy']>=5:    action='buy';  score=int(row['score_buy'])
    elif row['score_sell']>=5: action='sell'; score=int(row['score_sell'])
    if action is None: continue
    entry=round(row['close'],2); atr=row['atr_m5']
    sl=round(entry-atr if action=='buy' else entry+atr,2)
    tp=round(entry+2*atr if action=='buy' else entry-2*atr,2)
    outcome='open'; cp=None
    for j,fr in df_valid.iloc[i+1:i+200].iterrows():
        if action=='buy':
            if fr['low']<=sl:  outcome='loss';   cp=sl;  break
            if fr['high']>=tp: outcome='profit'; cp=tp;  break
        else:
            if fr['high']>=sl: outcome='loss';   cp=sl;  break
            if fr['low']<=tp:  outcome='profit'; cp=tp;  break
    if outcome=='open': continue
    profit=round((cp-entry)*(1 if action=='buy' else -1)*LOT*100,2)

    # Normalisasi fitur sesuai arah trade
    sign = 1 if action=='buy' else -1
    trades.append({
        'outcome': 1 if outcome=='profit' else 0,
        'action': action,
        'hour': df_valid.index[i].hour,
        'profit': profit,
        # Fitur teknikal (dinormalisasi arah)
        'rsi_m5':       row['rsi_m5'],
        'rsi_norm':     (row['rsi_m5']-50)*sign,          # positif = searah trend
        'atr_m5':       row['atr_m5'],
        'bbw':          row['bbw'],
        'bb_pos':       row['bb_pos'] if action=='buy' else 1-row['bb_pos'],
        'adx_m5':       row['adx_m5'],
        'macd_hist':    row['macd_hist']*sign,
        'stoch_k_norm': (row['stoch_k']-50)*sign,
        'cci_norm':     row['cci_m5']*sign,
        'willr_norm':   (row['williams_r']+50)*sign,
        'mfi_norm':     (row['mfi_m5']-50)*sign,
        'rsi_h1_norm':  (row['rsi_h1']-50)*sign,
        'adx_h1':       row['adx_h1'],
        'h4_confirm':   1 if ((action=='buy' and row['trend_h4']=='up') or
                               (action=='sell' and row['trend_h4']=='down')) else 0,
        'score':        score,
        'pattern':      row['pattern'],
        'candle_time':  df_valid.index[i],
    })

t = pd.DataFrame(trades)
print(f'Total trades: {len(t)} | WR: {t["outcome"].mean()*100:.1f}%')
print()

# ── Analisa korelasi fitur vs outcome ───────────────────────────
feat_cols = ['rsi_norm','bbw','bb_pos','adx_m5','macd_hist','stoch_k_norm',
             'cci_norm','willr_norm','mfi_norm','rsi_h1_norm','adx_h1',
             'h4_confirm','score','atr_m5','hour']

corr = t[feat_cols + ['outcome']].corr()['outcome'].drop('outcome').sort_values(key=abs, ascending=False)
print('KORELASI FITUR vs OUTCOME (win=1, loss=0):')
print('(positif = lebih tinggi → lebih sering win)')
print()
for feat, val in corr.items():
    bar = '|'*int(abs(val)*200)
    sign = '+' if val > 0 else '-'
    print(f'  {feat:<18} {sign}{bar:<20} {val:+.4f}')

# ── Test WR per bucket setiap fitur ─────────────────────────────
print()
print('='*60)
print('WR PER BUCKET FITUR TERPENTING:')
print('='*60)

def wr_bucket(col, bins, label):
    t['_bin'] = pd.cut(t[col], bins=bins)
    res = t.groupby('_bin', observed=True).agg(
        n=('outcome','count'),
        wr=('outcome','mean')
    ).reset_index()
    res['wr'] = res['wr']*100
    print(f'\n{label}:')
    for _,r in res.iterrows():
        if r['n'] < 10: continue
        bar = '|'*int(r['wr']/3)
        mark = ' <--' if r['wr'] >= 45 else ''
        print(f'  {str(r["_bin"]):<25} {bar:<20} {r["wr"]:.0f}%  ({int(r["n"])}){mark}')

wr_bucket('adx_m5',      [0,20,25,30,35,40,50,100], 'ADX M5 (trend strength)')
wr_bucket('adx_h1',      [0,20,25,30,35,40,50,100], 'ADX H1')
wr_bucket('bbw',         [0,0.5,1,1.5,2,2.5,3,4,6,100], 'BB Width %')
wr_bucket('rsi_norm',    [-50,-20,-10,0,10,20,50], 'RSI norm (searah trend)')
wr_bucket('bb_pos',      [0,0.2,0.4,0.6,0.8,1.0], 'BB Position (searah trend)')
wr_bucket('macd_hist',   [-5,-1,-0.5,0,0.5,1,5], 'MACD Histogram (searah)')
wr_bucket('atr_m5',      [0,3,4,5,6,8,10,20,100], 'ATR M5')
wr_bucket('h4_confirm',  [-0.5,0.5,1.5], 'H4 Confirm (0=tidak, 1=ya)')

# ── Chart korelasi ───────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(16, 7))

corr.plot(kind='barh', ax=axes[0],
          color=['green' if v>0 else 'red' for v in corr.values],
          alpha=0.8)
axes[0].axvline(0, color='black', linewidth=1)
axes[0].set_title('Korelasi Fitur vs Win/Loss')
axes[0].set_xlabel('Korelasi')

# WR per ADX bucket (fitur paling penting biasanya)
t['adx_bucket'] = pd.cut(t['adx_m5'], bins=[0,20,25,30,35,40,50,100],
                          labels=['<20','20-25','25-30','30-35','35-40','40-50','>50'])
adx_wr = t.groupby('adx_bucket', observed=True).agg(
    n=('outcome','count'), wr=('outcome',lambda x: x.mean()*100)
).reset_index()
colors = ['green' if w>=45 else 'steelblue' for w in adx_wr['wr']]
axes[1].bar(range(len(adx_wr)), adx_wr['wr'], color=colors, alpha=0.85)
axes[1].axhline(50, color='red', linestyle='--')
axes[1].set_xticks(range(len(adx_wr)))
axes[1].set_xticklabels(adx_wr['adx_bucket'])
axes[1].set_title('WR per ADX M5')
axes[1].set_ylabel('Win Rate (%)')
for i,(_, row) in enumerate(adx_wr.iterrows()):
    axes[1].text(i, row['wr']+0.5, f"{row['wr']:.0f}%\n({int(row['n'])})", ha='center', fontsize=9)

plt.suptitle('Analisa Fitur ML — XAUUSD 5m', fontsize=13, fontweight='bold')
plt.tight_layout()
plt.savefig('ml_analysis.png', dpi=120, bbox_inches='tight')
print()
print('Chart: ml_analysis.png')
